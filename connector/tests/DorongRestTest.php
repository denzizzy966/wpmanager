<?php
use PHPUnit\Framework\TestCase;

// Permintaan REST tiruan minimal (pola sama seperti TabelRestTest.php):
// hanya butuh get_body(), satu-satunya metode yang dipanggil
// WPMGR_Staging::unggah()/snapshot()/bersihkan().
final class WPMGR_FakeRequestDorongRest {
    private $body;

    public function __construct( $body ) {
        $this->body = $body;
    }

    public function get_body() {
        return $this->body;
    }
}

/**
 * WPMGR_Staging::unggah()/snapshot()/bersihkan() (callback REST) diuji
 * langsung di sini terhadap ABSPATH sungguhan (dibuat bootstrap.php, sama
 * seperti ManifestRestTest.php) -- bagian yang TIDAK menyentuh $wpdb:
 * validasi body, dan cabang 'awal' bernilai palsu/tak ada yang melewati
 * WPMGR_Staging_Manifest::tabel()/WPMGR_Staging_TandaAir::kumpulkan()
 * sepenuhnya (keduanya sudah diuji tuntas di berkas sendiri). Perilaku
 * WPMGR_Staging_Dorong sendiri (unggah/snapshot_berkas/bersihkan/kunci)
 * sudah diuji tuntas lewat DorongTest.php dengan DB tiruan; berkas ini
 * hanya membuktikan penghubung REST-nya (WPMGR_Staging::dorong() dan
 * kawan-kawan) memanggilnya dengan benar dan membentuk respons yang
 * sesuai kontrak (Koreksi #12).
 */
final class DorongRestTest extends TestCase {

    protected function tearDown(): void {
        StagingDasarTest::hapus( rtrim( WP_CONTENT_DIR, '/' ) . '/wpmgr-dorong' );
    }

    public function test_snapshot_menolak_body_bukan_objek(): void {
        foreach ( array( '', '"x"', '123', 'bukan json' ) as $body ) {
            $hasil = WPMGR_Staging::snapshot( new WPMGR_FakeRequestDorongRest( $body ) );
            $this->assertInstanceOf( WP_Error::class, $hasil, $body );
            $this->assertSame( 'wpmgr_staging_permintaan', $hasil->get_error_code() );
            $this->assertSame( array( 'status' => 400 ), $hasil->get_error_data() );
        }
    }

    // ---- Koreksi #12: /staging/snapshot hanya berisi metadata; tabel dan
    // tanda_air hanya disertakan pada permintaan 'awal' (tarik pertama). ----

    public function test_snapshot_tanpa_awal_hanya_berisi_berkas(): void {
        $req   = new WPMGR_FakeRequestDorongRest( json_encode( array( 'paths' => array( 'index.php' ) ) ) );
        $hasil = WPMGR_Staging::snapshot( $req )->get_data();
        $this->assertArrayHasKey( 'berkas', $hasil );
        $this->assertTrue( $hasil['berkas'][0]['ada'] );
        $this->assertArrayNotHasKey( 'tabel', $hasil );
        $this->assertArrayNotHasKey( 'tanda_air', $hasil );
    }

    public function test_snapshot_meneruskan_galat_path_sebagai_wp_error(): void {
        $req   = new WPMGR_FakeRequestDorongRest( json_encode( array( 'paths' => array( '../x' ) ) ) );
        $hasil = WPMGR_Staging::snapshot( $req );
        $this->assertInstanceOf( WP_Error::class, $hasil );
    }

    public function test_bersihkan_tanpa_dorong_id_sah_ditolak_400(): void {
        foreach ( array( '', '{}', 'bukan json', json_encode( array( 'dorong_id' => 'x' ) ) ) as $body ) {
            $hasil = WPMGR_Staging::bersihkan( new WPMGR_FakeRequestDorongRest( $body ) );
            $this->assertInstanceOf( WP_Error::class, $hasil, $body );
            $this->assertSame( 400, $hasil->get_error_data()['status'] );
        }
    }

    public function test_unggah_menolak_paket_tidak_sah(): void {
        $hasil = WPMGR_Staging::unggah( new WPMGR_FakeRequestDorongRest( 'bukan paket biner' ) );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_paket', $hasil->get_error_code() );
    }
}
