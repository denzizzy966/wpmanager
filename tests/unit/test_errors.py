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


def test_403_dengan_cf_ray_dan_plugin_json_adalah_auth_error():
    """CF-RAY header tidak menyiratkan firewall jika body dari plugin kita."""
    body = '{"code":"wpmgr_ditolak","message":"Tanda tangan tidak cocok."}'
    assert klasifikasi_respons(403, {"CF-RAY": "8a2f"}, body) == AUTH_ERROR


def test_403_dengan_cf_ray_dan_html_non_plugin_adalah_blocked():
    """CF-RAY header benar-benar firewall jika body bukan dari plugin."""
    assert klasifikasi_respons(403, {"CF-RAY": "8a2f"}, "<html>Access denied</html>") == BLOCKED


def test_401_dengan_cloudflare_di_body_plugin_adalah_auth_error():
    """Kata 'cloudflare' di pesan plugin tidak flip verdict jadi blocked."""
    body = '{"code":"wpmgr_ditolak","message":"Cloudflare sedang aktif di site ini."}'
    assert klasifikasi_respons(401, {}, body) == AUTH_ERROR


def test_403_dengan_json_non_plugin_tanpa_firewall_adalah_auth_error():
    """JSON valid tapi bukan kode plugin adalah auth_error, bukan blocked."""
    assert klasifikasi_respons(403, {}, '{"code":"rest_forbidden"}') == AUTH_ERROR


def test_429_adalah_transient():
    """429 Too Many Requests harus diulang."""
    assert klasifikasi_respons(429, {}, "Rate limited") == TRANSIENT


def test_302_adalah_bad_response():
    """Redirect (302) tanpa expected JSON adalah bad_response."""
    assert klasifikasi_respons(302, {"Location": "/login"}, "") == BAD_RESPONSE


def test_400_adalah_bad_response():
    """400 Bad Request tanpa expected JSON adalah bad_response."""
    assert klasifikasi_respons(400, {}, "Invalid request") == BAD_RESPONSE


def test_200_dengan_uppercase_html_adalah_bad_response():
    """Uppercase <HTML> tag juga harus dideteksi sebagai bad_response."""
    assert klasifikasi_respons(200, {}, "<HTML><body>oops</body></HTML>") == BAD_RESPONSE


def test_403_dengan_attention_required_dan_tanpa_plugin_code_adalah_blocked():
    """'Attention Required!' marker tanpa plugin code adalah blocked."""
    body = "<html><body>Attention Required! Please enable JavaScript and disable ad blockers.</body></html>"
    assert klasifikasi_respons(403, {}, body) == BLOCKED
