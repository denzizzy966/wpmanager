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
        # prod-buat gagal di tahap impor tarik terakhir: salinan VPS sudah disentuh.
        stg.simpan_kemajuan(sesi, job, langkah_aktifkan="tarik", tahap="impor")
        raise GalatPembantu("docker", "Membuat container situs gagal. Perintah Docker di server staging gagal.")

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    assert e.value.error_class == STAGING_GAGAL
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal) == (StatusHosting.gagal, "salinan")
    assert h.galat == "Membuat container situs gagal. Perintah Docker di server staging gagal."


@pytest.mark.parametrize("langkah,tahap,akhiran", [("dns", None, False), ("tarik", None, True),
                                                   ("tarik", "manifest", True)])
def test_gagal_final_sebelum_salinan_disentuh_kembali_ke_status_awal(sesi, site_hosting, langkah, tahap, akhiran):
    # Final review I2: kegagalan final sebelum tukar yang tidak menyentuh salinan VPS bukan `gagal`
    # 'salinan' (yang menolak Aktifkan tanpa salin ulang); status kembali seperti sebelum job.
    site_hosting.status = StatusHosting.menunggu_dns
    site_hosting.ditarik_pada = SEKARANG
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_aktifkan)
    job.attempts = job.max_attempts
    sesi.commit()

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, langkah_aktifkan=langkah, tahap=tahap)
        raise SiteError(TRANSIENT, "koneksi ke 10.0.0.5 putus")

    with pytest.raises(SiteError):
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal) == (StatusHosting.menunggu_dns, None)
    harapan = hu.PESAN_KELAS[TRANSIENT]
    if akhiran:
        harapan = f"{harapan} {hu.PESAN_SALINAN_TIDAK_BERUBAH}"
    assert h.galat == harapan


def test_salin_ulang_gagal_final_sebelum_salinan_disentuh_tetap_pratinjau(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    site_hosting.ditarik_pada = SEKARANG
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    job.attempts = job.max_attempts
    sesi.commit()

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, tahap="manifest")
        raise SiteError(TRANSIENT, "koneksi putus")

    with pytest.raises(SiteError):
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.pratinjau, None, hu.PESAN_KELAS[TRANSIENT])


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


def test_reaper_aktifkan_sebelum_salinan_disentuh_kembali_menunggu_dns(sesi, site_hosting):
    # Final review I2: aturan status reaper sama dengan pembungkus (`status_gagal_final`).
    site_hosting.status = StatusHosting.mengaktifkan
    site_hosting.ditarik_pada = SEKARANG
    sesi.commit()
    job = _yatim(sesi, site_hosting, JobType.pindah_aktifkan, {"kemajuan": {
        "langkah_aktifkan": "tarik", "tahap": "manifest", "status_hosting_awal": "menunggu_dns"}})
    assert pulihkan_job_yatim(sesi) == 1
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.unknown
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal) == (StatusHosting.menunggu_dns, None)
    assert h.galat == f"{hu.PESAN_TERHENTI} {hu.PESAN_SALINAN_TIDAK_BERUBAH}"


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
    # Penanda kunci sibuk di stderr skrip (`GalatPembantu.sibuk`); keluar 3 lainnya pasti (final review I1).
    return GalatPembantu("ditolak", SIBUK, sibuk=True)


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
    assert h.galat == hu.PESAN_MENUNGGU_SIBUK
    site = sesi.get(Site, site_hosting.site_id)
    assert site.status == SiteStatus.active and site.last_error is None


