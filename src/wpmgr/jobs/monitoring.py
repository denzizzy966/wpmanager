"""Handler job Lapis 2: pengambilan data pemantauan dan pembaruan connector.

Modul ini tidak mengimpor wpmgr.jobs.handlers; handlers yang mengimpor modul
ini untuk menyusun HANDLER.
"""

import hashlib

from sqlalchemy.orm import Session

from wpmgr.config import get_settings
from wpmgr.connector_paket import baca_manifest, baca_zip
from wpmgr.jobs.queue import antrekan_jika_belum
from wpmgr.models import ActivityLog, Job, JobType, Site, User
from wpmgr.site_client import SiteClient


def _email_pembuat(sesi: Session, job: Job) -> str | None:
    if job.dibuat_oleh is None:
        return None
    pembuat = sesi.get(User, job.dibuat_oleh)
    return pembuat.email if pembuat is not None else None


def tangani_update_connector(sesi: Session, job: Job, klien: SiteClient) -> dict:
    folder = get_settings().jalur_connector
    manifest = baca_manifest(folder)
    if manifest is None:
        # Kesalahan konfigurasi dashboard, bukan kondisi site: RuntimeError
        # membuat worker menandainya internal_error tanpa retry.
        raise RuntimeError(
            "Paket connector belum dibangun; jalankan python -m wpmgr.cli build-connector"
        )
    isi = baca_zip(folder)
    if hashlib.sha256(isi).hexdigest() != manifest["sha256"]:
        raise RuntimeError("Zip connector tidak cocok dengan manifest-nya; bangun ulang paket")

    site = sesi.get(Site, job.site_id)
    hasil = klien.self_update(manifest["versi"], manifest["sha256"], isi)
    sebelum = hasil.get("versi_sebelum") or site.connector_version
    sesudah = hasil.get("versi_sesudah") or manifest["versi"]
    site.connector_version = sesudah

    email = _email_pembuat(sesi, job)
    oleh = f" oleh {email}" if email else ""
    detail = {"versi_sebelum": sebelum, "versi_sesudah": sesudah, "email": email}
    if hasil.get("pesan"):
        detail["pesan"] = str(hasil["pesan"])[:500]
    sesi.add(
        ActivityLog(
            site_id=site.id, job_id=job.id, user_id=job.dibuat_oleh, level="info",
            pesan=f"Connector diperbarui: {sebelum or '?'} → {sesudah}{oleh}",
            detail=detail,
        )
    )
    sesi.commit()
    # Versi baru mungkin mengumumkan fitur baru; verify mencatatnya segera,
    # bukan menunggu scan per jam.
    antrekan_jika_belum(sesi, site.id, JobType.verify_site)
    return hasil
