from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from wpmgr.errors import (
    AUTH_ERROR,
    BAD_RESPONSE,
    STAGING_DITOLAK,
    STAGING_GAGAL,
    TRANSIENT,
    SiteError,
)
from wpmgr.hosting import umum as hu
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import (
    _masih_eksklusif,
    akan_diulang,
    ambil_job,
    buat_job,
    dalam_batas_pemulihan,
    dalam_batas_sibuk,
    menyentuh_produksi,
    selesai_gagal,
)
from wpmgr.jobs.reaper import pulihkan_job_yatim
from wpmgr.models import (
    ActivityLog,
    HostingVps,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
    StatusHosting,
)
from wpmgr.staging import umum as stg
from wpmgr.staging.pembantu import GalatPembantu
from wpmgr.worker import proses_satu

pytestmark = pytest.mark.integration

SEKARANG = datetime.now(timezone.utc)


def _berjalan(sesi, site, tipe, oleh="w-lain", payload=None):
    job = buat_job(sesi, site.id, tipe, payload)
    job.status = JobStatus.running
    job.locked_by = oleh
    job.locked_at = datetime.now(timezone.utc)
    sesi.commit()
    return job


def _h(sesi, site_hosting):
    return sesi.get(HostingVps, site_hosting.id, populate_existing=True)


# ---- antrean ----------------------------------------------------------------------


def test_pindah_tarik_berjalan_bersama_scan(sesi, site):
    _berjalan(sesi, site, JobType.scan_site)
    j = buat_job(sesi, site.id, JobType.pindah_tarik)
    assert ambil_job(sesi, "w1", "staging").id == j.id


@pytest.mark.parametrize("tipe", [JobType.pindah_aktifkan, JobType.backup_hosting])
def test_aktifkan_dan_backup_eksklusif(sesi, site, tipe):
    _berjalan(sesi, site, JobType.scan_site)
    buat_job(sesi, site.id, tipe)
    assert ambil_job(sesi, "w1", "staging") is None


def test_job_runtime_diserialkan_antara_staging_dan_hosting(sesi, site):
    _berjalan(sesi, site, JobType.staging_tarik)
    buat_job(sesi, site.id, JobType.pindah_tarik)
    assert ambil_job(sesi, "w1", "staging") is None


def test_worker_umum_tidak_mengklaim_job_hosting(sesi, site):
    j = buat_job(sesi, site.id, JobType.pindah_tarik)
    assert ambil_job(sesi, "w1", "umum") is None
    assert ambil_job(sesi, "w1", "staging").id == j.id


def test_aktifkan_sesudah_tukar_menahan_job_umum(sesi, site):
    j = buat_job(sesi, site.id, JobType.pindah_aktifkan,
                 {"kemajuan": {"langkah_aktifkan": "tukar", "tukar_pada": SEKARANG.isoformat()}})
    j.scheduled_for = SEKARANG + timedelta(minutes=10)
    sesi.commit()
    scan = buat_job(sesi, site.id, JobType.scan_site)
    assert ambil_job(sesi, "w1", "umum") is None
    j.payload = {"kemajuan": {"langkah_aktifkan": "tarik"}}
    flag_modified(j, "payload")
    sesi.commit()
    assert ambil_job(sesi, "w1", "umum").id == scan.id


@pytest.mark.parametrize("langkah,hasil", [
    ("tukar", True), ("verifikasi", True), ("beres", True), ("dns", False), ("sertifikat", False),
    ("tarik", False), (None, False),
])
def test_menyentuh_produksi_aktifkan(langkah, hasil):
    job = Job(tipe=JobType.pindah_aktifkan, payload={"kemajuan": {"langkah_aktifkan": langkah}})
    assert menyentuh_produksi(job) is hasil
    assert menyentuh_produksi(Job(tipe=JobType.backup_hosting, payload={"kemajuan": {"langkah_aktifkan": "tukar"}})) \
        is False


