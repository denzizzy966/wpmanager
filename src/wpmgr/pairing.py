import base64
import uuid

from sqlalchemy.orm import Session

from wpmgr.config import get_settings
from wpmgr.crypto import enkripsi_secret, secret_baru
from wpmgr.models import Site, SiteStatus


def kunci_koneksi(site_id: str, secret_hex: str, dashboard_url: str) -> str:
    mentah = f"{site_id}:{secret_hex}:{dashboard_url.rstrip('/')}".encode()
    return base64.urlsafe_b64encode(mentah).decode("ascii").rstrip("=")


def buat_site(
    sesi: Session,
    nama: str,
    url: str,
    client_id: uuid.UUID | None,
    dibuat_oleh: uuid.UUID | None,
) -> tuple[Site, str]:
    url = url.strip().rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("URL site wajib berskema https://")

    secret = secret_baru()
    site = Site(
        id=uuid.uuid4(),
        client_id=client_id,
        nama=nama.strip(),
        url=url,
        status=SiteStatus.pending_pair,
        secret_terenkripsi=enkripsi_secret(secret),
    )
    sesi.add(site)
    sesi.commit()
    return site, kunci_koneksi(str(site.id), secret, get_settings().base_url)
