import os
import time

import httpx
import pytest

from wpmgr.staging import uji
from wpmgr.staging.uji import baca_log_baru, nilai_uji, probe, slug_wpcli, urai_paket


@pytest.mark.parametrize("tipe,slug,hasil", [
    ("plugin", "akismet/akismet.php", "akismet"), ("plugin", "hello.php", "hello"),
    ("theme", "twentytwentyfour", "twentytwentyfour"), ("core", "core", "core"),
    ("plugin", "--exec/x.php", None), ("theme", "../x", None), ("plugin", "Akismet/a.php", None),
])
def test_slug_wpcli(tipe, slug, hasil):
    assert slug_wpcli(tipe, slug) == hasil


def test_urai_paket():
    hasil = urai_paket([{"tipe": "plugin", "slug": "akismet/akismet.php", "dari": "5.2", "ke": "5.3.1"}])
    assert hasil == [{"tipe": "plugin", "slug": "akismet/akismet.php", "wpcli": "akismet", "dari": "5.2", "ke": "5.3.1"}]
    for buruk in (None, [], [{"tipe": "plugin", "slug": "a/a.php", "ke": "1;id"}],
                  [{"tipe": "eval", "slug": "a", "ke": "1"}], [{"tipe": "plugin", "slug": 5, "ke": "1"}],
                  [{"tipe": "plugin", "slug": "a/a.php", "ke": "1"}] * 21):
        with pytest.raises(ValueError):
            urai_paket(buruk)


H = {"status": 200, "judul": "Beranda", "ukuran": 10000}


def test_nilai_lolos():
    assert nilai_uji({"/": H}, {"/": {**H, "ukuran": 6000}}, [], []) == ("lolos", [])
    assert nilai_uji({"/": H}, {"/": {**H, "status": 302}}, [], [])[0] == "lolos"


def test_nilai_gagal_dengan_alasan_per_pemeriksaan():
    hasil, alasan = nilai_uji(
        {"/": H, "/toko/": H, "/wp-login.php": H},
        {"/": {**H, "status": 500}, "/toko/": {**H, "ukuran": 4000}, "/wp-login.php": {**H, "status": 0, "ukuran": 0}},
        ["PHP Fatal error: x"], [{"slug": "akismet/akismet.php", "ok": False, "pesan": "Perintah wp-cli di staging gagal."}],
    )
    assert hasil == "gagal"
    assert alasan == [
        "Update akismet/akismet.php gagal: Perintah wp-cli di staging gagal.",
        "/ membalas HTTP 500",
        "/toko/ menyusut dari 9,8 KB ke 3,9 KB",
        "/wp-login.php tidak dapat dihubungi",
        "1 error fatal baru di log PHP staging",
    ]


def test_baca_log_baru_hanya_bagian_baru_dan_tanpa_path_container(tmp_path):
    log = tmp_path / "php-error.log"
    log.write_bytes(b"[x] PHP Fatal error:  lama in /var/www/html/a.php:1\n")
    posisi = log.stat().st_size
    with open(log, "ab") as f:
        f.write(b"[y] PHP Warning:  bukan fatal\n")
        f.write(b"[z] PHP Fatal error:  Uncaught Error: x() in /var/www/html/wp-content/plugins/p/p.php:3\n")
        f.write(b"[z] PHP Parse error:  \xff sintaks\n")
    baru = baca_log_baru(log, posisi)
    assert baru == ["[z] PHP Fatal error:  Uncaught Error: x() in wp-content/plugins/p/p.php:3",
                    "[z] PHP Parse error:  � sintaks"]
    assert baca_log_baru(log, 10**9)[0].startswith("[x] PHP Fatal error")
    assert baca_log_baru(tmp_path / "tidak-ada.log", 0) == []


