"""Tiruan connector produksi dan skrip pembantu untuk test integrasi staging.

`ProduksiPalsu` meniru kontrak connector 3.0 apa adanya (Task 3–8), bukan
kontrak draf rencana: halaman manifest boleh kosong dengan `lagi: true`,
`info` hanya di halaman pertama, paket berkas berhenti dini dengan
`lengkap: false` dan penanda per entri (`terlalu_besar`, `hilang`,
`galat`), rentang yang hilang menjadi paket satu-bagian `hilang: true`,
potongan tabel membawa `mode` pk|offset, dan tanda air memakai kunci
sumber WPMGR_Staging_TandaAir (`pesanan_hpos`, `pesanan_posts`, ...).
Dipakai test Task 14–17.
"""

import hashlib
import json
import re
from pathlib import Path

import httpx

from wpmgr.signing import verify
from wpmgr.site_client import SiteClient
from wpmgr.staging import paket
from wpmgr.staging.pembantu import StatusPembantu

GB = 1024**3
SECRET = "f" * 64
AWALAN = "/wp-json/wpmgr/v1"
_POLA_WAKTU = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")


def _json(kode: int, data) -> httpx.Response:
    return httpx.Response(kode, json=data)


def _galat(kode: int, kode_wp: str, pesan: str, **data) -> httpx.Response:
    return _json(kode, {"code": kode_wp, "message": pesan, "data": {"status": kode, **data}})


# Kosakata status dorong WPMGR_Staging_Dorong (Task 7-8), disalin apa adanya.
STATUS_BOLEH_BERSIHKAN = {"baru", "mengunggah", "menyiapkan", "siap", "mengimpor", "terimpor",
                          "selesai", "gagal", "direbut", "dipulihkan"}
STATUS_PRA_TUKAR = {"baru", "mengunggah", "menyiapkan", "siap", "mengimpor", "terimpor"}
STATUS_MENYENTUH_PRODUKSI = {"menukar", "ditukar", "memulihkan"}
STATUS_LANGKAH_SELESAI = {
    "siapkan": {"siap", "mengimpor", "terimpor", "menukar", "ditukar", "selesai"},
    "impor": {"terimpor", "menukar", "ditukar", "selesai"},
    "tukar": {"ditukar", "selesai"},
    "selesai": {"selesai"},
    "pulihkan": {"dipulihkan"},
}


def respons_putus(r: httpx.Request):
    """Koneksi putus sesudah permintaan terkirim: dashboard mendapat UNKNOWN."""
    raise httpx.ReadError("koneksi putus", request=r)


def _biner(isi: bytes) -> httpx.Response:
    return httpx.Response(200, content=isi, headers={"Content-Type": "application/octet-stream"})


