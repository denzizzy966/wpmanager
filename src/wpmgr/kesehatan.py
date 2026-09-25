"""Satu baris per site dan hitungan chip untuk halaman Kesehatan (spec §13.2)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from wpmgr.config import get_settings
from wpmgr.connector_paket import baca_manifest
from wpmgr.keamanan import (
    JENDELA_ERROR_BARU,
    StatusKeamanan,
    error_menyalakan_chip,
    nilai_keamanan,
)
from wpmgr.models import CatatanError, Site, SiteStatus, TrafficHarian, UptimeStatus
from wpmgr.ssl_cek import sisa_hari_ssl, ssl_bermasalah
from wpmgr.traffic import anomali_site
from wpmgr.uptime import persen_uptime_per_site
from wpmgr.versi import lebih_lama

URUTAN_CHIP = [
    "mati", "perlu_diperiksa", "diserang", "error_baru", "ssl", "koneksi",
    "penangkap_terbatas", "traffic_anjlok", "traffic_melonjak", "connector_usang",
]
TINGKAT_MASALAH = {
    "mati": 1, "perlu_diperiksa": 1,
    "diserang": 2, "error_baru": 2, "ssl": 2, "koneksi": 2, "penangkap_terbatas": 2,
    "traffic_anjlok": 3, "traffic_melonjak": 3, "connector_usang": 3,
}
TAB_MASALAH = {
    "mati": "uptime", "perlu_diperiksa": "login", "diserang": "login", "error_baru": "error",
    "ssl": "uptime", "koneksi": "ringkasan", "penangkap_terbatas": "ringkasan",
    "traffic_anjlok": "traffic", "traffic_melonjak": "traffic", "connector_usang": "ringkasan",
}
TINGKAT_SEHAT = 4


def _iso(nilai):
    return nilai.isoformat() if nilai else None


def _error_per_site(sesi: Session, sekarang: datetime) -> dict:
    hasil: dict = {}
    kandidat = sesi.scalars(
        select(CatatanError).where(
            CatatanError.tingkat.in_(("fatal", "database")),
            CatatanError.terakhir_terlihat >= sekarang - JENDELA_ERROR_BARU,
        )
    ).all()
    for e in kandidat:
        if not error_menyalakan_chip(e, sekarang):
            continue
        info = hasil.setdefault(e.site_id, {"baru": 0, "setelah_update": False})
        info["baru"] += 1
        info["setelah_update"] = info["setelah_update"] or e.setelah_update is not None
    return hasil


def _traffic_kemarin(sesi: Session, kemarin) -> dict:
    hasil: dict = {}
    # ga4 dibaca lebih dulu lalu ditimpa plugin: plugin tersedia di semua site
    # dan menjadi angka utama bila keduanya ada.
    for sumber in ("ga4", "plugin"):
        for site_id, n in sesi.execute(
            select(TrafficHarian.site_id, TrafficHarian.kunjungan).where(
                TrafficHarian.tanggal == kemarin, TrafficHarian.sumber == sumber
            )
        ).all():
            hasil[site_id] = n
    return hasil


def susun_kesehatan(sesi: Session, sekarang: datetime | None = None) -> dict:
    sekarang = sekarang or datetime.now(timezone.utc)
    hari_ini = sekarang.date()
    versi_terbaru = (baca_manifest(get_settings().jalur_connector) or {}).get("versi")
    sites = sesi.scalars(
        select(Site).where(Site.status != SiteStatus.disabled).order_by(Site.nama)
    ).all()
    persen = persen_uptime_per_site(sesi, sekarang - timedelta(hours=24))
    errors = _error_per_site(sesi, sekarang)
    traffic = _traffic_kemarin(sesi, hari_ini - timedelta(days=1))

    semua = []
    for site in sites:
        keamanan = nilai_keamanan(sesi, site, sekarang)
        anomali = anomali_site(sesi, site.id, hari_ini)
        info_error = errors.get(site.id, {"baru": 0, "setelah_update": False})

        masalah = []
        if site.uptime_status == UptimeStatus.mati:
            masalah.append("mati")
        if keamanan.status == StatusKeamanan.perlu_diperiksa:
            masalah.append("perlu_diperiksa")
        elif keamanan.status == StatusKeamanan.diserang:
            masalah.append("diserang")
        if info_error["baru"]:
            masalah.append("error_baru")
        if ssl_bermasalah(site, sekarang):
            masalah.append("ssl")
        if site.status != SiteStatus.active:
            masalah.append("koneksi")
        if site.mode_penangkap == "terbatas":
            masalah.append("penangkap_terbatas")
        if anomali == "anjlok":
            masalah.append("traffic_anjlok")
        elif anomali == "melonjak":
            masalah.append("traffic_melonjak")
        if lebih_lama(site.connector_version, versi_terbaru) or (site.connector_version and not site.fitur):
            masalah.append("connector_usang")

        utama = min(masalah, key=lambda m: (TINGKAT_MASALAH[m], URUTAN_CHIP.index(m)), default=None)
        semua.append({
            "id": str(site.id),
            "nama": site.nama,
            "url": site.url,
            "tingkat": TINGKAT_MASALAH[utama] if utama else TINGKAT_SEHAT,
            "masalah": masalah,
            "tab": TAB_MASALAH[utama] if utama else "ringkasan",
            "uptime_status": site.uptime_status.value,
            "uptime_persen_24j": persen.get(site.id),
            "uptime_sejak": _iso(site.uptime_sejak),
            "keamanan": keamanan.status.value,
            "percobaan_sejam": keamanan.percobaan_sejam,
            "error_baru": info_error["baru"],
            "error_setelah_update": info_error["setelah_update"],
            "traffic_kemarin": traffic.get(site.id),
            "anomali": anomali,
            "ssl_sisa_hari": sisa_hari_ssl(site, sekarang),
            "ssl_error": site.ssl_error,
            "koneksi": site.status.value,
            "connector_version": site.connector_version,
        })

    semua.sort(key=lambda b: (b["tingkat"], b["nama"].lower()))
    chip = {k: sum(1 for b in semua if k in b["masalah"]) for k in URUTAN_CHIP}
    return {"baris": semua, "chip": chip, "dibuat_pada": sekarang.isoformat()}
