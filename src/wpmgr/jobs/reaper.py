from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wpmgr.models import ActivityLog, Job, JobStatus

BATAS_MENIT_DEFAULT = 15


def pulihkan_job_yatim(sesi: Session, batas_menit: int = BATAS_MENIT_DEFAULT) -> int:
    batas = func.now() - timedelta(minutes=batas_menit)
    yatim = sesi.scalars(
        select(Job).where(Job.status == JobStatus.running, Job.locked_at < batas)
    ).all()

    for job in yatim:
        pemegang = job.locked_by
        job.locked_at = None
        job.locked_by = None
        job.started_at = None
        if job.attempts < job.max_attempts:
            job.status = JobStatus.pending
            pesan = f"Job dipulihkan dari worker yang mati ({pemegang}); dijadwalkan ulang"
        else:
            job.status = JobStatus.unknown
            job.error_class = "unknown"
            job.error = f"Worker {pemegang} berhenti dan jatah percobaan habis"
            pesan = f"Job ditinggalkan worker {pemegang} tanpa sisa percobaan"
        sesi.add(
            ActivityLog(
                site_id=job.site_id, job_id=job.id, level="warning", pesan=pesan,
                detail={"attempts": job.attempts, "locked_by": pemegang},
            )
        )

    sesi.commit()
    return len(yatim)
