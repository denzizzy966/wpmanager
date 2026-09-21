import uuid

import pytest

from wpmgr.crypto import dekripsi_secret

from .conftest import (
    AKAR_REPO,
    PLUGIN_DI_KONTAINER,
    hapus_di_kontainer,
    klien_http,
    permintaan_bertanda,
    tulis_di_kontainer,
    versi_connector_sumber,
    wpcli,
)

pytestmark = pytest.mark.e2e


def test_direktori_plugin_bukan_bind_mount(wp_site):
    nama = f"penanda-{uuid.uuid4().hex[:8]}.txt"
    tulis_di_kontainer(f"{PLUGIN_DI_KONTAINER}/{nama}", "x")
    try:
        assert not (AKAR_REPO / "connector" / "wp-manager-connector" / nama).exists()
    finally:
        hapus_di_kontainer(f"{PLUGIN_DI_KONTAINER}/{nama}")


def test_ping_mengumumkan_kemampuan(sesi, site_terpasang):
    data = klien_http(site_terpasang).ping()
    assert data["connector_version"] == versi_connector_sumber()
    assert isinstance(data["fitur"], list)
    assert data["versi_skema"] >= 1
    assert data["percayai_xff"] is False


def test_header_anti_cache_pada_respons_sah_dan_penolakan(sesi, site_terpasang):
    secret = dekripsi_secret(site_terpasang.secret_terenkripsi)
    sah = permintaan_bertanda(site_terpasang, secret, "GET", "/wpmgr/v1/ping")
    ditolak = permintaan_bertanda(site_terpasang, "0" * 64, "GET", "/wpmgr/v1/ping")
    assert sah.status_code == 200
    assert ditolak.status_code == 401
    for r in (sah, ditolak):
        assert "no-store" in r.headers["cache-control"]
        assert r.headers["x-litespeed-cache-control"] == "no-cache"


def test_tabel_pemantauan_dibuat(site_terpasang):
    tabel = wpcli("db", "query", "SHOW TABLES LIKE 'wp_wpmgr_%'", "--skip-column-names")
    assert set(tabel.split()) == {
        "wp_wpmgr_errors", "wp_wpmgr_logins", "wp_wpmgr_login_gagal",
        "wp_wpmgr_traffic", "wp_wpmgr_pengunjung",
    }
