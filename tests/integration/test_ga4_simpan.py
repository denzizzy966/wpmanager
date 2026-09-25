from datetime import date, timedelta

import httpx
import pytest

from tests.unit.test_traffic_ga4 import balasan_ga
from wpmgr.models import TrafficHarian
from wpmgr.traffic import anomali_site, kumpulkan_ga4

pytestmark = pytest.mark.integration

HARI_INI = date(2026, 9, 22)


def test_ga4_disimpan_dengan_sumber_ga4(sesi, site):
    site.ga4_property_id = "123456789"
    sesi.commit()
    with httpx.Client(transport=httpx.MockTransport(balasan_ga)) as http:
        hasil = kumpulkan_ga4(sesi, "kredensial.json", HARI_INI, http=http, token_fn=lambda p: "tkn")
    assert hasil == {"berhasil": 1, "gagal": 0}
    h = sesi.get(TrafficHarian, (site.id, date(2026, 9, 21), "ga4"))
    assert h.kunjungan == 120
    sesi.refresh(site)
    assert site.ga4_error is None and site.ga4_diambil_pada is not None


def test_akses_ditolak_dicatat_di_site(sesi, site):
    site.ga4_property_id = "123456789"
    sesi.commit()
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as http:
        hasil = kumpulkan_ga4(sesi, "k.json", HARI_INI, http=http, token_fn=lambda p: "tkn")
    assert hasil == {"berhasil": 0, "gagal": 1}
    sesi.refresh(site)
    assert "Viewer" in site.ga4_error


def test_kredensial_rusak_dicatat_di_semua_site_ga4(sesi, site):
    site.ga4_property_id = "123456789"
    sesi.commit()

    def token_gagal(jalur):
        raise ValueError("file kredensial tidak valid")

    hasil = kumpulkan_ga4(sesi, "k.json", HARI_INI, token_fn=token_gagal)
    assert hasil == {"berhasil": 0, "gagal": 1}
    sesi.refresh(site)
    assert site.ga4_error.startswith("Kredensial GA4 tidak dapat dipakai")


def test_site_tanpa_property_dilewati(sesi, site):
    dipanggil = []
    hasil = kumpulkan_ga4(sesi, "k.json", HARI_INI, token_fn=lambda p: dipanggil.append(p) or "t")
    assert hasil == {"berhasil": 0, "gagal": 0}
    assert dipanggil == []


def _isi(sesi, site, sumber, nilai_per_hari):
    for mundur, n in nilai_per_hari.items():
        sesi.add(TrafficHarian(site_id=site.id, tanggal=HARI_INI - timedelta(days=mundur),
                               sumber=sumber, kunjungan=n, pengunjung=n))
    sesi.commit()


def test_anomali_memakai_plugin_lebih_dulu(sesi, site):
    _isi(sesi, site, "plugin", {1: 30, **{i: 100 for i in range(2, 16)}})
    _isi(sesi, site, "ga4", {1: 100, **{i: 100 for i in range(2, 16)}})
    assert anomali_site(sesi, site.id, HARI_INI) == "anjlok"


def test_anomali_jatuh_ke_ga4_bila_plugin_kosong(sesi, site):
    _isi(sesi, site, "ga4", {1: 900, **{i: 100 for i in range(2, 16)}})
    assert anomali_site(sesi, site.id, HARI_INI) == "melonjak"


def test_tanpa_data_tanpa_anomali(sesi, site):
    assert anomali_site(sesi, site.id, HARI_INI) is None
