<?php
use PHPUnit\Framework\TestCase;

final class TerapkanTest extends TestCase {

    const ID    = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const ID2   = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
    const TOKEN = '0123456789abcdef0123456789abcdef';

    private $akar;
    private $db;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-trp-' . bin2hex( random_bytes( 6 ) ) . '/';
        foreach ( array(
            'index.php'                      => '<?php // inti',
            'wp-content/themes/t/style.css'  => 'lama',
            'wp-content/themes/t/hapus.php'  => 'dihapus',
            'wp-content/plugins/p/p.php'     => 'p lama',
        ) as $rel => $isi ) {
            if ( ! is_dir( dirname( $this->akar . $rel ) ) ) {
                mkdir( dirname( $this->akar . $rel ), 0777, true );
            }
            file_put_contents( $this->akar . $rel, $isi );
        }
        mkdir( $this->akar . 'wp-content/mu-plugins', 0777, true );
        $this->db        = new WPMGR_FakeDbDorong();
        $this->db->tabel = array( 'wp_posts', 'wp_options' );
    }

    protected function tearDown(): void {
        StagingDasarTest::hapus( rtrim( $this->akar, '/' ) );
        $GLOBALS['wpmgr_test_opsi'] = array();
        $GLOBALS['wpmgr_test_rute'] = array();
    }

    private function dorong( $detik = 20 ) {
        return new WPMGR_Staging_Dorong( $this->akar, $this->akar . 'wp-content/wpmgr-dorong/', $this->db,
            $detik, $this->akar . 'wp-content/mu-plugins/' );
    }

    private function dir() {
        return $this->akar . 'wp-content/wpmgr-dorong/' . self::ID . '/';
    }

    private function paket( $nomor, $jenis, array $berkas, array $isi ) {
        return WPMGR_Staging_Paket::susun(
            array( 'dorong_id' => self::ID, 'nomor' => $nomor, 'jenis' => $jenis, 'berkas' => $berkas ), $isi );
    }

    /** Unggah seperti dashboard; berkas besar dikirim dalam dua rentang, SQL dan rencana dalam dua potongan. */
    private function unggah( array $baru, array $hapus, $sql ) {
        $d       = $this->dorong();
        $nomor   = 0;
        $rencana = array( 'versi' => 1, 'berkas' => array(), 'hapus' => $hapus, 'sql' => null !== $sql, 'charset' => 'utf8mb4' );
        foreach ( $baru as $rel => $isi ) {
            $rencana['berkas'][] = array( 'path' => $rel, 'ukuran' => strlen( $isi ), 'sha256' => hash( 'sha256', $isi ), 'mtime' => 1700000000 );
            if ( strlen( $isi ) > 8 ) {
                $separuh = intdiv( strlen( $isi ), 2 );
                $d->unggah( $this->paket( $nomor++, 'rentang', array( array( 'path' => $rel, 'dari' => 0 ) ), array( substr( $isi, 0, $separuh ) ) ) );
                $d->unggah( $this->paket( $nomor++, 'rentang', array( array( 'path' => $rel, 'dari' => $separuh ) ), array( substr( $isi, $separuh ) ) ) );
            } else {
                $d->unggah( $this->paket( $nomor++, 'berkas', array( array( 'path' => $rel, 'mtime' => 1700000000 ) ), array( $isi ) ) );
            }
        }
        if ( null !== $sql ) {
            $separuh = intdiv( strlen( $sql ), 2 );
            $d->unggah( $this->paket( $nomor++, 'sql', array( array( 'path' => 'sql' ) ), array( substr( $sql, 0, $separuh ) ) ) );
            $d->unggah( $this->paket( $nomor++, 'sql', array( array( 'path' => 'sql' ) ), array( substr( $sql, $separuh ) ) ) );
        }
        $json = json_encode( $rencana );
        $d->unggah( $this->paket( $nomor++, 'rencana', array( array( 'path' => 'rencana' ) ), array( substr( $json, 0, 10 ) ) ) );
        $d->unggah( $this->paket( $nomor++, 'rencana', array( array( 'path' => 'rencana' ) ), array( substr( $json, 10 ) ) ) );
        return array( $nomor, hash( 'sha256', $json ) );
    }

    private function langkah( $langkah, array $tambahan = array(), $detik = 20 ) {
        return $this->dorong( $detik )->terapkan( array_merge( array( 'dorong_id' => self::ID, 'langkah' => $langkah ), $tambahan ) );
    }

    private function sampai_selesai( $langkah, array $tambahan = array(), $detik = 20 ) {
        for ( $i = 0; $i < 200; $i++ ) {
            $h = $this->langkah( $langkah, $tambahan, $detik );
            if ( is_wp_error( $h ) || $h['selesai'] ) {
                return $h;
            }
        }
        $this->fail( 'Langkah ' . $langkah . ' tidak pernah selesai.' );
    }

    /**
     * Penyimpangan dari brief: CREATE TABLE memakai ENGINE=InnoDB. Validator
     * Task 7 (WPMGR_Staging_Sql::ubah(), Ruling R8) mewajibkan ENGINE tepat
     * satu kali; CREATE tanpa ENGINE ditolak sebelum dieksekusi.
     */
    private function sql_contoh() {
        return "SET NAMES utf8mb4;\nDROP TABLE IF EXISTS `wp_posts`;\nCREATE TABLE `wp_posts` (`ID` int) ENGINE=InnoDB;\n"
            . "INSERT INTO `wp_posts` (`ID`) VALUES (1),(2);\nINSERT INTO `wp_posts` (`ID`) VALUES (3);\n"
            . "DROP TABLE IF EXISTS `wp_options`;\nCREATE TABLE `wp_options` (`option_name` varchar(191)) ENGINE=InnoDB;\n"
            . "INSERT INTO `wp_wpmgr_errors` VALUES (1);\n";
    }

    private function siap_dengan_sql( array $baru = array( 'wp-content/themes/t/style.css' => 'baru' ), $sql = null ) {
        list( $n, $sha ) = $this->unggah( $baru, array(), null === $sql ? $this->sql_contoh() : $sql );
        $h = $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->assertSame( 'siap', $h['status'] );
    }

    // ------------------------------------------------------------------
    // Test dari brief (SQL contoh disesuaikan dengan validator Task 7).
    // ------------------------------------------------------------------

    public function test_alur_lengkap_berkas_dan_database(): void {
        $baru = array(
            'wp-content/themes/t/style.css' => 'baru-besar-sekali',
            'wp-content/themes/t/baru.php'  => '<?php //',
            'wp-content/mu-plugins/m.php'   => '<?php //m',
        );
        list( $n, $sha ) = $this->unggah( $baru, array( 'wp-content/themes/t/hapus.php' ), $this->sql_contoh() );

        $h = $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->assertSame( 'siap', $h['status'] );
        $dir = $this->dir();
        $this->assertSame( 'baru-besar-sekali', file_get_contents( $dir . 'baru/wp-content/themes/t/style.css' ) );
        $this->assertFileDoesNotExist( $dir . 'baru/wp-content/themes/t/style.css.wpmgr-bagian' );

        $this->assertSame( 'terimpor', $this->sampai_selesai( 'impor' )['status'] );
        $semua = implode( "\n", $this->db->kueri );
        $this->assertStringContainsString( 'CREATE TABLE `wpmgr_tmp_wp_posts`', $semua );
        $this->assertStringContainsString( 'INSERT INTO `wpmgr_tmp_wp_posts` (`ID`) VALUES (3)', $semua );
        $this->assertStringNotContainsString( 'wp_wpmgr_errors', $semua );
        $this->assertStringContainsString( 'UPDATE `wpmgr_tmp_wp_options` t JOIN `wp_options` o', $semua );
        $this->assertStringContainsString( "SET SESSION sql_mode = 'NO_AUTO_VALUE_ON_ZERO'", $semua );

        // Satu operasi per request: pengaman terlihat di tengah jalan.
        $tengah = $this->langkah( 'tukar', array( 'token' => self::TOKEN ), 0 );
        $this->assertFalse( $tengah['selesai'] );
        $m = file_get_contents( $this->akar . '.maintenance' );
        $this->assertStringContainsString( '$upgrading = ' . ( $this->dorong()->keadaan( self::ID )['maintenance_dibuat'] + 300 ) . ';', $m );
        $this->assertStringContainsString( hash( 'sha256', self::TOKEN ), $m );
        $this->assertFileExists( $this->akar . 'wp-content/mu-plugins/wpmgr-dorong-aman.php' );

        $h = $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'ditukar', $h['status'] );
        $this->assertSame( 'baru-besar-sekali', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertSame( '<?php //', file_get_contents( $this->akar . 'wp-content/themes/t/baru.php' ) );
        $this->assertSame( '<?php //m', file_get_contents( $this->akar . 'wp-content/mu-plugins/m.php' ) );
        $this->assertFileDoesNotExist( $this->akar . 'wp-content/themes/t/hapus.php' );
        $this->assertSame( 'p lama', file_get_contents( $this->akar . 'wp-content/plugins/p/p.php' ) );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
        $this->assertFileDoesNotExist( $this->akar . 'wp-content/mu-plugins/wpmgr-dorong-aman.php' );
        $this->assertContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        $this->assertContains( 'wp_posts', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_tmp_wp_posts', $this->db->tabel );

        $this->assertSame( 'selesai', $this->langkah( 'selesai' )['status'] );
        $this->assertSame( array( 'lagi' => false ), $this->dorong()->bersihkan( self::ID ) );
        $this->assertNotContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_old_wp_options', $this->db->tabel );
    }

    public function test_impor_berlanjut_tepat_di_batas_pernyataan(): void {
        list( $n, $sha ) = $this->unggah( array(), array(), $this->sql_contoh() );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->assertSame( 'terimpor', $this->sampai_selesai( 'impor', array(), 0 )['status'] );
        $sisip = array_filter( $this->db->kueri, function ( $q ) {
            return 0 === strpos( $q, 'INSERT INTO `wpmgr_tmp_wp_posts`' );
        } );
        $this->assertCount( 2, $sisip );
    }

    public function test_siapkan_menolak_potongan_kurang_dan_rencana_salah(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/a.css' => 'a' ), array(), null );
        $kurang = $this->langkah( 'siapkan', array( 'jumlah_potongan' => $n + 1, 'sha256_rencana' => $sha ) );
        $this->assertSame( 'wpmgr_staging_kurang', $kurang->get_error_code() );
        $salah = $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => str_repeat( '0', 64 ) ) );
        $this->assertSame( 'wpmgr_staging_rencana', $salah->get_error_code() );
    }

    public function test_hash_berkas_tidak_cocok_ditolak_saat_verifikasi(): void {
        $d       = $this->dorong();
        $d->unggah( $this->paket( 0, 'berkas', array( array( 'path' => 'wp-content/themes/t/a.css' ) ), array( 'isi asli' ) ) );
        $json    = json_encode( array( 'versi' => 1, 'berkas' => array( array( 'path' => 'wp-content/themes/t/a.css',
            'ukuran' => 8, 'sha256' => hash( 'sha256', 'isi lain' ), 'mtime' => 1 ) ), 'hapus' => array(), 'sql' => false, 'charset' => 'utf8mb4' ) );
        $d->unggah( $this->paket( 1, 'rencana', array( array( 'path' => 'rencana' ) ), array( $json ) ) );
        $h = $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => 2, 'sha256_rencana' => hash( 'sha256', $json ) ) );
        $this->assertSame( 'wpmgr_staging_verifikasi', $h->get_error_code() );
    }

    public function test_tukar_gagal_di_tengah_dipulihkan_otomatis(): void {
        // Induk tujuan berupa berkas: direktori untuk berkas baru tidak bisa dibuat.
        file_put_contents( $this->akar . 'wp-content/themes/t/penghalang', 'berkas' );
        list( $n, $sha ) = $this->unggah( array(
            'wp-content/themes/t/style.css'        => 'baru',
            'wp-content/themes/t/penghalang/x.css' => 'x',
        ), array( 'wp-content/themes/t/hapus.php' ), null );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $h = $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertInstanceOf( WP_Error::class, $h );
        $this->assertSame( 'wpmgr_staging_tukar', $h->get_error_code() );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertSame( 'dihapus', file_get_contents( $this->akar . 'wp-content/themes/t/hapus.php' ) );
        $this->assertSame( 'berkas', file_get_contents( $this->akar . 'wp-content/themes/t/penghalang' ) );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
        $this->assertFileDoesNotExist( $this->akar . 'wp-content/mu-plugins/wpmgr-dorong-aman.php' );
        $this->assertSame( 'dipulihkan', $this->dorong()->keadaan( self::ID )['status'] );
    }

    public function test_rename_tabel_gagal_memulihkan_berkas(): void {
        $this->siap_dengan_sql();
        $this->sampai_selesai( 'impor' );
        $this->db->gagal_pada = 'RENAME TABLE `wp_posts` TO `wpmgr_old_wp_posts`';
        $h = $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'wpmgr_staging_tukar', $h->get_error_code() );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertContains( 'wp_posts', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_tmp_wp_posts', $this->db->tabel );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
        $this->assertSame( 'dipulihkan', $this->dorong()->keadaan( self::ID )['status'] );
    }

    public function test_token_berbeda_ditolak(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/a.css' => 'a', 'wp-content/themes/t/b.css' => 'b' ), array(), null );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->langkah( 'tukar', array( 'token' => self::TOKEN ), 0 );
        $lain = $this->langkah( 'tukar', array( 'token' => str_repeat( 'f', 32 ) ) );
        $this->assertSame( 403, $lain->get_error_data()['status'] );
        $this->assertSame( 403, $this->langkah( 'pulihkan', array( 'token' => str_repeat( 'f', 32 ) ) )->get_error_data()['status'] );
        // Token yang sama tetap bisa melanjutkan; setelah selesai, ulangan
        // dengan token lain juga ditolak (bukan dibalas sukses).
        $this->assertSame( 'ditukar', $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) )['status'] );
        $this->assertSame( 403, $this->langkah( 'tukar', array( 'token' => str_repeat( 'f', 32 ) ) )->get_error_data()['status'] );
    }

    public function test_pulihkan_setelah_ditukar_mengembalikan_semuanya(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/style.css' => 'baru', 'wp-content/themes/t/baru.php' => 'b' ),
            array( 'wp-content/themes/t/hapus.php' ), $this->sql_contoh() );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->sampai_selesai( 'impor' );
        $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );
        $h = $this->sampai_selesai( 'pulihkan', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'dipulihkan', $h['status'] );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertSame( 'dihapus', file_get_contents( $this->akar . 'wp-content/themes/t/hapus.php' ) );
        $this->assertFileDoesNotExist( $this->akar . 'wp-content/themes/t/baru.php' );
        $this->assertContains( 'wp_posts', $this->db->tabel );
        $this->assertContains( 'wp_options', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_tmp_wp_posts', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_tmp_wp_options', $this->db->tabel );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
        $this->assertFileDoesNotExist( $this->akar . 'wp-content/mu-plugins/wpmgr-dorong-aman.php' );
    }

    public function test_cron_memulihkan_tukar_yang_macet(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/style.css' => 'baru', 'wp-content/themes/t/b.css' => 'b' ), array(), null );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->langkah( 'tukar', array( 'token' => self::TOKEN ), 0 );
        $this->assertSame( 'baru', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $d           = $this->dorong();
        $k           = $d->keadaan( self::ID );
        $k['diubah'] = time() - 1000;
        $d->simpan_keadaan( self::ID, $k );
        $d->cron();
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertSame( 'dipulihkan', $d->keadaan( self::ID )['status'] );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
    }

    public function test_urutan_operasi_mu_plugin_terakhir(): void {
        $ops = $this->dorong()->operasi( array(
            'berkas' => array( array( 'path' => 'wp-content/mu-plugins/a.php' ), array( 'path' => 'wp-content/themes/t/a.css' ) ),
            'hapus'  => array( 'wp-content/mu-plugins/b.php', 'wp-content/plugins/p/x.php' ),
        ) );
        $this->assertSame( array(
            array( 'ganti', 'wp-content/themes/t/a.css' ), array( 'ganti', 'wp-content/mu-plugins/a.php' ),
            array( 'hapus', 'wp-content/plugins/p/x.php' ), array( 'hapus', 'wp-content/mu-plugins/b.php' ),
        ), $ops );
    }

    public function test_isi_maintenance_dan_mu_aman(): void {
        $m = WPMGR_Staging_Dorong::isi_maintenance( str_repeat( 'a', 64 ), 1700000300 );
        $this->assertStringStartsWith( '<?php', $m );
        $this->assertStringContainsString( '$upgrading = 1700000300;', $m );
        $this->assertStringContainsString( "HTTP_X_WPMGR_LEWATI", $m );
        $mu = WPMGR_Staging_Dorong::isi_mu_aman( str_repeat( 'a', 64 ) );
        $this->assertStringContainsString( "'wp-manager-connector/wp-manager-connector.php'", $mu );
        $this->assertStringContainsString( 'pre_option_template', $mu );
        $this->assertStringContainsString( 'pre_option_stylesheet', $mu );
    }

    // ------------------------------------------------------------------
    // Ruling F4: setiap langkah idempoten.
    // ------------------------------------------------------------------

    public function test_setiap_langkah_diulang_membalas_hasil_sukses_yang_sama(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/style.css' => 'baru' ), array(), $this->sql_contoh() );
        $p       = array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha );
        $siapkan = $this->sampai_selesai( 'siapkan', $p );
        $this->assertSame( array( 'selesai' => true, 'status' => 'siap' ), $siapkan );
        $this->assertSame( $siapkan, $this->langkah( 'siapkan', $p ) );

        $impor = $this->sampai_selesai( 'impor' );
        $this->assertSame( array( 'selesai' => true, 'status' => 'terimpor' ), $impor );
        $jumlah_kueri = count( $this->db->kueri );
        $this->assertSame( $impor, $this->langkah( 'impor' ) );
        $this->assertStringNotContainsString( 'wpmgr_tmp_wp_posts', implode( "\n", array_slice( $this->db->kueri, $jumlah_kueri ) ),
            'Ulangan impor yang sudah selesai tidak boleh menjalankan SQL lagi.' );
        // siapkan yang sudah lewat tetap dibalas sukses yang sama.
        $this->assertSame( $siapkan, $this->langkah( 'siapkan', $p ) );

        $tukar = $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertSame( array( 'selesai' => true, 'status' => 'ditukar' ), $tukar );
        $this->assertSame( $tukar, $this->langkah( 'tukar', array( 'token' => self::TOKEN ) ) );
        // Ulangan tidak memulihkan dan tidak menukar ulang apa pun.
        $this->assertSame( 'baru', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        $this->assertSame( $impor, $this->langkah( 'impor' ) );

        $selesai = $this->langkah( 'selesai' );
        $this->assertSame( array( 'selesai' => true, 'status' => 'selesai' ), $selesai );
        $this->assertSame( $selesai, $this->langkah( 'selesai' ) );
        $this->assertSame( $tukar, $this->langkah( 'tukar', array( 'token' => self::TOKEN ) ) );
    }

    public function test_pulihkan_diulang_membalas_hasil_yang_sama(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/style.css' => 'baru', 'wp-content/themes/t/b.css' => 'b' ), array(), null );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->langkah( 'tukar', array( 'token' => self::TOKEN ), 0 );
        $pertama = $this->sampai_selesai( 'pulihkan', array( 'token' => self::TOKEN ) );
        $this->assertSame( array( 'selesai' => true, 'status' => 'dipulihkan' ), $pertama );
        $this->assertSame( $pertama, $this->langkah( 'pulihkan', array( 'token' => self::TOKEN ) ) );
        $this->assertSame( $pertama, $this->langkah( 'pulihkan', array( 'token' => self::TOKEN ) ) );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertFileDoesNotExist( $this->akar . 'wp-content/themes/t/b.css' );
        // Tukar tidak boleh bangkit lagi setelah dipulihkan.
        $this->assertSame( 409, $this->langkah( 'tukar', array( 'token' => self::TOKEN ) )->get_error_data()['status'] );
    }

    public function test_pulihkan_setelah_tukar_sebagian_bertahap(): void {
        list( $n, $sha ) = $this->unggah( array(
            'wp-content/themes/t/style.css' => 'baru',
            'wp-content/themes/t/b.css'     => 'b',
            'wp-content/themes/t/c.css'     => 'c',
        ), array( 'wp-content/themes/t/hapus.php' ), null );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->langkah( 'tukar', array( 'token' => self::TOKEN ), 0 );
        $this->langkah( 'tukar', array( 'token' => self::TOKEN ), 0 );
        $this->assertFileExists( $this->akar . 'wp-content/themes/t/b.css' );
        // Anggaran 0: satu entri jurnal per request, status di tengah jalan
        // 'memulihkan' (bukan status yang boleh dibersihkan/direbut).
        $h = $this->langkah( 'pulihkan', array( 'token' => self::TOKEN ), 0 );
        $this->assertFalse( $h['selesai'] );
        $this->assertSame( 'memulihkan', $h['status'] );
        $this->assertSame( 'wpmgr_staging_sibuk', $this->dorong()->bersihkan( self::ID )->get_error_code() );
        $h = $this->sampai_selesai( 'pulihkan', array( 'token' => self::TOKEN ), 0 );
        $this->assertSame( 'dipulihkan', $h['status'] );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertSame( 'dihapus', file_get_contents( $this->akar . 'wp-content/themes/t/hapus.php' ) );
        $this->assertFileDoesNotExist( $this->akar . 'wp-content/themes/t/b.css' );
        $this->assertFileDoesNotExist( $this->akar . 'wp-content/themes/t/c.css' );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
    }

    public function test_pulihkan_bermasalah_tidak_pernah_menjadi_dipulihkan_dan_bisa_diulang(): void {
        $this->siap_dengan_sql();
        $this->sampai_selesai( 'impor' );
        $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );

        // (a) RENAME balik gagal: galat, status tetap 'memulihkan', salinan
        // produksi lama utuh dan tidak boleh dibersihkan.
        $this->db->gagal_pada = 'RENAME TABLE `wp_posts` TO `wpmgr_tmp_wp_posts`';
        $h = $this->sampai_selesai( 'pulihkan', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'wpmgr_staging_pulihkan', $h->get_error_code() );
        $this->assertSame( 'memulihkan', $this->dorong()->keadaan( self::ID )['status'] );
        $this->assertContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        $this->assertSame( 'wpmgr_staging_sibuk', $this->dorong()->bersihkan( self::ID )->get_error_code() );

        // (b) Keadaan tabel tidak membuktikan penukaran (tabel sementara
        // bernama sama muncul lagi): RENAME dilewati, tetapi wpmgr_old_*
        // yang tercatat masih ada -- tetap bukan 'dipulihkan'.
        $this->db->gagal_pada = null;
        $this->db->tabel[]    = 'wpmgr_tmp_wp_posts';
        $h = $this->sampai_selesai( 'pulihkan', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'wpmgr_staging_pulihkan', $h->get_error_code() );
        $this->assertSame( 'memulihkan', $this->dorong()->keadaan( self::ID )['status'] );
        $this->assertContains( 'wpmgr_old_wp_posts', $this->db->tabel );

        // (c) Penghalang hilang: pengulangan memulihkan semuanya.
        $this->db->tabel = array_values( array_diff( $this->db->tabel, array( 'wpmgr_tmp_wp_posts' ) ) );
        $h = $this->sampai_selesai( 'pulihkan', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'dipulihkan', $h['status'] );
        $this->assertNotContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        $this->assertContains( 'wp_posts', $this->db->tabel );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
    }

    public function test_pulihkan_sebelum_tukar_membatalkan_tanpa_menyentuh_produksi(): void {
        $this->siap_dengan_sql();
        $this->sampai_selesai( 'impor' );
        $this->assertContains( 'wpmgr_tmp_wp_posts', $this->db->tabel );
        $h = $this->langkah( 'pulihkan', array( 'token' => self::TOKEN ) );
        $this->assertSame( array( 'selesai' => true, 'status' => 'dipulihkan' ), $h );
        $this->assertSame( array( 'wp_posts', 'wp_options' ), $this->db->tabel );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
        $this->assertSame( $h, $this->langkah( 'pulihkan', array( 'token' => self::TOKEN ) ) );
    }

    public function test_tukar_diulang_setelah_rename_sukses_tetapi_keadaan_tidak_tersimpan(): void {
        $this->siap_dengan_sql();
        $this->sampai_selesai( 'impor' );
        $this->assertSame( 'ditukar', $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) )['status'] );
        // Proses mati tepat setelah RENAME TABLE, sebelum keadaan disimpan.
        $d                 = $this->dorong();
        $k                 = $d->keadaan( self::ID );
        $k['status']       = 'menukar';
        $k['db_ditukar']   = false;
        unset( $k['hasil']['tukar'] );
        $d->simpan_keadaan( self::ID, $k );
        $h = $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertSame( array( 'selesai' => true, 'status' => 'ditukar' ), $h );
        $this->assertContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        $this->assertSame( 'baru', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
    }

    // ------------------------------------------------------------------
    // Ruling F5: kolom UPDATE ... JOIN selalu berkualifikasi.
    // ------------------------------------------------------------------

    public function test_pertahankan_opsi_sql_dipatok_dan_kolom_berkualifikasi(): void {
        $this->siap_dengan_sql();
        $this->sampai_selesai( 'impor' );
        $opsi = array_values( array_filter( $this->db->kueri, function ( $q ) {
            return false !== strpos( $q, '`wpmgr_tmp_wp_options`' ) && 1 === preg_match( '/^(INSERT IGNORE|UPDATE|DELETE)/', $q );
        } ) );
        $this->assertSame( array(
            "INSERT IGNORE INTO `wpmgr_tmp_wp_options` (`option_name`, `option_value`, `autoload`)"
                . " SELECT o.option_name, o.option_value, o.autoload FROM `wp_options` o"
                . " WHERE (o.option_name IN ('siteurl', 'home', 'blog_public') OR o.option_name LIKE 'wpmgr\\_%')",
            "UPDATE `wpmgr_tmp_wp_options` t JOIN `wp_options` o ON o.option_name = t.option_name"
                . " SET t.option_value = o.option_value, t.autoload = o.autoload"
                . " WHERE (t.option_name IN ('siteurl', 'home', 'blog_public') OR t.option_name LIKE 'wpmgr\\_%')",
            "DELETE t FROM `wpmgr_tmp_wp_options` t LEFT JOIN `wp_options` o ON o.option_name = t.option_name"
                . " WHERE t.option_name LIKE 'wpmgr\\_%' AND o.option_name IS NULL",
        ), $opsi );
        foreach ( array( $opsi[1], $opsi[2] ) as $q ) {
            $this->assertSame( 0, preg_match( '/(?<![.`\w])(option_name|option_value|autoload)\b/', $q ), $q );
        }
    }

    // ------------------------------------------------------------------
    // Kontrol kompensasi R10: ENGINE/CREATE_OPTIONS dibaca ulang.
    // ------------------------------------------------------------------

    public function test_impor_menolak_engine_di_luar_daftar(): void {
        $this->siap_dengan_sql();
        $this->db->mesin['wpmgr_tmp_wp_posts'] = array( 'ENGINE' => 'FEDERATED', 'CREATE_OPTIONS' => '' );
        $h = $this->sampai_selesai( 'impor' );
        $this->assertInstanceOf( WP_Error::class, $h );
        $this->assertSame( 'wpmgr_staging_impor', $h->get_error_code() );
        $this->assertSame( 'Tabel hasil impor memakai mesin atau opsi yang tidak diizinkan.', $h->get_error_message() );
        $this->assertNotContains( 'wpmgr_tmp_wp_posts', $this->db->tabel );
        $this->assertContains( 'DROP TABLE IF EXISTS `wpmgr_tmp_wp_posts`', array_slice( $this->db->kueri, -3 ) );
        $this->assertNotContains( 'wpmgr_tmp_wp_posts', $this->dorong()->keadaan( self::ID )['tabel_tmp'] );
        // Tidak ada data yang dimuat ke tabel itu, dan tukar tidak bisa dimulai.
        $this->assertEmpty( preg_grep( '/^INSERT INTO `wpmgr_tmp_wp_posts`/', $this->db->kueri ) );
        $this->assertSame( 409, $this->langkah( 'tukar', array( 'token' => self::TOKEN ) )->get_error_data()['status'] );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
    }

    public function test_impor_menolak_create_options_berbahaya(): void {
        foreach ( array( 'partitioned', 'UNION=(`wp_users`)', "connection='mysql://x'" ) as $i => $opsi ) {
            if ( $i > 0 ) {
                $this->tearDown();
                $this->setUp();
            }
            $this->siap_dengan_sql();
            $this->db->mesin['wpmgr_tmp_wp_options'] = array( 'ENGINE' => 'InnoDB', 'CREATE_OPTIONS' => $opsi );
            $h = $this->sampai_selesai( 'impor' );
            $this->assertInstanceOf( WP_Error::class, $h, $opsi );
            $this->assertSame( 'wpmgr_staging_impor', $h->get_error_code() );
            $this->assertNotContains( 'wpmgr_tmp_wp_options', $this->db->tabel );
            $this->assertContains( 'wpmgr_tmp_wp_posts', $this->db->tabel );
        }
    }

    public function test_impor_menerima_mesin_dan_opsi_biasa(): void {
        $this->siap_dengan_sql();
        $this->db->mesin['wpmgr_tmp_wp_posts']   = array( 'ENGINE' => 'aria', 'CREATE_OPTIONS' => 'row_format=DYNAMIC' );
        $this->db->mesin['wpmgr_tmp_wp_options'] = array( 'ENGINE' => 'MyISAM', 'CREATE_OPTIONS' => '' );
        $this->assertSame( 'terimpor', $this->sampai_selesai( 'impor' )['status'] );
        $cek = preg_grep( '/information_schema\.TABLES/', $this->db->kueri );
        $this->assertCount( 2, $cek );
        $this->assertStringContainsString( "TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'wpmgr_tmp_wp_posts'", implode( "\n", $cek ) );
    }

    // ------------------------------------------------------------------
    // Gerbang SQL: sql_mode, galat per pernyataan, prefix asing, jurnal.
    // ------------------------------------------------------------------

    public function test_sql_mode_diset_sebelum_pernyataan_pertama_dieksekusi(): void {
        $this->siap_dengan_sql();
        $this->db->kueri = array();
        $this->sampai_selesai( 'impor', array(), 0 );
        $mode    = array_keys( preg_grep( "/^SET SESSION sql_mode = /", $this->db->kueri ) );
        $pertama = array_keys( preg_grep( '/^(DROP|CREATE|INSERT)[^`]*`wpmgr_tmp_/', $this->db->kueri ) );
        $this->assertNotEmpty( $mode );
        $this->assertLessThan( $pertama[0], $mode[0] );
        foreach ( preg_grep( '/sql_mode/', $this->db->kueri ) as $q ) {
            $this->assertStringNotContainsString( 'NO_BACKSLASH_ESCAPES', $q );
        }
    }

    public function test_pernyataan_gagal_menggagalkan_impor_dengan_pesan_tetap(): void {
        $this->siap_dengan_sql();
        $this->db->gagal_pada = 'INSERT INTO `wpmgr_tmp_wp_posts` (`ID`) VALUES (3)';
        $h = $this->sampai_selesai( 'impor' );
        $this->assertSame( 'wpmgr_staging_impor', $h->get_error_code() );
        $this->assertStringNotContainsString( 'galat tiruan', $h->get_error_message() );
        $this->assertSame( 'mengimpor', $this->dorong()->keadaan( self::ID )['status'] );
        $this->assertSame( 409, $this->langkah( 'tukar', array( 'token' => self::TOKEN ) )->get_error_data()['status'] );
    }

    public function test_impor_meneruskan_prefix_asing_ke_gerbang_sql(): void {
        $this->db->tabel = array( 'wp_posts', 'wp_options', 'wp_abc_options', 'wp_abc_posts' );
        $this->siap_dengan_sql( array(), "DROP TABLE IF EXISTS `wp_abc_posts`;\nCREATE TABLE `wp_abc_posts` (`ID` int) ENGINE=InnoDB;\n" );
        $h = $this->sampai_selesai( 'impor' );
        $this->assertSame( 'wpmgr_staging_sql', $h->get_error_code() );
        $this->assertEmpty( preg_grep( '/wpmgr_tmp_wp_abc_posts/', $this->db->kueri ) );
    }

    public function test_tukar_hanya_menukar_tabel_di_jurnal(): void {
        $this->siap_dengan_sql();
        $this->sampai_selesai( 'impor' );
        // Tabel sementara yang tidak dibuat dorongan ini (sisa pihak lain).
        $this->db->tabel[] = 'wp_users';
        $this->db->tabel[] = 'wpmgr_tmp_wp_users';
        $this->assertSame( 'ditukar', $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) )['status'] );
        $rename = implode( "\n", preg_grep( '/^RENAME TABLE/', $this->db->kueri ) );
        $this->assertStringNotContainsString( 'wp_users', $rename );
        $this->assertContains( 'wpmgr_tmp_wp_users', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_old_wp_users', $this->db->tabel );
        $k = $this->dorong()->keadaan( self::ID );
        $this->assertSame( array( 'wpmgr_old_wp_posts', 'wpmgr_old_wp_options' ), $k['tabel_old'] );
    }

    public function test_tukar_menolak_bila_tabel_lama_dorongan_sebelumnya_masih_ada(): void {
        $this->siap_dengan_sql();
        $this->sampai_selesai( 'impor' );
        $this->db->tabel[] = 'wpmgr_old_wp_posts';
        $h = $this->langkah( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertSame( 409, $h->get_error_data()['status'] );
        $this->assertSame( 'terimpor', $this->dorong()->keadaan( self::ID )['status'] );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
    }

    // ------------------------------------------------------------------
    // Kunci dorong dan status.
    // ------------------------------------------------------------------

    public function test_langkah_ditolak_bila_kunci_dipegang_dorongan_lain(): void {
        $this->siap_dengan_sql( array( 'wp-content/themes/t/style.css' => 'baru' ), null );
        $this->sampai_selesai( 'impor' );
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID2 . '|' . time();
        $h = $this->langkah( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'wpmgr_staging_sibuk', $h->get_error_code() );
        $this->assertSame( 'terimpor', $this->dorong()->keadaan( self::ID )['status'] );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
    }

    public function test_langkah_menyegarkan_kunci(): void {
        $this->siap_dengan_sql();
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 10000 );
        $this->langkah( 'impor', array(), 0 );
        $bagian = explode( '|', $this->db->opsi['wpmgr_dorong_kunci'] );
        $this->assertSame( self::ID, $bagian[0] );
        $this->assertGreaterThanOrEqual( time() - 5, (int) $bagian[1] );
    }

    public function test_dorongan_direbut_ditolak_permanen(): void {
        $this->siap_dengan_sql();
        $d           = $this->dorong();
        $k           = $d->keadaan( self::ID );
        $k['status'] = 'direbut';
        $d->simpan_keadaan( self::ID, $k );
        foreach ( array( 'siapkan', 'impor', 'tukar', 'pulihkan', 'selesai' ) as $langkah ) {
            $h = $this->langkah( $langkah, array( 'token' => self::TOKEN ) );
            $this->assertSame( 'wpmgr_staging_direbut', $h->get_error_code(), $langkah );
            $this->assertSame( 409, $h->get_error_data()['status'] );
        }
    }

    public function test_status_tukar_dan_pulihkan_di_luar_semua_himpunan_aman(): void {
        foreach ( WPMGR_Staging_Dorong::STATUS_MENYENTUH_PRODUKSI as $status ) {
            $this->assertNotContains( $status, WPMGR_Staging_Dorong::STATUS_BOLEH_BERSIHKAN, $status );
            $this->assertNotContains( $status, WPMGR_Staging_Dorong::STATUS_PRA_TUKAR, $status );
            $this->assertNotContains( $status, WPMGR_Staging_Dorong::STATUS_AMAN_TERMINAL, $status );
        }
        $this->assertSame( array( 'menukar', 'ditukar', 'memulihkan' ), WPMGR_Staging_Dorong::STATUS_MENYENTUH_PRODUKSI );
        foreach ( array( 'menyiapkan', 'mengimpor', 'terimpor' ) as $status ) {
            $this->assertContains( $status, WPMGR_Staging_Dorong::STATUS_PRA_TUKAR );
            $this->assertContains( $status, WPMGR_Staging_Dorong::STATUS_BOLEH_BERSIHKAN );
        }
    }

    public function test_bersihkan_ditolak_saat_ditukar_salinan_pemulihan_utuh(): void {
        $this->siap_dengan_sql();
        $this->sampai_selesai( 'impor' );
        $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'wpmgr_staging_sibuk', $this->dorong()->bersihkan( self::ID )->get_error_code() );
        $this->assertSame( 'lama', file_get_contents( $this->dir() . 'lama/wp-content/themes/t/style.css' ) );
        $this->assertContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        // Perebutan kunci basi juga ditolak saat ditukar.
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 10800 );
        $this->assertSame( 'wpmgr_staging_perlu_pemulihan', $this->dorong()->kunci( self::ID2 )->get_error_code() );
    }

    public function test_langkah_bersamaan_ditolak(): void {
        $this->siap_dengan_sql();
        $h = fopen( $this->dir() . 'terapkan.lock', 'c' );
        $this->assertTrue( flock( $h, LOCK_EX | LOCK_NB ) );
        $galat = $this->langkah( 'impor' );
        flock( $h, LOCK_UN );
        fclose( $h );
        $this->assertSame( 'wpmgr_staging_sibuk', $galat->get_error_code() );
        $this->assertSame( 'siap', $this->dorong()->keadaan( self::ID )['status'] );
    }

    // ------------------------------------------------------------------
    // Koreksi #10/#14: pengaman dan yang tidak pernah disentuh.
    // ------------------------------------------------------------------

    public function test_maintenance_pihak_lain_menolak_tukar(): void {
        file_put_contents( $this->akar . '.maintenance', '<?php $upgrading = 1;' );
        $this->siap_dengan_sql( array( 'wp-content/themes/t/style.css' => 'baru' ), null );
        $this->sampai_selesai( 'impor' );
        $h = $this->langkah( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'wpmgr_staging_maintenance', $h->get_error_code() );
        $this->assertSame( 409, $h->get_error_data()['status'] );
        $this->assertSame( '<?php $upgrading = 1;', file_get_contents( $this->akar . '.maintenance' ) );
        $this->assertSame( 'terimpor', $this->dorong()->keadaan( self::ID )['status'] );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
    }

    public function test_hapus_di_uploads_tidak_pernah_dijalankan(): void {
        $ops = $this->dorong()->operasi( array(
            'berkas' => array( array( 'path' => 'wp-content/uploads/2024/a.jpg' ) ),
            'hapus'  => array( 'wp-content/uploads/2024/pesanan.pdf', 'wp-content/themes/t/x.php', 'WP-CONTENT/Uploads/b.jpg' ),
        ) );
        $this->assertSame( array(
            array( 'ganti', 'wp-content/uploads/2024/a.jpg' ), array( 'hapus', 'wp-content/themes/t/x.php' ),
        ), $ops );
    }

    public function test_rencana_path_ganda_ditolak(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/a.css' => 'a' ), array( 'wp-content/themes/t/a.css' ), null );
        $h = $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->assertSame( 'wpmgr_staging_rencana', $h->get_error_code() );
    }

    public function test_pengecualian_menjadi_wp_error_pesan_tetap(): void {
        $this->siap_dengan_sql();
        $this->db->lempar_pada = 'wpmgr_dorong_kunci';
        $h = $this->langkah( 'impor' );
        $this->assertInstanceOf( WP_Error::class, $h );
        $this->assertSame( 'wpmgr_staging_galat', $h->get_error_code() );
        $this->assertSame( 500, $h->get_error_data()['status'] );
        $this->assertStringNotContainsString( 'tiruan', $h->get_error_message() );
    }

    public function test_permintaan_tidak_sah(): void {
        $d = $this->dorong();
        $this->assertSame( 400, $d->terapkan( null )->get_error_data()['status'] );
        $this->assertSame( 400, $d->terapkan( array( 'dorong_id' => 'x', 'langkah' => 'impor' ) )->get_error_data()['status'] );
        $this->assertSame( 404, $d->terapkan( array( 'dorong_id' => self::ID, 'langkah' => 'impor' ) )->get_error_data()['status'] );
        $this->siap_dengan_sql();
        $this->assertSame( 400, $this->langkah( 'hapus_semua' )->get_error_data()['status'] );
        $this->assertSame( 400, $this->langkah( 'tukar', array( 'token' => 'bukan-hex' ) )->get_error_data()['status'] );
    }

    // ---- Setiap route baru punya test akses anonim (401). ----

    public function test_staging_terapkan_terdaftar_dengan_guard_dan_menolak_tanpa_tanda_tangan(): void {
        $GLOBALS['wpmgr_test_rute'] = array();
        WPMGR_Staging::daftarkan_route();
        $kunci = 'wpmgr/v1/staging/terapkan';
        $this->assertArrayHasKey( $kunci, $GLOBALS['wpmgr_test_rute'] );
        $r = $GLOBALS['wpmgr_test_rute'][ $kunci ];
        $this->assertSame( 'POST', $r['methods'] );
        $this->assertSame( array( 'WPMGR_Staging', 'terapkan' ), $r['callback'] );
        $this->assertSame( array( 'WPMGR_Staging', 'guard' ), $r['permission_callback'] );
        $GLOBALS['wpmgr_test_opsi']['wpmgr_site_id'] = 'situs-uji';
        $GLOBALS['wpmgr_test_opsi']['wpmgr_secret']  = str_repeat( 'a', 64 );
        $hasil = call_user_func( $r['permission_callback'], new WPMGR_FakeRequestDorong() );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( array( 'status' => 401 ), $hasil->get_error_data() );
    }
}
