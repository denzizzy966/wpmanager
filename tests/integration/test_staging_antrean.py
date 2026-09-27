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
    Staging,
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


# ---- fix round 1 -------------------------------------------------------------


def test_penanda_diubah_masa_depan_tidak_pernah_menempel(site_staging, staging_aktif):
    log = staging_aktif / str(site_staging.site_id) / "log"
    log.mkdir(parents=True)
    masa_depan = int((datetime.now(timezone.utc) + timedelta(days=365)).timestamp())
    (log / "diubah").write_text(str(masa_depan), encoding="ascii")
    assert umum.baca_diubah(site_staging.site_id) is None
    umum.perbarui_diubah(site_staging)
    assert site_staging.diubah_pada is None

    # Nilai masa depan yang sudah tersimpan (mis. dari versi lama) digantikan
    # penanda sah berikutnya, bukan menahan semua pembaruan selamanya.
    site_staging.diubah_pada = datetime(9999, 1, 1, tzinfo=timezone.utc)
    (log / "diubah").write_text("1790000000", encoding="ascii")
    umum.perbarui_diubah(site_staging)
    assert site_staging.diubah_pada == datetime.fromtimestamp(1790000000, tz=timezone.utc)


def _jalankan_lewat_worker(sesi, monkeypatch, tipe, inti, status_kerja=StatusStaging.mendorong, nama="Dorong"):
    def handler(sesi, job, klien):
        return umum.jalankan_staging(sesi, job, inti, status_kerja, nama)

    monkeypatch.setitem(handlers.HANDLER, tipe, handler)
    return proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")


@pytest.mark.parametrize("kelas", [TRANSIENT, UNKNOWN])
def test_batal_lalu_potongan_gagal_sementara_tetap_batal(sesi, site_staging, monkeypatch, kelas):
    """Batal yang diminta sebelum potongan gagal sementara tidak hilang; dorong tidak berlanjut."""
    site_staging.ditarik_pada = datetime.now(timezone.utc)
    sesi.commit()
    panggilan = []

    def inti(sesi, job, site, staging):
        panggilan.append(1)
        staging.batal_diminta_pada = datetime.now(timezone.utc)
        sesi.commit()
        raise SiteError(kelas, "koneksi putus")

    job = buat_job(sesi, site_staging.site_id, JobType.staging_dorong)
    assert _jalankan_lewat_worker(sesi, monkeypatch, JobType.staging_dorong, inti)
    sesi.expire_all()
    job = sesi.get(Job, job.id)
    assert job.status == JobStatus.failed
    st = sesi.get(Staging, site_staging.id)
    assert st.status == StatusStaging.siap
    assert st.galat == "Dibatalkan oleh pengguna."
    assert st.batal_diminta_pada is None
    # Tidak ada putaran berikutnya yang menjalankan dorong lagi.
    assert not proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    assert len(panggilan) == 1


def test_batal_yang_tiba_saat_percobaan_ulang_tidak_dihapus(sesi, site_staging, monkeypatch):
    """Batal yang masuk sesudah pemeriksaan tetapi sebelum status ulang ditulis tetap berlaku."""
    panggilan = []

    def inti(sesi, job, site, staging):
        panggilan.append(1)
        raise SiteError(TRANSIENT, "koneksi putus")

    asli = umum._batal_diminta

    def batal_tiba_sesudah_diperiksa(sesi, staging_id):
        hasil = asli(sesi, staging_id)
        if panggilan:
            sesi.execute(text("UPDATE staging SET batal_diminta_pada = now() WHERE id = :i"), {"i": staging_id})
            sesi.commit()
        return hasil

    monkeypatch.setattr(umum, "_batal_diminta", batal_tiba_sesudah_diperiksa)
    job = buat_job(sesi, site_staging.site_id, JobType.staging_dorong)
    assert _jalankan_lewat_worker(sesi, monkeypatch, JobType.staging_dorong, inti)
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.pending
    assert sesi.get(Staging, site_staging.id).batal_diminta_pada is not None

    # Putaran berikutnya berhenti sebagai batal sebelum inti dipanggil.
    monkeypatch.setattr(umum, "_batal_diminta", asli)
    sesi.execute(text("UPDATE jobs SET scheduled_for = now() WHERE id = :i"), {"i": job.id})
    sesi.commit()
    assert _jalankan_lewat_worker(sesi, monkeypatch, JobType.staging_dorong, inti)
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.failed
    st = sesi.get(Staging, site_staging.id)
    assert st.galat == "Dibatalkan oleh pengguna."
    assert st.batal_diminta_pada is None
    assert len(panggilan) == 1


