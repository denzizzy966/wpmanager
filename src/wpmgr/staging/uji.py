"""Job staging_uji_update: tarik -> sebelum -> update -> sesudah -> nilai (spec §8.1).

Penilaiannya heuristik (spec §17): halaman hidup, tidak ada fatal baru, dan
tidak ada halaman yang menyusut drastis. Hasilnya tidak mengunci tombol
update produksi; keputusan tetap di pengguna.

Semua yang dibaca dari staging -- HTML halaman dan log PHP -- ditulis oleh
kode salinan produksi yang bisa saja disusupi, jadi diperlakukan sebagai
masukan penyerang: dibaca dengan tenggat dan batas byte, diurai secara
linear, dan dibersihkan sebelum masuk database atau UI.
"""

import os
import re
import time
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select

from wpmgr.config import get_settings
from wpmgr.crypto import dekripsi_secret
from wpmgr.models import Staging, StagingUji, StatusStaging, TrafficRincian
from wpmgr.site_client import MelebihiBatas, TanpaHasil, TenggatHabis, minta_bertenggat
from wpmgr.staging import tarik as tarik_mod
from wpmgr.staging import umum
from wpmgr.staging.aman import (
    PathTidakAman,
    bersih_json,
    bersih_teks,
    buka_baca,
    hapus_berkas,
)
from wpmgr.staging.pembantu import GalatPembantu, cookie_akses
from wpmgr.staging.rencana import format_byte

JALUR_TETAP = ("/", "/wp-login.php")
JUMLAH_JALUR_TRAFFIC = 3
MAKS_PAKET = 20
BATAS_HTML = 5 * 1024 * 1024
BATAS_LOG = 1024 * 1024
# Log yang dibanjiri peringatan tidak boleh menyembunyikan fatal sesudahnya:
# dibaca per BATAS_LOG, paling banyak sekian blok per pembacaan.
MAKS_BLOK_LOG = 8
# Satu baris log yang lebih panjang dari ini dipotong sebelum diurai.
MAKS_BARIS_LOG = 64 * 1024
MAKS_FATAL = 10
TENGGAT_SIAP = 60
# Probe membaca halaman yang dirender PHP staging: tenggat total per
# permintaan (bukan hanya timeout per baca) supaya halaman yang meneteskan
# byte tidak menahan job (putusan F11).
TIMEOUT_PROBE = 20.0
TENGGAT_PROBE = 30.0
# Judul dicari hanya di awal dokumen, dan isinya dibatasi sebelum didekode.
BATAS_CARI_JUDUL = 512 * 1024
BATAS_JUDUL_MENTAH = 4096
POLA_FATAL = re.compile(r"PHP (?:Fatal error|Parse error)")
POLA_JALUR = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,199}")
POLA_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}")
POLA_BERKAS_PLUGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.php")
POLA_VERSI = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,29}")
# Skrip pembantu mewajibkan versi core diawali angka (cmd_wpcli).
POLA_VERSI_CORE = re.compile(r"[0-9][0-9A-Za-z._-]{0,29}")
LOG_PHP = "log/php-error.log"
PENANDA_DIUBAH = "log/diubah"
_AKHIR_TAG = frozenset(b">/ \t\r\n\f")


class PaketTidakSah(ValueError):
    """Payload paket uji ditolak; pesannya teks tetap yang aman untuk UI."""


def slug_wpcli(tipe: str, slug: str) -> str | None:
    """Nama paket untuk wp-cli. Slug plugin Lapis 1 adalah 'dir/berkas.php'."""
    if tipe == "core":
        return "core"
    if not isinstance(slug, str):
        return None
    if tipe == "plugin":
        if "/" in slug:
            nama, berkas = slug.split("/", 1)
            # Bagian berkas ikut disimpan dan ditampilkan: dibatasi ketat juga.
            if not POLA_BERKAS_PLUGIN.fullmatch(berkas):
                return None
        else:
            nama = slug.removesuffix(".php")
    else:
        nama = slug
    return nama if POLA_SLUG.fullmatch(nama) else None


