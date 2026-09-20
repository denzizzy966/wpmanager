from wpmgr.errors import (
    AUTH_ERROR,
    BAD_RESPONSE,
    BLOCKED,
    CONNECTOR_MISSING,
    DAPAT_DIULANG,
    TRANSIENT,
    SiteError,
    klasifikasi_respons,
)


def test_200_sehat():
    assert klasifikasi_respons(200, {}, '{"ok":true}') is None


def test_401_dari_plugin_adalah_auth_error():
    assert klasifikasi_respons(401, {}, '{"code":"wpmgr_bad_signature"}') == AUTH_ERROR


def test_403_polos_adalah_auth_error():
    assert klasifikasi_respons(403, {}, '{"code":"wpmgr_bad_signature"}') == AUTH_ERROR


def test_403_dengan_header_cloudflare_adalah_blocked():
    assert klasifikasi_respons(403, {"Server": "cloudflare"}, "denied") == BLOCKED


def test_403_dengan_cf_ray_adalah_blocked():
    assert klasifikasi_respons(403, {"CF-RAY": "8a2f"}, "") == BLOCKED


def test_403_menyebut_wordfence_adalah_blocked():
    body = "<html><body>Your access to this site has been limited by Wordfence</body></html>"
    assert klasifikasi_respons(403, {}, body) == BLOCKED


def test_header_tidak_peka_huruf_besar_kecil():
    assert klasifikasi_respons(403, {"server": "Cloudflare"}, "") == BLOCKED


def test_404_adalah_connector_missing():
    assert klasifikasi_respons(404, {}, "Not found") == CONNECTOR_MISSING


def test_500_adalah_transient():
    assert klasifikasi_respons(500, {}, "error") == TRANSIENT


def test_502_adalah_transient():
    assert klasifikasi_respons(502, {}, "") == TRANSIENT


def test_200_tapi_html_adalah_bad_response():
    assert klasifikasi_respons(200, {}, "<!DOCTYPE html><html>") == BAD_RESPONSE


def test_hanya_transient_dan_bad_response_yang_diulang():
    assert DAPAT_DIULANG == frozenset({TRANSIENT, BAD_RESPONSE})
    assert AUTH_ERROR not in DAPAT_DIULANG
    assert BLOCKED not in DAPAT_DIULANG


def test_site_error_membawa_sifat_dapat_diulang():
    assert SiteError(TRANSIENT, "gagal").dapat_diulang is True
    assert SiteError(AUTH_ERROR, "ditolak").dapat_diulang is False
