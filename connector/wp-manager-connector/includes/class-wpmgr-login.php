<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Riwayat login untuk menjawab "apakah site ini diserang, atau sudah dibobol".
 *
 * Login gagal diagregasi per jam per (IP, username, jalur): serangan brute
 * force mengirim ribuan request per jam, dan menyimpan per percobaan berarti
 * satu serangan menghasilkan ribuan baris. (Satu request system.multicall
 * sendiri hanya menghasilkan satu kegagalan: sejak WP 4.4 xmlrpc menolak
 * percobaan berikutnya tanpa memanggil wp_authenticate().) Semua kejadian
 * ditampung selama request dan ditulis sekali di hook shutdown.
 *
 * Kejadian administrator (user baru langsung admin, atau naik jabatan)
 * dideduplikasi per user_id, bukan langsung ditambahkan ke baris berhasil:
 * WP_User::set_role() (wp-includes/class-wp-user.php:651-666) memicu
 * add_user_role DAN set_user_role sekaligus untuk SATU perubahan peran yang
 * sama, dan wp_insert_user() (wp-includes/user.php:2659-2711) memanggil
 * set_role() sebelum user_register untuk user baru -- tanpa dedup, satu
 * kejadian tercatat dua baris (lihat gabung_admin()).
 */
class WPMGR_Login {

    const BATAS_GAGAL_PER_JAM = 2000;
    const USERNAME_LAIN       = '(lainnya)';

    private static $berhasil = array();
    private static $gagal    = array();
    private static $admin    = array();

    public static function pasang() {
        add_action( 'wp_login', array( __CLASS__, 'saat_berhasil' ), 10, 2 );
        add_action( 'wp_login_failed', array( __CLASS__, 'saat_gagal' ), 10, 1 );
        add_action( 'application_password_failed_authentication', array( __CLASS__, 'saat_gagal_app' ), 10, 1 );
        add_action( 'user_register', array( __CLASS__, 'saat_user_baru' ), 10, 1 );
        add_action( 'set_user_role', array( __CLASS__, 'saat_role_diset' ), 10, 3 );
        add_action( 'add_user_role', array( __CLASS__, 'saat_role_ditambah' ), 10, 2 );
        add_action( 'shutdown', array( __CLASS__, 'tulis' ), 1 );
    }

    // ---- fungsi murni ---------------------------------------------------

    public static function jalur( array $konteks ) {
        if ( ! empty( $konteks['app_password'] ) ) {
            return 'app_password';
        }
        if ( ! empty( $konteks['xmlrpc'] ) ) {
            return 'xmlrpc';
        }
        if ( ! empty( $konteks['rest'] ) ) {
            return 'rest';
        }
        return 'form';
    }

    public static function jam_dari( $ts ) {
        return (int) $ts - ( (int) $ts % 3600 );
    }

    public static function kunci_gagal( $jam, $ip, $username, $jalur ) {
        return $jam . '|' . $ip . '|' . $username . '|' . $jalur;
    }

    public static function tambah_gagal( array &$buffer, $jam, $ip, $username, $jalur, $ua ) {
        $k = self::kunci_gagal( $jam, $ip, $username, $jalur );
        if ( isset( $buffer[ $k ] ) ) {
            $buffer[ $k ]['jumlah']++;
            return;
        }
        $buffer[ $k ] = array(
            'jam' => $jam, 'ip' => $ip, 'username' => $username, 'jalur' => $jalur,
            'jumlah' => 1, 'user_agent' => $ua,
        );
    }

    public static function potong( $teks, $n ) {
        $teks = (string) $teks;
        // Byte tak valid UTF-8 (mis. header User-Agent yang dipalsukan
        // penyerang) lolos APA ADANYA lewat mb_substr() -- baru diganti '?'
        // oleh mb_substr() sendiri sejak PHP 8.3. String hasil mb_substr()
        // yang masih memuat byte tak valid membuat wpdb::query()/insert()
        // MENOLAK seluruh query (strip_invalid_text_from_query(),
        // class-wpdb.php:2243-2259 dan 2828-2855) -- baris riwayat login itu
        // lalu hilang tanpa jejak, tanpa error apa pun yang tercatat. Byte
        // tak valid harus disingkirkan di sini, sebelum mb_substr(), apa pun
        // versi PHP-nya.
        $teks = function_exists( 'mb_scrub' ) ? mb_scrub( $teks, 'UTF-8' ) : wp_check_invalid_utf8( $teks, true );
        return function_exists( 'mb_substr' ) ? mb_substr( $teks, 0, $n ) : substr( $teks, 0, $n );
    }

