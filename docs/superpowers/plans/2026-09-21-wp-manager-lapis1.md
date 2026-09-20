# WP Manager Lapis 1 — Rencana Implementasi

> **Untuk agentic worker:** REQUIRED SUB-SKILL: gunakan `superpowers:subagent-driven-development` (disarankan) atau `superpowers:executing-plans` untuk mengerjakan rencana ini task demi task. Langkah memakai sintaks checkbox (`- [ ]`) untuk pelacakan.

**Goal:** Membangun dashboard terpusat yang dapat memindai, meng-update, dan membuka wp-admin (SSO) untuk ~10–40 site WordPress client dari satu layar.

**Architecture:** Dashboard FastAPI menyetir (pull-primary); plugin companion PHP di tiap site hanya membuka endpoint REST ber-HMAC dan menunggu diperintah. Semua pekerjaan panjang berjalan sebagai baris job di PostgreSQL yang diambil proses worker terpisah, sehingga selamat dari restart dan dapat dilanjutkan setelah crash. Frontend server-rendered Jinja2 + Alpine.js dengan DataGrid vanilla, tanpa npm dan tanpa build step.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL 16, httpx, cryptography (Fernet), argon2-cffi, Jinja2, Alpine.js 3, DataGrid custom vanilla JS, PHP 7.4+ (plugin WordPress), pytest, PHPUnit, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-20-wp-manager-lapis1-design.md`

## Global Constraints

Setiap task secara implisit tunduk pada seluruh butir di bawah ini.

- **Tidak ada asumsi SSH, WP-CLI, atau root di site client.** Semua aksi terhadap site dilakukan lewat kode PHP yang berjalan di dalam WordPress.
- **Proses web tidak pernah melakukan panggilan HTTP ke site client.** Web hanya menulis baris job dan membalas. Seluruh panggilan keluar milik worker.
- **Satu paket per request HTTP.** Tidak pernah ada batch update dalam satu panggilan ke site.
- **Maksimum satu job berstatus `running` per site**, ditegakkan di SQL pengambilan job.
- **Endpoint `/update` wajib idempoten:** bila versi terpasang sudah sama dengan `ke_versi`, balas sukses tanpa melakukan apa pun.
- **Timeout bukan kegagalan, melainkan `unknown`:** verifikasi keadaan sebenarnya lewat scan ulang, jangan menebak.
- **Kunci HMAC adalah string hex 64 karakter apa adanya (ASCII), bukan hasil decode hex-nya.** Python dan PHP sama-sama memakai string itu sebagai key. Ini menghapus satu kelas bug decode yang gejalanya hanya 401 tanpa petunjuk.
- **Canonical string HMAC:** `METHOD \n PATH \n TIMESTAMP \n NONCE \n sha256_hex(body)`, dipisah LF (`\n`), tanpa newline di akhir, `METHOD` huruf kapital, `PATH` tanpa query string.
- **Jendela timestamp ±300 detik; nonce sekali pakai, transient 600 detik.**
- **Token SSO:** umur 60 detik, sekali pakai, nonce ditandai terpakai **sebelum** cookie login diset.
- **Perbandingan tanda tangan wajib `hmac.compare_digest()` (Python) / `hash_equals()` (PHP).** Tidak pernah `==` atau `===`.
- **Site wajib HTTPS.** URL berskema `http://` ditolak saat pendaftaran.
- **Base64url tanpa padding** di kedua bahasa.
- **Modul web mengakses database lewat `from wpmgr import db` lalu `db.SessionLocal()`**, tidak pernah `from wpmgr.db import SessionLocal`. Nama yang sudah di-impor tidak dapat ditukar oleh `monkeypatch`, sehingga pola impor langsung membuat seluruh test integrasi web menulis ke database sungguhan alih-alih database test.
- **Commit setiap akhir task.** Prefix pesan: `feat:`, `test:`, `chore:`, `fix:`.
- **Setiap commit menyertakan baris:** `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

## Struktur File

Dipetakan lebih dulu supaya batas tanggung jawab tiap file terkunci sebelum task disusun.

```
wp-manager/
├── pyproject.toml                       Dependensi & konfigurasi pytest/ruff
├── docker-compose.yml                   PostgreSQL (dev & test) + WordPress (e2e)
├── alembic.ini
├── .env.example
├── migrations/versions/                 Migrasi Alembic
├── scripts/
│   └── gen_hmac_vectors.py              Pembangkit fixture test vector lintas bahasa
├── src/wpmgr/
│   ├── config.py                        Settings dari environment
│   ├── db.py                            Engine + session factory
│   ├── models.py                        Model SQLAlchemy + enum
│   ├── crypto.py                        Enkripsi/dekripsi secret site (Fernet)
│   ├── signing.py                       Canonical string + sign/verify HMAC
│   ├── sso.py                           Pembuatan token SSO
│   ├── errors.py                         Taksonomi error_class + SiteError
│   ├── site_client.py                   Klien HTTP ke site + klasifikasi error
│   ├── jobs/queue.py                    Claim, selesaikan, retry, backoff
│   ├── jobs/reaper.py                   Pemulihan job yatim
│   ├── jobs/handlers.py                 scan_site, update_package, verify_site
│   ├── worker.py                        Loop worker
│   ├── cli.py                           enqueue-scans, reap-jobs, create-user
│   ├── web/app.py                       App factory + middleware sesi
│   ├── web/auth.py                      Login, logout, dependency sesi
│   ├── web/routes_api.py                JSON API untuk Alpine
│   ├── web/routes_pair.py               /api/pair/confirm (diamankan HMAC)
│   ├── web/routes_pages.py              Halaman Jinja2
│   ├── templates/                       base, login, updates, sites, site_detail,
│   │                                    site_new, activity, settings
│   └── static/
│       ├── vendor/datagrid/             Salinan DataGrid custom (js + css)
│       └── app/                         Wiring Alpine per halaman
├── connector/wp-manager-connector/
│   ├── wp-manager-connector.php         Bootstrap plugin
│   └── includes/
│       ├── class-wpmgr-signing.php      Verifikasi HMAC
│       ├── class-wpmgr-settings.php     Halaman setting + pairing
│       ├── class-wpmgr-rest.php         Registrasi route REST + guard auth
│       ├── class-wpmgr-inventory.php    Pengumpulan core/plugin/tema
│       ├── class-wpmgr-updater.php      Eksekusi update idempoten
│       └── class-wpmgr-sso.php          Handler token SSO
└── tests/
    ├── fixtures/hmac-test-vectors.json  Dibaca test Python DAN test PHP
    ├── unit/                            signing, sso, crypto, errors
    ├── integration/                     queue, reaper, handlers (Postgres asli)
    └── e2e/                             WordPress asli di Docker
```

**Keputusan pemisahan yang perlu dipahami:** `signing.py` dan `class-wpmgr-signing.php` adalah pasangan yang dikunci oleh satu fixture bersama. Keduanya tidak boleh berubah sendirian. `queue.py` hanya mengurus siklus hidup baris job dan tidak tahu apa-apa soal HTTP; `site_client.py` hanya mengurus HTTP dan tidak tahu apa-apa soal job. `handlers.py` yang menjahit keduanya. Pemisahan ini yang membuat job engine bisa diuji tanpa menyentuh jaringan.

---

## Fase 0 — Fondasi

### Task 1: Scaffold proyek, konfigurasi, dan PostgreSQL dev

**Files:**
- Create: `pyproject.toml`, `.env.example`, `docker-compose.yml`, `src/wpmgr/__init__.py`, `src/wpmgr/config.py`, `tests/unit/test_config.py`

**Interfaces:**
- Consumes: —
- Produces: `wpmgr.config.Settings` (pydantic-settings) dengan field `database_url: str`, `secret_key: str`, `base_url: str`, `session_secret: str`; fungsi `get_settings() -> Settings` ber-cache.

- [ ] **Step 1: Buat `pyproject.toml`**

```toml
[project]
name = "wpmgr"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "psycopg[binary]>=3.2",
    "pydantic-settings>=2.4",
    "httpx>=0.27",
    "cryptography>=43",
    "argon2-cffi>=23",
    "jinja2>=3.1",
    "itsdangerous>=2.2",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.6"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: butuh PostgreSQL berjalan",
    "e2e: butuh WordPress di Docker",
]
asyncio_mode = "auto"
```

- [ ] **Step 2: Buat `docker-compose.yml` (bagian PostgreSQL saja)**

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: wpmgr
      POSTGRES_PASSWORD: wpmgr
      POSTGRES_DB: wpmgr
    ports: ["5433:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U wpmgr"]
      interval: 5s
      retries: 10

volumes:
  pgdata:
```

Port 5433 dipakai supaya tidak bentrok dengan PostgreSQL lain yang mungkin sudah berjalan di mesin dev.

- [ ] **Step 3: Buat `.env.example`**

```
DATABASE_URL=postgresql+psycopg://wpmgr:wpmgr@localhost:5433/wpmgr
WPMGR_SECRET_KEY=ganti-dengan-hasil-Fernet.generate_key()
WPMGR_BASE_URL=https://wpmgr.example.com
WPMGR_SESSION_SECRET=ganti-dengan-string-acak-panjang
```

- [ ] **Step 4: Tulis test yang gagal**

```python
# tests/unit/test_config.py
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
```

- [ ] **Step 5: Jalankan test, pastikan gagal**

Run: `pytest tests/unit/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wpmgr.config'`

- [ ] **Step 6: Implementasi minimal**

```python
# src/wpmgr/config.py
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    secret_key: str
    base_url: str
    session_secret: str

    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_prefix="", populate_by_name=True
    )

    @field_validator("base_url")
    @classmethod
    def _tanpa_slash_akhir(cls, v: str) -> str:
        return v.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Field `secret_key`, `base_url`, dan `session_secret` dipetakan dari `WPMGR_SECRET_KEY`, `WPMGR_BASE_URL`, `WPMGR_SESSION_SECRET` lewat alias. Tambahkan alias itu pada masing-masing field menggunakan `Field(alias=...)` bila pydantic tidak memetakannya otomatis, lalu jalankan ulang test sampai keduanya hijau.

- [ ] **Step 7: Jalankan test, pastikan lolos**

Run: `pip install -e ".[dev]" && pytest tests/unit/test_config.py -v`
Expected: PASS, 2 test

- [ ] **Step 8: Nyalakan database dan pastikan sehat**

Run: `docker compose up -d db && docker compose ps`
Expected: service `db` berstatus `healthy`

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml docker-compose.yml .env.example src/wpmgr tests/unit/test_config.py
git commit -m "chore: scaffold proyek wpmgr, konfigurasi env, dan PostgreSQL dev

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Fase 1 — Kontrak Kriptografi

Fase ini didahulukan karena ia adalah satu-satunya kontrak yang harus disepakati dua bahasa. Bila ia salah, seluruh sistem gagal dengan gejala yang sama dan tidak informatif: 401.

### Task 2: Canonical string, signing Python, dan fixture lintas bahasa

**Files:**
- Create: `src/wpmgr/signing.py`, `scripts/gen_hmac_vectors.py`, `tests/unit/test_signing.py`
- Create (dibangkitkan): `tests/fixtures/hmac-test-vectors.json`

**Interfaces:**
- Consumes: —
- Produces:
  - `canonical_string(method: str, path: str, timestamp: int, nonce: str, body: bytes) -> str`
  - `sign(secret_hex: str, method: str, path: str, timestamp: int, nonce: str, body: bytes) -> str` → hex digest
  - `verify(secret_hex: str, signature: str, method: str, path: str, timestamp: int, nonce: str, body: bytes) -> bool`
  - `new_nonce() -> str` → 32 karakter hex

- [ ] **Step 1: Tulis test yang gagal untuk canonical string**

Canonical string adalah kontrak sebenarnya dan bisa diverifikasi manusia tanpa menghitung kriptografi apa pun. Karena itu ia diuji terpisah dari tanda tangannya.

```python
# tests/unit/test_signing.py
import hashlib
import json
from pathlib import Path

import pytest

from wpmgr.signing import canonical_string, new_nonce, sign, verify

SECRET = "a" * 64


def test_canonical_string_bentuk_persis():
    hasil = canonical_string("get", "/wp-json/wpmgr/v1/ping", 1758387600, "deadbeef", b"")
    kosong = hashlib.sha256(b"").hexdigest()
    assert hasil == f"GET\n/wp-json/wpmgr/v1/ping\n1758387600\ndeadbeef\n{kosong}"


def test_canonical_string_tidak_berakhir_newline():
    hasil = canonical_string("POST", "/x", 1, "n", b"{}")
    assert not hasil.endswith("\n")
    assert hasil.count("\n") == 4


def test_canonical_string_membesarkan_method():
    assert canonical_string("post", "/x", 1, "n", b"").startswith("POST\n")


def test_body_berbeda_menghasilkan_canonical_berbeda():
    a = canonical_string("POST", "/x", 1, "n", b'{"a":1}')
    b = canonical_string("POST", "/x", 1, "n", b'{"a":2}')
    assert a != b
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/unit/test_signing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wpmgr.signing'`

- [ ] **Step 3: Implementasi `signing.py`**

```python
# src/wpmgr/signing.py
import hashlib
import hmac
import secrets

JENDELA_DETIK = 300


def canonical_string(
    method: str, path: str, timestamp: int, nonce: str, body: bytes
) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join([method.upper(), path, str(timestamp), nonce, body_hash])


def sign(
    secret_hex: str, method: str, path: str, timestamp: int, nonce: str, body: bytes
) -> str:
    canonical = canonical_string(method, path, timestamp, nonce, body)
    return hmac.new(
        secret_hex.encode("ascii"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify(
    secret_hex: str,
    signature: str,
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
    body: bytes,
) -> bool:
    diharapkan = sign(secret_hex, method, path, timestamp, nonce, body)
    return hmac.compare_digest(diharapkan, signature)


def new_nonce() -> str:
    return secrets.token_hex(16)
```

Perhatikan `secret_hex.encode("ascii")`: kunci adalah string hex apa adanya, bukan `bytes.fromhex(secret_hex)`. PHP akan melakukan hal yang sama, sehingga tidak ada langkah decode yang bisa berbeda di antara keduanya.

- [ ] **Step 4: Jalankan test, pastikan lolos**

Run: `pytest tests/unit/test_signing.py -v`
Expected: PASS, 4 test

- [ ] **Step 5: Tulis pembangkit fixture**

```python
# scripts/gen_hmac_vectors.py
"""Membangkitkan tests/fixtures/hmac-test-vectors.json.

Canonical string di tiap kasus adalah kontrak yang ditulis manusia. Tanda
tangannya dihitung dari implementasi Python, lalu dibaca juga oleh test PHP.
Dengan begitu kedua bahasa terikat pada satu dokumen yang sama, dan perubahan
di salah satu sisi langsung merah di sisi itu.
"""

import json
from pathlib import Path

from wpmgr.signing import canonical_string, sign

KASUS = [
    {
        "nama": "get_ping_body_kosong",
        "secret_hex": "a" * 64,
        "method": "GET",
        "path": "/wp-json/wpmgr/v1/ping",
        "timestamp": 1758387600,
        "nonce": "0123456789abcdef0123456789abcdef",
        "body": "",
    },
    {
        "nama": "get_inventory_body_kosong",
        "secret_hex": "b3" * 32,
        "method": "GET",
        "path": "/wp-json/wpmgr/v1/inventory",
        "timestamp": 1700000000,
        "nonce": "ffffffffffffffffffffffffffffffff",
        "body": "",
    },
    {
        "nama": "post_update_dengan_body",
        "secret_hex": "0f" * 32,
        "method": "POST",
        "path": "/wp-json/wpmgr/v1/update",
        "timestamp": 1758387600,
        "nonce": "00112233445566778899aabbccddeeff",
        "body": '{"tipe":"plugin","slug":"elementor/elementor.php","ke_versi":"3.20.1"}',
    },
    {
        "nama": "body_dengan_karakter_non_ascii",
        "secret_hex": "12" * 32,
        "method": "POST",
        "path": "/wp-json/wpmgr/v1/update",
        "timestamp": 1758387601,
        "nonce": "aabbccddeeff00112233445566778899",
        "body": '{"nama":"Tema Café — Ñandú"}',
    },
]


def main() -> None:
    keluaran = []
    for k in KASUS:
        body = k["body"].encode("utf-8")
        keluaran.append(
            {
                **k,
                "canonical": canonical_string(
                    k["method"], k["path"], k["timestamp"], k["nonce"], body
                ),
                "signature": sign(
                    k["secret_hex"],
                    k["method"],
                    k["path"],
                    k["timestamp"],
                    k["nonce"],
                    body,
                ),
            }
        )
    tujuan = Path(__file__).resolve().parents[1] / "tests/fixtures/hmac-test-vectors.json"
    tujuan.parent.mkdir(parents=True, exist_ok=True)
    tujuan.write_text(json.dumps(keluaran, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Ditulis {len(keluaran)} vector ke {tujuan}")


if __name__ == "__main__":
    main()
```

Kasus keempat memakai karakter non-ASCII dengan sengaja: nama tema berisi aksen adalah hal biasa, dan perbedaan encoding antara Python dan PHP akan tertangkap di situ, bukan di produksi.

- [ ] **Step 6: Bangkitkan fixture**

Run: `python scripts/gen_hmac_vectors.py`
Expected: `Ditulis 4 vector ke .../tests/fixtures/hmac-test-vectors.json`

- [ ] **Step 7: Tambahkan test yang membaca fixture**

```python
# tambahkan ke tests/unit/test_signing.py
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/hmac-test-vectors.json"


def muat_vectors():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("v", muat_vectors(), ids=lambda v: v["nama"])
def test_vector_canonical_cocok(v):
    body = v["body"].encode("utf-8")
    assert canonical_string(v["method"], v["path"], v["timestamp"], v["nonce"], body) == v["canonical"]


@pytest.mark.parametrize("v", muat_vectors(), ids=lambda v: v["nama"])
def test_vector_signature_cocok(v):
    body = v["body"].encode("utf-8")
    assert sign(v["secret_hex"], v["method"], v["path"], v["timestamp"], v["nonce"], body) == v["signature"]


def test_verify_menolak_tanda_tangan_salah():
    assert verify(SECRET, "0" * 64, "GET", "/x", 1, "n", b"") is False


def test_verify_menerima_tanda_tangan_benar():
    s = sign(SECRET, "GET", "/x", 1, "n", b"")
    assert verify(SECRET, s, "GET", "/x", 1, "n", b"") is True


def test_nonce_unik_dan_32_hex():
    a, b = new_nonce(), new_nonce()
    assert a != b
    assert len(a) == 32 and int(a, 16) >= 0
```

- [ ] **Step 8: Jalankan seluruh test signing**

Run: `pytest tests/unit/test_signing.py -v`
Expected: PASS, 13 test

- [ ] **Step 9: Commit**

```bash
git add src/wpmgr/signing.py scripts/gen_hmac_vectors.py tests/unit/test_signing.py tests/fixtures/hmac-test-vectors.json
git commit -m "feat: canonical string dan HMAC signing dengan fixture lintas bahasa

Kunci HMAC adalah string hex apa adanya (ASCII), bukan hasil decode-nya,
supaya tidak ada langkah konversi yang bisa berbeda antara Python dan PHP.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Enkripsi secret site

**Files:**
- Create: `src/wpmgr/crypto.py`, `tests/unit/test_crypto.py`

**Interfaces:**
- Consumes: `wpmgr.config.get_settings`
- Produces:
  - `enkripsi_secret(secret_hex: str) -> bytes`
  - `dekripsi_secret(ciphertext: bytes) -> str`
  - `secret_baru() -> str` → 64 karakter hex (32 byte acak)

- [ ] **Step 1: Tulis test yang gagal**

```python
# tests/unit/test_crypto.py
import pytest
from cryptography.fernet import Fernet, InvalidToken

from wpmgr.crypto import dekripsi_secret, enkripsi_secret, secret_baru


@pytest.fixture(autouse=True)
def _kunci(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://a:b@localhost/c")
    monkeypatch.setenv("WPMGR_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("WPMGR_BASE_URL", "https://contoh.test")
    monkeypatch.setenv("WPMGR_SESSION_SECRET", "rahasia")
    from wpmgr.config import get_settings

    get_settings.cache_clear()


def test_bolak_balik():
    s = secret_baru()
    assert dekripsi_secret(enkripsi_secret(s)) == s


def test_ciphertext_tidak_memuat_plaintext():
    s = secret_baru()
    assert s.encode() not in enkripsi_secret(s)


def test_secret_baru_64_hex_dan_unik():
    a, b = secret_baru(), secret_baru()
    assert len(a) == 64 and a != b
    int(a, 16)


def test_ciphertext_rusak_ditolak():
    with pytest.raises(InvalidToken):
        dekripsi_secret(b"bukan-token-fernet")
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/unit/test_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wpmgr.crypto'`

- [ ] **Step 3: Implementasi**

```python
# src/wpmgr/crypto.py
import secrets

from cryptography.fernet import Fernet

from wpmgr.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().secret_key.encode())


def secret_baru() -> str:
    return secrets.token_hex(32)


def enkripsi_secret(secret_hex: str) -> bytes:
    return _fernet().encrypt(secret_hex.encode("ascii"))


def dekripsi_secret(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode("ascii")
```

- [ ] **Step 4: Jalankan test, pastikan lolos**

Run: `pytest tests/unit/test_crypto.py -v`
Expected: PASS, 4 test

- [ ] **Step 5: Commit**

```bash
git add src/wpmgr/crypto.py tests/unit/test_crypto.py
git commit -m "feat: enkripsi secret site dengan Fernet, kunci dari environment

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Token SSO

**Files:**
- Create: `src/wpmgr/sso.py`, `tests/unit/test_sso.py`

**Interfaces:**
- Consumes: `wpmgr.signing`
- Produces:
  - `b64url_encode(data: bytes) -> str` / `b64url_decode(s: str) -> bytes` (tanpa padding)
  - `buat_token(secret_hex: str, site_id: str, umur_detik: int = 60, now: int | None = None) -> str`
  - `baca_token(secret_hex: str, token: str, now: int | None = None) -> dict` — melempar `TokenTidakValid` bila tanda tangan salah atau kedaluwarsa
  - `class TokenTidakValid(Exception)`

- [ ] **Step 1: Tulis test yang gagal**

```python
# tests/unit/test_sso.py
import pytest

from wpmgr.sso import TokenTidakValid, b64url_decode, b64url_encode, baca_token, buat_token

SECRET = "c" * 64
SITE = "6f1a2b3c-0000-4000-8000-000000000001"


def test_b64url_tanpa_padding_dan_bolak_balik():
    data = b"abcde"
    s = b64url_encode(data)
    assert "=" not in s and "+" not in s and "/" not in s
    assert b64url_decode(s) == data


def test_token_berbentuk_body_titik_signature():
    t = buat_token(SECRET, SITE, now=1_000_000)
    body, _, sig = t.partition(".")
    assert body and sig and len(sig) == 64


def test_token_valid_terbaca_dan_memuat_site_id():
    t = buat_token(SECRET, SITE, now=1_000_000)
    p = baca_token(SECRET, t, now=1_000_030)
    assert p["site_id"] == SITE
    assert p["exp"] == 1_000_060
    assert len(p["nonce"]) == 32


def test_token_kedaluwarsa_ditolak():
    t = buat_token(SECRET, SITE, now=1_000_000)
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, t, now=1_000_061)


def test_token_dengan_secret_lain_ditolak():
    t = buat_token(SECRET, SITE, now=1_000_000)
    with pytest.raises(TokenTidakValid):
        baca_token("d" * 64, t, now=1_000_010)


def test_body_diubah_ditolak():
    t = buat_token(SECRET, SITE, now=1_000_000)
    body, _, sig = t.partition(".")
    rusak = b64url_encode(b'{"site_id":"lain","exp":9999999999,"nonce":"x"}')
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, f"{rusak}.{sig}", now=1_000_010)


def test_dua_token_punya_nonce_berbeda():
    a = baca_token(SECRET, buat_token(SECRET, SITE, now=1_000_000), now=1_000_001)
    b = baca_token(SECRET, buat_token(SECRET, SITE, now=1_000_000), now=1_000_001)
    assert a["nonce"] != b["nonce"]


