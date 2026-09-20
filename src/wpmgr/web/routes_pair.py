import time
import uuid
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request

from wpmgr import db
from wpmgr.crypto import dekripsi_secret
from wpmgr.jobs.queue import buat_job
from wpmgr.models import ActivityLog, Job, JobStatus, JobType, Site
from wpmgr.signing import JENDELA_DETIK, verify

router = APIRouter()
PATH = "/api/pair/confirm"

BATAS_PER_MENIT = 10
_pembatas: dict[str, deque] = defaultdict(deque)


def _lolos_rate_limit(ip: str) -> bool:
    sekarang = time.monotonic()
    jejak = _pembatas[ip]
    while jejak and sekarang - jejak[0] > 60:
        jejak.popleft()
    if len(jejak) >= BATAS_PER_MENIT:
        return False
    jejak.append(sekarang)
    return True


@router.post(PATH)
async def konfirmasi_pairing(request: Request):
    ip = request.client.host if request.client else "tidak-diketahui"
    if not _lolos_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Terlalu banyak percobaan")

    body = await request.body()
    h = request.headers

    try:
        site_id = uuid.UUID(h.get("X-Wpmgr-Site", ""))
        timestamp = int(h.get("X-Wpmgr-Timestamp", "0"))
    except ValueError:
        raise HTTPException(status_code=401, detail="Header tidak valid") from None

    nonce = h.get("X-Wpmgr-Nonce", "")
    signature = h.get("X-Wpmgr-Signature", "")

    if abs(int(time.time()) - timestamp) > JENDELA_DETIK:
        raise HTTPException(status_code=401, detail="Timestamp di luar jendela")

    with db.SessionLocal() as sesi:
        site = sesi.get(Site, site_id)
        if site is None:
            raise HTTPException(status_code=401, detail="Site tidak dikenal")

        secret = dekripsi_secret(site.secret_terenkripsi)
        if not verify(secret, signature, "POST", PATH, timestamp, nonce, body):
            raise HTTPException(status_code=401, detail="Tanda tangan tidak cocok")

        muatan = await request.json() if body else {}
        site.connector_version = muatan.get("connector_version")
        site.wp_version = muatan.get("wp_version")
        site.php_version = muatan.get("php_version")
        sesi.add(
            ActivityLog(
                site_id=site.id,
                level="info",
                pesan="Connector mengonfirmasi pairing",
                detail={"connector_version": site.connector_version},
            )
        )
        sesi.commit()

        tertunda = (
            sesi.query(Job)
            .filter(
                Job.site_id == site.id,
                Job.tipe == JobType.verify_site,
                Job.status.in_([JobStatus.pending, JobStatus.running]),
            )
            .count()
        )
        if tertunda == 0:
            buat_job(sesi, site.id, JobType.verify_site)

    return {"ok": True}
