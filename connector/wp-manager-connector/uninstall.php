<?php
if ( ! defined( 'WP_UNINSTALL_PLUGIN' ) ) {
    exit;
}

require_once __DIR__ . '/includes/class-wpmgr-skema.php';
WPMGR_Skema::hapus_semua();
