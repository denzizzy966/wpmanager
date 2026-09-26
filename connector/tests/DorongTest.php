<?php
use PHPUnit\Framework\TestCase;

/** DB tiruan: cukup untuk kunci opsi, SHOW TABLES, DROP/CREATE/RENAME. */
final class WPMGR_FakeDbDorong {
    public $kueri      = array();
    public $tabel      = array();
    public $opsi       = array();
    public $gagal_pada = null;

    public function prefix() {
        return 'wp_';
    }

    public function opsi() {
        return 'wp_options';
    }

    public function suka( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function siapkan( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        $args = array_map( function ( $a ) {
            return is_int( $a ) ? $a : addslashes( (string) $a );
        }, $args );
        return vsprintf( str_replace( '%s', "'%s'", $sql ), $args );
    }

    private static function pola_like( $like ) {
        $r = preg_quote( stripslashes( str_replace( array( '\\_', '\\%' ), array( "\x01", "\x02" ), $like ) ), '/' );
        $r = str_replace( array( '%', '_', "\x01", "\x02" ), array( '.*', '.', '_', '%' ), $r );
        return '/^' . $r . '\z/';
    }

    public function kolom( $sql ) {
        $this->kueri[] = $sql;
        if ( preg_match( "/^SHOW TABLES LIKE '(.*)'\z/", $sql, $m ) ) {
            $pola = self::pola_like( $m[1] );
            return array_values( array_filter( $this->tabel, function ( $t ) use ( $pola ) {
                return 1 === preg_match( $pola, $t );
            } ) );
        }
        return array();
    }

    public function nilai( $sql ) {
        $this->kueri[] = $sql;
        if ( false !== strpos( $sql, "option_name = 'wpmgr_dorong_kunci'" ) ) {
            return isset( $this->opsi['wpmgr_dorong_kunci'] ) ? $this->opsi['wpmgr_dorong_kunci'] : null;
        }
        return null;
    }

    public function kueri( $sql ) {
        $this->kueri[] = $sql;
        if ( null !== $this->gagal_pada && false !== strpos( $sql, $this->gagal_pada ) ) {
            return 'galat tiruan';
        }
        if ( preg_match( "/^INSERT IGNORE INTO wp_options .*VALUES \('wpmgr_dorong_kunci', '([^']*)'/", $sql, $m ) ) {
            if ( ! isset( $this->opsi['wpmgr_dorong_kunci'] ) ) {
                $this->opsi['wpmgr_dorong_kunci'] = $m[1];
            }
        } elseif ( preg_match( "/^UPDATE wp_options SET option_value = '([^']*)' WHERE option_name = 'wpmgr_dorong_kunci' AND option_value = '([^']*)'/", $sql, $m ) ) {
            if ( isset( $this->opsi['wpmgr_dorong_kunci'] ) && $this->opsi['wpmgr_dorong_kunci'] === $m[2] ) {
                $this->opsi['wpmgr_dorong_kunci'] = $m[1];
            }
        } elseif ( preg_match( "/^DELETE FROM wp_options WHERE option_name = 'wpmgr_dorong_kunci' AND option_value LIKE '([0-9a-f]+)\|%'/", $sql, $m ) ) {
            if ( isset( $this->opsi['wpmgr_dorong_kunci'] ) && 0 === strpos( $this->opsi['wpmgr_dorong_kunci'], $m[1] . '|' ) ) {
                unset( $this->opsi['wpmgr_dorong_kunci'] );
            }
        } elseif ( preg_match( '/^DROP TABLE IF EXISTS `([^`]+)`/', $sql, $m ) ) {
            $this->tabel = array_values( array_diff( $this->tabel, array( $m[1] ) ) );
        } elseif ( preg_match( '/^CREATE TABLE `([^`]+)`/', $sql, $m ) ) {
            $this->tabel[] = $m[1];
        } elseif ( preg_match( '/^RENAME TABLE (.*)\z/s', $sql, $m ) ) {
            $tabel = $this->tabel;
            foreach ( explode( ', ', $m[1] ) as $pasang ) {
                if ( ! preg_match( '/^`([^`]+)` TO `([^`]+)`\z/', $pasang, $p ) || ! in_array( $p[1], $tabel, true )
                    || in_array( $p[2], $tabel, true ) ) {
                    return 'RENAME gagal: ' . $pasang;
                }
                $tabel = array_values( array_diff( $tabel, array( $p[1] ) ) );
                $tabel[] = $p[2];
            }
            $this->tabel = $tabel;
        }
        return true;
    }
}

// Permintaan REST tiruan minimal (pola sama seperti FileTest.php/TandaAirTest.php):
// get_header() selalu mengembalikan string kosong, meniru permintaan anonim
// tanpa tanda tangan HMAC apa pun.
final class WPMGR_FakeRequestDorong {
    private $body;

