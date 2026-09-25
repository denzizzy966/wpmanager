<?php
use PHPUnit\Framework\TestCase;

// Stub minimal, dijaga function_exists/defined supaya tak bentrok dengan
// stub yang sama yang mungkin ditambahkan brief task lain. Hanya dipakai
// oleh jalur tulis WPMGR_Traffic::simpan_hit()/kumpulkan() di bawah --
// tersimpan di $GLOBALS supaya tearDown() bisa mengembalikannya ke keadaan
// kosong di antar-test.
if ( ! defined( 'DAY_IN_SECONDS' ) ) {
    define( 'DAY_IN_SECONDS', 86400 );
}
if ( ! function_exists( 'get_transient' ) ) {
    function get_transient( $kunci ) {
        return isset( $GLOBALS['wpmgr_test_transient'][ $kunci ] ) ? $GLOBALS['wpmgr_test_transient'][ $kunci ] : false;
    }
}
if ( ! function_exists( 'set_transient' ) ) {
    function set_transient( $kunci, $nilai, $ttl = 0 ) {
        // wp_options.option_value adalah kolom TEKS: get_transient() di
        // WordPress sungguhan TAK PERNAH mengembalikan bool/int PHP asli,
        // selalu string (ronde 2, temuan #2) -- meng-cast di sini juga,
        // bukan menyimpan $nilai mentah, supaya bug "true === get_transient(...)"
        // benar-benar tertangkap test, bukan kebetulan lolos karena stub
        // ini terlalu baik hati.
        $GLOBALS['wpmgr_test_transient'][ $kunci ] = (string) $nilai;
        return true;
    }
}
if ( ! function_exists( 'wp_timezone' ) ) {
    function wp_timezone() {
        return new DateTimeZone( 'UTC' );
    }
}
if ( ! function_exists( 'wp_timezone_string' ) ) {
    function wp_timezone_string() {
        return 'UTC';
    }
}

/**
 * wpdb tiruan untuk menguji WPMGR_Traffic::simpan_hit()/kumpulkan(): keadaan
 * dua tabel (wpmgr_pengunjung, wpmgr_traffic) disimpan sebagai array
 * asosiatif, dan MySQL affected-rows untuk INSERT ... ON DUPLICATE KEY
 * UPDATE ditiru persis (1 = baris baru disisipkan, 2 = baris sudah ada dan
 * diperbarui, 0 = nilainya sama persis) -- semantik itulah yang menentukan
 * "baru"-nya seorang pengunjung di simpan_hit() (lihat test_pengunjung_
 * tidak_dobel_hitung_saat_race). Pola sama seperti WPMGR_FakeWpdbPenangkap/
 * WPMGR_FakeWpdbLogin di test lain.
 */
final class WPMGR_FakeWpdbTraffic {
    public $prefix        = 'wp_';
    public $options       = 'wp_options';
    public $rows_affected = 0;
    public $queries       = array();

    // "tanggal|hash" => hit
    public $pengunjung = array();
    // "tanggal|dimensi|kunci" => array( kunjungan, pengunjung )
    public $traffic = array();
    // option_name => option_value -- dipakai menguji WPMGR_Traffic::garam().
    public $opsi = array();

    private $tersambung;
    /** @var callable|null dijalankan sekali, tepat setelah UPDATE pengunjung diproses. */
    private $suntik_setelah_update_pengunjung;
    /** @var callable|null dijalankan sekali, tepat sebelum INSERT IGNORE opsi garam diperiksa. */
    private $suntik_saat_insert_opsi;

    public function __construct(
        array $pengunjung = array(), array $traffic = array(), $tersambung = true,
        callable $suntik_setelah_update_pengunjung = null, callable $suntik_saat_insert_opsi = null
    ) {
        $this->pengunjung = $pengunjung;
        $this->traffic    = $traffic;
        $this->tersambung = $tersambung;
        $this->suntik_setelah_update_pengunjung = $suntik_setelah_update_pengunjung;
        $this->suntik_saat_insert_opsi          = $suntik_saat_insert_opsi;
    }

