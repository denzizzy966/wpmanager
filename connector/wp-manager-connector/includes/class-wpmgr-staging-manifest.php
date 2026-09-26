<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Manifest untuk tarik (spec §6.2 langkah 1): daftar berkas dengan ukuran,
 * mtime, dan sha256 (hanya ≤ 50 MB), dipaging lewat kursor supaya site
 * dengan ratusan ribu berkas tetap muat dalam batas 30 detik per request.
 *
 * Aturan path dipusatkan di WPMGR_Staging_Path (Task 2): symlink (di
 * komponen mana pun), nama bukan UTF-8, segmen berisi ':' atau berakhiran
 * '.'/spasi, dan nama terlalu panjang semuanya ditolak fungsi itu. Manifest
 * memakainya di sini supaya tidak pernah mendaftar path yang nanti ditolak
 * /staging/file -- symlink dicek langsung (penelusuran tidak pernah masuk
 * ke direktori symlink), dan nama berkas non-UTF-8/tidak sah dicek lewat
 * normalisasi() sebelum berkas itu dimasukkan ke hasil.
 *
 * Kontrak respons (fix R3, review putaran 1, item 1): `kursor` bisa menunjuk
 * ke entri yang DILEWATI (symlink, dikecualikan, tidak terbaca), bukan
 * hanya yang benar-benar dikirim -- tenggat waktu diperiksa pada SETIAP
 * entri yang dikunjungi, termasuk yang dilewati, supaya direktori berisi
 * banyak entri anomali tidak bisa berjalan lewat batas waktu tanpa kursor
 * pernah maju. Akibatnya satu halaman bisa berisi `berkas: []` dengan
 * `lagi: true` dan kursor yang tetap maju -- pemanggil (dashboard, task
 * lanjutan) HARUS menerima halaman kosong seperti itu dan memakai kursor
 * baru pada permintaan berikutnya, bukan menganggapnya selesai. Residu yang
 * disengaja: nama bukan UTF-8 tidak pernah bisa dijadikan kursor (kursor
 * dikirim balik sebagai string dan divalidasi ulang lewat normalisasi() di
 * WPMGR_Staging::manifest()); nama seperti itu dilewati lagi dengan biaya
 * murah (satu preg_match) pada setiap halaman berikutnya sampai terlewati.
 */
class WPMGR_Staging_Manifest {

    const BATAS_ENTRI    = 5000;
    const MAKS_KEDALAMAN = 64;
    const MAKS_CONTOH    = 50;

    public static $maks_hash          = 52428800;
    public static $anggaran_hash      = 536870912;
    // Item 5a (review putaran 1): perkiraan ukuran terkode JSON tempat
    // halaman berhenti walau batas jumlah entri (batas()) belum tercapai --
    // path yang sangat panjang bisa membuat 5000 entri jauh melebihi batas
    // ukuran request/respons connector (spec: <= 8 MB).
    public static $maks_bytes_halaman = 6291456;

    public static function batas( $nilai ) {
        $n = (int) $nilai;
        return ( $n < 1 || $n > self::BATAS_ENTRI ) ? self::BATAS_ENTRI : $n;
    }

    public static function jalan( $akar, $kursor, $batas, $tenggat ) {
        $kursor = (string) $kursor;
        $ctx    = array(
            'berkas'          => array(),
            'dilewati'        => array(),
            'jumlah_dilewati' => 0,
            'batas'           => self::batas( $batas ),
            'tenggat'         => (float) $tenggat,
            'terhash'         => 0,
            'bytes_hal'       => 0,
            'kursor_masuk'    => $kursor,
            'kursor_kandidat' => null,
            'berhenti'        => false,
        );
        $bagian = ( '' === $kursor ) ? array() : explode( '/', $kursor );
        self::telusuri( $akar, '', $bagian, ! empty( $bagian ), $ctx, 0 );
        return array(
            'berkas'          => $ctx['berkas'],
            'dilewati'        => $ctx['dilewati'],
            'jumlah_dilewati' => $ctx['jumlah_dilewati'],
            'kursor'          => $ctx['berhenti'] ? $ctx['kursor_kandidat'] : null,
            'lagi'            => $ctx['berhenti'] && null !== $ctx['kursor_kandidat'],
        );
    }

