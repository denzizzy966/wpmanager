from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from sqlalchemy import func as safunc
from sqlalchemy import select
from sqlalchemy.orm import Session

from wpmgr.crypto import dekripsi_secret
from wpmgr.jobs.queue import jeda_menit
from wpmgr.models import (
    Job,
    JobStatus,
    JobType,
    PackageType,
    Site,
    SitePackage,
    SiteStatus,
)
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


def _tipe_dilaporkan(data: dict) -> set[PackageType]:
    """Kategori mana yang benar-benar dilaporkan pada payload ini.

    Pembedaannya penting: `themes: []` berarti "tema dilaporkan, tidak ada satu
    pun" dan baris tema lama memang harus dihapus. `themes` yang tidak hadir
    berarti "tidak ada informasi tentang tema", dan menghapus apa pun atas dasar
    itu adalah kehilangan data, bukan sinkronisasi.
    """
    dilaporkan: set[PackageType] = set()
    if data.get("core"):
        dilaporkan.add(PackageType.core)
    if isinstance(data.get("plugins"), list):
        dilaporkan.add(PackageType.plugin)
    if isinstance(data.get("themes"), list):
        dilaporkan.add(PackageType.theme)
    return dilaporkan


def simpan_inventaris(sesi: Session, site: Site, data: dict) -> int:
    sekarang = datetime.now(timezone.utc)
    terlihat: set[tuple[PackageType, str]] = set()
    dilaporkan = _tipe_dilaporkan(data)

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
    dihapus = 0
    for baris in lama:
        if baris.tipe in dilaporkan and (baris.tipe, baris.slug) not in terlihat:
            sesi.delete(baris)
            dihapus += 1

    site.last_scan_at = sekarang
    site.last_seen_at = sekarang
    site.last_error = None
    sesi.commit()
    return len(lama) - dihapus


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


def tangani_update_package(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    p = job.payload
    hasil = klien.update(p["tipe"], p["slug"], p["ke_versi"])

    versi_sesudah = hasil.get("versi_sesudah") or p["ke_versi"]
    baris = sesi.scalar(
        select(SitePackage).where(
            SitePackage.site_id == site.id,
            SitePackage.tipe == PackageType(p["tipe"]),
            SitePackage.slug == p["slug"],
        )
    )
    if baris is not None:
        baris.versi_terpasang = versi_sesudah
        if baris.versi_tersedia == versi_sesudah:
            baris.versi_tersedia = None
        baris.last_scan_at = datetime.now(timezone.utc)
    site.last_seen_at = datetime.now(timezone.utc)
    sesi.commit()
    return hasil


def resolusi_unknown(sesi: Session, job: Job, klien: SiteClient) -> str:
    """Setelah timeout, tanyakan keadaan sebenarnya ke site alih-alih menebak."""
    site = sesi.get(Site, job.site_id)
    p = job.payload
    simpan_inventaris(sesi, site, klien.inventory())

    baris = sesi.scalar(
        select(SitePackage).where(
            SitePackage.site_id == site.id,
            SitePackage.tipe == PackageType(p["tipe"]),
            SitePackage.slug == p["slug"],
        )
    )
    if baris is not None and baris.versi_terpasang == p["ke_versi"]:
        job.status = JobStatus.success
        job.hasil = {"versi_sesudah": baris.versi_terpasang,
                     "pesan": "terverifikasi lewat scan ulang setelah timeout"}
        job.error = None
        job.error_class = None
        job.finished_at = safunc.now()
        sesi.commit()
        return "success"

    if job.attempts < job.max_attempts:
        job.status = JobStatus.pending
        job.scheduled_for = safunc.now() + timedelta(minutes=jeda_menit(job.attempts))
        job.started_at = None
        sesi.commit()
        return "pending"

    job.status = JobStatus.failed
    job.finished_at = safunc.now()
    sesi.commit()
    return "failed"


HANDLER = {
    JobType.scan_site: tangani_scan_site,
    JobType.update_package: tangani_update_package,
    JobType.verify_site: tangani_verify_site,
}