    public function check_connection( $allow_bail = true ) {
        return $this->tersambung;
    }

    public function suppress_errors( $suppress = true ) {
        return true;
    }

    public function esc_like( $teks ) {
        return addcslashes( (string) $teks, '_%\\' );
    }

    public function prepare( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        // wpdb::prepare() sungguhan menerima juga SATU array sebagai
        // argumen kedua (dipakai simpan_hit() untuk INSERT wpmgr_traffic
        // yang jumlah kolomnya berubah-ubah) -- ditiru di sini juga.
        if ( 1 === count( $args ) && is_array( $args[0] ) ) {
            $args = $args[0];
        }
        return array( 'sql' => $sql, 'args' => $args );
    }

    public function get_results( $disiapkan, $format = null ) {
        return array();
    }

    public function get_var( $disiapkan ) {
        // Dicatat juga di $this->queries: COUNT(*)/SELECT 1 (pengecekan cap)
        // lewat get_var(), bukan query() -- test yang memverifikasi "COUNT(*)
        // tak diulang" harus bisa melihatnya juga, bukan cuma INSERT/UPDATE.
        $this->queries[] = $disiapkan;
        $sql             = $disiapkan['sql'];
        $args            = $disiapkan['args'];

        if ( false !== strpos( $sql, 'SELECT option_value FROM' ) ) {
            // Ditiru seperti kolom teks sungguhan: null kalau baris tak ada,
            // string apa pun yang tersimpan (tak pernah tipe PHP mentah).
            return isset( $this->opsi[ $args[0] ] ) ? (string) $this->opsi[ $args[0] ] : null;
        }
        if ( false !== strpos( $sql, 'SELECT hit FROM' ) ) {
            $k = $args[0] . '|' . $args[1];
            return isset( $this->pengunjung[ $k ] ) ? $this->pengunjung[ $k ] : 0;
        }
        if ( false !== strpos( $sql, 'SELECT 1 FROM' ) ) {
            $k = $args[0] . '|' . $args[1] . '|' . $args[2];
            return isset( $this->traffic[ $k ] ) ? '1' : null;
        }
        if ( false !== strpos( $sql, 'COUNT(*)' ) && false !== strpos( $sql, 'wpmgr_pengunjung' ) ) {
            $tanggal = $args[0];
            $n       = 0;
            foreach ( array_keys( $this->pengunjung ) as $k ) {
                if ( 0 === strpos( $k, $tanggal . '|' ) ) {
                    $n++;
                }
            }
            return $n;
        }
        if ( false !== strpos( $sql, 'COUNT(*)' ) && false !== strpos( $sql, 'wpmgr_traffic' ) ) {
            $tanggal = $args[0];
            $dimensi = $args[1];
            // Argumen ke-3 (opsional) adalah pola LIKE "awalan%" dari
            // kunci_dengan_batas()'s $hanya_awalan, sudah lewat esc_like()
            // (addcslashes) -- di-stripslashes lagi di sini supaya strpos()
            // naif ini membandingkan ke prefiks ASLI, bukan versi ber-escape.
            $awalan = isset( $args[2] ) ? stripslashes( rtrim( $args[2], '%' ) ) : null;
            $n      = 0;
            foreach ( $this->traffic as $k => $v ) {
                $bagian = explode( '|', $k, 3 );
                if ( $bagian[0] === $tanggal && $bagian[1] === $dimensi ) {
                    if ( null !== $awalan && 0 !== strpos( $bagian[2], $awalan ) ) {
                        continue;
                    }
                    $n++;
                }
            }
            return $n;
        }
        return null;
    }

