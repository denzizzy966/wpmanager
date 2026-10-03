import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import String, bindparam, select, text
from sqlalchemy import func as safunc
from sqlalchemy.orm import Session

from wpmgr.errors import BAD_RESPONSE, DAPAT_DIULANG, TRANSIENT, UNKNOWN
from wpmgr.models import Job, JobStatus, JobType

log = logging.getLogger("wpmgr.jobs.queue")

# Percobaan pertama ditambah satu ulangan.
BATAS_PERCOBAAN_BAD_RESPONSE = 2

# Job runtime (staging Lapis 3 + hosting Lapis 4) diproses worker staging dan
# diserialkan satu sama lain per site. Yang hanya membaca produksi/hosting
# lama (_RUNTIME_BACA) boleh berjalan bersama job non-runtime site yang sama.
_RUNTIME = ("('staging_tarik', 'staging_uji_update', 'staging_dorong', 'staging_kembalikan', "
            "'pindah_tarik', 'pindah_aktifkan', 'backup_hosting')")
_RUNTIME_BACA = "('staging_tarik', 'staging_uji_update', 'pindah_tarik')"
# Pasangan (j, j2) yang TIDAK boleh berjalan bersamaan di satu site. Dua job
# runtime selalu bentrok: indeks unik staging dan hosting terpisah
# (uq_jobs_staging_aktif, uq_jobs_hosting_aktif), jadi job staging dan job
# hosting site yang sama bisa sama-sama tertunda, dan hanya aturan ini (lewat
# SQL_AMBIL dan SQL_BENTROK) yang mencegah keduanya berjalan bersamaan.
_BENTROK = (
    f"NOT (j.tipe IN {_RUNTIME_BACA} AND j2.tipe NOT IN {_RUNTIME})"
    f" AND NOT (j2.tipe IN {_RUNTIME_BACA} AND j.tipe NOT IN {_RUNTIME})"
)


# Job yang menahan job lain di site yang sama: yang sedang berjalan, dan (I1)
# job TERTUNDA yang sudah menyentuh produksi -- dorong/kembalikan dengan
# `unggah_mulai` atau `langkah_terapkan` di kemajuan, atau pindah_aktifkan
# yang sudah memulai tukar (`langkah_aktifkan` tukar/verifikasi/beres).
# Produksi bisa setengah diterapkan/beralih, jadi tidak ada job lain di site
# itu yang boleh berjalan di atasnya: job Lapis 1 (update, scan, ...), dan
# juga job runtime keluarga LAIN (putusan L11) -- dorong/kembalikan tertunda
# sesudah tukar menahan pindah_*/backup_hosting, dan pindah_aktifkan
# tertunda sesudah tukar menahan job staging. Indeks unik staging dan
# hosting terpisah, jadi hanya aturan ini yang menahannya. Di keluarga yang
# sama tidak ada job lain yang bisa tertunda bersamanya (indeks unik per
# keluarga), dan job itu sendiri dikecualikan lewat `j2.id <> j.id`.
def _menahan() -> str:
    return (
        "(j2.status = 'running'"
        " OR (j2.status = 'pending'"
        " AND ((j2.tipe IN ('staging_dorong', 'staging_kembalikan')"
        " AND (j2.payload #> '{kemajuan,langkah_terapkan}' IS NOT NULL"
        " OR (j2.payload #>> '{kemajuan,unggah_mulai}') = 'true'))"
        " OR (j2.tipe = 'pindah_aktifkan'"
        " AND (j2.payload #>> '{kemajuan,langkah_aktifkan}') IN ('tukar', 'verifikasi', 'beres')))))"
    )


