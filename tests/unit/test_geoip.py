import gzip
from datetime import date

import httpx
import pytest

from wpmgr import geoip


@pytest.fixture
def geoip_di(tmp_path, monkeypatch):
    from wpmgr.config import get_settings

    jalur = tmp_path / "geo" / "negara.mmdb"
    monkeypatch.setenv("WPMGR_GEOIP_PATH", str(jalur))
    get_settings.cache_clear()
    geoip.reset_cache()
    yield jalur
    geoip.reset_cache()


def test_tanpa_database_negara_none(geoip_di):
    assert geoip.negara("8.8.8.8") is None
    assert geoip.negara(None) is None
    assert geoip.negara("") is None


def test_database_rusak_tidak_melempar(geoip_di):
    geoip_di.parent.mkdir(parents=True)
    geoip_di.write_bytes(b"bukan mmdb")
    assert geoip.negara("8.8.8.8") is None


def test_unduh_bulan_ini(tmp_path):
    diminta = []

    def handler(request):
        diminta.append(str(request.url))
        return httpx.Response(200, content=gzip.compress(b"ISI-MMDB"))

    tujuan = tmp_path / "g" / "db.mmdb"
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        url = geoip.unduh_geoip(tujuan, date(2026, 9, 22), http)
    assert url.endswith("dbip-country-lite-2026-09.mmdb.gz")
    assert tujuan.read_bytes() == b"ISI-MMDB"
    assert diminta == [url]


def test_jatuh_ke_bulan_lalu_bila_bulan_ini_belum_terbit(tmp_path):
    def handler(request):
        if "2026-01" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=gzip.compress(b"LAMA"))

    tujuan = tmp_path / "db.mmdb"
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        url = geoip.unduh_geoip(tujuan, date(2026, 1, 3), http)
    assert url.endswith("dbip-country-lite-2025-12.mmdb.gz")
    assert tujuan.read_bytes() == b"LAMA"


def test_keduanya_tidak_ada_melempar(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as http, \
            pytest.raises(RuntimeError):
        geoip.unduh_geoip(tmp_path / "db.mmdb", date(2026, 9, 22), http)