    public function query( $disiapkan ) {
        $this->queries[] = $disiapkan;
        $sql             = $disiapkan['sql'];
        $args            = $disiapkan['args'];

        if ( 0 === strpos( $sql, 'INSERT IGNORE' ) && false !== strpos( $sql, $this->options ) ) {
            list( $nama, $nilai ) = $args;
            if ( null !== $this->suntik_saat_insert_opsi ) {
                // Mensimulasikan request lain yang menang menyisipkan opsi
                // yang sama TEPAT sebelum INSERT IGNORE kita sendiri
                // diproses (jendela race yang sama seperti UPDATE-pengunjung
                // di atas, tapi untuk garam harian -- lihat WPMGR_Traffic::garam()).
                $cb = $this->suntik_saat_insert_opsi;
                $this->suntik_saat_insert_opsi = null; // sekali saja
                $cb( $this );
            }
            if ( isset( $this->opsi[ $nama ] ) ) {
                // UNIQUE KEY option_name: IGNORE menolak baris kedua diam-diam,
                // nilai yang sudah tersimpan TIDAK ditimpa (beda dari
                // add_option()'s ON DUPLICATE KEY UPDATE, yang justru menimpa).
                $this->rows_affected = 0;
            } else {
                $this->opsi[ $nama ]  = $nilai;
                $this->rows_affected  = 1;
            }
            return true;
        }

        if ( 0 === strpos( $sql, 'DELETE' ) && false !== strpos( $sql, $this->options ) ) {
            list( $awalan_like, $kecuali ) = $args;
            $awalan = stripslashes( rtrim( $awalan_like, '%' ) );
            foreach ( array_keys( $this->opsi ) as $nama ) {
                if ( 0 === strpos( $nama, $awalan ) && $nama !== $kecuali ) {
                    unset( $this->opsi[ $nama ] );
                }
            }
            return true;
        }

        // Dicek lewat AWALAN (0 === strpos), bukan sekadar "mengandung":
        // "INSERT ... ON DUPLICATE KEY UPDATE" JUGA mengandung substring
        // "UPDATE" -- kalau dicek dengan strpos() biasa, upsert pengunjung
        // di bawah salah terbaca sebagai UPDATE polos dan tak pernah
        // benar-benar menyisipkan apa pun ke $this->pengunjung.
        if ( 0 === strpos( $sql, 'UPDATE' ) && false !== strpos( $sql, 'wpmgr_pengunjung' ) ) {
            $k = $args[0] . '|' . $args[1];
            if ( isset( $this->pengunjung[ $k ] ) ) {
                $this->pengunjung[ $k ]++;
                $this->rows_affected = 1;
            } else {
                $this->rows_affected = 0;
            }
            if ( null !== $this->suntik_setelah_update_pengunjung ) {
                $cb = $this->suntik_setelah_update_pengunjung;
                $this->suntik_setelah_update_pengunjung = null; // sekali saja
                $cb( $this );
            }
            return true;
        }

        if ( 0 === strpos( $sql, 'INSERT' ) && false !== strpos( $sql, 'wpmgr_pengunjung' ) ) {
            $k = $args[0] . '|' . $args[1];
            if ( isset( $this->pengunjung[ $k ] ) ) {
                // Baris sudah ada (request lain menang menyisipkannya lebih
                // dulu): ON DUPLICATE KEY UPDATE meng-UPDATE, MySQL melapor 2.
                $this->pengunjung[ $k ]++;
                $this->rows_affected = 2;
            } else {
                $this->pengunjung[ $k ] = 1;
                $this->rows_affected    = 1; // baris benar-benar baru disisipkan.
            }
            return true;
        }

        if ( 0 === strpos( $sql, 'INSERT' ) && false !== strpos( $sql, 'wpmgr_traffic' ) ) {
            // Argumen berulang per 4: tanggal, dimensi, kunci, pengunjung(0/1).
            for ( $i = 0; $i + 3 < count( $args ); $i += 4 ) {
                $k = $args[ $i ] . '|' . $args[ $i + 1 ] . '|' . $args[ $i + 2 ];
                if ( ! isset( $this->traffic[ $k ] ) ) {
                    $this->traffic[ $k ] = array( 0, 0 );
                }
                $this->traffic[ $k ][0]++;
                $this->traffic[ $k ][1] += (int) $args[ $i + 3 ];
            }
            $this->rows_affected = 1;
            return true;
        }

        return true;
    }
}

