import copy
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from wpmgr.errors import BAD_RESPONSE, SiteError
from wpmgr.jobs import monitoring
from wpmgr.jobs.monitoring import tangani_collect_events
from wpmgr.jobs.queue import buat_job
from wpmgr.keamanan import StatusKeamanan, nilai_keamanan
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


def klien_body_mentah(payload: dict) -> SiteClient:
    """Untuk payload yang tidak bisa dibangun lewat kwarg `json=` httpx (ia
    memakai ensure_ascii=False dan allow_nan=False, sehingga surrogate lepas
    atau NaN/Infinity meledak lebih dulu di sisi test, bukan di sisi kode
    yang diuji): body ditulis manual dengan json.dumps(ensure_ascii=True)
    milik stdlib, yang menerima keduanya apa adanya seperti json.loads di
    sisi SiteClient sungguhan."""
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

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


# --- Fix round 1: baris beracun tidak boleh menggagalkan seluruh halaman. ---


def test_pesan_dengan_nul_byte_dibersihkan(sesi, site):
    payload = copy.deepcopy(PAYLOAD)
    payload["errors"][0]["pesan"] = "Uncaught Error: \x00 x()"
    hasil = jalankan(sesi, site, [payload])
    assert hasil["errors"] == 1
    e = sesi.query(CatatanError).one()
    assert "\x00" not in e.pesan
    sesi.refresh(site)
    assert site.events_kursor == payload["kursor"]


def test_baris_overflow_disimpan_sebagai_none(sesi, site):
    payload = copy.deepcopy(PAYLOAD)
    payload["errors"][0]["baris"] = 2**40
    hasil = jalankan(sesi, site, [payload])
    assert hasil["errors"] == 1
    assert sesi.query(CatatanError).one().baris is None


def test_konteks_dengan_nul_dibersihkan(sesi, site):
    payload = copy.deepcopy(PAYLOAD)
    payload["errors"][0]["konteks"] = {"path": "/a\x00b", "jenis": "depan"}
    hasil = jalankan(sesi, site, [payload])
    assert hasil["errors"] == 1
    e = sesi.query(CatatanError).one()
    assert e.konteks["path"] == "/ab"


def test_lone_surrogate_dibersihkan(sesi, site):
    payload = copy.deepcopy(PAYLOAD)
    payload["errors"][0]["pesan"] = "abc\udc00def"
    job = buat_job(sesi, site.id, JobType.collect_events)
    hasil = tangani_collect_events(sesi, job, klien_body_mentah(payload))
    assert hasil["errors"] == 1
    e = sesi.query(CatatanError).one()
    assert "\udc00" not in e.pesan


def test_kegagalan_db_dilewati_via_savepoint(sesi, site, monkeypatch):
    """Validator sendiri dianggap bisa punya bug: nilai yang lolos validasi
    tetapi tetap ditolak database harus melewati baris itu saja, bukan
    menggagalkan seluruh halaman -- ini membuktikan jalur savepoint bekerja
    dengan sendirinya, terlepas dari validasi di 1a."""
    asli = monitoring._baris_kolom

    def _baris_bocor(nilai):
        if nilai == 999999999999:
            return 2**40  # lolos validasi kita, tetap di luar jangkauan `integer`
        return asli(nilai)

    monkeypatch.setattr(monitoring, "_baris_kolom", _baris_bocor)
    payload = copy.deepcopy(PAYLOAD)
    payload["errors"][0]["baris"] = 999999999999
    payload["errors"].append({**PAYLOAD["errors"][0], "sidik_jari": "b" * 32, "baris": 12})
    hasil = jalankan(sesi, site, [payload])
    assert hasil["errors"] == 1
    sisa = sesi.query(CatatanError).one()
    assert sisa.sidik_jari == "b" * 32
    sesi.refresh(site)
    assert site.events_kursor == payload["kursor"]


