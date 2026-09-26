<?php
use PHPUnit\Framework\TestCase;

final class WPMGR_FakeWpdbTandaAir {
    public $prefix     = 'wp_';
    public $tabel      = array( 'wp_posts', 'wp_comments', 'wp_users' );
    public $kueri      = array();
    public $baris      = array();
    public $nilai      = array();
    // Tambahan (di luar brief) untuk menguji lesson Task 3-5 "periksa
    // last_error setelah SETIAP query": meniru wpdb sungguhan, yang
    // mengosongkan last_error di awal setiap query() baru dan mengisinya
    // hanya saat query itu gagal. $gagal_pola men-set last_error dan
    // mengembalikan null persis seperti get_var()/get_row() nyata saat
    // gagal, supaya null hasil GAGAL tidak bisa dibedakan dari null hasil
    // "0 baris" hanya dari nilai baliknya -- last_error-lah yang harus
    // diperiksa.
    public $last_error = '';
    public $gagal_pola = null;

    public function esc_like( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function prepare( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        $sql  = str_replace( array( '%s', '%d' ), array( "'%s'", '%d' ), $sql );
        return vsprintf( $sql, $args );
    }

    public function get_var( $sql ) {
        $this->kueri[] = $sql;
        if ( null !== $this->gagal_pola && false !== strpos( $sql, $this->gagal_pola ) ) {
            $this->last_error = 'galat tiruan pada get_var()';
            return null;
        }
        $this->last_error = '';
        if ( preg_match( "/^SHOW TABLES LIKE '(.*)'\z/", $sql, $m ) ) {
            $nama = stripslashes( $m[1] );
            return in_array( $nama, $this->tabel, true ) ? $nama : null;
        }
        foreach ( $this->nilai as $pola => $v ) {
            if ( false !== strpos( $sql, $pola ) ) {
                return $v;
            }
        }
        return '0';
    }

    public function get_row( $sql, $format = null ) {
        $this->kueri[] = $sql;
        if ( null !== $this->gagal_pola && false !== strpos( $sql, $this->gagal_pola ) ) {
            $this->last_error = 'galat tiruan pada get_row()';
            return null;
        }
        $this->last_error = '';
        foreach ( $this->baris as $pola => $v ) {
            if ( false !== strpos( $sql, $pola ) ) {
                return $v;
            }
        }
        return array( 'maks' => null, 'jumlah' => '0', 'diubah' => null );
    }
}

// Permintaan REST tiruan minimal, dipakai untuk menguji WPMGR_Staging::tanda_air()
// (callback REST) dan pendaftaran rute-nya -- sama seperti pola
// WPMGR_FakeRequestManifest (get_param) dan WPMGR_FakeRequestFile (get_header)
// digabung dalam satu berkas, karena keduanya dibutuhkan di sini.
final class WPMGR_FakeRequestTandaAir {
    private $params;

    public function __construct( array $params = array() ) {
        $this->params = $params;
    }

    public function get_param( $nama ) {
        return isset( $this->params[ $nama ] ) ? $this->params[ $nama ] : null;
    }

    public function get_header( $nama ) {
        return '';
    }
}

final class TandaAirTest extends TestCase {

    protected function tearDown(): void {
        // Dipakai test rute/guard di bawah -- dibersihkan supaya tidak
        // bocor ke berkas test lain yang berjalan di proses PHPUnit yang sama.
        $GLOBALS['wpmgr_test_opsi'] = array();
        $GLOBALS['wpmgr_test_rute'] = array();
    }

    private function wpdb() {
        $w        = new WPMGR_FakeWpdbTandaAir();
        $w->baris = array(
            'FROM wp_posts WHERE post_type NOT IN' => array( 'maks' => '120', 'jumlah' => '80', 'diubah' => '2026-09-26 01:02:03' ),
            'FROM wp_comments'                     => array( 'maks' => '55', 'jumlah' => '40' ),
            'FROM wp_users'                        => array( 'maks' => '3', 'jumlah' => '3' ),
        );
        return $w;
    }

    public function test_sumber_inti_dan_nilai_bulat(): void {
        $h = WPMGR_Staging_TandaAir::kumpulkan( $this->wpdb(), '', 0 );
        $this->assertSame( array( 'maks_id' => 120, 'jumlah' => 80, 'diubah' => '2026-09-26 01:02:03', 'diubah_sejak' => null ),
            $h['sumber']['posts'] );
        $this->assertSame( array( 'maks_id' => 55, 'jumlah' => 40 ), $h['sumber']['comments'] );
        $this->assertSame( array( 'maks_id' => 3, 'jumlah' => 3 ), $h['sumber']['users'] );
        foreach ( array( 'pesanan', 'gravity_forms', 'wpforms', 'fluent_forms', 'flamingo' ) as $tidak ) {
            $this->assertArrayNotHasKey( $tidak, $h['sumber'] );
        }
        $this->assertIsInt( $h['diambil'] );
    }

