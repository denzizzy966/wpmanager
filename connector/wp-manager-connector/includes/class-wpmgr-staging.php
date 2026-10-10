<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Pintu masuk endpoint /staging/* (spec §6.1). Semua endpoint mati kecuali
 * admin site menyalakan "Izinkan staging", dan connector di site staging
 * sendiri (WPMGR_STAGING) tidak pernah mengumumkan atau melayaninya.
 */
class WPMGR_Staging {

    const KUNCI_BINER    = '__wpmgr_biner';
    const HOOK_BERSIHKAN = 'wpmgr_staging_bersihkan';

    private static $biner = null;
    /** Tingkat buffer keluaran yang dipertahankan sajikan_biner() (0 di produksi; test menaikkannya). */
    public static $ob_dasar = 0;

    public static function mode_staging() {
        return defined( 'WPMGR_STAGING' ) && WPMGR_STAGING;
    }

    public static function fitur_aktif() {
        return WPMGR_Settings::izinkan_staging() && ! self::mode_staging();
    }

    public static function root() {
        return rtrim( str_replace( '\\', '/', ABSPATH ), '/' ) . '/';
    }

    /**
     * Sisa waktu yang aman dipakai satu request. Hosting murah memakai
     * max_execution_time 30 detik; delapan detik disisakan untuk bootstrap
     * WordPress dan pengiriman respons.
     */
    public static function anggaran_detik() {
        $batas = (int) ini_get( 'max_execution_time' );
        if ( $batas <= 0 ) {
            return 20;
        }
        return max( 5, min( 20, $batas - 8 ) );
    }

    public static function bersih( $teks, $panjang ) {
        return WPMGR_Penangkap::potong( $teks, $panjang );
    }

    public static function galat( $kode, $pesan, $status ) {
        return new WP_Error( $kode, $pesan, array( 'status' => $status ) );
    }

    /**
     * Murni: HMAC diperiksa lebih dulu supaya pihak tak dikenal tidak bisa
     * menebak setelan.
     *
     * $mode_staging (fix R1 minor e) opsional supaya tetap murni/mudah diuji
     * tanpa konstanta global: null berarti "tanyakan mode_staging() sendiri"
     * (dipakai guard() sungguhan). Site yang MEMANG salinan staging perlu
     * pesan yang berbeda dari site produksi yang setelannya sekadar belum
     * dinyalakan -- menyuruh admin staging "mengaktifkan Izinkan staging"
     * membingungkan, karena endpoint staging memang selalu mati di sana.
     */
    public static function putuskan( $hasil_hmac, $fitur_aktif, $mode_staging = null ) {
        if ( true !== $hasil_hmac ) {
            return $hasil_hmac;
        }
        if ( $fitur_aktif ) {
            return true;
        }
        if ( null === $mode_staging ) {
            $mode_staging = self::mode_staging();
        }
        if ( $mode_staging ) {
            return self::galat( 'wpmgr_staging_mati',
                'Site ini adalah salinan staging yang dikelola dashboard WP Manager; endpoint staging dimatikan di sini.',
                403 );
        }
        return self::galat( 'wpmgr_staging_mati',
            'Staging tidak diizinkan di site ini. Aktifkan "Izinkan staging" di Pengaturan -> WP Manager.', 403 );
    }

    public static function guard( $request ) {
        return self::putuskan( WPMGR_REST::guard( $request ), self::fitur_aktif(), self::mode_staging() );
    }

    /** Jalur => array( metode, nama callback di kelas ini ). Diisi Task 3–8. */
    public static function rute() {
        return array(
            '/staging/manifest'  => array( 'GET', 'manifest' ),
            '/staging/file'      => array( 'POST', 'file' ),
            '/staging/tabel'     => array( 'POST', 'tabel' ),
            '/staging/tanda-air' => array( 'GET', 'tanda_air' ),
            '/staging/snapshot'  => array( 'POST', 'snapshot' ),
            '/staging/unggah'    => array( 'POST', 'unggah' ),
            '/staging/bersihkan' => array( 'POST', 'bersihkan' ),
            '/staging/terapkan'  => array( 'POST', 'terapkan' ),
        );
    }

