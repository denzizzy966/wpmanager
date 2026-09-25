from typing import Annotated

from fastapi import APIRouter, Depends

from wpmgr import db
from wpmgr.kesehatan import susun_kesehatan
from wpmgr.models import User
from wpmgr.web.auth import pengguna_api

router = APIRouter()
PenggunaApi = Annotated[User, Depends(pengguna_api)]


@router.get("/api/kesehatan")
def kesehatan(pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        return susun_kesehatan(sesi)
