import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from staging_palsu import PembantuHostingPalsu

from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting import pindah
from wpmgr.hosting.dns import HasilDns
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    HostingVps,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
    Staging,
    StatusHosting,
)
from wpmgr.staging import dorong, umum
from wpmgr.staging.pembantu import GalatPembantu
from wpmgr.web import routes_hosting, routes_staging
from wpmgr.web.routes_staging import ringkas_kemajuan

pytestmark = pytest.mark.integration

IP_LAMA = "93.184.216.34"
ROUTE = [
    ("GET", "/api/sites/{id}/hosting"), ("POST", "/api/sites/{id}/hosting"), ("DELETE", "/api/sites/{id}/hosting"),
    ("POST", "/api/sites/{id}/hosting/tarik"), ("POST", "/api/sites/{id}/hosting/lanjut-dns"),
    ("POST", "/api/sites/{id}/hosting/kembali-pratinjau"), ("POST", "/api/sites/{id}/hosting/aktifkan"),
    ("POST", "/api/sites/{id}/hosting/sandi"), ("POST", "/api/sites/{id}/hosting/batal"),
]


@pytest.fixture
def pb(hosting_aktif, monkeypatch):
    palsu = PembantuHostingPalsu(hosting_aktif)
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


@pytest.fixture
def dns_lama(monkeypatch):
    """DNS publik tiruan untuk Pindahkan: A domain ke hosting lama, www ada.

    Route memakai `ip_lama_dan_cdn` (bukan `ip_lama_dari_dns`) supaya CDN
    Hostinger bisa dibedakan dari DNS yang tidak menjawab (carry Task 9).
    """
    keadaan = {"ip": IP_LAMA, "cdn": False, "www": True, "www_n": 0}

    def ada_www(domain, **kw):
        keadaan["www_n"] += 1
        return keadaan["www"]

    monkeypatch.setattr(dns_mod, "ip_lama_dan_cdn", lambda domain, **kw: (keadaan["ip"], keadaan["cdn"]))
    monkeypatch.setattr(dns_mod, "ada_www", ada_www)
    return keadaan


@pytest.fixture
def cek_dns(monkeypatch):
    keadaan = {"ok": True, "n": 0}

    def periksa(h, **kw):
        keadaan["n"] += 1
        item = [{"nama": "@", "jenis": "A", "terlihat": ["169.58.91.181"], "harus": ["169.58.91.181"],
                 "ok": keadaan["ok"], "kode": "cocok" if keadaan["ok"] else "kurang"}]
        return HasilDns(ok=keadaan["ok"], dicek=datetime.now(timezone.utc).isoformat(), nama=item)

    monkeypatch.setattr(dns_mod, "periksa_dns", periksa)
    return keadaan


@pytest.fixture
def site_baru(sesi, site, hosting_aktif):
    site.url = "https://www.toko.co.id"
    site.fitur = ["self_update", "staging"]
    sesi.commit()
    return site


def _status(sesi, h, status, **lain):
    h.status = status
    for k, v in lain.items():
        setattr(h, k, v)
    sesi.commit()


def _h(sesi, site_id):
    return sesi.scalar(select(HostingVps).where(HostingVps.site_id == site_id)
                       .execution_options(populate_existing=True))


@pytest.mark.parametrize("metode,path", ROUTE)
def test_anonim_ditolak(engine, hosting_aktif, metode, path):
    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), base_url="https://testserver")
    assert anon.request(metode, path.format(id=uuid.uuid4()), json={}).status_code == 401


