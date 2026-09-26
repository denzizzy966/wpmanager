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
 */
class WPMGR_Staging_Dorong {

    const KEPALA      = "<?php exit; ?>\n";
    const MAKS_UNGGAH = 8454144;
    const KUNCI       = 'wpmgr_dorong_kunci';
    // Ruling F9b (putusan-preflight.md) + instruksi dispatch task ini: kunci
    // dorong basi setelah 2 JAM tanpa aktivitas, BUKAN 24 jam. Ini timer yang
    // berbeda dari pembersihan area sementara oleh cron() (86400 detik,
    // konteks-global.md: "area sementara connector dibersihkan cron setelah
    // 24 jam") -- dorongan yang macet tidak boleh menahan dorongan BARU
    // selama sehari penuh, walau berkas/keadaannya sendiri tetap disimpan
    // untuk diselidiki/dilanjutkan sampai cron menghapusnya 24 jam kemudian.
    const UMUR_KUNCI  = 7200;
    const JENIS       = array( 'berkas', 'rentang', 'sql', 'rencana' );

    protected $akar;
    protected $dasar;
    protected $db;
    protected $tenggat;

    public function __construct( $akar, $dasar, $db, $detik ) {
        $this->akar    = $akar;
        $this->dasar   = rtrim( str_replace( '\\', '/', $dasar ), '/' ) . '/';
        $this->db      = $db;
        $this->tenggat = microtime( true ) + (int) $detik;
    }

    public static function id_sah( $id ) {
        return is_string( $id ) && 1 === preg_match( '/^[0-9a-f]{32}\z/', $id );
    }