def urai_paket(daftar) -> list[dict]:
    if not isinstance(daftar, list) or not 1 <= len(daftar) <= MAKS_PAKET:
        raise PaketTidakSah("Daftar paket uji kosong atau terlalu panjang.")
    hasil = []
    for p in daftar:
        p = p if isinstance(p, dict) else {}
        tipe, slug, ke = p.get("tipe"), p.get("slug"), p.get("ke")
        pola_versi = POLA_VERSI_CORE if tipe == "core" else POLA_VERSI
        if tipe not in ("plugin", "theme", "core") or not isinstance(slug, str) \
                or not isinstance(ke, str) or not pola_versi.fullmatch(ke):
            raise PaketTidakSah("Paket uji tidak sah.")
        wp = slug_wpcli(tipe, slug)
        if wp is None:
            raise PaketTidakSah("Slug paket uji tidak sah.")
        dari = p.get("dari")
        hasil.append({"tipe": tipe, "slug": bersih_teks(slug, 200), "wpcli": wp,
                      "dari": bersih_teks(dari, 30) if isinstance(dari, str) else None, "ke": ke})
    return hasil


def jalur_uji(sesi, site_id, hari_ini) -> list[str]:
    """`/`, `/wp-login.php`, dan 3 halaman teratas traffic plugin 30 hari.

    Kunci traffic berasal dari connector produksi: hanya path relatif
    sederhana (tanpa skema, query, atau `//host`) yang dipakai.
    """
    jumlah = func.sum(TrafficRincian.kunjungan)
    baris = sesi.execute(
        select(TrafficRincian.kunci, jumlah)
        .where(TrafficRincian.site_id == site_id, TrafficRincian.sumber == "plugin",
               TrafficRincian.dimensi == "halaman", TrafficRincian.tanggal >= hari_ini - timedelta(days=30))
        .group_by(TrafficRincian.kunci)
        .order_by(jumlah.desc(), TrafficRincian.kunci)
        .limit(20)
    ).all()
    hasil = list(JALUR_TETAP)
    for kunci, _ in baris:
        if len(hasil) >= len(JALUR_TETAP) + JUMLAH_JALUR_TRAFFIC:
            break
        if isinstance(kunci, str) and POLA_JALUR.fullmatch(kunci) and not kunci.startswith("//") and kunci not in hasil:
            hasil.append(kunci)
    return hasil


def judul_html(isi: bytes) -> str | None:
    """Isi <title> pertama, dicari secara linear tanpa regex (tanpa ReDoS).

    Pencarian memakai bytes.lower(), yang hanya mengubah huruf ASCII
    sehingga indeksnya sama dengan isi asli.
    """
    data = isi[:BATAS_CARI_JUDUL]
    kecil = data.lower()
    i = kecil.find(b"<title")
    while i != -1:
        sesudah = i + len(b"<title")
        if sesudah < len(kecil) and kecil[sesudah] in _AKHIR_TAG:
            break
        i = kecil.find(b"<title", sesudah)
    if i == -1:
        return None
    buka = kecil.find(b">", i)
    if buka == -1:
        return None
    tutup = kecil.find(b"</title", buka)
    if tutup == -1:
        return None
    mentah = data[buka + 1:min(tutup, buka + 1 + BATAS_JUDUL_MENTAH)]
    teks = " ".join(mentah.decode("utf-8", "replace").replace("\x00", "").split())
    return bersih_teks(teks, 200) or None


