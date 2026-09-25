<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * IP klien untuk riwayat login. Header proxy hanya dipercaya dari sumber yang
 * terbukti; tanpa itu penyerang cukup mengirim header sendiri untuk
 * memalsukan IP-nya di log.
 */
class WPMGR_IP {

    public static function valid( $ip ) {
        return false !== filter_var( (string) $ip, FILTER_VALIDATE_IP );
    }

    public static function publik( $ip ) {
        return false !== filter_var( (string) $ip, FILTER_VALIDATE_IP,
            FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE );
    }

    public static function dalam_rentang( $ip, $cidr ) {
        $bagian = explode( '/', (string) $cidr, 2 );
        if ( 2 !== count( $bagian ) ) {
            return false;
        }
        $ip_bin  = @inet_pton( (string) $ip );      // phpcs:ignore WordPress.PHP.NoSilencedErrors
        $net_bin = @inet_pton( $bagian[0] );         // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $ip_bin || false === $net_bin || strlen( $ip_bin ) !== strlen( $net_bin ) ) {
            return false;
        }
        // Prefiks wajib bilangan bulat tak-negatif dalam batas panjang alamat
        // keluarganya. Tanpa ini "/abc", "/-1", atau "/33" pada IPv4 dibaca
        // (int) sebagai 0 atau lolos batas byte, membuat rentang ini cocok
        // dengan IP mana pun -- IP privat lolos jadi "dari Cloudflare".
        if ( ! ctype_digit( $bagian[1] ) ) {
            return false;
        }
        $bit      = (int) $bagian[1];
        $maks_bit = strlen( $net_bin ) * 8;
        if ( $bit > $maks_bit ) {
            return false;
        }
        $utuh = intdiv( $bit, 8 );
        $sisa = $bit % 8;
        if ( substr( $ip_bin, 0, $utuh ) !== substr( $net_bin, 0, $utuh ) ) {
            return false;
        }
        if ( 0 === $sisa ) {
            return true;
        }
        $mask = ( 0xFF << ( 8 - $sisa ) ) & 0xFF;
        return ( ord( $ip_bin[ $utuh ] ) & $mask ) === ( ord( $net_bin[ $utuh ] ) & $mask );
    }

    private static function dari_cloudflare( $ip, array $rentang ) {
        foreach ( $rentang as $cidr ) {
            if ( self::dalam_rentang( $ip, $cidr ) ) {
                return true;
            }
        }
        return false;
    }

    /**
     * Menyamakan alamat IPv4-mapped ("::ffff:a.b.c.d", dipakai soket
     * dual-stack) ke bentuk IPv4 murninya. Tanpa ini alamat semacam itu tidak
     * pernah cocok dengan CIDR Cloudflare yang seluruhnya IPv4, dan tersimpan
     * tidak konsisten dengan REMOTE_ADDR IPv4 biasa di riwayat login.
     */
    public static function normalisasi( $ip ) {
        $ip  = trim( (string) $ip );
        $bin = @inet_pton( $ip ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $bin || 16 !== strlen( $bin ) ) {
            return $ip;
        }
        // Prefiks IPv4-mapped menurut RFC 4291 S2.5.5.2: 80 bit nol, 16 bit satu.
        if ( "\0\0\0\0\0\0\0\0\0\0\xff\xff" !== substr( $bin, 0, 12 ) ) {
            return $ip;
        }
        return inet_ntop( substr( $bin, 12, 4 ) );
    }

    /**
     * Melepas ":port" (IPv4) atau kurung "[..]"/"[..]:port" (IPv6) dari satu
     * entri X-Forwarded-For sebelum divalidasi. Mengembalikan null bila
     * strukturnya sama sekali tak bisa diurai (mis. kurung tak tertutup),
     * yang oleh pemanggil diperlakukan sebagai akhir rantai yang tepercaya.
     */
    private static function urai_entri_xff( $mentah ) {
        $mentah = trim( (string) $mentah );
        if ( '' === $mentah ) {
            return null;
        }
        if ( '[' === $mentah[0] ) {
            $tutup = strpos( $mentah, ']' );
            if ( false === $tutup ) {
                return null;
            }
            return substr( $mentah, 1, $tutup - 1 );
        }
        if ( preg_match( '/^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):\d+$/', $mentah, $cocok ) ) {
            return $cocok[1];
        }
        return $mentah;
    }

    public static function tentukan( array $server, $percayai_xff, array $rentang_cf ) {
        $remote = isset( $server['REMOTE_ADDR'] ) ? trim( (string) $server['REMOTE_ADDR'] ) : '';
        if ( ! self::valid( $remote ) ) {
            return array( 'ip' => null, 'lewat_cloudflare' => false );
        }
        $remote = self::normalisasi( $remote );

        if ( isset( $server['HTTP_CF_CONNECTING_IP'] ) && self::dari_cloudflare( $remote, $rentang_cf ) ) {
            $cf = trim( (string) $server['HTTP_CF_CONNECTING_IP'] );
            if ( self::valid( $cf ) ) {
                return array( 'ip' => self::normalisasi( $cf ), 'lewat_cloudflare' => true );
            }
        }

        // XFF hanya dipercaya bila REMOTE_ADDR sendiri BUKAN alamat publik --
        // artinya request ini benar memang lewat reverse proxy privat kita.
        // Bila REMOTE_ADDR sudah publik, penyerang bisa menyentuh origin
        // langsung dan menitipkan header XFF palsu sendiri; itu wajib diabaikan.
        if ( $percayai_xff && ! self::publik( $remote ) && ! empty( $server['HTTP_X_FORWARDED_FOR'] ) ) {
            // Proxy menambahkan alamat yang ia lihat di posisi paling KANAN;
            // entri di kiri dikendalikan klien. Entri yang valid tapi bukan
            // publik adalah hop internal lain dan dilewati; entri yang sama
            // sekali tak bisa diurai berarti rantai proxy tak lagi bisa
            // dipercaya mulai titik itu, jadi walk dihentikan dan REMOTE_ADDR
            // dipakai -- bukan lanjut ke entri kiri yang dikendalikan klien.
            $daftar = array_reverse( array_map( 'trim', explode( ',', (string) $server['HTTP_X_FORWARDED_FOR'] ) ) );
            foreach ( $daftar as $mentah ) {
                $kandidat = self::urai_entri_xff( $mentah );
                if ( null === $kandidat || ! self::valid( $kandidat ) ) {
                    break;
                }
                $kandidat = self::normalisasi( $kandidat );
                if ( self::publik( $kandidat ) ) {
                    return array( 'ip' => $kandidat, 'lewat_cloudflare' => false );
                }
            }
        }

        return array( 'ip' => $remote, 'lewat_cloudflare' => false );
    }

    public static function saat_ini() {
        static $rentang = null;
        if ( null === $rentang ) {
            $rentang = require __DIR__ . '/cloudflare-ip.php';
        }
        $xff = class_exists( 'WPMGR_Settings' ) && WPMGR_Settings::percayai_xff();
        return self::tentukan( $_SERVER, $xff, $rentang );
    }
}
