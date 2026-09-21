<?php
use PHPUnit\Framework\TestCase;

final class SelfUpdateTest extends TestCase {

    private function payload( $isi = 'PK-isi-zip', $versi = '2.0.1' ) {
        return array(
            'versi'   => $versi,
            'sha256'  => hash( 'sha256', $isi ),
            'zip_b64' => base64_encode( $isi ),
        );
    }

    private function kode( $hasil ) {
        $this->assertInstanceOf( WP_Error::class, $hasil );
        return $hasil->get_error_code();
    }

    public function test_paket_sah_mengembalikan_versi_dan_isi(): void {
        $this->assertSame( array( '2.0.1', 'PK-isi-zip' ), WPMGR_SelfUpdate::periksa_paket( $this->payload() ) );
    }

    public function test_hash_huruf_besar_tetap_diterima(): void {
        $p           = $this->payload();
        $p['sha256'] = strtoupper( $p['sha256'] );
        $this->assertIsArray( WPMGR_SelfUpdate::periksa_paket( $p ) );
    }

    public function test_hash_tidak_cocok_ditolak(): void {
        $p           = $this->payload();
        $p['sha256'] = hash( 'sha256', 'lain' );
        $this->assertSame( 'wpmgr_paket_rusak', $this->kode( WPMGR_SelfUpdate::periksa_paket( $p ) ) );
    }

    public function test_base64_rusak_ditolak(): void {
        $p            = $this->payload();
        $p['zip_b64'] = '***';
        $this->assertSame( 'wpmgr_paket_rusak', $this->kode( WPMGR_SelfUpdate::periksa_paket( $p ) ) );
    }

    public function test_field_hilang_atau_bukan_string_ditolak(): void {
        $this->assertSame( 'wpmgr_payload_salah', $this->kode( WPMGR_SelfUpdate::periksa_paket( null ) ) );
        $p = $this->payload();
        unset( $p['versi'] );
        $this->assertSame( 'wpmgr_payload_salah', $this->kode( WPMGR_SelfUpdate::periksa_paket( $p ) ) );
        $p          = $this->payload();
        $p['versi'] = array( '2.0.1' );
        $this->assertSame( 'wpmgr_payload_salah', $this->kode( WPMGR_SelfUpdate::periksa_paket( $p ) ) );
    }

    public function test_versi_bukan_nomor_rilis_ditolak(): void {
        $this->assertSame( 'wpmgr_payload_salah',
            $this->kode( WPMGR_SelfUpdate::periksa_paket( $this->payload( 'x', '../../evil' ) ) ) );
        $this->assertIsArray( WPMGR_SelfUpdate::periksa_paket( $this->payload( 'x', '2.0.0.1' ) ) );
    }

    public function test_status_http_galat(): void {
        $e = WPMGR_SelfUpdate::periksa_paket( null );
        $this->assertSame( array( 'status' => 400 ), $e->get_error_data() );
    }

    public function test_perlu_pasang_hanya_bila_lebih_baru(): void {
        $this->assertTrue( WPMGR_SelfUpdate::perlu_pasang( '2.0.0', '2.0.1' ) );
        $this->assertTrue( WPMGR_SelfUpdate::perlu_pasang( '2.0.0', '2.0.0.1' ) );
        $this->assertFalse( WPMGR_SelfUpdate::perlu_pasang( '2.0.0', '2.0.0' ) );
        $this->assertFalse( WPMGR_SelfUpdate::perlu_pasang( '2.1.0', '2.0.9' ) );
    }
}
