import hashlib
import json
import zipfile

import pytest

from wpmgr.connector_paket import (
    NAMA_MANIFEST,
    NAMA_ZIP,
    baca_manifest,
    bangun_paket,
    versi_dari_header,
)

HEADER = "<?php\n/**\n * Plugin Name: WP Manager Connector\n * Version:     2.3.4\n */\n"


@pytest.fixture
def sumber(tmp_path):
    akar = tmp_path / "wp-manager-connector"
    (akar / "includes").mkdir(parents=True)
    (akar / "wp-manager-connector.php").write_text(HEADER, encoding="utf-8")
    (akar / "includes" / "class-a.php").write_text("<?php", encoding="utf-8")
    (akar / "uninstall.php").write_text("<?php", encoding="utf-8")
    for dikecualikan in ("tests", "vendor", ".git"):
        (akar / dikecualikan).mkdir()
        (akar / dikecualikan / "x.php").write_text("<?php", encoding="utf-8")
    return akar


def test_versi_dari_header(sumber):
    assert versi_dari_header(sumber / "wp-manager-connector.php") == "2.3.4"


def test_header_tanpa_versi_ditolak(tmp_path):
    berkas = tmp_path / "p.php"
    berkas.write_text("<?php // tanpa header", encoding="utf-8")
    with pytest.raises(ValueError):
        versi_dari_header(berkas)


def test_zip_berisi_direktori_puncak_tanpa_tests_vendor_dan_dotfile(sumber, tmp_path):
    tujuan = tmp_path / "keluar"
    bangun_paket(sumber, tujuan)
    with zipfile.ZipFile(tujuan / NAMA_ZIP) as z:
        nama = set(z.namelist())
    assert nama == {
        "wp-manager-connector/wp-manager-connector.php",
        "wp-manager-connector/includes/class-a.php",
        "wp-manager-connector/uninstall.php",
    }


def test_manifest_cocok_dengan_zip(sumber, tmp_path):
    tujuan = tmp_path / "keluar"
    manifest = bangun_paket(sumber, tujuan)
    isi = (tujuan / NAMA_ZIP).read_bytes()
    assert manifest["versi"] == "2.3.4"
    assert manifest["sha256"] == hashlib.sha256(isi).hexdigest()
    assert manifest["ukuran"] == len(isi)
    assert baca_manifest(tujuan) == manifest


def test_baca_manifest_none_bila_tidak_ada_atau_rusak(tmp_path):
    assert baca_manifest(tmp_path) is None
    (tmp_path / NAMA_MANIFEST).write_text("bukan json", encoding="utf-8")
    assert baca_manifest(tmp_path) is None
    (tmp_path / NAMA_MANIFEST).write_text(json.dumps({"versi": "1"}), encoding="utf-8")
    assert baca_manifest(tmp_path) is None


def test_template_staging_ikut_zip(tmp_path):
    from wpmgr.connector_paket import NAMA_TEMPLATE_STAGING, sumber_bawaan

    tujuan = tmp_path / "keluar"
    bangun_paket(sumber_bawaan(), tujuan)
    with zipfile.ZipFile(tujuan / NAMA_ZIP) as z:
        assert f"wp-manager-connector/{NAMA_TEMPLATE_STAGING}" in z.namelist()


def test_isi_mu_plugin_staging_mengisi_nama():
    from wpmgr.connector_paket import isi_mu_plugin_staging

    isi = isi_mu_plugin_staging("toko-contoh")
    assert "define( 'WPMGR_STAGING_NAMA', 'toko-contoh' );" in isi
    assert "__WPMGR_NAMA__" not in isi


@pytest.mark.parametrize("nama", ["", "Toko", "a'b", "a" * 41, "toko\n", "../x"])
def test_isi_mu_plugin_staging_menolak_nama_tidak_sah(nama):
    from wpmgr.connector_paket import isi_mu_plugin_staging

    with pytest.raises(ValueError):
        isi_mu_plugin_staging(nama)
