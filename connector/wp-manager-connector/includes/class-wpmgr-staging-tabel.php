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
 *
 * Batasan yang diketahui (fix round 1, review):
 * - Trigger TIDAK pernah ikut terekspor -- SHOW CREATE TABLE tidak pernah
 *   memuatnya (trigger adalah objek terpisah); impor staging yang membuat
 *   ulang trigger, bukan endpoint ini.
 * - SHOW CREATE TABLE bisa memuat komentar versi (mis. `/*!50100 ... *\/`)
 *   dan nama collation khas MySQL 8 (mis. `utf8mb4_0900_ai_ci`) yang
 *   diteruskan APA ADANYA. Penerjemahannya (bila perlu supaya cocok dengan
 *   MariaDB staging) adalah tugas proses impor staging, bukan connector ini.
 * - Item 5, fix round 2: satu baris yang SQL ter-escape-nya SENDIRI sudah
 *   melebihi $maks_respon gagal PERMANEN dengan wpmgr_staging_baris_terlalu_besar
 *   (413) -- tidak pernah dicoba ulang berhasil (baris itu, tabel itu,
 *   selalu menghasilkan galat yang sama). Karena nilai biner/bukan-UTF-8
 *   ditulis sebagai literal hex (dua karakter teks per byte data), ini
 *   mencakup satu kolom blob/text mana pun yang isinya melebihi kira-kira
 *   4 MB (separuh $maks_respon 8 MiB). Bagaimana dashboard memetakan kode
 *   galat ini menjadi "jangan diulang" adalah pekerjaan task lain (Python,
 *   tidak disentuh di sini).
 */
class WPMGR_Staging_Tabel {

    const MAKS_KURSOR = 8192;

    public static $baris           = 2000;
    public static $sub             = 200;
    public static $maks_byte       = 6291456;
    // Batas satu PERNYATAAN INSERT (dipecah sebelum melebihi ini) --
    // tunable (bukan const) supaya bisa diuji dengan nilai kecil (item 6j).
    public static $maks_pernyataan = 1048576;
    // Batas KERAS ukuran respons (beda dari $maks_byte yang lunak): satu
    // baris yang SENDIRIAN sudah melebihi ini tidak akan pernah muat dalam
    // satu potongan berapa pun kecilnya potongan itu -- fix item 3, review
    // putaran 1.
    public static $maks_respon     = 8388608;

    private static function galat_tabel( $pesan, $status ) {
        return WPMGR_Staging::galat( 'wpmgr_staging_tabel', $pesan, $status );
    }

    private static function galat_kursor() {
        return WPMGR_Staging::galat( 'wpmgr_staging_kursor', 'Kursor tabel tidak sah.', 400 );
    }

    /**
     * Fix item 3 (Penting), review putaran 1: kode KHUSUS untuk satu baris
     * yang sendirian sudah melebihi $maks_respon -- BUKAN 500 generik,
     * supaya dashboard bisa membedakannya dari galat sementara dan
     * berhenti mengulang permintaan yang sama (ekspor ini juga dipakai
     * snapshot, jadi baris itu TIDAK pernah dilewati diam-diam).
     */
    private static function galat_baris_besar( $tabel ) {
        return WPMGR_Staging::galat( 'wpmgr_staging_baris_terlalu_besar',
            "Satu baris pada tabel `{$tabel}` melebihi batas ukuran respons dan tidak dapat diekspor.", 413 );
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
        // Item 6e (Minor), review putaran 1: tipe spasial (WKB biner di
        // bawahnya) diperlakukan sama seperti blob/binary -- selalu hex,
        // tidak pernah string berkutip.
        return 1 === preg_match(
            '/^(tiny|medium|long)?blob|^(var)?binary|^bit|^(geometrycollection|multilinestring|multipolygon|multipoint|linestring|polygon|geometry|point)\b/i',
            (string) $tipe
        );
    }

