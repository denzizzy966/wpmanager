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
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import bcrypt

from wpmgr.config import Settings, get_settings
from wpmgr.staging.aman import (
    POLA_NAMA,
    POLA_NAMA_PROD,
    VERSI_PHP,
    angka,
    bersih_teks,
    domain_sah,
    nama_sah,
)

log = logging.getLogger("wpmgr.staging.pembantu")

# Cermin `galat()` di deploy/staging/wpmgr-staging.
KODE_KELUAR = {2: "argumen", 3: "ditolak", 4: "docker", 5: "sertifikat", 6: "impor",
               7: "konfigurasi", 8: "wpcli", 9: "internal", 10: "nginx", 11: "backup"}
# Keluar 3 (`galat ditolak`): skrip menolak SEBELUM mengubah apa pun, termasuk
# saat kunci router (30 s) atau kunci nginx prod-aktifkan (60 s, putusan L4)
# sedang dipegang proses lain, mis. prod-db-impor yang memegang kunci router
# sampai 3 jam. Bukan galat final: pemanggil boleh mengulang nanti.
KODE_TANPA_UBAH = "ditolak"
PESAN_UMUM = {
    "argumen": "Skrip pembantu menolak argumen permintaan ini.",
    "ditolak": "Skrip pembantu menolak permintaan ini.",
    "docker": "Perintah Docker di server staging gagal.",
    "sertifikat": "Sertifikat staging belum dapat diterbitkan.",
    "impor": "Impor database staging gagal.",
    "konfigurasi": "Konfigurasi skrip pembantu di server belum lengkap.",
    "wpcli": "Perintah wp-cli di staging gagal.",
    "internal": "Skrip pembantu mengalami galat tak terduga; lihat log server.",
    "nginx": "Konfigurasi nginx domain ditolak; site lain tidak terpengaruh.",
    "backup": "Backup situs gagal dibuat.",
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
    "mail-kredensial": "Membaca kredensial kotak email staging",
    "prod-siapkan": "Menyiapkan layanan hosting",
    "prod-buat": "Membuat container situs",
    "prod-jalan": "Menjalankan situs",
    "prod-hapus": "Menghapus situs hosting",
    "prod-db-buat": "Membuat database situs",
    "prod-db-impor": "Mengimpor database situs",
    "prod-router-muat": "Memuat ulang router hosting",
    "prod-domain": "Memasang konfigurasi nginx domain",
    "prod-sertifikat": "Menerbitkan sertifikat domain",
    "prod-aktifkan": "Mengaktifkan situs",
    "prod-backup": "Membuat backup situs",
    "prod-backup-hapus": "Menghapus backup situs",
    "prod-status": "Membaca status server hosting",
}
_POLA_PREFIX = re.compile(r"[A-Za-z0-9_]{1,20}")
_POLA_HASH = re.compile(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}")
_POLA_RAHASIA = re.compile(r"[0-9a-f]{64}")
_POLA_KREDENSIAL_MAIL = re.compile(r"wpmgr:[0-9a-f]{48}")
_POLA_STEMPEL = re.compile(r"[0-9]{8}T[0-9]{6}Z")
# Keluaran prod-sertifikat: satu kata dari daftar tetap (spec §7.3.3).
HASIL_SERTIFIKAT = frozenset({"terbit", "tetap", "diperbarui"})
PENGGUNA_PRATINJAU = "pratinjau"

