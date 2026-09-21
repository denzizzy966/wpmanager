import uuid
from datetime import datetime, timezone

import httpx
import pytest

from wpmgr.errors import TRANSIENT, UNKNOWN, SiteError
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    PackageType,
    Site,
    SitePackage,
    SiteStatus,
    User,
)
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
    # Difilter per tipe: sejak R59-d verify yang sukses ikut membuat scan_site.
    j = sesi.query(Job).filter_by(tipe=JobType.verify_site).one()
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
    # Difilter per tipe: handler verify tetap berjalan sampai selesai (klaimnya
    # baru diperiksa sesudahnya) dan sejak R59-d ikut membuat scan_site.
    j = sesi.query(Job).filter_by(tipe=JobType.verify_site).one()
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


# --- R54: status site mengikuti hasil akhir, bukan percobaan pertama --------

PAYLOAD_UPDATE = {"tipe": "plugin", "slug": "elementor/elementor.php",
                  "dari_versi": "3.18.3", "ke_versi": "3.20.1"}


def klien_dari(handler):
    def buat(site):
        return SiteClient("https://contoh.test", "s", "f" * 64,
                          client=httpx.Client(transport=httpx.MockTransport(handler)))

    return buat


def _paket(sesi, site, versi="3.18.3"):
    sesi.add(SitePackage(
        site_id=site.id, tipe=PackageType.plugin, slug="elementor/elementor.php",
        nama="Elementor", versi_terpasang=versi, versi_tersedia="3.20.1",
        last_scan_at=datetime.now(timezone.utc)))
    sesi.commit()


def test_transient_percobaan_pertama_tidak_menandai_unreachable(sesi, site):
    job = buat_job(sesi, site.id, JobType.scan_site)
    proses_satu(sesi, "w1", pabrik(status=502, muatan={}))
    sesi.refresh(site)
    sesi.refresh(job)
    assert job.status == JobStatus.pending
    assert site.status == SiteStatus.active


def test_transient_yang_menghabiskan_jatah_menandai_unreachable(sesi, site):
    job = buat_job(sesi, site.id, JobType.scan_site)
    job.attempts = job.max_attempts - 1
    sesi.commit()
    proses_satu(sesi, "w1", pabrik(status=502, muatan={}))
    sesi.refresh(site)
    sesi.refresh(job)
    assert job.status == JobStatus.failed
    assert site.status == SiteStatus.unreachable


@pytest.mark.parametrize(
    "status_awal", [SiteStatus.unreachable, SiteStatus.needs_reconnect, SiteStatus.blocked]
)
def test_update_sukses_memulihkan_status_site(sesi, site, status_awal):
    """Tipe job apa pun, bukan hanya scan: site yang barusan menjawab update
    bertanda tangan jelas terjangkau dan terpasang dengan benar."""
    site.status = status_awal
    sesi.commit()
    _paket(sesi, site)
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD_UPDATE)
    proses_satu(sesi, "w1", klien_dari(lambda r: httpx.Response(200, json={
        "ok": True, "versi_sebelum": "3.18.3", "versi_sesudah": "3.20.1"})))
    sesi.refresh(site)
    assert site.status == SiteStatus.active


@pytest.mark.parametrize("tipe", [JobType.update_package, JobType.verify_site])
def test_job_sukses_tidak_mengaktifkan_site_yang_dinonaktifkan_selagi_berjalan(sesi, site, tipe):
    _paket(sesi, site)
    buat_job(sesi, site.id, tipe, PAYLOAD_UPDATE if tipe == JobType.update_package else {})

    def nonaktifkan_lalu_buat(s):
        # Operator menonaktifkan site setelah job terklaim.
        s.status = SiteStatus.disabled
        sesi.commit()
        return SiteClient("https://contoh.test", "s", "f" * 64,
                          client=httpx.Client(transport=httpx.MockTransport(
                              lambda r: httpx.Response(200, json={
                                  **PING, "ok": True, "versi_sesudah": "3.20.1"}))))

    proses_satu(sesi, "w1", nonaktifkan_lalu_buat)
    sesi.refresh(site)
    assert site.status == SiteStatus.disabled


# --- R53: package_missing ----------------------------------------------------

TIDAK_DITEMUKAN = {"code": "wpmgr_tidak_ditemukan", "message": "Paket tidak ditemukan di site ini.",
                   "data": {"status": 404}}


def test_package_missing_final_tanpa_mengubah_status_dan_memicu_scan(sesi, site):
    job = buat_job(sesi, site.id, JobType.update_package, PAYLOAD_UPDATE)
    proses_satu(sesi, "w1", klien_dari(lambda r: httpx.Response(404, json=TIDAK_DITEMUKAN)))

    sesi.refresh(job)
    sesi.refresh(site)
    assert job.status == JobStatus.failed
    assert job.error_class == "package_missing"
    assert job.attempts == 1
    assert site.status == SiteStatus.active
    scan = sesi.query(Job).filter_by(site_id=site.id, tipe=JobType.scan_site).one()
    assert scan.status == JobStatus.pending


def test_package_missing_tidak_menggandakan_scan_yang_sudah_tertunda(sesi, site):
    from datetime import timedelta

    # Dijadwalkan di masa depan supaya worker mengambil job update, bukan scan.
    buat_job(sesi, site.id, JobType.scan_site,
             scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1))
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD_UPDATE)
    proses_satu(sesi, "w1", klien_dari(lambda r: httpx.Response(404, json=TIDAK_DITEMUKAN)))
    assert sesi.query(Job).filter_by(site_id=site.id, tipe=JobType.scan_site).count() == 1


