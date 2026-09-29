import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker
from staging_palsu import GB, PembantuPalsu, ProduksiPalsu

from wpmgr.crypto import enkripsi_secret
from wpmgr.errors import STAGING_DITOLAK, TRANSIENT, SiteError
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    PackageType,
    Site,
    SitePackage,
    SiteStatus,
    Staging,
    StagingSnapshot,
    StagingUji,
    StatusStaging,
)
from wpmgr.staging import dorong, umum
from wpmgr.staging.pembantu import GalatPembantu, StatusPembantu

pytestmark = pytest.mark.integration

ROUTE = [
    ("GET", "/api/sites/{id}/staging"), ("POST", "/api/sites/{id}/staging"), ("DELETE", "/api/sites/{id}/staging"),
    ("POST", "/api/sites/{id}/staging/jalan"), ("POST", "/api/sites/{id}/staging/jeda"),
    ("GET", "/api/sites/{id}/staging/tanda-air"), ("POST", "/api/sites/{id}/staging/dorong"),
    ("POST", "/api/sites/{id}/staging/kembalikan"), ("POST", "/api/sites/{id}/staging/sandi"),
    ("POST", "/api/sites/{id}/staging/uji"), ("POST", "/api/staging/uji"), ("GET", "/api/sites/{id}/staging/uji"),
    ("GET", "/api/sites/{id}/staging/snapshot"), ("POST", "/api/sites/{id}/staging/batal"),
    ("GET", "/api/sites/{id}/staging/sso"), ("GET", "/api/sites/{id}/staging/email"),
    ("GET", "/api/sites/{id}/staging/email/abc123"),
]


@pytest.fixture
def pb(staging_aktif, monkeypatch):
    palsu = PembantuPalsu(staging_aktif)
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


@pytest.fixture
def siap(sesi, site_staging):
    site_staging.ditarik_pada = datetime.now(timezone.utc)
    site_staging.aktif = True
    site_staging.status = StatusStaging.siap
    site_staging.tanda_air = {"sumber": {"comments": {"maks_id": 3, "jumlah": 2}}}
    site_staging.secret_connector_terenkripsi = enkripsi_secret("d" * 64)
    sesi.commit()
    return site_staging


def _jumlah_job(sesi) -> int:
    sesi.expire_all()
    return sesi.query(Job).count()


@pytest.mark.parametrize("metode,path", ROUTE)
def test_anonim_ditolak(engine, staging_aktif, metode, path):
    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), base_url="https://testserver")
    r = anon.request(metode, path.format(id=uuid.uuid4()), json={})
    assert r.status_code == 401


