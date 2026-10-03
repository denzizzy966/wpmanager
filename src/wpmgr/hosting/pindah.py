"""Job pindah_tarik dan pindah_aktifkan (spec Lapis 4 §10.2-10.4).

Mesin tarik Lapis 3 (`tarik.tarik_inti`) menyalin ke HOSTING_DIR/<site_id>
lewat `TujuanHosting`; klien hosting lama dipatok IP dan baca-saja. Site lama
tidak pernah diubah. Sesudah `dilayani_vps_pada` terisi, setiap tarik ditolak
sebelum klien lama dihubungi (RF2). Aktivasi: cek DNS, sertifikat domain,
tarik terakhir, tukar (tulis-lebih-dulu), verifikasi.
"""

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from wpmgr.config import get_settings
from wpmgr.connector_paket import isi_mu_plugin_pratinjau
from wpmgr.errors import TRANSIENT, SiteError
from wpmgr.fitur import STAGING, punya_fitur
from wpmgr.hosting import dns
from wpmgr.hosting import umum as hu
from wpmgr.jobs.queue import LANGKAH_AKTIFKAN_SESUDAH_TUKAR
from wpmgr.models import HostingVps, StatusHosting
from wpmgr.site_client import MelebihiBatas, TanpaHasil, TenggatHabis, minta_bertenggat
from wpmgr.staging import tarik
from wpmgr.staging import umum as stg
from wpmgr.staging.aman import PathTidakAman, baca_terbatas, hapus_berkas, tulis_atomik
from wpmgr.staging.indeks import Indeks
from wpmgr.staging.pembantu import GalatPembantu, tulis_htpasswd_pratinjau
from wpmgr.staging.rencana import cek_ram, format_byte

log = logging.getLogger("wpmgr.hosting.pindah")

MU_PLUGIN_PRATINJAU = "wp-content/mu-plugins/wpmgr-pratinjau.php"
# Berkas milik salinan VPS sendiri: tidak pernah ditimpa atau dihapus tarik.
# wp-config.php ditulis skrip pembantu; mu-plugin pratinjau ditulis dashboard
# dan tidak pernah ada di produksi, jadi tanpa perlindungan ini setiap salin
# ulang menghapusnya (berkas di tujuan yang tidak ada di manifest).
DILINDUNGI_HOSTING = frozenset({"wp-config.php", MU_PLUGIN_PRATINJAU})
PESAN_IZIN = ("Connector hosting lama belum mengizinkan staging. Aktifkan 'Izinkan staging' di "
              "Pengaturan -> WP Manager (connector 3.0).")
PESAN_SUDAH_DILAYANI = "Site ini sudah dilayani VPS; salinan dari hosting lama tidak boleh menimpanya."
PESAN_SANDI_BELUM = "Kata sandi pratinjau belum dibuat; buat ulang kata sandi pratinjau."
PESAN_STATUS_TARIK = "Salin ke VPS hanya bisa dari status pratinjau, menunggu DNS, atau gagal menyalin."
PESAN_HTTP = "Site lama memakai http (bukan https) di home/siteurl; aktifkan HTTPS di hosting lama dulu."
PESAN_SUBFOLDER = "WordPress di subfolder atau di port lain (home/siteurl ber-path) belum didukung pindah hosting."
PESAN_HOST_LAIN = "Alamat home/siteurl site lama tidak cocok dengan domain site ini."
PESAN_WWW = ("Site lama memakai www di home/siteurl, tetapi www tidak terdaftar untuk domain ini; "
             "batalkan pindah lalu mulai lagi.")
PESAN_MU_PLUGIN = "Folder mu-plugins salinan VPS tidak aman (berupa symlink); salin ulang untuk memulihkannya."
STATUS_BOLEH_TARIK = ("menyalin", "pratinjau", "menunggu_dns")
# Kunci kemajuan job: hasil sertifikat pratinjau ("terbit" | "gagal") job ini.
KUNCI_SERTIFIKAT = "sertifikat_pratinjau"


def cek_ram_hosting(status) -> str | None:
    """RAM >= 2 GB sebelum menyalin (spec §10.3); ERPNext dan ±19 site lain berbagi VPS ini.

    Aturannya sama dengan staging (`rencana.cek_ram`, preflight M6); hanya
    kata tujuannya yang berbeda.
    """
    return cek_ram(status, "situs")


