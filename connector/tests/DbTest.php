<?php
use PHPUnit\Framework\TestCase;

/**
 * WPMGR_Staging_Db::prefix_asing()/tabel_milik_site() (fix I2, review
 * putaran 1): helper bersama dipakai WPMGR_Staging_Manifest::tabel(),
 * daftar tabel snapshot, jurnal tabel dorong, dan WPMGR_Staging_Sql::ubah().
 */
/**
 * Meniru wpdb::prepare() WP >= 4.8.3: '%' di argumen diganti placeholder
 * acak, dan hanya remove_placeholder_escape() (dipanggil wpdb::query())
 * yang mengembalikannya.
 */
final class WPMGR_FakeWpdbPlaceholder {
    const PH = '{9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08}';

    public function esc_like( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function prepare( $sql ) {
        $args = array_map( function ( $a ) {
            return str_replace( '%', self::PH, addslashes( (string) $a ) );
        }, array_slice( func_get_args(), 1 ) );
        return vsprintf( str_replace( '%s', "'%s'", $sql ), $args );
    }

    public function remove_placeholder_escape( $q ) {
        return str_replace( self::PH, '%', $q );
    }
}

final class DbTest extends TestCase {

    public function test_prefix_asing_terdeteksi_lewat_tabel_opsi_sendiri(): void {
        $semua = array( 'wp_posts', 'wp_options', 'wp_abc_posts', 'wp_abc_options' );
        $this->assertSame( array( 'wp_abc_' ), WPMGR_Staging_Db::prefix_asing( $semua, 'wp_' ) );
    }

    public function test_prefix_asing_kosong_tanpa_tabel_opsi_sendiri(): void {
        // 'wp_abc_posts' sendirian, TANPA 'wp_abc_options' -- tidak cukup
        // bukti itu site lain (bisa saja sekadar tabel plugin site ini).
        $semua = array( 'wp_posts', 'wp_options', 'wp_abc_posts' );
        $this->assertSame( array(), WPMGR_Staging_Db::prefix_asing( $semua, 'wp_' ) );
    }

    public function test_tabel_milik_site_membuang_tabel_prefix_asing(): void {
        $semua  = array( 'wp_posts', 'wp_options', 'wp_abc_posts', 'wp_abc_options', 'lain_x' );
        $hasil  = WPMGR_Staging_Db::tabel_milik_site( $semua, 'wp_' );
        $this->assertSame( array( 'wp_posts', 'wp_options' ), $hasil );
    }

    /**
     * wpdb::prepare() (WP >= 4.8.3) mengganti setiap '%' di dalam argumen
     * dengan placeholder acak '{hash}'; hanya $wpdb->query() yang
     * mengembalikannya. kueri() memakai mysqli_query() langsung, jadi tanpa
     * pengembalian di siapkan() `LIKE 'id|%'` terkirim sebagai
     * `LIKE 'id|{hash}'` dan tidak cocok apa pun: lepas_kunci() diam-diam
     * tidak melepas kunci dorong (ditemukan e2e Task 22).
     */
    public function test_siapkan_mengembalikan_persen_di_argumen_seperti_wpdb_query(): void {
        $db  = new WPMGR_Staging_Db( new WPMGR_FakeWpdbPlaceholder() );
        $sql = $db->siapkan( 'DELETE FROM wp_options WHERE option_name = %s AND option_value LIKE %s',
            'wpmgr_dorong_kunci', $db->suka( 'abc|' ) . '%' );
        $this->assertSame( "DELETE FROM wp_options WHERE option_name = 'wpmgr_dorong_kunci' AND option_value LIKE 'abc|%'", $sql );
    }

    public function test_siapkan_tanpa_remove_placeholder_escape_memakai_hasil_prepare_apa_adanya(): void {
        // WordPress lama (< 4.8.3) tidak punya placeholder escape sama sekali.
        $db = new WPMGR_Staging_Db( new WPMGR_FakeWpdbManifest() );
        $this->assertSame( "SELECT 'a%'", $db->siapkan( 'SELECT %s', 'a%' ) );
    }

    public function test_tabel_milik_site_tanpa_tumpang_tindih_tidak_berubah(): void {
        $semua = array( 'wp_posts', 'wp_options', 'wp_woocommerce_x' );
        $this->assertSame( $semua, WPMGR_Staging_Db::tabel_milik_site( $semua, 'wp_' ) );
    }
}
