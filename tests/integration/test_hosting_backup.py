import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from staging_palsu import GB, PembantuHostingPalsu

from wpmgr.errors import TRANSIENT, SiteError
from wpmgr.hosting import backup, cron
from wpmgr.hosting import umum as hu
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import akan_diulang, buat_job
from wpmgr.models import (
    ActivityLog,
    HostingBackup,
    HostingVps,
    Job,
    JobStatus,
    JobType,
    StatusHosting,
)
from wpmgr.staging import umum
from wpmgr.staging.pembantu import GalatPembantu, StatusProd
from wpmgr.web import routes_hosting

pytestmark = pytest.mark.integration


@pytest.fixture
def aktif(sesi, site_hosting):
    sekarang = datetime.now(timezone.utc)
    site_hosting.status = StatusHosting.aktif
    site_hosting.dilayani_vps_pada = sekarang - timedelta(days=20)
    site_hosting.aktif_pada = sekarang - timedelta(days=20)
    sesi.commit()
    return site_hosting


@pytest.fixture
def pb(hosting_aktif, aktif, monkeypatch):
    palsu = PembantuHostingPalsu(hosting_aktif)
    palsu.state["toko-co-id"] = {"site_id": str(aktif.site_id), "domain": "toko.co.id", "mode": "aktif",
                                 "prefix": "wp_", "php": "8.1"}
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


def _lama(sesi, h, n):
    """n backup lama, satu per hari mulai 30 hari lalu."""
    awal = datetime.now(timezone.utc) - timedelta(days=30)
    for i in range(n):
        t = awal + timedelta(days=i)
        sesi.add(HostingBackup(site_id=h.site_id, tujuan="lokal", stempel=backup.stempel_dari(t), status="tersedia",
                               manual=False, ukuran_db=1, ukuran_file=1, sha256_db="a" * 64, sha256_file="b" * 64,
                               dibuat_pada=t))
    sesi.commit()


def _jalankan(sesi, h, payload=None, job=None):
    job = job or buat_job(sesi, h.site_id, JobType.backup_hosting, payload or {})
    hasil = backup.tangani_backup_hosting(sesi, job, None)
    job.status = JobStatus.success
    sesi.commit()
    return job, hasil


def _h(sesi, h):
    return sesi.get(HostingVps, h.id, populate_existing=True)


def test_handler_terdaftar():
    assert handlers.HANDLER[JobType.backup_hosting] is backup.tangani_backup_hosting


def test_backup_sukses_dan_pangkas(sesi, aktif, pb):
    _lama(sesi, aktif, 12)
    job, hasil = _jalankan(sesi, aktif, {"manual": True})
    baru = sesi.query(HostingBackup).filter(HostingBackup.job_id == job.id).one()
    assert (baru.status, baru.manual, baru.tujuan, baru.sha256_db) == ("tersedia", True, "lokal", "a" * 64)
    assert baru.stempel == umum.kemajuan(job)["stempel"] == hasil["stempel"]
    tersedia = sesi.query(HostingBackup).filter(HostingBackup.status == "tersedia").all()
    dipangkas = sesi.query(HostingBackup).filter(HostingBackup.status == "dipangkas").all()
    assert len(tersedia) + len(dipangkas) == 13 and dipangkas
    assert hasil["dipangkas"] == len(dipangkas)
    assert {("prod_backup_hapus", "toko-co-id", b.stempel) for b in dipangkas} <= set(pb.panggilan)
    h = sesi.get(HostingVps, aktif.id, populate_existing=True)
    assert h.backup_terakhir_pada is not None and h.backup_gagal_pada is None
    assert h.status == StatusHosting.aktif
    assert sesi.query(ActivityLog).filter(ActivityLog.job_id == job.id).one().pesan == "Backup situs dibuat"


def test_backup_gagal_tidak_memangkas(sesi, aktif, pb):
    _lama(sesi, aktif, 12)
    pb.gagal["prod_backup"] = GalatPembantu("backup", "Membuat backup situs gagal. Backup situs gagal dibuat.")
    job = buat_job(sesi, aktif.site_id, JobType.backup_hosting)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError):
        backup.tangani_backup_hosting(sesi, job, None)
    assert "prod_backup_hapus" not in pb.nama_panggilan()
    assert sesi.query(HostingBackup).filter(HostingBackup.status == "tersedia").count() == 12
    h = sesi.get(HostingVps, aktif.id, populate_existing=True)
    assert h.backup_gagal_pada is not None and h.status == StatusHosting.aktif


