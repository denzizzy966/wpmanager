import json
import subprocess
import time
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from wpmgr.jobs.queue import buat_job
from wpmgr.keamanan import StatusKeamanan, nilai_keamanan
from wpmgr.models import (
    CatatanError,
    JobStatus,
    JobType,
    KejadianLogin,
    LoginGagal,
    UptimeInsiden,
    UptimeStatus,
)
from wpmgr.uptime import buat_klien_http, cek_satu, jalankan_putaran

from .conftest import (
    WP_URL,
    _wpcli_status,
    hapus_di_kontainer,
    jalankan_sampai_selesai,
    klien_http,
    permintaan_bertanda,
    tulis_di_kontainer,
    tunggu_hingga,
    wpcli,
)

pytestmark = pytest.mark.e2e

DIR_PLUGIN_FATAL = "/var/www/html/wp-content/plugins/wpmgr-uji-fatal"
ISI_PLUGIN_FATAL = """<?php
/**
 * Plugin Name: WPMGR Uji Fatal
 * Description: Dipasang test e2e; memicu fatal error hanya bila diminta.
 */
add_action( 'init', function () {
    if ( isset( $_GET['wpmgr_uji_fatal'] ) ) {
        wpmgr_fungsi_yang_tidak_ada();
    }
} );
"""


def _job(sesi, site, tipe):
    job = buat_job(sesi, site.id, tipe, max_attempts=1)
    # verify_site menjadwalkan scan_site susulan yang lebih tua; satu panggilan
    # proses_satu() belum tentu mengambil job ini (Ruling R5).
    jalankan_sampai_selesai(sesi, job)
    assert job.status == JobStatus.success, job.error
    sesi.refresh(site)
    return job


def _job_sampai(sesi, site, tipe, kondisi, batas_detik: float = 20) -> None:
    """Ulangi job `tipe` sampai `kondisi()` bernilai True.

    Koreksi #10: /events (dipakai collect_events) hanya mengirim baris yang
    sudah melewati CAKRAWALA (5 detik). Memicu sebuah kejadian di site lalu
    memanggil collect_events SEKALI tidak cukup -- baris itu belum tentu
    "tenang" pada saat itu. Setiap percobaan menjalankan job penuh (bukan
    sekadar menunggu), jadi begitu baris sudah tenang, percobaan berikutnya
    pasti membawanya pulang.
    """
    def coba() -> bool:
        _job(sesi, site, tipe)
        return kondisi()

    assert tunggu_hingga(coba, batas_detik), (
        f"kondisi tidak terpenuhi dalam {batas_detik} detik setelah collect_events berulang"
    )


@pytest.fixture
def site_siap(sesi, site_terpasang):
    _job(sesi, site_terpasang, JobType.verify_site)
    assert {"events", "traffic", "self_update"} <= set(site_terpasang.fitur)
    return site_terpasang


def test_fatal_error_plugin_tertangkap_dengan_atribusi(sesi, site_siap):
    tulis_di_kontainer(f"{DIR_PLUGIN_FATAL}/wpmgr-uji-fatal.php", ISI_PLUGIN_FATAL)
    try:
        wpcli("plugin", "activate", "wpmgr-uji-fatal")
        assert httpx.get(f"{WP_URL}/?wpmgr_uji_fatal=1", timeout=30).status_code == 500

        def tercatat() -> bool:
            return sesi.query(CatatanError).filter_by(
                site_id=site_siap.id, tingkat="fatal", komponen_slug="wpmgr-uji-fatal"
            ).first() is not None

        _job_sampai(sesi, site_siap, JobType.collect_events, tercatat)
        e = sesi.query(CatatanError).filter_by(site_id=site_siap.id, tingkat="fatal",
                                               komponen_slug="wpmgr-uji-fatal").one()
        assert e.komponen_tipe == "plugin"
        assert "wpmgr_fungsi_yang_tidak_ada" in e.pesan
        assert site_siap.mode_penangkap == "penuh"
    finally:
        _wpcli_status("plugin", "deactivate", "wpmgr-uji-fatal")
        hapus_di_kontainer(DIR_PLUGIN_FATAL)


