import secrets

from cryptography.fernet import Fernet

from wpmgr.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().secret_key.encode())


def secret_baru() -> str:
    return secrets.token_hex(32)


def enkripsi_secret(secret_hex: str) -> bytes:
    return _fernet().encrypt(secret_hex.encode("ascii"))


def dekripsi_secret(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode("ascii")
