import uuid
from datetime import datetime, timezone

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from wpmgr.crypto import dekripsi_secret
from wpmgr.models import (
    Job,
    JobStatus,
    JobType,
    PackageType,
    Site,
    SitePackage,
    SiteStatus,
    User,
)
from wpmgr.pairing import buat_site
from wpmgr.sso import baca_token

pytestmark = pytest.mark.integration


@pytest.fixture
def klien(engine, monkeypatch, sesi):
    from sqlalchemy.orm import sessionmaker

    from wpmgr import db
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    sesi.add(User(id=uuid.uuid4(), email="a@b.test", nama="Uji",
                  password_hash=PasswordHasher().hash("sandi")))
    sesi.commit()
    # base_url harus "https" (bukan default TestClient "http://testserver"):
    # SessionMiddleware memakai https_only=True sehingga cookie sesi ditandai
    # Secure, dan httpx diam-diam menolak mengirim balik cookie Secure di atas
    # permintaan berskema http meski cookie itu ada di jar-nya.
    c = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    c.post("/login", data={"email": "a@b.test", "password": "sandi"})
    return c


@pytest.fixture
def site_aktif(sesi):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    site.status = SiteStatus.active
    sesi.add(SitePackage(
        site_id=site.id, tipe=PackageType.plugin, slug="elementor/elementor.php",
        nama="Elementor", versi_terpasang="3.18.3", versi_tersedia="3.20.1",
        last_scan_at=datetime.now(timezone.utc)))
    sesi.add(SitePackage(
        site_id=site.id, tipe=PackageType.plugin, slug="akismet/akismet.php",
        nama="Akismet", versi_terpasang="5.3", versi_tersedia=None,
        last_scan_at=datetime.now(timezone.utc)))
    sesi.commit()
    return site


def test_packages_hanya_yang_punya_update(klien, site_aktif):
    data = klien.get("/api/packages").json()
    assert len(data) == 1
    assert data[0]["slug"] == "elementor/elementor.php"
    assert data[0]["site_nama"] == "Contoh"


def test_packages_semua_dengan_parameter(klien, site_aktif):
    assert len(klien.get("/api/packages?semua=1").json()) == 2


def test_sites_memuat_jumlah_update(klien, site_aktif):
    data = klien.get("/api/sites").json()
    assert len(data) == 1
    assert data[0]["jumlah_update"] == 1
    assert data[0]["status"] == "active"


def test_buat_job_update_massal(klien, site_aktif, sesi):
    r = klien.post("/api/jobs/update", json={"items": [
        {"site_id": str(site_aktif.id), "tipe": "plugin",
         "slug": "elementor/elementor.php", "ke_versi": "3.20.1"},
    ]})
    assert r.status_code == 200
    assert len(r.json()["job_ids"]) == 1
    j = sesi.query(Job).filter_by(tipe=JobType.update_package).one()
    assert j.payload["ke_versi"] == "3.20.1"
    assert j.dibuat_oleh is not None


def test_update_menolak_site_yang_tidak_ada(klien):
    r = klien.post("/api/jobs/update", json={"items": [
        {"site_id": str(uuid.uuid4()), "tipe": "plugin", "slug": "a", "ke_versi": "1"},
    ]})
    assert r.status_code == 404


def test_update_batch_campuran_tidak_membuat_job(klien, site_aktif, sesi):
    # Item pertama sah, item kedua menunjuk site yang tidak ada. Sebelum
    # perbaikan, buat_job() untuk item pertama sudah commit sebelum item kedua
    # ditolak, sehingga job sungguhan tertinggal walau pemanggil menerima 404.
    r = klien.post("/api/jobs/update", json={"items": [
        {"site_id": str(site_aktif.id), "tipe": "plugin",
         "slug": "elementor/elementor.php", "ke_versi": "3.20.1"},
        {"site_id": str(uuid.uuid4()), "tipe": "plugin", "slug": "a", "ke_versi": "1"},
    ]})
    assert r.status_code == 404
    assert sesi.query(Job).filter_by(tipe=JobType.update_package).count() == 0


