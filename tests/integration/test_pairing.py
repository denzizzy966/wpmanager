import base64
import json
import time
import uuid

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from wpmgr.crypto import dekripsi_secret
from wpmgr.models import Job, JobType, Site, SiteStatus
from wpmgr.pairing import buat_site, kunci_koneksi
from wpmgr.signing import sign

pytestmark = pytest.mark.integration

PATH = "/api/pair/confirm"


@pytest.fixture(autouse=True)
def _bersihkan_status_modul():
    # _pembatas dan _nonce_terpakai adalah state mutable level-modul; tanpa
    # ini, urutan test yang tidak disengaja (mis. test rate-limit berjalan
    # sebelum test replay) bisa membuat test lain gagal atau lolos secara
    # kebetulan alih-alih karena benar.
    from wpmgr.web.routes_pair import _nonce_terpakai, _pembatas

    _pembatas.clear()
    _nonce_terpakai.clear()
    yield
    _pembatas.clear()
    _nonce_terpakai.clear()


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


def _permintaan(peer_host: str, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "client": (peer_host, 12345),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


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
    assert r.json() == {"detail": "Tidak sah"}


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
    assert r.json() == {"detail": "Tidak sah"}


def test_site_tidak_dikenal_dan_tanda_tangan_salah_tidak_dapat_dibedakan(sesi, klien):
    # Keduanya 401, tetapi jika body-nya berbeda, siapa pun yang dapat
    # menjangkau endpoint ini bisa memakai perbedaan itu sebagai oracle untuk
    # menebak site_id mana yang terdaftar -- persis yang coba dicegah rate
    # limit di bawah. Body harus identik byte demi byte.
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    r_salah = _kirim(klien, site, "f" * 64, {"x": 1})

    body = json.dumps({"x": 1}, separators=(",", ":")).encode()
    ts, nonce = int(time.time()), uuid.uuid4().hex
    r_asing = klien.post(PATH, content=body, headers={
        "X-Wpmgr-Site": str(uuid.uuid4()),
        "X-Wpmgr-Timestamp": str(ts),
        "X-Wpmgr-Nonce": nonce,
        "X-Wpmgr-Signature": sign("a" * 64, "POST", PATH, ts, nonce, body),
    })

    assert r_salah.status_code == r_asing.status_code == 401
    assert r_salah.json() == r_asing.json()


def test_confirm_tidak_butuh_sesi_login(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    secret = dekripsi_secret(site.secret_terenkripsi)
    assert _kirim(klien, site, secret, {"connector_version": "1.0"}).status_code == 200


def test_rate_limit_menolak_percobaan_berlebihan(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)

    kode = [_kirim(klien, site, "f" * 64, {"x": 1}).status_code for _ in range(12)]
    assert kode[:10] == [401] * 10
    assert kode[10] == 429
    assert kode[11] == 429


def test_ip_klien_mempercayai_x_real_ip_dari_loopback():
    from wpmgr.web.routes_pair import _ip_klien

    r = _permintaan("127.0.0.1", {"X-Real-IP": "9.9.9.9"})
    assert _ip_klien(r) == "9.9.9.9"


def test_ip_klien_mengabaikan_x_real_ip_dari_bukan_loopback():
    from wpmgr.web.routes_pair import _ip_klien

    r = _permintaan("203.0.113.5", {"X-Real-IP": "9.9.9.9"})
    assert _ip_klien(r) == "203.0.113.5"


def test_ip_klien_memakai_entri_terkanan_x_forwarded_for_dari_loopback():
    from wpmgr.web.routes_pair import _ip_klien

    r = _permintaan("127.0.0.1", {"X-Forwarded-For": "1.1.1.1, 2.2.2.2"})
    assert _ip_klien(r) == "2.2.2.2"


def test_pembatas_membuang_entri_basi():
    from collections import deque

    from wpmgr.web.routes_pair import _lolos_rate_limit, _pembatas

    _pembatas["ip-lama"] = deque([time.monotonic() - 120])
    assert _lolos_rate_limit("ip-baru")
    assert "ip-lama" not in _pembatas


def test_replay_nonce_ditolak(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    secret = dekripsi_secret(site.secret_terenkripsi)
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    body = json.dumps({"connector_version": "1.0"}, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Wpmgr-Site": str(site.id),
        "X-Wpmgr-Timestamp": str(ts),
        "X-Wpmgr-Nonce": nonce,
        "X-Wpmgr-Signature": sign(secret, "POST", PATH, ts, nonce, body),
    }

    r1 = klien.post(PATH, content=body, headers=headers)
    r2 = klien.post(PATH, content=body, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 401


def test_body_bukan_json_dengan_tanda_tangan_sah_mendapat_400(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    secret = dekripsi_secret(site.secret_terenkripsi)
    body = b"bukan-json"
    ts, nonce = int(time.time()), uuid.uuid4().hex
    r = klien.post(PATH, content=body, headers={
        "X-Wpmgr-Site": str(site.id),
        "X-Wpmgr-Timestamp": str(ts),
        "X-Wpmgr-Nonce": nonce,
        "X-Wpmgr-Signature": sign(secret, "POST", PATH, ts, nonce, body),
    })
    assert r.status_code == 400


@pytest.mark.parametrize("body", [b"[1,2]", b'"teks"', b"42", b"null"])
def test_body_json_bukan_objek_dengan_tanda_tangan_sah_mendapat_400(sesi, klien, body):
    """JSON sah tetapi bukan objek dulu meledak sebagai 500 di muatan.get()."""
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    secret = dekripsi_secret(site.secret_terenkripsi)
    ts, nonce = int(time.time()), uuid.uuid4().hex
    r = klien.post(PATH, content=body, headers={
        "Content-Type": "application/json",
        "X-Wpmgr-Site": str(site.id),
        "X-Wpmgr-Timestamp": str(ts),
        "X-Wpmgr-Nonce": nonce,
        "X-Wpmgr-Signature": sign(secret, "POST", PATH, ts, nonce, body),
    })
    assert r.status_code == 400
    assert r.json() == {"detail": "Body bukan objek JSON"}
    assert sesi.query(Job).filter_by(site_id=site.id).count() == 0