# Keluaran wp-cli dikendalikan kode salinan site yang bisa saja disusupi:
# stdout dan stderr ditampung di memori sampai batas ini saja, tidak pernah ke
# disk, dan skrip dihentikan begitu batasnya terlewati.
BATAS_KELUARAN = 4 * 1024 * 1024
BATAS_STDERR = 64 * 1024
BATAS_STDERR_LOG = 2000
PESAN_MELIMPAH = "Keluaran skrip pembantu melebihi batas; perintah dihentikan."
PESAN_TIDAK_TUNTAS = "Keluaran skrip pembantu tidak tuntas terbaca; hasilnya diabaikan."
_BLOK = 64 * 1024
# Tenggat habis: SIGTERM ke grup proses sudo, tunggu sebentar, lalu SIGKILL
# (putusan F10a).
JEDA_HENTI = 5
# Sesudah urutan henti: batas menunggu proses dan pembaca pipa. Anak root di
# bawah sudo bisa tetap memegang pipa; mereka ditinggalkan, bukan ditunggu.
TENGGANG_AKHIR = 2
_ADA_KILLPG = hasattr(os, "killpg")
TIMEOUT_BAWAAN = 120
TIMEOUT_SIAPKAN = 900
TIMEOUT_BUAT = 600
TIMEOUT_IMPOR = 3 * 3600
TIMEOUT_WPCLI = 900
TIMEOUT_SERTIFIKAT = 300
TIMEOUT_AKTIFKAN = 300
TIMEOUT_BACKUP = 3 * 3600
# prod-domain dan prod-hapus: kunci router (prod-hapus, 30 s) dan kunci nginx
# (60 s) bisa menghabiskan 90 s sebelum pekerjaan dimulai, jadi bawaan 120 s
# terlalu sempit. Skrip memakai WAKTU_NGINX = 300 + 60 (preflight M13).
TIMEOUT_NGINX = 300
UMUR_TAUTAN = 12 * 3600


class GalatPembantu(Exception):
    def __init__(self, kode: str, pesan: str) -> None:
        super().__init__(pesan)
        self.kode = kode
        self.pesan = pesan

    @property
    def tanpa_ubah(self) -> bool:
        """Skrip menolak tanpa mengubah apa pun (keluar 3), termasuk "sedang sibuk".

        Pemanggil job memperlakukannya sebagai boleh diulang nanti (R15,
        `GalatDitolakTanpaUbah`), bukan galat final. Kode keluar lain tidak
        menjamin tanpa perubahan, termasuk `nginx` (10) yang juga dipakai saat
        kunci nginx prod-domain/prod-sertifikat/prod-hapus sibuk.
        """
        return self.kode == KODE_TANPA_UBAH


@dataclass(frozen=True)
class StatusPembantu:
    mem_tersedia: int
    disk_total: int
    disk_bebas: int
    container: dict[str, bool]
    akses: dict[str, int]


@dataclass(frozen=True)
class StatusProd:
    mem_tersedia: int
    disk_total: int
    disk_bebas: int
    backup_total: int
    backup_bebas: int
    container: dict[str, bool]


def _cek_nama(nama) -> str:
    if not nama_sah(nama):
        raise ValueError("Nama staging tidak sah")
    return nama


def _cek_id(site_id) -> str:
    teks = str(site_id)
    if str(uuid.UUID(teks)) != teks:
        raise ValueError("Id site tidak sah")
    return teks


def _cek_nama_prod(nama) -> str:
    if not isinstance(nama, str) or not POLA_NAMA_PROD.fullmatch(nama):
        raise ValueError("Nama situs tidak sah")
    return nama


def _cek_domain(domain) -> str:
    if not domain_sah(domain, get_settings().staging_domain):
        raise ValueError("Domain tidak sah")
    return domain


def _cek_stempel(stempel) -> str:
    if not isinstance(stempel, str) or not _POLA_STEMPEL.fullmatch(stempel):
        raise ValueError("Stempel backup tidak sah")
    return stempel


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