def periksa_info_hosting(h, info: dict) -> None:
    """Info site lama yang tidak bisa dilayani VPS ditolak sebelum salinan disentuh (spec §10.2)."""
    for kunci in ("home", "siteurl"):
        bagian = urlsplit(info[kunci])
        if bagian.scheme != "https":
            raise stg.GalatDitolakTanpaUbah(PESAN_HTTP)
        try:
            port = bagian.port
        except ValueError:
            port = -1
        if bagian.path not in ("", "/") or port is not None:
            raise stg.GalatDitolakTanpaUbah(PESAN_SUBFOLDER)
        host = (bagian.hostname or "").lower()
        if host not in (h.domain, f"www.{h.domain}"):
            raise stg.GalatDitolakTanpaUbah(PESAN_HOST_LAIN)
        if host == f"www.{h.domain}" and not h.dengan_www:
            raise stg.GalatDitolakTanpaUbah(PESAN_WWW)


def tulis_mu_plugin(akar: Path) -> None:
    """Mu-plugin pratinjau ke files/ salinan (lewat `aman`: tidak mengikuti symlink)."""
    try:
        tulis_atomik(akar / "files", MU_PLUGIN_PRATINJAU, isi_mu_plugin_pratinjau().encode("utf-8"))
    except PathTidakAman:
        raise stg.galat_gagal(PESAN_MU_PLUGIN) from None


def tujuan_hosting(sesi, job, site, h: HostingVps, pb) -> tarik.TujuanSalinan:
    akar = hu.dir_hosting(site.id)
    # Nilai baris disalin sekarang: closure di bawah dipanggil sesudah banyak
    # commit/rollback, dan tidak boleh memuat ulang baris di tengah tarik.
    nama, domain, www, site_id, sandi_hash = h.nama, h.domain, h.dengan_www, site.id, h.sandi_hash
    identitas = SimpleNamespace(domain=domain, dengan_www=www)

    def impor(info: dict, berkas: list[Path]) -> None:
        # Koreksi #1: prod-db-buat menemukan files/ lewat state root yang
        # dibuat prod-buat, jadi prod-buat (idempoten) dipanggil lebih dulu.
        pb.prod_buat(nama, info["versi_php"], site_id, domain, www)
        pb.prod_db_buat(nama, info["prefix"])
        pb.prod_db_impor(nama, berkas)

    def siapkan(sesi_, job_, info: dict) -> None:
        # Tanpa search-replace dan tanpa wp-cli (spec §10.2): host pratinjau
        # ditangani WP_HOME bersyarat di wp-config dan penggantian keluaran
        # di mu-plugin.
        with stg.detak_latar(sesi_, job_):
            tulis_mu_plugin(akar)
            tulis_htpasswd_pratinjau(get_settings().jalur_hosting, nama, sandi_hash)
            pb.prod_buat(nama, info["versi_php"], site_id, domain, www)
            pb.prod_router_muat()

    return tarik.TujuanSalinan(
        akar=akar, baris=h, status_sumber=pb.prod_status, cek_awal=cek_ram_hosting,
        periksa_info=lambda info: periksa_info_hosting(identitas, info),
        # Putusan R25 tidak berlaku (spec §4.1): salinan ini AKAN menjadi
        # produksi dan memakai secret connector produksi yang sama, jadi
        # `wpmgr_secret` di database salinan tidak disentuh.
        sql_tambahan=lambda sesi_, info, db_dir: None,
        impor=impor, siapkan_runtime=siapkan, dilindungi=DILINDUNGI_HOSTING,
        subdir=("files", "log"), tahap_akhir="pratinjau",
    )


def salin(sesi, job, site, h: HostingVps, klien, pb, k: dict | None = None) -> dict:
    """Gerbang lalu `tarik_inti` ke HOSTING_DIR/<site_id>; dipakai pindah_tarik dan langkah tarik aktivasi.

    `k`: kemajuan awal untuk `tarik_inti` (bawaan: kemajuan job). Kemajuan
    tanpa `tahap` memulai tarik baru dari manifest.
    """
    if h.dilayani_vps_pada is not None:
        # RF2: selalu, juga pada percobaan ulang, sebelum hosting lama dihubungi.
        raise stg.GalatDitolakTanpaUbah(PESAN_SUDAH_DILAYANI)
    if not punya_fitur(site, STAGING):
        raise tarik._tolak(job, PESAN_IZIN)
    if not h.sandi_hash:
        raise tarik._tolak(job, PESAN_SANDI_BELUM)
    return tarik.tarik_inti(sesi, job, site, klien, tujuan_hosting(sesi, job, site, h, pb),
                            stg.kemajuan(job) if k is None else k)


