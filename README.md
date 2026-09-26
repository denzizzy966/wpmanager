# WP Manager

Dashboard terpusat untuk memelihara puluhan site WordPress milik client dari satu
tempat: melihat inventaris plugin/tema/core di seluruh site, menjalankan update
(termasuk update massal lintas-site) lewat plugin connector yang terpasang di
tiap site, dan masuk ke wp-admin site mana pun dengan satu klik lewat SSO
bertanda tangan — tanpa dashboard pernah menyimpan atau mengetik password
wp-admin site tersebut. Dashboard juga memantau uptime, SSL, error PHP,
riwayat login, dan traffic setiap site. Backend Python/FastAPI menjalankan web
dan worker antrean job terpisah; plugin PHP `wp-manager-connector` di sisi
site mengekspos endpoint REST yang diverifikasi dengan HMAC.

## Prasyarat

- **Python 3.10+** (proyek ini dikembangkan dan diuji dengan 3.10.6; lihat `requires-python` di `pyproject.toml`)
- **PostgreSQL 16** — versi yang dipakai `docker-compose.yml` untuk dev lokal; produksi boleh memakai instance PostgreSQL 16 mana pun
- **Docker + Docker Compose** — untuk menjalankan PostgreSQL dan (untuk test e2e) WordPress + MariaDB di lokal. Tidak dibutuhkan di server produksi dashboard itu sendiri.
- **PHP 7.4+ dan Composer** — untuk menjalankan plugin connector dan test
  PHPUnit-nya (`connector/`). Angka ini berasal dari `Requires PHP: 7.4` di
  header `connector/wp-manager-connector/wp-manager-connector.php`, bukan
  dari perkiraan. Kontainer WordPress yang dipakai test e2e (lihat
  `docker-compose.yml`) kebetulan memakai PHP 8.1, tapi itu properti image
  Docker tersebut, bukan syarat minimum plugin ini.
- **Site WordPress klien harus memakai permalink "cantik"** (bukan "Plain"). Lihat catatan di bawah — ini bukan sekadar preferensi kosmetik.

## Setup lokal

**Konvensi path virtualenv dalam dokumen ini:** semua contoh perintah di
bagian "Setup lokal", "Menjalankan web dan worker", dan "Menjalankan test"
di bawah ditulis untuk Windows, tempat `venv` menaruh binernya di
`.venv/Scripts/`. Di Linux atau macOS, `venv` menaruh biner yang sama di
`.venv/bin/` — ganti setiap `.venv/Scripts/x` di bawah menjadi `.venv/bin/x`.
Bagian **Deploy ke VPS** nanti sudah ditulis untuk Linux dan memakai
`.venv/bin/` secara konsisten; perintah di sana tidak perlu diterjemahkan.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
docker compose up -d db                     # PostgreSQL saja cukup untuk kerja sehari-hari
cp .env.example .env
```

Isi `.env`. Dua nilai di dalamnya harus dibangkitkan, bukan diketik sembarangan:

```bash
.venv/Scripts/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Tempel hasilnya sebagai `WPMGR_SECRET_KEY`. **Kunci ini mengenkripsi setiap
secret site yang tersimpan di database.** Jika hilang atau diganti,
seluruh secret site yang sudah tersimpan menjadi tidak dapat didekripsi
lagi — setiap site harus di-pairing ulang dari nol. Cadangkan kunci ini
bersama backup database, dan jangan pernah merotasinya begitu saja tanpa
rencana migrasi ulang seluruh site.

`WPMGR_SESSION_SECRET` cukup string acak yang panjang (dipakai
`itsdangerous`/Starlette untuk menandatangani cookie sesi login); tidak perlu
format khusus seperti Fernet, misalnya:

