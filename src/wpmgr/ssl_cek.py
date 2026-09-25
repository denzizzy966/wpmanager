"""Pemeriksaan masa berlaku sertifikat SSL harian untuk setiap site https."""

import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from wpmgr.models import Site, SiteStatus

AMBANG_HARI_SSL = 14


def baca_kedaluwarsa(host: str, port: int = 443, timeout: float = 15.0) -> datetime:
    konteks = ssl.create_default_context()
    with (
        socket.create_connection((host, port), timeout=timeout) as sock,
        konteks.wrap_socket(sock, server_hostname=host) as tls,
    ):
        sertifikat = tls.getpeercert()
    detik = ssl.cert_time_to_seconds(sertifikat["notAfter"])
    return datetime.fromtimestamp(detik, tz=timezone.utc)


def cek_semua_ssl(sesi: Session, baca_fn=baca_kedaluwarsa, sekarang: datetime | None = None) -> int:
    sekarang = sekarang or datetime.now(timezone.utc)
    dicek = 0
    for site in sesi.scalars(select(Site).where(Site.status != SiteStatus.disabled)).all():
        bagian = urlsplit(site.url)
        if bagian.scheme != "https" or not bagian.hostname:
            continue
        try:
            # bagian.port melempar ValueError untuk port bukan angka (mis.
            # "https://contoh.test:abcd"). buat_site hanya mengecek prefiks
            # https, jadi URL semacam ini tetap bisa tersimpan. Satu site
            # rusak wajib jadi ssl_error per-site, bukan menghentikan batch
            # (dan karenanya juga tidak boleh melewatkan sesi.commit()).
            port = bagian.port or 443
        except ValueError:
            site.ssl_error = f"URL site tidak sah: {site.url}"[:500]
            site.ssl_dicek_pada = sekarang
            dicek += 1
            continue
        try:
            site.ssl_kedaluwarsa = baca_fn(bagian.hostname, port)
            site.ssl_error = None
        except ssl.SSLCertVerificationError as exc:
            alasan = getattr(exc, "verify_message", None) or str(exc)
            site.ssl_error = f"Sertifikat tidak valid: {alasan}"[:500]
        except (OSError, ssl.SSLError) as exc:
            site.ssl_error = f"Tidak dapat membuka koneksi TLS: {exc}"[:500]
        site.ssl_dicek_pada = sekarang
        dicek += 1
    sesi.commit()
    return dicek


def sisa_hari_ssl(site, sekarang: datetime) -> int | None:
    if site.ssl_kedaluwarsa is None:
        return None
    return (site.ssl_kedaluwarsa - sekarang).days


def ssl_bermasalah(site, sekarang: datetime) -> bool:
    if site.ssl_error:
        return True
    sisa = sisa_hari_ssl(site, sekarang)
    return sisa is not None and sisa < AMBANG_HARI_SSL