def _xmlrpc_login(username, password):
    return (
        "<?xml version='1.0'?><methodCall><methodName>wp.getUsersBlogs</methodName><params>"
        f"<param><value><string>{username}</string></value></param>"
        f"<param><value><string>{password}</string></value></param></params></methodCall>"
    )


def _xmlrpc_multicall(username, jumlah):
    panggilan = "".join(
        "<value><struct><member><name>methodName</name><value><string>wp.getUsersBlogs</string></value></member>"
        "<member><name>params</name><value><array><data>"
        f"<value><string>{username}</string></value><value><string>salah{i}</string></value>"
        "</data></array></value></member></struct></value>"
        for i in range(jumlah)
    )
    return ("<?xml version='1.0'?><methodCall><methodName>system.multicall</methodName><params>"
            f"<param><value><array><data>{panggilan}</data></array></value></param></params></methodCall>")


def test_login_gagal_lewat_form_dan_xmlrpc(sesi, site_siap):
    nama = f"penyerang{uuid.uuid4().hex[:6]}"
    for _ in range(3):
        httpx.post(f"{WP_URL}/wp-login.php", data={"log": nama, "pwd": "salah"}, timeout=30)
    for _ in range(2):
        httpx.post(f"{WP_URL}/xmlrpc.php", content=_xmlrpc_login(nama, "salah"),
                   headers={"Content-Type": "text/xml"}, timeout=30)
    # Koreksi #9: satu request multicall = satu kejadian gagal, berapa pun isinya.
    httpx.post(f"{WP_URL}/xmlrpc.php", content=_xmlrpc_multicall(nama, 5),
               headers={"Content-Type": "text/xml"}, timeout=30)

    def _per_jalur():
        return {g.jalur: g.jumlah for g in sesi.query(LoginGagal).filter_by(site_id=site_siap.id, username=nama)}

    _job_sampai(sesi, site_siap, JobType.collect_events, lambda: _per_jalur() == {"form": 3, "xmlrpc": 3})
    assert _per_jalur() == {"form": 3, "xmlrpc": 3}


def _masuk(client: httpx.Client):
    client.get(f"{WP_URL}/wp-login.php")
    r = client.post(f"{WP_URL}/wp-login.php",
                    data={"log": "admin", "pwd": "admin-uji-123", "testcookie": "1"},
                    cookies={"wordpress_test_cookie": "WP Cookie check"})
    assert r.status_code == 302


def test_login_berhasil_tercatat(sesi, site_siap):
    sebelum = sesi.query(KejadianLogin).filter_by(site_id=site_siap.id, username="admin").count()
    with httpx.Client(timeout=30, follow_redirects=False) as c:
        _masuk(c)

    def _jumlah():
        return sesi.query(KejadianLogin).filter_by(
            site_id=site_siap.id, username="admin", jenis="berhasil"
        ).count()

    _job_sampai(sesi, site_siap, JobType.collect_events, lambda: _jumlah() > sebelum)
    baris = sesi.query(KejadianLogin).filter_by(site_id=site_siap.id, username="admin", jenis="berhasil").all()
    assert len(baris) > sebelum
    assert baris[-1].jalur == "form"


def test_admin_baru_memerahkan_status_tetapi_user_wpmgr_tidak(sesi, site_siap):
    wpcli("eval", "WPMGR_Settings::pastikan_user();")
    nama = f"uji{uuid.uuid4().hex[:6]}"
    wpcli("user", "create", nama, f"{nama}@uji.local", "--role=administrator")
    try:
        def _tercatat() -> bool:
            return sesi.query(KejadianLogin).filter_by(
                site_id=site_siap.id, username=nama, jenis="admin_baru"
            ).first() is not None

        _job_sampai(sesi, site_siap, JobType.collect_events, _tercatat)
        kejadian = {(k.username, k.jenis) for k in sesi.query(KejadianLogin).filter_by(site_id=site_siap.id)}
        assert (nama, "admin_baru") in kejadian
        assert not any(u == "wpmgr" and j != "berhasil" for u, j in kejadian)
        hasil = nilai_keamanan(sesi, site_siap, datetime.now(timezone.utc))
        assert hasil.status == StatusKeamanan.perlu_diperiksa
    finally:
        _wpcli_status("user", "delete", nama, "--yes")


