<?php
use PHPUnit\Framework\TestCase;

final class TrafficTest extends TestCase {

    public function test_urai_body(): void {
        $this->assertSame( array( '/layanan', 'https://google.com/' ),
            WPMGR_Traffic::urai_body( '{"p":"/layanan","r":"https://google.com/"}' ) );
        $this->assertSame( array( '/', '' ), WPMGR_Traffic::urai_body( '{"p":"/"}' ) );
        $this->assertNull( WPMGR_Traffic::urai_body( 'bukan json' ) );
        $this->assertNull( WPMGR_Traffic::urai_body( '{"p":["/"]}' ) );
        $this->assertNull( WPMGR_Traffic::urai_body( '{"p":"/","x":"' . str_repeat( 'a', 3000 ) . '"}' ) );
        $this->assertNull( WPMGR_Traffic::urai_body( null ) );
        // Byte tak valid UTF-8 (mis. Referer yang dipalsukan lewat curl, bukan
        // browser) membuat seluruh dokumen JSON gagal diurai -- json_decode()
        // mewajibkan UTF-8 valid untuk KESELURUHAN input, bukan cuma per nilai
        // string. Ini gerbang yang membuat path/referer di sini tak pernah
        // butuh mb_scrub() sendiri (lihat WPMGR_Traffic::potong_kunci()).
        $this->assertNull( WPMGR_Traffic::urai_body( '{"p":"/","r":"' . "\xff" . '"}' ) );
    }

    public function test_normalisasi_path(): void {
        $this->assertSame( '/layanan/', WPMGR_Traffic::normalisasi_path( '/layanan/?utm_source=x#atas' ) );
        $this->assertSame( '/a/b', WPMGR_Traffic::normalisasi_path( '//a///b' ) );
        $this->assertNull( WPMGR_Traffic::normalisasi_path( 'https://evil.test/' ) );
        $this->assertNull( WPMGR_Traffic::normalisasi_path( '' ) );
        $this->assertSame( 180, strlen( WPMGR_Traffic::normalisasi_path( '/' . str_repeat( 'x', 400 ) ) ) );
    }

    public function test_normalisasi_path_memotong_per_karakter_bukan_per_byte(): void {
        // 'é' dua byte per karakter di UTF-8. substr() byte biasa memotong
        // persis di tengah salah satu karakter pada batas 180 byte, meninggal-
        // kan ekor byte tak valid UTF-8 -- wpdb::query() menolak diam-diam
        // seluruh query yang memuat byte semacam itu (lihat WPMGR_Login::
        // potong()). mb_substr() memotong per karakter dan tak pernah begitu.
        $path  = '/' . str_repeat( 'é', 200 );
        $hasil = WPMGR_Traffic::normalisasi_path( $path );
        $this->assertSame( 180, mb_strlen( $hasil, 'UTF-8' ) );
        $this->assertTrue( mb_check_encoding( $hasil, 'UTF-8' ) );
    }

    /** @dataProvider kasus_asal */
    public function test_kategori_asal( $referer, $harapan ): void {
        $this->assertSame( $harapan, WPMGR_Traffic::kategori_asal( $referer, 'www.cvmaju.id' ) );
    }

    public function kasus_asal(): array {
        return array(
            array( '', 'langsung:' ),
            array( 'bukan url', 'langsung:' ),
            array( 'https://cvmaju.id/tentang', null ),
            array( 'https://www.cvmaju.id/', null ),
            array( 'https://www.google.co.id/', 'pencarian:Google' ),
            array( 'https://news.google.com/', 'pencarian:Google' ),
            array( 'https://www.bing.com/search?q=x', 'pencarian:Bing' ),
            array( 'https://l.facebook.com/l.php?u=x', 'sosial:Facebook' ),
            array( 'https://m.facebook.com/', 'sosial:Facebook' ),
            array( 'https://t.co/abc', 'sosial:X' ),
            array( 'https://www.instagram.com/', 'sosial:Instagram' ),
            array( 'https://wa.me/62812', 'sosial:WhatsApp' ),
            array( 'https://direktori.example.com/cv', 'site_lain:direktori.example.com' ),
            array( 'https://notgoogle.com/', 'site_lain:notgoogle.com' ),
        );
    }

