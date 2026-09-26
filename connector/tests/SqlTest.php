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

    // ---- MINOR (review putaran 1): pemecah tidak boleh menganggap --, #,
    // /* di DALAM literal string sebagai awal komentar, dan ';' di dalam
    // literal string tidak pernah dihitung sebagai akhir pernyataan. ----

    public function test_pemecah_tidak_menganggap_komentar_di_dalam_literal(): void {
        $sql = "INSERT INTO `t` (`a`) VALUES ('-- x; bukan komentar'),\n"
            . "('# y; bukan komentar'),\n"
            . "('/* z; bukan komentar */');";
        list( $hasil, $sisa ) = $this->pecah_sekaligus( $sql );
        $this->assertSame( '', $sisa );
        $this->assertCount( 1, $hasil );
        $this->assertSame( rtrim( $sql, ';' ), trim( $hasil[0][0] ) );
    }

    // ---- Fix I1 (review putaran 1): draf awal hanya memvalidasi kepala
    // setiap pernyataan -- sekarang pernyataan UTUH divalidasi per jenis. ----

    public function test_ubah_menolak_drop_table_lebih_dari_satu(): void {
        // Draf awal hanya memvalidasi kepala pernyataan: ini akan lolos
        // sebagai "DROP TABLE IF EXISTS `wpmgr_tmp_wp_a`, `wp_users`" --
        // tabel KEDUA (produksi, tidak diganti nama) ikut ter-DROP mentah
        // saat Task 8 menjalankannya. Harus ditolak SELURUHNYA.
        foreach ( array( 'DROP TABLE `wp_a`, `wp_users`', 'DROP TABLE IF EXISTS `wp_a`, `wp_users`' ) as $s ) {
            $hasil = WPMGR_Staging_Sql::ubah( $s, 'wp_' );
            $this->assertInstanceOf( WP_Error::class, $hasil, $s );
            $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
        }
    }

    public function test_ubah_menolak_insert_select(): void {
        foreach ( array(
            'INSERT INTO `wp_posts` SELECT * FROM `wp_x`',
            'INSERT INTO `wp_posts` (`a`) SELECT `a` FROM `wp_x`',
        ) as $s ) {
            $hasil = WPMGR_Staging_Sql::ubah( $s, 'wp_' );
            $this->assertInstanceOf( WP_Error::class, $hasil, $s );
            $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
        }
    }

    public function test_ubah_menolak_insert_on_duplicate_key_update(): void {
        $hasil = WPMGR_Staging_Sql::ubah(
            "INSERT INTO `wp_posts` (`a`) VALUES (1) ON DUPLICATE KEY UPDATE `a` = 2", 'wp_' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
    }

    public function test_ubah_menolak_insert_set(): void {
        $hasil = WPMGR_Staging_Sql::ubah( "INSERT INTO `wp_posts` SET `a` = 1", 'wp_' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
    }

    public function test_ubah_menolak_create_table_select(): void {
        foreach ( array(
            "CREATE TABLE `wp_posts` (`a` int) AS SELECT * FROM `wp_x`",
            "CREATE TABLE `wp_posts` SELECT * FROM `wp_x`",
        ) as $s ) {
            $hasil = WPMGR_Staging_Sql::ubah( $s, 'wp_' );
            $this->assertInstanceOf( WP_Error::class, $hasil, $s );
            $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
        }
    }

    public function test_ubah_menolak_engine_dan_direktori_berbahaya(): void {
        foreach ( array(
            "CREATE TABLE `wp_posts` (`a` int) ENGINE=FEDERATED",
            "CREATE TABLE `wp_posts` (`a` int) ENGINE=CONNECT",
            "CREATE TABLE `wp_posts` (`a` int) DATA DIRECTORY='/tmp'",
            "CREATE TABLE `wp_posts` (`a` int) INDEX DIRECTORY='/tmp'",
        ) as $s ) {
            $hasil = WPMGR_Staging_Sql::ubah( $s, 'wp_' );
            $this->assertInstanceOf( WP_Error::class, $hasil, $s );
            $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
        }
    }

    // ---- Tokenizer-level: kata kunci/nama tabel di DALAM literal string
    // tidak boleh menipu (menolak yang sah) atau meloloskan (yang berbahaya). ----

    public function test_ubah_tidak_tertipu_kata_kunci_di_dalam_literal(): void {
        // "SELECT"/"ENGINE=FEDERATED" di sini murni ISI kolom DEFAULT
        // (string), bukan struktur SQL sungguhan -- pernyataan yang SAH ini
        // tidak boleh ditolak hanya karena topeng literal gagal.
        $hasil = WPMGR_Staging_Sql::ubah(
            "CREATE TABLE `wp_a` (`b` varchar(50) DEFAULT 'SELECT * ENGINE=FEDERATED CREATE TABLE fake')", 'wp_' );
        $this->assertSame(
            "CREATE TABLE `wpmgr_tmp_wp_a` (`b` varchar(50) DEFAULT 'SELECT * ENGINE=FEDERATED CREATE TABLE fake')",
            $hasil );
    }

    public function test_ubah_case_insensitive_untuk_tabel_wpmgr(): void {
        // Server lower_case_table_names bisa mengembalikan huruf apa pun.
        $this->assertNull( WPMGR_Staging_Sql::ubah( 'INSERT INTO `wp_WPMGR_errors` VALUES (1)', 'wp_' ) );
        $this->assertNull( WPMGR_Staging_Sql::ubah( 'DROP TABLE IF EXISTS `wp_WPMGR_traffic`', 'wp_' ) );
    }

    // ---- Fix I2 (review putaran 1): prefix asing yang tumpang tindih
    // ('wp_' vs 'wp_abc_') ditolak bila pemanggil menyertakannya. ----

    public function test_ubah_menolak_prefix_asing_yang_tumpang_tindih(): void {
        $hasil = WPMGR_Staging_Sql::ubah(
            'DROP TABLE IF EXISTS `wp_abc_posts`', 'wp_', 'wpmgr_tmp_', array( 'wp_abc_' ) );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
        // Tanpa daftar prefix asing (perilaku lama, tanpa akses DB): tetap diterima.
        $this->assertSame( 'DROP TABLE IF EXISTS `wpmgr_tmp_wp_abc_posts`',
            WPMGR_Staging_Sql::ubah( 'DROP TABLE IF EXISTS `wp_abc_posts`', 'wp_' ) );
    }

    // ---- Fix N1 (review putaran 2, KRITIS): komentar bertanda versi
    // MySQL/MariaDB (/*! ... */, /*M! ... */) DIEKSEKUSI sebagai SQL
    // sungguhan, bukan dibuang -- topeng tanpa_literal() (fix I1)
    // menyembunyikannya dari validator tapi teks yang DIKEMBALIKAN tetap
    // memuat isinya utuh. Probe reviewer persis, harus ditolak SELURUHNYA. ----

    public function test_ubah_menolak_komentar_bertanda_yang_menyembunyikan_perintah(): void {
        foreach ( array(
            'DROP TABLE IF EXISTS `wp_a` /*!, `wp_users` */',
            "INSERT INTO `wp_posts` (`a`) VALUES (1) /*!50000 ON DUPLICATE KEY UPDATE `a` = 2 */",
            "CREATE TABLE `wp_posts` (`a` int) /*!50000 ENGINE=FEDERATED */",
            'ALTER TABLE `wp_posts` ENABLE KEYS /*!, RENAME TO `wp_x` */',
        ) as $s ) {
            $hasil = WPMGR_Staging_Sql::ubah( $s, 'wp_' );
            $this->assertInstanceOf( WP_Error::class, $hasil, $s );
            $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code(), $s );
        }
    }

    public function test_ubah_menolak_komentar_apa_pun_di_luar_string(): void {
        foreach ( array(
            "DROP TABLE IF EXISTS `wp_posts` -- komentar\n",
            'DROP TABLE IF EXISTS `wp_posts` # komentar',
            'DROP TABLE /* komentar */ IF EXISTS `wp_posts`',
        ) as $s ) {
            $hasil = WPMGR_Staging_Sql::ubah( $s, 'wp_' );
            $this->assertInstanceOf( WP_Error::class, $hasil, $s );
            $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code(), $s );
        }
    }

    public function test_ubah_menerima_komentar_di_dalam_literal_string(): void {
        // '/* bukan komentar */' di sini adalah ISI STRING (literal),
        // bukan komentar sungguhan -- ada_komentar() harus tetap menerima
        // pernyataan ini.
        $hasil = WPMGR_Staging_Sql::ubah(
            "INSERT INTO `wp_posts` (`a`) VALUES ('/* bukan komentar */ -- juga bukan # juga')", 'wp_' );
        $this->assertSame(
            "INSERT INTO `wpmgr_tmp_wp_posts` (`a`) VALUES ('/* bukan komentar */ -- juga bukan # juga')", $hasil );
    }

    // ---- Fix N1: ENGINE diperiksa lewat allowlist, mencakup nilai
    // berkutip/backtick yang sebelumnya tersembunyi dari denylist oleh
    // topeng literal. ----

    public function test_ubah_menolak_engine_berkutip_atau_backtick(): void {
        foreach ( array(
            "CREATE TABLE `wp_posts` (`a` int) ENGINE='FEDERATED'",
            'CREATE TABLE `wp_posts` (`a` int) ENGINE="FEDERATED"',
            'CREATE TABLE `wp_posts` (`a` int) ENGINE=`FEDERATED`',
        ) as $s ) {
            $hasil = WPMGR_Staging_Sql::ubah( $s, 'wp_' );
            $this->assertInstanceOf( WP_Error::class, $hasil, $s );
            $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code(), $s );
        }
    }

    public function test_ubah_menolak_engine_merge_dengan_union(): void {
        $hasil = WPMGR_Staging_Sql::ubah(
            'CREATE TABLE `wp_posts` (`a` int) ENGINE=MERGE UNION=(`wp_x`,`wp_y`) INSERT_METHOD=LAST', 'wp_' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
    }

    public function test_ubah_menolak_connection(): void {
        $hasil = WPMGR_Staging_Sql::ubah(
            "CREATE TABLE `wp_posts` (`a` int) ENGINE=InnoDB CONNECTION='mysql://x/y'", 'wp_' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
    }

    public function test_ubah_menerima_engine_yang_diizinkan(): void {
        foreach ( array( 'InnoDB', 'MyISAM', 'Aria', 'innodb', 'MYISAM' ) as $mesin ) {
            $hasil = WPMGR_Staging_Sql::ubah( "CREATE TABLE `wp_posts` (`a` int) ENGINE={$mesin}", 'wp_' );
            $this->assertSame( "CREATE TABLE `wpmgr_tmp_wp_posts` (`a` int) ENGINE={$mesin}", $hasil, $mesin );
        }
    }

    // ---- Fix N1: isi VALUES divalidasi token demi token -- subquery dan
    // pemanggilan fungsi ditolak, bukan diterima apa pun setelah VALUES. ----

    public function test_ubah_menolak_subquery_di_dalam_values(): void {
        $hasil = WPMGR_Staging_Sql::ubah( 'INSERT INTO `wp_posts` (`a`) VALUES ((SELECT `a` FROM `wp_x`))', 'wp_' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
    }

    public function test_ubah_menolak_pemanggilan_fungsi_di_dalam_values(): void {
        foreach ( array(
            "INSERT INTO `wp_posts` (`a`) VALUES (LOAD_FILE('/etc/passwd'))",
            "INSERT INTO `wp_posts` (`a`) VALUES (CONCAT('a','b'))",
        ) as $s ) {
            $hasil = WPMGR_Staging_Sql::ubah( $s, 'wp_' );
            $this->assertInstanceOf( WP_Error::class, $hasil, $s );
            $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code(), $s );
        }
    }

    public function test_ubah_menerima_semua_bentuk_literal_values_yang_sah(): void {
        $hasil = WPMGR_Staging_Sql::ubah(
            "INSERT INTO `wp_posts` (`a`,`b`,`c`,`d`) VALUES (1,'x',NULL,0x3b27),(-2.5,'y',NULL,0x00)", 'wp_' );
        $this->assertSame(
            "INSERT INTO `wpmgr_tmp_wp_posts` (`a`,`b`,`c`,`d`) VALUES (1,'x',NULL,0x3b27),(-2.5,'y',NULL,0x00)", $hasil );
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
