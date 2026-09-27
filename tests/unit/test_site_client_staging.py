import json
import socket
import threading
import time

import httpx
import pytest

from wpmgr import site_client
from wpmgr.errors import (
    BAD_RESPONSE,
    BERKAS_HILANG,
    STAGING_GAGAL,
    STAGING_MATI,
    TERLALU_BESAR,
    TRANSIENT,
    UNKNOWN,
    SiteError,
)
from wpmgr.signing import verify
from wpmgr.site_client import SiteClient
from wpmgr.staging.paket import BATAS_PAKET, susun

SECRET = "f" * 64


def klien(handler):
    return SiteClient("https://contoh.test", "s1", SECRET, client=httpx.Client(transport=httpx.MockTransport(handler)))


def _cek_tanda_tangan(r: httpx.Request):
    assert verify(SECRET, r.headers["X-Wpmgr-Signature"], r.method, r.url.path, int(r.headers["X-Wpmgr-Timestamp"]),
                  r.headers["X-Wpmgr-Nonce"], r.content)


def test_manifest_query_tidak_ditandatangani():
    diminta = []

    def h(r):
        diminta.append(r)
        _cek_tanda_tangan(r)
        return httpx.Response(200, json={"berkas": [], "lagi": False})

    assert klien(h).staging_manifest("a/b c.txt", 100) == {"berkas": [], "lagi": False}
    assert diminta[0].url.path == "/wp-json/wpmgr/v1/staging/manifest"
    assert diminta[0].url.params["kursor"] == "a/b c.txt"
    assert diminta[0].url.params["batas"] == "100"
    # Tanpa kompresi: batas byte berlaku pada byte yang sungguh diterima.
    assert diminta[0].headers["Accept-Encoding"] == "identity"


def test_file_mengurai_paket():
    data = susun({"berkas": [{"path": "a.txt", "mtime": 5}]}, [b"isi"])

    def h(r):
        _cek_tanda_tangan(r)
        assert json.loads(r.content) == {"berkas": ["a.txt"]}
        return httpx.Response(200, content=data, headers={"Content-Type": "application/octet-stream"})

    meta, bagian = klien(h).staging_file(["a.txt"])
    assert bagian == [b"isi"]
    assert meta["berkas"][0]["mtime"] == 5


def test_rentang_dan_tabel_mengirim_body_yang_benar():
    diminta = []
    data = susun({"berkas": [{"path": "x"}]}, [b"q"])

    def h(r):
        diminta.append(json.loads(r.content))
        return httpx.Response(200, content=data)

    k = klien(h)
    k.staging_rentang("big.bin", 8, 16)
    k.staging_tabel("wp_posts", None)
    k.staging_tabel("wp_posts", "abc=")
    assert diminta == [{"rentang": {"path": "big.bin", "dari": 8, "panjang": 16}},
                       {"tabel": "wp_posts", "kursor": ""}, {"tabel": "wp_posts", "kursor": "abc="}]


def test_paket_rusak_dan_html_menjadi_bad_response():
    data = susun({"berkas": [{"path": "a"}]}, [b"isi"])
    for balasan in (data[:-1] + b"X", b"<!DOCTYPE html><p>cache</p>"):
        with pytest.raises(SiteError) as e:
            klien(lambda r, b=balasan: httpx.Response(200, content=b)).staging_file(["a"])
        assert e.value.error_class == BAD_RESPONSE


def test_respons_melebihi_batas_dipotong():
    besar = b"W" * (13 * 1024 * 1024)
    with pytest.raises(SiteError) as e:
        klien(lambda r: httpx.Response(200, content=besar)).staging_file(["a"])
    assert e.value.error_class == BAD_RESPONSE
    assert "batas" in e.value.pesan


def test_paket_penuh_di_batas_tetap_diterima():
    # 8 MiB isi + meta: persis anggaran connector, tidak boleh ditolak batas stream.
    data = susun({"berkas": [{"path": "a"}]}, [b"x" * (8 * 1024 * 1024)])
    assert len(data) <= BATAS_PAKET
    _, bagian = klien(lambda r: httpx.Response(200, content=data)).staging_rentang("a", 0, 8 * 1024 * 1024)
    assert len(bagian[0]) == 8 * 1024 * 1024


