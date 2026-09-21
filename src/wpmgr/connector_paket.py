"""Paket zip connector yang dikirim dashboard lewat /self-update.

Zip dibangun sekali saat deploy (`wpmgr.cli build-connector`), bukan saat
permintaan: isinya harus sama persis untuk semua site, dan hash di
manifest-nya adalah yang diverifikasi connector sebelum memasang apa pun.
"""

import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

NAMA_DIREKTORI = "wp-manager-connector"
NAMA_ZIP = "wp-manager-connector.zip"
NAMA_MANIFEST = "manifest.json"
_DIKECUALIKAN = frozenset({"tests", "vendor", "node_modules", "__pycache__"})


def sumber_bawaan() -> Path:
    return Path(__file__).resolve().parents[2] / "connector" / NAMA_DIREKTORI


def versi_dari_header(berkas_utama: Path) -> str:
    teks = berkas_utama.read_text(encoding="utf-8")
    cocok = re.search(r"^\s*\*\s*Version:\s*(\S+)", teks, re.M)
    if cocok is None:
        raise ValueError(f"Header 'Version:' tidak ditemukan di {berkas_utama}")
    return cocok.group(1)


def _berkas_paket(sumber: Path):
    for jalur in sorted(sumber.rglob("*")):
        relatif = jalur.relative_to(sumber)
        if any(b in _DIKECUALIKAN or b.startswith(".") for b in relatif.parts):
            continue
        if jalur.is_file():
            yield jalur, relatif


def bangun_paket(sumber: Path, tujuan: Path) -> dict:
    versi = versi_dari_header(sumber / f"{NAMA_DIREKTORI}.php")
    tujuan.mkdir(parents=True, exist_ok=True)
    zip_akhir = tujuan / NAMA_ZIP
    sementara = tujuan / f"{NAMA_ZIP}.tmp"
    # Direktori puncak wajib: Plugin_Upgrader::install() memakai namanya
    # sebagai direktori tujuan di wp-content/plugins.
    with zipfile.ZipFile(sementara, "w", zipfile.ZIP_DEFLATED) as z:
        for jalur, relatif in _berkas_paket(sumber):
            z.write(jalur, f"{NAMA_DIREKTORI}/{relatif.as_posix()}")
    sementara.replace(zip_akhir)

    isi = zip_akhir.read_bytes()
    manifest = {
        "versi": versi,
        "sha256": hashlib.sha256(isi).hexdigest(),
        "ukuran": len(isi),
        "dibangun_pada": datetime.now(timezone.utc).isoformat(),
    }
    (tujuan / NAMA_MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def baca_manifest(tujuan: Path) -> dict | None:
    try:
        data = json.loads((tujuan / NAMA_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("versi") or not data.get("sha256"):
        return None
    return data


def baca_zip(tujuan: Path) -> bytes:
    return (tujuan / NAMA_ZIP).read_bytes()
