import pytest

from wpmgr.traffic import urai_asal


@pytest.mark.parametrize(
    "kunci, diharapkan",
    [
        # Overflow plugin (tanpa titik dua) -- kategori tak dikenal, jatuh ke "Lainnya".
        ("(lainnya)", ("Lainnya", "")),
        # Kategori dikenal tapi tanpa nama (mis. "langsung" dari plugin tak
        # pernah membawa nama sumber).
        ("langsung:", ("Langsung", "")),
        # Bentuk GA4: kategori dikenal, nama diisi teks channel GA4.
        ("langsung:Direct", ("Langsung", "Direct")),
        # String kosong: tak boleh melempar, jatuh ke "Lainnya" tanpa nama.
        ("", ("Lainnya", "")),
        # Titik dua kedua dst. ikut jadi bagian nama (partition hanya membelah
        # di titik dua PERTAMA) -- kategori "a" tak dikenal -> "Lainnya".
        ("a:b:c", ("Lainnya", "b:c")),
    ],
)
def test_urai_asal_tidak_pernah_melempar(kunci, diharapkan):
    assert urai_asal(kunci) == diharapkan