def probe(http: httpx.Client, dasar: str, host: str, cookie: str, jalur: str) -> dict:
    """Ambil satu halaman staging lewat router: status, judul, dan ukuran HTML.

    Redirect tidak diikuti (3xx dihitung hidup). Pembacaan dibatasi
    BATAS_HTML byte dan TENGGAT_PROBE detik untuk seluruh permintaan; yang
    tidak menjawab dalam tenggat dianggap tidak dapat dihubungi.
    """
    headers = {"Host": host, "Cookie": cookie, "Accept": "text/html",
               # Byte di kabel yang dihitung; tanpa dekompresi atas masukan
               # staging. Koneksi baru per probe supaya tenggat bisa memutus soketnya.
               "Accept-Encoding": "identity", "Connection": "close"}
    try:
        status, _, isi = minta_bertenggat(http, "GET", dasar + jalur, headers=headers,
                                          timeout=min(TIMEOUT_PROBE, TENGGAT_PROBE), tenggat=TENGGAT_PROBE,
                                          batas_byte=BATAS_HTML, potong=True)
    except (httpx.HTTPError, TenggatHabis, TanpaHasil, MelebihiBatas):
        return {"status": 0, "judul": None, "ukuran": 0}
    return {"status": status, "judul": judul_html(isi), "ukuran": len(isi)}


def tunggu_siap(http, dasar, host, cookie, tidur=time.sleep) -> None:
    """Container baru dibuat/diperbarui butuh beberapa detik sebelum Apache menjawab."""
    batas = time.monotonic() + TENGGAT_SIAP
    while True:
        if probe(http, dasar, host, cookie, "/")["status"] not in (0, 502, 503, 504) or time.monotonic() >= batas:
            return
        tidur(2)


def _akar_dan_relatif(berkas: Path, akar: Path | None) -> tuple[Path, str]:
    if akar is None:
        return berkas.parent, berkas.name
    return akar, berkas.relative_to(akar).as_posix()


def ukuran_log(akar: Path, relatif: str = LOG_PHP) -> int:
    """Ukuran log PHP staging, 0 bila tidak ada atau tidak aman dibaca (symlink, FIFO)."""
    try:
        with buka_baca(akar, relatif) as f:
            return os.fstat(f.fileno()).st_size
    except (PathTidakAman, OSError, ValueError):
        return 0


def _fatal_dari(baris: bytes, hasil: list[str]) -> None:
    teks = baris[:MAKS_BARIS_LOG].decode("utf-8", "replace")
    if POLA_FATAL.search(teks):
        # Path di dalam container, bukan path VPS; dipendekkan supaya terbaca.
        hasil.append(bersih_teks(teks.rstrip("\r").replace("/var/www/html/", ""), 300))


def baca_log_baru(berkas: Path, posisi: int, akar: Path | None = None) -> list[str]:
    """Baris `PHP Fatal error`/`PHP Parse error` yang ditulis sesudah `posisi`.

    `log/` di-bind mount ke container staging: berkasnya dibuka lewat
    `aman.buka_baca` (tanpa symlink, hanya berkas biasa) relatif terhadap
    `akar` (bawaan: direktori berkas itu sendiri), dan dibaca paling banyak
    MAKS_BLOK_LOG x BATAS_LOG byte. Log yang dipangkas atau diputar sejak
    "sebelum" (ukurannya kini lebih kecil dari posisi) dibaca dari awal.
    """
    hasil: list[str] = []
    try:
        akar, relatif = _akar_dan_relatif(berkas, akar)
        with buka_baca(akar, relatif) as f:
            if os.fstat(f.fileno()).st_size < posisi:
                posisi = 0
            f.seek(max(0, posisi))
            sisa = b""
            for _ in range(MAKS_BLOK_LOG):
                blok = f.read(BATAS_LOG)
                if not blok:
                    break
                *baris, sisa = (sisa + blok).split(b"\n")
                # Baris tanpa akhir yang sangat panjang tidak ditampung utuh.
                sisa = sisa[:MAKS_BARIS_LOG]
                for b in baris:
                    _fatal_dari(b, hasil)
                    if len(hasil) >= MAKS_FATAL:
                        return hasil
            if sisa:
                _fatal_dari(sisa, hasil)
    except (PathTidakAman, OSError, ValueError):
        return []
    return hasil[:MAKS_FATAL]