class ProduksiPalsu:
    """Connector 3.0 tiruan di atas kamus berkas dan potongan SQL per tabel."""

    def __init__(self) -> None:
        self.berkas: dict[str, tuple[bytes, int]] = {}
        self.tabel: dict[str, list[bytes]] = {}
        # Kolom PK per tabel; daftar kosong = tabel tanpa PK (mode offset).
        self.pk: dict[str, list[str]] = {}
        self.info = {"php": "8.1.29", "wp": "6.5", "table_prefix": "wp_", "charset": "utf8mb4",
                     "home": "https://contoh.test", "siteurl": "https://contoh.test", "multisite": False,
                     "konten_di_luar": False, "batas_unggah": 4194304, "unggah_terlalu_kecil": False,
                     "tabel_dilewati": 0}
        self.tanda_air = {"sumber": {
            "posts": {"maks_id": 10, "jumlah": 5, "diubah": "2026-09-20 00:00:00", "diubah_sejak": None},
            "comments": {"maks_id": 3, "jumlah": 2}, "users": {"maks_id": 1, "jumlah": 1},
            "pesanan_posts": {"maks_id": 0, "jumlah": 0},
        }}
        # Nilai diubah_sejak bila permintaan membawa posts_sejak + posts_maks yang sah.
        self.posts_diubah_sejak = 0
        self.halaman = 2
        # Jumlah halaman kosong (berkas: [], lagi: true, kursor maju) sebelum
        # halaman berisi pertama; connector mengirimnya bila tenggat habis
        # saat baru melewati entri anomali.
        self.halaman_kosong = 0
        self.maks_hash = 50 * 1024 * 1024
        self.maks_paket = 8 * 1024 * 1024
        # Paling banyak sekian entri per paket berkas (meniru berhenti dini
        # karena tenggat); None = tanpa batas selain maks_paket.
        self.maks_entri: int | None = None
        # Paling banyak sekian byte per balasan rentang (connector yang
        # meneteskan data); None = sebanyak `panjang` yang diminta.
        self.maks_rentang: int | None = None
        # Ukuran tabel yang dilaporkan di info, menimpa ukuran sebenarnya.
        self.ukuran_tabel: dict[str, int] = {}
        # Path yang ada tetapi tidak terbaca: penanda `galat: 'baca'`.
        self.gagal_baca: set[str] = set()
        self.jadwal_gagal: dict[str, set[int]] = {}
        self.sebelum: dict = {}
        self.ekstra_manifest: list = []
        self.jumlah_dilewati = 0
        # Langkah yang gagal di connector. "tukar": gagal_tukar() connector
        # (pemulihan otomatis di request yang sama, lalu 500
        # wpmgr_staging_tukar dengan data.pemulihan); langkah lain: galat
        # khas langkah itu.
        self.gagal_langkah: str | None = None
        # Pemulihan otomatis di gagal_tukar() tidak tuntas: status tetap
        # "memulihkan" dan dashboard harus melanjutkan dengan langkah pulihkan.
        self.pulih_otomatis_macet = False
        # Kejadian sekali pakai per langkah terapkan ("siapkan", "tukar", ...)
        # atau per route ("unggah", "bersihkan"), diambil berurutan:
        #   "putus"        langkah dijalankan, lalu respons hilang (ReadError);
        #   "putus_awal"   koneksi putus sebelum apa pun dijalankan;
        #   "gagal_putus"  tukar gagal di connector (seperti gagal_langkah),
        #                  lalu respons hilang;
        #   "lagi"         langkah maju tetapi belum selesai (selesai:false);
        #   httpx.Response dikembalikan apa adanya tanpa menjalankan langkah.
        self.kejadian: dict[str, list] = {}
        self.impor_ditahan = False
        self.halaman_utama = 200
        self.hitung: dict[str, int] = {}
        self.diminta: list[tuple[str, object]] = []
        # Potongan dorongan TERAKHIR yang mulai diunggah (nomor -> paket).
        self.unggahan: dict[int, bytes] = {}
        self.langkah: list[str] = []
        # (dorong_id, langkah) setiap panggilan terapkan.
        self.langkah_id: list[tuple[str, str]] = []
        self.sql_diterapkan = b""
        # Keadaan per dorongan (keadaan.php connector) dan kunci dorong site.
        self.dorongan: dict[str, dict] = {}
        self.kunci: str | None = None
        self._ekstra_terkirim = False

    def klien(self, site) -> SiteClient:
        return SiteClient(site.url, str(site.id), SECRET,
                          client=httpx.Client(transport=httpx.MockTransport(self.tangani)))

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(self.halaman_utama, text="<html><title>Produksi</title></html>")))

    def _entri(self, path: str) -> dict:
        isi, mtime = self.berkas[path]
        return {"path": path, "ukuran": len(isi), "mtime": mtime,
                "hash": hashlib.sha256(isi).hexdigest() if len(isi) <= self.maks_hash else None}

    def tangani(self, r: httpx.Request) -> httpx.Response:
        route = r.url.path.removeprefix(AWALAN)
        h = r.headers
        if not verify(SECRET, h.get("X-Wpmgr-Signature", ""), r.method, r.url.path,
                      int(h.get("X-Wpmgr-Timestamp", "0")), h.get("X-Wpmgr-Nonce", ""), r.content):
            return _galat(401, "wpmgr_ditolak", "Tanda tangan tidak cocok.")
        n = self.hitung[route] = self.hitung.get(route, 0) + 1
        if n in self.jadwal_gagal.get(route, set()):
            return httpx.Response(500, text="galat sementara")
        badan = json.loads(r.content) if r.content and route != "/staging/unggah" else None
        if route in self.sebelum:
            self.sebelum[route](self, n, badan)
        self.diminta.append((route, badan if badan is not None else dict(r.url.params)))
        return getattr(self, "_" + route.removeprefix("/staging/").replace("-", "_"))(r, badan)

    def info_tabel(self) -> list[dict]:
        return [{"nama": t, "baris": 1, "ukuran": self.ukuran_tabel.get(t, sum(len(c) for c in isi)),
                 "mesin": "InnoDB",
                 "pk": self.pk.get(t, ["id"])} for t, isi in sorted(self.tabel.items())]

    def _manifest(self, r, badan):
        kursor = r.url.params.get("kursor")
        data = {"berkas": [], "dilewati": [], "jumlah_dilewati": 0}
        if kursor is None:
            self._ekstra_terkirim = False
            data["info"] = {**self.info, "tabel": self.info_tabel()}
        # Halaman kosong berkursor "0", "00", ...: selalu sebelum path
        # sungguhan di test, jadi tidak ada berkas yang terlewat.
        ke = 0 if kursor is None else (len(kursor) if set(kursor) == {"0"} else None)
        if ke is not None and ke < self.halaman_kosong:
            data.update(kursor="0" * (ke + 1), lagi=True)
            return _json(200, data)
        urut = [p for p in sorted(self.berkas) if kursor is None or p > kursor]
        halaman, lagi = urut[:self.halaman], len(urut) > self.halaman
        data.update(berkas=[self._entri(p) for p in halaman], kursor=halaman[-1] if lagi else None, lagi=lagi)
        if not self._ekstra_terkirim:
            # Entri anomali dan hitungan dilewati sekali per manifest.
            self._ekstra_terkirim = True
            data["berkas"] += self.ekstra_manifest
            data["jumlah_dilewati"] = self.jumlah_dilewati
        return _json(200, data)

    def _file(self, r, badan):
        if "rentang" in badan:
            x = badan["rentang"]
            if x["panjang"] > self.maks_paket:
                return _galat(413, "wpmgr_staging_terlalu_besar", "Permintaan melebihi batas 8 MB per potongan.")
            if x["path"] not in self.berkas:
                return _biner(paket.susun({"berkas": [{"path": x["path"], "hilang": True, "total": 0}]}, [b""]))
            isi, mtime = self.berkas[x["path"]]
            bagian = isi[x["dari"]:x["dari"] + min(x["panjang"], self.maks_rentang or x["panjang"])]
            return _biner(paket.susun({"berkas": [
                {"path": x["path"], "dari": x["dari"], "total": len(isi), "mtime": mtime}]}, [bagian]))
        meta, isi, lengkap, pakai = [], [], True, 0
        for i, p in enumerate(badan["berkas"]):
            if self.maks_entri is not None and i >= self.maks_entri:
                lengkap = False
                break
            if p not in self.berkas:
                meta.append({"path": p, "hilang": True})
                isi.append(b"")
                continue
            data, mtime = self.berkas[p]
            if p in self.gagal_baca:
                meta.append({"path": p, "galat": "baca"})
                isi.append(b"")
                continue
            if pakai + len(data) > self.maks_paket:
                if i == 0:
                    # Entri pertama sendiri melampaui anggaran: satu penanda,
                    # dashboard beralih ke mode rentang untuk berkas ini.
                    return _biner(paket.susun({"berkas": [
                        {"path": p, "terlalu_besar": True, "total": len(data), "mtime": mtime}],
                        "lengkap": False}, [b""]))
                lengkap = False
                break
            meta.append({"path": p, "mtime": mtime})
            isi.append(data)
            pakai += len(data)
        return _biner(paket.susun({"berkas": meta, "lengkap": lengkap}, isi))

    def _tabel(self, r, badan):
        nama, kursor = badan["tabel"], badan["kursor"] or ""
        idx = int(kursor) if kursor else 0
        potongan = self.tabel[nama]
        selesai = idx >= len(potongan) - 1
        meta = {"tabel": nama, "kursor": None if selesai else str(idx + 1), "selesai": selesai, "baris": 1,
                "mode": "pk" if self.pk.get(nama, ["id"]) else "offset", "berkas": [{"path": "sql"}]}
        return _biner(paket.susun(meta, [potongan[idx]]))

    def _tanda_air(self, r, badan):
        sumber = json.loads(json.dumps(self.tanda_air["sumber"]))
        sejak, maks = r.url.params.get("posts_sejak", ""), r.url.params.get("posts_maks", "0")
        if "posts" in sumber and _POLA_WAKTU.fullmatch(sejak) and maks.isdigit() and int(maks) > 0:
            sumber["posts"]["diubah_sejak"] = self.posts_diubah_sejak
        return _json(200, {"sumber": sumber, "diambil": 1790000000})

    def _snapshot(self, r, badan):
        hasil = {"berkas": [
            {"path": p, "ada": True, "ukuran": len(self.berkas[p][0]), "mtime": self.berkas[p][1]}
            if p in self.berkas else {"path": p, "ada": False} for p in badan["paths"]]}
        if badan.get("awal"):
            hasil["tabel"] = self.info_tabel()
            hasil["tanda_air"] = {**self.tanda_air, "diambil": 1790000000}
        return _json(200, hasil)

    # ---- sisi dorong (WPMGR_Staging_Dorong, Task 7-8) ------------------------

    def _kejadian(self, kunci: str):
        antre = self.kejadian.get(kunci)
        return antre.pop(0) if antre else None

    def _jalankan(self, r, kejadian, fungsi) -> httpx.Response:
        """Jalankan `fungsi` sesuai kejadian yang dijadwalkan untuk permintaan ini."""
        if isinstance(kejadian, httpx.Response):
            return kejadian
        if kejadian == "putus_awal":
            respons_putus(r)
        hasil = fungsi()
        if kejadian == "putus":
            respons_putus(r)
        return hasil

    def _unggah(self, r, badan):
        meta, _ = paket.urai(r.content)
        return self._jalankan(r, self._kejadian("unggah"), lambda: self._unggah_inti(r.content, meta))

    def _unggah_inti(self, data: bytes, meta: dict) -> httpx.Response:
        id_, nomor = meta["dorong_id"], meta["nomor"]
        d = self.dorongan.get(id_)
        if d is not None and d["status"] == "direbut":
            return _galat(409, "wpmgr_staging_direbut", "Dorongan ini sudah direbut dorongan lain.")
        if d is not None and d["status"] != "mengunggah":
            return _galat(409, "wpmgr_staging_urutan", "Dorongan ini sudah melewati tahap unggah.")
        if self.kunci not in (None, id_):
            return _galat(409, "wpmgr_staging_sibuk", "Dorongan lain sedang berlangsung di site ini.")
        self.kunci = id_
        if d is None:
            d = self.dorongan[id_] = {"status": "mengunggah", "potongan": {}, "hasil": {}}
            self.unggahan = d["potongan"]
        lama = d["potongan"].get(nomor)
        if lama is not None and lama != data:
            return _galat(409, "wpmgr_staging_nomor_bentrok", "Nomor potongan ini sudah dipakai dengan isi berbeda.")
        d["potongan"][nomor] = data
        return _json(200, {"ok": True, "nomor": nomor, "sha256": hashlib.sha256(data).hexdigest()})

    @staticmethod
    def _rakit(d: dict, badan) -> httpx.Response | None:
        rencana, sql, isi_baru = b"", b"", {}
        for nomor in range(badan["jumlah_potongan"]):
            if nomor not in d["potongan"]:
                return _galat(409, "wpmgr_staging_kurang", f"Potongan {nomor} belum diunggah.")
        for nomor in sorted(d["potongan"]):
            meta, bagian = paket.urai(d["potongan"][nomor])
            if meta["jenis"] == "rencana":
                rencana += bagian[0]
            elif meta["jenis"] == "sql":
                sql += bagian[0]
            else:
                for m, isi in zip(meta["berkas"], bagian):
                    isi_baru[m["path"]] = isi_baru.get(m["path"], b"") + isi
        if hashlib.sha256(rencana).hexdigest() != badan["sha256_rencana"]:
            return _galat(422, "wpmgr_staging_rencana", "Rencana dorong rusak atau tidak cocok.")
        d.update(rencana=json.loads(rencana), sql=sql, isi_baru=isi_baru)
        for b in d["rencana"]["berkas"]:
            if hashlib.sha256(isi_baru.get(b["path"], b"")).hexdigest() != b["sha256"]:
                return _galat(422, "wpmgr_staging_verifikasi", "Berkas tidak lengkap atau berbeda dari rencana: "
                              + b["path"])
        return None

    def _selesaikan_langkah(self, d: dict, langkah: str, status: str) -> httpx.Response:
        d["status"] = status
        d["hasil"][langkah] = {"selesai": True, "status": status}
        return _json(200, d["hasil"][langkah])

    def _lepas_kunci(self, id_: str) -> None:
        if self.kunci == id_:
            self.kunci = None

    def _pulihkan_produksi(self, id_: str, d: dict) -> None:
        if "cadangan" in d:
            self.berkas = dict(d["cadangan"])
            self.sql_diterapkan = d["sql_lama"]
        d["status"] = "dipulihkan"
        d["hasil"]["pulihkan"] = {"selesai": True, "status": "dipulihkan"}
        self._lepas_kunci(id_)

    def _gagal_tukar(self, id_: str, d: dict) -> httpx.Response:
        """WPMGR_Staging_Dorong::gagal_tukar(): pulihkan otomatis lalu 500 wpmgr_staging_tukar."""
        d["status"] = "menukar"
        if self.pulih_otomatis_macet:
            d["status"] = "memulihkan"
            return _galat(500, "wpmgr_staging_tukar", "Penukaran gagal; pemulihan belum selesai, lanjutkan "
                          "dengan langkah pulihkan: Berkas baru tidak dapat dipasang: wp-content/x",
                          pemulihan="memulihkan")
        self._pulihkan_produksi(id_, d)
        return _galat(500, "wpmgr_staging_tukar", "Penukaran gagal dan sudah dipulihkan otomatis: "
                      "Berkas baru tidak dapat dipasang: wp-content/x", pemulihan="dipulihkan")

    def _terapkan(self, r, badan):
        langkah = badan["langkah"]
        self.langkah.append(langkah)
        self.langkah_id.append((badan["dorong_id"], langkah))
        kejadian = self._kejadian(langkah)
        if kejadian == "gagal_putus":
            self._gagal_tukar(badan["dorong_id"], self.dorongan[badan["dorong_id"]])
            respons_putus(r)
        return self._jalankan(r, kejadian, lambda: self._terapkan_inti(badan, langkah))

    def _terapkan_inti(self, badan, langkah) -> httpx.Response:
        id_, token = badan["dorong_id"], badan.get("token")
        d = self.dorongan.get(id_)
        if d is None:
            return _galat(404, "wpmgr_staging_tidak_ada", "Dorongan tidak ditemukan.")
        if d["status"] == "direbut":
            return _galat(409, "wpmgr_staging_direbut", "Dorongan ini sudah direbut dorongan lain.")
        # Ruling F4: ulangan langkah yang sudah selesai dibalas hasil tersimpan.
        if langkah in d["hasil"] and d["status"] in STATUS_LANGKAH_SELESAI[langkah]:
            if langkah in ("tukar", "pulihkan") and d.get("token_hash") and \
                    hashlib.sha256((token or "").encode()).hexdigest() != d["token_hash"]:
                return _galat(403, "wpmgr_staging_token", "Token tidak cocok.")
            return _json(200, d["hasil"][langkah])
        if self.gagal_langkah == langkah:
            if langkah == "tukar" and d["status"] in ("siap", "terimpor"):
                d["token_hash"] = hashlib.sha256(token.encode()).hexdigest()
                return self._gagal_tukar(id_, d)
            if langkah == "siapkan":
                return _galat(422, "wpmgr_staging_verifikasi", "Berkas tidak lengkap atau berbeda dari rencana: x")
            if langkah == "impor":
                return _galat(422, "wpmgr_staging_impor", "Pernyataan SQL memuat komentar; tidak diizinkan.")
            if langkah == "pulihkan" and d["status"] in STATUS_MENYENTUH_PRODUKSI:
                d["status"] = "memulihkan"
                return _galat(500, "wpmgr_staging_pulihkan",
                              "Sebagian berkas atau tabel belum dapat dikembalikan; ulangi langkah pulihkan.")
        if langkah == "siapkan":
            if d["status"] not in ("mengunggah", "menyiapkan"):
                return _galat(409, "wpmgr_staging_urutan", "Dorongan ini tidak sedang menunggu disiapkan.")
            galat = self._rakit(d, badan)
            if galat is not None:
                return galat
            return self._selesaikan_langkah(d, "siapkan", "siap")
        if langkah == "impor":
            if not d["rencana"]["sql"] or d["status"] not in ("siap", "mengimpor"):
                return _galat(409, "wpmgr_staging_urutan", "Dorongan ini tidak menunggu impor database.")
            if self.impor_ditahan:
                d["status"] = "mengimpor"
                return _galat(409, "wpmgr_staging_ditahan", "Tabel sementara ini masih ditahan pemulihan "
                              "dorongan lain (24 jam); coba lagi nanti.")
            return self._selesaikan_langkah(d, "impor", "terimpor")
        if langkah == "tukar":
            if d["status"] == "menukar":
                if hashlib.sha256((token or "").encode()).hexdigest() != d["token_hash"]:
                    return _galat(403, "wpmgr_staging_token", "Token tukar tidak cocok.")
            elif d["status"] != ("terimpor" if d["rencana"]["sql"] else "siap"):
                return _galat(409, "wpmgr_staging_urutan", "Dorongan belum siap ditukar.")
            d.update(status="menukar", token_hash=hashlib.sha256(token.encode()).hexdigest(),
                     cadangan=dict(self.berkas), sql_lama=self.sql_diterapkan)
            for b in d["rencana"]["berkas"]:
                self.berkas[b["path"]] = (d["isi_baru"][b["path"]], b["mtime"])
            for p in d["rencana"]["hapus"]:
                if not p.startswith("wp-content/uploads/"):
                    self.berkas.pop(p, None)
            if d["rencana"]["sql"]:
                self.sql_diterapkan = d["sql"]
            return self._selesaikan_langkah(d, "tukar", "ditukar")
        if langkah == "pulihkan":
            if d["status"] in STATUS_PRA_TUKAR or d["status"] in STATUS_MENYENTUH_PRODUKSI:
                self._pulihkan_produksi(id_, d)
                return _json(200, d["hasil"]["pulihkan"])
            return _galat(409, "wpmgr_staging_urutan", "Dorongan ini tidak dapat dipulihkan lewat langkah ini.")
        if d["status"] != "ditukar":
            return _galat(409, "wpmgr_staging_urutan", "Dorongan belum ditukar.")
        return self._selesaikan_langkah(d, "selesai", "selesai")

    def _bersihkan(self, r, badan):
        return self._jalankan(r, self._kejadian("bersihkan"), lambda: self._bersihkan_inti(badan["dorong_id"]))

    def _bersihkan_inti(self, id_: str) -> httpx.Response:
        d = self.dorongan.get(id_)
        if d is not None and d["status"] not in STATUS_BOLEH_BERSIHKAN:
            return _galat(409, "wpmgr_staging_sibuk", "Dorongan sedang atau sudah diterapkan; selesaikan atau "
                          "pulihkan dulu.")
        self.dorongan.pop(id_, None)
        self._lepas_kunci(id_)
        return _json(200, {"lagi": False})


