<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Sisi penerima dorong (spec §6.3): area sementara wp-content/wpmgr-dorong/<id>/,
 * kunci satu-dorongan-per-site, dan (Task 8) langkah terapkan.
 *
 *   potongan/000000.php   paket unggahan apa adanya (kepala exit)
 *   keadaan.php           status dan kursor langkah (kepala exit + JSON)
 *   rencana.php, db.php   rencana dan SQL yang dirakit dari potongan
 *   baru/<path>           berkas baru yang menunggu ditukar
 *   lama/<path>           berkas produksi yang tergeser (untuk pemulihan)
 *   jurnal.php            catatan tulis-lebih-dulu setiap operasi tukar
 *
 * $k['tabel_tmp']/$k['tabel_old'] pada keadaan.php adalah JURNAL nama tabel
 * PERSIS yang dibuat push ini (fix C2, review putaran 1) -- diisi lewat
 * catat_tabel() oleh Task 8 setiap kali CREATE/RENAME sungguhan terjadi.
 * bersihkan()/cron() HANYA boleh men-DROP tabel yang tercatat di jurnal
 * push itu SENDIRI, tidak pernah menebak lewat pola nama: nama tabel
 * sementara diturunkan dari nama tabel ASLI (WPMGR_Staging_Sql::ubah()),
 * bukan dari id push, jadi dua push berurutan/tumpang-tindih di site yang
 * sama memakai nama tabel sementara yang SAMA PERSIS -- menghapus lewat
 * pola nama bisa mengenai tabel milik push lain yang sedang aktif.
 *
 * STATUS_BOLEH_BERSIHKAN (fix I6) adalah daftar IZIN (allowlist) status
 * yang aman dibersihkan sepenuhnya (berkas + jurnal tabel tmp DAN old).
 * Status APA PUN yang tidak ada di daftar ini -- termasuk status baru yang
 * kelak dipakai Task 8 selama proses 'menukar'/'memulihkan' yang BELUM
 * SELESAI -- memblokir bersihkan() dengan 409. Task 8 WAJIB memilih nama
 * status baru dari kosakata ini atau menambahkannya ke daftar ini secara
 * sadar, bukan menambah status baru lalu berasumsi bersihkan() aman.
 */
class WPMGR_Staging_Dorong {

    const KEPALA           = "<?php exit; ?>\n";
    const MAKS_UNGGAH      = 8454144;
    const KUNCI            = 'wpmgr_dorong_kunci';
    // Ruling F9b (putusan-preflight.md) + instruksi dispatch task ini: kunci
    // dorong basi setelah 2 JAM tanpa aktivitas, BUKAN 24 jam. Ini timer yang
    // berbeda dari pembersihan area sementara oleh cron() (86400 detik,
    // konteks-global.md: "area sementara connector dibersihkan cron setelah
    // 24 jam") -- dorongan yang macet tidak boleh menahan dorongan BARU
    // selama sehari penuh, walau berkas/keadaannya sendiri tetap disimpan
    // untuk diselidiki/dilanjutkan sampai cron menghapusnya 24 jam kemudian.
    const UMUR_KUNCI       = 7200;
    const JENIS            = array( 'berkas', 'rentang', 'sql', 'rencana' );
    // Fix I3 (review putaran 1): batas keras total byte yang diterima SATU
    // push, di atas dan di luar pemeriksaan ruang disk sungguhan di bawah --
    // jaga-jaga terhadap push yang tidak pernah selesai/dibersihkan dan
    // terus mengirim potongan baru tanpa batas.
    const MAKS_TOTAL_UNGGAH = 53687091200; // 50 GB

    const STATUS_BOLEH_BERSIHKAN = array(
        'baru', 'mengunggah', 'siap', 'selesai', 'gagal', 'direbut', 'ditukar', 'dipulihkan',
    );
    // Subset STATUS_BOLEH_BERSIHKAN yang aman menghapus JURNAL 'tabel_old'
    // (data produksi yang tergeser, disimpan untuk pemulihan): hanya status
    // TERMINAL yang membuktikan tukar sudah selesai (atau sudah dipulihkan)
    // -- status lain di STATUS_BOLEH_BERSIHKAN ('mengunggah', 'gagal',
    // 'direbut', dst.) membiarkan berkas & tabel sementara dibuang, tetapi
    // TIDAK PERNAH ikut membuang 'tabel_old' -- itu satu-satunya salinan
    // data produksi lama bila proses tukar sendiri belum terbukti tuntas.
    const STATUS_AMAN_HAPUS_LAMA = array( 'selesai', 'ditukar', 'dipulihkan' );
    // Fix N3 (review putaran 2, Penting): status PRA-TUKAR -- satu-satunya
    // status push LAMA yang aman direbut lewat kunci basi (kunci()). Belum
    // pernah menyentuh lama/ (produksi yang tergeser) atau tabel_old sama
    // sekali, jadi merebutnya dan menandainya 'direbut' tidak pernah
    // membuang apa pun yang masih dibutuhkan. Task 8 WAJIB memakai salah
    // satu nama di sini untuk setiap status SEBELUM langkah 'menukar'
    // dimulai -- status apa pun sesudahnya (termasuk 'menukar' sendiri,
    // dan status pemulihan) TIDAK PERNAH ditambahkan ke sini.
    const STATUS_PRA_TUKAR = array( 'baru', 'mengunggah', 'siap' );

