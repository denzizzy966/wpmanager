"""Format paket biner staging, sama dengan WPMGR_Staging_Paket di connector.

    b"WPMGRPAK1\\n" + 8 hex panjang meta + b"\\n" + meta JSON + bagian...

Setiap meta["berkas"][i] membawa `ukuran` dan `sha256` bagiannya (spec §6.2
"hash per potongan"). Paket dari connector adalah masukan penyerang: setiap
bentuk yang menyimpang ditolak sebagai PaketRusak, bukan dicoba dipahami.
"""

import hashlib
import hmac
import json
import re

MAGIC = b"WPMGRPAK1\n"
# Sama dengan WPMGR_Staging_Paket::MAKS_META di connector (diturunkan dari
# 4 MiB ke 1 MiB di sana): meta hanya daftar path/ukuran/sha256.
MAKS_META = 1024 * 1024
# Isi satu paket/rentang dari connector (WPMGR_Staging_File::$maks_paket).
MAKS_ISI = 8 * 1024 * 1024
# Batas stream balasan /staging/file dan /staging/tabel: isi penuh + meta
# penuh + kepala. Paket sah tidak pernah lebih besar dari ini.
BATAS_PAKET = MAKS_ISI + MAKS_META + len(MAGIC) + 9
_POLA_HEX8 = re.compile(rb"[0-9a-f]{8}")
_POLA_SHA = re.compile(r"[0-9a-f]{64}")


class PaketRusak(ValueError):
    pass


def susun(meta: dict, bagian: list[bytes]) -> bytes:
    berkas = [dict(b) for b in meta.get("berkas", [])]
    if len(berkas) != len(bagian):
        raise ValueError("Jumlah entri meta dan bagian paket tidak sama")
    for b, isi in zip(berkas, bagian):
        b["ukuran"] = len(isi)
        b["sha256"] = hashlib.sha256(isi).hexdigest()
    kepala = json.dumps({**meta, "berkas": berkas}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(kepala) > MAKS_META:
        raise ValueError("Meta paket terlalu besar")
    return MAGIC + f"{len(kepala):08x}\n".encode("ascii") + kepala + b"".join(bagian)


def urai(data: bytes) -> tuple[dict, list[bytes]]:
    awal = len(MAGIC)
    if len(data) < awal + 9 or not data.startswith(MAGIC):
        raise PaketRusak("bukan paket staging")
    hex8 = data[awal:awal + 8]
    if not _POLA_HEX8.fullmatch(hex8) or data[awal + 8:awal + 9] != b"\n":
        raise PaketRusak("kepala paket rusak")
    panjang = int(hex8, 16)
    if panjang > MAKS_META or awal + 9 + panjang > len(data):
        raise PaketRusak("panjang meta tidak sah")
    try:
        meta = json.loads(data[awal + 9:awal + 9 + panjang])
    except (ValueError, RecursionError):
        # RecursionError: JSON bersarang sangat dalam ("[[[[...") masih muat
        # dalam 1 MiB dan akan lolos sebagai galat non-SiteError.
        raise PaketRusak("meta paket bukan JSON") from None
    if not isinstance(meta, dict) or not isinstance(meta.get("berkas"), list):
        raise PaketRusak("meta paket tidak sah")
    posisi = awal + 9 + panjang
    bagian = []
    for b in meta["berkas"]:
        if not isinstance(b, dict):
            raise PaketRusak("entri paket tidak sah")
        ukuran, sha = b.get("ukuran"), b.get("sha256")
        if isinstance(ukuran, bool) or not isinstance(ukuran, int) or ukuran < 0 \
                or not isinstance(sha, str) or not _POLA_SHA.fullmatch(sha):
            raise PaketRusak("entri paket tidak sah")
        if posisi + ukuran > len(data):
            raise PaketRusak("paket terpotong")
        isi = data[posisi:posisi + ukuran]
        if not hmac.compare_digest(hashlib.sha256(isi).hexdigest(), sha):
            raise PaketRusak("hash potongan tidak cocok")
        bagian.append(isi)
        posisi += ukuran
    if posisi != len(data):
        raise PaketRusak("ada data sisa di akhir paket")
    return meta, bagian
