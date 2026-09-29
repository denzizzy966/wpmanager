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
from wpmgr.jobs.queue import (  # noqa: F401
    LANGKAH_SESUDAH_TUKAR,
    akan_diulang,
    menyentuh_produksi,
)
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    Site,
    Staging,
    StatusStaging,
    User,
)
from wpmgr.staging.aman import (
    BATAS_DIUBAH,
    TOLERANSI_JAM,
    PathTidakAman,
    baca_terbatas,
    bersih_json,
    bersih_teks,
    waktu_penanda,
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
PESAN_DIBATALKAN = "Dibatalkan oleh pengguna."
PESAN_BATAL_TENGAH = "Penyegaran dibatalkan di tengah; salinan staging belum utuh, segarkan ulang."


class KlaimHilang(Exception):
    """Reaper atau worker lain sudah mengambil alih job ini."""


class Dibatalkan(Exception):
    """Pengguna meminta pembatalan (staging.batal_diminta_pada)."""


class GalatDibatalkan(SiteError):
    """Job berakhir karena pengguna membatalkannya: final, tetapi bukan kegagalan.

    Pembungkus sudah mencatat aktivitasnya (dengan nama pengguna), jadi worker
    tidak menambah baris "gagal" level error.
    """

    sudah_dicatat = True

    def __init__(self) -> None:
        super().__init__(STAGING_GAGAL, PESAN_DIBATALKAN)


class GalatBerhenti(SiteError):
    """Worker dihentikan (deploy/restart) di tengah job; job dilanjutkan otomatis."""

    level_aktivitas = "info"
    ringkasan_aktivitas = "dihentikan bersama worker; dilanjutkan otomatis"
    sudah_dicatat = False

    def __init__(self) -> None:
        super().__init__(TRANSIENT, "Worker dihentikan; job staging dilanjutkan otomatis.")


class GalatDitolakTanpaUbah(SiteError):
    """Permintaan ditolak sebelum staging disentuh (mis. konfirmasi belum ada).

    Penolakan seperti ini tidak berarti staging rusak: pembungkus
    mengembalikan statusnya seperti `_batalkan` (siap bila pernah ditarik),
    bukan menandainya gagal. Job tetap berakhir gagal dengan pesan tetap ini.
    """

    def __init__(self, pesan: str) -> None:
        super().__init__(STAGING_DITOLAK, pesan)


def galat_ditolak(pesan: str) -> SiteError:
    return SiteError(STAGING_DITOLAK, pesan)


def galat_gagal(pesan: str) -> SiteError:
    return SiteError(STAGING_GAGAL, pesan)


def buat_pembantu() -> Pembantu:
    return Pembantu.dari_setelan()


def buat_http() -> httpx.Client:
    """Klien HTTP untuk probe staging dan cek halaman utama produksi.

    Tanpa keep-alive: tenggat total `site_client.minta_bertenggat` memutus
    permintaan lewat soket yang dibuka untuknya, dan koneksi dari pool tidak
    membuka soket baru yang bisa ditangkap.
    """
    return httpx.Client(follow_redirects=False, timeout=30.0, headers={"User-Agent": UA},
                        limits=httpx.Limits(max_keepalive_connections=0))


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
    return waktu_penanda(mentah, sekarang())


def perbarui_diubah(staging: Staging) -> None:
    d = baca_diubah(staging.site_id)
    lama = staging.diubah_pada
    if lama is not None and lama > sekarang() + TOLERANSI_JAM:
        # Nilai masa depan yang sempat tersimpan tidak boleh menahan setiap
        # pembaruan sesudahnya.
        lama = None
    if d is not None and (lama is None or d > lama):
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
    # Transaksi utas utama yang masih terbuka bisa memegang kunci baris job
    # (mis. kemajuan yang sudah di-flush); detak latar akan tertahan olehnya
    # sepanjang blok. Di-commit di sini supaya blok selalu dimulai bersih.
    sesi.commit()
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


def _batal_diminta(sesi: Session, staging_id) -> bool:
    return sesi.scalar(select(Staging.batal_diminta_pada).where(Staging.id == staging_id)) is not None


def periksa_batal(sesi: Session, staging: Staging) -> None:
    if _batal_diminta(sesi, staging.id):
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
        raise GalatBerhenti()
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
                         level=level, pesan=bersih_teks(teks, 500),
                         detail=None if detail is None else bersih_json(detail)))


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


_TETAP = object()


