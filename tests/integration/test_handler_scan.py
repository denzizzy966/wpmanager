import httpx
import pytest

from wpmgr.jobs.handlers import (
    simpan_inventaris,
    tangani_scan_site,
    tangani_verify_site,
)
from wpmgr.jobs.queue import ambil_job, buat_job
from wpmgr.models import JobType, PackageType, SitePackage, SiteStatus
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration

INVENTARIS = {
    "core": {"slug": "core", "nama": "WordPress", "versi_terpasang": "6.5.2",
             "versi_tersedia": "6.6", "aktif": True, "auto_update": False},
    "plugins": [
        {"slug": "elementor/elementor.php", "nama": "Elementor", "versi_terpasang": "3.18.3",
         "versi_tersedia": "3.20.1", "aktif": True, "auto_update": False},
        {"slug": "akismet/akismet.php", "nama": "Akismet", "versi_terpasang": "5.3",
         "versi_tersedia": None, "aktif": False, "auto_update": True},
    ],
    "themes": [
        {"slug": "astra", "nama": "Astra", "versi_terpasang": "4.6",
         "versi_tersedia": None, "aktif": True, "auto_update": False},
    ],
}


def klien_palsu(muatan, status=200):
    def handler(request):
        return httpx.Response(status, json=muatan)

    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_simpan_inventaris_membuat_satu_baris_per_paket(sesi, site):
    assert simpan_inventaris(sesi, site, INVENTARIS) == 4
    assert sesi.query(SitePackage).filter_by(site_id=site.id).count() == 4


def test_core_disimpan_dengan_slug_core(sesi, site):
    simpan_inventaris(sesi, site, INVENTARIS)
    inti = sesi.query(SitePackage).filter_by(site_id=site.id, tipe=PackageType.core).one()
    assert inti.slug == "core"
    assert inti.versi_tersedia == "6.6"


def test_scan_ulang_memperbarui_bukan_menggandakan(sesi, site):
    simpan_inventaris(sesi, site, INVENTARIS)
    baru = {**INVENTARIS, "plugins": [
        {**INVENTARIS["plugins"][0], "versi_terpasang": "3.20.1", "versi_tersedia": None},
        INVENTARIS["plugins"][1],
    ]}
    assert simpan_inventaris(sesi, site, baru) == 4
    e = sesi.query(SitePackage).filter_by(site_id=site.id, slug="elementor/elementor.php").one()
    assert e.versi_terpasang == "3.20.1"
    assert e.versi_tersedia is None


def test_paket_yang_dihapus_di_site_ikut_hilang(sesi, site):
    simpan_inventaris(sesi, site, INVENTARIS)
    tanpa_akismet = {**INVENTARIS, "plugins": [INVENTARIS["plugins"][0]]}
    assert simpan_inventaris(sesi, site, tanpa_akismet) == 3
    assert sesi.query(SitePackage).filter_by(site_id=site.id, slug="akismet/akismet.php").count() == 0


def test_handler_scan_menyimpan_dan_memperbarui_last_scan_at(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    job = ambil_job(sesi, "w1")
    hasil = tangani_scan_site(sesi, job, klien_palsu(INVENTARIS))
    sesi.refresh(site)
    assert hasil["jumlah_paket"] == 4
    assert site.last_scan_at is not None
    assert site.last_seen_at is not None


def test_handler_verify_mengaktifkan_site(sesi, site):
    site.status = SiteStatus.pending_pair
    sesi.commit()
    buat_job(sesi, site.id, JobType.verify_site)
    job = ambil_job(sesi, "w1")
    tangani_verify_site(sesi, job, klien_palsu(
        {"connector_version": "1.0", "wp_version": "6.5.2", "php_version": "8.1"}))
    sesi.refresh(site)
    assert site.status == SiteStatus.active
    assert site.wp_version == "6.5.2"
    assert site.php_version == "8.1"
    assert site.connector_version == "1.0"


def test_core_none_tidak_menghapus_baris_core(sesi, site):
    simpan_inventaris(sesi, site, INVENTARIS)
    payload = {**INVENTARIS, "core": None}
    hasil = simpan_inventaris(sesi, site, payload)
    inti = sesi.query(SitePackage).filter_by(site_id=site.id, tipe=PackageType.core).one()
    assert inti.slug == "core"
    assert inti.versi_tersedia == "6.6"
    assert sesi.query(SitePackage).filter_by(site_id=site.id).count() == 4
    assert hasil == 4


def test_tanpa_kunci_themes_tidak_menghapus_baris_tema(sesi, site):
    simpan_inventaris(sesi, site, INVENTARIS)
    payload = {"core": INVENTARIS["core"], "plugins": INVENTARIS["plugins"]}
    hasil = simpan_inventaris(sesi, site, payload)
    assert sesi.query(SitePackage).filter_by(
        site_id=site.id, tipe=PackageType.theme
    ).count() == 1
    assert hasil == 4


def test_themes_kosong_tetap_menghapus_baris_tema_lama(sesi, site):
    simpan_inventaris(sesi, site, INVENTARIS)
    payload = {**INVENTARIS, "themes": []}
    hasil = simpan_inventaris(sesi, site, payload)
    assert sesi.query(SitePackage).filter_by(
        site_id=site.id, tipe=PackageType.theme
    ).count() == 0
    assert hasil == 3


def test_scan_sukses_mengaktifkan_site_unreachable(sesi, site):
    site.status = SiteStatus.unreachable
    sesi.commit()
    buat_job(sesi, site.id, JobType.scan_site)
    job = ambil_job(sesi, "w1")
    tangani_scan_site(sesi, job, klien_palsu(INVENTARIS))
    sesi.refresh(site)
    assert site.status == SiteStatus.active


def test_scan_sukses_tidak_mengaktifkan_site_disabled(sesi, site):
    site.status = SiteStatus.disabled
    sesi.commit()

    # Job diambil langsung dari buat_job, bukan lewat ambil_job: sejak T12 SQL
    # klaim menyaring site berstatus disabled, sehingga job ini memang tidak
    # akan pernah terklaim. Yang diuji di sini adalah pertahanan lapis kedua —
    # handler sendiri tidak boleh mengaktifkan site yang sengaja dimatikan,
    # untuk kasus site dinonaktifkan setelah job terklaim tetapi sebelum
    # handler sempat berjalan.
    job = buat_job(sesi, site.id, JobType.scan_site)
    tangani_scan_site(sesi, job, klien_palsu(INVENTARIS))

    sesi.refresh(site)
    assert site.status == SiteStatus.disabled
