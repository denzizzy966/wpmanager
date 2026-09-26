<?php
use PHPUnit\Framework\TestCase;

// Permintaan REST tiruan minimal, dipakai HANYA untuk membuktikan bahwa
// permission_callback yang terdaftar untuk /staging/file (WPMGR_Staging::guard)
// menolak permintaan tanpa tanda tangan HMAC apa pun -- get_header() selalu
// mengembalikan string kosong, meniru permintaan anonim yang tidak pernah
// mengirim header X-Wpmgr-*.
final class WPMGR_FakeRequestFile {
    public function get_header( $nama ) {
        return '';
    }
}

final class FileTest extends TestCase {

    private $akar;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-file-' . bin2hex( random_bytes( 6 ) ) . '/';
        mkdir( $this->akar . 'wp-content/uploads', 0777, true );
        file_put_contents( $this->akar . 'index.php', '<?php // indeks' );
        file_put_contents( $this->akar . 'wp-content/uploads/biner.bin', "\0\xff\x1a" . str_repeat( 'z', 100 ) );
        file_put_contents( $this->akar . 'wp-config.php', '<?php // rahasia' );
        WPMGR_Staging_File::$maks_paket = 8388608;
    }

    protected function tearDown(): void {
        StagingDasarTest::hapus( rtrim( $this->akar, '/' ) );
        $GLOBALS['wpmgr_test_opsi'] = array();
        $GLOBALS['wpmgr_test_rute'] = array();
    }

    public function test_paket_beberapa_berkas(): void {
        $data = WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'index.php', 'wp-content/uploads/biner.bin' ) ) );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( $data );
        $this->assertSame( '<?php // indeks', $bagian[0] );
        $this->assertSame( "\0\xff\x1a" . str_repeat( 'z', 100 ), $bagian[1] );
        $this->assertSame( 'index.php', $meta['berkas'][0]['path'] );
        $this->assertSame( filemtime( $this->akar . 'index.php' ), $meta['berkas'][0]['mtime'] );
        $this->assertArrayNotHasKey( 'hilang', $meta['berkas'][0] );
        $this->assertTrue( $meta['lengkap'] );
    }

    public function test_berkas_yang_hilang_ditandai(): void {
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai(
            WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'sudah-dihapus.php', 'index.php' ) ) )
        );
        $this->assertTrue( $meta['berkas'][0]['hilang'] );
        $this->assertSame( '', $bagian[0] );
        $this->assertSame( '<?php // indeks', $bagian[1] );
        $this->assertTrue( $meta['lengkap'] );
    }

    public function test_path_berbahaya_menolak_seluruh_permintaan(): void {
        foreach ( array( 'wp-config.php', '../index.php', '/etc/passwd', 'wp-content/cache/a' ) as $p ) {
            $hasil = WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'index.php', $p ) ) );
            $this->assertInstanceOf( WP_Error::class, $hasil, $p );
        }
    }

    public function test_body_salah(): void {
        foreach ( array( null, 'x', array(), array( 'berkas' => array() ), array( 'berkas' => array( 5 ) ),
                         array( 'berkas' => array_fill( 0, 2001, 'index.php' ) ),
                         array( 'rentang' => array( 'path' => 'index.php', 'dari' => '0', 'panjang' => 5 ) ),
                         array( 'rentang' => array( 'path' => 'index.php', 'dari' => -1, 'panjang' => 5 ) ),
                         array( 'rentang' => array( 'path' => 'index.php', 'dari' => 0, 'panjang' => 0 ) ),
                         // R4: body ambigu (dua mode sekaligus) ditolak.
                         array( 'berkas' => array( 'index.php' ),
                                'rentang' => array( 'path' => 'index.php', 'dari' => 0, 'panjang' => 5 ) ),
                         // R4: 'berkas' berbentuk objek (kunci bukan 0..n-1), bukan daftar JSON.
                         array( 'berkas' => array( 'x' => 'index.php', 'y' => 'wp-content/uploads/biner.bin' ) ) )
                 as $body ) {
            $hasil = WPMGR_Staging_File::ambil( $this->akar, $body );
            $this->assertInstanceOf( WP_Error::class, $hasil );
            $this->assertSame( 'wpmgr_staging_permintaan', $hasil->get_error_code() );
        }
    }

    // ---- R4: rentang yang PERSIS di batas (dan paket dengan isi PERSIS di
    // batas) harus tetap sukses -- draf awal menjumlah meta+isi ke 413 walau
    // bentuknya sah (lihat docblock kelas: "Draf awal (brief)..."). ----

    public function test_rentang_pas_di_batas_tidak_413(): void {
        $ukuran = WPMGR_Staging_File::$maks_paket;
        file_put_contents( $this->akar . 'wp-content/uploads/besar.bin', str_repeat( 'Q', $ukuran ) );
        $hasil = WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'wp-content/uploads/besar.bin', 'dari' => 0, 'panjang' => $ukuran ) ) );
        $this->assertNotInstanceOf( WP_Error::class, $hasil );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( $hasil );
        $this->assertSame( $ukuran, strlen( $bagian[0] ) );
        $this->assertSame( $ukuran, $meta['berkas'][0]['total'] );
    }

    public function test_paket_pas_di_batas_konten_tidak_413(): void {
        $ukuran = WPMGR_Staging_File::$maks_paket;
        file_put_contents( $this->akar . 'wp-content/uploads/besar.bin', str_repeat( 'Q', $ukuran ) );
        $hasil = WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'wp-content/uploads/besar.bin' ) ) );
        $this->assertNotInstanceOf( WP_Error::class, $hasil );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( $hasil );
        $this->assertTrue( $meta['lengkap'] );
        $this->assertSame( $ukuran, strlen( $bagian[0] ) );
    }

    // ---- R4: berhenti dini pada anggaran isi -- entri yang muat dikirim,
    // sisanya TIDAK, dengan 'lengkap' => false (bukan 413 untuk semuanya). ----

    public function test_berhenti_dini_karena_anggaran_konten(): void {
        WPMGR_Staging_File::$maks_paket = 20;
        $hasil = WPMGR_Staging_File::ambil( $this->akar,
            array( 'berkas' => array( 'index.php', 'wp-content/uploads/biner.bin' ) ) );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( $hasil );
        $this->assertCount( 1, $meta['berkas'] );
        $this->assertFalse( $meta['lengkap'] );
        $this->assertSame( 'index.php', $meta['berkas'][0]['path'] );
        $this->assertSame( '<?php // indeks', $bagian[0] );
    }

    // ---- R4: berhenti dini pada anggaran META -- banyak path panjang,
    // tanpa exception, 'lengkap' => false, jumlah entri < jumlah diminta. ----

    public function test_berhenti_dini_karena_anggaran_meta(): void {
        // Tiga segmen 250 karakter (<= MAKS_SEGMEN) supaya total path (~780
        // karakter, <= MAKS_PANJANG 1024) tetap sah lolos normalisasi(),
        // tapi cukup panjang supaya ~1165 entri saja sudah menabrak 90% dari
        // WPMGR_Staging_Paket::MAKS_META (1 MiB) -- jauh sebelum MAKS_JUMLAH
        // (2000) atau anggaran isi (semuanya berkas HILANG, isi kosong).
        $segmen = str_repeat( 'a', 250 ) . '/' . str_repeat( 'b', 250 ) . '/' . str_repeat( 'c', 250 );
        $daftar = array();
        for ( $i = 0; $i < 1500; $i++ ) {
            $daftar[] = 'wp-content/uploads/' . $segmen . '/' . $i . '.dat';
        }
        $hasil = WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => $daftar ) );
        $this->assertNotInstanceOf( WP_Error::class, $hasil );
        list( $meta, ) = WPMGR_Staging_Paket::urai( $hasil );
        $this->assertFalse( $meta['lengkap'] );
        $this->assertLessThan( 1500, count( $meta['berkas'] ) );
        foreach ( $meta['berkas'] as $b ) {
            $this->assertTrue( $b['hilang'] );
        }
    }

    // ---- R4: berhenti dini pada TENGGAT -- entri pertama tetap terjamin
    // diproses (jaminan kemajuan), sisanya berhenti begitu tenggat lewat. ----

    public function test_berhenti_dini_karena_tenggat(): void {
        file_put_contents( $this->akar . 'wp-content/uploads/ketiga.txt', 'ketiga' );
        $hasil = WPMGR_Staging_File::ambil(
            $this->akar,
            array( 'berkas' => array( 'index.php', 'wp-content/uploads/biner.bin', 'wp-content/uploads/ketiga.txt' ) ),
            microtime( true ) - 1 // tenggat sudah lewat sebelum permintaan dimulai
        );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( $hasil );
        $this->assertCount( 1, $meta['berkas'] );
        $this->assertFalse( $meta['lengkap'] );
        $this->assertSame( 'index.php', $meta['berkas'][0]['path'] );
        $this->assertSame( '<?php // indeks', $bagian[0] );
    }

    // ---- R4: entri PERTAMA yang sendirian melampaui anggaran isi mendapat
    // penanda terlalu_besar, supaya dashboard beralih ke mode 'rentang'. ----

    public function test_berkas_pertama_terlalu_besar_mendapat_penanda(): void {
        WPMGR_Staging_File::$maks_paket = 10;
        $hasil = WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'wp-content/uploads/biner.bin' ) ) );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( $hasil );
        $this->assertTrue( $meta['berkas'][0]['terlalu_besar'] );
        $this->assertSame( 'wp-content/uploads/biner.bin', $meta['berkas'][0]['path'] );
        $this->assertSame( 103, $meta['berkas'][0]['total'] );
        $this->assertSame( '', $bagian[0] );
        $this->assertFalse( $meta['lengkap'] );
    }

    // ---- R4: berkas yang ADA tapi tidak terbaca (izin dicabut) mendapat
    // penanda galat:'baca', bukan galat 500. chmod tidak menegakkan apa pun
    // di Windows, dan tidak menegakkan apa pun bila proses berjalan sebagai
    // root (umum di kontainer Docker) -- keduanya dilewati (skip), bukan
    // dianggap gagal. ----

    public function test_berkas_tidak_terbaca_mendapat_penanda(): void {
        if ( 'WIN' === strtoupper( substr( PHP_OS, 0, 3 ) ) ) {
            $this->markTestSkipped( 'chmod tidak menegakkan izin baca di Windows.' );
        }
        $berkas = $this->akar . 'wp-content/uploads/tidak-terbaca.bin';
        file_put_contents( $berkas, 'rahasia' );
        chmod( $berkas, 0000 );
        if ( false !== @file_get_contents( $berkas ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            chmod( $berkas, 0644 );
            $this->markTestSkipped( 'Proses ini berjalan sebagai root -- izin berkas tidak menegakkan apa pun di sini.' );
        }
        try {
            list( $meta, $bagian ) = WPMGR_Staging_Paket::urai(
                WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'wp-content/uploads/tidak-terbaca.bin' ) ) )
            );
            $this->assertSame( 'baca', $meta['berkas'][0]['galat'] );
            $this->assertSame( '', $bagian[0] );
        } finally {
            chmod( $berkas, 0644 ); // supaya tearDown() bisa menghapusnya di semua OS
        }
    }

    public function test_rentang_berkas_besar(): void {
        $isi = "\0\xff\x1a" . str_repeat( 'z', 100 );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'wp-content/uploads/biner.bin', 'dari' => 2, 'panjang' => 10 ) ) ) );
        $this->assertSame( substr( $isi, 2, 10 ), $bagian[0] );
        $this->assertSame( 2, $meta['berkas'][0]['dari'] );
        $this->assertSame( strlen( $isi ), $meta['berkas'][0]['total'] );

        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'wp-content/uploads/biner.bin', 'dari' => 100, 'panjang' => 50 ) ) ) );
        $this->assertSame( substr( $isi, 100 ), $bagian[0] );

        list( , $bagian ) = WPMGR_Staging_Paket::urai( WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'wp-content/uploads/biner.bin', 'dari' => 500, 'panjang' => 5 ) ) ) );
        $this->assertSame( '', $bagian[0] );
    }

    public function test_rentang_melebihi_batas(): void {
        WPMGR_Staging_File::$maks_paket = 8;
        $hasil = WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'index.php', 'dari' => 0, 'panjang' => 9 ) ) );
        $this->assertSame( 'wpmgr_staging_terlalu_besar', $hasil->get_error_code() );
    }

    // ---- R4: berkas hilang di mode 'rentang' -> paket hilang, bukan 404. ----

    public function test_rentang_berkas_hilang_mengembalikan_paket_hilang(): void {
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'sudah-dihapus.php', 'dari' => 0, 'panjang' => 5 ) ) ) );
        $this->assertTrue( $meta['berkas'][0]['hilang'] );
        $this->assertSame( 0, $meta['berkas'][0]['total'] );
        $this->assertSame( '', $bagian[0] );
    }

    // ---- Setiap route baru punya test akses anonim (401). ----

    public function test_staging_file_terdaftar_dengan_guard_dan_menolak_tanpa_tanda_tangan(): void {
        $GLOBALS['wpmgr_test_rute'] = array();
        WPMGR_Staging::daftarkan_route();
        $this->assertArrayHasKey( 'wpmgr/v1/staging/file', $GLOBALS['wpmgr_test_rute'] );
        $r = $GLOBALS['wpmgr_test_rute']['wpmgr/v1/staging/file'];
        $this->assertSame( 'POST', $r['methods'] );
        $this->assertSame( array( 'WPMGR_Staging', 'file' ), $r['callback'] );
        $this->assertSame( array( 'WPMGR_Staging', 'guard' ), $r['permission_callback'] );

        // Site "terpasang" (site_id/secret ada) supaya penolakan yang diuji
        // sungguh berasal dari ketiadaan tanda tangan, bukan dari
        // "connector belum dipasangkan".
        $GLOBALS['wpmgr_test_opsi']['wpmgr_site_id'] = 'situs-uji';
        $GLOBALS['wpmgr_test_opsi']['wpmgr_secret']  = str_repeat( 'a', 64 );
        $hasil = call_user_func( $r['permission_callback'], new WPMGR_FakeRequestFile() );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( array( 'status' => 401 ), $hasil->get_error_data() );
    }
}
