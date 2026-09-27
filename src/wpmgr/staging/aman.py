"""Validasi dan pembersihan untuk staging (spec §13).

Semua yang datang dari connector -- path, nama tabel, angka, teks -- adalah
masukan penyerang: site produksi bisa saja sudah disusupi. Aturan path di
sini adalah cermin WPMGR_Staging_Path di connector; keduanya harus tetap
sama ketatnya.

Direktori `files/`, `ekspor/`, dan `log/` staging di-bind mount ke container
yang menjalankan kode salinan produksi sebagai UID dashboard (putusan R13).
Proses PHP di sana bisa menukar path apa pun di pohon itu dengan symlink kapan
saja, termasuk di sela pemeriksaan dan pemakaian. Karena itu dashboard hanya
membaca/menulis di pohon itu lewat `buka_baca`, `baca_terbatas`, `tulis_bertahap`,
`tulis_atomik`, dan `hapus_berkas` (putusan F1): setiap komponen dibuka
tanpa mengikuti symlink, dan hanya berkas biasa yang dibaca.
"""

import errno
import os
import re
import stat
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

POLA_NAMA = re.compile(r"[a-z0-9-]{1,40}")
POLA_ID_DORONG = re.compile(r"[0-9a-f]{32}")
POLA_TABEL = re.compile(r"[A-Za-z0-9_$]{1,64}")
POLA_SHA256 = re.compile(r"[0-9a-f]{64}")
_POLA_ANGKA = re.compile(r"-?[0-9]{1,20}")
_POLA_VERSI = re.compile(r"([0-9]{1,2})\.([0-9]{1,2})")
_KENDALI = re.compile(r"[\x00-\x1f\x7f\\]")
_POLA_WP_AKAR = re.compile(r"wp-[a-z0-9-]+\.php")

VERSI_PHP = ("7.4", "8.0", "8.1", "8.2", "8.3")
VERSI_PHP_BAWAAN = "8.1"
MAKS_PATH = 1024
MAKS_SEGMEN = 255
BACKUP_KONTEN = frozenset({"updraft", "ai1wm-backups", "wpvividbackups"})
# Berkas milik staging sendiri: tidak pernah dihapus/ditimpa saat tarik.
DILINDUNGI_STAGING = frozenset({"wp-config.php", "wp-content/mu-plugins/wpmgr-staging.php"})
TIDAK_PERNAH_DITULIS = frozenset({
    "wp-config.php", ".maintenance",
    "wp-content/mu-plugins/wpmgr-staging.php", "wp-content/mu-plugins/wpmgr-dorong-aman.php",
})
AKAR_INTI = frozenset({"index.php", "xmlrpc.php", "license.txt", "readme.html", ".htaccess"})
# `log/diubah` hanya memuat satu stempel waktu; container bisa mengisinya
# dengan apa saja (atau menautkannya ke /dev/zero), jadi pembacaannya dibatasi.
BATAS_DIUBAH = 64 * 1024

# Linux (produksi) punya O_NOFOLLOW, O_DIRECTORY, dan dir_fd: setiap komponen
# dibuka relatif terhadap deskriptor induknya, sehingga symlink yang ditukar
# di sela langkah tidak pernah diikuti. Windows (dev) tidak punya ketiganya;
# di sana dipakai pemeriksaan lstat per komponen, yang cukup untuk dev tetapi
# tidak kebal balapan.


def _dukung_dir_fd(o=os, platform: str = sys.platform) -> bool:
    ada = (
        hasattr(o, "O_NOFOLLOW") and hasattr(o, "O_DIRECTORY")
        and o.open in o.supports_dir_fd and o.mkdir in o.supports_dir_fd
        # os.replace tidak tercantum di supports_dir_fd; os.rename dengan dir_fd
        # di POSIX sama-sama menimpa secara atomik (renameat).
        and o.unlink in o.supports_dir_fd and o.rename in o.supports_dir_fd
        and o.stat in o.supports_dir_fd and o.stat in o.supports_follow_symlinks
        and o.utime in o.supports_fd
    )
    if not ada and platform.startswith("linux"):
        # Produksi berjalan di Linux: cadangan lstat bisa kalah balapan dengan
        # container, jadi lebih baik gagal keras daripada diam-diam kurang aman.
        raise RuntimeError("Python ini tidak mendukung O_NOFOLLOW/dir_fd; akses berkas staging tidak aman")
    return ada


