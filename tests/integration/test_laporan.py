from datetime import date, datetime, timedelta, timezone

import pytest

from wpmgr.models import (
    ActivityLog,
    TrafficHarian,
    TrafficRincian,
    UptimeInsiden,
    UptimeStatus,
)

pytestmark = pytest.mark.integration


def isi_agustus(sesi, site):
    for hari in range(1, 32):
        sesi.add(TrafficHarian(site_id=site.id, tanggal=date(2026, 8, hari), sumber="plugin",
                               kunjungan=10, pengunjung=4))
    sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                            dimensi="halaman", kunci="/layanan", kunjungan=120))
    sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                            dimensi="halaman", kunci="<script>alert(1)</script>", kunjungan=3))
    sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                            dimensi="asal", kunci="pencarian:Google", kunjungan=50))
    sesi.add(UptimeInsiden(site_id=site.id, mulai=datetime(2026, 8, 10, 10, tzinfo=timezone.utc),
                           selesai=datetime(2026, 8, 10, 13, tzinfo=timezone.utc), penyebab="HTTP 500"))
    sesi.add(ActivityLog(site_id=site.id, level="info", pesan="Update plugin elementor",
                         dibuat_pada=datetime(2026, 8, 15, 9, tzinfo=timezone.utc),
                         detail={"tipe": "plugin", "slug": "elementor/elementor.php",
                                 "versi_sebelum": "3.18", "versi_sesudah": "3.20", "email": "a@b.test"}))
    site.uptime_status = UptimeStatus.naik
    sesi.commit()


def test_api_traffic_tanpa_ga4(klien_web, sesi, site):
    hari_ini = datetime.now(timezone.utc).date()
    sesi.add(TrafficHarian(site_id=site.id, tanggal=hari_ini - timedelta(days=1), sumber="plugin",
                           kunjungan=7, pengunjung=5))
    sesi.commit()
    d = klien_web.get(f"/api/sites/{site.id}/traffic?hari=30").json()
    assert len(d["plugin"]["harian"]) == 30
    assert d["plugin"]["total_kunjungan"] == 7
    assert d["ga4"] is None
    assert d["ga4_terpasang"] is False


def test_api_traffic_ga4_terpasang_tanpa_data(klien_web, sesi, site):
    site.ga4_property_id = "123456789"
    site.ga4_error = "Service account belum ditambahkan sebagai Viewer di property ini"
    sesi.commit()
    d = klien_web.get(f"/api/sites/{site.id}/traffic").json()
    assert d["ga4_terpasang"] is True
    assert d["ga4"] is None
    assert "Viewer" in d["ga4_error"]


def test_laporan_agustus(klien_web, sesi, site):
    isi_agustus(sesi, site)
    r = klien_web.get(f"/sites/{site.id}/laporan/2026-08")
    assert r.status_code == 200
    teks = r.text
    assert "Agustus 2026" in teks
    assert "310" in teks                      # total kunjungan 31 hari x 10
    assert "99.60%" in teks                   # 3 jam mati dari 744 jam
    assert "/layanan" in teks
    assert "Pencarian" in teks and "Google" in teks
    assert "3.18" in teks and "3.20" in teks
    assert "<script>alert(1)</script>" not in teks
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in teks


def test_laporan_site_belum_dipantau(klien_web, sesi, site):
    r = klien_web.get(f"/sites/{site.id}/laporan/2026-08")
    assert "belum dipantau" in r.text
    assert "Belum ada data pengunjung" in r.text


@pytest.mark.parametrize("bulan", ["2026-13", "2026-8", "abc", "2099-01"])
def test_bulan_tidak_sah_404(klien_web, site, bulan):
    assert klien_web.get(f"/sites/{site.id}/laporan/{bulan}").status_code == 404


def test_detail_punya_tab_traffic(klien_web, site):
    assert "pilih('traffic')" in klien_web.get(f"/sites/{site.id}").text
