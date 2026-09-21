<?php
use PHPUnit\Framework\TestCase;

/**
 * Keputusan murni di WPMGR_Updater yang menentukan apa yang dilaporkan ke
 * dashboard setelah (atau sebelum) upgrader WordPress bekerja. Semuanya
 * dipisah dari pemanggilan WordPress supaya dapat diuji tanpa WordPress.
 */
final class UpdaterKeputusanTest extends TestCase {

    // --- tidak_mencapai_target (R59-c) ---------------------------------

    public function test_versi_masih_di_bawah_target_berarti_tidak_mencapai(): void {
        // Upgrader melapor sukses tetapi versi di disk tidak bergerak: yang
        // dipasang WordPress bukan versi yang diminta dashboard.
        $this->assertTrue( WPMGR_Updater::tidak_mencapai_target( '1.6', '1.7.2', true ) );
    }

    public function test_versi_sama_dengan_target_berarti_tercapai(): void {
        $this->assertFalse( WPMGR_Updater::tidak_mencapai_target( '1.7.2', '1.7.2', true ) );
    }

    public function test_versi_melampaui_target_berarti_tercapai(): void {
        $this->assertFalse( WPMGR_Updater::tidak_mencapai_target( '1.8', '1.7.2', true ) );
    }

    public function test_versi_tak_terbaca_setelah_upgrade_yang_bekerja_tidak_dituduh_gagal(): void {
        // Mis. plugin mengganti nama berkas utamanya di versi baru. Upgrader
        // melaporkan sukses; kita tidak punya bukti sebaliknya.
        $this->assertFalse( WPMGR_Updater::tidak_mencapai_target( null, '1.7.2', true ) );
    }

    public function test_bulk_upgrade_true_dengan_versi_tetap_berarti_tidak_mencapai(): void {
        // bulk_upgrade() mengembalikan true untuk plugin yang tidak punya
        // penawaran di transient update_plugins -- tidak ada yang dipasang.
        $this->assertTrue( WPMGR_Updater::tidak_mencapai_target( '1.6', '1.7.2', false ) );
    }

    public function test_bulk_upgrade_true_tetapi_versi_sudah_di_target_berarti_tercapai(): void {
        // Proses lain (auto-update WordPress, admin di wp-admin) sempat
        // memasangnya lebih dulu. Kenyataan di disk yang menentukan.
        $this->assertFalse( WPMGR_Updater::tidak_mencapai_target( '1.7.2', '1.7.2', false ) );
    }

    public function test_bulk_upgrade_true_dengan_versi_tak_terbaca_tidak_boleh_mengaku_sukses(): void {
        // Tidak ada yang dipasang DAN tidak ada versi yang bisa dibaca:
        // melaporkan ke_versi sebagai versi_sesudah di sini adalah karangan.
        $this->assertTrue( WPMGR_Updater::tidak_mencapai_target( null, '1.7.2', false ) );
    }

    // --- hasil_bulk (R52) ----------------------------------------------

    public function test_bulk_false_seluruhnya_menjadi_false(): void {
        // fs_connect() gagal: bulk_upgrade() mengembalikan false untuk
        // seluruh panggilan, bukan array.
        $this->assertFalse( WPMGR_Updater::hasil_bulk( false, 'a/a.php' ) );
    }

    public function test_bulk_array_mengembalikan_hasil_untuk_slug(): void {
        $hasil = array( 'destination_name' => 'a' );
        $this->assertSame( $hasil, WPMGR_Updater::hasil_bulk( array( 'a/a.php' => $hasil ), 'a/a.php' ) );
    }

    public function test_bulk_true_dipertahankan_apa_adanya(): void {
        // true di sini berarti "tidak ada di transient", dan keputusan soal
        // itu milik tidak_mencapai_target(), bukan fungsi ini.
        $this->assertTrue( WPMGR_Updater::hasil_bulk( array( 'a/a.php' => true ), 'a/a.php' ) );
    }

    public function test_bulk_wp_error_dipertahankan(): void {
        $galat = new WP_Error( 'incompatible_php_required_version', 'PHP terlalu lama' );
        $this->assertSame( $galat, WPMGR_Updater::hasil_bulk( array( 'a/a.php' => $galat ), 'a/a.php' ) );
    }

