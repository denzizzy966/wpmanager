"""Bagian bersama hosting VPS (spec Lapis 4 §10).

- Klien connector hosting LAMA: dipatok ke IP lama (tarik ulang sesudah DNS
  berpindah tetap mengambil dari hosting lama, RF4) dan hanya bisa membaca
  (site lama tidak mungkin diubah).
- Pembungkus job hosting `jalankan_hosting` (pola `staging.umum.jalankan_staging`):
  status kerja, batal, asal `gagal`, penolakan sibuk skrip pembantu, dan
  pemetaan galat ke pesan tetap.
"""

import ipaddress
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from wpmgr.config import get_settings
from wpmgr.crypto import dekripsi_secret
from wpmgr.errors import (
    AUTH_ERROR,
    BAD_RESPONSE,
    BERKAS_HILANG,
    BLOCKED,
    CONNECTOR_MISSING,
    STAGING_DITOLAK,
    STAGING_GAGAL,
    STAGING_MATI,
    TERLALU_BESAR,
    TRANSIENT,
    UNKNOWN,
    SiteError,
)
from wpmgr.jobs.queue import (
    akan_diulang,
    dalam_batas_sibuk,
    menyentuh_produksi,
    sibuk_kali,
)
from wpmgr.models import HostingVps, Job, JobType, Site, StatusHosting
from wpmgr.site_client import PREFIX, SiteClient, buat_klien_staging
from wpmgr.staging import umum as stg
from wpmgr.staging.aman import bersih_teks
from wpmgr.staging.pembantu import GalatPembantu

log = logging.getLogger("wpmgr.hosting.umum")

# ---- klien hosting lama (spec §10.1) ---------------------------------------------

PESAN_IP_LAMA = ("Alamat IP hosting lama tidak sah (kosong, privat, atau sama dengan VPS); "
                 "batalkan pindah lalu mulai lagi.")
PESAN_BACA_SAJA = "Klien hosting lama hanya boleh membaca; permintaan ini ditolak."
METODE_BACA = ("ping", "staging_manifest", "staging_file", "staging_rentang", "staging_tabel", "staging_tanda_air")
# Satu-satunya (metode, path) yang boleh dikirim ke hosting lama. Semuanya
# lewat `_kirim` (tenggat total + batas byte); `staging_rentang` memakai
# POST /staging/file.
RUTE_BACA = frozenset({
    ("GET", f"{PREFIX}/ping"),
    ("GET", f"{PREFIX}/staging/manifest"),
    ("POST", f"{PREFIX}/staging/file"),
    ("POST", f"{PREFIX}/staging/tabel"),
    ("GET", f"{PREFIX}/staging/tanda-air"),
})


class _SiteClientLama(SiteClient):
    """SiteClient yang menolak setiap rute di luar `RUTE_BACA` sebelum mengirim.

    Baca-saja secara struktural (review Task 5 M2): metode tulis SiteClient
    yang terpanggil lewat jalan apa pun (`k._klien.staging_terapkan(...)`)
    ditolak tanpa satu byte pun terkirim. `_panggil` (jalur Lapis 1 tanpa
    tenggat total) ditolak seluruhnya: ping memakai `ping_bertenggat`.
    """

    def _panggil(self, method: str, path: str, *argumen, **opsi) -> dict:
        raise stg.galat_ditolak(PESAN_BACA_SAJA)

    def _kirim(self, method: str, path: str, *argumen, **opsi):
        if (method, path) not in RUTE_BACA:
            raise stg.galat_ditolak(PESAN_BACA_SAJA)
        return super()._kirim(method, path, *argumen, **opsi)


