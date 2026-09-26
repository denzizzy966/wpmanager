<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Isi berkas untuk tarik dan snapshot (spec §6.2 langkah 2): satu paket
 * berisi beberapa berkas kecil, atau satu rentang byte berkas besar.
 *
 * Setiap nilai dari body permintaan adalah masukan penyerang walau HMAC-nya
 * sah (kontrak HMAC hanya membuktikan siapa pengirimnya, bukan bahwa isinya
 * masuk akal) -- semua tipe dan jumlah divalidasi di sini sebelum dipakai.
 *
 * === Kontrak berhenti-dini (R4, ruling controller review putaran 1) ===
 *
 * Draf awal (brief) menjumlah HANYA isi berkas terhadap $maks_paket, lalu
 * menolak SELURUH paket dengan 413 bila PAKET UTUH (isi + meta) ternyata
 * melebihi $maks_paket setelah disusun -- meta (path/mtime/hash per entri)
 * menambah overhead di atas isi, jadi permintaan yang isinya PERSIS di
 * batas (mis. rentang 8 MiB, atau paket berisi manifest seberat 8 MiB)
 * SELALU gagal 413 walau sepenuhnya sesuai kontrak ukuran. Dashboard tidak
 * bisa pulih dari itu (mengulang permintaan yang sama menghasilkan 413 yang
 * sama lagi -- tidak ada kemajuan).
 *
 * Kontrak baru untuk mode 'berkas': `ambil()` BERHENTI SEBELUM entri mana
 * pun yang akan melampaui salah satu dari tiga anggaran --
 *   (a) anggaran ISI: $maks_paket byte, diukur dari pembacaan SUNGGUHAN
 *       (bukan filesize(), yang bisa basi -- RF3);
 *   (b) anggaran META TERUKUR: dijaga aman di bawah
 *       WPMGR_Staging_Paket::MAKS_META (di batas_meta_aman(), 90% darinya)
 *       dengan menjumlah ukuran json_encode() setiap entri SEBELUM entri
 *       itu ditambahkan, bukan mengecek ukuran paket UTUH setelah selesai;
 *   (c) anggaran WAKTU: tenggat yang sama seperti /staging/manifest
 *       (WPMGR_Staging::anggaran_detik()), supaya request ini juga tidak
 *       pernah menabrak batas 30 detik hosting.
 * Hasilnya berisi entri 0..k-1 (k <= n) dengan penanda `lengkap` di level
 * meta: `false` bila berhenti dini (masih ada sisa yang belum diproses,
 * dashboard WAJIB meminta ulang path yang tersisa lewat permintaan
 * berikutnya), `true` bila seluruh daftar habis diproses. Sinyal ini
 * SENGAJA sesederhana mungkin: dashboard sudah tahu path mana yang ia
 * minta dan berapa entri yang kembali, jadi `lengkap` saja cukup --
 * ia tidak perlu tahu ALASAN berhenti (isi/meta/waktu), hanya bahwa ia
 * harus meminta sisanya.
 *
 * Jaminan kemajuan (progress guarantee, meniru habis() milik
 * WPMGR_Staging_Manifest): entri INDEKS 0 selalu diproses -- tenggat waktu
 * dan anggaran meta hanya dihormati mulai entri kedua dan seterusnya.
 * Tanpa ini, permintaan yang tenggatnya sudah lewat SEBELUM mulai (atau
 * anggaran meta yang kebetulan sudah nol) bisa kembali dengan NOL entri
 * dan `lengkap: false` selamanya -- dashboard mengulang permintaan yang
 * sama tanpa pernah maju.
 *
 * Pengecualian khusus: bila entri PERTAMA SENDIRI melebihi anggaran isi
 * (mis. berkas tumbuh raksasa sejak manifest dibuat), memberi jawaban
 * "berhenti dengan nol entri" juga tidak membuat dashboard bisa maju --
 * permintaan berikutnya akan mengulang berkas yang sama sebagai entri
 * pertama lagi. Untuk kasus ini `ambil()` mengembalikan SATU entri penanda
 * `{path, terlalu_besar: true, total, mtime}` dengan bagian isi kosong;
 * dashboard membaca ini sebagai sinyal "pakai mode 'rentang' untuk berkas
 * ini", yang tidak punya batas ukuran isi (lihat di bawah).
 *
 * Berkas yang sudah terhapus sejak manifest ditandai `hilang: true` (bagian
 * isi kosong) di KEDUA mode, bukan galat 404 -- dashboard menghapusnya di
 * staging. Berkas yang ADA tapi tiba-tiba tidak terbaca (izin berubah,
 * race TOCTOU antara pemeriksaan path dan pembacaan sungguhan) ditandai
 * `galat: 'baca'` (bagian isi kosong) di mode 'berkas' -- BUKAN galat 500;
 * satu berkas yang gagal tidak boleh menggagalkan seluruh potongan.
 *
 * Path berbahaya/dikecualikan (kode WP_Error APA PUN selain
 * 'wpmgr_staging_tidak_ada' dari WPMGR_Staging_Path::untuk_dibaca()) tetap
 * menolak SELURUH permintaan (400 wpmgr_staging_path) -- dashboard tidak
 * pernah memintanya lewat manifest yang sah; bila ia meminta, ada yang
 * salah di sisi pemanggil dan wajib terlihat sebagai galat keras, bukan
 * dilewati diam-diam seperti berkas yang hilang.
 *
 * Mode 'rentang' TIDAK memakai anggaran meta/berhenti-dini -- ia selalu
 * tepat SATU entri, jadi overhead meta-nya diabaikan (jauh dari batas)
 * dan tidak pernah dijadikan alasan 413. Satu-satunya alasan 413 di mode
 * ini (dan satu-satunya alasan 413 di seluruh kelas ini sekarang) adalah
 * BENTUK permintaan yang tidak sah: `panjang` melebihi $maks_paket. Berkas
 * yang hilang di mode ini mengembalikan paket satu-bagian bertanda
 * `hilang: true` (bukan galat 404), sama seperti mode 'berkas'.
 *
 * === Perbaikan (R5, ruling controller review putaran 2) ===
 *
 * Temuan 1 (Penting): ukuran_meta_entri() versi R4 mengukur SATU entri
 * SEBELUM 'ukuran' dan 'sha256' ditambahkan -- padahal
 * WPMGR_Staging_Paket::susun() SELALU menambahkan keduanya ke SETIAP
 * entri (`$berkas[$i]['ukuran'] = strlen($data); $berkas[$i]['sha256'] =
 * hash('sha256',$data);`), termasuk entri 'hilang'/'galat'/'terlalu_besar'
 * yang isinya kosong. Itu meninggalkan ~87-95 byte PER ENTRI tidak
 * terhitung (`,"ukuran":0` + `,"sha256":"<64 hex>"`), sehingga margin 10%
 * batas_meta_aman() (bukan "beberapa KB" seperti diklaim docblock R4 --
 * klaim itu SALAH, dikoreksi di sini) habis sekitar 1200 entri; 2000 path
 * tidak-ada sepanjang ~400-470 karakter lolos pengukuran yang salah lalu
 * MELEDAK di susun() sebagai 500 wpmgr_staging_susun PADA SETIAP
 * percobaan (dashboard tidak bisa pulih -- jaminan kemajuan rusak total).
 * Diperbaiki: ukuran_meta_entri() sekarang menerima ukuran isi SUNGGUHAN
 * ($ukuran_isi, sudah diketahui dari strlen($data) SEBELUM entri
 * ditambahkan) dan menambahkan 'ukuran' + placeholder sha256 64 karakter
 * (panjang hash sha256 heksadesimal SELALU 64 karakter, apa pun isinya --
 * placeholder ini memberi ukuran BYTE-PERSIS sama dengan hash sungguhan)
 * SEBELUM mengukur, plus satu byte koma pemisah antar entri di larik
 * 'berkas' -- persis kunci dan urutan yang dipakai susun().
 *
 * Temuan 2 (Minor): entri ke-i>0 yang SUDAH JELAS akan melampaui anggaran
 * isi atau meta (dari filesize()/filemtime(), stat MURAH) tidak perlu
 * dibaca isinya (sampai beberapa MB) hanya untuk dibuang -- perkiraan
 * dicek DULU, pembacaan isi sungguhan (file_get_contents) menyusul HANYA
 * bila perkiraan itu lolos kedua anggaran.
 *
 * Temuan 3 (Minor): cabang galat:'baca' (berkas ada tapi tiba-tiba tidak
 * terbaca) tidak punya test yang benar-benar berjalan -- chmod(0000) tidak
 * ditegakkan di Windows, dan tidak ditegakkan bagi proses yang berjalan
 * sebagai root (default kontainer Docker yang dipakai suite PHP 7.4).
 * Ditambahkan celah uji baca_isi()/$pembaca_isi (lihat di bawah) supaya
 * cabang ini bisa diuji deterministik di kedua platform tanpa bergantung
 * pada penegakan izin OS; test chmod tetap dipertahankan sebagai regresi
 * dunia-nyata di platform yang menegakkannya.
 */
