import errno
import hashlib
import os
import shutil
import uuid
from datetime import datetime, timezone

import pytest
from staging_palsu import GB, PembantuPalsu, ProduksiPalsu

from wpmgr.errors import STAGING_DITOLAK, STAGING_GAGAL, TRANSIENT, SiteError
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import buat_job
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
from wpmgr.staging import rencana, tarik, umum
from wpmgr.staging.aman import PathTidakAman
from wpmgr.staging.pembantu import GalatPembantu, StatusPembantu

pytestmark = pytest.mark.integration

MTIME = 1_700_000_000


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))


@pytest.fixture
def prod():
    p = ProduksiPalsu()
    p.berkas = {
        "index.php": (b"<?php // indeks", MTIME),
        "wp-content/themes/t/style.css": (b"body{}", MTIME),
        "wp-content/uploads/besar.bin": (bytes(range(256)) * 10, MTIME),
    }
    p.tabel = {
        "wp_posts": [b"DROP TABLE IF EXISTS `wp_posts`;\nCREATE TABLE `wp_posts` (`id` int);\n",
                     b"INSERT INTO `wp_posts` (`id`) VALUES ('1');\n"],
        "wp_options": [b"DROP TABLE IF EXISTS `wp_options`;\nCREATE TABLE `wp_options` (`a` text);\n"],
    }
    return p


@pytest.fixture
def pb(staging_aktif, monkeypatch):
    palsu = PembantuPalsu(staging_aktif)
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


@pytest.fixture(autouse=True)
def potongan_kecil(monkeypatch):
    # Berkas besar.bin (2560 byte) diambil lewat rentang 1000 byte.
    monkeypatch.setattr(rencana, "UKURAN_PAKET", 1000)
    monkeypatch.setattr(tarik, "UKURAN_PAKET", 1000)


def _jalankan(sesi, site_staging, prod, job=None):
    """Jalankan handler langsung; job yang selesai ditandai sukses supaya job staging berikutnya boleh dibuat."""
    site = sesi.get(Site, site_staging.site_id)
    job = job or buat_job(sesi, site.id, JobType.staging_tarik)
    hasil = tarik.tangani_staging_tarik(sesi, job, prod.klien(site))
    job.status = JobStatus.success
    sesi.commit()
    return job, hasil


def _gagalkan_job_tertunda(sesi):
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.failed
    sesi.commit()


def _files(staging_aktif, site_staging):
    return staging_aktif / str(site_staging.site_id) / "files"


def _paths_diminta(prod):
    return [p for route, b in prod.diminta if route == "/staging/file" and "berkas" in b for p in b["berkas"]]


def _rentang_diminta(prod, path=None):
    return [b["rentang"] for route, b in prod.diminta
            if route == "/staging/file" and "rentang" in b and (path is None or b["rentang"]["path"] == path)]


def test_handler_terdaftar():
    assert handlers.HANDLER[JobType.staging_tarik] is tarik.tangani_staging_tarik


