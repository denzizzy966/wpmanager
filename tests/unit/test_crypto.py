import pytest
from cryptography.fernet import Fernet, InvalidToken

from wpmgr.crypto import dekripsi_secret, enkripsi_secret, secret_baru


@pytest.fixture(autouse=True)
def _kunci(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://a:b@localhost/c")
    monkeypatch.setenv("WPMGR_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("WPMGR_BASE_URL", "https://contoh.test")
    monkeypatch.setenv("WPMGR_SESSION_SECRET", "rahasia")
    from wpmgr.config import get_settings

    get_settings.cache_clear()


def test_bolak_balik():
    s = secret_baru()
    assert dekripsi_secret(enkripsi_secret(s)) == s


def test_ciphertext_tidak_memuat_plaintext():
    s = secret_baru()
    assert s.encode() not in enkripsi_secret(s)


def test_secret_baru_64_hex_dan_unik():
    a, b = secret_baru(), secret_baru()
    assert len(a) == 64 and a != b
    int(a, 16)


def test_ciphertext_rusak_ditolak():
    with pytest.raises(InvalidToken):
        dekripsi_secret(b"bukan-token-fernet")