class KlienLamaBacaSaja:
    """Klien connector hosting lama yang hanya bisa membaca (spec §10.1).

    Metode tulis (`staging_unggah`, `staging_terapkan`, `staging_bersihkan`,
    `update`, `self_update`) sengaja tidak ada: handler hosting yang keliru
    memanggilnya gagal dengan AttributeError, bukan mengubah site lama.
    """

    def __init__(self, klien: SiteClient) -> None:
        self._klien = klien

    @property
    def alamat(self) -> str | None:
        return self._klien.alamat_tetap

    def ping(self) -> dict:
        # Putusan L9: jalur bertenggat (tenggat total, batas byte), bukan ping Lapis 1.
        return self._klien.ping_bertenggat()

    def staging_manifest(self, kursor: str | None = None, batas: int = 5000) -> dict:
        return self._klien.staging_manifest(kursor, batas=batas)

    def staging_file(self, paths: list[str]):
        return self._klien.staging_file(paths)

    def staging_rentang(self, path: str, dari: int, panjang: int):
        return self._klien.staging_rentang(path, dari, panjang)

    def staging_tabel(self, tabel: str, kursor: str | None):
        return self._klien.staging_tabel(tabel, kursor)

    def staging_tanda_air(self, posts_sejak: str | None = None, posts_maks: int | None = None) -> dict:
        return self._klien.staging_tanda_air(posts_sejak, posts_maks)


def alamat_lama_sah(ip) -> bool:
    """IPv4 unicast publik (bukan privat/loopback/dokumentasi/multicast) yang bukan IPv4 VPS sendiri."""
    if not isinstance(ip, str):
        return False
    try:
        alamat = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    # Multicast (224.0.0.0/4) lolos `is_global` di Python 3.10.
    if not alamat.is_global or alamat.is_multicast or str(alamat) != ip:
        return False
    return ip != get_settings().hosting_ipv4


def buat_http_lama() -> httpx.Client:
    """Klien httpx untuk hosting lama: tanpa keep-alive (tenggat total, putusan F11)."""
    return buat_klien_staging()


def klien_lama(site, hosting) -> KlienLamaBacaSaja:
    if not alamat_lama_sah(hosting.ip_lama):
        raise stg.galat_ditolak(PESAN_IP_LAMA)
    http = buat_http_lama()
    klien = _SiteClientLama(site.url, str(site.id), dekripsi_secret(site.secret_terenkripsi), client=http,
                            klien_staging=http, alamat_tetap=hosting.ip_lama)
    return KlienLamaBacaSaja(klien)


# ---- status dan pesan ---------------------------------------------------------------

ASAL_SALINAN = "salinan"
ASAL_PRODUKSI = "produksi"
PESAN_FITUR_MATI = "Fitur hosting VPS tidak aktif (WPMGR_HOSTING_IPV4 atau WPMGR_STAGING_DOMAIN kosong)."
PESAN_BELUM_ADA = "Pindah hosting untuk site ini belum dimulai."
PESAN_TERHENTI = "Proses terhenti tak terduga; coba lagi."
PESAN_PRODUKSI_GAGAL = ("Situs sudah dilayani VPS tetapi pemeriksaan akhir gagal. Periksa situs; bila rusak, "
                        "arahkan DNS kembali ke hosting lama (masih utuh).")
PESAN_BATAL_TENGAH = "Salin ke VPS dibatalkan di tengah; salinan VPS belum utuh, salin ulang."
PESAN_LAIN = "Pindah hosting gagal; lihat log server."
# Global Constraints / Koreksi #7: teks respons connector tidak pernah tampil di UI.
PESAN_KELAS = {
    TRANSIENT: "Hosting lama tidak dapat dihubungi saat ini.",
    UNKNOWN: "Hosting lama tidak menjawab tuntas.",
    BAD_RESPONSE: "Balasan connector hosting lama tidak sesuai.",
    AUTH_ERROR: "Connector hosting lama menolak tanda tangan dashboard; periksa pairing site.",
    BLOCKED: "Permintaan ke hosting lama diblokir firewall.",
    CONNECTOR_MISSING: "Connector di hosting lama tidak ditemukan.",
    STAGING_MATI: "'Izinkan staging' di connector hosting lama mati.",
    TERLALU_BESAR: "Potongan dari hosting lama melebihi batas.",
    BERKAS_HILANG: "Berkas di hosting lama hilang selama penyalinan.",
}
# Galat dengan `kode` connector (WP_Error): pesannya dari connector, bukan dashboard.
PESAN_KODE = {
    STAGING_GAGAL: "Connector hosting lama menolak permintaan penyalinan.",
    STAGING_DITOLAK: "Hosting lama menolak permintaan saat ini; coba lagi nanti.",
}
STATUS_KERJA = {JobType.pindah_tarik: StatusHosting.menyalin, JobType.pindah_aktifkan: StatusHosting.mengaktifkan}
STATUS_KERJA_SEMUA = frozenset(STATUS_KERJA.values())
_TETAP = object()


