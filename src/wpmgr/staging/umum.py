"""Bagian bersama semua job staging (spec §6.4, §11, Koreksi #1–#3)."""

import errno
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from wpmgr.config import get_settings
from wpmgr.errors import (
    DAPAT_DIULANG,
    STAGING_DITOLAK,
    STAGING_GAGAL,
    TRANSIENT,
    UNKNOWN,
    SiteError,
)
from wpmgr.jobs.queue import akan_diulang
from wpmgr.models import ActivityLog, Job, JobStatus, Site, Staging, StatusStaging, User
from wpmgr.staging.aman import (
    BATAS_DIUBAH,
    PathTidakAman,
    angka,
    baca_terbatas,
    bersih_teks,
)
from wpmgr.staging.pembantu import GalatPembantu, Pembantu

log = logging.getLogger("wpmgr.staging.umum")

ULANG_POTONGAN = 3
JEDA_ULANG = (2, 5)
UA = "WP-Manager-Staging/3.0"
# Reaper merebut klaim setelah `locked_at` 15 menit; detak tiap menit
# memberi ruang untuk beberapa kali gagal berturut-turut.
JEDA_DETAK = 60.0
# Utas detak tidak boleh menunggu kunci baris selamanya: bila transaksi utas
# utama sedang memegang baris job itu, utas utama akan menunggu utas ini
# saat keluar dari blok -- kebuntuan tanpa batas waktu ini.
BATAS_KUNCI_DETAK = "5s"
TUNGGU_UTAS_DETAK = 10.0
PESAN_TAK_TERDUGA = "Galat tak terduga; lihat log server"
# Stempel waktu Unix terbesar yang masih bisa menjadi datetime (9999-12-31).
_DETIK_MAKS = 253402300799


class KlaimHilang(Exception):
    """Reaper atau worker lain sudah mengambil alih job ini."""


class Dibatalkan(Exception):
    """Pengguna meminta pembatalan (staging.batal_diminta_pada)."""


def galat_ditolak(pesan: str) -> SiteError:
    return SiteError(STAGING_DITOLAK, pesan)


def galat_gagal(pesan: str) -> SiteError:
    return SiteError(STAGING_GAGAL, pesan)


def buat_pembantu() -> Pembantu:
    return Pembantu.dari_setelan()


def buat_http() -> httpx.Client:
    """Klien HTTP untuk probe staging dan cek halaman utama produksi."""
    return httpx.Client(follow_redirects=False, timeout=30.0, headers={"User-Agent": UA})


def sekarang() -> datetime:
    return datetime.now(timezone.utc)


def dir_site(site_id) -> Path:
    return get_settings().jalur_staging / str(site_id)


def host_staging(staging: Staging) -> str:
    return f"{staging.nama}.{get_settings().staging_domain}"


def url_staging(staging: Staging) -> str:
    return f"https://{host_staging(staging)}"


def baca_diubah(site_id) -> datetime | None:
    """Waktu terakhir staging diubah, dari penanda yang ditulis mu-plugin staging.

    `log/` di-bind mount ke container staging, jadi berkasnya bisa berupa
    symlink, FIFO, atau berukuran gigabyte: dibaca hanya lewat `aman`, tanpa
    mengikuti symlink dan paling banyak BATAS_DIUBAH byte (putusan F1).
    """
    try:
        mentah = baca_terbatas(dir_site(site_id), "log/diubah", BATAS_DIUBAH)
    except (PathTidakAman, OSError):
        return None
    teks = mentah.decode("ascii", errors="replace").strip()[:20]
    detik = angka(teks, 0, _DETIK_MAKS)
    if detik is None:
        return None
    try:
        return datetime.fromtimestamp(detik, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        # Windows menolak stempel waktu di atas tahun 3000 dengan OSError.
        return None


def perbarui_diubah(staging: Staging) -> None:
    d = baca_diubah(staging.site_id)
    if d is not None and (staging.diubah_pada is None or d > staging.diubah_pada):
        staging.diubah_pada = d


def _perpanjang(sesi: Session, job_id, pemegang: str) -> bool:
    n = sesi.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.running, Job.locked_by == pemegang)
        .values(locked_at=func.now())
    ).rowcount
    sesi.commit()
    return n > 0


def detak(sesi: Session, job: Job) -> None:
    """Perpanjang klaim (locked_at) supaya reaper tidak merebut job yang berjalan lama.

    Job yang dipanggil langsung tanpa klaim (test, pemanggilan manual) tidak
    punya locked_by dan tidak perlu detak.
    """
    if job.locked_by is None:
        return
    if not _perpanjang(sesi, job.id, job.locked_by):
        raise KlaimHilang(f"Klaim job {job.id} sudah tidak dipegang {job.locked_by}")


