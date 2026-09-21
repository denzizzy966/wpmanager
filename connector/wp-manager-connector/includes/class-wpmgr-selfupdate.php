<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Connector memasang versi barunya sendiri dari zip yang dikirim dashboard.
 *
 * Paket dikirim di dalam body, bukan diunduh site: sebagian shared hosting
 * memblokir koneksi keluar, dan arah dashboard -> site sudah terbukti bisa
 * dilalui saat pairing. Tanda tangan HMAC request mencakup hash body, dan
 * hash isi zip diperiksa lagi di sini sebelum apa pun ditulis ke disk.
 */
class WPMGR_SelfUpdate {

    private static function galat( $kode, $pesan, $status ) {
        return new WP_Error( $kode, $pesan, array( 'status' => $status ) );
    }

    public static function periksa_paket( $p ) {
        if ( ! is_array( $p ) ) {
            return self::galat( 'wpmgr_payload_salah', 'Payload self-update bukan objek.', 400 );
        }
        foreach ( array( 'versi', 'sha256', 'zip_b64' ) as $kunci ) {
            if ( ! isset( $p[ $kunci ] ) || ! is_string( $p[ $kunci ] ) || '' === $p[ $kunci ] ) {
                return self::galat( 'wpmgr_payload_salah', 'Payload self-update tidak lengkap.', 400 );
            }
        }
        // Versi ikut tampil di log dan dibandingkan dengan version_compare();
        // hanya bentuk nomor rilis yang diterima.
        if ( ! preg_match( '/^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$/', $p['versi'] ) ) {
            return self::galat( 'wpmgr_payload_salah', 'Versi paket tidak sah.', 400 );
        }
        $isi = base64_decode( $p['zip_b64'], true );
        if ( false === $isi || '' === $isi ) {
            return self::galat( 'wpmgr_paket_rusak', 'Isi paket tidak dapat dibaca.', 400 );
        }
        if ( ! hash_equals( strtolower( $p['sha256'] ), hash( 'sha256', $isi ) ) ) {
            return self::galat( 'wpmgr_paket_rusak', 'Hash paket tidak cocok dengan isinya.', 400 );
        }
        return array( $p['versi'], $isi );
    }

    public static function perlu_pasang( $terpasang, $versi ) {
        return version_compare( $versi, $terpasang, '>' );
    }

    /** Dibaca dari disk: konstanta WPMGR_VERSION di request ini milik kode lama. */
    public static function versi_di_disk() {
        $data = get_file_data( WPMGR_FILE, array( 'versi' => 'Version' ) );
        return (string) $data['versi'];
    }

    public static function jalankan( $p ) {
        $hasil = self::periksa_paket( $p );
        if ( is_wp_error( $hasil ) ) {
            return $hasil;
        }
        list( $versi, $isi ) = $hasil;

        $sebelum = self::versi_di_disk();
        if ( ! self::perlu_pasang( $sebelum, $versi ) ) {
            return array(
                'ok'            => true,
                'versi_sebelum' => $sebelum,
                'versi_sesudah' => $sebelum,
                'pesan'         => 'sudah di versi tersebut atau lebih baru',
            );
        }

        require_once ABSPATH . 'wp-admin/includes/file.php';
        require_once ABSPATH . 'wp-admin/includes/misc.php';
        require_once ABSPATH . 'wp-admin/includes/plugin.php';
        require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';

        // Lock yang sama dengan /update: memasang connector sambil meng-upgrade
        // plugin lain berarti dua upgrader berebut wp-content/upgrade.
        if ( WPMGR_Updater::sedang_sibuk()
            || ! WP_Upgrader::create_lock( WPMGR_Updater::NAMA_KUNCI, WPMGR_Updater::DETIK_KUNCI ) ) {
            return WPMGR_Updater::galat_sibuk();
        }

        $berkas = wp_tempnam( 'wpmgr-connector.zip' );
        try {
            ignore_user_abort( true );
            if ( false === file_put_contents( $berkas, $isi ) ) {
                return self::galat( 'wpmgr_pasang_gagal', 'Paket tidak dapat ditulis ke direktori sementara.', 500 );
            }

            $skin     = new Automatic_Upgrader_Skin();
            $upgrader = new Plugin_Upgrader( $skin );
            // install() dengan overwrite_package, bukan upgrade(): upgrade()
            // menonaktifkan plugin yang aktif di luar WP-cron (bug C1 Lapis 1),
            // dan connector yang nonaktif tidak bisa menjawab siapa pun lagi.
            $r = $upgrader->install( $berkas, array( 'overwrite_package' => true ) );

            if ( is_wp_error( $r ) ) {
                return self::galat( 'wpmgr_pasang_gagal', $r->get_error_message(), 500 );
            }
            if ( ! $r ) {
                $pesan = implode( ' | ', (array) $skin->get_upgrade_messages() );
                return self::galat( 'wpmgr_pasang_gagal',
                    $pesan ? $pesan : 'Pemasangan mengembalikan false tanpa pesan.', 500 );
            }

            return array(
                'ok'            => true,
                'versi_sebelum' => $sebelum,
                'versi_sesudah' => self::versi_di_disk(),
                'pesan'         => implode( ' | ', (array) $skin->get_upgrade_messages() ),
            );
        } finally {
            if ( file_exists( $berkas ) ) {
                @unlink( $berkas ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
            WP_Upgrader::release_lock( WPMGR_Updater::NAMA_KUNCI );
        }
    }
}
