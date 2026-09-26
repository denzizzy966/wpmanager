import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from wpmgr.models import LoginGagal, Site, SiteStatus
from wpmgr.web import routes_monitoring

pytestmark = pytest.mark.integration

JAM = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def gagal(sesi, site, ip, jumlah, username="admin", jam=JAM, jalur="xmlrpc", ua="curl/8"):
    sesi.add(LoginGagal(site_id=site.id, jam=jam, ip=ip, username=username, jalur=jalur,
                        jumlah=jumlah, user_agent=ua, negara="RU" if ip else None))
    sesi.commit()


def test_agregasi_lintas_site(klien_web, sesi, site):
    lain = Site(id=uuid.uuid4(), nama="Lain", url="https://lain.test", status=SiteStatus.active,
                secret_terenkripsi=b"x")
    sesi.add(lain)
    sesi.commit()
    gagal(sesi, site, "198.51.100.7", 30)
    gagal(sesi, lain, "198.51.100.7", 12, username="root", jalur="form",
          ua="Mozilla/5.0 (Windows NT 10.0) Chrome/128.0")
    gagal(sesi, site, "203.0.113.9", 5)
    gagal(sesi, site, "", 99, username="(lainnya)")
    gagal(sesi, site, "192.0.2.1", 1000, jam=JAM - timedelta(days=3))

    d = klien_web.get("/api/keamanan/penyerang?jam=24").json()
    assert [b["ip"] for b in d] == ["198.51.100.7", "203.0.113.9"]
    teratas = d[0]
    assert teratas["jumlah"] == 42
    assert teratas["jumlah_site"] == 2
    assert teratas["site"] == "Contoh, Lain"
    assert teratas["username"] == "admin, root"
    assert teratas["jalur"] == "form, xmlrpc"
    assert teratas["skrip"] is True
    assert teratas["negara"] == "RU"


def test_jendela_waktu(klien_web, sesi, site):
    gagal(sesi, site, "192.0.2.1", 10, jam=JAM - timedelta(days=3))
    assert [b["ip"] for b in klien_web.get("/api/keamanan/penyerang?jam=168").json()] == ["192.0.2.1"]


def test_site_nonaktif_dikecualikan(klien_web, sesi, site):
    nonaktif = Site(id=uuid.uuid4(), nama="Nonaktif", url="https://nonaktif.test",
                    status=SiteStatus.disabled, secret_terenkripsi=b"x")
    sesi.add(nonaktif)
    sesi.commit()
    gagal(sesi, nonaktif, "203.0.113.55", 40)

    d = klien_web.get("/api/keamanan/penyerang?jam=24").json()
    assert "203.0.113.55" not in [b["ip"] for b in d]


def test_jumlah_query_konstan_walau_banyak_ip(klien_web, sesi, site, engine):
    """Step 1 (SUM/COUNT DISTINCT/MAX) dan Step 2 (site/username/jalur/skrip
    per IP lewat row_number()/IN) masing-masing SATU query terlepas dari
    berapa banyak IP yang lolos ke 500 besar -- bukan satu query per IP.
    Tanpa ini, agregasi lintas SEMUA site untuk IP dalam jumlah besar (mis.
    saat botnet menyerang banyak site sekaligus) bisa menjalankan ratusan
    query per pembukaan halaman."""

    def hitung_query():
        n = [0]

        def _tambah(conn, cursor, statement, parameters, context, executemany):
            n[0] += 1

        event.listen(engine, "before_cursor_execute", _tambah)
        try:
            r = klien_web.get("/api/keamanan/penyerang?jam=24")
            assert r.status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _tambah)
        return n[0]

    gagal(sesi, site, "203.0.113.1", 10)
    dengan_satu = hitung_query()

    for i in range(2, 31):
        gagal(sesi, site, f"203.0.113.{i}", 10)
    dengan_tiga_puluh = hitung_query()

    assert dengan_tiga_puluh == dengan_satu


