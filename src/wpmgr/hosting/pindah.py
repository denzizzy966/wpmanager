"""Job pindah_tarik (spec Lapis 4 §10.2-10.3): salin site dari hosting lama ke VPS.

Mesin tarik Lapis 3 (`tarik.tarik_inti`) menyalin ke HOSTING_DIR/<site_id>
lewat `TujuanHosting`; klien hosting lama dipatok IP dan baca-saja. Site lama
tidak pernah diubah. Sesudah `dilayani_vps_pada` terisi, setiap tarik ditolak
sebelum klien lama dihubungi (RF2).
"""

import shutil
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlalchemy import select

from wpmgr.config import get_settings
from wpmgr.connector_paket import isi_mu_plugin_pratinjau
from wpmgr.fitur import STAGING, punya_fitur
from wpmgr.hosting import umum as hu
from wpmgr.models import HostingVps, StatusHosting
from wpmgr.staging import tarik
from wpmgr.staging import umum as stg
from wpmgr.staging.aman import PathTidakAman, tulis_atomik
from wpmgr.staging.indeks import Indeks
from wpmgr.staging.pembantu import GalatPembantu, tulis_htpasswd_pratinjau
from wpmgr.staging.rencana import cek_ram, format_byte

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


def salin(sesi, job, site, h: HostingVps, klien, pb) -> dict:
    """Gerbang lalu `tarik_inti` ke HOSTING_DIR/<site_id>; dipakai pindah_tarik dan langkah tarik aktivasi."""
    if h.dilayani_vps_pada is not None:
        # RF2: selalu, juga pada percobaan ulang, sebelum hosting lama dihubungi.
        raise stg.GalatDitolakTanpaUbah(PESAN_SUDAH_DILAYANI)
    if not punya_fitur(site, STAGING):
        raise tarik._tolak(job, PESAN_IZIN)
    if not h.sandi_hash:
        raise tarik._tolak(job, PESAN_SANDI_BELUM)
    return tarik.tarik_inti(sesi, job, site, klien, tujuan_hosting(sesi, job, site, h, pb), stg.kemajuan(job))


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


def pindah_tarik(sesi, job, site, h: HostingVps, klien, pb) -> dict:
    if h.dilayani_vps_pada is not None:
        raise stg.GalatDitolakTanpaUbah(PESAN_SUDAH_DILAYANI)
    k_awal = stg.kemajuan(job)
    awal = k_awal.get("status_hosting_awal")
    if not (awal in STATUS_BOLEH_TARIK or (awal == "gagal" and k_awal.get("gagal_asal_awal") == hu.ASAL_SALINAN)):
        raise stg.GalatDitolakTanpaUbah(PESAN_STATUS_TARIK)
    pertama = h.ditarik_pada is None
    k = salin(sesi, job, site, h, klien, pb)
    sertifikat_ok = None
    if k.get("tahap") == "pratinjau":
        stg.titik_potongan(sesi, job, h)
        sertifikat_ok = True
        with stg.detak_latar(sesi, job):
            try:
                # Host pratinjau lewat server port 80 wildcard staging (spec §8.4).
                pb.sertifikat(f"vps-{h.nama}")
            except GalatPembantu as exc:
                if exc.tanpa_ubah:
                    # Keluar 3 (kunci sibuk, tanpa perubahan) bukan sertifikat
                    # yang ditolak: pembungkus menjadwalkan ulang dari tahap ini.
                    raise
                # Pratinjau HTTPS belum tersedia; dicoba lagi pada Salin ulang.
                sertifikat_ok = False
                tarik._tambah_peringatan(k, f"Sertifikat pratinjau belum terbit: {exc.pesan}")
            pb.prod_domain(h.nama)
        k = stg.simpan_kemajuan(sesi, job, tahap="selesai", peringatan=list(k.get("peringatan") or []))
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