def test_batas_pemulihan_24_jam_aktifkan():
    def job(jam):
        return Job(tipe=JobType.pindah_aktifkan, payload={"kemajuan": {
            "langkah_aktifkan": "verifikasi", "tukar_pada": (SEKARANG - timedelta(hours=jam)).isoformat()}})

    assert dalam_batas_pemulihan(job(23)) is True
    assert dalam_batas_pemulihan(job(25)) is False


def test_titik_potongan_membaca_batal_hosting(sesi, site_hosting):
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    stg.titik_potongan(sesi, job, site_hosting)
    site_hosting.batal_diminta_pada = SEKARANG
    sesi.commit()
    with pytest.raises(stg.Dibatalkan):
        stg.titik_potongan(sesi, job, site_hosting)


# ---- pembungkus jalankan_hosting ----------------------------------------------------


def test_muat_hosting_fitur_mati(sesi, site):
    job = buat_job(sesi, site.id, JobType.pindah_tarik)
    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, lambda *a: {}, "Salin ke VPS")
    assert e.value.error_class == STAGING_DITOLAK
    assert e.value.pesan == hu.PESAN_FITUR_MATI


def test_sukses_membersihkan_galat(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    site_hosting.galat = "lama"
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)

    def inti(sesi, job, site, h):
        assert h.status == StatusHosting.menyalin
        h.status = StatusHosting.pratinjau
        sesi.commit()
        return {"ok": 1}

    assert hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS") == {"ok": 1}
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat, h.gagal_asal) == (StatusHosting.pratinjau, None, None)
    assert stg.kemajuan(job)["status_hosting_awal"] == "pratinjau"


def test_ditolak_tanpa_ubah_mengembalikan_status_awal(sesi, site_hosting):
    site_hosting.status = StatusHosting.menunggu_dns
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_aktifkan)

    def inti(sesi, job, site, h):
        raise stg.GalatDitolakTanpaUbah("DNS belum menunjuk VPS.")

    with pytest.raises(stg.GalatDitolakTanpaUbah):
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat, h.gagal_asal) == (StatusHosting.menunggu_dns, "DNS belum menunjuk VPS.", None)


def test_galat_pembantu_sebelum_tukar_final_gagal_salinan(sesi, site_hosting):
    site_hosting.status = StatusHosting.menunggu_dns
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_aktifkan)

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, langkah_aktifkan="tarik")
        raise GalatPembantu("docker", "Membuat container situs gagal. Perintah Docker di server staging gagal.")

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    assert e.value.error_class == STAGING_GAGAL
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal) == (StatusHosting.gagal, "salinan")
    assert h.galat == "Membuat container situs gagal. Perintah Docker di server staging gagal."


def _sesudah_tukar(sesi, site_hosting, jam_lalu=0.0, attempts=3):
    site_hosting.status = StatusHosting.menunggu_dns
    site_hosting.dilayani_vps_pada = SEKARANG - timedelta(hours=jam_lalu)
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_aktifkan, {"kemajuan": {
        "langkah_aktifkan": "tukar", "tukar_pada": (SEKARANG - timedelta(hours=jam_lalu)).isoformat(),
        "status_hosting_awal": "menunggu_dns"}})
    job.attempts = attempts
    sesi.commit()
    return job


def test_galat_pembantu_sesudah_tukar_diulang_melewati_max_attempts(sesi, site_hosting):
    job = _sesudah_tukar(sesi, site_hosting)

    def inti(sesi, job, site, h):
        raise GalatPembantu("waktu", "Skrip pembantu tidak selesai dalam 300 detik.")

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    assert e.value.error_class == TRANSIENT
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.mengaktifkan
    assert h.galat == "Terputus, dilanjutkan otomatis: Skrip pembantu tidak selesai dalam 300 detik."


