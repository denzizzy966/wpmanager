import json
from datetime import date

import httpx
import pytest

from wpmgr.traffic import GalatGA4, ambil_ga4, nilai_anomali


def balasan_ga(request):
    body = json.loads(request.content)
    dimensi = [d["name"] for d in body["dimensions"]]
    assert request.headers["authorization"] == "Bearer tkn"
    assert body["dateRanges"] == [{"startDate": "2026-09-19", "endDate": "2026-09-21"}]
    if dimensi == ["date"]:
        rows = [{"dimensionValues": [{"value": "20260921"}],
                 "metricValues": [{"value": "120"}, {"value": "80"}]}]
    elif dimensi == ["date", "pagePath"]:
        rows = [{"dimensionValues": [{"value": "20260921"}, {"value": f"/p{i}"}],
                 "metricValues": [{"value": str(100 - i)}]} for i in range(60)]
    elif dimensi == ["date", "sessionDefaultChannelGroup"]:
        rows = [{"dimensionValues": [{"value": "20260921"}, {"value": "Organic Search"}],
                 "metricValues": [{"value": "30"}]},
                {"dimensionValues": [{"value": "20260921"}, {"value": "Email"}],
                 "metricValues": [{"value": "2"}]}]
    else:
        rows = [{"dimensionValues": [{"value": "20260921"}, {"value": "mobile"}],
                 "metricValues": [{"value": "90"}]}]
    return httpx.Response(200, json={"rows": rows})


def ambil(handler):
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        return ambil_ga4(http, "tkn", "123456789", date(2026, 9, 19), date(2026, 9, 21))


def test_pemetaan_ke_bentuk_traffic_plugin():
    hari = ambil(balasan_ga)
    assert len(hari) == 1
    h = hari[0]
    assert h["tanggal"] == "2026-09-21"
    assert h["total"] == {"kunjungan": 120, "pengunjung": 80}
    assert len(h["halaman"]) == 50
    assert h["halaman"]["/p0"] == 100
    assert "/p59" not in h["halaman"]
    assert h["asal"] == {"pencarian:Organic Search": 30, "lainnya:Email": 2}
    assert h["perangkat"] == {"mobile": 90}


def test_403_menjadi_galat_akses():
    with pytest.raises(GalatGA4) as exc:
        ambil(lambda r: httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}}))
    assert exc.value.jenis == "akses"
    assert "Viewer" in exc.value.pesan


def test_kuota_habis():
    with pytest.raises(GalatGA4) as exc:
        ambil(lambda r: httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}))
    assert exc.value.jenis == "kuota"


def test_galat_lain_membawa_status():
    with pytest.raises(GalatGA4) as exc:
        ambil(lambda r: httpx.Response(400, text="Invalid property"))
    assert exc.value.jenis == "lain"
    assert "400" in exc.value.pesan


@pytest.mark.parametrize(
    ("kemarin", "riwayat", "harapan"),
    [
        (100, [100] * 14, None),
        (40, [100] * 14, "anjlok"),
        (401, [100] * 14, "melonjak"),
        (400, [100] * 14, None),
        (0, [10] * 14, None),          # site sepi: terlalu acak untuk dinilai
        (10, [100] * 6, None),         # riwayat kurang dari 7 hari
        (None, [100] * 14, None),      # data kemarin tidak ada
    ],
)
def test_nilai_anomali(kemarin, riwayat, harapan):
    assert nilai_anomali(kemarin, riwayat) == harapan
