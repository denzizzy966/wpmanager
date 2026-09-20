import os
import socket
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func as safunc
from sqlalchemy import text
from sqlalchemy.orm import Session

from wpmgr.errors import DAPAT_DIULANG, UNKNOWN
from wpmgr.models import Job, JobStatus, JobType

SQL_AMBIL = text(
    """
    UPDATE jobs
       SET status       = 'running',
           locked_at    = now(),
           locked_by    = :worker,
           started_at   = now(),
           attempts     = attempts + 1
     WHERE id = (
           SELECT j.id
             FROM jobs j
             JOIN sites s ON s.id = j.site_id
            WHERE j.status = 'pending'
              AND j.scheduled_for <= now()
              AND NOT EXISTS (
                    SELECT 1 FROM jobs j2
                     WHERE j2.site_id = j.site_id
                       AND j2.status = 'running')
            ORDER BY j.scheduled_for
              FOR UPDATE OF j, s SKIP LOCKED
            LIMIT 1)
    RETURNING id
    """
)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def buat_job(
    sesi: Session,
    site_id: uuid.UUID,
    tipe: JobType,
    payload: dict | None = None,
    dibuat_oleh: uuid.UUID | None = None,
    scheduled_for: datetime | None = None,
) -> Job:
    job = Job(site_id=site_id, tipe=tipe, payload=payload or {}, dibuat_oleh=dibuat_oleh)
    if scheduled_for is not None:
        job.scheduled_for = scheduled_for
    sesi.add(job)
    sesi.commit()
    return job


def ambil_job(sesi: Session, worker: str) -> Job | None:
    baris = sesi.execute(SQL_AMBIL, {"worker": worker}).first()
    sesi.commit()
    if baris is None:
        return None
    return sesi.get(Job, baris[0], populate_existing=True)


def jeda_menit(attempts: int) -> int:
    return 2**attempts


def _lepas_kunci(job: Job) -> None:
    job.locked_at = None
    job.locked_by = None


def selesai_sukses(sesi: Session, job: Job, hasil: dict) -> None:
    job.status = JobStatus.success
    job.hasil = hasil
    job.error = None
    job.error_class = None
    job.finished_at = safunc.now()
    _lepas_kunci(job)
    sesi.commit()


def selesai_gagal(sesi: Session, job: Job, error_class: str, pesan: str) -> None:
    job.error_class = error_class
    job.error = pesan[:2000]
    _lepas_kunci(job)
    boleh_ulang = error_class in DAPAT_DIULANG and job.attempts < job.max_attempts
    if boleh_ulang:
        job.status = JobStatus.pending
        job.scheduled_for = safunc.now() + timedelta(minutes=jeda_menit(job.attempts))
        job.started_at = None
    else:
        job.status = JobStatus.failed
        job.finished_at = safunc.now()
    sesi.commit()


def tandai_unknown(sesi: Session, job: Job, pesan: str) -> None:
    job.status = JobStatus.unknown
    job.error_class = UNKNOWN
    job.error = pesan[:2000]
    _lepas_kunci(job)
    sesi.commit()
