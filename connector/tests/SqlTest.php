<?php
use PHPUnit\Framework\TestCase;

final class SqlTest extends TestCase {

    const CONTOH = "/*!40101 SET NAMES utf8mb4 */;\n"
        . "-- komentar; dengan titik koma\n"
        . "# komentar lain;\n"
        . "DROP TABLE IF EXISTS `wp_posts`;\n"
        . "CREATE TABLE `wp_posts` (\n  `ID` bigint(20) NOT NULL, `a` text /* komentar; */\n);\n"
        . "INSERT INTO `wp_posts` (`ID`,`a`) VALUES\n(1,'titik;koma'),\n(2,'kutip \\' dan \\\\'),\n(3,'ganda '' kutip'),\n(4,\"ganda \\\" ; \"),\n(5,0x3b27);\n"
        . "INSERT INTO `wp_x`; `y`;";

    private function pecah_sekaligus( $data ) {
        $p     = new WPMGR_Staging_Sql();
        $hasil = $p->tambah( $data );
        return array( $hasil, $p->sisa() );
    }

    public function test_memecah_dengan_sadar_kutip_dan_komentar(): void {
        list( $hasil, $sisa ) = $this->pecah_sekaligus( self::CONTOH );
        $teks = array_map( function ( $h ) {
            return trim( $h[0] );
        }, $hasil );
        $this->assertSame( '/*!40101 SET NAMES utf8mb4 */', $teks[0] );
        $this->assertStringEndsWith( 'DROP TABLE IF EXISTS `wp_posts`', $teks[1] );
        $this->assertStringStartsWith( 'CREATE TABLE `wp_posts`', $teks[2] );
        $this->assertStringContainsString( "(5,0x3b27)", $teks[3] );
        $this->assertStringContainsString( "'titik;koma'", $teks[3] );
        $this->assertSame( 'INSERT INTO `wp_x`', $teks[4] );
        $this->assertSame( '`y`', $teks[5] );
        $this->assertCount( 6, $hasil );
        $this->assertSame( '', $sisa );
        $akhir = end( $hasil );
        $this->assertSame( strlen( self::CONTOH ), $akhir[1] );
    }

    public function test_hasil_sama_walau_diumpankan_per_byte(): void {
        list( $sekaligus ) = $this->pecah_sekaligus( self::CONTOH );
        $p      = new WPMGR_Staging_Sql();
        $bertahap = array();
        for ( $i = 0; $i < strlen( self::CONTOH ); $i++ ) {
            foreach ( $p->tambah( self::CONTOH[ $i ] ) as $h ) {
                $bertahap[] = $h;
            }
        }
        $this->assertSame( $sekaligus, $bertahap );
    }

    public function test_offset_awal_dan_sisa(): void {
        $p     = new WPMGR_Staging_Sql( 1000 );
        $hasil = $p->tambah( "SET a=1;\nINSERT INTO `wp_x` VALUES ('belum" );
        $this->assertSame( array( array( 'SET a=1', 1008 ) ), $hasil );
        $this->assertSame( "\nINSERT INTO `wp_x` VALUES ('belum", $p->sisa() );
    }

    public function test_ubah_mengganti_nama_tabel(): void {
        $this->assertSame( 'DROP TABLE IF EXISTS `wpmgr_tmp_wp_posts`',
            WPMGR_Staging_Sql::ubah( "\n-- x\nDROP TABLE IF EXISTS `wp_posts`", 'wp_' ) );
        $this->assertSame( "CREATE TABLE `wpmgr_tmp_wp_posts` (\n `ID` int)",
            WPMGR_Staging_Sql::ubah( "CREATE TABLE `wp_posts` (\n `ID` int)", 'wp_' ) );
        $this->assertSame( "INSERT INTO `wpmgr_tmp_wp_posts` (`ID`) VALUES (1)",
            WPMGR_Staging_Sql::ubah( "INSERT INTO `wp_posts` (`ID`) VALUES (1)", 'wp_' ) );
        // /*!40000 ... */ adalah komentar berversi; pemecah memperlakukannya
        // sebagai komentar, jadi ALTER di dalamnya dilewati (hanya optimasi).
        $this->assertNull( WPMGR_Staging_Sql::ubah( '/*!40000 ALTER TABLE `wp_posts` DISABLE KEYS */', 'wp_' ) );
        $this->assertSame( 'ALTER TABLE `wpmgr_tmp_wp_posts` ENABLE KEYS',
            WPMGR_Staging_Sql::ubah( 'ALTER TABLE `wp_posts` ENABLE KEYS', 'wp_' ) );
    }

    public function test_ubah_melewati_yang_tidak_perlu(): void {
        foreach ( array( 'SET NAMES utf8mb4', 'set foreign_key_checks=0', '/*!40101 SET NAMES utf8 */',
                         'LOCK TABLES `wp_posts` WRITE', 'UNLOCK TABLES', "  \n-- hanya komentar\n",
                         'INSERT INTO `wp_wpmgr_errors` VALUES (1)', 'DROP TABLE IF EXISTS `wp_wpmgr_traffic`' ) as $s ) {
            $this->assertNull( WPMGR_Staging_Sql::ubah( $s, 'wp_' ), $s );
        }
    }

    public function test_ubah_menolak_pernyataan_lain(): void {
        foreach ( array( 'DELETE FROM `wp_posts`', 'UPDATE `wp_options` SET a=1', "GRANT ALL ON *.* TO 'x'",
                         'CREATE TRIGGER t BEFORE INSERT ON `wp_posts` FOR EACH ROW SET @a=1',
                         'INSERT INTO `lain_posts` VALUES (1)', 'INSERT INTO wp_posts VALUES (1)',
                         'DROP DATABASE wp', 'ALTER TABLE `wp_posts` ADD COLUMN x int',
                         'CREATE TABLE `wp_' . str_repeat( 'a', 52 ) . '` (a int)' ) as $s ) {
            $hasil = WPMGR_Staging_Sql::ubah( $s, 'wp_' );
            $this->assertInstanceOf( WP_Error::class, $hasil, $s );
            $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
        }
    }

    public function test_keluaran_ekspor_tabel_terpecah_benar(): void {
        WPMGR_Staging_Tabel::$baris     = 2000;
        WPMGR_Staging_Tabel::$sub       = 200;
        WPMGR_Staging_Tabel::$maks_byte = 6291456;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'a', 'Type' => 'longtext' ) );
        $w->baris = array( array( 'a' => "x;\n'y'\\" ), array( 'a' => "\0;\x1a" ) );
        $sql      = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' )['sql'];
        list( $hasil, $sisa ) = $this->pecah_sekaligus( $sql );
        $this->assertCount( 3, $hasil );
        $this->assertSame( '', trim( $sisa ) );
        $this->assertStringStartsWith( 'INSERT INTO `wpmgr_tmp_wp_x`', WPMGR_Staging_Sql::ubah( $hasil[2][0], 'wp_' ) );
    }
}
