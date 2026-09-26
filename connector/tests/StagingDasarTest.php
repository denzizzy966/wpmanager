<?php
use PHPUnit\Framework\TestCase;

final class StagingDasarTest extends TestCase {

    private $akar;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-stg-' . bin2hex( random_bytes( 6 ) ) . '/';
        mkdir( $this->akar . 'wp-content/uploads', 0777, true );
    }

    protected function tearDown(): void {
        self::hapus( rtrim( $this->akar, '/' ) );
        $GLOBALS['wpmgr_test_opsi'] = array();
    }

    public static function hapus( $jalur ) {
        if ( is_link( $jalur ) || is_file( $jalur ) ) {
            @unlink( $jalur );
            return;
        }
        if ( ! is_dir( $jalur ) ) {
            return;
        }
        foreach ( scandir( $jalur ) as $n ) {
            if ( '.' !== $n && '..' !== $n ) {
                self::hapus( $jalur . '/' . $n );
            }
        }
        @rmdir( $jalur );
    }

    private function symlink_atau_lewati( $target, $tautan ) {
        if ( ! @symlink( $target, $tautan ) ) {
            $this->markTestSkipped( 'Sistem ini tidak mengizinkan symlink (Windows tanpa Developer Mode).' );
        }
    }

    public function test_putuskan_mendahulukan_penolakan_hmac(): void {
        $tolak = new WP_Error( 'wpmgr_ditolak', 'x', array( 'status' => 401 ) );
        $this->assertSame( $tolak, WPMGR_Staging::putuskan( $tolak, true ) );
        $this->assertSame( $tolak, WPMGR_Staging::putuskan( $tolak, false ) );
    }

    public function test_putuskan_403_bila_staging_mati(): void {
        $hasil = WPMGR_Staging::putuskan( true, false );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_mati', $hasil->get_error_code() );
        $this->assertSame( array( 'status' => 403 ), $hasil->get_error_data() );
        $this->assertTrue( WPMGR_Staging::putuskan( true, true ) );
    }

    public function test_izinkan_staging_default_mati(): void {
        $this->assertFalse( WPMGR_Settings::izinkan_staging() );
        $GLOBALS['wpmgr_test_opsi']['wpmgr_izinkan_staging'] = '1';
        $this->assertTrue( WPMGR_Settings::izinkan_staging() );
        $this->assertTrue( WPMGR_Staging::fitur_aktif() );
        $GLOBALS['wpmgr_test_opsi']['wpmgr_izinkan_staging'] = '0';
        $this->assertFalse( WPMGR_Staging::fitur_aktif() );
    }

    public function test_fitur_staging_hanya_bila_diizinkan(): void {
        $this->assertSame( array( 'self_update', 'events', 'traffic' ), WPMGR_Skema::fitur( false ) );
        $this->assertSame( array( 'self_update', 'events', 'traffic', 'staging' ), WPMGR_Skema::fitur( false, true ) );
        $this->assertSame( array( 'self_update', 'staging' ), WPMGR_Skema::fitur( true, true ) );
    }

    public function path_berbahaya(): array {
        return array(
            'kosong'          => array( '' ),
            'absolut'         => array( '/etc/passwd' ),
            'naik'            => array( '../x' ),
            'naik di tengah'  => array( 'a/../b' ),
            'segmen kosong'   => array( 'a//b' ),
            'titik'           => array( 'a/./b' ),
            'drive windows'   => array( 'C:/x' ),
            'backslash'       => array( 'a\\b' ),
            'nul'             => array( "a\0b" ),
            'baris baru'      => array( "a\nb" ),
            'terlalu panjang' => array( str_repeat( 'a', 1025 ) ),
            'segmen panjang'  => array( 'a/' . str_repeat( 'b', 256 ) ),
            'bukan utf8'      => array( "\xff.txt" ),
            'slash akhir'     => array( 'a/' ),
        );
    }

    /** @dataProvider path_berbahaya */
    public function test_normalisasi_menolak( $path ): void {
        $hasil = WPMGR_Staging_Path::normalisasi( $path );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_path', $hasil->get_error_code() );
    }

    public function test_normalisasi_menerima_path_sah(): void {
        foreach ( array( 'wp-content/uploads/ü-berkas.txt', '.htaccess', 'wp-content/plugins/a b/c.php',
                         'wp-content/uploads/' . str_repeat( 'é', 120 ) . '.jpg' ) as $p ) {
            $this->assertSame( $p, WPMGR_Staging_Path::normalisasi( $p ) );
        }
    }

    public function test_dikecualikan(): void {
        foreach ( array( 'wp-config.php', '.maintenance', 'debug.log', 'wp-content/debug.LOG',
                         'wp-content/cache/a/b.html', 'wp-content/cache', 'wp-content/wpmgr-dorong/x/y',
                         'wp-content/updraft/b.zip', 'wp-content/ai1wm-backups/a.wpress',
                         'wp-content/backups-dup-lite/a.zip', 'wp-content/wpvividbackups/a.zip' ) as $p ) {
            $this->assertTrue( WPMGR_Staging_Path::dikecualikan( $p ), $p );
        }
        foreach ( array( 'index.php', 'wp-content/uploads/cache/a.jpg', 'wp-content/plugins/updraft/x.php',
                         'wp-content/themes/a/logs.php', 'wp-config-sample.php' ) as $p ) {
            $this->assertFalse( WPMGR_Staging_Path::dikecualikan( $p ), $p );
        }
    }

    public function test_boleh_ditulis(): void {
        foreach ( array( 'wp-content/themes/x/style.css', 'wp-admin/index.php', 'wp-includes/version.php',
                         'index.php', 'wp-login.php', 'wp-settings.php', '.htaccess', 'xmlrpc.php',
                         'license.txt', 'readme.html', 'wp-content/uploads/2026/09/a.jpg' ) as $p ) {
            $this->assertTrue( WPMGR_Staging_Path::boleh_ditulis( $p ), $p );
        }
        foreach ( array( 'wp-config.php', 'wp-content/plugins/wp-manager-connector/wp-manager-connector.php',
                         'wp-content/mu-plugins/wpmgr-staging.php', 'wp-content/mu-plugins/wpmgr-dorong-aman.php',
                         'lain.php', 'google123.html', 'foo/bar.php', '.maintenance', 'wp-content/cache/a',
                         'debug.log', '../index.php', 'WP-LOGIN.PHP' ) as $p ) {
            $this->assertFalse( WPMGR_Staging_Path::boleh_ditulis( $p ), $p );
        }
    }

    public function test_untuk_dibaca(): void {
        file_put_contents( $this->akar . 'index.php', '<?php' );
        $this->assertSame( $this->akar . 'index.php', WPMGR_Staging_Path::untuk_dibaca( $this->akar, 'index.php' ) );
        $hilang = WPMGR_Staging_Path::untuk_dibaca( $this->akar, 'tidak-ada.php' );
        $this->assertSame( 'wpmgr_staging_tidak_ada', $hilang->get_error_code() );
        file_put_contents( $this->akar . 'wp-config.php', '<?php' );
        $this->assertSame( 'wpmgr_staging_path',
            WPMGR_Staging_Path::untuk_dibaca( $this->akar, 'wp-config.php' )->get_error_code() );
    }

    public function test_untuk_dibaca_menolak_symlink_keluar(): void {
        $luar = sys_get_temp_dir() . '/wpmgr-luar-' . bin2hex( random_bytes( 4 ) ) . '.txt';
        file_put_contents( $luar, 'rahasia' );
        try {
            $this->symlink_atau_lewati( $luar, $this->akar . 'wp-content/uploads/tautan.txt' );
            $hasil = WPMGR_Staging_Path::untuk_dibaca( $this->akar, 'wp-content/uploads/tautan.txt' );
            $this->assertInstanceOf( WP_Error::class, $hasil );
        } finally {
            @unlink( $luar );
        }
    }

    public function test_untuk_ditulis_menolak_leluhur_symlink_keluar(): void {
        $luar = sys_get_temp_dir() . '/wpmgr-luar-' . bin2hex( random_bytes( 4 ) );
        mkdir( $luar );
        try {
            $this->symlink_atau_lewati( $luar, $this->akar . 'wp-content/uploads/luar' );
            $hasil = WPMGR_Staging_Path::untuk_ditulis( $this->akar, 'wp-content/uploads/luar/baru/x.php' );
            $this->assertInstanceOf( WP_Error::class, $hasil );
        } finally {
            self::hapus( $luar );
        }
    }

    public function test_untuk_ditulis_path_baru_di_dalam(): void {
        $this->assertSame( $this->akar . 'wp-content/uploads/2026/x.jpg',
            WPMGR_Staging_Path::untuk_ditulis( $this->akar, 'wp-content/uploads/2026/x.jpg' ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_Staging_Path::untuk_ditulis( $this->akar, 'wp-config.php' ) );
    }

    public function test_paket_bolak_balik(): void {
        $isi   = array( '', "biner\0\xff\x1a\n'\"" );
        $data  = WPMGR_Staging_Paket::susun( array( 'jenis' => 'uji', 'berkas' => array(
            array( 'path' => 'a.txt' ), array( 'path' => 'wp-content/ü.bin' ),
        ) ), $isi );
        $this->assertSame( "WPMGRPAK1\n", substr( $data, 0, 10 ) );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( $data );
        $this->assertSame( $isi, $bagian );
        $this->assertSame( 'uji', $meta['jenis'] );
        $this->assertSame( 0, $meta['berkas'][0]['ukuran'] );
        $this->assertSame( hash( 'sha256', $isi[1] ), $meta['berkas'][1]['sha256'] );
        $this->assertSame( 'wp-content/ü.bin', $meta['berkas'][1]['path'] );
    }

    public function test_paket_rusak_ditolak(): void {
        $data = WPMGR_Staging_Paket::susun( array( 'berkas' => array( array( 'path' => 'a' ) ) ), array( 'abcdef' ) );
        $this->assertSame( 'wpmgr_staging_paket', WPMGR_Staging_Paket::urai( 'BUKANPAKET' )->get_error_code() );
        $this->assertSame( 'wpmgr_staging_paket', WPMGR_Staging_Paket::urai( "WPMGRPAK1\nzzzzzzzz\n{}" )->get_error_code() );
        $this->assertSame( 'wpmgr_staging_paket', WPMGR_Staging_Paket::urai( "WPMGRPAK1\n7fffffff\n{}" )->get_error_code() );
        $this->assertSame( 'wpmgr_staging_paket', WPMGR_Staging_Paket::urai( $data . 'x' )->get_error_code() );
        $this->assertSame( 'wpmgr_staging_paket', WPMGR_Staging_Paket::urai( substr( $data, 0, -1 ) )->get_error_code() );
        $rusak = substr( $data, 0, -1 ) . 'X';
        $galat = WPMGR_Staging_Paket::urai( $rusak );
        $this->assertSame( 'wpmgr_staging_hash', $galat->get_error_code() );
        $this->assertSame( array( 'status' => 422 ), $galat->get_error_data() );
    }

    public function test_respons_biner_disajikan_apa_adanya(): void {
        $r = WPMGR_Staging::respons_biner( "isi\0biner" );
        $this->assertSame( 'application/octet-stream', $r->get_headers()['Content-Type'] );
        $this->assertSame( hash( 'sha256', "isi\0biner" ), $r->get_headers()['X-Wpmgr-Sha256'] );
        ob_start();
        $disajikan = WPMGR_Staging::sajikan_biner( false, $r, null, null );
        $keluar    = ob_get_clean();
        $this->assertTrue( $disajikan );
        $this->assertSame( "isi\0biner", $keluar );
        // Respons JSON biasa tidak disentuh.
        $this->assertFalse( WPMGR_Staging::sajikan_biner( false, new WP_REST_Response( array( 'a' => 1 ) ), null, null ) );
    }
}