    public function __construct( $body = '' ) {
        $this->body = $body;
    }

    public function get_header( $nama ) {
        return '';
    }

    public function get_body() {
        return $this->body;
    }
}

final class DorongTest extends TestCase {

    const ID  = '0123456789abcdef0123456789abcdef';
    const ID2 = 'fedcba9876543210fedcba9876543210';

    private $akar;
    private $db;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-drg-' . bin2hex( random_bytes( 6 ) ) . '/';
        mkdir( $this->akar . 'wp-content/themes/t', 0777, true );
        file_put_contents( $this->akar . 'wp-content/themes/t/style.css', 'lama' );
        $this->db = new WPMGR_FakeDbDorong();
    }

    protected function tearDown(): void {
        StagingDasarTest::hapus( rtrim( $this->akar, '/' ) );
        $GLOBALS['wpmgr_test_opsi'] = array();
        $GLOBALS['wpmgr_test_rute'] = array();
    }

    private function dorong() {
        return new WPMGR_Staging_Dorong( $this->akar, $this->akar . 'wp-content/wpmgr-dorong/', $this->db, 20 );
    }

    private function paket( $id, $nomor, $jenis, array $berkas, array $isi ) {
        return WPMGR_Staging_Paket::susun(
            array( 'dorong_id' => $id, 'nomor' => $nomor, 'jenis' => $jenis, 'berkas' => $berkas ), $isi );
    }

    public function test_unggah_menyimpan_potongan_terlindung_dan_idempoten(): void {
        $data  = $this->paket( self::ID, 0, 'berkas', array( array( 'path' => 'wp-content/themes/t/style.css', 'mtime' => 5 ) ), array( 'baru' ) );
        $hasil = $this->dorong()->unggah( $data );
        $this->assertSame( array( 'ok' => true, 'nomor' => 0, 'sha256' => hash( 'sha256', $data ) ), $hasil );
        $berkas = $this->akar . 'wp-content/wpmgr-dorong/' . self::ID . '/potongan/000000.php';
        $this->assertSame( WPMGR_Staging_Dorong::KEPALA . $data, file_get_contents( $berkas ) );
        $this->assertFileExists( $this->akar . 'wp-content/wpmgr-dorong/.htaccess' );
        $this->assertFileExists( $this->akar . 'wp-content/wpmgr-dorong/index.php' );
        $this->assertSame( 'mengunggah', $this->dorong()->keadaan( self::ID )['status'] );
        $this->assertSame( $hasil, $this->dorong()->unggah( $data ) );
        $this->assertStringStartsWith( self::ID . '|', $this->db->opsi['wpmgr_dorong_kunci'] );
    }