def test_fitur_mati(klien_web, site, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    get_settings.cache_clear()
    assert klien_web.get(f"/api/sites/{site.id}/staging").json() == {"aktif_fitur": False}
    assert klien_web.post(f"/api/sites/{site.id}/staging", json={}).status_code == 404


def test_buat_staging_membalas_sandi_sekali(klien_web, sesi, site, staging_aktif, pb):
    site.url = "https://www.Toko-Contoh.co.id"
    site.fitur = ["staging"]
    sesi.commit()
    r = klien_web.post(f"/api/sites/{site.id}/staging", json={})
    assert r.status_code == 200
    d = r.json()
    assert d["pengguna"] == "staging" and len(d["sandi"]) >= 16
    st = sesi.query(Staging).one()
    assert st.nama == "toko-contoh-co-id"
    assert bcrypt.checkpw(d["sandi"].encode(), st.sandi_hash.encode())
    job = sesi.get(Job, d["job_id"])
    assert job.tipe == JobType.staging_tarik
    job.status = JobStatus.success
    sesi.commit()
    kedua = klien_web.post(f"/api/sites/{site.id}/staging", json={}).json()
    assert kedua["sandi"] is None


def test_nama_bentrok_diberi_akhiran(klien_web, sesi, site, staging_aktif, pb):
    lain = Site(id=uuid.uuid4(), nama="L", url="https://lain.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(lain)
    sesi.flush()
    sesi.add(Staging(site_id=lain.id, nama="toko-id"))
    site.url = "https://toko.id"
    site.fitur = ["staging"]
    sesi.commit()
    klien_web.post(f"/api/sites/{site.id}/staging", json={})
    assert sesi.query(Staging).filter(Staging.site_id == site.id).one().nama == "toko-id-2"


def test_buat_ditolak_tanpa_izin_connector_dan_batas_aktif(klien_web, sesi, site, staging_aktif, pb):
    r = klien_web.post(f"/api/sites/{site.id}/staging", json={})
    assert r.status_code == 409 and "Izinkan staging" in r.json()["detail"]
    site.fitur = ["staging"]
    for i in range(3):
        s = Site(id=uuid.uuid4(), nama=f"S{i}", url=f"https://s{i}.test", status=SiteStatus.active, secret_terenkripsi=b"x")
        sesi.add(s)
        sesi.flush()
        sesi.add(Staging(site_id=s.id, nama=f"s{i}", aktif=True))
    sesi.commit()
    r = klien_web.post(f"/api/sites/{site.id}/staging", json={})
    assert r.status_code == 409 and "3 staging aktif" in r.json()["detail"]
    assert sesi.query(Staging).filter(Staging.site_id == site.id).count() == 0


def test_segarkan_butuh_konfirmasi_bila_staging_diubah(klien_web, sesi, siap, pb):
    siap.diubah_pada = siap.ditarik_pada + timedelta(hours=1)
    sesi.commit()
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging", json={})
    assert r.status_code == 409 and "diubah" in r.json()["detail"]
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging", json={"konfirmasi": True}).status_code == 200


def test_job_ganda_ditolak(klien_web, sesi, siap, pb):
    buat_job(sesi, siap.site_id, JobType.staging_dorong, {"mode": "hanya_kode"})
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging", json={})
    assert r.status_code == 409 and "pekerjaan staging" in r.json()["detail"]


def test_job_tertunda_di_site_nonaktif_dijelaskan(klien_web, sesi, siap, pb):
    """Job staging di site `disabled` tidak pernah diklaim worker: pesan 409 menyebut sebabnya."""
    buat_job(sesi, siap.site_id, JobType.staging_tarik)
    site = sesi.get(Site, siap.site_id)
    site.status = SiteStatus.disabled
    sesi.commit()
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/dorong", json={"mode": "hanya_kode"})
    assert r.status_code == 409 and "dinonaktifkan" in r.json()["detail"]


def test_status_staging_dan_kemajuan_job(klien_web, sesi, siap, pb):
    job = buat_job(sesi, siap.site_id, JobType.staging_tarik)
    job.payload = {"kemajuan": {"tahap": "berkas", "byte_total": 200 * 1024**2, "byte_selesai": 50 * 1024**2,
                                "mulai": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}}
    sesi.commit()
    d = klien_web.get(f"/api/sites/{siap.site_id}/staging").json()
    assert d["aktif_fitur"] is True
    assert d["staging"]["url"] == "https://contoh-test.staging.contoh.id"
    assert d["staging"]["status"] == "siap"
    assert d["job"]["progres"]["persen"] == 25
    assert d["job"]["progres"]["teks"].startswith("Menyalin berkas 25% · 50,0 MB dari 200,0 MB · sisa ±30 menit")
    aktif = klien_web.get("/api/jobs/active").json()
    assert aktif[0]["progres"].startswith("Menyalin berkas 25%")


def test_asal_gagal_hanya_tampil_saat_gagal(klien_web, sesi, siap, pb):
    siap.gagal_asal = "salinan"
    sesi.commit()
    assert "gagal_asal" not in klien_web.get(f"/api/sites/{siap.site_id}/staging").json()["staging"]
    siap.status = StatusStaging.gagal
    sesi.commit()
    assert klien_web.get(f"/api/sites/{siap.site_id}/staging").json()["staging"]["gagal_asal"] == "salinan"


def test_jalan_dan_jeda(klien_web, sesi, siap, pb):
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/jeda").status_code == 200
    sesi.refresh(siap)
    assert (siap.aktif, siap.status) == (False, StatusStaging.dijeda)
    pb.status_palsu = StatusPembantu(int(1.5 * GB), 1, 1, {}, {})
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/jalan")
    assert r.status_code == 409 and "RAM tersedia" in r.json()["detail"]
    pb.status_palsu = StatusPembantu(8 * GB, 1, 1, {}, {})
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/jalan").status_code == 200
    sesi.refresh(siap)
    assert (siap.aktif, siap.status) == (True, StatusStaging.siap)
    assert ("jeda", "contoh-test") in pb.panggilan and ("jalan", "contoh-test") in pb.panggilan
    pesan = [a.pesan for a in sesi.query(ActivityLog).order_by(ActivityLog.id)]
    assert pesan == ["Staging dijeda oleh a@b.test", "Staging dijalankan oleh a@b.test"]


def test_jeda_dan_jalan_tidak_menyentuh_asal_gagal(klien_web, sesi, siap, pb):
    siap.status = StatusStaging.gagal
    siap.gagal_asal = "produksi"
    sesi.commit()
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/jeda").status_code == 200
    sesi.refresh(siap)
    assert (siap.aktif, siap.status, siap.gagal_asal) == (False, StatusStaging.gagal, "produksi")
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/jalan").status_code == 200
    sesi.refresh(siap)
    assert (siap.aktif, siap.status, siap.gagal_asal) == (True, StatusStaging.gagal, "produksi")


def test_jalan_ditolak_bila_belum_pernah_ditarik(klien_web, sesi, site_staging, pb):
    r = klien_web.post(f"/api/sites/{site_staging.site_id}/staging/jalan")
    assert r.status_code == 409
    assert pb.panggilan == []


def test_galat_pembantu_menjadi_502(klien_web, siap, pb):
    pb.gagal["jeda"] = GalatPembantu("docker", "Perintah Docker di server staging gagal.")
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/jeda")
    assert r.status_code == 502
    assert r.json()["detail"] == "Perintah Docker di server staging gagal."


def test_hapus_mempertahankan_snapshot(klien_web, sesi, siap, staging_aktif, pb):
    akar = staging_aktif / str(siap.site_id)
    (akar / "files").mkdir(parents=True)
    (akar / "snapshot" / "j1").mkdir(parents=True)
    (akar / "indeks.jsonl").write_bytes(b"{}\n")
    sesi.add(StagingSnapshot(site_id=siap.site_id, jenis="sebelum_dorong", status="tersedia", ukuran=1,
                             path=f"{siap.site_id}/snapshot/j1"))
    sesi.commit()
    (staging_aktif / "router").mkdir()
    (staging_aktif / "router" / "contoh-test.rahasia").write_bytes(b"e" * 64)
    r = klien_web.delete(f"/api/sites/{siap.site_id}/staging")
    assert r.status_code == 200
    assert [p[0] for p in pb.panggilan] == ["hapus", "db_hapus", "router_muat"]
    assert not (akar / "files").exists() and not (akar / "indeks.jsonl").exists()
    assert (akar / "snapshot" / "j1").exists()
    assert not (staging_aktif / "router" / "contoh-test.rahasia").exists()
    assert not [p.name for p in staging_aktif.iterdir() if p.name.startswith(".hapus-")]
    assert sesi.query(Staging).count() == 0
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Staging dihapus%")).count() == 1


def test_hapus_ditolak_saat_job_berjalan(klien_web, sesi, siap, pb):
    buat_job(sesi, siap.site_id, JobType.staging_tarik)
    assert klien_web.delete(f"/api/sites/{siap.site_id}/staging").status_code == 409
    assert pb.panggilan == []


def test_hapus_galat_pembantu_staging_tetap(klien_web, sesi, siap, pb):
    pb.gagal["db_hapus"] = GalatPembantu("docker", "Perintah Docker di server staging gagal.")
    r = klien_web.delete(f"/api/sites/{siap.site_id}/staging")
    assert r.status_code == 502
    sesi.expire_all()
    assert sesi.query(Staging).count() == 1


def test_dorong_mengantrekan_job(klien_web, sesi, siap, pb):
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/dorong", json={"mode": "semua"}).status_code == 422
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/dorong",
                       json={"mode": "timpa_penuh", "konfirmasi_nama": "Contoh"})
    job = sesi.get(Job, r.json()["job_id"])
    assert (job.tipe, job.payload) == (JobType.staging_dorong, {"mode": "timpa_penuh", "konfirmasi_nama": "Contoh"})


@pytest.mark.parametrize("ubah,pesan", [
    ({"ditarik_pada": None}, dorong.PESAN_BELUM_TARIK),
    ({"tanda_air": None}, dorong.PESAN_BELUM_TARIK),
    ({"aktif": False, "status": StatusStaging.dijeda}, dorong.PESAN_DIJEDA),
    ({"status": StatusStaging.menyalin}, dorong.PESAN_SALINAN_SIBUK),
    ({"status": StatusStaging.berjalan_uji}, dorong.PESAN_SALINAN_SIBUK),
    ({"status": StatusStaging.gagal, "gagal_asal": "salinan"}, dorong.PESAN_SALINAN_GAGAL),
])
def test_dorong_ditolak_sebelum_job_dibuat(klien_web, sesi, siap, pb, ubah, pesan):
    for k, v in ubah.items():
        setattr(siap, k, v)
    sesi.commit()
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/dorong", json={"mode": "hanya_kode"})
    assert r.status_code == 409 and r.json()["detail"] == pesan
    assert _jumlah_job(sesi) == 0


def test_dorong_boleh_saat_gagal_milik_produksi(klien_web, sesi, siap, pb):
    siap.status = StatusStaging.gagal
    siap.gagal_asal = "produksi"
    sesi.commit()
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/dorong", json={"mode": "hanya_kode"})
    assert r.status_code == 200
    sesi.refresh(siap)
    assert siap.gagal_asal == "produksi"


def test_tanda_air_langsung_dari_produksi(klien_web, sesi, siap, pb, monkeypatch):
    prod = ProduksiPalsu()
    prod.tanda_air = {"sumber": {"comments": {"maks_id": 10, "jumlah": 9}}}
    monkeypatch.setattr("wpmgr.web.routes_staging.buat_klien", prod.klien)
    assert klien_web.get(f"/api/sites/{siap.site_id}/staging/tanda-air").json() == {"perubahan": ["7 komentar baru"]}


@pytest.mark.parametrize("kelas,kode", [(STAGING_DITOLAK, 409), (TRANSIENT, 503)])
def test_tanda_air_galat_connector_dipetakan_tanpa_teks_mentah(klien_web, siap, pb, monkeypatch, kelas, kode):
    class Klien:
        def staging_tanda_air(self, *a):
            raise SiteError(kelas, "gagal membuka /var/lib/wpmgr/rahasia")

    monkeypatch.setattr("wpmgr.web.routes_staging.buat_klien", lambda site: Klien())
    r = klien_web.get(f"/api/sites/{siap.site_id}/staging/tanda-air")
    assert r.status_code == kode
    assert "/var/lib" not in r.json()["detail"]


def test_kembalikan_butuh_nama_dan_snapshot_milik_site(klien_web, sesi, siap, pb):
    snap = StagingSnapshot(site_id=siap.site_id, jenis="sebelum_dorong", status="tersedia", ukuran=1, path="x")
    sesi.add(snap)
    sesi.commit()
    dasar = f"/api/sites/{siap.site_id}/staging/kembalikan"
    assert klien_web.post(dasar, json={"snapshot_id": snap.id, "konfirmasi_nama": "contoh"}).status_code == 422
    assert klien_web.post(dasar, json={"snapshot_id": 999999, "konfirmasi_nama": "Contoh"}).status_code == 404
    r = klien_web.post(dasar, json={"snapshot_id": snap.id, "konfirmasi_nama": "Contoh"})
    assert sesi.get(Job, r.json()["job_id"]).tipe == JobType.staging_kembalikan


def test_kembalikan_tanpa_staging_dan_tanpa_gerbang_salinan(klien_web, sesi, site, staging_aktif, pb):
    site.fitur = ["staging"]
    snap = StagingSnapshot(site_id=site.id, jenis="sebelum_dorong", status="tersedia", ukuran=1, path="x")
    sesi.add(snap)
    sesi.commit()
    dasar = f"/api/sites/{site.id}/staging/kembalikan"
    r = klien_web.post(dasar, json={"snapshot_id": snap.id, "konfirmasi_nama": "Contoh"})
    assert r.status_code == 200
    assert sesi.get(Job, r.json()["job_id"]).tipe == JobType.staging_kembalikan
    # Job staging lain (termasuk kembalikan ini) masih tertunda: ditolak.
    r = klien_web.post(dasar, json={"snapshot_id": snap.id, "konfirmasi_nama": "Contoh"})
    assert r.status_code == 409


def test_kembalikan_boleh_saat_salinan_gagal(klien_web, sesi, siap, pb):
    siap.status = StatusStaging.gagal
    siap.gagal_asal = "salinan"
    snap = StagingSnapshot(site_id=siap.site_id, jenis="sebelum_dorong", status="tersedia", ukuran=1, path="x")
    sesi.add(snap)
    sesi.commit()
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/kembalikan",
                       json={"snapshot_id": snap.id, "konfirmasi_nama": "Contoh"})
    assert r.status_code == 200


