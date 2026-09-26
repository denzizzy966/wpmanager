<?php
use PHPUnit\Framework\TestCase;

if ( ! function_exists( 'get_bloginfo' ) ) {
    function get_bloginfo( $apa = '' ) {
        return 'version' === $apa ? '6.5' : '';
    }
}
if ( ! function_exists( 'home_url' ) ) {
    function home_url() {
        return 'https://contoh.test';
    }
}
if ( ! function_exists( 'site_url' ) ) {
    function site_url() {
        return 'https://contoh.test/wp';
    }
}
if ( ! function_exists( 'is_multisite' ) ) {
    function is_multisite() {
        return false;
    }
}

final class WPMGR_FakeWpdbManifest {
    public $prefix  = 'wp_';
    public $charset = 'utf8mb4';
    public $jawaban = array();

    public function esc_like( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function prepare( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        return vsprintf( str_replace( '%s', "'%s'", $sql ), $args );
    }

    public function get_results( $sql, $format = null ) {
        foreach ( $this->jawaban as $pola => $hasil ) {
            if ( false !== strpos( $sql, $pola ) ) {
                return $hasil;
            }
        }
        return array();
    }
}

final class ManifestTest extends TestCase {

    private $akar;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-man-' . bin2hex( random_bytes( 6 ) ) . '/';
        mkdir( $this->akar, 0777, true );
        WPMGR_Staging_Manifest::$maks_hash     = 52428800;
        WPMGR_Staging_Manifest::$anggaran_hash = 536870912;
    }

    protected function tearDown(): void {
        StagingDasarTest::hapus( rtrim( $this->akar, '/' ) );
    }

    private function tulis( $rel, $isi = 'x' ) {
        $abs = $this->akar . $rel;
        if ( ! is_dir( dirname( $abs ) ) ) {
            mkdir( dirname( $abs ), 0777, true );
        }
        return false !== @file_put_contents( $abs, $isi );
    }

    private function jalan_semua( $batas ) {
        $semua  = array();
        $kursor = '';
        for ( $i = 0; $i < 100; $i++ ) {
            $h = WPMGR_Staging_Manifest::jalan( $this->akar, $kursor, $batas, microtime( true ) + 30 );
            foreach ( $h['berkas'] as $b ) {
                $semua[] = $b['path'];
            }
            if ( ! $h['lagi'] ) {
                return $semua;
            }
            $kursor = $h['kursor'];
        }
        $this->fail( 'Paging tidak berhenti.' );
    }

