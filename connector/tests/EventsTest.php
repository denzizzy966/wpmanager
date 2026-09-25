<?php
use PHPUnit\Framework\TestCase;

/**
 * wpdb tiruan yang menerapkan WHERE/ORDER/LIMIT dari bentuk query TETAP
 * WPMGR_Events::kumpulkan() ke array di memori -- bukan mesin SQL umum,
 * cukup untuk bentuk query itu saja (lihat prepare()/get_results()).
 */
final class WPMGR_FakeWpdbEvents {
    public $prefix = 'wp_';

    /** @var array<string, array<int, array<string, mixed>>> nama tabel penuh => baris */
    private $tabel;

    public function __construct( array $tabel = array() ) {
        $this->tabel = $tabel;
    }

    public function prepare( $sql ) {
        return array( 'sql' => $sql, 'args' => array_slice( func_get_args(), 1 ) );
    }

    public function get_results( $disiapkan, $output = null ) {
        preg_match( '/FROM\s+(\S+)/', $disiapkan['sql'], $m );
        $baris = isset( $this->tabel[ $m[1] ] ) ? $this->tabel[ $m[1] ] : array();
        list( $t, , $id, $horizon, $batas ) = $disiapkan['args'];

        $cocok = array_values( array_filter( $baris, function ( $b ) use ( $t, $id, $horizon ) {
            if ( $b['diubah'] < $t || $b['diubah'] > $horizon ) {
                return false;
            }
            return $b['diubah'] > $t || $b['id'] > $id;
        } ) );
        usort( $cocok, function ( $a, $b ) {
            return $a['diubah'] <=> $b['diubah'] ?: $a['id'] <=> $b['id'];
        } );
        return array_slice( $cocok, 0, $batas );
    }

    /** Menyimulasikan sebuah UPDATE yang membumbui 'diubah' baris yang sudah ada. */
    public function ubah_baris( $tabel, $id, $diubah_baru ) {
        foreach ( $this->tabel[ $tabel ] as &$b ) {
            if ( $b['id'] === $id ) {
                $b['diubah'] = $diubah_baru;
            }
        }
    }
}

final class EventsTest extends TestCase {

    protected function tearDown(): void {
        unset( $GLOBALS['wpdb'] );
    }

    private function baris_login( $id, $diubah ) {
        return array(
            'id' => $id, 'waktu' => $diubah, 'jenis' => 'berhasil', 'username' => 'user' . $id,
            'role' => null, 'ip' => '203.0.113.1', 'lewat_cloudflare' => 0, 'user_agent' => null,
            'jalur' => 'form', 'diubah' => $diubah,
        );
    }

    private function baris_error( $id, $diubah ) {
        return array(
            'id' => $id, 'sidik_jari' => str_pad( (string) $id, 32, '0', STR_PAD_LEFT ), 'tingkat' => 'fatal',
            'komponen_tipe' => 'core', 'komponen_slug' => null, 'pesan' => 'pesan uji',
            'file' => null, 'baris' => null, 'konteks' => null, 'jumlah' => 1,
            'pertama' => $diubah, 'terakhir' => $diubah, 'diubah' => $diubah,
        );
    }

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

    public function test_kursor_bukan_skalar_diabaikan(): void {
        // ?kursor[]=x lolos ke sini sebagai array. Tanpa penjagaan, (string)
        // di urai_kursor() memicu peringatan "Array to string conversion".
        $this->assertSame(
            array( 'e' => array( 0, 0 ), 'l' => array( 0, 0 ), 'g' => array( 0, 0 ) ),
            WPMGR_Events::urai_kursor( array( 'x' ) )
        );
    }

    public function test_posisi_masa_depan_direset_karena_jam_mundur(): void {
        // Jam site mundur atau basis data dipulihkan dari cadangan lama:
        // posisi kursor yang "lebih maju" dari jam sekarang tidak masuk
        // akal dan akan membuat tabel itu terjebak tak pernah mengambil
        // apa pun. $sekarang disuntikkan supaya tes ini tidak bergantung
        // pada jam dinding sungguhan.
        $posisi = WPMGR_Events::urai_kursor( 'e=200:5;l=150:1', 100 );
        $this->assertSame( array( 0, 0 ), $posisi['e'] );
        // 150 masih di dalam toleransi 60 detik (100 + 60 = 160): dipakai apa adanya.
        $this->assertSame( array( 150, 1 ), $posisi['l'] );
    }

    public function test_posisi_lanjut_ke_baris_terakhir_saat_ada_baris(): void {
        $baris = array( array( 'diubah' => 100, 'id' => 7 ), array( 'diubah' => 100, 'id' => 9 ) );
        $this->assertSame( array( 100, 9 ), WPMGR_Events::posisi_berikut( array( 0, 0 ), $baris ) );
    }