def rampungkan_salinan(sesi, job, site, h: HostingVps, k: dict, sertifikat_ok: bool | None) -> dict:
    """Padatkan indeks, buang area kerja, dan catat ukuran salinan di baris hosting.

    Hanya baris hosting yang ditulis di transaksi ini; kemajuan job sudah
    di-commit pemanggil lewat `simpan_kemajuan` (pola kunci reaper: baris job
    dan hosting_vps tidak pernah dikunci bersama oleh handler).
    """
    akar = hu.dir_hosting(site.id)
    indeks = Indeks(akar / "indeks.jsonl")
    lokal = indeks.muat()
    indeks.padatkan(lokal)
    shutil.rmtree(akar / "tarik", ignore_errors=True)
    info = k["info"]
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    sekarang = hu.sekarang()
    baris.ditarik_pada = sekarang
    baris.versi_php = info["versi_php"]
    baris.ukuran_file = min(sum(e.ukuran for e in lokal.values()), 2**62)
    baris.ukuran_db = min(info["ukuran_db"], 2**62)
    if sertifikat_ok:
        baris.pratinjau_sertifikat_pada = sekarang
    sesi.commit()
    return {"byte_disalin": k.get("byte_selesai", 0), "ukuran_file": baris.ukuran_file,
            "ukuran_db": baris.ukuran_db, "versi_php": info["versi_php"],
            "peringatan": list(k.get("peringatan") or [])}


def _sertifikat_pratinjau(sesi, job, h: HostingVps, pb, k: dict) -> dict:
    """Sertifikat host pratinjau, dicoba SEKALI per job; hasilnya disimpan di kemajuan (review M1).

    Percobaan ulang job (mis. prod-domain sibuk) tidak menjalankan certbot
    lagi: setiap validasi yang gagal memakan batas Let's Encrypt. Apa pun
    galat pembantunya, termasuk keluar 3 (`cmd_sertifikat` tidak memakai
    kunci, jadi 3 berarti nama atau direktori sertifikat ditolak, bukan
    sibuk; review I1), pratinjau tetap jadi tanpa HTTPS dengan peringatan
    (spec §8.4, §10.2); Salin ulang mencoba lagi.
    """
    hasil = "terbit"
    try:
        with stg.detak_latar(sesi, job):
            # Host pratinjau lewat server port 80 wildcard staging (spec §8.4).
            pb.sertifikat(f"vps-{h.nama}")
    except GalatPembantu as exc:
        hasil = "gagal"
        tarik._tambah_peringatan(k, f"Sertifikat pratinjau belum terbit: {exc.pesan}")
    return stg.simpan_kemajuan(sesi, job, **{KUNCI_SERTIFIKAT: hasil}, peringatan=list(k.get("peringatan") or []))


def pindah_tarik(sesi, job, site, h: HostingVps, klien, pb) -> dict:
    if h.dilayani_vps_pada is not None:
        raise stg.GalatDitolakTanpaUbah(PESAN_SUDAH_DILAYANI)
    k_awal = stg.kemajuan(job)
    awal = k_awal.get("status_hosting_awal")
    if not (awal in STATUS_BOLEH_TARIK or (awal == "gagal" and k_awal.get("gagal_asal_awal") == hu.ASAL_SALINAN)):
        raise stg.GalatDitolakTanpaUbah(PESAN_STATUS_TARIK)
    pertama = h.ditarik_pada is None
    k = salin(sesi, job, site, h, klien, pb)
    if k.get("tahap") == "pratinjau":
        stg.titik_potongan(sesi, job, h)
        if k.get(KUNCI_SERTIFIKAT) is None:
            k = _sertifikat_pratinjau(sesi, job, h, pb, k)
        with stg.detak_latar(sesi, job):
            pb.prod_domain(h.nama)
        k = stg.simpan_kemajuan(sesi, job, tahap="selesai", peringatan=list(k.get("peringatan") or []))
    # Dari kemajuan, bukan variabel lokal: percobaan ulang sesudah prod-domain
    # (atau sesudah tahap "selesai") tetap mencatat hasil sertifikat yang benar.
    hasil_sert = k.get(KUNCI_SERTIFIKAT)
    sertifikat_ok = None if hasil_sert is None else hasil_sert == "terbit"
    hasil = rampungkan_salinan(sesi, job, site, h, k, sertifikat_ok)
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    baris.status = StatusHosting.pratinjau
    stg.catat_aktivitas(sesi, site.id, job, "Salinan VPS dibuat" if pertama else "Salinan VPS disegarkan", {
        **hasil, "ukuran_file_teks": format_byte(hasil["ukuran_file"]), "peringatan": hasil["peringatan"][:10]})
    sesi.commit()
    return hasil


