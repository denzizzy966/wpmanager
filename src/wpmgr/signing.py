import hashlib
import hmac
import secrets

JENDELA_DETIK = 300


def canonical_string(
    method: str, path: str, timestamp: int, nonce: str, body: bytes
) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join([method.upper(), path, str(timestamp), nonce, body_hash])


def sign(
    secret_hex: str, method: str, path: str, timestamp: int, nonce: str, body: bytes
) -> str:
    canonical = canonical_string(method, path, timestamp, nonce, body)
    return hmac.new(
        secret_hex.encode("ascii"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify(
    secret_hex: str,
    signature: str,
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
    body: bytes,
) -> bool:
    diharapkan = sign(secret_hex, method, path, timestamp, nonce, body)
    return hmac.compare_digest(diharapkan, signature)


def new_nonce() -> str:
    return secrets.token_hex(16)