def test_sandi_baru_ditampilkan_sekali(klien_web, sesi, siap, staging_aktif, pb):
    lama = siap.sandi_hash
    d = klien_web.post(f"/api/sites/{siap.site_id}/staging/sandi").json()
    sesi.refresh(siap)
    assert siap.sandi_hash != lama
    assert bcrypt.checkpw(d["sandi"].encode(), siap.sandi_hash.encode())
    baris = (staging_aktif / "router" / "contoh-test.htpasswd").read_bytes()
    assert baris == f"staging:{siap.sandi_hash}\n".encode()
    assert ("router_muat",) in pb.panggilan
    assert "sandi" not in klien_web.get(f"/api/sites/{siap.site_id}/staging").json()["staging"]
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Kata sandi preview dibuat ulang%")).count() == 1


def test_uji_per_site_dan_dari_halaman_update(klien_web, sesi, siap, pb):
    sesi.add(SitePackage(site_id=siap.site_id, tipe=PackageType.plugin, slug="akismet/akismet.php", nama="Akismet",
                         versi_terpasang="5.2", versi_tersedia="5.3.1", last_scan_at=datetime.now(timezone.utc)))
    sesi.commit()
    r = klien_web.post("/api/staging/uji", json={"items": [
        {"site_id": str(siap.site_id), "tipe": "plugin", "slug": "akismet/akismet.php", "ke_versi": "5.3.1"}]})
    assert r.status_code == 200
    job = sesi.get(Job, r.json()["job_ids"][0])
    assert job.payload["paket"] == [{"tipe": "plugin", "slug": "akismet/akismet.php", "dari": "5.2", "ke": "5.3.1"}]
    job.status = JobStatus.success
    sesi.commit()
    buruk = klien_web.post(f"/api/sites/{siap.site_id}/staging/uji",
                           json={"paket": [{"tipe": "plugin", "slug": "a/a.php", "ke": "1;id"}]})
    assert buruk.status_code == 400 and "a/a.php" in buruk.json()["detail"]
    tanpa = klien_web.post("/api/staging/uji", json={"items": [
        {"site_id": str(uuid.uuid4()), "tipe": "plugin", "slug": "a/a.php", "ke_versi": "1"}]})
    assert tanpa.status_code == 404


