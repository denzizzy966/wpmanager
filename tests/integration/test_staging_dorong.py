import hashlib
import json
import os
import time

import httpx
import pytest
from staging_palsu import GB, PembantuPalsu, ProduksiPalsu

from wpmgr.errors import STAGING_DITOLAK, STAGING_GAGAL, TRANSIENT, SiteError
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    Site,
    Staging,
    StagingSnapshot,
    StatusStaging,
)
from wpmgr.staging import dorong, tarik, umum
from wpmgr.staging.rencana import Entri

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
        "wp-content/plugins/p/p.php": (b"<?php //p", MTIME),
        "wp-content/uploads/lama.jpg": (b"jpg", MTIME),
    }
    p.tabel = {"wp_options": [b"CREATE TABLE `wp_options` (`option_name` varchar(191));\n"],
               "wp_posts": [b"CREATE TABLE `wp_posts` (`id` int);\n"]}
    monkeypatch.setattr(umum, "buat_http", p.http)
    return p


@pytest.fixture
def pb(staging_aktif, monkeypatch, site_staging):
    palsu = PembantuPalsu(staging_aktif)
    ekspor = staging_aktif / str(site_staging.site_id) / "ekspor" / "dorong.sql"

    def saat_wpcli(nama, argumen):
        if "--export" in argumen:
            ekspor.write_bytes(palsu.sql_ekspor)

    palsu.sql_ekspor = SQL_EKSPOR
    palsu.saat_wpcli = saat_wpcli
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


def _selesaikan(sesi):
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.success
    sesi.commit()


def _tarik(sesi, site_staging, prod):
    site = sesi.get(Site, site_staging.site_id)
    tarik.tangani_staging_tarik(sesi, buat_job(sesi, site.id, JobType.staging_tarik), prod.klien(site))
    _selesaikan(sesi)


def _dorong(sesi, site_staging, prod, mode, konfirmasi=None, job=None, klien=None):
    site = sesi.get(Site, site_staging.site_id)
    job = job or buat_job(sesi, site.id, JobType.staging_dorong, {"mode": mode, "konfirmasi_nama": konfirmasi})
    hasil = dorong.tangani_staging_dorong(sesi, job, klien or prod.klien(site))
    _selesaikan(sesi)
    return hasil


def _job_baru(sesi, site_staging, mode, konfirmasi=None):
    return buat_job(sesi, site_staging.site_id, JobType.staging_dorong, {"mode": mode, "konfirmasi_nama": konfirmasi})


def _jalankan(sesi, site_staging, prod, job):
    site = sesi.get(Site, site_staging.site_id)
    return dorong.tangani_staging_dorong(sesi, job, prod.klien(site))


def _files(staging_aktif, site_staging):
    return staging_aktif / str(site_staging.site_id) / "files"


def _ubah_staging(files):
    baru = int(time.time())
    (files / "wp-content/themes/t/style.css").write_bytes(b"body{color:red}")
    os.utime(files / "wp-content/themes/t/style.css", (baru, baru))
    (files / "wp-content/plugins/p/baru.php").write_bytes(b"<?php //baru")
    (files / "wp-content/plugins/p/p.php").unlink()
    (files / "wp-content/uploads/baru.jpg").write_bytes(b"jpg2")
    (files / "index.php").write_bytes(b"<?php // inti diubah di staging")


def _siap(sesi, site_staging, staging_aktif, prod):
    _tarik(sesi, site_staging, prod)
    _ubah_staging(_files(staging_aktif, site_staging))


def _staging(sesi, site_staging) -> Staging:
    return sesi.get(Staging, site_staging.id, populate_existing=True)


def _kemajuan(sesi, job) -> dict:
    return umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))


def _galat(kode_http: int, kode: str, pesan: str = "x", **data) -> httpx.Response:
    return httpx.Response(kode_http, json={"code": kode, "message": pesan, "data": {"status": kode_http, **data}})


def _jumlah(prod, route) -> int:
    return sum(1 for r, _ in prod.diminta if r == route)


# ---- alur dasar (brief) --------------------------------------------------------


def test_dorong_hanya_kode(sesi, site_staging, staging_aktif, prod, pb):
    _tarik(sesi, site_staging, prod)
    _ubah_staging(_files(staging_aktif, site_staging))
    prod.berkas["wp-content/uploads/pesanan.pdf"] = (b"pdf", MTIME)
    tanda_air_sebelum = prod.hitung.get("/staging/tanda-air")

    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")

    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"
    assert prod.berkas["wp-content/plugins/p/baru.php"][0] == b"<?php //baru"
    assert "wp-content/plugins/p/p.php" not in prod.berkas
    assert prod.berkas["wp-content/uploads/baru.jpg"][0] == b"jpg2"
    assert prod.berkas["index.php"][0] == b"<?php // inti"
    assert prod.berkas["wp-content/uploads/pesanan.pdf"][0] == b"pdf"
    assert "wp-content/mu-plugins/wpmgr-staging.php" not in prod.berkas
    assert prod.langkah == ["siapkan", "tukar", "selesai"]
    assert prod.hitung.get("/staging/tanda-air") == tanda_air_sebelum
    assert any(route == "/staging/bersihkan" for route, _ in prod.diminta)
    assert prod.dorongan == {} and prod.kunci is None

    snap = sesi.query(StagingSnapshot).one()
    assert snap.status == "tersedia" and snap.jenis == "sebelum_dorong"
    assert snap.detail["mode"] == "hanya_kode"
    d = staging_aktif / snap.path
    assert (d / "berkas/wp-content/themes/t/style.css").read_bytes() == b"body{}"
    assert (d / "berkas/wp-content/plugins/p/p.php").read_bytes() == b"<?php //p"
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert sorted(meta["baru"]) == ["wp-content/plugins/p/baru.php", "wp-content/uploads/baru.jpg"]
    assert meta["diganti"] == ["wp-content/themes/t/style.css"]
    assert meta["dihapus"] == ["wp-content/plugins/p/p.php"]
    assert any(p.name.endswith(".sql") for p in (d / "db").iterdir())
    assert hasil["dorong_gagal"] is False
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap
    assert not (staging_aktif / str(site_staging.site_id) / "dorong").exists()
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Dorong ke produksi (hanya kode)%")).count() == 1


def test_timpa_penuh_ditolak_karena_data_baru(sesi, site_staging, prod, pb):
    _tarik(sesi, site_staging, prod)
    prod.tanda_air["sumber"]["comments"] = {"maks_id": 5, "jumlah": 4}
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert e.value.error_class == STAGING_DITOLAK
    assert "2 komentar baru" in e.value.pesan
    assert "Ketik nama site" in e.value.pesan
    assert prod.unggahan == {}
    assert prod.langkah == []
    # Ditolak sebelum apa pun disentuh: staging tidak ditandai gagal.
    assert _staging(sesi, site_staging).status == StatusStaging.siap


def test_timpa_penuh_dengan_konfirmasi_nama(sesi, site_staging, staging_aktif, prod, pb):
    _tarik(sesi, site_staging, prod)
    _ubah_staging(_files(staging_aktif, site_staging))
    prod.tanda_air["sumber"]["comments"] = {"maks_id": 5, "jumlah": 4}
    prod.berkas["wp-content/uploads/pesanan.pdf"] = (b"pdf", MTIME)
    prod.berkas["google123.html"] = (b"verifikasi", MTIME)
    _dorong(sesi, site_staging, prod, "timpa_penuh", konfirmasi="Contoh")
    assert ("wpcli", "contoh-test", "search-replace", "https://contoh-test.staging.contoh.id",
            "https://contoh.test", "--export") in pb.panggilan
    assert prod.sql_diterapkan == SQL_EKSPOR
    assert prod.langkah == ["siapkan", "impor", "tukar", "selesai"]
    assert prod.berkas["index.php"][0] == b"<?php // inti diubah di staging"
    assert prod.berkas["wp-content/uploads/pesanan.pdf"][0] == b"pdf"
    assert prod.berkas["google123.html"][0] == b"verifikasi"
    log = sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Dorong ke produksi (timpa penuh)%")).one()
    assert log.detail["perubahan"] == ["2 komentar baru"]
    # ekspor/ di-bind mount ke container: salinannya dipindah, bukan dibiarkan di sana.
    assert not (staging_aktif / str(site_staging.site_id) / "ekspor" / "dorong.sql").exists()


def test_tanda_air_berubah_di_tengah_membatalkan(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)

    def user_baru(p, n, badan):
        p.tanda_air["sumber"]["users"] = {"maks_id": 2, "jumlah": 2}

    prod.sebelum["/staging/unggah"] = user_baru
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert e.value.error_class == STAGING_GAGAL
    assert "berubah selama dorong" in e.value.pesan and "1 user baru" in e.value.pesan
    assert "tukar" not in prod.langkah
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    assert any(route == "/staging/bersihkan" for route, _ in prod.diminta)
    assert prod.dorongan == {} and prod.kunci is None
    # Produksi tidak pernah disentuh: snapshotnya tidak disimpan sebagai titik kembali.
    assert sesi.query(StagingSnapshot).count() == 0


