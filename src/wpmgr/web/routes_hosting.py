"""JSON API hosting VPS (spec Lapis 4 §11).

Kunci baris: route yang mengubah status atau mengantrekan job mengikuti
kontrak kunci cron (`hosting.umum.kunci_hosting`): baris `sites` FOR NO KEY
UPDATE, lalu `hosting_vps` FOR UPDATE, dipegang sampai commit. Cek DNS
(jaringan, sampai 10 detik) selalu di luar kunci, dan keadaan diperiksa
ulang sesudah kunci diambil. GET tidak mengunci apa pun (tidak menulis),
jadi tidak pernah menunggu cron atau route lain. Semua `detail` galat adalah
teks tetap: tanpa stderr skrip, path, nilai DNS mentah, atau `str(exc)`.
"""

import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from wpmgr import db
from wpmgr.config import Settings, get_settings
from wpmgr.fitur import STAGING, punya_fitur
from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting import pindah
from wpmgr.hosting import umum as hu
from wpmgr.hosting.umum import kunci_hosting
from wpmgr.models import (
    JOB_HOSTING,
    HostingBackup,
    HostingVps,
    Job,
    JobStatus,
    JobType,
    Site,
    Staging,
    StatusHosting,
    User,
)
from wpmgr.staging import umum as stg
from wpmgr.staging.aman import domain_sah, host_dari_url, nama_prod_dari_url
from wpmgr.staging.cron import _dir_nyata, _hapus_nisan, _ke_nisan, _kunci_site
from wpmgr.staging.pembantu import (
    PENGGUNA_PRATINJAU,
    GalatPembantu,
    hapus_htpasswd_pratinjau,
    hash_sandi,
    sandi_baru,
    tulis_htpasswd_pratinjau,
)
from wpmgr.staging.rencana import format_byte
from wpmgr.web.auth import pengguna_api
from wpmgr.web.routes_staging import ringkas_kemajuan

__all__ = ["kunci_hosting", "router"]

log = logging.getLogger("wpmgr.web.routes_hosting")
router = APIRouter()
PenggunaApi = Annotated[User, Depends(pengguna_api)]

BATAS_DAFTAR = 50
TEKS_STATUS = {"menyalin": "Menyalin ke VPS", "pratinjau": "Pratinjau", "menunggu_dns": "Menunggu DNS",
               "mengaktifkan": "Mengaktifkan", "aktif": "Dihosting di VPS", "gagal": "Gagal"}
PESAN_BELUM_ADA = "Pindah hosting untuk site ini belum dimulai."
PESAN_SUDAH_ADA = "Pindah hosting untuk site ini sudah dimulai."
PESAN_IZIN = pindah.PESAN_IZIN
PESAN_DOMAIN = ("Domain site ini tidak dapat dipindahkan: URL harus https:// dengan nama domain biasa, "
                "bukan di bawah domain staging.")
PESAN_IP_LAMA = ("Alamat IP hosting lama tidak dapat ditentukan dari DNS publik (domain sudah menunjuk VPS, "
                 "atau DNS tidak menjawab).")
PESAN_CDN_IP_LAMA = dns_mod.PESAN_CDN_IP_LAMA
# ada_www None: tidak ada resolver yang menjawab. Menganggapnya "tanpa www"
# bisa mengaktifkan situs sementara pengunjung www masih ke hosting lama.
PESAN_WWW_DNS = ("Keberadaan www untuk domain ini tidak dapat dipastikan karena DNS publik tidak menjawab; "
                 "coba lagi beberapa menit lagi.")
