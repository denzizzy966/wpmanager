from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from staging_palsu import PembantuPalsu, ProduksiPalsu

from wpmgr.errors import STAGING_DITOLAK, SiteError
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    Site,
    StagingUji,
    StatusStaging,
    TrafficRincian,
)
from wpmgr.staging import uji, umum
from wpmgr.staging.pembantu import GalatPembantu

pytestmark = pytest.mark.integration

HTML = "<html><title>Beranda</title>" + "x" * 5000 + "</html>"
PAKET = [{"tipe": "plugin", "slug": "akismet/akismet.php", "dari": "5.2", "ke": "5.3.1"}]


class WebStaging:
    """Router staging tiruan: isi halaman berganti setelah update dijalankan."""

    def __init__(self):
        self.fase = "sebelum"
        self.halaman = {"sebelum": {}, "sesudah": {}}
        self.diminta = []

    def http(self):
        return httpx.Client(transport=httpx.MockTransport(self.tangani))

    def tangani(self, r):
        self.diminta.append(r)
        status, isi = self.halaman[self.fase].get(r.url.path, (200, HTML))
        return httpx.Response(status, text=isi)


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))


@pytest.fixture
def prod():
    p = ProduksiPalsu()
    p.berkas = {"index.php": (b"<?php", 1700000000)}
    p.tabel = {"wp_posts": [b"CREATE TABLE `wp_posts` (`id` int);\n"]}
    return p


@pytest.fixture
def web(monkeypatch):
    w = WebStaging()
    monkeypatch.setattr(umum, "buat_http", w.http)
    return w


@pytest.fixture
def pb(staging_aktif, monkeypatch, web):
    palsu = PembantuPalsu(staging_aktif)

    def saat_wpcli(nama, argumen):
        if argumen[:2] == ("plugin", "update"):
            web.fase = "sesudah"

    palsu.saat_wpcli = saat_wpcli
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


def _jalankan(sesi, site_staging, prod, konfirmasi=False):
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_uji_update, {"paket": PAKET, "konfirmasi": konfirmasi})
    return uji.tangani_staging_uji_update(sesi, job, prod.klien(site))


def test_lolos_dengan_halaman_traffic(sesi, site_staging, prod, pb, web):
    for kunci, n in (("/toko/", 50), ("/kontak/", 30), ("/blog/", 20), ("/jarang/", 1), ("javascript:x", 99)):
        sesi.add(TrafficRincian(site_id=site_staging.site_id, tanggal=datetime.now(timezone.utc).date() - timedelta(days=1),
                                sumber="plugin", dimensi="halaman", kunci=kunci, kunjungan=n))
    sesi.commit()
    hasil = _jalankan(sesi, site_staging, prod)
    assert hasil["hasil"] == "lolos"
    assert ("wpcli", "contoh-test", "plugin", "update", "akismet", "--version=5.3.1") in pb.panggilan
    u = sesi.query(StagingUji).one()
    assert u.hasil == "lolos"
    assert [h["jalur"] for h in u.pemeriksaan["halaman"]] == ["/", "/wp-login.php", "/toko/", "/kontak/", "/blog/"]
    assert u.pemeriksaan["halaman"][0]["sebelum"]["judul"] == "Beranda"
    assert u.paket == [{"tipe": "plugin", "slug": "akismet/akismet.php", "dari": "5.2", "ke": "5.3.1"}]
    assert web.diminta[0].headers["Host"] == "contoh-test.staging.contoh.id"
    assert "wpmgr_stg_m=" in web.diminta[0].headers["Cookie"]
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap
    assert site_staging.diubah_pada is None
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Uji update di staging: lolos%")).count() == 1