def test_tanda_air_dicek_ulang_tepat_sebelum_tukar(sesi, site_staging, staging_aktif, prod, pb):
    """Data yang masuk selama siapkan/impor juga membatalkan dorong (cek ulang sesudah impor)."""
    _siap(sesi, site_staging, staging_aktif, prod)

    def pesanan_baru(p, n, badan):
        if badan["langkah"] == "impor":
            p.tanda_air["sumber"]["pesanan_posts"] = {"maks_id": 7, "jumlah": 1}

    prod.sebelum["/staging/terapkan"] = pesanan_baru
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert "1 pesanan (tabel posts) baru" in e.value.pesan
    assert prod.langkah == ["siapkan", "impor"]
    assert prod.dorongan == {}


def test_halaman_utama_gagal_menyalakan_dorong_gagal(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.halaman_utama = 500
    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")
    assert hasil["dorong_gagal"] is True
    assert hasil["halaman_utama"] == 500
    sesi.refresh(site_staging)
    assert site_staging.dorong_gagal_pada is not None
    log = sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Dorong ke produksi%HTTP 500%")).one()
    assert log.level == "error"


def test_cek_halaman_tidak_mengikuti_redirect_dan_dibatasi(monkeypatch):
    diminta = []

    def tangani(r):
        diminta.append(str(r.url))
        return httpx.Response(302, headers={"Location": "https://lain.test/"}, content=b"x" * (3 * 1024 * 1024))

    monkeypatch.setattr(umum, "buat_http", lambda: httpx.Client(transport=httpx.MockTransport(tangani)))
    assert dorong.cek_halaman("https://contoh.test") == 302
    assert diminta == ["https://contoh.test/"]

    def putus(r):
        raise httpx.ConnectError("mati", request=r)

    monkeypatch.setattr(umum, "buat_http", lambda: httpx.Client(transport=httpx.MockTransport(putus)))
    assert dorong.cek_halaman("https://contoh.test") == 0


def test_terapkan_gagal_dipulihkan(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.gagal_langkah = "tukar"
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert e.value.error_class == STAGING_GAGAL
    assert "dipulihkan" in e.value.pesan
    assert "pulihkan" in prod.langkah
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    sesi.refresh(site_staging)
    assert site_staging.dorong_gagal_pada is None
    assert sesi.query(StagingSnapshot).count() == 0


def test_snapshot_dipangkas_ke_n(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_SNAPSHOT", "2")
    get_settings.cache_clear()
    _tarik(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    for i in range(3):
        (files / "wp-content/themes/t/style.css").write_bytes(f"body{{z-index:{i}}}".encode())
        _dorong(sesi, site_staging, prod, "hanya_kode")
    snap = sesi.query(StagingSnapshot).order_by(StagingSnapshot.id).all()
    assert [s.status for s in snap] == ["dipangkas", "tersedia", "tersedia"]
    assert not (staging_aktif / snap[0].path).exists()
    assert (staging_aktif / snap[2].path).exists()


def test_snapshot_kandidat_rekonsiliasi_tidak_dipangkas_di_cek(sesi, site_staging, staging_aktif, prod, pb,
                                                               monkeypatch):
    """Dorongan lama yang belum terbukti bersih menahan snapshotnya walau melebihi WPMGR_STAGING_SNAPSHOT."""
    from sqlalchemy.orm.attributes import flag_modified

    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_SNAPSHOT", "1")
    get_settings.cache_clear()
    _tarik(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    (files / "wp-content/themes/t/style.css").write_bytes(b"body{z-index:0}")
    _dorong(sesi, site_staging, prod, "hanya_kode")
    lama = sesi.query(Job).filter(Job.tipe == JobType.staging_dorong).one()
    # Pembersihan area dorong job lama tidak pernah terbukti di connector.
    lama.payload = {**lama.payload, "kemajuan": {**lama.payload["kemajuan"], "produksi_bersih": False}}
    flag_modified(lama, "payload")
    sesi.commit()
    # Rekonsiliasi di awal dorongan baru ditolak tanpa kode yang menahan dorongan: job lama tetap kandidat.
    prod.kejadian["bersihkan"] = [_galat(400, "wpmgr_staging_permintaan", "Permintaan tidak sah.")]
    (files / "wp-content/themes/t/style.css").write_bytes(b"body{z-index:1}")

    _dorong(sesi, site_staging, prod, "hanya_kode")

    assert _kemajuan(sesi, lama).get("produksi_bersih") is not True
    snap = sesi.query(StagingSnapshot).order_by(StagingSnapshot.id).all()
    assert snap[0].job_id == lama.id
    assert [s.status for s in snap] == ["tersedia", "tersedia"]
    assert all((staging_aktif / s.path).is_dir() for s in snap)


def test_unggah_dilanjutkan_setelah_putus(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.jadwal_gagal["/staging/unggah"] = {2, 3, 4}
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_dorong, {"mode": "hanya_kode"})
    with pytest.raises(SiteError):
        dorong.tangani_staging_dorong(sesi, job, prod.klien(site))
    # Galat sementara: job akan diulang, area dorong di produksi dibiarkan.
    assert _staging(sesi, site_staging).status == StatusStaging.mendorong
    assert not any(route == "/staging/bersihkan" for route, _ in prod.diminta)
    dorong.tangani_staging_dorong(sesi, job, prod.klien(site))
    assert prod.hitung["/staging/unggah"] == len(prod.unggahan) + 3
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"


def test_batal_dorong_membersihkan_area_sementara(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)

    def minta_batal(p, n, badan):
        st = sesi.get(Staging, site_staging.id)
        st.batal_diminta_pada = umum.sekarang()
        sesi.commit()

    prod.sebelum["/staging/unggah"] = minta_batal
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert "Dibatalkan" in e.value.pesan
    assert any(route == "/staging/bersihkan" for route, _ in prod.diminta)
    assert not (staging_aktif / str(site_staging.site_id) / "dorong").exists()
    assert prod.langkah == []
    assert prod.dorongan == {} and prod.kunci is None
    assert sesi.query(StagingSnapshot).count() == 0


def test_mode_tidak_sah_dan_staging_dijeda_ditolak(sesi, site_staging, prod, pb):
    _tarik(sesi, site_staging, prod)
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "semuanya")
    assert e.value.error_class == STAGING_DITOLAK
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.failed
    st = sesi.get(Staging, site_staging.id)
    st.aktif = False
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert "dijeda" in e.value.pesan
    # Status staging yang dijeda tidak diubah oleh penolakan.
    assert _staging(sesi, site_staging).status == StatusStaging.siap


def test_rencana_unggah_dan_baca_gabungan(tmp_path):
    a, b = tmp_path / "a.sql", tmp_path / "b.sql"
    a.write_bytes(b"12345")
    b.write_bytes(b"6789")
    assert dorong.baca_gabungan([a, b], 3, 4) == b"4567"
    assert dorong.baca_gabungan([a, b], 0, 100) == b"123456789"
    sumber = dorong.SumberDorong(ganti=[Entri("x.php", 10, 1, "0" * 64)], akar_berkas=tmp_path, hapus=[],
                                 sql=[a, b], charset="utf8mb4")
    daftar = dorong.rencana_unggah(sumber, b"R" * 7, 4)
    assert [(u.jenis, u.dari, u.panjang) for u in daftar] == [
        ("rentang", 0, 4), ("rentang", 4, 4), ("rentang", 8, 2),
        ("sql", 0, 4), ("sql", 4, 4), ("sql", 8, 1),
        ("rencana", 0, 4), ("rencana", 4, 3),
    ]


def test_isi_unggahan_membaca_lewat_aman_dan_memeriksa_hash(tmp_path):
    (tmp_path / "a.php").write_bytes(b"isi-a")
    benar = Entri("a.php", 5, 1, hashlib.sha256(b"isi-a").hexdigest())
    sumber = dorong.SumberDorong(ganti=[benar], akar_berkas=tmp_path, hapus=[], sql=[], charset="utf8mb4")
    meta, isi = dorong.isi_unggahan(sumber, dorong.Unggahan("berkas", (benar,), 0, 5), b"")
    assert meta == [{"path": "a.php", "mtime": 1}] and isi == [b"isi-a"]
    salah = Entri("a.php", 5, 1, "0" * 64)
    with pytest.raises(SiteError) as e:
        dorong.isi_unggahan(sumber, dorong.Unggahan("berkas", (salah,), 0, 5), b"")
    assert e.value.error_class == STAGING_GAGAL and "a.php" in e.value.pesan
    hilang = Entri("tidak-ada.php", 5, 1, "0" * 64)
    with pytest.raises(SiteError):
        dorong.isi_unggahan(sumber, dorong.Unggahan("rentang", (hilang,), 0, 5), b"")


# ---- tukar dengan hasil tidak diketahui ----------------------------------------


def test_tukar_unknown_direkonsiliasi_dari_keadaan_connector(sesi, site_staging, staging_aktif, prod, pb):
    """Respons tukar hilang padahal tukar sukses: dikirim ulang, dibalas hasil tersimpan, TANPA pulihkan."""
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["tukar"] = ["putus"]
    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")
    assert hasil["dorong_gagal"] is False
    assert prod.langkah == ["siapkan", "tukar", "tukar", "selesai"]
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"


def test_tukar_unknown_berlarut_dilanjutkan_job_berikutnya(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["tukar"] = ["putus"] * (dorong.MAKS_RAGU_TUKAR + 1)
    job = _job_baru(sesi, site_staging, "hanya_kode")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.error_class == TRANSIENT
    assert _kemajuan(sesi, job)["langkah_terapkan"] == "tukar"
    assert _staging(sesi, site_staging).status == StatusStaging.mendorong
    assert "pulihkan" not in prod.langkah
    # Batal yang diminta sesudah tukar dikirim tidak menghentikan job.
    st = _staging(sesi, site_staging)
    st.batal_diminta_pada = umum.sekarang()
    sesi.commit()
    hasil = _jalankan(sesi, site_staging, prod, job)
    assert hasil["dorong_gagal"] is False
    assert "pulihkan" not in prod.langkah
    assert prod.langkah[-1] == "selesai" and prod.langkah.count("siapkan") == 1
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"
    st = _staging(sesi, site_staging)
    assert st.batal_diminta_pada is None and st.status == StatusStaging.siap


def test_tukar_unknown_final_mencatat_dorong_gagal_dan_menjaga_snapshot(sesi, site_staging, staging_aktif,
                                                                         prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["tukar"] = ["putus"] * (dorong.MAKS_RAGU_TUKAR + 1)
    job = _job_baru(sesi, site_staging, "hanya_kode")
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.error_class == STAGING_GAGAL
    assert "tidak dapat dipastikan" in e.value.pesan
    st = _staging(sesi, site_staging)
    assert st.dorong_gagal_pada is not None and st.status == StatusStaging.gagal
    assert "tidak dapat dipastikan" in st.galat
    # Snapshot dipertahankan (satu-satunya jalan kembali), dan bersihkan tidak
    # pernah membatalkan tukar yang sudah terjadi: connector menolaknya (409).
    assert sesi.query(StagingSnapshot).one().status == "tersedia"
    dorong_id = _kemajuan(sesi, job)["dorong_id"]
    assert prod.dorongan[dorong_id]["status"] == "ditukar"
    assert "pulihkan" not in prod.langkah
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"

    # Dorongan berikutnya menuntaskan dorongan yang tertinggal lebih dulu.
    job.status = JobStatus.failed
    sesi.commit()
    _dorong(sesi, site_staging, prod, "hanya_kode")
    assert (dorong_id, "selesai") in prod.langkah_id
    assert (dorong_id, "pulihkan") not in prod.langkah_id
    assert dorong_id not in prod.dorongan
    assert _kemajuan(sesi, job)["produksi_bersih"] is True


def test_tukar_gagal_dengan_respons_hilang_lalu_dipulihkan(sesi, site_staging, staging_aktif, prod, pb):
    """Tukar gagal dan dipulihkan otomatis, tetapi respons hilang: kirim ulang -> 409 urutan -> pulihkan."""
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["tukar"] = ["gagal_putus"]
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert e.value.error_class == STAGING_GAGAL and "dipulihkan" in e.value.pesan
    assert prod.langkah == ["siapkan", "tukar", "tukar", "pulihkan"]
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    assert _staging(sesi, site_staging).dorong_gagal_pada is None
    assert prod.dorongan == {}


def test_pulihkan_diulang_selama_memulihkan(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.gagal_langkah = "tukar"
    prod.pulih_otomatis_macet = True
    pulih_gagal = _galat(500, "wpmgr_staging_pulihkan", "Sebagian berkas belum dapat dikembalikan.")
    prod.kejadian["pulihkan"] = [pulih_gagal, pulih_gagal]
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert "dipulihkan" in e.value.pesan
    assert prod.langkah == ["siapkan", "tukar", "pulihkan", "pulihkan", "pulihkan"]
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"


def test_pulihkan_berlarut_dilanjutkan_tanpa_tukar_ulang(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.gagal_langkah = "tukar"
    prod.pulih_otomatis_macet = True
    pulih_gagal = _galat(500, "wpmgr_staging_pulihkan", "Sebagian berkas belum dapat dikembalikan.")
    prod.kejadian["pulihkan"] = [pulih_gagal] * (dorong.MAKS_ULANG_PULIHKAN + 1)
    job = _job_baru(sesi, site_staging, "hanya_kode")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.error_class == TRANSIENT
    assert _kemajuan(sesi, job)["langkah_terapkan"] == "pulihkan"
    sebelum = len(prod.langkah)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.error_class == STAGING_GAGAL and "dipulihkan" in e.value.pesan
    assert set(prod.langkah[sebelum:]) == {"pulihkan"}
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"


def test_selesai_dipanggil_ulang_sesudah_tukar(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["selesai"] = [httpx.Response(500, text="galat sementara")] * (umum.ULANG_POTONGAN + 1)
    job = _job_baru(sesi, site_staging, "hanya_kode")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.error_class == TRANSIENT
    assert _kemajuan(sesi, job)["langkah_terapkan"] == "selesai"
    hasil = _jalankan(sesi, site_staging, prod, job)
    assert hasil["dorong_gagal"] is False
    assert prod.langkah.count("tukar") == 1
    assert prod.langkah[-1] == "selesai" and prod.dorongan == {}


# ---- melanjutkan dari setiap tahap ---------------------------------------------


TITIK_PUTUS = [
    ("tahap_dorong", "tanda_air"), ("tahap_dorong", "manifest"), ("tahap_dorong", "rencana"),
    ("tahap_dorong", "snapshot_db"), ("tahap_dorong", "snapshot_berkas"), ("tahap_dorong", "snapshot_catat"),
    ("tahap_dorong", "unggah"), ("unggah_nomor", 1), ("tahap_dorong", "terapkan"),
    ("langkah_terapkan", "impor"), ("langkah_terapkan", "cek_ulang"), ("langkah_terapkan", "tukar"),
    ("langkah_terapkan", "selesai"), ("langkah_terapkan", "beres"), ("tahap_dorong", "cek"),
]


@pytest.mark.parametrize("kunci,nilai", TITIK_PUTUS)
def test_dilanjutkan_dari_setiap_tahap(sesi, site_staging, staging_aktif, prod, pb, monkeypatch, kunci, nilai):
    """Worker mati tepat sesudah kemajuan tahap itu di-commit; job berikutnya menuntaskan dorong yang sama."""
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.tanda_air["sumber"]["comments"] = {"maks_id": 5, "jumlah": 4}
    asli = umum.simpan_kemajuan
    putus = []

    def simpan(sesi_, job_, **perubahan):
        k = asli(sesi_, job_, **perubahan)
        if not putus and perubahan.get(kunci) == nilai:
            putus.append(1)
            raise SiteError(TRANSIENT, "worker mati")
        return k

    monkeypatch.setattr(umum, "simpan_kemajuan", simpan)
    job = _job_baru(sesi, site_staging, "timpa_penuh", "Contoh")
    with pytest.raises(SiteError):
        _jalankan(sesi, site_staging, prod, job)
    assert putus == [1]
    hasil = _jalankan(sesi, site_staging, prod, job)
    _selesaikan(sesi)

    assert hasil["dorong_gagal"] is False
    assert prod.berkas["index.php"][0] == b"<?php // inti diubah di staging"
    assert prod.sql_diterapkan == SQL_EKSPOR
    ids = {i for i, _ in prod.langkah_id}
    assert len(ids) == 1, "hanya satu dorongan yang dipakai"
    assert prod.langkah.count("tukar") == 1 and "pulihkan" not in prod.langkah
    assert prod.langkah[-1] == "selesai"
    assert prod.dorongan == {} and prod.kunci is None
    assert sesi.query(StagingSnapshot).count() == 1
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Dorong ke produksi (timpa penuh)%")).count() == 1
    assert not (staging_aktif / str(site_staging.site_id) / "dorong").exists()


# ---- batal ----------------------------------------------------------------------


def test_batal_sebelum_tukar_membersihkan_tanpa_tukar(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)

    def batal_saat_siapkan(p, n, badan):
        st = sesi.get(Staging, site_staging.id)
        st.batal_diminta_pada = umum.sekarang()
        sesi.commit()

    prod.sebelum["/staging/terapkan"] = batal_saat_siapkan
    with pytest.raises(umum.GalatDibatalkan):
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert prod.langkah == ["siapkan"]
    assert prod.dorongan == {} and prod.kunci is None
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    assert _staging(sesi, site_staging).status == StatusStaging.siap


def test_batal_sesudah_tukar_diabaikan_dan_dorong_dituntaskan(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)

    def batal_saat_tukar(p, n, badan):
        if badan["langkah"] == "tukar":
            st = sesi.get(Staging, site_staging.id)
            st.batal_diminta_pada = umum.sekarang()
            sesi.commit()

    prod.sebelum["/staging/terapkan"] = batal_saat_tukar
    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")
    assert hasil["dorong_gagal"] is False
    assert prod.langkah == ["siapkan", "tukar", "selesai"]
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.batal_diminta_pada is None


def test_batal_sesudah_tukar_tidak_mengalahkan_percobaan_ulang(sesi, site_staging, staging_aktif, prod, pb):
    """Galat sementara sesudah tukar + batal yang tertunda: job diulang dan menuntaskan selesai, bukan batal."""
    _siap(sesi, site_staging, staging_aktif, prod)

    def batal_saat_tukar(p, n, badan):
        if badan["langkah"] == "tukar":
            st = sesi.get(Staging, site_staging.id)
            st.batal_diminta_pada = umum.sekarang()
            sesi.commit()

    prod.sebelum["/staging/terapkan"] = batal_saat_tukar
    prod.kejadian["selesai"] = [httpx.Response(500, text="galat sementara")] * umum.ULANG_POTONGAN
    job = _job_baru(sesi, site_staging, "hanya_kode")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert not isinstance(e.value, umum.GalatDibatalkan)
    assert _staging(sesi, site_staging).status == StatusStaging.mendorong
    assert prod.dorongan[_kemajuan(sesi, job)["dorong_id"]]["status"] == "ditukar"
    hasil = _jalankan(sesi, site_staging, prod, job)
    assert hasil["dorong_gagal"] is False
    assert prod.langkah == ["siapkan", "tukar"] + ["selesai"] * (umum.ULANG_POTONGAN + 1)
    assert prod.dorongan == {}
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.batal_diminta_pada is None


# ---- gagal final (putusan F9a) --------------------------------------------------


def test_gagal_final_membersihkan_produksi_dengan_tenggat_pendek(sesi, site_staging, staging_aktif, prod, pb,
                                                                 monkeypatch):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.gagal_langkah = "siapkan"
    site = sesi.get(Site, site_staging.site_id)
    klien = prod.klien(site)
    tenggat_dipakai = []
    asli = klien.staging_bersihkan

    def catat(dorong_id, tenggat=None):
        tenggat_dipakai.append(tenggat)
        return asli(dorong_id, tenggat=tenggat)

    monkeypatch.setattr(klien, "staging_bersihkan", catat)
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode", klien=klien)
    assert e.value.error_class == STAGING_GAGAL
    assert "tidak cocok dengan rencana" in e.value.pesan
    assert len(tenggat_dipakai) == 1 and 0 < tenggat_dipakai[0] <= dorong.TENGGAT_BERSIHKAN_AKHIR
    assert prod.dorongan == {} and prod.kunci is None
    assert sesi.query(StagingSnapshot).count() == 0
    assert list((staging_aktif / str(site_staging.site_id)).glob("snapshot/j*")) == []
    assert "tukar" not in prod.langkah


def test_gagal_final_bersihkan_yang_gagal_diabaikan(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.gagal_langkah = "siapkan"
    prod.kejadian["bersihkan"] = [httpx.Response(500, text="galat")]
    job = _job_baru(sesi, site_staging, "hanya_kode")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    # Galat aslinya yang dilaporkan, bukan galat pembersihan.
    assert e.value.error_class == STAGING_GAGAL and "tidak cocok dengan rencana" in e.value.pesan
    assert _kemajuan(sesi, job).get("produksi_bersih") is not True


def test_ditahan_ditampilkan_dan_tidak_diulang(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.impor_ditahan = True
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert e.value.error_class == STAGING_DITOLAK
    assert "ditahan" in e.value.pesan and "coba lagi nanti" in e.value.pesan
    assert prod.langkah.count("impor") == 1
    assert "tukar" not in prod.langkah
    assert _staging(sesi, site_staging).status == StatusStaging.siap
    assert prod.dorongan == {}


def test_potongan_hilang_diunggah_ulang(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)

    def hapus_potongan(p, n, badan):
        if badan["langkah"] == "siapkan" and not p.kejadian.get("_sudah"):
            p.kejadian["_sudah"] = [1]
            p.dorongan[badan["dorong_id"]]["potongan"].pop(0)

    prod.sebelum["/staging/terapkan"] = hapus_potongan
    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")
    assert hasil["dorong_gagal"] is False
    assert prod.langkah == ["siapkan", "siapkan", "tukar", "selesai"]
    assert prod.hitung["/staging/unggah"] == 2 * len(prod.unggahan)


def test_berkas_staging_berubah_di_tengah_dorong_ditolak(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    files = _files(staging_aktif, site_staging)

    def ubah(p, n, badan):
        (files / "wp-content/plugins/p/baru.php").write_bytes(b"<?php //BARU")

    prod.sebelum["/staging/snapshot"] = ubah
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert e.value.error_class == STAGING_GAGAL
    assert "wp-content/plugins/p/baru.php" in e.value.pesan and "berubah" in e.value.pesan
    assert "tukar" not in prod.langkah
    assert prod.dorongan == {}


# ---- dorongan lama yang tertinggal ---------------------------------------------


def _job_lama(sesi, site_staging, dorong_id, token, langkah="beres"):
    j = buat_job(sesi, site_staging.site_id, JobType.staging_dorong, {"mode": "hanya_kode", "kemajuan": {
        "dorong_id": dorong_id, "token": token, "unggah_mulai": True, "tahap_dorong": "terapkan",
        "langkah_terapkan": langkah}})
    j.status = JobStatus.failed
    sesi.commit()
    return j


@pytest.mark.parametrize("status,langkah_lama", [
    # Terminal tetapi belum dibersihkan (tabel lama tertinggal): cukup bersihkan.
    ("selesai", []), ("dipulihkan", []),
    # Tukar sudah terjadi: diselesaikan, TIDAK dipulihkan.
    ("ditukar", ["selesai"]),
    # Setengah tertukar atau sedang dipulihkan: selesai ditolak (409 urutan), lalu pulihkan.
    ("memulihkan", ["selesai", "pulihkan"]), ("menukar", ["selesai", "pulihkan"]),
])
def test_dorongan_lama_dituntaskan_sebelum_dorongan_baru(sesi, site_staging, staging_aktif, prod, pb,
                                                         status, langkah_lama):
    _siap(sesi, site_staging, staging_aktif, prod)
    lama_id, token = "a" * 32, "b" * 32
    prod.dorongan[lama_id] = {"status": status, "potongan": {}, "rencana": {"sql": False},
                              "hasil": {"tukar": {"selesai": True, "status": "ditukar"}} if status == "ditukar" else {},
                              "token_hash": hashlib.sha256(token.encode()).hexdigest()}
    prod.kunci = lama_id
    job_lama = _job_lama(sesi, site_staging, lama_id, token)
    mulai = len(prod.diminta)

    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")

    assert hasil["dorong_gagal"] is False
    assert lama_id not in prod.dorongan
    # Dorongan lama dituntaskan SEBELUM dorongan baru membaca produksi.
    baru = prod.diminta[mulai:]
    pertama_baru = next(i for i, (route, _) in enumerate(baru) if route == "/staging/manifest")
    urutan_lama = [i for i, (_, badan) in enumerate(baru) if isinstance(badan, dict)
                   and badan.get("dorong_id") == lama_id]
    assert urutan_lama and max(urutan_lama) < pertama_baru
    assert baru[0][0] == "/staging/bersihkan"
    assert [lk for i, lk in prod.langkah_id if i == lama_id] == langkah_lama
    assert _kemajuan(sesi, job_lama)["produksi_bersih"] is True
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"


def test_dorongan_lama_yang_tidak_bisa_dituntaskan_menolak_dorongan_baru(sesi, site_staging, staging_aktif,
                                                                        prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    lama_id, token = "a" * 32, "b" * 32
    prod.dorongan[lama_id] = {"status": "memulihkan", "potongan": {}, "hasil": {},
                              "token_hash": hashlib.sha256(token.encode()).hexdigest()}
    prod.kunci = lama_id
    prod.kejadian["pulihkan"] = [_galat(403, "wpmgr_staging_token", "Token pemulihan tidak cocok.")]
    job_lama = _job_lama(sesi, site_staging, lama_id, token)
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert e.value.error_class == STAGING_DITOLAK and "belum dapat dituntaskan" in e.value.pesan
    # Dorongan baru tidak memulai apa pun, dan staging tidak ditandai gagal.
    assert not any(route == "/staging/manifest" for route, _ in prod.diminta[-4:])
    assert prod.unggahan == {}
    assert _staging(sesi, site_staging).status == StatusStaging.siap
    assert _kemajuan(sesi, job_lama).get("produksi_bersih") is not True


def test_dorongan_lama_yang_sudah_bersih_tidak_disentuh(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    job_lama = _job_lama(sesi, site_staging, "a" * 32, "b" * 32)
    job_lama.payload = {**job_lama.payload, "kemajuan": {**job_lama.payload["kemajuan"], "produksi_bersih": True}}
    sesi.commit()
    _dorong(sesi, site_staging, prod, "hanya_kode")
    assert not any(isinstance(b, dict) and b.get("dorong_id") == "a" * 32 for _, b in prod.diminta)


# ---- pra-pemeriksaan (R8) dan konfirmasi ---------------------------------------


def test_r8_menolak_komentar_di_sql_staging(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    pb.sql_ekspor = b"CREATE TABLE `wp_posts` (`id` int) /*!50100 PARTITION BY HASH (`id`) */;\n"
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert e.value.error_class == STAGING_DITOLAK
    assert "komentar SQL" in e.value.pesan
    assert prod.unggahan == {} and prod.langkah == []
    assert not any(route == "/staging/snapshot" for route, _ in prod.diminta)
    assert _staging(sesi, site_staging).status == StatusStaging.siap


def test_r8_menolak_partition_di_struktur_tabel_produksi(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.tabel["wp_posts"] = [(b"DROP TABLE IF EXISTS `wp_posts`;\n"
                               b"CREATE TABLE `wp_posts` (`id` int) PARTITION BY HASH (`id`) PARTITIONS 2;\n")]
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert e.value.error_class == STAGING_DITOLAK
    assert "wp_posts" in e.value.pesan and "PARTITION" in e.value.pesan
    assert prod.unggahan == {} and prod.langkah == []
    # Snapshot berkas belum diambil: penolakan terjadi sebelum snapshot berkas dan unggah.
    assert not any(route == "/staging/file" for route, _ in prod.diminta[-5:])
    assert sesi.query(StagingSnapshot).count() == 0


def test_r8_struktur_produksi_tidak_menahan_hanya_kode(sesi, site_staging, staging_aktif, prod, pb):
    """Kembalikan snapshot hanya-kode tidak pernah mengimpor database (Koreksi #13)."""
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.tabel["wp_posts"] = [b"CREATE TABLE `wp_posts` (`id` int) PARTITION BY HASH (`id`);\n"]
    assert _dorong(sesi, site_staging, prod, "hanya_kode")["dorong_gagal"] is False


def test_konfirmasi_diperlukan_bila_data_baru_tidak_bisa_dipastikan(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.posts_diubah_sejak = None
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert e.value.error_class == STAGING_DITOLAK
    assert "tidak dapat dipastikan" in e.value.pesan and "Ketik nama site" in e.value.pesan
    assert prod.langkah == []
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.failed
    sesi.commit()
    # Nama yang salah tetap ditolak; nama yang persis sama diterima.
    with pytest.raises(SiteError):
        _dorong(sesi, site_staging, prod, "timpa_penuh", konfirmasi="contoh")
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.failed
    sesi.commit()
    _dorong(sesi, site_staging, prod, "timpa_penuh", konfirmasi="Contoh")
    assert prod.langkah == ["siapkan", "impor", "tukar", "selesai"]


def test_unggah_terlalu_kecil_ditolak_sebelum_apa_pun(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.info["unggah_terlalu_kecil"] = True
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert e.value.error_class == STAGING_DITOLAK and "post_max_size" in e.value.pesan
    assert prod.unggahan == {}


def test_tanpa_perubahan_tidak_menyentuh_produksi(sesi, site_staging, prod, pb):
    _tarik(sesi, site_staging, prod)
    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")
    assert hasil["berkas"] == 0 and hasil["dorong_gagal"] is False
    assert prod.unggahan == {} and prod.langkah == []
    assert sesi.query(StagingSnapshot).count() == 0
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("%tidak ada perubahan%")).count() == 1


# ---- minor 3/4: pangkas hanya menghitung titik kembali sah, dengan pola nisan ------------


def _snap_dengan_job(sesi, site_staging, staging_aktif, nama, status_job, langkah=None, umur_hari=0):
    from datetime import datetime, timedelta, timezone

    job = buat_job(sesi, site_staging.site_id, JobType.staging_dorong,
                   {"mode": "hanya_kode", "kemajuan": {"langkah_terapkan": langkah} if langkah else {}})
    job.status = status_job
    sesi.commit()
    rel = f"{site_staging.site_id}/snapshot/{nama}"
    (staging_aktif / rel).mkdir(parents=True)
    snap = StagingSnapshot(site_id=site_staging.site_id, job_id=job.id, jenis="sebelum_dorong", status="tersedia",
                           ukuran=1, path=rel, dibuat_pada=datetime.now(timezone.utc) - timedelta(days=umur_hari))
    sesi.add(snap)
    sesi.commit()
    return snap


def test_pangkas_snapshot_hanya_menghitung_titik_kembali_sah(sesi, site_staging, staging_aktif):
    # Tiga titik kembali sah (job sukses) dan dua sisa dorongan yang tidak pernah menukar (lebih baru).
    sah = [_snap_dengan_job(sesi, site_staging, staging_aktif, f"j{i}", JobStatus.success, umur_hari=10 - i)
           for i in range(3)]
    bukan = [_snap_dengan_job(sesi, site_staging, staging_aktif, f"x{i}", JobStatus.failed, umur_hari=i)
             for i in range(2)]
    dihapus = []
    n = dorong.pangkas_snapshot(sesi, site_staging.site_id, 2, hapus=dihapus.append)
    sesi.commit()
    assert n == 1 and dihapus == [sah[0].path], "yang terlama dari titik kembali sah, bukan sisa dorongan baru"
    for s in sah[1:] + bukan:
        sesi.refresh(s)
        assert s.status == "tersedia"
    sesi.refresh(sah[0])
    assert sah[0].status == "dipangkas"


def test_pangkas_snapshot_titik_kembali_lewat_tukar_dihitung_walau_job_belum_sukses(sesi, site_staging, staging_aktif):
    a = _snap_dengan_job(sesi, site_staging, staging_aktif, "j0", JobStatus.failed, langkah="tukar", umur_hari=3)
    b = _snap_dengan_job(sesi, site_staging, staging_aktif, "j1", JobStatus.success, umur_hari=2)
    dihapus = []
    assert dorong.pangkas_snapshot(sesi, site_staging.site_id, 1, hapus=dihapus.append) == 1
    assert dihapus == [a.path] and b.status == "tersedia"


def test_pangkas_snapshot_nisan_rename_lalu_hapus_di_luar_transaksi(sesi, site_staging, staging_aktif):
    lama = _snap_dengan_job(sesi, site_staging, staging_aktif, "j0", JobStatus.success, umur_hari=5)
    baru = _snap_dengan_job(sesi, site_staging, staging_aktif, "j1", JobStatus.success, umur_hari=1)
    (staging_aktif / lama.path / "besar.bin").write_bytes(b"x")
    n, nisan = dorong.pangkas_snapshot_nisan(sesi, site_staging.site_id, 1)
    sesi.commit()
    assert n == 1 and len(nisan) == 1 and nisan[0].startswith(".hapus-")
    # Sesudah commit: direktori asli sudah tidak ada, isinya masih di nisan (belum di-rmtree).
    assert not (staging_aktif / lama.path).exists() and (staging_aktif / nisan[0] / "besar.bin").exists()
    assert (staging_aktif / baru.path).is_dir()
    dorong.hapus_nisan(staging_aktif, nisan[0])
    assert not (staging_aktif / nisan[0]).exists()


def test_dorong_sukses_memangkas_lewat_nisan_tanpa_sisa(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_SNAPSHOT", "1")
    get_settings.cache_clear()
    try:
        _siap(sesi, site_staging, staging_aktif, prod)
        lama = _snap_dengan_job(sesi, site_staging, staging_aktif, "j-lama", JobStatus.success, umur_hari=9)
        _dorong(sesi, site_staging, prod, "hanya_kode")
        sesi.refresh(lama)
        assert lama.status == "dipangkas"
        assert not (staging_aktif / lama.path).exists()
        assert not [n for n in os.listdir(staging_aktif) if n.startswith(".hapus-")]
    finally:
        get_settings.cache_clear()


def test_handler_terdaftar():
    from wpmgr.jobs.handlers import HANDLER

    assert HANDLER[JobType.staging_dorong] is dorong.tangani_staging_dorong


# ---- fix putaran 1: fidelitas snapshot ------------------------------------------

CREATE_MYSQL8 = (b"DROP TABLE IF EXISTS `wp_posts`;\n"
                 b"CREATE TABLE `wp_posts` (`id` int, `t` text COLLATE utf8mb4_0900_ai_ci) "
                 b"DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;\n")


def test_tarik_tetap_menyesuaikan_mariadb_untuk_staging(sesi, site_staging, prod, pb):
    prod.tabel["wp_posts"] = [CREATE_MYSQL8]
    _tarik(sesi, site_staging, prod)
    assert b"utf8mb4_0900_ai_ci" not in pb.sql and b"utf8mb4_unicode_520_ci" in pb.sql


def test_snapshot_menyimpan_sql_produksi_mentah(sesi, site_staging, staging_aktif, prod, pb):
    """Snapshot dipulihkan ke produksi (MySQL 8), bukan ke MariaDB staging: SQL-nya tidak diubah."""
    prod.tabel["wp_posts"] = [CREATE_MYSQL8]
    _siap(sesi, site_staging, staging_aktif, prod)
    _dorong(sesi, site_staging, prod, "timpa_penuh", konfirmasi="Contoh")
    snap = sesi.query(StagingSnapshot).one()
    isi = b"".join(p.read_bytes() for p in sorted((staging_aktif / snap.path / "db").glob("*.sql")))
    # Tabel wp_options (fixture) ikut di snapshot; tabel wp_posts persis mentah di ujungnya.
    assert isi.endswith(CREATE_MYSQL8)


def test_r8_memeriksa_create_produksi_mentah(sesi, site_staging, staging_aktif, prod, pb):
    """Komentar versi MySQL 8 di tengah CREATE ikut diimpor saat Kembalikan dan ditolak ubah() connector."""
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.tabel["wp_posts"] = [b"CREATE TABLE `wp_posts` (`id` int /*!80023 INVISIBLE */);\n"]
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert e.value.error_class == STAGING_DITOLAK
    assert "wp_posts" in e.value.pesan and "komentar SQL" in e.value.pesan
    assert prod.unggahan == {} and sesi.query(StagingSnapshot).count() == 0


# ---- fix putaran 1: penolakan pra-tukar (M1) ------------------------------------


def test_tukar_ditunda_maintenance_diulang_tanpa_pulihkan(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.tolak_tukar = {"wpmgr_staging_maintenance": 1}
    job = _job_baru(sesi, site_staging, "hanya_kode")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.error_class == TRANSIENT and "pemeliharaan" in e.value.pesan
    assert "dipulihkan" not in e.value.pesan
    k = _kemajuan(sesi, job)
    # Produksi belum tersentuh: kembali ke titik sebelum tukar (batal berlaku lagi).
    assert k["langkah_terapkan"] == "cek_ulang"
    assert prod.dorongan[k["dorong_id"]]["status"] == "siap"
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.mendorong and st.galat.count("dilanjutkan otomatis") == 1
    hasil = _jalankan(sesi, site_staging, prod, job)
    assert hasil["dorong_gagal"] is False
    assert prod.langkah == ["siapkan", "tukar", "tukar", "selesai"]


def test_tukar_ditunda_maintenance_final_ditolak_tanpa_gagal(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.tolak_tukar = {"wpmgr_staging_maintenance": 1}
    job = _job_baru(sesi, site_staging, "hanya_kode")
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.error_class == STAGING_DITOLAK and "coba lagi nanti" in e.value.pesan.lower()
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.dorong_gagal_pada is None
    assert "dilanjutkan otomatis" not in st.galat
    assert "pulihkan" not in prod.langkah and prod.dorongan == {}
    assert sesi.query(StagingSnapshot).count() == 0


def test_tukar_ditolak_tabel_lama_tanpa_pulihkan(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.tolak_tukar = {"wpmgr_staging_tabel_lama": 1}
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert e.value.error_class == STAGING_DITOLAK
    assert "Tabel produksi lama" in e.value.pesan and "dipulihkan" not in e.value.pesan
    assert "pulihkan" not in prod.langkah
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.dorong_gagal_pada is None
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    assert prod.dorongan == {} and sesi.query(StagingSnapshot).count() == 0


# ---- fix putaran 1: rekonsiliasi dorongan lama (M2, M3, M9) --------------------


def _dorongan_lama(prod, status, token="b" * 32, **lain):
    lama_id = "a" * 32
    prod.dorongan[lama_id] = {"status": status, "potongan": {}, "rencana": {"sql": False}, "hasil": {},
                              "token_hash": hashlib.sha256(token.encode()).hexdigest(), **lain}
    prod.kunci = lama_id
    return lama_id


def test_dorongan_lama_gangguan_jaringan_menghentikan_dorongan_baru(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    lama_id = _dorongan_lama(prod, "ditukar", hasil={"tukar": {"selesai": True, "status": "ditukar"}})
    job_lama = _job_lama(sesi, site_staging, lama_id, "b" * 32)
    # Job lain yang sudah bersih dan lebih baru dari batas pencarian tidak menutupinya.
    for _ in range(dorong.MAKS_JOB_LAMA + 2):
        j = buat_job(sesi, site_staging.site_id, JobType.staging_dorong, {"mode": "hanya_kode", "kemajuan": {
            "dorong_id": "c" * 32, "unggah_mulai": True, "produksi_bersih": True}})
        j.status = JobStatus.success
        sesi.commit()
    prod.kejadian["bersihkan"] = ["putus_awal"]
    mulai = len(prod.diminta)
    job = _job_baru(sesi, site_staging, "hanya_kode")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.error_class == TRANSIENT
    assert [r for r, _ in prod.diminta[mulai:]] == ["/staging/bersihkan"]
    k = _kemajuan(sesi, job)
    assert "tahap_dorong" not in k and not k.get("lama_dituntaskan")
    assert _staging(sesi, site_staging).status == StatusStaging.mendorong
    hasil = _jalankan(sesi, site_staging, prod, job)
    assert hasil["dorong_gagal"] is False
    assert (lama_id, "selesai") in prod.langkah_id and lama_id not in prod.dorongan
    assert _kemajuan(sesi, job)["lama_dituntaskan"] is True
    assert _kemajuan(sesi, job_lama)["produksi_bersih"] is True
    log = sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Dorongan sebelumnya%diselesaikan%")).one()
    assert log.job_id == job.id


def test_dorongan_lama_dipulihkan_membuang_snapshotnya_dan_mencatat(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    lama_id = _dorongan_lama(prod, "memulihkan")
    job_lama = _job_lama(sesi, site_staging, lama_id, "b" * 32, langkah="pulihkan")
    sesi.add(StagingSnapshot(site_id=site_staging.site_id, job_id=job_lama.id, jenis="sebelum_dorong",
                             status="tersedia", path=f"{site_staging.site_id}/snapshot/j{job_lama.id}"))
    st = _staging(sesi, site_staging)
    st.dorong_gagal_pada = umum.sekarang()
    sesi.commit()
    # Dorongan baru ditolak (butuh konfirmasi) SESUDAH dorongan lama dituntaskan.
    prod.tanda_air["sumber"]["comments"] = {"maks_id": 5, "jumlah": 4}
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert "Ketik nama site" in e.value.pesan
    assert (lama_id, "pulihkan") in prod.langkah_id and lama_id not in prod.dorongan
    # Produksi sudah kembali ke keadaan sebelum dorongan lama: snapshotnya dibuang
    # dan chip merah dimatikan karena halaman utama hidup.
    assert sesi.query(StagingSnapshot).filter(StagingSnapshot.job_id == job_lama.id).count() == 0
    assert _staging(sesi, site_staging).dorong_gagal_pada is None
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Dorongan sebelumnya%dipulihkan%")).count() == 1


def test_dorongan_lama_perlu_pemulihan_dipulihkan(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    lama_id = _dorongan_lama(prod, "terimpor", tabel_old=True)
    _job_lama(sesi, site_staging, lama_id, "b" * 32)
    _dorong(sesi, site_staging, prod, "hanya_kode")
    assert [lk for i, lk in prod.langkah_id if i == lama_id] == ["selesai", "pulihkan"]
    assert lama_id not in prod.dorongan


def test_dorongan_lama_tahan_batal_dianggap_bersih(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    lama_id = _dorongan_lama(prod, "dipulihkan", tahan_batal=True)
    job_lama = _job_lama(sesi, site_staging, lama_id, "b" * 32)
    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")
    assert hasil["dorong_gagal"] is False
    # Area ditahan 24 jam oleh connector, tetapi kuncinya dilepas.
    assert lama_id in prod.dorongan and prod.kunci is None
    assert _kemajuan(sesi, job_lama)["produksi_bersih"] is True


def test_bersihkan_mengulang_selama_lagi(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.bersihkan_lagi = 2
    _dorong(sesi, site_staging, prod, "hanya_kode")
    assert _jumlah(prod, "/staging/bersihkan") == 3
    assert prod.dorongan == {}


# ---- fix putaran 1: gagal final sesudah tukar (M8) -----------------------------


def test_gagal_final_di_selesai(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["selesai"] = [httpx.Response(500, text="galat")] * 10
    job = _job_baru(sesi, site_staging, "hanya_kode")
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.error_class == STAGING_GAGAL and e.value.pesan == dorong.PESAN_AKHIR["selesai"]
    st = _staging(sesi, site_staging)
    assert st.dorong_gagal_pada is not None and st.galat == dorong.PESAN_AKHIR["selesai"]
    assert sesi.query(StagingSnapshot).one().status == "tersedia"
    assert prod.dorongan[_kemajuan(sesi, job)["dorong_id"]]["status"] == "ditukar"


def test_r26_gagal_sesudah_tukar_diulang_walau_percobaan_habis(sesi, site_staging, staging_aktif, prod, pb,
                                                                monkeypatch):
    """R26: tukar sudah dikirim dan connector tak menjawab: job dicoba lagi, bukan final di max_attempts."""
    from datetime import timedelta

    from wpmgr.jobs import queue

    monkeypatch.setattr(queue, "BATAS_PEMULIHAN", timedelta(hours=24))
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["selesai"] = [httpx.Response(500, text="galat")] * 10
    job = _job_baru(sesi, site_staging, "hanya_kode")
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.error_class != STAGING_GAGAL
    assert "tukar_pada" in _kemajuan(sesi, job)
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.mendorong and st.dorong_gagal_pada is None
    assert st.galat.startswith("Terputus, dilanjutkan otomatis")
    assert sesi.query(StagingSnapshot).one().status == "tersedia"
    assert queue.akan_diulang(sesi.get(Job, job.id), TRANSIENT)


def test_gagal_final_di_pulihkan(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.gagal_langkah = "tukar"
    prod.pulih_otomatis_macet = True
    prod.kejadian["pulihkan"] = [_galat(500, "wpmgr_staging_pulihkan", "belum")] * 20
    job = _job_baru(sesi, site_staging, "hanya_kode")
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.pesan == dorong.PESAN_AKHIR["pulihkan"] and "paling cepat 15 menit" in e.value.pesan
    assert _staging(sesi, site_staging).dorong_gagal_pada is not None
    assert sesi.query(StagingSnapshot).one().status == "tersedia"


def test_galat_tak_terduga_sesudah_tukar(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    _siap(sesi, site_staging, staging_aktif, prod)

    def rusak(*a, **kw):
        raise RuntimeError("bug dashboard")

    monkeypatch.setattr(dorong, "_selesai", rusak)
    job = _job_baru(sesi, site_staging, "hanya_kode")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.pesan == dorong.PESAN_AKHIR["selesai"]
    assert isinstance(e.value.__cause__, RuntimeError)
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.dorong_gagal_pada is not None
    assert sesi.query(StagingSnapshot).one().status == "tersedia"
    assert prod.dorongan[_kemajuan(sesi, job)["dorong_id"]]["status"] == "ditukar"


# ---- fix putaran 2 -------------------------------------------------------------


def test_rekonsiliasi_lama_gagal_final_tidak_menandai_staging_gagal(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    lama_id = _dorongan_lama(prod, "ditukar", hasil={"tukar": {"selesai": True, "status": "ditukar"}})
    job_lama = _job_lama(sesi, site_staging, lama_id, "b" * 32)
    prod.kejadian["bersihkan"] = ["putus_awal"] * 5
    job = _job_baru(sesi, site_staging, "hanya_kode")
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    assert e.value.error_class == STAGING_DITOLAK and "coba lagi nanti" in e.value.pesan.lower()
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.galat == e.value.pesan
    assert st.dorong_gagal_pada is None
    assert prod.unggahan == {} and prod.dorongan[lama_id]["status"] == "ditukar"
    assert not _kemajuan(sesi, job_lama).get("produksi_bersih")


def test_rekonsiliasi_lama_yang_terputus_tetap_kandidat(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    """Penanda bersih, baris snapshot, dan log aktivitas dorongan lama di-commit bersama."""
    _siap(sesi, site_staging, staging_aktif, prod)
    lama_id = _dorongan_lama(prod, "memulihkan")
    job_lama = _job_lama(sesi, site_staging, lama_id, "b" * 32, langkah="pulihkan")
    sesi.add(StagingSnapshot(site_id=site_staging.site_id, job_id=job_lama.id, jenis="sebelum_dorong",
                             status="tersedia", path=f"{site_staging.site_id}/snapshot/j{job_lama.id}"))
    sesi.commit()

    def mati(*a, **kw):
        raise RuntimeError("worker mati")

    monkeypatch.setattr(umum, "catat_aktivitas", mati)
    with pytest.raises(RuntimeError):
        _jalankan(sesi, site_staging, prod, _job_baru(sesi, site_staging, "hanya_kode"))
    sesi.rollback()
    assert not _kemajuan(sesi, job_lama).get("produksi_bersih")
    assert sesi.query(StagingSnapshot).filter(StagingSnapshot.job_id == job_lama.id).count() == 1


def test_urutan_pra_tukar_tidak_mengaku_dipulihkan(sesi, site_staging, staging_aktif, prod, pb):
    """409 urutan saat status masih terimpor (mis. tabel hasil impor hilang): produksi tidak pernah diubah."""
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.tolak_tukar = {"wpmgr_staging_urutan": 1}
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert e.value.error_class == STAGING_GAGAL
    assert "sudah dipulihkan" not in e.value.pesan and "tidak pernah diubah" in e.value.pesan
    assert prod.langkah == ["siapkan", "impor", "tukar", "pulihkan"]
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    assert _staging(sesi, site_staging).dorong_gagal_pada is None


def test_pulihkan_pra_tukar_menolak_token_tidak_sah(prod):
    """Tiruan setia connector: pulihkan pra-tukar tanpa token sah ditolak 400 (token_sah)."""
    prod.dorongan["a" * 32] = {"status": "siap", "potongan": {}, "hasil": {}}
    r = prod._terapkan_inti({"dorong_id": "a" * 32, "langkah": "pulihkan", "token": "bukan-token"}, "pulihkan")
    assert r.status_code == 400 and r.json()["code"] == "wpmgr_staging_permintaan"
    assert prod.dorongan["a" * 32]["status"] == "siap"


# ---- Task 17: putusan R18/R19/R20 (asal status gagal) ----------------------------


def _tarik_gagal(sesi, site_staging, prod, pb) -> str:
    """Tarik (segarkan) sungguhan yang gagal final di tengah: `gagal` milik salinan staging."""
    from wpmgr.staging.pembantu import GalatPembantu

    pb.gagal["db_impor"] = GalatPembantu("impor", "Impor database staging gagal.")
    with pytest.raises(SiteError):
        _tarik(sesi, site_staging, prod)
    del pb.gagal["db_impor"]
    _selesaikan_gagal(sesi)
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.gagal_asal == umum.ASAL_SALINAN
    return st.galat


def _selesaikan_gagal(sesi):
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.failed
    sesi.commit()


def test_dorong_ditolak_bila_tarik_terakhir_gagal(sesi, site_staging, staging_aktif, prod, pb):
    """R19: salinan staging yang setengah disegarkan tidak pernah didorong ke produksi."""
    _siap(sesi, site_staging, staging_aktif, prod)
    galat = _tarik_gagal(sesi, site_staging, prod, pb)
    sebelum, panggilan = len(prod.diminta), len(pb.panggilan)
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh", konfirmasi="Contoh")
    assert e.value.error_class == STAGING_DITOLAK and e.value.pesan == dorong.PESAN_SALINAN_GAGAL
    assert isinstance(e.value, umum.GalatDitolakTanpaUbah)
    assert len(prod.diminta) == sebelum and prod.langkah == []
    # Tidak ada satu pun panggilan skrip pembantu (termasuk ekspor wp-cli).
    assert pb.panggilan[panggilan:] == []
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.galat == galat and st.gagal_asal == umum.ASAL_SALINAN
    assert sesi.query(StagingSnapshot).count() == 0


def test_dorong_boleh_sesudah_tarik_gagal_lalu_tarik_sukses(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    _tarik_gagal(sesi, site_staging, prod, pb)
    _tarik(sesi, site_staging, prod)
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.gagal_asal is None and st.galat is None
    _ubah_staging(_files(staging_aktif, site_staging))
    assert _dorong(sesi, site_staging, prod, "hanya_kode")["dorong_gagal"] is False


def test_dorong_gagal_sebelum_tukar_tidak_menandai_staging_gagal(sesi, site_staging, staging_aktif, prod, pb):
    """R20: gagal final dorong sebelum tukar (unggah terputus terus) mengembalikan status staging sebelumnya."""
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["unggah"] = ["putus_awal"] * 20
    job = _job_baru(sesi, site_staging, "hanya_kode")
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job)
    _selesaikan_gagal(sesi)
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.gagal_asal is None
    # Pesan kegagalannya tetap terlihat di staging.galat.
    assert st.galat == e.value.pesan
    # F9a: area dorong di produksi tetap dibersihkan.
    assert _kemajuan(sesi, job).get("produksi_bersih") is True
    prod.kejadian.pop("unggah")
    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")
    assert hasil["dorong_gagal"] is False
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"


def test_dorong_gagal_sesudah_tukar_uji_ditolak_dorong_tetap_boleh(sesi, site_staging, staging_aktif, prod, pb):
    """Skenario reviewer: dorong gagal sesudah tukar, uji ditolak sebelum mulai, dorong tetap boleh."""
    from wpmgr.staging import uji

    _siap(sesi, site_staging, staging_aktif, prod)
    prod.kejadian["tukar"] = ["putus"] * (dorong.MAKS_RAGU_TUKAR + 1)
    job = _job_baru(sesi, site_staging, "hanya_kode")
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError):
        _jalankan(sesi, site_staging, prod, job)
    _selesaikan_gagal(sesi)
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.gagal_asal == umum.ASAL_PRODUKSI

    site = sesi.get(Site, site_staging.site_id)
    job_uji = buat_job(sesi, site.id, JobType.staging_uji_update, {"paket": "bukan daftar"})
    with pytest.raises(SiteError):
        uji.tangani_staging_uji_update(sesi, job_uji, prod.klien(site))
    _selesaikan_gagal(sesi)
    assert _staging(sesi, site_staging).gagal_asal == umum.ASAL_PRODUKSI

    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")
    assert hasil["dorong_gagal"] is False
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.gagal_asal is None and st.galat is None


def test_dorong_ditolak_selama_menyalin_atau_uji(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    for status in (StatusStaging.menyalin, StatusStaging.berjalan_uji):
        st = _staging(sesi, site_staging)
        st.status = status
        sesi.commit()
        sebelum = len(prod.diminta)
        with pytest.raises(SiteError) as e:
            _dorong(sesi, site_staging, prod, "hanya_kode")
        _selesaikan_gagal(sesi)
        assert isinstance(e.value, umum.GalatDitolakTanpaUbah) and e.value.pesan == dorong.PESAN_SALINAN_SIBUK
        assert len(prod.diminta) == sebelum
        assert _staging(sesi, site_staging).status == status


def test_dorong_tolak_pra_tukar_tanpa_gagal_staging_tetap_siap(sesi, site_staging, staging_aktif, prod, pb):
    _siap(sesi, site_staging, staging_aktif, prod)
    prod.tolak_tukar = {"wpmgr_staging_tabel_lama": 1}
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.galat == e.value.pesan and st.gagal_asal is None


# ---- putusan R21: penolakan tarik sebelum salinan disentuh ----------------------


def _status_pembantu(pb, **ubah):
    from dataclasses import replace

    pb.status_palsu = replace(pb.status_palsu, **ubah)


def test_tarik_ditolak_disk_tidak_menandai_staging_yang_sudah_ditarik(sesi, site_staging, staging_aktif, prod, pb):
    """R21: tarik ditolak (disk) sebelum salinan disentuh; staging tetap siap tanpa penanda, dorong boleh."""
    _siap(sesi, site_staging, staging_aktif, prod)
    asli = pb.status_palsu
    _status_pembantu(pb, disk_total=100 * GB, disk_bebas=10 * GB)
    with pytest.raises(SiteError) as e:
        _tarik(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_DITOLAK and "Sisa disk" in e.value.pesan
    _selesaikan_gagal(sesi)
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.siap and st.gagal_asal is None and st.galat == e.value.pesan
    pb.status_palsu = asli
    assert _dorong(sesi, site_staging, prod, "hanya_kode")["dorong_gagal"] is False


def test_tarik_ditolak_ram_staging_dijeda_tetap_dijeda(sesi, site_staging, staging_aktif, prod, pb):
    """R21: staging dijeda yang ditarik ulang dan ditolak karena RAM tetap dijeda, tanpa penanda."""
    _siap(sesi, site_staging, staging_aktif, prod)
    st = _staging(sesi, site_staging)
    st.status, st.aktif = StatusStaging.dijeda, False
    sesi.commit()
    _status_pembantu(pb, mem_tersedia=GB)
    with pytest.raises(SiteError) as e:
        _tarik(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_DITOLAK and "RAM" in e.value.pesan
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.dijeda and st.gagal_asal is None


def test_tarik_ditolak_batas_aktif_staging_dijeda_tetap_dijeda(sesi, site_staging, staging_aktif, prod, pb,
                                                               monkeypatch):
    _siap(sesi, site_staging, staging_aktif, prod)
    st = _staging(sesi, site_staging)
    st.status, st.aktif = StatusStaging.dijeda, False
    sesi.commit()
    monkeypatch.setattr(tarik, "jumlah_aktif", lambda sesi, staging: 99)
    with pytest.raises(SiteError) as e:
        _tarik(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_DITOLAK
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.dijeda and st.gagal_asal is None


def test_tarik_ditolak_pada_staging_belum_pernah_ditarik(sesi, site_staging, staging_aktif, prod, pb):
    """R21: staging yang belum pernah ditarik tetap gagal dengan pesan penolakan; dorong menolak (belum ditarik)."""
    _status_pembantu(pb, mem_tersedia=GB)
    with pytest.raises(SiteError) as e:
        _tarik(sesi, site_staging, prod)
    _selesaikan_gagal(sesi)
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.galat == e.value.pesan and st.ditarik_pada is None
    with pytest.raises(SiteError) as e2:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert e2.value.error_class == STAGING_DITOLAK and e2.value.pesan == dorong.PESAN_BELUM_TARIK


def test_tarik_ditolak_sesudah_salinan_disentuh_tetap_menandai_salinan(sesi, site_staging, staging_aktif, prod, pb):
    """Tarik yang dilanjutkan sesudah mulai menulis files/ lalu ditolak: salinan setengah jadi -> gagal 'salinan'."""
    _siap(sesi, site_staging, staging_aktif, prod)
    st = _staging(sesi, site_staging)
    st.aktif = False
    sesi.commit()
    job = buat_job(sesi, site_staging.site_id, JobType.staging_tarik, {"kemajuan": {"tahap": "berkas"}})
    job.attempts = job.max_attempts
    sesi.commit()
    _status_pembantu(pb, mem_tersedia=GB)
    site = sesi.get(Site, site_staging.site_id)
    with pytest.raises(SiteError):
        tarik.tangani_staging_tarik(sesi, job, prod.klien(site))
    st = _staging(sesi, site_staging)
    assert st.status == StatusStaging.gagal and st.gagal_asal == umum.ASAL_SALINAN


# ---- putusan R22: 'salinan' menang; tarik/uji yang dibatalkan ------------------------


def _tarik_dibatalkan(sesi, site_staging, prod, route: str, ke: int) -> Job:
    """Tarik yang dibatalkan pengguna saat permintaan `route` ke-`ke` ke produksi."""
    def minta_batal(p, n, badan):
        if n == ke:
            st = sesi.get(Staging, site_staging.id)
            st.batal_diminta_pada = umum.sekarang()
            sesi.commit()

    prod.hitung.clear()
    prod.sebelum[route] = minta_batal
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    with pytest.raises(umum.GalatDibatalkan):
        tarik.tangani_staging_tarik(sesi, job, prod.klien(site))
    del prod.sebelum[route]
    _selesaikan_gagal(sesi)
    return job


def _dorong_ditolak_salinan(sesi, site_staging, prod):
    sebelum = len(prod.diminta)
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    _selesaikan_gagal(sesi)
    assert isinstance(e.value, umum.GalatDitolakTanpaUbah) and e.value.pesan == dorong.PESAN_SALINAN_GAGAL
    assert len(prod.diminta) == sebelum


def test_r22_tarik_gagal_lalu_tarik_baru_dibatalkan_segera_tetap_salinan(sesi, site_staging, staging_aktif,
                                                                         prod, pb):
    """Skenario A: batal sebelum salinan disentuh mengembalikan gagal 'salinan' beserta galat aslinya."""
    _siap(sesi, site_staging, staging_aktif, prod)
    galat = _tarik_gagal(sesi, site_staging, prod, pb)
    st = _staging(sesi, site_staging)
    st.batal_diminta_pada = umum.sekarang()
    sesi.commit()
    with pytest.raises(umum.GalatDibatalkan):
        _tarik(sesi, site_staging, prod)
    _selesaikan_gagal(sesi)
    st = _staging(sesi, site_staging)
    assert (st.status, st.gagal_asal, st.galat) == (StatusStaging.gagal, umum.ASAL_SALINAN, galat)
    assert st.batal_diminta_pada is None
    _dorong_ditolak_salinan(sesi, site_staging, prod)


def test_r22_tarik_dibatalkan_saat_berkas_menandai_salinan(sesi, site_staging, staging_aktif, prod, pb):
    """Skenario B: tarik pada staging yang baik dibatalkan saat `berkas` -> gagal 'salinan', dorong ditolak."""
    _siap(sesi, site_staging, staging_aktif, prod)
    job = _tarik_dibatalkan(sesi, site_staging, prod, "/staging/manifest", ke=2)
    assert _kemajuan(sesi, job)["tahap"] == "berkas"
    st = _staging(sesi, site_staging)
    assert (st.status, st.gagal_asal) == (StatusStaging.gagal, umum.ASAL_SALINAN)
    assert st.galat == umum.PESAN_BATAL_TENGAH
    _dorong_ditolak_salinan(sesi, site_staging, prod)


def test_r22_tarik_dibatalkan_saat_manifest_tetap_siap(sesi, site_staging, staging_aktif, prod, pb):
    """Batal di tahap manifest: salinan belum disentuh, staging tetap siap tanpa penanda, dorong boleh."""
    _siap(sesi, site_staging, staging_aktif, prod)
    job = _tarik_dibatalkan(sesi, site_staging, prod, "/staging/manifest", ke=1)
    assert _kemajuan(sesi, job)["tahap"] == "manifest"
    st = _staging(sesi, site_staging)
    assert (st.status, st.gagal_asal) == (StatusStaging.siap, None)
    assert _dorong(sesi, site_staging, prod, "hanya_kode")["dorong_gagal"] is False
