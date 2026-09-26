import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Huruf kecil ASCII, angka, dan '-' per label, dengan fullmatch (bukan $):
# domain ini ikut menjadi nama host nginx dan nama sertifikat certbot, jadi
# karakter di luar itu -- termasuk baris baru di akhir -- tidak boleh lolos.
POLA_DOMAIN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(validation_alias="DATABASE_URL")
    secret_key: str = Field(validation_alias="WPMGR_SECRET_KEY")
    base_url: str = Field(validation_alias="WPMGR_BASE_URL")
    session_secret: str = Field(validation_alias="WPMGR_SESSION_SECRET")

    # Lapis 2. Ketiganya opsional: dashboard tanpa GeoIP atau GA4 tetap
    # berjalan penuh, hanya tanpa kolom negara dan tanpa panel GA.
    var_dir: str = Field(default="var", validation_alias="WPMGR_VAR_DIR")
    geoip_path: str | None = Field(default=None, validation_alias="WPMGR_GEOIP_PATH")
    ga4_credentials: str | None = Field(default=None, validation_alias="WPMGR_GA4_CREDENTIALS")

    # Lapis 3 (spec §10). Domain kosong mematikan seluruh fitur staging.
    staging_domain: str | None = Field(default=None, validation_alias="WPMGR_STAGING_DOMAIN")
    staging_dir: str = Field(default="/var/lib/wpmgr/staging", validation_alias="WPMGR_STAGING_DIR")
    staging_pembantu: str = Field(
        default="/usr/local/sbin/wpmgr-staging", validation_alias="WPMGR_STAGING_PEMBANTU"
    )
    # Hanya untuk pengembangan/e2e: bila diisi, dipakai sebagai pengganti
    # `sudo -n <pembantu>` (lihat Koreksi #18). Kosong di produksi.
    staging_pembantu_awalan: str | None = Field(
        default=None, validation_alias="WPMGR_STAGING_PEMBANTU_AWALAN"
    )
    staging_maks_aktif: int = Field(default=3, ge=1, le=50, validation_alias="WPMGR_STAGING_MAKS_AKTIF")
    staging_jeda_hari: int = Field(default=3, ge=1, le=365, validation_alias="WPMGR_STAGING_JEDA_HARI")
    staging_snapshot: int = Field(default=3, ge=1, le=50, validation_alias="WPMGR_STAGING_SNAPSHOT")
    # Dipakai skrip pembantu lewat staging.conf miliknya (Koreksi #7);
    # disimpan di sini hanya supaya README dan .env tetap satu sumber.
    staging_email_acme: str | None = Field(default=None, validation_alias="WPMGR_STAGING_EMAIL_ACME")
    staging_router_url: str = Field(
        default="http://127.0.0.1:8090", validation_alias="WPMGR_STAGING_ROUTER_URL"
    )
    staging_mailpit_url: str = Field(
        default="http://127.0.0.1:8025", validation_alias="WPMGR_STAGING_MAILPIT_URL"
    )

    @field_validator("base_url")
    @classmethod
    def _tanpa_slash_akhir(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("staging_router_url", "staging_mailpit_url")
    @classmethod
    def _url_tanpa_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("staging_domain", mode="before")
    @classmethod
    def _domain_staging(cls, v):
        if v is None:
            return None
        teks = str(v).strip().lower().strip(".")
        if not teks:
            return None
        if not POLA_DOMAIN.fullmatch(teks):
            raise ValueError("WPMGR_STAGING_DOMAIN bukan nama domain yang sah")
        return teks

    @field_validator("staging_pembantu_awalan", "staging_email_acme", mode="before")
    @classmethod
    def _kosong_jadi_none(cls, v):
        if v is None:
            return None
        teks = str(v).strip()
        return teks or None

    @property
    def jalur_geoip(self) -> Path:
        if self.geoip_path:
            return Path(self.geoip_path)
        return Path(self.var_dir) / "geoip" / "dbip-country-lite.mmdb"

    @property
    def jalur_connector(self) -> Path:
        return Path(self.var_dir) / "connector"

    @property
    def staging_aktif(self) -> bool:
        return bool(self.staging_domain)

    @property
    def jalur_staging(self) -> Path:
        return Path(self.staging_dir)


@lru_cache
def get_settings() -> Settings:
    return Settings()