    public function test_pesanan_hpos_didahulukan(): void {
        $w          = $this->wpdb();
        $w->tabel[] = 'wp_wc_orders';
        $w->tabel[] = 'wp_woocommerce_order_items';
        $w->baris['FROM wp_wc_orders'] = array( 'maks' => '900', 'jumlah' => '850' );
        $h = WPMGR_Staging_TandaAir::kumpulkan( $w, '', 0 );
        $this->assertSame( array( 'maks_id' => 900, 'jumlah' => 850, 'sumber' => 'hpos' ), $h['sumber']['pesanan'] );
    }

    public function test_pesanan_lama_dari_posts(): void {
        $w          = $this->wpdb();
        $w->tabel[] = 'wp_woocommerce_order_items';
        $w->baris["post_type IN ('shop_order')"] = array( 'maks' => '77', 'jumlah' => '12' );
        $h = WPMGR_Staging_TandaAir::kumpulkan( $w, '', 0 );
        $this->assertSame( array( 'maks_id' => 77, 'jumlah' => 12, 'sumber' => 'posts' ), $h['sumber']['pesanan'] );
    }

    public function test_tabel_form_yang_ada(): void {
        $w = $this->wpdb();
        array_push( $w->tabel, 'wp_gf_entry', 'wp_wpforms_entries', 'wp_fluentform_submissions' );
        $w->baris['MAX(`id`) AS maks, COUNT(*) AS jumlah FROM wp_gf_entry']             = array( 'maks' => '5', 'jumlah' => '5' );
        $w->baris['MAX(`entry_id`) AS maks, COUNT(*) AS jumlah FROM wp_wpforms_entries'] = array( 'maks' => '9', 'jumlah' => '8' );
        $w->baris['FROM wp_fluentform_submissions']                                     = array( 'maks' => '2', 'jumlah' => '2' );
        $h = WPMGR_Staging_TandaAir::kumpulkan( $w, '', 0 );
        $this->assertSame( array( 'maks_id' => 5, 'jumlah' => 5 ), $h['sumber']['gravity_forms'] );
        $this->assertSame( array( 'maks_id' => 9, 'jumlah' => 8 ), $h['sumber']['wpforms'] );
        $this->assertSame( array( 'maks_id' => 2, 'jumlah' => 2 ), $h['sumber']['fluent_forms'] );
    }

    public function test_flamingo_hanya_bila_ada_isi(): void {
        $w = $this->wpdb();
        $w->baris["post_type = 'flamingo_inbound'"] = array( 'maks' => '40', 'jumlah' => '6' );
        $this->assertSame( array( 'maks_id' => 40, 'jumlah' => 6 ),
            WPMGR_Staging_TandaAir::kumpulkan( $w, '', 0 )['sumber']['flamingo'] );
        $this->assertArrayNotHasKey( 'flamingo', WPMGR_Staging_TandaAir::kumpulkan( $this->wpdb(), '', 0 )['sumber'] );
    }

    public function test_posts_diubah_sejak(): void {
        $w        = $this->wpdb();
        $w->nilai = array( "post_modified_gmt > '2026-09-20 00:00:00'" => '4' );
        $h = WPMGR_Staging_TandaAir::kumpulkan( $w, '2026-09-20 00:00:00', 100 );
        $this->assertSame( 4, $h['sumber']['posts']['diubah_sejak'] );
        $semua = implode( "\n", $w->kueri );
        $this->assertStringContainsString( 'ID <= 100', $semua );
    }

    public function test_posts_sejak_tidak_sah_diabaikan(): void {
        foreach ( array( "2026-09-20' OR '1'='1", '2026-09-20', '2026-09-20 00:00:00x' ) as $sejak ) {
            $w = $this->wpdb();
            $h = WPMGR_Staging_TandaAir::kumpulkan( $w, $sejak, 100 );
            $this->assertNull( $h['sumber']['posts']['diubah_sejak'] );
            $this->assertStringNotContainsString( 'post_modified_gmt >', implode( "\n", $w->kueri ) );
        }
    }

    // ---- Tambahan di luar brief: lesson Task 3-5, "jangan pernah melaporkan
    // 'tidak ada data baru' yang palsu" -- galat query harus menghasilkan
    // 500 WP_Error, tidak pernah watermark sebagian yang tampak lengkap. ----

    public function test_galat_query_posts_menghasilkan_500_bukan_nol(): void {
        $w             = $this->wpdb();
        $w->gagal_pola = 'FROM wp_posts WHERE post_type NOT IN';
        $h             = WPMGR_Staging_TandaAir::kumpulkan( $w, '', 0 );
        $this->assertInstanceOf( WP_Error::class, $h );
        $this->assertSame( 'wpmgr_staging_tanda_air', $h->get_error_code() );
        $this->assertSame( array( 'status' => 500 ), $h->get_error_data() );
    }

