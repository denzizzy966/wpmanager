"""SQL ekspor tabel dari connector sebelum diimpor ke MariaDB staging.

Potongan SQL adalah masukan penyerang sampai 8 MB. Karena itu setiap pola di
sini linear: alternatif teks berkutip dan komentar yang tidak ditutup selalu
menelan sisa masukan (tidak pernah gagal lalu dicoba ulang dari posisi
berikutnya), sehingga tidak ada pemindaian ulang kuadratik (putusan C1).

- `periksa_sql`: pertahanan berlapis terhadap SQL yang membaca/menulis
  berkas (LOAD DATA/XML, INTO OUTFILE/DUMPFILE, LOAD_FILE) dan terhadap apa
  pun yang tidak pernah dihasilkan eksportir connector kita sendiri. Lapis
  utamanya ada di skrip pembantu: klien dan server `--local-infile=0`, user
  staging tanpa hak FILE.
- `sesuaikan_mariadb`: kolasi dan komentar versi khas MySQL 8 (putusan R5).
"""

import re

# ---- periksa ----------------------------------------------------------------

# Bagian yang dilompati (diganti satu spasi): teks berkutip, identifier
# backtick, komentar biasa, komentar baris. Komentar eksekusi (/*! dan /*M!)
# TIDAK dilompati: MariaDB menjalankan isinya, jadi isinya ikut diperiksa.
# Dua alternatif terakhir (`putus`) menelan sisa masukan bila teks atau
# komentar tidak ditutup.
_LEWATI = re.compile(
    rb"'[^'\\]*(?:\\.[^'\\]*)*'"
    rb'|"[^"\\]*(?:\\.[^"\\]*)*"'
    rb"|`[^`]*`"
    rb"|/\*(?!M?!)[^*]*\*+(?:[^/*][^*]*\*+)*/"
    rb"|--(?:[ \t\r\f\v][^\n]*|(?=\n)|\Z)"
    rb"|#[^\n]*"
    rb"|['\"`].*|/\*(?!M?!).*",
    re.DOTALL,
)
# Penanda di belakang masukan: hanya bertahan bila tidak ada teks/komentar
# yang tidak ditutup (alternatif `putus` menelannya). Penggantian memakai
# bytes literal, bukan template grup: ekspansi template per kecocokan
# berjalan di Python dan beberapa kali lebih lambat untuk jutaan token.
_PENANDA = b"\n\x01wpmgr-akhir\x01"
_TERLARANG = re.compile(rb"\b(?:LOAD\s+(?:DATA|XML)|INTO\s+(?:OUTFILE|DUMPFILE)|LOAD_FILE)\b", re.IGNORECASE)
# Komentar eksekusi yang memuat ";" (klien mariadb bisa memecah pernyataan di
# dalamnya), membuka komentar lain, atau tidak ditutup. Linear: setiap
# percobaan berhenti di "*/", ";", "/*", atau akhir, jadi rentang yang
# dipindai dari dua pembuka berbeda tidak pernah bertumpuk.
_EKSEKUSI_BURUK = re.compile(rb"/\*M?!(?:[^*;/]|\*(?!/)|/(?!\*))*(?:;|/\*|\Z)")
# Eksportir connector (WPMGR_Staging_Tabel::ekspor) hanya menghasilkan tiga
# jenis pernyataan ini, masing-masing diakhiri ";".
_PERNYATAAN = re.compile(rb"\s*(?:DROP\s+TABLE|CREATE\s+TABLE|INSERT\s+INTO)\b[^;]*;", re.IGNORECASE)


def periksa_sql(sql: bytes) -> str | None:
    """Alasan (teks tetap) bila potongan SQL ditolak, atau None bila boleh diimpor.

    Setiap potongan harus berupa pernyataan utuh (tidak ada teks/komentar
    yang menyeberang ke potongan berikutnya), karena berkas potongan
    digabung berurutan saat impor dan diperiksa satu per satu di sini.
    """
    ringkas = _LEWATI.sub(b" ", sql + _PENANDA)
    if not ringkas.endswith(_PENANDA):
        return "teks atau komentar tidak ditutup"
    ringkas = ringkas[:-len(_PENANDA)]
    if b"\\" in ringkas:
        return "garis miring terbalik di luar teks (perintah klien)"
    if _TERLARANG.search(ringkas):
        return "memuat perintah baca/tulis berkas (LOAD DATA, INTO OUTFILE, LOAD_FILE)"
    if _EKSEKUSI_BURUK.search(ringkas):
        return "komentar versi tidak sah"
    posisi = 0
    while True:
        m = _PERNYATAAN.match(ringkas, posisi)
        if m is None:
            break
        posisi = m.end()
    if ringkas[posisi:].strip():
        return "memuat pernyataan selain DROP TABLE, CREATE TABLE, dan INSERT INTO"
    return None


