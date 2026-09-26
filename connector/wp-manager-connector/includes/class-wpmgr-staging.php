<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Pintu masuk endpoint /staging/* (spec §6.1). Semua endpoint mati kecuali
 * admin site menyalakan "Izinkan staging", dan connector di site staging
 * sendiri (WPMGR_STAGING) tidak pernah mengumumkan atau melayaninya.
 */
class WPMGR_Staging {

    const KUNCI_BINER    = '__wpmgr_biner';
    const HOOK_BERSIHKAN = 'wpmgr_staging_bersihkan';

    private static $biner = null;

    public static function mode_staging() {
        return defined( 'WPMGR_STAGING' ) && WPMGR_STAGING;
    }

    public static function fitur_aktif() {
        return WPMGR_Settings::izinkan_staging() && ! self::mode_staging();
    }

    public static function root() {
        return rtrim( str_replace( '\\', '/', ABSPATH ), '/' ) . '/';
    }

    /**
     * Sisa waktu yang aman dipakai satu request. Hosting murah memakai
     * max_execution_time 30 detik; delapan detik disisakan untuk bootstrap
     * WordPress dan pengiriman respons.
     */
    public static function anggaran_detik() {
        $batas = (int) ini_get( 'max_execution_time' );
        if ( $batas <= 0 ) {
            return 20;
        }
        return max( 5, min( 20, $batas - 8 ) );
    }

    public static function bersih( $teks, $panjang ) {
        return WPMGR_Penangkap::potong( $teks, $panjang );
    }

    public static function galat( $kode, $pesan, $status ) {
        return new WP_Error( $kode, $pesan, array( 'status' => $status ) );
    }

    /**
     * Murni: HMAC diperiksa lebih dulu supaya pihak tak dikenal tidak bisa
     * menebak setelan.
     *
     * $mode_staging (fix R1 minor e) opsional supaya tetap murni/mudah diuji
     * tanpa konstanta global: null berarti "tanyakan mode_staging() sendiri"
     * (dipakai guard() sungguhan). Site yang MEMANG salinan staging perlu
     * pesan yang berbeda dari site produksi yang setelannya sekadar belum
     * dinyalakan -- menyuruh admin staging "mengaktifkan Izinkan staging"
     * membingungkan, karena endpoint staging memang selalu mati di sana.
     */
    public static function putuskan( $hasil_hmac, $fitur_aktif, $mode_staging = null ) {
        if ( true !== $hasil_hmac ) {
            return $hasil_hmac;
        }
        if ( $fitur_aktif ) {
            return true;
        }
        if ( null === $mode_staging ) {
            $mode_staging = self::mode_staging();
        }
        if ( $mode_staging ) {
            return self::galat( 'wpmgr_staging_mati',
                'Site ini adalah salinan staging yang dikelola dashboard WP Manager; endpoint staging dimatikan di sini.',
                403 );
        }
        return self::galat( 'wpmgr_staging_mati',
            'Staging tidak diizinkan di site ini. Aktifkan "Izinkan staging" di Pengaturan -> WP Manager.', 403 );
    }

    public static function guard( $request ) {
        return self::putuskan( WPMGR_REST::guard( $request ), self::fitur_aktif(), self::mode_staging() );
    }

    /** Jalur => array( metode, nama callback di kelas ini ). Diisi Task 3–8. */
    public static function rute() {
        return array(
            '/staging/manifest' => array( 'GET', 'manifest' ),
        );
    }

    public static function manifest( $request ) {
        $kursor = $request->get_param( 'kursor' );
        // Fix 5d (review putaran 1): pagar tipe SEBELUM cast ke string --
        // get_param() bisa mengembalikan array/objek bila query string
        // dikirim dalam bentuk itu (mis. '?kursor[]=x'); (string) pada
        // array memicu PHP Notice/Warning "Array to string conversion",
        // bukan penolakan bersih.
        $kursor = is_string( $kursor ) ? $kursor : '';
        if ( '' !== $kursor && is_wp_error( WPMGR_Staging_Path::normalisasi( $kursor ) ) ) {
            return self::galat( 'wpmgr_staging_path', 'Kursor manifest tidak sah.', 400 );
        }

        $akar = self::root();
        if ( ! WPMGR_Staging_Manifest::akar_bisa_dibaca( $akar ) ) {
            // Fix 5e (review putaran 1): direktori WordPress yang tidak
            // terbaca sama sekali adalah galat KERAS -- manifest kosong
            // tanpa galat bisa disalahartikan dashboard sebagai "site ini
            // memang tidak punya berkas" dan melanjutkan tarik seolah-olah
            // itu benar (mis. menghapus semua berkas staging yang ada).
            return self::galat( 'wpmgr_manifest_akar', 'Direktori WordPress tidak dapat dibaca.', 500 );
        }

        $anggaran = self::anggaran_detik();
        $info     = null;
        if ( '' === $kursor ) {
            // Fix 2 (review putaran 1): info() menjalankan SHOW TABLE
            // STATUS + satu SHOW KEYS per tabel -- bisa ratusan query pada
            // site dengan banyak plugin. Waktu itu dikurangi dari anggaran
            // penelusuran berkas SEBELUM jalan() dipanggil, bukan
            // dibiarkan memakan anggaran penuh diam-diam.
            $mulai    = microtime( true );
            global $wpdb;
            $info     = WPMGR_Staging_Manifest::info( $wpdb, $akar, WP_CONTENT_DIR );
            $anggaran = WPMGR_Staging_Manifest::anggaran_setelah( $anggaran, $mulai );
        }

        $hasil = WPMGR_Staging_Manifest::jalan( $akar, $kursor, $request->get_param( 'batas' ),
            microtime( true ) + $anggaran );
        if ( null !== $info ) {
            $hasil['info'] = $info;
        }
        return rest_ensure_response( $hasil );
    }

    public static function daftarkan_route() {
        foreach ( self::rute() as $jalur => $r ) {
            register_rest_route( WPMGR_REST::NS, $jalur, array(
                'methods'             => $r[0],
                'callback'            => array( __CLASS__, $r[1] ),
                'permission_callback' => array( __CLASS__, 'guard' ),
            ) );
        }
    }

    /**
     * Balasan biner (isi berkas, SQL) tanpa base64: 8 MB isi tetap 8 MB di
     * kabel. Isinya ditahan di sini dan dicetak oleh sajikan_biner() pada
     * rest_pre_serve_request, setelah WordPress mengirim header respons
     * (termasuk header anti-cache dari rest_post_dispatch).
     */
    public static function respons_biner( $isi ) {
        self::$biner = (string) $isi;
        $r = new WP_REST_Response( array( self::KUNCI_BINER => true ) );
        $r->header( 'Content-Type', 'application/octet-stream' );
        $r->header( 'Content-Length', (string) strlen( self::$biner ) );
        $r->header( 'X-Wpmgr-Sha256', hash( 'sha256', self::$biner ) );
        return $r;
    }

    public static function sajikan_biner( $served, $result, $request, $server ) {
        if ( $served || null === self::$biner || ! ( $result instanceof WP_HTTP_Response ) ) {
            return $served;
        }
        $data = $result->get_data();
        if ( ! is_array( $data ) || empty( $data[ self::KUNCI_BINER ] ) ) {
            return $served;
        }
        echo self::$biner; // phpcs:ignore WordPress.Security.EscapeOutput -- isi biner, bukan HTML
        self::$biner = null;
        return true;
    }
}
