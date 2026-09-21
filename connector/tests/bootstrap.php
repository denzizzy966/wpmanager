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

require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-skema.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-settings.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-rest.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-updater.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-sso.php';
