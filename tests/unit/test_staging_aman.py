import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wpmgr.staging.aman import (
    BATAS_DIUBAH,
    PathTidakAman,
    angka,
    baca_terbatas,
    bersih_teks,
    boleh_didorong,
    buka_baca,
    daftar_direktori,
    dikecualikan,
    hapus_berkas,
    jalur_di_dalam,
    nama_dari_url,
    nama_sah,
    path_sah,
    tulis_atomik,
    tulis_bertahap,
    versi_php_staging,
    waktu_penanda,
)


def _symlink(sumber, tujuan, direktori: bool) -> None:
    try:
        os.symlink(sumber, tujuan, target_is_directory=direktori)
    except OSError:
        pytest.skip("symlink tidak diizinkan di sistem ini")


@pytest.mark.parametrize("url,nama", [
    ("https://www.Toko-Contoh.co.id", "toko-contoh-co-id"),
    ("https://klinik.halosocia.my.id/", "klinik-halosocia-my-id"),
    ("https://xn--caf-dma.id", "xn-caf-dma-id"),
    ("https://" + "a" * 60 + ".id", "a" * 40),
    ("https://-.id", "id"),
    ("bukan url", "situs"),
])
def test_nama_dari_url(url, nama):
    hasil = nama_dari_url(url)
    assert hasil == nama
    assert nama_sah(hasil)


@pytest.mark.parametrize("nama,sah", [
    ("toko-1", True), ("a" * 40, True), ("a" * 41, False), ("Toko", False), ("toko\n", False),
    ("", False), ("../x", False), ("toko_1", False), (None, False), ("٣", False),
])
def test_nama_sah(nama, sah):
    assert nama_sah(nama) is sah


@pytest.mark.parametrize("versi,hasil", [
    ("8.1.29", ("8.1", False)), ("7.4.33", ("7.4", False)), ("8.3.0", ("8.3", False)),
    ("7.2.34", ("7.4", True)), ("8.4.1", ("8.3", True)), (None, ("8.1", True)), ("abc", ("8.1", True)),
])
def test_versi_php_staging(versi, hasil):
    assert versi_php_staging(versi) == hasil


def test_bersih_teks():
    assert bersih_teks("a\x00b", 10) == "ab"
    assert bersih_teks("a\ud800b", 10) == "a?b"
    assert bersih_teks("é" * 10, 3) == "ééé"
    assert bersih_teks(None, 3) is None
    assert bersih_teks(12, 5) == "12"


@pytest.mark.parametrize("nilai,hasil", [
    (5, 5), ("7", 7), (-3, 0), (10**20, 1000), ("12a", None), (True, None), (1.5, None), (None, None), ("٣", None),
])
def test_angka(nilai, hasil):
    assert angka(nilai, 0, 1000) == hasil


@pytest.mark.parametrize("p", [
    "", "/etc/passwd", "../x", "a/../b", "a//b", "a/./b", "C:/x", "a\\b", "a\x00b", "a\nb",
    "a" * 1025, "a/" + "b" * 256, "a/", "a\ud800", 5, None,
])
def test_path_sah_menolak(p):
    with pytest.raises(PathTidakAman):
        path_sah(p)


def test_path_sah_menerima():
    for p in ("wp-content/uploads/ü-berkas.txt", ".htaccess", "wp-content/plugins/a b/c.php",
              "wp-content/uploads/" + "é" * 120 + ".jpg"):
        assert path_sah(p) == p


def test_dikecualikan_cermin_connector():
    for p in ("wp-config.php", ".maintenance", "debug.log", "wp-content/debug.LOG", "wp-content/cache/a",
              "wp-content/wpmgr-dorong/x", "wp-content/updraft/b.zip", "wp-content/backups-dup-lite/a"):
        assert dikecualikan(p), p
    for p in ("index.php", "wp-content/uploads/cache/a.jpg", "wp-content/plugins/updraft/x.php"):
        assert not dikecualikan(p), p