def test_tarik_penuh_membuat_staging(sesi, site_staging, staging_aktif, prod, pb):
    _, hasil = _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    assert (files / "index.php").read_bytes() == b"<?php // indeks"
    assert (files / "wp-content/uploads/besar.bin").read_bytes() == bytes(range(256)) * 10
    assert int(os.stat(files / "index.php").st_mtime) == MTIME
    assert int(os.stat(files / "wp-content/uploads/besar.bin").st_mtime) == MTIME
    mu = (files / "wp-content/mu-plugins/wpmgr-staging.php").read_text(encoding="utf-8")
    assert "define( 'WPMGR_STAGING_NAMA', 'contoh-test' );" in mu
    assert [(x["dari"], x["panjang"]) for x in _rentang_diminta(prod)] == [(0, 1000), (1000, 1000), (2000, 1000)]

    assert pb.sql.startswith(b"SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;")
    assert pb.sql.index(b"CREATE TABLE `wp_options`") < pb.sql.index(b"CREATE TABLE `wp_posts`")
    assert pb.sql.index(b"CREATE TABLE `wp_posts`") < pb.sql.index(b"INSERT INTO `wp_posts`")
    assert ("db_buat", "contoh-test", str(site_staging.site_id), "wp_") in pb.panggilan
    assert ("buat", "contoh-test", "8.1", str(site_staging.site_id)) in pb.panggilan
    url = "https://contoh-test.staging.contoh.id"
    assert ("wpcli", "contoh-test", "search-replace", "https://contoh.test", url) in pb.panggilan
    assert ("wpcli", "contoh-test", "search-replace", "http://contoh.test", url) in pb.panggilan
    assert ("wpcli", "contoh-test", "option", "update", "blog_public", "0") in pb.panggilan
    assert ("wpcli", "contoh-test", "cache", "flush") in pb.panggilan
    assert pb.nama_panggilan().index("db_buat") < pb.nama_panggilan().index("db_impor")
    assert pb.nama_panggilan().index("router_muat") < pb.nama_panggilan().index("buat")
    assert pb.nama_panggilan()[-1] == "sertifikat"

    router = staging_aktif / "router"
    assert (router / "contoh-test.rahasia").read_bytes() == b"e" * 64
    assert (router / "contoh-test.htpasswd").read_bytes().startswith(b"staging:$2b$")

    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap
    assert site_staging.aktif is True
    assert site_staging.galat is None
    assert site_staging.versi_php == "8.1"
    assert site_staging.ditarik_pada is not None and site_staging.sertifikat_pada is not None
    assert site_staging.tanda_air["sumber"]["comments"] == {"maks_id": 3, "jumlah": 2}
    assert site_staging.tanda_air["sumber"]["pesanan_posts"] == {"maks_id": 0, "jumlah": 0}
    assert site_staging.tanda_air["sumber"]["posts"]["diubah_sejak"] is None
    assert site_staging.ukuran_file == len(b"<?php // indeks") + len(b"body{}") + 2560
    assert not (staging_aktif / str(site_staging.site_id) / "tarik").exists()
    assert hasil["byte_disalin"] == site_staging.ukuran_file
    log = sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Staging dibuat%")).one()
    assert log.detail["byte_disalin"] == site_staging.ukuran_file


def test_segarkan_inkremental(sesi, site_staging, staging_aktif, prod, pb):
    _jalankan(sesi, site_staging, prod)
    prod.diminta.clear()
    prod.berkas["index.php"] = (b"<?php // berubah", MTIME + 5)
    del prod.berkas["wp-content/themes/t/style.css"]
    prod.berkas["wp-content/uploads/baru.jpg"] = (b"jpg", MTIME)
    _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    assert sorted(_paths_diminta(prod)) == ["index.php", "wp-content/uploads/baru.jpg"]
    assert _rentang_diminta(prod) == []
    assert (files / "index.php").read_bytes() == b"<?php // berubah"
    assert not (files / "wp-content/themes/t/style.css").exists()
    assert (files / "wp-config.php").exists()
    assert (files / "wp-content/mu-plugins/wpmgr-staging.php").exists()
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Staging disegarkan%")).count() == 1
    sesi.refresh(site_staging)
    # Berkas milik staging sendiri (mu-plugin, wp-config) tidak ikut dihitung.
    assert site_staging.ukuran_file == len(b"<?php // berubah") + 3 + 2560


def test_segarkan_mengembalikan_perubahan_di_staging(sesi, site_staging, staging_aktif, prod, pb):
    """F3: tarik baru memindai files/ dulu, jadi suntingan di staging dikembalikan ke produksi."""
    _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    (files / "index.php").write_bytes(b"<?php // disunting di staging")
    (files / "wp-content/plugins/baru").mkdir(parents=True)
    (files / "wp-content/plugins/baru/baru.php").write_bytes(b"<?php // plugin staging")
    prod.diminta.clear()
    _jalankan(sesi, site_staging, prod)
    assert (files / "index.php").read_bytes() == b"<?php // indeks"
    assert not (files / "wp-content/plugins/baru/baru.php").exists()
    assert _paths_diminta(prod) == ["index.php"]


def test_segarkan_mengembalikan_berkas_besar_yang_disunting(sesi, site_staging, staging_aktif, prod, pb):
    _jalankan(sesi, site_staging, prod)
    besar = _files(staging_aktif, site_staging) / "wp-content/uploads/besar.bin"
    besar.write_bytes(bytes(2560))
    prod.diminta.clear()
    _jalankan(sesi, site_staging, prod)
    assert besar.read_bytes() == bytes(range(256)) * 10
    assert len(_rentang_diminta(prod)) == 3


