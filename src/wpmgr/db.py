from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from wpmgr.config import get_settings

# TimeZone sesi dipatok ke UTC: date_trunc('day', timestamptz) (mis. bucket
# harian uptime) membagi hari menurut TimeZone sesi Postgres, bukan UTC.
# Tanpa ini, hasilnya kebetulan benar hari ini hanya karena image Docker
# defaultnya UTC -- bukan karena kode ini menjaminnya.
engine = create_engine(
    get_settings().database_url, pool_pre_ping=True, future=True,
    connect_args={"options": "-c timezone=UTC"},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def get_session() -> Iterator[Session]:
    sesi = SessionLocal()
    try:
        yield sesi
        sesi.commit()
    except Exception:
        sesi.rollback()
        raise
    finally:
        sesi.close()