def test_boleh_didorong_cermin_connector():
    for p in ("wp-content/themes/x/style.css", "wp-admin/index.php", "wp-includes/version.php", "index.php",
              "wp-login.php", ".htaccess", "xmlrpc.php"):
        assert boleh_didorong(p), p
    for p in ("wp-config.php", "wp-content/plugins/wp-manager-connector/x.php",
              "wp-content/mu-plugins/wpmgr-staging.php", "lain.php", "foo/bar.php", "WP-LOGIN.PHP", "../x"):
        assert not boleh_didorong(p), p


def test_jalur_di_dalam(tmp_path):
    akar = tmp_path / "files"
    akar.mkdir()
    assert jalur_di_dalam(akar, "wp-content/uploads/a.jpg") == akar / "wp-content" / "uploads" / "a.jpg"
    with pytest.raises(PathTidakAman):
        jalur_di_dalam(akar, "../luar.txt")


def test_jalur_di_dalam_menolak_symlink(tmp_path):
    akar = tmp_path / "files"
    (akar / "wp-content").mkdir(parents=True)
    luar = tmp_path / "luar"
    luar.mkdir()
    _symlink(luar, akar / "wp-content" / "uploads", True)
    with pytest.raises(PathTidakAman):
        jalur_di_dalam(akar, "wp-content/uploads/x.php")
    assert not (luar / "x.php").exists()
    assert isinstance(jalur_di_dalam(akar, "wp-content/lain.txt"), Path)


# ---- akses berkas di bawah files/ dan log/ (putusan F1) ------------------


@pytest.fixture
def pohon(tmp_path):
    """files/ staging dan sebuah berkas rahasia di luar akar."""
    akar = tmp_path / "files"
    (akar / "wp-content").mkdir(parents=True)
    luar = tmp_path / "luar"
    luar.mkdir()
    (luar / "rahasia.env").write_bytes(b"WPMGR_SECRET_KEY=bocor")
    return akar, luar


def test_baca_berkas_biasa(pohon):
    akar, _ = pohon
    (akar / "wp-content" / "a.txt").write_bytes(b"0123456789")
    with buka_baca(akar, "wp-content/a.txt") as f:
        assert f.read() == b"0123456789"
    assert baca_terbatas(akar, "wp-content/a.txt", 4) == b"0123"
    assert baca_terbatas(akar, "wp-content/a.txt", 4, dari=8) == b"89"
    with pytest.raises(FileNotFoundError):
        baca_terbatas(akar, "wp-content/tidak-ada.txt", 4)
    with pytest.raises(PathTidakAman):
        baca_terbatas(akar, "../luar/rahasia.env", 4)


def test_baca_diubah_dibatasi(tmp_path):
    log = tmp_path / "log"
    log.mkdir()
    (log / "diubah").write_bytes(b"1" * (BATAS_DIUBAH * 4))
    assert BATAS_DIUBAH == 64 * 1024
    assert len(baca_terbatas(log, "diubah", BATAS_DIUBAH)) == BATAS_DIUBAH


def test_baca_menolak_symlink_ke_berkas_luar(pohon):
    akar, luar = pohon
    _symlink(luar / "rahasia.env", akar / "wp-content" / "dorong.sql", False)
    with pytest.raises(PathTidakAman):
        buka_baca(akar, "wp-content/dorong.sql")
    with pytest.raises(PathTidakAman):
        baca_terbatas(akar, "wp-content/dorong.sql", 1024)


def test_baca_menolak_direktori_induk_symlink(pohon):
    akar, luar = pohon
    _symlink(luar, akar / "wp-content" / "uploads", True)
    with pytest.raises(PathTidakAman):
        baca_terbatas(akar, "wp-content/uploads/rahasia.env", 1024)


def test_baca_menolak_akar_symlink(pohon, tmp_path):
    _, luar = pohon
    tautan = tmp_path / "log"
    _symlink(luar, tautan, True)
    with pytest.raises(PathTidakAman):
        baca_terbatas(tautan, "rahasia.env", 1024)


def test_baca_menolak_bukan_berkas_biasa(pohon):
    akar, _ = pohon
    with pytest.raises(PathTidakAman):
        buka_baca(akar, "wp-content")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO hanya ada di POSIX")
