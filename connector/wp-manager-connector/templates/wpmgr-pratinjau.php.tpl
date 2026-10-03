<?php
/**
 * Plugin Name: WP Manager — pengaman pratinjau VPS
 * Description: Dipasang dashboard WP Manager selama pratinjau pindah hosting. Email diblokir, mesin pencari ditolak, dan tautan diarahkan ke host pratinjau. Diam sepenuhnya di luar pratinjau.
 */
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}
// Tanpa konstanta dari wp-config.php mode pratinjau, berkas ini diam
// sepenuhnya: aktivasi mencabut konstanta itu, jadi berkas yang tertinggal
// sesudah aktivasi tidak berbuat apa-apa (spec Lapis 4 §7.6).
if ( ! defined( 'WPMGR_PRATINJAU' ) || ! WPMGR_PRATINJAU ) {
    return;
}

// Email tidak pernah keluar dari salinan pratinjau. WordPress >= 5.7:
// pre_wp_mail menghentikan wp_mail() sebelum PHPMailer disentuh.
add_filter( 'pre_wp_mail', function () {
    return false;
}, PHP_INT_MAX );

// WordPress lebih lama: penerima dikosongkan di saat terakhir (sesudah
// plugin SMTP mengatur PHPMailer), sehingga PHPMailer menolak mengirim.
// Argumen diperiksa dulu: plugin lain yang memanggil kait ini dengan nilai
// aneh tidak boleh membuat fatal error di seluruh situs.
add_action( 'phpmailer_init', function ( $phpmailer ) {
    if ( is_object( $phpmailer ) && method_exists( $phpmailer, 'clearAllRecipients' ) ) {
        $phpmailer->clearAllRecipients();
    }
}, PHP_INT_MAX );

// Mesin pencari ditolak tanpa mengubah database (blog_public tetap nilai asli).
add_filter( 'pre_option_blog_public', function () {
    return '0';
} );

add_filter( 'wp_robots', function ( $robots ) {
    if ( ! is_array( $robots ) ) {
        $robots = array();
    }
    $robots['noindex']  = true;
    $robots['nofollow'] = true;
    return $robots;
} );

add_action( 'admin_notices', function () {
    // esc_html() walau literal tetap: berkas ini tinggal di salinan site
    // client dan mengikuti aturan "semua string lewat esc()" dashboard.
    echo '<div class="notice notice-warning"><p><strong>' . esc_html( 'PRATINJAU VPS' ) . '</strong> — '
        . esc_html( 'email diblokir, cron mati. Ini salinan pindah hosting; situs asli masih dilayani hosting lama.' )
        . '</p></div>';
} );

add_action( 'admin_bar_menu', function ( $bar ) {
    if ( is_object( $bar ) && method_exists( $bar, 'add_node' ) ) {
        $bar->add_node( array( 'id' => 'wpmgr-pratinjau', 'title' => 'PRATINJAU VPS — email diblokir, cron mati' ) );
    }
}, 1 );

// Hanya untuk request ke host pratinjau: URL domain asli di keluaran
// (termasuk bentuk ber-escape JSON) diganti host pratinjau. Database tidak
// diubah; tanpa ini gambar dan tautan di konten mengarah ke hosting lama.
// Header Host datang dari klien, jadi hanya dibandingkan persis (bentuk
// lain tidak memicu apa pun), dan penggantinya hanya dua konstanta dari
// wp-config: konten situs tidak bisa menyisipkan apa pun lewat sini.
if ( defined( 'WPMGR_PRATINJAU_HOST' ) && defined( 'WPMGR_DOMAIN' )
    && isset( $_SERVER['HTTP_HOST'] ) && WPMGR_PRATINJAU_HOST === $_SERVER['HTTP_HOST'] ) {
    ob_start( function ( $html ) {
        if ( ! is_string( $html ) ) {
            return $html;
        }
        $ke    = 'https://' . WPMGR_PRATINJAU_HOST;
        $ganti = array();
        foreach ( array( 'https://www.' . WPMGR_DOMAIN, 'https://' . WPMGR_DOMAIN ) as $asal ) {
            $ganti[ $asal ] = $ke;
            $ganti[ str_replace( '/', '\\/', $asal ) ] = str_replace( '/', '\\/', $ke );
        }
        // strtr mendahulukan kunci terpanjang, jadi www.<domain> tidak terpotong.
        return strtr( $html, $ganti );
    } );
}
