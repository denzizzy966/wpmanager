<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Manifest untuk tarik (spec §6.2 langkah 1): daftar berkas dengan ukuran,
 * mtime, dan sha256 (hanya ≤ 50 MB), dipaging lewat kursor supaya site
 * dengan ratusan ribu berkas tetap muat dalam batas 30 detik per request.
 *
 * Aturan path dipusatkan di WPMGR_Staging_Path (Task 2): symlink (di
 * komponen mana pun), nama bukan UTF-8, segmen berisi ':' atau berakhiran
 * '.'/spasi, dan nama terlalu panjang semuanya ditolak fungsi itu. Manifest
 * memakainya di sini supaya tidak pernah mendaftar path yang nanti ditolak
 * /staging/file -- symlink dicek langsung (penelusuran tidak pernah masuk
 * ke direktori symlink), dan nama berkas non-UTF-8/tidak sah dicek lewat
 * normalisasi() sebelum berkas itu dimasukkan ke hasil.
 */
class WPMGR_Staging_Manifest {

    const BATAS_ENTRI    = 5000;
    const MAKS_KEDALAMAN = 64;
    const MAKS_CONTOH    = 50;

    public static $maks_hash     = 52428800;
    public static $anggaran_hash = 536870912;

    public static function batas( $nilai ) {
        $n = (int) $nilai;
        return ( $n < 1 || $n > self::BATAS_ENTRI ) ? self::BATAS_ENTRI : $n;
    }

    public static function jalan( $akar, $kursor, $batas, $tenggat ) {
        $ctx = array(
            'berkas'          => array(),
            'dilewati'        => array(),
            'jumlah_dilewati' => 0,
            'batas'           => self::batas( $batas ),
            'tenggat'         => (float) $tenggat,
            'terhash'         => 0,
            'berhenti'        => false,
        );
        $kursor = (string) $kursor;
        $bagian = ( '' === $kursor ) ? array() : explode( '/', $kursor );
        self::telusuri( $akar, '', $bagian, ! empty( $bagian ), $ctx, 0 );
        $jumlah = count( $ctx['berkas'] );
        return array(
            'berkas'          => $ctx['berkas'],
            'dilewati'        => $ctx['dilewati'],
            'jumlah_dilewati' => $ctx['jumlah_dilewati'],
            'kursor'          => ( $ctx['berhenti'] && $jumlah ) ? $ctx['berkas'][ $jumlah - 1 ]['path'] : null,
            'lagi'            => $ctx['berhenti'] && $jumlah > 0,
        );
    }

    private static function lewati( array &$ctx, $rel, $alasan ) {
        $ctx['jumlah_dilewati']++;
        if ( count( $ctx['dilewati'] ) < self::MAKS_CONTOH ) {
            // Nama yang dilewati bisa berupa byte bukan UTF-8; hanya versi
            // yang sudah dibersihkan yang dikirim, sebagai teks tampilan.
            $ctx['dilewati'][] = array( 'path' => WPMGR_Staging::bersih( $rel, 300 ), 'alasan' => $alasan );
        }
    }

    /** Tenggat dan anggaran hanya berlaku setelah ada berkas: kursor harus selalu maju. */
    private static function habis( array $ctx ) {
        return count( $ctx['berkas'] ) > 0 && microtime( true ) >= $ctx['tenggat'];
    }