def test_token_tanpa_titik_ditolak():
    with pytest.raises(TokenTidakValid):
        baca_token(SECRET, "tanpatitik", now=1_000_000)
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/unit/test_sso.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wpmgr.sso'`

- [ ] **Step 3: Implementasi**

```python
# src/wpmgr/sso.py
import base64
import hashlib
import hmac
import json
import time

from wpmgr.signing import new_nonce

UMUR_DEFAULT_DETIK = 60


class TokenTidakValid(Exception):
    pass


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _tanda_tangan(secret_hex: str, body: str) -> str:
    return hmac.new(
        secret_hex.encode("ascii"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()


def buat_token(
    secret_hex: str, site_id: str, umur_detik: int = UMUR_DEFAULT_DETIK, now: int | None = None
) -> str:
    sekarang = int(time.time()) if now is None else now
    payload = {"site_id": site_id, "exp": sekarang + umur_detik, "nonce": new_nonce()}
    body = b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return f"{body}.{_tanda_tangan(secret_hex, body)}"


def baca_token(secret_hex: str, token: str, now: int | None = None) -> dict:
    body, pemisah, sig = token.partition(".")
    if not pemisah or not body or not sig:
        raise TokenTidakValid("bentuk token salah")
    if not hmac.compare_digest(_tanda_tangan(secret_hex, body), sig):
        raise TokenTidakValid("tanda tangan salah")
    try:
        payload = json.loads(b64url_decode(body))
    except Exception as exc:
        raise TokenTidakValid("payload tidak dapat dibaca") from exc
    sekarang = int(time.time()) if now is None else now
    if sekarang > int(payload.get("exp", 0)):
        raise TokenTidakValid("token kedaluwarsa")
    return payload
```

Urutannya disengaja: tanda tangan diverifikasi **sebelum** payload di-decode, sehingga tidak ada JSON dari pihak tak dikenal yang pernah diurai.

- [ ] **Step 4: Jalankan test, pastikan lolos**

Run: `pytest tests/unit/test_sso.py -v`
Expected: PASS, 8 test

- [ ] **Step 5: Commit**

```bash
git add src/wpmgr/sso.py tests/unit/test_sso.py
git commit -m "feat: token SSO sekali-pakai berumur 60 detik

Tanda tangan diverifikasi sebelum payload di-decode.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Klasifikasi error

**Files:**
- Create: `src/wpmgr/errors.py`, `tests/unit/test_errors.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `class SiteError(Exception)` dengan atribut `error_class: str`, `pesan: str`, `dapat_diulang: bool`
  - `klasifikasi_respons(status: int, headers: dict[str, str], body: str) -> str | None` — `None` berarti respons sehat
  - `DAPAT_DIULANG: frozenset[str]`
  - Konstanta: `AUTH_ERROR`, `CONNECTOR_MISSING`, `BLOCKED`, `TRANSIENT`, `BAD_RESPONSE`, `UPGRADE_FAILED`, `UNKNOWN`

- [ ] **Step 1: Tulis test yang gagal**

```python
# tests/unit/test_errors.py
from wpmgr.errors import (
    AUTH_ERROR, BAD_RESPONSE, BLOCKED, CONNECTOR_MISSING, DAPAT_DIULANG,
    TRANSIENT, SiteError, klasifikasi_respons,
)


def test_200_sehat():
    assert klasifikasi_respons(200, {}, '{"ok":true}') is None


def test_401_dari_plugin_adalah_auth_error():
    assert klasifikasi_respons(401, {}, '{"code":"wpmgr_bad_signature"}') == AUTH_ERROR


def test_403_polos_adalah_auth_error():
    assert klasifikasi_respons(403, {}, '{"code":"wpmgr_bad_signature"}') == AUTH_ERROR


def test_403_dengan_header_cloudflare_adalah_blocked():
    assert klasifikasi_respons(403, {"Server": "cloudflare"}, "denied") == BLOCKED


def test_403_dengan_cf_ray_adalah_blocked():
    assert klasifikasi_respons(403, {"CF-RAY": "8a2f"}, "") == BLOCKED


def test_403_menyebut_wordfence_adalah_blocked():
    body = "<html><body>Your access to this site has been limited by Wordfence</body></html>"
    assert klasifikasi_respons(403, {}, body) == BLOCKED


def test_header_tidak_peka_huruf_besar_kecil():
    assert klasifikasi_respons(403, {"server": "Cloudflare"}, "") == BLOCKED


def test_404_adalah_connector_missing():
    assert klasifikasi_respons(404, {}, "Not found") == CONNECTOR_MISSING


def test_500_adalah_transient():
    assert klasifikasi_respons(500, {}, "error") == TRANSIENT


def test_502_adalah_transient():
    assert klasifikasi_respons(502, {}, "") == TRANSIENT


def test_200_tapi_html_adalah_bad_response():
    assert klasifikasi_respons(200, {}, "<!DOCTYPE html><html>") == BAD_RESPONSE


def test_hanya_transient_dan_bad_response_yang_diulang():
    assert DAPAT_DIULANG == frozenset({TRANSIENT, BAD_RESPONSE})
    assert AUTH_ERROR not in DAPAT_DIULANG
    assert BLOCKED not in DAPAT_DIULANG


def test_site_error_membawa_sifat_dapat_diulang():
    assert SiteError(TRANSIENT, "gagal").dapat_diulang is True
    assert SiteError(AUTH_ERROR, "ditolak").dapat_diulang is False
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/unit/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wpmgr.errors'`

- [ ] **Step 3: Implementasi**

```python
# src/wpmgr/errors.py
AUTH_ERROR = "auth_error"
CONNECTOR_MISSING = "connector_missing"
BLOCKED = "blocked"
TRANSIENT = "transient"
BAD_RESPONSE = "bad_response"
UPGRADE_FAILED = "upgrade_failed"
UNKNOWN = "unknown"

DAPAT_DIULANG = frozenset({TRANSIENT, BAD_RESPONSE})

_PENANDA_FIREWALL_BODY = ("wordfence", "cloudflare", "attention required")


class SiteError(Exception):
    def __init__(self, error_class: str, pesan: str) -> None:
        super().__init__(pesan)
        self.error_class = error_class
        self.pesan = pesan

    @property
    def dapat_diulang(self) -> bool:
        return self.error_class in DAPAT_DIULANG


def _terlihat_firewall(headers: dict[str, str], body: str) -> bool:
    rendah = {k.lower(): (v or "").lower() for k, v in headers.items()}
    if "cf-ray" in rendah:
        return True
    if "cloudflare" in rendah.get("server", ""):
        return True
    cuplikan = body[:2000].lower()
    return any(p in cuplikan for p in _PENANDA_FIREWALL_BODY)


def klasifikasi_respons(status: int, headers: dict[str, str], body: str) -> str | None:
    if status in (401, 403):
        return BLOCKED if _terlihat_firewall(headers, body) else AUTH_ERROR
    if status == 404:
        return CONNECTOR_MISSING
    if status >= 500:
        return TRANSIENT
    if status == 200 and body.lstrip()[:9].lower().startswith("<!doctype"):
        return BAD_RESPONSE
    if status == 200 and body.lstrip().startswith("<html"):
        return BAD_RESPONSE
    return None
```

Membedakan 403 dari plugin (salah tanda tangan, tidak perlu diulang) dengan 403 dari firewall (IP diblokir, butuh tindakan manual yang sama sekali berbeda) adalah alasan fungsi ini ada. Tanpa pembedaan itu, pesan yang sampai ke pengguna akan menyuruh menyambung ulang site padahal yang perlu dilakukan adalah meng-allowlist IP.

- [ ] **Step 4: Jalankan test, pastikan lolos**

Run: `pytest tests/unit/test_errors.py -v`
Expected: PASS, 13 test

- [ ] **Step 5: Commit**

```bash
git add src/wpmgr/errors.py tests/unit/test_errors.py
git commit -m "feat: taksonomi error_class dan klasifikasi respons site

Membedakan 403 dari plugin (auth_error) dengan 403 dari firewall (blocked),
karena tindakan perbaikannya berbeda total.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Fase 2 — Model Data

### Task 6: Model SQLAlchemy dan migrasi Alembic

**Files:**
- Create: `src/wpmgr/db.py`, `src/wpmgr/models.py`, `alembic.ini`, `migrations/env.py`, `tests/integration/conftest.py`, `tests/integration/test_models.py`
- Create (dibangkitkan): `migrations/versions/<rev>_awal.py`

**Interfaces:**
- Consumes: `wpmgr.config.get_settings`
- Produces:
  - `wpmgr.db`: `engine`, `SessionLocal`, `get_session()` (context manager)
  - `wpmgr.models`: `Base`, `User`, `Client`, `Site`, `SitePackage`, `Job`, `ActivityLog`
  - Enum Python: `SiteStatus`, `PackageType`, `JobType`, `JobStatus` (semua `str, enum.Enum`)
  - Nilai enum persis seperti spec bagian 5.

- [ ] **Step 1: Tulis `db.py`**

```python
# src/wpmgr/db.py
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
```

- [ ] **Step 2: Tulis `models.py`**

```python
# src/wpmgr/models.py
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, LargeBinary,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SiteStatus(str, enum.Enum):
    pending_pair = "pending_pair"
    active = "active"
    needs_reconnect = "needs_reconnect"
    blocked = "blocked"
    unreachable = "unreachable"
    disabled = "disabled"


class PackageType(str, enum.Enum):
    core = "core"
    plugin = "plugin"
    theme = "theme"


