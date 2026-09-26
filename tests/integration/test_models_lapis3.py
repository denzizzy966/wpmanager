import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    JOB_STAGING,
    JOB_STAGING_BACA,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
    Staging,
    StagingSnapshot,
    StagingUji,
    StatusStaging,
)

pytestmark = pytest.mark.integration


def _site_lain(sesi, nama="Lain"):
    s = Site(id=uuid.uuid4(), nama=nama, url=f"https://{uuid.uuid4().hex[:8]}.test",
             status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.commit()
    return s


def test_himpunan_job_staging():
    assert JOB_STAGING == {JobType.staging_tarik, JobType.staging_uji_update,
                           JobType.staging_dorong, JobType.staging_kembalikan}
    assert JOB_STAGING_BACA == {JobType.staging_tarik, JobType.staging_uji_update}


def test_staging_default_dan_kolom(sesi, site):
    st = Staging(site_id=site.id, nama="contoh-test")
    sesi.add(st)
    sesi.commit()
    sesi.refresh(st)
    assert st.status == StatusStaging.menyalin
    assert st.aktif is False
    assert (st.ukuran_file, st.ukuran_db) == (0, 0)
    assert st.dibuat_pada.tzinfo is not None
    assert st.tanda_air is None and st.dorong_gagal_pada is None and st.batal_diminta_pada is None


def test_satu_staging_per_site_dan_nama_unik(sesi, site):
    sesi.add(Staging(site_id=site.id, nama="a"))
    sesi.commit()
    sesi.add(Staging(site_id=site.id, nama="b"))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()
    lain = _site_lain(sesi)
    sesi.add(Staging(site_id=lain.id, nama="a"))
    with pytest.raises(IntegrityError):
        sesi.commit()


def test_hapus_site_menghapus_staging_snapshot_dan_uji(sesi, site):
    job = buat_job(sesi, site.id, JobType.staging_uji_update)
    sesi.add_all([
        Staging(site_id=site.id, nama="c"),
        StagingSnapshot(site_id=site.id, job_id=None, jenis="sebelum_dorong", status="tersedia",
                        ukuran=10, path="x/snapshot/j1"),
        StagingUji(site_id=site.id, job_id=job.id, paket=[], hasil="lolos", pemeriksaan={}),
    ])
    sesi.commit()
    sesi.delete(sesi.get(Site, site.id))
    sesi.commit()
    assert sesi.scalars(select(Staging)).all() == []
    assert sesi.scalars(select(StagingSnapshot)).all() == []
    assert sesi.scalars(select(StagingUji)).all() == []


def test_hapus_job_tidak_menghapus_riwayat_uji(sesi, site):
    job = buat_job(sesi, site.id, JobType.staging_uji_update)
    uji = StagingUji(site_id=site.id, job_id=job.id, paket=[{"tipe": "plugin"}],
                     hasil="gagal", pemeriksaan={"alasan": ["x"]})
    sesi.add(uji)
    sesi.commit()
    sesi.delete(job)
    sesi.commit()
    sesi.refresh(uji)
    assert uji.job_id is None


def test_satu_job_staging_aktif_per_site(sesi, site):
    buat_job(sesi, site.id, JobType.staging_tarik)
    with pytest.raises(IntegrityError):
        buat_job(sesi, site.id, JobType.staging_dorong)
    sesi.rollback()


def test_job_non_staging_tidak_terhalang_indeks(sesi, site):
    buat_job(sesi, site.id, JobType.staging_tarik)
    buat_job(sesi, site.id, JobType.scan_site)
    buat_job(sesi, site.id, JobType.scan_site)
    assert sesi.query(Job).count() == 3


def test_job_staging_baru_boleh_setelah_yang_lama_selesai(sesi, site):
    lama = buat_job(sesi, site.id, JobType.staging_tarik)
    lama.status = JobStatus.failed
    lama.finished_at = datetime.now(timezone.utc)
    sesi.commit()
    buat_job(sesi, site.id, JobType.staging_tarik)
    lain = _site_lain(sesi)
    buat_job(sesi, lain.id, JobType.staging_tarik)
    assert sesi.query(Job).filter(Job.tipe == JobType.staging_tarik).count() == 3