    public static function manifest( $request ) {
        $kursor = $request->get_param( 'kursor' );
        // Fix 5d (review putaran 1): pagar tipe SEBELUM cast ke string --
        // get_param() bisa mengembalikan array/objek bila query string
        // dikirim dalam bentuk itu (mis. '?kursor[]=x'); (string) pada
        // array memicu PHP Notice/Warning "Array to string conversion",
        // bukan penolakan bersih.
        $kursor = is_string( $kursor ) ? $kursor : '';
        if ( '' !== $kursor && is_wp_error( WPMGR_Staging_Path::normalisasi( $kursor ) ) ) {
            return self::galat( 'wpmgr_staging_path', 'Kursor manifest tidak sah.', 400 );
        }

        $akar = self::root();
        if ( ! WPMGR_Staging_Manifest::akar_bisa_dibaca( $akar ) ) {
            // Fix 5e (review putaran 1): direktori WordPress yang tidak
            // terbaca sama sekali adalah galat KERAS -- manifest kosong
            // tanpa galat bisa disalahartikan dashboard sebagai "site ini
            // memang tidak punya berkas" dan melanjutkan tarik seolah-olah
            // itu benar (mis. menghapus semua berkas staging yang ada).
            return self::galat( 'wpmgr_manifest_akar', 'Direktori WordPress tidak dapat dibaca.', 500 );
        }

        $anggaran = self::anggaran_detik();
        $info     = null;
        if ( '' === $kursor ) {
            // Fix 2 (review putaran 1): info() menjalankan SHOW TABLE
            // STATUS + satu SHOW KEYS per tabel -- bisa ratusan query pada
            // site dengan banyak plugin. Waktu itu dikurangi dari anggaran
            // penelusuran berkas SEBELUM jalan() dipanggil, bukan
            // dibiarkan memakan anggaran penuh diam-diam.
            $mulai    = microtime( true );
            global $wpdb;
            $info     = WPMGR_Staging_Manifest::info( $wpdb, $akar, WP_CONTENT_DIR );
            $anggaran = WPMGR_Staging_Manifest::anggaran_setelah( $anggaran, $mulai );
        }

        $hasil = WPMGR_Staging_Manifest::jalan( $akar, $kursor, $request->get_param( 'batas' ),
            microtime( true ) + $anggaran );
        if ( null !== $info ) {
            $hasil['info'] = $info;
        }
        return rest_ensure_response( $hasil );
    }

    /**
     * Isi berkas untuk tarik/snapshot (Task 4): daftar berkas kecil atau satu
     * rentang byte berkas besar, dikirim sebagai paket biner. Validasi body
     * dan pembacaan berkasnya ada di WPMGR_Staging_File; di sini hanya
     * penghubung ke respons REST.
     */
    public static function file( $request ) {
        $hasil = WPMGR_Staging_File::ambil( self::root(), json_decode( $request->get_body(), true ) );
        return is_wp_error( $hasil ) ? $hasil : self::respons_biner( $hasil );
    }

    /**
     * SQL ekspor tabel untuk tarik/snapshot (Task 5): potongan berisi DROP+
     * CREATE (kursor kosong) lalu INSERT bertahap, dikirim sebagai paket
     * biner satu bagian. Validasi nama tabel, kursor, dan escaping ada di
     * WPMGR_Staging_Tabel; di sini hanya penghubung ke respons REST.
     */
    public static function tabel( $request ) {
        global $wpdb;
        $p = json_decode( $request->get_body(), true );
        if ( ! is_array( $p ) ) {
            return self::galat( 'wpmgr_staging_permintaan', 'Body permintaan bukan objek.', 400 );
        }
        $nama = isset( $p['tabel'] ) ? $p['tabel'] : null;
        // Item 3 (Minor), fix round 2: kursor yang ADA di body tapi
        // bukan string/null tidak boleh diam-diam diperlakukan sebagai ''
        // (mengulang ekspor dari awal) -- itu bentuk permintaan yang
        // tidak sah, harus 400 keras, bukan restart senyap yang membuang
        // kemajuan tarik yang sedang berjalan.
        if ( array_key_exists( 'kursor', $p ) && null !== $p['kursor'] && ! is_string( $p['kursor'] ) ) {
            return self::galat( 'wpmgr_staging_permintaan', 'Kursor bukan string atau null.', 400 );
        }
        $kursor = ( isset( $p['kursor'] ) && is_string( $p['kursor'] ) ) ? $p['kursor'] : '';
        $hasil  = WPMGR_Staging_Tabel::ekspor( $wpdb, $nama, $kursor );
        if ( is_wp_error( $hasil ) ) {
            return $hasil;
        }
        // susun() bisa melempar InvalidArgumentException (mis. meta melebihi
        // MAKS_META) -- dibungkus supaya itu tidak pernah lolos ke WordPress
        // sebagai fatal error, sama seperti WPMGR_Staging_File::bungkus_atau_500().
        try {
            $paket = WPMGR_Staging_Paket::susun(
                array(
                    'tabel'   => $nama,
                    'kursor'  => $hasil['kursor'],
                    'selesai' => $hasil['selesai'],
                    'baris'   => $hasil['baris'],
                    // Item 6h, fix round 1 (Task 5): dashboard bisa
                    // memperingatkan tabel tanpa PK (mode 'offset').
                    'mode'    => $hasil['mode'],
                    'berkas'  => array( array( 'path' => 'sql' ) ),
                ),
                array( $hasil['sql'] )
            );
        } catch ( InvalidArgumentException $e ) {
            return self::galat( 'wpmgr_staging_susun', 'Paket staging tidak dapat disusun.', 500 );
        }
        return self::respons_biner( $paket );
    }

