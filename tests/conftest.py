import os

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://wpmgr:wpmgr@localhost:5433/wpmgr"
)
os.environ.setdefault("WPMGR_SECRET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("WPMGR_BASE_URL", "https://wpmgr.test")
os.environ.setdefault("WPMGR_SESSION_SECRET", "rahasia-sesi-untuk-test")
os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://wpmgr:wpmgr@localhost:5433/wpmgr_test",
)


@pytest.fixture(autouse=True)
def _settings_bersih():
    """Tidak ada Settings ter-cache yang boleh hidup melewati test pembuatnya."""
    from wpmgr.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