def test_keputusan_ulang_memakai_keadaan_yang_sudah_di_rollback(sesi, site_staging):
    def inti(sesi, job, site, staging):
        job.attempts = 99  # perubahan yang belum di-commit tidak boleh memengaruhi keputusan
        raise SiteError(TRANSIENT, "koneksi putus")

    job, jalan = _jalankan(sesi, site_staging, inti)
    job.attempts = 1
    sesi.commit()
    with pytest.raises(SiteError):
        jalan()
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.menyalin
    assert sesi.get(Job, job.id).attempts == 1


def test_batal_pengguna_dicatat_netral(sesi, site_staging, monkeypatch):
    def inti(sesi, job, site, staging):
        staging.batal_diminta_pada = datetime.now(timezone.utc)
        sesi.commit()
        umum.titik_potongan(sesi, job, staging)

    buat_job(sesi, site_staging.site_id, JobType.staging_dorong)
    _jalankan_lewat_worker(sesi, monkeypatch, JobType.staging_dorong, inti)
    log = sesi.query(ActivityLog).all()
    assert [(x.level, x.pesan) for x in log] == [("warning", "Dorong dibatalkan")]


def test_worker_dihentikan_dicatat_netral(sesi, site_staging, monkeypatch):
    monkeypatch.setattr(umum, "harus_berhenti", lambda: True)

    def inti(sesi, job, site, staging):
        umum.titik_potongan(sesi, job, staging)

    job = buat_job(sesi, site_staging.site_id, JobType.staging_tarik)
    _jalankan_lewat_worker(sesi, monkeypatch, JobType.staging_tarik, inti, StatusStaging.menyalin, "Tarik")
    sesi.expire_all()
    job = sesi.get(Job, job.id)
    assert job.status == JobStatus.pending
    assert job.attempts == 0
    log = sesi.query(ActivityLog).one()
    assert log.level == "info"
    assert "gagal" not in log.pesan
    assert log.pesan == "staging_tarik dihentikan bersama worker; dilanjutkan otomatis"


def test_reaper_menandai_staging_gagal_saat_jatah_habis(sesi, site_staging):
    from wpmgr.jobs.reaper import pulihkan_job_yatim

    site_staging.status = StatusStaging.mendorong
    site_staging.batal_diminta_pada = datetime.now(timezone.utc)
    sesi.commit()
    buat_job(sesi, site_staging.site_id, JobType.staging_dorong)
    job = ambil_job(sesi, "w1", "staging")
    job.attempts = job.max_attempts
    job.locked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    sesi.commit()
    assert pulihkan_job_yatim(sesi) == 1
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.unknown
    st = sesi.get(Staging, site_staging.id)
    assert st.status == StatusStaging.gagal
    assert st.galat == "Proses terhenti tak terduga; coba lagi."
    assert st.batal_diminta_pada is None


