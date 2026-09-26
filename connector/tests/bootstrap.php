<?php
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-signing.php';

// Stub WordPress minimal supaya kelas yang menyertakan validasi murni (seperti
// WPMGR_Settings::urai_kunci) dapat diuji tanpa memuat seluruh WordPress.
// Dijaga dengan class_exists/function_exists supaya tidak bentrok dengan
// stub yang sama yang ditambahkan brief Task 17.
if ( ! class_exists( 'WP_Error' ) ) {
    class WP_Error {
        private $errors = array();
        private $data   = array();

        public function __construct( $code = '', $message = '', $data = '' ) {
            if ( '' !== $code ) {
                $this->errors[ $code ][] = $message;
                if ( '' !== $data ) {
                    $this->data[ $code ] = $data;
                }
            }
        }

        // Status HTTP sebuah WP_Error hidup di data-nya; tanpa ini test tidak
        // bisa membedakan 409 dari 500, padahal dashboard mengklasifikasikan
        // keduanya secara berbeda.
        public function get_error_data( $code = '' ) {
            if ( '' === $code ) {
                $code = $this->get_error_code();
            }
            return isset( $this->data[ $code ] ) ? $this->data[ $code ] : null;
        }

        public function get_error_message( $code = '' ) {
            if ( '' === $code ) {
                $code = $this->get_error_code();
            }
            $pesan = isset( $this->errors[ $code ] ) ? $this->errors[ $code ] : array();
            return isset( $pesan[0] ) ? $pesan[0] : '';
        }

        public function get_error_code() {
            $kode = array_keys( $this->errors );
            return isset( $kode[0] ) ? $kode[0] : '';
        }
    }
}

if ( ! function_exists( 'is_wp_error' ) ) {
    function is_wp_error( $thing ) {
        return $thing instanceof WP_Error;
    }
}

// Konstanta format hasil query wpdb, dipakai WPMGR_Events::kumpulkan().
if ( ! defined( 'ARRAY_A' ) ) {
    define( 'ARRAY_A', 'ARRAY_A' );
}

// get_option()/wp_unslash() minimal: cukup untuk kelas yang menyentuhnya
// tanpa memuat WordPress penuh (mis. WPMGR_IP::saat_ini() lewat
// WPMGR_Settings::percayai_xff()). Nilai baliknya sengaja "tidak ada
// pengaturan khusus" (default apa adanya, tanpa unslash sungguhan) --
// cukup untuk diuji, bukan tiruan perilaku WordPress yang lengkap.
if ( ! function_exists( 'get_option' ) ) {
    // Test yang butuh opsi tertentu (mis. versi skema) mengisinya lewat
    // $GLOBALS['wpmgr_test_opsi'] dan wajib mengosongkannya di tearDown().
    function get_option( $name, $default = false ) {
        return isset( $GLOBALS['wpmgr_test_opsi'][ $name ] ) ? $GLOBALS['wpmgr_test_opsi'][ $name ] : $default;
    }
}

if ( ! function_exists( 'wp_unslash' ) ) {
    function wp_unslash( $value ) {
        return is_string( $value ) ? stripslashes( $value ) : $value;
    }
}

// doing_filter() tiruan yang bisa dikendalikan test lewat variabel global,
// dipakai WPMGR_Login::saat_gagal_app() untuk membedakan jalur filter
// 'authenticate' (wp_authenticate_application_password() dipanggil DI
// DALAM filter itu) dari jalur determine_current_user (REST Basic Auth,
// memanggilnya LANGSUNG, bukan lewat filter).
if ( ! function_exists( 'doing_filter' ) ) {
    $GLOBALS['wpmgr_test_doing_filter'] = array();
    function doing_filter( $hook_name = null ) {
        if ( null === $hook_name ) {
            return ! empty( $GLOBALS['wpmgr_test_doing_filter'] );
        }
        return ! empty( $GLOBALS['wpmgr_test_doing_filter'][ $hook_name ] );
    }
}

// Respons REST minimal untuk WPMGR_Staging::respons_biner()/sajikan_biner().
if ( ! class_exists( 'WP_HTTP_Response' ) ) {
    class WP_HTTP_Response {
        public $data;
        public $headers = array();
        public $status  = 200;

        public function __construct( $data = null, $status = 200, $headers = array() ) {
            $this->data    = $data;
            $this->status  = $status;
            $this->headers = $headers;
        }

        public function get_data() {
            return $this->data;
        }

        public function header( $kunci, $nilai, $ganti = true ) {
            $this->headers[ $kunci ] = $nilai;
        }

        public function get_headers() {
            return $this->headers;
        }
    }
}
if ( ! class_exists( 'WP_REST_Response' ) ) {
    class WP_REST_Response extends WP_HTTP_Response {
    }
}

