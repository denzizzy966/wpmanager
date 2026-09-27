import logging
import os
import signal
import time

from sqlalchemy.orm import Session

from wpmgr.db import get_session
from wpmgr.errors import (
    AUTH_ERROR,
    BLOCKED,
    CONNECTOR_MISSING,
    INTERNAL_ERROR,
    KELAS_STAGING,
    PACKAGE_MISSING,
    TRANSIENT,
    UNKNOWN,
    SiteError,
)
from wpmgr.jobs.handlers import HANDLER, buat_klien, resolusi_unknown
from wpmgr.jobs.queue import (
    ambil_job,
    antrekan_scan,
    selesai_gagal,
    selesai_sukses,
    tandai_unknown,
    worker_id,
)
from wpmgr.models import (
    JOB_STAGING,
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
)
from wpmgr.staging.aman import bersih_teks
from wpmgr.staging.umum import PESAN_TAK_TERDUGA

log = logging.getLogger("wpmgr.worker")
JEDA_ANTREAN_KOSONG = 5.0

STATUS_SITE_DARI_ERROR = {
    AUTH_ERROR: SiteStatus.needs_reconnect,
    CONNECTOR_MISSING: SiteStatus.needs_reconnect,
    BLOCKED: SiteStatus.blocked,
    # Hanya dipasang saat job BERAKHIR failed; lihat _catat_kegagalan.
    TRANSIENT: SiteStatus.unreachable,
}

_berhenti = False


def _tangani_sinyal(signum, frame):
    global _berhenti
    _berhenti = True
    log.info("Sinyal %s diterima; berhenti setelah job berjalan selesai", signum)


def jenis_worker(instans: str) -> str:
    """Instans systemd `wpmgr-worker@staging*` hanya mengambil job staging."""
    return "staging" if instans.startswith("staging") else "umum"


def _masih_milik_kita(sesi: Session, job_id: int, worker: str) -> bool:
    """Apakah klaim atas job ini masih milik kita.

    Reaper memutuskan sebuah job ditinggalkan semata dari umur `locked_at`, jadi
    worker yang hidup tetapi tersendat bisa kehilangan klaimnya selagi masih
    bekerja. Menulis hasil setelah itu akan menimpa keputusan reaper dan apa pun
    yang dilakukan worker berikutnya.
    """
    segar = sesi.get(Job, job_id, populate_existing=True)
    return (
        segar is not None
        and segar.status == JobStatus.running
        and segar.locked_by == worker
    )


def _pulihkan_status(sesi: Session, site: Site) -> None:
    """Job yang sukses, apa pun tipenya, mengembalikan site ke `active`.

    Site baru saja menjawab permintaan bertanda tangan dengan benar, jadi ia
    terjangkau, tidak diblokir, dan secret-nya cocok -- apa pun status lama
    yang ditempelkan kegagalan sebelumnya. Satu-satunya pengecualian adalah
    `disabled`: keputusan operator, bukan diagnosis, dan dibaca ulang dari
    database karena operator bisa menetapkannya selagi job berjalan.
    """
    sesi.refresh(site)
    if site.status != SiteStatus.disabled:
        site.status = SiteStatus.active


def proses_satu(sesi: Session, worker: str, buat_klien_fn=buat_klien, jenis: str | None = None) -> bool:
    job = ambil_job(sesi, worker, jenis)
    if job is None:
        return False

    # Disimpan sebelum handler berjalan: setelah rollback di cabang kesalahan
    # tak terduga, objek `job` sudah kedaluwarsa dan barisnya mungkin sudah
    # tidak ada.
    job_id = job.id
    site = sesi.get(Site, job.site_id)
    try:
        hasil = HANDLER[job.tipe](sesi, job, buat_klien_fn(site))
        if not _masih_milik_kita(sesi, job_id, worker):
            log.warning("Klaim job %s sudah diambil alih; hasil tidak ditulis", job_id)
            return True
        if job.tipe not in JOB_STAGING:
            # Job staging tidak membuktikan apa pun tentang koneksi ke
            # produksi (uji update bahkan tidak menghubunginya), jadi
            # suksesnya juga tidak memulihkan status site (putusan F8).
            _pulihkan_status(sesi, site)
        selesai_sukses(sesi, job, hasil if isinstance(hasil, dict) else {})
        return True
    except SiteError as exc:
        if not _masih_milik_kita(sesi, job_id, worker):
            log.warning("Klaim job %s sudah diambil alih; kegagalan tidak dicatat", job_id)
            return True
        _catat_kegagalan(sesi, job, site, exc, worker, buat_klien_fn)
        return True
    except Exception as exc:
        # Bug di sisi dashboard (KeyError pada payload, dsb.), bukan kondisi
        # site. Tanpa cabang ini job tertinggal `running` sampai reaper
        # datang 15 menit kemudian dan mengulangnya -- hanya untuk melempar
        # kesalahan yang sama lagi.
        log.exception("Kesalahan tak terduga saat menjalankan job %s", job_id)
        _catat_kesalahan_internal(sesi, job_id, worker, exc)
        return True


