from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting.dns import (
    PESAN_LOLOS,
    PESAN_MENYEBAR,
    Jawaban,
    ada_www,
    backoff_mengizinkan,
    coba_lagi_pada,
    instruksi,
    ip_lama_dan_cdn,
    ip_lama_dari_dns,
    periksa_dns,
)

VPS = "169.58.91.181"
VPS6 = "2a02:c207:2347:2607::1"
LAMA = "93.184.216.34"
LAMA6 = "2a02:4780:6:1512:0:1e2d:4bc3:3"
R1, R2 = "1.1.1.1", "8.8.8.8"


@pytest.fixture
def setelan(monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_HOSTING_IPV4", VPS)
    monkeypatch.delenv("WPMGR_HOSTING_IPV6", raising=False)
    monkeypatch.setenv("WPMGR_HOSTING_RESOLVER", f"{R1},{R2}")
    get_settings.cache_clear()


@pytest.fixture
def ipv6_vps(setelan, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_HOSTING_IPV6", VPS6)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _tanpa_jaringan(monkeypatch):
    def terlarang():
        raise AssertionError("buat_penanya dipanggil: test harus memberi penanya palsu")

    monkeypatch.setattr(dns_mod, "buat_penanya", terlarang)


class PenanyaPalsu:
    """Jawaban per (resolver, nama, jenis); resolver None = semua resolver."""

    def __init__(self, jawaban: dict) -> None:
        self.jawaban = jawaban
        self.diminta: list[tuple] = []

    def tanya(self, resolver, nama, jenis, batas):
        self.diminta.append((resolver, nama, jenis, batas))
        if (resolver, nama, jenis) in self.jawaban:
            return self.jawaban[(resolver, nama, jenis)]
        return self.jawaban.get((None, nama, jenis), Jawaban())


def _h(www=True):
    return SimpleNamespace(domain="toko.co.id", dengan_www=www, dns_hasil=None,
                           sertifikat_gagal_kali=0, sertifikat_gagal_pada=None)


def _item(hasil, nama, jenis):
    return next(x for x in hasil.nama if x["nama"] == nama and x["jenis"] == jenis)


def _sudah_pindah(**lain):
    j = {(None, "toko.co.id", "A"): Jawaban((VPS,)), (None, "www.toko.co.id", "A"): Jawaban((VPS,))}
    j.update(lain)
    return PenanyaPalsu(j)


def test_dns_lolos_bila_a_cocok_aaaa_kosong_caa_kosong(setelan):
    p = _sudah_pindah()
    hasil = periksa_dns(_h(), penanya=p)
    assert hasil.ok is True and hasil.pesan == PESAN_LOLOS
    assert {(x["nama"], x["jenis"]) for x in hasil.nama} == {
        ("@", "A"), ("@", "AAAA"), ("www", "A"), ("www", "AAAA"), ("@", "CAA")}
    assert all(b <= dns_mod.TENGGAT_KUERI for *_, b in p.diminta)
    # Setiap resolver ditanya untuk setiap nama dan jenis.
    assert {(r, n, j) for r, n, j, _ in p.diminta if j in ("A", "AAAA")} == {
        (r, n, j) for r in (R1, R2) for n in ("toko.co.id", "www.toko.co.id") for j in ("A", "AAAA")}
    assert hasil.ke_json()["ok"] is True and hasil.ke_json()["dicek"]


def test_tanpa_www_hanya_apex_diperiksa(setelan):
    hasil = periksa_dns(_h(www=False), penanya=_sudah_pindah())
    assert {x["nama"] for x in hasil.nama} == {"@"}


def test_dns_a_ganda_belum_lolos(setelan):
    hasil = periksa_dns(_h(), penanya=PenanyaPalsu({
        (None, "toko.co.id", "A"): Jawaban((VPS, LAMA)), (None, "www.toko.co.id", "A"): Jawaban((VPS,))}))
    a = _item(hasil, "@", "A")
    assert (a["ok"], a["kode"], a["terlihat"], a["harus"]) == (False, "lebih", sorted([VPS, LAMA]), [VPS])
    assert hasil.ok is False


def test_dns_aaaa_lama_menahan_aktivasi(setelan):
    p = _sudah_pindah()
    p.jawaban[(None, "toko.co.id", "AAAA")] = Jawaban((LAMA6,))
    hasil = periksa_dns(_h(), penanya=p)
    aaaa = _item(hasil, "@", "AAAA")
    assert (aaaa["ok"], aaaa["kode"], aaaa["terlihat"], aaaa["harus"]) == (False, "hapus", [LAMA6], [])
    assert hasil.ok is False


def test_dns_aaaa_ke_ipv6_vps_diterima(ipv6_vps):
    p = _sudah_pindah()
    p.jawaban[(None, "toko.co.id", "AAAA")] = Jawaban((VPS6,))
    p.jawaban[(None, "www.toko.co.id", "AAAA")] = Jawaban((VPS6,))
    assert periksa_dns(_h(), penanya=p).ok is True
    p.jawaban[(None, "www.toko.co.id", "AAAA")] = Jawaban((LAMA6,))
    hasil = periksa_dns(_h(), penanya=p)
    assert _item(hasil, "www", "AAAA")["kode"] == "kurang" and hasil.ok is False


def test_dns_www_lewat_cname_ke_apex_lolos(setelan):
    p = _sudah_pindah()
    p.jawaban[(None, "www.toko.co.id", "A")] = Jawaban((VPS,), cname="toko.co.id")
    assert periksa_dns(_h(), penanya=p).ok is True


def test_dns_cname_cdn_hostinger_ditandai(setelan):
    p = _sudah_pindah()
    p.jawaban[(None, "www.toko.co.id", "A")] = Jawaban(("185.10.10.10",), cname="toko.co.id.cdn.hstgr.net")
    hasil = periksa_dns(_h(), penanya=p)
    assert _item(hasil, "www", "A")["kode"] == "cname" and hasil.ok is False


def test_dns_resolver_berbeda_belum_lolos(setelan):
    p = _sudah_pindah()
    p.jawaban[(R2, "toko.co.id", "A")] = Jawaban((LAMA,))
    hasil = periksa_dns(_h(), penanya=p)
    a = _item(hasil, "@", "A")
    assert (a["ok"], a["kode"], a["terlihat"]) == (False, "beda_resolver", sorted([VPS, LAMA]))
    assert hasil.pesan == PESAN_MENYEBAR


def test_dns_resolver_tidak_menjawab_belum_lolos(setelan):
    p = _sudah_pindah()
    p.jawaban[(R1, "www.toko.co.id", "AAAA")] = None
    hasil = periksa_dns(_h(), penanya=p)
    assert _item(hasil, "www", "AAAA")["kode"] == "beda_resolver" and hasil.ok is False


@pytest.mark.parametrize("caa,ok", [
    ((), True), (("issue letsencrypt.org",), True), (("issuewild letsencrypt.org",), False),
    (("issue sectigo.com",), False), (("iodef mailto:a@b.id",), False),
])
def test_dns_caa(setelan, caa, ok):
    p = _sudah_pindah()
    p.jawaban[(R1, "toko.co.id", "CAA")] = Jawaban(caa)
    hasil = periksa_dns(_h(), penanya=p)
    c = _item(hasil, "@", "CAA")
    assert c["ok"] is ok and hasil.ok is ok
    assert c["kode"] == ("cocok" if ok else "caa")


def test_dns_caa_dari_induk_zona(setelan):
    p = _sudah_pindah()
    p.jawaban[(R1, "co.id", "CAA")] = Jawaban(("issue sectigo.com",))
    assert _item(periksa_dns(_h(), penanya=p), "@", "CAA")["kode"] == "caa"


def test_dns_tenggat_total_tidak_terlewati(setelan, monkeypatch):
    jam = [0.0]
    monkeypatch.setattr(dns_mod, "_jam", lambda: jam[0])

    class Lambat:
        n = 0

        def tanya(self, resolver, nama, jenis, batas):
            assert batas <= dns_mod.TENGGAT_KUERI
            self.n += 1
            jam[0] += 3.0
            return Jawaban((VPS,)) if jenis == "A" else Jawaban()

    p = Lambat()
    hasil = periksa_dns(_h(), penanya=p)
    assert p.n == 4
    assert hasil.ok is False


def test_penanya_yang_melempar_dianggap_tidak_menjawab(setelan):
    class Rusak:
        def tanya(self, *argumen):
            raise RuntimeError("galat tak terduga")

    hasil = periksa_dns(_h(), penanya=Rusak())
    assert hasil.ok is False and _item(hasil, "@", "A")["kode"] == "beda_resolver"


# ---- IP lama dan www ---------------------------------------------------------------


def test_ip_lama_dari_dns(setelan):
    assert ip_lama_dari_dns("toko.co.id", penanya=PenanyaPalsu({(None, "toko.co.id", "A"): Jawaban((LAMA,))})) == LAMA
    # Domain sudah menunjuk VPS: ip_lama tidak bisa ditentukan (spec §16).
    assert ip_lama_dari_dns("toko.co.id", penanya=PenanyaPalsu({
        (None, "toko.co.id", "A"): Jawaban((LAMA, VPS))})) is None
    assert ip_lama_dari_dns("toko.co.id", penanya=PenanyaPalsu({
        (None, "toko.co.id", "A"): Jawaban(("10.0.0.5",))})) is None
    assert ip_lama_dari_dns("toko.co.id", penanya=PenanyaPalsu({})) is None
    # Resolver pertama tidak menjawab: resolver berikutnya dipakai.
    assert ip_lama_dari_dns("toko.co.id", penanya=PenanyaPalsu({
        (R1, "toko.co.id", "A"): None, (R2, "toko.co.id", "A"): Jawaban((LAMA,))})) == LAMA


def test_ada_www(setelan):
    assert ada_www("toko.co.id", penanya=PenanyaPalsu({(None, "www.toko.co.id", "A"): Jawaban((LAMA,))})) is True
    assert ada_www("toko.co.id", penanya=PenanyaPalsu({})) is False


# ---- instruksi record ----------------------------------------------------------------


def test_instruksi_hapus_aaaa_lama_dan_ubah_a(setelan):
    p = _sudah_pindah()
    p.jawaban[(None, "toko.co.id", "A")] = Jawaban((LAMA,))
    p.jawaban[(None, "toko.co.id", "AAAA")] = Jawaban((LAMA6,))
    h = _h()
    h.dns_hasil = periksa_dns(h, penanya=p).ke_json()
    daftar = instruksi(h, VPS, None)
    assert {"jenis": "A", "nama": "@", "aksi": "ubah", "nilai": VPS, "ok": False, "cname": False} in daftar
    assert {"jenis": "A", "nama": "www", "aksi": "ubah", "nilai": VPS, "ok": True, "cname": False} in daftar
    assert {"jenis": "AAAA", "nama": "@", "aksi": "hapus", "nilai": LAMA6, "ok": False} in daftar
    assert {"jenis": "AAAA", "nama": "www", "aksi": "hapus", "nilai": "", "ok": True} in daftar


def test_instruksi_ipv6_vps_dan_caa(ipv6_vps):
    p = _sudah_pindah()
    p.jawaban[(R1, "toko.co.id", "CAA")] = Jawaban(("issue sectigo.com",))
    h = _h(www=False)
    h.dns_hasil = periksa_dns(h, penanya=p).ke_json()
    daftar = instruksi(h, VPS, VPS6)
    assert {"jenis": "AAAA", "nama": "@", "aksi": "ubah", "nilai": VPS6, "ok": True} in daftar
    assert {"jenis": "CAA", "nama": "@", "aksi": "ubah", "nilai": dns_mod.NILAI_CAA, "ok": False} in daftar
    assert not any(x["nama"] == "www" for x in daftar)


def test_instruksi_tanpa_cek_dan_nilai_rusak_dibuang(setelan):
    h = _h()
    daftar = instruksi(h, VPS, None)
    assert [(x["jenis"], x["nama"], x["aksi"]) for x in daftar] == [
        ("A", "@", "ubah"), ("AAAA", "@", "hapus"), ("A", "www", "ubah"), ("AAAA", "www", "hapus")]
    h.dns_hasil = {"nama": [{"nama": "@", "jenis": "AAAA", "terlihat": ["<script>", LAMA6], "ok": False,
                             "kode": "hapus"}, "rusak"]}
    nilai = [x["nilai"] for x in instruksi(h, VPS, None) if x["jenis"] == "AAAA" and x["nama"] == "@"]
    assert nilai == [LAMA6]


# ---- backoff sertifikat -------------------------------------------------------------------

T0 = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("kali,jam", [(1, 1), (2, 2), (3, 4), (4, 6), (5, 6), (9, 6)])
def test_coba_lagi_pada(kali, jam):
    h = SimpleNamespace(sertifikat_gagal_kali=kali, sertifikat_gagal_pada=T0)
    assert coba_lagi_pada(h) == T0 + timedelta(hours=jam)


def test_backoff_mengizinkan():
    belum = SimpleNamespace(sertifikat_gagal_kali=0, sertifikat_gagal_pada=None)
    assert coba_lagi_pada(belum) is None and backoff_mengizinkan(belum, T0, manual=False)
    h = SimpleNamespace(sertifikat_gagal_kali=3, sertifikat_gagal_pada=T0)
    assert backoff_mengizinkan(h, T0 + timedelta(hours=3), manual=False) is False
    assert backoff_mengizinkan(h, T0 + timedelta(hours=4), manual=False) is True
    # Tombol manual mengabaikan backoff hanya sesudah >= 15 menit (batas Let's Encrypt).
    assert backoff_mengizinkan(h, T0 + timedelta(minutes=10), manual=True) is False
    assert backoff_mengizinkan(h, T0 + timedelta(minutes=15), manual=True) is True


# ---- fakta lingkungan nyata (spec §8.2, §22) ---------------------------------------------


def test_dns_cname_hstgr_ditandai_dan_pesan_matikan_cdn(setelan):
    p = _sudah_pindah()
    # CDN Hostinger: A apex juga IP CDN; www CNAME ke *.cdn.hstgr.net.
    p.jawaban[(None, "www.toko.co.id", "A")] = Jawaban(("185.10.10.10",), cname="x.cdn.hstgr.net")
    hasil = periksa_dns(_h(), penanya=p)
    assert _item(hasil, "www", "A")["kode"] == "cname"
    assert hasil.pesan == dns_mod.PESAN_CDN and "hPanel" in hasil.pesan


def test_dns_www_cname_ke_apex_di_hosting_lama_belum_lolos(setelan):
    p = _sudah_pindah()
    p.jawaban[(None, "toko.co.id", "A")] = Jawaban((LAMA,))
    p.jawaban[(None, "www.toko.co.id", "A")] = Jawaban((LAMA,), cname="toko.co.id")
    hasil = periksa_dns(_h(), penanya=p)
    assert _item(hasil, "www", "A")["kode"] == "kurang" and hasil.ok is False


def test_nilai_dns_tidak_sah_dibuang():
    class Rd:
        def __init__(self, address):
            self.address = address

    assert dns_mod._nilai_rdata("A", Rd("1.2.3.4")) == "1.2.3.4"
    assert dns_mod._nilai_rdata("A", Rd("<x>")) is None
    assert dns_mod._nilai_rdata("AAAA", Rd("zzz")) is None


# ---- putaran perbaikan 1 --------------------------------------------------------------------


def test_ada_www_tiga_keadaan(setelan):
    semua_mati = PenanyaPalsu({(R1, "www.toko.co.id", "A"): None, (R2, "www.toko.co.id", "A"): None})
    assert ada_www("toko.co.id", penanya=semua_mati) is None
    assert ada_www("toko.co.id", penanya=PenanyaPalsu({})) is False  # NXDOMAIN di semua resolver
    assert ada_www("toko.co.id", penanya=PenanyaPalsu({
        (R1, "www.toko.co.id", "A"): None, (R2, "www.toko.co.id", "A"): Jawaban((LAMA,))})) is True
    assert ada_www("toko.co.id", penanya=PenanyaPalsu({(R1, "www.toko.co.id", "A"): None})) is False


@pytest.mark.parametrize("caa,ok", [
    (Jawaban((), ada=1), False),  # `0 issue ""`: tak terbaca tetap ada, melarang semua CA
    (Jawaban(("issue letsencrypt.org; validationmethods=dns-01",), ada=1), False),
    (Jawaban(("issue letsencrypt.org; validationmethods=dns-01,http-01",), ada=1), True),
    (Jawaban(("issue letsencrypt.org; accounturi=https://x",), ada=1), True),
    (Jawaban(("issue notletsencrypt.org",), ada=1), False),
    (Jawaban(("issue letsencrypt.org.evil.com",), ada=1), False),
    (Jawaban(("issue evil.com; x=letsencrypt.org",), ada=1), False),
    (Jawaban(("issuewild letsencrypt.org",), ada=1), False),
    (Jawaban(("issue sectigo.com", "issue letsencrypt.org"), ada=2), True),
    (Jawaban(("issue letsencrypt.org",), ada=3), True),
])
def test_dns_caa_gagal_tertutup(setelan, caa, ok):
    p = _sudah_pindah()
    p.jawaban[(R1, "toko.co.id", "CAA")] = caa
    hasil = periksa_dns(_h(), penanya=p)
    assert _item(hasil, "@", "CAA")["ok"] is ok and hasil.ok is ok


def test_dns_caa_www_sendiri_diperiksa(setelan):
    p = _sudah_pindah()
    p.jawaban[(R1, "www.toko.co.id", "CAA")] = Jawaban(("issue sectigo.com",), ada=1)
    assert _item(periksa_dns(_h(), penanya=p), "@", "CAA")["kode"] == "caa"
    assert _item(periksa_dns(_h(www=False), penanya=p), "@", "CAA")["kode"] == "cocok"


def test_ip_lama_lewat_cdn_hostinger_none(setelan):
    # Bentuk rizkycahayaraya.com: A apex = IP CDN, www CNAME ke *.cdn.hstgr.net.
    p = PenanyaPalsu({(None, "toko.co.id", "A"): Jawaban(("84.32.84.10",)),
                      (None, "www.toko.co.id", "A"): Jawaban(("84.32.84.10",), cname="toko.co.id.cdn.hstgr.net")})
    assert ip_lama_dari_dns("toko.co.id", penanya=p) is None
    assert ip_lama_dan_cdn("toko.co.id", penanya=p) == (None, True)
    assert "hPanel" in dns_mod.PESAN_CDN_IP_LAMA
    p2 = PenanyaPalsu({(None, "toko.co.id", "A"): Jawaban(("84.32.84.10",), cname="a.hstgr.net")})
    assert ip_lama_dan_cdn("toko.co.id", penanya=p2) == (None, True)
    biasa = PenanyaPalsu({(None, "toko.co.id", "A"): Jawaban((LAMA,))})
    assert ip_lama_dan_cdn("toko.co.id", penanya=biasa) == (LAMA, False)


def test_cname_hstgr_didahulukan_dari_beda_resolver(setelan):
    p = _sudah_pindah()
    p.jawaban[(R1, "www.toko.co.id", "A")] = Jawaban(("185.10.10.10",), cname="x.cdn.hstgr.net")
    p.jawaban[(R2, "www.toko.co.id", "A")] = Jawaban((VPS,))
    hasil = periksa_dns(_h(), penanya=p)
    assert _item(hasil, "www", "A")["kode"] == "cname" and hasil.pesan == dns_mod.PESAN_CDN


def test_batas_titik_hstgr():
    assert dns_mod._hstgr("evilhstgr.net") is False
    assert dns_mod._hstgr("hstgr.net") and dns_mod._hstgr("a.cdn.hstgr.net")
    assert not dns_mod._hstgr(None)


def test_instruksi_www_cname_ke_apex_ikut_apex(setelan):
    p = _sudah_pindah()
    p.jawaban[(None, "toko.co.id", "A")] = Jawaban((LAMA,))
    p.jawaban[(None, "www.toko.co.id", "A")] = Jawaban((LAMA,), cname="toko.co.id")
    h = _h()
    h.dns_hasil = periksa_dns(h, penanya=p).ke_json()
    www = next(x for x in instruksi(h, VPS, None) if x["jenis"] == "A" and x["nama"] == "www")
    assert www["aksi"] == "ikut_apex" and www["ikut_apex"] is True


@pytest.mark.parametrize("rusak", ["teks", 5, ["a"], {"nama": "x"}, {"nama": 7}])
def test_instruksi_dns_hasil_bukan_dict(setelan, rusak):
    h = _h()
    h.dns_hasil = rusak
    assert len(instruksi(h, VPS, None)) == 4


def test_nilai_dibatasi_terurut(setelan):
    ips = [f"10.0.0.{i}" for i in range(20, 0, -1)]
    p = PenanyaPalsu({(None, "toko.co.id", "A"): Jawaban(tuple(ips))})
    a = _item(periksa_dns(_h(www=False), penanya=p), "@", "A")
    assert a["terlihat"] == sorted(ips)[: dns_mod.MAKS_NILAI]


# ---- PenanyaDns dengan dnspython palsu -------------------------------------------------------


class _Rd:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Jawab:
    def __init__(self, rrset, kanonik="toko.co.id."):
        self.rrset = rrset
        self.canonical_name = kanonik


def _pasang_resolver(monkeypatch, hasil):
    class R:
        def __init__(self, configure=True):
            assert configure is False

        def resolve(self, nama, jenis, **opsi):
            assert opsi == {"raise_on_no_answer": False, "search": False}
            assert self.lifetime == self.timeout == 2.5 and self.nameservers == [R1]
            if isinstance(hasil, Exception):
                raise hasil
            return hasil

    monkeypatch.setattr(dns_mod.dns.resolver, "Resolver", R)


def test_penanya_nxdomain_dan_galat(monkeypatch):
    _pasang_resolver(monkeypatch, dns_mod.dns.resolver.NXDOMAIN())
    assert dns_mod.PenanyaDns().tanya(R1, "toko.co.id", "A", 2.5) == Jawaban()
    _pasang_resolver(monkeypatch, dns_mod.dns.exception.Timeout())
    assert dns_mod.PenanyaDns().tanya(R1, "toko.co.id", "A", 2.5) is None


def test_penanya_kanonik_dan_batas_nilai(monkeypatch):
    rr = [_Rd(address=f"10.0.0.{i}") for i in range(30, 0, -1)] + [_Rd(address="bukan-ip")]
    _pasang_resolver(monkeypatch, _Jawab(rr, "www.toko.co.id.cdn.hstgr.net."))
    j = dns_mod.PenanyaDns().tanya(R1, "www.toko.co.id", "A", 2.5)
    assert j.cname == "www.toko.co.id.cdn.hstgr.net"
    assert len(j.nilai) == dns_mod.MAKS_NILAI and list(j.nilai) == sorted(j.nilai)
    _pasang_resolver(monkeypatch, _Jawab([_Rd(address="1.2.3.4")], "toko.co.id."))
    assert dns_mod.PenanyaDns().tanya(R1, "toko.co.id", "A", 2.5) == Jawaban(("1.2.3.4",))


def test_penanya_caa_tak_terbaca_tetap_terhitung(monkeypatch):
    rr = [_Rd(tag=b"issue", value=b""), _Rd(tag=b"issue", value="é".encode()), _Rd(tag=b"issue", value=b"a" * 300)]
    _pasang_resolver(monkeypatch, _Jawab(rr))
    j = dns_mod.PenanyaDns().tanya(R1, "toko.co.id", "CAA", 2.5)
    assert j.nilai == () and j.ada == 3 and j.terisi
