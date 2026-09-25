import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from wpmgr.connector_paket import bangun_paket, sumber_bawaan
from wpmgr.kesehatan import susun_kesehatan
from wpmgr.models import (
    CatatanError,
    KejadianLogin,
    LoginGagal,
    Site,
    SiteStatus,
    TrafficHarian,
    UptimeCheck,
    UptimeHasil,
    UptimePutaran,
    UptimeStatus,
)

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)


def buat(sesi, nama, **kolom):
    s = Site(id=uuid.uuid4(), nama=nama, url=f"https://{nama.lower()}.test",
             status=kolom.pop("status", SiteStatus.active), secret_terenkripsi=b"x",
             fitur=kolom.pop("fitur", ["self_update", "events", "traffic"]),
             connector_version=kolom.pop("connector_version", "2.0.0"), **kolom)
    sesi.add(s)
    sesi.commit()
    return s


def baris(hasil, site):
    return next(b for b in hasil["baris"] if b["id"] == str(site.id))


def test_urutan_keparahan_dan_chip(sesi):
    # "Aaa" alfabetis lebih dulu dari ketiga nama lain, tetapi site ini sehat
    # (tingkat 4): kalau urutan hasil ternyata cuma sort nama biasa (bukan
    # (tingkat, nama)), "Aaa" akan muncul PERTAMA, bukan TERAKHIR seperti yang
    # diharapkan di sini.
    sehat = buat(sesi, "Aaa")
    mati = buat(sesi, "Mati", uptime_status=UptimeStatus.mati)
    serang = buat(sesi, "Diserang")
    sesi.add(KejadianLogin(site_id=serang.id, id_di_site=1, waktu=SEKARANG - timedelta(hours=1),
                           jenis="admin_baru", username="x"))
    sesi.commit()

    hasil = susun_kesehatan(sesi, SEKARANG)
    assert [b["nama"] for b in hasil["baris"]] == ["Diserang", "Mati", "Aaa"]
    assert baris(hasil, mati)["masalah"] == ["mati"]
    assert baris(hasil, mati)["tab"] == "uptime"
    assert baris(hasil, serang)["keamanan"] == "perlu_diperiksa"
    assert baris(hasil, serang)["tab"] == "login"
    assert baris(hasil, sehat)["tingkat"] == 4
    assert hasil["chip"]["mati"] == 1
    assert hasil["chip"]["perlu_diperiksa"] == 1
    assert hasil["chip"]["error_baru"] == 0


def test_error_baru_setelah_update(sesi):
    s = buat(sesi, "Err")
    sesi.add(CatatanError(site_id=s.id, sidik_jari="a" * 32, tingkat="fatal", komponen_tipe="plugin",
                          komponen_slug="elementor", pesan="x", jumlah=2,
                          pertama_terlihat=SEKARANG - timedelta(hours=2),
                          terakhir_terlihat=SEKARANG - timedelta(hours=1),
                          setelah_update={"slug": "elementor/elementor.php", "versi_sesudah": "3.20"}))
    sesi.add(CatatanError(site_id=s.id, sidik_jari="b" * 32, tingkat="warning", komponen_tipe="core",
                          pesan="w", jumlah=50, pertama_terlihat=SEKARANG - timedelta(hours=2),
                          terakhir_terlihat=SEKARANG - timedelta(hours=1)))
    sesi.commit()
    b = baris(susun_kesehatan(sesi, SEKARANG), s)
    assert b["error_baru"] == 1
    assert b["error_setelah_update"] is True
    assert b["tab"] == "error"


def test_ssl_koneksi_dan_penangkap(sesi):
    s = buat(sesi, "Ssl", ssl_kedaluwarsa=SEKARANG + timedelta(days=5),
             status=SiteStatus.unreachable, mode_penangkap="terbatas")
    b = baris(susun_kesehatan(sesi, SEKARANG), s)
    assert set(b["masalah"]) == {"ssl", "koneksi", "penangkap_terbatas"}
    assert b["ssl_sisa_hari"] == 5


def test_connector_usang(sesi, var_sementara):
    manifest = bangun_paket(sumber_bawaan(), var_sementara / "connector")
    lama = buat(sesi, "Lama", connector_version="1.0.0", fitur=[])
    kini = buat(sesi, "Kini", connector_version=manifest["versi"])
    hasil = susun_kesehatan(sesi, SEKARANG)
    assert "connector_usang" in baris(hasil, lama)["masalah"]
    assert "connector_usang" not in baris(hasil, kini)["masalah"]