def test_probe_membaca_judul_dan_ukuran():
    diminta = []
    isi = "<html><head><title>\n Toko\tContoh </title></head></html>"

    def h(r):
        diminta.append(r)
        return httpx.Response(200, text=isi)

    hasil = probe(httpx.Client(transport=httpx.MockTransport(h)), "http://127.0.0.1:8090", "t.staging.id",
                  "wpmgr_stg_m=a; wpmgr_stg_e=1", "/toko/")
    assert hasil == {"status": 200, "judul": "Toko Contoh", "ukuran": len(isi.encode())}
    assert diminta[0].headers["Host"] == "t.staging.id"
    assert diminta[0].headers["Cookie"] == "wpmgr_stg_m=a; wpmgr_stg_e=1"
    assert str(diminta[0].url) == "http://127.0.0.1:8090/toko/"


def test_probe_gagal_koneksi():
    def h(r):
        raise httpx.ConnectError("tolak", request=r)

    assert probe(httpx.Client(transport=httpx.MockTransport(h)), "http://x", "h", "", "/") == \
        {"status": 0, "judul": None, "ukuran": 0}


# ---- tambahan: masukan dari PHP staging yang tidak dipercaya ----------------


def _klien(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_probe_menetes_dihentikan_tenggat_total(monkeypatch):
    # Setiap potong datang jauh sebelum timeout baca httpx; hanya tenggat
    # total yang bisa menghentikannya (putusan F11).
    monkeypatch.setattr(uji, "TENGGAT_PROBE", 0.3)

    def menetes():
        while True:
            time.sleep(0.02)
            yield b"W"

    mulai = time.monotonic()
    hasil = probe(_klien(lambda r: httpx.Response(200, content=menetes())), "http://x", "h", "", "/")
    assert time.monotonic() - mulai < 3
    assert hasil == {"status": 0, "judul": None, "ukuran": 0}


def test_probe_diam_sebelum_header_dihentikan_tenggat(monkeypatch):
    monkeypatch.setattr(uji, "TENGGAT_PROBE", 0.3)

    def diam(r):
        time.sleep(2)
        return httpx.Response(200, text="<title>x</title>")

    mulai = time.monotonic()
    assert probe(_klien(diam), "http://x", "h", "", "/")["status"] == 0
    assert time.monotonic() - mulai < 1.5


def test_probe_membatasi_byte_dan_tidak_mengikuti_redirect(monkeypatch):
    monkeypatch.setattr(uji, "BATAS_HTML", 1000)
    dibaca = []

    def besar():
        for _ in range(100_000):
            dibaca.append(1)
            yield b"<p>" + b"x" * 97

    hasil = probe(_klien(lambda r: httpx.Response(200, content=besar())), "http://x", "h", "", "/")
    assert hasil["status"] == 200 and hasil["ukuran"] == 1000
    assert len(dibaca) < 100, "stream tetap dibaca sesudah batas"
    diminta = []

    def alih(r):
        diminta.append(str(r.url))
        return httpx.Response(302, headers={"Location": "http://jahat.test/"})

    assert probe(_klien(alih), "http://x", "h", "", "/")["status"] == 302
    assert diminta == ["http://x/"]


def test_probe_judul_dibatasi_dan_tahan_masukan_jahat():
    judul_panjang = "<title>" + "A\x00" * 5000 + "</title>"
    hasil = probe(_klien(lambda r: httpx.Response(200, text=judul_panjang)), "http://x", "h", "", "/")
    assert "\x00" not in hasil["judul"] and len(hasil["judul"]) <= 200
    # Ribuan <title tanpa penutup: penguraian tetap linear (tanpa ReDoS).
    jahat = "<title " * 200_000
    mulai = time.monotonic()
    hasil = probe(_klien(lambda r: httpx.Response(200, text=jahat)), "http://x", "h", "", "/")
    assert hasil["judul"] is None
    assert time.monotonic() - mulai < 2
    # <titlebar> bukan <title>; atribut dan huruf besar diterima.
    isi = "<TITLEBAR>x</TITLEBAR><Title lang='id'>Beranda &amp; Toko</TITLE>"
    assert probe(_klien(lambda r: httpx.Response(200, text=isi)), "http://x", "h", "", "/")["judul"] == \
        "Beranda & Toko"
    # Entitas didekode sebelum dibersihkan: &#0; dan &nbsp; tidak lolos mentah.
    isi = "<title>A&#0;B&nbsp;&lt;C&gt;&#x27;</title>"
    assert probe(_klien(lambda r: httpx.Response(200, text=isi)), "http://x", "h", "", "/")["judul"] == \
        "A�B <C>'"


@pytest.mark.parametrize("tipe,slug,ke", [
    ("core", "core", "abc"),  # skrip pembantu mewajibkan versi core diawali angka
    ("plugin", "a/../../x.php", "1.0"),
    ("plugin", "a/b/c.php", "1.0"),
    ("plugin", "a/<script>.php", "1.0"),
    ("theme", "t", "--exec"),
    ("plugin", "a/a.php", "1\n"),
])
def test_urai_paket_menolak_slug_dan_versi_berbahaya(tipe, slug, ke):
    with pytest.raises(ValueError):
        urai_paket([{"tipe": tipe, "slug": slug, "ke": ke}])


def test_urai_paket_core_dan_dari_dibersihkan():
    hasil = urai_paket([{"tipe": "core", "slug": "core", "ke": "6.6.1", "dari": "6.5\x00" + "x" * 100}])
    assert hasil[0]["wpcli"] == "core" and hasil[0]["ke"] == "6.6.1"
    assert "\x00" not in hasil[0]["dari"] and len(hasil[0]["dari"]) <= 30


def test_baca_log_baru_berbaris_panjang_dan_jumlah_sebenarnya(tmp_path, monkeypatch):
    monkeypatch.setattr(uji, "BATAS_LOG", 4096)
    log = tmp_path / "php-error.log"
    # Peringatan yang membanjir (berkali-kali BATAS_LOG) tidak menyembunyikan
    # fatal sesudahnya: seluruh wilayah baru dipindai per blok.
    log.write_bytes(b"[w] PHP Warning: " + b"w" * 100_000 + b"\n" + b"[f] PHP Fatal error: " + b"z" * 900 + b"\n")
    baru = baca_log_baru(log, 0)
    assert len(baru) == 1 and baru[0].startswith("[f] PHP Fatal error") and len(baru[0]) <= 300
    # Lebih dari MAKS_FATAL baris fatal: yang disimpan dipotong, jumlahnya tidak.
    log.write_bytes(b"PHP Fatal error: x\n" * 50)
    assert len(baca_log_baru(log, 0)) == uji.MAKS_FATAL
    hasil = uji.periksa_log_baru(log, 0)
    assert hasil["jumlah"] == 50 and len(hasil["baris"]) == uji.MAKS_FATAL and hasil["terpotong"] is False


def test_baca_log_baru_memindai_seluruh_wilayah_baru(tmp_path, monkeypatch):
    monkeypatch.setattr(uji, "BATAS_LOG", 1024)
    log = tmp_path / "php-error.log"
    log.write_bytes(b"x" * (1024 * 20 + 10) + b"\nPHP Fatal error: jauh\n")
    assert baca_log_baru(log, 0) == ["PHP Fatal error: jauh"]


def test_periksa_log_berhenti_di_tenggat_waktu_dan_melapor(tmp_path, monkeypatch):
    monkeypatch.setattr(uji, "BATAS_LOG", 1024)
    monkeypatch.setattr(uji, "TENGGAT_LOG", -1)
    log = tmp_path / "php-error.log"
    log.write_bytes(b"x" * (1024 * 5) + b"\nPHP Fatal error: jauh\n")
    hasil = uji.periksa_log_baru(log, 0)
    assert hasil["terpotong"] is True and hasil["jumlah"] == 0
    # Selesai tepat di blok terakhir: bukan terpotong.
    log.write_bytes(b"PHP Fatal error: dekat\n")
    assert uji.periksa_log_baru(log, 0) == {"baris": ["PHP Fatal error: dekat"], "jumlah": 1, "terpotong": False,
                                            "tidak_terbaca": False}
    # Log yang tidak ada: tidak ada fatal, bukan "tidak terbaca".
    assert uji.periksa_log_baru(tmp_path / "tidak-ada.log", 0)["tidak_terbaca"] is False


def test_baca_log_baru_menolak_symlink_dan_bukan_berkas(tmp_path):
    asli = tmp_path / "rahasia.txt"
    asli.write_bytes(b"PHP Fatal error: rahasia VPS\n")
    (tmp_path / "log").mkdir()
    tautan = tmp_path / "log" / "php-error.log"
    try:
        os.symlink(asli, tautan)
    except (OSError, NotImplementedError):
        pytest.skip("symlink tidak dapat dibuat di sistem ini")
    assert baca_log_baru(tautan, 0, akar=tmp_path) == []
    # Gagal tertutup: log yang ditukar container dengan symlink membuat uji gagal.
    assert uji.periksa_log_baru(tautan, 0, akar=tmp_path)["tidak_terbaca"] is True
    assert uji.nilai_uji({}, {}, [], [], log_tidak_terbaca=True) == ("gagal", ["log PHP staging tidak dapat diperiksa"])
    assert uji.ukuran_log(tmp_path, "log/php-error.log") == 0


def test_baca_log_baru_direktori_log_berupa_symlink(tmp_path):
    luar = tmp_path / "luar"
    luar.mkdir()
    (luar / "php-error.log").write_bytes(b"PHP Fatal error: dari luar\n")
    akar = tmp_path / "situs"
    akar.mkdir()
    try:
        os.symlink(luar, akar / "log", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink tidak dapat dibuat di sistem ini")
    assert baca_log_baru(akar / "log" / "php-error.log", 0, akar=akar) == []


def test_nilai_sebelum_nol_tidak_dianggap_menyusut():
    assert nilai_uji({"/": {"status": 0, "judul": None, "ukuran": 0}}, {"/": H}, [], []) == ("lolos", [])
    assert nilai_uji({}, {"/": H}, [], []) == ("lolos", [])


# ---- putusan R17: hanya regresi yang dihitung ------------------------------


def test_halaman_yang_sudah_gagal_sebelum_update_bukan_alasan():
    sebelum = {"/": {**H, "status": 500}, "/a/": {"status": 0, "judul": None, "ukuran": 0}, "/b/": H}
    sesudah = {"/": {**H, "status": 500}, "/a/": {"status": 0, "judul": None, "ukuran": 0}, "/b/": H}
    assert nilai_uji(sebelum, sesudah, [], []) == ("lolos", [])
    assert uji.catatan_uji(sebelum, sesudah) == [
        "/ sudah membalas HTTP 500 sebelum update",
        "/a/ sudah tidak dapat dihubungi sebelum update",
    ]
    # Hidup sebelum, mati sesudah: regresi.
    assert nilai_uji({"/": H}, {"/": {**H, "status": 503}}, [], []) == ("gagal", ["/ membalas HTTP 503"])
    # Tanpa data sebelum: tidak ada bukti ia sudah gagal, jadi dihitung gagal.
    assert nilai_uji({}, {"/": {**H, "status": 500}}, [], [])[0] == "gagal"
    assert uji.catatan_uji({}, {"/": {**H, "status": 500}}) == []


def test_aturan_menyusut_hanya_untuk_halaman_2xx_sebelum():
    # 3xx sebelum (mis. redirect ke login) lalu 200 kecil: bukan penyusutan.
    assert nilai_uji({"/": {**H, "status": 302}}, {"/": {**H, "ukuran": 100}}, [], []) == ("lolos", [])
    # 500 sebelum lalu 200 kecil: halaman membaik, bukan penyusutan.
    assert nilai_uji({"/": {**H, "status": 500}}, {"/": {**H, "ukuran": 100}}, [], []) == ("lolos", [])
    assert nilai_uji({"/": H}, {"/": {**H, "ukuran": 100}}, [], [])[0] == "gagal"


def test_jumlah_fatal_sebenarnya_dan_log_terpotong_gagal_tertutup():
    hasil, alasan = nilai_uji({}, {}, ["PHP Fatal error: x"] * 10, [], jumlah_fatal=37)
    assert hasil == "gagal" and alasan == ["37 error fatal baru di log PHP staging"]
    hasil, alasan = nilai_uji({}, {}, [], [], jumlah_fatal=0, log_terpotong=True)
    assert hasil == "gagal" and alasan == ["log PHP terlalu besar untuk diperiksa penuh"]