def test_lewat_24_jam_menjadi_gagal_produksi_dengan_pesan_tetap(sesi, site_hosting):
    job = _sesudah_tukar(sesi, site_hosting, jam_lalu=25)

    def inti(sesi, job, site, h):
        raise SiteError(TRANSIENT, "Situs belum menjawab HTTPS dengan benar.")

    with pytest.raises(SiteError):
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "produksi", hu.PESAN_PRODUKSI_GAGAL)


def test_batal_sebelum_inti_mengembalikan_status_awal(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    site_hosting.batal_diminta_pada = SEKARANG
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    with pytest.raises(stg.GalatDibatalkan):
        hu.jalankan_hosting(sesi, job, lambda *a: pytest.fail("inti tidak boleh berjalan"), "Salin ke VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.batal_diminta_pada) == (StatusHosting.pratinjau, None)
    assert sesi.scalar(select(ActivityLog.pesan).where(ActivityLog.job_id == job.id)) == "Salin ke VPS dibatalkan"


def test_batal_sesudah_salinan_disentuh_menandai_gagal_salinan(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, tahap="berkas")
        raise stg.Dibatalkan()

    with pytest.raises(stg.GalatDibatalkan):
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", hu.PESAN_BATAL_TENGAH)


def test_backup_gagal_final_tidak_mengubah_status(sesi, site_hosting):
    site_hosting.status = StatusHosting.aktif
    site_hosting.dilayani_vps_pada = SEKARANG
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.backup_hosting)
    job.attempts = job.max_attempts
    sesi.commit()

    def inti(sesi, job, site, h):
        raise GalatPembantu("backup", "Membuat backup situs gagal. Backup situs gagal dibuat.")

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Backup situs")
    assert e.value.error_class == TRANSIENT
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.aktif and h.galat is None
    assert h.backup_gagal_pada is not None


def test_galat_connector_tidak_bocor_ke_ui_dan_site_tidak_disentuh(sesi, site_hosting, monkeypatch):
    mentah = "<html>Fatal error in /home/u123/public_html/wp-config.php</html>"

    def inti(sesi, job, site, h):
        raise SiteError(BAD_RESPONSE, mentah)

    monkeypatch.setitem(handlers.HANDLER, JobType.pindah_tarik,
                        lambda sesi, job, klien: hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS"))
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.expire_all()
    h = _h(sesi, site_hosting)
    j = sesi.get(Job, job.id)
    assert h.galat == f"Terputus, dilanjutkan otomatis: {hu.PESAN_KELAS[BAD_RESPONSE]}"
    assert "public_html" not in (j.error or "")
    for log in sesi.scalars(select(ActivityLog)).all():
        assert "public_html" not in log.pesan and "public_html" not in str(log.detail)
    site = sesi.get(Site, site_hosting.site_id)
    assert site.status == SiteStatus.active and site.last_error is None


def test_pesan_ui_meneruskan_pesan_tetap_milik_hosting():
    pesan = "Situs belum menjawab HTTPS dengan benar."
    assert hu.pesan_ui(hu.GalatHosting(TRANSIENT, pesan)) == pesan
    assert hu.pesan_ui(SiteError(TRANSIENT, "koneksi gagal: [Errno 111] 10.0.0.5")) == hu.PESAN_KELAS[TRANSIENT]
    dari_connector = SiteError(STAGING_GAGAL, "pesan connector", kode="wpmgr_staging_path")
    assert hu.pesan_ui(dari_connector) == hu.PESAN_KODE[STAGING_GAGAL]
    assert hu.pesan_ui(stg.galat_gagal("Manifest produksi melebihi batas.")) == "Manifest produksi melebihi batas."


def test_kegagalan_akhir_hosting_tidak_mengubah_status_site(sesi, site_hosting, monkeypatch):
    def inti(sesi, job, site, h):
        raise SiteError(AUTH_ERROR, "403 dari hosting lama")

    monkeypatch.setitem(handlers.HANDLER, JobType.pindah_tarik,
                        lambda sesi, job, klien: hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS"))
    buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.expire_all()
    site = sesi.get(Site, site_hosting.site_id)
    assert site.status == SiteStatus.active and site.last_error is None
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat) == (StatusHosting.gagal, hu.PESAN_KELAS[AUTH_ERROR])


# ---- reaper ----------------------------------------------------------------------------


def _yatim(sesi, site_hosting, tipe, payload=None, attempts=None):
    job = buat_job(sesi, site_hosting.site_id, tipe, payload)
    job = ambil_job(sesi, "w-mati", "staging")
    job.attempts = job.max_attempts if attempts is None else attempts
    job.locked_at = SEKARANG - timedelta(minutes=30)
    sesi.commit()
    return job


def test_reaper_melepas_hosting_menyalin(sesi, site_hosting):
    site_hosting.status = StatusHosting.menyalin
    sesi.commit()
    job = _yatim(sesi, site_hosting, JobType.pindah_tarik)
    assert pulihkan_job_yatim(sesi) == 1
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.unknown
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", hu.PESAN_TERHENTI)


def test_reaper_aktifkan_sesudah_tukar_dalam_24_jam_diulang(sesi, site_hosting):
    site_hosting.status = StatusHosting.mengaktifkan
    site_hosting.dilayani_vps_pada = SEKARANG
    sesi.commit()
    job = _yatim(sesi, site_hosting, JobType.pindah_aktifkan,
                 {"kemajuan": {"langkah_aktifkan": "tukar", "tukar_pada": SEKARANG.isoformat()}})
    pulihkan_job_yatim(sesi)
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.pending
    assert _h(sesi, site_hosting).status == StatusHosting.mengaktifkan


def test_reaper_aktifkan_sesudah_24_jam_gagal_produksi(sesi, site_hosting):
    site_hosting.status = StatusHosting.mengaktifkan
    site_hosting.dilayani_vps_pada = SEKARANG - timedelta(hours=30)
    sesi.commit()
    _yatim(sesi, site_hosting, JobType.pindah_aktifkan, {"kemajuan": {
        "langkah_aktifkan": "verifikasi", "tukar_pada": (SEKARANG - timedelta(hours=30)).isoformat()}})
    pulihkan_job_yatim(sesi)
    sesi.expire_all()
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "produksi", hu.PESAN_PRODUKSI_GAGAL)


def test_reaper_backup_menandai_backup_gagal(sesi, site_hosting):
    site_hosting.status = StatusHosting.aktif
    site_hosting.dilayani_vps_pada = SEKARANG
    sesi.commit()
    _yatim(sesi, site_hosting, JobType.backup_hosting)
    pulihkan_job_yatim(sesi)
    sesi.expire_all()
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.aktif and h.backup_gagal_pada is not None


# ---- tambahan: eksklusi job staging <-> hosting di site yang sama -------------------
# Indeks unik staging dan hosting terpisah (uq_jobs_staging_aktif, uq_jobs_hosting_aktif),
# jadi satu job staging dan satu job hosting bisa sama-sama tertunda; yang menjamin
# keduanya tidak pernah BERJALAN bersamaan hanyalah klaim (SQL_AMBIL dan SQL_BENTROK).


@pytest.mark.parametrize("berjalan,kandidat", [
    (JobType.staging_tarik, JobType.pindah_tarik),
    (JobType.staging_uji_update, JobType.backup_hosting),
    (JobType.staging_dorong, JobType.pindah_aktifkan),
    (JobType.staging_kembalikan, JobType.pindah_tarik),
    (JobType.pindah_tarik, JobType.staging_tarik),
    (JobType.pindah_tarik, JobType.staging_uji_update),
    (JobType.pindah_aktifkan, JobType.staging_dorong),
    (JobType.backup_hosting, JobType.staging_kembalikan),
])
def test_job_staging_dan_hosting_tidak_pernah_berjalan_bersamaan(sesi, site, berjalan, kandidat):
    _berjalan(sesi, site, berjalan)
    j = buat_job(sesi, site.id, kandidat)
    assert ambil_job(sesi, "w1", "staging") is None
    # Pemeriksaan ulang di transaksi klaim memakai aturan yang sama.
    assert _masih_eksklusif(sesi, j.id, site.id) is False
    sesi.rollback()


def test_job_staging_dan_hosting_tertunda_hanya_satu_yang_diklaim(sesi, site):
    a = buat_job(sesi, site.id, JobType.staging_tarik)
    b = buat_job(sesi, site.id, JobType.pindah_tarik)
    assert ambil_job(sesi, "w1", "staging").id in (a.id, b.id)
    assert ambil_job(sesi, "w2", "staging") is None


# ---- tambahan: penolakan berkode connector (preflight M5) ------------------------------


def test_penolakan_tanpa_ubah_berkode_connector_memakai_pesan_tetap(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)

    def inti(sesi, job, site, h):
        g = stg.GalatDitolakTanpaUbah("teks connector /home/u123/public_html")
        g.kode = "wpmgr_staging_ditahan"
        raise g

    with pytest.raises(stg.GalatDitolakTanpaUbah) as e:
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    assert e.value.pesan == hu.PESAN_KODE[STAGING_DITOLAK]
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat) == (StatusHosting.pratinjau, hu.PESAN_KODE[STAGING_DITOLAK])


