import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wpmgr.errors import SiteError
from wpmgr.hosting.backup import (
    HasilBackup,
    TujuanLokal,
    cek_disk_backup,
    pilih_simpan,
    stempel_dari,
    tujuan_dari_setelan,
    urai_manifest_backup,
)
from wpmgr.staging.pembantu import GalatPembantu, StatusProd

SID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
H = SimpleNamespace(site_id=SID, nama="toko-co-id")
ST = "20261003T023000Z"


def _b(id_, waktu):
    return SimpleNamespace(id=id_, dibuat_pada=waktu)


def _hari(tahun, bulan, tanggal, jam=2):
    return datetime(tahun, bulan, tanggal, jam, 30, tzinfo=timezone.utc)


def _manifest(**ganti):
    data = {"versi": 1, "site_id": str(SID), "nama": "toko-co-id", "domain": "toko.co.id", "stempel": ST,
            "versi_php": "8.1", "prefix": "wp_", "ukuran_db": 100, "ukuran_file": 200,
            "sha256_db": "a" * 64, "sha256_file": "b" * 64}
    data.update(ganti)
    return json.dumps(data) + "\n"


def test_stempel_utc():
    assert stempel_dari(datetime(2026, 10, 3, 2, 30, tzinfo=timezone.utc)) == ST
    assert stempel_dari(datetime(2026, 10, 3, 9, 30, tzinfo=timezone(timedelta(hours=7)))) == ST


def test_urai_manifest_sah():
    assert urai_manifest_backup(_manifest(), H, ST) == HasilBackup(100, 200, "a" * 64, "b" * 64)


@pytest.mark.parametrize("teks", [
    "bukan json", "[]", _manifest(versi=2), _manifest(site_id=str(uuid.uuid4())), _manifest(nama="lain"),
    _manifest(stempel="20261004T023000Z"), _manifest(ukuran_db=-1), _manifest(ukuran_file="200"),
    _manifest(ukuran_db=True), _manifest(sha256_db="A" * 64), _manifest(sha256_file="b" * 63),
    _manifest(domain="x" * 5000),
])
def test_urai_manifest_rusak_atau_melebihi_batas_ditolak(teks):
    with pytest.raises(GalatPembantu) as e:
        urai_manifest_backup(teks, H, ST)
    assert e.value.kode == "backup"


def test_urai_manifest_bukan_teks_ditolak_dengan_pesan_tetap():
    from wpmgr.hosting.backup import PESAN_MANIFEST

    for teks in (None, b"{}", 1):
        with pytest.raises(GalatPembantu) as e:
            urai_manifest_backup(teks, H, ST)
        assert e.value.pesan == PESAN_MANIFEST


def test_tujuan_lokal_memanggil_subperintah():
    class Pb:
        def __init__(self):
            self.panggilan = []

        def prod_backup(self, nama, stempel):
            self.panggilan.append(("prod_backup", nama, stempel))
            return _manifest()

        def prod_backup_hapus(self, nama, stempel):
            self.panggilan.append(("prod_backup_hapus", nama, stempel))

    pb = Pb()
    t = TujuanLokal(pb)
    assert t.kode == "lokal"
    assert t.buat(H, ST).sha256_db == "a" * 64
    t.hapus(H, ST)
    assert pb.panggilan == [("prod_backup", "toko-co-id", ST), ("prod_backup_hapus", "toko-co-id", ST)]


def test_tujuan_dari_setelan(monkeypatch):
    from wpmgr.config import get_settings

    assert isinstance(tujuan_dari_setelan(object()), TujuanLokal)
    monkeypatch.setenv("WPMGR_BACKUP_TUJUAN", "s3")
    get_settings.cache_clear()
    with pytest.raises(SiteError):
        tujuan_dari_setelan(object())


def test_cek_disk_backup():
    gb = 1024**3
    assert cek_disk_backup(StatusProd(0, 0, 0, 100 * gb, 50 * gb, {}), 10 * gb) is None
    pesan = cek_disk_backup(StatusProd(0, 0, 0, 100 * gb, 20 * gb, {}), 10 * gb)
    assert pesan.startswith("Sisa disk backup sesudah backup akan 10,0 GB (10% dari 100,0 GB)")