    public static function bulat( $tipe ) {
        return 1 === preg_match( '/^(tiny|small|medium|big)?int|^integer/i', (string) $tipe );
    }

    /**
     * Item 6d (Minor), review putaran 1: FLOAT/DOUBLE tidak presisi untuk
     * perbandingan keyset ("WHERE kol > nilai" bisa melompati/menduplikasi
     * baris akibat pembulatan IEEE 754) -- kolom PK bertipe ini membuat
     * seluruh tabel diperlakukan seperti tanpa PK (mode offset). DECIMAL
     * TIDAK termasuk (presisi eksak, aman dipakai sebagai PK).
     */
    public static function mengambang( $tipe ) {
        return 1 === preg_match( '/^(float|double)\b/i', (string) $tipe );
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
        // Item 6c (Minor), review putaran 1: pagar tipe SEBELUM cast ke
        // string -- pemanggil hostile bisa mengirim array/objek/angka;
        // (string) pada array memicu PHP Notice/Warning "Array to string
        // conversion", bukan penolakan bersih 400. null tetap sah (berarti
        // "mulai dari awal", sama seperti string kosong).
        if ( null === $kursor ) {
            return null;
        }
        if ( ! is_string( $kursor ) ) {
            return false;
        }
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

    /**
     * Item 4 (Minor), fix round 2: prawalan charset literal hex ikut
     * charset koneksi SUNGGUHAN ($wpdb->charset), bukan "_utf8mb4" tetap --
     * situs yang koneksinya BUKAN utf8mb4 (mis. latin1 lama) tetap
     * menghasilkan literal yang mode-independen dan cocok dengan charset
     * kolom teksnya. Nilai $wpdb->charset divalidasi ketat (huruf/angka
     * saja) sebelum ditempel ke SQL -- itu properti wpdb, bukan masukan
     * penyerang langsung, tapi tetap tidak pernah dipercaya mentah-mentah.
     */
    public static function utf8_charset( $wpdb ) {
        $c = isset( $wpdb->charset ) ? (string) $wpdb->charset : '';
        return 1 === preg_match( '/^[a-z0-9]+\z/', $c ) ? $c : 'utf8mb4';
    }

    public static function literal_kunci( $entri, $tipe, $charset = 'utf8mb4' ) {
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
        // Item 5 (Penting), review putaran 1: literal STRING BERKUTIP yang
        // di-escape sendiri (esc()) bergantung pada sql_mode koneksi impor
        // -- di bawah NO_BACKSLASH_ESCAPES, backslash bukan lagi karakter
        // escape, sehingga "\\'" yang kita tulis dibaca sebagai backslash
        // literal DIIKUTI penutup kutip, memutus kueri (kunci hostile bisa
        // menyuntik SQL; kunci SAH yang memuat "'" atau "\" ikut rusak).
        // Literal HEX berprawalan charset (`_<charset> 0x..`/`_binary 0x..`)
        // tidak mengenal escape sequence sama sekali -- aman di SEMUA
        // sql_mode. Nilai 's' selalu UTF-8 sah (lihat posisi_berikut(): byte
        // bukan UTF-8 selalu masuk sebagai 'x'/hex, bukan 's'), jadi
        // encode-nya selalu representasi UTF-8 nilai itu.
        if ( '' === $s ) {
            return "''"; // '0x' tanpa digit tidak selalu diterima parser.
        }
        $prawalan = self::biner( $tipe ) ? '_binary' : ( '_' . $charset );
        return $prawalan . ' 0x' . bin2hex( $s );
    }

    /**
     * Item 4 (Penting), review putaran 1: kolom GENERATED (VIRTUAL/STORED
     * di MySQL, VIRTUAL/PERSISTENT di MariaDB) dilewati DI SINI -- baik
     * daftar kolom INSERT maupun nilai per baris di ekspor() memakai peta
     * hasil fungsi ini secara langsung, jadi kolom generated otomatis tidak
     * pernah ikut ke keduanya sekaligus. MySQL/MariaDB menghitung nilainya
     * sendiri saat impor dan MENOLAK bila kita mencoba menuliskannya.
     *
     * Item 1 (Kritis, regresi), fix round 2: regex round 1 (`/GENERATED/i`
     * polos) JUGA cocok dengan `DEFAULT_GENERATED` dan
     * `DEFAULT_GENERATED on update CURRENT_TIMESTAMP` -- nilai Extra yang
     * MySQL >= 8.0.13 berikan untuk kolom BIASA yang defaultnya berupa
     * EKSPRESI (mis. `ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP`), bukan
     * kolom generated sungguhan. Kolom seperti itu lazim di tabel plugin;
     * ikut dilewati membuat NILAI ASLINYA hilang dari INSERT, sehingga
     * setiap baris berubah jadi NOW() saat diimpor -- kerusakan diam-diam.
     * Diperbaiki: hanya cocok "VIRTUAL GENERATED"/"STORED GENERATED"
     * (kata "GENERATED" harus didahului VIRTUAL/STORED, bukan berdiri
     * sendiri) atau MariaDB "VIRTUAL"/"PERSISTENT" TEPAT (tanpa embel-embel
     * lain di depan/belakang).
     */
    private static function kolom_generated( $extra ) {
        $extra = trim( (string) $extra );
        return 1 === preg_match( '/\b(VIRTUAL|STORED)\s+GENERATED\b/i', $extra )
            || 1 === preg_match( '/^(VIRTUAL|PERSISTENT)\z/i', $extra );
    }

    public static function kolom( $wpdb, $tabel ) {
        $baris = $wpdb->get_results( "SHOW COLUMNS FROM `{$tabel}`", ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL -- nama tabel sudah divalidasi
        $hasil = array();
        foreach ( (array) $baris as $b ) {
            $nama = isset( $b['Field'] ) ? (string) $b['Field'] : '';
            if ( 1 !== preg_match( '/^[^\x00-\x1f`]{1,64}\z/u', $nama ) ) {
                return array();
            }
            if ( self::kolom_generated( isset( $b['Extra'] ) ? $b['Extra'] : '' ) ) {
                continue;
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

    private static function kueri( $tabel, array $pk, array $kolom, $posisi, $batas, $charset = 'utf8mb4' ) {
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
                $l = self::literal_kunci( $posisi['pk'][ $i ], isset( $kolom[ $k ] ) ? $kolom[ $k ] : '', $charset );
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

    /**
     * Item 2 (Penting), fix round 2: perkiraan MURAH panjang baris rata-
     * rata dari metadata tabel (satu kueri per request) -- dipakai
     * sub_awal() supaya sub-batch PERTAMA setiap request langsung
     * berukuran wajar untuk tabel berbaris lebar, bukan selalu mulai buta
     * di $sub penuh (200) lalu baru mengecil SETELAH satu batch penuh
     * baris lebar sudah terlanjur dimuat ke memori.
     */
    private static function avg_row_length( $wpdb, $tabel ) {
        $baris = $wpdb->get_row(
            $wpdb->prepare( 'SHOW TABLE STATUS LIKE %s', $wpdb->esc_like( $tabel ) ), ARRAY_A
        ); // phpcs:ignore WordPress.DB.PreparedSQL
        if ( ! is_array( $baris ) || ! isset( $baris['Avg_row_length'] ) ) {
            return 0;
        }
        return (int) $baris['Avg_row_length'];
    }

    /**
     * clamp(floor($maks_byte / rata_rata / 2), 1, $sub) -- separuh anggaran
     * lunak dibagi ukuran baris rata-rata, supaya SATU sub-batch penuh
     * masih longgar di bawah anggaran walau perkiraannya meleset (mis.
     * beberapa baris jauh di atas rata-rata). $avg 0/tidak diketahui
     * (mis. tabel kosong atau metadata tidak terbaca) jatuh balik ke $sub
     * penuh seperti sebelumnya (max(1,0) -> 1, hasil bagi jadi sangat
     * besar, langsung dijepit ke $sub oleh min()).
     */
    private static function sub_awal( $avg_row_length ) {
        $avg = max( 1, (int) $avg_row_length );
        $n   = intdiv( intdiv( self::$maks_byte, $avg ), 2 );
        return max( 1, min( self::$sub, $n ) );
    }

    /**
     * $tenggat (fix item 2, Penting, review putaran 1): tenggat waktu UNIX
     * (microtime(true)) opsional -- null berarti "pakai anggaran sisa
     * request yang sama seperti /staging/manifest dan /staging/file"
     * (WPMGR_Staging::anggaran_detik()). Dicek di setiap AWAL sub-batch
     * (bukan di tengah satu SELECT, yang tidak bisa disela) supaya request
     * ini juga tidak pernah menabrak batas max_execution_time hosting.
     * Jaminan progres: sub-batch PERTAMA pada request ini SELALU diproses
     * walau tenggat sudah lewat sebelum mulai (sama seperti pola
     * WPMGR_Staging_File/Manifest) -- kursor tetap maju.
     */
    public static function ekspor( $wpdb, $tabel, $kursor, $tenggat = null ) {
        if ( ! is_string( $tabel ) || ! WPMGR_Staging_Manifest::nama_tabel_sah( $tabel, $wpdb->prefix ) ) {
            return self::galat_tabel( 'Nama tabel tidak sah.', 400 );
        }
        $posisi = self::urai_kursor( $kursor );
        if ( false === $posisi ) {
            return self::galat_kursor();
        }

        // Item 6b (Minor), review putaran 1: SHOW FULL TABLES (bukan SHOW
        // TABLES) membawa Table_type sekaligus -- VIEW dan jenis lain non-
        // "BASE TABLE" ditolak 400 (bukan diekspor sebagai tabel kosong
        // atau gagal aneh di tengah SHOW CREATE TABLE/SHOW COLUMNS).
        $info_tabel = $wpdb->get_row(
            $wpdb->prepare( 'SHOW FULL TABLES LIKE %s', $wpdb->esc_like( $tabel ) ), ARRAY_N
        ); // phpcs:ignore WordPress.DB.PreparedSQL
        if ( ! is_array( $info_tabel ) || ! isset( $info_tabel[0] ) || (string) $info_tabel[0] !== $tabel ) {
            return self::galat_tabel( 'Tabel tidak ditemukan.', 404 );
        }
        if ( ! isset( $info_tabel[1] ) || 'BASE TABLE' !== (string) $info_tabel[1] ) {
            return self::galat_tabel( 'Hanya tabel dasar yang dapat diekspor (VIEW tidak didukung).', 400 );
        }

        $kolom = self::kolom( $wpdb, $tabel );
        if ( empty( $kolom ) ) {
            return self::galat_tabel( 'Kolom tabel tidak dapat dibaca.', 500 );
        }
        $pk = WPMGR_Staging_Manifest::pk( $wpdb, $tabel );
        foreach ( $pk as $k ) {
            // Kolom PK yang generated (tidak ada di $kolom -- sudah
            // disaring kolom()) atau FLOAT/DOUBLE (item 6d): perlakukan
            // tabel sebagai tanpa PK (mode offset), bukan mencoba
            // memakainya untuk keyset yang tidak presisi/tidak ada.
            if ( ! isset( $kolom[ $k ] ) || self::mengambang( $kolom[ $k ] ) ) {
                $pk = array();
                break;
            }
        }
        $charset = self::utf8_charset( $wpdb );
        if ( false === self::kueri( $tabel, $pk, $kolom, $posisi, 1, $charset ) ) {
            return self::galat_kursor();
        }

        if ( null === $tenggat ) {
            $tenggat = microtime( true ) + WPMGR_Staging::anggaran_detik();
        }

        $sql = '';
        if ( null === $posisi ) {
            $buat = $wpdb->get_row( "SHOW CREATE TABLE `{$tabel}`", ARRAY_N ); // phpcs:ignore WordPress.DB.PreparedSQL
            if ( ! is_array( $buat ) || empty( $buat[1] ) ) {
                return self::galat_tabel( 'Struktur tabel tidak dapat dibaca.', 500 );
            }
            $sql .= "DROP TABLE IF EXISTS `{$tabel}`;\n" . $buat[1] . ";\n";
        }

        // Item 6g (Minor), review putaran 1: TANPA "SESSION" -- hanya
        // berlaku untuk TRANSAKSI BERIKUTNYA pada koneksi ini, bukan
        // seluruh sesi (koneksi $wpdb dipakai berulang lintas request pada
        // proses PHP-FPM yang sama; membiarkannya SESSION bisa membocorkan
        // isolation level ini ke kueri lain yang tidak terkait ekspor ini).
        $wpdb->query( 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ' );
        // Konsisten di dalam satu potongan (spec §6.2); lihat "Yang tidak
        // dapat dipenuhi" di rencana untuk konsistensi lintas potongan.
        $mulai = $wpdb->query( 'START TRANSACTION WITH CONSISTENT SNAPSHOT' );
        if ( false === $mulai ) {
            // Item 1 & 6g, review putaran 1: transaksi yang gagal dimulai
            // tidak boleh diam-diam lanjut membaca tanpa snapshot konsisten.
            $wpdb->query( 'ROLLBACK' );
            return self::galat_tabel( 'Tidak dapat memulai transaksi baca tabel.', 500 );
        }

        $daftar      = '`' . implode( '`,`', array_keys( $kolom ) ) . '`';
        $awalan      = "INSERT INTO `{$tabel}` ({$daftar}) VALUES\n";
        $akhir       = $posisi;
        $jumlah      = 0;
        $selesai     = false;
        $penuh       = false;
        $pernyataan  = '';
        // Item 3b (Penting, round 1) + item 2 (Penting, round 2): ukuran
        // sub-batch EFEKTIF, lokal untuk panggilan ini (tidak mengubah
        // $sub statis). Sub-batch PERTAMA request ini langsung disesuaikan
        // dengan Avg_row_length tabel (sub_awal()) -- BUKAN selalu mulai
        // buta di $sub penuh (200), yang pada tabel berbaris lebar memuat
        // ~200 baris besar ke memori sebelum sempat diperiksa satu pun,
        // dan galat kehabisan memori akan terulang PERSIS SAMA pada setiap
        // percobaan berikutnya (kursor belum sempat maju sama sekali).
        // Sesudah itu: mengecil (dibagi dua, lantai 1) setiap kali
        // sub-batch SEBELUMNYA memuat baris "lebar" (lebih besar dari
        // jatah rata-rata per baris), dan tumbuh KEMBALI (dikali dua,
        // dijepit ke $sub) setiap kali sub-batch SEBELUMNYA semuanya
        // kecil -- supaya SATU baris lebar di awal tidak mengunci ukuran
        // sub-batch jadi 1 untuk SISA request walau baris berikutnya
        // sudah normal lagi. Pendekatan paling sederhana yang tetap aman:
        // tidak perlu kueri SELECT LENGTH() tambahan per baris, dan tidak
        // ada state baru yang perlu disimpan di kursor lintas-request.
        $sub_efektif = self::sub_awal( self::avg_row_length( $wpdb, $tabel ) );
        $batas_wajar = max( 1, intdiv( self::$maks_byte, max( 1, self::$sub ) ) );

        while ( ! $penuh && $jumlah < self::$baris ) {
            if ( $jumlah > 0 && microtime( true ) >= $tenggat ) {
                break; // Belum selesai; kursor sudah maju sejauh sub-batch terakhir.
            }
            $batas = min( $sub_efektif, self::$baris - $jumlah );
            $rows  = $wpdb->get_results( self::kueri( $tabel, $pk, $kolom, $akhir, $batas, $charset ), ARRAY_A );
            // Item 1 (Kritis), review putaran 1: wpdb SUNGGUHAN mengembalikan
            // array() -- BUKAN null/false -- juga saat query GAGAL (lihat
            // implementasi get_results() inti WordPress). !is_array($rows)
            // saja tidak pernah terpicu pada wpdb nyata; last_error adalah
            // satu-satunya sinyal yang bisa dipercaya untuk membedakan
            // "0 baris tersisa" (selesai, sah) dari "kueri gagal" (tabel
            // jadi terpotong diam-diam bila tidak dicek).
            if ( ! is_array( $rows ) || '' !== (string) $wpdb->last_error ) {
                $wpdb->query( 'ROLLBACK' );
                return self::galat_tabel( 'Isi tabel tidak dapat dibaca.', 500 );
            }
            $baris_lebar = false;
            foreach ( $rows as $row ) {
                $nilai = array();
                foreach ( $kolom as $k => $tipe ) {
                    $nilai[] = self::nilai( array_key_exists( $k, $row ) ? $row[ $k ] : null, $tipe );
                }
                $tuple = '(' . implode( ',', $nilai ) . ')';
                if ( strlen( $tuple ) > $batas_wajar ) {
                    $baris_lebar = true;
                }

                // Item 3c (Penting): baris yang SENDIRIAN (potongan minimal
                // berisi hanya baris ini) sudah melebihi batas KERAS tidak
                // akan PERNAH muat, seberapa pun kecilnya potongan --
                // dilaporkan sebagai galat tetap, TIDAK dilewati (ekspor
                // ini juga memberi data ke snapshot).
                if ( strlen( $awalan ) + strlen( $tuple ) + 2 > self::$maks_respon ) {
                    $wpdb->query( 'ROLLBACK' );
                    return self::galat_baris_besar( $tabel );
                }

                $tambahan = ( '' === $pernyataan ) ? ( $awalan . $tuple ) : ( ",\n" . $tuple );

                // Item 6a (Minor): PECAH SEBELUM melebihi batas satu
                // pernyataan (bukan sesudah menambahkan lalu memecah).
                if ( '' !== $pernyataan && strlen( $pernyataan ) + strlen( $tambahan ) > self::$maks_pernyataan ) {
                    $sql       .= $pernyataan . ";\n";
                    $pernyataan = '';
                    $tambahan   = $awalan . $tuple;
                }

                // Item 3a (Penting): anggaran LUNAK dicek SEBELUM
                // menambahkan -- hanya baris PERTAMA pada REQUEST ini yang
                // boleh melampauinya (jaminan progres: baris itu sendiri
                // sudah lolos cek batas KERAS di atas, jadi aman dikirim
                // utuh). Baris berikutnya yang akan melampaui dibiarkan
                // TIDAK ditambahkan -- diambil ulang di request berikutnya
                // lewat kursor yang belum berubah untuknya.
                if ( $jumlah > 0 && strlen( $sql ) + strlen( $pernyataan ) + strlen( $tambahan ) > self::$maks_byte ) {
                    $penuh = true;
                    break;
                }

                $pernyataan .= $tambahan;
                $jumlah++;
                $akhir = self::posisi_berikut( $pk, $row, $akhir );
            }
            if ( $baris_lebar ) {
                $sub_efektif = max( 1, intdiv( $sub_efektif, 2 ) );
            } else {
                // Item 2 (Penting), fix round 2: tumbuh kembali setelah
                // sub-batch yang semua barisnya kecil -- dijepit ke $sub
                // supaya tidak pernah melebihi konfigurasi.
                $sub_efektif = min( self::$sub, $sub_efektif * 2 );
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
            // Item 6h (Minor): dashboard bisa memperingatkan tabel tanpa PK
            // (LIMIT/OFFSET bisa melompati/menduplikasi baris bila tabel
            // ditulisi selama tarik -- lihat "Yang tidak dapat dipenuhi").
            'mode'    => empty( $pk ) ? 'offset' : 'pk',
        );
    }
}
