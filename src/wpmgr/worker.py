import logging
import signal
import time

from sqlalchemy.orm import Session

from wpmgr.db import get_session
from wpmgr.errors import (
    AUTH_ERROR,
    BLOCKED,
    CONNECTOR_MISSING,
    INTERNAL_ERROR,
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
from wpmgr.models import ActivityLog, Job, JobStatus, JobType, Site, SiteStatus

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


def proses_satu(sesi: Session, worker: str, buat_klien_fn=buat_klien) -> bool:
    job = ambil_job(sesi, worker)
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
    if kelas == UNKNOWN and job.tipe != JobType.update_package:
        # Sejak R55 ping dan inventory tidak pernah menghasilkan unknown. Bila
        # tetap terjadi, keduanya read-only dan aman diulang; menandainya
        # `unknown` berarti tidak ada yang akan pernah melihatnya lagi (klaim
        # hanya mengambil `pending`, reaper hanya `running`).
        kelas = TRANSIENT

    if kelas == UNKNOWN:
        tandai_unknown(sesi, job, exc.pesan)
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
        selesai_gagal(sesi, job, kelas, exc.pesan)

    if kelas == PACKAGE_MISSING:
        # Inventaris dashboard menyimpang dari kenyataan; scan ulang yang
        # memperbaikinya, bukan percobaan ulang update yang sama.
        antrekan_scan(sesi, site.id)

    status_baru = STATUS_SITE_DARI_ERROR.get(kelas)
    if kelas == TRANSIENT and job.status != JobStatus.failed:
        # Masih akan diulang. Satu gangguan jaringan sesaat bukan alasan
        # menyatakan site tak terjangkau.
        status_baru = None
    if status_baru is not None and site.status != SiteStatus.disabled:
        site.status = status_baru
    site.last_error = exc.pesan[:2000]
    sesi.add(
        ActivityLog(
            site_id=site.id, job_id=job.id, level="error",
            pesan=f"{job.tipe.value} gagal: {kelas}",
            detail={"pesan": exc.pesan[:500], "worker": worker},
        )
    )
    sesi.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _tangani_sinyal)
    signal.signal(signal.SIGINT, _tangani_sinyal)
    worker = worker_id()
    log.info("Worker %s mulai", worker)

    while not _berhenti:
        try:
            with get_session() as sesi:
                ada = proses_satu(sesi, worker, buat_klien)
        except Exception:
            log.exception("Kesalahan tak terduga di loop worker")
            ada = False
        if not ada:
            time.sleep(JEDA_ANTREAN_KOSONG)

    log.info("Worker %s berhenti", worker)


if __name__ == "__main__":
    main()
