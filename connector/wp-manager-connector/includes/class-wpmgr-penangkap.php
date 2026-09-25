<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Menangkap fatal error, warning, dan error database, lalu menyimpannya per
 * sidik jari di tabel wpmgr_errors.
 *
 * Prinsipnya: penangkap tidak boleh merusak site. Selama request ia hanya
 * menampung di memori; penulisan terjadi sekali di akhir request. Semua jalur
 * dibungkus try/catch, dan error handler sebelumnya selalu dipanggil sehingga
 * perilaku PHP di site tidak berubah.
 */
class WPMGR_Penangkap {

    const BATAS_BARU_PER_REQUEST = 20;
    const BATAS_BARIS            = 500;
    const TINGKAT_WARNING        = array( E_WARNING, E_USER_WARNING, E_CORE_WARNING, E_COMPILE_WARNING );
    const TINGKAT_FATAL          = array( E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR, E_USER_ERROR, E_RECOVERABLE_ERROR );

    private static $buffer        = array();
    private static $sebelumnya    = null;
    private static $terpasang     = false;
    private static $sudah_ditulis = false;

    public static function pasang() {
        if ( self::$terpasang ) {
            return;
        }
        self::$terpasang = true;
        try {
            self::$sebelumnya = set_error_handler( array( __CLASS__, 'tangani_error' ) );
            register_shutdown_function( array( __CLASS__, 'saat_shutdown' ) );
            // Handler fatal WordPress dipasang sebelum mu-plugin mana pun
            // (wp-settings.php) dan berakhir di wp_die() -> die(). Exit di
            // dalam fungsi shutdown menghentikan fungsi shutdown berikutnya,
            // termasuk milik kita; filter ini dipanggil tepat sebelum wp_die().
            if ( function_exists( 'add_filter' ) ) {
                add_filter( 'wp_php_error_args', array( __CLASS__, 'dari_template_error' ), 10, 2 );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function tangani_error( $errno, $errstr, $errfile = '', $errline = 0 ) {
        try {
            // error_reporting() tidak memuat $errno untuk ekspresi yang
            // dibungkam dengan @ -- core sendiri memakainya di banyak tempat.
            if ( in_array( $errno, self::TINGKAT_WARNING, true ) && ( error_reporting() & $errno ) ) {
                self::catat( 'warning', (string) $errstr, (string) $errfile, (int) $errline );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
        if ( null !== self::$sebelumnya ) {
            return call_user_func( self::$sebelumnya, $errno, $errstr, $errfile, $errline );
        }
        return false;
    }

    public static function dari_template_error( $args, $error ) {
        try {
            self::catat_fatal( $error );
            self::tulis();
        } catch ( \Throwable $e ) {
            unset( $e );
        }
        return $args;
    }

    public static function saat_shutdown() {
        try {
            self::catat_fatal( error_get_last() );
            self::catat_database();
            self::tulis();
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function catat_fatal( $error ) {
        if ( is_array( $error ) && isset( $error['type'] ) && in_array( (int) $error['type'], self::TINGKAT_FATAL, true ) ) {
            self::catat( 'fatal',
                isset( $error['message'] ) ? (string) $error['message'] : '',
                isset( $error['file'] ) ? (string) $error['file'] : '',
                isset( $error['line'] ) ? (int) $error['line'] : 0 );
        }
    }

    private static function catat_database() {
        if ( isset( $GLOBALS['wpdb'] ) && is_object( $GLOBALS['wpdb'] ) && ! empty( $GLOBALS['wpdb']->last_error ) ) {
            self::catat( 'database', (string) $GLOBALS['wpdb']->last_error, '', 0 );
        }
    }

    private static function catat( $tingkat, $pesan, $file, $baris ) {
        $abspath = defined( 'ABSPATH' ) ? ABSPATH : '';
        $konten  = defined( 'WP_CONTENT_DIR' ) ? WP_CONTENT_DIR : $abspath . 'wp-content';
        self::tambah( self::$buffer, self::susun( $tingkat, $pesan, $file, $baris, $abspath, $konten ),
            self::BATAS_BARU_PER_REQUEST );
    }

    // ---- fungsi murni ---------------------------------------------------

    public static function potong( $teks, $panjang ) {
        return function_exists( 'mb_substr' ) ? mb_substr( $teks, 0, $panjang ) : substr( $teks, 0, $panjang );
    }

    private static function garis_miring( $path ) {
        return str_replace( '\\', '/', (string) $path );
    }

    public static function file_relatif( $file, $abspath ) {
        $f = self::garis_miring( $file );
        $a = rtrim( self::garis_miring( $abspath ), '/' ) . '/';
        if ( '/' !== $a && 0 === strpos( $f, $a ) ) {
            return substr( $f, strlen( $a ) );
        }
        return $f;
    }

    public static function normalisasi_pesan( $pesan, $abspath ) {
        $baris_pertama = strtok( (string) $pesan, "\n" );
        $baris_pertama = false === $baris_pertama ? '' : $baris_pertama;
        $baris_pertama = self::garis_miring( $baris_pertama );
        $a             = rtrim( self::garis_miring( $abspath ), '/' ) . '/';
        if ( '/' !== $a ) {
            $baris_pertama = str_replace( $a, '', $baris_pertama );
        }
        return preg_replace( '/\d+/', 'N', $baris_pertama );
    }

    public static function atribusi( $file, $content_dir, $abspath ) {
        $f = self::garis_miring( $file );
        if ( '' === $f ) {
            return array( 'lainnya', null );
        }
        $c = rtrim( self::garis_miring( $content_dir ), '/' ) . '/';
        $a = rtrim( self::garis_miring( $abspath ), '/' ) . '/';
        if ( 0 === strpos( $f, $c ) ) {
            $bagian = explode( '/', substr( $f, strlen( $c ) ) );
            if ( 'plugins' === $bagian[0] && isset( $bagian[1] ) ) {
                return array( 'plugin', $bagian[1] );
            }
            if ( 'mu-plugins' === $bagian[0] && isset( $bagian[1] ) ) {
                return array( 'mu-plugin', $bagian[1] );
            }
            if ( 'themes' === $bagian[0] && isset( $bagian[1] ) ) {
                return array( 'theme', $bagian[1] );
            }
            return array( 'lainnya', null );
        }
        if ( 0 === strpos( $f, $a . 'wp-includes/' ) || 0 === strpos( $f, $a . 'wp-admin/' ) ) {
            return array( 'core', null );
        }
        return array( 'lainnya', null );
    }

    public static function sidik_jari( $tingkat, $file_relatif, $baris, $pesan_norm ) {
        return md5( $tingkat . '|' . $file_relatif . '|' . (int) $baris . '|' . $pesan_norm );
    }

    public static function susun( $tingkat, $pesan, $file, $baris, $abspath, $content_dir ) {
        $pesan   = self::potong( (string) $pesan, 1000 );
        $relatif = self::file_relatif( $file, $abspath );
        list( $tipe, $slug ) = self::atribusi( $file, $content_dir, $abspath );
        return array(
            'sidik_jari'    => self::sidik_jari( $tingkat, $relatif, $baris, self::normalisasi_pesan( $pesan, $abspath ) ),
            'tingkat'       => $tingkat,
            'komponen_tipe' => $tipe,
            'komponen_slug' => $slug,
            'pesan'         => $pesan,
            'file'          => self::potong( $relatif, 255 ),
            'baris'         => (int) $baris,
            'jumlah'        => 1,
        );
    }

    public static function tambah( array &$buffer, array $kejadian, $batas_baru ) {
        $s = $kejadian['sidik_jari'];
        if ( isset( $buffer[ $s ] ) ) {
            $buffer[ $s ]['jumlah']++;
            return;
        }
        if ( count( $buffer ) >= $batas_baru ) {
            return;
        }
        $buffer[ $s ] = $kejadian;
    }

    // ---- penulisan ------------------------------------------------------

    private static function konteks() {
        $jenis = 'depan';
        if ( defined( 'WP_CLI' ) && WP_CLI ) {
            $jenis = 'cli';
        } elseif ( function_exists( 'wp_doing_cron' ) && wp_doing_cron() ) {
            $jenis = 'cron';
        } elseif ( function_exists( 'wp_doing_ajax' ) && wp_doing_ajax() ) {
            $jenis = 'ajax';
        } elseif ( defined( 'REST_REQUEST' ) && REST_REQUEST ) {
            $jenis = 'rest';
        } elseif ( function_exists( 'is_admin' ) && is_admin() ) {
            $jenis = 'admin';
        }
        $uri  = isset( $_SERVER['REQUEST_URI'] ) ? (string) $_SERVER['REQUEST_URI'] : '';
        // Tanpa query string: query bisa memuat token (reset password, SSO).
        $path = (string) parse_url( $uri, PHP_URL_PATH );
        return array( 'path' => self::potong( $path, 191 ), 'jenis' => $jenis );
    }

    private static function tulis() {
        if ( self::$sudah_ditulis || empty( self::$buffer ) || ! isset( $GLOBALS['wpdb'] ) ) {
            return;
        }
        if ( defined( 'WPMGR_DISABLE_MONITORING' ) && WPMGR_DISABLE_MONITORING ) {
            return;
        }
        self::$sudah_ditulis = true;

        $wpdb     = $GLOBALS['wpdb'];
        $tabel    = $wpdb->prefix . 'wpmgr_errors';
        $sekarang = time();
        $konteks  = json_encode( self::konteks() );
        $lama     = $wpdb->suppress_errors( true );
        try {
            $penuh = (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$tabel}" ) >= self::BATAS_BARIS; // phpcs:ignore WordPress.DB.PreparedSQL
            foreach ( self::$buffer as $sidik => $k ) {
                if ( $penuh ) {
                    // Setelah batas: hanya error yang sudah dikenal yang
                    // diperbarui; sidik baru menunggu pemangkasan harian.
                    $wpdb->query( $wpdb->prepare(
                        "UPDATE {$tabel} SET jumlah = jumlah + %d, terakhir = %d, diubah = %d WHERE sidik_jari = %s",
                        $k['jumlah'], $sekarang, $sekarang, $sidik
                    ) );
                    continue;
                }
                $wpdb->query( $wpdb->prepare(
                    "INSERT INTO {$tabel} (sidik_jari, tingkat, komponen_tipe, komponen_slug, pesan, file, baris, konteks, jumlah, pertama, terakhir, diubah)
                     VALUES (%s, %s, %s, %s, %s, %s, %d, %s, %d, %d, %d, %d)
                     ON DUPLICATE KEY UPDATE jumlah = jumlah + VALUES(jumlah), terakhir = VALUES(terakhir), diubah = VALUES(diubah), konteks = VALUES(konteks)",
                    $sidik, $k['tingkat'], $k['komponen_tipe'], (string) $k['komponen_slug'], $k['pesan'],
                    $k['file'], $k['baris'], $konteks, $k['jumlah'], $sekarang, $sekarang, $sekarang
                ) );
            }
        } finally {
            $wpdb->suppress_errors( $lama );
        }
    }

    // ---- kait test ------------------------------------------------------

    public static function reset_untuk_test( $sebelumnya = null ) {
        self::$buffer        = array();
        self::$sebelumnya    = $sebelumnya;
        self::$terpasang     = false;
        self::$sudah_ditulis = false;
    }

    public static function buffer_untuk_test() {
        return self::$buffer;
    }
}
