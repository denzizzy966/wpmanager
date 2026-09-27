import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker
from staging_palsu import GB, PembantuPalsu

from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
    Staging,
    StagingSnapshot,
    StatusStaging,
)
from wpmgr.staging import cron
from wpmgr.staging.pembantu import GalatPembantu, StatusPembantu

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _staging(sesi, nama, **kolom):
    s = Site(id=uuid.uuid4(), nama=nama, url=f"https://{nama}.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.flush()
    st = Staging(site_id=s.id, nama=nama, **kolom)
    sesi.add(st)
    sesi.commit()
    return st


def test_jeda_otomatis(sesi, staging_aktif):
    lama = SEKARANG - timedelta(days=5)
    diam = _staging(sesi, "diam", aktif=True, status=StatusStaging.siap, dibuka_pada=lama, ditarik_pada=lama)
    ramai = _staging(sesi, "ramai", aktif=True, status=StatusStaging.siap, dibuka_pada=lama, ditarik_pada=lama)
    sibuk = _staging(sesi, "sibuk", aktif=True, status=StatusStaging.siap, dibuka_pada=lama, ditarik_pada=lama)
    buat_job(sesi, sibuk.site_id, JobType.staging_tarik)
    pb = PembantuPalsu(staging_aktif)
    pb.status_palsu = StatusPembantu(8 * GB, 1, 1, {}, {"ramai": int((SEKARANG - timedelta(hours=2)).timestamp())})

    assert cron.jeda_otomatis(sesi, pb, SEKARANG) == 1
    assert ("jeda", "diam") in pb.panggilan
    assert ("jeda", "ramai") not in pb.panggilan and ("jeda", "sibuk") not in pb.panggilan
    for st in (diam, ramai, sibuk):
        sesi.refresh(st)
    assert (diam.aktif, diam.status) == (False, StatusStaging.dijeda)
    assert ramai.aktif is True
    assert ramai.dibuka_pada == SEKARANG - timedelta(hours=2)
    log = sesi.query(ActivityLog).filter(ActivityLog.site_id == diam.site_id).one()
    assert log.pesan == "Staging dijeda otomatis setelah 3 hari tanpa akses"


def test_jeda_otomatis_galat_pembantu_tidak_menghentikan_putaran(sesi, staging_aktif):
    lama = SEKARANG - timedelta(days=9)
    a = _staging(sesi, "a", aktif=True, status=StatusStaging.siap, ditarik_pada=lama)
    b = _staging(sesi, "b", aktif=True, status=StatusStaging.siap, ditarik_pada=lama)
    pb = PembantuPalsu(staging_aktif)
    asli = pb.jeda

    def jeda(nama):
        if nama == "a":
            raise GalatPembantu("docker", "Perintah Docker di server staging gagal.")
        asli(nama)

    pb.jeda = jeda
    assert cron.jeda_otomatis(sesi, pb, SEKARANG) == 1
    sesi.refresh(a)
    sesi.refresh(b)
    assert a.aktif is True and b.aktif is False


def test_jeda_otomatis_hanya_status_siap_dan_asal_gagal_tetap(sesi, staging_aktif):
    """R20-R22: cron tidak pernah mengubah `gagal_asal`; hanya `siap` -> `dijeda`."""
    lama = SEKARANG - timedelta(days=9)
    lain = {}
    for status in (StatusStaging.gagal, StatusStaging.menyalin, StatusStaging.berjalan_uji,
                   StatusStaging.mendorong):
        lain[status] = _staging(sesi, status.value.replace("_", "-"), aktif=True, status=status, ditarik_pada=lama,
                                gagal_asal="salinan" if status == StatusStaging.gagal else None,
                                galat="x" if status == StatusStaging.gagal else None)
    pb = PembantuPalsu(staging_aktif)

    assert cron.jeda_otomatis(sesi, pb, SEKARANG) == 0
    assert "jeda" not in pb.nama_panggilan()
    for status, st in lain.items():
        sesi.refresh(st)
        assert (st.aktif, st.status) == (True, status)
    assert lain[StatusStaging.gagal].gagal_asal == "salinan"


def test_jeda_otomatis_waktu_masa_depan_tidak_menahan_selamanya(sesi, staging_aktif):
    """Jam yang melompat ke depan tidak boleh membuat staging tampak baru diakses selamanya."""
    lama = SEKARANG - timedelta(days=9)
    depan = SEKARANG + timedelta(days=400)
    log_depan = _staging(sesi, "log-depan", aktif=True, status=StatusStaging.siap, ditarik_pada=lama)
    kolom_depan = _staging(sesi, "kolom-depan", aktif=True, status=StatusStaging.siap, ditarik_pada=lama,
                           dibuka_pada=depan)
    pb = PembantuPalsu(staging_aktif)
    pb.status_palsu = StatusPembantu(8 * GB, 1, 1, {}, {"log-depan": int(depan.timestamp())})

    assert cron.jeda_otomatis(sesi, pb, SEKARANG) == 2
    sesi.refresh(log_depan)
    sesi.refresh(kolom_depan)
    assert log_depan.dibuka_pada is None
    assert log_depan.aktif is False and kolom_depan.aktif is False


def test_jeda_otomatis_status_pembantu_gagal_tidak_menjeda_apa_pun(sesi, staging_aktif):
    _staging(sesi, "x", aktif=True, status=StatusStaging.siap, ditarik_pada=SEKARANG - timedelta(days=9))
    pb = PembantuPalsu(staging_aktif)
    pb.gagal["status"] = GalatPembantu("docker", "Perintah Docker di server staging gagal.")

    assert cron.jeda_otomatis(sesi, pb, SEKARANG) == 0
    assert "jeda" not in pb.nama_panggilan()


def test_perpanjang_sertifikat(sesi, staging_aktif):
    x = _staging(sesi, "x", ditarik_pada=SEKARANG)
    y = _staging(sesi, "y", ditarik_pada=SEKARANG)
    _staging(sesi, "belum-ditarik")
    pb = PembantuPalsu(staging_aktif)
    asli = pb.sertifikat

    def sertifikat(nama):
        if nama == "y":
            raise GalatPembantu("sertifikat", "sertifikat untuk y.staging.contoh.id belum dapat diterbitkan")
        asli(nama)

    pb.sertifikat = sertifikat
    assert cron.perpanjang_sertifikat(sesi, pb, SEKARANG) == {"berhasil": 1, "gagal": 1}
    sesi.refresh(x)
    sesi.refresh(y)
    assert x.sertifikat_pada == SEKARANG
    assert y.sertifikat_pada is None
    assert sesi.query(ActivityLog).filter(ActivityLog.level == "warning").count() == 1


def test_perpanjang_sertifikat_pesan_ui_tetap(sesi, staging_aktif):
    """Teks galat pembantu (bisa memuat path/stderr) tidak pernah masuk log aktivitas."""
    _staging(sesi, "y", ditarik_pada=SEKARANG)
    pb = PembantuPalsu(staging_aktif)
    pb.gagal["sertifikat"] = GalatPembantu("sertifikat", "/etc/letsencrypt/live/y rahasia stderr")

    assert cron.perpanjang_sertifikat(sesi, pb, SEKARANG) == {"berhasil": 0, "gagal": 1}
    log = sesi.query(ActivityLog).one()
    assert log.pesan == cron.PESAN_SERTIFIKAT_GAGAL
    assert "/etc" not in log.pesan and "stderr" not in log.pesan


def test_pangkas_staging(sesi, staging_aktif, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_SNAPSHOT", "1")
    get_settings.cache_clear()
    hidup = _staging(sesi, "hidup")
    akar = staging_aktif
    (akar / str(hidup.site_id) / "tarik").mkdir(parents=True)
    tua = (SEKARANG - timedelta(days=2)).timestamp()
    os.utime(akar / str(hidup.site_id) / "tarik", (tua, tua))
    (akar / str(hidup.site_id) / "dorong").mkdir()
    for i in range(2):
        d = akar / str(hidup.site_id) / "snapshot" / f"j{i}"
        d.mkdir(parents=True)
        sesi.add(StagingSnapshot(site_id=hidup.site_id, jenis="sebelum_dorong", status="tersedia", ukuran=1,
                                 path=f"{hidup.site_id}/snapshot/j{i}", dibuat_pada=SEKARANG - timedelta(days=2 - i)))
    yatim = akar / str(uuid.uuid4())
    (yatim / "files").mkdir(parents=True)
    tanpa_staging = Site(id=uuid.uuid4(), nama="ts", url="https://ts.test", status=SiteStatus.active,
                         secret_terenkripsi=b"x")
    sesi.add(tanpa_staging)
    sesi.flush()
    sisa = akar / str(tanpa_staging.id)
    (sisa / "files").mkdir(parents=True)
    (sisa / "snapshot" / "j9").mkdir(parents=True)
    sesi.add(StagingSnapshot(site_id=tanpa_staging.id, jenis="sebelum_dorong", status="tersedia", ukuran=1,
                             path=f"{tanpa_staging.id}/snapshot/j9"))
    (akar / "bukan-uuid").mkdir()
    (akar / "router").mkdir()
    (akar / "router" / "hidup.rahasia").write_bytes(b"e" * 64)
    (akar / "router" / "hilang.rahasia").write_bytes(b"e" * 64)
    (akar / "router" / "hilang.htpasswd").write_bytes(b"x")
    sesi.commit()
    pb = PembantuPalsu(akar)

    hasil = cron.pangkas_staging(sesi, pb, SEKARANG)

    assert hasil == {"snapshot": 1, "direktori": 2, "sementara": 1, "router": 2}
    assert not (akar / str(hidup.site_id) / "tarik").exists()
    assert (akar / str(hidup.site_id) / "dorong").exists()
    assert not (akar / str(hidup.site_id) / "snapshot" / "j0").exists()
    assert (akar / str(hidup.site_id) / "snapshot" / "j1").exists()
    assert not yatim.exists()
    assert not (sisa / "files").exists() and (sisa / "snapshot" / "j9").exists()
    assert (akar / "bukan-uuid").exists()
    assert not (akar / "router" / "hilang.rahasia").exists()
    assert (akar / "router" / "hidup.rahasia").exists()
    assert ("router_muat",) in pb.panggilan


# ---- keamanan penghapusan -----------------------------------------------------


def _bisa_symlink(tmp_path) -> bool:
    try:
        os.symlink(tmp_path / "x", tmp_path / "tautan-uji", target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return True


@pytest.fixture
def luar(tmp_path):
    """Direktori di luar akar staging yang tidak boleh tersentuh (mis. data PostgreSQL, .env)."""
    if not _bisa_symlink(tmp_path):
        pytest.skip("symlink tidak dapat dibuat di sistem ini")
    d = tmp_path / "luar"
    (d / "dalam").mkdir(parents=True)
    (d / "penting.txt").write_bytes(b"jangan dihapus")
    (d / "dalam" / "juga.txt").write_bytes(b"jangan dihapus")
    return d


def _utuh(luar) -> bool:
    return (luar / "penting.txt").read_bytes() == b"jangan dihapus" and (luar / "dalam" / "juga.txt").exists()


def test_pangkas_symlink_di_files_tidak_diikuti(sesi, staging_aktif, luar):
    akar = staging_aktif
    yatim = akar / str(uuid.uuid4())
    (yatim / "files" / "wp-content").mkdir(parents=True)
    os.symlink(luar, yatim / "files" / "wp-content" / "tautan", target_is_directory=True)
    os.symlink(luar / "penting.txt", yatim / "files" / "berkas-tautan")
    # Site tanpa staging dengan snapshot tersisa: hanya selain snapshot/ yang dihapus.
    s = Site(id=uuid.uuid4(), nama="ts", url="https://ts.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.flush()
    sisa = akar / str(s.id)
    (sisa / "snapshot" / "j1").mkdir(parents=True)
    (sisa / "log").mkdir()
    os.symlink(luar, sisa / "log" / "tautan", target_is_directory=True)
    os.symlink(luar, sisa / "files", target_is_directory=True)
    sesi.add(StagingSnapshot(site_id=s.id, jenis="sebelum_dorong", status="tersedia", ukuran=1,
                             path=f"{s.id}/snapshot/j1"))
    sesi.commit()

    hasil = cron.pangkas_staging(sesi, PembantuPalsu(akar), SEKARANG)

    assert hasil["direktori"] == 2
    assert _utuh(luar)
    assert not yatim.exists()
    assert not (sisa / "log").exists() and not os.path.lexists(sisa / "files")
    assert (sisa / "snapshot" / "j1").is_dir()


def test_pangkas_direktori_uuid_symlink_dilewati(sesi, staging_aktif, luar):
    akar = staging_aktif
    akar.mkdir(parents=True)
    tautan = akar / str(uuid.uuid4())
    os.symlink(luar, tautan, target_is_directory=True)
    # Juga symlink bernama UUID milik staging yang ada, dan tarik/ yang berupa symlink.
    hidup = _staging(sesi, "hidup")
    (akar / str(hidup.site_id)).mkdir()
    os.symlink(luar, akar / str(hidup.site_id) / "tarik", target_is_directory=True)
    tua = (SEKARANG - timedelta(days=2)).timestamp()
    os.utime(luar, (tua, tua))

    hasil = cron.pangkas_staging(sesi, PembantuPalsu(akar), SEKARANG)

    assert hasil == {"snapshot": 0, "direktori": 0, "sementara": 0, "router": 0}
    assert _utuh(luar)
    assert os.path.islink(tautan)


def test_hapus_dir_staging_menolak_rmtree_tidak_aman_di_linux(monkeypatch, staging_aktif):
    """Di Linux, rmtree wajib varian berbasis fd; tanpa itu lebih baik gagal keras."""
    import shutil

    from wpmgr.staging import dorong

    (staging_aktif / "x").mkdir(parents=True)
    monkeypatch.setattr(dorong.sys, "platform", "linux")
    monkeypatch.setattr(shutil.rmtree, "avoids_symlink_attacks", False, raising=False)
    with pytest.raises(RuntimeError):
        dorong.hapus_dir_staging("x")
    assert (staging_aktif / "x").is_dir()


# ---- job staging dan rekonsiliasi dorong -----------------------------------------


def test_pangkas_melewati_site_dengan_job_staging(sesi, staging_aktif, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_SNAPSHOT", "1")
    get_settings.cache_clear()
    akar = staging_aktif
    tua = (SEKARANG - timedelta(days=2)).timestamp()
    hidup = _staging(sesi, "hidup")
    for nama in ("tarik", "dorong"):
        (akar / str(hidup.site_id) / nama).mkdir(parents=True)
        os.utime(akar / str(hidup.site_id) / nama, (tua, tua))
    for i in range(2):
        (akar / str(hidup.site_id) / "snapshot" / f"j{i}").mkdir(parents=True)
        sesi.add(StagingSnapshot(site_id=hidup.site_id, jenis="sebelum_dorong", status="tersedia", ukuran=1,
                                 path=f"{hidup.site_id}/snapshot/j{i}", dibuat_pada=SEKARANG - timedelta(days=2 - i)))
    sesi.commit()
    buat_job(sesi, hidup.site_id, JobType.staging_dorong)
    # Kembalikan berjalan tanpa baris Staging (staging sudah dihapus).
    s = Site(id=uuid.uuid4(), nama="ts", url="https://ts.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.flush()
    (akar / str(s.id) / "dorong").mkdir(parents=True)
    (akar / str(s.id) / "snapshot" / "j5").mkdir(parents=True)
    sesi.add(StagingSnapshot(site_id=s.id, jenis="sebelum_dorong", status="tersedia", ukuran=1,
                             path=f"{s.id}/snapshot/j5"))
    sesi.commit()
    job = buat_job(sesi, s.id, JobType.staging_kembalikan)
    job.status = JobStatus.running
    sesi.commit()

    hasil = cron.pangkas_staging(sesi, PembantuPalsu(akar), SEKARANG)

    assert hasil == {"snapshot": 0, "direktori": 0, "sementara": 0, "router": 0}
    for nama in ("tarik", "dorong", "snapshot/j0", "snapshot/j1"):
        assert (akar / str(hidup.site_id) / nama).is_dir(), nama
    assert (akar / str(s.id) / "dorong").is_dir()
    assert sesi.query(StagingSnapshot).filter(StagingSnapshot.status == "tersedia").count() == 3


def test_pangkas_mempertahankan_snapshot_dorongan_lama_belum_bersih(sesi, staging_aktif, monkeypatch):
    """Rekonsiliasi dorong (Task 16) masih menganggap job lama kandidat: snapshotnya dipertahankan."""
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_SNAPSHOT", "1")
    get_settings.cache_clear()
    akar = staging_aktif
    hidup = _staging(sesi, "hidup")
    lama = buat_job(sesi, hidup.site_id, JobType.staging_dorong,
                    payload={"kemajuan": {"unggah_mulai": True, "dorong_id": "a" * 32}})
    lama.status = JobStatus.failed
    sesi.commit()
    bersih = buat_job(sesi, hidup.site_id, JobType.staging_dorong,
                      payload={"kemajuan": {"unggah_mulai": True, "produksi_bersih": True}})
    bersih.status = JobStatus.failed
    sesi.commit()
    baris = []
    for i, job_id in enumerate((lama.id, bersih.id, None)):
        (akar / str(hidup.site_id) / "snapshot" / f"j{i}").mkdir(parents=True)
        r = StagingSnapshot(site_id=hidup.site_id, job_id=job_id, jenis="sebelum_dorong", status="tersedia",
                            ukuran=1, path=f"{hidup.site_id}/snapshot/j{i}",
                            dibuat_pada=SEKARANG - timedelta(days=3 - i))
        sesi.add(r)
        baris.append(r)
    sesi.commit()

    hasil = cron.pangkas_staging(sesi, PembantuPalsu(akar), SEKARANG)

    assert hasil["snapshot"] == 1
    for r in baris:
        sesi.refresh(r)
    assert [r.status for r in baris] == ["tersedia", "dipangkas", "tersedia"]
    assert (akar / str(hidup.site_id) / "snapshot" / "j0").is_dir()
    assert not (akar / str(hidup.site_id) / "snapshot" / "j1").exists()
    assert sesi.get(Job, lama.id).status == JobStatus.failed


# ---- CLI ----------------------------------------------------------------------


@pytest.fixture
def cli(engine, monkeypatch, staging_aktif):
    from wpmgr import cli as modul
    from wpmgr import db

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    monkeypatch.setattr(db, "engine", engine)
    pb = PembantuPalsu(staging_aktif)
    monkeypatch.setattr(modul.Pembantu, "dari_setelan", classmethod(lambda cls, s=None: pb))
    return modul


def test_cli_dilewati_bila_kunci_dipegang(cli, engine):
    from wpmgr.kunci import KUNCI_STAGING_JEDA, kunci_advisory

    with kunci_advisory(engine, KUNCI_STAGING_JEDA) as dapat:
        assert dapat
        assert cli.staging_jeda_otomatis() == 0


def test_cli_sertifikat_dan_pangkas_dilewati_bila_kunci_dipegang(cli, engine):
    from wpmgr.kunci import (
        KUNCI_STAGING_PANGKAS,
        KUNCI_STAGING_SERTIFIKAT,
        kunci_advisory,
    )

    with kunci_advisory(engine, KUNCI_STAGING_SERTIFIKAT) as a, kunci_advisory(engine, KUNCI_STAGING_PANGKAS) as b:
        assert a and b
        assert cli.renew_staging_certs() is None
        assert cli.prune_staging() is None


def test_cli_dilewati_bila_fitur_mati(cli, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    get_settings.cache_clear()
    assert cli.staging_jeda_otomatis() == 0
    assert cli.renew_staging_certs() is None
    assert cli.prune_staging() is None


def test_cli_main_mengenal_subperintah(cli):
    for perintah in ("staging-jeda-otomatis", "renew-staging-certs", "prune-staging"):
        assert cli.main([perintah]) == 0