final class TrafficTest extends TestCase {

    protected function tearDown(): void {
        unset( $GLOBALS['wpdb'] );
        $GLOBALS['wpmgr_test_transient'] = array();
    }

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

    // --- Pola tulis simpan_hit(): pengunjung unik, batas keras, konkurensi ---

    public function test_pengunjung_unik_berdasarkan_ip_dan_ua(): void {
        $wpdb            = new WPMGR_FakeWpdbTraffic();
        $GLOBALS['wpdb'] = $wpdb;
        $tanggal         = '2026-09-22';

        $hash_a = WPMGR_Traffic::hash_pengunjung( 'garam', '203.0.113.9', 'UA-X' );
        $hash_b = WPMGR_Traffic::hash_pengunjung( 'garam', '203.0.113.9', 'UA-Y' );

        // Dua hit dari IP+UA yang sama: satu pengunjung unik, dua kunjungan.
        WPMGR_Traffic::simpan_hit( $tanggal, $hash_a, '/a', 'UA-X', 'langsung:' );
        WPMGR_Traffic::simpan_hit( $tanggal, $hash_a, '/a', 'UA-X', 'langsung:' );
        // UA berbeda (hash berbeda): pengunjung unik kedua.
        WPMGR_Traffic::simpan_hit( $tanggal, $hash_b, '/a', 'UA-Y', 'langsung:' );

        list( $kunjungan, $pengunjung ) = $wpdb->traffic[ $tanggal . '|total|' ];
        $this->assertSame( 3, $kunjungan );
        $this->assertSame( 2, $pengunjung );
    }

    public function test_pengunjung_baru_ditentukan_dari_rows_affected_insert_bukan_diasumsikan(): void {
        // Mensimulasikan dua hit nyaris bersamaan dengan hash yang sama (dua
        // tab, reload cepat): UPDATE kita sendiri tidak menemukan barisnya
        // (belum ada sama sekali), tapi TEPAT setelah itu -- lewat callback
        // suntikan ini -- request "lain" menang menyisipkan baris yang sama
        // duluan. INSERT ... ON DUPLICATE KEY UPDATE milik kita sendiri lalu
        // menemukan baris itu SUDAH ADA dan meng-UPDATE-nya (MySQL melapor
        // rows_affected 2, bukan 1). Spec: unik = "baris benar-benar baru
        // disisipkan" -- jadi hit ini TIDAK boleh terhitung pengunjung baru.
        $tanggal = '2026-09-22';
        $hash    = 'hash-race';
        $wpdb    = new WPMGR_FakeWpdbTraffic( array(), array(), true, function ( WPMGR_FakeWpdbTraffic $w ) use ( $tanggal, $hash ) {
            $w->pengunjung[ $tanggal . '|' . $hash ] = 1; // request lain menang menyisipkan lebih dulu.
        } );
        $GLOBALS['wpdb'] = $wpdb;

        WPMGR_Traffic::simpan_hit( $tanggal, $hash, '/a', 'UA', 'langsung:' );

        list( $kunjungan, $pengunjung ) = $wpdb->traffic[ $tanggal . '|total|' ];
        $this->assertSame( 1, $kunjungan, 'kunjungan tetap dihitung' );
        $this->assertSame( 0, $pengunjung, 'BUKAN pengunjung baru: INSERT kita sendiri melapor rows_affected 2 (UPDATE), bukan 1 (INSERT)' );
    }

