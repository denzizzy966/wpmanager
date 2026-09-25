"""Negara dari IP memakai DB-IP Lite Country (CC BY 4.0).

File database opsional: tanpa file, negara() mengembalikan None dan fitur
lain tetap berjalan. Pembaca di-cache per (path, mtime) sehingga unduhan
bulanan terbaca tanpa me-restart proses.
"""

import gzip
import threading
from datetime import date
from pathlib import Path

import httpx
import maxminddb

from wpmgr.config import get_settings

URL_DBIP = "https://download.db-ip.com/free/dbip-country-lite-{tahun:04d}-{bulan:02d}.mmdb.gz"

_kunci = threading.Lock()
_pembaca = None
_tanda: tuple[str, float] | None = None


def reset_cache() -> None:
    global _pembaca, _tanda
    with _kunci:
        if _pembaca is not None:
            _pembaca.close()
        _pembaca = None
        _tanda = None


def _buka():
    global _pembaca, _tanda
    jalur = get_settings().jalur_geoip
    try:
        mtime = jalur.stat().st_mtime
    except OSError:
        return None
    with _kunci:
        if _tanda != (str(jalur), mtime):
            if _pembaca is not None:
                _pembaca.close()
            _pembaca = maxminddb.open_database(str(jalur))
            _tanda = (str(jalur), mtime)
        return _pembaca


def negara(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        pembaca = _buka()
        if pembaca is None:
            return None
        data = pembaca.get(ip)
    except (ValueError, OSError, maxminddb.InvalidDatabaseError):
        return None
    if not isinstance(data, dict):
        return None
    kode = (data.get("country") or {}).get("iso_code")
    return kode if isinstance(kode, str) else None


def _bulan_mundur(hari: date, mundur: int) -> tuple[int, int]:
    tahun, bulan = hari.year, hari.month - mundur
    while bulan < 1:
        bulan += 12
        tahun -= 1
    return tahun, bulan


def unduh_geoip(tujuan: Path, hari_ini: date, http: httpx.Client) -> str:
    # Berkas bulan berjalan terbit di awal bulan; pada tanggal 1-2 bisa belum ada.
    for mundur in (0, 1):
        tahun, bulan = _bulan_mundur(hari_ini, mundur)
        url = URL_DBIP.format(tahun=tahun, bulan=bulan)
        r = http.get(url)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        isi = gzip.decompress(r.content)
        tujuan.parent.mkdir(parents=True, exist_ok=True)
        sementara = tujuan.with_name(tujuan.name + ".tmp")
        sementara.write_bytes(isi)
        sementara.replace(tujuan)
        reset_cache()
        return url
    raise RuntimeError("Database DB-IP untuk bulan ini maupun bulan lalu tidak tersedia")