@pytest.mark.parametrize("tipe,status_awal", [
    (JobType.pindah_tarik, StatusHosting.pratinjau),
    (JobType.pindah_aktifkan, StatusHosting.menunggu_dns),
])
def test_pembantu_menolak_bukan_sibuk_langsung_final_tanpa_ubah(sesi, site_hosting, monkeypatch, tipe,
                                                                 status_awal):
    # Final review I1 (pola backup._tolak_permanen): keluar 3 tanpa penanda kunci sibuk (vhost sisa,
    # direktori tidak aman, prod-siapkan belum jalan, domain dipakai situs lain) pasti; tidak
    # diulang 4 jam dengan pesan "menunggu proses lain".
    site_hosting.status = status_awal
    site_hosting.ditarik_pada = SEKARANG
    sesi.commit()
    pesan = "Membuat container situs gagal. Skrip pembantu menolak permintaan ini."

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, langkah_aktifkan="sertifikat")
        raise GalatPembantu("ditolak", pesan)

    _lewat_worker(monkeypatch, tipe, inti)
    job = buat_job(sesi, site_hosting.site_id, tipe)
    assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.expire_all()
    j = sesi.get(Job, job.id)
    assert j.status == JobStatus.failed and j.attempts == 1 and j.error == pesan
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (status_awal, None, pesan)
    assert not stg.kemajuan(j).get("sibuk_kali")


def test_pembantu_menolak_bukan_sibuk_sesudah_salinan_disentuh_gagal_salinan(sesi, site_hosting):
    # Penolakan pasti tidak menyembunyikan salinan setengah jadi di balik status siap.
    site_hosting.status = StatusHosting.pratinjau
    site_hosting.ditarik_pada = SEKARANG
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, tahap="impor")
        raise GalatPembantu("ditolak", SIBUK)

    with pytest.raises(SiteError):
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", SIBUK)


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
        "sibuk_sejak": (SEKARANG - timedelta(hours=5)).isoformat(), "sibuk_kali": 20, "sibuk_langkah": "-/-"}})

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
        "sibuk_sejak": (SEKARANG - timedelta(hours=5)).isoformat(), "sibuk_kali": 20, "sibuk_langkah": "-/impor"}})

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


# ---- fix round 1: job tertunda setengah jalan menahan keluarga runtime lain (I1/L11) --


def _tertunda(sesi, site, tipe, **kemajuan):
    job = buat_job(sesi, site.id, tipe, {"kemajuan": kemajuan})
    job.attempts = 1
    job.scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=10)
    sesi.commit()
    return job


@pytest.mark.parametrize("menahan,kemajuan,kandidat", [
    (JobType.staging_dorong, {"langkah_terapkan": "tukar"}, JobType.pindah_tarik),
    (JobType.staging_dorong, {"unggah_mulai": True}, JobType.pindah_aktifkan),
    (JobType.staging_kembalikan, {"langkah_terapkan": "pulihkan"}, JobType.backup_hosting),
    (JobType.pindah_aktifkan, {"langkah_aktifkan": "tukar"}, JobType.staging_tarik),
    (JobType.pindah_aktifkan, {"langkah_aktifkan": "verifikasi"}, JobType.staging_dorong),
    (JobType.pindah_aktifkan, {"langkah_aktifkan": "beres"}, JobType.staging_uji_update),
])
def test_tertunda_sesudah_tukar_menahan_keluarga_runtime_lain(sesi, site, menahan, kemajuan, kandidat):
    _tertunda(sesi, site, menahan, **kemajuan)
    j = buat_job(sesi, site.id, kandidat)
    assert ambil_job(sesi, "w1", "staging") is None
    assert _masih_eksklusif(sesi, j.id, site.id) is False
    sesi.rollback()


@pytest.mark.parametrize("menahan,kemajuan,kandidat", [
    (JobType.staging_dorong, {"tahap_dorong": "rencana"}, JobType.pindah_tarik),
    (JobType.pindah_aktifkan, {"langkah_aktifkan": "tarik"}, JobType.staging_tarik),
])
def test_tertunda_sebelum_tukar_tidak_menahan_keluarga_lain(sesi, site, menahan, kemajuan, kandidat):
    _tertunda(sesi, site, menahan, **kemajuan)
    j = buat_job(sesi, site.id, kandidat)
    assert ambil_job(sesi, "w1", "staging").id == j.id


