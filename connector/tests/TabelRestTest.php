<?php
use PHPUnit\Framework\TestCase;

// Permintaan REST tiruan minimal (fix round 2, item 3): hanya butuh
// get_body(), satu-satunya metode yang dipanggil WPMGR_Staging::tabel().
final class WPMGR_FakeRequestTabel {
    private $body;

    public function __construct( $body ) {
        $this->body = $body;
    }

    public function get_body() {
        return $this->body;
    }
}

/**
 * WPMGR_Staging::tabel() (callback REST) diuji langsung di sini (item 3,
 * fix round 2): kursor yang ADA di body tapi bukan string/null tidak
 * boleh diam-diam diperlakukan sebagai '' (mengulang ekspor dari awal,
 * membuang kemajuan tarik yang sedang berjalan) -- harus 400 keras.
 * Berkas terpisah dari TabelTest.php: PHPUnit (direktori) hanya
 * mengumpulkan SATU kelas test per berkas di proyek ini.
 */
final class TabelRestTest extends TestCase {

    protected function setUp(): void {
        WPMGR_Staging_Tabel::$baris           = 2000;
        WPMGR_Staging_Tabel::$sub             = 200;
        WPMGR_Staging_Tabel::$maks_byte       = 6291456;
        WPMGR_Staging_Tabel::$maks_pernyataan = 1048576;
        WPMGR_Staging_Tabel::$maks_respon     = 8388608;
    }

    private function wpdb_ber_pk() {
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'id', 'Type' => 'bigint(20) unsigned' ) );
        $w->pk    = array( array( 'Column_name' => 'id', 'Seq_in_index' => '1' ) );
        $w->baris = array( array( 'id' => '1' ) );
        return $w;
    }

    public function test_kursor_bukan_string_atau_null_di_body_ditolak_400(): void {
        global $wpdb;
        $wpdb = new WPMGR_FakeWpdbTabel();
        foreach ( array( array( 'x' ), 123, true, 1.5 ) as $k ) {
            $req   = new WPMGR_FakeRequestTabel( json_encode( array( 'tabel' => 'wp_x', 'kursor' => $k ) ) );
            $hasil = WPMGR_Staging::tabel( $req );
            $this->assertInstanceOf( WP_Error::class, $hasil, json_encode( $k ) );
            $this->assertSame( 'wpmgr_staging_permintaan', $hasil->get_error_code() );
            $this->assertSame( array( 'status' => 400 ), $hasil->get_error_data() );
        }
    }

    public function test_kursor_kosong_atau_tanpa_field_tetap_sah(): void {
        global $wpdb;
        $wpdb  = $this->wpdb_ber_pk();
        $req   = new WPMGR_FakeRequestTabel( json_encode( array( 'tabel' => 'wp_x' ) ) );
        $hasil = WPMGR_Staging::tabel( $req );
        $this->assertNotInstanceOf( WP_Error::class, $hasil );

        $req2   = new WPMGR_FakeRequestTabel( json_encode( array( 'tabel' => 'wp_x', 'kursor' => null ) ) );
        $hasil2 = WPMGR_Staging::tabel( $req2 );
        $this->assertNotInstanceOf( WP_Error::class, $hasil2 );

        $req3   = new WPMGR_FakeRequestTabel( json_encode( array( 'tabel' => 'wp_x', 'kursor' => '' ) ) );
        $hasil3 = WPMGR_Staging::tabel( $req3 );
        $this->assertNotInstanceOf( WP_Error::class, $hasil3 );
    }
}