class GalatHosting(SiteError):
    """Galat yang pesannya disusun kode hosting sendiri (teks tetap): diteruskan apa adanya ke UI."""


def sekarang() -> datetime:
    return datetime.now(timezone.utc)


def dir_hosting(site_id) -> Path:
    return get_settings().jalur_hosting / str(site_id)


def host_pratinjau(hosting) -> str:
    return f"vps-{hosting.nama}.{get_settings().staging_domain}"


def url_pratinjau(hosting) -> str:
    return f"https://{host_pratinjau(hosting)}"


def muat_hosting(sesi: Session, job: Job) -> tuple[Site, HostingVps]:
    if not get_settings().hosting_aktif:
        raise stg.galat_ditolak(PESAN_FITUR_MATI)
    site = sesi.get(Site, job.site_id)
    h = sesi.scalar(select(HostingVps).where(HostingVps.site_id == job.site_id))
    if h is None:
        raise stg.galat_ditolak(PESAN_BELUM_ADA)
    return site, h


def pesan_ui(exc: SiteError) -> str:
    """Pesan tetap untuk UI dari galat job hosting (Koreksi #7)."""
    if isinstance(exc, GalatHosting):
        return exc.pesan
    if exc.kode is not None:
        # Termasuk GalatDitolakTanpaUbah yang membawa kode connector (preflight
        # M5, mis. R21 di tarik_inti): teksnya milik connector.
        return PESAN_KODE.get(exc.error_class) or PESAN_KELAS.get(exc.error_class, PESAN_LAIN)
    if isinstance(exc, (stg.GalatDitolakTanpaUbah, stg.GalatBerhenti, stg.GalatDibatalkan)):
        return exc.pesan
    if exc.error_class in (STAGING_GAGAL, STAGING_DITOLAK):
        # Disusun dashboard sendiri (galat_gagal/galat_ditolak tanpa kode).
        return bersih_teks(exc.pesan, 1000) or PESAN_LAIN
    return PESAN_KELAS.get(exc.error_class, PESAN_LAIN)


def _pakai_pesan_ui(exc: SiteError, job_id, nama: str) -> None:
    """Ganti pesan galat dengan pesan tetap UI; teks aslinya hanya ke log server."""
    pesan = pesan_ui(exc)
    if pesan != exc.pesan:
        log.warning("Job hosting %s (%s) gagal: %s", job_id, nama, bersih_teks(exc.pesan, 500))
        exc.pesan = pesan
        exc.args = (pesan,)


def kelas_pembantu(job: Job) -> str:
    """Kelas antrean untuk GalatPembantu (Koreksi #16).

    Backup dan pindah_aktifkan sesudah tukar: sementara (F26/R26), karena
    setiap subperintah idempoten dan produksi tidak boleh ditinggal setengah
    beralih. Selain itu final, seperti staging. Penolakan sibuk (keluar 3)
    ditangani terpisah (`_tangani_sibuk`).
    """
    if job.tipe == JobType.backup_hosting or menyentuh_produksi(job):
        return TRANSIENT
    return STAGING_GAGAL


# ---- pembungkus job hosting (spec §10.6) ------------------------------------------

