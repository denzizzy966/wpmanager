import pytest
from sqlalchemy.exc import IntegrityError

from wpmgr.models import Job, JobStatus, JobType, PackageType, SitePackage

pytestmark = pytest.mark.integration


def test_site_tersimpan_dengan_default_status(sesi, site):
    assert site.id is not None
    assert site.dibuat_pada is not None


def test_paket_unik_per_site_tipe_slug(sesi, site):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for _ in range(2):
        sesi.add(
            SitePackage(
                site_id=site.id, tipe=PackageType.plugin, slug="a/a.php",
                nama="A", versi_terpasang="1.0", last_scan_at=now,
            )
        )
    with pytest.raises(IntegrityError):
        sesi.commit()


def test_job_default_pending_dan_attempts_nol(sesi, site):
    j = Job(site_id=site.id, tipe=JobType.scan_site)
    sesi.add(j)
    sesi.commit()
    assert j.status == JobStatus.pending
    assert j.attempts == 0
    assert j.max_attempts == 3


def test_menghapus_site_menghapus_paketnya(sesi, site):
    from datetime import datetime, timezone

    sesi.add(
        SitePackage(
            site_id=site.id, tipe=PackageType.core, slug="core", nama="WordPress",
            versi_terpasang="6.5", last_scan_at=datetime.now(timezone.utc),
        )
    )
    sesi.commit()
    sesi.delete(site)
    sesi.commit()
    assert sesi.query(SitePackage).count() == 0
