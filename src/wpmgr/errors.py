import json

AUTH_ERROR = "auth_error"
CONNECTOR_MISSING = "connector_missing"
BLOCKED = "blocked"
TRANSIENT = "transient"
BAD_RESPONSE = "bad_response"
UPGRADE_FAILED = "upgrade_failed"
UNKNOWN = "unknown"
# Site hidup dan connector menjawab, tetapi paket yang diminta tidak ada di
# sana: inventaris dashboard sudah menyimpang dari kenyataan. Mengulang tidak
# akan mengubah jawabannya; scan ulang yang memperbaikinya.
PACKAGE_MISSING = "package_missing"
# Handler melempar sesuatu yang bukan SiteError -- bug di sisi dashboard,
# bukan kondisi site. Tidak diulang dan tidak menyentuh status site.
INTERNAL_ERROR = "internal_error"
# Lapis 3. Tidak satu pun mengubah status site produksi: staging yang mati,
# ditolak, atau gagal bukan diagnosis tentang koneksi ke site.
STAGING_MATI = "staging_mati"
STAGING_DITOLAK = "staging_ditolak"
STAGING_GAGAL = "staging_gagal"
# 413 dari connector: satu permintaan potongan melebihi 8 MB (berkas tumbuh
# sejak manifest). Pemanggil memecah permintaannya, bukan mengulang.
TERLALU_BESAR = "terlalu_besar"
# 404 wpmgr_staging_tidak_ada: berkas dihapus di produksi sejak manifest
# (berkas itu dihapus juga di staging), ATAU dorongan/area sementara dorong
# yang dirujuk tidak ada di site (mis. /staging/bersihkan atau langkah
# `selesai` yang diulang sesudah area dibersihkan; pemanggil dorong
# memperlakukannya sebagai "sudah selesai"). Bukan "connector hilang".
BERKAS_HILANG = "berkas_hilang"
KELAS_STAGING = frozenset({STAGING_MATI, STAGING_DITOLAK, STAGING_GAGAL, TERLALU_BESAR, BERKAS_HILANG})

DAPAT_DIULANG = frozenset({TRANSIENT, BAD_RESPONSE})

_PENANDA_FIREWALL_BODY = ("wordfence", "cloudflare", "attention required")
_PREFIX_KODE_PLUGIN = "wpmgr_"

# Kode WP_Error yang dikirim connector (lihat class-wpmgr-updater.php).
KODE_TIDAK_DITEMUKAN = "wpmgr_tidak_ditemukan"
KODE_TIDAK_ADA_UPDATE = "wpmgr_tidak_ada_update"
KODE_SIBUK = "wpmgr_sibuk"
KODE_UPGRADE_GAGAL = "wpmgr_upgrade_gagal"
KODE_PASANG_GAGAL = "wpmgr_pasang_gagal"
KODE_PAKET_RUSAK = "wpmgr_paket_rusak"
KODE_STAGING_MATI = "wpmgr_staging_mati"
KODE_TERLALU_BESAR = "wpmgr_staging_terlalu_besar"
KODE_STAGING_TIDAK_ADA = "wpmgr_staging_tidak_ada"
# Satu baris tabel yang SQL-nya sendiri melebihi batas respons connector:
# baris itu selalu gagal dengan cara yang sama, jadi mengulang tidak berguna.
KODE_BARIS_TERLALU_BESAR = "wpmgr_staging_baris_terlalu_besar"

# Kode staging lain dari connector, dipetakan per (status, kode) karena satu
# kode bisa dipakai untuk kondisi berbeda (mis. wpmgr_staging_impor 422 =
# SQL ditolak, selalu gagal sama; 500 = galat database, bisa sementara).
# Putusan R15.
KELAS_KODE_STAGING = {
    # Tabel sementara masih ditahan pemulihan dorongan lain (24 jam):
    # ditampilkan ke pengguna ("ditahan, coba lagi nanti"), tidak diulang segera.
    (409, "wpmgr_staging_ditahan"): STAGING_DITOLAK,
    # Langkah lain untuk dorongan yang sama sedang memegang kunci; lepas sendiri.
    (409, "wpmgr_staging_sibuk"): TRANSIENT,
    # Dorongan ini direbut dorongan lain: tidak akan pernah bisa dilanjutkan.
    (409, "wpmgr_staging_direbut"): STAGING_GAGAL,
    # Permintaan yang ditolak bentuknya: permintaan yang sama ditolak lagi.
    (400, "wpmgr_staging_path"): STAGING_GAGAL,
    (400, "wpmgr_staging_permintaan"): STAGING_GAGAL,
    (400, "wpmgr_staging_kursor"): STAGING_GAGAL,
    (400, "wpmgr_staging_paket"): STAGING_GAGAL,
    (400, "wpmgr_staging_sql"): STAGING_GAGAL,
    (422, "wpmgr_staging_impor"): STAGING_GAGAL,
    (422, "wpmgr_staging_rencana"): STAGING_GAGAL,
    (422, "wpmgr_staging_verifikasi"): STAGING_GAGAL,
    # Hash potongan tidak cocok: body rusak di jalan, mengirim ulang membantu.
    (422, "wpmgr_staging_hash"): BAD_RESPONSE,
    # Connector memakai kode ini untuk dua kondisi: total unggahan dorongan
    # melebihi MAKS_TOTAL_UNGGAH (pasti gagal lagi), dan disk server hampir
    # penuh (sisa < max(512 MB, 5%)). Yang kedua tidak pulih dalam jeda
    # ulang job; mengulang hanya menunda pesan yang sama.
    (507, "wpmgr_staging_disk_penuh"): STAGING_GAGAL,
}


