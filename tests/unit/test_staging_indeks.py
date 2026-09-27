import hashlib
import os

import pytest

from wpmgr.staging.aman import PathTidakAman
from wpmgr.staging.indeks import Indeks, pindai_lokal, sha256_berkas
from wpmgr.staging.rencana import Entri


def _symlink(sumber, tujuan, direktori: bool = False) -> None:
    try:
        os.symlink(sumber, tujuan, target_is_directory=direktori)
    except (OSError, NotImplementedError):
        pytest.skip("symlink tidak diizinkan di sistem ini")


def test_catat_muat_hapus_dan_padatkan(tmp_path):
    ind = Indeks(tmp_path / "indeks.jsonl")
    assert ind.muat() == {}
    ind.catat(Entri("a.php", 3, 5, "1" * 64))
    ind.catat(Entri("b.php", 4, 6, None))
    ind.catat(Entri("a.php", 7, 8, "2" * 64))
    ind.catat_hapus("b.php")
    with open(tmp_path / "indeks.jsonl", "a", encoding="utf-8") as f:
        f.write("bukan json\n")
        f.write('{"p": "../x", "u": 1, "m": 1}\n')
        f.write('{"p": "c.php", "u": 1')  # baris terpotong karena crash
    isi = Indeks(tmp_path / "indeks.jsonl").muat()
    assert isi == {"a.php": Entri("a.php", 7, 8, "2" * 64)}
    ind.padatkan(isi)
    assert (tmp_path / "indeks.jsonl").read_text(encoding="utf-8").count("\n") == 1
    assert ind.muat() == isi


def test_catat_sesudah_baris_terpotong_tidak_ikut_rusak(tmp_path):
    berkas = tmp_path / "indeks.jsonl"
    berkas.write_text('{"p": "a.php", "u": 1, "m": 1, "h": null}\n{"p": "c.php", "u": 1', encoding="utf-8")
    # Instans baru (job dilanjutkan sesudah crash) menambah ke berkas yang
    # berakhir di tengah baris: catatan baru tidak boleh tersambung ke sisa itu.
    Indeks(berkas).catat(Entri("d.php", 2, 3, None))
    assert set(Indeks(berkas).muat()) == {"a.php", "d.php"}


def test_pindai_lokal_memakai_ulang_hash_indeks(tmp_path):
    akar = tmp_path / "files"
    (akar / "wp-content").mkdir(parents=True)
    (akar / "index.php").write_bytes(b"<?php")
    (akar / "wp-content" / "debug.log").write_bytes(b"log")
    (akar / "wp-content" / "a.css").write_bytes(b"body{}")
    os.utime(akar / "index.php", (1000, 1000))
    lama = {"index.php": Entri("index.php", 5, 1000, "f" * 64)}
    hasil = pindai_lokal(akar, lama)
    assert set(hasil) == {"index.php", "wp-content/a.css"}
    assert hasil["index.php"].hash == "f" * 64
    assert hasil["wp-content/a.css"].hash == hashlib.sha256(b"body{}").hexdigest()


def test_pindai_lokal_melewati_symlink(tmp_path):
    akar = tmp_path / "files"
    akar.mkdir()
    (tmp_path / "luar.txt").write_bytes(b"x")
    _symlink(tmp_path / "luar.txt", akar / "tautan.txt")
    peringatan = []
    assert pindai_lokal(akar, {}, peringatan) == {}
    assert any("tautan.txt" in p for p in peringatan)


def test_pindai_lokal_tidak_masuk_direktori_symlink(tmp_path):
    akar = tmp_path / "files"
    (akar / "wp-content").mkdir(parents=True)
    luar = tmp_path / "luar"
    luar.mkdir()
    (luar / "rahasia.php").write_bytes(b"x")
    _symlink(luar, akar / "wp-content" / "uploads", direktori=True)
    peringatan = []
    assert pindai_lokal(akar, {}, peringatan) == {}
    assert any("wp-content/uploads" in p for p in peringatan)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO hanya ada di POSIX")
def test_pindai_lokal_melewati_bukan_berkas_biasa(tmp_path):
    akar = tmp_path / "files"
    akar.mkdir()
    os.mkfifo(akar / "pipa.php")
    (akar / "a.php").write_bytes(b"a")
    peringatan = []
    assert set(pindai_lokal(akar, {}, peringatan)) == {"a.php"}
    assert any("pipa.php" in p for p in peringatan)


def test_sha256_berkas_lewat_akses_aman(tmp_path):
    akar = tmp_path / "files"
    akar.mkdir()
    (akar / "a.bin").write_bytes(b"\x00" * 3_000_000)
    assert sha256_berkas(akar, "a.bin") == hashlib.sha256(b"\x00" * 3_000_000).hexdigest()
    (tmp_path / "luar.txt").write_bytes(b"x")
    _symlink(tmp_path / "luar.txt", akar / "tautan.txt")
    with pytest.raises(PathTidakAman):
        sha256_berkas(akar, "tautan.txt")
