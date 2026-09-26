import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from wpmgr import db
from wpmgr.keamanan import nilai_keamanan, status_error
from wpmgr.kesehatan import susun_kesehatan
from wpmgr.models import (
    ActivityLog,
    CatatanError,
    KejadianLogin,
    LoginGagal,
    PackageType,
    Site,
    SitePackage,
    UptimeInsiden,
    User,
)
from wpmgr.ssl_cek import sisa_hari_ssl
from wpmgr.traffic import POLA_PROPERTY
from wpmgr.uagent import urai_ua
from wpmgr.uptime import persen_uptime, rata_waktu_ms, teks_durasi, uptime_harian
from wpmgr.web.auth import pengguna_api

router = APIRouter()
PenggunaApi = Annotated[User, Depends(pengguna_api)]

TEKS_STATUS_ERROR = {"selesai": "Selesai", "baru": "Baru", "masih_terjadi": "Masih terjadi",
                     "berhenti": "Berhenti"}
TEKS_STATUS_UPTIME = {"naik": "Naik", "mati": "Mati", "terblokir": "Terblokir",
                      "belum_dicek": "Belum dicek"}
TEKS_KOMPONEN = {"plugin": "Plugin", "mu-plugin": "Must-use plugin", "theme": "Tema",
                 "core": "WordPress core", "lainnya": "Lainnya"}
# Batas baris LoginGagal mentah yang diambil dari DB sebelum diringkas per IP,
# diurutkan dari yang paling deras dulu -- site yang sedang digempur brute
# force dengan banyak username/jam berbeda tidak boleh membuat query ini
# menarik puluhan ribu baris ke memori proses dashboard.
BATAS_BARIS_LOGIN_GAGAL = 5000


