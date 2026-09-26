<?php
use PHPUnit\Framework\TestCase;

/** DB tiruan: cukup untuk kunci opsi, SHOW TABLES, DROP/CREATE/RENAME. */
final class WPMGR_FakeDbDorong {
    public $kueri      = array();
    public $tabel      = array();
    public $opsi       = array();
    public $gagal_pada = null;
    // Fix I5 (review putaran 1): meniru $wpdb->last_error sungguhan --
    // dikosongkan di awal SETIAP kolom()/nilai()/kueri() baru, diisi hanya
    // saat panggilan itu gagal (lihat WPMGR_Staging_Db::galat_terakhir()).
    private $galat = '';
    // Fix N5 (review putaran 2, Minor): menyuntikkan nilai PENGGANTI untuk
    // SELECT option_value ke-n (0-based, dihitung per panggilan nilai()
    // yang menyentuh wpmgr_dorong_kunci) -- dipakai mensimulasikan
    // permintaan LAIN dari PEMEGANG YANG SAMA yang menyegarkan kunci di
    // ANTARA UPDATE dan baca-ulang milik panggilan yang sedang diuji.
    public $nilai_urutan   = 0;
    public $nilai_override = array();

    public function prefix() {
        return 'wp_';
    }

    public function opsi() {
        return 'wp_options';
    }