class JobType(str, enum.Enum):
    scan_site = "scan_site"
    update_package = "update_package"
    verify_site = "verify_site"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    unknown = "unknown"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    nama: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="admin")
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    nama: Mapped[str] = mapped_column(Text, nullable=False)
    kontak: Mapped[str | None] = mapped_column(Text)
    catatan: Mapped[str | None] = mapped_column(Text)
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL")
    )
    nama: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[SiteStatus] = mapped_column(
        Enum(SiteStatus, name="site_status"), nullable=False, default=SiteStatus.pending_pair
    )
    secret_terenkripsi: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    connector_version: Mapped[str | None] = mapped_column(Text)
    wp_version: Mapped[str | None] = mapped_column(Text)
    php_version: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SitePackage(Base):
    __tablename__ = "site_packages"
    __table_args__ = (UniqueConstraint("site_id", "tipe", "slug", name="uq_site_package"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tipe: Mapped[PackageType] = mapped_column(Enum(PackageType, name="package_type"), nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    nama: Mapped[str] = mapped_column(Text, nullable=False)
    versi_terpasang: Mapped[str] = mapped_column(Text, nullable=False)
    versi_tersedia: Mapped[str | None] = mapped_column(Text)
    aktif: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_update: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_scan_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    tipe: Mapped[JobType] = mapped_column(Enum(JobType, name="job_type"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), nullable=False, default=JobStatus.pending
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hasil: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    error_class: Mapped[str | None] = mapped_column(Text)
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dibuat_oleh: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class ActivityLog(Base):
    __tablename__ = "activity_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    level: Mapped[str] = mapped_column(Text, nullable=False)
    pesan: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 3: Inisialisasi Alembic dan arahkan ke metadata**

Run: `alembic init -t generic migrations`

Lalu ubah `alembic.ini` agar `sqlalchemy.url` kosong, dan ganti bagian konfigurasi pada `migrations/env.py` menjadi:

```python
from wpmgr.config import get_settings
from wpmgr.models import Base

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata
```

- [ ] **Step 4: Bangkitkan dan periksa migrasi**

Run: `alembic revision --autogenerate -m "awal"`

Buka file hasil di `migrations/versions/`. Pastikan ia memuat pembuatan empat tipe ENUM (`site_status`, `package_type`, `job_type`, `job_status`) dan enam tabel. Alembic kadang melewatkan pembuatan ENUM pada autogenerate; bila tipe-tipe itu tidak ada, tambahkan di awal `upgrade()`:

```python
sa.Enum("pending_pair", "active", "needs_reconnect", "blocked", "unreachable",
        "disabled", name="site_status").create(op.get_bind())
sa.Enum("core", "plugin", "theme", name="package_type").create(op.get_bind())
sa.Enum("scan_site", "update_package", "verify_site", name="job_type").create(op.get_bind())
sa.Enum("pending", "running", "success", "failed", "unknown", name="job_status").create(op.get_bind())
```

dan `.drop(op.get_bind())` untuk masing-masing di akhir `downgrade()`.

- [ ] **Step 5: Jalankan migrasi**

Run: `alembic upgrade head`
Expected: selesai tanpa error

Verifikasi: `docker compose exec db psql -U wpmgr -c "\dt"` menampilkan enam tabel.

- [ ] **Step 6: Tulis conftest integrasi**

```python
# tests/integration/conftest.py
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from wpmgr.models import Base, Site, SiteStatus

DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://wpmgr:wpmgr@localhost:5433/wpmgr_test"
)


@pytest.fixture(scope="session")
def engine():
    dasar = DB_URL.rsplit("/", 1)[0] + "/postgres"
    adm = create_engine(dasar, isolation_level="AUTOCOMMIT", future=True)
    nama = DB_URL.rsplit("/", 1)[1]
    with adm.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{nama}"'))
        c.execute(text(f'CREATE DATABASE "{nama}"'))
    adm.dispose()

    e = create_engine(DB_URL, future=True)
    Base.metadata.create_all(e)
    yield e
    e.dispose()


@pytest.fixture
def sesi(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = Session()
    yield s
    s.rollback()
    for tabel in reversed(Base.metadata.sorted_tables):
        s.execute(text(f'TRUNCATE TABLE "{tabel.name}" CASCADE'))
    s.commit()
    s.close()


@pytest.fixture
def site(sesi):
    s = Site(
        id=uuid.uuid4(),
        nama="Contoh",
        url=f"https://contoh-{uuid.uuid4().hex[:8]}.test",
        status=SiteStatus.active,
        secret_terenkripsi=b"x",
    )
    sesi.add(s)
    sesi.commit()
    return s
```

- [ ] **Step 7: Tulis test integrasi model**

```python
# tests/integration/test_models.py
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from wpmgr.models import Job, JobStatus, JobType, PackageType, SitePackage

pytestmark = pytest.mark.integration


def test_site_tersimpan_dengan_default_status(sesi, site):
    assert site.id is not None
    assert site.dibuat_pada is not None


def test_paket_unik_per_site_tipe_slug(sesi, site):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for _ in range(2):
        sesi.add(
            SitePackage(
                site_id=site.id, tipe=PackageType.plugin, slug="a/a.php",
                nama="A", versi_terpasang="1.0", last_scan_at=now,
            )
        )
    with pytest.raises(IntegrityError):
        sesi.commit()


def test_job_default_pending_dan_attempts_nol(sesi, site):
    j = Job(site_id=site.id, tipe=JobType.scan_site)
    sesi.add(j)
    sesi.commit()
    assert j.status == JobStatus.pending
    assert j.attempts == 0
    assert j.max_attempts == 3


def test_menghapus_site_menghapus_paketnya(sesi, site):
    from datetime import datetime, timezone

    sesi.add(
        SitePackage(
            site_id=site.id, tipe=PackageType.core, slug="core", nama="WordPress",
            versi_terpasang="6.5", last_scan_at=datetime.now(timezone.utc),
        )
    )
    sesi.commit()
    sesi.delete(site)
    sesi.commit()
    assert sesi.query(SitePackage).count() == 0
```

- [ ] **Step 8: Jalankan test integrasi**

Run: `pytest tests/integration/test_models.py -v -m integration`
Expected: PASS, 4 test

- [ ] **Step 9: Commit**

```bash
git add src/wpmgr/db.py src/wpmgr/models.py alembic.ini migrations tests/integration
git commit -m "feat: model SQLAlchemy dan migrasi awal untuk enam tabel

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Fase 3 — Job Engine

### Task 7: Pengambilan job dengan SKIP LOCKED dan aturan satu job per site

**Files:**
- Create: `src/wpmgr/jobs/__init__.py`, `src/wpmgr/jobs/queue.py`, `tests/integration/test_queue.py`

**Interfaces:**
- Consumes: `wpmgr.models`
- Produces:
  - `ambil_job(sesi: Session, worker_id: str) -> Job | None`
  - `buat_job(sesi, site_id: uuid.UUID, tipe: JobType, payload: dict | None = None, dibuat_oleh=None, scheduled_for=None) -> Job`
  - `worker_id() -> str` → `"<hostname>:<pid>"`

- [ ] **Step 1: Tulis test yang gagal**

```python
# tests/integration/test_queue.py
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from wpmgr.jobs.queue import ambil_job, buat_job, worker_id
from wpmgr.models import Job, JobStatus, JobType, Site, SiteStatus

pytestmark = pytest.mark.integration


def _site(sesi, nama="S"):
    s = Site(id=uuid.uuid4(), nama=nama, url=f"https://{uuid.uuid4().hex[:8]}.test",
             status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.commit()
    return s


def test_mengambil_job_pending_dan_menandainya_running(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    assert j is not None
    assert j.status == JobStatus.running
    assert j.locked_by == "w1"
    assert j.attempts == 1
    assert j.locked_at is not None and j.started_at is not None


def test_tidak_mengambil_job_terjadwal_masa_depan(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site,
             scheduled_for=datetime.now(timezone.utc) + timedelta(minutes=10))
    assert ambil_job(sesi, "w1") is None


def test_hanya_satu_job_berjalan_per_site(sesi, site):
    buat_job(sesi, site.id, JobType.update_package, {"slug": "a"})
    buat_job(sesi, site.id, JobType.update_package, {"slug": "b"})
    assert ambil_job(sesi, "w1") is not None
    assert ambil_job(sesi, "w2") is None, "site yang sama tidak boleh punya dua job running"


def test_site_lain_tetap_berjalan_paralel(sesi, site):
    lain = _site(sesi, "Lain")
    buat_job(sesi, site.id, JobType.scan_site)
    buat_job(sesi, lain.id, JobType.scan_site)
    a = ambil_job(sesi, "w1")
    b = ambil_job(sesi, "w2")
    assert a is not None and b is not None
    assert a.site_id != b.site_id


def test_dua_worker_tidak_pernah_mengambil_job_yang_sama(engine, sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s1, s2 = Session(), Session()
    try:
        a = ambil_job(s1, "w1")
        b = ambil_job(s2, "w2")
        assert a is not None
        assert b is None
    finally:
        s1.close()
        s2.close()


def test_antrean_kosong_mengembalikan_none(sesi):
    assert ambil_job(sesi, "w1") is None


def test_urutan_berdasarkan_scheduled_for(sesi, site):
    lama = buat_job(sesi, site.id, JobType.scan_site,
                    scheduled_for=datetime.now(timezone.utc) - timedelta(hours=2))
    buat_job(sesi, site.id, JobType.scan_site,
             scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert ambil_job(sesi, "w1").id == lama.id


def test_worker_id_berbentuk_host_titikdua_pid():
    assert ":" in worker_id()
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/integration/test_queue.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'wpmgr.jobs'`

- [ ] **Step 3: Implementasi `queue.py`**

```python
# src/wpmgr/jobs/queue.py
import os
import socket
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from wpmgr.models import Job, JobStatus, JobType

SQL_AMBIL = text(
    """
    UPDATE jobs
       SET status       = 'running',
           locked_at    = now(),
           locked_by    = :worker,
           started_at   = now(),
           attempts     = attempts + 1
     WHERE id = (
           SELECT j.id
             FROM jobs j
            WHERE j.status = 'pending'
              AND j.scheduled_for <= now()
              AND NOT EXISTS (
                    SELECT 1 FROM jobs j2
                     WHERE j2.site_id = j.site_id
                       AND j2.status = 'running')
            ORDER BY j.scheduled_for
              FOR UPDATE SKIP LOCKED
            LIMIT 1)
    RETURNING id
    """
)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def buat_job(
    sesi: Session,
    site_id: uuid.UUID,
    tipe: JobType,
    payload: dict | None = None,
    dibuat_oleh: uuid.UUID | None = None,
    scheduled_for: datetime | None = None,
) -> Job:
    job = Job(site_id=site_id, tipe=tipe, payload=payload or {}, dibuat_oleh=dibuat_oleh)
    if scheduled_for is not None:
        job.scheduled_for = scheduled_for
    sesi.add(job)
    sesi.commit()
    return job


def ambil_job(sesi: Session, worker: str) -> Job | None:
    baris = sesi.execute(SQL_AMBIL, {"worker": worker}).first()
    sesi.commit()
    if baris is None:
        return None
    return sesi.get(Job, baris[0])
```

`UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)` adalah idiom antrean PostgreSQL yang benar. Subquery mengunci satu baris dan melewati baris yang sudah dipegang transaksi lain, sehingga dua worker tidak pernah bertabrakan tanpa satu pun `sleep` atau retry di kode aplikasi.

- [ ] **Step 4: Jalankan test, pastikan lolos**

Run: `pytest tests/integration/test_queue.py -v -m integration`
Expected: PASS, 8 test

- [ ] **Step 5: Commit**

```bash
git add src/wpmgr/jobs tests/integration/test_queue.py
git commit -m "feat: pengambilan job dengan FOR UPDATE SKIP LOCKED

Aturan maksimum satu job running per site ditegakkan di SQL, karena
Plugin_Upgrader WordPress tidak aman dijalankan bersamaan pada satu site.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Penyelesaian job, backoff, dan reaper

**Files:**
- Modify: `src/wpmgr/jobs/queue.py`
- Create: `src/wpmgr/jobs/reaper.py`, `tests/integration/test_penyelesaian.py`, `tests/integration/test_reaper.py`

**Interfaces:**
- Consumes: `ambil_job`, `buat_job` (Task 7), `wpmgr.errors`
- Produces:
  - `selesai_sukses(sesi, job: Job, hasil: dict) -> None`
  - `selesai_gagal(sesi, job: Job, error_class: str, pesan: str) -> None` — mengulang bila `error_class` ada di `DAPAT_DIULANG` dan jatah tersisa, selain itu `failed`
  - `tandai_unknown(sesi, job: Job, pesan: str) -> None`
  - `jeda_menit(attempts: int) -> int` → `2 ** attempts`
  - `reaper.pulihkan_job_yatim(sesi, batas_menit: int = 15) -> int` → jumlah job yang dipulihkan

- [ ] **Step 1: Tulis test penyelesaian yang gagal**

```python
# tests/integration/test_penyelesaian.py
import pytest

from wpmgr.errors import AUTH_ERROR, TRANSIENT
from wpmgr.jobs.queue import (
    ambil_job, buat_job, jeda_menit, selesai_gagal, selesai_sukses, tandai_unknown,
)
from wpmgr.models import JobStatus, JobType

pytestmark = pytest.mark.integration


def test_backoff_eksponensial():
    assert jeda_menit(1) == 2
    assert jeda_menit(2) == 4
    assert jeda_menit(3) == 8


def test_sukses_menyimpan_hasil_dan_waktu_selesai(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    selesai_sukses(sesi, j, {"jumlah": 12})
    sesi.refresh(j)
    assert j.status == JobStatus.success
    assert j.hasil == {"jumlah": 12}
    assert j.finished_at is not None
    assert j.locked_by is None


def test_error_transient_dijadwalkan_ulang(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    sebelum = j.scheduled_for
    selesai_gagal(sesi, j, TRANSIENT, "koneksi ditolak")
    sesi.refresh(j)
    assert j.status == JobStatus.pending
    assert j.scheduled_for > sebelum
    assert j.error_class == TRANSIENT


def test_auth_error_langsung_failed_tanpa_retry(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    selesai_gagal(sesi, j, AUTH_ERROR, "tanda tangan ditolak")
    sesi.refresh(j)
    assert j.status == JobStatus.failed
    assert j.attempts == 1, "tidak boleh ada percobaan tambahan"


def test_transient_menjadi_failed_setelah_jatah_habis(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    for _ in range(3):
        j = ambil_job(sesi, "w1")
        assert j is not None
        j.scheduled_for = j.dibuat_pada
        selesai_gagal(sesi, j, TRANSIENT, "gagal")
        sesi.refresh(j)
        if j.status == JobStatus.pending:
            j.scheduled_for = j.dibuat_pada
            sesi.commit()
    assert j.status == JobStatus.failed
    assert j.attempts == 3


def test_unknown_melepas_kunci_tapi_tidak_menjadwal_ulang(sesi, site):
    buat_job(sesi, site.id, JobType.update_package, {"slug": "a"})
    j = ambil_job(sesi, "w1")
    tandai_unknown(sesi, j, "timeout")
    sesi.refresh(j)
    assert j.status == JobStatus.unknown
    assert j.locked_by is None
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/integration/test_penyelesaian.py -v -m integration`
Expected: FAIL — `ImportError: cannot import name 'selesai_sukses'`

- [ ] **Step 3: Tambahkan fungsi penyelesaian ke `queue.py`**

```python
# tambahkan ke src/wpmgr/jobs/queue.py
from datetime import timedelta

from sqlalchemy import func as safunc

from wpmgr.errors import DAPAT_DIULANG


def jeda_menit(attempts: int) -> int:
    return 2**attempts


def _lepas_kunci(job: Job) -> None:
    job.locked_at = None
    job.locked_by = None


def selesai_sukses(sesi: Session, job: Job, hasil: dict) -> None:
    job.status = JobStatus.success
    job.hasil = hasil
    job.error = None
    job.error_class = None
    job.finished_at = safunc.now()
    _lepas_kunci(job)
    sesi.commit()


def selesai_gagal(sesi: Session, job: Job, error_class: str, pesan: str) -> None:
    job.error_class = error_class
    job.error = pesan[:2000]
    _lepas_kunci(job)
    boleh_ulang = error_class in DAPAT_DIULANG and job.attempts < job.max_attempts
    if boleh_ulang:
        job.status = JobStatus.pending
        job.scheduled_for = safunc.now() + timedelta(minutes=jeda_menit(job.attempts))
        job.started_at = None
    else:
        job.status = JobStatus.failed
        job.finished_at = safunc.now()
    sesi.commit()


def tandai_unknown(sesi: Session, job: Job, pesan: str) -> None:
    job.status = JobStatus.unknown
    job.error_class = "unknown"
    job.error = pesan[:2000]
    _lepas_kunci(job)
    sesi.commit()
```

- [ ] **Step 4: Jalankan test penyelesaian, pastikan lolos**

Run: `pytest tests/integration/test_penyelesaian.py -v -m integration`
Expected: PASS, 6 test

- [ ] **Step 5: Tulis test reaper yang gagal**

```python
# tests/integration/test_reaper.py
from datetime import datetime, timedelta, timezone

import pytest

from wpmgr.jobs.queue import ambil_job, buat_job
from wpmgr.jobs.reaper import pulihkan_job_yatim
from wpmgr.models import JobStatus, JobType

pytestmark = pytest.mark.integration


def test_job_running_yang_masih_segar_tidak_disentuh(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    assert pulihkan_job_yatim(sesi) == 0
    sesi.refresh(j)
    assert j.status == JobStatus.running


def test_job_yatim_dikembalikan_ke_pending(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    j.locked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    sesi.commit()
    assert pulihkan_job_yatim(sesi) == 1
    sesi.refresh(j)
    assert j.status == JobStatus.pending
    assert j.locked_by is None


def test_job_yatim_tanpa_jatah_menjadi_unknown(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    j.attempts = j.max_attempts
    j.locked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    sesi.commit()
    assert pulihkan_job_yatim(sesi) == 1
    sesi.refresh(j)
    assert j.status == JobStatus.unknown


def test_reaper_menulis_activity_log(sesi, site):
    from wpmgr.models import ActivityLog

    buat_job(sesi, site.id, JobType.scan_site)
    j = ambil_job(sesi, "w1")
    j.locked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    sesi.commit()
    pulihkan_job_yatim(sesi)
    assert sesi.query(ActivityLog).filter_by(job_id=j.id).count() == 1
```

- [ ] **Step 6: Implementasi `reaper.py`**

```python
# src/wpmgr/jobs/reaper.py
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wpmgr.models import ActivityLog, Job, JobStatus

BATAS_MENIT_DEFAULT = 15


def pulihkan_job_yatim(sesi: Session, batas_menit: int = BATAS_MENIT_DEFAULT) -> int:
    batas = func.now() - timedelta(minutes=batas_menit)
    yatim = sesi.scalars(
        select(Job).where(Job.status == JobStatus.running, Job.locked_at < batas)
    ).all()

    for job in yatim:
        pemegang = job.locked_by
        job.locked_at = None
        job.locked_by = None
        job.started_at = None
        if job.attempts < job.max_attempts:
            job.status = JobStatus.pending
            pesan = f"Job dipulihkan dari worker yang mati ({pemegang}); dijadwalkan ulang"
        else:
            job.status = JobStatus.unknown
            job.error_class = "unknown"
            job.error = f"Worker {pemegang} berhenti dan jatah percobaan habis"
            pesan = f"Job ditinggalkan worker {pemegang} tanpa sisa percobaan"
        sesi.add(
            ActivityLog(
                site_id=job.site_id, job_id=job.id, level="warning", pesan=pesan,
                detail={"attempts": job.attempts, "locked_by": pemegang},
            )
        )

    sesi.commit()
    return len(yatim)
```

- [ ] **Step 7: Jalankan test reaper, pastikan lolos**

Run: `pytest tests/integration/test_reaper.py -v -m integration`
Expected: PASS, 4 test

- [ ] **Step 8: Commit**

```bash
git add src/wpmgr/jobs tests/integration/test_penyelesaian.py tests/integration/test_reaper.py
git commit -m "feat: penyelesaian job, backoff eksponensial, dan reaper job yatim

Hanya error transient dan bad_response yang diulang; auth_error dan blocked
langsung failed karena mengulanginya tidak akan pernah berhasil.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Klien HTTP ke site client

**Files:**
- Create: `src/wpmgr/site_client.py`, `tests/unit/test_site_client.py`

**Interfaces:**
- Consumes: `wpmgr.signing`, `wpmgr.errors`
- Produces:
  - `class SiteClient` dengan konstruktor `(base_url: str, site_id: str, secret_hex: str, client: httpx.Client | None = None)`
  - `.ping(timeout: float = 15) -> dict`
  - `.inventory(timeout: float = 60) -> dict`
  - `.update(tipe: str, slug: str, ke_versi: str, timeout: float = 180) -> dict`
  - Melempar `SiteError` dengan `error_class` sesuai taksonomi; timeout melempar `SiteError(UNKNOWN, ...)`
  - `TIMEOUT_PING = 15`, `TIMEOUT_INVENTORY = 60`, `TIMEOUT_UPDATE = 180`

- [ ] **Step 1: Tulis test yang gagal**

Test memakai `httpx.MockTransport`, sehingga tidak ada jaringan yang disentuh dan seluruh perilaku tetap teruji.

```python
# tests/unit/test_site_client.py
import json

import httpx
import pytest

from wpmgr.errors import AUTH_ERROR, BLOCKED, CONNECTOR_MISSING, TRANSIENT, UNKNOWN, SiteError
from wpmgr.signing import verify
from wpmgr.site_client import SiteClient

SECRET = "e" * 64
SITE_ID = "11111111-2222-4333-8444-555555555555"
BASE = "https://contoh.test"


def buat_klien(handler):
    return SiteClient(BASE, SITE_ID, SECRET, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_ping_mengembalikan_json():
    def handler(request):
        return httpx.Response(200, json={"connector_version": "1.0", "wp_version": "6.5"})

    assert buat_klien(handler).ping()["wp_version"] == "6.5"


def test_request_membawa_empat_header_dan_tanda_tangan_sah():
    ditangkap = {}

    def handler(request):
        ditangkap.update(request.headers)
        ditangkap["_body"] = request.content
        return httpx.Response(200, json={})

    buat_klien(handler).inventory()

    assert ditangkap["x-wpmgr-site"] == SITE_ID
    assert len(ditangkap["x-wpmgr-nonce"]) == 32
    assert verify(
        SECRET,
        ditangkap["x-wpmgr-signature"],
        "GET",
        "/wp-json/wpmgr/v1/inventory",
        int(ditangkap["x-wpmgr-timestamp"]),
        ditangkap["x-wpmgr-nonce"],
        ditangkap["_body"],
    )


def test_update_mengirim_body_json_dan_menandatanganinya():
    ditangkap = {}

    def handler(request):
        ditangkap["body"] = request.content
        ditangkap["sig"] = request.headers["x-wpmgr-signature"]
        ditangkap["ts"] = int(request.headers["x-wpmgr-timestamp"])
        ditangkap["nonce"] = request.headers["x-wpmgr-nonce"]
        return httpx.Response(200, json={"ok": True, "versi_sesudah": "3.20.1"})

    hasil = buat_klien(handler).update("plugin", "elementor/elementor.php", "3.20.1")

    assert hasil["versi_sesudah"] == "3.20.1"
    assert json.loads(ditangkap["body"])["ke_versi"] == "3.20.1"
    assert verify(SECRET, ditangkap["sig"], "POST", "/wp-json/wpmgr/v1/update",
                  ditangkap["ts"], ditangkap["nonce"], ditangkap["body"])


def test_nonce_berbeda_tiap_request():
    nonces = []

    def handler(request):
        nonces.append(request.headers["x-wpmgr-nonce"])
        return httpx.Response(200, json={})

    k = buat_klien(handler)
    k.ping()
    k.ping()
    assert nonces[0] != nonces[1]


@pytest.mark.parametrize(
    "status,headers,body,diharapkan",
    [
        (401, {}, '{"code":"x"}', AUTH_ERROR),
        (403, {"Server": "cloudflare"}, "denied", BLOCKED),
        (404, {}, "not found", CONNECTOR_MISSING),
        (500, {}, "boom", TRANSIENT),
    ],
)
def test_status_error_menjadi_site_error(status, headers, body, diharapkan):
    def handler(request):
        return httpx.Response(status, headers=headers, content=body)

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).ping()
    assert exc.value.error_class == diharapkan


def test_timeout_menjadi_unknown():
    def handler(request):
        raise httpx.ReadTimeout("kehabisan waktu", request=request)

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).update("plugin", "a/a.php", "1.0")
    assert exc.value.error_class == UNKNOWN


def test_koneksi_gagal_menjadi_transient():
    def handler(request):
        raise httpx.ConnectError("tidak dapat terhubung", request=request)

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).ping()
    assert exc.value.error_class == TRANSIENT


def test_respons_bukan_json_menjadi_bad_response():
    def handler(request):
        return httpx.Response(200, content="<!DOCTYPE html><html>halo</html>")

    with pytest.raises(SiteError) as exc:
        buat_klien(handler).ping()
    assert exc.value.error_class == "bad_response"


def test_url_http_ditolak_saat_konstruksi():
    with pytest.raises(ValueError):
        SiteClient("http://tidak-aman.test", SITE_ID, SECRET)
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/unit/test_site_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wpmgr.site_client'`

- [ ] **Step 3: Implementasi**

```python
# src/wpmgr/site_client.py
import json
import time

import httpx

from wpmgr.errors import BAD_RESPONSE, TRANSIENT, UNKNOWN, SiteError, klasifikasi_respons
from wpmgr.signing import new_nonce, sign

PREFIX = "/wp-json/wpmgr/v1"
TIMEOUT_PING = 15.0
TIMEOUT_INVENTORY = 60.0
TIMEOUT_UPDATE = 180.0


class SiteClient:
    def __init__(
        self, base_url: str, site_id: str, secret_hex: str, client: httpx.Client | None = None
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("URL site wajib berskema https://")
        self.base_url = base_url.rstrip("/")
        self.site_id = site_id
        self.secret_hex = secret_hex
        self._client = client or httpx.Client(follow_redirects=False)

    def _panggil(self, method: str, path: str, body: bytes, timeout: float) -> dict:
        timestamp = int(time.time())
        nonce = new_nonce()
        headers = {
            "X-Wpmgr-Site": self.site_id,
            "X-Wpmgr-Timestamp": str(timestamp),
            "X-Wpmgr-Nonce": nonce,
            "X-Wpmgr-Signature": sign(self.secret_hex, method, path, timestamp, nonce, body),
            "Accept": "application/json",
        }
        if body:
            headers["Content-Type"] = "application/json"

        try:
            resp = self._client.request(
                method, f"{self.base_url}{path}", content=body or None,
                headers=headers, timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise SiteError(UNKNOWN, f"timeout setelah {timeout} detik") from exc
        except httpx.HTTPError as exc:
            raise SiteError(TRANSIENT, f"kesalahan koneksi: {exc}") from exc

        teks = resp.text
        kelas = klasifikasi_respons(resp.status_code, dict(resp.headers), teks)
        if kelas is not None:
            raise SiteError(kelas, teks[:500])

        try:
            return json.loads(teks)
        except ValueError as exc:
            raise SiteError(BAD_RESPONSE, teks[:500]) from exc

    def ping(self, timeout: float = TIMEOUT_PING) -> dict:
        return self._panggil("GET", f"{PREFIX}/ping", b"", timeout)

    def inventory(self, timeout: float = TIMEOUT_INVENTORY) -> dict:
        return self._panggil("GET", f"{PREFIX}/inventory", b"", timeout)

    def update(self, tipe: str, slug: str, ke_versi: str, timeout: float = TIMEOUT_UPDATE) -> dict:
        body = json.dumps(
            {"tipe": tipe, "slug": slug, "ke_versi": ke_versi}, separators=(",", ":")
        ).encode("utf-8")
        return self._panggil("POST", f"{PREFIX}/update", body, timeout)
```

`follow_redirects=False` disengaja: bila site tiba-tiba mengarahkan ke halaman login atau ke domain lain, kita ingin melihat 301-nya sebagai anomali, bukan diam-diam mengirim header bertanda tangan ke tujuan yang tidak kita maksud.

- [ ] **Step 4: Jalankan test, pastikan lolos**

Run: `pytest tests/unit/test_site_client.py -v`
Expected: PASS, 12 test

- [ ] **Step 5: Commit**

```bash
git add src/wpmgr/site_client.py tests/unit/test_site_client.py
git commit -m "feat: klien HTTP bertanda tangan ke site client

Timeout dipetakan ke error_class unknown, bukan gagal, karena update yang
timeout mungkin sudah berhasil di sisi site.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Handler `scan_site` dan `verify_site`

**Files:**
- Create: `src/wpmgr/jobs/handlers.py`, `tests/integration/test_handler_scan.py`

**Interfaces:**
- Consumes: `SiteClient`, `queue`, `models`, `crypto`
- Produces:
  - `buat_klien(site: Site) -> SiteClient` (mendekripsi secret)
  - `tangani_scan_site(sesi, job, klien: SiteClient) -> dict`
  - `tangani_verify_site(sesi, job, klien: SiteClient) -> dict`
  - `simpan_inventaris(sesi, site: Site, data: dict) -> int` → jumlah baris setelah sinkronisasi
  - `HANDLER: dict[JobType, Callable]`

Bentuk `data` yang diterima `simpan_inventaris` persis seperti spec 6.1: `{"core": {...}, "plugins": [...], "themes": [...]}`, dengan tiap item memiliki kunci `slug`, `nama`, `versi_terpasang`, `versi_tersedia`, `aktif`, `auto_update`. Item `core` memakai `slug` bernilai `"core"`.

- [ ] **Step 1: Tulis test yang gagal**

```python
# tests/integration/test_handler_scan.py
from datetime import datetime, timezone

import httpx
import pytest

from wpmgr.jobs.handlers import simpan_inventaris, tangani_scan_site, tangani_verify_site
from wpmgr.jobs.queue import ambil_job, buat_job
from wpmgr.models import JobType, PackageType, SitePackage, SiteStatus
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration

INVENTARIS = {
    "core": {"slug": "core", "nama": "WordPress", "versi_terpasang": "6.5.2",
             "versi_tersedia": "6.6", "aktif": True, "auto_update": False},
    "plugins": [
        {"slug": "elementor/elementor.php", "nama": "Elementor", "versi_terpasang": "3.18.3",
         "versi_tersedia": "3.20.1", "aktif": True, "auto_update": False},
        {"slug": "akismet/akismet.php", "nama": "Akismet", "versi_terpasang": "5.3",
         "versi_tersedia": None, "aktif": False, "auto_update": True},
    ],
    "themes": [
        {"slug": "astra", "nama": "Astra", "versi_terpasang": "4.6",
         "versi_tersedia": None, "aktif": True, "auto_update": False},
    ],
}


def klien_palsu(muatan, status=200):
    def handler(request):
        return httpx.Response(status, json=muatan)

    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_simpan_inventaris_membuat_satu_baris_per_paket(sesi, site):
    assert simpan_inventaris(sesi, site, INVENTARIS) == 4
    assert sesi.query(SitePackage).filter_by(site_id=site.id).count() == 4


def test_core_disimpan_dengan_slug_core(sesi, site):
    simpan_inventaris(sesi, site, INVENTARIS)
    inti = sesi.query(SitePackage).filter_by(site_id=site.id, tipe=PackageType.core).one()
    assert inti.slug == "core"
    assert inti.versi_tersedia == "6.6"


def test_scan_ulang_memperbarui_bukan_menggandakan(sesi, site):
    simpan_inventaris(sesi, site, INVENTARIS)
    baru = {**INVENTARIS, "plugins": [
        {**INVENTARIS["plugins"][0], "versi_terpasang": "3.20.1", "versi_tersedia": None},
        INVENTARIS["plugins"][1],
    ]}
    assert simpan_inventaris(sesi, site, baru) == 4
    e = sesi.query(SitePackage).filter_by(site_id=site.id, slug="elementor/elementor.php").one()
    assert e.versi_terpasang == "3.20.1"
    assert e.versi_tersedia is None


def test_paket_yang_dihapus_di_site_ikut_hilang(sesi, site):
    simpan_inventaris(sesi, site, INVENTARIS)
    tanpa_akismet = {**INVENTARIS, "plugins": [INVENTARIS["plugins"][0]]}
    assert simpan_inventaris(sesi, site, tanpa_akismet) == 3
    assert sesi.query(SitePackage).filter_by(site_id=site.id, slug="akismet/akismet.php").count() == 0


def test_handler_scan_menyimpan_dan_memperbarui_last_scan_at(sesi, site):
    buat_job(sesi, site.id, JobType.scan_site)
    job = ambil_job(sesi, "w1")
    hasil = tangani_scan_site(sesi, job, klien_palsu(INVENTARIS))
    sesi.refresh(site)
    assert hasil["jumlah_paket"] == 4
    assert site.last_scan_at is not None
    assert site.last_seen_at is not None


def test_handler_verify_mengaktifkan_site(sesi, site):
    site.status = SiteStatus.pending_pair
    sesi.commit()
    buat_job(sesi, site.id, JobType.verify_site)
    job = ambil_job(sesi, "w1")
    tangani_verify_site(sesi, job, klien_palsu(
        {"connector_version": "1.0", "wp_version": "6.5.2", "php_version": "8.1"}))
    sesi.refresh(site)
    assert site.status == SiteStatus.active
    assert site.wp_version == "6.5.2"
    assert site.php_version == "8.1"
    assert site.connector_version == "1.0"
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/integration/test_handler_scan.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'wpmgr.jobs.handlers'`

- [ ] **Step 3: Implementasi handler scan dan verify**

```python
# src/wpmgr/jobs/handlers.py
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from wpmgr.crypto import dekripsi_secret
from wpmgr.models import Job, PackageType, Site, SitePackage, SiteStatus
from wpmgr.site_client import SiteClient


def buat_klien(site: Site) -> SiteClient:
    return SiteClient(site.url, str(site.id), dekripsi_secret(site.secret_terenkripsi))


def _item_inventaris(data: dict):
    inti = data.get("core")
    if inti:
        yield PackageType.core, {**inti, "slug": "core"}
    for p in data.get("plugins", []):
        yield PackageType.plugin, p
    for t in data.get("themes", []):
        yield PackageType.theme, t


def simpan_inventaris(sesi: Session, site: Site, data: dict) -> int:
    sekarang = datetime.now(timezone.utc)
    terlihat: set[tuple[PackageType, str]] = set()

    for tipe, item in _item_inventaris(data):
        kunci = (tipe, item["slug"])
        terlihat.add(kunci)
        baris = sesi.scalar(
            select(SitePackage).where(
                SitePackage.site_id == site.id,
                SitePackage.tipe == tipe,
                SitePackage.slug == item["slug"],
            )
        )
        if baris is None:
            baris = SitePackage(site_id=site.id, tipe=tipe, slug=item["slug"])
            sesi.add(baris)
        baris.nama = item["nama"]
        baris.versi_terpasang = item["versi_terpasang"]
        baris.versi_tersedia = item.get("versi_tersedia")
        baris.aktif = bool(item.get("aktif", True))
        baris.auto_update = bool(item.get("auto_update", False))
        baris.last_scan_at = sekarang

    sesi.flush()

    lama = sesi.scalars(select(SitePackage).where(SitePackage.site_id == site.id)).all()
    for baris in lama:
        if (baris.tipe, baris.slug) not in terlihat:
            sesi.delete(baris)

    site.last_scan_at = sekarang
    site.last_seen_at = sekarang
    site.last_error = None
    sesi.commit()
    return len(terlihat)


def tangani_scan_site(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    data = klien.inventory()
    jumlah = simpan_inventaris(sesi, site, data)
    if site.status in (SiteStatus.unreachable, SiteStatus.needs_reconnect, SiteStatus.blocked):
        site.status = SiteStatus.active
        sesi.commit()
    return {"jumlah_paket": jumlah}


def tangani_verify_site(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    data = klien.ping()
    site.connector_version = data.get("connector_version")
    site.wp_version = data.get("wp_version")
    site.php_version = data.get("php_version")
    site.last_seen_at = datetime.now(timezone.utc)
    site.last_error = None
    site.status = SiteStatus.active
    sesi.commit()
    return data
```

Menghapus baris yang tidak lagi muncul pada hasil scan adalah bagian yang mudah dilupakan. Tanpa itu, plugin yang dihapus client akan terus tampil di dashboard sebagai "punya update" selamanya, dan kamu akan mengejar sesuatu yang sudah tidak ada.

- [ ] **Step 4: Jalankan test, pastikan lolos**

Run: `pytest tests/integration/test_handler_scan.py -v -m integration`
Expected: PASS, 6 test

- [ ] **Step 5: Commit**

```bash
git add src/wpmgr/jobs/handlers.py tests/integration/test_handler_scan.py
git commit -m "feat: handler scan_site dan verify_site dengan sinkronisasi inventaris

Paket yang hilang dari hasil scan dihapus, supaya plugin yang sudah dibuang
client tidak terus muncul sebagai pekerjaan tertunda.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Handler `update_package` dan alur `unknown`

**Files:**
- Modify: `src/wpmgr/jobs/handlers.py`
- Create: `tests/integration/test_handler_update.py`

**Interfaces:**
- Consumes: handler Task 10, `SiteError`, `queue`
- Produces:
  - `tangani_update_package(sesi, job, klien) -> dict`
  - `resolusi_unknown(sesi, job, klien) -> str` → `"success"`, `"pending"`, atau `"failed"`
  - `HANDLER` dilengkapi untuk ketiga `JobType`

Payload job `update_package`: `{"tipe": "plugin", "slug": "...", "dari_versi": "...", "ke_versi": "..."}`.

- [ ] **Step 1: Tulis test yang gagal**

```python
# tests/integration/test_handler_update.py
from datetime import datetime, timezone

import httpx
import pytest

from wpmgr.errors import UNKNOWN, SiteError
from wpmgr.jobs.handlers import resolusi_unknown, tangani_update_package
from wpmgr.jobs.queue import ambil_job, buat_job
from wpmgr.models import JobType, PackageType, SitePackage
from wpmgr.site_client import SiteClient

pytestmark = pytest.mark.integration

PAYLOAD = {"tipe": "plugin", "slug": "elementor/elementor.php",
           "dari_versi": "3.18.3", "ke_versi": "3.20.1"}


def klien_dari(handler):
    return SiteClient("https://contoh.test", "s", "f" * 64,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


def _paket(sesi, site, versi):
    sesi.add(SitePackage(
        site_id=site.id, tipe=PackageType.plugin, slug="elementor/elementor.php",
        nama="Elementor", versi_terpasang=versi, versi_tersedia="3.20.1",
        last_scan_at=datetime.now(timezone.utc)))
    sesi.commit()


def test_update_sukses_memperbarui_baris_paket(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")

    klien = klien_dari(lambda r: httpx.Response(200, json={
        "ok": True, "versi_sebelum": "3.18.3", "versi_sesudah": "3.20.1", "pesan": "berhasil"}))
    hasil = tangani_update_package(sesi, job, klien)

    p = sesi.query(SitePackage).filter_by(site_id=site.id).one()
    assert hasil["versi_sesudah"] == "3.20.1"
    assert p.versi_terpasang == "3.20.1"
    assert p.versi_tersedia is None


def test_update_hanya_mengirim_satu_request(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")
    jumlah = {"n": 0}

    def handler(request):
        jumlah["n"] += 1
        return httpx.Response(200, json={"ok": True, "versi_sesudah": "3.20.1"})

    tangani_update_package(sesi, job, klien_dari(handler))
    assert jumlah["n"] == 1


def test_timeout_melempar_site_error_unknown(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")

    def handler(request):
        raise httpx.ReadTimeout("habis", request=request)

    with pytest.raises(SiteError) as exc:
        tangani_update_package(sesi, job, klien_dari(handler))
    assert exc.value.error_class == UNKNOWN


def test_resolusi_unknown_menjadi_success_bila_versi_sudah_naik(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")

    klien = klien_dari(lambda r: httpx.Response(200, json={
        "core": None,
        "plugins": [{"slug": "elementor/elementor.php", "nama": "Elementor",
                     "versi_terpasang": "3.20.1", "versi_tersedia": None,
                     "aktif": True, "auto_update": False}],
        "themes": [],
    }))
    assert resolusi_unknown(sesi, job, klien) == "success"


def test_resolusi_unknown_menjadi_pending_bila_versi_belum_naik(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")

    klien = klien_dari(lambda r: httpx.Response(200, json={
        "core": None,
        "plugins": [{"slug": "elementor/elementor.php", "nama": "Elementor",
                     "versi_terpasang": "3.18.3", "versi_tersedia": "3.20.1",
                     "aktif": True, "auto_update": False}],
        "themes": [],
    }))
    assert resolusi_unknown(sesi, job, klien) == "pending"


def test_resolusi_unknown_menjadi_failed_bila_jatah_habis(sesi, site):
    _paket(sesi, site, "3.18.3")
    buat_job(sesi, site.id, JobType.update_package, PAYLOAD)
    job = ambil_job(sesi, "w1")
    job.attempts = job.max_attempts
    sesi.commit()

    klien = klien_dari(lambda r: httpx.Response(200, json={
        "core": None,
        "plugins": [{"slug": "elementor/elementor.php", "nama": "Elementor",
                     "versi_terpasang": "3.18.3", "versi_tersedia": "3.20.1",
                     "aktif": True, "auto_update": False}],
        "themes": [],
    }))
    assert resolusi_unknown(sesi, job, klien) == "failed"
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/integration/test_handler_update.py -v -m integration`
Expected: FAIL — `ImportError: cannot import name 'tangani_update_package'`

- [ ] **Step 3: Tambahkan handler update ke `handlers.py`**

```python
# tambahkan ke src/wpmgr/jobs/handlers.py
from wpmgr.jobs.queue import jeda_menit
from wpmgr.models import JobStatus, JobType


def tangani_update_package(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    p = job.payload
    hasil = klien.update(p["tipe"], p["slug"], p["ke_versi"])

    versi_sesudah = hasil.get("versi_sesudah") or p["ke_versi"]
    baris = sesi.scalar(
        select(SitePackage).where(
            SitePackage.site_id == site.id,
            SitePackage.tipe == PackageType(p["tipe"]),
            SitePackage.slug == p["slug"],
        )
    )
    if baris is not None:
        baris.versi_terpasang = versi_sesudah
        if baris.versi_tersedia == versi_sesudah:
            baris.versi_tersedia = None
        baris.last_scan_at = datetime.now(timezone.utc)
    site.last_seen_at = datetime.now(timezone.utc)
    sesi.commit()
    return hasil


def resolusi_unknown(sesi: Session, job: Job, klien: SiteClient) -> str:
    """Setelah timeout, tanyakan keadaan sebenarnya ke site alih-alih menebak."""
    from datetime import timedelta

    from sqlalchemy import func as safunc

    site = sesi.get(Site, job.site_id)
    p = job.payload
    simpan_inventaris(sesi, site, klien.inventory())

    baris = sesi.scalar(
        select(SitePackage).where(
            SitePackage.site_id == site.id,
            SitePackage.tipe == PackageType(p["tipe"]),
            SitePackage.slug == p["slug"],
        )
    )
    if baris is not None and baris.versi_terpasang == p["ke_versi"]:
        job.status = JobStatus.success
        job.hasil = {"versi_sesudah": baris.versi_terpasang,
                     "pesan": "terverifikasi lewat scan ulang setelah timeout"}
        job.error = None
        job.error_class = None
        job.finished_at = safunc.now()
        sesi.commit()
        return "success"

    if job.attempts < job.max_attempts:
        job.status = JobStatus.pending
        job.scheduled_for = safunc.now() + timedelta(minutes=jeda_menit(job.attempts))
        job.started_at = None
        sesi.commit()
        return "pending"

    job.status = JobStatus.failed
    job.finished_at = safunc.now()
    sesi.commit()
    return "failed"


HANDLER = {
    JobType.scan_site: tangani_scan_site,
    JobType.update_package: tangani_update_package,
    JobType.verify_site: tangani_verify_site,
}
```

- [ ] **Step 4: Jalankan test, pastikan lolos**

Run: `pytest tests/integration/test_handler_update.py -v -m integration`
Expected: PASS, 6 test

- [ ] **Step 5: Commit**

```bash
git add src/wpmgr/jobs/handlers.py tests/integration/test_handler_update.py
git commit -m "feat: handler update_package dan resolusi status unknown

Timeout tidak pernah ditebak sebagai gagal; keadaan sebenarnya diverifikasi
lewat scan ulang sebelum job diputuskan sukses, diulang, atau gagal.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Loop worker dan CLI

**Files:**
- Create: `src/wpmgr/worker.py`, `src/wpmgr/cli.py`, `tests/integration/test_worker.py`

**Interfaces:**
- Consumes: `queue`, `handlers`, `reaper`, `errors`
- Produces:
  - `worker.proses_satu(sesi, worker: str, buat_klien_fn=buat_klien) -> bool` — `True` bila ada job diproses
  - `worker.main() -> None` — loop dengan jeda 5 detik saat antrean kosong
  - `cli.enqueue_scans() -> int`, `cli.reap_jobs() -> int`, `cli.create_user(email, nama, password) -> None`
  - Entry point: `python -m wpmgr.cli <perintah>`

- [ ] **Step 1: Tulis test yang gagal**

```python
# tests/integration/test_worker.py
import httpx
import pytest

from wpmgr.jobs.queue import ambil_job, buat_job
from wpmgr.models import ActivityLog, Job, JobStatus, JobType, Site, SiteStatus
from wpmgr.site_client import SiteClient
from wpmgr.worker import proses_satu

pytestmark = pytest.mark.integration

PING = {"connector_version": "1.0", "wp_version": "6.5", "php_version": "8.1"}


def pabrik(status=200, muatan=PING):
    def buat(site):
        return SiteClient("https://contoh.test", "s", "f" * 64,
                          client=httpx.Client(transport=httpx.MockTransport(
                              lambda r: httpx.Response(status, json=muatan))))

    return buat


def test_antrean_kosong_mengembalikan_false(sesi):
    assert proses_satu(sesi, "w1", pabrik()) is False


def test_job_sukses_ditandai_success(sesi, site):
    buat_job(sesi, site.id, JobType.verify_site)
    assert proses_satu(sesi, "w1", pabrik()) is True
    j = sesi.query(Job).one()
    assert j.status == JobStatus.success


def test_auth_error_menandai_site_needs_reconnect(sesi, site):
    buat_job(sesi, site.id, JobType.verify_site)
    proses_satu(sesi, "w1", pabrik(status=401, muatan={"code": "x"}))
    sesi.refresh(site)
    assert site.status == SiteStatus.needs_reconnect
    assert sesi.query(Job).one().status == JobStatus.failed


def test_403_firewall_menandai_site_blocked(sesi, site):
    buat_job(sesi, site.id, JobType.verify_site)

    def buat(s):
        return SiteClient("https://contoh.test", "s", "f" * 64,
                          client=httpx.Client(transport=httpx.MockTransport(
                              lambda r: httpx.Response(403, headers={"Server": "cloudflare"},
                                                       content="denied"))))

    proses_satu(sesi, "w1", buat)
    sesi.refresh(site)
    assert site.status == SiteStatus.blocked


def test_kegagalan_menulis_activity_log(sesi, site):
    buat_job(sesi, site.id, JobType.verify_site)
    proses_satu(sesi, "w1", pabrik(status=401, muatan={"code": "x"}))
    assert sesi.query(ActivityLog).filter_by(level="error").count() == 1


def test_job_site_nonaktif_tidak_diambil(sesi, site):
    site.status = SiteStatus.disabled
    sesi.commit()
    buat_job(sesi, site.id, JobType.verify_site)
    assert proses_satu(sesi, "w1", pabrik()) is False
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/integration/test_worker.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'wpmgr.worker'`

- [ ] **Step 3: Tambahkan filter site nonaktif pada `ambil_job`**

Ubah subquery di `SQL_AMBIL` (`src/wpmgr/jobs/queue.py`) agar ikut menyaring site yang tidak boleh dihubungi:

```sql
           SELECT j.id
             FROM jobs j
             JOIN sites s ON s.id = j.site_id
            WHERE j.status = 'pending'
              AND j.scheduled_for <= now()
              AND s.status <> 'disabled'
              AND NOT EXISTS (
                    SELECT 1 FROM jobs j2
                     WHERE j2.site_id = j.site_id
                       AND j2.status = 'running')
            ORDER BY j.scheduled_for
              FOR UPDATE OF j SKIP LOCKED
            LIMIT 1
```

`FOR UPDATE OF j` diperlukan karena kini ada dua tabel dalam query; tanpa penunjuk itu PostgreSQL akan mencoba mengunci `sites` juga.

- [ ] **Step 4: Implementasi `worker.py`**

```python
# src/wpmgr/worker.py
import logging
import signal
import time

from sqlalchemy.orm import Session

from wpmgr.db import get_session
from wpmgr.errors import (
    AUTH_ERROR, BLOCKED, CONNECTOR_MISSING, TRANSIENT, UNKNOWN, SiteError,
)
from wpmgr.jobs.handlers import HANDLER, buat_klien, resolusi_unknown
from wpmgr.jobs.queue import ambil_job, selesai_gagal, selesai_sukses, tandai_unknown, worker_id
from wpmgr.models import ActivityLog, JobType, Site, SiteStatus

log = logging.getLogger("wpmgr.worker")
JEDA_ANTREAN_KOSONG = 5.0

STATUS_SITE_DARI_ERROR = {
    AUTH_ERROR: SiteStatus.needs_reconnect,
    CONNECTOR_MISSING: SiteStatus.needs_reconnect,
    BLOCKED: SiteStatus.blocked,
    TRANSIENT: SiteStatus.unreachable,
}

_berhenti = False


def _tangani_sinyal(signum, frame):
    global _berhenti
    _berhenti = True
    log.info("Sinyal %s diterima; berhenti setelah job berjalan selesai", signum)


def proses_satu(sesi: Session, worker: str, buat_klien_fn=buat_klien) -> bool:
    job = ambil_job(sesi, worker)
    if job is None:
        return False

    site = sesi.get(Site, job.site_id)
    try:
        hasil = HANDLER[job.tipe](sesi, job, buat_klien_fn(site))
        selesai_sukses(sesi, job, hasil if isinstance(hasil, dict) else {})
        return True
    except SiteError as exc:
        _catat_kegagalan(sesi, job, site, exc, worker, buat_klien_fn)
        return True


def _catat_kegagalan(sesi, job, site, exc: SiteError, worker: str, buat_klien_fn) -> None:
    if exc.error_class == UNKNOWN and job.tipe == JobType.update_package:
        tandai_unknown(sesi, job, exc.pesan)
        try:
            hasil = resolusi_unknown(sesi, job, buat_klien_fn(site))
            log.info("Job %s diselesaikan lewat scan ulang: %s", job.id, hasil)
        except SiteError as exc2:
            log.warning("Scan ulang untuk job %s juga gagal: %s", job.id, exc2.pesan)
    elif exc.error_class == UNKNOWN:
        tandai_unknown(sesi, job, exc.pesan)
    else:
        selesai_gagal(sesi, job, exc.error_class, exc.pesan)

    status_baru = STATUS_SITE_DARI_ERROR.get(exc.error_class)
    if status_baru is not None and site.status != SiteStatus.disabled:
        site.status = status_baru
    site.last_error = exc.pesan[:2000]
    sesi.add(
        ActivityLog(
            site_id=site.id, job_id=job.id, level="error",
            pesan=f"{job.tipe.value} gagal: {exc.error_class}",
            detail={"pesan": exc.pesan[:500], "worker": worker},
        )
    )
    sesi.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _tangani_sinyal)
    signal.signal(signal.SIGINT, _tangani_sinyal)
    worker = worker_id()
    log.info("Worker %s mulai", worker)

    while not _berhenti:
        try:
            with get_session() as sesi:
                ada = proses_satu(sesi, worker, buat_klien)
        except Exception:
            log.exception("Kesalahan tak terduga di loop worker")
            ada = False
        if not ada:
            time.sleep(JEDA_ANTREAN_KOSONG)

    log.info("Worker %s berhenti", worker)


if __name__ == "__main__":
    main()
```

Penanganan `SIGTERM` bukan hiasan: saat `systemctl restart` dikirim di tengah update plugin, worker menyelesaikan job yang sedang berjalan lebih dulu, bukan mati di tengah dan meninggalkan site dalam keadaan yang harus ditebak reaper.

- [ ] **Step 5: Implementasi `cli.py`**

```python
# src/wpmgr/cli.py
import argparse
import sys
import uuid

from argon2 import PasswordHasher
from sqlalchemy import select

from wpmgr.db import get_session
from wpmgr.jobs.queue import buat_job
from wpmgr.jobs.reaper import pulihkan_job_yatim
from wpmgr.models import Job, JobStatus, JobType, Site, SiteStatus, User


def enqueue_scans() -> int:
    dibuat = 0
    with get_session() as sesi:
        sites = sesi.scalars(select(Site).where(Site.status == SiteStatus.active)).all()
        for site in sites:
            sudah_ada = sesi.scalar(
                select(Job.id).where(
                    Job.site_id == site.id,
                    Job.tipe == JobType.scan_site,
                    Job.status.in_([JobStatus.pending, JobStatus.running]),
                )
            )
            if sudah_ada is None:
                buat_job(sesi, site.id, JobType.scan_site)
                dibuat += 1
    print(f"{dibuat} job scan dibuat")
    return dibuat


def reap_jobs() -> int:
    with get_session() as sesi:
        n = pulihkan_job_yatim(sesi)
    print(f"{n} job yatim dipulihkan")
    return n


def create_user(email: str, nama: str, password: str) -> None:
    with get_session() as sesi:
        sesi.add(
            User(id=uuid.uuid4(), email=email, nama=nama,
                 password_hash=PasswordHasher().hash(password))
        )
    print(f"User {email} dibuat")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wpmgr")
    sub = parser.add_subparsers(dest="perintah", required=True)
    sub.add_parser("enqueue-scans")
    sub.add_parser("reap-jobs")
    p = sub.add_parser("create-user")
    p.add_argument("--email", required=True)
    p.add_argument("--nama", required=True)
    p.add_argument("--password", required=True)

    args = parser.parse_args(argv)
    if args.perintah == "enqueue-scans":
        enqueue_scans()
    elif args.perintah == "reap-jobs":
        reap_jobs()
    elif args.perintah == "create-user":
        create_user(args.email, args.nama, args.password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Jalankan test, pastikan lolos**

Run: `pytest tests/integration/test_worker.py -v -m integration`
Expected: PASS, 6 test

- [ ] **Step 7: Jalankan seluruh test yang sudah ada**

Run: `pytest -v`
Expected: PASS, seluruh test unit dan integrasi hijau

- [ ] **Step 8: Commit**

```bash
git add src/wpmgr/worker.py src/wpmgr/cli.py src/wpmgr/jobs/queue.py tests/integration/test_worker.py
git commit -m "feat: loop worker dengan shutdown rapi dan CLI penjadwalan

Job pada site berstatus disabled tidak pernah diambil. SIGTERM menyelesaikan
job berjalan lebih dulu, bukan meninggalkannya untuk dipungut reaper.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Fase 4 — Plugin Connector (PHP)

### Task 13: Bootstrap plugin dan verifikasi HMAC terhadap fixture bersama

**Files:**
- Create: `connector/wp-manager-connector/wp-manager-connector.php`
- Create: `connector/wp-manager-connector/includes/class-wpmgr-signing.php`
- Create: `connector/phpunit.xml`, `connector/tests/bootstrap.php`, `connector/tests/SigningTest.php`

**Interfaces:**
- Consumes: `tests/fixtures/hmac-test-vectors.json` (dibangkitkan di Task 2)
- Produces:
  - `WPMGR_Signing::canonical( $method, $path, $timestamp, $nonce, $body ) : string`
  - `WPMGR_Signing::sign( $secret_hex, $method, $path, $timestamp, $nonce, $body ) : string`
  - `WPMGR_Signing::verify( $secret_hex, $signature, $method, $path, $timestamp, $nonce, $body ) : bool`
  - Konstanta `WPMGR_VERSION`, `WPMGR_JENDELA_DETIK` (300), `WPMGR_NONCE_TTL` (600)

Task ini sengaja tidak memuat WordPress sama sekali. `class-wpmgr-signing.php` adalah PHP murni tanpa satu pun panggilan fungsi WordPress, sehingga dapat diuji dengan PHPUnit polos dan cepat.

- [ ] **Step 1: Tulis test PHP yang gagal**

```php
<?php
// connector/tests/SigningTest.php
use PHPUnit\Framework\TestCase;

final class SigningTest extends TestCase {

    private function vectors(): array {
        $path = __DIR__ . '/../../tests/fixtures/hmac-test-vectors.json';
        $this->assertFileExists( $path, 'Jalankan python scripts/gen_hmac_vectors.py lebih dulu' );
        return json_decode( file_get_contents( $path ), true );
    }

    public function test_canonical_cocok_dengan_fixture(): void {
        foreach ( $this->vectors() as $v ) {
            $this->assertSame(
                $v['canonical'],
                WPMGR_Signing::canonical( $v['method'], $v['path'], $v['timestamp'], $v['nonce'], $v['body'] ),
                "canonical meleset pada kasus {$v['nama']}"
            );
        }
    }

    public function test_signature_cocok_dengan_fixture(): void {
        foreach ( $this->vectors() as $v ) {
            $this->assertSame(
                $v['signature'],
                WPMGR_Signing::sign( $v['secret_hex'], $v['method'], $v['path'], $v['timestamp'], $v['nonce'], $v['body'] ),
                "signature meleset pada kasus {$v['nama']}"
            );
        }
    }

    public function test_verify_menerima_yang_benar(): void {
        $v = $this->vectors()[0];
        $this->assertTrue(
            WPMGR_Signing::verify( $v['secret_hex'], $v['signature'], $v['method'], $v['path'], $v['timestamp'], $v['nonce'], $v['body'] )
        );
    }

    public function test_verify_menolak_yang_salah(): void {
        $v = $this->vectors()[0];
        $this->assertFalse(
            WPMGR_Signing::verify( $v['secret_hex'], str_repeat( '0', 64 ), $v['method'], $v['path'], $v['timestamp'], $v['nonce'], $v['body'] )
        );
    }

    public function test_method_dibesarkan(): void {
        $this->assertStringStartsWith( 'GET' . "\n", WPMGR_Signing::canonical( 'get', '/x', 1, 'n', '' ) );
    }

    public function test_canonical_punya_empat_newline(): void {
        $this->assertSame( 4, substr_count( WPMGR_Signing::canonical( 'POST', '/x', 1, 'n', '{}' ), "\n" ) );
    }
}
```

- [ ] **Step 2: Buat bootstrap dan konfigurasi PHPUnit**

```php
<?php
// connector/tests/bootstrap.php
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-signing.php';
```

```xml
<!-- connector/phpunit.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<phpunit bootstrap="tests/bootstrap.php" colors="true">
  <testsuites>
    <testsuite name="connector">
      <directory>tests</directory>
    </testsuite>
  </testsuites>
</phpunit>
```

- [ ] **Step 3: Jalankan test, pastikan gagal**

Run: `cd connector && php -d error_reporting=E_ALL vendor/bin/phpunit` (pasang PHPUnit lebih dulu dengan `composer require --dev phpunit/phpunit ^9` di dalam `connector/`)
Expected: FAIL — `Class "WPMGR_Signing" not found`

- [ ] **Step 4: Implementasi `class-wpmgr-signing.php`**

```php
<?php
/**
 * Verifikasi tanda tangan HMAC. PHP murni, tanpa ketergantungan WordPress,
 * supaya dapat diuji langsung terhadap fixture bersama dengan sisi Python.
 */

if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

class WPMGR_Signing {

    /**
     * Kunci HMAC adalah string hex apa adanya (ASCII), BUKAN hasil hex2bin().
     * Sisi Python melakukan hal yang sama. Tidak ada langkah konversi yang
     * dapat berbeda di antara keduanya.
     */
    public static function canonical( $method, $path, $timestamp, $nonce, $body ) {
        return implode(
            "\n",
            array(
                strtoupper( $method ),
                $path,
                (string) $timestamp,
                $nonce,
                hash( 'sha256', (string) $body ),
            )
        );
    }

    public static function sign( $secret_hex, $method, $path, $timestamp, $nonce, $body ) {
        return hash_hmac(
            'sha256',
            self::canonical( $method, $path, $timestamp, $nonce, $body ),
            $secret_hex
        );
    }

    public static function verify( $secret_hex, $signature, $method, $path, $timestamp, $nonce, $body ) {
        $diharapkan = self::sign( $secret_hex, $method, $path, $timestamp, $nonce, $body );
        return hash_equals( $diharapkan, (string) $signature );
    }
}
```

- [ ] **Step 5: Jalankan test, pastikan lolos**

Run: `cd connector && vendor/bin/phpunit`
Expected: PASS, 6 test

Bila salah satu kasus meleset, bandingkan `canonical` yang dihasilkan dengan nilai `canonical` di fixture. Perbedaan akan langsung terlihat sebagai string, bukan sebagai 401 tanpa petunjuk.

- [ ] **Step 6: Tulis bootstrap plugin**

```php
<?php
/**
 * Plugin Name: WP Manager Connector
 * Description: Menghubungkan site ini ke dashboard WP Manager untuk pemindaian dan update terpusat.
 * Version:     1.0.0
 * Requires PHP: 7.4
 * License:     GPL-2.0-or-later
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

define( 'WPMGR_VERSION', '1.0.0' );
define( 'WPMGR_JENDELA_DETIK', 300 );
define( 'WPMGR_NONCE_TTL', 600 );
define( 'WPMGR_SSO_TTL', 120 );
define( 'WPMGR_USER_LOGIN', 'wpmgr' );
define( 'WPMGR_DIR', plugin_dir_path( __FILE__ ) );

require_once WPMGR_DIR . 'includes/class-wpmgr-signing.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-settings.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-inventory.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-updater.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-rest.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-sso.php';

add_action( 'rest_api_init', array( 'WPMGR_REST', 'daftarkan_route' ) );
add_action( 'admin_menu', array( 'WPMGR_Settings', 'daftarkan_menu' ) );
add_action( 'admin_init', array( 'WPMGR_Settings', 'tangani_simpan' ) );
add_action( 'init', array( 'WPMGR_SSO', 'tangani_permintaan' ), 1 );
```

Berkas-berkas yang di-`require` dibuat pada Task 14–17; plugin belum dapat diaktifkan sampai Task 17 selesai. Itu disengaja — mengaktifkan plugin setengah jadi di site nyata tidak pernah berguna.

- [ ] **Step 7: Commit**

```bash
git add connector/
git commit -m "feat(connector): bootstrap plugin dan verifikasi HMAC sisi PHP

Test PHP membaca fixture yang sama dengan test Python, sehingga perubahan
canonical string di satu bahasa langsung merah di bahasa itu.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Halaman setting, pairing, dan user `wpmgr`

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-settings.php`

**Interfaces:**
- Consumes: `WPMGR_Signing`
- Produces:
  - `WPMGR_Settings::site_id()`, `::secret()`, `::dashboard_url()` — membaca opsi
  - `WPMGR_Settings::terpasang() : bool`
  - `WPMGR_Settings::pastikan_user() : int` — id user `wpmgr`, dibuat bila belum ada
  - `WPMGR_Settings::simpan_kunci( string $kunci ) : true|WP_Error`
  - `WPMGR_Settings::kirim_konfirmasi() : true|WP_Error`
  - Opsi WordPress: `wpmgr_site_id`, `wpmgr_secret`, `wpmgr_dashboard_url`

- [ ] **Step 1: Implementasi**

```php
<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPMGR_Settings {

    const OPT_SITE_ID   = 'wpmgr_site_id';
    const OPT_SECRET    = 'wpmgr_secret';
    const OPT_DASHBOARD = 'wpmgr_dashboard_url';

    public static function site_id() {
        return (string) get_option( self::OPT_SITE_ID, '' );
    }

    public static function secret() {
        return (string) get_option( self::OPT_SECRET, '' );
    }

    public static function dashboard_url() {
        return (string) get_option( self::OPT_DASHBOARD, '' );
    }

    public static function terpasang() {
        return '' !== self::site_id() && '' !== self::secret();
    }

    /**
     * User khusus untuk SSO. Tidak disembunyikan dari daftar user: akun
     * administrator yang disembunyikan adalah pola perilaku malware.
     */
    public static function pastikan_user() {
        $user = get_user_by( 'login', WPMGR_USER_LOGIN );
        if ( $user ) {
            return (int) $user->ID;
        }
        $id = wp_insert_user(
            array(
                'user_login'   => WPMGR_USER_LOGIN,
                'user_pass'    => wp_generate_password( 64, true, true ),
                'user_email'   => 'wpmgr+' . wp_generate_password( 8, false ) . '@invalid.local',
                'display_name' => 'WP Manager',
                'role'         => 'administrator',
            )
        );
        return is_wp_error( $id ) ? 0 : (int) $id;
    }

    public static function simpan_kunci( $kunci ) {
        $mentah = base64_decode( strtr( trim( $kunci ), '-_', '+/' ), true );
        if ( false === $mentah ) {
            return new WP_Error( 'wpmgr_kunci_rusak', 'Kunci koneksi tidak dapat dibaca.' );
        }
        $bagian = explode( ':', $mentah );
        if ( 3 !== count( $bagian ) ) {
            return new WP_Error( 'wpmgr_kunci_rusak', 'Kunci koneksi tidak lengkap.' );
        }
        list( $site_id, $secret, $dashboard ) = $bagian;

        if ( ! preg_match( '/^[0-9a-f]{64}$/', $secret ) ) {
            return new WP_Error( 'wpmgr_kunci_rusak', 'Secret pada kunci tidak valid.' );
        }
        if ( 0 !== strpos( $dashboard, 'https://' ) ) {
            return new WP_Error( 'wpmgr_kunci_rusak', 'URL dashboard wajib https://.' );
        }

        update_option( self::OPT_SITE_ID, $site_id, false );
        update_option( self::OPT_SECRET, $secret, false );
        update_option( self::OPT_DASHBOARD, rtrim( $dashboard, '/' ), false );
        self::pastikan_user();

        return self::kirim_konfirmasi();
    }

    /**
     * Membuktikan ke dashboard bahwa site ini benar memegang secret-nya, dan
     * sekaligus membuktikan arah keluar (site -> dashboard) dapat dilalui.
     */
    public static function kirim_konfirmasi() {
        $path = '/api/pair/confirm';
        $body = wp_json_encode(
            array(
                'connector_version' => WPMGR_VERSION,
                'wp_version'        => get_bloginfo( 'version' ),
                'php_version'       => PHP_VERSION,
                'site_url'          => home_url(),
            )
        );
        $ts    = time();
        $nonce = bin2hex( random_bytes( 16 ) );

        $resp = wp_remote_post(
            self::dashboard_url() . $path,
            array(
                'timeout' => 20,
                'headers' => array(
                    'Content-Type'       => 'application/json',
                    'X-Wpmgr-Site'       => self::site_id(),
                    'X-Wpmgr-Timestamp'  => (string) $ts,
                    'X-Wpmgr-Nonce'      => $nonce,
                    'X-Wpmgr-Signature'  => WPMGR_Signing::sign( self::secret(), 'POST', $path, $ts, $nonce, $body ),
                ),
                'body'    => $body,
            )
        );

        if ( is_wp_error( $resp ) ) {
            return new WP_Error( 'wpmgr_tidak_terhubung',
                'Site ini tidak dapat menghubungi dashboard: ' . $resp->get_error_message() );
        }
        $kode = (int) wp_remote_retrieve_response_code( $resp );
        if ( 200 !== $kode ) {
            return new WP_Error( 'wpmgr_ditolak',
                'Dashboard menolak konfirmasi (HTTP ' . $kode . '): ' . wp_remote_retrieve_body( $resp ) );
        }
        return true;
    }

    public static function daftarkan_menu() {
        add_options_page( 'WP Manager', 'WP Manager', 'manage_options', 'wpmgr',
            array( __CLASS__, 'render' ) );
    }

    public static function tangani_simpan() {
        if ( ! isset( $_POST['wpmgr_kunci'] ) || ! current_user_can( 'manage_options' ) ) {
            return;
        }
        check_admin_referer( 'wpmgr_simpan' );

        $hasil = self::simpan_kunci( sanitize_text_field( wp_unslash( $_POST['wpmgr_kunci'] ) ) );
        $pesan = is_wp_error( $hasil ) ? $hasil->get_error_message() : 'Terhubung ke dashboard.';
        set_transient( 'wpmgr_pesan', $pesan, 30 );

        wp_safe_redirect( admin_url( 'options-general.php?page=wpmgr' ) );
        exit;
    }

    public static function render() {
        $pesan = get_transient( 'wpmgr_pesan' );
        delete_transient( 'wpmgr_pesan' );
        ?>
        <div class="wrap">
            <h1>WP Manager</h1>
            <?php if ( $pesan ) : ?>
                <div class="notice notice-info"><p><?php echo esc_html( $pesan ); ?></p></div>
            <?php endif; ?>

            <?php if ( self::terpasang() ) : ?>
                <p><strong>Status:</strong> terhubung ke
                   <code><?php echo esc_html( self::dashboard_url() ); ?></code></p>
                <p>ID site: <code><?php echo esc_html( self::site_id() ); ?></code></p>
            <?php else : ?>
                <p>Belum terhubung. Tempel kunci koneksi dari dashboard.</p>
            <?php endif; ?>

            <form method="post">
                <?php wp_nonce_field( 'wpmgr_simpan' ); ?>
                <p>
                    <label for="wpmgr_kunci">Kunci koneksi</label><br>
                    <textarea id="wpmgr_kunci" name="wpmgr_kunci" rows="3" cols="80"></textarea>
                </p>
                <?php submit_button( 'Simpan dan hubungkan' ); ?>
            </form>
        </div>
        <?php
    }
}
```

`update_option( ..., false )` memakai `autoload = false` supaya secret tidak ikut dimuat pada setiap request halaman site.

- [ ] **Step 2: Periksa sintaks**

Run: `php -l connector/wp-manager-connector/includes/class-wpmgr-settings.php`
Expected: `No syntax errors detected`

- [ ] **Step 3: Commit**

```bash
git add connector/wp-manager-connector/includes/class-wpmgr-settings.php
git commit -m "feat(connector): halaman setting, pairing, dan user wpmgr

Konfirmasi pairing membuktikan kepemilikan secret sekaligus menguji arah
keluar site -> dashboard. Secret disimpan dengan autoload=false.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Route REST, guard HMAC, dan endpoint `/ping` + `/inventory`

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-rest.php`
- Create: `connector/wp-manager-connector/includes/class-wpmgr-inventory.php`

**Interfaces:**
- Consumes: `WPMGR_Signing`, `WPMGR_Settings`
- Produces:
  - `WPMGR_REST::daftarkan_route()` — mendaftarkan `wpmgr/v1` untuk `/ping`, `/inventory`, `/update`
  - `WPMGR_REST::guard( WP_REST_Request $request ) : true|WP_Error`
  - `WPMGR_Inventory::kumpulkan() : array` — bentuk persis seperti spec 6.1

- [ ] **Step 1: Implementasi guard dan route**

```php
<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPMGR_REST {

    const NS = 'wpmgr/v1';

    public static function daftarkan_route() {
        $guard = array( __CLASS__, 'guard' );

        register_rest_route( self::NS, '/ping', array(
            'methods'             => 'GET',
            'callback'            => array( __CLASS__, 'ping' ),
            'permission_callback' => $guard,
        ) );

        register_rest_route( self::NS, '/inventory', array(
            'methods'             => 'GET',
            'callback'            => array( __CLASS__, 'inventory' ),
            'permission_callback' => $guard,
        ) );

        register_rest_route( self::NS, '/update', array(
            'methods'             => 'POST',
            'callback'            => array( __CLASS__, 'update' ),
            'permission_callback' => $guard,
        ) );
    }

    private static function tolak( $pesan ) {
        return new WP_Error( 'wpmgr_ditolak', $pesan, array( 'status' => 401 ) );
    }

    public static function guard( $request ) {
        if ( ! WPMGR_Settings::terpasang() ) {
            return self::tolak( 'Connector belum dipasangkan.' );
        }

        $site_id   = (string) $request->get_header( 'x_wpmgr_site' );
        $timestamp = (int) $request->get_header( 'x_wpmgr_timestamp' );
        $nonce     = (string) $request->get_header( 'x_wpmgr_nonce' );
        $signature = (string) $request->get_header( 'x_wpmgr_signature' );

        if ( ! hash_equals( WPMGR_Settings::site_id(), $site_id ) ) {
            return self::tolak( 'ID site tidak cocok.' );
        }
        if ( abs( time() - $timestamp ) > WPMGR_JENDELA_DETIK ) {
            return self::tolak( 'Timestamp di luar jendela yang diizinkan.' );
        }
        if ( ! preg_match( '/^[0-9a-f]{32}$/', $nonce ) ) {
            return self::tolak( 'Nonce tidak valid.' );
        }
        if ( false !== get_transient( 'wpmgr_nonce_' . $nonce ) ) {
            return self::tolak( 'Nonce sudah pernah dipakai.' );
        }

        $path  = '/wp-json/' . self::NS . $request->get_route();
        $path  = str_replace( '/wp-json/' . self::NS . '/' . self::NS, '/wp-json/' . self::NS, $path );
        $body  = $request->get_body();
        $valid = WPMGR_Signing::verify(
            WPMGR_Settings::secret(), $signature, $request->get_method(), $path, $timestamp, $nonce, $body
        );
        if ( ! $valid ) {
            return self::tolak( 'Tanda tangan tidak cocok.' );
        }

        set_transient( 'wpmgr_nonce_' . $nonce, 1, WPMGR_NONCE_TTL );
        return true;
    }

    public static function ping() {
        return rest_ensure_response( array(
            'connector_version' => WPMGR_VERSION,
            'wp_version'        => get_bloginfo( 'version' ),
            'php_version'       => PHP_VERSION,
            'site_url'          => home_url(),
        ) );
    }

    public static function inventory() {
        return rest_ensure_response( WPMGR_Inventory::kumpulkan() );
    }

    public static function update( $request ) {
        $p = json_decode( $request->get_body(), true );
        if ( ! is_array( $p ) || empty( $p['tipe'] ) || empty( $p['slug'] ) || empty( $p['ke_versi'] ) ) {
            return new WP_Error( 'wpmgr_payload_salah', 'Payload update tidak lengkap.', array( 'status' => 400 ) );
        }
        return rest_ensure_response(
            WPMGR_Updater::jalankan( $p['tipe'], $p['slug'], $p['ke_versi'] )
        );
    }
}
```

`$request->get_route()` mengembalikan route lengkap termasuk namespace (`/wpmgr/v1/ping`). Konstruksi `$path` di atas harus menghasilkan persis `/wp-json/wpmgr/v1/ping` agar cocok dengan yang ditandatangani dashboard. Verifikasi ini pada Step 4.

- [ ] **Step 2: Implementasi `class-wpmgr-inventory.php`**

```php
<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPMGR_Inventory {

    /**
     * Memaksa WordPress memeriksa update lebih dulu, karena transient update
     * bisa berumur belasan jam dan dashboard yang menampilkan data basi sama
     * saja dengan dashboard yang salah.
     */
    private static function segarkan() {
        require_once ABSPATH . 'wp-admin/includes/update.php';
        wp_version_check( array(), true );
        wp_update_plugins();
        wp_update_themes();
    }

    public static function kumpulkan() {
        self::segarkan();

        return array(
            'core'    => self::core(),
            'plugins' => self::plugins(),
            'themes'  => self::themes(),
        );
    }

    private static function core() {
        $terpasang = get_bloginfo( 'version' );
        $tersedia  = null;

        $cek = get_site_transient( 'update_core' );
        if ( ! empty( $cek->updates ) ) {
            foreach ( $cek->updates as $u ) {
                if ( isset( $u->response, $u->current ) && 'upgrade' === $u->response ) {
                    $tersedia = $u->current;
                    break;
                }
            }
        }

        return array(
            'slug'            => 'core',
            'nama'            => 'WordPress',
            'versi_terpasang' => $terpasang,
            'versi_tersedia'  => $tersedia,
            'aktif'           => true,
            'auto_update'     => false,
        );
    }

    private static function plugins() {
        require_once ABSPATH . 'wp-admin/includes/plugin.php';

        $semua   = get_plugins();
        $update  = get_site_transient( 'update_plugins' );
        $otomatis = (array) get_option( 'auto_update_plugins', array() );
        $keluar  = array();

        foreach ( $semua as $file => $data ) {
            $tersedia = null;
            if ( isset( $update->response[ $file ]->new_version ) ) {
                $tersedia = $update->response[ $file ]->new_version;
            }
            $keluar[] = array(
                'slug'            => $file,
                'nama'            => $data['Name'],
                'versi_terpasang' => $data['Version'],
                'versi_tersedia'  => $tersedia,
                'aktif'           => is_plugin_active( $file ),
                'auto_update'     => in_array( $file, $otomatis, true ),
            );
        }
        return $keluar;
    }

    private static function themes() {
        $semua    = wp_get_themes();
        $update   = get_site_transient( 'update_themes' );
        $otomatis = (array) get_option( 'auto_update_themes', array() );
        $aktif    = get_stylesheet();
        $keluar   = array();

        foreach ( $semua as $slug => $tema ) {
            $tersedia = null;
            if ( isset( $update->response[ $slug ]['new_version'] ) ) {
                $tersedia = $update->response[ $slug ]['new_version'];
            }
            $keluar[] = array(
                'slug'            => $slug,
                'nama'            => $tema->get( 'Name' ),
                'versi_terpasang' => $tema->get( 'Version' ),
                'versi_tersedia'  => $tersedia,
                'aktif'           => ( $slug === $aktif ),
                'auto_update'     => in_array( $slug, $otomatis, true ),
            );
        }
        return $keluar;
    }
}
```

- [ ] **Step 3: Periksa sintaks kedua berkas**

Run: `php -l connector/wp-manager-connector/includes/class-wpmgr-rest.php && php -l connector/wp-manager-connector/includes/class-wpmgr-inventory.php`
Expected: `No syntax errors detected` dua kali

- [ ] **Step 4: Verifikasi konstruksi `$path` di lingkungan WordPress nyata**

Path yang ditandatangani harus identik di kedua sisi. Setelah kontainer WordPress e2e tersedia (Task 21), jalankan verifikasi ini; sampai saat itu tandai dengan menambahkan `error_log()` sementara pada `guard()`:

```php
error_log( 'WPMGR path yang diverifikasi: ' . $path );
```

Nilai yang tercatat harus persis `/wp-json/wpmgr/v1/ping`. Bila muncul duplikasi namespace, sederhanakan konstruksinya menjadi:

```php
$path = '/wp-json' . $request->get_route();
```

karena `get_route()` sudah memuat `/wpmgr/v1/...`. Pilih bentuk yang menghasilkan nilai benar, hapus `error_log`, lalu lanjutkan.

- [ ] **Step 5: Commit**

```bash
git add connector/wp-manager-connector/includes/class-wpmgr-rest.php connector/wp-manager-connector/includes/class-wpmgr-inventory.php
git commit -m "feat(connector): route REST ber-HMAC dengan endpoint ping dan inventory

Guard menolak timestamp di luar jendela 300 detik dan nonce yang sudah
terpakai. Inventaris memaksa pemeriksaan update lebih dulu agar tidak basi.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Endpoint `/update` yang idempoten

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-updater.php`

**Interfaces:**
- Consumes: `WPMGR_Inventory`
- Produces:
  - `WPMGR_Updater::jalankan( string $tipe, string $slug, string $ke_versi ) : array|WP_Error`
  - Bentuk balasan: `array( 'ok' => bool, 'versi_sebelum' => string, 'versi_sesudah' => string, 'pesan' => string )`
  - `WPMGR_Updater::versi_terpasang( string $tipe, string $slug ) : string|null`

- [ ] **Step 1: Implementasi**

```php
<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPMGR_Updater {

    public static function versi_terpasang( $tipe, $slug ) {
        if ( 'core' === $tipe ) {
            return get_bloginfo( 'version' );
        }
        if ( 'plugin' === $tipe ) {
            require_once ABSPATH . 'wp-admin/includes/plugin.php';
            $semua = get_plugins();
            return isset( $semua[ $slug ]['Version'] ) ? $semua[ $slug ]['Version'] : null;
        }
        if ( 'theme' === $tipe ) {
            $tema = wp_get_theme( $slug );
            return $tema->exists() ? $tema->get( 'Version' ) : null;
        }
        return null;
    }

    /**
     * Idempoten secara sengaja: skenario "update berhasil tetapi respons tidak
     * sampai" pasti terjadi cepat atau lambat, dan retry harus aman.
     */
    public static function jalankan( $tipe, $slug, $ke_versi ) {
        $sebelum = self::versi_terpasang( $tipe, $slug );

        if ( null === $sebelum ) {
            return new WP_Error( 'wpmgr_tidak_ditemukan',
                'Paket tidak ditemukan di site ini.', array( 'status' => 404 ) );
        }

        if ( version_compare( $sebelum, $ke_versi, '>=' ) ) {
            return array(
                'ok'            => true,
                'versi_sebelum' => $sebelum,
                'versi_sesudah' => $sebelum,
                'pesan'         => 'sudah di versi tersebut',
            );
        }

        require_once ABSPATH . 'wp-admin/includes/file.php';
        require_once ABSPATH . 'wp-admin/includes/misc.php';
        require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';
        require_once ABSPATH . 'wp-admin/includes/update.php';

        wp_update_plugins();
        wp_update_themes();

        $skin = new Automatic_Upgrader_Skin();
        $hasil = null;

        if ( 'plugin' === $tipe ) {
            $upgrader = new Plugin_Upgrader( $skin );
            $hasil    = $upgrader->upgrade( $slug );
        } elseif ( 'theme' === $tipe ) {
            $upgrader = new Theme_Upgrader( $skin );
            $hasil    = $upgrader->upgrade( $slug );
        } elseif ( 'core' === $tipe ) {
            $cek = get_site_transient( 'update_core' );
            if ( empty( $cek->updates[0] ) ) {
                return new WP_Error( 'wpmgr_tidak_ada_update',
                    'Tidak ada update core yang tersedia.', array( 'status' => 409 ) );
            }
            $upgrader = new Core_Upgrader( $skin );
            $hasil    = $upgrader->upgrade( $cek->updates[0] );
        } else {
            return new WP_Error( 'wpmgr_tipe_salah', 'Tipe paket tidak dikenal.', array( 'status' => 400 ) );
        }

        if ( is_wp_error( $hasil ) ) {
            return new WP_Error( 'wpmgr_upgrade_gagal',
                $hasil->get_error_message(), array( 'status' => 500 ) );
        }
        if ( false === $hasil ) {
            $pesan = implode( ' | ', (array) $skin->get_upgrade_messages() );
            return new WP_Error( 'wpmgr_upgrade_gagal',
                $pesan ? $pesan : 'Upgrader mengembalikan false tanpa pesan.',
                array( 'status' => 500 ) );
        }

        wp_clean_plugins_cache( true );
        wp_clean_themes_cache( true );
        $sesudah = self::versi_terpasang( $tipe, $slug );

        return array(
            'ok'            => true,
            'versi_sebelum' => $sebelum,
            'versi_sesudah' => null === $sesudah ? $ke_versi : $sesudah,
            'pesan'         => implode( ' | ', (array) $skin->get_upgrade_messages() ),
        );
    }
}
```

`version_compare( $sebelum, $ke_versi, '>=' )` dipakai alih-alih perbandingan string persis, karena site yang sudah lebih baru dari versi yang diminta juga tidak boleh di-downgrade oleh retry yang terlambat.

- [ ] **Step 2: Periksa sintaks**

Run: `php -l connector/wp-manager-connector/includes/class-wpmgr-updater.php`
Expected: `No syntax errors detected`

- [ ] **Step 3: Commit**

```bash
git add connector/wp-manager-connector/includes/class-wpmgr-updater.php
git commit -m "feat(connector): endpoint update idempoten untuk plugin, tema, dan core

Versi terpasang diperiksa lebih dulu; bila sudah sama atau lebih baru,
balas sukses tanpa melakukan apa pun sehingga retry selalu aman.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: Handler SSO

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-sso.php`
- Create: `connector/tests/SsoTest.php`

**Interfaces:**
- Consumes: `WPMGR_Settings`
- Produces:
  - `WPMGR_SSO::tangani_permintaan()` — hook `init`
  - `WPMGR_SSO::b64url_decode( string $s ) : string|false`
  - `WPMGR_SSO::periksa_token( string $secret, string $token, int $now ) : array|WP_Error` — PHP murni, dapat diuji tanpa WordPress

- [ ] **Step 1: Tulis test PHP yang gagal**

Fungsi pemeriksaan token dipisahkan dari hook agar dapat diuji tanpa memuat WordPress.

```php
<?php
// connector/tests/SsoTest.php
use PHPUnit\Framework\TestCase;

final class SsoTest extends TestCase {

    private const SECRET = 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc';

    private function buat_token( array $payload, string $secret = self::SECRET ): string {
        $body = rtrim( strtr( base64_encode( json_encode( $payload ) ), '+/', '-_' ), '=' );
        return $body . '.' . hash_hmac( 'sha256', $body, $secret );
    }

    public function test_token_valid_terbaca(): void {
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000, 'nonce' => str_repeat( 'a', 32 ) ) );
        $p = WPMGR_SSO::periksa_token( self::SECRET, $t, 990 );
        $this->assertSame( 's1', $p['site_id'] );
        $this->assertSame( str_repeat( 'a', 32 ), $p['nonce'] );
    }

    public function test_token_kedaluwarsa_ditolak(): void {
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000, 'nonce' => str_repeat( 'a', 32 ) ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $t, 1001 ) );
    }

    public function test_secret_lain_ditolak(): void {
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000, 'nonce' => str_repeat( 'a', 32 ) ), str_repeat( 'd', 64 ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $t, 990 ) );
    }

    public function test_token_tanpa_titik_ditolak(): void {
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, 'tanpatitik', 990 ) );
    }

    public function test_nonce_tidak_valid_ditolak(): void {
        $t = $this->buat_token( array( 'site_id' => 's1', 'exp' => 1000, 'nonce' => 'pendek' ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_SSO::periksa_token( self::SECRET, $t, 990 ) );
    }
}
```

Tambahkan stub `WP_Error` ke `connector/tests/bootstrap.php` agar test berjalan tanpa WordPress:

```php
<?php
// connector/tests/bootstrap.php
if ( ! class_exists( 'WP_Error' ) ) {
    class WP_Error {
        public $kode;
        public $pesan;
        public function __construct( $kode = '', $pesan = '' ) {
            $this->kode  = $kode;
            $this->pesan = $pesan;
        }
        public function get_error_message() {
            return $this->pesan;
        }
    }
}
if ( ! function_exists( 'is_wp_error' ) ) {
    function is_wp_error( $x ) {
        return $x instanceof WP_Error;
    }
}

