"""Job staging_dorong (spec §6.3) dan jalur bersama untuk staging_kembalikan (§8.3).

Dorong menulis ke site PRODUKSI klien, jadi setiap langkahnya bisa dilanjutkan
dan aman diulang. Keadaannya ada di `job.payload["kemajuan"]`, di-commit
setiap kali berpindah, dan job yang diulang melanjutkan tepat dari sana.

Mesin keadaan luar (`tahap_dorong`)::

    tanda_air -> manifest -> rencana -> snapshot_db -> snapshot_berkas
      -> snapshot_catat -> unggah -> terapkan -> cek -> (selesai)
    rencana -> tanpa_perubahan (tidak ada yang didorong)

Mesin keadaan terapkan (`langkah_terapkan`, dipakai juga Kembalikan)::

    siapkan -> impor* -> cek_ulang -> tukar -> selesai -> beres
                                        \\-> pulihkan -> dipulihkan (gagal)
    (* hanya bila ada SQL)

Aturan yang dijaga:
- `langkah_terapkan = "tukar"` di-commit SEBELUM permintaan tukar pertama
  (tulis-lebih-dulu). Sejak titik itu produksi dianggap mungkin tersentuh:
  batal tidak berlaku lagi, snapshot tidak pernah dibuang, dan kegagalan
  final dicatat di `dorong_gagal_pada`.
- `pulihkan` HANYA dijalankan sesudah connector menjawab pasti bahwa tukar
  tidak tuntas (wpmgr_staging_tukar dengan data.pemulihan, atau 409 urutan
  saat tukar dikirim ulang). Hasil yang tidak diketahui (koneksi putus,
  tenggat, 5xx tanpa kode) diselesaikan dengan MENGIRIM ULANG tukar: langkah
  connector idempoten (putusan F4) dan tukar yang sudah sukses dibalas hasil
  tersimpannya, jadi pulihkan tidak pernah membalik tukar yang berhasil.
- Batal: sebelum tukar, batal membersihkan area dorong di produksi dan
  selesai. Sesudah tukar dikirim, batal diabaikan sampai dorong dituntaskan
  (`selesai`, atau `pulihkan` bila tukar gagal).
- Gagal final (putusan F9a): area dorong di produksi dibersihkan upaya-
  terbaik dengan tenggat pendek. `bersihkan` connector sendiri menolak
  (409) dorongan yang sedang/sudah ditukar (STATUS_BOLEH_BERSIHKAN), jadi
  pemanggilan ini tidak pernah bisa membatalkan tukar yang sudah terjadi.
- Dorongan lama yang belum dibersihkan (dicatat di kemajuan job lama)
  dituntaskan lebih dulu sebelum dorongan baru dimulai: dibersihkan bila
  terminal, diselesaikan bila sudah ditukar, dipulihkan bila setengah jalan.
"""

import hashlib
import json
import logging
import os
import secrets
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from wpmgr.config import get_settings
from wpmgr.errors import (
    BAD_RESPONSE,
    BERKAS_HILANG,
    STAGING_DITOLAK,
    STAGING_GAGAL,
    TRANSIENT,
    UNKNOWN,
    SiteError,
)
from wpmgr.models import (
    Job,
    JobStatus,
    JobType,
    Staging,
    StagingSnapshot,
    StatusStaging,
)
from wpmgr.site_client import MelebihiBatas, TanpaHasil, TenggatHabis, minta_bertenggat
from wpmgr.staging import umum
from wpmgr.staging.aman import (
    POLA_ID_DORONG,
    PathTidakAman,
    adalah_tautan,
    bersih_teks,
    buka_baca,
    hapus_berkas,
    jalur_di_dalam,
)
from wpmgr.staging.indeks import Indeks, pindai_lokal
from wpmgr.staging.paket import susun
from wpmgr.staging.rencana import (
    Entri,
    bagi_potongan,
    bandingkan_tanda_air,
    entri_dari,
    format_byte,
    rencana_dorong,
    selisih,
    urai_tanda_air,
)
from wpmgr.staging.sql_impor import periksa_berkas_terapkan, periksa_terapkan
from wpmgr.staging.tarik import (
    MAKS_PERINGATAN,
    Salin,
    _muat_manifest,
    ambil_manifest,
    anggaran_db,
    ekspor_db,
    urai_tabel,
)

log = logging.getLogger("wpmgr.staging.dorong")

MODE = ("hanya_kode", "timpa_penuh")
LABEL_MODE = {"hanya_kode": "hanya kode", "timpa_penuh": "timpa penuh"}
UKURAN_UNGGAH = 4 * 1024 * 1024
MAKS_LANGKAH = 2000
BATCH_SNAPSHOT = 5000
# Tukar yang hasilnya tidak diketahui dikirim ulang sekian kali dalam satu
# putaran job sebelum job diserahkan ke percobaan ulang worker (jeda menit).
MAKS_RAGU_TUKAR = 5
# Pulihkan yang dijawab "belum tuntas" (status memulihkan) diulang sekian kali.
MAKS_ULANG_PULIHKAN = 5
# Pembersihan upaya-terbaik saat job gagal final (putusan F9a).
TENGGAT_BERSIHKAN_AKHIR = 20.0
MAKS_PUTARAN_BERSIHKAN = 100
# Cek halaman utama (putusan F11): tenggat total dan batas byte.
TIMEOUT_HALAMAN = 20.0
TENGGAT_HALAMAN = 30.0
BATAS_HALAMAN = 1024 * 1024
# Job dorong/kembalikan lama yang diperiksa untuk dorongan yang tertinggal.
MAKS_JOB_LAMA = 20
SALIN_BLOK = 1 << 20

LANGKAH_SESUDAH_TUKAR = frozenset({"tukar", "pulihkan", "dipulihkan", "selesai", "beres"})
# Galat tukar yang membuktikan tukar TIDAK tuntas di produksi dan perlu
# pulihkan: connector sudah memulihkan/sedang memulihkan (gagal_tukar), atau
# tukar dikirim ulang dan connector menjawab dorongan ini tidak lagi menunggu
# tukar (409 urutan: hasil tukar yang sukses selalu dibalas ulang, putusan F4).
KODE_TUKAR_GAGAL = frozenset({"wpmgr_staging_tukar", "wpmgr_staging_urutan"})
# Penolakan PRA-PEMERIKSAAN tukar (prapemeriksaan_tukar()/rencana()/token di
# connector): dijawab SEBELUM status berubah menjadi 'menukar', jadi produksi
# belum tersentuh dan dorongan tetap 'siap'/'terimpor'. Tidak dipulihkan;
# mengirim ulang tukar nanti aman karena status itu masih diterima tukar().
KODE_TUKAR_PRA = frozenset({
    "wpmgr_staging_maintenance", "wpmgr_staging_tabel_lama", "wpmgr_staging_rencana",
    "wpmgr_staging_permintaan",
})
KODE_SIBUK = "wpmgr_staging_sibuk"
# Jeda antarputaran bersihkan yang menjawab lagi:true, dan tenggat totalnya.
TENGGAT_BERSIHKAN = 120.0

PESAN_MODE = "Mode dorong tidak dikenal."
PESAN_BELUM_TARIK = "Tarik staging dulu sebelum mendorong."
PESAN_DIJEDA = "Staging sedang dijeda; jalankan staging dulu."
PESAN_DIREBUT = "Dorongan ini direbut dorongan lain di produksi dan tidak dapat dilanjutkan; jalankan dorong lagi."
PESAN_URUTAN = ("Dorongan di produksi tidak lagi menunggu langkah ini (sudah dibatalkan, dipulihkan, atau "
                "dibersihkan); jalankan dorong lagi.")
# 409 urutan saat siapkan juga berasal dari ekstrak(): potongan rentang
# berkas besar tidak bersambung.
PESAN_URUTAN_SIAPKAN = ("Area dorong di produksi tidak dapat dirakit (potongan berkas besar tidak berurutan, atau "
                        "dorongan sudah dibatalkan); jalankan dorong lagi.")
PESAN_MAINTENANCE_ULANG = ("Site produksi sedang dalam mode pemeliharaan lain (mis. update WordPress); "
                           "penukaran ditunda, produksi belum diubah.")