PESAN_SITE_BERUBAH = "Alamat site berubah selagi DNS diperiksa; coba lagi."
PESAN_BENTROK = "Nama atau domain situs bentrok dengan pindah hosting lain; coba lagi."
PESAN_SIBUK = "Tunggu pekerjaan hosting yang sedang berjalan selesai."
PESAN_SIBUK_SIMPAN = "Masih ada pekerjaan hosting yang tertunda atau berjalan untuk site ini."
# Skrip pembantu keluar 3 (tanpa ubah) pada panggilan sinkron route: kunci
# router/nginx dipegang proses lain (impor database bisa sampai 3 jam).
PESAN_SERVER_SIBUK = "Server sedang sibuk dengan proses lain; tidak ada yang diubah. Coba lagi beberapa menit lagi."
PESAN_SUDAH_DILAYANI = pindah.PESAN_SUDAH_DILAYANI
PESAN_STATUS_TARIK = pindah.PESAN_STATUS_TARIK
PESAN_STATUS_AKTIFKAN = pindah.PESAN_STATUS_AKTIFKAN
PESAN_PERIKSA_ULANG = "Situs sudah dilayani VPS; periksa ulang hanya bisa dari status gagal."
PESAN_LANJUT_DNS = "Lanjut ke DNS hanya dari status pratinjau."
PESAN_KEMBALI = "Kembali ke pratinjau hanya dari status menunggu DNS."
PESAN_KONFIRMASI_DOMAIN = "Ketik domain persis untuk konfirmasi."
PESAN_BACKOFF = "Sertifikat domain baru saja gagal diterbitkan; tunggu jeda sebelum mencoba lagi."
PESAN_SANDI_DILAYANI = "Kata sandi pratinjau tidak berlaku lagi; situs sudah dilayani VPS."
PESAN_TANPA_JOB = "Tidak ada pekerjaan hosting yang bisa dibatalkan."
PESAN_BATAL_BACKUP = "Backup tidak bisa dibatalkan."
PESAN_BATAL_SESUDAH_TUKAR = "Aktivasi sudah mengubah VPS dan tidak bisa dibatalkan lagi."
PESAN_HAPUS_DILAYANI = "Site ini dihosting di VPS; lepas manual (README)."


def _iso(nilai):
    return nilai.isoformat() if nilai else None


def fitur_hosting() -> Settings:
    s = get_settings()
    if not s.hosting_aktif:
        raise HTTPException(status_code=404, detail="Fitur hosting VPS tidak aktif")
    return s


def site_atau_404(sesi, site_id: uuid.UUID) -> Site:
    site = sesi.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site tidak ditemukan")
    return site


def _hosting_atau_409(sesi, site_id: uuid.UUID) -> HostingVps:
    h = kunci_hosting(sesi, site_id)
    if h is None:
        raise HTTPException(status_code=409, detail=PESAN_BELUM_ADA)
    return h


def job_aktif_hosting(sesi, site_id) -> Job | None:
    return sesi.scalar(select(Job).where(
        Job.site_id == site_id, Job.tipe.in_(JOB_HOSTING),
        Job.status.in_((JobStatus.pending, JobStatus.running))).order_by(Job.id.desc()).limit(1))


def tolak_bila_sibuk(sesi, site_id) -> None:
    if job_aktif_hosting(sesi, site_id) is not None:
        raise HTTPException(status_code=409, detail=PESAN_SIBUK)


def simpan_job_hosting(sesi, job: Job) -> Job:
    """Sisipkan job dalam satu commit (yang juga melepas kunci); `uq_jobs_hosting_aktif` penjaga terakhir."""
    sesi.add(job)
    try:
        sesi.commit()
    except IntegrityError:
        sesi.rollback()
        raise HTTPException(status_code=409, detail=PESAN_SIBUK_SIMPAN) from None
    return job


def galat_pembantu_http(exc: GalatPembantu) -> HTTPException:
    """GalatPembantu dari panggilan sinkron route ke HTTP; `pesan` selalu teks tetap (F20).

    Keluar 3 (`tanpa_ubah`) berarti skrip tidak mengubah apa pun: 409, bukan
    502. Kunci router/nginx yang sibuk (`sibuk`) mendapat pesan "server
    sedang sibuk"; penolakan prasyarat lain memakai teks tetap pembantu.
    """
    if exc.tanpa_ubah:
        return HTTPException(status_code=409, detail=PESAN_SERVER_SIBUK if exc.sibuk else exc.pesan)
    return HTTPException(status_code=502, detail=exc.pesan)


def dict_hosting(h: HostingVps, s: Settings) -> dict:
    hasil = {
        "nama": h.nama, "status": h.status.value, "status_teks": TEKS_STATUS[h.status.value], "domain": h.domain,
        "dengan_www": h.dengan_www, "url_pratinjau": hu.url_pratinjau(h), "pengguna": PENGGUNA_PRATINJAU,
        "versi_php": h.versi_php, "ukuran_file_teks": format_byte(h.ukuran_file),
        "ukuran_db_teks": format_byte(h.ukuran_db), "ditarik_pada": _iso(h.ditarik_pada),
        "pratinjau_sertifikat_pada": _iso(h.pratinjau_sertifikat_pada), "dns_dicek_pada": _iso(h.dns_dicek_pada),
        "sertifikat_pada": _iso(h.sertifikat_pada), "sertifikat_gagal_pada": _iso(h.sertifikat_gagal_pada),
        "dilayani_vps_pada": _iso(h.dilayani_vps_pada), "aktif_pada": _iso(h.aktif_pada),
        "backup_terakhir_pada": _iso(h.backup_terakhir_pada), "backup_gagal_pada": _iso(h.backup_gagal_pada),
        "galat": h.galat, "batal_diminta": h.batal_diminta_pada is not None,
        "dns_hasil": h.dns_hasil, "instruksi": dns_mod.instruksi(h, s.hosting_ipv4, s.hosting_ipv6),
        "coba_lagi_pada": _iso(dns_mod.coba_lagi_pada(h)),
    }
    # Asal hanya bermakna untuk status gagal.
    if h.status == StatusHosting.gagal:
        hasil["gagal_asal"] = h.gagal_asal
    return hasil


