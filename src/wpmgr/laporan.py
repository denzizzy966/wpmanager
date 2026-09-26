"""Data laporan bulanan untuk client: traffic, ketersediaan, dan update."""

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from wpmgr.models import ActivityLog, UptimeInsiden, UptimeStatus
from wpmgr.traffic import CATATAN_DUA_SUMBER, ringkasan_traffic
from wpmgr.uptime import teks_durasi

NAMA_BULAN = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
              "Agustus", "September", "Oktober", "November", "Desember"]


def susun_laporan(sesi: Session, site, tahun: int, bulan: int, sekarang: datetime) -> dict:
    awal = date(tahun, bulan, 1)
    akhir = date(tahun, bulan, monthrange(tahun, bulan)[1])
    awal_dt = datetime(tahun, bulan, 1, tzinfo=timezone.utc)
    akhir_dt = datetime.combine(akhir + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    batas = min(akhir_dt, sekarang)
    # Site belum ada sebelum site.dibuat_pada, jadi periode uptime tidak boleh
    # mulai lebih awal dari itu -- tanpa ini, bulan-bulan sebelum site
    # terdaftar dihitung seolah 100% naik (tidak ada insiden karena memang
    # belum dipantau, bukan karena benar-benar tidak pernah mati).
    mulai_pantau = max(awal_dt, site.dibuat_pada)

    insiden = sesi.scalars(
        select(UptimeInsiden).where(
            UptimeInsiden.site_id == site.id,
            UptimeInsiden.mulai < akhir_dt,
            or_(UptimeInsiden.selesai.is_(None), UptimeInsiden.selesai > awal_dt),
        ).order_by(UptimeInsiden.mulai)
    ).all()

    def _durasi_terpotong(i: UptimeInsiden) -> float:
        # Dipotong ke mulai_pantau..batas (koreksi #6), dipakai baik untuk
        # total maupun untuk durasi per baris di tabel -- keduanya harus
        # memakai potongan yang sama, supaya baris per insiden benar-benar
        # berjumlah sama dengan total "waktu tidak dapat diakses".
        return max(0.0, (min(i.selesai or sekarang, batas) - max(i.mulai, mulai_pantau)).total_seconds())

    # Dihitung dari insiden, bukan dari tabel cek: cek dipangkas setelah 90
    # hari, insiden disimpan permanen.
    detik_mati = sum(_durasi_terpotong(i) for i in insiden)
    detik_periode = (batas - mulai_pantau).total_seconds()
    dipantau = site.uptime_status != UptimeStatus.belum_dicek and detik_periode > 0
    # max(0.0, ...): penjaga terakhir kalau pembulatan/insiden yang tumpang
    # tindih membuat detik_mati sedikit melebihi detik_periode.
    persen = round(max(0.0, 100 * (1 - detik_mati / detik_periode)), 2) if dipantau else None

    update = []
    for log in sesi.scalars(
        select(ActivityLog).where(
            ActivityLog.site_id == site.id, ActivityLog.level == "info",
            ActivityLog.dibuat_pada >= awal_dt, ActivityLog.dibuat_pada < akhir_dt,
            ActivityLog.detail.has_key("versi_sesudah"),
        ).order_by(ActivityLog.dibuat_pada)
    ).all():
        d = log.detail or {}
        update.append({
            "tanggal": log.dibuat_pada,
            "komponen": d.get("slug") or "WP Manager Connector",
            "tipe": d.get("tipe") or "connector",
            "versi_sebelum": d.get("versi_sebelum") or "?",
            "versi_sesudah": d.get("versi_sesudah"),
        })

    return {
        "site": site,
        "judul_bulan": f"{NAMA_BULAN[bulan]} {tahun}",
        "awal": awal,
        "akhir": akhir,
        "traffic": {
            "plugin": ringkasan_traffic(sesi, site.id, awal, akhir, "plugin"),
            "ga4": ringkasan_traffic(sesi, site.id, awal, akhir, "ga4"),
        },
        "catatan": CATATAN_DUA_SUMBER,
        "uptime": {
            "persen": persen,
            "jumlah_insiden": len(insiden),
            "durasi_mati": teks_durasi(detik_mati),
            "insiden": [
                {"mulai": i.mulai, "selesai": i.selesai, "penyebab": i.penyebab,
                 "durasi": teks_durasi(_durasi_terpotong(i))}
                for i in insiden
            ],
        },
        "update": update,
        "dibuat_pada": sekarang,
    }