def urai_status_prod(teks: str) -> StatusProd:
    """JSON `prod-status`; angka wajib bilangan bulat >= 0, container disaring nama sah."""
    try:
        data = json.loads(teks)
    except ValueError:
        raise GalatPembantu("status", "Status server hosting tidak terbaca.") from None
    if not isinstance(data, dict):
        raise GalatPembantu("status", "Status server hosting tidak terbaca.")
    nilai = {}
    for kunci in ("mem_tersedia", "disk_total", "disk_bebas", "backup_total", "backup_bebas"):
        mentah = data.get(kunci)
        # `angka` menjepit nilai negatif ke 0; di sini nilai negatif berarti
        # keluaran rusak, jadi ditolak lebih dulu.
        if not isinstance(mentah, int) or isinstance(mentah, bool) or mentah < 0:
            raise GalatPembantu("status", "Status server hosting tidak lengkap.")
        nilai[kunci] = min(mentah, 2**53)
    container = {}
    mentah_c = data.get("container")
    for k, v in mentah_c.items() if isinstance(mentah_c, dict) else ():
        if isinstance(k, str) and POLA_NAMA_PROD.fullmatch(k) and isinstance(v, dict):
            container[k] = v.get("berjalan") is True
    return StatusProd(container=container, **nilai)


def _pesan(subperintah: str, kode: str) -> str:
    aksi = AKSI.get(subperintah)
    return f"{aksi} gagal. {PESAN_UMUM[kode]}" if aksi else PESAN_UMUM[kode]


def _stderr_log(stderr: str) -> str:
    # Stderr bisa memuat path VPS atau keluaran docker/wp-cli: hanya untuk log
    # server, satu baris (baris baru di-escape supaya tidak bisa memalsukan
    # entri log), dipotong.
    satu_baris = stderr.strip().replace("\r", "\\r").replace("\n", "\\n")
    return bersih_teks(satu_baris, BATAS_STDERR_LOG) or ""


class _Penampung:
    """Menyimpan paling banyak `batas` byte pertama sebuah aliran; sisanya dibuang."""

    def __init__(self, batas: int) -> None:
        self.batas = batas
        self.data = bytearray()
        self.melimpah = False

    def tambah(self, blok: bytes) -> bool:
        """True tepat sekali: saat batas pertama kali terlewati."""
        sisa = self.batas - len(self.data)
        if len(blok) <= sisa:
            self.data += blok
            return False
        self.data += blok[:max(sisa, 0)]
        baru = not self.melimpah
        self.melimpah = True
        return baru


def _baca_aliran(aliran, penampung: _Penampung, saat_melimpah) -> None:
    try:
        while blok := aliran.read(_BLOK):
            if penampung.tambah(blok):
                saat_melimpah()
    except (OSError, ValueError):
        pass


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


def _tulis_penuh(stdin, blok: bytes) -> None:
    tampilan = memoryview(blok)
    while tampilan:
        n = stdin.write(tampilan)
        tampilan = tampilan[n or 0:]


def _alirkan(stdin, masukan: list[Path], berhenti: threading.Event) -> None:
    """Alirkan berkas berurutan ke stdin sampai selesai atau `berhenti` diset.

    OSError hanya dilempar untuk galat baca berkas. Stdin tidak ditutup di
    sini: bila baca gagal, pemanggil menghentikan proses dulu, supaya anak
    langsungnya (sudo/skrip) tidak melihat EOF lalu mengimpor potongan yang
    terpotong. Anak root di bawahnya tidak terjangkau sinyal pengguna
    dashboard; untuk mereka skrip pembantu sendiri meneruskan SIGTERM.
    """
    for berkas in masukan:
        with open(berkas, "rb") as f:
            while blok := f.read(_BLOK):
                if berhenti.is_set():
                    return
                try:
                    _tulis_penuh(stdin, blok)
                except OSError:
                    # Proses berhenti lebih dulu (pipa putus; di Windows bisa
                    # EINVAL); kode keluarnya yang menjelaskan.
                    return


