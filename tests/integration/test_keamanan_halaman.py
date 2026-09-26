import uuid
from datetime import datetime, timedelta, timezone

import pytest

from wpmgr.models import LoginGagal, Site, SiteStatus

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