def bersihkan_bila_final(sesi, site_id) -> None:
    """Hapus area kerja tarik/ bila kegagalan job ini final (pola `tarik.bersihkan_bila_final`)."""
    try:
        sesi.rollback()
        status = sesi.scalar(select(HostingVps.status).where(HostingVps.site_id == site_id))
    except Exception:  # noqa: BLE001 -- galat asli yang dilempar ulang lebih penting
        return
    if status is not None and status not in hu.STATUS_KERJA_SEMUA:
        shutil.rmtree(hu.dir_hosting(site_id) / "tarik", ignore_errors=True)


def tangani_pindah_tarik(sesi, job, klien) -> dict:
    """Handler worker. Klien bawaan worker (ke site.url) diabaikan: hosting lama lewat IP yang dipatok."""
    def inti(sesi, job, site, h):
        return pindah_tarik(sesi, job, site, h, hu.klien_lama(site, h), stg.buat_pembantu())

    site_id = job.site_id
    try:
        return hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    except stg.KlaimHilang:
        raise
    except Exception:
        bersihkan_bila_final(sesi, site_id)
        raise


# ---- pindah_aktifkan (spec §10.4) ---------------------------------------------------

LANGKAH_AKTIFKAN = ("dns", "sertifikat", "tarik", "tukar", "verifikasi", "beres")
ALAMAT_VERIFIKASI = "127.0.0.1"
TIMEOUT_VERIFIKASI = 15.0
TENGGAT_VERIFIKASI = 20.0
BATAS_VERIFIKASI = 64 * 1024
BATAS_WP_CONFIG = 256 * 1024
UA_VERIFIKASI = "WP-Manager-Hosting/4.0"
PESAN_STATUS_AKTIFKAN = "Aktivasi hanya bisa dari status menunggu DNS atau gagal."
PESAN_NGINX = "Konfigurasi nginx domain belum dapat dipasang; aktivasi ditunda."
PESAN_SERTIFIKAT = "Sertifikat domain belum dapat diterbitkan; dicoba lagi otomatis sesudah jeda."
PESAN_TUKAR_DITOLAK = ("VPS menolak mengaktifkan situs (sertifikat, database, atau container belum siap); "
                       "tidak ada yang diubah.")
PESAN_VERIFIKASI = "Situs belum menjawab HTTPS dengan benar lewat VPS; diperiksa lagi otomatis."
PESAN_SALINAN_BELUM_UTUH = ("Salinan VPS belum utuh; aktivasi tanpa tarik ulang dari hosting lama ditolak. "
                            "Salin ke VPS lagi dulu.")
PESAN_SERVER_SIBUK = ("Server VPS sedang sibuk dengan proses lain sehingga aktivasi belum dijalankan; "
                      "tidak ada yang diubah, coba lagi nanti.")
PESAN_PERIKSA_ULANG = "Situs sudah dilayani VPS; pemeriksaan ulang hanya bisa dari status gagal atau aktif."
STATUS_BOLEH_AKTIFKAN = ("menunggu_dns", "gagal")
# Putusan L17: tarik terakhir yang selesai belum selama ini dipakai lagi oleh
# percobaan ulang job yang sama, bukan diekspor ulang dari hosting lama.
UMUR_TARIK_TERAKHIR = timedelta(minutes=60)


def buat_http_verifikasi(**opsi) -> httpx.Client:
    """Klien verifikasi: tanpa ikut alihan (3xx dinilai apa adanya) dan tanpa keep-alive (tenggat total)."""
    return httpx.Client(follow_redirects=False, limits=httpx.Limits(max_keepalive_connections=0), **opsi)


def ambil_halaman_verifikasi(host: str) -> tuple[int, dict] | None:
    """GET https://<host>/ ke nginx host lokal dengan SNI host (spec §10.4).

    Sertifikat diverifikasi terhadap nama host: situs yang hanya punya
    sertifikat pratinjau tidak lolos. None = tidak ada jawaban yang sah.
    """
    with buat_http_verifikasi() as http:
        try:
            status, header, _ = minta_bertenggat(
                http, "GET", f"https://{ALAMAT_VERIFIKASI}/",
                headers={"Host": host, "User-Agent": UA_VERIFIKASI, "Connection": "close",
                         "Accept-Encoding": "identity"},
                timeout=TIMEOUT_VERIFIKASI, tenggat=TENGGAT_VERIFIKASI, batas_byte=BATAS_VERIFIKASI, potong=True,
                ekstensi={"sni_hostname": host})
        except (httpx.HTTPError, TenggatHabis, TanpaHasil, MelebihiBatas):
            return None
    return status, {str(k).lower(): str(v) for k, v in header.items()}