def _tandai(sesi: Session, staging_id, status: StatusStaging, galat: str | None,
            bersihkan_batal: bool = True, asal=_TETAP) -> None:
    """Tulis status staging. `asal` (R20) hanya diubah bila diberikan; status bukan gagal selalu tanpa asal."""
    sesi.rollback()
    st = sesi.get(Staging, staging_id, populate_existing=True)
    st.status = status
    st.galat = bersih_teks(galat, 1000)
    if asal is not _TETAP:
        st.gagal_asal = asal if status == StatusStaging.gagal else None
    if bersihkan_batal:
        st.batal_diminta_pada = None
    sesi.commit()


def status_istirahat(st: Staging) -> StatusStaging:
    """Status staging tanpa job yang berjalan, diturunkan dari keadaannya sendiri.

    Kembalikan boleh berjalan saat staging dijeda (container mati, `aktif`
    False): pengembalian produksi tidak membutuhkan staging, dan sesudahnya
    staging harus tetap tampil dijeda, bukan siap.
    """
    if st.ditarik_pada is None:
        return StatusStaging.gagal
    return StatusStaging.siap if st.aktif else StatusStaging.dijeda


# ---- putusan R18-R22: asal status `gagal` -------------------------------------
#
# `staging.gagal_asal` menandai siapa pemilik status `gagal`:
# - 'salinan': tarik/uji gagal final, atau dibatalkan sesudah menyentuh
#   salinan -- berkas atau database staging bisa setengah disegarkan. Dorong
#   ditolak (R19). Hanya tarik/uji yang sukses menghapusnya (R22).
# - 'produksi': dorong/kembalikan gagal final SESUDAH tukar dikirim pada
#   salinan yang utuh. Job dorong/kembalikan berikutnya yang sukses
#   membersihkannya.
# Dorong/kembalikan tidak pernah menandai SALINAN rusak: gagal final sebelum
# tukar, batal, dan penolakan mengembalikan status staging sebelum job itu.
# R22: salinan yang belum utuh sebelum dorong/kembalikan (`gagal` 'salinan',
# atau tarik/uji tertunda: `menyalin`/`berjalan_uji`) tidak pernah ditimpa job
# itu -- apa pun hasilnya, status, penanda, dan galat salinan dikembalikan;
# masalah produksi hanya ditandai `dorong_gagal_pada`
# (`salinan_tidak_utuh_sebelum`).

ASAL_SALINAN = "salinan"
ASAL_PRODUKSI = "produksi"
JOB_PRODUKSI = frozenset({JobType.staging_dorong, JobType.staging_kembalikan})
# LANGKAH_SESUDAH_TUKAR dan menyentuh_produksi hidup di `jobs.queue` (dipakai
# juga keputusan ulang worker dan reaper, putusan R26) dan diimpor di atas.
STATUS_KERJA = frozenset({StatusStaging.menyalin, StatusStaging.berjalan_uji, StatusStaging.mendorong})
# Status kerja tarik/uji: salinan sedang (atau menunggu untuk) disegarkan.
STATUS_SALINAN_KERJA = frozenset({StatusStaging.menyalin.value, StatusStaging.berjalan_uji.value})
# Tahap tarik (juga tarik di awal uji) sebelum salinan (files/ dan database
# staging) mulai ditulis. Manifest hanya ditulis ke area kerja tarik/.
TAHAP_SEBELUM_SALINAN = (None, "manifest")


def catat_status_awal(sesi: Session, job: Job, st: Staging) -> None:
    """Status, asal, dan galat staging sebelum job ini, dicatat SEKALI (percobaan ulang melihat status kerja)."""
    if "status_staging_awal" not in kemajuan(job):
        simpan_kemajuan(sesi, job, status_staging_awal=st.status.value, gagal_asal_awal=st.gagal_asal,
                        galat_staging_awal=st.galat)


def status_sebelum(job: Job, st: Staging) -> tuple[StatusStaging, str | None]:
    """(status, asal) staging sebelum job ini, untuk dikembalikan bila job ini tidak mengubah apa pun."""
    k = kemajuan(job)
    awal = k.get("status_staging_awal")
    if awal == StatusStaging.gagal.value:
        return StatusStaging.gagal, k.get("gagal_asal_awal")
    if awal in (StatusStaging.siap.value, StatusStaging.dijeda.value):
        return StatusStaging(awal), None
    if awal in STATUS_SALINAN_KERJA and job.tipe in JOB_PRODUKSI:
        # R22: kembalikan dimulai saat tarik/uji tertunda; tidak pernah diangkat ke siap.
        return StatusStaging(awal), None
    # Tidak tercatat, atau status kerja yang basi: seperti sebelum R20.
    if st.ditarik_pada:
        return StatusStaging.siap, None
    return StatusStaging.gagal, ASAL_SALINAN