def test_uji_menyebut_paket_yang_tidak_sah(klien_web, sesi, siap, pb):
    # Pola slug skrip pembantu (cek_slug) menerima garis bawah, bukan huruf besar.
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/uji", json={"paket": [
        {"tipe": "plugin", "slug": "js_composer/js_composer.php", "ke": "7.0"},
        {"tipe": "plugin", "slug": "LayerSlider/layerslider.php", "ke": "7.0"},
        {"tipe": "theme", "slug": "Divi", "ke": "4.2"},
    ]})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "LayerSlider/layerslider.php" in detail and "Divi" in detail and "js_composer" not in detail
    assert _jumlah_job(sesi) == 0


def test_uji_banyak_semua_atau_tidak_sama_sekali(klien_web, sesi, siap, pb):
    """F16: satu site yang ditolak (di urutan mana pun) membuat tidak satu job pun dibuat."""
    lain = Site(id=uuid.uuid4(), nama="Lain", url="https://lain.test", status=SiteStatus.active,
                secret_terenkripsi=b"x", fitur=["staging"])
    sesi.add(lain)
    sesi.flush()
    st_lain = Staging(site_id=lain.id, nama="lain-test", sandi_hash="x", rahasia_router_terenkripsi=b"x",
                      ditarik_pada=datetime.now(timezone.utc) - timedelta(hours=2),
                      diubah_pada=datetime.now(timezone.utc) - timedelta(hours=1))
    sesi.add(st_lain)
    sesi.commit()
    items = [
        {"site_id": str(siap.site_id), "tipe": "plugin", "slug": "akismet/akismet.php", "ke_versi": "5.3.1"},
        {"site_id": str(lain.id), "tipe": "plugin", "slug": "akismet/akismet.php", "ke_versi": "5.3.1"},
    ]
    r = klien_web.post("/api/staging/uji", json={"items": items})
    assert r.status_code == 409 and "diubah" in r.json()["detail"]
    assert _jumlah_job(sesi) == 0
    buat_job(sesi, lain.id, JobType.staging_tarik)
    r = klien_web.post("/api/staging/uji", json={"items": items, "konfirmasi": True})
    assert r.status_code == 409 and "pekerjaan staging" in r.json()["detail"]
    assert _jumlah_job(sesi) == 1
    sesi.query(Job).delete()
    sesi.commit()
    r = klien_web.post("/api/staging/uji", json={"items": items, "konfirmasi": True})
    assert r.status_code == 200 and len(r.json()["job_ids"]) == 2


def test_lencana_uji_di_daftar_paket(klien_web, sesi, siap):
    sesi.add(SitePackage(site_id=siap.site_id, tipe=PackageType.plugin, slug="akismet/akismet.php", nama="Akismet",
                         versi_terpasang="5.2", versi_tersedia="5.3.1", last_scan_at=datetime.now(timezone.utc)))
    sesi.add(StagingUji(site_id=siap.site_id, paket=[{"tipe": "plugin", "slug": "akismet/akismet.php", "dari": "5.2",
                                                      "ke": "5.3.1"}], hasil="gagal",
                        pemeriksaan={"alasan": ["/ membalas HTTP 500"]}))
    sesi.commit()
    paket = klien_web.get("/api/packages").json()[0]
    assert paket["uji"]["hasil"] == "gagal"
    assert paket["uji"]["alasan"] == "/ membalas HTTP 500"