PESAN_TOLAK_PRA = {
    "wpmgr_staging_maintenance": ("Site produksi sedang dalam mode pemeliharaan lain (mis. update WordPress); "
                                  "dorong tidak diterapkan dan produksi tidak diubah. Coba lagi nanti."),
    "wpmgr_staging_tabel_lama": ("Tabel produksi lama dari dorongan sebelumnya masih ada di produksi; dorong "
                                 "tidak diterapkan dan produksi tidak diubah. Coba lagi nanti."),
    "wpmgr_staging_rencana": ("Rencana dorong ditolak connector saat penukaran; dorong tidak diterapkan dan "
                              "produksi tidak diubah. Jalankan dorong lagi."),
    "wpmgr_staging_permintaan": ("Permintaan penukaran ditolak connector; dorong tidak diterapkan dan produksi "
                                 "tidak diubah. Jalankan dorong lagi."),
}
PESAN_BENTROK = ("Berkas staging berubah selama dorong (isi potongan berbeda dari unggahan sebelumnya); "
                 "jalankan dorong lagi.")
PESAN_DITAHAN = ("Tabel dari pemulihan dorongan sebelumnya masih ditahan di produksi (paling lama 24 jam); "
                 "dorong ditahan, coba lagi nanti.")
PESAN_VERIFIKASI = ("Berkas yang diunggah tidak cocok dengan rencana di produksi (berkas staging berubah "
                    "selama dorong?); jalankan dorong lagi.")
PESAN_RENCANA = "Rencana dorong ditolak connector (rusak atau tidak cocok); jalankan dorong lagi."
PESAN_DISK = "Disk di server produksi hampir penuh; unggahan dorong ditolak."
PESAN_TERLALU_BESAR = "Potongan unggahan ditolak produksi karena terlalu besar; periksa post_max_size."
PESAN_UNGGAH_KECIL = ("post_max_size di produksi terlalu kecil untuk mengunggah potongan dorong (< 256 KB); "
                      "naikkan post_max_size lalu coba lagi.")
PESAN_POST_TIDAK_PASTI = "jumlah post yang diubah sejak tarik tidak dapat dipastikan"
PESAN_KURANG_BERULANG = "Potongan unggahan berulang kali hilang dari area dorong di produksi; jalankan dorong lagi."
# Kegagalan sesudah tukar dikirim: produksi mungkin sudah berubah. Pesan
# *_RAGU dipakai selama job masih akan diulang (pembungkus menambahkan
# "Terputus, dilanjutkan otomatis: " di depannya); PESAN_AKHIR menggantikannya
# bila kegagalan itu final.
PESAN_TUKAR_RAGU = "Hasil penukaran di produksi belum dapat dipastikan (connector tidak menjawab)."
PESAN_PULIH_RAGU = "Penukaran di produksi gagal dan pemulihannya belum tuntas."
PESAN_SELESAI_RAGU = "Dorongan sudah diterapkan di produksi, tetapi langkah penyelesaiannya belum terkonfirmasi."
PESAN_AKHIR = {
    "tukar": ("Hasil penukaran di produksi tidak dapat dipastikan. Periksa site; bila rusak, gunakan Kembalikan. "
              "Dorongan berikutnya menuntaskan dorongan ini lebih dulu."),
    "pulihkan": ("Penukaran di produksi gagal dan pemulihannya belum terkonfirmasi. Connector melanjutkan "
                 "pemulihan sendiri paling cepat 15 menit setelah aktivitas terakhir; periksa site, lalu gunakan "
                 "Kembalikan bila perlu."),
    "selesai": ("Dorongan sudah diterapkan di produksi, tetapi penyelesaiannya belum terkonfirmasi. Periksa "
                "site; dorongan berikutnya menuntaskannya lebih dulu."),
}
PESAN_LAMA_GAGAL = ("Dorongan sebelumnya masih setengah diterapkan di produksi dan belum dapat dituntaskan; "
                    "periksa site lalu coba lagi.")
PESAN_LAMA_SEMENTARA = "Dorongan sebelumnya di produksi belum dapat diperiksa atau dituntaskan (connector tidak menjawab)."
PESAN_LAMA_AKHIR = ("Dorongan sebelumnya di produksi belum dapat diperiksa atau dituntaskan (connector tidak "
                    "menjawab); dorong baru tidak dimulai, produksi dan staging tidak diubah. Coba lagi nanti.")


class PotonganKurang(Exception):
    """Connector melaporkan potongan (atau seluruh area dorong) hilang: unggah ulang."""


# ---- sumber dan rencana unggah -----------------------------------------------


@dataclass
class SumberDorong:
    ganti: list[Entri]
    akar_berkas: Path
    hapus: list[str]
    sql: list[Path]
    charset: str


@dataclass(frozen=True)
class Unggahan:
    jenis: str
    berkas: tuple = ()
    dari: int = 0
    panjang: int = 0


