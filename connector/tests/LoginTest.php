<?php
use PHPUnit\Framework\TestCase;

// Stub minimal untuk jalur saat_user_baru() dan WPMGR_Settings::pastikan_user().
// wp_insert_user() tiruan memanggil saat_user_baru() secara sinkron, persis
// seperti hook user_register di WordPress sungguhan (wp-includes/user.php).
if ( ! defined( 'WPMGR_USER_LOGIN' ) ) {
    define( 'WPMGR_USER_LOGIN', 'wpmgr' );
}
if ( ! function_exists( 'get_userdata' ) ) {
    function get_userdata( $user_id ) {
        return isset( $GLOBALS['wpmgr_test_user'][ $user_id ] ) ? $GLOBALS['wpmgr_test_user'][ $user_id ] : false;
    }
}
if ( ! function_exists( 'get_user_by' ) ) {
    function get_user_by( $field, $value ) {
        return false;
    }
}
if ( ! function_exists( 'wp_generate_password' ) ) {
    function wp_generate_password( $length = 12, $special = true, $extra = false ) {
        return str_repeat( 'a', $length );
    }
}
if ( ! function_exists( 'wp_insert_user' ) ) {
    function wp_insert_user( $data ) {
        $id                                  = 42;
        $GLOBALS['wpmgr_test_user'][ $id ]   = (object) array(
            'ID'         => $id,
            'user_login' => $data['user_login'],
            'roles'      => array( $data['role'] ),
        );
        WPMGR_Login::saat_user_baru( $id );
        return $id;
    }
}

/**
 * wpdb tiruan minimal untuk menguji jalur tulis(): tanpa DB sungguhan, hanya
 * mencatat query yang "dikirim" dan mensimulasikan rows_affected/COUNT(*)
 * berdasarkan kombinasi (jam,ip,username,jalur) yang sudah "ada", supaya jalur
 * UPDATE-dulu-baru-INSERT dan batas per jam bisa diuji tanpa MySQL. Pola sama
 * seperti WPMGR_FakeWpdbPenangkap di PenangkapTest.php.
 */
final class WPMGR_FakeWpdbLogin {
    public $prefix            = 'wp_';
    public $rows_affected     = 0;
    public $queries           = array();
    public $inserted          = array();
    public $get_var_panggilan = 0;

    private $ada;
    private $jumlah_per_jam;
    private $tersambung;

    public function __construct( array $ada = array(), array $jumlah_per_jam = array(), $tersambung = true ) {
        $this->ada            = $ada;
        $this->jumlah_per_jam = $jumlah_per_jam;
        $this->tersambung     = $tersambung;
    }

    public function check_connection( $allow_bail = true ) {
        return $this->tersambung;
    }

    public function suppress_errors( $suppress = true ) {
        return true;
    }

    public function insert( $table, $data ) {
        $this->inserted[] = $data;
        return 1;
    }

    public function prepare( $sql ) {
        return array( 'sql' => $sql, 'args' => array_slice( func_get_args(), 1 ) );
    }

    public function get_var( $disiapkan ) {
        $this->get_var_panggilan++;
        $jam = $disiapkan['args'][0];
        return isset( $this->jumlah_per_jam[ $jam ] ) ? $this->jumlah_per_jam[ $jam ] : 0;
    }

    public function query( $disiapkan ) {
        $this->queries[] = $disiapkan;
        if ( false !== strpos( $disiapkan['sql'], 'UPDATE' ) ) {
            // Kunci (jam,ip,username,jalur) SELALU 4 argumen TERAKHIR di
            // klausa WHERE, apa pun jumlah argumen SET di depannya (kolom
            // user_agent NULL memakai fragmen literal, bukan placeholder,
            // sehingga argumennya sendiri hilang dari daftar).
            $empat = array_slice( $disiapkan['args'], -4 );
            $kunci = implode( '|', $empat );
            $this->rows_affected = isset( $this->ada[ $kunci ] ) ? 1 : 0;
        } elseif ( false !== strpos( $disiapkan['sql'], 'INSERT' ) ) {
            // Kunci (jam,ip,username,jalur) SELALU 4 argumen PERTAMA di
            // VALUES, sebelum jumlah/user_agent/diubah -- posisinya stabil
            // terlepas dari NULL-nya user_agent.
            $kunci                        = implode( '|', array_slice( $disiapkan['args'], 0, 4 ) );
            $this->ada[ $kunci ]         = true;
            $jam                         = $disiapkan['args'][0];
            $this->jumlah_per_jam[ $jam ] = ( isset( $this->jumlah_per_jam[ $jam ] ) ? $this->jumlah_per_jam[ $jam ] : 0 ) + 1;
            $this->rows_affected         = 1;
        }
        return true;
    }
}