# ---- sesuaikan (R5) ------------------------------------------------------------

# Setiap alternatif selalu berhasil secara serakah (menelan sampai penutup
# atau akhir), jadi finditer tidak pernah memindai ulang.
_TOKEN_SQL = re.compile(
    rb"'[^'\\]*(?:\\.?[^'\\]*)*(?:'|\Z)"
    rb'|"[^"\\]*(?:\\.?[^"\\]*)*(?:"|\Z)'
    rb"|`[^`]*(?:`|\Z)"
    rb"|/\*!(?P<versi>[0-9]{5,6})(?:[^*]|\*(?!/))*(?:\*/|\Z)"
    rb"|/\*(?:[^*]|\*(?!/))*(?:\*/|\Z)"
    rb"|;",
    re.DOTALL,
)
_POLA_CREATE = re.compile(rb"\s*CREATE\s+TABLE\b", re.IGNORECASE)
_KOLASI_MYSQL8 = re.compile(rb"\butf8mb4_[a-z0-9_]*0900_[a-z0-9_]+")
# Susunan potongan pertama dari connector: "DROP TABLE IF EXISTS `t`;\n",
# lalu keluaran SHOW CREATE TABLE, lalu INSERT (WPMGR_Staging_Tabel::ekspor).
# CREATE TABLE selalu berada di awal potongan; hanya awal ini yang dipindai
# token demi token di Python, jadi biayanya terbatas apa pun isi sisanya.
BATAS_CREATE = 256 * 1024


def _ganti_kolasi(m: re.Match) -> bytes:
    return b"utf8mb4_bin" if m.group(0).endswith(b"_bin") else b"utf8mb4_unicode_520_ci"


def _sesuaikan_create(pernyataan: bytes) -> bytes:
    hasil, posisi = [], 0
    for m in _TOKEN_SQL.finditer(pernyataan):
        hasil.append(_KOLASI_MYSQL8.sub(_ganti_kolasi, pernyataan[posisi:m.start()]))
        versi = m.group("versi")
        # /*!8xxxx ... */ hanya dimengerti MySQL 8/9, tetapi MariaDB 11
        # (nomor versi 11xxxx) ikut mengeksekusinya lalu gagal: dibuang.
        # Komentar versi lama (/*!50100 ...) sah di MariaDB dan dibiarkan.
        hasil.append(b" " if versi is not None and 80000 <= int(versi) < 100000 else m.group(0))
        posisi = m.end()
    hasil.append(_KOLASI_MYSQL8.sub(_ganti_kolasi, pernyataan[posisi:]))
    return b"".join(hasil)


def sesuaikan_mariadb(sql: bytes) -> bytes:
    """Potongan tabel pertama yang bisa diimpor MariaDB 11.4 (putusan R5).

    Hanya pernyataan CREATE TABLE di BATAS_CREATE byte pertama yang diubah:
    kolasi `utf8mb4_0900_*` diterjemahkan dan komentar versi MySQL 8 dibuang.
    Baris data (INSERT) tidak pernah disentuh. CREATE yang tidak ditemukan
    di batas itu dibiarkan; impornya lalu gagal dengan galat MariaDB yang jelas.
    """
    awal = 0
    for m in _TOKEN_SQL.finditer(sql, 0, BATAS_CREATE):
        if m.group(0) != b";":
            continue
        pernyataan = sql[awal:m.end()]
        if _POLA_CREATE.match(pernyataan):
            return sql[:awal] + _sesuaikan_create(pernyataan) + sql[m.end():]
        awal = m.end()
    return sql


# ---- pra-pemeriksaan dorong (R8, Task 16) --------------------------------------
#
# Connector menerapkan SQL dorong (dan kelak SQL snapshot saat Kembalikan)
# lewat WPMGR_Staging_Sql::ubah(), yang menolak SELURUH pernyataan bila memuat
# komentar di luar literal (kecuali komentar di awal pernyataan, yang dibuang
# tanpa_komentar_awal()) dan menolak CREATE TABLE ber-PARTITION. Bila baru
# ketahuan di tengah impor, dorongan gagal sesudah snapshot dan unggahan
# selesai; bila ada di struktur tabel produksi, snapshot database tidak akan
# pernah bisa dipulihkan. Karena itu keduanya diperiksa di dashboard sebelum
# apa pun diunggah.
#
# Pemindaian linear dan bertahap (berkas SQL bisa bergigabyte): `_RUNTUN`
# menelan teks biasa dan literal utuh dalam satu pencocokan di mesin regex,
# jadi loop Python hanya berputar per pernyataan, per komentar awal, dan per
# literal yang terpotong di batas bagian. Byte yang belum bisa diputuskan
# ("/" atau "-" di ujung bagian, backslash di ujung literal, "*" di ujung
# komentar) ditahan paling banyak dua byte untuk bagian berikutnya.

