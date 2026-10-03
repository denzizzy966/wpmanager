<?php
use PHPUnit\Framework\TestCase;

/**
 * Mu-plugin pratinjau VPS (spec Lapis 4 §7.6, §18.2). Dijalankan di proses
 * PHP terpisah dengan stub add_filter/add_action, supaya stub WordPress milik
 * bootstrap test tidak ikut dan perilaku `return` di puncak berkas teruji.
 */
final class PratinjauTest extends TestCase {

    private function templat(): string {
        return __DIR__ . '/../wp-manager-connector/templates/wpmgr-pratinjau.php.tpl';
    }

    private function skrip( string $awal, string $akhir ): string {
        $kepala = <<<'PHP'
<?php
define( 'ABSPATH', '/tmp/' );
$GLOBALS['wpmgr_kait'] = array();
function add_filter( $nama, $fungsi, $prioritas = 10, $argumen = 1 ) {
    $GLOBALS['wpmgr_kait'][ $nama ][] = array( $fungsi, $prioritas );
    return true;
}
function add_action( $nama, $fungsi, $prioritas = 10, $argumen = 1 ) {
    return add_filter( $nama, $fungsi, $prioritas, $argumen );
}
function esc_html( $teks ) {
    return htmlspecialchars( $teks, ENT_QUOTES );
}
function wpmgr_kait( $nama ) {
    return $GLOBALS['wpmgr_kait'][ $nama ][0];
}
PHP;
        return $kepala . "\n" . $awal . "\ninclude " . var_export( $this->templat(), true ) . ";\n" . $akhir . "\n";
    }

    private function jalankan( string $awal, string $akhir ): array {
        $skrip = sys_get_temp_dir() . '/wpmgr-pratinjau-' . getmypid() . '-' . mt_rand() . '.php';
        file_put_contents( $skrip, $this->skrip( $awal, $akhir ) );
        exec( escapeshellarg( PHP_BINARY ) . ' ' . escapeshellarg( $skrip ) . ' 2>&1', $keluar, $kode );
        unlink( $skrip );
        $this->assertSame( 0, $kode, implode( "\n", $keluar ) );
        return $keluar;
    }

    private function konstanta( string $host ): string {
        return "define( 'WPMGR_PRATINJAU', true );\n"
            . "define( 'WPMGR_PRATINJAU_HOST', 'vps-toko.staging.contoh.id' );\n"
            . "define( 'WPMGR_DOMAIN', 'toko.co.id' );\n"
            . "\$_SERVER['HTTP_HOST'] = " . var_export( $host, true ) . ";\n";
    }

    public function test_template_tanpa_placeholder_dan_valid_php(): void {
        $isi = file_get_contents( $this->templat() );
        $this->assertStringStartsWith( '<?php', $isi );
        $this->assertStringNotContainsString( '__WPMGR_', $isi );
        exec( escapeshellarg( PHP_BINARY ) . ' -l ' . escapeshellarg( $this->templat() ) . ' 2>&1', $keluar, $kode );
        $this->assertSame( 0, $kode, implode( "\n", $keluar ) );
    }

    public function test_diam_tanpa_konstanta(): void {
        $keluar = $this->jalankan( "\$_SERVER['HTTP_HOST'] = 'vps-toko.staging.contoh.id';",
            "echo count( \$GLOBALS['wpmgr_kait'] ), '|', ob_get_level();" );
        $this->assertSame( array( '0|0' ), $keluar );
    }

    public function test_pre_wp_mail_false_dengan_prioritas_terakhir(): void {
        $keluar = $this->jalankan( $this->konstanta( 'toko.co.id' ),
            "\$k = wpmgr_kait( 'pre_wp_mail' ); echo json_encode( array( \$k[0]( null, array() ), \$k[1] === PHP_INT_MAX ) );" );
        $this->assertSame( array( '[false,true]' ), $keluar );
    }

