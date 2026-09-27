"""Pembungkus skrip pembantu wpmgr-staging (spec §7.3, Task 10).

Proses dashboard tidak punya akses Docker. Semua yang menyentuh container
lewat satu skrip root yang dipanggil `sudo -n`, dengan argumen yang sudah
divalidasi di sini (lapis pertama) dan divalidasi lagi di skrip (lapis
yang menentukan). Setiap panggilan punya tenggat keras, dan keluarannya
dibatasi ukurannya.

Kategori galat hanya diambil dari kode keluar skrip, tidak pernah dari teks
stderr, dan pesan untuk UI selalu teks tetap (putusan F20). Stderr mentah
hanya masuk log server, dipotong.
"""

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shlex
import signal
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import bcrypt

from wpmgr.config import Settings, get_settings
from wpmgr.staging.aman import POLA_NAMA, VERSI_PHP, angka, bersih_teks, nama_sah

log = logging.getLogger("wpmgr.staging.pembantu")

# Cermin `galat()` di deploy/staging/wpmgr-staging.
KODE_KELUAR = {2: "argumen", 3: "ditolak", 4: "docker", 5: "sertifikat", 6: "impor",
               7: "konfigurasi", 8: "wpcli", 9: "internal"}
PESAN_UMUM = {
    "argumen": "Skrip pembantu menolak argumen permintaan ini.",
    "ditolak": "Skrip pembantu menolak permintaan ini.",
    "docker": "Perintah Docker di server staging gagal.",
    "sertifikat": "Sertifikat staging belum dapat diterbitkan.",
    "impor": "Impor database staging gagal.",
    "konfigurasi": "Konfigurasi skrip pembantu di server belum lengkap.",
    "wpcli": "Perintah wp-cli di staging gagal.",
    "internal": "Skrip pembantu mengalami galat tak terduga; lihat log server.",
    "lain": "Skrip pembantu tidak dapat dijalankan (periksa pemasangan dan sudoers).",
}
# Awalan pesan UI per subperintah, supaya pengguna tahu langkah mana yang gagal
# tanpa pernah melihat stderr.
AKSI = {
    "siapkan": "Menyiapkan layanan staging",
    "buat": "Membuat container staging",
    "jalan": "Menjalankan staging",
    "jeda": "Menjeda staging",
    "hapus": "Menghapus staging",
    "db-buat": "Membuat database staging",
    "db-hapus": "Menghapus database staging",
    "db-impor": "Mengimpor database staging",
    "wpcli": "Menjalankan wp-cli di staging",
    "router-muat": "Memuat ulang router staging",
    "sertifikat": "Menerbitkan sertifikat staging",
    "status": "Membaca status server staging",
}
_POLA_PREFIX = re.compile(r"[A-Za-z0-9_]{1,20}")
_POLA_HASH = re.compile(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}")
_POLA_RAHASIA = re.compile(r"[0-9a-f]{64}")

BATAS_KELUARAN = 4 * 1024 * 1024
BATAS_STDERR = 64 * 1024
BATAS_STDERR_LOG = 2000
# Tenggat habis: SIGTERM ke grup proses sudo, tunggu sebentar, lalu SIGKILL
# (putusan F10a).
JEDA_HENTI = 5
_ADA_KILLPG = hasattr(os, "killpg")
TIMEOUT_BAWAAN = 120
TIMEOUT_SIAPKAN = 900
TIMEOUT_BUAT = 600
TIMEOUT_IMPOR = 3 * 3600
TIMEOUT_WPCLI = 900
TIMEOUT_SERTIFIKAT = 300
UMUR_TAUTAN = 12 * 3600


class GalatPembantu(Exception):
    def __init__(self, kode: str, pesan: str) -> None:
        super().__init__(pesan)
        self.kode = kode
        self.pesan = pesan


@dataclass(frozen=True)
class StatusPembantu:
    mem_tersedia: int
    disk_total: int
    disk_bebas: int
    container: dict[str, bool]
    akses: dict[str, int]


