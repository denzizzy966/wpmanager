import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.exc import IntegrityError

from wpmgr import db
from wpmgr.config import get_settings
from wpmgr.connector_paket import NAMA_ZIP, baca_manifest
from wpmgr.keamanan import JENDELA_ERROR_BARU, nilai_keamanan
from wpmgr.laporan import susun_laporan
from wpmgr.models import (
    ActivityLog,
    CatatanError,
    Site,
    SitePackage,
    UptimeStatus,
    User,
)
from wpmgr.pairing import buat_site
from wpmgr.traffic import anomali_site
from wpmgr.web.auth import pengguna_saat_ini

router = APIRouter()

# Annotated[..., Depends(...)] alih-alih default `= Depends(...)`, mengikuti
# pola yang sudah dipakai di routes_api.py: bentuk terakhir memanggil
# Depends() setiap kali Python mengevaluasi signature fungsi, yang dianggap
# flake8-bugbear (B008) sebagai pemanggilan fungsi pada default argumen.
PenggunaHalaman = Annotated[User, Depends(pengguna_saat_ini)]


def _tpl():
    from wpmgr.web.app import templates

    return templates


def _versi_connector() -> str | None:
    manifest = baca_manifest(get_settings().jalur_connector)
    return manifest["versi"] if manifest else None


@router.get("/")
def halaman_kesehatan(request: Request, pengguna: PenggunaHalaman):
    return _tpl().TemplateResponse(request, "kesehatan.html", {"pengguna": pengguna})


@router.get("/updates")
def halaman_updates(request: Request, pengguna: PenggunaHalaman):
    return _tpl().TemplateResponse(request, "updates.html", {"pengguna": pengguna})


@router.get("/sites")
def halaman_sites(request: Request, pengguna: PenggunaHalaman):
    return _tpl().TemplateResponse(request, "sites.html", {"pengguna": pengguna})


@router.get("/keamanan")
def halaman_keamanan(request: Request, pengguna: PenggunaHalaman):
    return _tpl().TemplateResponse(request, "keamanan.html", {"pengguna": pengguna})


@router.get("/activity")
def halaman_activity(request: Request, pengguna: PenggunaHalaman):
    with db.SessionLocal() as sesi:
        baris = sesi.execute(
            select(ActivityLog, Site.nama)
            .outerjoin(Site, Site.id == ActivityLog.site_id)
            .order_by(ActivityLog.dibuat_pada.desc())
            .limit(300)
        ).all()
    return _tpl().TemplateResponse(
        request, "activity.html", {"pengguna": pengguna, "baris": baris}
    )


@router.get("/connector/unduh")
def unduh_connector(pengguna: PenggunaHalaman):
    folder = get_settings().jalur_connector
    manifest = baca_manifest(folder)
    if manifest is None or not (folder / NAMA_ZIP).exists():
        raise HTTPException(
            status_code=404,
            detail="Paket connector belum dibangun. Jalankan: python -m wpmgr.cli build-connector",
        )
    return FileResponse(
        folder / NAMA_ZIP, media_type="application/zip",
        filename=f"wp-manager-connector-{manifest['versi']}.zip",
    )


@router.get("/sites/new")
def form_site_baru(request: Request, pengguna: PenggunaHalaman):
    return _tpl().TemplateResponse(
        request, "site_new.html",
        {"pengguna": pengguna, "kunci": None, "galat": None, "nama": "", "url": "", "versi_connector": _versi_connector()},
    )


@router.post("/sites")
def simpan_site(
    request: Request,
    pengguna: PenggunaHalaman,
    nama: str = Form(...),
    url: str = Form(...),
):
    try:
        with db.SessionLocal() as sesi:
            site, kunci = buat_site(sesi, nama, url, None, pengguna.id)
            konteks = {
                "pengguna": pengguna, "kunci": kunci, "galat": None,
                "site": site, "nama": nama, "url": url, "versi_connector": _versi_connector(),
            }
    except ValueError as exc:
        # URL tanpa https:// atau nilai tak sah lain — pesannya berasal dari
        # buat_site() sendiri. Input yang sudah diketik operator dikembalikan
        # ke form; menghapusnya justru menghukum kesalahan kecil di momen
        # orang paling malas mengetik ulang URL yang panjang.
        konteks = {
            "pengguna": pengguna, "kunci": None, "galat": str(exc),
            "nama": nama, "url": url, "versi_connector": _versi_connector(),
        }
    except IntegrityError:
        # Site.url unik: pengiriman kedua untuk URL yang sama meledak di
        # commit() sebagai IntegrityError, bukan ValueError, karena buat_site()
        # sendiri tidak memeriksa keunikan lebih dulu. Tanpa except ini,
        # operator yang tidak sengaja mengirim form dua kali (atau memang
        # sudah pernah menambah site itu) akan melihat 500 polos untuk
        # sesuatu yang sama sekali biasa.
        konteks = {
            "pengguna": pengguna, "kunci": None,
            "galat": "URL tersebut sudah terdaftar sebagai site lain.",
            "nama": nama, "url": url, "versi_connector": _versi_connector(),
        }
    return _tpl().TemplateResponse(request, "site_new.html", konteks)