def test_agregasi_sum_di_sql(klien_web, sesi, site, engine):
    """Bukti langsung bahwa penjumlahan per IP terjadi di SQL (GROUP BY +
    LIMIT), bukan menarik seluruh baris login_gagal mentah ke Python dulu
    baru meringkasnya di sana -- pola yang sama persis yang membuat /logins
    pernah salah sebelum Task 22 memperbaikinya, dan yang membuat endpoint
    ini bisa menarik jutaan baris ke memori saat banyak site diserang
    sekaligus. Statement utama endpoint ini WAJIB memuat GROUP BY dan LIMIT."""
    pernyataan = []

    def _rekam(conn, cursor, statement, parameters, context, executemany):
        pernyataan.append(statement)

    gagal(sesi, site, "203.0.113.9", 10)
    event.listen(engine, "before_cursor_execute", _rekam)
    try:
        assert klien_web.get("/api/keamanan/penyerang?jam=24").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _rekam)

    utama = next(s for s in pernyataan if "login_gagal" in s.lower() and "sum(" in s.lower())
    assert "group by" in utama.lower()
    assert "limit" in utama.lower()


def test_site_duplikat_nama_dihitung_terpisah(klien_web, sesi, site):
    """Nama site TIDAK unik -- dua site berbeda boleh bernama sama (mis. dua
    klien yang sama-sama memberi nama site staging mereka "Staging"). jumlah_site
    dan string `site` harus menghitung/menampilkan keduanya sebagai site
    terpisah (berdasarkan site_id), bukan menyusut jadi satu lewat nama."""
    kembar = Site(id=uuid.uuid4(), nama=site.nama, url="https://kembar.test",
                  status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(kembar)
    sesi.commit()
    gagal(sesi, site, "203.0.113.77", 10)
    gagal(sesi, kembar, "203.0.113.77", 10)

    d = klien_web.get("/api/keamanan/penyerang?jam=24").json()
    baris = next(b for b in d if b["ip"] == "203.0.113.77")
    assert baris["jumlah_site"] == 2
    assert baris["site"] == f"{site.nama}, {site.nama}"


def test_marjin_ember_batas_jendela(klien_web, sesi, site, monkeypatch):
    """Koreksi #11: LoginGagal.jam dibulatkan ke bawah oleh connector, jadi
    jendela "N jam terakhir" mundur satu jam ekstra (marjin ember) supaya
    ember yang sedang berjalan ikut terhitung. Untuk sekarang=12:05 dan
    jam=24, ambangnya (sejak) = sekarang - 24 jam - 1 jam = kemarin 11:05:
    ember yang mulai TEPAT SESUDAH ambang itu harus ikut, ember yang mulai
    lebih dari sejam SEBELUM ambang itu -- yang seluruh isinya sudah berakhir
    sebelum ambang -- harus dikecualikan."""
    sekarang = datetime(2026, 9, 27, 12, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(routes_monitoring, "_sekarang", lambda: sekarang)

    ambang = sekarang - timedelta(hours=24) - timedelta(hours=1)
    tepat_masuk = ambang + timedelta(minutes=1)
    jelas_keluar = ambang - timedelta(hours=1)

    gagal(sesi, site, "203.0.113.201", 5, jam=tepat_masuk)
    gagal(sesi, site, "203.0.113.202", 7, jam=jelas_keluar)

    ips = [b["ip"] for b in klien_web.get("/api/keamanan/penyerang?jam=24").json()]
    assert "203.0.113.201" in ips
    assert "203.0.113.202" not in ips


def test_halaman_keamanan(klien_web):
    r = klien_web.get("/keamanan")
    assert r.status_code == 200
    assert "layarKeamanan()" in r.text
    assert "DB-IP" in r.text
    assert 'href="/keamanan"' in r.text


def test_api_butuh_login(klien_web, engine):
    from fastapi.testclient import TestClient

    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), base_url="https://testserver")
    assert anon.get("/api/keamanan/penyerang").status_code == 401


def test_halaman_butuh_login(klien_web, engine):
    from fastapi.testclient import TestClient

    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    r = anon.get("/keamanan")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
