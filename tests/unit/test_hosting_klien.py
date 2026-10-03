import json
import ssl
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import httpx
import pytest

from wpmgr import site_client
from wpmgr.crypto import enkripsi_secret
from wpmgr.errors import BAD_RESPONSE, STAGING_DITOLAK, TRANSIENT, SiteError
from wpmgr.hosting import umum
from wpmgr.hosting.umum import (
    METODE_BACA,
    KlienLamaBacaSaja,
    alamat_lama_sah,
    klien_lama,
)
from wpmgr.signing import verify
from wpmgr.site_client import SiteClient

SECRET = "f" * 64
IP_LAMA = "93.184.216.34"


def _cek_tanda_tangan(r: httpx.Request):
    assert verify(SECRET, r.headers["X-Wpmgr-Signature"], r.method, r.url.path, int(r.headers["X-Wpmgr-Timestamp"]),
                  r.headers["X-Wpmgr-Nonce"], r.content)


def test_alamat_tetap_mengirim_ke_ip_dengan_host_dan_sni():
    diminta = []

    def h(r):
        diminta.append(r)
        _cek_tanda_tangan(r)
        return httpx.Response(200, json={"berkas": [], "lagi": False, "ok": True})

    k = SiteClient("https://www.toko.co.id", "s1", SECRET, client=httpx.Client(transport=httpx.MockTransport(h)),
                   alamat_tetap=IP_LAMA)
    assert k.alamat_tetap == IP_LAMA
    k.ping()
    k.staging_manifest(None, batas=10)
    for r in diminta:
        assert r.url.scheme == "https" and r.url.host == IP_LAMA
        assert r.headers["host"] == "www.toko.co.id"
        assert r.extensions["sni_hostname"] == "www.toko.co.id"
    assert diminta[1].url.path == "/wp-json/wpmgr/v1/staging/manifest"


def test_tanpa_alamat_tetap_perilaku_lama():
    diminta = []

    def h(r):
        diminta.append(r)
        return httpx.Response(200, json={"ok": True})

    SiteClient("https://toko.co.id", "s1", SECRET, client=httpx.Client(transport=httpx.MockTransport(h))).ping()
    assert diminta[0].url.host == "toko.co.id"
    assert "sni_hostname" not in diminta[0].extensions


@pytest.mark.parametrize("alamat", ["toko.co.id", "::1", "2a02::1", "1.2.3", ""])
def test_alamat_tetap_harus_ipv4(alamat):
    with pytest.raises(ValueError):
        SiteClient("https://toko.co.id", "s1", SECRET, alamat_tetap=alamat)


# ---- A1: SNI dan verifikasi nama sertifikat lewat server TLS lokal ----------------


def _pem(tmp_path, nama_berkas: str, data: bytes):
    p = tmp_path / nama_berkas
    p.write_bytes(data)
    return p


