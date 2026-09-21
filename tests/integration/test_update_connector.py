import hashlib
import json

import httpx
import pytest

from wpmgr.connector_paket import bangun_paket, sumber_bawaan
from wpmgr.jobs.monitoring import tangani_update_connector
from wpmgr.jobs.queue import antrekan_jika_belum, buat_job
from wpmgr.models import ActivityLog, Job, JobStatus, JobType
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration


def klien_mencatat(tampung, balasan):
    def handler(request):
        tampung.append(request)
        return httpx.Response(200, json=balasan)

    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_handler_mengirim_paket_dan_mencatat_versi(sesi, site, var_sementara, pengguna_uji):
    manifest = bangun_paket(sumber_bawaan(), var_sementara / "connector")
    job = buat_job(sesi, site.id, JobType.update_connector, {"versi": manifest["versi"]},
                   dibuat_oleh=pengguna_uji.id)
    tampung = []
    hasil = tangani_update_connector(sesi, job, klien_mencatat(
        tampung, {"ok": True, "versi_sebelum": "1.0.0", "versi_sesudah": manifest["versi"],
                  "pesan": ""}))

    body = json.loads(tampung[0].content)
    assert body["versi"] == manifest["versi"]
    assert body["sha256"] == manifest["sha256"]
    assert tampung[0].url.path == "/wp-json/wpmgr/v1/self-update"
    assert hasil["versi_sesudah"] == manifest["versi"]
    sesi.refresh(site)
    assert site.connector_version == manifest["versi"]
    log = sesi.query(ActivityLog).filter_by(site_id=site.id, level="info").one()
    assert "1.0.0" in log.pesan and manifest["versi"] in log.pesan
    assert log.detail["email"] == "a@b.test"
    # Verify dijadwalkan supaya daftar fitur versi baru segera tercatat.
    assert sesi.query(Job).filter_by(site_id=site.id, tipe=JobType.verify_site).count() == 1


def test_handler_tanpa_paket_melempar_kesalahan_dashboard(sesi, site, var_sementara):
    job = buat_job(sesi, site.id, JobType.update_connector, {"versi": "9.9.9"})
    with pytest.raises(RuntimeError, match="build-connector"):
        tangani_update_connector(sesi, job, klien_mencatat([], {}))


def test_handler_menolak_zip_yang_tidak_cocok_dengan_manifest(sesi, site, var_sementara):
    bangun_paket(sumber_bawaan(), var_sementara / "connector")
    (var_sementara / "connector" / "wp-manager-connector.zip").write_bytes(b"diubah")
    job = buat_job(sesi, site.id, JobType.update_connector, {"versi": "x"})
    with pytest.raises(RuntimeError, match="tidak cocok"):
        tangani_update_connector(sesi, job, klien_mencatat([], {}))


def test_antrekan_jika_belum_tidak_menggandakan(sesi, site):
    assert antrekan_jika_belum(sesi, site.id, JobType.verify_site) is not None
    assert antrekan_jika_belum(sesi, site.id, JobType.verify_site) is None
    job = antrekan_jika_belum(sesi, site.id, JobType.collect_events, max_attempts=1)
    assert job.max_attempts == 1


def test_api_semua_atau_tidak_sama_sekali(klien_web, sesi, site, var_sementara):
    from wpmgr.models import Site, SiteStatus

    bangun_paket(sumber_bawaan(), var_sementara / "connector")
    site.fitur = ["self_update"]
    lama = Site(nama="Lama", url="https://lama.test", status=SiteStatus.active,
                secret_terenkripsi=b"x", fitur=[])
    sesi.add(lama)
    sesi.commit()

    r = klien_web.post("/api/jobs/update-connector",
                       json={"site_ids": [str(site.id), str(lama.id)]})
    assert r.status_code == 409
    assert "Lama" in r.json()["detail"]
    assert sesi.query(Job).filter_by(tipe=JobType.update_connector).count() == 0


def test_api_membuat_job_dengan_versi_manifest(klien_web, sesi, site, var_sementara):
    manifest = bangun_paket(sumber_bawaan(), var_sementara / "connector")
    site.fitur = ["self_update"]
    sesi.commit()
    r = klien_web.post("/api/jobs/update-connector", json={"site_ids": [str(site.id)]})
    assert r.status_code == 200
    job = sesi.get(Job, r.json()["job_ids"][0])
    assert job.tipe == JobType.update_connector
    assert job.payload == {"versi": manifest["versi"]}
    assert job.status == JobStatus.pending


def test_api_409_bila_paket_belum_dibangun(klien_web, sesi, site, var_sementara):
    site.fitur = ["self_update"]
    sesi.commit()
    r = klien_web.post("/api/jobs/update-connector", json={"site_ids": [str(site.id)]})
    assert r.status_code == 409
    assert "build-connector" in r.json()["detail"]


def test_api_sites_menandai_connector_usang(klien_web, sesi, site, var_sementara):
    bangun_paket(sumber_bawaan(), var_sementara / "connector")
    site.connector_version = "1.0.0"
    site.fitur = ["self_update"]
    sesi.commit()
    baris = next(b for b in klien_web.get("/api/sites").json() if b["id"] == str(site.id))
    assert baris["connector_version"] == "1.0.0"
    assert baris["connector_usang"] is True
    assert baris["bisa_self_update"] is True


def test_sha_manifest_konsisten(var_sementara):
    manifest = bangun_paket(sumber_bawaan(), var_sementara / "connector")
    isi = (var_sementara / "connector" / "wp-manager-connector.zip").read_bytes()
    assert hashlib.sha256(isi).hexdigest() == manifest["sha256"]
