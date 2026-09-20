from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from wpmgr.crypto import dekripsi_secret
from wpmgr.models import Job, PackageType, Site, SitePackage, SiteStatus
from wpmgr.site_client import SiteClient


def buat_klien(site: Site) -> SiteClient:
    return SiteClient(site.url, str(site.id), dekripsi_secret(site.secret_terenkripsi))


def _item_inventaris(data: dict) -> Iterator[tuple[PackageType, dict]]:
    inti = data.get("core")
    if inti:
        yield PackageType.core, {**inti, "slug": "core"}
    for p in data.get("plugins", []):
        yield PackageType.plugin, p
    for t in data.get("themes", []):
        yield PackageType.theme, t


def simpan_inventaris(sesi: Session, site: Site, data: dict) -> int:
    sekarang = datetime.now(timezone.utc)
    terlihat: set[tuple[PackageType, str]] = set()

    for tipe, item in _item_inventaris(data):
        kunci = (tipe, item["slug"])
        terlihat.add(kunci)
        baris = sesi.scalar(
            select(SitePackage).where(
                SitePackage.site_id == site.id,
                SitePackage.tipe == tipe,
                SitePackage.slug == item["slug"],
            )
        )
        if baris is None:
            baris = SitePackage(site_id=site.id, tipe=tipe, slug=item["slug"])
            sesi.add(baris)
        baris.nama = item["nama"]
        baris.versi_terpasang = item["versi_terpasang"]
        baris.versi_tersedia = item.get("versi_tersedia")
        baris.aktif = bool(item.get("aktif", True))
        baris.auto_update = bool(item.get("auto_update", False))
        baris.last_scan_at = sekarang

    sesi.flush()

    lama = sesi.scalars(select(SitePackage).where(SitePackage.site_id == site.id)).all()
    for baris in lama:
        if (baris.tipe, baris.slug) not in terlihat:
            sesi.delete(baris)

    site.last_scan_at = sekarang
    site.last_seen_at = sekarang
    site.last_error = None
    sesi.commit()
    return len(terlihat)


def tangani_scan_site(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    data = klien.inventory()
    jumlah = simpan_inventaris(sesi, site, data)
    if site.status in (SiteStatus.unreachable, SiteStatus.needs_reconnect, SiteStatus.blocked):
        site.status = SiteStatus.active
        sesi.commit()
    return {"jumlah_paket": jumlah}


def tangani_verify_site(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    data = klien.ping()
    site.connector_version = data.get("connector_version")
    site.wp_version = data.get("wp_version")
    site.php_version = data.get("php_version")
    site.last_seen_at = datetime.now(timezone.utc)
    site.last_error = None
    site.status = SiteStatus.active
    sesi.commit()
    return data
