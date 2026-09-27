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
from wpmgr.models import (
    CatatanError,
    Site,
    SiteStatus,
    Staging,
    StatusStaging,
    TrafficHarian,
    UptimeStatus,
)
from wpmgr.ssl_cek import sisa_hari_ssl, ssl_bermasalah
from wpmgr.staging.umum import ASAL_PRODUKSI
from wpmgr.traffic import anomali_site
from wpmgr.uptime import persen_uptime_per_site
from wpmgr.versi import lebih_lama

URUTAN_CHIP = [
    "mati", "perlu_diperiksa", "dorong_gagal", "diserang", "error_baru", "ssl", "koneksi",
    "penangkap_terbatas", "staging_gagal", "traffic_anjlok", "traffic_melonjak", "connector_usang",
]
TINGKAT_MASALAH = {
    "mati": 1, "perlu_diperiksa": 1, "dorong_gagal": 1,
    "diserang": 2, "error_baru": 2, "ssl": 2, "koneksi": 2, "penangkap_terbatas": 2, "staging_gagal": 2,
    "traffic_anjlok": 3, "traffic_melonjak": 3, "connector_usang": 3,
}
TAB_MASALAH = {
    "mati": "uptime", "perlu_diperiksa": "login", "dorong_gagal": "staging", "diserang": "login",
    "error_baru": "error", "ssl": "uptime", "koneksi": "ringkasan", "penangkap_terbatas": "ringkasan",
    "staging_gagal": "staging", "traffic_anjlok": "traffic", "traffic_melonjak": "traffic",
    "connector_usang": "ringkasan",
}
TINGKAT_SEHAT = 4


def _iso(nilai):
    return nilai.isoformat() if nilai else None


def _error_per_site(sesi: Session, sekarang: datetime, site_ids: list) -> dict:
    hasil: dict = {}
    if not site_ids:
        return hasil
    kandidat = sesi.scalars(
        select(CatatanError).where(
            CatatanError.site_id.in_(site_ids),
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


def _traffic_kemarin(sesi: Session, kemarin, site_ids: list) -> dict:
    hasil: dict = {}
    if not site_ids:
        return hasil
    # ga4 dibaca lebih dulu lalu ditimpa plugin: plugin tersedia di semua site
    # dan menjadi angka utama bila keduanya ada.
    for sumber in ("ga4", "plugin"):
        for site_id, n in sesi.execute(
            select(TrafficHarian.site_id, TrafficHarian.kunjungan).where(
                TrafficHarian.site_id.in_(site_ids),
                TrafficHarian.tanggal == kemarin, TrafficHarian.sumber == sumber,
            )
        ).all():
            hasil[site_id] = n
    return hasil


def masalah_staging(st: Staging | None) -> list[str]:
    """Chip staging satu site, tanpa menghitung satu kegagalan dua kali.

    - `dorong_gagal`: produksi bermasalah sesudah dorong/kembalikan, yaitu
      `dorong_gagal_pada` terisi atau staging `gagal` berasal 'produksi'
      (salinannya utuh; yang rusak produksi).
    - `staging_gagal`: salinan staging sendiri gagal, yaitu `gagal` berasal
      'salinan' atau tanpa penanda asal (baris sebelum R20).
    Keduanya menyala bersamaan hanya bila memang dua masalah (R22: salinan
    sudah belum utuh sebelum dorongan yang gagal di produksi).
    """
    if st is None:
        return []
    gagal = st.status == StatusStaging.gagal
    masalah = []
    if st.dorong_gagal_pada is not None or (gagal and st.gagal_asal == ASAL_PRODUKSI):
        masalah.append("dorong_gagal")
    if gagal and st.gagal_asal != ASAL_PRODUKSI:
        masalah.append("staging_gagal")
    return masalah


def susun_kesehatan(sesi: Session, sekarang: datetime | None = None) -> dict:
    sekarang = sekarang or datetime.now(timezone.utc)
    hari_ini = sekarang.date()
    versi_terbaru = (baca_manifest(get_settings().jalur_connector) or {}).get("versi")
    sites = sesi.scalars(
        select(Site).where(Site.status != SiteStatus.disabled).order_by(Site.nama)
    ).all()
    site_ids = [s.id for s in sites]
    persen = persen_uptime_per_site(sesi, sekarang - timedelta(hours=24), site_ids)
    errors = _error_per_site(sesi, sekarang, site_ids)
    traffic = _traffic_kemarin(sesi, hari_ini - timedelta(days=1), site_ids)
    # Fitur staging mati: tab Staging tidak ada, jadi chip-nya tidak punya tujuan.
    stagings = {}
    if site_ids and get_settings().staging_aktif:
        stagings = {st.site_id: st for st in sesi.scalars(select(Staging).where(Staging.site_id.in_(site_ids)))}

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
        st = stagings.get(site.id)
        masalah.extend(masalah_staging(st))

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
            "staging_status": st.status.value if st is not None else None,
        })

    semua.sort(key=lambda b: (b["tingkat"], b["nama"].lower()))
    chip = {k: sum(1 for b in semua if k in b["masalah"]) for k in URUTAN_CHIP}
    return {"baris": semua, "chip": chip, "dibuat_pada": sekarang.isoformat()}