@router.get("/api/kesehatan")
def kesehatan(pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        return susun_kesehatan(sesi)


def _sekarang() -> datetime:
    return datetime.now(timezone.utc)


def _iso(nilai):
    return nilai.isoformat() if nilai else None


def _site(sesi, site_id: uuid.UUID) -> Site:
    site = sesi.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site tidak ditemukan")
    return site


@router.get("/api/sites/{site_id}/uptime")
def uptime_site(site_id: uuid.UUID, pengguna: PenggunaApi, hari: int = 30):
    hari = max(1, min(hari, 90))
    sekarang = _sekarang()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        insiden = sesi.scalars(
            select(UptimeInsiden).where(UptimeInsiden.site_id == site.id)
            .order_by(UptimeInsiden.mulai.desc()).limit(50)
        ).all()
        sisa = sisa_hari_ssl(site, sekarang)
        if site.ssl_error:
            ssl_teks = site.ssl_error
        elif sisa is None:
            ssl_teks = "Belum dicek"
        else:
            ssl_teks = f"Berlaku sampai {site.ssl_kedaluwarsa:%Y-%m-%d} ({sisa} hari lagi)"
        return {
            "status": site.uptime_status.value,
            "status_teks": TEKS_STATUS_UPTIME[site.uptime_status.value],
            "sejak": _iso(site.uptime_sejak),
            "persen": {
                "24j": persen_uptime(sesi, site.id, sekarang - timedelta(days=1)),
                "7h": persen_uptime(sesi, site.id, sekarang - timedelta(days=7)),
                "30h": persen_uptime(sesi, site.id, sekarang - timedelta(days=30)),
            },
            "rata_ms": rata_waktu_ms(sesi, site.id, sekarang - timedelta(days=1)),
            "harian": uptime_harian(sesi, site.id, sekarang, hari),
            "insiden": [
                {"id": i.id, "mulai": _iso(i.mulai), "selesai": _iso(i.selesai),
                 "durasi": teks_durasi(((i.selesai or sekarang) - i.mulai).total_seconds()),
                 "penyebab": i.penyebab}
                for i in insiden
            ],
            "ssl": {"sisa_hari": sisa, "error": site.ssl_error, "teks": ssl_teks},
        }


def _nama_komponen(paket: list[SitePackage], tipe: str, slug: str | None) -> str | None:
    if not slug:
        return None
    for p in paket:
        if tipe == "plugin" and p.tipe == PackageType.plugin and (
            p.slug == slug or p.slug.startswith(slug + "/")
        ):
            return p.nama
        if tipe == "theme" and p.tipe == PackageType.theme and p.slug == slug:
            return p.nama
    return None


@router.get("/api/sites/{site_id}/errors")
def errors_site(site_id: uuid.UUID, pengguna: PenggunaApi):
    sekarang = _sekarang()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        paket = sesi.scalars(select(SitePackage).where(SitePackage.site_id == site.id)).all()
        daftar = sesi.scalars(
            select(CatatanError).where(CatatanError.site_id == site.id)
            .order_by(CatatanError.terakhir_terlihat.desc()).limit(500)
        ).all()
        hasil = []
        for e in daftar:
            status = status_error(e, sekarang).value
            nama = _nama_komponen(paket, e.komponen_tipe, e.komponen_slug) or e.komponen_slug
            komponen = TEKS_KOMPONEN.get(e.komponen_tipe, e.komponen_tipe)
            setelah = e.setelah_update or None
            hasil.append({
                "id": e.id,
                "tingkat": e.tingkat,
                "status": status,
                "status_teks": TEKS_STATUS_ERROR[status],
                "komponen_teks": f"{komponen} {nama}" if nama else komponen,
                "pesan": e.pesan,
                "lokasi": f"{e.file}:{e.baris}" if e.file else "—",
                "jumlah": e.jumlah,
                "pertama_terlihat": _iso(e.pertama_terlihat),
                "terakhir_terlihat": _iso(e.terakhir_terlihat),
                "konteks": e.konteks,
                "setelah_update": setelah,
                "setelah_update_teks": (
                    f"muncul setelah update ke v{setelah.get('versi_sesudah')}" if setelah else None
                ),
            })
        return hasil


@router.post("/api/sites/{site_id}/errors/{error_id}/selesai")
def tandai_error_selesai(site_id: uuid.UUID, error_id: int, pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        e = sesi.get(CatatanError, error_id)
        if e is None or e.site_id != site_id:
            raise HTTPException(status_code=404, detail="Error tidak ditemukan")
        e.ditandai_selesai_pada = _sekarang()
        sesi.add(ActivityLog(site_id=site_id, user_id=pengguna.id, level="info",
                             pesan=f"Error ditandai selesai oleh {pengguna.email}",
                             detail={"sidik_jari": e.sidik_jari, "pesan": e.pesan[:200]}))
        sesi.commit()
    return {"ok": True}


@router.get("/api/sites/{site_id}/logins")
def logins_site(site_id: uuid.UUID, pengguna: PenggunaApi, hari: int = 30):
    hari = max(1, min(hari, 90))
    sekarang = _sekarang()
    sejak = sekarang - timedelta(days=hari)
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        keamanan = nilai_keamanan(sesi, site, sekarang)
        kejadian = sesi.scalars(
            select(KejadianLogin).where(KejadianLogin.site_id == site.id, KejadianLogin.waktu >= sejak)
            .order_by(KejadianLogin.waktu.desc()).limit(500)
        ).all()

        def bentuk(k):
            ua = urai_ua(k.user_agent)
            return {"waktu": _iso(k.waktu), "jenis": k.jenis, "username": k.username, "role": k.role,
                    "ip": k.ip, "negara": k.negara, "jalur": k.jalur, "peramban": ua["peramban"],
                    "os": ua["os"], "skrip": ua["skrip"]}

        per_ip: dict = defaultdict(lambda: {"jumlah": 0, "negara": None, "username": defaultdict(int),
                                            "jalur": set(), "skrip": False})
        for g in sesi.scalars(
            select(LoginGagal).where(LoginGagal.site_id == site.id, LoginGagal.jam >= sejak)
            .order_by(LoginGagal.jumlah.desc()).limit(BATAS_BARIS_LOGIN_GAGAL)
        ).all():
            ringkas = per_ip[g.ip or "(IP lain)"]
            ringkas["jumlah"] += g.jumlah
            ringkas["negara"] = ringkas["negara"] or g.negara
            ringkas["username"][g.username] += g.jumlah
            ringkas["jalur"].add(g.jalur)
            ringkas["skrip"] = ringkas["skrip"] or urai_ua(g.user_agent)["skrip"]
        gagal = sorted(
            ({"ip": ip, "negara": r["negara"], "jumlah": r["jumlah"],
              "username": [u for u, _ in sorted(r["username"].items(), key=lambda x: -x[1])[:5]],
              "jalur": sorted(r["jalur"]), "skrip": r["skrip"]}
             for ip, r in per_ip.items()),
            key=lambda b: -b["jumlah"],
        )[:200]

        return {
            "status": keamanan.status.value,
            "alasan": keamanan.alasan,
            "percobaan_sejam": keamanan.percobaan_sejam,
            "diperiksa_pada": _iso(site.keamanan_diperiksa_pada),
            "berhasil": [bentuk(k) for k in kejadian if k.jenis == "berhasil"],
            "admin": [bentuk(k) for k in kejadian if k.jenis != "berhasil"],
            "gagal": gagal,
        }


@router.post("/api/sites/{site_id}/keamanan/diperiksa")
def keamanan_diperiksa(site_id: uuid.UUID, pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        site.keamanan_diperiksa_pada = _sekarang()
        sesi.add(ActivityLog(site_id=site.id, user_id=pengguna.id, level="info",
                             pesan=f"Keamanan ditandai sudah diperiksa oleh {pengguna.email}"))
        sesi.commit()
    return {"ok": True}


class PermintaanGA4(BaseModel):
    property_id: str = ""


@router.put("/api/sites/{site_id}/ga4")
def atur_ga4(site_id: uuid.UUID, req: PermintaanGA4, pengguna: PenggunaApi):
    nilai = req.property_id.strip()
    if nilai and not POLA_PROPERTY.match(nilai):
        raise HTTPException(status_code=422,
                            detail="Property ID GA4 berupa 6 sampai 12 digit angka, bukan Measurement ID (G-...).")
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        site.ga4_property_id = nilai or None
        site.ga4_error = None
        sesi.commit()
    return {"ok": True}