def test_cek_disk_backup_memakai_disk_backup_bukan_disk_hosting():
    # Preflight M6: satu rumus (rencana.cek_disk) atas backup_total/backup_bebas.
    gb = 1024**3
    assert cek_disk_backup(StatusProd(0, 100 * gb, 1 * gb, 100 * gb, 90 * gb, {}), 10 * gb) is None
    assert cek_disk_backup(StatusProd(0, 100 * gb, 90 * gb, 100 * gb, 1 * gb, {}), 10 * gb) is not None


def test_cek_disk_tarik_tetap_sama():
    from wpmgr.staging.rencana import cek_disk

    gb = 1024**3
    pesan = cek_disk(SimpleNamespace(disk_total=100 * gb, disk_bebas=20 * gb), 10 * gb)
    assert pesan.startswith("Sisa disk sesudah tarik akan 10,0 GB (10% dari 100,0 GB)")


def test_pilih_simpan_celah_hari():
    # Backup gagal tanggal 5-9: yang disimpan 7 TANGGAL berbeda terbaru (13, 12, 11, 10, 4, 3, 2),
    # bukan 7 hari kalender terakhir (yang hanya menyisakan 10-13).
    hari = [1, 2, 3, 4, 10, 11, 12, 13]
    backups = [_b(d, _hari(2026, 10, d)) for d in hari]
    simpan = pilih_simpan(backups, harian=7, mingguan=0)
    assert simpan == {13, 12, 11, 10, 4, 3, 2}


def test_pilih_simpan_satu_per_tanggal_dan_manual_ikut_dihitung():
    backups = [_b(1, _hari(2026, 10, 5, 2)), _b(2, _hari(2026, 10, 5, 14)), _b(3, _hari(2026, 10, 4))]
    assert pilih_simpan(backups, harian=7, mingguan=0) == {2, 3}


def test_pilih_simpan_tanggal_utc_bukan_wib():
    # 2026-10-05 23:30 UTC = 2026-10-06 06:30 WIB: tetap tanggal UTC 5, jadi satu tanggal dengan 5 Oktober 02:30.
    wib = timezone(timedelta(hours=7))
    backups = [_b(1, _hari(2026, 10, 5, 2)), _b(2, datetime(2026, 10, 6, 6, 30, tzinfo=wib))]
    assert pilih_simpan(backups, harian=1, mingguan=0) == {2}
    assert pilih_simpan(backups, harian=2, mingguan=0) == {2}


def test_pilih_simpan_mingguan():
    # 6 minggu, satu backup per hari: 7 harian + 4 mingguan (terbaru per minggu ISO).
    awal = _hari(2026, 9, 1)
    backups = [_b(i, awal + timedelta(days=i)) for i in range(42)]
    simpan = pilih_simpan(backups, harian=7, mingguan=4)
    harian = {i for i in range(35, 42)}
    mingguan = {max(b.id for b in backups if b.dibuat_pada.isocalendar()[:2] == mg)
                for mg in sorted({b.dibuat_pada.isocalendar()[:2] for b in backups}, reverse=True)[:4]}
    assert simpan == harian | mingguan


def test_pilih_simpan_pergantian_tahun_iso():
    # 2027-01-01 (Jumat) masih minggu ISO 53 tahun 2026; 2027-01-04 minggu 1 tahun 2027.
    backups = [_b(1, _hari(2026, 12, 28)), _b(2, _hari(2027, 1, 1)), _b(3, _hari(2027, 1, 4))]
    assert pilih_simpan(backups, harian=1, mingguan=3) == {3, 2}


def test_pilih_simpan_terbaru_selalu_disimpan():
    backups = [_b(1, _hari(2026, 10, 1)), _b(2, _hari(2026, 10, 2))]
    assert pilih_simpan(backups, harian=1, mingguan=0) == {2}
    assert pilih_simpan([], harian=7, mingguan=4) == set()
