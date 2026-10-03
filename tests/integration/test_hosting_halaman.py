from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wpmgr.kesehatan import susun_kesehatan
from wpmgr.models import StatusHosting

pytestmark = pytest.mark.integration

AKAR = Path(__file__).resolve().parents[2] / "src" / "wpmgr"


def test_tab_hosting_tampil_bila_fitur_aktif(klien_web, sesi, site, hosting_aktif):
    site.nama = '<script>alert("x")</script>'
    sesi.commit()
    r = klien_web.get(f"/sites/{site.id}?tab=hosting")
    assert r.status_code == 200
    assert "tabHosting(" in r.text and "', 'hosting')" in r.text
    assert "/static/app/hosting.js" in r.text
    assert "Hosting VPS" in r.text
    assert "<script>alert" not in r.text
    # Fixture hosting_aktif juga menyalakan staging: tab Hosting VPS tepat sesudah Staging.
    assert r.text.index("Staging") < r.text.index("Hosting VPS")


def test_tab_hosting_tersembunyi_bila_fitur_mati(klien_web, site, monkeypatch, staging_aktif):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_HOSTING_IPV4", raising=False)
    get_settings.cache_clear()
    r = klien_web.get(f"/sites/{site.id}?tab=hosting")
    assert "tabHosting(" not in r.text and "/static/app/hosting.js" not in r.text
    assert "', 'ringkasan')" in r.text


def test_lencana_tab_hosting(klien_web, sesi, site_hosting):
    site_hosting.status = StatusHosting.gagal
    site_hosting.gagal_asal = "salinan"
    sesi.commit()
    r = klien_web.get(f"/sites/{site_hosting.site_id}")
    awal = r.text.index("Hosting VPS")
    assert '<span class="lencana">!</span>' in r.text[awal:awal + 120]


def test_kesehatan_memuat_chip_dan_status_hosting(sesi, site_hosting):
    sekarang = datetime.now(timezone.utc)
    site_hosting.status = StatusHosting.aktif
    site_hosting.dilayani_vps_pada = sekarang - timedelta(days=3)
    site_hosting.aktif_pada = sekarang - timedelta(days=3)
    sesi.commit()
    hasil = susun_kesehatan(sesi, sekarang)
    baris = next(b for b in hasil["baris"] if b["id"] == str(site_hosting.site_id))
    assert baris["hosting_status"] == "aktif"
    assert "backup_gagal" in baris["masalah"] and baris["tab"] == "hosting"
    assert hasil["chip"]["backup_gagal"] == 1


def test_script_hosting_memakai_x_text_dan_tanpa_innerhtml():
    js = (AKAR / "static" / "app" / "hosting.js").read_text(encoding="utf-8")
    tpl = (AKAR / "templates" / "_tab_hosting.html").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "x-html" not in tpl and "|safe" not in tpl
    for kata in ("Pindahkan ke VPS", "Pratinjau sudah benar, lanjut ke DNS", "Periksa DNS &amp; aktifkan sekarang",
                 "Kembali ke pratinjau", "Salin ulang", "Buat ulang kata sandi", "Batalkan pindah", "Periksa ulang",
                 "jangan ubah DNS", "hosting lama boleh dimatikan", "Turunkan TTL"):
        assert kata in tpl, kata


def test_hosting_tanpa_storage_dan_innerhtml():
    # Kata sandi pratinjau tidak boleh menyentuh storage browser; teks server hanya lewat x-text.
    js = (AKAR / "static" / "app" / "hosting.js").read_text(encoding="utf-8")
    tpl = (AKAR / "templates" / "_tab_hosting.html").read_text(encoding="utf-8")
    for isi in (js, tpl):
        for kata in ("localStorage", "sessionStorage", "indexedDB", "document.cookie", "innerHTML", "outerHTML",
                     "insertAdjacentHTML", "x-html", "|safe"):
            assert kata not in isi, kata
    assert "{{ site.nama" not in tpl and "{{ site.url" not in tpl
