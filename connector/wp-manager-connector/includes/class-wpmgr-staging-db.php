<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Akses database untuk dorong. Pernyataan impor dijalankan lewat
 * mysqli_query() langsung, bukan $wpdb->query(): wpdb memeriksa ulang
 * charset setiap query yang memuat byte non-ASCII (strip_invalid_text),
 * yang untuk INSERT multi-megabyte berarti query SHOW FULL COLUMNS
 * tambahan dan regex atas seluruh isi.
 */
class WPMGR_Staging_Db {

    private $wpdb;

    public function __construct( $wpdb ) {
        $this->wpdb = $wpdb;
    }

    public function prefix() {
        return (string) $this->wpdb->prefix;
    }

    public function opsi() {
        return (string) $this->wpdb->options;
    }

    public function kueri( $sql ) {
        $dbh = $this->wpdb->dbh;
        if ( $dbh instanceof mysqli ) {
            $hasil = mysqli_query( $dbh, $sql );
            if ( false === $hasil ) {
                return WPMGR_Staging::bersih( mysqli_error( $dbh ), 300 );
            }
            if ( $hasil instanceof mysqli_result ) {
                mysqli_free_result( $hasil );
            }
            return true;
        }
        $hasil = $this->wpdb->query( $sql ); // phpcs:ignore WordPress.DB.PreparedSQL
        return false === $hasil ? WPMGR_Staging::bersih( (string) $this->wpdb->last_error, 300 ) : true;
    }

    public function kolom( $sql ) {
        return (array) $this->wpdb->get_col( $sql ); // phpcs:ignore WordPress.DB.PreparedSQL
    }

    public function nilai( $sql ) {
        return $this->wpdb->get_var( $sql ); // phpcs:ignore WordPress.DB.PreparedSQL
    }

    /** Satu baris sebagai array asosiatif, atau null; pemanggil memeriksa galat_terakhir(). */
    public function baris( $sql ) {
        $r = $this->wpdb->get_row( $sql, ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL
        return is_array( $r ) ? $r : null;
    }

    /**
     * wpdb::prepare() (WP >= 4.8.3) mengganti '%' di dalam argumen dengan
     * placeholder acak yang hanya dikembalikan oleh $wpdb->query(). kueri()
     * memakai mysqli_query() langsung, jadi pengembalian itu dilakukan di
     * sini: tanpa itu `LIKE 'id|%'` menjadi `LIKE 'id|{hash}'` dan
     * lepas_kunci()/penyegaran kunci diam-diam tidak mengenai baris apa pun.
     * Aman juga untuk nilai()/baris() (query() tidak menemukan placeholder lagi).
     */
    public function siapkan( $sql ) {
        $siap = call_user_func_array( array( $this->wpdb, 'prepare' ), func_get_args() );
        if ( is_string( $siap ) && method_exists( $this->wpdb, 'remove_placeholder_escape' ) ) {
            $siap = $this->wpdb->remove_placeholder_escape( $siap );
        }
        return $siap;
    }

    public function suka( $t ) {
        return $this->wpdb->esc_like( $t );
    }

    /**
     * Fix I5 (review putaran 1): kolom()/nilai() (get_col/get_var) tidak
     * membedakan "0 baris" dari "kueri gagal" lewat nilai baliknya saja --
     * wpdb sungguhan mengosongkan last_error di awal SETIAP query() baru
     * dan mengisinya hanya saat gagal (sama seperti dijelaskan di
     * WPMGR_Staging_TandaAir). Pemanggil WAJIB memeriksa ini SEGERA
     * setelah kolom()/nilai(), sebelum query lain berjalan di $wpdb yang
     * sama.
     */
    public function galat_terakhir() {
        return isset( $this->wpdb->last_error ) ? (string) $this->wpdb->last_error : '';
    }

    /**
     * Fix I2 (review putaran 1): prefix "asing" yang tumpang tindih dengan
     * $prefix -- pada database bersama, site LAIN dengan prefix lebih
     * panjang yang MEMPERPANJANG prefix site ini (mis. site ini 'wp_', site
     * lain 'wp_abc_') membuat tabelnya sendiri (mis. 'wp_abc_posts') lolos
     * pencocokan awalan sederhana terhadap 'wp_'. Terdeteksi lewat
     * keberadaan tabel OPSI milik prefix itu sendiri ('wp_abc_options') di
     * $semua_tabel -- tanda kuat itu memang site WordPress lain, bukan
     * sekadar tabel plugin site ini yang panjang namanya.
     */
    public static function prefix_asing( array $semua_tabel, $prefix ) {
        $prefix = (string) $prefix;
        $hasil  = array();
        foreach ( $semua_tabel as $t ) {
            $t = (string) $t;
            if ( strlen( $t ) > strlen( $prefix ) + 7 && 'options' === substr( $t, -7 ) && 0 === strpos( $t, $prefix ) ) {
                $kandidat = substr( $t, 0, -7 );
                if ( $kandidat !== $prefix && ! in_array( $kandidat, $hasil, true ) ) {
                    $hasil[] = $kandidat;
                }
            }
        }
        return $hasil;
    }

    /**
     * Fix I2 (review putaran 1): menyaring $semua_tabel supaya hanya yang
     * BENAR milik site dengan $prefix ini yang tersisa -- tabel di bawah
     * prefix asing yang tumpang tindih (prefix_asing()) dibuang, walau ia
     * sendiri lolos pencocokan awalan $prefix. Dipakai bersama oleh
     * WPMGR_Staging_Manifest::tabel(), daftar tabel snapshot, dan jurnal
     * tabel dorong (WPMGR_Staging_Dorong) -- satu tempat, satu definisi.
     */
    public static function tabel_milik_site( array $semua_tabel, $prefix ) {
        $prefix       = (string) $prefix;
        $prefix_asing = self::prefix_asing( $semua_tabel, $prefix );
        return array_values( array_filter( $semua_tabel, function ( $t ) use ( $prefix, $prefix_asing ) {
            $t = (string) $t;
            if ( 0 !== strpos( $t, $prefix ) ) {
                return false;
            }
            foreach ( $prefix_asing as $pa ) {
                if ( 0 === strpos( $t, $pa ) ) {
                    return false;
                }
            }
            return true;
        } ) );
    }
}
