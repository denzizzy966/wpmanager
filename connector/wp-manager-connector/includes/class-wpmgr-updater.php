<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

class WPMGR_Updater {

    public static function versi_terpasang( $tipe, $slug ) {
        if ( 'core' === $tipe ) {
            return get_bloginfo( 'version' );
        }
        if ( 'plugin' === $tipe ) {
            require_once ABSPATH . 'wp-admin/includes/plugin.php';
            $semua = get_plugins();
            return isset( $semua[ $slug ]['Version'] ) ? $semua[ $slug ]['Version'] : null;
        }
        if ( 'theme' === $tipe ) {
            $tema = wp_get_theme( $slug );
            return $tema->exists() ? $tema->get( 'Version' ) : null;
        }
        return null;
    }

    /**
     * Apakah versi terpasang sudah berada di target atau melewatinya.
     *
     * Dipisahkan menjadi metode murni supaya keputusan yang menentukan seluruh
     * sifat idempoten endpoint ini dapat diuji tanpa WordPress. Perbandingan
     * memakai '>=' dan bukan kesetaraan: bila client sempat meng-update manual
     * ke versi lebih baru, menjalankan upgrade ke target yang lebih lama akan
     * menjadi penurunan versi.
     */
    public static function sudah_di_versi( $terpasang, $ke_versi ) {
        return version_compare( $terpasang, $ke_versi, '>=' );
    }

    /**
     * Idempoten secara sengaja: skenario "update berhasil tetapi respons tidak
     * sampai" pasti terjadi cepat atau lambat, dan retry harus aman.
     */
    public static function jalankan( $tipe, $slug, $ke_versi ) {
        $sebelum = self::versi_terpasang( $tipe, $slug );

        if ( null === $sebelum ) {
            return new WP_Error( 'wpmgr_tidak_ditemukan',
                'Paket tidak ditemukan di site ini.', array( 'status' => 404 ) );
        }

        if ( self::sudah_di_versi( $sebelum, $ke_versi ) ) {
            return array(
                'ok'            => true,
                'versi_sebelum' => $sebelum,
                'versi_sesudah' => $sebelum,
                'pesan'         => 'sudah di versi tersebut',
            );
        }

        require_once ABSPATH . 'wp-admin/includes/file.php';
        require_once ABSPATH . 'wp-admin/includes/misc.php';
        require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';
        require_once ABSPATH . 'wp-admin/includes/update.php';

        wp_update_plugins();
        wp_update_themes();

        $skin  = new Automatic_Upgrader_Skin();
        $hasil = null;

        if ( 'plugin' === $tipe ) {
            $upgrader = new Plugin_Upgrader( $skin );
            $hasil    = $upgrader->upgrade( $slug );
        } elseif ( 'theme' === $tipe ) {
            $upgrader = new Theme_Upgrader( $skin );
            $hasil    = $upgrader->upgrade( $slug );
        } elseif ( 'core' === $tipe ) {
            $cek = get_site_transient( 'update_core' );
            if ( empty( $cek->updates[0] ) ) {
                return new WP_Error( 'wpmgr_tidak_ada_update',
                    'Tidak ada update core yang tersedia.', array( 'status' => 409 ) );
            }
            $upgrader = new Core_Upgrader( $skin );
            $hasil    = $upgrader->upgrade( $cek->updates[0] );
        } else {
            return new WP_Error( 'wpmgr_tipe_salah', 'Tipe paket tidak dikenal.', array( 'status' => 400 ) );
        }

        if ( is_wp_error( $hasil ) ) {
            return new WP_Error( 'wpmgr_upgrade_gagal',
                $hasil->get_error_message(), array( 'status' => 500 ) );
        }
        if ( false === $hasil ) {
            // Upgrader dapat gagal tanpa WP_Error sama sekali -- ia hanya
            // mengembalikan false, dengan alasan kegagalan cuma tersimpan di
            // pesan skin. Bila ini tidak diperiksa, kegagalan seperti itu
            // akan terlihat seperti sukses tanpa perubahan apa pun.
            $pesan = implode( ' | ', (array) $skin->get_upgrade_messages() );
            return new WP_Error( 'wpmgr_upgrade_gagal',
                $pesan ? $pesan : 'Upgrader mengembalikan false tanpa pesan.',
                array( 'status' => 500 ) );
        }

        // Cache plugin/tema dibersihkan sebelum membaca ulang versi terpasang
        // -- tanpa ini, versi yang dibaca bisa berasal dari cache pra-upgrade
        // dan endpoint melaporkan versi tidak berubah padahal upgrade sukses.
        wp_clean_plugins_cache( true );
        wp_clean_themes_cache( true );
        $sesudah = self::versi_terpasang( $tipe, $slug );

        return array(
            'ok'            => true,
            'versi_sebelum' => $sebelum,
            'versi_sesudah' => null === $sesudah ? $ke_versi : $sesudah,
            'pesan'         => implode( ' | ', (array) $skin->get_upgrade_messages() ),
        );
    }
}
