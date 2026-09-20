import pytest

from wpmgr.sso import (
    TokenTidakValid,
    _tanda_tangan,
    b64url_decode,
    b64url_encode,
    baca_token,
    buat_token,
)

SECRET = "c" * 64
SITE = "6f1a2b3c-0000-4000-8000-000000000001"


def test_b64url_tanpa_padding_dan_bolak_balik():
    data = b"abcde"
    s = b64url_encode(data)
    assert "=" not in s and "+" not in s and "/" not in s
    assert b64url_decode(s) == data


def test_token_berbentuk_body_titik_signature():
    t = buat_token(SECRET, SITE, now=1_000_000)
    body, _, sig = t.partition(".")
    assert body and sig and len(sig) == 64


def test_token_valid_terbaca_dan_memuat_site_id():
    t = buat_token(SECRET, SITE, now=1_000_000)
    p = baca_token(SECRET, t, now=1_000_030)
    assert p["site_id"] == SITE
    assert p["exp"] == 1_000_060
    assert len(p["nonce"]) == 32


def test_token_kedaluwarsa_ditolak():
    t = buat_token(SECRET, SITE, now=1_000_000)
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, t, now=1_000_061)


def test_token_dengan_secret_lain_ditolak():
    t = buat_token(SECRET, SITE, now=1_000_000)
    with pytest.raises(TokenTidakValid):
        baca_token("d" * 64, t, now=1_000_010)


def test_body_diubah_ditolak():
    t = buat_token(SECRET, SITE, now=1_000_000)
    _, _, sig = t.partition(".")
    rusak = b64url_encode(b'{"site_id":"lain","exp":9999999999,"nonce":"x"}')
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, f"{rusak}.{sig}", now=1_000_010)


def test_dua_token_punya_nonce_berbeda():
    a = baca_token(SECRET, buat_token(SECRET, SITE, now=1_000_000), now=1_000_001)
    b = baca_token(SECRET, buat_token(SECRET, SITE, now=1_000_000), now=1_000_001)
    assert a["nonce"] != b["nonce"]


def test_token_tanpa_titik_ditolak():
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, "tanpatitik", now=1_000_000)


def test_payload_bukan_dict_ditolak():
    """Validly-signed token whose payload decodes to non-dict raises TokenTidakValid."""
    body = b64url_encode(b"5")
    sig = _tanda_tangan(SECRET, body)
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, f"{body}.{sig}", now=1_000_000)


def test_exp_non_numeric_ditolak():
    """Validly-signed token whose exp is non-numeric string raises TokenTidakValid."""
    body = b64url_encode(b'{"site_id":"test","exp":"not_a_number","nonce":"' + b"0" * 32 + b'"}')
    sig = _tanda_tangan(SECRET, body)
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, f"{body}.{sig}", now=1_000_000)


def test_nonce_hilang_ditolak():
    """Validly-signed token with missing nonce raises TokenTidakValid."""
    body = b64url_encode(b'{"site_id":"test","exp":9999999999}')
    sig = _tanda_tangan(SECRET, body)
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, f"{body}.{sig}", now=1_000_000)


def test_nonce_tidak_hex_ditolak():
    """Validly-signed token with non-hex nonce raises TokenTidakValid."""
    body = b64url_encode(b'{"site_id":"test","exp":9999999999,"nonce":"' + b"z" * 32 + b'"}')
    sig = _tanda_tangan(SECRET, body)
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, f"{body}.{sig}", now=1_000_000)


def test_nonce_panjang_salah_ditolak():
    """Validly-signed token with wrong-length nonce raises TokenTidakValid."""
    body = b64url_encode(b'{"site_id":"test","exp":9999999999,"nonce":"' + b"0" * 31 + b'"}')
    sig = _tanda_tangan(SECRET, body)
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, f"{body}.{sig}", now=1_000_000)


def test_token_titik_awal_ditolak():
    """Token starting with dot raises TokenTidakValid."""
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, ".abc", now=1_000_000)


def test_token_titik_akhir_ditolak():
    """Token ending with dot raises TokenTidakValid."""
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, "abc.", now=1_000_000)


def test_token_titik_ganda_ditolak():
    """Token with extra dot raises TokenTidakValid."""
    t = buat_token(SECRET, SITE, now=1_000_000)
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, f"{t}.extra", now=1_000_000)


def test_signature_rusak_ditolak():
    """Token with one hex character of signature flipped raises TokenTidakValid."""
    t = buat_token(SECRET, SITE, now=1_000_000)
    body, _, sig = t.partition(".")
    # Flip first character of signature
    flipped_char = chr((int(sig[0], 16) + 1) % 16)
    rusak_sig = flipped_char + sig[1:]
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, f"{body}.{rusak_sig}", now=1_000_000)
