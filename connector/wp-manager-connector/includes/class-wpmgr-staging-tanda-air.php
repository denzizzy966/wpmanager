<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Tanda air data baru produksi (spec §8.2): angka yang cukup untuk
 * mengatakan "ada pesanan/komentar/user/isian form baru sejak staging
 * ditarik" tanpa membandingkan isi tabel. Sumber hanya dilaporkan bila
 * tabelnya ada, supaya plugin yang tidak terpasang tidak tampak sebagai
 * "0 entri" yang kemudian dibandingkan.
 *
 * Setiap panggilan ke $wpdb (get_row/get_var, termasuk yang tersembunyi di
 * dalam ada_tabel()) diperiksa lewat $wpdb->last_error SEGERA sesudahnya,
 * bukan sekali di akhir. wpdb sungguhan mengosongkan last_error di awal
 * SETIAP query() baru, jadi galat pada satu query bisa "tertutupi" oleh
 * query lain yang berhasil sesudahnya bila hanya diperiksa belakangan --
 * dan get_var()/get_row() sama-sama mengembalikan null baik saat 0 baris
 * (yang untuk agregat SELECT tanpa GROUP BY seharusnya tidak pernah
 * terjadi) maupun saat query gagal, jadi last_error adalah satu-satunya
 * sinyal yang bisa membedakan keduanya. Galat apa pun di sini menghentikan
 * seluruh pengumpulan dengan 500 KERAS (bukan watermark sebagian yang
 * tampak lengkap): tanda air ini menjaga dorong timpa penuh dari menghapus
 * pesanan/komentar/user produksi baru, jadi ia tidak boleh terlihat sah
 * padahal sebagian datanya hilang akibat galat query.
 */
class WPMGR_Staging_TandaAir {

    const FORM = array(
        'gravity_forms' => array( 'gf_entry', 'id' ),
        'wpforms'       => array( 'wpforms_entries', 'entry_id' ),
        'fluent_forms'  => array( 'fluentform_submissions', 'id' ),
    );

    private static function galat_query( $wpdb ) {
        return isset( $wpdb->last_error ) && '' !== (string) $wpdb->last_error;
    }

    private static function galat_500() {
        return WPMGR_Staging::galat( 'wpmgr_staging_tanda_air',
            'Tanda air tidak dapat dibaca dari database.', 500 );
    }