# --- R55: unknown pada job non-update ----------------------------------------

def test_unknown_pada_job_non_update_diperlakukan_transient(sesi, site):
    class KlienTimeout:
        def ping(self):
            raise SiteError(UNKNOWN, "timeout setelah 15.0 detik")

    job = buat_job(sesi, site.id, JobType.verify_site)
    proses_satu(sesi, "w1", lambda s: KlienTimeout())
    sesi.refresh(job)
    assert job.status == JobStatus.pending
    assert job.error_class == TRANSIENT


# --- R57 / R59-b: timeout update yang ternyata sukses ------------------------

def _timeout_lalu_inventaris(versi_terpasang):
    def handler(request):
        if request.url.path.endswith("/update"):
            raise httpx.ReadTimeout("habis", request=request)
        return httpx.Response(200, json={
            "core": None,
            "plugins": [{"slug": "elementor/elementor.php", "nama": "Elementor",
                         "versi_terpasang": versi_terpasang, "versi_tersedia": None,
                         "aktif": True, "auto_update": False}],
            "themes": [],
        })

    return klien_dari(handler)


def test_timeout_yang_teratasi_sukses_tidak_menulis_error(sesi, site):
    site.status = SiteStatus.unreachable
    sesi.commit()
    _paket(sesi, site)
    pembuat = User(id=uuid.uuid4(), email="op@contoh.test", nama="Op", password_hash="x")
    sesi.add(pembuat)
    sesi.commit()
    job = buat_job(sesi, site.id, JobType.update_package, PAYLOAD_UPDATE, dibuat_oleh=pembuat.id)

    proses_satu(sesi, "w1", _timeout_lalu_inventaris("3.20.1"))

    sesi.refresh(job)
    sesi.refresh(site)
    assert job.status == JobStatus.success
    assert sesi.query(ActivityLog).filter_by(level="error").count() == 0
    assert site.last_error is None
    assert site.status == SiteStatus.active
    info = sesi.query(ActivityLog).filter_by(job_id=job.id, level="info").one()
    assert info.detail["versi_sebelum"] == "3.18.3"
    assert info.detail["versi_sesudah"] == "3.20.1"
    assert info.detail["email"] == "op@contoh.test"
    assert info.user_id == pembuat.id


def test_timeout_yang_belum_teratasi_tetap_menulis_error(sesi, site):
    _paket(sesi, site)
    job = buat_job(sesi, site.id, JobType.update_package, PAYLOAD_UPDATE)
    proses_satu(sesi, "w1", _timeout_lalu_inventaris("3.18.3"))
    sesi.refresh(job)
    assert job.status == JobStatus.pending
    assert sesi.query(ActivityLog).filter_by(level="error").count() == 1
    assert sesi.query(ActivityLog).filter_by(level="info").count() == 0


# --- R59-a: kesalahan tak terduga di handler ---------------------------------

def test_handler_yang_melempar_keyerror_menjadi_internal_error(sesi, site):
    # Payload tanpa ke_versi: tangani_update_package melempar KeyError, bukan
    # SiteError. Dulu job ini tertinggal `running` sampai reaper datang
    # 15 menit kemudian -- lalu diulang, dan melempar KeyError yang sama.
    job = buat_job(sesi, site.id, JobType.update_package, {"tipe": "plugin", "slug": "a/a.php"})
    assert proses_satu(sesi, "w1", pabrik()) is True

    sesi.refresh(job)
    sesi.refresh(site)
    assert job.status == JobStatus.failed
    assert job.error_class == "internal_error"
    assert "KeyError" in job.error
    assert job.locked_by is None
    assert site.status == SiteStatus.active
    log = sesi.query(ActivityLog).filter_by(job_id=job.id, level="error").one()
    assert "internal_error" in log.pesan


def test_internal_error_membatalkan_perubahan_yang_belum_di_commit(sesi, site, monkeypatch):
    from wpmgr.jobs.handlers import HANDLER

    def handler_rusak(s, job, klien):
        situs = s.get(Site, job.site_id)
        situs.status = SiteStatus.blocked  # belum di-commit
        raise RuntimeError("bug")

    monkeypatch.setitem(HANDLER, JobType.scan_site, handler_rusak)
    job = buat_job(sesi, site.id, JobType.scan_site)
    proses_satu(sesi, "w1", pabrik())

    sesi.expire_all()
    assert sesi.get(Site, site.id).status == SiteStatus.active
    assert sesi.get(Job, job.id).error_class == "internal_error"


def test_internal_error_tidak_ditulis_bila_klaim_sudah_diambil_alih(sesi, site, monkeypatch):
    from wpmgr.jobs.handlers import HANDLER

    def rebut_lalu_rusak(s, job, klien):
        segar = s.get(Job, job.id)
        segar.status = JobStatus.pending
        segar.locked_by = None
        s.commit()
        raise RuntimeError("bug")

    monkeypatch.setitem(HANDLER, JobType.scan_site, rebut_lalu_rusak)
    job = buat_job(sesi, site.id, JobType.scan_site)
    proses_satu(sesi, "w1", pabrik())

    sesi.expire_all()
    j = sesi.get(Job, job.id)
    assert j.status == JobStatus.pending
    assert j.error_class is None
    assert sesi.query(ActivityLog).filter_by(level="error").count() == 0