    public function test_menelusuri_dan_mengecualikan(): void {
        foreach ( array( 'index.php', 'wp-config.php', 'debug.log', 'wp-content/cache/a.html',
                         'wp-content/updraft/b.zip', 'wp-content/backups-dup-lite/c.zip',
                         'wp-content/wpmgr-dorong/d/e', 'wp-content/themes/t/style.css',
                         'wp-content/uploads/2026/09/f.jpg' ) as $p ) {
            $this->tulis( $p, 'isi-' . $p );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertFalse( $h['lagi'] );
        $this->assertNull( $h['kursor'] );
        $this->assertSame(
            array( 'index.php', 'wp-content/themes/t/style.css', 'wp-content/uploads/2026/09/f.jpg' ),
            array_column( $h['berkas'], 'path' )
        );
        $this->assertSame( hash( 'sha256', 'isi-index.php' ), $h['berkas'][0]['hash'] );
        $this->assertSame( strlen( 'isi-index.php' ), $h['berkas'][0]['ukuran'] );
        $this->assertIsInt( $h['berkas'][0]['mtime'] );
    }

    public function test_berkas_besar_tanpa_hash(): void {
        WPMGR_Staging_Manifest::$maks_hash = 5;
        $this->tulis( 'kecil.txt', '12345' );
        $this->tulis( 'besar.bin', '123456' );
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $per_path = array_column( $h['berkas'], 'hash', 'path' );
        $this->assertNull( $per_path['besar.bin'] );
        $this->assertSame( hash( 'sha256', '12345' ), $per_path['kecil.txt'] );
    }

    public function test_paging_mengikuti_urutan_dfs_tanpa_duplikat(): void {
        foreach ( array( 'a/x.txt', 'a/y.txt', 'a-b.txt', 'b.txt', 'c/d/e.txt' ) as $p ) {
            $this->tulis( $p );
        }
        $harapan = array( 'a/x.txt', 'a/y.txt', 'a-b.txt', 'b.txt', 'c/d/e.txt' );
        $this->assertSame( $harapan, $this->jalan_semua( 5000 ) );
        $this->assertSame( $harapan, $this->jalan_semua( 2 ) );
        $this->assertSame( $harapan, $this->jalan_semua( 1 ) );
    }

    public function test_kursor_berkas_yang_sudah_dihapus_tetap_maju(): void {
        foreach ( array( 'a/w.txt', 'a/z.txt', 'b.txt' ) as $p ) {
            $this->tulis( $p );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, 'a/xx.txt', 5000, microtime( true ) + 30 );
        $this->assertSame( array( 'a/z.txt', 'b.txt' ), array_column( $h['berkas'], 'path' ) );
    }

    public function test_nama_non_ascii_bukan_utf8_dan_panjang(): void {
        $panjang = 'wp-content/uploads/' . str_repeat( 'é', 100 ) . '.txt';
        $this->tulis( 'wp-content/uploads/ü-berkas.txt' );
        $this->tulis( $panjang );
        $this->tulis( "wp-content/uploads/\xff\xfe.txt" );
        // Windows menyimpan nama berkas sebagai UTF-16 dan PHP mengembalikannya
        // sudah dikonversi; hanya di Linux nama bukan UTF-8 benar-benar ada.
        $ada_bukan_utf8 = false;
        foreach ( scandir( $this->akar . 'wp-content/uploads' ) as $n ) {
            $ada_bukan_utf8 = $ada_bukan_utf8 || 1 !== preg_match( '//u', $n );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $path = array_column( $h['berkas'], 'path' );
        $this->assertContains( 'wp-content/uploads/ü-berkas.txt', $path );
        $this->assertContains( $panjang, $path );
        foreach ( $path as $p ) {
            $this->assertSame( 1, preg_match( '//u', $p ) );
        }
        if ( $ada_bukan_utf8 ) {
            $this->assertGreaterThanOrEqual( 1, $h['jumlah_dilewati'] );
            $this->assertSame( 'nama_bukan_utf8', $h['dilewati'][0]['alasan'] );
        }
        // Seluruh hasil tetap bisa dikodekan JSON: satu nama rusak tidak boleh
        // membuat json_encode() gagal dan membungkam seluruh manifest.
        $this->assertNotFalse( json_encode( $h ) );
    }

    public function test_symlink_dilewati(): void {
        $this->tulis( 'asli.txt' );
        if ( ! @symlink( $this->akar . 'asli.txt', $this->akar . 'tautan.txt' ) ) {
            $this->markTestSkipped( 'Symlink tidak didukung di sistem ini.' );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertSame( array( 'asli.txt' ), array_column( $h['berkas'], 'path' ) );
        $this->assertSame( 'symlink', $h['dilewati'][0]['alasan'] );
    }

    public function test_anggaran_hash_dan_tenggat_menjamin_kemajuan(): void {
        $this->tulis( 'a.txt', '1234' );
        $this->tulis( 'b.txt', '1234' );
        WPMGR_Staging_Manifest::$anggaran_hash = 5;
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertSame( array( 'a.txt' ), array_column( $h['berkas'], 'path' ) );
        $this->assertTrue( $h['lagi'] );
        $this->assertSame( 'a.txt', $h['kursor'] );

        WPMGR_Staging_Manifest::$anggaran_hash = 536870912;
        $lewat = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) - 1 );
        $this->assertCount( 1, $lewat['berkas'] );
        $this->assertTrue( $lewat['lagi'] );
    }

    public function test_batas_dijepit(): void {
        $this->assertSame( 5000, WPMGR_Staging_Manifest::batas( null ) );
        $this->assertSame( 5000, WPMGR_Staging_Manifest::batas( 0 ) );
        $this->assertSame( 5000, WPMGR_Staging_Manifest::batas( 99999 ) );
        $this->assertSame( 20, WPMGR_Staging_Manifest::batas( '20' ) );
    }

    public function test_info_dan_tabel(): void {
        $wpdb = new WPMGR_FakeWpdbManifest();
        $wpdb->jawaban = array(
            'SHOW TABLE STATUS' => array(
                array( 'Name' => 'wp_posts', 'Rows' => '12', 'Data_length' => '1000', 'Index_length' => '24', 'Engine' => 'InnoDB' ),
                array( 'Name' => 'wp_tampilan', 'Rows' => null, 'Data_length' => null, 'Index_length' => null, 'Engine' => null ),
                array( 'Name' => 'wp_bad-name', 'Rows' => '1', 'Data_length' => '1', 'Index_length' => '0', 'Engine' => 'MyISAM' ),
                array( 'Name' => 'lain_posts', 'Rows' => '1', 'Data_length' => '1', 'Index_length' => '0', 'Engine' => 'MyISAM' ),
            ),
            'SHOW KEYS FROM `wp_posts`' => array(
                array( 'Column_name' => 'ID', 'Seq_in_index' => '1' ),
            ),
        );
        $info = WPMGR_Staging_Manifest::info( $wpdb, $this->akar, rtrim( $this->akar, '/' ) . '/wp-content' );
        $this->assertSame( 'wp_', $info['table_prefix'] );
        $this->assertSame( 'https://contoh.test', $info['home'] );
        $this->assertFalse( $info['konten_di_luar'] );
        $this->assertSame( array( array( 'nama' => 'wp_posts', 'baris' => 12, 'ukuran' => 1024,
                                         'mesin' => 'InnoDB', 'pk' => array( 'ID' ) ) ), $info['tabel'] );
        $this->assertSame( 2, $info['tabel_dilewati'] );
        $luar = WPMGR_Staging_Manifest::info( $wpdb, $this->akar, sys_get_temp_dir() . '/konten-lain' );
        $this->assertTrue( $luar['konten_di_luar'] );
    }

    public function test_pk_komposit_berurutan(): void {
        $wpdb = new WPMGR_FakeWpdbManifest();
        $wpdb->jawaban = array( 'SHOW KEYS FROM `wp_x`' => array(
            array( 'Column_name' => 'b', 'Seq_in_index' => '2' ),
            array( 'Column_name' => 'a', 'Seq_in_index' => '1' ),
        ) );
        $this->assertSame( array( 'a', 'b' ), WPMGR_Staging_Manifest::pk( $wpdb, 'wp_x' ) );
    }

    public function test_batas_unggah(): void {
        $this->assertSame( 4194304, WPMGR_Staging_Manifest::batas_unggah( '8M' ) );
        $this->assertSame( 4194304, WPMGR_Staging_Manifest::batas_unggah( '64M' ) );
        $this->assertSame( 1048576, WPMGR_Staging_Manifest::batas_unggah( '2M' ) );
        $this->assertSame( 262144, WPMGR_Staging_Manifest::batas_unggah( '100K' ) );
        $this->assertSame( 4194304, WPMGR_Staging_Manifest::batas_unggah( '0' ) );
        $this->assertSame( 2147483648, WPMGR_Staging_Manifest::ke_byte( '2G' ) );
    }
}
