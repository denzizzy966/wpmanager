from wpmgr.config import Settings


def test_settings_membaca_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://a:b@localhost/c")
    monkeypatch.setenv("WPMGR_SECRET_KEY", "kunci")
    monkeypatch.setenv("WPMGR_BASE_URL", "https://contoh.test")
    monkeypatch.setenv("WPMGR_SESSION_SECRET", "rahasia")
    s = Settings()
    assert s.database_url.startswith("postgresql+psycopg://")
    assert s.base_url == "https://contoh.test"


def test_base_url_tanpa_slash_di_akhir(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://a:b@localhost/c")
    monkeypatch.setenv("WPMGR_SECRET_KEY", "kunci")
    monkeypatch.setenv("WPMGR_BASE_URL", "https://contoh.test/")
    monkeypatch.setenv("WPMGR_SESSION_SECRET", "rahasia")
    assert Settings().base_url == "https://contoh.test"
