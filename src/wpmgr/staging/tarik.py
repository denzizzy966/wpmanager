"""Job staging_tarik: buat atau segarkan staging dari produksi (spec §6.2).

Urutan: pindai files/ (tarik baru saja), manifest berpaging, cek sumber
daya, sinkron berkas (hapus lalu ambil per paket/rentang), tanda air,
ekspor tabel per potongan, db-buat + db-impor, penyiapan runtime, dan
sertifikat. Setiap tahap mencatat kemajuannya di job.payload["kemajuan"]
dan commit, sehingga job yang diulang melanjutkan dari tahap dan kursornya.

Setiap balasan connector adalah masukan penyerang: diurai lewat
`wpmgr.staging.rencana`, dan semua tulisan ke files/ (di-bind mount ke
container staging) hanya lewat `wpmgr.staging.aman`. Area kerja tarik/ dan
indeks.jsonl berada langsung di bawah <site_id>/, tidak di-bind mount ke
container mana pun, jadi I/O biasa aman di sana.
"""

import errno
import hashlib
import json
import math
import re
import shutil
from pathlib import Path

from sqlalchemy import func, select

from wpmgr.config import get_settings
from wpmgr.connector_paket import isi_mu_plugin_staging
from wpmgr.crypto import dekripsi_secret
from wpmgr.errors import BAD_RESPONSE, SiteError
from wpmgr.fitur import STAGING, punya_fitur
from wpmgr.models import Staging, StatusStaging
from wpmgr.staging import umum
from wpmgr.staging.aman import (
    DILINDUNGI_STAGING,
    POLA_TABEL,
    PathTidakAman,
    angka,
    bersih_teks,
    hapus_berkas,
    hapus_direktori_kosong,
    hapus_tautan,
    tulis_atomik,
    tulis_bertahap,
    versi_php_staging,
)
from wpmgr.staging.indeks import Indeks, IndeksTerlaluBesar, pindai_lokal
from wpmgr.staging.pembantu import GalatPembantu, tulis_akses_router
from wpmgr.staging.rencana import (
    BERUBAH,
    HILANG,
    SELESAI,
    UKURAN_PAKET,
    BalasanTidakSah,
    Entri,
    RakitRentang,
    bagi_potongan,
    cek_disk,
    cek_maks_aktif,
    cek_ram,
    format_byte,
    halaman_manifest,
    hasil_paket,
    potongan_tabel,
    selisih,
    urai_tanda_air,
)
from wpmgr.staging.sql_impor import periksa_sql, sesuaikan_mariadb

MAKS_POTONGAN_TABEL = 200_000
MAKS_PERINGATAN = 50
MAKS_TABEL = 2000
MAKS_BATAS_UNGGAH = 4 * 1024 * 1024
# Berkas besar yang berubah di tengah rentang (atau hash akhirnya tidak
# cocok) diulang dari awal paling banyak sekian kali, lalu dilewati dengan
# peringatan; tidak pernah berputar tanpa batas.
MAKS_ULANG_RENTANG = 3
# Anggaran byte (lihat anggaran_berkas/anggaran_db). Connector yang disusupi
# bisa terus mengirim "berkas yang tumbuh" atau SQL tanpa akhir untuk
# memenuhi disk dashboard; pertumbuhan wajar tetap lolos.
TOLERANSI_BYTE = 64 * 1024 * 1024
# Rentang yang meneteskan sedikit byte per balasan: paling banyak sekian
# permintaan per berkas = ceil(total_maks / KEMAJUAN_MINIMUM) + TAMBAHAN_PERMINTAAN
# (untuk semua percobaan ulangnya), lalu berkas dilewati dengan peringatan.
KEMAJUAN_MINIMUM = 1024 * 1024
TAMBAHAN_PERMINTAAN = 16
# Manifest: entri per halaman yang diminta, total entri, dan total byte
# manifest.jsonl. Diperiksa SEBELUM ditulis, supaya berkas lokal dan
# Indeks.muat() atasnya tidak pernah bisa digiring melewati batas ini.
BATAS_HALAMAN_MANIFEST = 5000
MAKS_ENTRI_MANIFEST = 2_000_000
MAKS_BYTE_MANIFEST = 512 * 1024 * 1024
# Penghapusan berkas lokal: titik potongan (batal, detak) setiap sekian berkas.
HAPUS_PER_TITIK = 500
CHARSET_SAH = ("utf8mb4", "utf8", "utf8mb3", "latin1")
POLA_PREFIX = re.compile(r"[A-Za-z0-9_]{1,20}")
# Sama dengan cek_url di skrip pembantu: nilai ini dikirim ke search-replace.
POLA_URL = re.compile(r"https?://[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/-]{0,200})?")
# Galat berkas yang berarti server staging tidak sehat, bukan satu nama yang
# bermasalah: job dihentikan, bukan berkasnya dilewati.
ERRNO_FATAL = frozenset({errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC), errno.EROFS, errno.EIO})
MU_PLUGIN = "wp-content/mu-plugins/wpmgr-staging.php"


