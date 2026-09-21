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


def test_setelan_lapis2_punya_default(monkeypatch):
    from pathlib import Path

    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_VAR_DIR", raising=False)
    monkeypatch.delenv("WPMGR_GEOIP_PATH", raising=False)
    monkeypatch.delenv("WPMGR_GA4_CREDENTIALS", raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.var_dir == "var"
    assert s.ga4_credentials is None
    assert s.jalur_geoip == Path("var") / "geoip" / "dbip-country-lite.mmdb"
    assert s.jalur_connector == Path("var") / "connector"


def test_setelan_lapis2_dari_env(monkeypatch, tmp_path):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_VAR_DIR", str(tmp_path))
    monkeypatch.setenv("WPMGR_GEOIP_PATH", str(tmp_path / "negara.mmdb"))
    monkeypatch.setenv("WPMGR_GA4_CREDENTIALS", str(tmp_path / "ga.json"))
    get_settings.cache_clear()
    s = get_settings()
    assert s.jalur_geoip == tmp_path / "negara.mmdb"
    assert s.jalur_connector == tmp_path / "connector"
    assert s.ga4_credentials == str(tmp_path / "ga.json")