def test_disk_backup_penuh_ditolak(sesi, aktif, pb):
    pb.status_prod = StatusProd(8 * GB, 200 * GB, 150 * GB, 100 * GB, 10 * GB, {})
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _jalankan(sesi, aktif)
    assert "prod_backup" not in pb.nama_panggilan()
    assert sesi.get(HostingVps, aktif.id, populate_existing=True).backup_gagal_pada is not None


def test_backup_idempoten_per_stempel(sesi, aktif, pb):
    job = buat_job(sesi, aktif.site_id, JobType.backup_hosting, {"kemajuan": {"stempel": "20261003T023000Z"}})
    sesi.add(HostingBackup(site_id=aktif.site_id, tujuan="lokal", stempel="20261003T023000Z", status="tersedia",
                           manual=False, ukuran_db=1, ukuran_file=1, sha256_db="a" * 64, sha256_file="b" * 64))
    sesi.commit()
    _jalankan(sesi, aktif, job=job)
    assert ("prod_backup", "toko-co-id", "20261003T023000Z") in pb.panggilan
    assert sesi.query(HostingBackup).filter(HostingBackup.stempel == "20261003T023000Z").count() == 1


def test_backup_ditolak_bila_belum_dilayani(sesi, aktif, pb):
    aktif.dilayani_vps_pada = None
    aktif.status = StatusHosting.pratinjau
    sesi.commit()
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _jalankan(sesi, aktif)
    assert e.value.pesan == backup.PESAN_BELUM_DILAYANI


def test_hapus_gagal_tetap_tersedia_dan_dicoba_lagi(sesi, aktif, pb):
    _lama(sesi, aktif, 12)
    pb.gagal["prod_backup_hapus"] = GalatPembantu("ditolak", "Menghapus backup situs gagal.")
    _, hasil = _jalankan(sesi, aktif)
    assert hasil["dipangkas"] == 0
    assert sesi.query(HostingBackup).filter(HostingBackup.status == "tersedia").count() == 13


# ---- carry: pembungkus, batal, sibuk, muat ditolak ---------------------------------


def test_sisa_permintaan_batal_tidak_membatalkan_atau_mengubah_status(sesi, aktif, pb):
    # Carry Task 6: inti backup tidak memeriksa batal_diminta_pada (titik_potongan tanpa baris
    # hosting); sisa permintaan batal tidak boleh menulis PESAN_DIBATALKAN ke baris aktif.
    aktif.batal_diminta_pada = datetime.now(timezone.utc)
    sesi.commit()
    _, hasil = _jalankan(sesi, aktif)
    assert hasil["stempel"]
    h = _h(sesi, aktif)
    assert (h.status, h.galat, h.gagal_asal) == (StatusHosting.aktif, None, None)
    assert h.backup_terakhir_pada is not None
    assert sesi.query(HostingBackup).filter(HostingBackup.status == "tersedia").count() == 1


def test_pangkas_berdetak_tanpa_titik_potongan_berbaris_hosting(sesi, aktif, pb, monkeypatch):
    # Pemangkasan panjang memperpanjang klaim (detak) di antara penghapusan; tidak pernah lewat
    # titik_potongan dengan baris hosting (yang akan membaca batal_diminta_pada).
    _lama(sesi, aktif, 12)
    detak, titik = [], []
    monkeypatch.setattr(umum, "detak", lambda sesi_, job: detak.append(job.id))
    monkeypatch.setattr(umum, "titik_potongan", lambda sesi_, job, baris: titik.append(baris))
    _, hasil = _jalankan(sesi, aktif)
    assert len(detak) >= hasil["dipangkas"] > 0
    assert all(b is None for b in titik)