def anggaran_berkas(byte_total: int) -> int:
    """Byte berkas yang boleh diterima satu tarik: total manifest + 25% + toleransi."""
    return byte_total + byte_total // 4 + TOLERANSI_BYTE


def anggaran_db(ukuran_db: int) -> int:
    """Byte SQL ekspor yang boleh diterima: 3 x ukuran_db yang dilaporkan + toleransi.

    ukuran_db (Data_length + Index_length) hanya perkiraan, dan SQL-nya bisa
    lebih besar dari data mentah: nilai biner menjadi literal hex (2x), angka
    kecil menjadi teks, ditambah sintaks INSERT.
    """
    return 3 * ukuran_db + TOLERANSI_BYTE


def kebutuhan_disk(byte_berkas: int, ukuran_db: int) -> int:
    """Ruang disk yang dicadangkan sebelum tarik (M4): anggaran maksimum, bukan perkiraan.

    Berkas sampai anggaran_berkas, dump SQL sampai anggaran_db, ditambah
    database hasil impor (~ukuran_db). Memakai anggaran yang sama dengan
    batas saat menerima data, jadi tarik yang lolos cek disk tidak bisa
    menghabiskan sisa 15% sebelum batasnya menghentikan tarik.
    """
    return anggaran_berkas(byte_berkas) + anggaran_db(ukuran_db) + ukuran_db


def _tambah_peringatan(k: dict, teks: str) -> None:
    daftar = k.setdefault("peringatan", [])
    if len(daftar) < MAKS_PERINGATAN:
        daftar.append(bersih_teks(teks, 300))


def _simpan(sesi, job, k: dict, **perubahan) -> dict:
    """simpan_kemajuan yang selalu membawa peringatan yang terkumpul di `k`."""
    return umum.simpan_kemajuan(sesi, job, peringatan=list(k.get("peringatan") or []), **perubahan)


def _ulangi(fungsi):
    """Satu potongan diambil dan diurai; balasan yang tidak sesuai kontrak diulang seperti galat kabel."""
    def coba():
        try:
            return fungsi()
        except BalasanTidakSah as exc:
            raise SiteError(BAD_RESPONSE, str(exc)) from None

    return umum.ulangi(coba)


# ---- validasi dan data ------------------------------------------------------


def urai_tabel(daftar, prefix: str) -> list[dict]:
    """Daftar tabel dari connector: nama sah ber-prefix site, angka dijepit, PK yang bisa dikutip."""
    tabel = []
    for t in daftar if isinstance(daftar, list) else []:
        if not isinstance(t, dict) or len(tabel) >= MAKS_TABEL:
            continue
        nama = t.get("nama")
        if not isinstance(nama, str) or not POLA_TABEL.fullmatch(nama) or not nama.startswith(prefix):
            continue
        pk = t.get("pk") if isinstance(t.get("pk"), list) else []
        # Satu kolom PK yang tidak bisa dikutip aman: perlakukan sebagai tanpa PK.
        pk = pk if all(isinstance(c, str) and POLA_TABEL.fullmatch(c) for c in pk) else []
        tabel.append({"nama": nama, "baris": angka(t.get("baris"), 0, 2**62) or 0,
                      "ukuran": angka(t.get("ukuran"), 0, 2**62) or 0, "pk": pk})
    tabel.sort(key=lambda t: t["nama"])
    return tabel


def urai_info(info) -> dict:
    """Info site dari `info` MENTAH halaman manifest pertama, divalidasi sebelum dipakai di mana pun.

    `rencana.halaman_manifest` sengaja hanya meneruskan bendera; teks dan
    daftar tabel diurai ketat di sini.
    """
    if not isinstance(info, dict):
        raise umum.galat_gagal("Manifest produksi tidak membawa info site.")
    if info.get("multisite"):
        raise umum.galat_ditolak("Site multisite belum didukung staging.")
    if info.get("konten_di_luar"):
        raise umum.galat_ditolak("Folder wp-content site ini berada di luar folder WordPress; belum didukung staging.")
    prefix = info.get("table_prefix")
    if not isinstance(prefix, str) or not POLA_PREFIX.fullmatch(prefix):
        raise umum.galat_gagal("Table prefix produksi tidak sah.")
    alamat = {}
    for kunci in ("home", "siteurl"):
        nilai = info.get(kunci)
        nilai = nilai.rstrip("/") if isinstance(nilai, str) else ""
        if not POLA_URL.fullmatch(nilai):
            raise umum.galat_gagal(f"Nilai {kunci} produksi bukan alamat yang sah.")
        alamat[kunci] = nilai
    charset = info.get("charset") if info.get("charset") in CHARSET_SAH else "utf8mb4"
    php = bersih_teks(info.get("php"), 40) if isinstance(info.get("php"), str) else None
    versi, peringatan = versi_php_staging(php)
    mentah = info.get("tabel")
    tabel = urai_tabel(mentah, prefix)
    if not tabel:
        raise umum.galat_gagal("Produksi tidak melaporkan satu pun tabel database site ini.")
    ditolak = (len(mentah) if isinstance(mentah, list) else 0) - len(tabel)
    return {
        "prefix": prefix, "home": alamat["home"], "siteurl": alamat["siteurl"], "charset": charset,
        "php": php, "versi_php": versi, "php_peringatan": peringatan,
        # Connector mengirim 1 byte..4 MiB (bisa < 256 KB bila post_max_size kecil).
        "batas_unggah": angka(info.get("batas_unggah"), 1, MAKS_BATAS_UNGGAH) or MAKS_BATAS_UNGGAH,
        # post_max_size produksi terlalu kecil untuk potongan unggah yang
        # wajar (< 256 KB); dorong menolak lebih awal (Task 16).
        "unggah_terlalu_kecil": info.get("unggah_terlalu_kecil") is True,
        "tabel": tabel, "ukuran_db": min(sum(t["ukuran"] for t in tabel), 2**62),
        "tabel_dilewati": (angka(info.get("tabel_dilewati"), 0, 10**6) or 0) + max(0, ditolak),
    }


