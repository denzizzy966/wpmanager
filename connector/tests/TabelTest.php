<?php
use PHPUnit\Framework\TestCase;

if ( ! defined( 'ARRAY_N' ) ) {
    define( 'ARRAY_N', 'ARRAY_N' );
}

final class WPMGR_FakeWpdbTabel {
    public $prefix         = 'wp_';
    public $kueri          = array();
    public $kolom          = array();
    public $pk             = array();
    public $baris          = array();
    public $ada            = true;
    public $tipe_tabel     = 'BASE TABLE';
    public $buat           = "CREATE TABLE `wp_x` (\n  `id` bigint(20) NOT NULL\n) ENGINE=InnoDB";
    public $last_error     = '';
    public $charset        = 'utf8mb4';
    // Fix round 2, item 2: nilai Avg_row_length yang dikembalikan SHOW
    // TABLE STATUS tiruan -- 0 (bawaan) berarti "tidak diketahui", jatuh
    // balik ke $sub penuh persis seperti sebelum fix ini ada.
    public $avg_row_length = 0;
    // Fix round 1, item 1: simulasikan get_results() ke-N (1-based, semua
    // panggilan get_results termasuk SHOW COLUMNS/SHOW KEYS) "gagal" --
    // mengembalikan array() (seperti wpdb sungguhan) TAPI mengisi
    // last_error, supaya bisa dibedakan dari "0 baris tersisa" yang sah.
    public $galat_pada_hitung_ke    = null;
    // Fix round 1, item 6g: simulasikan query() yang diawali teks ini
    // "gagal" (query() mengembalikan false, last_error terisi).
    public $galat_pada_query_awalan = null;
    private $hitung_get_results     = 0;

    public function esc_like( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function prepare( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        return vsprintf( str_replace( '%s', "'%s'", $sql ), $args );
    }

    public function get_var( $sql ) {
        $this->kueri[]     = $sql;
        $this->last_error  = '';
        return $this->ada && preg_match( "/LIKE '(.*)'/", $sql, $m ) ? stripslashes( $m[1] ) : null;
    }

    public function get_row( $sql, $format = null ) {
        $this->kueri[]    = $sql;
        $this->last_error = '';
        if ( 0 === strpos( $sql, 'SHOW FULL TABLES' ) ) {
            if ( ! $this->ada || ! preg_match( "/LIKE '(.*)'/", $sql, $m ) ) {
                return null;
            }
            return array( stripslashes( $m[1] ), $this->tipe_tabel );
        }
        if ( 0 === strpos( $sql, 'SHOW TABLE STATUS' ) ) {
            return array( 'Avg_row_length' => $this->avg_row_length );
        }
        return array( 'wp_x', $this->buat );
    }

    public function query( $sql ) {
        $this->kueri[] = $sql;
        if ( null !== $this->galat_pada_query_awalan && 0 === strpos( $sql, $this->galat_pada_query_awalan ) ) {
            $this->last_error = 'galat tiruan pada query()';
            return false;
        }
        $this->last_error = '';
        return true;
    }

    public function get_results( $sql, $format = null ) {
        $this->kueri[]    = $sql;
        $panggilan_ke     = ++$this->hitung_get_results;
        if ( 0 === strpos( $sql, 'SHOW COLUMNS' ) ) {
            $this->last_error = '';
            return $this->kolom;
        }
        if ( 0 === strpos( $sql, 'SHOW KEYS' ) ) {
            $this->last_error = '';
            return $this->pk;
        }
        if ( null !== $this->galat_pada_hitung_ke && $panggilan_ke === $this->galat_pada_hitung_ke ) {
            $this->last_error = 'galat tiruan pada get_results()';
            return array();
        }
        $this->last_error = '';
        // SELECT tiruan: cukup untuk PK bulat tunggal, PK komposit, dan
        // LIMIT/OFFSET.
        $baris = $this->baris;
        if ( preg_match( '/WHERE \(`([a-z_]+)`,`([a-z_]+)`\) > \(([^,]+),(.+?)\) ORDER/', $sql, $m ) ) {
            $ka = $m[1];
            $kb = $m[2];
            $va = self::uji_literal( $m[3] );
            $vb = self::uji_literal( $m[4] );
            $baris = array_values( array_filter( $baris, function ( $b ) use ( $ka, $kb, $va, $vb ) {
                $c = self::uji_bandingkan( $b[ $ka ], $va );
                if ( 0 !== $c ) {
                    return $c > 0;
                }
                return self::uji_bandingkan( $b[ $kb ], $vb ) > 0;
            } ) );
        } elseif ( preg_match( '/WHERE `([a-z_]+)` > (-?[0-9]+) /', $sql, $m ) ) {
            $baris = array_values( array_filter( $baris, function ( $b ) use ( $m ) {
                return (int) $b[ $m[1] ] > (int) $m[2];
            } ) );
        }
        preg_match( '/LIMIT ([0-9]+)(?: OFFSET ([0-9]+))?/', $sql, $m );
        return array_slice( $baris, isset( $m[2] ) ? (int) $m[2] : 0, (int) $m[1] );
    }

    private static function uji_literal( $lit ) {
        $lit = trim( $lit );
        if ( preg_match( '/^-?[0-9]+\z/', $lit ) ) {
            return (int) $lit;
        }
        return $lit;
    }

    private static function uji_bandingkan( $a, $b ) {
        if ( is_int( $b ) ) {
            return ( (int) $a ) <=> $b;
        }
        return strcmp( (string) $a, (string) $b );
    }
}

final class TabelTest extends TestCase {

