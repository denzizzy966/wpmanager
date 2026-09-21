import shutil

import pytest

from wpmgr.config import get_settings
from wpmgr.connector_paket import bangun_paket
from wpmgr.jobs.queue import buat_job
from wpmgr.models import Job, JobStatus, JobType
from wpmgr.worker import proses_satu

from .conftest import (
    AKAR_REPO,
    _wpcli_status,
    klien_http,
    sinkronkan_connector,
    versi_connector_sumber,
)

pytestmark = pytest.mark.e2e


@pytest.fixture
def connector_dipulihkan():
    yield
    # Test ini menimpa connector di container dengan versi uji; sesi e2e
    # berikutnya dan test lain harus kembali memakai kode checkout.
    sinkronkan_connector()


def _jalankan(sesi):
    return proses_satu(sesi, "uji-e2e", buat_klien_fn=klien_http)


def _jalankan_sampai_selesai(sesi, job, batas: int = 10) -> None:
    """Proses antrean sampai `job` tertentu keluar dari pending/running.

    proses_satu() memproses SATU job per panggilan -- yang tertua menurut
    scheduled_for, di seluruh antrean, bukan hanya job yang baru dibuat.
    verify_site dan update_connector sama-sama meng-antre-kan job susulan
    (scan_site, verify_site) yang scheduled_for-nya lebih tua daripada job
    yang baru saja dibuat test ini, sehingga satu panggilan proses_satu()
    tidak menjamin job yang baru itu yang diambil. Deviasi dari brief Task 7
    (yang memanggil _jalankan() sekali per job): dengan hanya satu panggilan,
    test ini gagal karena scan_site/verify_site susulan itu diproses lebih
    dulu dan job yang diperiksa tetap `pending`.
    """
    for _ in range(batas):
        sesi.refresh(job)
        if job.status not in (JobStatus.pending, JobStatus.running):
            return
        assert _jalankan(sesi)
    sesi.refresh(job)


def test_self_update_mengganti_versi_dan_connector_tetap_aktif(
    sesi, site_terpasang, tmp_path, monkeypatch, connector_dipulihkan
):
    versi_lama = versi_connector_sumber()
    versi_baru = versi_lama + ".1"
    salinan = tmp_path / "wp-manager-connector"
    shutil.copytree(AKAR_REPO / "connector" / "wp-manager-connector", salinan)
    utama = salinan / "wp-manager-connector.php"
    teks = utama.read_text(encoding="utf-8")
    teks = teks.replace(f"Version:     {versi_lama}", f"Version:     {versi_baru}")
    teks = teks.replace(f"'WPMGR_VERSION', '{versi_lama}'", f"'WPMGR_VERSION', '{versi_baru}'")
    utama.write_text(teks, encoding="utf-8")

    monkeypatch.setenv("WPMGR_VAR_DIR", str(tmp_path / "var"))
    get_settings.cache_clear()
    bangun_paket(salinan, get_settings().jalur_connector)

    buat_job(sesi, site_terpasang.id, JobType.verify_site)
    assert _jalankan(sesi)
    sesi.refresh(site_terpasang)
    assert "self_update" in site_terpasang.fitur

    job = buat_job(sesi, site_terpasang.id, JobType.update_connector, {"versi": versi_baru})
    _jalankan_sampai_selesai(sesi, job)
    assert job.status == JobStatus.success, job.error

    assert klien_http(site_terpasang).ping()["connector_version"] == versi_baru
    assert _wpcli_status("plugin", "is-active", "wp-manager-connector") == 0
    # Repo tidak tersentuh: self-update menimpa salinan di container.
    assert versi_connector_sumber() == versi_lama

    # Mengulang dengan paket yang sama idempoten.
    job2 = buat_job(sesi, site_terpasang.id, JobType.update_connector, {"versi": versi_baru})
    _jalankan_sampai_selesai(sesi, job2)
    assert job2.status == JobStatus.success
    assert "sudah di versi" in job2.hasil["pesan"]
    assert sesi.query(Job).filter_by(tipe=JobType.verify_site).count() >= 1