def _tutup(berkas) -> None:
    try:
        berkas.close()
    except OSError:
        pass


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
        # Kategorinya dari kode keluar, bukan dari teks `GALAT <kode>`: dalam
        # kasus tenggat bisa tercetak dua baris (galat() menulis ke stderr asli).
        log.warning("Skrip pembantu %s gagal (kode keluar %s): %s", subperintah or "-", kode_keluar,
                    _stderr_log(stderr))
        return GalatPembantu(kode, _pesan(subperintah, kode))

    def jalankan(self, *argumen: str, masukan: Iterable[Path] = (), timeout: float = TIMEOUT_BAWAAN,
                 catat_stderr: bool = False) -> str:
        """Jalankan satu subperintah dan kembalikan stdout-nya.

        `catat_stderr`: stderr perintah yang BERHASIL juga dicatat ke log
        server (mis. PERINGATAN certbot delete di prod-hapus, putusan L6).
        """
        masukan = list(masukan)
        subperintah = argumen[0] if argumen and argumen[0] in AKSI else ""
        mulai = time.monotonic()
        try:
            proses = subprocess.Popen(
                [*self.perintah, *argumen],
                stdin=subprocess.PIPE if masukan else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                # Tanpa buffer: tulisan yang terblokir tidak memegang kunci
                # BufferedWriter, dan baca mengembalikan apa yang ada.
                bufsize=0,
                # Grup proses sendiri, supaya tenggat bisa menghentikan
                # sudo beserta anaknya sekaligus (putusan F10a).
                start_new_session=_ADA_KILLPG,
            )
        except OSError:
            # Pesan OSError memuat path biner; UI hanya mendapat pesan tetap.
            raise GalatPembantu("lain", _pesan(subperintah, "lain")) from None

        sebab: list[str] = []
        kunci = threading.Lock()
        berhenti = threading.Event()

        def hentikan(alasan: str) -> None:
            with kunci:
                if sebab:
                    return
                sebab.append(alasan)
            berhenti.set()
            _hentikan(proses)

        def melimpah() -> None:
            # Dari thread pembaca: jangan menunggu JEDA_HENTI di sana, pembaca
            # harus tetap menguras pipa supaya skrip bisa menerima sinyalnya.
            threading.Thread(target=hentikan, args=("keluaran",), daemon=True).start()

        keluar, galat = _Penampung(BATAS_KELUARAN), _Penampung(BATAS_STDERR)
        pembaca = [
            threading.Thread(target=_baca_aliran, args=(proses.stdout, keluar, melimpah), daemon=True),
            threading.Thread(target=_baca_aliran, args=(proses.stderr, galat, melimpah), daemon=True),
        ]
        for t_ in pembaca:
            t_.start()
        pengawas = threading.Timer(timeout, hentikan, ("waktu",))
        pengawas.daemon = True
        pengawas.start()
        try:
            if masukan:
                self._suapi(proses, masukan, berhenti, hentikan)
            try:
                kode = proses.wait(max(0.0, mulai + timeout + JEDA_HENTI + TENGGANG_AKHIR - time.monotonic()))
            except subprocess.TimeoutExpired:
                # Tidak mati juga sesudah SIGKILL (mis. sudo tak terjangkau).
                hentikan("waktu")
                sebab[:] = ["waktu"]
                kode = None
        finally:
            pengawas.cancel()
        batas_pembaca = time.monotonic() + TENGGANG_AKHIR
        for t_ in pembaca:
            t_.join(max(0.0, batas_pembaca - time.monotonic()))
        # Pipa hanya ditutup bila pembacanya sudah selesai: menutup fd yang
        # sedang dibaca thread lain berisiko nomor fd dipakai ulang.
        for t_, aliran in zip(pembaca, (proses.stdout, proses.stderr)):
            if not t_.is_alive():
                _tutup(aliran)
        if sebab:
            raise self._galat_berhenti(sebab[0], subperintah, timeout)
        if keluar.melimpah or galat.melimpah:
            raise self._galat_berhenti("keluaran", subperintah, timeout)
        if kode != 0:
            raise self._galat(subperintah, kode, galat.data.decode("utf-8", "replace"))
        if pembaca[0].is_alive():
            # Keluar 0, tetapi stdout masih dipegang proses lain (mis. anak
            # root yang tertinggal): keluarannya belum tentu lengkap, jadi
            # tidak boleh diperlakukan sebagai hasil (mis. JSON status/plugin).
            log.warning("Keluaran skrip pembantu %s tidak tuntas dibaca dalam %s detik",
                        subperintah or "-", TENGGANG_AKHIR)
            raise GalatPembantu("lain", PESAN_TIDAK_TUNTAS)
        if catat_stderr and galat.data.strip():
            log.warning("Skrip pembantu %s berhasil dengan pesan: %s", subperintah or "-",
                        _stderr_log(galat.data.decode("utf-8", "replace")))
        return keluar.data.decode("utf-8", "replace")

    @staticmethod
    def _suapi(proses: subprocess.Popen, masukan: list[Path], berhenti: threading.Event, hentikan) -> None:
        """Alirkan masukan lewat thread penulis yang bisa ditinggalkan.

        Tulisan ke pipa yang tidak dibaca (anak root `docker exec -i` yang
        tidak terjangkau sinyal) bisa terblokir selamanya; thread utama hanya
        menunggu sampai tenggat/henti lalu meninggalkannya. Hanya thread penulis
        yang menutup stdin, kecuali saat ia sudah selesai karena galat baca.
        """
        galat_baca: list[OSError] = []

        def menulis() -> None:
            try:
                _alirkan(proses.stdin, masukan, berhenti)
            except OSError as e:
                galat_baca.append(e)
                return
            _tutup(proses.stdin)

        penulis = threading.Thread(target=menulis, daemon=True)
        penulis.start()
        # Berhenti menunggu juga bila anak langsung sudah keluar: pipa bisa
        # masih dipegang cucunya, dan kode keluarnya yang menjelaskan.
        while penulis.is_alive() and not berhenti.is_set() and proses.poll() is None:
            penulis.join(0.1)
        if galat_baca:
            # Berkas masukan milik dashboard tidak terbaca: hentikan skrip
            # sebelum stdin ditutup (lihat _alirkan).
            hentikan("baca")
            _tutup(proses.stdin)
            log.error("Berkas masukan skrip pembantu tidak terbaca: %s", galat_baca[0])

    @staticmethod
    def _galat_berhenti(alasan: str, subperintah: str, timeout: float) -> GalatPembantu:
        nama = subperintah or "-"
        if alasan == "waktu":
            log.warning("Skrip pembantu %s melewati tenggat %s detik", nama, int(timeout))
            return GalatPembantu("waktu", f"Skrip pembantu tidak selesai dalam {int(timeout)} detik.")
        if alasan == "baca":
            return GalatPembantu("lain", "Berkas masukan untuk skrip pembantu tidak terbaca.")
        log.warning("Keluaran skrip pembantu %s melebihi batas; proses dihentikan", nama)
        return GalatPembantu("lain", PESAN_MELIMPAH)

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

    def mail_kredensial(self) -> tuple[str, str]:
        """(pengguna, sandi) Basic Auth Mailpit yang dibuat `siapkan` (putusan R25)."""
        teks = self.jalankan("mail-kredensial").strip()
        if not _POLA_KREDENSIAL_MAIL.fullmatch(teks):
            raise GalatPembantu("lain", PESAN_TIDAK_TUNTAS)
        pengguna, _, sandi = teks.partition(":")
        return pengguna, sandi

    # ---- produksi (Lapis 4, spec §7.3.3) -------------------------------------

    def prod_siapkan(self) -> str:
        return self.jalankan("prod-siapkan", timeout=TIMEOUT_SIAPKAN)

    def prod_buat(self, nama: str, versi_php: str, site_id, domain: str, www: bool) -> str:
        _cek_nama_prod(nama)
        if versi_php not in VERSI_PHP:
            raise ValueError("Versi PHP situs tidak didukung")
        # Hanya bool sungguhan: "1", 1, atau None bukan penanda yang sah, dan
        # galat argumen apa pun sebelum subprocess selalu ValueError.
        if www is not True and www is not False:
            raise ValueError("Penanda www tidak sah")
        return self.jalankan("prod-buat", nama, versi_php, _cek_id(site_id), _cek_domain(domain),
                             "1" if www else "0", timeout=TIMEOUT_BUAT)

    def prod_jalan(self, nama: str) -> str:
        return self.jalankan("prod-jalan", _cek_nama_prod(nama))

    def prod_hapus(self, nama: str) -> str:
        # Sukses pun bisa membawa PERINGATAN (lineage certbot tidak terhapus,
        # putusan L6): stderr-nya dicatat supaya pengelola bisa membersihkan manual.
        return self.jalankan("prod-hapus", _cek_nama_prod(nama), timeout=TIMEOUT_NGINX, catat_stderr=True)

    def prod_db_buat(self, nama: str, prefix: str) -> str:
        _cek_nama_prod(nama)
        if not isinstance(prefix, str) or not _POLA_PREFIX.fullmatch(prefix):
            raise ValueError("Prefix tabel tidak sah")
        return self.jalankan("prod-db-buat", nama, prefix)

    def prod_db_impor(self, nama: str, berkas: list[Path]) -> str:
        return self.jalankan("prod-db-impor", _cek_nama_prod(nama), masukan=berkas, timeout=TIMEOUT_IMPOR)

    def prod_router_muat(self) -> str:
        return self.jalankan("prod-router-muat")

    def prod_domain(self, nama: str) -> str:
        return self.jalankan("prod-domain", _cek_nama_prod(nama), timeout=TIMEOUT_NGINX)

    def prod_sertifikat(self, nama: str) -> str:
        teks = self.jalankan("prod-sertifikat", _cek_nama_prod(nama), timeout=TIMEOUT_SERTIFIKAT).strip()
        if teks not in HASIL_SERTIFIKAT:
            raise GalatPembantu("lain", PESAN_TIDAK_TUNTAS)
        return teks

    def prod_aktifkan(self, nama: str) -> str:
        return self.jalankan("prod-aktifkan", _cek_nama_prod(nama), timeout=TIMEOUT_AKTIFKAN)

    def prod_backup(self, nama: str, stempel: str) -> str:
        return self.jalankan("prod-backup", _cek_nama_prod(nama), _cek_stempel(stempel), timeout=TIMEOUT_BACKUP)

    def prod_backup_hapus(self, nama: str, stempel: str) -> str:
        return self.jalankan("prod-backup-hapus", _cek_nama_prod(nama), _cek_stempel(stempel))

    def prod_status(self) -> StatusProd:
        return urai_status_prod(self.jalankan("prod-status"))


