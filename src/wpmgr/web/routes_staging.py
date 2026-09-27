"""JSON API staging (spec §9, Task 19).

Kunci baris: setiap route yang mengantrekan job staging, dan hapus/jeda/
jalan, mengikuti kontrak kunci cron (`wpmgr.staging.cron`): baris `sites`
FOR NO KEY UPDATE lebih dulu, lalu baris `staging` FOR UPDATE, dipegang
sampai commit perubahan statusnya. Pemeriksaan "sibuk" dibaca dari baris job
(tertunda/berjalan) di bawah kunci itu, bukan dari `staging.status`: job yang
menunggu percobaan ulang di site yang dinonaktifkan tidak pernah diklaim
worker, sementara statusnya bisa saja masih `siap`.

Site yang dinonaktifkan tidak membatalkan job staging tertundanya: job itu
bisa berupa dorong/kembalikan yang sudah setengah jalan di produksi dan
harus dituntaskan begitu site diaktifkan lagi. Route menolaknya dengan
pesan yang menyebut sebabnya.
"""

import json
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from wpmgr import db
from wpmgr.config import get_settings
from wpmgr.crypto import dekripsi_secret, enkripsi_secret
from wpmgr.errors import STAGING_DITOLAK, STAGING_MATI, TRANSIENT, UNKNOWN, SiteError
from wpmgr.fitur import STAGING, punya_fitur
from wpmgr.jobs.handlers import buat_klien
from wpmgr.models import (
    JOB_STAGING,
    Job,
    JobStatus,
    JobType,
    PackageType,
    Site,
    SitePackage,
    SiteStatus,
    Staging,
    StagingSnapshot,
    StagingUji,
    StatusStaging,
    User,
)
from wpmgr.site_client import MelebihiBatas, TanpaHasil, TenggatHabis, minta_bertenggat
from wpmgr.sso import buat_token
from wpmgr.staging import dorong as dorong_mod
from wpmgr.staging import uji as uji_mod
from wpmgr.staging import umum
from wpmgr.staging.aman import angka, bersih_teks, nama_dari_url
from wpmgr.staging.cron import (
    _dir_nyata,
    _hapus_nisan,
    _kunci_site,
    _nisan_selain_snapshot,
)
from wpmgr.staging.pembantu import (
    GalatPembantu,
    hapus_akses_router,
    hash_sandi,
    sandi_baru,
    tautan_masuk,
    tulis_akses_router,
)
from wpmgr.staging.rencana import (
    bandingkan_tanda_air,
    cek_maks_aktif,
    cek_ram,
    format_byte,
    urai_tanda_air,
)
from wpmgr.web.auth import pengguna_api

router = APIRouter()
PenggunaApi = Annotated[User, Depends(pengguna_api)]

BATAS_DAFTAR = 50
BATAS_UJI_RINGKAS = 20
# Mailpit menampung email yang dikirim kode staging (salinan produksi yang
# bisa saja disusupi): balasannya dibaca dengan tenggat total dan batas byte
# (putusan F11), lalu setiap field dibersihkan.
BATAS_EMAIL_BYTE = 2 * 1024 * 1024
TIMEOUT_EMAIL = 10.0
TENGGAT_EMAIL = 15.0
POLA_ID_EMAIL = re.compile(r"[A-Za-z0-9]{1,64}")
MAKS_NAMA_PAKET_SALAH = 10
PENGGUNA_PREVIEW = "staging"
TEKS_STATUS = {"menyalin": "Menyalin dari produksi", "siap": "Siap", "berjalan_uji": "Menjalankan uji update",
               "mendorong": "Mendorong ke produksi", "dijeda": "Dijeda", "gagal": "Gagal"}
LABEL_TAHAP = {
    "mulai": "Menunggu giliran", "manifest": "Membaca daftar berkas", "berkas": "Menyalin berkas",
    "tanda_air": "Membaca tanda air", "db": "Menyalin database", "impor": "Mengimpor database",
    "penyiapan": "Menyiapkan container", "sertifikat": "Menerbitkan sertifikat", "sebelum": "Memeriksa halaman",
    "update": "Menjalankan update", "sesudah": "Memeriksa halaman sesudah update", "nilai": "Menilai hasil",
    "rencana": "Menyusun rencana", "snapshot_berkas": "Snapshot berkas produksi",
    "snapshot_db": "Snapshot database produksi", "snapshot_catat": "Mencatat snapshot",
    "unggah": "Mengunggah ke produksi", "cek_ulang": "Memeriksa ulang data baru", "terapkan": "Menerapkan di produksi",
    "cek": "Memeriksa halaman utama", "tanpa_perubahan": "Tidak ada perubahan untuk didorong",
}
# Hanya di tahap ini byte_selesai/byte_total menggambarkan pekerjaan yang
# sedang berjalan; di tahap lain nilainya sisa tahap sebelumnya.
TAHAP_BYTE = frozenset({"berkas", "unggah", "snapshot_berkas"})

