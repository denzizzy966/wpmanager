import uuid
from datetime import datetime, timedelta, timezone

import pytest

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


def test_ga4_property(klien_web, sesi, site):
    assert klien_web.put(f"/api/sites/{site.id}/ga4", json={"property_id": "123456789"}).status_code == 200
    sesi.refresh(site)
    assert site.ga4_property_id == "123456789"
    assert klien_web.put(f"/api/sites/{site.id}/ga4", json={"property_id": "G-ABC"}).status_code == 422
    klien_web.put(f"/api/sites/{site.id}/ga4", json={"property_id": ""})
    sesi.refresh(site)
    assert site.ga4_property_id is None


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
