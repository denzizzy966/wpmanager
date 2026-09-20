<?php
use PHPUnit\Framework\TestCase;

final class UpdaterVersiTest extends TestCase {

    public function test_versi_identik_dianggap_sudah_di_versi(): void {
        $this->assertTrue( WPMGR_Updater::sudah_di_versi( '1.2.3', '1.2.3' ) );
    }

    public function test_versi_terpasang_lebih_lama_belum_di_versi(): void {
        $this->assertFalse( WPMGR_Updater::sudah_di_versi( '1.2.3', '1.2.4' ) );
    }

    public function test_versi_terpasang_lebih_baru_dianggap_sudah_di_versi(): void {
        // Client bisa saja meng-update manual ke versi yang lebih baru daripada
        // target job yang sedang berjalan; retry yang terlambat tidak boleh
        // menurunkan (downgrade) versi tersebut.
        $this->assertTrue( WPMGR_Updater::sudah_di_versi( '1.2.4', '1.2.3' ) );
    }

    public function test_komponen_lebih_sedikit_tapi_sama_dianggap_belum(): void {
        // "3.20" vs "3.20.1": versi terpasang setara dengan menganggap
        // komponen ketiga yang hilang sebagai 0, jadi 3.20 < 3.20.1.
        $this->assertFalse( WPMGR_Updater::sudah_di_versi( '3.20', '3.20.1' ) );
    }

    public function test_komponen_lebih_banyak_dan_lebih_tinggi_dianggap_sudah(): void {
        // "3.20.1" vs "3.20": 3.20.1 > 3.20, jadi sudah di versi tersebut (dan
        // melewatinya).
        $this->assertTrue( WPMGR_Updater::sudah_di_versi( '3.20.1', '3.20' ) );
    }

    public function test_prerelease_terhadap_rilis_dijalankan_dan_dicatat(): void {
        // Perilaku version_compare() diperiksa langsung, bukan diasumsikan.
        // Dijalankan (lihat laporan task) dan hasilnya:
        //   version_compare('1.0-beta', '1.0', '>=') === false
        //   version_compare('1.0', '1.0-beta', '>=') === true
        // PHP memperlakukan akhiran pre-release seperti "-beta" sebagai
        // penanda versi yang LEBIH RENDAH daripada rilis polos dengan angka
        // yang sama ("1.0-beta" < "1.0"), sesuai urutan kematangan rilis
        // bawaan version_compare() (dev < alpha/a < beta/b < RC/rc < # < pl/p).
        $this->assertFalse( WPMGR_Updater::sudah_di_versi( '1.0-beta', '1.0' ) );
        $this->assertTrue( WPMGR_Updater::sudah_di_versi( '1.0', '1.0-beta' ) );
    }
}
