<?php
use PHPUnit\Framework\TestCase;

final class SkemaTest extends TestCase {

    public function test_fitur_yang_diumumkan(): void {
        $this->assertSame( array( 'self_update', 'events' ), WPMGR_Skema::fitur( false ) );
        // Self-update bukan pemantauan: tetap tersedia walau pemantauan dimatikan.
        $this->assertSame( array( 'self_update' ), WPMGR_Skema::fitur( true ) );
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
