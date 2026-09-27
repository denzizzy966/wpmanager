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


def _galat(kode: int, kode_wp: str, pesan: str) -> httpx.Response:
    return _json(kode, {"code": kode_wp, "message": pesan, "data": {"status": kode}})


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
        self.gagal_langkah: str | None = None
        self.halaman_utama = 200
        self.hitung: dict[str, int] = {}
        self.diminta: list[tuple[str, object]] = []
        self.unggahan: dict[int, bytes] = {}
        self.langkah: list[str] = []
        self.sql_diterapkan = b""
        self._rencana: dict | None = None
        self._isi_baru: dict[str, bytes] = {}
        self._sql = b""
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

    def _unggah(self, r, badan):
        meta, _ = paket.urai(r.content)
        self.unggahan[meta["nomor"]] = r.content
        return _json(200, {"ok": True, "nomor": meta["nomor"], "sha256": hashlib.sha256(r.content).hexdigest()})

    def _rakit(self, badan) -> None:
        rencana, self._sql, self._isi_baru = b"", b"", {}
        for nomor in sorted(self.unggahan):
            meta, bagian = paket.urai(self.unggahan[nomor])
            if meta["jenis"] == "rencana":
                rencana += bagian[0]
            elif meta["jenis"] == "sql":
                self._sql += bagian[0]
            else:
                for m, isi in zip(meta["berkas"], bagian):
                    self._isi_baru[m["path"]] = self._isi_baru.get(m["path"], b"") + isi
        assert hashlib.sha256(rencana).hexdigest() == badan["sha256_rencana"]
        self._rencana = json.loads(rencana)

    def _terapkan(self, r, badan):
        langkah = badan["langkah"]
        self.langkah.append(langkah)
        if self.gagal_langkah == langkah:
            return _galat(500, "wpmgr_staging_tukar", "Berkas baru tidak dapat dipasang: wp-content/x")
        if langkah == "siapkan":
            self._rakit(badan)
            return _json(200, {"selesai": True, "status": "siap"})
        if langkah == "impor":
            return _json(200, {"selesai": True, "status": "terimpor"})
        if langkah == "tukar":
            for b in self._rencana["berkas"]:
                self.berkas[b["path"]] = (self._isi_baru[b["path"]], b["mtime"])
            for p in self._rencana["hapus"]:
                self.berkas.pop(p, None)
            if self._rencana["sql"]:
                self.sql_diterapkan = self._sql
            return _json(200, {"selesai": True, "status": "ditukar"})
        return _json(200, {"selesai": True, "status": {"pulihkan": "dipulihkan", "selesai": "selesai"}[langkah]})

    def _bersihkan(self, r, badan):
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
