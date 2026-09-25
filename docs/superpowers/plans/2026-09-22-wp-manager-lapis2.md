# WP Manager Lapis 2 (Monitoring) — Rencana Implementasi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Menambahkan uptime, error PHP, riwayat login dan deteksi serangan, traffic (plugin + GA4), pembaruan connector dari dashboard, dan halaman Kesehatan ke WP Manager Lapis 1.

**Architecture:** Tetap pull-primary. Plugin connector 2.x mengumpulkan kejadian ke tabel kecil di database site, dan dashboard menariknya lewat endpoint bertanda tangan HMAC melalui job engine Lapis 1. Uptime, SSL, GA4, GeoIP, dan retensi berjalan sebagai perintah cron langsung. UI memakai Jinja2 + Alpine.js + DataGrid milik pengguna, tanpa npm dan tanpa CDN.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, httpx, maxminddb, google-auth; PHP 7.4+ (WordPress 5.5+) untuk connector; pytest, PHPUnit 9, WordPress 6.5 di Docker untuk e2e.

**Spec:** `docs/superpowers/specs/2026-09-21-wp-manager-lapis2-design.md`. Spec Lapis 1 (`docs/superpowers/specs/2026-09-20-wp-manager-lapis1-design.md`) tetap berlaku untuk semua yang tidak diubah.

## Global Constraints

- Python `>=3.10` (bukan 3.11): jangan memakai `datetime.UTC`, `match`, `Self`, `ExceptionGroup`. Pakai `timezone.utc`.
- PHP connector wajib jalan di PHP 7.4: tanpa `match`, tanpa named arguments, tanpa `str_contains`/`str_starts_with`, tanpa union type, tanpa `readonly`, tanpa nullsafe `?->`.
- WordPress minimum untuk connector 2.x: 5.5 (header `Requires at least: 5.5`).
- Tanpa npm, tanpa CDN. Frontend hanya Alpine.js dan DataGrid yang sudah di-vendor di `src/wpmgr/static/vendor/`.
- Semua komentar kode, pesan log, pesan error, dan teks UI berbahasa Indonesia, mengikuti gaya kode yang ada: komentar menjelaskan *mengapa*, bukan *apa*.
- **Semua string yang berasal dari site klien adalah masukan penyerang** (username yang dicoba, user-agent, pesan error, path, domain perujuk, path GA4). String itu dirender lewat autoescape Jinja2, `x-text`, atau `esc()` di `cellTemplate` DataGrid. Dilarang memakai `|safe` dan `x-html` di mana pun.
- Kontrak HMAC Lapis 1 tidak berubah: path yang ditandatangani adalah `/wp-json` + route, **tanpa query string**. Semua endpoint connector baru ditandatangani, kecuali `/hit`.
- Semua respons dari namespace `wpmgr/v1` membawa `Cache-Control: no-store, private` dan `X-LiteSpeed-Cache-Control: no-cache`.
- Kode connector yang berjalan di setiap request (penangkap error, pencatat login, `/hit`) dibungkus `try/catch (\Throwable)` dan tidak pernah melempar ke WordPress.
- Setiap perubahan skema tabel connector menaikkan konstanta `WPMGR_VERSI_SKEMA`.
- Klaim tentang perilaku WordPress wajib dicek ke source core di `D:\laragon\www\pacexports-wp` (WP 7.1.1, hanya-baca) dan dibuktikan e2e terhadap container WP 6.5.
- **Bukti RED ditempel mentah**, bukan dinarasikan (Ruling R28 Lapis 1). Setiap laporan task menyertakan keluaran perintah test yang gagal sebelum perbaikan dan yang lulus sesudahnya.
- **Konvensi blok kode di rencana ini:** baris `File: <path>` di atas blok kode menunjukkan berkas tujuannya. Baris itu bukan isi berkas dan tidak boleh disalin ke dalam berkas.
- Perintah test (dari akar repo, Git Bash di Windows):
  - Unit: `.venv/Scripts/python -m pytest -m "not integration and not e2e" -q`
  - Integrasi (butuh `docker compose up -d db`): `.venv/Scripts/python -m pytest -m integration -q`
  - E2E (butuh `docker compose up -d`): `.venv/Scripts/python -m pytest -m e2e -q`
  - PHP: `cd connector && vendor/bin/phpunit`
  - Lint: `.venv/Scripts/python -m ruff check .`
- Pesan commit diakhiri baris kosong lalu `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Koreksi terhadap spec yang ditemukan saat menyusun rencana

Butir-butir ini mengubah detail spec. Alasannya dicatat di sini supaya implementer dan reviewer tidak menganggapnya penyimpangan tanpa sebab.

1. **Fatal error ditangkap lewat filter `wp_php_error_args` DAN fungsi shutdown sendiri** (spec §8.2). Koreksi atas versi awal rencana ini (Ruling R9): handler fatal WordPress (`wp-settings.php:69`, dipasang sebelum mu-plugin) memanggil `wp_die()` dengan `'exit' => false` (`class-wp-fatal-error-handler.php:211-214`), jadi fungsi shutdown kita TETAP berjalan pada template bawaan. Filter `wp_php_error_args` dipertahankan sebagai jalur tambahan yang menulis lebih dulu, untuk kasus handler `wp_die` lain (plugin yang memasang `wp_die_handler` sendiri) yang benar-benar menghentikan proses. Filter itu harus mencatat fatal DAN error database sebelum menulis, karena setelah filter menulis, fungsi shutdown tidak menulis lagi. Batasan yang diketahui: site dengan drop-in `wp-content/php-error.php` memanggil `die()` sebelum filter itu, dan fatal-nya hanya tertangkap bila fungsi shutdown sempat berjalan sebelum drop-in.
2. **Deteksi "terblokir" pada uptime tidak memakai deteksi firewall Lapis 1** (spec §7.1). Deteksi Lapis 1 menganggap header `cf-ray` sebagai tanda firewall. Cloudflare menambahkan `cf-ray` ke *setiap* respons yang ia proksikan (Ruling R15 Lapis 1), termasuk halaman error 52x ketika origin mati. Memakainya berarti setiap site di balik Cloudflare yang benar-benar mati tercatat "terblokir" dan tidak pernah memicu insiden. Uptime memakai penanda sendiri yang hanya cocok dengan halaman tantangan/blokir: header `cf-mitigated`, atau isi halaman seperti "Just a moment...", "Attention Required! | Cloudflare", "Sorry, you have been blocked", atau "Wordfence".
3. **Kolom IP di `login_events` dan `login_gagal` bertipe `text`, bukan `inet`** (spec §5.1). Baris "(IP lain)" butuh nilai yang ikut dalam constraint unik. `NULL` di constraint unik PostgreSQL dianggap selalu berbeda, sehingga upsert baris itu akan menggandakan dirinya setiap kali. Nilainya disimpan sebagai string kosong `''`.
4. **Kolom `kunci` di `wpmgr_traffic` site `varchar(180)`, bukan 191, dan path dipotong 180 karakter** (spec §10.1). Primary key `(tanggal, dimensi, kunci)` dengan utf8mb4 harus muat di 767 byte, batas indeks InnoDB pada MySQL/MariaDB lama yang masih ditemui di shared hosting.
5. **Grafik memakai batang dari elemen `div`, bukan SVG** (spec §13.4). Data grafik dimuat Alpine dari API, dan `<template x-for>` di dalam `<svg>` tidak diurai sebagai `HTMLTemplateElement`, sehingga `x-for` gagal. Batang `div` dengan tinggi persentase memberi hasil yang sama tanpa library.
6. **Uptime di laporan bulanan dihitung dari durasi insiden, bukan dari tabel cek** (spec §10.4). Cek uptime dipangkas setelah 90 hari, sedangkan insiden disimpan permanen. Menghitung dari insiden membuat laporan bulan lama tetap bisa dibuat dengan angka yang konsisten.
7. **Pencatatan SSO di dashboard (spec §9.4) sudah ada di Lapis 1** (`routes_api.py`, `url_sso`, menulis `activity_log` "SSO dibuka oleh <email>"). Tidak ada task untuk itu.
8. **Container WordPress e2e tidak lagi me-*bind-mount* direktori connector** (Task 3). Self-update menimpa direktori plugin. Lewat bind mount, WordPress akan menghapus dan menulis ulang berkas di checkout repo, atau gagal karena direktori mount tidak bisa dihapus. Connector disalin ke dalam container di awal setiap sesi e2e.
9. **Satu request `xmlrpc.php` `system.multicall` hanya menghasilkan satu kejadian login gagal**, bukan ratusan seperti asumsi spec §5.1 dan §9.1. Sejak WordPress 4.4, `wp_xmlrpc_server::login()` menandai `$this->auth_failed` pada kegagalan pertama, dan percobaan berikutnya dalam request yang sama dibalas `login_prevented` tanpa memanggil `wp_authenticate()` (`wp-includes/class-wp-xmlrpc-server.php:305-315`). Agregasi per jam tetap diperlukan, karena serangan brute force mengirim banyak request. Test e2e mengharapkan satu kejadian per request multicall.
10. **`/events` hanya mengirim baris yang sudah "tenang"** (Ruling R15, Task 14). Setiap query tabel dibatasi `diubah <= time() - WPMGR_Events::CAKRAWALA` (5 detik, jam PHP), dan kursor selalu berhenti tepat di baris terakhir yang dikirim, tanpa mundur. Rancangan awal (kursor persis setelah halaman penuh, mundur 2 detik setelah tabel habis) bisa melewati baris yang di-UPDATE atau commit terlambat pada detik yang sama, dan membuat `lagi` bolak-balik. Akibatnya bagi task lain: test e2e yang memicu kejadian lalu langsung memanggil `collect_events` (Task 26: fatal error, login gagal lewat form dan XML-RPC, login berhasil, admin baru) harus menunggu dulu lebih dari 5 detik, misalnya dengan mengulang `collect_events` sampai kejadian muncul atau batas waktu 20 detik habis, jangan `sleep` tetap.
11. **Jendela status keamanan memperhitungkan ember per jam dan waktu masuk data** (spec §9.4, Ruling R17, Task 17). Pertama, `login_gagal.jam` adalah awal jam (dibulatkan ke bawah oleh connector). Filter `jam >= sekarang - 60 menit` membuang ember jam sebelumnya di setiap menit selain xx:00, sehingga serangan yang sedang berjalan terbaca "aman" hampir sepanjang jam. Jendela "60 menit terakhir" diambil sebagai `jam > sekarang - 2 jam` (setiap ember yang beririsan), dan jendela "gagal 24 jam sebelum login berhasil" sebagai `jam > waktu - 25 jam`. Keduanya sengaja bisa menghitung lebih, ke arah yang aman. Kedua, "Sudah diperiksa" dibandingkan dengan waktu data MASUK ke dashboard, bukan waktu kejadian di site. Kolom baru `login_events.dicatat_pada timestamptz NOT NULL DEFAULT now()` (migrasi ketiga), dan aturan kritis memakai `dicatat_pada > keamanan_diperiksa_pada`. Kejadian di site selalu tiba terlambat (cakrawala 5 detik ditambah interval `collect_events`), jadi admin baru yang dibuat sebelum klik tetapi baru terkumpul sesudahnya tetap harus tampil.

## Peta Berkas

**Dashboard (Python), baru:**

| Berkas | Tanggung jawab |
|---|---|
| `src/wpmgr/fitur.py` | Nama fitur yang diumumkan connector dan `punya_fitur()` |
| `src/wpmgr/versi.py` | Perbandingan versi numerik untuk penanda "connector usang" |
| `src/wpmgr/kunci.py` | Advisory lock PostgreSQL untuk perintah cron |
| `src/wpmgr/connector_paket.py` | Membangun zip connector dan manifest-nya |
| `src/wpmgr/uptime.py` | Penilaian hasil cek, transisi status, dan satu putaran uptime |
| `src/wpmgr/ssl_cek.py` | Membaca masa berlaku sertifikat |
| `src/wpmgr/uagent.py` | Mengurai user-agent (peramban, OS, skrip) |
| `src/wpmgr/geoip.py` | Pencarian negara dari DB-IP Lite dan pengunduhannya |
| `src/wpmgr/keamanan.py` | Aturan status keamanan dan status turunan error |
| `src/wpmgr/traffic.py` | GA4 Data API dan deteksi anomali |
| `src/wpmgr/kesehatan.py` | Menyusun baris dan chip halaman Kesehatan |
| `src/wpmgr/laporan.py` | Menyusun data laporan bulanan |
| `src/wpmgr/retensi.py` | Pemangkasan data monitoring dan job |
| `src/wpmgr/jobs/monitoring.py` | Handler `collect_events`, `collect_traffic`, `update_connector` |
| `src/wpmgr/web/routes_monitoring.py` | JSON API monitoring |
| `migrations/versions/c5a1e2d3f4b6_lapis2_job_type.py` | Nilai enum `job_type` baru |
| `migrations/versions/d6b2f3e4a5c7_lapis2_monitoring.py` | Tabel dan kolom Lapis 2 |
| `src/wpmgr/templates/kesehatan.html`, `keamanan.html`, `laporan.html`, `_batang.html` | Halaman baru dan macro grafik batang |
| `src/wpmgr/static/app/kesehatan.js`, `detail.js`, `keamanan.js` | Komponen Alpine |

**Dashboard, diubah:** `config.py`, `models.py`, `site_client.py`, `errors.py`, `jobs/handlers.py`, `jobs/queue.py`, `cli.py`, `web/app.py`, `web/routes_pages.py`, `web/routes_api.py`, `templates/base.html`, `templates/site_detail.html`, `templates/site_new.html`, `static/app/sites.js`, `static/app/app.css`, `pyproject.toml`, `.env.example`, `deploy/crontab`, `README.md`, `docker-compose.yml`.

**Connector (PHP), baru:** `includes/class-wpmgr-skema.php`, `includes/class-wpmgr-selfupdate.php`, `includes/class-wpmgr-penangkap.php`, `includes/class-wpmgr-ip.php`, `includes/cloudflare-ip.php`, `includes/class-wpmgr-login.php`, `includes/class-wpmgr-events.php`, `includes/class-wpmgr-traffic.php`, `uninstall.php`; test `connector/tests/{Skema,SelfUpdate,Penangkap,Ip,Login,Events,Traffic}Test.php`.

**Connector, diubah:** `wp-manager-connector.php`, `includes/class-wpmgr-rest.php`, `includes/class-wpmgr-settings.php`, `includes/class-wpmgr-sso.php`, `connector/tests/bootstrap.php`.

**Test:** `tests/unit/test_{fitur_versi,connector_paket,uptime,ssl_cek,uagent,geoip,keamanan_status,traffic_ga4}.py`; `tests/integration/test_{models_lapis2,kemampuan,update_connector,uptime_putaran,ssl_semua,collect_events,collect_traffic,keamanan,ga4_simpan,kesehatan,api_monitoring,laporan,keamanan_halaman,retensi}.py`; `tests/e2e/test_monitoring.py`.

## Urutan dan ketergantungan

Fase A (Task 1–4) fondasi → Fase B (5–7) pembaruan connector → Fase C (8–10) uptime → Fase D (11–14) penangkapan di connector → Fase E (15–17) penyimpanan di dashboard → Fase F (18–20) traffic → Fase G (21–24) UI → Fase H (25–26) operasi dan e2e. Setiap task hanya bergantung pada task bernomor lebih kecil.

---

## Fase A — Fondasi

### Task 1: Skema dashboard, konfigurasi, dan dependensi

**Files:**
- Modify: `pyproject.toml`, `.env.example`, `src/wpmgr/config.py`, `src/wpmgr/models.py`
- Create: `migrations/versions/c5a1e2d3f4b6_lapis2_job_type.py`, `migrations/versions/d6b2f3e4a5c7_lapis2_monitoring.py`
- Test: `tests/unit/test_config.py` (tambah), `tests/integration/test_models_lapis2.py`

**Interfaces:**
- Produces:
  - `Settings.var_dir: str` (default `"var"`), `Settings.geoip_path: str | None`, `Settings.ga4_credentials: str | None`, properti `Settings.jalur_geoip -> Path`, `Settings.jalur_connector -> Path`.
  - `JobType.collect_events`, `JobType.collect_traffic`, `JobType.update_connector`.
  - Enum `UptimeStatus` (`belum_dicek`, `naik`, `mati`, `terblokir`) dan `UptimeHasil` (`naik`, `gagal`, `terblokir`).
  - Kolom baru `Site`: `fitur: list[str]`, `mode_penangkap`, `percayai_xff`, `events_kursor`, `traffic_diambil_pada`, `uptime_status`, `uptime_sejak`, `uptime_gagal_beruntun`, `ssl_kedaluwarsa`, `ssl_dicek_pada`, `ssl_error`, `keamanan_diperiksa_pada`, `ga4_property_id`, `ga4_diambil_pada`, `ga4_error`.
  - Model `UptimePutaran`, `UptimeCheck`, `UptimeInsiden`, `CatatanError` (tabel `site_errors`), `KejadianLogin` (`login_events`), `LoginGagal` (`login_gagal`), `TrafficHarian`, `TrafficRincian`.

- [ ] **Step 1: Tambah dependensi.** Di `pyproject.toml`, tambahkan tiga baris ke `dependencies` setelah `"python-multipart>=0.0.9",`:

```toml
    "maxminddb>=2.6",
    "google-auth>=2.30",
    "requests>=2.32",
```

Lalu jalankan: `.venv/Scripts/pip install -e ".[dev]"`. Expected: berakhir dengan `Successfully installed ...` yang memuat `maxminddb` dan `google-auth`.

- [ ] **Step 2: Tulis test konfigurasi yang gagal.** Tambahkan ke akhir `tests/unit/test_config.py`:

```python
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
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_config.py -q`. Expected: 2 gagal dengan `AttributeError: 'Settings' object has no attribute 'var_dir'`.

- [ ] **Step 4: Implementasikan konfigurasi.** Ganti isi `src/wpmgr/config.py`:

File: `src/wpmgr/config.py`
```python
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
```

Tambahkan ke akhir `.env.example`:

```
# Lapis 2 (semuanya opsional)
# WPMGR_VAR_DIR=var
# WPMGR_GEOIP_PATH=var/geoip/dbip-country-lite.mmdb
# WPMGR_GA4_CREDENTIALS=/opt/wpmgr/ga4-service-account.json
```

- [ ] **Step 5: Jalankan dan pastikan lulus.** Run: `.venv/Scripts/python -m pytest tests/unit/test_config.py -q`. Expected: semua lulus.

- [ ] **Step 6: Tulis test model yang gagal.**

File: `tests/integration/test_models_lapis2.py`
```python
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from wpmgr.models import (
    CatatanError,
    JobType,
    KejadianLogin,
    LoginGagal,
    TrafficHarian,
    TrafficRincian,
    UptimeCheck,
    UptimeHasil,
    UptimeInsiden,
    UptimePutaran,
    UptimeStatus,
)

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)


def test_site_baru_punya_default_lapis2(sesi, site):
    sesi.refresh(site)
    assert site.fitur == []
    assert site.uptime_status == UptimeStatus.belum_dicek
    assert site.uptime_gagal_beruntun == 0


def test_job_type_lapis2_ada():
    assert {"collect_events", "collect_traffic", "update_connector"} <= {j.value for j in JobType}


def test_uptime_check_dan_putaran(sesi, site):
    p = UptimePutaran(jumlah_site=1, jumlah_gagal=0, gangguan_dashboard=False)
    sesi.add(p)
    sesi.flush()
    sesi.add(UptimeCheck(putaran_id=p.id, site_id=site.id, hasil=UptimeHasil.naik,
                         http_status=200, waktu_ms=120))
    sesi.commit()
    assert sesi.query(UptimeCheck).count() == 1


def test_hanya_satu_insiden_terbuka_per_site(sesi, site):
    sesi.add(UptimeInsiden(site_id=site.id, mulai=SEKARANG, penyebab="HTTP 500"))
    sesi.commit()
    sesi.add(UptimeInsiden(site_id=site.id, mulai=SEKARANG, penyebab="HTTP 502"))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()


def test_insiden_selesai_tidak_menghalangi_yang_baru(sesi, site):
    sesi.add(UptimeInsiden(site_id=site.id, mulai=SEKARANG, selesai=SEKARANG,
                           penyebab="HTTP 500"))
    sesi.add(UptimeInsiden(site_id=site.id, mulai=SEKARANG, penyebab="HTTP 502"))
    sesi.commit()


def test_sidik_jari_error_unik_per_site(sesi, site):
    def baris():
        return CatatanError(
            site_id=site.id, sidik_jari="a" * 32, tingkat="fatal", komponen_tipe="plugin",
            komponen_slug="elementor", pesan="x", jumlah=1,
            pertama_terlihat=SEKARANG, terakhir_terlihat=SEKARANG,
        )

    sesi.add(baris())
    sesi.commit()
    sesi.add(baris())
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()


def test_login_gagal_ip_kosong_ikut_constraint_unik(sesi, site):
    # Baris "(IP lain)" memakai '' dan harus bertabrakan dengan dirinya
    # sendiri -- itulah alasan kolom ini text, bukan inet yang boleh NULL.
    for _ in range(2):
        sesi.add(LoginGagal(site_id=site.id, jam=SEKARANG, ip="", username="(lainnya)",
                            jalur="form", jumlah=1))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()


def test_kejadian_login_unik_per_id_di_site(sesi, site):
    for _ in range(2):
        sesi.add(KejadianLogin(site_id=site.id, id_di_site=7, waktu=SEKARANG,
                               jenis="berhasil", username="admin"))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()


def test_traffic_harian_dan_rincian(sesi, site):
    sesi.add(TrafficHarian(site_id=site.id, tanggal=date(2026, 9, 21), sumber="plugin",
                           kunjungan=10, pengunjung=7))
    sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 9, 21), sumber="plugin",
                            dimensi="halaman", kunci="/", kunjungan=4))
    sesi.commit()


def test_hapus_site_ikut_menghapus_data_monitoring(sesi, site):
    sesi.add(CatatanError(site_id=site.id, sidik_jari="b" * 32, tingkat="warning",
                          komponen_tipe="core", pesan="x", jumlah=1,
                          pertama_terlihat=SEKARANG, terakhir_terlihat=SEKARANG))
    sesi.commit()
    sesi.delete(site)
    sesi.commit()
    assert sesi.query(CatatanError).count() == 0

```

- [ ] **Step 7: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_models_lapis2.py -q`. Expected: error koleksi `ImportError: cannot import name 'CatatanError' from 'wpmgr.models'`.

- [ ] **Step 8: Implementasikan model.** Di `src/wpmgr/models.py`:

1. Ganti blok impor sqlalchemy menjadi:

```python
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
```

2. Tambahkan tiga anggota ke `JobType` setelah `verify_site = "verify_site"`:

```python
    collect_events = "collect_events"
    collect_traffic = "collect_traffic"
    update_connector = "update_connector"
```

3. Tambahkan dua enum setelah kelas `JobStatus`:

```python
class UptimeStatus(str, enum.Enum):
    belum_dicek = "belum_dicek"
    naik = "naik"
    mati = "mati"
    terblokir = "terblokir"


class UptimeHasil(str, enum.Enum):
    naik = "naik"
    gagal = "gagal"
    terblokir = "terblokir"
```

4. Tambahkan kolom berikut ke kelas `Site`, setelah `last_error` dan sebelum `dibuat_pada`:

```python
    # Lapis 2 -- dilaporkan connector (bagian 11.3 spec Lapis 2)
    fitur: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'")
    )
    mode_penangkap: Mapped[str | None] = mapped_column(Text)
    percayai_xff: Mapped[bool | None] = mapped_column(Boolean)
    events_kursor: Mapped[str | None] = mapped_column(Text)
    traffic_diambil_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Lapis 2 -- uptime dan SSL
    uptime_status: Mapped[UptimeStatus] = mapped_column(
        Enum(UptimeStatus, name="uptime_status"), nullable=False,
        default=UptimeStatus.belum_dicek, server_default="belum_dicek",
    )
    uptime_sejak: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uptime_gagal_beruntun: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ssl_kedaluwarsa: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ssl_dicek_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ssl_error: Mapped[str | None] = mapped_column(Text)
    # Lapis 2 -- keamanan dan GA4
    keamanan_diperiksa_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ga4_property_id: Mapped[str | None] = mapped_column(Text)
    ga4_diambil_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ga4_error: Mapped[str | None] = mapped_column(Text)
```

5. Tambahkan `__table_args__` ke `ActivityLog` tepat di bawah `__tablename__ = "activity_log"`:

```python
    # Laporan bulanan dan tab Aktivitas membaca per site menurut waktu
    # (R60 Lapis 1, menjadi wajib di Lapis 2).
    __table_args__ = (Index("ix_activity_log_site_dibuat", "site_id", "dibuat_pada"),)
```

6. Tambahkan model-model baru di akhir berkas:

```python
class UptimePutaran(Base):
    __tablename__ = "uptime_putaran"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mulai: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    jumlah_site: Mapped[int] = mapped_column(Integer, nullable=False)
    jumlah_gagal: Mapped[int] = mapped_column(Integer, nullable=False)
    gangguan_dashboard: Mapped[bool] = mapped_column(Boolean, nullable=False)


class UptimeCheck(Base):
    __tablename__ = "uptime_checks"
    __table_args__ = (Index("ix_uptime_checks_site_dicek", "site_id", "dicek_pada"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    putaran_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("uptime_putaran.id", ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    dicek_pada: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    hasil: Mapped[UptimeHasil] = mapped_column(Enum(UptimeHasil, name="uptime_hasil"), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    waktu_ms: Mapped[int | None] = mapped_column(Integer)
    pesan: Mapped[str | None] = mapped_column(Text)


class UptimeInsiden(Base):
    __tablename__ = "uptime_insiden"
    __table_args__ = (
        Index("ix_uptime_insiden_site_mulai", "site_id", "mulai"),
        # Paling banyak satu insiden terbuka per site; penjaga terakhir bila
        # dua putaran uptime entah bagaimana berjalan bersamaan.
        Index("uq_uptime_insiden_terbuka", "site_id", unique=True,
              postgresql_where=text("selesai IS NULL")),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    mulai: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    selesai: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    penyebab: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)


class CatatanError(Base):
    """Satu error PHP unik (per sidik jari) di satu site."""

    __tablename__ = "site_errors"
    __table_args__ = (
        UniqueConstraint("site_id", "sidik_jari", name="uq_site_errors_sidik"),
        Index("ix_site_errors_site_terakhir", "site_id", "terakhir_terlihat"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    sidik_jari: Mapped[str] = mapped_column(Text, nullable=False)
    tingkat: Mapped[str] = mapped_column(Text, nullable=False)
    komponen_tipe: Mapped[str] = mapped_column(Text, nullable=False)
    komponen_slug: Mapped[str | None] = mapped_column(Text)
    pesan: Mapped[str] = mapped_column(Text, nullable=False)
    file: Mapped[str | None] = mapped_column(Text)
    baris: Mapped[int | None] = mapped_column(Integer)
    konteks: Mapped[dict | None] = mapped_column(JSONB)
    jumlah: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pertama_terlihat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terakhir_terlihat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    setelah_update: Mapped[dict | None] = mapped_column(JSONB)
    ditandai_selesai_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KejadianLogin(Base):
    """Login berhasil dan kemunculan administrator, satu baris per kejadian."""

    __tablename__ = "login_events"
    __table_args__ = (
        UniqueConstraint("site_id", "id_di_site", name="uq_login_events_id_site"),
        Index("ix_login_events_site_waktu", "site_id", "waktu"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    id_di_site: Mapped[int] = mapped_column(BigInteger, nullable=False)
    waktu: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    jenis: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(Text)
    lewat_cloudflare: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    negara: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    jalur: Mapped[str | None] = mapped_column(Text)


class LoginGagal(Base):
    """Login gagal teragregasi per jam per (IP, username, jalur)."""

    __tablename__ = "login_gagal"
    __table_args__ = (
        UniqueConstraint("site_id", "jam", "ip", "username", "jalur", name="uq_login_gagal_kunci"),
        Index("ix_login_gagal_site_jam", "site_id", "jam"),
        Index("ix_login_gagal_ip_jam", "ip", "jam"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    jam: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # '' = baris "(IP lain)"; lihat catatan koreksi #3 di rencana.
    ip: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    jalur: Mapped[str] = mapped_column(Text, nullable=False)
    jumlah: Mapped[int] = mapped_column(Integer, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text)
    negara: Mapped[str | None] = mapped_column(Text)


class TrafficHarian(Base):
    __tablename__ = "traffic_harian"
    __table_args__ = (PrimaryKeyConstraint("site_id", "tanggal", "sumber"),)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    tanggal: Mapped[date] = mapped_column(Date, nullable=False)
    sumber: Mapped[str] = mapped_column(Text, nullable=False)
    kunjungan: Mapped[int] = mapped_column(Integer, nullable=False)
    pengunjung: Mapped[int] = mapped_column(Integer, nullable=False)


class TrafficRincian(Base):
    __tablename__ = "traffic_rincian"
    __table_args__ = (PrimaryKeyConstraint("site_id", "tanggal", "sumber", "dimensi", "kunci"),)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    tanggal: Mapped[date] = mapped_column(Date, nullable=False)
    sumber: Mapped[str] = mapped_column(Text, nullable=False)
    dimensi: Mapped[str] = mapped_column(Text, nullable=False)
    kunci: Mapped[str] = mapped_column(Text, nullable=False)
    kunjungan: Mapped[int] = mapped_column(Integer, nullable=False)
```

Juga tambahkan `from datetime import date, datetime` menggantikan `from datetime import datetime` di puncak berkas.

- [ ] **Step 9: Jalankan dan pastikan lulus.** Run: `.venv/Scripts/python -m pytest tests/integration/test_models_lapis2.py tests/integration/test_models.py -q`. Expected: semua lulus.

- [ ] **Step 10: Tulis migrasi nilai enum.**

File: `migrations/versions/c5a1e2d3f4b6_lapis2_job_type.py`
```python
"""lapis 2: nilai job_type baru

Revision ID: c5a1e2d3f4b6
Revises: b27f9c3e1a04
Create Date: 2026-09-22 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "c5a1e2d3f4b6"
down_revision: str | Sequence[str] | None = "b27f9c3e1a04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nilai enum yang baru ditambahkan tidak boleh dipakai di transaksi yang
    # sama dengan penambahannya. Revisi ini berdiri sendiri dan memakai
    # autocommit supaya revisi berikutnya (dan worker) bisa langsung
    # memakainya.
    with op.get_context().autocommit_block():
        for nilai in ("collect_events", "collect_traffic", "update_connector"):
            op.execute(f"ALTER TYPE job_type ADD VALUE IF NOT EXISTS '{nilai}'")


def downgrade() -> None:
    # PostgreSQL tidak menyediakan penghapusan nilai enum. Nilai yang tidak
    # dipakai tidak berbahaya, jadi downgrade sengaja tidak melakukan apa-apa.
    pass
```

- [ ] **Step 11: Tulis migrasi tabel.**

File: `migrations/versions/d6b2f3e4a5c7_lapis2_monitoring.py`
```python
"""lapis 2: tabel dan kolom monitoring

Revision ID: d6b2f3e4a5c7
Revises: c5a1e2d3f4b6
Create Date: 2026-09-22 00:05:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6b2f3e4a5c7"
down_revision: str | Sequence[str] | None = "c5a1e2d3f4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPTIME_STATUS = ("belum_dicek", "naik", "mati", "terblokir")
UPTIME_HASIL = ("naik", "gagal", "terblokir")


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*UPTIME_STATUS, name="uptime_status").create(bind, checkfirst=False)
    sa.Enum(*UPTIME_HASIL, name="uptime_hasil").create(bind, checkfirst=False)
    status_t = postgresql.ENUM(*UPTIME_STATUS, name="uptime_status", create_type=False)
    hasil_t = postgresql.ENUM(*UPTIME_HASIL, name="uptime_hasil", create_type=False)
    ts = sa.DateTime(timezone=True)

    op.add_column("sites", sa.Column("fitur", postgresql.ARRAY(sa.Text()), nullable=False,
                                     server_default=sa.text("'{}'")))
    op.add_column("sites", sa.Column("mode_penangkap", sa.Text()))
    op.add_column("sites", sa.Column("percayai_xff", sa.Boolean()))
    op.add_column("sites", sa.Column("events_kursor", sa.Text()))
    op.add_column("sites", sa.Column("traffic_diambil_pada", ts))
    op.add_column("sites", sa.Column("uptime_status", status_t, nullable=False,
                                     server_default="belum_dicek"))
    op.add_column("sites", sa.Column("uptime_sejak", ts))
    op.add_column("sites", sa.Column("uptime_gagal_beruntun", sa.Integer(), nullable=False,
                                     server_default="0"))
    op.add_column("sites", sa.Column("ssl_kedaluwarsa", ts))
    op.add_column("sites", sa.Column("ssl_dicek_pada", ts))
    op.add_column("sites", sa.Column("ssl_error", sa.Text()))
    op.add_column("sites", sa.Column("keamanan_diperiksa_pada", ts))
    op.add_column("sites", sa.Column("ga4_property_id", sa.Text()))
    op.add_column("sites", sa.Column("ga4_diambil_pada", ts))
    op.add_column("sites", sa.Column("ga4_error", sa.Text()))

    site_fk = sa.ForeignKey("sites.id", ondelete="CASCADE")

    op.create_table(
        "uptime_putaran",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("mulai", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("jumlah_site", sa.Integer(), nullable=False),
        sa.Column("jumlah_gagal", sa.Integer(), nullable=False),
        sa.Column("gangguan_dashboard", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "uptime_checks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("putaran_id", sa.BigInteger(),
                  sa.ForeignKey("uptime_putaran.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk, nullable=False),
        sa.Column("dicek_pada", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("hasil", hasil_t, nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("waktu_ms", sa.Integer()),
        sa.Column("pesan", sa.Text()),
    )
    op.create_index("ix_uptime_checks_site_dicek", "uptime_checks", ["site_id", "dicek_pada"])
    op.create_table(
        "uptime_insiden",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk, nullable=False),
        sa.Column("mulai", ts, nullable=False),
        sa.Column("selesai", ts),
        sa.Column("penyebab", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer()),
    )
    op.create_index("ix_uptime_insiden_site_mulai", "uptime_insiden", ["site_id", "mulai"])
    op.create_index("uq_uptime_insiden_terbuka", "uptime_insiden", ["site_id"], unique=True,
                    postgresql_where=sa.text("selesai IS NULL"))
    op.create_table(
        "site_errors",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk, nullable=False),
        sa.Column("sidik_jari", sa.Text(), nullable=False),
        sa.Column("tingkat", sa.Text(), nullable=False),
        sa.Column("komponen_tipe", sa.Text(), nullable=False),
        sa.Column("komponen_slug", sa.Text()),
        sa.Column("pesan", sa.Text(), nullable=False),
        sa.Column("file", sa.Text()),
        sa.Column("baris", sa.Integer()),
        sa.Column("konteks", postgresql.JSONB()),
        sa.Column("jumlah", sa.BigInteger(), nullable=False),
        sa.Column("pertama_terlihat", ts, nullable=False),
        sa.Column("terakhir_terlihat", ts, nullable=False),
        sa.Column("setelah_update", postgresql.JSONB()),
        sa.Column("ditandai_selesai_pada", ts),
        sa.UniqueConstraint("site_id", "sidik_jari", name="uq_site_errors_sidik"),
    )
    op.create_index("ix_site_errors_site_terakhir", "site_errors", ["site_id", "terakhir_terlihat"])
    op.create_table(
        "login_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk, nullable=False),
        sa.Column("id_di_site", sa.BigInteger(), nullable=False),
        sa.Column("waktu", ts, nullable=False),
        sa.Column("jenis", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("role", sa.Text()),
        sa.Column("ip", sa.Text()),
        sa.Column("lewat_cloudflare", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("negara", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("jalur", sa.Text()),
        sa.UniqueConstraint("site_id", "id_di_site", name="uq_login_events_id_site"),
    )
    op.create_index("ix_login_events_site_waktu", "login_events", ["site_id", "waktu"])
    op.create_table(
        "login_gagal",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk, nullable=False),
        sa.Column("jam", ts, nullable=False),
        sa.Column("ip", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("jalur", sa.Text(), nullable=False),
        sa.Column("jumlah", sa.Integer(), nullable=False),
        sa.Column("user_agent", sa.Text()),
        sa.Column("negara", sa.Text()),
        sa.UniqueConstraint("site_id", "jam", "ip", "username", "jalur",
                            name="uq_login_gagal_kunci"),
    )
    op.create_index("ix_login_gagal_site_jam", "login_gagal", ["site_id", "jam"])
    op.create_index("ix_login_gagal_ip_jam", "login_gagal", ["ip", "jam"])
    op.create_table(
        "traffic_harian",
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk, nullable=False),
        sa.Column("tanggal", sa.Date(), nullable=False),
        sa.Column("sumber", sa.Text(), nullable=False),
        sa.Column("kunjungan", sa.Integer(), nullable=False),
        sa.Column("pengunjung", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("site_id", "tanggal", "sumber"),
    )
    op.create_table(
        "traffic_rincian",
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk, nullable=False),
        sa.Column("tanggal", sa.Date(), nullable=False),
        sa.Column("sumber", sa.Text(), nullable=False),
        sa.Column("dimensi", sa.Text(), nullable=False),
        sa.Column("kunci", sa.Text(), nullable=False),
        sa.Column("kunjungan", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("site_id", "tanggal", "sumber", "dimensi", "kunci"),
    )
    op.create_index("ix_activity_log_site_dibuat", "activity_log", ["site_id", "dibuat_pada"])


def downgrade() -> None:
    op.drop_index("ix_activity_log_site_dibuat", table_name="activity_log")
    for tabel in ("traffic_rincian", "traffic_harian", "login_gagal", "login_events",
                  "site_errors", "uptime_insiden", "uptime_checks", "uptime_putaran"):
        op.drop_table(tabel)
    for kolom in ("ga4_error", "ga4_diambil_pada", "ga4_property_id", "keamanan_diperiksa_pada",
                  "ssl_error", "ssl_dicek_pada", "ssl_kedaluwarsa", "uptime_gagal_beruntun",
                  "uptime_sejak", "uptime_status", "traffic_diambil_pada", "events_kursor",
                  "percayai_xff", "mode_penangkap", "fitur"):
        op.drop_column("sites", kolom)
    sa.Enum(name="uptime_hasil").drop(op.get_bind(), checkfirst=False)
    sa.Enum(name="uptime_status").drop(op.get_bind(), checkfirst=False)
```

- [ ] **Step 12: Verifikasi migrasi di database kosong.** Dari akar repo (Git Bash), dengan `db` menyala:

```bash
docker compose exec -T db psql -U wpmgr -d postgres -c "DROP DATABASE IF EXISTS wpmgr_migrasi" -c "CREATE DATABASE wpmgr_migrasi"
export DATABASE_URL=postgresql+psycopg://wpmgr:wpmgr@localhost:5433/wpmgr_migrasi WPMGR_SECRET_KEY=x WPMGR_BASE_URL=https://x.test WPMGR_SESSION_SECRET=x
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m alembic downgrade c5a1e2d3f4b6
.venv/Scripts/python -m alembic upgrade head
docker compose exec -T db psql -U wpmgr -d wpmgr_migrasi -c "\d site_errors" -c "SELECT unnest(enum_range(NULL::job_type))"
```

Expected: ketiga perintah alembic selesai tanpa error; `\d site_errors` menampilkan tabel dengan `uq_site_errors_sidik`; enum `job_type` memuat `collect_events`, `collect_traffic`, `update_connector`. Tempel keluarannya di laporan task.

- [ ] **Step 13: Jalankan seluruh suite unit dan integrasi.** Expected: semua lulus (tidak ada test Lapis 1 yang rusak).

- [ ] **Step 14: Commit.**

```bash
git add pyproject.toml .env.example src/wpmgr/config.py src/wpmgr/models.py migrations/versions/c5a1e2d3f4b6_lapis2_job_type.py migrations/versions/d6b2f3e4a5c7_lapis2_monitoring.py tests/unit/test_config.py tests/integration/test_models_lapis2.py
git commit -m "feat: skema dan konfigurasi dashboard untuk Lapis 2"
```

### Task 2: Fondasi connector 2.0 (skema site, pengumuman fitur, header anti-cache, setelan proxy, uninstall)

**Files:**
- Modify: `connector/wp-manager-connector/wp-manager-connector.php`, `includes/class-wpmgr-rest.php`, `includes/class-wpmgr-settings.php`, `connector/tests/bootstrap.php`
- Create: `connector/wp-manager-connector/includes/class-wpmgr-skema.php`, `connector/wp-manager-connector/uninstall.php`, `connector/tests/SkemaTest.php`

**Interfaces:**
- Produces:
  - Konstanta `WPMGR_VERSION = '2.0.0'`, `WPMGR_VERSI_SKEMA = 1`, `WPMGR_FILE`.
  - `WPMGR_Skema::monitoring_mati(): bool`, `WPMGR_Skema::fitur( bool $monitoring_mati ): array`, `WPMGR_Skema::mode_penangkap(): ?string`, `WPMGR_Skema::perlu_migrasi( $tersimpan, $kode ): bool`, `WPMGR_Skema::sql_tabel( string $prefix, string $charset ): array`, `WPMGR_Skema::pastikan()`, `WPMGR_Skema::migrasi()`, `WPMGR_Skema::pangkas()`, `WPMGR_Skema::hapus_semua()`, `WPMGR_Skema::hapus_mu_plugin()`, konstanta `WPMGR_Skema::OPT_VERSI`, `WPMGR_Skema::MU_PLUGIN`.
  - `WPMGR_Settings::percayai_xff(): bool`, `WPMGR_Settings::OPT_XFF`.
  - `WPMGR_REST::perlu_anti_cache( string $route ): bool`, `WPMGR_REST::tambah_header_anti_cache( $result, $server, $request )`.
  - `/ping` membalas tambahan `fitur` (array), `mode_penangkap` (string|null), `percayai_xff` (bool), `versi_skema` (int). `/inventory` membalas tambahan `fitur` dan `connector_version`.
  - Tabel site: `{prefix}wpmgr_errors`, `wpmgr_logins`, `wpmgr_login_gagal`, `wpmgr_traffic`, `wpmgr_pengunjung` (bentuk persis di Step 3).

`fitur()` sengaja mengembalikan array kosong di task ini. Nama fitur hanya boleh diumumkan oleh task yang benar-benar menambahkan endpoint-nya (Task 6: `self_update`, Task 14: `events`, Task 18: `traffic`). Mengumumkan `events` sebelum `/events` ada akan membuat dashboard menjadwalkan pengambilan yang dibalas 404 `rest_no_route`, dan klasifikasi Lapis 1 akan menandai site `needs_reconnect`.

- [ ] **Step 1: Tulis test PHP yang gagal.**

File: `connector/tests/SkemaTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class SkemaTest extends TestCase {

    public function test_belum_ada_fitur_yang_diumumkan(): void {
        $this->assertSame( array(), WPMGR_Skema::fitur( false ) );
        $this->assertSame( array(), WPMGR_Skema::fitur( true ) );
    }

    public function test_perlu_migrasi_hanya_bila_versi_berbeda(): void {
        $this->assertTrue( WPMGR_Skema::perlu_migrasi( 0, 1 ) );
        $this->assertTrue( WPMGR_Skema::perlu_migrasi( '1', 2 ) );
        $this->assertFalse( WPMGR_Skema::perlu_migrasi( '1', 1 ) );
        $this->assertFalse( WPMGR_Skema::perlu_migrasi( 1, 1 ) );
    }

    public function test_sql_tabel_memuat_kelima_tabel(): void {
        $sql = implode( "\n", WPMGR_Skema::sql_tabel( 'wp_', 'DEFAULT CHARSET=utf8mb4' ) );
        foreach ( array( 'wp_wpmgr_errors', 'wp_wpmgr_logins', 'wp_wpmgr_login_gagal',
                         'wp_wpmgr_traffic', 'wp_wpmgr_pengunjung' ) as $tabel ) {
            $this->assertStringContainsString( 'CREATE TABLE ' . $tabel . ' (', $sql );
        }
    }

    public function test_sql_tabel_mengikuti_format_dbdelta(): void {
        // dbDelta() mewajibkan dua spasi setelah PRIMARY KEY; dengan satu
        // spasi ia gagal mengenali primary key dan mencoba menambahkannya
        // lagi di setiap migrasi.
        foreach ( WPMGR_Skema::sql_tabel( 'wp_', '' ) as $satu ) {
            $this->assertMatchesRegularExpression( '/PRIMARY KEY  \(/', $satu );
        }
    }

    public function test_kunci_traffic_muat_di_batas_indeks_lama(): void {
        // (tanggal 3 byte + dimensi 10*4 + kunci 180*4) = 763 byte < 767.
        $sql = implode( "\n", WPMGR_Skema::sql_tabel( 'wp_', '' ) );
        $this->assertStringContainsString( 'kunci varchar(180) NOT NULL', $sql );
        $this->assertStringContainsString( 'dimensi varchar(10) NOT NULL', $sql );
    }

    public function test_hanya_route_wpmgr_yang_diberi_header_anti_cache(): void {
        $this->assertTrue( WPMGR_REST::perlu_anti_cache( '/wpmgr/v1/ping' ) );
        $this->assertTrue( WPMGR_REST::perlu_anti_cache( '/wpmgr/v1/events' ) );
        $this->assertFalse( WPMGR_REST::perlu_anti_cache( '/wp/v2/posts' ) );
        $this->assertFalse( WPMGR_REST::perlu_anti_cache( '/wpmgr/v10/ping' ) );
        $this->assertFalse( WPMGR_REST::perlu_anti_cache( '' ) );
    }
}
```

Tambahkan ke `connector/tests/bootstrap.php`, sebelum `require_once` untuk `class-wpmgr-settings.php`:

```php
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-skema.php';
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `cd connector && vendor/bin/phpunit --filter SkemaTest`. Expected: fatal `Failed opening required ... class-wpmgr-skema.php`.

- [ ] **Step 3: Buat kelas skema.**

File: `connector/wp-manager-connector/includes/class-wpmgr-skema.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Tabel, migrasi, dan pemangkasan data pemantauan di sisi site.
 *
 * Migrasi dijalankan dari plugins_loaded dengan membandingkan versi skema,
 * BUKAN dari activation hook: activation hook tidak berjalan ketika plugin
 * di-update (termasuk lewat self-update dari dashboard), sehingga site yang
 * di-update tidak akan pernah mendapat tabel barunya.
 */
class WPMGR_Skema {

    const OPT_VERSI    = 'wpmgr_versi_skema';
    const HOOK_PANGKAS = 'wpmgr_pangkas_harian';
    const HARI_SIMPAN  = 30;
    const MU_PLUGIN    = 'wpmgr-penangkap.php';

    const TABEL = array( 'wpmgr_errors', 'wpmgr_logins', 'wpmgr_login_gagal', 'wpmgr_traffic', 'wpmgr_pengunjung' );

    public static function monitoring_mati() {
        return defined( 'WPMGR_DISABLE_MONITORING' ) && WPMGR_DISABLE_MONITORING;
    }

    /**
     * Fitur yang benar-benar tersedia di versi connector ini.
     *
     * Dashboard hanya menjadwalkan pengambilan untuk fitur yang diumumkan di
     * sini. Sebuah nama hanya boleh ditambahkan bersamaan dengan endpoint-nya:
     * mengumumkan fitur yang endpoint-nya belum ada membuat dashboard menerima
     * 404 rest_no_route dan salah menyimpulkan connector sudah dicabut.
     */
    public static function fitur( $monitoring_mati ) {
        return array();
    }

    /** Belum ada penangkap error di versi ini; dilaporkan null. */
    public static function mode_penangkap() {
        return null;
    }

    public static function perlu_migrasi( $versi_tersimpan, $versi_kode ) {
        return (int) $versi_tersimpan !== (int) $versi_kode;
    }

    /**
     * Pernyataan CREATE TABLE dalam format yang dituntut dbDelta(): satu kolom
     * per baris, dua spasi setelah PRIMARY KEY, dan KEY alih-alih INDEX.
     */
    public static function sql_tabel( $prefix, $charset ) {
        return array(
            "CREATE TABLE {$prefix}wpmgr_errors (
  id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  sidik_jari char(32) NOT NULL,
  tingkat varchar(10) NOT NULL,
  komponen_tipe varchar(12) NOT NULL,
  komponen_slug varchar(191) DEFAULT NULL,
  pesan text NOT NULL,
  file varchar(255) DEFAULT NULL,
  baris int(11) DEFAULT NULL,
  konteks text DEFAULT NULL,
  jumlah bigint(20) unsigned NOT NULL DEFAULT 1,
  pertama int(10) unsigned NOT NULL,
  terakhir int(10) unsigned NOT NULL,
  diubah int(10) unsigned NOT NULL,
  PRIMARY KEY  (id),
  UNIQUE KEY sidik_jari (sidik_jari),
  KEY diubah (diubah,id)
) {$charset};",
            "CREATE TABLE {$prefix}wpmgr_logins (
  id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  waktu int(10) unsigned NOT NULL,
  jenis varchar(12) NOT NULL,
  username varchar(60) NOT NULL,
  role varchar(60) DEFAULT NULL,
  ip varchar(45) DEFAULT NULL,
  lewat_cloudflare tinyint(1) NOT NULL DEFAULT 0,
  user_agent varchar(255) DEFAULT NULL,
  jalur varchar(12) NOT NULL,
  diubah int(10) unsigned NOT NULL,
  PRIMARY KEY  (id),
  KEY diubah (diubah,id)
) {$charset};",
            "CREATE TABLE {$prefix}wpmgr_login_gagal (
  id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  jam int(10) unsigned NOT NULL,
  ip varchar(45) NOT NULL DEFAULT '',
  username varchar(60) NOT NULL,
  jalur varchar(12) NOT NULL,
  jumlah int(10) unsigned NOT NULL DEFAULT 0,
  user_agent varchar(255) DEFAULT NULL,
  diubah int(10) unsigned NOT NULL,
  PRIMARY KEY  (id),
  UNIQUE KEY kunci (jam,ip,username,jalur),
  KEY diubah (diubah,id)
) {$charset};",
            "CREATE TABLE {$prefix}wpmgr_traffic (
  tanggal date NOT NULL,
  dimensi varchar(10) NOT NULL,
  kunci varchar(180) NOT NULL,
  kunjungan int(10) unsigned NOT NULL DEFAULT 0,
  pengunjung int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY  (tanggal,dimensi,kunci)
) {$charset};",
            "CREATE TABLE {$prefix}wpmgr_pengunjung (
  tanggal date NOT NULL,
  hash char(40) NOT NULL,
  hit int(10) unsigned NOT NULL DEFAULT 1,
  PRIMARY KEY  (tanggal,hash)
) {$charset};",
        );
    }

    public static function pastikan() {
        try {
            if ( ! self::perlu_migrasi( get_option( self::OPT_VERSI, 0 ), WPMGR_VERSI_SKEMA ) ) {
                return;
            }
            self::migrasi();
            update_option( self::OPT_VERSI, WPMGR_VERSI_SKEMA, true );
        } catch ( \Throwable $e ) {
            // Migrasi yang gagal tidak boleh merusak halaman site. Versi skema
            // tidak diperbarui, jadi migrasi dicoba lagi di request berikutnya.
            unset( $e );
        }
    }

    public static function migrasi() {
        global $wpdb;
        require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        dbDelta( self::sql_tabel( $wpdb->prefix, $wpdb->get_charset_collate() ) );
        if ( ! wp_next_scheduled( self::HOOK_PANGKAS ) ) {
            wp_schedule_event( time() + HOUR_IN_SECONDS, 'daily', self::HOOK_PANGKAS );
        }
    }

    /** Dipanggil WP-Cron harian: data site dibatasi 30 hari (spec §5.2). */
    public static function pangkas() {
        global $wpdb;
        $p       = $wpdb->prefix;
        $batas   = time() - self::HARI_SIMPAN * DAY_IN_SECONDS;
        $tanggal = wp_date( 'Y-m-d', $batas );
        $kemarin = wp_date( 'Y-m-d', time() - DAY_IN_SECONDS );

        $wpdb->query( $wpdb->prepare( "DELETE FROM {$p}wpmgr_errors WHERE terakhir < %d", $batas ) );
        $wpdb->query( $wpdb->prepare( "DELETE FROM {$p}wpmgr_logins WHERE waktu < %d", $batas ) );
        $wpdb->query( $wpdb->prepare( "DELETE FROM {$p}wpmgr_login_gagal WHERE jam < %d", $batas ) );
        $wpdb->query( $wpdb->prepare( "DELETE FROM {$p}wpmgr_traffic WHERE tanggal < %s", $tanggal ) );
        // Hash pengunjung hanya berarti untuk hari yang sedang dihitung; menyimpannya
        // lebih lama berarti pengunjung dapat dilacak lintas hari.
        $wpdb->query( $wpdb->prepare( "DELETE FROM {$p}wpmgr_pengunjung WHERE tanggal < %s", $kemarin ) );
    }

    public static function hapus_mu_plugin() {
        if ( defined( 'WPMU_PLUGIN_DIR' ) ) {
            $berkas = WPMU_PLUGIN_DIR . '/' . self::MU_PLUGIN;
            if ( file_exists( $berkas ) ) {
                @unlink( $berkas ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
        }
    }

    /** Dipanggil uninstall.php: tidak ada sisa data pemantauan setelah plugin dihapus. */
    public static function hapus_semua() {
        global $wpdb;
        foreach ( self::TABEL as $tabel ) {
            $wpdb->query( "DROP TABLE IF EXISTS {$wpdb->prefix}{$tabel}" ); // phpcs:ignore WordPress.DB.PreparedSQL
        }
        foreach ( array( 'wpmgr_site_id', 'wpmgr_secret', 'wpmgr_dashboard_url', self::OPT_VERSI,
                         'wpmgr_percayai_xff', 'wpmgr_garam' ) as $opsi ) {
            delete_option( $opsi );
        }
        wp_clear_scheduled_hook( self::HOOK_PANGKAS );
        self::hapus_mu_plugin();
    }
}
```

- [ ] **Step 4: Buat `uninstall.php`.**

File: `connector/wp-manager-connector/uninstall.php`
```php
<?php
if ( ! defined( 'WP_UNINSTALL_PLUGIN' ) ) {
    exit;
}

require_once __DIR__ . '/includes/class-wpmgr-skema.php';
WPMGR_Skema::hapus_semua();
```

- [ ] **Step 5: Perbarui berkas utama plugin.** Ganti seluruh isi `connector/wp-manager-connector/wp-manager-connector.php`:

File: `connector/wp-manager-connector/wp-manager-connector.php`
```php
<?php
/**
 * Plugin Name: WP Manager Connector
 * Description: Menghubungkan site ini ke dashboard WP Manager untuk pemindaian, update, dan pemantauan terpusat.
 * Version:     2.0.0
 * Requires at least: 5.5
 * Requires PHP: 7.4
 * License:     GPL-2.0-or-later
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

define( 'WPMGR_VERSION', '2.0.0' );
define( 'WPMGR_VERSI_SKEMA', 1 );
define( 'WPMGR_JENDELA_DETIK', 300 );
define( 'WPMGR_NONCE_TTL', 600 );
define( 'WPMGR_SSO_TTL', 120 );
define( 'WPMGR_USER_LOGIN', 'wpmgr' );
define( 'WPMGR_FILE', __FILE__ );
define( 'WPMGR_DIR', plugin_dir_path( __FILE__ ) );

require_once WPMGR_DIR . 'includes/class-wpmgr-signing.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-settings.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-skema.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-inventory.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-updater.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-rest.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-sso.php';

add_action( 'plugins_loaded', array( 'WPMGR_Skema', 'pastikan' ) );
add_action( WPMGR_Skema::HOOK_PANGKAS, array( 'WPMGR_Skema', 'pangkas' ) );
add_action( 'rest_api_init', array( 'WPMGR_REST', 'daftarkan_route' ) );
add_filter( 'rest_post_dispatch', array( 'WPMGR_REST', 'tambah_header_anti_cache' ), 10, 3 );
add_action( 'admin_menu', array( 'WPMGR_Settings', 'daftarkan_menu' ) );
add_action( 'admin_init', array( 'WPMGR_Settings', 'tangani_simpan' ) );
add_action( 'init', array( 'WPMGR_SSO', 'tangani_permintaan' ), 1 );
```

- [ ] **Step 6: Perbarui REST.** Di `includes/class-wpmgr-rest.php`, ganti metode `ping()` dan `inventory()`, lalu tambahkan dua metode baru setelah `path_untuk_tanda_tangan()`:

```php
    public static function ping() {
        return rest_ensure_response( array(
            'connector_version' => WPMGR_VERSION,
            'wp_version'        => get_bloginfo( 'version' ),
            'php_version'       => PHP_VERSION,
            'site_url'          => home_url(),
            'fitur'             => WPMGR_Skema::fitur( WPMGR_Skema::monitoring_mati() ),
            'mode_penangkap'    => WPMGR_Skema::mode_penangkap(),
            'percayai_xff'      => WPMGR_Settings::percayai_xff(),
            'versi_skema'       => (int) get_option( WPMGR_Skema::OPT_VERSI, 0 ),
        ) );
    }

    public static function inventory() {
        // Selama /update memegang lock, direktori paket bisa sedang kosong
        // atau setengah terekstrak. Inventaris yang dibaca saat itu bukan
        // keadaan site yang sebenarnya, dan scan ulang setelah timeout update
        // akan memutuskan nasib job berdasarkan keadaan palsu itu.
        if ( WPMGR_Updater::sedang_sibuk() ) {
            return WPMGR_Updater::galat_sibuk();
        }
        $data                      = WPMGR_Inventory::kumpulkan();
        $data['fitur']             = WPMGR_Skema::fitur( WPMGR_Skema::monitoring_mati() );
        $data['connector_version'] = WPMGR_VERSION;
        return rest_ensure_response( $data );
    }
```

```php
    public static function perlu_anti_cache( $route ) {
        return 0 === strpos( (string) $route, '/' . self::NS . '/' );
    }

    /**
     * LiteSpeed Cache secara bawaan ikut meng-cache respons REST GET. Tanpa
     * header ini /events, /traffic, bahkan /ping bisa dijawab dari cache:
     * data basi yang tampak sah bagi dashboard. Dipasang di rest_post_dispatch
     * supaya juga berlaku untuk penolakan 401 dari guard().
     */
    public static function tambah_header_anti_cache( $result, $server, $request ) {
        if ( $result instanceof WP_HTTP_Response && self::perlu_anti_cache( $request->get_route() ) ) {
            $result->header( 'Cache-Control', 'no-store, private' );
            $result->header( 'Pragma', 'no-cache' );
            $result->header( 'Expires', 'Wed, 11 Jan 1984 05:00:00 GMT' );
            $result->header( 'X-LiteSpeed-Cache-Control', 'no-cache' );
        }
        return $result;
    }
```

- [ ] **Step 7: Setelan proxy di halaman pengaturan.** Di `includes/class-wpmgr-settings.php`:

1. Tambahkan konstanta dan getter setelah `const OPT_DASHBOARD = 'wpmgr_dashboard_url';`:

```php
    const OPT_XFF       = 'wpmgr_percayai_xff';
```

```php
    public static function percayai_xff() {
        return '1' === (string) get_option( self::OPT_XFF, '0' );
    }
```

2. Ganti `tangani_simpan()`:

```php
    public static function tangani_simpan() {
        if ( ! current_user_can( 'manage_options' ) ) {
            return;
        }

        if ( isset( $_POST['wpmgr_simpan_setelan'] ) ) {
            check_admin_referer( 'wpmgr_setelan' );
            update_option( self::OPT_XFF, empty( $_POST['wpmgr_percayai_xff'] ) ? '0' : '1', false );
            set_transient( 'wpmgr_pesan', 'Pengaturan pemantauan disimpan.', 30 );
            wp_safe_redirect( admin_url( 'options-general.php?page=wpmgr' ) );
            exit;
        }

        if ( ! isset( $_POST['wpmgr_kunci'] ) ) {
            return;
        }
        check_admin_referer( 'wpmgr_simpan' );

        $hasil = self::simpan_kunci( sanitize_text_field( wp_unslash( $_POST['wpmgr_kunci'] ) ) );
        $pesan = is_wp_error( $hasil ) ? $hasil->get_error_message() : 'Terhubung ke dashboard.';
        set_transient( 'wpmgr_pesan', $pesan, 30 );

        wp_safe_redirect( admin_url( 'options-general.php?page=wpmgr' ) );
        exit;
    }
```

3. Di `render()`, tepat sebelum `</div>` penutup `wrap`, tambahkan:

```php
            <h2>Pemantauan</h2>
            <form method="post">
                <?php wp_nonce_field( 'wpmgr_setelan' ); ?>
                <input type="hidden" name="wpmgr_simpan_setelan" value="1">
                <p>
                    <label>
                        <input type="checkbox" name="wpmgr_percayai_xff" value="1" <?php checked( self::percayai_xff() ); ?>>
                        Site ini berada di balik proxy atau load balancer (percayai header <code>X-Forwarded-For</code>)
                    </label>
                </p>
                <p class="description">
                    Aktifkan hanya bila server ini benar-benar berada di belakang proxy yang Anda
                    kendalikan. Bila diaktifkan tanpa proxy, pengunjung dapat memalsukan IP mereka
                    di riwayat login. Site di balik Cloudflare tidak perlu mengaktifkan ini.
                </p>
                <?php submit_button( 'Simpan pengaturan' ); ?>
            </form>
```

- [ ] **Step 8: Jalankan test PHP.** Run: `cd connector && vendor/bin/phpunit`. Expected: seluruh suite lulus, termasuk 6 test `SkemaTest`.

- [ ] **Step 9: Lint sintaks PHP 7.4.** Dari akar repo:

```bash
docker run --rm -v "$(pwd -W 2>/dev/null || pwd)/connector/wp-manager-connector:/src" php:7.4-cli sh -c 'for f in $(find /src -name "*.php"); do php -l "$f" || exit 1; done'
```

Expected: `No syntax errors detected` untuk setiap berkas. Perintah yang sama dipakai di setiap task yang mengubah PHP connector.

- [ ] **Step 10: Commit.**

```bash
git add connector/wp-manager-connector connector/tests/SkemaTest.php connector/tests/bootstrap.php
git commit -m "feat(connector): fondasi 2.0 -- skema pemantauan, pengumuman fitur, header anti-cache"
```

### Task 3: Infrastruktur e2e — connector disalin ke container, bukan di-bind-mount

**Files:**
- Modify: `docker-compose.yml`, `tests/e2e/conftest.py`, `tests/e2e/test_alur_penuh.py`
- Create: `tests/e2e/test_fondasi_lapis2.py`

**Interfaces:**
- Consumes: Task 2 (`/ping` dengan `fitur`, `versi_skema`, `percayai_xff`; header anti-cache; tabel `wpmgr_*`).
- Produces (di `tests/e2e/conftest.py`):
  - `sinkronkan_connector() -> None`
  - `versi_connector_sumber() -> str`
  - `tulis_di_kontainer(path: str, isi: str) -> None`, `hapus_di_kontainer(path: str) -> None` (dipindah dari `test_alur_penuh.py`, tanpa garis bawah awal)
  - `permintaan_bertanda(site, secret: str, method: str, route: str, body: bytes = b"", query: str = "") -> httpx.Response`, dengan `route` berbentuk `/wpmgr/v1/ping`
  - `klien_http(site) -> SiteClient` (dipindah dari `_klien_http`)
  - konstanta `PLUGIN_DI_KONTAINER`, `SUMBER_DI_KONTAINER`, `AKAR_REPO`

- [ ] **Step 1: Ubah volume WordPress.** Di `docker-compose.yml`, ganti baris volume layanan `wp`:

```yaml
    volumes:
      # Sumber connector dipasang read-only di luar direktori plugin, lalu
      # disalin ke direktori plugin oleh fixture e2e. Bind mount langsung ke
      # wp-content/plugins membuat self-update WordPress menghapus dan menulis
      # ulang berkas di checkout repo.
      - ./connector/wp-manager-connector:/opt/wpmgr-connector-src:ro
```

Lalu buat ulang container, termasuk volume anonim yang masih menyimpan titik mount lama:

```bash
docker compose up -d --force-recreate -V wp wpcli
```

- [ ] **Step 2: Tulis test e2e fondasi (gagal).**

File: `tests/e2e/test_fondasi_lapis2.py`
```python
import uuid

import pytest

from wpmgr.crypto import dekripsi_secret

from .conftest import (
    AKAR_REPO,
    PLUGIN_DI_KONTAINER,
    hapus_di_kontainer,
    klien_http,
    permintaan_bertanda,
    tulis_di_kontainer,
    versi_connector_sumber,
    wpcli,
)

pytestmark = pytest.mark.e2e


def test_direktori_plugin_bukan_bind_mount(wp_site):
    nama = f"penanda-{uuid.uuid4().hex[:8]}.txt"
    tulis_di_kontainer(f"{PLUGIN_DI_KONTAINER}/{nama}", "x")
    try:
        assert not (AKAR_REPO / "connector" / "wp-manager-connector" / nama).exists()
    finally:
        hapus_di_kontainer(f"{PLUGIN_DI_KONTAINER}/{nama}")


def test_ping_mengumumkan_kemampuan(sesi, site_terpasang):
    data = klien_http(site_terpasang).ping()
    assert data["connector_version"] == versi_connector_sumber()
    assert isinstance(data["fitur"], list)
    assert data["versi_skema"] >= 1
    assert data["percayai_xff"] is False


def test_header_anti_cache_pada_respons_sah_dan_penolakan(sesi, site_terpasang):
    secret = dekripsi_secret(site_terpasang.secret_terenkripsi)
    sah = permintaan_bertanda(site_terpasang, secret, "GET", "/wpmgr/v1/ping")
    ditolak = permintaan_bertanda(site_terpasang, "0" * 64, "GET", "/wpmgr/v1/ping")
    assert sah.status_code == 200
    assert ditolak.status_code == 401
    for r in (sah, ditolak):
        assert "no-store" in r.headers["cache-control"]
        assert r.headers["x-litespeed-cache-control"] == "no-cache"


def test_tabel_pemantauan_dibuat(site_terpasang):
    tabel = wpcli("db", "query", "SHOW TABLES LIKE 'wp_wpmgr_%'", "--skip-column-names")
    assert set(tabel.split()) == {
        "wp_wpmgr_errors", "wp_wpmgr_logins", "wp_wpmgr_login_gagal",
        "wp_wpmgr_traffic", "wp_wpmgr_pengunjung",
    }
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/e2e/test_fondasi_lapis2.py -q`. Expected: error impor `cannot import name 'AKAR_REPO'`.

- [ ] **Step 4: Perbarui `tests/e2e/conftest.py`.** Tambahkan impor dan fungsi berikut. Ubah fixture `wp_site` supaya memanggil `sinkronkan_connector()` tepat sebelum `wpcli("plugin", "activate", "wp-manager-connector")`. Pindahkan `_tulis_di_kontainer`, `_hapus_di_kontainer`, `_klien_http`, dan fixture `site_terpasang` dari `test_alur_penuh.py` ke sini dengan nama baru seperti di bawah. Tambahkan juga `from wpmgr.crypto import dekripsi_secret`, `from wpmgr.pairing import buat_site`, `from wpmgr.site_client import SiteClient` ke impor.

```python
import re
from pathlib import Path

from wpmgr.signing import new_nonce, sign

AKAR_REPO = Path(__file__).resolve().parents[2]
SUMBER_DI_KONTAINER = "/opt/wpmgr-connector-src"
PLUGIN_DI_KONTAINER = "/var/www/html/wp-content/plugins/wp-manager-connector"


def versi_connector_sumber() -> str:
    teks = (AKAR_REPO / "connector" / "wp-manager-connector" / "wp-manager-connector.php").read_text(
        encoding="utf-8"
    )
    return re.search(r"^\s*\*\s*Version:\s*(\S+)", teks, re.M).group(1)


def sinkronkan_connector() -> None:
    """Salin connector dari sumber read-only ke direktori plugin container.

    Dipanggil di awal setiap sesi e2e (dan oleh test yang menimpa connector),
    sehingga yang diuji selalu kode di checkout ini, dan self-update bisa
    menimpa direktori plugin tanpa menyentuh repo.
    """
    subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "sh", "-c",
         f"rm -rf '{PLUGIN_DI_KONTAINER}' && cp -r '{SUMBER_DI_KONTAINER}' '{PLUGIN_DI_KONTAINER}'"],
        capture_output=True, text=True, check=True,
    )


def tulis_di_kontainer(path: str, isi: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "sh", "-c",
         f"mkdir -p \"$(dirname '{path}')\" && cat > '{path}'"],
        input=isi, text=True, capture_output=True, check=True,
    )


def hapus_di_kontainer(path: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "rm", "-rf", path],
        capture_output=True, check=False,
    )


def permintaan_bertanda(site, secret: str, method: str, route: str,
                        body: bytes = b"", query: str = "") -> httpx.Response:
    """Permintaan HMAC mentah, untuk test yang perlu melihat header respons."""
    path = f"/wp-json{route}"
    ts, nonce = int(time.time()), new_nonce()
    headers = {
        "X-Wpmgr-Site": str(site.id),
        "X-Wpmgr-Timestamp": str(ts),
        "X-Wpmgr-Nonce": nonce,
        "X-Wpmgr-Signature": sign(secret, method, path, ts, nonce, body),
    }
    if body:
        headers["Content-Type"] = "application/json"
    return httpx.request(method, f"{site.url}{path}{query}", content=body or None,
                         headers=headers, timeout=60)


def klien_http(site) -> SiteClient:
    """SiteClient untuk WordPress lokal yang memakai http, bukan https.

    SiteClient menolak base_url non-https di konstruktor -- sengaja, karena
    tanpa TLS body respons dan token SSO yang lewat bisa dibaca di jalan.
    Kontainer WordPress di lingkungan test ini bicara HTTP polos di
    localhost:8081, jadi klien dibangun dengan URL https palsu lalu
    base_url-nya ditimpa setelah konstruksi. Pemeriksaan di konstruktor
    sendiri TIDAK dilonggarkan; workaround ini dikurung di sini saja.
    """
    klien = SiteClient("https://placeholder.test", str(site.id),
                       dekripsi_secret(site.secret_terenkripsi))
    klien.base_url = site.url
    return klien


@pytest.fixture
def site_terpasang(sesi, wp_site):
    site, _kunci = buat_site(sesi, "Uji E2E", "https://uji.test", None, None)
    site.url = wp_site  # http://localhost:8081
    sesi.commit()

    # Ditulis langsung lewat `wp option update`, BUKAN lewat
    # WPMGR_Settings::simpan_kunci() -- fixture ini hanya perlu WordPress
    # dalam keadaan "sudah terpasang" secepat mungkin. Jalur simpan_kunci()
    # sungguhan diuji terpisah di test_alur_penuh.py.
    wpcli("option", "update", "wpmgr_site_id", str(site.id))
    wpcli("option", "update", "wpmgr_secret", dekripsi_secret(site.secret_terenkripsi))
    wpcli("option", "update", "wpmgr_dashboard_url", "http://host.docker.internal:8000")
    return site
```

- [ ] **Step 5: Sesuaikan `tests/e2e/test_alur_penuh.py`.** Hapus definisi lokal `_tulis_di_kontainer`, `_hapus_di_kontainer`, `_klien_http`, dan fixture `site_terpasang`. Impor penggantinya dari `.conftest` (`tulis_di_kontainer`, `hapus_di_kontainer`, `klien_http`, `versi_connector_sumber`), lalu ganti seluruh pemanggilan lama ke nama baru. `klien_http` tidak lagi menerima `sesi`, jadi `_klien_http(site, sesi)` menjadi `klien_http(site)`. Ganti asersi versi di `test_ping_menjawab_dengan_versi`:

```python
    assert data["connector_version"] == versi_connector_sumber()
```

- [ ] **Step 6: Jalankan seluruh suite e2e.** Run: `.venv/Scripts/python -m pytest -m e2e -q`. Expected: 16 test Lapis 1 dan 4 test baru lulus (20 lulus).

- [ ] **Step 7: Commit.**

```bash
git add docker-compose.yml tests/e2e
git commit -m "test: connector disalin ke container e2e, bukan di-bind-mount"
```

### Task 4: Klien site dan pencatatan kemampuan connector

**Files:**
- Create: `src/wpmgr/fitur.py`, `src/wpmgr/versi.py`
- Modify: `src/wpmgr/site_client.py`, `src/wpmgr/jobs/handlers.py`
- Test: `tests/unit/test_site_client.py` (tambah), `tests/unit/test_fitur_versi.py`, `tests/integration/test_kemampuan.py`

**Interfaces:**
- Consumes: Task 1 (kolom `Site.fitur`, `mode_penangkap`, `percayai_xff`).
- Produces:
  - `wpmgr.fitur`: konstanta `EVENTS = "events"`, `TRAFFIC = "traffic"`, `SELF_UPDATE = "self_update"`; `punya_fitur(site, nama: str) -> bool`.
  - `wpmgr.versi.lebih_lama(a: str | None, b: str | None) -> bool`.
  - `SiteClient.events(kursor: str | None, batas: int = 500) -> dict`, `SiteClient.traffic(dari: str | None = None) -> dict`, `SiteClient.self_update(versi: str, sha256: str, isi_zip: bytes) -> dict`; konstanta `TIMEOUT_KOLEKSI = 30.0`, `TIMEOUT_SELF_UPDATE = 180.0`.
  - `wpmgr.jobs.handlers.simpan_kemampuan(site: Site, data: dict) -> None`, dipanggil oleh `tangani_verify_site` dan `tangani_scan_site`.

- [ ] **Step 1: Tulis test unit yang gagal.** Tambahkan ke akhir `tests/unit/test_site_client.py`:

```python
def _tangkap(tampung, balasan=None):
    def handler(request):
        tampung.append(request)
        return httpx.Response(200, json=balasan if balasan is not None else {"ok": True})

    return handler


def _tanda_tangan_sah(request, path):
    return verify(
        SECRET, request.headers["X-Wpmgr-Signature"], request.method, path,
        int(request.headers["X-Wpmgr-Timestamp"]), request.headers["X-Wpmgr-Nonce"],
        request.content,
    )


def test_events_membawa_kursor_di_query_tetapi_tidak_di_tanda_tangan():
    tampung = []
    buat_klien(_tangkap(tampung)).events("e=10:2;l=0:0;g=0:0", batas=200)
    r = tampung[0]
    assert r.url.path == "/wp-json/wpmgr/v1/events"
    assert r.url.params["kursor"] == "e=10:2;l=0:0;g=0:0"
    assert r.url.params["batas"] == "200"
    assert _tanda_tangan_sah(r, "/wp-json/wpmgr/v1/events")


def test_events_tanpa_kursor_tidak_mengirim_parameter_kursor():
    tampung = []
    buat_klien(_tangkap(tampung)).events(None)
    assert "kursor" not in tampung[0].url.params


def test_traffic_tanpa_dari_tanpa_query():
    tampung = []
    buat_klien(_tangkap(tampung)).traffic()
    assert tampung[0].url.query == b""
    assert _tanda_tangan_sah(tampung[0], "/wp-json/wpmgr/v1/traffic")


def test_traffic_dengan_dari():
    tampung = []
    buat_klien(_tangkap(tampung)).traffic("2026-09-01")
    assert tampung[0].url.params["dari"] == "2026-09-01"


def test_self_update_mengirim_zip_base64_bertanda_tangan():
    import base64

    tampung = []
    buat_klien(_tangkap(tampung)).self_update("2.0.1", "ab" * 32, b"PK\x03\x04isi")
    r = tampung[0]
    body = json.loads(r.content)
    assert body == {"versi": "2.0.1", "sha256": "ab" * 32,
                    "zip_b64": base64.b64encode(b"PK\x03\x04isi").decode()}
    assert r.method == "POST"
    assert _tanda_tangan_sah(r, "/wp-json/wpmgr/v1/self-update")


def test_self_update_timeout_baca_menjadi_unknown():
    def handler(request):
        raise httpx.ReadTimeout("lambat", request=request)

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).self_update("2.0.1", "ab" * 32, b"x")
    assert exc.value.error_class == UNKNOWN


def test_events_timeout_baca_menjadi_transient():
    def handler(request):
        raise httpx.ReadTimeout("lambat", request=request)

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).events(None)
    assert exc.value.error_class == TRANSIENT
```

File: `tests/unit/test_fitur_versi.py`
```python
from types import SimpleNamespace

import pytest

from wpmgr.fitur import EVENTS, SELF_UPDATE, TRAFFIC, punya_fitur
from wpmgr.versi import lebih_lama


def test_punya_fitur():
    site = SimpleNamespace(fitur=[EVENTS, SELF_UPDATE])
    assert punya_fitur(site, EVENTS)
    assert not punya_fitur(site, TRAFFIC)
    assert not punya_fitur(SimpleNamespace(fitur=None), EVENTS)


@pytest.mark.parametrize(
    ("a", "b", "harapan"),
    [
        ("1.0.0", "2.0.0", True),
        ("2.0.0", "2.0.0", False),
        ("2.0.1", "2.0.0", False),
        ("2.0.0", "2.0.1-uji", True),
        ("2.0.1-uji", "2.0.1", False),
        ("2.0", "2.0.1", True),
        (None, "2.0.0", False),
        ("2.0.0", None, False),
        ("abc", "2.0.0", False),
    ],
)
def test_lebih_lama(a, b, harapan):
    assert lebih_lama(a, b) is harapan
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_site_client.py tests/unit/test_fitur_versi.py -q`. Expected: `AttributeError: 'SiteClient' object has no attribute 'events'` dan `ModuleNotFoundError: No module named 'wpmgr.fitur'`.

- [ ] **Step 3: Implementasikan modul kecil.**

File: `src/wpmgr/fitur.py`
```python
"""Nama fitur yang diumumkan connector lewat /ping dan /inventory.

Dashboard hanya menjadwalkan pekerjaan untuk fitur yang diumumkan. Connector
lama tidak punya endpoint baru, dan WordPress membalasnya dengan 404
`rest_no_route`, yang oleh klasifikasi Lapis 1 dibaca sebagai "connector
hilang".
"""

EVENTS = "events"
TRAFFIC = "traffic"
SELF_UPDATE = "self_update"


def punya_fitur(site, nama: str) -> bool:
    return nama in (site.fitur or [])
```

File: `src/wpmgr/versi.py`
```python
def _komponen(versi: str) -> tuple[int, ...] | None:
    # Akhiran pra-rilis ("2.0.1-uji") dibuang: penanda "usang" hanya perlu
    # tahu apakah nomor rilisnya tertinggal.
    try:
        return tuple(int(bagian) for bagian in versi.strip().split("-")[0].split("."))
    except ValueError:
        return None


def lebih_lama(a: str | None, b: str | None) -> bool:
    """Apakah versi `a` secara numerik lebih rendah dari `b`.

    Versi yang tidak dapat diurai menghasilkan False: lebih baik tidak
    menandai "usang" daripada menandainya berdasarkan tebakan.
    """
    if not a or not b:
        return False
    ka, kb = _komponen(a), _komponen(b)
    if ka is None or kb is None:
        return False
    panjang = max(len(ka), len(kb))
    return ka + (0,) * (panjang - len(ka)) < kb + (0,) * (panjang - len(kb))
```

- [ ] **Step 4: Tambahkan metode klien.** Di `src/wpmgr/site_client.py`:

1. Ubah impor bagian atas menjadi:

```python
import base64
import json
import time
from urllib.parse import urlencode
```

2. Tambahkan konstanta setelah `TIMEOUT_UPDATE = 180.0`:

```python
TIMEOUT_KOLEKSI = 30.0
TIMEOUT_SELF_UPDATE = 180.0
```

3. Ubah signature `_panggil` dan pembentukan URL-nya:

```python
    def _panggil(
        self, method: str, path: str, body: bytes, timeout: float, berefek: bool = False,
        query: str = "",
    ) -> dict:
```

dan di dalamnya:

```python
            resp = self._client.request(
                method, f"{self.base_url}{path}{query}", content=body or None,
                headers=headers, timeout=timeout,
            )
```

Tambahkan satu kalimat ke docstring `_panggil`: `Query string tidak ikut ditandatangani: connector memverifikasi tanda tangan atas "/wp-json" + route, dan route WordPress tidak memuat query.`

4. Tambahkan metode di akhir kelas:

```python
    def events(self, kursor: str | None, batas: int = 500,
               timeout: float = TIMEOUT_KOLEKSI) -> dict:
        param = {"batas": str(batas)}
        if kursor:
            param["kursor"] = kursor
        return self._panggil("GET", f"{PREFIX}/events", b"", timeout,
                             query="?" + urlencode(param))

    def traffic(self, dari: str | None = None, timeout: float = TIMEOUT_KOLEKSI) -> dict:
        query = "?" + urlencode({"dari": dari}) if dari else ""
        return self._panggil("GET", f"{PREFIX}/traffic", b"", timeout, query=query)

    def self_update(self, versi: str, sha256: str, isi_zip: bytes,
                    timeout: float = TIMEOUT_SELF_UPDATE) -> dict:
        body = json.dumps(
            {"versi": versi, "sha256": sha256,
             "zip_b64": base64.b64encode(isi_zip).decode("ascii")},
            separators=(",", ":"),
        ).encode("utf-8")
        # berefek: connector mungkin sedang menimpa dirinya ketika koneksi
        # terputus. Mengulang aman karena versi yang sama dibalas "sudah di
        # versi tersebut".
        return self._panggil("POST", f"{PREFIX}/self-update", body, timeout, berefek=True)
```

- [ ] **Step 5: Jalankan test unit.** Run: `.venv/Scripts/python -m pytest tests/unit -q`. Expected: semua lulus.

- [ ] **Step 6: Tulis test integrasi yang gagal.**

File: `tests/integration/test_kemampuan.py`
```python
import httpx
import pytest

from wpmgr.jobs.handlers import tangani_scan_site, tangani_verify_site
from wpmgr.jobs.queue import buat_job
from wpmgr.models import JobType
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration


def klien_palsu(muatan):
    def handler(request):
        return httpx.Response(200, json=muatan)

    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


PING = {
    "connector_version": "2.0.0", "wp_version": "6.5", "php_version": "8.1",
    "site_url": "https://contoh.test", "fitur": ["self_update", "events"],
    "mode_penangkap": "penuh", "percayai_xff": True, "versi_skema": 1,
}


def test_verify_mencatat_fitur_dan_setelan(sesi, site):
    job = buat_job(sesi, site.id, JobType.verify_site)
    tangani_verify_site(sesi, job, klien_palsu(PING))
    sesi.refresh(site)
    assert site.fitur == ["events", "self_update"]
    assert site.mode_penangkap == "penuh"
    assert site.percayai_xff is True
    assert site.connector_version == "2.0.0"


def test_connector_lama_tanpa_fitur_dicatat_kosong(sesi, site):
    site.fitur = ["events"]
    sesi.commit()
    lama = {"connector_version": "1.0.0", "wp_version": "6.5", "php_version": "8.1",
            "site_url": "https://contoh.test"}
    job = buat_job(sesi, site.id, JobType.verify_site)
    tangani_verify_site(sesi, job, klien_palsu(lama))
    sesi.refresh(site)
    assert site.fitur == []
    assert site.mode_penangkap is None


def test_mode_penangkap_tak_dikenal_diabaikan(sesi, site):
    job = buat_job(sesi, site.id, JobType.verify_site)
    tangani_verify_site(sesi, job, klien_palsu({**PING, "mode_penangkap": "<b>x</b>"}))
    sesi.refresh(site)
    assert site.mode_penangkap is None


def test_scan_juga_mencatat_fitur(sesi, site):
    inventaris = {"core": {"slug": "core", "nama": "WordPress", "versi_terpasang": "6.5",
                           "versi_tersedia": None, "aktif": True, "auto_update": False},
                  "plugins": [], "themes": [], "fitur": ["traffic"],
                  "connector_version": "2.0.0"}
    job = buat_job(sesi, site.id, JobType.scan_site)
    tangani_scan_site(sesi, job, klien_palsu(inventaris))
    sesi.refresh(site)
    assert site.fitur == ["traffic"]
    assert site.connector_version == "2.0.0"
```

- [ ] **Step 7: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_kemampuan.py -q`. Expected: `assert [] == ['events', 'self_update']` (fitur belum dicatat).

- [ ] **Step 8: Implementasikan `simpan_kemampuan`.** Di `src/wpmgr/jobs/handlers.py`, tambahkan fungsi setelah `buat_klien()`:

```python
MODE_PENANGKAP_SAH = frozenset({"penuh", "terbatas"})


def simpan_kemampuan(site: Site, data: dict) -> None:
    """Catat apa yang diumumkan connector tentang dirinya sendiri.

    Connector 1.x tidak mengirim `fitur`; nilainya dikosongkan, bukan
    dibiarkan, supaya site yang di-downgrade berhenti dijadwalkan untuk
    endpoint yang tidak lagi ia punya.
    """
    fitur = data.get("fitur")
    site.fitur = sorted({str(f) for f in fitur}) if isinstance(fitur, list) else []
    mode = data.get("mode_penangkap")
    site.mode_penangkap = mode if mode in MODE_PENANGKAP_SAH else None
    if "percayai_xff" in data:
        site.percayai_xff = bool(data.get("percayai_xff"))
    versi = data.get("connector_version")
    if isinstance(versi, str) and versi:
        site.connector_version = versi
```

Di `tangani_verify_site`, panggil `simpan_kemampuan(site, data)` tepat sebelum `if site.status != SiteStatus.disabled:`. Ganti `tangani_scan_site` menjadi:

```python
def tangani_scan_site(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    data = klien.inventory()
    jumlah = simpan_inventaris(sesi, site, data)
    simpan_kemampuan(site, data)
    if site.status in (SiteStatus.unreachable, SiteStatus.needs_reconnect, SiteStatus.blocked):
        site.status = SiteStatus.active
    sesi.commit()
    return {"jumlah_paket": jumlah}
```

- [ ] **Step 9: Jalankan unit dan integrasi.** Expected: semua lulus, termasuk `test_handler_scan.py` Lapis 1.

- [ ] **Step 10: Commit.**

```bash
git add src/wpmgr/fitur.py src/wpmgr/versi.py src/wpmgr/site_client.py src/wpmgr/jobs/handlers.py tests/unit/test_site_client.py tests/unit/test_fitur_versi.py tests/integration/test_kemampuan.py
git commit -m "feat: klien events/traffic/self-update dan pencatatan fitur connector"
```

---

## Fase B — Pembaruan connector dari dashboard

### Task 5: Paket connector (build-connector dan unduhan)

**Files:**
- Create: `src/wpmgr/connector_paket.py`
- Modify: `src/wpmgr/cli.py`, `src/wpmgr/web/routes_pages.py`, `src/wpmgr/templates/site_new.html`, `tests/integration/conftest.py`
- Test: `tests/unit/test_connector_paket.py`, `tests/integration/test_unduh_connector.py`

**Interfaces:**
- Consumes: Task 1 (`Settings.jalur_connector`).
- Produces:
  - `wpmgr.connector_paket`: konstanta `NAMA_DIREKTORI = "wp-manager-connector"`, `NAMA_ZIP`, `NAMA_MANIFEST`; `sumber_bawaan() -> Path`, `versi_dari_header(berkas: Path) -> str`, `bangun_paket(sumber: Path, tujuan: Path) -> dict`, `baca_manifest(tujuan: Path) -> dict | None`, `baca_zip(tujuan: Path) -> bytes`. Manifest: `{"versi", "sha256", "ukuran", "dibangun_pada"}`.
  - CLI `python -m wpmgr.cli build-connector [--sumber PATH]`.
  - Route `GET /connector/unduh` (butuh login).
  - Fixture integrasi bersama `klien_web` (TestClient yang sudah login sebagai `a@b.test`) dan `pengguna_uji` di `tests/integration/conftest.py`.

Isi zip harus berada di dalam direktori puncak `wp-manager-connector/`. `Plugin_Upgrader::install()` memakai nama direktori puncak itu sebagai direktori tujuan, sehingga zip tanpa direktori puncak akan terpasang sebagai plugin *lain*, bukan menimpa connector.

- [ ] **Step 1: Tulis test unit yang gagal.**

File: `tests/unit/test_connector_paket.py`
```python
import hashlib
import json
import zipfile

import pytest

from wpmgr.connector_paket import (
    NAMA_MANIFEST,
    NAMA_ZIP,
    bangun_paket,
    baca_manifest,
    versi_dari_header,
)

HEADER = "<?php\n/**\n * Plugin Name: WP Manager Connector\n * Version:     2.3.4\n */\n"


@pytest.fixture
def sumber(tmp_path):
    akar = tmp_path / "wp-manager-connector"
    (akar / "includes").mkdir(parents=True)
    (akar / "wp-manager-connector.php").write_text(HEADER, encoding="utf-8")
    (akar / "includes" / "class-a.php").write_text("<?php", encoding="utf-8")
    (akar / "uninstall.php").write_text("<?php", encoding="utf-8")
    for dikecualikan in ("tests", "vendor", ".git"):
        (akar / dikecualikan).mkdir()
        (akar / dikecualikan / "x.php").write_text("<?php", encoding="utf-8")
    return akar


def test_versi_dari_header(sumber):
    assert versi_dari_header(sumber / "wp-manager-connector.php") == "2.3.4"


def test_header_tanpa_versi_ditolak(tmp_path):
    berkas = tmp_path / "p.php"
    berkas.write_text("<?php // tanpa header", encoding="utf-8")
    with pytest.raises(ValueError):
        versi_dari_header(berkas)


def test_zip_berisi_direktori_puncak_tanpa_tests_vendor_dan_dotfile(sumber, tmp_path):
    tujuan = tmp_path / "keluar"
    bangun_paket(sumber, tujuan)
    with zipfile.ZipFile(tujuan / NAMA_ZIP) as z:
        nama = set(z.namelist())
    assert nama == {
        "wp-manager-connector/wp-manager-connector.php",
        "wp-manager-connector/includes/class-a.php",
        "wp-manager-connector/uninstall.php",
    }


def test_manifest_cocok_dengan_zip(sumber, tmp_path):
    tujuan = tmp_path / "keluar"
    manifest = bangun_paket(sumber, tujuan)
    isi = (tujuan / NAMA_ZIP).read_bytes()
    assert manifest["versi"] == "2.3.4"
    assert manifest["sha256"] == hashlib.sha256(isi).hexdigest()
    assert manifest["ukuran"] == len(isi)
    assert baca_manifest(tujuan) == manifest


def test_baca_manifest_none_bila_tidak_ada_atau_rusak(tmp_path):
    assert baca_manifest(tmp_path) is None
    (tmp_path / NAMA_MANIFEST).write_text("bukan json", encoding="utf-8")
    assert baca_manifest(tmp_path) is None
    (tmp_path / NAMA_MANIFEST).write_text(json.dumps({"versi": "1"}), encoding="utf-8")
    assert baca_manifest(tmp_path) is None
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_connector_paket.py -q`. Expected: `ModuleNotFoundError: No module named 'wpmgr.connector_paket'`.

- [ ] **Step 3: Implementasikan modul paket.**

File: `src/wpmgr/connector_paket.py`
```python
"""Paket zip connector yang dikirim dashboard lewat /self-update.

Zip dibangun sekali saat deploy (`wpmgr.cli build-connector`), bukan saat
permintaan: isinya harus sama persis untuk semua site, dan hash di
manifest-nya adalah yang diverifikasi connector sebelum memasang apa pun.
"""

import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

NAMA_DIREKTORI = "wp-manager-connector"
NAMA_ZIP = "wp-manager-connector.zip"
NAMA_MANIFEST = "manifest.json"
_DIKECUALIKAN = frozenset({"tests", "vendor", "node_modules", "__pycache__"})


def sumber_bawaan() -> Path:
    return Path(__file__).resolve().parents[2] / "connector" / NAMA_DIREKTORI


def versi_dari_header(berkas_utama: Path) -> str:
    teks = berkas_utama.read_text(encoding="utf-8")
    cocok = re.search(r"^\s*\*\s*Version:\s*(\S+)", teks, re.M)
    if cocok is None:
        raise ValueError(f"Header 'Version:' tidak ditemukan di {berkas_utama}")
    return cocok.group(1)


def _berkas_paket(sumber: Path):
    for jalur in sorted(sumber.rglob("*")):
        relatif = jalur.relative_to(sumber)
        if any(b in _DIKECUALIKAN or b.startswith(".") for b in relatif.parts):
            continue
        if jalur.is_file():
            yield jalur, relatif


def bangun_paket(sumber: Path, tujuan: Path) -> dict:
    versi = versi_dari_header(sumber / f"{NAMA_DIREKTORI}.php")
    tujuan.mkdir(parents=True, exist_ok=True)
    zip_akhir = tujuan / NAMA_ZIP
    sementara = tujuan / f"{NAMA_ZIP}.tmp"
    # Direktori puncak wajib: Plugin_Upgrader::install() memakai namanya
    # sebagai direktori tujuan di wp-content/plugins.
    with zipfile.ZipFile(sementara, "w", zipfile.ZIP_DEFLATED) as z:
        for jalur, relatif in _berkas_paket(sumber):
            z.write(jalur, f"{NAMA_DIREKTORI}/{relatif.as_posix()}")
    sementara.replace(zip_akhir)

    isi = zip_akhir.read_bytes()
    manifest = {
        "versi": versi,
        "sha256": hashlib.sha256(isi).hexdigest(),
        "ukuran": len(isi),
        "dibangun_pada": datetime.now(timezone.utc).isoformat(),
    }
    (tujuan / NAMA_MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def baca_manifest(tujuan: Path) -> dict | None:
    try:
        data = json.loads((tujuan / NAMA_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("versi") or not data.get("sha256"):
        return None
    return data


def baca_zip(tujuan: Path) -> bytes:
    return (tujuan / NAMA_ZIP).read_bytes()
```

- [ ] **Step 4: Jalankan test unit.** Expected: 5 lulus.

- [ ] **Step 5: Tambahkan perintah CLI.** Di `src/wpmgr/cli.py`, tambahkan impor `from pathlib import Path`, `from wpmgr.config import get_settings`, `from wpmgr.connector_paket import bangun_paket, sumber_bawaan`, lalu fungsi:

```python
def build_connector(sumber: str | None = None) -> dict:
    manifest = bangun_paket(
        Path(sumber) if sumber else sumber_bawaan(), get_settings().jalur_connector
    )
    print(
        f"Connector {manifest['versi']} dibangun ({manifest['ukuran']} byte), "
        f"sha256 {manifest['sha256']}"
    )
    return manifest
```

Di `main()`, daftarkan subperintah dan cabangnya:

```python
    b = sub.add_parser("build-connector")
    b.add_argument("--sumber", default=None)
```

```python
    elif args.perintah == "build-connector":
        build_connector(args.sumber)
```

- [ ] **Step 6: Fixture web bersama.** Tambahkan ke akhir `tests/integration/conftest.py`:

```python
@pytest.fixture
def pengguna_uji(sesi):
    from argon2 import PasswordHasher

    from wpmgr.models import User

    u = User(id=uuid.uuid4(), email="a@b.test", nama="Uji",
             password_hash=PasswordHasher().hash("sandi"))
    sesi.add(u)
    sesi.commit()
    return u


@pytest.fixture
def klien_web(engine, monkeypatch, pengguna_uji):
    """TestClient yang sudah login. base_url https: cookie sesi bertanda Secure."""
    from fastapi.testclient import TestClient

    from wpmgr import db
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(
        db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True)
    )
    c = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    c.post("/login", data={"email": "a@b.test", "password": "sandi"})
    return c


@pytest.fixture
def var_sementara(tmp_path, monkeypatch):
    """WPMGR_VAR_DIR diarahkan ke direktori sementara untuk satu test."""
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_VAR_DIR", str(tmp_path / "var"))
    get_settings.cache_clear()
    return tmp_path / "var"
```

- [ ] **Step 7: Tulis test integrasi yang gagal.**

File: `tests/integration/test_unduh_connector.py`
```python
import pytest
from fastapi.testclient import TestClient

from wpmgr.connector_paket import bangun_paket, sumber_bawaan

pytestmark = pytest.mark.integration


def test_unduh_butuh_login(engine):
    from wpmgr.web.app import buat_app

    c = TestClient(buat_app(), follow_redirects=False, base_url="https://testserver")
    assert c.get("/connector/unduh").status_code == 303


def test_unduh_404_bila_paket_belum_dibangun(klien_web, var_sementara):
    r = klien_web.get("/connector/unduh")
    assert r.status_code == 404
    assert "build-connector" in r.text


def test_unduh_menyajikan_zip_bernama_versi(klien_web, var_sementara):
    manifest = bangun_paket(sumber_bawaan(), var_sementara / "connector")
    r = klien_web.get("/connector/unduh")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f"wp-manager-connector-{manifest['versi']}.zip" in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"


def test_halaman_tambah_site_menautkan_unduhan(klien_web, var_sementara):
    bangun_paket(sumber_bawaan(), var_sementara / "connector")
    r = klien_web.get("/sites/new")
    assert 'href="/connector/unduh"' in r.text
```

- [ ] **Step 8: Jalankan dan pastikan gagal.** Expected: 404 untuk route yang belum ada pada test ketiga dan keempat.

- [ ] **Step 9: Implementasikan route dan tautan.** Di `src/wpmgr/web/routes_pages.py`, tambahkan impor `from fastapi.responses import FileResponse`, `from wpmgr.config import get_settings`, `from wpmgr.connector_paket import NAMA_ZIP, baca_manifest`, lalu:

```python
def _versi_connector() -> str | None:
    manifest = baca_manifest(get_settings().jalur_connector)
    return manifest["versi"] if manifest else None


@router.get("/connector/unduh")
def unduh_connector(pengguna: PenggunaHalaman):
    folder = get_settings().jalur_connector
    manifest = baca_manifest(folder)
    if manifest is None or not (folder / NAMA_ZIP).exists():
        raise HTTPException(
            status_code=404,
            detail="Paket connector belum dibangun. Jalankan: python -m wpmgr.cli build-connector",
        )
    return FileResponse(
        folder / NAMA_ZIP, media_type="application/zip",
        filename=f"wp-manager-connector-{manifest['versi']}.zip",
    )
```

Tambahkan `"versi_connector": _versi_connector()` ke setiap dict konteks yang dikirim ke `site_new.html` (di `form_site_baru` dan ketiga cabang `simpan_site`). Di `site_new.html`, sisipkan tepat setelah `<h1>Tambah Site</h1>`:

```html
<p>
  <a href="/connector/unduh">Unduh plugin connector</a>{% if versi_connector %} (versi {{ versi_connector }}){% endif %}.
  Pasang di site klien lewat <strong>Plugin → Tambah Baru → Unggah Plugin</strong>.
</p>
```

- [ ] **Step 10: Jalankan unit dan integrasi.** Expected: semua lulus.

- [ ] **Step 11: Commit.**

```bash
git add src/wpmgr/connector_paket.py src/wpmgr/cli.py src/wpmgr/web/routes_pages.py src/wpmgr/templates/site_new.html tests/unit/test_connector_paket.py tests/integration/conftest.py tests/integration/test_unduh_connector.py
git commit -m "feat: paket zip connector, perintah build-connector, dan unduhannya"
```

### Task 6: Endpoint `/self-update` di connector

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-selfupdate.php`, `connector/tests/SelfUpdateTest.php`
- Modify: `connector/wp-manager-connector/wp-manager-connector.php`, `includes/class-wpmgr-rest.php`, `includes/class-wpmgr-skema.php`, `connector/tests/SkemaTest.php`, `connector/tests/bootstrap.php`

**Interfaces:**
- Consumes: Task 2 (`WPMGR_Skema::fitur`), lock Lapis 1 (`WPMGR_Updater::NAMA_KUNCI`, `DETIK_KUNCI`, `sedang_sibuk()`, `galat_sibuk()`).
- Produces:
  - `POST /wp-json/wpmgr/v1/self-update` (HMAC). Body `{versi, sha256, zip_b64}`. Balasan sukses `{ok: true, versi_sebelum, versi_sesudah, pesan}`. Galat: 400 `wpmgr_payload_salah`, 400 `wpmgr_paket_rusak`, 409 `wpmgr_sibuk`, 500 `wpmgr_pasang_gagal`.
  - `WPMGR_SelfUpdate::periksa_paket( $payload )` → `array( versi, bytes )` atau `WP_Error`; `WPMGR_SelfUpdate::perlu_pasang( $terpasang, $versi ): bool`; `WPMGR_SelfUpdate::jalankan( $payload )`.
  - `WPMGR_Skema::fitur()` kini memuat `self_update`.

**Verifikasi core sebelum menulis kode** (Global Constraints). Baca `wp-admin/includes/class-plugin-upgrader.php` baris 118–173 (`install()`) dan 190–245 (`upgrade()`) di `D:\laragon\www\pacexports-wp`. Konfirmasikan di laporan bahwa `install()` **tidak** memasang `deactivate_plugin_before_upgrade` (baris 211 hanya milik `upgrade()`), sehingga menimpa plugin yang aktif lewat `install( $file, array( 'overwrite_package' => true ) )` tidak menonaktifkannya. Baca juga `WP_Upgrader::download_package()` di `class-wp-upgrader.php` dan konfirmasikan bahwa path file lokal dikembalikan apa adanya tanpa diunduh. Tempel nomor baris yang Anda baca.

- [ ] **Step 1: Tulis test PHP yang gagal.**

File: `connector/tests/SelfUpdateTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class SelfUpdateTest extends TestCase {

    private function payload( $isi = 'PK-isi-zip', $versi = '2.0.1' ) {
        return array(
            'versi'   => $versi,
            'sha256'  => hash( 'sha256', $isi ),
            'zip_b64' => base64_encode( $isi ),
        );
    }

    private function kode( $hasil ) {
        $this->assertInstanceOf( WP_Error::class, $hasil );
        return $hasil->get_error_code();
    }

    public function test_paket_sah_mengembalikan_versi_dan_isi(): void {
        $this->assertSame( array( '2.0.1', 'PK-isi-zip' ), WPMGR_SelfUpdate::periksa_paket( $this->payload() ) );
    }

    public function test_hash_huruf_besar_tetap_diterima(): void {
        $p           = $this->payload();
        $p['sha256'] = strtoupper( $p['sha256'] );
        $this->assertIsArray( WPMGR_SelfUpdate::periksa_paket( $p ) );
    }

    public function test_hash_tidak_cocok_ditolak(): void {
        $p           = $this->payload();
        $p['sha256'] = hash( 'sha256', 'lain' );
        $this->assertSame( 'wpmgr_paket_rusak', $this->kode( WPMGR_SelfUpdate::periksa_paket( $p ) ) );
    }

    public function test_base64_rusak_ditolak(): void {
        $p            = $this->payload();
        $p['zip_b64'] = '***';
        $this->assertSame( 'wpmgr_paket_rusak', $this->kode( WPMGR_SelfUpdate::periksa_paket( $p ) ) );
    }

    public function test_field_hilang_atau_bukan_string_ditolak(): void {
        $this->assertSame( 'wpmgr_payload_salah', $this->kode( WPMGR_SelfUpdate::periksa_paket( null ) ) );
        $p = $this->payload();
        unset( $p['versi'] );
        $this->assertSame( 'wpmgr_payload_salah', $this->kode( WPMGR_SelfUpdate::periksa_paket( $p ) ) );
        $p          = $this->payload();
        $p['versi'] = array( '2.0.1' );
        $this->assertSame( 'wpmgr_payload_salah', $this->kode( WPMGR_SelfUpdate::periksa_paket( $p ) ) );
    }

    public function test_versi_bukan_nomor_rilis_ditolak(): void {
        $this->assertSame( 'wpmgr_payload_salah',
            $this->kode( WPMGR_SelfUpdate::periksa_paket( $this->payload( 'x', '../../evil' ) ) ) );
        $this->assertIsArray( WPMGR_SelfUpdate::periksa_paket( $this->payload( 'x', '2.0.0.1' ) ) );
    }

    public function test_status_http_galat(): void {
        $e = WPMGR_SelfUpdate::periksa_paket( null );
        $this->assertSame( array( 'status' => 400 ), $e->get_error_data() );
    }

    public function test_perlu_pasang_hanya_bila_lebih_baru(): void {
        $this->assertTrue( WPMGR_SelfUpdate::perlu_pasang( '2.0.0', '2.0.1' ) );
        $this->assertTrue( WPMGR_SelfUpdate::perlu_pasang( '2.0.0', '2.0.0.1' ) );
        $this->assertFalse( WPMGR_SelfUpdate::perlu_pasang( '2.0.0', '2.0.0' ) );
        $this->assertFalse( WPMGR_SelfUpdate::perlu_pasang( '2.1.0', '2.0.9' ) );
    }
}
```

Ganti `test_belum_ada_fitur_yang_diumumkan` di `SkemaTest.php` dengan:

```php
    public function test_fitur_yang_diumumkan(): void {
        $this->assertSame( array( 'self_update' ), WPMGR_Skema::fitur( false ) );
        // Self-update bukan pemantauan: tetap tersedia walau pemantauan dimatikan.
        $this->assertSame( array( 'self_update' ), WPMGR_Skema::fitur( true ) );
    }
```

Tambahkan ke `bootstrap.php` setelah require `class-wpmgr-updater.php`:

```php
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-selfupdate.php';
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `cd connector && vendor/bin/phpunit`. Expected: fatal `Failed opening required ... class-wpmgr-selfupdate.php`.

- [ ] **Step 3: Implementasikan kelas.**

File: `connector/wp-manager-connector/includes/class-wpmgr-selfupdate.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Connector memasang versi barunya sendiri dari zip yang dikirim dashboard.
 *
 * Paket dikirim di dalam body, bukan diunduh site: sebagian shared hosting
 * memblokir koneksi keluar, dan arah dashboard -> site sudah terbukti bisa
 * dilalui saat pairing. Tanda tangan HMAC request mencakup hash body, dan
 * hash isi zip diperiksa lagi di sini sebelum apa pun ditulis ke disk.
 */
class WPMGR_SelfUpdate {

    private static function galat( $kode, $pesan, $status ) {
        return new WP_Error( $kode, $pesan, array( 'status' => $status ) );
    }

    public static function periksa_paket( $p ) {
        if ( ! is_array( $p ) ) {
            return self::galat( 'wpmgr_payload_salah', 'Payload self-update bukan objek.', 400 );
        }
        foreach ( array( 'versi', 'sha256', 'zip_b64' ) as $kunci ) {
            if ( ! isset( $p[ $kunci ] ) || ! is_string( $p[ $kunci ] ) || '' === $p[ $kunci ] ) {
                return self::galat( 'wpmgr_payload_salah', 'Payload self-update tidak lengkap.', 400 );
            }
        }
        // Versi ikut tampil di log dan dibandingkan dengan version_compare();
        // hanya bentuk nomor rilis yang diterima.
        if ( ! preg_match( '/^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$/', $p['versi'] ) ) {
            return self::galat( 'wpmgr_payload_salah', 'Versi paket tidak sah.', 400 );
        }
        $isi = base64_decode( $p['zip_b64'], true );
        if ( false === $isi || '' === $isi ) {
            return self::galat( 'wpmgr_paket_rusak', 'Isi paket tidak dapat dibaca.', 400 );
        }
        if ( ! hash_equals( strtolower( $p['sha256'] ), hash( 'sha256', $isi ) ) ) {
            return self::galat( 'wpmgr_paket_rusak', 'Hash paket tidak cocok dengan isinya.', 400 );
        }
        return array( $p['versi'], $isi );
    }

    public static function perlu_pasang( $terpasang, $versi ) {
        return version_compare( $versi, $terpasang, '>' );
    }

    /** Dibaca dari disk: konstanta WPMGR_VERSION di request ini milik kode lama. */
    public static function versi_di_disk() {
        $data = get_file_data( WPMGR_FILE, array( 'versi' => 'Version' ) );
        return (string) $data['versi'];
    }

    public static function jalankan( $p ) {
        $hasil = self::periksa_paket( $p );
        if ( is_wp_error( $hasil ) ) {
            return $hasil;
        }
        list( $versi, $isi ) = $hasil;

        $sebelum = self::versi_di_disk();
        if ( ! self::perlu_pasang( $sebelum, $versi ) ) {
            return array(
                'ok'            => true,
                'versi_sebelum' => $sebelum,
                'versi_sesudah' => $sebelum,
                'pesan'         => 'sudah di versi tersebut atau lebih baru',
            );
        }

        require_once ABSPATH . 'wp-admin/includes/file.php';
        require_once ABSPATH . 'wp-admin/includes/misc.php';
        require_once ABSPATH . 'wp-admin/includes/plugin.php';
        require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';

        // Lock yang sama dengan /update: memasang connector sambil meng-upgrade
        // plugin lain berarti dua upgrader berebut wp-content/upgrade.
        if ( WPMGR_Updater::sedang_sibuk()
            || ! WP_Upgrader::create_lock( WPMGR_Updater::NAMA_KUNCI, WPMGR_Updater::DETIK_KUNCI ) ) {
            return WPMGR_Updater::galat_sibuk();
        }

        $berkas = wp_tempnam( 'wpmgr-connector.zip' );
        try {
            ignore_user_abort( true );
            if ( false === file_put_contents( $berkas, $isi ) ) {
                return self::galat( 'wpmgr_pasang_gagal', 'Paket tidak dapat ditulis ke direktori sementara.', 500 );
            }

            $skin     = new Automatic_Upgrader_Skin();
            $upgrader = new Plugin_Upgrader( $skin );
            // install() dengan overwrite_package, bukan upgrade(): upgrade()
            // menonaktifkan plugin yang aktif di luar WP-cron (bug C1 Lapis 1),
            // dan connector yang nonaktif tidak bisa menjawab siapa pun lagi.
            $r = $upgrader->install( $berkas, array( 'overwrite_package' => true ) );

            if ( is_wp_error( $r ) ) {
                return self::galat( 'wpmgr_pasang_gagal', $r->get_error_message(), 500 );
            }
            if ( ! $r ) {
                $pesan = implode( ' | ', (array) $skin->get_upgrade_messages() );
                return self::galat( 'wpmgr_pasang_gagal',
                    $pesan ? $pesan : 'Pemasangan mengembalikan false tanpa pesan.', 500 );
            }

            return array(
                'ok'            => true,
                'versi_sebelum' => $sebelum,
                'versi_sesudah' => self::versi_di_disk(),
                'pesan'         => implode( ' | ', (array) $skin->get_upgrade_messages() ),
            );
        } finally {
            if ( file_exists( $berkas ) ) {
                @unlink( $berkas ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
            WP_Upgrader::release_lock( WPMGR_Updater::NAMA_KUNCI );
        }
    }
}
```

- [ ] **Step 4: Daftarkan route dan umumkan fitur.** Di `wp-manager-connector.php`, tambahkan `require_once WPMGR_DIR . 'includes/class-wpmgr-selfupdate.php';` setelah require updater. Di `WPMGR_REST::daftarkan_route()`, tambahkan:

```php
        register_rest_route( self::NS, '/self-update', array(
            'methods'             => 'POST',
            'callback'            => array( __CLASS__, 'self_update' ),
            'permission_callback' => $guard,
        ) );
```

dan metode:

```php
    public static function self_update( $request ) {
        return rest_ensure_response(
            WPMGR_SelfUpdate::jalankan( json_decode( $request->get_body(), true ) )
        );
    }
```

Di `WPMGR_Skema::fitur()`, ganti isi fungsinya:

```php
        return array( 'self_update' );
```

- [ ] **Step 5: Jalankan PHPUnit dan lint PHP 7.4 (Task 2 Step 9).** Expected: semua lulus.

- [ ] **Step 6: Commit.**

```bash
git add connector
git commit -m "feat(connector): endpoint self-update dengan verifikasi hash dan lock upgrade"
```

### Task 7: Job `update_connector`, API, dan aksi massal di halaman Site

**Files:**
- Create: `src/wpmgr/jobs/monitoring.py`, `tests/integration/test_update_connector.py`, `tests/e2e/test_self_update.py`
- Modify: `src/wpmgr/errors.py`, `src/wpmgr/jobs/queue.py`, `src/wpmgr/jobs/handlers.py`, `src/wpmgr/web/routes_api.py`, `src/wpmgr/templates/sites.html`, `src/wpmgr/static/app/sites.js`, `tests/unit/test_errors.py`

**Interfaces:**
- Consumes: Task 4 (`SiteClient.self_update`, `punya_fitur`, `SELF_UPDATE`, `lebih_lama`), Task 5 (`baca_manifest`, `baca_zip`), Task 6 (endpoint dan kode galat).
- Produces:
  - `wpmgr.jobs.queue.buat_job(..., max_attempts: int | None = None)`; `antrekan_jika_belum(sesi, site_id, tipe: JobType, max_attempts: int | None = None) -> Job | None`. `antrekan_scan` kini mendelegasikan ke fungsi ini.
  - `wpmgr.jobs.monitoring.tangani_update_connector(sesi, job, klien) -> dict`; `HANDLER[JobType.update_connector]`.
  - `wpmgr.errors.KODE_PASANG_GAGAL = "wpmgr_pasang_gagal"` → `UPGRADE_FAILED`; `KODE_PAKET_RUSAK = "wpmgr_paket_rusak"` (400 → `BAD_RESPONSE`, perilaku yang sudah ada).
  - `POST /api/jobs/update-connector` body `{site_ids: [uuid]}` → `{job_ids: [int]}`; 409 bila paket belum dibangun atau ada site tanpa fitur `self_update` (semua-atau-tidak-sama-sekali, R46).
  - `/api/sites` menambah `connector_version`, `connector_usang: bool`, `bisa_self_update: bool`.

- [ ] **Step 1: Tulis test unit klasifikasi yang gagal.** Tambahkan ke `tests/unit/test_errors.py`:

```python
def test_pasang_connector_gagal_menjadi_upgrade_failed():
    from wpmgr.errors import UPGRADE_FAILED, klasifikasi_respons

    body = '{"code":"wpmgr_pasang_gagal","message":"Could not create directory."}'
    assert klasifikasi_respons(500, {}, body) == UPGRADE_FAILED


def test_paket_connector_rusak_menjadi_bad_response():
    from wpmgr.errors import BAD_RESPONSE, klasifikasi_respons

    body = '{"code":"wpmgr_paket_rusak","message":"Hash paket tidak cocok dengan isinya."}'
    assert klasifikasi_respons(400, {}, body) == BAD_RESPONSE
```

Run: `.venv/Scripts/python -m pytest tests/unit/test_errors.py -q`. Expected: test pertama gagal (`'transient' == 'upgrade_failed'`).

- [ ] **Step 2: Perbaiki klasifikasi.** Di `src/wpmgr/errors.py`, tambahkan setelah `KODE_UPGRADE_GAGAL`:

```python
KODE_PASANG_GAGAL = "wpmgr_pasang_gagal"
KODE_PAKET_RUSAK = "wpmgr_paket_rusak"
```

dan di cabang `status >= 500`, ubah kondisinya menjadi `if kode in (KODE_UPGRADE_GAGAL, KODE_PASANG_GAGAL):`. Jalankan ulang: lulus.

- [ ] **Step 3: Tulis test integrasi yang gagal.**

File: `tests/integration/test_update_connector.py`
```python
import hashlib
import json

import httpx
import pytest

from wpmgr.connector_paket import bangun_paket, sumber_bawaan
from wpmgr.jobs.monitoring import tangani_update_connector
from wpmgr.jobs.queue import antrekan_jika_belum, buat_job
from wpmgr.models import ActivityLog, Job, JobStatus, JobType
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration


def klien_mencatat(tampung, balasan):
    def handler(request):
        tampung.append(request)
        return httpx.Response(200, json=balasan)

    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_handler_mengirim_paket_dan_mencatat_versi(sesi, site, var_sementara, pengguna_uji):
    manifest = bangun_paket(sumber_bawaan(), var_sementara / "connector")
    job = buat_job(sesi, site.id, JobType.update_connector, {"versi": manifest["versi"]},
                   dibuat_oleh=pengguna_uji.id)
    tampung = []
    hasil = tangani_update_connector(sesi, job, klien_mencatat(
        tampung, {"ok": True, "versi_sebelum": "1.0.0", "versi_sesudah": manifest["versi"],
                  "pesan": ""}))

    body = json.loads(tampung[0].content)
    assert body["versi"] == manifest["versi"]
    assert body["sha256"] == manifest["sha256"]
    assert tampung[0].url.path == "/wp-json/wpmgr/v1/self-update"
    assert hasil["versi_sesudah"] == manifest["versi"]
    sesi.refresh(site)
    assert site.connector_version == manifest["versi"]
    log = sesi.query(ActivityLog).filter_by(site_id=site.id, level="info").one()
    assert "1.0.0" in log.pesan and manifest["versi"] in log.pesan
    assert log.detail["email"] == "a@b.test"
    # Verify dijadwalkan supaya daftar fitur versi baru segera tercatat.
    assert sesi.query(Job).filter_by(site_id=site.id, tipe=JobType.verify_site).count() == 1


def test_handler_tanpa_paket_melempar_kesalahan_dashboard(sesi, site, var_sementara):
    job = buat_job(sesi, site.id, JobType.update_connector, {"versi": "9.9.9"})
    with pytest.raises(RuntimeError, match="build-connector"):
        tangani_update_connector(sesi, job, klien_mencatat([], {}))


def test_handler_menolak_zip_yang_tidak_cocok_dengan_manifest(sesi, site, var_sementara):
    bangun_paket(sumber_bawaan(), var_sementara / "connector")
    (var_sementara / "connector" / "wp-manager-connector.zip").write_bytes(b"diubah")
    job = buat_job(sesi, site.id, JobType.update_connector, {"versi": "x"})
    with pytest.raises(RuntimeError, match="tidak cocok"):
        tangani_update_connector(sesi, job, klien_mencatat([], {}))


def test_antrekan_jika_belum_tidak_menggandakan(sesi, site):
    assert antrekan_jika_belum(sesi, site.id, JobType.verify_site) is not None
    assert antrekan_jika_belum(sesi, site.id, JobType.verify_site) is None
    job = antrekan_jika_belum(sesi, site.id, JobType.collect_events, max_attempts=1)
    assert job.max_attempts == 1


def test_api_semua_atau_tidak_sama_sekali(klien_web, sesi, site, var_sementara):
    from wpmgr.models import Site, SiteStatus

    bangun_paket(sumber_bawaan(), var_sementara / "connector")
    site.fitur = ["self_update"]
    lama = Site(nama="Lama", url="https://lama.test", status=SiteStatus.active,
                secret_terenkripsi=b"x", fitur=[])
    sesi.add(lama)
    sesi.commit()

    r = klien_web.post("/api/jobs/update-connector",
                       json={"site_ids": [str(site.id), str(lama.id)]})
    assert r.status_code == 409
    assert "Lama" in r.json()["detail"]
    assert sesi.query(Job).filter_by(tipe=JobType.update_connector).count() == 0


def test_api_membuat_job_dengan_versi_manifest(klien_web, sesi, site, var_sementara):
    manifest = bangun_paket(sumber_bawaan(), var_sementara / "connector")
    site.fitur = ["self_update"]
    sesi.commit()
    r = klien_web.post("/api/jobs/update-connector", json={"site_ids": [str(site.id)]})
    assert r.status_code == 200
    job = sesi.get(Job, r.json()["job_ids"][0])
    assert job.tipe == JobType.update_connector
    assert job.payload == {"versi": manifest["versi"]}
    assert job.status == JobStatus.pending


def test_api_409_bila_paket_belum_dibangun(klien_web, sesi, site, var_sementara):
    site.fitur = ["self_update"]
    sesi.commit()
    r = klien_web.post("/api/jobs/update-connector", json={"site_ids": [str(site.id)]})
    assert r.status_code == 409
    assert "build-connector" in r.json()["detail"]


def test_api_sites_menandai_connector_usang(klien_web, sesi, site, var_sementara):
    bangun_paket(sumber_bawaan(), var_sementara / "connector")
    site.connector_version = "1.0.0"
    site.fitur = ["self_update"]
    sesi.commit()
    baris = next(b for b in klien_web.get("/api/sites").json() if b["id"] == str(site.id))
    assert baris["connector_version"] == "1.0.0"
    assert baris["connector_usang"] is True
    assert baris["bisa_self_update"] is True


def test_sha_manifest_konsisten(var_sementara):
    manifest = bangun_paket(sumber_bawaan(), var_sementara / "connector")
    isi = (var_sementara / "connector" / "wp-manager-connector.zip").read_bytes()
    assert hashlib.sha256(isi).hexdigest() == manifest["sha256"]
```

Run dan pastikan gagal: `ModuleNotFoundError: No module named 'wpmgr.jobs.monitoring'`.

- [ ] **Step 4: Antrean.** Di `src/wpmgr/jobs/queue.py`, ubah `buat_job` dan tambahkan `antrekan_jika_belum`:

```python
def buat_job(
    sesi: Session,
    site_id: uuid.UUID,
    tipe: JobType,
    payload: dict | None = None,
    dibuat_oleh: uuid.UUID | None = None,
    scheduled_for: datetime | None = None,
    max_attempts: int | None = None,
) -> Job:
    job = Job(site_id=site_id, tipe=tipe, payload=payload or {}, dibuat_oleh=dibuat_oleh)
    if scheduled_for is not None:
        job.scheduled_for = scheduled_for
    if max_attempts is not None:
        job.max_attempts = max_attempts
    sesi.add(job)
    sesi.commit()
    return job


def antrekan_jika_belum(
    sesi: Session, site_id: uuid.UUID, tipe: JobType, max_attempts: int | None = None
) -> Job | None:
    """Buat job bertipe ini untuk site, kecuali sudah ada yang tertunda/berjalan.

    Periksa-lalu-sisipkan tidak atomik: dua pemanggil bersamaan bisa sama-sama
    membuat job. Itu dibiarkan -- semua pemakainya (scan, verify, pengambilan
    berkala) hanya membaca, jadi job ganda tidak merusak apa pun, sedangkan
    kunci advisory demi mencegahnya menambah bagian bergerak tanpa
    melindungi apa pun yang berharga.
    """
    sudah_ada = sesi.scalar(
        select(Job.id).where(
            Job.site_id == site_id,
            Job.tipe == tipe,
            Job.status.in_([JobStatus.pending, JobStatus.running]),
        )
    )
    if sudah_ada is not None:
        return None
    return buat_job(sesi, site_id, tipe, max_attempts=max_attempts)


def antrekan_scan(sesi: Session, site_id: uuid.UUID) -> Job | None:
    return antrekan_jika_belum(sesi, site_id, JobType.scan_site)
```

(Hapus badan dan docstring lama `antrekan_scan`; alasannya kini ada di `antrekan_jika_belum`.)

- [ ] **Step 5: Handler.**

File: `src/wpmgr/jobs/monitoring.py`
```python
"""Handler job Lapis 2: pengambilan data pemantauan dan pembaruan connector.

Modul ini tidak mengimpor wpmgr.jobs.handlers; handlers yang mengimpor modul
ini untuk menyusun HANDLER.
"""

import hashlib

from sqlalchemy.orm import Session

from wpmgr.config import get_settings
from wpmgr.connector_paket import baca_manifest, baca_zip
from wpmgr.jobs.queue import antrekan_jika_belum
from wpmgr.models import ActivityLog, Job, JobType, Site, User
from wpmgr.site_client import SiteClient


def _email_pembuat(sesi: Session, job: Job) -> str | None:
    if job.dibuat_oleh is None:
        return None
    pembuat = sesi.get(User, job.dibuat_oleh)
    return pembuat.email if pembuat is not None else None


def tangani_update_connector(sesi: Session, job: Job, klien: SiteClient) -> dict:
    folder = get_settings().jalur_connector
    manifest = baca_manifest(folder)
    if manifest is None:
        # Kesalahan konfigurasi dashboard, bukan kondisi site: RuntimeError
        # membuat worker menandainya internal_error tanpa retry.
        raise RuntimeError(
            "Paket connector belum dibangun; jalankan python -m wpmgr.cli build-connector"
        )
    isi = baca_zip(folder)
    if hashlib.sha256(isi).hexdigest() != manifest["sha256"]:
        raise RuntimeError("Zip connector tidak cocok dengan manifest-nya; bangun ulang paket")

    site = sesi.get(Site, job.site_id)
    hasil = klien.self_update(manifest["versi"], manifest["sha256"], isi)
    sebelum = hasil.get("versi_sebelum") or site.connector_version
    sesudah = hasil.get("versi_sesudah") or manifest["versi"]
    site.connector_version = sesudah

    email = _email_pembuat(sesi, job)
    oleh = f" oleh {email}" if email else ""
    detail = {"versi_sebelum": sebelum, "versi_sesudah": sesudah, "email": email}
    if hasil.get("pesan"):
        detail["pesan"] = str(hasil["pesan"])[:500]
    sesi.add(
        ActivityLog(
            site_id=site.id, job_id=job.id, user_id=job.dibuat_oleh, level="info",
            pesan=f"Connector diperbarui: {sebelum or '?'} → {sesudah}{oleh}",
            detail=detail,
        )
    )
    sesi.commit()
    # Versi baru mungkin mengumumkan fitur baru; verify mencatatnya segera,
    # bukan menunggu scan per jam.
    antrekan_jika_belum(sesi, site.id, JobType.verify_site)
    return hasil
```

Di `src/wpmgr/jobs/handlers.py`, tambahkan di bagian paling bawah (setelah fungsi-fungsi, sebelum atau menggantikan definisi `HANDLER`):

```python
from wpmgr.jobs.monitoring import tangani_update_connector  # noqa: E402

HANDLER = {
    JobType.scan_site: tangani_scan_site,
    JobType.update_package: tangani_update_package,
    JobType.verify_site: tangani_verify_site,
    JobType.update_connector: tangani_update_connector,
}
```

(Impor di bawah dipilih supaya tidak ada impor melingkar di masa depan; bila ruff tidak mengeluh, impor boleh dipindah ke atas bersama impor lain.)

- [ ] **Step 6: API.** Di `src/wpmgr/web/routes_api.py`, tambahkan impor `from wpmgr.config import get_settings`, `from wpmgr.connector_paket import baca_manifest`, `from wpmgr.fitur import SELF_UPDATE, punya_fitur`, `from wpmgr.versi import lebih_lama`. Tambahkan model dan route:

```python
class PermintaanUpdateConnector(BaseModel):
    site_ids: list[uuid.UUID]


@router.post("/api/jobs/update-connector")
def buat_job_update_connector(req: PermintaanUpdateConnector, pengguna: PenggunaApi):
    manifest = baca_manifest(get_settings().jalur_connector)
    if manifest is None:
        raise HTTPException(
            status_code=409,
            detail="Paket connector belum dibangun di server dashboard. "
                   "Jalankan: python -m wpmgr.cli build-connector",
        )
    with db.SessionLocal() as sesi:
        # Semua divalidasi sebelum satu job pun dibuat (R46 Lapis 1).
        sites = []
        for site_id in req.site_ids:
            site = sesi.get(Site, site_id)
            if site is None:
                raise HTTPException(status_code=404, detail=f"Site {site_id} tidak ditemukan")
            if not punya_fitur(site, SELF_UPDATE):
                raise HTTPException(
                    status_code=409,
                    detail=f"Site '{site.nama}' memakai connector yang belum bisa diperbarui "
                           f"dari dashboard. Pasang connector {manifest['versi']} sekali secara "
                           f"manual lewat wp-admin.",
                )
            sites.append(site)
        ids = [
            buat_job(sesi, s.id, JobType.update_connector, {"versi": manifest["versi"]},
                     dibuat_oleh=pengguna.id).id
            for s in sites
        ]
    return {"job_ids": ids}
```

Di `daftar_site`, hitung `versi_terbaru = (baca_manifest(get_settings().jalur_connector) or {}).get("versi")` di awal fungsi, lalu tambahkan tiga field ke dict setiap baris:

```python
                "connector_version": s.connector_version,
                "connector_usang": lebih_lama(s.connector_version, versi_terbaru),
                "bisa_self_update": punya_fitur(s, SELF_UPDATE),
```

- [ ] **Step 7: UI halaman Site.** Ganti `src/wpmgr/templates/sites.html`:

```html
{% extends "base.html" %}
{% block judul %}Site — WP Manager{% endblock %}
{% block isi %}
<div x-data="layarSite()" x-init="muat()">
  <h1>Site</h1>
  <div class="toolbar">
    <button @click="perbaruiConnector()" :disabled="terpilih.length === 0 || mengirim">
      <span x-text="`Perbarui connector (${terpilih.length} terpilih)`"></span>
    </button>
  </div>
  <p class="info" x-show="info" x-text="info" role="status"></p>
  <p class="galat" x-show="galat" x-text="galat" role="alert"></p>
  <div id="grid" style="height: 70vh"></div>
</div>
<script src="/static/app/sites.js"></script>
{% endblock %}
```

Di `src/wpmgr/static/app/sites.js`:

1. Tambahkan state `terpilih: [], mengirim: false, info: '',` di awal objek yang dikembalikan `layarSite()`.
2. Tambahkan kolom setelah kolom `php_version`:

```js
          {
            dataField: 'connector_version',
            caption: 'Connector',
            // Versi dilaporkan site client, jadi tetap lewat esc(); kelas
            // badge hanya salah satu dari dua string tetap.
            cellTemplate: (v, baris) =>
              `${esc(v || '—')}${baris.connector_usang ? ' <span class="dg-badge-warning">usang</span>' : ''}`,
          },
```

DataGrid memanggil `cellTemplate(nilai, baris, grid)` (`static/vendor/datagrid/datagrid.js:743-744`), jadi data baris tersedia di argumen kedua.

3. Tambahkan `onSelectionChanged: (e) => { this.terpilih = e.rows; },` ke opsi `new DataGrid(...)`.
4. Tambahkan metode:

```js
    async perbaruiConnector() {
      this.galat = '';
      this.info = '';
      const tidakBisa = this.terpilih.filter((b) => !b.bisa_self_update);
      if (tidakBisa.length) {
        this.galat = `${tidakBisa.length} site memakai connector lama yang harus diperbarui ` +
          'manual sekali lewat wp-admin: ' + tidakBisa.map((b) => b.nama).join(', ');
        return;
      }
      this.mengirim = true;
      try {
        const r = await fetch('/api/jobs/update-connector', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ site_ids: this.terpilih.map((b) => b.id) }),
        });
        if (!r.ok) {
          this.galat = `Pembaruan tidak dijadwalkan. ${await pesanGalat(r)}`;
          return;
        }
        const data = await r.json();
        this.info = `${data.job_ids.length} pembaruan connector dijadwalkan. Pantau hasilnya di halaman Aktivitas.`;
      } catch (e) {
        this.galat = `Gagal menghubungi server: ${e.message}`;
      } finally {
        this.mengirim = false;
      }
    },
```

Tambahkan `.info{color:#1f6f43}` ke `static/app/app.css`.

- [ ] **Step 8: Jalankan unit dan integrasi.** Expected: semua lulus.

- [ ] **Step 9: Test e2e self-update.**

File: `tests/e2e/test_self_update.py`
```python
import shutil

import pytest

from wpmgr.config import get_settings
from wpmgr.connector_paket import bangun_paket
from wpmgr.jobs.queue import buat_job
from wpmgr.models import Job, JobStatus, JobType
from wpmgr.worker import proses_satu

from .conftest import (
    AKAR_REPO,
    _wpcli_status,
    klien_http,
    sinkronkan_connector,
    versi_connector_sumber,
)

pytestmark = pytest.mark.e2e


@pytest.fixture
def connector_dipulihkan():
    yield
    # Test ini menimpa connector di container dengan versi uji; sesi e2e
    # berikutnya dan test lain harus kembali memakai kode checkout.
    sinkronkan_connector()


def _jalankan(sesi):
    return proses_satu(sesi, "uji-e2e", buat_klien_fn=klien_http)


def test_self_update_mengganti_versi_dan_connector_tetap_aktif(
    sesi, site_terpasang, tmp_path, monkeypatch, connector_dipulihkan
):
    versi_lama = versi_connector_sumber()
    versi_baru = versi_lama + ".1"
    salinan = tmp_path / "wp-manager-connector"
    shutil.copytree(AKAR_REPO / "connector" / "wp-manager-connector", salinan)
    utama = salinan / "wp-manager-connector.php"
    teks = utama.read_text(encoding="utf-8")
    teks = teks.replace(f"Version:     {versi_lama}", f"Version:     {versi_baru}")
    teks = teks.replace(f"'WPMGR_VERSION', '{versi_lama}'", f"'WPMGR_VERSION', '{versi_baru}'")
    utama.write_text(teks, encoding="utf-8")

    monkeypatch.setenv("WPMGR_VAR_DIR", str(tmp_path / "var"))
    get_settings.cache_clear()
    bangun_paket(salinan, get_settings().jalur_connector)

    buat_job(sesi, site_terpasang.id, JobType.verify_site)
    assert _jalankan(sesi)
    sesi.refresh(site_terpasang)
    assert "self_update" in site_terpasang.fitur

    job = buat_job(sesi, site_terpasang.id, JobType.update_connector, {"versi": versi_baru})
    assert _jalankan(sesi)
    sesi.refresh(job)
    assert job.status == JobStatus.success, job.error

    assert klien_http(site_terpasang).ping()["connector_version"] == versi_baru
    assert _wpcli_status("plugin", "is-active", "wp-manager-connector") == 0
    # Repo tidak tersentuh: self-update menimpa salinan di container.
    assert versi_connector_sumber() == versi_lama

    # Mengulang dengan paket yang sama idempoten.
    job2 = buat_job(sesi, site_terpasang.id, JobType.update_connector, {"versi": versi_baru})
    assert _jalankan(sesi)
    sesi.refresh(job2)
    assert job2.status == JobStatus.success
    assert "sudah di versi" in job2.hasil["pesan"]
    assert sesi.query(Job).filter_by(tipe=JobType.verify_site).count() >= 1
```

Run: `.venv/Scripts/python -m pytest tests/e2e/test_self_update.py -q`. Expected: lulus. Bila connector ternyata nonaktif setelah pemasangan, **jangan** mengakalinya dengan mengaktifkan ulang di test. Laporkan temuan itu beserta baris core yang menjelaskannya, karena itu berarti asumsi Task 6 salah.

- [ ] **Step 10: Seluruh suite.** Jalankan unit, integrasi, PHPUnit, dan e2e. Expected: semua lulus.

- [ ] **Step 11: Commit.**

```bash
git add src/wpmgr tests
git commit -m "feat: pembaruan connector dari dashboard lewat job update_connector"
```

---

## Fase C — Uptime dan SSL

### Task 8: Penilaian uptime (logika murni)

**Files:**
- Create: `src/wpmgr/uptime.py`, `tests/unit/test_uptime.py`

**Interfaces:**
- Consumes: Task 1 (`UptimeHasil`, `UptimeStatus`).
- Produces (di `wpmgr.uptime`):
  - Konstanta `GAGAL_UNTUK_MATI = 2`, `MIN_SITE_ATURAN_GANGGUAN = 5`, `AMBANG_GANGGUAN = 0.8`.
  - `@dataclass(frozen=True) HasilCek(hasil: UptimeHasil, http_status: int | None = None, waktu_ms: int | None = None, pesan: str | None = None)`.
  - `nilai_respons(status: int, headers: dict[str, str], body: str, waktu_ms: int) -> HasilCek`.
  - `nilai_kesalahan(exc: Exception) -> HasilCek`.
  - `gangguan_dashboard(jumlah_site: int, jumlah_gagal: int) -> bool`.
  - `@dataclass(frozen=True) Transisi(status: UptimeStatus, gagal_beruntun: int, buka_insiden: bool, tutup_insiden: bool)`.
  - `terapkan(status_lama: UptimeStatus, gagal_beruntun: int, hasil: UptimeHasil) -> Transisi`.

Ingat koreksi #2 di bagian atas rencana: deteksi "terblokir" **tidak** memakai `errors._terlihat_firewall`, karena fungsi itu menganggap `cf-ray` sebagai tanda blokir.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/unit/test_uptime.py`
```python
import httpx
import pytest

from wpmgr.models import UptimeHasil, UptimeStatus
from wpmgr.uptime import (
    gangguan_dashboard,
    nilai_kesalahan,
    nilai_respons,
    terapkan,
)


def test_2xx_naik():
    h = nilai_respons(200, {}, "<html>ok</html>", 120)
    assert (h.hasil, h.http_status, h.waktu_ms) == (UptimeHasil.naik, 200, 120)


def test_5xx_gagal_dengan_pesan():
    h = nilai_respons(500, {}, "There has been a critical error on this website.", 80)
    assert h.hasil == UptimeHasil.gagal
    assert h.pesan == "HTTP 500"


def test_503_origin_di_balik_cloudflare_tetap_gagal():
    # Halaman 52x/503 Cloudflare saat origin mati memuat cf-ray DAN kata
    # "cloudflare". Keduanya bukan tanda blokir; menganggapnya blokir
    # menyembunyikan setiap site mati yang memakai Cloudflare.
    body = "<title>example.com | 521: Web server is down</title> ... Cloudflare Ray ID"
    h = nilai_respons(521, {"cf-ray": "8a1b2c3d4e5f-SIN", "server": "cloudflare"}, body, 300)
    assert h.hasil == UptimeHasil.gagal
    h = nilai_respons(503, {"cf-ray": "8a1b-SIN", "server": "cloudflare"}, "Service Unavailable", 300)
    assert h.hasil == UptimeHasil.gagal


@pytest.mark.parametrize(
    ("status", "headers", "body"),
    [
        (403, {"cf-mitigated": "challenge"}, ""),
        (503, {}, "<title>Just a moment...</title><script>window._cf_chl_opt={}</script>"),
        (403, {}, "<title>Attention Required! | Cloudflare</title> Sorry, you have been blocked"),
        (403, {}, "<h1>Your access to this site has been limited by the site owner</h1> Wordfence"),
        (503, {}, "Generated by Wordfence at Tue, 22 Sep 2026"),
        (429, {}, "Too Many Requests"),
    ],
)
def test_halaman_tantangan_dan_blokir_terblokir(status, headers, body):
    assert nilai_respons(status, headers, body, 50).hasil == UptimeHasil.terblokir


def test_403_biasa_gagal():
    assert nilai_respons(403, {}, "Forbidden", 50).hasil == UptimeHasil.gagal


def test_404_gagal():
    assert nilai_respons(404, {}, "Not Found", 50).hasil == UptimeHasil.gagal


def test_kesalahan_timeout():
    h = nilai_kesalahan(httpx.ReadTimeout("x"))
    assert h.hasil == UptimeHasil.gagal
    assert "15 detik" in h.pesan


def test_kesalahan_redirect_berlebihan():
    h = nilai_kesalahan(httpx.TooManyRedirects("x"))
    assert "Redirect" in h.pesan


def test_kesalahan_sertifikat():
    h = nilai_kesalahan(httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired"))
    assert h.pesan.startswith("Sertifikat SSL tidak valid")


def test_kesalahan_dns():
    h = nilai_kesalahan(httpx.ConnectError("[Errno -2] Name or service not known"))
    assert "DNS" in h.pesan


def test_kesalahan_koneksi_lain():
    h = nilai_kesalahan(httpx.ConnectError("[Errno 111] Connection refused"))
    assert h.pesan.startswith("Koneksi gagal")


@pytest.mark.parametrize(
    ("site", "gagal", "harapan"),
    [(10, 9, True), (10, 8, False), (5, 5, True), (4, 4, False), (0, 0, False)],
)
def test_aturan_gangguan_dashboard(site, gagal, harapan):
    assert gangguan_dashboard(site, gagal) is harapan


def test_satu_kegagalan_belum_mati():
    t = terapkan(UptimeStatus.naik, 0, UptimeHasil.gagal)
    assert (t.status, t.gagal_beruntun, t.buka_insiden) == (UptimeStatus.naik, 1, False)


def test_kegagalan_kedua_mati_dan_membuka_insiden():
    t = terapkan(UptimeStatus.naik, 1, UptimeHasil.gagal)
    assert (t.status, t.gagal_beruntun, t.buka_insiden) == (UptimeStatus.mati, 2, True)


def test_sudah_mati_tidak_membuka_insiden_lagi():
    t = terapkan(UptimeStatus.mati, 2, UptimeHasil.gagal)
    assert (t.status, t.gagal_beruntun, t.buka_insiden) == (UptimeStatus.mati, 3, False)


def test_naik_mengembalikan_penghitung_dan_menutup():
    t = terapkan(UptimeStatus.mati, 5, UptimeHasil.naik)
    assert (t.status, t.gagal_beruntun, t.tutup_insiden) == (UptimeStatus.naik, 0, True)


def test_terblokir_tidak_mengubah_penghitung_dan_tanpa_insiden():
    t = terapkan(UptimeStatus.naik, 1, UptimeHasil.terblokir)
    assert (t.status, t.gagal_beruntun, t.buka_insiden, t.tutup_insiden) == (
        UptimeStatus.terblokir, 1, False, False)


def test_site_baru_gagal_sekali_tetap_belum_dicek():
    t = terapkan(UptimeStatus.belum_dicek, 0, UptimeHasil.gagal)
    assert t.status == UptimeStatus.belum_dicek
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: `ModuleNotFoundError: No module named 'wpmgr.uptime'`.

- [ ] **Step 3: Implementasikan.**

File: `src/wpmgr/uptime.py`
```python
"""Uptime: menilai satu cek, menentukan transisi status, dan menjalankan putaran.

Bagian penilaian murni (tanpa jaringan dan database) supaya aturan yang
menentukan kapan sebuah site dinyatakan mati bisa diuji langsung.
"""

from dataclasses import dataclass

import httpx

from wpmgr.models import UptimeHasil, UptimeStatus

GAGAL_UNTUK_MATI = 2
MIN_SITE_ATURAN_GANGGUAN = 5
AMBANG_GANGGUAN = 0.8

# Hanya penanda halaman tantangan/blokir. Header cf-ray dan kata "cloudflare"
# sengaja TIDAK dipakai: Cloudflare membubuhkannya ke setiap respons yang ia
# proksikan, termasuk halaman 52x ketika origin benar-benar mati (R15 Lapis 1).
_PENANDA_BLOKIR = (
    "_cf_chl_opt",
    "challenge-platform",
    "<title>just a moment...</title>",
    "attention required! | cloudflare",
    "sorry, you have been blocked",
    "your access to this site has been limited",
    "generated by wordfence",
)


@dataclass(frozen=True)
class HasilCek:
    hasil: UptimeHasil
    http_status: int | None = None
    waktu_ms: int | None = None
    pesan: str | None = None


@dataclass(frozen=True)
class Transisi:
    status: UptimeStatus
    gagal_beruntun: int
    buka_insiden: bool
    tutup_insiden: bool


def _halaman_blokir(headers: dict[str, str], body: str) -> bool:
    rendah = {k.lower(): (v or "").lower() for k, v in headers.items()}
    if rendah.get("cf-mitigated") == "challenge":
        return True
    cuplikan = body[:5000].lower()
    return any(p in cuplikan for p in _PENANDA_BLOKIR)


def nilai_respons(status: int, headers: dict[str, str], body: str, waktu_ms: int) -> HasilCek:
    if 200 <= status < 300:
        return HasilCek(UptimeHasil.naik, status, waktu_ms)
    # 429 berarti server menjawab dan sedang membatasi dashboard: site hidup.
    if status == 429 or (status in (403, 503) and _halaman_blokir(headers, body)):
        return HasilCek(UptimeHasil.terblokir, status, waktu_ms,
                        "Permintaan dashboard diblokir firewall site")
    return HasilCek(UptimeHasil.gagal, status, waktu_ms, f"HTTP {status}")


def nilai_kesalahan(exc: Exception) -> HasilCek:
    teks = str(exc)
    if isinstance(exc, httpx.TooManyRedirects):
        pesan = "Redirect lebih dari 5 kali"
    elif isinstance(exc, httpx.TimeoutException):
        pesan = "Tidak menjawab dalam 15 detik"
    elif isinstance(exc, httpx.ConnectError):
        rendah = teks.lower()
        if "certificate" in rendah or "ssl" in rendah:
            pesan = f"Sertifikat SSL tidak valid: {teks[:200]}"
        elif any(p in rendah for p in ("name or service not known", "getaddrinfo",
                                        "nodename nor servname", "no address associated")):
            pesan = "Nama domain tidak dapat di-resolve (DNS)"
        else:
            pesan = f"Koneksi gagal: {teks[:200]}"
    else:
        pesan = f"{type(exc).__name__}: {teks[:200]}"
    return HasilCek(UptimeHasil.gagal, None, None, pesan)


def gangguan_dashboard(jumlah_site: int, jumlah_gagal: int) -> bool:
    """Bila hampir semua site gagal bersamaan, yang bermasalah jaringan dashboard."""
    return (
        jumlah_site >= MIN_SITE_ATURAN_GANGGUAN
        and jumlah_gagal / jumlah_site > AMBANG_GANGGUAN
    )


def terapkan(status_lama: UptimeStatus, gagal_beruntun: int, hasil: UptimeHasil) -> Transisi:
    if hasil == UptimeHasil.naik:
        # tutup_insiden selalu True: lapisan database menutup insiden hanya
        # bila memang ada yang terbuka, termasuk insiden yang dibuka sebelum
        # site sempat berstatus terblokir.
        return Transisi(UptimeStatus.naik, 0, False, True)
    if hasil == UptimeHasil.terblokir:
        return Transisi(UptimeStatus.terblokir, gagal_beruntun, False, False)
    baru = gagal_beruntun + 1
    if baru >= GAGAL_UNTUK_MATI:
        return Transisi(UptimeStatus.mati, baru, status_lama != UptimeStatus.mati, False)
    return Transisi(status_lama, baru, False, False)
```

- [ ] **Step 4: Jalankan dan pastikan lulus.** Expected: semua test `test_uptime.py` lulus.

- [ ] **Step 5: Commit.**

```bash
git add src/wpmgr/uptime.py tests/unit/test_uptime.py
git commit -m "feat: aturan penilaian uptime"
```

### Task 9: Putaran uptime dan perintah `check-uptime`

**Files:**
- Create: `src/wpmgr/kunci.py`, `tests/integration/test_uptime_putaran.py`
- Modify: `src/wpmgr/uptime.py`, `src/wpmgr/cli.py`

**Interfaces:**
- Consumes: Task 8.
- Produces:
  - `wpmgr.kunci`: `kunci_advisory(engine, kunci: int)` (context manager yang menghasilkan `bool`), konstanta `KUNCI_UPTIME`, `KUNCI_SSL`, `KUNCI_GA4`, `KUNCI_RETENSI`, `KUNCI_GEOIP`.
  - `wpmgr.uptime`: `buat_klien_http(transport=None) -> httpx.Client`, `cek_satu(client, url) -> HasilCek`, `jalankan_putaran(sesi, cek_fn: Callable[[str], HasilCek], sekarang: datetime | None = None) -> UptimePutaran | None`.
  - CLI `check-uptime`.

Advisory lock memakai **satu koneksi khusus** yang dipegang selama putaran berjalan. Lock level sesi PostgreSQL melekat pada koneksi. Mengambilnya lewat `Session` biasa lalu melepasnya setelah commit bisa terjadi di koneksi pool yang berbeda: `pg_advisory_unlock` gagal diam-diam, dan lock tertahan di koneksi pool sampai koneksi itu ditutup.

- [ ] **Step 1: Tulis test integrasi yang gagal.**

File: `tests/integration/test_uptime_putaran.py`
```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from wpmgr.kunci import KUNCI_UPTIME, kunci_advisory
from wpmgr.models import (
    Site,
    SiteStatus,
    UptimeCheck,
    UptimeHasil,
    UptimeInsiden,
    UptimeStatus,
)
from wpmgr.uptime import HasilCek, jalankan_putaran

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)
NAIK = HasilCek(UptimeHasil.naik, 200, 100)
GAGAL = HasilCek(UptimeHasil.gagal, 500, 90, "HTTP 500")
BLOKIR = HasilCek(UptimeHasil.terblokir, 403, 40, "Permintaan dashboard diblokir firewall site")


def putaran(sesi, hasil, menit):
    return jalankan_putaran(sesi, lambda url: hasil, sekarang=T0 + timedelta(minutes=menit))


def test_dua_kegagalan_membuka_insiden_sejak_kegagalan_pertama(sesi, site):
    putaran(sesi, NAIK, 0)
    putaran(sesi, GAGAL, 5)
    sesi.refresh(site)
    assert site.uptime_status == UptimeStatus.naik
    assert sesi.query(UptimeInsiden).count() == 0

    putaran(sesi, GAGAL, 10)
    sesi.refresh(site)
    assert site.uptime_status == UptimeStatus.mati
    insiden = sesi.query(UptimeInsiden).one()
    assert insiden.mulai == T0 + timedelta(minutes=5)
    assert insiden.selesai is None
    assert insiden.penyebab == "HTTP 500"


def test_pulih_menutup_insiden(sesi, site):
    for menit in (0, 5):
        putaran(sesi, GAGAL, menit)
    putaran(sesi, NAIK, 10)
    sesi.refresh(site)
    assert site.uptime_status == UptimeStatus.naik
    assert site.uptime_gagal_beruntun == 0
    assert sesi.query(UptimeInsiden).one().selesai == T0 + timedelta(minutes=10)


def test_kegagalan_berlanjut_tidak_menambah_insiden(sesi, site):
    for menit in (0, 5, 10, 15):
        putaran(sesi, GAGAL, menit)
    assert sesi.query(UptimeInsiden).count() == 1


def test_terblokir_tanpa_insiden(sesi, site):
    for menit in (0, 5, 10):
        putaran(sesi, BLOKIR, menit)
    sesi.refresh(site)
    assert site.uptime_status == UptimeStatus.terblokir
    assert sesi.query(UptimeInsiden).count() == 0


def test_putaran_gangguan_dashboard_tidak_mengubah_status(sesi, site):
    for i in range(4):
        sesi.add(Site(id=uuid.uuid4(), nama=f"S{i}", url=f"https://s{i}.test",
                      status=SiteStatus.active, secret_terenkripsi=b"x"))
    sesi.commit()
    for menit in (0, 5, 10):
        p = putaran(sesi, GAGAL, menit)
        assert p.gangguan_dashboard is True
    sesi.refresh(site)
    assert site.uptime_status == UptimeStatus.belum_dicek
    assert site.uptime_gagal_beruntun == 0
    assert sesi.query(UptimeInsiden).count() == 0
    # Hasil cek tetap disimpan untuk ditelusuri.
    assert sesi.query(UptimeCheck).count() == 15


def test_site_disabled_tidak_dicek(sesi, site):
    site.status = SiteStatus.disabled
    sesi.commit()
    assert putaran(sesi, NAIK, 0) is None


def test_cek_dijalankan_untuk_setiap_url(sesi, site):
    dilihat = []
    jalankan_putaran(sesi, lambda url: dilihat.append(url) or NAIK, sekarang=T0)
    assert dilihat == [site.url]


def test_kunci_advisory_tidak_bisa_diambil_dua_kali(engine):
    with kunci_advisory(engine, KUNCI_UPTIME) as pertama:
        assert pertama is True
        with kunci_advisory(engine, KUNCI_UPTIME) as kedua:
            assert kedua is False
    with kunci_advisory(engine, KUNCI_UPTIME) as lagi:
        assert lagi is True
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: `ModuleNotFoundError: No module named 'wpmgr.kunci'`.

- [ ] **Step 3: Advisory lock.**

File: `src/wpmgr/kunci.py`
```python
"""Advisory lock PostgreSQL untuk perintah cron yang tidak boleh tumpang-tindih."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

KUNCI_UPTIME = 72_140_001
KUNCI_SSL = 72_140_002
KUNCI_GA4 = 72_140_003
KUNCI_RETENSI = 72_140_004
KUNCI_GEOIP = 72_140_005


@contextmanager
def kunci_advisory(engine, kunci: int) -> Iterator[bool]:
    """Pegang lock selama blok berjalan; menghasilkan False bila sudah dipegang.

    Lock level sesi melekat pada koneksi, jadi diambil dan dilepas pada satu
    koneksi khusus yang dipegang sampai blok selesai. Lewat Session biasa,
    unlock bisa terjadi di koneksi pool yang lain dan gagal diam-diam.
    """
    with engine.connect() as conn:
        dapat = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": kunci}).scalar())
        conn.commit()
        try:
            yield dapat
        finally:
            if dapat:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": kunci})
                conn.commit()
```

- [ ] **Step 4: Putaran uptime.** Tambahkan ke `src/wpmgr/uptime.py`. Perluas impor di puncak berkas menjadi:

```python
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from wpmgr.models import (
    Site,
    SiteStatus,
    UptimeCheck,
    UptimeHasil,
    UptimeInsiden,
    UptimePutaran,
    UptimeStatus,
)
```

lalu tambahkan di akhir berkas:

```python
UA = "WPManager-Uptime/2.0"
TIMEOUT = 15.0
MAKS_REDIRECT = 5
MAKS_PARALEL = 10


def buat_klien_http(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        follow_redirects=True, max_redirects=MAKS_REDIRECT, timeout=TIMEOUT,
        headers={"User-Agent": UA}, transport=transport,
    )


def cek_satu(client: httpx.Client, url: str) -> HasilCek:
    mulai = time.monotonic()
    try:
        r = client.get(url)
    except httpx.HTTPError as exc:
        return nilai_kesalahan(exc)
    return nilai_respons(r.status_code, dict(r.headers), r.text,
                         int((time.monotonic() - mulai) * 1000))


def _awal_deret_gagal(sesi: Session, site_id, sekarang: datetime) -> datetime:
    """Waktu kegagalan pertama dalam deret yang baru mencapai ambang."""
    waktu = sesi.scalars(
        select(UptimeCheck.dicek_pada)
        .join(UptimePutaran, UptimePutaran.id == UptimeCheck.putaran_id)
        .where(
            UptimeCheck.site_id == site_id,
            UptimeCheck.hasil == UptimeHasil.gagal,
            UptimeCheck.dicek_pada < sekarang,
            UptimePutaran.gangguan_dashboard.is_(False),
        )
        .order_by(UptimeCheck.dicek_pada.desc())
        .limit(GAGAL_UNTUK_MATI - 1)
    ).all()
    return waktu[-1] if waktu else sekarang


def _terapkan_ke_site(sesi: Session, site: Site, h: HasilCek, sekarang: datetime) -> None:
    t = terapkan(site.uptime_status, site.uptime_gagal_beruntun, h.hasil)
    terbuka = sesi.scalar(
        select(UptimeInsiden).where(
            UptimeInsiden.site_id == site.id, UptimeInsiden.selesai.is_(None)
        )
    )
    sejak = sekarang
    if t.buka_insiden and terbuka is None:
        sejak = _awal_deret_gagal(sesi, site.id, sekarang)
        sesi.add(UptimeInsiden(
            site_id=site.id, mulai=sejak,
            penyebab=h.pesan or f"HTTP {h.http_status}", http_status=h.http_status,
        ))
    if t.tutup_insiden and terbuka is not None:
        terbuka.selesai = sekarang
    if t.status != site.uptime_status:
        site.uptime_sejak = sejak
    site.uptime_status = t.status
    site.uptime_gagal_beruntun = t.gagal_beruntun


def jalankan_putaran(
    sesi: Session, cek_fn: Callable[[str], HasilCek], sekarang: datetime | None = None
) -> UptimePutaran | None:
    sites = sesi.scalars(
        select(Site).where(Site.status != SiteStatus.disabled).order_by(Site.nama)
    ).all()
    if not sites:
        return None

    with ThreadPoolExecutor(max_workers=MAKS_PARALEL) as ex:
        hasil = list(ex.map(cek_fn, [s.url for s in sites]))

    sekarang = sekarang or datetime.now(timezone.utc)
    jumlah_gagal = sum(1 for h in hasil if h.hasil == UptimeHasil.gagal)
    gangguan = gangguan_dashboard(len(sites), jumlah_gagal)
    putaran = UptimePutaran(mulai=sekarang, jumlah_site=len(sites),
                            jumlah_gagal=jumlah_gagal, gangguan_dashboard=gangguan)
    sesi.add(putaran)
    sesi.flush()

    for site, h in zip(sites, hasil):
        sesi.add(UptimeCheck(
            putaran_id=putaran.id, site_id=site.id, dicek_pada=sekarang, hasil=h.hasil,
            http_status=h.http_status, waktu_ms=h.waktu_ms, pesan=h.pesan,
        ))
        if not gangguan:
            _terapkan_ke_site(sesi, site, h, sekarang)
    sesi.commit()
    return putaran
```

- [ ] **Step 5: Perintah CLI.** Di `src/wpmgr/cli.py`, tambahkan impor `from wpmgr import db`, `from wpmgr.kunci import KUNCI_UPTIME, kunci_advisory`, `from wpmgr.uptime import buat_klien_http, cek_satu, jalankan_putaran`, lalu:

```python
def check_uptime() -> int:
    with kunci_advisory(db.engine, KUNCI_UPTIME) as dapat:
        if not dapat:
            print("Putaran uptime sebelumnya masih berjalan; putaran ini dilewati")
            return 0
        with buat_klien_http() as http, get_session() as sesi:
            putaran = jalankan_putaran(sesi, lambda url: cek_satu(http, url))
    if putaran is None:
        print("Tidak ada site untuk dicek")
        return 0
    catatan = " (gangguan dashboard, status tidak diubah)" if putaran.gangguan_dashboard else ""
    print(f"{putaran.jumlah_site} site dicek, {putaran.jumlah_gagal} gagal{catatan}")
    return putaran.jumlah_site
```

dan daftarkan `sub.add_parser("check-uptime")` beserta cabang `elif args.perintah == "check-uptime": check_uptime()`.

- [ ] **Step 6: Jalankan unit dan integrasi.** Expected: semua lulus.

- [ ] **Step 7: Uji asap perintah terhadap WordPress lokal.** Dengan `docker compose up -d` dan database dev berisi minimal satu site (atau lewati bila database dev kosong), jalankan `.venv/Scripts/python -m wpmgr.cli check-uptime` dengan env Task 1 Step 12. Expected: `N site dicek, M gagal` tanpa traceback. Tempel keluarannya.

- [ ] **Step 8: Commit.**

```bash
git add src/wpmgr/kunci.py src/wpmgr/uptime.py src/wpmgr/cli.py tests/integration/test_uptime_putaran.py
git commit -m "feat: putaran uptime dengan insiden dan aturan gangguan dashboard"
```

### Task 10: Pemeriksaan SSL harian

**Files:**
- Create: `src/wpmgr/ssl_cek.py`, `tests/unit/test_ssl_cek.py`, `tests/integration/test_ssl_semua.py`
- Modify: `src/wpmgr/cli.py`

**Interfaces:**
- Consumes: Task 9 (`kunci_advisory`, `KUNCI_SSL`).
- Produces (di `wpmgr.ssl_cek`):
  - `AMBANG_HARI_SSL = 14`.
  - `baca_kedaluwarsa(host: str, port: int = 443, timeout: float = 15.0) -> datetime`.
  - `cek_semua_ssl(sesi, baca_fn=baca_kedaluwarsa, sekarang: datetime | None = None) -> int`.
  - `sisa_hari_ssl(site, sekarang: datetime) -> int | None`, `ssl_bermasalah(site, sekarang: datetime) -> bool`.
  - CLI `check-ssl`.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/unit/test_ssl_cek.py`
```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from wpmgr.ssl_cek import sisa_hari_ssl, ssl_bermasalah

SEKARANG = datetime(2026, 9, 22, tzinfo=timezone.utc)


def site(kedaluwarsa=None, error=None):
    return SimpleNamespace(ssl_kedaluwarsa=kedaluwarsa, ssl_error=error)


def test_sisa_hari():
    assert sisa_hari_ssl(site(SEKARANG + timedelta(days=20, hours=3)), SEKARANG) == 20
    assert sisa_hari_ssl(site(), SEKARANG) is None


def test_bermasalah_bila_kurang_dari_14_hari():
    assert ssl_bermasalah(site(SEKARANG + timedelta(days=13)), SEKARANG)
    assert not ssl_bermasalah(site(SEKARANG + timedelta(days=14, hours=1)), SEKARANG)


def test_bermasalah_bila_sudah_lewat_atau_error():
    assert ssl_bermasalah(site(SEKARANG - timedelta(days=1)), SEKARANG)
    assert ssl_bermasalah(site(error="Sertifikat tidak valid"), SEKARANG)


def test_belum_pernah_dicek_tidak_bermasalah():
    assert not ssl_bermasalah(site(), SEKARANG)
```

File: `tests/integration/test_ssl_semua.py`
```python
import ssl
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from wpmgr.models import Site, SiteStatus
from wpmgr.ssl_cek import cek_semua_ssl

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 22, tzinfo=timezone.utc)


def test_kedaluwarsa_disimpan(sesi, site):
    dilihat = []

    def baca(host, port):
        dilihat.append((host, port))
        return SEKARANG + timedelta(days=60)

    assert cek_semua_ssl(sesi, baca, SEKARANG) == 1
    sesi.refresh(site)
    assert site.ssl_kedaluwarsa == SEKARANG + timedelta(days=60)
    assert site.ssl_error is None
    assert site.ssl_dicek_pada == SEKARANG
    assert dilihat == [(site.url.removeprefix("https://"), 443)]


def test_sertifikat_tidak_valid_dicatat(sesi, site):
    def baca(host, port):
        raise ssl.SSLCertVerificationError("certificate verify failed: certificate has expired")

    cek_semua_ssl(sesi, baca, SEKARANG)
    sesi.refresh(site)
    assert site.ssl_error.startswith("Sertifikat tidak valid")


def test_koneksi_gagal_dicatat(sesi, site):
    def baca(host, port):
        raise OSError("Connection refused")

    cek_semua_ssl(sesi, baca, SEKARANG)
    sesi.refresh(site)
    assert site.ssl_error.startswith("Tidak dapat membuka koneksi TLS")


def test_site_http_dan_disabled_dilewati(sesi, site):
    site.url = "http://lokal.test"
    sesi.add(Site(id=uuid.uuid4(), nama="Mati", url="https://mati.test",
                  status=SiteStatus.disabled, secret_terenkripsi=b"x"))
    sesi.commit()
    assert cek_semua_ssl(sesi, lambda h, p: SEKARANG, SEKARANG) == 0


def test_port_non_standar(sesi, site):
    site.url = "https://contoh.test:8443"
    sesi.commit()
    dilihat = []
    cek_semua_ssl(sesi, lambda h, p: dilihat.append((h, p)) or SEKARANG, SEKARANG)
    assert dilihat == [("contoh.test", 8443)]
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: `ModuleNotFoundError: No module named 'wpmgr.ssl_cek'`.

- [ ] **Step 3: Implementasikan.**

File: `src/wpmgr/ssl_cek.py`
```python
import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from wpmgr.models import Site, SiteStatus

AMBANG_HARI_SSL = 14


def baca_kedaluwarsa(host: str, port: int = 443, timeout: float = 15.0) -> datetime:
    konteks = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with konteks.wrap_socket(sock, server_hostname=host) as tls:
            sertifikat = tls.getpeercert()
    detik = ssl.cert_time_to_seconds(sertifikat["notAfter"])
    return datetime.fromtimestamp(detik, tz=timezone.utc)


def cek_semua_ssl(sesi: Session, baca_fn=baca_kedaluwarsa, sekarang: datetime | None = None) -> int:
    sekarang = sekarang or datetime.now(timezone.utc)
    dicek = 0
    for site in sesi.scalars(select(Site).where(Site.status != SiteStatus.disabled)).all():
        bagian = urlsplit(site.url)
        if bagian.scheme != "https" or not bagian.hostname:
            continue
        try:
            site.ssl_kedaluwarsa = baca_fn(bagian.hostname, bagian.port or 443)
            site.ssl_error = None
        except ssl.SSLCertVerificationError as exc:
            alasan = getattr(exc, "verify_message", None) or str(exc)
            site.ssl_error = f"Sertifikat tidak valid: {alasan}"[:500]
        except (OSError, ssl.SSLError) as exc:
            site.ssl_error = f"Tidak dapat membuka koneksi TLS: {exc}"[:500]
        site.ssl_dicek_pada = sekarang
        dicek += 1
    sesi.commit()
    return dicek


def sisa_hari_ssl(site, sekarang: datetime) -> int | None:
    if site.ssl_kedaluwarsa is None:
        return None
    return (site.ssl_kedaluwarsa - sekarang).days


def ssl_bermasalah(site, sekarang: datetime) -> bool:
    if site.ssl_error:
        return True
    sisa = sisa_hari_ssl(site, sekarang)
    return sisa is not None and sisa < AMBANG_HARI_SSL
```

Catatan: `ssl.SSLCertVerificationError` adalah subkelas `ssl.SSLError`, yang merupakan subkelas `OSError`. Cabang sertifikat karenanya wajib berada **sebelum** cabang `OSError`.

- [ ] **Step 4: Perintah CLI.** Tambahkan ke `cli.py` (impor `KUNCI_SSL`, `cek_semua_ssl`):

```python
def check_ssl() -> int:
    with kunci_advisory(db.engine, KUNCI_SSL) as dapat:
        if not dapat:
            print("Pemeriksaan SSL lain masih berjalan; dilewati")
            return 0
        with get_session() as sesi:
            n = cek_semua_ssl(sesi)
    print(f"{n} sertifikat dicek")
    return n
```

beserta `sub.add_parser("check-ssl")` dan cabangnya.

- [ ] **Step 5: Jalankan unit dan integrasi.** Expected: semua lulus.

- [ ] **Step 6: Uji manual terhadap host sungguhan.** Run:

```bash
.venv/Scripts/python -c "from wpmgr.ssl_cek import baca_kedaluwarsa; print(baca_kedaluwarsa('wordpress.org'))"
```

Expected: tanggal kedaluwarsa di masa depan. Tempel keluarannya. Bila mesin tanpa internet, catat itu di laporan.

- [ ] **Step 7: Commit.**

```bash
git add src/wpmgr/ssl_cek.py src/wpmgr/cli.py tests/unit/test_ssl_cek.py tests/integration/test_ssl_semua.py
git commit -m "feat: pemeriksaan masa berlaku SSL harian"
```

---

## Fase D — Penangkapan di connector

### Task 11: Penangkap error PHP dan pemasangnya di mu-plugins

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-penangkap.php`, `connector/tests/PenangkapTest.php`
- Modify: `connector/wp-manager-connector/wp-manager-connector.php` (`WPMGR_VERSI_SKEMA` → 2, require dan pasang), `includes/class-wpmgr-skema.php` (`mode_penangkap`, `isi_mu_plugin`, `tulis_mu_plugin`, `migrasi`), `connector/tests/SkemaTest.php`, `connector/tests/bootstrap.php`

**Interfaces:**
- Consumes: Task 2 (`WPMGR_Skema`, tabel `wpmgr_errors`).
- Produces:
  - `WPMGR_Penangkap::pasang()`, `tangani_error( $errno, $errstr, $errfile = '', $errline = 0 )`, `dari_template_error( $args, $error )`, `saat_shutdown()`.
  - Fungsi murni: `normalisasi_pesan( $pesan, $abspath ): string`, `file_relatif( $file, $abspath ): string`, `atribusi( $file, $content_dir, $abspath ): array( tipe, slug|null )`, `sidik_jari( $tingkat, $file_relatif, $baris, $pesan_norm ): string`, `susun( $tingkat, $pesan, $file, $baris, $abspath, $content_dir ): array`, `tambah( array &$buffer, array $kejadian, int $batas_baru )`.
  - Kait test: `reset_untuk_test( $sebelumnya = null )`, `buffer_untuk_test(): array`.
  - `WPMGR_Skema::mode_penangkap()` → `'penuh' | 'terbatas' | null`; `WPMGR_Skema::isi_mu_plugin(): string`; `WPMGR_Skema::tulis_mu_plugin(): bool`.

**Baca dulu koreksi #1 di bagian atas rencana.** Fatal error ditangkap lewat filter `wp_php_error_args`, bukan hanya lewat fungsi shutdown. Sebelum menulis kode, verifikasi di core (`D:\laragon\www\pacexports-wp`): `wp-settings.php:69` (`wp_register_fatal_error_handler()`), `wp-includes/class-wp-fatal-error-handler.php` baris 31–75 dan 174–245 (`apply_filters( 'wp_php_error_args', ... )` lalu `wp_die()`), dan `wp-includes/functions.php` sekitar baris 4092 (`die()`). Tempel nomor barisnya di laporan.

- [ ] **Step 1: Tulis test PHP yang gagal.**

File: `connector/tests/PenangkapTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class PenangkapTest extends TestCase {

    const AKAR    = '/var/www/html/';
    const KONTEN  = '/var/www/html/wp-content';

    protected function tearDown(): void {
        WPMGR_Penangkap::reset_untuk_test();
    }

    public function test_angka_dinormalkan_sehingga_error_memori_satu_sidik(): void {
        $a = WPMGR_Penangkap::normalisasi_pesan( 'Allowed memory size of 268435456 bytes exhausted (tried to allocate 20480 bytes)', self::AKAR );
        $b = WPMGR_Penangkap::normalisasi_pesan( 'Allowed memory size of 268435456 bytes exhausted (tried to allocate 40960 bytes)', self::AKAR );
        $this->assertSame( $a, $b );
    }

    public function test_hanya_baris_pertama_dan_path_relatif_yang_dipakai(): void {
        $pesan = "Uncaught Error: Call to undefined function x() in /var/www/html/wp-content/plugins/a/a.php:12\nStack trace:\n#0 {main}";
        $this->assertSame(
            'Uncaught Error: Call to undefined function x() in wp-content/plugins/a/a.php:N',
            WPMGR_Penangkap::normalisasi_pesan( $pesan, self::AKAR )
        );
    }

    public function test_file_relatif_juga_untuk_path_windows(): void {
        $this->assertSame( 'wp-content/plugins/a/a.php',
            WPMGR_Penangkap::file_relatif( 'C:\\laragon\\www\\situs\\wp-content\\plugins\\a\\a.php', 'C:\\laragon\\www\\situs\\' ) );
        $this->assertSame( '/tmp/x.php', WPMGR_Penangkap::file_relatif( '/tmp/x.php', self::AKAR ) );
    }

    /** @dataProvider kasus_atribusi */
    public function test_atribusi( $file, $harapan ): void {
        $this->assertSame( $harapan, WPMGR_Penangkap::atribusi( $file, self::KONTEN, self::AKAR ) );
    }

    public function kasus_atribusi(): array {
        return array(
            array( '/var/www/html/wp-content/plugins/elementor/core/base.php', array( 'plugin', 'elementor' ) ),
            array( '/var/www/html/wp-content/plugins/hello.php', array( 'plugin', 'hello.php' ) ),
            array( '/var/www/html/wp-content/mu-plugins/x.php', array( 'mu-plugin', 'x.php' ) ),
            array( '/var/www/html/wp-content/themes/astra/functions.php', array( 'theme', 'astra' ) ),
            array( '/var/www/html/wp-includes/class-wpdb.php', array( 'core', null ) ),
            array( '/var/www/html/wp-admin/includes/file.php', array( 'core', null ) ),
            array( '/tmp/lain.php', array( 'lainnya', null ) ),
            array( '', array( 'lainnya', null ) ),
        );
    }

    public function test_sidik_jari_stabil_dan_membedakan_tingkat_dan_baris(): void {
        $a = WPMGR_Penangkap::sidik_jari( 'warning', 'a.php', 10, 'pesan' );
        $this->assertSame( $a, WPMGR_Penangkap::sidik_jari( 'warning', 'a.php', 10, 'pesan' ) );
        $this->assertNotSame( $a, WPMGR_Penangkap::sidik_jari( 'fatal', 'a.php', 10, 'pesan' ) );
        $this->assertNotSame( $a, WPMGR_Penangkap::sidik_jari( 'warning', 'a.php', 11, 'pesan' ) );
        $this->assertSame( 32, strlen( $a ) );
    }

    public function test_buffer_menggabung_duplikat_dan_membatasi_sidik_baru(): void {
        $buffer = array();
        for ( $i = 0; $i < 25; $i++ ) {
            WPMGR_Penangkap::tambah( $buffer, WPMGR_Penangkap::susun( 'warning', "pesan $i", '/tmp/x.php', $i, self::AKAR, self::KONTEN ), 20 );
        }
        $this->assertCount( 20, $buffer );
        $pertama = WPMGR_Penangkap::susun( 'warning', 'pesan 0', '/tmp/x.php', 0, self::AKAR, self::KONTEN );
        WPMGR_Penangkap::tambah( $buffer, $pertama, 20 );
        $this->assertSame( 2, $buffer[ $pertama['sidik_jari'] ]['jumlah'] );
    }

    public function test_handler_sebelumnya_tetap_dipanggil_dan_nilainya_diteruskan(): void {
        $dipanggil = array();
        set_error_handler( function ( $no, $str ) use ( &$dipanggil ) {
            $dipanggil[] = array( $no, $str );
            return true;
        } );
        WPMGR_Penangkap::reset_untuk_test();
        WPMGR_Penangkap::pasang();
        try {
            trigger_error( 'warning uji', E_USER_WARNING );
            $this->assertSame( array( array( E_USER_WARNING, 'warning uji' ) ), $dipanggil );
            $this->assertCount( 1, WPMGR_Penangkap::buffer_untuk_test() );
            $this->assertTrue( WPMGR_Penangkap::tangani_error( E_USER_WARNING, 'langsung', __FILE__, __LINE__ ) );
        } finally {
            restore_error_handler();
            restore_error_handler();
        }
    }

    public function test_tanpa_handler_sebelumnya_mengembalikan_false(): void {
        WPMGR_Penangkap::reset_untuk_test( null );
        $this->assertFalse( WPMGR_Penangkap::tangani_error( E_USER_WARNING, 'x', __FILE__, __LINE__ ) );
    }

    public function test_notice_dan_warning_yang_dibungkam_tidak_dicatat(): void {
        set_error_handler( function () { return true; } );
        WPMGR_Penangkap::reset_untuk_test();
        WPMGR_Penangkap::pasang();
        try {
            trigger_error( 'notice', E_USER_NOTICE );
            @trigger_error( 'dibungkam', E_USER_WARNING );
            $this->assertCount( 0, WPMGR_Penangkap::buffer_untuk_test() );
        } finally {
            restore_error_handler();
            restore_error_handler();
        }
    }

    public function test_filter_template_mencatat_fatal_dan_mengembalikan_args_utuh(): void {
        WPMGR_Penangkap::reset_untuk_test( null );
        $args  = array( 'response' => 500 );
        $error = array( 'type' => E_ERROR, 'message' => 'fatal uji', 'file' => '/tmp/x.php', 'line' => 3 );
        $this->assertSame( $args, WPMGR_Penangkap::dari_template_error( $args, $error ) );
        $buffer = WPMGR_Penangkap::buffer_untuk_test();
        $this->assertCount( 1, $buffer );
        $this->assertSame( 'fatal', array_values( $buffer )[0]['tingkat'] );
    }

    public function test_error_non_fatal_dari_error_get_last_diabaikan_oleh_catat_fatal(): void {
        WPMGR_Penangkap::reset_untuk_test( null );
        WPMGR_Penangkap::catat_fatal( array( 'type' => E_WARNING, 'message' => 'w', 'file' => '', 'line' => 0 ) );
        WPMGR_Penangkap::catat_fatal( null );
        $this->assertCount( 0, WPMGR_Penangkap::buffer_untuk_test() );
    }
}
```

Tambahkan ke `SkemaTest.php`:

```php
    public function test_isi_mu_plugin_punya_semua_penjaga(): void {
        $isi = WPMGR_Skema::isi_mu_plugin();
        $this->assertStringStartsWith( '<?php', $isi );
        $this->assertStringContainsString( 'WPMGR_DISABLE_MONITORING', $isi );
        $this->assertStringContainsString( "'active_plugins'", $isi );
        $this->assertStringContainsString( "'wp-manager-connector/wp-manager-connector.php'", $isi );
        $this->assertStringContainsString( 'is_readable', $isi );
        $this->assertStringContainsString( 'WPMGR_Penangkap::pasang()', $isi );
    }
```

Tambahkan `require_once` untuk `class-wpmgr-penangkap.php` ke `bootstrap.php`.

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: fatal `Failed opening required ... class-wpmgr-penangkap.php`.

- [ ] **Step 3: Implementasikan penangkap.**

File: `connector/wp-manager-connector/includes/class-wpmgr-penangkap.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Menangkap fatal error, warning, dan error database, lalu menyimpannya per
 * sidik jari di tabel wpmgr_errors.
 *
 * Prinsipnya: penangkap tidak boleh merusak site. Selama request ia hanya
 * menampung di memori; penulisan terjadi sekali di akhir request. Semua jalur
 * dibungkus try/catch, dan error handler sebelumnya selalu dipanggil sehingga
 * perilaku PHP di site tidak berubah.
 */
class WPMGR_Penangkap {

    const BATAS_BARU_PER_REQUEST = 20;
    const BATAS_BARIS            = 500;
    const TINGKAT_WARNING        = array( E_WARNING, E_USER_WARNING, E_CORE_WARNING, E_COMPILE_WARNING );
    const TINGKAT_FATAL          = array( E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR, E_USER_ERROR, E_RECOVERABLE_ERROR );

    private static $buffer        = array();
    private static $sebelumnya    = null;
    private static $terpasang     = false;
    private static $sudah_ditulis = false;

    public static function pasang() {
        if ( self::$terpasang ) {
            return;
        }
        self::$terpasang = true;
        try {
            self::$sebelumnya = set_error_handler( array( __CLASS__, 'tangani_error' ) );
            register_shutdown_function( array( __CLASS__, 'saat_shutdown' ) );
            // Handler fatal WordPress dipasang sebelum mu-plugin mana pun
            // (wp-settings.php) dan berakhir di wp_die() -> die(). Exit di
            // dalam fungsi shutdown menghentikan fungsi shutdown berikutnya,
            // termasuk milik kita; filter ini dipanggil tepat sebelum wp_die().
            if ( function_exists( 'add_filter' ) ) {
                add_filter( 'wp_php_error_args', array( __CLASS__, 'dari_template_error' ), 10, 2 );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function tangani_error( $errno, $errstr, $errfile = '', $errline = 0 ) {
        try {
            // error_reporting() tidak memuat $errno untuk ekspresi yang
            // dibungkam dengan @ -- core sendiri memakainya di banyak tempat.
            if ( in_array( $errno, self::TINGKAT_WARNING, true ) && ( error_reporting() & $errno ) ) {
                self::catat( 'warning', (string) $errstr, (string) $errfile, (int) $errline );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
        if ( null !== self::$sebelumnya ) {
            return call_user_func( self::$sebelumnya, $errno, $errstr, $errfile, $errline );
        }
        return false;
    }

    public static function dari_template_error( $args, $error ) {
        try {
            self::catat_fatal( $error );
            self::tulis();
        } catch ( \Throwable $e ) {
            unset( $e );
        }
        return $args;
    }

    public static function saat_shutdown() {
        try {
            self::catat_fatal( error_get_last() );
            self::catat_database();
            self::tulis();
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function catat_fatal( $error ) {
        if ( is_array( $error ) && isset( $error['type'] ) && in_array( (int) $error['type'], self::TINGKAT_FATAL, true ) ) {
            self::catat( 'fatal',
                isset( $error['message'] ) ? (string) $error['message'] : '',
                isset( $error['file'] ) ? (string) $error['file'] : '',
                isset( $error['line'] ) ? (int) $error['line'] : 0 );
        }
    }

    private static function catat_database() {
        if ( isset( $GLOBALS['wpdb'] ) && is_object( $GLOBALS['wpdb'] ) && ! empty( $GLOBALS['wpdb']->last_error ) ) {
            self::catat( 'database', (string) $GLOBALS['wpdb']->last_error, '', 0 );
        }
    }

    private static function catat( $tingkat, $pesan, $file, $baris ) {
        $abspath = defined( 'ABSPATH' ) ? ABSPATH : '';
        $konten  = defined( 'WP_CONTENT_DIR' ) ? WP_CONTENT_DIR : $abspath . 'wp-content';
        self::tambah( self::$buffer, self::susun( $tingkat, $pesan, $file, $baris, $abspath, $konten ),
            self::BATAS_BARU_PER_REQUEST );
    }

    // ---- fungsi murni ---------------------------------------------------

    public static function potong( $teks, $panjang ) {
        return function_exists( 'mb_substr' ) ? mb_substr( $teks, 0, $panjang ) : substr( $teks, 0, $panjang );
    }

    private static function garis_miring( $path ) {
        return str_replace( '\\', '/', (string) $path );
    }

    public static function file_relatif( $file, $abspath ) {
        $f = self::garis_miring( $file );
        $a = rtrim( self::garis_miring( $abspath ), '/' ) . '/';
        if ( '/' !== $a && 0 === strpos( $f, $a ) ) {
            return substr( $f, strlen( $a ) );
        }
        return $f;
    }

    public static function normalisasi_pesan( $pesan, $abspath ) {
        $baris_pertama = strtok( (string) $pesan, "\n" );
        $baris_pertama = false === $baris_pertama ? '' : $baris_pertama;
        $baris_pertama = self::garis_miring( $baris_pertama );
        $a             = rtrim( self::garis_miring( $abspath ), '/' ) . '/';
        if ( '/' !== $a ) {
            $baris_pertama = str_replace( $a, '', $baris_pertama );
        }
        return preg_replace( '/\d+/', 'N', $baris_pertama );
    }

    public static function atribusi( $file, $content_dir, $abspath ) {
        $f = self::garis_miring( $file );
        if ( '' === $f ) {
            return array( 'lainnya', null );
        }
        $c = rtrim( self::garis_miring( $content_dir ), '/' ) . '/';
        $a = rtrim( self::garis_miring( $abspath ), '/' ) . '/';
        if ( 0 === strpos( $f, $c ) ) {
            $bagian = explode( '/', substr( $f, strlen( $c ) ) );
            if ( 'plugins' === $bagian[0] && isset( $bagian[1] ) ) {
                return array( 'plugin', $bagian[1] );
            }
            if ( 'mu-plugins' === $bagian[0] && isset( $bagian[1] ) ) {
                return array( 'mu-plugin', $bagian[1] );
            }
            if ( 'themes' === $bagian[0] && isset( $bagian[1] ) ) {
                return array( 'theme', $bagian[1] );
            }
            return array( 'lainnya', null );
        }
        if ( 0 === strpos( $f, $a . 'wp-includes/' ) || 0 === strpos( $f, $a . 'wp-admin/' ) ) {
            return array( 'core', null );
        }
        return array( 'lainnya', null );
    }

    public static function sidik_jari( $tingkat, $file_relatif, $baris, $pesan_norm ) {
        return md5( $tingkat . '|' . $file_relatif . '|' . (int) $baris . '|' . $pesan_norm );
    }

    public static function susun( $tingkat, $pesan, $file, $baris, $abspath, $content_dir ) {
        $pesan   = self::potong( (string) $pesan, 1000 );
        $relatif = self::file_relatif( $file, $abspath );
        list( $tipe, $slug ) = self::atribusi( $file, $content_dir, $abspath );
        return array(
            'sidik_jari'    => self::sidik_jari( $tingkat, $relatif, $baris, self::normalisasi_pesan( $pesan, $abspath ) ),
            'tingkat'       => $tingkat,
            'komponen_tipe' => $tipe,
            'komponen_slug' => $slug,
            'pesan'         => $pesan,
            'file'          => self::potong( $relatif, 255 ),
            'baris'         => (int) $baris,
            'jumlah'        => 1,
        );
    }

    public static function tambah( array &$buffer, array $kejadian, $batas_baru ) {
        $s = $kejadian['sidik_jari'];
        if ( isset( $buffer[ $s ] ) ) {
            $buffer[ $s ]['jumlah']++;
            return;
        }
        if ( count( $buffer ) >= $batas_baru ) {
            return;
        }
        $buffer[ $s ] = $kejadian;
    }

    // ---- penulisan ------------------------------------------------------

    private static function konteks() {
        $jenis = 'depan';
        if ( defined( 'WP_CLI' ) && WP_CLI ) {
            $jenis = 'cli';
        } elseif ( function_exists( 'wp_doing_cron' ) && wp_doing_cron() ) {
            $jenis = 'cron';
        } elseif ( function_exists( 'wp_doing_ajax' ) && wp_doing_ajax() ) {
            $jenis = 'ajax';
        } elseif ( defined( 'REST_REQUEST' ) && REST_REQUEST ) {
            $jenis = 'rest';
        } elseif ( function_exists( 'is_admin' ) && is_admin() ) {
            $jenis = 'admin';
        }
        $uri  = isset( $_SERVER['REQUEST_URI'] ) ? (string) $_SERVER['REQUEST_URI'] : '';
        // Tanpa query string: query bisa memuat token (reset password, SSO).
        $path = (string) parse_url( $uri, PHP_URL_PATH );
        return array( 'path' => self::potong( $path, 191 ), 'jenis' => $jenis );
    }

    private static function tulis() {
        if ( self::$sudah_ditulis || empty( self::$buffer ) || ! isset( $GLOBALS['wpdb'] ) ) {
            return;
        }
        if ( defined( 'WPMGR_DISABLE_MONITORING' ) && WPMGR_DISABLE_MONITORING ) {
            return;
        }
        self::$sudah_ditulis = true;

        $wpdb     = $GLOBALS['wpdb'];
        $tabel    = $wpdb->prefix . 'wpmgr_errors';
        $sekarang = time();
        $konteks  = json_encode( self::konteks() );
        $lama     = $wpdb->suppress_errors( true );
        try {
            $penuh = (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$tabel}" ) >= self::BATAS_BARIS; // phpcs:ignore WordPress.DB.PreparedSQL
            foreach ( self::$buffer as $sidik => $k ) {
                if ( $penuh ) {
                    // Setelah batas: hanya error yang sudah dikenal yang
                    // diperbarui; sidik baru menunggu pemangkasan harian.
                    $wpdb->query( $wpdb->prepare(
                        "UPDATE {$tabel} SET jumlah = jumlah + %d, terakhir = %d, diubah = %d WHERE sidik_jari = %s",
                        $k['jumlah'], $sekarang, $sekarang, $sidik
                    ) );
                    continue;
                }
                $wpdb->query( $wpdb->prepare(
                    "INSERT INTO {$tabel} (sidik_jari, tingkat, komponen_tipe, komponen_slug, pesan, file, baris, konteks, jumlah, pertama, terakhir, diubah)
                     VALUES (%s, %s, %s, %s, %s, %s, %d, %s, %d, %d, %d, %d)
                     ON DUPLICATE KEY UPDATE jumlah = jumlah + VALUES(jumlah), terakhir = VALUES(terakhir), diubah = VALUES(diubah), konteks = VALUES(konteks)",
                    $sidik, $k['tingkat'], $k['komponen_tipe'], (string) $k['komponen_slug'], $k['pesan'],
                    $k['file'], $k['baris'], $konteks, $k['jumlah'], $sekarang, $sekarang, $sekarang
                ) );
            }
        } finally {
            $wpdb->suppress_errors( $lama );
        }
    }

    // ---- kait test ------------------------------------------------------

    public static function reset_untuk_test( $sebelumnya = null ) {
        self::$buffer        = array();
        self::$sebelumnya    = $sebelumnya;
        self::$terpasang     = false;
        self::$sudah_ditulis = false;
    }

    public static function buffer_untuk_test() {
        return self::$buffer;
    }
}
```

Catatan: `test_handler_sebelumnya_...` memanggil `reset_untuk_test()` lalu `pasang()`; `pasang()` mengisi `$sebelumnya` dari `set_error_handler()`. Karena `$terpasang` di-reset, `pasang()` di test mendaftarkan fungsi shutdown lagi. Itu tidak berbahaya: `tulis()` keluar tanpa `$wpdb`.

- [ ] **Step 4: Pemasang mu-plugin di kelas skema.** Di `class-wpmgr-skema.php`, ganti `mode_penangkap()` dan tambahkan dua metode:

```php
    public static function mode_penangkap() {
        if ( self::monitoring_mati() ) {
            return null;
        }
        return ( defined( 'WPMU_PLUGIN_DIR' ) && file_exists( WPMU_PLUGIN_DIR . '/' . self::MU_PLUGIN ) )
            ? 'penuh' : 'terbatas';
    }

    /**
     * Isi mu-plugin pemuat. Tetap diam bila connector dinonaktifkan atau
     * dihapus: menghentikan connector berarti menghentikan pemantauan.
     */
    public static function isi_mu_plugin() {
        return <<<'PHP'
<?php
/**
 * Plugin Name: WP Manager — penangkap error
 * Description: Dipasang otomatis oleh WP Manager Connector supaya fatal error dari plugin lain ikut tertangkap. Aman dihapus; connector akan memasangnya lagi saat pembaruan skema berikutnya.
 */
if ( defined( 'WPMGR_DISABLE_MONITORING' ) && WPMGR_DISABLE_MONITORING ) {
    return;
}
if ( ! in_array( 'wp-manager-connector/wp-manager-connector.php', (array) get_option( 'active_plugins', array() ), true ) ) {
    return;
}
$wpmgr_penangkap = WP_PLUGIN_DIR . '/wp-manager-connector/includes/class-wpmgr-penangkap.php';
if ( ! is_readable( $wpmgr_penangkap ) ) {
    return;
}
require_once $wpmgr_penangkap;
WPMGR_Penangkap::pasang();
PHP;
    }

    public static function tulis_mu_plugin() {
        if ( ! defined( 'WPMU_PLUGIN_DIR' ) || self::monitoring_mati() ) {
            return false;
        }
        if ( ! is_dir( WPMU_PLUGIN_DIR ) && ! wp_mkdir_p( WPMU_PLUGIN_DIR ) ) {
            return false;
        }
        // Gagal menulis (hosting mengunci mu-plugins) bukan kesalahan: connector
        // memasang penangkap dari dirinya sendiri dan melaporkan mode 'terbatas'.
        return false !== @file_put_contents( WPMU_PLUGIN_DIR . '/' . self::MU_PLUGIN, self::isi_mu_plugin() ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
    }
```

Di `migrasi()`, panggil `self::tulis_mu_plugin();` setelah `dbDelta(...)`.

- [ ] **Step 5: Berkas utama plugin.** Naikkan `define( 'WPMGR_VERSI_SKEMA', 2 );`. Tambahkan `require_once WPMGR_DIR . 'includes/class-wpmgr-penangkap.php';` setelah require `class-wpmgr-skema.php`, lalu tambahkan blok berikut di **akhir** berkas, setelah semua `require_once` dan `add_action`. Task 13 dan 18 menambahkan pemasang lain ke blok yang sama, dan kelas mereka harus sudah di-require saat blok ini berjalan:

```php
// Pemasang pemantauan. Bila mu-plugin sudah memasang penangkap, panggilan
// pertama tidak melakukan apa-apa; bila belum (mu-plugins terkunci), inilah
// pemasangan paling awal yang bisa dilakukan dari plugin biasa.
if ( ! WPMGR_Skema::monitoring_mati() ) {
    WPMGR_Penangkap::pasang();
}
```

- [ ] **Step 6: PHPUnit dan lint PHP 7.4.** Expected: semua lulus.

- [ ] **Step 7: Commit.**

```bash
git add connector
git commit -m "feat(connector): penangkap fatal error, warning, dan error database"
```

### Task 12: Penentuan IP klien di connector

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-ip.php`, `connector/wp-manager-connector/includes/cloudflare-ip.php`, `connector/tests/IpTest.php`
- Modify: `connector/wp-manager-connector/wp-manager-connector.php`, `connector/tests/bootstrap.php`

**Interfaces:**
- Produces:
  - `WPMGR_IP::tentukan( array $server, bool $percayai_xff, array $rentang_cf ): array( 'ip' => ?string, 'lewat_cloudflare' => bool )`.
  - `WPMGR_IP::dalam_rentang( string $ip, string $cidr ): bool`, `WPMGR_IP::valid( $ip ): bool`, `WPMGR_IP::publik( $ip ): bool`.
  - `WPMGR_IP::saat_ini(): array` (membaca `$_SERVER`, setelan plugin, dan daftar Cloudflare).
  - `includes/cloudflare-ip.php` mengembalikan array CIDR.

- [ ] **Step 1: Tulis test PHP yang gagal.**

File: `connector/tests/IpTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class IpTest extends TestCase {

    private $cf = array( '173.245.48.0/20', '104.16.0.0/13', '2400:cb00::/32' );

    public function test_default_remote_addr(): void {
        $this->assertSame( array( 'ip' => '203.0.113.9', 'lewat_cloudflare' => false ),
            WPMGR_IP::tentukan( array( 'REMOTE_ADDR' => '203.0.113.9' ), false, $this->cf ) );
    }

    public function test_cf_connecting_ip_dipercaya_hanya_dari_rentang_cloudflare(): void {
        $dari_cf = array( 'REMOTE_ADDR' => '104.16.1.2', 'HTTP_CF_CONNECTING_IP' => '198.51.100.7' );
        $this->assertSame( array( 'ip' => '198.51.100.7', 'lewat_cloudflare' => true ),
            WPMGR_IP::tentukan( $dari_cf, false, $this->cf ) );

        $palsu = array( 'REMOTE_ADDR' => '203.0.113.9', 'HTTP_CF_CONNECTING_IP' => '1.1.1.1' );
        $this->assertSame( array( 'ip' => '203.0.113.9', 'lewat_cloudflare' => false ),
            WPMGR_IP::tentukan( $palsu, false, $this->cf ) );
    }

    public function test_cloudflare_ipv6(): void {
        $s = array( 'REMOTE_ADDR' => '2400:cb00:2049:1::a29f:1804', 'HTTP_CF_CONNECTING_IP' => '2001:db8::1' );
        $this->assertSame( '2001:db8::1', WPMGR_IP::tentukan( $s, false, $this->cf )['ip'] );
    }

    public function test_cf_connecting_ip_tidak_valid_diabaikan(): void {
        $s = array( 'REMOTE_ADDR' => '104.16.1.2', 'HTTP_CF_CONNECTING_IP' => '<script>' );
        $this->assertSame( array( 'ip' => '104.16.1.2', 'lewat_cloudflare' => false ),
            WPMGR_IP::tentukan( $s, false, $this->cf ) );
    }

    public function test_xff_hanya_bila_setelan_aktif_dan_paling_kanan_yang_publik(): void {
        $s = array( 'REMOTE_ADDR' => '10.0.0.5', 'HTTP_X_FORWARDED_FOR' => '1.2.3.4, 198.51.100.20, 10.0.0.9' );
        $this->assertSame( '10.0.0.5', WPMGR_IP::tentukan( $s, false, $this->cf )['ip'] );
        $this->assertSame( '198.51.100.20', WPMGR_IP::tentukan( $s, true, $this->cf )['ip'] );
    }

    public function test_remote_addr_tidak_valid_menghasilkan_null(): void {
        $this->assertNull( WPMGR_IP::tentukan( array(), false, $this->cf )['ip'] );
        $this->assertNull( WPMGR_IP::tentukan( array( 'REMOTE_ADDR' => 'bukan-ip' ), false, $this->cf )['ip'] );
    }

    public function test_dalam_rentang(): void {
        $this->assertTrue( WPMGR_IP::dalam_rentang( '173.245.63.255', '173.245.48.0/20' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( '173.245.64.0', '173.245.48.0/20' ) );
        $this->assertTrue( WPMGR_IP::dalam_rentang( '104.23.255.255', '104.16.0.0/13' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( '2400:cb00::1', '104.16.0.0/13' ) );
        $this->assertFalse( WPMGR_IP::dalam_rentang( 'x', '104.16.0.0/13' ) );
    }

    public function test_daftar_cloudflare_bawaan_berisi_cidr_sah(): void {
        $daftar = require __DIR__ . '/../wp-manager-connector/includes/cloudflare-ip.php';
        $this->assertGreaterThan( 10, count( $daftar ) );
        foreach ( $daftar as $cidr ) {
            list( $net, $bit ) = explode( '/', $cidr );
            $this->assertTrue( WPMGR_IP::valid( $net ), $cidr );
            $this->assertMatchesRegularExpression( '/^\d{1,3}$/', $bit );
        }
    }
}
```

Tambahkan `require_once` untuk `class-wpmgr-ip.php` ke `bootstrap.php`.

- [ ] **Step 2: Jalankan dan pastikan gagal.**

- [ ] **Step 3: Daftar IP Cloudflare.** Ambil daftar terbaru dari `https://www.cloudflare.com/ips-v4` dan `https://www.cloudflare.com/ips-v6` saat implementasi, lalu tulis dalam bentuk ini. Bila internet tidak tersedia, pakai daftar di bawah (per 2024) dan catat di laporan.

File: `connector/wp-manager-connector/includes/cloudflare-ip.php`
```php
<?php
/**
 * Rentang IP Cloudflare (https://www.cloudflare.com/ips/). Hanya dari rentang
 * ini header CF-Connecting-IP dipercaya. Diperbarui lewat update connector.
 */
return array(
    '173.245.48.0/20',
    '103.21.244.0/22',
    '103.22.200.0/22',
    '103.31.4.0/22',
    '141.101.64.0/18',
    '108.162.192.0/18',
    '190.93.240.0/20',
    '188.114.96.0/20',
    '197.234.240.0/22',
    '198.41.128.0/17',
    '162.158.0.0/15',
    '104.16.0.0/13',
    '104.24.0.0/14',
    '172.64.0.0/13',
    '131.0.72.0/22',
    '2400:cb00::/32',
    '2606:4700::/32',
    '2803:f800::/32',
    '2405:b500::/32',
    '2405:8100::/32',
    '2a06:98c0::/29',
    '2c0f:f248::/32',
);
```

- [ ] **Step 4: Implementasikan kelas IP.**

File: `connector/wp-manager-connector/includes/class-wpmgr-ip.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * IP klien untuk riwayat login. Header proxy hanya dipercaya dari sumber yang
 * terbukti; tanpa itu penyerang cukup mengirim header sendiri untuk
 * memalsukan IP-nya di log.
 */
class WPMGR_IP {

    public static function valid( $ip ) {
        return false !== filter_var( (string) $ip, FILTER_VALIDATE_IP );
    }

    public static function publik( $ip ) {
        return false !== filter_var( (string) $ip, FILTER_VALIDATE_IP,
            FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE );
    }

    public static function dalam_rentang( $ip, $cidr ) {
        $bagian = explode( '/', (string) $cidr, 2 );
        if ( 2 !== count( $bagian ) ) {
            return false;
        }
        $ip_bin  = @inet_pton( (string) $ip );      // phpcs:ignore WordPress.PHP.NoSilencedErrors
        $net_bin = @inet_pton( $bagian[0] );         // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $ip_bin || false === $net_bin || strlen( $ip_bin ) !== strlen( $net_bin ) ) {
            return false;
        }
        $bit  = (int) $bagian[1];
        $utuh = intdiv( $bit, 8 );
        $sisa = $bit % 8;
        if ( substr( $ip_bin, 0, $utuh ) !== substr( $net_bin, 0, $utuh ) ) {
            return false;
        }
        if ( 0 === $sisa ) {
            return true;
        }
        $mask = ( 0xFF << ( 8 - $sisa ) ) & 0xFF;
        return ( ord( $ip_bin[ $utuh ] ) & $mask ) === ( ord( $net_bin[ $utuh ] ) & $mask );
    }

    private static function dari_cloudflare( $ip, array $rentang ) {
        foreach ( $rentang as $cidr ) {
            if ( self::dalam_rentang( $ip, $cidr ) ) {
                return true;
            }
        }
        return false;
    }

    public static function tentukan( array $server, $percayai_xff, array $rentang_cf ) {
        $remote = isset( $server['REMOTE_ADDR'] ) ? trim( (string) $server['REMOTE_ADDR'] ) : '';
        if ( ! self::valid( $remote ) ) {
            return array( 'ip' => null, 'lewat_cloudflare' => false );
        }

        if ( isset( $server['HTTP_CF_CONNECTING_IP'] ) && self::dari_cloudflare( $remote, $rentang_cf ) ) {
            $cf = trim( (string) $server['HTTP_CF_CONNECTING_IP'] );
            if ( self::valid( $cf ) ) {
                return array( 'ip' => $cf, 'lewat_cloudflare' => true );
            }
        }

        if ( $percayai_xff && ! empty( $server['HTTP_X_FORWARDED_FOR'] ) ) {
            // Proxy menambahkan alamat yang ia lihat di posisi paling KANAN;
            // entri di kiri dikendalikan klien.
            $daftar = array_reverse( array_map( 'trim', explode( ',', (string) $server['HTTP_X_FORWARDED_FOR'] ) ) );
            foreach ( $daftar as $kandidat ) {
                if ( self::publik( $kandidat ) ) {
                    return array( 'ip' => $kandidat, 'lewat_cloudflare' => false );
                }
            }
        }

        return array( 'ip' => $remote, 'lewat_cloudflare' => false );
    }

    public static function saat_ini() {
        static $rentang = null;
        if ( null === $rentang ) {
            $rentang = require __DIR__ . '/cloudflare-ip.php';
        }
        $xff = class_exists( 'WPMGR_Settings' ) && WPMGR_Settings::percayai_xff();
        return self::tentukan( $_SERVER, $xff, $rentang );
    }
}
```

Tambahkan `require_once WPMGR_DIR . 'includes/class-wpmgr-ip.php';` ke berkas utama plugin (setelah require settings).

- [ ] **Step 5: PHPUnit dan lint PHP 7.4.** Expected: semua lulus.

- [ ] **Step 6: Commit.**

```bash
git add connector
git commit -m "feat(connector): penentuan IP klien dengan Cloudflare dan proxy tepercaya"
```

### Task 13: Pencatat login, login gagal, dan administrator baru

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-login.php`, `connector/tests/LoginTest.php`
- Modify: `connector/wp-manager-connector/wp-manager-connector.php`, `includes/class-wpmgr-sso.php`, `connector/tests/bootstrap.php`

**Interfaces:**
- Consumes: Task 2 (tabel `wpmgr_logins`, `wpmgr_login_gagal`), Task 12 (`WPMGR_IP::saat_ini()`).
- Produces:
  - `WPMGR_Login::pasang()`; hook `wp_login`, `wp_login_failed`, `application_password_failed_authentication`, `user_register`, `set_user_role`, `add_user_role`, dan `shutdown` (menulis).
  - `WPMGR_Login::catat_berhasil( $user, string $jalur )`, dipanggil juga oleh SSO dengan `'sso'`.
  - Fungsi murni: `jalur( array $konteks ): string`, `jam_dari( int $ts ): int`, `kunci_gagal( $jam, $ip, $username, $jalur ): string`, `tambah_gagal( array &$buffer, $jam, $ip, $username, $jalur, $ua )`, `potong( $teks, $n ): string`, `naik_ke_admin( $role, $old_roles ): bool`.
  - Konstanta `BATAS_GAGAL_PER_JAM = 2000`, `USERNAME_LAIN = '(lainnya)'`.

**Verifikasi core sebelum menulis kode:** baca `wp-includes/pluggable.php` (`wp_authenticate()`: kapan `wp_login_failed` dipicu), `wp-includes/user.php` (`wp_authenticate_application_password()`: urutan `application_password_failed_authentication` terhadap `wp_login_failed`), `wp-includes/class-wp-user.php` (`set_role()`: `set_user_role` dipicu dengan `$old_roles`), dan `wp_insert_user()` (apakah `set_role()` berjalan sebelum `user_register`). Tempel nomor barisnya. Aturan dedup di bawah bergantung pada urutan-urutan itu.

- [ ] **Step 1: Tulis test PHP yang gagal.**

File: `connector/tests/LoginTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class LoginTest extends TestCase {

    public function test_prioritas_jalur(): void {
        $this->assertSame( 'form', WPMGR_Login::jalur( array() ) );
        $this->assertSame( 'rest', WPMGR_Login::jalur( array( 'rest' => true ) ) );
        $this->assertSame( 'xmlrpc', WPMGR_Login::jalur( array( 'xmlrpc' => true, 'rest' => true ) ) );
        $this->assertSame( 'app_password', WPMGR_Login::jalur( array( 'app_password' => true, 'xmlrpc' => true ) ) );
    }

    public function test_jam_dibulatkan_ke_bawah(): void {
        $this->assertSame( 1790064000, WPMGR_Login::jam_dari( 1790067599 ) );
        $this->assertSame( 1790064000, WPMGR_Login::jam_dari( 1790064000 ) );
    }

    public function test_percobaan_berulang_menjadi_satu_baris(): void {
        $buffer = array();
        for ( $i = 0; $i < 500; $i++ ) {
            WPMGR_Login::tambah_gagal( $buffer, 1790064000, '198.51.100.7', 'admin', 'xmlrpc', 'curl/8.0' );
        }
        WPMGR_Login::tambah_gagal( $buffer, 1790064000, '198.51.100.7', 'editor', 'xmlrpc', 'curl/8.0' );
        $this->assertCount( 2, $buffer );
        $baris = $buffer[ WPMGR_Login::kunci_gagal( 1790064000, '198.51.100.7', 'admin', 'xmlrpc' ) ];
        $this->assertSame( 500, $baris['jumlah'] );
        $this->assertSame( 'curl/8.0', $baris['user_agent'] );
    }

    public function test_potong_aman_multibyte(): void {
        $this->assertSame( 'ééé', WPMGR_Login::potong( 'éééé', 3 ) );
    }

    public function test_naik_ke_admin(): void {
        $this->assertTrue( WPMGR_Login::naik_ke_admin( 'administrator', array( 'editor' ) ) );
        // old_roles kosong = user baru; dicatat oleh user_register sebagai admin_baru.
        $this->assertFalse( WPMGR_Login::naik_ke_admin( 'administrator', array() ) );
        $this->assertFalse( WPMGR_Login::naik_ke_admin( 'administrator', array( 'administrator' ) ) );
        $this->assertFalse( WPMGR_Login::naik_ke_admin( 'editor', array( 'author' ) ) );
    }
}
```

Tambahkan `require_once` untuk `class-wpmgr-login.php` ke `bootstrap.php`.

- [ ] **Step 2: Jalankan dan pastikan gagal.**

- [ ] **Step 3: Implementasikan pencatat.**

File: `connector/wp-manager-connector/includes/class-wpmgr-login.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Riwayat login untuk menjawab "apakah site ini diserang, atau sudah dibobol".
 *
 * Login gagal diagregasi per jam per (IP, username, jalur): serangan brute
 * force mengirim ribuan request per jam, dan menyimpan per percobaan berarti
 * satu serangan menghasilkan ribuan baris. (Satu request system.multicall
 * sendiri hanya menghasilkan satu kegagalan: sejak WP 4.4 xmlrpc menolak
 * percobaan berikutnya tanpa memanggil wp_authenticate().) Semua kejadian
 * ditampung selama request dan ditulis sekali di hook shutdown.
 */
class WPMGR_Login {

    const BATAS_GAGAL_PER_JAM = 2000;
    const USERNAME_LAIN       = '(lainnya)';

    private static $berhasil  = array();
    private static $gagal     = array();
    private static $app_gagal = array();

    public static function pasang() {
        add_action( 'wp_login', array( __CLASS__, 'saat_berhasil' ), 10, 2 );
        add_action( 'wp_login_failed', array( __CLASS__, 'saat_gagal' ), 10, 1 );
        add_action( 'application_password_failed_authentication', array( __CLASS__, 'saat_gagal_app' ), 10, 1 );
        add_action( 'user_register', array( __CLASS__, 'saat_user_baru' ), 10, 1 );
        add_action( 'set_user_role', array( __CLASS__, 'saat_role_diset' ), 10, 3 );
        add_action( 'add_user_role', array( __CLASS__, 'saat_role_ditambah' ), 10, 2 );
        add_action( 'shutdown', array( __CLASS__, 'tulis' ), 1 );
    }

    // ---- fungsi murni ---------------------------------------------------

    public static function jalur( array $konteks ) {
        if ( ! empty( $konteks['app_password'] ) ) {
            return 'app_password';
        }
        if ( ! empty( $konteks['xmlrpc'] ) ) {
            return 'xmlrpc';
        }
        if ( ! empty( $konteks['rest'] ) ) {
            return 'rest';
        }
        return 'form';
    }

    public static function jam_dari( $ts ) {
        return (int) $ts - ( (int) $ts % 3600 );
    }

    public static function kunci_gagal( $jam, $ip, $username, $jalur ) {
        return $jam . '|' . $ip . '|' . $username . '|' . $jalur;
    }

    public static function tambah_gagal( array &$buffer, $jam, $ip, $username, $jalur, $ua ) {
        $k = self::kunci_gagal( $jam, $ip, $username, $jalur );
        if ( isset( $buffer[ $k ] ) ) {
            $buffer[ $k ]['jumlah']++;
            return;
        }
        $buffer[ $k ] = array(
            'jam' => $jam, 'ip' => $ip, 'username' => $username, 'jalur' => $jalur,
            'jumlah' => 1, 'user_agent' => $ua,
        );
    }

    public static function potong( $teks, $n ) {
        $teks = (string) $teks;
        return function_exists( 'mb_substr' ) ? mb_substr( $teks, 0, $n ) : substr( $teks, 0, $n );
    }

    public static function naik_ke_admin( $role, $old_roles ) {
        $lama = (array) $old_roles;
        return 'administrator' === $role && ! empty( $lama ) && ! in_array( 'administrator', $lama, true );
    }

    // ---- konteks request ------------------------------------------------

    private static function konteks_sekarang( $app = false ) {
        return array(
            'app_password' => $app,
            'xmlrpc'       => defined( 'XMLRPC_REQUEST' ) && XMLRPC_REQUEST,
            'rest'         => defined( 'REST_REQUEST' ) && REST_REQUEST,
        );
    }

    private static function ua() {
        $ua = isset( $_SERVER['HTTP_USER_AGENT'] ) ? self::potong( wp_unslash( $_SERVER['HTTP_USER_AGENT'] ), 255 ) : '';
        return '' === $ua ? null : $ua;
    }

    private static function role_utama( $user ) {
        $roles = isset( $user->roles ) ? (array) $user->roles : array();
        return empty( $roles ) ? null : (string) reset( $roles );
    }

    private static function aktif() {
        return ! WPMGR_Skema::monitoring_mati();
    }

    private static function catat_baris( $jenis, $user, $jalur ) {
        $ip                = WPMGR_IP::saat_ini();
        self::$berhasil[] = array(
            'waktu'            => time(),
            'jenis'            => $jenis,
            'username'         => self::potong( $user->user_login, 60 ),
            'role'             => self::role_utama( $user ),
            'ip'               => $ip['ip'],
            'lewat_cloudflare' => $ip['lewat_cloudflare'] ? 1 : 0,
            'user_agent'       => self::ua(),
            'jalur'            => $jalur,
        );
    }

    // ---- hook -----------------------------------------------------------

    public static function catat_berhasil( $user, $jalur ) {
        try {
            if ( self::aktif() && is_object( $user ) && isset( $user->user_login ) ) {
                self::catat_baris( 'berhasil', $user, $jalur );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function saat_berhasil( $user_login, $user = null ) {
        self::catat_berhasil( $user, self::jalur( self::konteks_sekarang() ) );
    }

    private static function tambah_gagal_sekarang( $username, $jalur ) {
        $ip = WPMGR_IP::saat_ini();
        self::tambah_gagal( self::$gagal, self::jam_dari( time() ), (string) $ip['ip'],
            self::potong( $username, 60 ), $jalur, self::ua() );
    }

    public static function saat_gagal( $username ) {
        try {
            if ( ! self::aktif() ) {
                return;
            }
            // application_password_failed_authentication dipicu lebih dulu untuk
            // percobaan yang sama; jangan hitung dua kali.
            if ( ! empty( self::$app_gagal[ strtolower( (string) $username ) ] ) ) {
                return;
            }
            self::tambah_gagal_sekarang( (string) $username, self::jalur( self::konteks_sekarang() ) );
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function saat_gagal_app( $error ) {
        try {
            if ( ! self::aktif() ) {
                return;
            }
            $username = isset( $_SERVER['PHP_AUTH_USER'] ) ? (string) wp_unslash( $_SERVER['PHP_AUTH_USER'] ) : '(tidak diketahui)';
            self::$app_gagal[ strtolower( $username ) ] = true;
            self::tambah_gagal_sekarang( $username, 'app_password' );
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    private static function catat_admin( $user_id, $jenis ) {
        $user = get_userdata( $user_id );
        // User wpmgr dibuat connector sendiri saat pairing; mencatatnya membuat
        // setiap site baru langsung berstatus merah di dashboard.
        if ( ! $user || WPMGR_USER_LOGIN === $user->user_login ) {
            return;
        }
        self::catat_baris( $jenis, $user, self::jalur( self::konteks_sekarang() ) );
    }

    public static function saat_user_baru( $user_id ) {
        try {
            $user = self::aktif() ? get_userdata( $user_id ) : false;
            if ( $user && in_array( 'administrator', (array) $user->roles, true ) ) {
                self::catat_admin( $user_id, 'admin_baru' );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function saat_role_diset( $user_id, $role, $old_roles ) {
        try {
            if ( self::aktif() && self::naik_ke_admin( $role, $old_roles ) ) {
                self::catat_admin( $user_id, 'jadi_admin' );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function saat_role_ditambah( $user_id, $role ) {
        try {
            if ( self::aktif() && 'administrator' === $role ) {
                self::catat_admin( $user_id, 'jadi_admin' );
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function tulis() {
        try {
            if ( ( empty( self::$berhasil ) && empty( self::$gagal ) ) || ! self::aktif() ) {
                return;
            }
            global $wpdb;
            $p        = $wpdb->prefix;
            $sekarang = time();
            $lama     = $wpdb->suppress_errors( true );
            try {
                foreach ( self::$berhasil as $b ) {
                    $b['diubah'] = $sekarang;
                    $wpdb->insert( $p . 'wpmgr_logins', $b );
                }
                foreach ( self::$gagal as $g ) {
                    $jumlah_jam = (int) $wpdb->get_var( $wpdb->prepare(
                        "SELECT COUNT(*) FROM {$p}wpmgr_login_gagal WHERE jam = %d", $g['jam'] ) );
                    if ( $jumlah_jam >= self::BATAS_GAGAL_PER_JAM ) {
                        $g['ip']       = '';
                        $g['username'] = self::USERNAME_LAIN;
                    }
                    $wpdb->query( $wpdb->prepare(
                        "INSERT INTO {$p}wpmgr_login_gagal (jam, ip, username, jalur, jumlah, user_agent, diubah)
                         VALUES (%d, %s, %s, %s, %d, %s, %d)
                         ON DUPLICATE KEY UPDATE jumlah = jumlah + VALUES(jumlah), user_agent = VALUES(user_agent), diubah = VALUES(diubah)",
                        $g['jam'], $g['ip'], $g['username'], $g['jalur'], $g['jumlah'], (string) $g['user_agent'], $sekarang
                    ) );
                }
            } finally {
                $wpdb->suppress_errors( $lama );
            }
            self::$berhasil = array();
            self::$gagal    = array();
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }
}
```

- [ ] **Step 4: Pasang dan catat SSO.** Di berkas utama, tambahkan `require_once WPMGR_DIR . 'includes/class-wpmgr-login.php';` setelah require IP, lalu di dalam blok `if ( ! WPMGR_Skema::monitoring_mati() )` yang sama dengan penangkap, tambahkan `WPMGR_Login::pasang();`. Di `WPMGR_SSO::tangani_permintaan()`, tepat sebelum `wp_set_current_user( $user_id );`, tambahkan:

```php
        // wp_set_auth_cookie() tidak memicu wp_login, jadi login lewat SSO
        // dicatat eksplisit supaya riwayat login site tetap lengkap.
        if ( class_exists( 'WPMGR_Login' ) ) {
            WPMGR_Login::catat_berhasil( get_userdata( $user_id ), 'sso' );
        }
```

- [ ] **Step 5: PHPUnit dan lint PHP 7.4.** Expected: semua lulus.

- [ ] **Step 6: Commit.**

```bash
git add connector
git commit -m "feat(connector): riwayat login, login gagal teragregasi, dan administrator baru"
```

### Task 14: Endpoint `/events` dan pengumuman fitur `events`

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-events.php`, `connector/tests/EventsTest.php`
- Modify: `connector/wp-manager-connector/wp-manager-connector.php`, `includes/class-wpmgr-rest.php`, `includes/class-wpmgr-skema.php`, `connector/tests/SkemaTest.php`, `connector/tests/bootstrap.php`

**Interfaces:**
- Consumes: Task 11 dan 13 (isi tabel).
- Produces:
  - `GET /wp-json/wpmgr/v1/events?kursor=<k>&batas=<n>` (HMAC) → `{errors: [...], logins: [...], login_gagal: [...], kursor: string, lagi: bool}`.
  - Bentuk item:
    - `errors`: `{sidik_jari, tingkat, komponen_tipe, komponen_slug|null, pesan, file|null, baris|null, konteks|null, jumlah, pertama, terakhir}`, waktu dalam detik Unix.
    - `logins`: `{id, waktu, jenis, username, role|null, ip|null, lewat_cloudflare, user_agent|null, jalur}`.
    - `login_gagal`: `{jam, ip, username, jalur, jumlah, user_agent|null}`.
  - Kursor buram berformat `e=<detik>:<id>;l=<detik>:<id>;g=<detik>:<id>`.
  - `WPMGR_Events::urai_kursor( $kursor ): array`, `susun_kursor( array $posisi ): string`, `posisi_berikut( array $lama, array $baris, int $batas ): array`, `batas( $nilai ): int`, `bentuk_error`, `bentuk_login`, `bentuk_gagal`.
  - `WPMGR_Skema::fitur( false )` → `array( 'self_update', 'events' )`; `fitur( true )` → `array( 'self_update' )`.

Aturan kursor per tabel: bila satu halaman penuh (`count == batas`), posisi berikutnya adalah `(diubah, id)` baris terakhir, persis, supaya paging tidak berputar di tempat. Bila tidak penuh, posisi berikutnya adalah `(max(diubah) - 2, 0)`: dua detik terakhir diambil ulang pada pengambilan berikutnya, karena baris bisa diperbarui pada detik yang sama tepat setelah pengambilan. Duplikat yang dihasilkannya tidak berbahaya karena semua upsert di dashboard idempoten (spec §6.2).

- [ ] **Step 1: Tulis test PHP yang gagal.**

File: `connector/tests/EventsTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class EventsTest extends TestCase {

    public function test_kursor_kosong_mulai_dari_nol(): void {
        $this->assertSame(
            array( 'e' => array( 0, 0 ), 'l' => array( 0, 0 ), 'g' => array( 0, 0 ) ),
            WPMGR_Events::urai_kursor( '' )
        );
    }

    public function test_kursor_bolak_balik(): void {
        $posisi = array( 'e' => array( 1790000000, 12 ), 'l' => array( 5, 0 ), 'g' => array( 0, 0 ) );
        $this->assertSame( $posisi, WPMGR_Events::urai_kursor( WPMGR_Events::susun_kursor( $posisi ) ) );
        $this->assertSame( 'e=1790000000:12;l=5:0;g=0:0', WPMGR_Events::susun_kursor( $posisi ) );
    }

    public function test_bagian_kursor_rusak_diabaikan(): void {
        $posisi = WPMGR_Events::urai_kursor( "e=10:2;l=x:y;g=-1:3;z=1:1;e2=1:1" );
        $this->assertSame( array( 10, 2 ), $posisi['e'] );
        $this->assertSame( array( 0, 0 ), $posisi['l'] );
        $this->assertSame( array( 0, 0 ), $posisi['g'] );
    }

    public function test_halaman_penuh_melanjutkan_persis_dari_baris_terakhir(): void {
        $baris = array( array( 'diubah' => 100, 'id' => 7 ), array( 'diubah' => 100, 'id' => 9 ) );
        $this->assertSame( array( 100, 9 ), WPMGR_Events::posisi_berikut( array( 0, 0 ), $baris, 2 ) );
    }

    public function test_halaman_tidak_penuh_mundur_dua_detik(): void {
        $baris = array( array( 'diubah' => 100, 'id' => 7 ), array( 'diubah' => 104, 'id' => 3 ) );
        $this->assertSame( array( 102, 0 ), WPMGR_Events::posisi_berikut( array( 0, 0 ), $baris, 500 ) );
    }

    public function test_tanpa_baris_mundur_dua_detik_dari_posisi_lama(): void {
        $this->assertSame( array( 98, 0 ), WPMGR_Events::posisi_berikut( array( 100, 5 ), array(), 500 ) );
        $this->assertSame( array( 0, 0 ), WPMGR_Events::posisi_berikut( array( 1, 0 ), array(), 500 ) );
    }

    public function test_batas_dijepit(): void {
        $this->assertSame( 500, WPMGR_Events::batas( null ) );
        $this->assertSame( 500, WPMGR_Events::batas( 0 ) );
        $this->assertSame( 500, WPMGR_Events::batas( 10000 ) );
        $this->assertSame( 50, WPMGR_Events::batas( '50' ) );
    }

    public function test_bentuk_error(): void {
        $b = WPMGR_Events::bentuk_error( array(
            'id' => '3', 'sidik_jari' => str_repeat( 'a', 32 ), 'tingkat' => 'fatal',
            'komponen_tipe' => 'core', 'komponen_slug' => '', 'pesan' => 'x', 'file' => '',
            'baris' => null, 'konteks' => '{"path":"/","jenis":"depan"}', 'jumlah' => '4',
            'pertama' => '10', 'terakhir' => '20', 'diubah' => '20',
        ) );
        $this->assertNull( $b['komponen_slug'] );
        $this->assertNull( $b['file'] );
        $this->assertNull( $b['baris'] );
        $this->assertSame( array( 'path' => '/', 'jenis' => 'depan' ), $b['konteks'] );
        $this->assertSame( 4, $b['jumlah'] );
        $this->assertSame( 20, $b['terakhir'] );
        $this->assertArrayNotHasKey( 'diubah', $b );
    }

    public function test_bentuk_login_dan_gagal(): void {
        $l = WPMGR_Events::bentuk_login( array(
            'id' => '9', 'waktu' => '100', 'jenis' => 'berhasil', 'username' => 'admin', 'role' => 'administrator',
            'ip' => '203.0.113.9', 'lewat_cloudflare' => '1', 'user_agent' => 'UA', 'jalur' => 'form', 'diubah' => '100',
        ) );
        $this->assertSame( 9, $l['id'] );
        $this->assertTrue( $l['lewat_cloudflare'] );
        $g = WPMGR_Events::bentuk_gagal( array(
            'id' => '1', 'jam' => '3600', 'ip' => '', 'username' => '(lainnya)', 'jalur' => 'form',
            'jumlah' => '12', 'user_agent' => null, 'diubah' => '3700',
        ) );
        $this->assertSame( array( 'jam' => 3600, 'ip' => '', 'username' => '(lainnya)', 'jalur' => 'form',
                                  'jumlah' => 12, 'user_agent' => null ), $g );
    }
}
```

Ubah `test_fitur_yang_diumumkan` di `SkemaTest.php`:

```php
    public function test_fitur_yang_diumumkan(): void {
        $this->assertSame( array( 'self_update', 'events' ), WPMGR_Skema::fitur( false ) );
        // Self-update bukan pemantauan: tetap tersedia walau pemantauan dimatikan.
        $this->assertSame( array( 'self_update' ), WPMGR_Skema::fitur( true ) );
    }
```

Tambahkan `require_once` untuk `class-wpmgr-events.php` ke `bootstrap.php`.

- [ ] **Step 2: Jalankan dan pastikan gagal.**

- [ ] **Step 3: Implementasikan.**

File: `connector/wp-manager-connector/includes/class-wpmgr-events.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * /events: error, login, dan login gagal yang berubah sejak kursor terakhir.
 *
 * Kursor buram bagi dashboard; ia hanya mengembalikannya pada pengambilan
 * berikutnya. Setiap tabel punya posisinya sendiri berupa (diubah, id).
 */
class WPMGR_Events {

    const BATAS_MAKS     = 500;
    const TUMPANG_TINDIH = 2;
    const TABEL          = array( 'e' => 'wpmgr_errors', 'l' => 'wpmgr_logins', 'g' => 'wpmgr_login_gagal' );

    public static function urai_kursor( $kursor ) {
        $posisi = array( 'e' => array( 0, 0 ), 'l' => array( 0, 0 ), 'g' => array( 0, 0 ) );
        foreach ( explode( ';', (string) $kursor ) as $bagian ) {
            if ( preg_match( '/^([elg])=(\d{1,10}):(\d{1,19})$/', $bagian, $m ) ) {
                $posisi[ $m[1] ] = array( (int) $m[2], (int) $m[3] );
            }
        }
        return $posisi;
    }

    public static function susun_kursor( array $posisi ) {
        $bagian = array();
        foreach ( array( 'e', 'l', 'g' ) as $k ) {
            $bagian[] = $k . '=' . (int) $posisi[ $k ][0] . ':' . (int) $posisi[ $k ][1];
        }
        return implode( ';', $bagian );
    }

    public static function posisi_berikut( array $lama, array $baris, $batas ) {
        if ( count( $baris ) >= $batas && ! empty( $baris ) ) {
            $akhir = end( $baris );
            return array( (int) $akhir['diubah'], (int) $akhir['id'] );
        }
        $maks = (int) $lama[0];
        foreach ( $baris as $b ) {
            $maks = max( $maks, (int) $b['diubah'] );
        }
        return array( max( 0, $maks - self::TUMPANG_TINDIH ), 0 );
    }

    public static function batas( $nilai ) {
        $n = (int) $nilai;
        return ( $n < 1 || $n > self::BATAS_MAKS ) ? self::BATAS_MAKS : $n;
    }

    private static function atau_null( $nilai ) {
        return ( null === $nilai || '' === (string) $nilai ) ? null : (string) $nilai;
    }

    public static function bentuk_error( $b ) {
        $konteks = null === $b['konteks'] ? null : json_decode( (string) $b['konteks'], true );
        return array(
            'sidik_jari'    => (string) $b['sidik_jari'],
            'tingkat'       => (string) $b['tingkat'],
            'komponen_tipe' => (string) $b['komponen_tipe'],
            'komponen_slug' => self::atau_null( $b['komponen_slug'] ),
            'pesan'         => (string) $b['pesan'],
            'file'          => self::atau_null( $b['file'] ),
            'baris'         => null === $b['baris'] ? null : (int) $b['baris'],
            'konteks'       => is_array( $konteks ) ? $konteks : null,
            'jumlah'        => (int) $b['jumlah'],
            'pertama'       => (int) $b['pertama'],
            'terakhir'      => (int) $b['terakhir'],
        );
    }

    public static function bentuk_login( $b ) {
        return array(
            'id'               => (int) $b['id'],
            'waktu'            => (int) $b['waktu'],
            'jenis'            => (string) $b['jenis'],
            'username'         => (string) $b['username'],
            'role'             => self::atau_null( $b['role'] ),
            'ip'               => self::atau_null( $b['ip'] ),
            'lewat_cloudflare' => (bool) (int) $b['lewat_cloudflare'],
            'user_agent'       => self::atau_null( $b['user_agent'] ),
            'jalur'            => (string) $b['jalur'],
        );
    }

    public static function bentuk_gagal( $b ) {
        return array(
            'jam'        => (int) $b['jam'],
            'ip'         => (string) $b['ip'],
            'username'   => (string) $b['username'],
            'jalur'      => (string) $b['jalur'],
            'jumlah'     => (int) $b['jumlah'],
            'user_agent' => self::atau_null( $b['user_agent'] ),
        );
    }

    public static function kumpulkan( $kursor, $batas ) {
        global $wpdb;
        $posisi = self::urai_kursor( $kursor );
        $batas  = self::batas( $batas );
        $hasil  = array();
        $lagi   = false;
        foreach ( self::TABEL as $k => $tabel ) {
            list( $t, $id ) = $posisi[ $k ];
            $baris = $wpdb->get_results( $wpdb->prepare(
                "SELECT * FROM {$wpdb->prefix}{$tabel}
                  WHERE diubah > %d OR (diubah = %d AND id > %d)
                  ORDER BY diubah ASC, id ASC LIMIT %d",
                $t, $t, $id, $batas
            ), ARRAY_A );
            $baris = is_array( $baris ) ? $baris : array();
            if ( count( $baris ) >= $batas ) {
                $lagi = true;
            }
            $posisi[ $k ] = self::posisi_berikut( $posisi[ $k ], $baris, $batas );
            $hasil[ $k ]  = $baris;
        }
        return array(
            'errors'      => array_map( array( __CLASS__, 'bentuk_error' ), $hasil['e'] ),
            'logins'      => array_map( array( __CLASS__, 'bentuk_login' ), $hasil['l'] ),
            'login_gagal' => array_map( array( __CLASS__, 'bentuk_gagal' ), $hasil['g'] ),
            'kursor'      => self::susun_kursor( $posisi ),
            'lagi'        => $lagi,
        );
    }
}
```

- [ ] **Step 4: Route dan fitur.** Tambahkan `require_once WPMGR_DIR . 'includes/class-wpmgr-events.php';` ke berkas utama. Di `WPMGR_REST::daftarkan_route()`:

```php
        register_rest_route( self::NS, '/events', array(
            'methods'             => 'GET',
            'callback'            => array( __CLASS__, 'events' ),
            'permission_callback' => $guard,
        ) );
```

```php
    public static function events( $request ) {
        return rest_ensure_response(
            WPMGR_Events::kumpulkan( $request->get_param( 'kursor' ), $request->get_param( 'batas' ) )
        );
    }
```

Di `WPMGR_Skema::fitur()`:

```php
    public static function fitur( $monitoring_mati ) {
        $fitur = array( 'self_update' );
        if ( ! $monitoring_mati ) {
            $fitur[] = 'events';
        }
        return $fitur;
    }
```

- [ ] **Step 5: PHPUnit dan lint PHP 7.4.** Expected: semua lulus.

- [ ] **Step 6: Uji asap di container.** Dengan `docker compose up -d` dan e2e sudah pernah dijalankan sekali (site terpasang), panggil `/events` lewat `permintaan_bertanda` dari Python REPL, atau jalankan ulang `tests/e2e/test_fondasi_lapis2.py`. Expected: `test_ping_mengumumkan_kemampuan` kini melihat `events` di `fitur`. Uji e2e fungsional penuh ada di Task 26.

- [ ] **Step 7: Commit.**

```bash
git add connector
git commit -m "feat(connector): endpoint events dengan kursor per tabel"
```

---

## Fase E — Penyimpanan dan penilaian di dashboard

### Task 15: Penguraian user-agent dan GeoIP

**Files:**
- Create: `src/wpmgr/uagent.py`, `src/wpmgr/geoip.py`, `tests/unit/test_uagent.py`, `tests/unit/test_geoip.py`
- Modify: `src/wpmgr/cli.py`

**Interfaces:**
- Consumes: Task 1 (`Settings.jalur_geoip`), Task 9 (`kunci_advisory`, `KUNCI_GEOIP`).
- Produces:
  - `wpmgr.uagent.urai_ua(ua: str | None) -> dict` dengan kunci `peramban`, `os`, `skrip` (bool).
  - `wpmgr.geoip.negara(ip: str | None) -> str | None` (kode ISO dua huruf); `wpmgr.geoip.reset_cache() -> None`; `wpmgr.geoip.unduh_geoip(tujuan: Path, hari_ini: date, http: httpx.Client) -> str` (mengembalikan URL yang dipakai); konstanta `URL_DBIP`.
  - CLI `update-geoip`.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/unit/test_uagent.py`
```python
import pytest

from wpmgr.uagent import urai_ua

CHROME_WIN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
EDGE = CHROME_WIN + " Edg/128.0.0.0"
SAFARI_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
SAMSUNG = ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) "
           "SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36")
SAFARI_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.5 Safari/605.1.15")


@pytest.mark.parametrize(
    ("ua", "peramban", "os_"),
    [
        (CHROME_WIN, "Chrome", "Windows"),
        (EDGE, "Edge", "Windows"),
        (SAFARI_IPHONE, "Safari", "iOS"),
        (FIREFOX_LINUX, "Firefox", "Linux"),
        (SAMSUNG, "Samsung Internet", "Android"),
        (SAFARI_MAC, "Safari", "macOS"),
    ],
)
def test_peramban_dan_os(ua, peramban, os_):
    hasil = urai_ua(ua)
    assert (hasil["peramban"], hasil["os"], hasil["skrip"]) == (peramban, os_, False)


@pytest.mark.parametrize(
    "ua",
    ["curl/8.4.0", "python-requests/2.32.3", "Go-http-client/1.1", "Wget/1.21",
     "okhttp/4.12.0", "", None, "   "],
)
def test_skrip_dan_ua_kosong(ua):
    assert urai_ua(ua)["skrip"] is True


def test_ua_asing_menjadi_lainnya():
    assert urai_ua("SesuatuYangAneh/1.0") == {"peramban": "lainnya", "os": "lainnya", "skrip": False}
```

File: `tests/unit/test_geoip.py`
```python
import gzip
from datetime import date

import httpx
import pytest

from wpmgr import geoip


@pytest.fixture
def geoip_di(tmp_path, monkeypatch):
    from wpmgr.config import get_settings

    jalur = tmp_path / "geo" / "negara.mmdb"
    monkeypatch.setenv("WPMGR_GEOIP_PATH", str(jalur))
    get_settings.cache_clear()
    geoip.reset_cache()
    yield jalur
    geoip.reset_cache()


def test_tanpa_database_negara_none(geoip_di):
    assert geoip.negara("8.8.8.8") is None
    assert geoip.negara(None) is None
    assert geoip.negara("") is None


def test_database_rusak_tidak_melempar(geoip_di):
    geoip_di.parent.mkdir(parents=True)
    geoip_di.write_bytes(b"bukan mmdb")
    assert geoip.negara("8.8.8.8") is None


def test_unduh_bulan_ini(tmp_path):
    diminta = []

    def handler(request):
        diminta.append(str(request.url))
        return httpx.Response(200, content=gzip.compress(b"ISI-MMDB"))

    tujuan = tmp_path / "g" / "db.mmdb"
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        url = geoip.unduh_geoip(tujuan, date(2026, 9, 22), http)
    assert url.endswith("dbip-country-lite-2026-09.mmdb.gz")
    assert tujuan.read_bytes() == b"ISI-MMDB"
    assert diminta == [url]


def test_jatuh_ke_bulan_lalu_bila_bulan_ini_belum_terbit(tmp_path):
    def handler(request):
        if "2026-01" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=gzip.compress(b"LAMA"))

    tujuan = tmp_path / "db.mmdb"
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        url = geoip.unduh_geoip(tujuan, date(2026, 1, 3), http)
    assert url.endswith("dbip-country-lite-2025-12.mmdb.gz")
    assert tujuan.read_bytes() == b"LAMA"


def test_keduanya_tidak_ada_melempar(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as http:
        with pytest.raises(RuntimeError):
            geoip.unduh_geoip(tmp_path / "db.mmdb", date(2026, 9, 22), http)
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implementasikan.**

File: `src/wpmgr/uagent.py`
```python
import re

_SKRIP = re.compile(
    r"curl|wget|python-requests|python-urllib|go-http-client|java/|okhttp|libwww-perl|"
    r"postmanruntime|httpclient|axios/|node-fetch|scrapy",
    re.I,
)
# Urutan penting: Edge, Opera, dan Samsung Internet juga memuat "Chrome/";
# Chrome memuat "Safari/".
_PERAMBAN = (
    ("Edge", re.compile(r"Edg(e|A|iOS)?/")),
    ("Opera", re.compile(r"OPR/|Opera")),
    ("Samsung Internet", re.compile(r"SamsungBrowser/")),
    ("Firefox", re.compile(r"Firefox/|FxiOS/")),
    ("Chrome", re.compile(r"Chrome/|CriOS/")),
    ("Safari", re.compile(r"Version/[\d.]+.*Safari/")),
)
# iOS sebelum macOS (UA iPhone memuat "like Mac OS X"); Android sebelum Linux.
_OS = (
    ("Windows", re.compile(r"Windows NT")),
    ("iOS", re.compile(r"iPhone|iPad|iPod")),
    ("Android", re.compile(r"Android")),
    ("macOS", re.compile(r"Mac OS X|Macintosh")),
    ("Linux", re.compile(r"Linux|X11")),
)


def urai_ua(ua: str | None) -> dict:
    if not ua or not ua.strip():
        # UA kosong hampir selalu alat otomatis, bukan peramban.
        return {"peramban": "lainnya", "os": "lainnya", "skrip": True}
    peramban = next((nama for nama, pola in _PERAMBAN if pola.search(ua)), "lainnya")
    sistem = next((nama for nama, pola in _OS if pola.search(ua)), "lainnya")
    return {"peramban": peramban, "os": sistem, "skrip": bool(_SKRIP.search(ua))}
```

File: `src/wpmgr/geoip.py`
```python
"""Negara dari IP memakai DB-IP Lite Country (CC BY 4.0).

File database opsional: tanpa file, negara() mengembalikan None dan fitur
lain tetap berjalan. Pembaca di-cache per (path, mtime) sehingga unduhan
bulanan terbaca tanpa me-restart proses.
"""

import gzip
import threading
from datetime import date
from pathlib import Path

import httpx
import maxminddb

from wpmgr.config import get_settings

URL_DBIP = "https://download.db-ip.com/free/dbip-country-lite-{tahun:04d}-{bulan:02d}.mmdb.gz"

_kunci = threading.Lock()
_pembaca = None
_tanda: tuple[str, float] | None = None


def reset_cache() -> None:
    global _pembaca, _tanda
    with _kunci:
        if _pembaca is not None:
            _pembaca.close()
        _pembaca = None
        _tanda = None


def _buka():
    global _pembaca, _tanda
    jalur = get_settings().jalur_geoip
    try:
        mtime = jalur.stat().st_mtime
    except OSError:
        return None
    with _kunci:
        if _tanda != (str(jalur), mtime):
            if _pembaca is not None:
                _pembaca.close()
            _pembaca = maxminddb.open_database(str(jalur))
            _tanda = (str(jalur), mtime)
        return _pembaca


def negara(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        pembaca = _buka()
        if pembaca is None:
            return None
        data = pembaca.get(ip)
    except (ValueError, OSError, maxminddb.InvalidDatabaseError):
        return None
    if not isinstance(data, dict):
        return None
    kode = (data.get("country") or {}).get("iso_code")
    return kode if isinstance(kode, str) else None


def _bulan_mundur(hari: date, mundur: int) -> tuple[int, int]:
    tahun, bulan = hari.year, hari.month - mundur
    while bulan < 1:
        bulan += 12
        tahun -= 1
    return tahun, bulan


def unduh_geoip(tujuan: Path, hari_ini: date, http: httpx.Client) -> str:
    # Berkas bulan berjalan terbit di awal bulan; pada tanggal 1-2 bisa belum ada.
    for mundur in (0, 1):
        tahun, bulan = _bulan_mundur(hari_ini, mundur)
        url = URL_DBIP.format(tahun=tahun, bulan=bulan)
        r = http.get(url)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        isi = gzip.decompress(r.content)
        tujuan.parent.mkdir(parents=True, exist_ok=True)
        sementara = tujuan.with_name(tujuan.name + ".tmp")
        sementara.write_bytes(isi)
        sementara.replace(tujuan)
        reset_cache()
        return url
    raise RuntimeError("Database DB-IP untuk bulan ini maupun bulan lalu tidak tersedia")
```

- [ ] **Step 4: Perintah CLI.** Tambahkan ke `cli.py` (impor `from datetime import date`, `import httpx`, `KUNCI_GEOIP`, `from wpmgr.geoip import unduh_geoip`):

```python
def update_geoip() -> str | None:
    with kunci_advisory(db.engine, KUNCI_GEOIP) as dapat:
        if not dapat:
            print("Pembaruan GeoIP lain masih berjalan; dilewati")
            return None
        with httpx.Client(timeout=120, follow_redirects=True) as http:
            url = unduh_geoip(get_settings().jalur_geoip, date.today(), http)
    print(f"Database GeoIP diperbarui dari {url}")
    return url
```

beserta `sub.add_parser("update-geoip")` dan cabangnya.

- [ ] **Step 5: Jalankan test unit.** Expected: semua lulus.

- [ ] **Step 6: Verifikasi manual dengan database sungguhan.** Dengan env Task 1 Step 12 dan `WPMGR_VAR_DIR` sementara:

```bash
.venv/Scripts/python -m wpmgr.cli update-geoip
.venv/Scripts/python -c "from wpmgr.geoip import negara; print(negara('8.8.8.8'), negara('103.10.66.1'))"
```

Expected: baris pertama menyebut URL `dbip-country-lite-YYYY-MM.mmdb.gz`; baris kedua `US ID` (atau kode negara yang benar untuk kedua IP). Tempel keluarannya. Bila mesin tanpa internet, catat di laporan dan lanjutkan: test unit sudah mencakup jalur tanpa database.

- [ ] **Step 7: Commit.**

```bash
git add src/wpmgr/uagent.py src/wpmgr/geoip.py src/wpmgr/cli.py tests/unit/test_uagent.py tests/unit/test_geoip.py
git commit -m "feat: penguraian user-agent dan negara IP dari DB-IP Lite"
```

### Task 16: Job `collect_events` dan perintah `enqueue-monitoring`

**Files:**
- Modify: `src/wpmgr/jobs/monitoring.py`, `src/wpmgr/jobs/handlers.py`, `src/wpmgr/cli.py`
- Test: `tests/integration/test_collect_events.py`

**Interfaces:**
- Consumes: Task 4 (`SiteClient.events`, `EVENTS`), Task 7 (`antrekan_jika_belum`), Task 14 (bentuk respons `/events`), Task 15 (`geoip.negara`).
- Produces (di `wpmgr.jobs.monitoring`):
  - `BATAS_HALAMAN_EVENTS = 10`, `JENDELA_KAITAN_UPDATE = timedelta(minutes=60)`.
  - `simpan_errors(sesi, site, baris: list) -> int`, `simpan_logins(sesi, site, baris) -> int`, `simpan_login_gagal(sesi, site, baris) -> int`.
  - `kaitkan_update(sesi, site_id, komponen_tipe: str, slug: str | None, pertama: datetime) -> dict | None`.
  - `tangani_collect_events(sesi, job, klien) -> dict` (hitungan per jenis dan jumlah halaman); `HANDLER[JobType.collect_events]`.
  - CLI `enqueue-monitoring` (`wpmgr.cli.enqueue_monitoring() -> int`).

Baris yang rusak (field hilang, angka tidak bisa diurai) **dilewati**, tidak menggagalkan job. Site yang disusupi bisa mengirim apa saja, dan satu baris beracun tidak boleh menghentikan pengambilan data site itu selamanya karena kursor tidak pernah maju.

- [ ] **Step 1: Tulis test integrasi yang gagal.**

File: `tests/integration/test_collect_events.py`
```python
import copy
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from wpmgr.jobs.monitoring import tangani_collect_events
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    CatatanError,
    Job,
    JobStatus,
    JobType,
    KejadianLogin,
    LoginGagal,
    SiteStatus,
)
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration

T = 1790064000  # 2026-09-22 08:00 UTC
WAKTU_T = datetime.fromtimestamp(T, tz=timezone.utc)

PAYLOAD = {
    "errors": [{
        "sidik_jari": "a" * 32, "tingkat": "fatal", "komponen_tipe": "plugin",
        "komponen_slug": "elementor", "pesan": "Uncaught Error: x()", "file": "wp-content/plugins/elementor/a.php",
        "baris": 12, "konteks": {"path": "/", "jenis": "depan"}, "jumlah": 3,
        "pertama": T, "terakhir": T + 60,
    }],
    "logins": [{
        "id": 5, "waktu": T, "jenis": "berhasil", "username": "admin", "role": "administrator",
        "ip": "203.0.113.9", "lewat_cloudflare": False, "user_agent": "curl/8", "jalur": "form",
    }],
    "login_gagal": [{
        "jam": T, "ip": "198.51.100.7", "username": "admin", "jalur": "xmlrpc", "jumlah": 40,
        "user_agent": "python-requests/2",
    }],
    "kursor": "e=10:1;l=10:5;g=10:1",
    "lagi": False,
}


def klien_berurutan(balasan, diminta):
    antrian = list(balasan)

    def handler(request):
        diminta.append(request)
        return httpx.Response(200, json=antrian.pop(0) if len(antrian) > 1 else antrian[0])

    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


def jalankan(sesi, site, balasan, diminta=None):
    job = buat_job(sesi, site.id, JobType.collect_events)
    return tangani_collect_events(sesi, job, klien_berurutan(balasan, diminta if diminta is not None else []))


def test_menyimpan_ketiga_jenis(sesi, site, monkeypatch):
    monkeypatch.setattr("wpmgr.geoip.negara", lambda ip: {"203.0.113.9": "ID", "198.51.100.7": "SG"}.get(ip))
    hasil = jalankan(sesi, site, [PAYLOAD])
    assert hasil == {"errors": 1, "logins": 1, "login_gagal": 1, "halaman": 1}
    e = sesi.query(CatatanError).one()
    assert (e.tingkat, e.komponen_slug, e.jumlah) == ("fatal", "elementor", 3)
    assert e.terakhir_terlihat == WAKTU_T + timedelta(seconds=60)
    assert sesi.query(KejadianLogin).one().negara == "ID"
    g = sesi.query(LoginGagal).one()
    assert (g.jumlah, g.negara, g.jalur) == (40, "SG", "xmlrpc")
    sesi.refresh(site)
    assert site.events_kursor == "e=10:1;l=10:5;g=10:1"


def test_mengambil_ulang_payload_sama_idempoten(sesi, site):
    jalankan(sesi, site, [PAYLOAD])
    jalankan(sesi, site, [PAYLOAD])
    assert sesi.query(CatatanError).one().jumlah == 3
    assert sesi.query(KejadianLogin).count() == 1
    assert sesi.query(LoginGagal).one().jumlah == 40


def test_nilai_baru_dari_site_menimpa(sesi, site):
    jalankan(sesi, site, [PAYLOAD])
    kedua = copy.deepcopy(PAYLOAD)
    kedua["errors"][0]["jumlah"] = 9
    kedua["login_gagal"][0]["jumlah"] = 55
    jalankan(sesi, site, [kedua])
    assert sesi.query(CatatanError).one().jumlah == 9
    assert sesi.query(LoginGagal).one().jumlah == 55


def test_kursor_dikirim_pada_pengambilan_berikutnya(sesi, site):
    jalankan(sesi, site, [PAYLOAD])
    diminta = []
    jalankan(sesi, site, [{**PAYLOAD, "errors": [], "logins": [], "login_gagal": []}], diminta)
    assert diminta[0].url.params["kursor"] == "e=10:1;l=10:5;g=10:1"


def test_paging_berhenti_di_sepuluh_halaman(sesi, site):
    diminta = []
    hasil = jalankan(sesi, site, [{**PAYLOAD, "lagi": True}], diminta)
    assert hasil["halaman"] == 10
    assert len(diminta) == 10


def test_baris_rusak_dilewati(sesi, site):
    rusak = {
        "errors": [{"tingkat": "fatal"}, {**PAYLOAD["errors"][0], "pertama": "bukan-angka"}],
        "logins": [{"id": "x"}], "login_gagal": [{"jam": T}], "kursor": "e=1:1;l=0:0;g=0:0",
        "lagi": False,
    }
    hasil = jalankan(sesi, site, [rusak])
    assert hasil == {"errors": 0, "logins": 0, "login_gagal": 0, "halaman": 1}
    sesi.refresh(site)
    assert site.events_kursor == "e=1:1;l=0:0;g=0:0"


def _update_sukses(sesi, site, tipe, slug, selesai):
    job = buat_job(sesi, site.id, JobType.update_package,
                   {"tipe": tipe, "slug": slug, "dari_versi": "3.18", "ke_versi": "3.20"})
    job.status = JobStatus.success
    job.finished_at = selesai
    job.hasil = {"versi_sebelum": "3.18", "versi_sesudah": "3.20"}
    sesi.commit()
    return job


def test_error_baru_setelah_update_plugin_dikaitkan(sesi, site):
    job = _update_sukses(sesi, site, "plugin", "elementor/elementor.php", WAKTU_T - timedelta(minutes=30))
    jalankan(sesi, site, [PAYLOAD])
    e = sesi.query(CatatanError).one()
    assert e.setelah_update["job_id"] == job.id
    assert e.setelah_update["versi_sesudah"] == "3.20"
    assert e.setelah_update["slug"] == "elementor/elementor.php"


@pytest.mark.parametrize("selisih", [timedelta(minutes=61), timedelta(minutes=-5)])
def test_update_di_luar_jendela_tidak_dikaitkan(sesi, site, selisih):
    _update_sukses(sesi, site, "plugin", "elementor/elementor.php", WAKTU_T - selisih)
    jalankan(sesi, site, [PAYLOAD])
    assert sesi.query(CatatanError).one().setelah_update is None


def test_plugin_lain_tidak_dikaitkan(sesi, site):
    _update_sukses(sesi, site, "plugin", "elementor-pro/elementor-pro.php", WAKTU_T - timedelta(minutes=10))
    jalankan(sesi, site, [PAYLOAD])
    assert sesi.query(CatatanError).one().setelah_update is None


def test_kaitan_hanya_dihitung_saat_sisipan_pertama(sesi, site):
    jalankan(sesi, site, [PAYLOAD])
    _update_sukses(sesi, site, "plugin", "elementor/elementor.php", WAKTU_T - timedelta(minutes=10))
    jalankan(sesi, site, [PAYLOAD])
    assert sesi.query(CatatanError).one().setelah_update is None


def test_status_selesai_dipertahankan_saat_upsert(sesi, site):
    jalankan(sesi, site, [PAYLOAD])
    e = sesi.query(CatatanError).one()
    e.ditandai_selesai_pada = WAKTU_T + timedelta(hours=1)
    sesi.commit()
    jalankan(sesi, site, [PAYLOAD])
    sesi.refresh(e)
    assert e.ditandai_selesai_pada == WAKTU_T + timedelta(hours=1)


def test_enqueue_monitoring_hanya_site_aktif_berfitur(sesi, site, engine, monkeypatch):
    from wpmgr import db
    from wpmgr.cli import enqueue_monitoring
    from wpmgr.models import Site

    monkeypatch.setattr(db, "SessionLocal",
                        sessionmaker(bind=engine, expire_on_commit=False, future=True))
    site.fitur = ["events"]
    sesi.add_all([
        Site(nama="Tanpa", url="https://tanpa.test", status=SiteStatus.active,
             secret_terenkripsi=b"x", fitur=["self_update"]),
        Site(nama="Mati", url="https://mati.test", status=SiteStatus.unreachable,
             secret_terenkripsi=b"x", fitur=["events"]),
    ])
    sesi.commit()

    assert enqueue_monitoring() == 1
    assert enqueue_monitoring() == 0  # tidak menggandakan yang masih tertunda
    job = sesi.query(Job).filter_by(tipe=JobType.collect_events).one()
    assert job.site_id == site.id
    assert job.max_attempts == 1
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: `ImportError: cannot import name 'tangani_collect_events'`.

- [ ] **Step 3: Implementasikan.** Tambahkan ke `src/wpmgr/jobs/monitoring.py` (perluas impor di puncak berkas sesuai kebutuhan):

```python
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from wpmgr import geoip
from wpmgr.models import CatatanError, JobStatus, KejadianLogin, LoginGagal

BATAS_HALAMAN_EVENTS = 10
JENDELA_KAITAN_UPDATE = timedelta(minutes=60)
TINGKAT_SAH = frozenset({"fatal", "warning", "database"})
KOMPONEN_SAH = frozenset({"plugin", "mu-plugin", "theme", "core", "lainnya"})
JENIS_LOGIN_SAH = frozenset({"berhasil", "admin_baru", "jadi_admin"})


def _waktu(detik) -> datetime:
    return datetime.fromtimestamp(int(detik), tz=timezone.utc)


def _teks(nilai, panjang: int) -> str | None:
    if nilai is None:
        return None
    return str(nilai)[:panjang]


def _daftar(data: dict, kunci: str) -> list:
    nilai = data.get(kunci)
    return nilai if isinstance(nilai, list) else []


def kaitkan_update(sesi: Session, site_id, komponen_tipe: str, slug: str | None,
                   pertama: datetime) -> dict | None:
    """Update sukses atas komponen ini dalam 60 menit sebelum error pertama muncul."""
    if komponen_tipe not in ("plugin", "theme") or not slug:
        return None
    kandidat = sesi.scalars(
        select(Job)
        .where(
            Job.site_id == site_id,
            Job.tipe == JobType.update_package,
            Job.status == JobStatus.success,
            Job.finished_at >= pertama - JENDELA_KAITAN_UPDATE,
            Job.finished_at <= pertama,
        )
        .order_by(Job.finished_at.desc())
    ).all()
    for job in kandidat:
        p = job.payload or {}
        if p.get("tipe") != komponen_tipe:
            continue
        slug_job = str(p.get("slug", ""))
        # Error menyebut direktori plugin ("elementor"); job menyebut berkas
        # utamanya ("elementor/elementor.php"). Tema memakai nama direktori
        # di kedua sisi.
        if komponen_tipe == "plugin":
            cocok = slug_job == slug or slug_job.startswith(slug + "/")
        else:
            cocok = slug_job == slug
        if cocok:
            hasil = job.hasil or {}
            return {
                "slug": slug_job,
                "versi_sebelum": hasil.get("versi_sebelum") or p.get("dari_versi"),
                "versi_sesudah": hasil.get("versi_sesudah") or p.get("ke_versi"),
                "job_id": job.id,
                "waktu": job.finished_at.isoformat(),
            }
    return None


def simpan_errors(sesi: Session, site: Site, baris: list) -> int:
    n = 0
    for b in baris:
        try:
            sidik = _teks(b["sidik_jari"], 64)
            tingkat = b["tingkat"]
            if not sidik or tingkat not in TINGKAT_SAH:
                continue
            komponen = b.get("komponen_tipe") if b.get("komponen_tipe") in KOMPONEN_SAH else "lainnya"
            slug = _teks(b.get("komponen_slug"), 191) or None
            nilai = {
                "tingkat": tingkat,
                "komponen_tipe": komponen,
                "komponen_slug": slug,
                "pesan": _teks(b.get("pesan"), 2000) or "",
                "file": _teks(b.get("file"), 255),
                "baris": int(b["baris"]) if b.get("baris") is not None else None,
                "konteks": b.get("konteks") if isinstance(b.get("konteks"), dict) else None,
                "jumlah": int(b.get("jumlah") or 1),
                "pertama_terlihat": _waktu(b["pertama"]),
                "terakhir_terlihat": _waktu(b["terakhir"]),
            }
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue

        sudah_ada = sesi.scalar(
            select(CatatanError.id).where(
                CatatanError.site_id == site.id, CatatanError.sidik_jari == sidik
            )
        ) is not None
        # ditandai_selesai_pada dan setelah_update milik dashboard: tidak ada
        # di `nilai`, sehingga upsert tidak pernah menimpanya.
        sesi.execute(
            insert(CatatanError)
            .values(site_id=site.id, sidik_jari=sidik, **nilai)
            .on_conflict_do_update(constraint="uq_site_errors_sidik", set_=nilai)
        )
        if not sudah_ada:
            kaitan = kaitkan_update(sesi, site.id, komponen, slug, nilai["pertama_terlihat"])
            if kaitan is not None:
                sesi.execute(
                    update(CatatanError)
                    .where(CatatanError.site_id == site.id, CatatanError.sidik_jari == sidik)
                    .values(setelah_update=kaitan)
                )
        n += 1
    return n


def simpan_logins(sesi: Session, site: Site, baris: list) -> int:
    n = 0
    for b in baris:
        try:
            nilai = {
                "id_di_site": int(b["id"]),
                "waktu": _waktu(b["waktu"]),
                "jenis": str(b["jenis"]),
                "username": _teks(b.get("username"), 60) or "",
                "role": _teks(b.get("role"), 60),
                "ip": _teks(b.get("ip"), 45),
                "lewat_cloudflare": bool(b.get("lewat_cloudflare")),
                "user_agent": _teks(b.get("user_agent"), 255),
                "jalur": _teks(b.get("jalur"), 20),
            }
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        if nilai["jenis"] not in JENIS_LOGIN_SAH:
            continue
        nilai["negara"] = geoip.negara(nilai["ip"])
        sesi.execute(
            insert(KejadianLogin)
            .values(site_id=site.id, **nilai)
            .on_conflict_do_nothing(constraint="uq_login_events_id_site")
        )
        n += 1
    return n


def simpan_login_gagal(sesi: Session, site: Site, baris: list) -> int:
    n = 0
    for b in baris:
        try:
            nilai = {
                "jam": _waktu(b["jam"]),
                "ip": _teks(b.get("ip"), 45) or "",
                "username": _teks(b.get("username"), 60) or "",
                "jalur": _teks(b.get("jalur"), 20) or "form",
                "jumlah": int(b["jumlah"]),
                "user_agent": _teks(b.get("user_agent"), 255),
            }
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        nilai["negara"] = geoip.negara(nilai["ip"]) if nilai["ip"] else None
        # jumlah dari site sudah kumulatif per (jam, ip, username, jalur):
        # ditimpa, bukan ditambah, supaya pengambilan ulang tidak menggandakan.
        sesi.execute(
            insert(LoginGagal)
            .values(site_id=site.id, **nilai)
            .on_conflict_do_update(
                constraint="uq_login_gagal_kunci",
                set_={"jumlah": nilai["jumlah"], "user_agent": nilai["user_agent"],
                      "negara": nilai["negara"]},
            )
        )
        n += 1
    return n


def tangani_collect_events(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    total = {"errors": 0, "logins": 0, "login_gagal": 0, "halaman": 0}
    kursor = site.events_kursor
    for _ in range(BATAS_HALAMAN_EVENTS):
        data = klien.events(kursor)
        total["errors"] += simpan_errors(sesi, site, _daftar(data, "errors"))
        total["logins"] += simpan_logins(sesi, site, _daftar(data, "logins"))
        total["login_gagal"] += simpan_login_gagal(sesi, site, _daftar(data, "login_gagal"))
        total["halaman"] += 1
        baru = data.get("kursor")
        if isinstance(baru, str) and baru:
            kursor = baru[:200]
        # Kursor disimpan per halaman: bila halaman berikutnya gagal, yang
        # sudah tersimpan tidak diambil ulang dari awal.
        site.events_kursor = kursor
        site.last_seen_at = datetime.now(timezone.utc)
        sesi.commit()
        if not data.get("lagi"):
            break
    return total
```

Tambahkan `JobType.collect_events: tangani_collect_events` ke `HANDLER` di `handlers.py` (impor dari `wpmgr.jobs.monitoring`).

- [ ] **Step 4: Perintah `enqueue-monitoring`.** Di `cli.py` (impor `from wpmgr.fitur import EVENTS`, `from wpmgr.jobs.queue import antrekan_jika_belum`, `JobType`):

```python
def enqueue_monitoring() -> int:
    dibuat = 0
    with get_session() as sesi:
        sites = sesi.scalars(
            select(Site).where(Site.status == SiteStatus.active, Site.fitur.any(EVENTS))
        ).all()
        for site in sites:
            # max_attempts=1: pengambilan berikutnya 15 menit lagi sudah menjadi
            # retry-nya; mengulang lebih cepat hanya menggandakan beban.
            if antrekan_jika_belum(sesi, site.id, JobType.collect_events, max_attempts=1):
                dibuat += 1
    print(f"{dibuat} job collect_events dibuat")
    return dibuat
```

beserta `sub.add_parser("enqueue-monitoring")` dan cabangnya.

- [ ] **Step 5: Jalankan unit dan integrasi.** Expected: semua lulus.

- [ ] **Step 6: Commit.**

```bash
git add src/wpmgr/jobs/monitoring.py src/wpmgr/jobs/handlers.py src/wpmgr/cli.py tests/integration/test_collect_events.py
git commit -m "feat: pengambilan error dan riwayat login lewat job collect_events"
```

### Task 17: Status keamanan dan status turunan error

**Files:**
- Create: `src/wpmgr/keamanan.py`, `tests/unit/test_keamanan_status.py`, `tests/integration/test_keamanan.py`

**Interfaces:**
- Consumes: Task 1 (model), Task 16 (data tersimpan).
- Produces (di `wpmgr.keamanan`):
  - Konstanta `AMBANG_GAGAL_SEBELUM_TEMBUS = 5`, `JENDELA_TEMBUS = timedelta(hours=24)`, `AMBANG_SERANGAN_TOTAL = 50`, `AMBANG_SERANGAN_PER_IP = 20`, `JENDELA_SERANGAN = timedelta(minutes=60)`, `JENDELA_NEGARA = timedelta(days=90)`, `JENDELA_ERROR_BARU = timedelta(hours=24)`.
  - `StatusKeamanan` (`aman`, `diserang`, `perlu_diperiksa`), `StatusError` (`selesai`, `baru`, `masih_terjadi`, `berhenti`).
  - `@dataclass HasilKeamanan(status, alasan: list[str], percobaan_sejam: int, ip_teratas: list[tuple[str, int]])`.
  - `nilai_keamanan(sesi, site, sekarang: datetime) -> HasilKeamanan`.
  - `status_error(e, sekarang) -> StatusError`, `error_menyalakan_chip(e, sekarang) -> bool`.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/unit/test_keamanan_status.py`
```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wpmgr.keamanan import StatusError, error_menyalakan_chip, status_error

SEKARANG = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
JAM = timedelta(hours=1)


def e(pertama, terakhir, selesai=None, tingkat="fatal"):
    return SimpleNamespace(pertama_terlihat=SEKARANG - pertama, terakhir_terlihat=SEKARANG - terakhir,
                           ditandai_selesai_pada=None if selesai is None else SEKARANG - selesai,
                           tingkat=tingkat)


@pytest.mark.parametrize(
    ("err", "harapan"),
    [
        (e(2 * JAM, JAM), StatusError.baru),
        (e(48 * JAM, JAM), StatusError.masih_terjadi),
        (e(72 * JAM, 30 * JAM), StatusError.berhenti),
        (e(72 * JAM, 30 * JAM, selesai=20 * JAM), StatusError.selesai),
        # Muncul lagi setelah ditandai selesai: terbuka kembali.
        (e(72 * JAM, JAM, selesai=20 * JAM), StatusError.masih_terjadi),
    ],
)
def test_status_error(err, harapan):
    assert status_error(err, SEKARANG) == harapan


def test_chip_hanya_untuk_fatal_dan_database_yang_aktif():
    assert error_menyalakan_chip(e(2 * JAM, JAM), SEKARANG)
    assert error_menyalakan_chip(e(2 * JAM, JAM, tingkat="database"), SEKARANG)
    assert not error_menyalakan_chip(e(2 * JAM, JAM, tingkat="warning"), SEKARANG)
    assert not error_menyalakan_chip(e(72 * JAM, 30 * JAM), SEKARANG)
```

File: `tests/integration/test_keamanan.py`
```python
from datetime import datetime, timedelta, timezone

import pytest

from wpmgr.keamanan import StatusKeamanan, nilai_keamanan
from wpmgr.models import KejadianLogin, LoginGagal

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 22, 12, 30, tzinfo=timezone.utc)
JAM_INI = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
_id = iter(range(1, 10_000))


def login(sesi, site, waktu, jenis="berhasil", username="admin", ip="203.0.113.9",
          negara=None, jalur="form"):
    sesi.add(KejadianLogin(site_id=site.id, id_di_site=next(_id), waktu=waktu, jenis=jenis,
                           username=username, ip=ip, negara=negara, jalur=jalur))
    sesi.commit()


def gagal(sesi, site, jam, jumlah, ip="198.51.100.7", username="admin"):
    sesi.add(LoginGagal(site_id=site.id, jam=jam, ip=ip, username=username, jalur="form",
                        jumlah=jumlah))
    sesi.commit()


def test_site_tanpa_kejadian_aman(sesi, site):
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_admin_baru_perlu_diperiksa_sampai_ditandai(sesi, site):
    login(sesi, site, SEKARANG - timedelta(hours=3), jenis="admin_baru", username="backdoor")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.perlu_diperiksa
    assert "backdoor" in hasil.alasan[0]

    site.keamanan_diperiksa_pada = SEKARANG - timedelta(hours=1)
    sesi.commit()
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_brute_force_yang_tembus(sesi, site):
    gagal(sesi, site, JAM_INI - timedelta(hours=2), 6, ip="203.0.113.9")
    login(sesi, site, SEKARANG - timedelta(minutes=10), ip="203.0.113.9")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.perlu_diperiksa
    assert "203.0.113.9" in hasil.alasan[0]


def test_gagal_di_bawah_ambang_tidak_dianggap_tembus(sesi, site):
    gagal(sesi, site, JAM_INI - timedelta(hours=2), 4, ip="203.0.113.9")
    login(sesi, site, SEKARANG - timedelta(minutes=10), ip="203.0.113.9")
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_login_sso_tidak_pernah_mencurigakan(sesi, site):
    gagal(sesi, site, JAM_INI - timedelta(hours=2), 30, ip="203.0.113.9")
    login(sesi, site, SEKARANG - timedelta(minutes=10), ip="203.0.113.9", jalur="sso")
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_login_dari_negara_baru(sesi, site):
    login(sesi, site, SEKARANG - timedelta(days=10), negara="ID")
    login(sesi, site, SEKARANG - timedelta(minutes=5), negara="RU", ip="192.0.2.5")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.perlu_diperiksa
    assert "RU" in hasil.alasan[0]


def test_login_pertama_kali_tidak_dinilai_negaranya(sesi, site):
    login(sesi, site, SEKARANG - timedelta(minutes=5), negara="RU")
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_negara_sama_aman(sesi, site):
    login(sesi, site, SEKARANG - timedelta(days=10), negara="ID")
    login(sesi, site, SEKARANG - timedelta(minutes=5), negara="ID")
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_riwayat_tanpa_negara_tidak_membuat_negara_baru(sesi, site):
    login(sesi, site, SEKARANG - timedelta(days=10), negara=None)
    login(sesi, site, SEKARANG - timedelta(minutes=5), negara="ID")
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_diserang_karena_total(sesi, site):
    for i in range(5):
        gagal(sesi, site, JAM_INI, 10, ip=f"198.51.100.{i}")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.diserang
    assert hasil.percobaan_sejam == 50


def test_diserang_karena_satu_ip(sesi, site):
    gagal(sesi, site, JAM_INI, 20)
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.diserang
    assert hasil.ip_teratas[0] == ("198.51.100.7", 20)


def test_serangan_lama_tidak_dihitung(sesi, site):
    gagal(sesi, site, JAM_INI - timedelta(hours=2), 500)
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_baris_ip_lain_tidak_dihitung_sebagai_satu_ip(sesi, site):
    gagal(sesi, site, JAM_INI, 30, ip="", username="(lainnya)")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.aman
    assert hasil.ip_teratas[0] == ("(IP lain)", 30)
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: `ModuleNotFoundError: No module named 'wpmgr.keamanan'`.

- [ ] **Step 3: Implementasikan.**

File: `src/wpmgr/keamanan.py`
```python
"""Status keamanan per site dan status turunan error (spec §8.6, §9.4).

Ambang di sini adalah titik awal, bukan setelan UI: disetel ulang di kode
setelah pola serangan nyata di site-site client terlihat.
"""

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wpmgr.models import KejadianLogin, LoginGagal

AMBANG_GAGAL_SEBELUM_TEMBUS = 5
JENDELA_TEMBUS = timedelta(hours=24)
AMBANG_SERANGAN_TOTAL = 50
AMBANG_SERANGAN_PER_IP = 20
JENDELA_SERANGAN = timedelta(minutes=60)
JENDELA_NEGARA = timedelta(days=90)
JENDELA_ERROR_BARU = timedelta(hours=24)
_AWAL = datetime(1970, 1, 1, tzinfo=timezone.utc)


class StatusKeamanan(str, enum.Enum):
    aman = "aman"
    diserang = "diserang"
    perlu_diperiksa = "perlu_diperiksa"


class StatusError(str, enum.Enum):
    selesai = "selesai"
    baru = "baru"
    masih_terjadi = "masih_terjadi"
    berhenti = "berhenti"


@dataclass
class HasilKeamanan:
    status: StatusKeamanan
    alasan: list[str] = field(default_factory=list)
    percobaan_sejam: int = 0
    ip_teratas: list[tuple[str, int]] = field(default_factory=list)


def status_error(e, sekarang: datetime) -> StatusError:
    if e.ditandai_selesai_pada is not None and e.terakhir_terlihat <= e.ditandai_selesai_pada:
        return StatusError.selesai
    if e.pertama_terlihat >= sekarang - JENDELA_ERROR_BARU:
        return StatusError.baru
    if e.terakhir_terlihat >= sekarang - JENDELA_ERROR_BARU:
        return StatusError.masih_terjadi
    return StatusError.berhenti


def error_menyalakan_chip(e, sekarang: datetime) -> bool:
    # Warning terlalu sering dan jarang berarti site rusak; tetap terlihat di tab Error.
    return e.tingkat in ("fatal", "database") and status_error(e, sekarang) in (
        StatusError.baru, StatusError.masih_terjadi
    )


def _alasan_kritis(sesi: Session, site, sejak: datetime) -> list[str]:
    alasan: list[str] = []

    admin = sesi.scalars(
        select(KejadianLogin)
        .where(KejadianLogin.site_id == site.id,
               KejadianLogin.jenis.in_(("admin_baru", "jadi_admin")),
               KejadianLogin.waktu > sejak)
        .order_by(KejadianLogin.waktu)
    ).all()
    for k in admin:
        apa = "Administrator baru" if k.jenis == "admin_baru" else "User dinaikkan menjadi administrator"
        alasan.append(f"{apa}: {k.username} ({k.waktu:%Y-%m-%d %H:%M} UTC)")

    berhasil = sesi.scalars(
        select(KejadianLogin)
        .where(KejadianLogin.site_id == site.id,
               KejadianLogin.jenis == "berhasil",
               KejadianLogin.jalur.is_distinct_from("sso"),
               KejadianLogin.waktu > sejak)
        .order_by(KejadianLogin.waktu)
    ).all()
    for k in berhasil:
        if k.ip:
            gagal = sesi.scalar(
                select(func.coalesce(func.sum(LoginGagal.jumlah), 0)).where(
                    LoginGagal.site_id == site.id,
                    LoginGagal.ip == k.ip,
                    LoginGagal.jam >= k.waktu - JENDELA_TEMBUS,
                    LoginGagal.jam <= k.waktu,
                )
            )
            if gagal >= AMBANG_GAGAL_SEBELUM_TEMBUS:
                alasan.append(
                    f"Login berhasil sebagai {k.username} dari {k.ip}, yang sebelumnya "
                    f"gagal {gagal} kali dalam 24 jam"
                )
        if k.negara:
            riwayat = select(func.count()).select_from(KejadianLogin).where(
                KejadianLogin.site_id == site.id,
                KejadianLogin.username == k.username,
                KejadianLogin.jenis == "berhasil",
                KejadianLogin.negara.is_not(None),
                KejadianLogin.waktu < k.waktu,
                KejadianLogin.waktu >= k.waktu - JENDELA_NEGARA,
            )
            pernah = sesi.scalar(riwayat)
            sama = sesi.scalar(riwayat.where(KejadianLogin.negara == k.negara))
            if pernah and not sama:
                alasan.append(f"Login {k.username} dari negara yang belum pernah dipakai: {k.negara}")
    return alasan


def nilai_keamanan(sesi: Session, site, sekarang: datetime) -> HasilKeamanan:
    per_ip = sesi.execute(
        select(LoginGagal.ip, func.sum(LoginGagal.jumlah).label("n"))
        .where(LoginGagal.site_id == site.id, LoginGagal.jam >= sekarang - JENDELA_SERANGAN)
        .group_by(LoginGagal.ip)
        .order_by(func.sum(LoginGagal.jumlah).desc())
    ).all()
    total = int(sum(n for _, n in per_ip))
    teratas = [(ip or "(IP lain)", int(n)) for ip, n in per_ip[:5]]

    # Status merah tidak padam sendiri; hanya tombol "Sudah diperiksa".
    alasan = _alasan_kritis(sesi, site, site.keamanan_diperiksa_pada or _AWAL)
    if alasan:
        return HasilKeamanan(StatusKeamanan.perlu_diperiksa, alasan, total, teratas)

    if total >= AMBANG_SERANGAN_TOTAL or any(ip and n >= AMBANG_SERANGAN_PER_IP for ip, n in per_ip):
        return HasilKeamanan(
            StatusKeamanan.diserang,
            [f"{total} percobaan login gagal dalam 60 menit terakhir"],
            total, teratas,
        )
    return HasilKeamanan(StatusKeamanan.aman, [], total, teratas)
```

- [ ] **Step 4: Jalankan unit dan integrasi.** Expected: semua lulus.

- [ ] **Step 5: Commit.**

```bash
git add src/wpmgr/keamanan.py tests/unit/test_keamanan_status.py tests/integration/test_keamanan.py
git commit -m "feat: status keamanan tiga tingkat dan status turunan error"
```

---

## Fase F — Traffic

### Task 18: Penghitung traffic di connector (beacon, `/hit`, `/traffic`)

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-traffic.php`, `connector/tests/TrafficTest.php`
- Modify: `connector/wp-manager-connector/wp-manager-connector.php`, `includes/class-wpmgr-rest.php`, `includes/class-wpmgr-skema.php`, `connector/tests/SkemaTest.php`, `connector/tests/bootstrap.php`

**Interfaces:**
- Consumes: Task 2 (tabel `wpmgr_traffic`, `wpmgr_pengunjung`), Task 12 (`WPMGR_IP::saat_ini()`).
- Produces:
  - Script inline di `wp_footer` yang memanggil `navigator.sendBeacon(rest_url('wpmgr/v1/hit'), Blob(text/plain))`.
  - `POST /wp-json/wpmgr/v1/hit` publik → selalu `204`, termasuk ketika hit diabaikan (tidak memberi sinyal apa pun kepada pengirim).
  - `GET /wp-json/wpmgr/v1/traffic?dari=YYYY-MM-DD` (HMAC) → `{zona_waktu, dari, hari: [{tanggal, total: {kunjungan, pengunjung}, halaman: {path: n}, asal: {kunci: n}, perangkat: {jenis: n}}]}`. Tanpa `dari`: sejak kemarin menurut zona waktu site. Dimensi yang kosong dikodekan PHP sebagai `[]`, bukan `{}`, dan dashboard harus menerima keduanya.
  - Kunci asal: `langsung:`, `pencarian:<Nama>` (Google, Bing, Yahoo, DuckDuckGo, Yandex, Baidu, Ecosia), `sosial:<Nama>` (Facebook, Instagram, X, LinkedIn, TikTok, YouTube, Pinterest, WhatsApp, Telegram, Threads), `site_lain:<domain>`.
  - Fungsi murni: `urai_body`, `normalisasi_path`, `host_bersih`, `kategori_asal( $referer, $host_site )`, `jenis_perangkat( $ua )`, `adalah_bot( $ua )`, `hash_pengunjung( $garam, $ip, $ua )`, `susun_hari( array $baris )`, `urai_tanggal( $nilai )`.
  - `WPMGR_Skema::fitur( false )` → `array( 'self_update', 'events', 'traffic' )`.

- [ ] **Step 1: Tulis test PHP yang gagal.**

File: `connector/tests/TrafficTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class TrafficTest extends TestCase {

    public function test_urai_body(): void {
        $this->assertSame( array( '/layanan', 'https://google.com/' ),
            WPMGR_Traffic::urai_body( '{"p":"/layanan","r":"https://google.com/"}' ) );
        $this->assertSame( array( '/', '' ), WPMGR_Traffic::urai_body( '{"p":"/"}' ) );
        $this->assertNull( WPMGR_Traffic::urai_body( 'bukan json' ) );
        $this->assertNull( WPMGR_Traffic::urai_body( '{"p":["/"]}' ) );
        $this->assertNull( WPMGR_Traffic::urai_body( '{"p":"/","x":"' . str_repeat( 'a', 3000 ) . '"}' ) );
        $this->assertNull( WPMGR_Traffic::urai_body( null ) );
    }

    public function test_normalisasi_path(): void {
        $this->assertSame( '/layanan/', WPMGR_Traffic::normalisasi_path( '/layanan/?utm_source=x#atas' ) );
        $this->assertSame( '/a/b', WPMGR_Traffic::normalisasi_path( '//a///b' ) );
        $this->assertNull( WPMGR_Traffic::normalisasi_path( 'https://evil.test/' ) );
        $this->assertNull( WPMGR_Traffic::normalisasi_path( '' ) );
        $this->assertSame( 180, strlen( WPMGR_Traffic::normalisasi_path( '/' . str_repeat( 'x', 400 ) ) ) );
    }

    /** @dataProvider kasus_asal */
    public function test_kategori_asal( $referer, $harapan ): void {
        $this->assertSame( $harapan, WPMGR_Traffic::kategori_asal( $referer, 'www.cvmaju.id' ) );
    }

    public function kasus_asal(): array {
        return array(
            array( '', 'langsung:' ),
            array( 'bukan url', 'langsung:' ),
            array( 'https://cvmaju.id/tentang', null ),
            array( 'https://www.cvmaju.id/', null ),
            array( 'https://www.google.co.id/', 'pencarian:Google' ),
            array( 'https://news.google.com/', 'pencarian:Google' ),
            array( 'https://www.bing.com/search?q=x', 'pencarian:Bing' ),
            array( 'https://l.facebook.com/l.php?u=x', 'sosial:Facebook' ),
            array( 'https://m.facebook.com/', 'sosial:Facebook' ),
            array( 'https://t.co/abc', 'sosial:X' ),
            array( 'https://www.instagram.com/', 'sosial:Instagram' ),
            array( 'https://wa.me/62812', 'sosial:WhatsApp' ),
            array( 'https://direktori.example.com/cv', 'site_lain:direktori.example.com' ),
            array( 'https://notgoogle.com/', 'site_lain:notgoogle.com' ),
        );
    }

    public function test_jenis_perangkat(): void {
        $this->assertSame( 'mobile', WPMGR_Traffic::jenis_perangkat( 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Mobile/15E148' ) );
        $this->assertSame( 'mobile', WPMGR_Traffic::jenis_perangkat( 'Mozilla/5.0 (Linux; Android 14; SM-S918B) Mobile Safari/537.36' ) );
        $this->assertSame( 'tablet', WPMGR_Traffic::jenis_perangkat( 'Mozilla/5.0 (Linux; Android 13; SM-X700) Safari/537.36' ) );
        $this->assertSame( 'tablet', WPMGR_Traffic::jenis_perangkat( 'Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X)' ) );
        $this->assertSame( 'desktop', WPMGR_Traffic::jenis_perangkat( 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0' ) );
    }

    public function test_adalah_bot(): void {
        $this->assertTrue( WPMGR_Traffic::adalah_bot( '' ) );
        $this->assertTrue( WPMGR_Traffic::adalah_bot( 'Mozilla/5.0 (compatible; Googlebot/2.1)' ) );
        $this->assertTrue( WPMGR_Traffic::adalah_bot( 'Mozilla/5.0 HeadlessChrome/120.0' ) );
        $this->assertTrue( WPMGR_Traffic::adalah_bot( 'WPManager-Uptime/2.0' ) );
        $this->assertFalse( WPMGR_Traffic::adalah_bot( 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0' ) );
    }

    public function test_hash_pengunjung_bergantung_pada_garam(): void {
        $a = WPMGR_Traffic::hash_pengunjung( 'garam1', '203.0.113.9', 'UA' );
        $this->assertSame( $a, WPMGR_Traffic::hash_pengunjung( 'garam1', '203.0.113.9', 'UA' ) );
        $this->assertNotSame( $a, WPMGR_Traffic::hash_pengunjung( 'garam2', '203.0.113.9', 'UA' ) );
        $this->assertSame( 40, strlen( $a ) );
    }

    public function test_susun_hari(): void {
        $baris = array(
            array( 'tanggal' => '2026-09-22', 'dimensi' => 'total', 'kunci' => '', 'kunjungan' => '5', 'pengunjung' => '3' ),
            array( 'tanggal' => '2026-09-21', 'dimensi' => 'halaman', 'kunci' => '/', 'kunjungan' => '2', 'pengunjung' => '0' ),
            array( 'tanggal' => '2026-09-22', 'dimensi' => 'asal', 'kunci' => 'langsung:', 'kunjungan' => '4', 'pengunjung' => '0' ),
            array( 'tanggal' => '2026-09-22', 'dimensi' => 'aneh', 'kunci' => 'x', 'kunjungan' => '9', 'pengunjung' => '0' ),
        );
        $hari = WPMGR_Traffic::susun_hari( $baris );
        $this->assertSame( '2026-09-21', $hari[0]['tanggal'] );
        $this->assertSame( array( 'kunjungan' => 0, 'pengunjung' => 0 ), $hari[0]['total'] );
        $this->assertSame( array( '/' => 2 ), $hari[0]['halaman'] );
        $this->assertSame( array( 'kunjungan' => 5, 'pengunjung' => 3 ), $hari[1]['total'] );
        $this->assertSame( array( 'langsung:' => 4 ), $hari[1]['asal'] );
    }

    public function test_urai_tanggal(): void {
        $this->assertSame( '2026-09-01', WPMGR_Traffic::urai_tanggal( '2026-09-01' ) );
        $this->assertNull( WPMGR_Traffic::urai_tanggal( '2026-9-1' ) );
        $this->assertNull( WPMGR_Traffic::urai_tanggal( "2026-09-01' OR 1=1" ) );
        $this->assertNull( WPMGR_Traffic::urai_tanggal( null ) );
    }
}
```

Ubah `test_fitur_yang_diumumkan` di `SkemaTest.php` supaya mengharapkan `array( 'self_update', 'events', 'traffic' )` untuk `fitur( false )` (`fitur( true )` tetap `array( 'self_update' )`). Tambahkan `require_once` untuk `class-wpmgr-traffic.php` ke `bootstrap.php`.

- [ ] **Step 2: Jalankan dan pastikan gagal.**

- [ ] **Step 3: Implementasikan.**

File: `connector/wp-manager-connector/includes/class-wpmgr-traffic.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Penghitung kunjungan: script beacon di footer, endpoint /hit publik, dan
 * rangkuman harian untuk /traffic.
 *
 * Dihitung di browser karena halaman yang dilayani plugin cache tidak
 * menjalankan PHP. Tanpa cookie; IP tidak disimpan. Pengunjung unik memakai
 * hash IP+UA bergaram harian yang dibuang setelah hari itu.
 */
class WPMGR_Traffic {

    const BATAS_HIT_PER_PENGUNJUNG = 200;
    const BATAS_PATH_PER_HARI      = 1000;
    const PATH_LAIN                = '(lainnya)';
    const MAKS_BODY                = 2048;
    const PANJANG_KUNCI            = 180;

    const PENCARIAN = array(
        'Google' => array( 'google.' ), 'Bing' => array( 'bing.com' ), 'Yahoo' => array( 'yahoo.' ),
        'DuckDuckGo' => array( 'duckduckgo.com' ), 'Yandex' => array( 'yandex.' ),
        'Baidu' => array( 'baidu.com' ), 'Ecosia' => array( 'ecosia.org' ),
    );
    const SOSIAL = array(
        'Facebook' => array( 'facebook.com', 'fb.com' ), 'Instagram' => array( 'instagram.com' ),
        'X' => array( 'twitter.com', 'x.com', 't.co' ), 'LinkedIn' => array( 'linkedin.com', 'lnkd.in' ),
        'TikTok' => array( 'tiktok.com' ), 'YouTube' => array( 'youtube.com', 'youtu.be' ),
        'Pinterest' => array( 'pinterest.' ), 'WhatsApp' => array( 'whatsapp.com', 'wa.me' ),
        'Telegram' => array( 'telegram.org', 't.me' ), 'Threads' => array( 'threads.net' ),
    );

    // ---- fungsi murni ---------------------------------------------------

    public static function potong( $teks, $n ) {
        $teks = (string) $teks;
        return function_exists( 'mb_substr' ) ? mb_substr( $teks, 0, $n ) : substr( $teks, 0, $n );
    }

    public static function urai_body( $body ) {
        if ( ! is_string( $body ) || strlen( $body ) > self::MAKS_BODY ) {
            return null;
        }
        $d = json_decode( $body, true );
        if ( ! is_array( $d ) || ! isset( $d['p'] ) || ! is_string( $d['p'] ) ) {
            return null;
        }
        return array( $d['p'], ( isset( $d['r'] ) && is_string( $d['r'] ) ) ? $d['r'] : '' );
    }

    public static function normalisasi_path( $p ) {
        $p = preg_replace( '/[?#].*$/s', '', (string) $p );
        $p = preg_replace( '#/{2,}#', '/', $p );
        if ( '' === $p || '/' !== $p[0] ) {
            return null;
        }
        return substr( $p, 0, self::PANJANG_KUNCI );
    }

    public static function host_bersih( $host ) {
        return preg_replace( '/^(www\.|m\.)/', '', strtolower( trim( (string) $host ) ) );
    }

    private static function cocok_domain( $host, $pola ) {
        if ( '.' === substr( $pola, -1 ) ) {
            // "google." cocok dengan google.co.id dan news.google.com.
            return 0 === strpos( $host, $pola ) || false !== strpos( $host, '.' . $pola );
        }
        return $host === $pola || substr( $host, -strlen( '.' . $pola ) ) === '.' . $pola;
    }

    public static function kategori_asal( $referer, $host_site ) {
        $r = trim( (string) $referer );
        $host = '' === $r ? null : parse_url( $r, PHP_URL_HOST );
        if ( ! is_string( $host ) || '' === $host ) {
            return 'langsung:';
        }
        $host = self::host_bersih( $host );
        if ( $host === self::host_bersih( $host_site ) ) {
            return null; // navigasi di dalam site sendiri bukan asal pengunjung
        }
        foreach ( array( 'pencarian' => self::PENCARIAN, 'sosial' => self::SOSIAL ) as $kategori => $daftar ) {
            foreach ( $daftar as $nama => $pola_semua ) {
                foreach ( $pola_semua as $pola ) {
                    if ( self::cocok_domain( $host, $pola ) ) {
                        return $kategori . ':' . $nama;
                    }
                }
            }
        }
        return substr( 'site_lain:' . $host, 0, self::PANJANG_KUNCI );
    }

    public static function jenis_perangkat( $ua ) {
        $ua = (string) $ua;
        if ( preg_match( '/ipad|tablet|kindle|silk|playbook/i', $ua )
            || ( preg_match( '/android/i', $ua ) && ! preg_match( '/mobile/i', $ua ) ) ) {
            return 'tablet';
        }
        if ( preg_match( '/mobi|iphone|ipod|android|blackberry|opera mini|iemobile|windows phone/i', $ua ) ) {
            return 'mobile';
        }
        return 'desktop';
    }

    public static function adalah_bot( $ua ) {
        $ua = trim( (string) $ua );
        if ( '' === $ua ) {
            return true;
        }
        return (bool) preg_match(
            '/bot|crawl|spider|slurp|headless|lighthouse|phantomjs|preview|facebookexternalhit|embedly|curl|wget|python-|go-http|java\/|okhttp|monitor|uptime|wpmanager/i',
            $ua
        );
    }

    public static function hash_pengunjung( $garam, $ip, $ua ) {
        return sha1( $garam . '|' . $ip . '|' . $ua );
    }

    public static function urai_tanggal( $nilai ) {
        return ( is_string( $nilai ) && preg_match( '/^\d{4}-\d{2}-\d{2}$/', $nilai ) ) ? $nilai : null;
    }

    public static function susun_hari( array $baris ) {
        $hari = array();
        foreach ( $baris as $b ) {
            $t = (string) $b['tanggal'];
            if ( ! isset( $hari[ $t ] ) ) {
                $hari[ $t ] = array(
                    'tanggal'   => $t,
                    'total'     => array( 'kunjungan' => 0, 'pengunjung' => 0 ),
                    'halaman'   => array(),
                    'asal'      => array(),
                    'perangkat' => array(),
                );
            }
            if ( 'total' === $b['dimensi'] ) {
                $hari[ $t ]['total'] = array( 'kunjungan' => (int) $b['kunjungan'], 'pengunjung' => (int) $b['pengunjung'] );
            } elseif ( in_array( $b['dimensi'], array( 'halaman', 'asal', 'perangkat' ), true ) ) {
                $hari[ $t ][ $b['dimensi'] ][ (string) $b['kunci'] ] = (int) $b['kunjungan'];
            }
        }
        ksort( $hari );
        return array_values( $hari );
    }

    // ---- WordPress --------------------------------------------------------

    public static function pasang() {
        add_action( 'wp_footer', array( __CLASS__, 'sisipkan_script' ), 100 );
    }

    public static function sisipkan_script() {
        try {
            if ( is_admin() || is_user_logged_in() || is_feed() || is_preview() || ! WPMGR_Settings::terpasang() ) {
                return;
            }
            $url = wp_json_encode( rest_url( 'wpmgr/v1/hit' ) );
            echo "<script>(function(){try{var d=JSON.stringify({p:location.pathname,r:document.referrer});"
                . "navigator.sendBeacon&&navigator.sendBeacon(" . $url . ",new Blob([d],{type:'text/plain'}));}catch(e){}})();</script>\n"; // phpcs:ignore WordPress.Security.EscapeOutput
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }

    public static function tangani_hit( $request ) {
        try {
            self::catat_hit( $request->get_body() );
        } catch ( \Throwable $e ) {
            unset( $e );
        }
        // Selalu 204: pengirim tidak mendapat petunjuk apakah hit-nya dihitung.
        return new WP_REST_Response( null, 204 );
    }

    private static function garam( $tanggal ) {
        $simpan = get_option( 'wpmgr_garam' );
        if ( is_array( $simpan ) && isset( $simpan['tanggal'], $simpan['garam'] ) && $simpan['tanggal'] === $tanggal ) {
            return $simpan['garam'];
        }
        $garam = bin2hex( random_bytes( 32 ) );
        update_option( 'wpmgr_garam', array( 'tanggal' => $tanggal, 'garam' => $garam ), false );
        return $garam;
    }

    private static function path_dengan_batas( $tanggal, $path ) {
        global $wpdb;
        $tabel = $wpdb->prefix . 'wpmgr_traffic';
        $ada   = $wpdb->get_var( $wpdb->prepare(
            "SELECT 1 FROM {$tabel} WHERE tanggal = %s AND dimensi = 'halaman' AND kunci = %s", $tanggal, $path ) );
        if ( null !== $ada ) {
            return $path;
        }
        $kunci_cache = 'wpmgr_jml_path_' . $tanggal;
        $jumlah      = get_transient( $kunci_cache );
        if ( false === $jumlah ) {
            $jumlah = (int) $wpdb->get_var( $wpdb->prepare(
                "SELECT COUNT(*) FROM {$tabel} WHERE tanggal = %s AND dimensi = 'halaman'", $tanggal ) );
        }
        if ( (int) $jumlah >= self::BATAS_PATH_PER_HARI ) {
            return self::PATH_LAIN;
        }
        set_transient( $kunci_cache, (int) $jumlah + 1, DAY_IN_SECONDS );
        return $path;
    }

    private static function catat_hit( $body ) {
        if ( WPMGR_Skema::monitoring_mati() || ! WPMGR_Settings::terpasang() ) {
            return;
        }
        $isi = self::urai_body( $body );
        if ( null === $isi ) {
            return;
        }
        // Autentikasi cookie REST tanpa nonce selalu menganggap user = 0, jadi
        // cookie login diperiksa langsung.
        if ( wp_validate_auth_cookie( '', 'logged_in' ) ) {
            return;
        }
        $ua = isset( $_SERVER['HTTP_USER_AGENT'] ) ? (string) wp_unslash( $_SERVER['HTTP_USER_AGENT'] ) : '';
        if ( self::adalah_bot( $ua ) ) {
            return;
        }
        $path = self::normalisasi_path( $isi[0] );
        if ( null === $path ) {
            return;
        }

        global $wpdb;
        $p       = $wpdb->prefix;
        $tanggal = wp_date( 'Y-m-d' );
        $ip      = WPMGR_IP::saat_ini();
        $hash    = self::hash_pengunjung( self::garam( $tanggal ), (string) $ip['ip'], $ua );
        $lama    = $wpdb->suppress_errors( true );
        try {
            $wpdb->query( $wpdb->prepare(
                "INSERT INTO {$p}wpmgr_pengunjung (tanggal, hash, hit) VALUES (%s, %s, 1)
                 ON DUPLICATE KEY UPDATE hit = hit + 1", $tanggal, $hash ) );
            $baru = 1 === (int) $wpdb->rows_affected;
            if ( ! $baru ) {
                $hit = (int) $wpdb->get_var( $wpdb->prepare(
                    "SELECT hit FROM {$p}wpmgr_pengunjung WHERE tanggal = %s AND hash = %s", $tanggal, $hash ) );
                if ( $hit > self::BATAS_HIT_PER_PENGUNJUNG ) {
                    return;
                }
            }

            $kunci = array(
                array( 'total', '' ),
                array( 'halaman', self::path_dengan_batas( $tanggal, $path ) ),
                array( 'perangkat', self::jenis_perangkat( $ua ) ),
            );
            $asal = self::kategori_asal( $isi[1], (string) wp_parse_url( home_url(), PHP_URL_HOST ) );
            if ( null !== $asal ) {
                $kunci[] = array( 'asal', $asal );
            }
            $tempat = array();
            $arg    = array();
            foreach ( $kunci as $k ) {
                $tempat[] = '(%s, %s, %s, 1, %d)';
                array_push( $arg, $tanggal, $k[0], $k[1], ( 'total' === $k[0] && $baru ) ? 1 : 0 );
            }
            $wpdb->query( $wpdb->prepare(
                "INSERT INTO {$p}wpmgr_traffic (tanggal, dimensi, kunci, kunjungan, pengunjung) VALUES "
                . implode( ', ', $tempat )
                . " ON DUPLICATE KEY UPDATE kunjungan = kunjungan + 1, pengunjung = pengunjung + VALUES(pengunjung)",
                $arg
            ) );
        } finally {
            $wpdb->suppress_errors( $lama );
        }
    }

    public static function kumpulkan( $dari ) {
        global $wpdb;
        $dari = self::urai_tanggal( $dari );
        if ( null === $dari ) {
            $kemarin = new DateTime( 'yesterday', wp_timezone() );
            $dari    = $kemarin->format( 'Y-m-d' );
        }
        $baris = $wpdb->get_results( $wpdb->prepare(
            "SELECT tanggal, dimensi, kunci, kunjungan, pengunjung FROM {$wpdb->prefix}wpmgr_traffic
              WHERE tanggal >= %s ORDER BY tanggal", $dari ), ARRAY_A );
        return array(
            'zona_waktu' => wp_timezone_string(),
            'dari'       => $dari,
            'hari'       => self::susun_hari( is_array( $baris ) ? $baris : array() ),
        );
    }
}
```

- [ ] **Step 4: Route, pemasangan, dan fitur.** Di berkas utama, tambahkan `require_once WPMGR_DIR . 'includes/class-wpmgr-traffic.php';` dan `WPMGR_Traffic::pasang();` di blok `if ( ! WPMGR_Skema::monitoring_mati() )`. Di `WPMGR_REST::daftarkan_route()`:

```php
        // Satu-satunya route tanpa HMAC: dipanggil browser pengunjung. Tidak
        // pernah membaca atau mengembalikan data.
        register_rest_route( self::NS, '/hit', array(
            'methods'             => 'POST',
            'callback'            => array( 'WPMGR_Traffic', 'tangani_hit' ),
            'permission_callback' => '__return_true',
        ) );

        register_rest_route( self::NS, '/traffic', array(
            'methods'             => 'GET',
            'callback'            => array( __CLASS__, 'traffic' ),
            'permission_callback' => $guard,
        ) );
```

```php
    public static function traffic( $request ) {
        return rest_ensure_response( WPMGR_Traffic::kumpulkan( $request->get_param( 'dari' ) ) );
    }
```

Di `WPMGR_Skema::fitur()`, tambahkan `$fitur[] = 'traffic';` di dalam blok `if ( ! $monitoring_mati )`.

- [ ] **Step 5: PHPUnit dan lint PHP 7.4.** Expected: semua lulus.

- [ ] **Step 6: Commit.**

```bash
git add connector
git commit -m "feat(connector): penghitung traffic dengan beacon, endpoint hit, dan rangkuman harian"
```

### Task 19: Job `collect_traffic` dan perintah `enqueue-traffic`

**Files:**
- Modify: `src/wpmgr/jobs/monitoring.py`, `src/wpmgr/jobs/handlers.py`, `src/wpmgr/cli.py`
- Test: `tests/integration/test_collect_traffic.py`

**Interfaces:**
- Consumes: Task 4 (`SiteClient.traffic`, `TRAFFIC`), Task 18 (bentuk respons).
- Produces:
  - `wpmgr.jobs.monitoring.simpan_traffic(sesi, site_id, hari: list, sumber: str) -> int` (jumlah hari yang disimpan; baris hari itu untuk sumber itu diganti seluruhnya). Dipakai ulang oleh GA4 di Task 20.
  - `tangani_collect_traffic(sesi, job, klien) -> dict`; `HANDLER[JobType.collect_traffic]`.
  - CLI `enqueue-traffic` (`wpmgr.cli.enqueue_traffic() -> int`).

- [ ] **Step 1: Tulis test integrasi yang gagal.**

File: `tests/integration/test_collect_traffic.py`
```python
from datetime import date

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from wpmgr.jobs.monitoring import simpan_traffic, tangani_collect_traffic
from wpmgr.jobs.queue import buat_job
from wpmgr.models import Job, JobType, SiteStatus, TrafficHarian, TrafficRincian
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration

HARI = {
    "tanggal": "2026-09-21",
    "total": {"kunjungan": 12, "pengunjung": 7},
    "halaman": {"/": 8, "/layanan": 4},
    "asal": {"langsung:": 5, "pencarian:Google": 6},
    "perangkat": {"mobile": 9, "desktop": 3},
}


def klien(muatan, diminta=None):
    def handler(request):
        if diminta is not None:
            diminta.append(request)
        return httpx.Response(200, json=muatan)

    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_menyimpan_hari_dan_rincian(sesi, site):
    job = buat_job(sesi, site.id, JobType.collect_traffic)
    hasil = tangani_collect_traffic(sesi, job, klien({"zona_waktu": "Asia/Jakarta", "hari": [HARI]}))
    assert hasil == {"hari": 1}
    h = sesi.get(TrafficHarian, (site.id, date(2026, 9, 21), "plugin"))
    assert (h.kunjungan, h.pengunjung) == (12, 7)
    assert sesi.query(TrafficRincian).filter_by(site_id=site.id, dimensi="halaman").count() == 2
    sesi.refresh(site)
    assert site.traffic_diambil_pada is not None


def test_tanpa_dari_dashboard_membiarkan_site_menentukan_kemarin(sesi, site):
    diminta = []
    job = buat_job(sesi, site.id, JobType.collect_traffic)
    tangani_collect_traffic(sesi, job, klien({"hari": []}, diminta))
    assert diminta[0].url.query == b""


def test_pengambilan_ulang_mengganti_bukan_menambah(sesi, site):
    simpan_traffic(sesi, site.id, [HARI], "plugin")
    baru = {**HARI, "total": {"kunjungan": 20, "pengunjung": 9}, "halaman": {"/": 20}}
    simpan_traffic(sesi, site.id, [baru], "plugin")
    assert sesi.get(TrafficHarian, (site.id, date(2026, 9, 21), "plugin")).kunjungan == 20
    halaman = sesi.query(TrafficRincian).filter_by(site_id=site.id, dimensi="halaman").all()
    assert [(r.kunci, r.kunjungan) for r in halaman] == [("/", 20)]


def test_sumber_lain_tidak_tersentuh(sesi, site):
    simpan_traffic(sesi, site.id, [HARI], "ga4")
    simpan_traffic(sesi, site.id, [HARI], "plugin")
    assert sesi.query(TrafficHarian).filter_by(site_id=site.id).count() == 2


def test_dimensi_kosong_berbentuk_list_diterima(sesi, site):
    kosong = {"tanggal": "2026-09-20", "total": {"kunjungan": 0, "pengunjung": 0},
              "halaman": [], "asal": [], "perangkat": []}
    assert simpan_traffic(sesi, site.id, [kosong], "plugin") == 1


def test_hari_rusak_dilewati(sesi, site):
    rusak = [{"tanggal": "bukan-tanggal"}, {"total": {}}, {**HARI, "halaman": {"/": "x"}}]
    assert simpan_traffic(sesi, site.id, rusak, "plugin") == 0


def test_kunci_panjang_yang_bertabrakan_setelah_dipotong_dijumlah(sesi, site):
    panjang = "/" + "a" * 300
    hari = {**HARI, "halaman": {panjang + "1": 2, panjang + "2": 3}}
    simpan_traffic(sesi, site.id, [hari], "plugin")
    baris = sesi.query(TrafficRincian).filter_by(site_id=site.id, dimensi="halaman").one()
    assert baris.kunjungan == 5


def test_enqueue_traffic_hanya_site_aktif_berfitur(sesi, site, engine, monkeypatch):
    from wpmgr import db
    from wpmgr.cli import enqueue_traffic
    from wpmgr.models import Site

    monkeypatch.setattr(db, "SessionLocal",
                        sessionmaker(bind=engine, expire_on_commit=False, future=True))
    site.fitur = ["traffic"]
    sesi.add(Site(nama="Tanpa", url="https://tanpa.test", status=SiteStatus.active,
                  secret_terenkripsi=b"x", fitur=["events"]))
    sesi.commit()
    assert enqueue_traffic() == 1
    assert enqueue_traffic() == 0
    assert sesi.query(Job).filter_by(tipe=JobType.collect_traffic).one().max_attempts == 1
```

- [ ] **Step 2: Jalankan dan pastikan gagal.**

- [ ] **Step 3: Implementasikan.** Tambahkan ke `src/wpmgr/jobs/monitoring.py` (tambahkan `date` ke impor datetime, `delete` ke impor sqlalchemy, dan `TrafficHarian`, `TrafficRincian` ke impor model):

```python
DIMENSI_RINCIAN = ("halaman", "asal", "perangkat")


def simpan_traffic(sesi: Session, site_id, hari: list, sumber: str) -> int:
    n = 0
    for h in hari:
        try:
            tanggal = date.fromisoformat(str(h["tanggal"])[:10])
            total = h["total"]
            kunjungan, pengunjung = int(total["kunjungan"]), int(total["pengunjung"])
            rincian: dict[tuple[str, str], int] = {}
            for dimensi in DIMENSI_RINCIAN:
                isi = h.get(dimensi)
                if not isinstance(isi, dict):
                    continue  # PHP mengodekan array kosong sebagai [], bukan {}
                for kunci, nilai in isi.items():
                    k = (dimensi, str(kunci)[:200])
                    rincian[k] = rincian.get(k, 0) + int(nilai)
        except (KeyError, TypeError, ValueError):
            continue

        # Diganti utuh per (tanggal, sumber): angka dari site sudah akumulatif
        # untuk hari itu, dan hari ini masih terus bertambah sampai berganti.
        sesi.execute(delete(TrafficRincian).where(
            TrafficRincian.site_id == site_id, TrafficRincian.tanggal == tanggal,
            TrafficRincian.sumber == sumber))
        sesi.execute(delete(TrafficHarian).where(
            TrafficHarian.site_id == site_id, TrafficHarian.tanggal == tanggal,
            TrafficHarian.sumber == sumber))
        sesi.add(TrafficHarian(site_id=site_id, tanggal=tanggal, sumber=sumber,
                               kunjungan=kunjungan, pengunjung=pengunjung))
        sesi.add_all(
            TrafficRincian(site_id=site_id, tanggal=tanggal, sumber=sumber,
                           dimensi=dimensi, kunci=kunci, kunjungan=nilai)
            for (dimensi, kunci), nilai in rincian.items()
        )
        n += 1
    sesi.commit()
    return n


def tangani_collect_traffic(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    data = klien.traffic()
    n = simpan_traffic(sesi, site.id, _daftar(data, "hari"), "plugin")
    site.traffic_diambil_pada = datetime.now(timezone.utc)
    sesi.commit()
    return {"hari": n}
```

Tambahkan `JobType.collect_traffic: tangani_collect_traffic` ke `HANDLER`. Di `cli.py` (impor `TRAFFIC`):

```python
def enqueue_traffic() -> int:
    dibuat = 0
    with get_session() as sesi:
        sites = sesi.scalars(
            select(Site).where(Site.status == SiteStatus.active, Site.fitur.any(TRAFFIC))
        ).all()
        for site in sites:
            if antrekan_jika_belum(sesi, site.id, JobType.collect_traffic, max_attempts=1):
                dibuat += 1
    print(f"{dibuat} job collect_traffic dibuat")
    return dibuat
```

beserta `sub.add_parser("enqueue-traffic")` dan cabangnya.

- [ ] **Step 4: Jalankan unit dan integrasi.** Expected: semua lulus.

- [ ] **Step 5: Commit.**

```bash
git add src/wpmgr/jobs/monitoring.py src/wpmgr/jobs/handlers.py src/wpmgr/cli.py tests/integration/test_collect_traffic.py
git commit -m "feat: pengambilan traffic plugin lewat job collect_traffic"
```

### Task 20: Google Analytics 4 dan deteksi anomali traffic

**Files:**
- Create: `src/wpmgr/traffic.py`, `tests/unit/test_traffic_ga4.py`, `tests/integration/test_ga4_simpan.py`
- Modify: `src/wpmgr/cli.py`

**Interfaces:**
- Consumes: Task 19 (`simpan_traffic`), Task 9 (`kunci_advisory`, `KUNCI_GA4`).
- Produces (di `wpmgr.traffic`):
  - `POLA_PROPERTY = re.compile(r"^\d{6,12}$")`, `PETA_CHANNEL`, `MIN_MEDIAN_ANOMALI = 20`, `HARI_RIWAYAT_ANOMALI = 14`.
  - `class GalatGA4(Exception)` dengan atribut `jenis` (`"akses" | "kuota" | "lain"`) dan `pesan`.
  - `token_ga4(jalur_kredensial: str) -> str`.
  - `ambil_ga4(http, token, property_id, dari: date, sampai: date) -> list[dict]`, dengan bentuk hari sama seperti `/traffic` plugin.
  - `kumpulkan_ga4(sesi, jalur_kredensial: str, hari_ini: date, http=None, token_fn=token_ga4) -> dict` → `{"berhasil": int, "gagal": int}`.
  - `nilai_anomali(kemarin: int | None, riwayat: list[int]) -> str | None` → `"anjlok" | "melonjak" | None`.
  - `anomali_site(sesi, site_id, hari_ini: date) -> str | None`.
  - CLI `collect-ga4`.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/unit/test_traffic_ga4.py`
```python
import json
from datetime import date

import httpx
import pytest

from wpmgr.traffic import GalatGA4, ambil_ga4, nilai_anomali


def balasan_ga(request):
    body = json.loads(request.content)
    dimensi = [d["name"] for d in body["dimensions"]]
    assert request.headers["authorization"] == "Bearer tkn"
    assert body["dateRanges"] == [{"startDate": "2026-09-19", "endDate": "2026-09-21"}]
    if dimensi == ["date"]:
        rows = [{"dimensionValues": [{"value": "20260921"}],
                 "metricValues": [{"value": "120"}, {"value": "80"}]}]
    elif dimensi == ["date", "pagePath"]:
        rows = [{"dimensionValues": [{"value": "20260921"}, {"value": f"/p{i}"}],
                 "metricValues": [{"value": str(100 - i)}]} for i in range(60)]
    elif dimensi == ["date", "sessionDefaultChannelGroup"]:
        rows = [{"dimensionValues": [{"value": "20260921"}, {"value": "Organic Search"}],
                 "metricValues": [{"value": "30"}]},
                {"dimensionValues": [{"value": "20260921"}, {"value": "Email"}],
                 "metricValues": [{"value": "2"}]}]
    else:
        rows = [{"dimensionValues": [{"value": "20260921"}, {"value": "mobile"}],
                 "metricValues": [{"value": "90"}]}]
    return httpx.Response(200, json={"rows": rows})


def ambil(handler):
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        return ambil_ga4(http, "tkn", "123456789", date(2026, 9, 19), date(2026, 9, 21))


def test_pemetaan_ke_bentuk_traffic_plugin():
    hari = ambil(balasan_ga)
    assert len(hari) == 1
    h = hari[0]
    assert h["tanggal"] == "2026-09-21"
    assert h["total"] == {"kunjungan": 120, "pengunjung": 80}
    assert len(h["halaman"]) == 50
    assert h["halaman"]["/p0"] == 100
    assert "/p59" not in h["halaman"]
    assert h["asal"] == {"pencarian:Organic Search": 30, "lainnya:Email": 2}
    assert h["perangkat"] == {"mobile": 90}


def test_403_menjadi_galat_akses():
    with pytest.raises(GalatGA4) as exc:
        ambil(lambda r: httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}}))
    assert exc.value.jenis == "akses"
    assert "Viewer" in exc.value.pesan


def test_kuota_habis():
    with pytest.raises(GalatGA4) as exc:
        ambil(lambda r: httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}))
    assert exc.value.jenis == "kuota"


def test_galat_lain_membawa_status():
    with pytest.raises(GalatGA4) as exc:
        ambil(lambda r: httpx.Response(400, text="Invalid property"))
    assert exc.value.jenis == "lain"
    assert "400" in exc.value.pesan


@pytest.mark.parametrize(
    ("kemarin", "riwayat", "harapan"),
    [
        (100, [100] * 14, None),
        (40, [100] * 14, "anjlok"),
        (401, [100] * 14, "melonjak"),
        (400, [100] * 14, None),
        (0, [10] * 14, None),          # site sepi: terlalu acak untuk dinilai
        (10, [100] * 6, None),         # riwayat kurang dari 7 hari
        (None, [100] * 14, None),      # data kemarin tidak ada
    ],
)
def test_nilai_anomali(kemarin, riwayat, harapan):
    assert nilai_anomali(kemarin, riwayat) == harapan
```

File: `tests/integration/test_ga4_simpan.py`
```python
from datetime import date, timedelta

import httpx
import pytest

from tests.unit.test_traffic_ga4 import balasan_ga
from wpmgr.models import TrafficHarian
from wpmgr.traffic import anomali_site, kumpulkan_ga4

pytestmark = pytest.mark.integration

HARI_INI = date(2026, 9, 22)


def test_ga4_disimpan_dengan_sumber_ga4(sesi, site):
    site.ga4_property_id = "123456789"
    sesi.commit()
    with httpx.Client(transport=httpx.MockTransport(balasan_ga)) as http:
        hasil = kumpulkan_ga4(sesi, "kredensial.json", HARI_INI, http=http, token_fn=lambda p: "tkn")
    assert hasil == {"berhasil": 1, "gagal": 0}
    h = sesi.get(TrafficHarian, (site.id, date(2026, 9, 21), "ga4"))
    assert h.kunjungan == 120
    sesi.refresh(site)
    assert site.ga4_error is None and site.ga4_diambil_pada is not None


def test_akses_ditolak_dicatat_di_site(sesi, site):
    site.ga4_property_id = "123456789"
    sesi.commit()
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as http:
        hasil = kumpulkan_ga4(sesi, "k.json", HARI_INI, http=http, token_fn=lambda p: "tkn")
    assert hasil == {"berhasil": 0, "gagal": 1}
    sesi.refresh(site)
    assert "Viewer" in site.ga4_error


def test_kredensial_rusak_dicatat_di_semua_site_ga4(sesi, site):
    site.ga4_property_id = "123456789"
    sesi.commit()

    def token_gagal(jalur):
        raise ValueError("file kredensial tidak valid")

    hasil = kumpulkan_ga4(sesi, "k.json", HARI_INI, token_fn=token_gagal)
    assert hasil == {"berhasil": 0, "gagal": 1}
    sesi.refresh(site)
    assert site.ga4_error.startswith("Kredensial GA4 tidak dapat dipakai")


def test_site_tanpa_property_dilewati(sesi, site):
    dipanggil = []
    hasil = kumpulkan_ga4(sesi, "k.json", HARI_INI, token_fn=lambda p: dipanggil.append(p) or "t")
    assert hasil == {"berhasil": 0, "gagal": 0}
    assert dipanggil == []


def _isi(sesi, site, sumber, nilai_per_hari):
    for mundur, n in nilai_per_hari.items():
        sesi.add(TrafficHarian(site_id=site.id, tanggal=HARI_INI - timedelta(days=mundur),
                               sumber=sumber, kunjungan=n, pengunjung=n))
    sesi.commit()


def test_anomali_memakai_plugin_lebih_dulu(sesi, site):
    _isi(sesi, site, "plugin", {1: 30, **{i: 100 for i in range(2, 16)}})
    _isi(sesi, site, "ga4", {1: 100, **{i: 100 for i in range(2, 16)}})
    assert anomali_site(sesi, site.id, HARI_INI) == "anjlok"


def test_anomali_jatuh_ke_ga4_bila_plugin_kosong(sesi, site):
    _isi(sesi, site, "ga4", {1: 900, **{i: 100 for i in range(2, 16)}})
    assert anomali_site(sesi, site.id, HARI_INI) == "melonjak"


def test_tanpa_data_tanpa_anomali(sesi, site):
    assert anomali_site(sesi, site.id, HARI_INI) is None
```

(Bila impor `tests.unit.test_traffic_ga4` tidak dapat diselesaikan karena `tests/` bukan paket, salin fungsi `balasan_ga` ke berkas integrasi ini. Jangan menambahkan `__init__.py` hanya demi impor ini.)

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: `ModuleNotFoundError: No module named 'wpmgr.traffic'`.

- [ ] **Step 3: Implementasikan.**

File: `src/wpmgr/traffic.py`
```python
"""Google Analytics 4 (sumber traffic kedua) dan deteksi anomali traffic.

GA4 disimpan di tabel yang sama dengan traffic plugin, dengan sumber 'ga4'.
Keduanya tidak pernah dijumlah: angkanya memang tidak akan sama.
"""

import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from wpmgr.jobs.monitoring import simpan_traffic
from wpmgr.models import Site, SiteStatus, TrafficHarian

URL_RUN_REPORT = "https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"
CAKUPAN = "https://www.googleapis.com/auth/analytics.readonly"
POLA_PROPERTY = re.compile(r"^\d{6,12}$")
HALAMAN_TERATAS_GA4 = 50
MIN_MEDIAN_ANOMALI = 20
HARI_RIWAYAT_ANOMALI = 14
MIN_HARI_RIWAYAT = 7
PETA_CHANNEL = {
    "Organic Search": "pencarian",
    "Paid Search": "pencarian",
    "Organic Social": "sosial",
    "Paid Social": "sosial",
    "Direct": "langsung",
    "Referral": "site_lain",
}


class GalatGA4(Exception):
    def __init__(self, jenis: str, pesan: str) -> None:
        super().__init__(pesan)
        self.jenis = jenis
        self.pesan = pesan


def token_ga4(jalur_kredensial: str) -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    kredensial = service_account.Credentials.from_service_account_file(
        jalur_kredensial, scopes=[CAKUPAN]
    )
    kredensial.refresh(Request())
    return kredensial.token


def _laporan(http: httpx.Client, token: str, property_id: str, dari: date, sampai: date,
             dimensi: list[str], metrik: list[str]) -> list[tuple[list[str], list[str]]]:
    body = {
        "dateRanges": [{"startDate": dari.isoformat(), "endDate": sampai.isoformat()}],
        "dimensions": [{"name": d} for d in dimensi],
        "metrics": [{"name": m} for m in metrik],
        "limit": 10000,
    }
    r = http.post(URL_RUN_REPORT.format(pid=property_id), json=body,
                  headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 403:
        raise GalatGA4("akses", "Service account belum ditambahkan sebagai Viewer di property ini")
    if r.status_code == 429 or "RESOURCE_EXHAUSTED" in r.text:
        raise GalatGA4("kuota", "Kuota GA4 habis; dicoba lagi besok")
    if r.status_code >= 400:
        raise GalatGA4("lain", f"GA4 membalas HTTP {r.status_code}: {r.text[:300]}")
    return [
        ([v.get("value", "") for v in row.get("dimensionValues", [])],
         [v.get("value", "0") for v in row.get("metricValues", [])])
        for row in r.json().get("rows", [])
    ]


def _tanggal(teks: str) -> str:
    return f"{teks[:4]}-{teks[4:6]}-{teks[6:8]}"


def ambil_ga4(http: httpx.Client, token: str, property_id: str, dari: date, sampai: date) -> list[dict]:
    hari: dict[str, dict] = {}

    def untuk(t: str) -> dict:
        return hari.setdefault(t, {"tanggal": t, "total": {"kunjungan": 0, "pengunjung": 0},
                                   "halaman": {}, "asal": {}, "perangkat": {}})

    args = (http, token, property_id, dari, sampai)
    for (tgl,), (tampilan, pengguna) in _laporan(*args, ["date"], ["screenPageViews", "totalUsers"]):
        untuk(_tanggal(tgl))["total"] = {"kunjungan": int(tampilan), "pengunjung": int(pengguna)}

    per_hari: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (tgl, path), (tampilan,) in _laporan(*args, ["date", "pagePath"], ["screenPageViews"]):
        per_hari[_tanggal(tgl)].append((path, int(tampilan)))
    for t, daftar in per_hari.items():
        for path, n in sorted(daftar, key=lambda x: -x[1])[:HALAMAN_TERATAS_GA4]:
            untuk(t)["halaman"][path[:180]] = n

    for (tgl, channel), (sesi_ga,) in _laporan(*args, ["date", "sessionDefaultChannelGroup"], ["sessions"]):
        kunci = f"{PETA_CHANNEL.get(channel, 'lainnya')}:{channel}"
        asal = untuk(_tanggal(tgl))["asal"]
        asal[kunci] = asal.get(kunci, 0) + int(sesi_ga)

    for (tgl, perangkat), (tampilan,) in _laporan(*args, ["date", "deviceCategory"], ["screenPageViews"]):
        untuk(_tanggal(tgl))["perangkat"][perangkat.lower()] = int(tampilan)

    return [hari[t] for t in sorted(hari)]


def kumpulkan_ga4(sesi: Session, jalur_kredensial: str, hari_ini: date,
                  http: httpx.Client | None = None, token_fn=token_ga4) -> dict:
    sites = sesi.scalars(
        select(Site).where(Site.ga4_property_id.is_not(None), Site.status != SiteStatus.disabled)
    ).all()
    hasil = {"berhasil": 0, "gagal": 0}
    if not sites:
        return hasil
    try:
        token = token_fn(jalur_kredensial)
    except Exception as exc:  # noqa: BLE001 -- apa pun penyebabnya, tampilkan ke operator
        for site in sites:
            site.ga4_error = f"Kredensial GA4 tidak dapat dipakai: {exc}"[:500]
        sesi.commit()
        hasil["gagal"] = len(sites)
        return hasil

    # GA4 memfinalkan data dalam 24-48 jam; tiga hari terakhir diambil ulang.
    dari, sampai = hari_ini - timedelta(days=3), hari_ini - timedelta(days=1)
    milik_sendiri = http is None
    http = http or httpx.Client(timeout=60)
    try:
        for site in sites:
            try:
                hari = ambil_ga4(http, token, site.ga4_property_id, dari, sampai)
            except GalatGA4 as exc:
                site.ga4_error = exc.pesan
                hasil["gagal"] += 1
                continue
            except httpx.HTTPError as exc:
                site.ga4_error = f"GA4 tidak dapat dihubungi: {exc}"[:500]
                hasil["gagal"] += 1
                continue
            simpan_traffic(sesi, site.id, hari, "ga4")
            site.ga4_error = None
            site.ga4_diambil_pada = datetime.now(timezone.utc)
            hasil["berhasil"] += 1
        sesi.commit()
    finally:
        if milik_sendiri:
            http.close()
    return hasil


def nilai_anomali(kemarin: int | None, riwayat: list[int]) -> str | None:
    if kemarin is None or len(riwayat) < MIN_HARI_RIWAYAT:
        return None
    median = statistics.median(riwayat)
    if median < MIN_MEDIAN_ANOMALI:
        return None
    if kemarin < 0.5 * median:
        return "anjlok"
    if kemarin > 4 * median:
        return "melonjak"
    return None


def anomali_site(sesi: Session, site_id, hari_ini: date) -> str | None:
    kemarin = hari_ini - timedelta(days=1)
    for sumber in ("plugin", "ga4"):
        baris = dict(sesi.execute(
            select(TrafficHarian.tanggal, TrafficHarian.kunjungan).where(
                TrafficHarian.site_id == site_id,
                TrafficHarian.sumber == sumber,
                TrafficHarian.tanggal >= kemarin - timedelta(days=HARI_RIWAYAT_ANOMALI),
                TrafficHarian.tanggal <= kemarin,
            )
        ).all())
        if not baris:
            continue
        # Hari tanpa baris dianggap tidak ada data, bukan nol kunjungan:
        # connector yang baru dipasang tidak boleh terbaca sebagai "melonjak".
        riwayat = [n for t, n in baris.items() if t < kemarin]
        return nilai_anomali(baris.get(kemarin), riwayat)
    return None
```

- [ ] **Step 4: Perintah CLI.** Di `cli.py` (impor `KUNCI_GA4`, `from wpmgr.traffic import kumpulkan_ga4`):

```python
def collect_ga4() -> dict | None:
    jalur = get_settings().ga4_credentials
    if not jalur:
        print("GA4 tidak dikonfigurasi (WPMGR_GA4_CREDENTIALS kosong); dilewati")
        return None
    with kunci_advisory(db.engine, KUNCI_GA4) as dapat:
        if not dapat:
            print("Pengambilan GA4 lain masih berjalan; dilewati")
            return None
        with get_session() as sesi:
            hasil = kumpulkan_ga4(sesi, jalur, date.today())
    print(f"GA4: {hasil['berhasil']} site berhasil, {hasil['gagal']} gagal")
    return hasil
```

beserta `sub.add_parser("collect-ga4")` dan cabangnya.

- [ ] **Step 5: Jalankan unit dan integrasi.** Expected: semua lulus.

- [ ] **Step 6: Commit.**

```bash
git add src/wpmgr/traffic.py src/wpmgr/cli.py tests/unit/test_traffic_ga4.py tests/integration/test_ga4_simpan.py
git commit -m "feat: pengambilan Google Analytics 4 dan deteksi anomali traffic"
```

---

## Fase G — Antarmuka

Aturan untuk seluruh fase ini, tambahan terhadap Global Constraints:

- Data dari site ditampilkan lewat `x-text` (Alpine) atau autoescape Jinja. `cellTemplate` DataGrid **wajib** melewatkan setiap nilai dinamis lewat `esc()`.
- **Jangan pernah menyisipkan nilai yang berasal dari site ke dalam ekspresi Alpine** (`x-data="f('{{ nilai }}')"`). Autoescape Jinja mengubah `'` menjadi `&#39;`, tetapi browser mengembalikannya ke `'` sebelum Alpine mengevaluasi ekspresi, sehingga nilai itu bisa keluar dari string. Hanya UUID buatan server dan nilai dari daftar putih tetap yang boleh masuk ekspresi. Nilai lain dibaca dari `data-*` lewat `$el.dataset` atau diambil dari API.

### Task 21: Halaman Kesehatan

**Files:**
- Create: `src/wpmgr/kesehatan.py`, `src/wpmgr/web/routes_monitoring.py`, `src/wpmgr/templates/kesehatan.html`, `src/wpmgr/static/app/kesehatan.js`, `tests/integration/test_kesehatan.py`
- Modify: `src/wpmgr/uptime.py`, `src/wpmgr/web/app.py`, `src/wpmgr/web/routes_pages.py`, `src/wpmgr/templates/base.html`, `src/wpmgr/static/app/app.css`, `tests/integration/test_pages.py`

**Interfaces:**
- Consumes: Task 9 (uptime), Task 10 (`ssl_bermasalah`, `sisa_hari_ssl`), Task 17 (`nilai_keamanan`, `error_menyalakan_chip`), Task 20 (`anomali_site`), Task 5 (`baca_manifest`), Task 4 (`lebih_lama`).
- Produces:
  - `wpmgr.uptime.persen_uptime_per_site(sesi, sejak: datetime) -> dict[uuid.UUID, float | None]`.
  - `wpmgr.kesehatan`: `URUTAN_CHIP: list[str]`, `TINGKAT_MASALAH: dict[str, int]`, `TAB_MASALAH: dict[str, str]`, `susun_kesehatan(sesi, sekarang: datetime | None = None) -> dict` → `{"baris": [...], "chip": {kunci: int}, "dibuat_pada": iso}`.
  - Setiap baris: `id, nama, url, tingkat (1-4), masalah: list[str], tab, uptime_status, uptime_persen_24j, uptime_sejak, keamanan, percobaan_sejam, error_baru, error_setelah_update, traffic_kemarin, anomali, ssl_sisa_hari, ssl_error, koneksi, connector_version`.
  - Router `wpmgr.web.routes_monitoring.router` dengan `GET /api/kesehatan`.
  - Halaman `/` = Kesehatan; `/updates` = halaman "Semua Update" Lapis 1.

Kunci masalah dan tingkatnya:

| Kunci | Tingkat | Tab tujuan | Kondisi |
|---|---|---|---|
| `mati` | 1 | uptime | `uptime_status == mati` |
| `perlu_diperiksa` | 1 | login | status keamanan merah |
| `diserang` | 2 | login | status keamanan kuning |
| `error_baru` | 2 | error | ada error fatal/database yang Baru atau Masih terjadi |
| `ssl` | 2 | uptime | `ssl_bermasalah()` |
| `koneksi` | 2 | ringkasan | status Lapis 1 selain `active` |
| `penangkap_terbatas` | 2 | ringkasan | `mode_penangkap == "terbatas"` |
| `traffic_anjlok` | 3 | traffic | anomali `anjlok` |
| `traffic_melonjak` | 3 | traffic | anomali `melonjak` |
| `connector_usang` | 3 | ringkasan | versi connector lebih lama dari paket di dashboard, atau connector tanpa `fitur` |

Site `disabled` tidak ditampilkan. Tab tujuan sebuah baris adalah tab milik masalah bertingkat terkecil; bila setara, dipilih yang lebih awal di `URUTAN_CHIP`. Tab `traffic` baru ada setelah Task 23; sampai saat itu tautannya membuka tab Ringkasan (fallback tab tidak dikenal, Task 22).

- [ ] **Step 1: Tulis test integrasi yang gagal.**

File: `tests/integration/test_kesehatan.py`
```python
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from wpmgr.connector_paket import bangun_paket, sumber_bawaan
from wpmgr.kesehatan import susun_kesehatan
from wpmgr.models import (
    CatatanError,
    KejadianLogin,
    Site,
    SiteStatus,
    TrafficHarian,
    UptimeCheck,
    UptimeHasil,
    UptimePutaran,
    UptimeStatus,
)

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)


def buat(sesi, nama, **kolom):
    s = Site(id=uuid.uuid4(), nama=nama, url=f"https://{nama.lower()}.test",
             status=kolom.pop("status", SiteStatus.active), secret_terenkripsi=b"x",
             fitur=kolom.pop("fitur", ["self_update", "events", "traffic"]),
             connector_version=kolom.pop("connector_version", "2.0.0"), **kolom)
    sesi.add(s)
    sesi.commit()
    return s


def baris(hasil, site):
    return next(b for b in hasil["baris"] if b["id"] == str(site.id))


def test_urutan_keparahan_dan_chip(sesi):
    sehat = buat(sesi, "Sehat")
    mati = buat(sesi, "Mati", uptime_status=UptimeStatus.mati)
    serang = buat(sesi, "Diserang")
    sesi.add(KejadianLogin(site_id=serang.id, id_di_site=1, waktu=SEKARANG - timedelta(hours=1),
                           jenis="admin_baru", username="x"))
    sesi.commit()

    hasil = susun_kesehatan(sesi, SEKARANG)
    assert [b["nama"] for b in hasil["baris"]] == ["Diserang", "Mati", "Sehat"]
    assert baris(hasil, mati)["masalah"] == ["mati"]
    assert baris(hasil, mati)["tab"] == "uptime"
    assert baris(hasil, serang)["keamanan"] == "perlu_diperiksa"
    assert baris(hasil, serang)["tab"] == "login"
    assert baris(hasil, sehat)["tingkat"] == 4
    assert hasil["chip"]["mati"] == 1
    assert hasil["chip"]["perlu_diperiksa"] == 1
    assert hasil["chip"]["error_baru"] == 0


def test_error_baru_setelah_update(sesi):
    s = buat(sesi, "Err")
    sesi.add(CatatanError(site_id=s.id, sidik_jari="a" * 32, tingkat="fatal", komponen_tipe="plugin",
                          komponen_slug="elementor", pesan="x", jumlah=2,
                          pertama_terlihat=SEKARANG - timedelta(hours=2),
                          terakhir_terlihat=SEKARANG - timedelta(hours=1),
                          setelah_update={"slug": "elementor/elementor.php", "versi_sesudah": "3.20"}))
    sesi.add(CatatanError(site_id=s.id, sidik_jari="b" * 32, tingkat="warning", komponen_tipe="core",
                          pesan="w", jumlah=50, pertama_terlihat=SEKARANG - timedelta(hours=2),
                          terakhir_terlihat=SEKARANG - timedelta(hours=1)))
    sesi.commit()
    b = baris(susun_kesehatan(sesi, SEKARANG), s)
    assert b["error_baru"] == 1
    assert b["error_setelah_update"] is True
    assert b["tab"] == "error"


def test_ssl_koneksi_dan_penangkap(sesi):
    s = buat(sesi, "Ssl", ssl_kedaluwarsa=SEKARANG + timedelta(days=5),
             status=SiteStatus.unreachable, mode_penangkap="terbatas")
    b = baris(susun_kesehatan(sesi, SEKARANG), s)
    assert set(b["masalah"]) == {"ssl", "koneksi", "penangkap_terbatas"}
    assert b["ssl_sisa_hari"] == 5


def test_connector_usang(sesi, var_sementara):
    manifest = bangun_paket(sumber_bawaan(), var_sementara / "connector")
    lama = buat(sesi, "Lama", connector_version="1.0.0", fitur=[])
    kini = buat(sesi, "Kini", connector_version=manifest["versi"])
    hasil = susun_kesehatan(sesi, SEKARANG)
    assert "connector_usang" in baris(hasil, lama)["masalah"]
    assert "connector_usang" not in baris(hasil, kini)["masalah"]


def test_persen_uptime_24_jam_mengabaikan_gangguan_dan_blokir(sesi):
    s = buat(sesi, "Up")
    normal = UptimePutaran(mulai=SEKARANG, jumlah_site=1, jumlah_gagal=0, gangguan_dashboard=False)
    ganggu = UptimePutaran(mulai=SEKARANG, jumlah_site=1, jumlah_gagal=1, gangguan_dashboard=True)
    sesi.add_all([normal, ganggu])
    sesi.flush()
    for i, hasil in enumerate([UptimeHasil.naik] * 3 + [UptimeHasil.gagal, UptimeHasil.terblokir]):
        sesi.add(UptimeCheck(putaran_id=normal.id, site_id=s.id, hasil=hasil,
                             dicek_pada=SEKARANG - timedelta(minutes=5 * i)))
    sesi.add(UptimeCheck(putaran_id=ganggu.id, site_id=s.id, hasil=UptimeHasil.gagal,
                         dicek_pada=SEKARANG - timedelta(minutes=1)))
    sesi.commit()
    assert baris(susun_kesehatan(sesi, SEKARANG), s)["uptime_persen_24j"] == 75.0


def test_traffic_kemarin_dan_anomali(sesi):
    s = buat(sesi, "Trf")
    kemarin = date(2026, 9, 21)
    for i in range(1, 15):
        sesi.add(TrafficHarian(site_id=s.id, tanggal=kemarin - timedelta(days=i), sumber="plugin",
                               kunjungan=100, pengunjung=50))
    sesi.add(TrafficHarian(site_id=s.id, tanggal=kemarin, sumber="plugin", kunjungan=20, pengunjung=10))
    sesi.commit()
    b = baris(susun_kesehatan(sesi, SEKARANG), s)
    assert b["traffic_kemarin"] == 20
    assert b["anomali"] == "anjlok"
    assert "traffic_anjlok" in b["masalah"]


def test_site_disabled_tidak_ditampilkan(sesi):
    buat(sesi, "Mati", status=SiteStatus.disabled)
    assert susun_kesehatan(sesi, SEKARANG)["baris"] == []


def test_api_kesehatan(klien_web, sesi):
    buat(sesi, "Api")
    r = klien_web.get("/api/kesehatan")
    assert r.status_code == 200
    assert r.json()["baris"][0]["nama"] == "Api"


def test_api_kesehatan_butuh_login(engine):
    from fastapi.testclient import TestClient

    from wpmgr.web.app import buat_app

    c = TestClient(buat_app(), base_url="https://testserver")
    assert c.get("/api/kesehatan").status_code == 401


def test_beranda_adalah_kesehatan_dan_update_pindah(klien_web):
    assert "layarKesehatan()" in klien_web.get("/").text
    r = klien_web.get("/updates")
    assert r.status_code == 200
    assert "layarUpdate()" in r.text
```

Di `tests/integration/test_pages.py`, ubah parametrisasi `test_halaman_terbuka_dan_memuat_datagrid` menjadi `["/", "/updates", "/sites", "/sites/new", "/activity"]`, dan ubah `test_beranda_menyajikan_halaman_update_bukan_placeholder` supaya meminta `/updates` (ganti namanya menjadi `test_halaman_update_menyajikan_layar_update`).

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: `ModuleNotFoundError: No module named 'wpmgr.kesehatan'`.

- [ ] **Step 3: Persentase uptime per site.** Tambahkan ke `src/wpmgr/uptime.py` (tambahkan `func` ke impor sqlalchemy):

```python
def persen_uptime_per_site(sesi: Session, sejak: datetime) -> dict:
    """Persen cek `naik` per site, tanpa putaran gangguan dashboard dan tanpa hasil terblokir."""
    naik = func.count().filter(UptimeCheck.hasil == UptimeHasil.naik)
    gagal = func.count().filter(UptimeCheck.hasil == UptimeHasil.gagal)
    baris = sesi.execute(
        select(UptimeCheck.site_id, naik, gagal)
        .join(UptimePutaran, UptimePutaran.id == UptimeCheck.putaran_id)
        .where(UptimeCheck.dicek_pada >= sejak, UptimePutaran.gangguan_dashboard.is_(False))
        .group_by(UptimeCheck.site_id)
    ).all()
    return {
        site_id: (round(n / (n + g) * 100, 2) if n + g else None)
        for site_id, n, g in baris
    }
```

- [ ] **Step 4: Penyusun Kesehatan.**

File: `src/wpmgr/kesehatan.py`
```python
"""Satu baris per site dan hitungan chip untuk halaman Kesehatan (spec §13.2)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from wpmgr.config import get_settings
from wpmgr.connector_paket import baca_manifest
from wpmgr.keamanan import JENDELA_ERROR_BARU, StatusKeamanan, error_menyalakan_chip, nilai_keamanan
from wpmgr.models import CatatanError, Site, SiteStatus, TrafficHarian, UptimeStatus
from wpmgr.ssl_cek import sisa_hari_ssl, ssl_bermasalah
from wpmgr.traffic import anomali_site
from wpmgr.uptime import persen_uptime_per_site
from wpmgr.versi import lebih_lama

URUTAN_CHIP = [
    "mati", "perlu_diperiksa", "diserang", "error_baru", "ssl", "koneksi",
    "penangkap_terbatas", "traffic_anjlok", "traffic_melonjak", "connector_usang",
]
TINGKAT_MASALAH = {
    "mati": 1, "perlu_diperiksa": 1,
    "diserang": 2, "error_baru": 2, "ssl": 2, "koneksi": 2, "penangkap_terbatas": 2,
    "traffic_anjlok": 3, "traffic_melonjak": 3, "connector_usang": 3,
}
TAB_MASALAH = {
    "mati": "uptime", "perlu_diperiksa": "login", "diserang": "login", "error_baru": "error",
    "ssl": "uptime", "koneksi": "ringkasan", "penangkap_terbatas": "ringkasan",
    "traffic_anjlok": "traffic", "traffic_melonjak": "traffic", "connector_usang": "ringkasan",
}
TINGKAT_SEHAT = 4


def _iso(nilai):
    return nilai.isoformat() if nilai else None


def _error_per_site(sesi: Session, sekarang: datetime) -> dict:
    hasil: dict = {}
    kandidat = sesi.scalars(
        select(CatatanError).where(
            CatatanError.tingkat.in_(("fatal", "database")),
            CatatanError.terakhir_terlihat >= sekarang - JENDELA_ERROR_BARU,
        )
    ).all()
    for e in kandidat:
        if not error_menyalakan_chip(e, sekarang):
            continue
        info = hasil.setdefault(e.site_id, {"baru": 0, "setelah_update": False})
        info["baru"] += 1
        info["setelah_update"] = info["setelah_update"] or e.setelah_update is not None
    return hasil


def _traffic_kemarin(sesi: Session, kemarin) -> dict:
    hasil: dict = {}
    # ga4 dibaca lebih dulu lalu ditimpa plugin: plugin tersedia di semua site
    # dan menjadi angka utama bila keduanya ada.
    for sumber in ("ga4", "plugin"):
        for site_id, n in sesi.execute(
            select(TrafficHarian.site_id, TrafficHarian.kunjungan).where(
                TrafficHarian.tanggal == kemarin, TrafficHarian.sumber == sumber
            )
        ).all():
            hasil[site_id] = n
    return hasil


def susun_kesehatan(sesi: Session, sekarang: datetime | None = None) -> dict:
    sekarang = sekarang or datetime.now(timezone.utc)
    hari_ini = sekarang.date()
    versi_terbaru = (baca_manifest(get_settings().jalur_connector) or {}).get("versi")
    sites = sesi.scalars(
        select(Site).where(Site.status != SiteStatus.disabled).order_by(Site.nama)
    ).all()
    persen = persen_uptime_per_site(sesi, sekarang - timedelta(hours=24))
    errors = _error_per_site(sesi, sekarang)
    traffic = _traffic_kemarin(sesi, hari_ini - timedelta(days=1))

    semua = []
    for site in sites:
        keamanan = nilai_keamanan(sesi, site, sekarang)
        anomali = anomali_site(sesi, site.id, hari_ini)
        info_error = errors.get(site.id, {"baru": 0, "setelah_update": False})

        masalah = []
        if site.uptime_status == UptimeStatus.mati:
            masalah.append("mati")
        if keamanan.status == StatusKeamanan.perlu_diperiksa:
            masalah.append("perlu_diperiksa")
        elif keamanan.status == StatusKeamanan.diserang:
            masalah.append("diserang")
        if info_error["baru"]:
            masalah.append("error_baru")
        if ssl_bermasalah(site, sekarang):
            masalah.append("ssl")
        if site.status != SiteStatus.active:
            masalah.append("koneksi")
        if site.mode_penangkap == "terbatas":
            masalah.append("penangkap_terbatas")
        if anomali == "anjlok":
            masalah.append("traffic_anjlok")
        elif anomali == "melonjak":
            masalah.append("traffic_melonjak")
        if lebih_lama(site.connector_version, versi_terbaru) or (site.connector_version and not site.fitur):
            masalah.append("connector_usang")

        utama = min(masalah, key=lambda m: (TINGKAT_MASALAH[m], URUTAN_CHIP.index(m)), default=None)
        semua.append({
            "id": str(site.id),
            "nama": site.nama,
            "url": site.url,
            "tingkat": TINGKAT_MASALAH[utama] if utama else TINGKAT_SEHAT,
            "masalah": masalah,
            "tab": TAB_MASALAH[utama] if utama else "ringkasan",
            "uptime_status": site.uptime_status.value,
            "uptime_persen_24j": persen.get(site.id),
            "uptime_sejak": _iso(site.uptime_sejak),
            "keamanan": keamanan.status.value,
            "percobaan_sejam": keamanan.percobaan_sejam,
            "error_baru": info_error["baru"],
            "error_setelah_update": info_error["setelah_update"],
            "traffic_kemarin": traffic.get(site.id),
            "anomali": anomali,
            "ssl_sisa_hari": sisa_hari_ssl(site, sekarang),
            "ssl_error": site.ssl_error,
            "koneksi": site.status.value,
            "connector_version": site.connector_version,
        })

    semua.sort(key=lambda b: (b["tingkat"], b["nama"].lower()))
    chip = {k: sum(1 for b in semua if k in b["masalah"]) for k in URUTAN_CHIP}
    return {"baris": semua, "chip": chip, "dibuat_pada": sekarang.isoformat()}
```

- [ ] **Step 5: Router monitoring dan halaman.**

File: `src/wpmgr/web/routes_monitoring.py`
```python
from typing import Annotated

from fastapi import APIRouter, Depends

from wpmgr import db
from wpmgr.kesehatan import susun_kesehatan
from wpmgr.models import User
from wpmgr.web.auth import pengguna_api

router = APIRouter()
PenggunaApi = Annotated[User, Depends(pengguna_api)]


@router.get("/api/kesehatan")
def kesehatan(pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        return susun_kesehatan(sesi)
```

Di `web/app.py`, impor `from wpmgr.web.routes_monitoring import router as monitoring_router` dan daftarkan `app.include_router(monitoring_router)` sebelum `pages_router`. Di `routes_pages.py`, ganti route `/`:

```python
@router.get("/")
def halaman_kesehatan(request: Request, pengguna: PenggunaHalaman):
    return _tpl().TemplateResponse(request, "kesehatan.html", {"pengguna": pengguna})


@router.get("/updates")
def halaman_updates(request: Request, pengguna: PenggunaHalaman):
    return _tpl().TemplateResponse(request, "updates.html", {"pengguna": pengguna})
```

Di `base.html`, ganti baris tautan navigasi:

```html
    <a href="/">Kesehatan</a>
    <a href="/updates">Update</a>
    <a href="/sites">Site</a>
    <a href="/activity">Aktivitas</a>
    <a href="/sites/new">Tambah Site</a>
```

File: `src/wpmgr/templates/kesehatan.html`
```html
{% extends "base.html" %}
{% block judul %}Kesehatan — WP Manager{% endblock %}
{% block isi %}
<div x-data="layarKesehatan()" x-init="mulai()">
  <h1>Kesehatan</h1>
  <p class="redup" x-show="diperbarui"
     x-text="`Diperbarui ${diperbarui} · menyegarkan sendiri setiap 60 detik`"></p>
  <p class="galat" x-show="galat" x-text="galat" role="alert"></p>

  <div class="chips">
    <template x-for="k in urutanChip" :key="k">
      <button type="button" class="chip" x-show="chip[k] > 0"
              :class="[kelasChip(k), filter === k ? 'chip-aktif' : '']"
              @click="pilihChip(k)" x-text="`${chip[k]} ${label(k)}`"></button>
    </template>
    <span class="chip chip-hijau" x-show="semuaSehat()">Semua site sehat</span>
    <button type="button" class="chip" x-show="filter" @click="pilihChip(filter)">Tampilkan semua</button>
  </div>

  <div id="grid" style="height: 70vh"></div>
</div>
<script src="/static/app/kesehatan.js"></script>
{% endblock %}
```

File: `src/wpmgr/static/app/kesehatan.js`
```js
const URUTAN_CHIP = [
  'mati', 'perlu_diperiksa', 'diserang', 'error_baru', 'ssl', 'koneksi',
  'penangkap_terbatas', 'traffic_anjlok', 'traffic_melonjak', 'connector_usang',
];
const LABEL_CHIP = {
  mati: 'mati', perlu_diperiksa: 'perlu diperiksa', diserang: 'diserang',
  error_baru: 'error baru', ssl: 'SSL bermasalah', koneksi: 'koneksi bermasalah',
  penangkap_terbatas: 'penangkap terbatas', traffic_anjlok: 'traffic anjlok',
  traffic_melonjak: 'traffic melonjak', connector_usang: 'connector usang',
};
const TINGKAT_CHIP = {
  mati: 1, perlu_diperiksa: 1, diserang: 2, error_baru: 2, ssl: 2, koneksi: 2,
  penangkap_terbatas: 2, traffic_anjlok: 3, traffic_melonjak: 3, connector_usang: 3,
};
const KELAS_TINGKAT = { 1: 'chip-merah', 2: 'chip-kuning', 3: 'chip-biru' };
const TEKS_UPTIME = { naik: 'Naik', mati: 'Mati', terblokir: 'Terblokir', belum_dicek: 'Belum dicek' };
const KELAS_UPTIME = {
  naik: 'dg-badge-success', mati: 'dg-badge-danger', terblokir: 'dg-badge-warning', belum_dicek: 'dg-badge-muted',
};
const TEKS_KEAMANAN = { aman: 'Aman', diserang: 'Diserang', perlu_diperiksa: 'Perlu diperiksa' };
const KELAS_KEAMANAN = {
  aman: 'dg-badge-success', diserang: 'dg-badge-warning', perlu_diperiksa: 'dg-badge-danger',
};

// Kelas selalu salah satu string tetap dari peta di atas; teks tetap lewat esc().
function lencana(kelas, teks) {
  return `<span class="${kelas}">${esc(teks)}</span>`;
}

function layarKesehatan() {
  return {
    grid: null,
    data: [],
    chip: {},
    filter: null,
    galat: '',
    diperbarui: '',
    urutanChip: URUTAN_CHIP,

    label(k) { return LABEL_CHIP[k]; },
    kelasChip(k) { return KELAS_TINGKAT[TINGKAT_CHIP[k]]; },
    semuaSehat() { return this.data.length > 0 && URUTAN_CHIP.every((k) => !this.chip[k]); },

    mulai() {
      this.muat();
      // Tanpa notifikasi, halaman yang dibiarkan terbuka adalah satu-satunya
      // tempat masalah baru terlihat.
      setInterval(() => this.muat(), 60000);
    },

    async muat() {
      try {
        const r = await fetch('/api/kesehatan');
        if (!r.ok) {
          this.galat = `Data kesehatan tidak dapat dimuat. ${await pesanGalat(r)}`;
          return;
        }
        const d = await r.json();
        this.galat = '';
        this.data = d.baris;
        this.chip = d.chip;
        this.diperbarui = new Date().toLocaleTimeString('id-ID');
        this.terapkan();
      } catch (e) {
        this.galat = `Gagal menghubungi server: ${e.message}`;
      }
    },

    pilihChip(k) {
      this.filter = this.filter === k ? null : k;
      this.terapkan();
    },

    terapkan() {
      const baris = this.filter ? this.data.filter((b) => b.masalah.includes(this.filter)) : this.data;
      if (this.grid) {
        this.grid.setData(baris);
        return;
      }
      this.grid = new DataGrid('#grid', {
        dataSource: baris,
        keyExpr: 'id',
        selection: false,
        columns: [
          {
            dataField: 'nama',
            caption: 'Site',
            cellTemplate: (v, b) => `<a href="/sites/${esc(b.id)}?tab=${esc(b.tab)}">${esc(v)}</a>`,
          },
          {
            dataField: 'uptime_status',
            caption: 'Uptime',
            cellTemplate: (v, b) => lencana(KELAS_UPTIME[v] || '', TEKS_UPTIME[v] || v)
              + (b.uptime_persen_24j == null ? '' : ` ${esc(b.uptime_persen_24j)}%`),
          },
          {
            dataField: 'keamanan',
            caption: 'Keamanan',
            cellTemplate: (v, b) => lencana(KELAS_KEAMANAN[v] || '', TEKS_KEAMANAN[v] || v)
              + (b.percobaan_sejam ? ` ${esc(b.percobaan_sejam)}/jam` : ''),
          },
          {
            dataField: 'error_baru',
            caption: 'Error',
            cellTemplate: (v, b) => (v
              ? lencana('dg-badge-warning', `${v} baru`) + (b.error_setelah_update ? ' · setelah update' : '')
              : '0'),
          },
          {
            dataField: 'traffic_kemarin',
            caption: 'Traffic kemarin',
            cellTemplate: (v, b) => `${v == null ? '—' : esc(v)}`
              + (b.anomali ? ' ' + lencana(b.anomali === 'anjlok' ? 'dg-badge-danger' : 'dg-badge-info', b.anomali) : ''),
          },
          {
            dataField: 'ssl_sisa_hari',
            caption: 'SSL',
            cellTemplate: (v, b) => {
              if (b.ssl_error) return lencana('dg-badge-danger', 'error');
              if (v == null) return '—';
              return v < 14 ? lencana('dg-badge-warning', `${v} hari`) : `${esc(v)} hari`;
            },
          },
          { dataField: 'koneksi', caption: 'Koneksi' },
          {
            dataField: 'connector_version',
            caption: 'Connector',
            cellTemplate: (v, b) => esc(v || '—')
              + (b.masalah.includes('connector_usang') ? ' ' + lencana('dg-badge-info', 'usang') : ''),
          },
        ],
      });
    },
  };
}
```

Tambahkan ke `static/app/app.css`:

```css
.chips{display:flex;gap:.5rem;flex-wrap:wrap;margin:.5rem 0 1rem}
.chip{padding:.35rem .8rem;border-radius:1rem;border:1px solid #ccd;background:#fff;font-weight:600;cursor:pointer}
.chip-merah{background:#fbe9e9;border-color:#d64545;color:#a12a2a}
.chip-kuning{background:#fdf3e1;border-color:#e2a03f;color:#8a5a12}
.chip-biru{background:#e8f0fb;border-color:#5b8def;color:#2a5bb8}
.chip-hijau{background:#e6f5ee;border-color:#22a06b;color:#16704a;cursor:default}
.chip-aktif{outline:2px solid currentColor;outline-offset:1px}
```

- [ ] **Step 6: Jalankan unit dan integrasi.** Expected: semua lulus.

- [ ] **Step 7: Periksa di browser.** Jalankan web lokal (`.venv/Scripts/uvicorn wpmgr.web.app:app --reload`, lihat README), masuk, buka `/`. Periksa bahwa chip tampil, klik chip memfilter grid, dan tautan nama site membuka `/sites/<id>?tab=...`. Tempel ringkasan pengamatan di laporan. Tidak perlu screenshot.

- [ ] **Step 8: Commit.**

```bash
git add src/wpmgr tests/integration/test_kesehatan.py tests/integration/test_pages.py
git commit -m "feat: halaman Kesehatan sebagai halaman utama dashboard"
```

### Task 22: Detail site bertab (Ringkasan, Paket, Uptime, Error, Login, Aktivitas)

**Files:**
- Create: `src/wpmgr/static/app/detail.js`, `tests/integration/test_api_monitoring.py`, `tests/unit/test_template_aman.py`
- Modify: `src/wpmgr/uptime.py`, `src/wpmgr/web/routes_monitoring.py`, `src/wpmgr/web/routes_pages.py`, `src/wpmgr/templates/site_detail.html`, `src/wpmgr/static/app/app.css`

**Interfaces:**
- Consumes: Task 17, 20 (`POLA_PROPERTY`), 21.
- Produces:
  - `wpmgr.uptime.persen_uptime(sesi, site_id, sejak) -> float | None`, `uptime_harian(sesi, site_id, sekarang, hari: int) -> list[dict]` (`{"tanggal", "persen"}`), `rata_waktu_ms(sesi, site_id, sejak) -> int | None`, `teks_durasi(detik: float) -> str`.
  - API: `GET /api/sites/{id}/uptime?hari=30`, `GET /api/sites/{id}/errors`, `POST /api/sites/{id}/errors/{error_id}/selesai`, `GET /api/sites/{id}/logins?hari=30`, `POST /api/sites/{id}/keamanan/diperiksa`, `PUT /api/sites/{id}/ga4` (body `{property_id}`).
  - `routes_pages.TAB_DETAIL: list[tuple[str, str]]` dan konteks `tab`, `lencana`, `bulan_lalu`, `ga4_aktif` untuk `site_detail.html`.
  - Komponen Alpine `detailSite(siteId, tabAwal)`.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/unit/test_template_aman.py`
```python
"""Penjaga struktural: tidak ada jalan pintas yang mem-bypass escaping.

Hampir semua string yang tampil di dashboard berasal dari site klien, dan
site klien bisa saja sudah disusupi.
"""

from pathlib import Path

AKAR = Path(__file__).resolve().parents[2] / "src" / "wpmgr"


def test_template_tanpa_safe_dan_x_html():
    for berkas in (AKAR / "templates").glob("*.html"):
        isi = berkas.read_text(encoding="utf-8")
        assert "|safe" not in isi.replace(" ", ""), berkas.name
        assert "x-html" not in isi, berkas.name


def test_script_aplikasi_tanpa_innerhtml():
    for berkas in (AKAR / "static" / "app").glob("*.js"):
        assert "innerHTML" not in berkas.read_text(encoding="utf-8"), berkas.name
```

File: `tests/integration/test_api_monitoring.py`
```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from wpmgr.models import (
    ActivityLog,
    CatatanError,
    KejadianLogin,
    LoginGagal,
    PackageType,
    Site,
    SitePackage,
    UptimeCheck,
    UptimeHasil,
    UptimeInsiden,
    UptimePutaran,
)

pytestmark = pytest.mark.integration

SEKARANG = datetime.now(timezone.utc)


def test_api_butuh_login_dan_site_ada(klien_web, engine):
    from fastapi.testclient import TestClient

    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), base_url="https://testserver")
    assert anon.get(f"/api/sites/{uuid.uuid4()}/uptime").status_code == 401
    assert klien_web.get(f"/api/sites/{uuid.uuid4()}/uptime").status_code == 404


def test_uptime(klien_web, sesi, site):
    p = UptimePutaran(mulai=SEKARANG, jumlah_site=1, jumlah_gagal=0, gangguan_dashboard=False)
    sesi.add(p)
    sesi.flush()
    for i in range(4):
        sesi.add(UptimeCheck(putaran_id=p.id, site_id=site.id, dicek_pada=SEKARANG - timedelta(minutes=5 * i),
                             hasil=UptimeHasil.naik if i else UptimeHasil.gagal, waktu_ms=200))
    sesi.add(UptimeInsiden(site_id=site.id, mulai=SEKARANG - timedelta(hours=3),
                           selesai=SEKARANG - timedelta(hours=1, minutes=55), penyebab="HTTP 500"))
    sesi.commit()

    d = klien_web.get(f"/api/sites/{site.id}/uptime?hari=30").json()
    assert d["persen"]["24j"] == 75.0
    assert d["rata_ms"] == 200
    assert len(d["harian"]) == 30
    assert d["insiden"][0]["penyebab"] == "HTTP 500"
    assert d["insiden"][0]["durasi"] == "1 jam 5 menit"


def test_errors_dan_tandai_selesai(klien_web, sesi, site):
    sesi.add(SitePackage(site_id=site.id, tipe=PackageType.plugin, slug="elementor/elementor.php",
                         nama="Elementor", versi_terpasang="3.20", last_scan_at=SEKARANG))
    e = CatatanError(site_id=site.id, sidik_jari="a" * 32, tingkat="fatal", komponen_tipe="plugin",
                     komponen_slug="elementor", pesan="<script>alert(1)</script>", file="a.php", baris=3,
                     jumlah=2, pertama_terlihat=SEKARANG - timedelta(hours=1),
                     terakhir_terlihat=SEKARANG - timedelta(minutes=5),
                     setelah_update={"slug": "elementor/elementor.php", "versi_sesudah": "3.20"})
    sesi.add(e)
    sesi.commit()

    daftar = klien_web.get(f"/api/sites/{site.id}/errors").json()
    assert daftar[0]["status"] == "baru"
    assert daftar[0]["komponen_teks"] == "Plugin Elementor"
    assert daftar[0]["setelah_update_teks"] == "muncul setelah update ke v3.20"
    assert daftar[0]["pesan"] == "<script>alert(1)</script>"  # JSON apa adanya; dirender lewat x-text

    r = klien_web.post(f"/api/sites/{site.id}/errors/{e.id}/selesai")
    assert r.status_code == 200
    assert klien_web.get(f"/api/sites/{site.id}/errors").json()[0]["status"] == "selesai"
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Error ditandai selesai%")).count() == 1


def test_tandai_selesai_error_site_lain_404(klien_web, sesi, site):
    lain = Site(id=uuid.uuid4(), nama="L", url="https://l.test", secret_terenkripsi=b"x")
    sesi.add(lain)
    e = CatatanError(site_id=site.id, sidik_jari="b" * 32, tingkat="fatal", komponen_tipe="core",
                     pesan="x", jumlah=1, pertama_terlihat=SEKARANG, terakhir_terlihat=SEKARANG)
    sesi.add(e)
    sesi.commit()
    assert klien_web.post(f"/api/sites/{lain.id}/errors/{e.id}/selesai").status_code == 404


def test_logins_dan_sudah_diperiksa(klien_web, sesi, site):
    sesi.add(KejadianLogin(site_id=site.id, id_di_site=1, waktu=SEKARANG - timedelta(hours=1),
                           jenis="berhasil", username="admin", ip="203.0.113.9", negara="ID",
                           jalur="form", user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/128.0"))
    sesi.add(KejadianLogin(site_id=site.id, id_di_site=2, waktu=SEKARANG - timedelta(minutes=30),
                           jenis="admin_baru", username="baru"))
    jam = SEKARANG.replace(minute=0, second=0, microsecond=0)
    sesi.add(LoginGagal(site_id=site.id, jam=jam, ip="198.51.100.7", username="admin", jalur="xmlrpc",
                        jumlah=15, user_agent="curl/8"))
    sesi.add(LoginGagal(site_id=site.id, jam=jam, ip="198.51.100.7", username="root", jalur="xmlrpc",
                        jumlah=5, user_agent="curl/8"))
    sesi.commit()

    d = klien_web.get(f"/api/sites/{site.id}/logins?hari=30").json()
    assert d["status"] == "perlu_diperiksa"
    assert d["berhasil"][0]["peramban"] == "Chrome"
    assert d["admin"][0]["username"] == "baru"
    assert d["gagal"][0] == {
        "ip": "198.51.100.7", "negara": None, "jumlah": 20, "username": ["admin", "root"],
        "jalur": ["xmlrpc"], "skrip": True,
    }

    assert klien_web.post(f"/api/sites/{site.id}/keamanan/diperiksa").status_code == 200
    d2 = klien_web.get(f"/api/sites/{site.id}/logins?hari=30").json()
    assert d2["status"] == "diserang"   # admin_baru sudah diperiksa; 20 percobaan dari satu IP tersisa


def test_ga4_property(klien_web, sesi, site):
    assert klien_web.put(f"/api/sites/{site.id}/ga4", json={"property_id": "123456789"}).status_code == 200
    sesi.refresh(site)
    assert site.ga4_property_id == "123456789"
    assert klien_web.put(f"/api/sites/{site.id}/ga4", json={"property_id": "G-ABC"}).status_code == 422
    klien_web.put(f"/api/sites/{site.id}/ga4", json={"property_id": ""})
    sesi.refresh(site)
    assert site.ga4_property_id is None


def test_halaman_detail_bertab(klien_web, sesi, site):
    sesi.add(SitePackage(site_id=site.id, tipe=PackageType.plugin, slug="x/x.php",
                         nama="<img src=x onerror=alert(1)>", versi_terpasang="1", last_scan_at=SEKARANG))
    sesi.commit()
    r = klien_web.get(f"/sites/{site.id}?tab=error")
    assert r.status_code == 200
    assert f"detailSite('{site.id}', 'error')" in r.text
    for label in ("Ringkasan", "Paket", "Uptime", "Error", "Login", "Aktivitas"):
        assert label in r.text
    assert "<img src=x" not in r.text
    assert "&lt;img src=x" in r.text


def test_tab_tak_dikenal_jatuh_ke_ringkasan(klien_web, site):
    r = klien_web.get(f"/sites/{site.id}?tab=%27);alert(1);//")
    assert f"detailSite('{site.id}', 'ringkasan')" in r.text
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: `test_template_aman` lulus bila berkas yang ada sudah bersih (catat hasilnya), sedangkan test API gagal dengan 404/405.

- [ ] **Step 3: Fungsi uptime untuk satu site.** Tambahkan ke `src/wpmgr/uptime.py`:

```python
def persen_uptime(sesi: Session, site_id, sejak: datetime) -> float | None:
    return persen_uptime_per_site(sesi, sejak).get(site_id)


def uptime_harian(sesi: Session, site_id, sekarang: datetime, hari: int) -> list[dict]:
    awal = (sekarang - timedelta(days=hari - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    kolom_hari = func.date_trunc("day", UptimeCheck.dicek_pada).label("hari")
    naik = func.count().filter(UptimeCheck.hasil == UptimeHasil.naik)
    gagal = func.count().filter(UptimeCheck.hasil == UptimeHasil.gagal)
    peta = {
        baris.hari.date(): (baris[1], baris[2])
        for baris in sesi.execute(
            select(kolom_hari, naik, gagal)
            .join(UptimePutaran, UptimePutaran.id == UptimeCheck.putaran_id)
            .where(UptimeCheck.site_id == site_id, UptimeCheck.dicek_pada >= awal,
                   UptimePutaran.gangguan_dashboard.is_(False))
            .group_by(kolom_hari)
        ).all()
    }
    hasil = []
    for i in range(hari):
        tanggal = awal.date() + timedelta(days=i)
        n, g = peta.get(tanggal, (0, 0))
        hasil.append({"tanggal": tanggal.isoformat(),
                      "persen": round(n / (n + g) * 100, 2) if n + g else None})
    return hasil


def rata_waktu_ms(sesi: Session, site_id, sejak: datetime) -> int | None:
    nilai = sesi.scalar(
        select(func.avg(UptimeCheck.waktu_ms)).where(
            UptimeCheck.site_id == site_id, UptimeCheck.hasil == UptimeHasil.naik,
            UptimeCheck.dicek_pada >= sejak,
        )
    )
    return int(nilai) if nilai is not None else None


def teks_durasi(detik: float) -> str:
    menit = int(detik // 60)
    jam, menit = divmod(menit, 60)
    hari, jam = divmod(jam, 24)
    bagian = []
    if hari:
        bagian.append(f"{hari} hari")
    if jam:
        bagian.append(f"{jam} jam")
    if menit or not bagian:
        bagian.append(f"{menit} menit")
    return " ".join(bagian)
```

Tambahkan `timedelta` ke impor `datetime` di puncak berkas.

- [ ] **Step 4: API detail.** Tambahkan ke `src/wpmgr/web/routes_monitoring.py`. Perluas impor:

```python
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from wpmgr import db
from wpmgr.keamanan import nilai_keamanan, status_error
from wpmgr.kesehatan import susun_kesehatan
from wpmgr.models import (
    ActivityLog,
    CatatanError,
    KejadianLogin,
    LoginGagal,
    PackageType,
    Site,
    SitePackage,
    UptimeInsiden,
    User,
)
from wpmgr.traffic import POLA_PROPERTY
from wpmgr.uagent import urai_ua
from wpmgr.uptime import persen_uptime, rata_waktu_ms, teks_durasi, uptime_harian
from wpmgr.ssl_cek import sisa_hari_ssl
```

lalu:

```python
TEKS_STATUS_ERROR = {"selesai": "Selesai", "baru": "Baru", "masih_terjadi": "Masih terjadi",
                     "berhenti": "Berhenti"}
TEKS_STATUS_UPTIME = {"naik": "Naik", "mati": "Mati", "terblokir": "Terblokir",
                      "belum_dicek": "Belum dicek"}
TEKS_KOMPONEN = {"plugin": "Plugin", "mu-plugin": "Must-use plugin", "theme": "Tema",
                 "core": "WordPress core", "lainnya": "Lainnya"}


def _sekarang() -> datetime:
    return datetime.now(timezone.utc)


def _iso(nilai):
    return nilai.isoformat() if nilai else None


def _site(sesi, site_id: uuid.UUID) -> Site:
    site = sesi.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site tidak ditemukan")
    return site


@router.get("/api/sites/{site_id}/uptime")
def uptime_site(site_id: uuid.UUID, pengguna: PenggunaApi, hari: int = 30):
    hari = max(1, min(hari, 90))
    sekarang = _sekarang()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        insiden = sesi.scalars(
            select(UptimeInsiden).where(UptimeInsiden.site_id == site.id)
            .order_by(UptimeInsiden.mulai.desc()).limit(50)
        ).all()
        sisa = sisa_hari_ssl(site, sekarang)
        if site.ssl_error:
            ssl_teks = site.ssl_error
        elif sisa is None:
            ssl_teks = "Belum dicek"
        else:
            ssl_teks = f"Berlaku sampai {site.ssl_kedaluwarsa:%Y-%m-%d} ({sisa} hari lagi)"
        return {
            "status": site.uptime_status.value,
            "status_teks": TEKS_STATUS_UPTIME[site.uptime_status.value],
            "sejak": _iso(site.uptime_sejak),
            "persen": {
                "24j": persen_uptime(sesi, site.id, sekarang - timedelta(days=1)),
                "7h": persen_uptime(sesi, site.id, sekarang - timedelta(days=7)),
                "30h": persen_uptime(sesi, site.id, sekarang - timedelta(days=30)),
            },
            "rata_ms": rata_waktu_ms(sesi, site.id, sekarang - timedelta(days=1)),
            "harian": uptime_harian(sesi, site.id, sekarang, hari),
            "insiden": [
                {"id": i.id, "mulai": _iso(i.mulai), "selesai": _iso(i.selesai),
                 "durasi": teks_durasi(((i.selesai or sekarang) - i.mulai).total_seconds()),
                 "penyebab": i.penyebab}
                for i in insiden
            ],
            "ssl": {"sisa_hari": sisa, "error": site.ssl_error, "teks": ssl_teks},
        }


def _nama_komponen(paket: list[SitePackage], tipe: str, slug: str | None) -> str | None:
    if not slug:
        return None
    for p in paket:
        if tipe == "plugin" and p.tipe == PackageType.plugin and (
            p.slug == slug or p.slug.startswith(slug + "/")
        ):
            return p.nama
        if tipe == "theme" and p.tipe == PackageType.theme and p.slug == slug:
            return p.nama
    return None


@router.get("/api/sites/{site_id}/errors")
def errors_site(site_id: uuid.UUID, pengguna: PenggunaApi):
    sekarang = _sekarang()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        paket = sesi.scalars(select(SitePackage).where(SitePackage.site_id == site.id)).all()
        daftar = sesi.scalars(
            select(CatatanError).where(CatatanError.site_id == site.id)
            .order_by(CatatanError.terakhir_terlihat.desc()).limit(500)
        ).all()
        hasil = []
        for e in daftar:
            status = status_error(e, sekarang).value
            nama = _nama_komponen(paket, e.komponen_tipe, e.komponen_slug) or e.komponen_slug
            komponen = TEKS_KOMPONEN.get(e.komponen_tipe, e.komponen_tipe)
            setelah = e.setelah_update or None
            hasil.append({
                "id": e.id,
                "tingkat": e.tingkat,
                "status": status,
                "status_teks": TEKS_STATUS_ERROR[status],
                "komponen_teks": f"{komponen} {nama}" if nama else komponen,
                "pesan": e.pesan,
                "lokasi": f"{e.file}:{e.baris}" if e.file else "—",
                "jumlah": e.jumlah,
                "pertama_terlihat": _iso(e.pertama_terlihat),
                "terakhir_terlihat": _iso(e.terakhir_terlihat),
                "konteks": e.konteks,
                "setelah_update": setelah,
                "setelah_update_teks": (
                    f"muncul setelah update ke v{setelah.get('versi_sesudah')}" if setelah else None
                ),
            })
        return hasil


@router.post("/api/sites/{site_id}/errors/{error_id}/selesai")
def tandai_error_selesai(site_id: uuid.UUID, error_id: int, pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        e = sesi.get(CatatanError, error_id)
        if e is None or e.site_id != site_id:
            raise HTTPException(status_code=404, detail="Error tidak ditemukan")
        e.ditandai_selesai_pada = _sekarang()
        sesi.add(ActivityLog(site_id=site_id, user_id=pengguna.id, level="info",
                             pesan=f"Error ditandai selesai oleh {pengguna.email}",
                             detail={"sidik_jari": e.sidik_jari, "pesan": e.pesan[:200]}))
        sesi.commit()
    return {"ok": True}


@router.get("/api/sites/{site_id}/logins")
def logins_site(site_id: uuid.UUID, pengguna: PenggunaApi, hari: int = 30):
    hari = max(1, min(hari, 90))
    sekarang = _sekarang()
    sejak = sekarang - timedelta(days=hari)
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        keamanan = nilai_keamanan(sesi, site, sekarang)
        kejadian = sesi.scalars(
            select(KejadianLogin).where(KejadianLogin.site_id == site.id, KejadianLogin.waktu >= sejak)
            .order_by(KejadianLogin.waktu.desc()).limit(500)
        ).all()

        def bentuk(k):
            ua = urai_ua(k.user_agent)
            return {"waktu": _iso(k.waktu), "jenis": k.jenis, "username": k.username, "role": k.role,
                    "ip": k.ip, "negara": k.negara, "jalur": k.jalur, "peramban": ua["peramban"],
                    "os": ua["os"], "skrip": ua["skrip"]}

        per_ip: dict = defaultdict(lambda: {"jumlah": 0, "negara": None, "username": defaultdict(int),
                                            "jalur": set(), "skrip": False})
        for g in sesi.scalars(
            select(LoginGagal).where(LoginGagal.site_id == site.id, LoginGagal.jam >= sejak)
        ).all():
            ringkas = per_ip[g.ip or "(IP lain)"]
            ringkas["jumlah"] += g.jumlah
            ringkas["negara"] = ringkas["negara"] or g.negara
            ringkas["username"][g.username] += g.jumlah
            ringkas["jalur"].add(g.jalur)
            ringkas["skrip"] = ringkas["skrip"] or urai_ua(g.user_agent)["skrip"]
        gagal = sorted(
            ({"ip": ip, "negara": r["negara"], "jumlah": r["jumlah"],
              "username": [u for u, _ in sorted(r["username"].items(), key=lambda x: -x[1])[:5]],
              "jalur": sorted(r["jalur"]), "skrip": r["skrip"]}
             for ip, r in per_ip.items()),
            key=lambda b: -b["jumlah"],
        )[:200]

        return {
            "status": keamanan.status.value,
            "alasan": keamanan.alasan,
            "percobaan_sejam": keamanan.percobaan_sejam,
            "diperiksa_pada": _iso(site.keamanan_diperiksa_pada),
            "berhasil": [bentuk(k) for k in kejadian if k.jenis == "berhasil"],
            "admin": [bentuk(k) for k in kejadian if k.jenis != "berhasil"],
            "gagal": gagal,
        }


@router.post("/api/sites/{site_id}/keamanan/diperiksa")
def keamanan_diperiksa(site_id: uuid.UUID, pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        site.keamanan_diperiksa_pada = _sekarang()
        sesi.add(ActivityLog(site_id=site.id, user_id=pengguna.id, level="info",
                             pesan=f"Keamanan ditandai sudah diperiksa oleh {pengguna.email}"))
        sesi.commit()
    return {"ok": True}


class PermintaanGA4(BaseModel):
    property_id: str = ""


@router.put("/api/sites/{site_id}/ga4")
def atur_ga4(site_id: uuid.UUID, req: PermintaanGA4, pengguna: PenggunaApi):
    nilai = req.property_id.strip()
    if nilai and not POLA_PROPERTY.match(nilai):
        raise HTTPException(status_code=422,
                            detail="Property ID GA4 berupa 6 sampai 12 digit angka, bukan Measurement ID (G-...).")
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        site.ga4_property_id = nilai or None
        site.ga4_error = None
        sesi.commit()
    return {"ok": True}
```

(`test_logins_dan_sudah_diperiksa` mengharapkan `"negara": None`, jadi pastikan `geoip` tidak dipanggil di sini; negara diambil dari kolom yang sudah terisi saat penyimpanan.)

- [ ] **Step 5: Halaman detail.** Di `routes_pages.py`, tambahkan impor `from datetime import date, datetime, timedelta, timezone`, `from wpmgr.keamanan import StatusKeamanan, error_menyalakan_chip, nilai_keamanan`, `from wpmgr.models import CatatanError, UptimeStatus`, lalu:

```python
TAB_DETAIL = [
    ("ringkasan", "Ringkasan"), ("paket", "Paket"), ("uptime", "Uptime"),
    ("error", "Error"), ("login", "Login"), ("aktivitas", "Aktivitas"),
]


def _bulan_lalu(hari_ini: date) -> str:
    awal_bulan = hari_ini.replace(day=1)
    return (awal_bulan - timedelta(days=1)).strftime("%Y-%m")
```

Ganti `halaman_detail`:

```python
@router.get("/sites/{site_id}")
def halaman_detail(request: Request, site_id: uuid.UUID, pengguna: PenggunaHalaman, tab: str = "ringkasan"):
    sah = {k for k, _ in TAB_DETAIL}
    # Hanya nilai dari daftar putih yang boleh masuk ke ekspresi Alpine di template.
    tab = tab if tab in sah else "ringkasan"
    sekarang = datetime.now(timezone.utc)
    with db.SessionLocal() as sesi:
        site = sesi.get(Site, site_id)
        if site is None:
            raise HTTPException(status_code=404, detail="Site tidak ditemukan")
        paket = sesi.scalars(
            select(SitePackage).where(SitePackage.site_id == site_id).order_by(SitePackage.nama)
        ).all()
        riwayat = sesi.scalars(
            select(ActivityLog).where(ActivityLog.site_id == site_id)
            .order_by(ActivityLog.dibuat_pada.desc()).limit(100)
        ).all()
        errors = sesi.scalars(select(CatatanError).where(CatatanError.site_id == site_id)).all()
        keamanan = nilai_keamanan(sesi, site, sekarang)
    lencana = {
        "uptime": "!" if site.uptime_status == UptimeStatus.mati else "",
        "error": sum(1 for e in errors if error_menyalakan_chip(e, sekarang)) or "",
        "login": {"perlu_diperiksa": "!", "diserang": "serangan"}.get(keamanan.status.value, ""),
    }
    return _tpl().TemplateResponse(
        request, "site_detail.html",
        {"pengguna": pengguna, "site": site, "paket": paket, "riwayat": riwayat,
         "tab": tab, "tab_detail": TAB_DETAIL, "lencana": lencana,
         "bulan_lalu": _bulan_lalu(sekarang.date()),
         "ga4_aktif": bool(get_settings().ga4_credentials)},
    )
```

Ganti seluruh `site_detail.html`:

```html
{% extends "base.html" %}
{% block judul %}{{ site.nama }} — WP Manager{% endblock %}
{% block isi %}
{# Hanya site.id (UUID buatan server) dan tab (daftar putih di route) yang
   boleh masuk ke ekspresi Alpine. Nilai lain dibaca lewat data-* atau API. #}
<div x-data="detailSite('{{ site.id }}', '{{ tab }}')" x-init="mulai()">
  <h1>{{ site.nama }}</h1>
  <p><a href="{{ site.url }}" target="_blank" rel="noopener">{{ site.url }}</a></p>
  <div class="toolbar">
    <button type="button" @click="sso()">Masuk wp-admin</button>
    <button type="button" @click="scan()">Scan</button>
    <a href="/sites/{{ site.id }}/laporan/{{ bulan_lalu }}" target="_blank" rel="noopener">Laporan bulanan</a>
  </div>
  <p class="galat" x-show="galat" x-text="galat" role="alert"></p>
  <p class="info" x-show="info" x-text="info" role="status"></p>

  <nav class="tab">
    {% for kunci, label in tab_detail %}
    <button type="button" :class="{ aktif: tab === '{{ kunci }}' }" @click="pilih('{{ kunci }}')">
      {{ label }}{% if lencana[kunci] %} <span class="lencana">{{ lencana[kunci] }}</span>{% endif %}
    </button>
    {% endfor %}
  </nav>

  <section x-show="tab === 'ringkasan'">
    <p>Status koneksi: <strong>{{ site.status.value }}</strong> ·
       WP {{ site.wp_version or '—' }} · PHP {{ site.php_version or '—' }} ·
       Connector {{ site.connector_version or '—' }}</p>
    {% if site.last_error %}<p class="galat">{{ site.last_error }}</p>{% endif %}
    <p>Penangkap error: <strong>{{ site.mode_penangkap or 'belum aktif' }}</strong>
      {% if site.mode_penangkap == 'terbatas' %} — folder mu-plugins di site ini tidak dapat ditulisi,
      jadi fatal error dari plugin yang dimuat sebelum connector tidak tertangkap.{% endif %}</p>
    <p>Percayai <code>X-Forwarded-For</code>: <strong>{{ 'ya' if site.percayai_xff else 'tidak' }}</strong>
       (diatur di <em>Pengaturan → WP Manager</em> di wp-admin site ini).</p>

    {% if ga4_aktif %}
    <h3>Google Analytics 4</h3>
    <p>
      <label>Property ID
        <input x-model="ga4" x-init="ga4 = $el.dataset.awal" data-awal="{{ site.ga4_property_id or '' }}"
               inputmode="numeric" placeholder="mis. 412345678">
      </label>
      <button type="button" @click="simpanGa4()">Simpan</button>
    </p>
    {% if site.ga4_error %}<p class="galat">{{ site.ga4_error }}</p>{% endif %}
    {% endif %}

    <h3>Cabut site</h3>
    <div x-data="{ konfirmasi: false, galatCabut: '' }">
      <p>Mencabut site menghapus datanya dari dashboard berikut secret-nya. Plugin
         <code>wp-manager-connector</code> dan user <code>wpmgr</code> di site itu harus dihapus
         manual lewat wp-admin.</p>
      <button type="button" x-show="!konfirmasi" @click="konfirmasi = true">Cabut site ini</button>
      <div x-show="konfirmasi">
        <p><strong>Yakin mencabut {{ site.nama }}?</strong></p>
        <button type="button" @click="
          galatCabut = '';
          fetch('/api/sites/{{ site.id }}', { method: 'DELETE' })
            .then(async (r) => {
              if (r.ok) { window.location = '/sites'; return; }
              galatCabut = 'Site tidak dicabut. ' + await pesanGalat(r);
            })
            .catch((e) => { galatCabut = 'Gagal menghubungi server: ' + e.message; })">Ya, cabut</button>
        <button type="button" @click="konfirmasi = false">Batal</button>
        <p class="galat" x-show="galatCabut" x-text="galatCabut" role="alert"></p>
      </div>
    </div>
  </section>

  <section x-show="tab === 'paket'">
    <table>
      <tr><th>Tipe</th><th>Nama</th><th>Terpasang</th><th>Tersedia</th><th>Aktif</th></tr>
      {% for p in paket %}
      <tr>
        <td>{{ p.tipe.value }}</td><td>{{ p.nama }}</td>
        <td>{{ p.versi_terpasang }}</td><td>{{ p.versi_tersedia or '—' }}</td>
        <td>{{ 'ya' if p.aktif else 'tidak' }}</td>
      </tr>
      {% endfor %}
    </table>
  </section>

  <section x-show="tab === 'uptime'">
    <template x-if="uptime">
      <div>
        <p>Status: <strong x-text="uptime.status_teks"></strong>
           <span x-show="uptime.sejak" x-text="'sejak ' + waktu(uptime.sejak)"></span></p>
        <p>Uptime 24 jam <strong x-text="persen(uptime.persen['24j'])"></strong> ·
           7 hari <strong x-text="persen(uptime.persen['7h'])"></strong> ·
           30 hari <strong x-text="persen(uptime.persen['30h'])"></strong> ·
           waktu respons <span x-text="uptime.rata_ms == null ? '—' : uptime.rata_ms + ' ms'"></span></p>
        <p>SSL: <span x-text="uptime.ssl.teks"></span></p>
        <div class="batang" aria-label="Uptime harian 30 hari">
          <template x-for="h in uptime.harian" :key="h.tanggal">
            <div class="batang-kolom" :title="`${h.tanggal}: ${persen(h.persen)}`">
              <div class="batang-isi" :class="{ 'batang-buruk': h.persen != null && h.persen < 99 }"
                   :style="`height:${h.persen == null ? 0 : Math.max(2, h.persen)}%`"></div>
            </div>
          </template>
        </div>
        <h3>Insiden</h3>
        <p class="redup" x-show="uptime.insiden.length === 0">Belum ada insiden.</p>
        <table x-show="uptime.insiden.length">
          <tr><th>Mulai</th><th>Selesai</th><th>Durasi</th><th>Penyebab</th></tr>
          <template x-for="i in uptime.insiden" :key="i.id">
            <tr>
              <td x-text="waktu(i.mulai)"></td>
              <td x-text="i.selesai ? waktu(i.selesai) : 'masih mati'"></td>
              <td x-text="i.durasi"></td>
              <td x-text="i.penyebab"></td>
            </tr>
          </template>
        </table>
      </div>
    </template>
  </section>

  <section x-show="tab === 'error'">
    <template x-if="errors">
      <div>
        <p class="redup" x-show="errors.length === 0">Belum ada error yang tercatat.</p>
        <table x-show="errors.length">
          <tr><th>Status</th><th>Tingkat</th><th>Komponen</th><th>Pesan</th><th>Lokasi</th>
              <th>Jumlah</th><th>Terakhir</th><th></th></tr>
          <template x-for="e in errors" :key="e.id">
            <tr>
              <td x-text="e.status_teks"></td>
              <td x-text="e.tingkat"></td>
              <td x-text="e.komponen_teks"></td>
              <td>
                <div class="pesan-detail" x-text="e.pesan"></div>
                <div class="galat" x-show="e.setelah_update_teks" x-text="e.setelah_update_teks"></div>
              </td>
              <td x-text="e.lokasi"></td>
              <td x-text="e.jumlah"></td>
              <td x-text="waktu(e.terakhir_terlihat)"></td>
              <td><button type="button" x-show="e.status !== 'selesai'" @click="tandaiSelesai(e.id)">Tandai selesai</button></td>
            </tr>
          </template>
        </table>
      </div>
    </template>
  </section>

  <section x-show="tab === 'login'">
    <template x-if="logins">
      <div>
        <p>Status keamanan: <strong x-text="teksKeamanan(logins.status)"></strong>
           <span x-show="logins.percobaan_sejam" x-text="`· ${logins.percobaan_sejam} percobaan gagal dalam 60 menit`"></span></p>
        <ul x-show="logins.alasan.length">
          <template x-for="a in logins.alasan" :key="a"><li x-text="a"></li></template>
        </ul>
        <button type="button" x-show="logins.status === 'perlu_diperiksa'" @click="sudahDiperiksa()">Sudah diperiksa</button>

        <h3>Administrator baru</h3>
        <p class="redup" x-show="logins.admin.length === 0">Tidak ada.</p>
        <table x-show="logins.admin.length">
          <tr><th>Waktu</th><th>Kejadian</th><th>Username</th><th>IP</th></tr>
          <template x-for="(k, i) in logins.admin" :key="i">
            <tr><td x-text="waktu(k.waktu)"></td><td x-text="k.jenis"></td><td x-text="k.username"></td><td x-text="k.ip || '—'"></td></tr>
          </template>
        </table>

        <h3>Login berhasil</h3>
        <table>
          <tr><th>Waktu</th><th>Username</th><th>Role</th><th>IP</th><th>Negara</th><th>Browser</th><th>Jalur</th></tr>
          <template x-for="(k, i) in logins.berhasil" :key="i">
            <tr>
              <td x-text="waktu(k.waktu)"></td><td x-text="k.username"></td><td x-text="k.role || '—'"></td>
              <td x-text="k.ip || '—'"></td><td x-text="k.negara || '—'"></td>
              <td x-text="k.skrip ? 'skrip' : `${k.peramban} · ${k.os}`"></td><td x-text="k.jalur"></td>
            </tr>
          </template>
        </table>

        <h3>Login gagal (30 hari)</h3>
        <table>
          <tr><th>IP</th><th>Negara</th><th>Percobaan</th><th>Username yang dicoba</th><th>Jalur</th><th></th></tr>
          <template x-for="g in logins.gagal" :key="g.ip">
            <tr>
              <td x-text="g.ip"></td><td x-text="g.negara || '—'"></td><td x-text="g.jumlah"></td>
              <td x-text="g.username.join(', ')"></td><td x-text="g.jalur.join(', ')"></td>
              <td x-text="g.skrip ? 'skrip' : ''"></td>
            </tr>
          </template>
        </table>
      </div>
    </template>
  </section>

  <section x-show="tab === 'aktivitas'">
    <ul>
      {% for r in riwayat %}
      <li>{{ r.dibuat_pada.strftime('%Y-%m-%d %H:%M') }} — [{{ r.level }}] {{ r.pesan }}</li>
      {% endfor %}
    </ul>
  </section>
</div>
<script src="/static/app/detail.js"></script>
{% endblock %}
```

File: `src/wpmgr/static/app/detail.js`
```js
const TAB_SAH = ['ringkasan', 'paket', 'uptime', 'error', 'login', 'traffic', 'aktivitas'];
const TEKS_KEAMANAN_DETAIL = { aman: 'Aman', diserang: 'Diserang', perlu_diperiksa: 'Perlu diperiksa' };

function detailSite(siteId, tabAwal) {
  return {
    siteId,
    tab: tabAwal,
    galat: '',
    info: '',
    ga4: '',
    uptime: null,
    errors: null,
    logins: null,
    traffic: null,

    mulai() {
      // Tab dari URL (tautan halaman Kesehatan) didahulukan; tanpa itu, tab
      // terakhir yang dibuka di browser ini.
      if (!new URLSearchParams(location.search).get('tab')) {
        try {
          const t = localStorage.getItem('wpmgr_tab');
          if (TAB_SAH.includes(t)) this.tab = t;
        } catch (e) { /* localStorage bisa diblokir; tab bawaan tetap berlaku */ }
      }
      this.muatTab();
    },

    pilih(t) {
      this.tab = t;
      try { localStorage.setItem('wpmgr_tab', t); } catch (e) { /* abaikan */ }
      history.replaceState(null, '', `?tab=${encodeURIComponent(t)}`);
      this.muatTab();
    },

    async ambil(url) {
      const r = await fetch(url);
      if (!r.ok) throw new Error(await pesanGalat(r));
      return r.json();
    },

    async kirim(method, url, body) {
      const r = await fetch(url, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) throw new Error(await pesanGalat(r));
      return r.json();
    },

    async muatTab() {
      this.galat = '';
      const dasar = `/api/sites/${this.siteId}`;
      try {
        if (this.tab === 'uptime') this.uptime = await this.ambil(`${dasar}/uptime?hari=30`);
        if (this.tab === 'error') this.errors = await this.ambil(`${dasar}/errors`);
        if (this.tab === 'login') this.logins = await this.ambil(`${dasar}/logins?hari=30`);
        if (this.tab === 'traffic') this.traffic = await this.ambil(`${dasar}/traffic?hari=30`);
      } catch (e) {
        this.galat = `Data tidak dapat dimuat. ${e.message}`;
      }
    },

    async tandaiSelesai(id) {
      try {
        await this.kirim('POST', `/api/sites/${this.siteId}/errors/${id}/selesai`);
        await this.muatTab();
      } catch (e) {
        this.galat = `Error tidak dapat ditandai selesai. ${e.message}`;
      }
    },

    async sudahDiperiksa() {
      try {
        await this.kirim('POST', `/api/sites/${this.siteId}/keamanan/diperiksa`);
        await this.muatTab();
      } catch (e) {
        this.galat = `Status tidak dapat diperbarui. ${e.message}`;
      }
    },

    async simpanGa4() {
      this.info = '';
      try {
        await this.kirim('PUT', `/api/sites/${this.siteId}/ga4`, { property_id: this.ga4 });
        this.info = 'Property GA4 disimpan. Data diambil pada pengambilan harian berikutnya.';
      } catch (e) {
        this.galat = `Property GA4 tidak disimpan. ${e.message}`;
      }
    },

    async sso() {
      try {
        const d = await this.ambil(`/api/sso/${this.siteId}`);
        window.open(d.url, '_blank', 'noopener');
      } catch (e) {
        this.galat = `SSO gagal. ${e.message}`;
      }
    },

    async scan() {
      this.info = '';
      try {
        await this.kirim('POST', '/api/jobs/scan', { site_id: this.siteId });
        this.info = 'Scan diantrekan.';
      } catch (e) {
        this.galat = `Scan tidak diantrekan. ${e.message}`;
      }
    },

    teksKeamanan(s) { return TEKS_KEAMANAN_DETAIL[s] || s; },
    persen(p) { return p == null ? '—' : `${p}%`; },
    waktu(iso) { return iso ? new Date(iso).toLocaleString('id-ID') : '—'; },
  };
}
```

Tab dari `localStorage` wajib divalidasi terhadap `TAB_SAH` sebelum dipakai. Tab `traffic` sudah ada di `TAB_SAH` supaya Task 23 hanya perlu menambah tombol dan seksinya. Bila tab tersimpan adalah `traffic` sebelum Task 23 selesai, tidak ada seksi yang tampil; itu bisa diterima selama rencana ini berjalan.

Tambahkan ke `app.css`:

```css
nav.tab{display:flex;gap:.25rem;border-bottom:2px solid #ccd;margin:1rem 0}
nav.tab button{border:1px solid transparent;border-bottom:none;background:none;padding:.4rem .8rem;border-radius:.4rem .4rem 0 0;cursor:pointer}
nav.tab button.aktif{background:#e8f0fb;border-color:#ccd;font-weight:600}
.lencana{display:inline-block;min-width:1.1em;padding:0 .3em;border-radius:.6em;background:#d64545;color:#fff;font-size:.75em;text-align:center}
.batang{display:flex;align-items:flex-end;gap:2px;height:80px;margin:.75rem 0}
.batang-kolom{flex:1;height:100%;display:flex;align-items:flex-end;background:#f3f4f6}
.batang-isi{width:100%;background:#5b8def}
.batang-buruk{background:#d64545}
```

- [ ] **Step 6: Jalankan unit dan integrasi.** Expected: semua lulus, termasuk `test_template_aman.py`.

- [ ] **Step 7: Periksa di browser.** Buka detail satu site, klik setiap tab, dan pastikan data termuat tanpa galat di konsol. Tempel ringkasan pengamatan di laporan.

- [ ] **Step 8: Commit.**

```bash
git add src/wpmgr tests/integration/test_api_monitoring.py tests/unit/test_template_aman.py
git commit -m "feat: detail site bertab dengan uptime, error, dan riwayat login"
```

### Task 23: Tab Traffic dan laporan bulanan

**Files:**
- Create: `src/wpmgr/laporan.py`, `src/wpmgr/templates/laporan.html`, `src/wpmgr/templates/_batang.html`, `tests/integration/test_laporan.py`
- Modify: `src/wpmgr/traffic.py`, `src/wpmgr/web/routes_monitoring.py`, `src/wpmgr/web/routes_pages.py`, `src/wpmgr/templates/site_detail.html`, `src/wpmgr/static/app/detail.js`, `src/wpmgr/static/app/app.css`

**Interfaces:**
- Consumes: Task 19–22.
- Produces:
  - `wpmgr.traffic.CATATAN_DUA_SUMBER: str`, `urai_asal(kunci: str) -> tuple[str, str]`, `ringkasan_traffic(sesi, site_id, dari: date, sampai: date, sumber: str) -> dict | None` → `{harian: [{tanggal, kunjungan|None, pengunjung|None}], total_kunjungan, total_pengunjung_harian, maks_harian, halaman: [{kunci, kunjungan}] (10 teratas), asal: [{kategori, nama, kunjungan}], perangkat: [{kunci, kunjungan}]}`.
  - `GET /api/sites/{id}/traffic?hari=30` → `{plugin, ga4, ga4_terpasang, ga4_aktif, ga4_error, anomali, catatan}`.
  - `wpmgr.laporan.susun_laporan(sesi, site, tahun: int, bulan: int, sekarang: datetime) -> dict`.
  - `GET /sites/{id}/laporan/{YYYY-MM}` (butuh login; 404 untuk format salah atau bulan di masa depan).
  - Tab `("traffic", "Traffic")` di `TAB_DETAIL`, disisipkan setelah `login`.

Pengunjung ditampilkan sebagai **"jumlah pengunjung harian"**, yaitu jumlah dari angka pengunjung unik setiap hari. Pengunjung yang datang di dua hari berbeda terhitung dua kali, karena hash-nya berganti setiap hari. Label harus menyatakannya apa adanya.

Uptime laporan dihitung dari durasi insiden (koreksi #6): `persen = 100 × (1 − detik_mati / detik_periode)`. Periode dipotong di `sekarang` untuk bulan yang sedang berjalan. Persen bernilai `None` ("belum dipantau") bila `site.uptime_status == belum_dicek`.

- [ ] **Step 1: Tulis test integrasi yang gagal.**

File: `tests/integration/test_laporan.py`
```python
from datetime import date, datetime, timedelta, timezone

import pytest

from wpmgr.models import (
    ActivityLog,
    TrafficHarian,
    TrafficRincian,
    UptimeInsiden,
    UptimeStatus,
)

pytestmark = pytest.mark.integration


def isi_agustus(sesi, site):
    for hari in range(1, 32):
        sesi.add(TrafficHarian(site_id=site.id, tanggal=date(2026, 8, hari), sumber="plugin",
                               kunjungan=10, pengunjung=4))
    sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                            dimensi="halaman", kunci="/layanan", kunjungan=120))
    sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                            dimensi="halaman", kunci="<script>alert(1)</script>", kunjungan=3))
    sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 8, 5), sumber="plugin",
                            dimensi="asal", kunci="pencarian:Google", kunjungan=50))
    sesi.add(UptimeInsiden(site_id=site.id, mulai=datetime(2026, 8, 10, 10, tzinfo=timezone.utc),
                           selesai=datetime(2026, 8, 10, 13, tzinfo=timezone.utc), penyebab="HTTP 500"))
    sesi.add(ActivityLog(site_id=site.id, level="info", pesan="Update plugin elementor",
                         dibuat_pada=datetime(2026, 8, 15, 9, tzinfo=timezone.utc),
                         detail={"tipe": "plugin", "slug": "elementor/elementor.php",
                                 "versi_sebelum": "3.18", "versi_sesudah": "3.20", "email": "a@b.test"}))
    site.uptime_status = UptimeStatus.naik
    sesi.commit()


def test_api_traffic_tanpa_ga4(klien_web, sesi, site):
    hari_ini = datetime.now(timezone.utc).date()
    sesi.add(TrafficHarian(site_id=site.id, tanggal=hari_ini - timedelta(days=1), sumber="plugin",
                           kunjungan=7, pengunjung=5))
    sesi.commit()
    d = klien_web.get(f"/api/sites/{site.id}/traffic?hari=30").json()
    assert len(d["plugin"]["harian"]) == 30
    assert d["plugin"]["total_kunjungan"] == 7
    assert d["ga4"] is None
    assert d["ga4_terpasang"] is False


def test_api_traffic_ga4_terpasang_tanpa_data(klien_web, sesi, site):
    site.ga4_property_id = "123456789"
    site.ga4_error = "Service account belum ditambahkan sebagai Viewer di property ini"
    sesi.commit()
    d = klien_web.get(f"/api/sites/{site.id}/traffic").json()
    assert d["ga4_terpasang"] is True
    assert d["ga4"] is None
    assert "Viewer" in d["ga4_error"]


def test_laporan_agustus(klien_web, sesi, site):
    isi_agustus(sesi, site)
    r = klien_web.get(f"/sites/{site.id}/laporan/2026-08")
    assert r.status_code == 200
    teks = r.text
    assert "Agustus 2026" in teks
    assert "310" in teks                      # total kunjungan 31 hari x 10
    assert "99.60%" in teks                   # 3 jam mati dari 744 jam
    assert "/layanan" in teks
    assert "Pencarian" in teks and "Google" in teks
    assert "3.18" in teks and "3.20" in teks
    assert "<script>alert(1)</script>" not in teks
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in teks


def test_laporan_site_belum_dipantau(klien_web, sesi, site):
    r = klien_web.get(f"/sites/{site.id}/laporan/2026-08")
    assert "belum dipantau" in r.text
    assert "Belum ada data pengunjung" in r.text


@pytest.mark.parametrize("bulan", ["2026-13", "2026-8", "abc", "2099-01"])
def test_bulan_tidak_sah_404(klien_web, site, bulan):
    assert klien_web.get(f"/sites/{site.id}/laporan/{bulan}").status_code == 404


def test_detail_punya_tab_traffic(klien_web, site):
    assert "pilih('traffic')" in klien_web.get(f"/sites/{site.id}").text
```

- [ ] **Step 2: Jalankan dan pastikan gagal.**

- [ ] **Step 3: Ringkasan traffic.** Tambahkan ke `src/wpmgr/traffic.py` (tambahkan `func` ke impor sqlalchemy dan `TrafficRincian` ke impor model):

```python
CATATAN_DUA_SUMBER = (
    "Angka GA biasanya lebih kecil karena tidak menghitung pengunjung yang memakai "
    "ad-blocker atau menolak cookie. Keduanya benar menurut cara hitungnya masing-masing."
)
NAMA_KATEGORI = {
    "pencarian": "Pencarian", "sosial": "Media sosial", "langsung": "Langsung",
    "site_lain": "Site lain", "lainnya": "Lainnya",
}


def urai_asal(kunci: str) -> tuple[str, str]:
    kategori, _, nama = kunci.partition(":")
    return NAMA_KATEGORI.get(kategori, "Lainnya"), nama


def ringkasan_traffic(sesi: Session, site_id, dari: date, sampai: date, sumber: str) -> dict | None:
    harian_db = {
        b.tanggal: b
        for b in sesi.scalars(
            select(TrafficHarian).where(
                TrafficHarian.site_id == site_id, TrafficHarian.sumber == sumber,
                TrafficHarian.tanggal >= dari, TrafficHarian.tanggal <= sampai,
            )
        ).all()
    }
    if not harian_db:
        return None
    harian = []
    for i in range((sampai - dari).days + 1):
        t = dari + timedelta(days=i)
        b = harian_db.get(t)
        harian.append({"tanggal": t.isoformat(),
                       "kunjungan": b.kunjungan if b else None,
                       "pengunjung": b.pengunjung if b else None})

    jumlah = func.sum(TrafficRincian.kunjungan).label("n")
    rincian = sesi.execute(
        select(TrafficRincian.dimensi, TrafficRincian.kunci, jumlah)
        .where(TrafficRincian.site_id == site_id, TrafficRincian.sumber == sumber,
               TrafficRincian.tanggal >= dari, TrafficRincian.tanggal <= sampai)
        .group_by(TrafficRincian.dimensi, TrafficRincian.kunci)
        .order_by(jumlah.desc())
    ).all()
    halaman = [{"kunci": k, "kunjungan": int(n)} for d, k, n in rincian if d == "halaman"][:10]
    asal = [
        {"kategori": urai_asal(k)[0], "nama": urai_asal(k)[1], "kunjungan": int(n)}
        for d, k, n in rincian if d == "asal"
    ]
    perangkat = [{"kunci": k, "kunjungan": int(n)} for d, k, n in rincian if d == "perangkat"]

    return {
        "harian": harian,
        "total_kunjungan": sum(b.kunjungan for b in harian_db.values()),
        "total_pengunjung_harian": sum(b.pengunjung for b in harian_db.values()),
        "maks_harian": max(b.kunjungan for b in harian_db.values()),
        "halaman": halaman,
        "asal": asal,
        "perangkat": perangkat,
    }
```

- [ ] **Step 4: API traffic.** Di `routes_monitoring.py` (impor `from wpmgr.config import get_settings`, `from wpmgr.traffic import CATATAN_DUA_SUMBER, anomali_site, ringkasan_traffic`):

```python
@router.get("/api/sites/{site_id}/traffic")
def traffic_site(site_id: uuid.UUID, pengguna: PenggunaApi, hari: int = 30):
    hari = max(1, min(hari, 90))
    sampai = _sekarang().date()
    dari = sampai - timedelta(days=hari - 1)
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        return {
            "plugin": ringkasan_traffic(sesi, site.id, dari, sampai, "plugin"),
            "ga4": ringkasan_traffic(sesi, site.id, dari, sampai, "ga4") if site.ga4_property_id else None,
            "ga4_terpasang": bool(site.ga4_property_id),
            "ga4_aktif": bool(get_settings().ga4_credentials),
            "ga4_error": site.ga4_error,
            "anomali": anomali_site(sesi, site.id, sampai),
            "catatan": CATATAN_DUA_SUMBER,
        }
```

- [ ] **Step 5: Tab Traffic.** Di `routes_pages.py`, sisipkan `("traffic", "Traffic")` ke `TAB_DETAIL` setelah `("login", "Login")`. Tambahkan juga lencana `"traffic": "!" if anomali_site(sesi, site.id, sekarang.date()) else ""`, dihitung di dalam blok sesi `halaman_detail` (impor `anomali_site`). Di `site_detail.html`, tambahkan seksi berikut sebelum seksi `aktivitas`:

```html
  <section x-show="tab === 'traffic'">
    <template x-if="traffic">
      <div>
        <p class="galat" x-show="traffic.anomali === 'anjlok'">Traffic kemarin anjlok dibandingkan median 14 hari sebelumnya.</p>
        <p class="info" x-show="traffic.anomali === 'melonjak'">Traffic kemarin melonjak dibandingkan median 14 hari sebelumnya (kemungkinan bot atau kampanye).</p>
        <div class="dua-panel">
          <template x-for="panel in panelTraffic()" :key="panel.kunci">
            <div class="panel">
              <h3>Kunjungan 30 hari <span class="sumber" x-text="panel.label"></span></h3>
              <p class="redup" x-show="!panel.data" x-text="panel.kosong"></p>
              <template x-if="panel.data">
                <div>
                  <div class="batang">
                    <template x-for="h in panel.data.harian" :key="h.tanggal">
                      <div class="batang-kolom" :title="`${h.tanggal}: ${h.kunjungan ?? 'tanpa data'}`">
                        <div class="batang-isi" :style="`height:${tinggi(h.kunjungan, panel.data.maks_harian)}%`"></div>
                      </div>
                    </template>
                  </div>
                  <p>Kunjungan <strong x-text="panel.data.total_kunjungan"></strong> ·
                     jumlah pengunjung harian <strong x-text="panel.data.total_pengunjung_harian"></strong></p>
                  <h4>Halaman teratas</h4>
                  <ol>
                    <template x-for="h in panel.data.halaman" :key="h.kunci">
                      <li><span x-text="h.kunci"></span> — <span x-text="h.kunjungan"></span></li>
                    </template>
                  </ol>
                  <h4>Asal pengunjung</h4>
                  <ul>
                    <template x-for="a in panel.data.asal" :key="a.kategori + a.nama">
                      <li x-text="`${a.kategori}${a.nama ? ' · ' + a.nama : ''}: ${a.kunjungan}`"></li>
                    </template>
                  </ul>
                  <h4>Perangkat</h4>
                  <ul>
                    <template x-for="p in panel.data.perangkat" :key="p.kunci">
                      <li x-text="`${p.kunci}: ${p.kunjungan}`"></li>
                    </template>
                  </ul>
                </div>
              </template>
            </div>
          </template>
        </div>
        <p class="redup" x-show="traffic.ga4_terpasang" x-text="traffic.catatan"></p>
      </div>
    </template>
  </section>
```

Tambahkan metode ke `detailSite()` di `detail.js`:

```js
    panelTraffic() {
      if (!this.traffic) return [];
      const panel = [{
        kunci: 'plugin', label: 'penghitung plugin', data: this.traffic.plugin,
        kosong: 'Belum ada data dari penghitung plugin. Data mulai terkumpul setelah connector 2.x terpasang.',
      }];
      if (this.traffic.ga4_terpasang) {
        panel.push({
          kunci: 'ga4', label: 'Google Analytics', data: this.traffic.ga4,
          kosong: this.traffic.ga4_error || 'Data GA4 belum diambil. Pengambilan berjalan sekali sehari.',
        });
      }
      return panel;
    },

    tinggi(n, maks) {
      return !n || !maks ? 0 : Math.max(2, Math.round((n / maks) * 100));
    },
```

Tambahkan ke `app.css`:

```css
.dua-panel{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1rem}
.panel{border:1px solid #d5d9de;border-radius:.5rem;padding:.75rem}
.sumber{display:inline-block;font-size:.75em;font-weight:400;padding:0 .5em;border-radius:.6em;background:#eef0f3;color:#5a626c}
```

- [ ] **Step 6: Laporan bulanan.**

File: `src/wpmgr/laporan.py`
```python
"""Data laporan bulanan untuk client: traffic, ketersediaan, dan update."""

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from wpmgr.models import ActivityLog, UptimeInsiden, UptimeStatus
from wpmgr.traffic import CATATAN_DUA_SUMBER, ringkasan_traffic
from wpmgr.uptime import teks_durasi

NAMA_BULAN = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
              "Agustus", "September", "Oktober", "November", "Desember"]


def susun_laporan(sesi: Session, site, tahun: int, bulan: int, sekarang: datetime) -> dict:
    awal = date(tahun, bulan, 1)
    akhir = date(tahun, bulan, monthrange(tahun, bulan)[1])
    awal_dt = datetime(tahun, bulan, 1, tzinfo=timezone.utc)
    akhir_dt = datetime.combine(akhir + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    batas = min(akhir_dt, sekarang)

    insiden = sesi.scalars(
        select(UptimeInsiden).where(
            UptimeInsiden.site_id == site.id,
            UptimeInsiden.mulai < akhir_dt,
            or_(UptimeInsiden.selesai.is_(None), UptimeInsiden.selesai > awal_dt),
        ).order_by(UptimeInsiden.mulai)
    ).all()
    # Dihitung dari insiden, bukan dari tabel cek: cek dipangkas setelah 90
    # hari, insiden disimpan permanen.
    detik_mati = sum(
        max(0.0, (min(i.selesai or sekarang, batas) - max(i.mulai, awal_dt)).total_seconds())
        for i in insiden
    )
    detik_periode = (batas - awal_dt).total_seconds()
    dipantau = site.uptime_status != UptimeStatus.belum_dicek and detik_periode > 0
    persen = round(100 * (1 - detik_mati / detik_periode), 2) if dipantau else None

    update = []
    for log in sesi.scalars(
        select(ActivityLog).where(
            ActivityLog.site_id == site.id, ActivityLog.level == "info",
            ActivityLog.dibuat_pada >= awal_dt, ActivityLog.dibuat_pada < akhir_dt,
            ActivityLog.detail.has_key("versi_sesudah"),
        ).order_by(ActivityLog.dibuat_pada)
    ).all():
        d = log.detail or {}
        update.append({
            "tanggal": log.dibuat_pada,
            "komponen": d.get("slug") or "WP Manager Connector",
            "tipe": d.get("tipe") or "connector",
            "versi_sebelum": d.get("versi_sebelum") or "?",
            "versi_sesudah": d.get("versi_sesudah"),
        })

    return {
        "site": site,
        "judul_bulan": f"{NAMA_BULAN[bulan]} {tahun}",
        "awal": awal,
        "akhir": akhir,
        "traffic": {
            "plugin": ringkasan_traffic(sesi, site.id, awal, akhir, "plugin"),
            "ga4": ringkasan_traffic(sesi, site.id, awal, akhir, "ga4"),
        },
        "catatan": CATATAN_DUA_SUMBER,
        "uptime": {
            "persen": persen,
            "jumlah_insiden": len(insiden),
            "durasi_mati": teks_durasi(detik_mati),
            "insiden": [
                {"mulai": i.mulai, "selesai": i.selesai, "penyebab": i.penyebab,
                 "durasi": teks_durasi(((i.selesai or sekarang) - i.mulai).total_seconds())}
                for i in insiden
            ],
        },
        "update": update,
        "dibuat_pada": sekarang,
    }
```

Di `routes_pages.py` (impor `re`, `from wpmgr.laporan import susun_laporan`):

```python
POLA_BULAN = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


@router.get("/sites/{site_id}/laporan/{bulan}")
def laporan_bulanan(request: Request, site_id: uuid.UUID, bulan: str, pengguna: PenggunaHalaman):
    cocok = POLA_BULAN.match(bulan)
    sekarang = datetime.now(timezone.utc)
    if cocok is None:
        raise HTTPException(status_code=404, detail="Bulan tidak dikenal")
    tahun, nomor = int(cocok.group(1)), int(cocok.group(2))
    if date(tahun, nomor, 1) > sekarang.date():
        raise HTTPException(status_code=404, detail="Bulan ini belum dimulai")
    with db.SessionLocal() as sesi:
        site = sesi.get(Site, site_id)
        if site is None:
            raise HTTPException(status_code=404, detail="Site tidak ditemukan")
        data = susun_laporan(sesi, site, tahun, nomor, sekarang)
    return _tpl().TemplateResponse(request, "laporan.html", {"pengguna": pengguna, **data})
```

File: `src/wpmgr/templates/_batang.html`
```html
{% macro batang(harian, maks) -%}
<div class="batang">
  {% for h in harian %}
  <div class="batang-kolom" title="{{ h.tanggal }}: {{ h.kunjungan if h.kunjungan is not none else 'tanpa data' }}">
    <div class="batang-isi" style="height: {{ (((h.kunjungan or 0) / maks) * 100)|round(0, 'ceil')|int if maks else 0 }}%"></div>
  </div>
  {% endfor %}
</div>
{%- endmacro %}
```

File: `src/wpmgr/templates/laporan.html`
```html
{% from "_batang.html" import batang %}
<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <title>Laporan {{ judul_bulan }} — {{ site.nama }}</title>
  <style>
    body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#1f2328;max-width:900px;margin:2rem auto;padding:0 1rem;line-height:1.45}
    h1{margin-bottom:.25rem} h2{border-bottom:1px solid #d5d9de;padding-bottom:.25rem;margin-top:2rem}
    table{border-collapse:collapse;width:100%} th,td{text-align:left;padding:.3rem .5rem;border-bottom:1px solid #e3e6ea}
    .dua{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}
    .batang{display:flex;align-items:flex-end;gap:2px;height:70px;margin:.5rem 0}
    .batang-kolom{flex:1;height:100%;display:flex;align-items:flex-end;background:#f3f4f6}
    .batang-isi{width:100%;background:#5b8def}
    .catatan{color:#5a626c;font-size:.9em}
    @media print{.cetak-sembunyi{display:none} body{margin:0} h2{break-after:avoid}}
  </style>
</head>
<body>
<p class="cetak-sembunyi"><button type="button" onclick="window.print()">Cetak / simpan PDF</button>
  <a href="/sites/{{ site.id }}">Kembali ke dashboard</a></p>

<h1>Laporan pemeliharaan website</h1>
<p><strong>{{ site.nama }}</strong> · {{ site.url }}<br>
   Periode {{ judul_bulan }} ({{ awal.strftime('%d-%m-%Y') }} s.d. {{ akhir.strftime('%d-%m-%Y') }})</p>

<h2>Ketersediaan</h2>
{% if uptime.persen is none %}
  <p>Site belum dipantau pada periode ini.</p>
{% else %}
  <p>Uptime <strong>{{ '%.2f'|format(uptime.persen) }}%</strong> · {{ uptime.jumlah_insiden }} insiden ·
     total waktu tidak dapat diakses {{ uptime.durasi_mati }}</p>
  {% if uptime.insiden %}
  <table>
    <tr><th>Mulai</th><th>Selesai</th><th>Durasi</th><th>Penyebab</th></tr>
    {% for i in uptime.insiden %}
    <tr><td>{{ i.mulai.strftime('%d-%m-%Y %H:%M') }}</td>
        <td>{{ i.selesai.strftime('%d-%m-%Y %H:%M') if i.selesai else 'belum pulih' }}</td>
        <td>{{ i.durasi }}</td><td>{{ i.penyebab }}</td></tr>
    {% endfor %}
  </table>
  {% endif %}
{% endif %}

<h2>Pengunjung</h2>
{% if not traffic.plugin and not traffic.ga4 %}
  <p>Belum ada data pengunjung untuk periode ini.</p>
{% else %}
<div class="dua">
  {% for kunci, judul in [('plugin', 'Penghitung WP Manager'), ('ga4', 'Google Analytics')] %}
  {% set t = traffic[kunci] %}
  {% if t %}
  <div>
    <h3>{{ judul }}</h3>
    <p>Kunjungan <strong>{{ t.total_kunjungan }}</strong> · jumlah pengunjung harian <strong>{{ t.total_pengunjung_harian }}</strong></p>
    {{ batang(t.harian, t.maks_harian) }}
    <h4>Halaman teratas</h4>
    <ol>{% for h in t.halaman %}<li>{{ h.kunci }} — {{ h.kunjungan }}</li>{% endfor %}</ol>
    <h4>Asal pengunjung</h4>
    <ul>{% for a in t.asal %}<li>{{ a.kategori }}{% if a.nama %} · {{ a.nama }}{% endif %}: {{ a.kunjungan }}</li>{% endfor %}</ul>
    <h4>Perangkat</h4>
    <ul>{% for p in t.perangkat %}<li>{{ p.kunci }}: {{ p.kunjungan }}</li>{% endfor %}</ul>
  </div>
  {% endif %}
  {% endfor %}
</div>
{% if traffic.plugin and traffic.ga4 %}<p class="catatan">{{ catatan }}</p>{% endif %}
{% endif %}

<h2>Pembaruan yang dikerjakan</h2>
{% if update %}
<table>
  <tr><th>Tanggal</th><th>Komponen</th><th>Versi</th></tr>
  {% for u in update %}
  <tr><td>{{ u.tanggal.strftime('%d-%m-%Y') }}</td><td>{{ u.komponen }}</td>
      <td>{{ u.versi_sebelum }} → {{ u.versi_sesudah }}</td></tr>
  {% endfor %}
</table>
{% else %}
<p>Tidak ada pembaruan pada periode ini.</p>
{% endif %}

<p class="catatan">Dibuat {{ dibuat_pada.strftime('%d-%m-%Y %H:%M') }} UTC oleh WP Manager.</p>
</body>
</html>
```

`onclick="window.print()"` tidak memuat data apa pun dari site, jadi aman.

- [ ] **Step 7: Jalankan unit dan integrasi.** Expected: semua lulus, termasuk `test_template_aman.py`.

- [ ] **Step 8: Periksa cetak di browser.** Buka laporan satu bulan, gunakan pratinjau cetak, dan pastikan tombol tersembunyi dan tata letak dua kolom terbaca. Tempel pengamatan di laporan.

- [ ] **Step 9: Commit.**

```bash
git add src/wpmgr tests/integration/test_laporan.py
git commit -m "feat: tab Traffic dua sumber dan laporan bulanan siap cetak"
```

### Task 24: Halaman Keamanan (IP penyerang lintas site)

**Files:**
- Create: `src/wpmgr/templates/keamanan.html`, `src/wpmgr/static/app/keamanan.js`, `tests/integration/test_keamanan_halaman.py`
- Modify: `src/wpmgr/web/routes_monitoring.py`, `src/wpmgr/web/routes_pages.py`, `src/wpmgr/templates/base.html`

**Interfaces:**
- Consumes: Task 16 (data `login_gagal`), Task 15 (`urai_ua`).
- Produces:
  - `GET /api/keamanan/penyerang?jam=24` → daftar `{ip, negara, jumlah, jumlah_site, site, username, jalur, skrip, terakhir}` (maksimal 500, urut `jumlah` menurun; `site`, `username`, `jalur` berupa string yang sudah digabung koma).
  - Halaman `/keamanan` dengan atribusi DB-IP; tautan "Keamanan" di navigasi.

- [ ] **Step 1: Tulis test integrasi yang gagal.**

File: `tests/integration/test_keamanan_halaman.py`
```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from wpmgr.models import LoginGagal, Site, SiteStatus

pytestmark = pytest.mark.integration

JAM = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def gagal(sesi, site, ip, jumlah, username="admin", jam=JAM, jalur="xmlrpc", ua="curl/8"):
    sesi.add(LoginGagal(site_id=site.id, jam=jam, ip=ip, username=username, jalur=jalur,
                        jumlah=jumlah, user_agent=ua, negara="RU" if ip else None))
    sesi.commit()


def test_agregasi_lintas_site(klien_web, sesi, site):
    lain = Site(id=uuid.uuid4(), nama="Lain", url="https://lain.test", status=SiteStatus.active,
                secret_terenkripsi=b"x")
    sesi.add(lain)
    sesi.commit()
    gagal(sesi, site, "198.51.100.7", 30)
    gagal(sesi, lain, "198.51.100.7", 12, username="root", jalur="form",
          ua="Mozilla/5.0 (Windows NT 10.0) Chrome/128.0")
    gagal(sesi, site, "203.0.113.9", 5)
    gagal(sesi, site, "", 99, username="(lainnya)")
    gagal(sesi, site, "192.0.2.1", 1000, jam=JAM - timedelta(days=3))

    d = klien_web.get("/api/keamanan/penyerang?jam=24").json()
    assert [b["ip"] for b in d] == ["198.51.100.7", "203.0.113.9"]
    teratas = d[0]
    assert teratas["jumlah"] == 42
    assert teratas["jumlah_site"] == 2
    assert teratas["site"] == "Contoh, Lain"
    assert teratas["username"] == "admin, root"
    assert teratas["jalur"] == "form, xmlrpc"
    assert teratas["skrip"] is True
    assert teratas["negara"] == "RU"


def test_jendela_waktu(klien_web, sesi, site):
    gagal(sesi, site, "192.0.2.1", 10, jam=JAM - timedelta(days=3))
    assert [b["ip"] for b in klien_web.get("/api/keamanan/penyerang?jam=168").json()] == ["192.0.2.1"]


def test_halaman_keamanan(klien_web):
    r = klien_web.get("/keamanan")
    assert r.status_code == 200
    assert "layarKeamanan()" in r.text
    assert "DB-IP" in r.text
    assert 'href="/keamanan"' in r.text
```

- [ ] **Step 2: Jalankan dan pastikan gagal.**

- [ ] **Step 3: API.** Tambahkan ke `routes_monitoring.py`:

```python
@router.get("/api/keamanan/penyerang")
def penyerang(pengguna: PenggunaApi, jam: int = 24):
    jam = max(1, min(jam, 24 * 30))
    sejak = _sekarang() - timedelta(hours=jam)
    per_ip: dict = defaultdict(lambda: {"jumlah": 0, "negara": None, "site": set(), "username": defaultdict(int),
                                        "jalur": set(), "skrip": False, "terakhir": None})
    with db.SessionLocal() as sesi:
        for g, nama_site in sesi.execute(
            select(LoginGagal, Site.nama).join(Site, Site.id == LoginGagal.site_id)
            .where(LoginGagal.jam >= sejak, LoginGagal.ip != "")
        ).all():
            r = per_ip[g.ip]
            r["jumlah"] += g.jumlah
            r["negara"] = r["negara"] or g.negara
            r["site"].add(nama_site)
            r["username"][g.username] += g.jumlah
            r["jalur"].add(g.jalur)
            r["skrip"] = r["skrip"] or urai_ua(g.user_agent)["skrip"]
            r["terakhir"] = max(filter(None, (r["terakhir"], g.jam)))
    hasil = [
        {
            "ip": ip,
            "negara": r["negara"],
            "jumlah": r["jumlah"],
            "jumlah_site": len(r["site"]),
            "site": ", ".join(sorted(r["site"])[:5]),
            "username": ", ".join(u for u, _ in sorted(r["username"].items(), key=lambda x: (-x[1], x[0]))[:5]),
            "jalur": ", ".join(sorted(r["jalur"])),
            "skrip": r["skrip"],
            "terakhir": _iso(r["terakhir"]),
        }
        for ip, r in per_ip.items()
    ]
    hasil.sort(key=lambda b: -b["jumlah"])
    return hasil[:500]
```

- [ ] **Step 4: Halaman.** Di `routes_pages.py`:

```python
@router.get("/keamanan")
def halaman_keamanan(request: Request, pengguna: PenggunaHalaman):
    return _tpl().TemplateResponse(request, "keamanan.html", {"pengguna": pengguna})
```

Di `base.html`, tambahkan `<a href="/keamanan">Keamanan</a>` setelah tautan Site.

File: `src/wpmgr/templates/keamanan.html`
```html
{% extends "base.html" %}
{% block judul %}Keamanan — WP Manager{% endblock %}
{% block isi %}
<div x-data="layarKeamanan()" x-init="muat()">
  <h1>IP penyerang</h1>
  <p>Percobaan login gagal lintas semua site. IP yang menyerang banyak site sekaligus hampir selalu
     botnet; ekspor daftar ini untuk dimasukkan ke daftar blokir Wordfence atau Cloudflare.</p>
  <div class="toolbar">
    <label>Rentang
      <select x-model.number="jam" @change="muat()">
        <option value="24">24 jam</option>
        <option value="168">7 hari</option>
        <option value="720">30 hari</option>
      </select>
    </label>
  </div>
  <p class="galat" x-show="galat" x-text="galat" role="alert"></p>
  <div id="grid" style="height: 65vh"></div>
  <p class="redup">IP geolocation by <a href="https://db-ip.com" target="_blank" rel="noopener">DB-IP</a>.</p>
</div>
<script src="/static/app/keamanan.js"></script>
{% endblock %}
```

File: `src/wpmgr/static/app/keamanan.js`
```js
function layarKeamanan() {
  return {
    grid: null,
    jam: 24,
    galat: '',

    async muat() {
      this.galat = '';
      try {
        const r = await fetch(`/api/keamanan/penyerang?jam=${encodeURIComponent(this.jam)}`);
        if (!r.ok) {
          this.galat = `Data tidak dapat dimuat. ${await pesanGalat(r)}`;
          return;
        }
        const data = await r.json();
        if (this.grid) {
          this.grid.setData(data);
          return;
        }
        // Tanpa cellTemplate: DataGrid meng-escape sendiri isi sel biasa, dan
        // semua kolom di sini berasal dari site klien (username, UA).
        this.grid = new DataGrid('#grid', {
          dataSource: data,
          keyExpr: 'ip',
          selection: false,
          columns: [
            { dataField: 'ip', caption: 'IP' },
            { dataField: 'negara', caption: 'Negara' },
            { dataField: 'jumlah', caption: 'Percobaan' },
            { dataField: 'jumlah_site', caption: 'Site diserang' },
            { dataField: 'site', caption: 'Site' },
            { dataField: 'username', caption: 'Username dicoba' },
            { dataField: 'jalur', caption: 'Jalur' },
            { caption: 'Alat', calculateCellValue: (b) => (b.skrip ? 'skrip' : '') },
            { dataField: 'terakhir', caption: 'Terakhir', dataType: 'date' },
          ],
        });
      } catch (e) {
        this.galat = `Gagal menghubungi server: ${e.message}`;
      }
    },
  };
}
```

- [ ] **Step 5: Jalankan unit dan integrasi.** Expected: semua lulus.

- [ ] **Step 6: Commit.**

```bash
git add src/wpmgr tests/integration/test_keamanan_halaman.py
git commit -m "feat: halaman Keamanan dengan IP penyerang lintas site"
```

---

## Fase H — Operasi dan e2e

### Task 25: Retensi, crontab, dan README

**Files:**
- Create: `src/wpmgr/retensi.py`, `tests/integration/test_retensi.py`
- Modify: `src/wpmgr/cli.py`, `deploy/crontab`, `README.md`

**Interfaces:**
- Consumes: semua model Lapis 2, `kunci_advisory`, `KUNCI_RETENSI`.
- Produces: `wpmgr.retensi.RETENSI_MENTAH = timedelta(days=90)`, `RETENSI_JOB = timedelta(days=14)`, `JOB_DIPANGKAS`, `pangkas(sesi, sekarang: datetime) -> dict[str, int]`; CLI `prune-monitoring`.

- [ ] **Step 1: Tulis test integrasi yang gagal.**

File: `tests/integration/test_retensi.py`
```python
from datetime import date, datetime, timedelta, timezone

import pytest

from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    CatatanError,
    Job,
    JobStatus,
    JobType,
    KejadianLogin,
    LoginGagal,
    TrafficHarian,
    UptimeCheck,
    UptimeHasil,
    UptimeInsiden,
    UptimePutaran,
)
from wpmgr.retensi import pangkas

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 22, tzinfo=timezone.utc)
LAMA = SEKARANG - timedelta(days=91)
BARU = SEKARANG - timedelta(days=89)


def job_selesai(sesi, site, tipe, selesai, status=JobStatus.success):
    job = buat_job(sesi, site.id, tipe, {"slug": "x"} if tipe == JobType.update_package else None)
    job.status = status
    job.finished_at = selesai
    sesi.commit()
    return job


def test_data_mentah_lama_dipangkas_yang_baru_dipertahankan(sesi, site):
    for waktu in (LAMA, BARU):
        p = UptimePutaran(mulai=waktu, jumlah_site=1, jumlah_gagal=0, gangguan_dashboard=False)
        sesi.add(p)
        sesi.flush()
        sesi.add(UptimeCheck(putaran_id=p.id, site_id=site.id, dicek_pada=waktu, hasil=UptimeHasil.naik))
        sesi.add(KejadianLogin(site_id=site.id, id_di_site=int(waktu.timestamp()), waktu=waktu,
                               jenis="berhasil", username="a"))
        sesi.add(LoginGagal(site_id=site.id, jam=waktu, ip="1.2.3.4", username="a", jalur="form", jumlah=1))
        sesi.add(CatatanError(site_id=site.id, sidik_jari=str(waktu.timestamp()), tingkat="warning",
                              komponen_tipe="core", pesan="x", jumlah=1,
                              pertama_terlihat=waktu, terakhir_terlihat=waktu))
    sesi.add(UptimeInsiden(site_id=site.id, mulai=LAMA, selesai=LAMA, penyebab="HTTP 500"))
    sesi.add(TrafficHarian(site_id=site.id, tanggal=date(2025, 1, 1), sumber="plugin", kunjungan=1, pengunjung=1))
    sesi.commit()

    hasil = pangkas(sesi, SEKARANG)
    assert hasil["uptime_checks"] >= 1
    for model in (UptimeCheck, UptimePutaran, KejadianLogin, LoginGagal, CatatanError):
        assert sesi.query(model).count() == 1, model.__name__
    assert sesi.query(UptimeInsiden).count() == 1
    assert sesi.query(TrafficHarian).count() == 1


def test_job_rutin_lama_dipangkas_job_update_tidak(sesi, site):
    tua = SEKARANG - timedelta(days=15)
    job_selesai(sesi, site, JobType.collect_events, tua)
    job_selesai(sesi, site, JobType.scan_site, tua, JobStatus.failed)
    muda = job_selesai(sesi, site, JobType.collect_traffic, SEKARANG - timedelta(days=13))
    update = job_selesai(sesi, site, JobType.update_package, tua)
    connector = job_selesai(sesi, site, JobType.update_connector, tua)
    tertunda = buat_job(sesi, site.id, JobType.collect_events)

    pangkas(sesi, SEKARANG)
    sisa = {j.id for j in sesi.query(Job).all()}
    assert sisa == {muda.id, update.id, connector.id, tertunda.id}
```

- [ ] **Step 2: Jalankan dan pastikan gagal.**

- [ ] **Step 3: Implementasikan.**

File: `src/wpmgr/retensi.py`
```python
"""Pemangkasan data monitoring (spec §5.1).

Yang dipertahankan permanen: insiden uptime, traffic, job update, dan
activity_log -- semuanya dibutuhkan laporan bulanan dan jejak audit.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from wpmgr.models import (
    CatatanError,
    Job,
    JobStatus,
    JobType,
    KejadianLogin,
    LoginGagal,
    UptimeCheck,
    UptimePutaran,
)

RETENSI_MENTAH = timedelta(days=90)
RETENSI_JOB = timedelta(days=14)
JOB_DIPANGKAS = (JobType.collect_events, JobType.collect_traffic, JobType.scan_site, JobType.verify_site)


def pangkas(sesi: Session, sekarang: datetime) -> dict[str, int]:
    batas = sekarang - RETENSI_MENTAH
    hasil = {
        "uptime_checks": sesi.execute(delete(UptimeCheck).where(UptimeCheck.dicek_pada < batas)).rowcount,
        "uptime_putaran": sesi.execute(delete(UptimePutaran).where(UptimePutaran.mulai < batas)).rowcount,
        "login_events": sesi.execute(delete(KejadianLogin).where(KejadianLogin.waktu < batas)).rowcount,
        "login_gagal": sesi.execute(delete(LoginGagal).where(LoginGagal.jam < batas)).rowcount,
        "site_errors": sesi.execute(
            delete(CatatanError).where(CatatanError.terakhir_terlihat < batas)).rowcount,
        # ±4.800 job pengambilan per hari untuk 40 site; tanpa ini tabel jobs
        # tumbuh ±1,7 juta baris per tahun.
        "jobs": sesi.execute(
            delete(Job).where(
                Job.tipe.in_(JOB_DIPANGKAS),
                Job.status.in_((JobStatus.success, JobStatus.failed)),
                Job.finished_at < sekarang - RETENSI_JOB,
            )
        ).rowcount,
    }
    sesi.commit()
    return hasil
```

Di `cli.py` (impor `KUNCI_RETENSI`, `from wpmgr.retensi import pangkas`):

```python
def prune_monitoring() -> dict | None:
    with kunci_advisory(db.engine, KUNCI_RETENSI) as dapat:
        if not dapat:
            print("Pemangkasan lain masih berjalan; dilewati")
            return None
        with get_session() as sesi:
            hasil = pangkas(sesi, datetime.now(timezone.utc))
    print(", ".join(f"{k}: {v}" for k, v in hasil.items()))
    return hasil
```

beserta `sub.add_parser("prune-monitoring")` dan cabangnya (impor `from datetime import datetime, timezone` bila belum ada).

- [ ] **Step 4: Crontab.** Tambahkan ke `deploy/crontab`, dengan format yang sama seperti baris yang ada (`cd /opt/wpmgr && .venv/bin/python -m wpmgr.cli ... >> /var/log/wpmgr/cron.log 2>&1`):

```
*/5 * * * *  ... check-uptime
*/15 * * * * ... enqueue-monitoring
5   * * * *  ... enqueue-traffic
30  2 * * *  ... check-ssl
45  2 * * *  ... collect-ga4
15  3 * * *  ... prune-monitoring
0   4 1 * *  ... update-geoip
```

- [ ] **Step 5: README.** Perbarui `README.md`:

1. Di paragraf pembuka, tambahkan satu kalimat: dashboard juga memantau uptime, SSL, error PHP, riwayat login, dan traffic setiap site.
2. Di bagian "Menambahkan site dan memasang plugin connector", ganti langkah membuat zip manual dengan: unduh zip dari tautan "Unduh plugin connector" di halaman Tambah Site (`/connector/unduh`), yang disiapkan oleh `python -m wpmgr.cli build-connector`. Pertahankan catatan bahwa `tests/` dan `vendor/` tidak ikut di dalam zip; perintah build sudah mengecualikannya.
3. Tambahkan bagian baru **"## Pemantauan (Lapis 2)"** sebelum "## Keterbatasan yang diketahui", berisi:
   - Apa yang dipantau dan dari mana datanya (tabel singkat: uptime dan SSL dari cron dashboard, error/login/traffic dari connector 2.x lewat job, GA4 dari API Google).
   - **Setiap deploy:** `alembic upgrade head` lalu `python -m wpmgr.cli build-connector`. Tanpa build, tombol "Perbarui connector" dan unduhan zip membalas pesan yang menyuruh menjalankannya.
   - **Sekali setelah deploy pertama:** `python -m wpmgr.cli update-geoip` (setelahnya dijalankan cron setiap bulan).
   - **Memperbarui connector di site klien:** site yang masih memakai connector 1.x perlu satu kali upload manual versi 2.x lewat wp-admin. Setelah itu gunakan aksi massal "Perbarui connector" di halaman Site.
   - **Setup GA4 (opsional):** buat project dan service account di Google Cloud; aktifkan Google Analytics Data API; unduh kunci JSON ke `/opt/wpmgr/ga4-service-account.json`, `chown wpmgr:wpmgr`, `chmod 600`; isi `WPMGR_GA4_CREDENTIALS` di `.env`; untuk tiap site, tambahkan email service account sebagai **Viewer** di property GA4 client (Admin → Property access management), lalu isi **Property ID** (angka, bukan `G-...`) di tab Ringkasan detail site.
   - **Mematikan pemantauan di satu site:** `define( 'WPMGR_DISABLE_MONITORING', true );` di `wp-config.php` site itu. Pembaruan dan SSO tetap berjalan.
   - **Setelan proxy per site:** Pengaturan → WP Manager di wp-admin site, hanya untuk site di balik load balancer sendiri.
   - Semua variabel env baru (`WPMGR_VAR_DIR`, `WPMGR_GEOIP_PATH`, `WPMGR_GA4_CREDENTIALS`) beserta default-nya.
   - Atribusi: data negara dari DB-IP Lite (CC BY 4.0).
4. Di "## Keterbatasan yang diketahui", tambahkan: tanpa notifikasi (masalah hanya terlihat di halaman Kesehatan); site dengan drop-in `wp-content/php-error.php` tidak tertangkap fatal error-nya; mode penangkap "terbatas" bila `mu-plugins` tidak dapat ditulisi; angka traffic plugin dan GA4 memang berbeda; "jumlah pengunjung harian" menghitung ulang pengunjung yang sama di hari berbeda; ambang keamanan adalah konstanta di `src/wpmgr/keamanan.py`.
5. Di "## Struktur repo (ringkas)", tambahkan modul-modul baru dari Peta Berkas rencana ini.
6. Perbarui angka test di bagian "Menjalankan test" dengan angka dari run terakhir di Task 26.

- [ ] **Step 6: Jalankan unit dan integrasi.** Expected: semua lulus.

- [ ] **Step 7: Commit.**

```bash
git add src/wpmgr/retensi.py src/wpmgr/cli.py deploy/crontab README.md tests/integration/test_retensi.py
git commit -m "feat: retensi data monitoring, jadwal cron Lapis 2, dan dokumentasi"
```

### Task 26: E2E Lapis 2 terhadap WordPress asli

**Files:**
- Create: `tests/e2e/test_monitoring.py`
- Modify: `README.md` (angka test), `tests/e2e/conftest.py`, `tests/e2e/test_self_update.py`

**Interfaces:**
- Consumes: semua task sebelumnya; fixture dan helper e2e dari Task 3.

Test di bawah berjalan terhadap WordPress 6.5 di Docker, dengan WordPress yang **sama** dipakai lintas sesi e2e. Karena itu asersi traffic dan login memakai selisih sebelum/sesudah atau penanda unik per run, bukan angka mutlak.

- [ ] **Step 0: Pindahkan helper antrean ke conftest (Ruling R5).** Task 7 menambahkan `_jalankan_sampai_selesai(sesi, job, batas=10)` di `tests/e2e/test_self_update.py`: helper itu memanggil `proses_satu(sesi, "uji-e2e", buat_klien_fn=klien_http)` sampai `job` keluar dari `pending`/`running`, karena handler menjadwalkan job susulan yang lebih tua dan satu panggilan belum tentu mengambil job sasaran. Pindahkan helper itu ke `tests/e2e/conftest.py` sebagai `jalankan_sampai_selesai(sesi, job, batas: int = 10) -> None` (docstring ikut pindah), lalu ganti pemakaiannya di `test_self_update.py`. Pastikan `test_self_update.py` tetap lulus.

- [ ] **Step 1: Tulis test e2e.**

File: `tests/e2e/test_monitoring.py`
```python
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from wpmgr.jobs.queue import buat_job
from wpmgr.keamanan import StatusKeamanan, nilai_keamanan
from wpmgr.models import (
    CatatanError,
    JobStatus,
    JobType,
    KejadianLogin,
    LoginGagal,
    UptimeInsiden,
    UptimeStatus,
)
from wpmgr.uptime import buat_klien_http, cek_satu, jalankan_putaran

from .conftest import (
    WP_URL,
    _wpcli_status,
    hapus_di_kontainer,
    jalankan_sampai_selesai,
    klien_http,
    permintaan_bertanda,
    tulis_di_kontainer,
    wpcli,
)

pytestmark = pytest.mark.e2e

DIR_PLUGIN_FATAL = "/var/www/html/wp-content/plugins/wpmgr-uji-fatal"
ISI_PLUGIN_FATAL = """<?php
/**
 * Plugin Name: WPMGR Uji Fatal
 * Description: Dipasang test e2e; memicu fatal error hanya bila diminta.
 */
add_action( 'init', function () {
    if ( isset( $_GET['wpmgr_uji_fatal'] ) ) {
        wpmgr_fungsi_yang_tidak_ada();
    }
} );
"""


def _job(sesi, site, tipe):
    job = buat_job(sesi, site.id, tipe, max_attempts=1)
    # verify_site menjadwalkan scan_site susulan yang lebih tua; satu panggilan
    # proses_satu() belum tentu mengambil job ini (Ruling R5).
    jalankan_sampai_selesai(sesi, job)
    assert job.status == JobStatus.success, job.error
    sesi.refresh(site)
    return job


@pytest.fixture
def site_siap(sesi, site_terpasang):
    _job(sesi, site_terpasang, JobType.verify_site)
    assert {"events", "traffic", "self_update"} <= set(site_terpasang.fitur)
    return site_terpasang


def test_fatal_error_plugin_tertangkap_dengan_atribusi(sesi, site_siap):
    tulis_di_kontainer(f"{DIR_PLUGIN_FATAL}/wpmgr-uji-fatal.php", ISI_PLUGIN_FATAL)
    try:
        wpcli("plugin", "activate", "wpmgr-uji-fatal")
        assert httpx.get(f"{WP_URL}/?wpmgr_uji_fatal=1", timeout=30).status_code == 500
        _job(sesi, site_siap, JobType.collect_events)
        e = sesi.query(CatatanError).filter_by(site_id=site_siap.id, tingkat="fatal",
                                               komponen_slug="wpmgr-uji-fatal").one()
        assert e.komponen_tipe == "plugin"
        assert "wpmgr_fungsi_yang_tidak_ada" in e.pesan
        assert site_siap.mode_penangkap == "penuh"
    finally:
        _wpcli_status("plugin", "deactivate", "wpmgr-uji-fatal")
        hapus_di_kontainer(DIR_PLUGIN_FATAL)


def _xmlrpc_login(username, password):
    return (
        "<?xml version='1.0'?><methodCall><methodName>wp.getUsersBlogs</methodName><params>"
        f"<param><value><string>{username}</string></value></param>"
        f"<param><value><string>{password}</string></value></param></params></methodCall>"
    )


def _xmlrpc_multicall(username, jumlah):
    panggilan = "".join(
        "<value><struct><member><name>methodName</name><value><string>wp.getUsersBlogs</string></value></member>"
        "<member><name>params</name><value><array><data>"
        f"<value><string>{username}</string></value><value><string>salah{i}</string></value>"
        "</data></array></value></member></struct></value>"
        for i in range(jumlah)
    )
    return ("<?xml version='1.0'?><methodCall><methodName>system.multicall</methodName><params>"
            f"<param><value><array><data>{panggilan}</data></array></value></param></params></methodCall>")


def test_login_gagal_lewat_form_dan_xmlrpc(sesi, site_siap):
    nama = f"penyerang{uuid.uuid4().hex[:6]}"
    for _ in range(3):
        httpx.post(f"{WP_URL}/wp-login.php", data={"log": nama, "pwd": "salah"}, timeout=30)
    for _ in range(2):
        httpx.post(f"{WP_URL}/xmlrpc.php", content=_xmlrpc_login(nama, "salah"),
                   headers={"Content-Type": "text/xml"}, timeout=30)
    # Koreksi #9: satu request multicall = satu kejadian gagal, berapa pun isinya.
    httpx.post(f"{WP_URL}/xmlrpc.php", content=_xmlrpc_multicall(nama, 5),
               headers={"Content-Type": "text/xml"}, timeout=30)

    _job(sesi, site_siap, JobType.collect_events)
    per_jalur = {g.jalur: g.jumlah for g in sesi.query(LoginGagal).filter_by(site_id=site_siap.id, username=nama)}
    assert per_jalur == {"form": 3, "xmlrpc": 3}


def _masuk(client: httpx.Client):
    client.get(f"{WP_URL}/wp-login.php")
    r = client.post(f"{WP_URL}/wp-login.php",
                    data={"log": "admin", "pwd": "admin-uji-123", "testcookie": "1"},
                    cookies={"wordpress_test_cookie": "WP Cookie check"})
    assert r.status_code == 302


def test_login_berhasil_tercatat(sesi, site_siap):
    sebelum = sesi.query(KejadianLogin).filter_by(site_id=site_siap.id, username="admin").count()
    with httpx.Client(timeout=30, follow_redirects=False) as c:
        _masuk(c)
    _job(sesi, site_siap, JobType.collect_events)
    baris = sesi.query(KejadianLogin).filter_by(site_id=site_siap.id, username="admin", jenis="berhasil").all()
    assert len(baris) > sebelum
    assert baris[-1].jalur == "form"


def test_admin_baru_memerahkan_status_tetapi_user_wpmgr_tidak(sesi, site_siap):
    wpcli("eval", "WPMGR_Settings::pastikan_user();")
    nama = f"uji{uuid.uuid4().hex[:6]}"
    wpcli("user", "create", nama, f"{nama}@uji.local", "--role=administrator")
    try:
        _job(sesi, site_siap, JobType.collect_events)
        kejadian = {(k.username, k.jenis) for k in sesi.query(KejadianLogin).filter_by(site_id=site_siap.id)}
        assert (nama, "admin_baru") in kejadian
        assert not any(u == "wpmgr" and j != "berhasil" for u, j in kejadian)
        hasil = nilai_keamanan(sesi, site_siap, datetime.now(timezone.utc))
        assert hasil.status == StatusKeamanan.perlu_diperiksa
    finally:
        _wpcli_status("user", "delete", nama, "--yes")


def _hari_ini(klien):
    # Zona waktu WordPress baru adalah UTC, jadi "hari ini" site = tanggal UTC.
    hari_ini = datetime.now(timezone.utc).date().isoformat()
    for h in klien.traffic()["hari"]:
        if h["tanggal"] == hari_ini:
            return h["total"]
    return {"kunjungan": 0, "pengunjung": 0}


def test_beacon_dan_hit_masuk_ke_traffic(sesi, site_siap):
    halaman = httpx.get(WP_URL, timeout=30).text
    assert "sendBeacon" in halaman and "wpmgr" in halaman

    klien = klien_http(site_siap)
    sebelum = _hari_ini(klien)
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 UjiE2E/{uuid.uuid4().hex[:8]}"
    for path in ("/", "/", "/tentang"):
        r = httpx.post(f"{WP_URL}/wp-json/wpmgr/v1/hit",
                       content=json.dumps({"p": path, "r": "https://www.google.com/"}),
                       headers={"Content-Type": "text/plain", "User-Agent": ua}, timeout=30)
        assert r.status_code == 204

    # Pengunjung yang sedang login tidak dihitung.
    with httpx.Client(timeout=30, follow_redirects=False) as c:
        _masuk(c)
        c.post(f"{WP_URL}/wp-json/wpmgr/v1/hit", content=json.dumps({"p": "/", "r": ""}),
               headers={"Content-Type": "text/plain", "User-Agent": ua + "-login"})

    sesudah = _hari_ini(klien)
    assert sesudah["kunjungan"] - sebelum["kunjungan"] == 3
    assert sesudah["pengunjung"] - sebelum["pengunjung"] == 1

    _job(sesi, site_siap, JobType.collect_traffic)
    assert site_siap.traffic_diambil_pada is not None


def test_header_anti_cache_di_endpoint_pemantauan(sesi, site_siap):
    from wpmgr.crypto import dekripsi_secret

    secret = dekripsi_secret(site_siap.secret_terenkripsi)
    for route in ("/wpmgr/v1/inventory", "/wpmgr/v1/events", "/wpmgr/v1/traffic"):
        r = permintaan_bertanda(site_siap, secret, "GET", route)
        assert r.status_code == 200, route
        assert "no-store" in r.headers["cache-control"], route


def _tunggu_wordpress(batas_detik=120):
    batas = time.time() + batas_detik
    while time.time() < batas:
        try:
            if httpx.get(WP_URL, timeout=5).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    pytest.fail("WordPress tidak kembali dalam waktu yang ditentukan")


def test_uptime_mati_lalu_pulih(sesi, site_siap):
    with buat_klien_http() as http:
        def cek(url):
            return cek_satu(http, url)

        jalankan_putaran(sesi, cek)
        sesi.refresh(site_siap)
        assert site_siap.uptime_status == UptimeStatus.naik

        subprocess.run(["docker", "compose", "stop", "wp"], check=True, capture_output=True)
        try:
            jalankan_putaran(sesi, cek)
            jalankan_putaran(sesi, cek)
            sesi.refresh(site_siap)
            assert site_siap.uptime_status == UptimeStatus.mati
            assert sesi.query(UptimeInsiden).filter_by(site_id=site_siap.id, selesai=None).count() == 1
        finally:
            subprocess.run(["docker", "compose", "start", "wp"], check=True, capture_output=True)
            _tunggu_wordpress()

        jalankan_putaran(sesi, cek)
        sesi.refresh(site_siap)
        assert site_siap.uptime_status == UptimeStatus.naik
        assert sesi.query(UptimeInsiden).filter_by(site_id=site_siap.id, selesai=None).count() == 0
```

- [ ] **Step 2: Jalankan e2e.** Run: `.venv/Scripts/python -m pytest -m e2e -q`. Expected: seluruh e2e lulus (Lapis 1, fondasi Task 3, self-update Task 7, dan 7 test di berkas ini).

Bila sebuah test gagal karena perilaku WordPress berbeda dari yang diasumsikan rencana ini, **jangan** melonggarkan asersinya. Cari baris core yang menjelaskannya, perbaiki kode connector bila asumsi rencana yang salah, dan catat temuan itu di laporan.

- [ ] **Step 3: Jalankan seluruh suite dan lint.**

```bash
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m pytest -m "not integration and not e2e" -q
.venv/Scripts/python -m pytest -m integration -q
.venv/Scripts/python -m pytest -m e2e -q
cd connector && vendor/bin/phpunit
```

Tempel kelima baris ringkasan. Perbarui angka test di README ("Menjalankan test") dengan angka ini.

- [ ] **Step 4: Commit.**

```bash
git add tests/e2e/test_monitoring.py README.md
git commit -m "test: e2e pemantauan Lapis 2 terhadap WordPress asli"
```

- [ ] **Step 5: Verifikasi manual GA4 bersama pengguna** (spec §16.5). Langkah ini tidak dijalankan subagent. Controller menyerahkannya ke pengguna di laporan akhir, bersama instruksi setup GA4 dari README, dan mencatat hasilnya bila pengguna sudah menjalankannya.