# ---- tambahan: skrip pembantu menolak/sibuk (keluar 3, GalatPembantu.tanpa_ubah) -------
# Kunci router bisa dipegang sampai 3 jam selama impor database situs lain: penolakan
# seperti ini dijadwalkan ulang dengan jeda, tidak menghabiskan jatah percobaan, dan
# tidak pernah menandai baris hosting `gagal` selama jendela sibuk.

SIBUK = "Mengimpor database situs gagal. Skrip pembantu menolak permintaan ini."


def _sibuk():
    return GalatPembantu("ditolak", SIBUK)


def _lewat_worker(monkeypatch, tipe, inti, nama="Salin ke VPS"):
    monkeypatch.setitem(handlers.HANDLER, tipe, lambda sesi, job, klien: hu.jalankan_hosting(sesi, job, inti, nama))


def test_dalam_batas_sibuk():
    def job(jam, kali=1):
        return Job(tipe=JobType.pindah_tarik, payload={"kemajuan": {
            "sibuk_sejak": (SEKARANG - timedelta(hours=jam)).isoformat(), "sibuk_kali": kali}})

    assert dalam_batas_sibuk(job(3.5)) is True
    assert dalam_batas_sibuk(job(4.5)) is False
    assert dalam_batas_sibuk(job(1, kali=0)) is False
    assert dalam_batas_sibuk(Job(tipe=JobType.pindah_tarik, payload={})) is False