require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-signing.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-sso.php';
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `cd connector && vendor/bin/phpunit`
Expected: FAIL — `Class "WPMGR_SSO" not found`

- [ ] **Step 3: Implementasi**

```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

class WPMGR_SSO {

    const PARAM = 'wpmgr_sso';

    public static function b64url_decode( $s ) {
        $pad = strlen( $s ) % 4;
        if ( $pad ) {
            $s .= str_repeat( '=', 4 - $pad );
        }
        return base64_decode( strtr( $s, '-_', '+/' ), true );
    }

    /**
     * Tanda tangan diverifikasi SEBELUM payload di-decode, sehingga tidak ada
     * JSON dari pihak tak dikenal yang pernah diurai.
     */
    public static function periksa_token( $secret, $token, $now ) {
        $pos = strpos( (string) $token, '.' );
        if ( false === $pos || 0 === $pos || $pos === strlen( $token ) - 1 ) {
            return new WP_Error( 'wpmgr_sso_bentuk', 'Bentuk token salah.' );
        }

        $body = substr( $token, 0, $pos );
        $sig  = substr( $token, $pos + 1 );

        if ( ! hash_equals( hash_hmac( 'sha256', $body, $secret ), $sig ) ) {
            return new WP_Error( 'wpmgr_sso_tanda_tangan', 'Tanda tangan token salah.' );
        }

        $mentah = self::b64url_decode( $body );
        if ( false === $mentah ) {
            return new WP_Error( 'wpmgr_sso_payload', 'Payload tidak dapat dibaca.' );
        }
        $payload = json_decode( $mentah, true );
        if ( ! is_array( $payload ) || empty( $payload['nonce'] ) || empty( $payload['exp'] ) ) {
            return new WP_Error( 'wpmgr_sso_payload', 'Payload tidak lengkap.' );
        }
        if ( ! preg_match( '/^[0-9a-f]{32}$/', $payload['nonce'] ) ) {
            return new WP_Error( 'wpmgr_sso_nonce', 'Nonce tidak valid.' );
        }
        if ( (int) $now > (int) $payload['exp'] ) {
            return new WP_Error( 'wpmgr_sso_kedaluwarsa', 'Token kedaluwarsa.' );
        }
        return $payload;
    }

    public static function tangani_permintaan() {
        if ( empty( $_GET[ self::PARAM ] ) || ! WPMGR_Settings::terpasang() ) {
            return;
        }

        $token   = sanitize_text_field( wp_unslash( $_GET[ self::PARAM ] ) );
        $payload = self::periksa_token( WPMGR_Settings::secret(), $token, time() );

        if ( is_wp_error( $payload ) ) {
            wp_die( esc_html( $payload->get_error_message() ), 'SSO ditolak', array( 'response' => 403 ) );
        }

        $kunci_nonce = 'wpmgr_sso_' . $payload['nonce'];
        if ( false !== get_transient( $kunci_nonce ) ) {
            wp_die( 'Token SSO sudah pernah dipakai.', 'SSO ditolak', array( 'response' => 403 ) );
        }

        // Ditandai SEBELUM login: request yang gagal di tengah tidak boleh
        // menyisakan token yang masih dapat dipakai ulang.
        set_transient( $kunci_nonce, 1, WPMGR_SSO_TTL );

        $user_id = WPMGR_Settings::pastikan_user();
        if ( ! $user_id ) {
            wp_die( 'User wpmgr tidak dapat dibuat.', 'SSO gagal', array( 'response' => 500 ) );
        }

        wp_set_current_user( $user_id );
        wp_set_auth_cookie( $user_id, false );

        wp_safe_redirect( admin_url() );
        exit;
    }
}
```

