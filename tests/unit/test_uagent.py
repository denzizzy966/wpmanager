import pytest

from wpmgr.uagent import urai_ua

CHROME_WIN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
EDGE = CHROME_WIN + " Edg/128.0.0.0"
SAFARI_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
SAMSUNG = ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) "
           "SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36")
SAFARI_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.5 Safari/605.1.15")


@pytest.mark.parametrize(
    ("ua", "peramban", "os_"),
    [
        (CHROME_WIN, "Chrome", "Windows"),
        (EDGE, "Edge", "Windows"),
        (SAFARI_IPHONE, "Safari", "iOS"),
        (FIREFOX_LINUX, "Firefox", "Linux"),
        (SAMSUNG, "Samsung Internet", "Android"),
        (SAFARI_MAC, "Safari", "macOS"),
    ],
)
def test_peramban_dan_os(ua, peramban, os_):
    hasil = urai_ua(ua)
    assert (hasil["peramban"], hasil["os"], hasil["skrip"]) == (peramban, os_, False)


@pytest.mark.parametrize(
    "ua",
    ["curl/8.4.0", "python-requests/2.32.3", "Go-http-client/1.1", "Wget/1.21",
     "okhttp/4.12.0", "", None, "   "],
)
def test_skrip_dan_ua_kosong(ua):
    assert urai_ua(ua)["skrip"] is True


def test_ua_asing_menjadi_lainnya():
    assert urai_ua("SesuatuYangAneh/1.0") == {"peramban": "lainnya", "os": "lainnya", "skrip": False}
