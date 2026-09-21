import uuid

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from wpmgr.models import User

pytestmark = pytest.mark.integration


@pytest.fixture
def klien(engine, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from wpmgr import db
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    # base_url harus "https" (bukan default TestClient "http://testserver"):
    # SessionMiddleware memakai https_only=True sehingga cookie sesi ditandai
    # Secure, dan httpx diam-diam menolak mengirim balik cookie Secure di atas
    # permintaan berskema http meski cookie itu ada di jar-nya.
    return TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")


@pytest.fixture
def pengguna(sesi):
    u = User(
        id=uuid.uuid4(),
        email="a@b.test",
        nama="Uji",
        password_hash=PasswordHasher().hash("sandi-benar"),
    )
    sesi.add(u)
    sesi.commit()
    return u


def test_halaman_dilindungi_mengarahkan_ke_login(klien):
    r = klien.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_api_dilindungi_membalas_401(klien):
    assert klien.get("/api/sites").status_code == 401


def test_login_dengan_sandi_benar(klien, pengguna):
    r = klien.post("/login", data={"email": "a@b.test", "password": "sandi-benar"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert klien.get("/api/sites").status_code == 200


def test_login_dengan_sandi_salah_ditolak(klien, pengguna):
    r = klien.post("/login", data={"email": "a@b.test", "password": "salah"})
    assert r.status_code == 200
    assert "tidak cocok" in r.text.lower()


def test_email_tidak_dikenal_ditolak(klien, pengguna):
    r = klien.post("/login", data={"email": "x@y.test", "password": "apa-saja"})
    assert r.status_code == 200
    assert "tidak cocok" in r.text.lower()


def test_logout_mengakhiri_sesi(klien, pengguna):
    klien.post("/login", data={"email": "a@b.test", "password": "sandi-benar"})
    klien.post("/logout")
    assert klien.get("/api/sites").status_code == 401
