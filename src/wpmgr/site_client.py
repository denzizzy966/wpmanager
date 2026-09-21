import json
import time

import httpx

from wpmgr.errors import (
    BAD_RESPONSE,
    TRANSIENT,
    UNKNOWN,
    UPGRADE_FAILED,
    SiteError,
    klasifikasi_respons,
    pesan_plugin,
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

    def _panggil(
        self, method: str, path: str, body: bytes, timeout: float, berefek: bool = False
    ) -> dict:
        """Satu panggilan bertanda tangan ke connector.

        `berefek` menandai panggilan yang mengubah sesuatu di site (hanya
        /update). Hanya pada panggilan seperti itu timeout berarti "tidak
        diketahui": request mungkin sudah sampai dan upgrade mungkin sedang
        berjalan. Panggilan read-only aman diulang menurut konstruksinya.
        """
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
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # Koneksi ke site tidak pernah terbentuk (atau tidak pernah
            # mendapat slot di pool), jadi tidak ada yang berjalan di sana.
            raise SiteError(TRANSIENT, f"timeout koneksi setelah {timeout} detik") from exc
        except httpx.TimeoutException as exc:
            if berefek:
                raise SiteError(UNKNOWN, f"timeout setelah {timeout} detik") from exc
            raise SiteError(TRANSIENT, f"timeout setelah {timeout} detik") from exc
        except httpx.HTTPError as exc:
            raise SiteError(TRANSIENT, f"kesalahan koneksi: {exc}") from exc

        teks = resp.text
        kelas = klasifikasi_respons(resp.status_code, dict(resp.headers), teks)
        if kelas is not None:
            pesan = teks[:500]
            if kelas == UPGRADE_FAILED:
                # Spec §9: pesan asli WordPress dicatat apa adanya.
                pesan = (pesan_plugin(teks) or teks)[:500]
            if 300 <= resp.status_code < 400:
                tujuan = resp.headers.get("location", "(tanpa header Location)")
                pesan = (
                    f"HTTP {resp.status_code} mengalihkan ke {tujuan}. Site "
                    f"mengalihkan permintaan REST sebelum plugin menerimanya; "
                    f"penyebab paling umum adalah permalink masih disetel "
                    f"'Plain' di Pengaturan -> Permalink. "
                    f"{pesan}"
                ).strip()
            raise SiteError(kelas, pesan)

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
        return self._panggil("POST", f"{PREFIX}/update", body, timeout, berefek=True)