    public static function naik_ke_admin( $role, $old_roles ) {
        $lama = (array) $old_roles;
        return 'administrator' === $role && ! empty( $lama ) && ! in_array( 'administrator', $lama, true );
    }

    /**
     * Menggabungkan satu kejadian administrator ke buffer bertingkat user_id,
     * dengan prioritas 'admin_baru' > 'jadi_admin'. set_role() memicu
     * add_user_role lalu set_user_role untuk transisi yang sama (keduanya
     * 'jadi_admin' di sisi kita -- hasilnya melebur jadi satu baris tanpa
     * masalah). Untuk user baru yang langsung dibuat administrator,
     * add_user_role tetap memicu 'jadi_admin' palsu (ia tak menerima
     * $old_roles untuk tahu usernya baru), tapi user_register yang menyusul
     * dengan 'admin_baru' menang dan menimpanya -- itulah sebabnya
     * 'admin_baru' harus selalu menang, tidak peduli urutan kedatangan.
     */
    public static function gabung_admin( array &$buffer, $user_id, array $baris ) {
        if ( isset( $buffer[ $user_id ] ) && 'admin_baru' === $buffer[ $user_id ]['jenis']
            && 'admin_baru' !== $baris['jenis'] ) {
            return;
        }
        $buffer[ $user_id ] = $baris;
    }

    // ---- konteks request ------------------------------------------------

    private static function konteks_sekarang( $app = false ) {
        return array(
            'app_password' => $app,
            'xmlrpc'       => defined( 'XMLRPC_REQUEST' ) && XMLRPC_REQUEST,
            'rest'         => defined( 'REST_REQUEST' ) && REST_REQUEST,
        );
    }

    private static function ua() {
        $ua = isset( $_SERVER['HTTP_USER_AGENT'] ) ? self::potong( wp_unslash( $_SERVER['HTTP_USER_AGENT'] ), 255 ) : '';
        return '' === $ua ? null : $ua;
    }

    private static function role_utama( $user ) {
        $roles = isset( $user->roles ) ? (array) $user->roles : array();
        // null (bukan '') dipertahankan saat user tak berperan sama sekali:
        // kolom role di skema mengizinkan NULL, dan memotong null lewat
        // potong() akan salah mengubahnya jadi string kosong.
        return empty( $roles ) ? null : self::potong( (string) reset( $roles ), 60 );
    }

    private static function aktif() {
        return ! WPMGR_Skema::monitoring_mati();
    }

    // Jalur/konteks direkam SAAT kejadian terjadi, bukan ditunda ke shutdown:
    // keduanya tak berubah dalam satu request yang sama saat ini, tapi
    // membangun barisnya di titik pemicu (bukan di titik tulis) membuat itu
    // tetap benar bila suatu saat konteksnya bisa berbeda per pemicu.
    private static function susun_baris( $jenis, $user, $jalur ) {
        $ip = WPMGR_IP::saat_ini();
        return array(
            'waktu'            => time(),
            'jenis'            => $jenis,
            'username'         => self::potong( $user->user_login, 60 ),
            'role'             => self::role_utama( $user ),
            'ip'               => $ip['ip'],
            'lewat_cloudflare' => $ip['lewat_cloudflare'] ? 1 : 0,
            'user_agent'       => self::ua(),
            'jalur'            => $jalur,
        );
    }

    // ---- hook -----------------------------------------------------------