# Skrip pembantu sibuk (keluar 3) bukan kegagalan: teks netral untuk UI.
PESAN_MENUNGGU_SIBUK = "Menunggu proses lain di server selesai; dilanjutkan otomatis."
PESAN_BATAL_DIABAIKAN = "Pembatalan diabaikan: situs sudah mulai dilayani VPS; aktivasi dilanjutkan otomatis."
# Tahap tarik (kemajuan.tahap) sesudah salinan VPS lengkap: berkas, database,
# dan runtime sudah siap (`tarik_inti` menyimpan `tahap_akhir` "pratinjau";
# pindah_tarik menulis "selesai" sesudah tahap pratinjau).
TAHAP_SALINAN_UTUH = frozenset({"pratinjau", "selesai"})


class GalatSibuk(SiteError):
    """Skrip pembantu sibuk/menolak tanpa perubahan; job dijadwalkan ulang (bukan kegagalan).

    Worker mencatat baris aktivitas level info hanya untuk penolakan
    pertama sebuah rentetan (`sudah_dicatat` untuk sisanya); akhir rentetan
    tercatat oleh hasil job berikutnya (sukses, gagal, atau penolakan final).
    """

    level_aktivitas = "info"
    ringkasan_aktivitas = "menunggu proses lain di server; dilanjutkan otomatis"

    def __init__(self, pertama: bool) -> None:
        super().__init__(TRANSIENT, PESAN_MENUNGGU_SIBUK)
        self.sudah_dicatat = not pertama


def catat_status_awal(sesi: Session, job: Job, h: HostingVps) -> None:
    """Status dan asal hosting sebelum job ini, dicatat SEKALI (percobaan ulang melihat status kerja)."""
    if "status_hosting_awal" not in stg.kemajuan(job):
        stg.simpan_kemajuan(sesi, job, status_hosting_awal=h.status.value, gagal_asal_awal=h.gagal_asal)


def status_sebelum(job: Job, h: HostingVps) -> tuple[StatusHosting, str | None]:
    """(status, asal) sebelum job ini, untuk penolakan tanpa ubah dan batal (Koreksi #11)."""
    k = stg.kemajuan(job)
    awal = k.get("status_hosting_awal")
    if awal == StatusHosting.gagal.value:
        return StatusHosting.gagal, k.get("gagal_asal_awal")
    if awal in (StatusHosting.pratinjau.value, StatusHosting.menunggu_dns.value, StatusHosting.aktif.value):
        return StatusHosting(awal), None
    # Tidak tercatat, atau status kerja yang basi.
    if h.dilayani_vps_pada is not None:
        return StatusHosting.gagal, ASAL_PRODUKSI
    if job.tipe == JobType.pindah_aktifkan:
        return StatusHosting.menunggu_dns, None
    if h.ditarik_pada is not None:
        return StatusHosting.pratinjau, None
    return StatusHosting.gagal, ASAL_SALINAN


def salinan_utuh(job: Job, h: HostingVps) -> bool:
    """Tarik job ini sudah menyelesaikan salinan VPS (berkas, database, runtime).

    Salinan pertama (`ditarik_pada` masih kosong) baru utuh sesudah
    `rampungkan_salinan` mengisi `ditarik_pada`; sebelum itu tidak pernah
    dianggap utuh, walau tahapnya sudah `pratinjau`.
    """
    return h.ditarik_pada is not None and stg.kemajuan(job).get("tahap") in TAHAP_SALINAN_UTUH


def status_tanpa_salinan_rusak(job: Job, h: HostingVps) -> tuple[StatusHosting, str | None] | None:
    """(status, asal) bila job berhenti tanpa meninggalkan salinan setengah jadi; None bila salinan belum utuh.

    Belum disentuh: status sebelum job. Sudah disalin utuh oleh job ini:
    status sebelum job, kecuali `gagal` 'salinan' -- salinan itu kini utuh,
    jadi kembali ke status siapnya (pratinjau; aktivasi: menunggu_dns).
    """
    if job.tipe not in STATUS_KERJA or stg.salinan_belum_disentuh(job):
        return status_sebelum(job, h)
    if not salinan_utuh(job, h):
        return None
    status, asal = status_sebelum(job, h)
    if status == StatusHosting.gagal and asal == ASAL_SALINAN:
        siap = StatusHosting.menunggu_dns if job.tipe == JobType.pindah_aktifkan else StatusHosting.pratinjau
        return siap, None
    return status, asal