def _sertifikat(tmp_path, nama: str):
    """CA uji + sertifikat daun untuk `nama` (ekstensi lengkap, lolos mode X509 strict)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    awal = datetime.now(timezone.utc) - timedelta(hours=1)
    akhir = awal + timedelta(days=2)
    kunci_ca = ec.generate_private_key(ec.SECP256R1())
    nama_ca = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CA uji WP Manager")])
    ca = (x509.CertificateBuilder().subject_name(nama_ca).issuer_name(nama_ca)
          .public_key(kunci_ca.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(awal).not_valid_after(akhir)
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                       crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(kunci_ca.public_key()), critical=False)
          .sign(kunci_ca, hashes.SHA256()))
    kunci = ec.generate_private_key(ec.SECP256R1())
    daun = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nama)]))
            .issuer_name(nama_ca).public_key(kunci.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(awal).not_valid_after(akhir)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(nama)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(kunci_ca.public_key()), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(kunci.public_key()), critical=False)
            .sign(kunci_ca, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    return (_pem(tmp_path, f"ca-{nama}.pem", ca.public_bytes(pem)),
            _pem(tmp_path, f"daun-{nama}.pem", daun.public_bytes(pem)),
            _pem(tmp_path, f"kunci-{nama}.pem", kunci.private_bytes(
                pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())))


class _Penangan(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.host_diterima.append(self.headers.get("Host"))
        isi = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(isi)))
        self.end_headers()
        self.wfile.write(isi)

    def log_message(self, *argumen):
        pass


class _ServerDiam(HTTPServer):
    def handle_error(self, request, client_address):
        # Jabat tangan yang sengaja ditolak klien (sertifikat salah) bukan galat test.
        pass


@contextmanager
def _server_tls(daun, kunci):
    srv = _ServerDiam(("127.0.0.1", 0), _Penangan)
    srv.host_diterima, srv.sni = [], []
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(daun), str(kunci))
    ctx.sni_callback = lambda sock, nama, c: srv.sni.append(nama)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def test_sni_memverifikasi_sertifikat_terhadap_nama_domain(tmp_path):
    ca, daun, kunci = _sertifikat(tmp_path, "nama-uji.test")
    with _server_tls(daun, kunci) as srv:
        port = srv.server_address[1]
        http = httpx.Client(verify=ssl.create_default_context(cafile=str(ca)))
        k = SiteClient(f"https://nama-uji.test:{port}", "s1", SECRET, client=http, alamat_tetap="127.0.0.1")
        assert k.ping() == {"ok": True}
        assert k.staging_tanda_air() == {"ok": True}
    assert srv.sni[:2] == ["nama-uji.test", "nama-uji.test"]
    assert srv.host_diterima == [f"nama-uji.test:{port}", f"nama-uji.test:{port}"]


def test_sertifikat_nama_lain_ditolak_walau_ip_benar(tmp_path):
    ca, daun, kunci = _sertifikat(tmp_path, "lain.test")
    with _server_tls(daun, kunci) as srv:
        port = srv.server_address[1]
        http = httpx.Client(verify=ssl.create_default_context(cafile=str(ca)))
        k = SiteClient(f"https://nama-uji.test:{port}", "s1", SECRET, client=http, alamat_tetap="127.0.0.1")
        with pytest.raises(SiteError):
            k.ping()
    assert srv.host_diterima == []


# ---- klien hosting lama ----------------------------------------------------------------


def _site_dan_hosting(ip=IP_LAMA):
    site = SimpleNamespace(url="https://www.toko.co.id", id=uuid.uuid4(), secret_terenkripsi=enkripsi_secret(SECRET))
    return site, SimpleNamespace(ip_lama=ip)


@pytest.fixture
def ipv4_vps(monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_HOSTING_IPV4", "169.58.91.181")
    get_settings.cache_clear()


def test_klien_lama_tanpa_metode_tulis(ipv4_vps):
    site, h = _site_dan_hosting()
    k = klien_lama(site, h)
    assert isinstance(k, KlienLamaBacaSaja)
    for nama in ("staging_unggah", "staging_terapkan", "staging_bersihkan", "staging_snapshot", "update",
                 "self_update", "inventory", "events", "traffic"):
        assert not hasattr(k, nama), nama
    publik = {m for m in dir(k) if not m.startswith("_")}
    assert publik == set(METODE_BACA) | {"alamat"}
    assert k.alamat == IP_LAMA


def test_klien_lama_dipatok_ke_ip_lama(ipv4_vps, monkeypatch):
    diminta = []

    def h(r):
        diminta.append(r)
        _cek_tanda_tangan(r)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(umum, "buat_http_lama", lambda: httpx.Client(transport=httpx.MockTransport(h)))
    site, hosting = _site_dan_hosting()
    klien_lama(site, hosting).ping()
    assert diminta[0].url.host == IP_LAMA
    assert diminta[0].headers["host"] == "www.toko.co.id"
    assert diminta[0].extensions["sni_hostname"] == "www.toko.co.id"


@pytest.mark.parametrize("ip", [None, "", "10.0.0.5", "127.0.0.1", "192.168.1.1", "169.58.91.181", "2a02::1",
                                "100.64.1.1", "203.0.113.5", "001.2.3.4",
                                # Multicast lolos `is_global` di Python 3.10 (review M4).
                                "224.0.0.251", "233.252.0.1", "239.255.255.250"])
def test_alamat_lama_tidak_sah_ditolak(ipv4_vps, ip):
    assert alamat_lama_sah(ip) is False
    site, h = _site_dan_hosting(ip)
    with pytest.raises(SiteError) as e:
        klien_lama(site, h)
    assert e.value.error_class == STAGING_DITOLAK


def test_alamat_lama_publik_diterima(ipv4_vps):
    assert alamat_lama_sah(IP_LAMA) is True


# ---- fix round 1 (review Task 5) ------------------------------------------------------


def _klien_lama_dengan(monkeypatch, handler, **opsi):
    monkeypatch.setattr(umum, "buat_http_lama",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler), **opsi))
    return klien_lama(*_site_dan_hosting())


def test_ping_lama_lewat_jalur_bertenggat(ipv4_vps, monkeypatch):
    # Putusan L9: ping ke hosting lama memakai minta_bertenggat (tenggat total,
    # batas byte, Connection: close), bukan _panggil Lapis 1.
    diminta = []

    def h(r):
        diminta.append(r)
        _cek_tanda_tangan(r)
        return httpx.Response(200, json={"ok": True})

    assert _klien_lama_dengan(monkeypatch, h).ping() == {"ok": True}
    r = diminta[0]
    assert (r.method, r.url.path) == ("GET", "/wp-json/wpmgr/v1/ping")
    assert r.headers["connection"] == "close"
    assert r.headers["accept-encoding"] == "identity"
    assert "trace" in r.extensions
    assert site_client.TENGGAT_PING_BERTENGGAT == 20.0
    assert site_client.BATAS_PING_BERTENGGAT == 64 * 1024


def test_ping_lama_yang_menetes_dihentikan_tenggat_total(ipv4_vps, monkeypatch):
    monkeypatch.setattr(site_client, "TENGGAT_PING_BERTENGGAT", 0.3)

    def menetes():
        for _ in range(200):
            time.sleep(0.02)
            yield b" "

    k = _klien_lama_dengan(monkeypatch, lambda r: httpx.Response(200, content=menetes()))
    mulai = time.monotonic()
    with pytest.raises(SiteError) as e:
        k.ping()
    assert time.monotonic() - mulai < 3
    assert e.value.error_class == TRANSIENT
    assert "tenggat" in e.value.pesan


def test_ping_lama_melebihi_batas_byte_ditolak(ipv4_vps, monkeypatch):
    besar = b'{"ok": true, "x": "' + b"W" * (64 * 1024) + b'"}'
    k = _klien_lama_dengan(monkeypatch, lambda r: httpx.Response(200, content=besar))
    with pytest.raises(SiteError) as e:
        k.ping()
    assert e.value.error_class == BAD_RESPONSE
    assert "batas" in e.value.pesan


def test_ping_lapis1_tidak_berubah():
    diminta = []

    def h(r):
        diminta.append(r)
        return httpx.Response(200, json={"ok": True})

    SiteClient("https://toko.co.id", "s1", SECRET, client=httpx.Client(transport=httpx.MockTransport(h))).ping()
    assert "connection" not in diminta[0].headers or diminta[0].headers["connection"] != "close"
    assert "trace" not in diminta[0].extensions


def test_klien_lama_menolak_rute_di_luar_daftar_tanpa_mengirim(ipv4_vps, monkeypatch):
    # Review M2: baca-saja struktural, bukan hanya karena pembungkusnya tidak
    # punya metode tulis.
    diminta = []

    def h(r):
        diminta.append(r)
        return httpx.Response(200, json={"ok": True})

    k = _klien_lama_dengan(monkeypatch, h)
    dalam = k._klien
    for panggil in (lambda: dalam.staging_terapkan({"tukar": "x"}), lambda: dalam.staging_unggah(b"x"),
                    lambda: dalam.staging_bersihkan("d1"), lambda: dalam.staging_snapshot(["a"]),
                    lambda: dalam.update("plugin", "akismet", "5.3"), lambda: dalam.inventory(),
                    lambda: dalam.events(None), lambda: dalam.traffic(), lambda: dalam.ping()):
        with pytest.raises(SiteError) as e:
            panggil()
        assert e.value.error_class == STAGING_DITOLAK
    assert diminta == []


def test_klien_lama_rute_baca_tetap_diizinkan(ipv4_vps, monkeypatch):
    from wpmgr.staging.paket import susun

    data = susun({"berkas": [{"path": "a"}]}, [b"isi"])
    diminta = []

    def h(r):
        diminta.append((r.method, r.url.path))
        if r.url.path.endswith(("/staging/file", "/staging/tabel")):
            return httpx.Response(200, content=data)
        return httpx.Response(200, json={"ok": True})

    k = _klien_lama_dengan(monkeypatch, h)
    k.ping()
    k.staging_manifest(None, batas=10)
    k.staging_file(["a"])
    k.staging_rentang("a", 0, 3)
    k.staging_tabel("wp_posts", None)
    k.staging_tanda_air()
    p = "/wp-json/wpmgr/v1"
    assert diminta == [("GET", f"{p}/ping"), ("GET", f"{p}/staging/manifest"), ("POST", f"{p}/staging/file"),
                       ("POST", f"{p}/staging/file"), ("POST", f"{p}/staging/tabel"),
                       ("GET", f"{p}/staging/tanda-air")]


def test_alamat_tetap_tidak_mengikuti_alihan(monkeypatch):
    # Review M3: walau klien yang disuntikkan mengikuti alihan, permintaan
    # yang dipatok IP tidak pernah dialihkan ke host lain.
    diminta = []

    def h(r):
        diminta.append(r)
        if r.url.host == "jahat.test":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(302, headers={"Location": "https://jahat.test/wp-json/wpmgr/v1/ping"})

    http = httpx.Client(transport=httpx.MockTransport(h), follow_redirects=True)
    k = SiteClient("https://www.toko.co.id", "s1", SECRET, client=http, alamat_tetap=IP_LAMA)
    for panggil in (k.ping, k.ping_bertenggat, k.staging_tanda_air):
        diminta.clear()
        with pytest.raises(SiteError):
            panggil()
        assert [r.url.host for r in diminta] == [IP_LAMA]


def test_kirim_dipatok_tetap_connection_close_dan_tanpa_kompresi():
    diminta = []

    def h(r):
        diminta.append(r)
        return httpx.Response(200, json={"ok": True})

    k = SiteClient("https://www.toko.co.id", "s1", SECRET, client=httpx.Client(transport=httpx.MockTransport(h)),
                   alamat_tetap=IP_LAMA)
    k.staging_tanda_air()
    k.staging_manifest(None, batas=10)
    for r in diminta:
        assert r.url.host == IP_LAMA and r.headers["host"] == "www.toko.co.id"
        assert r.headers["connection"] == "close"
        assert r.headers["accept-encoding"] == "identity"
        assert r.extensions["sni_hostname"] == "www.toko.co.id"


def test_sertifikat_nama_lain_ditolak_di_jalur_kirim(tmp_path):
    ca, daun, kunci = _sertifikat(tmp_path, "lain.test")
    with _server_tls(daun, kunci) as srv:
        port = srv.server_address[1]
        http = httpx.Client(verify=ssl.create_default_context(cafile=str(ca)))
        k = SiteClient(f"https://nama-uji.test:{port}", "s1", SECRET, client=http, alamat_tetap="127.0.0.1")
        for panggil in (k.staging_tanda_air, k.ping_bertenggat):
            with pytest.raises(SiteError):
                panggil()
    assert srv.host_diterima == []
