import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from wpmgr.jobs.queue import ambil_job, buat_job, worker_id
from wpmgr.models import JobStatus, JobType, Site, SiteStatus

pytestmark = pytest.mark.integration


def _site(sesi, nama="S"):
    s = Site(id=uuid.uuid4(), nama=nama, url=f"https://{uuid.uuid4().hex[:8]}.test",
             status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.commit()
    return s


def test_mengambil_job_pending_dan_menandainya_running(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    assert j is not None
    assert j.status == JobStatus.running
    assert j.locked_by == "w1"
    assert j.attempts == 1
    assert j.locked_at is not None and j.started_at is not None


def test_tidak_mengambil_job_terjadwal_masa_depan(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site,
             scheduled_for=datetime.now(timezone.utc) + timedelta(minutes=10))
    assert ambil_job(sesi, "w1") is None


def test_hanya_satu_job_berjalan_per_site(sesi, site):
    buat_job(sesi, site.id, JobType.update_package, {"slug": "a"})
    buat_job(sesi, site.id, JobType.update_package, {"slug": "b"})
    assert ambil_job(sesi, "w1") is not None
    assert ambil_job(sesi, "w2") is None, "site yang sama tidak boleh punya dua job running"


def test_site_lain_tetap_berjalan_paralel(sesi, site):
    lain = _site(sesi, "Lain")
    buat_job(sesi, site.id, JobType.scan_site)
    buat_job(sesi, lain.id, JobType.scan_site)
    a = ambil_job(sesi, "w1")
    b = ambil_job(sesi, "w2")
    assert a is not None and b is not None
    assert a.site_id != b.site_id


def test_dua_worker_tidak_pernah_mengambil_job_yang_sama(engine, sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s1, s2 = Session(), Session()
    try:
        a = ambil_job(s1, "w1")
        b = ambil_job(s2, "w2")
        assert a is not None
        assert b is None
    finally:
        s1.close()
        s2.close()


def test_antrean_kosong_mengembalikan_none(sesi):
    assert ambil_job(sesi, "w1") is None


def test_urutan_berdasarkan_scheduled_for(sesi, site):
    lama = buat_job(sesi, site.id, JobType.scan_site,
                    scheduled_for=datetime.now(timezone.utc) - timedelta(hours=2))
    buat_job(sesi, site.id, JobType.scan_site,
             scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert ambil_job(sesi, "w1").id == lama.id


def test_worker_id_berbentuk_host_titikdua_pid():
    assert ":" in worker_id()