    protected function setUp(): void {
        WPMGR_Staging_Tabel::$baris           = 2000;
        WPMGR_Staging_Tabel::$sub             = 200;
        WPMGR_Staging_Tabel::$maks_byte       = 6291456;
        WPMGR_Staging_Tabel::$maks_pernyataan = 1048576;
        WPMGR_Staging_Tabel::$maks_respon     = 8388608;
    }

    private function wpdb_ber_pk( $n ) {
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'id', 'Type' => 'bigint(20) unsigned' ), array( 'Field' => 'judul', 'Type' => 'text' ) );
        $w->pk    = array( array( 'Column_name' => 'id', 'Seq_in_index' => '1' ) );
        for ( $i = 1; $i <= $n; $i++ ) {
            $w->baris[] = array( 'id' => (string) $i, 'judul' => "judul $i" );
        }
        return $w;
    }

    public function test_esc_meniru_real_escape_string(): void {
        $this->assertSame( "a\\'b\\\"c\\\\d\\0e\\nf\\rg\\Zh", WPMGR_Staging_Tabel::esc( "a'b\"c\\d\0e\nf\rg\x1ah" ) );
        $this->assertSame( 'ü😀', WPMGR_Staging_Tabel::esc( 'ü😀' ) );
    }

    public function test_nilai_per_tipe(): void {
        $this->assertSame( 'NULL', WPMGR_Staging_Tabel::nilai( null, 'text' ) );
        $this->assertSame( "'abc'", WPMGR_Staging_Tabel::nilai( 'abc', 'varchar(10)' ) );
        $this->assertSame( "'12'", WPMGR_Staging_Tabel::nilai( '12', 'bigint(20) unsigned' ) );
        $this->assertSame( '0xff', WPMGR_Staging_Tabel::nilai( "\xff", 'varchar(10)' ) );
        $this->assertSame( '0x61', WPMGR_Staging_Tabel::nilai( 'a', 'longblob' ) );
        $this->assertSame( '0x01', WPMGR_Staging_Tabel::nilai( "\x01", 'bit(1)' ) );
        $this->assertSame( "''", WPMGR_Staging_Tabel::nilai( '', 'varbinary(16)' ) );
    }

    public function test_tipe_spasial_dianggap_biner(): void {
        // Item 6e, fix round 1: tipe spasial (WKB biner) selalu hex.
        $this->assertTrue( WPMGR_Staging_Tabel::biner( 'point' ) );
        $this->assertTrue( WPMGR_Staging_Tabel::biner( 'geometry' ) );
        $this->assertTrue( WPMGR_Staging_Tabel::biner( 'geometrycollection' ) );
        $this->assertTrue( WPMGR_Staging_Tabel::biner( 'multipolygon' ) );
        $this->assertTrue( WPMGR_Staging_Tabel::biner( 'linestring' ) );
        $this->assertSame( '0x' . bin2hex( "\x00\x01" ), WPMGR_Staging_Tabel::nilai( "\x00\x01", 'point' ) );
    }

    public function test_potongan_pertama_membawa_create_lalu_berlanjut_dengan_kursor(): void {
        WPMGR_Staging_Tabel::$baris = 3;
        WPMGR_Staging_Tabel::$sub   = 2;
        $w = $this->wpdb_ber_pk( 5 );
        $a = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertStringStartsWith( "DROP TABLE IF EXISTS `wp_x`;\nCREATE TABLE `wp_x`", $a['sql'] );
        $this->assertStringContainsString( "INSERT INTO `wp_x` (`id`,`judul`) VALUES\n('1','judul 1'),\n('2','judul 2')", $a['sql'] );
        $this->assertStringContainsString( "('3','judul 3');\n", $a['sql'] );
        $this->assertSame( 3, $a['baris'] );
        $this->assertFalse( $a['selesai'] );
        $this->assertSame( 'pk', $a['mode'] );
        $this->assertContains( 'SELECT * FROM `wp_x` ORDER BY `id` LIMIT 2', $w->kueri );
        $this->assertContains( 'SELECT * FROM `wp_x` WHERE `id` > 2 ORDER BY `id` LIMIT 1', $w->kueri );
        $this->assertContains( 'START TRANSACTION WITH CONSISTENT SNAPSHOT', $w->kueri );
        $this->assertContains( 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ', $w->kueri );
        $this->assertSame( array( 'pk' => array( array( 's' => '3' ) ) ), WPMGR_Staging_Tabel::urai_kursor( $a['kursor'] ) );

        $b = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $a['kursor'] );
        $this->assertStringNotContainsString( 'DROP TABLE', $b['sql'] );
        $this->assertStringContainsString( "('4','judul 4'),\n('5','judul 5');\n", $b['sql'] );
        $this->assertTrue( $b['selesai'] );
        $this->assertNull( $b['kursor'] );
        $this->assertSame( 2, $b['baris'] );
    }

    public function test_tanpa_pk_dan_biner_memakai_offset_dan_hex(): void {
        WPMGR_Staging_Tabel::$sub = 2;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'data', 'Type' => 'mediumblob' ), array( 'Field' => 'catatan', 'Type' => 'varchar(191)' ) );
        $w->baris = array(
            array( 'data' => "\0\xff\x1a", 'catatan' => "it's" ),
            array( 'data' => null, 'catatan' => "baris\nbaru" ),
            array( 'data' => '', 'catatan' => "\xc3\x28" ),
        );
        $a = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertContains( 'SELECT * FROM `wp_x` LIMIT 2 OFFSET 0', $w->kueri );
        $this->assertContains( 'SELECT * FROM `wp_x` LIMIT 2 OFFSET 2', $w->kueri );
        $this->assertStringContainsString( "(0x00ff1a,'it\\'s')", $a['sql'] );
        $this->assertStringContainsString( "(NULL,'baris\\nbaru')", $a['sql'] );
        $this->assertStringContainsString( "('',0xc328)", $a['sql'] );
        $this->assertTrue( $a['selesai'] );
        $this->assertSame( 3, $a['baris'] );
        $this->assertSame( 'offset', $a['mode'] );
        // Tidak ada baris baru mentah di dalam literal: setiap pernyataan
        // berakhir tepat di ";\n", syarat pemecah pernyataan di Task 7.
        foreach ( explode( ";\n", rtrim( $a['sql'] ) ) as $pernyataan ) {
            $this->assertStringNotContainsString( "'baris\nbaru'", $pernyataan );
        }
        $this->assertSame( 1, preg_match( '//u', $a['sql'] ) );
    }

    public function test_kursor_offset_melanjutkan(): void {
        WPMGR_Staging_Tabel::$baris = 2;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'a', 'Type' => 'int(11)' ) );
        $w->baris = array( array( 'a' => '7' ), array( 'a' => '8' ), array( 'a' => '9' ) );
        $a = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertSame( array( 'o' => 2 ), WPMGR_Staging_Tabel::urai_kursor( $a['kursor'] ) );
        $b = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $a['kursor'] );
        $this->assertStringContainsString( "('9');\n", $b['sql'] );
        $this->assertTrue( $b['selesai'] );
    }

    public function test_batas_byte_memotong_tanpa_duplikat(): void {
        WPMGR_Staging_Tabel::$maks_byte = 10;
        $w      = $this->wpdb_ber_pk( 4 );
        $semua  = '';
        $kursor = '';
        for ( $i = 0; $i < 10; $i++ ) {
            $h      = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $kursor );
            $semua .= $h['sql'];
            if ( $h['selesai'] ) {
                break;
            }
            $this->assertGreaterThanOrEqual( 1, $h['baris'] );
            $kursor = $h['kursor'];
        }
        for ( $n = 1; $n <= 4; $n++ ) {
            $this->assertSame( 1, substr_count( $semua, "'judul $n'" ) );
        }
    }

    public function test_kunci_bukan_utf8_menjadi_hex_di_kursor_dan_where(): void {
        WPMGR_Staging_Tabel::$baris = 1;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'kunci', 'Type' => 'binary(2)' ) );
        $w->pk    = array( array( 'Column_name' => 'kunci', 'Seq_in_index' => '1' ) );
        $w->baris = array( array( 'kunci' => "\xff\x01" ), array( 'kunci' => "\xff\x02" ) );
        $a = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertSame( array( 'pk' => array( array( 'x' => 'ff01' ) ) ), WPMGR_Staging_Tabel::urai_kursor( $a['kursor'] ) );
        WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $a['kursor'] );
        $this->assertContains( 'SELECT * FROM `wp_x` WHERE `kunci` > 0xff01 ORDER BY `kunci` LIMIT 1', $w->kueri );
    }

    public function test_kursor_rusak_ditolak(): void {
        $w = $this->wpdb_ber_pk( 2 );
        foreach ( array( 'bukan base64!!', base64_encode( '{"pk":[{"s":"1 OR 1=1"}]}' ),
                         base64_encode( '{"pk":[{"s":"1"},{"s":"2"}]}' ), base64_encode( '{"o":-1}' ),
                         base64_encode( '{"pk":[{"x":"zz"}]}' ), base64_encode( '"teks"' ) ) as $k ) {
            $hasil = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $k );
            $this->assertInstanceOf( WP_Error::class, $hasil, $k );
            $this->assertSame( 'wpmgr_staging_kursor', $hasil->get_error_code() );
        }
    }

    public function test_kursor_bukan_string_atau_null_ditolak(): void {
        // Item 6c, fix round 1: bentuk kursor selain string/null (array,
        // int, bool, float) ditolak 400 -- bukan fatal (string) cast.
        $w = $this->wpdb_ber_pk( 1 );
        foreach ( array( array( 'x' ), 123, true, 1.5 ) as $k ) {
            $hasil = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $k );
            $this->assertInstanceOf( WP_Error::class, $hasil );
            $this->assertSame( 'wpmgr_staging_kursor', $hasil->get_error_code() );
        }
        // null tetap sah -- sama seperti '' (mulai dari awal).
        $ok = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', null );
        $this->assertIsArray( $ok );
    }

    public function test_tabel_tidak_sah_atau_tidak_ada(): void {
        $w = $this->wpdb_ber_pk( 1 );
        foreach ( array( 'wp_x; DROP TABLE wp_users', 'lain_x', '', null, 'wp_`x' ) as $t ) {
            $this->assertSame( 'wpmgr_staging_tabel', WPMGR_Staging_Tabel::ekspor( $w, $t, '' )->get_error_code() );
        }
        $w->ada = false;
        $galat  = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertSame( array( 'status' => 404 ), $galat->get_error_data() );
    }

    public function test_view_ditolak_400(): void {
        // Item 6b, fix round 1: SHOW FULL TABLES melaporkan Table_type;
        // VIEW (atau jenis lain bukan BASE TABLE) ditolak 400.
        $w             = $this->wpdb_ber_pk( 1 );
        $w->tipe_tabel = 'VIEW';
        $hasil         = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_tabel', $hasil->get_error_code() );
        $this->assertSame( array( 'status' => 400 ), $hasil->get_error_data() );
    }

    public function test_kolom_generated_mysql_dilewati_dari_daftar_dan_nilai(): void {
        // Item 4, fix round 1 (Penting): kolom generated tidak boleh
        // ditulis di INSERT -- MySQL menolaknya saat impor.
        WPMGR_Staging_Tabel::$baris = 2;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array(
            array( 'Field' => 'a', 'Type' => 'int(11)', 'Extra' => '' ),
            array( 'Field' => 'b', 'Type' => 'int(11)', 'Extra' => 'VIRTUAL GENERATED' ),
            array( 'Field' => 'c', 'Type' => 'int(11)', 'Extra' => 'STORED GENERATED' ),
        );
        $w->baris = array( array( 'a' => '1', 'b' => '2', 'c' => '3' ) );
        $a        = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertStringContainsString( "INSERT INTO `wp_x` (`a`) VALUES\n('1')", $a['sql'] );
        $this->assertStringNotContainsString( '`b`', $a['sql'] );
        $this->assertStringNotContainsString( '`c`', $a['sql'] );
    }

    public function test_kolom_generated_mariadb_dilewati(): void {
        // MariaDB melaporkan Extra "VIRTUAL"/"PERSISTENT" (bukan kata
        // "GENERATED") untuk kolom generated -- ejaan berbeda dari MySQL.
        WPMGR_Staging_Tabel::$baris = 2;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array(
            array( 'Field' => 'a', 'Type' => 'int(11)', 'Extra' => '' ),
            array( 'Field' => 'b', 'Type' => 'int(11)', 'Extra' => 'VIRTUAL' ),
            array( 'Field' => 'c', 'Type' => 'int(11)', 'Extra' => 'PERSISTENT' ),
        );
        $w->baris = array( array( 'a' => '1', 'b' => '2', 'c' => '3' ) );
        $a        = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertStringContainsString( "INSERT INTO `wp_x` (`a`) VALUES\n('1')", $a['sql'] );
        $this->assertStringNotContainsString( '`b`', $a['sql'] );
        $this->assertStringNotContainsString( '`c`', $a['sql'] );
    }

    public function test_galat_mid_ekspor_menjadi_500_bukan_selesai_diam2(): void {
        // Item 1, fix round 1 (Kritis): SATU query yang gagal di tengah
        // (mengembalikan array() seperti wpdb sungguhan, TAPI last_error
        // terisi) harus menjadi galat 500, bukan "selesai" dengan baris
        // yang hilang diam-diam.
        WPMGR_Staging_Tabel::$baris = 10;
        WPMGR_Staging_Tabel::$sub   = 2;
        $w = $this->wpdb_ber_pk( 6 );
        // Panggilan get_results(): #1 SHOW COLUMNS, #2 SHOW KEYS,
        // #3 SELECT pertama (sukses, 2 baris), #4 SELECT kedua -- gagal.
        $w->galat_pada_hitung_ke = 4;
        $hasil                   = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_tabel', $hasil->get_error_code() );
        $this->assertSame( array( 'status' => 500 ), $hasil->get_error_data() );
        $this->assertContains( 'ROLLBACK', $w->kueri );
    }

    public function test_start_transaction_gagal_mengembalikan_500(): void {
        // Item 6g, fix round 1: hasil START TRANSACTION diperiksa.
        $w                          = $this->wpdb_ber_pk( 1 );
        $w->galat_pada_query_awalan = 'START TRANSACTION';
        $hasil                      = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( array( 'status' => 500 ), $hasil->get_error_data() );
    }

    public function test_tenggat_berhenti_setelah_subquery_saat_ini(): void {
        // Item 2, fix round 1 (Penting): tenggat dicek di AWAL setiap sub-
        // batch (bukan di tengah satu SELECT) -- jaminan progres: sub-batch
        // PERTAMA tetap diproses walau tenggat sudah lewat sebelum mulai.
        WPMGR_Staging_Tabel::$baris = 100;
        WPMGR_Staging_Tabel::$sub   = 1;
        $w       = $this->wpdb_ber_pk( 5 );
        $tenggat = microtime( true ) - 1;
        $a       = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '', $tenggat );
        $this->assertFalse( $a['selesai'] );
        $this->assertSame( 1, $a['baris'] );
        $this->assertNotNull( $a['kursor'] );
    }

    public function test_baris_terlalu_besar_ditolak_bukan_dilewati(): void {
        // Item 3c, fix round 1 (Penting): baris yang SENDIRIAN melebihi
        // batas KERAS tidak pernah bisa dikirim -- galat tetap, bukan
        // dilewati diam-diam (ekspor ini juga memberi data ke snapshot).
        WPMGR_Staging_Tabel::$maks_respon = 20;
        $w     = $this->wpdb_ber_pk( 1 );
        $hasil = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_baris_terlalu_besar', $hasil->get_error_code() );
        $this->assertSame( array( 'status' => 413 ), $hasil->get_error_data() );
        $this->assertContains( 'ROLLBACK', $w->kueri );
    }

    public function test_sub_batch_mengecil_setelah_baris_lebar(): void {
        // Item 3b, fix round 1 (Penting): LIMIT sub-query berikutnya
        // mengecil setelah sub-batch memuat baris jauh di atas rata-rata --
        // membatasi berapa banyak baris besar dimuat ke memori sekaligus.
        WPMGR_Staging_Tabel::$baris     = 20;
        WPMGR_Staging_Tabel::$sub       = 8;
        WPMGR_Staging_Tabel::$maks_byte = 2000;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'id', 'Type' => 'int(11)' ), array( 'Field' => 'teks', 'Type' => 'text' ) );
        $w->baris[] = array( 'id' => '1', 'teks' => str_repeat( 'x', 300 ) );
        for ( $i = 2; $i <= 20; $i++ ) {
            $w->baris[] = array( 'id' => (string) $i, 'teks' => 'y' );
        }
        WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $limit_select = array();
        foreach ( $w->kueri as $q ) {
            if ( 0 === strpos( $q, 'SELECT * FROM' ) && preg_match( '/LIMIT ([0-9]+)/', $q, $m ) ) {
                $limit_select[] = (int) $m[1];
            }
        }
        $this->assertSame( 8, $limit_select[0] );
        $this->assertLessThan( 8, $limit_select[1] );
    }

    public function test_pernyataan_dipecah_sebelum_melebihi_batas(): void {
        // Item 6j / 6a, fix round 1: pemisahan pernyataan pada
        // $maks_pernyataan kecil menghasilkan lebih dari satu INSERT.
        WPMGR_Staging_Tabel::$baris           = 10;
        WPMGR_Staging_Tabel::$sub             = 10;
        WPMGR_Staging_Tabel::$maks_pernyataan = 50;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'v', 'Type' => 'varchar(20)' ) );
        for ( $i = 1; $i <= 5; $i++ ) {
            $w->baris[] = array( 'v' => str_repeat( (string) $i, 10 ) );
        }
        $a          = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $pernyataan = array_values( array_filter( explode( ";\n", $a['sql'] ), function ( $p ) {
            return false !== strpos( $p, 'INSERT INTO' );
        } ) );
        $this->assertGreaterThan( 1, count( $pernyataan ) );
        foreach ( $pernyataan as $p ) {
            $this->assertLessThanOrEqual( 50 + 64, strlen( $p ) );
        }
        $this->assertSame( 5, $a['baris'] );
        $this->assertTrue( $a['selesai'] );
    }

    public function test_pk_float_diperlakukan_tanpa_pk(): void {
        // Item 6d, fix round 1: FLOAT/DOUBLE PK -> mode offset.
        WPMGR_Staging_Tabel::$baris = 2;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'skor', 'Type' => 'double' ) );
        $w->pk    = array( array( 'Column_name' => 'skor', 'Seq_in_index' => '1' ) );
        $w->baris = array( array( 'skor' => '1.5' ), array( 'skor' => '2.5' ) );
        $a        = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertContains( 'SELECT * FROM `wp_x` LIMIT 2 OFFSET 0', $w->kueri );
        $this->assertSame( 'offset', $a['mode'] );
    }

    public function test_mode_pk_dan_offset_di_meta(): void {
        $w1 = $this->wpdb_ber_pk( 1 );
        $a1 = WPMGR_Staging_Tabel::ekspor( $w1, 'wp_x', '' );
        $this->assertSame( 'pk', $a1['mode'] );

        $w2        = new WPMGR_FakeWpdbTabel();
        $w2->kolom = array( array( 'Field' => 'a', 'Type' => 'int(11)' ) );
        $w2->baris = array( array( 'a' => '1' ) );
        $a2        = WPMGR_Staging_Tabel::ekspor( $w2, 'wp_x', '' );
        $this->assertSame( 'offset', $a2['mode'] );
    }

    public function test_pk_komposit_where_tuple(): void {
        // Item 6j: PK komposit, dengan wpdb tiruan mengevaluasi (a,b) > (x,y).
        WPMGR_Staging_Tabel::$baris = 2;
        WPMGR_Staging_Tabel::$sub   = 2;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array(
            array( 'Field' => 'a', 'Type' => 'int(11)' ),
            array( 'Field' => 'b', 'Type' => 'int(11)' ),
            array( 'Field' => 'v', 'Type' => 'text' ),
        );
        $w->pk    = array(
            array( 'Column_name' => 'a', 'Seq_in_index' => '1' ),
            array( 'Column_name' => 'b', 'Seq_in_index' => '2' ),
        );
        $w->baris = array(
            array( 'a' => '1', 'b' => '1', 'v' => 'satu' ),
            array( 'a' => '1', 'b' => '2', 'v' => 'dua' ),
            array( 'a' => '2', 'b' => '1', 'v' => 'tiga' ),
        );
        $x = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertContains( 'SELECT * FROM `wp_x` ORDER BY `a`,`b` LIMIT 2', $w->kueri );
        $this->assertStringContainsString( "('1','1','satu'),\n('1','2','dua')", $x['sql'] );
        $this->assertFalse( $x['selesai'] );

        $y = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $x['kursor'] );
        $this->assertContains( 'SELECT * FROM `wp_x` WHERE (`a`,`b`) > (1,2) ORDER BY `a`,`b` LIMIT 2', $w->kueri );
        $this->assertStringContainsString( "('2','1','tiga')", $y['sql'] );
        $this->assertTrue( $y['selesai'] );
    }

    public function test_pk_varchar_dengan_karakter_berbahaya(): void {
        // Item 5, fix round 1 (Penting): literal kursor untuk PK non-
        // integer memakai hex berprawalan charset, tidak bergantung
        // sql_mode -- diuji dengan kutip, backslash, dan multibyte UTF-8.
        WPMGR_Staging_Tabel::$baris = 1;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'kode', 'Type' => 'varchar(64)' ) );
        $w->pk    = array( array( 'Column_name' => 'kode', 'Seq_in_index' => '1' ) );
        $nilai_pk = "a'b\\c\xc3\xbc"; // kutip, backslash, dan 'ü' multibyte UTF-8.
        $w->baris = array( array( 'kode' => $nilai_pk ), array( 'kode' => 'z' ) );
        $a        = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertSame( array( 'pk' => array( array( 's' => $nilai_pk ) ) ), WPMGR_Staging_Tabel::urai_kursor( $a['kursor'] ) );
        WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $a['kursor'] );
        $harap = 'SELECT * FROM `wp_x` WHERE `kode` > _utf8mb4 0x' . bin2hex( $nilai_pk ) . ' ORDER BY `kode` LIMIT 1';
        $this->assertContains( $harap, $w->kueri );
    }

    public function test_pk_binary_pakai_prawalan_binary(): void {
        // literal_kunci(): kolom PK biner memakai prawalan _binary, bukan
        // _utf8mb4, walau nilainya kebetulan UTF-8 sah.
        $this->assertSame( '_binary 0x6162', WPMGR_Staging_Tabel::literal_kunci( array( 's' => 'ab' ), 'varbinary(16)' ) );
        $this->assertSame( '_utf8mb4 0x6162', WPMGR_Staging_Tabel::literal_kunci( array( 's' => 'ab' ), 'varchar(16)' ) );
        $this->assertSame( "''", WPMGR_Staging_Tabel::literal_kunci( array( 's' => '' ), 'varchar(16)' ) );
    }

    public function test_default_generated_bukan_kolom_generated(): void {
        // Item 1, fix round 2 (Kritis, regresi): MySQL >= 8.0.13 menandai
        // kolom BIASA berdefault EKSPRESI (mis. DEFAULT CURRENT_TIMESTAMP,
        // atau ON UPDATE CURRENT_TIMESTAMP) sebagai Extra "DEFAULT_GENERATED"
        // / "DEFAULT_GENERATED on update CURRENT_TIMESTAMP" -- BUKAN kolom
        // generated sungguhan. Kolom seperti ini harus TETAP ditulis di
        // INSERT dengan nilai sungguhannya, bukan dilewati (yang membuat
        // MySQL/MariaDB mengisinya NOW() saat impor -- kerusakan diam-diam).
        WPMGR_Staging_Tabel::$baris = 2;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array(
            array( 'Field' => 'a', 'Type' => 'int(11)', 'Extra' => '' ),
            array( 'Field' => 'ts', 'Type' => 'timestamp', 'Extra' => 'DEFAULT_GENERATED' ),
            array( 'Field' => 'tu', 'Type' => 'timestamp', 'Extra' => 'DEFAULT_GENERATED on update CURRENT_TIMESTAMP' ),
        );
        $w->baris = array( array( 'a' => '1', 'ts' => '2024-01-01 00:00:00', 'tu' => '2024-01-02 00:00:00' ) );
        $a        = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertStringContainsString( '`a`,`ts`,`tu`', $a['sql'] );
        $this->assertStringContainsString( "('1','2024-01-01 00:00:00','2024-01-02 00:00:00')", $a['sql'] );
    }

    public function test_sub_awal_kecil_dari_avg_row_length_besar(): void {
        // Item 2, fix round 2: LIMIT sub-query PERTAMA pada request
        // dihitung dari Avg_row_length (SHOW TABLE STATUS), bukan selalu
        // mulai buta di $sub penuh -- tabel berbaris lebar tidak lagi
        // memuat ~200 baris besar ke memori hanya untuk memakai segelintir
        // sebelum anggaran lunak habis.
        WPMGR_Staging_Tabel::$baris     = 100;
        WPMGR_Staging_Tabel::$sub       = 200;
        WPMGR_Staging_Tabel::$maks_byte = 20000;
        $w                 = new WPMGR_FakeWpdbTabel();
        $w->avg_row_length = 5000;
        $w->kolom          = array( array( 'Field' => 'id', 'Type' => 'int(11)' ), array( 'Field' => 'teks', 'Type' => 'text' ) );
        for ( $i = 1; $i <= 100; $i++ ) {
            $w->baris[] = array( 'id' => (string) $i, 'teks' => 'y' );
        }
        WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $limit_pertama = null;
        foreach ( $w->kueri as $q ) {
            if ( 0 === strpos( $q, 'SELECT * FROM' ) && preg_match( '/LIMIT ([0-9]+)/', $q, $m ) ) {
                $limit_pertama = (int) $m[1];
                break;
            }
        }
        // clamp(floor(20000/5000/2),1,200) = clamp(2,1,200) = 2.
        $this->assertSame( 2, $limit_pertama );
        $this->assertContains( "SHOW TABLE STATUS LIKE '" . addcslashes( 'wp_x', '_%\\' ) . "'", $w->kueri );
    }

    public function test_sub_tumbuh_kembali_setelah_batch_kecil(): void {
        // Item 2, fix round 2: sub-batch yang mengecil karena satu baris
        // lebar TIDAK boleh terkunci kecil untuk sisa request -- tumbuh
        // kembali (dikali dua) setelah sub-batch yang seluruhnya kecil.
        WPMGR_Staging_Tabel::$baris     = 40;
        WPMGR_Staging_Tabel::$sub       = 8;
        WPMGR_Staging_Tabel::$maks_byte = 2000;
        $w          = new WPMGR_FakeWpdbTabel();
        $w->kolom   = array( array( 'Field' => 'id', 'Type' => 'int(11)' ), array( 'Field' => 'teks', 'Type' => 'text' ) );
        $w->baris[] = array( 'id' => '1', 'teks' => str_repeat( 'x', 300 ) ); // baris lebar pertama
        for ( $i = 2; $i <= 40; $i++ ) {
            $w->baris[] = array( 'id' => (string) $i, 'teks' => 'y' );
        }
        WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $limit = array();
        foreach ( $w->kueri as $q ) {
            if ( 0 === strpos( $q, 'SELECT * FROM' ) && preg_match( '/LIMIT ([0-9]+)/', $q, $m ) ) {
                $limit[] = (int) $m[1];
            }
        }
        // batch0=8 (lebar -> mengecil), batch1=4 (kecil semua -> tumbuh),
        // batch2=8 (dijepit ke $sub, bukan 16).
        $this->assertSame( 8, $limit[0] );
        $this->assertSame( 4, $limit[1] );
        $this->assertSame( 8, $limit[2] );
    }

    public function test_baris_yang_akan_melampaui_anggaran_tidak_disertakan(): void {
        // Item 2 (test tersemat), fix round 2: anggaran lunak dicek
        // SEBELUM menambahkan, bukan sesudah -- baris yang akan melampaui
        // anggaran TIDAK muncul di potongan ini sama sekali (diambil ulang
        // lewat kursor pada request berikutnya). Gagal pada logika lama
        // (tambah-lalu-cek) karena baris itu akan tetap muncul di sql
        // sebelum potongan berhenti.
        // Ambil baris pertama dulu SENDIRIAN ($baris=1) -- supaya DROP+
        // CREATE TABLE di potongan pertama (dan baris 1 itu sendiri, yang
        // selalu dikecualikan dari anggaran lunak sebagai baris pertama
        // REQUEST) tidak ikut memakan anggaran lunak yang diuji di bawah.
        $w                          = $this->wpdb_ber_pk( 3 );
        WPMGR_Staging_Tabel::$baris = 1;
        $awal                       = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertStringContainsString( "'judul 1'", $awal['sql'] );
        $this->assertFalse( $awal['selesai'] );

        WPMGR_Staging_Tabel::$baris     = 2000;
        WPMGR_Staging_Tabel::$maks_byte = 60; // muat 1 baris ('judul 2'), tidak muat 2 ('judul 2'+'judul 3').
        $a = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $awal['kursor'] );
        $this->assertStringContainsString( "'judul 2'", $a['sql'] );
        $this->assertStringNotContainsString( "'judul 3'", $a['sql'] );
        $this->assertFalse( $a['selesai'] );
        $this->assertSame( 1, $a['baris'] );
    }

    public function test_prawalan_utf8_ikuti_charset_wpdb(): void {
        // Item 4, fix round 2: prawalan literal hex ikut $wpdb->charset
        // sungguhan (bukan "_utf8mb4" tetap) bila charset itu sah.
        WPMGR_Staging_Tabel::$baris = 1;
        $w          = new WPMGR_FakeWpdbTabel();
        $w->charset = 'latin1';
        $w->kolom   = array( array( 'Field' => 'kode', 'Type' => 'varchar(64)' ) );
        $w->pk      = array( array( 'Column_name' => 'kode', 'Seq_in_index' => '1' ) );
        $w->baris   = array( array( 'kode' => 'a' ), array( 'kode' => 'b' ) );
        $a          = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $a['kursor'] );
        $this->assertContains( 'SELECT * FROM `wp_x` WHERE `kode` > _latin1 0x61 ORDER BY `kode` LIMIT 1', $w->kueri );
    }

    public function test_prawalan_charset_tidak_sah_jatuh_ke_utf8mb4(): void {
        WPMGR_Staging_Tabel::$baris = 1;
        $w          = new WPMGR_FakeWpdbTabel();
        $w->charset = "utf8mb4'; DROP"; // tidak sah -- harus jatuh ke default.
        $w->kolom   = array( array( 'Field' => 'kode', 'Type' => 'varchar(64)' ) );
        $w->pk      = array( array( 'Column_name' => 'kode', 'Seq_in_index' => '1' ) );
        $w->baris   = array( array( 'kode' => 'a' ), array( 'kode' => 'b' ) );
        $a          = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $a['kursor'] );
        $this->assertContains( 'SELECT * FROM `wp_x` WHERE `kode` > _utf8mb4 0x61 ORDER BY `kode` LIMIT 1', $w->kueri );
    }
}
