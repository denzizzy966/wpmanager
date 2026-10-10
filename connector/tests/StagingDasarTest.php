<?php
use PHPUnit\Framework\TestCase;

final class StagingDasarTest extends TestCase {

    private $akar;

    protected function setUp(): void {
        // Dinormalisasi ke '/' (fix R1): sys_get_temp_dir() di Windows memakai
        // '\\', persis seperti ABSPATH mentah -- dan WPMGR_Staging::root()
        // sendiri selalu menormalisasinya sebelum dipakai. Tanpa ini,
        // perbandingan realpath() (yang juga dinormalisasi ke '/') di
        // path_kanonik() tidak pernah cocok dengan $this->akar apa adanya.
        $this->akar = str_replace( '\\', '/', sys_get_temp_dir() ) . '/wpmgr-stg-' . bin2hex( random_bytes( 6 ) ) . '/';
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
                         'wp-content/uploads/' . str_repeat( 'é', 120 ) . '.jpg', 'jquery.min.js' ) as $p ) {
            $this->assertSame( $p, WPMGR_Staging_Path::normalisasi( $p ) );
        }
    }

    // ---- fix R2: sintaks NTFS alternate data stream, titik/spasi akhir ----

    public function test_normalisasi_menolak_ads_dan_titik_spasi_akhir(): void {
        foreach ( array(
            'wp-content/mu-plugins/wpmgr-staging.php::$DATA',
            'wp-content/plugins/wp-manager-connector::$INDEX_ALLOCATION/wp-manager-connector.php',
            'wp-config.php.',
            'wp-config.php ',
        ) as $p ) {
            $hasil = WPMGR_Staging_Path::normalisasi( $p );
            $this->assertInstanceOf( WP_Error::class, $hasil, $p );
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

    // ---- fix R2: sintaks NTFS alternate data stream pada jalur tulis ------

    public function test_untuk_ditulis_menolak_ads_dan_titik_spasi_akhir(): void {
        // Berkas ASLI yang sebelum fix R2 bisa ditimpa lewat sintaks ADS
        // ('nama::$DATA' merujuk isi 'nama' itu sendiri; 'dir::$INDEX_ALLOCATION'
        // adalah alias 'dir' itu sendiri) tanpa pernah cocok dengan larangan
        // string apa pun di boleh_ditulis()/dikecualikan().
        mkdir( $this->akar . 'wp-content/mu-plugins', 0777, true );
        file_put_contents( $this->akar . 'wp-content/mu-plugins/wpmgr-staging.php', 'ASLI' );
        mkdir( $this->akar . 'wp-content/plugins/wp-manager-connector', 0777, true );
        file_put_contents( $this->akar . 'wp-content/plugins/wp-manager-connector/wp-manager-connector.php', 'ASLI' );
        file_put_contents( $this->akar . 'wp-config.php', 'ASLI' );

        foreach ( array(
            'wp-content/mu-plugins/wpmgr-staging.php::$DATA',
            'wp-content/plugins/wp-manager-connector::$INDEX_ALLOCATION/wp-manager-connector.php',
            'wp-config.php.',
            'wp-config.php ',
        ) as $p ) {
            $this->assertInstanceOf( WP_Error::class, WPMGR_Staging_Path::untuk_ditulis( $this->akar, $p ), $p );
        }
    }

    // ---- fix R2 minor: leluhur symlink yang MENGGANTUNG (dangling) --------

    public function test_untuk_ditulis_menolak_leluhur_symlink_gantung(): void {
        $tidak_ada = sys_get_temp_dir() . '/wpmgr-tidak-ada-' . bin2hex( random_bytes( 4 ) );
        // Sengaja TIDAK dibuat: symlink menggantung, menunjuk ke target yang
        // tidak ada, di luar akar. file_exists() mengembalikan false untuk
        // symlink macam ini, jadi ancestor walk yang hanya memakai
        // file_exists() melewatinya begitu saja seolah "belum ada".
        $this->symlink_atau_lewati( $tidak_ada, $this->akar . 'wp-content/uploads/gantung' );
        $hasil = WPMGR_Staging_Path::untuk_ditulis( $this->akar, 'wp-content/uploads/gantung/new.txt' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
    }

    // ---- fix R1: direktori symlink DI DALAM akar (bukan ke luar akar) -----

    public function test_untuk_dibaca_menolak_direktori_symlink_di_dalam_akar(): void {
        file_put_contents( $this->akar . 'wp-config.php', "define('DB_PASSWORD','rahasia');" );
        // 'akar' menunjuk ke root itu sendiri -- path relatif yang lewat
        // situ tampak berada "di dalam" root menurut perbandingan berawalan
        // lama, padahal sebenarnya jalan memutar untuk membaca wp-config.php.
        $this->symlink_atau_lewati( rtrim( $this->akar, '/' ), $this->akar . 'wp-content/uploads/akar' );
        $hasil = WPMGR_Staging_Path::untuk_dibaca( $this->akar, 'wp-content/uploads/akar/wp-config.php' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
    }

    public function test_untuk_ditulis_menolak_direktori_symlink_di_dalam_akar(): void {
        file_put_contents( $this->akar . 'wp-config.php', "define('DB_PASSWORD','rahasia');" );
        $this->symlink_atau_lewati( rtrim( $this->akar, '/' ), $this->akar . 'wp-content/uploads/akar' );
        $hasil = WPMGR_Staging_Path::untuk_ditulis( $this->akar, 'wp-content/uploads/akar/wp-config.php' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
    }

    public function test_menolak_symlink_berkas_ke_berkas_lain_di_dalam_akar(): void {
        file_put_contents( $this->akar . 'wp-content/uploads/asli.txt', 'isi asli' );
        $this->symlink_atau_lewati(
            $this->akar . 'wp-content/uploads/asli.txt',
            $this->akar . 'wp-content/uploads/tautan-lokal.txt'
        );
        $this->assertInstanceOf( WP_Error::class,
            WPMGR_Staging_Path::untuk_dibaca( $this->akar, 'wp-content/uploads/tautan-lokal.txt' ) );
        $this->assertInstanceOf( WP_Error::class,
            WPMGR_Staging_Path::untuk_ditulis( $this->akar, 'wp-content/uploads/tautan-lokal.txt' ) );
    }

    // ---- fix R1: alias huruf besar/kecil dan nama pendek 8.3 (Windows/macOS) ----

    public function test_untuk_dibaca_menolak_variasi_huruf_besar_kecil(): void {
        file_put_contents( $this->akar . 'wp-config.php', "define('DB_PASSWORD','rahasia');" );
        if ( ! file_exists( $this->akar . 'WP-CONFIG.PHP' ) ) {
            $this->markTestSkipped( 'Sistem berkas ini case-sensitive (bukan Windows/macOS).' );
        }
        foreach ( array( 'WP-CONFIG.PHP', 'Wp-Config.php' ) as $variasi ) {
            $hasil = WPMGR_Staging_Path::untuk_dibaca( $this->akar, $variasi );
            $this->assertInstanceOf( WP_Error::class, $hasil, $variasi );
        }
    }

    public function test_untuk_dibaca_menolak_nama_pendek_8_3(): void {
        file_put_contents( $this->akar . 'wp-config.php', "define('DB_PASSWORD','rahasia');" );
        if ( ! file_exists( $this->akar . 'WP-CON~1.PHP' ) ) {
            $this->markTestSkipped( 'Nama pendek 8.3 tidak didukung/tidak aktif di volume ini.' );
        }
        $hasil = WPMGR_Staging_Path::untuk_dibaca( $this->akar, 'WP-CON~1.PHP' );
        $this->assertInstanceOf( WP_Error::class, $hasil );
    }

    public function test_boleh_ditulis_menolak_variasi_huruf_besar_kecil(): void {
        // Logika murni (tanpa I/O): berlaku di semua OS, bukan hanya yang
        // sistem berkasnya case-insensitive.
        foreach ( array(
            'WP-CONFIG.PHP',
            'wp-content/plugins/WP-MANAGER-CONNECTOR/x.php',
            'wp-content/mu-plugins/WPMGR-STAGING.php',
            'WP-CONTENT/MU-PLUGINS/wpmgr-dorong-aman.php',
        ) as $p ) {
            $this->assertFalse( WPMGR_Staging_Path::boleh_ditulis( $p ), $p );
        }
    }

    // ---- fix R1 minor (c): direktori terlarang tanpa slash akhir ----------

    public function test_boleh_ditulis_menolak_direktori_plugin_tanpa_slash_akhir(): void {
        $this->assertFalse(
            WPMGR_Staging_Path::boleh_ditulis( 'wp-content/plugins/wp-manager-connector' )
        );
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

    // ---- fix R1 minor (a): kunci meta['berkas'] harus berurutan 0..n-1 ----

    public function test_paket_menolak_kunci_berkas_tidak_berurutan(): void {
        // Dirakit manual (bukan lewat susun(), yang selalu menomori ulang
        // berurutan): meta dengan lubang di indeks (0, 2) tidak boleh
        // diterima -- pemanggil mencocokkan bagian isi ke meta['berkas']
        // berdasarkan posisi, dan lubang/urutan yang tidak berurutan membuat
        // pencocokan itu tidak lagi bisa dipercaya.
        $meta = array(
            'berkas' => array(
                0 => array( 'path' => 'a', 'ukuran' => 0, 'sha256' => hash( 'sha256', '' ) ),
                2 => array( 'path' => 'b', 'ukuran' => 0, 'sha256' => hash( 'sha256', '' ) ),
            ),
        );
        $json  = json_encode( $meta, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE );
        $data  = "WPMGRPAK1\n" . sprintf( '%08x', strlen( $json ) ) . "\n" . $json;
        $galat = WPMGR_Staging_Paket::urai( $data );
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( 'wpmgr_staging_paket', $galat->get_error_code() );
    }

    // ---- fix R1 minor (e): pesan 403 berbeda bila site ini sendiri staging ----

    public function test_putuskan_pesan_berbeda_saat_mode_staging(): void {
        $biasa   = WPMGR_Staging::putuskan( true, false, false );
        $staging = WPMGR_Staging::putuskan( true, false, true );
        $this->assertStringContainsString( 'Izinkan staging', $biasa->get_error_message() );
        $this->assertStringNotContainsString( 'Izinkan staging', $staging->get_error_message() );
        $this->assertStringContainsString( 'staging', strtolower( $staging->get_error_message() ) );
        $this->assertSame( 'wpmgr_staging_mati', $staging->get_error_code() );
        $this->assertSame( array( 'status' => 403 ), $staging->get_error_data() );
    }

    public function test_respons_biner_disajikan_apa_adanya(): void {
        $r = WPMGR_Staging::respons_biner( "isi\0biner" );
        $this->assertSame( 'application/octet-stream', $r->get_headers()['Content-Type'] );
        $this->assertSame( hash( 'sha256', "isi\0biner" ), $r->get_headers()['X-Wpmgr-Sha256'] );
        ob_start();
        WPMGR_Staging::$ob_dasar = ob_get_level();
        try {
            $disajikan = WPMGR_Staging::sajikan_biner( false, $r, null, null );
        } finally {
            WPMGR_Staging::$ob_dasar = 0;
        }
        $keluar = ob_get_clean();
        $this->assertTrue( $disajikan );
        $this->assertSame( "isi\0biner", $keluar );
        // Respons JSON biasa tidak disentuh.
        $this->assertFalse( WPMGR_Staging::sajikan_biner( false, new WP_REST_Response( array( 'a' => 1 ) ), null, null ) );
    }

    public function test_respons_biner_melewati_buffer_plugin_yang_mengubah_keluaran(): void {
        // Plugin seperti pengubah mixed content (http -> https) memasang ob_start dengan
        // callback; isi biner tidak boleh ikut diubah, atau hash potongannya tidak cocok.
        $isi = "url http://contoh.test/a.css\0biner";
        $r   = WPMGR_Staging::respons_biner( $isi );
        ob_start();
        WPMGR_Staging::$ob_dasar = ob_get_level();
        ob_start( function ( $s ) { return str_replace( 'http://', 'https://', $s ); } );
        ob_start();
        try {
            $disajikan = WPMGR_Staging::sajikan_biner( false, $r, null, null );
        } finally {
            WPMGR_Staging::$ob_dasar = 0;
        }
        $keluar = ob_get_clean();
        $this->assertTrue( $disajikan );
        $this->assertSame( $isi, $keluar );
        $this->assertSame( hash( 'sha256', $keluar ), $r->get_headers()['X-Wpmgr-Sha256'] );
    }
}
