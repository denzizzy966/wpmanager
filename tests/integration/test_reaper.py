from datetime import datetime, timedelta, timezone

import pytest

from wpmgr.jobs.queue import ambil_job, buat_job
from wpmgr.jobs.reaper import pulihkan_job_yatim
from wpmgr.models import JobStatus, JobType

pytestmark = pytest.mark.integration


def test_job_running_yang_masih_segar_tidak_disentuh(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    assert pulihkan_job_yatim(sesi) == 0
    sesi.refresh(j)
    assert j.status == JobStatus.running


def test_job_yatim_dikembalikan_ke_pending(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    j.locked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    sesi.commit()
    assert pulihkan_job_yatim(sesi) == 1
    sesi.refresh(j)
    assert j.status == JobStatus.pending
    assert j.locked_by is None


def test_job_yatim_tanpa_jatah_menjadi_unknown(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    j.attempts = j.max_attempts
    j.locked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    sesi.commit()
    assert pulihkan_job_yatim(sesi) == 1
    sesi.refresh(j)
    assert j.status == JobStatus.unknown


def test_reaper_menulis_activity_log(sesi, site):
    from wpmgr.models import ActivityLog

    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    j.locked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    sesi.commit()
    pulihkan_job_yatim(sesi)
    assert sesi.query(ActivityLog).filter_by(job_id=j.id).count() == 1
