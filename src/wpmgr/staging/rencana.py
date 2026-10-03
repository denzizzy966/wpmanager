"""Logika murni staging: selisih manifest, potongan, rencana dorong,
tanda air, pemeriksaan sumber daya, dan penguraian balasan connector
(halaman manifest, paket berkas, rentang, potongan tabel). Tanpa I/O, supaya
bisa diuji tuntas dan tetap sama di tarik, dorong, dan kembalikan.
"""

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from wpmgr.staging.aman import (
    DILINDUNGI_STAGING,
    POLA_SHA256,
    PathTidakAman,
    angka,
    boleh_didorong,
    dikecualikan,
    path_sah,
)
from wpmgr.staging.paket import MAKS_ISI

UKURAN_PAKET = MAKS_ISI
MAKS_BERKAS_PAKET = 500
MAKS_UKURAN = 2**50
MAKS_MTIME = 2**40
RAM_MINIMUM = 2 * 1024**3
SISA_DISK_MINIMUM = 0.15
AWALAN_KODE = ("wp-content/themes/", "wp-content/plugins/", "wp-content/mu-plugins/")
AWALAN_UPLOADS = "wp-content/uploads/"
# Batas jumlah halaman manifest per tarik. Connector boleh mengembalikan
# halaman kosong (lagi:true) bila tenggatnya habis saat melewati entri
# anomali, jadi batas ini yang menjamin paging tidak berjalan selamanya.
MAKS_HALAMAN_MANIFEST = 2000
# Kursor tabel dari connector: base64 dari JSON (WPMGR_Staging_Tabel::MAKS_KURSOR).
POLA_KURSOR_TABEL = re.compile(r"[A-Za-z0-9+/=]{1,8192}")


class BalasanTidakSah(ValueError):
    """Balasan connector tidak sesuai kontrak; pemanggil memetakannya ke BAD_RESPONSE."""


@dataclass(frozen=True)
class Entri:
    path: str
    ukuran: int
    mtime: int
    hash: str | None


def _bulat(nilai, atas: int) -> int | None:
    if isinstance(nilai, bool) or not isinstance(nilai, int) or not 0 <= nilai <= atas:
        return None
    return nilai


def entri_dari(item) -> Entri | None:
    """Satu entri manifest dari connector, atau None bila ada yang tidak beres."""
    if not isinstance(item, dict):
        return None
    try:
        path = path_sah(item.get("path"))
    except PathTidakAman:
        return None
    if dikecualikan(path):
        return None
    ukuran = _bulat(item.get("ukuran"), MAKS_UKURAN)
    mtime = item.get("mtime")
    if isinstance(mtime, int) and not isinstance(mtime, bool) and mtime < 0:
        # Berkas bermtime sebelum 1970 (arsip lama, jam server salah) sah di
        # produksi; dijepit ke 0 alih-alih dibuang dari manifest.
        mtime = 0
    mtime = _bulat(mtime, MAKS_MTIME)
    h = item.get("hash")
    if ukuran is None or mtime is None:
        return None
    if h is not None and (not isinstance(h, str) or not POLA_SHA256.fullmatch(h)):
        return None
    return Entri(path, ukuran, mtime, h)


def berubah(a: Entri, b: Entri) -> bool:
    if a.hash and b.hash:
        return a.hash != b.hash
    return a.ukuran != b.ukuran or a.mtime != b.mtime


@dataclass
class Selisih:
    baru: list[Entri] = field(default_factory=list)
    berubah: list[Entri] = field(default_factory=list)
    hapus: list[str] = field(default_factory=list)

    @property
    def diambil(self) -> list[Entri]:
        return self.baru + self.berubah

    @property
    def byte(self) -> int:
        return sum(x.ukuran for x in self.diambil)


def selisih(produksi: dict[str, Entri], lokal: dict[str, Entri],
            dilindungi: frozenset[str] = DILINDUNGI_STAGING) -> Selisih:
    """`dilindungi`: berkas milik tujuan salinan (staging atau hosting) yang tidak pernah ditimpa/dihapus tarik."""
    hasil = Selisih()
    for path in sorted(produksi):
        if path in dilindungi:
            # Berkas milik tujuan sendiri tidak pernah ditimpa oleh tarik,
            # sekalipun produksi punya berkas bernama sama.
            continue
        e = produksi[path]
        if path not in lokal:
            hasil.baru.append(e)
        elif berubah(e, lokal[path]):
            hasil.berubah.append(e)
    hasil.hapus = sorted(p for p in lokal if p not in produksi and p not in dilindungi)
    return hasil