def test_stream_yang_menetes_dihentikan_tenggat_total(monkeypatch):
    # Setiap potong datang jauh sebelum timeout baca httpx, jadi hanya tenggat
    # total yang bisa menghentikannya (putusan F11).
    monkeypatch.setattr(site_client, "TENGGAT_STAGING", 0.3)

    def menetes():
        while True:
            time.sleep(0.02)
            yield b"W"

    for panggil in (lambda k: k.staging_file(["a"]), lambda k: k.staging_manifest()):
        mulai = time.monotonic()
        with pytest.raises(SiteError) as e:
            panggil(klien(lambda r: httpx.Response(200, content=menetes())))
        assert time.monotonic() - mulai < 3
        assert e.value.error_class == TRANSIENT
        assert "tenggat" in e.value.pesan


def test_tenggat_total_pada_panggilan_berefek_adalah_unknown(monkeypatch):
    monkeypatch.setattr(site_client, "TENGGAT_STAGING_TERAPKAN", 0.3)

    def menetes():
        while True:
            time.sleep(0.02)
            yield b" "

    with pytest.raises(SiteError) as e:
        klien(lambda r: httpx.Response(200, content=menetes())).staging_terapkan({"langkah": "tukar"})
    assert e.value.error_class == UNKNOWN


def test_json_bersarang_dalam_dan_bukan_objek_menjadi_bad_response():
    for isi in (b"[" * 100000 + b"]" * 100000, b"[1,2]", b"\xff\xfe"):
        with pytest.raises(SiteError) as e:
            klien(lambda r, b=isi: httpx.Response(200, content=b)).staging_manifest()
        assert e.value.error_class == BAD_RESPONSE
        with pytest.raises(SiteError) as e:
            klien(lambda r, b=isi: httpx.Response(200, content=b)).staging_unggah(b"x")
        assert e.value.error_class == BAD_RESPONSE


def test_staging_mati_dan_terlalu_besar_diklasifikasikan():
    mati = {"code": "wpmgr_staging_mati", "message": "Staging tidak diizinkan", "data": {"status": 403}}
    with pytest.raises(SiteError) as e:
        klien(lambda r: httpx.Response(403, json=mati)).staging_manifest()
    assert e.value.error_class == STAGING_MATI
    besar = {"code": "wpmgr_staging_terlalu_besar", "message": "x", "data": {"status": 413}}
    with pytest.raises(SiteError) as e:
        klien(lambda r: httpx.Response(413, json=besar)).staging_file(["a"])
    assert e.value.error_class == TERLALU_BESAR
    hilang = {"code": "wpmgr_staging_tidak_ada", "message": "Berkas tidak ada.", "data": {"status": 404}}
    with pytest.raises(SiteError) as e:
        klien(lambda r: httpx.Response(404, json=hilang)).staging_rentang("a", 0, 10)
    assert e.value.error_class == BERKAS_HILANG


def test_baris_tabel_terlalu_besar_gagal_permanen():
    baris = {"code": "wpmgr_staging_baris_terlalu_besar",
             "message": "Satu baris pada tabel `wp_x` melebihi batas ukuran respons dan tidak dapat diekspor.",
             "data": {"status": 413}}
    with pytest.raises(SiteError) as e:
        klien(lambda r: httpx.Response(413, json=baris)).staging_tabel("wp_x", None)
    assert e.value.error_class == STAGING_GAGAL
    assert not e.value.dapat_diulang
    assert "wp_x" in e.value.pesan