def status_gagal_final(job: Job, h: HostingVps, pesan: str) -> tuple[StatusHosting, str, str]:
    """(status, asal, galat) untuk kegagalan FINAL (pembungkus dan reaper).

    Sudah dilayani VPS (tukar dikirim): 'produksi' dengan pesan tetap yang
    menyuruh memeriksa situs atau mengembalikan DNS. Belum: 'salinan' (site
    lama masih produksi bagi resolver yang belum berpindah).
    """
    if h.dilayani_vps_pada is not None:
        return StatusHosting.gagal, ASAL_PRODUKSI, PESAN_PRODUKSI_GAGAL
    return StatusHosting.gagal, ASAL_SALINAN, pesan


def _tandai(sesi: Session, hosting_id, status: StatusHosting, galat: str | None, asal=_TETAP,
            bersihkan_batal: bool = True) -> None:
    sesi.rollback()
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if h is None:
        return
    h.status = status
    h.galat = bersih_teks(galat, 1000)
    if asal is not _TETAP:
        h.gagal_asal = asal if status == StatusHosting.gagal else None
    if bersihkan_batal:
        h.batal_diminta_pada = None
    sesi.commit()


def _tandai_backup_gagal(sesi: Session, hosting_id) -> None:
    sesi.rollback()
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if h is not None:
        h.backup_gagal_pada = sekarang()
        sesi.commit()


def _gagal_final(sesi: Session, job: Job, hosting_id, pesan: str) -> None:
    sesi.rollback()
    if job.tipe == JobType.backup_hosting:
        # Backup tidak pernah mengubah status hosting (spec §10.5).
        _tandai_backup_gagal(sesi, hosting_id)
        return
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if h is None:
        return
    status, asal, galat = status_gagal_final(job, h, pesan)
    _tandai(sesi, hosting_id, status, galat, asal=asal)


def _kembali_tanpa_ubah(sesi: Session, job: Job, hosting_id, pesan: str) -> None:
    """Penolakan tanpa perubahan: status sebelum job (backup: hanya `backup_gagal_pada`).

    "Tanpa perubahan" berlaku untuk langkah yang menolak, bukan untuk
    seluruh job: percobaan sebelumnya bisa sudah menulis salinan VPS
    setengah jadi (mis. tarik terakhir aktivasi terputus, lalu DNS menolak;
    atau pindah_tarik yang izinnya dicabut di tengah). Salinan seperti itu
    tidak boleh tersembunyi di balik status siap (spec §10.3): `gagal`
    'salinan' (review Task 10 I2).
    """
    sesi.rollback()
    if job.tipe == JobType.backup_hosting:
        _tandai_backup_gagal(sesi, hosting_id)
        return
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if h is None:
        return
    hasil = status_tanpa_salinan_rusak(job, h)
    if hasil is None:
        _gagal_final(sesi, job, hosting_id, pesan)
        return
    status, asal = hasil
    _tandai(sesi, hosting_id, status, pesan, asal=asal)


def _batalkan(sesi: Session, job: Job, site_id, hosting_id, nama: str) -> stg.GalatDibatalkan:
    sesi.rollback()
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if h is not None:
        hasil = status_tanpa_salinan_rusak(job, h)
        if hasil is None:
            # Salinan VPS sudah mulai ditulis dan belum lengkap: salin ulang.
            status, asal, galat = StatusHosting.gagal, ASAL_SALINAN, PESAN_BATAL_TENGAH
        else:
            (status, asal), galat = hasil, stg.PESAN_DIBATALKAN
        _tandai(sesi, hosting_id, status, galat, asal=asal)
    stg.catat_aktivitas(sesi, site_id, job, f"{nama} dibatalkan", level="warning")
    sesi.commit()
    return stg.GalatDibatalkan()


def _akhiri_rentetan_sibuk(sesi: Session, job: Job) -> None:
    """Kegagalan lain sesudah penolakan sibuk: rentetan selesai, jendela berikutnya mulai dari awal."""
    if sibuk_kali(job):
        stg.simpan_kemajuan(sesi, job, sibuk_kali=0, sibuk_sejak=None, sibuk_langkah=None, sibuk_dilihat=[])