    public function test_unggah_menolak_meta_tidak_sah(): void {
        $d = $this->dorong();
        $this->assertSame( 400, $d->unggah( $this->paket( 'bukan-id', 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) )->get_error_data()['status'] );
        $this->assertSame( 400, $d->unggah( $this->paket( self::ID, -1, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) )->get_error_data()['status'] );
        $this->assertSame( 400, $d->unggah( $this->paket( self::ID, 1, 'eval', array( array( 'path' => 'sql' ) ), array( 'x' ) ) )->get_error_data()['status'] );
        $this->assertSame( 400, $d->unggah( $this->paket( self::ID, 1, 'berkas', array( array( 'path' => 'wp-config.php' ) ), array( 'x' ) ) )->get_error_data()['status'] );
        $this->assertSame( 400, $d->unggah( $this->paket( self::ID, 1, 'berkas', array( array( 'path' => '../x.php' ) ), array( 'x' ) ) )->get_error_data()['status'] );
        $this->assertSame( 'wpmgr_staging_hash', $d->unggah( substr( $this->paket( self::ID, 1, 'sql', array( array( 'path' => 'sql' ) ), array( 'xy' ) ), 0, -1 ) . 'z' )->get_error_code() );
        $this->assertSame( 413, $d->unggah( str_repeat( 'a', WPMGR_Staging_Dorong::MAKS_UNGGAH + 1 ) )->get_error_data()['status'] );
        $this->assertDirectoryDoesNotExist( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
    }

    public function test_kunci_menahan_dorongan_lain_dan_basi_direbut(): void {
        $d = $this->dorong();
        $this->assertTrue( $d->kunci( self::ID ) );
        $galat = $d->kunci( self::ID2 );
        $this->assertSame( 'wpmgr_staging_sibuk', $galat->get_error_code() );
        $this->assertSame( 409, $galat->get_error_data()['status'] );
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 90000 );
        $this->assertTrue( $d->kunci( self::ID2 ) );
        $this->assertStringStartsWith( self::ID2 . '|', $this->db->opsi['wpmgr_dorong_kunci'] );
        $d->lepas_kunci( self::ID2 );
        $this->assertArrayNotHasKey( 'wpmgr_dorong_kunci', $this->db->opsi );
    }

    // ---- Ruling F9b (putusan-preflight.md) + dispatch: ambang basi kunci
    // dorong adalah 2 JAM (aktivitas terakhir), BUKAN 24 jam seperti
    // pembersihan area sementara oleh cron -- keduanya adalah timer yang
    // berbeda ("Ini adalah kunci yang dirujuk ruling F9b" pada dispatch). ----

