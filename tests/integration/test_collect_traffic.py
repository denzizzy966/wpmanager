from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from wpmgr.errors import BAD_RESPONSE, SiteError
from wpmgr.jobs import monitoring
from wpmgr.jobs.monitoring import simpan_traffic, tangani_collect_traffic
from wpmgr.jobs.queue import buat_job
from wpmgr.models import Job, JobType, SiteStatus, TrafficHarian, TrafficRincian
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration

HARI = {
    "tanggal": "2026-09-21",
    "total": {"kunjungan": 12, "pengunjung": 7},
    "halaman": {"/": 8, "/layanan": 4},
    "asal": {"langsung:": 5, "pencarian:Google": 6},
    "perangkat": {"mobile": 9, "desktop": 3},
}


def klien(muatan, diminta=None):
    def handler(request):
        if diminta is not None:
            diminta.append(request)
        return httpx.Response(200, json=muatan)

    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_menyimpan_hari_dan_rincian(sesi, site):
    job = buat_job(sesi, site.id, JobType.collect_traffic)
    hasil = tangani_collect_traffic(sesi, job, klien({"zona_waktu": "Asia/Jakarta", "hari": [HARI]}))
    assert hasil == {"hari": 1}
    h = sesi.get(TrafficHarian, (site.id, date(2026, 9, 21), "plugin"))
    assert (h.kunjungan, h.pengunjung) == (12, 7)
    assert sesi.query(TrafficRincian).filter_by(site_id=site.id, dimensi="halaman").count() == 2
    sesi.refresh(site)
    assert site.traffic_diambil_pada is not None


def test_tanpa_dari_dashboard_membiarkan_site_menentukan_kemarin(sesi, site):
    diminta = []
    job = buat_job(sesi, site.id, JobType.collect_traffic)
    tangani_collect_traffic(sesi, job, klien({"hari": []}, diminta))
    assert diminta[0].url.query == b""


def test_pengambilan_ulang_mengganti_bukan_menambah(sesi, site):
    simpan_traffic(sesi, site.id, [HARI], "plugin")
    baru = {**HARI, "total": {"kunjungan": 20, "pengunjung": 9}, "halaman": {"/": 20}}
    simpan_traffic(sesi, site.id, [baru], "plugin")
    assert sesi.get(TrafficHarian, (site.id, date(2026, 9, 21), "plugin")).kunjungan == 20
    halaman = sesi.query(TrafficRincian).filter_by(site_id=site.id, dimensi="halaman").all()
    assert [(r.kunci, r.kunjungan) for r in halaman] == [("/", 20)]


def test_sumber_lain_tidak_tersentuh(sesi, site):
    simpan_traffic(sesi, site.id, [HARI], "ga4")
    simpan_traffic(sesi, site.id, [HARI], "plugin")
    assert sesi.query(TrafficHarian).filter_by(site_id=site.id).count() == 2


def test_dimensi_kosong_berbentuk_list_diterima(sesi, site):
    kosong = {"tanggal": "2026-09-20", "total": {"kunjungan": 0, "pengunjung": 0},
              "halaman": [], "asal": [], "perangkat": []}
    assert simpan_traffic(sesi, site.id, [kosong], "plugin") == 1


def test_hari_rusak_dilewati(sesi, site):
    rusak = [{"tanggal": "bukan-tanggal"}, {"total": {}}, {**HARI, "halaman": {"/": "x"}}]
    assert simpan_traffic(sesi, site.id, rusak, "plugin") == 0


def test_kunci_panjang_yang_bertabrakan_setelah_dipotong_dijumlah(sesi, site):
    panjang = "/" + "a" * 300
    hari = {**HARI, "halaman": {panjang + "1": 2, panjang + "2": 3}}
    simpan_traffic(sesi, site.id, [hari], "plugin")
    baris = sesi.query(TrafficRincian).filter_by(site_id=site.id, dimensi="halaman").one()
    assert baris.kunjungan == 5


def test_enqueue_traffic_hanya_site_aktif_berfitur(sesi, site, engine, monkeypatch):
    from wpmgr import db
    from wpmgr.cli import enqueue_traffic
    from wpmgr.models import Site

    monkeypatch.setattr(db, "SessionLocal",
                        sessionmaker(bind=engine, expire_on_commit=False, future=True))
    site.fitur = ["traffic"]
    sesi.add(Site(nama="Tanpa", url="https://tanpa.test", status=SiteStatus.active,
                  secret_terenkripsi=b"x", fitur=["events"]))
    sesi.commit()
    assert enqueue_traffic() == 1
    assert enqueue_traffic() == 0
    assert sesi.query(Job).filter_by(tipe=JobType.collect_traffic).one().max_attempts == 1


