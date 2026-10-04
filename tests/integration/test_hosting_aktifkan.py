from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified
from staging_palsu import PembantuHostingPalsu, ProduksiPalsu

from wpmgr.errors import TRANSIENT, SiteError
from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting import pindah
from wpmgr.hosting import umum as hu
from wpmgr.hosting.dns import Jawaban
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import akan_diulang, buat_job
from wpmgr.models import ActivityLog, HostingVps, Job, JobStatus, JobType, StatusHosting
from wpmgr.staging import rencana, tarik, umum
from wpmgr.staging.pembantu import GalatPembantu

pytestmark = pytest.mark.integration

MTIME = 1_700_000_000
IP_LAMA = "93.184.216.34"
VPS = "169.58.91.181"
MU = "wp-content/mu-plugins/wpmgr-pratinjau.php"


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))
    monkeypatch.setattr(rencana, "UKURAN_PAKET", 1000)
    monkeypatch.setattr(tarik, "UKURAN_PAKET", 1000)


@pytest.fixture
def prod():
    p = ProduksiPalsu()
    p.info = {**p.info, "home": "https://toko.co.id", "siteurl": "https://toko.co.id"}
    p.berkas = {"index.php": (b"<?php // indeks", MTIME)}
    p.tabel = {"wp_options": [b"DROP TABLE IF EXISTS `wp_options`;\nCREATE TABLE `wp_options` (`a` text);\n"]}
    return p


@pytest.fixture
def pb(hosting_aktif, monkeypatch):
    palsu = PembantuHostingPalsu(hosting_aktif)
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


@pytest.fixture
def lama(prod, monkeypatch):
    host: list[tuple[str, str]] = []

    def tangani(r):
        host.append((r.url.host, r.headers.get("host")))
        return prod.tangani(r)

    monkeypatch.setattr(hu, "buat_http_lama", lambda: httpx.Client(transport=httpx.MockTransport(tangani)))
    return host


class PenanyaDnsPalsu:
    """DNS publik tiruan: A ke VPS; AAAA lama di apex bila `aaaa_lama`."""

    def __init__(self) -> None:
        self.aaaa_lama = False
        self.n = 0

    def tanya(self, resolver, nama, jenis, batas):
        self.n += 1
        if jenis == "A":
            return Jawaban((VPS,))
        if jenis == "AAAA" and self.aaaa_lama and nama == "toko.co.id":
            return Jawaban(("2a02:4780:6:1512:0:1e2d:4bc3:3",))
        return Jawaban()


@pytest.fixture
def dns_palsu(monkeypatch):
    p = PenanyaDnsPalsu()
    monkeypatch.setattr(dns_mod, "buat_penanya", lambda: p)
    return p


class HalamanPalsu:
    def __init__(self) -> None:
        self.jawaban = (200, {})
        self.host: list[str] = []

    def __call__(self, host):
        self.host.append(host)
        return self.jawaban


@pytest.fixture
def halaman(monkeypatch):
    h = HalamanPalsu()
    monkeypatch.setattr(pindah, "ambil_halaman_verifikasi", h)
    return h


@pytest.fixture
def siap(sesi, site_hosting, prod, pb, lama, dns_palsu, halaman):
    """Salinan VPS sudah dibuat (pindah_tarik sungguhan) dan pengguna sudah lanjut ke DNS."""
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    pindah.tangani_pindah_tarik(sesi, job, None)
    job.status = JobStatus.success
    h = sesi.get(HostingVps, site_hosting.id, populate_existing=True)
    h.status = StatusHosting.menunggu_dns
    sesi.commit()
    pb.panggilan.clear()
    lama.clear()
    return h


def _aktifkan(sesi, h, payload=None, job=None):
    job = job or buat_job(sesi, h.site_id, JobType.pindah_aktifkan, payload or {})
    return job, pindah.tangani_pindah_aktifkan(sesi, job, None)


def _h(sesi, h):
    return sesi.get(HostingVps, h.id, populate_existing=True)


def _files(hosting_aktif, h):
    return hosting_aktif / str(h.site_id) / "files"


def _ditolak_sibuk() -> GalatPembantu:
    # Kunci router/nginx sibuk (`GalatPembantu.sibuk`): satu-satunya keluar 3 yang diulang sebagai sibuk.
    return GalatPembantu("ditolak", "Mengaktifkan situs gagal. Skrip pembantu menolak permintaan ini.", sibuk=True)


def _ditolak_permanen(pesan="Mengaktifkan situs gagal. Skrip pembantu menolak permintaan ini.") -> GalatPembantu:
    # Keluar 3 tanpa penanda kunci sibuk: prasyarat yang ditolak (final review I1).
    return GalatPembantu("ditolak", pesan)


def _ubah_kemajuan(sesi, job, **ubah) -> Job:
    job = sesi.get(Job, job.id, populate_existing=True)
    payload = dict(job.payload)
    payload["kemajuan"] = {**payload["kemajuan"], **ubah}
    job.payload = payload
    flag_modified(job, "payload")
    sesi.commit()
    return job


def test_handler_terdaftar():
    assert handlers.HANDLER[JobType.pindah_aktifkan] is pindah.tangani_pindah_aktifkan


