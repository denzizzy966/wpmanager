<?php
use PHPUnit\Framework\TestCase;

// Permintaan REST tiruan minimal: hanya get_route(), satu-satunya metode yang
// dipanggil WPMGR_REST::minta_tanpa_cache().
final class WPMGR_FakeRequestAntiCache {
    private $route;

    public function __construct( $route ) {
        $this->route = $route;
    }

    public function get_route() {
        return $this->route;
    }
}

/**
 * LiteSpeed Cache dengan opsi "Cache REST API" menyimpan respons connector
 * yang ditandatangani dan menyajikannya basi kepada siapa pun, walaupun header
 * X-LiteSpeed-Cache-Control: no-cache sudah dikirim. Route wpmgr meminta plugin
 * cache sendiri untuk tidak menyimpan respons sejak rest_pre_dispatch.
 */
final class RestAntiCacheTest extends TestCase {

    protected function setUp(): void {
        $GLOBALS['wpmgr_test_aksi'] = array();
    }

    protected function tearDown(): void {
        $GLOBALS['wpmgr_test_aksi'] = array();
    }

    private function aksi_nocache() {
        return array_values( array_filter( $GLOBALS['wpmgr_test_aksi'], function ( $a ) {
            return 'litespeed_control_set_nocache' === $a[0];
        } ) );
    }

    public function test_route_wpmgr_memanggil_api_nocache_litespeed_dan_donotcachepage(): void {
        // Route plugin lain lebih dulu: tidak menyentuh apa pun. DONOTCACHEPAGE
        // hanya bisa didefinisikan sekali per proses, jadi urutannya penting.
        $hasil = WPMGR_REST::minta_tanpa_cache( null, null, new WPMGR_FakeRequestAntiCache( '/wp/v2/posts' ) );
        $this->assertNull( $hasil );
        $this->assertSame( array(), $this->aksi_nocache() );
        $this->assertFalse( defined( 'DONOTCACHEPAGE' ) );

        $hasil = WPMGR_REST::minta_tanpa_cache( null, null, new WPMGR_FakeRequestAntiCache( '/wpmgr/v10/ping' ) );
        $this->assertSame( array(), $this->aksi_nocache() );
        $this->assertFalse( defined( 'DONOTCACHEPAGE' ) );

        $hasil = WPMGR_REST::minta_tanpa_cache( null, null, new WPMGR_FakeRequestAntiCache( '/wpmgr/v1/events' ) );
        $this->assertNull( $hasil );
        $this->assertSame( array( array( 'litespeed_control_set_nocache', 'wpmgr connector' ) ), $this->aksi_nocache() );
        $this->assertTrue( defined( 'DONOTCACHEPAGE' ) && DONOTCACHEPAGE );
    }

    public function test_route_staging_juga_diminta_tanpa_cache(): void {
        WPMGR_REST::minta_tanpa_cache( null, null, new WPMGR_FakeRequestAntiCache( '/wpmgr/v1/staging/manifest' ) );
        $this->assertCount( 1, $this->aksi_nocache() );
    }

    public function test_hasil_pre_dispatch_lain_diteruskan_apa_adanya(): void {
        // Plugin lain yang sudah memutus dispatch (mis. WP_Error) tidak diubah.
        $galat = new WP_Error( 'lain', 'ditolak plugin lain' );
        $this->assertSame( $galat, WPMGR_REST::minta_tanpa_cache( $galat, null, new WPMGR_FakeRequestAntiCache( '/wpmgr/v1/ping' ) ) );
        $this->assertCount( 1, $this->aksi_nocache() );
    }

    public function test_filter_terpasang_di_rest_pre_dispatch(): void {
        $isi = file_get_contents( __DIR__ . '/../wp-manager-connector/wp-manager-connector.php' );
        $this->assertStringContainsString(
            "add_filter( 'rest_pre_dispatch', array( 'WPMGR_REST', 'minta_tanpa_cache' ), 10, 3 );", $isi
        );
    }
}