def test_batal(klien_web, sesi, siap):
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/batal").status_code == 409
    buat_job(sesi, siap.site_id, JobType.staging_tarik)
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/batal").status_code == 200
    sesi.refresh(siap)
    assert siap.batal_diminta_pada is not None


def test_sso_staging(klien_web, sesi, siap):
    url = klien_web.get(f"/api/sites/{siap.site_id}/staging/sso").json()["url"]
    assert url.startswith("https://contoh-test.staging.contoh.id/__wpmgr_masuk?e=")
    assert "&m=" in url and "&sso=" in url
    sesi.refresh(siap)
    assert siap.dibuka_pada is not None
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("SSO staging dibuka%")).count() == 1


def _token_sso(url: str) -> str:
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(url).query)["sso"][0]


def test_sso_staging_memakai_secret_staging_bukan_produksi(klien_web, sesi, siap):
    """R25: token SSO staging ditandatangani secret staging; secret produksi tidak berlaku di sana."""
    from wpmgr.crypto import enkripsi_secret
    from wpmgr.sso import TokenTidakValid, baca_token, buat_token

    rahasia = "a1" * 32
    siap.secret_connector_terenkripsi = enkripsi_secret(rahasia)
    sesi.commit()
    token = _token_sso(klien_web.get(f"/api/sites/{siap.site_id}/staging/sso").json()["url"])
    assert baca_token(rahasia, token)["site_id"] == str(siap.site_id)
    with pytest.raises(TokenTidakValid):
        baca_token("f" * 64, token)
    # Kebalikannya: token produksi (secret "f"*64) ditolak connector staging yang memegang secret staging.
    with pytest.raises(TokenTidakValid):
        baca_token(rahasia, buat_token("f" * 64, str(siap.site_id)))


def test_sso_staging_tanpa_secret_staging_minta_segarkan(klien_web, sesi, siap):
    siap.secret_connector_terenkripsi = None
    sesi.commit()
    r = klien_web.get(f"/api/sites/{siap.site_id}/staging/sso")
    assert r.status_code == 409 and "Segarkan" in r.json()["detail"]
    sesi.refresh(siap)
    assert siap.dibuka_pada is None


def test_sso_ditolak_saat_dijeda(klien_web, sesi, siap):
    siap.aktif = False
    siap.status = StatusStaging.dijeda
    sesi.commit()
    r = klien_web.get(f"/api/sites/{siap.site_id}/staging/sso")
    assert r.status_code == 409 and r.json()["detail"] == "Staging sedang dijeda; jalankan dulu."
    sesi.refresh(siap)
    assert siap.dibuka_pada is None


def test_sandi_ditolak_saat_job_staging_aktif(klien_web, sesi, siap, staging_aktif, pb):
    lama = siap.sandi_hash
    buat_job(sesi, siap.site_id, JobType.staging_tarik)
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/sandi")
    assert r.status_code == 409 and "pekerjaan staging" in r.json()["detail"]
    sesi.refresh(siap)
    assert siap.sandi_hash == lama and pb.panggilan == []
    assert not (staging_aktif / "router" / "contoh-test.htpasswd").exists()


def test_uji_banyak_dibatasi_jumlah_item_dan_site(klien_web, sesi, siap, pb):
    satu = {"site_id": str(siap.site_id), "tipe": "plugin", "slug": "a/a.php", "ke_versi": "1.0"}
    r = klien_web.post("/api/staging/uji", json={"items": [satu] * 201})
    assert r.status_code == 400 and "200" in r.json()["detail"]
    banyak = [dict(satu, site_id=str(uuid.uuid4())) for _ in range(51)]
    r = klien_web.post("/api/staging/uji", json={"items": banyak})
    assert r.status_code == 400 and "50" in r.json()["detail"]
    assert _jumlah_job(sesi) == 0


def test_email_mailpit_disaring_per_staging(klien_web, siap, monkeypatch):
    diminta = []

    def mailpit(r):
        diminta.append(r)
        if r.url.path == "/api/v1/search":
            return httpx.Response(200, json={"messages": [
                {"ID": "abc123", "From": {"Address": "<b>x@y.id</b>"}, "To": [{"Address": "c@d.id"}],
                 "Subject": "Pesanan \x00baru", "Created": "2026-09-26T01:02:03Z", "Tags": ["contoh-test"],
                 "Snippet": "halo"},
                {"ID": "../jahat", "From": {}, "To": [], "Subject": "x", "Tags": ["contoh-test"]},
                # Pencarian Mailpit tidak dipercaya: tag milik staging lain disaring ulang.
                {"ID": "lain77", "From": {}, "To": [], "Subject": "rahasia", "Tags": ["staging-lain"]},
                {"ID": "tanpa1", "From": {}, "To": [], "Subject": "rahasia", "Tags": "contoh-test"},
            ], "total": 4})
        if r.url.path == "/api/v1/message/abc123":
            return httpx.Response(200, json={"ID": "abc123", "Subject": "Pesanan", "From": {"Address": "x@y.id"},
                                             "To": [{"Address": "c@d.id"}], "Date": "2026-09-26T01:02:03Z",
                                             "Text": "isi", "HTML": "<script>", "Tags": ["contoh-test"]})
        if r.url.path == "/api/v1/message/lain99":
            return httpx.Response(200, json={"ID": "lain99", "Tags": ["staging-lain"], "Text": "rahasia"})
        return httpx.Response(404)

    monkeypatch.setattr(umum, "buat_http", lambda: httpx.Client(transport=httpx.MockTransport(mailpit)))
    daftar = klien_web.get(f"/api/sites/{siap.site_id}/staging/email").json()
    assert daftar == [{"id": "abc123", "dari": "<b>x@y.id</b>", "ke": ["c@d.id"], "subjek": "Pesanan baru",
                       "waktu": "2026-09-26T01:02:03Z", "cuplikan": "halo"}]
    assert diminta[0].url.params["query"] == 'tag:"contoh-test"'
    isi = klien_web.get(f"/api/sites/{siap.site_id}/staging/email/abc123").json()
    assert isi["teks"] == "isi" and "html" not in isi
    assert klien_web.get(f"/api/sites/{siap.site_id}/staging/email/lain99").status_code == 404
    assert klien_web.get(f"/api/sites/{siap.site_id}/staging/email/..%2Fx").status_code == 404