def test_aktifkan_sukses_penuh(sesi, siap, hosting_aktif, pb, lama, halaman):
    job, hasil = _aktifkan(sesi, siap)
    nama = pb.nama_panggilan()
    assert nama.index("prod_domain") < nama.index("prod_sertifikat") < nama.index("prod_db_impor") \
        < nama.index("prod_aktifkan")
    assert not (_files(hosting_aktif, siap) / MU).exists()
    assert halaman.host == ["toko.co.id", "www.toko.co.id"]
    h = _h(sesi, siap)
    assert h.status == StatusHosting.aktif and h.galat is None and h.gagal_asal is None
    assert h.aktif_pada is not None and h.dilayani_vps_pada is not None and h.sertifikat_pada is not None
    assert h.dns_hasil["ok"] is True and h.dns_dicek_pada is not None
    k = umum.kemajuan(job)
    assert k["langkah_aktifkan"] == "beres" and k["tukar_pada"]
    assert hasil["domain"] == "toko.co.id"
    log = [x.pesan for x in sesi.query(ActivityLog).filter(ActivityLog.job_id == job.id)]
    assert log == ["Site dihosting di VPS"]


def test_beres_tidak_menyisipkan_job_lain(sesi, siap, pb, lama, halaman):
    _aktifkan(sesi, siap)
    assert {j.tipe for j in sesi.query(Job).all()} == {JobType.pindah_tarik, JobType.pindah_aktifkan}
    assert sesi.query(Job).filter(Job.tipe == JobType.backup_hosting).count() == 0


def test_aktifkan_dns_belum_lolos_status_tetap(sesi, siap, pb, dns_palsu):
    dns_palsu.aaaa_lama = True
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _aktifkan(sesi, siap)
    h = _h(sesi, siap)
    assert h.status == StatusHosting.menunggu_dns
    assert h.galat == dns_mod.PESAN_BELUM
    assert h.dns_hasil["ok"] is False and h.dilayani_vps_pada is None
    assert pb.panggilan == []


def test_aktifkan_sertifikat_gagal_backoff_tanpa_tarik(sesi, siap, pb, lama):
    pb.gagal["prod_sertifikat"] = GalatPembantu("sertifikat", "Menerbitkan sertifikat domain gagal. Sertifikat "
                                                               "staging belum dapat diterbitkan.")
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _aktifkan(sesi, siap)
    h = _h(sesi, siap)
    assert (h.status, h.galat) == (StatusHosting.menunggu_dns, pindah.PESAN_SERTIFIKAT)
    assert h.sertifikat_gagal_kali == 1 and h.sertifikat_gagal_pada is not None
    assert lama == [] and "prod_aktifkan" not in pb.nama_panggilan()


def test_aktifkan_tarik_dipatok_ke_ip_lama(sesi, siap, hosting_aktif, prod, lama, halaman):
    prod.berkas["index.php"] = (b"<?php // data terakhir", MTIME + 5)
    _aktifkan(sesi, siap)
    assert lama and all(x == (IP_LAMA, "toko.co.id") for x in lama)
    assert (_files(hosting_aktif, siap) / "index.php").read_bytes() == b"<?php // data terakhir"


def test_prod_aktifkan_keluar_3_menghapus_penanda(sesi, siap, hosting_aktif, pb):
    # Carry Task 10 + putusan L10: keluar 3 pada kiriman pertama = pasti tanpa
    # perubahan; penanda tulis-lebih-dulu dicabut, lalu job diulang sebagai
    # "sibuk" (kunci router dipegang impor situs lain sampai 3 jam).
    pb.gagal["prod_aktifkan"] = _ditolak_sibuk()
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(hu.GalatSibuk) as e:
        _aktifkan(sesi, siap, job=job)
    assert e.value.error_class == TRANSIENT
    h = _h(sesi, siap)
    assert h.dilayani_vps_pada is None
    assert (h.status, h.galat) == (StatusHosting.mengaktifkan, hu.PESAN_MENUNGGU_SIBUK)
    job = sesi.get(Job, job.id, populate_existing=True)
    k = umum.kemajuan(job)
    assert k["langkah_aktifkan"] == "dns" and k["tukar_pada"] is None and k["tukar_dikirim"] is False
    assert akan_diulang(job, TRANSIENT) is True
    # Lewat jendela sibuk (4 jam): berakhir seperti spec §10.4, status sebelum
    # job (menunggu DNS) dengan pesan tetap untuk kunci sibuk (review Task 10 M5).
    _ubah_kemajuan(sesi, job, sibuk_sejak=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat())
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _aktifkan(sesi, siap, job=job)
    h = _h(sesi, siap)
    assert h.dilayani_vps_pada is None
    assert (h.status, h.galat) == (StatusHosting.menunggu_dns, pindah.PESAN_SERVER_SIBUK)
    k = umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))
    assert k["langkah_aktifkan"] == "dns" and k["tukar_pada"] is None and k["tukar_dikirim"] is False


def test_prod_aktifkan_keluar_3_bukan_sibuk_langsung_ditolak_tanpa_ubah(sesi, siap, hosting_aktif, pb):
    # Final review I1: prasyarat prod-aktifkan yang ditolak (bukan kunci sibuk) bersifat pasti (R15):
    # tidak diulang 4 jam sebagai "Menunggu proses lain", status langsung kembali ke menunggu DNS.
    pb.gagal["prod_aktifkan"] = _ditolak_permanen()
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _aktifkan(sesi, siap, job=job)
    assert not isinstance(e.value, hu.GalatSibuk) and e.value.pesan == pindah.PESAN_TUKAR_DITOLAK
    job = sesi.get(Job, job.id, populate_existing=True)
    assert akan_diulang(job, e.value.error_class) is False
    h = _h(sesi, siap)
    assert h.dilayani_vps_pada is None and h.gagal_asal is None
    assert (h.status, h.galat) == (StatusHosting.menunggu_dns, pindah.PESAN_TUKAR_DITOLAK)
    k = umum.kemajuan(job)
    assert k["langkah_aktifkan"] == "dns" and k["tukar_pada"] is None and k["tukar_dikirim"] is False
    assert not k.get("sibuk_kali")
    # Situs masih pratinjau: pemblokir email dipasang lagi.
    assert (_files(hosting_aktif, siap) / MU).exists()