def salinan_belum_disentuh(job: Job) -> bool:
    """R21/R22: tarik (atau tarik di awal uji) ini belum menulis apa pun ke salinan, termasuk percobaan sebelumnya."""
    return kemajuan(job).get("tahap") in TAHAP_SEBELUM_SALINAN


def salinan_tidak_utuh_sebelum(job: Job) -> bool:
    """R22: sebelum dorong/kembalikan ini salinan belum utuh; apa pun hasil job ini, keadaan itu dipertahankan."""
    if job.tipe not in JOB_PRODUKSI:
        return False
    k = kemajuan(job)
    awal = k.get("status_staging_awal")
    return (awal == StatusStaging.gagal.value and k.get("gagal_asal_awal") == ASAL_SALINAN) \
        or awal in STATUS_SALINAN_KERJA


def galat_salinan(job: Job, pesan: str | None) -> str | None:
    """Galat staging untuk hasil dorong/kembalikan: galat salinan yang belum utuh menang (R22)."""
    if salinan_tidak_utuh_sebelum(job):
        galat = kemajuan(job).get("galat_staging_awal")
        if galat is not None:
            return galat
    return pesan


def status_sukses_produksi(job: Job, st: Staging) -> StatusStaging:
    """Status staging sesudah dorong/kembalikan yang sukses (R18/R22)."""
    if salinan_tidak_utuh_sebelum(job):
        return status_sebelum(job, st)[0]
    return status_istirahat(st)


def status_gagal_final(job: Job, st: Staging, pesan: str) -> tuple[StatusStaging, str | None, str]:
    """(status, asal, galat) staging untuk kegagalan FINAL job ini (pembungkus, `akhiri_gagal`, reaper)."""
    if job.tipe in JOB_PRODUKSI:
        if menyentuh_produksi(job) and not salinan_tidak_utuh_sebelum(job):
            return StatusStaging.gagal, ASAL_PRODUKSI, pesan
        status, asal = status_sebelum(job, st)
        return status, asal, galat_salinan(job, pesan)
    return StatusStaging.gagal, ASAL_SALINAN, pesan


def galat_tetap(job: Job, status: StatusStaging, pesan: str) -> str:
    """Galat staging sesudah batal/penolakan: galat `gagal` sebelumnya dipertahankan bila statusnya tetap gagal."""
    k = kemajuan(job)
    if status == StatusStaging.gagal and k.get("status_staging_awal") == StatusStaging.gagal.value \
            and k.get("galat_staging_awal") is not None:
        return k["galat_staging_awal"]
    return galat_salinan(job, pesan)


def _status_batal(job: Job, st: Staging) -> tuple[StatusStaging, str | None, str]:
    if job.tipe in JOB_PRODUKSI or salinan_belum_disentuh(job):
        # Batal dorong/kembalikan hanya berlaku sebelum tukar, dan tarik/uji
        # yang belum menyentuh salinan tidak mengubah apa pun (R22).
        status, asal = status_sebelum(job, st)
        return status, asal, galat_tetap(job, status, PESAN_DIBATALKAN)
    # Salinan sudah mulai ditulis (atau uji sudah menjalankan update): belum utuh.
    return StatusStaging.gagal, ASAL_SALINAN, PESAN_BATAL_TENGAH


def _batalkan(sesi: Session, job: Job, site_id, staging_id, nama: str) -> GalatDibatalkan:
    sesi.rollback()
    st = sesi.get(Staging, staging_id, populate_existing=True)
    status, asal, galat = _status_batal(job, st)
    # Pesan pembatalan tetap sampai ke job dan log aktivitas di bawah.
    _tandai(sesi, staging_id, status, galat, asal=asal)
    catat_aktivitas(sesi, site_id, job, f"{nama} dibatalkan", level="warning")
    sesi.commit()
    return GalatDibatalkan()


def _tandai_gagal_final(sesi: Session, job: Job, staging_id, pesan: str) -> None:
    sesi.rollback()
    st = sesi.get(Staging, staging_id, populate_existing=True)
    status, asal, galat = status_gagal_final(job, st, pesan)
    _tandai(sesi, staging_id, status, galat, asal=asal)