def test_unggah_biner_dan_terapkan_dengan_header_lewati():
    diminta = []

    def h(r):
        diminta.append(r)
        _cek_tanda_tangan(r)
        return httpx.Response(200, json={"ok": True})

    k = klien(h)
    k.staging_unggah(b"WPMGRPAK1\n\x00\xff")
    k.staging_terapkan({"dorong_id": "a" * 32, "langkah": "tukar", "token": "b" * 32}, token_lewati="b" * 32)
    assert diminta[0].headers["Content-Type"] == "application/octet-stream"
    assert diminta[0].content == b"WPMGRPAK1\n\x00\xff"
    assert diminta[1].headers["X-Wpmgr-Lewati"] == "b" * 32
    assert "X-Wpmgr-Lewati" not in diminta[0].headers


def test_timeout_unggah_unknown_manifest_transient():
    def h(r):
        raise httpx.ReadTimeout("lambat", request=r)

    with pytest.raises(SiteError) as e:
        klien(h).staging_unggah(b"x")
    assert e.value.error_class == UNKNOWN
    with pytest.raises(SiteError) as e:
        klien(h).staging_manifest()
    assert e.value.error_class == TRANSIENT


# ---- fix putaran 1 -----------------------------------------------------------


@pytest.mark.parametrize("panggil,kelas", [
    (lambda k: k.staging_file(["a"]), TRANSIENT),
    (lambda k: k.staging_manifest(), TRANSIENT),
    (lambda k: k.staging_unggah(b"x"), UNKNOWN),
    (lambda k: k.staging_terapkan({"langkah": "tukar"}), UNKNOWN),
])
def test_tenggat_total_mencakup_tunggu_sebelum_header(monkeypatch, panggil, kelas):
    # Connector yang diam sebelum mengirim header: loop body belum pernah
    # berjalan, jadi tenggat harus berlaku atas seluruh permintaan.
    monkeypatch.setattr(site_client, "TENGGAT_STAGING", 0.3)
    monkeypatch.setattr(site_client, "TENGGAT_STAGING_TERAPKAN", 0.3)

    def diam(r):
        time.sleep(2)
        return httpx.Response(200, json={"ok": True})

    mulai = time.monotonic()
    with pytest.raises(SiteError) as e:
        panggil(klien(diam))
    assert time.monotonic() - mulai < 1.5
    assert e.value.error_class == kelas
    assert "tenggat" in e.value.pesan


def test_tenggat_total_memutus_soket_yang_macet(monkeypatch):
    """Server TCP sungguhan yang menerima koneksi lalu tidak pernah menjawab
    (jabat tangan TLS macet). Soket TLS belum ada di tahap ini, jadi yang
    memutusnya adalah timeout koneksi yang dijepit ke tenggat, bukan
    shutdown; jalur shutdown diuji di test_header_menetes_diputus_lewat_shutdown."""
    monkeypatch.setattr(site_client, "TENGGAT_STAGING", 0.3)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    tertutup = threading.Event()

    def layani():
        conn, _ = server.accept()
        conn.settimeout(30)
        try:
            while conn.recv(4096):
                pass
            tertutup.set()
        except OSError:
            tertutup.set()
        finally:
            conn.close()

    t = threading.Thread(target=layani, daemon=True)
    t.start()
    try:
        k = SiteClient(f"https://127.0.0.1:{port}", "s1", SECRET)
        mulai = time.monotonic()
        with pytest.raises(SiteError) as e:
            k.staging_manifest()
        assert time.monotonic() - mulai < 1.5
        # Tenggat total atau timeout koneksi yang dijepit ke tenggat, mana
        # yang lebih dulu; keduanya sementara karena belum ada yang terkirim.
        assert e.value.error_class == TRANSIENT
        assert tertutup.wait(3), "soket ke connector tidak diputus sesudah tenggat"
    finally:
        server.close()