def verifikasi(h, akar: Path) -> list[str]:
    """Masalah aktivasi (kosong = lolos): HTTPS 2xx/3xx tanpa 401 dan tanpa noindex, wp-config tanpa pratinjau."""
    masalah = []
    for host in [h.domain] + ([f"www.{h.domain}"] if h.dengan_www else []):
        jawab = ambil_halaman_verifikasi(host)
        if jawab is None:
            masalah.append(f"{host}: tidak menjawab HTTPS dengan sertifikat yang sah")
            continue
        status, header = jawab
        if status == 401:
            masalah.append(f"{host}: masih meminta kata sandi pratinjau")
        elif not 200 <= status < 400:
            masalah.append(f"{host}: HTTP {status}")
        elif "noindex" in header.get("x-robots-tag", "").lower():
            masalah.append(f"{host}: masih mengirim X-Robots-Tag noindex")
    try:
        isi = baca_terbatas(akar / "files", "wp-config.php", BATAS_WP_CONFIG)
    except (PathTidakAman, OSError):
        masalah.append("wp-config.php situs tidak terbaca")
    else:
        if b"WPMGR_PRATINJAU" in isi:
            masalah.append("wp-config.php masih dalam mode pratinjau")
    return masalah


def boleh_batal_aktifkan(job) -> bool:
    """Batal hanya berlaku sebelum tukar (pola dorong Lapis 3)."""
    return stg.kemajuan(job).get("langkah_aktifkan") not in LANGKAH_AKTIFKAN_SESUDAH_TUKAR


def _langkah(sesi, job, langkah: str, **lain) -> dict:
    # Ditulis SEBELUM langkahnya dijalankan (spec §10.4). Hanya baris job yang
    # di-commit di sini: baris hosting_vps selalu di transaksinya sendiri
    # (pola kunci reaper, carry Task 6).
    return stg.simpan_kemajuan(sesi, job, langkah_aktifkan=langkah, **lain)


def _periksa_awal(job, h: HostingVps) -> None:
    """Gerbang tanpa ubah sebelum langkah pertama (R15): status, salinan, dan IP hosting lama."""
    k = stg.kemajuan(job)
    awal = k.get("status_hosting_awal")
    if awal not in STATUS_BOLEH_AKTIFKAN:
        raise stg.GalatDitolakTanpaUbah(PESAN_STATUS_AKTIFKAN)
    if (job.payload or {}).get("tanpa_tarik_ulang"):
        # Salinan setengah jadi hanya bisa dirampungkan tarik (spec §10.3);
        # mengaktifkannya tanpa tarik menyajikan situs rusak.
        if h.ditarik_pada is None or (awal == StatusHosting.gagal.value
                                      and k.get("gagal_asal_awal") == hu.ASAL_SALINAN):
            raise stg.GalatDitolakTanpaUbah(PESAN_SALINAN_BELUM_UTUH)
    elif not hu.alamat_lama_sah(h.ip_lama):
        # Tarik terakhir hanya lewat IP lama yang TERSIMPAN (RF4): DNS domain
        # sudah menunjuk VPS, jadi tidak ada sumber lain yang benar. Ditolak
        # sebelum DNS dan certbot supaya tidak ada validasi yang terbuang.
        raise stg.GalatDitolakTanpaUbah(hu.PESAN_IP_LAMA)


def _cek_dns(sesi, job, h: HostingVps) -> None:
    _langkah(sesi, job, "dns")
    # Jaringan di luar kunci baris apa pun (Global Constraints); hasilnya
    # hanya memuat alamat yang lolos `ipaddress` dan gagal tertutup (L15).
    hasil = dns.periksa_dns(h)
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    baris.dns_hasil = hasil.ke_json()
    baris.dns_dicek_pada = hu.sekarang()
    sesi.commit()
    if not hasil.ok:
        raise stg.GalatDitolakTanpaUbah(hasil.pesan)


