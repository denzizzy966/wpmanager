<?php
use PHPUnit\Framework\TestCase;

// Stub minimal untuk pastikan()/hapus_semua(), dijaga function_exists/defined
// seperti stub yang sama di TrafficTest.php.
if ( ! defined( 'DAY_IN_SECONDS' ) ) {
    define( 'DAY_IN_SECONDS', 86400 );
}
if ( ! defined( 'WPMGR_VERSI_SKEMA' ) ) {
    define( 'WPMGR_VERSI_SKEMA', 2 );
}
if ( ! defined( 'WPMU_PLUGIN_DIR' ) ) {
    define( 'WPMU_PLUGIN_DIR', sys_get_temp_dir() . '/wpmgr-test-mu-' . getmypid() );
}
if ( ! function_exists( 'get_transient' ) ) {
    function get_transient( $kunci ) {
        return isset( $GLOBALS['wpmgr_test_transient'][ $kunci ] ) ? $GLOBALS['wpmgr_test_transient'][ $kunci ] : false;
    }
}
if ( ! function_exists( 'set_transient' ) ) {
    function set_transient( $kunci, $nilai, $ttl = 0 ) {
        $GLOBALS['wpmgr_test_transient'][ $kunci ] = (string) $nilai;
        return true;
    }
}
if ( ! function_exists( 'wp_mkdir_p' ) ) {
    function wp_mkdir_p( $dir ) {
        return is_dir( $dir ) || mkdir( $dir, 0777, true );
    }
}
if ( ! function_exists( 'delete_option' ) ) {
    function delete_option( $nama ) {
        $GLOBALS['wpmgr_test_opsi_dihapus'][] = $nama;
        return true;
    }
}
if ( ! defined( 'HOUR_IN_SECONDS' ) ) {
    define( 'HOUR_IN_SECONDS', 3600 );
}
// MINOR (review putaran 1, Task 7): pastikan() sekarang juga menjadwalkan
// WPMGR_Staging::HOOK_BERSIHKAN di jalur TANPA migrasi -- stub minimal ini
// hanya mencatat jadwal ke variabel global, cukup untuk diperiksa idempoten
// (wp_next_scheduled) tanpa WP-Cron sungguhan.
if ( ! function_exists( 'wp_next_scheduled' ) ) {
    function wp_next_scheduled( $hook ) {
        return isset( $GLOBALS['wpmgr_test_terjadwal'][ $hook ] ) ? $GLOBALS['wpmgr_test_terjadwal'][ $hook ] : false;
    }
}
if ( ! function_exists( 'wp_schedule_event' ) ) {
    function wp_schedule_event( $waktu, $jadwal, $hook ) {
        $GLOBALS['wpmgr_test_terjadwal'][ $hook ] = $waktu;
        $GLOBALS['wpmgr_test_terjadwal_panggilan'][ $hook ] = isset( $GLOBALS['wpmgr_test_terjadwal_panggilan'][ $hook ] )
            ? $GLOBALS['wpmgr_test_terjadwal_panggilan'][ $hook ] + 1 : 1;
        return true;
    }
}
if ( ! function_exists( 'wp_clear_scheduled_hook' ) ) {
    function wp_clear_scheduled_hook( $hook ) {
        return 0;
    }
}

final class WPMGR_FakeWpdbSkema {
    public $prefix  = 'wp_';
    public $options = 'wp_options';
    public $queries = array();

    public function esc_like( $teks ) {
        return addcslashes( $teks, '_%\\' );
    }

    public function prepare( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        return vsprintf( str_replace( '%s', "'%s'", $sql ), $args );
    }

    public function query( $sql ) {
        $this->queries[] = $sql;
        return true;
    }
}

final class SkemaTest extends TestCase {

    private function berkas_mu() {
        return WPMU_PLUGIN_DIR . '/' . WPMGR_Skema::MU_PLUGIN;
    }

    protected function tearDown(): void {
        if ( file_exists( $this->berkas_mu() ) ) {
            unlink( $this->berkas_mu() );
        }
        if ( is_dir( WPMU_PLUGIN_DIR ) ) {
            @rmdir( WPMU_PLUGIN_DIR );
        }
        $GLOBALS['wpmgr_test_opsi']         = array();
        $GLOBALS['wpmgr_test_transient']    = array();
        $GLOBALS['wpmgr_test_opsi_dihapus'] = array();
        $GLOBALS['wpmgr_test_terjadwal']            = array();
        $GLOBALS['wpmgr_test_terjadwal_panggilan']  = array();
        unset( $GLOBALS['wpdb'] );
    }

    // ---- MINOR (review putaran 1, Task 7): HOOK_BERSIHKAN dijadwalkan
    // juga dari jalur TANPA migrasi (idempoten). ----

    public function test_pastikan_menjadwalkan_bersihkan_dorong_tanpa_migrasi(): void {
        $GLOBALS['wpmgr_test_opsi'][ WPMGR_Skema::OPT_VERSI ] = WPMGR_VERSI_SKEMA;
        $this->assertArrayNotHasKey( WPMGR_Staging::HOOK_BERSIHKAN,
            isset( $GLOBALS['wpmgr_test_terjadwal'] ) ? $GLOBALS['wpmgr_test_terjadwal'] : array() );
        WPMGR_Skema::pastikan();
        $this->assertArrayHasKey( WPMGR_Staging::HOOK_BERSIHKAN, $GLOBALS['wpmgr_test_terjadwal'] );
        $this->assertSame( 1, $GLOBALS['wpmgr_test_terjadwal_panggilan'][ WPMGR_Staging::HOOK_BERSIHKAN ] );
        // Idempoten: panggilan kedua tidak menjadwalkan ULANG (wp_next_scheduled sudah ada).
        WPMGR_Skema::pastikan();
        $this->assertSame( 1, $GLOBALS['wpmgr_test_terjadwal_panggilan'][ WPMGR_Staging::HOOK_BERSIHKAN ] );
    }