def rencana_connector(sumber: SumberDorong) -> bytes:
    """Rencana yang diverifikasi connector sebelum menulis apa pun (Task 8)."""
    return json.dumps({
        "versi": 1,
        "berkas": [{"path": e.path, "ukuran": e.ukuran, "sha256": e.hash, "mtime": e.mtime} for e in sumber.ganti],
        "hapus": list(sumber.hapus),
        "sql": bool(sumber.sql),
        "charset": sumber.charset,
    }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def rencana_unggah(sumber: SumberDorong, rencana_json: bytes, ukuran: int) -> list[Unggahan]:
    """Daftar potongan yang deterministik: melanjutkan cukup dengan nomor potongan.

    Setiap potongan paling besar `ukuran` byte isi, jadi yang ditampung di
    memori per permintaan tidak pernah lebih dari itu.
    """
    daftar = [Unggahan("berkas" if p.jenis == "paket" else "rentang", p.berkas, p.dari, p.panjang)
              for p in bagi_potongan(sumber.ganti, ukuran_paket=ukuran)]
    total_sql = sum(p.stat().st_size for p in sumber.sql)
    daftar += [Unggahan("sql", (), d, min(ukuran, total_sql - d)) for d in range(0, total_sql, ukuran)]
    daftar += [Unggahan("rencana", (), d, min(ukuran, len(rencana_json) - d))
               for d in range(0, len(rencana_json), ukuran)]
    return daftar


def baca_gabungan(berkas: list[Path], dari: int, panjang: int) -> bytes:
    """`panjang` byte mulai `dari` atas gabungan berkas berurutan.

    Dibuka lewat `aman.buka_baca` (tanpa mengikuti symlink, hanya berkas
    biasa): berkas SQL dorong berasal dari ekspor staging, dan snapshot
    Kembalikan bisa disentuh pihak lain di disk.
    """
    hasil = bytearray()
    posisi = 0
    for b in berkas:
        with buka_baca(b.parent, b.name) as f:
            n = os.fstat(f.fileno()).st_size
            if dari < posisi + n and len(hasil) < panjang:
                f.seek(max(0, dari - posisi))
                hasil.extend(f.read(panjang - len(hasil)))
        posisi += n
        if len(hasil) >= panjang:
            break
    return bytes(hasil)


def _galat_berkas_berubah(path: str) -> SiteError:
    return umum.galat_gagal(f"Berkas staging {bersih_teks(path, 300)} berubah atau tidak aman sejak rencana dorong "
                            "dibuat; jalankan dorong lagi.")


def isi_unggahan(sumber: SumberDorong, u: Unggahan, rencana_json: bytes) -> tuple[list[dict], list[bytes]]:
    """Meta dan isi satu potongan.

    files/ staging di-bind mount ke container yang menjalankan kode salinan
    produksi: dibaca hanya lewat `aman` (tanpa symlink), dan isi berkas kecil
    dicocokkan dengan ukuran dan hash rencana. Berkas besar diverifikasi
    connector di langkah siapkan (hash seluruh berkas).
    """
    if u.jenis == "berkas":
        isi = []
        for e in u.berkas:
            try:
                with buka_baca(sumber.akar_berkas, e.path) as f:
                    data = f.read(e.ukuran + 1)
            except (PathTidakAman, OSError):
                raise _galat_berkas_berubah(e.path) from None
            if len(data) != e.ukuran or hashlib.sha256(data).hexdigest() != e.hash:
                raise _galat_berkas_berubah(e.path)
            isi.append(data)
        return [{"path": e.path, "mtime": e.mtime} for e in u.berkas], isi
    if u.jenis == "rentang":
        e = u.berkas[0]
        try:
            with buka_baca(sumber.akar_berkas, e.path) as f:
                f.seek(u.dari)
                data = f.read(u.panjang)
        except (PathTidakAman, OSError):
            raise _galat_berkas_berubah(e.path) from None
        if len(data) != u.panjang:
            raise _galat_berkas_berubah(e.path)
        return [{"path": e.path, "dari": u.dari}], [data]
    if u.jenis == "sql":
        return [{"path": "sql"}], [baca_gabungan(sumber.sql, u.dari, u.panjang)]
    return [{"path": "rencana"}], [rencana_json[u.dari:u.dari + u.panjang]]


def _galat_unggah(exc: SiteError) -> SiteError:
    pesan = {
        "wpmgr_staging_nomor_bentrok": PESAN_BENTROK, "wpmgr_staging_direbut": PESAN_DIREBUT,
        "wpmgr_staging_urutan": PESAN_URUTAN, "wpmgr_staging_disk_penuh": PESAN_DISK,
        "wpmgr_staging_terlalu_besar": PESAN_TERLALU_BESAR,
    }.get(exc.kode)
    return umum.galat_gagal(pesan) if pesan else exc


def unggah_semua(sesi, job, staging, klien, sumber: SumberDorong, dorong_id: str, ukuran: int, k: dict) -> dict:
    """Unggah semua potongan mulai `unggah_nomor`; kemajuan di-commit per potongan.

    `unggah_mulai` di-commit SEBELUM potongan pertama dikirim: sejak itu
    connector mungkin menyimpan sesuatu (dan memegang kunci dorong), jadi
    kegagalan final wajib membersihkannya (putusan F9a).
    """
    rencana_json = rencana_connector(sumber)
    daftar = rencana_unggah(sumber, rencana_json, ukuran)
    if not k.get("unggah_mulai"):
        k = umum.simpan_kemajuan(sesi, job, unggah_mulai=True, jumlah_potongan=len(daftar),
                                 sha256_rencana=hashlib.sha256(rencana_json).hexdigest(),
                                 ada_sql=bool(sumber.sql), byte_selesai=0,
                                 byte_total=sum(u.panjang if u.jenis != "berkas" else sum(e.ukuran for e in u.berkas)
                                                for u in daftar))
    for nomor in range(k.get("unggah_nomor", 0), len(daftar)):
        umum.titik_potongan(sesi, job, staging)
        meta, isi = isi_unggahan(sumber, daftar[nomor], rencana_json)
        data = susun({"dorong_id": dorong_id, "nomor": nomor, "jenis": daftar[nomor].jenis, "berkas": meta}, isi)

        def kirim(data=data, nomor=nomor):
            h = klien.staging_unggah(data)
            if h.get("ok") is not True or h.get("nomor") != nomor:
                raise SiteError(BAD_RESPONSE, "Balasan unggah tidak sesuai potongan yang dikirim.")
            return h

        try:
            umum.ulangi(kirim)
        except SiteError as exc:
            raise _galat_unggah(exc) from None
        k = umum.simpan_kemajuan(sesi, job, unggah_nomor=nomor + 1, jumlah_potongan=len(daftar),
                                 byte_selesai=k.get("byte_selesai", 0) + sum(len(x) for x in isi))
    return k


# ---- terapkan -----------------------------------------------------------------


def _pindah(sesi, job, langkah: str, **lain) -> str:
    umum.simpan_kemajuan(sesi, job, langkah_terapkan=langkah, **lain)
    return langkah


class GalatLamaSementara(SiteError):
    """Dorongan lama belum bisa diperiksa/dituntaskan karena gangguan: diulang, belum ada yang disentuh."""

    def __init__(self) -> None:
        super().__init__(TRANSIENT, PESAN_LAMA_SEMENTARA)


class TolakPraTukar(Exception):
    """Connector menolak tukar di pra-pemeriksaan: produksi belum tersentuh."""

    def __init__(self, kode: str) -> None:
        super().__init__(kode)
        self.kode = kode


def _galat_pra_tukar(exc: SiteError, langkah: str) -> Exception:
    """Galat siapkan/impor: produksi belum pernah disentuh, pesan tetap untuk UI."""
    if exc.kode == "wpmgr_staging_urutan" and langkah == "siapkan":
        return umum.galat_gagal(PESAN_URUTAN_SIAPKAN)
    if exc.kode in ("wpmgr_staging_kurang", "wpmgr_staging_tidak_ada"):
        return PotonganKurang()
    if exc.kode == "wpmgr_staging_ditahan":
        # Putusan R15: ditampilkan, tidak diulang segera; staging sendiri utuh.
        return umum.GalatDitolakTanpaUbah(PESAN_DITAHAN)
    if exc.kode == "wpmgr_staging_impor" and exc.error_class == STAGING_GAGAL:
        return umum.galat_gagal(f"Database staging ditolak saat diimpor di produksi: {bersih_teks(exc.pesan, 300)}")
    pesan = {
        "wpmgr_staging_verifikasi": PESAN_VERIFIKASI, "wpmgr_staging_rencana": PESAN_RENCANA,
        "wpmgr_staging_urutan": PESAN_URUTAN, "wpmgr_staging_direbut": PESAN_DIREBUT,
    }.get(exc.kode)
    return umum.galat_gagal(pesan) if pesan else exc


def _langkah_pra_tukar(sesi, job, staging, klien, badan: dict) -> None:
    for _ in range(MAKS_LANGKAH):
        umum.titik_potongan(sesi, job, staging)
        try:
            h = umum.ulangi(klien.staging_terapkan, badan, None)
        except SiteError as exc:
            raise _galat_pra_tukar(exc, badan["langkah"]) from None
        if h.get("selesai") is True:
            return
    raise umum.galat_gagal(f"Langkah {badan['langkah']} di produksi tidak selesai.")


def _ragu(exc: SiteError) -> bool:
    """Hasil permintaan tidak diketahui atau sementara: kirim ulang, jangan putuskan apa pun."""
    return exc.kode == KODE_SIBUK or (exc.error_class in (TRANSIENT, UNKNOWN, BAD_RESPONSE)
                                      and exc.kode not in KODE_TUKAR_GAGAL)


def _jeda(percobaan: int) -> None:
    time.sleep(umum.JEDA_ULANG[min(percobaan, len(umum.JEDA_ULANG) - 1)])


def _tukar(sesi, job, klien, dasar: dict, token: str) -> tuple[str, str] | None:
    """Kirim tukar sampai connector menjawab pasti. None = ditukar; (kode, alasan) = gagal pasti.

    Tidak ada keputusan yang diambil dari hasil yang tidak diketahui: tukar
    dikirim ulang, dan connector membalas keadaan sebenarnya (hasil tersimpan
    bila sudah ditukar, lanjutan bila masih 'menukar', 409 sibuk bila request
    sebelumnya masih berjalan, 409 urutan bila sudah dipulihkan). Penolakan
    pra-pemeriksaan melempar TolakPraTukar: tidak ada yang perlu dipulihkan.
    """
    badan = {**dasar, "langkah": "tukar", "token": token}
    ragu = 0
    for _ in range(MAKS_LANGKAH):
        umum.titik_potongan(sesi, job, None)
        try:
            h = klien.staging_terapkan(badan, token)
        except SiteError as exc:
            if exc.kode in KODE_TUKAR_PRA:
                raise TolakPraTukar(exc.kode) from None
            if exc.kode in KODE_TUKAR_GAGAL:
                return exc.kode, bersih_teks(exc.pesan, 300) or "tukar ditolak connector"
            if not _ragu(exc):
                # Kode lain (token, direbut, tidak ada, 4xx asing): keadaan
                # produksi tidak terbukti; tidak dipulihkan atas dugaan.
                raise SiteError(STAGING_GAGAL, PESAN_TUKAR_RAGU) from None
            ragu += 1
            if ragu > MAKS_RAGU_TUKAR:
                raise SiteError(TRANSIENT, PESAN_TUKAR_RAGU) from None
            _jeda(ragu - 1)
            continue
        if h.get("selesai") is True:
            if h.get("status") in ("ditukar", "selesai"):
                return None
            raise SiteError(TRANSIENT, PESAN_TUKAR_RAGU)
        ragu = 0
    raise SiteError(TRANSIENT, PESAN_TUKAR_RAGU)


def _pulihkan(sesi, job, klien, dasar: dict, token: str | None) -> None:
    """Pulihkan sampai connector melaporkan 'dipulihkan'; status 'memulihkan' diulang terus."""
    badan = {**dasar, "langkah": "pulihkan", "token": token}
    gagal = 0
    for _ in range(MAKS_LANGKAH):
        umum.titik_potongan(sesi, job, None)
        try:
            h = klien.staging_terapkan(badan, token)
        except SiteError as exc:
            if exc.kode == "wpmgr_staging_pulihkan" or _ragu(exc):
                gagal += 1
                if gagal > MAKS_ULANG_PULIHKAN:
                    raise SiteError(TRANSIENT, PESAN_PULIH_RAGU) from None
                _jeda(gagal - 1)
                continue
            raise SiteError(STAGING_GAGAL, PESAN_PULIH_RAGU) from None
        if h.get("selesai") is True:
            if h.get("status") == "dipulihkan":
                return
            raise SiteError(TRANSIENT, PESAN_PULIH_RAGU)
    raise SiteError(TRANSIENT, PESAN_PULIH_RAGU)


def _selesai(klien, dasar: dict, token: str | None) -> None:
    # Token dikirim sebagai header X-Wpmgr-Lewati (connector tidak memeriksa
    # token untuk selesai): bila .maintenance dorong ini masih terpasang
    # (mis. lepas_pengaman gagal sesudah 'ditukar'), request tetap sampai.
    try:
        h = umum.ulangi(klien.staging_terapkan, {**dasar, "langkah": "selesai"}, token)
    except SiteError as exc:
        if exc.error_class == BERKAS_HILANG:
            # Putusan R15: area sudah tidak ada sesudah percobaan yang hasilnya
            # tidak diketahui berarti langkah ini sudah tuntas dan dibersihkan.
            return
        kelas = TRANSIENT if _ragu(exc) else STAGING_GAGAL
        raise SiteError(kelas, PESAN_SELESAI_RAGU) from None
    if h.get("selesai") is not True:
        raise SiteError(TRANSIENT, PESAN_SELESAI_RAGU)


def terapkan(sesi, job, staging, klien, site_url: str, dorong_id: str, jumlah: int, sha: str,
             ada_sql: bool, token: str, sebelum_tukar=None) -> None:
    """Terapkan dorongan yang sudah terunggah: siapkan, impor, tukar, lalu selesai.

    Memanggil `pulihkan` sendiri bila tukar gagal pasti, lalu melempar
    galat_gagal "sudah dipulihkan". `sebelum_tukar()` (opsional) dipanggil
    tepat sebelum tukar pertama dikirim -- titik terakhir dorong boleh
    dibatalkan (cek ulang tanda air). Keadaan di `kemajuan.langkah_terapkan`.
    """
    dasar = {"dorong_id": dorong_id}
    langkah = umum.kemajuan(job).get("langkah_terapkan") or "siapkan"
    # Siapkan/impor/tukar bisa berjalan lama (polling berulang): detak dari
    # utas latar juga (putusan F7). Kemajuan sudah ter-commit di titik ini.
    with umum.detak_latar(sesi, job):
        while langkah != "beres":
            if langkah == "siapkan":
                _langkah_pra_tukar(sesi, job, staging, klien, {
                    **dasar, "langkah": "siapkan", "jumlah_potongan": jumlah, "sha256_rencana": sha})
                langkah = _pindah(sesi, job, "impor" if ada_sql else "cek_ulang")
            elif langkah == "impor":
                _langkah_pra_tukar(sesi, job, staging, klien, {**dasar, "langkah": "impor"})
                langkah = _pindah(sesi, job, "cek_ulang")
            elif langkah == "cek_ulang":
                umum.titik_potongan(sesi, job, staging)
                if sebelum_tukar is not None:
                    sebelum_tukar()
                    # Cek ulang bisa memakan waktu: batal diperiksa sekali lagi.
                    umum.titik_potongan(sesi, job, staging)
                # Tulis-lebih-dulu: sejak baris ini produksi mungkin tersentuh.
                langkah = _pindah(sesi, job, "tukar", tolak_pra_tukar=None)
            elif langkah == "tukar":
                try:
                    gagal = _tukar(sesi, job, klien, dasar, token)
                except TolakPraTukar as tolak:
                    # Status connector masih siap/terimpor: produksi tidak
                    # tersentuh dan dorongan yang terunggah tetap utuh. Kembali
                    # ke titik sebelum tukar (batal berlaku lagi, tanda air dicek
                    # ulang); mengirim tukar lagi nanti aman.
                    _pindah(sesi, job, "cek_ulang", tolak_pra_tukar=tolak.kode)
                    if tolak.kode == "wpmgr_staging_maintenance":
                        raise SiteError(TRANSIENT, PESAN_MAINTENANCE_ULANG) from None
                    raise umum.GalatDitolakTanpaUbah(PESAN_TOLAK_PRA[tolak.kode]) from None
                langkah = (_pindah(sesi, job, "selesai") if gagal is None
                           else _pindah(sesi, job, "pulihkan", galat_tukar_kode=gagal[0], galat_tukar=gagal[1]))
            elif langkah == "pulihkan":
                _pulihkan(sesi, job, klien, dasar, token)
                langkah = _pindah(sesi, job, "dipulihkan", pulih_terkonfirmasi=True)
            elif langkah == "dipulihkan":
                status = cek_halaman(site_url)
                if staging is not None and not _hidup(status):
                    sesi.get(Staging, staging.id, populate_existing=True).dorong_gagal_pada = umum.sekarang()
                    sesi.commit()
                k = umum.kemajuan(job)
                alasan = k.get("galat_tukar") or "tukar ditolak connector"
                if k.get("galat_tukar_kode") == "wpmgr_staging_tukar":
                    # gagal_tukar(): penukaran sempat berjalan lalu dibalik connector.
                    raise umum.galat_gagal(f"Terapkan di produksi gagal: {alasan}. Produksi sudah dipulihkan "
                                           "dari salinan lokal connector.")
                # 409 urutan: bisa pra-pemeriksaan (status masih terimpor, mis.
                # tabel hasil impor tidak ditemukan) atau jawaban atas tukar yang
                # dikirim ulang sesudah connector memulihkan sendiri. Keduanya
                # berakhir pada keadaan sebelum dorong; kalimatnya benar untuk keduanya.
                raise umum.galat_gagal(f"Terapkan di produksi tidak tuntas: {alasan}. Produksi dalam keadaan "
                                       "sebelum dorong (tidak pernah diubah, atau diubah lalu dipulihkan "
                                       "connector).")
            elif langkah == "selesai":
                _selesai(klien, dasar, token)
                langkah = _pindah(sesi, job, "beres")
            else:
                raise umum.galat_gagal("Keadaan terapkan dorong tidak dikenal.")


# ---- bersihkan, halaman utama, dan dorongan lama ------------------------------


def _bersihkan_rinci(klien, dorong_id: str, tenggat: float | None = None,
                     detak=None) -> tuple[bool, SiteError | None]:
    """(True, None) bila connector memastikan area dorong ini bersih (atau memang tidak ada).

    Connector menjawab lagi:true bila tenggat request-nya habis di tengah;
    putaran berikutnya diberi jeda, `detak()` (opsional) dipanggil di antara
    putaran supaya klaim job tetap hidup, dan seluruhnya dibatasi `tenggat`
    (bawaan TENGGAT_BERSIHKAN). (False, None) = belum tuntas dalam tenggat.
    """
    akhir = time.monotonic() + (TENGGAT_BERSIHKAN if tenggat is None else tenggat)
    for putaran in range(MAKS_PUTARAN_BERSIHKAN):
        if putaran:
            if detak is not None:
                detak()
            _jeda(0)
        sisa = akhir - time.monotonic()
        if sisa <= 0:
            return False, None
        try:
            h = klien.staging_bersihkan(dorong_id, tenggat=max(1.0, sisa))
        except SiteError as exc:
            if exc.error_class == BERKAS_HILANG:
                return True, None
            return False, exc
        # tahan_batal: tabel pembalikan ditahan 24 jam, tetapi area dan kunci
        # dorong sudah dilepas -- bagi dashboard ini sudah bersih.
        if h.get("lagi") is not True:
            return True, None
    return False, None


def bersihkan(klien, dorong_id: str, tenggat: float | None = None, detak=None) -> bool:
    """Buang area sementara dorong di connector; upaya terbaik, tidak pernah melempar.

    Yang gagal dibersihkan di sini dibereskan cron connector (24 jam) atau
    dorongan berikutnya. Connector menolak membersihkan dorongan yang sedang
    atau sudah ditukar (409), jadi ini tidak pernah membatalkan tukar.
    """
    ok, galat = _bersihkan_rinci(klien, dorong_id, tenggat, detak)
    if galat is not None:
        log.warning("Bersihkan dorong %s gagal: %s (%s)", dorong_id, galat.error_class, galat.kode)
    return ok


def cek_halaman(url: str) -> int:
    """Status HTTP halaman utama produksi, atau 0 bila tidak dapat dihubungi.

    Redirect tidak diikuti (3xx dihitung hidup); tenggat total dan batas byte
    berlaku atas seluruh permintaan (putusan F11).
    """
    http = umum.buat_http()
    try:
        status, _, _ = minta_bertenggat(http, "GET", url.rstrip("/") + "/", headers={
            "Accept": "text/html", "Accept-Encoding": "identity", "Connection": "close"},
            timeout=TIMEOUT_HALAMAN, tenggat=TENGGAT_HALAMAN, batas_byte=BATAS_HALAMAN, potong=True)
        return status
    except (httpx.HTTPError, TenggatHabis, TanpaHasil, MelebihiBatas):
        return 0
    finally:
        http.close()


def _hidup(status: int) -> bool:
    return 200 <= status < 400


def _tandai_bersih(sesi, job) -> None:
    payload = dict(job.payload or {})
    payload["kemajuan"] = {**(payload.get("kemajuan") or {}), "produksi_bersih": True}
    job.payload = payload
    flag_modified(job, "payload")
    sesi.commit()


def _tuntaskan_dorongan_lama(sesi, job, klien, dorong_id: str, token) -> str | None:
    """Tuntaskan satu dorongan lama di connector.

    Mengembalikan "dibersihkan", "diselesaikan", atau "dipulihkan" bila areanya
    kini bersih; None bila connector menolak dengan kode yang tidak menahan
    dorongan baru (upaya terbaik). Gangguan jaringan atau pembersihan yang
    belum tuntas melempar TRANSIENT: dorongan baru BERHENTI sebelum membaca
    atau mengunggah apa pun, dan percobaan berikutnya mengulang rekonsiliasi.
    """
    def detak():
        umum.detak(sesi, job)

    def bersih_atau_ulang() -> None:
        ok, _ = _bersihkan_rinci(klien, dorong_id, detak=detak)
        if not ok:
            raise GalatLamaSementara()

    ok, galat = _bersihkan_rinci(klien, dorong_id, detak=detak)
    if ok:
        return "dibersihkan"
    if galat is None or (_ragu(galat) and galat.kode != KODE_SIBUK):
        raise GalatLamaSementara()
    if galat.kode not in (KODE_SIBUK, "wpmgr_staging_perlu_pemulihan"):
        log.warning("Dorongan lama %s tidak dapat dibersihkan: %s (%s)", dorong_id, galat.error_class, galat.kode)
        return None
    # Dorongan lama sedang/sudah menyentuh produksi. Yang sudah ditukar
    # diselesaikan (hasilnya memang yang diinginkan job lama); selain itu
    # (menukar/memulihkan) connector menjawab 409 urutan dan dipulihkan.
    dasar = {"dorong_id": dorong_id}
    try:
        h = umum.ulangi(klien.staging_terapkan, {**dasar, "langkah": "selesai"}, token)
        if h.get("selesai") is not True:
            raise GalatLamaSementara()
        hasil = "diselesaikan"
    except SiteError as exc:
        if exc.error_class == BERKAS_HILANG:
            return "dibersihkan"
        if exc.kode != "wpmgr_staging_urutan":
            if _ragu(exc):
                raise GalatLamaSementara() from None
            # Dorongan baru belum menyentuh apa pun: ditolak tanpa menandai staging gagal.
            raise umum.GalatDitolakTanpaUbah(PESAN_LAMA_GAGAL) from None
        try:
            _pulihkan(sesi, job, klien, dasar, token)
        except SiteError as exc2:
            if exc2.error_class == TRANSIENT:
                raise GalatLamaSementara() from None
            raise umum.GalatDitolakTanpaUbah(PESAN_LAMA_GAGAL) from None
        hasil = "dipulihkan"
    bersih_atau_ulang()
    return hasil


def selesaikan_dorongan_lama(sesi, job, site, klien) -> None:
    """Dorongan dari job dorong/kembalikan lama yang belum terbukti bersih dituntaskan dulu.

    Hanya kandidat yang disaring di SQL (pernah mengunggah, belum terbukti
    bersih), jadi job lama yang sudah bersih tidak pernah menutupi dorongan
    macet di luar batas MAKS_JOB_LAMA. Setiap dorongan yang dituntaskan
    dicatat di log aktivitas. Bila dituntaskan dengan pulihkan, produksi
    kembali ke keadaan sebelum dorongan lama itu: snapshotnya dibuang (sama
    seperti akhiri_gagal) dan `dorong_gagal_pada` mengikuti halaman utama.
    Yang diselesaikan (tukar sudah terjadi) mempertahankan snapshotnya dan
    tidak mengubah `dorong_gagal_pada` (dinilai ulang di akhir dorongan baru).
    """
    kemajuan_lama = Job.payload["kemajuan"]
    lama = sesi.scalars(
        select(Job).where(Job.site_id == job.site_id, Job.id != job.id,
                          Job.tipe.in_((JobType.staging_dorong, JobType.staging_kembalikan)),
                          Job.status.in_((JobStatus.failed, JobStatus.success, JobStatus.unknown)),
                          kemajuan_lama["unggah_mulai"].astext == "true",
                          func.coalesce(kemajuan_lama["produksi_bersih"].astext, "false") != "true")
        .order_by(Job.id.desc()).limit(MAKS_JOB_LAMA)
    ).all()
    for j in lama:
        k = umum.kemajuan(j)
        dorong_id = k.get("dorong_id")
        if not isinstance(dorong_id, str) or not POLA_ID_DORONG.fullmatch(dorong_id):
            continue
        token = k.get("token") if isinstance(k.get("token"), str) else None
        hasil = _tuntaskan_dorongan_lama(sesi, job, klien, dorong_id, token)
        if hasil is None:
            continue
        # Baris snapshot, dorong_gagal_pada, log aktivitas, dan penanda bersih
        # job lama di-commit BERSAMA (commit di _tandai_bersih): worker yang mati
        # di tengah meninggalkan job lama tetap kandidat, bukan setengah dicatat.
        if hasil == "dipulihkan":
            _hapus_baris_snapshot(sesi, site.id, j.id)
            st = sesi.scalar(select(Staging).where(Staging.site_id == site.id))
            if st is not None:
                st.dorong_gagal_pada = None if _hidup(cek_halaman(site.url)) else umum.sekarang()
        umum.catat_aktivitas(sesi, site.id, job, f"Dorongan sebelumnya (job #{j.id}) {hasil} di produksi",
                             {"job_lama": j.id, "hasil": hasil})
        _tandai_bersih(sesi, j)
        if hasil == "dipulihkan":
            # Sesudah commit: direktori yatim bila mati di sini dibersihkan prune-staging.
            _hapus_dir_staging(f"{site.id}/snapshot/j{j.id}")


# ---- snapshot -----------------------------------------------------------------


def ukuran_dir(path: Path) -> int:
    """Total ukuran berkas biasa di bawah `path`, tanpa mengikuti symlink."""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for n in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, n))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total