def test_backup_sibuk_dijadwalkan_ulang_tanpa_menandai_gagal(sesi, aktif, pb):
    # L10: keluar 3 (termasuk kunci sibuk, GalatPembantu.sibuk) diulang dengan jeda, bukan gagal.
    _lama(sesi, aktif, 12)
    pb.gagal["prod_backup"] = GalatPembantu("ditolak", "Membuat backup situs gagal. Skrip pembantu menolak "
                                            "permintaan ini.", sibuk=True)
    job = buat_job(sesi, aktif.site_id, JobType.backup_hosting)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(hu.GalatSibuk):
        backup.tangani_backup_hosting(sesi, job, None)
    h = _h(sesi, aktif)
    assert (h.status, h.galat, h.backup_gagal_pada) == (StatusHosting.aktif, None, None)
    assert "prod_backup_hapus" not in pb.nama_panggilan()
    j = sesi.get(Job, job.id, populate_existing=True)
    assert akan_diulang(j, TRANSIENT)


def test_muat_ditolak_sebelum_tukar_hanya_menandai_backup_gagal(sesi, aktif, pb, monkeypatch):
    # Minor tertunda Task 6: jalur muat-ditolak pra-tukar tidak boleh menulis ulang status hosting
    # untuk backup_hosting; hanya backup_gagal_pada.
    from wpmgr.config import get_settings

    aktif.status = StatusHosting.mengaktifkan  # status kerja basi (mis. sisa reaper)
    aktif.galat = "biarkan"
    sesi.commit()
    job = buat_job(sesi, aktif.site_id, JobType.backup_hosting)
    monkeypatch.delenv("WPMGR_HOSTING_IPV4")
    get_settings.cache_clear()
    with pytest.raises(SiteError):
        backup.tangani_backup_hosting(sesi, job, None)
    h = _h(sesi, aktif)
    assert (h.status, h.galat, h.gagal_asal) == (StatusHosting.mengaktifkan, "biarkan", None)
    assert h.backup_gagal_pada is not None
    assert "prod_backup" not in pb.nama_panggilan()


def test_pesan_galat_backup_teks_tetap(sesi, aktif, pb):
    pb.gagal["prod_backup"] = GalatPembantu("backup", "Membuat backup situs gagal. Backup situs gagal dibuat.")
    job = buat_job(sesi, aktif.site_id, JobType.backup_hosting)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        backup.tangani_backup_hosting(sesi, job, None)
    assert e.value.pesan == "Membuat backup situs gagal. Backup situs gagal dibuat."
    assert _h(sesi, aktif).galat is None


def test_manifest_tidak_sah_tidak_disimpan(sesi, aktif, pb, monkeypatch):
    monkeypatch.setattr(pb, "prod_backup", lambda nama, stempel: '{"versi":1}\n')
    job = buat_job(sesi, aktif.site_id, JobType.backup_hosting)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        backup.tangani_backup_hosting(sesi, job, None)
    assert e.value.pesan == backup.PESAN_MANIFEST
    assert sesi.query(HostingBackup).count() == 0
    assert _h(sesi, aktif).backup_gagal_pada is not None


# ---- cron ------------------------------------------------------------------------


def test_backup_harian_mengantrekan_dan_melewati_yang_sibuk(sesi, aktif):
    sekarang = datetime.now(timezone.utc)
    assert cron.antrekan_backup_harian(sesi, sekarang) == {"diantrekan": 1, "dilewati": 0}
    assert cron.antrekan_backup_harian(sesi, sekarang) == {"diantrekan": 0, "dilewati": 1}
    job = sesi.query(Job).one()
    assert job.tipe == JobType.backup_hosting and job.payload == {"manual": False}


def test_backup_harian_melewati_situs_belum_dilayani(sesi, site_hosting):
    site_hosting.status = StatusHosting.menunggu_dns
    sesi.commit()
    assert cron.antrekan_backup_harian(sesi, datetime.now(timezone.utc)) == {"diantrekan": 0, "dilewati": 0}
    assert sesi.query(Job).count() == 0


def test_cek_dns_mengantrekan_backup_pertama_sekali(sesi, aktif):
    assert cron.antrekan_backup_pertama(sesi) == 1
    assert cron.antrekan_backup_pertama(sesi) == 0
    sesi.query(Job).delete()
    aktif.backup_gagal_pada = datetime.now(timezone.utc)
    sesi.commit()
    assert cron.antrekan_backup_pertama(sesi) == 0
    aktif.backup_gagal_pada = None
    aktif.backup_terakhir_pada = datetime.now(timezone.utc)
    sesi.commit()
    assert cron.antrekan_backup_pertama(sesi) == 0