# Satu job berjalan per site, dengan dua pengecualian (Koreksi #1): tarik
# dan uji staging, juga pindah_tarik, hanya membaca produksi/hosting lama,
# jadi tidak menahan dan tidak ditahan job non-runtime. Job runtime lain
# eksklusif terhadap semuanya. `:jenis` memisahkan worker staging (job
# runtime berjam-jam: staging dan hosting) dari worker umum.
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
                   OR (CAST(:jenis AS text) = 'staging') = (j.tipe IN {_RUNTIME}))
              AND NOT EXISTS (
                    SELECT 1 FROM jobs j2
                     WHERE j2.site_id = j.site_id
                       AND j2.id <> j.id
                       AND {_menahan()}
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
       AND {_menahan()}
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
    job = sesi.get(Job, job_id, populate_existing=True)
    # Atribut biasa (bukan kolom): tidak ikut kedaluwarsa saat rollback, jadi
    # tetap menunjuk pemegang klaim sesudah reaper merebut job ini.
    job._pemegang_klaim = worker
    return job


def pemegang_klaim(job: Job) -> str | None:
    """Worker yang mengklaim job ini lewat `ambil_job`, untuk memagari tulisan dan detak.

    `job.locked_by` tidak cukup: rollback mengedaluwarsakan objek, dan sesudah
    reaper merebut job itu nilai yang dimuat ulang adalah NULL -- worker zombi
    akan menulis tanpa pagar. Job yang tidak diklaim lewat `ambil_job` (test
    yang mengisi `locked_by` sendiri) memakai `locked_by`.
    """
    return getattr(job, "_pemegang_klaim", None) or job.locked_by


def jeda_menit(attempts: int) -> int:
    return 2**attempts


# ---- pemulihan produksi sesudah tukar (putusan R26) ----------------------------

# Langkah terapkan sejak tukar dikirim ke produksi (tulis-lebih-dulu di dorong).
LANGKAH_SESUDAH_TUKAR = frozenset({"tukar", "pulihkan", "dipulihkan", "selesai", "beres"})
# Lapis 4: langkah pindah_aktifkan sejak prod-aktifkan dikirim (spec §10.4).
LANGKAH_AKTIFKAN_SESUDAH_TUKAR = frozenset({"tukar", "verifikasi", "beres"})
_JOB_PRODUKSI = frozenset({JobType.staging_dorong, JobType.staging_kembalikan})
# Produksi yang setengah ditukar tidak boleh dibiarkan bergantung pada WP-Cron:
# job dicoba lagi terus (jeda dibatasi) sampai batas ini, tidak berhenti di max_attempts.
JEDA_PEMULIHAN_MAKS_MENIT = 15
BATAS_PEMULIHAN = timedelta(hours=24)


def menyentuh_produksi(job: Job) -> bool:
    """Job yang sudah mengirim perubahan ke produksi dan tidak terbukti dipulihkan (R26).

    Dorong/kembalikan sesudah tukar, atau pindah_aktifkan sesudah
    `prod-aktifkan` dikirim (situs mungkin sudah dilayani VPS setengah jalan).
    """
    k = (job.payload or {}).get("kemajuan") or {}
    if job.tipe == JobType.pindah_aktifkan:
        return k.get("langkah_aktifkan") in LANGKAH_AKTIFKAN_SESUDAH_TUKAR
    if job.tipe not in _JOB_PRODUKSI:
        return False
    return k.get("langkah_terapkan") in LANGKAH_SESUDAH_TUKAR and not k.get("pulih_terkonfirmasi")


def _waktu_kemajuan(job: Job, kunci: str) -> datetime | None:
    k = (job.payload or {}).get("kemajuan") or {}
    try:
        mulai = datetime.fromisoformat(k[kunci])
    except (KeyError, TypeError, ValueError):
        return None
    return mulai if mulai.tzinfo else mulai.replace(tzinfo=timezone.utc)


def _mulai_tukar(job: Job) -> datetime | None:
    # Job dari sebelum penanda ini ada: batas dihitung dari pembuatan job.
    return _waktu_kemajuan(job, "tukar_pada") or job.dibuat_pada


# ---- skrip pembantu sibuk/menolak tanpa perubahan (Lapis 4) ---------------------
#
# Skrip root keluar 3 (`GalatPembantu.tanpa_ubah`) bila kunci router/nginx
# sedang dipegang -- kunci router dipegang selama `prod-db-impor` situs lain,
# sampai 3 jam -- atau bila prasyaratnya menolak sebelum mengubah apa pun.
# Job hosting yang ditolak begini dijadwalkan ulang dengan jeda yang bertambah
# (dibatasi seperti pemulihan) selama BATAS_SIBUK sejak penolakan pertama
# dalam satu rentetan. Pembungkus `hosting.umum.jalankan_hosting` mencatat
# rentetan itu di kemajuan (`sibuk_sejak`, `sibuk_kali`) dan mengembalikan
# jatah percobaan, jadi `akan_diulang` tetap memutuskan "diulang" lewat aturan
# biasanya; di sini hanya jendela dan jedanya.
BATAS_SIBUK = timedelta(hours=4)


def sibuk_kali(job: Job) -> int:
    """Panjang rentetan penolakan sibuk terakhir (0 = kegagalan terakhir bukan penolakan sibuk)."""
    k = (job.payload or {}).get("kemajuan") or {}
    try:
        return max(0, int(k.get("sibuk_kali") or 0))
    except (TypeError, ValueError):
        return 0


def dalam_batas_sibuk(job: Job, sekarang: datetime | None = None) -> bool:
    """Kegagalan terakhir job ini penolakan sibuk, dan jendela rentetannya belum lewat."""
    if not sibuk_kali(job):
        return False
    mulai = _waktu_kemajuan(job, "sibuk_sejak")
    if mulai is None:
        return False
    return (sekarang or datetime.now(timezone.utc)) - mulai < BATAS_SIBUK


def _jeda_dibatasi(kali: int) -> int:
    return min(jeda_menit(min(kali, 10)), JEDA_PEMULIHAN_MAKS_MENIT)


def dalam_batas_pemulihan(job: Job, sekarang: datetime | None = None) -> bool:
    """Produksi tersentuh dan batas 24 jam sejak tukar dikirim belum lewat (R26).

    Selama benar, percobaan ulang (worker, pembungkus staging, reaper) tidak
    berhenti di `max_attempts`. Sesudahnya kegagalan menjadi final dengan
    `dorong_gagal_pada` dan pesan tetap "dipulihkan otomatis oleh connector".
    """
    if not menyentuh_produksi(job):
        return False
    mulai = _mulai_tukar(job)
    if mulai is None:
        return True
    return (sekarang or datetime.now(timezone.utc)) - mulai < BATAS_PEMULIHAN


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
    if error_class in (TRANSIENT, UNKNOWN) and dalam_batas_pemulihan(job):
        return True
    return error_class in DAPAT_DIULANG and job.attempts < _batas_percobaan(job, error_class)


def selesai_gagal(sesi: Session, job: Job, error_class: str, pesan: str) -> None:
    job.error_class = error_class
    job.error = pesan[:2000]
    _lepas_kunci(job)
    if akan_diulang(job, error_class):
        job.status = JobStatus.pending
        menit = jeda_menit(job.attempts)
        if dalam_batas_pemulihan(job):
            # Backoff eksponensial yang tak dibatasi melewati batas 24 jam dan
            # meluap di timedelta; pemulihan produksi dicoba tiap <= 15 menit.
            menit = _jeda_dibatasi(job.attempts)
        elif dalam_batas_sibuk(job):
            # Jatah percobaan dikembalikan pada penolakan sibuk, jadi jedanya
            # bertambah menurut panjang rentetan, bukan menurut `attempts`.
            menit = _jeda_dibatasi(sibuk_kali(job))
        job.scheduled_for = safunc.now() + timedelta(minutes=menit)
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