def sandi_baru() -> str:
    return secrets.token_urlsafe(12)


def hash_sandi(sandi: str) -> str:
    return bcrypt.hashpw(sandi.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")


def _tulis_atomik(path: Path, teks: str) -> None:
    # Nama sementara acak dengan O_EXCL|O_NOFOLLOW: nama tetap yang sudah
    # berupa symlink tidak pernah diikuti. O_BINARY supaya "\n" tidak menjadi
    # "\r\n" di Windows; skrip pembantu (Linux) menolak baris yang berakhiran "\r".
    sementara = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    bendera = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
               | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0))
    fd = os.open(sementara, bendera, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(teks.encode("ascii"))
        os.replace(sementara, path)
    except BaseException:
        sementara.unlink(missing_ok=True)
        raise


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


def tulis_htpasswd_pratinjau(dir_hosting: Path, nama: str, sandi_hash: str) -> None:
    """Satu baris htpasswd pratinjau yang dibaca `prod-router-muat` (spec §10.2); divalidasi di dua sisi."""
    _cek_nama_prod(nama)
    if not isinstance(sandi_hash, str) or not _POLA_HASH.fullmatch(sandi_hash):
        raise ValueError("Hash kata sandi tidak sah")
    d = Path(dir_hosting) / "router"
    d.mkdir(parents=True, exist_ok=True)
    _tulis_atomik(d / f"{nama}.htpasswd", f"{PENGGUNA_PRATINJAU}:{sandi_hash}\n")


def hapus_htpasswd_pratinjau(dir_hosting: Path, nama: str) -> None:
    _cek_nama_prod(nama)
    (Path(dir_hosting) / "router" / f"{nama}.htpasswd").unlink(missing_ok=True)


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