    /**
     * Tanda air data baru produksi (Task 6): validasi dan pengumpulan ada di
     * WPMGR_Staging_TandaAir; di sini hanya penghubung ke respons REST.
     *
     * posts_sejak lewat query string (TIDAK tercakup HMAC Lapis 1) --
     * pagar tipe SEBELUM dipakai, sama seperti manifest(): get_param() bisa
     * mengembalikan array bila query string dikirim dalam bentuk itu (mis.
     * '?posts_sejak[]=x'), dan (string) pada array memicu PHP Warning
     * "Array to string conversion", bukan penolakan bersih. Nilai yang
     * bukan string diperlakukan sama seperti string kosong: format yang
     * dihasilkan tidak akan cocok regex kumpulkan(), jadi diubah_sejak
     * tetap null (kontrak brief) -- tanpa query tambahan dan tanpa risiko
     * SQL injection, karena kumpulkan() selalu menempelkannya lewat
     * $wpdb->prepare(), bukan mentah.
     */
    public static function tanda_air( $request ) {
        global $wpdb;
        $sejak  = $request->get_param( 'posts_sejak' );
        $sejak  = is_string( $sejak ) ? $sejak : '';
        $hasil  = WPMGR_Staging_TandaAir::kumpulkan( $wpdb, $sejak, (int) $request->get_param( 'posts_maks' ) );
        return is_wp_error( $hasil ) ? $hasil : rest_ensure_response( $hasil );
    }

    public static function dorong() {
        return new WPMGR_Staging_Dorong(
            self::root(),
            rtrim( str_replace( '\\', '/', WP_CONTENT_DIR ), '/' ) . '/wpmgr-dorong/',
            new WPMGR_Staging_Db( $GLOBALS['wpdb'] ),
            self::anggaran_detik(),
            defined( 'WPMU_PLUGIN_DIR' ) ? WPMU_PLUGIN_DIR : WP_CONTENT_DIR . '/mu-plugins'
        );
    }

    private static function body_json( $request ) {
        $p = json_decode( $request->get_body(), true );
        return is_array( $p ) ? $p : null;
    }

    /**
     * Unggah potongan dorong (Task 7): validasi meta, kunci, dan penulisan
     * terlindung ada di WPMGR_Staging_Dorong; di sini hanya penghubung ke
     * respons REST.
     */
    public static function unggah( $request ) {
        return rest_ensure_response( self::dorong()->unggah( $request->get_body() ) );
    }

    /**
     * Metadata snapshot sebelum dorong (Task 7, Koreksi #12): hanya
     * ada/ukuran/mtime untuk path yang akan tertimpa, daftar tabel, dan
     * tanda air -- isi berkas/tabel diambil lewat /staging/file dan
     * /staging/tabel yang sudah ada (Task 4, 5).
     */
    public static function snapshot( $request ) {
        global $wpdb;
        $p = self::body_json( $request );
        if ( null === $p ) {
            return self::galat( 'wpmgr_staging_permintaan', 'Body permintaan bukan objek.', 400 );
        }
        $berkas = self::dorong()->snapshot_berkas( isset( $p['paths'] ) ? $p['paths'] : null );
        if ( is_wp_error( $berkas ) ) {
            return $berkas;
        }
        $hasil = array( 'berkas' => $berkas );
        // MINOR (review putaran 1): 'awal' diurai KETAT sebagai boolean
        // (true/1 saja) -- nilai lain (string "ya", array, dst.) yang lolos
        // ! empty() draf awal tidak boleh diam-diam dianggap "tarik pertama".
        $awal = isset( $p['awal'] ) && ( true === $p['awal'] || 1 === $p['awal'] );
        if ( $awal ) {
            list( $hasil['tabel'] ) = WPMGR_Staging_Manifest::tabel( $wpdb );
            // Fix I4 (review putaran 1, Penting): Manifest::tabel() sendiri
            // tidak memeriksa last_error (SHOW TABLE STATUS yang gagal
            // mengembalikan array kosong, tampak seperti "site ini memang
            // tidak punya tabel"). Diperiksa di sini supaya daftar tabel
            // TIDAK PERNAH kembali kosong secara diam-diam akibat galat --
            // dorong timpa penuh yang dijalankan atas daftar kosong seperti
            // itu bisa menghapus tabel produksi yang seharusnya dipertahankan.
            if ( isset( $wpdb->last_error ) && '' !== (string) $wpdb->last_error ) {
                return self::galat( 'wpmgr_staging_tabel', 'Daftar tabel tidak dapat dibaca dari database.', 500 );
            }
            $tanda_air = WPMGR_Staging_TandaAir::kumpulkan( $wpdb, '', 0 );
            // Fix I4 (review putaran 1, Penting): kumpulkan() sendiri SUDAH
            // mengembalikan WP_Error saat galat query -- draf awal
            // menyimpannya begitu saja ke 'tanda_air' seolah berhasil,
            // membuat snapshot tampak sukses padahal tanda airnya rusak.
            if ( is_wp_error( $tanda_air ) ) {
                return $tanda_air;
            }
            $hasil['tanda_air'] = $tanda_air;
        }
        return rest_ensure_response( $hasil );
    }

