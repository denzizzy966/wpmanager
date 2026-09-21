<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

class WPMGR_Updater {

    /**
     * Nama lock WP_Upgrader yang dipegang selama /update memasang paket.
     * create_lock() menyimpannya sebagai option '<nama>.lock' berisi
     * timestamp Unix saat lock diambil.
     */
    const NAMA_KUNCI = 'wpmgr_update';

    /**
     * 15 * MINUTE_IN_SECONDS, ditulis sebagai angka karena konstanta
     * WordPress tidak tersedia di test PHPUnit. Sama dengan lock
     * 'core_updater' milik Core_Upgrader sendiri. Proses PHP yang mati sambil
     * memegang lock (fatal error, OOM -- `finally` tidak berjalan) menahan
     * update dan scan site ini paling lama selama ini; keduanya diulang
     * dashboard dengan backoff.
     */
    const DETIK_KUNCI = 900;

    public static function versi_terpasang( $tipe, $slug ) {
        if ( 'core' === $tipe ) {
            return get_bloginfo( 'version' );
        }
        if ( 'plugin' === $tipe ) {
            require_once ABSPATH . 'wp-admin/includes/plugin.php';
            $semua = get_plugins();
            return isset( $semua[ $slug ]['Version'] ) ? $semua[ $slug ]['Version'] : null;
        }
        if ( 'theme' === $tipe ) {
            $tema = wp_get_theme( $slug );
            return $tema->exists() ? $tema->get( 'Version' ) : null;
        }
        return null;
    }

    /**
     * Versi core menurut wp-includes/version.php di disk.
     *
     * Hanya ini yang benar SETELAH Core_Upgrader bekerja di request yang sama:
     * update_core() mengimpor versi baru sebagai variabel lokal dan sengaja
     * tidak meng-global-kannya ("DO NOT globalize"), sehingga
     * get_bloginfo('version') terus melaporkan versi LAMA sampai request
     * berakhir. update_core() menyalin version.php paling akhir dan
     * membatalkan cache opcache-nya, jadi berkas inilah penanda paling jujur
     * bahwa upgrade benar-benar selesai.
     */
    public static function versi_core_di_disk() {
        $wp_version = null;
        include ABSPATH . WPINC . '/version.php';
        return is_string( $wp_version ) ? $wp_version : null;
    }

    /**
     * Apakah versi terpasang sudah berada di target atau melewatinya.
     *
     * Dipisahkan menjadi metode murni supaya keputusan yang menentukan seluruh
     * sifat idempoten endpoint ini dapat diuji tanpa WordPress. Perbandingan
     * memakai '>=' dan bukan kesetaraan: bila client sempat meng-update manual
     * ke versi lebih baru, menjalankan upgrade ke target yang lebih lama akan
     * menjadi penurunan versi.
     */
    public static function sudah_di_versi( $terpasang, $ke_versi ) {
        return version_compare( $terpasang, $ke_versi, '>=' );
    }

    /**
     * Apakah upgrade yang selesai tanpa error tetap harus dilaporkan sebagai
     * "WordPress tidak menawarkan update ke versi itu".
     *
     * Upgrader WordPress memasang apa pun yang ditawarkan transient update
     * saat itu, bukan versi yang diminta dashboard -- dan bulk_upgrade()
     * mengembalikan `true` untuk plugin yang tidak punya penawaran sama sekali,
     * nilai yang sekilas terlihat seperti sukses. Yang diputuskan di sini
     * adalah versi di disk sesudahnya, bukan kata upgrader.
     *
     * @param string|null $sesudah          Versi terpasang yang dibaca ulang.
     * @param string      $ke_versi         Versi yang diminta dashboard.
     * @param bool        $upgrader_bekerja False bila upgrader melaporkan tidak
     *                                      ada yang dipasang (bulk_upgrade: true).
     */
    public static function tidak_mencapai_target( $sesudah, $ke_versi, $upgrader_bekerja ) {
        if ( null === $sesudah ) {
            // Versi tak terbaca setelah upgrade yang benar-benar bekerja (mis.
            // berkas utama plugin berganti nama) tidak dituduh gagal. Tetapi
            // bila tidak ada yang dipasang, tidak ada dasar untuk mengaku
            // mencapai apa pun.
            return ! $upgrader_bekerja;
        }
        return version_compare( $sesudah, $ke_versi, '<' );
    }