@dataclass(frozen=True)
class Potongan:
    jenis: str
    berkas: tuple[Entri, ...]
    dari: int = 0
    panjang: int = 0

    @property
    def ukuran(self) -> int:
        return self.panjang if self.jenis == "rentang" else sum(e.ukuran for e in self.berkas)


def bagi_potongan(entri: list[Entri], ukuran_paket: int = UKURAN_PAKET,
                  maks_berkas: int = MAKS_BERKAS_PAKET) -> list[Potongan]:
    """Kelompokkan berkas kecil ke paket; berkas besar menjadi potongan "rentang".

    Rentang di sini hanya RENCANA menurut ukuran manifest, untuk menghitung
    jumlah potongan dan byte. Pengambilan berkas besar yang sebenarnya
    memakai `RakitRentang` (rentang dinamis: maju sebesar bagian yang benar-
    benar dikembalikan, ukuran berkas bisa berubah sejak manifest), mulai
    dari potongan rentang pertama (`dari == 0`); potongan rentang lain untuk
    berkas yang sama dilewati pemanggil. Untuk mengemas berkas kecil,
    potongan "paket" dipakai apa adanya.
    """
    hasil: list[Potongan] = []
    kumpulan: list[Entri] = []
    total = 0
    for e in entri:
        if e.ukuran > ukuran_paket:
            for dari in range(0, e.ukuran, ukuran_paket):
                hasil.append(Potongan("rentang", (e,), dari, min(ukuran_paket, e.ukuran - dari)))
            continue
        if kumpulan and (total + e.ukuran > ukuran_paket or len(kumpulan) >= maks_berkas):
            hasil.append(Potongan("paket", tuple(kumpulan)))
            kumpulan, total = [], 0
        kumpulan.append(e)
        total += e.ukuran
    if kumpulan:
        hasil.append(Potongan("paket", tuple(kumpulan)))
    return hasil


@dataclass
class RencanaDorong:
    ganti: list[Entri]
    hapus: list[str]
    db: bool

    @property
    def byte(self) -> int:
        return sum(e.ukuran for e in self.ganti)


def rencana_dorong(mode: str, staging: dict[str, Entri], produksi: dict[str, Entri]) -> RencanaDorong:
    """Berkas yang ditulis/dihapus di produksi (spec §6.3 langkah 1, Koreksi #14)."""
    if mode not in ("hanya_kode", "timpa_penuh"):
        raise ValueError("Mode dorong tidak dikenal")

    def beda(p: str) -> bool:
        return p not in produksi or berubah(staging[p], produksi[p])

    if mode == "hanya_kode":
        ganti = [staging[p] for p in sorted(staging) if boleh_didorong(p) and (
            (p.startswith(AWALAN_KODE) and beda(p)) or (p.startswith(AWALAN_UPLOADS) and p not in produksi))]
        hapus = [p for p in sorted(produksi) if boleh_didorong(p) and p.startswith(AWALAN_KODE) and p not in staging]
        return RencanaDorong(ganti, hapus, False)
    ganti = [staging[p] for p in sorted(staging) if boleh_didorong(p) and beda(p)]
    hapus = [p for p in sorted(produksi)
             if boleh_didorong(p) and p not in staging and not p.startswith(AWALAN_UPLOADS)]
    return RencanaDorong(ganti, hapus, True)


# ---- penguraian balasan connector -------------------------------------------


@dataclass
class HalamanManifest:
    entri: list[Entri]
    dilewati: int
    kursor: str | None
    lagi: bool
    info: dict | None


MAKS_BATAS_UNGGAH = 4 * 1024 * 1024


