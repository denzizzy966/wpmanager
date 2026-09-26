"""Penjaga struktural: tidak ada jalan pintas yang mem-bypass escaping.

Hampir semua string yang tampil di dashboard berasal dari site klien, dan
site klien bisa saja sudah disusupi.
"""

from pathlib import Path

AKAR = Path(__file__).resolve().parents[2] / "src" / "wpmgr"


def _template():
    # rglob, bukan glob: template di subdirektori (partial, makro) juga
    # merender string dari site klien.
    berkas = sorted((AKAR / "templates").rglob("*.html"))
    assert berkas, "tidak ada template yang ditemukan"
    return berkas


def test_template_tanpa_safe_dan_x_html():
    for berkas in _template():
        isi = berkas.read_text(encoding="utf-8")
        assert "|safe" not in isi.replace(" ", ""), berkas.name
        assert "x-html" not in isi, berkas.name


def test_template_tanpa_mematikan_autoescape():
    for berkas in _template():
        isi = berkas.read_text(encoding="utf-8")
        assert "autoescapefalse" not in isi.replace(" ", ""), berkas.name
        assert "Markup(" not in isi, berkas.name


def test_kode_python_tanpa_markup():
    # Markup() menandai string sebagai aman sehingga autoescape melewatinya.
    for berkas in AKAR.rglob("*.py"):
        assert "Markup(" not in berkas.read_text(encoding="utf-8"), str(berkas)


def test_script_aplikasi_tanpa_innerhtml():
    berkas_js = sorted((AKAR / "static" / "app").rglob("*.js"))
    assert berkas_js, "tidak ada script aplikasi yang ditemukan"
    for berkas in berkas_js:
        assert "innerHTML" not in berkas.read_text(encoding="utf-8"), berkas.name
