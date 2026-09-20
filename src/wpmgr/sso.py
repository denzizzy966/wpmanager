import base64
import hashlib
import hmac
import json
import time

from wpmgr.signing import new_nonce

UMUR_DEFAULT_DETIK = 60


class TokenTidakValid(Exception):
    pass


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _tanda_tangan(secret_hex: str, body: str) -> str:
    return hmac.new(
        secret_hex.encode("ascii"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()


def buat_token(
    secret_hex: str, site_id: str, umur_detik: int = UMUR_DEFAULT_DETIK, now: int | None = None
) -> str:
    sekarang = int(time.time()) if now is None else now
    payload = {"site_id": site_id, "exp": sekarang + umur_detik, "nonce": new_nonce()}
    body = b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return f"{body}.{_tanda_tangan(secret_hex, body)}"


def baca_token(secret_hex: str, token: str, now: int | None = None) -> dict:
    body, pemisah, sig = token.partition(".")
    if not pemisah or not body or not sig:
        raise TokenTidakValid("bentuk token salah")
    if not hmac.compare_digest(_tanda_tangan(secret_hex, body), sig):
        raise TokenTidakValid("tanda tangan salah")
    try:
        payload = json.loads(b64url_decode(body))
    except Exception as exc:
        raise TokenTidakValid("payload tidak dapat dibaca") from exc
    sekarang = int(time.time()) if now is None else now
    if sekarang > int(payload.get("exp", 0)):
        raise TokenTidakValid("token kedaluwarsa")
    return payload