def _info_manifest(info) -> dict | None:
    """Hanya bendera yang dipakai dashboard, dengan tipe yang dijamin.

    Teks lain dari connector (home, siteurl, versi, dst.) sengaja tidak
    diteruskan dari sini supaya tidak ada teks penyerang yang sampai ke
    penyimpanan tanpa dibersihkan.
    """
    if not isinstance(info, dict):
        return None
    return {
        "multisite": bool(info.get("multisite")),
        "konten_di_luar": bool(info.get("konten_di_luar")),
        "unggah_terlalu_kecil": bool(info.get("unggah_terlalu_kecil")),
        # None bila tidak ada/rusak; connector selalu mengirim 1..4 MiB.
        "batas_unggah": angka(info.get("batas_unggah"), 1, MAKS_BATAS_UNGGAH),
    }


def halaman_manifest(data, kursor_masuk: str | None, halaman: int | None = None) -> HalamanManifest:
    """Satu halaman /staging/manifest yang sudah diperiksa.

    Halaman boleh kosong dengan `lagi: true` (connector berhenti di tenggat
    sesudah hanya melewati entri anomali); yang tidak boleh adalah kursor
    yang tidak maju. `info` hanya dibaca dari halaman pertama, dan hanya
    bendera `multisite`, `konten_di_luar`, `unggah_terlalu_kecil`, dan
    `batas_unggah`.

    `halaman` adalah nomor halaman ini (mulai 1). Bila diberikan, halaman
    ke-MAKS_HALAMAN_MANIFEST yang masih `lagi: true` ditolak, sehingga paging
    tidak berjalan selamanya. Pemanggil yang tidak mengirim `halaman` wajib
    menghitung sendiri.
    """
    if not isinstance(data, dict) or not isinstance(data.get("berkas"), list):
        raise BalasanTidakSah("Halaman manifest tidak sah.")
    lagi = data.get("lagi")
    if not isinstance(lagi, bool):
        raise BalasanTidakSah("Penanda lanjutan manifest tidak sah.")
    entri, dilewati = [], angka(data.get("jumlah_dilewati"), 0, 10**9) or 0
    for item in data["berkas"]:
        e = entri_dari(item)
        if e is None:
            dilewati += 1
        else:
            entri.append(e)
    kursor = None
    if lagi:
        kursor = data.get("kursor")
        try:
            path_sah(kursor)
        except PathTidakAman:
            raise BalasanTidakSah("Kursor manifest dari produksi tidak sah.") from None
        if kursor == kursor_masuk:
            raise BalasanTidakSah("Manifest produksi tidak maju; periksa connector di site.")
        if halaman is not None and halaman >= MAKS_HALAMAN_MANIFEST:
            raise BalasanTidakSah("Manifest produksi terlalu panjang.")
    info = _info_manifest(data.get("info")) if kursor_masuk is None else None
    return HalamanManifest(entri, dilewati, kursor, lagi, info)


@dataclass
class HasilPaket:
    isi: list[tuple[str, bytes, int]] = field(default_factory=list)
    hilang: list[str] = field(default_factory=list)
    gagal_baca: list[str] = field(default_factory=list)
    terlalu_besar: list[str] = field(default_factory=list)
    # Path yang diminta tetapi belum dikirim (lengkap:false); minta lagi.
    sisa: list[str] = field(default_factory=list)


def hasil_paket(diminta: list[str], meta: dict, bagian: list[bytes]) -> HasilPaket:
    """Balasan /staging/file mode berkas, dicocokkan dengan urutan permintaan.

    Connector mengirim awalan daftar yang diminta (paling sedikit satu entri)
    dan `lengkap:false` bila berhenti dini. Per entri ada penanda:
    `terlalu_besar` (ambil lewat rentang), `hilang` (hapus di staging), atau
    `galat` (tidak terbaca; lewati dengan peringatan).
    """
    berkas = meta.get("berkas") if isinstance(meta, dict) else None
    lengkap = meta.get("lengkap") if isinstance(meta, dict) else None
    if not isinstance(berkas, list) or not isinstance(lengkap, bool) or len(berkas) != len(bagian) \
            or not 1 <= len(berkas) <= len(diminta) or (lengkap and len(berkas) != len(diminta)):
        raise BalasanTidakSah("Paket berkas tidak sesuai permintaan.")
    hasil = HasilPaket(sisa=list(diminta[len(berkas):]))
    for path, m, isi in zip(diminta, berkas, bagian):
        if not isinstance(m, dict) or m.get("path") != path:
            raise BalasanTidakSah("Paket berkas tidak sesuai permintaan.")
        penanda = m.get("terlalu_besar") is True or m.get("hilang") is True or "galat" in m
        if penanda and isi:
            raise BalasanTidakSah("Entri penanda paket berkas membawa isi.")
        if m.get("terlalu_besar") is True:
            hasil.terlalu_besar.append(path)
        elif m.get("hilang") is True:
            hasil.hilang.append(path)
        elif "galat" in m:
            hasil.gagal_baca.append(path)
        else:
            mtime = _bulat(m.get("mtime"), MAKS_MTIME)
            if mtime is None:
                raise BalasanTidakSah("Waktu ubah berkas dalam paket tidak sah.")
            hasil.isi.append((path, isi, mtime))
    return hasil