    private static function lewati( array &$ctx, $rel, $alasan ) {
        $ctx['jumlah_dilewati']++;
        if ( count( $ctx['dilewati'] ) < self::MAKS_CONTOH ) {
            // Nama yang dilewati bisa berupa byte bukan UTF-8; hanya versi
            // yang sudah dibersihkan yang dikirim, sebagai teks tampilan.
            $ctx['dilewati'][] = array( 'path' => WPMGR_Staging::bersih( $rel, 300 ), 'alasan' => $alasan );
        }
    }

    /**
     * Kandidat kursor: path entri yang baru saja dikunjungi (dikirim MAUPUN
     * dilewati), disimpan HANYA bila path itu sendiri lolos normalisasi().
     * Kursor dikembalikan sebagai string dan dikirim balik oleh dashboard
     * pada permintaan berikutnya, yang divalidasi ulang lewat normalisasi()
     * juga (lihat WPMGR_Staging::manifest()) -- path yang gagal validasi
     * itu (nama bukan UTF-8, mengandung ':', berakhiran '.'/spasi, atau
     * terlalu panjang) tidak aman dijadikan kursor karena permintaan
     * lanjutan dengan kursor itu akan ditolak 400, bukan melanjutkan.
     *
     * Fix item 1 (review putaran 2): kursor KELUAR tidak pernah boleh sama
     * dengan kursor MASUK ($ctx['kursor_masuk']). Tanpa pagar ini, direktori
     * yang dikecualikan TEPAT di posisi kursor (mis. kursor menunjuk ke
     * 'wp-content/cache' itu sendiri, sebuah direktori yang dikecualikan)
     * mencatat ulang path yang sama persis sebagai kandidat kursor baru;
     * bila tenggat lalu berhenti pada entri berikutnya, halaman itu
     * mengembalikan kursor yang identik dengan kursor masuk -- paging
     * tidak pernah maju (dashboard memanggil ulang dengan kursor yang sama
     * selamanya). Lihat ManifestTest::test_kursor_tidak_macet_pada_direktori_dikecualikan.
     */
    private static function catat_kursor( array &$ctx, $rel ) {
        if ( $rel === $ctx['kursor_masuk'] ) {
            return;
        }
        if ( ! is_wp_error( WPMGR_Staging_Path::normalisasi( $rel ) ) ) {
            $ctx['kursor_kandidat'] = $rel;
        }
    }

    /**
     * Tenggat hanya aktif setelah ADA kandidat kursor yang tercatat: ini
     * menjamin setiap request memvisit setidaknya satu entri (terkirim
     * ATAU dilewati) sebelum bisa berhenti, sehingga kursor selalu maju
     * walau tenggat sudah lewat sebelum entri pertama diproses.
     */
    private static function habis( array $ctx ) {
        return null !== $ctx['kursor_kandidat'] && microtime( true ) >= $ctx['tenggat'];
    }

    /**
     * Item 2 (review putaran 2): perkiraan ukuran sebelumnya
     * (strlen(path) + konstanta) meremehkan ukuran JSON SUNGGUHAN sampai
     * ~3x untuk path yang berisi banyak karakter non-ASCII -- REST server
     * WordPress meng-encode respons lewat wp_json_encode(), yang (seperti
     * json_encode() dengan opsi bawaan) meng-escape setiap karakter
     * non-ASCII menjadi '\uXXXX' (6 byte) dan setiap '/' menjadi '\/'
     * (2 byte). Perkiraan yang meremehkan membuat $maks_bytes_halaman
     * hampir tidak pernah tercapai justru pada kasus yang paling
     * membutuhkannya (path panjang, banyak karakter non-ASCII). Di sini
     * ukuran diambil dari hasil encode SUNGGUHAN per entri: wp_json_encode()
     * bila tersedia (WordPress sungguhan), atau json_encode() dengan opsi
     * bawaan yang sama (tanpa JSON_UNESCAPED_UNICODE/JSON_UNESCAPED_SLASHES)
     * sebagai cadangan di lingkungan test PHP murni.
     */
    private static function ukuran_json_entri( array $entri ) {
        $enc = function_exists( 'wp_json_encode' ) ? wp_json_encode( $entri ) : json_encode( $entri );
        return false === $enc ? ( strlen( $entri['path'] ) + 96 ) : strlen( $enc );
    }

