import base64
import json
import socket
import threading
import time
from urllib.parse import urlencode

import httpx

from wpmgr.errors import (
    BAD_RESPONSE,
    TRANSIENT,
    UNKNOWN,
    UPGRADE_FAILED,
    SiteError,
    data_plugin,
    klasifikasi_respons,
    kode_plugin,
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


def buat_klien_staging(**kwargs) -> httpx.Client:
    """Klien httpx untuk endpoint staging: tanpa koneksi keep-alive.

    Tenggat total memutus permintaan dengan shutdown soket yang ditangkap
    saat koneksi dibuka (lihat SiteClient._kirim). Koneksi dari pool tidak
    pernah membuka soket baru, jadi tidak bisa diputus; tanpa keep-alive
    setiap permintaan staging selalu memakai koneksi baru.
    """
    return httpx.Client(follow_redirects=False, limits=httpx.Limits(max_keepalive_connections=0), **kwargs)


class TenggatHabis(Exception):
    """Permintaan melewati tenggat total (`minta_bertenggat`)."""


class MelebihiBatas(Exception):
    """Body respons melebihi batas byte (`minta_bertenggat` tanpa `potong`)."""


class TanpaHasil(Exception):
    """Pekerja berhenti tanpa hasil maupun galat (mis. SystemExit di thread itu)."""


def minta_bertenggat(http: httpx.Client, method: str, url: str, *, headers: dict, content: bytes | None = None,
                     timeout: float, tenggat: float, batas_byte: int, potong: bool = False,
                     periksa=None) -> tuple[int, dict, bytes]:
    """Satu permintaan HTTP dengan tenggat total dan batas byte (putusan F11).

    Tenggat total mencakup SELURUH permintaan: koneksi, TLS, pengiriman body,
    tunggu header, dan body. httpx tidak punya timeout total, dan panggilan
    yang sedang terblokir di recv() tidak bisa dibatalkan dari thread yang
    sama. Karena itu permintaan dijalankan di thread pekerja, dan thread
    pemanggil menunggu paling lama sampai tenggat. Bila lewat, pemanggil
    langsung mendapat TenggatHabis, dan soket yang dibuka untuk permintaan
    ini (ditangkap lewat ekstensi `trace` httpcore) di-shutdown supaya recv()
    pekerja yang terblokir ikut bangun, termasuk saat header atau body
    diteteskan (yang terus mengulang timeout baca). Supaya soket itu selalu
    tertangkap, klien sebaiknya tanpa keep-alive dan `headers` membawa
    `Connection: close`: koneksi dari pool tidak membuka soket baru. Sebelum
    jabat tangan TLS selesai belum ada soket TLS; tahap itu dibatasi
    `timeout` (timeout per operasi httpx), yang pemanggil jepit ke tenggat.

    Body dibaca mentah (iter_raw, tanpa dekompresi) sampai `batas_byte`:
    lebih dari itu melempar MelebihiBatas, atau dengan `potong` dipotong di
    batas dan pembacaan berhenti. `periksa(resp)` dipanggil sebelum body
    dibaca dan boleh melempar untuk menolak respons. Galat httpx diteruskan
    apa adanya supaya pemanggil yang memetakannya.
    """
    akhir = time.monotonic() + tenggat
    batal = threading.Event()
    soket: list = []
    kunci = threading.Lock()
    hasil: dict = {}

    def putus(s) -> None:
        # socket.socket.shutdown, bukan SSLSocket.shutdown: yang kedua
        # melepas objek TLS lebih dulu, sehingga pekerja yang sedang
        # berjalan sempat mengirim/menerima teks polos lewat soket itu.
        try:
            socket.socket.shutdown(s, socket.SHUT_RDWR)
        except (OSError, TypeError):
            pass

    def trace(nama: str, info: dict) -> None:
        # Soket TLS (dan TCP untuk jaga-jaga): soket TCP mentah terlepas
        # (detach) saat dibungkus TLS, jadi yang bisa di-shutdown untuk
        # membangunkan recv() yang terblokir adalah soket TLS-nya.
        if nama not in ("connection.connect_tcp.complete", "connection.start_tls.complete"):
            return
        s = getattr(info.get("return_value"), "get_extra_info", lambda _: None)("socket")
        if s is None:
            return
        with kunci:
            soket.append(s)
            sudah_batal = batal.is_set()
        if sudah_batal:
            # Koneksi baru selesai sesudah pemanggil menyerah.
            putus(s)

    def kerja() -> None:
        try:
            with http.stream(method, url, content=content, headers=headers, timeout=timeout,
                             extensions={"trace": trace}) as resp:
                try:
                    if periksa is not None:
                        periksa(resp)
                    isi = bytearray()
                    # Transport yang menyerahkan body sebagai bytes di memori
                    # (MockTransport) membuat httpx membacanya lebih dulu;
                    # tanpa content-encoding isinya sama dengan byte mentah.
                    aliran = [resp.content] if resp.is_stream_consumed else resp.iter_raw()
                    for bagian in aliran:
                        if batal.is_set():
                            return
                        isi.extend(bagian)
                        if len(isi) > batas_byte:
                            if not potong:
                                raise MelebihiBatas()
                            del isi[batas_byte:]
                            break
                        if len(isi) == batas_byte and potong:
                            break
                        if time.monotonic() > akhir:
                            raise TenggatHabis()
                    with kunci:
                        if not batal.is_set():
                            hasil["ok"] = (resp.status_code, dict(resp.headers), bytes(isi))
                finally:
                    # Sebelum koneksi ditutup (keluar dari `with`): soket ini
                    # tidak boleh lagi di-shutdown oleh pemanggil yang
                    # kebetulan baru mencapai tenggat.
                    with kunci:
                        soket.clear()
        except Exception as exc:  # noqa: BLE001 -- dilempar ulang di thread pemanggil
            hasil["galat"] = exc

    pekerja = threading.Thread(target=kerja, name="wpmgr-http-bertenggat", daemon=True)
    pekerja.start()
    pekerja.join(max(0.0, akhir - time.monotonic()))
    if pekerja.is_alive():
        with kunci:
            # Hasil yang sudah lengkap tetap dipakai walau pekerja masih
            # menutup koneksi saat tenggat lewat.
            selesai = "ok" in hasil
            if not selesai:
                batal.set()
                tertangkap = list(soket)
        if not selesai:
            for s in tertangkap:
                putus(s)
            raise TenggatHabis()
        return hasil["ok"]
    galat = hasil.get("galat")
    if galat is not None:
        raise galat
    if "ok" not in hasil:
        raise TanpaHasil()
    return hasil["ok"]


class SiteClient:
    def __init__(
        self, base_url: str, site_id: str, secret_hex: str, client: httpx.Client | None = None,
        klien_staging: httpx.Client | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("URL site wajib berskema https://")
        self.base_url = base_url.rstrip("/")
        self.site_id = site_id
        self.secret_hex = secret_hex
        self._client = client or httpx.Client(follow_redirects=False)
        # Klien yang disuntikkan (test dengan MockTransport) dipakai juga
        # untuk staging bila klien staging tidak diberikan tersendiri.
        self._klien_staging = klien_staging or client

    @property
    def _staging_http(self) -> httpx.Client:
        if self._klien_staging is None:
            self._klien_staging = buat_klien_staging()
        return self._klien_staging

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

        Tenggat total, batas byte, dan pemutusan soket dikerjakan
        `minta_bertenggat` (putusan F11); di sini hanya penandatanganan dan
        pemetaan galatnya ke SiteError.

        Galat sesudah permintaan mungkin sudah sampai (baca/tulis putus,
        protokol rusak, timeout baca) pada panggilan `berefek` menjadi
        UNKNOWN; hanya kegagalan membuka koneksi yang pasti TRANSIENT.
        """
        timestamp = int(time.time())
        nonce = new_nonce()
        headers = {
            "X-Wpmgr-Site": self.site_id,
            "X-Wpmgr-Timestamp": str(timestamp),
            "X-Wpmgr-Nonce": nonce,
            "X-Wpmgr-Signature": sign(self.secret_hex, method, path, timestamp, nonce, body),
            "Accept": "application/json, application/octet-stream",
            # Minta tanpa kompresi; body dibaca mentah (iter_raw) dan balasan
            # yang tetap dikompresi ditolak, sehingga batas byte berlaku pada
            # byte di kabel dan tidak ada dekompresi atas masukan penyerang.
            "Accept-Encoding": "identity",
            # Koneksi tidak dipakai ulang (lihat buat_klien_staging), juga bila
            # klien yang disuntikkan masih memakai keep-alive.
            "Connection": "close",
            **(header_tambahan or {}),
        }
        if body:
            headers["Content-Type"] = content_type
        kelas_waktu = UNKNOWN if berefek else TRANSIENT
        # Setiap operasi yang terblokir juga tidak lebih lama dari tenggat
        # total (lihat minta_bertenggat); nilai ini juga dipakai pesan galat.
        timeout = min(timeout, tenggat)

        def tolak_kompresi(resp: httpx.Response) -> None:
            enkode = resp.headers.get("content-encoding", "").strip().lower()
            if enkode not in ("", "identity"):
                raise SiteError(BAD_RESPONSE, "Respons connector dikompresi padahal diminta tanpa kompresi")

        try:
            return minta_bertenggat(self._staging_http, method, f"{self.base_url}{path}{query}",
                                    headers=headers, content=body or None, timeout=timeout, tenggat=tenggat,
                                    batas_byte=batas_byte, periksa=tolak_kompresi)
        except TenggatHabis:
            raise SiteError(kelas_waktu, f"Respons connector melewati tenggat total {tenggat:g} detik") from None
        except MelebihiBatas:
            raise SiteError(BAD_RESPONSE, f"Respons connector melebihi batas {batas_byte} byte") from None
        except TanpaHasil:
            raise SiteError(kelas_waktu, "Permintaan ke connector berhenti tanpa hasil") from None
        except httpx.HTTPError as galat:
            raise self._galat_http(galat, kelas_waktu, timeout) from galat

    @staticmethod
    def _galat_http(galat: httpx.HTTPError, kelas_waktu: str, timeout: float) -> SiteError:
        if isinstance(galat, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            # Koneksi tidak pernah terbentuk: tidak ada yang sampai ke site.
            return SiteError(TRANSIENT, f"koneksi gagal: {galat}")
        if isinstance(galat, httpx.TimeoutException):
            return SiteError(kelas_waktu, f"timeout setelah {timeout} detik")
        # Putus sesudah permintaan mungkin sudah sampai (ReadError,
        # WriteError, RemoteProtocolError): pada panggilan berefek hasilnya
        # tidak diketahui, seperti timeout.
        return SiteError(kelas_waktu, f"kesalahan koneksi: {galat}")

    @staticmethod
    def _galat_dari(status: int, headers: dict, isi: bytes) -> SiteError | None:
        teks = isi[:65536].decode("utf-8", "replace")
        kelas = klasifikasi_respons(status, headers, teks)
        if kelas is None:
            return None
        return SiteError(kelas, (pesan_plugin(teks) or teks)[:500], kode=kode_plugin(teks), data=data_plugin(teks))

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
                      header_tambahan: dict | None = None, tenggat: float | None = None) -> dict:
        body = json.dumps(badan, separators=(",", ":")).encode("utf-8") if badan is not None else b""
        timeout, batas = ((TIMEOUT_STAGING_TERAPKAN, TENGGAT_STAGING_TERAPKAN) if terapkan
                          else (TIMEOUT_STAGING, TENGGAT_STAGING))
        tenggat = batas if tenggat is None else min(batas, tenggat)
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
        # POST hanya karena daftar path bisa panjang. Connector
        # (WPMGR_Staging::snapshot, WPMGR_Staging_Dorong::snapshot_berkas)
        # hanya membaca: ada/ukuran/mtime berkas, daftar tabel, tanda air;
        # tidak menulis apa pun di produksi, jadi aman diulang (bukan berefek).
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

    def staging_bersihkan(self, dorong_id: str, tenggat: float | None = None) -> dict:
        # `tenggat` pendek untuk pembersihan upaya-terbaik saat job gagal final
        # (putusan F9a): job yang sudah gagal tidak boleh tertahan lama di sini.
        return self._staging_json("POST", "/staging/bersihkan", {"dorong_id": dorong_id}, berefek=True,
                                  tenggat=tenggat)
