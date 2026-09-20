<?php
/**
 * Verifikasi tanda tangan HMAC. PHP murni, tanpa ketergantungan WordPress,
 * supaya dapat diuji langsung terhadap fixture bersama dengan sisi Python.
 */

if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

class WPMGR_Signing {

    /**
     * Kunci HMAC adalah string hex apa adanya (ASCII), BUKAN hasil hex2bin().
     * Sisi Python melakukan hal yang sama. Tidak ada langkah konversi yang
     * dapat berbeda di antara keduanya.
     */
    public static function canonical( $method, $path, $timestamp, $nonce, $body ) {
        return implode(
            "\n",
            array(
                strtoupper( $method ),
                $path,
                (string) $timestamp,
                $nonce,
                hash( 'sha256', (string) $body ),
            )
        );
    }

    public static function sign( $secret_hex, $method, $path, $timestamp, $nonce, $body ) {
        return hash_hmac(
            'sha256',
            self::canonical( $method, $path, $timestamp, $nonce, $body ),
            $secret_hex
        );
    }

    public static function verify( $secret_hex, $signature, $method, $path, $timestamp, $nonce, $body ) {
        $diharapkan = self::sign( $secret_hex, $method, $path, $timestamp, $nonce, $body );
        return hash_equals( $diharapkan, (string) $signature );
    }
}