def test_soket_tls_yang_tertangkap_di_shutdown_saat_tenggat(monkeypatch):
    # Sesudah jabat tangan TLS, recv() yang terblokir (header yang diteteskan)
    # hanya bisa dibangunkan dengan shutdown soket TLS dari thread lain.
    monkeypatch.setattr(site_client, "TENGGAT_STAGING", 0.3)

    class SoketTls(socket.socket):
        """Meniru SSLSocket: shutdown() miliknya melepas objek TLS lebih dulu
        dan tidak boleh dipakai; socket.socket.shutdown yang dipanggil."""

        dipanggil = False

        def shutdown(self, how):
            SoketTls.dipanggil = True
            super().shutdown(how)

    a, b = socket.socketpair()
    s = SoketTls(fileno=a.detach())
    b.settimeout(5)

    class AliranPalsu:
        def get_extra_info(self, nama):
            return s if nama == "socket" else None

    def h(r):
        r.extensions["trace"]("connection.start_tls.complete", {"return_value": AliranPalsu()})
        time.sleep(2)
        raise httpx.ReadError("soket diputus", request=r)

    try:
        with pytest.raises(SiteError) as e:
            klien(h).staging_manifest()
        assert "tenggat" in e.value.pesan
        assert b.recv(1) == b"", "soket tidak di-shutdown"
        assert SoketTls.dipanggil is False
    finally:
        s.close()
        b.close()


# ---- server TLS sungguhan (fix putaran 2) -----------------------------------