def _hapus_dir_staging(relatif: str) -> None:
    """Hapus direktori di bawah WPMGR_STAGING_DIR tanpa pernah mengikuti symlink."""
    try:
        p = jalur_di_dalam(get_settings().jalur_staging, relatif)
    except PathTidakAman:
        return
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return
    if adalah_tautan(st) or not stat.S_ISDIR(st.st_mode):
        return
    # rmtree tidak mengikuti symlink di dalam pohon (dan di Linux memakai
    # varian berbasis fd yang kebal ditukar di tengah jalan).
    shutil.rmtree(p, ignore_errors=True)


def pangkas_snapshot(sesi, site_id, n: int) -> int:
    daftar = sesi.scalars(
        select(StagingSnapshot)
        .where(StagingSnapshot.site_id == site_id, StagingSnapshot.status.in_(("tersedia", "dipakai")))
        .order_by(StagingSnapshot.dibuat_pada.desc(), StagingSnapshot.id.desc())
    ).all()
    dipangkas = 0
    for s in daftar[n:]:
        _hapus_dir_staging(s.path)
        s.status = "dipangkas"
        dipangkas += 1
    return dipangkas


def _buang_snapshot(sesi, site_id, job_id) -> None:
    """Snapshot dorongan yang tidak pernah (atau terbukti tidak lagi) mengubah produksi dibuang.

    Mengembalikan produksi ke snapshot seperti itu hanya menghapus data yang
    masuk sesudahnya, jadi ia tidak boleh tampil sebagai titik kembali.
    """
    _hapus_baris_snapshot(sesi, site_id, job_id)
    sesi.commit()
    _hapus_dir_staging(f"{site_id}/snapshot/j{job_id}")