    public function test_penerima_phpmailer_dikosongkan(): void {
        $awal = $this->konstanta( 'toko.co.id' )
            . "class PhpMailerTiruan { public \$dikosongkan = false; "
            . "public function clearAllRecipients() { \$this->dikosongkan = true; } }";
        $keluar = $this->jalankan( $awal,
            "\$m = new PhpMailerTiruan(); \$k = wpmgr_kait( 'phpmailer_init' ); \$k[0]( \$m ); "
            . "echo json_encode( array( \$m->dikosongkan, \$k[1] === PHP_INT_MAX ) );" );
        $this->assertSame( array( '[true,true]' ), $keluar );
    }

    public function test_blog_public_nol_dan_noindex(): void {
        $keluar = $this->jalankan( $this->konstanta( 'toko.co.id' ),
            "\$b = wpmgr_kait( 'pre_option_blog_public' ); \$r = wpmgr_kait( 'wp_robots' ); "
            . "echo json_encode( array( \$b[0](), \$r[0]( array( 'max-image-preview' => 'large' ) ) ) );" );
        $this->assertSame( array( '["0",{"max-image-preview":"large","noindex":true,"nofollow":true}]' ), $keluar );
    }

    public function test_spanduk_dan_admin_bar(): void {
        $awal   = $this->konstanta( 'toko.co.id' )
            . "class BarTiruan { public \$simpul = array(); public function add_node( \$n ) { \$this->simpul[] = \$n; } }";
        $keluar = $this->jalankan( $awal,
            "\$a = wpmgr_kait( 'admin_notices' ); \$a[0](); \$bar = new BarTiruan(); \$m = wpmgr_kait( 'admin_bar_menu' ); "
            . "\$m[0]( \$bar ); echo \"\\n\", \$bar->simpul[0]['title'];" );
        $this->assertStringContainsString( 'PRATINJAU VPS', $keluar[0] );
        $this->assertStringContainsString( 'email diblokir, cron mati', $keluar[0] );
        $this->assertSame( 'PRATINJAU VPS — email diblokir, cron mati', $keluar[1] );
    }

    public function test_url_diganti_hanya_untuk_host_pratinjau(): void {
        $html  = 'a https://toko.co.id/x b https://www.toko.co.id/y c https:\\/\\/toko.co.id\\/z d https://lain.id/';
        $akhir = 'echo ' . var_export( $html, true ) . '; while ( ob_get_level() > 0 ) { ob_end_flush(); }';
        $this->assertSame(
            array( 'a https://vps-toko.staging.contoh.id/x b https://vps-toko.staging.contoh.id/y '
                . 'c https:\\/\\/vps-toko.staging.contoh.id\\/z d https://lain.id/' ),
            $this->jalankan( $this->konstanta( 'vps-toko.staging.contoh.id' ), $akhir )
        );
        $this->assertSame( array( $html ), $this->jalankan( $this->konstanta( 'toko.co.id' ), $akhir ) );
    }

    public function test_diam_bila_konstanta_bernilai_salah(): void {
        $awal = "define( 'WPMGR_PRATINJAU', false );\n"
            . "define( 'WPMGR_PRATINJAU_HOST', 'vps-toko.staging.contoh.id' );\n"
            . "define( 'WPMGR_DOMAIN', 'toko.co.id' );\n"
            . "\$_SERVER['HTTP_HOST'] = 'vps-toko.staging.contoh.id';";
        $this->assertSame( array( '0|0' ),
            $this->jalankan( $awal, "echo count( \$GLOBALS['wpmgr_kait'] ), '|', ob_get_level();" ) );
    }

    public function test_host_lain_bentuk_tidak_diganti(): void {
        // Header Host datang dari klien: hanya bentuk persis host pratinjau
        // yang memicu penggantian; huruf besar, port, atau sufiks lain tidak.
        $akhir = "echo 'x https://toko.co.id/'; while ( ob_get_level() > 0 ) { ob_end_flush(); }";
        foreach ( array( 'VPS-TOKO.staging.contoh.id', 'vps-toko.staging.contoh.id:443',
            'vps-toko.staging.contoh.id.jahat.id', '' ) as $host ) {
            $this->assertSame( array( 'x https://toko.co.id/' ),
                $this->jalankan( $this->konstanta( $host ), $akhir ), $host );
        }
    }

