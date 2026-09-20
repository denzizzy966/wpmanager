AUTH_ERROR = "auth_error"
CONNECTOR_MISSING = "connector_missing"
BLOCKED = "blocked"
TRANSIENT = "transient"
BAD_RESPONSE = "bad_response"
UPGRADE_FAILED = "upgrade_failed"
UNKNOWN = "unknown"

DAPAT_DIULANG = frozenset({TRANSIENT, BAD_RESPONSE})

import json

_PENANDA_FIREWALL_BODY = ("wordfence", "cloudflare", "attention required")
_PREFIX_KODE_PLUGIN = "wpmgr_"


class SiteError(Exception):
    def __init__(self, error_class: str, pesan: str) -> None:
        super().__init__(pesan)
        self.error_class = error_class
        self.pesan = pesan

    @property
    def dapat_diulang(self) -> bool:
        return self.error_class in DAPAT_DIULANG


def _dari_plugin(body: str) -> bool:
    """True bila body ini adalah balasan JSON milik connector kita sendiri.

    Connector selalu menyebut dirinya lewat field `code` berawalan `wpmgr_`.
    Firewall tidak pernah melakukannya, sehingga inilah pembeda yang andal
    antara penolakan dari origin dan blokir dari edge.
    """
    try:
        data = json.loads(body)
    except ValueError:
        return False
    return isinstance(data, dict) and str(data.get("code", "")).startswith(
        _PREFIX_KODE_PLUGIN
    )


def _terlihat_firewall(headers: dict[str, str], body: str) -> bool:
    rendah = {k.lower(): (v or "").lower() for k, v in headers.items()}
    if "cf-ray" in rendah:
        return True
    if "cloudflare" in rendah.get("server", ""):
        return True
    cuplikan = body[:2000].lower()
    return any(p in cuplikan for p in _PENANDA_FIREWALL_BODY)


def klasifikasi_respons(status: int, headers: dict[str, str], body: str) -> str | None:
    if status in (401, 403):
        if _dari_plugin(body):
            return AUTH_ERROR
        return BLOCKED if _terlihat_firewall(headers, body) else AUTH_ERROR
    if status == 404:
        return CONNECTOR_MISSING
    if status == 429:
        return TRANSIENT
    if status >= 500:
        return TRANSIENT
    if status == 200:
        awal = body.lstrip()[:200].lower()
        if awal.startswith(("<!doctype", "<html")):
            return BAD_RESPONSE
        return None
    return BAD_RESPONSE
