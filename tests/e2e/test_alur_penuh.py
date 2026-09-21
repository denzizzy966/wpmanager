import subprocess
import time
import uuid

import pytest

from wpmgr.crypto import dekripsi_secret, secret_baru
from wpmgr.errors import TRANSIENT, UPGRADE_FAILED, SiteError
from wpmgr.jobs.queue import buat_job
from wpmgr.models import JobStatus, JobType, PackageType, SitePackage, SiteStatus
from wpmgr.pairing import buat_site, kunci_koneksi
from wpmgr.site_client import SiteClient
from wpmgr.worker import proses_satu

from .conftest import _wpcli_status, wpcli

MU_PLUGIN_BLOKIR = "/var/www/html/wp-content/mu-plugins/wpmgr-uji-blokir-wporg.php"
ISI_MU_PLUGIN_BLOKIR = """<?php
// Dipasang test e2e: mensimulasikan wordpress.org tidak terjangkau selama
// option wpmgr_uji_blokir_wporg bernilai truthy.
add_filter( 'pre_http_request', function ( $pre, $args, $url ) {
    if ( get_option( 'wpmgr_uji_blokir_wporg' ) && false !== strpos( $url, 'api.wordpress.org' ) ) {
        return new WP_Error( 'http_request_failed', 'diblokir test e2e' );
    }
    return $pre;
}, 10, 3 );
"""


def _tulis_di_kontainer(path: str, isi: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "sh", "-c",
         f"mkdir -p \"$(dirname '{path}')\" && cat > '{path}'"],
        input=isi, text=True, capture_output=True, check=True,
    )


def _hapus_di_kontainer(path: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "rm", "-f", path],
        capture_output=True, check=False,
    )

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


def test_scan_memaksa_refresh_transient_update_plugins(sesi, site_terpasang):
    """segarkan() menyetel `last_checked` site transient update_plugins ke 0
    sebelum memanggil wp_update_plugins(), tepatnya karena fungsi itu sendiri
    tidak menerima argumen paksa dan langsung kembali tanpa berbuat apa pun
    bila transient-nya berumur kurang dari 12 jam.

    test_scan_mengisi_inventaris hanya membuktikan sebuah inventaris kembali --
    itu akan tetap terjadi bahkan bila segarkan() tidak memaksa apa pun sama
    sekali, semata-mata karena instalasi ini baru dan transient-nya belum ada.
    Properti yang benar-benar menjadi taruhan adalah: pemindaian KEDUA, yang
    dijalankan segera setelah yang pertama (jauh di bawah jendela 12 jam),
    tetap memicu pemeriksaan ulang yang nyata. Tanpa pemaksaan di segarkan(),
    wp_update_plugins() pada pemindaian kedua akan diam-diam kembali lebih
    awal dan `last_checked` tidak akan pernah maju -- gejala yang tidak akan
    pernah terlihat lewat balasan API kita sendiri, hanya lewat transient
    WordPress yang sebenarnya.
    """
    import time

    buat_job(sesi, site_terpasang.id, JobType.scan_site)
    assert proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi)) is True
    diperiksa_pertama = int(
        wpcli("eval", "echo get_site_transient('update_plugins')->last_checked;")
    )

    time.sleep(2)

    buat_job(sesi, site_terpasang.id, JobType.scan_site)
    assert proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi)) is True
    diperiksa_kedua = int(
        wpcli("eval", "echo get_site_transient('update_plugins')->last_checked;")
    )

    assert diperiksa_kedua > diperiksa_pertama


