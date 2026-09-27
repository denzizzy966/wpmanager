import base64
import hashlib
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path

import bcrypt
import pytest

from wpmgr.config import Settings
from wpmgr.staging import pembantu as modul_pembantu
from wpmgr.staging.pembantu import (
    BATAS_KELUARAN,
    KODE_KELUAR,
    PESAN_UMUM,
    GalatPembantu,
    Pembantu,
    cookie_akses,
    hapus_akses_router,
    hash_sandi,
    sandi_baru,
    tautan_masuk,
    tulis_akses_router,
    urai_status,
)

PALSU = Path(__file__).with_name("pembantu_palsu.py")
ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
POSIX = pytest.mark.skipif(not hasattr(os, "killpg"), reason="grup proses hanya ada di POSIX")


@pytest.fixture
def catatan(tmp_path, monkeypatch):
    berkas = tmp_path / "catatan.jsonl"
    monkeypatch.setenv("PALSU_CATATAN", str(berkas))

    def baca():
        if not berkas.exists():
            return []
        return [json.loads(b) for b in berkas.read_text(encoding="utf-8").splitlines()]

    return baca


@pytest.fixture
def pembantu():
    return Pembantu([sys.executable, str(PALSU)])


def _settings(monkeypatch, **env):
    for k, v in {"DATABASE_URL": "postgresql+psycopg://a:b@localhost/c", "WPMGR_SECRET_KEY": "k",
                 "WPMGR_BASE_URL": "https://d.test", "WPMGR_SESSION_SECRET": "s", **env}.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def test_dari_setelan_memakai_sudo_n(monkeypatch):
    monkeypatch.delenv("WPMGR_STAGING_PEMBANTU_AWALAN", raising=False)
    assert Pembantu.dari_setelan(_settings(monkeypatch)).perintah == ["sudo", "-n", "/usr/local/sbin/wpmgr-staging"]


def test_dari_setelan_dengan_awalan_dev(monkeypatch):
    s = _settings(monkeypatch, WPMGR_STAGING_PEMBANTU_AWALAN="docker compose exec -T pembantu /usr/local/sbin/wpmgr-staging")
    assert Pembantu.dari_setelan(s).perintah == [
        "docker", "compose", "exec", "-T", "pembantu", "/usr/local/sbin/wpmgr-staging"]


def test_argumen_diteruskan_persis(pembantu, catatan):
    assert pembantu.buat("toko", "8.1", ID) == f"ok buat toko 8.1 {ID}"
    assert pembantu.wpcli("toko", "plugin", "update", "akismet", "--version=5.3") == \
        "ok wpcli toko plugin update akismet --version=5.3"
    assert catatan()[0]["argv"] == ["buat", "toko", "8.1", ID]


@pytest.mark.parametrize("panggil", [
    lambda p: p.buat("Toko", "8.1", ID),
    lambda p: p.buat("toko", "9.9", ID),
    lambda p: p.buat("toko", "8.1", "../x"),
    lambda p: p.db_buat("toko", ID, "wp_'"),
    lambda p: p.jalan("toko;id"),
    lambda p: p.wpcli("toko\n", "core", "version"),
])
def test_validasi_sebelum_subprocess(pembantu, catatan, panggil):
    with pytest.raises(ValueError):
        panggil(pembantu)
    assert catatan() == []


def test_kode_keluar_cermin_skrip_pembantu():
    assert KODE_KELUAR == {2: "argumen", 3: "ditolak", 4: "docker", 5: "sertifikat", 6: "impor",
                           7: "konfigurasi", 8: "wpcli", 9: "internal"}
    assert set(KODE_KELUAR.values()) | {"lain"} <= set(PESAN_UMUM)


def test_galat_dipetakan_ke_pesan_tetap(pembantu, caplog):
    caplog.set_level(logging.WARNING, logger="wpmgr.staging.pembantu")
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalankan("gagal")
    assert e.value.kode == "ditolak"
    # Putusan F20: stderr tidak pernah menjadi pesan UI, hanya masuk log server.
    assert e.value.pesan == PESAN_UMUM["ditolak"]
    assert "container wp-x sudah ada dan bukan milik staging" in caplog.text
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalankan("gagal-tanpa-pesan")
    assert e.value.kode == "docker"
    assert "Docker" in e.value.pesan