    public function test_pastikan_memulihkan_mu_plugin_yang_hilang_sekali_per_hari(): void {
        $GLOBALS['wpmgr_test_opsi'][ WPMGR_Skema::OPT_VERSI ] = WPMGR_VERSI_SKEMA;
        $this->assertFileDoesNotExist( $this->berkas_mu() );

        WPMGR_Skema::pastikan();
        $this->assertFileExists( $this->berkas_mu() );
        $this->assertSame( 'penuh', WPMGR_Skema::mode_penangkap() );

        // Dihapus lagi (mis. oleh plugin keamanan): tidak ditulis ulang di
        // setiap request selama transient jeda masih ada.
        unlink( $this->berkas_mu() );
        WPMGR_Skema::pastikan();
        $this->assertFileDoesNotExist( $this->berkas_mu() );

        $GLOBALS['wpmgr_test_transient'] = array();
        WPMGR_Skema::pastikan();
        $this->assertFileExists( $this->berkas_mu() );
    }

    public function test_hapus_semua_menghapus_transient_wpmgr(): void {
        $wpdb            = new WPMGR_FakeWpdbSkema();
        $GLOBALS['wpdb'] = $wpdb;

        WPMGR_Skema::hapus_semua();

        $semua = implode( "\n", $wpdb->queries );
        $this->assertStringContainsString( 'option_name LIKE \'\\_transient\\_wpmgr\\_%\'', $semua );
        $this->assertStringContainsString( 'option_name LIKE \'\\_transient\\_timeout\\_wpmgr\\_%\'', $semua );
    }

    public function test_fitur_yang_diumumkan(): void {
        $this->assertSame( array( 'self_update', 'events', 'traffic' ), WPMGR_Skema::fitur( false ) );
        // Self-update bukan pemantauan: tetap tersedia walau pemantauan dimatikan.
        $this->assertSame( array( 'self_update' ), WPMGR_Skema::fitur( true ) );
        $this->assertSame( array( 'self_update', 'staging' ), WPMGR_Skema::fitur( true, true ) );
    }

    public function test_perlu_migrasi_hanya_bila_versi_berbeda(): void {
        $this->assertTrue( WPMGR_Skema::perlu_migrasi( 0, 1 ) );
        $this->assertTrue( WPMGR_Skema::perlu_migrasi( '1', 2 ) );
        $this->assertFalse( WPMGR_Skema::perlu_migrasi( '1', 1 ) );
        $this->assertFalse( WPMGR_Skema::perlu_migrasi( 1, 1 ) );
    }

    public function test_sql_tabel_memuat_kelima_tabel(): void {
        $sql = implode( "\n", WPMGR_Skema::sql_tabel( 'wp_', 'DEFAULT CHARSET=utf8mb4' ) );
        foreach ( array( 'wp_wpmgr_errors', 'wp_wpmgr_logins', 'wp_wpmgr_login_gagal',
                         'wp_wpmgr_traffic', 'wp_wpmgr_pengunjung' ) as $tabel ) {
            $this->assertStringContainsString( 'CREATE TABLE ' . $tabel . ' (', $sql );
        }
    }

    public function test_sql_tabel_mengikuti_format_dbdelta(): void {
        // dbDelta() mewajibkan dua spasi setelah PRIMARY KEY; dengan satu
        // spasi ia gagal mengenali primary key dan mencoba menambahkannya
        // lagi di setiap migrasi.
        foreach ( WPMGR_Skema::sql_tabel( 'wp_', '' ) as $satu ) {
            $this->assertMatchesRegularExpression( '/PRIMARY KEY  \(/', $satu );
        }
    }

    public function test_kunci_traffic_muat_di_batas_indeks_lama(): void {
        // (tanggal 3 byte + dimensi 10*4 + kunci 180*4) = 763 byte < 767.
        $sql = implode( "\n", WPMGR_Skema::sql_tabel( 'wp_', '' ) );
        $this->assertStringContainsString( 'kunci varchar(180) NOT NULL', $sql );
        $this->assertStringContainsString( 'dimensi varchar(10) NOT NULL', $sql );
    }

    public function test_hanya_route_wpmgr_yang_diberi_header_anti_cache(): void {
        $this->assertTrue( WPMGR_REST::perlu_anti_cache( '/wpmgr/v1/ping' ) );
        $this->assertTrue( WPMGR_REST::perlu_anti_cache( '/wpmgr/v1/events' ) );
        $this->assertFalse( WPMGR_REST::perlu_anti_cache( '/wp/v2/posts' ) );
        $this->assertFalse( WPMGR_REST::perlu_anti_cache( '/wpmgr/v10/ping' ) );
        $this->assertFalse( WPMGR_REST::perlu_anti_cache( '' ) );
    }

    public function test_isi_mu_plugin_punya_semua_penjaga(): void {
        $isi = WPMGR_Skema::isi_mu_plugin();
        $this->assertStringStartsWith( '<?php', $isi );
        $this->assertStringContainsString( 'WPMGR_DISABLE_MONITORING', $isi );
        $this->assertStringContainsString( "'active_plugins'", $isi );
        $this->assertStringContainsString( "'wp-manager-connector/wp-manager-connector.php'", $isi );
        $this->assertStringContainsString( 'is_readable', $isi );
        $this->assertStringContainsString( 'WPMGR_Penangkap::pasang()', $isi );
    }
}