@pytest.mark.parametrize("tipe,status_awal,status_kerja", [
    (JobType.pindah_tarik, StatusHosting.pratinjau, StatusHosting.menyalin),
    (JobType.pindah_aktifkan, StatusHosting.menunggu_dns, StatusHosting.mengaktifkan),
])
def test_pembantu_sibuk_dijadwalkan_ulang_tanpa_menghabiskan_jatah(sesi, site_hosting, monkeypatch, tipe,
                                                                    status_awal, status_kerja):
    site_hosting.status = status_awal
    sesi.commit()

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, tahap="impor", langkah_aktifkan="tarik")
        raise _sibuk()

    _lewat_worker(monkeypatch, tipe, inti)
    job = buat_job(sesi, site_hosting.site_id, tipe)
    maks = job.max_attempts
    job.attempts = maks - 1  # klaim berikut adalah percobaan terakhir
    sesi.commit()
    assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.expire_all()
    j = sesi.get(Job, job.id)
    assert j.status == JobStatus.pending
    assert j.attempts == maks - 1
    assert j.error_class == TRANSIENT
    assert j.scheduled_for - datetime.now(timezone.utc) > timedelta(minutes=1)
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal) == (status_kerja, None)
    assert h.galat == f"Terputus, dilanjutkan otomatis: {SIBUK}"
    site = sesi.get(Site, site_hosting.site_id)
    assert site.status == SiteStatus.active and site.last_error is None


