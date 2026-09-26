<?php
/**
 * Plugin Name: WP Manager Connector
 * Description: Menghubungkan site ini ke dashboard WP Manager untuk pemindaian, update, dan pemantauan terpusat.
 * Version:     3.0.0
 * Requires at least: 5.5
 * Requires PHP: 7.4
 * License:     GPL-2.0-or-later
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

define( 'WPMGR_VERSION', '3.0.0' );
define( 'WPMGR_VERSI_SKEMA', 3 );
define( 'WPMGR_JENDELA_DETIK', 300 );
define( 'WPMGR_NONCE_TTL', 600 );
define( 'WPMGR_SSO_TTL', 120 );
define( 'WPMGR_USER_LOGIN', 'wpmgr' );
define( 'WPMGR_FILE', __FILE__ );
define( 'WPMGR_DIR', plugin_dir_path( __FILE__ ) );

require_once WPMGR_DIR . 'includes/class-wpmgr-signing.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-settings.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-ip.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-login.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-events.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-traffic.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-skema.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-penangkap.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-inventory.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-updater.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-selfupdate.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-rest.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-sso.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-staging-path.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-staging-paket.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-staging-manifest.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-staging-file.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-staging-tabel.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-staging-tanda-air.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-staging.php';

add_action( 'plugins_loaded', array( 'WPMGR_Skema', 'pastikan' ) );
add_action( WPMGR_Skema::HOOK_PANGKAS, array( 'WPMGR_Skema', 'pangkas' ) );
add_action( 'rest_api_init', array( 'WPMGR_REST', 'daftarkan_route' ) );
add_filter( 'rest_post_dispatch', array( 'WPMGR_REST', 'tambah_header_anti_cache' ), 10, 3 );
add_filter( 'rest_pre_serve_request', array( 'WPMGR_Staging', 'sajikan_biner' ), 10, 4 );
add_action( 'admin_menu', array( 'WPMGR_Settings', 'daftarkan_menu' ) );
add_action( 'admin_init', array( 'WPMGR_Settings', 'tangani_simpan' ) );
add_action( 'init', array( 'WPMGR_SSO', 'tangani_permintaan' ), 1 );

// Pemasang pemantauan. Bila mu-plugin sudah memasang penangkap, panggilan
// pertama tidak melakukan apa-apa; bila belum (mu-plugins terkunci), inilah
// pemasangan paling awal yang bisa dilakukan dari plugin biasa.
if ( ! WPMGR_Skema::monitoring_mati() ) {
    WPMGR_Penangkap::pasang();
    WPMGR_Login::pasang();
    WPMGR_Traffic::pasang();
}
