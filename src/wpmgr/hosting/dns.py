"""Pemeriksaan DNS sebelum aktivasi hosting VPS (spec Lapis 4 §8).

Resolver publik tertentu (WPMGR_HOSTING_RESOLVER) ditanya langsung lewat
dnspython: socket.getaddrinfo memakai resolver lokal, /etc/hosts, dan cache,
dan tidak bisa menanyakan resolver tertentu maupun CAA. Nilai dari DNS adalah
masukan luar: hanya alamat yang lolos `ipaddress` yang disimpan, paling banyak
MAKS_NILAI per jenis, dan UI menyusun teks dari kode tetap.
"""

import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import dns.exception
import dns.resolver

from wpmgr.config import get_settings
from wpmgr.hosting.umum import alamat_lama_sah

log = logging.getLogger("wpmgr.hosting.dns")

TENGGAT_KUERI = 3.0
TENGGAT_TOTAL = 10.0
MAKS_NILAI = 8
JEDA_SERTIFIKAT_MAKS = timedelta(hours=6)
JEDA_MANUAL = timedelta(minutes=15)
POLA_CAA = re.compile(r"[a-z0-9]{1,15} [\x21-\x7e][\x20-\x7e]{0,254}")
CAA_LETSENCRYPT = "letsencrypt.org"
NILAI_CAA = '0 issue "letsencrypt.org"'
PESAN_LOLOS = "DNS sudah menunjuk VPS."
PESAN_MENYEBAR = "DNS sedang menyebar"
PESAN_CDN = ("Domain memakai CDN Hostinger: matikan CDN di hPanel, lalu ganti CNAME www "
            "(...hstgr.net) dengan record A ke VPS.")
PESAN_CDN_IP_LAMA = ("Domain masih lewat CDN Hostinger sehingga IP hosting lama tidak terlihat: matikan CDN "
                     "di hPanel, tunggu DNS menyebar, lalu coba lagi.")
PESAN_BELUM = "DNS belum menunjuk VPS; ubah record sesuai tabel."


def _jam() -> float:
    return time.monotonic()


@dataclass(frozen=True)
class Jawaban:
    nilai: tuple[str, ...] = ()
    # Nama kanonik bila jawaban datang lewat CNAME ke nama lain.
    cname: str | None = None
    # Jumlah record mentah di RRset (CAA): nilai yang tak terbaca tetap terhitung
    # supaya CAA yang aneh dianggap ada dan tidak mengizinkan (gagal tertutup).
    ada: int = 0

    @property
    def terisi(self) -> bool:
        return bool(self.nilai) or self.ada > 0


def _hstgr(nama: str | None) -> bool:
    return bool(nama) and (nama == "hstgr.net" or nama.endswith(".hstgr.net"))


def _nilai_rdata(jenis: str, rd) -> str | None:
    try:
        if jenis == "A":
            return str(ipaddress.IPv4Address(rd.address))
        if jenis == "AAAA":
            return ipaddress.IPv6Address(rd.address).compressed
        if jenis == "CAA":
            teks = f"{rd.tag.decode('ascii').lower()} {rd.value.decode('ascii').lower()}"
            return teks if POLA_CAA.fullmatch(teks) else None
    except (ValueError, AttributeError, UnicodeDecodeError):
        return None
    return None


class PenanyaDns:
    """Satu kueri ke satu resolver publik tertentu, dengan batas waktu sendiri."""

    def tanya(self, resolver: str, nama: str, jenis: str, batas: float) -> Jawaban | None:
        r = dns.resolver.Resolver(configure=False)
        r.nameservers = [resolver]
        r.timeout = batas
        r.lifetime = batas
        try:
            jawab = r.resolve(nama, jenis, raise_on_no_answer=False, search=False)
        except dns.resolver.NXDOMAIN:
            return Jawaban()
        except dns.exception.DNSException:
            return None
        nilai: set[str] = set()
        ada = 0
        for rd in jawab.rrset or ():
            ada += 1
            teks = _nilai_rdata(jenis, rd)
            if teks is not None:
                nilai.add(teks)
            if ada >= 4 * MAKS_NILAI:
                break
        # Diurutkan sebelum dipotong supaya subset yang tampil tetap.
        kanonik = str(jawab.canonical_name).rstrip(".").lower()
        return Jawaban(tuple(sorted(nilai))[:MAKS_NILAI], kanonik if kanonik != nama.lower() else None,
                       ada if jenis == "CAA" else 0)


