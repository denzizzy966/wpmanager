import logging
import os
import socket
import uuid
from datetime import datetime, timedelta

from sqlalchemy import String, bindparam, select, text
from sqlalchemy import func as safunc
from sqlalchemy.orm import Session

from wpmgr.errors import BAD_RESPONSE, DAPAT_DIULANG, UNKNOWN
from wpmgr.models import Job, JobStatus, JobType

log = logging.getLogger("wpmgr.jobs.queue")

# Percobaan pertama ditambah satu ulangan.
BATAS_PERCOBAAN_BAD_RESPONSE = 2

_STAGING = "('staging_tarik', 'staging_uji_update', 'staging_dorong', 'staging_kembalikan')"
_STAGING_BACA = "('staging_tarik', 'staging_uji_update')"
# Pasangan (j, j2) yang TIDAK boleh berjalan bersamaan di satu site.
_BENTROK = (
    f"NOT (j.tipe IN {_STAGING_BACA} AND j2.tipe NOT IN {_STAGING})"
    f" AND NOT (j2.tipe IN {_STAGING_BACA} AND j.tipe NOT IN {_STAGING})"
)

# Satu job berjalan per site, dengan dua pengecualian (Koreksi #1): tarik
# dan uji staging hanya membaca produksi, jadi tidak menahan dan tidak
# ditahan job non-staging. Dorong/kembalikan menulis ke produksi dan tetap
# eksklusif terhadap semuanya. `:jenis` memisahkan worker staging (job
# berjam-jam) dari worker umum.
#
# NOT EXISTS di sini hanya penyaring cepat: di READ COMMITTED ia membaca
# snapshot awal pernyataan, jadi job yang baru di-commit worker lain sesudah
# snapshot itu tidak terlihat. Penjaga yang sebenarnya adalah
# `_masih_eksklusif` sesudahnya.
SQL_AMBIL = text(
    f"""
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
              AND s.status <> 'disabled'
              AND (CAST(:jenis AS text) IS NULL
                   OR (CAST(:jenis AS text) = 'staging') = (j.tipe IN {_STAGING}))
              AND NOT EXISTS (
                    SELECT 1 FROM jobs j2
                     WHERE j2.site_id = j.site_id
                       AND j2.status = 'running'
                       AND {_BENTROK})
            ORDER BY j.scheduled_for
              FOR UPDATE OF j, s SKIP LOCKED
            LIMIT 1)
    RETURNING id, site_id
    """
# Nilai bawaan None: pemanggil lama (dan test antrean Lapis 1) yang hanya
# mengirim :worker tetap mendapat perilaku "klaim apa saja".
).bindparams(bindparam("jenis", value=None, type_=String))