def _cek_nama(nama) -> str:
    if not nama_sah(nama):
        raise ValueError("Nama staging tidak sah")
    return nama


def _cek_id(site_id) -> str:
    teks = str(site_id)
    if str(uuid.UUID(teks)) != teks:
        raise ValueError("Id site tidak sah")
    return teks


def urai_status(teks: str) -> StatusPembantu:
    try:
        data = json.loads(teks)
    except ValueError:
        raise GalatPembantu("status", "Status server staging tidak terbaca.") from None
    if not isinstance(data, dict):
        raise GalatPembantu("status", "Status server staging tidak terbaca.")
    nilai = {}
    for kunci in ("mem_tersedia", "disk_total", "disk_bebas"):
        mentah = data.get(kunci)
        n = angka(mentah, 0, 2**53)
        if n is None or not isinstance(mentah, int) or mentah < 0:
            raise GalatPembantu("status", "Status server staging tidak lengkap.")
        nilai[kunci] = n
    container = {}
    for k, v in (data.get("container") or {}).items() if isinstance(data.get("container"), dict) else ():
        if isinstance(k, str) and k.startswith("wp-") and POLA_NAMA.fullmatch(k[3:]) and isinstance(v, dict):
            container[k[3:]] = v.get("berjalan") is True
    akses = {}
    for k, v in (data.get("akses") or {}).items() if isinstance(data.get("akses"), dict) else ():
        n = angka(v, 0, 2**40) if isinstance(v, int) else None
        if isinstance(k, str) and POLA_NAMA.fullmatch(k) and n is not None:
            akses[k] = n
    return StatusPembantu(container=container, akses=akses, **nilai)


def _pesan(subperintah: str, kode: str) -> str:
    aksi = AKSI.get(subperintah)
    return f"{aksi} gagal. {PESAN_UMUM[kode]}" if aksi else PESAN_UMUM[kode]


def _sinyal(proses: subprocess.Popen, paksa: bool) -> None:
    if proses.poll() is not None:
        return
    try:
        if _ADA_KILLPG:
            # start_new_session=True: id grup = pid pemimpinnya.
            os.killpg(proses.pid, signal.SIGKILL if paksa else signal.SIGTERM)
        elif paksa:
            proses.kill()
        else:
            proses.terminate()
    except (ProcessLookupError, PermissionError):
        pass


def _hentikan(proses: subprocess.Popen) -> None:
    """SIGTERM ke grup, tunggu `JEDA_HENTI` detik, lalu SIGKILL (putusan F10a).

    Tanpa killpg (Windows dev): terminate() lalu kill().
    """
    _sinyal(proses, paksa=False)
    try:
        proses.wait(JEDA_HENTI)
    except subprocess.TimeoutExpired:
        _sinyal(proses, paksa=True)


def _alirkan(proses: subprocess.Popen, masukan: list[Path]) -> None:
    """Alirkan berkas berurutan ke stdin; OSError hanya dilempar untuk galat baca.

    Stdin tidak ditutup di sini: bila baca gagal, pemanggil harus menghentikan
    proses dulu, karena EOF membuat skrip mengimpor potongan yang terpotong.
    """
    for berkas in masukan:
        with open(berkas, "rb") as f:
            while blok := f.read(1 << 20):
                try:
                    proses.stdin.write(blok)
                except OSError:
                    # Proses berhenti lebih dulu (pipa putus; di Windows bisa
                    # EINVAL); kode keluarnya yang menjelaskan.
                    return