```bash
.venv/Scripts/python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Lalu siapkan skema database dan akun pengguna dashboard pertama:

```bash
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m wpmgr.cli create-user --email admin@example.com --nama "Admin" --password "ganti-ini"
```

Catatan: `--password` di atas singgah sebentar di riwayat shell dan daftar
proses lokal mesin Anda selama perintah berjalan. Untuk pembuatan user di
server produksi, jalankan dari sesi yang tidak disimpan riwayatnya.
Lapis 1 **tidak punya** fitur ganti password — tidak di dashboard, tidak
sebagai perintah CLI, dan `create-user` menolak email yang sudah terdaftar.
Cara merotasinya ada di bagian [Mengganti password akun dashboard](#mengganti-password-akun-dashboard).

## Menjalankan web dan worker

Dua proses terpisah, keduanya baca `.env` yang sama:

```bash
.venv/Scripts/uvicorn wpmgr.web.app:app --reload --port 8000   # web (dev; buang --reload di produksi)
.venv/Scripts/python -m wpmgr.worker                            # worker antrean job
```

Web melayani dashboard dan API. Worker mengambil job (scan inventaris,
verifikasi pairing, update plugin/tema/core) dari antrean di PostgreSQL dan
mengeksekusinya satu per satu terhadap site tujuan lewat `SiteClient`. Kedua
proses harus berjalan bersamaan; tanpa worker, job hanya menumpuk sebagai
`pending` dan tidak pernah dieksekusi.

## Menjalankan test

Proyek ini punya tiga lapis test Python plus satu suite PHP, masing-masing
butuh prasyarat berbeda. **Jangan jalankan `pytest -m "not integration"` saja**
— marker itu hanya menyingkirkan test integrasi, bukan test e2e, sehingga ia
tetap mengumpulkan 245 dari 608 test, termasuk 34 test e2e yang butuh
kontainer WordPress menyala. Di clone segar tanpa Docker jalan, ini gagal
dengan cara yang tidak ada hubungannya dengan perubahan yang sedang diuji.
Gunakan tiga perintah berikut, sesuai apa yang tersedia:

```bash
# Unit — tidak butuh service apa pun (211 test)
.venv/Scripts/python -m pytest -m "not integration and not e2e"

# Integrasi — butuh PostgreSQL (363 test)
docker compose up -d db
.venv/Scripts/python -m pytest tests/integration -m integration

# End-to-end — butuh kontainer WordPress + MariaDB (34 test)
docker compose up -d db wp wpdb wpcli
.venv/Scripts/python -m pytest tests/e2e -m e2e
```

Dan untuk plugin connector PHP (206 test):

```bash
cd connector && php vendor/bin/phpunit
```

(`composer install` sekali di `connector/` dulu jika `vendor/` belum ada.)

## Menambahkan site dan memasang plugin connector

1. Di dashboard, buka **Sites → Tambah Site**, isi nama dan URL (`https://`
   wajib). Dashboard membuatkan secret dan menampilkan satu **kunci koneksi**
   — kunci ini hanya ditampilkan sekali, salin sebelum berpindah halaman.
