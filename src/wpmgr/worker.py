import logging
import signal
import time

from sqlalchemy.orm import Session

from wpmgr.db import get_session
from wpmgr.errors import (
    AUTH_ERROR,
    BLOCKED,
    CONNECTOR_MISSING,
    TRANSIENT,
    UNKNOWN,
    SiteError,
)
from wpmgr.jobs.handlers import HANDLER, buat_klien, resolusi_unknown
from wpmgr.jobs.queue import (
    ambil_job,
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
    TRANSIENT: SiteStatus.unreachable,
}

_berhenti = False


def _tangani_sinyal(signum, frame):
    global _berhenti
    _berhenti = True
    log.info("Sinyal %s diterima; berhenti setelah job berjalan selesai", signum)


def _masih_milik_kita(sesi: Session, job: Job, worker: str) -> bool:
    """Apakah klaim atas job ini masih milik kita.

    Reaper memutuskan sebuah job ditinggalkan semata dari umur `locked_at`, jadi
    worker yang hidup tetapi tersendat bisa kehilangan klaimnya selagi masih
    bekerja. Menulis hasil setelah itu akan menimpa keputusan reaper dan apa pun
    yang dilakukan worker berikutnya.
    """
    segar = sesi.get(Job, job.id, populate_existing=True)
    return (
        segar is not None
        and segar.status == JobStatus.running
        and segar.locked_by == worker
    )


def proses_satu(sesi: Session, worker: str, buat_klien_fn=buat_klien) -> bool:
    job = ambil_job(sesi, worker)
    if job is None:
        return False

    site = sesi.get(Site, job.site_id)
    try:
        hasil = HANDLER[job.tipe](sesi, job, buat_klien_fn(site))
        if not _masih_milik_kita(sesi, job, worker):
            log.warning("Klaim job %s sudah diambil alih; hasil tidak ditulis", job.id)
            return True
        selesai_sukses(sesi, job, hasil if isinstance(hasil, dict) else {})
        return True
    except SiteError as exc:
        if not _masih_milik_kita(sesi, job, worker):
            log.warning("Klaim job %s sudah diambil alih; kegagalan tidak dicatat", job.id)
            return True
        _catat_kegagalan(sesi, job, site, exc, worker, buat_klien_fn)
        return True


def _catat_kegagalan(sesi, job, site, exc: SiteError, worker: str, buat_klien_fn) -> None:
    if exc.error_class == UNKNOWN and job.tipe == JobType.update_package:
        tandai_unknown(sesi, job, exc.pesan)
        try:
            hasil = resolusi_unknown(sesi, job, buat_klien_fn(site))
            log.info("Job %s diselesaikan lewat scan ulang: %s", job.id, hasil)
        except SiteError as exc2:
            log.warning("Scan ulang untuk job %s juga gagal: %s", job.id, exc2.pesan)
    elif exc.error_class == UNKNOWN:
        tandai_unknown(sesi, job, exc.pesan)
    else:
        selesai_gagal(sesi, job, exc.error_class, exc.pesan)

    status_baru = STATUS_SITE_DARI_ERROR.get(exc.error_class)
    if status_baru is not None and site.status != SiteStatus.disabled:
        site.status = status_baru
    site.last_error = exc.pesan[:2000]
    sesi.add(
        ActivityLog(
            site_id=site.id, job_id=job.id, level="error",
            pesan=f"{job.tipe.value} gagal: {exc.error_class}",
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
