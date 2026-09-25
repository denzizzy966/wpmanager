<?php
use PHPUnit\Framework\TestCase;

final class IpTest extends TestCase {

    private $cf = array( '173.245.48.0/20', '104.16.0.0/13', '2400:cb00::/32' );

    public function test_default_remote_addr(): void {
        $this->assertSame( array( 'ip' => '203.0.113.9', 'lewat_cloudflare' => false ),
            WPMGR_IP::tentukan( array( 'REMOTE_ADDR' => '203.0.113.9' ), false, $this->cf ) );
    }

    public function test_cf_connecting_ip_dipercaya_hanya_dari_rentang_cloudflare(): void {
        $dari_cf = array( 'REMOTE_ADDR' => '104.16.1.2', 'HTTP_CF_CONNECTING_IP' => '198.51.100.7' );
        $this->assertSame( array( 'ip' => '198.51.100.7', 'lewat_cloudflare' => true ),
            WPMGR_IP::tentukan( $dari_cf, false, $this->cf ) );

        $palsu = array( 'REMOTE_ADDR' => '203.0.113.9', 'HTTP_CF_CONNECTING_IP' => '1.1.1.1' );
        $this->assertSame( array( 'ip' => '203.0.113.9', 'lewat_cloudflare' => false ),
            WPMGR_IP::tentukan( $palsu, false, $this->cf ) );
    }

    public function test_cloudflare_ipv6(): void {
        $s = array( 'REMOTE_ADDR' => '2400:cb00:2049:1::a29f:1804', 'HTTP_CF_CONNECTING_IP' => '2001:db8::1' );
        $this->assertSame( '2001:db8::1', WPMGR_IP::tentukan( $s, false, $this->cf )['ip'] );
    }

    public function test_cf_connecting_ip_tidak_valid_diabaikan(): void {
        $s = array( 'REMOTE_ADDR' => '104.16.1.2', 'HTTP_CF_CONNECTING_IP' => '<script>' );
        $this->assertSame( array( 'ip' => '104.16.1.2', 'lewat_cloudflare' => false ),
            WPMGR_IP::tentukan( $s, false, $this->cf ) );
    }

    public function test_xff_hanya_bila_setelan_aktif_dan_paling_kanan_yang_publik(): void {
        $s = array( 'REMOTE_ADDR' => '10.0.0.5', 'HTTP_X_FORWARDED_FOR' => '1.2.3.4, 198.51.100.20, 10.0.0.9' );
        $this->assertSame( '10.0.0.5', WPMGR_IP::tentukan( $s, false, $this->cf )['ip'] );
        $this->assertSame( '198.51.100.20', WPMGR_IP::tentukan( $s, true, $this->cf )['ip'] );
    }

    public function test_remote_addr_tidak_valid_menghasilkan_null(): void {
        $this->assertNull( WPMGR_IP::tentukan( array(), false, $this->cf )['ip'] );
        $this->assertNull( WPMGR_IP::tentukan( array( 'REMOTE_ADDR' => 'bukan-ip' ), false, $this->cf )['ip'] );
    }

    public function test_dalam_rentang(): void {
        $this->assertTrue( WPMGR_IP::dalam_rentang( '173.245.63.255', '173.245.48.0/20' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( '173.245.64.0', '173.245.48.0/20' ) );
        $this->assertTrue( WPMGR_IP::dalam_rentang( '104.23.255.255', '104.16.0.0/13' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( '2400:cb00::1', '104.16.0.0/13' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( 'x', '104.16.0.0/13' ) );
    }

    public function test_daftar_cloudflare_bawaan_berisi_cidr_sah(): void {
        $daftar = require __DIR__ . '/../wp-manager-connector/includes/cloudflare-ip.php';
        $this->assertGreaterThan( 10, count( $daftar ) );
        foreach ( $daftar as $cidr ) {
            list( $net, $bit ) = explode( '/', $cidr );
            $this->assertTrue( WPMGR_IP::valid( $net ), $cidr );
            $this->assertMatchesRegularExpression( '/^\d{1,3}$/', $bit );
        }
    }
}
