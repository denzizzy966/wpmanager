import hashlib
import json

import pytest

from wpmgr.staging.paket import (
    BATAS_PAKET,
    MAGIC,
    MAKS_ISI,
    MAKS_META,
    PaketRusak,
    susun,
    urai,
)


def test_bolak_balik_dengan_hash_per_bagian():
    bagian = [b"", b"biner\x00\xff\x1a\n'\""]
    data = susun({"jenis": "uji", "berkas": [{"path": "a"}, {"path": "wp-content/ü.bin"}]}, bagian)
    assert data.startswith(MAGIC)
    meta, hasil = urai(data)
    assert hasil == bagian
    assert meta["berkas"][1]["sha256"] == hashlib.sha256(bagian[1]).hexdigest()
    assert meta["berkas"][0]["ukuran"] == 0
    assert meta["berkas"][1]["path"] == "wp-content/ü.bin"


def _kepala(meta) -> bytes:
    j = json.dumps(meta).encode()
    return MAGIC + f"{len(j):08x}\n".encode() + j


@pytest.mark.parametrize("data", [
    b"", b"BUKAN", MAGIC + b"zzzzzzzz\n{}", MAGIC + b"7fffffff\n{}",
    _kepala([]), _kepala({"berkas": "x"}),
    _kepala({"berkas": [{"ukuran": True, "sha256": "0" * 64}]}),
    _kepala({"berkas": [{"ukuran": 5, "sha256": "0" * 64}]}) + b"abc",
    _kepala({"berkas": [{"ukuran": 1, "sha256": "bukan-hex"}]}) + b"a",
])
def test_paket_rusak_ditolak(data):
    with pytest.raises(PaketRusak):
        urai(data)


def test_hash_salah_dan_sisa_data_ditolak():
    data = susun({"berkas": [{"path": "a"}]}, [b"abcdef"])
    with pytest.raises(PaketRusak):
        urai(data[:-1] + b"X")
    with pytest.raises(PaketRusak):
        urai(data + b"x")


def test_batas_meta_sama_dengan_connector():
    # WPMGR_Staging_Paket::MAKS_META diturunkan ke 1 MiB di connector.
    assert MAKS_META == 1024 * 1024
    j = b'{"berkas":[],"x":"' + b"a" * MAKS_META + b'"}'
    with pytest.raises(PaketRusak):
        urai(MAGIC + f"{len(j):08x}\n".encode() + j)
    with pytest.raises(ValueError):
        susun({"berkas": [], "x": "a" * MAKS_META}, [])


def test_meta_bersarang_dalam_menjadi_paket_rusak_bukan_recursionerror():
    j = b'{"berkas":[],"x":' + b"[" * 200000 + b"]" * 200000 + b"}"
    j = j[:MAKS_META]
    with pytest.raises(PaketRusak):
        urai(MAGIC + f"{len(j):08x}\n".encode() + j)


def test_batas_paket_mencakup_isi_meta_dan_kepala():
    assert MAKS_ISI == 8 * 1024 * 1024
    assert BATAS_PAKET == MAKS_ISI + MAKS_META + len(MAGIC) + 9
