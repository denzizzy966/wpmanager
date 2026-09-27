import base64
import json
import time
from urllib.parse import urlencode

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
from wpmgr.staging import paket

PREFIX = "/wp-json/wpmgr/v1"
TIMEOUT_PING = 15.0
TIMEOUT_INVENTORY = 60.0
TIMEOUT_UPDATE = 180.0
TIMEOUT_KOLEKSI = 30.0
TIMEOUT_SELF_UPDATE = 180.0
# Timeout httpx berlaku per operasi baca, bukan total: connector (atau apa
# pun di depannya) yang meneteskan satu byte per beberapa detik menahan
# stream selamanya. TENGGAT_* adalah batas total monotonik per panggilan
# (putusan F11), dibaca saat panggilan supaya test bisa mengecilkannya.
TIMEOUT_STAGING = 45.0
TIMEOUT_STAGING_TERAPKAN = 60.0
TENGGAT_STAGING = 120.0
TENGGAT_STAGING_TERAPKAN = 150.0
BATAS_JSON_STAGING = 32 * 1024 * 1024
# Isi 8 MiB + meta 1 MiB + kepala: paket sah terbesar dari connector.
BATAS_BINER_STAGING = paket.BATAS_PAKET


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
        self, method: str, path: str, body: bytes, timeout: float, berefek: bool = False,
        query: str = "",
    ) -> dict:
        """Satu panggilan bertanda tangan ke connector.

        `berefek` menandai panggilan yang mengubah sesuatu di site (hanya
        /update). Hanya pada panggilan seperti itu timeout berarti "tidak
        diketahui": request mungkin sudah sampai dan upgrade mungkin sedang
        berjalan. Panggilan read-only aman diulang menurut konstruksinya.
        Query string tidak ikut ditandatangani: connector memverifikasi tanda tangan atas "/wp-json" + route, dan route WordPress tidak memuat query.
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
                method, f"{self.base_url}{path}{query}", content=body or None,
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
        except (ValueError, RecursionError) as exc:
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

    def events(self, kursor: str | None, batas: int = 500,
               timeout: float = TIMEOUT_KOLEKSI) -> dict:
        param = {"batas": str(batas)}
        if kursor:
            param["kursor"] = kursor
        return self._panggil("GET", f"{PREFIX}/events", b"", timeout,
                             query="?" + urlencode(param))

    def traffic(self, dari: str | None = None, timeout: float = TIMEOUT_KOLEKSI) -> dict:
        query = "?" + urlencode({"dari": dari}) if dari else ""
        return self._panggil("GET", f"{PREFIX}/traffic", b"", timeout, query=query)

    def self_update(self, versi: str, sha256: str, isi_zip: bytes,
                    timeout: float = TIMEOUT_SELF_UPDATE) -> dict:
        body = json.dumps(
            {"versi": versi, "sha256": sha256,
             "zip_b64": base64.b64encode(isi_zip).decode("ascii")},
            separators=(",", ":"),
        ).encode("utf-8")
        # berefek: connector mungkin sedang menimpa dirinya ketika koneksi
        # terputus. Mengulang aman karena versi yang sama dibalas "sudah di
        # versi tersebut".
        return self._panggil("POST", f"{PREFIX}/self-update", body, timeout, berefek=True)

    # ---- staging (Lapis 3) ------------------------------------------------

    def _kirim(self, method: str, path: str, body: bytes, timeout: float, tenggat: float, berefek: bool,
               query: str, content_type: str, header_tambahan: dict | None,
               batas_byte: int) -> tuple[int, dict, bytes]:
        """Seperti _panggil(), tetapi body dibaca sebagai stream dengan batas.

        Endpoint staging mengembalikan sampai 8 MB per potongan; connector
        yang disusupi bisa mengirim apa saja, jadi pembacaan berhenti di
        batas byte atau tenggat total, alih-alih menampung seluruhnya di
        memori atau menunggu selamanya.
        """
        timestamp = int(time.time())
        nonce = new_nonce()
        headers = {
            "X-Wpmgr-Site": self.site_id,
            "X-Wpmgr-Timestamp": str(timestamp),
            "X-Wpmgr-Nonce": nonce,
            "X-Wpmgr-Signature": sign(self.secret_hex, method, path, timestamp, nonce, body),
            "Accept": "application/json, application/octet-stream",
            # Tanpa kompresi: batas byte berlaku pada byte di kabel, dan satu
            # potongan gzip kecil tidak bisa mengembang menjadi ratusan MB.
            "Accept-Encoding": "identity",
            **(header_tambahan or {}),
        }
        if body:
            headers["Content-Type"] = content_type
        akhir = time.monotonic() + tenggat
        kelas_waktu = UNKNOWN if berefek else TRANSIENT
        try:
            with self._client.stream(method, f"{self.base_url}{path}{query}", content=body or None,
                                     headers=headers, timeout=timeout) as resp:
                isi = bytearray()
                for potong in resp.iter_bytes():
                    isi.extend(potong)
                    if len(isi) > batas_byte:
                        raise SiteError(BAD_RESPONSE, f"Respons connector melebihi batas {batas_byte} byte")
                    if time.monotonic() > akhir:
                        raise SiteError(kelas_waktu, f"Respons connector melewati tenggat total {tenggat:g} detik")
                return resp.status_code, dict(resp.headers), bytes(isi)
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise SiteError(TRANSIENT, f"timeout koneksi setelah {timeout} detik") from exc
        except httpx.TimeoutException as exc:
            raise SiteError(kelas_waktu, f"timeout setelah {timeout} detik") from exc
        except httpx.HTTPError as exc:
            raise SiteError(TRANSIENT, f"kesalahan koneksi: {exc}") from exc

    @staticmethod
    def _galat_dari(status: int, headers: dict, isi: bytes) -> SiteError | None:
        teks = isi[:65536].decode("utf-8", "replace")
        kelas = klasifikasi_respons(status, headers, teks)
        if kelas is None:
            return None
        return SiteError(kelas, (pesan_plugin(teks) or teks)[:500])

    @classmethod
    def _json_objek(cls, status: int, headers: dict, isi: bytes) -> dict:
        """Balasan JSON staging sebagai dict, atau SiteError (putusan F15c: satu tempat)."""
        galat = cls._galat_dari(status, headers, isi)
        if galat is not None:
            raise galat
        try:
            data = json.loads(isi)
        except (ValueError, RecursionError) as exc:
            # RecursionError: "[[[[..." sedalam ratusan ribu tingkat.
            raise SiteError(BAD_RESPONSE, isi[:500].decode("utf-8", "replace")) from exc
        if not isinstance(data, dict):
            raise SiteError(BAD_RESPONSE, "Balasan connector bukan objek JSON")
        return data

    def _staging_json(self, method: str, route: str, badan: dict | None = None, query: str = "",
                      berefek: bool = False, terapkan: bool = False,
                      header_tambahan: dict | None = None) -> dict:
        body = json.dumps(badan, separators=(",", ":")).encode("utf-8") if badan is not None else b""
        timeout, tenggat = ((TIMEOUT_STAGING_TERAPKAN, TENGGAT_STAGING_TERAPKAN) if terapkan
                            else (TIMEOUT_STAGING, TENGGAT_STAGING))
        return self._json_objek(*self._kirim(method, f"{PREFIX}{route}", body, timeout, tenggat, berefek, query,
                                             "application/json", header_tambahan, BATAS_JSON_STAGING))

    def _staging_paket(self, route: str, badan: dict) -> tuple[dict, list[bytes]]:
        body = json.dumps(badan, separators=(",", ":")).encode("utf-8")
        status, headers, isi = self._kirim("POST", f"{PREFIX}{route}", body, TIMEOUT_STAGING, TENGGAT_STAGING,
                                           False, "", "application/json", None, BATAS_BINER_STAGING)
        if status == 200 and isi.startswith(paket.MAGIC):
            try:
                return paket.urai(isi)
            except paket.PaketRusak as exc:
                raise SiteError(BAD_RESPONSE, f"Paket staging rusak: {exc}") from exc
        galat = self._galat_dari(status, headers, isi)
        raise galat or SiteError(BAD_RESPONSE, "Balasan connector bukan paket staging")

    def staging_manifest(self, kursor: str | None = None, batas: int = 5000) -> dict:
        param = {"batas": str(int(batas))}
        if kursor:
            param["kursor"] = kursor
        return self._staging_json("GET", "/staging/manifest", query="?" + urlencode(param))

    def staging_file(self, paths: list[str]) -> tuple[dict, list[bytes]]:
        return self._staging_paket("/staging/file", {"berkas": list(paths)})

    def staging_rentang(self, path: str, dari: int, panjang: int) -> tuple[dict, list[bytes]]:
        return self._staging_paket("/staging/file", {"rentang": {"path": path, "dari": dari, "panjang": panjang}})

    def staging_tabel(self, tabel: str, kursor: str | None) -> tuple[dict, list[bytes]]:
        return self._staging_paket("/staging/tabel", {"tabel": tabel, "kursor": kursor or ""})

    def staging_tanda_air(self, posts_sejak: str | None = None, posts_maks: int | None = None) -> dict:
        param = {}
        if posts_sejak and posts_maks:
            param = {"posts_sejak": posts_sejak, "posts_maks": str(posts_maks)}
        return self._staging_json("GET", "/staging/tanda-air", query="?" + urlencode(param) if param else "")

    def staging_snapshot(self, paths: list[str], awal: bool = False) -> dict:
        return self._staging_json("POST", "/staging/snapshot", {"paths": list(paths), "awal": awal})

    def staging_unggah(self, isi: bytes) -> dict:
        return self._json_objek(*self._kirim("POST", f"{PREFIX}/staging/unggah", isi, TIMEOUT_STAGING,
                                             TENGGAT_STAGING, True, "", "application/octet-stream", None,
                                             BATAS_JSON_STAGING))

    def staging_terapkan(self, badan: dict, token_lewati: str | None = None) -> dict:
        # X-Wpmgr-Lewati tidak ditandatangani, dan memang tidak perlu: token
        # yang sama ada di body `tukar` yang ditandatangani, dan header ini
        # hanya membuka pintu .maintenance untuk request ini.
        header = {"X-Wpmgr-Lewati": token_lewati} if token_lewati else None
        return self._staging_json("POST", "/staging/terapkan", badan, berefek=True, terapkan=True,
                                  header_tambahan=header)

    def staging_bersihkan(self, dorong_id: str) -> dict:
        return self._staging_json("POST", "/staging/bersihkan", {"dorong_id": dorong_id}, berefek=True)
