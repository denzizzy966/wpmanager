import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker
from staging_palsu import PembantuHostingPalsu

from wpmgr.hosting import cron
from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting.dns import Jawaban
from wpmgr.jobs.queue import buat_job
from wpmgr.models import ActivityLog, HostingVps, Job, JobType, StatusHosting
from wpmgr.staging.pembantu import GalatPembantu

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)
VPS = "169.58.91.181"


class PenanyaPalsu:
    def __init__(self, a=VPS) -> None:
        self.a = a
        self.n = 0

    def tanya(self, resolver, nama, jenis, batas):
        self.n += 1
        return Jawaban((self.a,)) if jenis == "A" else Jawaban()


def _status(sesi, h, status, **lain):
    h.status = status
    for k, v in lain.items():
        setattr(h, k, v)
    sesi.commit()


def _h(sesi, h):
    return sesi.get(HostingVps, h.id, populate_existing=True)


def test_cek_dns_mengantrekan_aktivasi_sekali(sesi, site_hosting):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    hasil = cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu())
    assert hasil == {"diperiksa": 1, "diantrekan": 1}
    job = sesi.query(Job).one()
    assert job.tipe == JobType.pindah_aktifkan and job.payload == {"tanpa_tarik_ulang": False, "manual": False}
    h = _h(sesi, site_hosting)
    assert h.dns_hasil["ok"] is True and h.dns_dicek_pada == SEKARANG
    assert sesi.query(ActivityLog).filter(ActivityLog.site_id == h.site_id).one().pesan == \
        "DNS sudah menunjuk VPS; aktivasi diantrekan otomatis"
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu()) == {"diperiksa": 1, "diantrekan": 0}
    assert sesi.query(Job).count() == 1


def test_cek_dns_belum_lolos_hanya_menyimpan_hasil(sesi, site_hosting):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu(a="93.184.216.34"))["diantrekan"] == 0
    assert _h(sesi, site_hosting).dns_hasil["ok"] is False
    assert sesi.query(Job).count() == 0


def test_cek_dns_menghormati_backoff_sertifikat(sesi, site_hosting):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns, sertifikat_gagal_kali=1,
            sertifikat_gagal_pada=SEKARANG - timedelta(minutes=30))
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu())["diantrekan"] == 0
    _status(sesi, site_hosting, StatusHosting.menunggu_dns, sertifikat_gagal_pada=SEKARANG - timedelta(hours=1))
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu())["diantrekan"] == 1


def test_cek_dns_hanya_baris_menunggu_dns_dan_tanpa_job_hosting(sesi, site_hosting):
    _status(sesi, site_hosting, StatusHosting.pratinjau)
    p = PenanyaPalsu()
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=p) == {"diperiksa": 0, "diantrekan": 0}
    assert p.n == 0
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu())["diantrekan"] == 0
    assert sesi.query(Job).count() == 1


def test_cek_dns_memakai_penanya_bawaan(sesi, site_hosting, monkeypatch):
    p = PenanyaPalsu()
    monkeypatch.setattr(dns_mod, "buat_penanya", lambda: p)
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    cron.cek_dns_semua(sesi, SEKARANG)
    assert p.n > 0


def test_cek_dns_galat_satu_situs_tidak_menghentikan_putaran(sesi, site_hosting, monkeypatch):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)

    def meledak(*a, **k):
        raise RuntimeError("rahasia /var/lib")

    monkeypatch.setattr(dns_mod, "periksa_dns", meledak)
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu()) == {"diperiksa": 0, "diantrekan": 0}
    assert sesi.query(Job).count() == 0


def test_perpanjang_sertifikat_hanya_situs_yang_dilayani(sesi, site_hosting, hosting_aktif):
    pb = PembantuHostingPalsu(hosting_aktif)
    assert cron.perpanjang_sertifikat_hosting(sesi, pb, SEKARANG) == {"berhasil": 0, "gagal": 0, "diperbarui": 0}
    _status(sesi, site_hosting, StatusHosting.aktif, dilayani_vps_pada=SEKARANG - timedelta(days=60),
            sertifikat_pada=SEKARANG - timedelta(days=60))
    pb.sertifikat_hasil = "tetap"
    assert cron.perpanjang_sertifikat_hosting(sesi, pb, SEKARANG) == {"berhasil": 1, "gagal": 0, "diperbarui": 0}
    assert _h(sesi, site_hosting).sertifikat_pada == SEKARANG - timedelta(days=60)
    pb.sertifikat_hasil = "diperbarui"
    assert cron.perpanjang_sertifikat_hosting(sesi, pb, SEKARANG)["diperbarui"] == 1
    assert _h(sesi, site_hosting).sertifikat_pada == SEKARANG
    assert ("prod_sertifikat", "toko-co-id") in pb.panggilan