def test_baca_fifo_ditolak_tanpa_menggantung(pohon):
    akar, _ = pohon
    os.mkfifo(akar / "diubah")
    with pytest.raises(PathTidakAman):
        baca_terbatas(akar, "diubah", 32)


def test_tulis_atomik_membuat_induk_dan_mtime(pohon):
    akar, _ = pohon
    tulis_atomik(akar, "wp-content/uploads/2026/a.jpg", b"isi", mtime=1_700_000_000)
    tujuan = akar / "wp-content" / "uploads" / "2026" / "a.jpg"
    assert tujuan.read_bytes() == b"isi"
    assert int(tujuan.stat().st_mtime) == 1_700_000_000
    tulis_atomik(akar, "wp-content/uploads/2026/a.jpg", b"baru")
    assert tujuan.read_bytes() == b"baru"
    assert [p.name for p in tujuan.parent.iterdir()] == ["a.jpg"]


def test_tulis_menolak_symlink_ke_berkas_luar(pohon):
    akar, luar = pohon
    _symlink(luar / "rahasia.env", akar / "wp-content" / "x.php", False)
    with pytest.raises(PathTidakAman):
        tulis_atomik(akar, "wp-content/x.php", b"<?php jahat();")
    assert (luar / "rahasia.env").read_bytes() == b"WPMGR_SECRET_KEY=bocor"


def test_tulis_menolak_direktori_induk_symlink(pohon):
    akar, luar = pohon
    _symlink(luar, akar / "wp-content" / "uploads", True)
    with pytest.raises(PathTidakAman):
        tulis_atomik(akar, "wp-content/uploads/x.php", b"<?php jahat();")
    with pytest.raises(PathTidakAman):
        tulis_atomik(akar, "wp-content/uploads/baru/x.php", b"<?php jahat();")
    assert sorted(p.name for p in luar.iterdir()) == ["rahasia.env"]


def test_tulis_menolak_path_tidak_sah(pohon):
    akar, luar = pohon
    with pytest.raises(PathTidakAman):
        tulis_atomik(akar, "../luar/x.php", b"x")
    assert sorted(p.name for p in luar.iterdir()) == ["rahasia.env"]


def _sisa_sementara(d: Path) -> list[str]:
    return [p.name for p in d.iterdir() if p.name.startswith(".wpmgr-")]


def test_tulis_bertahap_menulis_potongan_lalu_memasang(pohon):
    akar, _ = pohon
    with tulis_bertahap(akar, "wp-content/uploads/2026/besar.bin") as w:
        w.tulis(b"abc")
        w.tulis(b"def")
        # Belum dipasang: tujuan belum terlihat selama penulisan.
        assert not (akar / "wp-content/uploads/2026/besar.bin").exists()
        w.selesai(mtime=1_700_000_000)
    tujuan = akar / "wp-content/uploads/2026/besar.bin"
    assert tujuan.read_bytes() == b"abcdef"
    assert int(tujuan.stat().st_mtime) == 1_700_000_000
    assert _sisa_sementara(tujuan.parent) == []


def test_tulis_bertahap_tanpa_selesai_tidak_memasang(pohon):
    akar, _ = pohon
    tulis_atomik(akar, "wp-content/a.bin", b"lama")
    with tulis_bertahap(akar, "wp-content/a.bin") as w:
        w.tulis(b"setengah")
    assert (akar / "wp-content/a.bin").read_bytes() == b"lama"
    assert _sisa_sementara(akar / "wp-content") == []


def test_tulis_bertahap_galat_membuang_sementara(pohon):
    akar, _ = pohon
    tulis_atomik(akar, "wp-content/a.bin", b"lama")
    with pytest.raises(RuntimeError), tulis_bertahap(akar, "wp-content/a.bin") as w:
        w.tulis(b"setengah")
        raise RuntimeError("putus")
    assert (akar / "wp-content/a.bin").read_bytes() == b"lama"
    assert _sisa_sementara(akar / "wp-content") == []


