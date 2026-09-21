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


@pytest.mark.parametrize("jalur", ["/", "/sites", "/sites/new", "/activity"])
def test_halaman_terbuka_dan_memuat_datagrid(klien, jalur):
    r = klien.get(jalur)
    assert r.status_code == 200
    assert "/static/vendor/datagrid/datagrid.js" in r.text


def test_beranda_menyajikan_halaman_update_bukan_placeholder(klien):
    # Regresi terhadap placeholder GET "/" yang harus dihapus Task 21: jika
    # placeholder itu masih terdaftar, Starlette akan mencocokkannya lebih
    # dulu (ia terdaftar sebelum router halaman) dan "/" tak pernah benar-benar
    # menyajikan updates.html walau status code-nya tetap 200.
    r = klien.get("/")
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
