<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Aturan path staging (spec §13): hanya path relatif di dalam root
 * WordPress, tanpa `..`, tanpa symlink keluar, dan `wp-config.php` tidak
 * pernah dibaca maupun ditulis. Dashboard menerapkan aturan yang sama
 * (wpmgr.staging.aman); keduanya harus tetap sama ketatnya.
 */
class WPMGR_Staging_Path {

    const MAKS_PANJANG  = 1024;
    const MAKS_SEGMEN   = 255;
    const BACKUP_KONTEN = array( 'updraft', 'ai1wm-backups', 'wpvividbackups' );
    const TIDAK_PERNAH_DITULIS = array(
        'wp-config.php', '.maintenance',
        'wp-content/mu-plugins/wpmgr-staging.php', 'wp-content/mu-plugins/wpmgr-dorong-aman.php',
    );
    const AKAR_INTI = array( 'index.php', 'xmlrpc.php', 'license.txt', 'readme.html', '.htaccess' );

    private static function tolak( $pesan ) {
        return new WP_Error( 'wpmgr_staging_path', $pesan, array( 'status' => 400 ) );
    }

    public static function normalisasi( $rel ) {
        if ( ! is_string( $rel ) || '' === $rel ) {
            return self::tolak( 'Path kosong.' );
        }
        if ( strlen( $rel ) > self::MAKS_PANJANG ) {
            return self::tolak( 'Path terlalu panjang.' );
        }
        if ( 1 !== preg_match( '//u', $rel ) ) {
            return self::tolak( 'Path bukan UTF-8 yang sah.' );
        }
        if ( preg_match( '/[\x00-\x1f\x7f\\\\]/', $rel ) ) {
            return self::tolak( 'Path memuat karakter terlarang.' );
        }
        if ( '/' === $rel[0] || preg_match( '/^[A-Za-z]:/', $rel ) ) {
            return self::tolak( 'Path absolut ditolak.' );
        }
        foreach ( explode( '/', $rel ) as $segmen ) {
            if ( '' === $segmen || '.' === $segmen || '..' === $segmen ) {
                return self::tolak( 'Path memuat segmen terlarang.' );
            }
            if ( strlen( $segmen ) > self::MAKS_SEGMEN ) {
                return self::tolak( 'Nama berkas terlalu panjang.' );
            }
        }
        return $rel;
    }

    /** Tidak pernah disalin ke staging maupun didorong (spec §6.2 langkah 1). */
    public static function dikecualikan( $rel ) {
        $rel = (string) $rel;
        if ( 'wp-config.php' === $rel || '.maintenance' === $rel ) {
            return true;
        }
        if ( '.log' === strtolower( substr( $rel, -4 ) ) ) {
            return true;
        }
        foreach ( array( 'wp-content/cache', 'wp-content/wpmgr-dorong' ) as $dir ) {
            if ( $rel === $dir || 0 === strpos( $rel, $dir . '/' ) ) {
                return true;
            }
        }
        $bagian = explode( '/', $rel );
        if ( count( $bagian ) >= 2 && 'wp-content' === $bagian[0] ) {
            if ( in_array( $bagian[1], self::BACKUP_KONTEN, true ) || 0 === strpos( $bagian[1], 'backups-dup-' ) ) {
                return true;
            }
        }
        return false;
    }

    /**
     * Penulisan di luar wp-content dibatasi pada berkas inti WordPress
     * (spec §13). Plugin connector sendiri tidak pernah ditimpa: menimpanya
     * di tengah request yang sedang ia layani adalah cara tercepat membuat
     * dorongan tidak bisa dipulihkan.
     */
    public static function boleh_ditulis( $rel ) {
        if ( is_wp_error( self::normalisasi( $rel ) ) || self::dikecualikan( $rel ) ) {
            return false;
        }
        if ( in_array( $rel, self::TIDAK_PERNAH_DITULIS, true ) ) {
            return false;
        }
        if ( 0 === strpos( $rel, 'wp-content/plugins/wp-manager-connector/' ) ) {
            return false;
        }
        foreach ( array( 'wp-content/', 'wp-admin/', 'wp-includes/' ) as $awalan ) {
            if ( 0 === strpos( $rel, $awalan ) ) {
                return true;
            }
        }
        if ( false !== strpos( $rel, '/' ) ) {
            return false;
        }
        return in_array( $rel, self::AKAR_INTI, true ) || 1 === preg_match( '/^wp-[a-z0-9-]+\.php\z/', $rel );
    }

    private static function garis( $p ) {
        return str_replace( '\\', '/', (string) $p );
    }

    /** Apakah $abs (yang harus ada) benar-benar berada di dalam $akar setelah symlink diurai. */
    public static function di_dalam( $akar, $abs ) {
        $akar_nyata = realpath( $akar );
        $nyata      = realpath( $abs );
        if ( false === $akar_nyata || false === $nyata ) {
            return false;
        }
        $akar_nyata = rtrim( self::garis( $akar_nyata ), '/' ) . '/';
        $nyata      = self::garis( $nyata );
        if ( is_dir( $nyata ) ) {
            $nyata = rtrim( $nyata, '/' ) . '/';
        }
        return 0 === strpos( $nyata, $akar_nyata );
    }

    public static function untuk_dibaca( $akar, $rel ) {
        $n = self::normalisasi( $rel );
        if ( is_wp_error( $n ) ) {
            return $n;
        }
        if ( self::dikecualikan( $rel ) ) {
            return self::tolak( 'Path dikecualikan dari staging.' );
        }
        $abs = $akar . $rel;
        if ( is_link( $abs ) ) {
            return self::tolak( 'Symlink tidak disalin.' );
        }
        if ( ! is_file( $abs ) ) {
            return new WP_Error( 'wpmgr_staging_tidak_ada', 'Berkas tidak ada.', array( 'status' => 404 ) );
        }
        if ( ! self::di_dalam( $akar, $abs ) ) {
            return self::tolak( 'Path keluar dari root WordPress.' );
        }
        return $abs;
    }

    public static function untuk_ditulis( $akar, $rel ) {
        if ( ! self::boleh_ditulis( $rel ) ) {
            return self::tolak( 'Path tidak boleh ditulis oleh dorongan staging.' );
        }
        $abs = $akar . $rel;
        if ( is_link( $abs ) ) {
            return self::tolak( 'Path tujuan adalah symlink.' );
        }
        // Leluhur terdekat yang sudah ada harus berada di dalam akar: satu
        // direktori symlink ke luar membuat penulisan mendarat di luar
        // WordPress walau path relatifnya bersih.
        $dir = dirname( $abs );
        while ( ! file_exists( $dir ) && strlen( $dir ) > strlen( rtrim( $akar, '/' ) ) ) {
            $dir = dirname( $dir );
        }
        if ( ! self::di_dalam( $akar, $dir ) ) {
            return self::tolak( 'Path keluar dari root WordPress.' );
        }
        return $abs;
    }
}