def test_pesan_tetap_per_subperintah(pembantu, catatan, monkeypatch):
    monkeypatch.setenv("PALSU_KELUAR", "4")
    monkeypatch.setenv("PALSU_STDERR", "GALAT docker: /var/lib/wpmgr/staging/x gagal\n")
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalan("toko")
    assert e.value.kode == "docker"
    assert e.value.pesan == "Menjalankan staging gagal. " + PESAN_UMUM["docker"]
    assert "/var/lib" not in e.value.pesan


def test_kode_keluar_menentukan_kategori_bukan_stderr(pembantu, catatan, monkeypatch):
    monkeypatch.setenv("PALSU_KELUAR", "3")
    monkeypatch.setenv("PALSU_STDERR", "GALAT docker: menyesatkan\nGALAT impor: juga menyesatkan\n")
    with pytest.raises(GalatPembantu) as e:
        pembantu.hapus("toko")
    assert e.value.kode == "ditolak"


@pytest.mark.parametrize("kode_keluar,kode", [(9, "internal"), (1, "lain"), (127, "lain")])
def test_kode_keluar_internal_dan_tak_dikenal(pembantu, catatan, monkeypatch, kode_keluar, kode):
    monkeypatch.setenv("PALSU_KELUAR", str(kode_keluar))
    monkeypatch.setenv("PALSU_STDERR", "GALAT internal: kegagalan tak terduga\n")
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalankan("status")
    assert e.value.kode == kode
    assert e.value.pesan == "Membaca status server staging gagal. " + PESAN_UMUM[kode]


def test_stderr_di_log_dipotong(pembantu, catatan, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="wpmgr.staging.pembantu")
    monkeypatch.setenv("PALSU_KELUAR", "8")
    monkeypatch.setenv("PALSU_STDERR", "GALAT wpcli: " + "z" * 20000)
    with pytest.raises(GalatPembantu):
        pembantu.wpcli("toko", "core", "version")
    assert "z" * 100 in caplog.text
    assert "z" * (modul_pembantu.BATAS_STDERR_LOG + 1) not in caplog.text


def test_perintah_tidak_ada_menjadi_galat(tmp_path):
    with pytest.raises(GalatPembantu) as e:
        Pembantu([str(tmp_path / "tidak-ada")]).jalankan("status")
    assert e.value.kode == "lain"
    assert str(tmp_path) not in e.value.pesan


def test_tenggat_keras(pembantu):
    mulai = time.monotonic()
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalankan("tidur", timeout=1)
    assert e.value.kode == "waktu"
    assert time.monotonic() - mulai < 15


@POSIX
def test_tenggat_mengirim_sigterm_dulu(pembantu, catatan):
    mulai = time.monotonic()
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalankan("sopan", timeout=1)
    assert e.value.kode == "waktu"
    assert catatan() == [{"sinyal": "TERM"}]
    assert time.monotonic() - mulai < modul_pembantu.JEDA_HENTI


