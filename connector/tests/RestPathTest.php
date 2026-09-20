<?php
use PHPUnit\Framework\TestCase;

final class RestPathTest extends TestCase {

    public function test_ping_menghasilkan_path_persis(): void {
        $this->assertSame(
            '/wp-json/wpmgr/v1/ping',
            WPMGR_REST::path_untuk_tanda_tangan( '/wpmgr/v1/ping' )
        );
    }

    public function test_inventory_menghasilkan_path_persis(): void {
        $this->assertSame(
            '/wp-json/wpmgr/v1/inventory',
            WPMGR_REST::path_untuk_tanda_tangan( '/wpmgr/v1/inventory' )
        );
    }

    public function test_update_menghasilkan_path_persis(): void {
        $this->assertSame(
            '/wp-json/wpmgr/v1/update',
            WPMGR_REST::path_untuk_tanda_tangan( '/wpmgr/v1/update' )
        );
    }

    public function test_namespace_hanya_muncul_sekali(): void {
        // Menjaga dari regresi duplikasi namespace yang coba ditambal brief
        // dengan str_replace() -- bila konstruksi path pernah diubah kembali
        // ke bentuk itu dan gagal, duplikasi akan muncul di sini.
        $hasil = WPMGR_REST::path_untuk_tanda_tangan( '/wpmgr/v1/ping' );
        $this->assertSame( 1, substr_count( $hasil, 'wpmgr/v1' ) );
    }

    public function test_tidak_ada_garis_miring_ganda(): void {
        $hasil = WPMGR_REST::path_untuk_tanda_tangan( '/wpmgr/v1/inventory' );
        $this->assertStringNotContainsString( '//', $hasil );
    }
}