    /**
     * Hasil untuk satu plugin dari keluaran Plugin_Upgrader::bulk_upgrade().
     *
     * bulk_upgrade() mengembalikan false untuk seluruh panggilan bila tidak
     * dapat terhubung ke filesystem; selain itu array per plugin berisi array
     * hasil install (sukses), WP_Error, false (run() gagal terhubung), atau
     * true -- "tidak ada penawaran di transient update_plugins", yang BUKAN
     * sukses dan diputuskan oleh tidak_mencapai_target().
     */
    public static function hasil_bulk( $semua, $slug ) {
        if ( ! is_array( $semua ) || ! array_key_exists( $slug, $semua ) ) {
            return false;
        }
        return null === $semua[ $slug ] ? false : $semua[ $slug ];
    }

    /**
     * Apakah nilai option lock menandakan lock yang masih dipegang, dibaca
     * persis seperti WP_Upgrader::create_lock() membacanya.
     *
     * create_lock() gagal bila baris lock ada dan nilainya falsy, atau bila
     * timestamp-nya lebih baru dari (sekarang - batas). Lock yang lebih tua
     * dari batas dianggap basi dan boleh direbut. /inventory dan /update harus
     * sepakat soal ini, kalau tidak dashboard bisa membaca inventaris setengah
     * jadi dari upgrade yang menurut /update masih berjalan.
     *
     * @param mixed $nilai    Hasil get_option() -- false bila option tidak ada.
     * @param int   $sekarang Timestamp Unix.
     */
    public static function kunci_dipegang( $nilai, $sekarang ) {
        if ( false === $nilai ) {
            return false;
        }
        if ( ! $nilai ) {
            return true;
        }
        return $nilai > ( $sekarang - self::DETIK_KUNCI );
    }

    public static function sedang_sibuk() {
        return self::kunci_dipegang( get_option( self::NAMA_KUNCI . '.lock' ), time() );
    }

    public static function galat_sibuk() {
        return new WP_Error(
            'wpmgr_sibuk',
            'Update lain sedang berjalan di site ini; coba lagi nanti.',
            array( 'status' => 409 )
        );
    }

    /**
     * Penawaran core untuk versi yang diminta dari locale mana pun.
     *
     * find_core_update() hanya mencocokkan locale site. Site berbahasa
     * Indonesia yang paket id_ID-nya belum dirilis untuk versi itu tetap
     * mendapat penawaran en_US untuk versi yang sama -- itulah yang dipasang
     * layar update wp-admin juga. Versinya harus persis dan penawarannya
     * harus 'upgrade'; memasang versi lain sambil melapor sukses adalah
     * persis yang dicegah pemilihan penawaran ini.
     *
     * @param array|false $penawaran Hasil get_core_updates().
     */
    public static function pilih_penawaran_core( $penawaran, $ke_versi ) {
        if ( ! is_array( $penawaran ) ) {
            return false;
        }
        foreach ( $penawaran as $p ) {
            if ( isset( $p->current, $p->response )
                && $p->current === $ke_versi
                && 'upgrade' === $p->response ) {
                return $p;
            }
        }
        return false;
    }

