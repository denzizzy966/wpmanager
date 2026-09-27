import errno
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from wpmgr.errors import (
    AUTH_ERROR,
    BAD_RESPONSE,
    BERKAS_HILANG,
    BLOCKED,
    CONNECTOR_MISSING,
    INTERNAL_ERROR,
    STAGING_DITOLAK,
    STAGING_GAGAL,
    TERLALU_BESAR,
    TRANSIENT,
    UNKNOWN,
    UPGRADE_FAILED,
    SiteError,
    klasifikasi_respons,
)
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import ambil_job, buat_job
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
    StatusStaging,
)
from wpmgr.staging import umum
from wpmgr.staging.aman import BATAS_DIUBAH
from wpmgr.staging.pembantu import GalatPembantu
from wpmgr.worker import jenis_worker, proses_satu

pytestmark = pytest.mark.integration


def _berjalan(sesi, site, tipe, oleh="w-lain"):
    job = buat_job(sesi, site.id, tipe)
    job.status = JobStatus.running
    job.locked_by = oleh
    job.locked_at = datetime.now(timezone.utc)
    sesi.commit()
    return job


def test_jenis_worker():
    assert jenis_worker("staging") == "staging"
    assert jenis_worker("staging2") == "staging"
    assert jenis_worker("1") == "umum"
    assert jenis_worker("") == "umum"


def test_tarik_boleh_berjalan_bersama_scan_di_site_yang_sama(sesi, site):
    _berjalan(sesi, site, JobType.scan_site)
    tarik = buat_job(sesi, site.id, JobType.staging_tarik)
    assert ambil_job(sesi, "w1", "staging").id == tarik.id


def test_dorong_menunggu_job_lain_di_site_yang_sama(sesi, site):
    _berjalan(sesi, site, JobType.scan_site)
    buat_job(sesi, site.id, JobType.staging_dorong)
    assert ambil_job(sesi, "w1", "staging") is None


def test_job_umum_menunggu_dorong_tetapi_tidak_menunggu_tarik(sesi, site):
    tarik = _berjalan(sesi, site, JobType.staging_tarik)
    scan = buat_job(sesi, site.id, JobType.scan_site)
    assert ambil_job(sesi, "w1", "umum").id == scan.id
    scan.status = JobStatus.success
    tarik.tipe = JobType.staging_dorong
    sesi.commit()
    buat_job(sesi, site.id, JobType.verify_site)
    assert ambil_job(sesi, "w1", "umum") is None


