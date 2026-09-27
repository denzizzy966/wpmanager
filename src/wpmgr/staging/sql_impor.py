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