    public function test_kunci_basi_setelah_dua_jam_bukan_dua_puluh_empat_jam(): void {
        $d = $this->dorong();
        // 90 menit (1,5 jam): BELUM basi pada ambang 2 jam -- dorongan lain
        // tetap ditolak sibuk.
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 5400 );
        $galat                                = $d->kunci( self::ID2 );
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( 'wpmgr_staging_sibuk', $galat->get_error_code() );
        // 3 jam: SUDAH basi (> 2 jam) -- direbut dorongan lain.
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 10800 );
        $this->assertTrue( $d->kunci( self::ID2 ) );
    }

    public function test_snapshot_berkas(): void {
        $hasil = $this->dorong()->snapshot_berkas( array( 'wp-content/themes/t/style.css', 'wp-content/themes/t/baru.php' ) );
        $this->assertSame( 'wp-content/themes/t/style.css', $hasil[0]['path'] );
        $this->assertTrue( $hasil[0]['ada'] );
        $this->assertSame( 4, $hasil[0]['ukuran'] );
        $this->assertSame( array( 'path' => 'wp-content/themes/t/baru.php', 'ada' => false ), $hasil[1] );
        foreach ( array( array( 'wp-config.php' ), array( '../x' ), 'bukan-daftar', array_fill( 0, 5001, 'index.php' ) ) as $p ) {
            $this->assertInstanceOf( WP_Error::class, $this->dorong()->snapshot_berkas( $p ) );
        }
    }

    public function test_bersihkan_menghapus_area_tabel_sementara_dan_kunci(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'SELECT 1;' ) ) );
        $this->db->tabel = array( 'wp_posts', 'wpmgr_tmp_wp_posts', 'wpmgr_old_wp_posts' );
        $this->assertSame( array( 'lagi' => false ), $d->bersihkan( self::ID ) );
        $this->assertDirectoryDoesNotExist( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
        $this->assertSame( array( 'wp_posts', 'wpmgr_old_wp_posts' ), $this->db->tabel );
        $this->assertArrayNotHasKey( 'wpmgr_dorong_kunci', $this->db->opsi );
    }

    // ---- Catatan Task 3 (dispatch): pada database bersama, prefix yang
    // saling tumpang tindih (mis. site ini 'wp_', site lain di database yang
    // sama 'wpmgr_tmp_lain_') tidak boleh membuat bersihkan() menghapus
    // tabel sementara MILIK SITE LAIN -- hanya tabel berawalan
    // 'wpmgr_tmp_<prefix_site_ini>'/'wpmgr_old_<prefix_site_ini>' yang boleh
    // disentuh, bukan sekadar 'wpmgr_tmp_%'/'wpmgr_old_%' global. ----

    public function test_bersihkan_tidak_menyentuh_tabel_sementara_site_lain(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'SELECT 1;' ) ) );
        // 'wpmgr_tmp_lain_posts' meniru tabel sementara site LAIN yang
        // berbagi database ini (prefix 'lain_', bukan 'wp_' milik site ini).
        $this->db->tabel = array( 'wp_posts', 'wpmgr_tmp_wp_posts', 'wpmgr_tmp_lain_posts' );
        $this->assertSame( array( 'lagi' => false ), $d->bersihkan( self::ID ) );
        $this->assertSame( array( 'wp_posts', 'wpmgr_tmp_lain_posts' ), $this->db->tabel );
    }

    public function test_bersihkan_menolak_saat_menukar(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $k           = $d->keadaan( self::ID );
        $k['status'] = 'menukar';
        $d->simpan_keadaan( self::ID, $k );
        $this->assertSame( 'wpmgr_staging_sibuk', $d->bersihkan( self::ID )->get_error_code() );
        $this->assertSame( 400, $d->bersihkan( 'x' )->get_error_data()['status'] );
    }

    public function test_cron_menghapus_area_berumur_24_jam(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $d->cron();
        $this->assertDirectoryExists( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
        $k           = $d->keadaan( self::ID );
        $k['diubah'] = time() - 90000;
        $d->simpan_keadaan( self::ID, $k );
        $d->cron();
        $this->assertDirectoryDoesNotExist( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
    }

    // ---- Setiap route baru punya test akses anonim (401). ----

    private function cek_rute_anonim( $jalur, $metode, $callback ) {
        $GLOBALS['wpmgr_test_rute'] = array();
        WPMGR_Staging::daftarkan_route();
        $kunci = 'wpmgr/v1' . $jalur;
        $this->assertArrayHasKey( $kunci, $GLOBALS['wpmgr_test_rute'] );
        $r = $GLOBALS['wpmgr_test_rute'][ $kunci ];
        $this->assertSame( $metode, $r['methods'] );
        $this->assertSame( array( 'WPMGR_Staging', $callback ), $r['callback'] );
        $this->assertSame( array( 'WPMGR_Staging', 'guard' ), $r['permission_callback'] );

        // Site "terpasang" (site_id/secret ada) supaya penolakan yang diuji
        // sungguh berasal dari ketiadaan tanda tangan, bukan dari
        // "connector belum dipasangkan".
        $GLOBALS['wpmgr_test_opsi']['wpmgr_site_id'] = 'situs-uji';
        $GLOBALS['wpmgr_test_opsi']['wpmgr_secret']  = str_repeat( 'a', 64 );
        $hasil = call_user_func( $r['permission_callback'], new WPMGR_FakeRequestDorong() );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( array( 'status' => 401 ), $hasil->get_error_data() );
    }

    public function test_staging_unggah_terdaftar_dengan_guard_dan_menolak_tanpa_tanda_tangan(): void {
        $this->cek_rute_anonim( '/staging/unggah', 'POST', 'unggah' );
    }

    public function test_staging_snapshot_terdaftar_dengan_guard_dan_menolak_tanpa_tanda_tangan(): void {
        $this->cek_rute_anonim( '/staging/snapshot', 'POST', 'snapshot' );
    }

    public function test_staging_bersihkan_terdaftar_dengan_guard_dan_menolak_tanpa_tanda_tangan(): void {
        $this->cek_rute_anonim( '/staging/bersihkan', 'POST', 'bersihkan' );
    }
}