final class LoginTest extends TestCase {

    protected function tearDown(): void {
        WPMGR_Login::reset_untuk_test();
        unset( $GLOBALS['wpdb'] );
        unset( $_SERVER['PHP_AUTH_USER'] );
        $GLOBALS['wpmgr_test_user'] = array();
        $GLOBALS['wpmgr_test_doing_filter'] = array();
    }

    public function test_prioritas_jalur(): void {
        $this->assertSame( 'form', WPMGR_Login::jalur( array() ) );
        $this->assertSame( 'rest', WPMGR_Login::jalur( array( 'rest' => true ) ) );
        $this->assertSame( 'xmlrpc', WPMGR_Login::jalur( array( 'xmlrpc' => true, 'rest' => true ) ) );
        $this->assertSame( 'app_password', WPMGR_Login::jalur( array( 'app_password' => true, 'xmlrpc' => true ) ) );
    }

    public function test_jam_dibulatkan_ke_bawah(): void {
        $this->assertSame( 1790064000, WPMGR_Login::jam_dari( 1790067599 ) );
        $this->assertSame( 1790064000, WPMGR_Login::jam_dari( 1790064000 ) );
    }

    public function test_percobaan_berulang_menjadi_satu_baris(): void {
        $buffer = array();
        for ( $i = 0; $i < 500; $i++ ) {
            WPMGR_Login::tambah_gagal( $buffer, 1790064000, '198.51.100.7', 'admin', 'xmlrpc', 'curl/8.0' );
        }
        WPMGR_Login::tambah_gagal( $buffer, 1790064000, '198.51.100.7', 'editor', 'xmlrpc', 'curl/8.0' );
        $this->assertCount( 2, $buffer );
        $baris = $buffer[ WPMGR_Login::kunci_gagal( 1790064000, '198.51.100.7', 'admin', 'xmlrpc' ) ];
        $this->assertSame( 500, $baris['jumlah'] );
        $this->assertSame( 'curl/8.0', $baris['user_agent'] );
    }

    public function test_potong_aman_multibyte(): void {
        $this->assertSame( 'ééé', WPMGR_Login::potong( 'éééé', 3 ) );
    }

    // --- Fix round 1, temuan #1: byte UTF-8 tak valid tak boleh lolos -----
    //
    // mb_substr() meloloskan byte tak valid APA ADANYA pada PHP <=8.2 (baru
    // di 8.3 mb_substr sendiri mengganti byte rusak dengan '?', jadi RED
    // untuk temuan ini hanya nyata di bawah PHP 8.3 -- lihat verifikasi
    // docker php:7.4-cli di laporan). String yang masih memuat byte tak
    // valid membuat wpdb::query()/insert() MENOLAK seluruh query
    // (class-wpdb.php:2243-2259 dan 2828-2855) -- baris riwayat login hilang
    // tanpa jejak sama sekali.

    public function test_potong_membuang_byte_tunggal_tak_valid(): void {
        $hasil = WPMGR_Login::potong( "\xff", 255 );
        $this->assertTrue( mb_check_encoding( $hasil, 'UTF-8' ) );
    }

    public function test_potong_membuang_byte_tak_valid_di_tengah_tetap_simpan_yang_valid(): void {
        $hasil = WPMGR_Login::potong( "ab\xff\xfecd", 255 );
        $this->assertTrue( mb_check_encoding( $hasil, 'UTF-8' ) );
        $this->assertStringContainsString( 'ab', $hasil );
        $this->assertStringContainsString( 'cd', $hasil );
    }

    public function test_naik_ke_admin(): void {
        $this->assertTrue( WPMGR_Login::naik_ke_admin( 'administrator', array( 'editor' ) ) );
        // old_roles kosong = user baru; dicatat oleh user_register sebagai admin_baru.
        $this->assertFalse( WPMGR_Login::naik_ke_admin( 'administrator', array() ) );
        $this->assertFalse( WPMGR_Login::naik_ke_admin( 'administrator', array( 'administrator' ) ) );
        $this->assertFalse( WPMGR_Login::naik_ke_admin( 'editor', array( 'author' ) ) );
    }