    public function test_jenis_perangkat(): void {
        $this->assertSame( 'mobile', WPMGR_Traffic::jenis_perangkat( 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Mobile/15E148' ) );
        $this->assertSame( 'mobile', WPMGR_Traffic::jenis_perangkat( 'Mozilla/5.0 (Linux; Android 14; SM-S918B) Mobile Safari/537.36' ) );
        $this->assertSame( 'tablet', WPMGR_Traffic::jenis_perangkat( 'Mozilla/5.0 (Linux; Android 13; SM-X700) Safari/537.36' ) );
        $this->assertSame( 'tablet', WPMGR_Traffic::jenis_perangkat( 'Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X)' ) );
        $this->assertSame( 'desktop', WPMGR_Traffic::jenis_perangkat( 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0' ) );
    }

    public function test_adalah_bot(): void {
        $this->assertTrue( WPMGR_Traffic::adalah_bot( '' ) );
        $this->assertTrue( WPMGR_Traffic::adalah_bot( 'Mozilla/5.0 (compatible; Googlebot/2.1)' ) );
        $this->assertTrue( WPMGR_Traffic::adalah_bot( 'Mozilla/5.0 HeadlessChrome/120.0' ) );
        $this->assertTrue( WPMGR_Traffic::adalah_bot( 'WPManager-Uptime/2.0' ) );
        $this->assertFalse( WPMGR_Traffic::adalah_bot( 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0' ) );
    }

    public function test_hash_pengunjung_bergantung_pada_garam(): void {
        $a = WPMGR_Traffic::hash_pengunjung( 'garam1', '203.0.113.9', 'UA' );
        $this->assertSame( $a, WPMGR_Traffic::hash_pengunjung( 'garam1', '203.0.113.9', 'UA' ) );
        $this->assertNotSame( $a, WPMGR_Traffic::hash_pengunjung( 'garam2', '203.0.113.9', 'UA' ) );
        $this->assertSame( 40, strlen( $a ) );
    }

    public function test_dalam_batas(): void {
        // Kombinasi yang sudah ada hari ini selalu boleh: ia hanya di-UPDATE,
        // tak pernah menambah baris baru, jadi tak ada yang perlu dibatasi.
        $this->assertTrue( WPMGR_Traffic::dalam_batas( true, 999999, 10 ) );
        // Kombinasi baru boleh selama hitungan hari ini masih di bawah batas.
        $this->assertTrue( WPMGR_Traffic::dalam_batas( false, 9, 10 ) );
        // Batas keras: begitu tercapai (atau terlewati), kombinasi baru ditolak.
        $this->assertFalse( WPMGR_Traffic::dalam_batas( false, 10, 10 ) );
        $this->assertFalse( WPMGR_Traffic::dalam_batas( false, 11, 10 ) );
    }

    public function test_susun_hari(): void {
        $baris = array(
            array( 'tanggal' => '2026-09-22', 'dimensi' => 'total', 'kunci' => '', 'kunjungan' => '5', 'pengunjung' => '3' ),
            array( 'tanggal' => '2026-09-21', 'dimensi' => 'halaman', 'kunci' => '/', 'kunjungan' => '2', 'pengunjung' => '0' ),
            array( 'tanggal' => '2026-09-22', 'dimensi' => 'asal', 'kunci' => 'langsung:', 'kunjungan' => '4', 'pengunjung' => '0' ),
            array( 'tanggal' => '2026-09-22', 'dimensi' => 'aneh', 'kunci' => 'x', 'kunjungan' => '9', 'pengunjung' => '0' ),
        );
        $hari = WPMGR_Traffic::susun_hari( $baris );
        $this->assertSame( '2026-09-21', $hari[0]['tanggal'] );
        $this->assertSame( array( 'kunjungan' => 0, 'pengunjung' => 0 ), $hari[0]['total'] );
        $this->assertSame( array( '/' => 2 ), $hari[0]['halaman'] );
        $this->assertSame( array( 'kunjungan' => 5, 'pengunjung' => 3 ), $hari[1]['total'] );
        $this->assertSame( array( 'langsung:' => 4 ), $hari[1]['asal'] );
    }

    public function test_urai_tanggal(): void {
        $this->assertSame( '2026-09-01', WPMGR_Traffic::urai_tanggal( '2026-09-01' ) );
        $this->assertNull( WPMGR_Traffic::urai_tanggal( '2026-9-1' ) );
        $this->assertNull( WPMGR_Traffic::urai_tanggal( "2026-09-01' OR 1=1" ) );
        $this->assertNull( WPMGR_Traffic::urai_tanggal( null ) );
        // Modifier D: "$" tanpa itu juga cocok tepat sebelum newline akhir.
        $this->assertNull( WPMGR_Traffic::urai_tanggal( "2026-09-01\n" ) );
    }
}
