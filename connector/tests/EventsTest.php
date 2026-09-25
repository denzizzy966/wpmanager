<?php
use PHPUnit\Framework\TestCase;

final class EventsTest extends TestCase {

    public function test_kursor_kosong_mulai_dari_nol(): void {
        $this->assertSame(
            array( 'e' => array( 0, 0 ), 'l' => array( 0, 0 ), 'g' => array( 0, 0 ) ),
            WPMGR_Events::urai_kursor( '' )
        );
    }

    public function test_kursor_bolak_balik(): void {
        $posisi = array( 'e' => array( 1790000000, 12 ), 'l' => array( 5, 0 ), 'g' => array( 0, 0 ) );
        $this->assertSame( $posisi, WPMGR_Events::urai_kursor( WPMGR_Events::susun_kursor( $posisi ) ) );
        $this->assertSame( 'e=1790000000:12;l=5:0;g=0:0', WPMGR_Events::susun_kursor( $posisi ) );
    }

    public function test_bagian_kursor_rusak_diabaikan(): void {
        $posisi = WPMGR_Events::urai_kursor( "e=10:2;l=x:y;g=-1:3;z=1:1;e2=1:1" );
        $this->assertSame( array( 10, 2 ), $posisi['e'] );
        $this->assertSame( array( 0, 0 ), $posisi['l'] );
        $this->assertSame( array( 0, 0 ), $posisi['g'] );
    }

    public function test_halaman_penuh_melanjutkan_persis_dari_baris_terakhir(): void {
        $baris = array( array( 'diubah' => 100, 'id' => 7 ), array( 'diubah' => 100, 'id' => 9 ) );
        $this->assertSame( array( 100, 9 ), WPMGR_Events::posisi_berikut( array( 0, 0 ), $baris, 2 ) );
    }

    public function test_halaman_tidak_penuh_mundur_dua_detik(): void {
        $baris = array( array( 'diubah' => 100, 'id' => 7 ), array( 'diubah' => 104, 'id' => 3 ) );
        $this->assertSame( array( 102, 0 ), WPMGR_Events::posisi_berikut( array( 0, 0 ), $baris, 500 ) );
    }

    public function test_tanpa_baris_mundur_dua_detik_dari_posisi_lama(): void {
        $this->assertSame( array( 98, 0 ), WPMGR_Events::posisi_berikut( array( 100, 5 ), array(), 500 ) );
        $this->assertSame( array( 0, 0 ), WPMGR_Events::posisi_berikut( array( 1, 0 ), array(), 500 ) );
    }

    public function test_batas_dijepit(): void {
        $this->assertSame( 500, WPMGR_Events::batas( null ) );
        $this->assertSame( 500, WPMGR_Events::batas( 0 ) );
        $this->assertSame( 500, WPMGR_Events::batas( 10000 ) );
        $this->assertSame( 50, WPMGR_Events::batas( '50' ) );
    }

    public function test_bentuk_error(): void {
        $b = WPMGR_Events::bentuk_error( array(
            'id' => '3', 'sidik_jari' => str_repeat( 'a', 32 ), 'tingkat' => 'fatal',
            'komponen_tipe' => 'core', 'komponen_slug' => '', 'pesan' => 'x', 'file' => '',
            'baris' => null, 'konteks' => '{"path":"/","jenis":"depan"}', 'jumlah' => '4',
            'pertama' => '10', 'terakhir' => '20', 'diubah' => '20',
        ) );
        $this->assertNull( $b['komponen_slug'] );
        $this->assertNull( $b['file'] );
        $this->assertNull( $b['baris'] );
        $this->assertSame( array( 'path' => '/', 'jenis' => 'depan' ), $b['konteks'] );
        $this->assertSame( 4, $b['jumlah'] );
        $this->assertSame( 20, $b['terakhir'] );
        $this->assertArrayNotHasKey( 'diubah', $b );
    }

    public function test_bentuk_login_dan_gagal(): void {
        $l = WPMGR_Events::bentuk_login( array(
            'id' => '9', 'waktu' => '100', 'jenis' => 'berhasil', 'username' => 'admin', 'role' => 'administrator',
            'ip' => '203.0.113.9', 'lewat_cloudflare' => '1', 'user_agent' => 'UA', 'jalur' => 'form', 'diubah' => '100',
        ) );
        $this->assertSame( 9, $l['id'] );
        $this->assertTrue( $l['lewat_cloudflare'] );
        $g = WPMGR_Events::bentuk_gagal( array(
            'id' => '1', 'jam' => '3600', 'ip' => '', 'username' => '(lainnya)', 'jalur' => 'form',
            'jumlah' => '12', 'user_agent' => null, 'diubah' => '3700',
        ) );
        $this->assertSame( array( 'jam' => 3600, 'ip' => '', 'username' => '(lainnya)', 'jalur' => 'form',
                                  'jumlah' => 12, 'user_agent' => null ), $g );
    }
}
