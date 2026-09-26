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
     * Fix N1 (review putaran 2, KRITIS): true bila $s memuat komentar APA
     * PUN (`--`, `#`, blok `/* ... *\/` -- TERMASUK varian bertanda MySQL/
     * MariaDB `/*! ... *\/` dan `/*M! ... *\/`) DI LUAR literal string/
     * backtick. Dipakai ubah() untuk MENOLAK SELURUHNYA, bukan menopengi
     * lalu memvalidasi topeng-nya -- lihat catatan panjang di ubah() untuk
     * alasan draf sebelumnya (tanpa_literal()) tidak cukup: ia MENOPENGI
     * isi komentar dengan spasi, sehingga validator tidak pernah tahu ada
     * komentar di sana sama sekali, sedangkan teks yang DIKEMBALIKAN
     * (bukan topeng) tetap memuat komentar itu utuh -- termasuk isi
     * `/*! ... *\/`, yang MySQL/MariaDB EKSEKUSI sebagai SQL sungguhan,
     * bukan komentar biasa. Itulah bagaimana `DROP TABLE IF EXISTS
     * \`wp_a\` /*!, \`wp_users\` *\/` bisa lolos: topeng-nya tampak seperti
     * `DROP TABLE IF EXISTS \`wpmgr_tmp_wp_a\`` (bersih), tapi teks yang
     * benar-benar dikirim ke server tetap memuat `, \`wp_users\`` di dalam
     * "komentar" yang sebenarnya dieksekusi MySQL/MariaDB.
     */
    public static function ada_komentar( $s ) {
        $s       = (string) $s;
        $n       = strlen( $s );
        $i       = 0;
        $keadaan = self::NORMAL;
        while ( $i < $n ) {
            $c = $s[ $i ];
            if ( self::NORMAL === $keadaan ) {
                if ( "'" === $c ) {
                    $keadaan = self::KUTIP_TUNGGAL;
                    $i++;
                } elseif ( '"' === $c ) {
                    $keadaan = self::KUTIP_GANDA;
                    $i++;
                } elseif ( '`' === $c ) {
                    $keadaan = self::BACKTICK;
                    $i++;
                } elseif ( '#' === $c ) {
                    return true;
                } elseif ( '-' === $c && $i + 2 < $n && '-' === $s[ $i + 1 ] && ctype_space( $s[ $i + 2 ] ) ) {
                    return true;
                } elseif ( '/' === $c && $i + 1 < $n && '*' === $s[ $i + 1 ] ) {
                    return true;
                } else {
                    $i++;
                }
            } elseif ( self::BACKTICK === $keadaan ) {
                if ( '`' === $c ) {
                    $keadaan = self::NORMAL;
                }
                $i++;
            } else { // KUTIP_TUNGGAL / KUTIP_GANDA
                $q = self::KUTIP_TUNGGAL === $keadaan ? "'" : '"';
                if ( '\\' === $c && $i + 1 < $n ) {
                    $i += 2;
                    continue;
                }
                if ( $q === $c ) {
                    if ( $i + 1 < $n && $q === $s[ $i + 1 ] ) {
                        $i += 2;
                        continue;
                    }
                    $keadaan = self::NORMAL;
                }
                $i++;
            }
        }
        return false;
    }

    /**
     * Fix N1 (review putaran 3, KRITIS -- masih terbuka setelah putaran 2):
     * nama opsi tabel CREATE TABLE yang diizinkan, sebagai ALLOWLIST
     * lengkap (bukan sekadar menolak ENGINE/CONNECTION/UNION tertentu).
     * Terurut dari yang PALING PANJANG (kata majemuk) -- cocok_kata_kunci()
     * memeriksa daftar ini berurutan, jadi 'DEFAULT CHARACTER SET' harus
     * diperiksa sebelum 'CHARACTER SET' sendirian, dst., supaya tidak
     * berhenti pada pencocokan sebagian yang lebih pendek.
     */
    const OPSI_TABEL = array(
        'DEFAULT CHARACTER SET', 'DEFAULT CHARSET', 'CHARACTER SET', 'CHARSET',
        'DEFAULT COLLATE', 'COLLATE',
        'ENGINE',
        'ROW_FORMAT', 'KEY_BLOCK_SIZE', 'AUTO_INCREMENT', 'COMMENT',
        'STATS_PERSISTENT', 'STATS_AUTO_RECALC', 'STATS_SAMPLE_PAGES',
        'PAGE_CHECKSUM', 'TRANSACTIONAL',
        'PACK_KEYS', 'CHECKSUM', 'DELAY_KEY_WRITE', 'MAX_ROWS', 'MIN_ROWS', 'AVG_ROW_LENGTH',
    );

    /**
     * Posisi ')' yang berpasangan dengan '(' di $buka pada $topeng (dihitung
     * di atas TOPENG, bukan $s mentah -- tanda kurung di dalam literal
     * string sudah ditopengi jadi 'x', jadi menghitung kedalaman di sini
     * tidak bisa dikelabui oleh mis. `DEFAULT '(a)'` di dalam definisi
     * kolom). false bila tidak seimbang sampai akhir teks.
     */
    private static function cari_penutup_kurung( $topeng, $buka ) {
        $n     = strlen( $topeng );
        $depth = 0;
        for ( $i = $buka; $i < $n; $i++ ) {
            $c = $topeng[ $i ];
            if ( '(' === $c ) {
                $depth++;
            } elseif ( ')' === $c ) {
                $depth--;
                if ( 0 === $depth ) {
                    return $i;
                }
            }
        }
        return false;
    }

    /**
     * Mencocokkan kata kunci opsi (satu atau lebih kata dipisah SATU spasi
     * di $kata_kunci, mis. 'DEFAULT CHARACTER SET') pada posisi $i di $s,
     * mengizinkan spasi APA PUN (satu atau lebih) di antara kata-kata di
     * teks sungguhan. Mengembalikan posisi TEPAT setelah kata kunci itu
     * (selalu > $i, jadi aman dibedakan dari false) bila cocok DAN diikuti
     * batas kata (bukan karakter identifier lain, supaya 'ENGINEX' tidak
     * salah cocok sebagai 'ENGINE'); false bila tidak cocok.
     */
    private static function cocok_kata_kunci( $s, $i, $kata_kunci ) {
        $n    = strlen( $s );
        $kata = explode( ' ', $kata_kunci );
        $pos  = $i;
        foreach ( $kata as $idx => $k ) {
            if ( $idx > 0 ) {
                $spasi = strspn( $s, " \t\r\n", $pos );
                if ( 0 === $spasi ) {
                    return false;
                }
                $pos += $spasi;
            }
            $panjang = strlen( $k );
            if ( $pos + $panjang > $n || 0 !== strncasecmp( substr( $s, $pos, $panjang ), $k, $panjang ) ) {
                return false;
            }
            $pos += $panjang;
        }
        if ( $pos < $n && self::karakter_identifier( $s[ $pos ] ) ) {
            return false;
        }
        return $pos;
    }

    /**
     * Membaca SATU nilai opsi tabel (string berkutip/backtick, ATAU
     * bareword identifier/angka) pada posisi $i di $topeng; memajukan $i.
     * false bila tidak ada nilai yang bisa diurai di sana. $berkutip
     * (lewat referensi) memberi tahu pemanggil apakah nilai itu literal
     * string -- dipakai untuk mewajibkan COMMENT berupa string.
     */
    private static function baca_nilai_opsi( $topeng, $n, &$i, &$berkutip ) {
        $berkutip = false;
        if ( $i >= $n ) {
            return false;
        }
        $c = $topeng[ $i ];
        if ( "'" === $c || '"' === $c ) {
            $tutup = strpos( $topeng, $c, $i + 1 );
            if ( false === $tutup ) {
                return false;
            }
            $i        = $tutup + 1;
            $berkutip = true;
            return true;
        }
        if ( '`' === $c ) {
            $tutup = strpos( $topeng, '`', $i + 1 );
            if ( false === $tutup ) {
                return false;
            }
            $i = $tutup + 1;
            return true;
        }
        $panjang = strspn( $topeng, 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-', $i );
        if ( 0 === $panjang ) {
            return false;
        }
        $i += $panjang;
        return true;
    }

    /**
     * Fix N1 (review putaran 3, KRITIS): draf putaran 2 hanya mencari
     * klausa ENGINE= dan menolak CONNECTION=/UNION= secara terpisah --
     * diverifikasi reviewer atas server MySQL 8.0.46/MariaDB 11.8
     * sungguhan bahwa itu masih bisa dilewati lewat: '=' yang OPSIONAL
     * (`ENGINE FEDERATED` tanpa '='), opsi yang DIULANG (`ENGINE=InnoDB
     * ENGINE=FEDERATED` -- ENGINE TERAKHIR yang menang di MySQL/MariaDB),
     * `CONNECTION 'mysql://...'` tanpa '=', `ENGINE=InnoDB UNION
     * (\`wp_users\`)` (membuat tabel MERGE yang membaca tabel lain --
     * dikonfirmasi benar-benar terjadi di MariaDB 11), `PARTITION p0
     * STORAGE ENGINE FEDERATED`, dan `TABLE_TYPE=CSV FILE_NAME=...`.
     *
     * Sekarang: EKOR opsi tabel (semua setelah ')' penutup daftar kolom)
     * di-tokenize UTUH sebagai ALLOWLIST nama opsi (OPSI_TABEL) -- setiap
     * opsi lain (CONNECTION, UNION, TABLE_TYPE, FILE_NAME, PASSWORD,
     * ENCRYPTION, TABLESPACE, INSERT_METHOD, dst.) ditolak murni karena
     * NAMANYA tidak dikenal, bukan lewat pencocokan pola per nama. PARTITION/
     * SUBPARTITION ditolak mutlak di mana pun di ekor (pesan tersendiri
     * yang jelas). ENGINE WAJIB muncul TEPAT SATU KALI dengan nilai
     * InnoDB/MyISAM/Aria (huruf besar/kecil bebas, bentuk bare/berkutip/
     * backtick) -- opsi ENGINE kedua (walau nilainya sendiri sah) tetap
     * ditolak, menutup celah "yang terakhir menang". COMMENT wajib berupa
     * literal string, bukan bareword.
     *
     * Bagian DAFTAR KOLOM (di antara '(' dan ')' pembukanya) juga diperiksa
     * terpisah: tidak boleh memuat 'STORAGE ENGINE' atau 'DATA'/'INDEX
     * DIRECTORY' -- klausa itu tidak pernah sah muncul di sana, hanya
     * fitur PARTITION (yang sudah ditolak mutlak) yang memakainya.
     *
     * Semuanya berjalan di atas TOPENG (bukan $s mentah) -- posisi ')'
     * penutup daftar kolom dihitung lewat cari_penutup_kurung() di topeng
     * supaya tanda kurung di dalam literal string (mis. `DEFAULT '(a)'`)
     * tidak mengacaukan hitungan kedalaman. Nilai ENGINE sendiri tetap
     * diambil dari $s ASLI pada offset yang sama (topeng menopengi isi
     * 'InnoDB'/"InnoDB" berkutip jadi 'xxxxxxx').
     */
    private static function periksa_opsi_tabel( $s, $topeng, $buka_kolom ) {
        $tutup_kolom = self::cari_penutup_kurung( $topeng, $buka_kolom );
        if ( false === $tutup_kolom ) {
            return self::tolak( 'CREATE TABLE tidak seimbang tanda kurungnya.' );
        }
        $span_kolom = substr( $topeng, $buka_kolom + 1, $tutup_kolom - $buka_kolom - 1 );
        if ( 1 === preg_match( '/\bSTORAGE\s+ENGINE\b/i', $span_kolom )
            || 1 === preg_match( '/\b(DATA|INDEX)\s+DIRECTORY\b/i', $span_kolom ) ) {
            return self::tolak( 'Opsi CREATE TABLE ini tidak diizinkan.' );
        }

        $n     = strlen( $topeng );
        $ekor  = $tutup_kolom + 1;
        $ekor_teks = substr( $topeng, $ekor );
        if ( 1 === preg_match( '/\b(SUB)?PARTITION\b/i', $ekor_teks ) ) {
            return self::tolak( 'CREATE TABLE dengan PARTITION tidak diizinkan.' );
        }

        $i          = $ekor;
        $ada_engine = 0;
        while ( true ) {
            $i += strspn( $topeng, " \t\r\n,", $i );
            if ( $i >= $n ) {
                break;
            }
            $nama_cocok  = null;
            $pos_setelah = false;
            foreach ( self::OPSI_TABEL as $nama ) {
                $hasil = self::cocok_kata_kunci( $topeng, $i, $nama );
                if ( false !== $hasil ) {
                    $nama_cocok  = $nama;
                    $pos_setelah = $hasil;
                    break;
                }
            }
            if ( null === $nama_cocok ) {
                return self::tolak( 'Opsi CREATE TABLE ini tidak diizinkan.' );
            }
            $i  = $pos_setelah;
            $i += strspn( $topeng, " \t\r\n", $i );
            if ( $i < $n && '=' === $topeng[ $i ] ) {
                $i++;
                $i += strspn( $topeng, " \t\r\n", $i );
            }
            $awal_nilai = $i;
            $berkutip   = false;
            if ( ! self::baca_nilai_opsi( $topeng, $n, $i, $berkutip ) ) {
                return self::tolak( 'Nilai opsi CREATE TABLE tidak dapat diurai.' );
            }
            if ( 'ENGINE' === $nama_cocok ) {
                $ada_engine++;
                // Nilai SESUNGGUHNYA diambil dari $s ASLI (bukan topeng,
                // yang menopengi isi 'InnoDB'/"InnoDB" berkutip) pada
                // offset yang SAMA -- tanpa_literal() menjaga panjang tetap sama.
                $nilai_asli = trim( substr( $s, $awal_nilai, $i - $awal_nilai ), "'\"`" );
                if ( 1 !== $ada_engine || 1 !== preg_match( '/^(InnoDB|MyISAM|Aria)\z/i', $nilai_asli ) ) {
                    return self::tolak( 'ENGINE tabel tidak diizinkan: ' . WPMGR_Staging::bersih( $nilai_asli, 32 ) );
                }
            } elseif ( 'COMMENT' === $nama_cocok && ! $berkutip ) {
                return self::tolak( 'Nilai COMMENT harus berupa literal string.' );
            }
        }
        if ( 1 !== $ada_engine ) {
            return self::tolak( 'CREATE TABLE harus menyebut ENGINE tepat satu kali.' );
        }
        return null;
    }

    /** true bila $c adalah karakter identifier ASCII ([A-Za-z0-9_]). */
    private static function karakter_identifier( $c ) {
        return ( $c >= 'a' && $c <= 'z' ) || ( $c >= 'A' && $c <= 'Z' ) || ( $c >= '0' && $c <= '9' ) || '_' === $c;
    }

    /**
     * Fix N1 (review putaran 3, KRITIS BARU): membaca SATU token literal
     * VALUES pada posisi $i di $s (topeng); memajukan $i dan mengembalikan
     * true bila cocok, atau mengembalikan false TANPA mengubah $i bila
     * tidak ada token yang cocok di sana. Dipakai values_aman() -- lihat
     * docblock-nya untuk alasan ini LINEAR (bukan regex bersarang).
     */
    private static function baca_token_values( $s, $n, &$i ) {
        if ( $i >= $n ) {
            return false;
        }
        $c = $s[ $i ];
        // Literal string bertopeng: 'x...x' -- tanpa_literal() menjamin
        // SATU-SATUNYA kutip tunggal yang tersisa di dalam rentang ini
        // adalah pembatas pembuka/penutupnya sendiri (lihat docblock
        // tanpa_literal()), jadi kutip BERIKUTNYA (strpos biasa, bukan
        // regex) sudah pasti penutup yang benar -- tidak perlu mengurai
        // escape apa pun lagi di sini.
        if ( "'" === $c ) {
            $tutup = strpos( $s, "'", $i + 1 );
            if ( false === $tutup ) {
                return false;
            }
            $i = $tutup + 1;
            return true;
        }
        // NULL (kata utuh -- tidak boleh diikuti karakter identifier lain,
        // supaya 'NULLABLE' misalnya tidak salah cocok sebagai 'NULL').
        if ( $i + 4 <= $n && 0 === strncasecmp( substr( $s, $i, 4 ), 'NULL', 4 )
            && ( $i + 4 === $n || ! self::karakter_identifier( $s[ $i + 4 ] ) ) ) {
            $i += 4;
            return true;
        }
        // 0x... heksadesimal (literal biner Task 5, tanpa prawalan charset).
        if ( '0' === $c && $i + 1 < $n && ( 'x' === $s[ $i + 1 ] || 'X' === $s[ $i + 1 ] ) ) {
            $mulai = $i + 2;
            $akhir = $mulai + strspn( $s, '0123456789abcdefABCDEF', $mulai );
            if ( $akhir > $mulai ) {
                $i = $akhir;
                return true;
            }
            return false;
        }
        // Angka: -?digit+(.digit+)?([eE][+-]?digit+)? -- strspn/strcspn
        // saja, tanpa regex sama sekali, supaya benar-benar linear.
        $j = $i;
        if ( '-' === $s[ $j ] ) {
            $j++;
        }
        $mulai_digit = $j;
        $j          += strspn( $s, '0123456789', $j );
        if ( $j === $mulai_digit ) {
            return false; // Bukan angka, NULL, hex, maupun string -- token tidak dikenal.
        }
        if ( $j < $n && '.' === $s[ $j ] ) {
            $mulai_pecahan = $j + 1;
            $akhir_pecahan = $mulai_pecahan + strspn( $s, '0123456789', $mulai_pecahan );
            if ( $akhir_pecahan > $mulai_pecahan ) {
                $j = $akhir_pecahan;
            }
        }
        if ( $j < $n && ( 'e' === $s[ $j ] || 'E' === $s[ $j ] ) ) {
            $k = $j + 1;
            if ( $k < $n && ( '+' === $s[ $k ] || '-' === $s[ $k ] ) ) {
                $k++;
            }
            $akhir_eksponen = $k + strspn( $s, '0123456789', $k );
            if ( $akhir_eksponen > $k ) {
                $j = $akhir_eksponen;
            }
        }
        $i = $j;
        return true;
    }

    /**
     * Fix N1 (review putaran 2, lalu putaran 3 KRITIS): isi VALUES
     * divalidasi token demi token di atas topeng ($topeng_values, sudah
     * dipotong mulai tepat setelah kata kunci VALUES) -- hanya literal
     * yang BENAR-BENAR dipakai pengekspor kita (Task 5,
     * class-wpmgr-staging-tabel.php: string berkutip lewat nilai()/esc(),
     * NULL, dan literal hex 0x... untuk data biner -- tidak pernah literal
     * berprawalan charset seperti _utf8mb4 0x.., itu hanya dipakai
     * literal_kunci() untuk klausa WHERE kursor, tidak pernah ditulis ke
     * dalam teks VALUES itu sendiri) yang diizinkan, ditambah tanda
     * kurung/koma sebagai pemisah baris/tuple. Apa pun yang lain -- SELECT,
     * LOAD_FILE(...), pemanggilan fungsi, subquery -- ditolak.
     *
     * Fix KRITIS BARU (review putaran 3): draf putaran 2 memakai SATU
     * regex dengan grup berulang bersarang (tuple berisi token berulang,
     * masing-masing berisi alternasi). Regex seperti itu di PCRE (dengan
     * pcre.jit=1, default PHP 7.4/8.3) memicu backtracking eksponensial
     * pada input besar -- diverifikasi reviewer: 1000 baris (78 KB)
     * diterima, 2000 baris (156 KB, jumlah yang persis dipakai
     * WPMGR_Staging_Tabel::$baris) DITOLAK karena preg_match() mengembalikan
     * false (PREG_JIT_STACKLIMIT_ERROR) -- yang oleh kode di sini
     * (sebelum perbaikan ini) diperlakukan sebagai "tidak cocok" (`1 ===
     * preg_match(...)` bernilai false untuk false ATAUPUN 0), jadi
     * hampir SETIAP dorongan sungguhan (1 MiB, 2000 baris per potongan
     * ekspor) gagal diimpor. Diganti dengan tokenizer LINEAR (satu lintasan,
     * satu token per langkah lewat baca_token_values() -- strspn/strcspn/
     * strncasecmp, TANPA regex bersarang atau backtracking sama sekali)
     * yang menerima PERSIS token yang sama seperti draf regex sebelumnya.
     */
    private static function values_aman( $topeng_values ) {
        $s = (string) $topeng_values;
        $n = strlen( $s );
        $i = 0;
        while ( true ) {
            $i += strspn( $s, " \t\r\n", $i );
            if ( $i >= $n || '(' !== $s[ $i ] ) {
                return false;
            }
            $i++;
            $i += strspn( $s, " \t\r\n", $i );
            if ( ! self::baca_token_values( $s, $n, $i ) ) {
                return false;
            }
            $i += strspn( $s, " \t\r\n", $i );
            while ( $i < $n && ',' === $s[ $i ] ) {
                $i++;
                $i += strspn( $s, " \t\r\n", $i );
                if ( ! self::baca_token_values( $s, $n, $i ) ) {
                    return false;
                }
                $i += strspn( $s, " \t\r\n", $i );
            }
            if ( $i >= $n || ')' !== $s[ $i ] ) {
                return false;
            }
            $i++;
            $i += strspn( $s, " \t\r\n", $i );
            if ( $i >= $n ) {
                return true;
            }
            if ( ',' !== $s[ $i ] ) {
                return false;
            }
            $i++;
        }
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
     * Fix N1 (review putaran 2, KRITIS): fix I1 memvalidasi STRUKTUR di
     * atas topeng, tapi MENGEMBALIKAN $s (teks ASLI, bukan topeng) dengan
     * nama tabel diganti. Topeng tanpa_literal() menopengi isi komentar
     * `/* ... *\/` dengan SPASI (dianggap "aman diabaikan", sama seperti
     * spasi biasa) -- padahal MySQL/MariaDB mengenal bentuk KHUSUS
     * `/*! ... *\/` dan `/*M! ... *\/` ("komentar bertanda versi") yang
     * ISINYA DIEKSEKUSI sebagai SQL sungguhan, bukan dibuang seperti
     * komentar biasa. Karena topeng menyembunyikan APA PUN yang ditulis
     * penyerang di dalam komentar itu (validator tidak pernah melihatnya),
     * sementara teks yang dikembalikan tetap memuat komentar itu utuh, ini
     * membuka celah: `DROP TABLE IF EXISTS \`wp_a\` /*!, \`wp_users\` *\/`
     * lolos sebagai head yang "bersih" (topengnya berakhir setelah nama
     * tabel pertama), tapi teks yang DIKEMBALIKAN -- dan akhirnya
     * DIEKSEKUSI Task 8 -- tetap men-DROP `wp_users` produksi lewat isi
     * komentar bertanda itu. Trik yang sama menembus INSERT (ON DUPLICATE
     * KEY UPDATE lewat `/*!50000 ... *\/`), CREATE TABLE (ENGINE=FEDERATED
     * lewat komentar bertanda), dan ALTER TABLE (RENAME TO lewat komentar
     * bertanda).
     *
     * Perbaikannya BUKAN mencoba mendeteksi hanya `/*!`/`/*M!` (bentuk
     * komentar bertanda versi lain bisa ditemukan/berubah), melainkan
     * MENOLAK SELURUH pernyataan yang memuat komentar APA PUN di luar
     * literal, di mana pun posisinya (ada_komentar()) -- bukan hanya
     * menopenginya. Pengekspor sendiri (Task 5, class-wpmgr-staging-tabel.php)
     * TIDAK PERNAH menyisipkan komentar ke badan pernyataan DROP/INSERT
     * (semuanya dirakit dari string PHP literal, bukan hasil MySQL) --
     * SATU pengecualian yang diketahui: docblock class-wpmgr-staging-tabel.php
     * sendiri (baris ~17) mencatat bahwa keluaran `SHOW CREATE TABLE`
     * MySQL/MariaDB bisa memuat komentar bertanda versi (mis. untuk tabel
     * yang dipartisi atau fitur MySQL 8 tertentu) yang diteruskan APA
     * ADANYA ke badan CREATE TABLE. Skema inti WordPress dan WooCommerce
     * tidak pernah memakai partisi atau fitur semacam itu, jadi dalam
     * praktiknya CREATE TABLE yang diekspor tidak akan pernah memuat
     * komentar sungguhan -- tapi BILA suatu saat memang muncul (mis. plugin
     * lain yang eksotis), menolaknya di sini adalah kegagalan AMAN dan
     * jelas (400 pada satu tabel itu saat Task 8 mengimpor), bukan lubang
     * keamanan yang diam-diam terbuka lagi demi kompatibilitas kasus tepi
     * yang belum pernah benar-benar terlihat di skema WordPress/WooCommerce.
     *
     * $prefix_asing (I2, opsional): lihat di_luar_prefix().
     */
    public static function ubah( $pernyataan, $prefix, $awalan = 'wpmgr_tmp_', array $prefix_asing = array() ) {
        $s = self::tanpa_komentar_awal( $pernyataan );
        if ( '' === $s || 1 === preg_match( '/^(SET\s|LOCK\s+TABLES\s|UNLOCK\s+TABLES\b)/i', $s ) ) {
            return null;
        }
        if ( self::ada_komentar( $s ) ) {
            return self::tolak( 'Pernyataan SQL memuat komentar; tidak diizinkan.' );
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
            // Fix N1: isi VALUES divalidasi token demi token (values_aman()),
            // bukan hanya dicek "diawali VALUES" lalu diterima apa pun
            // sesudahnya -- draf I1 meloloskan `VALUES ((SELECT ...))`,
            // `VALUES (LOAD_FILE(...))`, dan pemanggilan fungsi lain.
            if ( 1 !== preg_match( '/^INSERT\s+INTO\s+`[A-Za-z0-9_$]{1,64}`\s*(?:\([^()]*\)\s*)?VALUES\s*(.*)\z/is', $topeng, $mval ) ) {
                return self::tolak( 'Hanya bentuk INSERT INTO ... VALUES yang diizinkan.' );
            }
            if ( ! self::values_aman( $mval[1] ) ) {
                return self::tolak( 'Isi VALUES memuat token yang tidak diizinkan.' );
            }
            return self::ubah_nama_tabel( $s, $prefix, $awalan, $m[1][0], $m[1][1], $prefix_asing );
        }
        if ( 1 === preg_match( '/^CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+`([A-Za-z0-9_$]{1,64})`/i', $topeng, $m, PREG_OFFSET_CAPTURE ) ) {
            if ( 1 === preg_match( '/\bSELECT\b/i', $topeng ) ) {
                return self::tolak( 'CREATE TABLE ... SELECT tidak diizinkan.' );
            }
            if ( 1 === preg_match( '/\b(DATA|INDEX)\s+DIRECTORY\b/i', $topeng ) ) {
                return self::tolak( 'Opsi CREATE TABLE ini tidak diizinkan.' );
            }
            // Posisi '(' pembuka daftar kolom -- tepat setelah head yang
            // barusan cocok (nama tabel + backtick penutup).
            $buka_kolom = strpos( $topeng, '(', $m[0][1] + strlen( $m[0][0] ) );
            if ( false === $buka_kolom ) {
                return self::tolak( 'CREATE TABLE tidak memiliki daftar kolom.' );
            }
            $galat_opsi = self::periksa_opsi_tabel( $s, $topeng, $buka_kolom );
            if ( null !== $galat_opsi ) {
                return $galat_opsi;
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