PESAN_BELUM_DIBUAT = "Staging untuk site ini belum dibuat."
PESAN_IZIN = ("Connector site ini belum mengizinkan staging. Aktifkan 'Izinkan staging' di Pengaturan -> "
              "WP Manager (connector 3.0).")
PESAN_SIBUK = "Tunggu pekerjaan staging yang sedang berjalan selesai."
PESAN_SIBUK_NONAKTIF = (" Site ini dinonaktifkan, jadi pekerjaan staging yang tertunda tidak dijalankan sampai "
                        "site diaktifkan lagi.")
PESAN_BELUM_DISALIN = "Staging belum selesai disalin; segarkan staging dulu."
PESAN_DIUBAH_SEGARKAN = ("Staging diubah sejak tarik terakhir; perubahan itu akan tertimpa. Konfirmasi untuk tetap "
                         "menyegarkan.")
PESAN_EMAIL_GAGAL = "Kotak email staging tidak dapat dibaca."
PESAN_EMAIL_HILANG = "Email tidak ditemukan."


def _iso(nilai):
    return nilai.isoformat() if nilai else None


def ringkas_kemajuan(job: Job, sekarang: datetime | None = None) -> dict:
    """Persen, MB, dan perkiraan sisa waktu untuk strip progres (spec §9).

    Kemajuan ditulis job dashboard sendiri, tetapi tetap dibaca defensif:
    tahap yang tidak dikenal menjadi "mulai" dan angka dijepit.
    """
    sekarang = sekarang or datetime.now(timezone.utc)
    k = (job.payload or {}).get("kemajuan") or {}
    if not isinstance(k, dict):
        k = {}
    tahap_uji = k.get("tahap_uji")
    # Uji menjalankan tarik lebih dulu (tahap_uji "tarik"): selama itu tahap
    # tarik yang ditampilkan.
    tahap = k.get("tahap_dorong") or k.get("tahap_balik") \
        or (tahap_uji if tahap_uji not in (None, "tarik") else None) or k.get("tahap") or "mulai"
    tahap = tahap if isinstance(tahap, str) and tahap in LABEL_TAHAP else "mulai"
    total = angka(k.get("byte_total"), 0, 2**62) or 0
    selesai = min(angka(k.get("byte_selesai"), 0, 2**62) or 0, total)
    persen = int(selesai * 100 // total) if total and tahap in TAHAP_BYTE else None
    teks = LABEL_TAHAP[tahap]
    if persen is not None:
        teks += f" {persen}% · {format_byte(selesai)} dari {format_byte(total)}"
        try:
            mulai = datetime.fromisoformat(str(k.get("mulai")))
        except ValueError:
            mulai = None
        if mulai is not None and mulai.tzinfo is not None and 0 < selesai < total:
            detik = (sekarang - mulai).total_seconds()
            if detik > 0:
                sisa = (total - selesai) / (selesai / detik)
                teks += f" · sisa ±{max(1, round(sisa / 60))} menit"
    return {"tahap": tahap, "label": LABEL_TAHAP[tahap], "persen": persen, "byte_selesai": selesai,
            "byte_total": total, "teks": teks}


# ---- pembantu route -------------------------------------------------------------


def _fitur() -> None:
    if not get_settings().staging_aktif:
        raise HTTPException(status_code=404, detail="Fitur staging tidak aktif")


def _site(sesi, site_id: uuid.UUID) -> Site:
    site = sesi.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site tidak ditemukan")
    return site


def _izin(site: Site) -> None:
    if not punya_fitur(site, STAGING):
        raise HTTPException(status_code=409, detail=PESAN_IZIN)


def _staging(sesi, site_id: uuid.UUID) -> Staging:
    st = sesi.scalar(select(Staging).where(Staging.site_id == site_id))
    if st is None:
        raise HTTPException(status_code=409, detail=PESAN_BELUM_DIBUAT)
    return st


def _kunci_staging(sesi, site_id: uuid.UUID) -> Staging | None:
    """Baris staging FOR UPDATE, dibaca ulang; pemanggil sudah memegang kunci baris sites."""
    return sesi.scalar(select(Staging).where(Staging.site_id == site_id).with_for_update()
                       .execution_options(populate_existing=True))


def _kunci(sesi, site_id: uuid.UUID) -> Staging | None:
    """Kontrak kunci cron: sites FOR NO KEY UPDATE lalu staging FOR UPDATE, sampai commit."""
    _kunci_site(sesi, site_id)
    return _kunci_staging(sesi, site_id)


def _job_aktif(sesi, site_id) -> Job | None:
    return sesi.scalar(select(Job).where(
        Job.site_id == site_id, Job.tipe.in_(JOB_STAGING),
        Job.status.in_((JobStatus.pending, JobStatus.running))).order_by(Job.id.desc()).limit(1))


def _tolak_bila_sibuk(sesi, site: Site) -> None:
    job = _job_aktif(sesi, site.id)
    if job is None:
        return
    pesan = PESAN_SIBUK
    if site.status == SiteStatus.disabled and job.status == JobStatus.pending:
        pesan += PESAN_SIBUK_NONAKTIF
    raise HTTPException(status_code=409, detail=pesan)


def _job_baru(site_id, tipe: JobType, payload: dict, pengguna: User) -> Job:
    return Job(site_id=site_id, tipe=tipe, payload=payload, dibuat_oleh=pengguna.id)


def _simpan_job(sesi, jobs: list[Job]) -> list[Job]:
    """Sisipkan semua job dalam satu commit (yang juga melepas kunci baris).

    `uq_jobs_staging_aktif` tetap penjaga terakhir: bila ia menolak, tidak
    satu job pun tersimpan.
    """
    sesi.add_all(jobs)
    try:
        sesi.commit()
    except IntegrityError:
        sesi.rollback()
        raise HTTPException(status_code=409,
                            detail="Masih ada pekerjaan staging yang tertunda atau berjalan untuk site ini.") from None
    return jobs


def _pembantu_gagal(exc: GalatPembantu) -> HTTPException:
    # GalatPembantu.pesan selalu teks tetap (tanpa stderr, tanpa path).
    return HTTPException(status_code=502, detail=exc.pesan)


def _diubah_sejak_tarik(st: Staging) -> bool:
    return bool(st.diubah_pada and st.ditarik_pada and st.diubah_pada > st.ditarik_pada)


def _dict_staging(st: Staging) -> dict:
    hasil = {
        "nama": st.nama, "status": st.status.value, "status_teks": TEKS_STATUS[st.status.value], "aktif": st.aktif,
        "url": umum.url_staging(st), "pengguna": PENGGUNA_PREVIEW, "versi_php": st.versi_php,
        "ukuran_file_teks": format_byte(st.ukuran_file), "ukuran_db_teks": format_byte(st.ukuran_db),
        "ditarik_pada": _iso(st.ditarik_pada), "diubah_pada": _iso(st.diubah_pada), "dibuka_pada": _iso(st.dibuka_pada),
        "sertifikat_pada": _iso(st.sertifikat_pada), "galat": st.galat, "dorong_gagal_pada": _iso(st.dorong_gagal_pada),
        "batal_diminta": st.batal_diminta_pada is not None,
        "diubah_sejak_tarik": _diubah_sejak_tarik(st),
    }
    # Asal hanya bermakna untuk status gagal (R20); di luar itu sisa lama.
    if st.status == StatusStaging.gagal:
        hasil["gagal_asal"] = st.gagal_asal
    return hasil


def _dict_snapshot(s: StagingSnapshot) -> dict:
    detail = s.detail if isinstance(s.detail, dict) else {}
    return {"id": s.id, "status": s.status, "jenis": s.jenis, "ukuran_teks": format_byte(s.ukuran),
            "mode": detail.get("mode"), "jumlah_berkas": detail.get("jumlah_berkas"),
            "perubahan": detail.get("perubahan") or [], "dibuat_pada": _iso(s.dibuat_pada)}


def _dict_uji(u: StagingUji) -> dict:
    periksa = u.pemeriksaan if isinstance(u.pemeriksaan, dict) else {}
    return {"id": u.id, "hasil": u.hasil, "paket": u.paket, "alasan": periksa.get("alasan") or [],
            "halaman": periksa.get("halaman") or [], "dibuat_pada": _iso(u.dibuat_pada)}


def _daftar_snapshot(sesi, site_id, batas: int) -> list[dict]:
    return [_dict_snapshot(s) for s in sesi.scalars(
        select(StagingSnapshot).where(StagingSnapshot.site_id == site_id)
        .order_by(StagingSnapshot.dibuat_pada.desc(), StagingSnapshot.id.desc()).limit(batas))]


def _daftar_uji(sesi, site_id, batas: int) -> list[dict]:
    return [_dict_uji(u) for u in sesi.scalars(
        select(StagingUji).where(StagingUji.site_id == site_id)
        .order_by(StagingUji.dibuat_pada.desc(), StagingUji.id.desc()).limit(batas))]


# ---- status -------------------------------------------------------------------------


@router.get("/api/sites/{site_id}/staging")
def status_staging(site_id: uuid.UUID, pengguna: PenggunaApi):
    if not get_settings().staging_aktif:
        return {"aktif_fitur": False}
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        st = sesi.scalar(select(Staging).where(Staging.site_id == site_id))
        if st is not None:
            umum.perbarui_diubah(st)
            sesi.commit()
        job = _job_aktif(sesi, site_id)
        return {
            "aktif_fitur": True,
            "izin_connector": punya_fitur(site, STAGING),
            "staging": _dict_staging(st) if st is not None else None,
            "job": {"id": job.id, "tipe": job.tipe.value, "status": job.status.value, "progres": ringkas_kemajuan(job)}
            if job is not None else None,
            "snapshot": _daftar_snapshot(sesi, site_id, BATAS_DAFTAR),
            "uji": _daftar_uji(sesi, site_id, BATAS_UJI_RINGKAS),
        }


# ---- buat / segarkan, jalan, jeda, hapus --------------------------------------------


class PermintaanBuat(BaseModel):
    konfirmasi: bool = False


def _nama_unik(sesi, dasar: str) -> str:
    kandidat = dasar
    for i in range(2, 100):
        if sesi.scalar(select(Staging.id).where(Staging.nama == kandidat)) is None:
            return kandidat
        akhiran = f"-{i}"
        kandidat = dasar[:40 - len(akhiran)].rstrip("-") + akhiran
    raise HTTPException(status_code=409, detail="Nama staging untuk site ini tidak dapat ditentukan.")


@router.post("/api/sites/{site_id}/staging")
def buat_atau_segarkan(site_id: uuid.UUID, req: PermintaanBuat, pengguna: PenggunaApi):
    _fitur()
    s = get_settings()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        _izin(site)
        st = _kunci(sesi, site_id)
        _tolak_bila_sibuk(sesi, site)
        sandi = None
        if st is None:
            aktif = sesi.scalar(select(func.count()).select_from(Staging).where(Staging.aktif.is_(True))) or 0
            pesan = cek_maks_aktif(aktif, s.staging_maks_aktif)
            if pesan:
                raise HTTPException(status_code=409, detail=pesan)
            sandi = sandi_baru()
            st = Staging(site_id=site.id, nama=_nama_unik(sesi, nama_dari_url(site.url)), sandi_hash=hash_sandi(sandi),
                         rahasia_router_terenkripsi=enkripsi_secret(secrets.token_hex(32)))
            sesi.add(st)
            try:
                sesi.flush()
            except IntegrityError:
                # Site lain baru saja mengambil nama yang sama (kunci per site
                # tidak menahan site lain).
                sesi.rollback()
                raise HTTPException(status_code=409, detail="Nama staging bentrok dengan staging lain yang baru "
                                                            "dibuat; coba lagi.") from None
        else:
            umum.perbarui_diubah(st)
            if _diubah_sejak_tarik(st) and not req.konfirmasi:
                raise HTTPException(status_code=409, detail=PESAN_DIUBAH_SEGARKAN)
        # Baris staging baru dan job-nya tersimpan bersama, dalam satu commit.
        job, = _simpan_job(sesi, [_job_baru(site.id, JobType.staging_tarik, {}, pengguna)])
        return {"job_id": job.id, "sandi": sandi, "pengguna": PENGGUNA_PREVIEW if sandi else None}


@router.post("/api/sites/{site_id}/staging/jalan")
def jalankan(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    s = get_settings()
    with db.SessionLocal() as sesi:
        _site(sesi, site_id)
        st = _kunci(sesi, site_id)
        if st is None:
            raise HTTPException(status_code=409, detail=PESAN_BELUM_DIBUAT)
        if st.aktif:
            return {"ok": True}
        if st.ditarik_pada is None:
            # Container staging baru ada sesudah tarik pertama selesai.
            raise HTTPException(status_code=409, detail=PESAN_BELUM_DISALIN)
        pb = umum.buat_pembantu()
        try:
            status = pb.status()
            aktif = sesi.scalar(select(func.count()).select_from(Staging).where(Staging.aktif.is_(True))) or 0
            pesan = cek_ram(status) or cek_maks_aktif(aktif, s.staging_maks_aktif)
            if pesan:
                raise HTTPException(status_code=409, detail=pesan)
            pb.jalan(st.nama)
        except GalatPembantu as exc:
            raise _pembantu_gagal(exc) from None
        st.aktif = True
        # Hanya dijeda -> siap; status lain (termasuk gagal dan asalnya) tetap.
        if st.status == StatusStaging.dijeda:
            st.status = StatusStaging.siap
        umum.catat_aktivitas(sesi, site_id, None, "Staging dijalankan", user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


@router.post("/api/sites/{site_id}/staging/jeda")
def jeda(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        # F29: kunci dipegang sejak pemeriksaan sibuk sampai perubahan status di-commit.
        st = _kunci(sesi, site_id)
        if st is None:
            raise HTTPException(status_code=409, detail=PESAN_BELUM_DIBUAT)
        _tolak_bila_sibuk(sesi, site)
        try:
            umum.buat_pembantu().jeda(st.nama)
        except GalatPembantu as exc:
            raise _pembantu_gagal(exc) from None
        st.aktif = False
        if st.status == StatusStaging.siap:
            st.status = StatusStaging.dijeda
        umum.catat_aktivitas(sesi, site_id, None, "Staging dijeda", user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


def _bongkar_staging(pb, st: Staging) -> None:
    """Container, database, dan akses router staging; GalatPembantu diteruskan ke pemanggil."""
    pb.hapus(st.nama)
    pb.db_hapus(st.nama)
    hapus_akses_router(get_settings().jalur_staging, st.nama)
    pb.router_muat()


@router.delete("/api/sites/{site_id}/staging")
def hapus(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    akar = get_settings().jalur_staging
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        st = _kunci(sesi, site_id)
        if st is None:
            raise HTTPException(status_code=409, detail=PESAN_BELUM_DIBUAT)
        _tolak_bila_sibuk(sesi, site)
        try:
            _bongkar_staging(umum.buat_pembantu(), st)
        except GalatPembantu as exc:
            raise _pembantu_gagal(exc) from None
        # Snapshot adalah cadangan produksi: `snapshot/` dipertahankan supaya
        # kembalikan tetap bisa berjalan tanpa baris Staging; cron memangkas
        # sisanya. Selain itu dipindah ke nisan di bawah kunci sites yang
        # sama dengan pemangkasan cron (rename atomik, tanpa mengikuti
        # symlink), lalu dihapus sesudah commit.
        nama_dir = str(site_id)
        nisan = _nisan_selain_snapshot(akar, nama_dir, site_id) if _dir_nyata(akar / nama_dir) else []
        nama = st.nama
        sesi.delete(st)
        umum.catat_aktivitas(sesi, site_id, None, f"Staging dihapus ({nama})", user_id=pengguna.id)
        sesi.commit()
    for n in nisan:
        _hapus_nisan(akar, n)
    return {"ok": True}


def bersihkan_untuk_hapus_site(sesi, site: Site) -> None:
    """Dipanggil `DELETE /api/sites/{id}` di bawah kunci sites, sebelum baris site dihapus.

    Baris staging ikut terhapus kaskade, tetapi container, database, dan akses
    router tidak: dibongkar dulu di sini. Berkas `<site_id>/` dipangkas cron
    (tanpa baris Staging maupun snapshot, seluruh direktori dibuang).
    """
    st = _kunci_staging(sesi, site.id)
    if st is None:
        return
    berjalan = sesi.scalar(select(Job.id).where(
        Job.site_id == site.id, Job.tipe.in_(JOB_STAGING), Job.status == JobStatus.running).limit(1))
    if berjalan is not None:
        # Job tertunda ikut terhapus kaskade dan tidak pernah berjalan; job
        # yang sedang berjalan akan terus menulis ke container dan produksi.
        raise HTTPException(status_code=409, detail="Tunggu pekerjaan staging yang sedang berjalan selesai "
                                                    "sebelum mencabut site ini.")
    try:
        _bongkar_staging(umum.buat_pembantu(), st)
    except GalatPembantu as exc:
        raise _pembantu_gagal(exc) from None


# ---- tanda air ----------------------------------------------------------------------


def _galat_connector(exc: SiteError) -> HTTPException:
    """Kelas galat connector (R15) ke HTTP. Teks galat mentah tidak pernah dikirim ke UI."""
    if exc.error_class == STAGING_DITOLAK:
        return HTTPException(status_code=409, detail="Produksi menolak permintaan staging ini saat ini; coba lagi "
                                                     "nanti.")
    if exc.error_class == STAGING_MATI:
        return HTTPException(status_code=409, detail=PESAN_IZIN)
    if exc.error_class in (TRANSIENT, UNKNOWN):
        return HTTPException(status_code=503, detail="Site produksi tidak dapat dihubungi saat ini; coba lagi.")
    return HTTPException(status_code=502, detail="Tanda air produksi tidak dapat dibaca.")


@router.get("/api/sites/{site_id}/staging/tanda-air")
def tanda_air(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        lama = _staging(sesi, site_id).tanda_air
    # Sesi DB sudah ditutup: permintaan ke connector bisa sampai 30 detik.
    posts = ((lama or {}).get("sumber") or {}).get("posts") or {}
    try:
        data = buat_klien(site).staging_tanda_air(posts.get("diubah") or None, posts.get("maks_id") or None)
    except SiteError as exc:
        raise _galat_connector(exc) from None
    baru = urai_tanda_air(data)
    if baru is None:
        raise HTTPException(status_code=502, detail="Tanda air produksi tidak dapat dibaca.")
    return {"perubahan": bandingkan_tanda_air(lama, baru)}


# ---- dorong, kembalikan -------------------------------------------------------------


class PermintaanDorong(BaseModel):
    mode: str
    konfirmasi_nama: str | None = None


def _periksa_dorong(st: Staging) -> None:
    """Cermin penolakan pra-sentuh `dorong._periksa_awal`, supaya tidak ada job yang dibuat untuk ditolak.

    Job tetap memeriksa ulang sendiri.
    """
    if st.ditarik_pada is None or not st.tanda_air:
        raise HTTPException(status_code=409, detail=dorong_mod.PESAN_BELUM_TARIK)
    if not st.aktif:
        raise HTTPException(status_code=409, detail=dorong_mod.PESAN_DIJEDA)
    if st.status in (StatusStaging.menyalin, StatusStaging.berjalan_uji):
        raise HTTPException(status_code=409, detail=dorong_mod.PESAN_SALINAN_SIBUK)
    if dorong_mod.gagal_milik_salinan(st):
        raise HTTPException(status_code=409, detail=dorong_mod.PESAN_SALINAN_GAGAL)


@router.post("/api/sites/{site_id}/staging/dorong")
def antrekan_dorong(site_id: uuid.UUID, req: PermintaanDorong, pengguna: PenggunaApi):
    _fitur()
    if req.mode not in dorong_mod.MODE:
        raise HTTPException(status_code=422, detail=dorong_mod.PESAN_MODE)
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        _izin(site)
        st = _kunci(sesi, site_id)
        if st is None:
            raise HTTPException(status_code=409, detail=PESAN_BELUM_DIBUAT)
        _periksa_dorong(st)
        _tolak_bila_sibuk(sesi, site)
        payload = {"mode": req.mode, "konfirmasi_nama": (req.konfirmasi_nama or "")[:200] or None}
        job, = _simpan_job(sesi, [_job_baru(site_id, JobType.staging_dorong, payload, pengguna)])
        return {"job_id": job.id}


class PermintaanKembalikan(BaseModel):
    snapshot_id: int
    konfirmasi_nama: str


@router.post("/api/sites/{site_id}/staging/kembalikan")
def antrekan_kembalikan(site_id: uuid.UUID, req: PermintaanKembalikan, pengguna: PenggunaApi):
    """Tidak digerbangi keadaan salinan (R19): pemulihan produksi tidak butuh staging, bahkan barisnya."""
    _fitur()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        _izin(site)
        _kunci(sesi, site_id)
        snap = sesi.get(StagingSnapshot, req.snapshot_id)
        if snap is None or snap.site_id != site_id or snap.status not in dorong_mod.STATUS_SNAPSHOT_SAH:
            raise HTTPException(status_code=404, detail="Snapshot tidak ditemukan atau sudah dipangkas.")
        if req.konfirmasi_nama != site.nama:
            raise HTTPException(status_code=422, detail=dorong_mod.PESAN_KONFIRMASI_BALIK)
        _tolak_bila_sibuk(sesi, site)
        payload = {"snapshot_id": snap.id, "konfirmasi_nama": req.konfirmasi_nama}
        job, = _simpan_job(sesi, [_job_baru(site_id, JobType.staging_kembalikan, payload, pengguna)])
        return {"job_id": job.id}


# ---- kata sandi preview ---------------------------------------------------------------


@router.post("/api/sites/{site_id}/staging/sandi")
def sandi_preview_baru(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    akar = get_settings().jalur_staging
    with db.SessionLocal() as sesi:
        _site(sesi, site_id)
        st = _kunci(sesi, site_id)
        if st is None:
            raise HTTPException(status_code=409, detail=PESAN_BELUM_DIBUAT)
        # Dibalas sekali; yang disimpan hanya hash bcrypt.
        sandi = sandi_baru()
        hash_baru = hash_sandi(sandi)
        if st.ditarik_pada is not None and st.rahasia_router_terenkripsi:
            rahasia = dekripsi_secret(st.rahasia_router_terenkripsi)
            try:
                tulis_akses_router(akar, st.nama, hash_baru, rahasia)
                umum.buat_pembantu().router_muat()
            except GalatPembantu as exc:
                # Berkas router dikembalikan ke hash lama: router-muat berikutnya
                # tidak boleh memasang kata sandi yang tidak pernah dilihat pengguna.
                if st.sandi_hash:
                    tulis_akses_router(akar, st.nama, st.sandi_hash, rahasia)
                raise _pembantu_gagal(exc) from None
        st.sandi_hash = hash_baru
        umum.catat_aktivitas(sesi, site_id, None, "Kata sandi preview dibuat ulang", user_id=pengguna.id)
        sesi.commit()
    return {"sandi": sandi, "pengguna": PENGGUNA_PREVIEW}


# ---- uji update ------------------------------------------------------------------------


def _label_paket(p) -> str:
    slug = p.get("slug") if isinstance(p, dict) else None
    return bersih_teks(slug, 100) if isinstance(slug, str) and slug else "(tanpa slug)"


def _paket_uji(daftar) -> list[dict]:
    """Paket yang sah untuk skrip pembantu, atau 400 yang menyebut setiap paket yang ditolak.

    Pola slug skrip pembantu lebih sempit daripada WordPress (mis.
    `js_composer`, `Divi` ditolak): pengguna perlu tahu paket mana, bukan
    hanya bahwa daftarnya ditolak.
    """
    if not isinstance(daftar, list) or not 1 <= len(daftar) <= uji_mod.MAKS_PAKET:
        raise HTTPException(status_code=400,
                            detail=f"Daftar paket uji harus berisi 1 sampai {uji_mod.MAKS_PAKET} paket.")
    hasil, salah = [], []
    for p in daftar:
        try:
            hasil.extend(uji_mod.urai_paket([p]))
        except uji_mod.PaketTidakSah:
            salah.append(_label_paket(p))
    if salah:
        lebih = len(salah) - MAKS_NAMA_PAKET_SALAH
        teks = ", ".join(salah[:MAKS_NAMA_PAKET_SALAH]) + (f", dan {lebih} lainnya" if lebih > 0 else "")
        raise HTTPException(status_code=400, detail="Paket berikut tidak bisa diuji di staging (slug atau versi "
                                                    f"tidak dikenali skrip pembantu): {teks}.")
    return [{"tipe": p["tipe"], "slug": p["slug"], "dari": p["dari"], "ke": p["ke"]} for p in hasil]


def _isi_dari(sesi, site_id, paket: list[dict]) -> list[dict]:
    """Versi terpasang dari inventaris untuk paket yang tidak membawa `dari`."""
    for p in paket:
        if p["dari"] is None:
            p["dari"] = sesi.scalar(select(SitePackage.versi_terpasang).where(
                SitePackage.site_id == site_id, SitePackage.tipe == PackageType(p["tipe"]),
                SitePackage.slug == p["slug"]))
    return paket


def _periksa_uji(sesi, site: Site, st: Staging | None, konfirmasi: bool) -> Staging:
    """Semua penolakan uji sebelum job dibuat (cermin `uji._periksa_awal` dan cek konfirmasi `uji.uji`)."""
    _izin(site)
    if st is None:
        raise HTTPException(status_code=409, detail=PESAN_BELUM_DIBUAT)
    if not st.rahasia_router_terenkripsi or not st.sandi_hash:
        raise HTTPException(status_code=409, detail=uji_mod.PESAN_AKSES_BELUM)
    umum.perbarui_diubah(st)
    if _diubah_sejak_tarik(st) and not konfirmasi:
        raise HTTPException(status_code=409, detail=uji_mod.PESAN_KONFIRMASI)
    _tolak_bila_sibuk(sesi, site)
    return st


class PermintaanUji(BaseModel):
    paket: list[dict]
    konfirmasi: bool = False


@router.post("/api/sites/{site_id}/staging/uji")
def uji_site(site_id: uuid.UUID, req: PermintaanUji, pengguna: PenggunaApi):
    _fitur()
    paket = _paket_uji(req.paket)
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        st = _kunci(sesi, site_id)
        _periksa_uji(sesi, site, st, req.konfirmasi)
        payload = {"paket": _isi_dari(sesi, site_id, paket), "konfirmasi": req.konfirmasi}
        job, = _simpan_job(sesi, [_job_baru(site_id, JobType.staging_uji_update, payload, pengguna)])
        return {"job_id": job.id}


class ItemUji(BaseModel):
    site_id: uuid.UUID
    tipe: PackageType
    slug: str
    ke_versi: str


class PermintaanUjiBanyak(BaseModel):
    items: list[ItemUji]
    konfirmasi: bool = False


@router.post("/api/staging/uji")
def uji_banyak(req: PermintaanUjiBanyak, pengguna: PenggunaApi):
    """Satu job per site dari halaman Update; semua atau tidak sama sekali (F16)."""
    _fitur()
    mentah: dict[uuid.UUID, list[dict]] = {}
    for item in req.items:
        mentah.setdefault(item.site_id, []).append({"tipe": item.tipe.value, "slug": item.slug, "ke": item.ke_versi})
    # Lewatan 1: paket semua site, sebelum menyentuh database.
    paket = {site_id: _paket_uji(daftar) for site_id, daftar in mentah.items()}
    with db.SessionLocal() as sesi:
        sites = {site_id: _site(sesi, site_id) for site_id in paket}
        # Lewatan 2, di bawah kunci: semua baris sites (urutan tetap), lalu
        # semua baris staging, sesuai kontrak kunci cron.
        urutan = sorted(paket, key=str)
        for site_id in urutan:
            _kunci_site(sesi, site_id)
        staging = {site_id: _kunci_staging(sesi, site_id) for site_id in urutan}
        for site_id in paket:
            try:
                _periksa_uji(sesi, sites[site_id], staging[site_id], req.konfirmasi)
            except HTTPException as exc:
                raise HTTPException(status_code=exc.status_code,
                                    detail=f"{sites[site_id].nama}: {exc.detail}") from None
        # Lewatan 3: semua job dalam satu commit.
        jobs = _simpan_job(sesi, [
            _job_baru(site_id, JobType.staging_uji_update,
                      {"paket": _isi_dari(sesi, site_id, p), "konfirmasi": req.konfirmasi}, pengguna)
            for site_id, p in paket.items()])
        return {"job_ids": [j.id for j in jobs]}


@router.get("/api/sites/{site_id}/staging/uji")
def daftar_uji(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        _site(sesi, site_id)
        return _daftar_uji(sesi, site_id, BATAS_DAFTAR)


@router.get("/api/sites/{site_id}/staging/snapshot")
def daftar_snapshot(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        _site(sesi, site_id)
        return _daftar_snapshot(sesi, site_id, BATAS_DAFTAR)


# ---- batal, SSO ------------------------------------------------------------------------


@router.post("/api/sites/{site_id}/staging/batal")
def batal(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        st = _staging(sesi, site_id)
        if _job_aktif(sesi, site_id) is None:
            raise HTTPException(status_code=409, detail="Tidak ada pekerjaan staging yang bisa dibatalkan.")
        st.batal_diminta_pada = datetime.now(timezone.utc)
        umum.catat_aktivitas(sesi, site_id, None, "Pembatalan job staging diminta", user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


@router.get("/api/sites/{site_id}/staging/sso")
def sso_staging(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        st = _staging(sesi, site_id)
        if st.ditarik_pada is None:
            raise HTTPException(status_code=409, detail=PESAN_BELUM_DISALIN)
        if not st.rahasia_router_terenkripsi:
            raise HTTPException(status_code=409, detail=uji_mod.PESAN_AKSES_BELUM)
        host = umum.host_staging(st)
        # Salinan membawa secret connector produksi, jadi token SSO produksi
        # juga berlaku di staging; tautan secure_link melewati Basic Auth (Koreksi #5).
        token = buat_token(dekripsi_secret(site.secret_terenkripsi), str(site.id))
        url = f"https://{host}" + tautan_masuk(dekripsi_secret(st.rahasia_router_terenkripsi), host, token,
                                               int(time.time()))
        st.dibuka_pada = datetime.now(timezone.utc)
        umum.catat_aktivitas(sesi, site_id, None, "SSO staging dibuka", user_id=pengguna.id)
        sesi.commit()
        return {"url": url}


# ---- email Mailpit (Koreksi #17) -------------------------------------------------------


def _alamat(nilai) -> str:
    if not isinstance(nilai, dict):
        return ""
    return bersih_teks(nilai.get("Address"), 320) or ""


def _daftar_alamat(nilai) -> list[str]:
    return [_alamat(t) for t in (nilai if isinstance(nilai, list) else [])[:10] if isinstance(t, dict)]


def _bertag(m: dict, nama: str) -> bool:
    tags = m.get("Tags")
    return isinstance(tags, list) and nama in tags


def _teks(nilai, panjang: int) -> str | None:
    return bersih_teks(nilai, panjang) if isinstance(nilai, str) else None


def _mailpit(path: str, params: dict | None = None) -> dict:
    url = get_settings().staging_mailpit_url + path + ("?" + urlencode(params) if params else "")
    http = umum.buat_http()
    try:
        status, _, isi = minta_bertenggat(
            http, "GET", url, headers={"Accept": "application/json", "Accept-Encoding": "identity",
                                       "Connection": "close"},
            timeout=TIMEOUT_EMAIL, tenggat=TENGGAT_EMAIL, batas_byte=BATAS_EMAIL_BYTE)
    except MelebihiBatas:
        raise HTTPException(status_code=502, detail="Balasan kotak email staging terlalu besar.") from None
    except (httpx.HTTPError, TenggatHabis, TanpaHasil):
        raise HTTPException(status_code=502, detail=PESAN_EMAIL_GAGAL) from None
    finally:
        http.close()
    if status == 404:
        raise HTTPException(status_code=404, detail=PESAN_EMAIL_HILANG)
    try:
        data = json.loads(isi)
    except ValueError:
        data = None
    if status != 200 or not isinstance(data, dict):
        raise HTTPException(status_code=502, detail=PESAN_EMAIL_GAGAL)
    return data


@router.get("/api/sites/{site_id}/staging/email")
def daftar_email(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        nama = _staging(sesi, site_id).nama
    data = _mailpit("/api/v1/search", {"query": f'tag:"{nama}"', "limit": str(BATAS_DAFTAR)})
    pesan = data.get("messages")
    hasil = []
    for m in pesan if isinstance(pesan, list) else []:
        if not isinstance(m, dict) or not isinstance(m.get("ID"), str) or not POLA_ID_EMAIL.fullmatch(m["ID"]):
            continue
        # Satu Mailpit untuk semua staging: saring ulang, jangan percaya pencarian saja.
        if not _bertag(m, nama):
            continue
        hasil.append({
            "id": m["ID"], "dari": _alamat(m.get("From")), "ke": _daftar_alamat(m.get("To")),
            "subjek": _teks(m.get("Subject"), 300) or "", "waktu": _teks(m.get("Created"), 40),
            "cuplikan": _teks(m.get("Snippet"), 300) or "",
        })
        if len(hasil) >= BATAS_DAFTAR:
            break
    return hasil


@router.get("/api/sites/{site_id}/staging/email/{msg_id}")
def isi_email(site_id: uuid.UUID, msg_id: str, pengguna: PenggunaApi):
    _fitur()
    if not POLA_ID_EMAIL.fullmatch(msg_id):
        raise HTTPException(status_code=404, detail=PESAN_EMAIL_HILANG)
    with db.SessionLocal() as sesi:
        nama = _staging(sesi, site_id).nama
    m = _mailpit(f"/api/v1/message/{msg_id}")
    # Email milik staging lain tidak boleh terbaca lewat site ini.
    if not _bertag(m, nama):
        raise HTTPException(status_code=404, detail=PESAN_EMAIL_HILANG)
    # Hanya teks: HTML email ditulis kode staging dan tidak pernah dikirim ke UI.
    return {"id": msg_id, "subjek": _teks(m.get("Subject"), 300) or "", "dari": _alamat(m.get("From")),
            "ke": _daftar_alamat(m.get("To")), "waktu": _teks(m.get("Date"), 40),
            "teks": _teks(m.get("Text"), 200_000) or ""}
