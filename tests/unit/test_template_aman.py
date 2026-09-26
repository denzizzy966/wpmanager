"""Penjaga struktural: tidak ada jalan pintas yang mem-bypass escaping.

Hampir semua string yang tampil di dashboard berasal dari site klien, dan
site klien bisa saja sudah disusupi.
"""

from pathlib import Path

AKAR = Path(__file__).resolve().parents[2] / "src" / "wpmgr"


def test_template_tanpa_safe_dan_x_html():
    for berkas in (AKAR / "templates").glob("*.html"):
        isi = berkas.read_text(encoding="utf-8")
        assert "|safe" not in isi.replace(" ", ""), berkas.name
        assert "x-html" not in isi, berkas.name


def test_script_aplikasi_tanpa_innerhtml():
    for berkas in (AKAR / "static" / "app").glob("*.js"):
        assert "innerHTML" not in berkas.read_text(encoding="utf-8"), berkas.name
