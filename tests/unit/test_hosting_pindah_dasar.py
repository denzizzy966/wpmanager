from types import SimpleNamespace

import pytest

from wpmgr.errors import STAGING_DITOLAK, SiteError
from wpmgr.hosting.pindah import (
    DILINDUNGI_HOSTING,
    MU_PLUGIN_PRATINJAU,
    PESAN_HOST_LAIN,
    PESAN_HTTP,
    PESAN_SUBFOLDER,
    PESAN_WWW,
    cek_ram_hosting,
    periksa_info_hosting,
)
from wpmgr.staging.pembantu import StatusProd
from wpmgr.staging.umum import GalatDitolakTanpaUbah

GB = 1024**3


@pytest.mark.parametrize("home,siteurl,www,pesan", [
    ("http://toko.co.id", "https://toko.co.id", True, PESAN_HTTP),
    ("https://toko.co.id", "http://toko.co.id", True, PESAN_HTTP),
    ("https://toko.co.id/blog", "https://toko.co.id/blog", True, PESAN_SUBFOLDER),
    ("https://toko.co.id:8443", "https://toko.co.id:8443", True, PESAN_SUBFOLDER),
    ("https://lain.id", "https://lain.id", True, PESAN_HOST_LAIN),
    ("https://toko.co.id.lain.id", "https://toko.co.id.lain.id", True, PESAN_HOST_LAIN),
    ("https://www.toko.co.id", "https://www.toko.co.id", False, PESAN_WWW),
])
def test_periksa_info_hosting_menolak(home, siteurl, www, pesan):
    h = SimpleNamespace(domain="toko.co.id", dengan_www=www)
    with pytest.raises(GalatDitolakTanpaUbah) as e:
        periksa_info_hosting(h, {"home": home, "siteurl": siteurl})
    assert e.value.pesan == pesan


@pytest.mark.parametrize("home,www", [
    ("https://toko.co.id", False), ("https://www.toko.co.id", True), ("https://TOKO.co.id/", False),
])
def test_periksa_info_hosting_menerima(home, www):
    # Preflight M7: hasilnya dinyatakan, bukan sekadar "tidak melempar".
    assert periksa_info_hosting(SimpleNamespace(domain="toko.co.id", dengan_www=www),
                                {"home": home, "siteurl": home}) is None


def test_cek_ram_hosting():
    assert cek_ram_hosting(StatusProd(3 * GB, 1, 1, 1, 1, {})) is None
    pesan = cek_ram_hosting(StatusProd(1 * GB, 1, 1, 1, 1, {}))
    assert pesan.startswith("RAM tersedia di VPS 1,0 GB; minimal 2,0 GB")


def test_cek_ram_hosting_memakai_cek_ram_bersama():
    # Preflight M6: satu sumber aturan RAM; hanya kata tujuannya yang berbeda.
    from wpmgr.staging.rencana import cek_ram

    st = StatusProd(1 * GB, 1, 1, 1, 1, {})
    assert cek_ram_hosting(st) == cek_ram(st, "situs")
    assert cek_ram_hosting(st).endswith("untuk menjalankan situs.")


def test_berkas_dilindungi_hosting():
    # wp-config.php milik skrip pembantu, mu-plugin pratinjau milik dashboard;
    # produksi tidak pernah punya mu-plugin itu, jadi tarik tidak boleh menghapusnya.
    assert MU_PLUGIN_PRATINJAU == "wp-content/mu-plugins/wpmgr-pratinjau.php"
    assert DILINDUNGI_HOSTING == frozenset({"wp-config.php", MU_PLUGIN_PRATINJAU})


def test_tujuan_hosting_tidak_menyentuh_secret_connector(tmp_path, monkeypatch):
    # Putusan R25 tidak berlaku (spec §4.1): salinan ini akan menjadi produksi
    # dan memakai secret connector produksi yang sama; tidak ada SQL tambahan.
    import uuid

    from wpmgr.hosting import pindah

    monkeypatch.setattr(pindah.hu, "dir_hosting", lambda site_id: tmp_path / str(site_id))
    h = SimpleNamespace(nama="toko-co-id", domain="toko.co.id", dengan_www=True, sandi_hash="x")
    site = SimpleNamespace(id=uuid.uuid4())
    t = pindah.tujuan_hosting(None, None, site, h, SimpleNamespace(prod_status=lambda: None))
    db = tmp_path / "db"
    db.mkdir()
    info = {"prefix": "wp_", "tabel": [{"nama": "wp_options"}]}
    assert t.sql_tambahan(None, info, db) is None
    assert list(db.iterdir()) == []
    assert t.dilindungi is DILINDUNGI_HOSTING
    assert t.subdir == ("files", "log") and t.tahap_akhir == "pratinjau"


@pytest.mark.parametrize("ubah", [{"multisite": True}, {"konten_di_luar": True}])
def test_teks_penolakan_mesin_tarik_bersama_netral(ubah):
    # Mesin tarik dipakai staging dan hosting VPS: teksnya tidak menyebut staging.
    from wpmgr.staging import tarik

    info = {"table_prefix": "wp_", "home": "https://toko.co.id", "siteurl": "https://toko.co.id",
            "tabel": [{"nama": "wp_options", "baris": 1, "ukuran": 1, "pk": []}], **ubah}
    with pytest.raises(SiteError) as e:
        tarik.urai_info(info)
    assert e.value.error_class == STAGING_DITOLAK
    assert "staging" not in e.value.pesan.lower()
    assert "belum didukung" in e.value.pesan
