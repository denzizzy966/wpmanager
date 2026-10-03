import pytest

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


def _env_wajib(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://a:b@localhost/c")
    monkeypatch.setenv("WPMGR_SECRET_KEY", "kunci")
    monkeypatch.setenv("WPMGR_BASE_URL", "https://contoh.test")
    monkeypatch.setenv("WPMGR_SESSION_SECRET", "rahasia")


def test_setelan_staging_default_mati(monkeypatch):
    from pathlib import Path

    _env_wajib(monkeypatch)
    for nama in ("WPMGR_STAGING_DOMAIN", "WPMGR_STAGING_DIR", "WPMGR_STAGING_PEMBANTU",
                 "WPMGR_STAGING_PEMBANTU_AWALAN", "WPMGR_STAGING_MAKS_AKTIF",
                 "WPMGR_STAGING_JEDA_HARI", "WPMGR_STAGING_SNAPSHOT", "WPMGR_STAGING_EMAIL_ACME",
                 "WPMGR_STAGING_ROUTER_URL", "WPMGR_STAGING_MAILPIT_URL"):
        monkeypatch.delenv(nama, raising=False)
    s = Settings(_env_file=None)
    assert s.staging_domain is None
    assert s.staging_aktif is False
    assert s.jalur_staging == Path("/var/lib/wpmgr/staging")
    assert s.staging_pembantu == "/usr/local/sbin/wpmgr-staging"
    assert s.staging_pembantu_awalan is None
    assert (s.staging_maks_aktif, s.staging_jeda_hari, s.staging_snapshot) == (3, 3, 3)
    assert s.staging_email_acme is None
    assert s.staging_router_url == "http://127.0.0.1:8090"
    assert s.staging_mailpit_url == "http://127.0.0.1:8025"


def test_setelan_staging_dari_env(monkeypatch, tmp_path):
    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", " Staging.HaloSocia.my.id. ")
    monkeypatch.setenv("WPMGR_STAGING_DIR", str(tmp_path))
    monkeypatch.setenv("WPMGR_STAGING_MAKS_AKTIF", "5")
    monkeypatch.setenv("WPMGR_STAGING_ROUTER_URL", "http://localhost:8090/")
    s = Settings(_env_file=None)
    assert s.staging_domain == "staging.halosocia.my.id"
    assert s.staging_aktif is True
    assert s.jalur_staging == tmp_path
    assert s.staging_maks_aktif == 5
    assert s.staging_router_url == "http://localhost:8090"


def test_domain_staging_kosong_berarti_mati(monkeypatch):
    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", "   ")
    assert Settings(_env_file=None).staging_aktif is False


@pytest.mark.parametrize("domain", [
    "staging", "-a.b.id", "a_b.id", "a..b.id", "staging.halosocia.my.id\nevil.id", "ex ample.id", "é.id",
])
def test_domain_staging_tidak_sah_ditolak(monkeypatch, domain):
    from pydantic import ValidationError

    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", domain)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("nama,nilai", [
    ("WPMGR_STAGING_MAKS_AKTIF", "0"), ("WPMGR_STAGING_JEDA_HARI", "0"), ("WPMGR_STAGING_SNAPSHOT", "999"),
])
def test_angka_staging_dijepit_validasi(monkeypatch, nama, nilai):
    from pydantic import ValidationError

    _env_wajib(monkeypatch)
    monkeypatch.setenv(nama, nilai)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_setelan_hosting_default_mati(monkeypatch):
    from pathlib import Path

    _env_wajib(monkeypatch)
    for nama in ("WPMGR_HOSTING_IPV4", "WPMGR_HOSTING_IPV6", "WPMGR_HOSTING_DIR", "WPMGR_HOSTING_RESOLVER",
                 "WPMGR_BACKUP_TUJUAN", "WPMGR_BACKUP_HARIAN", "WPMGR_BACKUP_MINGGUAN", "WPMGR_STAGING_DOMAIN"):
        monkeypatch.delenv(nama, raising=False)
    s = Settings(_env_file=None)
    assert s.hosting_ipv4 is None and s.hosting_ipv6 is None
    assert s.hosting_aktif is False
    assert s.jalur_hosting == Path("/var/lib/wpmgr/hosting")
    assert s.daftar_resolver == ["1.1.1.1", "8.8.8.8"]
    assert (s.backup_tujuan, s.backup_harian, s.backup_mingguan) == ("lokal", 7, 4)


def test_setelan_hosting_dari_env(monkeypatch, tmp_path):
    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", "staging.halosocia.my.id")
    monkeypatch.setenv("WPMGR_STAGING_DIR", str(tmp_path / "stg"))
    monkeypatch.setenv("WPMGR_HOSTING_IPV4", " 169.58.91.181 ")
    monkeypatch.setenv("WPMGR_HOSTING_IPV6", "2a02:c207:2347:2607:0:0:0:1")
    monkeypatch.setenv("WPMGR_HOSTING_DIR", str(tmp_path / "hosting"))
    monkeypatch.setenv("WPMGR_HOSTING_RESOLVER", "9.9.9.9, 1.0.0.1")
    s = Settings(_env_file=None)
    assert s.hosting_ipv4 == "169.58.91.181"
    assert s.hosting_ipv6 == "2a02:c207:2347:2607::1"
    assert s.hosting_aktif is True
    assert s.daftar_resolver == ["9.9.9.9", "1.0.0.1"]


def test_hosting_mati_tanpa_domain_staging(monkeypatch):
    _env_wajib(monkeypatch)
    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    monkeypatch.setenv("WPMGR_HOSTING_IPV4", "169.58.91.181")
    assert Settings(_env_file=None).hosting_aktif is False


@pytest.mark.parametrize("nama,nilai", [
    ("WPMGR_HOSTING_IPV4", "169.58.91"), ("WPMGR_HOSTING_IPV4", "2a02::1"), ("WPMGR_HOSTING_IPV6", "169.58.91.181"),
    ("WPMGR_HOSTING_RESOLVER", "1.1.1.1,dns.google"), ("WPMGR_HOSTING_RESOLVER", " , "),
    ("WPMGR_BACKUP_HARIAN", "0"), ("WPMGR_BACKUP_HARIAN", "61"), ("WPMGR_BACKUP_MINGGUAN", "53"),
    ("WPMGR_BACKUP_TUJUAN", "S3!"),
])
def test_setelan_hosting_tidak_sah_ditolak(monkeypatch, nama, nilai):
    from pydantic import ValidationError

    _env_wajib(monkeypatch)
    monkeypatch.setenv(nama, nilai)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("staging,hosting", [
    ("/var/lib/wpmgr/staging", "/var/lib/wpmgr/staging"),
    ("/var/lib/wpmgr/staging", "/var/lib/wpmgr/staging/hosting"),
    ("/var/lib/wpmgr/staging", "/var/lib/wpmgr"),
    ("/var/lib/wpmgr/staging", "/var/lib/wpmgr/staging/"),
])
def test_hosting_dir_berimpit_dengan_staging_dir_ditolak(monkeypatch, staging, hosting):
    from pydantic import ValidationError

    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DIR", staging)
    monkeypatch.setenv("WPMGR_HOSTING_DIR", hosting)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_hosting_dir_berawalan_sama_tetapi_terpisah_diterima(monkeypatch):
    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DIR", "/var/lib/wpmgr/staging")
    monkeypatch.setenv("WPMGR_HOSTING_DIR", "/var/lib/wpmgr/staging-hosting")
    assert Settings(_env_file=None).hosting_dir == "/var/lib/wpmgr/staging-hosting"
