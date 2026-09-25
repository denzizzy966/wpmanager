<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Penghitung kunjungan: script beacon di footer, endpoint /hit publik, dan
 * rangkuman harian untuk /traffic.
 *
 * Dihitung di browser karena halaman yang dilayani plugin cache tidak
 * menjalankan PHP. Tanpa cookie; IP tidak disimpan. Pengunjung unik memakai
 * hash IP+UA bergaram harian yang dibuang setelah hari itu.
 *
 * /hit publik dan tak bertanda tangan: path, referer, dan user-agent yang
 * masuk lewat sini adalah masukan penyerang. Path dan referer selalu lewat
 * urai_body() (json_decode) lebih dulu -- JSON wajib UTF-8 valid, jadi
 * keduanya tak pernah memuat byte tak valid begitu lolos dari sana. User-
 * agent mentah TIDAK lewat json_decode (ia header HTTP biasa), tapi di sini
 * hanya pernah disaring lewat regex byte-safe atau di-hash (sha1) -- tak
 * pernah disimpan mentah ke kolom manapun, jadi tak butuh scrub sendiri.
 */
class WPMGR_Traffic {

    const BATAS_HIT_PER_PENGUNJUNG  = 200;
    const BATAS_PATH_PER_HARI       = 1000;
    // Jumlah domain perujuk berbeda yang wajar untuk site kecil/menengah
    // jauh lebih sedikit daripada jumlah halaman -- 200 kombinasi baru per
    // hari sudah longgar untuk lalu lintas sah, sekaligus membatasi
    // pertumbuhan tabel saat Referer dipalsukan berulang-ulang.
    const BATAS_ASAL_PER_HARI       = 200;
    // Batas keras jumlah baris pengunjung unik (hash) baru per hari: tanpa
    // ini, membanjiri /hit dengan User-Agent yang selalu berbeda (bagian
    // hash yang sepenuhnya dikuasai penyerang; IP datang dari
    // WPMGR_IP::saat_ini(), bukan header yang bisa dipalsukan bebas)
    // membuat wpmgr_pengunjung tumbuh tanpa batas dalam satu hari yang sama.
    const BATAS_PENGUNJUNG_PER_HARI = 50000;
    const PATH_LAIN                 = '(lainnya)';
    const ASAL_LAIN                 = '(lainnya)';
    const MAKS_BODY                 = 2048;
    const PANJANG_KUNCI             = 180;

    const PENCARIAN = array(
        'Google' => array( 'google.' ), 'Bing' => array( 'bing.com' ), 'Yahoo' => array( 'yahoo.' ),
        'DuckDuckGo' => array( 'duckduckgo.com' ), 'Yandex' => array( 'yandex.' ),
        'Baidu' => array( 'baidu.com' ), 'Ecosia' => array( 'ecosia.org' ),
    );
    const SOSIAL = array(
        'Facebook' => array( 'facebook.com', 'fb.com' ), 'Instagram' => array( 'instagram.com' ),
        'X' => array( 'twitter.com', 'x.com', 't.co' ), 'LinkedIn' => array( 'linkedin.com', 'lnkd.in' ),
        'TikTok' => array( 'tiktok.com' ), 'YouTube' => array( 'youtube.com', 'youtu.be' ),
        'Pinterest' => array( 'pinterest.' ), 'WhatsApp' => array( 'whatsapp.com', 'wa.me' ),
        'Telegram' => array( 'telegram.org', 't.me' ), 'Threads' => array( 'threads.net' ),
    );

    // ---- fungsi murni ---------------------------------------------------

