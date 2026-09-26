<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Ekspor satu potongan tabel sebagai SQL yang bisa diimpor MariaDB (spec
 * §6.2 langkah 3). Escaping dikerjakan sendiri, bukan lewat $wpdb->prepare,
 * supaya hasilnya deterministik (bisa diuji tanpa koneksi MySQL) dan tahan
 * data biner: nilai biner dan byte bukan UTF-8 ditulis sebagai literal hex,
 * sehingga tidak ada byte mentah yang bergantung pada charset koneksi impor.
 */
class WPMGR_Staging_Tabel {

    const MAKS_PERNYATAAN = 1048576;
    const MAKS_KURSOR     = 8192;

    public static $baris     = 2000;
    public static $sub       = 200;
    public static $maks_byte = 6291456;

    private static function galat_tabel( $pesan, $status ) {
        return WPMGR_Staging::galat( 'wpmgr_staging_tabel', $pesan, $status );
    }

    private static function galat_kursor() {
        return WPMGR_Staging::galat( 'wpmgr_staging_kursor', 'Kursor tabel tidak sah.', 400 );
    }

    /** Sama dengan mysqli_real_escape_string(): tidak ada baris baru mentah di literal. */
    public static function esc( $s ) {
        return strtr( (string) $s, array(
            '\\'   => '\\\\',
            "\0"   => '\\0',
            "\n"   => '\\n',
            "\r"   => '\\r',
            "'"    => "\\'",
            '"'    => '\\"',
            "\x1a" => '\\Z',
        ) );
    }

    public static function biner( $tipe ) {
        return 1 === preg_match( '/^(tiny|medium|long)?blob|^(var)?binary|^bit/i', (string) $tipe );
    }

    public static function bulat( $tipe ) {
        return 1 === preg_match( '/^(tiny|small|medium|big)?int|^integer/i', (string) $tipe );
    }

    private static function utf8( $v ) {
        return 1 === preg_match( '//u', $v );
    }

    public static function nilai( $v, $tipe ) {
        if ( null === $v ) {
            return 'NULL';
        }
        $v = (string) $v;
        if ( self::biner( $tipe ) || ! self::utf8( $v ) ) {
            return '' === $v ? "''" : '0x' . bin2hex( $v );
        }
        return "'" . self::esc( $v ) . "'";
    }

    public static function kode_kursor( $data ) {
        return base64_encode( json_encode( $data ) );
    }

    /** null = mulai dari awal; false = rusak. */
    public static function urai_kursor( $kursor ) {
        $kursor = (string) $kursor;
        if ( '' === $kursor ) {
            return null;
        }
        if ( strlen( $kursor ) > self::MAKS_KURSOR || 1 !== preg_match( '/^[A-Za-z0-9+\/=]+\z/', $kursor ) ) {
            return false;
        }
        $data = json_decode( (string) base64_decode( $kursor, true ), true );
        if ( ! is_array( $data ) ) {
            return false;
        }
        if ( array_key_exists( 'o', $data ) ) {
            return ( is_int( $data['o'] ) && $data['o'] >= 0 ) ? array( 'o' => $data['o'] ) : false;
        }
        if ( isset( $data['pk'] ) && is_array( $data['pk'] ) && ! empty( $data['pk'] ) ) {
            foreach ( $data['pk'] as $v ) {
                $sah = is_array( $v ) && ( ( isset( $v['s'] ) && is_string( $v['s'] ) ) || ( isset( $v['x'] ) && is_string( $v['x'] ) ) );
                if ( ! $sah ) {
                    return false;
                }
            }
            return array( 'pk' => array_values( $data['pk'] ) );
        }
        return false;
    }

    public static function literal_kunci( $entri, $tipe ) {
        if ( isset( $entri['x'] ) ) {
            if ( 1 !== preg_match( '/^(?:[0-9a-f]{2})*\z/', $entri['x'] ) ) {
                return false;
            }
            return '' === $entri['x'] ? "''" : '0x' . $entri['x'];
        }
        $s = (string) $entri['s'];
        if ( self::bulat( $tipe ) ) {
            // Angka ditulis tanpa kutip: membandingkan kolom BIGINT dengan
            // string memaksa perbandingan floating point dan meleset di atas 2^53.
            return 1 === preg_match( '/^-?[0-9]{1,20}\z/', $s ) ? $s : false;
        }
        return "'" . self::esc( $s ) . "'";
    }

