<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

class WPMGR_Settings {

    const OPT_SITE_ID   = 'wpmgr_site_id';
    const OPT_SECRET    = 'wpmgr_secret';
    const OPT_DASHBOARD = 'wpmgr_dashboard_url';
    const OPT_XFF       = 'wpmgr_percayai_xff';

    public static function site_id() {
        return (string) get_option( self::OPT_SITE_ID, '' );
    }

    public static function secret() {
        return (string) get_option( self::OPT_SECRET, '' );
    }

    public static function dashboard_url() {
        return (string) get_option( self::OPT_DASHBOARD, '' );
    }

    public static function percayai_xff() {
        return '1' === (string) get_option( self::OPT_XFF, '0' );
    }

    public static function terpasang() {
        return '' !== self::site_id() && '' !== self::secret();
    }

    /**
     * User khusus untuk SSO. Tidak disembunyikan dari daftar user: akun
     * administrator yang disembunyikan adalah pola perilaku malware.
     */
    public static function pastikan_user() {
        $user = get_user_by( 'login', WPMGR_USER_LOGIN );
        if ( $user ) {
            return (int) $user->ID;
        }
        $id = wp_insert_user(
            array(
                'user_login'   => WPMGR_USER_LOGIN,
                'user_pass'    => wp_generate_password( 64, true, true ),
                'user_email'   => 'wpmgr+' . wp_generate_password( 8, false ) . '@invalid.local',
                'display_name' => 'WP Manager',
                'role'         => 'administrator',
            )
        );
        return is_wp_error( $id ) ? 0 : (int) $id;
    }

    /**
     * Mengurai kunci koneksi tanpa menyentuh WordPress sama sekali.
     *
     * Dipisahkan dari simpan_kunci() supaya seluruh validasi yang menentukan
     * apakah sebuah kunci layak dipercaya dapat diuji langsung dengan PHPUnit,
     * tanpa memuat WordPress. Mengembalikan array tiga elemen bila sah, atau
     * WP_Error bila tidak.
     *
     * Limit 3 pada explode() disengaja: dashboard_url sendiri memuat ':'
     * (skema https://), jadi hanya dua ':' pertama yang bertindak sebagai
     * pemisah bagian -- sisanya, termasuk ':' apa pun di dalamnya, menjadi
     * utuh bagian ketiga.
     */
    public static function urai_kunci( $kunci ) {
        $mentah = base64_decode( strtr( trim( $kunci ), '-_', '+/' ), true );
        if ( false === $mentah ) {
            return new WP_Error( 'wpmgr_kunci_rusak', 'Kunci koneksi tidak dapat dibaca.' );
        }
        $bagian = explode( ':', $mentah, 3 );
        if ( 3 !== count( $bagian ) ) {
            return new WP_Error( 'wpmgr_kunci_rusak', 'Kunci koneksi tidak lengkap.' );
        }
        list( $site_id, $secret, $dashboard ) = $bagian;

        if ( ! preg_match( '/^[0-9a-f]{64}$/', $secret ) ) {
            return new WP_Error( 'wpmgr_kunci_rusak', 'Secret pada kunci tidak valid.' );
        }
        if ( 0 !== strpos( $dashboard, 'https://' ) ) {
            return new WP_Error( 'wpmgr_kunci_rusak', 'URL dashboard wajib https://.' );
        }

        return array( $site_id, $secret, rtrim( $dashboard, '/' ) );
    }

    public static function simpan_kunci( $kunci ) {
        $hasil = self::urai_kunci( $kunci );
        if ( is_wp_error( $hasil ) ) {
            return $hasil;
        }
        list( $site_id, $secret, $dashboard ) = $hasil;

        update_option( self::OPT_SITE_ID, $site_id, false );
        update_option( self::OPT_SECRET, $secret, false );
        update_option( self::OPT_DASHBOARD, $dashboard, false );

        if ( ! self::pastikan_user() ) {
            return new WP_Error(
                'wpmgr_user_gagal',
                'Kunci tersimpan, tetapi user wpmgr untuk SSO tidak dapat dibuat. '
                . 'Scan dan update akan berjalan; tombol Masuk tidak akan berfungsi '
                . 'sampai masalah ini diperbaiki.'
            );
        }

        return self::kirim_konfirmasi();
    }