def test_email_mailpit_dibatasi_ukurannya(klien_web, siap, monkeypatch):
    besar = {"messages": [{"ID": "a1", "Tags": ["contoh-test"], "Snippet": "x" * 5000}]}
    monkeypatch.setattr("wpmgr.web.routes_staging.BATAS_EMAIL_BYTE", 1000)
    monkeypatch.setattr(umum, "buat_http", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=besar))))
    r = klien_web.get(f"/api/sites/{siap.site_id}/staging/email")
    assert r.status_code == 502 and "terlalu besar" in r.json()["detail"]


def test_email_mailpit_punya_tenggat_total(klien_web, siap, monkeypatch):
    lepas = threading.Event()

    def lambat(r):
        # Mailpit yang tidak menjawab: ditahan sampai test selesai.
        lepas.wait(10)
        return httpx.Response(200, json={"messages": []})

    monkeypatch.setattr("wpmgr.web.routes_staging.TENGGAT_EMAIL", 0.5)
    monkeypatch.setattr(umum, "buat_http", lambda: httpx.Client(transport=httpx.MockTransport(lambat)))
    mulai = time.monotonic()
    try:
        r = klien_web.get(f"/api/sites/{siap.site_id}/staging/email")
    finally:
        lepas.set()
    assert time.monotonic() - mulai < 5
    assert r.status_code == 502 and r.json()["detail"] == "Kotak email staging tidak dapat dibaca."


def test_daftar_uji_dan_snapshot_dibatasi(klien_web, sesi, siap):
    for i in range(60):
        sesi.add(StagingSnapshot(site_id=siap.site_id, jenis="sebelum_dorong", status="tersedia", ukuran=i, path=f"s{i}"))
        sesi.add(StagingUji(site_id=siap.site_id, paket=[], hasil="lolos", pemeriksaan={"alasan": []}))
    sesi.commit()
    assert len(klien_web.get(f"/api/sites/{siap.site_id}/staging/snapshot").json()) == 50
    assert len(klien_web.get(f"/api/sites/{siap.site_id}/staging/uji").json()) == 50


# ---- kunci baris: sites (NO KEY UPDATE) lalu staging (FOR UPDATE) -------------------

RUTE_TERKUNCI = [
    ("POST", "/api/sites/{id}/staging", {"konfirmasi": True}),
    ("POST", "/api/sites/{id}/staging/jeda", None),
    ("DELETE", "/api/sites/{id}/staging", None),
    ("POST", "/api/sites/{id}/staging/dorong", {"mode": "hanya_kode"}),
    ("POST", "/api/sites/{id}/staging/uji", {"paket": [{"tipe": "plugin", "slug": "a/a.php", "ke": "1.0"}],
                                             "konfirmasi": True}),
    ("POST", "/api/sites/{id}/staging/sandi", None),
    ("POST", "/api/sites/{id}/staging/kembalikan", {"snapshot_id": "SNAP", "konfirmasi_nama": "Contoh"}),
    ("POST", "/api/staging/uji", {"items": [{"site_id": "SITE", "tipe": "plugin", "slug": "a/a.php",
                                             "ke_versi": "1.0"}], "konfirmasi": True}),
]


def _isi_body(body, site_id, snap_id):
    """Ganti penanda SITE/SNAP di body parametrize dengan id sungguhan."""
    if isinstance(body, dict):
        return {k: _isi_body(v, site_id, snap_id) for k, v in body.items()}
    if isinstance(body, list):
        return [_isi_body(v, site_id, snap_id) for v in body]
    return {"SITE": str(site_id), "SNAP": snap_id}.get(body, body) if isinstance(body, str) else body


@pytest.mark.parametrize("sql", [
    "SELECT id FROM sites WHERE id = :site FOR NO KEY UPDATE",
    "SELECT id FROM staging WHERE site_id = :site FOR UPDATE",
])
@pytest.mark.parametrize("metode,path,body", RUTE_TERKUNCI)
def test_rute_menunggu_kunci_lalu_memeriksa_ulang(klien_web, engine, sesi, siap, pb, sql, metode, path, body):
    """Route antre dan hapus/jeda/sandi memeriksa "sibuk" di bawah kunci yang sama dengan cron (F29)."""
    snap = StagingSnapshot(site_id=siap.site_id, jenis="sebelum_dorong", status="tersedia", ukuran=1, path="x")
    sesi.add(snap)
    sesi.commit()
    body = _isi_body(body, siap.site_id, snap.id)
    lain = sessionmaker(bind=engine, future=True)()
    lain.execute(text(sql), {"site": siap.site_id})
    hasil = []
    t = threading.Thread(target=lambda: hasil.append(
        klien_web.request(metode, path.format(id=siap.site_id), json=body)))
    t.start()
    try:
        t.join(1.0)
        menunggu = t.is_alive()
        # Commit melepas kunci; job ini harus terlihat oleh pemeriksaan route.
        buat_job(lain, siap.site_id, JobType.staging_kembalikan, {"snapshot_id": 1})
    finally:
        lain.rollback()
        lain.close()
        t.join(10)
    assert menunggu, "route tidak menunggu kunci baris"
    assert hasil[0].status_code == 409, hasil[0].text
    assert pb.nama_panggilan() == []