def test_klaim_lintas_keluarga_diserialkan_lintas_sesi(engine, sesi, site):
    """Dua sesi: B (dorong staging) memeriksa ulang sesudah klaim A (aktivasi hosting) di-commit, dan melihatnya."""
    import threading
    import time

    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    aktifkan = buat_job(sesi, site.id, JobType.pindah_aktifkan)
    dorong = buat_job(sesi, site.id, JobType.staging_dorong)
    buat = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    a, b = buat(), buat()
    hasil = {}
    try:
        a.execute(text("UPDATE jobs SET status = 'running', locked_by = 'A' WHERE id = :i"), {"i": aktifkan.id})
        assert _masih_eksklusif(a, aktifkan.id, site.id)

        def klaim_b():
            b.execute(text("UPDATE jobs SET status = 'running', locked_by = 'B' WHERE id = :i"), {"i": dorong.id})
            hasil["b"] = _masih_eksklusif(b, dorong.id, site.id)

        utas = threading.Thread(target=klaim_b)
        utas.start()
        time.sleep(0.5)
        assert utas.is_alive(), "B harus menunggu lock site yang dipegang A"
        a.commit()
        utas.join(5)
        assert hasil["b"] is False
    finally:
        b.rollback()
        a.rollback()
        a.close()
        b.close()


# ---- fix round 1: tes antrean tambahan (M5) ----------------------------------------


@pytest.mark.parametrize("tipe", [JobType.pindah_aktifkan, JobType.backup_hosting])
def test_job_hosting_berjalan_menahan_job_lapis1(sesi, site, tipe):
    _berjalan(sesi, site, tipe)
    scan = buat_job(sesi, site.id, JobType.scan_site)
    assert ambil_job(sesi, "w1", "umum") is None
    assert _masih_eksklusif(sesi, scan.id, site.id) is False
    sesi.rollback()


def test_aktifkan_tertunda_sesudah_tukar_menahan_di_pemeriksaan_ulang(sesi, site):
    _tertunda(sesi, site, JobType.pindah_aktifkan, langkah_aktifkan="tukar")
    scan = buat_job(sesi, site.id, JobType.scan_site)
    assert _masih_eksklusif(sesi, scan.id, site.id) is False
    sesi.rollback()


def test_reaper_aktifkan_sebelum_tukar_gagal_salinan(sesi, site_hosting):
    site_hosting.status = StatusHosting.mengaktifkan
    sesi.commit()
    # Tarik terakhir sudah menulis ke salinan VPS (tahap berkas). Yang belum menyentuh salinan
    # kembali ke status sebelum job (final review I2, test_reaper_aktifkan_sebelum_salinan_disentuh_...).
    job = _yatim(sesi, site_hosting, JobType.pindah_aktifkan, {"kemajuan": {"langkah_aktifkan": "tarik",
                                                                           "tahap": "berkas"}})
    assert pulihkan_job_yatim(sesi) == 1
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.unknown
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", hu.PESAN_TERHENTI)


def test_reaper_tidak_menyentuh_hosting_di_luar_status_kerja(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    site_hosting.galat = "lama"
    sesi.commit()
    job = _yatim(sesi, site_hosting, JobType.pindah_tarik)
    assert pulihkan_job_yatim(sesi) == 1
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.unknown
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.pratinjau, None, "lama")


# ---- fix round 1: reaper tidak menunggu kunci baris pemilik (M4) ---------------------


def test_reaper_melewati_job_bila_baris_hosting_terkunci(engine, sesi, site_hosting):
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    site_hosting.status = StatusHosting.menyalin
    sesi.commit()
    job = _yatim(sesi, site_hosting, JobType.pindah_tarik)
    lain = sessionmaker(bind=engine, future=True)()
    try:
        lain.execute(text("SELECT 1 FROM hosting_vps WHERE id = :i FOR UPDATE"), {"i": site_hosting.id})
        # Reaper lama menunggu kunci itu tanpa batas; di sini menunggu paling lama 3 detik.
        sesi.execute(text("SET LOCAL lock_timeout = '3s'"))
        assert pulihkan_job_yatim(sesi) == 0
        sesi.expire_all()
        assert sesi.get(Job, job.id).status == JobStatus.running
    finally:
        lain.rollback()
        lain.close()
    assert pulihkan_job_yatim(sesi) == 1
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.unknown
    assert _h(sesi, site_hosting).status == StatusHosting.gagal