    private static function telusuri( $akar, $rel_dir, array $kursor, $selaras, array &$ctx, $kedalaman ) {
        if ( $kedalaman > self::MAKS_KEDALAMAN ) {
            self::lewati( $ctx, rtrim( $rel_dir, '/' ), 'terlalu_dalam' );
            return;
        }
        $nama = @scandir( $akar . $rel_dir ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $nama ) {
            self::lewati( $ctx, rtrim( $rel_dir, '/' ), 'tidak_terbaca' );
            return;
        }
        sort( $nama, SORT_STRING );
        $target   = ( $selaras && isset( $kursor[ $kedalaman ] ) ) ? $kursor[ $kedalaman ] : null;
        $terakhir = count( $kursor ) - 1;
        foreach ( $nama as $n ) {
            if ( $ctx['berhenti'] ) {
                return;
            }
            if ( '.' === $n || '..' === $n ) {
                continue;
            }
            $masuk_selaras      = false;
            $lewati_leaf_kursor = false;
            if ( null !== $target ) {
                $banding = strcmp( $n, $target );
                if ( $banding < 0 ) {
                    continue;
                }
                if ( 0 === $banding ) {
                    if ( $kedalaman === $terakhir ) {
                        $lewati_leaf_kursor = true;
                    } else {
                        $masuk_selaras = true;
                    }
                }
            }

            $rel = $rel_dir . $n;
            $abs = $akar . $rel;

            // Fix 5c (review putaran 1): kursor menunjuk ke NAMA ini sebagai
            // berkas yang sudah terkirim pada halaman sebelumnya. Bila di
            // antara dua request berkas itu dihapus dan digantikan
            // direktori BERNAMA SAMA, isi direktori itu belum pernah
            // dikirim sama sekali -- melewatinya begitu saja (perilaku
            // lama) akan menghilangkan semua berkas di dalamnya dari
            // manifest selamanya. Hanya lewati bila nama ini SUNGGUH BUKAN
            // direktori (symlink tetap ditangani lewat jalur symlink biasa
            // di bawah, bukan di sini).
            if ( $lewati_leaf_kursor ) {
                if ( is_dir( $abs ) && ! is_link( $abs ) ) {
                    $masuk_selaras = false; // tidak ada info penyelarasan lagi di bawah level ini
                } else {
                    continue;
                }
            }

            // Item 1 (review putaran 1): tenggat waktu diperiksa di SINI,
            // pada SETIAP entri yang benar-benar dikunjungi -- termasuk
            // yang akan dilewati (symlink, dikecualikan, tidak terbaca,
            // dst.), bukan hanya saat ada berkas yang terkirim. Tanpa ini,
            // direktori berisi ribuan entri yang semuanya dilewati (mis.
            // ribuan *.log, atau symlink) bisa berjalan lewat batas waktu
            // 30 detik tanpa pernah berhenti, DAN tanpa kursor pernah maju
            // -- request berikutnya mengulang dari awal dan macet
            // selamanya. habis() hanya aktif setelah ADA kandidat kursor
            // yang tercatat (lihat catat_kursor()), supaya setiap request
            // tetap dijamin maju walau tenggat sudah lewat sebelum entri
            // pertama diproses.
            if ( self::habis( $ctx ) ) {
                $ctx['berhenti'] = true;
                return;
            }

            if ( 1 !== preg_match( '//u', $n ) ) {
                self::lewati( $ctx, $rel, 'nama_bukan_utf8' );
                continue; // catat_kursor() TIDAK dipanggil -- lihat catatan residu di docblock kelas.
            }
            if ( is_link( $abs ) ) {
                self::lewati( $ctx, $rel, 'symlink' );
                self::catat_kursor( $ctx, $rel );
                continue;
            }
            if ( is_dir( $abs ) ) {
                // Item 3 (review putaran 2, sebelumnya duplikasi lokal
                // direktori_dikecualikan()): dikecualikan() milik Task 2
                // dipanggil dengan '/' di akhir supaya aturan yang hanya
                // masuk akal untuk BERKAS (akhiran '.log', 'wp-config.php',
                // '.maintenance') tidak pernah cocok -- karakter terakhir
                // string yang diuji selalu '/', bukan huruf nama berkas --
                // sementara aturan KHUSUS direktori (cache, area sementara
                // dorong, isi backup plugin) tetap cocok seperti biasa,
                // karena semuanya diuji lewat awalan direktori, bukan
                // akhiran nama. Satu sumber kebenaran; berkas Task 2 tidak
                // disentuh.
                if ( WPMGR_Staging_Path::dikecualikan( $rel . '/' ) ) {
                    self::catat_kursor( $ctx, $rel );
                    continue;
                }
                self::telusuri( $akar, $rel . '/', $kursor, $masuk_selaras, $ctx, $kedalaman + 1 );
                continue;
            }
            if ( ! is_file( $abs ) || WPMGR_Staging_Path::dikecualikan( $rel ) ) {
                self::catat_kursor( $ctx, $rel );
                continue;
            }
            if ( is_wp_error( WPMGR_Staging_Path::normalisasi( $rel ) ) ) {
                self::lewati( $ctx, $rel, 'path_tidak_sah' );
                continue; // rel sendiri gagal normalisasi: tidak aman dijadikan kursor.
            }
            $ukuran = @filesize( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            $mtime  = @filemtime( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( false === $ukuran || false === $mtime || ! is_readable( $abs ) ) {
                self::lewati( $ctx, $rel, 'tidak_terbaca' );
                self::catat_kursor( $ctx, $rel );
                continue;
            }
            $perlu_hash = $ukuran <= self::$maks_hash;
            if ( $perlu_hash && $ctx['terhash'] > 0 && $ctx['terhash'] + $ukuran > self::$anggaran_hash ) {
                $ctx['berhenti'] = true;
                return;
            }
            $hash = null;
            if ( $perlu_hash ) {
                $hash = @hash_file( 'sha256', $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
                if ( false === $hash ) {
                    self::lewati( $ctx, $rel, 'tidak_terbaca' );
                    self::catat_kursor( $ctx, $rel );
                    continue;
                }
                $ctx['terhash'] += $ukuran;
            }
            $entri             = array( 'path' => $rel, 'ukuran' => (int) $ukuran, 'mtime' => (int) $mtime, 'hash' => $hash );
            $ctx['berkas'][]   = $entri;
            $ctx['bytes_hal'] += self::ukuran_json_entri( $entri );
            self::catat_kursor( $ctx, $rel );
            if ( count( $ctx['berkas'] ) >= $ctx['batas'] || $ctx['bytes_hal'] >= self::$maks_bytes_halaman ) {
                $ctx['berhenti'] = true;
                return;
            }
        }
    }

    public static function nama_tabel_sah( $nama, $prefix ) {
        return 1 === preg_match( '/^[A-Za-z0-9_$]{1,64}\z/', (string) $nama ) && 0 === strpos( (string) $nama, (string) $prefix );
    }

    public static function pk( $wpdb, $tabel ) {
        $kunci = $wpdb->get_results( "SHOW KEYS FROM `{$tabel}` WHERE Key_name = 'PRIMARY'", ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL -- nama tabel sudah divalidasi regex
        $kunci = is_array( $kunci ) ? $kunci : array();
        usort( $kunci, function ( $a, $b ) {
            return (int) $a['Seq_in_index'] - (int) $b['Seq_in_index'];
        } );
        $kolom = array();
        foreach ( $kunci as $k ) {
            $nama = isset( $k['Column_name'] ) ? (string) $k['Column_name'] : '';
            // Item 6f (Minor), fix round 1 (Task 5): disamakan dengan aturan
            // nama kolom WPMGR_Staging_Tabel::kolom() -- backtick-quoting
            // hanya butuh "tidak ada backtick/karakter kontrol", bukan
            // identifier ASCII murni; regex lama menolak nama kolom PK yang
            // SAH (mis. mengandung spasi atau huruf non-ASCII) sebagai
            // "tidak bisa dikutip aman", padahal bisa.
            if ( 1 !== preg_match( '/^[^\x00-\x1f`]{1,64}\z/u', $nama ) ) {
                // Nama kolom yang tidak bisa kita kutip dengan aman: perlakukan
                // tabel ini sebagai tanpa PK (LIMIT/OFFSET).
                return array();
            }
            $kolom[] = $nama;
        }
        return $kolom;
    }

    public static function tabel( $wpdb ) {
        $baris = $wpdb->get_results( $wpdb->prepare( 'SHOW TABLE STATUS LIKE %s', $wpdb->esc_like( $wpdb->prefix ) . '%' ), ARRAY_A );
        // Fix I2 (review putaran 1, Task 7): pada database bersama, site
        // LAIN dengan prefix lebih panjang yang tumpang tindih (mis. site
        // ini 'wp_', site lain 'wp_abc_') membuat tabelnya sendiri lolos
        // pencocokan awalan sederhana nama_tabel_sah() di atas -- dihitung
        // sekali dari SELURUH baris SHOW TABLE STATUS yang cocok (yang
        // sudah tentu memuat tabel opsi milik site asing itu bila ada),
        // lihat WPMGR_Staging_Db::tabel_milik_site().
        $nama_milik = WPMGR_Staging_Db::tabel_milik_site(
            array_map( function ( $b ) {
                return isset( $b['Name'] ) ? (string) $b['Name'] : '';
            }, (array) $baris ),
            $wpdb->prefix
        );
        $hasil    = array();
        $dilewati = 0;
        foreach ( (array) $baris as $b ) {
            $nama = isset( $b['Name'] ) ? (string) $b['Name'] : '';
            if ( ! self::nama_tabel_sah( $nama, $wpdb->prefix ) || ! in_array( $nama, $nama_milik, true ) ) {
                $dilewati++;
                continue;
            }
            if ( empty( $b['Engine'] ) ) {
                // VIEW (Comment = 'VIEW') tidak diekspor, dibuat ulang oleh
                // plugin pemiliknya -- dilewati diam-diam seperti sebelumnya.
                // Item 5g (review putaran 1): Engine NULL yang BUKAN view
                // (mis. tabel rusak) dihitung di tabel_dilewati supaya
                // dashboard bisa memperingatkan, bukan hilang tanpa jejak.
                if ( 'VIEW' !== ( isset( $b['Comment'] ) ? (string) $b['Comment'] : '' ) ) {
                    $dilewati++;
                }
                continue;
            }
            if ( count( $hasil ) >= 2000 ) {
                $dilewati++;
                continue;
            }
            $hasil[] = array(
                'nama'   => $nama,
                // 'baris' (Rows) adalah ESTIMASI untuk InnoDB (statistik
                // kardinalitas indeks), bukan hitungan pasti -- item 5h.
                'baris'  => (int) $b['Rows'],
                'ukuran' => (int) $b['Data_length'] + (int) $b['Index_length'],
                'mesin'  => WPMGR_Staging::bersih( isset( $b['Engine'] ) ? (string) $b['Engine'] : '', 64 ),
                'pk'     => self::pk( $wpdb, $nama ),
            );
        }
        return array( $hasil, $dilewati );
    }

    /**
     * Meniru penguraian PHP untuk nilai bergaya "8M"/"512K"/"2G"
     * (post_max_size, upload_max_filesize, dst.): angka di depan diambil
     * sampai karakter bukan digit pertama (bagian desimal seperti '.5' pada
     * '1.5M' diabaikan, seperti zend_atol()), lalu karakter TERAKHIR dari
     * string dipakai sebagai akhiran satuan bila K/M/G. Fix item 4 (review
     * putaran 1): regex lama menuntut string SELURUHNYA berupa digit +
     * akhiran, sehingga '1.5M' gagal total dan dianggap 0 byte.
     */
    public static function ke_byte( $nilai ) {
        $nilai = trim( (string) $nilai );
        if ( ! preg_match( '/^([0-9]+)/', $nilai, $m ) ) {
            return 0;
        }
        $n     = (int) $m[1];
        $akhir = strtolower( substr( $nilai, -1 ) );
        $kali  = array( 'k' => 1024, 'm' => 1048576, 'g' => 1073741824 );
        return $n * ( isset( $kali[ $akhir ] ) ? $kali[ $akhir ] : 1 );
    }

    /**
     * Koreksi #15: body 8 MB ditolak hosting dengan post_max_size=8M --
     * potongan unggah paling besar separuh post_max_size, dijepit ke 4 MB
     * di atas.
     *
     * Fix item 4 (review putaran 1): batas BAWAH 256 KB HANYA berlaku bila
     * itu tidak melebihi separuh post_max_size -- pada post_max_size <
     * 512 KB, menjepit ke 256 KB membuat SATU potongan lebih besar dari
     * post_max_size itu sendiri (ditolak PHP, persis masalah yang batas ini
     * seharusnya mencegah). Pada kasus itu potongan mengikuti separuh
     * post_max_size apa adanya (kecil tapi valid); unggah_kecil() menandai
     * kasus ini supaya dashboard bisa memperingatkan operator.
     */
    public static function batas_unggah( $post_max_size ) {
        $b = self::ke_byte( $post_max_size );
        if ( $b <= 0 ) {
            return 4194304;
        }
        $separuh = intdiv( $b, 2 );
        if ( $separuh < 262144 ) {
            return max( 1, $separuh );
        }
        return min( 4194304, $separuh );
    }

    public static function unggah_kecil( $post_max_size ) {
        return self::batas_unggah( $post_max_size ) < 262144;
    }

    /**
     * Item 3 (review putaran 1): perbandingan berawalan (strpos) terhadap
     * path MENTAH tidak cukup -- wp-content yang di-symlink-kan (mis.
     * ABSPATH/wp-content -> /mnt/data/wp-content) tetap "tampak" berada di
     * dalam akar menurut strpos, padahal manifest yang hanya menelusuri
     * ABSPATH tidak pernah masuk ke direktori symlink dan tidak akan
     * pernah melihat isinya -- staging jadi dibuat tanpa tema, plugin,
     * atau unggahan sama sekali tanpa peringatan. Di sini realpath(konten)
     * dibandingkan PERSIS SAMA dengan realpath(akar) . '/wp-content'; wp-
     * content yang di-symlink-kan, berada di luar akar sama sekali, atau
     * realpath yang gagal (tidak terbaca/tidak ada), semuanya dilaporkan
     * konten_di_luar = true.
     */
    private static function konten_di_luar( $akar_baku, $konten_baku ) {
        if ( @is_link( $akar_baku . '/wp-content' ) ) {
            return true;
        }
        $akar_nyata   = @realpath( $akar_baku );
        $konten_nyata = @realpath( $konten_baku );
        if ( false === $akar_nyata || false === $konten_nyata ) {
            return true;
        }
        $akar_nyata   = rtrim( str_replace( '\\', '/', $akar_nyata ), '/' );
        $konten_nyata = rtrim( str_replace( '\\', '/', $konten_nyata ), '/' );
        return $konten_nyata !== ( $akar_nyata . '/wp-content' );
    }

    /**
     * Koreksi #22: multisite dan WP_CONTENT_DIR di luar ABSPATH dilaporkan
     * di sini supaya dashboard bisa menolak staging pada site seperti itu
     * (spec §3 mengecualikan multisite; manifest hanya menelusuri ABSPATH).
     */
    public static function info( $wpdb, $akar, $konten ) {
        list( $tabel, $dilewati ) = self::tabel( $wpdb );
        $akar_baku   = rtrim( str_replace( '\\', '/', (string) $akar ), '/' );
        $konten_baku = rtrim( str_replace( '\\', '/', (string) $konten ), '/' );
        $post_max    = ini_get( 'post_max_size' );
        return array(
            'php'                  => PHP_VERSION,
            'wp'                   => (string) get_bloginfo( 'version' ),
            'table_prefix'         => (string) $wpdb->prefix,
            // Item 5b (review putaran 1): string dari WordPress/DB dibersihkan
            // ke UTF-8 sah lewat WPMGR_Staging::bersih() sebelum dikirim --
            // sama seperti aturan umum untuk teks dari sistem berkas/DB.
            'charset'              => WPMGR_Staging::bersih( (string) $wpdb->charset, 64 ),
            'home'                 => WPMGR_Staging::bersih( (string) home_url(), 255 ),
            'siteurl'              => WPMGR_Staging::bersih( (string) site_url(), 255 ),
            'multisite'            => (bool) is_multisite(),
            'konten_di_luar'       => self::konten_di_luar( $akar_baku, $konten_baku ),
            'batas_unggah'         => self::batas_unggah( $post_max ),
            'unggah_terlalu_kecil' => self::unggah_kecil( $post_max ),
            'tabel'                => $tabel,
            'tabel_dilewati'       => $dilewati,
        );
    }

    /**
     * Item 2 (review putaran 1): anggaran waktu tersisa setelah info()
     * (query metadata tabel: SHOW TABLE STATUS + satu SHOW KEYS per tabel,
     * bisa ratusan query) memakan sebagian waktu request -- dijepit
     * minimal 1 detik supaya jalan() masih sempat memvisit setidaknya satu
     * entri walau info() kebetulan lambat.
     */
    public static function anggaran_setelah( $anggaran_awal, $mulai ) {
        return max( 1.0, (float) $anggaran_awal - ( microtime( true ) - (float) $mulai ) );
    }

    /**
     * Item 5e (review putaran 1): direktori akar yang tidak terbaca sama
     * sekali (hak akses salah, atau akar salah dikonfigurasi) dibedakan
     * dari "site ini memang tidak punya berkas" -- dipakai
     * WPMGR_Staging::manifest() untuk menolak dengan galat KERAS, bukan
     * mengirim manifest kosong yang tampak seolah-olah sah.
     */
    public static function akar_bisa_dibaca( $akar ) {
        return false !== @scandir( $akar ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
    }
}