    /**
     * Memotong string ke PANJANG_KUNCI KARAKTER (bukan byte) untuk kolom
     * `kunci` (varchar(180)). substr() biasa memotong per byte dan bisa
     * memenggal tepat di tengah karakter multibyte, menyisakan ekor byte tak
     * valid UTF-8 -- wpdb::query() menolak DIAM-DIAM seluruh query yang
     * memuat byte semacam itu (lihat WPMGR_Login::potong()). mb_scrub()
     * dipertahankan sebagai lapis pertahanan kedua meski path/asal di sini
     * sudah lewat gerbang json_decode yang mewajibkan UTF-8 valid.
     */
    private static function potong_kunci( $teks ) {
        $teks = (string) $teks;
        $teks = function_exists( 'mb_scrub' ) ? mb_scrub( $teks, 'UTF-8' ) : wp_check_invalid_utf8( $teks, true );
        return function_exists( 'mb_substr' ) ? mb_substr( $teks, 0, self::PANJANG_KUNCI ) : substr( $teks, 0, self::PANJANG_KUNCI );
    }

    public static function urai_body( $body ) {
        if ( ! is_string( $body ) || strlen( $body ) > self::MAKS_BODY ) {
            return null;
        }
        $d = json_decode( $body, true );
        if ( ! is_array( $d ) || ! isset( $d['p'] ) || ! is_string( $d['p'] ) ) {
            return null;
        }
        return array( $d['p'], ( isset( $d['r'] ) && is_string( $d['r'] ) ) ? $d['r'] : '' );
    }

    public static function normalisasi_path( $p ) {
        $p = preg_replace( '/[?#].*$/s', '', (string) $p );
        $p = preg_replace( '#/{2,}#', '/', $p );
        if ( '' === $p || '/' !== $p[0] ) {
            return null;
        }
        return self::potong_kunci( $p );
    }

    public static function host_bersih( $host ) {
        return preg_replace( '/^(www\.|m\.)/', '', strtolower( trim( (string) $host ) ) );
    }

    private static function cocok_domain( $host, $pola ) {
        if ( '.' === substr( $pola, -1 ) ) {
            // "google." cocok dengan google.co.id dan news.google.com.
            return 0 === strpos( $host, $pola ) || false !== strpos( $host, '.' . $pola );
        }
        return $host === $pola || substr( $host, -strlen( '.' . $pola ) ) === '.' . $pola;
    }

    public static function kategori_asal( $referer, $host_site ) {
        $r    = trim( (string) $referer );
        $host = '' === $r ? null : parse_url( $r, PHP_URL_HOST );
        if ( ! is_string( $host ) || '' === $host ) {
            return 'langsung:';
        }
        $host = self::host_bersih( $host );
        if ( $host === self::host_bersih( $host_site ) ) {
            return null; // navigasi di dalam site sendiri bukan asal pengunjung
        }
        foreach ( array( 'pencarian' => self::PENCARIAN, 'sosial' => self::SOSIAL ) as $kategori => $daftar ) {
            foreach ( $daftar as $nama => $pola_semua ) {
                foreach ( $pola_semua as $pola ) {
                    if ( self::cocok_domain( $host, $pola ) ) {
                        return $kategori . ':' . $nama;
                    }
                }
            }
        }
        return self::potong_kunci( 'site_lain:' . $host );
    }

    public static function jenis_perangkat( $ua ) {
        $ua = (string) $ua;
        if ( preg_match( '/ipad|tablet|kindle|silk|playbook/i', $ua )
            || ( preg_match( '/android/i', $ua ) && ! preg_match( '/mobile/i', $ua ) ) ) {
            return 'tablet';
        }
        if ( preg_match( '/mobi|iphone|ipod|android|blackberry|opera mini|iemobile|windows phone/i', $ua ) ) {
            return 'mobile';
        }
        return 'desktop';
    }

    public static function adalah_bot( $ua ) {
        $ua = trim( (string) $ua );
        if ( '' === $ua ) {
            return true;
        }
        return (bool) preg_match(
            '/bot|crawl|spider|slurp|headless|lighthouse|phantomjs|preview|facebookexternalhit|embedly|curl|wget|python-|go-http|java\/|okhttp|monitor|uptime|wpmanager/i',
            $ua
        );
    }

    public static function hash_pengunjung( $garam, $ip, $ua ) {
        return sha1( $garam . '|' . $ip . '|' . $ua );
    }

