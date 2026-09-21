import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from wpmgr.jobs.queue import buat_job
from wpmgr.models import Job, JobStatus, JobType, Site, SiteStatus

pytestmark = pytest.mark.integration


@pytest.fixture
def enqueue_scans(engine, monkeypatch):
    from wpmgr import db
    from wpmgr.cli import enqueue_scans

    monkeypatch.setattr(
        db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True)
    )
    return enqueue_scans


def _site(sesi, status):
    s = Site(id=uuid.uuid4(), nama=status.value, url=f"https://{uuid.uuid4().hex[:8]}.test",
             status=status, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.commit()
    return s


def _jumlah_scan(sesi, site):
    return sesi.query(Job).filter_by(site_id=site.id, tipe=JobType.scan_site).count()


def test_scan_terjadwal_mencakup_active_dan_unreachable(sesi, enqueue_scans):
    """R54: unreachable menurut definisinya sementara. Tanpa scan terjadwal,
    satu gangguan jaringan membuat site keluar dari pemantauan selamanya."""
    per_status = {st: _site(sesi, st) for st in SiteStatus}

    assert enqueue_scans() == 2

    assert _jumlah_scan(sesi, per_status[SiteStatus.active]) == 1
    assert _jumlah_scan(sesi, per_status[SiteStatus.unreachable]) == 1
    # Keempatnya menuntut tindakan manusia (tempel ulang kunci, allowlist IP,
    # pairing) atau sengaja dimatikan; scan otomatis hanya menambah log.
    for st in (SiteStatus.needs_reconnect, SiteStatus.blocked,
               SiteStatus.disabled, SiteStatus.pending_pair):
        assert _jumlah_scan(sesi, per_status[st]) == 0, st


def test_scan_terjadwal_tidak_menggandakan_scan_tertunda(sesi, enqueue_scans):
    site = _site(sesi, SiteStatus.active)
    buat_job(sesi, site.id, JobType.scan_site)

    assert enqueue_scans() == 0
    assert _jumlah_scan(sesi, site) == 1


def test_scan_terjadwal_tidak_menggandakan_scan_berjalan(sesi, enqueue_scans):
    site = _site(sesi, SiteStatus.unreachable)
    job = buat_job(sesi, site.id, JobType.scan_site)
    job.status = JobStatus.running
    sesi.commit()

    assert enqueue_scans() == 0
    assert _jumlah_scan(sesi, site) == 1


def test_scan_terjadwal_berjalan_ulang_setelah_scan_lama_selesai(sesi, enqueue_scans):
    site = _site(sesi, SiteStatus.active)
    job = buat_job(sesi, site.id, JobType.scan_site)
    job.status = JobStatus.success
    sesi.commit()

    assert enqueue_scans() == 1
    assert _jumlah_scan(sesi, site) == 2