class WPMGR_Staging_File {

    const MAKS_JUMLAH = 2000;

    public static $maks_paket = 8388608;

    /**
     * Pembaca isi berkas yang bisa diganti TEST (temuan 3, review putaran
     * 2): null berarti "pakai file_get_contents() sungguhan" (lihat
     * baca_isi()). Tetap protected -- bukan bagian API publik kelas ini,
     * hanya diubah lewat atur_pembaca_untuk_uji().
     * @var callable|null
     */
    protected static $pembaca_isi = null;

    /**
     * Hanya dipakai test: mengganti pembaca isi berkas sungguhan dengan
     * pengganti (mis. yang selalu mengembalikan false) untuk menguji jalur
     * galat:'baca' tanpa bergantung pada chmod, yang tidak ditegakkan di
     * Windows maupun oleh proses yang berjalan sebagai root. Panggil
     * dengan null untuk mengembalikan ke pembaca sungguhan.
     */
    public static function atur_pembaca_untuk_uji( $pembaca ) {
        self::$pembaca_isi = $pembaca;
    }

    private static function baca_isi( $abs, $sisa ) {
        if ( null !== self::$pembaca_isi ) {
            return call_user_func( self::$pembaca_isi, $abs, $sisa );
        }
        // Batasi pembacaan ke (sisa+1) byte -- cukup untuk mendeteksi
        // "melebihi sisa anggaran" tanpa pernah memuat berkas yang sudah
        // tumbuh raksasa (RF3) seutuhnya ke memori.
        return @file_get_contents( $abs, false, null, 0, $sisa + 1 ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
    }

    private static function salah( $pesan ) {
        return WPMGR_Staging::galat( 'wpmgr_staging_permintaan', $pesan, 400 );
    }

    private static function terlalu_besar() {
        return WPMGR_Staging::galat( 'wpmgr_staging_terlalu_besar', 'Permintaan melebihi batas 8 MB per potongan.', 413 );
    }

    /**
     * meta['berkas'] wajib berupa DAFTAR (kunci 0..n-1 berurutan), bukan
     * objek JSON dengan kunci acak -- pemanggil mencocokkan hasil ke path
     * yang diminta berdasarkan URUTAN, dan kunci yang tidak berurutan
     * (hasil `{"0":...,"2":...}` yang direkayasa tangan) membuat pencocokan
     * itu tidak lagi bisa dipercaya. Sama seperti kunci_berurutan() di
     * WPMGR_Staging_Paket (privat di sana, jadi diduplikasi kecil di sini).
     */
    private static function daftar_berurutan( array $x ) {
        return array_keys( $x ) === ( empty( $x ) ? array() : range( 0, count( $x ) - 1 ) );
    }

    /**
     * Ukuran json_encode() SATU entri meta PERSIS seperti yang akan muncul
     * di paket akhir -- termasuk 'ukuran' dan 'sha256' yang ditambahkan
     * WPMGR_Staging_Paket::susun() SETELAH entri diukur (fix temuan 1,
     * review putaran 2 -- lihat docblock kelas), dan satu byte koma
     * pemisah antar entri di larik 'berkas'. $ukuran_isi adalah panjang
     * SUNGGUHAN bagian isi entri ini (sudah diketahui sebelum dipanggil,
     * strlen($data)) dipakai sebagai placeholder 'ukuran' (jumlah digit
     * cocok persis dengan nilai yang akan ditulis susun()); placeholder
     * sha256 64 karakter cocok persis ukurannya (panjang hash sha256
     * heksadesimal SELALU 64 karakter, apa pun isinya).
     *
     * wp_json_encode()/json_encode() TANPA opsi unescaped meng-escape
     * unicode ('\uXXXX', 6 byte) dan slash ('\/', 2 byte) -- selalu >=
     * ukuran sungguhan yang dipakai susun() (yang memakai
     * JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES) untuk path yang sama,
     * jadi bagian 'path' dari perkiraan ini tetap aman sebagai batas atas;
     * bagian 'ukuran'/'sha256'/koma sekarang PERSIS sama (bukan hanya batas
     * atas) dengan yang ditulis susun().
     */
    private static function ukuran_meta_entri( array $entri, $ukuran_isi ) {
        $entri['ukuran'] = (int) $ukuran_isi;
        $entri['sha256'] = str_repeat( '0', 64 );
        $enc             = function_exists( 'wp_json_encode' ) ? wp_json_encode( $entri ) : json_encode( $entri );
        // +1: koma pemisah antar entri di larik 'berkas' -- susun() meng-
        // gabung seluruh entri dengan satu json_encode() atas larik utuh,
        // bukan menyusun potongan string satu-satu.
        return ( false === $enc ? 512 : strlen( $enc ) ) + 1;
    }

    /**
     * 90% dari batas keras WPMGR_Staging_Paket::MAKS_META -- margin aman
     * untuk overhead PEMBUNGKUS LEVEL-ATAS (`{"berkas":[...],"lengkap":
     * ...}`, sekitar 30 byte TETAP, tidak bergantung jumlah entri) yang
     * tidak ikut terhitung di ukuran_meta_entri() (fungsi itu sendiri
     * sudah menghitung 'ukuran'/'sha256'/koma persis seperti susun() --
     * lihat catatan "fix temuan 1" di sana). Margin 10% (~100 KB pada
     * MAKS_META 1 MiB) jauh melebihi overhead tetap itu.
     */
    private static function batas_meta_aman() {
        return (int) floor( WPMGR_Staging_Paket::MAKS_META * 0.9 );
    }

    /**
     * Bungkus meta+isi jadi paket biner. susun() SEHARUSNYA tidak pernah
     * melempar di sini -- anggaran meta sudah diukur SEBELUM entri
     * ditambahkan (lihat batas_meta_aman()) -- tetapi tetap dibungkus
     * try/catch sebagai jaring pengaman: kegagalan yang tak terduga
     * menjadi WP_Error 500, bukan fatal error PHP yang tidak tertangkap.
     */
    private static function bungkus_atau_500( array $meta, array $isi ) {
        try {
            return WPMGR_Staging_Paket::susun( $meta, $isi );
        } catch ( InvalidArgumentException $e ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_susun', 'Paket staging tidak dapat disusun.', 500 );
        }
    }

    /**
     * Penanda satu-entri "berkas ini melampaui anggaran isi" (lihat
     * docblock kelas, "Pengecualian khusus"). $total_diketahui, bila
     * sudah ada (dari perkiraan filesize() di ambil()), menghindari
     * filesize() kedua kalinya; bila null, diukur ulang di sini (mis.
     * dipanggil setelah pembacaan sungguhan MELEBIHI sisa anggaran --
     * perkiraan awal sudah basi, perlu ukuran segar).
     */
    private static function paket_terlalu_besar( $rel, $abs, $total_diketahui = null ) {
        if ( null === $total_diketahui ) {
            clearstatcache( true, $abs );
            $total_diketahui = (int) @filesize( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
        return self::bungkus_atau_500(
            array(
                'berkas'  => array( array(
                    'path'          => $rel,
                    'terlalu_besar' => true,
                    'total'         => (int) $total_diketahui,
                    'mtime'         => (int) @filemtime( $abs ), // phpcs:ignore WordPress.PHP.NoSilencedErrors
                ) ),
                'lengkap' => false,
            ),
            array( '' )
        );
    }

    public static function ambil( $akar, $p, $tenggat = null ) {
        if ( ! is_array( $p ) ) {
            return self::salah( 'Body permintaan bukan objek.' );
        }
        if ( isset( $p['rentang'] ) && isset( $p['berkas'] ) ) {
            return self::salah( 'Body memuat rentang dan berkas sekaligus.' );
        }
        if ( isset( $p['rentang'] ) ) {
            return self::rentang( $akar, $p['rentang'] );
        }
        if ( ! isset( $p['berkas'] ) || ! is_array( $p['berkas'] ) || ! self::daftar_berurutan( $p['berkas'] )
            || count( $p['berkas'] ) < 1 || count( $p['berkas'] ) > self::MAKS_JUMLAH ) {
            return self::salah( 'Daftar berkas kosong, bukan daftar berurutan, atau terlalu panjang.' );
        }
        if ( null === $tenggat ) {
            // Anggaran waktu yang sama dengan /staging/manifest -- request
            // ini juga tidak boleh menabrak batas max_execution_time hosting.
            $tenggat = microtime( true ) + WPMGR_Staging::anggaran_detik();
        }

        $daftar       = array_values( $p['berkas'] );
        $meta         = array();
        $isi          = array();
        $konten_pakai = 0;
        $meta_pakai   = 0;
        $batas_meta   = self::batas_meta_aman();
        $lengkap      = true;

        foreach ( $daftar as $i => $rel ) {
            if ( ! is_string( $rel ) ) {
                return self::salah( 'Path berkas bukan string.' );
            }
            // Jaminan kemajuan: tenggat hanya dihormati mulai entri KEDUA --
            // entri pertama SELALU diproses, supaya permintaan yang
            // tenggatnya sudah lewat sebelum mulai tidak pernah kembali
            // dengan nol entri (lihat docblock kelas).
            if ( $i > 0 && microtime( true ) >= $tenggat ) {
                $lengkap = false;
                break;
            }

            $abs = WPMGR_Staging_Path::untuk_dibaca( $akar, $rel );
            if ( is_wp_error( $abs ) ) {
                if ( 'wpmgr_staging_tidak_ada' !== $abs->get_error_code() ) {
                    // Path berbahaya/dikecualikan menolak SELURUH permintaan.
                    return $abs;
                }
                $entri = array( 'path' => $rel, 'hilang' => true );
                $data  = '';
            } else {
                $sisa = self::$maks_paket - $konten_pakai;

                // Perkiraan MURAH (filesize/filemtime) dulu -- entri yang
                // sudah jelas akan menabrak anggaran isi ATAU meta dari
                // perkiraan ini tidak perlu membaca isinya (sampai
                // beberapa MB) hanya untuk dibuang (fix temuan 2, review
                // putaran 2). Bila filesize() gagal (race TOCTOU: berkas
                // hilang persis di celah ini), perkiraan dilewati dan alur
                // baca sungguhan di bawah yang memutuskan.
                clearstatcache( true, $abs );
                $ukuran_dini = @filesize( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors

                if ( false !== $ukuran_dini && $ukuran_dini > $sisa ) {
                    if ( 0 === $i ) {
                        return self::paket_terlalu_besar( $rel, $abs, $ukuran_dini );
                    }
                    $lengkap = false;
                    break;
                }
                if ( $i > 0 && false !== $ukuran_dini ) {
                    $entri_dini = array( 'path' => $rel, 'mtime' => (int) @filemtime( $abs ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
                    if ( $meta_pakai + self::ukuran_meta_entri( $entri_dini, $ukuran_dini ) > $batas_meta ) {
                        $lengkap = false;
                        break;
                    }
                }

                $baca = self::baca_isi( $abs, $sisa );
                if ( false === $baca ) {
                    // Berkas ADA (lolos untuk_dibaca()) tapi tiba-tiba tidak
                    // terbaca (izin berubah, race TOCTOU): ditandai per-berkas,
                    // BUKAN galat 500 -- entri lain dalam paket yang sama
                    // tetap harus terlayani.
                    $entri = array( 'path' => $rel, 'galat' => 'baca' );
                    $data  = '';
                } elseif ( strlen( $baca ) > $sisa ) {
                    // Berkas tumbuh di antara perkiraan filesize() dan
                    // pembacaan sungguhan (RF3): perkiraan sudah basi,
                    // ukur ulang untuk penanda/berhenti di bawah.
                    if ( 0 === $i ) {
                        return self::paket_terlalu_besar( $rel, $abs );
                    }
                    $lengkap = false;
                    break;
                } else {
                    clearstatcache( true, $abs );
                    $entri = array( 'path' => $rel, 'mtime' => (int) @filemtime( $abs ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
                    $data  = $baca;
                }
            }

            $ukuran_meta = self::ukuran_meta_entri( $entri, strlen( $data ) );
            if ( $i > 0 && $meta_pakai + $ukuran_meta > $batas_meta ) {
                $lengkap = false;
                break;
            }
            $meta_pakai   += $ukuran_meta;
            $konten_pakai += strlen( $data );
            $meta[]        = $entri;
            $isi[]         = $data;
        }

        return self::bungkus_atau_500( array( 'berkas' => $meta, 'lengkap' => $lengkap ), $isi );
    }

    public static function rentang( $akar, $r ) {
        if ( ! is_array( $r ) || ! isset( $r['path'], $r['dari'], $r['panjang'] ) || ! is_string( $r['path'] )
            || ! is_int( $r['dari'] ) || ! is_int( $r['panjang'] ) || $r['dari'] < 0 || $r['panjang'] < 1 ) {
            return self::salah( 'Rentang tidak sah.' );
        }
        // Satu-satunya alasan 413 di mode ini: BENTUK permintaan tidak sah
        // (panjang melebihi anggaran isi). Meta satu entri tidak pernah
        // dijadikan alasan 413 -- lihat docblock kelas.
        if ( $r['panjang'] > self::$maks_paket ) {
            return self::terlalu_besar();
        }
        $abs = WPMGR_Staging_Path::untuk_dibaca( $akar, $r['path'] );
        if ( is_wp_error( $abs ) ) {
            if ( 'wpmgr_staging_tidak_ada' === $abs->get_error_code() ) {
                // Terhapus sejak manifest: paket satu-bagian "hilang", bukan
                // galat 404 -- sama seperti mode 'berkas'.
                return self::bungkus_atau_500(
                    array( 'berkas' => array( array( 'path' => $r['path'], 'hilang' => true, 'total' => 0 ) ) ),
                    array( '' )
                );
            }
            return $abs;
        }
        clearstatcache( true, $abs );
        $total = (int) @filesize( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        $mtime = (int) @filemtime( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        $data  = '';
        if ( $r['dari'] < $total ) {
            // Berkas besar: dibaca lewat fopen/fseek/fread pada path KANONIK
            // yang sudah divalidasi (bukan $akar.$rel mentah), berpotongan
            // 1 MiB supaya panjang besar tidak butuh satu buffer fread() raksasa.
            $h = @fopen( $abs, 'rb' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( false === $h ) {
                return WPMGR_Staging::galat( 'wpmgr_staging_baca', 'Berkas tidak dapat dibaca.', 500 );
            }
            fseek( $h, $r['dari'] );
            while ( strlen( $data ) < $r['panjang'] && ! feof( $h ) ) {
                $potong = fread( $h, min( 1048576, $r['panjang'] - strlen( $data ) ) );
                if ( false === $potong || '' === $potong ) {
                    // Berkas menyusut di antara filesize() dan pembacaan
                    // (RF3): berhenti dengan apa yang benar-benar terbaca,
                    // bukan menunggu/gagal fatal.
                    break;
                }
                $data .= $potong;
            }
            fclose( $h );
        }
        return self::bungkus_atau_500(
            array( 'berkas' => array( array( 'path' => $r['path'], 'dari' => $r['dari'], 'total' => $total, 'mtime' => $mtime ) ) ),
            array( $data )
        );
    }
}
