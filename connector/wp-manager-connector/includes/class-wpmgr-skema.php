<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Tabel, migrasi, dan pemangkasan data pemantauan di sisi site.
 *
 * Migrasi dijalankan dari plugins_loaded dengan membandingkan versi skema,
 * BUKAN dari activation hook: activation hook tidak berjalan ketika plugin
 * di-update (termasuk lewat self-update dari dashboard), sehingga site yang
 * di-update tidak akan pernah mendapat tabel barunya.
 */
class WPMGR_Skema {

    const OPT_VERSI    = 'wpmgr_versi_skema';
    const HOOK_PANGKAS = 'wpmgr_pangkas_harian';
    const HARI_SIMPAN  = 30;
    const MU_PLUGIN    = 'wpmgr-penangkap.php';

    const TABEL = array( 'wpmgr_errors', 'wpmgr_logins', 'wpmgr_login_gagal', 'wpmgr_traffic', 'wpmgr_pengunjung' );

    public static function monitoring_mati() {
        return defined( 'WPMGR_DISABLE_MONITORING' ) && WPMGR_DISABLE_MONITORING;
    }

    /**
     * Fitur yang benar-benar tersedia di versi connector ini.
     *
     * Dashboard hanya menjadwalkan pengambilan untuk fitur yang diumumkan di
     * sini. Sebuah nama hanya boleh ditambahkan bersamaan dengan endpoint-nya:
     * mengumumkan fitur yang endpoint-nya belum ada membuat dashboard menerima
     * 404 rest_no_route dan salah menyimpulkan connector sudah dicabut.
     */
    public static function fitur( $monitoring_mati ) {
        return array();
    }

    /** Belum ada penangkap error di versi ini; dilaporkan null. */
    public static function mode_penangkap() {
        return null;
    }

    public static function perlu_migrasi( $versi_tersimpan, $versi_kode ) {
        return (int) $versi_tersimpan !== (int) $versi_kode;
    }

