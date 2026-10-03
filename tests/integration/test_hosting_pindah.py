from datetime import datetime, timezone

import httpx
import pytest
from staging_palsu import GB, PembantuHostingPalsu, ProduksiPalsu

from wpmgr.connector_paket import isi_mu_plugin_pratinjau
from wpmgr.errors import STAGING_GAGAL, TRANSIENT, SiteError
from wpmgr.hosting import pindah
from wpmgr.hosting import umum as hu
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import buat_job
from wpmgr.models import ActivityLog, HostingVps, JobStatus, JobType, StatusHosting
from wpmgr.staging import rencana, tarik, umum
from wpmgr.staging.pembantu import GalatPembantu, StatusProd

pytestmark = pytest.mark.integration

MTIME = 1_700_000_000
IP_LAMA = "93.184.216.34"


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))
    monkeypatch.setattr(rencana, "UKURAN_PAKET", 1000)
    monkeypatch.setattr(tarik, "UKURAN_PAKET", 1000)


@pytest.fixture
def prod():
    p = ProduksiPalsu()
    p.info = {**p.info, "home": "https://toko.co.id", "siteurl": "https://toko.co.id"}
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
def pb(hosting_aktif, monkeypatch):
    palsu = PembantuHostingPalsu(hosting_aktif)
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


@pytest.fixture
def lama(prod, monkeypatch):
    """Klien hosting lama ASLI (dipatok IP) di atas transport tiruan; mencatat (host URL, header Host)."""
    host: list[tuple[str, str]] = []

    def tangani(r):
        host.append((r.url.host, r.headers.get("host")))
        return prod.tangani(r)

    monkeypatch.setattr(hu, "buat_http_lama", lambda: httpx.Client(transport=httpx.MockTransport(tangani)))
    return host


def _jalankan(sesi, site_hosting, job=None):
    job = job or buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    hasil = pindah.tangani_pindah_tarik(sesi, job, None)
    job.status = JobStatus.success
    sesi.commit()
    return job, hasil


def _h(sesi, site_hosting):
    return sesi.get(HostingVps, site_hosting.id, populate_existing=True)


def _files(hosting_aktif, site_hosting):
    return hosting_aktif / str(site_hosting.site_id) / "files"


def test_handler_terdaftar():
    assert handlers.HANDLER[JobType.pindah_tarik] is pindah.tangani_pindah_tarik


def test_pindah_tarik_penuh(sesi, site_hosting, hosting_aktif, prod, pb, lama):
    _, hasil = _jalankan(sesi, site_hosting)
    files = _files(hosting_aktif, site_hosting)
    assert (files / "index.php").read_bytes() == b"<?php // indeks"
    assert (files / "wp-content/uploads/besar.bin").read_bytes() == bytes(range(256)) * 10
    mu = files / "wp-content/mu-plugins/wpmgr-pratinjau.php"
    assert mu.read_text(encoding="utf-8") == isi_mu_plugin_pratinjau()
    assert (hosting_aktif / "router" / "toko-co-id.htpasswd").read_bytes().startswith(b"pratinjau:$2b$")
    sid = str(site_hosting.site_id)
    assert pb.nama_panggilan() == ["prod_status", "prod_buat", "prod_db_buat", "prod_db_impor", "prod_buat",
                                   "prod_router_muat", "sertifikat", "prod_domain"]
    assert ("prod_buat", "toko-co-id", "8.1", sid, "toko.co.id", True) in pb.panggilan
    assert ("prod_db_buat", "toko-co-id", "wp_") in pb.panggilan
    assert ("sertifikat", "vps-toko-co-id") in pb.panggilan
    assert ("prod_domain", "toko-co-id") in pb.panggilan
    assert pb.sql.startswith(b"SET NAMES utf8mb4;")
    # Putusan R25 tidak berlaku: secret connector produksi dibiarkan apa adanya (spec §4.1).
    assert b"wpmgr_secret" not in pb.sql
    assert lama and all(x == (IP_LAMA, "toko.co.id") for x in lama)
    akar = hosting_aktif / sid
    assert not (akar / "tarik").exists() and not (akar / "ekspor").exists()
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.pratinjau and h.galat is None
    assert h.ditarik_pada is not None and h.pratinjau_sertifikat_pada is not None
    assert h.versi_php == "8.1"
    assert h.ukuran_file == len(b"<?php // indeks") + len(b"body{}") + 2560
    assert hasil["ukuran_file"] == h.ukuran_file
    log = sesi.query(ActivityLog).filter(ActivityLog.site_id == site_hosting.site_id).all()
    assert [x.pesan for x in log] == ["Salinan VPS dibuat"]


def test_prod_buat_sebelum_prod_db_buat(sesi, site_hosting, prod, pb, lama):
    _jalankan(sesi, site_hosting)
    nama = pb.nama_panggilan()
    assert nama.index("prod_buat") < nama.index("prod_db_buat")
    assert _h(sesi, site_hosting).status == StatusHosting.pratinjau