class Detak:
    """Keadaan detak latar yang bisa dibaca handler di dalam blok."""

    def __init__(self) -> None:
        self.hilang = False


@contextmanager
def detak_latar(sesi: Session, job: Job, jeda: float = JEDA_DETAK) -> Iterator[Detak]:
    """Detak dari utas latar selama blok berjalan (putusan F7).

    Dipakai membungkus pemanggilan pembantu yang bisa berjam-jam (`db-impor`,
    `search-replace`): utas utama diam menunggu subprocess, jadi tanpa ini
    `locked_at` tidak diperbarui dan reaper merebut job setelah 15 menit.
    Utas memakai sesi DB sendiri (sesi SQLAlchemy tidak aman dibagi antar
    utas) dan berhenti saat blok selesai atau klaim ternyata hilang. Klaim
    yang hilang membuat blok melempar KlaimHilang begitu selesai, sehingga
    handler berhenti di titik itu dan tidak menulis apa pun lagi.
    """
    keadaan = Detak()
    if job.locked_by is None:
        yield keadaan
        return
    job_id, pemegang = job.id, job.locked_by
    bind = sesi.get_bind()
    # Sesi utama mungkin terikat ke Connection; utas butuh koneksinya sendiri.
    buat_sesi = sessionmaker(bind=getattr(bind, "engine", bind), future=True)
    henti = threading.Event()

    def putar() -> None:
        while not henti.wait(jeda):
            try:
                with buat_sesi() as s:
                    s.execute(text(f"SET LOCAL lock_timeout = '{BATAS_KUNCI_DETAK}'"))
                    if not _perpanjang(s, job_id, pemegang):
                        keadaan.hilang = True
                        log.warning("Klaim job %s sudah tidak dipegang %s; detak latar berhenti",
                                    job_id, pemegang)
                        return
            except Exception:
                # Gangguan DB sesaat: dicoba lagi pada detak berikutnya; reaper
                # baru bertindak setelah 15 menit.
                log.exception("Detak latar job %s gagal; dicoba lagi", job_id)

    utas = threading.Thread(target=putar, name=f"wpmgr-detak-job-{job_id}", daemon=True)
    utas.start()
    try:
        yield keadaan
    finally:
        henti.set()
        utas.join(TUNGGU_UTAS_DETAK)
    if keadaan.hilang:
        raise KlaimHilang(f"Klaim job {job_id} sudah tidak dipegang {pemegang}")


def harus_berhenti() -> bool:
    from wpmgr import worker

    return worker._berhenti


def periksa_batal(sesi: Session, staging: Staging) -> None:
    diminta = sesi.scalar(select(Staging.batal_diminta_pada).where(Staging.id == staging.id))
    if diminta is not None:
        raise Dibatalkan()


def titik_potongan(sesi: Session, job: Job, staging: Staging | None) -> None:
    """Dipanggil di antara potongan: batal, penghentian worker, dan detak.

    `staging` None hanya untuk kembalikan setelah staging dihapus: tidak ada
    kolom batal yang bisa diperiksa.
    """
    if staging is not None:
        periksa_batal(sesi, staging)
    if harus_berhenti():
        # Berhenti karena deploy/restart bukan kegagalan: jatah percobaan
        # tidak dihabiskan, dan progres di payload membuat job melanjutkan.
        job.attempts = max(0, job.attempts - 1)
        sesi.commit()
        raise SiteError(TRANSIENT, "Worker dihentikan; job staging dilanjutkan otomatis.")
    detak(sesi, job)


def kemajuan(job: Job) -> dict:
    return dict((job.payload or {}).get("kemajuan") or {})


def simpan_kemajuan(sesi: Session, job: Job, **perubahan) -> dict:
    payload = dict(job.payload or {})
    k = dict(payload.get("kemajuan") or {})
    k.update(perubahan)
    k["diperbarui"] = sekarang().isoformat()
    payload["kemajuan"] = k
    job.payload = payload
    flag_modified(job, "payload")
    sesi.commit()
    detak(sesi, job)
    return k


def _layak_ulang(error_class: str) -> bool:
    # UNKNOWN (tulis yang terputus sesudah dikirim) layak diulang untuk job
    # staging, sama seperti worker memperlakukannya (putusan F26).
    return error_class in DAPAT_DIULANG or error_class == UNKNOWN


def ulangi(fungsi, *args, kali: int = ULANG_POTONGAN, tidur=time.sleep, **kwargs):
    """Satu potongan diulang sampai 3 kali (spec §12), hanya untuk galat yang layak diulang."""
    for percobaan in range(1, kali + 1):
        try:
            return fungsi(*args, **kwargs)
        except SiteError as exc:
            if not _layak_ulang(exc.error_class) or percobaan == kali:
                raise
            tidur(JEDA_ULANG[min(percobaan - 1, len(JEDA_ULANG) - 1)])
    raise AssertionError("tidak tercapai")