def test_reaper_melewati_job_bila_baris_staging_terkunci(engine, sesi, site_staging):
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    from wpmgr.models import Staging, StatusStaging

    site_staging.status = StatusStaging.menyalin
    sesi.commit()
    buat_job(sesi, site_staging.site_id, JobType.staging_tarik)
    job = ambil_job(sesi, "w-mati", "staging")
    job.attempts = job.max_attempts
    job.locked_at = SEKARANG - timedelta(minutes=30)
    sesi.commit()
    lain = sessionmaker(bind=engine, future=True)()
    try:
        lain.execute(text("SELECT 1 FROM staging WHERE id = :i FOR UPDATE"), {"i": site_staging.id})
        # Reaper lama menunggu kunci itu tanpa batas; di sini menunggu paling lama 3 detik.
        sesi.execute(text("SET LOCAL lock_timeout = '3s'"))
        assert pulihkan_job_yatim(sesi) == 0
    finally:
        lain.rollback()
        lain.close()
    assert pulihkan_job_yatim(sesi) == 1
    sesi.expire_all()
    assert sesi.get(Staging, site_staging.id).status == StatusStaging.gagal


# ---- fix round 1: R26 di pembungkus sesudah tukar (M1/L12) ---------------------------


def test_penolakan_tanpa_ubah_sesudah_tukar_diulang(sesi, site_hosting):
    job = _sesudah_tukar(sesi, site_hosting)

    def inti(sesi, job, site, h):
        raise stg.GalatDitolakTanpaUbah("Ditolak.")

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    assert e.value.error_class == TRANSIENT and not isinstance(e.value, stg.GalatDitolakTanpaUbah)
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.mengaktifkan
    assert akan_diulang(sesi.get(Job, job.id, populate_existing=True), TRANSIENT)


@pytest.mark.parametrize("galat,pesan", [
    (OSError(28, "No space left on device: /var/lib/wpmgr/hosting/x"), None),
    (KeyError("rahasia"), stg.PESAN_TAK_TERDUGA),
])
def test_galat_berkas_dan_tak_terduga_sesudah_tukar_diulang(sesi, site_hosting, galat, pesan):
    job = _sesudah_tukar(sesi, site_hosting)

    def inti(sesi, job, site, h):
        raise galat

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    assert e.value.error_class == TRANSIENT
    assert "/var/lib" not in e.value.pesan and "rahasia" not in e.value.pesan
    if pesan is not None:
        assert e.value.pesan == pesan
    assert _h(sesi, site_hosting).status == StatusHosting.mengaktifkan


def test_batal_diabaikan_sesudah_tukar(sesi, site_hosting):
    job = _sesudah_tukar(sesi, site_hosting)
    site_hosting.batal_diminta_pada = SEKARANG
    sesi.commit()
    dipanggil = []

    def inti(sesi, job, site, h):
        dipanggil.append(1)
        raise SiteError(TRANSIENT, "Situs belum menjawab HTTPS dengan benar.")

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    assert dipanggil and not isinstance(e.value, stg.GalatDibatalkan)
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.mengaktifkan and h.batal_diminta_pada is not None


def test_dibatalkan_dari_inti_sesudah_tukar_diabaikan_dan_diulang(sesi, site_hosting):
    job = _sesudah_tukar(sesi, site_hosting)

    def inti(sesi, job, site, h):
        raise stg.Dibatalkan()

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    assert e.value.error_class == TRANSIENT and e.value.pesan == hu.PESAN_BATAL_DIABAIKAN
    h = _h(sesi, site_hosting)
    assert (h.status, h.batal_diminta_pada) == (StatusHosting.mengaktifkan, None)


# ---- fix round 1: salinan yang sudah utuh tidak ditandai rusak (M2) ------------------