def _sertifikat(sesi, job, h: HostingVps, pb) -> None:
    _langkah(sesi, job, "sertifikat")
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    if not dns.backoff_mengizinkan(baris, hu.sekarang(), bool((job.payload or {}).get("manual"))):
        # Batas Let's Encrypt (§8.4): jeda sesudah validasi gagal berlaku apa
        # pun yang mengantrekan job ini. Jeda bukan kegagalan baru.
        raise stg.GalatDitolakTanpaUbah(PESAN_SERTIFIKAT)
    try:
        with stg.detak_latar(sesi, job):
            # Idempoten; memastikan port 80 domain melayani tantangan ACME.
            pb.prod_domain(h.nama)
    except GalatPembantu as exc:
        if exc.tanpa_ubah:
            # Kunci nginx/router sibuk (putusan L4/L8): dijadwalkan ulang
            # sebagai sibuk oleh pembungkus (putusan L10), bukan gagal.
            raise
        log.warning("prod-domain %s gagal sebelum sertifikat: %s", h.nama, exc.kode)
        raise stg.GalatDitolakTanpaUbah(PESAN_NGINX) from None
    try:
        with stg.detak_latar(sesi, job):
            pb.prod_sertifikat(h.nama)
    except GalatPembantu as exc:
        if exc.tanpa_ubah:
            # Keluar 3 bukan penolakan CA (kunci nginx sibuk sesudah certbot,
            # atau prasyarat): sibuk, tanpa menambah backoff. Percobaan ulang
            # tidak memvalidasi lagi sertifikat yang sudah terbit
            # (certbot --keep-until-expiring).
            raise
        log.warning("Sertifikat domain %s gagal: %s", h.domain, exc.kode)
        baris = sesi.get(HostingVps, h.id, populate_existing=True)
        # R15 + backoff §8.4: tidak diulang segera; cron/tombol menjadwalkan ulang.
        baris.sertifikat_gagal_kali = (baris.sertifikat_gagal_kali or 0) + 1
        baris.sertifikat_gagal_pada = hu.sekarang()
        sesi.commit()
        raise stg.GalatDitolakTanpaUbah(PESAN_SERTIFIKAT) from None
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    baris.sertifikat_pada = hu.sekarang()
    baris.sertifikat_gagal_kali = 0
    baris.sertifikat_gagal_pada = None
    sesi.commit()


def _tarik_terakhir(sesi, job, site, h: HostingVps, pb) -> None:
    _langkah(sesi, job, "tarik")
    if (job.payload or {}).get("tanpa_tarik_ulang"):
        return
    k = stg.kemajuan(job)
    selesai_pada = _waktu_kemajuan(k, "tarik_selesai_pada")
    if k.get("tahap") in hu.TAHAP_SALINAN_UTUH:
        if selesai_pada is not None and hu.sekarang() - selesai_pada <= UMUR_TARIK_TERAKHIR:
            # Putusan L17: tarik job ini selesai belum lama (mis. tukar ditolak
            # sibuk lalu diulang); dipakai lagi supaya hosting lama tidak
            # diekspor ulang setiap percobaan.
            return
        # Lebih tua dari itu: hosting lama masih menerima data selama job
        # menunggu, jadi tarik dimulai lagi dari manifest (inkremental
        # terhadap files/), bukan dilewati.
        k.pop("tahap")
    # Data terakhir dari hosting lama, lewat IP yang dipatok (RF4): DNS
    # domain sudah menunjuk VPS sendiri pada titik ini.
    k = salin(sesi, job, site, h, hu.klien_lama(site, h), pb, k)
    rampungkan_salinan(sesi, job, site, h, k, None)
    if selesai_pada is None:
        # Tarik pertama job ini tuntas: kemajuan sungguhan, rentetan sibuk
        # sebelumnya selesai (M4). Tarik penyegaran bukan kemajuan baru.
        hu._akhiri_rentetan_sibuk(sesi, job)
    stg.simpan_kemajuan(sesi, job, tarik_selesai_pada=hu.sekarang().isoformat())


def _waktu_kemajuan(k: dict, kunci: str) -> datetime | None:
    try:
        waktu = datetime.fromisoformat(k[kunci])
    except (KeyError, TypeError, ValueError):
        return None
    return waktu if waktu.tzinfo else waktu.replace(tzinfo=timezone.utc)


def _waktu_tukar(k: dict) -> datetime:
    return _waktu_kemajuan(k, "tukar_pada") or hu.sekarang()


def _pasang_penanda(sesi, h: HostingVps, waktu: datetime) -> None:
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    if baris.dilayani_vps_pada is None:
        baris.dilayani_vps_pada = waktu
        sesi.commit()


def _mulai_tukar(sesi, job, h: HostingVps) -> None:
    """Tulis-lebih-dulu (RF5): penanda di-commit sebelum perintah apa pun dikirim ke VPS.

    Dua commit, baris job lebih dulu (baris job dan hosting_vps tidak pernah
    satu transaksi, carry Task 6). Sejak commit pertama job menyentuh
    produksi (R26). Terhenti sebelum commit kedua: `_kirim_tukar` mengisi
    penanda hosting sebelum prod-aktifkan dikirim. Urutan sebaliknya bisa
    meninggalkan `dilayani_vps_pada` terisi dengan langkah sebelum tukar,
    yang percobaan berikutnya baca sebagai "Periksa ulang" -- prod-aktifkan
    tidak pernah dikirim dan pindah_tarik ditolak selamanya.
    """
    sekarang = hu.sekarang()
    _langkah(sesi, job, "tukar", tukar_pada=sekarang.isoformat(), tukar_dikirim=False)
    _pasang_penanda(sesi, h, sekarang)