    public function test_posisi_tetap_saat_tabel_kosong(): void {
        // Tabel tuntas (atau semua barisnya masih di dalam cakrawala): posisi
        // TIDAK mundur -- dicoba lagi persis dari titik yang sama nanti.
        $this->assertSame( array( 100, 5 ), WPMGR_Events::posisi_berikut( array( 100, 5 ), array() ) );
        $this->assertSame( array( 0, 0 ), WPMGR_Events::posisi_berikut( array( 0, 0 ), array() ) );
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

    public function test_kumpulkan_memaging_tanpa_lompat_atau_dobel_saat_banyak_baris_sedetik(): void {
        $lama  = time() - 20; // jauh di dalam cakrawala, aman diambil sekaligus.
        $baris = array();
        for ( $i = 1; $i <= 7; $i++ ) {
            $baris[] = $this->baris_login( $i, $lama );
        }
        $GLOBALS['wpdb'] = new WPMGR_FakeWpdbEvents( array( 'wp_wpmgr_logins' => $baris ) );

        $terkumpul = array();
        $kursor    = '';
        for ( $i = 0; $i < 10; $i++ ) { // jaga-jaga; harus tuntas jauh sebelum ini.
            $hasil = WPMGR_Events::kumpulkan( $kursor, 3 );
            foreach ( $hasil['logins'] as $l ) {
                $terkumpul[] = $l['id'];
            }
            $kursor = $hasil['kursor'];
            if ( ! $hasil['lagi'] ) {
                break;
            }
        }
        $this->assertSame( array( 1, 2, 3, 4, 5, 6, 7 ), $terkumpul );
    }

    public function test_kumpulkan_baris_yang_diperbarui_muncul_lagi_di_pengambilan_berikutnya(): void {
        $sekarang        = time();
        $wpdb            = new WPMGR_FakeWpdbEvents( array(
            'wp_wpmgr_logins' => array( $this->baris_login( 1, $sekarang - 20 ) ),
        ) );
        $GLOBALS['wpdb'] = $wpdb;

        $pertama = WPMGR_Events::kumpulkan( '', 10 );
        $this->assertSame( array( 1 ), array_column( $pertama['logins'], 'id' ) );

        // Menyimulasikan WPMGR_Login::tulis() mengubah baris yang sama lagi
        // (mis. login berulang): 'diubah' dibumbui maju ke detik baru, masih
        // di dalam cakrawala. Baris berhak muncul lagi di pengambilan
        // berikutnya supaya dashboard melihat kejadian barunya.
        $wpdb->ubah_baris( 'wp_wpmgr_logins', 1, $sekarang - 8 );

        $kedua = WPMGR_Events::kumpulkan( $pertama['kursor'], 10 );
        $this->assertSame( array( 1 ), array_column( $kedua['logins'], 'id' ) );
    }

    public function test_kumpulkan_tidak_mengembalikan_baris_yang_lebih_baru_dari_cakrawala(): void {
        $sekarang        = time();
        $GLOBALS['wpdb'] = new WPMGR_FakeWpdbEvents( array(
            'wp_wpmgr_logins' => array( $this->baris_login( 1, $sekarang ) ), // baru saja terjadi
        ) );
        $hasil = WPMGR_Events::kumpulkan( '', 10 );
        $this->assertSame( array(), $hasil['logins'] );
        // Belum maju: baris itu masih ditunda sampai lewat cakrawala.
        $this->assertSame( 'e=0:0;l=0:0;g=0:0', $hasil['kursor'] );
        $this->assertFalse( $hasil['lagi'] );
    }

    public function test_lagi_menyelesai_ke_false_setelah_tabel_tuntas(): void {
        $lama            = time() - 20;
        $GLOBALS['wpdb'] = new WPMGR_FakeWpdbEvents( array(
            'wp_wpmgr_errors' => array( $this->baris_error( 1, $lama ), $this->baris_error( 2, $lama ) ),
        ) );

        $pertama = WPMGR_Events::kumpulkan( '', 2 );
        $this->assertTrue( $pertama['lagi'] );
        $this->assertCount( 2, $pertama['errors'] );

        $kedua = WPMGR_Events::kumpulkan( $pertama['kursor'], 2 );
        $this->assertFalse( $kedua['lagi'] );
        $this->assertSame( array(), $kedua['errors'] );
    }

    public function test_kursor_tabel_kosong_tidak_berubah(): void {
        $GLOBALS['wpdb'] = new WPMGR_FakeWpdbEvents(); // ketiga tabel kosong
        $hasil           = WPMGR_Events::kumpulkan( 'e=500:9;l=0:0;g=0:0', 10 );
        $this->assertSame( 'e=500:9;l=0:0;g=0:0', $hasil['kursor'] );
        $this->assertFalse( $hasil['lagi'] );
    }
}