LANJUT, SELESAI, HILANG, BERUBAH = "lanjut", "selesai", "hilang", "berubah"


class RakitRentang:
    """Keadaan pengambilan satu berkas besar lewat /staging/file mode rentang.

    Posisi maju sebesar bagian yang benar-benar dikembalikan (connector boleh
    mengembalikan kurang dari `panjang`). `total`/`mtime` diambil connector
    SEBELUM setiap pembacaan, jadi penulisan di tengah berkas dengan ukuran
    dan detik mtime yang sama tidak terlihat dari keduanya; `verifikasi()`
    membandingkan hash seluruh berkas dengan manifest di akhir.
    """

    def __init__(self, entri: Entri, panjang: int = UKURAN_PAKET) -> None:
        if panjang < 1:
            raise ValueError("Panjang rentang tidak sah")
        self.entri = entri
        self.panjang = panjang
        self.dari = 0
        self.total: int | None = None
        self.mtime: int | None = None
        self._h = hashlib.sha256()

    def total_maks(self) -> int:
        """Ukuran terbesar yang diterima: manifest + 10% + 8 MiB (berkas yang tumbuh wajar)."""
        return self.entri.ukuran + self.entri.ukuran // 10 + UKURAN_PAKET

    def permintaan(self) -> tuple[str, int, int]:
        return self.entri.path, self.dari, self.panjang

    def terima(self, meta, isi: bytes) -> str:
        """Catat satu balasan; kembalikan LANJUT, SELESAI, HILANG, atau BERUBAH.

        Pemanggil menulis `isi` hanya bila hasilnya LANJUT atau SELESAI, dan
        membuang tulisan sementaranya bila BERUBAH (ulangi dari awal).
        """
        if not isinstance(meta, dict) or meta.get("path") != self.entri.path:
            raise BalasanTidakSah("Rentang berkas tidak sesuai permintaan.")
        if meta.get("hilang") is True:
            return HILANG
        total, mtime = _bulat(meta.get("total"), MAKS_UKURAN), _bulat(meta.get("mtime"), MAKS_MTIME)
        if meta.get("dari") != self.dari or total is None or mtime is None \
                or len(isi) > self.panjang or self.dari + len(isi) > total:
            raise BalasanTidakSah("Rentang berkas tidak sesuai permintaan.")
        if total > self.total_maks():
            # Berkas (menurut connector) tumbuh jauh melebihi manifest. Tidak
            # diikuti: bisa jadi connector yang disusupi mencoba memenuhi disk
            # dashboard. BERUBAH (bukan BalasanTidakSah) karena berkas yang
            # sungguh tumbuh juga sah; pemanggil mengulang dari awal lalu
            # melewatinya dengan peringatan, dan tarik berikutnya membawa
            # ukuran baru dari manifest.
            return BERUBAH
        if self.total is None:
            self.total, self.mtime = total, mtime
        elif (total, mtime) != (self.total, self.mtime):
            return BERUBAH
        if not isi and self.dari < total:
            # Berkas menyusut di antara filesize() dan pembacaan di connector.
            return BERUBAH
        self._h.update(isi)
        self.dari += len(isi)
        return SELESAI if self.dari >= total else LANJUT

    def hash(self) -> str:
        return self._h.hexdigest()

    def verifikasi(self) -> bool | None:
        """True cocok, False tidak cocok (ulangi), None tidak bisa diperiksa.

        Hash manifest hanya berlaku bila berkasnya belum berubah sejak
        manifest (ukuran dan mtime sama) dan manifest memang membawa hash
        (hanya berkas <= 50 MB).
        """
        if self.entri.hash is None or (self.total, self.mtime) != (self.entri.ukuran, self.entri.mtime):
            return None
        return hmac.compare_digest(self.hash(), self.entri.hash)