def _catat_kesalahan_internal(sesi: Session, job_id: int, worker: str, exc: Exception) -> None:
    # Perubahan setengah jadi dari handler dibuang lebih dulu; yang ditulis
    # sesudah ini hanya penanda kegagalan job itu sendiri.
    sesi.rollback()
    if not _masih_milik_kita(sesi, job_id, worker):
        log.warning("Klaim job %s sudah diambil alih; kesalahan internal tidak dicatat", job_id)
        return

    job = sesi.get(Job, job_id)
    if job.tipe in JOB_STAGING:
        # Teks pengecualian job staging bisa memuat path VPS atau isi payload,
        # dan job.error tampil di UI (putusan F12): traceback-nya sudah di log
        # server, yang disimpan hanya pesan tetap.
        pesan = PESAN_TAK_TERDUGA
    else:
        pesan = f"{type(exc).__name__}: {exc}"
    # Status site sengaja tidak disentuh: kesalahan ini milik dashboard.
    sesi.add(
        ActivityLog(
            site_id=job.site_id, job_id=job.id, level="error",
            pesan=f"{job.tipe.value} gagal: {INTERNAL_ERROR}",
            detail={"pesan": pesan[:500], "worker": worker},
        )
    )
    selesai_gagal(sesi, job, INTERNAL_ERROR, pesan)


def _catat_kegagalan(sesi, job, site, exc: SiteError, worker: str, buat_klien_fn) -> None:
    kelas = exc.error_class
    # Pesan bisa membawa teks connector; NUL atau surrogate tunggal di sana
    # membuat commit job.error dan detail log gagal seluruhnya.
    pesan = bersih_teks(exc.pesan, 2000)
    if kelas == UNKNOWN and job.tipe != JobType.update_package:
        # Sejak R55 ping dan inventory tidak pernah menghasilkan unknown. Bila
        # tetap terjadi, keduanya read-only dan aman diulang; menandainya
        # `unknown` berarti tidak ada yang akan pernah melihatnya lagi (klaim
        # hanya mengambil `pending`, reaper hanya `running`). Job staging juga
        # masuk ke sini (putusan F26): setiap langkahnya dapat dilanjutkan dan
        # idempoten, jadi tulis yang terputus sesudah dikirim diulang, bukan
        # dibiarkan menggantung sebagai `unknown`.
        kelas = TRANSIENT

    if kelas == UNKNOWN:
        tandai_unknown(sesi, job, pesan)
        try:
            hasil = resolusi_unknown(sesi, job, buat_klien_fn(site))
            log.info("Job %s diselesaikan lewat scan ulang: %s", job.id, hasil)
        except SiteError as exc2:
            hasil = None
            log.warning("Scan ulang untuk job %s juga gagal: %s", job.id, exc2.pesan)
        if hasil == "success":
            # Update ternyata berhasil; resolusi_unknown sudah menulis jejak
            # auditnya. Baris error dan last_error di sini hanya akan
            # mengarang kegagalan yang tidak pernah terjadi.
            _pulihkan_status(sesi, site)
            sesi.commit()
            return
    else:
        selesai_gagal(sesi, job, kelas, pesan)

    if kelas == PACKAGE_MISSING:
        # Inventaris dashboard menyimpang dari kenyataan; scan ulang yang
        # memperbaikinya, bukan percobaan ulang update yang sama.
        antrekan_scan(sesi, site.id)

    # Staging yang gagal bukan kabar tentang site produksi: galatnya disimpan
    # di staging.galat, bukan menimpa galat koneksi atau status site. Untuk
    # job staging ini berlaku apa pun kelasnya (putusan F8): 403
    # wpmgr_staging_token dibaca auth_error, dan tanpa pengecualian ini site
    # sehat berubah menjadi needs_reconnect.
    sentuh_site = job.tipe not in JOB_STAGING and kelas not in KELAS_STAGING
    status_baru = STATUS_SITE_DARI_ERROR.get(kelas) if sentuh_site else None
    if kelas == TRANSIENT and job.status != JobStatus.failed:
        # Masih akan diulang. Satu gangguan jaringan sesaat bukan alasan
        # menyatakan site tak terjangkau.
        status_baru = None
    if status_baru is not None and site.status != SiteStatus.disabled:
        site.status = status_baru
    if sentuh_site:
        site.last_error = pesan[:2000]
    # Batal oleh pengguna sudah dicatat pembungkus staging (dengan nama
    # pengguna), dan worker yang dihentikan saat deploy bukan kegagalan:
    # keduanya tidak boleh muncul sebagai baris "gagal" level error.
    if not getattr(exc, "sudah_dicatat", False):
        ringkasan = getattr(exc, "ringkasan_aktivitas", None)
        sesi.add(
            ActivityLog(
                site_id=site.id, job_id=job.id, level=getattr(exc, "level_aktivitas", "error"),
                pesan=f"{job.tipe.value} {ringkasan}" if ringkasan else f"{job.tipe.value} gagal: {kelas}",
                detail={"pesan": pesan[:500], "worker": worker},
            )
        )
    sesi.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _tangani_sinyal)
    signal.signal(signal.SIGINT, _tangani_sinyal)
    worker = worker_id()
    jenis = jenis_worker(os.environ.get("WPMGR_WORKER_INSTANS", ""))
    log.info("Worker %s (%s) mulai", worker, jenis)

    while not _berhenti:
        try:
            with get_session() as sesi:
                ada = proses_satu(sesi, worker, buat_klien, jenis)
        except Exception:
            log.exception("Kesalahan tak terduga di loop worker")
            ada = False
        if not ada:
            time.sleep(JEDA_ANTREAN_KOSONG)

    log.info("Worker %s berhenti", worker)


if __name__ == "__main__":
    main()
