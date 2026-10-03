import pytest

from wpmgr.staging.aman import domain_sah, host_dari_url, nama_prod_dari_url

STG = "staging.halosocia.my.id"


@pytest.mark.parametrize("domain", [
    "dutamakmurabadi.com", "scaffoldingsurabayamurah.com", "rizkycahayaraya.com", "toko.co.id",
    "xn--bcher-kva.de", "a-b.c-d.id", "halosocia.my.id",
])
def test_domain_sah_diterima(domain):
    assert domain_sah(domain, STG)


@pytest.mark.parametrize("domain", [
    "Toko.co.id", "toko.co.id.", "toko.co.id\n", "toko..co.id", "-toko.co.id", "toko-.co.id", "toko",
    "toko.c", "toko.1d", "toko_x.co.id", "www.toko.co.id", "a" * 64 + ".id",
    ".".join(["a" * 63] * 4) + ".id", STG, "vps-x." + STG, "", None, 123, "toko.co.id/", "toko co.id",
])
def test_domain_sah_ditolak(domain):
    assert not domain_sah(domain, STG)


def test_domain_sah_tanpa_domain_staging():
    assert domain_sah("staging.halosocia.my.id")


@pytest.mark.parametrize("url,host", [
    ("https://Toko.CO.id/", "toko.co.id"),
    ("https://www.toko.co.id", "www.toko.co.id"),
    ("https://bücher.de/", "xn--bcher-kva.de"),
    ("https://toko.co.id:8443/x", "toko.co.id"),
    ("bukan url", None),
    ("https://", None),
    ("https://" + "a" * 70 + ".id", None),
])
def test_host_dari_url(url, host):
    assert host_dari_url(url) == host


@pytest.mark.parametrize("url,nama", [
    ("https://www.toko.co.id", "toko-co-id"),
    ("https://scaffoldingsurabayamurah.com", "scaffoldingsurabayamurah-com"),
    ("https://" + "a" * 40 + ".com", "a" * 36),
    ("https://abcdefghijklmnopqrstuvwxyz-0123456789.com", "abcdefghijklmnopqrstuvwxyz-012345678"),
])
def test_nama_prod_dari_url(url, nama):
    hasil = nama_prod_dari_url(url)
    assert hasil == nama
    assert len(hasil) <= 36 and not hasil.endswith("-")
