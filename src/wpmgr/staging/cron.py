"""Perintah cron staging (spec §11): jeda otomatis, sertifikat, pemangkasan disk.

Pemangkasan menghapus direktori di bawah WPMGR_STAGING_DIR. Pohon `files/`,
`log/`, dan `ekspor/` ditulis container staging yang menjalankan kode
produksi (tidak dipercaya) sebagai UID dashboard, jadi bisa berisi symlink
ke mana saja yang bisa ditulis dashboard (data PostgreSQL, kode, `.env`).
Karena itu setiap penghapusan lewat `dorong.hapus_dir_staging` dan helper
`aman`: path disusun dari nama direktori berbentuk UUID, diperiksa dengan
`jalur_di_dalam`, target teratas harus direktori sungguhan (lstat), dan
rmtree tidak pernah mengikuti symlink.
"""

import logging
import os
import stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from wpmgr.config import get_settings
from wpmgr.models import (
    JOB_STAGING,
    Job,
    JobStatus,
    Staging,
    StagingSnapshot,
    StatusStaging,
)
from wpmgr.staging import umum
from wpmgr.staging.aman import (
    POLA_NAMA,
    TOLERANSI_JAM,
    PathTidakAman,
    adalah_tautan,
    hapus_berkas,
    hapus_tautan,
    jalur_di_dalam,
)
from wpmgr.staging.dorong import (
    STATUS_SNAPSHOT_SAH,
    hapus_dir_staging,
    pangkas_snapshot,
)
from wpmgr.staging.pembantu import GalatPembantu

log = logging.getLogger("wpmgr.staging.cron")
UMUR_SEMENTARA = timedelta(hours=24)
PESAN_SERTIFIKAT_GAGAL = "Sertifikat staging belum dapat diperpanjang; lihat log server."
AKHIRAN_ROUTER = ("rahasia", "htpasswd")


def _ada_job_staging(sesi, site_id) -> bool:
    return sesi.scalar(select(Job.id).where(
        Job.site_id == site_id, Job.tipe.in_(JOB_STAGING),
        Job.status.in_((JobStatus.pending, JobStatus.running))).limit(1)) is not None


# ---- jeda otomatis ------------------------------------------------------------


def _waktu_sah(t: datetime | None, sekarang: datetime) -> datetime | None:
    """None untuk waktu masa depan di luar toleransi (seperti `aman.waktu_penanda`).

    Jam yang sempat melompat ke depan tidak boleh membuat staging tampak baru
    diakses selamanya; nilai seperti itu diabaikan, bukan dipakai.
    """
    if t is None or t > sekarang + TOLERANSI_JAM:
        return None
    return t


def jeda_otomatis(sesi, pb, sekarang: datetime) -> int:
    """Jeda staging `siap` yang tidak diakses lebih lama dari WPMGR_STAGING_JEDA_HARI.

    Akses terakhir = maks dari `dibuka_pada`, `ditarik_pada`, dan mtime log
    akses router. Staging dengan status lain (termasuk `gagal`, apa pun
    `gagal_asal`-nya) atau dengan job staging tertunda/berjalan dilewati.
    """
    hari = get_settings().staging_jeda_hari
    batas = sekarang - timedelta(days=hari)
    try:
        status = pb.status()
    except GalatPembantu as exc:
        # Tanpa log akses, staging yang sedang dipakai bisa tampak diam.
        log.warning("Status server staging tidak terbaca; jeda otomatis dilewati: %s", exc.pesan)
        return 0
    maks_akses = (sekarang + TOLERANSI_JAM).timestamp()
    n = 0
    ids = sesi.scalars(select(Staging.id).where(Staging.aktif.is_(True), Staging.status == StatusStaging.siap)
                       .order_by(Staging.nama)).all()
    for sid in ids:
        st = sesi.get(Staging, sid, populate_existing=True)
        if st is None or not st.aktif or st.status != StatusStaging.siap:
            sesi.commit()
            continue
        dibuka = _waktu_sah(st.dibuka_pada, sekarang)
        akses = status.akses.get(st.nama)
        if akses is not None and akses <= maks_akses:
            waktu_akses = datetime.fromtimestamp(akses, tz=timezone.utc)
            if dibuka is None or waktu_akses > dibuka:
                st.dibuka_pada = dibuka = waktu_akses
        terakhir = max((t for t in (dibuka, _waktu_sah(st.ditarik_pada, sekarang)) if t is not None), default=None)
        if (terakhir is not None and terakhir >= batas) or _ada_job_staging(sesi, st.site_id):
            sesi.commit()
            continue
        nama, site_id = st.nama, st.site_id
        sesi.commit()
        try:
            pb.jeda(nama)
        except (GalatPembantu, ValueError) as exc:
            log.warning("Staging %s tidak dapat dijeda otomatis: %s", nama, getattr(exc, "pesan", "nama tidak sah"))
            continue
        st = sesi.get(Staging, sid, populate_existing=True, with_for_update=True)
        if st is None:
            sesi.commit()
            continue
        # Container sudah berhenti: `aktif` mengikuti kenyataan. Status hanya
        # siap -> dijeda; bila job staging sempat dimulai, statusnya milik job itu.
        st.aktif = False
        if st.status == StatusStaging.siap:
            st.status = StatusStaging.dijeda
        umum.catat_aktivitas(sesi, site_id, None, f"Staging dijeda otomatis setelah {hari} hari tanpa akses")
        sesi.commit()
        n += 1
    return n