    public static function urai_tanggal( $nilai ) {
        // Modifier D: tanpa itu "$" juga cocok tepat sebelum newline akhir,
        // meloloskan "2026-09-01\n" sebagai tanggal valid.
        return ( is_string( $nilai ) && preg_match( '/^\d{4}-\d{2}-\d{2}$/D', $nilai ) ) ? $nilai : null;
    }

    /**
     * Menentukan apakah sebuah kombinasi (path/asal/hash pengunjung) baru
     * boleh ditambahkan hari ini. Dipisah dari pemanggil ber-wpdb-nya supaya
     * batas keras bisa diuji tanpa database tiruan: kombinasi yang SUDAH ada
     * selalu boleh (ia hanya di-UPDATE, tak menambah baris baru); kombinasi
     * baru hanya boleh selama hitungan hari ini belum menyentuh batas.
     */
    public static function dalam_batas( $sudah_ada, $jumlah, $batas ) {
        return $sudah_ada || (int) $jumlah < (int) $batas;
    }

    public static function susun_hari( array $baris ) {
        $hari = array();
        foreach ( $baris as $b ) {
            $t = (string) $b['tanggal'];
            if ( ! isset( $hari[ $t ] ) ) {
                $hari[ $t ] = array(
                    'tanggal'   => $t,
                    'total'     => array( 'kunjungan' => 0, 'pengunjung' => 0 ),
                    'halaman'   => array(),
                    'asal'      => array(),
                    'perangkat' => array(),
                );
            }
            if ( 'total' === $b['dimensi'] ) {
                $hari[ $t ]['total'] = array( 'kunjungan' => (int) $b['kunjungan'], 'pengunjung' => (int) $b['pengunjung'] );
            } elseif ( in_array( $b['dimensi'], array( 'halaman', 'asal', 'perangkat' ), true ) ) {
                $hari[ $t ][ $b['dimensi'] ][ (string) $b['kunci'] ] = (int) $b['kunjungan'];
            }
        }
        ksort( $hari );
        return array_values( $hari );
    }

    // ---- WordPress --------------------------------------------------------

    public static function pasang() {
        add_action( 'wp_footer', array( __CLASS__, 'sisipkan_script' ), 100 );
    }