    // --- Dedup kejadian administrator (ruling controller atas Task 13) ----
    //
    // WP_User::set_role() (wp-includes/class-wp-user.php:651-666) memicu
    // add_user_role LALU set_user_role untuk SATU transisi peran yang sama.
    // Tanpa dedup, mengait keduanya secara independen (seperti brief semula)
    // mencatat satu kejadian dua kali. gabung_admin() adalah fungsi murni
    // yang menyerap urutan-urutan nyata itu.

    public function test_dedup_admin_set_role_menaikkan_user_lama_jadi_satu_baris(): void {
        // (a) Skenario nyata: user lama dipromosikan lewat set_role().
        // add_user_role memicu duluan (class-wp-user.php:653), lalu
        // set_user_role (class-wp-user.php:666) -- keduanya 'jadi_admin'
        // di sisi kita karena old_roles tidak kosong.
        $buffer = array();
        WPMGR_Login::gabung_admin( $buffer, 42, array( 'jenis' => 'jadi_admin', 'waktu' => 1 ) );
        WPMGR_Login::gabung_admin( $buffer, 42, array( 'jenis' => 'jadi_admin', 'waktu' => 2 ) );
        $this->assertCount( 1, $buffer );
        $this->assertSame( 'jadi_admin', $buffer[42]['jenis'] );
    }

    public function test_dedup_admin_baru_menang_atas_jadi_admin_palsu_dari_add_user_role(): void {
        // (b) Skenario nyata: user BARU dibuat langsung administrator.
        // add_user_role memicu tanpa tahu old_roles kosong (mencatat
        // 'jadi_admin' palsu); set_user_role menerima old_roles kosong jadi
        // saat_role_diset() TIDAK memanggil gabung_admin() sama sekali
        // (naik_ke_admin() bernilai false); lalu user_register memicu
        // 'admin_baru' yang wajib menang menimpa 'jadi_admin' palsu itu.
        $buffer = array();
        WPMGR_Login::gabung_admin( $buffer, 7, array( 'jenis' => 'jadi_admin', 'waktu' => 1 ) );
        WPMGR_Login::gabung_admin( $buffer, 7, array( 'jenis' => 'admin_baru', 'waktu' => 2 ) );
        $this->assertCount( 1, $buffer );
        $this->assertSame( 'admin_baru', $buffer[7]['jenis'] );
    }

    public function test_dedup_admin_jadi_admin_tidak_pernah_menimpa_admin_baru(): void {
        // Urutan terbalik dari (b): pastikan aturannya arah-tunggal, bukan
        // "yang terakhir menang".
        $buffer = array();
        WPMGR_Login::gabung_admin( $buffer, 9, array( 'jenis' => 'admin_baru', 'waktu' => 1 ) );
        WPMGR_Login::gabung_admin( $buffer, 9, array( 'jenis' => 'jadi_admin', 'waktu' => 2 ) );
        $this->assertCount( 1, $buffer );
        $this->assertSame( 'admin_baru', $buffer[9]['jenis'] );
    }

    public function test_dedup_admin_add_user_role_sendirian_tetap_tercatat(): void {
        // (c) add_role() berdiri sendiri (tanpa set_role()) tidak memicu
        // set_user_role sama sekali -- add_user_role adalah SATU-SATUNYA
        // jalur yang menangkapnya, dan itu tetap harus tercatat.
        $buffer = array();
        WPMGR_Login::gabung_admin( $buffer, 3, array( 'jenis' => 'jadi_admin', 'waktu' => 1 ) );
        $this->assertCount( 1, $buffer );
        $this->assertSame( 'jadi_admin', $buffer[3]['jenis'] );
    }

    public function test_dedup_admin_user_berbeda_tidak_saling_memengaruhi(): void {
        $buffer = array();
        WPMGR_Login::gabung_admin( $buffer, 1, array( 'jenis' => 'jadi_admin' ) );
        WPMGR_Login::gabung_admin( $buffer, 2, array( 'jenis' => 'admin_baru' ) );
        $this->assertCount( 2, $buffer );
        $this->assertSame( 'jadi_admin', $buffer[1]['jenis'] );
        $this->assertSame( 'admin_baru', $buffer[2]['jenis'] );
    }

    // --- Fix round 1, temuan #4: role dipotong ke panjang skema (60) -----

