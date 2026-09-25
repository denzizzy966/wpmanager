import re

_SKRIP = re.compile(
    r"curl|wget|python-requests|python-urllib|go-http-client|java/|okhttp|libwww-perl|"
    r"postmanruntime|httpclient|axios/|node-fetch|scrapy",
    re.IGNORECASE,
)
# Urutan penting: Edge, Opera, dan Samsung Internet juga memuat "Chrome/";
# Chrome memuat "Safari/".
_PERAMBAN = (
    ("Edge", re.compile(r"Edg(e|A|iOS)?/")),
    ("Opera", re.compile(r"OPR/|Opera")),
    ("Samsung Internet", re.compile(r"SamsungBrowser/")),
    ("Firefox", re.compile(r"Firefox/|FxiOS/")),
    ("Chrome", re.compile(r"Chrome/|CriOS/")),
    ("Safari", re.compile(r"Version/[\d.]+.*Safari/")),
)
# iOS sebelum macOS (UA iPhone memuat "like Mac OS X"); Android sebelum Linux.
_OS = (
    ("Windows", re.compile(r"Windows NT")),
    ("iOS", re.compile(r"iPhone|iPad|iPod")),
    ("Android", re.compile(r"Android")),
    ("macOS", re.compile(r"Mac OS X|Macintosh")),
    ("Linux", re.compile(r"Linux|X11")),
)


def urai_ua(ua: str | None) -> dict:
    if not ua or not ua.strip():
        # UA kosong hampir selalu alat otomatis, bukan peramban.
        return {"peramban": "lainnya", "os": "lainnya", "skrip": True}
    peramban = next((nama for nama, pola in _PERAMBAN if pola.search(ua)), "lainnya")
    sistem = next((nama for nama, pola in _OS if pola.search(ua)), "lainnya")
    return {"peramban": peramban, "os": sistem, "skrip": bool(_SKRIP.search(ua))}