    public function test_kait_tahan_argumen_bukan_objek(): void {
        // Plugin lain bisa memanggil kait ini dengan argumen aneh; mu-plugin
        // tidak boleh membuat fatal error di seluruh situs.
        $keluar = $this->jalankan( $this->konstanta( 'toko.co.id' ),
            "\$k = wpmgr_kait( 'phpmailer_init' ); \$k[0]( null ); \$m = wpmgr_kait( 'admin_bar_menu' ); "
            . "\$m[0]( 'bukan-bar' ); \$r = wpmgr_kait( 'wp_robots' ); echo json_encode( \$r[0]( null ) );" );
        $this->assertSame( array( '{"noindex":true,"nofollow":true}' ), $keluar );
    }

    public function test_konten_berbahaya_hanya_domain_yang_berganti(): void {
        // Konten situs (bisa dikendalikan penyerang) tidak bisa menyisipkan
        // apa pun lewat penggantian: hanya literal domain asli yang berubah.
        $html  = "<script>x='https://toko.co.id\"><img src=x onerror=1>'</script> https://toko.co.idx";
        $akhir = 'echo ' . var_export( $html, true ) . '; while ( ob_get_level() > 0 ) { ob_end_flush(); }';
        $keluar = $this->jalankan( $this->konstanta( 'vps-toko.staging.contoh.id' ), $akhir );
        $this->assertSame( explode( "\n", strtr( $html, array(
            'https://toko.co.id' => 'https://vps-toko.staging.contoh.id' ) ) ), $keluar );
    }

    // ---- fix round 1: wp_mail pluggable (I2), jenis konten dan skema (M4) ----------

    public function test_wp_mail_pluggable_didefinisikan_dan_menolak(): void {
        // Plugin SMTP yang mengganti wp_mail() pluggable melewati kait
        // pre_wp_mail/phpmailer_init; mu-plugin dimuat lebih dulu, jadi
        // definisinya yang menang dan tidak ada email yang terkirim.
        $keluar = $this->jalankan( $this->konstanta( 'toko.co.id' ),
            "echo json_encode( array( function_exists( 'wp_mail' ), "
            . "wp_mail( 'a@contoh.id', 'subjek', 'isi', array(), array() ), wp_mail( 'a@contoh.id', 's', 'i' ) ) );" );
        $this->assertSame( array( '[true,false,false]' ), $keluar );
    }

    public function test_wp_mail_tidak_didefinisikan_tanpa_konstanta(): void {
        $keluar = $this->jalankan( "\$_SERVER['HTTP_HOST'] = 'toko.co.id';",
            "echo json_encode( function_exists( 'wp_mail' ) );" );
        $this->assertSame( array( 'false' ), $keluar );
    }

    public function test_wp_mail_yang_sudah_ada_tidak_didefinisikan_ulang(): void {
        // Definisi ganda adalah fatal error; kait cadangan tetap terpasang.
        $keluar = $this->jalankan( "function wp_mail() { return 'lama'; }\n" . $this->konstanta( 'toko.co.id' ),
            "\$k = wpmgr_kait( 'pre_wp_mail' ); echo json_encode( array( wp_mail(), \$k[0]( null, array() ) ) );" );
        $this->assertSame( array( '["lama",false]' ), $keluar );
    }

