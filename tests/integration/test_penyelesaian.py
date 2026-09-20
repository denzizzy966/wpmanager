import pytest

from wpmgr.errors import AUTH_ERROR, TRANSIENT
from wpmgr.jobs.queue import (
    ambil_job,
    buat_job,
    jeda_menit,
    selesai_gagal,
    selesai_sukses,
    tandai_unknown,
)
from wpmgr.models import JobStatus, JobType

pytestmark = pytest.mark.integration


def test_backoff_eksponensial():
    assert jeda_menit(1) == 2
    assert jeda_menit(2) == 4
    assert jeda_menit(3) == 8


def test_sukses_menyimpan_hasil_dan_waktu_selesai(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    selesai_sukses(sesi, j, {"jumlah": 12})
    sesi.refresh(j)
    assert j.status == JobStatus.success
    assert j.hasil == {"jumlah": 12}
    assert j.finished_at is not None
    assert j.locked_by is None


def test_error_transient_dijadwalkan_ulang(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    sebelum = j.scheduled_for
    selesai_gagal(sesi, j, TRANSIENT, "koneksi ditolak")
    sesi.refresh(j)
    assert j.status == JobStatus.pending
    assert j.scheduled_for > sebelum
    assert j.error_class == TRANSIENT


def test_auth_error_langsung_failed_tanpa_retry(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    selesai_gagal(sesi, j, AUTH_ERROR, "tanda tangan ditolak")
    sesi.refresh(j)
    assert j.status == JobStatus.failed
    assert j.attempts == 1, "tidak boleh ada percobaan tambahan"


def test_transient_menjadi_failed_setelah_jatah_habis(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    for _ in range(3):
        j = ambil_job(sesi, "w1")
        assert j is not None
        j.scheduled_for = j.dibuat_pada
        selesai_gagal(sesi, j, TRANSIENT, "gagal")
        sesi.refresh(j)
        if j.status == JobStatus.pending:
            j.scheduled_for = j.dibuat_pada
            sesi.commit()
    assert j.status == JobStatus.failed
    assert j.attempts == 3


def test_unknown_melepas_kunci_tapi_tidak_menjadwal_ulang(sesi, site):
    buat_job(sesi, site.id, JobType.update_package, {"slug": "a"})
    j = ambil_job(sesi, "w1")
    tandai_unknown(sesi, j, "timeout")
    sesi.refresh(j)
    assert j.status == JobStatus.unknown
    assert j.locked_by is None
