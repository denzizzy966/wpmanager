import hashlib
import json
import os
import uuid

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from staging_palsu import PembantuPalsu, ProduksiPalsu

from wpmgr.errors import STAGING_DITOLAK, STAGING_GAGAL, TRANSIENT, SiteError
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
from wpmgr.staging import dorong, tarik, umum

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _jendela_pemulihan_nol(monkeypatch):
    """Test di berkas ini memaksa kegagalan FINAL lewat `attempts = max_attempts`.

    Sejak R26 job yang sudah menukar produksi terus dicoba sampai 24 jam sejak
    tukar; jendela dinolkan di sini supaya perilaku final tetap teruji.
    Perilaku R26 sendiri diuji di test_staging_antrean.py dan
    `test_r26_*` (yang memulihkan batas 24 jam).
    """
    from datetime import timedelta

    from wpmgr.jobs import queue

    monkeypatch.setattr(queue, "BATAS_PEMULIHAN", timedelta(0))

MTIME = 1_700_000_000
SQL_EKSPOR = b"DROP TABLE IF EXISTS `wp_posts`;\nCREATE TABLE `wp_posts` (`id` int);\n"


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))


@pytest.fixture
def prod(monkeypatch):
    p = ProduksiPalsu()
    p.berkas = {
        "index.php": (b"<?php // inti", MTIME),
        "wp-content/themes/t/style.css": (b"body{}", MTIME),
    }
    p.tabel = {"wp_options": [b"CREATE TABLE `wp_options` (`option_name` varchar(191));\n"],
               "wp_posts": [b"DROP TABLE IF EXISTS `wp_posts`;\nCREATE TABLE `wp_posts` (`id` int);\n",
                            b"INSERT INTO `wp_posts` (`id`) VALUES ('7');\n"]}
    monkeypatch.setattr(umum, "buat_http", p.http)
    return p


@pytest.fixture
def pb(staging_aktif, monkeypatch, site_staging):
    palsu = PembantuPalsu(staging_aktif)
    ekspor = staging_aktif / str(site_staging.site_id) / "ekspor" / "dorong.sql"
    palsu.saat_wpcli = lambda nama, argumen: ekspor.write_bytes(SQL_EKSPOR) if "--export" in argumen else None
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


def _selesaikan(sesi):
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.success
    sesi.commit()


def _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, mode):
    site = sesi.get(Site, site_staging.site_id)
    tarik.tangani_staging_tarik(sesi, buat_job(sesi, site.id, JobType.staging_tarik), prod.klien(site))
    _selesaikan(sesi)
    files = staging_aktif / str(site.id) / "files"
    (files / "wp-content/themes/t/style.css").write_bytes(b"body{color:red}")
    (files / "wp-content/themes/t/baru.php").write_bytes(b"<?php //baru")
    job = buat_job(sesi, site.id, JobType.staging_dorong, {"mode": mode, "konfirmasi_nama": "Contoh"})
    dorong.tangani_staging_dorong(sesi, job, prod.klien(site))
    _selesaikan(sesi)
    return site, sesi.query(StagingSnapshot).one()


def _kembalikan(sesi, site, prod, snap_id, nama="Contoh"):
    job = buat_job(sesi, site.id, JobType.staging_kembalikan, {"snapshot_id": snap_id, "konfirmasi_nama": nama})
    hasil = dorong.tangani_staging_kembalikan(sesi, job, prod.klien(site))
    _selesaikan(sesi)
    return hasil


def test_kembalikan_hanya_kode_memulihkan_berkas_tanpa_database(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"
    prod.langkah.clear()
    prod.unggahan.clear()
    hasil = _kembalikan(sesi, site, prod, snap.id)
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    assert "wp-content/themes/t/baru.php" not in prod.berkas
    assert "impor" not in prod.langkah
    assert prod.sql_diterapkan == b""
    assert hasil["db"] is False
    sesi.refresh(snap)
    assert snap.status == "dipakai"
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan == f"Produksi dikembalikan dari snapshot #{snap.id}").count() == 1


def test_kembalikan_timpa_penuh_memulihkan_database(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "timpa_penuh")
    assert prod.sql_diterapkan == SQL_EKSPOR
    prod.langkah.clear()
    prod.unggahan.clear()
    _kembalikan(sesi, site, prod, snap.id)
    assert prod.langkah == ["siapkan", "impor", "tukar", "selesai"]
    assert b"VALUES ('7')" in prod.sql_diterapkan
    assert prod.sql_diterapkan.startswith(b"SET NAMES utf8mb4;")


