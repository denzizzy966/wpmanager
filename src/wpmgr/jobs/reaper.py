from datetime import timedelta

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from wpmgr.errors import UNKNOWN
from wpmgr.hosting import umum as hosting_umum
from wpmgr.jobs.queue import dalam_batas_pemulihan
from wpmgr.models import (
    JOB_HOSTING,
    JOB_STAGING,
    ActivityLog,
    HostingVps,
    Job,
    JobStatus,
    JobType,
    Staging,
    StatusStaging,
)
from wpmgr.staging import umum

BATAS_MENIT_DEFAULT = 15
PESAN_STAGING_TERHENTI = "Proses terhenti tak terduga; coba lagi."
# Status yang hanya benar selama sebuah job staging sedang bekerja.
STATUS_KERJA_STAGING = (StatusStaging.menyalin, StatusStaging.berjalan_uji, StatusStaging.mendorong)
# Baris pemilik (staging/hosting_vps) sedang dikunci transaksi lain: job yatim dilewati putaran ini.
_TERKUNCI = object()


def _kunci_atau_lewati(sesi: Session, kueri):
    """Kunci baris pemilik tanpa menunggu (SKIP LOCKED); `_TERKUNCI` bila transaksi lain memegangnya.

    Reaper sudah memegang kunci baris job. Menunggu kunci baris pemilik di
    sini bisa membuat siklus dengan transaksi yang memegang baris pemilik
    lalu menyentuh baris job yang sama (review Task 6 M4), jadi reaper tidak
    pernah menunggu: job itu diproses lagi pada putaran berikutnya. Baris yang
    tidak ada (atau tidak cocok) dibedakan dari yang terkunci lewat baca
    biasa, yang tidak tertahan kunci baris.
    """
    baris = sesi.scalar(kueri.with_for_update(skip_locked=True))
    if baris is None and sesi.scalar(kueri.with_only_columns(literal(1), maintain_column_froms=True).limit(1)) is not None:
        return _TERKUNCI
    return baris


def _kueri_staging(job: Job):
    return select(Staging).where(Staging.site_id == job.site_id, Staging.status.in_(STATUS_KERJA_STAGING))


def _lepas_staging(sesi: Session, job: Job, st: Staging | None) -> None:
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
    if st is None:
        return
    status, asal, galat = umum.status_gagal_final(job, st, PESAN_STAGING_TERHENTI)
    st.status = status
    st.gagal_asal = asal if status == StatusStaging.gagal else None
    st.galat = galat
    st.batal_diminta_pada = None
    if umum.menyentuh_produksi(job):
        # Kegagalan final sesudah tukar (jendela pemulihan 24 jam habis): sama
        # dengan `dorong.akhiri_gagal`, produksi ditandai belum terbukti sehat.
        st.dorong_gagal_pada = umum.sekarang()


def _kueri_hosting(job: Job):
    return select(HostingVps).where(HostingVps.site_id == job.site_id)


def _lepas_hosting(sesi: Session, job: Job, h: HostingVps | None) -> None:
    """Hosting milik job yatim yang tidak akan diulang (pola `_lepas_staging`, spec §10.6).

    Aturan status sama dengan pembungkus (`hosting.umum.status_gagal_final`).
    Backup tidak pernah mengubah status; kegagalannya ditandai
    `backup_gagal_pada`. Urutan kunci: jobs lalu hosting_vps (tanpa menunggu,
    `_kunci_atau_lewati`).
    """
    if h is None:
        return
    if job.tipe == JobType.backup_hosting:
        h.backup_gagal_pada = hosting_umum.sekarang()
        return
    if h.status not in hosting_umum.STATUS_KERJA_SEMUA:
        return
    status, asal, galat = hosting_umum.status_gagal_final(job, h, hosting_umum.PESAN_TERHENTI)
    h.status = status
    h.gagal_asal = asal
    h.galat = galat
    h.batal_diminta_pada = None


def pulihkan_job_yatim(sesi: Session, batas_menit: int = BATAS_MENIT_DEFAULT) -> int:
    """Kembalikan job `running` yang kuncinya basi ke `pending`, atau tandai `unknown` bila jatah habis.

    Urutan kunci: baris `jobs` yatim (FOR UPDATE SKIP LOCKED), lalu baris
    `staging`/`hosting_vps` (FOR UPDATE SKIP LOCKED, `_kunci_atau_lewati`; job
    yang baris pemiliknya terkunci dilewati putaran ini). Mengembalikan jumlah
    job yang diproses. Reaper TIDAK mengambil kunci
    `sites`, jadi urutannya berbeda dari kontrak route/cron di
    `wpmgr.staging.cron` (sites -> staging). Itu aman: jalur sites -> staging
    hanya MENYISIPKAN baris job baru atau membaca job, tidak pernah mengunci
    baris job yang sudah ada sesudah baris staging, jadi tidak ada siklus
    (jobs -> staging di sini, tidak pernah staging -> jobs yang sama). SKIP
    LOCKED membuat reaper melewati job yang sedang disentuh worker hidup, dan
    tidak menunggu kunci sites yang dipegang route/cron.
    """
    batas = func.now() - timedelta(minutes=batas_menit)
    yatim = sesi.scalars(
        select(Job)
        .where(Job.status == JobStatus.running, Job.locked_at < batas)
        .with_for_update(skip_locked=True)
    ).all()

    diproses = 0
    for job in yatim:
        # Produksi yang sudah ditukar dipulihkan terus sampai batas 24 jam (R26),
        # bukan sampai max_attempts: pemulihan tidak boleh bergantung pada WP-Cron.
        diulang = job.attempts < job.max_attempts or dalam_batas_pemulihan(job)
        pemilik = None
        if not diulang and job.tipe in JOB_STAGING:
            pemilik = _kunci_atau_lewati(sesi, _kueri_staging(job))
        elif not diulang and job.tipe in JOB_HOSTING:
            pemilik = _kunci_atau_lewati(sesi, _kueri_hosting(job))
        if pemilik is _TERKUNCI:
            # Job belum disentuh sama sekali; diambil lagi putaran berikutnya.
            continue
        diproses += 1
        pemegang = job.locked_by
        job.locked_at = None
        job.locked_by = None
        job.started_at = None
        if diulang:
            job.status = JobStatus.pending
            pesan = f"Job dipulihkan dari worker yang mati ({pemegang}); dijadwalkan ulang"
        else:
            job.status = JobStatus.unknown
            job.error_class = UNKNOWN
            job.error = f"Worker {pemegang} berhenti dan jatah percobaan habis"
            pesan = f"Job ditinggalkan worker {pemegang} tanpa sisa percobaan"
            if job.tipe in JOB_STAGING:
                _lepas_staging(sesi, job, pemilik)
            elif job.tipe in JOB_HOSTING:
                _lepas_hosting(sesi, job, pemilik)
        sesi.add(
            ActivityLog(
                site_id=job.site_id, job_id=job.id, level="warning", pesan=pesan,
                detail={"attempts": job.attempts, "locked_by": pemegang},
            )
        )

    sesi.commit()
    return diproses
