import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from wpmgr.laporan import susun_laporan
from wpmgr.models import (
    ActivityLog,
    TrafficHarian,
    TrafficRincian,
    UptimeInsiden,
    UptimeStatus,
)
from wpmgr.traffic import (
    BATAS_HALAMAN,
    BATAS_PERANGKAT,
    KUNCI_ASAL_LAINNYA,
    KUNCI_ASAL_LIPATAN,
    ringkasan_traffic,
)

pytestmark = pytest.mark.integration


def isi_agustus(sesi, site):
    # site.dibuat_pada default-nya "sekarang" (server_default), yaitu SETELAH
    # Agustus 2026 kalau test ini dijalankan di 2026-09-xx -- tanpa
    # menyetelnya ke sebelum Agustus, koreksi #6 (uptime dipotong ke
    # site.dibuat_pada) akan salah menganggap seluruh Agustus "belum
    # dipantau" untuk skenario yang justru dimaksudkan sudah terpantau penuh.
    site.dibuat_pada = datetime(2026, 7, 1, tzinfo=timezone.utc)
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


@pytest.mark.parametrize("bulan", ["2026-13", "2026-8", "abc", "2099-01", "0000-01"])
def test_bulan_tidak_sah_404(klien_web, site, bulan):
    assert klien_web.get(f"/sites/{site.id}/laporan/{bulan}").status_code == 404


def test_detail_punya_tab_traffic(klien_web, site):
    assert "pilih('traffic')" in klien_web.get(f"/sites/{site.id}").text


# --- Fix round 1: akses anonim ditolak (item 3) --------------------------

def test_laporan_menolak_tanpa_login():
    from fastapi.testclient import TestClient

    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    r = anon.get(f"/sites/{uuid.uuid4()}/laporan/2026-08")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_api_traffic_menolak_tanpa_login():
    from fastapi.testclient import TestClient

    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), base_url="https://testserver")
    assert anon.get(f"/api/sites/{uuid.uuid4()}/traffic").status_code == 401


# --- Fix round 1: asal/halaman/perangkat dibatasi di SQL (item 1) --------

def test_asal_top_15_dan_baris_lipatan(sesi, site):
    # 20 kunci "asal" berbeda, semua dari "site_lain:<host>" -- persis bentuk
    # data yang bisa membengkak dari header Referer pengunjung yang datang
    # dari domain rujukan berbeda-beda tiap hari.
    for i in range(20):
        sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                                dimensi="asal", kunci=f"site_lain:host{i:02d}.test", kunjungan=100 - i))
    sesi.add(TrafficHarian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                           kunjungan=1000, pengunjung=1))
    sesi.commit()

    hasil = ringkasan_traffic(sesi, site.id, date(2026, 8, 1), date(2026, 8, 31), "plugin")

    assert len(hasil["asal"]) == 16  # 15 teratas + 1 baris "Lainnya" terlipat
    kunci_semua = [a["kunci"] for a in hasil["asal"]]
    assert len(set(kunci_semua)) == 16  # tak ada tabrakan kunci (item 5)
    lipatan = next(a for a in hasil["asal"] if a["kunci"] == KUNCI_ASAL_LIPATAN)
    assert lipatan["kategori"] == "Lainnya"
    assert lipatan["kunjungan"] == 415  # sum(81..85), 5 kunci di luar 15 besar
    # Total SEMUA baris (15 besar + lipatan) harus persis sama dengan total
    # baris asal yang sungguhan tersimpan -- tak ada kunjungan yang hilang.
    assert sum(a["kunjungan"] for a in hasil["asal"]) == sum(100 - i for i in range(20)) == 1810


def test_asal_lipatan_bergabung_dengan_lainnya_asli(sesi, site):
    # Kunci overflow "(lainnya)" asli kebetulan cukup deras untuk masuk 15
    # besar sendiri -- sisa dari luar 15 besar harus digabung ke baris itu,
    # bukan membuat baris "Lainnya" kedua yang membingungkan pengguna.
    sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                            dimensi="asal", kunci=KUNCI_ASAL_LAINNYA, kunjungan=200))
    for i in range(20):
        sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                                dimensi="asal", kunci=f"pencarian:mesin{i:02d}", kunjungan=10 + i))
    sesi.add(TrafficHarian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                           kunjungan=1, pengunjung=1))
    sesi.commit()

    hasil = ringkasan_traffic(sesi, site.id, date(2026, 8, 1), date(2026, 8, 31), "plugin")

    kunci_semua = [a["kunci"] for a in hasil["asal"]]
    assert KUNCI_ASAL_LIPATAN not in kunci_semua  # tidak ada baris lipatan kedua
    assert len(hasil["asal"]) == 15
    baris_lainnya = next(a for a in hasil["asal"] if a["kunci"] == KUNCI_ASAL_LAINNYA)
    assert baris_lainnya["kunjungan"] == 275  # 200 asli + 75 sisa (10..15) yang tersisih
    total_semua = 200 + sum(10 + i for i in range(20))
    assert sum(a["kunjungan"] for a in hasil["asal"]) == total_semua == 590


