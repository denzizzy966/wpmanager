from datetime import datetime, timezone

import httpx
import pytest

from wpmgr.errors import UNKNOWN, SiteError
from wpmgr.jobs.handlers import resolusi_unknown, tangani_update_package
from wpmgr.jobs.queue import ambil_job, buat_job
from wpmgr.models import JobType, PackageType, SitePackage
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration

PAYLOAD = {"tipe": "plugin", "slug": "elementor/elementor.php",
           "dari_versi": "3.18.3", "ke_versi": "3.20.1"}


def klien_dari(handler):
    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


def _paket(sesi, site, versi):
    sesi.add(SitePackage(
        site_id=site.id, tipe=PackageType.plugin, slug="elementor/elementor.php",
        nama="Elementor", versi_terpasang=versi, versi_tersedia="3.20.1",
        last_scan_at=datetime.now(timezone.utc)))
    sesi.commit()


def test_update_sukses_memperbarui_baris_paket(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")

    klien = klien_dari(lambda r: httpx.Response(200, json={
        "ok": True, "versi_sebelum": "3.18.3", "versi_sesudah": "3.20.1", "pesan": "berhasil"}))
    hasil = tangani_update_package(sesi, job, klien)

    p = sesi.query(SitePackage).filter_by(site_id=site.id).one()
    assert hasil["versi_sesudah"] == "3.20.1"
    assert p.versi_terpasang == "3.20.1"
    assert p.versi_tersedia is None


def test_update_hanya_mengirim_satu_request(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")
    jumlah = {"n": 0}

    def handler(request):
        jumlah["n"] += 1
        return httpx.Response(200, json={"ok": True, "versi_sesudah": "3.20.1"})

    tangani_update_package(sesi, job, klien_dari(handler))
    assert jumlah["n"] == 1


def test_timeout_melempar_site_error_unknown(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")

    def handler(request):
        raise httpx.ReadTimeout("habis", request=request)

    with pytest.raises(SiteError) as exc:
        tangani_update_package(sesi, job, klien_dari(handler))
    assert exc.value.error_class == UNKNOWN


def test_resolusi_unknown_menjadi_success_bila_versi_sudah_naik(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")

    klien = klien_dari(lambda r: httpx.Response(200, json={
        "core": None,
        "plugins": [{"slug": "elementor/elementor.php", "nama": "Elementor",
                     "versi_terpasang": "3.20.1", "versi_tersedia": None,
                     "aktif": True, "auto_update": False}],
        "themes": [],
    }))
    assert resolusi_unknown(sesi, job, klien) == "success"


def test_resolusi_unknown_menjadi_pending_bila_versi_belum_naik(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")

    klien = klien_dari(lambda r: httpx.Response(200, json={
        "core": None,
        "plugins": [{"slug": "elementor/elementor.php", "nama": "Elementor",
                     "versi_terpasang": "3.18.3", "versi_tersedia": "3.20.1",
                     "aktif": True, "auto_update": False}],
        "themes": [],
    }))
    assert resolusi_unknown(sesi, job, klien) == "pending"


def test_resolusi_unknown_menjadi_failed_bila_jatah_habis(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")
    job.attempts = job.max_attempts
    sesi.commit()

    klien = klien_dari(lambda r: httpx.Response(200, json={
        "core": None,
        "plugins": [{"slug": "elementor/elementor.php", "nama": "Elementor",
                     "versi_terpasang": "3.18.3", "versi_tersedia": "3.20.1",
                     "aktif": True, "auto_update": False}],
        "themes": [],
    }))
    assert resolusi_unknown(sesi, job, klien) == "failed"
