import pytest
from staging_palsu import GB, ProduksiPalsu

from wpmgr.jobs.queue import buat_job
from wpmgr.models import JobType, Site
from wpmgr.staging import rencana, tarik, umum
from wpmgr.staging.pembantu import StatusPembantu

pytestmark = pytest.mark.integration

MTIME = 1_700_000_000
MILIK = "wp-content/mu-plugins/milik-tujuan.php"


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))
    monkeypatch.setattr(rencana, "UKURAN_PAKET", 1000)
    monkeypatch.setattr(tarik, "UKURAN_PAKET", 1000)


@pytest.fixture
def prod():
    p = ProduksiPalsu()
    p.berkas = {
        "index.php": (b"<?php // indeks", MTIME),
        "wp-content/uploads/besar.bin": (bytes(range(256)) * 10, MTIME),
        MILIK: (b"<?php // versi produksi", MTIME),
    }
    p.tabel = {
        "wp_posts": [b"DROP TABLE IF EXISTS `wp_posts`;\nCREATE TABLE `wp_posts` (`id` int);\n",
                     b"INSERT INTO `wp_posts` (`id`) VALUES ('1');\n"],
        "wp_options": [b"DROP TABLE IF EXISTS `wp_options`;\nCREATE TABLE `wp_options` (`a` text);\n"],
    }
    return p


def _tujuan(akar, baris, panggilan, **ganti):
    status = StatusPembantu(8 * GB, 200 * GB, 150 * GB, {}, {})
    dasar = {
        "akar": akar, "baris": baris,
        "status_sumber": lambda: panggilan.append("status") or status,
        "cek_awal": lambda st: panggilan.append("cek_awal"),
        "periksa_info": lambda info: panggilan.append(("info", info["home"])),
        "sql_tambahan": lambda sesi, info, d: panggilan.append("sql_tambahan"),
        "impor": lambda info, berkas: panggilan.append(("impor", len(berkas))),
        "siapkan_runtime": lambda sesi, job, info: panggilan.append("siapkan"),
        "dilindungi": frozenset({"wp-config.php", MILIK}),
        "subdir": ("files", "log"),
        "tahap_akhir": "pratinjau",
    }
    dasar.update(ganti)
    return tarik.TujuanSalinan(**dasar)


def test_tarik_inti_memanggil_tujuan_berurutan_dan_menjaga_berkas_milik_tujuan(sesi, site_staging, prod, tmp_path):
    akar = tmp_path / "tujuan" / str(site_staging.site_id)
    (akar / "files" / "wp-content" / "mu-plugins").mkdir(parents=True)
    (akar / "files" / MILIK).write_bytes(b"<?php // milik tujuan")
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    panggilan: list = []
    k = tarik.tarik_inti(sesi, job, site, prod.klien(site), _tujuan(akar, site_staging, panggilan), umum.kemajuan(job))
    assert panggilan == ["status", "cek_awal", ("info", "https://contoh.test"), "sql_tambahan", ("impor", 4), "siapkan"]
    assert k["tahap"] == "pratinjau"
    assert k["info"]["prefix"] == "wp_" and k["tanda_air"]["sumber"]["comments"]["jumlah"] == 2
    assert (akar / "files" / "index.php").read_bytes() == b"<?php // indeks"
    assert (akar / "files" / "wp-content/uploads/besar.bin").read_bytes() == bytes(range(256)) * 10
    assert (akar / "files" / MILIK).read_bytes() == b"<?php // milik tujuan"
    assert (akar / "log").is_dir() and not (akar / "ekspor").exists()
    # Melanjutkan sesudah tahap akhir tidak mengulang apa pun.
    panggilan.clear()
    k2 = tarik.tarik_inti(sesi, job, site, prod.klien(site), _tujuan(akar, site_staging, panggilan), k)
    assert panggilan == ["status", "cek_awal"] and k2["tahap"] == "pratinjau"


def test_periksa_info_menolak_sebelum_salinan_disentuh(sesi, site_staging, prod, tmp_path):
    akar = tmp_path / "tujuan" / str(site_staging.site_id)
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_tarik)

    def tolak(info):
        raise umum.GalatDitolakTanpaUbah("Site lama memakai http.")

    with pytest.raises(umum.GalatDitolakTanpaUbah):
        tarik.tarik_inti(sesi, job, site, prod.klien(site), _tujuan(akar, site_staging, [], periksa_info=tolak),
                         umum.kemajuan(job))
    assert not any(route == "/staging/file" for route, _ in prod.diminta)
    assert list((akar / "files").iterdir()) == []


def test_cek_awal_menolak_tanpa_ubah(sesi, site_staging, prod, tmp_path):
    akar = tmp_path / "tujuan" / str(site_staging.site_id)
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        tarik.tarik_inti(sesi, job, site, prod.klien(site),
                         _tujuan(akar, site_staging, [], cek_awal=lambda st: "RAM kurang."), umum.kemajuan(job))
    assert e.value.pesan == "RAM kurang."
    assert prod.diminta == []


# ---- preflight M5: penolakan connector membawa `kode` ---------------------------------

TEKS_CONNECTOR = "Tabel sementara ditahan di /home/u123/public_html; kunci rahasia abc"


def _manifest_ditahan(r, badan):
    import httpx

    return httpx.Response(409, json={"code": "wpmgr_staging_ditahan", "message": TEKS_CONNECTOR,
                                     "data": {"status": 409}})


def test_penolakan_connector_sebelum_salinan_membawa_kode(sesi, site_staging, prod, tmp_path):
    """R21: penolakan connector di tahap manifest menjadi tanpa-ubah, dengan `kode` connector-nya."""
    akar = tmp_path / "tujuan" / str(site_staging.site_id)
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    prod._manifest = _manifest_ditahan
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        tarik.tarik_inti(sesi, job, site, prod.klien(site), _tujuan(akar, site_staging, []), umum.kemajuan(job))
    assert e.value.kode == "wpmgr_staging_ditahan"


def test_penolakan_connector_di_job_hosting_tidak_menampilkan_teks_connector(sesi, site_hosting, prod, tmp_path):
    """M5 ujung ke ujung: tarik_inti dengan baris HostingVps di dalam jalankan_hosting."""
    from wpmgr.errors import STAGING_DITOLAK
    from wpmgr.hosting import umum as hu
    from wpmgr.models import HostingVps

    site = sesi.get(Site, site_hosting.site_id)
    job = buat_job(sesi, site.id, JobType.pindah_tarik)
    prod._manifest = _manifest_ditahan
    akar = hu.dir_hosting(site.id)

    def inti(sesi, job, site, h):
        return tarik.tarik_inti(sesi, job, site, prod.klien(site), _tujuan(akar, h, []), umum.kemajuan(job))

    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    assert e.value.pesan == hu.PESAN_KODE[STAGING_DITOLAK]
    h = sesi.get(HostingVps, site_hosting.id, populate_existing=True)
    assert h.galat == hu.PESAN_KODE[STAGING_DITOLAK]
