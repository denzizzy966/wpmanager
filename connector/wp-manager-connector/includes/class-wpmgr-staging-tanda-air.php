<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Tanda air data baru produksi (spec §8.2): angka yang cukup untuk
 * mengatakan "ada pesanan/komentar/user/isian form baru sejak staging
 * ditarik" tanpa membandingkan isi tabel. Sumber hanya dilaporkan bila
 * tabelnya ada, supaya plugin yang tidak terpasang tidak tampak sebagai
 * "0 entri" yang kemudian dibandingkan -- KECUALI pesanan_posts (lihat
 * catatan fix R6 #1 di bawah), yang sengaja SELALU dilaporkan.
 *
 * Setiap panggilan ke $wpdb (get_row/get_var, termasuk yang tersembunyi di
 * dalam cari_tabel()) diperiksa lewat $wpdb->last_error SEGERA sesudahnya,
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
 *
 * Fix round 1 (review controller, ruling R6) -- deviasi disengaja dari
 * brief:
 * 1. (Kritis) Sumber pesanan HPOS (`wc_orders`) dan posts (`post_type IN
 *    ('shop_order', ...)`) sekarang dilaporkan TERPISAH dan INDEPENDEN
 *    (`pesanan_hpos`, `pesanan_posts`), bukan salah satu menggantikan yang
 *    lain. Situs yang pernah mencoba HPOS lalu kembali ke posts (dengan
 *    sinkronisasi mati) terus menulis pesanan BARU hanya ke `posts`
 *    sementara `wc_orders` beku pada nilai lama; mendahulukan HPOS seperti
 *    versi sebelumnya membuat pesanan baru semacam itu tidak pernah
 *    terlihat, dan dorong timpa penuh bisa menghapusnya. `pesanan_posts`
 *    SELALU dihitung (tabel posts selalu ada; hasilnya boleh 0) --
 *    `pesanan_hpos` tetap hanya dilaporkan bila `wc_orders` ada.
 * 2. (Penting) Kedua kueri pesanan sekarang menyertakan
 *    `shop_order_refund`, bukan hanya `shop_order` -- refund pada pesanan
 *    lama tetap menggerakkan maks_id, jadi tidak lagi tak terlihat.
 * 3. (Penting) Deteksi tabel (cari_tabel()/ada_tabel()) tidak lagi
 *    case-sensitive: server dengan lower_case_table_names=1 mengembalikan
 *    nama tabel dalam huruf kecil apa pun huruf permintaannya, dan
 *    perbandingan `===` yang lama membuat tabel seperti itu tampak "tidak
 *    ada". Kueri lanjutan memakai nama PERSIS seperti yang dikembalikan
 *    server (cari_tabel()), bukan nama yang kita minta.
 * 4. (Penting) baris_sah() menolak baris agregat yang null/bukan array
 *    atau kehilangan kolom maks/jumlah yang diminta dengan 500 KERAS --
 *    sebelumnya maks_jumlah() diam-diam menjadikannya 0/0, yang untuk
 *    SELECT agregat tanpa GROUP BY (yang seharusnya SELALU tepat satu
 *    baris) hanya masuk akal sebagai kondisi anomali, bukan "0 baris".
 * 5. (Minor) $wpdb->users/posts/comments dipakai apa adanya (menghormati
 *    CUSTOM_USER_TABLE), bukan disusun manual dari prefix; nama tabel form
 *    di-backtick-quote; diubah_sejak ikut mengecualikan post_status
 *    'auto-draft', sama seperti agregat posts utama.
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

    /**
     * Fix item 3 (Penting), review putaran 1: mengembalikan nama tabel
     * PERSIS seperti yang dikembalikan server (null bila tidak ada),
     * dibandingkan TANPA peduli huruf besar/kecil -- server dengan
     * lower_case_table_names=1 mengembalikan nama huruf kecil apa pun
     * huruf pattern SHOW TABLES LIKE yang dikirim. Pemanggil WAJIB memakai
     * nilai balik ini (bukan $nama yang diminta) untuk kueri berikutnya.
     */
    private static function cari_tabel( $wpdb, $nama ) {
        $hasil = $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $wpdb->esc_like( $nama ) ) );
        if ( ! is_string( $hasil ) || 0 !== strcasecmp( $hasil, (string) $nama ) ) {
            return null;
        }
        return $hasil;
    }

    public static function ada_tabel( $wpdb, $nama ) {
        return null !== self::cari_tabel( $wpdb, $nama );
    }

    /**
     * Fix item 4 (Penting), review putaran 1: SELECT agregat (MAX/COUNT)
     * tanpa GROUP BY SELALU tepat satu baris pada query yang sukses --
     * null/bukan array, atau kehilangan salah satu kolom $kunci, berarti
     * sesuatu yang tidak terduga terjadi dan TIDAK BOLEH ditafsirkan
     * sebagai "0 baris ditemukan" oleh maks_jumlah().
     */
    private static function baris_sah( $baris, array $kunci = array( 'maks', 'jumlah' ) ) {
        if ( ! is_array( $baris ) ) {
            return false;
        }
        foreach ( $kunci as $k ) {
            if ( ! array_key_exists( $k, $baris ) ) {
                return false;
            }
        }
        return true;
    }

    /** Hanya dipanggil setelah baris_sah() -- $baris dijamin punya 'maks'/'jumlah'. */
    private static function maks_jumlah( array $baris ) {
        return array(
            'maks_id' => (int) $baris['maks'],
            'jumlah'  => (int) $baris['jumlah'],
        );
    }

    public static function kumpulkan( $wpdb, $posts_sejak, $posts_maks ) {
        $p      = $wpdb->prefix;
        $sumber = array();

        $posts = $wpdb->get_row(
            "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah, MAX(post_modified_gmt) AS diubah FROM {$wpdb->posts}"
            . " WHERE post_type NOT IN ('revision','shop_order','shop_order_refund','shop_order_placehold','flamingo_inbound')"
            . " AND post_status <> 'auto-draft'",
            ARRAY_A
        );
        if ( self::galat_query( $wpdb ) || ! self::baris_sah( $posts, array( 'maks', 'jumlah', 'diubah' ) ) ) {
            return self::galat_500();
        }
        $sumber['posts']                 = self::maks_jumlah( $posts );
        $sumber['posts']['diubah']       = ( null !== $posts['diubah'] ) ? (string) $posts['diubah'] : '';
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
                "SELECT COUNT(*) FROM {$wpdb->posts} WHERE ID <= %d AND post_modified_gmt > %s"
                . " AND post_type NOT IN ('revision','shop_order','shop_order_refund','shop_order_placehold','flamingo_inbound')"
                // Fix item 5c, review putaran 1: sama seperti agregat posts
                // utama -- auto-draft bukan post sungguhan, jadi perubahannya
                // tidak dihitung sebagai "post lama yang diubah".
                . " AND post_status <> 'auto-draft'",
                (int) $posts_maks, $sejak
            ) );
            if ( self::galat_query( $wpdb ) ) {
                return self::galat_500();
            }
            $sumber['posts']['diubah_sejak'] = (int) $diubah_sejak;
        }

        $comments = $wpdb->get_row( "SELECT MAX(comment_ID) AS maks, COUNT(*) AS jumlah FROM {$wpdb->comments}", ARRAY_A );
        if ( self::galat_query( $wpdb ) || ! self::baris_sah( $comments ) ) {
            return self::galat_500();
        }
        $sumber['comments'] = self::maks_jumlah( $comments );

        // Fix item 5a, review putaran 1: properti $wpdb->users (bukan
        // {$p}users manual) supaya CUSTOM_USER_TABLE dihormati.
        $users = $wpdb->get_row( "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah FROM {$wpdb->users}", ARRAY_A );
        if ( self::galat_query( $wpdb ) || ! self::baris_sah( $users ) ) {
            return self::galat_500();
        }
        $sumber['users'] = self::maks_jumlah( $users );

        // pesanan_hpos: hanya bila tabel HPOS `wc_orders` ada.
        $nama_hpos = self::cari_tabel( $wpdb, $p . 'wc_orders' );
        if ( self::galat_query( $wpdb ) ) {
            return self::galat_500();
        }
        if ( null !== $nama_hpos ) {
            // Fix item 2, review putaran 1: refund (shop_order_refund) ikut
            // disertakan -- refund pada pesanan lama tetap menggerakkan
            // maks_id, bukan tak terlihat.
            $hpos = $wpdb->get_row(
                "SELECT MAX(id) AS maks, COUNT(*) AS jumlah FROM {$nama_hpos}"
                . " WHERE type IN ('shop_order','shop_order_refund')", ARRAY_A );
            if ( self::galat_query( $wpdb ) || ! self::baris_sah( $hpos ) ) {
                return self::galat_500();
            }
            $sumber['pesanan_hpos'] = self::maks_jumlah( $hpos );
        }

        // Fix item 1 (Kritis), review putaran 1: pesanan_posts SELALU
        // dihitung (tabel posts selalu ada; hasilnya boleh 0), TERLEPAS
        // dari ada/tidaknya wc_orders -- lihat catatan di docblock kelas.
        $posts_pesanan = $wpdb->get_row(
            "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah FROM {$wpdb->posts}"
            . " WHERE post_type IN ('shop_order','shop_order_refund')", ARRAY_A );
        if ( self::galat_query( $wpdb ) || ! self::baris_sah( $posts_pesanan ) ) {
            return self::galat_500();
        }
        $sumber['pesanan_posts'] = self::maks_jumlah( $posts_pesanan );

        foreach ( self::FORM as $kunci => $def ) {
            $nama_tabel = self::cari_tabel( $wpdb, $p . $def[0] );
            if ( self::galat_query( $wpdb ) ) {
                return self::galat_500();
            }
            if ( null !== $nama_tabel ) {
                // Fix item 5b, review putaran 1: nama tabel di-backtick-quote,
                // sama seperti nama kolomnya.
                $baris = $wpdb->get_row(
                    "SELECT MAX(`{$def[1]}`) AS maks, COUNT(*) AS jumlah FROM `{$nama_tabel}`", ARRAY_A );
                if ( self::galat_query( $wpdb ) || ! self::baris_sah( $baris ) ) {
                    return self::galat_500();
                }
                $sumber[ $kunci ] = self::maks_jumlah( $baris );
            }
        }

        $flamingo = $wpdb->get_row(
            "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah FROM {$wpdb->posts} WHERE post_type = 'flamingo_inbound'", ARRAY_A );
        if ( self::galat_query( $wpdb ) || ! self::baris_sah( $flamingo ) ) {
            return self::galat_500();
        }
        $flamingo = self::maks_jumlah( $flamingo );
        if ( $flamingo['jumlah'] > 0 ) {
            $sumber['flamingo'] = $flamingo;
        }

        return array( 'sumber' => $sumber, 'diambil' => time() );
    }
}