def _putuskan(sesi: Session, job: Job, hosting_id, site_id, exc: SiteError, status_kerja, batal_berlaku,
              nama: str) -> None:
    """Final atau diulang, persis seperti worker (queue.akan_diulang; F26: UNKNOWN = TRANSIENT)."""
    sesi.rollback()
    if batal_berlaku() and stg._batal_diminta(sesi, hosting_id, HostingVps):
        raise _batalkan(sesi, job, site_id, hosting_id, nama)
    _akhiri_rentetan_sibuk(sesi, job)
    kelas = TRANSIENT if exc.error_class == UNKNOWN else exc.error_class
    if akan_diulang(job, kelas):
        if status_kerja is not None:
            _tandai(sesi, hosting_id, status_kerja, f"Terputus, dilanjutkan otomatis: {exc.pesan}",
                    bersihkan_batal=False)
    else:
        _gagal_final(sesi, job, hosting_id, exc.pesan)


def _ulang_sesudah_tukar(sesi: Session, job: Job, hosting_id, site_id, pesan: str, status_kerja, batal_berlaku,
                         nama: str) -> SiteError:
    """Produksi sudah tersentuh (R26, putusan L12): apa pun galatnya diulang sampai 24 jam, lalu `gagal` 'produksi'."""
    galat = SiteError(TRANSIENT, pesan)
    _putuskan(sesi, job, hosting_id, site_id, galat, status_kerja, batal_berlaku, nama)
    return galat


def _langkah_sekarang(job: Job) -> str:
    """Penanda langkah job (langkah aktivasi + tahap tarik): rentetan sibuk dimulai ulang di langkah baru."""
    k = stg.kemajuan(job)
    return f"{k.get('langkah_aktifkan') or '-'}/{k.get('tahap') or '-'}"