def _cabut_tukar(sesi, job, akar: Path, h: HostingVps) -> None:
    """prod-aktifkan keluar 3 pada kiriman pertama: pasti tanpa perubahan (Koreksi #5).

    Urutan: (1) `tukar_dikirim=False` di job, (2) pemblokir email dipasang
    lagi -- situs masih pratinjau, konstanta WPMGR_PRATINJAU tetap ada
    (Koreksi #6) --, (3) `dilayani_vps_pada` dicabut di baris hosting, (4)
    langkah job kembali ke `dns`. Setiap tulisan DB satu commit sendiri
    (baris job dan hosting_vps tidak pernah satu transaksi). Terhenti di mana
    pun meninggalkan job di `tukar` dengan `tukar_dikirim` False: percobaan
    berikutnya mengirim lagi sebagai kiriman pertama (penanda hosting yang
    hilang dipasang lagi oleh `_kirim_tukar`), tidak pernah penanda terisi
    dengan langkah sebelum tukar. Mu-plugin yang gagal ditulis dilaporkan
    sesudah penanda dicabut: salinan perlu disalin ulang (`gagal`
    'salinan'), bukan produksi yang tersentuh.
    """
    # Langkah pertama, dipagari klaim: worker zombi (klaimnya sudah direbut
    # reaper) berhenti di KlaimHilang di sini sebelum menyentuh mu-plugin
    # atau penanda. Terhenti sesudahnya, percobaan berikutnya melihat
    # `tukar_dikirim` False, jadi keluar 3 berikutnya tetap diperlakukan
    # sebagai kiriman pertama dan pencabutan ini diulang (review Task 10 I1).
    stg.simpan_kemajuan(sesi, job, tukar_dikirim=False)
    galat = None
    try:
        tulis_mu_plugin(akar)
    except (SiteError, OSError) as exc:
        galat = exc
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    baris.dilayani_vps_pada = None
    sesi.commit()
    _langkah(sesi, job, "dns", tukar_pada=None)
    if galat is not None:
        raise galat


def _kirim_tukar(sesi, job, site, h: HostingVps, pb) -> None:
    akar = hu.dir_hosting(site.id)
    k = stg.kemajuan(job)
    # Penanda hosting wajib ter-commit sebelum kiriman ini (lihat _mulai_tukar).
    _pasang_penanda(sesi, h, _waktu_tukar(k))
    pertama = not k.get("tukar_dikirim")
    try:
        # Dihapus SEBELUM prod-aktifkan; berkas yang tertinggal tetap diam
        # karena konstantanya dicabut prod-aktifkan (spec §7.6).
        hapus_berkas(akar / "files", MU_PLUGIN_PRATINJAU)
    except (PathTidakAman, OSError):
        tarik._tambah_peringatan(k, "Mu-plugin pratinjau tidak dapat dihapus; berkasnya diam tanpa konstanta.")
        stg.simpan_kemajuan(sesi, job, peringatan=k["peringatan"])
    stg.simpan_kemajuan(sesi, job, tukar_dikirim=True)
    try:
        with stg.detak_latar(sesi, job):
            pb.prod_aktifkan(h.nama)
    except GalatPembantu as exc:
        if not (pertama and exc.tanpa_ubah):
            # Hasil tidak pasti, atau kiriman ulang: kiriman sebelumnya bisa
            # sudah menulis MODE=aktif (putusan L5), jadi keluar 3 kali ini
            # (kunci sibuk) tidak membuktikan situs masih pratinjau. Produksi
            # tersentuh: diulang menurut R26, tidak pernah ditolak tanpa ubah
            # (putusan L12).
            raise
        _cabut_tukar(sesi, job, akar, h)
        # Dilempar ulang sebagai GalatPembantu keluar 3: pembungkus
        # menjadwalkannya ulang sebagai sibuk (putusan L10; kunci router
        # dipegang impor situs lain sampai 3 jam). Lewat jendela sibuk, status
        # kembali seperti sebelum job dengan pesan tetap ini (spec §10.4);
        # kunci yang sibuk tidak menyalahkan sertifikat/database/container (M5).
        pesan = PESAN_SERVER_SIBUK if exc.sibuk else PESAN_TUKAR_DITOLAK
        raise GalatPembantu(exc.kode, pesan, sibuk=exc.sibuk) from None
    # Kemajuan sungguhan: rentetan sibuk sebelum tukar selesai (M4).
    hu._akhiri_rentetan_sibuk(sesi, job)
    _langkah(sesi, job, "verifikasi")


