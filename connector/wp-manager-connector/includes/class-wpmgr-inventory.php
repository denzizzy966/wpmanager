<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPMGR_Inventory {

    /**
     * Memaksa WordPress memeriksa update lebih dulu, karena transient update
     * bisa berumur belasan jam dan dashboard yang menampilkan data basi sama
     * saja dengan dashboard yang salah.
     */
    private static function segarkan() {
        require_once ABSPATH . 'wp-admin/includes/update.php';

        // wp_update_plugins() dan wp_update_themes() tidak menerima argumen
        // pemaksa dan akan langsung kembali bila transient-nya belum berumur
        // 12 jam. Menghapus transient lebih dulu adalah satu-satunya cara
        // membuat ketiganya benar-benar memeriksa ulang. Tanpa ini, dashboard
        // melaporkan ketersediaan update dari data yang bisa berumur hampir
        // setengah hari — padahal ketepatan soal itu adalah inti produknya.
        delete_site_transient( 'update_core' );
        delete_site_transient( 'update_plugins' );
        delete_site_transient( 'update_themes' );

        wp_version_check( array(), true );
        wp_update_plugins();
        wp_update_themes();
    }

    public static function kumpulkan() {
        self::segarkan();

        return array(
            'core'    => self::core(),
            'plugins' => self::plugins(),
            'themes'  => self::themes(),
        );
    }

    private static function core() {
        $terpasang = get_bloginfo( 'version' );
        $tersedia  = null;

        $cek = get_site_transient( 'update_core' );
        if ( ! empty( $cek->updates ) ) {
            foreach ( $cek->updates as $u ) {
                if ( isset( $u->response, $u->current ) && 'upgrade' === $u->response ) {
                    $tersedia = $u->current;
                    break;
                }
            }
        }

        return array(
            'slug'            => 'core',
            'nama'            => 'WordPress',
            'versi_terpasang' => $terpasang,
            'versi_tersedia'  => $tersedia,
            'aktif'           => true,
            'auto_update'     => false,
        );
    }

    private static function plugins() {
        require_once ABSPATH . 'wp-admin/includes/plugin.php';

        $semua    = get_plugins();
        $update   = get_site_transient( 'update_plugins' );
        $otomatis = (array) get_option( 'auto_update_plugins', array() );
        $keluar   = array();

        foreach ( $semua as $file => $data ) {
            $tersedia = null;
            if ( isset( $update->response[ $file ]->new_version ) ) {
                $tersedia = $update->response[ $file ]->new_version;
            }
            $keluar[] = array(
                'slug'            => $file,
                'nama'            => $data['Name'],
                'versi_terpasang' => $data['Version'],
                'versi_tersedia'  => $tersedia,
                'aktif'           => is_plugin_active( $file ),
                'auto_update'     => in_array( $file, $otomatis, true ),
            );
        }
        return $keluar;
    }

    private static function themes() {
        $semua    = wp_get_themes();
        $update   = get_site_transient( 'update_themes' );
        $otomatis = (array) get_option( 'auto_update_themes', array() );
        $aktif    = get_stylesheet();
        $keluar   = array();

        foreach ( $semua as $slug => $tema ) {
            $tersedia = null;
            if ( isset( $update->response[ $slug ]['new_version'] ) ) {
                $tersedia = $update->response[ $slug ]['new_version'];
            }
            $keluar[] = array(
                'slug'            => $slug,
                'nama'            => $tema->get( 'Name' ),
                'versi_terpasang' => $tema->get( 'Version' ),
                'versi_tersedia'  => $tersedia,
                'aktif'           => ( $slug === $aktif ),
                'auto_update'     => in_array( $slug, $otomatis, true ),
            );
        }
        return $keluar;
    }
}
