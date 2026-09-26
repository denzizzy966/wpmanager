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
 * wpdb tiruan gabungan (fix I4, review putaran 1): cukup untuk
 * WPMGR_Staging_Manifest::tabel()/pk() DAN WPMGR_Staging_TandaAir::kumpulkan()
 * sekaligus -- keduanya dipanggil bersamaan oleh snapshot() saat 'awal'
 * benar. $gagal_pola mensimulasikan $wpdb->last_error sungguhan (kosong di
 * awal setiap panggilan, terisi hanya saat query yang cocok pola gagal).
 */
final class WPMGR_FakeWpdbSnapshot {
    public $prefix       = 'wp_';
    public $charset      = 'utf8mb4';
    public $posts        = 'wp_posts';
    public $comments     = 'wp_comments';
    public $users        = 'wp_users';
    public $last_error   = '';
    public $gagal_pola   = null;
    public $tabel_status = array();
    public $baris        = array();

    public function esc_like( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function prepare( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        $sql  = str_replace( array( '%s', '%d' ), array( "'%s'", '%d' ), $sql );
        return vsprintf( $sql, $args );
    }

    private function gagal( $sql ) {
        if ( null !== $this->gagal_pola && false !== strpos( $sql, $this->gagal_pola ) ) {
            $this->last_error = 'galat tiruan';
            return true;
        }
        $this->last_error = '';
        return false;
    }

    public function get_results( $sql, $format = null ) {
        if ( $this->gagal( $sql ) ) {
            return array();
        }
        if ( false !== strpos( $sql, 'SHOW TABLE STATUS' ) ) {
            return $this->tabel_status;
        }
        return array();
    }

    public function get_row( $sql, $format = null ) {
        if ( $this->gagal( $sql ) ) {
            return null;
        }
        foreach ( $this->baris as $pola => $v ) {
            if ( false !== strpos( $sql, $pola ) ) {
                return $v;
            }
        }
        return array( 'maks' => null, 'jumlah' => '0', 'diubah' => null );
    }

    public function get_var( $sql ) {
        if ( $this->gagal( $sql ) ) {
            return null;
        }
        return null; // tidak ada tabel wc_orders/form tambahan di daftar.
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

    // ---- MINOR (review putaran 1): 'awal' diurai ketat sebagai boolean
    // (true/1 saja), bukan ! empty(). ----

    public function test_snapshot_awal_bukan_boolean_diabaikan(): void {
        global $wpdb;
        $wpdb  = new WPMGR_FakeWpdbSnapshot();
        $req   = new WPMGR_FakeRequestDorongRest( json_encode( array( 'paths' => array(), 'awal' => 'ya' ) ) );
        $hasil = WPMGR_Staging::snapshot( $req )->get_data();
        $this->assertArrayNotHasKey( 'tabel', $hasil );
        $this->assertArrayNotHasKey( 'tanda_air', $hasil );
    }

    public function test_snapshot_awal_menyertakan_tabel_dan_tanda_air(): void {
        global $wpdb;
        $wpdb                = new WPMGR_FakeWpdbSnapshot();
        $wpdb->tabel_status  = array(
            array( 'Name' => 'wp_posts', 'Rows' => '1', 'Data_length' => '1', 'Index_length' => '0', 'Engine' => 'InnoDB' ),
        );
        $req   = new WPMGR_FakeRequestDorongRest( json_encode( array( 'paths' => array(), 'awal' => true ) ) );
        $hasil = WPMGR_Staging::snapshot( $req )->get_data();
        $this->assertArrayHasKey( 'tabel', $hasil );
        $this->assertSame( 'wp_posts', $hasil['tabel'][0]['nama'] );
        $this->assertArrayHasKey( 'tanda_air', $hasil );
        $this->assertArrayHasKey( 'sumber', $hasil['tanda_air'] );
    }

    // ---- Fix I4 (review putaran 1, Penting): snapshot() draf awal
    // melaporkan galat basis data sebagai SUKSES -- daftar tabel kosong
    // (Manifest::tabel() sendiri tidak memeriksa last_error) atau tanda air
    // yang sebenarnya WP_Error disimpan begitu saja ke hasil. ----

    public function test_snapshot_awal_galat_tabel_menghasilkan_500(): void {
        global $wpdb;
        $wpdb             = new WPMGR_FakeWpdbSnapshot();
        $wpdb->gagal_pola = 'SHOW TABLE STATUS';
        $req              = new WPMGR_FakeRequestDorongRest( json_encode( array( 'paths' => array(), 'awal' => true ) ) );
        $hasil            = WPMGR_Staging::snapshot( $req );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 500, $hasil->get_error_data()['status'] );
    }

    public function test_snapshot_awal_galat_tanda_air_menghasilkan_500(): void {
        global $wpdb;
        $wpdb             = new WPMGR_FakeWpdbSnapshot();
        $wpdb->gagal_pola = 'FROM wp_posts';
        $req              = new WPMGR_FakeRequestDorongRest( json_encode( array( 'paths' => array(), 'awal' => true ) ) );
        $hasil            = WPMGR_Staging::snapshot( $req );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 500, $hasil->get_error_data()['status'] );
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