# Klaim per site diserialkan dengan advisory lock transaksi (bentuk dua
# int4, ruang kunci terpisah dari kunci cron bigint di wpmgr.kunci). Lock
# dilepas saat commit, jadi pemegang berikutnya pasti melihat klaim yang
# sudah di-commit ketika memeriksa ulang.
SQL_KUNCI_SITE = text("SELECT pg_advisory_xact_lock(:ruang, hashtext(CAST(:site AS text)))")
RUANG_KUNCI_KLAIM = 72_140_100
SQL_BENTROK = text(
    f"""
    SELECT 1
      FROM jobs j
      JOIN jobs j2 ON j2.site_id = j.site_id AND j2.id <> j.id
     WHERE j.id = :id
       AND j2.status = 'running'
       AND {_BENTROK}
     LIMIT 1
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
    max_attempts: int | None = None,
) -> Job:
    job = Job(site_id=site_id, tipe=tipe, payload=payload or {}, dibuat_oleh=dibuat_oleh)
    if scheduled_for is not None:
        job.scheduled_for = scheduled_for
    if max_attempts is not None:
        job.max_attempts = max_attempts
    sesi.add(job)
    sesi.commit()
    return job


def antrekan_jika_belum(
    sesi: Session, site_id: uuid.UUID, tipe: JobType, max_attempts: int | None = None
) -> Job | None:
    """Buat job bertipe ini untuk site, kecuali sudah ada yang tertunda/berjalan.

    Periksa-lalu-sisipkan tidak atomik: dua pemanggil bersamaan bisa sama-sama
    membuat job. Itu dibiarkan -- semua pemakainya (scan, verify, pengambilan
    berkala) hanya membaca, jadi job ganda tidak merusak apa pun, sedangkan
    kunci advisory demi mencegahnya menambah bagian bergerak tanpa
    melindungi apa pun yang berharga.
    """
    sudah_ada = sesi.scalar(
        select(Job.id).where(
            Job.site_id == site_id,
            Job.tipe == tipe,
            Job.status.in_([JobStatus.pending, JobStatus.running]),
        )
    )
    if sudah_ada is not None:
        return None
    return buat_job(sesi, site_id, tipe, max_attempts=max_attempts)


def antrekan_scan(sesi: Session, site_id: uuid.UUID) -> Job | None:
    return antrekan_jika_belum(sesi, site_id, JobType.scan_site)


def _masih_eksklusif(sesi: Session, job_id: int, site_id) -> bool:
    """Periksa ulang, di transaksi klaim, bahwa tidak ada job bentrok yang berjalan.

    Celah yang ditutup: worker A meng-commit klaim dorong tepat sesudah
    snapshot pernyataan klaim worker B diambil tetapi sebelum B mengunci
    baris site. NOT EXISTS milik B tidak melihat klaim A, sehingga dorong
    (menulis ke produksi) bisa berjalan bersama job lain. Dengan lock per
    site, B baru memeriksa setelah A commit, dan pernyataan baru di READ
    COMMITTED memakai snapshot baru yang melihat klaim A.
    """
    sesi.execute(SQL_KUNCI_SITE, {"ruang": RUANG_KUNCI_KLAIM, "site": str(site_id)})
    return sesi.execute(SQL_BENTROK, {"id": job_id}).first() is None


def ambil_job(sesi: Session, worker: str, jenis: str | None = None) -> Job | None:
    baris = sesi.execute(SQL_AMBIL, {"worker": worker, "jenis": jenis}).first()
    if baris is None:
        sesi.commit()
        return None
    job_id, site_id = baris
    if not _masih_eksklusif(sesi, job_id, site_id):
        # Klaim dibatalkan utuh (status, attempts); job tetap pending dan
        # diambil lagi setelah job yang bentrok selesai.
        sesi.rollback()
        log.info("Klaim job %s dibatalkan: job lain di site yang sama baru saja berjalan", job_id)
        return None
    sesi.commit()
    return sesi.get(Job, job_id, populate_existing=True)


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


def _batas_percobaan(job: Job, error_class: str) -> int:
    # Spec §9: bad_response diulang sekali. Penyebab umumnya -- plugin lain
    # mencetak warning sebelum JSON, cache mengembalikan HTML -- tidak hilang
    # dalam hitungan menit, jadi percobaan ketiga hanya menunda operator
    # melihat masalahnya. Dihitung dari `attempts`: bad_response hanya diulang
    # bila ia terjadi pada percobaan pertama.
    if error_class == BAD_RESPONSE:
        return min(job.max_attempts, BATAS_PERCOBAAN_BAD_RESPONSE)
    return job.max_attempts


def akan_diulang(job: Job, error_class: str) -> bool:
    """Apakah kegagalan kelas ini pada percobaan `job.attempts` dijadwalkan ulang.

    Satu sumber kebenaran untuk `selesai_gagal` dan pembungkus job staging,
    yang harus tahu lebih dulu apakah kegagalannya final.
    """
    return error_class in DAPAT_DIULANG and job.attempts < _batas_percobaan(job, error_class)


def selesai_gagal(sesi: Session, job: Job, error_class: str, pesan: str) -> None:
    job.error_class = error_class
    job.error = pesan[:2000]
    _lepas_kunci(job)
    if akan_diulang(job, error_class):
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
