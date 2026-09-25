import time
import uuid

import httpx
import pytest

from wpmgr.crypto import dekripsi_secret

from .conftest import permintaan_bertanda, wpcli

pytestmark = pytest.mark.e2e


def _kosongkan_traffic() -> None:
    wpcli("db", "query", "TRUNCATE TABLE wp_wpmgr_traffic")
    wpcli("db", "query", "TRUNCATE TABLE wp_wpmgr_pengunjung")


def test_hit_publik_selalu_204_tanpa_tanda_tangan(wp_site):
    # /hit satu-satunya route tanpa HMAC: browser pengunjung memanggilnya
    # langsung, tanpa header X-Wpmgr-*.
    r = httpx.post(
        f"{wp_site}/wp-json/wpmgr/v1/hit",
        content=b'{"p":"/","r":""}',
        headers={"Content-Type": "text/plain"},
        timeout=30,
    )
    assert r.status_code == 204
    assert r.content == b""


def test_hit_body_rusak_tetap_204(wp_site):
    # Tak pernah memberi sinyal apakah hit-nya dihitung -- body yang sama
    # sekali bukan JSON pun dibalas 204, bukan 400.
    r = httpx.post(
        f"{wp_site}/wp-json/wpmgr/v1/hit",
        content=b"bukan json sama sekali",
        headers={"Content-Type": "text/plain"},
        timeout=30,
    )
    assert r.status_code == 204


def test_traffic_butuh_tanda_tangan(site_terpasang):
    r = httpx.get(f"{site_terpasang.url}/wp-json/wpmgr/v1/traffic", timeout=30)
    assert r.status_code == 401


def test_hit_tercatat_dan_muncul_di_traffic(site_terpasang):
    _kosongkan_traffic()
    path = f"/uji-traffic-{uuid.uuid4().hex[:8]}"
    body = f'{{"p":"{path}","r":""}}'.encode()
    r = httpx.post(
        f"{site_terpasang.url}/wp-json/wpmgr/v1/hit",
        content=body,
        headers={
            "Content-Type": "text/plain",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0",
        },
        timeout=30,
    )
    assert r.status_code == 204

    secret = dekripsi_secret(site_terpasang.secret_terenkripsi)
    batas = time.time() + 20
    hari_ditemukan = None
    while time.time() < batas:
        resp = permintaan_bertanda(site_terpasang, secret, "GET", "/wpmgr/v1/traffic")
        assert resp.status_code == 200
        data = resp.json()
        for hari in data["hari"]:
            if path in hari.get("halaman", {}):
                hari_ditemukan = hari
                break
        if hari_ditemukan:
            break
        time.sleep(1)

    assert hari_ditemukan is not None, "hit tidak muncul di /traffic dalam 20 detik"
    assert hari_ditemukan["halaman"][path] == 1
    assert hari_ditemukan["total"]["kunjungan"] >= 1
    assert hari_ditemukan["perangkat"].get("desktop", 0) >= 1


def test_hit_dari_bot_tidak_dihitung(site_terpasang):
    _kosongkan_traffic()
    path_bot = f"/uji-bot-{uuid.uuid4().hex[:8]}"
    r = httpx.post(
        f"{site_terpasang.url}/wp-json/wpmgr/v1/hit",
        content=f'{{"p":"{path_bot}","r":""}}'.encode(),
        headers={"Content-Type": "text/plain", "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"},
        timeout=30,
    )
    assert r.status_code == 204

    # Bukan fixed sleep: hit kontrol (bukan bot) dikirim SESUDAH hit bot di
    # atas dan ditunggu lewat polling sampai muncul di /traffic (pola yang
    # sama seperti test_hit_tercatat_dan_muncul_di_traffic, lihat koreksi
    # #10 konteks-global -- poll, bukan sleep tetap). /hit memproses secara
    # sinkron (bukan job async), jadi begitu hit kontrol ini kelihatan,
    # hit bot yang dikirim lebih dulu SUDAH PASTI juga selesai diproses --
    # kalaupun ia (keliru) ditulis, ia pasti sudah kelihatan juga saat ini.
    path_kontrol = f"/uji-kontrol-{uuid.uuid4().hex[:8]}"
    r = httpx.post(
        f"{site_terpasang.url}/wp-json/wpmgr/v1/hit",
        content=f'{{"p":"{path_kontrol}","r":""}}'.encode(),
        headers={
            "Content-Type": "text/plain",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0",
        },
        timeout=30,
    )
    assert r.status_code == 204

    secret = dekripsi_secret(site_terpasang.secret_terenkripsi)
    batas = time.time() + 20
    data = None
    while time.time() < batas:
        resp = permintaan_bertanda(site_terpasang, secret, "GET", "/wpmgr/v1/traffic")
        assert resp.status_code == 200
        data = resp.json()
        if any(path_kontrol in hari.get("halaman", {}) for hari in data["hari"]):
            break
        time.sleep(1)
    else:
        pytest.fail("hit kontrol tidak muncul di /traffic dalam 20 detik")

    for hari in data["hari"]:
        assert path_bot not in hari.get("halaman", {})


def test_footer_menyisipkan_script_beacon(site_terpasang):
    r = httpx.get(site_terpasang.url, timeout=30)
    assert r.status_code == 200
    assert "sendBeacon" in r.text
    # wp_json_encode() meloloskan "/" jadi "\/" -- perilaku bawaan json_encode().
    assert "wpmgr\\/v1\\/hit" in r.text
