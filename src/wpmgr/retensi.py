"""Pemangkasan data monitoring (spec §5.1).

Yang dipertahankan permanen: insiden uptime (`UptimeInsiden` -- laporan bulanan
menghitung persen uptime dari insiden, bukan dari cek mentah, koreksi #6),
traffic (`TrafficHarian`/`TrafficRincian`), job update/koneksi, dan
`activity_log` -- semuanya dibutuhkan laporan bulanan atau jejak audit dan
tidak pernah tumbuh secepat data mentah yang dipangkas di sini.

Kejadian admin baru/dinaikkan (`KejadianLogin.jenis` `admin_baru`/`jadi_admin`)
juga sengaja TIDAK PERNAH dipangkas, walau lebih tua dari retensi (deviasi
sadar dari draf awal fungsi ini, yang memangkas seluruh `login_events`
berdasarkan `waktu` tanpa mengecualikan jenis ini). Status keamanan
"perlu_diperiksa" (`wpmgr.keamanan._alasan_kritis`) membandingkan kejadian ini
ke `site.keamanan_diperiksa_pada` TANPA batas waktu ke belakang (Task 17,
sengaja dibuat tidak mengempis sendiri lewat waktu) -- kalau baris itu boleh
kena pangkas, admin baru yang belum pernah diperiksa bisa hilang begitu saja
dan status merahnya padam bukan karena diperiksa, tapi karena datanya lenyap.
Baris jenis ini jarang muncul, jadi membiarkannya menumpuk tidak menambah
beban berarti pada tabel `login_events`.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from wpmgr.models import (
    CatatanError,
    Job,
    JobStatus,
    JobType,
    KejadianLogin,
    LoginGagal,
    UptimeCheck,
    UptimePutaran,
)

RETENSI_MENTAH = timedelta(days=90)
RETENSI_JOB = timedelta(days=14)
JOB_DIPANGKAS = (JobType.collect_events, JobType.collect_traffic, JobType.scan_site, JobType.verify_site)

# Baris dihapus per putaran DELETE, diulang sampai habis, bukan satu DELETE
# untuk seluruh tabel sekaligus: site yang sedang digempur brute force bisa
# punya jutaan baris login_gagal, dan satu DELETE raksasa memegang lock baris
# itu selama menit-menit penuh serta membengkakkan WAL sekaligus. Angka ini
# cukup besar untuk tetap efisien, cukup kecil supaya tiap putaran selesai
# dalam hitungan puluhan-ratusan milidetik.
UKURAN_BATCH = 5_000

# Lihat penjelasan panjang di docstring modul ini.
JENIS_ADMIN = ("admin_baru", "jadi_admin")


def _hapus_bertahap(sesi: Session, model, *kondisi) -> int:
    """``DELETE ... WHERE id IN (SELECT id ... LIMIT N)``, diulang sampai 0 baris.

    Setiap putaran di-commit sendiri supaya lock dan transaksinya tidak
    menumpuk untuk keseluruhan tabel; lihat catatan `UKURAN_BATCH`.
    """
    total = 0
    while True:
        subq = select(model.id).where(*kondisi).limit(UKURAN_BATCH)
        n = sesi.execute(delete(model).where(model.id.in_(subq))).rowcount
        sesi.commit()
        total += n
        if n < UKURAN_BATCH:
            return total


def pangkas(sesi: Session, sekarang: datetime) -> dict[str, int]:
    batas = sekarang - RETENSI_MENTAH
    return {
        # Cek dulu, baru putaran: uptime.jalankan_putaran menulis keduanya
        # dengan `sekarang` yang sama persis, jadi setiap putaran yang masih
        # dipangkas di sini pasti seluruh cek-nya sudah lebih dulu terhapus di
        # atas (mulai < batas => dicek_pada < batas). ondelete=CASCADE pada FK
        # putaran_id tetap jadi jaring pengaman terakhir kalau invarian itu
        # ternyata tidak selalu berlaku -- bukan jalur utama penghapusan.
        "uptime_checks": _hapus_bertahap(sesi, UptimeCheck, UptimeCheck.dicek_pada < batas),
        "uptime_putaran": _hapus_bertahap(sesi, UptimePutaran, UptimePutaran.mulai < batas),
        "login_events": _hapus_bertahap(
            sesi, KejadianLogin,
            KejadianLogin.waktu < batas, KejadianLogin.jenis.not_in(JENIS_ADMIN),
        ),
        "login_gagal": _hapus_bertahap(sesi, LoginGagal, LoginGagal.jam < batas),
        "site_errors": _hapus_bertahap(sesi, CatatanError, CatatanError.terakhir_terlihat < batas),
        # ±4.800 job pengambilan per hari untuk 40 site; tanpa ini tabel jobs
        # tumbuh ±1,7 juta baris per tahun. Hanya job monitoring rutin
        # (JOB_DIPANGKAS) yang selesai (success/failed) dan sudah lewat
        # RETENSI_JOB -- job update/koneksi dan job yang masih tertunda/berjalan
        # tidak pernah tersentuh di sini.
        "jobs": _hapus_bertahap(
            sesi, Job,
            Job.tipe.in_(JOB_DIPANGKAS),
            Job.status.in_((JobStatus.success, JobStatus.failed)),
            Job.finished_at < sekarang - RETENSI_JOB,
        ),
    }