    /**
     * Idempoten secara sengaja: skenario "update berhasil tetapi respons tidak
     * sampai" pasti terjadi cepat atau lambat, dan retry harus aman.
     */
    public static function jalankan( $tipe, $slug, $ke_versi ) {
        if ( ! in_array( $tipe, array( 'core', 'plugin', 'theme' ), true ) ) {
            // Divalidasi lebih dulu, sebelum versi_terpasang() dipanggil --
            // fungsi itu mengembalikan null untuk tipe apa pun di luar
            // core/plugin/theme, sehingga tanpa validasi ini permintaan
            // dengan tipe salah akan salah dilaporkan sebagai 404 "paket
            // tidak ditemukan" alih-alih 400 "tipe tidak dikenal". Keduanya
            // berarti hal berbeda bagi pemanggil: 404 berarti site ini
            // memang tidak punya paket tersebut (kondisi wajar, dashboard
            // menangani dengan scan ulang); 400 berarti pemanggil mengirim
            // sesuatu yang tidak dikenal (bug di pemanggil).
            return new WP_Error(
                'wpmgr_tipe_salah',
                'Tipe paket tidak dikenal.',
                array( 'status' => 400 )
            );
        }

        // Diperiksa sebelum membaca versi terpasang, bukan hanya lewat
        // create_lock() di bawah: selama upgrade berjalan, direktori plugin
        // sempat hilang (dipindah ke cadangan sementara lalu diekstrak ulang),
        // dan membaca versi pada saat itu menghasilkan 404 "paket tidak
        // ditemukan" -- kegagalan final -- untuk paket yang justru sedang
        // di-update oleh permintaan sebelumnya.
        if ( self::sedang_sibuk() ) {
            return self::galat_sibuk();
        }

        $sebelum = self::versi_terpasang( $tipe, $slug );

        if ( null === $sebelum ) {
            return new WP_Error( 'wpmgr_tidak_ditemukan',
                'Paket tidak ditemukan di site ini.', array( 'status' => 404 ) );
        }

        if ( self::sudah_di_versi( $sebelum, $ke_versi ) ) {
            return array(
                'ok'            => true,
                'versi_sebelum' => $sebelum,
                'versi_sesudah' => $sebelum,
                'pesan'         => 'sudah di versi tersebut',
            );
        }

        require_once ABSPATH . 'wp-admin/includes/file.php';
        require_once ABSPATH . 'wp-admin/includes/misc.php';
        require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';
        require_once ABSPATH . 'wp-admin/includes/update.php';

        // Diambil setelah jalan pintas "sudah di versi": permintaan yang tidak
        // mengubah apa pun tidak perlu menahan update lain. create_lock()
        // atomik (INSERT IGNORE), jadi dua permintaan yang lolos pemeriksaan
        // sedang_sibuk() bersamaan tetap hanya satu yang mendapatkannya.
        if ( ! WP_Upgrader::create_lock( self::NAMA_KUNCI, self::DETIK_KUNCI ) ) {
            return self::galat_sibuk();
        }

        try {
            // Dashboard memutus koneksi setelah 180 detik dan menanyakan
            // keadaan sebenarnya lewat /inventory. Tanpa ini PHP boleh
            // menghentikan skrip begitu mendeteksi klien pergi -- di tengah
            // menyalin berkas, meninggalkan plugin rusak separuh.
            ignore_user_abort( true );
            return self::pasang( $tipe, $slug, $ke_versi, $sebelum );
        } finally {
            WP_Upgrader::release_lock( self::NAMA_KUNCI );
        }
    }