def buat_penanya() -> PenanyaDns:
    return PenanyaDns()


@dataclass
class HasilDns:
    ok: bool
    dicek: str
    nama: list[dict] = field(default_factory=list)

    @property
    def pesan(self) -> str:
        if self.ok:
            return PESAN_LOLOS
        if any(x["kode"] == "beda_resolver" for x in self.nama):
            return PESAN_MENYEBAR
        if any(x["kode"] == "cname" for x in self.nama):
            return PESAN_CDN
        return PESAN_BELUM

    def ke_json(self) -> dict:
        return {"ok": self.ok, "dicek": self.dicek, "nama": self.nama}


def _penanya_bertenggat(penanya):
    akhir = _jam() + TENGGAT_TOTAL

    def tanya(resolver: str, nama: str, jenis: str) -> Jawaban | None:
        sisa = akhir - _jam()
        if sisa <= 0:
            return None
        try:
            return penanya.tanya(resolver, nama, jenis, min(TENGGAT_KUERI, sisa))
        except Exception:
            log.warning("Kueri DNS %s %s ke %s gagal", jenis, nama, resolver, exc_info=True)
            return None

    return tanya


def _nilai_item(label: str, jenis: str, fqdn: str, domain: str, jawaban: list, ipv4: str,
                ipv6: str | None) -> dict:
    harus = [ipv4] if jenis == "A" else ([ipv6] if ipv6 else [])
    terlihat = sorted({v for j in jawaban if j is not None for v in j.nilai})[:MAKS_NILAI]
    dasar = {"nama": label, "jenis": jenis, "terlihat": terlihat, "harus": harus}
    cdn = jenis == "A" and any(j is not None and _hstgr(j.cname) for j in jawaban)
    if cdn:
        # CNAME CDN didahulukan: panduannya sama berapa pun resolver yang sudah berubah.
        return {**dasar, "ok": False, "kode": "cname"}
    # Resolver yang tidak menjawab atau berbeda pendapat = DNS belum stabil.
    if any(j is None for j in jawaban) or len({frozenset(j.nilai) for j in jawaban}) > 1:
        return {**dasar, "ok": False, "kode": "beda_resolver"}
    nilai = set(jawaban[0].nilai)
    if jenis == "A":
        if nilai == {ipv4}:
            kode = "cocok"
        elif ipv4 in nilai:
            kode = "lebih"
        elif any(j.cname and j.cname not in (fqdn, domain) for j in jawaban):
            # A12: www lewat CNAME ke CDN hosting lama; CNAME harus diganti A.
            kode = "cname"
        else:
            kode = "kurang"
    elif not nilai or (ipv6 and nilai == {ipv6}):
        kode = "cocok"
    elif ipv6:
        kode = "lebih" if ipv6 in nilai else "kurang"
    else:
        # §8.2: AAAA lama wajib dihapus bila VPS tanpa IPv6 di setelan.
        kode = "hapus"
    hasil = {**dasar, "ok": kode == "cocok", "kode": kode}
    if jenis == "A" and label == "www" and all(j.cname == domain for j in jawaban):
        hasil["cname_apex"] = True
    return hasil


def _caa_mengizinkan(nilai: tuple[str, ...]) -> bool:
    """Hanya `issue` untuk letsencrypt.org persis (domain sebelum `;`); `issuewild` tidak
    berlaku untuk apex/www, dan daftar validationmethods tanpa http-01 tidak mengizinkan."""
    for v in nilai:
        tag, _, isi = v.partition(" ")
        if tag != "issue":
            continue
        penerbit, *param = (bagian.strip() for bagian in isi.split(";"))
        if penerbit != CAA_LETSENCRYPT:
            continue
        metode = [p.partition("=")[2].split(",") for p in param if p.startswith("validationmethods=")]
        if all("http-01" in [m.strip() for m in daftar] for daftar in metode):
            return True
    return False