- [ ] **Step 4: Jalankan test, pastikan lolos**

Run: `cd connector && vendor/bin/phpunit`
Expected: PASS, 11 test (6 signing + 5 SSO)

- [ ] **Step 5: Verifikasi plugin dapat diaktifkan**

Run: `php -l connector/wp-manager-connector/wp-manager-connector.php`
Expected: `No syntax errors detected`

- [ ] **Step 6: Commit**

```bash
git add connector/
git commit -m "feat(connector): handler SSO sekali-pakai

Nonce ditandai terpakai sebelum cookie login diset, sehingga request yang
gagal di tengah tidak menyisakan token yang masih berlaku.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Fase 5 — Dashboard Web

### Task 18: App factory, sesi, dan login

**Files:**
- Create: `src/wpmgr/web/__init__.py`, `src/wpmgr/web/app.py`, `src/wpmgr/web/auth.py`
- Create: `src/wpmgr/templates/base.html`, `src/wpmgr/templates/login.html`
- Create: `tests/integration/test_auth.py`

**Interfaces:**
- Consumes: `models.User`, `config`
- Produces:
  - `web.app.buat_app() -> FastAPI`
  - `web.app.app` — instance untuk uvicorn (`wpmgr.web.app:app`)
  - `web.auth.pengguna_saat_ini(request) -> User` — dependency, redirect 303 ke `/login` bila belum masuk
  - `web.auth.pengguna_api(request) -> User` — dependency versi API, melempar 401
  - `web.app.templates` — `Jinja2Templates`

- [ ] **Step 1: Tulis test yang gagal**

```python
# tests/integration/test_auth.py
import uuid

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from wpmgr.models import User