@dataclass(frozen=True)
class PotonganTabel:
    sql: bytes
    selesai: bool
    kursor: str | None
    baris: int
    # "pk" atau "offset"; offset berarti tabel tanpa primary key, yang bisa
    # melewatkan/menggandakan baris bila ditulisi selama tarik.
    mode: str


def potongan_tabel(nama: str, kursor_masuk: str | None, meta, bagian: list[bytes]) -> PotonganTabel:
    """Satu potongan /staging/tabel yang sudah diperiksa."""
    if not isinstance(meta, dict) or meta.get("tabel") != nama or len(bagian) != 1:
        raise BalasanTidakSah(f"Potongan tabel {nama} tidak sah.")
    selesai, kursor, mode = meta.get("selesai"), meta.get("kursor"), meta.get("mode")
    baris = _bulat(meta.get("baris"), 10**9)
    if not isinstance(selesai, bool) or baris is None or mode not in ("pk", "offset"):
        raise BalasanTidakSah(f"Potongan tabel {nama} tidak sah.")
    if selesai:
        kursor = None
    elif not isinstance(kursor, str) or not POLA_KURSOR_TABEL.fullmatch(kursor) or kursor == kursor_masuk:
        raise BalasanTidakSah(f"Kursor potongan tabel {nama} tidak sah atau tidak maju.")
    return PotonganTabel(bagian[0], selesai, kursor, baris, mode)


# ---- tanda air --------------------------------------------------------------

# Kunci persis seperti WPMGR_Staging_TandaAir::kumpulkan(): pesanan HPOS dan
# pesanan di tabel posts dilaporkan terpisah, karena site yang kembali dari
# HPOS ke posts menulis pesanan baru hanya ke salah satunya.
LABEL_SUMBER = {
    "pesanan_hpos": "pesanan (HPOS)", "pesanan_posts": "pesanan (tabel posts)",
    "comments": "komentar", "users": "user",
    "gravity_forms": "isian Gravity Forms", "wpforms": "isian WPForms",
    "fluent_forms": "isian Fluent Forms", "flamingo": "pesan Contact Form 7 (Flamingo)", "posts": "post",
}
URUTAN_SUMBER = ("pesanan_hpos", "pesanan_posts", "comments", "users", "gravity_forms", "wpforms",
                 "fluent_forms", "flamingo", "posts")
_MAKS_ANGKA = 2**62
_FORMAT_DIUBAH = "%Y-%m-%d %H:%M:%S"
_POLA_DIUBAH = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")


def urai_tanda_air(data) -> dict | None:
    """Tanda air dari connector dalam bentuk yang dijamin: hanya sumber dikenal, angka sah."""
    if not isinstance(data, dict) or not isinstance(data.get("sumber"), dict):
        return None
    sumber = {}
    for kunci, nilai in data["sumber"].items():
        if kunci not in LABEL_SUMBER or not isinstance(nilai, dict):
            continue
        maks_id = _bulat(nilai.get("maks_id"), _MAKS_ANGKA)
        jumlah = _bulat(nilai.get("jumlah"), _MAKS_ANGKA)
        if maks_id is None or jumlah is None:
            continue
        bersih = {"maks_id": maks_id, "jumlah": jumlah}
        if kunci == "posts":
            diubah = nilai.get("diubah")
            # Hanya format post_modified_gmt; teks lain dari connector tidak
            # disimpan (nilai ini juga dikirim balik sebagai posts_sejak).
            bersih["diubah"] = diubah if isinstance(diubah, str) and _POLA_DIUBAH.fullmatch(diubah) else ""
            # None berarti "tidak dihitung" (tanpa posts_sejak), bukan nol.
            bersih["diubah_sejak"] = _bulat(nilai.get("diubah_sejak"), _MAKS_ANGKA)
        sumber[kunci] = bersih
    return {"sumber": sumber}


