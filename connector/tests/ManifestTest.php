<?php
use PHPUnit\Framework\TestCase;

// get_bloginfo()/home_url()/site_url()/is_multisite()/rest_ensure_response()
// dan WPMGR_FakeWpdbManifest kini dipusatkan di tests/bootstrap.php (dipakai
// juga oleh ManifestRestTest.php).

final class ManifestTest extends TestCase {

    private $akar;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-man-' . bin2hex( random_bytes( 6 ) ) . '/';
        mkdir( $this->akar, 0777, true );
        WPMGR_Staging_Manifest::$maks_hash          = 52428800;
        WPMGR_Staging_Manifest::$anggaran_hash      = 536870912;
        WPMGR_Staging_Manifest::$maks_bytes_halaman = 6291456;
    }

    protected function tearDown(): void {
        StagingDasarTest::hapus( rtrim( $this->akar, '/' ) );
    }

    private function tulis( $rel, $isi = 'x' ) {
        $abs = $this->akar . $rel;
        if ( ! is_dir( dirname( $abs ) ) ) {
            mkdir( dirname( $abs ), 0777, true );
        }
        return false !== @file_put_contents( $abs, $isi );
    }

    private function jalan_semua( $batas ) {
        $semua  = array();
        $kursor = '';
        for ( $i = 0; $i < 100; $i++ ) {
            $h = WPMGR_Staging_Manifest::jalan( $this->akar, $kursor, $batas, microtime( true ) + 30 );
            foreach ( $h['berkas'] as $b ) {
                $semua[] = $b['path'];
            }
            if ( ! $h['lagi'] ) {
                return $semua;
            }
            $kursor = $h['kursor'];
        }
        $this->fail( 'Paging tidak berhenti.' );
    }

    public function test_menelusuri_dan_mengecualikan(): void {
        foreach ( array( 'index.php', 'wp-config.php', 'debug.log', 'wp-content/cache/a.html',
                         'wp-content/updraft/b.zip', 'wp-content/backups-dup-lite/c.zip',
                         'wp-content/wpmgr-dorong/d/e', 'wp-content/themes/t/style.css',
                         'wp-content/uploads/2026/09/f.jpg' ) as $p ) {
            $this->tulis( $p, 'isi-' . $p );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertFalse( $h['lagi'] );
        $this->assertNull( $h['kursor'] );
        $this->assertSame(
            array( 'index.php', 'wp-content/themes/t/style.css', 'wp-content/uploads/2026/09/f.jpg' ),
            array_column( $h['berkas'], 'path' )
        );
        $this->assertSame( hash( 'sha256', 'isi-index.php' ), $h['berkas'][0]['hash'] );
        $this->assertSame( strlen( 'isi-index.php' ), $h['berkas'][0]['ukuran'] );
        $this->assertIsInt( $h['berkas'][0]['mtime'] );
    }

    public function test_direktori_berakhiran_log_tidak_dikecualikan(): void {
        // Item (5f, review putaran 1): aturan '.log' di
        // WPMGR_Staging_Path::dikecualikan() hanya masuk akal untuk BERKAS;
        // direktori yang kebetulan bernama serupa harus tetap ditelusuri.
        $this->tulis( 'wp-content/aneh.log/dalam.txt' );
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertContains( 'wp-content/aneh.log/dalam.txt', array_column( $h['berkas'], 'path' ) );
    }

    public function test_berkas_besar_tanpa_hash(): void {
        WPMGR_Staging_Manifest::$maks_hash = 5;
        $this->tulis( 'kecil.txt', '12345' );
        $this->tulis( 'besar.bin', '123456' );
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $per_path = array_column( $h['berkas'], 'hash', 'path' );
        $this->assertNull( $per_path['besar.bin'] );
        $this->assertSame( hash( 'sha256', '12345' ), $per_path['kecil.txt'] );
    }

    public function test_paging_mengikuti_urutan_dfs_tanpa_duplikat(): void {
        foreach ( array( 'a/x.txt', 'a/y.txt', 'a-b.txt', 'b.txt', 'c/d/e.txt' ) as $p ) {
            $this->tulis( $p );
        }
        $harapan = array( 'a/x.txt', 'a/y.txt', 'a-b.txt', 'b.txt', 'c/d/e.txt' );
        $this->assertSame( $harapan, $this->jalan_semua( 5000 ) );
        $this->assertSame( $harapan, $this->jalan_semua( 2 ) );
        $this->assertSame( $harapan, $this->jalan_semua( 1 ) );
    }

    public function test_kursor_berkas_yang_sudah_dihapus_tetap_maju(): void {
        foreach ( array( 'a/w.txt', 'a/z.txt', 'b.txt' ) as $p ) {
            $this->tulis( $p );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, 'a/xx.txt', 5000, microtime( true ) + 30 );
        $this->assertSame( array( 'a/z.txt', 'b.txt' ), array_column( $h['berkas'], 'path' ) );
    }

    public function test_kursor_menunjuk_berkas_yang_kini_jadi_direktori(): void {
        // Item (5c, review putaran 1): berkas yang kursor tunjuk (sudah
        // terkirim di halaman sebelumnya) dihapus dan digantikan direktori
        // BERNAMA SAMA di antara dua request. Isi direktori baru itu belum
        // pernah dikirim -- melewatinya (perilaku lama) akan menghilangkan
        // semua berkas di dalamnya dari manifest selamanya.
        $this->tulis( 'a/tandaku.txt' );
        unlink( $this->akar . 'a/tandaku.txt' );
        mkdir( $this->akar . 'a/tandaku.txt', 0777, true );
        file_put_contents( $this->akar . 'a/tandaku.txt/baru.txt', 'x' );
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, 'a/tandaku.txt', 5000, microtime( true ) + 30 );
        $this->assertSame( array( 'a/tandaku.txt/baru.txt' ), array_column( $h['berkas'], 'path' ) );
    }

    public function test_nama_non_ascii_bukan_utf8_dan_panjang(): void {
        $panjang = 'wp-content/uploads/' . str_repeat( 'é', 100 ) . '.txt';
        $this->tulis( 'wp-content/uploads/ü-berkas.txt' );
        $this->tulis( $panjang );
        $this->tulis( "wp-content/uploads/\xff\xfe.txt" );
        // Windows menyimpan nama berkas sebagai UTF-16 dan PHP mengembalikannya
        // sudah dikonversi; hanya di Linux nama bukan UTF-8 benar-benar ada.
        $ada_bukan_utf8 = false;
        foreach ( scandir( $this->akar . 'wp-content/uploads' ) as $n ) {
            $ada_bukan_utf8 = $ada_bukan_utf8 || 1 !== preg_match( '//u', $n );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $path = array_column( $h['berkas'], 'path' );
        $this->assertContains( 'wp-content/uploads/ü-berkas.txt', $path );
        $this->assertContains( $panjang, $path );
        foreach ( $path as $p ) {
            $this->assertSame( 1, preg_match( '//u', $p ) );
        }
        if ( $ada_bukan_utf8 ) {
            $this->assertGreaterThanOrEqual( 1, $h['jumlah_dilewati'] );
            $this->assertSame( 'nama_bukan_utf8', $h['dilewati'][0]['alasan'] );
        }
        // Seluruh hasil tetap bisa dikodekan JSON: satu nama rusak tidak boleh
        // membuat json_encode() gagal dan membungkam seluruh manifest.
        $this->assertNotFalse( json_encode( $h ) );
    }

    public function test_nama_mencapai_batas_segmen_255_byte(): void {
        // RF1 lama hanya menguji 200 byte, jauh di bawah MAKS_SEGMEN (255
        // byte) -- item 5i, fix round 1. Sisi TEPAT DI BATAS (masih sah)
        // diuji lewat penelusuran manifest sungguhan. Sisi MELEWATI batas
        // (256 byte) TIDAK BISA dibuat sebagai berkas sungguhan di disk:
        // filesystem lazim (ext4/NTFS/APFS) sendiri membatasi NAME_MAX di
        // sekitar 255 byte, jadi sisi itu diuji langsung lewat
        // WPMGR_Staging_Path::normalisasi() (fungsi murni, tanpa I/O)
        // alih-alih lewat penelusuran manifest.
        $pas = str_repeat( 'a', 255 );
        if ( ! $this->tulis( 'wp-content/uploads/' . $pas ) ) {
            $this->markTestSkipped( 'Sistem berkas ini tidak mengizinkan nama sepanjang ini.' );
        }
        $h    = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $path = array_column( $h['berkas'], 'path' );
        $this->assertContains( 'wp-content/uploads/' . $pas, $path );

        $lebih = str_repeat( 'a', 256 );
        $this->assertInstanceOf(
            WP_Error::class,
            WPMGR_Staging_Path::normalisasi( 'wp-content/uploads/' . $lebih )
        );
    }

    public function test_nama_titik_dua_atau_akhiran_titik_atau_spasi_dilewati(): void {
        // Item 5i, fix round 1: ':' di tengah nama, atau '.'/spasi di akhir
        // nama, ditolak WPMGR_Staging_Path::normalisasi() (aturan Task 2)
        // dan harus dilaporkan 'path_tidak_sah', tidak pernah didaftar.
        // Hanya benar-benar teruji di Linux -- Windows menolak ':' langsung
        // saat file_put_contents(), dan API Win32 memangkas '.'/spasi akhir
        // sebelum berkas tersimpan (lihat komentar path.php sendiri).
        $kasus   = array( 'aneh:nama.txt', 'akhiran-titik.txt.', 'akhiran-spasi.txt ' );
        $ditulis = array();
        foreach ( $kasus as $nama ) {
            $abs = $this->akar . 'wp-content/uploads/' . $nama;
            if ( ! is_dir( dirname( $abs ) ) ) {
                mkdir( dirname( $abs ), 0777, true );
            }
            if ( false === @file_put_contents( $abs, 'x' ) ) {
                continue;
            }
            if ( in_array( $nama, scandir( dirname( $abs ) ), true ) ) {
                $ditulis[] = $nama;
            }
        }
        if ( empty( $ditulis ) ) {
            $this->markTestSkipped( 'Sistem berkas ini tidak mengizinkan nama semacam ini (kemungkinan Windows).' );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        foreach ( $ditulis as $nama ) {
            $rel       = 'wp-content/uploads/' . $nama;
            $ditemukan = false;
            foreach ( $h['dilewati'] as $d ) {
                if ( $rel === $d['path'] ) {
                    $ditemukan = true;
                    $this->assertSame( 'path_tidak_sah', $d['alasan'], $nama );
                }
            }
            $this->assertTrue( $ditemukan, "Nama '$nama' seharusnya dilaporkan dilewati." );
            $this->assertNotContains( $rel, array_column( $h['berkas'], 'path' ), $nama );
        }
    }

    public function test_direktori_nama_bukan_utf8_dilewati(): void {
        $abs = $this->akar . "wp-content/uploads/\xff\xfe-dir";
        if ( ! @mkdir( $abs, 0777, true ) ) {
            $this->markTestSkipped( 'Tidak bisa membuat direktori dengan nama ini.' );
        }
        file_put_contents( $abs . '/dalam.txt', 'x' );
        $sungguhan_bukan_utf8 = false;
        foreach ( scandir( $this->akar . 'wp-content/uploads' ) as $n ) {
            $sungguhan_bukan_utf8 = $sungguhan_bukan_utf8 || 1 !== preg_match( '//u', $n );
        }
        if ( ! $sungguhan_bukan_utf8 ) {
            $this->markTestSkipped( 'Sistem berkas ini tidak menyimpan nama bukan UTF-8 (kemungkinan Windows).' );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        foreach ( $h['berkas'] as $b ) {
            $this->assertStringNotContainsString( 'dalam.txt', $b['path'] );
        }
        $alasan = array_column( $h['dilewati'], 'alasan' );
        $this->assertContains( 'nama_bukan_utf8', $alasan );
        $this->assertNotFalse( json_encode( $h ) );
    }

    public function test_symlink_dilewati(): void {
        $this->tulis( 'asli.txt' );
        if ( ! @symlink( $this->akar . 'asli.txt', $this->akar . 'tautan.txt' ) ) {
            $this->markTestSkipped( 'Symlink tidak didukung di sistem ini.' );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertSame( array( 'asli.txt' ), array_column( $h['berkas'], 'path' ) );
        $this->assertSame( 'symlink', $h['dilewati'][0]['alasan'] );
    }

    public function test_symlink_direktori_dilewati(): void {
        $this->tulis( 'nyata/dalam.txt' );
        if ( ! @symlink( $this->akar . 'nyata', $this->akar . 'tautan-dir' ) ) {
            $this->markTestSkipped( 'Symlink tidak didukung di sistem ini.' );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertSame( array( 'nyata/dalam.txt' ), array_column( $h['berkas'], 'path' ) );
        $ditemukan = false;
        foreach ( $h['dilewati'] as $d ) {
            if ( 'symlink' === $d['alasan'] && 'tautan-dir' === $d['path'] ) {
                $ditemukan = true;
            }
        }
        $this->assertTrue( $ditemukan );
    }

    public function test_anggaran_hash_dan_tenggat_menjamin_kemajuan(): void {
        $this->tulis( 'a.txt', '1234' );
        $this->tulis( 'b.txt', '1234' );
        WPMGR_Staging_Manifest::$anggaran_hash = 5;
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertSame( array( 'a.txt' ), array_column( $h['berkas'], 'path' ) );
        $this->assertTrue( $h['lagi'] );
        $this->assertSame( 'a.txt', $h['kursor'] );

        WPMGR_Staging_Manifest::$anggaran_hash = 536870912;
        $lewat = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) - 1 );
        $this->assertCount( 1, $lewat['berkas'] );
        $this->assertTrue( $lewat['lagi'] );
    }

    public function test_tenggat_maju_meski_semua_entri_pertama_dilewati(): void {
        // Item 1, fix round 1, DIPERKUAT fix round 2: sebelum perbaikan,
        // tenggat/kemajuan hanya diperiksa setelah ADA berkas yang
        // terkirim -- direktori berisi banyak entri yang dilewati (di
        // sini: 20 berkas *.log yang dikecualikan) tanpa satu pun berkas
        // asli bisa berjalan lewat batas waktu tanpa kursor pernah maju,
        // dan halaman berikutnya mengulang persis dari awal (macet
        // selamanya). Diperkuat (review putaran 2): SETIAP halaman
        // dipanggil dengan tenggat yang SUDAH LEWAT (bukan hanya halaman
        // pertama), dan setiap kursor harus benar-benar berbeda dari
        // kursor sebelumnya -- membuktikan paging tidak pernah berputar di
        // tempat yang sama, bukan hanya maju sekali lalu macet.
        for ( $i = 1; $i <= 20; $i++ ) {
            $this->tulis( sprintf( 'f%02d-debug.log', $i ) );
        }
        $this->tulis( 'z-asli.txt', 'isi' );

        $kursor    = '';
        $riwayat   = array();
        $terkumpul = array();
        for ( $i = 0; $i <= 20; $i++ ) {
            $h = WPMGR_Staging_Manifest::jalan( $this->akar, $kursor, 5000, microtime( true ) - 1 );
            foreach ( $h['berkas'] as $b ) {
                $terkumpul[] = $b['path'];
            }
            if ( ! $h['lagi'] ) {
                $this->assertSame( array( 'z-asli.txt' ), $terkumpul );
                return;
            }
            $this->assertNotSame( $kursor, $h['kursor'], "Kursor tidak maju dari '$kursor' pada iterasi $i." );
            $riwayat[] = $h['kursor'];
            $kursor    = $h['kursor'];
        }
        $this->assertSame( count( $riwayat ), count( array_unique( $riwayat ) ), 'Kursor berulang -- paging berputar di tempat.' );
        $this->fail( 'Paging tidak selesai dalam 21 halaman.' );
    }

    public function test_kursor_tidak_macet_pada_direktori_dikecualikan(): void {
        // Item 1, fix round 2: reproduksi PERSIS dari temuan review --
        // kursor MASUK menunjuk ke sebuah DIREKTORI yang dikecualikan itu
        // sendiri ('wp-content/cache'). Sebelum perbaikan, cabang 5c jatuh
        // ke direktori_dikecualikan()/dikecualikan() dan mencatat ULANG
        // kursor MASUK itu sendiri sebagai kandidat kursor KELUAR; tenggat
        // yang sudah lewat lalu berhenti pada entri berikutnya dan
        // mengembalikan kursor KELUAR yang identik dengan kursor MASUK --
        // paging tidak pernah maju (dashboard memanggil ulang dengan
        // kursor yang sama, selamanya).
        $this->tulis( 'wp-content/cache/a.html' );
        $this->tulis( 'wp-content/m.txt' );
        $this->tulis( 'wp-content/z.txt' );

        $h = WPMGR_Staging_Manifest::jalan( $this->akar, 'wp-content/cache', 5000, microtime( true ) - 1 );
        // 'cache' (dikecualikan) tidak lagi mencatat kandidat kursor, jadi
        // 'm.txt' langsung diperiksa (kandidat masih null di sana) dan
        // berhasil dikirim -- tenggat baru berhenti pada 'z.txt' berikutnya.
        $this->assertSame( array( 'wp-content/m.txt' ), array_column( $h['berkas'], 'path' ) );
        $this->assertTrue( $h['lagi'] );
        $this->assertNotSame( 'wp-content/cache', $h['kursor'] );
        $this->assertSame( 'wp-content/m.txt', $h['kursor'] );
    }

    public function test_invarian_kursor_keluar_tidak_pernah_sama_dengan_kursor_masuk(): void {
        // Item 1, fix round 2: invarian umum -- pada penelusuran mana pun
        // dengan tenggat yang selalu lewat, kursor KELUAR halaman tidak
        // pernah sama dengan kursor MASUK yang diberikan ke halaman itu
        // (kalau sama, paging berhenti maju selamanya).
        $this->tulis( 'wp-content/cache/a.html' );
        $this->tulis( 'wp-content/updraft/b.zip' );
        $this->tulis( 'wp-content/themes/t/style.css' );
        $this->tulis( 'wp-content/uploads/2026/09/f.jpg' );
        $this->tulis( 'index.php' );

        $kursor = '';
        for ( $i = 0; $i < 30; $i++ ) {
            $h = WPMGR_Staging_Manifest::jalan( $this->akar, $kursor, 5000, microtime( true ) - 1 );
            if ( $h['lagi'] ) {
                $this->assertNotSame( $kursor, $h['kursor'], "Kursor tidak maju dari '$kursor'." );
            } else {
                return;
            }
            $kursor = $h['kursor'];
        }
        $this->fail( 'Paging tidak berhenti dalam 30 halaman.' );
    }

    public function test_batas_ukuran_halaman_direspons(): void {
        // Item 5a, fix round 1: halaman berhenti sebelum jumlah entri
        // mencapai $batas bila perkiraan ukuran terkode sudah melewati
        // $maks_bytes_halaman -- di sini dijepit sangat kecil untuk diuji.
        WPMGR_Staging_Manifest::$maks_bytes_halaman = 10;
        $this->tulis( 'a.txt', 'x' );
        $this->tulis( 'b.txt', 'x' );
        $this->tulis( 'c.txt', 'x' );
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertTrue( $h['lagi'] );
        $this->assertLessThan( 3, count( $h['berkas'] ) );
    }

    public function test_batas_ukuran_halaman_menghitung_json_sungguhan(): void {
        // Item 2, fix round 2: perkiraan lama (strlen(path) + konstanta)
        // meremehkan ukuran JSON SUNGGUHAN sampai ~3x untuk path berisi
        // banyak karakter non-ASCII -- json_encode()/wp_json_encode()
        // meng-escape tiap karakter seperti itu menjadi '\uXXXX' (6 byte),
        // jauh lebih besar dari 2 byte UTF-8 mentahnya. Dengan 5 nama
        // panjang seperti ini dan batas kecil, perkiraan LAMA tidak akan
        // pernah mencapai batas (lagi: false, salah); perkiraan BARU
        // (encode sungguhan per entri) harus berhenti sebelum kelimanya.
        WPMGR_Staging_Manifest::$maks_bytes_halaman = 2000;
        $nama = str_repeat( 'é', 100 ) . '.txt';
        foreach ( array( 'a-', 'b-', 'c-', 'd-', 'e-' ) as $awalan ) {
            $this->tulis( $awalan . $nama );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertTrue( $h['lagi'] );
        $this->assertLessThan( 5, count( $h['berkas'] ) );
        $this->assertNotNull( $h['kursor'] );
    }

    public function test_batas_dijepit(): void {
        $this->assertSame( 5000, WPMGR_Staging_Manifest::batas( null ) );
        $this->assertSame( 5000, WPMGR_Staging_Manifest::batas( 0 ) );
        $this->assertSame( 5000, WPMGR_Staging_Manifest::batas( 99999 ) );
        $this->assertSame( 20, WPMGR_Staging_Manifest::batas( '20' ) );
    }

    public function test_info_dan_tabel(): void {
        mkdir( rtrim( $this->akar, '/' ) . '/wp-content', 0777, true );
        $wpdb          = new WPMGR_FakeWpdbManifest();
        $wpdb->jawaban = array(
            'SHOW TABLE STATUS' => array(
                array( 'Name' => 'wp_posts', 'Rows' => '12', 'Data_length' => '1000', 'Index_length' => '24', 'Engine' => 'InnoDB' ),
                array( 'Name' => 'wp_tampilan', 'Rows' => null, 'Data_length' => null, 'Index_length' => null, 'Engine' => null, 'Comment' => 'VIEW' ),
                array( 'Name' => 'wp_rusak', 'Rows' => null, 'Data_length' => null, 'Index_length' => null, 'Engine' => null ),
                array( 'Name' => 'wp_bad-name', 'Rows' => '1', 'Data_length' => '1', 'Index_length' => '0', 'Engine' => 'MyISAM' ),
                array( 'Name' => 'lain_posts', 'Rows' => '1', 'Data_length' => '1', 'Index_length' => '0', 'Engine' => 'MyISAM' ),
            ),
            'SHOW KEYS FROM `wp_posts`' => array(
                array( 'Column_name' => 'ID', 'Seq_in_index' => '1' ),
            ),
        );
        $info = WPMGR_Staging_Manifest::info( $wpdb, $this->akar, rtrim( $this->akar, '/' ) . '/wp-content' );
        $this->assertSame( 'wp_', $info['table_prefix'] );
        $this->assertSame( 'https://contoh.test', $info['home'] );
        $this->assertFalse( $info['konten_di_luar'] );
        $this->assertIsBool( $info['unggah_terlalu_kecil'] );
        $this->assertSame( array( array( 'nama' => 'wp_posts', 'baris' => 12, 'ukuran' => 1024,
                                         'mesin' => 'InnoDB', 'pk' => array( 'ID' ) ) ), $info['tabel'] );
        // 'wp_tampilan' (VIEW nyata) tetap dilewati diam-diam; 'wp_rusak'
        // (Engine NULL, BUKAN view) + 'wp_bad-name' + 'lain_posts' (prefix
        // tidak cocok) dihitung -- item 5g, fix round 1.
        $this->assertSame( 3, $info['tabel_dilewati'] );
        $luar = WPMGR_Staging_Manifest::info( $wpdb, $this->akar, sys_get_temp_dir() . '/konten-lain' );
        $this->assertTrue( $luar['konten_di_luar'] );
    }

    public function test_konten_di_luar_symlink_wp_content(): void {
        // Item 3, fix round 1: wp-content yang di-symlink-kan tetap "tampak"
        // di dalam akar menurut strpos berawalan, padahal manifest yang
        // hanya menelusuri ABSPATH tidak pernah melihat isinya lewat
        // symlink itu (penelusuran tidak pernah masuk ke direktori
        // symlink) -- staging jadi dibuat tanpa tema/plugin/unggahan.
        $wpdb  = new WPMGR_FakeWpdbManifest();
        $nyata = sys_get_temp_dir() . '/wpmgr-content-nyata-' . bin2hex( random_bytes( 4 ) );
        mkdir( $nyata, 0777, true );
        try {
            if ( ! @symlink( $nyata, rtrim( $this->akar, '/' ) . '/wp-content' ) ) {
                $this->markTestSkipped( 'Symlink tidak didukung di sistem ini.' );
            }
            $info = WPMGR_Staging_Manifest::info( $wpdb, $this->akar, rtrim( $this->akar, '/' ) . '/wp-content' );
            $this->assertTrue( $info['konten_di_luar'] );
        } finally {
            StagingDasarTest::hapus( $nyata );
        }
    }

    public function test_pk_komposit_berurutan(): void {
        $wpdb = new WPMGR_FakeWpdbManifest();
        $wpdb->jawaban = array( 'SHOW KEYS FROM `wp_x`' => array(
            array( 'Column_name' => 'b', 'Seq_in_index' => '2' ),
            array( 'Column_name' => 'a', 'Seq_in_index' => '1' ),
        ) );
        $this->assertSame( array( 'a', 'b' ), WPMGR_Staging_Manifest::pk( $wpdb, 'wp_x' ) );
    }

    public function test_batas_unggah(): void {
        $this->assertSame( 4194304, WPMGR_Staging_Manifest::batas_unggah( '8M' ) );
        $this->assertSame( 4194304, WPMGR_Staging_Manifest::batas_unggah( '64M' ) );
        $this->assertSame( 1048576, WPMGR_Staging_Manifest::batas_unggah( '2M' ) );
        $this->assertSame( 524288, WPMGR_Staging_Manifest::batas_unggah( '1.5M' ) );
        $this->assertSame( 4194304, WPMGR_Staging_Manifest::batas_unggah( '0' ) );
        $this->assertSame( 2147483648, WPMGR_Staging_Manifest::ke_byte( '2G' ) );

        // Item 4, fix round 1: batas bawah 256 KB tidak lagi dipaksakan bila
        // itu akan melebihi separuh post_max_size (dulu membuat SATU
        // potongan lebih besar dari post_max_size itu sendiri, ditolak PHP
        // di hosting dengan post_max_size < 512 KB).
        $this->assertSame( 51200, WPMGR_Staging_Manifest::batas_unggah( '100K' ) );
        $this->assertTrue( WPMGR_Staging_Manifest::unggah_kecil( '100K' ) );
        $this->assertFalse( WPMGR_Staging_Manifest::unggah_kecil( '2M' ) );
        $this->assertFalse( WPMGR_Staging_Manifest::unggah_kecil( '1.5M' ) );
        $this->assertFalse( WPMGR_Staging_Manifest::unggah_kecil( '0' ) );
    }

    public function test_anggaran_dikurangi_waktu_info(): void {
        // Item 2, fix round 1: waktu yang dipakai info() (SHOW TABLE STATUS
        // + SHOW KEYS per tabel) dikurangi dari anggaran penelusuran
        // berkas, bukan dibiarkan memakan anggaran penuh diam-diam.
        $mulai = microtime( true ) - 0.05;
        $hasil = WPMGR_Staging_Manifest::anggaran_setelah( 20.0, $mulai );
        $this->assertLessThan( 20.0, $hasil );
        $this->assertGreaterThan( 19.0, $hasil );
        // Dijepit minimal 1 detik: info() yang kebetulan lambat tidak boleh
        // membuat anggaran jalan() negatif/nol (jalan() tetap harus
        // sempat memvisit setidaknya satu entri).
        $this->assertSame( 1.0, WPMGR_Staging_Manifest::anggaran_setelah( 2.0, microtime( true ) - 10 ) );
    }

    public function test_akar_bisa_dibaca(): void {
        $this->assertTrue( WPMGR_Staging_Manifest::akar_bisa_dibaca( $this->akar ) );
        $this->assertFalse( WPMGR_Staging_Manifest::akar_bisa_dibaca( $this->akar . 'tidak-ada/' ) );
    }
}