    protected $akar;
    protected $dasar;
    protected $db;
    protected $tenggat;

    /** Suntikan uji (I3): callable(string $dir): array{bebas:int,total:int}|false. */
    protected static $penyedia_disk = null;

    public function __construct( $akar, $dasar, $db, $detik ) {
        $this->akar    = $akar;
        $this->dasar   = rtrim( str_replace( '\\', '/', $dasar ), '/' ) . '/';
        $this->db      = $db;
        $this->tenggat = microtime( true ) + (int) $detik;
    }

    public static function id_sah( $id ) {
        return is_string( $id ) && 1 === preg_match( '/^[0-9a-f]{32}\z/', $id );
    }

    public static function atur_penyedia_disk_untuk_uji( $fn ) {
        self::$penyedia_disk = $fn;
    }

    protected function galat( $kode, $pesan, $status ) {
        return WPMGR_Staging::galat( $kode, $pesan, $status );
    }

    /** Fix I5: hasil kueri() ('true' sukses, string = pesan galat) -> WP_Error 500 tetap. */
    protected function galat_db( $pesan ) {
        return $this->galat( 'wpmgr_staging_db', $pesan, 500 );
    }

    protected function waktu_habis() {
        return microtime( true ) >= $this->tenggat;
    }

    public function dir( $id ) {
        return $this->dasar . $id . '/';
    }

