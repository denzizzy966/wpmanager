<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Isi berkas untuk tarik dan snapshot (spec §6.2 langkah 2): satu paket
 * berisi beberapa berkas kecil, atau satu rentang byte berkas besar.
 *
 * Setiap nilai dari body permintaan adalah masukan penyerang walau HMAC-nya
 * sah (kontrak HMAC hanya membuktikan siapa pengirimnya, bukan bahwa isinya
 * masuk akal) -- semua tipe dan jumlah divalidasi di sini sebelum dipakai.
 */
class WPMGR_Staging_File {

    const MAKS_JUMLAH = 2000;

    public static $maks_paket = 8388608;

    private static function salah( $pesan ) {
        return WPMGR_Staging::galat( 'wpmgr_staging_permintaan', $pesan, 400 );
    }

    private static function terlalu_besar() {
        return WPMGR_Staging::galat( 'wpmgr_staging_terlalu_besar', 'Permintaan melebihi batas 8 MB per potongan.', 413 );
    }

    /**
     * Bungkus meta+isi jadi paket biner, lalu jepit ukuran PAKET UTUH
     * (bukan cuma isi berkas) ke $maks_paket -- meta (path, mtime, hash per
     * berkas) menambah beberapa ratus byte per entri di atas isi berkas, dan
     * batas global connector (spec: <= 8 MB per request) berlaku untuk
     * respons SELURUHNYA, meta ikut terhitung, bukan hanya isinya.
     */
    private static function bungkus( array $meta, array $isi ) {
        $paket = WPMGR_Staging_Paket::susun( $meta, $isi );
        if ( strlen( $paket ) > self::$maks_paket ) {
            return self::terlalu_besar();
        }
        return $paket;
    }

    public static function ambil( $akar, $p ) {
        if ( ! is_array( $p ) ) {
            return self::salah( 'Body permintaan bukan objek.' );
        }
        if ( isset( $p['rentang'] ) ) {
            return self::rentang( $akar, $p['rentang'] );
        }
        if ( ! isset( $p['berkas'] ) || ! is_array( $p['berkas'] ) || count( $p['berkas'] ) < 1
            || count( $p['berkas'] ) > self::MAKS_JUMLAH ) {
            return self::salah( 'Daftar berkas kosong atau terlalu panjang.' );
        }
        $meta  = array( 'berkas' => array() );
        $isi   = array();
        $total = 0;
        foreach ( array_values( $p['berkas'] ) as $rel ) {
            if ( ! is_string( $rel ) ) {
                return self::salah( 'Path berkas bukan string.' );
            }
            $abs = WPMGR_Staging_Path::untuk_dibaca( $akar, $rel );
            if ( is_wp_error( $abs ) ) {
                if ( 'wpmgr_staging_tidak_ada' === $abs->get_error_code() ) {
                    // Terhapus sejak manifest dibuat: dashboard menghapusnya di staging.
                    $meta['berkas'][] = array( 'path' => $rel, 'hilang' => true );
                    $isi[]            = '';
                    continue;
                }
                // Path berbahaya/dikecualikan menolak SELURUH permintaan (bukan
                // hanya entri ini) -- dashboard tidak pernah memintanya lewat
                // manifest yang sah; bila ia meminta, ada yang salah di sisi
                // pemanggil dan wajib terlihat sebagai galat keras, bukan
                // dilewati diam-diam seperti berkas yang hilang.
                return $abs;
            }
            clearstatcache( true, $abs );
            $ukuran = (int) @filesize( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( $total + $ukuran > self::$maks_paket ) {
                return self::terlalu_besar();
            }
            $data = @file_get_contents( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( false === $data ) {
                return WPMGR_Staging::galat( 'wpmgr_staging_baca', 'Berkas tidak dapat dibaca.', 500 );
            }
            // Berkas bisa tumbuh di antara filesize() dan pembacaan (RF3:
            // produksi berubah selama tarik) -- diperiksa ulang dengan
            // ukuran SUNGGUHAN yang terbaca, bukan hanya perkiraan filesize().
            if ( $total + strlen( $data ) > self::$maks_paket ) {
                return self::terlalu_besar();
            }
            $total           += strlen( $data );
            $meta['berkas'][] = array( 'path' => $rel, 'mtime' => (int) @filemtime( $abs ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            $isi[]            = $data;
        }
        return self::bungkus( $meta, $isi );
    }

    public static function rentang( $akar, $r ) {
        if ( ! is_array( $r ) || ! isset( $r['path'], $r['dari'], $r['panjang'] ) || ! is_string( $r['path'] )
            || ! is_int( $r['dari'] ) || ! is_int( $r['panjang'] ) || $r['dari'] < 0 || $r['panjang'] < 1 ) {
            return self::salah( 'Rentang tidak sah.' );
        }
        if ( $r['panjang'] > self::$maks_paket ) {
            return self::terlalu_besar();
        }
        $abs = WPMGR_Staging_Path::untuk_dibaca( $akar, $r['path'] );
        if ( is_wp_error( $abs ) ) {
            return $abs;
        }
        clearstatcache( true, $abs );
        $total = (int) @filesize( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        $mtime = (int) @filemtime( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        $data  = '';
        if ( $r['dari'] < $total ) {
            // Berkas besar: dibaca lewat fopen/fseek/fread pada path KANONIK
            // yang sudah divalidasi (bukan $akar.$rel mentah), berpotongan
            // 1 MiB supaya panjang besar tidak butuh satu buffer fread() raksasa.
            $h = @fopen( $abs, 'rb' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( false === $h ) {
                return WPMGR_Staging::galat( 'wpmgr_staging_baca', 'Berkas tidak dapat dibaca.', 500 );
            }
            fseek( $h, $r['dari'] );
            while ( strlen( $data ) < $r['panjang'] && ! feof( $h ) ) {
                $potong = fread( $h, min( 1048576, $r['panjang'] - strlen( $data ) ) );
                if ( false === $potong || '' === $potong ) {
                    // Berkas menyusut di antara filesize() dan pembacaan
                    // (RF3): berhenti dengan apa yang benar-benar terbaca,
                    // bukan menunggu/gagal fatal.
                    break;
                }
                $data .= $potong;
            }
            fclose( $h );
        }
        return self::bungkus(
            array( 'berkas' => array( array( 'path' => $r['path'], 'dari' => $r['dari'], 'total' => $total, 'mtime' => $mtime ) ) ),
            array( $data )
        );
    }
}