    /**
     * Regresi kunci: bila implementasi hanya memeriksa last_error SEKALI di
     * akhir (bukan segera sesudah SETIAP query), galat pada query comments
     * (di tengah urutan) akan "tertutupi" oleh query users yang sukses
     * setelahnya (yang mengosongkan kembali last_error) -- hasilnya
     * tampak sukses dengan jumlah comments yang salah (0), persis mode
     * kegagalan "no new data palsu" yang dilarang.
     */
    public function test_galat_query_di_tengah_tidak_disembunyikan_query_berikutnya(): void {
        $w             = $this->wpdb();
        $w->gagal_pola = 'FROM wp_comments';
        $h             = WPMGR_Staging_TandaAir::kumpulkan( $w, '', 0 );
        $this->assertInstanceOf( WP_Error::class, $h );
    }

    public function test_galat_query_diubah_sejak_menghasilkan_500(): void {
        $w             = $this->wpdb();
        $w->gagal_pola = 'post_modified_gmt >';
        $h             = WPMGR_Staging_TandaAir::kumpulkan( $w, '2026-09-20 00:00:00', 100 );
        $this->assertInstanceOf( WP_Error::class, $h );
    }

    public function test_galat_query_deteksi_tabel_pesanan_menghasilkan_500(): void {
        // ada_tabel() yang gagal mengembalikan null (!== nama tabel), yang
        // secara naif tampak seperti "tabel tidak ada" -- padahal itu galat.
        // wc_orders ADA di daftar tabel, tapi SHOW TABLES-nya disimulasikan
        // gagal; hasilnya wajib 500, bukan diam-diam jatuh ke jalur "posts"
        // seolah situs ini tidak memakai HPOS.
        $w             = $this->wpdb();
        $w->tabel[]    = 'wp_wc_orders';
        $w->gagal_pola = 'SHOW TABLES';
        $h             = WPMGR_Staging_TandaAir::kumpulkan( $w, '', 0 );
        $this->assertInstanceOf( WP_Error::class, $h );
    }

    // ---- Setiap route baru punya test akses anonim (401). ----

    public function test_tanda_air_terdaftar_dengan_guard_dan_menolak_tanpa_tanda_tangan(): void {
        $GLOBALS['wpmgr_test_rute'] = array();
        WPMGR_Staging::daftarkan_route();
        $this->assertArrayHasKey( 'wpmgr/v1/staging/tanda-air', $GLOBALS['wpmgr_test_rute'] );
        $r = $GLOBALS['wpmgr_test_rute']['wpmgr/v1/staging/tanda-air'];
        $this->assertSame( 'GET', $r['methods'] );
        $this->assertSame( array( 'WPMGR_Staging', 'tanda_air' ), $r['callback'] );
        $this->assertSame( array( 'WPMGR_Staging', 'guard' ), $r['permission_callback'] );

        // Site "terpasang" (site_id/secret ada) supaya penolakan yang diuji
        // sungguh berasal dari ketiadaan tanda tangan, bukan dari
        // "connector belum dipasangkan".
        $GLOBALS['wpmgr_test_opsi']['wpmgr_site_id'] = 'situs-uji';
        $GLOBALS['wpmgr_test_opsi']['wpmgr_secret']  = str_repeat( 'a', 64 );
        $hasil = call_user_func( $r['permission_callback'], new WPMGR_FakeRequestTandaAir() );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( array( 'status' => 401 ), $hasil->get_error_data() );
    }

    // ---- posts_sejak array (query string '?posts_sejak[]=x') lewat callback
    // REST sungguhan tidak boleh memicu galat PHP "Array to string
    // conversion" -- hanya membuat diubah_sejak tetap null. ----

    public function test_tanda_air_callback_posts_sejak_array_tidak_memicu_galat(): void {
        global $wpdb;
        $wpdb  = $this->wpdb();
        $req   = new WPMGR_FakeRequestTandaAir( array( 'posts_sejak' => array( 'x' ), 'posts_maks' => 100 ) );
        $hasil = WPMGR_Staging::tanda_air( $req );
        $this->assertNotInstanceOf( WP_Error::class, $hasil );
        $data = $hasil->get_data();
        $this->assertNull( $data['sumber']['posts']['diubah_sejak'] );
    }

    public function test_tanda_air_callback_meneruskan_galat_query_sebagai_wp_error(): void {
        global $wpdb;
        $wpdb             = $this->wpdb();
        $wpdb->gagal_pola = 'FROM wp_posts WHERE post_type NOT IN';
        $req              = new WPMGR_FakeRequestTandaAir();
        $hasil            = WPMGR_Staging::tanda_air( $req );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( array( 'status' => 500 ), $hasil->get_error_data() );
    }
}
