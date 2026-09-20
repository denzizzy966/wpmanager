import os
import socket
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from wpmgr.models import Job, JobType

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
            WHERE j.status = 'pending'
              AND j.scheduled_for <= now()
              AND NOT EXISTS (
                    SELECT 1 FROM jobs j2
                     WHERE j2.site_id = j.site_id
                       AND j2.status = 'running')
            ORDER BY j.scheduled_for
              FOR UPDATE SKIP LOCKED
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
    return sesi.get(Job, baris[0])