def test_fitur_mati(klien_web, site, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_HOSTING_IPV4", raising=False)
    get_settings.cache_clear()
    assert klien_web.get(f"/api/sites/{site.id}/hosting").json() == {"aktif_fitur": False}
    assert klien_web.post(f"/api/sites/{site.id}/hosting", json={}).status_code == 404
    assert klien_web.post(f"/api/sites/{site.id}/hosting/aktifkan", json={}).status_code == 404


# ---- Pindahkan ---------------------------------------------------------------------------


def test_pindahkan_membuat_baris_sandi_sekali_dan_job(klien_web, sesi, site_baru, pb, dns_lama):
    r = klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["pengguna"] == "pratinjau" and len(d["sandi"]) >= 16
    h = _h(sesi, site_baru.id)
    assert (h.nama, h.domain, h.dengan_www, h.ip_lama, h.status) == (
        "toko-co-id", "toko.co.id", True, IP_LAMA, StatusHosting.menyalin)
    assert bcrypt.checkpw(d["sandi"].encode(), h.sandi_hash.encode())
    job = sesi.get(Job, d["job_id"])
    assert job.tipe == JobType.pindah_tarik and job.dibuat_oleh is not None
    data = klien_web.get(f"/api/sites/{site_baru.id}/hosting").json()
    assert data["hosting"]["url_pratinjau"] == "https://vps-toko-co-id.staging.contoh.id"
    assert data["hosting"]["status_teks"] == "Menyalin ke VPS"
    assert "sandi" not in str(data["hosting"]) and "sandi_hash" not in data["hosting"]
    assert data["job"]["tipe"] == "pindah_tarik"
    assert [i["jenis"] for i in data["hosting"]["instruksi"]] == ["A", "AAAA", "A", "AAAA"]
    assert klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={}).status_code == 409


def test_pindahkan_ditolak_tanpa_izin_ip_lama_atau_domain_staging(klien_web, sesi, site_baru, pb, dns_lama):
    site_baru.fitur = ["self_update"]
    sesi.commit()
    r = klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={})
    assert r.status_code == 409 and "Izinkan staging" in r.json()["detail"]
    site_baru.fitur = ["staging"]
    sesi.commit()
    dns_lama["ip"] = None
    r = klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={})
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_IP_LAMA
    dns_lama["ip"] = IP_LAMA
    site_baru.url = "https://x.staging.contoh.id"
    sesi.commit()
    r = klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={})
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_DOMAIN
    assert sesi.scalar(select(HostingVps.id)) is None and sesi.query(Job).count() == 0


def test_pindahkan_lewat_cdn_ditolak_dengan_pesan_cdn(klien_web, sesi, site_baru, pb, dns_lama):
    """Carry Task 9: CDN Hostinger dibedakan dari DNS yang tidak menjawab."""
    dns_lama.update(ip=None, cdn=True)
    r = klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={})
    assert r.status_code == 409 and r.json()["detail"] == dns_mod.PESAN_CDN_IP_LAMA
    assert sesi.scalar(select(HostingVps.id)) is None and sesi.query(Job).count() == 0


def test_pindahkan_www_tak_terjawab_dns_ditolak(klien_web, sesi, site, hosting_aktif, pb, dns_lama):
    """Carry Task 9: ada_www None (tidak ada resolver yang menjawab) bukan "tanpa www"."""
    site.url = "https://toko.co.id"
    site.fitur = ["staging"]
    sesi.commit()
    dns_lama["www"] = None
    r = klien_web.post(f"/api/sites/{site.id}/hosting", json={})
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_WWW_DNS
    assert sesi.scalar(select(HostingVps.id)) is None and sesi.query(Job).count() == 0
    dns_lama["www"] = False
    r = klien_web.post(f"/api/sites/{site.id}/hosting", json={})
    assert r.status_code == 200
    assert _h(sesi, site.id).dengan_www is False


def test_pindahkan_url_www_tidak_bertanya_www(klien_web, sesi, site_baru, pb, dns_lama):
    dns_lama["www"] = None
    assert klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={}).status_code == 200
    assert dns_lama["www_n"] == 0 and _h(sesi, site_baru.id).dengan_www is True


def test_nama_hosting_menghindari_label_staging_vps(klien_web, sesi, site_baru, pb, dns_lama):
    lain = Site(id=uuid.uuid4(), nama="L", url="https://lain.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(lain)
    sesi.flush()
    sesi.add(Staging(site_id=lain.id, nama="vps-toko-co-id"))
    sesi.commit()
    klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={})
    assert _h(sesi, site_baru.id).nama == "toko-co-id-2"


def test_nama_staging_melewati_label_vps_milik_hosting(sesi, site_hosting):
    assert routes_staging._nama_unik(sesi, "vps-toko-co-id") == "vps-toko-co-id-2"
    assert routes_staging._nama_unik(sesi, "toko-co-id") == "toko-co-id"


# ---- transisi status -----------------------------------------------------------------------