def jalankan_staging(sesi: Session, job: Job, inti, status_kerja: StatusStaging, nama: str,
                     boleh_batal=None) -> dict:
    """Pembungkus bersama job staging: status, batal, asal gagal (R20), dan pemetaan galat.

    `boleh_batal(job) -> bool` (opsional) dibaca dari kemajuan yang sudah
    ter-commit. False berarti permintaan batal tidak lagi mengakhiri job --
    dipakai dorong sesudah tukar dikirim ke produksi: berhenti di sana
    meninggalkan produksi setengah jadi, jadi job diulang sampai tuntas dan
    permintaan batalnya dibiarkan tercatat (dihapus saat job selesai).

    Status akhir mengikuti tipe job (lihat blok R18/R19/R20 di atas):
    penolakan tanpa ubah selalu mengembalikan status sebelum job; gagal final
    tarik/uji menandai `gagal` 'salinan'; dorong/kembalikan menandai `gagal`
    'produksi' hanya sesudah tukar, dan selain itu mengembalikan status
    sebelumnya dengan pesan kegagalannya di `galat`.
    """
    site, staging = muat_staging(sesi, job)
    staging_id, job_id, site_id = staging.id, job.id, site.id
    catat_status_awal(sesi, job, staging)
    staging.status = status_kerja
    staging.galat = None
    sesi.commit()

    def batal_berlaku() -> bool:
        return boleh_batal is None or boleh_batal(job)

    try:
        if batal_berlaku():
            periksa_batal(sesi, staging)
        hasil = inti(sesi, job, site, staging)
    except Dibatalkan:
        raise _batalkan(sesi, job, site_id, staging_id, nama) from None
    except KlaimHilang:
        raise
    except GalatDitolakTanpaUbah as exc:
        sesi.rollback()
        st = sesi.get(Staging, staging_id, populate_existing=True)
        status, asal = status_sebelum(job, st)
        _tandai(sesi, staging_id, status, galat_tetap(job, status, exc.pesan), asal=asal)
        raise
    except GalatPembantu as exc:
        _tandai_gagal_final(sesi, job, staging_id, exc.pesan)
        raise galat_gagal(exc.pesan) from None
    except SiteError as exc:
        # Perubahan setengah jadi dari inti (termasuk pada objek job) dibuang
        # dulu: keputusan di bawah harus memakai keadaan yang ter-commit.
        sesi.rollback()
        if batal_berlaku() and _batal_diminta(sesi, staging_id):
            # Pembatalan menang atas percobaan ulang: tanpa ini dorong yang
            # dibatalkan tetap berlanjut pada putaran berikutnya.
            raise _batalkan(sesi, job, site_id, staging_id, nama) from None
        # Keputusan "final atau diulang" harus sama persis dengan worker
        # (queue.akan_diulang), termasuk batas bad_response dan UNKNOWN yang
        # oleh worker diulang sebagai TRANSIENT untuk job staging (F26).
        kelas = TRANSIENT if exc.error_class == UNKNOWN else exc.error_class
        if akan_diulang(job, kelas):
            # Permintaan batal yang tiba sesudah pemeriksaan di atas tidak
            # dihapus; putaran berikutnya berhenti di pemeriksaan awalnya.
            _tandai(sesi, staging_id, status_kerja, f"Terputus, dilanjutkan otomatis: {exc.pesan}",
                    bersihkan_batal=False)
        else:
            _tandai_gagal_final(sesi, job, staging_id, exc.pesan)
        raise
    except OSError as exc:
        pesan = pesan_os(exc)
        _tandai_gagal_final(sesi, job, staging_id, pesan)
        raise galat_gagal(pesan) from None
    except Exception:
        # Bug di sisi dashboard (putusan F12). Teks pengecualian bisa memuat
        # path VPS atau isi payload, jadi hanya masuk log server; baris staging
        # mendapat pesan tetap, dan pengecualian dilempar ulang supaya worker
        # mencatat job sebagai internal_error.
        log.exception("Galat tak terduga pada %s (job %s)", nama, job_id)
        _tandai_gagal_final(sesi, job, staging_id, PESAN_TAK_TERDUGA)
        raise
    st = sesi.get(Staging, staging_id, populate_existing=True)
    # R18/R22: dorong/kembalikan yang sukses tidak menghapus keadaan salinan
    # yang belum utuh (statusnya dipertahankan inti lewat `tuntaskan_sukses`).
    st.galat = galat_salinan(job, None)
    if st.status != StatusStaging.gagal:
        st.gagal_asal = None
    st.batal_diminta_pada = None
    sesi.commit()
    return hasil