class Pembantu:
    def __init__(self, perintah: list[str]) -> None:
        self.perintah = list(perintah)

    @classmethod
    def dari_setelan(cls, s: Settings | None = None) -> "Pembantu":
        s = s or get_settings()
        if s.staging_pembantu_awalan:
            return cls(shlex.split(s.staging_pembantu_awalan))
        return cls(["sudo", "-n", s.staging_pembantu])

    @staticmethod
    def _galat(subperintah: str, kode_keluar: int, stderr: str) -> GalatPembantu:
        kode = KODE_KELUAR.get(kode_keluar, "lain")
        # Stderr bisa memuat path VPS atau keluaran docker/wp-cli: hanya untuk
        # log server, dipotong. Kategorinya dari kode keluar, bukan dari teks
        # `GALAT <kode>` (dalam kasus tenggat bisa tercetak dua baris).
        log.warning("Skrip pembantu %s gagal (kode keluar %s): %s", subperintah or "-", kode_keluar,
                    bersih_teks(stderr.strip(), BATAS_STDERR_LOG))
        return GalatPembantu(kode, _pesan(subperintah, kode))

    def jalankan(self, *argumen: str, masukan: Iterable[Path] = (), timeout: float = TIMEOUT_BAWAAN) -> str:
        masukan = list(masukan)
        subperintah = argumen[0] if argumen and argumen[0] in AKSI else ""
        with tempfile.TemporaryFile() as keluar, tempfile.TemporaryFile() as galat:
            try:
                proses = subprocess.Popen(
                    [*self.perintah, *argumen],
                    stdin=subprocess.PIPE if masukan else subprocess.DEVNULL,
                    stdout=keluar, stderr=galat,
                    # Grup proses sendiri, supaya tenggat bisa menghentikan
                    # sudo beserta anaknya sekaligus (putusan F10a).
                    start_new_session=_ADA_KILLPG,
                )
            except OSError:
                # Pesan OSError memuat path biner; UI hanya mendapat pesan tetap.
                raise GalatPembantu("lain", _pesan(subperintah, "lain")) from None
            habis = threading.Event()

            def hentikan() -> None:
                habis.set()
                _hentikan(proses)

            pengawas = threading.Timer(timeout, hentikan)
            pengawas.daemon = True
            pengawas.start()
            try:
                if masukan:
                    try:
                        _alirkan(proses, masukan)
                    except OSError:
                        # Berkas masukan milik dashboard tidak terbaca: hentikan
                        # skrip sebelum stdin ditutup (lihat _alirkan).
                        _hentikan(proses)
                        log.exception("Berkas masukan skrip pembantu %s tidak terbaca", subperintah or "-")
                        raise GalatPembantu("lain", "Berkas masukan untuk skrip pembantu tidak terbaca.") from None
                    finally:
                        try:
                            proses.stdin.close()
                        except OSError:
                            pass
                kode = proses.wait()
            finally:
                pengawas.cancel()
            if habis.is_set():
                pengawas.join(JEDA_HENTI + 1)
                log.warning("Skrip pembantu %s melewati tenggat %s detik", subperintah or "-", int(timeout))
                raise GalatPembantu("waktu", f"Skrip pembantu tidak selesai dalam {int(timeout)} detik.")
            keluar.seek(0)
            teks = keluar.read(BATAS_KELUARAN).decode("utf-8", "replace")
            galat.seek(0)
            teks_galat = galat.read(BATAS_STDERR).decode("utf-8", "replace")
        if kode != 0:
            raise self._galat(subperintah, kode, teks_galat)
        return teks

    def siapkan(self) -> str:
        return self.jalankan("siapkan", timeout=TIMEOUT_SIAPKAN)

    def buat(self, nama: str, versi_php: str, site_id) -> str:
        _cek_nama(nama)
        if versi_php not in VERSI_PHP:
            raise ValueError("Versi PHP staging tidak didukung")
        return self.jalankan("buat", nama, versi_php, _cek_id(site_id), timeout=TIMEOUT_BUAT)

    def jalan(self, nama: str) -> str:
        return self.jalankan("jalan", _cek_nama(nama))

    def jeda(self, nama: str) -> str:
        return self.jalankan("jeda", _cek_nama(nama))

    def hapus(self, nama: str) -> str:
        return self.jalankan("hapus", _cek_nama(nama))

    def db_buat(self, nama: str, site_id, prefix: str) -> str:
        _cek_nama(nama)
        if not isinstance(prefix, str) or not _POLA_PREFIX.fullmatch(prefix):
            raise ValueError("Prefix tabel tidak sah")
        return self.jalankan("db-buat", nama, _cek_id(site_id), prefix)

    def db_hapus(self, nama: str) -> str:
        return self.jalankan("db-hapus", _cek_nama(nama))

    def db_impor(self, nama: str, berkas: list[Path]) -> str:
        return self.jalankan("db-impor", _cek_nama(nama), masukan=berkas, timeout=TIMEOUT_IMPOR)

    def wpcli(self, nama: str, *argumen: str) -> str:
        return self.jalankan("wpcli", _cek_nama(nama), *argumen, timeout=TIMEOUT_WPCLI)

    def router_muat(self) -> str:
        return self.jalankan("router-muat")

    def sertifikat(self, nama: str) -> str:
        return self.jalankan("sertifikat", _cek_nama(nama), timeout=TIMEOUT_SERTIFIKAT)

    def status(self) -> StatusPembantu:
        return urai_status(self.jalankan("status"))