# ---- hapus site membersihkan staging ------------------------------------------------


def test_hapus_site_menghapus_staging_dulu(klien_web, sesi, siap, staging_aktif, pb):
    (staging_aktif / "router").mkdir(parents=True)
    (staging_aktif / "router" / "contoh-test.htpasswd").write_bytes(b"x")
    r = klien_web.delete(f"/api/sites/{siap.site_id}")
    assert r.status_code == 200
    assert pb.nama_panggilan() == ["hapus", "db_hapus", "router_muat"]
    assert not (staging_aktif / "router" / "contoh-test.htpasswd").exists()
    sesi.expire_all()
    assert sesi.query(Site).count() == 0


def test_hapus_site_ditolak_bila_pembantu_gagal(klien_web, sesi, siap, pb):
    pb.gagal["hapus"] = GalatPembantu("docker", "Perintah Docker di server staging gagal.")
    r = klien_web.delete(f"/api/sites/{siap.site_id}")
    assert r.status_code == 502 and r.json()["detail"] == "Perintah Docker di server staging gagal."
    sesi.expire_all()
    assert sesi.query(Site).count() == 1 and sesi.query(Staging).count() == 1


def test_hapus_site_ditolak_saat_job_staging_berjalan(klien_web, sesi, siap, pb):
    job = buat_job(sesi, siap.site_id, JobType.staging_tarik)
    job.status = JobStatus.running
    sesi.commit()
    assert klien_web.delete(f"/api/sites/{siap.site_id}").status_code == 409
    assert pb.panggilan == []


def test_hapus_site_tanpa_staging_tidak_memanggil_pembantu(klien_web, sesi, site, staging_aktif, pb):
    assert klien_web.delete(f"/api/sites/{site.id}").status_code == 200
    assert pb.panggilan == []


def test_hapus_site_ditolak_saat_dorong_tertunda_sudah_berjalan(klien_web, sesi, siap, pb):
    """Dorong yang menunggu percobaan ulang sesudah mulai bisa sudah menyentuh produksi."""
    job = buat_job(sesi, siap.site_id, JobType.staging_dorong, {"mode": "hanya_kode"})
    job.payload = {"mode": "hanya_kode", "kemajuan": {"tahap_dorong": "unggah"}}
    sesi.commit()
    r = klien_web.delete(f"/api/sites/{siap.site_id}")
    assert r.status_code == 409 and "titik kembali" in r.json()["detail"]
    assert pb.panggilan == []
    sesi.expire_all()
    assert sesi.query(Site).count() == 1


def test_hapus_site_ditolak_saat_kembalikan_berjalan_tanpa_staging(klien_web, sesi, site, staging_aktif, pb):
    job = buat_job(sesi, site.id, JobType.staging_kembalikan, {"snapshot_id": 1})
    job.status = JobStatus.running
    sesi.commit()
    r = klien_web.delete(f"/api/sites/{site.id}")
    assert r.status_code == 409
    sesi.expire_all()
    assert sesi.query(Site).count() == 1


def test_hapus_site_dorong_tertunda_tanpa_kemajuan_ikut_terhapus(klien_web, sesi, siap, pb):
    buat_job(sesi, siap.site_id, JobType.staging_dorong, {"mode": "hanya_kode"})
    assert klien_web.delete(f"/api/sites/{siap.site_id}").status_code == 200
    assert _jumlah_job(sesi) == 0 and sesi.query(Site).count() == 0


def test_hapus_site_ditolak_saat_snapshot_dibutuhkan_rekonsiliasi(klien_web, sesi, site, staging_aktif, pb):
    """Dorongan lama yang belum terbukti bersih: snapshot-nya satu-satunya titik kembali produksi."""
    job = buat_job(sesi, site.id, JobType.staging_dorong, {"mode": "hanya_kode"})
    job.status = JobStatus.failed
    job.payload = {"mode": "hanya_kode", "kemajuan": {"unggah_mulai": True, "tahap_dorong": "terapkan"}}
    sesi.commit()
    r = klien_web.delete(f"/api/sites/{site.id}")
    assert r.status_code == 409 and "titik kembali" in r.json()["detail"]
    sesi.expire_all()
    assert sesi.query(Site).count() == 1


def test_hapus_staging_baris_dihapus_sebelum_berkas(klien_web, sesi, engine, siap, staging_aktif, pb, monkeypatch):
    """Berkas baru dipindah ke nisan sesudah penghapusan baris ter-commit: tidak pernah ada baris tanpa berkas."""
    from wpmgr.web import routes_staging

    (staging_aktif / str(siap.site_id) / "files").mkdir(parents=True)
    asli = routes_staging._nisan_selain_snapshot
    terlihat = []

    def periksa(akar, nama, site_id):
        with sessionmaker(bind=engine, future=True)() as s:
            terlihat.append(s.scalar(select(func.count()).select_from(Staging)))
        return asli(akar, nama, site_id)

    monkeypatch.setattr(routes_staging, "_nisan_selain_snapshot", periksa)
    assert klien_web.delete(f"/api/sites/{siap.site_id}/staging").status_code == 200
    assert terlihat == [0]
    assert not (staging_aktif / str(siap.site_id) / "files").exists()


