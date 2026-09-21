import json

import httpx
import pytest

from wpmgr.errors import (
    AUTH_ERROR,
    BLOCKED,
    CONNECTOR_MISSING,
    PACKAGE_MISSING,
    TRANSIENT,
    UNKNOWN,
    UPGRADE_FAILED,
    SiteError,
)
from wpmgr.signing import verify
from wpmgr.site_client import SiteClient

SECRET = "e" * 64
SITE_ID = "11111111-2222-4333-8444-555555555555"
BASE = "https://contoh.test"


def buat_klien(handler):
    return SiteClient(BASE, SITE_ID, SECRET, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_ping_mengembalikan_json():
    def handler(request):
        return httpx.Response(200, json={"connector_version": "1.0", "wp_version": "6.5"})

    assert buat_klien(handler).ping()["wp_version"] == "6.5"


def test_request_membawa_empat_header_dan_tanda_tangan_sah():
    ditangkap = {}

    def handler(request):
        ditangkap.update(request.headers)
        ditangkap["_body"] = request.content
        return httpx.Response(200, json={})

    buat_klien(handler).inventory()

    assert ditangkap["x-wpmgr-site"] == SITE_ID
    assert len(ditangkap["x-wpmgr-nonce"]) == 32
    assert verify(
        SECRET,
        ditangkap["x-wpmgr-signature"],
        "GET",
        "/wp-json/wpmgr/v1/inventory",
        int(ditangkap["x-wpmgr-timestamp"]),
        ditangkap["x-wpmgr-nonce"],
        ditangkap["_body"],
    )


def test_update_mengirim_body_json_dan_menandatanganinya():
    ditangkap = {}

    def handler(request):
        ditangkap["body"] = request.content
        ditangkap["sig"] = request.headers["x-wpmgr-signature"]
        ditangkap["ts"] = int(request.headers["x-wpmgr-timestamp"])
        ditangkap["nonce"] = request.headers["x-wpmgr-nonce"]
        return httpx.Response(200, json={"ok": True, "versi_sesudah": "3.20.1"})

    hasil = buat_klien(handler).update("plugin", "elementor/elementor.php", "3.20.1")

    assert hasil["versi_sesudah"] == "3.20.1"
    assert json.loads(ditangkap["body"])["ke_versi"] == "3.20.1"
    assert verify(SECRET, ditangkap["sig"], "POST", "/wp-json/wpmgr/v1/update",
                  ditangkap["ts"], ditangkap["nonce"], ditangkap["body"])


def test_nonce_berbeda_tiap_request():
    nonces = []

    def handler(request):
        nonces.append(request.headers["x-wpmgr-nonce"])
        return httpx.Response(200, json={})

    k = buat_klien(handler)
    k.ping()
    k.ping()
    assert nonces[0] != nonces[1]


@pytest.mark.parametrize(
    "status,headers,body,diharapkan",
    [
        (401, {}, '{"code":"x"}', AUTH_ERROR),
        (403, {"Server": "cloudflare"}, "denied", BLOCKED),
        (404, {}, "not found", CONNECTOR_MISSING),
        (500, {}, "boom", TRANSIENT),
    ],
)
def test_status_error_menjadi_site_error(status, headers, body, diharapkan):
    def handler(request):
        return httpx.Response(status, headers=headers, content=body)

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).ping()
    assert exc.value.error_class == diharapkan


def test_timeout_menjadi_unknown():
    def handler(request):
        raise httpx.ReadTimeout("kehabisan waktu", request=request)

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).update("plugin", "a/a.php", "1.0")
    assert exc.value.error_class == UNKNOWN


def _panggil(klien, metode):
    if metode == "update":
        return klien.update("plugin", "a/a.php", "1.0")
    return getattr(klien, metode)()


@pytest.mark.parametrize(
    "metode,pengecualian,diharapkan",
    [
        # R55: koneksi yang tidak pernah terbentuk tidak menjalankan apa pun di
        # site, jadi aman diulang -- untuk panggilan apa pun.
        ("ping", httpx.ConnectTimeout, TRANSIENT),
        ("ping", httpx.PoolTimeout, TRANSIENT),
        ("inventory", httpx.ConnectTimeout, TRANSIENT),
        ("inventory", httpx.PoolTimeout, TRANSIENT),
        ("update", httpx.ConnectTimeout, TRANSIENT),
        ("update", httpx.PoolTimeout, TRANSIENT),
        # ping dan inventory read-only: timeout jenis apa pun aman diulang.
        ("ping", httpx.ReadTimeout, TRANSIENT),
        ("ping", httpx.WriteTimeout, TRANSIENT),
        ("inventory", httpx.ReadTimeout, TRANSIENT),
        ("inventory", httpx.WriteTimeout, TRANSIENT),
        # Hanya pada update, request mungkin sudah sampai dan upgrade mungkin
        # sedang berjalan: kita tidak tahu, jadi unknown (spec §7.4).
        ("update", httpx.ReadTimeout, UNKNOWN),
        ("update", httpx.WriteTimeout, UNKNOWN),
    ],
)
def test_klasifikasi_timeout_per_panggilan(metode, pengecualian, diharapkan):
    def handler(request):
        raise pengecualian("kehabisan waktu", request=request)

    with pytest.raises(SiteError) as exc:
        _panggil(buat_klien(handler), metode)
    assert exc.value.error_class == diharapkan


def test_upgrade_failed_mencatat_pesan_wordpress_apa_adanya():
    def handler(request):
        return httpx.Response(500, json={
            "code": "wpmgr_upgrade_gagal",
            "message": "Download failed. https://downloads.wordpress.org/x.zip",
            "data": {"status": 500},
        })

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).update("plugin", "a/a.php", "1.0")
    assert exc.value.error_class == UPGRADE_FAILED
    assert exc.value.pesan == "Download failed. https://downloads.wordpress.org/x.zip"


def test_package_missing_dari_404_connector():
    def handler(request):
        return httpx.Response(404, json={
            "code": "wpmgr_tidak_ditemukan",
            "message": "Paket tidak ditemukan di site ini.",
            "data": {"status": 404},
        })

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).update("plugin", "a/a.php", "1.0")
    assert exc.value.error_class == PACKAGE_MISSING


def test_koneksi_gagal_menjadi_transient():
    def handler(request):
        raise httpx.ConnectError("tidak dapat terhubung", request=request)

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).ping()
    assert exc.value.error_class == TRANSIENT


def test_respons_bukan_json_menjadi_bad_response():
    def handler(request):
        return httpx.Response(200, content="<!DOCTYPE html><html>halo</html>")

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).ping()
    assert exc.value.error_class == "bad_response"


def test_warning_php_sebelum_json_menjadi_bad_response():
    """Kegagalan rutin di shared hosting: plugin lain mencetak warning sebelum
    body JSON. Body ini bukan HTML, sehingga klasifikasi_respons meloloskannya
    sebagai sehat — yang menangkapnya adalah json.loads di dalam SiteClient."""

    def handler(request):
        return httpx.Response(
            200,
            content='<br />\n<b>Warning</b>: fopen(): failed in /x.php on line 3<br />\n{"ok":true}',
        )

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).ping()
    assert exc.value.error_class == "bad_response"


def test_url_http_ditolak_saat_konstruksi():
    with pytest.raises(ValueError):
        SiteClient("http://tidak-aman.test", SITE_ID, SECRET)