@pytest.mark.parametrize("status", [StatusHosting.aktif, StatusHosting.pratinjau])
def test_tarik_ditolak_sesudah_dilayani_vps(sesi, site_hosting, prod, pb, lama, status):
    site_hosting.status = status
    site_hosting.dilayani_vps_pada = datetime.now(timezone.utc)
    sesi.commit()
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _jalankan(sesi, site_hosting)
    assert e.value.pesan == pindah.PESAN_SUDAH_DILAYANI
    assert lama == [] and pb.panggilan == []
    h = _h(sesi, site_hosting)
    assert h.status == status and h.galat == pindah.PESAN_SUDAH_DILAYANI


def test_pindah_tarik_terputus_lalu_dilanjutkan(sesi, site_hosting, hosting_aktif, prod, pb, lama):
    prod.jadwal_gagal = {"/staging/file": {1, 2, 3}}
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    with pytest.raises(SiteError) as e:
        pindah.tangani_pindah_tarik(sesi, job, None)
    assert e.value.error_class == TRANSIENT
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.menyalin
    assert h.galat == f"Terputus, dilanjutkan otomatis: {hu.PESAN_KELAS[TRANSIENT]}"
    manifest_sebelum = prod.hitung["/staging/manifest"]
    _jalankan(sesi, site_hosting, job)
    assert prod.hitung["/staging/manifest"] == manifest_sebelum
    assert (_files(hosting_aktif, site_hosting) / "index.php").exists()
    assert _h(sesi, site_hosting).status == StatusHosting.pratinjau


def test_batal_di_antara_potongan_menandai_salinan_belum_utuh(sesi, site_hosting, hosting_aktif, prod, pb, lama):
    def minta_batal(p, n, badan):
        h = sesi.get(HostingVps, site_hosting.id)
        h.batal_diminta_pada = datetime.now(timezone.utc)
        sesi.commit()

    prod.sebelum["/staging/tanda-air"] = minta_batal
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_hosting)
    assert "Dibatalkan" in e.value.pesan
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", hu.PESAN_BATAL_TENGAH)
    assert not (hosting_aktif / str(site_hosting.site_id) / "tarik").exists()


def test_info_http_ditolak_tanpa_ubah(sesi, site_hosting, prod, pb, lama):
    site_hosting.status = StatusHosting.pratinjau
    site_hosting.ditarik_pada = datetime.now(timezone.utc)
    sesi.commit()
    prod.info = {**prod.info, "home": "http://toko.co.id"}
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _jalankan(sesi, site_hosting)
    assert not any(route == "/staging/file" for route, _ in prod.diminta)
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat) == (StatusHosting.pratinjau, pindah.PESAN_HTTP)


def test_ram_kurang_ditolak_tanpa_ubah(sesi, site_hosting, prod, pb, lama):
    pb.status_prod = StatusProd(1 * GB, 200 * GB, 150 * GB, 200 * GB, 150 * GB, {})
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _jalankan(sesi, site_hosting)
    assert "minimal 2,0 GB" in e.value.pesan
    assert lama == []


def test_sertifikat_pratinjau_gagal_menjadi_peringatan(sesi, site_hosting, prod, pb, lama):
    pb.gagal["sertifikat"] = GalatPembantu("sertifikat", "Menerbitkan sertifikat staging gagal. Sertifikat "
                                                          "staging belum dapat diterbitkan.")
    _, hasil = _jalankan(sesi, site_hosting)
    assert any(p.startswith("Sertifikat pratinjau belum terbit") for p in hasil["peringatan"])
    assert "prod_domain" in pb.nama_panggilan()
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.pratinjau and h.pratinjau_sertifikat_pada is None


def test_prod_domain_gagal_menandai_gagal_salinan(sesi, site_hosting, hosting_aktif, prod, pb, lama):
    pesan = "Memasang konfigurasi nginx domain gagal. Konfigurasi nginx domain ditolak; site lain tidak terpengaruh."
    pb.gagal["prod_domain"] = GalatPembantu("nginx", pesan)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_hosting)
    assert e.value.error_class == STAGING_GAGAL
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", pesan)
    assert not (hosting_aktif / str(site_hosting.site_id) / "tarik").exists()


def test_salin_ulang_menjaga_wp_config_dan_menulis_ulang_mu_plugin(sesi, site_hosting, hosting_aktif, prod, pb, lama):
    _jalankan(sesi, site_hosting)
    files = _files(hosting_aktif, site_hosting)
    mu = files / "wp-content/mu-plugins/wpmgr-pratinjau.php"
    mu.write_bytes(b"<?php // dirusak")
    prod.berkas["index.php"] = (b"<?php // indeks baru", MTIME + 1)
    _jalankan(sesi, site_hosting)
    assert (files / "index.php").read_bytes() == b"<?php // indeks baru"
    assert (files / "wp-config.php").exists()
    assert mu.read_text(encoding="utf-8") == isi_mu_plugin_pratinjau()
    pesan = [x.pesan for x in sesi.query(ActivityLog).filter(ActivityLog.site_id == site_hosting.site_id)
             .order_by(ActivityLog.id)]
    assert pesan == ["Salinan VPS dibuat", "Salinan VPS disegarkan"]