    public static function kolom( $wpdb, $tabel ) {
        $baris = $wpdb->get_results( "SHOW COLUMNS FROM `{$tabel}`", ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL -- nama tabel sudah divalidasi
        $hasil = array();
        foreach ( (array) $baris as $b ) {
            $nama = isset( $b['Field'] ) ? (string) $b['Field'] : '';
            if ( 1 !== preg_match( '/^[^\x00-\x1f`]{1,64}\z/u', $nama ) ) {
                return array();
            }
            $hasil[ $nama ] = isset( $b['Type'] ) ? (string) $b['Type'] : '';
        }
        return $hasil;
    }

    private static function posisi_berikut( array $pk, array $row, $lama ) {
        if ( empty( $pk ) ) {
            return array( 'o' => ( is_array( $lama ) && isset( $lama['o'] ) ? $lama['o'] : 0 ) + 1 );
        }
        $nilai = array();
        foreach ( $pk as $k ) {
            $v       = (string) $row[ $k ];
            $nilai[] = self::utf8( $v ) ? array( 's' => $v ) : array( 'x' => bin2hex( $v ) );
        }
        return array( 'pk' => $nilai );
    }

    private static function kueri( $tabel, array $pk, array $kolom, $posisi, $batas ) {
        if ( empty( $pk ) ) {
            if ( is_array( $posisi ) && isset( $posisi['pk'] ) ) {
                return false;
            }
            $offset = ( is_array( $posisi ) && isset( $posisi['o'] ) ) ? (int) $posisi['o'] : 0;
            return "SELECT * FROM `{$tabel}` LIMIT " . (int) $batas . ' OFFSET ' . $offset;
        }
        $urut  = '`' . implode( '`,`', $pk ) . '`';
        $where = '';
        if ( is_array( $posisi ) ) {
            if ( ! isset( $posisi['pk'] ) || count( $posisi['pk'] ) !== count( $pk ) ) {
                return false;
            }
            $lit = array();
            foreach ( $pk as $i => $k ) {
                $l = self::literal_kunci( $posisi['pk'][ $i ], isset( $kolom[ $k ] ) ? $kolom[ $k ] : '' );
                if ( false === $l ) {
                    return false;
                }
                $lit[] = $l;
            }
            $where = 1 === count( $pk )
                ? " WHERE `{$pk[0]}` > {$lit[0]}"
                : " WHERE ({$urut}) > (" . implode( ',', $lit ) . ')';
        }
        return "SELECT * FROM `{$tabel}`{$where} ORDER BY {$urut} LIMIT " . (int) $batas;
    }

    public static function ekspor( $wpdb, $tabel, $kursor ) {
        if ( ! is_string( $tabel ) || ! WPMGR_Staging_Manifest::nama_tabel_sah( $tabel, $wpdb->prefix ) ) {
            return self::galat_tabel( 'Nama tabel tidak sah.', 400 );
        }
        if ( $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $wpdb->esc_like( $tabel ) ) ) !== $tabel ) {
            return self::galat_tabel( 'Tabel tidak ditemukan.', 404 );
        }
        $posisi = self::urai_kursor( $kursor );
        if ( false === $posisi ) {
            return self::galat_kursor();
        }
        $kolom = self::kolom( $wpdb, $tabel );
        if ( empty( $kolom ) ) {
            return self::galat_tabel( 'Kolom tabel tidak dapat dibaca.', 500 );
        }
        $pk = WPMGR_Staging_Manifest::pk( $wpdb, $tabel );
        foreach ( $pk as $k ) {
            if ( ! isset( $kolom[ $k ] ) ) {
                $pk = array();
                break;
            }
        }
        if ( false === self::kueri( $tabel, $pk, $kolom, $posisi, 1 ) ) {
            return self::galat_kursor();
        }

        $sql = '';
        if ( null === $posisi ) {
            $buat = $wpdb->get_row( "SHOW CREATE TABLE `{$tabel}`", ARRAY_N ); // phpcs:ignore WordPress.DB.PreparedSQL
            if ( ! is_array( $buat ) || empty( $buat[1] ) ) {
                return self::galat_tabel( 'Struktur tabel tidak dapat dibaca.', 500 );
            }
            $sql .= "DROP TABLE IF EXISTS `{$tabel}`;\n" . $buat[1] . ";\n";
        }

        // Konsisten di dalam satu potongan (spec §6.2); lihat "Yang tidak
        // dapat dipenuhi" di rencana untuk konsistensi lintas potongan.
        $wpdb->query( 'SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ' );
        $wpdb->query( 'START TRANSACTION WITH CONSISTENT SNAPSHOT' );

        $daftar     = '`' . implode( '`,`', array_keys( $kolom ) ) . '`';
        $akhir      = $posisi;
        $jumlah     = 0;
        $selesai    = false;
        $penuh      = false;
        $pernyataan = '';
        while ( ! $penuh && $jumlah < self::$baris ) {
            $batas = min( self::$sub, self::$baris - $jumlah );
            $rows  = $wpdb->get_results( self::kueri( $tabel, $pk, $kolom, $akhir, $batas ), ARRAY_A );
            if ( ! is_array( $rows ) ) {
                $wpdb->query( 'ROLLBACK' );
                return self::galat_tabel( 'Isi tabel tidak dapat dibaca.', 500 );
            }
            foreach ( $rows as $row ) {
                $nilai = array();
                foreach ( $kolom as $k => $tipe ) {
                    $nilai[] = self::nilai( array_key_exists( $k, $row ) ? $row[ $k ] : null, $tipe );
                }
                $tuple       = '(' . implode( ',', $nilai ) . ')';
                $pernyataan  = ( '' === $pernyataan )
                    ? "INSERT INTO `{$tabel}` ({$daftar}) VALUES\n" . $tuple
                    : $pernyataan . ",\n" . $tuple;
                if ( strlen( $pernyataan ) >= self::MAKS_PERNYATAAN ) {
                    $sql       .= $pernyataan . ";\n";
                    $pernyataan = '';
                }
                $jumlah++;
                $akhir = self::posisi_berikut( $pk, $row, $akhir );
                if ( strlen( $sql ) + strlen( $pernyataan ) >= self::$maks_byte ) {
                    $penuh = true;
                    break;
                }
            }
            if ( ! $penuh && count( $rows ) < $batas ) {
                $selesai = true;
                break;
            }
        }
        if ( '' !== $pernyataan ) {
            $sql .= $pernyataan . ";\n";
        }
        $wpdb->query( 'COMMIT' );
        return array(
            'sql'     => $sql,
            'kursor'  => $selesai ? null : self::kode_kursor( $akhir ),
            'selesai' => $selesai,
            'baris'   => $jumlah,
        );
    }
}