def test_persen_uptime_24_jam_mengabaikan_gangguan_dan_blokir(sesi):
    s = buat(sesi, "Up")
    normal = UptimePutaran(mulai=SEKARANG, jumlah_site=1, jumlah_gagal=0, gangguan_dashboard=False)
    ganggu = UptimePutaran(mulai=SEKARANG, jumlah_site=1, jumlah_gagal=1, gangguan_dashboard=True)
    sesi.add_all([normal, ganggu])
    sesi.flush()
    for i, hasil in enumerate([UptimeHasil.naik] * 3 + [UptimeHasil.gagal, UptimeHasil.terblokir]):
        sesi.add(UptimeCheck(putaran_id=normal.id, site_id=s.id, hasil=hasil,
                             dicek_pada=SEKARANG - timedelta(minutes=5 * i)))
    sesi.add(UptimeCheck(putaran_id=ganggu.id, site_id=s.id, hasil=UptimeHasil.gagal,
                         dicek_pada=SEKARANG - timedelta(minutes=1)))
    sesi.commit()
    assert baris(susun_kesehatan(sesi, SEKARANG), s)["uptime_persen_24j"] == 75.0


def test_traffic_kemarin_dan_anomali(sesi):
    s = buat(sesi, "Trf")
    kemarin = date(2026, 9, 21)
    for i in range(1, 15):
        sesi.add(TrafficHarian(site_id=s.id, tanggal=kemarin - timedelta(days=i), sumber="plugin",
                               kunjungan=100, pengunjung=50))
    sesi.add(TrafficHarian(site_id=s.id, tanggal=kemarin, sumber="plugin", kunjungan=20, pengunjung=10))
    sesi.commit()
    b = baris(susun_kesehatan(sesi, SEKARANG), s)
    assert b["traffic_kemarin"] == 20
    assert b["anomali"] == "anjlok"
    assert "traffic_anjlok" in b["masalah"]


def test_diserang_masuk_chip(sesi):
    s = buat(sesi, "Digempur")
    sesi.add(LoginGagal(site_id=s.id, jam=SEKARANG - timedelta(minutes=10), ip="1.2.3.4",
                        username="admin", jalur="wp-login", jumlah=60))
    sesi.commit()
    b = baris(susun_kesehatan(sesi, SEKARANG), s)
    assert b["keamanan"] == "diserang"
    assert "diserang" in b["masalah"]
    assert b["tab"] == "login"


def test_traffic_melonjak_masuk_chip(sesi):
    s = buat(sesi, "Melonjak")
    kemarin = date(2026, 9, 21)
    for i in range(1, 15):
        sesi.add(TrafficHarian(site_id=s.id, tanggal=kemarin - timedelta(days=i), sumber="plugin",
                               kunjungan=100, pengunjung=50))
    sesi.add(TrafficHarian(site_id=s.id, tanggal=kemarin, sumber="plugin", kunjungan=500, pengunjung=300))
    sesi.commit()
    b = baris(susun_kesehatan(sesi, SEKARANG), s)
    assert b["anomali"] == "melonjak"
    assert "traffic_melonjak" in b["masalah"]


def test_tab_menang_error_baru_atas_ssl(sesi):
    # error_baru dan ssl sama-sama tingkat 2, tetapi error_baru lebih dulu di
    # URUTAN_CHIP: tab hasil akhirnya harus "error", bukan "uptime".
    s = buat(sesi, "SslDanError", ssl_error="Sertifikat tidak valid")
    sesi.add(CatatanError(site_id=s.id, sidik_jari="c" * 32, tingkat="fatal", komponen_tipe="core",
                          pesan="fatal", jumlah=1, pertama_terlihat=SEKARANG - timedelta(hours=1),
                          terakhir_terlihat=SEKARANG - timedelta(minutes=30)))
    sesi.commit()
    b = baris(susun_kesehatan(sesi, SEKARANG), s)
    assert set(b["masalah"]) >= {"ssl", "error_baru"}
    assert b["tab"] == "error"


def test_site_pending_masuk_koneksi(sesi):
    s = buat(sesi, "BelumPasang", status=SiteStatus.pending_pair)
    b = baris(susun_kesehatan(sesi, SEKARANG), s)
    assert "koneksi" in b["masalah"]
    assert b["koneksi"] == "pending_pair"


def test_site_disabled_tidak_ditampilkan(sesi):
    buat(sesi, "Mati", status=SiteStatus.disabled)
    assert susun_kesehatan(sesi, SEKARANG)["baris"] == []


def test_api_kesehatan(klien_web, sesi):
    buat(sesi, "Api")
    r = klien_web.get("/api/kesehatan")
    assert r.status_code == 200
    assert r.json()["baris"][0]["nama"] == "Api"


def test_api_kesehatan_butuh_login(engine):
    from fastapi.testclient import TestClient

    from wpmgr.web.app import buat_app

    c = TestClient(buat_app(), base_url="https://testserver")
    assert c.get("/api/kesehatan").status_code == 401


def test_beranda_adalah_kesehatan_dan_update_pindah(klien_web):
    assert "layarKesehatan()" in klien_web.get("/").text
    r = klien_web.get("/updates")
    assert r.status_code == 200
    assert "layarUpdate()" in r.text
