import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    JOB_HOSTING,
    JOB_RUNTIME,
    JOB_RUNTIME_BACA,
    JOB_STAGING,
    HostingBackup,
    HostingVps,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
    StatusHosting,
)

pytestmark = pytest.mark.integration


def _site_lain(sesi):
    s = Site(id=uuid.uuid4(), nama="Lain", url=f"https://{uuid.uuid4().hex[:8]}.test",
             status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.commit()
    return s


def _hosting(site_id, nama="toko-co-id", domain="toko.co.id"):
    return HostingVps(site_id=site_id, nama=nama, domain=domain, dengan_www=True, ip_lama="93.184.216.34")


def test_himpunan_job_runtime():
    assert JOB_HOSTING == {JobType.pindah_tarik, JobType.pindah_aktifkan, JobType.backup_hosting}
    assert JOB_RUNTIME == JOB_STAGING | JOB_HOSTING
    assert JOB_RUNTIME_BACA == {JobType.staging_tarik, JobType.staging_uji_update, JobType.pindah_tarik}


def test_hosting_default(sesi, site):
    h = _hosting(site.id)
    sesi.add(h)
    sesi.commit()
    sesi.refresh(h)
    assert h.status == StatusHosting.menyalin
    assert (h.ukuran_file, h.ukuran_db, h.sertifikat_gagal_kali) == (0, 0, 0)
    assert h.dilayani_vps_pada is None and h.aktif_pada is None and h.dns_hasil is None
    assert h.dibuat_pada.tzinfo is not None


def test_satu_hosting_per_site_nama_dan_domain_unik(sesi, site):
    sesi.add(_hosting(site.id))
    sesi.commit()
    sesi.add(_hosting(site.id, nama="b", domain="b.co.id"))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()
    lain = _site_lain(sesi)
    sesi.add(_hosting(lain.id, domain="lain.co.id"))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()
    sesi.add(_hosting(lain.id, nama="lain"))
    with pytest.raises(IntegrityError):
        sesi.commit()


def test_gagal_asal_dibatasi(sesi, site):
    h = _hosting(site.id)
    h.gagal_asal = "entah"
    sesi.add(h)
    with pytest.raises(IntegrityError):
        sesi.commit()


def test_hapus_site_menghapus_hosting_dan_backup(sesi, site):
    sesi.add(_hosting(site.id))
    sesi.add(HostingBackup(site_id=site.id, tujuan="lokal", stempel="20261003T023000Z", status="tersedia",
                           manual=False, ukuran_db=1, ukuran_file=2, sha256_db="a" * 64, sha256_file="b" * 64))
    sesi.commit()
    sesi.delete(sesi.get(Site, site.id))
    sesi.commit()
    assert sesi.scalars(select(HostingVps)).all() == []
    assert sesi.scalars(select(HostingBackup)).all() == []


def test_backup_unik_per_stempel_dan_job_set_null(sesi, site):
    job = buat_job(sesi, site.id, JobType.backup_hosting)
    b = HostingBackup(site_id=site.id, job_id=job.id, tujuan="lokal", stempel="20261003T023000Z",
                      status="tersedia", manual=True, ukuran_db=1, ukuran_file=2,
                      sha256_db="a" * 64, sha256_file="b" * 64)
    sesi.add(b)
    sesi.commit()
    sesi.add(HostingBackup(site_id=site.id, tujuan="lokal", stempel="20261003T023000Z", status="tersedia",
                           manual=False, ukuran_db=1, ukuran_file=2, sha256_db="a" * 64, sha256_file="b" * 64))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()
    sesi.delete(sesi.get(Job, job.id))
    sesi.commit()
    sesi.refresh(b)
    assert b.job_id is None


def test_satu_job_hosting_aktif_per_site(sesi, site):
    buat_job(sesi, site.id, JobType.pindah_tarik)
    with pytest.raises(IntegrityError):
        buat_job(sesi, site.id, JobType.backup_hosting)
    sesi.rollback()


def test_job_hosting_dan_staging_punya_indeks_masing_masing(sesi, site):
    buat_job(sesi, site.id, JobType.pindah_tarik)
    buat_job(sesi, site.id, JobType.staging_tarik)
    buat_job(sesi, site.id, JobType.scan_site)
    assert sesi.query(Job).count() == 3


def test_job_hosting_baru_boleh_setelah_yang_lama_selesai(sesi, site):
    lama = buat_job(sesi, site.id, JobType.pindah_aktifkan)
    lama.status = JobStatus.failed
    lama.finished_at = datetime.now(timezone.utc)
    sesi.commit()
    buat_job(sesi, site.id, JobType.pindah_aktifkan)
    assert sesi.query(Job).filter(Job.tipe == JobType.pindah_aktifkan).count() == 2
