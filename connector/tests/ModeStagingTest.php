<?php
use PHPUnit\Framework\TestCase;

final class ModeStagingTest extends TestCase {

    private function template() {
        return file_get_contents( __DIR__ . '/../wp-manager-connector/templates/wpmgr-staging.php.tpl' );
    }

    public function test_monitoring_mati_di_staging(): void {
        $this->assertFalse( WPMGR_Skema::monitoring_mati_dari( false, false ) );
        $this->assertTrue( WPMGR_Skema::monitoring_mati_dari( true, false ) );
        $this->assertTrue( WPMGR_Skema::monitoring_mati_dari( false, true ) );
        // Connector staging: pemantauan mati dan fitur staging tidak diumumkan.
        $this->assertSame( array( 'self_update' ), WPMGR_Skema::fitur( WPMGR_Skema::monitoring_mati_dari( false, true ), false ) );
    }

    public function test_template_punya_semua_pengaman(): void {
        $isi = $this->template();
        $this->assertStringStartsWith( '<?php', $isi );
        $this->assertSame( 1, substr_count( $isi, '__WPMGR_NAMA__' ) );
        foreach ( array( "defined( 'WPMGR_STAGING' )", "'wpmgr-stg-mail'", '1025', 'X-Tags',
                         'phpmailer_init', 'pre_option_blog_public', 'admin_notices', 'STAGING',
                         'kredensial produksi', '/wpmgr-log/diubah', 'upgrader_process_complete', 'save_post' ) as $harus ) {
            $this->assertStringContainsString( $harus, $isi, $harus );
        }
    }

    public function test_template_valid_php_setelah_diisi(): void {
        $isi    = str_replace( '__WPMGR_NAMA__', 'contoh-id', $this->template() );
        $berkas = sys_get_temp_dir() . '/wpmgr-tpl-' . getmypid() . '.php';
        file_put_contents( $berkas, $isi );
        exec( escapeshellarg( PHP_BINARY ) . ' -l ' . escapeshellarg( $berkas ) . ' 2>&1', $keluar, $kode );
        unlink( $berkas );
        $this->assertSame( 0, $kode, implode( "\n", $keluar ) );
    }

    /**
     * mu-plugin pemuat penangkap error (wpmgr-penangkap.php, Lapis 2) bisa
     * ikut tersalin ke staging apa adanya lewat proses tarik/buat staging
     * (bukan ditulis ulang oleh connector staging sendiri, yang sudah diam
     * lewat monitoring_mati()). Isinya sendiri harus tetap diam bila
     * WPMGR_STAGING aktif, supaya salinan basi seperti itu tidak diam-diam
     * menghidupkan kembali penangkap error di staging. Dijalankan sungguhan
     * di proses PHP terpisah (bukan sekadar dicocokkan sebagai string) --
     * bila guard staging tidak ada di baris paling awal, get_option() yang
     * tidak terdefinisi di lingkungan telanjang ini akan membuatnya fatal.
     */
    public function test_mu_plugin_penangkap_diam_di_staging(): void {
        $muat   = sys_get_temp_dir() . '/wpmgr-penangkap-' . getmypid() . '.php';
        $skrip  = sys_get_temp_dir() . '/wpmgr-penangkap-jalan-' . getmypid() . '.php';
        file_put_contents( $muat, WPMGR_Skema::isi_mu_plugin() );
        file_put_contents( $skrip, "<?php\ndefine('WPMGR_STAGING', true);\ninclude "
            . var_export( $muat, true ) . ";\necho 'AMAN';\n" );
        exec( escapeshellarg( PHP_BINARY ) . ' ' . escapeshellarg( $skrip ) . ' 2>&1', $keluar, $kode );
        unlink( $muat );
        unlink( $skrip );
        $this->assertSame( 0, $kode, implode( "\n", $keluar ) );
        $this->assertSame( array( 'AMAN' ), $keluar );
    }
}