@pytest.mark.parametrize("tipe,status_kerja,kemajuan,status,asal", [
    # Tarik/uji yang ditinggalkan: salinan staging bisa setengah jadi.
    (JobType.staging_tarik, StatusStaging.menyalin, {}, StatusStaging.gagal, "salinan"),
    (JobType.staging_uji_update, StatusStaging.berjalan_uji, {}, StatusStaging.gagal, "salinan"),
    # Dorong/kembalikan yang sudah menukar: gagal milik produksi.
    (JobType.staging_dorong, StatusStaging.mendorong, {"status_staging_awal": "siap", "langkah_terapkan": "tukar"},
     StatusStaging.gagal, "produksi"),
    (JobType.staging_kembalikan, StatusStaging.mendorong,
     {"status_staging_awal": "siap", "langkah_terapkan": "selesai"}, StatusStaging.gagal, "produksi"),
    # Belum menukar (atau pemulihan terkonfirmasi): status sebelum job dikembalikan.
    (JobType.staging_dorong, StatusStaging.mendorong, {"status_staging_awal": "siap", "langkah_terapkan": "siapkan"},
     StatusStaging.siap, None),
    (JobType.staging_dorong, StatusStaging.mendorong,
     {"status_staging_awal": "siap", "langkah_terapkan": "dipulihkan", "pulih_terkonfirmasi": True},
     StatusStaging.siap, None),
    (JobType.staging_kembalikan, StatusStaging.mendorong,
     {"status_staging_awal": "gagal", "gagal_asal_awal": "salinan"}, StatusStaging.gagal, "salinan"),
    (JobType.staging_kembalikan, StatusStaging.mendorong, {"status_staging_awal": "dijeda"},
     StatusStaging.dijeda, None),
])
def test_reaper_asal_gagal_per_tipe_job(sesi, site_staging, tipe, status_kerja, kemajuan, status, asal):
    """Putusan R20: reaper memakai aturan asal yang sama dengan pembungkus."""
    from wpmgr.jobs.reaper import pulihkan_job_yatim

    site_staging.status = status_kerja
    site_staging.ditarik_pada = datetime.now(timezone.utc)
    sesi.commit()
    buat_job(sesi, site_staging.site_id, tipe, {"kemajuan": kemajuan})
    job = ambil_job(sesi, "w1", "staging")
    job.attempts = job.max_attempts
    job.locked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    sesi.commit()
    assert pulihkan_job_yatim(sesi) == 1
    sesi.expire_all()
    st = sesi.get(Staging, site_staging.id)
    assert (st.status, st.gagal_asal) == (status, asal)
    assert st.galat == "Proses terhenti tak terduga; coba lagi."


def test_reaper_yang_menjadwalkan_ulang_tidak_mengubah_staging(sesi, site_staging):
    from wpmgr.jobs.reaper import pulihkan_job_yatim

    buat_job(sesi, site_staging.site_id, JobType.staging_tarik)
    job = ambil_job(sesi, "w1", "staging")
    job.locked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    sesi.commit()
    assert pulihkan_job_yatim(sesi) == 1
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.pending
    assert sesi.get(Staging, site_staging.id).status == StatusStaging.menyalin


def test_reaper_job_non_staging_tidak_menyentuh_staging(sesi, site_staging):
    from wpmgr.jobs.reaper import pulihkan_job_yatim

    buat_job(sesi, site_staging.site_id, JobType.scan_site)
    job = ambil_job(sesi, "w1", "umum")
    job.attempts = job.max_attempts
    job.locked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    sesi.commit()
    pulihkan_job_yatim(sesi)
    sesi.expire_all()
    assert sesi.get(Staging, site_staging.id).status == StatusStaging.menyalin


def test_galat_tak_terduga_tidak_bocor_ke_job_error(sesi, site_staging, monkeypatch):
    def inti(sesi, job, site, staging):
        raise KeyError("/var/lib/wpmgr/staging/rahasia")

    job = buat_job(sesi, site_staging.site_id, JobType.staging_tarik)
    _jalankan_lewat_worker(sesi, monkeypatch, JobType.staging_tarik, inti, StatusStaging.menyalin, "Tarik")
    sesi.expire_all()
    job = sesi.get(Job, job.id)
    assert job.error == "Galat tak terduga; lihat log server"
    assert "KeyError" not in job.error and "/var/lib" not in job.error
    for baris in sesi.query(ActivityLog).all():
        teks = f"{baris.pesan} {baris.detail}"
        assert "KeyError" not in teks and "/var/lib" not in teks