def test_salin_ulang_dan_aturannya(klien_web, sesi, site_hosting, pb):
    url = f"/api/sites/{site_hosting.site_id}/hosting/tarik"
    _status(sesi, site_hosting, StatusHosting.menyalin)
    assert klien_web.post(url).status_code == 409
    _status(sesi, site_hosting, StatusHosting.pratinjau)
    r = klien_web.post(url)
    assert r.status_code == 200 and sesi.get(Job, r.json()["job_id"]).tipe == JobType.pindah_tarik
    assert klien_web.post(url).status_code == 409
    sesi.query(Job).delete()
    _status(sesi, site_hosting, StatusHosting.gagal, gagal_asal="salinan")
    assert klien_web.post(url).status_code == 200
    sesi.query(Job).delete()
    _status(sesi, site_hosting, StatusHosting.gagal, gagal_asal="produksi",
            dilayani_vps_pada=datetime.now(timezone.utc))
    r = klien_web.post(url)
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_SUDAH_DILAYANI


def test_lanjut_dns_dan_kembali_pratinjau(klien_web, sesi, site_hosting):
    dasar = f"/api/sites/{site_hosting.site_id}/hosting"
    assert klien_web.post(f"{dasar}/lanjut-dns").status_code == 409
    _status(sesi, site_hosting, StatusHosting.pratinjau)
    assert klien_web.post(f"{dasar}/lanjut-dns").status_code == 200
    assert _h(sesi, site_hosting.site_id).status == StatusHosting.menunggu_dns
    assert klien_web.post(f"{dasar}/kembali-pratinjau").status_code == 200
    assert _h(sesi, site_hosting.site_id).status == StatusHosting.pratinjau
    assert klien_web.post(f"{dasar}/kembali-pratinjau").status_code == 409