def nilai_uji(sebelum: dict, sesudah: dict, fatal_baru: list, update_gagal: list) -> tuple[str, list[str]]:
    alasan = [f"Update {u['slug']} gagal: {u['pesan']}" for u in update_gagal]
    for jalur, h in sesudah.items():
        status = h.get("status") or 0
        if not 200 <= status < 400:
            alasan.append(f"{jalur} membalas HTTP {status}" if status else f"{jalur} tidak dapat dihubungi")
            continue
        s = sebelum.get(jalur) or {}
        if s.get("ukuran", 0) > 0 and h.get("ukuran", 0) < s["ukuran"] * 0.5:
            alasan.append(f"{jalur} menyusut dari {format_byte(s['ukuran'])} ke {format_byte(h.get('ukuran', 0))}")
    if fatal_baru:
        alasan.append(f"{len(fatal_baru)} error fatal baru di log PHP staging")
    return ("lolos" if not alasan else "gagal"), alasan


def _probe_semua(sesi, job, staging, http, dasar, host, cookie, jalur: list[str]) -> dict:
    hasil = {}
    for j in jalur:
        # Tiap probe bisa memakan sampai TENGGAT_PROBE: batal dan detak di sela.
        umum.titik_potongan(sesi, job, staging)
        hasil[j] = probe(http, dasar, host, cookie, j)
    return hasil


def _argumen_update(p: dict) -> tuple[str, ...]:
    if p["tipe"] == "core":
        return ("core", "update", f"--version={p['ke']}")
    return (p["tipe"], "update", p["wpcli"], f"--version={p['ke']}")