    public function test_bulk_null_per_item_menjadi_false(): void {
        $this->assertFalse( WPMGR_Updater::hasil_bulk( array( 'a/a.php' => null ), 'a/a.php' ) );
    }

    public function test_bulk_tanpa_slug_yang_diminta_menjadi_false(): void {
        $this->assertFalse( WPMGR_Updater::hasil_bulk( array( 'b/b.php' => true ), 'a/a.php' ) );
    }

    // --- kunci_dipegang (R56) -------------------------------------------

    public function test_tanpa_option_lock_tidak_dipegang(): void {
        // get_option() mengembalikan false untuk option yang tidak ada.
        $this->assertFalse( WPMGR_Updater::kunci_dipegang( false, 1000000 ) );
    }

    public function test_lock_segar_dipegang(): void {
        $this->assertTrue( WPMGR_Updater::kunci_dipegang( '999990', 1000000 ) );
    }

    public function test_lock_tepat_di_batas_kedaluwarsa_tidak_dipegang(): void {
        // create_lock() memakai '>' (bukan '>='): lock setua DETIK_KUNCI
        // sudah boleh direbut.
        $sekarang = 1000000;
        $nilai    = (string) ( $sekarang - WPMGR_Updater::DETIK_KUNCI );
        $this->assertFalse( WPMGR_Updater::kunci_dipegang( $nilai, $sekarang ) );
    }

    public function test_lock_satu_detik_sebelum_kedaluwarsa_masih_dipegang(): void {
        $sekarang = 1000000;
        $nilai    = (string) ( $sekarang - WPMGR_Updater::DETIK_KUNCI + 1 );
        $this->assertTrue( WPMGR_Updater::kunci_dipegang( $nilai, $sekarang ) );
    }

    public function test_option_lock_kosong_dianggap_dipegang_seperti_create_lock(): void {
        // create_lock() menyerah bila baris lock ada tetapi nilainya falsy
        // ("If a lock couldn't be created, and there isn't a lock, bail").
        // /inventory harus sepakat dengan /update soal ini.
        $this->assertTrue( WPMGR_Updater::kunci_dipegang( '', 1000000 ) );
        $this->assertTrue( WPMGR_Updater::kunci_dipegang( '0', 1000000 ) );
    }

    public function test_durasi_lock_lima_belas_menit(): void {
        $this->assertSame( 15 * 60, WPMGR_Updater::DETIK_KUNCI );
    }

    public function test_galat_sibuk_adalah_409_wpmgr_sibuk(): void {
        $galat = WPMGR_Updater::galat_sibuk();
        $this->assertInstanceOf( WP_Error::class, $galat );
        $this->assertSame( 'wpmgr_sibuk', $galat->get_error_code() );
        $this->assertSame( array( 'status' => 409 ), $galat->get_error_data() );
    }

    // --- pilih_penawaran_core (R59-f) -----------------------------------

    private function penawaran( $current, $locale, $response = 'upgrade' ) {
        $o           = new stdClass();
        $o->current  = $current;
        $o->locale   = $locale;
        $o->response = $response;
        return $o;
    }

    public function test_penawaran_versi_sama_dari_locale_lain_dipakai(): void {
        $en = $this->penawaran( '6.6.1', 'en_US' );
        $this->assertSame(
            $en,
            WPMGR_Updater::pilih_penawaran_core( array( $this->penawaran( '6.6', 'en_US' ), $en ), '6.6.1' )
        );
    }

    public function test_penawaran_bukan_upgrade_tidak_dipakai(): void {
        // 'latest' berarti "sudah terbaru"; memasangnya bukan upgrade ke
        // versi yang diminta.
        $this->assertFalse(
            WPMGR_Updater::pilih_penawaran_core( array( $this->penawaran( '6.6.1', 'en_US', 'latest' ) ), '6.6.1' )
        );
    }

    public function test_tanpa_penawaran_versi_yang_diminta_hasilnya_false(): void {
        $this->assertFalse(
            WPMGR_Updater::pilih_penawaran_core( array( $this->penawaran( '6.7', 'en_US' ) ), '6.6.1' )
        );
    }

    public function test_get_core_updates_false_hasilnya_false(): void {
        // get_core_updates() mengembalikan false bila transient update_core
        // tidak berisi daftar penawaran sama sekali.
        $this->assertFalse( WPMGR_Updater::pilih_penawaran_core( false, '6.6.1' ) );
    }
}