def _hapus_baris_snapshot(sesi, site_id, job_id) -> None:
    """Hapus baris snapshot milik job ini (tanpa commit)."""
    for row in sesi.scalars(select(StagingSnapshot).where(StagingSnapshot.site_id == site_id,
                                                          StagingSnapshot.job_id == job_id)).all():
        sesi.delete(row)


# ---- job ----------------------------------------------------------------------


def _tanda_air_sekarang(klien, lama: dict | None) -> dict:
    posts = ((lama or {}).get("sumber") or {}).get("posts") or {}
    ta = urai_tanda_air(umum.ulangi(klien.staging_tanda_air, posts.get("diubah") or None, posts.get("maks_id") or None))
    if ta is None:
        raise SiteError(BAD_RESPONSE, "Tanda air produksi tidak dapat dibaca.")
    return ta


def _simpan_rencana(berkas: Path, ganti: list[Entri], hapus: list[str], sql: list[Path]) -> None:
    berkas.write_text(json.dumps({"ganti": [[e.path, e.ukuran, e.mtime, e.hash] for e in ganti],
                                  "hapus": hapus, "sql": [p.name for p in sql]}, ensure_ascii=False), encoding="utf-8")


def _muat_rencana(kerja: Path) -> tuple[list[Entri], list[str], list[Path]]:
    data = json.loads((kerja / "rencana.json").read_text(encoding="utf-8"))
    return [Entri(*x) for x in data["ganti"]], data["hapus"], [kerja / n for n in data["sql"]]