def test_lanjut_setelah_putus_di_tengah(sesi, site_staging, staging_aktif, prod, pb):
    prod.halaman = 10
    prod.berkas.update({f"wp-content/uploads/{i}.txt": (b"x" * 600, MTIME) for i in range(3)})
    prod.jadwal_gagal["/staging/file"] = {2, 3, 4}
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == TRANSIENT
    job = sesi.query(Job).one()
    assert job.payload["kemajuan"]["tahap"] == "berkas"
    assert job.payload["kemajuan"]["byte_selesai"] > 0
    sesi.refresh(site_staging)
    # attempts 0 < max_attempts: kegagalan TRANSIENT diulang, staging tetap menyalin.
    assert site_staging.status == StatusStaging.menyalin
    assert site_staging.galat.startswith("Terputus, dilanjutkan otomatis: ")
    assert (staging_aktif / str(site_staging.site_id) / "tarik" / "manifest.jsonl").exists()
    _jalankan(sesi, site_staging, prod, job=job)
    diminta = _paths_diminta(prod)
    assert len(diminta) == len(set(diminta))
    assert (_files(staging_aktif, site_staging) / "wp-content/uploads/2.txt").read_bytes() == b"x" * 600
    assert sesi.query(Job).one().payload["kemajuan"]["byte_selesai"] == 15 + 6 + 2560 + 3 * 600


def test_tarik_produksi_berubah_di_tengah(sesi, site_staging, staging_aktif, prod, pb):
    prod.berkas["wp-content/uploads/hilang.txt"] = (b"akan dihapus", MTIME)

    def ubah_sebelum_paket(p, n, badan):
        if "berkas" in badan:
            p.berkas["index.php"] = (b"<?php // diubah setelah manifest", MTIME + 9)
            p.berkas.pop("wp-content/uploads/hilang.txt", None)
        elif badan["rentang"]["dari"] == 1000 and not getattr(p, "_sudah", False):
            p._sudah = True
            isi, _ = p.berkas["wp-content/uploads/besar.bin"]
            p.berkas["wp-content/uploads/besar.bin"] = (isi[::-1], MTIME + 1)

    prod.sebelum["/staging/file"] = ubah_sebelum_paket
    _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    assert (files / "index.php").read_bytes() == b"<?php // diubah setelah manifest"
    assert int(os.stat(files / "index.php").st_mtime) == MTIME + 9
    assert not (files / "wp-content/uploads/hilang.txt").exists()
    assert (files / "wp-content/uploads/besar.bin").read_bytes() == (bytes(range(256)) * 10)[::-1]
    # Rentang dimulai ulang dari awal setelah berkas berubah di tengah.
    assert [x["dari"] for x in _rentang_diminta(prod)] == [0, 1000, 0, 1000, 2000]
    indeks = tarik.Indeks(staging_aktif / str(site_staging.site_id) / "indeks.jsonl").muat()
    assert indeks["index.php"].hash == hashlib.sha256(b"<?php // diubah setelah manifest").hexdigest()
    assert "wp-content/uploads/hilang.txt" not in indeks
    assert not list(files.rglob(".wpmgr-*"))


def test_berkas_dihapus_setelah_manifest_ikut_dihapus_di_staging(sesi, site_staging, staging_aktif, prod, pb):
    prod.berkas["wp-content/uploads/lama.txt"] = (b"versi 1", MTIME)
    _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    assert (files / "wp-content/uploads/lama.txt").exists()
    prod.berkas["wp-content/uploads/lama.txt"] = (b"versi 2", MTIME + 1)

    def hapus_sesudah_manifest(p, n, badan):
        p.berkas.pop("wp-content/uploads/lama.txt", None)

    prod.sebelum["/staging/file"] = hapus_sesudah_manifest
    _jalankan(sesi, site_staging, prod)
    assert "wp-content/uploads/lama.txt" in _paths_diminta(prod)
    assert not (files / "wp-content/uploads/lama.txt").exists()
    indeks = tarik.Indeks(staging_aktif / str(site_staging.site_id) / "indeks.jsonl").muat()
    assert "wp-content/uploads/lama.txt" not in indeks


def test_berkas_besar_yang_terus_berubah_dilewati_dengan_peringatan(sesi, site_staging, staging_aktif, prod, pb):
    def ubah_terus(p, n, badan):
        if "rentang" in badan and badan["rentang"]["dari"] == 1000:
            isi, mtime = p.berkas["wp-content/uploads/besar.bin"]
            p.berkas["wp-content/uploads/besar.bin"] = (isi, mtime + 1)

    prod.sebelum["/staging/file"] = ubah_terus
    _, hasil = _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    assert not (files / "wp-content/uploads/besar.bin").exists()
    assert len(_rentang_diminta(prod)) == 3 * 2
    assert any("wp-content/uploads/besar.bin" in p and "terus berubah" in p for p in hasil["peringatan"])
    assert not list(files.rglob(".wpmgr-*"))
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap


def test_berkas_besar_hash_tidak_cocok_diulang(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    """Isi berubah di tengah dengan ukuran dan mtime sama: verifikasi hash memulai ulang."""
    asli = bytes(range(256)) * 10
    prod.berkas["wp-content/uploads/besar.bin"] = (asli, MTIME)

    def tukar_sekali(p, n, badan):
        if "rentang" in badan and badan["rentang"]["dari"] == 2000 and not getattr(p, "_sudah", False):
            p._sudah = True
            p.berkas["wp-content/uploads/besar.bin"] = (asli[:2000] + bytes(560), MTIME)

    def pulihkan(p, n, badan):
        tukar_sekali(p, n, badan)
        if "rentang" in badan and badan["rentang"]["dari"] == 0 and getattr(p, "_sudah", False):
            p.berkas["wp-content/uploads/besar.bin"] = (asli, MTIME)

    prod.sebelum["/staging/file"] = pulihkan
    _jalankan(sesi, site_staging, prod)
    assert (_files(staging_aktif, site_staging) / "wp-content/uploads/besar.bin").read_bytes() == asli
    assert [x["dari"] for x in _rentang_diminta(prod)] == [0, 1000, 2000, 0, 1000, 2000]


def test_paket_tidak_lengkap_diminta_lagi_dan_penanda(sesi, site_staging, staging_aktif, prod, pb):
    prod.halaman = 10
    prod.maks_entri = 1
    prod.maks_paket = 1000
    prod.berkas["wp-content/uploads/rusak.txt"] = (b"tidak terbaca", MTIME)
    prod.gagal_baca.add("wp-content/uploads/rusak.txt")
    prod.berkas["wp-content/uploads/tumbuh.txt"] = (b"t" * 900, MTIME)

    def tumbuh(p, n, badan):
        if "berkas" in badan and badan["berkas"][0] == "wp-content/uploads/tumbuh.txt":
            p.berkas["wp-content/uploads/tumbuh.txt"] = (b"T" * 1100, MTIME + 3)

    prod.sebelum["/staging/file"] = tumbuh
    _, hasil = _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    # maks_entri=1: setiap paket hanya mengirim entri pertama (lengkap:false);
    # sisanya diminta lagi, tanpa ada path yang diminta sebagai entri pertama dua kali.
    pertama = [b["berkas"][0] for route, b in prod.diminta if route == "/staging/file" and "berkas" in b]
    assert len(pertama) == len(set(pertama))
    assert (files / "index.php").read_bytes() == b"<?php // indeks"
    assert (files / "wp-content/themes/t/style.css").read_bytes() == b"body{}"
    assert not (files / "wp-content/uploads/rusak.txt").exists()
    assert any("wp-content/uploads/rusak.txt" in p for p in hasil["peringatan"])
    # Penanda terlalu_besar: berkas tumbuh sejak manifest, diambil lewat rentang.
    assert (files / "wp-content/uploads/tumbuh.txt").read_bytes() == b"T" * 1100
    assert [x["dari"] for x in _rentang_diminta(prod, "wp-content/uploads/tumbuh.txt")] == [0, 1000]


def test_manifest_halaman_kosong_tetap_dilanjutkan(sesi, site_staging, staging_aktif, prod, pb):
    prod.halaman_kosong = 2
    _jalankan(sesi, site_staging, prod)
    manifest = [b for route, b in prod.diminta if route == "/staging/manifest"]
    assert [m.get("kursor") for m in manifest[:3]] == [None, "0", "00"]
    assert (_files(staging_aktif, site_staging) / "index.php").exists()
    assert (_files(staging_aktif, site_staging) / "wp-content/uploads/besar.bin").exists()


def test_anggaran_byte_dari_manifest(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    monkeypatch.setattr(tarik, "TOLERANSI_BYTE", 0)

    def bengkak(p, n, badan):
        if "berkas" in badan:
            p.berkas["index.php"] = (b"i" * 999, MTIME)
            p.berkas["wp-content/themes/t/style.css"] = (b"s" * 999, MTIME)

    prod.sebelum["/staging/file"] = bengkak
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_GAGAL
    assert "melebihi" in e.value.pesan
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal


def test_tarik_disk_habis_gagal_jelas_dan_bisa_dilanjutkan(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    asli = tarik.tulis_berkas
    tulisan = []

    def tulis_lalu_penuh(files, path, isi, mtime):
        tulisan.append(path)
        if len(tulisan) == 2:
            raise OSError(errno.ENOSPC, "No space left on device", str(files / path))
        asli(files, path, isi, mtime)

    monkeypatch.setattr(tarik, "tulis_berkas", tulis_lalu_penuh)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_GAGAL
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal
    assert "Disk VPS penuh" in site_staging.galat
    assert str(staging_aktif) not in site_staging.galat
    # Kegagalan final: area kerja tarik/ dibersihkan (M1).
    assert not (staging_aktif / str(site_staging.site_id) / "tarik").exists()
    pertama = tulisan[0]
    monkeypatch.setattr(tarik, "tulis_berkas", asli)
    _gagalkan_job_tertunda(sesi)
    prod.diminta.clear()
    _jalankan(sesi, site_staging, prod)
    assert pertama not in _paths_diminta(prod)
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap


def test_tarik_nama_non_ascii_dan_panjang(sesi, site_staging, staging_aktif, prod, pb):
    panjang = "wp-content/uploads/" + "é" * 60 + ".txt"
    prod.berkas["wp-content/uploads/ü-berkas.txt"] = (b"u", MTIME)
    prod.berkas[panjang] = (b"p", MTIME)
    prod.jumlah_dilewati = 2
    prod.ekstra_manifest = [
        {"path": "../evil.php", "ukuran": 1, "mtime": 1, "hash": None},
        {"path": "wp-content/x\x00y", "ukuran": 1, "mtime": 1, "hash": None},
        {"path": "/etc/passwd", "ukuran": 1, "mtime": 1, "hash": None},
        {"path": "wp-config.php", "ukuran": 1, "mtime": 1, "hash": None},
        {"path": "ok.txt", "ukuran": "1", "mtime": 1, "hash": None},
    ]
    _, hasil = _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    assert (files / "wp-content/uploads/ü-berkas.txt").read_bytes() == b"u"
    assert (files / panjang).read_bytes() == b"p"
    assert not (files.parent / "evil.php").exists()
    assert "../evil.php" not in _paths_diminta(prod)
    assert any("7 berkas dilewati" in p for p in hasil["peringatan"])


def test_tarik_ditolak_ram_rendah(sesi, site_staging, prod, pb):
    pb.status_palsu = StatusPembantu(int(1.5 * GB), 200 * GB, 150 * GB, {}, {})
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_DITOLAK
    assert "RAM tersedia di VPS 1,5 GB" in e.value.pesan
    assert _paths_diminta(prod) == []


def test_tarik_ditolak_disk_tidak_cukup(sesi, site_staging, prod, pb):
    pb.status_palsu = StatusPembantu(8 * GB, 100 * GB, 15 * GB, {}, {})
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_DITOLAK
    assert "minimal 15%" in e.value.pesan
    assert _paths_diminta(prod) == []


def test_tarik_ditolak_batas_staging_aktif(sesi, site_staging, prod, pb):
    for i in range(3):
        s = Site(id=uuid.uuid4(), nama=f"S{i}", url=f"https://s{i}.test", status=SiteStatus.active,
                 secret_terenkripsi=b"x")
        sesi.add(s)
        sesi.flush()
        sesi.add(Staging(site_id=s.id, nama=f"s{i}", aktif=True, status=StatusStaging.siap))
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert "Sudah ada 3 staging aktif" in e.value.pesan


def test_tarik_ditolak_tanpa_izin_connector(sesi, site_staging, prod, pb):
    site = sesi.get(Site, site_staging.site_id)
    site.fitur = ["self_update"]
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert "Izinkan staging" in e.value.pesan


def test_ekspor_tabel_dilanjutkan_dari_kursor(sesi, site_staging, prod, pb):
    prod.tabel["wp_posts"].append(b"INSERT INTO `wp_posts` (`id`) VALUES ('2');\n")
    prod.jadwal_gagal["/staging/tabel"] = {3, 4, 5}
    with pytest.raises(SiteError):
        _jalankan(sesi, site_staging, prod)
    job = sesi.query(Job).one()
    assert job.payload["kemajuan"]["tahap"] == "db"
    _jalankan(sesi, site_staging, prod, job=job)
    assert pb.sql.count(b"VALUES ('1')") == 1
    assert pb.sql.count(b"VALUES ('2')") == 1
    assert pb.nama_panggilan().count("db_impor") == 1


def test_potongan_tertulis_tanpa_tercatat_dibuang_saat_lanjut(sesi, site_staging, prod, pb, monkeypatch):
    """Terputus sesudah potongan ditulis tetapi sebelum kursornya tercatat: tidak tergandakan."""
    prod.tabel["wp_posts"].append(b"INSERT INTO `wp_posts` (`id`) VALUES ('2');\n")
    asli = umum.simpan_kemajuan
    putus = []

    def simpan_putus(sesi_, job_, **perubahan):
        if (perubahan.get("tabel_seq") or {}).get("wp_posts") == 2 and not putus:
            putus.append(1)
            raise SiteError(TRANSIENT, "koneksi database putus")
        return asli(sesi_, job_, **perubahan)

    monkeypatch.setattr(umum, "simpan_kemajuan", simpan_putus)
    with pytest.raises(SiteError):
        _jalankan(sesi, site_staging, prod)
    job = sesi.query(Job).one()
    assert job.payload["kemajuan"]["tabel_seq"]["wp_posts"] == 1
    _jalankan(sesi, site_staging, prod, job=job)
    assert pb.sql.count(b"VALUES ('1')") == 1
    assert pb.sql.count(b"VALUES ('2')") == 1


def test_impor_diulang_selalu_dari_database_bersih(sesi, site_staging, prod, pb):
    """Impor yang terputus tidak dilanjutkan di tengah: db-buat + db-impor diulang utuh."""
    # Worker dihentikan di tengah impor (deploy): job dilanjutkan otomatis.
    pb.gagal["db_impor"] = umum.GalatBerhenti()
    with pytest.raises(SiteError):
        _jalankan(sesi, site_staging, prod)
    job = sesi.query(Job).one()
    assert job.payload["kemajuan"]["tahap"] == "impor"
    del pb.gagal["db_impor"]
    pb.panggilan.clear()
    _jalankan(sesi, site_staging, prod, job=job)
    assert pb.nama_panggilan()[:3] == ["status", "db_buat", "db_impor"]
    assert pb.sql.count(b"CREATE TABLE `wp_posts`") == 1


def test_tabel_tanpa_pk_diperingatkan(sesi, site_staging, prod, pb):
    prod.pk["wp_options"] = []
    _, hasil = _jalankan(sesi, site_staging, prod)
    assert sum("wp_options" in p and "primary key" in p for p in hasil["peringatan"]) == 1
    assert not any("wp_posts" in p for p in hasil["peringatan"])


def test_sql_mysql8_disesuaikan_untuk_mariadb(sesi, site_staging, prod, pb):
    prod.tabel["wp_posts"] = [(
        b"DROP TABLE IF EXISTS `wp_posts`;\n"
        b"CREATE TABLE `wp_posts` (\n  `id` int NOT NULL /*!80023 INVISIBLE */,\n"
        b"  `t` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci COMMENT 'utf8mb4_0900_ai_ci; /*!80016 x */'\n"
        b") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci /*!50100 TABLESPACE `innodb_system` */;\n"
        b"INSERT INTO `wp_posts` (`id`,`t`) VALUES ('1','utf8mb4_0900_ai_ci /*!80016 tetap */');\n"
    )]
    _jalankan(sesi, site_staging, prod)
    sql = pb.sql
    assert b"COLLATE utf8mb4_unicode_520_ci COMMENT 'utf8mb4_0900_ai_ci; /*!80016 x */'" in sql
    assert b"COLLATE=utf8mb4_unicode_520_ci" in sql
    assert b"/*!80023" not in sql
    assert b"/*!50100 TABLESPACE `innodb_system` */" in sql
    # Data baris tidak pernah diubah.
    assert b"VALUES ('1','utf8mb4_0900_ai_ci /*!80016 tetap */');" in sql


@pytest.mark.parametrize("ubah,pesan", [
    ({"table_prefix": "wp_'; DROP"}, "prefix"),
    ({"multisite": True}, "multisite"),
    ({"konten_di_luar": True}, "wp-content"),
    ({"home": "javascript:alert(1)"}, "alamat"),
    ({"siteurl": "https://contoh.test/a b"}, "alamat"),
])
def test_info_berbahaya_ditolak(sesi, site_staging, prod, pb, ubah, pesan):
    prod.info.update(ubah)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert pesan in e.value.pesan
    assert _paths_diminta(prod) == []


def test_batal_membersihkan_area_tarik(sesi, site_staging, staging_aktif, prod, pb):
    def minta_batal(p, n, badan):
        st = sesi.get(Staging, site_staging.id)
        st.batal_diminta_pada = datetime.now(timezone.utc)
        sesi.commit()

    prod.sebelum["/staging/tanda-air"] = minta_batal
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert "Dibatalkan" in e.value.pesan
    assert not (staging_aktif / str(site_staging.site_id) / "tarik").exists()


def test_galat_pembantu_menandai_gagal_tanpa_bocor(sesi, site_staging, prod, pb):
    pb.gagal["db_impor"] = GalatPembantu("impor", "Impor database staging gagal.")
    with pytest.raises(SiteError):
        _jalankan(sesi, site_staging, prod)
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal
    assert site_staging.galat == "Impor database staging gagal."


def test_sertifikat_gagal_tidak_menggagalkan_tarik(sesi, site_staging, prod, pb):
    pb.gagal["sertifikat"] = GalatPembantu("sertifikat", "Sertifikat staging belum dapat diterbitkan.")
    _, hasil = _jalankan(sesi, site_staging, prod)
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap
    assert site_staging.sertifikat_pada is None
    assert any("Sertifikat" in p for p in hasil["peringatan"])


def test_urai_info_membersihkan_dan_menjepit():
    info = tarik.urai_info({
        "table_prefix": "wp_", "home": "https://contoh.test/", "siteurl": "https://contoh.test/wp",
        "charset": "koi8r'; x", "php": "8.2.1\x00", "batas_unggah": 10**12,
        "tabel": [
            {"nama": "wp_posts", "baris": -5, "ukuran": "12", "pk": ["ID"]},
            {"nama": "lain_x", "baris": 1, "ukuran": 1, "pk": []},
            {"nama": "wp_`x`", "baris": 1, "ukuran": 1, "pk": []},
            {"nama": "wp_meta", "baris": 1, "ukuran": 2**70, "pk": ["a`b"]},
            "bukan objek",
        ],
    })
    assert info["prefix"] == "wp_"
    assert info["home"] == "https://contoh.test"
    assert info["charset"] == "utf8mb4"
    assert info["php"] == "8.2.1" and info["versi_php"] == "8.2" and info["php_peringatan"] is False
    assert info["batas_unggah"] == 4 * 1024 * 1024
    assert [t["nama"] for t in info["tabel"]] == ["wp_meta", "wp_posts"]
    assert info["tabel"][0]["pk"] == [] and info["tabel"][1] == {"nama": "wp_posts", "baris": 0, "ukuran": 12,
                                                                  "pk": ["ID"]}
    assert info["tabel_dilewati"] == 3
    assert tarik.urls_produksi(info) == ["https://contoh.test/wp", "http://contoh.test/wp",
                                         "https://contoh.test", "http://contoh.test"]


def test_sql_baca_berkas_ditolak_sebelum_impor(sesi, site_staging, prod, pb):
    prod.tabel["wp_posts"].append(b"LOAD DATA LOCAL INFILE '/etc/shadow' INTO TABLE `wp_posts`;\n")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_GAGAL
    assert "Potongan SQL tabel wp_posts dari produksi ditolak" in e.value.pesan
    assert "db_impor" not in pb.nama_panggilan()


def test_anggaran_db_dihitung_lintas_potongan_dan_setelah_lanjut(sesi, site_staging, prod, pb, monkeypatch):
    prod.tabel["wp_posts"].append(b"INSERT INTO `wp_posts` (`id`) VALUES ('2');\n")
    total = sum(len(c) for isi in prod.tabel.values() for c in isi)
    # Setiap putaran sendiri di bawah batas; hanya jumlah keduanya yang melebihi.
    monkeypatch.setattr(tarik, "anggaran_db", lambda ukuran: total - 1)
    prod.jadwal_gagal["/staging/tabel"] = {3, 4, 5}
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == TRANSIENT
    job = sesi.query(Job).one()
    assert job.payload["kemajuan"]["db_diterima"] > 0
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod, job=job)
    assert e.value.error_class == STAGING_GAGAL
    assert "melebihi ukuran database" in e.value.pesan


def test_anggaran_db_dari_ukuran_yang_dilaporkan(sesi, site_staging, prod, pb, monkeypatch):
    monkeypatch.setattr(tarik, "TOLERANSI_BYTE", 0)
    prod.ukuran_tabel = {"wp_posts": 1, "wp_options": 1}
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert "melebihi ukuran database" in e.value.pesan


def test_halaman_manifest_melebihi_batas_ditolak(sesi, site_staging, prod, pb, monkeypatch):
    monkeypatch.setattr(tarik, "BATAS_HALAMAN_MANIFEST", 2)
    prod.halaman = 3
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_GAGAL
    assert "lebih banyak entri" in e.value.pesan
    assert next(b.get("batas") for route, b in prod.diminta if route == "/staging/manifest") == "2"


@pytest.mark.parametrize("batas", [("MAKS_ENTRI_MANIFEST", 2), ("MAKS_BYTE_MANIFEST", 100)])
def test_manifest_total_dibatasi(sesi, site_staging, prod, pb, monkeypatch, batas):
    monkeypatch.setattr(tarik, *batas)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_GAGAL
    assert "Manifest produksi melebihi batas" in e.value.pesan
    assert _paths_diminta(prod) == []


def test_cadangan_disk_memakai_anggaran(sesi, site_staging, prod, pb):
    # Cukup untuk ukuran manifest mentah, tetapi tidak untuk anggaran
    # maksimum (toleransi 64 MiB berkas + 64 MiB SQL).
    pb.status_palsu = StatusPembantu(8 * GB, 100 * GB, 15 * GB + 100 * 1024 * 1024, {}, {})
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_DITOLAK
    assert "minimal 15%" in e.value.pesan


def test_symlink_di_staging_dihapus_dan_isi_produksi_dipulihkan(sesi, site_staging, staging_aktif, prod, pb,
                                                               tmp_path):
    _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    luar = tmp_path / "luar"
    (luar / "d").mkdir(parents=True)
    (luar / "rahasia.txt").write_bytes(b"rahasia")
    (luar / "d" / "besar.bin").write_bytes(b"bukan milik staging")
    (files / "index.php").unlink()
    shutil.rmtree(files / "wp-content/uploads")
    try:
        os.symlink(luar / "rahasia.txt", files / "index.php")
        os.symlink(luar / "d", files / "wp-content/uploads", target_is_directory=True)
    except OSError:
        pytest.skip("symlink tidak diizinkan di sistem ini")
    _jalankan(sesi, site_staging, prod)
    assert not os.path.islink(files / "index.php")
    assert (files / "index.php").read_bytes() == b"<?php // indeks"
    assert not os.path.islink(files / "wp-content/uploads")
    assert (files / "wp-content/uploads/besar.bin").read_bytes() == bytes(range(256)) * 10
    assert (luar / "rahasia.txt").read_bytes() == b"rahasia"
    assert (luar / "d" / "besar.bin").read_bytes() == b"bukan milik staging"


def test_path_tidak_aman_saat_penyiapan_pesan_tetap(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    def tolak(*a, **kw):
        raise PathTidakAman("Path melewati symlink atau bukan direktori")

    monkeypatch.setattr(tarik, "tulis_atomik", tolak)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_GAGAL
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal
    assert "mu-plugins" in site_staging.galat and str(staging_aktif) not in site_staging.galat


def test_rentang_yang_menetes_dilewati(sesi, site_staging, staging_aktif, prod, pb):
    prod.maks_rentang = 10
    _, hasil = _jalankan(sesi, site_staging, prod)
    diminta = _rentang_diminta(prod, "wp-content/uploads/besar.bin")
    # ceil(total_maks / 1 MiB) + 16 permintaan untuk seluruh percobaan berkas ini.
    assert len(diminta) == 1 + tarik.TAMBAHAN_PERMINTAAN
    assert not (_files(staging_aktif, site_staging) / "wp-content/uploads/besar.bin").exists()
    assert any("besar.bin" in p and "terlalu lambat" in p for p in hasil["peringatan"])
    assert not list(_files(staging_aktif, site_staging).rglob(".wpmgr-*"))


def test_direktori_kosong_setelah_hapus_dirapikan(sesi, site_staging, staging_aktif, prod, pb):
    _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    assert (files / "wp-content/themes/t").is_dir()
    del prod.berkas["wp-content/themes/t/style.css"]
    _jalankan(sesi, site_staging, prod)
    assert not (files / "wp-content/themes").exists()
    assert (files / "wp-content/uploads/besar.bin").exists()