def test_perpanjang_sertifikat_gagal_dicatat_dengan_pesan_tetap(sesi, site_hosting, hosting_aktif):
    _status(sesi, site_hosting, StatusHosting.aktif, dilayani_vps_pada=SEKARANG)
    pb = PembantuHostingPalsu(hosting_aktif)
    pb.gagal["prod_sertifikat"] = GalatPembantu("sertifikat", "x /var/lib/wpmgr rahasia")
    assert cron.perpanjang_sertifikat_hosting(sesi, pb, SEKARANG)["gagal"] == 1
    log = sesi.query(ActivityLog).filter(ActivityLog.site_id == site_hosting.site_id).one()
    assert (log.pesan, log.level) == (cron.PESAN_SERTIFIKAT_GAGAL, "warning")


def test_sapu_nisan_hosting_tanpa_mengikuti_symlink(site_hosting, hosting_aktif, tmp_path):
    luar = tmp_path / "luar"
    luar.mkdir()
    (luar / "penting.txt").write_bytes(b"jangan dihapus")
    akar = hosting_aktif
    akar.mkdir(parents=True, exist_ok=True)
    nisan = akar / f".hapus-{uuid.uuid4()}-{'0' * 12}"
    (nisan / "files").mkdir(parents=True)
    (nisan / "files" / "a.txt").write_bytes(b"x")
    nisan_tautan = akar / f".hapus-{uuid.uuid4()}-{'1' * 12}"
    try:
        os.symlink(luar, nisan / "files" / "tautan", target_is_directory=True)
        os.symlink(luar, nisan_tautan, target_is_directory=True)
    except OSError:
        pytest.skip("symlink tidak dapat dibuat di sistem ini")
    bukan = akar / ".hapus-lain"
    bukan.mkdir()
    situs = akar / str(site_hosting.site_id)
    situs.mkdir()

    assert cron.sapu_nisan_hosting(akar) == 2

    assert not nisan.exists() and not os.path.lexists(nisan_tautan)
    assert bukan.is_dir() and situs.is_dir()
    assert (luar / "penting.txt").read_bytes() == b"jangan dihapus"


def test_cek_dns_menyapu_nisan(sesi, site_hosting, hosting_aktif):
    nisan = hosting_aktif / f".hapus-{uuid.uuid4()}-{'2' * 12}"
    nisan.mkdir(parents=True)
    cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu())
    assert not nisan.exists()


# ---- CLI -------------------------------------------------------------------------------


@pytest.fixture
def cli(engine, monkeypatch, hosting_aktif):
    from wpmgr import cli as modul
    from wpmgr import db

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    monkeypatch.setattr(db, "engine", engine)
    pb = PembantuHostingPalsu(hosting_aktif)
    monkeypatch.setattr(modul.Pembantu, "dari_setelan", classmethod(lambda cls, s=None: pb))
    monkeypatch.setattr(dns_mod, "buat_penanya", lambda: PenanyaPalsu())
    return modul


def test_cli_hosting_dilewati_bila_kunci_dipegang(cli, engine):
    from wpmgr.kunci import KUNCI_HOSTING_DNS, KUNCI_HOSTING_SERTIFIKAT, kunci_advisory

    with kunci_advisory(engine, KUNCI_HOSTING_DNS) as a, kunci_advisory(engine, KUNCI_HOSTING_SERTIFIKAT) as b:
        assert a and b
        assert cli.hosting_cek_dns() is None
        assert cli.renew_hosting_certs() is None


def test_cli_hosting_dilewati_bila_fitur_mati(cli, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_HOSTING_IPV4", raising=False)
    get_settings.cache_clear()
    assert cli.hosting_cek_dns() is None
    assert cli.renew_hosting_certs() is None


def test_cli_main_mengenal_subperintah_hosting(cli, sesi, site_hosting):
    for perintah in ("hosting-cek-dns", "renew-hosting-certs"):
        assert cli.main([perintah]) == 0
