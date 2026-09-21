import uuid

import pytest

from wpmgr.crypto import dekripsi_secret, secret_baru
from wpmgr.jobs.queue import buat_job
from wpmgr.models import JobType, PackageType, SitePackage
from wpmgr.pairing import buat_site, kunci_koneksi
from wpmgr.site_client import SiteClient
from wpmgr.worker import proses_satu

from .conftest import wpcli

pytestmark = pytest.mark.e2e


def _klien_http(site, sesi):
    """SiteClient untuk WordPress lokal yang memakai http, bukan https.

    SiteClient menolak base_url non-https di konstruktor -- sengaja, karena
    tanpa TLS body respons dan token SSO yang lewat bisa dibaca di jalan.
    Kontainer WordPress di lingkungan test ini bicara HTTP polos di
    localhost:8081, jadi klien dibangun dengan URL https palsu lalu
    base_url-nya ditimpa setelah konstruksi. Pemeriksaan di konstruktor
    sendiri TIDAK dilonggarkan; workaround ini dikurung di sini saja.
    """
    secret = dekripsi_secret(site.secret_terenkripsi)
    klien = SiteClient("https://placeholder.test", str(site.id), secret)
    klien.base_url = site.url  # lewati pemeriksaan https khusus lingkungan test
    return klien


@pytest.fixture
def site_terpasang(sesi, wp_site):
    site, _kunci = buat_site(sesi, "Uji E2E", "https://uji.test", None, None)
    site.url = wp_site  # http://localhost:8081
    sesi.commit()

    # Ditulis langsung lewat `wp option update`, BUKAN lewat
    # WPMGR_Settings::simpan_kunci() -- fixture ini hanya perlu WordPress
    # dalam keadaan "sudah terpasang" secepat mungkin untuk mayoritas test
    # di bawah. Jalur simpan_kunci() sungguhan diuji terpisah oleh
    # test_simpan_kunci_lewat_wpcli_menembus_pintu_masuk_asli di akhir file
    # ini, karena bypass ini sendiri tidak akan pernah menyentuh bug pada
    # parsernya.
    wpcli("option", "update", "wpmgr_site_id", str(site.id))
    wpcli("option", "update", "wpmgr_secret", dekripsi_secret(site.secret_terenkripsi))
    wpcli("option", "update", "wpmgr_dashboard_url", "http://host.docker.internal:8000")
    return site


def test_ping_menjawab_dengan_versi(sesi, site_terpasang):
    data = _klien_http(site_terpasang, sesi).ping()
    assert data["connector_version"] == "1.0.0"
    assert data["wp_version"].startswith("6.")


def test_tanda_tangan_salah_ditolak_401(sesi, site_terpasang):
    from wpmgr.errors import AUTH_ERROR, SiteError

    klien = SiteClient("https://placeholder.test", str(site_terpasang.id), "f" * 64)
    klien.base_url = site_terpasang.url
    with pytest.raises(SiteError) as exc:
        klien.ping()
    assert exc.value.error_class == AUTH_ERROR


def test_nonce_yang_diulang_ditolak(sesi, site_terpasang):
    import time

    from wpmgr.signing import new_nonce, sign

    secret = dekripsi_secret(site_terpasang.secret_terenkripsi)
    path = "/wp-json/wpmgr/v1/ping"
    ts, nonce = int(time.time()), new_nonce()
    headers = {
        "X-Wpmgr-Site": str(site_terpasang.id),
        "X-Wpmgr-Timestamp": str(ts),
        "X-Wpmgr-Nonce": nonce,
        "X-Wpmgr-Signature": sign(secret, "GET", path, ts, nonce, b""),
    }
    import httpx

    url = f"{site_terpasang.url}{path}"
    assert httpx.get(url, headers=headers, timeout=20).status_code == 200
    assert httpx.get(url, headers=headers, timeout=20).status_code == 401


def test_scan_mengisi_inventaris(sesi, site_terpasang):
    buat_job(sesi, site_terpasang.id, JobType.scan_site)
    assert proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi)) is True

    paket = sesi.query(SitePackage).filter_by(site_id=site_terpasang.id).all()
    assert any(p.tipe == PackageType.core for p in paket)
    assert any(p.slug == "wp-manager-connector/wp-manager-connector.php" for p in paket)


