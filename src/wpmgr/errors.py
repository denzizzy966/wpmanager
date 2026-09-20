AUTH_ERROR = "auth_error"
CONNECTOR_MISSING = "connector_missing"
BLOCKED = "blocked"
TRANSIENT = "transient"
BAD_RESPONSE = "bad_response"
UPGRADE_FAILED = "upgrade_failed"
UNKNOWN = "unknown"

DAPAT_DIULANG = frozenset({TRANSIENT, BAD_RESPONSE})

_PENANDA_FIREWALL_BODY = ("wordfence", "cloudflare", "attention required")


class SiteError(Exception):
    def __init__(self, error_class: str, pesan: str) -> None:
        super().__init__(pesan)
        self.error_class = error_class
        self.pesan = pesan

    @property
    def dapat_diulang(self) -> bool:
        return self.error_class in DAPAT_DIULANG


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
        return BLOCKED if _terlihat_firewall(headers, body) else AUTH_ERROR
    if status == 404:
        return CONNECTOR_MISSING
    if status >= 500:
        return TRANSIENT
    if status == 200 and body.lstrip()[:9].lower().startswith("<!doctype"):
        return BAD_RESPONSE
    if status == 200 and body.lstrip().startswith("<html"):
        return BAD_RESPONSE
    return None