def urls_produksi(info: dict) -> list[str]:
    """Alamat produksi yang diganti ke alamat staging, yang terpanjang lebih dulu."""
    urls = set()
    for u in (info["home"], info["siteurl"]):
        urls.add(u)
        if u.startswith("https://"):
            urls.add("http://" + u[len("https://"):])
    return sorted(urls, key=lambda u: (-len(u), u))


def jumlah_aktif(sesi, staging: Staging) -> int:
    return sesi.scalar(select(func.count()).select_from(Staging).where(
        Staging.aktif.is_(True), Staging.id != staging.id)) or 0


# ---- penulisan berkas ---------------------------------------------------------


def tulis_berkas(files: Path, path: str, isi: bytes, mtime: int) -> None:
    """Tulis satu berkas kecil ke files/ staging (lewat `aman`, tanpa mengikuti symlink)."""
    tulis_atomik(files, path, isi, mtime)


def _fatal(exc: OSError) -> bool:
    return exc.errno in ERRNO_FATAL


# ---- manifest -----------------------------------------------------------------


def _baris_entri(e: Entri) -> str:
    return json.dumps({"p": e.path, "u": e.ukuran, "m": e.mtime, "h": e.hash}, ensure_ascii=False) + "\n"


def ambil_manifest(sesi, job, staging, klien, dir_kerja: Path, k: dict) -> dict:
    """Manifest produksi berpaging ke dir_kerja/manifest.jsonl (dipakai tarik dan dorong)."""
    berkas = dir_kerja / "manifest.jsonl"
    kursor = k.get("manifest_kursor")
    halaman = k.get("manifest_halaman") or 0
    if kursor is None:
        # Belum ada halaman yang tercatat (atau pengulangan setelah halaman
        # terakhir tanpa pindah tahap): mulai dari berkas kosong.
        berkas.unlink(missing_ok=True)
        halaman = 0
        k = _simpan(sesi, job, k, berkas_dilewati=0, manifest_entri=0, manifest_byte=0)
    while True:
        umum.titik_potongan(sesi, job, staging)
        halaman += 1

        def ambil(kursor=kursor, halaman=halaman):
            mentah = klien.staging_manifest(kursor, batas=BATAS_HALAMAN_MANIFEST)
            if isinstance(mentah.get("berkas"), list) and len(mentah["berkas"]) > BATAS_HALAMAN_MANIFEST:
                raise umum.galat_gagal("Halaman manifest produksi memuat lebih banyak entri daripada yang diminta.")
            return mentah, halaman_manifest(mentah, kursor, halaman=halaman)

        mentah, h = _ulangi(ambil)
        if kursor is None:
            k = _simpan(sesi, job, k, info=urai_info(mentah.get("info")))
        baris = [_baris_entri(e) for e in h.entri]
        entri = (k.get("manifest_entri") or 0) + len(baris)
        byte = (k.get("manifest_byte") or 0) + sum(len(b.encode("utf-8")) for b in baris)
        if entri > MAKS_ENTRI_MANIFEST or byte > MAKS_BYTE_MANIFEST:
            raise umum.galat_gagal(f"Manifest produksi melebihi batas {MAKS_ENTRI_MANIFEST} berkas "
                                   f"atau {format_byte(MAKS_BYTE_MANIFEST)}; site ini terlalu besar untuk staging.")
        if baris:
            with open(berkas, "a", encoding="utf-8", newline="\n") as f:
                f.writelines(baris)
        k = _simpan(sesi, job, k, manifest_kursor=h.kursor, manifest_halaman=halaman,
                    manifest_entri=entri, manifest_byte=byte,
                    berkas_dilewati=(k.get("berkas_dilewati") or 0) + h.dilewati)
        if not h.lagi:
            return k
        kursor = h.kursor


def _muat_manifest(dir_kerja: Path) -> dict:
    try:
        return Indeks(dir_kerja / "manifest.jsonl").muat(maks=MAKS_ENTRI_MANIFEST)
    except IndeksTerlaluBesar:
        raise umum.galat_gagal(f"Manifest produksi melebihi batas {MAKS_ENTRI_MANIFEST} berkas.") from None


