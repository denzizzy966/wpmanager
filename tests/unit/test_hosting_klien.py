import json
import ssl
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import httpx
import pytest

from wpmgr.crypto import enkripsi_secret
from wpmgr.errors import STAGING_DITOLAK, SiteError
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
                                "100.64.1.1", "203.0.113.5", "001.2.3.4"])
def test_alamat_lama_tidak_sah_ditolak(ipv4_vps, ip):
    assert alamat_lama_sah(ip) is False
    site, h = _site_dan_hosting(ip)
    with pytest.raises(SiteError) as e:
        klien_lama(site, h)
    assert e.value.error_class == STAGING_DITOLAK


def test_alamat_lama_publik_diterima(ipv4_vps):
    assert alamat_lama_sah(IP_LAMA) is True