def test_jenis_worker_memisahkan_klaim(sesi, site):
    tarik = buat_job(sesi, site.id, JobType.staging_tarik)
    scan = buat_job(sesi, site.id, JobType.scan_site, scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert ambil_job(sesi, "w1", "staging").id == tarik.id
    assert ambil_job(sesi, "w2", "umum").id == scan.id


def test_tanpa_jenis_mengklaim_apa_saja(sesi, site):
    tarik = buat_job(sesi, site.id, JobType.staging_tarik)
    assert ambil_job(sesi, "w1").id == tarik.id


def test_detak_memperbarui_locked_at_dan_mendeteksi_klaim_hilang(sesi, site):
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    job = ambil_job(sesi, "w1", "staging")
    sesi.execute(text("UPDATE jobs SET locked_at = now() - interval '20 minutes' WHERE id = :i"), {"i": job.id})
    sesi.commit()
    umum.detak(sesi, job)
    sesi.refresh(job)
    assert job.locked_at > datetime.now(timezone.utc) - timedelta(minutes=1)
    sesi.execute(text("UPDATE jobs SET locked_by = 'w-lain' WHERE id = :i"), {"i": job.id})
    sesi.commit()
    with pytest.raises(umum.KlaimHilang):
        umum.detak(sesi, job)


def test_detak_diam_untuk_job_yang_belum_diklaim(sesi, site):
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    umum.detak(sesi, job)  # tidak melempar
    sesi.expire_all()
    job = sesi.get(Job, job.id)
    assert job.locked_at is None
    assert job.locked_by is None
    assert job.status == JobStatus.pending


def test_simpan_kemajuan_menggabungkan(sesi, site):
    job = buat_job(sesi, site.id, JobType.staging_tarik, {"konfirmasi": True})
    umum.simpan_kemajuan(sesi, job, tahap="berkas", byte_selesai=10)
    umum.simpan_kemajuan(sesi, job, byte_selesai=20)
    sesi.expire_all()
    job = sesi.get(Job, job.id)
    assert job.payload["konfirmasi"] is True
    assert job.payload["kemajuan"]["tahap"] == "berkas"
    assert job.payload["kemajuan"]["byte_selesai"] == 20


def test_ulangi_potongan():
    panggilan = []

    def gagal_dua_kali():
        panggilan.append(1)
        if len(panggilan) < 3:
            raise SiteError(TRANSIENT, "putus")
        return "ok"

    assert umum.ulangi(gagal_dua_kali, tidur=lambda d: None) == "ok"
    panggilan.clear()

    def selalu_gagal():
        panggilan.append(1)
        raise SiteError(TRANSIENT, "x")

    with pytest.raises(SiteError):
        umum.ulangi(selalu_gagal, tidur=lambda d: None)
    assert len(panggilan) == 3
    panggilan.clear()

    def tidak_diulang():
        panggilan.append(1)
        raise SiteError(UPGRADE_FAILED, "tidak")

    with pytest.raises(SiteError):
        umum.ulangi(tidak_diulang, tidur=lambda d: None)
    assert len(panggilan) == 1


def test_periksa_batal(sesi, site_staging):
    umum.periksa_batal(sesi, site_staging)
    site_staging.batal_diminta_pada = datetime.now(timezone.utc)
    sesi.commit()
    with pytest.raises(umum.Dibatalkan):
        umum.periksa_batal(sesi, site_staging)


def test_muat_staging_menolak_bila_fitur_mati(sesi, site, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    get_settings.cache_clear()
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    with pytest.raises(SiteError) as e:
        umum.muat_staging(sesi, job)
    assert e.value.error_class == "staging_ditolak"


def _jalankan(sesi, site_staging, inti):
    job = buat_job(sesi, site_staging.site_id, JobType.staging_tarik)
    return job, lambda: umum.jalankan_staging(sesi, job, inti, StatusStaging.menyalin, "Tarik staging")


def test_pembungkus_galat_pembantu_menjadi_status_gagal(sesi, site_staging):
    def inti(sesi, job, site, staging):
        assert staging.status == StatusStaging.menyalin
        raise GalatPembantu("docker", "Perintah Docker di server staging gagal.")

    _, jalan = _jalankan(sesi, site_staging, inti)
    with pytest.raises(SiteError) as e:
        jalan()
    assert e.value.error_class == STAGING_GAGAL
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal
    assert site_staging.galat == "Perintah Docker di server staging gagal."


def test_pembungkus_disk_penuh_tanpa_path(sesi, site_staging):
    def inti(sesi, job, site, staging):
        raise OSError(errno.ENOSPC, "No space left on device", "/var/lib/wpmgr/staging/x/files/a")

    _, jalan = _jalankan(sesi, site_staging, inti)
    with pytest.raises(SiteError) as e:
        jalan()
    sesi.refresh(site_staging)
    assert "Disk VPS penuh" in site_staging.galat
    assert "/var/lib" not in site_staging.galat and "/var/lib" not in e.value.pesan


def test_pembungkus_batal(sesi, site_staging):
    site_staging.ditarik_pada = datetime.now(timezone.utc)
    sesi.commit()

    def inti(sesi, job, site, staging):
        staging.batal_diminta_pada = datetime.now(timezone.utc)
        sesi.commit()
        umum.periksa_batal(sesi, staging)

    _, jalan = _jalankan(sesi, site_staging, inti)
    with pytest.raises(SiteError):
        jalan()
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap
    assert site_staging.batal_diminta_pada is None
    assert site_staging.galat == "Dibatalkan oleh pengguna."


def test_pembungkus_sukses_membersihkan_galat(sesi, site_staging):
    site_staging.galat = "lama"
    sesi.commit()
    _, jalan = _jalankan(sesi, site_staging, lambda sesi, job, site, staging: {"ok": True})
    assert jalan() == {"ok": True}
    sesi.refresh(site_staging)
    assert site_staging.galat is None


def test_galat_sementara_yang_masih_diulang_tidak_menandai_gagal(sesi, site_staging):
    def inti(sesi, job, site, staging):
        raise SiteError(TRANSIENT, "koneksi putus")

    job, jalan = _jalankan(sesi, site_staging, inti)
    job.attempts = 1
    sesi.commit()
    with pytest.raises(SiteError):
        jalan()
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.menyalin
    assert "dilanjutkan" in site_staging.galat


def test_kegagalan_staging_tidak_menimpa_last_error_site(sesi, site_staging, monkeypatch):
    from wpmgr.models import Site

    site = sesi.get(Site, site_staging.site_id)
    site.last_error = "galat lama yang asli"
    sesi.commit()

    def inti_gagal(sesi, job, klien):
        raise umum.galat_gagal("Staging gagal karena sesuatu.")

    monkeypatch.setitem(handlers.HANDLER, JobType.staging_tarik, inti_gagal)
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.refresh(site)
    sesi.refresh(job)
    assert job.status == JobStatus.failed
    assert site.last_error == "galat lama yang asli"


def test_penanda_diubah_dibaca_dan_dijaga(sesi, site_staging, staging_aktif):
    assert umum.baca_diubah(site_staging.site_id) is None
    log = staging_aktif / str(site_staging.site_id) / "log"
    log.mkdir(parents=True)
    (log / "diubah").write_text("bukan angka", encoding="ascii")
    assert umum.baca_diubah(site_staging.site_id) is None
    (log / "diubah").write_text("1790000000", encoding="ascii")
    umum.perbarui_diubah(site_staging)
    assert site_staging.diubah_pada == datetime.fromtimestamp(1790000000, tz=timezone.utc)


def test_catat_aktivitas_menyebut_pengguna(sesi, site, pengguna_uji):
    job = buat_job(sesi, site.id, JobType.staging_tarik, dibuat_oleh=pengguna_uji.id)
    umum.catat_aktivitas(sesi, site.id, job, "Staging disegarkan", {"ukuran": 5})
    sesi.commit()
    log = sesi.query(ActivityLog).one()
    assert log.pesan == "Staging disegarkan oleh a@b.test"
    assert log.user_id == pengguna_uji.id
    assert log.detail == {"ukuran": 5}


# ---- putusan preflight Task 13 ---------------------------------------------


def _locked_at(sesi, job_id):
    nilai = sesi.scalar(select(Job.locked_at).where(Job.id == job_id))
    sesi.commit()
    return nilai


def _tunggu(kondisi, batas=5.0):
    akhir = time.monotonic() + batas
    while time.monotonic() < akhir:
        if kondisi():
            return True
        time.sleep(0.02)
    return kondisi()


def _utas_detak(job_id):
    return [t for t in threading.enumerate() if t.name == f"wpmgr-detak-job-{job_id}"]


def test_detak_latar_hidup_selama_panggilan_pembantu_lambat_lalu_berhenti(sesi, site):
    """F7: klaim tetap segar selama pemanggilan pembantu yang lama, dan detak berhenti sesudahnya."""
    buat_job(sesi, site.id, JobType.staging_tarik)
    job = ambil_job(sesi, "w1", "staging")
    lama = "UPDATE jobs SET locked_at = now() - interval '20 minutes' WHERE id = :i"
    sesi.execute(text(lama), {"i": job.id})
    sesi.commit()
    basi = datetime.now(timezone.utc) - timedelta(minutes=10)

    class PembantuLambat:
        def db_impor(self):
            # Utas utama diam di sini seperti menunggu `db-impor` berjam-jam.
            time.sleep(0.6)

    with umum.detak_latar(sesi, job, jeda=0.05) as d:
        PembantuLambat().db_impor()
        assert not d.hilang
    assert _locked_at(sesi, job.id) > basi
    assert _tunggu(lambda: not _utas_detak(job.id))

    # Sesudah blok selesai tidak ada lagi yang memperbarui locked_at.
    sesi.execute(text(lama), {"i": job.id})
    sesi.commit()
    time.sleep(0.3)
    assert _locked_at(sesi, job.id) < basi


def test_detak_latar_berhenti_bila_klaim_hilang_dan_blok_membatalkan(sesi, site):
    buat_job(sesi, site.id, JobType.staging_tarik)
    job = ambil_job(sesi, "w1", "staging")
    with pytest.raises(umum.KlaimHilang), umum.detak_latar(sesi, job, jeda=0.05) as d:
        sesi.execute(text("UPDATE jobs SET locked_by = 'w-lain' WHERE id = :i"), {"i": job.id})
        sesi.commit()
        assert _tunggu(lambda: d.hilang)
        assert _tunggu(lambda: not _utas_detak(job.id))
    sesi.expire_all()
    assert sesi.get(Job, job.id).locked_by == "w-lain"


def test_detak_latar_tanpa_klaim_tidak_menyalakan_utas(sesi, site):
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    with umum.detak_latar(sesi, job, jeda=0.01) as d:
        assert not _utas_detak(job.id)
    assert not d.hilang
    assert _locked_at(sesi, job.id) is None


def _gagal_dengan(kelas):
    def handler(sesi, job, klien):
        raise SiteError(kelas, f"Gagal {kelas}")

    return handler


def _kelas_respons(status, kode):
    return klasifikasi_respons(status, {}, json.dumps({"code": kode, "message": "x"}))


# Kebijakan ulang job staging (putusan R15 dan F26). `diulang` = kembali
# `pending` setelah kegagalan pada percobaan pertama.
TABEL_ULANG_STAGING = [
    ("409_ditahan", lambda: _kelas_respons(409, "wpmgr_staging_ditahan"), False),
    ("409_sibuk", lambda: _kelas_respons(409, "wpmgr_staging_sibuk"), True),
    ("409_direbut", lambda: _kelas_respons(409, "wpmgr_staging_direbut"), False),
    ("422_impor", lambda: _kelas_respons(422, "wpmgr_staging_impor"), False),
    ("413_baris_terlalu_besar", lambda: _kelas_respons(413, "wpmgr_staging_baris_terlalu_besar"), False),
    ("422_hash", lambda: _kelas_respons(422, "wpmgr_staging_hash"), True),
    ("403_staging_mati", lambda: _kelas_respons(403, "wpmgr_staging_mati"), False),
    ("403_token", lambda: _kelas_respons(403, "wpmgr_staging_token"), False),
    ("unknown", lambda: UNKNOWN, True),
    ("transient", lambda: TRANSIENT, True),
    ("bad_response", lambda: BAD_RESPONSE, True),
    ("auth_error", lambda: AUTH_ERROR, False),
    ("staging_ditolak", lambda: STAGING_DITOLAK, False),
    ("staging_gagal", lambda: STAGING_GAGAL, False),
    ("terlalu_besar", lambda: TERLALU_BESAR, False),
    ("berkas_hilang", lambda: BERKAS_HILANG, False),
]


@pytest.mark.parametrize("kasus,kelas_fn,diulang", TABEL_ULANG_STAGING, ids=[t[0] for t in TABEL_ULANG_STAGING])
def test_kebijakan_ulang_job_staging_dan_site_tidak_disentuh(sesi, site, monkeypatch, kasus, kelas_fn, diulang):
    """F8 + F26: kelas galat apa pun, job staging tidak mengubah status/last_error site."""
    kelas = kelas_fn()
    site.last_error = "galat lama yang asli"
    sesi.commit()
    monkeypatch.setitem(handlers.HANDLER, JobType.staging_tarik, _gagal_dengan(kelas))
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.expire_all()
    job = sesi.get(Job, job.id)
    assert job.status == (JobStatus.pending if diulang else JobStatus.failed), kelas
    s = sesi.get(Site, site.id)
    assert s.status == SiteStatus.active
    assert s.last_error == "galat lama yang asli"


@pytest.mark.parametrize("kelas", [AUTH_ERROR, BAD_RESPONSE, TRANSIENT, UNKNOWN, BLOCKED, CONNECTOR_MISSING])
def test_kegagalan_akhir_staging_tidak_mengubah_status_site(sesi, site, monkeypatch, kelas):
    """F8: juga pada percobaan terakhir, saat job non-staging akan memasang status site."""
    monkeypatch.setitem(handlers.HANDLER, JobType.staging_dorong, _gagal_dengan(kelas))
    job = buat_job(sesi, site.id, JobType.staging_dorong, max_attempts=1)
    assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.failed
    s = sesi.get(Site, site.id)
    assert s.status == SiteStatus.active
    assert s.last_error is None


def test_sukses_staging_tidak_mengubah_status_site(sesi, site, monkeypatch):
    """Uji update tidak menghubungi produksi, jadi suksesnya bukan bukti site sehat."""
    site.status = SiteStatus.needs_reconnect
    sesi.commit()
    monkeypatch.setitem(handlers.HANDLER, JobType.staging_uji_update, lambda s, j, k: {"ok": True})
    job = buat_job(sesi, site.id, JobType.staging_uji_update)
    assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.success
    assert sesi.get(Site, site.id).status == SiteStatus.needs_reconnect


def test_unknown_masih_diulang_tidak_menandai_staging_gagal(sesi, site_staging):
    """F26: di pembungkus, unknown diperlakukan layak ulang seperti di worker."""
    def inti(sesi, job, site, staging):
        raise SiteError(UNKNOWN, "tulis terputus sesudah dikirim")

    job, jalan = _jalankan(sesi, site_staging, inti)
    job.attempts = 1
    sesi.commit()
    with pytest.raises(SiteError):
        jalan()
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.menyalin
    assert "dilanjutkan" in site_staging.galat


def test_bad_response_pada_percobaan_kedua_menandai_gagal(sesi, site_staging):
    """Pembungkus memakai batas percobaan yang sama dengan worker (bad_response hanya diulang sekali)."""
    def inti(sesi, job, site, staging):
        raise SiteError(BAD_RESPONSE, "Balasan rusak.")

    job, jalan = _jalankan(sesi, site_staging, inti)
    job.attempts = 2
    sesi.commit()
    with pytest.raises(SiteError):
        jalan()
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal


def test_galat_ditolak_langsung_menandai_gagal(sesi, site_staging):
    def inti(sesi, job, site, staging):
        raise SiteError(STAGING_DITOLAK, "Pemulihan dorongan lain masih ditahan.")

    job, jalan = _jalankan(sesi, site_staging, inti)
    job.attempts = 1
    sesi.commit()
    with pytest.raises(SiteError):
        jalan()
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal
    assert site_staging.galat == "Pemulihan dorongan lain masih ditahan."


def test_pembungkus_menangkap_galat_tak_terduga(sesi, site_staging, caplog):
    """F12: KeyError dari handler menandai staging gagal dengan pesan tetap dan dilempar ulang."""
    def inti(sesi, job, site, staging):
        return {}["kursor_/var/lib/rahasia"]

    _, jalan = _jalankan(sesi, site_staging, inti)
    with caplog.at_level(logging.ERROR, logger="wpmgr.staging.umum"), pytest.raises(KeyError):
        jalan()
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal
    assert site_staging.galat == "Galat tak terduga; lihat log server"
    assert site_staging.batal_diminta_pada is None
    assert any(r.exc_info and r.exc_info[0] is KeyError for r in caplog.records)


def test_galat_tak_terduga_lewat_worker_menjadi_internal_error(sesi, site_staging, monkeypatch):
    def handler(sesi, job, klien):
        return umum.jalankan_staging(sesi, job, lambda s, j, si, st: {}["x"], StatusStaging.menyalin, "Tarik")

    monkeypatch.setitem(handlers.HANDLER, JobType.staging_tarik, handler)
    job = buat_job(sesi, site_staging.site_id, JobType.staging_tarik)
    assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.expire_all()
    job = sesi.get(Job, job.id)
    assert job.status == JobStatus.failed
    assert job.error_class == INTERNAL_ERROR
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal


def test_penanda_diubah_dibaca_terbatas(site_staging, staging_aktif):
    """F1: pembacaan `log/diubah` berhenti di BATAS_DIUBAH (64 KiB)."""
    log = staging_aktif / str(site_staging.site_id) / "log"
    log.mkdir(parents=True)
    # Angka sah hanya ada SESUDAH batas: pembacaan yang tidak dibatasi akan menemukannya.
    (log / "diubah").write_bytes(b" " * BATAS_DIUBAH + b"1790000000")
    assert umum.baca_diubah(site_staging.site_id) is None
    # Angka di luar rentang datetime tidak meledak.
    (log / "diubah").write_bytes(b"99999999999999999999")
    assert umum.baca_diubah(site_staging.site_id) is None


def test_penanda_diubah_symlink_ditolak(site_staging, staging_aktif, tmp_path):
    log = staging_aktif / str(site_staging.site_id) / "log"
    log.mkdir(parents=True)
    luar = tmp_path / "luar"
    luar.write_text("1790000000", encoding="ascii")
    try:
        os.symlink(luar, log / "diubah")
    except (OSError, NotImplementedError):
        pytest.skip("symlink tidak dapat dibuat di platform ini")
    assert umum.baca_diubah(site_staging.site_id) is None
