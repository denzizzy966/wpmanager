<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

class WPMGR_REST {

    const NS = 'wpmgr/v1';

    public static function daftarkan_route() {
        $guard = array( __CLASS__, 'guard' );

        register_rest_route( self::NS, '/ping', array(
            'methods'             => 'GET',
            'callback'            => array( __CLASS__, 'ping' ),
            'permission_callback' => $guard,
        ) );

        register_rest_route( self::NS, '/inventory', array(
            'methods'             => 'GET',
            'callback'            => array( __CLASS__, 'inventory' ),
            'permission_callback' => $guard,
        ) );

        register_rest_route( self::NS, '/update', array(
            'methods'             => 'POST',
            'callback'            => array( __CLASS__, 'update' ),
            'permission_callback' => $guard,
        ) );
    }

    private static function tolak( $pesan ) {
        return new WP_Error( 'wpmgr_ditolak', $pesan, array( 'status' => 401 ) );
    }

    /**
     * Path yang ditandatangani, diturunkan dari route REST.
     *
     * Dipisahkan menjadi metode murni supaya dapat diuji tanpa WordPress.
     * Dashboard menandatangani `/wp-json/wpmgr/v1/<endpoint>`; bila string yang
     * disusun di sini meleset satu karakter saja, tanda tangan tidak akan
     * cocok dan satu-satunya gejala di seluruh sistem adalah 401 tanpa
     * penjelasan.
     */
    public static function path_untuk_tanda_tangan( $route ) {
        return '/wp-json' . $route;
    }

    public static function guard( $request ) {
        if ( ! WPMGR_Settings::terpasang() ) {
            return self::tolak( 'Connector belum dipasangkan.' );
        }

        $site_id   = (string) $request->get_header( 'x_wpmgr_site' );
        $timestamp = (int) $request->get_header( 'x_wpmgr_timestamp' );
        $nonce     = (string) $request->get_header( 'x_wpmgr_nonce' );
        $signature = (string) $request->get_header( 'x_wpmgr_signature' );

        if ( ! hash_equals( WPMGR_Settings::site_id(), $site_id ) ) {
            return self::tolak( 'ID site tidak cocok.' );
        }
        if ( abs( time() - $timestamp ) > WPMGR_JENDELA_DETIK ) {
            return self::tolak( 'Timestamp di luar jendela yang diizinkan.' );
        }
        if ( ! preg_match( '/^[0-9a-f]{32}$/', $nonce ) ) {
            return self::tolak( 'Nonce tidak valid.' );
        }
        if ( false !== get_transient( 'wpmgr_nonce_' . $nonce ) ) {
            return self::tolak( 'Nonce sudah pernah dipakai.' );
        }

        $path  = self::path_untuk_tanda_tangan( $request->get_route() );
        $body  = $request->get_body();
        $valid = WPMGR_Signing::verify(
            WPMGR_Settings::secret(), $signature, $request->get_method(), $path, $timestamp, $nonce, $body
        );
        if ( ! $valid ) {
            return self::tolak( 'Tanda tangan tidak cocok.' );
        }

        // Nonce dicatat hanya setelah tanda tangan terverifikasi -- bila
        // dicatat lebih awal, penyerang dapat "membakar" nonce sembarang
        // lewat permintaan tak bertanda tangan dan membuat retry yang sah
        // ditolak sebagai replay.
        set_transient( 'wpmgr_nonce_' . $nonce, 1, WPMGR_NONCE_TTL );
        return true;
    }

    public static function ping() {
        return rest_ensure_response( array(
            'connector_version' => WPMGR_VERSION,
            'wp_version'        => get_bloginfo( 'version' ),
            'php_version'       => PHP_VERSION,
            'site_url'          => home_url(),
        ) );
    }

    public static function inventory() {
        return rest_ensure_response( WPMGR_Inventory::kumpulkan() );
    }

    public static function update( $request ) {
        $p = json_decode( $request->get_body(), true );
        if ( ! is_array( $p ) || empty( $p['tipe'] ) || empty( $p['slug'] ) || empty( $p['ke_versi'] ) ) {
            return new WP_Error( 'wpmgr_payload_salah', 'Payload update tidak lengkap.', array( 'status' => 400 ) );
        }
        return rest_ensure_response(
            WPMGR_Updater::jalankan( $p['tipe'], $p['slug'], $p['ke_versi'] )
        );
    }
}