// rest_ensure_response() minimal, dipakai WPMGR_Staging::manifest() dan
// callback REST lain: lolos apa adanya bila sudah WP_Error atau
// WP_REST_Response, selain itu dibungkus. Dipusatkan di sini (bukan di satu
// berkas test) karena dipakai lebih dari satu berkas test Task 3.
if ( ! function_exists( 'rest_ensure_response' ) ) {
    function rest_ensure_response( $response ) {
        if ( is_wp_error( $response ) ) {
            return $response;
        }
        if ( $response instanceof WP_REST_Response ) {
            return $response;
        }
        return new WP_REST_Response( $response );
    }
}

// register_rest_route() tiruan: hanya MENCATAT argumen yang dikirim (kunci
// namespace+route => array metode/callback/permission_callback), dipakai
// FileTest.php untuk memverifikasi bahwa /staging/file didaftarkan dengan
// permission_callback WPMGR_Staging::guard yang sama seperti rute staging
// lain (konteks global: "Setiap route baru punya test akses anonim (401)").
if ( ! function_exists( 'register_rest_route' ) ) {
    $GLOBALS['wpmgr_test_rute'] = array();
    function register_rest_route( $namespace, $route, $args ) {
        $GLOBALS['wpmgr_test_rute'][ $namespace . $route ] = $args;
    }
}

// Stub WordPress minimal dipakai ManifestTest.php dan ManifestRestTest.php
// (dipusatkan di sini, bukan diduplikasi di kedua berkas, supaya urutan
// muat direktori-berbasis PHPUnit -- yang tidak menjamin urutan alfabet
// antar berkas -- tidak pernah membuat salah satu berkas gagal karena
// fungsi ini belum ada).
if ( ! function_exists( 'get_bloginfo' ) ) {
    function get_bloginfo( $apa = '' ) {
        return 'version' === $apa ? '6.5' : '';
    }
}
if ( ! function_exists( 'home_url' ) ) {
    function home_url() {
        return 'https://contoh.test';
    }
}
if ( ! function_exists( 'site_url' ) ) {
    function site_url() {
        return 'https://contoh.test/wp';
    }
}
if ( ! function_exists( 'is_multisite' ) ) {
    function is_multisite() {
        return false;
    }
}

// wpdb tiruan untuk WPMGR_Staging_Manifest::info()/tabel()/pk() -- dipakai
// ManifestTest.php dan ManifestRestTest.php.
if ( ! class_exists( 'WPMGR_FakeWpdbManifest' ) ) {
    final class WPMGR_FakeWpdbManifest {
        public $prefix  = 'wp_';
        public $charset = 'utf8mb4';
        public $jawaban = array();

        public function esc_like( $t ) {
            return addcslashes( $t, '_%\\' );
        }

        public function prepare( $sql ) {
            $args = array_slice( func_get_args(), 1 );
            return vsprintf( str_replace( '%s', "'%s'", $sql ), $args );
        }

        public function get_results( $sql, $format = null ) {
            foreach ( $this->jawaban as $pola => $hasil ) {
                if ( false !== strpos( $sql, $pola ) ) {
                    return $hasil;
                }
            }
            return array();
        }
    }
}

// ABSPATH/WP_CONTENT_DIR nyata: hanya dibutuhkan untuk menguji
// WPMGR_Staging::manifest() (callback REST, ManifestRestTest.php) secara
// langsung -- root()-nya membaca konstanta ABSPATH mentah. Konstanta hanya
// bisa didefinisikan sekali per proses PHP; tidak ada test lain di suite
// ini yang memakainya, jadi aman didefinisikan sekali di sini dan
// dibereskan lewat register_shutdown_function() di akhir proses test.
if ( ! defined( 'ABSPATH' ) ) {
    define( 'ABSPATH', str_replace( '\\', '/', sys_get_temp_dir() ) . '/wpmgr-man-abspath-' . bin2hex( random_bytes( 6 ) ) . '/' );
    @mkdir( ABSPATH . 'wp-content', 0777, true );
    @file_put_contents( ABSPATH . 'index.php', '<?php' );
    register_shutdown_function( function () {
        StagingDasarTest::hapus( rtrim( ABSPATH, '/' ) );
    } );
}
if ( ! defined( 'WP_CONTENT_DIR' ) ) {
    define( 'WP_CONTENT_DIR', rtrim( ABSPATH, '/' ) . '/wp-content' );
}

require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-skema.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-penangkap.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-settings.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-ip.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-login.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-events.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-traffic.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-rest.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-updater.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-selfupdate.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-sso.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-path.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-paket.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-manifest.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-file.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-tabel.php';