def test_respons_bukan_dict_menimbulkan_bad_response(sesi, site):
    with pytest.raises(SiteError) as exc:
        jalankan(sesi, site, [[]])
    assert exc.value.error_class == BAD_RESPONSE


def test_jumlah_negatif_dijepit_ke_satu(sesi, site):
    payload = copy.deepcopy(PAYLOAD)
    payload["errors"][0]["jumlah"] = -5
    payload["login_gagal"][0]["jumlah"] = -10
    hasil = jalankan(sesi, site, [payload])
    assert hasil == {"errors": 1, "logins": 1, "login_gagal": 1, "halaman": 1}
    assert sesi.query(CatatanError).one().jumlah == 1
    assert sesi.query(LoginGagal).one().jumlah == 1


def test_login_gagal_jumlah_overflow_dijepit(sesi, site):
    payload = copy.deepcopy(PAYLOAD)
    payload["login_gagal"][0]["jumlah"] = 2**40
    hasil = jalankan(sesi, site, [payload])
    assert hasil["login_gagal"] == 1
    assert sesi.query(LoginGagal).one().jumlah == 2**31 - 1


def test_id_login_di_luar_jangkauan_bigint_dilewati(sesi, site):
    payload = copy.deepcopy(PAYLOAD)
    payload["logins"][0]["id"] = 2**70
    hasil = jalankan(sesi, site, [payload])
    assert hasil["logins"] == 0
    assert sesi.query(KejadianLogin).count() == 0


def test_item_bukan_dict_dalam_daftar_dilewati(sesi, site):
    payload = copy.deepcopy(PAYLOAD)
    payload["errors"] = [None, "bukan-dict", 42, [], PAYLOAD["errors"][0]]
    payload["logins"] = [None, "x", 7, PAYLOAD["logins"][0]]
    payload["login_gagal"] = [None, 3.5, PAYLOAD["login_gagal"][0]]
    hasil = jalankan(sesi, site, [payload])
    assert hasil == {"errors": 1, "logins": 1, "login_gagal": 1, "halaman": 1}


def test_gagal_di_halaman_kedua_mempertahankan_kursor_halaman_pertama(sesi, site):
    halaman_pertama = {**PAYLOAD, "lagi": True}
    with pytest.raises(SiteError):
        jalankan(sesi, site, [halaman_pertama, "bukan-dict"])
    # Tanpa rollback ini, sesi yang sama masih melihat baris halaman pertama
    # di identity map-nya sendiri terlepas dari apakah commit-nya benar-benar
    # sampai ke database -- assert di bawah tidak lagi membuktikan apa pun
    # tentang penyimpanan lintas halaman tanpa rollback eksplisit ini
    # (mencerminkan sesi.rollback() yang dipanggil worker saat menangkap
    # kegagalan tak terduga).
    sesi.rollback()
    sesi.refresh(site)
    assert site.events_kursor == "e=10:1;l=10:5;g=10:1"
    assert sesi.query(CatatanError).count() == 1
    assert sesi.query(KejadianLogin).count() == 1
    assert sesi.query(LoginGagal).count() == 1


def test_konteks_terlalu_besar_disimpan_none(sesi, site):
    payload = copy.deepcopy(PAYLOAD)
    payload["errors"][0]["konteks"] = {"path": "/" + "a" * 9000, "jenis": "depan"}
    hasil = jalankan(sesi, site, [payload])
    assert hasil["errors"] == 1
    assert sesi.query(CatatanError).one().konteks is None


def test_timestamp_masa_depan_dilewati(sesi, site):
    depan = int(time.time()) + 2 * 86400
    payload = copy.deepcopy(PAYLOAD)
    payload["errors"][0]["terakhir"] = depan
    payload["logins"][0]["waktu"] = depan
    payload["login_gagal"][0]["jam"] = depan
    hasil = jalankan(sesi, site, [payload])
    assert hasil == {"errors": 0, "logins": 0, "login_gagal": 0, "halaman": 1}
    sesi.refresh(site)
    assert site.events_kursor == payload["kursor"]