def test_gagal_karena_fatal_baru(sesi, site_staging, staging_aktif, prod, pb, web):
    log = staging_aktif / str(site_staging.site_id) / "log" / "php-error.log"

    def saat_wpcli(nama, argumen):
        if argumen[:2] == ("plugin", "update"):
            web.fase = "sesudah"
            with open(log, "ab") as f:
                f.write(b"[26-Sep-2026] PHP Fatal error:  Uncaught Error in /var/www/html/wp-content/plugins/akismet/a.php:9\n")

    pb.saat_wpcli = saat_wpcli
    hasil = _jalankan(sesi, site_staging, prod)
    assert hasil["hasil"] == "gagal"
    u = sesi.query(StagingUji).one()
    assert "1 error fatal baru di log PHP staging" in u.pemeriksaan["alasan"]
    assert u.pemeriksaan["fatal_baru"] == [
        "[26-Sep-2026] PHP Fatal error:  Uncaught Error in wp-content/plugins/akismet/a.php:9"]


def test_gagal_karena_halaman_menyusut_lebih_dari_separuh(sesi, site_staging, prod, pb, web):
    web.halaman["sesudah"]["/"] = (200, "<html><title>Beranda</title>" + "x" * 1000 + "</html>")
    assert _jalankan(sesi, site_staging, prod)["hasil"] == "gagal"
    alasan = sesi.query(StagingUji).one().pemeriksaan["alasan"]
    assert any(a.startswith("/ menyusut dari") for a in alasan)


def test_gagal_karena_5xx(sesi, site_staging, prod, pb, web):
    web.halaman["sesudah"]["/wp-login.php"] = (500, "Galat")
    assert _jalankan(sesi, site_staging, prod)["hasil"] == "gagal"
    assert "/wp-login.php membalas HTTP 500" in sesi.query(StagingUji).one().pemeriksaan["alasan"]


def test_gagal_karena_update_gagal(sesi, site_staging, prod, pb, web):
    def saat_wpcli(nama, argumen):
        if argumen[:2] == ("plugin", "update"):
            raise GalatPembantu("wpcli", "Perintah wp-cli di staging gagal.")

    pb.saat_wpcli = saat_wpcli
    assert _jalankan(sesi, site_staging, prod)["hasil"] == "gagal"
    alasan = sesi.query(StagingUji).one().pemeriksaan["alasan"]
    assert alasan[0] == "Update akismet/akismet.php gagal: Perintah wp-cli di staging gagal."


def test_staging_diubah_minta_konfirmasi(sesi, site_staging, staging_aktif, prod, pb, web):
    site_staging.ditarik_pada = datetime(2026, 9, 1, tzinfo=timezone.utc)
    sesi.commit()
    log = staging_aktif / str(site_staging.site_id) / "log"
    log.mkdir(parents=True)
    (log / "diubah").write_text("1790000000", encoding="ascii")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_DITOLAK
    assert "Konfirmasi" in e.value.pesan
    sesi.query(StagingUji).delete()
    for j in sesi.query(Job).all():
        j.status = JobStatus.failed
    sesi.commit()
    assert _jalankan(sesi, site_staging, prod, konfirmasi=True)["hasil"] == "lolos"


def test_payload_paket_rusak_ditolak(sesi, site_staging, prod, pb, web):
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_uji_update, {"paket": [{"tipe": "plugin", "slug": "a/a.php", "ke": "1;id"}]})
    with pytest.raises(SiteError) as e:
        uji.tangani_staging_uji_update(sesi, job, prod.klien(site))
    assert e.value.error_class == STAGING_DITOLAK


# ---- tambahan --------------------------------------------------------------


def test_handler_terdaftar():
    assert handlers.HANDLER[JobType.staging_uji_update] is uji.tangani_staging_uji_update


