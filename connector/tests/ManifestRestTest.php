<?php
use PHPUnit\Framework\TestCase;

// Permintaan REST tiruan minimal (fix round 1, item 5i): hanya butuh
// get_param(), satu-satunya metode yang dipanggil WPMGR_Staging::manifest().
final class WPMGR_FakeRequestManifest {
    private $params;

    public function __construct( array $params = array() ) {
        $this->params = $params;
    }

    public function get_param( $nama ) {
        return isset( $this->params[ $nama ] ) ? $this->params[ $nama ] : null;
    }
}

/**
 * WPMGR_Staging::manifest() (callback REST) diuji langsung di sini (item
 * 5i, fix round 1): validasi kursor 400, dan info hanya di halaman pertama.
 * Berkas terpisah dari ManifestTest.php: PHPUnit (directory-based test
 * suite di phpunit.xml) hanya mengumpulkan SATU kelas test per berkas di
 * proyek ini -- dua kelas TestCase dalam satu berkas membuat kelas kedua
 * diam-diam tidak pernah dijalankan sama sekali.
 */
final class ManifestRestTest extends TestCase {

    protected function setUp(): void {
        WPMGR_Staging_Manifest::$maks_hash          = 52428800;
        WPMGR_Staging_Manifest::$anggaran_hash      = 536870912;
        WPMGR_Staging_Manifest::$maks_bytes_halaman = 6291456;
    }

    public function test_manifest_menolak_kursor_tidak_sah(): void {
        $req   = new WPMGR_FakeRequestManifest( array( 'kursor' => "../x\0" ) );
        $hasil = WPMGR_Staging::manifest( $req );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_path', $hasil->get_error_code() );
        $this->assertSame( array( 'status' => 400 ), $hasil->get_error_data() );
    }

    public function test_manifest_menolak_kursor_bukan_string(): void {
        // Item 5d: get_param() bisa mengembalikan array bila query string
        // dikirim dalam bentuk itu (mis. '?kursor[]=x'); tidak boleh
        // memicu error PHP saat di-cast paksa ke string. Kursor bukan
        // string diperlakukan sebagai '' (halaman pertama), jadi info()
        // ikut terpanggil -- $wpdb tiruan harus tersedia.
        global $wpdb;
        $wpdb  = new WPMGR_FakeWpdbManifest();
        $req   = new WPMGR_FakeRequestManifest( array( 'kursor' => array( 'x' ) ) );
        $hasil = WPMGR_Staging::manifest( $req );
        $this->assertNotInstanceOf( WP_Error::class, $hasil );
    }

    public function test_manifest_info_hanya_di_halaman_pertama(): void {
        global $wpdb;
        $wpdb = new WPMGR_FakeWpdbManifest();

        $req_pertama   = new WPMGR_FakeRequestManifest( array( 'kursor' => '' ) );
        $hasil_pertama = WPMGR_Staging::manifest( $req_pertama );
        $data_pertama  = $hasil_pertama->get_data();
        $this->assertArrayHasKey( 'info', $data_pertama );

        $req_lanjut   = new WPMGR_FakeRequestManifest( array( 'kursor' => 'index.php' ) );
        $hasil_lanjut = WPMGR_Staging::manifest( $req_lanjut );
        $data_lanjut  = $hasil_lanjut->get_data();
        $this->assertArrayNotHasKey( 'info', $data_lanjut );
    }

    public function test_manifest_akar_tidak_terbaca_galat_keras(): void {
        // Item 5e: direktori WordPress yang tidak terbaca sama sekali
        // adalah galat KERAS (500 wpmgr_manifest_akar), bukan manifest
        // kosong yang tampak seolah site itu memang tidak punya berkas.
        // ABSPATH sungguhan (dibuat di bootstrap.php) selalu bisa dibaca,
        // jadi kasus tidak-terbaca diuji langsung lewat
        // WPMGR_Staging_Manifest::akar_bisa_dibaca() (lihat
        // ManifestTest::test_akar_bisa_dibaca) dan pembacaan kode
        // manifest()/akar_bisa_dibaca(); di sini hanya dipastikan
        // permintaan NORMAL tidak ikut ditolak keras.
        global $wpdb;
        $wpdb  = new WPMGR_FakeWpdbManifest();
        $req   = new WPMGR_FakeRequestManifest( array( 'kursor' => '' ) );
        $hasil = WPMGR_Staging::manifest( $req );
        $this->assertNotInstanceOf( WP_Error::class, $hasil );
    }
}
