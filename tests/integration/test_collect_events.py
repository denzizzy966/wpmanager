import copy
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from wpmgr.jobs.monitoring import tangani_collect_events
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    CatatanError,
    Job,
    JobStatus,
    JobType,
    KejadianLogin,
    LoginGagal,
    SiteStatus,
)
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration

T = 1790064000  # 2026-09-22 08:00 UTC
WAKTU_T = datetime.fromtimestamp(T, tz=timezone.utc)

PAYLOAD = {
    "errors": [{
        "sidik_jari": "a" * 32, "tingkat": "fatal", "komponen_tipe": "plugin",
        "komponen_slug": "elementor", "pesan": "Uncaught Error: x()", "file": "wp-content/plugins/elementor/a.php",
        "baris": 12, "konteks": {"path": "/", "jenis": "depan"}, "jumlah": 3,
        "pertama": T, "terakhir": T + 60,
    }],
    "logins": [{
        "id": 5, "waktu": T, "jenis": "berhasil", "username": "admin", "role": "administrator",
        "ip": "203.0.113.9", "lewat_cloudflare": False, "user_agent": "curl/8", "jalur": "form",
    }],
    "login_gagal": [{
        "jam": T, "ip": "198.51.100.7", "username": "admin", "jalur": "xmlrpc", "jumlah": 40,
        "user_agent": "python-requests/2",
    }],
    "kursor": "e=10:1;l=10:5;g=10:1",
    "lagi": False,
}


def klien_berurutan(balasan, diminta):
    antrian = list(balasan)

    def handler(request):
        diminta.append(request)
        return httpx.Response(200, json=antrian.pop(0) if len(antrian) > 1 else antrian[0])

    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


def jalankan(sesi, site, balasan, diminta=None):
    job = buat_job(sesi, site.id, JobType.collect_events)
    return tangani_collect_events(sesi, job, klien_berurutan(balasan, diminta if diminta is not None else []))


def test_menyimpan_ketiga_jenis(sesi, site, monkeypatch):
    monkeypatch.setattr("wpmgr.geoip.negara", lambda ip: {"203.0.113.9": "ID", "198.51.100.7": "SG"}.get(ip))
    hasil = jalankan(sesi, site, [PAYLOAD])
    assert hasil == {"errors": 1, "logins": 1, "login_gagal": 1, "halaman": 1}
    e = sesi.query(CatatanError).one()
    assert (e.tingkat, e.komponen_slug, e.jumlah) == ("fatal", "elementor", 3)
    assert e.terakhir_terlihat == WAKTU_T + timedelta(seconds=60)
    assert sesi.query(KejadianLogin).one().negara == "ID"
    g = sesi.query(LoginGagal).one()
    assert (g.jumlah, g.negara, g.jalur) == (40, "SG", "xmlrpc")
    sesi.refresh(site)
    assert site.events_kursor == "e=10:1;l=10:5;g=10:1"


def test_mengambil_ulang_payload_sama_idempoten(sesi, site):
    jalankan(sesi, site, [PAYLOAD])
    jalankan(sesi, site, [PAYLOAD])
    assert sesi.query(CatatanError).one().jumlah == 3
    assert sesi.query(KejadianLogin).count() == 1
    assert sesi.query(LoginGagal).one().jumlah == 40


def test_nilai_baru_dari_site_menimpa(sesi, site):
    jalankan(sesi, site, [PAYLOAD])
    kedua = copy.deepcopy(PAYLOAD)
    kedua["errors"][0]["jumlah"] = 9
    kedua["login_gagal"][0]["jumlah"] = 55
    jalankan(sesi, site, [kedua])
    assert sesi.query(CatatanError).one().jumlah == 9
    assert sesi.query(LoginGagal).one().jumlah == 55


def test_kursor_dikirim_pada_pengambilan_berikutnya(sesi, site):
    jalankan(sesi, site, [PAYLOAD])
    diminta = []
    jalankan(sesi, site, [{**PAYLOAD, "errors": [], "logins": [], "login_gagal": []}], diminta)
    assert diminta[0].url.params["kursor"] == "e=10:1;l=10:5;g=10:1"


def test_paging_berhenti_di_sepuluh_halaman(sesi, site):
    diminta = []
    hasil = jalankan(sesi, site, [{**PAYLOAD, "lagi": True}], diminta)
    assert hasil["halaman"] == 10
    assert len(diminta) == 10