def _tambah_peringatan(k: dict, daftar: list[str]) -> list[str]:
    semua = list(k.get("peringatan") or [])
    for teks in daftar:
        if len(semua) < MAKS_PERINGATAN:
            semua.append(bersih_teks(teks, 300))
    return semua


class _Dorong:
    """Satu putaran job dorong: konteks bersama dan satu metode per tahap."""

    def __init__(self, sesi, job, site, staging: Staging, klien, pb) -> None:
        self.sesi, self.job, self.site, self.staging, self.klien, self.pb = sesi, job, site, staging, klien, pb
        self.p = job.payload or {}
        self.mode = self.p.get("mode")
        self.akar = umum.dir_site(site.id)
        self.kerja = self.akar / "dorong"
        self.snap = self.akar / "snapshot" / f"j{job.id}"

    def simpan(self, **perubahan) -> dict:
        return umum.simpan_kemajuan(self.sesi, self.job, **perubahan)

    # -- tahap ------------------------------------------------------------

    def tanda_air(self, k: dict) -> dict:
        if self.mode != "timpa_penuh":
            return self.simpan(tahap_dorong="manifest", perubahan=[])
        ta = _tanda_air_sekarang(self.klien, self.staging.tanda_air)
        perubahan = bandingkan_tanda_air(self.staging.tanda_air, ta)
        posts = ta["sumber"].get("posts")
        tidak_pasti = posts is not None and posts.get("diubah_sejak") is None
        if tidak_pasti and not any("dipastikan" in x for x in perubahan):
            # Connector tidak menghitung post yang diubah sejak tarik: data
            # baru mungkin ada. Timpa penuh tetap butuh konfirmasi nama.
            perubahan.append(PESAN_POST_TIDAK_PASTI)
        if perubahan and self.p.get("konfirmasi_nama") != self.site.nama:
            raise umum.GalatDitolakTanpaUbah(
                "Produksi punya data baru sejak staging ditarik: " + ", ".join(perubahan)
                + ". Timpa penuh akan menghapusnya. Ketik nama site untuk tetap menimpa.")
        return self.simpan(tahap_dorong="manifest", tanda_air_dorong=ta, perubahan=perubahan)

    def manifest(self, k: dict) -> dict:
        k = ambil_manifest(self.sesi, self.job, self.staging, self.klien, self.kerja, k)
        if k["info"].get("unggah_terlalu_kecil"):
            raise umum.GalatDitolakTanpaUbah(PESAN_UNGGAH_KECIL)
        return self.simpan(tahap_dorong="rencana")

    def rencana(self, k: dict) -> dict:
        info = k["info"]
        peringatan: list[str] = []
        with umum.detak_latar(self.sesi, self.job):
            staging_entri = pindai_lokal(self.akar / "files", Indeks(self.akar / "indeks.jsonl").muat(), peringatan)
        r = rencana_dorong(self.mode, staging_entri, _muat_manifest(self.kerja))
        sql: list[Path] = []
        if r.db:
            sql = [self._ekspor_staging(info)]
            with umum.detak_latar(self.sesi, self.job):
                alasan = periksa_berkas_terapkan(sql)
            if alasan is not None:
                raise umum.GalatDitolakTanpaUbah(
                    f"Ekspor database staging memuat {alasan}; connector produksi akan menolaknya saat impor. "
                    "Dorong dibatalkan sebelum apa pun diubah.")
        _simpan_rencana(self.kerja / "rencana.json", r.ganti, r.hapus, sql)
        tahap = "snapshot_db" if (r.ganti or r.hapus or sql) else "tanpa_perubahan"
        return self.simpan(tahap_dorong=tahap, byte_dorong=r.byte, jumlah_ganti=len(r.ganti),
                           jumlah_hapus=len(r.hapus), db=bool(sql), peringatan=_tambah_peringatan(k, peringatan))

    def _ekspor_staging(self, info: dict) -> Path:
        """search-replace --export di container staging, lalu dipindah dari ekspor/ lewat `aman`.

        ekspor/ di-bind mount ke container yang menjalankan kode salinan
        produksi sebagai UID dashboard: berkasnya bisa berupa symlink, FIFO,
        atau tumbuh tanpa batas, jadi diperlakukan sebagai masukan penyerang.
        """
        relatif = "ekspor/dorong.sql"
        try:
            hapus_berkas(self.akar, relatif)
        except PathTidakAman:
            raise umum.galat_gagal("Folder ekspor staging tidak aman (berupa symlink); jalankan tarik lagi.") from None
        with umum.detak_latar(self.sesi, self.job):
            self.pb.wpcli(self.staging.nama, "search-replace", umum.url_staging(self.staging), info["home"],
                          "--export")
        batas = anggaran_db(max(info["ukuran_db"], self.staging.ukuran_db or 0))
        tujuan = self.kerja / "dorong.sql"
        ditulis = 0
        try:
            with buka_baca(self.akar, relatif) as f, open(tujuan, "wb") as keluar:
                for bagian in iter(lambda: f.read(SALIN_BLOK), b""):
                    ditulis += len(bagian)
                    if ditulis > batas:
                        raise umum.galat_gagal(f"Ekspor database staging melebihi {format_byte(batas)}; "
                                               "jauh di atas ukuran database yang diketahui.")
                    keluar.write(bagian)
        except FileNotFoundError:
            raise umum.galat_gagal("Ekspor database staging tidak ditemukan.") from None
        except PathTidakAman:
            raise umum.galat_gagal("Ekspor database staging tidak aman (symlink atau bukan berkas biasa).") from None
        try:
            hapus_berkas(self.akar, relatif)
        except (PathTidakAman, OSError):
            pass
        if ditulis == 0:
            raise umum.galat_gagal("Ekspor database staging kosong.")
        return tujuan

    def _periksa_create_produksi(self, nama: str, sql: bytes) -> None:
        alasan = periksa_terapkan(sql)
        if alasan is not None:
            raise umum.GalatDitolakTanpaUbah(
                f"Struktur tabel produksi {nama} memuat {alasan}; snapshot database tidak akan bisa dipulihkan "
                "lewat connector. Dorong timpa penuh dibatalkan sebelum apa pun diubah.")

    def snapshot_db(self, k: dict) -> dict:
        """Ekspor database produksi ke snapshot (sebelum snapshot berkas, supaya R8 menolak sedini mungkin)."""
        info = k["info"]
        if "snapshot_tabel" not in k:
            resp = umum.ulangi(self.klien.staging_snapshot, [], True)
            tabel = urai_tabel(resp.get("tabel"), info["prefix"])
            if not tabel:
                raise umum.galat_gagal("Daftar tabel produksi untuk snapshot kosong.")
            k = self.simpan(snapshot_tabel=tabel, tabel={}, tabel_seq={}, tabel_selesai=[], db_diterima=0)
        tabel = k["snapshot_tabel"]
        info_db = {"charset": info["charset"], "tabel": tabel,
                   "ukuran_db": min(sum(t["ukuran"] for t in tabel), 2**62)}
        # R8: struktur tabel produksi yang tidak bisa diimpor ulang lewat
        # connector membuat snapshot database timpa penuh tidak bisa
        # dipulihkan. Snapshot hanya-kode tidak pernah mengimpor database
        # saat Kembalikan (Koreksi #13), jadi tidak ditahan olehnya. SQL
        # disimpan dan diperiksa MENTAH (untuk_mariadb=False): itulah yang
        # kelak diimpor Kembalikan dan dilihat ubah() connector.
        periksa = self._periksa_create_produksi if self.mode == "timpa_penuh" else None
        with umum.detak_latar(self.sesi, self.job):
            k = ekspor_db(self.sesi, self.job, self.staging, self.klien, self.snap, info_db, k, "snapshot_berkas",
                          periksa_awal=periksa, untuk_mariadb=False)
        return self.simpan(tahap_dorong="snapshot_berkas")

    def _metadata_produksi(self, paths: list[str]) -> dict[str, dict]:
        hasil: dict[str, dict] = {}
        for i in range(0, len(paths), BATCH_SNAPSHOT):
            batch = paths[i:i + BATCH_SNAPSHOT]

            def minta(batch=batch):
                resp = self.klien.staging_snapshot(batch, False)
                daftar = resp.get("berkas")
                if not isinstance(daftar, list) or len(daftar) != len(batch) or any(
                        not isinstance(m, dict) or m.get("path") != p for m, p in zip(daftar, batch)):
                    raise SiteError(BAD_RESPONSE, "Balasan snapshot tidak sesuai permintaan.")
                return resp

            resp = umum.ulangi(minta)
            for m, p in zip(resp["berkas"], batch):
                hasil[p] = m
        return hasil

    def snapshot_berkas(self, k: dict) -> dict:
        """Salinan produksi dari berkas yang akan ditimpa atau dihapus dorongan ini.

        Putusan F24: snapshot hanya menyimpan berkas produksi yang akan
        TERTIMPA atau TERHAPUS, ditambah DAFTAR berkas yang akan DITAMBAHKAN
        (meta.json "baru"). Kembalikan menulis ulang berkas tersimpan dan
        menghapus berkas yang ditambahkan; berkas lain tidak disentuh.
        """
        ganti, hapus, _ = _muat_rencana(self.kerja)
        set_ganti = {e.path for e in ganti}
        meta_prod = self._metadata_produksi([e.path for e in ganti] + list(hapus))
        ada: dict[str, Entri] = {}
        baru: list[str] = []
        for p, m in meta_prod.items():
            if m.get("ada") is not True:
                if p in set_ganti:
                    baru.append(p)
                continue
            e = entri_dari({"path": p, "ukuran": m.get("ukuran"), "mtime": m.get("mtime"), "hash": None})
            if e is None:
                # Berkas yang ada tetapi metadatanya rusak tidak bisa disalin;
                # melewatinya berarti snapshot diam-diam tidak lengkap.
                raise SiteError(BAD_RESPONSE, "Metadata berkas produksi untuk snapshot tidak sah.")
            ada[p] = e
        berkas_dir = self.snap / "berkas"
        berkas_dir.mkdir(parents=True, exist_ok=True)
        indeks = Indeks(self.snap / "indeks.jsonl")
        lokal = indeks.muat()
        # Anggaran penerimaan Salin dihitung dari ukuran berkas snapshot ini,
        # bukan dari ukuran dorongan.
        k = self.simpan(byte_total=sum(e.ukuran for e in ada.values()), byte_selesai=0, byte_diterima=0)
        salin = Salin(self.sesi, self.job, self.staging, self.klien, berkas_dir, indeks, lokal, k)
        with umum.detak_latar(self.sesi, self.job):
            for pot in bagi_potongan(selisih(ada, lokal).diambil):
                umum.titik_potongan(self.sesi, self.job, self.staging)
                if pot.jenis == "rentang":
                    if pot.dari == 0:
                        salin.besar(pot.berkas[0])
                else:
                    salin.paket(pot.berkas)
        tersalin = indeks.muat()
        tertinggal = sorted(p for p in ada if p not in tersalin)
        if tertinggal:
            # Hilang di produksi sejak metadata diambil: sekarang berkas itu
            # "baru" (Kembalikan menghapusnya). Yang masih ada tetapi tidak
            # tersalin membuat snapshot tidak lengkap: dorong dihentikan.
            for p, m in self._metadata_produksi(tertinggal).items():
                if m.get("ada") is True:
                    raise umum.galat_gagal(f"Berkas produksi {bersih_teks(p, 300)} tidak dapat disalin ke snapshot; "
                                           "dorong dihentikan sebelum produksi diubah.")
                if p in set_ganti:
                    baru.append(p)
        (self.snap / "meta.json").write_text(json.dumps({
            "mode": self.mode, "baru": sorted(set(baru)),
            "diganti": sorted(p for p in tersalin if p in set_ganti),
            "dihapus": sorted(p for p in tersalin if p not in set_ganti), "charset": k["info"]["charset"],
            "prefix": k["info"]["prefix"], "home": k["info"]["home"], "batas_unggah": k["info"]["batas_unggah"],
            "tanda_air": k.get("tanda_air_dorong"),
        }, ensure_ascii=False), encoding="utf-8")
        return self.simpan(tahap_dorong="snapshot_catat", peringatan=list(salin.k.get("peringatan") or []))

    def snapshot_catat(self, k: dict) -> dict:
        meta = json.loads((self.snap / "meta.json").read_text(encoding="utf-8"))
        row = self.sesi.scalar(select(StagingSnapshot).where(StagingSnapshot.job_id == self.job.id,
                                                             StagingSnapshot.site_id == self.site.id))
        if row is None:
            row = StagingSnapshot(
                site_id=self.site.id, job_id=self.job.id, jenis="sebelum_dorong", status="tersedia",
                ukuran=min(ukuran_dir(self.snap), 2**62), path=f"{self.site.id}/snapshot/j{self.job.id}",
                detail={"mode": self.mode, "jumlah_berkas": len(meta["diganti"]) + len(meta["dihapus"]),
                        "baru": len(meta["baru"]), "perubahan": k.get("perubahan", [])})
            self.sesi.add(row)
            self.sesi.commit()
        return self.simpan(tahap_dorong="unggah", snapshot_id=row.id, unggah_nomor=0)

    def sumber(self, k: dict) -> SumberDorong:
        ganti, hapus, sql = _muat_rencana(self.kerja)
        return SumberDorong(ganti, self.akar / "files", hapus, sql, k["info"]["charset"])

    def unggah(self, k: dict) -> dict:
        batas = min(UKURAN_UNGGAH, k["info"]["batas_unggah"])
        with umum.detak_latar(self.sesi, self.job):
            k = unggah_semua(self.sesi, self.job, self.staging, self.klien, self.sumber(k), k["dorong_id"], batas, k)
        return self.simpan(tahap_dorong="terapkan", langkah_terapkan="siapkan")

    def _cek_ulang_tanda_air(self) -> None:
        """Spec §6.3 langkah 6, tepat sebelum tukar: data baru sejak awal dorong membatalkan dorong."""
        dasar = umum.kemajuan(self.job).get("tanda_air_dorong")
        beda = bandingkan_tanda_air(dasar, _tanda_air_sekarang(self.klien, dasar))
        if beda:
            raise umum.galat_gagal("Data produksi berubah selama dorong (" + ", ".join(beda)
                                   + "); dorong dibatalkan dan produksi tidak diubah.")

    def terapkan(self, k: dict) -> dict:
        try:
            terapkan(self.sesi, self.job, self.staging, self.klien, self.site.url, k["dorong_id"],
                     k["jumlah_potongan"], k["sha256_rencana"], bool(k.get("ada_sql")), k["token"],
                     sebelum_tukar=self._cek_ulang_tanda_air if self.mode == "timpa_penuh" else None)
        except PotonganKurang:
            # Area dorong di produksi kehilangan potongan (mis. dibersihkan
            # cron setelah 24 jam): diunggah ulang sekali. Potongan yang masih
            # ada dibalas connector sebagai ulangan yang sama.
            if k.get("unggah_ulang"):
                raise umum.galat_gagal(PESAN_KURANG_BERULANG) from None
            return self.simpan(tahap_dorong="unggah", unggah_nomor=0, unggah_ulang=True,
                               langkah_terapkan="siapkan")
        return self.simpan(tahap_dorong="cek")

    def cek(self, k: dict) -> dict:
        status = cek_halaman(self.site.url)
        if bersihkan(self.klien, k["dorong_id"], detak=lambda: umum.detak(self.sesi, self.job)):
            k = self.simpan(produksi_bersih=True)
        ok = _hidup(status)
        st = self.sesi.get(Staging, self.staging.id, populate_existing=True)
        st.status = StatusStaging.siap
        st.dorong_gagal_pada = None if ok else umum.sekarang()
        pangkas_snapshot(self.sesi, self.site.id, get_settings().staging_snapshot)
        detail = {"mode": self.mode, "berkas": k.get("jumlah_ganti", 0), "hapus": k.get("jumlah_hapus", 0),
                  "db": bool(k.get("db")), "ukuran": format_byte(k.get("byte_dorong", 0)),
                  "perubahan": k.get("perubahan", []), "snapshot_id": k.get("snapshot_id"),
                  "halaman_utama": status, "peringatan": list(k.get("peringatan") or [])[:10]}
        judul = f"Dorong ke produksi ({LABEL_MODE[self.mode]})"
        if not ok:
            judul += f": halaman utama membalas HTTP {status}" if status else ": halaman utama tidak dapat dihubungi"
        umum.catat_aktivitas(self.sesi, self.site.id, self.job, judul, detail, level="info" if ok else "error")
        self.sesi.commit()
        shutil.rmtree(self.kerja, ignore_errors=True)
        return {**detail, "dorong_gagal": not ok}

    def tanpa_perubahan(self, k: dict) -> dict:
        st = self.sesi.get(Staging, self.staging.id, populate_existing=True)
        st.status = StatusStaging.siap
        umum.catat_aktivitas(self.sesi, self.site.id, self.job,
                             f"Dorong ke produksi ({LABEL_MODE[self.mode]}): tidak ada perubahan")
        self.sesi.commit()
        shutil.rmtree(self.kerja, ignore_errors=True)
        return {"mode": self.mode, "berkas": 0, "hapus": 0, "db": False, "dorong_gagal": False,
                "halaman_utama": None}