def test_tulis_bertahap_menolak_symlink(pohon):
    akar, luar = pohon
    with pytest.raises(PathTidakAman), tulis_bertahap(akar, "../luar/x.bin"):
        pass
    _symlink(luar / "rahasia.env", akar / "wp-content" / "x.bin", False)
    with pytest.raises(PathTidakAman), tulis_bertahap(akar, "wp-content/x.bin"):
        pass
    _symlink(luar, akar / "wp-content" / "uploads", True)
    with pytest.raises(PathTidakAman), tulis_bertahap(akar, "wp-content/uploads/baru/x.bin"):
        pass
    assert sorted(p.name for p in luar.iterdir()) == ["rahasia.env"]
    assert (luar / "rahasia.env").read_bytes() == b"WPMGR_SECRET_KEY=bocor"


def test_hapus_berkas(pohon):
    akar, luar = pohon
    (akar / "wp-content" / "a.txt").write_bytes(b"x")
    hapus_berkas(akar, "wp-content/a.txt")
    assert not (akar / "wp-content" / "a.txt").exists()
    hapus_berkas(akar, "wp-content/a.txt")
    hapus_berkas(akar, "tidak/ada/sama/sekali.txt")
    _symlink(luar, akar / "wp-content" / "uploads", True)
    with pytest.raises(PathTidakAman):
        hapus_berkas(akar, "wp-content/uploads/rahasia.env")
    assert (luar / "rahasia.env").exists()


def test_linux_tanpa_dir_fd_adalah_galat_keras():
    import types

    from wpmgr.staging.aman import _dukung_dir_fd

    tanpa = types.SimpleNamespace(supports_dir_fd=set(), supports_fd=set(), supports_follow_symlinks=set())
    with pytest.raises(RuntimeError):
        _dukung_dir_fd(tanpa, "linux")
    assert _dukung_dir_fd(tanpa, "win32") is False
    if sys.platform == "linux":
        assert _dukung_dir_fd(os, sys.platform) is True


def test_daftar_direktori_memakai_lstat_dan_menolak_induk_symlink(pohon):
    akar, luar = pohon
    (akar / "wp-content" / "a.txt").write_bytes(b"abc")
    (akar / "wp-content" / "d").mkdir()
    isi = dict(daftar_direktori(akar, "wp-content"))
    assert set(isi) == {"a.txt", "d"}
    assert isi["a.txt"].st_size == 3
    assert set(dict(daftar_direktori(akar, ""))) == {"wp-content"}
    _symlink(luar / "rahasia.env", akar / "wp-content" / "t.env", False)
    _symlink(luar, akar / "wp-content" / "uploads", True)
    isi = dict(daftar_direktori(akar, "wp-content"))
    # Entri symlink dilaporkan apa adanya (lstat), tidak diikuti.
    assert stat.S_ISLNK(isi["t.env"].st_mode)
    assert stat.S_ISLNK(isi["uploads"].st_mode)
    with pytest.raises(PathTidakAman):
        daftar_direktori(akar, "wp-content/uploads")


# ---- penanda `log/diubah` (fix round 1 Task 13) ----------------------------

KINI = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("mentah,harapan", [
    (b"1790000000", datetime.fromtimestamp(1790000000, tz=timezone.utc)),
    (b"  1790000000\n", datetime.fromtimestamp(1790000000, tz=timezone.utc)),
    (b"0", datetime(1970, 1, 1, tzinfo=timezone.utc)),
    # Tepat di batas toleransi jam (5 menit ke depan) masih diterima.
    (str(int(KINI.timestamp()) + 300).encode(), KINI + timedelta(minutes=5)),
])
def test_waktu_penanda_sah(mentah, harapan):
    assert waktu_penanda(mentah, KINI) == harapan


@pytest.mark.parametrize("mentah", [
    b"", b"bukan angka", b"-5", b"1.5", b"\xff\xfe", b"17900000001790000000",
    # Masa depan (lebih dari 5 menit): di Linux fromtimestamp menerimanya
    # sampai tahun 9999, jadi harus ditolak eksplisit, bukan dijepit.
    str(int(KINI.timestamp()) + 301).encode(),
    b"253402300799", b"99999999999999999999", b"9" * 400,
])
def test_waktu_penanda_ditolak(mentah):
    assert waktu_penanda(mentah, KINI) is None