def catat_aktivitas(sesi: Session, site_id, job: Job | None, pesan: str, detail: dict | None = None,
                    level: str = "info", user_id=None) -> None:
    uid = user_id if user_id is not None else (job.dibuat_oleh if job is not None else None)
    email = None
    if uid is not None:
        u = sesi.get(User, uid)
        email = u.email if u is not None else None
    teks = f"{pesan} oleh {email}" if email else pesan
    sesi.add(ActivityLog(site_id=site_id, job_id=job.id if job is not None else None, user_id=uid,
                         level=level, pesan=bersih_teks(teks, 500), detail=detail))


def pesan_os(exc: OSError) -> str:
    """Pesan UI untuk galat berkas. Teks OSError memuat path VPS, jadi tidak pernah dipakai."""
    if exc.errno == errno.ENOSPC:
        return ("Disk VPS penuh saat menulis staging. Kosongkan ruang lalu jalankan lagi; "
                "berkas yang sudah tersalin tidak diunduh ulang.")
    if exc.errno in (errno.EACCES, errno.EPERM):
        return "Izin berkas di server staging tidak cukup."
    return f"Galat berkas di server staging (errno {exc.errno})."


def muat_staging(sesi: Session, job: Job) -> tuple[Site, Staging]:
    if not get_settings().staging_aktif:
        raise galat_ditolak("Fitur staging tidak aktif (WPMGR_STAGING_DOMAIN kosong).")
    site = sesi.get(Site, job.site_id)
    staging = sesi.scalar(select(Staging).where(Staging.site_id == job.site_id))
    if staging is None:
        raise galat_ditolak("Staging untuk site ini belum dibuat.")
    return site, staging


def _tandai(sesi: Session, staging_id, status: StatusStaging, galat: str | None) -> None:
    sesi.rollback()
    st = sesi.get(Staging, staging_id, populate_existing=True)
    st.status = status
    st.galat = bersih_teks(galat, 1000)
    st.batal_diminta_pada = None
    sesi.commit()


def jalankan_staging(sesi: Session, job: Job, inti, status_kerja: StatusStaging, nama: str) -> dict:
    site, staging = muat_staging(sesi, job)
    staging_id, job_id = staging.id, job.id
    staging.status = status_kerja
    staging.galat = None
    sesi.commit()
    try:
        periksa_batal(sesi, staging)
        hasil = inti(sesi, job, site, staging)
    except Dibatalkan:
        sesi.rollback()
        st = sesi.get(Staging, staging_id, populate_existing=True)
        status = StatusStaging.siap if st.ditarik_pada else StatusStaging.gagal
        _tandai(sesi, staging_id, status, "Dibatalkan oleh pengguna.")
        catat_aktivitas(sesi, site.id, job, f"{nama} dibatalkan", level="warning")
        sesi.commit()
        raise galat_gagal("Dibatalkan oleh pengguna.") from None
    except KlaimHilang:
        raise
    except GalatPembantu as exc:
        _tandai(sesi, staging_id, StatusStaging.gagal, exc.pesan)
        raise galat_gagal(exc.pesan) from None
    except SiteError as exc:
        # Keputusan "final atau diulang" harus sama persis dengan worker
        # (queue.akan_diulang), termasuk batas bad_response dan UNKNOWN yang
        # oleh worker diulang sebagai TRANSIENT untuk job staging (F26).
        kelas = TRANSIENT if exc.error_class == UNKNOWN else exc.error_class
        if akan_diulang(job, kelas):
            _tandai(sesi, staging_id, status_kerja, f"Terputus, dilanjutkan otomatis: {exc.pesan}")
        else:
            _tandai(sesi, staging_id, StatusStaging.gagal, exc.pesan)
        raise
    except OSError as exc:
        pesan = pesan_os(exc)
        _tandai(sesi, staging_id, StatusStaging.gagal, pesan)
        raise galat_gagal(pesan) from None
    except Exception:
        # Bug di sisi dashboard (putusan F12). Teks pengecualian bisa memuat
        # path VPS atau isi payload, jadi hanya masuk log server; baris staging
        # mendapat pesan tetap, dan pengecualian dilempar ulang supaya worker
        # mencatat job sebagai internal_error.
        log.exception("Galat tak terduga pada %s (job %s)", nama, job_id)
        _tandai(sesi, staging_id, StatusStaging.gagal, PESAN_TAK_TERDUGA)
        raise
    st = sesi.get(Staging, staging_id, populate_existing=True)
    st.galat = None
    st.batal_diminta_pada = None
    sesi.commit()
    return hasil
