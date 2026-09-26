<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Pemecah SQL bertahap untuk impor dorongan (spec §6.3 langkah 6). Sadar
 * kutip tunggal/ganda (dengan escape backslash dan kutip ganda-dobel),
 * backtick, serta komentar `-- `, `#`, dan blok. Offset akhir setiap
 * pernyataan dilaporkan supaya impor bisa dilanjutkan tepat di batas
 * pernyataan pada request berikutnya.
 *
 * Hanya bentuk yang dihasilkan pengekspor kita sendiri (Task 5),
 * `wp search-replace --export`, dan mysqldump yang diterima ubah():
 * DROP/CREATE/INSERT/ALTER ... KEYS atas tabel ber-prefix site ini.
 */
class WPMGR_Staging_Sql {

    const MAKS_PERNYATAAN = 67108864;
    const NORMAL          = 0;
    const KUTIP_TUNGGAL   = 1;
    const KUTIP_GANDA     = 2;
    const BACKTICK        = 3;
    const KOMENTAR_BARIS  = 4;
    const KOMENTAR_BLOK   = 5;

    private $buffer  = '';
    private $awal    = 0;
    private $i       = 0;
    private $mulai   = 0;
    private $keadaan = self::NORMAL;

    public function __construct( $offset_awal = 0 ) {
        $this->awal = (int) $offset_awal;
    }

    public function sisa() {
        return (string) substr( $this->buffer, $this->mulai );
    }

    public function tambah( $data ) {
        $this->buffer .= (string) $data;
        $n     = strlen( $this->buffer );
        $hasil = array();
        $kutip = array( self::KUTIP_TUNGGAL => "'", self::KUTIP_GANDA => '"', self::BACKTICK => '`' );
        while ( $this->i < $n ) {
            $s = $this->keadaan;
            if ( self::NORMAL === $s ) {
                $this->i += strcspn( $this->buffer, ";'\"`-#/", $this->i );
                if ( $this->i >= $n ) {
                    break;
                }
                $c = $this->buffer[ $this->i ];
                if ( ';' === $c ) {
                    $hasil[]     = array( substr( $this->buffer, $this->mulai, $this->i - $this->mulai ), $this->awal + $this->i + 1 );
                    $this->i++;
                    $this->mulai = $this->i;
                } elseif ( "'" === $c ) {
                    $this->keadaan = self::KUTIP_TUNGGAL;
                    $this->i++;
                } elseif ( '"' === $c ) {
                    $this->keadaan = self::KUTIP_GANDA;
                    $this->i++;
                } elseif ( '`' === $c ) {
                    $this->keadaan = self::BACKTICK;
                    $this->i++;
                } elseif ( '#' === $c ) {
                    $this->keadaan = self::KOMENTAR_BARIS;
                    $this->i++;
                } elseif ( '-' === $c ) {
                    if ( $this->i + 2 >= $n ) {
                        break; // tunggu data: "--" hanya komentar bila diikuti spasi
                    }
                    if ( '-' === $this->buffer[ $this->i + 1 ] && ctype_space( $this->buffer[ $this->i + 2 ] ) ) {
                        $this->keadaan = self::KOMENTAR_BARIS;
                        $this->i      += 2;
                    } else {
                        $this->i++;
                    }
                } else { // '/'
                    if ( $this->i + 1 >= $n ) {
                        break;
                    }
                    if ( '*' === $this->buffer[ $this->i + 1 ] ) {
                        $this->keadaan = self::KOMENTAR_BLOK;
                        $this->i      += 2;
                    } else {
                        $this->i++;
                    }
                }
            } elseif ( isset( $kutip[ $s ] ) ) {
                $q        = $kutip[ $s ];
                $this->i += strcspn( $this->buffer, self::BACKTICK === $s ? $q : $q . '\\', $this->i );
                if ( $this->i >= $n ) {
                    break;
                }
                if ( '\\' === $this->buffer[ $this->i ] ) {
                    if ( $this->i + 1 >= $n ) {
                        break;
                    }
                    $this->i += 2;
                    continue;
                }
                if ( $this->i + 1 >= $n ) {
                    break; // perlu satu karakter lagi: '' adalah kutip yang di-escape
                }
                if ( $q === $this->buffer[ $this->i + 1 ] ) {
                    $this->i += 2;
                } else {
                    $this->keadaan = self::NORMAL;
                    $this->i++;
                }
            } elseif ( self::KOMENTAR_BARIS === $s ) {
                $p = strpos( $this->buffer, "\n", $this->i );
                if ( false === $p ) {
                    $this->i = $n;
                    break;
                }
                $this->i       = $p + 1;
                $this->keadaan = self::NORMAL;
            } else {
                $p = strpos( $this->buffer, '*/', $this->i );
                if ( false === $p ) {
                    $this->i = max( $this->i, $n - 1 );
                    break;
                }
                $this->i       = $p + 2;
                $this->keadaan = self::NORMAL;
            }
        }
        if ( $this->i - $this->mulai > self::MAKS_PERNYATAAN ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql', 'Satu pernyataan SQL melebihi 64 MB.', 400 );
        }
        // Buang bagian yang sudah selesai supaya buffer tidak tumbuh terus.
        if ( $this->mulai > 1048576 ) {
            $this->buffer = (string) substr( $this->buffer, $this->mulai );
            $this->awal  += $this->mulai;
            $this->i     -= $this->mulai;
            $this->mulai  = 0;
        }
        return $hasil;
    }