    public function suka( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function galat_terakhir() {
        return $this->galat;
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

    private function tandai_gagal_bila_cocok( $sql ) {
        $this->galat = ( null !== $this->gagal_pada && false !== strpos( $sql, $this->gagal_pada ) ) ? 'galat tiruan' : '';
        return '' !== $this->galat;
    }

    public function kolom( $sql ) {
        $this->kueri[] = $sql;
        if ( $this->tandai_gagal_bila_cocok( $sql ) ) {
            return array();
        }
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
        if ( $this->tandai_gagal_bila_cocok( $sql ) ) {
            return null;
        }
        if ( false !== strpos( $sql, "option_name = 'wpmgr_dorong_kunci'" ) ) {
            $urutan = $this->nilai_urutan++;
            if ( array_key_exists( $urutan, $this->nilai_override ) ) {
                return $this->nilai_override[ $urutan ];
            }
            return isset( $this->opsi['wpmgr_dorong_kunci'] ) ? $this->opsi['wpmgr_dorong_kunci'] : null;
        }
        return null;
    }

    public function kueri( $sql ) {
        $this->kueri[] = $sql;
        if ( $this->tandai_gagal_bila_cocok( $sql ) ) {
            return 'galat tiruan';
        }
        if ( preg_match( "/^INSERT IGNORE INTO wp_options .*VALUES \('wpmgr_dorong_kunci', '([^']*)'/", $sql, $m ) ) {
            if ( ! isset( $this->opsi['wpmgr_dorong_kunci'] ) ) {
                $this->opsi['wpmgr_dorong_kunci'] = $m[1];
            }
        } elseif ( preg_match( "/^UPDATE wp_options SET option_value = '([^']*)' WHERE option_name = 'wpmgr_dorong_kunci' AND option_value LIKE '([0-9a-f]+)\|%'/", $sql, $m ) ) {
            // Penyegaran oleh pemegang saat ini (fix C1) -- fencing lewat LIKE '<id>|%'.
            if ( isset( $this->opsi['wpmgr_dorong_kunci'] ) && 0 === strpos( $this->opsi['wpmgr_dorong_kunci'], $m[2] . '|' ) ) {
                $this->opsi['wpmgr_dorong_kunci'] = $m[1];
            }
        } elseif ( preg_match( "/^UPDATE wp_options SET option_value = '([^']*)' WHERE option_name = 'wpmgr_dorong_kunci' AND option_value = '([^']*)'/", $sql, $m ) ) {
            // Perebutan kunci basi (compare-and-swap atas nilai persis).
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
    const ID3 = '1111111111111111aaaaaaaaaaaaaaaa';

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

    // ---- Fix C1 (review putaran 1, Kritis): draf awal HANYA memasang
    // kunci sekali di potongan pertama dan tidak pernah menyegarkannya lagi
    // -- push yang masih AKTIF (potongan baru terus tiba) tapi berjalan
    // lebih dari 2 jam bisa direbut push lain padahal belum berhenti. ----

    public function test_kunci_disegarkan_setiap_unggah_tetap_dipegang_lebih_dari_dua_jam(): void {
        $d    = $this->dorong();
        $data = $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'a' ) );
        $this->assertSame( array( 'ok' => true, 'nomor' => 0, 'sha256' => hash( 'sha256', $data ) ), $d->unggah( $data ) );
        // Simulasikan waktu berlalu > 2 jam SEJAK potongan pertama TANPA
        // pernah disegarkan -- ini seolah-olah push tidak pernah
        // menyegarkan kuncinya (bug draf awal). Bila unggah() potongan
        // KEDUA menyegarkan dengan benar, dorongan lain sesudahnya tetap
        // ditolak sibuk -- BUKAN berhasil merebut kunci yang tampak basi.
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 10800 );
        $data2                                = $this->paket( self::ID, 1, 'sql', array( array( 'path' => 'sql' ) ), array( 'b' ) );
        $this->assertSame( array( 'ok' => true, 'nomor' => 1, 'sha256' => hash( 'sha256', $data2 ) ), $d->unggah( $data2 ) );
        $galat = $d->kunci( self::ID2 );
        $this->assertInstanceOf( WP_Error::class, $galat, 'Kunci push yang masih aktif tidak boleh bisa direbut.' );
        $this->assertSame( 'wpmgr_staging_sibuk', $galat->get_error_code() );
    }

    // ---- Fix N5 (review putaran 2, Minor): fencing penyegaran
    // membandingkan hanya AWALAN '<id>|', bukan nilai PERSIS -- dua
    // permintaan tumpang tindih dari PEMEGANG YANG SAMA yang melintasi
    // batas detik bisa saja sama-sama menyegarkan dengan stempel waktu
    // berbeda; membandingkan nilai persis salah mengira ini sebagai kunci
    // yang hilang, padahal tetap dipegang id yang sama sepanjang waktu. ----

    public function test_kunci_fencing_hanya_membandingkan_awalan_id(): void {
        $d = $this->dorong();
        $this->assertTrue( $d->kunci( self::ID ) );
        // Simulasikan permintaan LAIN dari PEMEGANG YANG SAMA yang tumpang
        // tindih dan menyegarkan kunci ini DI ANTARA UPDATE dan baca-ulang
        // milik panggilan kunci() yang sedang diuji -- indeks 0 adalah
        // SELECT setelah INSERT IGNORE (nilai asli), indeks 1 adalah
        // SELECT baca-ulang setelah UPDATE penyegaran, tempat permintaan
        // tumpang tindih itu "menang" dengan stempel waktu BERBEDA (tapi
        // id yang SAMA).
        $this->db->nilai_urutan   = 0; // Dihitung ulang dari 0 untuk panggilan kunci() berikut ini saja.
        $this->db->nilai_override = array( 1 => self::ID . '|' . ( time() + 5 ) );
        $this->assertTrue( $d->kunci( self::ID ),
            'Penyegaran dari pemegang yang sama tidak boleh dianggap kunci hilang hanya karena stempel waktu berbeda.' );
    }

    // ---- Fix C1 (review putaran 1, Kritis): push yang DIREBUT tidak boleh
    // bisa melanjutkan lagi walau kuncinya kelak bebas kembali (mis.
    // perebutnya sendiri melepaskannya setelah selesai). ----

    public function test_push_direbut_tetap_ditolak_walau_kunci_bebas_lagi(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'a' ) ) );
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 10800 );
        $this->assertTrue( $d->kunci( self::ID2 ) ); // ID2 merebut, menandai ID sebagai 'direbut'.
        $d->lepas_kunci( self::ID2 ); // Kunci sekarang BEBAS lagi.
        $this->assertArrayNotHasKey( 'wpmgr_dorong_kunci', $this->db->opsi );
        $galat = $d->unggah( $this->paket( self::ID, 1, 'sql', array( array( 'path' => 'sql' ) ), array( 'c' ) ) );
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( 'wpmgr_staging_direbut', $galat->get_error_code() );
        $this->assertSame( 409, $galat->get_error_data()['status'] );
    }

    // ---- Fix N2 (review putaran 2, Penting): unggah() memeriksa status
    // 'direbut' SEBELUM memanggil kunci() -- draf sebelumnya memanggil
    // kunci() lebih dulu (yang untuk push ini sendiri berhasil merebut/
    // menyegarkan miliknya sendiri), baru kemudian menolak. Percobaan yang
    // PASTI ditolak seperti itu tetap menahan kunci 2 jam lagi, memblokir
    // push BARU yang sah walau push lama sudah pasti akan ditolak. ----

    public function test_unggah_direbut_tidak_menahan_kunci_untuk_push_baru(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'a' ) ) );
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 10800 );
        $this->assertTrue( $d->kunci( self::ID2 ) ); // ID2 merebut, menandai ID sebagai 'direbut'.
        $d->lepas_kunci( self::ID2 ); // ID2 selesai/gagal dan melepas kuncinya sendiri.
        $this->assertArrayNotHasKey( 'wpmgr_dorong_kunci', $this->db->opsi );
        $galat = $d->unggah( $this->paket( self::ID, 1, 'sql', array( array( 'path' => 'sql' ) ), array( 'c' ) ) );
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( 'wpmgr_staging_direbut', $galat->get_error_code() );
        // Percobaan yang ditolak TIDAK BOLEH merebut/menyegarkan kunci --
        // kunci masih bebas persis seperti sebelum percobaan itu.
        $this->assertArrayNotHasKey( 'wpmgr_dorong_kunci', $this->db->opsi,
            'unggah() untuk push yang direbut tidak boleh mengunci ulang.' );
        // Push BARU (id lain) tidak boleh dianggap sibuk oleh kunci yang
        // seharusnya tidak pernah tersentuh itu.
        $this->assertTrue( $d->kunci( self::ID3 ) );
    }

    // ---- Fix N3 (review putaran 2, Penting): perebutan kunci basi hanya
    // aman bila status push lama masih PRA-TUKAR -- push yang sedang
    // 'menukar' (menggeser berkas/tabel produksi ke lama/) tidak boleh
    // direbut begitu saja: 'direbut' ada di STATUS_BOLEH_BERSIHKAN, jadi
    // bersihkan() akan menghapus lama/ (satu-satunya salinan produksi
    // tergeser) walau proses tukar belum tentu selesai/aman dibatalkan. ----

    public function test_takeover_ditolak_saat_status_lama_menukar_lama_tetap_utuh(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $k           = $d->keadaan( self::ID );
        $k['status'] = 'menukar';
        $d->simpan_keadaan( self::ID, $k );
        // lama/ menyimpan data produksi ASLI yang tergeser -- harus tetap utuh.
        $dir_lama = $this->akar . 'wp-content/wpmgr-dorong/' . self::ID . '/lama';
        mkdir( $dir_lama, 0777, true );
        file_put_contents( $dir_lama . '/asli.txt', 'produksi asli' );
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 10800 );
        $galat = $d->kunci( self::ID2 );
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( 'wpmgr_staging_perlu_pemulihan', $galat->get_error_code() );
        $this->assertSame( 409, $galat->get_error_data()['status'] );
        // Status TIDAK ditandai 'direbut' -- tidak pernah ditimpa.
        $this->assertSame( 'menukar', $d->keadaan( self::ID )['status'] );
        // Kunci masih milik push LAMA -- tidak diambil alih.
        $this->assertStringStartsWith( self::ID . '|', $this->db->opsi['wpmgr_dorong_kunci'] );
        // lama/ (data produksi tergeser) tetap utuh.
        $this->assertFileExists( $dir_lama . '/asli.txt' );
    }

    public function test_takeover_ditolak_saat_keadaan_hilang_padahal_area_ada(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        // keadaan.php rusak/hilang, tapi direktori (area) masih ada -- tidak
        // bisa dibuktikan aman, jadi ditolak, bukan diloloskan diam-diam.
        unlink( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID . '/keadaan.php' );
        $this->assertNull( $d->keadaan( self::ID ) );
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 10800 );
        $galat = $d->kunci( self::ID2 );
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( 'wpmgr_staging_perlu_pemulihan', $galat->get_error_code() );
        $this->assertSame( 409, $galat->get_error_data()['status'] );
    }

    // ---- Fix Minor 5 (review putaran 3): kunci basi milik push yang
    // statusnya SUDAH TERMINAL (STATUS_AMAN_TERMINAL -- riwayatnya sendiri
    // sudah berakhir, apa pun hasilnya) boleh direbut juga, bukan hanya
    // pra-tukar. Lock yang lupa dilepas SETELAH push itu sendiri selesai
    // bukan alasan menahan push BARU. ----

    public function test_takeover_diizinkan_saat_status_lama_terminal(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        foreach ( array( 'selesai', 'gagal', 'direbut', 'dipulihkan' ) as $status ) {
            $k           = $d->keadaan( self::ID );
            $k['status'] = $status;
            $d->simpan_keadaan( self::ID, $k );
            $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 10800 );
            $this->assertTrue( $d->kunci( self::ID2 ), $status );
        }
    }

    // ---- Fix Minor 4 (review putaran 3): tandai_direbut() membaca ulang
    // status TEPAT SEBELUM menulis -- push yang statusnya SUDAH TERMINAL
    // (bukan hanya sekadar "diizinkan direbut") tidak perlu/tidak boleh
    // ditandai 'direbut' -- riwayatnya sendiri sudah berakhir, menimpanya
    // hanya mengaburkan hasil asli (selesai/gagal) tanpa manfaat. ----

    public function test_takeover_tidak_menandai_direbut_bila_status_lama_sudah_terminal(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $k           = $d->keadaan( self::ID );
        $k['status'] = 'selesai';
        $d->simpan_keadaan( self::ID, $k );
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 10800 );
        $this->assertTrue( $d->kunci( self::ID2 ) );
        // Status TETAP 'selesai' -- TIDAK ditimpa jadi 'direbut'.
        $this->assertSame( 'selesai', $d->keadaan( self::ID )['status'] );
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

    // ---- MINOR (review putaran 1): snapshot_berkas() memakai path
    // kanonik (WPMGR_Staging_Path::untuk_dibaca()), bukan hanya is_link()
    // pada komponen TERAKHIR -- leluhur DIREKTORI yang di-symlink-kan ke
    // luar root tidak pernah terlihat oleh is_link() sendirian. ----

    public function test_snapshot_berkas_menolak_leluhur_symlink_keluar(): void {
        $luar = sys_get_temp_dir() . '/wpmgr-drg-luar-' . bin2hex( random_bytes( 4 ) );
        mkdir( $luar, 0777, true );
        file_put_contents( $luar . '/rahasia.txt', 'rahasia' );
        if ( ! @symlink( $luar, $this->akar . 'wp-content/tautan' ) ) {
            StagingDasarTest::hapus( $luar );
            $this->markTestSkipped( 'Sistem ini tidak mengizinkan symlink (Windows tanpa Developer Mode).' );
        }
        try {
            $hasil = $this->dorong()->snapshot_berkas( array( 'wp-content/tautan/rahasia.txt' ) );
            $this->assertInstanceOf( WP_Error::class, $hasil );
        } finally {
            StagingDasarTest::hapus( $luar );
        }
    }

    // ---- MINOR (review putaran 1): kunci 'status' yang hilang di keadaan
    // tidak boleh memicu PHP notice/warning di pemanggil. ----

    public function test_keadaan_tanpa_status_tidak_memicu_notice(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $k = $d->keadaan( self::ID );
        unset( $k['status'] );
        $d->simpan_keadaan( self::ID, $k );
        // 'status' yang hilang diperlakukan AMAN (bukan 'mengunggah', dan
        // tidak ada di STATUS_BOLEH_BERSIHKAN) -- kedua panggilan berikut
        // ditolak, tapi PALING PENTING tidak memicu PHP notice/warning
        // ("Undefined array key") yang di bawah PHPUnit akan tampak
        // sebagai test ERROR, bukan sekadar assertion gagal.
        $hasil = $d->unggah( $this->paket( self::ID, 1, 'sql', array( array( 'path' => 'sql' ) ), array( 'y' ) ) );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $galat = $d->bersihkan( self::ID );
        $this->assertInstanceOf( WP_Error::class, $galat );
    }

    public function test_bersihkan_menghapus_area_tabel_sementara_dan_kunci(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'SELECT 1;' ) ) );
        // Fix C2 (review putaran 1): tabel yang dihapus dibatasi JURNAL
        // push ini sendiri -- catat_tabel() adalah API yang akan dipakai
        // Task 8 setiap kali CREATE/RENAME sungguhan terjadi. tabel_old
        // TIDAK dicatat di sini -- dalam pemakaian sungguhan ia hanya
        // pernah ada setelah langkah 'menukar' dimulai, yang belum pernah
        // terjadi selama status masih 'mengunggah'.
        $d->catat_tabel( self::ID, 'tmp', 'wpmgr_tmp_wp_posts' );
        $this->db->tabel = array( 'wp_posts', 'wpmgr_tmp_wp_posts' );
        $this->assertSame( array( 'lagi' => false ), $d->bersihkan( self::ID ) );
        $this->assertDirectoryDoesNotExist( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
        $this->assertSame( array( 'wp_posts' ), $this->db->tabel );
        $this->assertArrayNotHasKey( 'wpmgr_dorong_kunci', $this->db->opsi );
    }

    // ---- Status TERMINAL (STATUS_AMAN_TERMINAL): 'tabel_old' (data
    // produksi tergeser) sekarang juga aman dihapus bersama tabel sementara. ----

    public function test_bersihkan_status_terminal_menghapus_tabel_lama_juga(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'SELECT 1;' ) ) );
        $d->catat_tabel( self::ID, 'tmp', 'wpmgr_tmp_wp_posts' );
        $d->catat_tabel( self::ID, 'old', 'wpmgr_old_wp_posts' );
        $k           = $d->keadaan( self::ID );
        $k['status'] = 'selesai';
        $d->simpan_keadaan( self::ID, $k );
        $this->db->tabel = array( 'wp_posts', 'wpmgr_tmp_wp_posts', 'wpmgr_old_wp_posts' );
        $this->assertSame( array( 'lagi' => false ), $d->bersihkan( self::ID ) );
        $this->assertDirectoryDoesNotExist( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
        $this->assertSame( array( 'wp_posts' ), $this->db->tabel );
        $this->assertArrayNotHasKey( 'wpmgr_dorong_kunci', $this->db->opsi );
    }

    // ---- Fix N4 (review putaran 2, Penting): jangan pernah menghapus
    // direktori (satu-satunya salinan jurnal) selama 'tabel_old' masih ada
    // tapi status BELUM membuktikan aman dihapus -- kehilangan jurnal itu
    // berarti tabel produksi lama tidak akan pernah dibersihkan siapa pun. ----

    public function test_bersihkan_menolak_saat_tabel_lama_belum_aman_dihapus(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $d->catat_tabel( self::ID, 'old', 'wpmgr_old_wp_posts' );
        $k           = $d->keadaan( self::ID );
        // Fix Minor 5 (review putaran 3): STATUS_AMAN_TERMINAL sekarang
        // mencakup 'gagal'/'direbut' juga (push yang riwayatnya sendiri
        // sudah berakhir, apa pun hasilnya) -- 'ditukar' SENGAJA TIDAK ada
        // di situ (Task 8 belum mendefinisikan artinya secara pasti),
        // jadi itulah satu-satunya status di STATUS_BOLEH_BERSIHKAN yang
        // masih memicu gerbang ini.
        $k['status'] = 'ditukar';
        $d->simpan_keadaan( self::ID, $k );
        $this->db->tabel = array( 'wp_posts', 'wpmgr_old_wp_posts' );
        $galat = $d->bersihkan( self::ID );
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( 'wpmgr_staging_perlu_pemulihan', $galat->get_error_code() );
        $this->assertSame( 409, $galat->get_error_data()['status'] );
        // Tidak disentuh sama sekali -- direktori, tabel, dan kunci utuh.
        $this->assertDirectoryExists( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
        $this->assertSame( array( 'wp_posts', 'wpmgr_old_wp_posts' ), $this->db->tabel );
    }

    // ---- Fix C2 (review putaran 1, Kritis): nama tabel sementara
    // diturunkan dari nama tabel ASLI (WPMGR_Staging_Sql::ubah()), bukan
    // dari id push -- dua push berurutan/tumpang-tindih di site yang sama
    // memakai nama tabel sementara yang SAMA PERSIS. bersihkan() harus
    // membatasi diri pada jurnal push itu SENDIRI, dan menolak men-DROP
    // apa pun bila push LAIN sedang memegang kunci (kemungkinan sedang
    // memakai nama tabel yang sama itu). Skenario: A selesai/direbut, B
    // merebut lalu mengimpor (memakai nama tabel sementara yang SAMA), lalu
    // cron membersihkan A -- tabel B tidak boleh ikut terhapus. ----

    public function test_bersihkan_push_direbut_tidak_menghapus_tabel_push_perebut(): void {
        $d = $this->dorong();
        // A mengunggah, lalu tabel sementaranya "dibuat" (dicatat jurnal).
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'SELECT 1;' ) ) );
        $d->catat_tabel( self::ID, 'tmp', 'wpmgr_tmp_wp_posts' );
        // Kunci A dibuat basi (> 2 jam), lalu B merebutnya -- ini juga
        // menandai A sebagai 'direbut' (fix C1).
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 10800 );
        $this->assertTrue( $d->kunci( self::ID2 ) );
        $this->assertSame( 'direbut', $d->keadaan( self::ID )['status'] );
        // B mengunggah dan mengimpor, memakai NAMA TABEL SEMENTARA YANG
        // SAMA seperti yang tadinya milik A (nama diturunkan dari nama
        // tabel asli, bukan dari id push).
        $d->unggah( $this->paket( self::ID2, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'SELECT 2;' ) ) );
        $d->catat_tabel( self::ID2, 'tmp', 'wpmgr_tmp_wp_posts' );
        $this->db->tabel = array( 'wp_posts', 'wpmgr_tmp_wp_posts' );
        // Cron/dashboard mencoba membersihkan A (direbut) -- tabel
        // 'wpmgr_tmp_wp_posts' sekarang milik B, TIDAK BOLEH ikut terhapus,
        // dan kunci B TIDAK BOLEH ikut terlepas. Fix N4 (review putaran 3):
        // karena jurnal A TIDAK KOSONG (masih mencatat 'wpmgr_tmp_wp_posts')
        // dan kunci dipegang push LAIN, area A (satu-satunya salinan
        // jurnal itu) juga TIDAK BOLEH ikut dihapus -- draf sebelumnya
        // jatuh lewat ke hapus_rekursif() begitu saja, mengorphankan
        // catatan tabel itu permanen. bersihkan() sekarang melaporkan
        // {lagi:true} dan area A tetap utuh untuk dicoba lagi nanti.
        $this->assertSame( array( 'lagi' => true ), $d->bersihkan( self::ID ) );
        $this->assertDirectoryExists( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
        $this->assertSame( array( 'wp_posts', 'wpmgr_tmp_wp_posts' ), $this->db->tabel );
        $this->assertStringStartsWith( self::ID2 . '|', $this->db->opsi['wpmgr_dorong_kunci'] );
    }

    // ---- Fix I5 (review putaran 1): hasil kueri() diperiksa di setiap
    // pemanggilan -- galat basis data adalah 500 KERAS, bukan 409, dan
    // bukan {lagi:false} yang tampak bersih padahal sebagian gagal. ----

    public function test_kunci_gagal_insert_menghasilkan_500_bukan_409(): void {
        $d                    = $this->dorong();
        $this->db->gagal_pada = 'INSERT IGNORE';
        $galat                = $d->kunci( self::ID );
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( 500, $galat->get_error_data()['status'] );
        $this->assertNotSame( 'wpmgr_staging_sibuk', $galat->get_error_code() );
    }

    public function test_bersihkan_gagal_drop_tabel_menghasilkan_500_bukan_lagi_false(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'SELECT 1;' ) ) );
        $d->catat_tabel( self::ID, 'tmp', 'wpmgr_tmp_wp_posts' );
        $this->db->tabel      = array( 'wp_posts', 'wpmgr_tmp_wp_posts' );
        $this->db->gagal_pada = 'DROP TABLE';
        $galat                = $d->bersihkan( self::ID );
        $this->assertInstanceOf( WP_Error::class, $galat, 'Galat DROP TABLE harus 500 keras, bukan {lagi:false} yang tampak bersih.' );
        $this->assertSame( 500, $galat->get_error_data()['status'] );
        // Tabel yang gagal di-drop TETAP ada -- bukan hilang diam-diam.
        $this->assertContains( 'wpmgr_tmp_wp_posts', $this->db->tabel );
        // Fix N4 (review putaran 2): direktori (satu-satunya salinan
        // jurnal) TIDAK BOLEH terhapus saat DROP gagal -- draf round 1
        // menghapus direktori LEBIH DULU (sebelum mencoba DROP), jadi
        // jurnal hilang bersamanya walau tabelnya sendiri tidak pernah
        // terhapus -- mengorphankannya permanen.
        $this->assertDirectoryExists( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
        $this->assertSame( array( 'wpmgr_tmp_wp_posts' ), $d->keadaan( self::ID )['tabel_tmp'] );
    }

    // ---- Fix N4 (review putaran 2, Penting): retry setelah galat DROP
    // melanjutkan dari jurnal yang tersisa, lalu benar-benar menghapus
    // direktori setelah jurnal kosong. ----

    public function test_bersihkan_gagal_drop_lalu_retry_berhasil_menghapus_direktori(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $d->catat_tabel( self::ID, 'tmp', 'wpmgr_tmp_wp_posts' );
        $this->db->tabel      = array( 'wp_posts', 'wpmgr_tmp_wp_posts' );
        $this->db->gagal_pada = 'DROP TABLE';
        $galat                = $d->bersihkan( self::ID );
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( array( 'wp_posts', 'wpmgr_tmp_wp_posts' ), $this->db->tabel );

        $this->db->gagal_pada = null; // Percobaan kedua (retry) tanpa galat.
        $hasil                = $d->bersihkan( self::ID );
        $this->assertSame( array( 'lagi' => false ), $hasil );
        $this->assertSame( array( 'wp_posts' ), $this->db->tabel );
        $this->assertDirectoryDoesNotExist( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
        $this->assertArrayNotHasKey( 'wpmgr_dorong_kunci', $this->db->opsi );
    }

    // ---- Fix N4 (review putaran 3, Penting): bersihkan() sederhana
    // (bukan skenario direbut) selagi push LAIN memegang kunci -- jurnal
    // dan direktori tetap dipertahankan, bukan cuma kasus direbut. ----

    public function test_bersihkan_saat_kunci_dipegang_push_lain_mempertahankan_direktori_dan_jurnal(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $d->catat_tabel( self::ID, 'tmp', 'wpmgr_tmp_wp_posts' );
        // Push LAIN (ID2) entah bagaimana memegang kunci saat ini (mis.
        // dashboard memanggil bersihkan() untuk id yang salah, atau id ini
        // sendiri tidak pernah benar-benar memegang kuncinya).
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID2 . '|' . time();
        $this->db->tabel                      = array( 'wp_posts', 'wpmgr_tmp_wp_posts' );
        $this->assertSame( array( 'lagi' => true ), $d->bersihkan( self::ID ) );
        $this->assertDirectoryExists( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
        $this->assertSame( array( 'wpmgr_tmp_wp_posts' ), $d->keadaan( self::ID )['tabel_tmp'] );
        $this->assertSame( array( 'wp_posts', 'wpmgr_tmp_wp_posts' ), $this->db->tabel );
        $this->assertStringStartsWith( self::ID2 . '|', $this->db->opsi['wpmgr_dorong_kunci'] );
    }

    // ---- Fix I3 (review putaran 1): batas ruang disk, lewat penyedia yang
    // bisa disuntik untuk uji (tanpa memanggil disk_free_space() nyata). ----

    public function test_unggah_ditolak_507_saat_disk_hampir_penuh(): void {
        $d = $this->dorong();
        WPMGR_Staging_Dorong::atur_penyedia_disk_untuk_uji( function () {
            // Total 100 GB, bebas hanya 400 MB -- di bawah ambang max(512MB, 5%).
            return array( 'bebas' => 400 * 1024 * 1024, 'total' => 100 * 1024 * 1024 * 1024 );
        } );
        try {
            $galat = $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
            $this->assertInstanceOf( WP_Error::class, $galat );
            $this->assertSame( 'wpmgr_staging_disk_penuh', $galat->get_error_code() );
            $this->assertSame( 507, $galat->get_error_data()['status'] );
        } finally {
            WPMGR_Staging_Dorong::atur_penyedia_disk_untuk_uji( null );
        }
    }

    public function test_unggah_diterima_saat_disk_masih_cukup(): void {
        $d = $this->dorong();
        WPMGR_Staging_Dorong::atur_penyedia_disk_untuk_uji( function () {
            return array( 'bebas' => 50 * 1024 * 1024 * 1024, 'total' => 100 * 1024 * 1024 * 1024 );
        } );
        try {
            $hasil = $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
            $this->assertArrayHasKey( 'ok', $hasil );
        } finally {
            WPMGR_Staging_Dorong::atur_penyedia_disk_untuk_uji( null );
        }
    }

    public function test_unggah_ditolak_saat_total_push_melebihi_batas(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $k               = $d->keadaan( self::ID );
        $k['byte_total'] = WPMGR_Staging_Dorong::MAKS_TOTAL_UNGGAH;
        $d->simpan_keadaan( self::ID, $k );
        $galat = $d->unggah( $this->paket( self::ID, 1, 'sql', array( array( 'path' => 'sql' ) ), array( 'y' ) ) );
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( 'wpmgr_staging_disk_penuh', $galat->get_error_code() );
        $this->assertSame( 507, $galat->get_error_data()['status'] );
    }

    // ---- MINOR (review putaran 1): nomor yang sama dengan isi BERBEDA
    // adalah 409; isi SAMA (retry aman) tetap idempoten. ----

    public function test_unggah_nomor_sama_isi_berbeda_ditolak_409(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $galat = $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'BERBEDA' ) ) );
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( 'wpmgr_staging_nomor_bentrok', $galat->get_error_code() );
        $this->assertSame( 409, $galat->get_error_data()['status'] );
    }

    // ---- Fix C2/I2 (review putaran 1): pertahanan berlapis di
    // tabel_journal_aman() -- entri jurnal yang SALAH (mis. bug pemanggil
    // Task 8 mencatat nama tabel di luar prefix site ini) tetap ditolak
    // walau sudah tercatat di jurnal, bukan dipercaya begitu saja. ----

    public function test_bersihkan_mengabaikan_entri_jurnal_di_luar_prefix_site_ini(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'SELECT 1;' ) ) );
        $d->catat_tabel( self::ID, 'tmp', 'wpmgr_tmp_wp_posts' );
        // Entri jurnal yang SALAH: nama asli 'lain_posts' di luar prefix
        // site ini ('wp_') -- meniru tabel sementara site LAIN yang berbagi
        // database ini (prefix 'lain_', sama sekali tidak tumpang tindih).
        $d->catat_tabel( self::ID, 'tmp', 'wpmgr_tmp_lain_posts' );
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
