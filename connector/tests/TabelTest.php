<?php
use PHPUnit\Framework\TestCase;

if ( ! defined( 'ARRAY_N' ) ) {
    define( 'ARRAY_N', 'ARRAY_N' );
}

final class WPMGR_FakeWpdbTabel {
    public $prefix = 'wp_';
    public $kueri  = array();
    public $kolom  = array();
    public $pk     = array();
    public $baris  = array();
    public $ada    = true;
    public $buat   = "CREATE TABLE `wp_x` (\n  `id` bigint(20) NOT NULL\n) ENGINE=InnoDB";

    public function esc_like( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function prepare( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        return vsprintf( str_replace( '%s', "'%s'", $sql ), $args );
    }

    public function get_var( $sql ) {
        $this->kueri[] = $sql;
        return $this->ada && preg_match( "/LIKE '(.*)'/", $sql, $m ) ? stripslashes( $m[1] ) : null;
    }

    public function get_row( $sql, $format = null ) {
        $this->kueri[] = $sql;
        return array( 'wp_x', $this->buat );
    }

    public function query( $sql ) {
        $this->kueri[] = $sql;
        return true;
    }

    public function get_results( $sql, $format = null ) {
        $this->kueri[] = $sql;
        if ( 0 === strpos( $sql, 'SHOW COLUMNS' ) ) {
            return $this->kolom;
        }
        if ( 0 === strpos( $sql, 'SHOW KEYS' ) ) {
            return $this->pk;
        }
        // SELECT tiruan: cukup untuk PK bulat tunggal dan LIMIT/OFFSET.
        $baris = $this->baris;
        if ( preg_match( '/WHERE `([a-z_]+)` > (-?[0-9]+) /', $sql, $m ) ) {
            $baris = array_values( array_filter( $baris, function ( $b ) use ( $m ) {
                return (int) $b[ $m[1] ] > (int) $m[2];
            } ) );
        }
        preg_match( '/LIMIT ([0-9]+)(?: OFFSET ([0-9]+))?/', $sql, $m );
        return array_slice( $baris, isset( $m[2] ) ? (int) $m[2] : 0, (int) $m[1] );
    }
}

final class TabelTest extends TestCase {

    protected function setUp(): void {
        WPMGR_Staging_Tabel::$baris     = 2000;
        WPMGR_Staging_Tabel::$sub       = 200;
        WPMGR_Staging_Tabel::$maks_byte = 6291456;
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
        $this->assertContains( 'SELECT * FROM `wp_x` ORDER BY `id` LIMIT 2', $w->kueri );
        $this->assertContains( 'SELECT * FROM `wp_x` WHERE `id` > 2 ORDER BY `id` LIMIT 1', $w->kueri );
        $this->assertContains( 'START TRANSACTION WITH CONSISTENT SNAPSHOT', $w->kueri );
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
        $w     = $this->wpdb_ber_pk( 4 );
        $semua = '';
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

    public function test_tabel_tidak_sah_atau_tidak_ada(): void {
        $w = $this->wpdb_ber_pk( 1 );
        foreach ( array( 'wp_x; DROP TABLE wp_users', 'lain_x', '', null, 'wp_`x' ) as $t ) {
            $this->assertSame( 'wpmgr_staging_tabel', WPMGR_Staging_Tabel::ekspor( $w, $t, '' )->get_error_code() );
        }
        $w->ada = false;
        $galat  = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertSame( array( 'status' => 404 ), $galat->get_error_data() );
    }
}