@pytest.fixture
def server_tls(tmp_path):
    """Server HTTPS kecil di 127.0.0.1 dengan sertifikat buatan sendiri.

    `perilaku(conn)` dijalankan per koneksi sesudah jabat tangan TLS.
    Mengembalikan (buat_server, konteks_klien).
    """
    import datetime
    import ipaddress
    import ssl

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    kunci = ec.generate_private_key(ec.SECP256R1())
    nama = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    sekarang = datetime.datetime.now(datetime.timezone.utc)
    sert = (x509.CertificateBuilder().subject_name(nama).issuer_name(nama).public_key(kunci.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(sekarang - datetime.timedelta(days=1))
            .not_valid_after(sekarang + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(kunci, hashes.SHA256()))
    (tmp_path / "sert.pem").write_bytes(sert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "kunci.pem").write_bytes(kunci.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx_server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx_server.load_cert_chain(tmp_path / "sert.pem", tmp_path / "kunci.pem")
    ctx_klien = ssl.create_default_context(cafile=str(tmp_path / "sert.pem"))
    daftar = []

    def buat(perilaku):
        dengar = socket.socket()
        dengar.bind(("127.0.0.1", 0))
        dengar.listen(8)
        daftar.append(dengar)

        def terima():
            while True:
                try:
                    conn, _ = dengar.accept()
                except OSError:
                    return
                try:
                    tls = ctx_server.wrap_socket(conn, server_side=True)
                except (OSError, ssl.SSLError):
                    conn.close()
                    continue
                threading.Thread(target=perilaku, args=(tls,), daemon=True).start()

        threading.Thread(target=terima, daemon=True).start()
        return dengar.getsockname()[1]

    yield buat, ctx_klien
    for d in daftar:
        d.close()


def _baca_permintaan(conn) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        potong = conn.recv(4096)
        if not potong:
            break
        data += potong
    return data


def test_setiap_panggilan_staging_membuka_koneksi_baru(server_tls):
    buat, ctx = server_tls
    permintaan = []

    def jawab(conn):
        with conn:
            permintaan.append(_baca_permintaan(conn))
            badan = b'{"berkas":[],"lagi":false}'
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(badan)).encode() + b"\r\n\r\n" + badan)
            # Tidak menutup lebih dulu: klien yang memakai ulang koneksi
            # (keep-alive) akan mengirim permintaan kedua lewat soket ini.
            conn.settimeout(3)
            try:
                while conn.recv(4096):
                    pass
            except OSError:
                pass

    port = buat(jawab)
    k = SiteClient(f"https://127.0.0.1:{port}", "s1", SECRET,
                   klien_staging=site_client.buat_klien_staging(verify=ctx))
    assert k.staging_manifest() == {"berkas": [], "lagi": False}
    assert k.staging_manifest() == {"berkas": [], "lagi": False}
    # Dua permintaan, dua koneksi (setiap koneksi hanya melihat satu
    # permintaan), masing-masing meminta ditutup.
    assert len(permintaan) == 2
    assert all(p.count(b"GET ") == 1 and b"connection: close" in p.lower() for p in permintaan)


def test_header_menetes_diputus_lewat_shutdown(monkeypatch, server_tls):
    """Server meneteskan satu baris header tiap 0,1 detik: setiap recv()
    berhasil sebelum timeout baca (yang dijepit ke 0,3 detik), jadi hanya
    shutdown soket TLS dari thread pemanggil yang bisa menghentikan pekerja."""
    monkeypatch.setattr(site_client, "TENGGAT_STAGING", 0.3)
    buat, ctx = server_tls
    diputus = threading.Event()

    def menetes(conn):
        _baca_permintaan(conn)
        try:
            conn.sendall(b"HTTP/1.1 200 OK\r\n")
            for _ in range(600):
                conn.sendall(b"X-A: b\r\n")
                time.sleep(0.1)
        except OSError:
            diputus.set()
        finally:
            conn.close()

    port = buat(menetes)
    k = SiteClient(f"https://127.0.0.1:{port}", "s1", SECRET,
                   klien_staging=site_client.buat_klien_staging(verify=ctx))
    mulai = time.monotonic()
    with pytest.raises(SiteError) as e:
        k.staging_manifest()
    assert time.monotonic() - mulai < 1.5
    assert "tenggat" in e.value.pesan
    assert diputus.wait(5), "pekerja tetap membaca header sesudah tenggat"


@pytest.mark.parametrize("pengecualian,kelas_tulis", [
    (httpx.ReadError, UNKNOWN),
    (httpx.RemoteProtocolError, UNKNOWN),
    (httpx.WriteError, UNKNOWN),
    (httpx.ConnectError, TRANSIENT),
    (httpx.ConnectTimeout, TRANSIENT),
])
def test_galat_sesudah_terkirim_pada_panggilan_berefek_adalah_unknown(pengecualian, kelas_tulis):
    def h(r):
        raise pengecualian("putus", request=r)

    with pytest.raises(SiteError) as e:
        klien(h).staging_unggah(b"x")
    assert e.value.error_class == kelas_tulis
    with pytest.raises(SiteError) as e:
        klien(h).staging_terapkan({"langkah": "tukar"})
    assert e.value.error_class == kelas_tulis
    # Panggilan baca tetap sementara apa pun galatnya.
    with pytest.raises(SiteError) as e:
        klien(h).staging_manifest()
    assert e.value.error_class == TRANSIENT


def test_hasil_yang_sudah_ada_tidak_dilaporkan_timeout(monkeypatch):
    # Pekerja sudah menyimpan hasil tetapi masih menutup koneksi saat
    # tenggat lewat: hasilnya dipakai, bukan dilaporkan timeout.
    class MasihMenutup(threading.Thread):
        """Pekerja yang sudah menyimpan hasil tetapi (menurut is_alive) belum
        keluar ketika join() pemanggil habis."""

        def join(self, timeout=None):
            super().join()

        def is_alive(self):
            return True

    monkeypatch.setattr(site_client.threading, "Thread", MasihMenutup)
    k = klien(lambda r: httpx.Response(200, json={"ok": True}))
    assert k.staging_manifest() == {"ok": True}
    # Tanpa hasil, pekerja yang masih hidup tetap dilaporkan melewati tenggat.
    with pytest.raises(SiteError) as e:
        klien(lambda r: (_ for _ in ()).throw(httpx.ReadError("x", request=r))).staging_manifest()
    assert "tenggat" in e.value.pesan


def test_content_encoding_selain_identity_ditolak():
    data = susun({"berkas": [{"path": "a"}]}, [b"isi"])
    with pytest.raises(SiteError) as e:
        # Body sebagai stream (seperti transport sungguhan), supaya httpx
        # tidak mendekodenya lebih dulu.
        klien(lambda r: httpx.Response(200, content=iter([data]),
                                       headers={"Content-Encoding": "gzip"})).staging_file(["a"])
    assert e.value.error_class == BAD_RESPONSE
    meta, _ = klien(lambda r: httpx.Response(200, content=data,
                                             headers={"Content-Encoding": "identity"})).staging_file(["a"])
    assert meta["berkas"][0]["path"] == "a"
