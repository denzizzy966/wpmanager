import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select

from wpmgr import db
from wpmgr.models import ActivityLog, Site, SitePackage, User
from wpmgr.pairing import buat_site
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


@router.get("/")
def halaman_updates(request: Request, pengguna: PenggunaHalaman):
    return _tpl().TemplateResponse(request, "updates.html", {"pengguna": pengguna})


@router.get("/sites")
def halaman_sites(request: Request, pengguna: PenggunaHalaman):
    return _tpl().TemplateResponse(request, "sites.html", {"pengguna": pengguna})


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


@router.get("/sites/new")
def form_site_baru(request: Request, pengguna: PenggunaHalaman):
    return _tpl().TemplateResponse(
        request, "site_new.html", {"pengguna": pengguna, "kunci": None, "galat": None}
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
            konteks = {"pengguna": pengguna, "kunci": kunci, "galat": None, "site": site}
    except ValueError as exc:
        konteks = {"pengguna": pengguna, "kunci": None, "galat": str(exc)}
    return _tpl().TemplateResponse(request, "site_new.html", konteks)


@router.get("/sites/{site_id}")
def halaman_detail(request: Request, site_id: uuid.UUID, pengguna: PenggunaHalaman):
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
    return _tpl().TemplateResponse(
        request, "site_detail.html",
        {"pengguna": pengguna, "site": site, "paket": paket, "riwayat": riwayat},
    )