TAHAP = ("tanda_air", "manifest", "rencana", "snapshot_db", "snapshot_berkas", "snapshot_catat", "unggah",
         "terapkan")
TAHAP_AKHIR = ("cek", "tanpa_perubahan")


def dorong(sesi, job, site, staging: Staging, klien, pb) -> dict:
    d = _Dorong(sesi, job, site, staging, klien, pb)
    if d.mode not in MODE:
        raise umum.GalatDitolakTanpaUbah(PESAN_MODE)
    k = umum.kemajuan(job)
    if "tahap_dorong" not in k:
        if staging.ditarik_pada is None or not staging.tanda_air:
            raise umum.GalatDitolakTanpaUbah(PESAN_BELUM_TARIK)
        if not staging.aktif:
            raise umum.GalatDitolakTanpaUbah(PESAN_DIJEDA)
        # Dorongan lama yang belum dibersihkan (mis. wpmgr_old_* tertinggal
        # atau tertahan di 'ditukar') menahan kunci dorong: dituntaskan dulu.
        # Diulang pada setiap percobaan sampai tuntas (penanda di kemajuan):
        # gangguan jaringan di sini menghentikan dorongan baru sebelum apa pun.
        if not k.get("lama_dituntaskan"):
            selesaikan_dorongan_lama(sesi, job, site, klien)
            k = umum.simpan_kemajuan(sesi, job, lama_dituntaskan=True)
        shutil.rmtree(d.kerja, ignore_errors=True)
        k = umum.simpan_kemajuan(sesi, job, tahap_dorong="tanda_air", dorong_id=uuid.uuid4().hex,
                                 token=secrets.token_hex(16), mulai=umum.sekarang().isoformat(),
                                 byte_selesai=0, byte_total=0, peringatan=[])
    d.kerja.mkdir(parents=True, exist_ok=True)
    while k["tahap_dorong"] not in TAHAP_AKHIR:
        tahap = k["tahap_dorong"]
        if tahap not in TAHAP:
            raise umum.galat_gagal("Keadaan dorong tidak dikenal.")
        k = getattr(d, tahap)(k)
    return getattr(d, k["tahap_dorong"])(k)