    public static function catat_berhasil( $user, $jalur ) {
        try {
            if ( self::aktif() && is_object( $user ) && isset( $user->user_login ) ) {
                self::$berhasil[] = self::susun_baris( 'berhasil', $user, $jalur );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function saat_berhasil( $user_login, $user = null ) {
        self::catat_berhasil( $user, self::jalur( self::konteks_sekarang() ) );
    }

    private static function tambah_gagal_sekarang( $username, $jalur ) {
        $ip = WPMGR_IP::saat_ini();
        self::tambah_gagal( self::$gagal, self::jam_dari( time() ), (string) $ip['ip'],
            self::potong( $username, 60 ), $jalur, self::ua() );
    }

    public static function saat_gagal( $username ) {
        try {
            if ( ! self::aktif() ) {
                return;
            }
            self::tambah_gagal_sekarang( (string) $username, self::jalur( self::konteks_sekarang() ) );
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function saat_gagal_app( $error ) {
        try {
            if ( ! self::aktif() ) {
                return;
            }
            // wp_authenticate_application_password() diperiksa dari DUA jalur
            // berbeda. Lewat filter 'authenticate' (wp-includes/user.php:372,
            // dipasang wp-includes/default-filters.php:518) -- dipakai form,
            // xmlrpc, dan rest lewat wp_authenticate() -- wp_login_failed
            // TETAP terpicu sesudahnya untuk kegagalan yang sama (pluggable.
            // php:731), dengan username asli dan jalur yang benar; mencatat
            // di sini juga berarti satu kegagalan terhitung dua kali dengan
            // label yang salah (PHP_AUTH_USER kosong untuk xmlrpc/form).
            // Lewat determine_current_user -> wp_validate_application_password
            // (wp-includes/user.php:521-544, dipasang default-filters.php:522)
            // -- REST Basic Auth murni -- wp_authenticate() tidak pernah
            // dipanggil, jadi wp_login_failed TIDAK PERNAH terpicu; itu
            // satu-satunya jalur yang harus dicatat di sini. doing_filter()
            // membedakan keduanya: hanya true saat benar-benar berada di
            // dalam apply_filters('authenticate', ...).
            if ( doing_filter( 'authenticate' ) ) {
                return;
            }
            $username = isset( $_SERVER['PHP_AUTH_USER'] ) ? (string) wp_unslash( $_SERVER['PHP_AUTH_USER'] ) : '(tidak diketahui)';
            self::tambah_gagal_sekarang( $username, 'app_password' );
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    private static function catat_admin( $user_id, $jenis ) {
        $user = get_userdata( $user_id );
        // User wpmgr dibuat connector sendiri saat pairing; mencatatnya membuat
        // setiap site baru langsung berstatus merah di dashboard.
        if ( ! $user || WPMGR_USER_LOGIN === $user->user_login ) {
            return;
        }
        $baris = self::susun_baris( $jenis, $user, self::jalur( self::konteks_sekarang() ) );
        self::gabung_admin( self::$admin, $user_id, $baris );
    }

    public static function saat_user_baru( $user_id ) {
        try {
            $user = self::aktif() ? get_userdata( $user_id ) : false;
            if ( $user && in_array( 'administrator', (array) $user->roles, true ) ) {
                self::catat_admin( $user_id, 'admin_baru' );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function saat_role_diset( $user_id, $role, $old_roles ) {
        try {
            if ( self::aktif() && self::naik_ke_admin( $role, $old_roles ) ) {
                self::catat_admin( $user_id, 'jadi_admin' );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function saat_role_ditambah( $user_id, $role ) {
        try {
            if ( self::aktif() && 'administrator' === $role ) {
                self::catat_admin( $user_id, 'jadi_admin' );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function tulis() {
        try {
            if ( ( empty( self::$berhasil ) && empty( self::$gagal ) && empty( self::$admin ) ) || ! self::aktif() ) {
                return;
            }
            global $wpdb;
            // Koneksi yang sudah putus membuat wpdb::bail() memanggil dead_db(),
            // yang berakhir di wp_die() -- dipanggil dari dalam fungsi shutdown
            // kita sendiri. Diam saja; request berikutnya mencoba lagi. (Pola
            // sama seperti WPMGR_Penangkap::tulis().)
            if ( ! $wpdb->check_connection( false ) ) {
                return;
            }
            $p        = $wpdb->prefix;
            $sekarang = time();
            $lama     = $wpdb->suppress_errors( true );
            try {
                foreach ( array_merge( self::$berhasil, array_values( self::$admin ) ) as $b ) {
                    $b['diubah'] = $sekarang;
                    $wpdb->insert( $p . 'wpmgr_logins', $b );
                }

                // Batas per jam dihitung sekali per nilai jam yang muncul di
                // buffer ini, bukan diulang tiap baris (sama seperti $sisa di
                // WPMGR_Penangkap::tulis()): UPDATE di atas sudah cukup untuk
                // kombinasi yang sudah ada -- baris itu boleh bertambah tak
                // terbatas -- jadi COUNT(*) hanya perlu dihitung saat sebuah
                // kombinasi benar-benar baru bagi jam itu.
                $jumlah_per_jam = array();
                foreach ( self::$gagal as $g ) {
                    // $wpdb->prepare() selalu mengutip %s sebagai string,
                    // termasuk untuk argumen null -- tak ada cara membuat
                    // placeholder %s menghasilkan NULL sungguhan (lihat
                    // class-wpdb.php prepare()). Kolom user_agent di sini
                    // mengizinkan NULL (class-wpmgr-skema.php), dan
                    // wpmgr_logins sudah menyimpan NULL sungguhan lewat
                    // $wpdb->insert() (yang menangani null secara khusus) --
                    // supaya konsisten, UA kosong di sini juga harus NULL,
                    // bukan string kosong ''. Makanya fragmen SQL kolomnya
                    // sendiri yang diganti (literal NULL tanpa kutip), bukan
                    // nilainya lewat placeholder.
                    $ua_kolom = null === $g['user_agent'] ? 'NULL' : '%s';
                    $ua_args  = null === $g['user_agent'] ? array() : array( (string) $g['user_agent'] );

                    $args_update = array_merge( array( $g['jumlah'] ), $ua_args, array( $sekarang, $g['jam'], $g['ip'], $g['username'], $g['jalur'] ) );
                    $wpdb->query( $wpdb->prepare(
                        "UPDATE {$p}wpmgr_login_gagal SET jumlah = jumlah + %d, user_agent = {$ua_kolom}, diubah = %d
                         WHERE jam = %d AND ip = %s AND username = %s AND jalur = %s",
                        ...$args_update
                    ) );
                    if ( $wpdb->rows_affected > 0 ) {
                        continue;
                    }
                    if ( ! isset( $jumlah_per_jam[ $g['jam'] ] ) ) {
                        $jumlah_per_jam[ $g['jam'] ] = (int) $wpdb->get_var( $wpdb->prepare(
                            "SELECT COUNT(*) FROM {$p}wpmgr_login_gagal WHERE jam = %d", $g['jam'] ) );
                    }
                    if ( $jumlah_per_jam[ $g['jam'] ] >= self::BATAS_GAGAL_PER_JAM ) {
                        $g['ip']       = '';
                        $g['username'] = self::USERNAME_LAIN;
                    } else {
                        $jumlah_per_jam[ $g['jam'] ]++;
                    }
                    // ON DUPLICATE KEY UPDATE, bukan INSERT polos: dua request
                    // yang sama-sama menemukan kombinasi ini "belum ada" lewat
                    // UPDATE di atas bisa saja bersaing menyisipkannya --
                    // upsert melebur ke baris yang menang, bukan gagal diam-
                    // diam kena kunci unik (jam,ip,username,jalur). Jendela
                    // TOCTOU pada pengecekan batas di atas tetap ada untuk
                    // kombinasi BARU yang berbeda-beda dan datang bersamaan
                    // dari request lain -- sama seperti batas $sisa di
                    // WPMGR_Penangkap, ini batas usaha terbaik, bukan kunci
                    // database, karena tak ada bagian connector lain yang
                    // memakai transaksi/locking.
                    $args_insert = array_merge( array( $g['jam'], $g['ip'], $g['username'], $g['jalur'], $g['jumlah'] ), $ua_args, array( $sekarang ) );
                    $wpdb->query( $wpdb->prepare(
                        "INSERT INTO {$p}wpmgr_login_gagal (jam, ip, username, jalur, jumlah, user_agent, diubah)
                         VALUES (%d, %s, %s, %s, %d, {$ua_kolom}, %d)
                         ON DUPLICATE KEY UPDATE jumlah = jumlah + VALUES(jumlah), user_agent = VALUES(user_agent), diubah = VALUES(diubah)",
                        ...$args_insert
                    ) );
                }
            } finally {
                $wpdb->suppress_errors( $lama );
            }
            self::$berhasil = array();
            self::$gagal    = array();
            self::$admin    = array();
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    // ---- kait test ------------------------------------------------------

    public static function reset_untuk_test() {
        self::$berhasil = array();
        self::$gagal    = array();
        self::$admin    = array();
    }

    public static function gagal_untuk_test() {
        return self::$gagal;
    }

    public static function berhasil_untuk_test() {
        return self::$berhasil;
    }

    /**
     * Menyuntik buffer tulis langsung, melewati WPMGR_IP::saat_ini() (yang
     * butuh WPMGR_Settings::percayai_xff() -> get_option(), WordPress penuh
     * yang tak tersedia di lingkungan PHPUnit murni ini) -- supaya jalur
     * tulis() bisa diuji terpisah dari jalur pembentukan baris.
     */
    public static function isi_untuk_test( array $berhasil = array(), array $gagal = array(), array $admin = array() ) {
        self::$berhasil = $berhasil;
        self::$gagal    = $gagal;
        self::$admin    = $admin;
    }
}