    /**
     * Membuktikan ke dashboard bahwa site ini benar memegang secret-nya, dan
     * sekaligus membuktikan arah keluar (site -> dashboard) dapat dilalui.
     */
    public static function kirim_konfirmasi() {
        $path = '/api/pair/confirm';
        $body = wp_json_encode(
            array(
                'connector_version' => WPMGR_VERSION,
                'wp_version'        => get_bloginfo( 'version' ),
                'php_version'       => PHP_VERSION,
                'site_url'          => home_url(),
            )
        );
        $ts    = time();
        $nonce = bin2hex( random_bytes( 16 ) );

        $resp = wp_remote_post(
            self::dashboard_url() . $path,
            array(
                'timeout' => 20,
                'headers' => array(
                    'Content-Type'      => 'application/json',
                    'X-Wpmgr-Site'      => self::site_id(),
                    'X-Wpmgr-Timestamp' => (string) $ts,
                    'X-Wpmgr-Nonce'     => $nonce,
                    'X-Wpmgr-Signature' => WPMGR_Signing::sign( self::secret(), 'POST', $path, $ts, $nonce, $body ),
                ),
                'body'    => $body,
            )
        );

        if ( is_wp_error( $resp ) ) {
            return new WP_Error( 'wpmgr_tidak_terhubung',
                'Site ini tidak dapat menghubungi dashboard: ' . $resp->get_error_message() );
        }
        $kode = (int) wp_remote_retrieve_response_code( $resp );
        if ( 200 !== $kode ) {
            return new WP_Error( 'wpmgr_ditolak',
                'Dashboard menolak konfirmasi (HTTP ' . $kode . '): ' . wp_remote_retrieve_body( $resp ) );
        }
        return true;
    }

    public static function daftarkan_menu() {
        add_options_page( 'WP Manager', 'WP Manager', 'manage_options', 'wpmgr',
            array( __CLASS__, 'render' ) );
    }

    public static function tangani_simpan() {
        if ( ! current_user_can( 'manage_options' ) ) {
            return;
        }

        if ( isset( $_POST['wpmgr_simpan_setelan'] ) ) {
            check_admin_referer( 'wpmgr_setelan' );
            update_option( self::OPT_XFF, empty( $_POST['wpmgr_percayai_xff'] ) ? '0' : '1', false );
            set_transient( 'wpmgr_pesan', 'Pengaturan pemantauan disimpan.', 30 );
            wp_safe_redirect( admin_url( 'options-general.php?page=wpmgr' ) );
            exit;
        }

        if ( ! isset( $_POST['wpmgr_kunci'] ) ) {
            return;
        }
        check_admin_referer( 'wpmgr_simpan' );

        $hasil = self::simpan_kunci( sanitize_text_field( wp_unslash( $_POST['wpmgr_kunci'] ) ) );
        $pesan = is_wp_error( $hasil ) ? $hasil->get_error_message() : 'Terhubung ke dashboard.';
        set_transient( 'wpmgr_pesan', $pesan, 30 );

        wp_safe_redirect( admin_url( 'options-general.php?page=wpmgr' ) );
        exit;
    }

    public static function render() {
        $pesan = get_transient( 'wpmgr_pesan' );
        delete_transient( 'wpmgr_pesan' );
        ?>
        <div class="wrap">
            <h1>WP Manager</h1>
            <?php if ( $pesan ) : ?>
                <div class="notice notice-info"><p><?php echo esc_html( $pesan ); ?></p></div>
            <?php endif; ?>

            <?php if ( self::terpasang() ) : ?>
                <p><strong>Status:</strong> terhubung ke
                   <code><?php echo esc_html( self::dashboard_url() ); ?></code></p>
                <p>ID site: <code><?php echo esc_html( self::site_id() ); ?></code></p>
            <?php else : ?>
                <p>Belum terhubung. Tempel kunci koneksi dari dashboard.</p>
            <?php endif; ?>

            <form method="post">
                <?php wp_nonce_field( 'wpmgr_simpan' ); ?>
                <p>
                    <label for="wpmgr_kunci">Kunci koneksi</label><br>
                    <textarea id="wpmgr_kunci" name="wpmgr_kunci" rows="3" cols="80"></textarea>
                </p>
                <?php submit_button( 'Simpan dan hubungkan' ); ?>
            </form>

            <h2>Pemantauan</h2>
            <form method="post">
                <?php wp_nonce_field( 'wpmgr_setelan' ); ?>
                <input type="hidden" name="wpmgr_simpan_setelan" value="1">
                <p>
                    <label>
                        <input type="checkbox" name="wpmgr_percayai_xff" value="1" <?php checked( self::percayai_xff() ); ?>>
                        Site ini berada di balik proxy atau load balancer (percayai header <code>X-Forwarded-For</code>)
                    </label>
                </p>
                <p class="description">
                    Aktifkan hanya bila server ini benar-benar berada di belakang proxy yang Anda
                    kendalikan. Bila diaktifkan tanpa proxy, pengunjung dapat memalsukan IP mereka
                    di riwayat login. Site di balik Cloudflare tidak perlu mengaktifkan ini.
                </p>
                <?php submit_button( 'Simpan pengaturan' ); ?>
            </form>
        </div>
        <?php
    }
}