    public function test_batas_hit_per_pengunjung_menghentikan_tanpa_menulis_traffic(): void {
        $tanggal = '2026-09-22';
        $hash    = 'hash-lama';
        // Sudah di atas BATAS_HIT_PER_PENGUNJUNG (200): hit ke-201 pun masih
        // di-UPDATE (baris sudah ada), tapi begitu hit tercatat melebihi
        // batas, seluruh sisa penulisan (termasuk wpmgr_traffic) dihentikan.
        $wpdb            = new WPMGR_FakeWpdbTraffic( array( $tanggal . '|' . $hash => 201 ) );
        $GLOBALS['wpdb'] = $wpdb;

        WPMGR_Traffic::simpan_hit( $tanggal, $hash, '/a', 'UA', 'langsung:' );

        $this->assertSame( array(), $wpdb->traffic, 'wpmgr_traffic tak boleh ditulis sama sekali setelah batas per-pengunjung terlampaui' );
    }

    public function test_batas_pengunjung_penuh_tidak_mengulang_count(): void {
        $tanggal = '2026-09-22';
        $wpdb    = new WPMGR_FakeWpdbTraffic();
        $GLOBALS['wpdb'] = $wpdb;
        // Status "penuh" sudah ter-cache dari request sebelumnya hari ini.
        set_transient( 'wpmgr_penuh_pengunjung_' . $tanggal, true, DAY_IN_SECONDS );

        WPMGR_Traffic::simpan_hit( $tanggal, 'hash-baru', '/a', 'UA', 'langsung:' );

        foreach ( $wpdb->queries as $q ) {
            $hitung_pengunjung = false !== strpos( $q['sql'], 'COUNT(*)' ) && false !== strpos( $q['sql'], 'wpmgr_pengunjung' );
            $this->assertFalse( $hitung_pengunjung, 'COUNT(*) tak boleh diulang setelah status penuh ter-cache' );
        }
        $this->assertArrayNotHasKey( $tanggal . '|hash-baru', $wpdb->pengunjung, 'batas tercapai: tak ada baris pengunjung baru' );
        // Kunjungan tetap dihitung; hanya tak jadi pengunjung unik baru.
        $this->assertSame( array( 1, 0 ), $wpdb->traffic[ $tanggal . '|total|' ] );
    }

    public function test_batas_halaman_penuh_baris_lama_tetap_bertambah_baris_baru_ke_lainnya(): void {
        $tanggal = '2026-09-22';
        $wpdb    = new WPMGR_FakeWpdbTraffic( array(), array( $tanggal . '|halaman|/lama' => array( 3, 0 ) ) );
        $GLOBALS['wpdb'] = $wpdb;
        set_transient( 'wpmgr_penuh_halaman_' . $tanggal, true, DAY_IN_SECONDS );

        // Baris yang SUDAH ADA tetap diperbarui apa adanya, tak terkena batas.
        WPMGR_Traffic::simpan_hit( $tanggal, 'h1', '/lama', 'UA', 'langsung:' );
        $this->assertSame( 4, $wpdb->traffic[ $tanggal . '|halaman|/lama' ][0] );

        // Path BENAR-BENAR baru dialihkan ke "(lainnya)".
        WPMGR_Traffic::simpan_hit( $tanggal, 'h2', '/baru-sekali', 'UA2', 'langsung:' );
        $this->assertArrayNotHasKey( $tanggal . '|halaman|/baru-sekali', $wpdb->traffic );
        $this->assertArrayHasKey( $tanggal . '|halaman|(lainnya)', $wpdb->traffic );

        $hitung_halaman = array_filter( $wpdb->queries, function ( $q ) {
            return false !== strpos( $q['sql'], 'COUNT(*)' ) && false !== strpos( $q['sql'], 'wpmgr_traffic' )
                && isset( $q['args'][1] ) && 'halaman' === $q['args'][1];
        } );
        $this->assertCount( 0, $hitung_halaman, 'COUNT(*) tak boleh diulang setelah status penuh ter-cache' );
    }

    // --- garam(): insert-if-absent atomik, bukan add_option() (ronde 2) ---

