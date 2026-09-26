<?php
use PHPUnit\Framework\TestCase;

final class FileTest extends TestCase {

    private $akar;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-file-' . bin2hex( random_bytes( 6 ) ) . '/';
        mkdir( $this->akar . 'wp-content/uploads', 0777, true );
        file_put_contents( $this->akar . 'index.php', '<?php // indeks' );
        file_put_contents( $this->akar . 'wp-content/uploads/biner.bin', "\0\xff\x1a" . str_repeat( 'z', 100 ) );
        file_put_contents( $this->akar . 'wp-config.php', '<?php // rahasia' );
        WPMGR_Staging_File::$maks_paket = 8388608;
    }

    protected function tearDown(): void {
        StagingDasarTest::hapus( rtrim( $this->akar, '/' ) );
    }

    public function test_paket_beberapa_berkas(): void {
        $data = WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'index.php', 'wp-content/uploads/biner.bin' ) ) );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( $data );
        $this->assertSame( '<?php // indeks', $bagian[0] );
        $this->assertSame( "\0\xff\x1a" . str_repeat( 'z', 100 ), $bagian[1] );
        $this->assertSame( 'index.php', $meta['berkas'][0]['path'] );
        $this->assertSame( filemtime( $this->akar . 'index.php' ), $meta['berkas'][0]['mtime'] );
        $this->assertArrayNotHasKey( 'hilang', $meta['berkas'][0] );
    }

    public function test_berkas_yang_hilang_ditandai(): void {
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai(
            WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'sudah-dihapus.php', 'index.php' ) ) )
        );
        $this->assertTrue( $meta['berkas'][0]['hilang'] );
        $this->assertSame( '', $bagian[0] );
        $this->assertSame( '<?php // indeks', $bagian[1] );
    }

    public function test_path_berbahaya_menolak_seluruh_permintaan(): void {
        foreach ( array( 'wp-config.php', '../index.php', '/etc/passwd', 'wp-content/cache/a' ) as $p ) {
            $hasil = WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'index.php', $p ) ) );
            $this->assertInstanceOf( WP_Error::class, $hasil, $p );
        }
    }

    public function test_body_salah(): void {
        foreach ( array( null, 'x', array(), array( 'berkas' => array() ), array( 'berkas' => array( 5 ) ),
                         array( 'berkas' => array_fill( 0, 2001, 'index.php' ) ),
                         array( 'rentang' => array( 'path' => 'index.php', 'dari' => '0', 'panjang' => 5 ) ),
                         array( 'rentang' => array( 'path' => 'index.php', 'dari' => -1, 'panjang' => 5 ) ),
                         array( 'rentang' => array( 'path' => 'index.php', 'dari' => 0, 'panjang' => 0 ) ) ) as $body ) {
            $hasil = WPMGR_Staging_File::ambil( $this->akar, $body );
            $this->assertInstanceOf( WP_Error::class, $hasil );
            $this->assertSame( 'wpmgr_staging_permintaan', $hasil->get_error_code() );
        }
    }

    public function test_melebihi_batas_paket(): void {
        WPMGR_Staging_File::$maks_paket = 20;
        $hasil = WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'index.php', 'wp-content/uploads/biner.bin' ) ) );
        $this->assertSame( 'wpmgr_staging_terlalu_besar', $hasil->get_error_code() );
        $this->assertSame( array( 'status' => 413 ), $hasil->get_error_data() );
    }

    public function test_rentang_berkas_besar(): void {
        $isi = "\0\xff\x1a" . str_repeat( 'z', 100 );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'wp-content/uploads/biner.bin', 'dari' => 2, 'panjang' => 10 ) ) ) );
        $this->assertSame( substr( $isi, 2, 10 ), $bagian[0] );
        $this->assertSame( 2, $meta['berkas'][0]['dari'] );
        $this->assertSame( strlen( $isi ), $meta['berkas'][0]['total'] );

        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'wp-content/uploads/biner.bin', 'dari' => 100, 'panjang' => 50 ) ) ) );
        $this->assertSame( substr( $isi, 100 ), $bagian[0] );

        list( , $bagian ) = WPMGR_Staging_Paket::urai( WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'wp-content/uploads/biner.bin', 'dari' => 500, 'panjang' => 5 ) ) ) );
        $this->assertSame( '', $bagian[0] );
    }

    public function test_rentang_melebihi_batas(): void {
        WPMGR_Staging_File::$maks_paket = 8;
        $hasil = WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'index.php', 'dari' => 0, 'panjang' => 9 ) ) );
        $this->assertSame( 'wpmgr_staging_terlalu_besar', $hasil->get_error_code() );
    }
}