pytestmark = pytest.mark.integration


@pytest.fixture
def klien(engine, monkeypatch):
    from wpmgr import db
    from sqlalchemy.orm import sessionmaker
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    return TestClient(buat_app(), follow_redirects=False)


@pytest.fixture
def pengguna(sesi):
    u = User(id=uuid.uuid4(), email="a@b.test", nama="Uji",
             password_hash=PasswordHasher().hash("sandi-benar"))
    sesi.add(u)
    sesi.commit()
    return u


def test_halaman_dilindungi_mengarahkan_ke_login(klien):
    r = klien.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_api_dilindungi_membalas_401(klien):
    assert klien.get("/api/sites").status_code == 401


def test_login_dengan_sandi_benar(klien, pengguna):
    r = klien.post("/login", data={"email": "a@b.test", "password": "sandi-benar"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert klien.get("/api/sites").status_code == 200


def test_login_dengan_sandi_salah_ditolak(klien, pengguna):
    r = klien.post("/login", data={"email": "a@b.test", "password": "salah"})
    assert r.status_code == 200
    assert "tidak cocok" in r.text.lower()


def test_email_tidak_dikenal_ditolak(klien, pengguna):
    r = klien.post("/login", data={"email": "x@y.test", "password": "apa-saja"})
    assert r.status_code == 200
    assert "tidak cocok" in r.text.lower()


def test_logout_mengakhiri_sesi(klien, pengguna):
    klien.post("/login", data={"email": "a@b.test", "password": "sandi-benar"})
    klien.post("/logout")
    assert klien.get("/api/sites").status_code == 401
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/integration/test_auth.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'wpmgr.web'`

- [ ] **Step 3: Implementasi `auth.py`**

```python
# src/wpmgr/web/auth.py
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from wpmgr import db
from wpmgr.models import User

ph = PasswordHasher()
KUNCI_SESI = "user_id"


class ButuhLogin(Exception):
    """Dilempar pada route halaman; ditangkap handler yang mengarahkan ke /login."""


def periksa_sandi(email: str, password: str) -> User | None:
    with db.SessionLocal() as sesi:
        pengguna = sesi.scalar(select(User).where(User.email == email))
        if pengguna is None:
            ph.hash(password)  # samakan waktu respons agar email tak dapat ditebak
            return None
        try:
            ph.verify(pengguna.password_hash, password)
        except VerifyMismatchError:
            return None
        return pengguna


def _ambil(request: Request) -> User | None:
    raw = request.session.get(KUNCI_SESI)
    if not raw:
        return None
    with db.SessionLocal() as sesi:
        return sesi.get(User, uuid.UUID(raw))


def pengguna_saat_ini(request: Request) -> User:
    pengguna = _ambil(request)
    if pengguna is None:
        raise ButuhLogin()
    return pengguna


def pengguna_api(request: Request) -> User:
    pengguna = _ambil(request)
    if pengguna is None:
        raise HTTPException(status_code=401, detail="Belum masuk")
    return pengguna
```

`ph.hash(password)` pada cabang email tidak dikenal disengaja: tanpa itu, balasan untuk email yang ada jauh lebih lambat daripada yang tidak ada, dan selisih waktu itu cukup untuk mendaftar siapa saja yang punya akun.

- [ ] **Step 4: Implementasi `app.py`**

```python
# src/wpmgr/web/app.py
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from wpmgr.config import get_settings
from wpmgr.web.auth import KUNCI_SESI, ButuhLogin, periksa_sandi

AKAR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(AKAR / "templates"))


def buat_app() -> FastAPI:
    app = FastAPI(title="WP Manager")
    app.add_middleware(
        SessionMiddleware,
        secret_key=get_settings().session_secret,
        https_only=True,
        same_site="lax",
        max_age=60 * 60 * 12,
    )
    app.mount("/static", StaticFiles(directory=str(AKAR / "static")), name="static")

    @app.exception_handler(ButuhLogin)
    async def _ke_login(request: Request, exc: ButuhLogin):
        return RedirectResponse("/login", status_code=303)

    @app.get("/login")
    async def form_login(request: Request):
        return templates.TemplateResponse(request, "login.html", {"galat": None})

    @app.post("/login")
    async def proses_login(request: Request, email: str = Form(...), password: str = Form(...)):
        pengguna = periksa_sandi(email, password)
        if pengguna is None:
            return templates.TemplateResponse(
                request, "login.html", {"galat": "Email dan kata sandi tidak cocok."}
            )
        request.session[KUNCI_SESI] = str(pengguna.id)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    from wpmgr.web.routes_api import router as api_router
    from wpmgr.web.routes_pages import router as pages_router
    from wpmgr.web.routes_pair import router as pair_router

    app.include_router(api_router)
    app.include_router(pair_router)
    app.include_router(pages_router)
    return app


app = buat_app()
```

- [ ] **Step 5: Tulis `base.html` dan `login.html`**

```html
<!-- src/wpmgr/templates/base.html -->
<!DOCTYPE html>
<html lang="id" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block judul %}WP Manager{% endblock %}</title>
  <link rel="stylesheet" href="/static/vendor/datagrid/themes.css">
  <link rel="stylesheet" href="/static/vendor/datagrid/datagrid.css">
  <link rel="stylesheet" href="/static/app/app.css">
  <script defer src="/static/vendor/alpine.min.js"></script>
  <script src="/static/vendor/datagrid/datagrid.js"></script>
</head>
<body>
  <nav class="bilah">
    <strong>WP Manager</strong>
    <a href="/">Semua Update</a>
    <a href="/sites">Site</a>
    <a href="/activity">Aktivitas</a>
    <a href="/sites/new">Tambah Site</a>
    <form method="post" action="/logout" class="kanan">
      <button type="submit">Keluar</button>
    </form>
  </nav>
  <main>{% block isi %}{% endblock %}</main>
</body>
</html>
```

```html
<!-- src/wpmgr/templates/login.html -->
{% extends "base.html" %}
{% block judul %}Masuk — WP Manager{% endblock %}
{% block isi %}
<h1>Masuk</h1>
{% if galat %}<p class="galat">{{ galat }}</p>{% endif %}
<form method="post" action="/login">
  <p><label>Email<br><input type="email" name="email" required autofocus></label></p>
  <p><label>Kata sandi<br><input type="password" name="password" required></label></p>
  <p><button type="submit">Masuk</button></p>
</form>
{% endblock %}
```

`base.html` memuat navigasi yang menunjuk ke route yang dibuat pada Task 20–21. Sampai task itu selesai, tautannya akan 404 — itu wajar dan tidak menghalangi test login.

Alpine.js di-*vendor* sebagai berkas lokal, bukan dimuat dari CDN. Untuk aplikasi biasa, `integrity="sha384-..."` pada tag CDN sudah memadai. Dashboard ini bukan aplikasi biasa: ia memegang kunci ke seluruh site client, sehingga skrip apa pun yang berjalan di halamannya dapat memicu SSO atau menjalankan update atas nama pengguna yang sedang masuk. Menyalin satu berkas 45 KB menghapus seluruh kelas risiko itu, sekaligus membuat dashboard tetap berfungsi saat VPS tidak dapat menjangkau internet publik.

- [ ] **Step 6: Salin DataGrid dan Alpine ke direktori statis**

```bash
mkdir -p src/wpmgr/static/vendor/datagrid src/wpmgr/static/app
cp "D:/Workspace/datagridcustom/js/datagrid.js"  src/wpmgr/static/vendor/datagrid/
cp "D:/Workspace/datagridcustom/css/datagrid.css" src/wpmgr/static/vendor/datagrid/
cp "D:/Workspace/datagridcustom/css/themes.css"   src/wpmgr/static/vendor/datagrid/
curl -fsSL https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js \
     -o src/wpmgr/static/vendor/alpine.min.js
printf '.bilah{display:flex;gap:1rem;align-items:center;padding:.75rem 1rem;border-bottom:1px solid #ddd}\n.bilah .kanan{margin-left:auto}\nmain{padding:1rem}\n.galat{color:#b00}\n' > src/wpmgr/static/app/app.css
```

Berkas Alpine ikut di-commit ke repo. Memperbaruinya kelak adalah tindakan sadar: unduh versi baru, jalankan test, commit — bukan sesuatu yang diam-diam berubah di bawah kaki kita.

- [ ] **Step 7: Buat berkas router kosong agar impor di `app.py` berhasil**

```python
# src/wpmgr/web/routes_api.py
from fastapi import APIRouter

router = APIRouter()
```

Buat `routes_pair.py` dan `routes_pages.py` dengan isi yang sama persis. Ketiganya diisi pada Task 19–21.

- [ ] **Step 8: Jalankan test, pastikan lolos**

Run: `pytest tests/integration/test_auth.py -v -m integration`
Expected: FAIL pada `test_api_dilindungi_membalas_401` dan `test_login_dengan_sandi_benar` karena `/api/sites` belum ada. Tandai kedua test itu `@pytest.mark.xfail(reason="menunggu Task 20")` untuk sementara, jalankan ulang, dan pastikan empat test sisanya PASS. Hapus penanda `xfail` pada Step 5 Task 20.

- [ ] **Step 9: Commit**

```bash
git add src/wpmgr/web src/wpmgr/templates src/wpmgr/static tests/integration/test_auth.py
git commit -m "feat: app factory, sesi cookie, dan login argon2

Sandi tetap di-hash saat email tidak dikenal agar selisih waktu respons
tidak membocorkan email mana yang punya akun.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 19: Pendaftaran site dan `/api/pair/confirm`

**Files:**
- Create: `src/wpmgr/web/routes_pair.py` (menggantikan stub), `src/wpmgr/pairing.py`
- Create: `tests/integration/test_pairing.py`

**Interfaces:**
- Consumes: `crypto`, `signing`, `models`, `queue.buat_job`
- Produces:
  - `pairing.buat_site(sesi, nama, url, client_id, dibuat_oleh) -> tuple[Site, str]` → site dan kunci koneksi
  - `pairing.kunci_koneksi(site_id: str, secret_hex: str, dashboard_url: str) -> str`
  - `POST /api/pair/confirm` — memverifikasi HMAC, memperbarui versi, membuat job `verify_site`
  - Route halaman `POST /sites` menerima form dan menampilkan kunci

- [ ] **Step 1: Tulis test yang gagal**

```python
# tests/integration/test_pairing.py
import base64
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from wpmgr.crypto import dekripsi_secret
from wpmgr.models import Job, JobType, Site, SiteStatus
from wpmgr.pairing import buat_site, kunci_koneksi
from wpmgr.signing import sign

pytestmark = pytest.mark.integration

PATH = "/api/pair/confirm"


@pytest.fixture
def klien(engine, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from wpmgr import db
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    return TestClient(buat_app(), follow_redirects=False)


def test_kunci_koneksi_dapat_diurai_kembali():
    k = kunci_koneksi("sid", "a" * 64, "https://dash.test")
    mentah = base64.urlsafe_b64decode(k + "=" * (-len(k) % 4)).decode()
    assert mentah.split(":") == ["sid", "a" * 64, "https://dash.test"]


def test_buat_site_menyimpan_secret_terenkripsi(sesi):
    site, kunci = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    assert site.status == SiteStatus.pending_pair
    assert site.secret_terenkripsi != b""
    assert len(dekripsi_secret(site.secret_terenkripsi)) == 64
    assert kunci


def test_url_http_ditolak(sesi):
    with pytest.raises(ValueError):
        buat_site(sesi, "Tidak Aman", "http://contoh.test", None, None)


def _kirim(klien, site, secret, body_obj, ts=None):
    body = json.dumps(body_obj, separators=(",", ":")).encode()
    ts = ts or int(time.time())
    nonce = uuid.uuid4().hex
    return klien.post(
        PATH,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Wpmgr-Site": str(site.id),
            "X-Wpmgr-Timestamp": str(ts),
            "X-Wpmgr-Nonce": nonce,
            "X-Wpmgr-Signature": sign(secret, "POST", PATH, ts, nonce, body),
        },
    )


def test_confirm_sah_membuat_job_verify(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    secret = dekripsi_secret(site.secret_terenkripsi)

    r = _kirim(klien, site, secret,
               {"connector_version": "1.0", "wp_version": "6.5", "php_version": "8.1"})

    assert r.status_code == 200
    sesi.expire_all()
    disimpan = sesi.get(Site, site.id)
    assert disimpan.connector_version == "1.0"
    assert sesi.query(Job).filter_by(site_id=site.id, tipe=JobType.verify_site).count() == 1


def test_confirm_dengan_tanda_tangan_salah_ditolak(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    r = _kirim(klien, site, "f" * 64, {"connector_version": "1.0"})
    assert r.status_code == 401


def test_confirm_dengan_timestamp_kedaluwarsa_ditolak(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    secret = dekripsi_secret(site.secret_terenkripsi)
    r = _kirim(klien, site, secret, {"connector_version": "1.0"}, ts=int(time.time()) - 400)
    assert r.status_code == 401


def test_confirm_untuk_site_tidak_dikenal_ditolak(klien):
    body = b"{}"
    ts, nonce = int(time.time()), uuid.uuid4().hex
    r = klien.post(PATH, content=body, headers={
        "X-Wpmgr-Site": str(uuid.uuid4()),
        "X-Wpmgr-Timestamp": str(ts),
        "X-Wpmgr-Nonce": nonce,
        "X-Wpmgr-Signature": sign("a" * 64, "POST", PATH, ts, nonce, body),
    })
    assert r.status_code == 401


def test_confirm_tidak_butuh_sesi_login(sesi, klien):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    secret = dekripsi_secret(site.secret_terenkripsi)
    assert _kirim(klien, site, secret, {"connector_version": "1.0"}).status_code == 200
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/integration/test_pairing.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'wpmgr.pairing'`

- [ ] **Step 3: Implementasi `pairing.py`**

```python
# src/wpmgr/pairing.py
import base64
import uuid

from sqlalchemy.orm import Session

from wpmgr.config import get_settings
from wpmgr.crypto import enkripsi_secret, secret_baru
from wpmgr.models import Site, SiteStatus


def kunci_koneksi(site_id: str, secret_hex: str, dashboard_url: str) -> str:
    mentah = f"{site_id}:{secret_hex}:{dashboard_url.rstrip('/')}".encode("utf-8")
    return base64.urlsafe_b64encode(mentah).decode("ascii").rstrip("=")


def buat_site(
    sesi: Session,
    nama: str,
    url: str,
    client_id: uuid.UUID | None,
    dibuat_oleh: uuid.UUID | None,
) -> tuple[Site, str]:
    url = url.strip().rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("URL site wajib berskema https://")

    secret = secret_baru()
    site = Site(
        id=uuid.uuid4(),
        client_id=client_id,
        nama=nama.strip(),
        url=url,
        status=SiteStatus.pending_pair,
        secret_terenkripsi=enkripsi_secret(secret),
    )
    sesi.add(site)
    sesi.commit()
    return site, kunci_koneksi(str(site.id), secret, get_settings().base_url)
```

- [ ] **Step 4: Implementasi `routes_pair.py`**

```python
# src/wpmgr/web/routes_pair.py
import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from wpmgr.crypto import dekripsi_secret
from wpmgr import db
from wpmgr.jobs.queue import buat_job
from wpmgr.models import ActivityLog, Job, JobStatus, JobType, Site
from wpmgr.signing import JENDELA_DETIK, verify

router = APIRouter()
PATH = "/api/pair/confirm"


@router.post(PATH)
async def konfirmasi_pairing(request: Request):
    body = await request.body()
    h = request.headers

    try:
        site_id = uuid.UUID(h.get("X-Wpmgr-Site", ""))
        timestamp = int(h.get("X-Wpmgr-Timestamp", "0"))
    except ValueError:
        raise HTTPException(status_code=401, detail="Header tidak valid")

    nonce = h.get("X-Wpmgr-Nonce", "")
    signature = h.get("X-Wpmgr-Signature", "")

    if abs(int(time.time()) - timestamp) > JENDELA_DETIK:
        raise HTTPException(status_code=401, detail="Timestamp di luar jendela")

    with db.SessionLocal() as sesi:
        site = sesi.get(Site, site_id)
        if site is None:
            raise HTTPException(status_code=401, detail="Site tidak dikenal")

        secret = dekripsi_secret(site.secret_terenkripsi)
        if not verify(secret, signature, "POST", PATH, timestamp, nonce, body):
            raise HTTPException(status_code=401, detail="Tanda tangan tidak cocok")

        muatan = await request.json() if body else {}
        site.connector_version = muatan.get("connector_version")
        site.wp_version = muatan.get("wp_version")
        site.php_version = muatan.get("php_version")
        sesi.add(
            ActivityLog(site_id=site.id, level="info",
                        pesan="Connector mengonfirmasi pairing",
                        detail={"connector_version": site.connector_version})
        )
        sesi.commit()

        tertunda = sesi.query(Job).filter(
            Job.site_id == site.id,
            Job.tipe == JobType.verify_site,
            Job.status.in_([JobStatus.pending, JobStatus.running]),
        ).count()
        if tertunda == 0:
            buat_job(sesi, site.id, JobType.verify_site)

    return {"ok": True}
```

Route ini sengaja tidak memverifikasi arah masuk sendiri. Ia hanya membuat job `verify_site`; worker yang memanggil site. Itulah yang menjaga aturan "proses web tidak pernah memanggil site client" tetap utuh, dan membuat pairing tidak menggantung 15 detik ketika site client sedang lambat.

- [ ] **Step 5: Tambahkan rate limit pada `/api/pair/confirm`**

Spec bagian 10 poin 9 mensyaratkan 10 percobaan per menit per IP. Endpoint ini satu-satunya yang terbuka tanpa sesi login, sehingga ia adalah satu-satunya permukaan yang dapat dipakai menebak `site_id` dan secret dari luar.

Tambahkan test lebih dulu:

```python
# tambahkan ke tests/integration/test_pairing.py
def test_rate_limit_menolak_percobaan_berlebihan(sesi, klien):
    from wpmgr.web.routes_pair import _pembatas

    _pembatas.clear()
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)

    kode = [_kirim(klien, site, "f" * 64, {"x": 1}).status_code for _ in range(12)]
    assert kode[:10] == [401] * 10
    assert kode[10] == 429
    assert kode[11] == 429
```

Lalu implementasikan di `src/wpmgr/web/routes_pair.py`:

```python
import time as _time
from collections import defaultdict, deque

BATAS_PER_MENIT = 10
_pembatas: dict[str, deque] = defaultdict(deque)


def _lolos_rate_limit(ip: str) -> bool:
    sekarang = _time.monotonic()
    jejak = _pembatas[ip]
    while jejak and sekarang - jejak[0] > 60:
        jejak.popleft()
    if len(jejak) >= BATAS_PER_MENIT:
        return False
    jejak.append(sekarang)
    return True
```

dan panggil di awal handler, sebelum pekerjaan apa pun:

```python
    ip = request.client.host if request.client else "tidak-diketahui"
    if not _lolos_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Terlalu banyak percobaan")
```

