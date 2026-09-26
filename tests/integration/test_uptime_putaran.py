import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from wpmgr.kunci import KUNCI_UPTIME, kunci_advisory
from wpmgr.models import (
    Site,
    SiteStatus,
    UptimeCheck,
    UptimeHasil,
    UptimeInsiden,
    UptimeStatus,
)
from wpmgr.uptime import HasilCek, jalankan_putaran

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)
NAIK = HasilCek(UptimeHasil.naik, 200, 100)
GAGAL = HasilCek(UptimeHasil.gagal, 500, 90, "HTTP 500")
BLOKIR = HasilCek(UptimeHasil.terblokir, 403, 40, "Permintaan dashboard diblokir firewall site")


def putaran(sesi, hasil, menit):
    return jalankan_putaran(sesi, lambda url: hasil, sekarang=T0 + timedelta(minutes=menit))


def test_dua_kegagalan_membuka_insiden_sejak_kegagalan_pertama(sesi, site):
    putaran(sesi, NAIK, 0)
    putaran(sesi, GAGAL, 5)
    sesi.refresh(site)
    assert site.uptime_status == UptimeStatus.naik
    assert sesi.query(UptimeInsiden).count() == 0

    putaran(sesi, GAGAL, 10)
    sesi.refresh(site)
    assert site.uptime_status == UptimeStatus.mati
    insiden = sesi.query(UptimeInsiden).one()
    assert insiden.mulai == T0 + timedelta(minutes=5)
    assert insiden.selesai is None
    assert insiden.penyebab == "HTTP 500"


def test_pulih_menutup_insiden(sesi, site):
    for menit in (0, 5):
        putaran(sesi, GAGAL, menit)
    putaran(sesi, NAIK, 10)
    sesi.refresh(site)
    assert site.uptime_status == UptimeStatus.naik
    assert site.uptime_gagal_beruntun == 0
    assert sesi.query(UptimeInsiden).one().selesai == T0 + timedelta(minutes=10)


def test_kegagalan_berlanjut_tidak_menambah_insiden(sesi, site):
    for menit in (0, 5, 10, 15):
        putaran(sesi, GAGAL, menit)
    assert sesi.query(UptimeInsiden).count() == 1


def test_terblokir_tanpa_insiden(sesi, site):
    for menit in (0, 5, 10):
        putaran(sesi, BLOKIR, menit)
    sesi.refresh(site)
    assert site.uptime_status == UptimeStatus.terblokir
    assert sesi.query(UptimeInsiden).count() == 0


def test_putaran_gangguan_dashboard_tidak_mengubah_status(sesi, site):
    for i in range(4):
        sesi.add(Site(id=uuid.uuid4(), nama=f"S{i}", url=f"https://s{i}.test",
                      status=SiteStatus.active, secret_terenkripsi=b"x"))
    sesi.commit()
    for menit in (0, 5, 10):
        p = putaran(sesi, GAGAL, menit)
        assert p.gangguan_dashboard is True
    sesi.refresh(site)
    assert site.uptime_status == UptimeStatus.belum_dicek
    assert site.uptime_gagal_beruntun == 0
    assert sesi.query(UptimeInsiden).count() == 0
    # Hasil cek tetap disimpan untuk ditelusuri.
    assert sesi.query(UptimeCheck).count() == 15


def test_site_disabled_tidak_dicek(sesi, site):
    site.status = SiteStatus.disabled
    sesi.commit()
    assert putaran(sesi, NAIK, 0) is None


def test_cek_dijalankan_untuk_setiap_url(sesi, site):
    dilihat = []
    jalankan_putaran(sesi, lambda url: dilihat.append(url) or NAIK, sekarang=T0)
    assert dilihat == [site.url]


def test_cek_macet_dianggap_gagal_tanpa_menahan_putaran(sesi, site, monkeypatch):
    monkeypatch.setattr("wpmgr.uptime.BATAS_PUTARAN", 0.3)
    lambat = Site(id=uuid.uuid4(), nama="Zlambat", url="https://lambat.test",
                  status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(lambat)
    sesi.commit()
    lepas = threading.Event()

    def cek(url):
        if url == lambat.url:
            lepas.wait(10)
        return NAIK

    mulai = time.monotonic()
    try:
        p = jalankan_putaran(sesi, cek, sekarang=T0)
    finally:
        lepas.set()
    assert time.monotonic() - mulai < 3
    assert (p.jumlah_site, p.jumlah_gagal, p.gangguan_dashboard) == (2, 1, False)
    hasil = {c.site_id: c for c in sesi.query(UptimeCheck).all()}
    assert hasil[site.id].hasil == UptimeHasil.naik
    assert hasil[lambat.id].hasil == UptimeHasil.gagal
    assert hasil[lambat.id].pesan == "Tidak selesai dalam batas waktu"


def test_kunci_advisory_tidak_bisa_diambil_dua_kali(engine):
    with kunci_advisory(engine, KUNCI_UPTIME) as pertama:
        assert pertama is True
        with kunci_advisory(engine, KUNCI_UPTIME) as kedua:
            assert kedua is False
    with kunci_advisory(engine, KUNCI_UPTIME) as lagi:
        assert lagi is True
