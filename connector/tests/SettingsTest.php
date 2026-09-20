<?php
use PHPUnit\Framework\TestCase;

final class SettingsTest extends TestCase {

    /**
     * Membangun kunci koneksi persis seperti cara dashboard melakukannya:
     * base64url( site_id . ':' . secret_hex . ':' . dashboard_url ), tanpa padding.
     */
    private function bungkus_kunci( string $mentah ): string {
        return rtrim( strtr( base64_encode( $mentah ), '+/', '-_' ), '=' );
    }

    private function kunci_valid( string $site_id = 'site-42', string $dashboard = 'https://dash.example.com/' ): string {
        $secret = str_repeat( 'a1', 32 ); // 64 karakter hex
        return $this->bungkus_kunci( $site_id . ':' . $secret . ':' . $dashboard );
    }

    public function test_kunci_valid_mengembalikan_tiga_bagian(): void {
        $secret = str_repeat( 'a1', 32 );
        $hasil  = WPMGR_Settings::urai_kunci( $this->kunci_valid( 'site-42', 'https://dash.example.com/' ) );

        $this->assertIsArray( $hasil );
        $this->assertSame( array( 'site-42', $secret, 'https://dash.example.com' ), $hasil );
    }

    public function test_base64url_rusak_mengembalikan_wp_error(): void {
        $hasil = WPMGR_Settings::urai_kunci( '!!!tidak-valid***' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
    }

    public function test_dua_bagian_mengembalikan_wp_error(): void {
        $secret = str_repeat( 'a1', 32 );
        $kunci  = $this->bungkus_kunci( 'site-42:' . $secret );
        $hasil  = WPMGR_Settings::urai_kunci( $kunci );
        $this->assertInstanceOf( WP_Error::class, $hasil );
    }

    public function test_empat_bagian_mengembalikan_wp_error(): void {
        $secret = str_repeat( 'a1', 32 );
        $kunci  = $this->bungkus_kunci( 'site-42:' . $secret . ':ekstra:https://dash.example.com' );
        $hasil  = WPMGR_Settings::urai_kunci( $kunci );
        $this->assertInstanceOf( WP_Error::class, $hasil );
    }

    public function test_secret_terlalu_pendek_mengembalikan_wp_error(): void {
        $secret_pendek = str_repeat( 'a', 63 );
        $kunci         = $this->bungkus_kunci( 'site-42:' . $secret_pendek . ':https://dash.example.com' );
        $hasil         = WPMGR_Settings::urai_kunci( $kunci );
        $this->assertInstanceOf( WP_Error::class, $hasil );
    }

    public function test_secret_bukan_hex_mengembalikan_wp_error(): void {
        $secret_kapital = strtoupper( str_repeat( 'a1', 31 ) ) . 'ZZ';
        $kunci          = $this->bungkus_kunci( 'site-42:' . $secret_kapital . ':https://dash.example.com' );
        $hasil          = WPMGR_Settings::urai_kunci( $kunci );
        $this->assertInstanceOf( WP_Error::class, $hasil );
    }

    public function test_dashboard_http_mengembalikan_wp_error(): void {
        $secret = str_repeat( 'a1', 32 );
        $kunci  = $this->bungkus_kunci( 'site-42:' . $secret . ':http://dash.example.com' );
        $hasil  = WPMGR_Settings::urai_kunci( $kunci );
        $this->assertInstanceOf( WP_Error::class, $hasil );
    }

    public function test_spasi_di_sekitar_kunci_ditoleransi(): void {
        $secret = str_repeat( 'a1', 32 );
        $kunci  = "  \n" . $this->kunci_valid( 'site-42', 'https://dash.example.com' ) . "\t\n  ";
        $hasil  = WPMGR_Settings::urai_kunci( $kunci );

        $this->assertIsArray( $hasil );
        $this->assertSame( array( 'site-42', $secret, 'https://dash.example.com' ), $hasil );
    }
}
