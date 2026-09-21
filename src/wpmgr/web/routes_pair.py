import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from wpmgr import db
from wpmgr.crypto import dekripsi_secret
from wpmgr.jobs.queue import buat_job
from wpmgr.models import ActivityLog, Job, JobStatus, JobType, Site
from wpmgr.signing import JENDELA_DETIK, verify
from wpmgr.web.pembatas import PembatasLaju, ip_klien

router = APIRouter()
PATH = "/api/pair/confirm"

# Dipakai untuk site tidak dikenal maupun tanda tangan salah, sehingga
# keduanya byte-identik. Membedakan keduanya berarti siapa pun yang dapat
# menjangkau endpoint ini punya oracle untuk menebak site_id mana yang
# terdaftar -- persis yang coba dicegah rate limit di bawah.
TOLAK = "Tidak sah"

BATAS_PER_MENIT = 10
_pembatas_pairing = PembatasLaju(BATAS_PER_MENIT)
# Nama-nama lama tetap diekspor: test (dan siapa pun yang mengosongkan
# pembatas di antara test) memegang dict jejaknya secara langsung.
_pembatas = _pembatas_pairing.jejak
_lolos_rate_limit = _pembatas_pairing.lolos
_ip_klien = ip_klien

NONCE_TTL = 600
_nonce_terpakai: dict[str, float] = {}


def _nonce_baru(nonce: str) -> bool:
    sekarang = time.monotonic()
    for lama in [n for n, t in _nonce_terpakai.items() if sekarang - t > NONCE_TTL]:
        del _nonce_terpakai[lama]
    if nonce in _nonce_terpakai:
        return False
    _nonce_terpakai[nonce] = sekarang
    return True


@router.post(PATH)
async def konfirmasi_pairing(request: Request):
    if not _lolos_rate_limit(_ip_klien(request)):
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
            raise HTTPException(status_code=401, detail=TOLAK)

        secret = dekripsi_secret(site.secret_terenkripsi)
        if not verify(secret, signature, "POST", PATH, timestamp, nonce, body):
            raise HTTPException(status_code=401, detail=TOLAK)

        # Nonce dicek setelah tanda tangan terverifikasi, bukan sebelumnya:
        # mencatat lebih dulu akan membiarkan siapa pun membakar nonce
        # sembarangan lewat request tak bertanda tangan, sehingga percobaan
        # ulang yang sah ditolak seolah replay.
        if not _nonce_baru(nonce):
            raise HTTPException(status_code=401, detail="Nonce sudah dipakai")

        try:
            muatan = await request.json() if body else {}
        except ValueError:
            raise HTTPException(status_code=400, detail="Body bukan JSON") from None
        if not isinstance(muatan, dict):
            # JSON sah tetapi berupa array/angka/null: tanpa ini muatan.get()
            # di bawah meledak sebagai 500.
            raise HTTPException(status_code=400, detail="Body bukan objek JSON")

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