def _waktu_gmt(teks) -> datetime | None:
    """`post_modified_gmt` tanpa zona: waktu UTC. None bila kosong/rusak."""
    if not isinstance(teks, str):
        return None
    try:
        return datetime.strptime(teks, _FORMAT_DIUBAH).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _post_diubah(lama: dict, baru: dict) -> str | None:
    sejak = baru.get("diubah_sejak")
    if sejak is not None:
        return f"{sejak} post diubah" if sejak > 0 else None
    w_lama, w_baru = _waktu_gmt(lama.get("diubah")), _waktu_gmt(baru.get("diubah"))
    if w_lama is None or w_baru is None:
        return "perubahan post tidak bisa dipastikan"
    if w_baru > w_lama:
        return "ada post yang diubah; jumlahnya tidak bisa dipastikan"
    return None


def bandingkan_tanda_air(lama: dict | None, baru: dict | None) -> list[str]:
    """Daftar data baru/berubah di produksi sejak tarik, dalam kalimat (spec §8.2)."""
    if not lama or not isinstance(lama.get("sumber"), dict):
        return ["Tanda air saat tarik tidak tersedia; data baru di produksi tidak dapat diperiksa."]
    if not isinstance(baru, dict) or not isinstance(baru.get("sumber"), dict):
        # Mis. urai_tanda_air() mengembalikan None untuk balasan rusak.
        return ["Tanda air produksi saat ini tidak tersedia; data baru di produksi tidak bisa dipastikan."]
    ls, bs = lama["sumber"], baru["sumber"]
    hasil = []
    for kunci in URUTAN_SUMBER:
        label = LABEL_SUMBER[kunci]
        l, b = ls.get(kunci), bs.get(kunci)
        if b is None:
            if l is not None:
                # Tabel dihapus atau (flamingo) semua isinya dihapus.
                hasil.append(f"{label} tidak ada lagi di produksi (sebelumnya {l['jumlah']} entri)")
            continue
        if l is None:
            # Sumber yang muncul sesudah tarik (plugin form baru, HPOS baru
            # dinyalakan) adalah data yang tidak ada di staging.
            hasil.append(f"sumber baru: {label} ({b['jumlah']} entri)")
            continue
        if b["maks_id"] > l["maks_id"]:
            hasil.append(f"{max(1, b['jumlah'] - l['jumlah'])} {label} baru")
        elif (b["maks_id"], b["jumlah"]) != (l["maks_id"], l["jumlah"]):
            # Penghapusan juga perubahan yang akan hilang bila staging didorong.
            hasil.append(f"{label} berubah ({l['jumlah']} menjadi {b['jumlah']} entri)")
        if kunci == "posts":
            pesan = _post_diubah(l, b)
            if pesan:
                hasil.append(pesan)
    return hasil


# ---- sumber daya ------------------------------------------------------------


def format_byte(n: int) -> str:
    n = max(0, int(n))
    if n < 1024:
        return f"{n} B"
    nilai = float(n)
    for satuan in ("KB", "MB", "GB", "TB"):
        nilai /= 1024
        if nilai < 1024 or satuan == "TB":
            return f"{nilai:.1f} {satuan}".replace(".", ",")
    return f"{n} B"


def cek_ram(status) -> str | None:
    if status.mem_tersedia < RAM_MINIMUM:
        return (f"RAM tersedia di VPS {format_byte(status.mem_tersedia)}; "
                f"minimal {format_byte(RAM_MINIMUM)} untuk menjalankan staging.")
    return None


def cek_disk(status, tambahan: int) -> str | None:
    total = max(1, status.disk_total)
    sisa = status.disk_bebas - max(0, tambahan)
    if sisa / total < SISA_DISK_MINIMUM:
        return (f"Sisa disk sesudah tarik akan {format_byte(max(0, sisa))} "
                f"({int(max(0, sisa) * 100 // total)}% dari {format_byte(total)}); minimal 15%.")
    return None


def cek_maks_aktif(jumlah_aktif: int, maks: int) -> str | None:
    if jumlah_aktif >= maks:
        return f"Sudah ada {jumlah_aktif} staging aktif (batas {maks}). Jeda salah satu dulu."
    return None