    private static function pasang( $tipe, $slug, $ke_versi, $sebelum ) {
        // Hanya refresh yang relevan dengan tipe yang diminta -- endpoint ini
        // hidup di dalam jendela 180 detik dashboard, dan permintaan core
        // tidak butuh me-refresh plugin maupun tema.
        if ( 'plugin' === $tipe ) {
            wp_update_plugins();
        } elseif ( 'theme' === $tipe ) {
            wp_update_themes();
        }

        $skin             = new Automatic_Upgrader_Skin();
        $hasil            = null;
        $upgrader_bekerja = true;

        if ( 'plugin' === $tipe ) {
            // bulk_upgrade(), bukan upgrade(): upgrade() memasang filter
            // deactivate_plugin_before_upgrade yang menonaktifkan plugin untuk
            // setiap request yang bukan WP-cron, dan tidak ada apa pun di jalur
            // REST yang mengaktifkannya kembali (layar update wp-admin
            // melakukannya lewat redirect terpisah). bulk_upgrade() tidak
            // memasang filter itu dan memakai maintenance mode sebagai
            // gantinya -- jalur yang sama yang dipakai updater AJAX core dan
            // WP-CLI.
            $upgrader         = new Plugin_Upgrader( $skin );
            $hasil            = self::hasil_bulk( $upgrader->bulk_upgrade( array( $slug ) ), $slug );
            $upgrader_bekerja = ( true !== $hasil );
        } elseif ( 'theme' === $tipe ) {
            // Theme_Upgrader::upgrade() mengaktifkan ulang tema aktif sendiri
            // (filter current_before/current_after), jadi tidak butuh jalur
            // bulk seperti plugin.
            $upgrader = new Theme_Upgrader( $skin );
            $hasil    = $upgrader->upgrade( $slug );
        } else {
            // $tipe sudah divalidasi di awal jalankan(), jadi cabang ini
            // pasti 'core'.
            wp_version_check( array(), true );

            // find_core_update() memilih penawaran yang cocok dengan versi
            // dan locale yang diminta. updates[0] hanyalah penawaran
            // pertama yang kebetulan didaftarkan WordPress, dan memakainya
            // berarti endpoint ini bisa memasang versi core yang berbeda
            // dari yang diminta dashboard sambil tetap melapor sukses.
            $penawaran = find_core_update( $ke_versi, get_locale() );
            if ( ! $penawaran ) {
                $penawaran = self::pilih_penawaran_core( get_core_updates(), $ke_versi );
            }
            if ( ! $penawaran ) {
                return self::galat_tidak_ada_update( $ke_versi, $sebelum );
            }

            $upgrader = new Core_Upgrader( $skin );
            $hasil    = $upgrader->upgrade( $penawaran );
        }

        if ( is_wp_error( $hasil ) ) {
            return new WP_Error( 'wpmgr_upgrade_gagal',
                $hasil->get_error_message(), array( 'status' => 500 ) );
        }
        if ( empty( $hasil ) ) {
            // Upgrader dapat gagal tanpa WP_Error sama sekali -- ia hanya
            // mengembalikan false (atau, pada Theme_Upgrader yang gagal
            // terhubung ke filesystem, array kosong), dengan alasan kegagalan
            // cuma tersimpan di pesan skin. Bila ini tidak diperiksa,
            // kegagalan seperti itu akan terlihat seperti sukses tanpa
            // perubahan apa pun.
            $pesan = implode( ' | ', (array) $skin->get_upgrade_messages() );
            return new WP_Error( 'wpmgr_upgrade_gagal',
                $pesan ? $pesan : 'Upgrader mengembalikan false tanpa pesan.',
                array( 'status' => 500 ) );
        }

        // Cache plugin/tema dibersihkan sebelum membaca ulang versi terpasang
        // -- tanpa ini, versi yang dibaca bisa berasal dari cache pra-upgrade
        // dan endpoint melaporkan versi tidak berubah padahal upgrade sukses.
        wp_clean_plugins_cache( true );
        wp_clean_themes_cache( true );
        $sesudah = 'core' === $tipe
            ? self::versi_core_di_disk()
            : self::versi_terpasang( $tipe, $slug );

        if ( self::tidak_mencapai_target( $sesudah, $ke_versi, $upgrader_bekerja ) ) {
            return self::galat_tidak_ada_update( $ke_versi, null === $sesudah ? $sebelum : $sesudah );
        }

        return array(
            'ok'            => true,
            'versi_sebelum' => $sebelum,
            'versi_sesudah' => null === $sesudah ? $ke_versi : $sesudah,
            'pesan'         => implode( ' | ', (array) $skin->get_upgrade_messages() ),
        );
    }

    private static function galat_tidak_ada_update( $ke_versi, $terpasang ) {
        return new WP_Error(
            'wpmgr_tidak_ada_update',
            sprintf(
                'WordPress tidak menawarkan update ke versi %s; versi terpasang tetap %s.',
                $ke_versi,
                $terpasang
            ),
            array( 'status' => 409 )
        );
    }
}