2. Unduh zip plugin lewat tautan **"Unduh plugin connector"** di halaman
   **Tambah Site** (`/connector/unduh`) — ini yang diunggah ke site klien;
   jangan unggah folder `connector/` mentah-mentah. Zip ini disiapkan lebih
   dulu di server dashboard dengan:

   ```bash
   python -m wpmgr.cli build-connector
   ```

   `connector/tests/` dan `connector/vendor/` (dependency development —
   PHPUnit dkk — yang dipasang lewat Composer hanya untuk menjalankan test)
   **tidak ikut** di dalam zip; perintah build di atas sudah mengecualikan
   keduanya. Tanpa perintah ini dijalankan sekali dulu, tautan unduhan
   membalas pesan yang menyuruh menjalankannya (lihat juga bagian
   [Pemantauan (Lapis 2)](#pemantauan-lapis-2) soal kapan menjalankannya
   ulang).
3. Di wp-admin site klien: **Plugins → Add New → Upload Plugin**, unggah
   `wp-manager-connector.zip`, aktifkan.
4. Buka halaman setting plugin, tempel kunci koneksi dari langkah 1. Plugin
   memanggil `/api/pair/confirm` di dashboard untuk menyelesaikan pairing.

**Prasyarat yang mudah terlewat:** site tujuan harus memakai permalink
"cantik" (Settings → Permalinks, apa saja selain "Plain"). Dengan permalink
"Plain", WordPress mengalihkan (redirect 301) setiap permintaan
`/wp-json/...` sebelum plugin sempat memprosesnya, sehingga dashboard tidak
pernah bisa bicara dengan site itu. Kebanyakan site nyata sudah memakai
permalink cantik secara default; instalasi WordPress yang benar-benar baru
biasanya belum. Jika ini terjadi, dashboard sekarang melaporkannya dengan
jelas — pesan errornya menyebutkan URL tujuan redirect dan menunjuk ke
pengaturan permalink — tetapi lebih murah mencegahnya di awal daripada
men-debug-nya belakangan.

## Mencabut akses site

Menghapus site dari dashboard (**Sites → hapus**) hanya menghapus salinan
secret di sisi dashboard. Setelah itu dashboard tidak lagi punya cara untuk
memberi perintah ke site tersebut. **Plugin `wp-manager-connector` dan user
administrator `wpmgr` yang dibuatnya tetap ada di site klien** — keduanya
tidak otomatis terhapus dan harus dicabut manual di site itu (nonaktifkan/
hapus plugin, hapus user `wpmgr`) jika akses tersebut memang ingin ditutup
sepenuhnya, bukan hanya diputus dari sisi dashboard.

## Deploy ke VPS

Berkas siap pakai ada di `deploy/`. Bagian ini ditulis untuk Linux — target
deploy sesungguhnya — sehingga setiap path venv di bawah memakai
`.venv/bin/`, bukan `.venv/Scripts/` seperti bagian setup lokal di atas.

**Bootstrap awal.** Ini belum ada cara otomatis; server baru tidak punya
clone, virtualenv, skema database, atau akun dashboard sampai langkah-langkah
berikut dijalankan sekali secara manual:

```bash
# sebagai root
adduser --system --group --home /opt/wpmgr wpmgr
git clone <url-repo-ini> /opt/wpmgr
cd /opt/wpmgr
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
# isi DATABASE_URL dan WPMGR_BASE_URL di .env secara langsung. WPMGR_SECRET_KEY
# dan WPMGR_SESSION_SECRET harus dibangkitkan, bukan diketik sembarangan
# (WPMGR_SECRET_KEY mengenkripsi setiap secret site di database — lihat
# catatan di bagian "Setup lokal" di atas soal apa yang terjadi jika hilang):
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> WPMGR_SECRET_KEY
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"                                # -> WPMGR_SESSION_SECRET

.venv/bin/python -m alembic upgrade head
.venv/bin/python -m wpmgr.cli create-user --email admin@example.com --nama "Admin" --password "ganti-ini"

chown -R wpmgr:wpmgr /opt/wpmgr
```

Kedua perintah terakhir sebelum `chown` — `alembic upgrade head` dan
`create-user` — butuh `DATABASE_URL` dkk. yang baru saja diisi di `.env`.
Keduanya membaca setting lewat `wpmgr.config.Settings`
(`pydantic_settings.BaseSettings` dengan `env_file=".env"`), dan
pydantic-settings mencari berkas itu relatif terhadap **direktori kerja saat
proses dijalankan**, bukan relatif terhadap lokasi modul. Karena kedua
perintah di atas dijalankan dari `/opt/wpmgr` (sesuai `cd /opt/wpmgr` di
awal blok) dan `.env` ada persis di situ, isinya terbaca otomatis — tidak
perlu `export` manual satu per satu. Menjalankan salah satu perintah ini
dari direktori lain membuat `.env` tidak ditemukan, dan Pydantic akan gagal
dengan error "field required" untuk tiap variabel yang tidak terbaca.

Baru setelah skema database ada dan akun pertama bisa login, pasang service,
cron, dan reverse proxy:

```bash
# sebagai root
mkdir -p /var/log/wpmgr && chown wpmgr:wpmgr /var/log/wpmgr

cp deploy/wpmgr-web.service deploy/wpmgr-worker@.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wpmgr-web wpmgr-worker@1 wpmgr-worker@2

crontab -u wpmgr deploy/crontab

cp deploy/nginx.conf /etc/nginx/sites-available/wpmgr.conf
ln -s /etc/nginx/sites-available/wpmgr.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Sesuaikan `server_name` dan path sertifikat di `deploy/nginx.conf`, dan
jalankan `certbot` (atau setara) untuk menerbitkan sertifikat sebelum
`nginx -t` akan lulus dengan path tersebut.

Beberapa detail di berkas-berkas ini tidak kosmetik:

- **`wpmgr-worker@.service` adalah unit template.** Setiap instans
  (`wpmgr-worker@1`, `wpmgr-worker@2`, …) adalah satu proses worker; dua
  instans berarti dua site bisa di-update bersamaan. Aturan maksimum satu
  job berjalan per site ditegakkan oleh query klaim di PostgreSQL, bukan
  oleh jumlah proses, jadi menambah instans aman. Merestart semuanya
  sekaligus: `systemctl restart 'wpmgr-worker@*'`.
- **Unit worker mengatur `TimeoutStopSec=240`**, sengaja lebih
  panjang dari `TIMEOUT_UPDATE` (180 detik) yang dipakai `SiteClient` untuk
  request update. Saat restart dikirim di tengah
  worker sedang mengeksekusi update, worker perlu waktu untuk sampai ke titik
  aman (commit/rollback) sebelum systemd kehabisan sabar dan mengirim
  `SIGKILL`. Memotongnya lebih pendek dari 180 detik berarti restart bisa
  membunuh worker persis di tengah panggilan HTTP ke site, meninggalkan site
  dalam keadaan yang harus ditebak-tebak oleh reaper berikutnya.
- **`WPMGR_BASE_URL` harus persis origin publik dashboard** (skema dan
  host yang diketik di browser, mis. `https://wpmgr.example.com`). Setiap
  POST/PUT/PATCH/DELETE yang membawa header `Origin` (atau `Referer`)
  dengan origin lain ditolak 403 sebagai perlindungan CSRF — termasuk login.
  Nilai yang salah di sini membuat dashboard terbuka tetapi setiap tombolnya
  gagal.
- **IP klien untuk pembatas laju datang dari `X-Forwarded-For`.** Pembatas
  laju di `/api/pair/confirm` dan `POST /login` (masing-masing 10
  percobaan/menit per IP) memakai `request.client.host`. Uvicorn secara
  bawaan (`proxy_headers` aktif; `forwarded_allow_ips` berisi `127.0.0.1`,
  ditambah `::1` di uvicorn 0.53 yang terpasang, kecuali
  `FORWARDED_ALLOW_IPS` disetel) mempercayai `X-Forwarded-For` hanya
  dari peer loopback, lalu mengganti `client.host` dengan entri paling kanan
  yang bukan alamat tepercaya — yaitu alamat yang ditambahkan nginx sendiri
  lewat `$proxy_add_x_forwarded_for`. Entri yang dikarang klien selalu
  berada di kiri entri itu, jadi tidak dapat dipalsukan. Yang menjaga ini:
  `wpmgr-web.service` mengikat uvicorn ke `127.0.0.1` (tidak ada yang bisa
  melewati nginx), dan `nginx.conf` mengirim `X-Forwarded-For`. Jangan
  menyetel `FORWARDED_ALLOW_IPS='*'`: dengan itu uvicorn mengambil entri
  paling **kiri**, yang sepenuhnya dikendalikan klien, dan setiap penyerang
  mendapat kuota baru per permintaan. `X-Real-IP` yang juga dikirim
  `nginx.conf` hanya cadangan: aplikasi membacanya bila `client.host` masih
  loopback, yang hanya terjadi bila uvicorn dijalankan dengan
  `--no-proxy-headers`. Bila nginx tidak mengirim `X-Forwarded-For` maupun
  `X-Real-IP`, setiap permintaan terlihat datang dari
  `127.0.0.1` dan batas per-penyerang berubah jadi kuota global bersama: satu
  penyerang yang sengaja memicu 429 berulang bisa mengunci login dan pairing
  semua orang.
- **`deploy/crontab` menjalankan `enqueue-scans` setiap jam** (membuat job
  scan inventaris untuk setiap site berstatus `active` atau `unreachable`
  yang belum punya job scan tertunda; `unreachable` ikut discan supaya site
  pulih sendiri setelah gangguan sementara, sedangkan `needs_reconnect` dan
  `blocked` menunggu tindakan manusia dan dipulihkan lewat tombol Scan) **dan
  `reap-jobs` setiap 5 menit** (memulihkan job yang worker-nya mati di
  tengah jalan). Keduanya lewat Python di virtualenv yang sama dengan
  service, output ditambahkan ke `/var/log/wpmgr/cron.log`.

### Mengganti password akun dashboard

Lapis 1 tidak punya fitur ini, dan `create-user` menolak email yang sudah
ada (pelanggaran unik di `users.email`). Rotasi dilakukan dengan menulis
hash argon2 baru langsung ke baris user-nya. Dari `/opt/wpmgr` (supaya
`.env` terbaca), sebagai root atau user `wpmgr`:

```bash
.venv/bin/python -c '
import getpass, sys
from argon2 import PasswordHasher
from sqlalchemy import update
from wpmgr.db import get_session
from wpmgr.models import User

hash_baru = PasswordHasher().hash(getpass.getpass("Password baru: "))
with get_session() as sesi:
    n = sesi.execute(
        update(User).where(User.email == sys.argv[1]).values(password_hash=hash_baru)
    ).rowcount
print(f"{n} user diperbarui")
' admin@example.com
```

Password dibaca lewat `getpass`, jadi tidak tercatat di riwayat shell
maupun daftar proses. `0 user diperbarui` berarti email itu tidak
terdaftar. Cara ini mempertahankan `id` user, sehingga jejak di
`activity_log` dan `jobs.dibuat_oleh` tetap menunjuk ke akun yang sama —
berbeda dari menghapus lalu membuat ulang user, yang membuat kolom-kolom
itu menjadi `NULL` (`ON DELETE SET NULL`).

Mengganti password **tidak** mengakhiri sesi login yang sudah ada: cookie
sesi hanya menyimpan id user, dan Lapis 1 tidak punya pencabutan sesi. Bila
password lama mungkin bocor, ganti juga `WPMGR_SESSION_SECRET` di `.env`
lalu `systemctl restart wpmgr-web` — setiap cookie sesi yang ada menjadi
tidak sah dan semua orang harus login ulang.

## Pemantauan (Lapis 2)

Selain inventaris dan update, dashboard memantau kesehatan tiap site dari
beberapa sumber berbeda:

| Data | Datang dari |
|---|---|
| Uptime dan SSL | Cron dashboard sendiri (`check-uptime`, `check-ssl`), tidak butuh apa pun di site |
| Error PHP, riwayat login, dan traffic (sumber "plugin") | Plugin connector 2.x, dikumpulkan lewat job (`collect_events`, `collect_traffic`) |
| Traffic (sumber "ga4") | Google Analytics Data API, lewat `collect-ga4` |

**Setiap deploy** (setelah `git pull`), dua perintah ini wajib dijalankan
sebelum service di-restart:

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m wpmgr.cli build-connector
```

Tanpa `build-connector` dijalankan ulang, tombol **"Perbarui connector"** di
halaman Site dan tautan unduhan di halaman Tambah Site tetap membalas pesan
yang menyuruh menjalankan perintah itu — keduanya membaca paket zip dan
manifest yang dihasilkannya, bukan folder `connector/` mentah.

**Sekali setelah deploy pertama** (database GeoIP belum ada sampai perintah
ini dijalankan; setelahnya `deploy/crontab` menjalankannya ulang otomatis
setiap bulan):

```bash
.venv/bin/python -m wpmgr.cli update-geoip
```

**Memperbarui connector di site klien.** Site yang masih memakai connector
1.x (dari Lapis 1) perlu satu kali upload manual versi 2.x lewat wp-admin
(**Plugins → Add New → Upload Plugin**, ambil zip dari `/connector/unduh`),
sama seperti pemasangan pertama kali. Setelah connector 2.x aktif di site
itu, pembaruan berikutnya cukup lewat aksi massal **"Perbarui connector"** di
halaman **Site**.

**Setup GA4 (opsional).** Tanpa langkah ini, dashboard tetap berjalan penuh,
hanya tanpa panel traffic sumber "ga4":

1. Di Google Cloud Console, buat project (atau pakai yang sudah ada) dan
   sebuah service account, lalu aktifkan **Google Analytics Data API**
   untuknya.
2. Unduh kunci JSON service account tersebut ke
   `/opt/wpmgr/ga4-service-account.json`, lalu batasi aksesnya:
   ```bash
   chown wpmgr:wpmgr /opt/wpmgr/ga4-service-account.json
   chmod 600 /opt/wpmgr/ga4-service-account.json
   ```
3. Isi `WPMGR_GA4_CREDENTIALS=/opt/wpmgr/ga4-service-account.json` di `.env`.
4. Untuk tiap site klien yang ingin dipantau: di properti GA4 milik client,
   tambahkan alamat email service account sebagai **Viewer**
   (**Admin → Property access management**). Lalu, di tab **Ringkasan** pada
   halaman detail site di dashboard, isi **Property ID** — angka polos
   (mis. `123456789`), **bukan** ID pengukuran berformat `G-XXXXXXX`.

**Mematikan pemantauan di satu site.** Tambahkan baris berikut ke
`wp-config.php` site tersebut:

```php
define( 'WPMGR_DISABLE_MONITORING', true );
```

Pembaruan plugin/tema/core dan SSO tetap berjalan seperti biasa; hanya
penangkap error, pencatat login, dan penghitung traffic connector yang mati.

**Setelan proxy per site.** Untuk site yang berjalan di belakang load
balancer atau reverse proxy sendiri (di luar nginx dashboard ini), buka
**Pengaturan → WP Manager** di wp-admin site tersebut untuk mengatur apakah
header `X-Forwarded-For` dari proxy itu boleh dipercaya saat menentukan IP
pengunjung/penyerang.

**Variabel env baru** (lihat `.env.example`), semuanya opsional:

| Variabel | Default | Untuk |
|---|---|---|
| `WPMGR_VAR_DIR` | `var` | Induk direktori data lokal (paket connector, database GeoIP) |
| `WPMGR_GEOIP_PATH` | `<WPMGR_VAR_DIR>/geoip/dbip-country-lite.mmdb` | Lokasi database GeoIP, bila ingin di luar `WPMGR_VAR_DIR` |
| `WPMGR_GA4_CREDENTIALS` | *(kosong = GA4 nonaktif)* | Jalur berkas kunci JSON service account GA4 |

**Atribusi:** data negara berasal dari [DB-IP Lite](https://db-ip.com/db/lite.php)
([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).

## Keterbatasan yang diketahui

Reaper memulihkan job berstatus `running` yang sudah terkunci lebih lama
dari 15 menit (`BATAS_MENIT_DEFAULT` di `wpmgr.jobs.reaper`), dengan asumsi
worker pemegangnya sudah mati. Jika sebuah worker sebenarnya masih hidup
tapi macet (wedged) lebih lama dari 15 menit itu — misalnya tersangkut di
luar timeout HTTP-nya sendiri — reaper bisa mencabut klaimnya sementara ia
masih bekerja. Worker itu sendiri tidak akan menulis hasilnya setelahnya
(ia memeriksa lebih dulu apakah klaimnya masih miliknya sebelum menulis),
tetapi perubahan yang sudah lebih dulu di-commit oleh handler ke tabel site
atau package sebelum titik itu sudah terlanjur tersimpan. Scan hourly
berikutnya (`enqueue-scans` lewat cron) memperbaiki drift semacam ini pada
putaran berikutnya. Ini didokumentasikan, bukan diperbaiki, karena
menutupnya dengan benar berarti memindahkan batas transaksi keluar dari
handler individual, dan jendela masalahnya sendiri butuh worker yang macet
di luar timeout HTTP-nya sendiri untuk terjadi — sesuatu yang jarang terjadi
dalam operasi normal.

Keterbatasan pemantauan (Lapis 2):

- **Tanpa notifikasi.** Uptime turun, SSL kedaluwarsa, error baru, atau
  serangan login hanya terlihat kalau seseorang membuka halaman Kesehatan;
  tidak ada email, Slack, atau saluran lain yang mengabari secara aktif.
- Site dengan drop-in `wp-content/php-error.php` **tidak** tertangkap fatal
  error-nya: drop-in itu memanggil `die()` sebelum fungsi shutdown milik
  connector sempat berjalan (lihat koreksi #1 di
  `docs/superpowers/plans/2026-09-22-wp-manager-lapis2.md`).
- Penangkap error berjalan dalam **mode "terbatas"** bila folder
  `wp-content/mu-plugins` di site tersebut tidak dapat ditulisi connector
  saat aktivasi; halaman detail site menandai kondisi ini di kolom "Penangkap
  error".
- Angka traffic sumber "plugin" dan "ga4" **memang berbeda** dan tidak pernah
  dijumlahkan — GA4 tidak menghitung pengunjung yang memakai ad-blocker atau
  menolak cookie, sedangkan penghitung plugin menghitung semuanya.
- "Jumlah pengunjung harian" di kedua sumber menghitung ulang pengunjung yang
  sama di hari yang berbeda (bukan pengunjung unik sepanjang periode); ini
  angka harian, bukan agregat yang boleh dijumlahkan lintas hari.
- Ambang status keamanan (jumlah percobaan login gagal yang dianggap
  serangan, jendela waktu tembusnya brute force, dst.) adalah konstanta di
  `src/wpmgr/keamanan.py`, bukan setelan yang bisa diubah lewat UI.

## Struktur repo (ringkas)

| Path | Isi |
|---|---|
| `src/wpmgr/` | Aplikasi Python: web (FastAPI), worker, job handler, klien HTTP ke site |
| `src/wpmgr/{uptime,ssl_cek,keamanan,traffic,laporan,retensi,kesehatan}.py` | Modul pemantauan Lapis 2: penilaian uptime, cek SSL, status keamanan, GA4/anomali traffic, laporan bulanan, retensi data, halaman Kesehatan |
| `src/wpmgr/{fitur,versi,kunci,connector_paket,uagent,geoip}.py` | Pendukung Lapis 2: fitur yang diumumkan connector, perbandingan versi, advisory lock cron, paket zip connector, parsing user-agent, GeoIP |
| `src/wpmgr/jobs/monitoring.py`, `src/wpmgr/web/routes_monitoring.py` | Handler job (`collect_events`, `collect_traffic`, `update_connector`) dan API JSON untuk data pemantauan |
| `connector/wp-manager-connector/` | Plugin WordPress yang dipasang di tiap site klien |
| `migrations/` | Migrasi Alembic |
| `tests/unit/`, `tests/integration/`, `tests/e2e/` | Tiga lapis test Python (lihat bagian test di atas) |
| `connector/tests/` | Test PHPUnit plugin connector |
| `deploy/` | Berkas siap salin ke VPS: unit systemd, crontab, konfigurasi nginx |
| `docs/superpowers/` | Spesifikasi desain dan rencana implementasi lapis ini |