def test_jalan_memeriksa_ulang_di_bawah_kunci(klien_web, engine, sesi, siap, pb):
    """Staging dijalankan pemegang kunci: route melihatnya sesudah kunci lepas dan tidak memanggil pembantu."""
    siap.aktif = False
    siap.status = StatusStaging.dijeda
    sesi.commit()
    lain = sessionmaker(bind=engine, future=True)()
    lain.execute(text("SELECT id FROM sites WHERE id = :site FOR NO KEY UPDATE"), {"site": siap.site_id})
    hasil = []
    t = threading.Thread(target=lambda: hasil.append(klien_web.post(f"/api/sites/{siap.site_id}/staging/jalan")))
    t.start()
    try:
        t.join(1.0)
        menunggu = t.is_alive()
        lain.execute(text("UPDATE staging SET aktif = true, status = 'siap' WHERE site_id = :site"),
                     {"site": siap.site_id})
        lain.commit()
    finally:
        lain.rollback()
        lain.close()
        t.join(10)
    assert menunggu, "jalan tidak menunggu kunci baris"
    assert hasil[0].status_code == 200 and pb.nama_panggilan() == []


@pytest.mark.parametrize("metode,path", [
    ("GET", "/api/sites/{id}/staging/sso"),
    ("POST", "/api/sites/{id}/staging/batal"),
])
def test_tulis_lain_menunggu_kunci_staging(klien_web, engine, sesi, siap, metode, path):
    """Kunci baris sites (bukan hanya staging): tanpa `_kunci`, UPDATE staging tidak tertahan kunci ini."""
    buat_job(sesi, siap.site_id, JobType.staging_tarik)
    lain = sessionmaker(bind=engine, future=True)()
    lain.execute(text("SELECT id FROM sites WHERE id = :site FOR NO KEY UPDATE"), {"site": siap.site_id})
    hasil = []
    t = threading.Thread(target=lambda: hasil.append(klien_web.request(metode, path.format(id=siap.site_id))))
    t.start()
    try:
        t.join(1.0)
        menunggu = t.is_alive()
    finally:
        lain.rollback()
        lain.close()
        t.join(10)
    assert menunggu, "route tidak menunggu kunci baris staging"
    assert hasil[0].status_code == 200, hasil[0].text


@pytest.mark.parametrize("sql", [
    "SELECT id FROM sites WHERE id = :site FOR NO KEY UPDATE",
    "SELECT id FROM staging WHERE site_id = :site FOR UPDATE",
])
def test_get_staging_tidak_menunggu_kunci(klien_web, engine, sesi, siap, staging_aktif, sql):
    """GET status tidak pernah tertahan (mis. cron memegang kunci selama `pb.jeda`); penanda diubah dilewati."""
    penanda = staging_aktif / str(siap.site_id) / "log" / "diubah"
    penanda.parent.mkdir(parents=True)
    penanda.write_bytes(str(int(time.time())).encode())
    lain = sessionmaker(bind=engine, future=True)()
    lain.execute(text(sql), {"site": siap.site_id})
    hasil = []
    # Di utas terpisah: bila route menunggu kunci, test gagal alih-alih macet.
    t = threading.Thread(target=lambda: hasil.append(klien_web.get(f"/api/sites/{siap.site_id}/staging")))
    t.start()
    try:
        t.join(5.0)
        tertahan = t.is_alive()
    finally:
        lain.rollback()
        lain.close()
        t.join(10)
    assert not tertahan, "GET staging menunggu kunci baris"
    r = hasil[0]
    assert r.status_code == 200 and r.json()["staging"]["nama"] == "contoh-test"
    sesi.expire_all()
    assert sesi.get(Staging, siap.id).diubah_pada is None
    # Tanpa kunci lain, penanda ditulis seperti biasa.
    klien_web.get(f"/api/sites/{siap.site_id}/staging")
    sesi.expire_all()
    assert sesi.get(Staging, siap.id).diubah_pada is not None


def _sisip_di_transaksi_kedua(monkeypatch, engine, siap, aksi):
    """Jalankan `aksi(sesi_lain)` tepat sebelum hapus mengambil kunci sites untuk kedua kalinya."""
    from wpmgr.web import routes_staging

    asli = routes_staging._kunci_site
    panggilan = []

    def kunci(sesi, site_id):
        panggilan.append(site_id)
        if len(panggilan) == 2:
            with sessionmaker(bind=engine, future=True)() as s:
                aksi(s)
        return asli(sesi, site_id)

    monkeypatch.setattr(routes_staging, "_kunci_site", kunci)


@pytest.mark.parametrize("muncul", ["staging", "job"])
def test_hapus_staging_berkas_dilewati_bila_staging_atau_job_baru_muncul(
        klien_web, sesi, engine, siap, staging_aktif, pb, monkeypatch, muncul):
    files = staging_aktif / str(siap.site_id) / "files"
    files.mkdir(parents=True)

    def aksi(s):
        if muncul == "staging":
            s.add(Staging(site_id=siap.site_id, nama="contoh-baru"))
            s.commit()
        else:
            buat_job(s, siap.site_id, JobType.staging_kembalikan, {"snapshot_id": 1})

    _sisip_di_transaksi_kedua(monkeypatch, engine, siap, aksi)
    assert klien_web.delete(f"/api/sites/{siap.site_id}/staging").status_code == 200
    assert files.exists()
    assert not [p.name for p in staging_aktif.iterdir() if p.name.startswith(".hapus-")]


def test_hapus_staging_galat_transaksi_kedua_tetap_sukses(klien_web, sesi, siap, staging_aktif, pb, monkeypatch,
                                                           caplog):
    from wpmgr.web import routes_staging

    files = staging_aktif / str(siap.site_id) / "files"
    files.mkdir(parents=True)

    def gagal(*a):
        raise OSError("disk")

    monkeypatch.setattr(routes_staging, "_nisan_selain_snapshot", gagal)
    r = klien_web.delete(f"/api/sites/{siap.site_id}/staging")
    assert r.status_code == 200
    sesi.expire_all()
    assert sesi.query(Staging).count() == 0 and files.exists()
    assert any("cron" in c.getMessage() for c in caplog.records if c.levelname == "ERROR")