def boleh_batal(job) -> bool:
    """Batal hanya berlaku sebelum tukar dikirim ke produksi."""
    return umum.kemajuan(job).get("langkah_terapkan") not in LANGKAH_SESUDAH_TUKAR


def _staging_utuh(sesi, site_id, pesan: str) -> SiteError:
    """Kegagalan final yang tidak menyentuh produksi maupun staging: staging tidak dibiarkan gagal."""
    st = sesi.scalar(select(Staging).where(Staging.site_id == site_id))
    if st is not None:
        st.status = StatusStaging.siap if st.ditarik_pada else StatusStaging.gagal
        st.galat = pesan
        sesi.commit()
    return SiteError(STAGING_DITOLAK, pesan)


def akhiri_gagal(sesi, job, site_id, klien, galat: Exception | None = None) -> SiteError | None:
    """Penanganan kegagalan FINAL dorong/kembalikan (putusan F9a). Tidak pernah melempar.

    - Area dorong di produksi dibersihkan upaya-terbaik dengan tenggat pendek.
    - Belum pernah tukar, atau pemulihan terkonfirmasi: produksi tidak
      berubah, snapshotnya dibuang.
    - Tukar sudah dikirim dan hasilnya tidak tuntas: snapshot DIPERTAHANKAN,
      `dorong_gagal_pada` diisi, dan galat pengganti berpesan tetap
      dikembalikan untuk dilempar pemanggil.
    """
    k = umum.kemajuan(job)
    if isinstance(galat, GalatLamaSementara) and "tahap_dorong" not in k:
        # Percobaan habis saat menuntaskan dorongan lama: dorongan baru belum
        # dimulai (belum ada dorong_id, unggahan, atau snapshot).
        return _staging_utuh(sesi, site_id, PESAN_LAMA_AKHIR)
    langkah = k.get("langkah_terapkan")
    pulih = bool(k.get("pulih_terkonfirmasi"))
    dorong_id = k.get("dorong_id")
    perlu_bersih = isinstance(dorong_id, str) and k.get("unggah_mulai") and not k.get("produksi_bersih")
    if perlu_bersih and bersihkan(klien, dorong_id, tenggat=TENGGAT_BERSIHKAN_AKHIR):
        _tandai_bersih(sesi, job)
    shutil.rmtree(umum.dir_site(site_id) / "dorong", ignore_errors=True)
    if langkah not in LANGKAH_SESUDAH_TUKAR or pulih:
        _buang_snapshot(sesi, site_id, job.id)
        pesan_tolak = PESAN_TOLAK_PRA.get(k.get("tolak_pra_tukar")) if langkah == "cek_ulang" else None
        if pesan_tolak is None:
            return None
        # Penolakan pra-pemeriksaan tukar (mis. pemeliharaan lain yang tidak
        # kunjung usai): produksi dan staging utuh, jadi staging tidak
        # dibiarkan berstatus gagal dan pesannya pesan final "coba lagi nanti".
        return _staging_utuh(sesi, site_id, pesan_tolak)
    pesan = PESAN_AKHIR.get(langkah)
    if pesan is None:
        return None
    st = sesi.scalar(select(Staging).where(Staging.site_id == site_id))
    if st is not None:
        st.dorong_gagal_pada = umum.sekarang()
        st.galat = pesan
        sesi.commit()
    return SiteError(STAGING_GAGAL, pesan)


def _setelah_gagal(sesi, job, site_id, klien, galat: Exception) -> SiteError | None:
    try:
        sesi.rollback()
        status = sesi.scalar(select(Staging.status).where(Staging.site_id == site_id))
        if status == StatusStaging.mendorong:
            # Pembungkus menandai "dilanjutkan otomatis": area dorong di
            # produksi dan snapshot masih dibutuhkan putaran berikutnya.
            return None
        sesi.refresh(job)
        return akhiri_gagal(sesi, job, site_id, klien, galat)
    except Exception:
        log.exception("Pembersihan sesudah dorong gagal (job %s) tidak tuntas", job.id)
        return None


def _periksa_awal(sesi, job) -> None:
    """Penolakan sebelum status staging disentuh pembungkus (lihat uji._periksa_awal).

    Staging yang dijeda tetap berstatus `dijeda`; penolakan di dalam
    pembungkus akan mengubahnya menjadi `siap`. Dorong yang sudah berjalan
    (kemajuan ada) tidak pernah ditolak di sini: ia harus dituntaskan.
    """
    if (job.payload or {}).get("mode") not in MODE:
        raise umum.galat_ditolak(PESAN_MODE)
    if "tahap_dorong" in umum.kemajuan(job):
        return
    st = sesi.scalar(select(Staging).where(Staging.site_id == job.site_id))
    if st is None:
        return
    if st.ditarik_pada is None or not st.tanda_air:
        raise umum.galat_ditolak(PESAN_BELUM_TARIK)
    if not st.aktif:
        raise umum.galat_ditolak(PESAN_DIJEDA)


def tangani_staging_dorong(sesi, job, klien) -> dict:
    def inti(sesi, job, site, staging):
        return dorong(sesi, job, site, staging, klien, umum.buat_pembantu())

    _periksa_awal(sesi, job)
    site_id = job.site_id
    try:
        return umum.jalankan_staging(sesi, job, inti, StatusStaging.mendorong, "Dorong ke produksi",
                                     boleh_batal=boleh_batal)
    except umum.KlaimHilang:
        raise
    except Exception as exc:
        pengganti = _setelah_gagal(sesi, job, site_id, klien, exc)
        if pengganti is not None:
            raise pengganti from exc
        raise