    public function test_garam_pertama_kali_menyisipkan_dan_bertahan_di_panggilan_kedua(): void {
        $wpdb            = new WPMGR_FakeWpdbTraffic();
        $GLOBALS['wpdb'] = $wpdb;
        $tanggal         = '2026-09-22';

        $garam = WPMGR_Traffic::garam( $tanggal );
        $this->assertSame( 64, strlen( $garam ) );
        $this->assertTrue( ctype_xdigit( $garam ) );

        // Panggilan kedua di hari yang sama: jalur cepat (SELECT saja),
        // mengembalikan garam yang SAMA, bukan membuat yang baru.
        $this->assertSame( $garam, WPMGR_Traffic::garam( $tanggal ) );
    }

    public function test_garam_race_insert_ignore_kalah_pakai_nilai_pemenang(): void {
        // add_option() WordPress sendiri memakai INSERT ... ON DUPLICATE KEY
        // UPDATE (upsert) -- KEDUA racer akan "berhasil" dan yang terakhir
        // menulis menang secara diam-diam dan acak. INSERT IGNORE benar-benar
        // menolak baris kedua: di sini disimulasikan lewat callback yang
        // membuat request "lain" menang menyisipkan garamnya sendiri TEPAT
        // sebelum INSERT IGNORE kita berjalan -- baris yang tersimpan
        // ujung-ujungnya harus dibaca ulang dari tabel, bukan dipercaya dari
        // garam yang kita buat sendiri di memori.
        $tanggal = '2026-09-22';
        $nama    = 'wpmgr_garam_' . $tanggal;
        $wpdb    = new WPMGR_FakeWpdbTraffic( array(), array(), true, null, function ( WPMGR_FakeWpdbTraffic $w ) use ( $nama ) {
            $w->opsi[ $nama ] = 'garam-milik-pemenang';
        } );
        $GLOBALS['wpdb'] = $wpdb;

        $garam = WPMGR_Traffic::garam( $tanggal );

        $this->assertSame(
            'garam-milik-pemenang', $garam,
            'INSERT IGNORE kita kalah (0 rows_affected) -- nilai yang dipakai HARUS dibaca ulang dari tabel'
        );
    }

    public function test_garam_membersihkan_opsi_lama_hanya_saat_benar_benar_menyisipkan(): void {
        $tanggal         = '2026-09-22';
        $wpdb            = new WPMGR_FakeWpdbTraffic();
        $GLOBALS['wpdb'] = $wpdb;
        // Opsi lama dari hari-hari sebelumnya (bukan cuma "kemarin" --
        // site yang nol hit selama beberapa hari tetap harus dibersihkan).
        $wpdb->opsi['wpmgr_garam_2026-09-01'] = 'lama1';
        $wpdb->opsi['wpmgr_garam_2026-09-15'] = 'lama2';

        WPMGR_Traffic::garam( $tanggal );

        $this->assertArrayNotHasKey( 'wpmgr_garam_2026-09-01', $wpdb->opsi );
        $this->assertArrayNotHasKey( 'wpmgr_garam_2026-09-15', $wpdb->opsi );
        $this->assertArrayHasKey( 'wpmgr_garam_' . $tanggal, $wpdb->opsi );

        // Panggilan kedua di hari yang sama (jalur cepat, tak menyisipkan
        // apa pun): pembersihan tak boleh berjalan lagi.
        $jumlah_delete = function () use ( $wpdb ) {
            return count( array_filter( $wpdb->queries, function ( $q ) {
                return 0 === strpos( $q['sql'], 'DELETE' );
            } ) );
        };
        $sebelum = $jumlah_delete();
        WPMGR_Traffic::garam( $tanggal );
        $this->assertSame( $sebelum, $jumlah_delete(), 'pembersihan cuma boleh berjalan sekali, saat garam hari ini benar-benar baru dibuat' );
    }

    // --- batas_kunci_penuh(): status di-cache sebagai string, bukan bool (ronde 2) ---