def _hidup(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        # Zombie yang belum dipanen init dianggap sudah mati.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


@POSIX
def test_tenggat_sigkill_ke_seluruh_grup(pembantu, catatan, monkeypatch):
    monkeypatch.setattr(modul_pembantu, "JEDA_HENTI", 2)
    mulai = time.monotonic()
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalankan("keras-kepala", timeout=1)
    lama = time.monotonic() - mulai
    assert e.value.kode == "waktu"
    assert 3 <= lama < 15
    anak = catatan()[0]["anak"]
    batas = time.monotonic() + 5
    while _hidup(anak) and time.monotonic() < batas:
        time.sleep(0.1)
    assert not _hidup(anak)


def test_keluaran_tepat_batas_diterima(pembantu, monkeypatch):
    monkeypatch.setenv("PALSU_UKURAN", str(BATAS_KELUARAN))
    assert len(pembantu.jalankan("pas")) == BATAS_KELUARAN


def test_keluaran_melebihi_batas_menjadi_galat_tetap(pembantu):
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalankan("berisik")
    assert e.value.kode == "lain"
    assert e.value.pesan == modul_pembantu.PESAN_MELIMPAH


@pytest.mark.parametrize("perintah", ["banjir", "banjir-galat"])
def test_banjir_keluaran_dihentikan_tanpa_menampung(pembantu, monkeypatch, perintah):
    """Keluaran tanpa akhir (wp-cli dari salinan site yang disusupi) tidak ditulis ke disk
    dan tidak ditampung melebihi batas: proses dihentikan begitu batasnya lewat."""
    def tanpa_berkas_sementara(*_a, **_k):
        raise AssertionError("keluaran pembantu tidak boleh ditampung di berkas sementara")

    monkeypatch.setattr("tempfile.TemporaryFile", tanpa_berkas_sementara)
    tambah_asli = modul_pembantu._Penampung.tambah
    terbesar = []

    def tambah(self, blok):
        hasil = tambah_asli(self, blok)
        terbesar.append((len(self.data), self.batas))
        return hasil

    monkeypatch.setattr(modul_pembantu._Penampung, "tambah", tambah)
    mulai = time.monotonic()
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalankan(perintah, timeout=60)
    assert e.value.kode == "lain"
    assert e.value.pesan == modul_pembantu.PESAN_MELIMPAH
    assert time.monotonic() - mulai < 15
    assert terbesar and all(n <= batas for n, batas in terbesar)


def test_db_impor_mengalirkan_berkas_berurutan(pembantu, catatan, tmp_path):
    a, b = tmp_path / "a.sql", tmp_path / "b.sql"
    a.write_bytes(b"SET NAMES utf8mb4;\n")
    b.write_bytes(b"INSERT INTO t VALUES (1);\n")
    pembantu.db_impor("toko", [a, b])
    assert catatan()[0] == {"argv": ["db-impor", "toko"], "stdin": "SET NAMES utf8mb4;\nINSERT INTO t VALUES (1);\n"}


def test_db_impor_berkas_tidak_terbaca_menghentikan_tanpa_eof(pembantu, catatan, tmp_path, monkeypatch):
    asli = modul_pembantu._hentikan
    stdin_saat_dihentikan = []

    def hentikan(proses):
        stdin_saat_dihentikan.append(proses.stdin.closed)
        asli(proses)

    monkeypatch.setattr(modul_pembantu, "_hentikan", hentikan)
    a = tmp_path / "a.sql"
    a.write_bytes(b"DROP TABLE wp_posts;\n")
    with pytest.raises(GalatPembantu) as e:
        pembantu.db_impor("toko", [a, tmp_path / "tidak-ada.sql"])
    assert e.value.kode == "lain"
    assert str(tmp_path) not in e.value.pesan
    # Skrip dihentikan sebelum stdin ditutup: EOF lebih dulu membuatnya
    # "mengimpor" potongan separuh.
    assert stdin_saat_dihentikan == [False]
    assert catatan() == []


def test_db_impor_berhenti_mengalirkan_setelah_tenggat(pembantu, catatan, tmp_path):
    """Skrip tidak membaca stdin dan cucunya (anak root di bawah sudo) tetap memegang
    pipa: penulisan yang terblokir ditinggalkan, panggilan kembali tepat waktu."""
    besar = tmp_path / "besar.sql"
    besar.write_bytes(b"INSERT INTO t VALUES (1);\n" * 200_000)  # ~5 MB, jauh di atas buffer pipa
    mulai = time.monotonic()
    try:
        with pytest.raises(GalatPembantu) as e:
            pembantu.jalankan("tuli", masukan=[besar], timeout=1)
        lama = time.monotonic() - mulai
    finally:
        for c in catatan():
            if "cucu" in c:
                try:
                    os.kill(c["cucu"], getattr(signal, "SIGKILL", signal.SIGTERM))
                except OSError:
                    pass
    assert e.value.kode == "waktu"
    assert lama < 1 + modul_pembantu.JEDA_HENTI + 3


def test_status_diurai_dan_disaring(pembantu, monkeypatch):
    monkeypatch.setenv("PALSU_STATUS", json.dumps({
        "mem_tersedia": 4294967296, "disk_total": 200, "disk_bebas": 60,
        "container": {"wp-toko": {"berjalan": True}, "wp-lain": {"berjalan": "ya"}, "wp-../x": {"berjalan": True},
                      "wpmgr-stg-db": {"berjalan": True}},
        "akses": {"toko": 1790000000, "JAHAT": 1, "lain": "x"},
    }))
    s = pembantu.status()
    assert (s.mem_tersedia, s.disk_total, s.disk_bebas) == (4294967296, 200, 60)
    assert s.container == {"toko": True, "lain": False}
    assert s.akses == {"toko": 1790000000}


@pytest.mark.parametrize("teks", [
    "bukan json", "[]", json.dumps({"mem_tersedia": -1, "disk_total": 1, "disk_bebas": 1}),
    json.dumps({"mem_tersedia": True, "disk_total": 1, "disk_bebas": 1}), json.dumps({"disk_total": 1}),
])
def test_status_rusak_ditolak(teks):
    with pytest.raises(GalatPembantu) as e:
        urai_status(teks)
    assert e.value.kode == "status"


def test_sandi_dan_htpasswd():
    sandi = sandi_baru()
    assert len(sandi) >= 16
    h = hash_sandi(sandi)
    assert re.fullmatch(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}", h)
    assert bcrypt.checkpw(sandi.encode(), h.encode())


def test_tulis_akses_router_tidak_mengikuti_symlink_sementara(tmp_path):
    luar = tmp_path / "luar.txt"
    luar.write_bytes(b"asli")
    (tmp_path / "router").mkdir()
    try:
        os.symlink(luar, tmp_path / "router" / "toko.htpasswd.tmp")
    except OSError:
        pytest.skip("symlink tidak diizinkan di sistem ini")
    tulis_akses_router(tmp_path, "toko", hash_sandi("x"), "e" * 64)
    assert luar.read_bytes() == b"asli"
    nama = sorted(p.name for p in (tmp_path / "router").iterdir())
    assert nama == ["toko.htpasswd", "toko.htpasswd.tmp", "toko.rahasia"]
    if hasattr(os, "O_NOFOLLOW"):
        assert (tmp_path / "router" / "toko.htpasswd").stat().st_mode & 0o777 == 0o600


def test_stderr_di_log_satu_baris(pembantu, catatan, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="wpmgr.staging.pembantu")
    monkeypatch.setenv("PALSU_KELUAR", "4")
    monkeypatch.setenv("PALSU_STDERR", "baris satu\r\nWARNING palsu: disuntikkan\n")
    with pytest.raises(GalatPembantu):
        pembantu.jalan("toko")
    catatan_log = [r.getMessage() for r in caplog.records if "gagal (kode keluar" in r.getMessage()]
    assert len(catatan_log) == 1
    assert "\n" not in catatan_log[0] and "\r" not in catatan_log[0]
    assert "baris satu\\r\\nWARNING palsu" in catatan_log[0]


def test_tulis_dan_hapus_akses_router(tmp_path):
    h = hash_sandi("rahasia-preview")
    tulis_akses_router(tmp_path, "toko", h, "e" * 64)
    assert (tmp_path / "router" / "toko.htpasswd").read_bytes() == f"staging:{h}\n".encode()
    assert (tmp_path / "router" / "toko.rahasia").read_bytes() == b"e" * 64
    with pytest.raises(ValueError):
        tulis_akses_router(tmp_path, "Toko", h, "e" * 64)
    with pytest.raises(ValueError):
        tulis_akses_router(tmp_path, "toko", h + "\nserver {", "e" * 64)
    with pytest.raises(ValueError):
        tulis_akses_router(tmp_path, "toko", h, "bukan-hex")
    hapus_akses_router(tmp_path, "toko")
    assert not (tmp_path / "router" / "toko.htpasswd").exists()


def test_tautan_masuk_sesuai_secure_link_nginx():
    rahasia, host = "e" * 64, "toko.staging.contoh.id"
    tautan = tautan_masuk(rahasia, host, "abc.def+/", 1_790_000_000)
    e = 1_790_000_000 + 43200
    m = base64.urlsafe_b64encode(hashlib.md5(f"{e}{host} {rahasia}".encode()).digest()).decode().rstrip("=")
    assert tautan == f"/__wpmgr_masuk?e={e}&m={m}&sso=abc.def%2B%2F"
    assert cookie_akses(rahasia, host, 1_790_000_000) == {"wpmgr_stg_m": m, "wpmgr_stg_e": str(e)}