    public function test_role_dipotong_ke_panjang_skema(): void {
        $user = (object) array( 'user_login' => 'siapa', 'roles' => array( str_repeat( 'x', 100 ) ) );
        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::catat_berhasil( $user, 'form' );
        $baris = array_values( WPMGR_Login::berhasil_untuk_test() );
        $this->assertCount( 1, $baris );
        $this->assertSame( 60, strlen( $baris[0]['role'] ) );
    }

    public function test_role_null_dipertahankan_saat_user_tanpa_peran(): void {
        // Kolom role mengizinkan NULL -- user tanpa peran sama sekali wajib
        // tetap NULL, bukan '' hasil pemotongan string kosong.
        $user = (object) array( 'user_login' => 'siapa', 'roles' => array() );
        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::catat_berhasil( $user, 'form' );
        $baris = array_values( WPMGR_Login::berhasil_untuk_test() );
        $this->assertNull( $baris[0]['role'] );
    }

    // --- Fix round 1, temuan #2: app_password vs wp_login_failed ----------
    //
    // wp_authenticate_application_password() diperiksa dari dua jalur:
    // - Lewat filter 'authenticate' (wp-includes/user.php:372, dipasang
    //   default-filters.php:518): dipakai wp_authenticate() untuk form,
    //   xmlrpc, dan rest. wp_login_failed TETAP terpicu sesudahnya untuk
    //   kegagalan yang sama (pluggable.php:731) -- mencatat di sini juga
    //   berarti dobel hitung dengan label salah (PHP_AUTH_USER kosong).
    // - Lewat determine_current_user -> wp_validate_application_password
    //   (user.php:521-544, dipasang default-filters.php:522): REST Basic
    //   Auth murni. wp_authenticate() tidak pernah dipanggil di jalur ini,
    //   jadi wp_login_failed TIDAK PERNAH terpicu -- satu-satunya jalur yang
    //   harus dicatat oleh saat_gagal_app().

    public function test_saat_gagal_app_di_dalam_filter_authenticate_tidak_mencatat(): void {
        $GLOBALS['wpmgr_test_doing_filter'] = array( 'authenticate' => true );
        $_SERVER['PHP_AUTH_USER'] = 'admin';
        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::saat_gagal_app( new WP_Error( 'x', 'gagal' ) );
        $this->assertSame( array(), WPMGR_Login::gagal_untuk_test() );
    }

    public function test_saat_gagal_app_di_luar_filter_authenticate_mencatat_satu_baris(): void {
        $GLOBALS['wpmgr_test_doing_filter'] = array();
        $_SERVER['PHP_AUTH_USER'] = 'admin';
        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::saat_gagal_app( new WP_Error( 'x', 'gagal' ) );
        $buffer = array_values( WPMGR_Login::gagal_untuk_test() );
        $this->assertCount( 1, $buffer );
        $this->assertSame( 'admin', $buffer[0]['username'] );
        $this->assertSame( 'app_password', $buffer[0]['jalur'] );
    }

    public function test_wp_login_failed_mencatat_satu_baris_dengan_jalur_benar(): void {
        $GLOBALS['wpmgr_test_doing_filter'] = array();
        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::saat_gagal( 'admin' );
        $buffer = array_values( WPMGR_Login::gagal_untuk_test() );
        $this->assertCount( 1, $buffer );
        $this->assertSame( 'admin', $buffer[0]['username'] );
        $this->assertSame( 'form', $buffer[0]['jalur'] );
    }

    public function test_xmlrpc_app_password_gagal_hanya_tercatat_sekali_lewat_wp_login_failed(): void {
        // Urutan nyata untuk xmlrpc/form/rest: application_password_failed_
        // authentication dipicu DI DALAM filter 'authenticate' lebih dulu,
        // lalu wp_authenticate() memicu wp_login_failed sesudahnya untuk
        // kegagalan yang sama.
        $GLOBALS['wpmgr_test_doing_filter'] = array( 'authenticate' => true );
        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::saat_gagal_app( new WP_Error( 'x', 'gagal' ) );
        WPMGR_Login::saat_gagal( 'admin' );
        $buffer = array_values( WPMGR_Login::gagal_untuk_test() );
        $this->assertCount( 1, $buffer );
        $this->assertSame( 'admin', $buffer[0]['username'] );
    }

