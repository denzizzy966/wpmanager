<?php
/**
 * Plugin Name: WP Manager Connector
 * Description: Menghubungkan site ini ke dashboard WP Manager untuk pemindaian dan update terpusat.
 * Version:     1.0.0
 * Requires PHP: 7.4
 * License:     GPL-2.0-or-later
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

define( 'WPMGR_VERSION', '1.0.0' );
define( 'WPMGR_JENDELA_DETIK', 300 );
define( 'WPMGR_NONCE_TTL', 600 );
define( 'WPMGR_SSO_TTL', 120 );
define( 'WPMGR_USER_LOGIN', 'wpmgr' );
define( 'WPMGR_DIR', plugin_dir_path( __FILE__ ) );

require_once WPMGR_DIR . 'includes/class-wpmgr-signing.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-settings.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-inventory.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-updater.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-rest.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-sso.php';

add_action( 'rest_api_init', array( 'WPMGR_REST', 'daftarkan_route' ) );
add_action( 'admin_menu', array( 'WPMGR_Settings', 'daftarkan_menu' ) );
add_action( 'admin_init', array( 'WPMGR_Settings', 'tangani_simpan' ) );
add_action( 'init', array( 'WPMGR_SSO', 'tangani_permintaan' ), 1 );
