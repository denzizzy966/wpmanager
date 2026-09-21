<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPMGR_Inventory {

    /**
     * Memaksa WordPress memeriksa update lebih dulu, karena transient update
     * bisa berumur belasan jam dan dashboard yang menampilkan data basi sama
     * saja dengan dashboard yang salah.
     *
     * Transient TIDAK dihapus. wp_update_plugins() dan wp_update_themes()
     * menyimpan ulang transient lama (dengan last_checked baru) sebelum
     * menghubungi api.wordpress.org, lalu kembali lebih awal bila permintaan
     * itu gagal. Transient yang dihapus lebih dulu tertinggal tanpa
     * `response` sama sekali, dan setiap plugin dilaporkan "tidak ada update"
     * -- bukan basi, melainkan salah, justru ketika koneksi site ke
     * wordpress.org sedang bermasalah. Harga pilihan ini: pada pemeriksaan
     * yang gagal, versi tersedia yang dilaporkan berasal dari pemeriksaan
     * sebelumnya.
     */
    private static function segarkan() {
        require_once ABSPATH . 'wp-admin/includes/update.php';

        // Kedua fungsi ini tidak menerima argumen pemaksa dan langsung
        // kembali bila last_checked lebih baru dari 12 jam (lihat
        // wp-includes/update.php). last_checked = 0 membuat pemeriksaan itu
        // gagal tanpa menyentuh isi transient lainnya.
        self::paksa_cek_ulang( 'update_plugins' );
        self::paksa_cek_ulang( 'update_themes' );

        // Core tidak butuh trik ini: argumen kedua wp_version_check() memang
        // pemaksa, dan fungsinya juga menyimpan ulang transient lama sebelum
        // menghubungi wordpress.org.
        wp_version_check( array(), true );
        wp_update_plugins();
        wp_update_themes();
    }

    private static function paksa_cek_ulang( $nama_transient ) {
        $lama = get_site_transient( $nama_transient );
        // Bukan objek berarti belum pernah ada pemeriksaan (atau isinya
        // rusak); fungsi update WordPress sendiri menggantinya dengan objek
        // baru tanpa last_checked, yang juga memaksa pemeriksaan.
        if ( is_object( $lama ) ) {
            $lama->last_checked = 0;
            set_site_transient( $nama_transient, $lama );
        }
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