def test_update_dibungkus_detak_latar_dan_penanda_diubah_dibuang(sesi, site_staging, staging_aktif, prod, pb, web,
                                                                  monkeypatch):
    """Update wp-cli bisa berjalan lama: harus di dalam detak_latar (F7).
    Penanda log/diubah yang ditulis mu-plugin saat uji meng-update plugin
    dibuang di akhir, jadi uji tidak terhitung sebagai perubahan pengguna."""
    dalam_detak = []
    asli = umum.detak_latar
    aktif = [False]

    @contextmanager
    def detak_palsu(s, j, *a, **kw):
        with asli(s, j, *a, **kw) as d:
            aktif[0] = True
            try:
                yield d
            finally:
                aktif[0] = False

    monkeypatch.setattr(umum, "detak_latar", detak_palsu)
    penanda = staging_aktif / str(site_staging.site_id) / "log" / "diubah"

    def saat_wpcli(nama, argumen):
        if argumen[0] in ("plugin", "theme", "core") and argumen[1:2] == ("update",):
            dalam_detak.append((argumen[0], aktif[0]))
            web.fase = "sesudah"
            penanda.write_text(str(int(datetime.now(timezone.utc).timestamp())), encoding="ascii")

    pb.saat_wpcli = saat_wpcli
    site = sesi.get(Site, site_staging.site_id)
    paket = [{"tipe": "theme", "slug": "twentytwentyfour", "ke": "1.2"},
             {"tipe": "core", "slug": "core", "dari": "6.5", "ke": "6.6.1"}]
    job = buat_job(sesi, site.id, JobType.staging_uji_update, {"paket": paket})
    assert uji.tangani_staging_uji_update(sesi, job, prod.klien(site))["hasil"] == "lolos"
    assert dalam_detak == [("theme", True), ("core", True)]
    assert ("wpcli", "contoh-test", "theme", "update", "twentytwentyfour", "--version=1.2") in pb.panggilan
    assert ("wpcli", "contoh-test", "core", "update", "--version=6.6.1") in pb.panggilan
    assert not penanda.exists()
    sesi.refresh(site_staging)
    assert site_staging.diubah_pada is None
    u = sesi.query(StagingUji).one()
    assert u.job_id == job.id
    assert [x["ok"] for x in u.pemeriksaan["update"]] == [True, True]


def test_judul_dan_log_jahat_dibersihkan(sesi, site_staging, staging_aktif, prod, pb, web):
    log = staging_aktif / str(site_staging.site_id) / "log" / "php-error.log"
    web.halaman["sesudah"]["/"] = (200, "<title>Be\x00randa</title>" + "x" * 5000)

    def saat_wpcli(nama, argumen):
        if argumen[:2] == ("plugin", "update"):
            web.fase = "sesudah"
            with open(log, "ab") as f:
                f.write(b"PHP Fatal error: \x00 \xed\xa0\x80 in /var/www/html/x.php\n")

    pb.saat_wpcli = saat_wpcli
    assert _jalankan(sesi, site_staging, prod)["hasil"] == "gagal"
    u = sesi.query(StagingUji).one()
    assert u.pemeriksaan["halaman"][0]["sesudah"]["judul"] == "Beranda"
    assert u.pemeriksaan["fatal_baru"] == ["PHP Fatal error:  ��� in x.php"]


def test_log_dipangkas_selama_uji_dibaca_dari_awal(sesi, site_staging, staging_aktif, prod, pb, web):
    log = staging_aktif / str(site_staging.site_id) / "log" / "php-error.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b"[lama] PHP Warning: x\n" * 200)

    def saat_wpcli(nama, argumen):
        if argumen[:2] == ("plugin", "update"):
            web.fase = "sesudah"
            # Log diputar: berkas baru lebih pendek dari posisi "sebelum".
            log.write_bytes(b"[baru] PHP Parse error: y\n")

    pb.saat_wpcli = saat_wpcli
    assert _jalankan(sesi, site_staging, prod)["hasil"] == "gagal"
    assert sesi.query(StagingUji).one().pemeriksaan["fatal_baru"] == ["[baru] PHP Parse error: y"]


def test_gagal_final_saat_tarik_membersihkan_area_kerja(sesi, site_staging, staging_aktif, prod, pb, web):
    pb.gagal["db_impor"] = GalatPembantu("impor", "Impor database staging gagal.")
    with pytest.raises(SiteError):
        _jalankan(sesi, site_staging, prod)
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal
    assert not (staging_aktif / str(site_staging.site_id) / "tarik").exists()
    assert sesi.query(StagingUji).count() == 0