def _gagal_dengan_pesan(pesan):
    def handler(sesi, job, klien):
        raise SiteError(STAGING_GAGAL, pesan)

    return handler


def test_detail_aktivitas_dibersihkan(sesi, site, monkeypatch):
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    umum.catat_aktivitas(sesi, site.id, job, "Tarik\x00 selesai", {"daftar": ["a\x00b", {"k\x00": "\ud800x"}]})
    sesi.commit()
    log = sesi.query(ActivityLog).one()
    assert log.pesan == "Tarik selesai"
    assert log.detail == {"daftar": ["ab", {"k": "?x"}]}

    sesi.query(ActivityLog).delete()
    sesi.commit()
    monkeypatch.setitem(handlers.HANDLER, JobType.staging_tarik, _gagal_dengan_pesan("rusak\x00 dari connector"))
    assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    log = sesi.query(ActivityLog).one()
    assert log.detail["pesan"] == "rusak dari connector"


def _locked_at_lain(sesi, job_id):
    """Baca locked_at lewat koneksi lain, supaya tidak membuka transaksi di sesi utama."""
    with sesi.get_bind().connect() as c:
        return c.execute(text("SELECT locked_at FROM jobs WHERE id = :i"), {"i": job_id}).scalar()


def test_detak_latar_meng_commit_sesi_utama_saat_masuk(sesi, site):
    """Transaksi utas utama yang memegang baris job tidak boleh menahan detak latar."""
    buat_job(sesi, site.id, JobType.staging_tarik)
    job = ambil_job(sesi, "w1", "staging")
    sesi.execute(text("UPDATE jobs SET locked_at = now() - interval '20 minutes' WHERE id = :i"), {"i": job.id})
    sesi.commit()
    basi = datetime.now(timezone.utc) - timedelta(minutes=10)
    job.payload = {"kemajuan": {"tahap": "db"}}
    sesi.flush()  # baris job kini terkunci oleh transaksi utas utama
    with umum.detak_latar(sesi, job, jeda=0.05):
        assert _tunggu(lambda: _locked_at_lain(sesi, job.id) > basi, batas=2.0)


@pytest.mark.parametrize("berjalan,diklaim,jenis,boleh", [
    (JobType.scan_site, JobType.staging_uji_update, "staging", True),
    (JobType.staging_uji_update, JobType.scan_site, "umum", True),
    (JobType.scan_site, JobType.staging_kembalikan, "staging", False),
    (JobType.staging_kembalikan, JobType.scan_site, "umum", False),
    (JobType.update_package, JobType.staging_kembalikan, "staging", False),
    (JobType.staging_dorong, JobType.update_package, "umum", False),
])
def test_matriks_klaim(sesi, site, berjalan, diklaim, jenis, boleh):
    _berjalan(sesi, site, berjalan)
    kandidat = buat_job(sesi, site.id, diklaim)
    hasil = ambil_job(sesi, "w1", jenis)
    assert (hasil is not None and hasil.id == kandidat.id) is boleh


def test_klaim_bentrok_diperiksa_ulang_sesudah_snapshot_basi(sesi, site, monkeypatch):
    """M1: klaim yang lolos NOT EXISTS berdasarkan snapshot basi dibatalkan oleh pemeriksaan ulang."""
    from wpmgr.jobs import queue

    _berjalan(sesi, site, JobType.staging_dorong)
    scan = buat_job(sesi, site.id, JobType.scan_site)
    # Tiru snapshot basi: pernyataan klaim tanpa penyaring NOT EXISTS.
    teks = queue.SQL_AMBIL.text
    basi = teks[:teks.index("AND NOT EXISTS")] + teks[teks.index("ORDER BY j.scheduled_for"):]
    monkeypatch.setattr(queue, "SQL_AMBIL", text(basi).bindparams(queue.SQL_AMBIL._bindparams["jenis"]))
    assert ambil_job(sesi, "w2", "umum") is None
    sesi.expire_all()
    scan = sesi.get(Job, scan.id)
    assert scan.status == JobStatus.pending
    assert scan.attempts == 0
    assert scan.locked_by is None