def test_kembalikan_tidak_memeriksa_tanda_air(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    sebelum = prod.hitung.get("/staging/tanda-air")
    prod.tanda_air["sumber"]["comments"] = {"maks_id": 99, "jumlah": 99}
    _kembalikan(sesi, site, prod, snap.id)
    assert prod.hitung.get("/staging/tanda-air") == sebelum


@pytest.mark.parametrize("nama", ["", "contoh", "Contoh ", None])
def test_kembalikan_butuh_nama_site_persis(sesi, site_staging, staging_aktif, prod, pb, nama):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    with pytest.raises(SiteError) as e:
        _kembalikan(sesi, site, prod, snap.id, nama=nama)
    assert e.value.error_class == STAGING_DITOLAK


def test_snapshot_dipangkas_atau_milik_site_lain_ditolak(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    snap.status = "dipangkas"
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _kembalikan(sesi, site, prod, snap.id)
    assert "dipangkas" in e.value.pesan
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.failed
    sesi.commit()
    with pytest.raises(SiteError):
        _kembalikan(sesi, site, prod, 999999)


def test_meta_snapshot_rusak_ditolak(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    (staging_aktif / snap.path / "meta.json").write_text('{"mode": "eval", "baru": ["../../etc/x"]}', encoding="utf-8")
    with pytest.raises(SiteError) as e:
        _kembalikan(sesi, site, prod, snap.id)
    assert "Snapshot rusak" in e.value.pesan


def test_kembalikan_setelah_staging_dihapus(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    sesi.delete(sesi.get(Staging, site_staging.id))
    sesi.commit()
    _kembalikan(sesi, site, prod, snap.id)
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"


def test_dorong_gagal_dibersihkan_setelah_kembalikan(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    st = sesi.get(Staging, site_staging.id)
    st.dorong_gagal_pada = umum.sekarang()
    sesi.commit()
    _kembalikan(sesi, site, prod, snap.id)
    sesi.refresh(st)
    assert st.dorong_gagal_pada is None


def test_kembalikan_tidak_bisa_diantrekan_saat_dorong_tertunda(sesi, site_staging):
    buat_job(sesi, site_staging.site_id, JobType.staging_dorong, {"mode": "hanya_kode"})
    with pytest.raises(IntegrityError):
        buat_job(sesi, site_staging.site_id, JobType.staging_kembalikan, {"snapshot_id": 1})
    sesi.rollback()


# ---- tambahan: jalur berbahaya ---------------------------------------------------


def _staging(sesi, site_staging) -> Staging:
    return sesi.get(Staging, site_staging.id, populate_existing=True)


def _kemajuan(sesi, job) -> dict:
    return umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))


def _job_balik(sesi, site, snap_id, nama="Contoh", **lain):
    return buat_job(sesi, site.id, JobType.staging_kembalikan, {"snapshot_id": snap_id, "konfirmasi_nama": nama},
                    **lain)


def _jalankan(sesi, site, prod, job, klien=None):
    return dorong.tangani_staging_kembalikan(sesi, job, klien or prod.klien(site))


def _galat(kode_http: int, kode: str, pesan: str = "x", **data) -> httpx.Response:
    return httpx.Response(kode_http, json={"code": kode, "message": pesan, "data": {"status": kode_http, **data}})


def _siap_balik(sesi, site_staging, staging_aktif, prod, mode="hanya_kode"):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, mode)
    prod.langkah.clear()
    prod.langkah_id.clear()
    prod.unggahan.clear()
    return site, snap


def _ids_kembalikan(prod, dorong_ids_lama) -> set:
    return {i for i, _ in prod.langkah_id} - set(dorong_ids_lama)


TITIK_PUTUS = [
    ("lama_dituntaskan", True), ("tahap_balik", "unggah"), ("unggah_nomor", 1), ("tahap_balik", "terapkan"),
    ("langkah_terapkan", "impor"), ("langkah_terapkan", "cek_ulang"), ("langkah_terapkan", "tukar"),
    ("langkah_terapkan", "selesai"), ("langkah_terapkan", "beres"), ("tahap_balik", "cek"),
]


@pytest.mark.parametrize("kunci,nilai", TITIK_PUTUS)
def test_kembalikan_dilanjutkan_dari_setiap_tahap(sesi, site_staging, staging_aktif, prod, pb, monkeypatch,
                                                  kunci, nilai):
    """Worker mati tepat sesudah kemajuan tahap itu di-commit; job berikutnya menuntaskan kembalikan yang sama."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod, "timpa_penuh")
    asli = umum.simpan_kemajuan
    putus = []

    def simpan(sesi_, job_, **perubahan):
        k = asli(sesi_, job_, **perubahan)
        if not putus and perubahan.get(kunci) == nilai:
            putus.append(1)
            raise SiteError(TRANSIENT, "worker mati")
        return k

    monkeypatch.setattr(umum, "simpan_kemajuan", simpan)
    job = _job_balik(sesi, site, snap.id)
    with pytest.raises(SiteError):
        _jalankan(sesi, site, prod, job)
    assert putus == [1]
    assert _staging(sesi, site_staging).status == StatusStaging.mendorong
    hasil = _jalankan(sesi, site, prod, job)
    _selesaikan(sesi)

    assert hasil["dorong_gagal"] is False and hasil["db"] is True
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    assert "wp-content/themes/t/baru.php" not in prod.berkas
    assert b"VALUES ('7')" in prod.sql_diterapkan
    assert len({i for i, _ in prod.langkah_id}) == 1, "hanya satu dorongan kembalikan yang dipakai"
    assert prod.langkah.count("tukar") == 1 and "pulihkan" not in prod.langkah
    assert prod.langkah[-1] == "selesai"
    assert prod.dorongan == {} and prod.kunci is None
    assert sesi.query(StagingSnapshot).one().status == "dipakai"
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Produksi dikembalikan%")).count() == 1
    assert _staging(sesi, site_staging).status == StatusStaging.siap


def test_tukar_unknown_kembalikan_direkonsiliasi(sesi, site_staging, staging_aktif, prod, pb):
    """Respons tukar hilang padahal tukar sukses: dikirim ulang, dibalas hasil tersimpan, TANPA pulihkan."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["tukar"] = ["putus"]
    hasil = _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert hasil["dorong_gagal"] is False
    assert prod.langkah == ["siapkan", "tukar", "tukar", "selesai"]
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"


def test_tukar_unknown_final_kembalikan_dicatat_lalu_dituntaskan_berikutnya(sesi, site_staging, staging_aktif,
                                                                            prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["tukar"] = ["putus"] * (dorong.MAKS_RAGU_TUKAR + 1)
    job = _job_balik(sesi, site, snap.id)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert e.value.error_class == STAGING_GAGAL and e.value.pesan == dorong.PESAN_AKHIR_BALIK["tukar"]
    st = _staging(sesi, site_staging)
    assert st.dorong_gagal_pada is not None and st.status == StatusStaging.gagal
    assert st.galat == dorong.PESAN_AKHIR_BALIK["tukar"]
    # Snapshot tetap ada dan belum "dipakai": kembalikan belum terbukti tuntas.
    assert sesi.query(StagingSnapshot).one().status == "tersedia"
    dorong_id = _kemajuan(sesi, job)["dorong_id"]
    assert prod.dorongan[dorong_id]["status"] == "ditukar"
    assert "pulihkan" not in prod.langkah
    assert not sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Produksi dikembalikan%")).count()

    # Kembalikan berikutnya menuntaskan dorongan yang tertinggal lebih dulu.
    job.status = JobStatus.failed
    sesi.commit()
    _kembalikan(sesi, site, prod, snap.id)
    assert (dorong_id, "selesai") in prod.langkah_id
    assert (dorong_id, "pulihkan") not in prod.langkah_id
    assert dorong_id not in prod.dorongan
    assert _kemajuan(sesi, job)["produksi_bersih"] is True
    st = _staging(sesi, site_staging)
    assert st.dorong_gagal_pada is None and st.status == StatusStaging.siap
    assert sesi.query(StagingSnapshot).one().status == "dipakai"


def test_tukar_kembalikan_gagal_dipulihkan(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    prod.gagal_langkah = "tukar"
    job = _job_balik(sesi, site, snap.id)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert e.value.error_class == STAGING_GAGAL and "dipulihkan" in e.value.pesan
    assert prod.langkah == ["siapkan", "tukar", "pulihkan"]
    # Produksi kembali ke keadaan sebelum kembalikan (versi dorongan).
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"
    assert prod.dorongan == {}
    assert _staging(sesi, site_staging).dorong_gagal_pada is None
    assert sesi.query(StagingSnapshot).one().status == "tersedia"


def test_pulihkan_kembalikan_diulang_selama_memulihkan(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    prod.gagal_langkah = "tukar"
    prod.pulih_otomatis_macet = True
    pulih_gagal = _galat(500, "wpmgr_staging_pulihkan", "Sebagian berkas belum dapat dikembalikan.")
    prod.kejadian["pulihkan"] = [pulih_gagal] * (dorong.MAKS_ULANG_PULIHKAN + 1)
    job = _job_balik(sesi, site, snap.id)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert e.value.error_class == TRANSIENT
    assert _kemajuan(sesi, job)["langkah_terapkan"] == "pulihkan"
    sebelum = len(prod.langkah)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert e.value.error_class == STAGING_GAGAL and "dipulihkan" in e.value.pesan
    assert set(prod.langkah[sebelum:]) == {"pulihkan"}
    assert prod.langkah.count("tukar") == 1


def _minta_batal(sesi, site_staging):
    st = sesi.get(Staging, site_staging.id)
    st.batal_diminta_pada = umum.sekarang()
    sesi.commit()


def test_batal_kembalikan_sebelum_tukar(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    prod.sebelum["/staging/terapkan"] = lambda p, n, badan: _minta_batal(sesi, site_staging)
    with pytest.raises(umum.GalatDibatalkan):
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert prod.langkah == ["siapkan"]
    assert prod.dorongan == {} and prod.kunci is None
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.batal_diminta_pada is None
    # Snapshot milik dorongan tidak ikut dibuang oleh kembalikan yang dibatalkan.
    assert sesi.query(StagingSnapshot).one().status == "tersedia"
    assert (staging_aktif / snap.path / "meta.json").exists()


def test_batal_kembalikan_sesudah_tukar_diabaikan(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)

    def batal_saat_tukar(p, n, badan):
        if badan["langkah"] == "tukar":
            _minta_batal(sesi, site_staging)

    prod.sebelum["/staging/terapkan"] = batal_saat_tukar
    prod.kejadian["selesai"] = [httpx.Response(500, text="galat sementara")] * umum.ULANG_POTONGAN
    job = _job_balik(sesi, site, snap.id)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert not isinstance(e.value, umum.GalatDibatalkan)
    assert _staging(sesi, site_staging).status == StatusStaging.mendorong
    hasil = _jalankan(sesi, site, prod, job)
    assert hasil["dorong_gagal"] is False
    assert prod.langkah == ["siapkan", "tukar"] + ["selesai"] * (umum.ULANG_POTONGAN + 1)
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.batal_diminta_pada is None


def test_gagal_final_kembalikan_membersihkan_produksi(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    """F9a: area dorong dibersihkan dengan tenggat pendek; snapshot dorongan TIDAK dibuang."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    prod.gagal_langkah = "siapkan"
    klien = prod.klien(site)
    tenggat_dipakai = []
    asli = klien.staging_bersihkan

    def catat(dorong_id, tenggat=None):
        tenggat_dipakai.append(tenggat)
        return asli(dorong_id, tenggat=tenggat)

    monkeypatch.setattr(klien, "staging_bersihkan", catat)
    job = _job_balik(sesi, site, snap.id)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job, klien=klien)
    assert e.value.error_class == STAGING_GAGAL
    assert tenggat_dipakai and 0 < tenggat_dipakai[-1] <= dorong.TENGGAT_BERSIHKAN_AKHIR
    assert prod.dorongan == {} and prod.kunci is None
    assert "tukar" not in prod.langkah
    assert _kemajuan(sesi, job)["produksi_bersih"] is True
    assert sesi.query(StagingSnapshot).one().status == "tersedia"
    assert (staging_aktif / snap.path / "meta.json").exists()
    assert _staging(sesi, site_staging).dorong_gagal_pada is None


def test_gagal_final_kembalikan_di_selesai(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["selesai"] = [httpx.Response(500, text="galat")] * 10
    job = _job_balik(sesi, site, snap.id)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert e.value.pesan == dorong.PESAN_AKHIR_BALIK["selesai"]
    st = _staging(sesi, site_staging)
    assert st.dorong_gagal_pada is not None and st.galat == dorong.PESAN_AKHIR_BALIK["selesai"]
    assert sesi.query(StagingSnapshot).one().status == "tersedia"


# ---- tanpa baris Staging ----------------------------------------------------------


def _hapus_staging(sesi, site_staging):
    sesi.delete(sesi.get(Staging, site_staging.id))
    sesi.commit()


def test_tanpa_staging_percobaan_ulang_melanjutkan(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _hapus_staging(sesi, site_staging)
    prod.kejadian["tukar"] = ["putus"] * (dorong.MAKS_RAGU_TUKAR + 1)
    job = _job_balik(sesi, site, snap.id)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert e.value.error_class == TRANSIENT
    dorong_id = _kemajuan(sesi, job)["dorong_id"]
    # Percobaan ulang masih akan datang: area dorong di produksi tidak dibersihkan.
    assert dorong_id in prod.dorongan
    hasil = _jalankan(sesi, site, prod, job)
    assert hasil["dorong_gagal"] is False
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    assert "pulihkan" not in prod.langkah and prod.langkah[-1] == "selesai"


def test_tanpa_staging_gagal_final_membersihkan(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _hapus_staging(sesi, site_staging)
    prod.gagal_langkah = "siapkan"
    job = _job_balik(sesi, site, snap.id)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert e.value.error_class == STAGING_GAGAL
    assert prod.dorongan == {} and prod.kunci is None
    assert _kemajuan(sesi, job)["produksi_bersih"] is True


def test_tanpa_staging_gagal_final_sesudah_tukar_berpesan_tetap(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _hapus_staging(sesi, site_staging)
    prod.kejadian["selesai"] = [httpx.Response(500, text="galat")] * 10
    job = _job_balik(sesi, site, snap.id)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert e.value.pesan == dorong.PESAN_AKHIR_BALIK["selesai"]
    assert prod.dorongan[_kemajuan(sesi, job)["dorong_id"]]["status"] == "ditukar"


# ---- validasi snapshot --------------------------------------------------------------


def _meta(staging_aktif, snap) -> dict:
    return json.loads((staging_aktif / snap.path / "meta.json").read_text(encoding="utf-8"))


def _tulis_meta(staging_aktif, snap, meta) -> None:
    (staging_aktif / snap.path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


@pytest.mark.parametrize("ubah", [
    lambda m: {**m, "mode": "eval"},
    lambda m: {**m, "baru": "wp-content/themes/t/baru.php"},
    lambda m: {**m, "baru": ["../../etc/x"]},
    lambda m: {**m, "baru": ["/etc/passwd"]},
    lambda m: {**m, "baru": ["wp-config.php"]},
    lambda m: {**m, "baru": ["wp-content/mu-plugins/wpmgr-staging.php"]},
    lambda m: {**m, "baru": ["wp-content/plugins/wp-manager-connector/wp-manager-connector.php"]},
    lambda m: {**m, "baru": ["wp-content/themes/t/baru.php", "wp-content/themes/t/BARU.php"]},
    lambda m: {**m, "baru": [7]},
    lambda m: {**m, "baru": ["wp-content/themes/t/style.css"]},
    lambda m: {**m, "diganti": ["../x"]},
    lambda m: {**m, "diganti": []},
    lambda m: {**m, "charset": "utf16; DROP"},
    lambda m: {**m, "prefix": "wp_`;"},
    lambda m: {**m, "batas_unggah": "4194304"},
    lambda m: {k: v for k, v in m.items() if k != "charset"},
    lambda m: ["bukan", "objek"],
], ids=["mode", "baru-teks", "baru-naik", "baru-absolut", "baru-wpconfig", "baru-mu-staging", "baru-connector",
        "baru-ganda", "baru-angka", "baru-juga-diganti", "diganti-naik", "indeks-tidak-cocok", "charset",
        "prefix", "batas-teks", "tanpa-charset", "bukan-objek"])
def test_meta_snapshot_divalidasi_ketat(sesi, site_staging, staging_aktif, prod, pb, ubah):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _tulis_meta(staging_aktif, snap, ubah(_meta(staging_aktif, snap)))
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert e.value.error_class == STAGING_DITOLAK and "Snapshot rusak" in e.value.pesan
    assert prod.unggahan == {} and prod.langkah == []
    assert _staging(sesi, site_staging).status == StatusStaging.siap


def test_urai_meta_snapshot_menerima_meta_sah():
    meta = dorong.urai_meta_snapshot(json.dumps({
        "mode": "hanya_kode", "baru": ["wp-content/themes/t/baru.php"], "diganti": ["wp-content/themes/t/a.css"],
        "dihapus": [], "charset": "utf8mb4", "prefix": "wp_", "home": "https://contoh.test",
        "batas_unggah": 1048576, "tanda_air": None}))
    assert meta["mode"] == "hanya_kode" and meta["baru"] == ["wp-content/themes/t/baru.php"]
    assert meta["charset"] == "utf8mb4" and meta["batas_unggah"] == 1048576


def test_meta_snapshot_daftar_dibatasi(monkeypatch):
    monkeypatch.setattr(dorong, "MAKS_DAFTAR_SNAPSHOT", 2)
    with pytest.raises(SiteError) as e:
        dorong.urai_meta_snapshot(json.dumps({
            "mode": "hanya_kode", "baru": ["wp-content/a.php", "wp-content/b.php", "wp-content/c.php"],
            "diganti": [], "dihapus": [], "charset": "utf8mb4", "prefix": "wp_", "batas_unggah": 1048576}))
    assert "Snapshot rusak" in e.value.pesan


def test_meta_snapshot_terlalu_besar_ditolak(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    monkeypatch.setattr(dorong, "MAKS_META_SNAPSHOT", 16)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert "Snapshot rusak" in e.value.pesan


def test_indeks_snapshot_rusak_ditolak(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    indeks = staging_aktif / snap.path / "indeks.jsonl"
    baris = json.loads(indeks.read_text(encoding="utf-8").splitlines()[0])
    indeks.write_text(json.dumps({**baris, "h": None}) + "\n", encoding="utf-8")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert "Snapshot rusak" in e.value.pesan
    assert prod.unggahan == {}


def test_berkas_snapshot_tidak_cocok_ditolak_sebelum_unggah(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    (staging_aktif / snap.path / "berkas/wp-content/themes/t/style.css").write_bytes(b"body{}/*lain*/")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert "Snapshot rusak" in e.value.pesan
    assert prod.unggahan == {} and prod.langkah == []


def _bisa_symlink(tmp_path) -> bool:
    try:
        os.symlink(tmp_path / "x", tmp_path / "tautan-uji")
    except (OSError, NotImplementedError):
        return False
    return True


def test_meta_snapshot_symlink_ditolak(sesi, site_staging, staging_aktif, prod, pb, tmp_path):
    if not _bisa_symlink(tmp_path):
        pytest.skip("symlink tidak dapat dibuat di sistem ini")
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    luar = tmp_path / "meta-luar.json"
    meta = staging_aktif / snap.path / "meta.json"
    luar.write_bytes(meta.read_bytes())
    meta.unlink()
    os.symlink(luar, meta)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert "Snapshot rusak" in e.value.pesan


def test_snapshot_path_di_luar_site_ditolak(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    snap.path = "lain/snapshot/j1"
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert e.value.error_class == STAGING_DITOLAK
    assert prod.langkah == []


@pytest.mark.parametrize("snapshot_id", ["1", True, None, -3, 1.5])
def test_snapshot_id_tidak_sah_ditolak(sesi, site_staging, staging_aktif, prod, pb, snapshot_id):
    site, _ = _siap_balik(sesi, site_staging, staging_aktif, prod)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snapshot_id))
    assert e.value.error_class == STAGING_DITOLAK
    assert prod.langkah == []


# ---- R8 pada SQL snapshot ---------------------------------------------------------


@pytest.mark.parametrize("create", [
    b"CREATE TABLE `wp_posts` (`id` int) /*!50100 PARTITION BY HASH (`id`) */;\n",
    b"CREATE TABLE `wp_posts` (`id` int) PARTITION BY HASH (`id`) PARTITIONS 2;\n",
    b"CREATE TABLE `wp_posts` (`id` int /*!80023 INVISIBLE */);\n",
])
def test_r8_sql_snapshot_diperiksa_sebelum_unggah(sesi, site_staging, staging_aktif, prod, pb, create):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod, "timpa_penuh")
    pertama = min((staging_aktif / snap.path / "db").glob("*.sql"))
    pertama.write_bytes(b"DROP TABLE IF EXISTS `wp_posts`;\n" + create)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert e.value.error_class == STAGING_DITOLAK
    assert "Kembalikan dibatalkan sebelum apa pun diubah" in e.value.pesan
    assert prod.unggahan == {} and prod.langkah == []
    assert _staging(sesi, site_staging).status == StatusStaging.siap


def test_r8_tidak_berlaku_untuk_snapshot_hanya_kode(sesi, site_staging, staging_aktif, prod, pb):
    """Snapshot hanya-kode tidak pernah mengimpor database (Koreksi #13), jadi SQL-nya tidak menahan."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    pertama = min((staging_aktif / snap.path / "db").glob("*.sql"))
    pertama.write_bytes(b"CREATE TABLE `wp_posts` (`id` int) PARTITION BY HASH (`id`);\n")
    hasil = _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert hasil["db"] is False and "impor" not in prod.langkah


def test_snapshot_timpa_penuh_tanpa_ekspor_database_ditolak(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod, "timpa_penuh")
    for p in (staging_aktif / snap.path / "db").glob("*.sql"):
        p.unlink()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert "Snapshot rusak" in e.value.pesan
    assert prod.langkah == []


# ---- status, dorong_gagal_pada, log ---------------------------------------------------


def test_halaman_utama_gagal_sesudah_kembalikan_mencatat_dorong_gagal(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    prod.halaman_utama = 500
    hasil = _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert hasil["dorong_gagal"] is True and hasil["halaman_utama"] == 500
    st = _staging(sesi, site_staging)
    assert st.dorong_gagal_pada is not None and st.status == StatusStaging.siap
    assert sesi.query(StagingSnapshot).one().status == "dipakai"
    log = sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Produksi dikembalikan%")).one()
    assert log.level == "error" and "HTTP 500" in log.pesan


def test_log_aktivitas_menyebut_pengguna(sesi, site_staging, staging_aktif, prod, pb, pengguna_uji):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id, dibuat_oleh=pengguna_uji.id))
    log = sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Produksi dikembalikan%")).one()
    assert log.pesan == f"Produksi dikembalikan dari snapshot #{snap.id} oleh a@b.test"
    assert log.user_id == pengguna_uji.id
    assert log.detail["snapshot_id"] == snap.id and log.detail["mode"] == "hanya_kode"


def test_log_dorong_menyebut_pengguna(sesi, site_staging, staging_aktif, prod, pb, pengguna_uji):
    """Tabel log aktivitas spec §8.4: pesan dorong juga diakhiri 'oleh <email>'."""
    site = sesi.get(Site, site_staging.site_id)
    tarik.tangani_staging_tarik(sesi, buat_job(sesi, site.id, JobType.staging_tarik, dibuat_oleh=pengguna_uji.id),
                                prod.klien(site))
    _selesaikan(sesi)
    (staging_aktif / str(site.id) / "files/wp-content/themes/t/style.css").write_bytes(b"body{color:red}")
    job = buat_job(sesi, site.id, JobType.staging_dorong, {"mode": "hanya_kode"}, dibuat_oleh=pengguna_uji.id)
    dorong.tangani_staging_dorong(sesi, job, prod.klien(site))
    pesan = {x.pesan for x in sesi.query(ActivityLog).all()}
    assert "Staging dibuat oleh a@b.test" in pesan
    assert "Dorong ke produksi (hanya kode) oleh a@b.test" in pesan


def test_kembalikan_staging_dijeda_tetap_dijeda(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    st = _staging(sesi, site_staging)
    st.status, st.aktif = StatusStaging.dijeda, False
    sesi.commit()
    with pytest.raises(SiteError):
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id, nama="salah"))
    assert _staging(sesi, site_staging).status == StatusStaging.dijeda
    _selesaikan_gagal(sesi)
    _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert _staging(sesi, site_staging).status == StatusStaging.dijeda
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"


def _selesaikan_gagal(sesi):
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.failed
    sesi.commit()


def test_snapshot_bisa_dipakai_ulang(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _kembalikan(sesi, site, prod, snap.id)
    prod.berkas["wp-content/themes/t/style.css"] = (b"body{color:blue}", MTIME)
    _kembalikan(sesi, site, prod, snap.id)
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Produksi dikembalikan%")).count() == 2


def test_snapshot_dorongan_yang_dipulihkan_rekonsiliasi_ditolak(sesi, site_staging, staging_aktif, prod, pb):
    """Dorongan pemilik snapshot ternyata macet setengah jalan: rekonsiliasi memulihkannya, snapshot tidak berlaku."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    job_dorong = sesi.get(Job, snap.job_id)
    k = dict(job_dorong.payload["kemajuan"])
    k.pop("produksi_bersih", None)
    job_dorong.payload = {**job_dorong.payload, "kemajuan": k}
    sesi.commit()
    token = k["token"]
    prod.dorongan[k["dorong_id"]] = {"status": "menukar", "potongan": {}, "hasil": {}, "rencana": {"sql": False},
                                     "token_hash": hashlib.sha256(token.encode()).hexdigest()}
    prod.kunci = k["dorong_id"]
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert e.value.error_class == STAGING_DITOLAK and "tidak ditemukan" in e.value.pesan
    assert (k["dorong_id"], "pulihkan") in prod.langkah_id
    assert prod.unggahan == {}
    assert _staging(sesi, site_staging).status == StatusStaging.siap


def test_handler_kembalikan_terdaftar():
    from wpmgr.jobs.handlers import HANDLER

    assert HANDLER[JobType.staging_kembalikan] is dorong.tangani_staging_kembalikan


def test_dorong_tidak_bisa_diantrekan_saat_kembalikan_tertunda(sesi, site_staging):
    buat_job(sesi, site_staging.site_id, JobType.staging_kembalikan, {"snapshot_id": 1})
    with pytest.raises(IntegrityError):
        buat_job(sesi, site_staging.site_id, JobType.staging_dorong, {"mode": "hanya_kode"})
    sesi.rollback()


# ---- fix putaran 1 ------------------------------------------------------------------


def test_kembalikan_tidak_menyembunyikan_staging_gagal(sesi, site_staging, staging_aktif, prod, pb):
    """Kembalikan menyangkut produksi: staging yang gagal tetap gagal dengan galatnya."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    st = _staging(sesi, site_staging)
    st.status, st.galat, st.gagal_asal = StatusStaging.gagal, "Tarik gagal: disk penuh.", "salinan"
    sesi.commit()
    hasil = _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert hasil["dorong_gagal"] is False
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.galat == "Tarik gagal: disk penuh."
    assert st.dorong_gagal_pada is None


def _staging_gagal(sesi, site_staging) -> None:
    st = _staging(sesi, site_staging)
    st.status, st.galat, st.gagal_asal = StatusStaging.gagal, "Tarik gagal: disk penuh.", "salinan"
    sesi.commit()


def test_tolak_pra_tukar_final_staging_gagal_tetap_gagal(sesi, site_staging, staging_aktif, prod, pb):
    """R18 pada _staging_utuh: penolakan pra-pemeriksaan tukar tidak mengangkat staging yang gagal."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _staging_gagal(sesi, site_staging)
    prod.tolak_tukar = {"wpmgr_staging_tabel_lama": 1}
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert e.value.error_class == STAGING_DITOLAK
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.galat == "Tarik gagal: disk penuh."
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"


def test_rekonsiliasi_lama_gagal_final_staging_gagal_tetap_gagal(sesi, site_staging, staging_aktif, prod, pb):
    """R18 pada _staging_utuh: rekonsiliasi dorongan lama yang gagal final tidak mengangkat staging yang gagal."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    lama_id, token = "a" * 32, "b" * 32
    prod.dorongan[lama_id] = {"status": "ditukar", "potongan": {}, "rencana": {"sql": False},
                              "hasil": {"tukar": {"selesai": True, "status": "ditukar"}},
                              "token_hash": hashlib.sha256(token.encode()).hexdigest()}
    prod.kunci = lama_id
    # Job lama yang sukses (bukan dorong gagal): gagal staging tetap milik salinan staging.
    lama = buat_job(sesi, site.id, JobType.staging_dorong, {"mode": "hanya_kode", "kemajuan": {
        "dorong_id": lama_id, "token": token, "unggah_mulai": True, "langkah_terapkan": "selesai"}})
    lama.status = JobStatus.success
    sesi.commit()
    _staging_gagal(sesi, site_staging)
    prod.kejadian["bersihkan"] = ["putus_awal"] * 5
    job = _job_balik(sesi, site, snap.id)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert e.value.error_class == STAGING_DITOLAK and e.value.pesan == dorong.PESAN_LAMA_AKHIR_BALIK
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.galat == "Tarik gagal: disk penuh."
    assert prod.unggahan == {}


def test_tolak_pra_tukar_final_gagal_produksi_dibersihkan(sesi, site_staging, staging_aktif, prod, pb):
    """Tanpa gagal milik staging, perilaku lama tetap: staging siap dengan pesan penolakan."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    prod.tolak_tukar = {"wpmgr_staging_tabel_lama": 1}
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.galat == e.value.pesan


def test_batal_kembalikan_staging_gagal_tetap_gagal(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    st = _staging(sesi, site_staging)
    st.status, st.galat, st.gagal_asal = StatusStaging.gagal, "Tarik gagal: disk penuh.", "salinan"
    sesi.commit()
    prod.sebelum["/staging/terapkan"] = lambda p, n, badan: _minta_batal(sesi, site_staging)
    with pytest.raises(umum.GalatDibatalkan):
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.galat == "Tarik gagal: disk penuh."
    # Pembatalannya tetap tercatat di log aktivitas.
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Kembalikan produksi dibatalkan%")).count() == 1


# ---- fix putaran 2 ------------------------------------------------------------------


def _tarik_gagal(sesi, site_staging, prod, pb) -> str:
    """Tarik (segarkan) sungguhan yang gagal final di tengah: gagal milik salinan staging (R20)."""
    from wpmgr.staging.pembantu import GalatPembantu

    site = sesi.get(Site, site_staging.site_id)
    pb.gagal["db_impor"] = GalatPembantu("impor", "Impor database staging gagal.")
    with pytest.raises(SiteError):
        tarik.tangani_staging_tarik(sesi, buat_job(sesi, site.id, JobType.staging_tarik), prod.klien(site))
    del pb.gagal["db_impor"]
    _selesaikan_gagal(sesi)
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.gagal_asal == "salinan"
    return st.galat


def test_tolak_di_tengah_kembalikan_mempertahankan_galat_staging(sesi, site_staging, staging_aktif, prod, pb):
    """GalatDitolakTanpaUbah di dalam pembungkus (R8 di tahap mulai): galat staging asli tetap."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod, "timpa_penuh")
    galat = _tarik_gagal(sesi, site_staging, prod, pb)
    min((staging_aktif / snap.path / "db").glob("*.sql")).write_bytes(
        b"CREATE TABLE `wp_posts` (`id` int) PARTITION BY HASH (`id`);\n")
    job = _job_balik(sesi, site, snap.id)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert e.value.error_class == STAGING_DITOLAK and "Kembalikan dibatalkan" in e.value.pesan
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.galat == galat


def test_kembalikan_batal_lalu_sukses_tidak_mengangkat_gagal_tarik(sesi, site_staging, staging_aktif, prod, pb):
    """Skenario reviewer: tarik gagal, kembalikan #1 dibatalkan sebelum tukar, kembalikan #2 sukses."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    galat = _tarik_gagal(sesi, site_staging, prod, pb)
    prod.sebelum["/staging/terapkan"] = lambda p, n, badan: _minta_batal(sesi, site_staging)
    with pytest.raises(umum.GalatDibatalkan):
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    _selesaikan_gagal(sesi)
    del prod.sebelum["/staging/terapkan"]
    _kembalikan(sesi, site, prod, snap.id)
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.galat == galat


def test_kembalikan_tolak_pra_tukar_lalu_sukses_tidak_mengangkat_gagal_tarik(sesi, site_staging, staging_aktif,
                                                                            prod, pb):
    """Varian lewat _staging_utuh: kembalikan #1 ditolak pra-pemeriksaan tukar (tidak pernah menukar)."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    galat = _tarik_gagal(sesi, site_staging, prod, pb)
    prod.tolak_tukar = {"wpmgr_staging_tabel_lama": 1}
    with pytest.raises(SiteError):
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    _selesaikan_gagal(sesi)
    _kembalikan(sesi, site, prod, snap.id)
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.galat == galat


def _dorong_ditolak(sesi, site, prod, pesan):
    sebelum = len(prod.diminta)
    job = buat_job(sesi, site.id, JobType.staging_dorong, {"mode": "hanya_kode", "konfirmasi_nama": None})
    with pytest.raises(SiteError) as e:
        dorong.tangani_staging_dorong(sesi, job, prod.klien(site))
    _selesaikan_gagal(sesi)
    assert isinstance(e.value, umum.GalatDitolakTanpaUbah) and e.value.pesan == pesan
    assert len(prod.diminta) == sebelum


def test_r22_kembalikan_gagal_sesudah_tukar_tidak_menimpa_salinan(sesi, site_staging, staging_aktif, prod, pb):
    """Putusan R22 (menggantikan R20 untuk kasus ini): 'salinan' menang atas kegagalan produksi sesudah tukar."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    galat = _tarik_gagal(sesi, site_staging, prod, pb)
    prod.kejadian["tukar"] = ["putus"] * (dorong.MAKS_RAGU_TUKAR + 1)
    job = _job_balik(sesi, site, snap.id)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    # Kegagalan produksinya tetap sampai ke job dan ke dorong_gagal_pada.
    assert e.value.pesan == dorong.PESAN_AKHIR_BALIK["tukar"]
    _selesaikan_gagal(sesi)
    st = _staging(sesi, site_staging)
    assert (st.status, st.gagal_asal, st.galat) == (StatusStaging.gagal, "salinan", galat)
    assert st.dorong_gagal_pada is not None
    _dorong_ditolak(sesi, site, prod, dorong.PESAN_SALINAN_GAGAL)
    # Kembalikan berikutnya yang sukses juga tidak menghapusnya.
    _kembalikan(sesi, site, prod, snap.id)
    st = _staging(sesi, site_staging)
    assert (st.status, st.gagal_asal, st.galat) == (StatusStaging.gagal, "salinan", galat)


# ---- putusan R22: kembalikan saat tarik/uji tertunda -------------------------------------


def _menyalin(sesi, site_staging, status=StatusStaging.menyalin) -> str:
    """Tarik/uji sedang menunggu percobaan ulang: salinan belum utuh."""
    st = _staging(sesi, site_staging)
    st.status, st.galat, st.gagal_asal = status, "Terputus, dilanjutkan otomatis: koneksi putus", None
    sesi.commit()
    return st.galat


@pytest.mark.parametrize("status_kerja", [StatusStaging.menyalin, StatusStaging.berjalan_uji])
def test_r22_kembalikan_sukses_saat_tarik_tertunda_tidak_mengangkat_ke_siap(sesi, site_staging, staging_aktif,
                                                                           prod, pb, status_kerja):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _menyalin(sesi, site_staging, status_kerja)
    assert _kembalikan(sesi, site, prod, snap.id)["dorong_gagal"] is False
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    st = _staging(sesi, site_staging)
    assert (st.status, st.gagal_asal) == (status_kerja, None)
    _dorong_ditolak(sesi, site, prod, dorong.PESAN_SALINAN_SIBUK)


def test_r22_kembalikan_gagal_pra_tukar_saat_tarik_tertunda_tetap_menyalin(sesi, site_staging, staging_aktif,
                                                                          prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _menyalin(sesi, site_staging)
    prod.kejadian["unggah"] = ["putus_awal"] * 20
    job = _job_balik(sesi, site, snap.id)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError):
        _jalankan(sesi, site, prod, job)
    _selesaikan_gagal(sesi)
    assert "tukar" not in prod.langkah
    st = _staging(sesi, site_staging)
    assert (st.status, st.gagal_asal) == (StatusStaging.menyalin, None)
    prod.kejadian.pop("unggah")
    _dorong_ditolak(sesi, site, prod, dorong.PESAN_SALINAN_SIBUK)


def test_r22_kembalikan_ditolak_pra_tukar_saat_tarik_tertunda_tetap_menyalin(sesi, site_staging, staging_aktif,
                                                                            prod, pb):
    """Lewat _staging_utuh (pra-pemeriksaan tukar menolak)."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _menyalin(sesi, site_staging)
    prod.tolak_tukar = {"wpmgr_staging_tabel_lama": 1}
    with pytest.raises(SiteError):
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    _selesaikan_gagal(sesi)
    assert _staging(sesi, site_staging).status == StatusStaging.menyalin
    _dorong_ditolak(sesi, site, prod, dorong.PESAN_SALINAN_SIBUK)


def test_r22_kembalikan_dibatalkan_saat_tarik_tertunda_tetap_menyalin(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _menyalin(sesi, site_staging)
    prod.sebelum["/staging/terapkan"] = lambda p, n, badan: _minta_batal(sesi, site_staging)
    with pytest.raises(umum.GalatDibatalkan):
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    _selesaikan_gagal(sesi)
    del prod.sebelum["/staging/terapkan"]
    assert _staging(sesi, site_staging).status == StatusStaging.menyalin
    _dorong_ditolak(sesi, site, prod, dorong.PESAN_SALINAN_SIBUK)


def test_r22_kembalikan_gagal_sesudah_tukar_saat_tarik_tertunda_tetap_menyalin(sesi, site_staging, staging_aktif,
                                                                              prod, pb):
    """Seperti 'salinan': kegagalan produksi hanya ditandai dorong_gagal_pada, gerbang dorong tetap."""
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    _menyalin(sesi, site_staging)
    prod.kejadian["tukar"] = ["putus"] * (dorong.MAKS_RAGU_TUKAR + 1)
    job = _job_balik(sesi, site, snap.id)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError):
        _jalankan(sesi, site, prod, job)
    _selesaikan_gagal(sesi)
    st = _staging(sesi, site_staging)
    assert (st.status, st.gagal_asal) == (StatusStaging.menyalin, None)
    assert st.dorong_gagal_pada is not None
    _dorong_ditolak(sesi, site, prod, dorong.PESAN_SALINAN_SIBUK)


@pytest.mark.parametrize("langkah,sah", [
    # akhiri_gagal mati sebelum _buang_snapshot: snapshot dorongan yang tidak pernah menukar tertinggal.
    ("siapkan", False), ("cek_ulang", False), (None, False),
    # Tukar sudah dikirim (hasil tidak pasti, atau selesai belum terkonfirmasi): titik kembali sah.
    ("tukar", True), ("selesai", True),
])
def test_snapshot_dorongan_yang_tidak_pernah_menukar_ditolak(sesi, site_staging, staging_aktif, prod, pb,
                                                            langkah, sah):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    job_dorong = sesi.get(Job, snap.job_id)
    job_dorong.payload = {**job_dorong.payload,
                          "kemajuan": {**job_dorong.payload["kemajuan"], "langkah_terapkan": langkah}}
    job_dorong.status = JobStatus.failed
    sesi.commit()
    job = _job_balik(sesi, site, snap.id)
    if sah:
        assert _jalankan(sesi, site, prod, job)["dorong_gagal"] is False
        return
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, job)
    assert e.value.error_class == STAGING_DITOLAK and e.value.pesan == dorong.PESAN_SNAPSHOT_BUKAN_TITIK
    assert prod.langkah == [] and prod.unggahan == {}
    assert _staging(sesi, site_staging).status == StatusStaging.siap


def test_snapshot_tanpa_job_pemilik_ditolak(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    snap.job_id = None
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap.id))
    assert e.value.pesan == dorong.PESAN_SNAPSHOT_BUKAN_TITIK


def test_snapshot_milik_site_lain_ditolak(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siap_balik(sesi, site_staging, staging_aktif, prod)
    lain = Site(id=uuid.uuid4(), nama="Contoh", url="https://lain.test", status=SiteStatus.active,
                secret_terenkripsi=b"x")
    sesi.add(lain)
    sesi.commit()
    snap_lain = StagingSnapshot(site_id=lain.id, job_id=snap.job_id, jenis="sebelum_dorong", status="tersedia",
                                path=snap.path)
    sesi.add(snap_lain)
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site, prod, _job_balik(sesi, site, snap_lain.id))
    assert e.value.error_class == STAGING_DITOLAK and e.value.pesan == dorong.PESAN_SNAPSHOT_HILANG
    assert prod.langkah == [] and prod.unggahan == {}
