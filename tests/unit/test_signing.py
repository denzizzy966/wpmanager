import hashlib
import json
from pathlib import Path

import pytest

from wpmgr.signing import canonical_string, new_nonce, sign, verify

SECRET = "a" * 64


def test_canonical_string_bentuk_persis():
    hasil = canonical_string("get", "/wp-json/wpmgr/v1/ping", 1758387600, "deadbeef", b"")
    kosong = hashlib.sha256(b"").hexdigest()
    assert hasil == f"GET\n/wp-json/wpmgr/v1/ping\n1758387600\ndeadbeef\n{kosong}"


def test_canonical_string_tidak_berakhir_newline():
    hasil = canonical_string("POST", "/x", 1, "n", b"{}")
    assert not hasil.endswith("\n")
    assert hasil.count("\n") == 4


def test_canonical_string_membesarkan_method():
    assert canonical_string("post", "/x", 1, "n", b"").startswith("POST\n")


def test_body_berbeda_menghasilkan_canonical_berbeda():
    a = canonical_string("POST", "/x", 1, "n", b'{"a":1}')
    b = canonical_string("POST", "/x", 1, "n", b'{"a":2}')
    assert a != b


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/hmac-test-vectors.json"


def muat_vectors():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("v", muat_vectors(), ids=lambda v: v["nama"])
def test_vector_canonical_cocok(v):
    body = v["body"].encode("utf-8")
    assert canonical_string(v["method"], v["path"], v["timestamp"], v["nonce"], body) == v["canonical"]


@pytest.mark.parametrize("v", muat_vectors(), ids=lambda v: v["nama"])
def test_vector_signature_cocok(v):
    body = v["body"].encode("utf-8")
    assert sign(v["secret_hex"], v["method"], v["path"], v["timestamp"], v["nonce"], body) == v["signature"]


def test_verify_menolak_tanda_tangan_salah():
    assert verify(SECRET, "0" * 64, "GET", "/x", 1, "n", b"") is False


def test_verify_menerima_tanda_tangan_benar():
    s = sign(SECRET, "GET", "/x", 1, "n", b"")
    assert verify(SECRET, s, "GET", "/x", 1, "n", b"") is True


def test_nonce_unik_dan_32_hex():
    a, b = new_nonce(), new_nonce()
    assert a != b
    assert len(a) == 32 and int(a, 16) >= 0