# ---- carry review Task 5-7 ---------------------------------------------------------


def test_berkas_milik_tujuan_bertahan_saat_salin_ulang(sesi, site_hosting, hosting_aktif, prod, pb, lama,
                                                       monkeypatch):
    """Mu-plugin pratinjau hanya ada di tujuan (produksi tidak pernah punya): salin ulang tidak menghapusnya.

    Salin ulang kedua dihentikan di impor (sesudah sinkron berkas), jadi
    penyiapan yang menulis ulang mu-plugin tidak berjalan: isi yang bertahan
    membuktikan sinkron berkas tidak menyentuhnya.
    """
    _, hasil1 = _jalankan(sesi, site_hosting)
    files = _files(hosting_aktif, site_hosting)
    mu = files / pindah.MU_PLUGIN_PRATINJAU
    mu.write_bytes(b"<?php // milik tujuan")
    prod.berkas["index.php"] = (b"<?php // indeks baru", MTIME + 1)
    dilindungi, dihapus = [], []
    asli_bangun, asli_hapus = tarik._bangun_ulang_indeks, tarik.hapus_berkas

    def bangun(sesi_, job_, akar, peringatan, *argumen, **opsi):
        dilindungi.append(argumen[0] if argumen else opsi.get("dilindungi"))
        return asli_bangun(sesi_, job_, akar, peringatan, *argumen, **opsi)

    def hapus(akar, path):
        dihapus.append(path)
        return asli_hapus(akar, path)

    monkeypatch.setattr(tarik, "_bangun_ulang_indeks", bangun)
    monkeypatch.setattr(tarik, "hapus_berkas", hapus)
    pb.gagal["prod_db_impor"] = GalatPembantu("impor", "Mengimpor database situs gagal. Impor database staging gagal.")
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    with pytest.raises(SiteError):
        pindah.tangani_pindah_tarik(sesi, job, None)
    # Seperti worker: kegagalan final menutup job (uq_jobs_hosting_aktif).
    job.status = JobStatus.failed
    sesi.commit()
    assert dilindungi == [pindah.DILINDUNGI_HOSTING]
    assert pindah.MU_PLUGIN_PRATINJAU not in dihapus and "wp-config.php" not in dihapus
    assert (files / "index.php").read_bytes() == b"<?php // indeks baru"
    assert mu.read_bytes() == b"<?php // milik tujuan"
    assert (files / "wp-config.php").exists()
    # Berkas milik tujuan tidak pernah masuk indeks salinan.
    indeks = (hosting_aktif / str(site_hosting.site_id) / "indeks.jsonl").read_text(encoding="utf-8")
    assert "wpmgr-pratinjau.php" not in indeks and "wp-config.php" not in indeks

    del pb.gagal["prod_db_impor"]
    _, hasil = _jalankan(sesi, site_hosting)
    assert mu.read_text(encoding="utf-8") == isi_mu_plugin_pratinjau()
    assert hasil["ukuran_file"] == hasil1["ukuran_file"] + len(b" baru")


def test_hosting_lama_hanya_lewat_klien_lama(sesi, site_hosting, prod, pb, lama):
    """Klien bawaan worker (ke site.url, mengikuti DNS) tidak pernah dipakai."""
    class KlienTerlarang:
        def __getattr__(self, nama):
            raise AssertionError(f"klien worker dipakai: {nama}")

    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    pindah.tangani_pindah_tarik(sesi, job, KlienTerlarang())
    assert lama and all(x == (IP_LAMA, "toko.co.id") for x in lama)
    assert _h(sesi, site_hosting).status == StatusHosting.pratinjau


def test_sertifikat_sibuk_diserahkan_ke_pembungkus(sesi, site_hosting, prod, pb, lama):
    """Keluar 3 (sibuk, tanpa ubah) bukan peringatan sertifikat: pembungkus menjadwalkan ulang."""
    pb.gagal["sertifikat"] = GalatPembantu("ditolak", "Menerbitkan sertifikat staging gagal. Skrip pembantu "
                                                      "menolak permintaan ini.")
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    with pytest.raises(hu.GalatSibuk):
        pindah.tangani_pindah_tarik(sesi, job, None)
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat) == (StatusHosting.menyalin, hu.PESAN_MENUNGGU_SIBUK)
    assert "prod_domain" not in pb.nama_panggilan()
    del pb.gagal["sertifikat"]
    pb.panggilan.clear()
    _, hasil = _jalankan(sesi, site_hosting, job)
    # Dilanjutkan dari tahap pratinjau: salinan tidak diulang.
    assert pb.nama_panggilan() == ["prod_status", "sertifikat", "prod_domain"]
    assert hasil["peringatan"] == []
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.pratinjau and h.pratinjau_sertifikat_pada is not None
