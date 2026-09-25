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
        $bit  = (int) $bagian[1];
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

    public static function tentukan( array $server, $percayai_xff, array $rentang_cf ) {
        $remote = isset( $server['REMOTE_ADDR'] ) ? trim( (string) $server['REMOTE_ADDR'] ) : '';
        if ( ! self::valid( $remote ) ) {
            return array( 'ip' => null, 'lewat_cloudflare' => false );
        }

        if ( isset( $server['HTTP_CF_CONNECTING_IP'] ) && self::dari_cloudflare( $remote, $rentang_cf ) ) {
            $cf = trim( (string) $server['HTTP_CF_CONNECTING_IP'] );
            if ( self::valid( $cf ) ) {
                return array( 'ip' => $cf, 'lewat_cloudflare' => true );
            }
        }

        if ( $percayai_xff && ! empty( $server['HTTP_X_FORWARDED_FOR'] ) ) {
            // Proxy menambahkan alamat yang ia lihat di posisi paling KANAN;
            // entri di kiri dikendalikan klien.
            $daftar = array_reverse( array_map( 'trim', explode( ',', (string) $server['HTTP_X_FORWARDED_FOR'] ) ) );
            foreach ( $daftar as $kandidat ) {
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
