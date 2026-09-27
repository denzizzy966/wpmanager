from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wpmgr.errors import UNKNOWN
from wpmgr.models import (
    JOB_STAGING,
    ActivityLog,
    Job,
    JobStatus,
    Staging,
    StatusStaging,
)
from wpmgr.staging import umum

BATAS_MENIT_DEFAULT = 15
PESAN_STAGING_TERHENTI = "Proses terhenti tak terduga; coba lagi."
# Status yang hanya benar selama sebuah job staging sedang bekerja.
STATUS_KERJA_STAGING = (StatusStaging.menyalin, StatusStaging.berjalan_uji, StatusStaging.mendorong)


def _lepas_staging(sesi: Session, job: Job) -> None:
    """Staging milik job yatim yang tidak akan diulang tidak boleh tertahan di status kerja.

    Tanpa ini baris staging tetap `menyalin`/`mendorong` selamanya: tidak
    ada job lagi yang akan memasang status akhirnya, dan UI menahan setiap
    aksi baru. Keadaan produksi (dorongan setengah jalan) bukan urusan di
    sini; rekonsiliasi dorong yang menanganinya. Ditulis di transaksi yang
    sama dengan penanda job `unknown`.

    Status, asal, dan galat (putusan R20/R22) sama dengan gagal final di
    pembungkus (`umum.status_gagal_final`): tarik/uji -> `gagal` 'salinan';
    dorong/kembalikan -> `gagal` 'produksi' hanya bila tukar sudah dikirim pada
    salinan yang utuh, selain itu status staging sebelum job itu dikembalikan
    (salinan yang belum utuh tetap dengan galatnya).
    """
    st =sesi.scalar(select(Staging).where(Staging.site_id == job.site_id,
                                           Staging.status.in_(STATUS_KERJA_STAGING)).with_for_update())
    if st is None:
        return
    status, asal, galat = umum.status_gagal_final(job, st, PESAN_STAGING_TERHENTI)
    st.status = status
    st.gagal_asal = asal if status == StatusStaging.gagal else None
    st.galat = galat
    st.batal_diminta_pada = None


def pulihkan_job_yatim(sesi: Session, batas_menit: int = BATAS_MENIT_DEFAULT) -> int:
    batas = func.now() - timedelta(minutes=batas_menit)
    yatim = sesi.scalars(
        select(Job)
        .where(Job.status == JobStatus.running, Job.locked_at < batas)
        .with_for_update(skip_locked=True)
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
            job.error_class = UNKNOWN
            job.error = f"Worker {pemegang} berhenti dan jatah percobaan habis"
            pesan = f"Job ditinggalkan worker {pemegang} tanpa sisa percobaan"
            if job.tipe in JOB_STAGING:
                _lepas_staging(sesi, job)
        sesi.add(
            ActivityLog(
                site_id=job.site_id, job_id=job.id, level="warning", pesan=pesan,
                detail={"attempts": job.attempts, "locked_by": pemegang},
            )
        )

    sesi.commit()
    return len(yatim)