def test_baris_rusak_dilewati(sesi, site):
    rusak = {
        "errors": [{"tingkat": "fatal"}, {**PAYLOAD["errors"][0], "pertama": "bukan-angka"}],
        "logins": [{"id": "x"}], "login_gagal": [{"jam": T}], "kursor": "e=1:1;l=0:0;g=0:0",
        "lagi": False,
    }
    hasil = jalankan(sesi, site, [rusak])
    assert hasil == {"errors": 0, "logins": 0, "login_gagal": 0, "halaman": 1}
    sesi.refresh(site)
    assert site.events_kursor == "e=1:1;l=0:0;g=0:0"


def _update_sukses(sesi, site, tipe, slug, selesai):
    job = buat_job(sesi, site.id, JobType.update_package,
                   {"tipe": tipe, "slug": slug, "dari_versi": "3.18", "ke_versi": "3.20"})
    job.status = JobStatus.success
    job.finished_at = selesai
    job.hasil = {"versi_sebelum": "3.18", "versi_sesudah": "3.20"}
    sesi.commit()
    return job


def test_error_baru_setelah_update_plugin_dikaitkan(sesi, site):
    job = _update_sukses(sesi, site, "plugin", "elementor/elementor.php", WAKTU_T - timedelta(minutes=30))
    jalankan(sesi, site, [PAYLOAD])
    e = sesi.query(CatatanError).one()
    assert e.setelah_update["job_id"] == job.id
    assert e.setelah_update["versi_sesudah"] == "3.20"
    assert e.setelah_update["slug"] == "elementor/elementor.php"


@pytest.mark.parametrize("selisih", [timedelta(minutes=61), timedelta(minutes=-5)])
def test_update_di_luar_jendela_tidak_dikaitkan(sesi, site, selisih):
    _update_sukses(sesi, site, "plugin", "elementor/elementor.php", WAKTU_T - selisih)
    jalankan(sesi, site, [PAYLOAD])
    assert sesi.query(CatatanError).one().setelah_update is None


def test_plugin_lain_tidak_dikaitkan(sesi, site):
    _update_sukses(sesi, site, "plugin", "elementor-pro/elementor-pro.php", WAKTU_T - timedelta(minutes=10))
    jalankan(sesi, site, [PAYLOAD])
    assert sesi.query(CatatanError).one().setelah_update is None


def test_kaitan_hanya_dihitung_saat_sisipan_pertama(sesi, site):
    jalankan(sesi, site, [PAYLOAD])
    _update_sukses(sesi, site, "plugin", "elementor/elementor.php", WAKTU_T - timedelta(minutes=10))
    jalankan(sesi, site, [PAYLOAD])
    assert sesi.query(CatatanError).one().setelah_update is None


def test_status_selesai_dipertahankan_saat_upsert(sesi, site):
    jalankan(sesi, site, [PAYLOAD])
    e = sesi.query(CatatanError).one()
    e.ditandai_selesai_pada = WAKTU_T + timedelta(hours=1)
    sesi.commit()
    jalankan(sesi, site, [PAYLOAD])
    sesi.refresh(e)
    assert e.ditandai_selesai_pada == WAKTU_T + timedelta(hours=1)


def test_enqueue_monitoring_hanya_site_aktif_berfitur(sesi, site, engine, monkeypatch):
    from wpmgr import db
    from wpmgr.cli import enqueue_monitoring
    from wpmgr.models import Site

    monkeypatch.setattr(db, "SessionLocal",
                        sessionmaker(bind=engine, expire_on_commit=False, future=True))
    site.fitur = ["events"]
    sesi.add_all([
        Site(nama="Tanpa", url="https://tanpa.test", status=SiteStatus.active,
             secret_terenkripsi=b"x", fitur=["self_update"]),
        Site(nama="Mati", url="https://mati.test", status=SiteStatus.unreachable,
             secret_terenkripsi=b"x", fitur=["events"]),
    ])
    sesi.commit()

    assert enqueue_monitoring() == 1
    assert enqueue_monitoring() == 0  # tidak menggandakan yang masih tertunda
    job = sesi.query(Job).filter_by(tipe=JobType.collect_events).one()
    assert job.site_id == site.id
    assert job.max_attempts == 1
