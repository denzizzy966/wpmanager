"""Google Analytics 4 (sumber traffic kedua) dan deteksi anomali traffic.

GA4 disimpan di tabel yang sama dengan traffic plugin, dengan sumber 'ga4'.
Keduanya tidak pernah dijumlah: angkanya memang tidak akan sama.
"""

import logging
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wpmgr.jobs.monitoring import simpan_traffic
from wpmgr.models import Site, SiteStatus, TrafficHarian, TrafficRincian

log = logging.getLogger("wpmgr.traffic")

URL_RUN_REPORT = "https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"
CAKUPAN = "https://www.googleapis.com/auth/analytics.readonly"
# [0-9] dan \Z, bukan \d dan $: \d juga cocok dengan digit non-ASCII, dan $
# cocok sebelum baris baru di akhir -- keduanya lolos ke URL runReport.
POLA_PROPERTY = re.compile(r"^[0-9]{6,12}\Z")
HALAMAN_TERATAS_GA4 = 50
MIN_MEDIAN_ANOMALI = 20
HARI_RIWAYAT_ANOMALI = 14
MIN_HARI_RIWAYAT = 7
PETA_CHANNEL = {
    "Organic Search": "pencarian",
    "Paid Search": "pencarian",
    "Organic Social": "sosial",
    "Paid Social": "sosial",
    "Direct": "langsung",
    "Referral": "site_lain",
}


class GalatGA4(Exception):
    def __init__(self, jenis: str, pesan: str) -> None:
        super().__init__(pesan)
        self.jenis = jenis
        self.pesan = pesan


def token_ga4(jalur_kredensial: str) -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    kredensial = service_account.Credentials.from_service_account_file(
        jalur_kredensial, scopes=[CAKUPAN]
    )
    kredensial.refresh(Request())
    return kredensial.token


