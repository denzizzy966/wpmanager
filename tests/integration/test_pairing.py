import base64
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from wpmgr.crypto import dekripsi_secret
from wpmgr.models import Job, JobType, Site, SiteStatus
from wpmgr.pairing import buat_site, kunci_koneksi
from wpmgr.signing import sign

pytestmark = pytest.mark.integration

PATH = "/api/pair/confirm"


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


def test_kunci_koneksi_dapat_diurai_kembali():
    # dashboard_url mengandung titik dua sendiri ("https://..."), sehingga
    # split tanpa batas akan memecahnya lebih dari tiga bagian. PHP mengurai
    # ini dengan explode(':', $mentah, 3) -- batas tiga -- persis karena itu,
    # jadi assert di sini memakai maxsplit=2 agar cocok dengan perilaku nyata.
    k = kunci_koneksi("sid", "a" * 64, "https://dash.test")
    mentah = base64.urlsafe_b64decode(k + "=" * (-len(k) % 4)).decode()
    assert mentah.split(":", 2) == ["sid", "a" * 64, "https://dash.test"]


def test_buat_site_menyimpan_secret_terenkripsi(sesi):
    site, kunci = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    assert site.status == SiteStatus.pending_pair
    assert site.secret_terenkripsi != b""
    assert len(dekripsi_secret(site.secret_terenkripsi)) == 64
    assert kunci


def test_url_http_ditolak(sesi):
    with pytest.raises(ValueError):
        buat_site(sesi, "Tidak Aman", "http://contoh.test", None, None)


def _kirim(klien, site, secret, body_obj, ts=None):
    body = json.dumps(body_obj, separators=(",", ":")).encode()
    ts = ts or int(time.time())
    nonce = uuid.uuid4().hex
    return klien.post(
        PATH,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Wpmgr-Site": str(site.id),
            "X-Wpmgr-Timestamp": str(ts),
            "X-Wpmgr-Nonce": nonce,
            "X-Wpmgr-Signature": sign(secret, "POST", PATH, ts, nonce, body),
        },
    )


def test_confirm_sah_membuat_job_verify(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    secret = dekripsi_secret(site.secret_terenkripsi)

    r = _kirim(klien, site, secret,
               {"connector_version": "1.0", "wp_version": "6.5", "php_version": "8.1"})

    assert r.status_code == 200
    sesi.expire_all()
    disimpan = sesi.get(Site, site.id)
    assert disimpan.connector_version == "1.0"
    assert sesi.query(Job).filter_by(site_id=site.id, tipe=JobType.verify_site).count() == 1


def test_confirm_dengan_tanda_tangan_salah_ditolak(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    r = _kirim(klien, site, "f" * 64, {"connector_version": "1.0"})
    assert r.status_code == 401


def test_confirm_dengan_timestamp_kedaluwarsa_ditolak(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    secret = dekripsi_secret(site.secret_terenkripsi)
    r = _kirim(klien, site, secret, {"connector_version": "1.0"}, ts=int(time.time()) - 400)
    assert r.status_code == 401


def test_confirm_untuk_site_tidak_dikenal_ditolak(klien):
    body = b"{}"
    ts, nonce = int(time.time()), uuid.uuid4().hex
    r = klien.post(PATH, content=body, headers={
        "X-Wpmgr-Site": str(uuid.uuid4()),
        "X-Wpmgr-Timestamp": str(ts),
        "X-Wpmgr-Nonce": nonce,
        "X-Wpmgr-Signature": sign("a" * 64, "POST", PATH, ts, nonce, body),
    })
    assert r.status_code == 401


def test_confirm_tidak_butuh_sesi_login(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    secret = dekripsi_secret(site.secret_terenkripsi)
    assert _kirim(klien, site, secret, {"connector_version": "1.0"}).status_code == 200


def test_rate_limit_menolak_percobaan_berlebihan(sesi, klien):
    from wpmgr.web.routes_pair import _pembatas

    _pembatas.clear()
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)

    kode = [_kirim(klien, site, "f" * 64, {"x": 1}).status_code for _ in range(12)]
    assert kode[:10] == [401] * 10
    assert kode[10] == 429
    assert kode[11] == 429