# ---- penyalinan berkas --------------------------------------------------------


def _ambil_paket(klien, paths: list[str]):
    return _ulangi(lambda: hasil_paket(paths, *klien.staging_file(paths)))


def _ambil_rentang(klien, r: RakitRentang) -> tuple[str, bytes]:
    def ambil():
        path, dari, panjang = r.permintaan()
        meta, bagian = klien.staging_rentang(path, dari, panjang)
        entri = meta.get("berkas") if isinstance(meta, dict) else None
        if not isinstance(entri, list) or len(entri) != 1 or len(bagian) != 1:
            raise BalasanTidakSah("Rentang berkas tidak sesuai permintaan.")
        # terima() hanya mengubah keadaan setelah balasan lolos pemeriksaan,
        # jadi balasan rusak aman diminta ulang.
        return r.terima(entri[0], bagian[0]), bagian[0]

    return _ulangi(ambil)


class Salin:
    """Keadaan penyalinan berkas satu job: tempat menulis, indeks, dan kemajuan.

    Kemajuan di `k`: `byte_selesai` (isi yang sudah ditulis, untuk UI) dan
    `byte_diterima` (semua byte yang diterima, termasuk yang dibuang karena
    berubah di tengah). `byte_diterima` dibatasi `byte_total` manifest +
    toleransi; pemanggil wajib mengisi `byte_total`.
    """

    def __init__(self, sesi, job, staging, klien, files: Path, indeks: Indeks, lokal: dict, k: dict) -> None:
        self.sesi, self.job, self.staging, self.klien = sesi, job, staging, klien
        self.files, self.indeks, self.lokal, self.k = files, indeks, lokal, k
        # Direktori induk berkas yang dihapus; dirapikan bila jadi kosong.
        self.induk_dihapus: set[str] = set()

    def _simpan(self, **perubahan) -> None:
        self.k = _simpan(self.sesi, self.job, self.k, byte_selesai=self.k.get("byte_selesai") or 0,
                         byte_diterima=self.k.get("byte_diterima") or 0, **perubahan)

    def _maju(self, byte: int) -> None:
        self.k["byte_selesai"] = max(0, (self.k.get("byte_selesai") or 0) + byte)
        self._simpan()

    def _terima(self, byte: int) -> None:
        total = self.k.get("byte_total") or 0
        anggaran = anggaran_berkas(total)
        diterima = (self.k.get("byte_diterima") or 0) + byte
        if diterima > anggaran:
            raise umum.galat_gagal(
                f"Data dari produksi ({format_byte(diterima)}) melebihi ukuran manifest ({format_byte(total)}) "
                "jauh di atas toleransi; tarik dihentikan supaya disk server staging tidak dipenuhi.")
        self.k["byte_diterima"] = diterima

    def _catat(self, e: Entri) -> None:
        self.indeks.catat(e)
        self.lokal[e.path] = e

    def hapus(self, path: str) -> None:
        """Hapus berkas di staging yang tidak ada lagi di produksi."""
        try:
            hapus_berkas(self.files, path)
        except PathTidakAman:
            _tambah_peringatan(self.k, f"Berkas staging {path} tidak dapat dihapus (melewati symlink); dilewati.")
            return
        except OSError as exc:
            if _fatal(exc):
                raise
            _tambah_peringatan(self.k, f"Berkas staging {path} tidak dapat dihapus (errno {exc.errno}); dilewati.")
            return
        self.indeks.catat_hapus(path)
        self.lokal.pop(path, None)
        if "/" in path:
            self.induk_dihapus.add(path.rsplit("/", 1)[0])

    def rapikan(self) -> None:
        """Hapus direktori yang menjadi kosong setelah penghapusan (upaya terbaik, lewat aman)."""
        calon = set()
        for d in self.induk_dihapus:
            bagian = d.split("/")
            calon.update("/".join(bagian[:i]) for i in range(1, len(bagian) + 1))
        # Terdalam lebih dulu, supaya induk yang ikut kosong juga terhapus.
        for d in sorted(calon, key=lambda x: (-x.count("/"), x)):
            hapus_direktori_kosong(self.files, d)
        self.induk_dihapus.clear()

    def _tulis(self, path: str, isi: bytes, mtime: int) -> bool:
        try:
            tulis_berkas(self.files, path, isi, mtime)
        except PathTidakAman:
            _tambah_peringatan(self.k, f"Path {path} tidak aman di staging; dilewati.")
            return False
        except OSError as exc:
            if _fatal(exc):
                raise
            _tambah_peringatan(self.k, f"Berkas {path} tidak dapat ditulis (errno {exc.errno}); dilewati.")
            return False
        return True

    def paket(self, berkas: tuple[Entri, ...]) -> None:
        """Berkas kecil per paket. Sisa paket yang tidak lengkap diminta lagi sampai habis."""
        per_path = {e.path: e for e in berkas}
        sisa = [e.path for e in berkas]
        while sisa:
            umum.titik_potongan(self.sesi, self.job, self.staging)
            try:
                h = _ambil_paket(self.klien, sisa)
            except SiteError as exc:
                # Spec §12: galat akhir menyebut berkasnya.
                lain = " dan lainnya" if len(sisa) > 1 else ""
                raise SiteError(exc.error_class, f"{exc.pesan} (berkas {sisa[0]}{lain})") from exc
            self._terima(sum(len(isi) for _, isi, _ in h.isi))
            total = 0
            for path, isi, mtime in h.isi:
                if self._tulis(path, isi, mtime):
                    self._catat(Entri(path, len(isi), mtime, hashlib.sha256(isi).hexdigest()))
                    total += len(isi)
            for path in h.hilang:
                # Dihapus di produksi sejak manifest: dihapus juga di staging.
                self.hapus(path)
            for path in h.gagal_baca:
                _tambah_peringatan(self.k, f"Berkas {path} tidak dapat dibaca di produksi; dilewati.")
            self._maju(total)
            for path in h.terlalu_besar:
                # Tumbuh sejak manifest melebihi satu paket: lewat rentang.
                self.besar(per_path[path])
            sisa = h.sisa

    def besar(self, e: Entri) -> None:
        """Satu berkas lewat rentang, ditulis bertahap ke berkas sementara (tidak ditampung di memori)."""
        sisa_permintaan = None
        for _ in range(MAKS_ULANG_RENTANG):
            r = RakitRentang(e, UKURAN_PAKET)
            if sisa_permintaan is None:
                sisa_permintaan = math.ceil(r.total_maks() / KEMAJUAN_MINIMUM) + TAMBAHAN_PERMINTAAN
            ditulis = 0
            try:
                with tulis_bertahap(self.files, e.path) as w:
                    while True:
                        if sisa_permintaan <= 0:
                            # Balasan meneteskan terlalu sedikit byte: tulisan
                            # sementara dibuang saat keluar dari blok.
                            self._maju(-ditulis)
                            _tambah_peringatan(self.k, f"Berkas {e.path} dikirim produksi terlalu lambat "
                                                       "(terlalu banyak potongan kecil); dilewati.")
                            self._simpan()
                            return
                        sisa_permintaan -= 1
                        umum.titik_potongan(self.sesi, self.job, self.staging)
                        try:
                            hasil, isi = _ambil_rentang(self.klien, r)
                        except SiteError as exc:
                            raise SiteError(exc.error_class, f"{exc.pesan} (berkas {e.path})") from exc
                        if hasil == HILANG:
                            self._maju(-ditulis)
                            self.hapus(e.path)
                            return
                        self._terima(len(isi))
                        if hasil == BERUBAH:
                            break
                        w.tulis(isi)
                        ditulis += len(isi)
                        self._maju(len(isi))
                        if hasil == SELESAI:
                            if r.verifikasi() is False:
                                # Isi berubah di tengah dengan ukuran dan mtime sama.
                                break
                            w.selesai(r.mtime)
                            self._catat(Entri(e.path, r.total, r.mtime, r.hash()))
                            return
            except PathTidakAman:
                self._maju(-ditulis)
                _tambah_peringatan(self.k, f"Path {e.path} tidak aman di staging; dilewati.")
                self._simpan()
                return
            except OSError as exc:
                if _fatal(exc):
                    raise
                self._maju(-ditulis)
                _tambah_peringatan(self.k, f"Berkas {e.path} tidak dapat ditulis (errno {exc.errno}); dilewati.")
                self._simpan()
                return
            # Berubah di tengah: tulisan sementara sudah dibuang; mulai dari awal.
            self._maju(-ditulis)
        _tambah_peringatan(self.k, f"Berkas {e.path} terus berubah selama disalin; dilewati.")
        self._simpan()


