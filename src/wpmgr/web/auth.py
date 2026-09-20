import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request
from sqlalchemy import select

from wpmgr import db
from wpmgr.models import User

ph = PasswordHasher()
KUNCI_SESI = "user_id"


class ButuhLogin(Exception):
    """Dilempar pada route halaman; ditangkap handler yang mengarahkan ke /login."""


def periksa_sandi(email: str, password: str) -> User | None:
    with db.SessionLocal() as sesi:
        pengguna = sesi.scalar(select(User).where(User.email == email))
        if pengguna is None:
            ph.hash(password)  # samakan waktu respons agar email tak dapat ditebak
            return None
        try:
            ph.verify(pengguna.password_hash, password)
        except VerifyMismatchError:
            return None
        return pengguna


def _ambil(request: Request) -> User | None:
    raw = request.session.get(KUNCI_SESI)
    if not raw:
        return None
    with db.SessionLocal() as sesi:
        return sesi.get(User, uuid.UUID(raw))


def pengguna_saat_ini(request: Request) -> User:
    pengguna = _ambil(request)
    if pengguna is None:
        raise ButuhLogin()
    return pengguna


def pengguna_api(request: Request) -> User:
    pengguna = _ambil(request)
    if pengguna is None:
        raise HTTPException(status_code=401, detail="Belum masuk")
    return pengguna