    private static function telusuri( $akar, $rel_dir, array $kursor, $selaras, array &$ctx, $kedalaman ) {
        if ( $kedalaman > self::MAKS_KEDALAMAN ) {
            self::lewati( $ctx, rtrim( $rel_dir, '/' ), 'terlalu_dalam' );
            return;
        }
        $nama = @scandir( $akar . $rel_dir ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $nama ) {
            self::lewati( $ctx, rtrim( $rel_dir, '/' ), 'tidak_terbaca' );
            return;
        }
        sort( $nama, SORT_STRING );
        $target   = ( $selaras && isset( $kursor[ $kedalaman ] ) ) ? $kursor[ $kedalaman ] : null;
        $terakhir = count( $kursor ) - 1;
        foreach ( $nama as $n ) {
            if ( $ctx['berhenti'] ) {
                return;
            }
            if ( '.' === $n || '..' === $n ) {
                continue;
            }
            $masuk_selaras = false;
            if ( null !== $target ) {
                $banding = strcmp( $n, $target );
                if ( $banding < 0 ) {
                    continue;
                }
                if ( 0 === $banding ) {
                    if ( $kedalaman === $terakhir ) {
                        continue;
                    }
                    $masuk_selaras = true;
                }
            }
            $rel = $rel_dir . $n;
            $abs = $akar . $rel;
            if ( 1 !== preg_match( '//u', $n ) ) {
                self::lewati( $ctx, $rel, 'nama_bukan_utf8' );
                continue;
            }
            if ( is_link( $abs ) ) {
                self::lewati( $ctx, $rel, 'symlink' );
                continue;
            }
            if ( is_dir( $abs ) ) {
                if ( WPMGR_Staging_Path::dikecualikan( $rel ) ) {
                    continue;
                }
                if ( self::habis( $ctx ) ) {
                    $ctx['berhenti'] = true;
                    return;
                }
                self::telusuri( $akar, $rel . '/', $kursor, $masuk_selaras, $ctx, $kedalaman + 1 );
                continue;
            }
            if ( ! is_file( $abs ) || WPMGR_Staging_Path::dikecualikan( $rel ) ) {
                continue;
            }
            if ( is_wp_error( WPMGR_Staging_Path::normalisasi( $rel ) ) ) {
                self::lewati( $ctx, $rel, 'path_tidak_sah' );
                continue;
            }
            $ukuran = @filesize( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            $mtime  = @filemtime( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( false === $ukuran || false === $mtime || ! is_readable( $abs ) ) {
                self::lewati( $ctx, $rel, 'tidak_terbaca' );
                continue;
            }
            $perlu_hash = $ukuran <= self::$maks_hash;
            if ( self::habis( $ctx )
                || ( $perlu_hash && $ctx['terhash'] > 0 && $ctx['terhash'] + $ukuran > self::$anggaran_hash ) ) {
                $ctx['berhenti'] = true;
                return;
            }
            $hash = null;
            if ( $perlu_hash ) {
                $hash = @hash_file( 'sha256', $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
                if ( false === $hash ) {
                    self::lewati( $ctx, $rel, 'tidak_terbaca' );
                    continue;
                }
                $ctx['terhash'] += $ukuran;
            }
            $ctx['berkas'][] = array( 'path' => $rel, 'ukuran' => (int) $ukuran, 'mtime' => (int) $mtime, 'hash' => $hash );
            if ( count( $ctx['berkas'] ) >= $ctx['batas'] ) {
                $ctx['berhenti'] = true;
                return;
            }
        }
    }

    public static function nama_tabel_sah( $nama, $prefix ) {
        return 1 === preg_match( '/^[A-Za-z0-9_$]{1,64}\z/', (string) $nama ) && 0 === strpos( (string) $nama, (string) $prefix );
    }

    public static function pk( $wpdb, $tabel ) {
        $kunci = $wpdb->get_results( "SHOW KEYS FROM `{$tabel}` WHERE Key_name = 'PRIMARY'", ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL -- nama tabel sudah divalidasi regex
        $kunci = is_array( $kunci ) ? $kunci : array();
        usort( $kunci, function ( $a, $b ) {
            return (int) $a['Seq_in_index'] - (int) $b['Seq_in_index'];
        } );
        $kolom = array();
        foreach ( $kunci as $k ) {
            $nama = isset( $k['Column_name'] ) ? (string) $k['Column_name'] : '';
            if ( 1 !== preg_match( '/^[A-Za-z0-9_$]{1,64}\z/', $nama ) ) {
                // Nama kolom yang tidak bisa kita kutip dengan aman: perlakukan
                // tabel ini sebagai tanpa PK (LIMIT/OFFSET).
                return array();
            }
            $kolom[] = $nama;
        }
        return $kolom;
    }

    public static function tabel( $wpdb ) {
        $baris    = $wpdb->get_results( $wpdb->prepare( 'SHOW TABLE STATUS LIKE %s', $wpdb->esc_like( $wpdb->prefix ) . '%' ), ARRAY_A );
        $hasil    = array();
        $dilewati = 0;
        foreach ( (array) $baris as $b ) {
            $nama = isset( $b['Name'] ) ? (string) $b['Name'] : '';
            if ( ! self::nama_tabel_sah( $nama, $wpdb->prefix ) ) {
                $dilewati++;
                continue;
            }
            if ( empty( $b['Engine'] ) ) {
                continue; // VIEW: tidak diekspor, dibuat ulang oleh plugin pemiliknya.
            }
            if ( count( $hasil ) >= 2000 ) {
                $dilewati++;
                continue;
            }
            $hasil[] = array(
                'nama'   => $nama,
                'baris'  => (int) $b['Rows'],
                'ukuran' => (int) $b['Data_length'] + (int) $b['Index_length'],
                'mesin'  => (string) $b['Engine'],
                'pk'     => self::pk( $wpdb, $nama ),
            );
        }
        return array( $hasil, $dilewati );
    }

    public static function ke_byte( $nilai ) {
        $nilai = trim( (string) $nilai );
        if ( ! preg_match( '/^([0-9]+)\s*([KkMmGg]?)\z/', $nilai, $m ) ) {
            return 0;
        }
        $n    = (int) $m[1];
        $kali = array( '' => 1, 'k' => 1024, 'm' => 1048576, 'g' => 1073741824 );
        return $n * $kali[ strtolower( $m[2] ) ];
    }

    /** Koreksi #15: body 8 MB ditolak hosting dengan post_max_size=8M. */
    public static function batas_unggah( $post_max_size ) {
        $b = self::ke_byte( $post_max_size );
        if ( $b <= 0 ) {
            return 4194304;
        }
        return max( 262144, min( 4194304, intdiv( $b, 2 ) ) );
    }

    /**
     * Koreksi #22: multisite dan WP_CONTENT_DIR di luar ABSPATH dilaporkan
     * di sini supaya dashboard bisa menolak staging pada site seperti itu
     * (spec §3 mengecualikan multisite; manifest hanya menelusuri ABSPATH).
     */
    public static function info( $wpdb, $akar, $konten ) {
        list( $tabel, $dilewati ) = self::tabel( $wpdb );
        $konten = rtrim( str_replace( '\\', '/', (string) $konten ), '/' ) . '/';
        $akar   = rtrim( str_replace( '\\', '/', (string) $akar ), '/' ) . '/';
        return array(
            'php'            => PHP_VERSION,
            'wp'             => (string) get_bloginfo( 'version' ),
            'table_prefix'   => (string) $wpdb->prefix,
            'charset'        => (string) $wpdb->charset,
            'home'           => (string) home_url(),
            'siteurl'        => (string) site_url(),
            'multisite'      => (bool) is_multisite(),
            'konten_di_luar' => 0 !== strpos( $konten, (string) $akar ),
            'batas_unggah'   => self::batas_unggah( ini_get( 'post_max_size' ) ),
            'tabel'          => $tabel,
            'tabel_dilewati' => $dilewati,
        );
    }
}
