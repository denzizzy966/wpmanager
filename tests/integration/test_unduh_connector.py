import pytest
from fastapi.testclient import TestClient

from wpmgr.connector_paket import bangun_paket, sumber_bawaan

pytestmark = pytest.mark.integration


def test_unduh_butuh_login(engine):
    from wpmgr.web.app import buat_app

    c = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    assert c.get("/connector/unduh").status_code == 303


def test_unduh_404_bila_paket_belum_dibangun(klien_web, var_sementara):
    r = klien_web.get("/connector/unduh")
    assert r.status_code == 404
    assert "build-connector" in r.text


def test_unduh_menyajikan_zip_bernama_versi(klien_web, var_sementara):
    manifest = bangun_paket(sumber_bawaan(), var_sementara / "connector")
    r = klien_web.get("/connector/unduh")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f"wp-manager-connector-{manifest['versi']}.zip" in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"


def test_halaman_tambah_site_menautkan_unduhan(klien_web, var_sementara):
    bangun_paket(sumber_bawaan(), var_sementara / "connector")
    r = klien_web.get("/sites/new")
    assert 'href="/connector/unduh"' in r.text