def test_tukar_ditolak_memasang_ulang_mu_plugin(sesi, siap, hosting_aktif, pb):
    pb.gagal["prod_aktifkan"] = _ditolak_sibuk()
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap)
    # Situs masih pratinjau (konstanta WPMGR_PRATINJAU ada): pemblokir email wajib ada lagi.
    assert (_files(hosting_aktif, siap) / MU).exists()


def test_penanda_tulis_lebih_dulu_sudah_dicommit_saat_prod_aktifkan_dikirim(sesi, engine, siap, pb, halaman):
    # RF5: dilayani_vps_pada dan tukar_pada sudah ter-commit (terlihat dari
    # koneksi lain) sebelum prod-aktifkan dikirim.
    terlihat = {}
    asli = pb.prod_aktifkan

    def periksa(nama):
        with sessionmaker(bind=engine, future=True)() as s:
            terlihat["dilayani"] = s.get(HostingVps, siap.id).dilayani_vps_pada
            j = s.query(Job).filter(Job.tipe == JobType.pindah_aktifkan).one()
            terlihat["kemajuan"] = dict(j.payload["kemajuan"])
        return asli(nama)

    pb.prod_aktifkan = periksa
    _aktifkan(sesi, siap)
    assert terlihat["dilayani"] is not None
    k = terlihat["kemajuan"]
    assert k["langkah_aktifkan"] == "tukar" and k["tukar_dikirim"] is True
    assert datetime.fromisoformat(k["tukar_pada"]) == terlihat["dilayani"]


def test_penanda_hilang_sesudah_langkah_tukar_dipasang_lagi(sesi, siap, pb, halaman):
    # Percobaan yang terhenti di antara dua commit tulis-lebih-dulu (job
    # sudah `tukar`, baris hosting belum): penanda diisi sebelum prod-aktifkan.
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    tukar_pada = datetime.now(timezone.utc) - timedelta(minutes=3)
    job.payload = {"kemajuan": {"langkah_aktifkan": "tukar", "tukar_pada": tukar_pada.isoformat(),
                                "tukar_dikirim": False, "status_hosting_awal": "menunggu_dns"}}
    sesi.commit()
    terlihat = {}
    asli = pb.prod_aktifkan

    def periksa(nama):
        terlihat["dilayani"] = sesi.get(HostingVps, siap.id, populate_existing=True).dilayani_vps_pada
        return asli(nama)

    pb.prod_aktifkan = periksa
    _aktifkan(sesi, siap, job=job)
    assert terlihat["dilayani"] == tukar_pada
    assert pb.nama_panggilan() == ["prod_aktifkan"]
    assert _h(sesi, siap).status == StatusHosting.aktif


def test_keluar_3_pada_kiriman_ulang_tetap_produksi_tersentuh(sesi, siap, hosting_aktif, pb):
    # Kiriman pertama keluar bukan 3: MODE=aktif sudah ditulis (putusan L5).
    # Keluar 3 sesudahnya (kunci sibuk) bukan bukti "tanpa perubahan".
    pb.gagal["prod_aktifkan"] = GalatPembantu("docker", "Mengaktifkan situs gagal. Perintah Docker di server "
                                                        "staging gagal.")
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap, job=job)
    pb.gagal["prod_aktifkan"] = _ditolak_sibuk()
    with pytest.raises(SiteError) as e:
        _aktifkan(sesi, siap, job=job)
    assert e.value.error_class == TRANSIENT and not isinstance(e.value, umum.GalatDitolakTanpaUbah)
    # R26, bukan jalur sibuk (yang juga TRANSIENT): review Task 10 M1.
    assert not isinstance(e.value, hu.GalatSibuk)
    h = _h(sesi, siap)
    assert h.dilayani_vps_pada is not None and h.status == StatusHosting.mengaktifkan
    assert h.galat.startswith("Terputus")
    k = umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))
    assert k["langkah_aktifkan"] == "tukar" and k["tukar_pada"]
    # Mu-plugin pratinjau tidak dipasang lagi: situs mungkin sudah aktif.
    assert not (_files(hosting_aktif, siap) / MU).exists()


def test_percobaan_sesudah_tukar_ditolak_menarik_data_terbaru(sesi, siap, hosting_aktif, prod, pb, lama, halaman):
    pb.gagal["prod_aktifkan"] = _ditolak_sibuk()
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(hu.GalatSibuk):
        _aktifkan(sesi, siap, job=job)
    assert umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))["tarik_selesai_pada"]
    # Hosting lama masih menerima data selama job menunggu kunci.
    prod.berkas["index.php"] = (b"<?php // sesudah menunggu", MTIME + 9)
    lama.clear()
    pb.panggilan.clear()
    # Putusan L17: diulang dalam 60 menit, tarik yang baru selesai dipakai lagi.
    with pytest.raises(hu.GalatSibuk):
        _aktifkan(sesi, siap, job=job)
    assert lama == [] and "prod_db_impor" not in pb.nama_panggilan()
    assert (_files(hosting_aktif, siap) / "index.php").read_bytes() == b"<?php // indeks"
    # Lewat 60 menit: tarik disegarkan dari hosting lama sebelum tukar.
    _ubah_kemajuan(sesi, job, tarik_selesai_pada=(datetime.now(timezone.utc) - timedelta(minutes=61)).isoformat())
    del pb.gagal["prod_aktifkan"]
    _aktifkan(sesi, siap, job=job)
    assert lama
    assert (_files(hosting_aktif, siap) / "index.php").read_bytes() == b"<?php // sesudah menunggu"
    assert _h(sesi, siap).status == StatusHosting.aktif


