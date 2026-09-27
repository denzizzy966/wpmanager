import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from wpmgr.kesehatan import TAB_MASALAH, TINGKAT_MASALAH, URUTAN_CHIP, susun_kesehatan
from wpmgr.models import Site, SiteStatus, Staging, StagingUji, StatusStaging

pytestmark = pytest.mark.integration

AKAR = Path(__file__).resolve().parents[2] / "src" / "wpmgr"


def test_tab_staging_tampil_bila_fitur_aktif(klien_web, sesi, site, staging_aktif):
    site.nama = '<script>alert("x")</script>'
    sesi.commit()
    r = klien_web.get(f"/sites/{site.id}?tab=staging")
    assert r.status_code == 200
    assert "tabStaging(" in r.text
    assert "detailSite('" in r.text and "', 'staging')" in r.text
    assert 'data-nama="&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;"' in r.text
    assert "<script>alert" not in r.text
    assert "/static/app/staging.js" in r.text


def test_tab_staging_tersembunyi_bila_fitur_mati(klien_web, site, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    get_settings.cache_clear()
    r = klien_web.get(f"/sites/{site.id}?tab=staging")
    assert "tabStaging(" not in r.text
    assert "', 'ringkasan')" in r.text
    assert "/static/app/staging.js" not in r.text


def test_lencana_tab_staging(klien_web, sesi, site_staging):
    site_staging.dorong_gagal_pada = datetime.now(timezone.utc)
    sesi.commit()
    r = klien_web.get(f"/sites/{site_staging.site_id}")
    awal = r.text.index("Staging")
    assert '<span class="lencana">!</span>' in r.text[awal:awal + 200]


def test_lencana_tab_staging_kosong_bila_sehat(klien_web, sesi, site_staging):
    site_staging.status = StatusStaging.siap
    sesi.commit()
    r = klien_web.get(f"/sites/{site_staging.site_id}")
    awal = r.text.index("Staging")
    assert '<span class="lencana">' not in r.text[awal:awal + 200]


def test_halaman_update_punya_tombol_uji(klien_web, staging_aktif):
    r = klien_web.get("/updates")
    assert "Uji di staging dulu" in r.text
    assert 'data-staging="1"' in r.text
    # Pesan konfirmasi dibandingkan persis di JS, dibawa lewat data-* (autoescape).
    assert "data-pesan-konfirmasi=" in r.text


def test_halaman_update_tanpa_tombol_uji_bila_fitur_mati(klien_web, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    get_settings.cache_clear()
    r = klien_web.get("/updates")
    assert r.status_code == 200
    assert "Uji di staging dulu" not in r.text
    assert 'data-staging="0"' in r.text


def _site(sesi, nama):
    s = Site(id=uuid.uuid4(), nama=nama, url=f"https://{nama.lower()}-{uuid.uuid4().hex[:6]}.test",
             status=SiteStatus.active, secret_terenkripsi=b"x", fitur=["self_update", "events", "traffic"],
             connector_version="3.0.0")
    sesi.add(s)
    sesi.flush()
    return s


def test_chip_kesehatan_staging(sesi, staging_aktif):
    assert URUTAN_CHIP.index("dorong_gagal") < URUTAN_CHIP.index("diserang")
    assert (TINGKAT_MASALAH["dorong_gagal"], TINGKAT_MASALAH["staging_gagal"]) == (1, 2)
    assert TAB_MASALAH["dorong_gagal"] == TAB_MASALAH["staging_gagal"] == "staging"
    sites = {nama: _site(sesi, nama) for nama in ("Gagal", "Dorong", "Sehat")}
    sesi.add_all([
        Staging(site_id=sites["Gagal"].id, nama="gagal", status=StatusStaging.gagal),
        Staging(site_id=sites["Dorong"].id, nama="dorong", status=StatusStaging.siap,
                dorong_gagal_pada=datetime.now(timezone.utc)),
        Staging(site_id=sites["Sehat"].id, nama="sehat", status=StatusStaging.siap),
    ])
    sesi.commit()
    hasil = susun_kesehatan(sesi)
    per_nama = {b["nama"]: b for b in hasil["baris"]}
    assert per_nama["Dorong"]["masalah"] == ["dorong_gagal"]
    assert (per_nama["Dorong"]["tingkat"], per_nama["Dorong"]["tab"]) == (1, "staging")
    assert per_nama["Gagal"]["masalah"] == ["staging_gagal"]
    assert per_nama["Gagal"]["tingkat"] == 2
    assert per_nama["Sehat"]["staging_status"] == "siap"
    assert hasil["chip"]["dorong_gagal"] == 1 and hasil["chip"]["staging_gagal"] == 1


def test_chip_gagal_produksi_hanya_dorong_gagal(sesi, staging_aktif):
    """Gagal asal 'produksi' adalah masalah produksi: chip dorong_gagal saja, tidak ganda dengan staging_gagal."""
    a = _site(sesi, "Produksi")
    b = _site(sesi, "TanpaTanda")
    c = _site(sesi, "Salinan")
    sesi.add_all([
        Staging(site_id=a.id, nama="produksi", status=StatusStaging.gagal, gagal_asal="produksi",
                dorong_gagal_pada=datetime.now(timezone.utc)),
        # Gagal produksi tanpa penanda waktu (mis. dari reaper) tetap chip produksi.
        Staging(site_id=b.id, nama="tanpatanda", status=StatusStaging.gagal, gagal_asal="produksi"),
        Staging(site_id=c.id, nama="salinan", status=StatusStaging.gagal, gagal_asal="salinan"),
    ])
    sesi.commit()
    per_nama = {r["nama"]: r for r in susun_kesehatan(sesi)["baris"]}
    assert per_nama["Produksi"]["masalah"] == ["dorong_gagal"]
    assert per_nama["TanpaTanda"]["masalah"] == ["dorong_gagal"]
    assert per_nama["Salinan"]["masalah"] == ["staging_gagal"]


def test_chip_salinan_rusak_dan_produksi_bermasalah_dua_masalah(sesi, staging_aktif):
    """R22: salinan belum utuh dan dorongan gagal di produksi adalah dua masalah terpisah."""
    s = _site(sesi, "Dua")
    sesi.add(Staging(site_id=s.id, nama="dua", status=StatusStaging.gagal, gagal_asal="salinan",
                     dorong_gagal_pada=datetime.now(timezone.utc)))
    sesi.commit()
    baris, = susun_kesehatan(sesi)["baris"]
    assert baris["masalah"] == ["dorong_gagal", "staging_gagal"]
    assert baris["tingkat"] == 1


def test_chip_staging_mati_bila_fitur_mati(sesi, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    get_settings.cache_clear()
    s = _site(sesi, "Mati")
    sesi.add(Staging(site_id=s.id, nama="mati", status=StatusStaging.gagal,
                     dorong_gagal_pada=datetime.now(timezone.utc)))
    sesi.commit()
    baris, = susun_kesehatan(sesi)["baris"]
    assert baris["masalah"] == []
    assert baris["staging_status"] is None


def test_api_uji_membawa_catatan(klien_web, sesi, site_staging):
    """R17: halaman yang sudah gagal sebelum update tampil sebagai catatan, terpisah dari alasan."""
    sesi.add(StagingUji(site_id=site_staging.site_id, paket=[{"tipe": "plugin", "slug": "a", "dari": "1", "ke": "2"}],
                        hasil="lolos", pemeriksaan={"alasan": [], "catatan": ["/toko sudah HTTP 500 sebelum update"]}))
    sesi.commit()
    data = klien_web.get(f"/api/sites/{site_staging.site_id}/staging").json()
    assert data["uji"][0]["catatan"] == ["/toko sudah HTTP 500 sebelum update"]
    assert data["uji"][0]["alasan"] == []


def test_ui_staging_tanpa_jalan_pintas_escaping():
    """Email, alasan uji, galat, dan pesan job hanya boleh masuk DOM lewat x-text atau esc()."""
    template = (AKAR / "templates" / "_tab_staging.html").read_text(encoding="utf-8")
    js = (AKAR / "static" / "app" / "staging.js").read_text(encoding="utf-8")
    for isi in (template, js):
        assert "x-html" not in isi
        assert "innerHTML" not in isi
        assert "|safe" not in isi.replace(" ", "")
        assert "outerHTML" not in isi and "insertAdjacentHTML" not in isi
    # Kata sandi preview tidak pernah disimpan di browser.
    assert "localStorage" not in js and "sessionStorage" not in js
    # Nama site tidak pernah disisipkan Jinja ke ekspresi Alpine.
    for ekspresi in re.findall(r'(?:x-[a-z-]+|@[a-z.]+|:[a-z-]+)="([^"]*)"', template):
        assert "{{" not in ekspresi or ekspresi == "tabStaging('{{ site.id }}')", ekspresi


def test_updates_js_lencana_uji_lewat_esc():
    js = (AKAR / "static" / "app" / "updates.js").read_text(encoding="utf-8")
    assert "esc(nilai.alasan" in js
    assert "Lolos uji" in js and "Gagal uji" in js


def test_alasan_dorong_nonaktif_untuk_salinan_gagal():
    js = (AKAR / "static" / "app" / "staging.js").read_text(encoding="utf-8")
    assert "Salinan staging belum utuh (penyegaran terakhir gagal); segarkan ulang sebelum mendorong." in js


def _badan_metode(js: str, awal: str, akhir: str) -> str:
    i = js.index(awal)
    return js[i:js.index(akhir, i + len(awal))]


def test_uji_staging_tidak_mengunci_tombol_update_produksi():
    """Uji bisa berjam-jam: ia tidak boleh memakai flag `berjalan` yang menonaktifkan Update dan Muat ulang."""
    js = (AKAR / "static" / "app" / "updates.js").read_text(encoding="utf-8")
    uji = _badan_metode(js, "async ujiStaging(", "\n    },\n")
    assert "berjalan" not in uji
    assert "this.pantau()" in uji
    assert "Uji di staging diantrekan" in uji
    template = (AKAR / "templates" / "updates.html").read_text(encoding="utf-8")
    tombol_uji = next(b for b in template.splitlines() if "Uji di staging dulu" in b)
    assert "berjalan" not in tombol_uji


def test_pantau_update_tanpa_permintaan_bertumpuk():
    js = (AKAR / "static" / "app" / "updates.js").read_text(encoding="utf-8")
    pantau = _badan_metode(js, "pantau() {", "\n    },\n")
    assert "_memantau" in pantau


def test_polling_staging_berhenti_pada_galat_menetap():
    js = (AKAR / "static" / "app" / "staging.js").read_text(encoding="utf-8")
    assert "MAKS_GAGAL_BERUNTUN = 5" in js
    assert "STATUS_BERHENTI = [401, 404]" in js
    # Galat dari muat() sendiri dihapus saat muat() berikutnya berhasil.
    assert "_galatMuat" in js


def test_sso_staging_punya_tautan_cadangan():
    template = (AKAR / "templates" / "_tab_staging.html").read_text(encoding="utf-8")
    assert ':href="aman(urlSso)"' in template
    assert 'rel="noopener' in template


def test_api_uji_menormalkan_daftar(klien_web, sesi, site_staging):
    sesi.add(StagingUji(site_id=site_staging.site_id, paket=[], hasil="gagal",
                        pemeriksaan={"alasan": "bukan daftar", "catatan": [f"c{i}" for i in range(500)],
                                     "halaman": {"bukan": "daftar"}}))
    sesi.commit()
    u, = klien_web.get(f"/api/sites/{site_staging.site_id}/staging").json()["uji"]
    assert u["alasan"] == [] and u["halaman"] == []
    assert len(u["catatan"]) == 50 and u["catatan"][0] == "c0"