    public function test_batas_penuh_terdeteksi_walau_get_transient_mengembalikan_string(): void {
        // wp_options.option_value adalah kolom TEKS: get_transient() di
        // WordPress sungguhan TAK PERNAH mengembalikan bool PHP asli, cuma
        // string '1' -- stub set_transient() di berkas ini meniru itu
        // (lihat atas). "true === get_transient(...)" gagal SELALU pada
        // kondisi ini; kode sekarang harus tetap mendeteksi status penuh.
        $tanggal = '2026-09-22';
        $wpdb    = new WPMGR_FakeWpdbTraffic();
        $GLOBALS['wpdb'] = $wpdb;
        set_transient( 'wpmgr_penuh_pengunjung_' . $tanggal, true, DAY_IN_SECONDS );
        $this->assertSame( '1', get_transient( 'wpmgr_penuh_pengunjung_' . $tanggal ), 'stub harus menyimpan sebagai string, bukan bool' );

        WPMGR_Traffic::simpan_hit( $tanggal, 'hash-baru', '/a', 'UA', 'langsung:' );

        foreach ( $wpdb->queries as $q ) {
            $hitung_pengunjung = false !== strpos( $q['sql'], 'COUNT(*)' ) && false !== strpos( $q['sql'], 'wpmgr_pengunjung' );
            $this->assertFalse( $hitung_pengunjung, 'status penuh (string "1") harus terdeteksi -- COUNT(*) tak boleh dijalankan' );
        }
        $this->assertArrayNotHasKey( $tanggal . '|hash-baru', $wpdb->pengunjung );
    }

    public function test_asal_site_lain_dibatasi_tapi_kategori_tetap_selalu_tercatat(): void {
        $tanggal = '2026-09-22';
        $traffic = array();
        for ( $i = 0; $i < 200; $i++ ) {
            $traffic[ $tanggal . '|asal|site_lain:s' . $i . '.test' ] = array( 1, 0 );
        }
        $wpdb            = new WPMGR_FakeWpdbTraffic( array(), $traffic );
        $GLOBALS['wpdb'] = $wpdb;

        // Batas (200) sudah tercapai: domain site_lain yang benar-benar baru
        // dialihkan ke "(lainnya)", bukan menambah baris ke-201.
        WPMGR_Traffic::simpan_hit( $tanggal, 'h1', '/x', 'UA1', 'site_lain:penyerang.test' );
        $this->assertArrayNotHasKey( $tanggal . '|asal|site_lain:penyerang.test', $wpdb->traffic );
        $this->assertArrayHasKey( $tanggal . '|asal|(lainnya)', $wpdb->traffic );

        // Kategori TETAP (himpunan terbatas) tak pernah kena batas site_lain:
        // banjir site_lain palsu tak boleh menggeser pencarian:Google ke lainnya.
        WPMGR_Traffic::simpan_hit( $tanggal, 'h2', '/x', 'UA2', 'pencarian:Google' );
        $this->assertSame( array( 1, 0 ), $wpdb->traffic[ $tanggal . '|asal|pencarian:Google' ] );

        // Domain site_lain yang SUDAH ADA (salah satu dari 200 yang di-seed)
        // tetap bertambah normal, tak ikut kena batas.
        WPMGR_Traffic::simpan_hit( $tanggal, 'h3', '/x', 'UA3', 'site_lain:s5.test' );
        $this->assertSame( array( 2, 0 ), $wpdb->traffic[ $tanggal . '|asal|site_lain:s5.test' ] );
    }

    public function test_kumpulkan_membatasi_dari_ke_jendela_retensi(): void {
        $wpdb            = new WPMGR_FakeWpdbTraffic();
        $GLOBALS['wpdb'] = $wpdb;

        $hasil       = WPMGR_Traffic::kumpulkan( '0000-01-01' );
        $paling_awal = ( new DateTime( '-' . WPMGR_Skema::HARI_SIMPAN . ' days', wp_timezone() ) )->format( 'Y-m-d' );
        $this->assertSame( $paling_awal, $hasil['dari'] );
    }
}