    protected function galat( $kode, $pesan, $status ) {
        return WPMGR_Staging::galat( $kode, $pesan, $status );
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

    public function tulis_terlindung( $berkas, $data ) {
        $this->pastikan_dir( dirname( $berkas ) );
        $sementara = $berkas . '.' . bin2hex( random_bytes( 4 ) ) . '.tmp';
        if ( false === @file_put_contents( $sementara, self::KEPALA . $data ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            return false;
        }
        return @rename( $sementara, $berkas ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
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

    protected function sentuh( $id, array $k ) {
        $k['diubah'] = time();
        $this->simpan_keadaan( $id, $k );
        return $k;
    }

    /** INSERT IGNORE + baca ulang (bukan add_option, yang upsert). */
    public function kunci( $id ) {
        $o     = $this->db->opsi();
        $nilai = $id . '|' . time();
        $this->db->kueri( $this->db->siapkan(
            "INSERT IGNORE INTO {$o} (option_name, option_value, autoload) VALUES (%s, %s, 'no')", self::KUNCI, $nilai ) );
        $sekarang = (string) $this->db->nilai( $this->db->siapkan( "SELECT option_value FROM {$o} WHERE option_name = %s", self::KUNCI ) );
        if ( 0 === strpos( $sekarang, $id . '|' ) ) {
            return true;
        }
        $bagian = explode( '|', $sekarang );
        if ( 2 === count( $bagian ) && (int) $bagian[1] < time() - self::UMUR_KUNCI ) {
            // Pemegang lama tidak menyentuh kuncinya 24 jam: direbut secara
            // atomik, hanya bila nilainya masih sama dengan yang kita baca.
            $this->db->kueri( $this->db->siapkan(
                "UPDATE {$o} SET option_value = %s WHERE option_name = %s AND option_value = %s", $nilai, self::KUNCI, $sekarang ) );
            $ulang = (string) $this->db->nilai( $this->db->siapkan( "SELECT option_value FROM {$o} WHERE option_name = %s", self::KUNCI ) );
            if ( 0 === strpos( $ulang, $id . '|' ) ) {
                return true;
            }
        }
        return $this->galat( 'wpmgr_staging_sibuk', 'Dorongan lain sedang berlangsung di site ini.', 409 );
    }

    public function lepas_kunci( $id ) {
        $o = $this->db->opsi();
        $this->db->kueri( $this->db->siapkan(
            "DELETE FROM {$o} WHERE option_name = %s AND option_value LIKE %s", self::KUNCI, $this->db->suka( $id . '|' ) . '%' ) );
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
        $kunci = $this->kunci( $id );
        if ( is_wp_error( $kunci ) ) {
            return $kunci;
        }
        $this->pastikan_dasar();
        $k = $this->keadaan( $id );
        if ( null === $k ) {
            $k = array( 'status' => 'mengunggah', 'dibuat' => time() );
        } elseif ( 'mengunggah' !== $k['status'] ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Dorongan ini sudah melewati tahap unggah.', 409 );
        }
        if ( ! $this->tulis_terlindung( $this->dir( $id ) . 'potongan/' . sprintf( '%06d', $nomor ) . '.php', $data ) ) {
            return $this->galat( 'wpmgr_staging_tulis', 'Potongan tidak dapat disimpan (disk penuh atau izin).', 500 );
        }
        $this->sentuh( $id, $k );
        return array( 'ok' => true, 'nomor' => $nomor, 'sha256' => hash( 'sha256', $data ) );
    }

    public function snapshot_berkas( $paths ) {
        if ( ! is_array( $paths ) || count( $paths ) > 5000 ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Daftar path snapshot tidak sah.', 400 );
        }
        $hasil = array();
        foreach ( array_values( $paths ) as $rel ) {
            if ( ! is_string( $rel ) || ! WPMGR_Staging_Path::boleh_ditulis( $rel ) ) {
                return $this->galat( 'wpmgr_staging_path', 'Path snapshot tidak sah.', 400 );
            }
            $abs = $this->akar . $rel;
            clearstatcache( true, $abs );
            if ( is_link( $abs ) || ! is_file( $abs ) ) {
                $hasil[] = array( 'path' => $rel, 'ada' => false );
                continue;
            }
            $hasil[] = array( 'path' => $rel, 'ada' => true, 'ukuran' => (int) filesize( $abs ), 'mtime' => (int) filemtime( $abs ) );
        }
        return $hasil;
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

    protected function tabel_dengan_awalan( $awalan ) {
        $semua = $this->db->kolom( "SHOW TABLES LIKE '" . $this->db->suka( $awalan ) . "%'" );
        return array_values( array_filter( array_map( 'strval', $semua ), function ( $t ) {
            return 1 === preg_match( '/^[A-Za-z0-9_$]{1,64}\z/', $t );
        } ) );
    }

    /**
     * Catatan Task 3 (dispatch task ini): pada database bersama, prefix
     * yang saling tumpang tindih (mis. site ini 'wp_', site lain di
     * database yang sama 'wpmgr_tmp_lain_') bisa membuat pencarian tanpa
     * prefix site ini menemukan -- dan MENGHAPUS -- tabel sementara MILIK
     * SITE LAIN. $awalan digabung dengan prefix site ini sendiri (bukan
     * dipakai sendirian sebagai 'wpmgr_tmp_%'/'wpmgr_old_%' global)
     * supaya hanya tabel 'wpmgr_tmp_<prefix_site_ini>...' atau
     * 'wpmgr_old_<prefix_site_ini>...' yang pernah disentuh di sini.
     * Tumpang tindih prefix ANTAR site itu sendiri (mis. 'wp_' vs
     * 'wp_abc_') tetap kasus tepi yang diketahui (sama seperti
     * WPMGR_Staging_Manifest::tabel(), Task 3) -- tidak bisa diselesaikan
     * tuntas hanya dari string prefix tanpa daftar tabel otoritatif.
     */
    protected function hapus_tabel( $awalan ) {
        foreach ( $this->tabel_dengan_awalan( $awalan . $this->db->prefix() ) as $t ) {
            $this->db->kueri( "DROP TABLE IF EXISTS `{$t}`" );
        }
    }

    public function bersihkan( $id ) {
        if ( ! self::id_sah( $id ) ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Id dorongan tidak sah.', 400 );
        }
        $k = $this->keadaan( $id );
        if ( null !== $k && 'menukar' === $k['status'] ) {
            return $this->galat( 'wpmgr_staging_sibuk', 'Dorongan sedang diterapkan; pulihkan dulu.', 409 );
        }
        if ( ! $this->hapus_rekursif( rtrim( $this->dir( $id ), '/' ) ) ) {
            return array( 'lagi' => true );
        }
        $this->hapus_tabel( 'wpmgr_tmp_' );
        if ( null !== $k && in_array( $k['status'], array( 'selesai', 'ditukar' ), true ) ) {
            $this->hapus_tabel( 'wpmgr_old_' );
        }
        $this->lepas_kunci( $id );
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
            $diubah = null !== $k && isset( $k['diubah'] ) ? (int) $k['diubah'] : (int) @filemtime( $this->dir( $id ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( $diubah < time() - 86400 && ( null === $k || 'menukar' !== $k['status'] ) ) {
                $this->bersihkan( $id );
            }
        }
    }
}