def test_aktifkan_dns_belum_lolos_409_dengan_hasil(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    cek_dns["ok"] = False
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 409
    assert r.json()["dns_hasil"]["ok"] is False and r.json()["detail"] == dns_mod.PESAN_BELUM
    h = _h(sesi, site_hosting.site_id)
    assert h.dns_hasil["ok"] is False and h.dns_dicek_pada is not None
    assert sesi.query(Job).count() == 0


def test_aktifkan_dns_lolos_mengantrekan_job(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 200
    job = sesi.get(Job, r.json()["job_id"])
    assert job.tipe == JobType.pindah_aktifkan
    assert job.payload == {"tanpa_tarik_ulang": False, "manual": True}


def test_aktifkan_tanpa_tarik_ulang_wajib_konfirmasi_domain(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns, ditarik_pada=datetime.now(timezone.utc))
    url = f"/api/sites/{site_hosting.site_id}/hosting/aktifkan"
    r = klien_web.post(url, json={"tanpa_tarik_ulang": True, "konfirmasi": "toko"})
    assert r.status_code == 400 and r.json()["detail"] == routes_hosting.PESAN_KONFIRMASI_DOMAIN
    r = klien_web.post(url, json={"tanpa_tarik_ulang": True, "konfirmasi": "toko.co.id"})
    assert r.status_code == 200
    assert sesi.get(Job, r.json()["job_id"]).payload["tanpa_tarik_ulang"] is True


def test_aktifkan_tanpa_tarik_ulang_ditolak_bila_salinan_belum_utuh(klien_web, sesi, site_hosting, cek_dns):
    """Cermin `pindah._periksa_awal`: tidak ada job yang dibuat hanya untuk ditolak."""
    url = f"/api/sites/{site_hosting.site_id}/hosting/aktifkan"
    badan = {"tanpa_tarik_ulang": True, "konfirmasi": "toko.co.id"}
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    r = klien_web.post(url, json=badan)
    assert r.status_code == 409 and r.json()["detail"] == pindah.PESAN_SALINAN_BELUM_UTUH
    _status(sesi, site_hosting, StatusHosting.gagal, gagal_asal="salinan", ditarik_pada=datetime.now(timezone.utc))
    r = klien_web.post(url, json=badan)
    assert r.status_code == 409 and r.json()["detail"] == pindah.PESAN_SALINAN_BELUM_UTUH
    assert cek_dns["n"] == 0 and sesi.query(Job).count() == 0


def test_aktifkan_menghormati_backoff_sertifikat(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns, sertifikat_gagal_kali=2,
            sertifikat_gagal_pada=datetime.now(timezone.utc) - timedelta(minutes=5))
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_BACKOFF
    assert cek_dns["n"] == 0


def test_periksa_ulang_tanpa_cek_dns(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.gagal, gagal_asal="produksi",
            dilayani_vps_pada=datetime.now(timezone.utc))
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 200 and cek_dns["n"] == 0
    job = sesi.get(Job, r.json()["job_id"])
    assert job.tipe == JobType.pindah_aktifkan and job.payload["manual"] is True


@pytest.mark.parametrize("status", [StatusHosting.menunggu_dns, StatusHosting.aktif, StatusHosting.pratinjau])
def test_periksa_ulang_hanya_dari_gagal(klien_web, sesi, site_hosting, cek_dns, status):
    """Putusan L16: sudah dilayani VPS -> hanya `gagal` yang mengantrekan pindah_aktifkan."""
    _status(sesi, site_hosting, status, dilayani_vps_pada=datetime.now(timezone.utc))
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == routes_hosting.PESAN_PERIKSA_ULANG
    assert cek_dns["n"] == 0 and sesi.query(Job).count() == 0


def test_aktifkan_ditolak_dari_status_lain(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.pratinjau)
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 409 and cek_dns["n"] == 0


def test_aktifkan_ditolak_selama_job_hosting_aktif(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    buat_job(sesi, site_hosting.site_id, JobType.backup_hosting)
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_SIBUK
    assert sesi.query(Job).count() == 1


# ---- kata sandi, batal, hapus ------------------------------------------------------------------


def test_sandi_baru_menulis_htpasswd_dan_memuat_router(klien_web, sesi, site_hosting, hosting_aktif, pb):
    _status(sesi, site_hosting, StatusHosting.pratinjau, ditarik_pada=datetime.now(timezone.utc))
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/sandi")
    assert r.status_code == 200
    sandi = r.json()["sandi"]
    baris = (hosting_aktif / "router" / "toko-co-id.htpasswd").read_text().strip()
    assert baris.startswith("pratinjau:") and bcrypt.checkpw(sandi.encode(), baris.split(":", 1)[1].encode())
    assert "prod_router_muat" in pb.nama_panggilan()
    assert bcrypt.checkpw(sandi.encode(), _h(sesi, site_hosting.site_id).sandi_hash.encode())


def test_sandi_gagal_memulihkan_htpasswd_lama(klien_web, sesi, site_hosting, hosting_aktif, pb):
    _status(sesi, site_hosting, StatusHosting.pratinjau, ditarik_pada=datetime.now(timezone.utc))
    lama = site_hosting.sandi_hash
    pb.gagal["prod_router_muat"] = GalatPembantu("docker", "Memuat ulang router hosting gagal. Perintah Docker "
                                                           "di server staging gagal.")
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/sandi")
    assert r.status_code == 502 and r.json()["detail"].startswith("Memuat ulang router hosting gagal.")
    assert (hosting_aktif / "router" / "toko-co-id.htpasswd").read_text() == f"pratinjau:{lama}\n"
    assert _h(sesi, site_hosting.site_id).sandi_hash == lama


def test_sandi_router_sibuk_409_pesan_tetap(klien_web, sesi, site_hosting, hosting_aktif, pb):
    """Keluar 3 (kunci router sibuk) dari panggilan sinkron = sibuk: 409, bukan 502."""
    _status(sesi, site_hosting, StatusHosting.pratinjau, ditarik_pada=datetime.now(timezone.utc))
    lama = site_hosting.sandi_hash
    pb.gagal["prod_router_muat"] = GalatPembantu("ditolak", "Memuat ulang router hosting gagal. Skrip pembantu "
                                                            "menolak permintaan ini.", sibuk=True)
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/sandi")
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_SERVER_SIBUK
    assert (hosting_aktif / "router" / "toko-co-id.htpasswd").read_text() == f"pratinjau:{lama}\n"
    assert _h(sesi, site_hosting.site_id).sandi_hash == lama


def test_sandi_ditolak_sesudah_dilayani(klien_web, sesi, site_hosting, pb):
    _status(sesi, site_hosting, StatusHosting.aktif, dilayani_vps_pada=datetime.now(timezone.utc))
    assert klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/sandi").status_code == 409


def test_batal(klien_web, sesi, site_hosting):
    url = f"/api/sites/{site_hosting.site_id}/hosting/batal"
    assert klien_web.post(url).status_code == 409
    buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    assert klien_web.post(url).status_code == 200
    assert _h(sesi, site_hosting.site_id).batal_diminta_pada is not None
    sesi.query(Job).delete()
    sesi.commit()
    buat_job(sesi, site_hosting.site_id, JobType.pindah_aktifkan, {"kemajuan": {"langkah_aktifkan": "tukar"}})
    r = klien_web.post(url)
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_BATAL_SESUDAH_TUKAR


def test_hapus_menolak_konfirmasi_salah_dilayani_atau_sibuk(klien_web, sesi, site_hosting, pb):
    url = f"/api/sites/{site_hosting.site_id}/hosting"
    assert klien_web.request("DELETE", url, json={"konfirmasi": "toko"}).status_code == 400
    buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    assert klien_web.request("DELETE", url, json={"konfirmasi": "toko.co.id"}).status_code == 409
    sesi.query(Job).delete()
    _status(sesi, site_hosting, StatusHosting.aktif, dilayani_vps_pada=datetime.now(timezone.utc))
    r = klien_web.request("DELETE", url, json={"konfirmasi": "toko.co.id"})
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_HAPUS_DILAYANI
    assert pb.panggilan == []


def test_hapus_membongkar_situs_baris_dan_direktori(klien_web, sesi, site_hosting, hosting_aktif, pb):
    akar = hosting_aktif / str(site_hosting.site_id)
    (akar / "files").mkdir(parents=True)
    (akar / "files" / "index.php").write_bytes(b"x")
    (hosting_aktif / "router").mkdir(parents=True, exist_ok=True)
    (hosting_aktif / "router" / "toko-co-id.htpasswd").write_text("pratinjau:x\n")
    r = klien_web.request("DELETE", f"/api/sites/{site_hosting.site_id}/hosting", json={"konfirmasi": "toko.co.id"})
    assert r.status_code == 200
    assert ("prod_hapus", "toko-co-id") in pb.panggilan
    assert _h(sesi, site_hosting.site_id) is None
    assert not akar.exists()
    assert not (hosting_aktif / "router" / "toko-co-id.htpasswd").exists()
    assert not any(p.name.startswith(".hapus-") for p in hosting_aktif.iterdir())


def test_hapus_galat_pembantu_502_tanpa_mengubah(klien_web, sesi, site_hosting, pb):
    pb.gagal["prod_hapus"] = GalatPembantu("docker", "Menghapus situs hosting gagal. Perintah Docker di server "
                                                    "staging gagal.")
    r = klien_web.request("DELETE", f"/api/sites/{site_hosting.site_id}/hosting", json={"konfirmasi": "toko.co.id"})
    assert r.status_code == 502
    assert r.json()["detail"] == "Menghapus situs hosting gagal. Perintah Docker di server staging gagal."
    assert _h(sesi, site_hosting.site_id) is not None


@pytest.mark.parametrize("sibuk", [True, False])
def test_hapus_pembantu_keluar_3_409_tanpa_mengubah(klien_web, sesi, site_hosting, pb, sibuk):
    """Keluar 3 (tanpa ubah) dari panggilan sinkron: 409 dengan pesan tetap, baris tetap ada."""
    pb.gagal["prod_hapus"] = GalatPembantu("ditolak", "Menghapus situs hosting gagal. Skrip pembantu menolak "
                                                      "permintaan ini.", sibuk=sibuk)
    r = klien_web.request("DELETE", f"/api/sites/{site_hosting.site_id}/hosting", json={"konfirmasi": "toko.co.id"})
    assert r.status_code == 409
    harapan = routes_hosting.PESAN_SERVER_SIBUK if sibuk else \
        "Menghapus situs hosting gagal. Skrip pembantu menolak permintaan ini."
    assert r.json()["detail"] == harapan
    assert _h(sesi, site_hosting.site_id) is not None


def test_hapus_nisan_memakai_akar_yang_diberikan(hosting_aktif):
    """Koreksi #9: nisan di WPMGR_HOSTING_DIR terhapus relatif terhadap akarnya sendiri."""
    akar = hosting_aktif
    (akar / "x" / "files").mkdir(parents=True)
    (akar / "x" / "files" / "a.php").write_bytes(b"x")
    nisan = dorong.ke_nisan(akar, "x", uuid.uuid4())
    assert nisan is not None and (akar / nisan).is_dir()
    dorong.hapus_nisan(akar, nisan)
    assert not (akar / nisan).exists()
    (akar / "y").mkdir()
    dorong.hapus_dir_staging("y", akar)
    assert not (akar / "y").exists()


# ---- penjaga hapus site (putusan L7) -------------------------------------------------------------


def test_hapus_site_ditolak_selama_ada_hosting(klien_web, sesi, site_hosting):
    r = klien_web.delete(f"/api/sites/{site_hosting.site_id}")
    assert r.status_code == 409 and r.json()["detail"] == routes_staging.PESAN_HAPUS_SITE_HOSTING
    _status(sesi, site_hosting, StatusHosting.aktif, dilayani_vps_pada=datetime.now(timezone.utc))
    r = klien_web.delete(f"/api/sites/{site_hosting.site_id}")
    assert r.status_code == 409 and r.json()["detail"] == routes_staging.PESAN_HAPUS_SITE_DIHOSTING
    assert sesi.get(Site, site_hosting.site_id) is not None


@pytest.mark.parametrize("status", list(StatusHosting))
@pytest.mark.parametrize("dilayani", [False, True])
def test_hapus_site_ditolak_di_setiap_status_hosting(klien_web, sesi, site_hosting, pb, status, dilayani):
    """L7: kaskade site tidak pernah meninggalkan container/nginx/sertifikat produksi yatim."""
    _status(sesi, site_hosting, status, gagal_asal="salinan" if status == StatusHosting.gagal else None,
            dilayani_vps_pada=datetime.now(timezone.utc) if dilayani else None)
    r = klien_web.delete(f"/api/sites/{site_hosting.site_id}")
    assert r.status_code == 409
    assert r.json()["detail"] == (routes_staging.PESAN_HAPUS_SITE_DIHOSTING if dilayani
                                  else routes_staging.PESAN_HAPUS_SITE_HOSTING)
    assert sesi.get(Site, site_hosting.site_id, populate_existing=True) is not None
    assert _h(sesi, site_hosting.site_id) is not None
    assert pb.panggilan == []


def test_hapus_site_boleh_sesudah_pindah_dibatalkan(klien_web, sesi, site_hosting, pb):
    site_id = site_hosting.site_id
    url = f"/api/sites/{site_id}/hosting"
    assert klien_web.request("DELETE", url, json={"konfirmasi": "toko.co.id"}).status_code == 200
    assert klien_web.delete(f"/api/sites/{site_id}").status_code == 200
    sesi.expire_all()
    assert sesi.get(Site, site_id) is None


# ---- job staging selama pindah hosting (carry Task 6) ------------------------------------------


def _antre_staging(klien_web, site_id) -> list:
    """Setiap route yang mengantrekan job staging; (status, detail) per route."""
    hasil = []
    for metode, url, badan in [
        ("POST", f"/api/sites/{site_id}/staging", {}),
        ("POST", f"/api/sites/{site_id}/staging/uji", {"paket": [{"tipe": "plugin", "slug": "akismet",
                                                                  "ke": "5.3"}]}),
        ("POST", "/api/staging/uji", {"items": [{"site_id": str(site_id), "tipe": "plugin", "slug": "akismet",
                                                 "ke_versi": "5.3"}]}),
        ("POST", f"/api/sites/{site_id}/staging/dorong", {"mode": "hanya_kode"}),
        ("POST", f"/api/sites/{site_id}/staging/kembalikan", {"snapshot_id": 1, "konfirmasi_nama": "Contoh"}),
    ]:
        r = klien_web.request(metode, url, json=badan)
        hasil.append((r.status_code, r.json().get("detail")))
    return hasil


def _jumlah_job_staging(sesi) -> int:
    return sesi.query(Job).filter(Job.tipe.in_((JobType.staging_tarik, JobType.staging_uji_update,
                                                JobType.staging_dorong, JobType.staging_kembalikan))).count()


@pytest.mark.parametrize("keadaan", ["job_pending", "job_running", "menyalin", "mengaktifkan"])
def test_job_staging_ditolak_selama_pindah_hosting(klien_web, sesi, site_hosting, pb, keadaan):
    if keadaan.startswith("job_"):
        _status(sesi, site_hosting, StatusHosting.gagal, gagal_asal="produksi",
                dilayani_vps_pada=datetime.now(timezone.utc))
        job = buat_job(sesi, site_hosting.site_id, JobType.pindah_aktifkan,
                       {"kemajuan": {"langkah_aktifkan": "tukar"}})
        if keadaan == "job_running":
            job.status = JobStatus.running
            sesi.commit()
    else:
        _status(sesi, site_hosting, StatusHosting[keadaan])
    hasil = _antre_staging(klien_web, site_hosting.site_id)
    for status, detail in hasil[:2] + hasil[3:]:
        assert (status, detail) == (409, routes_staging.PESAN_SIBUK_HOSTING)
    assert hasil[2] == (409, f"Contoh: {routes_staging.PESAN_SIBUK_HOSTING}")
    assert _jumlah_job_staging(sesi) == 0


def test_job_staging_boleh_saat_hosting_diam(klien_web, sesi, site_hosting, pb):
    _status(sesi, site_hosting, StatusHosting.pratinjau)
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/staging", json={})
    assert r.status_code == 200, r.text
    assert sesi.get(Job, r.json()["job_id"]).tipe == JobType.staging_tarik


# ---- ringkasan progres -----------------------------------------------------------------------


@pytest.mark.parametrize("kemajuan,tahap,label", [
    ({"langkah_aktifkan": "dns"}, "dns", "Memeriksa DNS"),
    ({"langkah_aktifkan": "tukar"}, "tukar", "Mengaktifkan situs di VPS"),
    ({"langkah_aktifkan": "tarik", "tahap": "berkas"}, "berkas", "Menyalin berkas"),
    ({"tahap": "pratinjau"}, "pratinjau", "Menyiapkan pratinjau (sertifikat dan nginx)"),
    ({"tahap_backup": "backup"}, "backup", "Membuat backup"),
])
def test_ringkas_kemajuan_tahap_hosting(kemajuan, tahap, label):
    p = ringkas_kemajuan(Job(tipe=JobType.pindah_aktifkan, payload={"kemajuan": kemajuan}))
    assert (p["tahap"], p["label"]) == (tahap, label)


def test_get_menampilkan_backup_terbaru_dan_gagal_asal(klien_web, sesi, site_hosting):
    from wpmgr.models import HostingBackup

    for i in range(3):
        sesi.add(HostingBackup(site_id=site_hosting.site_id, tujuan="lokal", stempel=f"2026100{i + 1}T023000Z",
                               status="tersedia", manual=i == 2, ukuran_db=1024, ukuran_file=2048,
                               sha256_db="a" * 64, sha256_file="b" * 64))
    _status(sesi, site_hosting, StatusHosting.gagal, gagal_asal="salinan", galat="Salin ke VPS gagal.")
    d = klien_web.get(f"/api/sites/{site_hosting.site_id}/hosting").json()
    assert [b["stempel"] for b in d["backup"]] == ["20261003T023000Z", "20261002T023000Z", "20261001T023000Z"]
    assert d["backup"][0]["manual"] is True and d["backup"][0]["ukuran_db_teks"] == "1,0 KB"
    assert d["hosting"]["gagal_asal"] == "salinan" and d["hosting"]["galat"] == "Salin ke VPS gagal."
    assert sesi.scalar(select(ActivityLog.id).where(ActivityLog.site_id == site_hosting.site_id)) is None


def test_get_tidak_menunggu_kunci_baris(klien_web, sesi, site_hosting, engine):
    """GET tidak pernah mengambil kunci yang menunggu: baris sites/hosting yang dikunci pihak lain tetap terbaca."""
    from sqlalchemy.orm import Session

    with Session(engine) as lain:
        lain.execute(select(Site.id).where(Site.id == site_hosting.site_id).with_for_update())
        lain.execute(select(HostingVps.id).where(HostingVps.site_id == site_hosting.site_id).with_for_update())
        r = klien_web.get(f"/api/sites/{site_hosting.site_id}/hosting")
        lain.rollback()
    assert r.status_code == 200 and r.json()["hosting"]["nama"] == "toko-co-id"