def uji(sesi, job, site, staging: Staging, klien, pb) -> dict:
    try:
        paket = urai_paket((job.payload or {}).get("paket"))
    except PaketTidakSah as exc:
        raise umum.galat_ditolak(exc.args[0]) from None
    if not staging.rahasia_router_terenkripsi:
        raise umum.galat_ditolak("Akses preview staging belum dibuat; buat ulang kata sandi preview.")
    umum.perbarui_diubah(staging)
    sesi.commit()
    k = umum.kemajuan(job)
    if "tahap_uji" not in k:
        if staging.diubah_pada and staging.ditarik_pada and staging.diubah_pada > staging.ditarik_pada \
                and not (job.payload or {}).get("konfirmasi"):
            raise umum.galat_ditolak("Staging diubah sejak tarik terakhir; perubahan itu akan tertimpa oleh uji ini. "
                                     "Konfirmasi dulu untuk melanjutkan.")
        k = umum.simpan_kemajuan(sesi, job, tahap_uji="tarik")

    akar = umum.dir_site(site.id)
    if k["tahap_uji"] == "tarik":
        tarik_mod.tarik(sesi, job, site, staging, klien, pb, akhir_status=False)
        k = umum.simpan_kemajuan(sesi, job, tahap_uji="sebelum")

    host = umum.host_staging(staging)
    dasar = get_settings().staging_router_url
    cookie = "; ".join(f"{a}={b}" for a, b in cookie_akses(
        dekripsi_secret(staging.rahasia_router_terenkripsi), host, int(time.time())).items())
    http = umum.buat_http()
    try:
        if k["tahap_uji"] == "sebelum":
            tunggu_siap(http, dasar, host, cookie)
            jalur = jalur_uji(sesi, site.id, umum.sekarang().date())
            sebelum = _probe_semua(sesi, job, staging, http, dasar, host, cookie, jalur)
            # Posisi diambil sesudah probe "sebelum": fatal yang sudah muncul
            # di salinan produksi sebelum update tidak dihitung sebagai akibatnya.
            k = umum.simpan_kemajuan(sesi, job, tahap_uji="update", jalur=jalur, update=[],
                                     sebelum=sebelum, log_posisi=ukuran_log(akar))
        if k["tahap_uji"] == "update":
            hasil_update = list(k.get("update") or [])
            for p in paket[len(hasil_update):]:
                umum.titik_potongan(sesi, job, staging)
                try:
                    # wp-cli update bisa berjalan belasan menit: detak dari
                    # utas latar supaya reaper tidak merebut job (putusan F7).
                    # Masuk ke blok ini meng-commit sesi; kemajuan sudah
                    # tersimpan di titik ini.
                    with umum.detak_latar(sesi, job):
                        pb.wpcli(staging.nama, *_argumen_update(p))
                    hasil_update.append({"slug": p["slug"], "ok": True, "pesan": None})
                except GalatPembantu as exc:
                    # exc.pesan adalah teks tetap (tanpa stderr).
                    hasil_update.append({"slug": p["slug"], "ok": False, "pesan": exc.pesan})
                k = umum.simpan_kemajuan(sesi, job, update=hasil_update)
            umum.titik_potongan(sesi, job, staging)
            try:
                with umum.detak_latar(sesi, job):
                    pb.wpcli(staging.nama, "cache", "flush")
            except GalatPembantu:
                pass
            k = umum.simpan_kemajuan(sesi, job, tahap_uji="sesudah")
        if k["tahap_uji"] == "sesudah":
            tunggu_siap(http, dasar, host, cookie)
            sesudah = _probe_semua(sesi, job, staging, http, dasar, host, cookie, k["jalur"])
            k = umum.simpan_kemajuan(sesi, job, tahap_uji="nilai", sesudah=sesudah,
                                     fatal_baru=baca_log_baru(akar / LOG_PHP, k["log_posisi"], akar=akar))
    finally:
        http.close()

    # JSONB tidak menyimpan urutan kunci: urutan halaman selalu dari k["jalur"].
    sebelum = {j: k["sebelum"].get(j) or {} for j in k["jalur"]}
    sesudah = {j: k["sesudah"].get(j) or {} for j in k["jalur"]}
    hasil, alasan = nilai_uji(sebelum, sesudah, k["fatal_baru"], [u for u in k["update"] if not u["ok"]])
    paket_simpan = [{"tipe": p["tipe"], "slug": p["slug"], "dari": p["dari"], "ke": p["ke"]} for p in paket]
    sesi.add(StagingUji(site_id=site.id, job_id=job.id, paket=paket_simpan, hasil=hasil, pemeriksaan=bersih_json({
        "halaman": [{"jalur": j, "sebelum": k["sebelum"].get(j), "sesudah": k["sesudah"].get(j)} for j in k["jalur"]],
        "fatal_baru": k["fatal_baru"], "update": k["update"], "alasan": alasan,
    })))
    st = sesi.get(Staging, staging.id, populate_existing=True)
    st.status = StatusStaging.siap
    # Update yang dijalankan uji ini bukan perubahan pengguna. log/ di-bind
    # mount ke container: dihapus hanya lewat `aman`.
    try:
        hapus_berkas(akar, PENANDA_DIUBAH)
    except (PathTidakAman, OSError):
        pass
    st.diubah_pada = None
    umum.catat_aktivitas(sesi, site.id, job, f"Uji update di staging: {hasil}",
                         {"paket": paket_simpan, "alasan": alasan[:10]})
    sesi.commit()
    return {"hasil": hasil, "alasan": alasan}


def tangani_staging_uji_update(sesi, job, klien) -> dict:
    def inti(sesi, job, site, staging):
        return uji(sesi, job, site, staging, klien, umum.buat_pembantu())

    site_id = job.site_id
    try:
        return umum.jalankan_staging(sesi, job, inti, StatusStaging.berjalan_uji, "Uji update di staging")
    except umum.KlaimHilang:
        raise
    except Exception:
        # Tarik di awal uji memakai area kerja tarik/ yang sama.
        tarik_mod.bersihkan_bila_final(sesi, site_id, StatusStaging.berjalan_uji)
        raise