Penyimpanan dalam memori memadai di sini: hanya ada satu proses web, dan kehilangan hitungan saat restart tidak berbahaya karena pembatas ini menahan penebakan otomatis, bukan menegakkan kuota. Bila kelak dijalankan lebih dari satu proses web, pindahkan hitungan ke tabel database.

- [ ] **Step 6: Jalankan test, pastikan lolos**

Run: `pytest tests/integration/test_pairing.py -v -m integration`
Expected: PASS, 9 test

- [ ] **Step 7: Commit**

```bash
git add src/wpmgr/pairing.py src/wpmgr/web/routes_pair.py tests/integration/test_pairing.py
git commit -m "feat: pendaftaran site dan endpoint konfirmasi pairing

Konfirmasi hanya membuat job verify_site; worker yang menguji arah masuk,
sehingga request pairing tidak pernah menunggu site client yang lambat.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 20: JSON API

**Files:**
- Modify: `src/wpmgr/web/routes_api.py` (menggantikan stub)
- Create: `tests/integration/test_api.py`
- Modify: `tests/integration/test_auth.py` (hapus penanda `xfail` dari Task 18)

**Interfaces:**
- Consumes: `auth.pengguna_api`, `pairing`, `queue.buat_job`, `sso.buat_token`, `crypto`
- Produces (semua butuh sesi login kecuali disebut lain):
  - `GET /api/packages` → `[{id, site_id, site_nama, client_nama, tipe, slug, nama, versi_terpasang, versi_tersedia, aktif, last_scan_at}]`
  - `GET /api/sites` → `[{id, nama, url, client_nama, status, wp_version, php_version, jumlah_update, last_seen_at, last_scan_at, last_error}]`
  - `POST /api/jobs/update` body `{items: [{site_id, tipe, slug, ke_versi}]}` → `{job_ids: [int]}`
  - `POST /api/jobs/scan` body `{site_id}` → `{job_id: int}`
  - `GET /api/jobs/active` → `[{id, site_id, site_nama, tipe, status, slug, attempts, error}]`
  - `GET /api/sso/{site_id}` → `{url: str}`

- [ ] **Step 1: Tulis test yang gagal**

```python
# tests/integration/test_api.py
import uuid
from datetime import datetime, timezone

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from wpmgr.crypto import dekripsi_secret
from wpmgr.models import (
    Job, JobStatus, JobType, PackageType, Site, SitePackage, SiteStatus, User,
)
from wpmgr.pairing import buat_site
from wpmgr.sso import baca_token

pytestmark = pytest.mark.integration


@pytest.fixture
def klien(engine, monkeypatch, sesi):
    from sqlalchemy.orm import sessionmaker

    from wpmgr import db
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    sesi.add(User(id=uuid.uuid4(), email="a@b.test", nama="Uji",
                  password_hash=PasswordHasher().hash("sandi")))
    sesi.commit()
    c = TestClient(buat_app(), follow_redirects=False)
    c.post("/login", data={"email": "a@b.test", "password": "sandi"})
    return c


@pytest.fixture
def site_aktif(sesi):
    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    site.status = SiteStatus.active
    sesi.add(SitePackage(
        site_id=site.id, tipe=PackageType.plugin, slug="elementor/elementor.php",
        nama="Elementor", versi_terpasang="3.18.3", versi_tersedia="3.20.1",
        last_scan_at=datetime.now(timezone.utc)))
    sesi.add(SitePackage(
        site_id=site.id, tipe=PackageType.plugin, slug="akismet/akismet.php",
        nama="Akismet", versi_terpasang="5.3", versi_tersedia=None,
        last_scan_at=datetime.now(timezone.utc)))
    sesi.commit()
    return site


def test_packages_hanya_yang_punya_update(klien, site_aktif):
    data = klien.get("/api/packages").json()
    assert len(data) == 1
    assert data[0]["slug"] == "elementor/elementor.php"
    assert data[0]["site_nama"] == "Contoh"


def test_packages_semua_dengan_parameter(klien, site_aktif):
    assert len(klien.get("/api/packages?semua=1").json()) == 2


def test_sites_memuat_jumlah_update(klien, site_aktif):
    data = klien.get("/api/sites").json()
    assert len(data) == 1
    assert data[0]["jumlah_update"] == 1
    assert data[0]["status"] == "active"


def test_buat_job_update_massal(klien, site_aktif, sesi):
    r = klien.post("/api/jobs/update", json={"items": [
        {"site_id": str(site_aktif.id), "tipe": "plugin",
         "slug": "elementor/elementor.php", "ke_versi": "3.20.1"},
    ]})
    assert r.status_code == 200
    assert len(r.json()["job_ids"]) == 1
    j = sesi.query(Job).filter_by(tipe=JobType.update_package).one()
    assert j.payload["ke_versi"] == "3.20.1"
    assert j.dibuat_oleh is not None


def test_update_menolak_site_yang_tidak_ada(klien):
    r = klien.post("/api/jobs/update", json={"items": [
        {"site_id": str(uuid.uuid4()), "tipe": "plugin", "slug": "a", "ke_versi": "1"},
    ]})
    assert r.status_code == 404


def test_buat_job_scan(klien, site_aktif, sesi):
    r = klien.post("/api/jobs/scan", json={"site_id": str(site_aktif.id)})
    assert r.status_code == 200
    assert sesi.query(Job).filter_by(tipe=JobType.scan_site).count() == 1


def test_jobs_active_hanya_pending_dan_running(klien, site_aktif, sesi):
    from wpmgr.jobs.queue import buat_job

    selesai = buat_job(sesi, site_aktif.id, JobType.scan_site)
    selesai.status = JobStatus.success
    buat_job(sesi, site_aktif.id, JobType.update_package, {"slug": "x"})
    sesi.commit()

    data = klien.get("/api/jobs/active").json()
    assert len(data) == 1
    assert data[0]["tipe"] == "update_package"


def test_sso_membalas_url_dengan_token_sah(klien, site_aktif, sesi):
    r = klien.get(f"/api/sso/{site_aktif.id}")
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://contoh.test/?wpmgr_sso=")

    token = url.split("wpmgr_sso=", 1)[1]
    secret = dekripsi_secret(sesi.get(Site, site_aktif.id).secret_terenkripsi)
    assert baca_token(secret, token)["site_id"] == str(site_aktif.id)


def test_sso_mencatat_activity_log(klien, site_aktif, sesi):
    from wpmgr.models import ActivityLog

    klien.get(f"/api/sso/{site_aktif.id}")
    baris = sesi.query(ActivityLog).filter_by(site_id=site_aktif.id).all()
    assert any("SSO" in b.pesan for b in baris)


def test_sso_site_tidak_ada_404(klien):
    assert klien.get(f"/api/sso/{uuid.uuid4()}").status_code == 404
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/integration/test_api.py -v -m integration`
Expected: FAIL — seluruh route membalas 404

- [ ] **Step 3: Implementasi `routes_api.py`**

```python
# src/wpmgr/web/routes_api.py
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from wpmgr.crypto import dekripsi_secret
from wpmgr import db
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog, Client, Job, JobStatus, JobType, PackageType, Site, SitePackage, User,
)
from wpmgr.sso import buat_token
from wpmgr.web.auth import pengguna_api

router = APIRouter()


class ItemUpdate(BaseModel):
    site_id: uuid.UUID
    tipe: str
    slug: str
    ke_versi: str


class PermintaanUpdate(BaseModel):
    items: list[ItemUpdate]


class PermintaanScan(BaseModel):
    site_id: uuid.UUID


def _waktu(nilai):
    return nilai.isoformat() if nilai else None


@router.get("/api/packages")
def daftar_paket(semua: int = 0, pengguna: User = Depends(pengguna_api)):
    with db.SessionLocal() as sesi:
        q = (
            select(SitePackage, Site.nama, Client.nama)
            .join(Site, Site.id == SitePackage.site_id)
            .outerjoin(Client, Client.id == Site.client_id)
        )
        if not semua:
            q = q.where(SitePackage.versi_tersedia.is_not(None))

        return [
            {
                "id": p.id,
                "site_id": str(p.site_id),
                "site_nama": site_nama,
                "client_nama": client_nama or "",
                "tipe": p.tipe.value,
                "slug": p.slug,
                "nama": p.nama,
                "versi_terpasang": p.versi_terpasang,
                "versi_tersedia": p.versi_tersedia,
                "aktif": p.aktif,
                "last_scan_at": _waktu(p.last_scan_at),
            }
            for p, site_nama, client_nama in sesi.execute(q).all()
        ]


@router.get("/api/sites")
def daftar_site(pengguna: User = Depends(pengguna_api)):
    with db.SessionLocal() as sesi:
        jumlah = (
            select(SitePackage.site_id, func.count().label("n"))
            .where(SitePackage.versi_tersedia.is_not(None))
            .group_by(SitePackage.site_id)
            .subquery()
        )
        q = (
            select(Site, Client.nama, func.coalesce(jumlah.c.n, 0))
            .outerjoin(Client, Client.id == Site.client_id)
            .outerjoin(jumlah, jumlah.c.site_id == Site.id)
            .order_by(Site.nama)
        )
        return [
            {
                "id": str(s.id),
                "nama": s.nama,
                "url": s.url,
                "client_nama": client_nama or "",
                "status": s.status.value,
                "wp_version": s.wp_version,
                "php_version": s.php_version,
                "jumlah_update": int(n),
                "last_seen_at": _waktu(s.last_seen_at),
                "last_scan_at": _waktu(s.last_scan_at),
                "last_error": s.last_error,
            }
            for s, client_nama, n in sesi.execute(q).all()
        ]


@router.post("/api/jobs/update")
def buat_job_update(req: PermintaanUpdate, pengguna: User = Depends(pengguna_api)):
    ids = []
    with db.SessionLocal() as sesi:
        for item in req.items:
            site = sesi.get(Site, item.site_id)
            if site is None:
                raise HTTPException(status_code=404, detail=f"Site {item.site_id} tidak ditemukan")

            terpasang = sesi.scalar(
                select(SitePackage.versi_terpasang).where(
                    SitePackage.site_id == item.site_id,
                    SitePackage.tipe == PackageType(item.tipe),
                    SitePackage.slug == item.slug,
                )
            )
            job = buat_job(
                sesi, item.site_id, JobType.update_package,
                {"tipe": item.tipe, "slug": item.slug,
                 "dari_versi": terpasang, "ke_versi": item.ke_versi},
                dibuat_oleh=pengguna.id,
            )
            ids.append(job.id)
    return {"job_ids": ids}


@router.post("/api/jobs/scan")
def buat_job_scan(req: PermintaanScan, pengguna: User = Depends(pengguna_api)):
    with db.SessionLocal() as sesi:
        if sesi.get(Site, req.site_id) is None:
            raise HTTPException(status_code=404, detail="Site tidak ditemukan")
        job = buat_job(sesi, req.site_id, JobType.scan_site, dibuat_oleh=pengguna.id)
    return {"job_id": job.id}


@router.get("/api/jobs/active")
def job_aktif(pengguna: User = Depends(pengguna_api)):
    with db.SessionLocal() as sesi:
        q = (
            select(Job, Site.nama)
            .join(Site, Site.id == Job.site_id)
            .where(Job.status.in_([JobStatus.pending, JobStatus.running]))
            .order_by(Job.id)
        )
        return [
            {
                "id": j.id,
                "site_id": str(j.site_id),
                "site_nama": site_nama,
                "tipe": j.tipe.value,
                "status": j.status.value,
                "slug": j.payload.get("slug"),
                "attempts": j.attempts,
                "error": j.error,
            }
            for j, site_nama in sesi.execute(q).all()
        ]


@router.get("/api/sso/{site_id}")
def url_sso(site_id: uuid.UUID, pengguna: User = Depends(pengguna_api)):
    with db.SessionLocal() as sesi:
        site = sesi.get(Site, site_id)
        if site is None:
            raise HTTPException(status_code=404, detail="Site tidak ditemukan")

        token = buat_token(dekripsi_secret(site.secret_terenkripsi), str(site.id))
        sesi.add(
            ActivityLog(site_id=site.id, user_id=pengguna.id, level="info",
                        pesan=f"SSO dibuka oleh {pengguna.email}")
        )
        sesi.commit()
        return {"url": f"{site.url}/?wpmgr_sso={token}"}
```

- [ ] **Step 4: Jalankan test, pastikan lolos**

Run: `pytest tests/integration/test_api.py -v -m integration`
Expected: PASS, 10 test

- [ ] **Step 5: Tambahkan pencabutan site**

Spec bagian 6.3 mensyaratkan pencabutan: menghapus site di dashboard menghapus barisnya berikut secret-nya, sehingga tidak ada lagi pihak yang dapat memerintah site itu.

Test lebih dulu:

```python
# tambahkan ke tests/integration/test_api.py
def test_hapus_site_menghapus_baris_dan_paketnya(klien, site_aktif, sesi):
    r = klien.delete(f"/api/sites/{site_aktif.id}")
    assert r.status_code == 200
    sesi.expire_all()
    assert sesi.get(Site, site_aktif.id) is None
    assert sesi.query(SitePackage).filter_by(site_id=site_aktif.id).count() == 0


def test_hapus_site_tidak_ada_404(klien):
    assert klien.delete(f"/api/sites/{uuid.uuid4()}").status_code == 404
```

Implementasi di `routes_api.py`:

```python
@router.delete("/api/sites/{site_id}")
def hapus_site(site_id: uuid.UUID, pengguna: User = Depends(pengguna_api)):
    with db.SessionLocal() as sesi:
        site = sesi.get(Site, site_id)
        if site is None:
            raise HTTPException(status_code=404, detail="Site tidak ditemukan")
        nama = site.nama
        sesi.delete(site)
        sesi.add(
            ActivityLog(level="warning",
                        user_id=pengguna.id,
                        pesan=f"Site '{nama}' dicabut oleh {pengguna.email}")
        )
        sesi.commit()
    return {"ok": True}
```

`ActivityLog` untuk penghapusan sengaja dibuat tanpa `site_id`: baris site-nya sudah tidak ada, dan `ON DELETE CASCADE` akan ikut menghapus catatan itu justru pada saat ia paling dibutuhkan. Jejak pencabutan harus hidup lebih lama daripada site yang dicabut.

Plugin di sisi site harus dihapus manual — tambahkan kalimat itu pada `site_detail.html` di dekat tombol hapus yang dibuat pada Task 21.

- [ ] **Step 6: Hapus penanda `xfail` dari Task 18 dan jalankan ulang**

Run: `pytest tests/integration/test_auth.py -v tests/integration/test_api.py -m integration`
Expected: PASS, 6 + 12 test

- [ ] **Step 7: Commit**

```bash
git add src/wpmgr/web/routes_api.py tests/integration/test_api.py tests/integration/test_auth.py
git commit -m "feat: JSON API untuk paket, site, job, dan SSO

Setiap pembuatan job mencatat pengguna yang memintanya, dan setiap pembukaan
SSO masuk ke activity_log.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 21: Halaman dashboard dan integrasi DataGrid

**Files:**
- Modify: `src/wpmgr/web/routes_pages.py` (menggantikan stub)
- Create: `src/wpmgr/templates/updates.html`, `sites.html`, `site_detail.html`, `site_new.html`, `activity.html`
- Create: `src/wpmgr/static/app/updates.js`, `src/wpmgr/static/app/sites.js`
- Create: `tests/integration/test_pages.py`

**Interfaces:**
- Consumes: `auth.pengguna_saat_ini`, `pairing.buat_site`, API dari Task 20
- Produces: route `GET /`, `GET /sites`, `GET /sites/new`, `POST /sites`, `GET /sites/{id}`, `GET /activity`

- [ ] **Step 1: Tulis test yang gagal**

```python
# tests/integration/test_pages.py
import uuid

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from wpmgr.models import Site, SiteStatus, User

pytestmark = pytest.mark.integration


@pytest.fixture
def klien(engine, monkeypatch, sesi):
    from sqlalchemy.orm import sessionmaker

    from wpmgr import db
    from wpmgr.web.app import buat_app

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    sesi.add(User(id=uuid.uuid4(), email="a@b.test", nama="Uji",
                  password_hash=PasswordHasher().hash("sandi")))
    sesi.commit()
    c = TestClient(buat_app(), follow_redirects=False)
    c.post("/login", data={"email": "a@b.test", "password": "sandi"})
    return c


@pytest.mark.parametrize("jalur", ["/", "/sites", "/sites/new", "/activity"])
def test_halaman_terbuka_dan_memuat_datagrid(klien, jalur):
    r = klien.get(jalur)
    assert r.status_code == 200
    assert "/static/vendor/datagrid/datagrid.js" in r.text


def test_buat_site_menampilkan_kunci_koneksi(klien, sesi):
    r = klien.post("/sites", data={"nama": "Client A", "url": "https://client-a.test"})
    assert r.status_code == 200
    assert "kunci koneksi" in r.text.lower()
    assert sesi.query(Site).filter_by(url="https://client-a.test").count() == 1


def test_buat_site_http_menampilkan_galat(klien):
    r = klien.post("/sites", data={"nama": "X", "url": "http://tidak-aman.test"})
    assert r.status_code == 200
    assert "https" in r.text.lower()


def test_detail_site_terbuka(klien, sesi):
    from wpmgr.pairing import buat_site

    site, _ = buat_site(sesi, "Contoh", "https://contoh.test", None, None)
    r = klien.get(f"/sites/{site.id}")
    assert r.status_code == 200
    assert "Contoh" in r.text


def test_detail_site_tidak_ada_404(klien):
    assert klien.get(f"/sites/{uuid.uuid4()}").status_code == 404
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `pytest tests/integration/test_pages.py -v -m integration`
Expected: FAIL — seluruh route membalas 404

- [ ] **Step 3: Implementasi `routes_pages.py`**

```python
# src/wpmgr/web/routes_pages.py
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select

from wpmgr import db
from wpmgr.models import ActivityLog, Site, SitePackage, User
from wpmgr.pairing import buat_site
from wpmgr.web.auth import pengguna_saat_ini

router = APIRouter()


def _tpl():
    from wpmgr.web.app import templates

    return templates


@router.get("/")
def halaman_updates(request: Request, pengguna: User = Depends(pengguna_saat_ini)):
    return _tpl().TemplateResponse(request, "updates.html", {"pengguna": pengguna})


@router.get("/sites")
def halaman_sites(request: Request, pengguna: User = Depends(pengguna_saat_ini)):
    return _tpl().TemplateResponse(request, "sites.html", {"pengguna": pengguna})


@router.get("/activity")
def halaman_activity(request: Request, pengguna: User = Depends(pengguna_saat_ini)):
    with db.SessionLocal() as sesi:
        baris = sesi.execute(
            select(ActivityLog, Site.nama)
            .outerjoin(Site, Site.id == ActivityLog.site_id)
            .order_by(ActivityLog.dibuat_pada.desc())
            .limit(300)
        ).all()
    return _tpl().TemplateResponse(
        request, "activity.html", {"pengguna": pengguna, "baris": baris}
    )


@router.get("/sites/new")
def form_site_baru(request: Request, pengguna: User = Depends(pengguna_saat_ini)):
    return _tpl().TemplateResponse(
        request, "site_new.html", {"pengguna": pengguna, "kunci": None, "galat": None}
    )


@router.post("/sites")
def simpan_site(
    request: Request,
    nama: str = Form(...),
    url: str = Form(...),
    pengguna: User = Depends(pengguna_saat_ini),
):
    try:
        with db.SessionLocal() as sesi:
            site, kunci = buat_site(sesi, nama, url, None, pengguna.id)
            konteks = {"pengguna": pengguna, "kunci": kunci, "galat": None, "site": site}
    except ValueError as exc:
        konteks = {"pengguna": pengguna, "kunci": None, "galat": str(exc)}
    return _tpl().TemplateResponse(request, "site_new.html", konteks)


@router.get("/sites/{site_id}")
def halaman_detail(
    request: Request, site_id: uuid.UUID, pengguna: User = Depends(pengguna_saat_ini)
):
    with db.SessionLocal() as sesi:
        site = sesi.get(Site, site_id)
        if site is None:
            raise HTTPException(status_code=404, detail="Site tidak ditemukan")
        paket = sesi.scalars(
            select(SitePackage).where(SitePackage.site_id == site_id).order_by(SitePackage.nama)
        ).all()
        riwayat = sesi.scalars(
            select(ActivityLog)
            .where(ActivityLog.site_id == site_id)
            .order_by(ActivityLog.dibuat_pada.desc())
            .limit(100)
        ).all()
    return _tpl().TemplateResponse(
        request, "site_detail.html",
        {"pengguna": pengguna, "site": site, "paket": paket, "riwayat": riwayat},
    )
```

- [ ] **Step 4: Tulis `updates.html`**

```html
{% extends "base.html" %}
{% block judul %}Semua Update — WP Manager{% endblock %}
{% block isi %}
<div x-data="layarUpdate()" x-init="muat()">
  <h1>Semua Update</h1>

  <div class="toolbar">
    <button @click="jalankanUpdate()" :disabled="terpilih.length === 0 || berjalan">
      <span x-text="berjalan ? 'Sedang berjalan…' : `Update ${terpilih.length} item terpilih`"></span>
    </button>
    <button @click="muat()" :disabled="berjalan">Muat ulang</button>
  </div>

  <div x-show="progres.length" class="progres">
    <template x-for="j in progres" :key="j.id">
      <div>
        <span x-text="j.site_nama"></span> —
        <span x-text="j.slug || j.tipe"></span> —
        <span x-text="j.status"></span>
      </div>
    </template>
  </div>

  <div id="grid" style="height: 70vh"></div>
</div>
<script src="/static/app/updates.js"></script>
{% endblock %}
```

- [ ] **Step 5: Tulis `updates.js`**

```javascript
// src/wpmgr/static/app/updates.js
function layarUpdate() {
  return {
    grid: null,
    terpilih: [],
    berjalan: false,
    progres: [],
    _timer: null,

    async muat() {
      const data = await (await fetch('/api/packages')).json();
      if (this.grid) {
        this.grid.option('dataSource', data);
        this.grid.refresh();
        return;
      }
      this.grid = new DataGrid('#grid', {
        dataSource: data,
        keyExpr: 'id',
        selection: 'multiple',
        columns: [
          { dataField: 'client_nama', caption: 'Client' },
          { dataField: 'site_nama', caption: 'Site' },
          { dataField: 'tipe', caption: 'Tipe' },
          { dataField: 'nama', caption: 'Nama' },
          { dataField: 'versi_terpasang', caption: 'Terpasang' },
          {
            dataField: 'versi_tersedia',
            caption: 'Tersedia',
            cellTemplate: (nilai) => `<span class="dg-badge-warning">${nilai}</span>`,
          },
          { dataField: 'last_scan_at', caption: 'Terakhir Scan', dataType: 'date' },
        ],
        summary: { totalItems: [{ column: 'nama', type: 'count' }] },
        onSelectionChanged: (e) => { this.terpilih = e.rows; },
      });
    },

    async jalankanUpdate() {
      if (!this.terpilih.length) return;
      this.berjalan = true;
      const items = this.terpilih.map((b) => ({
        site_id: b.site_id, tipe: b.tipe, slug: b.slug, ke_versi: b.versi_tersedia,
      }));
      await fetch('/api/jobs/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items }),
      });
      this.pantau();
    },

    pantau() {
      clearInterval(this._timer);
      this._timer = setInterval(async () => {
        this.progres = await (await fetch('/api/jobs/active')).json();
        if (this.progres.length === 0) {
          clearInterval(this._timer);
          this.berjalan = false;
          this.terpilih = [];
          this.muat();
        }
      }, 2000);
    },
  };
}
```

Bila `grid.option('dataSource', ...)` tidak tersedia pada DataGrid custom, periksa `docs.html` di `D:\Workspace\datagridcustom` untuk nama method penyegaran data yang benar, dan sesuaikan. Jangan membuat instance `DataGrid` baru setiap kali memuat ulang — itu akan menumpuk listener pada elemen yang sama.

- [ ] **Step 6: Tulis `sites.html` dan `sites.js`**

```html
{% extends "base.html" %}
{% block judul %}Site — WP Manager{% endblock %}
{% block isi %}
<div x-data="layarSite()" x-init="muat()">
  <h1>Site</h1>
  <div id="grid" style="height: 70vh"></div>