# --- Pengerasan terhadap site yang disusupi (pelajaran Task 16) ------------


def test_respons_bukan_dict_menimbulkan_bad_response(sesi, site):
    job = buat_job(sesi, site.id, JobType.collect_traffic)
    with pytest.raises(SiteError) as exc:
        tangani_collect_traffic(sesi, job, klien([]))
    assert exc.value.error_class == BAD_RESPONSE


def test_item_bukan_dict_dalam_daftar_dilewati(sesi, site):
    daftar = [None, "bukan-dict", 42, [], HARI]
    assert simpan_traffic(sesi, site.id, daftar, "plugin") == 1


def test_kunjungan_negatif_dijepit_ke_nol(sesi, site):
    hari = {**HARI, "total": {"kunjungan": -5, "pengunjung": -1}, "halaman": {"/": -3},
            "asal": {}, "perangkat": {}}
    simpan_traffic(sesi, site.id, [hari], "plugin")
    h = sesi.get(TrafficHarian, (site.id, date(2026, 9, 21), "plugin"))
    assert (h.kunjungan, h.pengunjung) == (0, 0)
    assert sesi.query(TrafficRincian).filter_by(
        site_id=site.id, dimensi="halaman"
    ).one().kunjungan == 0


def test_kunjungan_overflow_dijepit(sesi, site):
    hari = {**HARI, "total": {"kunjungan": 2**40, "pengunjung": 2**40}}
    simpan_traffic(sesi, site.id, [hari], "plugin")
    h = sesi.get(TrafficHarian, (site.id, date(2026, 9, 21), "plugin"))
    assert (h.kunjungan, h.pengunjung) == (2**31 - 1, 2**31 - 1)


def test_kunci_dengan_nul_dibersihkan(sesi, site):
    hari = {**HARI, "halaman": {"/a\x00b": 3}}
    simpan_traffic(sesi, site.id, [hari], "plugin")
    baris = sesi.query(TrafficRincian).filter_by(site_id=site.id, dimensi="halaman").one()
    assert baris.kunci == "/ab"


def test_tanggal_masa_depan_dilewati(sesi, site):
    jauh = (datetime.now(tz=timezone.utc).date() + timedelta(days=5)).isoformat()
    hari = {**HARI, "tanggal": jauh}
    assert simpan_traffic(sesi, site.id, [hari], "plugin") == 0


def test_kegagalan_db_dilewati_via_savepoint(sesi, site, monkeypatch):
    """Validator sendiri dianggap bisa punya bug: nilai yang lolos validasi
    tetapi tetap ditolak database harus melewati hari itu saja, bukan
    menggagalkan seluruh respons -- ini membuktikan jalur savepoint bekerja
    dengan sendirinya, terlepas dari validasi klem di atas."""
    asli = monitoring._hitungan_traffic

    def _bocor(nilai):
        if nilai == 999999999999:
            return 2**40  # lolos validasi kita, tetap di luar jangkauan `integer`
        return asli(nilai)

    monkeypatch.setattr(monitoring, "_hitungan_traffic", _bocor)
    racun = {**HARI, "tanggal": "2026-09-21",
             "total": {"kunjungan": 999999999999, "pengunjung": 0}}
    aman = {**HARI, "tanggal": "2026-09-22"}
    n = simpan_traffic(sesi, site.id, [racun, aman], "plugin")
    assert n == 1
    assert sesi.query(TrafficHarian).filter_by(site_id=site.id).count() == 1
    assert sesi.get(TrafficHarian, (site.id, date(2026, 9, 22), "plugin")) is not None


def test_batas_hari_per_respons(sesi, site, monkeypatch):
    monkeypatch.setattr(monitoring, "BATAS_HARI_TRAFFIC", 2)
    hari = [{**HARI, "tanggal": f"2026-09-{20 + i:02d}"} for i in range(5)]
    assert simpan_traffic(sesi, site.id, hari, "plugin") == 2


def test_batas_kunci_per_dimensi(sesi, site, monkeypatch):
    monkeypatch.setattr(monitoring, "BATAS_KUNCI_DIMENSI", 2)
    hari = {**HARI, "halaman": {f"/p{i}": 1 for i in range(5)}}
    simpan_traffic(sesi, site.id, [hari], "plugin")
    assert sesi.query(TrafficRincian).filter_by(
        site_id=site.id, dimensi="halaman"
    ).count() == 2