def _periksa_caa(resolver: str, domain: str, tanya, dengan_www: bool = False) -> dict:
    """CAA domain, lalu induknya sampai zona; berhenti di jawaban pertama (spec §8.1).

    RRset yang ada tetapi tak terbaca (`0 issue ""`, terlalu panjang, bukan ASCII)
    dianggap ada dan tidak mengizinkan. `www` yang punya CAA sendiri juga diperiksa.
    """
    dasar = {"nama": "@", "jenis": "CAA", "terlihat": [], "harus": []}
    www = f"www.{domain}"
    label = domain.split(".")
    awal = ([www] if dengan_www else []) + [".".join(label[i:]) for i in range(len(label) - 1)]
    ditemukan = []
    for nama in awal:
        j = tanya(resolver, nama, "CAA")
        if j is None:
            return {**dasar, "ok": False, "kode": "beda_resolver"}
        if j.terisi:
            ditemukan.append(j)
            if nama != www:
                break
    ok = all(_caa_mengizinkan(j.nilai) for j in ditemukan)
    return {**dasar, "ok": ok, "kode": "cocok" if ok else "caa"}


def periksa_dns(hosting, resolver=None, penanya=None, sekarang: datetime | None = None) -> HasilDns:
    """Lolos hanya bila SETIAP resolver dan setiap nama: A = {IPv4 VPS}, AAAA kosong atau
    {IPv6 VPS}, dan CAA kosong atau mengizinkan Let's Encrypt (spec §8.1)."""
    s = get_settings()
    tanya = _penanya_bertenggat(penanya or buat_penanya())
    daftar = list(resolver or s.daftar_resolver)
    sekarang = sekarang or datetime.now(timezone.utc)
    nama = [("@", hosting.domain)] + ([("www", f"www.{hosting.domain}")] if hosting.dengan_www else [])
    item = []
    for label, fqdn in nama:
        for jenis in ("A", "AAAA"):
            jawaban = [tanya(r, fqdn, jenis) for r in daftar]
            item.append(_nilai_item(label, jenis, fqdn, hosting.domain, jawaban, s.hosting_ipv4, s.hosting_ipv6))
    item.append(_periksa_caa(daftar[0], hosting.domain, tanya, hosting.dengan_www))
    return HasilDns(ok=all(x["ok"] for x in item), dicek=sekarang.isoformat(), nama=item)


def ip_lama_dan_cdn(domain: str, penanya=None, resolver=None) -> tuple[str | None, bool]:
    """(ip_lama, lewat_cdn). `lewat_cdn` True bila apex atau www lewat CNAME `hstgr.net`:
    A apex-nya IP CDN, bukan IP hosting lama, jadi ip_lama None (pakai PESAN_CDN_IP_LAMA)."""
    s = get_settings()
    tanya = _penanya_bertenggat(penanya or buat_penanya())
    for r in resolver or s.daftar_resolver:
        j = tanya(r, domain, "A")
        if j is None:
            continue
        w = tanya(r, f"www.{domain}", "A")
        if _hstgr(j.cname) or (w is not None and _hstgr(w.cname)):
            return None, True
        if not j.nilai:
            continue
        if s.hosting_ipv4 in j.nilai:
            return None, False
        return next((ip for ip in sorted(j.nilai) if alamat_lama_sah(ip)), None), False
    return None, False


def ip_lama_dari_dns(domain: str, penanya=None, resolver=None) -> str | None:
    """IPv4 hosting lama dari A domain (spec §6 langkah 2).

    None bila tidak ada resolver yang menjawab, tidak ada IPv4 publik, domain sudah
    (sebagian) menunjuk VPS, atau domain lewat CDN Hostinger (lihat `ip_lama_dan_cdn`).
    """
    return ip_lama_dan_cdn(domain, penanya, resolver)[0]


def ada_www(domain: str, penanya=None, resolver=None) -> bool | None:
    """`www.<domain>` punya A (langsung atau lewat CNAME) di salah satu resolver.

    True = ada; False = setidaknya satu resolver menjawab dan tidak ada; None = tidak
    ada resolver yang menjawab (DNS gagal). Pemanggil WAJIB menolak dengan pesan tetap
    pada None: menganggapnya "tanpa www" bisa mengaktifkan sementara pengunjung www
    masih ke hosting lama.
    """
    tanya = _penanya_bertenggat(penanya or buat_penanya())
    menjawab = False
    for r in resolver or get_settings().daftar_resolver:
        j = tanya(r, f"www.{domain}", "A")
        if j is None:
            continue
        menjawab = True
        if j.nilai:
            return True
    return False if menjawab else None