def test_jendela_sibuk_tidak_dimulai_ulang_saat_langkah_bergantian(sesi, siap, pb, lama):
    # Review Task 10 M4: sibuk di tukar, lalu di impor tarik penyegaran, lalu
    # di tukar lagi -- jendela 4 jam tetap satu rentetan, tidak terus dimulai ulang.
    def jadul(menit):
        return (datetime.now(timezone.utc) - timedelta(minutes=menit)).isoformat()

    pb.gagal["prod_aktifkan"] = _ditolak_sibuk()
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(hu.GalatSibuk):
        _aktifkan(sesi, siap, job=job)
    # Langkah baru (impor) untuk pertama kali: jendela dimulai di sini.
    _ubah_kemajuan(sesi, job, tarik_selesai_pada=jadul(61))
    pb.gagal["prod_db_impor"] = _ditolak_sibuk()
    with pytest.raises(hu.GalatSibuk):
        _aktifkan(sesi, siap, job=job)
    k = umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))
    sejak = k["sibuk_sejak"]
    assert k["sibuk_kali"] == 1
    # Bergantian kembali ke tukar, lalu ke impor: rentetan yang sama berlanjut.
    del pb.gagal["prod_db_impor"]
    with pytest.raises(hu.GalatSibuk):
        _aktifkan(sesi, siap, job=job)
    _ubah_kemajuan(sesi, job, tarik_selesai_pada=jadul(61))
    pb.gagal["prod_db_impor"] = _ditolak_sibuk()
    with pytest.raises(hu.GalatSibuk):
        _aktifkan(sesi, siap, job=job)
    k = umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))
    assert (k["sibuk_kali"], k["sibuk_sejak"]) == (3, sejak)


def test_kunci_sibuk_berakhir_dengan_pesan_netral(sesi, siap, pb):
    # Review Task 10 M5: keluar 3 karena kunci router/nginx tidak menyalahkan
    # sertifikat, database, atau container.
    pb.gagal["prod_aktifkan"] = GalatPembantu("ditolak", "Mengaktifkan situs gagal. Skrip pembantu menolak "
                                                         "permintaan ini.", sibuk=True)
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(hu.GalatSibuk):
        _aktifkan(sesi, siap, job=job)
    _ubah_kemajuan(sesi, job, sibuk_sejak=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat())
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _aktifkan(sesi, siap, job=job)
    h = _h(sesi, siap)
    assert (h.status, h.galat) == (StatusHosting.menunggu_dns, pindah.PESAN_SERVER_SIBUK)


def test_cabut_tukar_terhenti_di_akhir_diulang_sebagai_kiriman_pertama(sesi, siap, hosting_aktif, pb,
                                                                        monkeypatch):
    # Review Task 10 I1: terhenti tepat sebelum langkah kembali ke dns.
    pb.gagal["prod_aktifkan"] = _ditolak_sibuk()
    asli = pindah._langkah

    def langkah(sesi_, job_, nama, **lain):
        if nama == "dns" and "tukar_pada" in lain:
            raise RuntimeError("terhenti")
        return asli(sesi_, job_, nama, **lain)

    monkeypatch.setattr(pindah, "_langkah", langkah)
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap, job=job)
    k = umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))
    assert (k["langkah_aktifkan"], k["tukar_dikirim"]) == ("tukar", False)
    assert _h(sesi, siap).dilayani_vps_pada is None
    assert (_files(hosting_aktif, siap) / MU).exists()
    # Percobaan berikutnya: keluar 3 lagi = kiriman pertama, pencabutan dituntaskan.
    monkeypatch.setattr(pindah, "_langkah", asli)
    with pytest.raises(hu.GalatSibuk):
        _aktifkan(sesi, siap, job=job)
    k = umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))
    assert (k["langkah_aktifkan"], k["tukar_pada"], k["tukar_dikirim"]) == ("dns", None, False)
    assert _h(sesi, siap).dilayani_vps_pada is None
    assert (_files(hosting_aktif, siap) / MU).exists()


def test_cabut_tukar_oleh_worker_zombi_berhenti_sebelum_menyentuh_apa_pun(sesi, engine, siap, hosting_aktif, pb):
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    job.status = JobStatus.running
    job.locked_by = "pekerja-a"
    sesi.commit()
    job._pemegang_klaim = "pekerja-a"

    def direbut(nama):
        pb._catat("prod_aktifkan", nama)
        # Reaper merebut job ini selagi prod-aktifkan berjalan.
        with sessionmaker(bind=engine, future=True)() as s:
            s.execute(update(Job).where(Job.id == job.id).values(status=JobStatus.pending, locked_by=None))
            s.commit()
        raise _ditolak_sibuk()

    pb.prod_aktifkan = direbut
    with pytest.raises(umum.KlaimHilang):
        _aktifkan(sesi, siap, job=job)
    k = umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))
    assert (k["langkah_aktifkan"], k["tukar_dikirim"]) == ("tukar", True)
    assert _h(sesi, siap).dilayani_vps_pada is not None
    assert not (_files(hosting_aktif, siap) / MU).exists()