# ---- sertifikat ---------------------------------------------------------------


def perpanjang_sertifikat(sesi, pb, sekarang: datetime) -> dict:
    hasil = {"berhasil": 0, "gagal": 0}
    ids = sesi.scalars(select(Staging.id).where(Staging.ditarik_pada.is_not(None)).order_by(Staging.nama)).all()
    for sid in ids:
        st = sesi.get(Staging, sid, populate_existing=True)
        if st is None:
            continue
        nama, site_id = st.nama, st.site_id
        sesi.commit()
        try:
            pb.sertifikat(nama)
        except (GalatPembantu, ValueError) as exc:
            hasil["gagal"] += 1
            # Teks galat hanya ke log server; log aktivitas (UI) memakai pesan tetap.
            log.warning("Sertifikat staging %s gagal diperpanjang: %s (%s)", nama,
                        getattr(exc, "pesan", "nama tidak sah"), getattr(exc, "kode", "argumen"))
            umum.catat_aktivitas(sesi, site_id, None, PESAN_SERTIFIKAT_GAGAL, level="warning")
            sesi.commit()
            continue
        hasil["berhasil"] += 1
        st = sesi.get(Staging, sid, populate_existing=True)
        # certbot --keep-until-expiring tidak memberi tahu apakah sertifikat
        # benar-benar diperbarui; yang dicatat hanya penerbitan pertama.
        if st is not None and st.sertifikat_pada is None:
            st.sertifikat_pada = sekarang
        sesi.commit()
    return hasil


# ---- pemangkasan --------------------------------------------------------------


def _uuid(nama: str) -> bool:
    try:
        return str(uuid.UUID(nama)) == nama
    except ValueError:
        return False