def _ipv6_sah(nilai) -> bool:
    try:
        return isinstance(nilai, str) and ipaddress.IPv6Address(nilai).compressed == nilai
    except ValueError:
        return False


def instruksi(hosting, ipv4: str, ipv6: str | None) -> list[dict]:
    """Record yang harus ada di DNS domain (spec §6 langkah 5, §8.2), ditandai dari cek terakhir.

    `dns_hasil` ditulis dashboard sendiri, tetapi tetap dibaca defensif: nilai
    yang tampil hanya alamat IPv6 yang lolos `ipaddress`.
    """
    hasil = {}
    mentah = hosting.dns_hasil if isinstance(hosting.dns_hasil, dict) else {}
    for x in (mentah.get("nama") if isinstance(mentah.get("nama"), list) else []):
        if isinstance(x, dict):
            hasil[(x.get("nama"), x.get("jenis"))] = x
    daftar = []
    for label in ["@"] + (["www"] if hosting.dengan_www else []):
        a = hasil.get((label, "A")) or {}
        if label == "www" and a.get("cname_apex") is True:
            # www CNAME ke apex: ikut apex, tidak perlu perubahan terpisah.
            daftar.append({"jenis": "A", "nama": label, "aksi": "ikut_apex", "nilai": ipv4,
                           "ok": a.get("ok") is True, "cname": False, "ikut_apex": True})
        else:
            daftar.append({"jenis": "A", "nama": label, "aksi": "ubah", "nilai": ipv4, "ok": a.get("ok") is True,
                           "cname": a.get("kode") == "cname"})
        aaaa = hasil.get((label, "AAAA")) or {}
        if label == "www" and (a.get("cname_apex") is True or a.get("kode") == "cname"):
            # www masih CNAME: AAAA-nya tidak terpisah dari record A (ikut apex) atau baru
            # bermakna sesudah CNAME diganti A; nilai tidak ditampilkan supaya tidak menyesatkan.
            aksi = "ikut_apex" if a.get("cname_apex") is True else "setelah_cname"
            daftar.append({"jenis": "AAAA", "nama": label, "aksi": aksi, "nilai": "",
                           "ok": aaaa.get("ok") is True})
            continue
        if ipv6:
            daftar.append({"jenis": "AAAA", "nama": label, "aksi": "ubah", "nilai": ipv6, "ok": aaaa.get("ok") is True})
            continue
        terlihat = [v for v in (aaaa.get("terlihat") or []) if _ipv6_sah(v)]
        if terlihat and aaaa.get("ok") is not True:
            daftar.extend({"jenis": "AAAA", "nama": label, "aksi": "hapus", "nilai": v, "ok": False} for v in terlihat)
        else:
            daftar.append({"jenis": "AAAA", "nama": label, "aksi": "hapus", "nilai": "", "ok": aaaa.get("ok") is True})
    caa = hasil.get(("@", "CAA")) or {}
    if caa.get("kode") == "caa":
        daftar.append({"jenis": "CAA", "nama": "@", "aksi": "ubah", "nilai": NILAI_CAA, "ok": False})
    return daftar


# ---- backoff sertifikat (spec §8.4) ------------------------------------------------


def jeda_bertingkat(kali: int) -> timedelta:
    """min(2^(k-1) jam, 6 jam) sesudah gagal ke-k (k >= 1): 1 -> 2 -> 4 -> 6 jam (spec §8.4)."""
    return min(timedelta(hours=2 ** min(max(kali, 1) - 1, 10)), JEDA_SERTIFIKAT_MAKS)


def coba_lagi_pada(hosting) -> datetime | None:
    """Batas Let's Encrypt (5 validasi gagal per hostname per jam): sesudah gagal
    ke-k, pengantrean otomatis berikutnya tidak sebelum gagal_pada + min(2^(k-1) jam, 6 jam)."""
    kali = hosting.sertifikat_gagal_kali or 0
    if kali <= 0 or hosting.sertifikat_gagal_pada is None:
        return None
    return hosting.sertifikat_gagal_pada + jeda_bertingkat(kali)


def backoff_mengizinkan(hosting, sekarang: datetime, manual: bool) -> bool:
    batas = coba_lagi_pada(hosting)
    if batas is None or sekarang >= batas:
        return True
    return manual and sekarang >= hosting.sertifikat_gagal_pada + JEDA_MANUAL
