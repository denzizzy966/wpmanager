import httpx
import pytest

from wpmgr.jobs.handlers import tangani_scan_site, tangani_verify_site
from wpmgr.jobs.queue import buat_job
from wpmgr.models import JobType
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration


def klien_palsu(muatan):
    def handler(request):
        return httpx.Response(200, json=muatan)

    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


PING = {
    "connector_version": "2.0.0", "wp_version": "6.5", "php_version": "8.1",
    "site_url": "https://contoh.test", "fitur": ["self_update", "events"],
    "mode_penangkap": "penuh", "percayai_xff": True, "versi_skema": 1,
}


def test_verify_mencatat_fitur_dan_setelan(sesi, site):
    job = buat_job(sesi, site.id, JobType.verify_site)
    tangani_verify_site(sesi, job, klien_palsu(PING))
    sesi.refresh(site)
    assert site.fitur == ["events", "self_update"]
    assert site.mode_penangkap == "penuh"
    assert site.percayai_xff is True
    assert site.connector_version == "2.0.0"


def test_connector_lama_tanpa_fitur_dicatat_kosong(sesi, site):
    site.fitur = ["events"]
    sesi.commit()
    lama = {"connector_version": "1.0.0", "wp_version": "6.5", "php_version": "8.1",
            "site_url": "https://contoh.test"}
    job = buat_job(sesi, site.id, JobType.verify_site)
    tangani_verify_site(sesi, job, klien_palsu(lama))
    sesi.refresh(site)
    assert site.fitur == []
    assert site.mode_penangkap is None


def test_mode_penangkap_tak_dikenal_diabaikan(sesi, site):
    job = buat_job(sesi, site.id, JobType.verify_site)
    tangani_verify_site(sesi, job, klien_palsu({**PING, "mode_penangkap": "<b>x</b>"}))
    sesi.refresh(site)
    assert site.mode_penangkap is None


def test_scan_juga_mencatat_fitur(sesi, site):
    inventaris = {"core": {"slug": "core", "nama": "WordPress", "versi_terpasang": "6.5",
                           "versi_tersedia": None, "aktif": True, "auto_update": False},
                  "plugins": [], "themes": [], "fitur": ["traffic"],
                  "connector_version": "2.0.0"}
    job = buat_job(sesi, site.id, JobType.scan_site)
    tangani_scan_site(sesi, job, klien_palsu(inventaris))
    sesi.refresh(site)
    assert site.fitur == ["traffic"]
    assert site.connector_version == "2.0.0"
