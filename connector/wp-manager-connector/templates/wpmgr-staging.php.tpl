<?php
/**
 * Plugin Name: WP Manager — pengaman staging
 * Description: Dipasang dashboard WP Manager di salinan staging. Email ditangkap, mesin pencari ditolak, dan perubahan dicatat. Tidak berbuat apa-apa di luar staging.
 */
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}
// Bila berkas ini ikut tersalin ke produksi, tanpa konstanta dari
// wp-config.php staging ia diam sepenuhnya.
if ( ! defined( 'WPMGR_STAGING' ) || ! WPMGR_STAGING ) {
    return;
}
define( 'WPMGR_STAGING_NAMA', '__WPMGR_NAMA__' );

add_action( 'phpmailer_init', function ( $phpmailer ) {
    // Prioritas terakhir: plugin SMTP produksi (WP Mail SMTP dan sejenisnya)
    // mengatur PHPMailer lebih dulu, lalu ditimpa ke Mailpit di sini.
    $phpmailer->isSMTP();
    $phpmailer->Host        = 'wpmgr-stg-mail';
    $phpmailer->Port        = 1025;
    $phpmailer->SMTPAuth    = false;
    $phpmailer->SMTPSecure  = '';
    $phpmailer->SMTPAutoTLS = false;
    $phpmailer->Username    = '';
    $phpmailer->Password    = '';
    $phpmailer->addCustomHeader( 'X-Tags', WPMGR_STAGING_NAMA );
}, PHP_INT_MAX );

add_filter( 'pre_option_blog_public', function () {
    return '0';
} );

add_action( 'admin_notices', function () {
    // esc_html() dipakai walau kedua string berupa literal tetap: berkas
    // ini berasal dari site client (mungkin disusupi) dan ikut dipaketkan
    // apa adanya, jadi ditulis mengikuti aturan "semua string dirender lewat
    // esc()" seperti bagian dashboard lainnya, bukan dikecualikan.
    echo '<div class="notice notice-warning"><p><strong>' . esc_html( 'STAGING' ) . '</strong> — '
        . esc_html( 'payment gateway dan API pihak ketiga memakai kredensial produksi. Email ditangkap dashboard dan tidak dikirim, kecuali plugin yang mengirim lewat API HTTP penyedia email.' )
        . '</p></div>';
} );

add_action( 'admin_bar_menu', function ( $bar ) {
    $bar->add_node( array( 'id' => 'wpmgr-staging', 'title' => 'STAGING' ) );
}, 1 );

// Penanda "staging diubah sejak tarik terakhir" untuk dashboard (spec §8.1
// langkah 2). /wpmgr-log adalah bind mount milik dashboard di container
// staging. Path, nama berkas, dan isi (hanya timestamp) tetap tanpa
// masukan pengguna: kode di berkas ini berasal dari site client yang
// bisa saja disusupi, jadi tidak boleh ada cara menulis ke path lain.
$wpmgr_tandai_diubah = function () {
    @file_put_contents( '/wpmgr-log/diubah', (string) time() ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
};
foreach ( array( 'save_post', 'deleted_post', 'activated_plugin', 'deactivated_plugin', 'upgrader_process_complete',
                 'switch_theme', 'customize_save_after', 'wp_update_nav_menu', 'add_attachment', 'edit_attachment' ) as $wpmgr_kait ) {
    add_action( $wpmgr_kait, $wpmgr_tandai_diubah );
}
unset( $wpmgr_kait );