</div>
<script src="/static/app/sites.js"></script>
{% endblock %}
```

```javascript
// src/wpmgr/static/app/sites.js
const WARNA_STATUS = {
  active: 'dg-badge-success',
  pending_pair: 'dg-badge-info',
  needs_reconnect: 'dg-badge-warning',
  blocked: 'dg-badge-danger',
  unreachable: 'dg-badge-danger',
  disabled: 'dg-badge-muted',
};

function layarSite() {
  return {
    grid: null,

    async muat() {
      const data = await (await fetch('/api/sites')).json();
      if (this.grid) {
        this.grid.option('dataSource', data);
        this.grid.refresh();
        return;
      }
      this.grid = new DataGrid('#grid', {
        dataSource: data,
        keyExpr: 'id',
        columns: [
          { dataField: 'nama', caption: 'Site' },
          { dataField: 'client_nama', caption: 'Client' },
          {
            dataField: 'status',
            caption: 'Status',
            cellTemplate: (v) => `<span class="${WARNA_STATUS[v] || ''}">${v}</span>`,
          },
          { dataField: 'wp_version', caption: 'WP' },
          { dataField: 'php_version', caption: 'PHP' },
          { dataField: 'jumlah_update', caption: 'Update' },
          { dataField: 'last_seen_at', caption: 'Terakhir Terlihat', dataType: 'date' },
          {
            caption: 'Aksi',
            calculateCellValue: (baris) => baris.id,
            cellTemplate: (id) =>
              `<button data-sso="${id}">Masuk</button> ` +
              `<button data-scan="${id}">Scan</button> ` +
              `<a href="/sites/${id}">Detail</a>`,
          },
        ],
      });

      document.getElementById('grid').addEventListener('click', async (ev) => {
        const sso = ev.target.getAttribute('data-sso');
        const scan = ev.target.getAttribute('data-scan');
        if (sso) {
          const r = await (await fetch(`/api/sso/${sso}`)).json();
          window.open(r.url, '_blank', 'noopener');
        } else if (scan) {
          await fetch('/api/jobs/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ site_id: scan }),
          });
          ev.target.textContent = 'Antre…';
        }
      });
    },
  };
}
```

Listener dipasang pada wadah grid, bukan pada tiap tombol. DataGrid menggambar ulang isinya saat disortir, difilter, atau dipaginasi, sehingga listener yang menempel pada tombol akan hilang; delegasi event pada wadah tetap hidup melewati setiap penggambaran ulang.

- [ ] **Step 7: Tulis `site_new.html`, `site_detail.html`, dan `activity.html`**

```html
<!-- site_new.html -->
{% extends "base.html" %}
{% block judul %}Tambah Site — WP Manager{% endblock %}
{% block isi %}
<h1>Tambah Site</h1>
{% if galat %}<p class="galat">{{ galat }}</p>{% endif %}

{% if kunci %}
  <h2>Kunci koneksi</h2>
  <p>Pasang plugin <code>wp-manager-connector</code> di site tersebut, buka
     <strong>Pengaturan → WP Manager</strong>, lalu tempel kunci ini:</p>
  <textarea rows="4" cols="90" readonly>{{ kunci }}</textarea>
  <p>Kunci ini hanya ditampilkan sekali. Setelah connector mengonfirmasi,
     status site akan berubah di halaman <a href="/sites">Site</a>.</p>
{% else %}
  <form method="post" action="/sites">
    <p><label>Nama<br><input name="nama" required></label></p>
    <p><label>URL (wajib https)<br>
       <input name="url" required placeholder="https://client.example.com"></label></p>
    <p><button type="submit">Buat dan tampilkan kunci</button></p>
  </form>
{% endif %}
{% endblock %}
```

```html
<!-- site_detail.html -->
{% extends "base.html" %}
{% block judul %}{{ site.nama }} — WP Manager{% endblock %}
{% block isi %}
<h1>{{ site.nama }}</h1>
<p><a href="{{ site.url }}" target="_blank" rel="noopener">{{ site.url }}</a></p>
<p>Status: <strong>{{ site.status.value }}</strong> ·
   WP {{ site.wp_version or '—' }} · PHP {{ site.php_version or '—' }} ·
   Connector {{ site.connector_version or '—' }}</p>
{% if site.last_error %}<p class="galat">{{ site.last_error }}</p>{% endif %}

<h2>Inventaris ({{ paket|length }})</h2>
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

<h2>Riwayat</h2>
<ul>
  {% for r in riwayat %}
  <li>{{ r.dibuat_pada.strftime('%Y-%m-%d %H:%M') }} — [{{ r.level }}] {{ r.pesan }}</li>
  {% endfor %}
</ul>

<h2>Cabut site</h2>
<div x-data="{ konfirmasi: false }">
  <p>Mencabut site menghapus datanya dari dashboard berikut secret-nya, sehingga
     dashboard ini tidak lagi dapat memerintah site tersebut. Plugin
     <code>wp-manager-connector</code> dan user <code>wpmgr</code> di site itu
     harus dihapus manual lewat wp-admin.</p>
  <button x-show="!konfirmasi" @click="konfirmasi = true">Cabut site ini</button>
  <div x-show="konfirmasi">
    <p><strong>Yakin mencabut {{ site.nama }}?</strong></p>
    <button @click="
      fetch('/api/sites/{{ site.id }}', { method: 'DELETE' })
        .then(() => window.location = '/sites')">Ya, cabut</button>
    <button @click="konfirmasi = false">Batal</button>
  </div>
</div>
{% endblock %}
```

Konfirmasi memakai dua langkah Alpine, bukan `confirm()` bawaan browser. Selain karena dialog modal memblokir seluruh halaman, pola dua langkah memberi ruang untuk menjelaskan apa yang tidak ikut terhapus — plugin dan user di sisi site — yang justru bagian paling mudah dilupakan saat mencabut client.

```html
<!-- activity.html -->
{% extends "base.html" %}
{% block judul %}Aktivitas — WP Manager{% endblock %}
{% block isi %}
<h1>Aktivitas</h1>
<table>
  <tr><th>Waktu</th><th>Site</th><th>Level</th><th>Pesan</th></tr>
  {% for log, site_nama in baris %}
  <tr>
    <td>{{ log.dibuat_pada.strftime('%Y-%m-%d %H:%M') }}</td>
    <td>{{ site_nama or '—' }}</td>
    <td>{{ log.level }}</td>
    <td>{{ log.pesan }}</td>
  </tr>
  {% endfor %}
</table>
{% endblock %}
```

- [ ] **Step 8: Jalankan test, pastikan lolos**

Run: `pytest tests/integration/test_pages.py -v -m integration`
Expected: PASS, 8 test

- [ ] **Step 9: Jalankan seluruh test**

Run: `pytest -v`
Expected: seluruh test unit dan integrasi hijau

- [ ] **Step 10: Commit**

```bash
git add src/wpmgr/web/routes_pages.py src/wpmgr/templates src/wpmgr/static/app tests/integration/test_pages.py
git commit -m "feat: halaman dashboard dengan DataGrid dan polling progres

Aksi per baris memakai delegasi event pada wadah grid, karena DataGrid
menggambar ulang isinya setiap kali disortir atau difilter.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Fase 6 — End-to-End

### Task 22: WordPress asli di Docker dan uji alur penuh

**Files:**
- Modify: `docker-compose.yml`
- Create: `tests/e2e/conftest.py`, `tests/e2e/test_alur_penuh.py`

**Interfaces:**
- Consumes: seluruh komponen Task 1–21
- Produces: fixture `wp_site` (URL WordPress siap pakai), `pasang_connector()`, `pair_site()`

Ini satu-satunya test yang menjawab "apakah sistem ini bekerja". Seluruh nilai Lapis 1 bergantung pada `Plugin_Upgrader` yang berperilaku benar terhadap filesystem nyata, dan perilaku itu tidak dapat di-mock dengan jujur.

- [ ] **Step 1: Tambahkan WordPress ke `docker-compose.yml`**

```yaml
  wpdb:
    image: mariadb:11
    environment:
      MARIADB_ROOT_PASSWORD: root
      MARIADB_DATABASE: wp
      MARIADB_USER: wp
      MARIADB_PASSWORD: wp
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 5s
      retries: 20

  wp:
    image: wordpress:6.5-php8.1-apache
    depends_on:
      wpdb: { condition: service_healthy }
    environment:
      WORDPRESS_DB_HOST: wpdb
      WORDPRESS_DB_USER: wp
      WORDPRESS_DB_PASSWORD: wp
      WORDPRESS_DB_NAME: wp
    ports: ["8081:80"]
    volumes:
      - ./connector/wp-manager-connector:/var/www/html/wp-content/plugins/wp-manager-connector

  wpcli:
    image: wordpress:cli-php8.1
    depends_on: [wp]
    user: "33:33"
    entrypoint: ["sh", "-c", "sleep infinity"]
    environment:
      WORDPRESS_DB_HOST: wpdb
      WORDPRESS_DB_USER: wp
      WORDPRESS_DB_PASSWORD: wp
      WORDPRESS_DB_NAME: wp
    volumes_from: [wp]
```

Kontainer `wpcli` hanya dipakai test untuk menyiapkan keadaan awal (memasang plugin versi lama, mengaktifkan connector). Dashboard sendiri tidak pernah memakai WP-CLI — batasan "tidak ada asumsi WP-CLI" tetap berlaku untuk kode produksi.

- [ ] **Step 2: Tulis fixture e2e**

```python
# tests/e2e/conftest.py
import subprocess
import time
import uuid

import httpx
import pytest

WP_URL = "http://localhost:8081"
pytestmark = pytest.mark.e2e


def wpcli(*args: str) -> str:
    hasil = subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "wp", "--path=/var/www/html",
         "--allow-root", *args],
        capture_output=True, text=True, check=False,
    )
    if hasil.returncode != 0:
        raise RuntimeError(f"wp {' '.join(args)} gagal: {hasil.stderr}")
    return hasil.stdout.strip()


@pytest.fixture(scope="session")
def wp_site():
    batas = time.time() + 180
    while time.time() < batas:
        try:
            if httpx.get(WP_URL, timeout=5).status_code < 500:
                break
        except httpx.HTTPError:
            pass
        time.sleep(3)
    else:
        pytest.fail("WordPress tidak siap dalam 180 detik")

    wpcli("core", "install", f"--url={WP_URL}", "--title=Uji",
          "--admin_user=admin", "--admin_password=admin-uji-123",
          "--admin_email=admin@uji.local", "--skip-email")
    wpcli("plugin", "activate", "wp-manager-connector")
    return WP_URL
```

- [ ] **Step 3: Tulis test alur penuh**

```python
# tests/e2e/test_alur_penuh.py
import uuid

import pytest

from wpmgr.crypto import dekripsi_secret
from wpmgr.jobs.handlers import buat_klien
from wpmgr.jobs.queue import ambil_job, buat_job
from wpmgr.models import JobStatus, JobType, PackageType, SitePackage, SiteStatus
from wpmgr.pairing import buat_site, kunci_koneksi
from wpmgr.site_client import SiteClient
from wpmgr.worker import proses_satu

from .conftest import wpcli

pytestmark = pytest.mark.e2e


def _klien_http(site, sesi):
    """SiteClient untuk WordPress lokal yang memakai http, bukan https."""
    secret = dekripsi_secret(site.secret_terenkripsi)
    klien = SiteClient("https://placeholder.test", str(site.id), secret)
    klien.base_url = site.url  # lewati pemeriksaan https khusus lingkungan test
    return klien


@pytest.fixture
def site_terpasang(sesi, wp_site):
    site, kunci = buat_site(sesi, "Uji E2E", "https://uji.test", None, None)
    site.url = wp_site  # http://localhost:8081
    sesi.commit()

    kunci_http = kunci_koneksi(str(site.id), dekripsi_secret(site.secret_terenkripsi),
                               "http://host.docker.internal:8000")
    wpcli("option", "update", "wpmgr_site_id", str(site.id))
    wpcli("option", "update", "wpmgr_secret", dekripsi_secret(site.secret_terenkripsi))
    wpcli("option", "update", "wpmgr_dashboard_url", "http://host.docker.internal:8000")
    return site


def test_ping_menjawab_dengan_versi(sesi, site_terpasang):
    data = _klien_http(site_terpasang, sesi).ping()
    assert data["connector_version"] == "1.0.0"
    assert data["wp_version"].startswith("6.")


def test_tanda_tangan_salah_ditolak_401(sesi, site_terpasang):
    from wpmgr.errors import AUTH_ERROR, SiteError

    klien = SiteClient("https://placeholder.test", str(site_terpasang.id), "f" * 64)
    klien.base_url = site_terpasang.url
    with pytest.raises(SiteError) as exc:
        klien.ping()
    assert exc.value.error_class == AUTH_ERROR


def test_nonce_yang_diulang_ditolak(sesi, site_terpasang):
    import time

    from wpmgr.errors import AUTH_ERROR, SiteError
    from wpmgr.signing import new_nonce, sign

    secret = dekripsi_secret(site_terpasang.secret_terenkripsi)
    path = "/wp-json/wpmgr/v1/ping"
    ts, nonce = int(time.time()), new_nonce()
    headers = {
        "X-Wpmgr-Site": str(site_terpasang.id),
        "X-Wpmgr-Timestamp": str(ts),
        "X-Wpmgr-Nonce": nonce,
        "X-Wpmgr-Signature": sign(secret, "GET", path, ts, nonce, b""),
    }
    import httpx

    url = f"{site_terpasang.url}{path}"
    assert httpx.get(url, headers=headers, timeout=20).status_code == 200
    assert httpx.get(url, headers=headers, timeout=20).status_code == 401


def test_scan_mengisi_inventaris(sesi, site_terpasang):
    buat_job(sesi, site_terpasang.id, JobType.scan_site)
    assert proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi)) is True

    paket = sesi.query(SitePackage).filter_by(site_id=site_terpasang.id).all()
    assert any(p.tipe == PackageType.core for p in paket)
    assert any(p.slug == "wp-manager-connector/wp-manager-connector.php" for p in paket)


def test_update_plugin_versi_lama_benar_benar_naik(sesi, site_terpasang):
    wpcli("plugin", "install", "hello-dolly", "--version=1.6", "--force")

    buat_job(sesi, site_terpasang.id, JobType.scan_site)
    proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi))

    baris = sesi.query(SitePackage).filter_by(
        site_id=site_terpasang.id, slug="hello-dolly/hello.php").one()
    assert baris.versi_terpasang == "1.6"
    assert baris.versi_tersedia is not None
    target = baris.versi_tersedia

    buat_job(sesi, site_terpasang.id, JobType.update_package, {
        "tipe": "plugin", "slug": "hello-dolly/hello.php",
        "dari_versi": "1.6", "ke_versi": target,
    })
    assert proses_satu(sesi, "e2e", lambda s: _klien_http(s, sesi)) is True

    terpasang = wpcli("plugin", "get", "hello-dolly", "--field=version")
    assert terpasang == target

    sesi.refresh(baris)
    assert baris.versi_terpasang == target


def test_update_kedua_kalinya_adalah_no_op(sesi, site_terpasang):
    versi = wpcli("plugin", "get", "hello-dolly", "--field=version")
    klien = _klien_http(site_terpasang, sesi)
    hasil = klien.update("plugin", "hello-dolly/hello.php", versi)
    assert hasil["ok"] is True
    assert "sudah di versi tersebut" in hasil["pesan"]


def test_sso_mendarat_di_wp_admin_dalam_keadaan_masuk(sesi, site_terpasang):
    import httpx

    from wpmgr.sso import buat_token

    secret = dekripsi_secret(site_terpasang.secret_terenkripsi)
    token = buat_token(secret, str(site_terpasang.id))

    with httpx.Client(follow_redirects=True, timeout=30) as c:
        r = c.get(f"{site_terpasang.url}/?wpmgr_sso={token}")
        assert r.status_code == 200
        assert "/wp-admin" in str(r.url)
        assert any(n.startswith("wordpress_logged_in") for n in c.cookies.keys())


def test_token_sso_tidak_dapat_dipakai_dua_kali(sesi, site_terpasang):
    import httpx

    from wpmgr.sso import buat_token

    secret = dekripsi_secret(site_terpasang.secret_terenkripsi)
    token = buat_token(secret, str(site_terpasang.id))
    url = f"{site_terpasang.url}/?wpmgr_sso={token}"

    with httpx.Client(follow_redirects=True, timeout=30) as c1:
        assert c1.get(url).status_code == 200
    with httpx.Client(follow_redirects=True, timeout=30) as c2:
        assert c2.get(url).status_code == 403
```

`test_update_plugin_versi_lama_benar_benar_naik` memverifikasi versi lewat WP-CLI, bukan lewat balasan API kita sendiri. Memeriksa keadaan dengan alat yang sepenuhnya di luar sistem yang diuji adalah satu-satunya cara memastikan test ini tidak sekadar memercayai laporan komponen yang sedang diuji.

- [ ] **Step 4: Jalankan e2e**

Run:
```bash
docker compose up -d
pytest tests/e2e -v -m e2e
```
Expected: PASS, 8 test. Jalannya beberapa menit; unduhan plugin dari wordpress.org membuatnya lambat.

- [ ] **Step 5: Verifikasi `$path` pada guard (utang dari Task 15 Step 4)**

`test_ping_menjawab_dengan_versi` hanya lolos bila konstruksi `$path` di `WPMGR_REST::guard()` menghasilkan nilai identik dengan yang ditandatangani dashboard. Bila test itu gagal dengan 401, tambahkan `error_log()` sementara di `guard()`, jalankan ulang satu test itu, baca `docker compose logs wp`, dan perbaiki konstruksinya. Hapus `error_log()` setelah hijau.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml tests/e2e
git commit -m "test: alur end-to-end terhadap WordPress asli di Docker

Versi setelah update diverifikasi lewat WP-CLI, bukan lewat balasan API
sendiri, agar test tidak memercayai laporan komponen yang sedang diuji.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 23: Berkas deployment dan README

**Files:**
- Create: `deploy/wpmgr-web.service`, `deploy/wpmgr-worker.service`, `deploy/crontab`, `deploy/nginx.conf`, `README.md`

**Interfaces:**
- Consumes: seluruh sistem
- Produces: berkas konfigurasi siap salin ke VPS

- [ ] **Step 1: Tulis unit systemd**

```ini
# deploy/wpmgr-web.service
[Unit]
Description=WP Manager web
After=network.target postgresql.service

[Service]
User=wpmgr
WorkingDirectory=/opt/wpmgr
EnvironmentFile=/opt/wpmgr/.env
ExecStart=/opt/wpmgr/.venv/bin/uvicorn wpmgr.web.app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```ini
# deploy/wpmgr-worker.service
[Unit]
Description=WP Manager worker
After=network.target postgresql.service

[Service]
User=wpmgr
WorkingDirectory=/opt/wpmgr
EnvironmentFile=/opt/wpmgr/.env
ExecStart=/opt/wpmgr/.venv/bin/python -m wpmgr.worker
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=240

[Install]
WantedBy=multi-user.target
```

`TimeoutStopSec=240` lebih panjang daripada timeout update (180 detik) dengan sengaja: saat `systemctl restart` dikirim di tengah update plugin, worker harus punya waktu menyelesaikannya sebelum systemd mengirim `SIGKILL`.

- [ ] **Step 2: Tulis crontab dan nginx**

```cron
# deploy/crontab — pasang dengan: crontab -u wpmgr deploy/crontab
0   *  * * *  cd /opt/wpmgr && .venv/bin/python -m wpmgr.cli enqueue-scans >> /var/log/wpmgr/cron.log 2>&1
*/5 *  * * *  cd /opt/wpmgr && .venv/bin/python -m wpmgr.cli reap-jobs     >> /var/log/wpmgr/cron.log 2>&1
```

```nginx
# deploy/nginx.conf
server {
    listen 443 ssl http2;
    server_name wpmgr.example.com;

    ssl_certificate     /etc/letsencrypt/live/wpmgr.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/wpmgr.example.com/privkey.pem;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name wpmgr.example.com;
    return 301 https://$host$request_uri;
}
```

- [ ] **Step 3: Tulis `README.md`**

README memuat: ringkasan satu paragraf, prasyarat (Python 3.11+, PostgreSQL 16, Docker untuk test), langkah setup lokal (`pip install -e ".[dev]"`, `docker compose up -d db`, `cp .env.example .env`, membangkitkan `WPMGR_SECRET_KEY` dengan `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`, `alembic upgrade head`, `python -m wpmgr.cli create-user`), cara menjalankan web dan worker, cara menjalankan test per lapis (`pytest -m "not integration and not e2e"`, `-m integration`, `-m e2e`), dan langkah deploy ke VPS yang merujuk berkas di `deploy/`. Sertakan juga catatan bahwa plugin connector dibungkus dengan `cd connector && zip -r ../wp-manager-connector.zip wp-manager-connector`.

- [ ] **Step 4: Verifikasi seluruh test masih hijau**

Run: `pytest -v`
Expected: seluruh test unit dan integrasi PASS

Run: `cd connector && vendor/bin/phpunit`
Expected: PASS, 11 test

- [ ] **Step 5: Commit**

```bash
git add deploy README.md
git commit -m "chore: unit systemd, crontab, konfigurasi nginx, dan README

TimeoutStopSec worker melebihi timeout update agar restart di tengah update
tidak dipotong SIGKILL.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Ringkasan Task

| # | Task | Fase | Deliverable teruji |
|---|---|---|---|
| 1 | Scaffold, config, PostgreSQL dev | 0 | `pytest` jalan, settings terbaca |
| 2 | Canonical string + HMAC + fixture | 1 | 13 test signing hijau |
| 3 | Enkripsi secret | 1 | Bolak-balik Fernet |
| 4 | Token SSO | 1 | Kedaluwarsa & pemalsuan ditolak |
| 5 | Klasifikasi error | 1 | 403 plugin vs 403 firewall dibedakan |
| 6 | Model + migrasi | 2 | Enam tabel, constraint teruji |
| 7 | Pengambilan job | 3 | Dua worker tidak bertabrakan |
| 8 | Penyelesaian, backoff, reaper | 3 | Job yatim dipulihkan |
| 9 | Klien HTTP ke site | 3 | Semua kelas error terpetakan |
| 10 | Handler scan & verify | 3 | Inventaris tersinkron |
| 11 | Handler update & resolusi unknown | 3 | Timeout diverifikasi, bukan ditebak |
| 12 | Worker loop + CLI | 3 | Shutdown rapi, status site diperbarui |
| 13 | Bootstrap plugin + HMAC PHP | 4 | PHP cocok dengan fixture Python |
| 14 | Setting, pairing, user `wpmgr` | 4 | Sintaks bersih |
| 15 | Route REST + ping + inventory | 4 | Guard menolak replay |
| 16 | Endpoint update idempoten | 4 | Sintaks bersih |
| 17 | Handler SSO | 4 | 11 test PHP hijau |
| 18 | App factory, sesi, login | 5 | Route terlindungi |
| 19 | Pendaftaran site + pair/confirm | 5 | HMAC masuk terverifikasi |
| 20 | JSON API | 5 | Enam route teruji |
| 21 | Halaman + DataGrid | 5 | Semua layar terbuka |
| 22 | End-to-end | 6 | Update nyata terverifikasi WP-CLI |
| 23 | Deployment + README | 6 | Berkas siap salin |

---

## Catatan Eksekusi

**Urutan tidak boleh ditukar pada tiga titik:**

1. Task 2 mendahului Task 13. Fixture HMAC harus ada sebelum test PHP dapat membacanya.
2. Task 18 mendahului Task 19–21. Ketiganya mengisi stub router yang dibuat Task 18.
3. Task 15 Step 4 menyisakan utang yang baru dapat dilunasi pada Task 22 Step 5, karena konstruksi `$path` hanya dapat diverifikasi terhadap WordPress nyata.

**Dua task menghasilkan kode yang belum dapat dijalankan saat selesai:** Task 13–16 menghasilkan plugin yang belum dapat diaktifkan sampai Task 17 melengkapi berkas terakhir yang di-`require`. Ini disengaja; mengaktifkan plugin setengah jadi di site nyata tidak pernah berguna.

**Bila sebuah task gagal di tengah,** jangan lanjut ke task berikutnya. Setiap task berakhir pada commit, sehingga `git stash` atau `git checkout -- .` selalu mengembalikan ke keadaan hijau terakhir.