def test_dns_menolak_sesudah_tarik_terakhir_terputus_menandai_salinan(sesi, siap, prod, pb, dns_palsu, monkeypatch):
    # Review Task 10 I2: penolakan tanpa ubah sesudah salinan VPS setengah
    # ditulis tidak boleh mengembalikan status siap.
    prod.berkas["index.php"] = (b"<?php // baru", MTIME + 3)

    def http_putus():
        def tangani(r):
            if r.url.path.endswith("/staging/tabel"):
                raise httpx.ConnectError("putus", request=r)
            return prod.tangani(r)

        return httpx.Client(transport=httpx.MockTransport(tangani))

    monkeypatch.setattr(hu, "buat_http_lama", http_putus)
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(SiteError) as e:
        _aktifkan(sesi, siap, job=job)
    assert e.value.error_class == TRANSIENT
    assert umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))["tahap"] == "db"
    # Pengguna mengembalikan DNS sebelum percobaan berikutnya.
    dns_palsu.aaaa_lama = True
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _aktifkan(sesi, siap, job=job)
    h = _h(sesi, siap)
    assert (h.status, h.gagal_asal) == (StatusHosting.gagal, "salinan")
    job = sesi.get(Job, job.id, populate_existing=True)
    job.status = JobStatus.failed
    sesi.commit()
    pb.panggilan.clear()
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _aktifkan(sesi, siap, {"tanpa_tarik_ulang": True})
    assert e.value.pesan == pindah.PESAN_SALINAN_BELUM_UTUH and pb.panggilan == []


def test_ambil_halaman_verifikasi_dipatok_ke_localhost_dengan_sni(monkeypatch):
    # Review Task 10 M2.
    diminta = []

    def tangani(r):
        diminta.append(r)
        return httpx.Response(302, headers={"Location": "https://toko.co.id/lain", "X-Robots-Tag": "All"})

    asli = pindah.buat_http_verifikasi
    monkeypatch.setattr(pindah, "buat_http_verifikasi", lambda: asli(transport=httpx.MockTransport(tangani)))
    status, header = pindah.ambil_halaman_verifikasi("toko.co.id")
    # Alihan tidak diikuti: 3xx dinilai apa adanya, satu permintaan saja.
    assert status == 302 and len(diminta) == 1
    r = diminta[0]
    assert (r.url.scheme, r.url.host, r.url.path) == ("https", "127.0.0.1", "/")
    assert r.headers["host"] == "toko.co.id"
    assert r.extensions["sni_hostname"] == "toko.co.id"
    assert header["x-robots-tag"] == "All" and header["location"] == "https://toko.co.id/lain"
    assert all(k == k.lower() for k in header)

    def putus(r):
        raise httpx.ConnectError("ditolak", request=r)

    monkeypatch.setattr(pindah, "buat_http_verifikasi", lambda: asli(transport=httpx.MockTransport(putus)))
    assert pindah.ambil_halaman_verifikasi("toko.co.id") is None


def test_aktifkan_terputus_sesudah_tukar_diulang_sampai_24_jam(sesi, siap, pb):
    pb.gagal["prod_aktifkan"] = GalatPembantu("waktu", "Skrip pembantu tidak selesai dalam 300 detik.")
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _aktifkan(sesi, siap, job=job)
    assert e.value.error_class == TRANSIENT
    job = sesi.get(Job, job.id, populate_existing=True)
    assert akan_diulang(job, TRANSIENT) is True
    h = _h(sesi, siap)
    assert h.status == StatusHosting.mengaktifkan and h.dilayani_vps_pada is not None
    tukar_pada = umum.kemajuan(job)["tukar_pada"]
    # Percobaan berikutnya: hanya prod-aktifkan yang dikirim ulang; DNS, sertifikat, tarik dilewati.
    pb.panggilan.clear()
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap, job=job)
    assert pb.nama_panggilan() == ["prod_aktifkan"]
    job = sesi.get(Job, job.id, populate_existing=True)
    assert umum.kemajuan(job)["tukar_pada"] == tukar_pada
    # Lewat 24 jam sejak tukar: final, gagal asal produksi dengan pesan tetap.
    payload = dict(job.payload)
    payload["kemajuan"] = {**payload["kemajuan"],
                           "tukar_pada": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()}
    job.payload = payload
    flag_modified(job, "payload")
    sesi.commit()
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap, job=job)
    h = _h(sesi, siap)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "produksi", hu.PESAN_PRODUKSI_GAGAL)


def test_aktifkan_terputus_saat_tukar_tidak_bisa_tarik_lagi(sesi, siap, pb, lama):
    pb.gagal["prod_aktifkan"] = GalatPembantu("docker", "Mengaktifkan situs gagal. Perintah Docker di server "
                                                        "staging gagal.")
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap, job=job)
    job.status = JobStatus.failed
    sesi.commit()
    lama.clear()
    tarik_job = buat_job(sesi, siap.site_id, JobType.pindah_tarik)
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        pindah.tangani_pindah_tarik(sesi, tarik_job, None)
    assert e.value.pesan == pindah.PESAN_SUDAH_DILAYANI
    assert lama == []


def test_tanpa_tarik_ulang_melewati_tarik(sesi, siap, prod, pb, lama, halaman):
    _aktifkan(sesi, siap, {"tanpa_tarik_ulang": True})
    assert lama == []
    assert "prod_db_impor" not in pb.nama_panggilan() and "prod_aktifkan" in pb.nama_panggilan()
    assert _h(sesi, siap).status == StatusHosting.aktif


def test_tanpa_tarik_ulang_ditolak_untuk_salinan_setengah_jadi(sesi, siap, pb, lama):
    siap.status = StatusHosting.gagal
    siap.gagal_asal = "salinan"
    sesi.commit()
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _aktifkan(sesi, siap, {"tanpa_tarik_ulang": True})
    assert e.value.pesan == pindah.PESAN_SALINAN_BELUM_UTUH
    h = _h(sesi, siap)
    assert (h.status, h.gagal_asal) == (StatusHosting.gagal, "salinan")
    assert pb.panggilan == [] and lama == []