def test_scan_saat_wordpress_org_tak_terjangkau_mempertahankan_versi_tersedia(
    sesi, site_terpasang
):
    """R58: pemeriksaan update yang gagal tidak boleh menghapus apa yang sudah
    diketahui.

    Dulu segarkan() menghapus transient update_plugins lebih dulu. Bila
    permintaan ke api.wordpress.org lalu gagal, wp_update_plugins() kembali
    lebih awal dan transient tertinggal tanpa `response` sama sekali -- setiap
    plugin di site itu dilaporkan "tidak ada update". Itu bukan data basi,
    itu data salah: dashboard menyembunyikan update keamanan yang tertunda
    justru pada saat koneksi site ke wordpress.org sedang bermasalah.
    """
    wpcli("plugin", "install", "hello-dolly", "--version=1.6", "--force")
    _tulis_di_kontainer(MU_PLUGIN_BLOKIR, ISI_MU_PLUGIN_BLOKIR)
    try:
        buat_job(sesi, site_terpasang.id, JobType.scan_site)
        assert proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi)) is True
        baris = sesi.query(SitePackage).filter_by(
            site_id=site_terpasang.id, slug="hello-dolly/hello.php").one()
        tersedia = baris.versi_tersedia
        assert tersedia is not None

        wpcli("option", "update", "wpmgr_uji_blokir_wporg", "1")
        buat_job(sesi, site_terpasang.id, JobType.scan_site)
        assert proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi)) is True

        sesi.refresh(baris)
        assert baris.versi_tersedia == tersedia
    finally:
        _wpcli_status("option", "delete", "wpmgr_uji_blokir_wporg")
        _hapus_di_kontainer(MU_PLUGIN_BLOKIR)


def test_redirect_permalink_plain_menghasilkan_pesan_yang_bisa_didiagnosis(sesi, site_terpasang):
    """Site klien berpermalink 'Plain' (bawaan instalasi WordPress mana pun)
    membuat setiap permintaan /wp-json/... di-301 oleh WordPress sendiri
    sebelum pernah sampai ke WPMGR_REST::guard(). klasifikasi_respons()
    memetakan 3xx yang tak tertangani menjadi bad_response, dan badan respons
    redirect semacam itu nyaris selalu kosong -- tanpa penanganan khusus,
    operator hanya melihat "bad_response" tanpa isi apa pun, lalu mencurigai
    tiga tempat yang salah (secret, firewall, plugin nonaktif), masing-masing
    berharga satu putaran dukungan dengan klien sebelum permalink bahkan
    terpikirkan.

    Test ini membuktikan _panggil() di site_client.py menyisipkan tujuan
    redirect yang sesungguhnya dan penyebab paling mungkin ke pesan error itu
    sendiri, ketimbang membiarkan operator menebak dari body yang kosong.
    """
    from wpmgr.errors import SiteError

    wpcli("rewrite", "structure", "", "--hard")
    wpcli("rewrite", "flush", "--hard")
    try:
        with pytest.raises(SiteError) as exc:
            _klien_http(site_terpasang, sesi).ping()
        pesan = exc.value.pesan
        assert "permalink" in pesan.lower()
        assert f"{site_terpasang.url}/wp-json/wpmgr/v1/ping/" in pesan
    finally:
        # Harus terjadi bahkan bila asersi di atas gagal -- kalau tidak,
        # setiap test sesudah ini di file yang sama akan ikut gagal dengan
        # redirect yang sama, menyamarkan kegagalan test ini sendiri di
        # balik kegagalan berantai yang tidak berhubungan.
        wpcli("rewrite", "structure", "/%postname%/", "--hard")
        wpcli("rewrite", "flush", "--hard")


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


def test_update_plugin_aktif_tetap_aktif(sesi, site_terpasang):
    """R52 (C1): update lewat dashboard tidak boleh menonaktifkan plugin.

    Plugin_Upgrader::upgrade() memasang deactivate_plugin_before_upgrade,
    yang menonaktifkan plugin untuk setiap request yang bukan WP-cron dan
    tidak pernah mengaktifkannya kembali (layar update wp-admin melakukannya
    lewat redirect terpisah yang tidak ada di jalur REST). Setiap update
    plugin aktif lewat dashboard dulu mematikan plugin itu di site client
    produksi -- WooCommerce, plugin keamanan, apa pun -- sambil melapor
    sukses.
    """
    wpcli("plugin", "install", "hello-dolly", "--version=1.6", "--force")
    wpcli("plugin", "activate", "hello-dolly")
    try:
        buat_job(sesi, site_terpasang.id, JobType.scan_site)
        proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi))
        baris = sesi.query(SitePackage).filter_by(
            site_id=site_terpasang.id, slug="hello-dolly/hello.php").one()
        assert baris.versi_tersedia is not None
        target = baris.versi_tersedia

        job = buat_job(sesi, site_terpasang.id, JobType.update_package, {
            "tipe": "plugin", "slug": "hello-dolly/hello.php",
            "dari_versi": "1.6", "ke_versi": target,
        })
        assert proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi)) is True

        sesi.refresh(job)
        assert job.status == JobStatus.success, job.error
        assert wpcli("plugin", "get", "hello-dolly", "--field=version") == target
        assert _wpcli_status("plugin", "is-active", "hello-dolly") == 0, (
            "hello-dolly dinonaktifkan oleh update"
        )
        # Lock R56 dilepas di `finally`; bila tertinggal, update dan scan
        # berikutnya untuk site ini tertahan 15 menit.
        assert _wpcli_status("option", "get", "wpmgr_update.lock") != 0
    finally:
        _wpcli_status("plugin", "deactivate", "hello-dolly")