_ADA_DIR_FD = _dukung_dir_fd()
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_BINER = getattr(os, "O_BINARY", 0)
# O_NONBLOCK: membuka FIFO untuk dibaca tanpa ini menggantung selamanya.
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class PathTidakAman(ValueError):
    pass


def nama_sah(nama) -> bool:
    return isinstance(nama, str) and POLA_NAMA.fullmatch(nama) is not None


def nama_dari_url(url: str) -> str:
    """Label subdomain staging dari host produksi (spec §5.1)."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        host = ""
    host = host.removeprefix("www.")
    nama = re.sub(r"[^a-z0-9-]+", "-", host.replace(".", "-"))
    nama = re.sub(r"-{2,}", "-", nama).strip("-")[:40].strip("-")
    return nama or "situs"


def versi_php_staging(versi: str | None) -> tuple[str, bool]:
    """Versi image staging: sama dengan produksi, atau terdekat yang lebih tinggi (spec §7.1)."""
    cocok = _POLA_VERSI.match(versi or "")
    if cocok is None:
        return VERSI_PHP_BAWAAN, True
    diminta = (int(cocok.group(1)), int(cocok.group(2)))
    for v in VERSI_PHP:
        besar, kecil = (int(x) for x in v.split("."))
        if (besar, kecil) == diminta:
            return v, False
        if (besar, kecil) > diminta:
            return v, True
    return VERSI_PHP[-1], True


def bersih_teks(nilai, panjang: int) -> str | None:
    """Teks yang aman disimpan di kolom text PostgreSQL.

    PostgreSQL menolak NUL, dan psycopg menolak surrogate tunggal (bisa
    dikirim lewat escape \\ud800 di JSON). Keduanya dibuang di sini, sebelum
    dipotong, supaya satu nilai beracun tidak menggagalkan seluruh commit.
    """
    if nilai is None:
        return None
    teks = nilai if isinstance(nilai, str) else str(nilai)
    teks = teks.encode("utf-8", "replace").decode("utf-8").replace("\x00", "")
    return teks[:panjang]


def bersih_json(nilai, panjang: int = 2000):
    """Salinan nilai JSON dengan setiap teks (termasuk kunci) lewat `bersih_teks`.

    JSONB menolak \\u0000 sama seperti kolom text, jadi detail log yang memuat
    teks dari connector dibersihkan dengan cara yang sama.
    """
    if isinstance(nilai, str):
        return bersih_teks(nilai, panjang)
    if isinstance(nilai, dict):
        return {bersih_teks(k, panjang): bersih_json(v, panjang) for k, v in nilai.items()}
    if isinstance(nilai, (list, tuple)):
        return [bersih_json(v, panjang) for v in nilai]
    return nilai


def angka(nilai, bawah: int, atas: int) -> int | None:
    if isinstance(nilai, bool):
        return None
    if isinstance(nilai, int):
        n = nilai
    elif isinstance(nilai, str) and _POLA_ANGKA.fullmatch(nilai):
        n = int(nilai)
    else:
        return None
    return max(bawah, min(atas, n))


_POLA_DETIK = re.compile(r"[0-9]{1,12}")
# Jam VPS dan jam container staging sama (satu mesin); lima menit hanya
# menampung penulisan yang berlangsung tepat saat dibaca.
TOLERANSI_JAM = timedelta(minutes=5)


def waktu_penanda(mentah: bytes, kini: datetime) -> datetime | None:
    """Stempel waktu Unix dari penanda `log/diubah`, atau None bila tidak masuk akal.

    Penanda ditulis kode di container staging, jadi nilainya tidak
    dipercaya. Nilai di luar [epoch, kini + 5 menit] ditolak, bukan dijepit:
    penanda masa depan yang tersimpan akan mengalahkan setiap perubahan asli
    sesudahnya. Batasnya dihitung dari `kini`, bukan dari rentang datetime,
    yang berbeda antara Linux (sampai tahun 9999) dan Windows (sampai 3000).
    """
    teks = mentah.decode("ascii", errors="replace").strip()
    if not _POLA_DETIK.fullmatch(teks):
        return None
    detik = int(teks)
    if detik > (kini + TOLERANSI_JAM).timestamp():
        return None
    return datetime.fromtimestamp(detik, tz=timezone.utc)


def path_sah(p) -> str:
    if not isinstance(p, str) or not p:
        raise PathTidakAman("Path kosong atau bukan teks")
    try:
        mentah = p.encode("utf-8")
    except UnicodeEncodeError:
        raise PathTidakAman("Path bukan UTF-8 yang sah") from None
    if len(mentah) > MAKS_PATH:
        raise PathTidakAman("Path terlalu panjang")
    if _KENDALI.search(p):
        raise PathTidakAman("Path memuat karakter terlarang")
    if p.startswith("/") or re.match(r"[A-Za-z]:", p):
        raise PathTidakAman("Path absolut ditolak")
    for segmen in p.split("/"):
        if segmen in ("", ".", ".."):
            raise PathTidakAman("Path memuat segmen terlarang")
        if len(segmen.encode("utf-8")) > MAKS_SEGMEN:
            raise PathTidakAman("Nama berkas terlalu panjang")
    return p


def dikecualikan(p: str) -> bool:
    if p in ("wp-config.php", ".maintenance") or p.lower().endswith(".log"):
        return True
    for d in ("wp-content/cache", "wp-content/wpmgr-dorong"):
        if p == d or p.startswith(d + "/"):
            return True
    bagian = p.split("/")
    return len(bagian) >= 2 and bagian[0] == "wp-content" and (
        bagian[1] in BACKUP_KONTEN or bagian[1].startswith("backups-dup-")
    )


def boleh_didorong(p) -> bool:
    try:
        path_sah(p)
    except PathTidakAman:
        return False
    if dikecualikan(p) or p in TIDAK_PERNAH_DITULIS:
        return False
    if p.startswith("wp-content/plugins/wp-manager-connector/"):
        return False
    if p.startswith(("wp-content/", "wp-admin/", "wp-includes/")):
        return True
    if "/" in p:
        return False
    return p in AKAR_INTI or _POLA_WP_AKAR.fullmatch(p) is not None


def jalur_di_dalam(akar: Path, relatif: str) -> Path:
    """Path tulis di bawah `akar`, tidak pernah lewat symlink dan tidak pernah keluar.

    Hanya pemeriksaan sesaat: di pohon yang di-bind mount ke container, baca
    dan tulis tetap lewat `buka_baca`/`tulis_atomik`/`hapus_berkas`, yang
    tidak memberi celah antara periksa dan pakai.
    """
    path_sah(relatif)
    bagian = relatif.split("/")
    sekarang = akar
    for b in bagian:
        sekarang = sekarang / b
        if sekarang.is_symlink():
            raise PathTidakAman("Path melewati symlink")
    akar_nyata = Path(os.path.realpath(akar))
    induk = Path(os.path.realpath(sekarang.parent))
    if induk != akar_nyata and akar_nyata not in induk.parents:
        raise PathTidakAman("Path keluar dari akar staging")
    return sekarang


# ---- akses berkas di pohon yang di-bind mount (putusan F1) ----------------


def _tautan(st: os.stat_result) -> bool:
    # Junction Windows bukan S_ISLNK tetapi sama berbahayanya.
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _buka_dir(nama: str, dir_fd: int | None = None) -> int:
    try:
        return os.open(nama, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=dir_fd)
    except OSError as e:
        # ELOOP: komponen itu symlink. ENOTDIR: bukan direktori (atau symlink,
        # tergantung kernel). Keduanya berarti path ini tidak boleh dilalui.
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise PathTidakAman("Path melewati symlink atau bukan direktori") from None
        raise


def _fd_induk(akar: Path, bagian: list[str], buat: bool) -> int:
    """Deskriptor direktori `akar/bagian...`, setiap komponen dibuka tanpa mengikuti symlink.

    `akar` sendiri juga dibuka dengan O_NOFOLLOW; path di atasnya (direktori
    staging milik dashboard/root) berada di luar jangkauan container.
    """
    fd = _buka_dir(os.fspath(akar))
    try:
        for b in bagian:
            if buat:
                try:
                    os.mkdir(b, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
            baru = _buka_dir(b, fd)
            os.close(fd)
            fd = baru
    except BaseException:
        os.close(fd)
        raise
    return fd


def _periksa_jalur(akar: Path, bagian: list[str], buat: bool) -> Path:
    """Cadangan tanpa dir_fd (Windows dev): lstat setiap komponen dari akar ke bawah."""
    p = Path(akar)
    for i, b in enumerate(["", *bagian]):
        if i:
            p = p / b
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            if not (buat and i):
                raise
            os.mkdir(p, 0o755)
            st = os.lstat(p)
        if _tautan(st) or not stat.S_ISDIR(st.st_mode):
            raise PathTidakAman("Path melewati symlink atau bukan direktori")
    return p


def _pecah(relatif: str) -> tuple[list[str], str]:
    path_sah(relatif)
    *induk, nama = relatif.split("/")
    return induk, nama


def buka_baca(akar: Path, relatif: str) -> BinaryIO:
    """Buka berkas biasa `akar/relatif` untuk dibaca biner.

    Melempar `PathTidakAman` bila path tidak sah, melewati/berupa symlink,
    atau bukan berkas biasa (direktori, FIFO, device); `FileNotFoundError`
    bila tidak ada. Pemanggil yang mengirim berkas ke produksi melewati
    berkas yang ditolak dengan peringatan.
    """
    induk, nama = _pecah(relatif)
    lstat_awal = None
    if _ADA_DIR_FD:
        dfd = _fd_induk(akar, induk, buat=False)
        try:
            fd = os.open(nama, os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _CLOEXEC, dir_fd=dfd)
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise PathTidakAman("Path berupa symlink") from None
            raise
        finally:
            os.close(dfd)
    else:
        jalur = _periksa_jalur(akar, induk, buat=False) / nama
        lstat_awal = os.lstat(jalur)
        if _tautan(lstat_awal) or not stat.S_ISREG(lstat_awal.st_mode):
            raise PathTidakAman("Path berupa symlink atau bukan berkas biasa")
        fd = os.open(jalur, os.O_RDONLY | _BINER)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PathTidakAman("Path bukan berkas biasa")
        if lstat_awal is not None and (st.st_dev, st.st_ino) != (lstat_awal.st_dev, lstat_awal.st_ino):
            raise PathTidakAman("Berkas berganti saat dibuka")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def adalah_tautan(st: os.stat_result) -> bool:
    """Hasil lstat ini symlink (atau junction/reparse point Windows)."""
    return _tautan(st)


def daftar_direktori(akar: Path, relatif: str) -> list[tuple[str, os.stat_result]]:
    """Isi direktori `akar/relatif` ("" = akar) sebagai (nama, lstat), tanpa mengikuti symlink.

    Setiap komponen menuju direktori itu dibuka tanpa mengikuti symlink,
    jadi direktori yang ditukar container dengan symlink ke luar ditolak
    (`PathTidakAman`), bukan didaftar isinya. Entri symlink di dalamnya
    dilaporkan dengan lstat-nya sendiri; pemanggil yang melewatinya.
    """
    bagian = relatif.split("/") if relatif else []
    if relatif:
        path_sah(relatif)
    if _ADA_DIR_FD:
        dfd = _fd_induk(akar, bagian, buat=False)
        try:
            with os.scandir(dfd) as it:
                return [(d.name, d.stat(follow_symlinks=False)) for d in it]
        finally:
            os.close(dfd)
    with os.scandir(_periksa_jalur(akar, bagian, buat=False)) as it:
        return [(d.name, d.stat(follow_symlinks=False)) for d in it]


def baca_terbatas(akar: Path, relatif: str, maks: int, dari: int = 0) -> bytes:
    """Paling banyak `maks` byte dari `akar/relatif` mulai posisi `dari`."""
    if maks < 0 or dari < 0:
        raise ValueError("Batas baca tidak sah")
    with buka_baca(akar, relatif) as f:
        if dari:
            f.seek(dari)
        return f.read(maks)


class PenulisBertahap:
    """Berkas sementara yang ditulis per potongan, dipasang hanya lewat `selesai()`."""

    def __init__(self, f: BinaryIO, pasang) -> None:
        self._f = f
        self._pasang = pasang
        self.terpasang = False

    def tulis(self, data: bytes) -> None:
        if self.terpasang:
            raise ValueError("Berkas sudah dipasang")
        self._f.write(data)

    def selesai(self, mtime: int | None = None) -> None:
        """Pasang berkas sementara di tujuan (rename atomik), dengan mtime bila diberikan."""
        if self.terpasang:
            raise ValueError("Berkas sudah dipasang")
        self._pasang(mtime)
        self.terpasang = True


def _tolak_tujuan_tautan(st_fungsi) -> None:
    try:
        if _tautan(st_fungsi()):
            raise PathTidakAman("Tujuan tulis berupa symlink")
    except FileNotFoundError:
        pass


@contextmanager
def tulis_bertahap(akar: Path, relatif: str) -> Iterator[PenulisBertahap]:
    """Tulis `akar/relatif` per potongan lewat berkas sementara, lalu rename.

    Untuk berkas besar yang tidak boleh ditampung utuh di memori (rentang
    tarik): jaminannya sama dengan `tulis_atomik` -- setiap komponen induk
    dibuka tanpa mengikuti symlink, berkas sementara dibuat O_EXCL|O_NOFOLLOW
    di direktori yang sama, dan tujuan yang berupa symlink ditolak. Tujuan
    baru berubah saat `selesai()` dipanggil; keluar dari blok tanpa itu
    (termasuk karena galat) membuang berkas sementara dan membiarkan tujuan
    apa adanya.
    """
    induk, nama = _pecah(relatif)
    # Nama sementara pendek: akhiran pada nama asli yang sudah 250 byte
    # melanggar batas 255 byte per nama berkas.
    sementara = f".wpmgr-{uuid.uuid4().hex[:12]}.tmp"
    if _ADA_DIR_FD:
        dfd = _fd_induk(akar, induk, buat=True)
        try:
            _tolak_tujuan_tautan(lambda: os.stat(nama, dir_fd=dfd, follow_symlinks=False))
            fd = os.open(sementara, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o644,
                         dir_fd=dfd)
            f = os.fdopen(fd, "wb")

            def pasang(mtime: int | None) -> None:
                f.flush()
                if mtime is not None:
                    os.utime(f.fileno(), (mtime, mtime))
                f.close()
                os.rename(sementara, nama, src_dir_fd=dfd, dst_dir_fd=dfd)

            def buang() -> None:
                f.close()
                try:
                    os.unlink(sementara, dir_fd=dfd)
                except OSError:
                    pass

            penulis = PenulisBertahap(f, pasang)
            try:
                yield penulis
            finally:
                if not penulis.terpasang:
                    buang()
        finally:
            os.close(dfd)
        return
    d = _periksa_jalur(akar, induk, buat=True)
    tujuan = d / nama
    _tolak_tujuan_tautan(lambda: os.lstat(tujuan))
    jalur_sementara = d / sementara
    with open(jalur_sementara, "xb") as f:

        def pasang(mtime: int | None) -> None:
            f.close()
            if mtime is not None:
                os.utime(jalur_sementara, (mtime, mtime))
            os.replace(jalur_sementara, tujuan)

        penulis = PenulisBertahap(f, pasang)
        try:
            yield penulis
        finally:
            if not penulis.terpasang:
                # Ditutup dulu: Windows tidak bisa menghapus berkas yang masih terbuka.
                f.close()
                jalur_sementara.unlink(missing_ok=True)


def tulis_atomik(akar: Path, relatif: str, isi: bytes, mtime: int | None = None) -> None:
    """Tulis `akar/relatif` lewat berkas sementara lalu rename, membuat direktori induk.

    Tujuan yang berupa symlink ditolak. Rename sendiri tidak pernah mengikuti
    symlink, jadi symlink yang muncul sesudah pemeriksaan hanya tertimpa,
    bukan diikuti.
    """
    with tulis_bertahap(akar, relatif) as w:
        w.tulis(isi)
        w.selesai(mtime)


def hapus_tautan(akar: Path, relatif: str) -> bool:
    """Hapus `akar/relatif` hanya bila ia symlink/junction, tanpa mengikuti; True bila dihapus.

    Container staging bisa menukar berkas atau direktori di files/ dengan
    symlink. Tautan itu sendiri yang dihapus (target di luar tidak pernah
    disentuh), supaya isi produksi bisa ditulis ulang di tempatnya.
    """
    induk, nama = _pecah(relatif)
    try:
        if _ADA_DIR_FD:
            dfd = _fd_induk(akar, induk, buat=False)
            try:
                if not _tautan(os.stat(nama, dir_fd=dfd, follow_symlinks=False)):
                    return False
                # unlink pada symlink (juga symlink ke direktori) menghapus tautannya saja.
                os.unlink(nama, dir_fd=dfd)
            finally:
                os.close(dfd)
            return True
        jalur = _periksa_jalur(akar, induk, buat=False) / nama
        if not _tautan(os.lstat(jalur)):
            return False
        try:
            os.unlink(jalur)
        except (IsADirectoryError, PermissionError):
            # Symlink direktori dan junction Windows dihapus dengan rmdir, yang
            # juga tidak mengikuti tautannya.
            os.rmdir(jalur)
        return True
    except FileNotFoundError:
        return False


def hapus_direktori_kosong(akar: Path, relatif: str) -> bool:
    """Hapus direktori `akar/relatif` bila kosong (upaya terbaik); True bila terhapus.

    Tidak pernah mengikuti symlink: rmdir pada symlink gagal (ENOTDIR), dan
    setiap komponen induk dibuka tanpa mengikuti symlink.
    """
    induk, nama = _pecah(relatif)
    try:
        if _ADA_DIR_FD:
            dfd = _fd_induk(akar, induk, buat=False)
            try:
                os.rmdir(nama, dir_fd=dfd)
            finally:
                os.close(dfd)
            return True
        jalur = _periksa_jalur(akar, induk, buat=False) / nama
        st = os.lstat(jalur)
        if _tautan(st) or not stat.S_ISDIR(st.st_mode):
            return False
        os.rmdir(jalur)
        return True
    except (OSError, PathTidakAman):
        # Tidak kosong, tidak ada, atau bukan direktori.
        return False


def hapus_berkas(akar: Path, relatif: str) -> None:
    """Hapus `akar/relatif` (berkas atau symlink itu sendiri); diam bila tidak ada."""
    induk, nama = _pecah(relatif)
    try:
        if _ADA_DIR_FD:
            dfd = _fd_induk(akar, induk, buat=False)
            try:
                os.unlink(nama, dir_fd=dfd)
            finally:
                os.close(dfd)
        else:
            os.unlink(_periksa_jalur(akar, induk, buat=False) / nama)
    except FileNotFoundError:
        pass