def _tangani_sibuk(sesi: Session, job: Job, hosting_id, site_id, exc: GalatPembantu, status_kerja,
                   batal_berlaku, nama: str) -> SiteError:
    """Skrip pembantu menolak tanpa perubahan (keluar 3): dijadwalkan ulang, bukan gagal.

    Kunci router dipegang selama `prod-db-impor` situs lain (sampai 3 jam),
    dan kunci nginx yang sibuk juga keluar 3. Selama `queue.BATAS_SIBUK`
    sejak penolakan pertama rentetan ini (rentetan dimulai ulang bila job
    maju ke langkah yang belum pernah sibuk di rentetan ini), job diulang dengan jeda yang bertambah
    (queue.selesai_gagal), jatah percobaannya dikembalikan (pola
    GalatBerhenti), dan baris hosting tetap di status kerjanya dengan pesan
    netral. Sesudah jendela itu: berakhir seperti penolakan tanpa ubah
    (status sebelum job; salinan yang sudah disalin utuh tidak ditandai
    rusak), kecuali salinan VPS sudah mulai ditulis job ini dan belum lengkap
    -- salinan setengah jadi tidak boleh tersembunyi di balik `pratinjau`
    (spec §10.3), jadi `gagal` asal `salinan`.
    """
    sesi.rollback()
    if batal_berlaku() and stg._batal_diminta(sesi, hosting_id, HostingVps):
        return _batalkan(sesi, job, site_id, hosting_id, nama)
    k = stg.kemajuan(job)
    langkah = _langkah_sekarang(job)
    # Rentetan dimulai ulang hanya bila job maju ke langkah yang BELUM pernah
    # ditolak sibuk dalam rentetan ini (kemajuan sungguhan). Langkah yang
    # bergantian -- tarik terakhir aktivasi yang sibuk di impor, lalu tukar
    # yang sibuk di prod-aktifkan, lalu impor lagi -- meneruskan rentetan
    # yang sama, jadi jendela BATAS_SIBUK tetap berakhir (review Task 10 M4).
    # Langkah yang sudah ditolak sibuk diingat selama rentetannya berjalan
    # (juga sesudah jendela dimulai ulang di langkah baru), sehingga
    # banyaknya mulai ulang dibatasi jumlah langkah yang berbeda.
    berjalan = sibuk_kali(job) > 0
    dilihat = [x for x in (k.get("sibuk_dilihat") or []) if isinstance(x, str)] if berjalan else []
    if berjalan and isinstance(k.get("sibuk_langkah"), str) and k["sibuk_langkah"] not in dilihat:
        dilihat.append(k["sibuk_langkah"])
    kali = sibuk_kali(job) if langkah in dilihat else 0
    sejak = k.get("sibuk_sejak") if kali else None
    if langkah not in dilihat:
        dilihat.append(langkah)
    stg.simpan_kemajuan(sesi, job, sibuk_sejak=sejak or sekarang().isoformat(), sibuk_kali=kali + 1,
                        sibuk_langkah=langkah, sibuk_dilihat=dilihat[-20:])
    if dalam_batas_sibuk(job):
        job.attempts = max(0, job.attempts - 1)
        sesi.commit()
        if akan_diulang(job, TRANSIENT):
            log.info("Job hosting %s (%s): skrip pembantu sibuk (%s, ke-%s); dijadwalkan ulang",
                     job.id, nama, exc.pesan, kali + 1)
            if status_kerja is not None:
                _tandai(sesi, hosting_id, status_kerja, PESAN_MENUNGGU_SIBUK, bersihkan_batal=False)
            return GalatSibuk(pertama=kali == 0)
    log.warning("Job hosting %s (%s): skrip pembantu terus menolak sejak %s; diakhiri",
                job.id, nama, stg.kemajuan(job).get("sibuk_sejak"))
    if job.tipe == JobType.backup_hosting:
        _tandai_backup_gagal(sesi, hosting_id)
        return stg.GalatDitolakTanpaUbah(exc.pesan)
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    hasil = status_tanpa_salinan_rusak(job, h) if h is not None else None
    if hasil is None:
        _gagal_final(sesi, job, hosting_id, exc.pesan)
        return stg.galat_gagal(exc.pesan)
    status, asal = hasil
    _tandai(sesi, hosting_id, status, exc.pesan, asal=asal)
    return stg.GalatDitolakTanpaUbah(exc.pesan)


def _muat_atau_lepas(sesi: Session, job: Job) -> tuple[Site, HostingVps]:
    """`muat_hosting`; bila ditolak (fitur dimatikan, baris hilang) selagi job menunggu.

    Sesudah tukar (`queue.menyentuh_produksi`): diulang menurut R26 seperti
    galat lain (TRANSIENT) sampai batas 24 jam, lalu kegagalan final
    (`gagal` 'produksi'). Sebelum tukar: job berakhir, dan baris yang
    ditinggal percobaan sebelumnya di status kerja (`menyalin`/
    `mengaktifkan`) ditutup tanpa merusak salinan yang sehat
    (`status_tanpa_salinan_rusak`; salinan setengah jadi -> `gagal` 'salinan').
    """
    try:
        return muat_hosting(sesi, job)
    except SiteError as exc:
        sesi.rollback()
        h = sesi.scalar(select(HostingVps).where(HostingVps.site_id == job.site_id))
        if menyentuh_produksi(job):
            if akan_diulang(job, TRANSIENT):
                raise SiteError(TRANSIENT, exc.pesan) from None
            if h is not None:
                _gagal_final(sesi, job, h.id, exc.pesan)
            raise
        if h is not None and h.status in STATUS_KERJA_SEMUA:
            hasil = status_tanpa_salinan_rusak(job, h)
            if hasil is None:
                _gagal_final(sesi, job, h.id, exc.pesan)
            else:
                status, asal = hasil
                _tandai(sesi, h.id, status, exc.pesan, asal=asal)
        raise


