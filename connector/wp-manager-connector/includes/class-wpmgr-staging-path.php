<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Aturan path staging (spec §13): hanya path relatif di dalam root
 * WordPress, tanpa `..`, tanpa symlink keluar, dan `wp-config.php` tidak
 * pernah dibaca maupun ditulis. Dashboard menerapkan aturan yang sama
 * (wpmgr.staging.aman); keduanya harus tetap sama ketatnya.
 *
 * Fix R1 (review putaran 1): perbandingan berawalan (strpos) terhadap path
 * yang belum diurai tidak cukup -- symlink pada komponen MANA PUN (bukan
 * hanya komponen terakhir) membuat path yang "tampak" di dalam root menurut
 * strpos ternyata menunjuk ke tempat lain setelah diurai OS (mis.
 * wp-content/uploads/akar -> root itu sendiri membuat .../akar/wp-config.php
 * membaca wp-config.php lewat jalan memutar). Sistem berkas case-insensitive
 * (Windows, macOS default) juga membuat nama berbeda huruf besar/kecil --
 * atau alias nama pendek 8.3 di Windows -- menunjuk ke berkas yang sama.
 * untuk_dibaca()/untuk_ditulis() sekarang mewajibkan realpath() dari path
 * yang diminta persis sama dengan realpath(root) digabung path relatif
 * aslinya (path_kanonik()); ketidakcocokan apa pun ditolak. Nama yang
 * dilindungi (wp-config.php, mu-plugin staging, direktori plugin connector)
 * juga dibandingkan huruf besar/kecil sebagai lapis kedua di dikecualikan()/
 * boleh_ditulis(), karena kedua fungsi itu juga dipakai untuk path yang
 * BELUM ada di disk (tidak ada realpath untuk dibandingkan).
 *
 * Catatan TOCTOU: pemeriksaan di sini dan pembacaan/penulisan berkas yang
 * sungguhan menyusul (fopen()/rename() di pemanggil) bukan satu operasi
 * atomik -- antara keduanya, komponen path bisa saja diganti. Ini hanya bisa
 * dieksploitasi oleh proses lain yang SUDAH berjalan sebagai user web yang
 * sama (mis. plugin lain di site yang sama membuat symlink tepat di antara
 * pemeriksaan dan pemakaian); penyerang jarak jauh tidak dapat memicu race
 * ini lewat endpoint staging sendiri, karena endpoint staging tidak
 * menyediakan cara membuat symlink.
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
            // Fix R2: ':' tidak pernah sah di nama berkas WordPress, tetapi di
            // NTFS ia membuka alternate data stream ('nama::$DATA' membaca/
            // menulis ISI berkas 'nama' itu sendiri, dan 'dir::$INDEX_ALLOCATION'
            // adalah alias direktori itu sendiri) -- sintaks yang tidak
            // dikenali strpos/file_exists di sini tetapi dipahami OS saat
            // fopen() sungguhan terjadi, sehingga path yang tampak "baru"
            // ternyata menimpa berkas yang dilindungi. Titik atau spasi di
            // akhir segmen juga ditolak: Win32 API (dipakai fopen()) memangkas
            // keduanya dari komponen terakhir, jadi 'wp-config.php.' dan
            // 'wp-config.php ' adalah alias 'wp-config.php' juga.
            if ( false !== strpos( $segmen, ':' ) ) {
                return self::tolak( 'Path memuat karakter terlarang.' );
            }
            $akhir_segmen = substr( $segmen, -1 );
            if ( '.' === $akhir_segmen || ' ' === $akhir_segmen ) {
                return self::tolak( 'Path memuat segmen terlarang.' );
            }
        }
        return $rel;
    }

    /**
     * Tidak pernah disalin ke staging maupun didorong (spec §6.2 langkah 1).
     * Dibandingkan huruf besar/kecil (fix R1): sistem berkas Windows/macOS
     * tidak membedakannya, jadi 'WP-CONFIG.PHP' harus dianggap sama dengan
     * 'wp-config.php' di sini juga, bukan hanya lewat realpath() di
     * untuk_dibaca()/untuk_ditulis().
     */
    public static function dikecualikan( $rel ) {
        $rendah = strtolower( (string) $rel );
        if ( 'wp-config.php' === $rendah || '.maintenance' === $rendah ) {
            return true;
        }
        if ( '.log' === substr( $rendah, -4 ) ) {
            return true;
        }
        foreach ( array( 'wp-content/cache', 'wp-content/wpmgr-dorong' ) as $dir ) {
            if ( $rendah === $dir || 0 === strpos( $rendah, $dir . '/' ) ) {
                return true;
            }
        }
        $bagian = explode( '/', $rendah );
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
     *
     * Nama yang dilindungi secara eksplisit (TIDAK_PERNAH_DITULIS, direktori
     * plugin connector -- termasuk path direktori itu sendiri tanpa slash
     * akhir) dibandingkan huruf besar/kecil (fix R1): path di sini belum
     * tentu ada di disk (bisa saja path baru yang akan dibuat), jadi belum
     * ada realpath() untuk dijadikan pembanding kanonik seperti di
     * untuk_dibaca()/untuk_ditulis(). Aturan IZIN (awalan wp-content/,
     * wp-admin/, wp-includes/, daftar AKAR_INTI, pola wp-*.php) tetap huruf
     * besar/kecil apa adanya seperti semula: melonggarkannya di sini hanya
     * akan memperluas apa yang boleh ditulis, bukan mempersempitnya.
     */
    public static function boleh_ditulis( $rel ) {
        if ( is_wp_error( self::normalisasi( $rel ) ) || self::dikecualikan( $rel ) ) {
            return false;
        }
        $rendah = strtolower( (string) $rel );
        foreach ( self::TIDAK_PERNAH_DITULIS as $terlarang ) {
            if ( $rendah === strtolower( $terlarang ) ) {
                return false;
            }
        }
        $awalan_plugin = 'wp-content/plugins/wp-manager-connector';
        if ( 0 === strpos( $rendah, $awalan_plugin )
            && ( strlen( $rendah ) === strlen( $awalan_plugin ) || '/' === $rendah[ strlen( $awalan_plugin ) ] ) ) {
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

    /**
     * realpath() dinormalisasi ke '/' (fix R1): Windows selalu mengembalikan
     * '\\' dari realpath() walau input memakai '/', dan perbandingan persis
     * di path_kanonik() akan salah menolak path yang sah bila kedua ruas
     * tidak memakai pemisah yang sama.
     */
    private static function realpath_garis( $p ) {
        $r = @realpath( $p );
        return false === $r ? false : self::garis( $r );
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

    /**
     * Fix R1 -- aturan path kanonik: realpath($akar . $rel) harus PERSIS SAMA
     * dengan realpath($akar) . '/' . $rel (bukan sekadar berawalan sama,
     * yang dulu dipakai lewat di_dalam()). Perbandingan berawalan menolak
     * symlink KELUAR root, tetapi meloloskan symlink DI DALAM root yang
     * menunjuk ke tempat lain di dalam root juga (mis.
     * wp-content/uploads/x -> root itu sendiri membuat
     * wp-content/uploads/x/wp-config.php "tampak" di dalam root padahal ia
     * membaca wp-config.php lewat jalan memutar). Perbandingan persis ini
     * menolak symlink di komponen mana pun, dan sekaligus menolak alias
     * huruf besar/kecil serta nama pendek 8.3 di sistem berkas
     * case-insensitive, karena realpath() di sana mengembalikan nama asli
     * di disk -- yang tidak akan sama persis dengan $rel yang diminta bila
     * berbeda huruf besar/kecil atau memakai nama pendek.
     */
    private static function path_kanonik( $akar, $rel ) {
        $akar_nyata = self::realpath_garis( $akar );
        if ( false === $akar_nyata ) {
            return self::tolak( 'Root WordPress tidak dapat dibaca.' );
        }
        $abs_nyata = self::realpath_garis( $akar . $rel );
        if ( false === $abs_nyata ) {
            return self::tolak( 'Path keluar dari root WordPress.' );
        }
        $diharapkan = rtrim( $akar_nyata, '/' ) . '/' . $rel;
        if ( $abs_nyata !== $diharapkan ) {
            return self::tolak( 'Path keluar dari root WordPress.' );
        }
        return $abs_nyata;
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
        return self::path_kanonik( $akar, $rel );
    }

    public static function untuk_ditulis( $akar, $rel ) {
        if ( ! self::boleh_ditulis( $rel ) ) {
            return self::tolak( 'Path tidak boleh ditulis oleh dorongan staging.' );
        }
        $abs = $akar . $rel;
        if ( is_link( $abs ) ) {
            return self::tolak( 'Path tujuan adalah symlink.' );
        }
        if ( file_exists( $abs ) ) {
            // Berkas tujuan sudah ada: aturan baca berlaku juga di sini --
            // menimpa lewat leluhur yang di-symlink-kan, atau lewat alias
            // huruf besar/kecil, sama berbahayanya dengan membacanya.
            return self::path_kanonik( $akar, $rel );
        }

        $akar_nyata = self::realpath_garis( $akar );
        if ( false === $akar_nyata ) {
            return self::tolak( 'Root WordPress tidak dapat dibaca.' );
        }

        // Leluhur terdekat yang sudah ada harus berada PERSIS di dalam akar
        // (fix R1: bukan sekadar berawalan sama) setelah symlink diurai: satu
        // direktori symlink ke luar, atau symlink alias ke tempat lain di
        // dalam akar sendiri, membuat penulisan mendarat di lokasi yang
        // berbeda dari path relatif yang tampak bersih.
        $segmen = explode( '/', $rel );
        $baru   = array( array_pop( $segmen ) );
        while ( true ) {
            $rel_induk = implode( '/', $segmen );
            $abs_induk = '' === $rel_induk ? rtrim( $akar, '/' ) : $akar . $rel_induk;
            // is_link() di depan (fix R2 minor): file_exists() mengembalikan
            // false untuk symlink menggantung (target hilang), jadi tanpa ini
            // leluhur symlink yang kebetulan menggantung dianggap "belum ada"
            // dan diloncati begitu saja -- padahal komponen itu tetap sebuah
            // symlink, menggantung atau tidak, dan harus tetap ditolak.
            if ( is_link( $abs_induk ) || file_exists( $abs_induk ) ) {
                $induk_nyata = self::realpath_garis( $abs_induk );
                $diharapkan  = '' === $rel_induk
                    ? rtrim( $akar_nyata, '/' )
                    : rtrim( $akar_nyata, '/' ) . '/' . $rel_induk;
                if ( false === $induk_nyata || $induk_nyata !== $diharapkan ) {
                    return self::tolak( 'Path keluar dari root WordPress.' );
                }
                return rtrim( $induk_nyata, '/' ) . '/' . implode( '/', $baru );
            }
            if ( empty( $segmen ) ) {
                // Akar sendiri (rel_induk kosong) selalu ada dan sudah
                // ditangani cabang di atas; baris ini seharusnya tidak
                // pernah tercapai, dijaga saja supaya loop tidak tak berhenti.
                return self::tolak( 'Path keluar dari root WordPress.' );
            }
            array_unshift( $baru, array_pop( $segmen ) );
        }
    }
}
