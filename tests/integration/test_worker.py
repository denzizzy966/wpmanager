import httpx
import pytest

from wpmgr.jobs.queue import buat_job
from wpmgr.models import ActivityLog, Job, JobStatus, JobType, SiteStatus
from wpmgr.site_client import SiteClient
from wpmgr.worker import proses_satu

pytestmark = pytest.mark.integration

PING = {"connector_version": "1.0", "wp_version": "6.5", "php_version": "8.1"}


def pabrik(status=200, muatan=PING):
    def buat(site):
        return SiteClient("https://contoh.test", "s", "f" * 64,
                          client=httpx.Client(transport=httpx.MockTransport(
                              lambda r: httpx.Response(status, json=muatan))))

    return buat


def test_antrean_kosong_mengembalikan_false(sesi):
    assert proses_satu(sesi, "w1", pabrik()) is False


def test_job_sukses_ditandai_success(sesi, site):
    buat_job(sesi, site.id, JobType.verify_site)
    assert proses_satu(sesi, "w1", pabrik()) is True
    j = sesi.query(Job).one()
    assert j.status == JobStatus.success


def test_auth_error_menandai_site_needs_reconnect(sesi, site):
    buat_job(sesi, site.id, JobType.verify_site)
    proses_satu(sesi, "w1", pabrik(status=401, muatan={"code": "x"}))
    sesi.refresh(site)
    assert site.status == SiteStatus.needs_reconnect
    assert sesi.query(Job).one().status == JobStatus.failed


def test_403_firewall_menandai_site_blocked(sesi, site):
    buat_job(sesi, site.id, JobType.verify_site)

    def buat(s):
        return SiteClient("https://contoh.test", "s", "f" * 64,
                          client=httpx.Client(transport=httpx.MockTransport(
                              lambda r: httpx.Response(403, headers={"Server": "cloudflare"},
                                                       content="denied"))))

    proses_satu(sesi, "w1", buat)
    sesi.refresh(site)
    assert site.status == SiteStatus.blocked


def test_kegagalan_menulis_activity_log(sesi, site):
    buat_job(sesi, site.id, JobType.verify_site)
    proses_satu(sesi, "w1", pabrik(status=401, muatan={"code": "x"}))
    assert sesi.query(ActivityLog).filter_by(level="error").count() == 1


def test_job_site_nonaktif_tidak_diambil(sesi, site):
    site.status = SiteStatus.disabled
    sesi.commit()
    buat_job(sesi, site.id, JobType.verify_site)
    assert proses_satu(sesi, "w1", pabrik()) is False


def test_klaim_diambil_alih_reaper_saat_sukses_tidak_menulis_hasil(sesi, site):
    """Simulasi reaper: klaim direbut (status -> pending, locked_by -> None) tepat
    sebelum handler dijalankan. Ini mensimulasikan worker yang tersendat lewat
    ambang 15 menit reaper namun sebenarnya masih hidup dan sedang bekerja."""
    buat_job(sesi, site.id, JobType.verify_site)

    def buat_dan_rebut(s):
        job = sesi.query(Job).one()
        job.status = JobStatus.pending
        job.locked_by = None
        sesi.commit()
        return SiteClient("https://contoh.test", "s", "f" * 64,
                          client=httpx.Client(transport=httpx.MockTransport(
                              lambda r: httpx.Response(200, json=PING))))

    assert proses_satu(sesi, "w1", buat_dan_rebut) is True
    j = sesi.query(Job).one()
    assert j.status == JobStatus.pending
    assert j.hasil is None


def test_klaim_diambil_alih_reaper_saat_gagal_tidak_mencatat_error(sesi, site):
    buat_job(sesi, site.id, JobType.verify_site)

    def buat_dan_rebut(s):
        job = sesi.query(Job).one()
        job.status = JobStatus.pending
        job.locked_by = None
        sesi.commit()
        return SiteClient("https://contoh.test", "s", "f" * 64,
                          client=httpx.Client(transport=httpx.MockTransport(
                              lambda r: httpx.Response(401, json={"code": "x"}))))

    assert proses_satu(sesi, "w1", buat_dan_rebut) is True
    j = sesi.query(Job).one()
    assert j.status == JobStatus.pending
    assert j.error_class is None
    assert sesi.query(ActivityLog).filter_by(level="error").count() == 0