def test_tanpa_tarik_ulang_ditolak_tanpa_salinan_lengkap(sesi, siap, pb, lama):
    # Belum pernah ada salinan lengkap (`ditarik_pada` kosong): hanya salin yang bisa membuatnya.
    siap.ditarik_pada = None
    sesi.commit()
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _aktifkan(sesi, siap, {"tanpa_tarik_ulang": True})
    assert e.value.pesan == pindah.PESAN_SALINAN_BELUM_UTUH
    assert _h(sesi, siap).status == StatusHosting.menunggu_dns
    assert pb.panggilan == [] and lama == []


def test_hosting_lama_tak_terjangkau_dari_gagal_salinan_menunjuk_salin_ulang(sesi, siap, monkeypatch):
    # Koreksi I2.3: aktivasi yang dimulai dari `gagal` 'salinan' (salinan belum utuh) dan gagal sebelum
    # menyentuh salinan tetap `gagal` 'salinan', dengan pesan yang menunjuk Salin ulang -- bukan aktivasi.
    siap.status = StatusHosting.gagal
    siap.gagal_asal = "salinan"
    sesi.commit()

    def tangani(r):
        raise httpx.ConnectError("hosting lama mati", request=r)

    monkeypatch.setattr(hu, "buat_http_lama", lambda: httpx.Client(transport=httpx.MockTransport(tangani)))
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _aktifkan(sesi, siap, job=job)
    h = _h(sesi, siap)
    assert (h.status, h.gagal_asal) == (StatusHosting.gagal, "salinan")
    assert h.galat == f"{hu.pesan_ui(e.value)} {hu.PESAN_SALIN_ULANG_DULU}"
    assert "Aktifkan tanpa salin ulang" not in h.galat and "Salin ulang" in h.galat


@pytest.mark.parametrize("jawaban", ["putus", "tanpa_connector"])
def test_hosting_lama_tak_terjangkau_saat_aktivasi_kembali_menunggu_dns(sesi, siap, hosting_aktif, pb, halaman,
                                                                         monkeypatch, jawaban):
    # Final review I2: tarik terakhir gagal final di manifest (salinan VPS belum disentuh): bukan
    # `gagal` 'salinan' (yang menolak Aktifkan tanpa salin ulang), tetapi kembali menunggu DNS
    # dengan pesan tetap yang menunjuk tombol itu.
    def tangani(r):
        if jawaban == "putus":
            raise httpx.ConnectError("hosting lama mati", request=r)
        return httpx.Response(404, text="<html>Not Found /home/u1/public_html</html>")

    monkeypatch.setattr(hu, "buat_http_lama", lambda: httpx.Client(transport=httpx.MockTransport(tangani)))
    ditarik = siap.ditarik_pada
    isi = (_files(hosting_aktif, siap) / "index.php").read_bytes()
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    job.attempts = job.max_attempts  # kegagalan ini final
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _aktifkan(sesi, siap, job=job)
    assert not isinstance(e.value, hu.GalatSibuk)
    job = sesi.get(Job, job.id, populate_existing=True)
    assert akan_diulang(job, e.value.error_class) is False
    assert umum.kemajuan(job)["tahap"] == "manifest" and umum.kemajuan(job)["langkah_aktifkan"] == "tarik"
    h = _h(sesi, siap)
    assert (h.status, h.gagal_asal, h.dilayani_vps_pada) == (StatusHosting.menunggu_dns, None, None)
    assert h.galat == f"{hu.pesan_ui(e.value)} {hu.PESAN_SALINAN_TIDAK_BERUBAH}"
    assert "Aktifkan tanpa salin ulang" in h.galat and "public_html" not in h.galat
    assert h.ditarik_pada == ditarik and (_files(hosting_aktif, siap) / "index.php").read_bytes() == isi
    assert "prod_aktifkan" not in pb.nama_panggilan()
    # Jalan keluarnya: Aktifkan tanpa salin ulang dari menunggu DNS.
    job.status = JobStatus.failed
    sesi.commit()
    _aktifkan(sesi, siap, {"tanpa_tarik_ulang": True})
    assert _h(sesi, siap).status == StatusHosting.aktif


def test_hosting_lama_terputus_sesudah_salinan_disentuh_tetap_gagal_salinan(sesi, siap, prod, pb, monkeypatch):
    # Batas I2: salinan yang sudah mulai ditimpa tarik terakhir tidak disembunyikan di balik menunggu DNS.
    prod.berkas["index.php"] = (b"<?php // baru", MTIME + 3)

    def http_putus():
        def tangani(r):
            if r.url.path.endswith("/staging/tabel"):
                raise httpx.ConnectError("putus", request=r)
            return prod.tangani(r)

        return httpx.Client(transport=httpx.MockTransport(tangani))

    monkeypatch.setattr(hu, "buat_http_lama", http_putus)
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap, job=job)
    h = _h(sesi, siap)
    assert (h.status, h.gagal_asal) == (StatusHosting.gagal, "salinan")
    assert hu.PESAN_SALINAN_TIDAK_BERUBAH not in (h.galat or "")


def test_ip_lama_kosong_ditolak_sebelum_dns_dan_sertifikat(sesi, siap, pb, lama, dns_palsu):
    # Kolomnya NOT NULL; "kosong" = string kosong (atau alamat yang tidak sah).
    siap.ip_lama = ""
    sesi.commit()
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _aktifkan(sesi, siap)
    assert e.value.pesan == hu.PESAN_IP_LAMA
    h = _h(sesi, siap)
    assert (h.status, h.galat) == (StatusHosting.menunggu_dns, hu.PESAN_IP_LAMA)
    assert pb.panggilan == [] and lama == [] and dns_palsu.n == 0