def _sinkron_berkas(sesi, job, staging, klien, akar: Path, produksi: dict, k: dict) -> dict:
    indeks = Indeks(akar / "indeks.jsonl")
    lokal = indeks.muat()
    beda = selisih(produksi, lokal)
    salin = Salin(sesi, job, staging, klien, akar / "files", indeks, lokal, k)
    for i, path in enumerate(beda.hapus):
        if i % HAPUS_PER_TITIK == 0:
            umum.titik_potongan(sesi, job, staging)
        salin.hapus(path)
    for pot in bagi_potongan(beda.diambil, ukuran_paket=UKURAN_PAKET):
        if pot.jenis == "rentang":
            # Satu berkas besar menjadi beberapa potongan rencana; diambil
            # sekali, mulai dari potongan pertamanya (lihat bagi_potongan).
            if pot.dari == 0:
                salin.besar(pot.berkas[0])
        else:
            salin.paket(pot.berkas)
    salin.rapikan()
    return _simpan(sesi, job, salin.k, tahap="tanda_air")


# ---- database -----------------------------------------------------------------


def _ambil_tabel(klien, nama: str, kursor: str | None):
    return _ulangi(lambda: potongan_tabel(nama, kursor, *klien.staging_tabel(nama, kursor)))


def ekspor_db(sesi, job, staging, klien, dir_sql: Path, info: dict, k: dict, tahap_berikut: str,
              periksa_awal=None) -> dict:
    """Ekspor tabel per potongan ke dir_sql/db/ (dipakai tarik dan snapshot dorong).

    `periksa_awal(nama, sql)` (opsional) dipanggil untuk potongan pertama
    setiap tabel -- yang memuat DROP + CREATE TABLE -- sebelum ditulis, dan
    boleh melempar untuk menghentikan ekspor sedini mungkin (R8 dorong).
    """
    db_dir = dir_sql / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    (dir_sql / "prelude.sql").write_bytes(
        f"SET NAMES {info['charset']};\nSET FOREIGN_KEY_CHECKS=0;\nSET UNIQUE_CHECKS=0;\n"
        f"SET SQL_MODE='NO_AUTO_VALUE_ON_ZERO';\n".encode("ascii"))
    anggaran = anggaran_db(info["ukuran_db"])
    for idx, t in enumerate(info["tabel"]):
        nama = t["nama"]
        if nama in (k.get("tabel_selesai") or []):
            continue
        kursor = (k.get("tabel") or {}).get(nama) or None
        seq = (k.get("tabel_seq") or {}).get(nama, 0)
        # Potongan yang tertulis tetapi belum tercatat (terputus setelah
        # menulis) dibuang supaya INSERT tidak tergandakan.
        for sisa in db_dir.glob(f"{idx:04d}-*.sql"):
            if int(sisa.stem.split("-")[1]) >= seq:
                sisa.unlink()
        for _ in range(MAKS_POTONGAN_TABEL):
            umum.titik_potongan(sesi, job, staging)
            try:
                pot = _ambil_tabel(klien, nama, kursor)
            except SiteError as exc:
                raise SiteError(exc.error_class, f"{exc.pesan} (tabel {nama})") from exc
            sql = pot.sql
            # Dihitung lintas potongan dan tercatat bersama kursornya, jadi
            # tetap berlaku setelah job dilanjutkan.
            diterima = (k.get("db_diterima") or 0) + len(sql)
            if diterima > anggaran:
                raise umum.galat_gagal(
                    f"SQL dari produksi ({format_byte(diterima)}) melebihi ukuran database yang dilaporkan "
                    f"({format_byte(info['ukuran_db'])}) jauh di atas toleransi; tarik dihentikan.")
            alasan = periksa_sql(sql)
            if alasan is not None:
                raise umum.galat_gagal(f"Potongan SQL tabel {nama} dari produksi ditolak: {alasan}.")
            if kursor is None:
                sql = sesuaikan_mariadb(sql)
                if periksa_awal is not None:
                    periksa_awal(nama, sql)
                if pot.mode == "offset":
                    _tambah_peringatan(k, f"Tabel {nama} tanpa primary key disalin dengan LIMIT/OFFSET; "
                                          "baris yang berubah selama tarik bisa terlewat atau ganda.")
            (db_dir / f"{idx:04d}-{seq:06d}.sql").write_bytes(sql)
            seq += 1
            k = _simpan(
                sesi, job, k,
                tabel={**(k.get("tabel") or {}), nama: pot.kursor or ""},
                tabel_seq={**(k.get("tabel_seq") or {}), nama: seq},
                tabel_selesai=list(k.get("tabel_selesai") or []) + ([nama] if pot.selesai else []),
                db_diterima=diterima,
            )
            if pot.selesai:
                break
            kursor = pot.kursor
        else:
            raise umum.galat_gagal(f"Tabel {nama} terlalu besar untuk disalin.")
    return _simpan(sesi, job, k, tahap=tahap_berikut)