    /**
     * Pembersihan area sementara dan tabel dorong (Task 7): logikanya ada di
     * WPMGR_Staging_Dorong::bersihkan(); di sini hanya penghubung ke
     * respons REST.
     */
    public static function bersihkan( $request ) {
        $p = self::body_json( $request );
        return rest_ensure_response( self::dorong()->bersihkan( isset( $p['dorong_id'] ) ? $p['dorong_id'] : '' ) );
    }

    /**
     * Langkah terapkan dorong (Task 8): siapkan/impor/tukar/pulihkan/selesai.
     * Kunci, jurnal, dan pemulihan ada di WPMGR_Staging_Dorong::terapkan();
     * di sini hanya penghubung ke respons REST.
     */
    public static function terapkan( $request ) {
        return rest_ensure_response( self::dorong()->terapkan( self::body_json( $request ) ) );
    }

    /**
     * WP-Cron tiap jam (HOOK_BERSIHKAN): area dorong yang tidak disentuh
     * 24 jam dibuang. Tidak boleh pernah fatal -- itu akan menghentikan
     * semua hook WP-Cron lain yang dijadwalkan di request cron yang sama.
     */
    public static function cron_bersihkan() {
        try {
            if ( isset( $GLOBALS['wpdb'] ) ) {
                self::dorong()->cron();
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function daftarkan_route() {
        foreach ( self::rute() as $jalur => $r ) {
            register_rest_route( WPMGR_REST::NS, $jalur, array(
                'methods'             => $r[0],
                'callback'            => array( __CLASS__, $r[1] ),
                'permission_callback' => array( __CLASS__, 'guard' ),
            ) );
        }
    }

    /**
     * Balasan biner (isi berkas, SQL) tanpa base64: 8 MB isi tetap 8 MB di
     * kabel. Isinya ditahan di sini dan dicetak oleh sajikan_biner() pada
     * rest_pre_serve_request, setelah WordPress mengirim header respons
     * (termasuk header anti-cache dari rest_post_dispatch).
     */
    public static function respons_biner( $isi ) {
        self::$biner = (string) $isi;
        $r = new WP_REST_Response( array( self::KUNCI_BINER => true ) );
        $r->header( 'Content-Type', 'application/octet-stream' );
        $r->header( 'Content-Length', (string) strlen( self::$biner ) );
        $r->header( 'X-Wpmgr-Sha256', hash( 'sha256', self::$biner ) );
        return $r;
    }

    public static function sajikan_biner( $served, $result, $request, $server ) {
        if ( $served || null === self::$biner || ! ( $result instanceof WP_HTTP_Response ) ) {
            return $served;
        }
        $data = $result->get_data();
        if ( ! is_array( $data ) || empty( $data[ self::KUNCI_BINER ] ) ) {
            return $served;
        }
        // Buffer keluaran milik plugin lain (mis. pengubah "mixed content" http->https,
        // minify, optimasi) akan mengubah isi biner sesudah hash-nya dihitung, sehingga
        // dashboard menolak paket ("hash potongan tidak cocok"). Buang semua buffer di
        // atas tingkat dasar sebelum mencetak; buffer yang tidak bisa dilepas dibiarkan.
        while ( ob_get_level() > self::$ob_dasar ) {
            if ( ! @ob_end_clean() ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors -- buffer tak terlepas
                break;
            }
        }
        echo self::$biner; // phpcs:ignore WordPress.Security.EscapeOutput -- isi biner, bukan HTML
        self::$biner = null;
        return true;
    }
}