@pytest.mark.parametrize("kali,menit", [(1, 2), (2, 4), (3, 8), (4, 15), (9, 15)])
def test_jeda_sibuk_bertambah_dan_dibatasi(sesi, site, kali, menit):
    job = _berjalan(sesi, site, JobType.pindah_tarik, payload={"kemajuan": {
        "sibuk_sejak": SEKARANG.isoformat(), "sibuk_kali": kali}})
    selesai_gagal(sesi, job, TRANSIENT, SIBUK)
    jeda = job.scheduled_for - sesi.scalar(select(func.now()))
    assert timedelta(minutes=menit) - timedelta(seconds=30) < jeda <= timedelta(minutes=menit)


def test_pembantu_sibuk_lewat_batas_kembali_ke_status_sebelum(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik, {"kemajuan": {
        "sibuk_sejak": (SEKARANG - timedelta(hours=5)).isoformat(), "sibuk_kali": 20}})

    def inti(sesi, job, site, h):
        raise _sibuk()

    with pytest.raises(stg.GalatDitolakTanpaUbah) as e:
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    assert e.value.pesan == SIBUK
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.pratinjau, None, SIBUK)


def test_pembantu_sibuk_lewat_batas_sesudah_salinan_disentuh_gagal_salinan(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik, {"kemajuan": {
        "sibuk_sejak": (SEKARANG - timedelta(hours=5)).isoformat(), "sibuk_kali": 20}})

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, tahap="impor")
        raise _sibuk()

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    assert e.value.error_class == STAGING_GAGAL
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", SIBUK)


def test_backup_sibuk_tidak_menandai_backup_gagal(sesi, site_hosting):
    site_hosting.status = StatusHosting.aktif
    site_hosting.dilayani_vps_pada = SEKARANG
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.backup_hosting)
    job.attempts = job.max_attempts
    sesi.commit()

    def inti(sesi, job, site, h):
        raise _sibuk()

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Backup situs")
    assert e.value.error_class == TRANSIENT
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat, h.backup_gagal_pada) == (StatusHosting.aktif, None, None)
    j = sesi.get(Job, job.id, populate_existing=True)
    assert j.attempts == j.max_attempts - 1 and akan_diulang(j, TRANSIENT)


def test_galat_lain_sesudah_sibuk_mengakhiri_rentetan(sesi, site_hosting):
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik, {"kemajuan": {
        "sibuk_sejak": SEKARANG.isoformat(), "sibuk_kali": 3}})

    def inti(sesi, job, site, h):
        raise SiteError(TRANSIENT, "koneksi putus")

    with pytest.raises(SiteError):
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    j = sesi.get(Job, job.id, populate_existing=True)
    assert dalam_batas_sibuk(j) is False
    assert not stg.kemajuan(j).get("sibuk_kali")


def test_pembantu_sibuk_sesudah_tukar_mengikuti_r26(sesi, site_hosting):
    job = _sesudah_tukar(sesi, site_hosting)

    def inti(sesi, job, site, h):
        raise _sibuk()

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    assert e.value.error_class == TRANSIENT
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat) == (StatusHosting.mengaktifkan, f"Terputus, dilanjutkan otomatis: {SIBUK}")


def test_batal_menang_atas_pembantu_sibuk(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)

    def inti(sesi, job, site, h):
        h.batal_diminta_pada = SEKARANG
        sesi.commit()
        raise _sibuk()

    with pytest.raises(stg.GalatDibatalkan):
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.batal_diminta_pada) == (StatusHosting.pratinjau, None)