def test_update_ke_versi_yang_tidak_ditawarkan_menjadi_upgrade_failed(sesi, site_terpasang):
    """R59-c: upgrader yang "berhasil" tanpa memasang versi yang diminta
    bukan sukses.

    hello-dolly sudah di versi terbaru dari test sebelumnya, jadi
    wordpress.org tidak menawarkan apa pun untuknya. bulk_upgrade()
    mengembalikan `true` untuk plugin seperti itu -- nilai yang sama yang
    secara sekilas terlihat seperti sukses. Dashboard harus menerima 409
    `wpmgr_tidak_ada_update` dan menandai job gagal final, bukan sukses
    dengan versi karangan dan bukan diulang tiga kali.
    """
    job = buat_job(sesi, site_terpasang.id, JobType.update_package, {
        "tipe": "plugin", "slug": "hello-dolly/hello.php",
        "dari_versi": None, "ke_versi": "99.0",
    })
    assert proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi)) is True

    sesi.refresh(job)
    assert job.status == JobStatus.failed
    assert job.error_class == UPGRADE_FAILED
    assert "99.0" in job.error
    assert job.attempts == 1
    assert _wpcli_status("option", "get", "wpmgr_update.lock") != 0


def test_lock_update_membuat_scan_dan_update_dijadwal_ulang(sesi, site_terpasang):
    """R56: selama lock `wpmgr_update` dipegang, /inventory dan /update sama-
    sama membalas 409 `wpmgr_sibuk`, yang dashboard perlakukan sebagai
    transient.

    Lock-nya dipasang lewat wp-cli dengan format yang sama yang ditulis
    WP_Upgrader::create_lock() (timestamp Unix), mensimulasikan proses PHP
    lain yang sedang menjalankan upgrade -- mis. permintaan /update yang
    koneksinya sudah diputus dashboard setelah 180 detik tetapi PHP-nya
    masih menyalin berkas.
    """
    status_awal = site_terpasang.status
    wpcli("option", "update", "wpmgr_update.lock", str(int(time.time())))
    try:
        job = buat_job(sesi, site_terpasang.id, JobType.scan_site)
        assert proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi)) is True
        sesi.refresh(job)
        assert job.status == JobStatus.pending
        assert job.error_class == TRANSIENT
        assert "wpmgr_sibuk" in job.error
        # R54: percobaan pertama yang masih akan diulang tidak menandai site
        # unreachable.
        sesi.refresh(site_terpasang)
        assert site_terpasang.status == status_awal
        assert site_terpasang.status != SiteStatus.unreachable

        with pytest.raises(SiteError) as exc:
            _klien_http(site_terpasang, sesi).update("plugin", "hello-dolly/hello.php", "99.0")
        assert exc.value.error_class == TRANSIENT
        assert "wpmgr_sibuk" in exc.value.pesan
    finally:
        _wpcli_status("option", "delete", "wpmgr_update.lock")

    # Lock yang sudah kedaluwarsa (lebih tua dari 15 menit) tidak menahan
    # apa pun -- persis seperti create_lock() menafsirkannya.
    wpcli("option", "update", "wpmgr_update.lock", str(int(time.time()) - 16 * 60))
    try:
        assert "plugins" in _klien_http(site_terpasang, sesi).inventory()
    finally:
        _wpcli_status("option", "delete", "wpmgr_update.lock")


def test_versi_core_di_disk_sama_dengan_versi_terpasang(wp_site):
    """Update core membaca ulang versinya dari wp-includes/version.php, bukan
    dari global $wp_version yang tidak pernah diperbarui update_core() di
    request yang sama. Test ini memastikan pembaca berkas itu sendiri benar.
    """
    assert wpcli("eval", "echo WPMGR_Updater::versi_core_di_disk();") == wpcli("core", "version")


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
