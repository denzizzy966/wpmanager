"""Indeks berkas staging (Koreksi #2).

Setiap berkas yang selesai ditulis ke files/ dicatat satu baris JSON
(append-only), sehingga tarik yang terputus dilanjutkan dengan menghitung
ulang selisih manifest terhadap indeks, tanpa mengunduh ulang dan tanpa
menulis daftar ratusan ribu path ke job.payload setiap potongan.

`indeks.jsonl` sendiri berada langsung di bawah <site_id>/ dan tidak
di-bind mount ke container mana pun, jadi I/O biasa aman untuknya. Isi
files/ sebaliknya bisa ditukar container kapan saja; pemindaian dan hash di
sana hanya lewat `wpmgr.staging.aman` (putusan F1).
"""

import hashlib
import json
import logging
import os
import stat
from pathlib import Path
from typing import BinaryIO

from wpmgr.staging.aman import (
    PathTidakAman,
    adalah_tautan,
    bersih_teks,
    buka_baca,
    daftar_direktori,
    dikecualikan,
    path_sah,
)
from wpmgr.staging.rencana import Entri, entri_dari

log = logging.getLogger(__name__)

# Peringatan pemindaian yang disimpan untuk ditampilkan; sisanya hanya dihitung.
MAKS_PERINGATAN = 50


class IndeksTerlaluBesar(ValueError):
    """Berkas indeks/manifest memuat lebih banyak baris daripada batas pemanggil."""


class Indeks:
    def __init__(self, berkas: Path) -> None:
        self.berkas = Path(berkas)
        self._ujung_diperiksa = False

    def muat(self, maks: int | None = None) -> dict[str, Entri]:
        """Isi indeks. `maks` membatasi jumlah baris yang dibaca (IndeksTerlaluBesar bila lewat)."""
        if not self.berkas.exists():
            return {}
        with open(self.berkas, encoding="utf-8", errors="replace") as f:
            return urai_indeks(f, maks)

    def _awalan_baris_baru(self) -> str:
        """"\\n" bila berkas berakhir di tengah baris (crash saat menulis).

        Tanpa ini catatan pertama sesudah crash tersambung ke sisa baris
        terpotong itu dan ikut hilang saat dimuat.
        """
        if self._ujung_diperiksa:
            return ""
        self._ujung_diperiksa = True
        try:
            with open(self.berkas, "rb") as f:
                f.seek(0, os.SEEK_END)
                if f.tell() == 0:
                    return ""
                f.seek(-1, os.SEEK_END)
                return "" if f.read(1) == b"\n" else "\n"
        except FileNotFoundError:
            return ""

    def _tambah(self, data: dict) -> None:
        self.berkas.parent.mkdir(parents=True, exist_ok=True)
        awalan = self._awalan_baris_baru()
        with open(self.berkas, "a", encoding="utf-8", newline="\n") as f:
            f.write(awalan + json.dumps(data, ensure_ascii=False) + "\n")

    def catat(self, e: Entri) -> None:
        self._tambah({"p": e.path, "u": e.ukuran, "m": e.mtime, "h": e.hash})

    def catat_hapus(self, path: str) -> None:
        self._tambah({"p": path, "hapus": 1})

    def padatkan(self, isi: dict[str, Entri]) -> None:
        sementara = self.berkas.with_name(self.berkas.name + ".tmp")
        with open(sementara, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps({"p": e.path, "u": e.ukuran, "m": e.mtime, "h": e.hash}, ensure_ascii=False)
                         + "\n" for e in isi.values())
            f.flush()
            os.fsync(f.fileno())
        os.replace(sementara, self.berkas)
        self._ujung_diperiksa = True


def urai_indeks(baris_teks, maks: int | None = None) -> dict[str, Entri]:
    """Isi indeks dari baris-baris teks (berkas yang sudah dibuka pemanggil).

    Dipisah dari `Indeks.muat` supaya snapshot Kembalikan bisa dibaca lewat
    `aman.buka_baca` (tanpa mengikuti symlink) dengan penguraian yang sama.
    """
    hasil: dict[str, Entri] = {}
    for nomor, baris in enumerate(baris_teks, 1):
        if maks is not None and nomor > maks:
            raise IndeksTerlaluBesar(f"Indeks melebihi {maks} baris")
        try:
            d = json.loads(baris)
        except (ValueError, RecursionError):
            continue
        if not isinstance(d, dict):
            continue
        if d.get("hapus") == 1 and isinstance(d.get("p"), str):
            hasil.pop(d["p"], None)
            continue
        e = entri_dari({"path": d.get("p"), "ukuran": d.get("u"), "mtime": d.get("m"), "hash": d.get("h")})
        if e is not None:
            hasil[e.path] = e
    return hasil