@pytest.mark.parametrize("tipe,status_siap", [
    (JobType.pindah_tarik, StatusHosting.pratinjau),
    (JobType.pindah_aktifkan, StatusHosting.menunggu_dns),
])
def test_batal_sesudah_tarik_utuh_tidak_menandai_salinan_rusak(sesi, site_hosting, tipe, status_siap):
    site_hosting.ditarik_pada = SEKARANG - timedelta(days=1)
    site_hosting.status = StatusHosting.gagal
    site_hosting.gagal_asal = "salinan"
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, tipe)

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, tahap="pratinjau", langkah_aktifkan="tarik")
        raise stg.Dibatalkan()

    with pytest.raises(stg.GalatDibatalkan):
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (status_siap, None, stg.PESAN_DIBATALKAN)


def test_sibuk_lewat_batas_sesudah_tarik_utuh_status_sebelum(sesi, site_hosting):
    site_hosting.ditarik_pada = SEKARANG - timedelta(days=1)
    site_hosting.status = StatusHosting.pratinjau
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik, {"kemajuan": {
        "sibuk_sejak": (SEKARANG - timedelta(hours=5)).isoformat(), "sibuk_kali": 20,
        "sibuk_langkah": "-/pratinjau"}})

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, tahap="pratinjau")
        raise _sibuk()

    with pytest.raises(stg.GalatDitolakTanpaUbah):
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.pratinjau, None, SIBUK)


# ---- fix round 1: jendela sibuk dimulai ulang di langkah baru (M3) -------------------


def test_jendela_sibuk_dimulai_ulang_bila_job_maju_ke_langkah_baru(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik, {"kemajuan": {
        "sibuk_sejak": (SEKARANG - timedelta(hours=5)).isoformat(), "sibuk_kali": 20,
        "sibuk_langkah": "-/db"}})

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, tahap="impor")
        raise _sibuk()

    with pytest.raises(hu.GalatSibuk):
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    j = sesi.get(Job, job.id, populate_existing=True)
    k = stg.kemajuan(j)
    assert (k["sibuk_kali"], k["sibuk_langkah"]) == (1, "-/impor")
    assert dalam_batas_sibuk(j) is True
    assert _h(sesi, site_hosting).status == StatusHosting.menyalin


# ---- fix round 1: sibuk tidak tampil sebagai kegagalan (M6) --------------------------


def test_sibuk_berulang_hanya_satu_baris_aktivitas_info(sesi, site_hosting, monkeypatch):
    from sqlalchemy import text

    site_hosting.status = StatusHosting.pratinjau
    sesi.commit()

    def inti(sesi, job, site, h):
        raise _sibuk()

    _lewat_worker(monkeypatch, JobType.pindah_tarik, inti)
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    for _ in range(3):
        assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
        sesi.execute(text("UPDATE jobs SET scheduled_for = now() WHERE id = :i"), {"i": job.id})
        sesi.commit()
    sesi.expire_all()
    log = sesi.scalars(select(ActivityLog).where(ActivityLog.job_id == job.id)).all()
    assert [r.level for r in log] == ["info"]
    j = sesi.get(Job, job.id)
    assert j.status == JobStatus.pending and j.error == hu.PESAN_MENUNGGU_SIBUK
    assert stg.kemajuan(j)["sibuk_kali"] == 3
    assert _h(sesi, site_hosting).galat == hu.PESAN_MENUNGGU_SIBUK


# ---- fix round 1: kemajuan tanpa galat_hosting_awal (M7) ------------------------------


def test_status_awal_tidak_mencatat_galat(sesi, site_hosting):
    site_hosting.galat = "lama"
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    hu.jalankan_hosting(sesi, job, lambda *a: {}, "Salin ke VPS")
    assert "galat_hosting_awal" not in stg.kemajuan(sesi.get(Job, job.id, populate_existing=True))


# ---- fix round 1: fitur mati selagi job menunggu (M9) ---------------------------------


def test_fitur_mati_selagi_menunggu_tidak_meninggalkan_status_kerja(sesi, site_hosting, monkeypatch):
    from wpmgr.config import get_settings

    site_hosting.status = StatusHosting.mengaktifkan
    site_hosting.galat = "Terputus, dilanjutkan otomatis: x"
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_aktifkan)
    monkeypatch.delenv("WPMGR_HOSTING_IPV4")
    get_settings.cache_clear()
    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, lambda *a: pytest.fail("inti tidak boleh berjalan"), "Aktivasi hosting VPS")
    assert e.value.error_class == STAGING_DITOLAK
    h = _h(sesi, site_hosting)
    # Salinan belum disentuh job ini: status sebelum job (fix round 2), bukan `gagal`.
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.menunggu_dns, None, hu.PESAN_FITUR_MATI)


