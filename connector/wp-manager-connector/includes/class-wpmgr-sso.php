<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * SSO sekali-pakai: dashboard membubuhkan token bertanda tangan pada tautan
 * "Masuk"; connector memverifikasinya lalu membuka sesi wp-admin tanpa
 * password apa pun tersimpan atau diketik.
 *
 * Format token harus persis sama dengan sisi Python (src/wpmgr/sso.py):
 * base64url(json(payload)) + "." + hex(hmac_sha256(secret_hex, body)),
 * dengan secret hex dipakai sebagai string ASCII apa adanya sebagai kunci
 * HMAC (bukan hasil hex2bin()).
 */
class WPMGR_SSO {

    const PARAM = 'wpmgr_sso';

    public static function b64url_decode( $s ) {
        $pad = strlen( $s ) % 4;
        if ( $pad ) {
            $s .= str_repeat( '=', 4 - $pad );
        }
        return base64_decode( strtr( $s, '-_', '+/' ), true );
    }

    /**
     * PHP murni: tidak ada pemanggilan WordPress selain membentuk WP_Error,
     * sehingga dapat diuji langsung dengan PHPUnit tanpa memuat WordPress.
     *
     * Tanda tangan diverifikasi SEBELUM payload di-decode dan di-json_decode.
     * Dibalik urutannya, plugin ini akan menguraikan JSON kiriman pihak tak
     * dikenal sebelum tahu apakah token itu bisa dipercaya sama sekali.
     *
     * Pemeriksaan yang dilakukan sengaja dijaga agar tidak lebih longgar
     * daripada sisi Python (baca_token() di src/wpmgr/sso.py): payload wajib
     * berbentuk objek/array asosiatif, nonce wajib 32 karakter hex, dan exp
     * wajib nilai numerik yang belum lewat. Bila salah satu sisi lebih
     * longgar dari yang lain, sisi itu akan mengesahkan token yang ditolak
     * sisi satunya.
     */
    public static function periksa_token( $secret, $token, $now ) {
        $token = (string) $token;
        $pos   = strpos( $token, '.' );
        if ( false === $pos || 0 === $pos || $pos === strlen( $token ) - 1 ) {
            return new WP_Error( 'wpmgr_sso_bentuk', 'Bentuk token salah.' );
        }

        $body = substr( $token, 0, $pos );
        $sig  = substr( $token, $pos + 1 );

        if ( ! hash_equals( hash_hmac( 'sha256', $body, $secret ), $sig ) ) {
            return new WP_Error( 'wpmgr_sso_tanda_tangan', 'Tanda tangan token salah.' );
        }

        $mentah = self::b64url_decode( $body );
        if ( false === $mentah ) {
            return new WP_Error( 'wpmgr_sso_payload', 'Payload tidak dapat dibaca.' );
        }

        $payload = json_decode( $mentah, true );
        if ( ! is_array( $payload ) ) {
            return new WP_Error( 'wpmgr_sso_payload', 'Payload bukan objek.' );
        }

        if ( ! isset( $payload['nonce'] ) || ! is_string( $payload['nonce'] )
            || ! preg_match( '/^[0-9a-f]{32}$/', $payload['nonce'] ) ) {
            return new WP_Error( 'wpmgr_sso_nonce', 'Nonce tidak valid.' );
        }

        if ( ! isset( $payload['exp'] ) || ! is_numeric( $payload['exp'] ) ) {
            return new WP_Error( 'wpmgr_sso_exp', 'Kedaluwarsa token tidak valid.' );
        }

        if ( (int) $now > (int) $payload['exp'] ) {
            return new WP_Error( 'wpmgr_sso_kedaluwarsa', 'Token kedaluwarsa.' );
        }

        return $payload;
    }

    /**
     * Seluruh pekerjaan WordPress -- transient, current user, cookie login,
     * redirect -- hidup di sini, terpisah dari periksa_token() yang murni PHP.
     */
    public static function tangani_permintaan() {
        if ( empty( $_GET[ self::PARAM ] ) || ! WPMGR_Settings::terpasang() ) {
            return;
        }

        $token   = sanitize_text_field( wp_unslash( $_GET[ self::PARAM ] ) );
        $payload = self::periksa_token( WPMGR_Settings::secret(), $token, time() );

        if ( is_wp_error( $payload ) ) {
            wp_die( esc_html( $payload->get_error_message() ), 'SSO ditolak', array( 'response' => 403 ) );
        }

        $kunci_nonce = 'wpmgr_sso_' . $payload['nonce'];
        if ( false !== get_transient( $kunci_nonce ) ) {
            wp_die( 'Token SSO sudah pernah dipakai.', 'SSO ditolak', array( 'response' => 403 ) );
        }

        // Ditandai SEBELUM login diset: token ini sekali-pakai, dan "sudah
        // dipakai" wajib berarti "sudah mulai diproses", bukan "sudah
        // selesai diproses". Token ini melintas di URL -- artinya ia bisa
        // saja mendarat di access log server atau ikut dalam header
        // Referer -- sehingga masa berlakunya yang singkat adalah
        // pertahanannya. Token yang selamat dari pemakaiannya sendiri yang
        // gagal di tengah jalan akan meniadakan pertahanan itu.
        set_transient( $kunci_nonce, 1, WPMGR_SSO_TTL );

        $user_id = WPMGR_Settings::pastikan_user();
        if ( ! $user_id ) {
            wp_die( 'User wpmgr tidak dapat dibuat.', 'SSO gagal', array( 'response' => 500 ) );
        }

        wp_set_current_user( $user_id );
        wp_set_auth_cookie( $user_id, false );

        wp_safe_redirect( admin_url() );
        exit;
    }
}
