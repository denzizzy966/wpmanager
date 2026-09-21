import ssl
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from wpmgr.models import Site, SiteStatus
from wpmgr.ssl_cek import cek_semua_ssl

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 22, tzinfo=timezone.utc)


def test_kedaluwarsa_disimpan(sesi, site):
    dilihat = []

    def baca(host, port):
        dilihat.append((host, port))
        return SEKARANG + timedelta(days=60)

    assert cek_semua_ssl(sesi, baca, SEKARANG) == 1
    sesi.refresh(site)
    assert site.ssl_kedaluwarsa == SEKARANG + timedelta(days=60)
    assert site.ssl_error is None
    assert site.ssl_dicek_pada == SEKARANG
    assert dilihat == [(site.url.removeprefix("https://"), 443)]


def test_sertifikat_tidak_valid_dicatat(sesi, site):
    def baca(host, port):
        raise ssl.SSLCertVerificationError("certificate verify failed: certificate has expired")

    cek_semua_ssl(sesi, baca, SEKARANG)
    sesi.refresh(site)
    assert site.ssl_error.startswith("Sertifikat tidak valid")


def test_koneksi_gagal_dicatat(sesi, site):
    def baca(host, port):
        raise OSError("Connection refused")

    cek_semua_ssl(sesi, baca, SEKARANG)
    sesi.refresh(site)
    assert site.ssl_error.startswith("Tidak dapat membuka koneksi TLS")


def test_site_http_dan_disabled_dilewati(sesi, site):
    site.url = "http://lokal.test"
    sesi.add(Site(id=uuid.uuid4(), nama="Mati", url="https://mati.test",
                  status=SiteStatus.disabled, secret_terenkripsi=b"x"))
    sesi.commit()
    assert cek_semua_ssl(sesi, lambda h, p: SEKARANG, SEKARANG) == 0


def test_port_non_standar(sesi, site):
    site.url = "https://contoh.test:8443"
    sesi.commit()
    dilihat = []
    cek_semua_ssl(sesi, lambda h, p: dilihat.append((h, p)) or SEKARANG, SEKARANG)
    assert dilihat == [("contoh.test", 8443)]