@pytest.fixture
def cli(engine, monkeypatch, hosting_aktif):
    from wpmgr import cli as modul
    from wpmgr import db

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    monkeypatch.setattr(db, "engine", engine)
    return modul


def test_cli_backup_hosting_mengantrekan(cli, sesi, aktif):
    assert cli.main(["backup-hosting"]) == 0
    assert sesi.query(Job).filter(Job.tipe == JobType.backup_hosting).count() == 1


def test_cli_backup_hosting_dilewati_bila_kunci_dipegang_atau_fitur_mati(cli, engine, monkeypatch):
    from wpmgr.config import get_settings
    from wpmgr.kunci import KUNCI_HOSTING_BACKUP, kunci_advisory

    with kunci_advisory(engine, KUNCI_HOSTING_BACKUP) as dapat:
        assert dapat
        assert cli.backup_hosting() is None
    monkeypatch.delenv("WPMGR_HOSTING_IPV4", raising=False)
    get_settings.cache_clear()
    assert cli.backup_hosting() is None


def test_cli_cek_dns_mengantrekan_backup_pertama(cli, sesi, aktif, monkeypatch):
    from wpmgr.hosting import dns as dns_mod

    monkeypatch.setattr(dns_mod, "buat_penanya", lambda: None)
    hasil = cli.hosting_cek_dns()
    assert hasil["backup_pertama"] == 1
    assert sesi.query(Job).filter(Job.tipe == JobType.backup_hosting).one().payload == {"manual": False}


# ---- API -------------------------------------------------------------------------


@pytest.mark.parametrize("metode", ["GET", "POST"])
def test_api_backup_anonim_ditolak(engine, hosting_aktif, metode):
    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), base_url="https://testserver")
    assert anon.request(metode, f"/api/sites/{uuid.uuid4()}/hosting/backup").status_code == 401


def test_api_backup_sekarang(klien_web, sesi, site_hosting):
    url = f"/api/sites/{site_hosting.site_id}/hosting/backup"
    r = klien_web.post(url)
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_BACKUP_BELUM
    site_hosting.status = StatusHosting.aktif
    site_hosting.dilayani_vps_pada = datetime.now(timezone.utc)
    sesi.commit()
    r = klien_web.post(url)
    assert r.status_code == 200
    assert sesi.get(Job, r.json()["job_id"]).payload == {"manual": True}
    assert klien_web.post(url).status_code == 409
    assert klien_web.get(url).json() == {"backup": []}


def test_api_backup_daftar_dibatasi_dan_per_site(klien_web, sesi, site_hosting):
    for i in range(55):
        t = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
        sesi.add(HostingBackup(site_id=site_hosting.site_id, tujuan="lokal", stempel=backup.stempel_dari(t),
                               status="tersedia", manual=False, ukuran_db=1, ukuran_file=1, sha256_db="a" * 64,
                               sha256_file="b" * 64, dibuat_pada=t))
    sesi.commit()
    data = klien_web.get(f"/api/sites/{site_hosting.site_id}/hosting/backup").json()["backup"]
    assert len(data) == 50 and data[0]["stempel"] == backup.stempel_dari(
        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=54))
    assert "sha256_db" not in data[0]
    assert klien_web.get(f"/api/sites/{uuid.uuid4()}/hosting/backup").status_code == 404


def test_api_backup_fitur_mati(klien_web, site, monkeypatch):
    # Preflight M3 / Global Constraints: GET menjawab {"aktif_fitur": false}, route lain 404.
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_HOSTING_IPV4", raising=False)
    get_settings.cache_clear()
    url = f"/api/sites/{site.id}/hosting/backup"
    assert klien_web.get(url).json() == {"aktif_fitur": False}
    assert klien_web.post(url).status_code == 404


def test_api_backup_ditolak_tanpa_hosting(klien_web, site, hosting_aktif):
    r = klien_web.post(f"/api/sites/{site.id}/hosting/backup")
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_BELUM_ADA