def jalankan_hosting(sesi: Session, job: Job, inti, nama: str, boleh_batal=None) -> dict:
    """Pembungkus bersama job hosting (spec §10.6).

    `inti(sesi, job, site, hosting) -> dict`. `boleh_batal(job) -> bool`
    (opsional): False berarti permintaan batal tidak lagi berlaku. Sesudah
    tukar (`queue.menyentuh_produksi`) batal tidak pernah berlaku dan setiap
    galat diulang menurut R26 (putusan L12). Backup tidak bisa dibatalkan
    dan tidak mengubah status.
    """
    site, h = _muat_atau_lepas(sesi, job)
    hosting_id, site_id, job_id = h.id, site.id, job.id
    backup = job.tipe == JobType.backup_hosting
    status_kerja = STATUS_KERJA.get(job.tipe)
    catat_status_awal(sesi, job, h)
    if status_kerja is not None:
        h.status = status_kerja
        h.galat = None
    sesi.commit()

    def batal_berlaku() -> bool:
        # Dibaca dari kemajuan yang ter-commit (pemanggil rollback lebih dulu).
        return not backup and not menyentuh_produksi(job) and (boleh_batal is None or boleh_batal(job))

    def ulang(pesan: str) -> SiteError:
        return _ulang_sesudah_tukar(sesi, job, hosting_id, site_id, pesan, status_kerja, batal_berlaku, nama)

    try:
        if batal_berlaku():
            stg.periksa_batal(sesi, h)
        hasil = inti(sesi, job, site, h)
    except stg.Dibatalkan:
        sesi.rollback()
        if menyentuh_produksi(job):
            # Batal tidak berlaku lagi; permintaannya dihapus supaya putaran
            # berikutnya tidak berhenti di titik potongan yang sama.
            _tandai_batal_diabaikan(sesi, hosting_id)
            raise ulang(PESAN_BATAL_DIABAIKAN) from None
        raise _batalkan(sesi, job, site_id, hosting_id, nama) from None
    except stg.KlaimHilang:
        raise
    except stg.GalatDitolakTanpaUbah as exc:
        _pakai_pesan_ui(exc, job_id, nama)
        sesi.rollback()
        if menyentuh_produksi(job):
            raise ulang(exc.pesan) from None
        _kembali_tanpa_ubah(sesi, job, hosting_id, exc.pesan)
        raise
    except GalatPembantu as exc:
        # GalatPembantu.pesan sudah teks tetap (F20).
        sesi.rollback()
        if exc.tanpa_ubah and not menyentuh_produksi(job):
            raise _tangani_sibuk(sesi, job, hosting_id, site_id, exc, status_kerja, batal_berlaku, nama) from None
        # Sesudah tukar, penolakan sibuk pun mengikuti R26 (TRANSIENT, 24 jam).
        galat = SiteError(kelas_pembantu(job), exc.pesan)
        _putuskan(sesi, job, hosting_id, site_id, galat, status_kerja, batal_berlaku, nama)
        raise galat from None
    except SiteError as exc:
        _pakai_pesan_ui(exc, job_id, nama)
        _putuskan(sesi, job, hosting_id, site_id, exc, status_kerja, batal_berlaku, nama)
        raise
    except OSError as exc:
        pesan = stg.pesan_os(exc)
        sesi.rollback()
        if menyentuh_produksi(job):
            raise ulang(pesan) from None
        _gagal_final(sesi, job, hosting_id, pesan)
        raise stg.galat_gagal(pesan) from None
    except Exception:
        # Bug dashboard (F12): teks pengecualian hanya ke log server.
        log.exception("Galat tak terduga pada %s (job %s)", nama, job_id)
        sesi.rollback()
        if menyentuh_produksi(job):
            raise ulang(stg.PESAN_TAK_TERDUGA) from None
        _gagal_final(sesi, job, hosting_id, stg.PESAN_TAK_TERDUGA)
        raise
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if h is not None and not backup:
        h.galat = None
        if h.status != StatusHosting.gagal:
            h.gagal_asal = None
        h.batal_diminta_pada = None
    sesi.commit()
    return hasil


def _tandai_batal_diabaikan(sesi: Session, hosting_id) -> None:
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if h is not None:
        h.batal_diminta_pada = None
        sesi.commit()