# ---- runtime ------------------------------------------------------------------


def _siapkan_runtime(sesi, job, staging: Staging, site, pb, akar: Path, info: dict) -> None:
    s = get_settings()
    try:
        tulis_atomik(akar / "files", MU_PLUGIN, isi_mu_plugin_staging(staging.nama).encode("utf-8"))
    except PathTidakAman:
        raise umum.galat_gagal("Folder mu-plugins staging tidak aman (berupa symlink); "
                               "jalankan tarik lagi untuk memulihkannya.") from None
    tulis_akses_router(s.jalur_staging, staging.nama, staging.sandi_hash,
                       dekripsi_secret(staging.rahasia_router_terenkripsi))
    url = umum.url_staging(staging)
    # buat dan search-replace bisa berjalan puluhan menit: detak dari utas
    # latar supaya reaper tidak merebut job (putusan F7).
    with umum.detak_latar(sesi, job):
        pb.router_muat()
        pb.buat(staging.nama, info["versi_php"], site.id)
        for asal in urls_produksi(info):
            pb.wpcli(staging.nama, "search-replace", asal, url)
        pb.wpcli(staging.nama, "option", "update", "blog_public", "0")
        pb.wpcli(staging.nama, "cache", "flush")


def _bangun_ulang_indeks(sesi, job, akar: Path, peringatan: list[str]) -> None:
    """Putusan F3: indeks dibangun ulang dari isi files/ di awal setiap tarik baru.

    Suntingan di staging (dan plugin yang diperbarui uji update) lalu
    terlihat sebagai selisih terhadap manifest dan dikembalikan ke isi
    produksi (spec §6.2 langkah 2). Hash lama dipakai ulang untuk berkas
    yang ukuran dan mtime-nya sama, jadi pemindaian ini murah.
    """
    indeks = Indeks(akar / "indeks.jsonl")
    tautan: list[str] = []
    with umum.detak_latar(sesi, job):
        lokal = pindai_lokal(akar / "files", indeks.muat(), peringatan, tautan)
    # Symlink di files/ (dibuat kode di container staging) dihapus tautannya
    # saja, tanpa diikuti; path itu lalu tidak ada di indeks, sehingga isi
    # produksi ditulis ulang di tempatnya.
    for p in tautan:
        try:
            hapus_tautan(akar / "files", p)
        except (PathTidakAman, OSError):
            peringatan.append(bersih_teks(f"Symlink staging {p} tidak dapat dihapus.", 300))
    # Berkas milik staging sendiri tidak pernah menjadi bagian salinan.
    indeks.padatkan({p: e for p, e in lokal.items() if p not in DILINDUNGI_STAGING})