def test_backoff_sertifikat_menahan_certbot(sesi, siap, pb, lama):
    siap.sertifikat_gagal_kali = 3
    siap.sertifikat_gagal_pada = datetime.now(timezone.utc) - timedelta(hours=1)
    sesi.commit()
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _aktifkan(sesi, siap, job=job)
    assert e.value.pesan == pindah.PESAN_SERTIFIKAT
    assert "prod_sertifikat" not in pb.nama_panggilan() and lama == []
    job.status = JobStatus.failed
    sesi.commit()
    # Jeda tidak dihitung sebagai kegagalan baru.
    assert _h(sesi, siap).sertifikat_gagal_kali == 3
    # Manual: cukup 15 menit sejak kegagalan terakhir (spec §8.4).
    _aktifkan(sesi, siap, {"manual": True})
    assert _h(sesi, siap).status == StatusHosting.aktif and _h(sesi, siap).sertifikat_gagal_kali == 0


@pytest.mark.parametrize("langkah,pesan", [
    ("prod_domain", "Memasang konfigurasi nginx domain gagal. Skrip pembantu menolak permintaan ini."),
    ("prod_sertifikat", "Menerbitkan sertifikat domain gagal. Skrip pembantu menolak permintaan ini."),
])
@pytest.mark.parametrize("sibuk", [True, False])
def test_keluar_3_sebelum_tukar_sibuk_diulang_lainnya_ditolak(sesi, siap, pb, lama, langkah, pesan, sibuk):
    # Final review I1: hanya kunci sibuk (`GalatPembantu.sibuk`) yang diulang sebagai sibuk.
    # Keluar 3 lainnya (vhost sisa, direktori tidak aman, prod-siapkan belum jalan, prasyarat)
    # pasti: gagal sekarang dengan pesan tetap skrip pembantu (tanpa path), tidak 4 jam "menunggu".
    pb.gagal[langkah] = GalatPembantu("ditolak", pesan, sibuk=sibuk)
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    if sibuk:
        with pytest.raises(hu.GalatSibuk):
            _aktifkan(sesi, siap, job=job)
        h = _h(sesi, siap)
        assert (h.status, h.galat) == (StatusHosting.mengaktifkan, hu.PESAN_MENUNGGU_SIBUK)
    else:
        with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
            _aktifkan(sesi, siap, job=job)
        assert not isinstance(e.value, hu.GalatSibuk) and e.value.pesan == pesan
        assert akan_diulang(sesi.get(Job, job.id, populate_existing=True), e.value.error_class) is False
        h = _h(sesi, siap)
        assert (h.status, h.galat, h.gagal_asal) == (StatusHosting.menunggu_dns, pesan, None)
        assert "/" not in h.galat
    # Bukan kegagalan CA: tidak menambah backoff sertifikat; produksi tidak tersentuh.
    assert not h.sertifikat_gagal_kali and lama == [] and h.dilayani_vps_pada is None


def test_prod_domain_gagal_ditolak_tanpa_ubah(sesi, siap, pb, lama):
    pb.gagal["prod_domain"] = GalatPembantu("nginx", "Memasang domain gagal. nginx menolak konfigurasi.")
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _aktifkan(sesi, siap)
    h = _h(sesi, siap)
    assert (h.status, h.galat) == (StatusHosting.menunggu_dns, pindah.PESAN_NGINX)
    assert "prod_sertifikat" not in pb.nama_panggilan() and lama == []


def test_batal_sebelum_tukar_menghentikan_aktivasi(sesi, siap, pb):
    asli = pb.prod_sertifikat

    def minta_batal(nama):
        h = sesi.get(HostingVps, siap.id, populate_existing=True)
        h.batal_diminta_pada = datetime.now(timezone.utc)
        sesi.commit()
        return asli(nama)

    pb.prod_sertifikat = minta_batal
    with pytest.raises(umum.GalatDibatalkan):
        _aktifkan(sesi, siap, {"tanpa_tarik_ulang": True})
    assert "prod_aktifkan" not in pb.nama_panggilan()
    h = _h(sesi, siap)
    assert h.status == StatusHosting.menunggu_dns and h.dilayani_vps_pada is None


def test_verifikasi_gagal_bila_wpmgr_pratinjau_tersisa(sesi, siap, pb, halaman):
    # prod-aktifkan "berhasil" tetapi wp-config.php masih mode pratinjau.
    pb.prod_aktifkan = lambda nama: pb._catat("prod_aktifkan", nama)
    with pytest.raises(SiteError) as e:
        _aktifkan(sesi, siap)
    assert e.value.error_class == TRANSIENT and e.value.pesan == pindah.PESAN_VERIFIKASI
    h = _h(sesi, siap)
    assert h.status == StatusHosting.mengaktifkan
    assert h.galat == f"Terputus, dilanjutkan otomatis: {pindah.PESAN_VERIFIKASI}"


@pytest.mark.parametrize("lokasi", ["https://toko.co.id/", "https://TOKO.co.id:443/", "/", "https://toko.co.id",
                                    "https://toko.co.id/?dari=htaccess"])