def _dir_nyata(path: Path) -> bool:
    """Direktori sungguhan menurut lstat: symlink dan junction tidak pernah dihitung direktori."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not adalah_tautan(st)


def _tua(path: Path, sekarang: datetime) -> bool:
    try:
        mtime = os.lstat(path).st_mtime
    except OSError:
        return False
    return datetime.fromtimestamp(mtime, tz=timezone.utc) < sekarang - UMUR_SEMENTARA


def _ada_staging(sesi, site_id) -> bool:
    # Dibaca ulang tepat sebelum menghapus: staging yang baru dibuat sesudah
    # putaran dimulai tidak boleh dianggap yatim.
    return sesi.scalar(select(Staging.id).where(Staging.site_id == site_id)) is not None


def _ada_snapshot(sesi, site_id) -> bool:
    return sesi.scalar(select(StagingSnapshot.id).where(
        StagingSnapshot.site_id == site_id, StagingSnapshot.status.in_(STATUS_SNAPSHOT_SAH)).limit(1)) is not None


def _kosongkan_kecuali_snapshot(akar: Path, nama: str) -> bool:
    """Hapus isi `<nama>/` selain `snapshot/`; True bila ada yang dihapus.

    Kembalikan (staging_kembalikan) tetap berjalan tanpa baris Staging, jadi
    snapshot yang masih sah harus bertahan.
    """
    dihapus = False
    try:
        anak = sorted(os.listdir(akar / nama))
    except OSError:
        return False
    for a in anak:
        if a == "snapshot":
            continue
        rel = f"{nama}/{a}"
        try:
            st = os.lstat(akar / rel)
            if adalah_tautan(st):
                # Tautannya saja yang dihapus; targetnya tidak pernah disentuh.
                hapus_tautan(akar, rel)
            elif stat.S_ISDIR(st.st_mode):
                hapus_dir_staging(rel)
            else:
                hapus_berkas(akar, rel)
        except (OSError, PathTidakAman) as exc:
            log.warning("Sisa staging %s tidak dapat dihapus: %s", rel, type(exc).__name__)
            continue
        dihapus = True
    return dihapus


def _pangkas_direktori(sesi, akar: Path, sekarang: datetime, hasil: dict) -> None:
    try:
        daftar = sorted(os.listdir(akar))
    except OSError:
        return
    for nama in daftar:
        # Hanya nama UUID kanonis: `router/` dan apa pun yang lain tidak pernah disentuh.
        if not _uuid(nama) or not _dir_nyata(akar / nama):
            continue
        site_id = uuid.UUID(nama)
        # Job staging (termasuk kembalikan tanpa baris Staging) masih memakai
        # tarik/, dorong/, dan snapshot-nya.
        if _ada_job_staging(sesi, site_id):
            continue
        if not _ada_staging(sesi, site_id):
            if _ada_snapshot(sesi, site_id):
                if _kosongkan_kecuali_snapshot(akar, nama):
                    hasil["direktori"] += 1
            else:
                hapus_dir_staging(nama)
                hasil["direktori"] += 1
            continue
        for sub in ("tarik", "dorong"):
            rel = f"{nama}/{sub}"
            try:
                sementara = jalur_di_dalam(akar, rel)
            except PathTidakAman:
                continue
            if _dir_nyata(sementara) and _tua(sementara, sekarang):
                hapus_dir_staging(rel)
                hasil["sementara"] += 1


def _pangkas_router(sesi, pb, akar: Path, hasil: dict) -> None:
    router = akar / "router"
    if not _dir_nyata(router):
        return
    try:
        daftar = sorted(os.listdir(router))
    except OSError:
        return
    for berkas in daftar:
        dasar, titik, akhiran = berkas.rpartition(".")
        if not titik or akhiran not in AKHIRAN_ROUTER or not POLA_NAMA.fullmatch(dasar):
            continue
        if sesi.scalar(select(Staging.id).where(Staging.nama == dasar)) is not None:
            continue
        f = router / berkas
        try:
            st = os.lstat(f)
        except OSError:
            continue
        if adalah_tautan(st) or not stat.S_ISREG(st.st_mode):
            continue
        f.unlink(missing_ok=True)
        hasil["router"] += 1
    if hasil["router"]:
        try:
            pb.router_muat()
        except GalatPembantu as exc:
            log.warning("router-muat setelah pemangkasan gagal: %s", exc.pesan)


def pangkas_staging(sesi, pb, sekarang: datetime) -> dict:
    s = get_settings()
    akar = s.jalur_staging
    hasil = {"snapshot": 0, "direktori": 0, "sementara": 0, "router": 0}

    site_snapshot = set(sesi.scalars(select(StagingSnapshot.site_id).where(
        StagingSnapshot.status.in_(STATUS_SNAPSHOT_SAH))).all())
    for site_id in sorted(site_snapshot, key=str):
        if _ada_job_staging(sesi, site_id):
            continue
        hasil["snapshot"] += pangkas_snapshot(sesi, site_id, s.staging_snapshot)
        sesi.commit()

    if _dir_nyata(akar):
        _pangkas_direktori(sesi, akar, sekarang, hasil)
        _pangkas_router(sesi, pb, akar, hasil)
    return hasil
