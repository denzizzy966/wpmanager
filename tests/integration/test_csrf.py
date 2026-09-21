import json
import time
import uuid

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from wpmgr.crypto import dekripsi_secret
from wpmgr.models import Job, JobType, User
from wpmgr.pairing import buat_site
from wpmgr.signing import sign

pytestmark = pytest.mark.integration

# tests/conftest.py menyetel WPMGR_BASE_URL=https://wpmgr.test. TestClient
# sendiri memakai base_url https://testserver dan TIDAK mengirim Origin
# maupun Referer, sehingga test lain di suite ini masuk cabang "keduanya
# tidak ada" dan tidak terpengaruh pemeriksaan ini.
ASAL_DASHBOARD = "https://wpmgr.test"
ASAL_ASING = "https://jahat.test"


@pytest.fixture(autouse=True)
def _bersihkan_pembatas_pairing():
    from wpmgr.web.routes_pair import _nonce_terpakai, _pembatas

    _pembatas.clear()
    _nonce_terpakai.clear()
    yield
    _pembatas.clear()
    _nonce_terpakai.clear()


@pytest.fixture
def klien(engine, monkeypatch, sesi):
    from sqlalchemy.orm import sessionmaker

    from wpmgr import db
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    sesi.add(User(id=uuid.uuid4(), email="a@b.test", nama="Uji",
                  password_hash=PasswordHasher().hash("sandi")))
    sesi.commit()
    c = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    c.post("/login", data={"email": "a@b.test", "password": "sandi"})
    return c


@pytest.fixture
def site(sesi):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    return site


def _scan(klien, site, **headers):
    return klien.post("/api/jobs/scan", json={"site_id": str(site.id)}, headers=headers)


def test_post_lintas_asal_ditolak(klien, site, sesi):
    r = _scan(klien, site, Origin=ASAL_ASING)
    assert r.status_code == 403
    assert sesi.query(Job).count() == 0


def test_post_origin_sama_diterima(klien, site):
    assert _scan(klien, site, Origin=ASAL_DASHBOARD).status_code == 200


def test_post_tanpa_origin_dan_referer_diterima(klien, site):
    assert _scan(klien, site).status_code == 200


def test_referer_lintas_asal_ditolak_bila_origin_tidak_ada(klien, site):
    assert _scan(klien, site, Referer=f"{ASAL_ASING}/halaman").status_code == 403


def test_referer_asal_sama_diterima(klien, site):
    assert _scan(klien, site, Referer=f"{ASAL_DASHBOARD}/sites").status_code == 200


def test_origin_null_ditolak(klien, site):
    # Browser mengirim "Origin: null" dari iframe ber-sandbox dan konteks
    # sejenis -- persis tempat halaman penyerang bisa bersembunyi.
    assert _scan(klien, site, Origin="null").status_code == 403


def test_origin_beda_port_ditolak(klien, site):
    assert _scan(klien, site, Origin="https://wpmgr.test:8443").status_code == 403


def test_origin_dengan_port_bawaan_eksplisit_diterima(klien, site):
    assert _scan(klien, site, Origin="https://wpmgr.test:443").status_code == 200


def test_delete_lintas_asal_ditolak(klien, site, sesi):
    from wpmgr.models import Site

    r = klien.delete(f"/api/sites/{site.id}", headers={"Origin": ASAL_ASING})
    assert r.status_code == 403
    sesi.expire_all()
    assert sesi.get(Site, site.id) is not None


def test_get_lintas_asal_tidak_diperiksa(klien):
    assert klien.get("/api/sites", headers={"Origin": ASAL_ASING}).status_code == 200


def test_login_lintas_asal_ditolak(klien):
    r = klien.post("/login", data={"email": "a@b.test", "password": "sandi"},
                   headers={"Origin": ASAL_ASING})
    assert r.status_code == 403


def test_pair_confirm_tidak_terpengaruh_origin(klien, sesi):
    """/api/pair/confirm diamankan HMAC dan dipanggil server site client,
    bukan browser; Origin apa pun yang dibawanya tidak relevan."""
    site, _ = buat_site(sesi, "Pair", "https://pair.test", None, None)
    secret = dekripsi_secret(site.secret_terenkripsi)
    body = json.dumps({"connector_version": "1.0"}, separators=(",", ":")).encode()
    ts, nonce = int(time.time()), uuid.uuid4().hex
    r = klien.post("/api/pair/confirm", content=body, headers={
        "Content-Type": "application/json",
        "Origin": ASAL_ASING,
        "Referer": f"{ASAL_ASING}/x",
        "X-Wpmgr-Site": str(site.id),
        "X-Wpmgr-Timestamp": str(ts),
        "X-Wpmgr-Nonce": nonce,
        "X-Wpmgr-Signature": sign(secret, "POST", "/api/pair/confirm", ts, nonce, body),
    })
    assert r.status_code == 200
    assert sesi.query(Job).filter_by(site_id=site.id, tipe=JobType.verify_site).count() == 1


# --- R59-i: pembatas laju login ----------------------------------------------

def test_login_dibatasi_sepuluh_per_menit(engine, monkeypatch, sesi):
    from sqlalchemy.orm import sessionmaker

    from wpmgr import db
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    c = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")

    kode = [c.post("/login", data={"email": "x@y.test", "password": "salah"}).status_code
            for _ in range(11)]
    assert kode[:10] == [200] * 10
    assert kode[10] == 429


def test_pembatas_login_per_app_bukan_global(engine, monkeypatch, sesi):
    """Setiap app punya pembatasnya sendiri, jadi test (dan proses) lain yang
    membangun app baru tidak mewarisi hitungan dari yang sebelumnya."""
    from sqlalchemy.orm import sessionmaker

    from wpmgr import db
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    a = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    for _ in range(10):
        a.post("/login", data={"email": "x@y.test", "password": "salah"})
    b = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    assert b.post("/login", data={"email": "x@y.test", "password": "salah"}).status_code == 200