class PembantuPalsu:
    """Skrip pembantu tiruan: mencatat panggilan dan menulis apa yang ditulis skrip asli."""

    def __init__(self, dir_staging: Path) -> None:
        self.dir = Path(dir_staging)
        self.panggilan: list[tuple] = []
        self.sql = b""
        self.status_palsu = StatusPembantu(8 * GB, 200 * GB, 150 * GB, {}, {})
        self.gagal: dict[str, Exception] = {}
        self.saat_wpcli = None

    def _catat(self, nama: str, *argumen) -> None:
        self.panggilan.append((nama, *argumen))
        if nama in self.gagal:
            raise self.gagal[nama]

    def nama_panggilan(self) -> list[str]:
        return [p[0] for p in self.panggilan]

    def siapkan(self):
        self._catat("siapkan")

    def buat(self, nama, versi, site_id):
        self._catat("buat", nama, versi, str(site_id))

    def jalan(self, nama):
        self._catat("jalan", nama)

    def jeda(self, nama):
        self._catat("jeda", nama)

    def hapus(self, nama):
        self._catat("hapus", nama)

    def db_hapus(self, nama):
        self._catat("db_hapus", nama)

    def db_buat(self, nama, site_id, prefix):
        self._catat("db_buat", nama, str(site_id), prefix)
        berkas = self.dir / str(site_id) / "files" / "wp-config.php"
        berkas.parent.mkdir(parents=True, exist_ok=True)
        berkas.write_bytes(b"<?php // wp-config staging")

    def db_impor(self, nama, berkas):
        self._catat("db_impor", nama, len(berkas))
        # Seperti skrip asli: database dibuang dan dibuat ulang sebelum
        # setiap impor, jadi hasilnya hanya isi impor terakhir.
        self.sql = b"".join(Path(b).read_bytes() for b in berkas)

    def wpcli(self, nama, *argumen):
        self._catat("wpcli", nama, *argumen)
        return (self.saat_wpcli(nama, argumen) if self.saat_wpcli else None) or ""

    def router_muat(self):
        self._catat("router_muat")

    def sertifikat(self, nama):
        self._catat("sertifikat", nama)

    def status(self):
        self._catat("status")
        return self.status_palsu
