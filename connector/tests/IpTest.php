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
            $maks = ( false !== strpos( $net, ':' ) ) ? 128 : 32;
            $this->assertLessThanOrEqual( $maks, (int) $bit, $cidr );
        }
    }

    // --- Fix round 1: temuan review ---------------------------------------

    public function test_xff_diabaikan_bila_remote_addr_sudah_publik(): void {
        // Penyerang yang menyentuh origin langsung (REMOTE_ADDR publik) tidak
        // boleh bisa menitipkan XFF palsu untuk memalsukan IP-nya di log.
        $s = array( 'REMOTE_ADDR' => '203.0.113.50', 'HTTP_X_FORWARDED_FOR' => '8.8.8.8' );
        $this->assertSame( array( 'ip' => '203.0.113.50', 'lewat_cloudflare' => false ),
            WPMGR_IP::tentukan( $s, true, $this->cf ) );
    }

    public function test_xff_entri_dengan_port_dilepas(): void {
        $ipv4 = array( 'REMOTE_ADDR' => '10.0.0.5', 'HTTP_X_FORWARDED_FOR' => '8.8.8.8, 198.51.100.20:4711' );
        $this->assertSame( '198.51.100.20', WPMGR_IP::tentukan( $ipv4, true, $this->cf )['ip'] );

        $ipv6_port = array( 'REMOTE_ADDR' => '10.0.0.5', 'HTTP_X_FORWARDED_FOR' => '8.8.8.8, [2001:4860:4860::8888]:4711' );
        $this->assertSame( '2001:4860:4860::8888', WPMGR_IP::tentukan( $ipv6_port, true, $this->cf )['ip'] );

        $ipv6_tanpa_port = array( 'REMOTE_ADDR' => '10.0.0.5', 'HTTP_X_FORWARDED_FOR' => '8.8.8.8, [2001:4860:4860::8844]' );
        $this->assertSame( '2001:4860:4860::8844', WPMGR_IP::tentukan( $ipv6_tanpa_port, true, $this->cf )['ip'] );
    }

    public function test_xff_entri_kanan_tak_terurai_jatuh_ke_remote_addr(): void {
        // Entri paling kanan rusak total (bukan IP, bukan IP:port, bukan
        // [IPv6]) berarti proxy tak lagi bisa dipercaya; walk berhenti di
        // situ, TIDAK lanjut memakai entri kiri yang dikendalikan klien.
        $s = array( 'REMOTE_ADDR' => '10.0.0.5', 'HTTP_X_FORWARDED_FOR' => '198.51.100.20, tidak-valid' );
        $this->assertSame( '10.0.0.5', WPMGR_IP::tentukan( $s, true, $this->cf )['ip'] );
    }

    public function test_remote_addr_ipv4_mapped_dari_cloudflare(): void {
        $s = array( 'REMOTE_ADDR' => '::ffff:104.16.1.2', 'HTTP_CF_CONNECTING_IP' => '198.51.100.7' );
        $this->assertSame( array( 'ip' => '198.51.100.7', 'lewat_cloudflare' => true ),
            WPMGR_IP::tentukan( $s, false, $this->cf ) );
    }

    public function test_remote_addr_ipv4_mapped_dinormalisasi_saat_jadi_hasil_akhir(): void {
        $s = array( 'REMOTE_ADDR' => '::ffff:203.0.113.9' );
        $this->assertSame( array( 'ip' => '203.0.113.9', 'lewat_cloudflare' => false ),
            WPMGR_IP::tentukan( $s, false, $this->cf ) );
    }

    public function test_dalam_rentang_prefiks_tidak_valid(): void {
        $this->assertFalse( WPMGR_IP::dalam_rentang( '1.2.3.4', '1.2.3.0/abc' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( '1.2.3.4', '1.2.3.0/' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( '1.2.3.4', '1.2.3.0/-1' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( '1.2.3.4', '1.2.3.0/33' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( '::1', '::/129' ) );
    }

    public function test_dalam_rentang_batas_prefiks(): void {
        $this->assertTrue( WPMGR_IP::dalam_rentang( '8.8.8.8', '0.0.0.0/0' ) );
        $this->assertTrue( WPMGR_IP::dalam_rentang( '1.2.3.4', '1.2.3.4/32' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( '1.2.3.5', '1.2.3.4/32' ) );
        $this->assertTrue( WPMGR_IP::dalam_rentang( '::1', '::1/128' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( '::2', '::1/128' ) );
    }

    public function test_dalam_rentang_prefiks_ipv6_bukan_kelipatan_8(): void {
        // 2a06:98c0::/29 mencakup byte ke-4 0xC0..0xC7 (5 bit tetap, 3 bit bebas).
        $this->assertTrue( WPMGR_IP::dalam_rentang( '2a06:98c7:ffff:ffff:ffff:ffff:ffff:ffff', '2a06:98c0::/29' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( '2a06:98c8::', '2a06:98c0::/29' ) );
    }
}
