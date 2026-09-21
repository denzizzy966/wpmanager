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

# Dipakai untuk site tidak dikenal maupun tanda tangan salah, sehingga
# keduanya byte-identik. Membedakan keduanya berarti siapa pun yang dapat
# menjangkau endpoint ini punya oracle untuk menebak site_id mana yang
# terdaftar -- persis yang coba dicegah rate limit di bawah.
TOLAK = "Tidak sah"

BATAS_PER_MENIT = 10
_pembatas: dict[str, deque] = defaultdict(deque)

NONCE_TTL = 600
_nonce_terpakai: dict[str, float] = {}

_PEER_TEPERCAYA = frozenset({"127.0.0.1", "::1"})


def _ip_klien(request: Request) -> str:
    """IP klien sesungguhnya, dengan header proxy hanya dipercaya dari loopback.

    Membaca X-Real-IP tanpa syarat berarti siapa pun yang dapat menjangkau
    aplikasi secara langsung dapat mengarang identitas dan melewati pembatas
    laju. Mempercayainya hanya ketika peer TCP adalah loopback -- satu-satunya
    tempat nginx berada -- menutup itu.
    """
    peer = request.client.host if request.client else ""
    if peer in _PEER_TEPERCAYA:
        nyata = request.headers.get("X-Real-IP", "").strip()
        if nyata:
            return nyata
        diteruskan = request.headers.get("X-Forwarded-For", "")
        if diteruskan:
            # nginx menambahkan IP peer di posisi paling KANAN, sehingga entri
            # itulah yang tidak dapat dipalsukan klien.
            return diteruskan.split(",")[-1].strip()
    return peer or "tidak-diketahui"


def _lolos_rate_limit(ip: str) -> bool:
    sekarang = time.monotonic()

    # Buang IP yang jendelanya sudah lewat sebelum mengakses _pembatas[ip],
    # karena akses itu sendiri akan membuat entri baru (defaultdict). Tanpa
    # ini, setiap IP berbeda yang pernah mampir meninggalkan entri selamanya.
    basi = [k for k, v in _pembatas.items() if not v or sekarang - v[-1] > 60]
    for k in basi:
        del _pembatas[k]

    jejak = _pembatas[ip]
    while jejak and sekarang - jejak[0] > 60:
        jejak.popleft()
    if len(jejak) >= BATAS_PER_MENIT:
        return False
    jejak.append(sekarang)
    return True


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