ALASAN_KOMENTAR = "komentar SQL di tengah pernyataan"
ALASAN_PARTISI = "klausa PARTITION pada CREATE TABLE"
ALASAN_TERBUKA = "teks, identifier, atau komentar yang tidak ditutup"
ALASAN_CREATE_BESAR = "CREATE TABLE yang terlalu besar untuk diperiksa"

_SPASI = b" \t\r\n\v\f"
# Satu atau lebih token yang pasti bukan awal komentar dan bukan akhir
# pernyataan: teks biasa, literal/identifier yang sudah ditutup, "/" yang
# tidak diikuti "*", dan "-" yang tidak memulai "-- " (aturan ada_komentar()
# connector: "--" hanya komentar bila diikuti spasi). Lookahead mewajibkan
# byte berikutnya ada, sehingga "/" dan "-" di ujung bagian tidak pernah
# diputuskan terlalu dini. Setiap alternatif maju paling sedikit satu byte
# dan pencocokan tidak pernah bisa gagal (nol pengulangan sah), jadi tidak
# ada pemindaian ulang.
_RUNTUN = re.compile(
    rb"(?:[^'\"`#;/\-]+"
    rb"|'[^'\\]*(?:\\.[^'\\]*)*'"
    rb'|"[^"\\]*(?:\\.[^"\\]*)*"'
    rb"|`[^`]*`"
    rb"|/(?=[^*])"
    rb"|-(?=[^-]|-[^ \t\r\n\v\f])"
    rb")*",
    re.DOTALL,
)
_LANJUT_LITERAL = {
    ord("'"): re.compile(rb"[^'\\]*(?:\\.[^'\\]*)*"),
    ord('"'): re.compile(rb'[^"\\]*(?:\\.[^"\\]*)*'),
    ord("`"): re.compile(rb"[^`]*"),
}
_BUKAN_SPASI = re.compile(rb"[^ \t\r\n\v\f]")
_AWAL_CREATE = re.compile(rb"CREATE\b", re.IGNORECASE)
_PARTISI = re.compile(rb"(?<![A-Za-z0-9_$])(?:SUB)?PARTITION(?![A-Za-z0-9_$])", re.IGNORECASE)
# CREATE TABLE hasil SHOW CREATE TABLE hanya beberapa KB; yang lebih besar
# dari ini bukan struktur tabel yang wajar.
MAKS_CREATE = 4 * 1024 * 1024
# Cukup untuk memastikan kata pertama pernyataan ("CREATE" + pemisah).
_PANJANG_KEPALA = 7

_NORMAL, _LITERAL, _BLOK, _BARIS = range(4)


