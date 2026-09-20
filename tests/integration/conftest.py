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
