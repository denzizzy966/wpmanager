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

DAPAT_DIULANG = frozenset({TRANSIENT, BAD_RESPONSE})

_PENANDA_FIREWALL_BODY = ("wordfence", "cloudflare", "attention required")
_PREFIX_KODE_PLUGIN = "wpmgr_"

# Kode WP_Error yang dikirim connector (lihat class-wpmgr-updater.php).
KODE_TIDAK_DITEMUKAN = "wpmgr_tidak_ditemukan"
KODE_TIDAK_ADA_UPDATE = "wpmgr_tidak_ada_update"
KODE_SIBUK = "wpmgr_sibuk"
KODE_UPGRADE_GAGAL = "wpmgr_upgrade_gagal"


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
    except ValueError:
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
        if kode == KODE_UPGRADE_GAGAL:
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