    protected function pastikan_dir( $dir ) {
        return is_dir( $dir ) || @mkdir( $dir, 0755, true ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
    }

    protected function pastikan_dasar() {
        $this->pastikan_dir( $this->dasar );
        if ( ! file_exists( $this->dasar . 'index.php' ) ) {
            @file_put_contents( $this->dasar . 'index.php', "<?php\n// Silence is golden.\n" ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
        if ( ! file_exists( $this->dasar . '.htaccess' ) ) {
            @file_put_contents( $this->dasar . '.htaccess', // phpcs:ignore WordPress.PHP.NoSilencedErrors
                "<IfModule mod_authz_core.c>\nRequire all denied\n</IfModule>\n<IfModule !mod_authz_core.c>\nDeny from all\n</IfModule>\n" );
        }
    }

    /**
     * MINOR (review putaran 1): nama berkas sementara sekarang
     * `<nama>.<8hex>.tmp.php` (bukan `.tmp` tanpa `.php`) dan tetap membawa
     * kepala exit -- berkas sementara yang belum sempat di-rename (mis.
     * proses mati di tengah) tidak pernah bisa dieksekusi walau tersasar
     * lewat web sebelum cron membersihkannya. Gagal tulis/rename membuang
     * berkas sementara yang sudah terlanjur dibuat, bukan meninggalkannya.
     */
    public function tulis_terlindung( $berkas, $data ) {
        $this->pastikan_dir( dirname( $berkas ) );
        $sementara = $berkas . '.' . bin2hex( random_bytes( 4 ) ) . '.tmp.php';
        if ( false === @file_put_contents( $sementara, self::KEPALA . $data ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            @unlink( $sementara ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            return false;
        }
        if ( ! @rename( $sementara, $berkas ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            @unlink( $sementara ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            return false;
        }
        return true;
    }

    public function baca_terlindung( $berkas ) {
        $data = @file_get_contents( $berkas ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $data || 0 !== strncmp( $data, self::KEPALA, strlen( self::KEPALA ) ) ) {
            return false;
        }
        return (string) substr( $data, strlen( self::KEPALA ) );
    }

    public function keadaan( $id ) {
        $data = $this->baca_terlindung( $this->dir( $id ) . 'keadaan.php' );
        $k    = false === $data ? null : json_decode( $data, true );
        return is_array( $k ) ? $k : null;
    }

    public function simpan_keadaan( $id, array $k ) {
        return $this->tulis_terlindung( $this->dir( $id ) . 'keadaan.php', json_encode( $k ) );
    }

    /** MINOR: kunci 'status' yang hilang tidak boleh memicu notice PHP di pemanggil. */
    protected function status( $k ) {
        return ( is_array( $k ) && isset( $k['status'] ) ) ? $k['status'] : null;
    }

    protected function sentuh( $id, array $k ) {
        $k['diubah'] = time();
        $this->simpan_keadaan( $id, $k );
        return $k;
    }

    /**
     * Fix C1 (review putaran 1, Kritis): draf awal HANYA memasang kunci
     * sekali di potongan pertama dan tidak pernah menyegarkannya lagi --
     * staleness dihitung dari waktu potongan PERTAMA, jadi push yang masih
     * aktif mengirim potongan bisa direbut push lain setelah 2 jam
     * berjalan, lalu push LAMA (yang tidak tahu kuncinya sudah hilang)
     * melanjutkan menulis dengan INSERT IGNORE-nya sendiri seolah masih
     * pemegang sah -- dua push menimpa area/tabel yang sama bersamaan.
     *
     * Sekarang: setiap kali id ini SUDAH memegang kunci, panggilan ini
     * MENYEGARKAN stempel waktunya (fencing lewat UPDATE ... WHERE
     * option_value LIKE '<id>|%' + baca ulang untuk memastikan tulisan itu
     * benar-benar kita yang memenangkannya) -- selama potongan terus
     * datang, push aktif TIDAK PERNAH dianggap basi. Bila penyegaran itu
     * sendiri mendapati nilainya sudah berubah (push lain merebutnya di
     * antara baca dan tulis kita), itu berarti kunci sudah hilang DI
     * TENGAH permintaan ini -- dilaporkan 409 wpmgr_staging_kunci_hilang,
     * bukan diam-diam dianggap sukses.
     *
     * Perebutan kunci basi (bagian bawah, tidak berubah secara semantik)
     * sekarang juga menandai push LAMA sebagai 'direbut' di keadaan-nya
     * (tandai_direbut()) supaya ia tidak bisa melanjutkan lagi walau
     * kuncinya kelak bebas kembali.
     */
    public function kunci( $id ) {
        $o     = $this->db->opsi();
        $nilai = $id . '|' . time();
        $ins   = $this->db->kueri( $this->db->siapkan(
            "INSERT IGNORE INTO {$o} (option_name, option_value, autoload) VALUES (%s, %s, 'no')", self::KUNCI, $nilai ) );
        if ( is_string( $ins ) ) {
            return $this->galat_db( 'Kunci dorong tidak dapat dibuat.' );
        }
        $sekarang = $this->db->nilai( $this->db->siapkan( "SELECT option_value FROM {$o} WHERE option_name = %s", self::KUNCI ) );
        if ( '' !== $this->db->galat_terakhir() ) {
            return $this->galat_db( 'Kunci dorong tidak dapat dibaca.' );
        }
        $sekarang = (string) $sekarang;
        if ( 0 === strpos( $sekarang, $id . '|' ) ) {
            // Kita pemegangnya (baru dibuat atau memang sudah sebelumnya):
            // SEGARKAN stempel waktu (fix C1) supaya push aktif tidak
            // pernah basi selama masih mengirim potongan.
            $r = $this->db->kueri( $this->db->siapkan(
                "UPDATE {$o} SET option_value = %s WHERE option_name = %s AND option_value LIKE %s",
                $nilai, self::KUNCI, $this->db->suka( $id . '|' ) . '%' ) );
            if ( is_string( $r ) ) {
                return $this->galat_db( 'Kunci dorong tidak dapat diperbarui.' );
            }
            $ulang = $this->db->nilai( $this->db->siapkan( "SELECT option_value FROM {$o} WHERE option_name = %s", self::KUNCI ) );
            if ( '' !== $this->db->galat_terakhir() ) {
                return $this->galat_db( 'Kunci dorong tidak dapat dibaca.' );
            }
            // Fix N5 (review putaran 2, Minor): dibandingkan hanya AWALAN
            // '<id>|', bukan nilai PERSIS -- dua permintaan yang tumpang
            // tindih dari PEMEGANG YANG SAMA (mis. dua potongan diunggah
            // hampir bersamaan) bisa saja sama-sama menyegarkan dengan
            // stempel waktu berbeda satu detik; membandingkan nilai persis
            // salah mengira itu sebagai kunci yang "hilang", padahal
            // kuncinya tetap dipegang id yang sama sepanjang waktu.
            if ( 0 !== strpos( (string) $ulang, $id . '|' ) ) {
                // Fencing: antara baca dan tulis kita, push lain sudah
                // merebut kunci ini (mis. kita sendiri baru saja dianggap
                // basi oleh push yang lebih baru). Melanjutkan permintaan
                // ini TIDAK aman lagi.
                return $this->galat( 'wpmgr_staging_kunci_hilang', 'Kunci dorong hilang di tengah permintaan; direbut push lain.', 409 );
            }
            return true;
        }
        $bagian = explode( '|', $sekarang );
        if ( 2 === count( $bagian ) && (int) $bagian[1] < time() - self::UMUR_KUNCI ) {
            // Pemegang lama tidak menyentuh/menyegarkan kuncinya 2 jam:
            // direbut secara atomik, hanya bila nilainya masih sama dengan
            // yang kita baca DAN status push lama masih pra-tukar (fix N3).
            $id_lama = $bagian[0];
            $cek     = $this->cek_takeover_aman( $id_lama );
            if ( is_wp_error( $cek ) ) {
                return $cek;
            }
            $r = $this->db->kueri( $this->db->siapkan(
                "UPDATE {$o} SET option_value = %s WHERE option_name = %s AND option_value = %s", $nilai, self::KUNCI, $sekarang ) );
            if ( is_string( $r ) ) {
                return $this->galat_db( 'Kunci dorong tidak dapat direbut.' );
            }
            $ulang = $this->db->nilai( $this->db->siapkan( "SELECT option_value FROM {$o} WHERE option_name = %s", self::KUNCI ) );
            if ( '' !== $this->db->galat_terakhir() ) {
                return $this->galat_db( 'Kunci dorong tidak dapat dibaca.' );
            }
            if ( 0 === strpos( (string) $ulang, $id . '|' ) ) {
                $this->tandai_direbut( $id_lama );
                return true;
            }
        }
        return $this->galat( 'wpmgr_staging_sibuk', 'Dorongan lain sedang berlangsung di site ini.', 409 );
    }

    /**
     * Fix N3 (review putaran 2, Penting): perebutan kunci basi HANYA
     * diizinkan bila status push LAMA masih PRA-TUKAR (STATUS_PRA_TUKAR).
     * Draf sebelumnya (tandai_direbut()) menimpa status APA PUN dengan
     * 'direbut', termasuk status di TENGAH menukar (mis. 'menukar') --
     * 'direbut' ada di STATUS_BOLEH_BERSIHKAN, jadi bersihkan() lantas
     * menghapus lama/ (data produksi ASLI yang tergeser, satu-satunya
     * salinan untuk pemulihan) bersama jurnalnya begitu saja, padahal
     * proses menukar push lama itu belum tentu selesai atau aman
     * dibatalkan.
     *
     * Bila status push lama BUKAN pra-tukar (sedang menukar/memulihkan),
     * ATAU tidak diketahui/hilang padahal AREANYA masih ada di disk
     * (keadaan.php rusak/hilang tidak bisa membuktikan aman-tidaknya),
     * perebutan DITOLAK 409 wpmgr_staging_perlu_pemulihan. Status push
     * lama TIDAK PERNAH ditandai/ditimpa dalam kasus ini -- lihat juga
     * STATUS_PRA_TUKAR di dekat STATUS_BOLEH_BERSIHKAN untuk kosakata yang
     * harus dipakai Task 8.
     */
    protected function cek_takeover_aman( $id_lama ) {
        if ( ! self::id_sah( $id_lama ) ) {
            return true; // Nilai kunci rusak/tak dikenal -- tidak ada apa pun yang bisa diverifikasi maupun dilindungi.
        }
        $k_lama      = $this->keadaan( $id_lama );
        $status_lama = $this->status( $k_lama );
        if ( null === $k_lama ) {
            if ( is_dir( $this->dir( $id_lama ) ) ) {
                return $this->galat( 'wpmgr_staging_perlu_pemulihan',
                    'Dorongan sebelumnya tidak dapat diverifikasi amannya; pulihkan atau selesaikan dulu.', 409 );
            }
            return true; // Tidak ada keadaan maupun area -- tidak ada apa pun untuk dilindungi.
        }
        if ( ! in_array( $status_lama, self::STATUS_PRA_TUKAR, true ) ) {
            return $this->galat( 'wpmgr_staging_perlu_pemulihan',
                'Dorongan sebelumnya harus dipulihkan atau diselesaikan dulu sebelum direbut.', 409 );
        }
        return true;
    }

    /**
     * Fix C1: push LAMA yang kuncinya direbut ditandai permanen -- ia tidak
     * boleh melanjutkan lagi walau kuncinya kelak bebas kembali (mis.
     * perebutnya sendiri gagal/dibersihkan). Diam-diam tidak melakukan apa
     * pun bila push lama belum pernah mengunggah apa pun (belum ada
     * keadaan.php untuk ditandai) -- tidak ada apa pun untuk dilindungi.
     * Aman dipanggil di sini (fix N3): cek_takeover_aman() sudah memastikan
     * status push lama pra-tukar sebelum baris ini pernah tercapai.
     */
    protected function tandai_direbut( $id_lama ) {
        if ( ! self::id_sah( $id_lama ) ) {
            return;
        }
        $k = $this->keadaan( $id_lama );
        if ( null === $k ) {
            return;
        }
        $k['status'] = 'direbut';
        $this->simpan_keadaan( $id_lama, $k );
    }

    /** Siapa pemegang kunci saat ini ('' bila bebas); WP_Error 500 bila kueri gagal (fix I5). */
    protected function kunci_pemegang() {
        $o        = $this->db->opsi();
        $sekarang = $this->db->nilai( $this->db->siapkan( "SELECT option_value FROM {$o} WHERE option_name = %s", self::KUNCI ) );
        if ( '' !== $this->db->galat_terakhir() ) {
            return $this->galat_db( 'Kunci dorong tidak dapat dibaca.' );
        }
        $bagian = explode( '|', (string) $sekarang );
        return ( isset( $bagian[0] ) && self::id_sah( $bagian[0] ) ) ? $bagian[0] : '';
    }

    /** Fix I5: kueri() yang gagal dilaporkan sebagai galat 500, bukan diam-diam dilewati. */
    public function lepas_kunci( $id ) {
        $o = $this->db->opsi();
        $r = $this->db->kueri( $this->db->siapkan(
            "DELETE FROM {$o} WHERE option_name = %s AND option_value LIKE %s", self::KUNCI, $this->db->suka( $id . '|' ) . '%' ) );
        return is_string( $r ) ? $this->galat_db( 'Kunci dorong tidak dapat dilepas.' ) : true;
    }

    protected function ruang_disk() {
        if ( null !== self::$penyedia_disk ) {
            return call_user_func( self::$penyedia_disk, $this->dasar );
        }
        // Fix I3: fungsi ini bisa dimatikan hosting lewat disable_functions
        // -- dilewati diam-diam (bukan galat) bila tidak tersedia, karena
        // pemeriksaan byte_total per push di atasnya tetap berlaku sebagai
        // jaring pengaman kedua.
        if ( ! function_exists( 'disk_free_space' ) || ! function_exists( 'disk_total_space' ) ) {
            return false;
        }
        $bebas = @disk_free_space( $this->dasar ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        $total = @disk_total_space( $this->dasar ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $bebas || false === $total ) {
            return false;
        }
        return array( 'bebas' => (int) $bebas, 'total' => (int) $total );
    }

    public function unggah( $data ) {
        $data = (string) $data;
        if ( strlen( $data ) > self::MAKS_UNGGAH ) {
            return $this->galat( 'wpmgr_staging_terlalu_besar', 'Potongan unggahan melebihi 8 MB.', 413 );
        }
        $urai = WPMGR_Staging_Paket::urai( $data );
        if ( is_wp_error( $urai ) ) {
            return $urai;
        }
        $meta  = $urai[0];
        $id    = isset( $meta['dorong_id'] ) ? $meta['dorong_id'] : '';
        $nomor = isset( $meta['nomor'] ) ? $meta['nomor'] : null;
        $jenis = isset( $meta['jenis'] ) ? $meta['jenis'] : '';
        if ( ! self::id_sah( $id ) || ! is_int( $nomor ) || $nomor < 0 || $nomor > 99999
            || ! in_array( $jenis, self::JENIS, true ) ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Meta potongan tidak sah.', 400 );
        }
        if ( in_array( $jenis, array( 'berkas', 'rentang' ), true ) ) {
            foreach ( $meta['berkas'] as $b ) {
                if ( ! isset( $b['path'] ) || ! WPMGR_Staging_Path::boleh_ditulis( $b['path'] ) ) {
                    return $this->galat( 'wpmgr_staging_path', 'Path potongan tidak boleh ditulis.', 400 );
                }
            }
        }
        // Fix N2 (review putaran 2, Penting): status TERMINAL (direbut,
        // atau sudah melewati tahap unggah) diperiksa SEBELUM mengunci --
        // draf sebelumnya memanggil kunci() lebih dulu, yang untuk push
        // yang SUDAH direbut tetap berhasil (id ini sendiri yang memegang/
        // menyegarkan kuncinya, sebelum baru kemudian ditolak). Setiap
        // percobaan unggah() yang PASTI akan ditolak seperti itu tetap
        // menahan kunci 2 jam lagi -- memblokir push BARU yang sah tanpa
        // alasan. kunci() sekarang TIDAK PERNAH dipanggil untuk push yang
        // sudah pasti akan ditolak di sini.
        $k      = $this->keadaan( $id );
        $status = $this->status( $k );
        if ( 'direbut' === $status ) {
            return $this->galat( 'wpmgr_staging_direbut', 'Dorongan ini sudah direbut dorongan lain dan tidak dapat dilanjutkan.', 409 );
        }
        if ( null !== $k && 'mengunggah' !== $status ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Dorongan ini sudah melewati tahap unggah.', 409 );
        }

        $kunci = $this->kunci( $id );
        if ( is_wp_error( $kunci ) ) {
            return $kunci;
        }
        $this->pastikan_dasar();
        if ( null === $k ) {
            $k = array( 'status' => 'mengunggah', 'dibuat' => time(), 'byte_total' => 0 );
        }

        $path_potongan = $this->dir( $id ) . 'potongan/' . sprintf( '%06d', $nomor ) . '.php';
        $lama          = $this->baca_terlindung( $path_potongan );
        if ( false !== $lama ) {
            // MINOR: nomor yang sama dengan isi SAMA adalah retry yang aman
            // (idempoten); isi BERBEDA berarti sesuatu yang salah di sisi
            // pengirim -- ditolak keras, bukan diam-diam menimpa.
            if ( $lama === $data ) {
                return array( 'ok' => true, 'nomor' => $nomor, 'sha256' => hash( 'sha256', $data ) );
            }
            return $this->galat( 'wpmgr_staging_nomor_bentrok', 'Nomor potongan ini sudah dipakai dengan isi berbeda.', 409 );
        }

        // Fix I3: batas total per push, lalu batas ruang bebas SUNGGUHAN.
        $byte_total = isset( $k['byte_total'] ) ? (int) $k['byte_total'] : 0;
        if ( $byte_total + strlen( $data ) > self::MAKS_TOTAL_UNGGAH ) {
            return $this->galat( 'wpmgr_staging_disk_penuh', 'Total unggahan dorongan ini melebihi batas yang diizinkan.', 507 );
        }
        $ruang = $this->ruang_disk();
        if ( is_array( $ruang ) ) {
            $ambang = max( 536870912, (int) ( $ruang['total'] * 0.05 ) );
            if ( $ruang['bebas'] - strlen( $data ) < $ambang ) {
                return $this->galat( 'wpmgr_staging_disk_penuh', 'Disk di server hampir penuh; unggahan ditolak.', 507 );
            }
        }

        if ( ! $this->tulis_terlindung( $path_potongan, $data ) ) {
            return $this->galat( 'wpmgr_staging_tulis', 'Potongan tidak dapat disimpan (disk penuh atau izin).', 500 );
        }
        $k['byte_total'] = $byte_total + strlen( $data );
        $this->sentuh( $id, $k );
        return array( 'ok' => true, 'nomor' => $nomor, 'sha256' => hash( 'sha256', $data ) );
    }

    /**
     * Fix MINOR (review putaran 1): path yang ADA diverifikasi lewat
     * WPMGR_Staging_Path::untuk_dibaca() (realpath kanonik, sama seperti
     * /staging/file) -- bukan hanya is_link() pada komponen TERAKHIR.
     * Leluhur direktori yang di-symlink-kan (mis. wp-content/uploads/x ->
     * di luar root) tidak pernah terlihat oleh is_link($abs) sendirian,
     * padahal metadata yang dilaporkan (ukuran/mtime) bisa membocorkan
     * berkas DI LUAR root lewat jalan memutar itu.
     */
    public function snapshot_berkas( $paths ) {
        if ( ! is_array( $paths ) || count( $paths ) > 5000 ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Daftar path snapshot tidak sah.', 400 );
        }
        $hasil = array();
        foreach ( array_values( $paths ) as $rel ) {
            if ( ! is_string( $rel ) || ! WPMGR_Staging_Path::boleh_ditulis( $rel ) ) {
                return $this->galat( 'wpmgr_staging_path', 'Path snapshot tidak sah.', 400 );
            }
            $kanonik = WPMGR_Staging_Path::untuk_dibaca( $this->akar, $rel );
            if ( is_wp_error( $kanonik ) ) {
                if ( 'wpmgr_staging_tidak_ada' === $kanonik->get_error_code() ) {
                    $hasil[] = array( 'path' => $rel, 'ada' => false );
                    continue;
                }
                // Leluhur symlink atau sebab lain yang membuat path ini
                // tidak aman dibaca metadatanya: tolak SELURUH permintaan,
                // sama seperti kegagalan boleh_ditulis() di atas.
                return $this->galat( 'wpmgr_staging_path', 'Path snapshot tidak sah.', 400 );
            }
            $hasil[] = array( 'path' => $rel, 'ada' => true, 'ukuran' => (int) filesize( $kanonik ), 'mtime' => (int) filemtime( $kanonik ) );
        }
        return $hasil;
    }

    /**
     * Fix C2 (review putaran 1): dipanggil Task 8 setiap kali CREATE TABLE
     * (tmp) atau RENAME (old) sungguhan terjadi untuk push $id, supaya
     * bersihkan()/cron() TAHU PERSIS tabel mana yang boleh dihapus untuk
     * push ini -- bukan menebak lewat pola nama (lihat docblock kelas).
     * Diam-diam gagal (false) bila $id tidak sah atau belum pernah
     * mengunggah apa pun (belum ada keadaan.php) -- tidak ada tempat
     * mencatatnya.
     */
    public function catat_tabel( $id, $jenis, $nama ) {
        if ( ! self::id_sah( $id ) || ! in_array( $jenis, array( 'tmp', 'old' ), true )
            || ! is_string( $nama ) || '' === $nama ) {
            return false;
        }
        $k = $this->keadaan( $id );
        if ( null === $k ) {
            return false;
        }
        $kunci_jurnal          = 'tmp' === $jenis ? 'tabel_tmp' : 'tabel_old';
        $daftar                = ( isset( $k[ $kunci_jurnal ] ) && is_array( $k[ $kunci_jurnal ] ) ) ? $k[ $kunci_jurnal ] : array();
        if ( ! in_array( $nama, $daftar, true ) ) {
            $daftar[] = $nama;
        }
        $k[ $kunci_jurnal ] = $daftar;
        return (bool) $this->simpan_keadaan( $id, $k );
    }

    /** Menghapus pohon direktori; false bila waktu habis sebelum selesai. */
    protected function hapus_rekursif( $dir ) {
        if ( ! file_exists( $dir ) && ! is_link( $dir ) ) {
            return true;
        }
        if ( is_link( $dir ) || is_file( $dir ) ) {
            return @unlink( $dir ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
        $it = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator( $dir, FilesystemIterator::SKIP_DOTS ),
            RecursiveIteratorIterator::CHILD_FIRST
        );
        $n = 0;
        foreach ( $it as $f ) {
            $jalur = $f->getPathname();
            if ( $f->isDir() && ! $f->isLink() ) {
                @rmdir( $jalur ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            } else {
                @unlink( $jalur ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
            if ( 0 === ( ++$n % 200 ) && $this->waktu_habis() ) {
                return false;
            }
        }
        return @rmdir( $dir ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
    }

    /**
     * Fix C2 + I2 (review putaran 1): menyaring $daftar (jurnal push ini
     * SENDIRI) supaya hanya nama yang MASIH benar-benar tabel sementara/
     * lama MILIK SITE INI yang tersisa -- pertahanan berlapis di atas
     * jurnal itu sendiri.
     *
     * Nama tabel sementara/lama berbentuk 'wpmgr_tmp_<prefix><asli>' atau
     * 'wpmgr_old_<prefix><asli>' (WPMGR_Staging_Sql::ubah()) -- AWALAN
     * 'wpmgr_tmp_'/'wpmgr_old_' itu sendiri TIDAK berawalan prefix site
     * ini, jadi bagian yang diperiksa lewat WPMGR_Staging_Db::prefix_asing()
     * (I2) adalah bagian SESUDAH awalan itu dilepas, bukan nama utuhnya.
     */
    protected function tabel_journal_aman( array $daftar ) {
        $prefix = $this->db->prefix();
        $semua  = $this->db->kolom( "SHOW TABLES LIKE '" . $this->db->suka( $prefix ) . "%'" );
        if ( '' !== $this->db->galat_terakhir() ) {
            return $this->galat_db( 'Daftar tabel dorong tidak dapat dibaca.' );
        }
        $prefix_asing = WPMGR_Staging_Db::prefix_asing( array_map( 'strval', $semua ), $prefix );
        $aman         = array();
        foreach ( $daftar as $t ) {
            $t = (string) $t;
            if ( 1 !== preg_match( '/^[A-Za-z0-9_$]{1,64}\z/', $t ) ) {
                continue;
            }
            $asli = null;
            foreach ( array( 'wpmgr_tmp_', 'wpmgr_old_' ) as $awalan ) {
                if ( 0 === strpos( $t, $awalan ) ) {
                    $asli = substr( $t, strlen( $awalan ) );
                    break;
                }
            }
            if ( null === $asli || 0 !== strpos( $asli, $prefix ) ) {
                continue; // Bukan tabel sementara/lama sama sekali, atau nama aslinya di luar prefix site ini.
            }
            $di_luar = false;
            foreach ( $prefix_asing as $pa ) {
                if ( 0 === strpos( $asli, $pa ) ) {
                    $di_luar = true;
                    break;
                }
            }
            if ( ! $di_luar ) {
                $aman[] = $t;
            }
        }
        return $aman;
    }

    /**
     * Fix N4 (review putaran 2, Penting): menghapus tabel jurnal SATU per
     * SATU, menyimpan keadaan.php (membuang entri yang baru saja sukses
     * dari jurnal) SEGERA setelah setiap DROP berhasil -- bukan menghapus
     * semuanya lalu baru menyimpan sekali di akhir (fix C2/I5 round 1).
     * Waktu habis atau galat DROP di tengah proses meninggalkan entri yang
     * BELUM diproses tetap tersimpan di jurnal, supaya percobaan berikutnya
     * melanjutkan dari situ, bukan kehilangan jejak tabel yang belum
     * sempat dihapus (pemanggil, bersihkan(), baru menghapus DIREKTORI --
     * satu-satunya salinan keadaan.php/jurnal -- setelah fungsi ini
     * mengembalikan true).
     *
     * true = jurnal (tabel_tmp, dan tabel_old bila $status ada di
     * STATUS_AMAN_HAPUS_LAMA) sudah kosong sepenuhnya. false = waktu habis
     * di tengah (jurnal sisa sudah tersimpan). WP_Error = galat DROP atau
     * galat membaca listing tabel (jurnal sejauh yang sudah sukses juga
     * sudah tersimpan).
     */
    protected function kosongkan_jurnal_tabel( $id, array $k, $status ) {
        $jenis_list = array( 'tabel_tmp' );
        if ( in_array( $status, self::STATUS_AMAN_HAPUS_LAMA, true ) ) {
            $jenis_list[] = 'tabel_old';
        }
        $dijalankan = 0;
        foreach ( $jenis_list as $kunci_jurnal ) {
            $daftar = ( isset( $k[ $kunci_jurnal ] ) && is_array( $k[ $kunci_jurnal ] ) ) ? $k[ $kunci_jurnal ] : array();
            if ( empty( $daftar ) ) {
                continue;
            }
            $aman = $this->tabel_journal_aman( $daftar );
            if ( is_wp_error( $aman ) ) {
                return $aman;
            }
            $sisa = $daftar;
            foreach ( $daftar as $t ) {
                if ( ! in_array( $t, $aman, true ) ) {
                    // Bukan (lagi) tabel milik site ini menurut listing
                    // sungguhan (sudah dihapus di luar, atau entri jurnal
                    // keliru) -- tidak ada apa pun untuk di-DROP, buang
                    // saja dari jurnal.
                    $sisa               = array_values( array_diff( $sisa, array( $t ) ) );
                    $k[ $kunci_jurnal ] = $sisa;
                    $this->simpan_keadaan( $id, $k );
                    continue;
                }
                // Jaminan kemajuan: entri PERTAMA pada panggilan ini selalu
                // diproses, walau tenggat sudah lewat sebelum mulai.
                if ( $dijalankan > 0 && $this->waktu_habis() ) {
                    return false;
                }
                $r = $this->db->kueri( "DROP TABLE IF EXISTS `{$t}`" );
                if ( is_string( $r ) ) {
                    return $this->galat_db( 'Tabel sementara dorong tidak dapat dihapus.' );
                }
                $dijalankan++;
                $sisa               = array_values( array_diff( $sisa, array( $t ) ) );
                $k[ $kunci_jurnal ] = $sisa;
                $this->simpan_keadaan( $id, $k );
            }
        }
        return true;
    }

    /**
     * Fix I6: allowlist status (lihat docblock kelas), bukan denylist yang
     * hanya menyebut 'menukar'. Fix C2: tabel yang dihapus dibatasi jurnal
     * push $id sendiri, dan HANYA bila $id sedang memegang kunci atau kunci
     * sedang bebas -- push LAIN yang sudah merebut kunci ini mungkin sedang
     * memakai nama tabel sementara yang SAMA (nama diturunkan dari nama
     * tabel asli, bukan dari id push), jadi menghapusnya di sini akan
     * merusak push yang sedang berjalan. Pembersihan BERKAS milik push ini
     * SENDIRI (direktorinya sendiri) tetap selalu boleh, lepas dari itu.
     *
     * Fix N4 (review putaran 2, Penting): TABEL dihapus SEBELUM DIREKTORI
     * (dibalik dari round 1) -- direktori menyimpan keadaan.php, satu-
     * satunya salinan jurnal tabel. Draf round 1 menghapus direktori LEBIH
     * DULU, jadi galat DROP/waktu habis/lock-gated-skip SESUDAHNYA membuang
     * jurnal bersama direktorinya dan mengorphankan tabelnya PERMANEN
     * (termasuk wpmgr_old_*, salinan produksi tergeser) -- tidak ada lagi
     * yang mengingat tabel itu perlu dihapus.
     */
    public function bersihkan( $id ) {
        if ( ! self::id_sah( $id ) ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Id dorongan tidak sah.', 400 );
        }
        $k      = $this->keadaan( $id );
        $status = $this->status( $k );
        if ( null !== $k && ! in_array( $status, self::STATUS_BOLEH_BERSIHKAN, true ) ) {
            return $this->galat( 'wpmgr_staging_sibuk', 'Dorongan sedang diterapkan; pulihkan dulu.', 409 );
        }
        if ( null !== $k ) {
            $old_tersisa = ( isset( $k['tabel_old'] ) && is_array( $k['tabel_old'] ) ) ? $k['tabel_old'] : array();
            if ( ! empty( $old_tersisa ) && ! in_array( $status, self::STATUS_AMAN_HAPUS_LAMA, true ) ) {
                // Fix N4: tabel_old (data produksi tergeser) belum aman
                // dihapus untuk status ini -- jangan pernah lanjut ke
                // pembersihan direktori (lihat docblock kosongkan_jurnal_tabel()).
                return $this->galat( 'wpmgr_staging_perlu_pemulihan',
                    'Dorongan ini memiliki tabel produksi lama yang belum aman dihapus; pulihkan atau selesaikan dulu.', 409 );
            }
        }
        $pemegang = $this->kunci_pemegang();
        if ( is_wp_error( $pemegang ) ) {
            return $pemegang;
        }
        if ( null !== $k && ( '' === $pemegang || $pemegang === $id ) ) {
            $r = $this->kosongkan_jurnal_tabel( $id, $k, $status );
            if ( is_wp_error( $r ) ) {
                return $r;
            }
            if ( false === $r ) {
                return array( 'lagi' => true ); // Waktu habis di tengah -- jurnal sisa sudah tersimpan.
            }
        }
        if ( ! $this->hapus_rekursif( rtrim( $this->dir( $id ), '/' ) ) ) {
            return array( 'lagi' => true );
        }
        $r = $this->lepas_kunci( $id );
        if ( is_wp_error( $r ) ) {
            return $r;
        }
        return array( 'lagi' => false );
    }

    /** WP-Cron tiap jam: area yang tidak disentuh 24 jam dibuang. */
    public function cron() {
        if ( ! is_dir( $this->dasar ) ) {
            return;
        }
        foreach ( (array) scandir( $this->dasar ) as $id ) {
            if ( ! self::id_sah( $id ) || $this->waktu_habis() ) {
                continue;
            }
            $k      = $this->keadaan( $id );
            $status = $this->status( $k );
            $diubah = null !== $k && isset( $k['diubah'] ) ? (int) $k['diubah'] : (int) @filemtime( $this->dir( $id ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( $diubah < time() - 86400 && ( null === $k || in_array( $status, self::STATUS_BOLEH_BERSIHKAN, true ) ) ) {
                $this->bersihkan( $id );
            }
        }
    }
}
