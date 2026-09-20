<?php
use PHPUnit\Framework\TestCase;

/**
 * WPMGR_SSO::periksa_token() adalah PHP murni (tanpa panggilan WordPress apa
 * pun selain membentuk WP_Error), sehingga seluruh test di sini berjalan
 * tanpa memuat WordPress sama sekali. Format token harus persis sama dengan
 * sisi Python (src/wpmgr/sso.py): base64url(json(payload)) + "." +
 * hex(hmac_sha256(secret_hex, body)), dengan secret hex dipakai sebagai
 * string ASCII apa adanya.
 */
final class SsoTest extends TestCase {

    private const SECRET = 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc';

    private function b64url_encode( string $mentah ): string {
        return rtrim( strtr( base64_encode( $mentah ), '+/', '-_' ), '=' );
    }

    private function buat_token( array $payload, string $secret = self::SECRET ): string {
        $body = $this->b64url_encode( json_encode( $payload ) );
        return $body . '.' . hash_hmac( 'sha256', $body, $secret );
    }

    private function token_dari_body_mentah( string $body, string $secret = self::SECRET ): string {
        return $body . '.' . hash_hmac( 'sha256', $body, $secret );
    }

    // -- token valid -----------------------------------------------------

    public function test_token_valid_terbaca(): void {
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000, 'nonce' => str_repeat( 'a', 32 ) ) );
        $p = WPMGR_SSO::periksa_token( self::SECRET, $t, 990 );

        $this->assertIsArray( $p );
        $this->assertSame( 's1', $p['site_id'] );
        $this->assertSame( str_repeat( 'a', 32 ), $p['nonce'] );
    }

    public function test_token_tepat_pada_batas_exp_diterima(): void {
        // now == exp: belum kedaluwarsa, hanya "now > exp" yang ditolak.
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000, 'nonce' => str_repeat( 'a', 32 ) ) );
        $p = WPMGR_SSO::periksa_token( self::SECRET, $t, 1000 );
        $this->assertIsArray( $p );
    }

    // -- kedaluwarsa & secret ---------------------------------------------

    public function test_token_kedaluwarsa_ditolak(): void {
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000, 'nonce' => str_repeat( 'a', 32 ) ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $t, 1001 ) );
    }

    public function test_secret_lain_ditolak(): void {
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000, 'nonce' => str_repeat( 'a', 32 ) ), str_repeat( 'd', 64 ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $t, 990 ) );
    }

    public function test_body_diubah_ditolak(): void {
        // Tanda tangan diverifikasi atas body ASLI; body yang diganti sesudah
        // ditandatangani harus gagal cocok meski hasil decode-nya sendiri
        // adalah JSON yang sah.
        $t          = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000, 'nonce' => str_repeat( 'a', 32 ) ) );
        list( , $sig ) = explode( '.', $t, 2 );
        $body_lain  = $this->b64url_encode( json_encode( array( 'site_id' => 'lain', 'exp' => 9999999999, 'nonce' => str_repeat( 'b', 32 ) ) ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $body_lain . '.' . $sig, 990 ) );
    }

    // -- bentuk token ------------------------------------------------------

    public function test_token_tanpa_titik_ditolak(): void {
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, 'tanpatitik', 990 ) );
    }

    public function test_titik_di_awal_ditolak(): void {
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, '.' . str_repeat( 'a', 64 ), 990 ) );
    }

    public function test_titik_di_akhir_ditolak(): void {
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, 'abc.', 990 ) );
    }

    // -- nonce ---------------------------------------------------------

    public function test_nonce_tidak_valid_ditolak(): void {
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000, 'nonce' => 'pendek' ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $t, 990 ) );
    }

    public function test_nonce_bukan_hex_ditolak(): void {
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000, 'nonce' => str_repeat( 'g', 32 ) ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $t, 990 ) );
    }

    public function test_nonce_hilang_ditolak(): void {
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000 ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $t, 990 ) );
    }

    // -- exp -------------------------------------------------------------

    public function test_exp_hilang_ditolak(): void {
        $t = $this->buat_token( array( 'site_id' => 's1', 'nonce' => str_repeat( 'a', 32 ) ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $t, 990 ) );
    }

    public function test_exp_bukan_angka_ditolak(): void {
        // Sisi Python melakukan int(payload["exp"]) dan menolak string non-
        // numerik. Cast (int) PHP yang naif diam-diam menghasilkan 0 untuk
        // "not_a_number" -- longgar seperti itu akan mengesahkan token yang
        // ditolak Python, jadi exp wajib lolos is_numeric() lebih dulu.
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 'not_a_number', 'nonce' => str_repeat( 'a', 32 ) ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $t, 990 ) );
    }

    // -- payload -----------------------------------------------------------

    public function test_payload_bukan_objek_ditolak(): void {
        $body = $this->b64url_encode( json_encode( 5 ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $this->token_dari_body_mentah( $body ), 990 ) );
    }

    public function test_payload_tidak_dapat_dibaca_ditolak(): void {
        $body = $this->b64url_encode( 'bukan json yang sah {' );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $this->token_dari_body_mentah( $body ), 990 ) );
    }

    // -- b64url_decode -----------------------------------------------------

    public function test_b64url_decode_bolak_balik(): void {
        $asli   = 'abcde';
        $encode = $this->b64url_encode( $asli );
        $this->assertSame( $asli, WPMGR_SSO::b64url_decode( $encode ) );
    }

    public function test_b64url_decode_karakter_tidak_valid_mengembalikan_false(): void {
        $this->assertFalse( WPMGR_SSO::b64url_decode( 'tidak!valid!!' ) );
    }
}