def sandi_baru() -> str:
    return secrets.token_urlsafe(12)


def hash_sandi(sandi: str) -> str:
    return bcrypt.hashpw(sandi.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")


def _tulis_atomik(path: Path, teks: str) -> None:
    sementara = path.with_name(path.name + ".tmp")
    # Byte apa adanya: write_text() di Windows mengubah "\n" menjadi "\r\n",
    # dan skrip pembantu (Linux) menolak baris htpasswd yang berakhiran "\r".
    sementara.write_bytes(teks.encode("ascii"))
    os.chmod(sementara, 0o600)
    os.replace(sementara, path)


def tulis_akses_router(dir_staging: Path, nama: str, sandi_hash: str, rahasia: str) -> None:
    """Dua berkas yang dibaca `router-muat` (Koreksi #6); isinya divalidasi di dua sisi."""
    _cek_nama(nama)
    if not isinstance(sandi_hash, str) or not _POLA_HASH.fullmatch(sandi_hash):
        raise ValueError("Hash kata sandi tidak sah")
    if not isinstance(rahasia, str) or not _POLA_RAHASIA.fullmatch(rahasia):
        raise ValueError("Rahasia router tidak sah")
    d = Path(dir_staging) / "router"
    d.mkdir(parents=True, exist_ok=True)
    _tulis_atomik(d / f"{nama}.htpasswd", f"staging:{sandi_hash}\n")
    _tulis_atomik(d / f"{nama}.rahasia", rahasia)


def hapus_akses_router(dir_staging: Path, nama: str) -> None:
    _cek_nama(nama)
    for akhiran in (".htpasswd", ".rahasia"):
        (Path(dir_staging) / "router" / f"{nama}{akhiran}").unlink(missing_ok=True)


def _md5_tautan(rahasia: str, host: str, kedaluwarsa: int) -> str:
    # Sama persis dengan `secure_link_md5 "$secure_link_expires$host <rahasia>"`
    # di template router: base64url tanpa '='.
    inti = hashlib.md5(f"{kedaluwarsa}{host} {rahasia}".encode()).digest()
    return base64.urlsafe_b64encode(inti).decode("ascii").rstrip("=")


def tautan_masuk(rahasia: str, host: str, token_sso: str, sekarang: int, umur: int = UMUR_TAUTAN) -> str:
    e = int(sekarang) + umur
    return f"/__wpmgr_masuk?e={e}&m={_md5_tautan(rahasia, host, e)}&sso={quote(token_sso, safe='')}"


def cookie_akses(rahasia: str, host: str, sekarang: int, umur: int = UMUR_TAUTAN) -> dict[str, str]:
    e = int(sekarang) + umur
    return {"wpmgr_stg_m": _md5_tautan(rahasia, host, e), "wpmgr_stg_e": str(e)}