def _verifikasi(sesi, job, site, h: HostingVps) -> None:
    akar = hu.dir_hosting(site.id)
    masalah = verifikasi(h, akar)
    if masalah:
        # Rincian (nama host milik dashboard, kode HTTP) hanya ke log server.
        log.warning("Verifikasi aktivasi %s gagal: %s", h.domain, "; ".join(masalah))
        raise hu.GalatHosting(TRANSIENT, PESAN_VERIFIKASI)
    try:
        baca_terbatas(akar / "files", MU_PLUGIN_PRATINJAU, 1)
    except (PathTidakAman, OSError):
        pass
    else:
        k = stg.kemajuan(job)
        tarik._tambah_peringatan(k, "Mu-plugin pratinjau masih ada di situs (diam tanpa konstanta); hapus manual.")
        stg.simpan_kemajuan(sesi, job, peringatan=k["peringatan"])
    _langkah(sesi, job, "beres")


def _beres(sesi, job, site, h: HostingVps) -> dict:
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    sekarang = hu.sekarang()
    baris.status = StatusHosting.aktif
    baris.aktif_pada = sekarang
    baris.galat = None
    baris.gagal_asal = None
    k = stg.kemajuan(job)
    hasil = {"domain": baris.domain, "aktif_pada": sekarang.isoformat(),
             "peringatan": list(k.get("peringatan") or [])[:10]}
    stg.catat_aktivitas(sesi, site.id, job, "Site dihosting di VPS", hasil)
    sesi.commit()
    # Backup pertama diantrekan cron hosting-cek-dns (Koreksi #2): job ini
    # masih memegang uq_jobs_hosting_aktif sampai worker menandainya sukses.
    return hasil


def _periksa_ulang(sesi, job) -> None:
    """Tombol Periksa ulang untuk situs yang sudah dilayani VPS (Koreksi #13, putusan L16), jendela R26 baru.

    Dari `gagal` (asal produksi): masuk di `tukar` sebagai kiriman ulang --
    prod-aktifkan idempoten di bawah MODE=aktif (putusan L5), jadi aktivasi
    yang dulu terhenti di tengah dituntaskan, lalu verifikasi. Dari `aktif`:
    hanya verifikasi. Status lain dengan `dilayani_vps_pada` terisi tidak
    semestinya ada: ditolak tanpa ubah (langkah masih sebelum tukar).
    Tanpa DNS, sertifikat, dan tarik: data situs kini di VPS.
    """
    awal = stg.kemajuan(job).get("status_hosting_awal")
    sekarang = hu.sekarang().isoformat()
    if awal == StatusHosting.gagal.value:
        _langkah(sesi, job, "tukar", tukar_pada=sekarang, tukar_dikirim=True)
    elif awal == StatusHosting.aktif.value:
        _langkah(sesi, job, "verifikasi", tukar_pada=sekarang)
    else:
        raise stg.GalatDitolakTanpaUbah(PESAN_PERIKSA_ULANG)


def aktifkan(sesi, job, site, h: HostingVps, pb) -> dict:
    # Dibaca ulang: sesi tidak mengedaluwarsakan objek saat commit.
    h = sesi.get(HostingVps, h.id, populate_existing=True)
    langkah = stg.kemajuan(job).get("langkah_aktifkan")
    if langkah not in LANGKAH_AKTIFKAN_SESUDAH_TUKAR:
        if h.dilayani_vps_pada is not None:
            _periksa_ulang(sesi, job)
        else:
            # Sebelum tukar, percobaan ulang selalu mulai lagi dari dns: DNS
            # bisa saja dikembalikan pengguna (spec §10.4).
            _periksa_awal(job, h)
            _cek_dns(sesi, job, h)
            _sertifikat(sesi, job, h, pb)
            _tarik_terakhir(sesi, job, site, h, pb)
            # Titik terakhir untuk batal, penghentian worker, dan detak
            # sebelum produksi disentuh.
            stg.titik_potongan(sesi, job, h)
            _mulai_tukar(sesi, job, h)
    if stg.kemajuan(job).get("langkah_aktifkan") == "tukar":
        _kirim_tukar(sesi, job, site, h, pb)
    if stg.kemajuan(job).get("langkah_aktifkan") == "verifikasi":
        _verifikasi(sesi, job, site, h)
    return _beres(sesi, job, site, h)


def tangani_pindah_aktifkan(sesi, job, klien) -> dict:
    """Handler worker; klien bawaan worker diabaikan (lihat tangani_pindah_tarik)."""
    def inti(sesi, job, site, h):
        return aktifkan(sesi, job, site, h, stg.buat_pembantu())

    site_id = job.site_id
    try:
        return hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS", boleh_batal=boleh_batal_aktifkan)
    except stg.KlaimHilang:
        raise
    except Exception:
        bersihkan_bila_final(sesi, site_id)
        raise
