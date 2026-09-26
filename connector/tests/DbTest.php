<?php
use PHPUnit\Framework\TestCase;

/**
 * WPMGR_Staging_Db::prefix_asing()/tabel_milik_site() (fix I2, review
 * putaran 1): helper bersama dipakai WPMGR_Staging_Manifest::tabel(),
 * daftar tabel snapshot, jurnal tabel dorong, dan WPMGR_Staging_Sql::ubah().
 */
final class DbTest extends TestCase {

    public function test_prefix_asing_terdeteksi_lewat_tabel_opsi_sendiri(): void {
        $semua = array( 'wp_posts', 'wp_options', 'wp_abc_posts', 'wp_abc_options' );
        $this->assertSame( array( 'wp_abc_' ), WPMGR_Staging_Db::prefix_asing( $semua, 'wp_' ) );
    }

    public function test_prefix_asing_kosong_tanpa_tabel_opsi_sendiri(): void {
        // 'wp_abc_posts' sendirian, TANPA 'wp_abc_options' -- tidak cukup
        // bukti itu site lain (bisa saja sekadar tabel plugin site ini).
        $semua = array( 'wp_posts', 'wp_options', 'wp_abc_posts' );
        $this->assertSame( array(), WPMGR_Staging_Db::prefix_asing( $semua, 'wp_' ) );
    }

    public function test_tabel_milik_site_membuang_tabel_prefix_asing(): void {
        $semua  = array( 'wp_posts', 'wp_options', 'wp_abc_posts', 'wp_abc_options', 'lain_x' );
        $hasil  = WPMGR_Staging_Db::tabel_milik_site( $semua, 'wp_' );
        $this->assertSame( array( 'wp_posts', 'wp_options' ), $hasil );
    }

    public function test_tabel_milik_site_tanpa_tumpang_tindih_tidak_berubah(): void {
        $semua = array( 'wp_posts', 'wp_options', 'wp_woocommerce_x' );
        $this->assertSame( $semua, WPMGR_Staging_Db::tabel_milik_site( $semua, 'wp_' ) );
    }
}
