import json

import pytest

from wpmgr.errors import (
    AUTH_ERROR,
    BAD_RESPONSE,
    BLOCKED,
    CONNECTOR_MISSING,
    DAPAT_DIULANG,
    PACKAGE_MISSING,
    TRANSIENT,
    UPGRADE_FAILED,
    SiteError,
    klasifikasi_respons,
    pesan_plugin,
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


# --- R53: kode milik connector sendiri menentukan kelas ---------------------
# Connector membalas dengan WP_Error ber-`code` wpmgr_*; WordPress
# menyerialkannya sebagai {"code": ..., "message": ..., "data": {"status": N}}.


def _galat_connector(kode: str, pesan: str = "pesan dari connector", status: int = 0) -> str:
    return json.dumps({"code": kode, "message": pesan, "data": {"status": status}})


def test_404_wpmgr_tidak_ditemukan_adalah_package_missing():
    body = _galat_connector("wpmgr_tidak_ditemukan", "Paket tidak ditemukan di site ini.", 404)
    assert klasifikasi_respons(404, {}, body) == PACKAGE_MISSING


def test_404_rest_no_route_tetap_connector_missing():
    """Plugin nonaktif: WordPress sendiri yang menjawab, dengan kode `rest_*`."""
    body = '{"code":"rest_no_route","message":"No route was found","data":{"status":404}}'
    assert klasifikasi_respons(404, {}, body) == CONNECTOR_MISSING


def test_404_html_tetap_connector_missing():
    assert klasifikasi_respons(404, {}, "<html>Not Found</html>") == CONNECTOR_MISSING


def test_404_dengan_kode_wpmgr_lain_bukan_connector_missing():
    """Connector-nya jelas hidup (ia menjawab dengan kodenya sendiri), jadi
    menandai site needs_reconnect adalah diagnosis yang salah."""
    assert klasifikasi_respons(404, {}, _galat_connector("wpmgr_entah", status=404)) == BAD_RESPONSE


def test_409_wpmgr_tidak_ada_update_adalah_upgrade_failed():
    body = _galat_connector("wpmgr_tidak_ada_update", status=409)
    assert klasifikasi_respons(409, {}, body) == UPGRADE_FAILED


def test_409_wpmgr_sibuk_adalah_transient():
    assert klasifikasi_respons(409, {}, _galat_connector("wpmgr_sibuk", status=409)) == TRANSIENT


def test_409_tanpa_kode_wpmgr_tetap_bad_response():
    assert klasifikasi_respons(409, {}, "Conflict") == BAD_RESPONSE


def test_500_wpmgr_upgrade_gagal_adalah_upgrade_failed():
    body = _galat_connector("wpmgr_upgrade_gagal", "Could not copy file.", 500)
    assert klasifikasi_respons(500, {}, body) == UPGRADE_FAILED


def test_503_wpmgr_upgrade_gagal_juga_upgrade_failed():
    body = _galat_connector("wpmgr_upgrade_gagal", status=503)
    assert klasifikasi_respons(503, {}, body) == UPGRADE_FAILED


def test_500_json_bukan_wpmgr_tetap_transient():
    body = '{"code":"internal_server_error","message":"There has been a critical error."}'
    assert klasifikasi_respons(500, {}, body) == TRANSIENT


def test_401_dan_403_tidak_berubah_oleh_kode_connector():
    assert klasifikasi_respons(401, {}, _galat_connector("wpmgr_ditolak", status=401)) == AUTH_ERROR
    assert klasifikasi_respons(403, {"CF-RAY": "x"}, _galat_connector("wpmgr_ditolak")) == AUTH_ERROR


def test_package_missing_dan_upgrade_failed_tidak_diulang():
    assert PACKAGE_MISSING not in DAPAT_DIULANG
    assert UPGRADE_FAILED not in DAPAT_DIULANG


def test_pesan_plugin_mengambil_message_apa_adanya():
    """upgrade_failed mencatat pesan WordPress apa adanya (spec §9), bukan
    body JSON mentah -- yang meng-escape '/' menjadi '\\/', sehingga URL di
    pesan WordPress tidak lagi sama dengan aslinya."""
    body = json.dumps({"code": "wpmgr_upgrade_gagal",
                       "message": "Gagal unduh https://downloads.wordpress.org/x.zip"}).replace("/", "\\/")
    assert pesan_plugin(body) == "Gagal unduh https://downloads.wordpress.org/x.zip"


def test_pesan_plugin_none_untuk_body_bukan_connector():
    assert pesan_plugin("<html>boom</html>") is None
    assert pesan_plugin('{"code":"rest_no_route","message":"x"}') is None
    assert pesan_plugin('["bukan", "objek"]') is None


# --- Task 7: klasifikasi galat self-update ---------------------------------


def test_pasang_connector_gagal_menjadi_upgrade_failed():
    from wpmgr.errors import UPGRADE_FAILED, klasifikasi_respons

    body = '{"code":"wpmgr_pasang_gagal","message":"Could not create directory."}'
    assert klasifikasi_respons(500, {}, body) == UPGRADE_FAILED


def test_paket_connector_rusak_menjadi_bad_response():
    from wpmgr.errors import BAD_RESPONSE, klasifikasi_respons

    body = '{"code":"wpmgr_paket_rusak","message":"Hash paket tidak cocok dengan isinya."}'
    assert klasifikasi_respons(400, {}, body) == BAD_RESPONSE


def test_json_bersarang_dalam_tidak_meledak_saat_klasifikasi():
    dalam = "[" * 60000 + "]" * 60000
    assert klasifikasi_respons(200, {}, dalam) is None
    assert klasifikasi_respons(400, {}, dalam) == BAD_RESPONSE
    assert pesan_plugin(dalam) is None


@pytest.mark.parametrize("status,kode,kelas", [
    (403, "wpmgr_staging_mati", "staging_mati"),
    (413, "wpmgr_staging_terlalu_besar", "terlalu_besar"),
    (404, "wpmgr_staging_tidak_ada", "berkas_hilang"),
    (413, "wpmgr_staging_baris_terlalu_besar", "staging_gagal"),
    (409, "wpmgr_staging_ditahan", "staging_ditolak"),
    (409, "wpmgr_staging_sibuk", TRANSIENT),
    (409, "wpmgr_staging_direbut", "staging_gagal"),
    (400, "wpmgr_staging_path", "staging_gagal"),
    (400, "wpmgr_staging_permintaan", "staging_gagal"),
    (400, "wpmgr_staging_kursor", "staging_gagal"),
    (400, "wpmgr_staging_paket", "staging_gagal"),
    (400, "wpmgr_staging_sql", "staging_gagal"),
    (422, "wpmgr_staging_impor", "staging_gagal"),
    (422, "wpmgr_staging_rencana", "staging_gagal"),
    (422, "wpmgr_staging_verifikasi", "staging_gagal"),
    (422, "wpmgr_staging_hash", BAD_RESPONSE),
    (507, "wpmgr_staging_disk_penuh", "staging_gagal"),
    # Kode yang sama dengan status lain tidak ikut: 500 impor tetap sementara.
    (500, "wpmgr_staging_impor", TRANSIENT),
    (409, "wpmgr_staging_urutan", BAD_RESPONSE),
])
def test_tabel_kode_staging(status, kode, kelas):
    body = json.dumps({"code": kode, "message": "x", "data": {"status": status}})
    assert klasifikasi_respons(status, {}, body) == kelas


def test_kelas_staging_permanen_tidak_diulang():
    from wpmgr.errors import STAGING_DITOLAK, STAGING_GAGAL

    assert STAGING_GAGAL not in DAPAT_DIULANG and STAGING_DITOLAK not in DAPAT_DIULANG


def test_kode_staging_dipetakan_sebelum_cabang_status():
    from wpmgr.errors import (
        BERKAS_HILANG,
        KELAS_STAGING,
        STAGING_GAGAL,
        STAGING_MATI,
        TERLALU_BESAR,
    )

    def kode(k):
        return json.dumps({"code": k, "message": "x"})

    # 403 wpmgr_staging_mati bukan auth_error: site produksi tetap sehat.
    assert klasifikasi_respons(403, {}, kode("wpmgr_staging_mati")) == STAGING_MATI
    assert klasifikasi_respons(413, {}, kode("wpmgr_staging_terlalu_besar")) == TERLALU_BESAR
    assert klasifikasi_respons(404, {}, kode("wpmgr_staging_tidak_ada")) == BERKAS_HILANG
    # Satu baris tabel melebihi batas respons: mengulang selalu gagal sama.
    assert klasifikasi_respons(413, {}, kode("wpmgr_staging_baris_terlalu_besar")) == STAGING_GAGAL
    assert STAGING_GAGAL not in DAPAT_DIULANG
    assert KELAS_STAGING == {"staging_mati", "staging_ditolak", "staging_gagal", "terlalu_besar", "berkas_hilang"}