    public static function sisipkan_script() {
        try {
            if ( is_admin() || is_user_logged_in() || is_feed() || is_preview() || ! WPMGR_Settings::terpasang() ) {
                return;
            }
            $url = wp_json_encode( rest_url( 'wpmgr/v1/hit' ) );
            echo "<script>(function(){try{var d=JSON.stringify({p:location.pathname,r:document.referrer});"
                . "navigator.sendBeacon&&navigator.sendBeacon(" . $url . ",new Blob([d],{type:'text/plain'}));}catch(e){}})();</script>\n"; // phpcs:ignore WordPress.Security.EscapeOutput
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function tangani_hit( $request ) {
        try {
            self::catat_hit( $request->get_body() );
        } catch ( \Throwable $e ) {
            unset( $e );
        }
        // Selalu 204: pengirim tidak mendapat petunjuk apakah hit-nya dihitung.
        return new WP_REST_Response( null, 204 );
    }

    private static function garam( $tanggal ) {
        $simpan = get_option( 'wpmgr_garam' );
        if ( is_array( $simpan ) && isset( $simpan['tanggal'], $simpan['garam'] ) && $simpan['tanggal'] === $tanggal ) {
            return $simpan['garam'];
        }
        $garam = bin2hex( random_bytes( 32 ) );
        update_option( 'wpmgr_garam', array( 'tanggal' => $tanggal, 'garam' => $garam ), false );
        return $garam;
    }

    /**
     * Baris (dimensi, kunci) dengan batas keras: kombinasi yang sudah ada
     * hari ini selalu diperbarui apa adanya (tak menambah baris baru, jadi
     * tak perlu dibatasi); kombinasi yang benar-benar baru dialihkan ke
     * bucket $lainnya begitu jumlah kombinasi baru hari ini menyentuh
     * $batas. Dipakai untuk dimensi 'halaman' (path dikuasai penuh oleh
     * pengirim /hit) dan 'asal' (site_lain:<domain> dari Referer, juga
     * dikuasai penuh oleh pengirim).
     */
    private static function kunci_dengan_batas( $tanggal, $dimensi, $kunci, $batas, $lainnya ) {
        global $wpdb;
        $tabel = $wpdb->prefix . 'wpmgr_traffic';
        $ada   = $wpdb->get_var( $wpdb->prepare(
            "SELECT 1 FROM {$tabel} WHERE tanggal = %s AND dimensi = %s AND kunci = %s", $tanggal, $dimensi, $kunci ) );

        $kunci_cache = 'wpmgr_jml_' . $dimensi . '_' . $tanggal;
        $jumlah      = get_transient( $kunci_cache );
        if ( false === $jumlah ) {
            $jumlah = (int) $wpdb->get_var( $wpdb->prepare(
                "SELECT COUNT(*) FROM {$tabel} WHERE tanggal = %s AND dimensi = %s", $tanggal, $dimensi ) );
        }

        if ( ! self::dalam_batas( null !== $ada, (int) $jumlah, $batas ) ) {
            return $lainnya;
        }
        if ( null === $ada ) {
            set_transient( $kunci_cache, (int) $jumlah + 1, DAY_IN_SECONDS );
        }
        return $kunci;
    }

    /**
     * Batas keras jumlah baris pengunjung (hash) baru per hari. Sama seperti
     * kunci_dengan_batas(), tapi wpmgr_pengunjung tak punya dimensi -- hanya
     * satu hitungan per tanggal.
     */
    private static function pengunjung_baru_diizinkan( $tanggal ) {
        global $wpdb;
        $kunci_cache = 'wpmgr_jml_pengunjung_' . $tanggal;
        $jumlah      = get_transient( $kunci_cache );
        if ( false === $jumlah ) {
            $jumlah = (int) $wpdb->get_var( $wpdb->prepare(
                "SELECT COUNT(*) FROM {$wpdb->prefix}wpmgr_pengunjung WHERE tanggal = %s", $tanggal ) );
        }
        if ( ! self::dalam_batas( false, (int) $jumlah, self::BATAS_PENGUNJUNG_PER_HARI ) ) {
            return false;
        }
        set_transient( $kunci_cache, (int) $jumlah + 1, DAY_IN_SECONDS );
        return true;
    }

    private static function catat_hit( $body ) {
        if ( WPMGR_Skema::monitoring_mati() || ! WPMGR_Settings::terpasang() ) {
            return;
        }
        $isi = self::urai_body( $body );
        if ( null === $isi ) {
            return;
        }
        // Autentikasi cookie REST tanpa nonce selalu menganggap user = 0, jadi
        // cookie login diperiksa langsung.
        if ( wp_validate_auth_cookie( '', 'logged_in' ) ) {
            return;
        }
        $ua = isset( $_SERVER['HTTP_USER_AGENT'] ) ? (string) wp_unslash( $_SERVER['HTTP_USER_AGENT'] ) : '';
        if ( self::adalah_bot( $ua ) ) {
            return;
        }
        $path = self::normalisasi_path( $isi[0] );
        if ( null === $path ) {
            return;
        }

        global $wpdb;
        // Koneksi yang sudah putus membuat wpdb::bail() memanggil dead_db(),
        // yang berujung wp_die() -- ini dipanggil dari request pengunjung
        // biasa, bukan dari fungsi shutdown seperti WPMGR_Penangkap/Login,
        // tapi prinsipnya sama: diam saja, request berikutnya mencoba lagi.
        if ( ! $wpdb->check_connection( false ) ) {
            return;
        }

        $p       = $wpdb->prefix;
        $tanggal = wp_date( 'Y-m-d' );
        $ip      = WPMGR_IP::saat_ini();
        $hash    = self::hash_pengunjung( self::garam( $tanggal ), (string) $ip['ip'], $ua );
        $lama    = $wpdb->suppress_errors( true );
        try {
            $wpdb->query( $wpdb->prepare(
                "UPDATE {$p}wpmgr_pengunjung SET hit = hit + 1 WHERE tanggal = %s AND hash = %s", $tanggal, $hash ) );
            $baru = false;
            if ( $wpdb->rows_affected > 0 ) {
                $hit = (int) $wpdb->get_var( $wpdb->prepare(
                    "SELECT hit FROM {$p}wpmgr_pengunjung WHERE tanggal = %s AND hash = %s", $tanggal, $hash ) );
                if ( $hit > self::BATAS_HIT_PER_PENGUNJUNG ) {
                    return;
                }
            } elseif ( self::pengunjung_baru_diizinkan( $tanggal ) ) {
                // ON DUPLICATE KEY UPDATE, bukan INSERT polos: UPDATE barusan
                // tidak menemukan baris ini, tapi request lain dengan hash
                // yang sama (pengunjung memuat ulang halaman nyaris
                // bersamaan) bisa saja menang menyisipkannya lebih dulu --
                // upsert melebur ke baris yang menang, bukan gagal diam-diam
                // kena kunci unik (tanggal,hash).
                $wpdb->query( $wpdb->prepare(
                    "INSERT INTO {$p}wpmgr_pengunjung (tanggal, hash, hit) VALUES (%s, %s, 1)
                     ON DUPLICATE KEY UPDATE hit = hit + 1", $tanggal, $hash ) );
                $baru = true;
            }
            // Selain itu (tidak baru, tidak diizinkan): batas harian
            // wpmgr_pengunjung sudah tercapai. Kunjungan tetap dihitung di
            // bawah; hit ini hanya tak pernah tercatat sebagai baris
            // pengunjung unik baru.

            $kunci = array(
                array( 'total', '' ),
                array( 'halaman', self::kunci_dengan_batas( $tanggal, 'halaman', $path, self::BATAS_PATH_PER_HARI, self::PATH_LAIN ) ),
                array( 'perangkat', self::jenis_perangkat( $ua ) ),
            );
            $asal = self::kategori_asal( $isi[1], (string) wp_parse_url( home_url(), PHP_URL_HOST ) );
            if ( null !== $asal ) {
                $kunci[] = array( 'asal', self::kunci_dengan_batas( $tanggal, 'asal', $asal, self::BATAS_ASAL_PER_HARI, self::ASAL_LAIN ) );
            }
            $tempat = array();
            $arg    = array();
            foreach ( $kunci as $k ) {
                $tempat[] = '(%s, %s, %s, 1, %d)';
                array_push( $arg, $tanggal, $k[0], $k[1], ( 'total' === $k[0] && $baru ) ? 1 : 0 );
            }
            $wpdb->query( $wpdb->prepare(
                "INSERT INTO {$p}wpmgr_traffic (tanggal, dimensi, kunci, kunjungan, pengunjung) VALUES "
                . implode( ', ', $tempat )
                . " ON DUPLICATE KEY UPDATE kunjungan = kunjungan + 1, pengunjung = pengunjung + VALUES(pengunjung)",
                $arg
            ) );
        } finally {
            $wpdb->suppress_errors( $lama );
        }
    }

    public static function kumpulkan( $dari ) {
        global $wpdb;
        $dari = self::urai_tanggal( $dari );
        if ( null === $dari ) {
            $kemarin = new DateTime( 'yesterday', wp_timezone() );
            $dari    = $kemarin->format( 'Y-m-d' );
        }
        $baris = $wpdb->get_results( $wpdb->prepare(
            "SELECT tanggal, dimensi, kunci, kunjungan, pengunjung FROM {$wpdb->prefix}wpmgr_traffic
              WHERE tanggal >= %s ORDER BY tanggal", $dari ), ARRAY_A );
        return array(
            'zona_waktu' => wp_timezone_string(),
            'dari'       => $dari,
            'hari'       => self::susun_hari( is_array( $baris ) ? $baris : array() ),
        );
    }
}