# --- Fix round 2: NaN/Infinity dan konteks bersarang terlalu dalam. ---


def test_konteks_dengan_nan_disimpan_none(sesi, site):
    """json.loads menerima literal NaN/Infinity (bukan JSON standar, tetapi
    Python mengizinkannya), lalu JSONB Postgres menolaknya -- konteks harus
    dibuang di sini, bukan menggugurkan baris errornya lewat savepoint."""
    payload = copy.deepcopy(PAYLOAD)
    payload["errors"][0]["konteks"] = {"path": "/", "jenis": "depan", "angka": float("nan")}
    job = buat_job(sesi, site.id, JobType.collect_events)
    hasil = tangani_collect_events(sesi, job, klien_body_mentah(payload))
    assert hasil["errors"] == 1
    assert sesi.query(CatatanError).one().konteks is None


def _konteks_bersarang(kedalaman: int) -> dict:
    d = {"nilai": "dasar"}
    for _ in range(kedalaman):
        d = {"anak": d}
    return d


def test_konteks_bersarang_terlalu_dalam_disimpan_none(sesi, site):
    """json.loads menerima ~950 level bersarang dari payload hanya beberapa
    KB; rekursi _bersihkan_json sendiri jauh lebih boros stack (dua frame
    Python per level) dan tanpa batas eksplisit mencapai RecursionError lebih
    dulu -- baris error itu sendiri harus tetap tersimpan, hanya konteksnya
    yang dibuang, dan halaman (termasuk baris lain) tidak boleh ikut gugur."""
    payload = copy.deepcopy(PAYLOAD)
    payload["errors"][0]["konteks"] = _konteks_bersarang(600)
    hasil = jalankan(sesi, site, [payload])
    assert hasil == {"errors": 1, "logins": 1, "login_gagal": 1, "halaman": 1}
    e = sesi.query(CatatanError).one()
    assert e.konteks is None
    sesi.refresh(site)
    assert site.events_kursor == payload["kursor"]


def _hanya_login(logins):
    return {"errors": [], "logins": logins, "login_gagal": [], "kursor": "e=0:0;l=0:0;g=0:0",
            "lagi": False}


def test_id_sama_dengan_waktu_berbeda_disimpan_dua_baris(sesi, site):
    # AUTO_INCREMENT site bisa mulai ulang (restore backup, atau MySQL 5.7
    # setelah restart), sehingga id lama dipakai lagi untuk kejadian baru.
    jalankan(sesi, site, [_hanya_login([PAYLOAD["logins"][0]])])
    jalankan(sesi, site, [_hanya_login([{**PAYLOAD["logins"][0], "waktu": T + 3600}])])
    assert sesi.query(KejadianLogin).count() == 2


def test_id_dan_waktu_sama_tetap_satu_baris(sesi, site):
    jalankan(sesi, site, [_hanya_login([PAYLOAD["logins"][0]])])
    jalankan(sesi, site, [_hanya_login([dict(PAYLOAD["logins"][0])])])
    assert sesi.query(KejadianLogin).count() == 1


def test_admin_baru_dengan_id_terpakai_ulang_tetap_memerahkan_status(sesi, site):
    jalankan(sesi, site, [_hanya_login([PAYLOAD["logins"][0]])])
    # Login lama sudah "diperiksa"; yang tersisa hanya admin baru ber-id sama.
    site.keamanan_diperiksa_pada = sesi.query(KejadianLogin).one().dicatat_pada
    sesi.commit()
    admin = {**PAYLOAD["logins"][0], "waktu": T + 7200, "jenis": "admin_baru",
             "username": "penyusup"}
    jalankan(sesi, site, [_hanya_login([admin])])
    hasil = nilai_keamanan(sesi, site, WAKTU_T + timedelta(hours=3))
    assert hasil.status == StatusKeamanan.perlu_diperiksa
    assert any("penyusup" in a for a in hasil.alasan)