# ---- fix round 2: tulisan kemajuan dipagari klaim (worker zombi) ---------------------


def _rebut(engine, job_id):
    """Reaper di sesi lain merebut job yang klaimnya basi (worker dianggap mati)."""
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    lain = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        lain.execute(text("UPDATE jobs SET locked_at = now() - interval '30 minutes' WHERE id = :i"), {"i": job_id})
        lain.commit()
        assert pulihkan_job_yatim(lain) == 1
    finally:
        lain.close()


@pytest.mark.parametrize("rollback_dulu", [True, False])
def test_simpan_kemajuan_worker_zombi_ditolak_tanpa_menulis(engine, sesi, site, rollback_dulu):
    buat_job(sesi, site.id, JobType.staging_dorong)
    zombi = ambil_job(sesi, "w-zombi", "staging")
    _rebut(engine, zombi.id)
    if rollback_dulu:
        # Rollback mengedaluwarsakan objek: locked_by dimuat ulang sebagai NULL.
        sesi.rollback()
    with pytest.raises(stg.KlaimHilang):
        stg.simpan_kemajuan(sesi, zombi, unggah_mulai=True, langkah_terapkan="tukar")
    sesi.expire_all()
    j = sesi.get(Job, zombi.id)
    assert j.status == JobStatus.pending and j.locked_by is None
    assert "kemajuan" not in (j.payload or {})


def test_simpan_kemajuan_klaim_sah_menulis_dan_berdetak(sesi, site):
    from sqlalchemy import text

    buat_job(sesi, site.id, JobType.pindah_tarik)
    job = ambil_job(sesi, "w1", "staging")
    sesi.execute(text("UPDATE jobs SET locked_at = now() - interval '10 minutes' WHERE id = :i"), {"i": job.id})
    sesi.commit()
    stg.simpan_kemajuan(sesi, job, tahap="berkas")
    assert stg.kemajuan(job)["tahap"] == "berkas"
    sesi.expire_all()
    j = sesi.get(Job, job.id)
    assert stg.kemajuan(j)["tahap"] == "berkas"
    assert sesi.scalar(select(func.now() - Job.locked_at).where(Job.id == job.id)) < timedelta(minutes=1)


def test_zombi_tidak_menciptakan_saling_tahan(engine, sesi, site):
    """Dorong D direbut reaper; aktivasi A sampai tukar lalu tertunda; zombi D menulis penanda: A tetap bisa diklaim."""
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    buat_job(sesi, site.id, JobType.staging_dorong)
    zombi = ambil_job(sesi, "w-zombi", "staging")
    _rebut(engine, zombi.id)

    lain = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        lain.execute(text("UPDATE jobs SET scheduled_for = now() + interval '1 hour' WHERE id = :i"), {"i": zombi.id})
        lain.commit()
        buat_job(lain, site.id, JobType.pindah_aktifkan)
        a = ambil_job(lain, "w2", "staging")
        assert a is not None and a.tipe == JobType.pindah_aktifkan
        stg.simpan_kemajuan(lain, a, langkah_aktifkan="tukar", tukar_pada=datetime.now(timezone.utc).isoformat())
        selesai_gagal(lain, a, TRANSIENT, "Situs belum menjawab HTTPS dengan benar.")
        assert a.status == JobStatus.pending
        lain.execute(text("UPDATE jobs SET scheduled_for = now() WHERE id IN (:a, :d)"), {"a": a.id, "d": zombi.id})
        lain.commit()
        a_id = a.id
    finally:
        lain.close()

    # Zombi bangun dan mencoba menulis penanda tukar ke D yang kini tertunda.
    sesi.rollback()
    with pytest.raises(stg.KlaimHilang):
        stg.simpan_kemajuan(sesi, zombi, unggah_mulai=True, langkah_terapkan="tukar")
    sesi.expire_all()
    assert ambil_job(sesi, "w3", "staging").id == a_id


