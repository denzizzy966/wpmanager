import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from wpmgr import db
from wpmgr.crypto import dekripsi_secret
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    Client,
    Job,
    JobStatus,
    JobType,
    PackageType,
    Site,
    SitePackage,
    User,
)
from wpmgr.sso import buat_token
from wpmgr.web.auth import pengguna_api

router = APIRouter()

# Annotated[..., Depends(...)] alih-alih default `= Depends(...)`: bentuk
# terakhir memanggil Depends() setiap kali Python mengevaluasi signature
# fungsi, yang dianggap flake8-bugbear (B008) sebagai pemanggilan fungsi pada
# default argumen -- pola standar FastAPI ini menghindarinya tanpa menonaktifkan
# aturan tersebut.
PenggunaApi = Annotated[User, Depends(pengguna_api)]


class ItemUpdate(BaseModel):
    site_id: uuid.UUID
    tipe: PackageType
    slug: str
    ke_versi: str


class PermintaanUpdate(BaseModel):
    items: list[ItemUpdate]


class PermintaanScan(BaseModel):
    site_id: uuid.UUID


def _waktu(nilai):
    return nilai.isoformat() if nilai else None


@router.get("/api/packages")
def daftar_paket(pengguna: PenggunaApi, semua: int = 0):
    with db.SessionLocal() as sesi:
        q = (
            select(SitePackage, Site.nama, Client.nama)
            .join(Site, Site.id == SitePackage.site_id)
            .outerjoin(Client, Client.id == Site.client_id)
        )
        if not semua:
            q = q.where(SitePackage.versi_tersedia.is_not(None))

        return [
            {
                "id": p.id,
                "site_id": str(p.site_id),
                "site_nama": site_nama,
                "client_nama": client_nama or "",
                "tipe": p.tipe.value,
                "slug": p.slug,
                "nama": p.nama,
                "versi_terpasang": p.versi_terpasang,
                "versi_tersedia": p.versi_tersedia,
                "aktif": p.aktif,
                "last_scan_at": _waktu(p.last_scan_at),
            }
            for p, site_nama, client_nama in sesi.execute(q).all()
        ]


@router.get("/api/sites")
def daftar_site(pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        jumlah = (
            select(SitePackage.site_id, func.count().label("n"))
            .where(SitePackage.versi_tersedia.is_not(None))
            .group_by(SitePackage.site_id)
            .subquery()
        )
        q = (
            select(Site, Client.nama, func.coalesce(jumlah.c.n, 0))
            .outerjoin(Client, Client.id == Site.client_id)
            .outerjoin(jumlah, jumlah.c.site_id == Site.id)
            .order_by(Site.nama)
        )
        return [
            {
                "id": str(s.id),
                "nama": s.nama,
                "url": s.url,
                "client_nama": client_nama or "",
                "status": s.status.value,
                "wp_version": s.wp_version,
                "php_version": s.php_version,
                "jumlah_update": int(n),
                "last_seen_at": _waktu(s.last_seen_at),
                "last_scan_at": _waktu(s.last_scan_at),
                "last_error": s.last_error,
            }
            for s, client_nama, n in sesi.execute(q).all()
        ]


@router.post("/api/jobs/update")
def buat_job_update(req: PermintaanUpdate, pengguna: PenggunaApi):
    ids: list[int] = []
    with db.SessionLocal() as sesi:
        # Lewatan pertama: seluruh item divalidasi sebelum satu job pun dibuat.
        # buat_job() melakukan commit sendiri untuk setiap job, sehingga
        # memvalidasi sambil membuat berarti item yang sah sudah tersimpan
        # permanen pada saat item berikutnya ditolak. Pemanggil menerima 404
        # dan menyimpulkan tidak terjadi apa-apa, sementara worker menjalankan
        # update sungguhan terhadap site client yang hidup.
        for item in req.items:
            if sesi.get(Site, item.site_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"Site {item.site_id} tidak ditemukan"
                )

        # Lewatan kedua: seluruh batch sudah terbukti sah.
        for item in req.items:
            terpasang = sesi.scalar(
                select(SitePackage.versi_terpasang).where(
                    SitePackage.site_id == item.site_id,
                    SitePackage.tipe == item.tipe,
                    SitePackage.slug == item.slug,
                )
            )
            job = buat_job(
                sesi,
                item.site_id,
                JobType.update_package,
                {
                    "tipe": item.tipe.value,
                    "slug": item.slug,
                    "dari_versi": terpasang,
                    "ke_versi": item.ke_versi,
                },
                dibuat_oleh=pengguna.id,
            )
            ids.append(job.id)
    return {"job_ids": ids}


@router.post("/api/jobs/scan")
def buat_job_scan(req: PermintaanScan, pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        if sesi.get(Site, req.site_id) is None:
            raise HTTPException(status_code=404, detail="Site tidak ditemukan")
        job = buat_job(sesi, req.site_id, JobType.scan_site, dibuat_oleh=pengguna.id)
    return {"job_id": job.id}


@router.get("/api/jobs/active")
def job_aktif(pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        q = (
            select(Job, Site.nama)
            .join(Site, Site.id == Job.site_id)
            .where(Job.status.in_([JobStatus.pending, JobStatus.running]))
            .order_by(Job.id)
        )
        return [
            {
                "id": j.id,
                "site_id": str(j.site_id),
                "site_nama": site_nama,
                "tipe": j.tipe.value,
                "status": j.status.value,
                "slug": j.payload.get("slug"),
                "attempts": j.attempts,
                "error": j.error,
            }
            for j, site_nama in sesi.execute(q).all()
        ]


@router.get("/api/sso/{site_id}")
def url_sso(site_id: uuid.UUID, pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        site = sesi.get(Site, site_id)
        if site is None:
            raise HTTPException(status_code=404, detail="Site tidak ditemukan")

        token = buat_token(dekripsi_secret(site.secret_terenkripsi), str(site.id))
        sesi.add(
            ActivityLog(site_id=site.id, user_id=pengguna.id, level="info",
                        pesan=f"SSO dibuka oleh {pengguna.email}")
        )
        sesi.commit()
        return {"url": f"{site.url}/?wpmgr_sso={token}"}


@router.delete("/api/sites/{site_id}")
def hapus_site(site_id: uuid.UUID, pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        site = sesi.get(Site, site_id)
        if site is None:
            raise HTTPException(status_code=404, detail="Site tidak ditemukan")
        nama = site.nama
        sesi.delete(site)
        # ActivityLog untuk penghapusan sengaja dibuat tanpa site_id: baris
        # site-nya sudah tidak ada, dan ON DELETE CASCADE akan ikut menghapus
        # catatan itu justru pada saat ia paling dibutuhkan. Jejak pencabutan
        # harus hidup lebih lama daripada site yang dicabut.
        sesi.add(
            ActivityLog(level="warning",
                        user_id=pengguna.id,
                        pesan=f"Site '{nama}' dicabut oleh {pengguna.email}")
        )
        sesi.commit()
    return {"ok": True}
