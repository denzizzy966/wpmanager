import json
import time

import httpx

from wpmgr.errors import (
    BAD_RESPONSE,
    TRANSIENT,
    UNKNOWN,
    SiteError,
    klasifikasi_respons,
)
from wpmgr.signing import new_nonce, sign

PREFIX = "/wp-json/wpmgr/v1"
TIMEOUT_PING = 15.0
TIMEOUT_INVENTORY = 60.0
TIMEOUT_UPDATE = 180.0


class SiteClient:
    def __init__(
        self, base_url: str, site_id: str, secret_hex: str, client: httpx.Client | None = None
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("URL site wajib berskema https://")
        self.base_url = base_url.rstrip("/")
        self.site_id = site_id
        self.secret_hex = secret_hex
        self._client = client or httpx.Client(follow_redirects=False)

    def _panggil(self, method: str, path: str, body: bytes, timeout: float) -> dict:
        timestamp = int(time.time())
        nonce = new_nonce()
        headers = {
            "X-Wpmgr-Site": self.site_id,
            "X-Wpmgr-Timestamp": str(timestamp),
            "X-Wpmgr-Nonce": nonce,
            "X-Wpmgr-Signature": sign(self.secret_hex, method, path, timestamp, nonce, body),
            "Accept": "application/json",
        }
        if body:
            headers["Content-Type"] = "application/json"

        try:
            resp = self._client.request(
                method, f"{self.base_url}{path}", content=body or None,
                headers=headers, timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise SiteError(UNKNOWN, f"timeout setelah {timeout} detik") from exc
        except httpx.HTTPError as exc:
            raise SiteError(TRANSIENT, f"kesalahan koneksi: {exc}") from exc

        teks = resp.text
        kelas = klasifikasi_respons(resp.status_code, dict(resp.headers), teks)
        if kelas is not None:
            raise SiteError(kelas, teks[:500])

        try:
            return json.loads(teks)
        except ValueError as exc:
            raise SiteError(BAD_RESPONSE, teks[:500]) from exc

    def ping(self, timeout: float = TIMEOUT_PING) -> dict:
        return self._panggil("GET", f"{PREFIX}/ping", b"", timeout)

    def inventory(self, timeout: float = TIMEOUT_INVENTORY) -> dict:
        return self._panggil("GET", f"{PREFIX}/inventory", b"", timeout)

    def update(self, tipe: str, slug: str, ke_versi: str, timeout: float = TIMEOUT_UPDATE) -> dict:
        body = json.dumps(
            {"tipe": tipe, "slug": slug, "ke_versi": ke_versi}, separators=(",", ":")
        ).encode("utf-8")
        return self._panggil("POST", f"{PREFIX}/update", body, timeout)
