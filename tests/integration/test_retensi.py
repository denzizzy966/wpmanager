from datetime import date, datetime, timedelta, timezone

import pytest

from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    CatatanError,
    Job,
    JobStatus,
    JobType,
    KejadianLogin,
    LoginGagal,
    TrafficHarian,
    UptimeCheck,
    UptimeHasil,
    UptimeInsiden,
    UptimePutaran,
)
from wpmgr.retensi import pangkas

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 22, tzinfo=timezone.utc)
LAMA = SEKARANG - timedelta(days=91)
BARU = SEKARANG - timedelta(days=89)


def job_selesai(sesi, site, tipe, selesai, status=JobStatus.success):
    job = buat_job(sesi, site.id, tipe, {"slug": "x"} if tipe == JobType.update_package else None)
    job.status = status
    job.finished_at = selesai
    sesi.commit()
    return job


def test_data_mentah_lama_dipangkas_yang_baru_dipertahankan(sesi, site):
    for waktu in (LAMA, BARU):
        p = UptimePutaran(mulai=waktu, jumlah_site=1, jumlah_gagal=0, gangguan_dashboard=False)
        sesi.add(p)
        sesi.flush()
        sesi.add(UptimeCheck(putaran_id=p.id, site_id=site.id, dicek_pada=waktu, hasil=UptimeHasil.naik))
        sesi.add(KejadianLogin(site_id=site.id, id_di_site=int(waktu.timestamp()), waktu=waktu,
                               jenis="berhasil", username="a"))
        sesi.add(LoginGagal(site_id=site.id, jam=waktu, ip="1.2.3.4", username="a", jalur="form", jumlah=1))
        sesi.add(CatatanError(site_id=site.id, sidik_jari=str(waktu.timestamp()), tingkat="warning",
                              komponen_tipe="core", pesan="x", jumlah=1,
                              pertama_terlihat=waktu, terakhir_terlihat=waktu))
    sesi.add(UptimeInsiden(site_id=site.id, mulai=LAMA, selesai=LAMA, penyebab="HTTP 500"))
    sesi.add(TrafficHarian(site_id=site.id, tanggal=date(2025, 1, 1), sumber="plugin", kunjungan=1, pengunjung=1))
    sesi.commit()

    hasil = pangkas(sesi, SEKARANG)
    assert hasil["uptime_checks"] >= 1
    for model in (UptimeCheck, UptimePutaran, KejadianLogin, LoginGagal, CatatanError):
        assert sesi.query(model).count() == 1, model.__name__
    assert sesi.query(UptimeInsiden).count() == 1
    assert sesi.query(TrafficHarian).count() == 1


def test_job_rutin_lama_dipangkas_job_update_tidak(sesi, site):
    tua = SEKARANG - timedelta(days=15)
    job_selesai(sesi, site, JobType.collect_events, tua)
    job_selesai(sesi, site, JobType.scan_site, tua, JobStatus.failed)
    muda = job_selesai(sesi, site, JobType.collect_traffic, SEKARANG - timedelta(days=13))
    update = job_selesai(sesi, site, JobType.update_package, tua)
    connector = job_selesai(sesi, site, JobType.update_connector, tua)
    tertunda = buat_job(sesi, site.id, JobType.collect_events)

    pangkas(sesi, SEKARANG)
    sisa = {j.id for j in sesi.query(Job).all()}
    assert sisa == {muda.id, update.id, connector.id, tertunda.id}


def test_kejadian_admin_belum_diperiksa_tidak_pernah_dipangkas(sesi, site):
    """Admin baru yang belum pernah diklik "Sudah diperiksa" tidak boleh hilang
    lewat retensi, walau umurnya jauh melewati 90 hari -- kalau tidak, status
    merah "perlu_diperiksa" (wpmgr.keamanan) bisa padam sendiri tanpa ada yang
    benar-benar memeriksanya (Task 17 sengaja membuatnya tidak mengempis
    sendiri lewat waktu).
    """
    sangat_lama = SEKARANG - timedelta(days=400)
    sesi.add(KejadianLogin(site_id=site.id, id_di_site=1, waktu=sangat_lama, jenis="admin_baru",
                           username="backdoor", dicatat_pada=sangat_lama))
    # Kejadian non-admin yang sama tuanya tetap harus kena pangkas seperti biasa.
    sesi.add(KejadianLogin(site_id=site.id, id_di_site=2, waktu=sangat_lama, jenis="berhasil",
                           username="a", dicatat_pada=sangat_lama))
    sesi.commit()

    pangkas(sesi, SEKARANG)

    sisa = sesi.query(KejadianLogin).all()
    assert [k.jenis for k in sisa] == ["admin_baru"]