def _hash_dari(f: BinaryIO) -> str:
    h = hashlib.sha256()
    for potong in iter(lambda: f.read(1 << 20), b""):
        h.update(potong)
    return h.hexdigest()


def sha256_berkas(akar: Path, relatif: str) -> str:
    """sha256 berkas biasa `akar/relatif`, dibuka tanpa mengikuti symlink.

    Melempar `PathTidakAman` untuk symlink/bukan berkas biasa, seperti
    `aman.buka_baca`.
    """
    with buka_baca(akar, relatif) as f:
        return _hash_dari(f)


class _Peringatan:
    def __init__(self, tujuan: list[str] | None) -> None:
        self.tujuan = tujuan
        self.lebih = 0

    def __call__(self, relatif: str, alasan: str) -> None:
        # Hanya path relatif yang dicatat: pesan ini bisa tampil di UI, dan
        # path sistem berkas VPS tidak boleh ikut. bersih_teks membuang
        # surrogate dari nama bukan UTF-8 sebelum masuk PostgreSQL.
        pesan = f"Berkas staging {bersih_teks(relatif, 300)} {alasan}; dilewati."
        log.warning(pesan)
        if self.tujuan is None:
            return
        if len(self.tujuan) < MAKS_PERINGATAN:
            self.tujuan.append(pesan)
        else:
            self.lebih += 1

    def tutup(self) -> None:
        if self.tujuan is not None and self.lebih:
            self.tujuan.append(f"Dan {self.lebih} berkas staging lain dilewati.")


def pindai_lokal(akar: Path, indeks: dict[str, Entri], peringatan: list[str] | None = None,
                 tautan: list[str] | None = None) -> dict[str, Entri]:
    """Isi files/ staging saat ini. Hash diambil dari indeks bila ukuran dan mtime sama.

    Symlink (termasuk direktori symlink) dan yang bukan berkas biasa (FIFO,
    device) dilewati dengan peringatan, ditambahkan ke `peringatan` bila
    diberikan. Path relatif setiap symlink yang ditemukan ditambahkan ke
    `tautan` bila diberikan, supaya pemanggil bisa menghapusnya. Ukuran dan mtime diambil dari deskriptor yang sama yang
    di-hash, jadi berkas yang ditukar di sela langkah tidak tercampur.
    """
    hasil: dict[str, Entri] = {}
    akar = Path(akar)
    catat = _Peringatan(peringatan)
    tumpukan = [""]
    while tumpukan:
        rel_dir = tumpukan.pop()
        try:
            isi_dir = daftar_direktori(akar, rel_dir)
        except PathTidakAman:
            catat(rel_dir, "berubah menjadi symlink saat dipindai")
            continue
        except FileNotFoundError:
            continue
        except OSError as exc:
            catat(rel_dir or ".", f"tidak dapat dibaca (errno {exc.errno})")
            continue
        for nama, st in isi_dir:
            relatif = f"{rel_dir}/{nama}" if rel_dir else nama
            try:
                path_sah(relatif)
            except PathTidakAman:
                catat(relatif, "bernama tidak sah")
                continue
            if adalah_tautan(st):
                catat(relatif, "berupa symlink")
                if tautan is not None:
                    tautan.append(relatif)
                continue
            if stat.S_ISDIR(st.st_mode):
                # Akhiran "/" meniru manifest connector: aturan khusus berkas
                # (".log", "wp-config.php") tidak berlaku untuk direktori.
                if not dikecualikan(relatif + "/"):
                    tumpukan.append(relatif)
                continue
            if not stat.S_ISREG(st.st_mode):
                catat(relatif, "bukan berkas biasa")
                continue
            if dikecualikan(relatif):
                continue
            e = _entri_berkas(akar, relatif, indeks.get(relatif), catat)
            if e is not None:
                hasil[relatif] = e
    catat.tutup()
    return hasil


def _entri_berkas(akar: Path, relatif: str, lama: Entri | None, catat: _Peringatan) -> Entri | None:
    try:
        with buka_baca(akar, relatif) as f:
            st = os.fstat(f.fileno())
            ukuran, mtime = st.st_size, int(st.st_mtime)
            if lama is not None and lama.hash and (lama.ukuran, lama.mtime) == (ukuran, mtime):
                h = lama.hash
            else:
                h = _hash_dari(f)
    except PathTidakAman:
        catat(relatif, "berupa symlink atau bukan berkas biasa")
        return None
    except FileNotFoundError:
        return None
    except OSError as exc:
        catat(relatif, f"tidak dapat dibaca (errno {exc.errno})")
        return None
    return Entri(relatif, ukuran, mtime, h)
