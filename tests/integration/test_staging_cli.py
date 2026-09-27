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
    assert not [n for n in os.listdir(akar) if n.startswith(".hapus-")]


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
    (akar / str(hidup.site_id) / "snapshot" / "j77").mkdir()
    os.utime(akar / str(hidup.site_id) / "snapshot" / "j77", (tua, tua))
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
    for nama in ("tarik", "dorong", "snapshot/j0", "snapshot/j1", "snapshot/j77"):
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


# ---- snapshot yatim, nisan, kunci baris (fix round 1) -----------------------------


def _dir_tua(p):
    p.mkdir(parents=True)
    tua = (SEKARANG - timedelta(days=2)).timestamp()
    os.utime(p, (tua, tua))
    return p


def test_pangkas_snapshot_yatim(sesi, staging_aktif):
    """`snapshot/j<id>` tanpa baris sah, bukan kandidat rekonsiliasi, dan > 24 jam dihapus."""
    akar = staging_aktif
    hidup = _staging(sesi, "hidup")
    snap = akar / str(hidup.site_id) / "snapshot"
    kandidat = buat_job(sesi, hidup.site_id, JobType.staging_dorong,
                        payload={"kemajuan": {"unggah_mulai": True, "dorong_id": "a" * 32}})
    kandidat.status = JobStatus.failed
    sesi.commit()
    # Nama direktori lain tidak boleh kebetulan sama dengan id job kandidat.
    dasar = kandidat.id + 1000
    yatim = _dir_tua(snap / f"j{dasar}")
    (yatim / "meta.json").write_bytes(b"{}")
    tua = (SEKARANG - timedelta(days=2)).timestamp()
    os.utime(yatim, (tua, tua))
    dipangkas = _dir_tua(snap / f"j{dasar + 1}")
    sah = _dir_tua(snap / f"j{dasar + 2}")
    ditahan = _dir_tua(snap / f"j{kandidat.id}")
    muda = snap / f"j{dasar + 3}"
    muda.mkdir()
    lain = _dir_tua(snap / "bukan-j")
    for nama, status in ((f"j{dasar + 1}", "dipangkas"), (f"j{dasar + 2}", "tersedia")):
        sesi.add(StagingSnapshot(site_id=hidup.site_id, jenis="sebelum_dorong", status=status, ukuran=1,
                                 path=f"{hidup.site_id}/snapshot/{nama}"))
    # Site tanpa staging dengan snapshot sah: yatimnya juga dihapus, yang sah bertahan.
    s = Site(id=uuid.uuid4(), nama="ts", url="https://ts.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.flush()
    sah2 = _dir_tua(akar / str(s.id) / "snapshot" / "j1")
    yatim2 = _dir_tua(akar / str(s.id) / "snapshot" / "j2")
    sesi.add(StagingSnapshot(site_id=s.id, jenis="sebelum_dorong", status="dipakai", ukuran=1,
                             path=f"{s.id}/snapshot/j1"))
    sesi.commit()

    hasil = cron.pangkas_staging(sesi, PembantuPalsu(akar), SEKARANG)

    assert hasil == {"snapshot": 3, "direktori": 0, "sementara": 0, "router": 0}
    assert not yatim.exists() and not dipangkas.exists() and not yatim2.exists()
    for tetap in (sah, ditahan, muda, lain, sah2):
        assert tetap.is_dir(), tetap.name
    assert not [n for n in os.listdir(akar) if n.startswith(".hapus-")]


def test_pangkas_snapshot_yatim_symlink_tidak_diikuti(sesi, staging_aktif, luar):
    akar = staging_aktif
    hidup = _staging(sesi, "hidup")
    snap = akar / str(hidup.site_id) / "snapshot"
    snap.mkdir(parents=True)
    tua = (SEKARANG - timedelta(days=2)).timestamp()
    os.utime(luar, (tua, tua))
    os.symlink(luar, snap / "j5", target_is_directory=True)

    hasil = cron.pangkas_staging(sesi, PembantuPalsu(akar), SEKARANG)

    assert hasil["snapshot"] == 0
    assert _utuh(luar)
    assert os.path.islink(snap / "j5")


def test_pangkas_nisan_sisa_putaran_mati_dihapus(sesi, staging_aktif, luar):
    akar = staging_aktif
    nisan = akar / f".hapus-{uuid.uuid4()}-{'0' * 12}"
    (nisan / "files").mkdir(parents=True)
    os.symlink(luar, nisan / "files" / "tautan", target_is_directory=True)
    nisan_tautan = akar / f".hapus-{uuid.uuid4()}-{'1' * 12}"
    os.symlink(luar, nisan_tautan, target_is_directory=True)
    bukan = akar / ".hapus-lain"
    bukan.mkdir()

    cron.pangkas_staging(sesi, PembantuPalsu(akar), SEKARANG)

    assert not nisan.exists() and not os.path.lexists(nisan_tautan)
    assert bukan.is_dir()
    assert _utuh(luar)


def test_pangkas_menunggu_kunci_baris_site(sesi, engine, staging_aktif):
    """Route antre job staging mengunci baris sites yang sama: pemangkasan menunggu, lalu memeriksa ulang."""
    import threading

    from sqlalchemy import text

    akar = staging_aktif
    s = Site(id=uuid.uuid4(), nama="ts", url="https://ts.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.commit()
    (akar / str(s.id) / "files").mkdir(parents=True)
    lain = sessionmaker(bind=engine, future=True)()
    lain.execute(text("SELECT id FROM sites WHERE id = :id FOR UPDATE"), {"id": s.id})
    sesi_cron = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    hasil = {}
    t = threading.Thread(target=lambda: hasil.update(cron.pangkas_staging(sesi_cron, PembantuPalsu(akar), SEKARANG)))
    t.start()
    try:
        t.join(1.0)
        menunggu = t.is_alive()
        # Pemegang kunci mengantrekan job staging sebelum melepas kunci.
        buat_job(lain, s.id, JobType.staging_kembalikan)
    finally:
        lain.rollback()
        lain.close()
        t.join(10)
        sesi_cron.close()
    assert menunggu, "pemangkasan tidak menunggu kunci baris sites"
    assert not t.is_alive()
    assert hasil["direktori"] == 0
    assert (akar / str(s.id) / "files").is_dir()


def test_pangkas_router_galat_hapus_tidak_menghentikan_putaran(sesi, staging_aktif, monkeypatch):
    from pathlib import Path

    akar = staging_aktif
    (akar / "router").mkdir(parents=True)
    for nama in ("a.rahasia", "b.rahasia"):
        (akar / "router" / nama).write_bytes(b"e" * 64)
    asli = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == "a.rahasia":
            raise PermissionError(13, "ditolak")
        asli(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    pb = PembantuPalsu(akar)

    hasil = cron.pangkas_staging(sesi, pb, SEKARANG)

    assert hasil["router"] == 1
    assert (akar / "router" / "a.rahasia").exists() and not (akar / "router" / "b.rahasia").exists()
    assert ("router_muat",) in pb.panggilan


def test_pangkas_akar_symlink_diperingatkan_dan_dilewati(sesi, staging_aktif, luar, monkeypatch, caplog):
    from wpmgr.config import get_settings

    nyata = luar / "stg"
    yatim = nyata / str(uuid.uuid4())
    (yatim / "files").mkdir(parents=True)
    tautan = staging_aktif.parent / "stg-tautan"
    os.symlink(nyata, tautan, target_is_directory=True)
    monkeypatch.setenv("WPMGR_STAGING_DIR", str(tautan))
    get_settings.cache_clear()

    with caplog.at_level("WARNING", logger="wpmgr.staging.cron"):
        hasil = cron.pangkas_staging(sesi, PembantuPalsu(tautan), SEKARANG)

    assert hasil == {"snapshot": 0, "direktori": 0, "sementara": 0, "router": 0}
    assert yatim.is_dir()
    assert any("symlink" in r.getMessage() for r in caplog.records)


def test_jeda_otomatis_memeriksa_ulang_status_di_bawah_kunci(sesi, engine, staging_aktif):
    """Status yang berubah sesudah daftar diambil (mis. tarik dimulai) dibaca ulang dengan FOR UPDATE."""
    lama = SEKARANG - timedelta(days=9)
    a = _staging(sesi, "a", aktif=True, status=StatusStaging.siap, ditarik_pada=lama)
    b = _staging(sesi, "b", aktif=True, status=StatusStaging.siap, ditarik_pada=lama)
    pb = PembantuPalsu(staging_aktif)
    asli = pb.jeda

    def jeda(nama):
        asli(nama)
        if nama == "a":
            with sessionmaker(bind=engine, future=True)() as lain:
                lain.get(Staging, b.id).status = StatusStaging.menyalin
                lain.commit()

    pb.jeda = jeda
    assert cron.jeda_otomatis(sesi, pb, SEKARANG) == 1
    assert ("jeda", "b") not in pb.panggilan
    sesi.refresh(a)
    sesi.refresh(b)
    assert (a.aktif, a.status) == (False, StatusStaging.dijeda)
    assert (b.aktif, b.status) == (True, StatusStaging.menyalin)


def test_jeda_otomatis_menunggu_kunci_baris_staging(sesi, engine, staging_aktif):
    import threading

    from sqlalchemy import text

    st = _staging(sesi, "a", aktif=True, status=StatusStaging.siap, ditarik_pada=SEKARANG - timedelta(days=9))
    lain = sessionmaker(bind=engine, future=True)()
    lain.execute(text("SELECT id FROM staging WHERE id = :id FOR UPDATE"), {"id": st.id})
    sesi_cron = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    pb = PembantuPalsu(staging_aktif)
    hasil = []
    t = threading.Thread(target=lambda: hasil.append(cron.jeda_otomatis(sesi_cron, pb, SEKARANG)))
    t.start()
    try:
        t.join(1.0)
        menunggu = t.is_alive()
        buat_job(lain, st.site_id, JobType.staging_tarik)
    finally:
        lain.rollback()
        lain.close()
        t.join(10)
        sesi_cron.close()
    assert menunggu, "jeda otomatis tidak menunggu kunci baris staging"
    assert hasil == [0] and "jeda" not in pb.nama_panggilan()


# ---- kontrak kunci sites -> staging (fix round 2) ---------------------------------


def test_kunci_site_tidak_memblokir_insert_foreign_key(sesi, engine):
    """FOR NO KEY UPDATE: INSERT baris anak (FOR KEY SHARE atas sites) tidak menunggu kunci pemangkasan.

    Dengan FOR UPDATE, auto-pause (memegang staging, lalu INSERT activity_log)
    dan route (memegang sites, lalu menunggu staging) membentuk siklus.
    """
    from sqlalchemy import text

    s = Site(id=uuid.uuid4(), nama="ts", url="https://ts.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.commit()
    pemegang = sessionmaker(bind=engine, future=True)()
    penulis = sessionmaker(bind=engine, future=True)()
    try:
        cron._kunci_site(pemegang, s.id)
        penulis.execute(text("SET LOCAL lock_timeout = '2s'"))
        penulis.add(ActivityLog(site_id=s.id, level="info", pesan="tidak menunggu"))
        penulis.commit()
    finally:
        penulis.rollback()
        penulis.close()
        pemegang.rollback()
        pemegang.close()
    assert sesi.query(ActivityLog).filter(ActivityLog.site_id == s.id).count() == 1


def test_jeda_otomatis_mengunci_site_sebelum_staging(sesi, engine, staging_aktif):
    """Urutan kunci sama dengan route antre: sites dulu, lalu staging."""
    import threading

    from sqlalchemy import text

    st = _staging(sesi, "a", aktif=True, status=StatusStaging.siap, ditarik_pada=SEKARANG - timedelta(days=9))
    lain = sessionmaker(bind=engine, future=True)()
    lain.execute(text("SELECT id FROM sites WHERE id = :id FOR NO KEY UPDATE"), {"id": st.site_id})
    sesi_cron = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    pb = PembantuPalsu(staging_aktif)
    hasil = []
    t = threading.Thread(target=lambda: hasil.append(cron.jeda_otomatis(sesi_cron, pb, SEKARANG)))
    t.start()
    try:
        t.join(1.0)
        menunggu = t.is_alive()
        buat_job(lain, st.site_id, JobType.staging_tarik)
    finally:
        lain.rollback()
        lain.close()
        t.join(10)
        sesi_cron.close()
    assert menunggu, "jeda otomatis tidak mengunci baris sites sebelum staging"
    assert hasil == [0] and "jeda" not in pb.nama_panggilan()


def test_pangkas_snapshot_dihapus_sesudah_kunci_dilepas(sesi, engine, staging_aktif, monkeypatch):
    """rmtree snapshot yang dipangkas (bisa GB) berjalan sesudah commit, di luar kunci baris sites."""
    from sqlalchemy import text

    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_SNAPSHOT", "1")
    get_settings.cache_clear()
    akar = staging_aktif
    hidup = _staging(sesi, "hidup")
    for i in range(2):
        (akar / str(hidup.site_id) / "snapshot" / f"j{i}").mkdir(parents=True)
        sesi.add(StagingSnapshot(site_id=hidup.site_id, jenis="sebelum_dorong", status="tersedia", ukuran=1,
                                 path=f"{hidup.site_id}/snapshot/j{i}", dibuat_pada=SEKARANG - timedelta(days=2 - i)))
    sesi.commit()
    asli = cron._hapus_nisan
    saat_hapus = []

    def hapus_nisan(akar_, nisan):
        # Kunci baris sites harus sudah bebas: NOWAIT gagal seketika bila masih dipegang.
        with sessionmaker(bind=engine, future=True)() as lain:
            lain.execute(text("SELECT id FROM sites WHERE id = :id FOR UPDATE NOWAIT"), {"id": hidup.site_id})
            lain.rollback()
        saat_hapus.append(nisan)
        asli(akar_, nisan)

    monkeypatch.setattr(cron, "_hapus_nisan", hapus_nisan)
    hasil = cron.pangkas_staging(sesi, PembantuPalsu(akar), SEKARANG)

    assert hasil["snapshot"] == 1 and len(saat_hapus) == 1
    assert not (akar / str(hidup.site_id) / "snapshot" / "j0").exists()
    assert (akar / str(hidup.site_id) / "snapshot" / "j1").is_dir()
    assert not [n for n in os.listdir(akar) if n.startswith(".hapus-")]


def test_pangkas_galat_di_tengah_rollback_bukan_commit(sesi, staging_aktif, monkeypatch):
    """Galat di tengah satu site: transaksi di-rollback, nisan yang sudah dibuat dihapus putaran berikutnya."""
    akar = staging_aktif
    s = Site(id=uuid.uuid4(), nama="ts", url="https://ts.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.commit()
    (akar / str(s.id) / "files").mkdir(parents=True)
    asli = cron._pangkas_satu_site

    def rusak(sesi_, akar_, nama, sekarang, hasil):
        asli(sesi_, akar_, nama, sekarang, hasil)
        sesi_.add(ActivityLog(site_id=s.id, level="info", pesan="setengah jalan"))
        raise RuntimeError("galat di tengah")

    monkeypatch.setattr(cron, "_pangkas_satu_site", rusak)
    with pytest.raises(RuntimeError):
        cron.pangkas_staging(sesi, PembantuPalsu(akar), SEKARANG)
    assert sesi.query(ActivityLog).count() == 0
    assert [n for n in os.listdir(akar) if n.startswith(".hapus-")]

    monkeypatch.setattr(cron, "_pangkas_satu_site", asli)
    cron.pangkas_staging(sesi, PembantuPalsu(akar), SEKARANG)
    assert not [n for n in os.listdir(akar) if n.startswith(".hapus-")]


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
