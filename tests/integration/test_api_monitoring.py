import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from wpmgr.models import (
    ActivityLog,
    CatatanError,
    KejadianLogin,
    LoginGagal,
    PackageType,
    Site,
    SitePackage,
    UptimeCheck,
    UptimeHasil,
    UptimeInsiden,
    UptimePutaran,
)
from wpmgr.uptime import rata_waktu_ms, uptime_harian

pytestmark = pytest.mark.integration

SEKARANG = datetime.now(timezone.utc)


def test_api_butuh_login_dan_site_ada(klien_web, engine):
    from fastapi.testclient import TestClient

    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), base_url="https://testserver")
    assert anon.get(f"/api/sites/{uuid.uuid4()}/uptime").status_code == 401
    assert klien_web.get(f"/api/sites/{uuid.uuid4()}/uptime").status_code == 404


def test_uptime(klien_web, sesi, site):
    p = UptimePutaran(mulai=SEKARANG, jumlah_site=1, jumlah_gagal=0, gangguan_dashboard=False)
    sesi.add(p)
    sesi.flush()
    for i in range(4):
        sesi.add(UptimeCheck(putaran_id=p.id, site_id=site.id, dicek_pada=SEKARANG - timedelta(minutes=5 * i),
                             hasil=UptimeHasil.naik if i else UptimeHasil.gagal, waktu_ms=200))
    sesi.add(UptimeInsiden(site_id=site.id, mulai=SEKARANG - timedelta(hours=3),
                           selesai=SEKARANG - timedelta(hours=1, minutes=55), penyebab="HTTP 500"))
    sesi.commit()

    d = klien_web.get(f"/api/sites/{site.id}/uptime?hari=30").json()
    assert d["persen"]["24j"] == 75.0
    assert d["rata_ms"] == 200
    assert len(d["harian"]) == 30
    assert d["insiden"][0]["penyebab"] == "HTTP 500"
    assert d["insiden"][0]["durasi"] == "1 jam 5 menit"


def test_errors_dan_tandai_selesai(klien_web, sesi, site):
    sesi.add(SitePackage(site_id=site.id, tipe=PackageType.plugin, slug="elementor/elementor.php",
                         nama="Elementor", versi_terpasang="3.20", last_scan_at=SEKARANG))
    e = CatatanError(site_id=site.id, sidik_jari="a" * 32, tingkat="fatal", komponen_tipe="plugin",
                     komponen_slug="elementor", pesan="<script>alert(1)</script>", file="a.php", baris=3,
                     jumlah=2, pertama_terlihat=SEKARANG - timedelta(hours=1),
                     terakhir_terlihat=SEKARANG - timedelta(minutes=5),
                     setelah_update={"slug": "elementor/elementor.php", "versi_sesudah": "3.20"})
    sesi.add(e)
    sesi.commit()

    daftar = klien_web.get(f"/api/sites/{site.id}/errors").json()
    assert daftar[0]["status"] == "baru"
    assert daftar[0]["komponen_teks"] == "Plugin Elementor"
    assert daftar[0]["setelah_update_teks"] == "muncul setelah update ke v3.20"
    assert daftar[0]["pesan"] == "<script>alert(1)</script>"  # JSON apa adanya; dirender lewat x-text

    r = klien_web.post(f"/api/sites/{site.id}/errors/{e.id}/selesai")
    assert r.status_code == 200
    assert klien_web.get(f"/api/sites/{site.id}/errors").json()[0]["status"] == "selesai"
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Error ditandai selesai%")).count() == 1


def test_tandai_selesai_error_site_lain_404(klien_web, sesi, site):
    lain = Site(id=uuid.uuid4(), nama="L", url="https://l.test", secret_terenkripsi=b"x")
    sesi.add(lain)
    e = CatatanError(site_id=site.id, sidik_jari="b" * 32, tingkat="fatal", komponen_tipe="core",
                     pesan="x", jumlah=1, pertama_terlihat=SEKARANG, terakhir_terlihat=SEKARANG)
    sesi.add(e)
    sesi.commit()
    assert klien_web.post(f"/api/sites/{lain.id}/errors/{e.id}/selesai").status_code == 404


