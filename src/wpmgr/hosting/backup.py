"""Backup situs yang dihosting VPS (spec Lapis 4 §9) dan job backup_hosting (§10.5).

Isi backup hanya bisa dibaca root (BACKUP_DIR 0700); dashboard hanya
menyimpan metadata dari manifest yang dicetak skrip pembantu, setelah
divalidasi ketat. Pemangkasan hanya berjalan di akhir backup yang SUKSES:
backup yang gagal tidak pernah memangkas apa pun (RF6). Backup tidak pernah
mengubah status hosting; kegagalannya hanya mengisi `backup_gagal_pada`
(pembungkus `hosting.umum.jalankan_hosting`).
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from wpmgr.config import get_settings
from wpmgr.hosting import umum as hu
from wpmgr.models import HostingBackup, HostingVps
from wpmgr.staging import umum as stg
from wpmgr.staging.aman import POLA_SHA256
from wpmgr.staging.pembantu import GalatPembantu
from wpmgr.staging.rencana import cek_disk, format_byte

log = logging.getLogger("wpmgr.hosting.backup")

POLA_STEMPEL = re.compile(r"[0-9]{8}T[0-9]{6}Z")
BATAS_MANIFEST = 4096
STATUS_TERSEDIA = "tersedia"
STATUS_DIPANGKAS = "dipangkas"
PESAN_MANIFEST = "Keluaran backup dari skrip pembantu tidak sah."
PESAN_BELUM_DILAYANI = "Backup hanya untuk situs yang sudah dilayani VPS."
PESAN_TUJUAN = "Tujuan backup (WPMGR_BACKUP_TUJUAN) tidak dikenal."


@dataclass(frozen=True)
class HasilBackup:
    ukuran_db: int
    ukuran_file: int
    sha256_db: str
    sha256_file: str


class TujuanBackup(Protocol):
    kode: str  # disimpan di hosting_backup.tujuan

    def buat(self, hosting, stempel: str) -> HasilBackup: ...

    def hapus(self, hosting, stempel: str) -> None: ...  # idempoten


def stempel_dari(waktu: datetime) -> str:
    return waktu.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def urai_manifest_backup(teks, hosting, stempel: str) -> HasilBackup:
    """Manifest dari `prod-backup`: harus milik situs, nama, dan stempel ini persis."""
    if not isinstance(teks, str) or len(teks.encode("utf-8")) > BATAS_MANIFEST:
        raise GalatPembantu("backup", PESAN_MANIFEST)
    try:
        data = json.loads(teks)
    except ValueError:
        raise GalatPembantu("backup", PESAN_MANIFEST) from None
    if not isinstance(data, dict) or data.get("versi") != 1 or data.get("site_id") != str(hosting.site_id) \
            or data.get("nama") != hosting.nama or data.get("stempel") != stempel:
        raise GalatPembantu("backup", PESAN_MANIFEST)
    nilai = {}
    for kunci in ("ukuran_db", "ukuran_file"):
        v = data.get(kunci)
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 2**53:
            raise GalatPembantu("backup", PESAN_MANIFEST)
        nilai[kunci] = v
    for kunci in ("sha256_db", "sha256_file"):
        v = data.get(kunci)
        if not isinstance(v, str) or not POLA_SHA256.fullmatch(v):
            raise GalatPembantu("backup", PESAN_MANIFEST)
        nilai[kunci] = v
    return HasilBackup(**nilai)


class TujuanLokal:
    """Disk VPS sendiri lewat `prod-backup`/`prod-backup-hapus` (spec §9.1)."""

    kode = "lokal"

    def __init__(self, pb) -> None:
        self.pb = pb

    def buat(self, hosting, stempel: str) -> HasilBackup:
        return urai_manifest_backup(self.pb.prod_backup(hosting.nama, stempel), hosting, stempel)

    def hapus(self, hosting, stempel: str) -> None:
        self.pb.prod_backup_hapus(hosting.nama, stempel)


# Tujuan di luar VPS (S3/R2) menjadi implementasi berikutnya tanpa mengubah antarmuka.
TUJUAN = {"lokal": TujuanLokal}


def tujuan_dari_setelan(pb) -> TujuanBackup:
    kelas = TUJUAN.get(get_settings().backup_tujuan)
    if kelas is None:
        raise stg.galat_gagal(PESAN_TUJUAN)
    return kelas(pb)


def cek_disk_backup(status, tambahan: int) -> str | None:
    """Sisa disk BACKUP_DIR sesudah backup >= 15% (spec §10.5); taksiran tanpa kompresi, konservatif.

    Rumus yang sama dengan tarik (`rencana.cek_disk`, preflight M6), atas
    disk backup, bukan disk HOSTING_DIR.
    """
    disk = SimpleNamespace(disk_total=status.backup_total, disk_bebas=status.backup_bebas)
    return cek_disk(disk, tambahan, awalan="Sisa disk backup sesudah backup")


def pilih_simpan(backups, harian: int = 7, mingguan: int = 4) -> set:
    """Id backup yang disimpan (spec §9.3): terbaru per tanggal UTC untuk `harian` tanggal
    berbeda terbaru, terbaru per minggu ISO untuk `mingguan` minggu berbeda terbaru, dan
    backup terbaru selalu. Tanggal yang punya backup, bukan hari kalender: bila backup
    gagal beberapa hari, backup lama bertahan lebih lama."""
    urut = sorted(backups, key=lambda b: (b.dibuat_pada, b.id), reverse=True)
    if not urut:
        return set()
    simpan = {urut[0].id}
    tanggal: set = set()
    minggu: set = set()
    for b in urut:
        t = b.dibuat_pada.astimezone(timezone.utc)
        if t.date() not in tanggal and len(tanggal) < harian:
            tanggal.add(t.date())
            simpan.add(b.id)
        mg = tuple(t.isocalendar())[:2]
        if mg not in minggu and len(minggu) < mingguan:
            minggu.add(mg)
            simpan.add(b.id)
    return simpan


def pangkas(sesi, h: HostingVps, tujuan: TujuanBackup, job=None) -> int:
    """Hapus backup di luar retensi; hapus yang gagal tetap `tersedia` dan dicoba pada backup berikutnya.

    Hanya dipanggil sesudah backup baru tersimpan. `job`: klaimnya
    diperpanjang (detak) sebelum setiap penghapusan, karena tiap
    `prod-backup-hapus` bisa sampai 2 menit. Sengaja bukan `titik_potongan`
    dengan baris hosting: backup tidak bisa dibatalkan, dan sisa
    `batal_diminta_pada` tidak boleh membuatnya berhenti (carry Task 6).
    """
    s = get_settings()
    baris = sesi.scalars(select(HostingBackup).where(
        HostingBackup.site_id == h.site_id, HostingBackup.tujuan == tujuan.kode,
        HostingBackup.status == STATUS_TERSEDIA)).all()
    simpan = pilih_simpan(baris, s.backup_harian, s.backup_mingguan)
    n = 0
    for b in baris:
        if b.id in simpan:
            continue
        stempel = b.stempel
        sesi.commit()
        if job is not None:
            stg.detak(sesi, job)
        try:
            tujuan.hapus(h, stempel)
        except (GalatPembantu, ValueError) as exc:
            # Termasuk keluar 3 (symlink/bukan milik root, atau kunci sibuk): dicoba lagi lain kali.
            log.warning("Backup %s situs %s gagal dipangkas: %s", stempel, h.nama, getattr(exc, "kode", "argumen"))
            continue
        b.status = STATUS_DIPANGKAS
        sesi.commit()
        n += 1
    return n


def backup_hosting(sesi, job, site, h: HostingVps, pb) -> dict:
    if h.dilayani_vps_pada is None:
        raise stg.GalatDitolakTanpaUbah(PESAN_BELUM_DILAYANI)
    stempel = stg.kemajuan(job).get("stempel")
    if not (isinstance(stempel, str) and POLA_STEMPEL.fullmatch(stempel)):
        # Dari mulai percobaan pertama, disimpan supaya percobaan ulang memakai stempel yang sama
        # (prod-backup idempoten per stempel).
        stempel = stempel_dari(hu.sekarang())
        stg.simpan_kemajuan(sesi, job, stempel=stempel, tahap_backup="backup")
    status = pb.prod_status()
    pesan = cek_disk_backup(status, h.ukuran_file + h.ukuran_db)
    if pesan:
        raise stg.GalatDitolakTanpaUbah(pesan)
    tujuan = tujuan_dari_setelan(pb)
    with stg.detak_latar(sesi, job):
        hasil = tujuan.buat(h, stempel)
    sesi.execute(insert(HostingBackup).values(
        site_id=h.site_id, job_id=job.id, tujuan=tujuan.kode, stempel=stempel, status=STATUS_TERSEDIA,
        manual=bool((job.payload or {}).get("manual")), ukuran_db=hasil.ukuran_db, ukuran_file=hasil.ukuran_file,
        sha256_db=hasil.sha256_db, sha256_file=hasil.sha256_file,
    ).on_conflict_do_nothing(constraint="uq_hosting_backup_stempel"))
    sesi.commit()
    dipangkas = pangkas(sesi, h, tujuan, job)
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    baris.backup_terakhir_pada = hu.sekarang()
    baris.backup_gagal_pada = None
    ringkas = {"stempel": stempel, "ukuran_db": hasil.ukuran_db, "ukuran_file": hasil.ukuran_file,
               "dipangkas": dipangkas}
    stg.catat_aktivitas(sesi, h.site_id, job, "Backup situs dibuat", {
        **ringkas, "ukuran_teks": format_byte(hasil.ukuran_db + hasil.ukuran_file)})
    sesi.commit()
    return ringkas


def tangani_backup_hosting(sesi, job, klien) -> dict:
    """Handler worker; tidak memakai klien site."""
    def inti(sesi, job, site, h):
        return backup_hosting(sesi, job, site, h, stg.buat_pembantu())

    return hu.jalankan_hosting(sesi, job, inti, "Backup situs")
