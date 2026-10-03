"""Bagian bersama hosting VPS (spec Lapis 4 §10).

Bagian ini: klien connector hosting LAMA. Ia dipatok ke IP lama (supaya
tarik ulang sesudah DNS berpindah tetap mengambil dari hosting lama, RF4)
dan hanya punya metode baca (supaya site lama tidak mungkin diubah).
"""

import ipaddress

import httpx

from wpmgr.config import get_settings
from wpmgr.crypto import dekripsi_secret
from wpmgr.site_client import PREFIX, SiteClient, buat_klien_staging
from wpmgr.staging import umum as stg

PESAN_IP_LAMA = ("Alamat IP hosting lama tidak sah (kosong, privat, atau sama dengan VPS); "
                 "batalkan pindah lalu mulai lagi.")
PESAN_BACA_SAJA = "Klien hosting lama hanya boleh membaca; permintaan ini ditolak."
METODE_BACA = ("ping", "staging_manifest", "staging_file", "staging_rentang", "staging_tabel", "staging_tanda_air")
# Satu-satunya (metode, path) yang boleh dikirim ke hosting lama. Semuanya
# lewat `_kirim` (tenggat total + batas byte); `staging_rentang` memakai
# POST /staging/file.
RUTE_BACA = frozenset({
    ("GET", f"{PREFIX}/ping"),
    ("GET", f"{PREFIX}/staging/manifest"),
    ("POST", f"{PREFIX}/staging/file"),
    ("POST", f"{PREFIX}/staging/tabel"),
    ("GET", f"{PREFIX}/staging/tanda-air"),
})


class _SiteClientLama(SiteClient):
    """SiteClient yang menolak setiap rute di luar `RUTE_BACA` sebelum mengirim.

    Baca-saja secara struktural (review Task 5 M2): metode tulis SiteClient
    yang terpanggil lewat jalan apa pun (`k._klien.staging_terapkan(...)`)
    ditolak tanpa satu byte pun terkirim. `_panggil` (jalur Lapis 1 tanpa
    tenggat total) ditolak seluruhnya: ping memakai `ping_bertenggat`.
    """

    def _panggil(self, method: str, path: str, *argumen, **opsi) -> dict:
        raise stg.galat_ditolak(PESAN_BACA_SAJA)

    def _kirim(self, method: str, path: str, *argumen, **opsi):
        if (method, path) not in RUTE_BACA:
            raise stg.galat_ditolak(PESAN_BACA_SAJA)
        return super()._kirim(method, path, *argumen, **opsi)


class KlienLamaBacaSaja:
    """Klien connector hosting lama yang hanya bisa membaca (spec §10.1).

    Metode tulis (`staging_unggah`, `staging_terapkan`, `staging_bersihkan`,
    `update`, `self_update`) sengaja tidak ada: handler hosting yang keliru
    memanggilnya gagal dengan AttributeError, bukan mengubah site lama.
    """

    def __init__(self, klien: SiteClient) -> None:
        self._klien = klien

    @property
    def alamat(self) -> str | None:
        return self._klien.alamat_tetap

    def ping(self) -> dict:
        # Putusan L9: jalur bertenggat (tenggat total, batas byte), bukan ping Lapis 1.
        return self._klien.ping_bertenggat()

    def staging_manifest(self, kursor: str | None = None, batas: int = 5000) -> dict:
        return self._klien.staging_manifest(kursor, batas=batas)

    def staging_file(self, paths: list[str]):
        return self._klien.staging_file(paths)

    def staging_rentang(self, path: str, dari: int, panjang: int):
        return self._klien.staging_rentang(path, dari, panjang)

    def staging_tabel(self, tabel: str, kursor: str | None):
        return self._klien.staging_tabel(tabel, kursor)

    def staging_tanda_air(self, posts_sejak: str | None = None, posts_maks: int | None = None) -> dict:
        return self._klien.staging_tanda_air(posts_sejak, posts_maks)


def alamat_lama_sah(ip) -> bool:
    """IPv4 unicast publik (bukan privat/loopback/dokumentasi/multicast) yang bukan IPv4 VPS sendiri."""
    if not isinstance(ip, str):
        return False
    try:
        alamat = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    # Multicast (224.0.0.0/4) lolos `is_global` di Python 3.10.
    if not alamat.is_global or alamat.is_multicast or str(alamat) != ip:
        return False
    return ip != get_settings().hosting_ipv4


def buat_http_lama() -> httpx.Client:
    """Klien httpx untuk hosting lama: tanpa keep-alive (tenggat total, putusan F11)."""
    return buat_klien_staging()


def klien_lama(site, hosting) -> KlienLamaBacaSaja:
    if not alamat_lama_sah(hosting.ip_lama):
        raise stg.galat_ditolak(PESAN_IP_LAMA)
    http = buat_http_lama()
    klien = _SiteClientLama(site.url, str(site.id), dekripsi_secret(site.secret_terenkripsi), client=http,
                            klien_staging=http, alamat_tetap=hosting.ip_lama)
    return KlienLamaBacaSaja(klien)
