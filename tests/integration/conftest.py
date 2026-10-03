import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from wpmgr.models import Base, Site, SiteStatus

DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://wpmgr:wpmgr@localhost:5433/wpmgr_test"
)


@pytest.fixture(scope="session")
def engine():
    dasar = DB_URL.rsplit("/", 1)[0] + "/postgres"
    adm = create_engine(dasar, isolation_level="AUTOCOMMIT", future=True)
    nama = DB_URL.rsplit("/", 1)[1]
    with adm.connect() as c:
        c.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :nama AND pid <> pg_backend_pid()"
            ),
            {"nama": nama},
        )
        c.execute(text(f'DROP DATABASE IF EXISTS "{nama}"'))
        c.execute(text(f'CREATE DATABASE "{nama}"'))
    adm.dispose()

    e = create_engine(DB_URL, future=True)
    Base.metadata.create_all(e)
    yield e
    e.dispose()


@pytest.fixture
def sesi(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = Session()
    yield s
    s.rollback()
    for tabel in reversed(Base.metadata.sorted_tables):
        s.execute(text(f'TRUNCATE TABLE "{tabel.name}" CASCADE'))
    s.commit()
    s.close()


@pytest.fixture
def site(sesi):
    s = Site(
        id=uuid.uuid4(),
        nama="Contoh",
        url=f"https://contoh-{uuid.uuid4().hex[:8]}.test",
        status=SiteStatus.active,
        secret_terenkripsi=b"x",
    )
    sesi.add(s)
    sesi.commit()
    return s


@pytest.fixture
def pengguna_uji(sesi):
    from argon2 import PasswordHasher

    from wpmgr.models import User

    u = User(id=uuid.uuid4(), email="a@b.test", nama="Uji",
             password_hash=PasswordHasher().hash("sandi"))
    sesi.add(u)
    sesi.commit()
    return u


@pytest.fixture
def klien_web(engine, monkeypatch, pengguna_uji):
    """TestClient yang sudah login. base_url https: cookie sesi bertanda Secure."""
    from fastapi.testclient import TestClient

    from wpmgr import db
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(
        db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True)
    )
    c = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    c.post("/login", data={"email": "a@b.test", "password": "sandi"})
    return c


@pytest.fixture
def var_sementara(tmp_path, monkeypatch):
    """WPMGR_VAR_DIR diarahkan ke direktori sementara untuk satu test."""
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_VAR_DIR", str(tmp_path / "var"))
    get_settings.cache_clear()
    return tmp_path / "var"


@pytest.fixture
def staging_aktif(tmp_path, monkeypatch):
    """Fitur staging menyala dengan WPMGR_STAGING_DIR di direktori sementara."""
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", "staging.contoh.id")
    monkeypatch.setenv("WPMGR_STAGING_DIR", str(tmp_path / "stg"))
    get_settings.cache_clear()
    yield tmp_path / "stg"
    get_settings.cache_clear()


@pytest.fixture
def site_staging(sesi, site, staging_aktif):
    from wpmgr.crypto import enkripsi_secret
    from wpmgr.models import Staging
    from wpmgr.staging.pembantu import hash_sandi

    site.secret_terenkripsi = enkripsi_secret("f" * 64)
    site.fitur = ["self_update", "staging"]
    st = Staging(site_id=site.id, nama="contoh-test", sandi_hash=hash_sandi("rahasia-preview"),
                 rahasia_router_terenkripsi=enkripsi_secret("e" * 64))
    sesi.add(st)
    sesi.commit()
    return st


@pytest.fixture
def hosting_aktif(tmp_path, monkeypatch):
    """Fitur hosting VPS menyala (butuh domain staging) dengan WPMGR_HOSTING_DIR sementara."""
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", "staging.contoh.id")
    monkeypatch.setenv("WPMGR_STAGING_DIR", str(tmp_path / "stg"))
    monkeypatch.setenv("WPMGR_HOSTING_IPV4", "169.58.91.181")
    monkeypatch.setenv("WPMGR_HOSTING_DIR", str(tmp_path / "hosting"))
    get_settings.cache_clear()
    yield tmp_path / "hosting"
    get_settings.cache_clear()


@pytest.fixture
def site_hosting(sesi, site, hosting_aktif):
    from wpmgr.crypto import enkripsi_secret
    from wpmgr.models import HostingVps
    from wpmgr.staging.pembantu import hash_sandi

    site.url = "https://toko.co.id"
    site.secret_terenkripsi = enkripsi_secret("f" * 64)
    site.fitur = ["self_update", "staging"]
    h = HostingVps(site_id=site.id, nama="toko-co-id", domain="toko.co.id", dengan_www=True,
                   ip_lama="93.184.216.34", sandi_hash=hash_sandi("rahasia-pratinjau"))
    sesi.add(h)
    sesi.commit()
    return h