class PemeriksaTerapkan:
    """Pemindai bertahap R8: `tambah()` per bagian, lalu `akhir()`.

    Keduanya mengembalikan alasan penolakan (teks tetap) begitu ditemukan,
    atau None. Sesudah ada alasan, pemanggil berhenti memakai objek ini.
    Hanya teks CREATE TABLE yang ditampung (untuk mencari PARTITION di luar
    literal saat pernyataannya berakhir); isi INSERT tidak pernah ditampung.
    """

    def __init__(self) -> None:
        self._keadaan = _NORMAL
        self._kutip = 0
        self._tahan = b""
        self._awal = True
        self._jenis: str | None = None
        self._kepala = b""
        self._create = bytearray()

    def _isi(self, teks: bytes) -> str | None:
        """Isi pernyataan (bukan komentar awal) yang baru dikonsumsi."""
        if self._awal:
            m = _BUKAN_SPASI.search(teks)
            if m is None:
                return None
            self._awal = False
            teks = teks[m.start():]
        if self._jenis is None:
            self._kepala += teks
            if len(self._kepala) < _PANJANG_KEPALA:
                return None
            self._jenis = "create" if _AWAL_CREATE.match(self._kepala) else "lain"
            teks, self._kepala = self._kepala, b""
        if self._jenis == "create":
            if len(self._create) + len(teks) > MAKS_CREATE:
                return ALASAN_CREATE_BESAR
            self._create.extend(teks)
        return None

    def _akhir_pernyataan(self) -> str | None:
        if self._jenis is None and _AWAL_CREATE.match(self._kepala):
            teks = self._kepala
        else:
            teks = bytes(self._create) if self._jenis == "create" else None
        self._awal, self._jenis, self._kepala, self._create = True, None, b"", bytearray()
        if teks is not None and _PARTISI.search(_LEWATI.sub(b" ", teks)):
            return ALASAN_PARTISI
        return None

    def _pindai(self, buf: bytes, akhir: bool) -> str | None:
        pos, n = 0, len(buf)
        while pos < n:
            if self._keadaan == _LITERAL:
                m = _LANJUT_LITERAL[self._kutip].match(buf, pos)
                if m.end() >= n:
                    return None
                if buf[m.end()] == self._kutip:
                    self._keadaan, pos = _NORMAL, m.end() + 1
                    continue
                # Backslash tunggal di ujung bagian: yang di-escape ada di bagian berikutnya.
                if not akhir:
                    self._tahan = buf[m.end():]
                return None
            if self._keadaan == _BLOK:
                i = buf.find(b"*/", pos)
                if i < 0:
                    if not akhir and buf.endswith(b"*"):
                        self._tahan = b"*"
                    return None
                self._keadaan, pos = _NORMAL, i + 2
                continue
            if self._keadaan == _BARIS:
                i = buf.find(b"\n", pos)
                if i < 0:
                    return None
                self._keadaan, pos = _NORMAL, i + 1
                continue

            m = _RUNTUN.match(buf, pos)
            if m.end() > pos:
                alasan = self._isi(buf[pos:m.end()])
                if alasan is not None:
                    return alasan
                pos = m.end()
                if pos >= n:
                    return None
            c = buf[pos]
            if c == ord(";"):
                alasan = self._akhir_pernyataan()
                if alasan is not None:
                    return alasan
                pos += 1
            elif c in _LANJUT_LITERAL:
                # Literal yang belum ditutup di bagian ini (yang utuh sudah
                # ditelan _RUNTUN). Diwakili literal kosong utuh supaya teks
                # CREATE yang ditampung tetap bisa ditopengi dengan benar.
                alasan = self._isi(b"''")
                if alasan is not None:
                    return alasan
                self._keadaan, self._kutip, pos = _LITERAL, c, pos + 1
            elif c == ord("#"):
                if not self._awal:
                    return ALASAN_KOMENTAR
                self._keadaan, pos = _BARIS, pos + 1
            elif c == ord("/"):
                if pos + 1 < n:
                    # Pasti "/*": "/" lain sudah ditelan _RUNTUN.
                    if not self._awal:
                        return ALASAN_KOMENTAR
                    self._keadaan, pos = _BLOK, pos + 2
                elif akhir:
                    self._isi(b"/")
                    pos += 1
                else:
                    self._tahan = buf[pos:]
                    return None
            else:
                # "-" yang tidak ditelan _RUNTUN: "-- " (komentar), atau "-"/"--"
                # di ujung bagian yang belum bisa diputuskan.
                if pos + 2 < n:
                    if not self._awal:
                        return ALASAN_KOMENTAR
                    self._keadaan, pos = _BARIS, pos + 3
                elif akhir:
                    alasan = self._isi(buf[pos:])
                    if alasan is not None:
                        return alasan
                    pos = n
                else:
                    self._tahan = buf[pos:]
                    return None
        return None

    def tambah(self, data: bytes) -> str | None:
        buf, self._tahan = self._tahan + data, b""
        return self._pindai(buf, akhir=False)

    def akhir(self) -> str | None:
        buf, self._tahan = self._tahan, b""
        alasan = self._pindai(buf, akhir=True)
        if alasan is not None:
            return alasan
        if self._keadaan in (_LITERAL, _BLOK):
            return ALASAN_TERBUKA
        return self._akhir_pernyataan()


def periksa_terapkan(sql: bytes) -> str | None:
    """Alasan (teks tetap) bila SQL ini akan ditolak ubah() connector, atau None."""
    p = PemeriksaTerapkan()
    return p.tambah(sql) or p.akhir()


def periksa_berkas_terapkan(berkas, blok: int = 1 << 20) -> str | None:
    """Seperti `periksa_terapkan` atas gabungan berkas berurutan, dibaca per blok.

    Hanya untuk berkas milik dashboard (area kerja dorong, snapshot); SQL dari
    pohon yang di-bind mount dipindahkan lewat `aman` lebih dulu.
    """
    p = PemeriksaTerapkan()
    for b in berkas:
        with open(b, "rb") as f:
            for bagian in iter(lambda f=f: f.read(blok), b""):
                alasan = p.tambah(bagian)
                if alasan is not None:
                    return alasan
    return p.akhir()