def _hari_ini(klien):
    # Zona waktu WordPress baru adalah UTC, jadi "hari ini" site = tanggal UTC.
    hari_ini = datetime.now(timezone.utc).date().isoformat()
    for h in klien.traffic()["hari"]:
        if h["tanggal"] == hari_ini:
            return h["total"]
    return {"kunjungan": 0, "pengunjung": 0}


def test_beacon_dan_hit_masuk_ke_traffic(sesi, site_siap):
    halaman = httpx.get(WP_URL, timeout=30).text
    assert "sendBeacon" in halaman and "wpmgr" in halaman

    klien = klien_http(site_siap)
    sebelum = _hari_ini(klien)
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 UjiE2E/{uuid.uuid4().hex[:8]}"
    for path in ("/", "/", "/tentang"):
        r = httpx.post(f"{WP_URL}/wp-json/wpmgr/v1/hit",
                       content=json.dumps({"p": path, "r": "https://www.google.com/"}),
                       headers={"Content-Type": "text/plain", "User-Agent": ua}, timeout=30)
        assert r.status_code == 204

    # Pengunjung yang sedang login tidak dihitung.
    with httpx.Client(timeout=30, follow_redirects=False) as c:
        _masuk(c)
        c.post(f"{WP_URL}/wp-json/wpmgr/v1/hit", content=json.dumps({"p": "/", "r": ""}),
               headers={"Content-Type": "text/plain", "User-Agent": ua + "-login"})

    sesudah = _hari_ini(klien)
    assert sesudah["kunjungan"] - sebelum["kunjungan"] == 3
    assert sesudah["pengunjung"] - sebelum["pengunjung"] == 1

    _job(sesi, site_siap, JobType.collect_traffic)
    assert site_siap.traffic_diambil_pada is not None


def test_header_anti_cache_di_endpoint_pemantauan(sesi, site_siap):
    from wpmgr.crypto import dekripsi_secret

    secret = dekripsi_secret(site_siap.secret_terenkripsi)
    for route in ("/wpmgr/v1/inventory", "/wpmgr/v1/events", "/wpmgr/v1/traffic"):
        r = permintaan_bertanda(site_siap, secret, "GET", route)
        assert r.status_code == 200, route
        assert "no-store" in r.headers["cache-control"], route


def _tunggu_wordpress(batas_detik=120):
    batas = time.time() + batas_detik
    while time.time() < batas:
        try:
            if httpx.get(WP_URL, timeout=5).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    pytest.fail("WordPress tidak kembali dalam waktu yang ditentukan")


def test_uptime_mati_lalu_pulih(sesi, site_siap):
    with buat_klien_http() as http:
        def cek(url):
            return cek_satu(http, url)

        jalankan_putaran(sesi, cek)
        sesi.refresh(site_siap)
        assert site_siap.uptime_status == UptimeStatus.naik

        subprocess.run(["docker", "compose", "stop", "wp"], check=True, capture_output=True)
        try:
            jalankan_putaran(sesi, cek)
            jalankan_putaran(sesi, cek)
            sesi.refresh(site_siap)
            assert site_siap.uptime_status == UptimeStatus.mati
            assert sesi.query(UptimeInsiden).filter_by(site_id=site_siap.id, selesai=None).count() == 1
        finally:
            subprocess.run(["docker", "compose", "start", "wp"], check=True, capture_output=True)
            _tunggu_wordpress()

        jalankan_putaran(sesi, cek)
        sesi.refresh(site_siap)
        assert site_siap.uptime_status == UptimeStatus.naik
        assert sesi.query(UptimeInsiden).filter_by(site_id=site_siap.id, selesai=None).count() == 0