    // --- Pola tulis(): check_connection, UPDATE-dulu-baru-INSERT, batas ---

    public function test_tulis_tidak_menulis_saat_koneksi_terputus(): void {
        $wpdb            = new WPMGR_FakeWpdbLogin( array(), array(), false );
        $GLOBALS['wpdb'] = $wpdb;

        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::isi_untuk_test( array(), array(
            array( 'jam' => 1790064000, 'ip' => '198.51.100.7', 'username' => 'admin', 'jalur' => 'xmlrpc', 'jumlah' => 3, 'user_agent' => 'curl/8.0' ),
        ) );
        WPMGR_Login::tulis();

        $this->assertCount( 0, $wpdb->queries );
    }

    public function test_tulis_kombinasi_yang_sudah_ada_hanya_update_tanpa_hitung_ulang(): void {
        $kunci           = '1790064000|198.51.100.7|admin|xmlrpc';
        $wpdb            = new WPMGR_FakeWpdbLogin( array( $kunci => true ) );
        $GLOBALS['wpdb'] = $wpdb;

        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::isi_untuk_test( array(), array(
            array( 'jam' => 1790064000, 'ip' => '198.51.100.7', 'username' => 'admin', 'jalur' => 'xmlrpc', 'jumlah' => 3, 'user_agent' => 'curl/8.0' ),
        ) );
        WPMGR_Login::tulis();

        $this->assertCount( 1, $wpdb->queries );
        $this->assertStringContainsString( 'UPDATE', $wpdb->queries[0]['sql'] );
        $this->assertSame( 0, $wpdb->get_var_panggilan );
    }

    public function test_tulis_kombinasi_baru_update_gagal_lalu_insert_upsert(): void {
        $wpdb            = new WPMGR_FakeWpdbLogin( array(), array( 1790064000 => 5 ) );
        $GLOBALS['wpdb'] = $wpdb;

        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::isi_untuk_test( array(), array(
            array( 'jam' => 1790064000, 'ip' => '198.51.100.7', 'username' => 'admin', 'jalur' => 'xmlrpc', 'jumlah' => 1, 'user_agent' => 'curl/8.0' ),
        ) );
        WPMGR_Login::tulis();

        $this->assertCount( 2, $wpdb->queries );
        $this->assertStringContainsString( 'UPDATE', $wpdb->queries[0]['sql'] );
        $this->assertStringContainsString( 'INSERT', $wpdb->queries[1]['sql'] );
        $this->assertStringContainsString( 'ON DUPLICATE KEY UPDATE', $wpdb->queries[1]['sql'] );
        $this->assertSame( 1, $wpdb->get_var_panggilan );
    }

    public function test_tulis_batas_per_jam_dihitung_sekali_untuk_banyak_baris(): void {
        // Dua kombinasi BARU dan berbeda untuk jam yang sama: COUNT(*)
        // wajib hanya dipanggil sekali untuk jam itu (di-cache di dalam satu
        // pemanggilan tulis()), bukan diulang per baris.
        $wpdb            = new WPMGR_FakeWpdbLogin( array(), array( 1790064000 => 1999 ) );
        $GLOBALS['wpdb'] = $wpdb;

        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::isi_untuk_test( array(), array(
            array( 'jam' => 1790064000, 'ip' => '198.51.100.7', 'username' => 'admin', 'jalur' => 'xmlrpc', 'jumlah' => 1, 'user_agent' => 'a' ),
            array( 'jam' => 1790064000, 'ip' => '198.51.100.8', 'username' => 'root', 'jalur' => 'xmlrpc', 'jumlah' => 1, 'user_agent' => 'b' ),
        ) );
        WPMGR_Login::tulis();

        $this->assertSame( 1, $wpdb->get_var_panggilan );

        $sisipan = array_values( array_filter( $wpdb->queries, function ( $q ) {
            return false !== strpos( $q['sql'], 'INSERT' );
        } ) );
        $this->assertCount( 2, $sisipan );
        // Baris pertama: 1999 < 2000, lolos apa adanya, cache lokal naik ke 2000.
        $this->assertSame( 'admin', $sisipan[0]['args'][2] );
        // Baris kedua: cache lokal sudah 2000 >= batas, dialihkan ke ember anonim.
        $this->assertSame( '', $sisipan[1]['args'][1] );
        $this->assertSame( WPMGR_Login::USERNAME_LAIN, $sisipan[1]['args'][2] );
    }