    public function test_url_http_dan_tanpa_skema_ikut_diganti(): void {
        $html  = 'a http://toko.co.id/x b //www.toko.co.id/y c http:\\/\\/www.toko.co.id\\/z d \\/\\/toko.co.id\\/w '
            . 'e mailto:info@toko.co.id f https://lain.id/toko.co.id';
        $akhir = 'echo ' . var_export( $html, true ) . '; while ( ob_get_level() > 0 ) { ob_end_flush(); }';
        $this->assertSame(
            array( 'a https://vps-toko.staging.contoh.id/x b //vps-toko.staging.contoh.id/y '
                . 'c https:\\/\\/vps-toko.staging.contoh.id\\/z d \\/\\/vps-toko.staging.contoh.id\\/w '
                . 'e mailto:info@toko.co.id f https://lain.id/toko.co.id' ),
            $this->jalankan( $this->konstanta( 'vps-toko.staging.contoh.id' ), $akhir )
        );
    }

    /**
     * Satu request lewat server bawaan PHP (SAPI cli-server): header
     * Content-Type hanya tercatat di SAPI web, tidak di CLI.
     */
    private function layani( string $tubuh ): string {
        $dir = sys_get_temp_dir() . '/wpmgr-layani-' . getmypid() . '-' . mt_rand();
        mkdir( $dir );
        $awal = "define( 'WPMGR_PRATINJAU', true );\n"
            . "define( 'WPMGR_PRATINJAU_HOST', 'vps-toko.staging.contoh.id' );\n"
            . "define( 'WPMGR_DOMAIN', 'toko.co.id' );";
        file_put_contents( $dir . '/index.php', $this->skrip( $awal, $tubuh ) );
        $hasil = null;
        for ( $coba = 0; $coba < 5 && null === $hasil; $coba++ ) {
            $port   = mt_rand( 20000, 45000 );
            $log    = $dir . '/server-' . $coba . '.log';
            $proses = proc_open( array( PHP_BINARY, '-S', '127.0.0.1:' . $port, '-t', $dir ),
                array( 0 => array( 'pipe', 'r' ), 1 => array( 'file', $log, 'a' ), 2 => array( 'file', $log, 'a' ) ),
                $pipa );
            $siap = false;
            for ( $i = 0; $i < 50 && ! $siap; $i++ ) {
                $soket = @fsockopen( '127.0.0.1', $port, $errno, $errstr, 0.2 );
                if ( $soket ) {
                    fclose( $soket );
                    $siap = true;
                } elseif ( ! proc_get_status( $proses )['running'] ) {
                    break;
                } else {
                    usleep( 100000 );
                }
            }
            if ( $siap ) {
                $konteks = stream_context_create( array( 'http' => array(
                    'header' => "Host: vps-toko.staging.contoh.id\r\nConnection: close\r\n", 'ignore_errors' => true,
                    'timeout' => 10 ) ) );
                $badan = file_get_contents( 'http://127.0.0.1:' . $port . '/index.php', false, $konteks );
                $hasil = false === $badan ? '' : $badan;
            }
            fclose( $pipa[0] );
            proc_terminate( $proses );
            proc_close( $proses );
        }
        array_map( 'unlink', glob( $dir . '/*' ) );
        rmdir( $dir );
        $this->assertNotNull( $hasil, 'server bawaan PHP tidak dapat dijalankan' );
        return $hasil;
    }

    public function test_penggantian_hanya_untuk_html_dan_json(): void {
        $isi = 'x https://toko.co.id/a';
        $ganti = 'x https://vps-toko.staging.contoh.id/a';
        foreach ( array(
            '' => $ganti,  // tanpa header: default_mimetype text/html
            'text/html; charset=UTF-8' => $ganti,
            'application/json; charset=UTF-8' => $ganti,
            'application/ld+json' => $ganti,
            'TEXT/HTML' => $ganti,
            'application/octet-stream' => $isi,
            'text/plain' => $isi,
            'image/svg+xml' => $isi,
            'text/css' => $isi,
        ) as $jenis => $harapan ) {
            $kepala = '' === $jenis ? '' : 'header( ' . var_export( 'Content-Type: ' . $jenis, true ) . ' ); ';
            $this->assertSame( $harapan, $this->layani( $kepala . 'echo ' . var_export( $isi, true ) . ';' ),
                $jenis );
        }
    }
}