def daftar_backup(sesi, site_id) -> list[dict]:
    return [{"id": b.id, "stempel": b.stempel, "status": b.status, "manual": b.manual, "tujuan": b.tujuan,
             "ukuran_db_teks": format_byte(b.ukuran_db), "ukuran_file_teks": format_byte(b.ukuran_file),
             "dibuat_pada": _iso(b.dibuat_pada)}
            for b in sesi.scalars(select(HostingBackup).where(HostingBackup.site_id == site_id)
                                  .order_by(HostingBackup.stempel.desc(), HostingBackup.id.desc())
                                  .limit(BATAS_DAFTAR))]


def _nama_unik(sesi, dasar: str) -> str:
    """Nama situs bebas di hosting_vps dan `vps-<nama>` bukan nama staging mana pun (spec §10.7)."""
    kandidat = dasar
    for i in range(2, 100):
        bebas_hosting = sesi.scalar(select(HostingVps.id).where(HostingVps.nama == kandidat)) is None
        bebas_staging = sesi.scalar(select(Staging.id).where(Staging.nama == f"vps-{kandidat}")) is None
        if bebas_hosting and bebas_staging:
            return kandidat
        akhiran = f"-{i}"
        kandidat = dasar[:36 - len(akhiran)].rstrip("-") + akhiran
    raise HTTPException(status_code=409, detail="Nama situs untuk site ini tidak dapat ditentukan.")


# ---- status ------------------------------------------------------------------------------


@router.get("/api/sites/{site_id}/hosting")
def status_hosting(site_id: uuid.UUID, pengguna: PenggunaApi):
    s = get_settings()
    if not s.hosting_aktif:
        return {"aktif_fitur": False}
    with db.SessionLocal() as sesi:
        site = site_atau_404(sesi, site_id)
        # Hanya membaca: tanpa kunci baris, jadi tidak pernah menunggu.
        h = sesi.scalar(select(HostingVps).where(HostingVps.site_id == site_id))
        job = job_aktif_hosting(sesi, site_id)
        return {
            "aktif_fitur": True,
            "izin_connector": punya_fitur(site, STAGING),
            "ipv4": s.hosting_ipv4, "ipv6": s.hosting_ipv6,
            "hosting": dict_hosting(h, s) if h is not None else None,
            "job": {"id": job.id, "tipe": job.tipe.value, "status": job.status.value,
                    "progres": ringkas_kemajuan(job)} if job is not None else None,
            "backup": daftar_backup(sesi, site_id),
        }


# ---- pindahkan, salin ulang, DNS -------------------------------------------------------------


