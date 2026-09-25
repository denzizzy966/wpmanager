import uuid

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from wpmgr.models import Site, SiteStatus, User

pytestmark = pytest.mark.integration


@pytest.fixture
def klien(engine, monkeypatch, sesi):
    from sqlalchemy.orm import sessionmaker

    from wpmgr import db
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    sesi.add(User(id=uuid.uuid4(), email="a@b.test", nama="Uji",
                  password_hash=PasswordHasher().hash("sandi")))
    sesi.commit()
    # base_url harus "https" (bukan default TestClient "http://testserver"):
    # SessionMiddleware memakai https_only=True sehingga cookie sesi ditandai
    # Secure, dan httpx diam-diam menolak mengirim balik cookie Secure di atas
    # permintaan berskema http meski cookie itu ada di jar-nya.
    c = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    c.post("/login", data={"email": "a@b.test", "password": "sandi"})
    return c


@pytest.mark.parametrize("jalur", ["/", "/updates", "/sites", "/sites/new", "/activity"])
def test_halaman_terbuka_dan_memuat_datagrid(klien, jalur):
    r = klien.get(jalur)
    assert r.status_code == 200
    assert "/static/vendor/datagrid/datagrid.js" in r.text


def test_halaman_update_menyajikan_layar_update(klien):
    # Regresi terhadap Task 21, yang memindahkan updates.html dari "/" ke
    # "/updates" supaya "/" bisa dipakai halaman Kesehatan: rute lama harus
    # tetap menyajikan updates.html yang sama persis di path barunya, bukan
    # ikut terhapus atau diam-diam menjadi placeholder.
    r = klien.get("/updates")
    assert r.status_code == 200
    assert "layarUpdate()" in r.text
    assert "Masuk sebagai" not in r.text


def test_buat_site_menampilkan_kunci_koneksi(klien, sesi):
    r = klien.post("/sites", data={"nama": "Client A", "url": "https://client-a.test"})
    assert r.status_code == 200
    assert "kunci koneksi" in r.text.lower()
    assert sesi.query(Site).filter_by(url="https://client-a.test").count() == 1


def test_buat_site_http_menampilkan_galat(klien):
    r = klien.post("/sites", data={"nama": "X", "url": "http://tidak-aman.test"})
    assert r.status_code == 200
    assert "https" in r.text.lower()


def test_buat_site_gagal_mempertahankan_input(klien):
    # Kegagalan validasi tidak boleh membuang apa yang sudah diketik operator
    # — itu justru saat orang paling malas mengetik ulang URL yang panjang.
    r = klien.post("/sites", data={"nama": "Client Y", "url": "http://tidak-aman.test"})
    assert r.status_code == 200
    assert "https" in r.text.lower()
    assert "Client Y" in r.text
    assert "http://tidak-aman.test" in r.text


def test_buat_site_url_duplikat_menampilkan_galat_bukan_500(klien, sesi):
    from wpmgr.pairing import buat_site

    buat_site(sesi, "Awal", "https://dup.test", None, None)
    r = klien.post("/sites", data={"nama": "Client Z", "url": "https://dup.test"})
    assert r.status_code == 200
    assert "sudah terdaftar" in r.text.lower()
    assert "Client Z" in r.text
    assert "https://dup.test" in r.text


def test_detail_site_terbuka(klien, sesi):
    from wpmgr.pairing import buat_site

    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    r = klien.get(f"/sites/{site.id}")
    assert r.status_code == 200
    assert "Contoh" in r.text


def test_detail_site_menyebut_pencabutan_manual_plugin_dan_user(klien, sesi):
    from wpmgr.pairing import buat_site

    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    r = klien.get(f"/sites/{site.id}")
    isi = r.text.lower()
    assert "wp-manager-connector" in isi
    assert "wpmgr" in isi
    assert "manual" in isi or "hapus" in isi


def test_detail_site_tidak_ada_404(klien):
    assert klien.get(f"/sites/{uuid.uuid4()}").status_code == 404


def test_activity_menampilkan_baris_log(klien, sesi):
    from wpmgr.models import ActivityLog
    from wpmgr.pairing import buat_site

    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    sesi.add(ActivityLog(site_id=site.id, level="info", pesan="Pesan uji aktivitas"))
    sesi.commit()

    r = klien.get("/activity")
    assert r.status_code == 200
    assert "Pesan uji aktivitas" in r.text


def test_activity_menampilkan_isi_detail(klien, sesi):
    """R57: versi sebelum/sesudah dan pesan WordPress hidup di `detail`; tanpa
    ditampilkan, jejak audit hanya bisa dibaca lewat psql."""
    from wpmgr.models import ActivityLog
    from wpmgr.pairing import buat_site

    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    sesi.add(ActivityLog(
        site_id=site.id, level="info", pesan="Update plugin",
        detail={"tipe": "plugin", "slug": "elementor/elementor.php",
                "versi_sebelum": "3.18.3", "versi_sesudah": "3.20.1",
                "email": "op@contoh.test", "pesan": "Plugin berhasil diperbarui."},
    ))
    sesi.add(ActivityLog(
        site_id=site.id, level="error", pesan="update_package gagal: upgrade_failed",
        detail={"pesan": "Could not copy file.", "worker": "host:1"},
    ))
    sesi.commit()

    r = klien.get("/activity")
    assert "3.18.3" in r.text
    assert "3.20.1" in r.text
    assert "Plugin berhasil diperbarui." in r.text
    assert "op@contoh.test" in r.text
    assert "Could not copy file." in r.text


def test_activity_meng_escape_isi_detail(klien, sesi):
    """Isi `detail` datang dari site client (pesan WordPress, versi) -- site
    yang justru mungkin sudah disusupi. Harus lewat autoescape Jinja."""
    from wpmgr.models import ActivityLog
    from wpmgr.pairing import buat_site

    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    sesi.add(ActivityLog(
        site_id=site.id, level="error", pesan="gagal",
        detail={"pesan": "<script>alert(1)</script>",
                "versi_sebelum": "<img src=x onerror=alert(2)>", "versi_sesudah": "1.0"},
    ))
    sesi.commit()

    r = klien.get("/activity")
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    assert "<img src=x" not in r.text


def test_halaman_menolak_tanpa_login():
    from wpmgr.web.app import buat_app

    c = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    r = c.get("/sites")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_status_site_yang_belum_dikenal_tidak_meledak(klien, sesi):
    # SiteStatus punya nilai yang tidak dipetakan WARNA_STATUS di sites.js;
    # halaman sendiri tidak boleh gagal render untuk status apa pun.
    from wpmgr.pairing import buat_site

    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    site.status = SiteStatus.disabled
    sesi.commit()
    r = klien.get(f"/sites/{site.id}")
    assert r.status_code == 200