    /**
     * Pernyataan CREATE TABLE dalam format yang dituntut dbDelta(): satu kolom
     * per baris, dua spasi setelah PRIMARY KEY, dan KEY alih-alih INDEX.
     */
    public static function sql_tabel( $prefix, $charset ) {
        return array(
            "CREATE TABLE {$prefix}wpmgr_errors (
  id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  sidik_jari char(32) NOT NULL,
  tingkat varchar(10) NOT NULL,
  komponen_tipe varchar(12) NOT NULL,
  komponen_slug varchar(191) DEFAULT NULL,
  pesan text NOT NULL,
  file varchar(255) DEFAULT NULL,
  baris int(11) DEFAULT NULL,
  konteks text DEFAULT NULL,
  jumlah bigint(20) unsigned NOT NULL DEFAULT 1,
  pertama int(10) unsigned NOT NULL,
  terakhir int(10) unsigned NOT NULL,
  diubah int(10) unsigned NOT NULL,
  PRIMARY KEY  (id),
  UNIQUE KEY sidik_jari (sidik_jari),
  KEY diubah (diubah,id)
) {$charset};",
            "CREATE TABLE {$prefix}wpmgr_logins (
  id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  waktu int(10) unsigned NOT NULL,
  jenis varchar(12) NOT NULL,
  username varchar(60) NOT NULL,
  role varchar(60) DEFAULT NULL,
  ip varchar(45) DEFAULT NULL,
  lewat_cloudflare tinyint(1) NOT NULL DEFAULT 0,
  user_agent varchar(255) DEFAULT NULL,
  jalur varchar(12) NOT NULL,
  diubah int(10) unsigned NOT NULL,
  PRIMARY KEY  (id),
  KEY diubah (diubah,id)
) {$charset};",
            "CREATE TABLE {$prefix}wpmgr_login_gagal (
  id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  jam int(10) unsigned NOT NULL,
  ip varchar(45) NOT NULL DEFAULT '',
  username varchar(60) NOT NULL,
  jalur varchar(12) NOT NULL,
  jumlah int(10) unsigned NOT NULL DEFAULT 0,
  user_agent varchar(255) DEFAULT NULL,
  diubah int(10) unsigned NOT NULL,
  PRIMARY KEY  (id),
  UNIQUE KEY kunci (jam,ip,username,jalur),
  KEY diubah (diubah,id)
) {$charset};",
            "CREATE TABLE {$prefix}wpmgr_traffic (
  tanggal date NOT NULL,
  dimensi varchar(10) NOT NULL,
  kunci varchar(180) NOT NULL,
  kunjungan int(10) unsigned NOT NULL DEFAULT 0,
  pengunjung int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY  (tanggal,dimensi,kunci)
) {$charset};",
            "CREATE TABLE {$prefix}wpmgr_pengunjung (
  tanggal date NOT NULL,
  hash char(40) NOT NULL,
  hit int(10) unsigned NOT NULL DEFAULT 1,
  PRIMARY KEY  (tanggal,hash)
) {$charset};",
        );
    }

    public static function pastikan() {
        try {
            if ( ! self::perlu_migrasi( get_option( self::OPT_VERSI, 0 ), WPMGR_VERSI_SKEMA ) ) {
                return;
            }
            self::migrasi();
            update_option( self::OPT_VERSI, WPMGR_VERSI_SKEMA, true );
        } catch ( \Throwable $e ) {
            // Migrasi yang gagal tidak boleh merusak halaman site. Versi skema
            // tidak diperbarui, jadi migrasi dicoba lagi di request berikutnya.
            unset( $e );
        }
    }

    public static function migrasi() {
        global $wpdb;
        require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        dbDelta( self::sql_tabel( $wpdb->prefix, $wpdb->get_charset_collate() ) );
        if ( ! wp_next_scheduled( self::HOOK_PANGKAS ) ) {
            wp_schedule_event( time() + HOUR_IN_SECONDS, 'daily', self::HOOK_PANGKAS );
        }
    }

    /** Dipanggil WP-Cron harian: data site dibatasi 30 hari (spec §5.2). */
    public static function pangkas() {
        global $wpdb;
        $p       = $wpdb->prefix;
        $batas   = time() - self::HARI_SIMPAN * DAY_IN_SECONDS;
        $tanggal = wp_date( 'Y-m-d', $batas );
        $kemarin = wp_date( 'Y-m-d', time() - DAY_IN_SECONDS );

        $wpdb->query( $wpdb->prepare( "DELETE FROM {$p}wpmgr_errors WHERE terakhir < %d", $batas ) );
        $wpdb->query( $wpdb->prepare( "DELETE FROM {$p}wpmgr_logins WHERE waktu < %d", $batas ) );
        $wpdb->query( $wpdb->prepare( "DELETE FROM {$p}wpmgr_login_gagal WHERE jam < %d", $batas ) );
        $wpdb->query( $wpdb->prepare( "DELETE FROM {$p}wpmgr_traffic WHERE tanggal < %s", $tanggal ) );
        // Hash pengunjung hanya berarti untuk hari yang sedang dihitung; menyimpannya
        // lebih lama berarti pengunjung dapat dilacak lintas hari.
        $wpdb->query( $wpdb->prepare( "DELETE FROM {$p}wpmgr_pengunjung WHERE tanggal < %s", $kemarin ) );
    }

    public static function hapus_mu_plugin() {
        if ( defined( 'WPMU_PLUGIN_DIR' ) ) {
            $berkas = WPMU_PLUGIN_DIR . '/' . self::MU_PLUGIN;
            if ( file_exists( $berkas ) ) {
                @unlink( $berkas ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
        }
    }

    /** Dipanggil uninstall.php: tidak ada sisa data pemantauan setelah plugin dihapus. */
    public static function hapus_semua() {
        global $wpdb;
        foreach ( self::TABEL as $tabel ) {
            $wpdb->query( "DROP TABLE IF EXISTS {$wpdb->prefix}{$tabel}" ); // phpcs:ignore WordPress.DB.PreparedSQL
        }
        foreach ( array( 'wpmgr_site_id', 'wpmgr_secret', 'wpmgr_dashboard_url', self::OPT_VERSI,
                         'wpmgr_percayai_xff', 'wpmgr_garam' ) as $opsi ) {
            delete_option( $opsi );
        }
        wp_clear_scheduled_hook( self::HOOK_PANGKAS );
        self::hapus_mu_plugin();
    }
}