@pytest.mark.parametrize("dimensi, batas", [("halaman", BATAS_HALAMAN), ("perangkat", BATAS_PERANGKAT)])
def test_halaman_dan_perangkat_dibatasi(sesi, site, dimensi, batas):
    for i in range(batas + 5):
        sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                                dimensi=dimensi, kunci=f"k{i:03d}", kunjungan=i + 1))
    sesi.add(TrafficHarian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                           kunjungan=1, pengunjung=1))
    sesi.commit()

    hasil = ringkasan_traffic(sesi, site.id, date(2026, 8, 1), date(2026, 8, 31), "plugin")
    assert len(hasil[dimensi]) == batas


# --- Fix round 1: uptime tidak boleh mengklaim periode sebelum site ada (item 2) ---

def test_laporan_bulan_sebelum_site_dibuat(sesi, site):
    site.dibuat_pada = datetime(2026, 9, 1, tzinfo=timezone.utc)
    site.uptime_status = UptimeStatus.naik
    sesi.commit()

    data = susun_laporan(sesi, site, 2026, 8, datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert data["uptime"]["persen"] is None


def test_laporan_site_dibuat_pertengahan_bulan(sesi, site):
    site.dibuat_pada = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)
    site.uptime_status = UptimeStatus.naik
    # Sebelum site ada -- tidak boleh ikut dihitung sama sekali.
    sesi.add(UptimeInsiden(site_id=site.id, mulai=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
                           selesai=datetime(2026, 8, 1, 10, tzinfo=timezone.utc), penyebab="Sebelum dipantau"))
    # Sesudah site ada -- dihitung penuh.
    sesi.add(UptimeInsiden(site_id=site.id, mulai=datetime(2026, 8, 20, 10, tzinfo=timezone.utc),
                           selesai=datetime(2026, 8, 20, 13, tzinfo=timezone.utc), penyebab="HTTP 500"))
    sesi.commit()

    data = susun_laporan(sesi, site, 2026, 8, datetime(2026, 9, 26, tzinfo=timezone.utc))
    # Periode terpantau: 2026-08-15 12:00 s.d. 2026-09-01 00:00 = 1.425.600 detik;
    # 3 jam (10800 detik) mati -> 100*(1 - 10800/1425600) dibulatkan 2 desimal.
    assert data["uptime"]["persen"] == 99.24
    durasi = {i["penyebab"]: i["durasi"] for i in data["uptime"]["insiden"]}
    assert durasi["Sebelum dipantau"] == "0 menit"
    assert durasi["HTTP 500"] == "3 jam"


# --- Fix round 1: koreksi #6, pemotongan durasi insiden (item 4 & 7) -----

def test_insiden_lintas_batas_bulan_dipotong_ke_bulan(sesi, site):
    site.dibuat_pada = datetime(2026, 7, 1, tzinfo=timezone.utc)
    site.uptime_status = UptimeStatus.naik
    sesi.add(UptimeInsiden(site_id=site.id, mulai=datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
                           selesai=datetime(2026, 8, 2, 6, tzinfo=timezone.utc), penyebab="Lintas bulan"))
    sesi.commit()

    data = susun_laporan(sesi, site, 2026, 8, datetime(2026, 9, 26, tzinfo=timezone.utc))
    # Hanya potongan yang jatuh di Agustus yang dihitung: 2026-08-01 00:00
    # s.d. 2026-08-02 06:00 = 1 hari 6 jam, bukan sejak 30 Juli.
    assert data["uptime"]["insiden"][0]["durasi"] == "1 hari 6 jam"
    assert data["uptime"]["durasi_mati"] == "1 hari 6 jam"
    assert data["uptime"]["persen"] == 95.97


def test_insiden_terbuka_dipotong_ke_akhir_bulan(sesi, site):
    site.dibuat_pada = datetime(2026, 7, 1, tzinfo=timezone.utc)
    site.uptime_status = UptimeStatus.mati
    sesi.add(UptimeInsiden(site_id=site.id, mulai=datetime(2026, 8, 20, 0, tzinfo=timezone.utc),
                           selesai=None, penyebab="Server down"))
    sesi.commit()

    # Laporan untuk bulan yang SUDAH LEWAT; "sekarang" jauh di depan (September)
    # -- insiden yang masih terbuka dipotong ke akhir BULAN LAPORAN, bukan ke
    # "sekarang" yang sudah di luar bulan itu.
    data = susun_laporan(sesi, site, 2026, 8, datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert data["uptime"]["insiden"][0]["durasi"] == "12 hari"


def test_insiden_bulan_berjalan_dipotong_ke_sekarang(sesi, site):
    site.dibuat_pada = datetime(2026, 9, 1, tzinfo=timezone.utc)
    site.uptime_status = UptimeStatus.mati
    sesi.add(UptimeInsiden(site_id=site.id, mulai=datetime(2026, 9, 20, 0, tzinfo=timezone.utc),
                           selesai=None, penyebab="Server down"))
    sesi.commit()

    sekarang = datetime(2026, 9, 26, 15, tzinfo=timezone.utc)
    # Bulan September MASIH BERJALAN relatif ke "sekarang" -- insiden terbuka
    # dipotong ke "sekarang", bukan ke akhir kalender bulan (yang di masa depan).
    data = susun_laporan(sesi, site, 2026, 9, sekarang)
    assert data["uptime"]["insiden"][0]["durasi"] == "6 hari 15 jam"
