from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from wpmgr.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
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