@router.post("/api/sites/{site_id}/hosting")
def pindahkan(site_id: uuid.UUID, pengguna: PenggunaApi):
    s = fitur_hosting()
    with db.SessionLocal() as sesi:
        site = site_atau_404(sesi, site_id)
        if not punya_fitur(site, STAGING):
            raise HTTPException(status_code=409, detail=PESAN_IZIN)
        url = site.url
    host = host_dari_url(url) if url.startswith("https://") else None
    domain = host.removeprefix("www.") if host else None
    # Domain staging selalu diberikan (carry Task 4): situs di bawahnya ditolak.
    if not domain or not domain_sah(domain, s.staging_domain):
        raise HTTPException(status_code=409, detail=PESAN_DOMAIN)
    # DNS publik di luar kunci (<= 10 detik per pertanyaan). CDN Hostinger
    # dibedakan dari DNS yang tidak menjawab (carry Task 9): IP-nya bukan IP
    # hosting lama, dan tarik ulang terakhir harus ke IP lama yang benar (RF4).
    ip_lama, lewat_cdn = dns_mod.ip_lama_dan_cdn(domain)
    if lewat_cdn:
        raise HTTPException(status_code=409, detail=PESAN_CDN_IP_LAMA)
    if ip_lama is None:
        raise HTTPException(status_code=409, detail=PESAN_IP_LAMA)
    if host.startswith("www."):
        dengan_www = True
    else:
        www = dns_mod.ada_www(domain)
        if www is None:
            raise HTTPException(status_code=409, detail=PESAN_WWW_DNS)
        dengan_www = www
    with db.SessionLocal() as sesi:
        site = site_atau_404(sesi, site_id)
        if kunci_hosting(sesi, site_id) is not None:
            raise HTTPException(status_code=409, detail=PESAN_SUDAH_ADA)
        # Diperiksa ulang di bawah kunci: site bisa diubah selagi DNS ditanya.
        if site.url != url:
            raise HTTPException(status_code=409, detail=PESAN_SITE_BERUBAH)
        if not punya_fitur(site, STAGING):
            raise HTTPException(status_code=409, detail=PESAN_IZIN)
        tolak_bila_sibuk(sesi, site_id)
        # Kata sandi dari `secrets` (sandi_baru), dibalas sekali; yang disimpan hanya hash bcrypt.
        sandi = sandi_baru()
        h = HostingVps(site_id=site.id, nama=_nama_unik(sesi, nama_prod_dari_url(site.url)), domain=domain,
                       dengan_www=dengan_www, ip_lama=ip_lama, sandi_hash=hash_sandi(sandi),
                       status=StatusHosting.menyalin)
        sesi.add(h)
        try:
            sesi.flush()
        except IntegrityError:
            # Site lain baru saja mengambil nama/domain yang sama (kunci per site
            # tidak menahan site lain).
            sesi.rollback()
            raise HTTPException(status_code=409, detail=PESAN_BENTROK) from None
        stg.catat_aktivitas(sesi, site.id, None, f"Pindah hosting ke VPS dimulai ({domain})", user_id=pengguna.id)
        job = simpan_job_hosting(sesi, Job(site_id=site.id, tipe=JobType.pindah_tarik, payload={},
                                           dibuat_oleh=pengguna.id))
        return {"job_id": job.id, "sandi": sandi, "pengguna": PENGGUNA_PRATINJAU}


@router.post("/api/sites/{site_id}/hosting/tarik")
def salin_ulang(site_id: uuid.UUID, pengguna: PenggunaApi):
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site = site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        if h.dilayani_vps_pada is not None:
            raise HTTPException(status_code=409, detail=PESAN_SUDAH_DILAYANI)
        boleh = h.status in (StatusHosting.pratinjau, StatusHosting.menunggu_dns) or (
            h.status == StatusHosting.gagal and h.gagal_asal == hu.ASAL_SALINAN)
        if not boleh:
            raise HTTPException(status_code=409, detail=PESAN_STATUS_TARIK)
        if not punya_fitur(site, STAGING):
            raise HTTPException(status_code=409, detail=PESAN_IZIN)
        tolak_bila_sibuk(sesi, site_id)
        stg.catat_aktivitas(sesi, site_id, None, "Salin ulang ke VPS diminta", user_id=pengguna.id)
        job = simpan_job_hosting(sesi, Job(site_id=site_id, tipe=JobType.pindah_tarik, payload={},
                                           dibuat_oleh=pengguna.id))
        return {"job_id": job.id}


