<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * /events: error, login, dan login gagal yang berubah sejak kursor terakhir.
 *
 * Kursor buram bagi dashboard; ia hanya mengembalikannya pada pengambilan
 * berikutnya. Setiap tabel punya posisinya sendiri berupa (diubah, id).
 */
class WPMGR_Events {

    const BATAS_MAKS     = 500;
    const TUMPANG_TINDIH = 2;
    const TABEL          = array( 'e' => 'wpmgr_errors', 'l' => 'wpmgr_logins', 'g' => 'wpmgr_login_gagal' );

    public static function urai_kursor( $kursor ) {
        $posisi = array( 'e' => array( 0, 0 ), 'l' => array( 0, 0 ), 'g' => array( 0, 0 ) );
        foreach ( explode( ';', (string) $kursor ) as $bagian ) {
            if ( preg_match( '/^([elg])=(\d{1,10}):(\d{1,19})$/', $bagian, $m ) ) {
                $posisi[ $m[1] ] = array( (int) $m[2], (int) $m[3] );
            }
        }
        return $posisi;
    }

    public static function susun_kursor( array $posisi ) {
        $bagian = array();
        foreach ( array( 'e', 'l', 'g' ) as $k ) {
            $bagian[] = $k . '=' . (int) $posisi[ $k ][0] . ':' . (int) $posisi[ $k ][1];
        }
        return implode( ';', $bagian );
    }

    public static function posisi_berikut( array $lama, array $baris, $batas ) {
        if ( count( $baris ) >= $batas && ! empty( $baris ) ) {
            $akhir = end( $baris );
            return array( (int) $akhir['diubah'], (int) $akhir['id'] );
        }
        $maks = (int) $lama[0];
        foreach ( $baris as $b ) {
            $maks = max( $maks, (int) $b['diubah'] );
        }
        return array( max( 0, $maks - self::TUMPANG_TINDIH ), 0 );
    }

    public static function batas( $nilai ) {
        $n = (int) $nilai;
        return ( $n < 1 || $n > self::BATAS_MAKS ) ? self::BATAS_MAKS : $n;
    }

    private static function atau_null( $nilai ) {
        return ( null === $nilai || '' === (string) $nilai ) ? null : (string) $nilai;
    }

    public static function bentuk_error( $b ) {
        $konteks = null === $b['konteks'] ? null : json_decode( (string) $b['konteks'], true );
        return array(
            'sidik_jari'    => (string) $b['sidik_jari'],
            'tingkat'       => (string) $b['tingkat'],
            'komponen_tipe' => (string) $b['komponen_tipe'],
            'komponen_slug' => self::atau_null( $b['komponen_slug'] ),
            'pesan'         => (string) $b['pesan'],
            'file'          => self::atau_null( $b['file'] ),
            'baris'         => null === $b['baris'] ? null : (int) $b['baris'],
            'konteks'       => is_array( $konteks ) ? $konteks : null,
            'jumlah'        => (int) $b['jumlah'],
            'pertama'       => (int) $b['pertama'],
            'terakhir'      => (int) $b['terakhir'],
        );
    }

    public static function bentuk_login( $b ) {
        return array(
            'id'               => (int) $b['id'],
            'waktu'            => (int) $b['waktu'],
            'jenis'            => (string) $b['jenis'],
            'username'         => (string) $b['username'],
            'role'             => self::atau_null( $b['role'] ),
            'ip'               => self::atau_null( $b['ip'] ),
            'lewat_cloudflare' => (bool) (int) $b['lewat_cloudflare'],
            'user_agent'       => self::atau_null( $b['user_agent'] ),
            'jalur'            => (string) $b['jalur'],
        );
    }

    public static function bentuk_gagal( $b ) {
        return array(
            'jam'        => (int) $b['jam'],
            'ip'         => (string) $b['ip'],
            'username'   => (string) $b['username'],
            'jalur'      => (string) $b['jalur'],
            'jumlah'     => (int) $b['jumlah'],
            'user_agent' => self::atau_null( $b['user_agent'] ),
        );
    }

    public static function kumpulkan( $kursor, $batas ) {
        global $wpdb;
        $posisi = self::urai_kursor( $kursor );
        $batas  = self::batas( $batas );
        $hasil  = array();
        $lagi   = false;
        foreach ( self::TABEL as $k => $tabel ) {
            list( $t, $id ) = $posisi[ $k ];
            $baris = $wpdb->get_results( $wpdb->prepare(
                "SELECT * FROM {$wpdb->prefix}{$tabel}
                  WHERE diubah > %d OR (diubah = %d AND id > %d)
                  ORDER BY diubah ASC, id ASC LIMIT %d",
                $t, $t, $id, $batas
            ), ARRAY_A );
            $baris = is_array( $baris ) ? $baris : array();
            if ( count( $baris ) >= $batas ) {
                $lagi = true;
            }
            $posisi[ $k ] = self::posisi_berikut( $posisi[ $k ], $baris, $batas );
            $hasil[ $k ]  = $baris;
        }
        return array(
            'errors'      => array_map( array( __CLASS__, 'bentuk_error' ), $hasil['e'] ),
            'logins'      => array_map( array( __CLASS__, 'bentuk_login' ), $hasil['l'] ),
            'login_gagal' => array_map( array( __CLASS__, 'bentuk_gagal' ), $hasil['g'] ),
            'kursor'      => self::susun_kursor( $posisi ),
            'lagi'        => $lagi,
        );
    }
}