class SiteError(Exception):
    def __init__(self, error_class: str, pesan: str) -> None:
        super().__init__(pesan)
        self.error_class = error_class
        self.pesan = pesan

    @property
    def dapat_diulang(self) -> bool:
        return self.error_class in DAPAT_DIULANG


def _json_plugin(body: str) -> dict | None:
    """Body ini sebagai dict bila ia balasan JSON milik connector kita sendiri.

    Connector selalu menyebut dirinya lewat field `code` berawalan `wpmgr_`.
    Firewall tidak pernah melakukannya, dan WordPress sendiri memakai kode
    `rest_*` (mis. `rest_no_route` saat plugin nonaktif), sehingga inilah
    pembeda yang andal antara jawaban connector dan jawaban siapa pun yang
    berdiri di depannya.
    """
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        # RecursionError: body "[[[[..." sangat dalam dari pihak mana pun di
        # depan connector; itu jelas bukan balasan connector.
        return None
    if isinstance(data, dict) and str(data.get("code", "")).startswith(_PREFIX_KODE_PLUGIN):
        return data
    return None


def kode_plugin(body: str) -> str | None:
    data = _json_plugin(body)
    return None if data is None else str(data["code"])


def pesan_plugin(body: str) -> str | None:
    """Field `message` dari balasan connector, apa adanya.

    Body JSON mentah meng-escape '/' menjadi '\\/', jadi pesan WordPress yang
    memuat URL tidak lagi sama dengan aslinya bila body itu yang dicatat.
    """
    data = _json_plugin(body)
    if data is None:
        return None
    pesan = data.get("message")
    return pesan if isinstance(pesan, str) else None


def _terlihat_firewall(headers: dict[str, str], body: str) -> bool:
    rendah = {k.lower(): (v or "").lower() for k, v in headers.items()}
    if "cf-ray" in rendah:
        return True
    if "cloudflare" in rendah.get("server", ""):
        return True
    cuplikan = body[:2000].lower()
    return any(p in cuplikan for p in _PENANDA_FIREWALL_BODY)


def klasifikasi_respons(status: int, headers: dict[str, str], body: str) -> str | None:
    kode = kode_plugin(body)
    if kode == KODE_STAGING_MATI:
        # Admin site mematikan "Izinkan staging". Tanpa cabang ini 403 ini
        # dibaca auth_error dan site sehat berubah menjadi needs_reconnect.
        return STAGING_MATI
    if kode == KODE_TERLALU_BESAR:
        return TERLALU_BESAR
    if kode == KODE_STAGING_TIDAK_ADA:
        return BERKAS_HILANG
    if kode == KODE_BARIS_TERLALU_BESAR:
        # Tanpa cabang ini 413 jatuh ke BAD_RESPONSE, yang diulang sia-sia.
        return STAGING_GAGAL
    if (status, kode) in KELAS_KODE_STAGING:
        return KELAS_KODE_STAGING[(status, kode)]
    if status in (401, 403):
        if kode is not None:
            return AUTH_ERROR
        return BLOCKED if _terlihat_firewall(headers, body) else AUTH_ERROR
    if status == 404:
        if kode == KODE_TIDAK_DITEMUKAN:
            return PACKAGE_MISSING
        if kode is not None:
            # Connector hidup dan menjawab dengan kode yang tidak kita kenal
            # (versi connector lebih baru?). needs_reconnect adalah diagnosis
            # yang salah untuk itu.
            return BAD_RESPONSE
        return CONNECTOR_MISSING
    if status == 409:
        if kode == KODE_SIBUK:
            # Upgrade lain sedang memegang lock di site; akan selesai sendiri.
            return TRANSIENT
        if kode == KODE_TIDAK_ADA_UPDATE:
            return UPGRADE_FAILED
        return BAD_RESPONSE
    if status == 429:
        return TRANSIENT
    if status >= 500:
        if kode in (KODE_UPGRADE_GAGAL, KODE_PASANG_GAGAL):
            # Upgrader WordPress sendiri yang menolak (izin berkas, unduhan
            # gagal, PHP terlalu lama). Mengulang tiga kali hanya menghasilkan
            # tiga pesan yang sama.
            return UPGRADE_FAILED
        return TRANSIENT
    if status == 200:
        awal = body.lstrip()[:200].lower()
        if awal.startswith(("<!doctype", "<html")):
            return BAD_RESPONSE
        return None
    return BAD_RESPONSE