def test_verifikasi_gagal_untuk_alihan_ke_alamat_sendiri(sesi, siap, monkeypatch, lokasi):
    # Final review M1: .htaccess paksa-HTTPS lewat %{HTTPS} berputar di dalam container (TLS
    # berhenti di nginx host); 3xx ke URL yang sama (skema, host, path) bukan situs yang menjawab.
    def halaman(host):
        return 301, {"location": lokasi if host == "toko.co.id" else "https://toko.co.id/"}

    monkeypatch.setattr(pindah, "ambil_halaman_verifikasi", halaman)
    with pytest.raises(SiteError) as e:
        _aktifkan(sesi, siap)
    assert e.value.error_class == TRANSIENT and e.value.pesan == pindah.PESAN_ALIHAN_BERPUTAR
    assert ".htaccess" in pindah.PESAN_ALIHAN_BERPUTAR
    h = _h(sesi, siap)
    assert h.status == StatusHosting.mengaktifkan
    assert h.galat == f"Terputus, dilanjutkan otomatis: {pindah.PESAN_ALIHAN_BERPUTAR}"


def test_verifikasi_menerima_alihan_ke_alamat_lain(sesi, siap, monkeypatch):
    # www -> apex dan apex -> halaman lain bukan putaran.
    def halaman(host):
        return (301, {"location": "https://toko.co.id/"}) if host == "www.toko.co.id" \
            else (302, {"location": "https://toko.co.id/beranda/"})

    monkeypatch.setattr(pindah, "ambil_halaman_verifikasi", halaman)
    _aktifkan(sesi, siap)
    assert _h(sesi, siap).status == StatusHosting.aktif


@pytest.mark.parametrize("jawaban", [(401, {}), (500, {}), (200, {"x-robots-tag": "noindex, nofollow"}), None])
def test_verifikasi_gagal_untuk_401_500_noindex_atau_tanpa_jawaban(sesi, siap, halaman, jawaban):
    halaman.jawaban = jawaban
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap)
    assert _h(sesi, siap).status == StatusHosting.mengaktifkan


def _sudah_dilayani(sesi, siap, status, asal=None):
    siap.status = status
    siap.gagal_asal = asal
    siap.dilayani_vps_pada = datetime.now(timezone.utc) - timedelta(days=2)
    sesi.commit()
    (_files(hu.get_settings().jalur_hosting, siap) / "wp-config.php").write_bytes(b"<?php // aktif")


def test_periksa_ulang_langsung_verifikasi(sesi, siap, pb, lama, dns_palsu, halaman):
    # Putusan L16: dari gagal (asal produksi), prod-aktifkan dikirim ulang
    # (idempoten di bawah MODE=aktif) lalu verifikasi; tanpa DNS/sertifikat/tarik.
    _sudah_dilayani(sesi, siap, StatusHosting.gagal, "produksi")
    job, _ = _aktifkan(sesi, siap)
    assert pb.nama_panggilan() == ["prod_aktifkan"] and lama == [] and dns_palsu.n == 0
    assert halaman.host == ["toko.co.id", "www.toko.co.id"]
    k = umum.kemajuan(job)
    tukar = datetime.fromisoformat(k["tukar_pada"])
    assert datetime.now(timezone.utc) - tukar < timedelta(minutes=5)
    h = _h(sesi, siap)
    assert (h.status, h.gagal_asal) == (StatusHosting.aktif, None)


def test_periksa_ulang_keluar_3_tetap_produksi_tersentuh(sesi, siap, hosting_aktif, pb, halaman):
    # Situs sudah (setengah) aktif: keluar 3 kiriman ulang bukan "tanpa perubahan".
    _sudah_dilayani(sesi, siap, StatusHosting.gagal, "produksi")
    pb.gagal["prod_aktifkan"] = _ditolak_sibuk()
    with pytest.raises(SiteError) as e:
        _aktifkan(sesi, siap)
    assert e.value.error_class == TRANSIENT and not isinstance(e.value, hu.GalatSibuk)
    h = _h(sesi, siap)
    assert h.dilayani_vps_pada is not None and h.status == StatusHosting.mengaktifkan
    assert not (_files(hosting_aktif, siap) / MU).exists()


def test_periksa_ulang_dari_aktif_hanya_verifikasi(sesi, siap, pb, lama, dns_palsu, halaman):
    _sudah_dilayani(sesi, siap, StatusHosting.aktif)
    _aktifkan(sesi, siap)
    assert pb.panggilan == [] and lama == [] and dns_palsu.n == 0
    assert halaman.host == ["toko.co.id", "www.toko.co.id"]
    assert _h(sesi, siap).status == StatusHosting.aktif


def test_periksa_ulang_dari_status_lain_ditolak(sesi, siap, pb, lama, dns_palsu, halaman):
    _sudah_dilayani(sesi, siap, StatusHosting.menunggu_dns)
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _aktifkan(sesi, siap)
    assert e.value.pesan == pindah.PESAN_PERIKSA_ULANG
    assert pb.panggilan == [] and lama == [] and dns_palsu.n == 0 and halaman.host == []
    h = _h(sesi, siap)
    assert (h.status, h.galat) == (StatusHosting.menunggu_dns, pindah.PESAN_PERIKSA_ULANG)


def test_batal_hanya_berlaku_sebelum_tukar():
    def job(langkah):
        return Job(tipe=JobType.pindah_aktifkan, payload={"kemajuan": {"langkah_aktifkan": langkah}})

    assert pindah.boleh_batal_aktifkan(job(None)) and pindah.boleh_batal_aktifkan(job("tarik"))
    assert not pindah.boleh_batal_aktifkan(job("tukar")) and not pindah.boleh_batal_aktifkan(job("verifikasi"))


def test_status_awal_pratinjau_ditolak(sesi, siap, pb):
    siap.status = StatusHosting.pratinjau
    sesi.commit()
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _aktifkan(sesi, siap)
    assert e.value.pesan == pindah.PESAN_STATUS_AKTIFKAN
    assert _h(sesi, siap).status == StatusHosting.pratinjau and pb.panggilan == []