    /** Buang spasi dan komentar di awal. Komentar berversi /*!...*\/ juga dibuang. */
    public static function tanpa_komentar_awal( $s ) {
        $s = (string) $s;
        while ( true ) {
            $s = ltrim( $s );
            if ( 0 === strpos( $s, '--' ) || 0 === strpos( $s, '#' ) ) {
                $p = strpos( $s, "\n" );
                $s = false === $p ? '' : substr( $s, $p + 1 );
            } elseif ( 0 === strpos( $s, '/*' ) ) {
                $p = strpos( $s, '*/' );
                $s = false === $p ? '' : substr( $s, $p + 2 );
            } else {
                return rtrim( $s );
            }
        }
    }

    public static function ubah( $pernyataan, $prefix, $awalan = 'wpmgr_tmp_' ) {
        $s = self::tanpa_komentar_awal( $pernyataan );
        if ( '' === $s || 1 === preg_match( '/^(SET\s|LOCK\s+TABLES\s|UNLOCK\s+TABLES\b)/i', $s ) ) {
            return null;
        }
        $pola = '/^(DROP\s+TABLE\s+IF\s+EXISTS|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|INSERT\s+INTO|ALTER\s+TABLE)\s+`([A-Za-z0-9_$]{1,64})`/i';
        if ( 1 !== preg_match( $pola, $s, $m, PREG_OFFSET_CAPTURE ) ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql',
                'Pernyataan SQL tidak diizinkan: ' . WPMGR_Staging::bersih( substr( $s, 0, 60 ), 60 ), 400 );
        }
        $nama = $m[2][0];
        if ( 0 !== strpos( $nama, $prefix ) ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql', 'Tabel di luar prefix site ini: ' . $nama, 400 );
        }
        if ( 0 === strpos( $nama, $prefix . 'wpmgr_' ) ) {
            return null; // Koreksi #14: data pemantauan produksi tidak ditimpa salinan lama.
        }
        if ( 0 === stripos( $m[1][0], 'ALTER' )
            && 1 !== preg_match( '/^ALTER\s+TABLE\s+`[^`]+`\s+(DISABLE|ENABLE)\s+KEYS\z/i', $s ) ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql', 'ALTER TABLE hanya boleh DISABLE/ENABLE KEYS.', 400 );
        }
        $baru = $awalan . $nama;
        if ( strlen( $baru ) > 64 ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql', 'Nama tabel terlalu panjang untuk tabel sementara: ' . $nama, 400 );
        }
        return substr( $s, 0, $m[2][1] ) . $baru . substr( $s, $m[2][1] + strlen( $nama ) );
    }
}
