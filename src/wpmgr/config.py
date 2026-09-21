from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @field_validator("base_url")
    @classmethod
    def _tanpa_slash_akhir(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def jalur_geoip(self) -> Path:
        if self.geoip_path:
            return Path(self.geoip_path)
        return Path(self.var_dir) / "geoip" / "dbip-country-lite.mmdb"

    @property
    def jalur_connector(self) -> Path:
        return Path(self.var_dir) / "connector"


@lru_cache
def get_settings() -> Settings:
    return Settings()
