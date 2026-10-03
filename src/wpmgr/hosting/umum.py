"""Bagian bersama hosting VPS (spec Lapis 4 §10).

Bagian ini: klien connector hosting LAMA. Ia dipatok ke IP lama (supaya
tarik ulang sesudah DNS berpindah tetap mengambil dari hosting lama, RF4)
dan hanya punya metode baca (supaya site lama tidak mungkin diubah).
"""

import ipaddress

import httpx

from wpmgr.config import get_settings
from wpmgr.crypto import dekripsi_secret
from wpmgr.site_client import SiteClient, buat_klien_staging
from wpmgr.staging import umum as stg

PESAN_IP_LAMA = ("Alamat IP hosting lama tidak sah (kosong, privat, atau sama dengan VPS); "
                 "batalkan pindah lalu mulai lagi.")
METODE_BACA = ("ping", "staging_manifest", "staging_file", "staging_rentang", "staging_tabel", "staging_tanda_air")


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
        return self._klien.ping()

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
    """IPv4 publik (bukan privat/loopback/dokumentasi) yang bukan IPv4 VPS sendiri."""
    if not isinstance(ip, str):
        return False
    try:
        alamat = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    if not alamat.is_global or str(alamat) != ip:
        return False
    return ip != get_settings().hosting_ipv4


def buat_http_lama() -> httpx.Client:
    """Klien httpx untuk hosting lama: tanpa keep-alive (tenggat total, putusan F11)."""
    return buat_klien_staging()


def klien_lama(site, hosting) -> KlienLamaBacaSaja:
    if not alamat_lama_sah(hosting.ip_lama):
        raise stg.galat_ditolak(PESAN_IP_LAMA)
    http = buat_http_lama()
    klien = SiteClient(site.url, str(site.id), dekripsi_secret(site.secret_terenkripsi), client=http,
                       klien_staging=http, alamat_tetap=hosting.ip_lama)
    return KlienLamaBacaSaja(klien)