def test_logins_dan_sudah_diperiksa(klien_web, sesi, site):
    sesi.add(KejadianLogin(site_id=site.id, id_di_site=1, waktu=SEKARANG - timedelta(hours=1),
                           jenis="berhasil", username="admin", ip="203.0.113.9", negara="ID",
                           jalur="form", user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/128.0"))
    sesi.add(KejadianLogin(site_id=site.id, id_di_site=2, waktu=SEKARANG - timedelta(minutes=30),
                           jenis="admin_baru", username="baru"))
    jam = SEKARANG.replace(minute=0, second=0, microsecond=0)
    sesi.add(LoginGagal(site_id=site.id, jam=jam, ip="198.51.100.7", username="admin", jalur="xmlrpc",
                        jumlah=15, user_agent="curl/8"))
    sesi.add(LoginGagal(site_id=site.id, jam=jam, ip="198.51.100.7", username="root", jalur="xmlrpc",
                        jumlah=5, user_agent="curl/8"))
    sesi.commit()

    d = klien_web.get(f"/api/sites/{site.id}/logins?hari=30").json()
    assert d["status"] == "perlu_diperiksa"
    assert d["berhasil"][0]["peramban"] == "Chrome"
    assert d["admin"][0]["username"] == "baru"
    assert d["gagal"][0] == {
        "ip": "198.51.100.7", "negara": None, "jumlah": 20, "username": ["admin", "root"],
        "jalur": ["xmlrpc"], "skrip": True,
    }

    assert klien_web.post(f"/api/sites/{site.id}/keamanan/diperiksa").status_code == 200
    d2 = klien_web.get(f"/api/sites/{site.id}/logins?hari=30").json()
    assert d2["status"] == "diserang"   # admin_baru sudah diperiksa; 20 percobaan dari satu IP tersisa


def test_login_gagal_ip_kosong_diberi_label(klien_web, sesi, site):
    jam = SEKARANG.replace(minute=0, second=0, microsecond=0)
    sesi.add(LoginGagal(site_id=site.id, jam=jam, ip="", username="(lainnya)",
                        jalur="wp-login", jumlah=7))
    sesi.commit()

    d = klien_web.get(f"/api/sites/{site.id}/logins?hari=30").json()
    assert d["gagal"][0]["ip"] == "(IP lain)"
    assert d["gagal"][0]["jumlah"] == 7


def test_login_gagal_agregasi_sql_akurat_dan_ip_deras_tetap_ada(klien_web, sesi, site):
    """Regresi: LIMIT pada baris LoginGagal MENTAH (sebelum diringkas per IP)
    menjatuhkan baris "kecil" secara diam-diam. Serangan username-spray
    menyebar jadi banyak baris ber-jumlah kecil per IP yang sama; kalau LIMIT
    dipasang sebelum agregasi, total per IP itu jadi salah -- atau IP itu
    hilang total dari daftar walau totalnya sebenarnya termasuk yang terderas.
    """
    jam = SEKARANG.replace(minute=0, second=0, microsecond=0)
    baris = [
        # 205 IP "pengisi" bertotal kecil (1 percobaan setiap satu), supaya
        # jumlah IP berbeda > 200 (batas jumlah IP di respons).
        LoginGagal(site_id=site.id, jam=jam, ip=f"10.0.0.{i}", username="admin",
                   jalur="wp-login", jumlah=1)
        for i in range(1, 206)
    ]
    baris += [
        # Satu IP deras, totalnya disebar ke 5 baris (jam berbeda-beda) --
        # totalnya harus SUM dari semuanya (5000), bukan salah satu baris.
        LoginGagal(site_id=site.id, jam=jam - timedelta(hours=k), ip="10.0.1.1",
                   username="admin", jalur="xmlrpc", jumlah=1000, user_agent="curl/8")
        for k in range(5)
    ]
    baris += [
        # Satu IP username-spray: 30 username berbeda, baris kecil (jumlah=2)
        # tapi totalnya (60) tetap harus akurat dan IP-nya tidak boleh hilang.
        LoginGagal(site_id=site.id, jam=jam, ip="10.0.2.2", username=f"user{k}",
                   jalur="wp-login", jumlah=2)
        for k in range(30)
    ]
    sesi.add_all(baris)
    sesi.commit()

    d = klien_web.get(f"/api/sites/{site.id}/logins?hari=30").json()
    assert len(d["gagal"]) == 200  # 207 IP berbeda, dipotong ke 200 paling deras

    per_ip = {b["ip"]: b for b in d["gagal"]}
    assert per_ip["10.0.1.1"]["jumlah"] == 5000
    assert per_ip["10.0.1.1"]["skrip"] is True
    assert per_ip["10.0.2.2"]["jumlah"] == 60
    assert len(per_ip["10.0.2.2"]["username"]) <= 5


def test_admin_baru_tidak_tertimbun_login_berhasil(klien_web, sesi, site):
    """Regresi: satu LIMIT bersama untuk semua jenis kejadian login membuat
    login berhasil yang deras menenggelamkan kejadian admin yang lebih lama
    tapi masih dalam jendela `hari` -- tabel Administrator baru jadi bilang
    "Tidak ada" padahal kejadiannya sungguhan ada dan cukup baru.
    """
    sesi.add(KejadianLogin(site_id=site.id, id_di_site=0, waktu=SEKARANG - timedelta(days=2),
                           jenis="admin_baru", username="admin_lama"))
    sesi.add_all([
        KejadianLogin(site_id=site.id, id_di_site=i + 1, waktu=SEKARANG - timedelta(minutes=i),
                     jenis="berhasil", username="admin", ip="203.0.113.9")
        for i in range(600)
    ])
    sesi.commit()

    d = klien_web.get(f"/api/sites/{site.id}/logins?hari=30").json()
    assert any(a["username"] == "admin_lama" for a in d["admin"])


def test_ga4_property(klien_web, sesi, site):
    assert klien_web.put(f"/api/sites/{site.id}/ga4", json={"property_id": "123456789"}).status_code == 200
    sesi.refresh(site)
    assert site.ga4_property_id == "123456789"
    assert klien_web.put(f"/api/sites/{site.id}/ga4", json={"property_id": "G-ABC"}).status_code == 422
    r = klien_web.put(f"/api/sites/{site.id}/ga4", json={"property_id": ""})
    assert r.status_code == 200
    sesi.refresh(site)
    assert site.ga4_property_id is None


def test_ga4_tidak_hapus_status_bila_id_sama(klien_web, sesi, site):
    site.ga4_property_id = "123456789"
    site.ga4_error = "Kuota GA4 habis; dicoba lagi besok"
    site.ga4_diambil_pada = SEKARANG
    sesi.commit()

    # Simpan ulang NILAI YANG SAMA (mis. klik "Simpan" tanpa mengubah apa pun)
    # tidak boleh menghapus status pengambilan yang masih berlaku.
    assert klien_web.put(f"/api/sites/{site.id}/ga4", json={"property_id": "123456789"}).status_code == 200
    sesi.refresh(site)
    assert site.ga4_error == "Kuota GA4 habis; dicoba lagi besok"
    assert site.ga4_diambil_pada == SEKARANG

    # Property ID SUNGGUHAN berubah: status lama tidak relevan lagi.
    assert klien_web.put(f"/api/sites/{site.id}/ga4", json={"property_id": "987654321"}).status_code == 200
    sesi.refresh(site)
    assert site.ga4_error is None
    assert site.ga4_diambil_pada is None


def test_404_rute_mutasi_untuk_site_tak_dikenal(klien_web):
    acak = uuid.uuid4()
    assert klien_web.post(f"/api/sites/{acak}/errors/1/selesai").status_code == 404
    assert klien_web.post(f"/api/sites/{acak}/keamanan/diperiksa").status_code == 404
    assert klien_web.put(f"/api/sites/{acak}/ga4", json={"property_id": "123456789"}).status_code == 404


def test_hari_uptime_dijepit(klien_web, site):
    assert len(klien_web.get(f"/api/sites/{site.id}/uptime?hari=9999").json()["harian"]) == 90
    assert len(klien_web.get(f"/api/sites/{site.id}/uptime?hari=0").json()["harian"]) == 1
    assert len(klien_web.get(f"/api/sites/{site.id}/uptime?hari=-5").json()["harian"]) == 1


def test_rata_waktu_ms_mengabaikan_putaran_gangguan(sesi, site):
    p_baik = UptimePutaran(mulai=SEKARANG, jumlah_site=1, jumlah_gagal=0, gangguan_dashboard=False)
    p_gangguan = UptimePutaran(mulai=SEKARANG, jumlah_site=5, jumlah_gagal=5, gangguan_dashboard=True)
    sesi.add_all([p_baik, p_gangguan])
    sesi.flush()
    sesi.add(UptimeCheck(putaran_id=p_baik.id, site_id=site.id, dicek_pada=SEKARANG,
                         hasil=UptimeHasil.naik, waktu_ms=100))
    sesi.add(UptimeCheck(putaran_id=p_gangguan.id, site_id=site.id, dicek_pada=SEKARANG,
                         hasil=UptimeHasil.naik, waktu_ms=900))
    sesi.commit()

    # Tanpa filter gangguan_dashboard, rata-rata akan (100+900)/2 = 500.
    assert rata_waktu_ms(sesi, site.id, SEKARANG - timedelta(hours=1)) == 100


def test_uptime_harian_utc_walau_sesi_zona_lain(sesi, site):
    # 23:00 UTC 25 Sept = 06:00 Asia/Jakarta (+7) tanggal 26 -- offset +7 jam
    # dari sini MELEWATI batas hari UTC. Kalau date_trunc('day', ...) memakai
    # TimeZone sesi (Jakarta), tengah malam lokalnya jatuh pada hari kalender
    # ke-26, bukan ke-25 -- padahal menurut UTC (yang dipakai seluruh
    # dashboard ini), cek ini terjadi pada tanggal 25.
    waktu = datetime(2026, 9, 25, 23, 0, tzinfo=timezone.utc)
    p = UptimePutaran(mulai=waktu, jumlah_site=1, jumlah_gagal=0, gangguan_dashboard=False)
    sesi.add(p)
    sesi.flush()
    sesi.add(UptimeCheck(putaran_id=p.id, site_id=site.id, dicek_pada=waktu,
                         hasil=UptimeHasil.naik, waktu_ms=100))
    sesi.commit()

    sesi.execute(text("SET TIME ZONE 'Asia/Jakarta'"))
    try:
        hasil = uptime_harian(sesi, site.id, datetime(2026, 9, 27, 12, tzinfo=timezone.utc), hari=3)
    finally:
        # Dikembalikan sebelum sesi ini ditutup: koneksinya kembali ke pool
        # dan bisa dipakai ulang oleh test lain yang mengasumsikan UTC.
        sesi.execute(text("SET TIME ZONE 'UTC'"))

    peta = {h["tanggal"]: h["persen"] for h in hasil}
    assert peta["2026-09-25"] == 100.0
    assert peta.get("2026-09-26") is None


def test_lencana_error_hanya_hitung_yang_menyalakan_chip(klien_web, sesi, site):
    sesi.add_all([
        # baru: fatal, pertama_terlihat < 24 jam lalu -- dihitung.
        CatatanError(site_id=site.id, sidik_jari="c1" * 16, tingkat="fatal", komponen_tipe="core",
                     pesan="fatal baru", jumlah=1,
                     pertama_terlihat=SEKARANG - timedelta(hours=1), terakhir_terlihat=SEKARANG),
        # masih_terjadi: database, pertama_terlihat lama tapi terakhir_terlihat baru -- dihitung.
        CatatanError(site_id=site.id, sidik_jari="c2" * 16, tingkat="database", komponen_tipe="core",
                     pesan="db lama tapi masih terjadi", jumlah=1,
                     pertama_terlihat=SEKARANG - timedelta(hours=48), terakhir_terlihat=SEKARANG),
        # tingkat warning -- tidak pernah dihitung berapa pun baru-nya.
        CatatanError(site_id=site.id, sidik_jari="c3" * 16, tingkat="warning", komponen_tipe="core",
                     pesan="warning tidak dihitung", jumlah=1,
                     pertama_terlihat=SEKARANG, terakhir_terlihat=SEKARANG),
        # selesai: ditandai_selesai_pada sesudah terakhir_terlihat -- tidak dihitung.
        CatatanError(site_id=site.id, sidik_jari="c4" * 16, tingkat="fatal", komponen_tipe="core",
                     pesan="sudah selesai", jumlah=1,
                     pertama_terlihat=SEKARANG - timedelta(hours=48),
                     terakhir_terlihat=SEKARANG - timedelta(hours=47),
                     ditandai_selesai_pada=SEKARANG - timedelta(hours=1)),
        # berhenti: pertama dan terakhir terlihat sama-sama lebih dari 24 jam lalu -- tidak dihitung.
        CatatanError(site_id=site.id, sidik_jari="c5" * 16, tingkat="fatal", komponen_tipe="core",
                     pesan="sudah berhenti lama", jumlah=1,
                     pertama_terlihat=SEKARANG - timedelta(days=10),
                     terakhir_terlihat=SEKARANG - timedelta(days=9)),
    ])
    sesi.commit()

    r = klien_web.get(f"/sites/{site.id}")
    assert 'Error <span class="lencana">2</span>' in r.text


def test_halaman_detail_bertab(klien_web, sesi, site):
    sesi.add(SitePackage(site_id=site.id, tipe=PackageType.plugin, slug="x/x.php",
                         nama="<img src=x onerror=alert(1)>", versi_terpasang="1", last_scan_at=SEKARANG))
    sesi.commit()
    r = klien_web.get(f"/sites/{site.id}?tab=error")
    assert r.status_code == 200
    assert f"detailSite('{site.id}', 'error')" in r.text
    for label in ("Ringkasan", "Paket", "Uptime", "Error", "Login", "Aktivitas"):
        assert label in r.text
    assert "<img src=x" not in r.text
    assert "&lt;img src=x" in r.text


def test_tab_tak_dikenal_jatuh_ke_ringkasan(klien_web, site):
    r = klien_web.get(f"/sites/{site.id}?tab=%27);alert(1);//")
    assert f"detailSite('{site.id}', 'ringkasan')" in r.text
