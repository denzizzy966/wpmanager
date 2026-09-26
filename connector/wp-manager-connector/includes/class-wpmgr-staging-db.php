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

    public function siapkan( $sql ) {
        return call_user_func_array( array( $this->wpdb, 'prepare' ), func_get_args() );
    }

    public function suka( $t ) {
        return $this->wpdb->esc_like( $t );
    }
}
