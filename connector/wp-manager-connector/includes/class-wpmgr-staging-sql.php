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

    /**
     * Fix I1 (review putaran 1): topeng level-token dari $s -- isi literal
     * string ('...', "...") dan komentar diganti karakter netral ('x'/spasi)
     * PANJANG SAMA (posisi tidak bergeser), sedangkan identifier backtick,
     * kata kunci, spasi, tanda kurung, dan koma dibiarkan apa adanya.
     * Pemeriksaan struktur pernyataan di ubah() berjalan di atas TOPENG ini,
     * bukan $s mentah -- supaya kata kunci (mis. "SELECT", "ENGINE=") yang
     * kebetulan muncul di DALAM nilai literal (mis. VALUES ('...SELECT...'))
     * tidak pernah salah terdeteksi sebagai bagian struktur pernyataan, dan
     * sebaliknya kata kunci sungguhan tidak bisa "disembunyikan" penyerang
     * di dalam string untuk lolos dari pemeriksaan.
     *
     * Batasan yang diketahui: identifier backtick TIDAK ditopengi (isinya
     * perlu dibaca strukturnya), jadi kolom yang sengaja diberi nama sama
     * dengan kata kunci (mis. `` `select` ``) masih bisa memicu pencocokan
     * `\bSELECT\b` di ubah(). Situasi itu tidak pernah muncul pada keluaran
     * pengekspor sendiri (Task 5), wp search-replace --export, atau
     * mysqldump -- satu-satunya sumber yang diterima ubah().
     */
    public static function tanpa_literal( $s ) {
        $s       = (string) $s;
        $n       = strlen( $s );
        $keluar  = '';
        $i       = 0;
        $keadaan = self::NORMAL;
        while ( $i < $n ) {
            $c = $s[ $i ];
            if ( self::NORMAL === $keadaan ) {
                if ( "'" === $c ) {
                    $keadaan = self::KUTIP_TUNGGAL;
                    $keluar .= $c;
                    $i++;
                } elseif ( '"' === $c ) {
                    $keadaan = self::KUTIP_GANDA;
                    $keluar .= $c;
                    $i++;
                } elseif ( '`' === $c ) {
                    $keadaan = self::BACKTICK;
                    $keluar .= $c;
                    $i++;
                } elseif ( '#' === $c ) {
                    $keadaan = self::KOMENTAR_BARIS;
                    $keluar .= ' ';
                    $i++;
                } elseif ( '-' === $c && $i + 2 < $n && '-' === $s[ $i + 1 ] && ctype_space( $s[ $i + 2 ] ) ) {
                    $keadaan = self::KOMENTAR_BARIS;
                    $keluar .= '  ';
                    $i      += 2;
                } elseif ( '/' === $c && $i + 1 < $n && '*' === $s[ $i + 1 ] ) {
                    $keadaan = self::KOMENTAR_BLOK;
                    $keluar .= '  ';
                    $i      += 2;
                } else {
                    $keluar .= $c;
                    $i++;
                }
            } elseif ( self::BACKTICK === $keadaan ) {
                $keluar .= $c;
                if ( '`' === $c ) {
                    $keadaan = self::NORMAL;
                }
                $i++;
            } elseif ( self::KUTIP_TUNGGAL === $keadaan || self::KUTIP_GANDA === $keadaan ) {
                $q = self::KUTIP_TUNGGAL === $keadaan ? "'" : '"';
                if ( '\\' === $c && $i + 1 < $n ) {
                    $keluar .= 'xx';
                    $i      += 2;
                } elseif ( $q === $c ) {
                    if ( $i + 1 < $n && $q === $s[ $i + 1 ] ) {
                        $keluar .= 'xx'; // kutip yang di-escape ('' atau "")
                        $i      += 2;
                    } else {
                        $keluar .= $c;
                        $keadaan = self::NORMAL;
                        $i++;
                    }
                } else {
                    $keluar .= 'x';
                    $i++;
                }
            } elseif ( self::KOMENTAR_BARIS === $keadaan ) {
                if ( "\n" === $c ) {
                    $keluar .= "\n";
                    $keadaan = self::NORMAL;
                } else {
                    $keluar .= ' ';
                }
                $i++;
            } else { // KOMENTAR_BLOK
                if ( '*' === $c && $i + 1 < $n && '/' === $s[ $i + 1 ] ) {
                    $keluar .= '  ';
                    $keadaan = self::NORMAL;
                    $i      += 2;
                } else {
                    $keluar .= ' ';
                    $i++;
                }
            }
        }
        return $keluar;
    }

    /**
     * Tabel di luar prefix site ini, TERMASUK tabel yang berada di bawah
     * prefix LEBIH PANJANG yang tumpang tindih dengan prefix kita (Koreksi
     * #14 review, I2): site lain di database yang sama dengan prefix mis.
     * 'wp_abc_' membuat tabelnya sendiri (mis. 'wp_abc_posts') lolos
     * pencocokan awalan sederhana terhadap prefix site ini ('wp_'). $prefix_asing
     * (dihitung sekali oleh pemanggil lewat WPMGR_Staging_Db::prefix_asing()
     * dari SHOW TABLES sungguhan) berisi prefix-prefix asing semacam itu;
     * kosong (default) berarti pemanggil tidak punya akses DB untuk
     * menghitungnya (mis. unit test murni) -- perilaku jatuh balik ke
     * pencocokan awalan biasa, TIDAK menolak apa pun tambahan.
     */
    private static function di_luar_prefix( $nama, $prefix, array $prefix_asing ) {
        if ( 0 !== strpos( $nama, $prefix ) ) {
            return true;
        }
        foreach ( $prefix_asing as $pa ) {
            if ( '' !== $pa && 0 === strpos( $nama, $pa ) ) {
                return true;
            }
        }
        return false;
    }

    private static function tolak( $pesan ) {
        return WPMGR_Staging::galat( 'wpmgr_staging_sql', $pesan, 400 );
    }

    /** Bagian akhir bersama: validasi prefix, lewati wpmgr_*, ganti nama. */
    private static function ubah_nama_tabel( $s, $prefix, $awalan, $nama, $offset, array $prefix_asing ) {
        if ( self::di_luar_prefix( $nama, $prefix, $prefix_asing ) ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql', 'Tabel di luar prefix site ini: ' . $nama, 400 );
        }
        // Koreksi #14: data pemantauan produksi tidak ditimpa salinan lama.
        // Dibandingkan huruf besar/kecil (fix minor, review putaran 1):
        // server dengan lower_case_table_names bisa mengembalikan nama
        // dalam huruf apa pun; 'WP_WPMGR_ERRORS' tetap harus dilewati.
        if ( 0 === stripos( $nama, $prefix . 'wpmgr_' ) ) {
            return null;
        }
        $baru = $awalan . $nama;
        if ( strlen( $baru ) > 64 ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql', 'Nama tabel terlalu panjang untuk tabel sementara: ' . $nama, 400 );
        }
        return substr( $s, 0, $offset ) . $baru . substr( $s, $offset + strlen( $nama ) );
    }

    /**
     * Fix I1 (review putaran 1, plan-mandated): draf awal hanya memvalidasi
     * KEPALA setiap pernyataan (kata kerja + nama tabel), lalu menerima apa
     * pun sesudahnya -- itu meloloskan `INSERT INTO t SELECT ...`,
     * `INSERT INTO t ... ON DUPLICATE KEY UPDATE ...`, `CREATE TABLE t (...)
     * AS SELECT ...`, `CREATE TABLE t (...) ENGINE=FEDERATED/CONNECT ...`,
     * dan `DROP TABLE \`a\`, \`b\`` (menghapus tabel KEDUA yang tidak pernah
     * diperiksa). Sekarang PERNYATAAN UTUH divalidasi per jenis, di atas
     * topeng tanpa_literal() (bukan $s mentah -- lihat docblock-nya).
     *
     * $prefix_asing (I2, opsional): lihat di_luar_prefix().
     */
    public static function ubah( $pernyataan, $prefix, $awalan = 'wpmgr_tmp_', array $prefix_asing = array() ) {
        $s = self::tanpa_komentar_awal( $pernyataan );
        if ( '' === $s || 1 === preg_match( '/^(SET\s|LOCK\s+TABLES\s|UNLOCK\s+TABLES\b)/i', $s ) ) {
            return null;
        }
        $topeng = self::tanpa_literal( $s );

        if ( 1 === preg_match( '/^DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?`([A-Za-z0-9_$]{1,64})`/i', $topeng, $m, PREG_OFFSET_CAPTURE ) ) {
            if ( 1 !== preg_match( '/^DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?`[A-Za-z0-9_$]{1,64}`\s*\z/i', $topeng ) ) {
                return self::tolak( 'DROP TABLE hanya boleh menyebut satu tabel.' );
            }
            return self::ubah_nama_tabel( $s, $prefix, $awalan, $m[1][0], $m[1][1], $prefix_asing );
        }
        if ( 1 === preg_match( '/^INSERT\s+INTO\s+`([A-Za-z0-9_$]{1,64})`/i', $topeng, $m, PREG_OFFSET_CAPTURE ) ) {
            if ( 1 === preg_match( '/\bON\s+DUPLICATE\s+KEY\s+UPDATE\b/i', $topeng ) ) {
                return self::tolak( 'INSERT ... ON DUPLICATE KEY UPDATE tidak diizinkan.' );
            }
            if ( 1 === preg_match( '/^INSERT\s+INTO\s+`[A-Za-z0-9_$]{1,64}`\s+SET\b/i', $topeng ) ) {
                return self::tolak( 'INSERT ... SET tidak diizinkan.' );
            }
            if ( 1 !== preg_match( '/^INSERT\s+INTO\s+`[A-Za-z0-9_$]{1,64}`\s*(?:\([^()]*\)\s*)?VALUES\b/i', $topeng ) ) {
                return self::tolak( 'Hanya bentuk INSERT INTO ... VALUES yang diizinkan.' );
            }
            return self::ubah_nama_tabel( $s, $prefix, $awalan, $m[1][0], $m[1][1], $prefix_asing );
        }
        if ( 1 === preg_match( '/^CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+`([A-Za-z0-9_$]{1,64})`/i', $topeng, $m, PREG_OFFSET_CAPTURE ) ) {
            if ( 1 === preg_match( '/\bSELECT\b/i', $topeng ) ) {
                return self::tolak( 'CREATE TABLE ... SELECT tidak diizinkan.' );
            }
            if ( 1 === preg_match( '/\bENGINE\s*=?\s*(FEDERATED|CONNECT)\b/i', $topeng )
                || 1 === preg_match( '/\b(DATA|INDEX)\s+DIRECTORY\b/i', $topeng ) ) {
                return self::tolak( 'Opsi CREATE TABLE ini tidak diizinkan.' );
            }
            return self::ubah_nama_tabel( $s, $prefix, $awalan, $m[1][0], $m[1][1], $prefix_asing );
        }
        if ( 1 === preg_match( '/^ALTER\s+TABLE\s+`([A-Za-z0-9_$]{1,64})`\s+/i', $topeng, $m, PREG_OFFSET_CAPTURE ) ) {
            if ( 1 !== preg_match( '/^ALTER\s+TABLE\s+`[A-Za-z0-9_$]{1,64}`\s+(DISABLE|ENABLE)\s+KEYS\s*\z/i', $topeng ) ) {
                return self::tolak( 'ALTER TABLE hanya boleh DISABLE/ENABLE KEYS.' );
            }
            return self::ubah_nama_tabel( $s, $prefix, $awalan, $m[1][0], $m[1][1], $prefix_asing );
        }
        return self::tolak( 'Pernyataan SQL tidak diizinkan: ' . WPMGR_Staging::bersih( substr( $s, 0, 60 ), 60 ) );
    }
}
