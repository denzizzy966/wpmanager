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

    const BATAS_MAKS = 500;
    const TABEL      = array( 'e' => 'wpmgr_errors', 'l' => 'wpmgr_logins', 'g' => 'wpmgr_login_gagal' );

    /**
     * Detik penundaan sebelum sebuah "diubah" dianggap tuntas.
     *
     * Semua penulis (WPMGR_Penangkap::tulis(), WPMGR_Login::tulis()) memakai
     * $sekarang = time() SEKALI di awal flush lalu memakainya untuk setiap
     * UPDATE/INSERT baris itu -- flush sendiri selesai dalam hitungan
     * milidetik. Karena itu, begitu jam dinding sungguhan sudah lewat
     * (detik s + CAKRAWALA), TIDAK ADA request lain yang bisa lagi memanggil
     * time() dan mendapat s (time() hanya mundur bila jam sistem sendiri
     * mundur -- ditangani terpisah di urai_kursor()) -- setiap baris yang
     * "diubah" ke detik s pasti sudah tertulis. Kursor hanya boleh maju
     * melewati sebuah detik setelah itu terjamin, supaya baris berid rendah
     * yang diperbarui belakangan ke detik yang sama tidak pernah terlewat
     * permanen (Ruling review Task 14 putaran 1).
     */
    const CAKRAWALA = 5;

    public static function urai_kursor( $kursor, $sekarang = null ) {
        $sekarang = null === $sekarang ? time() : (int) $sekarang;
        $posisi   = array( 'e' => array( 0, 0 ), 'l' => array( 0, 0 ), 'g' => array( 0, 0 ) );
        // ?kursor[]=x mengirim array, bukan string -- (string) atas array
        // memicu peringatan "Array to string conversion" yang tak perlu.
        if ( ! is_scalar( $kursor ) ) {
            return $posisi;
        }
        foreach ( explode( ';', (string) $kursor ) as $bagian ) {
            if ( preg_match( '/^([elg])=(\d{1,10}):(\d{1,19})$/', $bagian, $m ) ) {
                $t = (int) $m[2];
                // Posisi "di masa depan" hanya masuk akal bila jam site
                // mundur atau basis data dipulihkan dari cadangan lama.
                // Memakainya apa adanya membuat tabel itu terjebak: ia tidak
                // akan pernah mengambil apa pun sampai jam sungguhan
                // mengejar posisi itu. Anggap saja belum pernah mengambil.
                if ( $t > $sekarang + 60 ) {
                    continue;
                }
                $posisi[ $m[1] ] = array( $t, (int) $m[3] );
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

    /**
     * Posisi berikutnya adalah persis (diubah, id) baris terakhir yang
     * dikembalikan -- tidak pernah mundur. Tabel yang tidak menghasilkan
     * baris (sudah tuntas, atau semuanya masih di dalam cakrawala) tetap di
     * posisi lamanya; ia dicoba lagi di pengambilan berikutnya, bukan
     * dianggap "sudah sampai di situ".
     */
    public static function posisi_berikut( array $lama, array $baris ) {
        if ( empty( $baris ) ) {
            return $lama;
        }
        $akhir = end( $baris );
        return array( (int) $akhir['diubah'], (int) $akhir['id'] );
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
        // Satu cakrawala untuk ketiga tabel dalam satu request: dihitung
        // sekali dari time() (bukan NOW() SQL) karena penulis juga memakai
        // time() PHP untuk kolom diubah -- menyamakan sumber jam mencegah
        // selisih jam antara PHP dan MySQL menggeser batas tuntas ini.
        $sekarang = time();
        $horizon  = $sekarang - self::CAKRAWALA;
        $posisi   = self::urai_kursor( $kursor, $sekarang );
        $batas    = self::batas( $batas );
        $hasil    = array();
        $lagi     = false;
        foreach ( self::TABEL as $k => $tabel ) {
            list( $t, $id ) = $posisi[ $k ];
            // diubah >= %d dulu (jangkauan tunggal yang bisa dipakai indeks
            // (diubah,id)), baru (diubah > %d OR id > %d) menyaring persis
            // baris sesudah posisi kursor -- setara dengan
            // "diubah > t OR (diubah = t AND id > id)" tapi tidak memaksa
            // MySQL memindai dari awal tabel untuk sisi OR yang kedua.
            $baris = $wpdb->get_results( $wpdb->prepare(
                "SELECT * FROM {$wpdb->prefix}{$tabel}
                  WHERE diubah >= %d AND (diubah > %d OR id > %d) AND diubah <= %d
                  ORDER BY diubah ASC, id ASC LIMIT %d",
                $t, $t, $id, $horizon, $batas
            ), ARRAY_A );
            $baris = is_array( $baris ) ? $baris : array();
            if ( count( $baris ) >= $batas ) {
                $lagi = true;
            }
            $posisi[ $k ] = self::posisi_berikut( $posisi[ $k ], $baris );
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