def test_klaim_diserialkan_per_site_lintas_sesi(engine, sesi, site):
    """M1, dua sesi: B memeriksa ulang baru setelah klaim A di-commit, dan melihatnya."""
    from sqlalchemy.orm import sessionmaker

    from wpmgr.jobs import queue

    dorong = buat_job(sesi, site.id, JobType.staging_dorong)
    scan = buat_job(sesi, site.id, JobType.scan_site)
    buat = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    a, b = buat(), buat()
    hasil = {}
    try:
        # A: klaim dorong, lolos pemeriksaan, belum commit (lock site dipegang).
        a.execute(text("UPDATE jobs SET status = 'running', locked_by = 'A' WHERE id = :i"), {"i": dorong.id})
        assert queue._masih_eksklusif(a, dorong.id, site.id)

        # B: klaim scan dengan snapshot yang belum melihat A, lalu memeriksa ulang.
        def klaim_b():
            b.execute(text("UPDATE jobs SET status = 'running', locked_by = 'B' WHERE id = :i"), {"i": scan.id})
            hasil["b"] = queue._masih_eksklusif(b, scan.id, site.id)

        utas = threading.Thread(target=klaim_b)
        utas.start()
        time.sleep(0.5)
        assert utas.is_alive(), "B harus menunggu lock site yang dipegang A"
        a.commit()
        utas.join(5)
        assert hasil["b"] is False
    finally:
        b.rollback()
        a.rollback()
        a.close()
        b.close()


# ---- Task 16: batal tidak berlaku lagi sesudah dorong menyentuh produksi ------


@pytest.mark.parametrize("kelas", [TRANSIENT, UNKNOWN])
def test_batal_diabaikan_bila_pembungkus_melarang(sesi, site_staging, monkeypatch, kelas):
    """Sesudah tukar dikirim, batal tidak boleh mengakhiri job: job diulang dan menuntaskan dorongnya."""
    site_staging.ditarik_pada = datetime.now(timezone.utc)
    site_staging.batal_diminta_pada = datetime.now(timezone.utc)
    sesi.commit()
    panggilan = []

    def inti(sesi, job, site, staging):
        panggilan.append(1)
        raise SiteError(kelas, "koneksi putus")

    job = buat_job(sesi, site_staging.site_id, JobType.staging_dorong)
    with pytest.raises(SiteError) as e:
        umum.jalankan_staging(sesi, job, inti, StatusStaging.mendorong, "Dorong", boleh_batal=lambda j: False)
    # Batal yang sudah diminta sebelum job mulai pun tidak menghentikan inti.
    assert panggilan == [1]
    assert not isinstance(e.value, umum.GalatDibatalkan)
    st = sesi.get(Staging, site_staging.id, populate_existing=True)
    assert st.status == StatusStaging.mendorong
    # Permintaan batal tetap tercatat, bukan dihapus diam-diam.
    assert st.batal_diminta_pada is not None


def test_batal_tetap_berlaku_bila_pembungkus_mengizinkan(sesi, site_staging):
    site_staging.ditarik_pada = datetime.now(timezone.utc)
    site_staging.batal_diminta_pada = datetime.now(timezone.utc)
    sesi.commit()
    job = buat_job(sesi, site_staging.site_id, JobType.staging_dorong)
    with pytest.raises(umum.GalatDibatalkan):
        umum.jalankan_staging(sesi, job, lambda *a: {}, StatusStaging.mendorong, "Dorong",
                              boleh_batal=lambda j: True)