def test_update_plugin_versi_lama_benar_benar_naik(sesi, site_terpasang):
    wpcli("plugin", "install", "hello-dolly", "--version=1.6", "--force")

    buat_job(sesi, site_terpasang.id, JobType.scan_site)
    proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi))

    baris = sesi.query(SitePackage).filter_by(
        site_id=site_terpasang.id, slug="hello-dolly/hello.php").one()
    assert baris.versi_terpasang == "1.6"
    assert baris.versi_tersedia is not None
    target = baris.versi_tersedia

    buat_job(sesi, site_terpasang.id, JobType.update_package, {
        "tipe": "plugin", "slug": "hello-dolly/hello.php",
        "dari_versi": "1.6", "ke_versi": target,
    })
    assert proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi)) is True

    terpasang = wpcli("plugin", "get", "hello-dolly", "--field=version")
    assert terpasang == target

    sesi.refresh(baris)
    assert baris.versi_terpasang == target


def test_update_kedua_kalinya_adalah_no_op(sesi, site_terpasang):
    versi = wpcli("plugin", "get", "hello-dolly", "--field=version")
    klien = _klien_http(site_terpasang, sesi)
    hasil = klien.update("plugin", "hello-dolly/hello.php", versi)
    assert hasil["ok"] is True
    assert "sudah di versi tersebut" in hasil["pesan"]


def test_sso_mendarat_di_wp_admin_dalam_keadaan_masuk(sesi, site_terpasang):
    import httpx

    from wpmgr.sso import buat_token

    secret = dekripsi_secret(site_terpasang.secret_terenkripsi)
    token = buat_token(secret, str(site_terpasang.id))

    with httpx.Client(follow_redirects=True, timeout=30) as c:
        r = c.get(f"{site_terpasang.url}/?wpmgr_sso={token}")
        assert r.status_code == 200
        assert "/wp-admin" in str(r.url)
        assert any(n.startswith("wordpress_logged_in") for n in c.cookies)


def test_token_sso_tidak_dapat_dipakai_dua_kali(sesi, site_terpasang):
    import httpx

    from wpmgr.sso import buat_token

    secret = dekripsi_secret(site_terpasang.secret_terenkripsi)
    token = buat_token(secret, str(site_terpasang.id))
    url = f"{site_terpasang.url}/?wpmgr_sso={token}"

    with httpx.Client(follow_redirects=True, timeout=30) as c1:
        assert c1.get(url).status_code == 200
    with httpx.Client(follow_redirects=True, timeout=30) as c2:
        assert c2.get(url).status_code == 403


def test_simpan_kunci_lewat_wpcli_menembus_pintu_masuk_asli(wp_site):
    """Menembus WPMGR_Settings::simpan_kunci() lewat pintu masuk sebenarnya.

    `site_terpasang` di atas menulis wpmgr_site_id/secret/dashboard_url
    langsung lewat `wp option update`, memotong jalur simpan_kunci()
    sepenuhnya. Itulah sebabnya bug paling merusak proyek ini bertahan
    selama itu: parser kunci memakai explode(':', $mentah) tanpa limit, dan
    karena dashboard_url memuat ':' (skema https://), SETIAP kunci yang sah
    ditolak -- tidak ada site yang pernah bisa dipasangkan. Ia hanya
    tertangkap karena pengurainya dipisah jadi fungsi murni (urai_kunci) dan
    diuji unit; jalur end-to-end mana pun yang memotong simpan_kunci() tidak
    akan pernah menyentuh bug itu.

    Test ini memanggil simpan_kunci() sungguhan lewat WP-CLI dengan kunci
    yang dihasilkan wpmgr.pairing.kunci_koneksi -- entry point yang sama
    yang dipakai formulir "Simpan dan hubungkan" di halaman pengaturan
    plugin. kirim_konfirmasi() di dalamnya pasti gagal, karena dashboard
    tidak bisa dihubungi dari dalam kontainer; kegagalan itu diharapkan dan
    diperiksa lewat kode error wpmgr_tidak_terhubung. Yang benar-benar
    diuji adalah keempat efek samping SEBELUM panggilan itu -- baris-baris
    inilah yang akan gagal terhadap bug explode() yang asli.
    """
    site_id = str(uuid.uuid4())
    secret = secret_baru()
    dashboard_url = "https://dashboard-e2e-tidak-terjangkau.invalid"
    kunci = kunci_koneksi(site_id, secret, dashboard_url)

    keluaran = wpcli(
        "eval",
        "echo is_wp_error($r = WPMGR_Settings::simpan_kunci('" + kunci + "')) "
        "? 'ERR:' . $r->get_error_code() : 'OK';",
    )
    assert keluaran == "ERR:wpmgr_tidak_terhubung", keluaran

    assert wpcli("option", "get", "wpmgr_site_id") == site_id
    secret_tersimpan = wpcli("option", "get", "wpmgr_secret")
    assert secret_tersimpan == secret
    assert len(secret_tersimpan) == 64
    assert wpcli("option", "get", "wpmgr_dashboard_url") == dashboard_url
    assert wpcli("user", "get", "wpmgr", "--field=roles") == "administrator"