TAB_DETAIL = [
    ("ringkasan", "Ringkasan"), ("paket", "Paket"), ("uptime", "Uptime"),
    ("error", "Error"), ("login", "Login"), ("traffic", "Traffic"), ("aktivitas", "Aktivitas"),
]
# [0-9] dan \Z, bukan \d dan $ (lihat POLA_PROPERTY di traffic.py): \d juga
# cocok dengan digit non-ASCII, dan $ cocok sebelum baris baru di akhir --
# keduanya bisa membuat pola ini lolos untuk input yang bukan "YYYY-MM".
POLA_BULAN = re.compile(r"^([0-9]{4})-(0[1-9]|1[0-2])\Z")


def _bulan_lalu(hari_ini: date) -> str:
    awal_bulan = hari_ini.replace(day=1)
    return (awal_bulan - timedelta(days=1)).strftime("%Y-%m")


@router.get("/sites/{site_id}")
def halaman_detail(request: Request, site_id: uuid.UUID, pengguna: PenggunaHalaman, tab: str = "ringkasan"):
    sah = {k for k, _ in TAB_DETAIL}
    # Hanya nilai dari daftar putih yang boleh masuk ke ekspresi Alpine di template.
    tab = tab if tab in sah else "ringkasan"
    sekarang = datetime.now(timezone.utc)
    with db.SessionLocal() as sesi:
        site = sesi.get(Site, site_id)
        if site is None:
            raise HTTPException(status_code=404, detail="Site tidak ditemukan")
        paket = sesi.scalars(
            select(SitePackage).where(SitePackage.site_id == site_id).order_by(SitePackage.nama)
        ).all()
        riwayat = sesi.scalars(
            select(ActivityLog)
            .where(ActivityLog.site_id == site_id)
            .order_by(ActivityLog.dibuat_pada.desc())
            .limit(100)
        ).all()
        # Setara SQL dari error_menyalakan_chip()/status_error() di keamanan.py
        # (status "baru" atau "masih_terjadi", bukan "selesai"/"berhenti"), tapi
        # dihitung dengan COUNT di database alih-alih menarik SETIAP CatatanError
        # site ini ke Python hanya untuk memberi badge di nav tab. Kalau logika
        # kedua fungsi itu berubah, cerminan di sini wajib ikut diperbarui.
        jumlah_error = sesi.scalar(
            select(func.count()).select_from(CatatanError).where(
                CatatanError.site_id == site_id,
                CatatanError.tingkat.in_(("fatal", "database")),
                not_(and_(
                    CatatanError.ditandai_selesai_pada.is_not(None),
                    CatatanError.terakhir_terlihat <= CatatanError.ditandai_selesai_pada,
                )),
                or_(
                    CatatanError.pertama_terlihat >= sekarang - JENDELA_ERROR_BARU,
                    CatatanError.terakhir_terlihat >= sekarang - JENDELA_ERROR_BARU,
                ),
            )
        )
        keamanan = nilai_keamanan(sesi, site, sekarang)
        anomali = anomali_site(sesi, site.id, sekarang.date())
    lencana = {
        "uptime": "!" if site.uptime_status == UptimeStatus.mati else "",
        "error": jumlah_error or "",
        "login": {"perlu_diperiksa": "!", "diserang": "serangan"}.get(keamanan.status.value, ""),
        "traffic": "!" if anomali else "",
    }
    return _tpl().TemplateResponse(
        request, "site_detail.html",
        {"pengguna": pengguna, "site": site, "paket": paket, "riwayat": riwayat,
         "tab": tab, "tab_detail": TAB_DETAIL, "lencana": lencana,
         "bulan_lalu": _bulan_lalu(sekarang.date()),
         "ga4_aktif": bool(get_settings().ga4_credentials)},
    )


@router.get("/sites/{site_id}/laporan/{bulan}")
def laporan_bulanan(request: Request, site_id: uuid.UUID, bulan: str, pengguna: PenggunaHalaman):
    cocok = POLA_BULAN.match(bulan)
    sekarang = datetime.now(timezone.utc)
    if cocok is None:
        raise HTTPException(status_code=404, detail="Bulan tidak dikenal")
    tahun, nomor = int(cocok.group(1)), int(cocok.group(2))
    try:
        # Regex membatasi ke 4 digit, tapi "0000-01" tetap lolos regex --
        # date() menolaknya sendiri (MINYEAR=1), jadi tetap harus ditangkap
        # supaya bulan tak masuk akal berujung 404, bukan 500.
        awal_bulan = date(tahun, nomor, 1)
    except ValueError:
        raise HTTPException(status_code=404, detail="Bulan tidak dikenal") from None
    if awal_bulan > sekarang.date():
        raise HTTPException(status_code=404, detail="Bulan ini belum dimulai")
    with db.SessionLocal() as sesi:
        site = sesi.get(Site, site_id)
        if site is None:
            raise HTTPException(status_code=404, detail="Site tidak ditemukan")
        data = susun_laporan(sesi, site, tahun, nomor, sekarang)
    return _tpl().TemplateResponse(request, "laporan.html", {"pengguna": pengguna, **data})