def test_update_tipe_tidak_valid_membalas_422(klien, site_aktif, sesi):
    r = klien.post("/api/jobs/update", json={"items": [
        {"site_id": str(site_aktif.id), "tipe": "plugin",
         "slug": "elementor/elementor.php", "ke_versi": "3.20.1"},
        {"site_id": str(site_aktif.id), "tipe": "widget", "slug": "x", "ke_versi": "1"},
    ]})
    assert r.status_code == 422
    assert sesi.query(Job).filter_by(tipe=JobType.update_package).count() == 0


def test_update_dua_item_valid_membuat_dua_job(klien, site_aktif, sesi):
    site_lain, _ = buat_site(sesi, "Lain", "https://lain.test", None, None)
    site_lain.status = SiteStatus.active
    sesi.add(SitePackage(
        site_id=site_lain.id, tipe=PackageType.plugin, slug="woocommerce/woocommerce.php",
        nama="WooCommerce", versi_terpasang="8.0", versi_tersedia="8.5",
        last_scan_at=datetime.now(timezone.utc)))
    sesi.commit()

    r = klien.post("/api/jobs/update", json={"items": [
        {"site_id": str(site_aktif.id), "tipe": "plugin",
         "slug": "elementor/elementor.php", "ke_versi": "3.20.1"},
        {"site_id": str(site_lain.id), "tipe": "plugin",
         "slug": "woocommerce/woocommerce.php", "ke_versi": "8.5"},
    ]})
    assert r.status_code == 200
    assert len(r.json()["job_ids"]) == 2
    assert sesi.query(Job).filter_by(tipe=JobType.update_package).count() == 2


def test_buat_job_scan(klien, site_aktif, sesi):
    r = klien.post("/api/jobs/scan", json={"site_id": str(site_aktif.id)})
    assert r.status_code == 200
    assert sesi.query(Job).filter_by(tipe=JobType.scan_site).count() == 1


def test_jobs_active_hanya_pending_dan_running(klien, site_aktif, sesi):
    from wpmgr.jobs.queue import buat_job

    selesai = buat_job(sesi, site_aktif.id, JobType.scan_site)
    selesai.status = JobStatus.success
    buat_job(sesi, site_aktif.id, JobType.update_package, {"slug": "x"})
    sesi.commit()

    data = klien.get("/api/jobs/active").json()
    assert len(data) == 1
    assert data[0]["tipe"] == "update_package"


def test_sso_membalas_url_dengan_token_sah(klien, site_aktif, sesi):
    r = klien.get(f"/api/sso/{site_aktif.id}")
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://contoh.test/?wpmgr_sso=")

    token = url.split("wpmgr_sso=", 1)[1]
    secret = dekripsi_secret(sesi.get(Site, site_aktif.id).secret_terenkripsi)
    assert baca_token(secret, token)["site_id"] == str(site_aktif.id)


def test_sso_mencatat_activity_log(klien, site_aktif, sesi):
    from wpmgr.models import ActivityLog

    klien.get(f"/api/sso/{site_aktif.id}")
    baris = sesi.query(ActivityLog).filter_by(site_id=site_aktif.id).all()
    assert any("SSO" in b.pesan for b in baris)


def test_sso_site_tidak_ada_404(klien):
    assert klien.get(f"/api/sso/{uuid.uuid4()}").status_code == 404


def test_hapus_site_menghapus_baris_dan_paketnya(klien, site_aktif, sesi):
    # site_id disalin ke variabel lokal sebelum delete/expire_all: setelah
    # baris site dihapus lewat sesi lain, mengakses site_aktif.id langsung
    # (atribut PK pada objek yang sudah di-expire) memicu SELECT ulang dan
    # meledak dengan ObjectDeletedError alih-alih memberi None dengan tenang.
    site_id = site_aktif.id
    r = klien.delete(f"/api/sites/{site_id}")
    assert r.status_code == 200
    sesi.expire_all()
    assert sesi.get(Site, site_id) is None
    assert sesi.query(SitePackage).filter_by(site_id=site_id).count() == 0


def test_hapus_site_tidak_ada_404(klien):
    assert klien.delete(f"/api/sites/{uuid.uuid4()}").status_code == 404


def test_hapus_site_mencatat_activity_log_tanpa_site_id(klien, site_aktif, sesi):
    from wpmgr.models import ActivityLog

    klien.delete(f"/api/sites/{site_aktif.id}")
    baris = sesi.query(ActivityLog).filter_by(site_id=None).all()
    assert any("Contoh" in b.pesan for b in baris)
