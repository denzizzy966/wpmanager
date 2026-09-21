from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from wpmgr.ssl_cek import sisa_hari_ssl, ssl_bermasalah

SEKARANG = datetime(2026, 9, 22, tzinfo=timezone.utc)


def site(kedaluwarsa=None, error=None):
    return SimpleNamespace(ssl_kedaluwarsa=kedaluwarsa, ssl_error=error)


def test_sisa_hari():
    assert sisa_hari_ssl(site(SEKARANG + timedelta(days=20, hours=3)), SEKARANG) == 20
    assert sisa_hari_ssl(site(), SEKARANG) is None


def test_bermasalah_bila_kurang_dari_14_hari():
    assert ssl_bermasalah(site(SEKARANG + timedelta(days=13)), SEKARANG)
    assert not ssl_bermasalah(site(SEKARANG + timedelta(days=14, hours=1)), SEKARANG)


def test_bermasalah_bila_sudah_lewat_atau_error():
    assert ssl_bermasalah(site(SEKARANG - timedelta(days=1)), SEKARANG)
    assert ssl_bermasalah(site(error="Sertifikat tidak valid"), SEKARANG)


def test_belum_pernah_dicek_tidak_bermasalah():
    assert not ssl_bermasalah(site(), SEKARANG)