@router.post("/api/sites/{site_id}/hosting/lanjut-dns")
def lanjut_dns(site_id: uuid.UUID, pengguna: PenggunaApi):
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        if h.status != StatusHosting.pratinjau:
            raise HTTPException(status_code=409, detail=PESAN_LANJUT_DNS)
        tolak_bila_sibuk(sesi, site_id)
        h.status = StatusHosting.menunggu_dns
        h.galat = None
        stg.catat_aktivitas(sesi, site_id, None, "Pratinjau disetujui; menunggu DNS", user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


@router.post("/api/sites/{site_id}/hosting/kembali-pratinjau")
def kembali_pratinjau(site_id: uuid.UUID, pengguna: PenggunaApi):
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        if h.status != StatusHosting.menunggu_dns:
            raise HTTPException(status_code=409, detail=PESAN_KEMBALI)
        # Aktivasi tertunda yang belum berjalan akan menolak sendiri (status awal pratinjau).
        h.status = StatusHosting.pratinjau
        stg.catat_aktivitas(sesi, site_id, None, "Kembali ke pratinjau; aktivasi otomatis dihentikan",
                            user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


class PermintaanAktifkan(BaseModel):
    tanpa_tarik_ulang: bool = False
    konfirmasi: str = ""


def _periksa_aktifkan(h: HostingVps, req: PermintaanAktifkan, sekarang: datetime) -> bool:
    """Penolakan sebelum job dibuat (cermin `pindah._periksa_awal`/`_periksa_ulang`); True = Periksa ulang.

    Sudah dilayani VPS (putusan L16): hanya dari `gagal` -- job masuk lagi di
    `tukar` dan mengirim ulang prod-aktifkan, lalu verifikasi. Status lain
    dengan `dilayani_vps_pada` terisi ditolak di sini.
    """
    dilayani = h.dilayani_vps_pada is not None
    if dilayani:
        if h.status != StatusHosting.gagal:
            raise HTTPException(status_code=409, detail=PESAN_PERIKSA_ULANG)
    elif h.status not in (StatusHosting.menunggu_dns, StatusHosting.gagal):
        raise HTTPException(status_code=409, detail=PESAN_STATUS_AKTIFKAN)
    if req.tanpa_tarik_ulang and req.konfirmasi != h.domain:
        raise HTTPException(status_code=400, detail=PESAN_KONFIRMASI_DOMAIN)
    if dilayani:
        return True
    if req.tanpa_tarik_ulang and (h.ditarik_pada is None or (
            h.status == StatusHosting.gagal and h.gagal_asal == hu.ASAL_SALINAN)):
        # Salinan setengah jadi hanya bisa dirampungkan tarik (spec §10.3).
        raise HTTPException(status_code=409, detail=pindah.PESAN_SALINAN_BELUM_UTUH)
    if not dns_mod.backoff_mengizinkan(h, sekarang, manual=True):
        raise HTTPException(status_code=409, detail=PESAN_BACKOFF)
    return False


@router.post("/api/sites/{site_id}/hosting/aktifkan")
def aktifkan(site_id: uuid.UUID, req: PermintaanAktifkan, pengguna: PenggunaApi):
    """Periksa DNS & aktifkan sekarang, juga Periksa ulang untuk gagal asal produksi (spec §11)."""
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = sesi.scalar(select(HostingVps).where(HostingVps.site_id == site_id))
        if h is None:
            raise HTTPException(status_code=409, detail=PESAN_BELUM_ADA)
        periksa_ulang = _periksa_aktifkan(h, req, datetime.now(timezone.utc))
        sasaran = SimpleNamespace(domain=h.domain, dengan_www=h.dengan_www)
    # Cek DNS sinkron di luar kunci; "Periksa ulang" (sudah dilayani) tidak memerlukannya.
    hasil = None if periksa_ulang else dns_mod.periksa_dns(sasaran)
    with db.SessionLocal() as sesi:
        h = _hosting_atau_409(sesi, site_id)
        # Diperiksa ulang di bawah kunci: status bisa berubah selama cek DNS.
        if _periksa_aktifkan(h, req, datetime.now(timezone.utc)) != periksa_ulang:
            raise HTTPException(status_code=409, detail=PESAN_STATUS_AKTIFKAN)
        tolak_bila_sibuk(sesi, site_id)
        if hasil is not None:
            h.dns_hasil = hasil.ke_json()
            h.dns_dicek_pada = datetime.now(timezone.utc)
            if not hasil.ok:
                sesi.commit()
                return JSONResponse(status_code=409, content={"detail": hasil.pesan, "dns_hasil": hasil.ke_json()})
        pesan = "Periksa ulang situs di VPS diminta" if periksa_ulang else "Aktivasi hosting VPS diminta"
        stg.catat_aktivitas(sesi, site_id, None, pesan, user_id=pengguna.id)
        # `manual`: backoff sertifikat di dalam job memakai jeda manual (carry Task 10).
        job = simpan_job_hosting(sesi, Job(site_id=site_id, tipe=JobType.pindah_aktifkan, dibuat_oleh=pengguna.id,
                                           payload={"tanpa_tarik_ulang": bool(req.tanpa_tarik_ulang), "manual": True}))
        return {"job_id": job.id, "dns_hasil": hasil.ke_json() if hasil is not None else None}


# ---- kata sandi, batal, batalkan pindah ------------------------------------------------------


@router.post("/api/sites/{site_id}/hosting/sandi")
def sandi_pratinjau_baru(site_id: uuid.UUID, pengguna: PenggunaApi):
    s = fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        if h.dilayani_vps_pada is not None:
            raise HTTPException(status_code=409, detail=PESAN_SANDI_DILAYANI)
        # Tarik menulis htpasswd dari hash yang dibacanya; hash baru bisa tertimpa.
        tolak_bila_sibuk(sesi, site_id)
        # Dari `secrets`, dibalas sekali; yang disimpan hanya hash bcrypt.
        sandi = sandi_baru()
        hash_baru = hash_sandi(sandi)
        if h.ditarik_pada is not None:
            try:
                tulis_htpasswd_pratinjau(s.jalur_hosting, h.nama, hash_baru)
                stg.buat_pembantu().prod_router_muat()
            except GalatPembantu as exc:
                # Router berikutnya tidak boleh memasang sandi yang tidak pernah dilihat pengguna.
                if h.sandi_hash:
                    tulis_htpasswd_pratinjau(s.jalur_hosting, h.nama, h.sandi_hash)
                raise galat_pembantu_http(exc) from None
        h.sandi_hash = hash_baru
        stg.catat_aktivitas(sesi, site_id, None, "Kata sandi pratinjau VPS dibuat ulang", user_id=pengguna.id)
        sesi.commit()
    return {"sandi": sandi, "pengguna": PENGGUNA_PRATINJAU}


@router.post("/api/sites/{site_id}/hosting/batal")
def batal(site_id: uuid.UUID, pengguna: PenggunaApi):
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        job = job_aktif_hosting(sesi, site_id)
        if job is None:
            raise HTTPException(status_code=409, detail=PESAN_TANPA_JOB)
        if job.tipe == JobType.backup_hosting:
            raise HTTPException(status_code=409, detail=PESAN_BATAL_BACKUP)
        if job.tipe == JobType.pindah_aktifkan and not pindah.boleh_batal_aktifkan(job):
            raise HTTPException(status_code=409, detail=PESAN_BATAL_SESUDAH_TUKAR)
        h.batal_diminta_pada = datetime.now(timezone.utc)
        stg.catat_aktivitas(sesi, site_id, None, "Pembatalan pekerjaan hosting diminta", user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


class PermintaanHapus(BaseModel):
    konfirmasi: str = ""


@router.delete("/api/sites/{site_id}/hosting")
def batalkan_pindah(site_id: uuid.UUID, req: PermintaanHapus, pengguna: PenggunaApi):
    s = fitur_hosting()
    akar = s.jalur_hosting
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        if req.konfirmasi != h.domain:
            raise HTTPException(status_code=400, detail=PESAN_KONFIRMASI_DOMAIN)
        # Batas satu arah (spec §4): situs yang sudah dilayani VPS tidak pernah dibongkar dashboard.
        if h.dilayani_vps_pada is not None:
            raise HTTPException(status_code=409, detail=PESAN_HAPUS_DILAYANI)
        tolak_bila_sibuk(sesi, site_id)
        nama, domain = h.nama, h.domain
        try:
            stg.buat_pembantu().prod_hapus(nama)
        except GalatPembantu as exc:
            raise galat_pembantu_http(exc) from None
        hapus_htpasswd_pratinjau(akar, nama)
        sesi.execute(delete(HostingBackup).where(HostingBackup.site_id == site_id))
        sesi.delete(h)
        stg.catat_aktivitas(sesi, site_id, None, f"Pindah hosting dibatalkan ({domain})", user_id=pengguna.id)
        # Baris dihapus (commit) lebih dulu, berkas sesudahnya (pola hapus staging).
        sesi.commit()
        nisan = []
        try:
            # Transaksi kedua: kunci sites diambil lagi dan keadaan diperiksa ulang,
            # karena pindah baru bisa dimulai di sela dua transaksi.
            _kunci_site(sesi, site_id)
            nama_dir = str(site_id)
            if sesi.scalar(select(HostingVps.id).where(HostingVps.site_id == site_id)) is None \
                    and job_aktif_hosting(sesi, site_id) is None and _dir_nyata(akar / nama_dir):
                n = _ke_nisan(akar, nama_dir, site_id)
                if n:
                    nisan.append(n)
            sesi.commit()
        except Exception:
            sesi.rollback()
            log.exception("Berkas hosting site %s tidak dapat dipindah sesudah pembatalan", site_id)
    # Nisan di WPMGR_HOSTING_DIR dihapus relatif terhadap akarnya sendiri (Koreksi #9).
    for n in nisan:
        _hapus_nisan(akar, n)
    return {"ok": True}
