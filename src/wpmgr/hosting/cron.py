"""Perintah cron hosting VPS (spec Lapis 4 §15).

Kontrak kunci sama dengan route hosting (`hosting.umum.kunci_hosting`): baris
`sites` FOR NO KEY UPDATE lalu `hosting_vps` FOR UPDATE, dipegang sampai
commit. Panggilan jaringan (cek DNS) dan skrip pembantu selalu di luar kunci;
keadaan diperiksa ulang sesudah kunci diambil. Teks galat hanya ke log
server; log aktivitas memakai pesan tetap. Galat satu situs tidak
menghentikan situs lain.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from wpmgr.config import get_settings
from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting.umum import kunci_hosting
from wpmgr.models import JOB_HOSTING, HostingVps, Job, JobStatus, JobType, StatusHosting
from wpmgr.staging import umum as stg
from wpmgr.staging.cron import POLA_NISAN, _dir_nyata
from wpmgr.staging.dorong import hapus_nisan
from wpmgr.staging.pembantu import GalatPembantu

__all__ = ["PESAN_SERTIFIKAT_GAGAL", "ada_job_hosting", "cek_dns_semua", "kunci_hosting",
           "perpanjang_sertifikat_hosting", "sapu_nisan_hosting"]

log = logging.getLogger("wpmgr.hosting.cron")
PESAN_SERTIFIKAT_GAGAL = "Sertifikat domain belum dapat diperpanjang; lihat log server."


def ada_job_hosting(sesi, site_id) -> bool:
    return sesi.scalar(select(Job.id).where(
        Job.site_id == site_id, Job.tipe.in_(JOB_HOSTING),
        Job.status.in_((JobStatus.pending, JobStatus.running))).limit(1)) is not None


def sapu_nisan_hosting(akar: Path) -> int:
    """Hapus nisan `.hapus-*` sisa putaran yang mati di akar hosting; kembalikan jumlah yang dicoba.

    Penghapusan lewat `hapus_nisan` (symlink-safe: lstat, tautan dihapus tanpa
    menyentuh target, rmtree tidak mengikuti symlink). Akar yang berupa
    symlink dilewati.
    """
    if not _dir_nyata(akar):
        return 0
    try:
        daftar = sorted(os.listdir(akar))
    except OSError:
        return 0
    n = 0
    for nama in daftar:
        if POLA_NISAN.fullmatch(nama):
            hapus_nisan(akar, nama)
            n += 1
    return n


def _periksa_satu(sesi, hid, sekarang: datetime, penanya, hasil: dict) -> None:
    h = sesi.get(HostingVps, hid, populate_existing=True)
    if h is None or h.status != StatusHosting.menunggu_dns:
        sesi.commit()
        return
    site_id = h.site_id
    sasaran = SimpleNamespace(domain=h.domain, dengan_www=h.dengan_www)
    sesi.commit()  # jaringan di luar kunci dan di luar transaksi terbuka
    cek = dns_mod.periksa_dns(sasaran, penanya=penanya, sekarang=sekarang)
    hasil["diperiksa"] += 1
    h = kunci_hosting(sesi, site_id)
    if h is None or h.status != StatusHosting.menunggu_dns:
        sesi.commit()
        return
    h.dns_hasil = cek.ke_json()
    h.dns_dicek_pada = sekarang
    if not (cek.ok and dns_mod.backoff_mengizinkan(h, sekarang, manual=False)) or ada_job_hosting(sesi, site_id):
        sesi.commit()
        return
    stg.catat_aktivitas(sesi, site_id, None, "DNS sudah menunjuk VPS; aktivasi diantrekan otomatis")
    # `manual` False: backoff sertifikat dalam job bergantung padanya.
    sesi.add(Job(site_id=site_id, tipe=JobType.pindah_aktifkan,
                 payload={"tanpa_tarik_ulang": False, "manual": False}))
    try:
        sesi.commit()
        hasil["diantrekan"] += 1
    except IntegrityError:
        # uq_jobs_hosting_aktif: job hosting lain baru saja diantrekan. Hasil DNS ikut dibuang;
        # putaran berikutnya menyimpannya lagi.
        sesi.rollback()


def cek_dns_semua(sesi, sekarang: datetime, penanya=None) -> dict:
    """Setiap baris `menunggu_dns`: cek DNS; bila lolos, backoff mengizinkan, dan tidak ada
    job hosting aktif, antrekan `pindah_aktifkan` (spec §15). Juga menyapu nisan hosting."""
    hasil = {"diperiksa": 0, "diantrekan": 0}
    ids = sesi.scalars(select(HostingVps.id).where(HostingVps.status == StatusHosting.menunggu_dns)
                       .order_by(HostingVps.nama)).all()
    sesi.commit()
    for hid in ids:
        try:
            _periksa_satu(sesi, hid, sekarang, penanya, hasil)
        except Exception as exc:  # noqa: BLE001 isolasi per situs
            sesi.rollback()
            log.warning("Cek DNS hosting %s gagal: %s", hid, type(exc).__name__)
    try:
        sapu_nisan_hosting(get_settings().jalur_hosting)
    except Exception as exc:  # noqa: BLE001 isolasi per situs
        log.warning("Sapu nisan hosting gagal: %s", type(exc).__name__)
    return hasil


def perpanjang_sertifikat_hosting(sesi, pb, sekarang: datetime) -> dict:
    """prod-sertifikat untuk setiap situs yang sudah dilayani VPS; skrip me-reload nginx hanya bila berubah."""
    hasil = {"berhasil": 0, "gagal": 0, "diperbarui": 0}
    ids = sesi.scalars(select(HostingVps.id).where(HostingVps.dilayani_vps_pada.is_not(None))
                       .order_by(HostingVps.nama)).all()
    sesi.commit()
    for hid in ids:
        try:
            h = sesi.get(HostingVps, hid, populate_existing=True)
            if h is None:
                sesi.commit()
                continue
            nama, site_id = h.nama, h.site_id
            sesi.commit()
            try:
                keluaran = pb.prod_sertifikat(nama)
            except (GalatPembantu, ValueError) as exc:
                hasil["gagal"] += 1
                log.warning("Sertifikat domain %s gagal diperpanjang: %s", nama, getattr(exc, "kode", "argumen"))
                stg.catat_aktivitas(sesi, site_id, None, PESAN_SERTIFIKAT_GAGAL, level="warning")
                sesi.commit()
                continue
            hasil["berhasil"] += 1
            if keluaran in ("terbit", "diperbarui"):
                hasil["diperbarui"] += 1
                h = sesi.get(HostingVps, hid, populate_existing=True)
                if h is not None:
                    h.sertifikat_pada = sekarang
                sesi.commit()
        except Exception as exc:  # noqa: BLE001 isolasi per situs
            sesi.rollback()
            log.warning("Perpanjangan sertifikat hosting %s gagal: %s", hid, type(exc).__name__)
    return hasil
