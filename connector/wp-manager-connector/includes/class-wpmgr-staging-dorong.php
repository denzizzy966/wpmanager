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

    // Task 8: status langkah terapkan, berurutan:
    //   mengunggah -> menyiapkan -> siap -> (mengimpor -> terimpor)
    //   -> menukar -> ditukar -> selesai, atau -> memulihkan -> dipulihkan.
    // 'menyiapkan'/'mengimpor'/'terimpor' belum menyentuh produksi sama
    // sekali (hanya area ini dan tabel wpmgr_tmp_* yang tercatat di jurnal),
    // jadi masuk STATUS_BOLEH_BERSIHKAN dan STATUS_PRA_TUKAR. 'ditukar'
    // DIKELUARKAN dari daftar ini oleh Task 8: lama/ dan wpmgr_old_* adalah
    // satu-satunya salinan untuk pemulihan eksplisit sesudah tukar; push
    // 'ditukar' harus diselesaikan ('selesai') atau dipulihkan dulu.
    const STATUS_BOLEH_BERSIHKAN = array(
        'baru', 'mengunggah', 'menyiapkan', 'siap', 'mengimpor', 'terimpor',
        'selesai', 'gagal', 'direbut', 'dipulihkan',
    );
    // Fix N4/Minor 5 (review putaran 3): status TERMINAL -- push yang sudah
    // mencapai akhir riwayatnya sendiri, apa pun hasilnya (berhasil, gagal,
    // direbut, atau sudah dipulihkan). Dipakai untuk DUA hal: (a) di
    // bersihkan(), aman menghapus JURNAL 'tabel_old' (data produksi yang
    // tergeser, disimpan untuk pemulihan) -- status lain di
    // STATUS_BOLEH_BERSIHKAN ('mengunggah', 'baru', 'siap', 'ditukar')
    // membiarkan berkas & tabel sementara dibuang, tetapi TIDAK PERNAH ikut
    // membuang 'tabel_old'; (b) di cek_takeover_aman(), kunci basi yang
    // pemiliknya SUDAH terminal boleh direbut juga (lock yang lupa
    // dilepas setelah push itu sendiri sudah selesai bukan alasan menolak
    // push BARU) -- lihat STATUS_PRA_TUKAR untuk status SEBELUM terminal
    // yang juga boleh direbut. 'ditukar' SENGAJA tidak ada di sini: Task 8
    // belum mendefinisikan apakah itu berarti "tukar selesai, aman" atau
    // sekadar penanda antara -- diperlakukan konservatif (tidak aman
    // menghapus tabel_old, tidak aman direbut) sampai jelas.
    const STATUS_AMAN_TERMINAL = array( 'selesai', 'gagal', 'direbut', 'dipulihkan' );
    // Fix N3 (review putaran 2, Penting): status PRA-TUKAR -- status push
    // LAMA yang aman direbut lewat kunci basi (kunci()) KARENA belum
    // pernah menyentuh lama/ (produksi yang tergeser) atau tabel_old sama
    // sekali. Task 8 WAJIB memakai salah satu nama di sini untuk setiap
    // status SEBELUM langkah 'menukar' dimulai -- status apa pun sesudahnya
    // TIDAK PERNAH ditambahkan ke sini. Kontrak untuk Task 8: status
    // 'menukar' HANYA boleh diset setelah kunci() berhasil DISEGARKAN
    // (dipanggil ulang dan mengembalikan true) tepat sebelum memulai
    // langkah tukar -- supaya tenggat basi (UMUR_KUNCI, 2 jam) terhitung
    // dari saat itu, bukan dari potongan terakhir yang diunggah jauh
    // sebelumnya.
    const STATUS_PRA_TUKAR = array( 'baru', 'mengunggah', 'menyiapkan', 'siap', 'mengimpor', 'terimpor' );
    // Task 8: status selama produksi SEDANG atau SUDAH diubah dan salinan
    // pemulihannya (lama/, wpmgr_old_*) masih dibutuhkan. TIDAK PERNAH boleh
    // masuk STATUS_BOLEH_BERSIHKAN, STATUS_PRA_TUKAR, maupun
    // STATUS_AMAN_TERMINAL: bersihkan() menolaknya (409), perebutan kunci
    // basi menolaknya (wpmgr_staging_perlu_pemulihan), dan cron() hanya
    // memulihkan 'menukar'/'memulihkan' yang macet, tidak menghapusnya.
    const STATUS_MENYENTUH_PRODUKSI = array( 'menukar', 'ditukar', 'memulihkan' );

    const CHARSET           = array( 'utf8mb4', 'utf8', 'utf8mb3', 'latin1' );
    const LANGKAH           = array( 'siapkan', 'impor', 'tukar', 'pulihkan', 'selesai' );
    const TANDA_MAINTENANCE = 'Dipasang WP Manager selama dorongan staging diterapkan';
    const MU_AMAN           = 'wpmgr-dorong-aman.php';
    // Tukar/pulihkan yang tidak disentuh selama ini dianggap ditinggal
    // dashboard (worker mati, jaringan putus) dan dipulihkan cron().
    const DIAM_MACET        = 900;
    // Fix round 1b: tabel batal (wpmgr_b<n>_*, tulisan produksi pasca-tukar
    // yang dibatalkan) ditahan sekurangnya selama ini sejak dibuat, bahkan
    // terhadap bersihkan() eksplisit, supaya masih bisa diselamatkan manual.
    const UMUR_BATAL        = 86400;
    // Status di mana hasil sebuah langkah yang SUDAH selesai masih berlaku
    // (Ruling F4): ulangan langkah itu dibalas hasil tersimpan yang sama.
    // 'dipulihkan' sengaja tidak ada di daftar tukar/impor/siapkan: tukar
    // yang sudah dibalik tidak boleh dilaporkan sukses lagi.
    const STATUS_LANGKAH_SELESAI = array(
        'siapkan'  => array( 'siap', 'mengimpor', 'terimpor', 'menukar', 'ditukar', 'selesai' ),
        'impor'    => array( 'terimpor', 'menukar', 'ditukar', 'selesai' ),
        'tukar'    => array( 'ditukar', 'selesai' ),
        'selesai'  => array( 'selesai' ),
        'pulihkan' => array( 'dipulihkan' ),
    );

    protected $akar;
    protected $dasar;
    protected $db;
    protected $tenggat;
    protected $dir_mu;
    protected $rencana_cache = null;

    /** Suntikan uji (I3): callable(string $dir): array{bebas:int,total:int}|false. */
    protected static $penyedia_disk = null;

    public function __construct( $akar, $dasar, $db, $detik, $dir_mu = null ) {
        $this->akar    = $akar;
        $this->dasar   = rtrim( str_replace( '\\', '/', $dasar ), '/' ) . '/';
        $this->db      = $db;
        $this->tenggat = microtime( true ) + (int) $detik;
        $this->dir_mu  = null === $dir_mu ? $akar . 'wp-content/mu-plugins/' : rtrim( str_replace( '\\', '/', $dir_mu ), '/' ) . '/';
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
        // Fix round 3: json_encode() yang gagal (mis. UTF-8 tidak sah) dulu
        // menulis badan kosong dan tetap melapor sukses -- jurnal hilang.
        // Sekarang gagal, dan berkas lama tidak disentuh.
        $json = json_encode( $k );
        if ( ! is_string( $json ) ) {
            return false;
        }
        return $this->tulis_terlindung( $this->dir( $id ) . 'keadaan.php', $json );
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
        // Fix Minor 5 (review putaran 3): kunci basi yang pemiliknya SUDAH
        // TERMINAL (STATUS_AMAN_TERMINAL -- push itu sudah selesai sendiri,
        // apa pun hasilnya) juga boleh direbut, bukan hanya pra-tukar --
        // lock yang lupa dilepas SETELAH push itu sendiri selesai bukan
        // alasan menahan push BARU. Hanya status di TENGAH menukar (mis.
        // 'menukar') atau yang tidak dikenal/tidak bisa dibuktikan yang
        // ditolak.
        if ( ! in_array( $status_lama, self::STATUS_PRA_TUKAR, true )
            && ! in_array( $status_lama, self::STATUS_AMAN_TERMINAL, true ) ) {
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
        // Fix Minor 4 (review putaran 3): status dibaca ULANG tepat sebelum
        // menulis, dan hanya ditulis bila MASIH pra-tukar pada saat ini --
        // status push lama BISA SAJA sudah berubah (mis. mulai 'menukar',
        // atau sudah mencapai status terminal sendiri) di antara pembacaan
        // di cek_takeover_aman() dan baris ini, karena push lama itu
        // sendiri mungkin masih berjalan di request lain. Push yang SUDAH
        // terminal (STATUS_AMAN_TERMINAL) tidak perlu ditandai apa pun --
        // riwayatnya sendiri sudah berakhir, menimpanya dengan 'direbut'
        // hanya mengaburkan hasil aslinya (berhasil/gagal) tanpa manfaat.
        $k = $this->keadaan( $id_lama );
        if ( null === $k || ! in_array( $this->status( $k ), self::STATUS_PRA_TUKAR, true ) ) {
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
            // Fix Minor 6 (review putaran 3): dijaga isset()/is_array()
            // secara eksplisit sebelum di-foreach -- WPMGR_Staging_Paket::urai()
            // saat ini SELALU menjamin 'berkas' berupa array sebelum
            // sampai di sini, tapi baris ini tidak boleh bergantung diam-diam
            // pada kontrak kelas lain yang bisa saja berubah.
            if ( ! isset( $meta['berkas'] ) || ! is_array( $meta['berkas'] ) ) {
                return $this->galat( 'wpmgr_staging_permintaan', 'Meta potongan tidak sah.', 400 );
            }
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
            // Task 8 fix round 1/2: wpmgr_b<n>_ (n = 1..99) adalah tabel
            // "batal" (hasil pembalikan yang memuat tulisan produksi pasca-tukar).
            $asli = 1 === preg_match( '/^(?:wpmgr_tmp_|wpmgr_old_|wpmgr_b[1-9][0-9]?_)(.*)\z/s', $t, $m ) ? $m[1] : null;
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
     * STATUS_AMAN_TERMINAL) sudah kosong sepenuhnya. false = waktu habis
     * di tengah (jurnal sisa sudah tersimpan). WP_Error = galat DROP atau
     * galat membaca listing tabel (jurnal sejauh yang sudah sukses juga
     * sudah tersimpan).
     */
    protected function kosongkan_jurnal_tabel( $id, array &$k, $status ) {
        $jenis_list = array( 'tabel_tmp' );
        if ( in_array( $status, self::STATUS_AMAN_TERMINAL, true ) ) {
            $jenis_list[] = 'tabel_old';
            // Task 8 fix round 1 (R11): tabel batal hanya ada setelah
            // pemulihan selesai ('dipulihkan', terminal); dihapus di sini --
            // lewat bersihkan() eksplisit atau cron setelah 24 jam.
            $jenis_list[] = 'tabel_batal';
        }
        $sesi = $this->buka_sesi( null );
        if ( is_wp_error( $sesi ) ) {
            return $sesi;
        }
        try {
            return $this->kosongkan_jurnal_tabel_inti( $id, $k, $jenis_list );
        } finally {
            $this->tutup_sesi( $sesi );
        }
    }

    protected function kosongkan_jurnal_tabel_inti( $id, array &$k, array $jenis_list ) {
        $dijalankan = 0;
        $tertahan   = false;
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
                if ( 'tabel_batal' === $kunci_jurnal && $this->batal_ditahan_sampai( $k, $t ) > 0 ) {
                    continue; // Fix round 1b: belum 24 jam -- tabel dan entrinya dipertahankan.
                }
                // Fix round 3: nama yang SAMA bisa sedang ditahan area lain
                // (tabel batal di nama wpmgr_tmp_*, atau entri lebih baru
                // bernama sama). Entri tetap di jurnal ini, area tidak dihapus.
                if ( $this->ditahan_area_lain( $id, $t ) ) {
                    $tertahan = true;
                    continue;
                }
                if ( 'tabel_batal' === $kunci_jurnal && ! isset( $k['batal_dibuat'][ $t ] ) ) {
                    // Tanpa cap waktu: tidak terbukti dibuat area ini --
                    // entri dilupakan, tabelnya tidak pernah di-DROP.
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
                unset( $k['batal_dibuat'][ $t ] );
                $this->simpan_keadaan( $id, $k );
            }
        }
        return $tertahan ? 'tertahan' : true;
    }

    /**
     * Fix round 1b: batas waktu (unix) penahanan tabel batal $t, atau --
     * bila $t null -- yang paling akhir di antara semua tabel_batal yang
     * masih tercatat; 0 bila tidak ada yang masih ditahan. Entri tanpa cap
     * waktu (tidak pernah ditulis simpan_tabel_batal()) tidak ditahan.
     */
    protected function batal_ditahan_sampai( $k, $t = null ) {
        if ( ! is_array( $k ) ) {
            return 0;
        }
        $daftar = null === $t ? ( ( isset( $k['tabel_batal'] ) && is_array( $k['tabel_batal'] ) ) ? $k['tabel_batal'] : array() ) : array( $t );
        $sampai = 0;
        foreach ( $daftar as $nama ) {
            $dibuat = isset( $k['batal_dibuat'][ (string) $nama ] ) ? (int) $k['batal_dibuat'][ (string) $nama ] : 0;
            if ( $dibuat > 0 && $dibuat + self::UMUR_BATAL > time() ) {
                $sampai = max( $sampai, $dibuat + self::UMUR_BATAL );
            }
        }
        return $sampai;
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
        // Task 8 fix round 1: flock per push yang SAMA dengan terapkan() --
        // bersihkan tidak pernah berjalan bersamaan dengan satu langkah
        // (mis. impor yang sedang membuat tabel sementara yang akan di-DROP).
        $flock = null;
        if ( is_dir( $this->dir( $id ) ) ) {
            $flock = $this->kunci_langkah( $id );
            if ( is_wp_error( $flock ) ) {
                return $flock;
            }
        }
        try {
            $hasil = $this->bersihkan_inti( $id );
        } finally {
            $this->lepas_kunci_langkah( $flock );
        }
        if ( is_array( $hasil ) && false === $hasil['lagi'] && empty( $hasil['tahan_batal'] ) ) {
            @unlink( $this->berkas_kunci_langkah( $id ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
        return $hasil;
    }

    protected function bersihkan_inti( $id ) {
        $k      = $this->keadaan( $id );
        $status = $this->status( $k );
        if ( null === $k && $this->keadaan_rusak_masih_ditahan( $id ) ) {
            // Fix round 2 (N2): keadaan.php ada tetapi (sesaat) tidak
            // terbaca -- jurnal di dalamnya mungkin masih menahan tabel.
            // Area tidak pernah dihapus atas dasar keadaan yang tidak diketahui.
            return array( 'lagi' => true );
        }
        if ( null !== $k && ! in_array( $status, self::STATUS_BOLEH_BERSIHKAN, true ) ) {
            return $this->galat( 'wpmgr_staging_sibuk', 'Dorongan sedang atau sudah diterapkan; selesaikan atau pulihkan dulu.', 409 );
        }
        if ( null !== $k ) {
            $old_tersisa = ( isset( $k['tabel_old'] ) && is_array( $k['tabel_old'] ) ) ? $k['tabel_old'] : array();
            if ( ! empty( $old_tersisa ) && ! in_array( $status, self::STATUS_AMAN_TERMINAL, true ) ) {
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
        $boleh_hapus_tabel = ( '' === $pemegang || $pemegang === $id );
        if ( null !== $k && $boleh_hapus_tabel ) {
            $r = $this->kosongkan_jurnal_tabel( $id, $k, $status );
            if ( is_wp_error( $r ) ) {
                return $r;
            }
            if ( false === $r ) {
                return array( 'lagi' => true ); // Waktu habis di tengah -- jurnal sisa sudah tersimpan.
            }
            if ( 'tertahan' === $r ) {
                return array( 'lagi' => true ); // Fix round 3: sebagian nama ditahan area lain; dicoba lagi nanti.
            }
            // Fix round 1b: tabel batal yang belum 24 jam tetap ada. Area
            // (satu-satunya salinan jurnalnya) dipertahankan, tetapi kunci
            // dorong DILEPAS -- dorongan baru tidak boleh tertahan olehnya.
            // Bukan "macet": dashboard membaca tahan_batal/sampai.
            // Fix round 2 (N2): dari jurnal di memori request ini (yang baru
            // saja diperbarui kosongkan_jurnal_tabel()), bukan baca ulang.
            $sampai = $this->batal_ditahan_sampai( $k );
            if ( $sampai > 0 ) {
                $lepas = $this->lepas_kunci( $id );
                if ( is_wp_error( $lepas ) ) {
                    return $lepas;
                }
                return array( 'lagi' => false, 'tahan_batal' => true, 'sampai' => $sampai );
            }
        } elseif ( null !== $k && ! $boleh_hapus_tabel ) {
            // Fix N4 (review putaran 3, Penting): push LAIN sedang memegang
            // kunci -- tabel jurnal TIDAK disentuh (lihat docblock kelas).
            // Bila jurnalnya TIDAK KOSONG, direktori (satu-satunya salinan
            // jurnal itu, lewat keadaan.php) TIDAK BOLEH ikut dihapus juga
            // -- draf sebelumnya jatuh lewat ke hapus_rekursif() di bawah
            // begitu saja, mengorphankan tabel yang masih tercatat di sana
            // secara permanen. cron() bisa mencapai jalur ini (push lain
            // baru merebut kunci di antara pemeriksaan status dan di sini).
            $tmp_tersisa   = ( isset( $k['tabel_tmp'] ) && is_array( $k['tabel_tmp'] ) ) ? $k['tabel_tmp'] : array();
            $old_tersisa   = ( isset( $k['tabel_old'] ) && is_array( $k['tabel_old'] ) ) ? $k['tabel_old'] : array();
            $batal_tersisa = ( isset( $k['tabel_batal'] ) && is_array( $k['tabel_batal'] ) ) ? $k['tabel_batal'] : array();
            if ( ! empty( $tmp_tersisa ) || ! empty( $old_tersisa ) || ! empty( $batal_tersisa ) ) {
                return array( 'lagi' => true );
            }
        }
        if ( null !== $k && $this->batal_ditahan_sampai( $k ) > 0 ) {
            return array( 'lagi' => true ); // Pengaman terakhir: jurnal tabel yang ditahan tidak pernah dibuang.
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

    // ==================================================================
    // Task 8: langkah terapkan (siapkan, impor, tukar, pulihkan, selesai).
    // ==================================================================

    /**
     * Pintu masuk /staging/terapkan. Urutan penjagaannya disengaja:
     *   1. push 'direbut' ditolak SEBELUM kunci() (sama seperti unggah(),
     *      fix N2) supaya push yang pasti ditolak tidak menahan kunci;
     *   2. ulangan langkah yang SUDAH selesai dibalas hasil tersimpan
     *      (Ruling F4) tanpa menyentuh apa pun -- termasuk tanpa kunci,
     *      supaya retry dashboard atas respons yang hilang tidak pernah
     *      berubah menjadi 409 dan memicu pemulihan atas tukar yang sukses;
     *   3. flock per push: dua request langkah untuk push yang sama (retry
     *      yang tumpang tindih, atau cron yang memulihkan) tidak pernah
     *      berjalan bersamaan atas berkas produksi yang sama;
     *   4. kunci() menyegarkan kunci dorong dan memastikan push ini masih
     *      pemiliknya -- baru sesudahnya status boleh diubah, termasuk
     *      'menukar' (CAS perebutan dari request lain lalu gagal karena
     *      nilai kunci sudah berubah);
     *   5. keadaan dibaca ULANG sesudah kunci, lalu dicek 'direbut' lagi.
     * Pengecualian apa pun menjadi WP_Error 500 berpesan tetap.
     */
    public function terapkan( $p ) {
        $flock = null;
        $aktif = null; // [id, langkah] setelah flock + kunci dipegang
        try {
            if ( ! is_array( $p ) || ! isset( $p['dorong_id'], $p['langkah'] ) || ! self::id_sah( $p['dorong_id'] ) ) {
                return $this->galat( 'wpmgr_staging_permintaan', 'Permintaan terapkan tidak sah.', 400 );
            }
            $id = $p['dorong_id'];
            $k  = $this->keadaan( $id );
            if ( null === $k ) {
                return $this->galat( 'wpmgr_staging_tidak_ada', 'Dorongan tidak ditemukan.', 404 );
            }
            if ( 'direbut' === $this->status( $k ) ) {
                return $this->galat_direbut();
            }
            $langkah = $p['langkah'];
            if ( ! is_string( $langkah ) || ! in_array( $langkah, self::LANGKAH, true ) ) {
                return $this->galat( 'wpmgr_staging_permintaan', 'Langkah tidak dikenal.', 400 );
            }
            $token = isset( $p['token'] ) ? $p['token'] : null;
            $ulang = $this->ulangan( $k, $langkah, $token );
            if ( null !== $ulang ) {
                return $ulang;
            }
            $flock = $this->kunci_langkah( $id );
            if ( is_wp_error( $flock ) ) {
                $galat = $flock;
                $flock = null;
                return $galat;
            }
            $kunci = $this->kunci( $id );
            if ( is_wp_error( $kunci ) ) {
                return $kunci;
            }
            $k = $this->keadaan( $id );
            if ( null === $k ) {
                return $this->galat( 'wpmgr_staging_tidak_ada', 'Dorongan tidak ditemukan.', 404 );
            }
            if ( 'direbut' === $this->status( $k ) ) {
                return $this->galat_direbut();
            }
            $aktif = array( $id, $langkah );
            switch ( $langkah ) {
                case 'siapkan':
                    return $this->siapkan( $id, $k, $p );
                case 'impor':
                    return $this->impor( $id, $k );
                case 'tukar':
                    return $this->tukar( $id, $k, $token );
                case 'pulihkan':
                    return $this->pulihkan_inti( $id, $token, false );
                default:
                    return $this->selesai( $id, $k );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
            // Fix round 1: pengecualian di tengah tukar tidak boleh
            // meninggalkan produksi setengah tertukar sampai cron datang --
            // pemulihan otomatis dijalankan sekarang (flock masih dipegang).
            if ( null !== $aktif && 'tukar' === $aktif[1] ) {
                try {
                    if ( 'menukar' === $this->status( $this->keadaan( $aktif[0] ) ) ) {
                        return $this->gagal_tukar( $aktif[0],
                            $this->galat( 'wpmgr_staging_tukar', 'Galat tak terduga di tengah penukaran.', 500 ) );
                    }
                } catch ( \Throwable $e2 ) {
                    unset( $e2 );
                }
            }
            return $this->galat( 'wpmgr_staging_galat', 'Galat tak terduga saat menerapkan dorongan; lihat log server.', 500 );
        } finally {
            $this->lepas_kunci_langkah( $flock );
        }
    }

    protected function galat_direbut() {
        return $this->galat( 'wpmgr_staging_direbut', 'Dorongan ini sudah direbut dorongan lain dan tidak dapat dilanjutkan.', 409 );
    }

    protected static function token_sah( $token ) {
        return is_string( $token ) && 1 === preg_match( '/^[0-9a-f]{32,64}\z/', $token );
    }

    protected function token_cocok( array $k, $token ) {
        return ! empty( $k['token_hash'] ) && self::token_sah( $token )
            && hash_equals( (string) $k['token_hash'], hash( 'sha256', $token ) );
    }

    /** Ruling F4: hasil tersimpan untuk langkah yang sudah selesai dan masih berlaku, atau null. */
    protected function ulangan( array $k, $langkah, $token ) {
        if ( ! isset( $k['hasil'][ $langkah ] ) || ! is_array( $k['hasil'][ $langkah ] )
            || ! in_array( $this->status( $k ), self::STATUS_LANGKAH_SELESAI[ $langkah ], true ) ) {
            return null;
        }
        // Tukar/pulihkan tetap terikat token: pemanggil lain (job lain)
        // tidak boleh diberi tahu "sukses" atas penukaran yang bukan miliknya.
        if ( in_array( $langkah, array( 'tukar', 'pulihkan' ), true ) && ! empty( $k['token_hash'] ) && ! $this->token_cocok( $k, $token ) ) {
            return $this->galat( 'wpmgr_staging_token', 'Token tidak cocok.', 403 );
        }
        return $k['hasil'][ $langkah ];
    }

    /**
     * Berkas flock per push, DI LUAR area push itu sendiri: bersihkan()
     * memegangnya selagi menghapus area (Windows tidak bisa menghapus
     * berkas yang sedang terbuka), lalu menghapusnya setelah dilepas.
     */
    protected function berkas_kunci_langkah( $id ) {
        return $this->dasar . $id . '.lock';
    }

    /** flock non-blok atas <dasar>/<id>.lock; WP_Error 409 bila langkah lain sedang berjalan. */
    protected function kunci_langkah( $id ) {
        $h = @fopen( $this->berkas_kunci_langkah( $id ), 'c' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $h ) {
            return $this->galat( 'wpmgr_staging_tulis', 'Kunci langkah dorongan tidak dapat dibuka.', 500 );
        }
        if ( ! flock( $h, LOCK_EX | LOCK_NB ) ) {
            fclose( $h );
            return $this->galat( 'wpmgr_staging_sibuk', 'Langkah lain untuk dorongan ini sedang berjalan; coba lagi sebentar.', 409 );
        }
        return $h;
    }

    protected function lepas_kunci_langkah( $h ) {
        if ( is_resource( $h ) ) {
            flock( $h, LOCK_UN );
            fclose( $h );
        }
    }

    /** Simpan keadaan dan LAPORKAN kegagalannya: dipakai sebelum setiap perubahan yang harus bisa dilacak. */
    protected function sentuh_wajib( $id, array $k ) {
        $k['diubah'] = time();
        if ( ! $this->simpan_keadaan( $id, $k ) ) {
            return $this->galat( 'wpmgr_staging_tulis', 'Keadaan dorongan tidak dapat disimpan.', 500 );
        }
        return $k;
    }

    /** Menyimpan hasil langkah yang selesai (diputar ulang oleh ulangan()). */
    protected function selesaikan_langkah( $id, array $k, $langkah, array $hasil ) {
        $k['hasil']             = ( isset( $k['hasil'] ) && is_array( $k['hasil'] ) ) ? $k['hasil'] : array();
        $k['hasil'][ $langkah ] = $hasil;
        $k                      = $this->sentuh_wajib( $id, $k );
        return is_wp_error( $k ) ? $k : $hasil;
    }

    protected function lagi( $id, array $k ) {
        $k = $this->sentuh( $id, $k );
        return array(
            'selesai'  => false,
            'status'   => $k['status'],
            'kemajuan' => array(
                'ekstrak'      => isset( $k['ekstrak'] ) ? (int) $k['ekstrak'] : 0,
                'verifikasi'   => isset( $k['verifikasi'] ) ? (int) $k['verifikasi'] : 0,
                'impor_posisi' => isset( $k['impor_posisi'] ) ? (int) $k['impor_posisi'] : 0,
                'tukar'        => isset( $k['tukar'] ) ? (int) $k['tukar'] : 0,
                'pulih'        => isset( $k['pulih'] ) ? (int) $k['pulih'] : 0,
            ),
        );
    }

    // ---- siapkan ------------------------------------------------------

    protected function potongan( $id, $i ) {
        return $this->dir( $id ) . 'potongan/' . sprintf( '%06d', $i ) . '.php';
    }

    /** Tulis bagian ke posisi tertentu; memotong sisa percobaan sebelumnya supaya idempoten. */
    protected function tambahkan( $berkas, $data, $posisi ) {
        $kepala = strlen( self::KEPALA );
        $h      = @fopen( $berkas, 'c+b' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $h ) {
            return false;
        }
        $ukuran = (int) fstat( $h )['size'];
        if ( 0 === $ukuran ) {
            fwrite( $h, self::KEPALA );
            $ukuran = $kepala;
        }
        if ( $ukuran - $kepala < $posisi ) {
            fclose( $h );
            return false;
        }
        ftruncate( $h, $kepala + $posisi );
        fseek( $h, $kepala + $posisi );
        $ok = strlen( $data ) === fwrite( $h, $data );
        fclose( $h );
        return $ok;
    }

    protected function ekstrak( $id, array $k, array $meta, array $bagian ) {
        $dir = $this->dir( $id );
        if ( 'rencana' === $meta['jenis'] || 'sql' === $meta['jenis'] ) {
            if ( 1 !== count( $bagian ) ) {
                return $this->galat( 'wpmgr_staging_paket', 'Potongan rencana/SQL harus berisi tepat satu bagian.', 400 );
            }
            $kunci  = 'rencana' === $meta['jenis'] ? 'rencana_byte' : 'sql_byte';
            $berkas = $dir . ( 'rencana' === $meta['jenis'] ? 'rencana.php' : 'db.php' );
            if ( ! $this->tambahkan( $berkas, $bagian[0], $k[ $kunci ] ) ) {
                return $this->galat( 'wpmgr_staging_tulis', 'Potongan tidak dapat dirakit.', 500 );
            }
            $k[ $kunci ] += strlen( $bagian[0] );
            return $k;
        }
        foreach ( $meta['berkas'] as $i => $b ) {
            if ( ! isset( $b['path'], $bagian[ $i ] ) || ! WPMGR_Staging_Path::boleh_ditulis( $b['path'] ) ) {
                return $this->galat( 'wpmgr_staging_path', 'Path potongan tidak boleh ditulis.', 400 );
            }
            $tujuan = $dir . 'baru/' . $b['path'];
            $this->pastikan_dir( dirname( $tujuan ) );
            if ( 'rentang' === $meta['jenis'] ) {
                $dari = isset( $b['dari'] ) && is_int( $b['dari'] ) ? $b['dari'] : -1;
                $sb   = $tujuan . '.wpmgr-bagian';
                clearstatcache( true, $sb );
                $ada  = is_file( $sb ) ? (int) filesize( $sb ) : 0;
                if ( $dari < 0 || $dari > $ada ) {
                    return $this->galat( 'wpmgr_staging_urutan', 'Rentang berkas tidak berurutan.', 409 );
                }
                $h = @fopen( $sb, 'c+b' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
                if ( false === $h ) {
                    return $this->galat( 'wpmgr_staging_tulis', 'Berkas sementara tidak dapat ditulis.', 500 );
                }
                ftruncate( $h, $dari );
                fseek( $h, $dari );
                $ok = strlen( $bagian[ $i ] ) === fwrite( $h, $bagian[ $i ] );
                fclose( $h );
            } else {
                $ok = false !== @file_put_contents( $tujuan, $bagian[ $i ] ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
            if ( ! $ok ) {
                return $this->galat( 'wpmgr_staging_tulis', 'Berkas sementara tidak dapat ditulis (disk penuh?).', 500 );
            }
        }
        return $k;
    }

    /**
     * Rencana dorong yang sudah dirakit, divalidasi utuh: hash cocok dengan
     * yang dikirim siapkan, setiap path lolos boleh_ditulis() (Koreksi #14:
     * connector, mu-plugin staging, wp-config.php, .maintenance tidak pernah
     * ada di sini), dan tidak ada path yang muncul dua kali (tanpa beda
     * huruf besar/kecil) -- dua operasi atas path yang sama berbagi satu
     * lama/<path>, sehingga pemulihan salah satunya bisa menimpa salinan
     * asli yang disimpan operasi lain.
     */
    protected function rencana( $id, array $k ) {
        if ( null !== $this->rencana_cache ) {
            return $this->rencana_cache;
        }
        $salah = $this->galat( 'wpmgr_staging_rencana', 'Rencana dorong rusak atau tidak cocok.', 422 );
        $data  = $this->baca_terlindung( $this->dir( $id ) . 'rencana.php' );
        if ( false === $data || empty( $k['sha256_rencana'] ) || ! is_string( $k['sha256_rencana'] )
            || ! hash_equals( $k['sha256_rencana'], hash( 'sha256', $data ) ) ) {
            return $salah;
        }
        $r = json_decode( $data, true );
        if ( ! is_array( $r ) || 1 !== ( isset( $r['versi'] ) ? $r['versi'] : 0 ) || ! isset( $r['berkas'], $r['hapus'] )
            || ! is_array( $r['berkas'] ) || ! is_array( $r['hapus'] ) || ! isset( $r['sql'] ) || ! is_bool( $r['sql'] )
            || ! isset( $r['charset'] ) || ! in_array( $r['charset'], self::CHARSET, true ) ) {
            return $salah;
        }
        $r['berkas'] = array_values( $r['berkas'] );
        $r['hapus']  = array_values( $r['hapus'] );
        $dilihat     = array();
        foreach ( $r['berkas'] as $b ) {
            if ( ! is_array( $b ) || ! isset( $b['path'], $b['ukuran'], $b['sha256'] ) || ! WPMGR_Staging_Path::boleh_ditulis( $b['path'] )
                || ! is_int( $b['ukuran'] ) || $b['ukuran'] < 0 || 1 !== preg_match( '/^[0-9a-f]{64}\z/', (string) $b['sha256'] ) ) {
                return $salah;
            }
            $kunci = strtolower( $b['path'] );
            if ( isset( $dilihat[ $kunci ] ) ) {
                return $salah;
            }
            $dilihat[ $kunci ] = true;
        }
        foreach ( $r['hapus'] as $h ) {
            if ( ! WPMGR_Staging_Path::boleh_ditulis( $h ) ) {
                return $salah;
            }
            $kunci = strtolower( $h );
            if ( isset( $dilihat[ $kunci ] ) ) {
                return $salah;
            }
            $dilihat[ $kunci ] = true;
        }
        $this->rencana_cache = $r;
        return $r;
    }

    protected function siapkan( $id, array $k, array $p ) {
        if ( ! in_array( $k['status'], array( 'mengunggah', 'menyiapkan' ), true ) ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Dorongan ini tidak sedang menunggu disiapkan.', 409 );
        }
        if ( 'mengunggah' === $k['status'] ) {
            $n   = isset( $p['jumlah_potongan'] ) ? $p['jumlah_potongan'] : 0;
            $sha = isset( $p['sha256_rencana'] ) ? $p['sha256_rencana'] : '';
            if ( ! is_int( $n ) || $n < 1 || $n > 100000 || ! is_string( $sha ) || 1 !== preg_match( '/^[0-9a-f]{64}\z/', $sha ) ) {
                return $this->galat( 'wpmgr_staging_permintaan', 'Jumlah potongan atau hash rencana tidak sah.', 400 );
            }
            for ( $i = 0; $i < $n; $i++ ) {
                if ( ! is_file( $this->potongan( $id, $i ) ) ) {
                    return $this->galat( 'wpmgr_staging_kurang', 'Potongan ' . $i . ' belum diunggah.', 409 );
                }
            }
            $k = $this->sentuh_wajib( $id, array_merge( $k, array(
                'status' => 'menyiapkan', 'jumlah_potongan' => $n, 'sha256_rencana' => $sha,
                'ekstrak' => 0, 'rencana_byte' => 0, 'sql_byte' => 0, 'verifikasi' => 0,
            ) ) );
            if ( is_wp_error( $k ) ) {
                return $k;
            }
        }
        $maju = false;
        while ( $k['ekstrak'] < $k['jumlah_potongan'] ) {
            if ( $maju && $this->waktu_habis() ) {
                return $this->lagi( $id, $k );
            }
            $data = $this->baca_terlindung( $this->potongan( $id, $k['ekstrak'] ) );
            $urai = false === $data ? $this->galat( 'wpmgr_staging_paket', 'Potongan tersimpan rusak.', 500 ) : WPMGR_Staging_Paket::urai( $data );
            if ( is_wp_error( $urai ) ) {
                return $urai;
            }
            $hasil = $this->ekstrak( $id, $k, $urai[0], $urai[1] );
            if ( is_wp_error( $hasil ) ) {
                return $hasil;
            }
            $k = $hasil;
            $k['ekstrak']++;
            $k    = $this->sentuh( $id, $k );
            $maju = true;
        }
        $r = $this->rencana( $id, $k );
        if ( is_wp_error( $r ) ) {
            return $r;
        }
        $dir = $this->dir( $id );
        foreach ( $r['berkas'] as $b ) {
            $sb = $dir . 'baru/' . $b['path'] . '.wpmgr-bagian';
            if ( is_file( $sb ) ) {
                @rename( $sb, $dir . 'baru/' . $b['path'] ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
        }
        $jumlah = count( $r['berkas'] );
        while ( $k['verifikasi'] < $jumlah ) {
            if ( $maju && $this->waktu_habis() ) {
                return $this->lagi( $id, $k );
            }
            $b = $r['berkas'][ $k['verifikasi'] ];
            $f = $dir . 'baru/' . $b['path'];
            clearstatcache( true, $f );
            if ( ! is_file( $f ) || is_link( $f ) || (int) filesize( $f ) !== $b['ukuran'] || ! hash_equals( $b['sha256'], (string) hash_file( 'sha256', $f ) ) ) {
                return $this->galat( 'wpmgr_staging_verifikasi',
                    'Berkas tidak lengkap atau berbeda dari rencana: ' . WPMGR_Staging::bersih( $b['path'], 200 ), 422 );
            }
            if ( isset( $b['mtime'] ) && is_int( $b['mtime'] ) ) {
                @touch( $f, $b['mtime'] ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
            $k['verifikasi']++;
            $maju = true;
        }
        $k['status']  = 'siap';
        $k['sql']     = $r['sql'];
        $k['charset'] = $r['charset'];
        return $this->selesaikan_langkah( $id, $k, 'siapkan', array( 'selesai' => true, 'status' => 'siap' ) );
    }

    // ---- impor --------------------------------------------------------

    /**
     * Membuka sesi database untuk satu blok kerja di koneksi $wpdb BERSAMA
     * (sisa request WordPress memakai koneksi yang sama), mengembalikan
     * nilai variabel sesi sebelumnya untuk tutup_sesi().
     *
     * $charset string (impor, tukar_db): SET NAMES, FOREIGN_KEY_CHECKS=0,
     * UNIQUE_CHECKS=0, dan sql_mode yang SENGAJA tanpa NO_BACKSLASH_ESCAPES
     * -- pemecah dan validator Task 7 mengasumsikan escape backslash.
     * $charset null: hanya lock_wait_timeout.
     *
     * Fix round 1 (I1): lock_wait_timeout = 5 SELALU diset. Bawaan server
     * 1 tahun (MySQL 8) / 1 hari (MariaDB): RENAME/DROP yang menunggu
     * metadata lock transaksi lain membuat semua pembacaan front-end antre
     * di belakangnya, dan bila klien diputus pernyataan itu tetap antre di
     * server lalu berjalan belakangan -- membalik jawaban "apakah tukar
     * terjadi" setelah dorongan dinilai. Dengan batas ini galat 1205
     * masuk jalur gagal/pemulihan yang biasa.
     */
    protected function buka_sesi( $charset ) {
        if ( null !== $charset && ! in_array( $charset, self::CHARSET, true ) ) {
            return $this->galat_db( 'Charset sesi database tidak sah.' );
        }
        $lama = $this->db->baris( 'SELECT @@SESSION.sql_mode AS sql_mode, @@SESSION.foreign_key_checks AS fk,'
            . ' @@SESSION.unique_checks AS uc, @@SESSION.character_set_client AS cs_client,'
            . ' @@SESSION.character_set_connection AS cs_conn, @@SESSION.character_set_results AS cs_results,'
            . ' @@SESSION.collation_connection AS coll, @@SESSION.lock_wait_timeout AS lwt' );
        if ( '' !== $this->db->galat_terakhir() || ! is_array( $lama ) ) {
            return $this->galat_db( 'Variabel sesi database tidak dapat dibaca.' );
        }
        $sesi = array( 'lama' => array_change_key_case( $lama, CASE_LOWER ), 'penuh' => null !== $charset );
        $set  = array();
        if ( null !== $charset ) {
            $set = array( "SET NAMES {$charset}", 'SET FOREIGN_KEY_CHECKS=0', 'SET UNIQUE_CHECKS=0',
                          "SET SESSION sql_mode = 'NO_AUTO_VALUE_ON_ZERO'" );
        }
        $set[] = 'SET SESSION lock_wait_timeout = 5';
        foreach ( $set as $q ) {
            if ( true !== $this->db->kueri( $q ) ) {
                $this->tutup_sesi( $sesi );
                return $this->galat_db( 'Sesi database tidak dapat disiapkan.' );
            }
        }
        return $sesi;
    }

    /** Mengembalikan variabel sesi yang diubah buka_sesi(); nilai yang tidak lolos validasi tidak ditanam ke SQL. */
    protected function tutup_sesi( $sesi ) {
        if ( ! is_array( $sesi ) || ! isset( $sesi['lama'] ) ) {
            return;
        }
        $l   = $sesi['lama'];
        $ada = function ( $kunci, $pola ) use ( $l ) {
            return isset( $l[ $kunci ] ) && 1 === preg_match( $pola, (string) $l[ $kunci ] );
        };
        $q = array();
        if ( $sesi['penuh'] ) {
            if ( $ada( 'sql_mode', '/^[A-Za-z0-9_,]*\z/' ) ) {
                $q[] = "SET SESSION sql_mode = '" . $l['sql_mode'] . "'";
            }
            foreach ( array( 'fk' => 'FOREIGN_KEY_CHECKS', 'uc' => 'UNIQUE_CHECKS' ) as $kunci => $nama ) {
                if ( $ada( $kunci, '/^[01]\z/' ) ) {
                    $q[] = "SET SESSION {$nama} = " . (int) $l[ $kunci ];
                }
            }
            foreach ( array( 'cs_client' => 'character_set_client', 'cs_conn' => 'character_set_connection',
                             'cs_results' => 'character_set_results', 'coll' => 'collation_connection' ) as $kunci => $nama ) {
                if ( $ada( $kunci, '/^[A-Za-z0-9_]{1,64}\z/' ) ) {
                    $q[] = "SET SESSION {$nama} = " . $l[ $kunci ];
                } elseif ( 'cs_results' === $kunci && array_key_exists( $kunci, $l ) && null === $l[ $kunci ] ) {
                    $q[] = 'SET SESSION character_set_results = NULL';
                }
            }
        }
        if ( $ada( 'lwt', '/^[0-9]{1,9}\z/' ) && (int) $l['lwt'] >= 1 ) {
            $q[] = 'SET SESSION lock_wait_timeout = ' . (int) $l['lwt'];
        }
        foreach ( $q as $satu ) {
            $this->db->kueri( $satu );
        }
    }

    /** Fix round 1 (I3): cache objek persisten (Redis/Memcached) jangan menyajikan opsi/post dari sebelum tabel ditukar. */
    protected function kosongkan_cache() {
        if ( function_exists( 'wp_cache_flush' ) ) {
            wp_cache_flush();
        }
    }

    /** Set nama tabel yang ada di bawah prefix site, wpmgr_tmp_<prefix>, dan wpmgr_old_<prefix>. */
    protected function tabel_ada() {
        $prefix = $this->db->prefix();
        $hasil  = array();
        foreach ( array( $prefix, 'wpmgr_tmp_' . $prefix, 'wpmgr_old_' . $prefix, 'wpmgr_b' ) as $awal ) {
            $daftar = $this->db->kolom( "SHOW TABLES LIKE '" . $this->db->suka( $awal ) . "%'" );
            if ( '' !== $this->db->galat_terakhir() ) {
                return $this->galat_db( 'Daftar tabel tidak dapat dibaca.' );
            }
            foreach ( $daftar as $t ) {
                $hasil[ (string) $t ] = true;
            }
        }
        return $hasil;
    }

    /** Prefix asing (I2) yang WAJIB diteruskan ke ubah(); tanpa ini tumpang tindih prefix lolos. */
    protected function prefix_asing_site() {
        $prefix = $this->db->prefix();
        $semua  = $this->db->kolom( "SHOW TABLES LIKE '" . $this->db->suka( $prefix ) . "%'" );
        if ( '' !== $this->db->galat_terakhir() ) {
            return $this->galat_db( 'Daftar tabel tidak dapat dibaca.' );
        }
        return WPMGR_Staging_Db::prefix_asing( array_map( 'strval', $semua ), $prefix );
    }

    protected function jurnal_tmp( array $k ) {
        return ( isset( $k['tabel_tmp'] ) && is_array( $k['tabel_tmp'] ) ) ? $k['tabel_tmp'] : array();
    }

    /**
     * DROP setiap tabel di jurnal $kunci_jurnal push ini (hanya yang lolos
     * tabel_journal_aman()), mencoretnya dari jurnal satu per satu SETELAH
     * DROP berhasil. Hanya dipanggil selagi push ini memegang kunci dorong.
     */
    protected function hapus_tabel_jurnal( $id, array &$k, $kunci_jurnal ) {
        $daftar = ( isset( $k[ $kunci_jurnal ] ) && is_array( $k[ $kunci_jurnal ] ) ) ? $k[ $kunci_jurnal ] : array();
        if ( empty( $daftar ) ) {
            return true;
        }
        $sesi = $this->buka_sesi( null );
        if ( is_wp_error( $sesi ) ) {
            return $sesi;
        }
        try {
            return $this->hapus_tabel_jurnal_inti( $id, $k, $kunci_jurnal, $daftar );
        } finally {
            $this->tutup_sesi( $sesi );
        }
    }

    protected function hapus_tabel_jurnal_inti( $id, array &$k, $kunci_jurnal, array $daftar ) {
        $aman = $this->tabel_journal_aman( $daftar );
        if ( is_wp_error( $aman ) ) {
            return $aman;
        }
        foreach ( $daftar as $t ) {
            if ( $this->ditahan_area_lain( $id, $t ) ) {
                continue; // Fix round 3: ditahan area lain -- tidak di-DROP, entri tetap di jurnal.
            }
            if ( in_array( $t, $aman, true ) && true !== $this->db->kueri( "DROP TABLE IF EXISTS `{$t}`" ) ) {
                return $this->galat_db( 'Tabel sementara dorong tidak dapat dihapus.' );
            }
            $k[ $kunci_jurnal ] = array_values( array_diff( $k[ $kunci_jurnal ], array( $t ) ) );
            $simpan             = $this->sentuh_wajib( $id, $k );
            if ( is_wp_error( $simpan ) ) {
                return $simpan;
            }
            $k = $simpan;
        }
        return true;
    }

    /**
     * Fix round 3: keadaan.php yang ada tetapi tidak terbaca diperlakukan
     * menahan (gagal tertutup) -- paling lama UMUR_BATAL sejak simpan
     * terakhir (mtime berkas). Tidak ada penahanan yang melampaui 24 jam
     * sejak keadaan terakhir berhasil ditulis; sesudahnya area itu tidak
     * menahan apa pun dan cron boleh menghapusnya.
     */
    protected function keadaan_rusak_masih_ditahan( $id ) {
        $f = $this->dir( $id ) . 'keadaan.php';
        clearstatcache( true, $f );
        if ( ! is_file( $f ) ) {
            return false;
        }
        $mtime = (int) @filemtime( $f ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        return $mtime + self::UMUR_BATAL > time();
    }

    /**
     * Fix round 2 (N1): nama $tabel masih ditahan sebagai tabel batal oleh
     * area dorongan LAIN (terjadi bila 99 slot wpmgr_b<n>_ habis dan tabel
     * dibiarkan di nama wpmgr_tmp_*). Area yang keadaannya tidak terbaca
     * dianggap menahan (gagal tertutup).
     */
    protected function ditahan_area_lain( $id, $tabel ) {
        foreach ( (array) @scandir( $this->dasar ) as $lain ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( ! self::id_sah( $lain ) || $lain === $id || ! is_dir( $this->dir( $lain ) ) ) {
                continue;
            }
            $k = $this->keadaan( $lain );
            if ( null === $k ) {
                if ( $this->keadaan_rusak_masih_ditahan( $lain ) ) {
                    return true;
                }
                continue;
            }
            $batal = ( isset( $k['tabel_batal'] ) && is_array( $k['tabel_batal'] ) ) ? $k['tabel_batal'] : array();
            if ( in_array( $tabel, $batal, true ) && $this->batal_ditahan_sampai( $k, $tabel ) > 0 ) {
                return true;
            }
        }
        return false;
    }

    /**
     * Kontrol kompensasi R10: struktur tabel yang BENAR-BENAR dibuat server
     * dibaca ulang dari information_schema, tidak bergantung pada pengurai
     * CREATE TABLE. Mesin di luar InnoDB/MyISAM/Aria, atau opsi UNION/
     * CONNECTION/partisi, berarti tabel itu bisa membaca tabel lain atau
     * server lain -- tabel di-DROP, dicoret dari jurnal, dan impor gagal
     * sebelum tukar mana pun.
     */
    protected function periksa_mesin( $id, array &$k, $tabel ) {
        $baris = $this->db->baris( $this->db->siapkan(
            'SELECT ENGINE, CREATE_OPTIONS FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', $tabel ) );
        $aman  = false;
        if ( '' === $this->db->galat_terakhir() && is_array( $baris ) ) {
            $baris = array_change_key_case( $baris, CASE_UPPER );
            $mesin = isset( $baris['ENGINE'] ) ? $baris['ENGINE'] : null;
            $opsi  = isset( $baris['CREATE_OPTIONS'] ) ? (string) $baris['CREATE_OPTIONS'] : '';
            $aman  = is_string( $mesin ) && 1 === preg_match( '/^(innodb|myisam|aria)\z/i', $mesin )
                && 1 !== preg_match( '/union|connection|partitioned/i', $opsi );
        }
        if ( $aman ) {
            return true;
        }
        if ( true === $this->db->kueri( "DROP TABLE IF EXISTS `{$tabel}`" ) ) {
            $k['tabel_tmp'] = array_values( array_diff( $this->jurnal_tmp( $k ), array( $tabel ) ) );
            $simpan         = $this->sentuh_wajib( $id, $k );
            if ( ! is_wp_error( $simpan ) ) {
                $k = $simpan;
            }
        }
        return $this->galat( 'wpmgr_staging_impor', 'Tabel hasil impor memakai mesin atau opsi yang tidak diizinkan.', 422 );
    }

    /**
     * Satu pernyataan unggahan: WAJIB lewat ubah() (dengan prefix asing),
     * nama tabel tujuannya dicatat di jurnal tabel SEBELUM DROP/CREATE
     * dieksekusi (tulis-lebih-dulu), dan INSERT/ALTER hanya boleh mengenai
     * tabel yang sudah tercatat -- tabel wpmgr_tmp_* sisa pihak lain tidak
     * pernah ikut terisi lalu tertukar ke produksi.
     */
    protected function jalankan_pernyataan( $id, array &$k, $sql, $prefix, array $prefix_asing ) {
        $ubah = WPMGR_Staging_Sql::ubah( $sql, $prefix, 'wpmgr_tmp_', $prefix_asing );
        if ( is_wp_error( $ubah ) || null === $ubah ) {
            return null === $ubah ? true : $ubah;
        }
        if ( 1 !== preg_match( '/^(DROP|CREATE|INSERT|ALTER)\s+(?:TABLE\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?|INTO\s+)`([A-Za-z0-9_$]{1,64})`/i', $ubah, $m )
            || 0 !== strpos( $m[2], 'wpmgr_tmp_' ) ) {
            return $this->galat( 'wpmgr_staging_impor', 'Pernyataan SQL tidak dikenali setelah divalidasi.', 422 );
        }
        $verba = strtoupper( $m[1] );
        $tabel = $m[2];
        if ( 'DROP' === $verba || 'CREATE' === $verba ) {
            if ( ! in_array( $tabel, $this->jurnal_tmp( $k ), true ) ) {
                if ( $this->ditahan_area_lain( $id, $tabel ) ) {
                    return $this->galat( 'wpmgr_staging_ditahan',
                        'Tabel sementara ini masih ditahan pemulihan dorongan lain (24 jam); coba lagi nanti.', 409 );
                }
                $k['tabel_tmp']   = $this->jurnal_tmp( $k );
                $k['tabel_tmp'][] = $tabel;
                $simpan           = $this->sentuh_wajib( $id, $k );
                if ( is_wp_error( $simpan ) ) {
                    return $simpan;
                }
                $k = $simpan;
            }
        } elseif ( ! in_array( $tabel, $this->jurnal_tmp( $k ), true ) ) {
            return $this->galat( 'wpmgr_staging_impor', 'Pernyataan SQL menulis ke tabel yang tidak dibuat dorongan ini.', 422 );
        }
        if ( true !== $this->db->kueri( $ubah ) ) {
            return $this->galat( 'wpmgr_staging_impor', 'Impor database gagal pada salah satu pernyataan SQL.', 500 );
        }
        return 'CREATE' === $verba ? $this->periksa_mesin( $id, $k, $tabel ) : true;
    }

    /**
     * Koreksi #14: alamat, visibilitas mesin pencari, dan opsi connector
     * produksi tidak ikut ditimpa. Opsi wpmgr_* milik salinan staging yang
     * TIDAK ada di produksi juga dibuang, supaya himpunan opsi connector
     * sesudah tukar persis milik produksi (termasuk baris kunci dorong).
     * Ruling F5: setiap kolom di UPDATE/DELETE ... JOIN berkualifikasi alias.
     */
    protected function pertahankan_opsi( array $k ) {
        $p    = $this->db->prefix();
        $tmp  = 'wpmgr_tmp_' . $p . 'options';
        $asli = $p . 'options';
        if ( ! in_array( $tmp, $this->jurnal_tmp( $k ), true ) ) {
            return true;
        }
        $ada = $this->tabel_ada();
        if ( is_wp_error( $ada ) ) {
            return $ada->get_error_message();
        }
        if ( ! isset( $ada[ $tmp ] ) || ! isset( $ada[ $asli ] ) ) {
            return true;
        }
        $syarat = function ( $alias ) {
            return "({$alias}.option_name IN ('siteurl', 'home', 'blog_public') OR {$alias}.option_name LIKE 'wpmgr\\_%')";
        };
        foreach ( array(
            "INSERT IGNORE INTO `{$tmp}` (`option_name`, `option_value`, `autoload`)"
                . " SELECT o.option_name, o.option_value, o.autoload FROM `{$asli}` o WHERE " . $syarat( 'o' ),
            "UPDATE `{$tmp}` t JOIN `{$asli}` o ON o.option_name = t.option_name"
                . ' SET t.option_value = o.option_value, t.autoload = o.autoload WHERE ' . $syarat( 't' ),
            "DELETE t FROM `{$tmp}` t LEFT JOIN `{$asli}` o ON o.option_name = t.option_name"
                . " WHERE t.option_name LIKE 'wpmgr\\_%' AND o.option_name IS NULL",
        ) as $q ) {
            $r = $this->db->kueri( $q );
            if ( true !== $r ) {
                return $r;
            }
        }
        return $this->pastikan_connector_aktif( $tmp );
    }

    /**
     * Fix round 1 (I4): active_plugins ikut dari staging. Bila connector
     * nonaktif di sana, sesudah tukar dashboard tidak bisa lagi mencapai
     * selesai/pulihkan. Nilainya array terserialisasi, jadi dibaca, diubah
     * di PHP (tanpa objek: allowed_classes=false), lalu ditulis kembali.
     */
    protected function pastikan_connector_aktif( $tmp ) {
        $basename = self::basename_connector();
        $nilai    = $this->db->nilai( "SELECT option_value FROM `{$tmp}` WHERE option_name = 'active_plugins'" );
        if ( '' !== $this->db->galat_terakhir() ) {
            return 'active_plugins tidak dapat dibaca.';
        }
        if ( null === $nilai ) {
            $q = $this->db->siapkan( "INSERT INTO `{$tmp}` (`option_name`, `option_value`, `autoload`) VALUES ('active_plugins', %s, 'yes')",
                serialize( array( $basename ) ) );
        } else {
            $daftar = @unserialize( (string) $nilai, array( 'allowed_classes' => false ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            $daftar = is_array( $daftar ) ? array_values( array_filter( $daftar, 'is_string' ) ) : array();
            if ( in_array( $basename, $daftar, true ) ) {
                return true;
            }
            $daftar[] = $basename;
            $daftar   = array_values( array_unique( $daftar ) );
            sort( $daftar ); // WordPress sendiri menyimpan daftar ini terurut (activate_plugin()).
            $q = $this->db->siapkan( "UPDATE `{$tmp}` SET option_value = %s WHERE option_name = 'active_plugins'", serialize( $daftar ) );
        }
        return $this->db->kueri( $q );
    }

    protected function impor( $id, array $k ) {
        if ( empty( $k['sql'] ) || ! in_array( $k['status'], array( 'siap', 'mengimpor' ), true ) ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Dorongan ini tidak menunggu impor database.', 409 );
        }
        if ( 'siap' === $k['status'] ) {
            $r = $this->hapus_tabel_jurnal( $id, $k, 'tabel_tmp' );
            if ( is_wp_error( $r ) ) {
                return $r;
            }
            $k = $this->sentuh_wajib( $id, array_merge( $k, array( 'status' => 'mengimpor', 'impor_posisi' => 0 ) ) );
            if ( is_wp_error( $k ) ) {
                return $k;
            }
        }
        $sesi = $this->buka_sesi( isset( $k['charset'] ) ? $k['charset'] : '' );
        if ( is_wp_error( $sesi ) ) {
            return $this->galat( 'wpmgr_staging_impor', 'Sesi impor database tidak dapat disiapkan.', 500 );
        }
        try {
            return $this->impor_dalam_sesi( $id, $k );
        } finally {
            $this->tutup_sesi( $sesi );
        }
    }

    /** Badan impor; sesi database sudah dibuka impor() dan ditutup di finally-nya. */
    protected function impor_dalam_sesi( $id, array $k ) {
        $prefix       = $this->db->prefix();
        $prefix_asing = $this->prefix_asing_site();
        if ( is_wp_error( $prefix_asing ) ) {
            return $prefix_asing;
        }
        $h = @fopen( $this->dir( $id ) . 'db.php', 'rb' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $h ) {
            return $this->galat( 'wpmgr_staging_impor', 'Berkas SQL dorongan tidak ada.', 500 );
        }
        if ( self::KEPALA !== fread( $h, strlen( self::KEPALA ) ) ) {
            fclose( $h );
            return $this->galat( 'wpmgr_staging_impor', 'Berkas SQL dorongan rusak.', 500 );
        }
        fseek( $h, strlen( self::KEPALA ) + (int) $k['impor_posisi'] );
        $pemecah = new WPMGR_Staging_Sql( (int) $k['impor_posisi'] );
        while ( ! feof( $h ) ) {
            $data = fread( $h, 1048576 );
            if ( false === $data ) {
                fclose( $h );
                return $this->galat( 'wpmgr_staging_impor', 'Berkas SQL dorongan tidak dapat dibaca.', 500 );
            }
            $hasil = $pemecah->tambah( $data );
            if ( is_wp_error( $hasil ) ) {
                fclose( $h );
                return $hasil;
            }
            foreach ( $hasil as $satu ) {
                $r = $this->jalankan_pernyataan( $id, $k, $satu[0], $prefix, $prefix_asing );
                if ( is_wp_error( $r ) ) {
                    fclose( $h );
                    return $r;
                }
                // Kursor disimpan setiap pernyataan: request yang mati di
                // tengah hanya mengulang paling banyak satu pernyataan.
                $k['impor_posisi'] = $satu[1];
                $k                 = $this->sentuh_wajib( $id, $k );
                if ( is_wp_error( $k ) ) {
                    fclose( $h );
                    return $k;
                }
                // Diperiksa setelah satu pernyataan selesai: setiap request maju.
                if ( $this->waktu_habis() ) {
                    fclose( $h );
                    return $this->lagi( $id, $k );
                }
            }
        }
        fclose( $h );
        if ( '' !== WPMGR_Staging_Sql::tanpa_komentar_awal( $pemecah->sisa() ) ) {
            return $this->galat( 'wpmgr_staging_impor', 'Berkas SQL terpotong di akhir.', 422 );
        }
        if ( true !== $this->pertahankan_opsi( $k ) ) {
            return $this->galat( 'wpmgr_staging_impor', 'Opsi produksi tidak dapat dipertahankan.', 500 );
        }
        $k['status'] = 'terimpor';
        return $this->selesaikan_langkah( $id, $k, 'impor', array( 'selesai' => true, 'status' => 'terimpor' ) );
    }

    // ---- tukar --------------------------------------------------------

    /**
     * Urutan operasi berkas. mu-plugins dikerjakan paling akhir di setiap
     * aksi karena dimuat di SETIAP request, termasuk request tukar
     * berikutnya. Koreksi #14: hapus di wp-content/uploads/ tidak pernah
     * dijalankan -- berkas yang hanya ada di produksi (lampiran pesanan
     * baru) tidak boleh hilang karena dorongan.
     */
    public function operasi( array $rencana ) {
        $ops = array();
        foreach ( array( 'ganti', 'hapus' ) as $aksi ) {
            $daftar = 'ganti' === $aksi
                ? array_map( function ( $b ) {
                    return $b['path'];
                }, $rencana['berkas'] )
                : $rencana['hapus'];
            $biasa = array();
            $mu    = array();
            foreach ( $daftar as $path ) {
                $rendah = strtolower( $path );
                if ( 'hapus' === $aksi && 0 === strpos( $rendah, 'wp-content/uploads/' ) ) {
                    continue;
                }
                if ( 0 === strpos( $rendah, 'wp-content/mu-plugins/' ) ) {
                    $mu[] = array( $aksi, $path );
                } else {
                    $biasa[] = array( $aksi, $path );
                }
            }
            $ops = array_merge( $ops, $biasa, $mu );
        }
        return $ops;
    }

    /** Fix round 1: hanya hash sha256 heksadesimal yang pernah ditanam ke kode PHP. */
    protected static function hash_sah( $hash ) {
        return is_string( $hash ) && 1 === preg_match( '/^[0-9a-f]{64}\z/', $hash );
    }

    protected static function basename_sah( $b ) {
        return is_string( $b ) && 1 === preg_match( '/^[A-Za-z0-9._-]{1,100}\/[A-Za-z0-9._-]{1,100}\.php\z/', $b )
            && false === strpos( $b, '..' );
    }

    /** Basename plugin connector (plugin_basename(WPMGR_FILE)), atau bawaan bila tidak tersedia/tidak sah. */
    public static function basename_connector() {
        $b = ( defined( 'WPMGR_FILE' ) && function_exists( 'plugin_basename' ) ) ? plugin_basename( WPMGR_FILE ) : '';
        return self::basename_sah( $b ) ? $b : 'wp-manager-connector/wp-manager-connector.php';
    }

    /** '' bila $hash tidak sah -- pemanggil memperlakukannya sebagai gagal pasang. */
    public static function isi_maintenance( $hash, $sampai ) {
        if ( ! self::hash_sah( $hash ) ) {
            return '';
        }
        return "<?php\n// " . self::TANDA_MAINTENANCE . ".\n"
            . "// WordPress mengabaikan berkas ini 10 menit setelah \$upgrading, jadi batasnya 15 menit sejak dibuat.\n"
            . '$upgrading = ' . (int) $sampai . ";\n"
            . "if ( isset( \$_SERVER['HTTP_X_WPMGR_LEWATI'] ) && hash_equals( '" . $hash . "', hash( 'sha256', (string) \$_SERVER['HTTP_X_WPMGR_LEWATI'] ) ) ) {\n"
            . "    \$upgrading = 0;\n"
            . "}\n";
    }

    /** '' bila $hash tidak sah; $basename yang tidak lolos regex ketat diganti basename_connector(). */
    public static function isi_mu_aman( $hash, $basename = null ) {
        if ( ! self::hash_sah( $hash ) ) {
            return '';
        }
        $basename = self::basename_sah( $basename ) ? $basename : self::basename_connector();
        return "<?php\n/**\n * Plugin Name: WP Manager — pengaman dorong (sementara)\n"
            . " * Description: Dipasang selama dorongan staging diterapkan dan dihapus sesudahnya.\n */\n"
            . "if ( isset( \$_SERVER['HTTP_X_WPMGR_LEWATI'] ) && hash_equals( '" . $hash . "', hash( 'sha256', (string) \$_SERVER['HTTP_X_WPMGR_LEWATI'] ) ) ) {\n"
            . "    add_filter( 'option_active_plugins', function () {\n"
            . "        return array( '" . $basename . "' );\n"
            . "    }, PHP_INT_MAX );\n"
            . "    add_filter( 'site_option_active_sitewide_plugins', function () {\n"
            . "        return array();\n"
            . "    }, PHP_INT_MAX );\n"
            . "    add_filter( 'pre_option_template', function () {\n"
            . "        return 'wpmgr-tanpa-tema';\n"
            . "    } );\n"
            . "    add_filter( 'pre_option_stylesheet', function () {\n"
            . "        return 'wpmgr-tanpa-tema';\n"
            . "    } );\n"
            . "}\n";
    }

    /** Berkas dimuat PHP di setiap request: tulis ke nama sementara non-.php lalu rename, tidak pernah setengah jadi. */
    protected function tulis_atomik( $berkas, $isi ) {
        $sementara = $berkas . '.' . bin2hex( random_bytes( 4 ) ) . '.tmp';
        if ( false === @file_put_contents( $sementara, $isi ) || ! @rename( $sementara, $berkas ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            @unlink( $sementara ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            return false;
        }
        return true;
    }

    protected function maintenance_milik_kita() {
        $m = $this->akar . '.maintenance';
        return is_file( $m ) && ! is_link( $m )
            && false !== strpos( (string) @file_get_contents( $m, false, null, 0, 4096 ), self::TANDA_MAINTENANCE ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
    }

    /** Koreksi #14: .maintenance milik pihak lain (mis. update inti WordPress) tidak pernah ditimpa. */
    protected function maintenance_asing() {
        $m = $this->akar . '.maintenance';
        return is_link( $m ) || ( file_exists( $m ) && ! $this->maintenance_milik_kita() );
    }

    /**
     * Koreksi #10: .maintenance ($upgrading = dibuat + 300, jadi WordPress
     * mengabaikannya 15 menit sejak dibuat) dan mu-plugin pengaman. true
     * bila .maintenance terpasang; mu-plugin yang gagal ditulis bukan alasan
     * gagal -- request tukar berikutnya tetap berjalan, hanya dengan plugin
     * lain ikut dimuat.
     */
    protected function pasang_pengaman( array $k ) {
        if ( $this->maintenance_asing() ) {
            return false;
        }
        $hash = isset( $k['token_hash'] ) ? (string) $k['token_hash'] : '';
        $isi  = self::isi_maintenance( $hash, (int) $k['maintenance_dibuat'] + 300 );
        if ( '' === $isi || ! $this->tulis_atomik( $this->akar . '.maintenance', $isi ) ) {
            return false;
        }
        $mu = $this->dir_mu . self::MU_AMAN;
        if ( $this->pastikan_dir( $this->dir_mu ) && ! is_link( $mu ) ) {
            $this->tulis_atomik( $mu, self::isi_mu_aman( $hash ) );
        }
        return true;
    }

    protected function lepas_pengaman() {
        if ( $this->maintenance_milik_kita() ) {
            @unlink( $this->akar . '.maintenance' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
        $mu = $this->dir_mu . self::MU_AMAN;
        if ( is_file( $mu ) || is_link( $mu ) ) {
            @unlink( $mu ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
    }

    /** Pindah berkas; lintas filesystem disalin lalu sumbernya dihapus, dan salinan dibatalkan bila sumber tidak bisa dihapus. */
    protected function pindah( $dari, $ke ) {
        if ( ! $this->pastikan_dir( dirname( $ke ) ) ) {
            return false;
        }
        if ( @rename( $dari, $ke ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            return true;
        }
        if ( is_file( $dari ) && ! is_dir( $ke ) && @copy( $dari, $ke ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( @unlink( $dari ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
                return true;
            }
            @unlink( $ke ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
        return false;
    }

    protected function jurnal( $id, array $entri ) {
        $berkas = $this->dir( $id ) . 'jurnal.php';
        if ( ! is_file( $berkas ) && false === @file_put_contents( $berkas, self::KEPALA ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            return false;
        }
        return false !== @file_put_contents( $berkas, json_encode( $entri ) . "\n", FILE_APPEND | LOCK_EX ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
    }

    protected function baca_jurnal( $id ) {
        $data  = $this->baca_terlindung( $this->dir( $id ) . 'jurnal.php' );
        $hasil = array();
        foreach ( explode( "\n", false === $data ? '' : $data ) as $baris ) {
            $e = json_decode( $baris, true );
            if ( is_array( $e ) && isset( $e['aksi'] ) ) {
                $hasil[] = $e;
            }
        }
        return $hasil;
    }

    /**
     * Satu operasi berkas, dicatat di jurnal SEBELUM apa pun disentuh.
     * Idempoten: aman diulang dari titik mana pun (lihat tabel operasi di
     * brief Task 8). Tujuan selalu path kanonik dari untuk_ditulis();
     * direktori atau symlink di tujuan ditolak -- memindahkannya ke lama/
     * akan membuat pemulihan tidak mengenalinya sebagai berkas.
     */
    protected function jalankan_op( $id, array $op ) {
        list( $aksi, $rel ) = $op;
        $tujuan = WPMGR_Staging_Path::untuk_ditulis( $this->akar, $rel );
        if ( is_wp_error( $tujuan ) ) {
            return $this->galat( 'wpmgr_staging_tukar', 'Path tujuan tidak boleh ditulis: ' . WPMGR_Staging::bersih( $rel, 200 ), 500 );
        }
        $baru = $this->dir( $id ) . 'baru/' . $rel;
        $lama = $this->dir( $id ) . 'lama/' . $rel;
        if ( ! $this->jurnal( $id, array( 'aksi' => $aksi, 'path' => $rel ) ) ) {
            return $this->galat( 'wpmgr_staging_tulis', 'Jurnal dorong tidak dapat ditulis.', 500 );
        }
        clearstatcache();
        if ( is_dir( $tujuan ) || is_link( $tujuan ) ) {
            return $this->galat( 'wpmgr_staging_tukar', 'Tujuan berupa direktori atau symlink: ' . WPMGR_Staging::bersih( $rel, 200 ), 500 );
        }
        if ( is_file( $tujuan ) && ! is_file( $lama ) ) {
            if ( 'ganti' === $aksi && ! is_file( $baru ) ) {
                return true; // sudah ditukar pada request sebelumnya
            }
            if ( ! $this->pindah( $tujuan, $lama ) ) {
                return $this->galat( 'wpmgr_staging_tukar', 'Berkas produksi tidak dapat dipindahkan: ' . WPMGR_Staging::bersih( $rel, 200 ), 500 );
            }
        }
        if ( 'ganti' === $aksi ) {
            if ( is_file( $baru ) ) {
                if ( ! $this->pindah( $baru, $tujuan ) ) {
                    return $this->galat( 'wpmgr_staging_tukar', 'Berkas baru tidak dapat dipasang: ' . WPMGR_Staging::bersih( $rel, 200 ), 500 );
                }
            } elseif ( ! is_file( $tujuan ) ) {
                return $this->galat( 'wpmgr_staging_tukar', 'Berkas baru hilang dari area dorong: ' . WPMGR_Staging::bersih( $rel, 200 ), 500 );
            }
        }
        return true;
    }

    /** Semua tabel pada rencana db sudah dalam keadaan tertukar (RENAME sudah terjadi). */
    protected function db_sudah_ditukar( array $rencana_db, array $ada ) {
        if ( empty( $rencana_db ) ) {
            return false;
        }
        foreach ( $rencana_db as $t ) {
            $asli = (string) $t[0];
            if ( isset( $ada[ 'wpmgr_tmp_' . $asli ] ) || ! isset( $ada[ $asli ] ) || ( (int) $t[1] && ! isset( $ada[ 'wpmgr_old_' . $asli ] ) ) ) {
                return false;
            }
        }
        return true;
    }

    /**
     * Pemeriksaan sebelum status 'menukar' diset -- tidak ada yang disentuh
     * bila salah satunya gagal: .maintenance milik pihak lain, atau tabel
     * wpmgr_old_* (salinan produksi dari dorongan sebelumnya yang belum
     * dibersihkan) yang akan bertabrakan dengan RENAME kita.
     */
    protected function prapemeriksaan_tukar( array $k ) {
        if ( $this->maintenance_asing() ) {
            return $this->galat( 'wpmgr_staging_maintenance', 'Site sedang dalam mode pemeliharaan lain; tukar ditunda.', 409 );
        }
        if ( empty( $k['sql'] ) ) {
            return true;
        }
        $ada = $this->tabel_ada();
        if ( is_wp_error( $ada ) ) {
            return $ada;
        }
        $tmp = $this->tabel_journal_aman( $this->jurnal_tmp( $k ) );
        if ( is_wp_error( $tmp ) ) {
            return $tmp;
        }
        $ditemukan = 0;
        foreach ( $tmp as $t ) {
            if ( 0 !== strpos( $t, 'wpmgr_tmp_' ) || ! isset( $ada[ $t ] ) ) {
                continue;
            }
            $ditemukan++;
            if ( isset( $ada[ 'wpmgr_old_' . substr( $t, strlen( 'wpmgr_tmp_' ) ) ] ) ) {
                return $this->galat( 'wpmgr_staging_tabel_lama',
                    'Tabel produksi lama dari dorongan sebelumnya masih ada; bersihkan dorongan itu dulu.', 409 );
            }
        }
        if ( 0 === $ditemukan ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Tabel hasil impor tidak ditemukan.', 409 );
        }
        return true;
    }

    /**
     * Satu RENAME TABLE atomik, hanya atas tabel di jurnal push ini.
     * Tulis-lebih-dulu: nama wpmgr_old_* masuk jurnal tabel dan rencana db
     * masuk keadaan + jurnal.php SEBELUM RENAME. Opsi produksi disalin
     * ulang tepat sebelum RENAME supaya perubahannya sejak impor (termasuk
     * penyegaran kunci dorong) ikut terbawa.
     */
    protected function tukar_db( $id, array &$k ) {
        $ada = $this->tabel_ada();
        if ( is_wp_error( $ada ) ) {
            return $ada;
        }
        if ( ! empty( $k['db_rencana'] ) && is_array( $k['db_rencana'] ) && $this->db_sudah_ditukar( $k['db_rencana'], $ada ) ) {
            // Request sebelumnya mati tepat setelah RENAME berhasil --
            // cache objeknya belum pernah dikosongkan (fix round 2, N3).
            $k['db_ditukar'] = true;
            $simpan          = $this->sentuh_wajib( $id, $k );
            $this->kosongkan_cache();
            return is_wp_error( $simpan ) ? $simpan : true;
        }
        $sesi = $this->buka_sesi( isset( $k['charset'] ) ? $k['charset'] : '' );
        if ( is_wp_error( $sesi ) ) {
            return $this->galat( 'wpmgr_staging_tukar', 'Sesi database tidak dapat disiapkan.', 500 );
        }
        try {
            return $this->tukar_db_dalam_sesi( $id, $k, $ada );
        } finally {
            $this->tutup_sesi( $sesi );
        }
    }

    /** Badan tukar_db(); sesi (lock_wait_timeout = 5) sudah dibuka pemanggil. */
    protected function tukar_db_dalam_sesi( $id, array &$k, array $ada ) {
        if ( true !== $this->pertahankan_opsi( $k ) ) {
            return $this->galat( 'wpmgr_staging_tukar', 'Opsi produksi tidak dapat dipertahankan.', 500 );
        }
        $jurnal = $this->tabel_journal_aman( $this->jurnal_tmp( $k ) );
        if ( is_wp_error( $jurnal ) ) {
            return $jurnal;
        }
        $prefix = $this->db->prefix();
        $pasang = array();
        $catat  = array();
        $old    = ( isset( $k['tabel_old'] ) && is_array( $k['tabel_old'] ) ) ? $k['tabel_old'] : array();
        foreach ( $jurnal as $t ) {
            if ( 0 !== strpos( $t, 'wpmgr_tmp_' ) || ! isset( $ada[ $t ] ) ) {
                continue;
            }
            $asli = substr( $t, strlen( 'wpmgr_tmp_' ) );
            if ( 0 === stripos( $asli, $prefix . 'wpmgr_' ) ) {
                continue; // Koreksi #14: tabel {prefix}wpmgr_* tidak pernah ditukar.
            }
            $lama = 'wpmgr_old_' . $asli;
            if ( isset( $ada[ $lama ] ) ) {
                return $this->galat( 'wpmgr_staging_tukar', 'Tabel produksi lama dari dorongan sebelumnya masih ada.', 500 );
            }
            if ( isset( $ada[ $asli ] ) ) {
                $pasang[] = "`{$asli}` TO `{$lama}`";
                if ( ! in_array( $lama, $old, true ) ) {
                    $old[] = $lama;
                }
            }
            $pasang[] = "`{$t}` TO `{$asli}`";
            $catat[]  = array( $asli, isset( $ada[ $asli ] ) ? 1 : 0 );
        }
        if ( empty( $pasang ) ) {
            return $this->galat( 'wpmgr_staging_tukar', 'Tabel hasil impor tidak ditemukan.', 500 );
        }
        $k['tabel_old']  = $old;
        $k['db_rencana'] = $catat;
        $simpan          = $this->sentuh_wajib( $id, $k );
        if ( is_wp_error( $simpan ) ) {
            return $simpan;
        }
        $k = $simpan;
        if ( ! $this->jurnal( $id, array( 'aksi' => 'db', 'tabel' => $catat ) ) ) {
            return $this->galat( 'wpmgr_staging_tulis', 'Jurnal dorong tidak dapat ditulis.', 500 );
        }
        // Satu pernyataan: MySQL menukar semua nama secara atomik.
        if ( true !== $this->db->kueri( 'RENAME TABLE ' . implode( ', ', $pasang ) ) ) {
            return $this->galat( 'wpmgr_staging_tukar', 'Penukaran tabel database gagal.', 500 );
        }
        $k['db_ditukar'] = true;
        $k               = $this->sentuh( $id, $k );
        $this->kosongkan_cache();
        return true;
    }

    /** Kegagalan di tengah tukar: pulihkan otomatis (dengan tenggat request ini), lalu laporkan galat tukar. */
    protected function gagal_tukar( $id, WP_Error $galat ) {
        $p       = $this->pulihkan_inti( $id, null, true );
        $selesai = is_array( $p ) && ! empty( $p['selesai'] );
        $pesan   = $selesai
            ? 'Penukaran gagal dan sudah dipulihkan otomatis: ' . $galat->get_error_message()
            : 'Penukaran gagal; pemulihan belum selesai, lanjutkan dengan langkah pulihkan: ' . $galat->get_error_message();
        return new WP_Error( 'wpmgr_staging_tukar', $pesan, array( 'status' => 500, 'pemulihan' => $selesai ? 'dipulihkan' : 'memulihkan' ) );
    }

    protected function tukar( $id, array $k, $token ) {
        if ( ! self::token_sah( $token ) ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Token tukar tidak sah.', 400 );
        }
        if ( 'menukar' === $k['status'] ) {
            if ( ! $this->token_cocok( $k, $token ) ) {
                return $this->galat( 'wpmgr_staging_token', 'Token tukar tidak cocok.', 403 );
            }
            // Fix round 1: pengaman dipasang ulang pada SETIAP request tukar
            // dengan cap waktu segar -- .maintenance yang hilang atau
            // kedaluwarsa di tengah tukar panjang kembali; bila dashboard
            // berhenti, site tetap pulih sendiri 15 menit setelah aktivitas
            // terakhir. Gagal memasang di sini tidak menghentikan tukar.
            $k['maintenance_dibuat'] = time();
            $k                       = $this->sentuh( $id, $k );
            $this->pasang_pengaman( $k );
        } else {
            if ( ( empty( $k['sql'] ) ? 'siap' : 'terimpor' ) !== $k['status'] ) {
                return $this->galat( 'wpmgr_staging_urutan', 'Dorongan belum siap ditukar.', 409 );
            }
            $r = $this->rencana( $id, $k );
            if ( is_wp_error( $r ) ) {
                return $r;
            }
            $cek = $this->prapemeriksaan_tukar( $k );
            if ( is_wp_error( $cek ) ) {
                return $cek;
            }
            // kunci() sudah disegarkan di terapkan() pada request ini --
            // baru sekarang status 'menukar' boleh ditulis.
            $k = $this->sentuh_wajib( $id, array_merge( $k, array(
                'status' => 'menukar', 'token_hash' => hash( 'sha256', $token ), 'tukar' => 0,
                'maintenance_dibuat' => time(), 'db_ditukar' => false,
            ) ) );
            if ( is_wp_error( $k ) ) {
                return $k;
            }
            if ( ! $this->pasang_pengaman( $k ) ) {
                return $this->gagal_tukar( $id, $this->galat( 'wpmgr_staging_tukar', 'Mode pemeliharaan tidak dapat dipasang.', 500 ) );
            }
        }
        $k = $this->sentuh( $id, $k );
        $r = $this->rencana( $id, $k );
        if ( is_wp_error( $r ) ) {
            return $this->gagal_tukar( $id, $r );
        }
        $ops  = $this->operasi( $r );
        $maju = false;
        while ( $k['tukar'] < count( $ops ) ) {
            if ( $maju && $this->waktu_habis() ) {
                return $this->lagi( $id, $k );
            }
            $hasil = $this->jalankan_op( $id, $ops[ $k['tukar'] ] );
            if ( is_wp_error( $hasil ) ) {
                $this->sentuh( $id, $k );
                return $this->gagal_tukar( $id, $hasil );
            }
            $k['tukar']++;
            $k    = $this->sentuh( $id, $k );
            $maju = true;
        }
        if ( ! empty( $k['sql'] ) && empty( $k['db_ditukar'] ) ) {
            if ( $maju && $this->waktu_habis() ) {
                return $this->lagi( $id, $k );
            }
            $hasil = $this->tukar_db( $id, $k );
            if ( is_wp_error( $hasil ) ) {
                $this->sentuh( $id, $k );
                return $this->gagal_tukar( $id, $hasil );
            }
        }
        // Fix round 1 (I2a): 'ditukar' disimpan DULU, baru pengaman dilepas.
        // Bila keadaan tidak tersimpan, pengaman tetap terpasang dan langkah
        // gagal -- ulangan tukar menyelesaikannya (semua operasi idempoten).
        $k['status'] = 'ditukar';
        $hasil       = $this->selesaikan_langkah( $id, $k, 'tukar', array( 'selesai' => true, 'status' => 'ditukar' ) );
        if ( is_wp_error( $hasil ) ) {
            return $hasil;
        }
        $this->lepas_pengaman();
        return $hasil;
    }

    // ---- pulihkan -----------------------------------------------------

    /**
     * Membalik satu entri jurnal. true = selesai (termasuk "tidak ada yang
     * perlu dibalik"); string = masalah (pesan tetap). Setiap cabang
     * idempoten, jadi entri yang sama boleh dibalik berkali-kali.
     */
    protected function balikkan( $id, array $e, array &$k ) {
        if ( 'db' === $e['aksi'] ) {
            return $this->balikkan_db( $id, $k, isset( $e['tabel'] ) ? (array) $e['tabel'] : array() );
        }
        $rel = isset( $e['path'] ) ? $e['path'] : null;
        if ( ! is_string( $rel ) || ! WPMGR_Staging_Path::boleh_ditulis( $rel ) ) {
            return true; // Tidak pernah bisa tercatat oleh jalankan_op().
        }
        $tujuan = WPMGR_Staging_Path::untuk_ditulis( $this->akar, $rel );
        if ( is_wp_error( $tujuan ) ) {
            return 'Berkas tidak dapat dikembalikan: ' . WPMGR_Staging::bersih( $rel, 200 );
        }
        $baru = $this->dir( $id ) . 'baru/' . $rel;
        $lama = $this->dir( $id ) . 'lama/' . $rel;
        clearstatcache();
        if ( 'ganti' === $e['aksi'] && ! is_file( $baru ) && is_file( $tujuan ) && ! is_link( $tujuan ) ) {
            // Berkas baru sudah terpasang: kembalikan ke baru/.
            if ( ! $this->pindah( $tujuan, $baru ) ) {
                return 'Berkas tidak dapat dikembalikan: ' . WPMGR_Staging::bersih( $rel, 200 );
            }
        }
        if ( is_file( $lama ) ) {
            if ( is_dir( $tujuan ) || is_link( $tujuan ) || ! $this->pindah( $lama, $tujuan ) ) {
                return 'Berkas tidak dapat dikembalikan: ' . WPMGR_Staging::bersih( $rel, 200 );
            }
        }
        return true;
    }

    /**
     * Membalik RENAME TABLE hanya bila keadaan tabel membuktikan penukaran
     * pernah terjadi: untuk tabel yang punya salinan lama, wpmgr_old_* ada;
     * untuk tabel baru, wpmgr_tmp_* sudah tidak ada (tabel itu kita buat
     * dan hanya bisa hilang lewat RENAME). Satu pernyataan, atomik.
     */
    protected function balikkan_db( $id, array &$k, array $tabel ) {
        $ada = $this->tabel_ada();
        if ( is_wp_error( $ada ) ) {
            return 'Daftar tabel tidak dapat dibaca.';
        }
        $prefix = $this->db->prefix();
        $pasang = array();
        foreach ( $tabel as $t ) {
            if ( ! is_array( $t ) || ! isset( $t[0], $t[1] ) ) {
                continue;
            }
            $asli = (string) $t[0];
            if ( 1 !== preg_match( '/^[A-Za-z0-9_$]{1,54}\z/', $asli ) || 0 !== strpos( $asli, $prefix ) ) {
                continue;
            }
            $tmp = 'wpmgr_tmp_' . $asli;
            $old = 'wpmgr_old_' . $asli;
            if ( (int) $t[1] ) {
                if ( isset( $ada[ $old ] ) && isset( $ada[ $asli ] ) && ! isset( $ada[ $tmp ] ) ) {
                    $pasang[] = "`{$asli}` TO `{$tmp}`";
                    $pasang[] = "`{$old}` TO `{$asli}`";
                } elseif ( isset( $ada[ $old ] ) && ! isset( $ada[ $asli ] ) ) {
                    $pasang[] = "`{$old}` TO `{$asli}`";
                }
            } elseif ( isset( $ada[ $asli ] ) && ! isset( $ada[ $tmp ] ) ) {
                $pasang[] = "`{$asli}` TO `{$tmp}`";
            }
        }
        if ( empty( $pasang ) ) {
            return true;
        }
        // Tulis-lebih-dulu (fix round 1, I2b): tanda bahwa tabel produksi
        // sesudah tukar (yang bisa memuat pesanan/komentar baru) dikembalikan
        // ke nama wpmgr_tmp_* -- pulihkan lalu menyimpannya sebagai tabel
        // batal, bukan men-DROP-nya.
        $k['db_dibalik'] = true;
        $simpan          = $this->sentuh_wajib( $id, $k );
        if ( is_wp_error( $simpan ) ) {
            return 'Keadaan dorongan tidak dapat disimpan.';
        }
        $k    = $simpan;
        $sesi = $this->buka_sesi( null );
        if ( is_wp_error( $sesi ) ) {
            return 'Sesi database tidak dapat disiapkan.';
        }
        try {
            $r = $this->db->kueri( 'RENAME TABLE ' . implode( ', ', $pasang ) );
        } finally {
            $this->tutup_sesi( $sesi );
        }
        if ( true !== $r ) {
            return 'Tabel database tidak dapat dikembalikan.';
        }
        $this->kosongkan_cache();
        return true;
    }

    /**
     * Fix round 1 (I2b, Ruling R11): setelah pertukaran DB yang terbukti,
     * tabel yang dibalik ke wpmgr_tmp_* memuat tulisan produksi SESUDAH
     * tukar (pesanan, komentar). Tabel itu TIDAK di-DROP, tetapi diganti
     * nama ke wpmgr_b<n>_<asli> (n = slot bebas terkecil 1..99) dan dicatat
     * di jurnal 'tabel_batal' dengan cap waktu. bersihkan() menahannya
     * UMUR_BATAL (24 jam) sejak dibuat.
     *
     * Bentuk nama: 'wpmgr_batal_' (12 karakter) tidak muat untuk nama asli
     * sampai 54 karakter (batas ubah(): 'wpmgr_tmp_' + asli <= 64). Awalan
     * 'wpmgr_b<n>_' paling panjang 10 karakter (n = 99) -- sama dengan
     * 'wpmgr_tmp_', jadi selalu muat 64.
     *
     * Fix round 2 (N1): pemulihan TIDAK PERNAH gagal karena tidak ada slot.
     * Bila 99 slot terpakai, tabel dibiarkan di nama wpmgr_tmp_<asli> dan
     * dicatat di tabel_batal dengan cap waktu yang sama (tetap ditahan 24
     * jam, tidak di-DROP); impor dorongan lain menolak memakai nama yang
     * masih ditahan (ditahan_area_lain()).
     */
    protected function simpan_tabel_batal( $id, array &$k ) {
        $calon = array();
        foreach ( ( isset( $k['db_rencana'] ) && is_array( $k['db_rencana'] ) ) ? $k['db_rencana'] : array() as $t ) {
            if ( is_array( $t ) && isset( $t[0] ) && 1 === preg_match( '/^[A-Za-z0-9_$]{1,54}\z/', (string) $t[0] ) ) {
                $calon[ 'wpmgr_tmp_' . $t[0] ] = (string) $t[0];
            }
        }
        if ( empty( $calon ) ) {
            return true;
        }
        $ada = $this->tabel_ada();
        if ( is_wp_error( $ada ) ) {
            return $ada;
        }
        $aman = $this->tabel_journal_aman( $this->jurnal_tmp( $k ) );
        if ( is_wp_error( $aman ) ) {
            return $aman;
        }
        $sesi = $this->buka_sesi( null );
        if ( is_wp_error( $sesi ) ) {
            return $sesi;
        }
        try {
            foreach ( $aman as $t ) {
                if ( ! isset( $calon[ $t ], $ada[ $t ] ) ) {
                    continue;
                }
                $nama = null;
                for ( $n = 1; $n <= 99 && null === $nama; $n++ ) {
                    $c = 'wpmgr_b' . $n . '_' . $calon[ $t ];
                    if ( ! isset( $ada[ $c ] ) ) {
                        $nama = $c;
                    }
                }
                if ( null === $nama ) {
                    // Tanpa slot: ditahan di nama sementaranya sendiri.
                    $batal                  = ( isset( $k['tabel_batal'] ) && is_array( $k['tabel_batal'] ) ) ? $k['tabel_batal'] : array();
                    $batal[]                = $t;
                    $k['tabel_batal']       = array_values( array_unique( $batal ) );
                    $dibuat                 = ( isset( $k['batal_dibuat'] ) && is_array( $k['batal_dibuat'] ) ) ? $k['batal_dibuat'] : array();
                    $dibuat[ $t ]           = time();
                    $k['batal_dibuat']      = $dibuat;
                    $k['tabel_tmp']         = array_values( array_diff( $this->jurnal_tmp( $k ), array( $t ) ) );
                    $simpan                 = $this->sentuh_wajib( $id, $k );
                    if ( is_wp_error( $simpan ) ) {
                        return $simpan;
                    }
                    $k = $simpan;
                    continue;
                }
                $batal            = ( isset( $k['tabel_batal'] ) && is_array( $k['tabel_batal'] ) ) ? $k['tabel_batal'] : array();
                $batal[]          = $nama;
                $k['tabel_batal'] = array_values( array_unique( $batal ) );
                $dibuat           = ( isset( $k['batal_dibuat'] ) && is_array( $k['batal_dibuat'] ) ) ? $k['batal_dibuat'] : array();
                $dibuat[ $nama ]  = time();
                $k['batal_dibuat'] = $dibuat;
                $simpan           = $this->sentuh_wajib( $id, $k );
                if ( is_wp_error( $simpan ) ) {
                    return $simpan;
                }
                $k = $simpan;
                if ( true !== $this->db->kueri( "RENAME TABLE `{$t}` TO `{$nama}`" ) ) {
                    // Nama itu tidak jadi milik kita: jangan sampai bersihkan()
                    // kelak men-DROP tabel lain yang memakainya.
                    $k['tabel_batal'] = array_values( array_diff( $k['tabel_batal'], array( $nama ) ) );
                    unset( $k['batal_dibuat'][ $nama ] );
                    $k                = $this->sentuh( $id, $k );
                    return $this->galat( 'wpmgr_staging_pulihkan', 'Tabel yang dibatalkan tidak dapat disimpan.', 500 );
                }
                $ada[ $nama ] = true;
                unset( $ada[ $t ] );
                $k['tabel_tmp'] = array_values( array_diff( $this->jurnal_tmp( $k ), array( $t ) ) );
                $k              = $this->sentuh( $id, $k );
            }
        } finally {
            $this->tutup_sesi( $sesi );
        }
        return true;
    }

    /** Dipanggil cron() dan pemanggil luar: flock dulu, lalu pulihkan_inti(). */
    public function pulihkan( $id, $token, $paksa ) {
        if ( ! self::id_sah( $id ) ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Id dorongan tidak sah.', 400 );
        }
        if ( null === $this->keadaan( $id ) ) {
            return $this->galat( 'wpmgr_staging_tidak_ada', 'Dorongan tidak ditemukan.', 404 );
        }
        $flock = null;
        try {
            $flock = $this->kunci_langkah( $id );
            if ( is_wp_error( $flock ) ) {
                $galat = $flock;
                $flock = null;
                return $galat;
            }
            return $this->pulihkan_inti( $id, $token, $paksa );
        } catch ( \Throwable $e ) {
            unset( $e );
            return $this->galat( 'wpmgr_staging_galat', 'Galat tak terduga saat memulihkan dorongan; lihat log server.', 500 );
        } finally {
            $this->lepas_kunci_langkah( $flock );
        }
    }

    /**
     * Aman dipanggil setelah tukar sebagian, setelah tukar lengkap
     * (pembatalan eksplisit), dan berulang kali. Status 'memulihkan'
     * (STATUS_MENYENTUH_PRODUKSI) ditulis SEBELUM entri jurnal pertama
     * dibalik. Pemulihan yang menyisakan masalah TIDAK menjadi
     * 'dipulihkan' (status terminal yang membuat bersihkan() menghapus
     * lama/ dan wpmgr_old_*): statusnya tetap 'memulihkan' dan langkah ini
     * harus diulang. $paksa hanya melewati pemeriksaan token (cron dan
     * pemulihan otomatis); tenggat request selalu dihormati.
     */
    protected function pulihkan_inti( $id, $token, $paksa ) {
        $k = $this->keadaan( $id );
        if ( null === $k ) {
            return $this->galat( 'wpmgr_staging_tidak_ada', 'Dorongan tidak ditemukan.', 404 );
        }
        $status = $this->status( $k );
        $hasil  = array( 'selesai' => true, 'status' => 'dipulihkan' );
        if ( 'dipulihkan' === $status ) {
            return isset( $k['hasil']['pulihkan'] ) && is_array( $k['hasil']['pulihkan'] ) ? $k['hasil']['pulihkan'] : $hasil;
        }
        if ( in_array( $status, self::STATUS_PRA_TUKAR, true ) ) {
            // Produksi belum pernah disentuh: pulihkan = batalkan dorongan.
            if ( ! $paksa && ! self::token_sah( $token ) ) {
                return $this->galat( 'wpmgr_staging_permintaan', 'Token pemulihan tidak sah.', 400 );
            }
            $r = $this->hapus_tabel_jurnal( $id, $k, 'tabel_tmp' );
            if ( is_wp_error( $r ) ) {
                return $r;
            }
            $k['status'] = 'dipulihkan';
            $hasil       = $this->selesaikan_langkah( $id, $k, 'pulihkan', $hasil );
            if ( ! is_wp_error( $hasil ) ) {
                $this->lepas_kunci_setelah_pulih( $id );
            }
            return $hasil;
        }
        if ( ! in_array( $status, self::STATUS_MENYENTUH_PRODUKSI, true ) ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Dorongan ini tidak dapat dipulihkan lewat langkah ini.', 409 );
        }
        if ( ! $paksa && ! $this->token_cocok( $k, $token ) ) {
            return $this->galat( 'wpmgr_staging_token', 'Token pemulihan tidak cocok.', 403 );
        }
        $awal = array( 'status' => 'memulihkan', 'maintenance_dibuat' => time() );
        if ( 'memulihkan' !== $status ) {
            $awal['pulih']       = 0;
            $awal['pulih_gagal'] = 0;
        }
        $k = $this->sentuh_wajib( $id, array_merge( $k, $awal ) );
        if ( is_wp_error( $k ) ) {
            return $k;
        }
        // Dipasang ulang pada SETIAP request pulihkan (fix round 1) dengan
        // cap waktu segar. Gagal memasang tidak menghentikan pemulihan:
        // produksi setengah tertukar lebih buruk daripada tanpa maintenance.
        $this->pasang_pengaman( $k );
        $jurnal = $this->baca_jurnal( $id );
        $sudah  = isset( $k['pulih'] ) ? (int) $k['pulih'] : 0;
        $gagal  = isset( $k['pulih_gagal'] ) ? (int) $k['pulih_gagal'] : 0;
        $maju   = false;
        for ( $i = count( $jurnal ) - 1 - $sudah; $i >= 0; $i-- ) {
            if ( $maju && $this->waktu_habis() ) {
                return $this->lagi( $id, $k );
            }
            if ( true !== $this->balikkan( $id, $jurnal[ $i ], $k ) ) {
                $gagal++;
            }
            $sudah++;
            $maju             = true;
            $k['pulih']       = $sudah;
            $k['pulih_gagal'] = $gagal;
            $k                = $this->sentuh( $id, $k );
        }
        $ada = 0 === $gagal ? $this->tabel_ada() : null;
        if ( is_wp_error( $ada ) ) {
            $gagal++;
        } elseif ( is_array( $ada ) ) {
            foreach ( ( isset( $k['tabel_old'] ) && is_array( $k['tabel_old'] ) ) ? $k['tabel_old'] : array() as $t ) {
                if ( isset( $ada[ (string) $t ] ) ) {
                    $gagal++; // Salinan produksi lama belum kembali ke nama aslinya.
                }
            }
        }
        if ( $gagal > 0 ) {
            // Putaran berikutnya membalik ulang seluruh jurnal (idempoten).
            $k['pulih']       = 0;
            $k['pulih_gagal'] = 0;
            $this->sentuh( $id, $k );
            return $this->galat( 'wpmgr_staging_pulihkan',
                'Sebagian berkas atau tabel belum dapat dikembalikan; ulangi langkah pulihkan.', 500 );
        }
        if ( ! empty( $k['db_ditukar'] ) || ! empty( $k['db_dibalik'] ) ) {
            $r = $this->simpan_tabel_batal( $id, $k );
            if ( is_wp_error( $r ) ) {
                return $r;
            }
        }
        $r = $this->hapus_tabel_jurnal( $id, $k, 'tabel_tmp' );
        if ( is_wp_error( $r ) ) {
            return $r;
        }
        // Semua wpmgr_old_* sudah terbukti kembali ke nama aslinya di atas.
        $k['tabel_old'] = array();
        $k['status']    = 'dipulihkan';
        $hasil          = $this->selesaikan_langkah( $id, $k, 'pulihkan', $hasil );
        if ( ! is_wp_error( $hasil ) ) {
            // Fix round 2 (N1): produksi sudah kembali dan tersimpan
            // 'dipulihkan' -- pengaman dan kunci dorong dilepas sekarang,
            // bukan menunggu bersihkan(), supaya dorongan lain tidak tertahan.
            // Kunci yang gagal dilepas tetap boleh direbut (status terminal).
            $this->lepas_pengaman();
            $this->lepas_kunci_setelah_pulih( $id );
        }
        return $hasil;
    }

    /**
     * Fix round 3: kunci dorong yang gagal dilepas setelah 'dipulihkan'
     * tidak mengubah hasil (produksi sudah kembali), tetapi dicatat di
     * keadaan ('kunci_tertahan') supaya terlihat; bersihkan() atau
     * perebutan kunci basi (status terminal) melepasnya kemudian.
     */
    protected function lepas_kunci_setelah_pulih( $id ) {
        $r = $this->lepas_kunci( $id );
        $k = $this->keadaan( $id );
        if ( null !== $k ) {
            $k['kunci_tertahan'] = is_wp_error( $r );
            $this->simpan_keadaan( $id, $k );
        }
    }

    // ---- selesai ------------------------------------------------------

    protected function selesai( $id, array $k ) {
        if ( 'ditukar' !== $k['status'] ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Dorongan belum ditukar.', 409 );
        }
        // Fix round 2: 'selesai' disimpan dulu, baru pengaman dilepas.
        $k['status'] = 'selesai';
        $hasil       = $this->selesaikan_langkah( $id, $k, 'selesai', array( 'selesai' => true, 'status' => 'selesai' ) );
        if ( ! is_wp_error( $hasil ) ) {
            $this->lepas_pengaman();
        }
        return $hasil;
    }

    /**
     * Cron memulihkan tukar/pulihkan yang macet. Keadaan dibaca ULANG di
     * bawah flock: request dashboard yang baru saja melanjutkan (dan
     * menyentuh 'diubah') membatalkan pemulihan ini. Tabel hanya disentuh
     * bila kunci dorong milik push ini atau bebas.
     */
    protected function pulihkan_macet( $id ) {
        $flock = $this->kunci_langkah( $id );
        if ( is_wp_error( $flock ) ) {
            return;
        }
        try {
            $k      = $this->keadaan( $id );
            $diubah = ( null !== $k && isset( $k['diubah'] ) ) ? (int) $k['diubah'] : time();
            if ( null === $k || ! in_array( $this->status( $k ), array( 'menukar', 'memulihkan' ), true ) || $diubah >= time() - self::DIAM_MACET ) {
                return;
            }
            $pemegang = $this->kunci_pemegang();
            if ( is_wp_error( $pemegang ) || ( '' !== $pemegang && $pemegang !== $id ) ) {
                return;
            }
            $this->pulihkan_inti( $id, null, true );
        } catch ( \Throwable $e ) {
            unset( $e );
        } finally {
            $this->lepas_kunci_langkah( $flock );
        }
    }

    /**
     * Fix round 2 (N4): berkas <id>.lock tanpa area (mis. lepas_kunci gagal
     * setelah area dihapus) dibuang -- hanya bila tidak sedang dipegang.
     */
    protected function sapu_kunci_langkah( $id ) {
        if ( is_dir( $this->dir( $id ) ) ) {
            return;
        }
        $h = $this->kunci_langkah( $id );
        if ( is_wp_error( $h ) ) {
            return;
        }
        $this->lepas_kunci_langkah( $h );
        if ( ! is_dir( $this->dir( $id ) ) ) {
            @unlink( $this->berkas_kunci_langkah( $id ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
    }

    /**
     * WP-Cron tiap jam: tukar/pulihkan yang ditinggal dashboard lebih dari
     * DIAM_MACET dipulihkan (site tidak boleh dibiarkan setengah tertukar);
     * area yang tidak disentuh 24 jam dan statusnya boleh dibersihkan dibuang.
     */
    public function cron() {
        if ( ! is_dir( $this->dasar ) ) {
            return;
        }
        foreach ( (array) scandir( $this->dasar ) as $id ) {
            if ( 1 === preg_match( '/^([0-9a-f]{32})\.lock\z/', (string) $id, $m ) ) {
                $this->sapu_kunci_langkah( $m[1] );
                continue;
            }
            if ( ! self::id_sah( $id ) || $this->waktu_habis() ) {
                continue;
            }
            $k      = $this->keadaan( $id );
            $status = $this->status( $k );
            $diubah = null !== $k && isset( $k['diubah'] ) ? (int) $k['diubah'] : (int) @filemtime( $this->dir( $id ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( in_array( $status, array( 'menukar', 'memulihkan' ), true ) ) {
                if ( $diubah < time() - self::DIAM_MACET ) {
                    $this->pulihkan_macet( $id );
                }
                continue;
            }
            if ( $diubah < time() - 86400 && ( null === $k || in_array( $status, self::STATUS_BOLEH_BERSIHKAN, true ) ) ) {
                $this->bersihkan( $id );
            }
        }
    }
}