# ---- fix round 2: salinan pertama belum utuh sebelum rampung (M2) --------------------


def test_batal_salinan_pertama_sesudah_tahap_pratinjau_tetap_belum_utuh(sesi, site_hosting):
    assert site_hosting.ditarik_pada is None
    site_hosting.status = StatusHosting.gagal
    site_hosting.gagal_asal = "salinan"
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, tahap="selesai")
        raise stg.Dibatalkan()

    with pytest.raises(stg.GalatDibatalkan):
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", hu.PESAN_BATAL_TENGAH)


def test_sibuk_lewat_batas_salinan_pertama_tahap_pratinjau_gagal_salinan(sesi, site_hosting):
    site_hosting.status = StatusHosting.menyalin
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik, {"kemajuan": {
        "sibuk_sejak": (SEKARANG - timedelta(hours=5)).isoformat(), "sibuk_kali": 20,
        "sibuk_langkah": "-/pratinjau"}})

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, tahap="pratinjau")
        raise _sibuk()

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    assert e.value.error_class == STAGING_GAGAL
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal) == (StatusHosting.gagal, "salinan")


# ---- fix round 2: fitur mati / baris hilang sesudah tukar mengikuti R26 (M9) ---------


def test_fitur_mati_sesudah_tukar_diulang_r26(sesi, site_hosting, monkeypatch):
    from wpmgr.config import get_settings

    job = _sesudah_tukar(sesi, site_hosting)
    site_hosting.status = StatusHosting.mengaktifkan
    sesi.commit()
    monkeypatch.delenv("WPMGR_HOSTING_IPV4")
    get_settings.cache_clear()
    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, lambda *a: pytest.fail("inti tidak boleh berjalan"), "Aktivasi hosting VPS")
    assert e.value.error_class == TRANSIENT
    assert _h(sesi, site_hosting).status == StatusHosting.mengaktifkan
    assert akan_diulang(sesi.get(Job, job.id, populate_existing=True), TRANSIENT)


def test_fitur_mati_sesudah_tukar_lewat_24_jam_gagal_produksi(sesi, site_hosting, monkeypatch):
    from wpmgr.config import get_settings

    job = _sesudah_tukar(sesi, site_hosting, jam_lalu=25)
    site_hosting.status = StatusHosting.mengaktifkan
    sesi.commit()
    monkeypatch.delenv("WPMGR_HOSTING_IPV4")
    get_settings.cache_clear()
    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, lambda *a: pytest.fail("inti tidak boleh berjalan"), "Aktivasi hosting VPS")
    assert e.value.error_class == STAGING_DITOLAK
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "produksi", hu.PESAN_PRODUKSI_GAGAL)


def test_baris_hilang_sesudah_tukar_diulang_r26(sesi, site_hosting):
    job = _sesudah_tukar(sesi, site_hosting)
    sesi.delete(site_hosting)
    sesi.commit()
    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, lambda *a: pytest.fail("inti tidak boleh berjalan"), "Aktivasi hosting VPS")
    assert e.value.error_class == TRANSIENT and e.value.pesan == hu.PESAN_BELUM_ADA


def test_fitur_mati_sebelum_tukar_salinan_sehat_tidak_menjadi_gagal(sesi, site_hosting, monkeypatch):
    from wpmgr.config import get_settings

    site_hosting.status = StatusHosting.pratinjau
    site_hosting.ditarik_pada = SEKARANG - timedelta(days=1)
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    # Percobaan sebelumnya mulai (status kerja) lalu terputus sebelum menyentuh salinan.
    hu.catat_status_awal(sesi, job, site_hosting)
    site_hosting.status = StatusHosting.menyalin
    sesi.commit()
    monkeypatch.delenv("WPMGR_HOSTING_IPV4")
    get_settings.cache_clear()
    with pytest.raises(SiteError):
        hu.jalankan_hosting(sesi, job, lambda *a: pytest.fail("inti tidak boleh berjalan"), "Salin ke VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.pratinjau, None, hu.PESAN_FITUR_MATI)