    public function test_tulis_login_gagal_ua_kosong_disimpan_null_bukan_string_kosong(): void {
        // Fix round 1, temuan #3: kolom user_agent di wpmgr_login_gagal
        // mengizinkan NULL (class-wpmgr-skema.php) dan wpmgr_logins sudah
        // menyimpan NULL sungguhan lewat $wpdb->insert() -- login_gagal
        // wajib konsisten, bukan string kosong ''.
        $wpdb            = new WPMGR_FakeWpdbLogin( array(), array( 1790064000 => 5 ) );
        $GLOBALS['wpdb'] = $wpdb;

        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::isi_untuk_test( array(), array(
            array( 'jam' => 1790064000, 'ip' => '198.51.100.7', 'username' => 'admin', 'jalur' => 'xmlrpc', 'jumlah' => 1, 'user_agent' => null ),
        ) );
        WPMGR_Login::tulis();

        $this->assertStringContainsString( 'user_agent = NULL', $wpdb->queries[0]['sql'] );
        $sisipan = array_values( array_filter( $wpdb->queries, function ( $q ) {
            return false !== strpos( $q['sql'], 'INSERT' );
        } ) );
        $this->assertCount( 1, $sisipan );
        $this->assertStringContainsString( 'VALUES (%d, %s, %s, %s, %d, NULL, %d)', $sisipan[0]['sql'] );
        // Kunci (jam,ip,username,jalur) tetap 4 argumen pertama walau
        // user_agent tak lagi punya argumen (bukti tak ada pergeseran posisi
        // yang salah menyisipkan jumlah sebagai bagian dari kunci).
        $this->assertSame( 1790064000, $sisipan[0]['args'][0] );
        $this->assertSame( 'admin', $sisipan[0]['args'][2] );
    }

    public function test_tulis_menulis_baris_berhasil_dan_admin_tergabung(): void {
        $wpdb            = new WPMGR_FakeWpdbLogin();
        $GLOBALS['wpdb'] = $wpdb;

        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::isi_untuk_test(
            array( array( 'jenis' => 'berhasil', 'username' => 'wpmgr' ) ),
            array(),
            array(
                7 => array( 'jenis' => 'jadi_admin', 'username' => 'promo' ),
                9 => array( 'jenis' => 'admin_baru', 'username' => 'baru' ),
            )
        );
        WPMGR_Login::tulis();

        $this->assertCount( 3, $wpdb->inserted );
        foreach ( $wpdb->inserted as $baris ) {
            $this->assertArrayHasKey( 'diubah', $baris );
        }
    }

    public function test_tulis_mengosongkan_buffer_setelah_menulis(): void {
        $wpdb            = new WPMGR_FakeWpdbLogin();
        $GLOBALS['wpdb'] = $wpdb;

        WPMGR_Login::reset_untuk_test();
        WPMGR_Login::isi_untuk_test( array( array( 'jenis' => 'berhasil', 'username' => 'x' ) ) );
        WPMGR_Login::tulis();
        $this->assertCount( 1, $wpdb->inserted );

        // Panggilan kedua tanpa mengisi ulang buffer: tidak ada tambahan tulis.
        WPMGR_Login::tulis();
        $this->assertCount( 1, $wpdb->inserted );
    }

    public function test_admin_bernama_wpmgr_tetap_tercatat_di_luar_pastikan_user(): void {
        // Siapa pun yang bisa membuat administrator bisa menamainya "wpmgr";
        // pengecualian berdasarkan nama membuat admin itu tak terlihat.
        $GLOBALS['wpmgr_test_user'][7] = (object) array(
            'ID' => 7, 'user_login' => 'wpmgr', 'roles' => array( 'administrator' ),
        );
        WPMGR_Login::saat_user_baru( 7 );

        $admin = WPMGR_Login::admin_untuk_test();
        $this->assertCount( 1, $admin );
        $this->assertSame( 'admin_baru', $admin[7]['jenis'] );
        $this->assertSame( 'wpmgr', $admin[7]['username'] );
    }

    public function test_user_yang_dibuat_pastikan_user_tidak_tercatat(): void {
        $this->assertSame( 42, WPMGR_Settings::pastikan_user() );
        $this->assertSame( array(), WPMGR_Login::admin_untuk_test() );
        $this->assertFalse( WPMGR_Settings::sedang_membuat_user() );
    }
}