# ---- job ----------------------------------------------------------------------


def tarik(sesi, job, site, staging: Staging, klien, pb, akhir_status: bool = True) -> dict:
    if not punya_fitur(site, STAGING):
        raise umum.galat_ditolak("Connector site ini belum mengizinkan staging. Aktifkan 'Izinkan staging' "
                                 "di Pengaturan -> WP Manager (connector 3.0).")
    if not staging.sandi_hash or not staging.rahasia_router_terenkripsi:
        raise umum.galat_ditolak("Akses preview staging belum dibuat; buat ulang kata sandi preview.")
    akar = umum.dir_site(site.id)
    tarik_dir = akar / "tarik"
    pertama = staging.ditarik_pada is None
    k = umum.kemajuan(job)
    # "tahap" (bukan sekadar kemajuan kosong): uji update memakai kemajuan
    # yang sama dan sudah menyimpan tahap_uji sebelum memanggil tarik().
    baru = "tahap" not in k
    for d in (akar / "files", akar / "log", akar / "ekspor"):
        d.mkdir(parents=True, exist_ok=True)

    try:
        status = pb.status()
        if not staging.aktif:
            pesan = cek_ram(status) or cek_maks_aktif(jumlah_aktif(sesi, staging), get_settings().staging_maks_aktif)
            if pesan:
                raise umum.galat_ditolak(pesan)

        if baru:
            shutil.rmtree(tarik_dir, ignore_errors=True)
            peringatan: list[str] = []
            _bangun_ulang_indeks(sesi, job, akar, peringatan)
            k = umum.simpan_kemajuan(sesi, job, tahap="manifest", mulai=umum.sekarang().isoformat(),
                                     byte_selesai=0, byte_diterima=0, byte_total=0,
                                     peringatan=peringatan[:MAKS_PERINGATAN], berkas_dilewati=0)
        tarik_dir.mkdir(parents=True, exist_ok=True)

        if k["tahap"] == "manifest":
            k = ambil_manifest(sesi, job, staging, klien, tarik_dir, k)
            produksi = _muat_manifest(tarik_dir)
            lokal = Indeks(akar / "indeks.jsonl").muat()
            beda = selisih(produksi, lokal)
            pesan = cek_disk(status, kebutuhan_disk(beda.byte, k["info"]["ukuran_db"]))
            if pesan:
                raise umum.galat_ditolak(pesan)
            info = k["info"]
            if k.get("berkas_dilewati"):
                _tambah_peringatan(k, f"{k['berkas_dilewati']} berkas dilewati (nama bukan UTF-8, symlink, "
                                      "tidak terbaca, atau path tidak sah).")
            if info["tabel_dilewati"]:
                _tambah_peringatan(k, f"{info['tabel_dilewati']} tabel produksi dilewati (view rusak, "
                                      "nama tidak sah, atau lebih dari 2000 tabel).")
            if info["php_peringatan"]:
                _tambah_peringatan(k, f"PHP produksi {info['php'] or 'tidak diketahui'} tidak tersedia; "
                                      f"staging memakai PHP {info['versi_php']}.")
            k = _simpan(sesi, job, k, tahap="berkas", byte_total=beda.byte, byte_selesai=0, byte_diterima=0)

        info = k["info"]
        if k["tahap"] == "berkas":
            produksi = _muat_manifest(tarik_dir)
            k = _sinkron_berkas(sesi, job, staging, klien, akar, produksi, k)

        if k["tahap"] == "tanda_air":
            umum.titik_potongan(sesi, job, staging)
            # Diambil sebelum ekspor database (Koreksi #20): data yang masuk
            # selama ekspor ikut terdeteksi sebagai "baru" saat dorong.
            ta = urai_tanda_air(umum.ulangi(klien.staging_tanda_air))
            if ta is None:
                raise umum.galat_gagal("Tanda air produksi tidak dapat dibaca.")
            k = _simpan(sesi, job, k, tahap="db", tanda_air=ta, tabel={}, tabel_seq={}, tabel_selesai=[],
                        db_diterima=0)

        if k["tahap"] == "db":
            k = ekspor_db(sesi, job, staging, klien, tarik_dir, info, k, "impor")

        if k["tahap"] == "impor":
            umum.titik_potongan(sesi, job, staging)
            # Impor yang gagal/terputus meninggalkan database setengah terisi
            # (docker exec tidak meneruskan TERM; mariadb melihat EOF dan
            # menyimpan yang sudah masuk). Karena itu tahap ini selalu diulang
            # utuh, tidak pernah dilanjutkan di tengah: db-buat idempoten, dan
            # db-impor di skrip pembantu membuang lalu membuat ulang database
            # sebelum mengimpor.
            with umum.detak_latar(sesi, job):
                pb.db_buat(staging.nama, site.id, info["prefix"])
                pb.db_impor(staging.nama, [tarik_dir / "prelude.sql", *sorted((tarik_dir / "db").glob("*.sql"))])
            k = _simpan(sesi, job, k, tahap="penyiapan")

        if k["tahap"] == "penyiapan":
            umum.titik_potongan(sesi, job, staging)
            _siapkan_runtime(sesi, job, staging, site, pb, akar, info)
            k = _simpan(sesi, job, k, tahap="sertifikat")
    except umum.Dibatalkan:
        shutil.rmtree(tarik_dir, ignore_errors=True)
        raise
    except PathTidakAman:
        # Container staging menukar direktori di files/ dengan symlink di sela
        # pemeriksaan; pesan tetap, tanpa path VPS. Tarik berikutnya menghapus
        # symlink itu (_bangun_ulang_indeks).
        raise umum.galat_gagal("Struktur folder staging tidak aman (ada symlink yang ditukar selama tarik); "
                               "jalankan tarik lagi.") from None

    sertifikat_ok = True
    try:
        with umum.detak_latar(sesi, job):
            pb.sertifikat(staging.nama)
    except GalatPembantu as exc:
        # Staging tetap bisa dibuka lewat SSO dashboard (spec §12); cron
        # renew-staging-certs mencoba lagi. exc.pesan adalah teks tetap.
        sertifikat_ok = False
        _tambah_peringatan(k, f"Sertifikat belum terbit: {exc.pesan}")

    indeks = Indeks(akar / "indeks.jsonl")
    lokal = indeks.muat()
    indeks.padatkan(lokal)
    try:
        # log/ di-bind mount ke container: dihapus hanya lewat `aman`.
        hapus_berkas(akar, "log/diubah")
    except (PathTidakAman, OSError):
        pass
    shutil.rmtree(tarik_dir, ignore_errors=True)

    st = sesi.get(Staging, staging.id, populate_existing=True)
    sekarang = umum.sekarang()
    if akhir_status:
        st.status = StatusStaging.siap
    st.aktif = True
    st.versi_php = info["versi_php"]
    st.ditarik_pada = sekarang
    st.diubah_pada = None
    st.tanda_air = k["tanda_air"]
    st.ukuran_file = min(sum(e.ukuran for e in lokal.values()), 2**62)
    st.ukuran_db = min(info["ukuran_db"], 2**62)
    if sertifikat_ok:
        st.sertifikat_pada = sekarang
    hasil = {
        "byte_disalin": k.get("byte_selesai", 0),
        "ukuran_file": st.ukuran_file,
        "ukuran_db": st.ukuran_db,
        "versi_php": info["versi_php"],
        "peringatan": list(k.get("peringatan") or []),
    }
    umum.catat_aktivitas(sesi, site.id, job, "Staging dibuat" if pertama else "Staging disegarkan", {
        **hasil, "ukuran_file_teks": format_byte(st.ukuran_file), "peringatan": hasil["peringatan"][:10]})
    sesi.commit()
    return hasil


