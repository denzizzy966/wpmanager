<?php
use PHPUnit\Framework\TestCase;

final class PenangkapTest extends TestCase {

    const AKAR    = '/var/www/html/';
    const KONTEN  = '/var/www/html/wp-content';

    protected function tearDown(): void {
        WPMGR_Penangkap::reset_untuk_test();
    }

    public function test_angka_dinormalkan_sehingga_error_memori_satu_sidik(): void {
        $a = WPMGR_Penangkap::normalisasi_pesan( 'Allowed memory size of 268435456 bytes exhausted (tried to allocate 20480 bytes)', self::AKAR );
        $b = WPMGR_Penangkap::normalisasi_pesan( 'Allowed memory size of 268435456 bytes exhausted (tried to allocate 40960 bytes)', self::AKAR );
        $this->assertSame( $a, $b );
    }

    public function test_hanya_baris_pertama_dan_path_relatif_yang_dipakai(): void {
        $pesan = "Uncaught Error: Call to undefined function x() in /var/www/html/wp-content/plugins/a/a.php:12\nStack trace:\n#0 {main}";
        $this->assertSame(
            'Uncaught Error: Call to undefined function x() in wp-content/plugins/a/a.php:N',
            WPMGR_Penangkap::normalisasi_pesan( $pesan, self::AKAR )
        );
    }

    public function test_file_relatif_juga_untuk_path_windows(): void {
        $this->assertSame( 'wp-content/plugins/a/a.php',
            WPMGR_Penangkap::file_relatif( 'C:\\laragon\\www\\situs\\wp-content\\plugins\\a\\a.php', 'C:\\laragon\\www\\situs\\' ) );
        $this->assertSame( '/tmp/x.php', WPMGR_Penangkap::file_relatif( '/tmp/x.php', self::AKAR ) );
    }

    /** @dataProvider kasus_atribusi */
    public function test_atribusi( $file, $harapan ): void {
        $this->assertSame( $harapan, WPMGR_Penangkap::atribusi( $file, self::KONTEN, self::AKAR ) );
    }

    public function kasus_atribusi(): array {
        return array(
            array( '/var/www/html/wp-content/plugins/elementor/core/base.php', array( 'plugin', 'elementor' ) ),
            array( '/var/www/html/wp-content/plugins/hello.php', array( 'plugin', 'hello.php' ) ),
            array( '/var/www/html/wp-content/mu-plugins/x.php', array( 'mu-plugin', 'x.php' ) ),
            array( '/var/www/html/wp-content/themes/astra/functions.php', array( 'theme', 'astra' ) ),
            array( '/var/www/html/wp-includes/class-wpdb.php', array( 'core', null ) ),
            array( '/var/www/html/wp-admin/includes/file.php', array( 'core', null ) ),
            array( '/tmp/lain.php', array( 'lainnya', null ) ),
            array( '', array( 'lainnya', null ) ),
        );
    }

    public function test_sidik_jari_stabil_dan_membedakan_tingkat_dan_baris(): void {
        $a = WPMGR_Penangkap::sidik_jari( 'warning', 'a.php', 10, 'pesan' );
        $this->assertSame( $a, WPMGR_Penangkap::sidik_jari( 'warning', 'a.php', 10, 'pesan' ) );
        $this->assertNotSame( $a, WPMGR_Penangkap::sidik_jari( 'fatal', 'a.php', 10, 'pesan' ) );
        $this->assertNotSame( $a, WPMGR_Penangkap::sidik_jari( 'warning', 'a.php', 11, 'pesan' ) );
        $this->assertSame( 32, strlen( $a ) );
    }

    public function test_buffer_menggabung_duplikat_dan_membatasi_sidik_baru(): void {
        $buffer = array();
        for ( $i = 0; $i < 25; $i++ ) {
            WPMGR_Penangkap::tambah( $buffer, WPMGR_Penangkap::susun( 'warning', "pesan $i", '/tmp/x.php', $i, self::AKAR, self::KONTEN ), 20 );
        }
        $this->assertCount( 20, $buffer );
        $pertama = WPMGR_Penangkap::susun( 'warning', 'pesan 0', '/tmp/x.php', 0, self::AKAR, self::KONTEN );
        WPMGR_Penangkap::tambah( $buffer, $pertama, 20 );
        $this->assertSame( 2, $buffer[ $pertama['sidik_jari'] ]['jumlah'] );
    }

    public function test_handler_sebelumnya_tetap_dipanggil_dan_nilainya_diteruskan(): void {
        $dipanggil = array();
        set_error_handler( function ( $no, $str ) use ( &$dipanggil ) {
            $dipanggil[] = array( $no, $str );
            return true;
        } );
        WPMGR_Penangkap::reset_untuk_test();
        WPMGR_Penangkap::pasang();
        try {
            trigger_error( 'warning uji', E_USER_WARNING );
            $this->assertSame( array( array( E_USER_WARNING, 'warning uji' ) ), $dipanggil );
            $this->assertCount( 1, WPMGR_Penangkap::buffer_untuk_test() );
            $this->assertTrue( WPMGR_Penangkap::tangani_error( E_USER_WARNING, 'langsung', __FILE__, __LINE__ ) );
        } finally {
            restore_error_handler();
            restore_error_handler();
        }
    }

    public function test_tanpa_handler_sebelumnya_mengembalikan_false(): void {
        WPMGR_Penangkap::reset_untuk_test( null );
        $this->assertFalse( WPMGR_Penangkap::tangani_error( E_USER_WARNING, 'x', __FILE__, __LINE__ ) );
    }

    public function test_notice_dan_warning_yang_dibungkam_tidak_dicatat(): void {
        set_error_handler( function () { return true; } );
        WPMGR_Penangkap::reset_untuk_test();
        WPMGR_Penangkap::pasang();
        try {
            trigger_error( 'notice', E_USER_NOTICE );
            @trigger_error( 'dibungkam', E_USER_WARNING );
            $this->assertCount( 0, WPMGR_Penangkap::buffer_untuk_test() );
        } finally {
            restore_error_handler();
            restore_error_handler();
        }
    }

    public function test_filter_template_mencatat_fatal_dan_mengembalikan_args_utuh(): void {
        WPMGR_Penangkap::reset_untuk_test( null );
        $args  = array( 'response' => 500 );
        $error = array( 'type' => E_ERROR, 'message' => 'fatal uji', 'file' => '/tmp/x.php', 'line' => 3 );
        $this->assertSame( $args, WPMGR_Penangkap::dari_template_error( $args, $error ) );
        $buffer = WPMGR_Penangkap::buffer_untuk_test();
        $this->assertCount( 1, $buffer );
        $this->assertSame( 'fatal', array_values( $buffer )[0]['tingkat'] );
    }

    public function test_error_non_fatal_dari_error_get_last_diabaikan_oleh_catat_fatal(): void {
        WPMGR_Penangkap::reset_untuk_test( null );
        WPMGR_Penangkap::catat_fatal( array( 'type' => E_WARNING, 'message' => 'w', 'file' => '', 'line' => 0 ) );
        WPMGR_Penangkap::catat_fatal( null );
        $this->assertCount( 0, WPMGR_Penangkap::buffer_untuk_test() );
    }
}