    public static function ada_tabel( $wpdb, $nama ) {
        return $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $wpdb->esc_like( $nama ) ) ) === $nama;
    }

    private static function maks_jumlah( $baris ) {
        $baris = is_array( $baris ) ? $baris : array();
        return array(
            'maks_id' => isset( $baris['maks'] ) ? (int) $baris['maks'] : 0,
            'jumlah'  => isset( $baris['jumlah'] ) ? (int) $baris['jumlah'] : 0,
        );
    }

    public static function kumpulkan( $wpdb, $posts_sejak, $posts_maks ) {
        $p      = $wpdb->prefix;
        $sumber = array();

        $posts = $wpdb->get_row(
            "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah, MAX(post_modified_gmt) AS diubah FROM {$p}posts"
            . " WHERE post_type NOT IN ('revision','shop_order','shop_order_refund','shop_order_placehold','flamingo_inbound')"
            . " AND post_status <> 'auto-draft'",
            ARRAY_A
        );
        if ( self::galat_query( $wpdb ) ) {
            return self::galat_500();
        }
        $sumber['posts']                 = self::maks_jumlah( $posts );
        $sumber['posts']['diubah']       = ( is_array( $posts ) && null !== $posts['diubah'] ) ? (string) $posts['diubah'] : '';
        $sumber['posts']['diubah_sejak'] = null;

        // posts_sejak lewat query string (tidak tercakup HMAC Lapis 1):
        // hanya format 'YYYY-MM-DD HH:MM:SS' yang diterima -- apa pun di
        // luar itu (termasuk percobaan suntik SQL semacam
        // "2026-09-20' OR '1'='1") membuat diubah_sejak tetap null TANPA
        // query tambahan, bukan ditolak 400 keras: parameter ini hanya
        // memperkaya satu angka informasional, jadi kegagalan validasinya
        // paling buruk adalah angka yang tidak dihitung, bukan permintaan
        // yang gagal total. Nilainya juga selalu lewat $wpdb->prepare()
        // (placeholder %s), jadi walau formatnya lolos regex, ia tidak
        // pernah ditempel mentah ke SQL.
        $sejak = (string) $posts_sejak;
        if ( 1 === preg_match( '/^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\z/', $sejak ) && (int) $posts_maks > 0 ) {
            $diubah_sejak = $wpdb->get_var( $wpdb->prepare(
                "SELECT COUNT(*) FROM {$p}posts WHERE ID <= %d AND post_modified_gmt > %s"
                . " AND post_type NOT IN ('revision','shop_order','shop_order_refund','shop_order_placehold','flamingo_inbound')",
                (int) $posts_maks, $sejak
            ) );
            if ( self::galat_query( $wpdb ) ) {
                return self::galat_500();
            }
            $sumber['posts']['diubah_sejak'] = (int) $diubah_sejak;
        }

        $comments = $wpdb->get_row( "SELECT MAX(comment_ID) AS maks, COUNT(*) AS jumlah FROM {$p}comments", ARRAY_A );
        if ( self::galat_query( $wpdb ) ) {
            return self::galat_500();
        }
        $sumber['comments'] = self::maks_jumlah( $comments );

        $users = $wpdb->get_row( "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah FROM {$p}users", ARRAY_A );
        if ( self::galat_query( $wpdb ) ) {
            return self::galat_500();
        }
        $sumber['users'] = self::maks_jumlah( $users );

        $ada_hpos = self::ada_tabel( $wpdb, $p . 'wc_orders' );
        if ( self::galat_query( $wpdb ) ) {
            return self::galat_500();
        }
        if ( $ada_hpos ) {
            $pesanan = $wpdb->get_row(
                "SELECT MAX(id) AS maks, COUNT(*) AS jumlah FROM {$p}wc_orders WHERE type = 'shop_order'", ARRAY_A );
            if ( self::galat_query( $wpdb ) ) {
                return self::galat_500();
            }
            $sumber['pesanan']           = self::maks_jumlah( $pesanan );
            $sumber['pesanan']['sumber'] = 'hpos';
        } else {
            $ada_legacy = self::ada_tabel( $wpdb, $p . 'woocommerce_order_items' );
            if ( self::galat_query( $wpdb ) ) {
                return self::galat_500();
            }
            if ( $ada_legacy ) {
                $pesanan = $wpdb->get_row(
                    "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah FROM {$p}posts WHERE post_type IN ('shop_order')", ARRAY_A );
                if ( self::galat_query( $wpdb ) ) {
                    return self::galat_500();
                }
                $sumber['pesanan']           = self::maks_jumlah( $pesanan );
                $sumber['pesanan']['sumber'] = 'posts';
            }
        }

        foreach ( self::FORM as $kunci => $def ) {
            $ada = self::ada_tabel( $wpdb, $p . $def[0] );
            if ( self::galat_query( $wpdb ) ) {
                return self::galat_500();
            }
            if ( $ada ) {
                $baris = $wpdb->get_row(
                    "SELECT MAX(`{$def[1]}`) AS maks, COUNT(*) AS jumlah FROM {$p}{$def[0]}", ARRAY_A );
                if ( self::galat_query( $wpdb ) ) {
                    return self::galat_500();
                }
                $sumber[ $kunci ] = self::maks_jumlah( $baris );
            }
        }

        $flamingo = $wpdb->get_row(
            "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah FROM {$p}posts WHERE post_type = 'flamingo_inbound'", ARRAY_A );
        if ( self::galat_query( $wpdb ) ) {
            return self::galat_500();
        }
        $flamingo = self::maks_jumlah( $flamingo );
        if ( $flamingo['jumlah'] > 0 ) {
            $sumber['flamingo'] = $flamingo;
        }

        return array( 'sumber' => $sumber, 'diambil' => time() );
    }
}