def bersihkan_bila_final(sesi, site_id, status_kerja: StatusStaging = StatusStaging.menyalin) -> None:
    """Hapus area kerja tarik/ bila kegagalan job ini final (M1).

    Pembungkus sudah menandai staging: masih `status_kerja` (`menyalin`,
    atau `berjalan_uji` untuk uji update yang diawali tarik) berarti job akan
    dilanjutkan (atau klaimnya direbut worker lain) dan area kerjanya
    dibutuhkan; status lain berarti tidak ada lagi yang melanjutkannya.
    """
    try:
        sesi.rollback()
        status = sesi.scalar(select(Staging.status).where(Staging.site_id == site_id))
    except Exception:  # noqa: BLE001 -- galat asli yang dilempar ulang lebih penting
        return
    if status is not None and status != status_kerja:
        shutil.rmtree(umum.dir_site(site_id) / "tarik", ignore_errors=True)


def tangani_staging_tarik(sesi, job, klien) -> dict:
    def inti(sesi, job, site, staging):
        return tarik(sesi, job, site, staging, klien, umum.buat_pembantu())

    site_id = job.site_id
    try:
        return umum.jalankan_staging(sesi, job, inti, StatusStaging.menyalin, "Tarik staging")
    except umum.KlaimHilang:
        raise
    except Exception:
        bersihkan_bila_final(sesi, site_id)
        raise