def _laporan(http: httpx.Client, token: str, property_id: str, dari: date, sampai: date,
             dimensi: list[str], metrik: list[str]) -> list[tuple[list[str], list[str]]]:
    body = {
        "dateRanges": [{"startDate": dari.isoformat(), "endDate": sampai.isoformat()}],
        "dimensions": [{"name": d} for d in dimensi],
        "metrics": [{"name": m} for m in metrik],
        # Metrik utama duluan sebelum limit 10000 memotong: tanpa ini, baris
        # yang GA4 kembalikan pertama (urutan tidak terjamin) yang terpotong,
        # bukan baris yang benar-benar paling ramai.
        "orderBys": [{"metric": {"metricName": metrik[0]}, "desc": True}],
        "limit": 10000,
    }
    r = http.post(URL_RUN_REPORT.format(pid=property_id), json=body,
                  headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 403:
        raise GalatGA4("akses", "Service account belum ditambahkan sebagai Viewer di property ini")
    if r.status_code >= 400:
        # "RESOURCE_EXHAUSTED" hanya diperiksa di sini, bukan di setiap respons:
        # `pagePath` datang dari pengunjung site, jadi kunjungan ke URL apa pun
        # yang kebetulan memuat substring itu tidak boleh membuat respons 200
        # yang sah dibaca sebagai kuota habis.
        if r.status_code == 429 or "RESOURCE_EXHAUSTED" in r.text:
            raise GalatGA4("kuota", "Kuota GA4 habis; dicoba lagi besok")
        raise GalatGA4("lain", f"GA4 membalas HTTP {r.status_code}: {r.text[:300]}")
    return [
        ([v.get("value", "") for v in row.get("dimensionValues", [])],
         [v.get("value", "0") for v in row.get("metricValues", [])])
        for row in r.json().get("rows", [])
    ]


def _tanggal(teks: str) -> str:
    return f"{teks[:4]}-{teks[4:6]}-{teks[6:8]}"


def ambil_ga4(http: httpx.Client, token: str, property_id: str, dari: date, sampai: date) -> list[dict]:
    hari: dict[str, dict] = {}

    def untuk(t: str) -> dict:
        return hari.setdefault(t, {"tanggal": t, "total": {"kunjungan": 0, "pengunjung": 0},
                                   "halaman": {}, "asal": {}, "perangkat": {}})

    args = (http, token, property_id, dari, sampai)
    for (tgl,), (tampilan, pengguna) in _laporan(*args, ["date"], ["screenPageViews", "totalUsers"]):
        untuk(_tanggal(tgl))["total"] = {"kunjungan": int(tampilan), "pengunjung": int(pengguna)}

    per_hari: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (tgl, path), (tampilan,) in _laporan(*args, ["date", "pagePath"], ["screenPageViews"]):
        per_hari[_tanggal(tgl)].append((path, int(tampilan)))
    for t, daftar in per_hari.items():
        for path, n in sorted(daftar, key=lambda x: -x[1])[:HALAMAN_TERATAS_GA4]:
            untuk(t)["halaman"][path[:180]] = n

    for (tgl, channel), (sesi_ga,) in _laporan(*args, ["date", "sessionDefaultChannelGroup"], ["sessions"]):
        kunci = f"{PETA_CHANNEL.get(channel, 'lainnya')}:{channel}"
        asal = untuk(_tanggal(tgl))["asal"]
        asal[kunci] = asal.get(kunci, 0) + int(sesi_ga)

    for (tgl, perangkat), (tampilan,) in _laporan(*args, ["date", "deviceCategory"], ["screenPageViews"]):
        untuk(_tanggal(tgl))["perangkat"][perangkat.lower()] = int(tampilan)

    return [hari[t] for t in sorted(hari)]


def _tandai_kredensial_gagal(sesi: Session, sites: list[Site], pesan: str) -> dict:
    for site in sites:
        site.ga4_error = pesan
    sesi.commit()
    return {"berhasil": 0, "gagal": len(sites)}


def kumpulkan_ga4(sesi: Session, jalur_kredensial: str, hari_ini: date,
                  http: httpx.Client | None = None, token_fn=token_ga4) -> dict:
    sites = sesi.scalars(
        select(Site).where(Site.ga4_property_id.is_not(None), Site.status != SiteStatus.disabled)
    ).all()
    hasil = {"berhasil": 0, "gagal": 0}
    if not sites:
        return hasil
    try:
        token = token_fn(jalur_kredensial)
    except OSError as exc:
        # Berkas tidak ada/tidak terbaca (mis. FileNotFoundError): str(exc)
        # menyertakan jalur berkas, dan ga4_error tampil di dashboard --
        # jenis exception dicatat di log, bukan pesannya, jalurnya tidak
        # pernah masuk ke keduanya.
        log.warning("Token GA4 gagal dimuat: berkas (%s)", type(exc).__name__)
        return _tandai_kredensial_gagal(
            sesi, sites,
            "Kredensial GA4 tidak dapat dipakai: berkas tidak ditemukan atau tidak dapat dibaca",
        )
    except Exception as exc:  # noqa: BLE001 -- JSON tidak valid, google.auth.exceptions.*,
        # atau kegagalan token_fn kustom apa pun; sama seperti di atas, str(exc)
        # tidak disisipkan supaya jalur berkas tidak pernah bocor ke DB.
        log.warning("Token GA4 gagal dimuat: kredensial (%s)", type(exc).__name__)
        return _tandai_kredensial_gagal(
            sesi, sites, "Kredensial GA4 tidak dapat dipakai: kredensial tidak sah"
        )

    # GA4 memfinalkan data dalam 24-48 jam; tiga hari terakhir diambil ulang.
    dari, sampai = hari_ini - timedelta(days=3), hari_ini - timedelta(days=1)
    milik_sendiri = http is None
    http = http or httpx.Client(timeout=60)
    try:
        for site in sites:
            if not POLA_PROPERTY.match(site.ga4_property_id):
                # property_id di kolom bisa berupa apa saja (diketik manual atau
                # disalin salah); tanpa penjagaan ini ia masuk mentah ke URL dan
                # httpx.InvalidURL (bukan httpx.HTTPError) menggagalkan seluruh
                # putaran sebelum sesi.commit() sempat jalan.
                site.ga4_error = "GA4 property ID tidak valid (harus 6-12 digit)"
                hasil["gagal"] += 1
                continue
            try:
                hari = ambil_ga4(http, token, site.ga4_property_id, dari, sampai)
            except GalatGA4 as exc:
                site.ga4_error = exc.pesan
                hasil["gagal"] += 1
                continue
            except httpx.HTTPError as exc:
                site.ga4_error = f"GA4 tidak dapat dihubungi: {exc}"[:500]
                hasil["gagal"] += 1
                continue
            simpan_traffic(sesi, site.id, hari, "ga4")
            site.ga4_error = None
            site.ga4_diambil_pada = datetime.now(timezone.utc)
            hasil["berhasil"] += 1
        sesi.commit()
    finally:
        if milik_sendiri:
            http.close()
    return hasil


def nilai_anomali(kemarin: int | None, riwayat: list[int]) -> str | None:
    if kemarin is None or len(riwayat) < MIN_HARI_RIWAYAT:
        return None
    median = statistics.median(riwayat)
    if median < MIN_MEDIAN_ANOMALI:
        return None
    if kemarin < 0.5 * median:
        return "anjlok"
    if kemarin > 4 * median:
        return "melonjak"
    return None


def anomali_site(sesi: Session, site_id, hari_ini: date) -> str | None:
    kemarin = hari_ini - timedelta(days=1)
    for sumber in ("plugin", "ga4"):
        baris = dict(sesi.execute(
            select(TrafficHarian.tanggal, TrafficHarian.kunjungan).where(
                TrafficHarian.site_id == site_id,
                TrafficHarian.sumber == sumber,
                TrafficHarian.tanggal >= kemarin - timedelta(days=HARI_RIWAYAT_ANOMALI),
                TrafficHarian.tanggal <= kemarin,
            )
        ).all())
        if not baris:
            continue
        # Hari tanpa baris dianggap tidak ada data, bukan nol kunjungan:
        # connector yang baru dipasang tidak boleh terbaca sebagai "melonjak".
        riwayat = [n for t, n in baris.items() if t < kemarin]
        return nilai_anomali(baris.get(kemarin), riwayat)
    return None


CATATAN_DUA_SUMBER = (
    "Angka GA biasanya lebih kecil karena tidak menghitung pengunjung yang memakai "
    "ad-blocker atau menolak cookie. Keduanya benar menurut cara hitungnya masing-masing."
)
NAMA_KATEGORI = {
    "pencarian": "Pencarian", "sosial": "Media sosial", "langsung": "Langsung",
    "site_lain": "Site lain", "lainnya": "Lainnya",
}


def urai_asal(kunci: str) -> tuple[str, str]:
    kategori, _, nama = kunci.partition(":")
    return NAMA_KATEGORI.get(kategori, "Lainnya"), nama


def ringkasan_traffic(sesi: Session, site_id, dari: date, sampai: date, sumber: str) -> dict | None:
    harian_db = {
        b.tanggal: b
        for b in sesi.scalars(
            select(TrafficHarian).where(
                TrafficHarian.site_id == site_id, TrafficHarian.sumber == sumber,
                TrafficHarian.tanggal >= dari, TrafficHarian.tanggal <= sampai,
            )
        ).all()
    }
    if not harian_db:
        return None
    harian = []
    for i in range((sampai - dari).days + 1):
        t = dari + timedelta(days=i)
        b = harian_db.get(t)
        harian.append({"tanggal": t.isoformat(),
                       "kunjungan": b.kunjungan if b else None,
                       "pengunjung": b.pengunjung if b else None})

    jumlah = func.sum(TrafficRincian.kunjungan).label("n")
    rincian = sesi.execute(
        select(TrafficRincian.dimensi, TrafficRincian.kunci, jumlah)
        .where(TrafficRincian.site_id == site_id, TrafficRincian.sumber == sumber,
               TrafficRincian.tanggal >= dari, TrafficRincian.tanggal <= sampai)
        .group_by(TrafficRincian.dimensi, TrafficRincian.kunci)
        .order_by(jumlah.desc())
    ).all()
    halaman = [{"kunci": k, "kunjungan": int(n)} for d, k, n in rincian if d == "halaman"][:10]
    asal = [
        {"kategori": urai_asal(k)[0], "nama": urai_asal(k)[1], "kunjungan": int(n)}
        for d, k, n in rincian if d == "asal"
    ]
    perangkat = [{"kunci": k, "kunjungan": int(n)} for d, k, n in rincian if d == "perangkat"]

    return {
        "harian": harian,
        "total_kunjungan": sum(b.kunjungan for b in harian_db.values()),
        "total_pengunjung_harian": sum(b.pengunjung for b in harian_db.values()),
        "maks_harian": max(b.kunjungan for b in harian_db.values()),
        "halaman": halaman,
        "asal": asal,
        "perangkat": perangkat,
    }
