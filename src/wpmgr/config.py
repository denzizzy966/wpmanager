import ipaddress
import os
import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
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
    # Lapis 4 (spec §14). IPv4 VPS kosong mematikan fitur hosting VPS; fitur
    # juga butuh WPMGR_STAGING_DOMAIN (host pratinjau vps-<nama>.<domain staging>).
    hosting_ipv4: str | None = Field(default=None, validation_alias="WPMGR_HOSTING_IPV4")
    hosting_ipv6: str | None = Field(default=None, validation_alias="WPMGR_HOSTING_IPV6")
    hosting_dir: str = Field(default="/var/lib/wpmgr/hosting", validation_alias="WPMGR_HOSTING_DIR")
    hosting_resolver: str = Field(default="1.1.1.1,8.8.8.8", validation_alias="WPMGR_HOSTING_RESOLVER")
    backup_tujuan: str = Field(default="lokal", validation_alias="WPMGR_BACKUP_TUJUAN")
    backup_harian: int = Field(default=7, ge=1, le=60, validation_alias="WPMGR_BACKUP_HARIAN")
    backup_mingguan: int = Field(default=4, ge=0, le=52, validation_alias="WPMGR_BACKUP_MINGGUAN")

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

    @field_validator("hosting_ipv4", mode="before")
    @classmethod
    def _ipv4_hosting(cls, v):
        teks = "" if v is None else str(v).strip()
        if not teks:
            return None
        try:
            return str(ipaddress.IPv4Address(teks))
        except ValueError:
            raise ValueError("WPMGR_HOSTING_IPV4 bukan alamat IPv4 yang sah") from None

    @field_validator("hosting_ipv6", mode="before")
    @classmethod
    def _ipv6_hosting(cls, v):
        teks = "" if v is None else str(v).strip()
        if not teks:
            return None
        try:
            return ipaddress.IPv6Address(teks).compressed
        except ValueError:
            raise ValueError("WPMGR_HOSTING_IPV6 bukan alamat IPv6 yang sah") from None

    @field_validator("hosting_resolver")
    @classmethod
    def _resolver(cls, v: str) -> str:
        # Alamat resolver publik dipakai langsung sebagai nameserver dnspython;
        # nama host di sini akan butuh resolver lain untuk diselesaikan.
        bagian = [b.strip() for b in v.split(",") if b.strip()]
        if not 1 <= len(bagian) <= 5:
            raise ValueError("WPMGR_HOSTING_RESOLVER harus berisi 1-5 alamat IP")
        try:
            return ",".join(str(ipaddress.ip_address(b)) for b in bagian)
        except ValueError:
            raise ValueError("WPMGR_HOSTING_RESOLVER hanya boleh berisi alamat IP") from None

    @field_validator("backup_tujuan")
    @classmethod
    def _tujuan_backup(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9]{1,20}", v):
            raise ValueError("WPMGR_BACKUP_TUJUAN tidak sah")
        return v

    @model_validator(mode="after")
    def _dir_terpisah(self):
        # Koreksi #12 (RFP1): pemangkasan staging menghapus setiap direktori
        # UUID di WPMGR_STAGING_DIR tanpa baris Staging. Situs produksi tidak
        # boleh pernah berada di pohon itu, dan sebaliknya.
        # Cermin `jalur_bersih` di skrip pembantu: jalur tak ternormalkan ditolak.
        mentah = self.hosting_dir
        if "//" in mentah or "/./" in mentah or mentah.endswith(("/", "/.")):
            raise ValueError("WPMGR_HOSTING_DIR harus berupa jalur yang sudah dinormalkan")
        h = os.path.normpath(self.hosting_dir)
        s = os.path.normpath(self.staging_dir)
        if h == s or h.startswith(s + os.sep) or s.startswith(h + os.sep):
            raise ValueError("WPMGR_HOSTING_DIR tidak boleh berimpit dengan WPMGR_STAGING_DIR")
        return self

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

    @property
    def hosting_aktif(self) -> bool:
        return self.staging_aktif and bool(self.hosting_ipv4)

    @property
    def jalur_hosting(self) -> Path:
        return Path(self.hosting_dir)

    @property
    def daftar_resolver(self) -> list[str]:
        return self.hosting_resolver.split(",")


@lru_cache
def get_settings() -> Settings:
    return Settings()
