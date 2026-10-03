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
tetap mengumpulkan 637 dari 1495 test, termasuk 35 test e2e yang butuh
kontainer WordPress menyala. Di clone segar tanpa Docker jalan, ini gagal
dengan cara yang tidak ada hubungannya dengan perubahan yang sedang diuji.
Gunakan tiga perintah berikut, sesuai apa yang tersedia:

```bash
# Unit — tidak butuh service apa pun (602 test)
.venv/Scripts/python -m pytest -m "not integration and not e2e"

# Integrasi — butuh PostgreSQL (858 test)
docker compose up -d db
.venv/Scripts/python -m pytest tests/integration -m integration

# End-to-end — butuh kontainer WordPress + MariaDB (35 test, termasuk e2e
# staging yang juga menjalankan container `pembantu`; lihat "Staging (Lapis 3)")
docker compose up -d db wp wpdb wpcli
.venv/Scripts/python -m pytest tests/e2e -m e2e
```

Integrasi dan e2e memakai database test yang sama (`wpmgr_test`) dan membuangnya di awal sesi:
jangan jalankan keduanya bersamaan, atau beri salah satunya `TEST_DATABASE_URL` lain.

Dan untuk plugin connector PHP (511 test), di PHP 8.3 lokal dan di PHP 7.4 (versi terendah yang
didukung connector):

```bash
cd connector && php vendor/bin/phpunit
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/app" -w /app/connector php:7.4-cli php vendor/bin/phpunit
```

(`composer install` sekali di `connector/` dulu jika `vendor/` belum ada; perintah PHP 7.4 dijalankan
dari akar repo.)

Skrip pembantu staging (bats, dengan `docker` tiruan):

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" -w /code bats/bats:1.11.0 deploy/staging/tests
```

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

## Staging (Lapis 3)

Setiap site bisa punya satu salinan staging di VPS dashboard, untuk menguji update, preview client,
bekerja, dan mendorong hasil ke produksi dengan snapshot yang bisa dikembalikan. Staging berjalan di
Docker, tetapi proses dashboard **tidak** punya akses Docker. Semua lewat satu skrip root
`/usr/local/sbin/wpmgr-staging` yang dipanggil `sudo -n`. Fitur ini mati selama `WPMGR_STAGING_DOMAIN`
kosong.

**Prasyarat VPS:** Docker (butuh sudo), nginx host, certbot 2.9, `setpriv` (paket util-linux), dan
wildcard DNS `*.staging.<domain>` yang mengarah ke IP VPS. Untuk `staging.halosocia.my.id`, record ini
**sudah ada**: `A *.staging.halosocia.my.id -> 169.58.91.181`, ditambahkan manual di panel DNS
Hostinger (Hostinger tidak punya plugin certbot, karena itu setiap host staging diterbitkan lewat
HTTP-01, bukan DNS-01 — lihat langkah 4 di bawah).

**Pemasangan (sekali, sebagai root, dari `/opt/wpmgr`):**

```bash
# 1. Skema, paket connector 3.0, lalu "Perbarui connector" di halaman Site
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m wpmgr.cli build-connector

# 2. Skrip pembantu, konfigurasinya, dan sudoers
install -o root -g root -m 0755 deploy/staging/wpmgr-staging /usr/local/sbin/wpmgr-staging
install -d -o root -g root -m 0755 /etc/wpmgr-staging
install -o root -g root -m 0644 deploy/staging/staging.conf.contoh /etc/wpmgr-staging/staging.conf
#    isi DOMAIN dan ACME_EMAIL (sama dengan WPMGR_STAGING_EMAIL_ACME) di staging.conf; DOMAIN di
#    sini HARUS persis sama dengan domain yang dipakai di deploy/staging/nginx-wpmgr-staging.conf
#    (staging.halosocia.my.id) -- keduanya tidak saling membaca, jadi tidak ada validasi otomatis
#    kalau salah satu diubah tanpa yang lain. NGINX_GROUP (opsional, default www-data) juga di
#    sini -- lihat catatan "Kunci privat sertifikat" di bawah.
install -d -o wpmgr -g wpmgr -m 0700 /var/lib/wpmgr/staging
install -o root -g root -m 0440 deploy/staging/sudoers-wpmgr-staging /etc/sudoers.d/wpmgr-staging
visudo -cf /etc/sudoers.d/wpmgr-staging

# 3a. br_netfilter (WAJIB agar isolasi antar-container berlaku; siapkan menolak bila mati).
#     Skrip tidak menulis sysctl; jadikan permanen:
echo br_netfilter > /etc/modules-load.d/br_netfilter.conf
echo 'net.bridge.bridge-nf-call-iptables=1' > /etc/sysctl.d/99-wpmgr-staging.conf
modprobe br_netfilter && sysctl --system
# 3. Jaringan, isolasi iptables, MariaDB, Mailpit, router, wp-cli (idempoten)
wpmgr-staging siapkan
cp deploy/staging/wpmgr-staging-siapkan.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable wpmgr-staging-siapkan

# 4. nginx host (SEKALI; staging baru tidak butuh reload). Pra-cek dulu: pastikan tidak ada site
#    lain yang sudah mengklaim wildcard serupa yang bisa menabrak host staging.
grep -rn 'server_name.*\*\.halosocia' /etc/nginx/sites-enabled
#    nginx memilih server_name paling SPESIFIK (*.staging.halosocia.my.id mengalahkan
#    *.halosocia.my.id untuk host yang cocok keduanya), jadi kecocokan yang lebih umum di atas
#    biasanya aman diabaikan. Tapi bila ada site lain yang PERSIS memakai
#    *.staging.halosocia.my.id, atau site lain punya `default_server` di :443, host staging bisa
#    salah dirutekan atau menabrak sertifikat site itu -- selesaikan tabrakan itu dulu (persempit
#    pola site lain, atau hapus default_server-nya) sebelum lanjut.
cp deploy/staging/nginx-wpmgr-staging.conf /etc/nginx/sites-enabled/wpmgr-staging.conf
nginx -t && systemctl reload nginx

# 5. Variabel .env, cron, dan worker khusus staging
crontab -u wpmgr deploy/crontab
cp deploy/wpmgr-worker@.service /etc/systemd/system/ && systemctl daemon-reload
systemctl enable --now wpmgr-worker@staging
systemctl restart 'wpmgr-worker@*' wpmgr-web
```

`/usr/local/sbin/wpmgr-staging` harus tetap root-owned `0755` dan direktori `/usr/local/sbin` sendiri
tidak boleh ditulisi `wpmgr` — pada Ubuntu 24.04 baku ini sudah begitu; jangan mengubah pemiliknya.
Sudoers hanya memberi `wpmgr` hak menjalankan skrip itu (`env_reset` aktif, tanpa baris `env_keep`),
jadi variabel lingkungan yang dikirim `wpmgr` (termasuk dua kait test skrip pembantu,
`WPMGR_STG_PATH`/`WPMGR_STG_KONF`) tidak pernah ikut lewat ke proses root. `/var/lib/wpmgr`, `certs/`,
`acme/`, dan `letsencrypt/` harus tetap root-owned dan tidak bisa ditulisi `wpmgr`. **Pengecualiannya
`staging/`** (`STAGING_DIR`, dibuat di langkah 2 di atas): itu justru milik `wpmgr`, `0700` — direktori
kerja dashboard sendiri untuk berkas staging dan snapshot. Syarat root-owned pada `CERT_DIR`/`ACME_DIR`/
`LE_DIR` **ditegakkan**, bukan cuma konvensi: `wpmgr-staging sertifikat` memeriksa setiap komponen path
ketiganya (direktori itu sendiri dan induk langsungnya) lewat `cek_mount_root` — bukan symlink, milik
root — sebelum menulis apa pun, dan menolak (`GALAT ditolak`) bila dilanggar.

Lalu aktifkan **Izinkan staging** di **Pengaturan → WP Manager** di wp-admin setiap site yang akan
distaging. Tanpa setelan itu connector membalas 403 untuk semua endpoint staging.

Periksa hasilnya dengan `sudo -u wpmgr sudo -n /usr/local/sbin/wpmgr-staging status`. Perintah ini
harus mencetak JSON berisi memori, disk, dan container. Baru setelah itu buat staging pertama dari UI
dan tunggu job tarik selesai — pemakaian pertama inilah yang benar-benar menguji wp-cli di dalam
container (dijalankan sebagai UID numerik dashboard tanpa entri `/etc/passwd`, jadi `HOME` tidak
otomatis terset); kalau wp-cli gagal karena itu, galatnya baru terlihat di sini, bukan saat `siapkan`.

Catatan:

- **`wpmgr-worker@staging` wajib.** Job staging hanya diambil instans worker yang namanya diawali
  `staging`, karena tarik site 20 GB bisa berjalan berjam-jam dan tidak boleh memakan worker umum.
  Tarik dan uji boleh berjalan bersamaan dengan scan/update site yang sama. Dorong dan kembalikan
  tidak boleh.
- **Jumlah worker staging harus tetap satu.** Jangan mengaktifkan instans kedua (mis.
  `wpmgr-worker@staging2`): kode tidak mencegahnya, tetapi RAM (±6,4 GB tersedia) dan disk VPS
  dihitung per operasi, bukan dikoordinasikan lintas worker, sehingga dua tarik/dorong 20 GB berjalan
  bersamaan bisa menghabiskannya.
- **`/etc/letsencrypt/options-ssl-nginx.conf`** dibuat certbot `--nginx` dan dipakai site lain di
  `sites-enabled`. Bila berkas itu tidak ada di VPS, ganti baris `include` dengan baris `ssl_protocols`/
  `ssl_ciphers` dari server block site lain sebelum `nginx -t`.
- **Sertifikat diterbitkan satu per host staging**, bukan wildcard (DNS Hostinger tidak punya plugin
  certbot untuk DNS-01), jadi tunduk pada batas laju Let's Encrypt per domain terdaftar
  (`halosocia.my.id`). Membuat/menghapus banyak staging dalam waktu singkat bisa memicu batas itu;
  `wpmgr-staging sertifikat` memakai `--keep-until-expiring` supaya penerbitan ulang untuk host yang
  sama tidak ikut menghitung.
- **Sebelum sertifikat sebuah staging terbit, handshake TLS-nya ditolak.** `ssl_certificate`/
  `ssl_certificate_key` di nginx host dibaca dari `/var/lib/wpmgr/certs/<host>/`, yang baru ada
  setelah `wpmgr-staging sertifikat <nama>` sukses; sebelum itu klien yang membuka
  `https://<nama>.staging.halosocia.my.id` mendapat galat TLS, bukan halaman WordPress. Dashboard
  menandai staging seperti ini dengan status "sedang diterbitkan" di UI, dan cron
  `renew-staging-certs` (harian) MENCOBA ULANG penerbitan yang gagal di percobaan sebelumnya --
  jadi kondisi ini biasanya sembuh sendiri dalam 24 jam tanpa campur tangan operator.
- **Kunci privat sertifikat (`privkey.pem`) dipasang `0640 root:$NGINX_GROUP`**, bukan `0600
  root:root`, karena `ssl_certificate_key` berbasis variabel di sini dibaca proses WORKER nginx
  (mis. `www-data`) setiap handshake, bukan cuma master yang start sebagai root; tanpa grup yang
  tepat setiap handshake staging gagal "Permission denied". `NGINX_GROUP` di
  `/etc/wpmgr-staging/staging.conf` defaultnya `www-data` (Debian/Ubuntu); ganti kalau paket nginx
  di VPS memakai grup lain (mis. `nginx`) -- skrip pembantu menolak jalan (`GALAT konfigurasi`)
  bila grup itu tidak ada di sistem.
  **Siapa saja yang bisa membaca kunci ini:** SETIAP proses yang berjalan sebagai grup
  `NGINX_GROUP`, tidak cuma nginx. Di Ubuntu baku itu termasuk pool php-fpm site lain, kalau
  semuanya juga dijalankan sebagai `www-data` (pola umum di shared hosting) — proses itu ikut bisa
  membaca `privkey.pem` staging manapun. Dampaknya terbatas pada bisa MENYAMAR sebagai host staging
  itu lewat TLS (mis. terminasi TLS palsu di tempat lain); kunci ini bukan kunci SSH/kredensial
  database (perlu diingat: salinan staging sendiri memuat data produksi, lihat catatan "Isi salinan
  staging" di bawah). Skrip pembantu menolak start
  (`GALAT konfigurasi`) bila `NGINX_GROUP` adalah grup `root`, grup utama pengguna dashboard, atau
  salah satu grup tambahan pengguna dashboard — tapi **`wpmgr` sendiri tidak boleh pernah dijadikan
  anggota `NGINX_GROUP`** lewat cara lain (mis. `usermod -aG www-data wpmgr`): pengecekan itu hanya
  berjalan sekali saat skrip pembantu start, bukan terus-menerus, jadi keanggotaan yang ditambahkan
  belakangan tidak terdeteksi otomatis dan akan membuat proses dashboard sendiri ikut bisa membaca
  kunci privat staging manapun.
- **Image dipin lewat digest** di `/etc/wpmgr-staging/digest.lock`, yang diisi pada pemakaian pertama
  setiap image. Untuk memperbarui image (mis. rilis keamanan PHP), hapus barisnya lalu jalankan
  `wpmgr-staging siapkan`. Container staging memakai image baru pada tarik berikutnya.
- **Isolasi:** container staging boleh ke internet (update plugin), tetapi tidak ke host atau jaringan
  privat. Aturan itu ada di rantai `INPUT` dan `DOCKER-USER` untuk jembatan `br-wpmgrstg`.
- **Aturan iptables isolasi itu bertahan lebih dari yang terlihat, tapi tidak tanpa batas.** Unit
  `wpmgr-staging-siapkan.service` memakai `Requires=docker.service` DITAMBAH `PartOf=docker.service`,
  jadi `siapkan` (idempoten) ikut jalan ulang setiap kali `docker.service` distop/direstart, baik
  manual (`systemctl restart docker`) maupun otomatis (dockerd crash lalu di-restart systemd
  sendiri) — walau sebenarnya restart Docker SENDIRI tidak menghapus aturan itu (dockerd tidak
  pernah membersihkan rantai `DOCKER-USER`/`INPUT`). Yang benar-benar MENGHAPUS aturan ini hanya
  **reboot host**, atau **firewall di-reload/ditulis ulang total** (`ufw reload`, `firewalld
  reload`, `iptables-restore` dari berkas yang tidak menyertakan rantai kustom ini) — keduanya
  menimpa seluruh tabel netfilter, bukan cuma milik Docker. Sesudah salah satu dari itu, jalankan
  `systemctl restart wpmgr-staging-siapkan` secara manual untuk memasang ulang isolasi jaringan
  staging.
- **`listen [::]:80`/`listen [::]:443`** di `nginx-wpmgr-staging.conf` menjadikan berkas ini
  *default* untuk IPv6 bila belum ada site lain di `sites-enabled` yang mendengarkan IPv6 di port
  itu (nginx menandai `listen` IPv6 pertama sebagai default secara implisit). Ini tidak berbahaya
  (map dan `server_name` tetap memvalidasi), tapi kalau `staging.halosocia.my.id` tidak punya
  rekaman DNS `AAAA`, kedua baris `[::]` ini boleh dihapus -- tidak ada trafik yang akan
  memakainya.
- **Kata sandi preview** ditampilkan sekali saat staging dibuat atau kata sandinya dibuat ulang
  (pengguna `staging`). Tombol **Masuk admin staging** melewati Basic Auth dengan tautan bertanda
  tangan yang berlaku 12 jam.
- **Isi salinan staging = salinan penuh produksi.** Database staging memuat semua data produksi,
  termasuk kredensial pihak ketiga yang disimpan di database (kunci API payment/email/SMTP,
  token integrasi, hash kata sandi pengguna, dan sebagainya), dan kode PHP-nya (plugin/tema) berjalan
  di container yang bisa dimodifikasi siapa pun yang mengambil alih staging itu. Karena itu
  staging TIDAK dianggap tempat yang aman untuk rahasia, dan isolasinya ditegakkan berlapis:
  (a) setiap tarik mengganti `wpmgr_secret` connector di database salinan dengan secret acak milik
  staging itu sendiri (disimpan terenkripsi di dashboard, dipakai SSO staging), sehingga salinan
  tidak pernah memegang kunci yang berlaku di produksi dan token produksi tidak berlaku di staging;
  dorong tidak pernah menyalin secret staging ke produksi (opsi `wpmgr_*` produksi dipertahankan);
  (b) `wpmgr-staging siapkan` membatasi lalu lintas antar-container di jembatan `br-wpmgrstg` lewat
  rantai iptables `WPMGR-STG-ANTAR` (dipanggil dari `DOCKER-USER`): hanya balasan koneksi,
  router -> container tcp/80, container -> db tcp/3306, dan container -> mail tcp/1025 yang lolos,
  sisanya dibuang, jadi satu staging tidak bisa menjangkau staging lain atau API Mailpit; db, mail,
  dan router memakai alamat tetap di puncak `SUBNET` (container lama tanpa alamat tetap dibuat ulang
  oleh `siapkan`, jadi jalankan `wpmgr-staging siapkan` lagi sesudah memperbarui skrip); dan
  (c) UI/API Mailpit dikunci Basic Auth dengan kredensial buatan `siapkan`
  (`/etc/wpmgr-staging/mail-auth`, hanya terbaca root, diambil dashboard lewat
  `wpmgr-staging mail-kredensial`). Isolasi ini membatasi dampak satu staging yang disusupi
  terhadap staging lain dan terhadap kunci produksi; ia tidak menyembunyikan data produksi dari
  orang yang memang boleh membuka staging itu. Staging lama (dibuat sebelum perubahan ini) baru
  memakai secret sendiri setelah disegarkan.
- **Email dari staging** tidak pernah terkirim: semua dialihkan ke Mailpit dan bisa dibaca di tab
  Staging. Plugin yang mengirim email lewat API HTTP penyedia (bukan SMTP/PHPMailer) tetap mengirim
  sungguhan; spanduk di wp-admin staging mengingatkan hal ini.

| Variabel | Default | Untuk |
|---|---|---|
| `WPMGR_STAGING_DOMAIN` | *(kosong = fitur mati)* | Domain induk staging, mis. `staging.halosocia.my.id` |
| `WPMGR_STAGING_DIR` | `/var/lib/wpmgr/staging` | Berkas staging, snapshot, dan area kerja (0700 milik `wpmgr`) |
| `WPMGR_STAGING_PEMBANTU` | `/usr/local/sbin/wpmgr-staging` | Lokasi skrip pembantu |
| `WPMGR_STAGING_MAKS_AKTIF` | `3` | Staging aktif paling banyak |
| `WPMGR_STAGING_JEDA_HARI` | `3` | Jeda otomatis setelah sekian hari tanpa akses |
| `WPMGR_STAGING_SNAPSHOT` | `3` | Snapshot produksi yang disimpan per site |
| `WPMGR_STAGING_EMAIL_ACME` | *(kosong)* | Email Let's Encrypt; salin juga ke `ACME_EMAIL` di `/etc/wpmgr-staging/staging.conf` |

Cron baru (`deploy/crontab`): `staging-jeda-otomatis` tiap jam, `renew-staging-certs` dan
`prune-staging` harian.

E2E staging (`tests/e2e/test_staging.py`) menjalankan skrip pembantu sungguhan di container
`pembantu` (profil compose `staging`, image `docker:27-cli` + bash/coreutils/setpriv) dengan socket
Docker Desktop; dashboard di pytest memanggilnya lewat `WPMGR_STAGING_PEMBANTU_AWALAN`, tanpa sudo.
Akar staging adalah bind mount `./var/e2e-stg` (di-`.gitignore`). Bind mount Windows di Docker
Desktop menyimpan pemilik Unix (`chown` bertahan), jadi cek pemilik skrip tetap berlaku; test hanya
menyerahkan direktori site ke UID 33 seperti yang terjadi di VPS, setelah lebih dulu membuktikan
skrip menolak direktori milik root. Router staging di `localhost:8090`, Mailpit di `localhost:8025` (Basic Auth; kredensial dari `wpmgr-staging mail-kredensial`).

```bash
docker compose up -d wp wpdb wpcli     # plus `db` bila PostgreSQL test belum jalan
.venv/Scripts/python -m pytest -m e2e tests/e2e/test_staging.py -q
```

Bila port 8081 sudah dipakai stack lain, jalankan WordPress e2e di port lain dengan
`WPMGR_E2E_WP_PORT=8082` pada kedua perintah di atas. Tarik menolak bila sisa disk sesudahnya di bawah
15%; bila drive repo sesempit itu, pindahkan akar staging e2e ke drive lain dengan
`WPMGR_E2E_STG_AKAR=C:/Users/<anda>/AppData/Local/Temp/wpmgr-e2e-stg`. Pilih path pendek: skrip
pembantu hanya menerima path sumber mount (sebagai `/run/desktop/mnt/host/<drive>/...`) sampai 200
karakter. Jalan pertama menarik image
`wordpress:php8.1-apache`, `mariadb:11.4`, `nginx:1.27-alpine`, dan `axllent/mailpit` (beberapa
menit). Skenario uji update butuh wordpress.org; tanpa internet keduanya dilewati dengan pesan.

Test skrip pembantu berjalan di container bats dengan `docker` tiruan:

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" -w /code bats/bats:1.11.0 deploy/staging/tests
```

Keterbatasan staging:

- Konsistensi database hanya dijamin per potongan (2.000 baris). Tabel tanpa primary key yang
  ditulisi selama tarik bisa kehilangan atau menggandakan baris, dan job mencatatnya sebagai peringatan.
- **Site dengan tabel produksi berpartisi (`PARTITION`) atau CREATE TABLE yang memuat komentar SQL**
  (umum pada dump MySQL 8) **tidak bisa didorong (timpa penuh).** Connector menolak keduanya saat
  mengimpor ulang, jadi pemeriksaan awal (R8) menolak job sejak awal dengan pesan jelas, sebelum apa
  pun diunggah — supaya rollback tidak pernah mustahil di tengah jalan.
- Tanda air hanya mengenal posts, komentar, user, pesanan WooCommerce, Gravity Forms, WPForms, Fluent
  Forms, dan Flamingo. Data plugin lain bisa tertimpa oleh **timpa penuh**; snapshot tetap
  memungkinkan pengembalian.
- **Kembalikan** snapshot dari dorongan *hanya kode* memulihkan berkas saja. Ekspor database di snapshot
  itu disimpan untuk pemulihan manual.
- Multisite dan site dengan `wp-content` di luar folder WordPress belum didukung.

## Pindah hosting (Lapis 4)

Memindahkan site dari shared hosting (tanpa SSH) ke VPS dashboard dan menjalankannya di sana sebagai
produksi, di container Docker. Data diambil lewat connector 3.0 (endpoint baca `/staging/*` Lapis 3) dari IP
hosting lama; site lama **tidak pernah diubah**. Spesifikasi: `docs/superpowers/specs/2026-10-03-wp-manager-lapis4-design.md`.
Semua langkah root di bawah dijalankan operator dengan `sudo` (atau sebagai root). Tidak ada kata sandi
atau kredensial FTP yang perlu ditulis di berkas mana pun di repo ini.

### Prasyarat

- Lapis 3 sudah terpasang di VPS dan `wpmgr-staging siapkan` sukses (domain staging
  `staging.halosocia.my.id` dengan wildcard DNS dan nginx `wpmgr-staging.conf`).
- Site lama terdaftar di dashboard, connector 3.0 terpasang lewat wp-admin, pairing sukses, dan
  **Izinkan staging** menyala di *Pengaturan → WP Manager*.
- `home`/`siteurl` site lama memakai `https://` tanpa subfolder.
- RAM tersedia VPS ≥ 2 GB dan sisa disk sesudah salin ≥ 15%.
- `/etc/letsencrypt/options-ssl-nginx.conf` ada (sama dengan Lapis 3).

### Pemasangan (sekali)

1. Merge, lalu `alembic upgrade head` dan `pip install -e .` (dependensi baru `dnspython`).
2. Pasang ulang skrip pembantu: `sudo install -o root -g root -m 0755 deploy/staging/wpmgr-staging /usr/local/sbin/wpmgr-staging`.
   Sudoers tidak berubah (satu entri untuk seluruh skrip, termasuk subperintah `prod-*`).
3. Tambahkan kunci hosting dari `deploy/staging/staging.conf.contoh` ke `/etc/wpmgr-staging/staging.conf`
   (`HOSTING_DIR`, `PROD_SUBNET`, `PROD_ROUTER_PORT`, `PROD_CERT_DIR`, `NGINX_HOSTING_DIR`, `BACKUP_DIR`,
   `IP_PUBLIK`), lalu siapkan direktori data:
   `sudo install -d -o wpmgr -g wpmgr -m 0700 /var/lib/wpmgr/hosting`.
4. **Pra-cek nginx host** (jangan dilewati). Container produksi tinggal di `172.31.251.0/24` dan boleh
   membuka port 80/443 IP publik VPS. Vhost lain di VPS (sekitar 19 situs) tidak boleh memercayai rentang
   privat, atau container yang diretas diperlakukan sebagai proxy tepercaya. Periksa semuanya:

   ```bash
   sudo grep -rn "set_real_ip_from\|allow 172\|allow 10\.\|allow 192\.168" /etc/nginx/
   sudo grep -rn 'allow\|deny' /etc/nginx/sites-enabled
   sudo nginx -T | grep -n server_name
   ```

   Tinjau setiap temuan dua `grep` pertama: tidak boleh ada `set_real_ip_from` atau `allow` yang mencakup
   rentang privat (RFC1918: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) sehingga `172.31.251.0/24`
   ikut terpercaya. Perbaiki dulu bila ada. Dari `server_name`, pastikan tidak ada nama untuk domain yang akan
   dipindah.
5. Jalankan `sudo wpmgr-staging prod-siapkan` (membuat `KONF_DIR/prod`, `php.ini`, jaringan `wpmgr-prod`,
   aturan iptables, `wpmgr-prod-db`, `wpmgr-prod-router`). Pasang ulang
   `deploy/staging/wpmgr-staging-siapkan.service` (kini punya `ExecStart` kedua `prod-siapkan`), lalu
   `sudo systemctl daemon-reload`. Lapis 4 juga memperbaiki aturan INPUT staging dengan
   `ESTABLISHED,RELATED` di atas `DROP`, supaya balasan router ke nginx host tidak dibuang; jalankan
   `sudo wpmgr-staging siapkan` sekali lagi sesudah memasang skrip baru.
6. Pasang include nginx hosting: salin `deploy/staging/nginx-wpmgr-hosting.conf` ke
   `/etc/nginx/sites-enabled/wpmgr-hosting.conf`, jalankan `sudo install -d -m 0755 /etc/nginx/wpmgr-hosting`,
   lalu `sudo nginx -t && sudo systemctl reload nginx`.
7. Isi variabel di `.env`:

   | Variabel | Nilai di VPS ini |
   |---|---|
   | `WPMGR_HOSTING_IPV4` | `169.58.91.181` (kosong = fitur hosting mati) |
   | `WPMGR_HOSTING_IPV6` | `2a02:c207:2347:2607::1` (kosong = AAAA wajib dihapus) |
   | `WPMGR_HOSTING_DIR` | `/var/lib/wpmgr/hosting` (sama dengan `HOSTING_DIR`) |
   | `WPMGR_HOSTING_RESOLVER` | `1.1.1.1,8.8.8.8` |
   | `WPMGR_BACKUP_TUJUAN` / `_HARIAN` / `_MINGGUAN` | `lokal` / `7` / `4` |

8. Pasang ulang `deploy/crontab` (`crontab -u wpmgr deploy/crontab`; berkas ini wajib berakhiran baris LF,
   dijaga `.gitattributes`, karena cron menolak CRLF). **Jadwal hosting memakai zona VPS `Europe/Berlin`,
   tanpa `CRON_TZ`** (cron Debian mengabaikannya): `hosting-cek-dns` tiap 10 menit; `backup-hosting`
   `30 21 * * *` = 02:30 WIB di musim panas (CEST) dan 03:30 WIB di musim dingin (CET);
   `renew-hosting-certs` `50 21 * * *` = 21:50 Berlin (02:50 WIB musim panas, 03:50 WIB musim dingin).
9. Restart `wpmgr-web`, `wpmgr-worker@1`, `wpmgr-worker@2`, dan `wpmgr-worker@staging` (job hosting
   diproses worker staging).

**Sesudah reboot VPS:** Docker menjalankan ulang container produksi (kebijakan restart) sebelum
`wpmgr-staging-siapkan.service` memasang ulang aturan iptables isolasi. Unit itu memerlukan Docker berjalan
(`siapkan` dan `prod-siapkan` memanggil `docker`, dan rantai `DOCKER-USER` baru ada sesudah dockerd hidup),
jadi urutannya tidak bisa dibalik dengan `Before=docker.service`. Akibatnya ada jendela beberapa detik saat
boot ketika isolasi container produksi belum terpasang. Risiko ini diterima dan didokumentasikan. Sesudah
reboot, pastikan unit sukses: `systemctl status wpmgr-staging-siapkan` dan `sudo iptables -S WPMGR-PROD-MASUK`.

### Alur pengguna

1. Tab **Hosting VPS** → **Pindahkan ke VPS**. Dashboard menurunkan domain dari URL site, mencari IP
   hosting lama lewat DNS publik, dan menampilkan kata sandi pratinjau **sekali**.
   **Bila site lama berada di balik CDN hPanel** (mis. `rizkycahayaraya.com`, record CNAME ke
   `*.cdn.hstgr.net` atau IP CDN), matikan CDN di hPanel **sebelum** menekan *Pindahkan ke VPS*, dan tunggu
   DNS menampilkan IP hosting asli. Selama CDN aktif, IP hosting lama tidak terlihat dan dashboard menolak
   dengan pesan CDN.
2. Penyalinan berjalan di latar (bisa dilanjutkan bila terputus). Hasilnya dibuka di
   `https://vps-<nama>.staging.halosocia.my.id` (pengguna `pratinjau`), dengan email diblokir, WP-Cron mati,
   dan noindex. Cara kedua: baris `169.58.91.181 <domain> www.<domain>` di berkas hosts komputer Anda
   (browser memperingatkan sertifikat; lanjutkan). **Salin ulang** tersedia selama belum aktif.
3. **Pratinjau sudah benar, lanjut ke DNS** → tabel record DNS. Ubah di hPanel, lalu tunggu: cron
   memeriksa tiap 10 menit (atau tombol **Periksa DNS & aktifkan sekarang**).
4. Begitu DNS lolos, aktivasi berjalan otomatis: sertifikat domain + `www`, salin terakhir dari IP hosting
   lama, tukar (wp-config, router, dan nginx mode aktif), verifikasi HTTPS. Status menjadi
   **Dihosting di VPS**, lalu backup pertama diantrekan dalam 10 menit.
5. Hosting lama boleh dimatikan sesudahnya. Sebelum itu periksa entri form/komentar yang mungkin masuk ke
   hosting lama sejak salinan terakhir, dan bandingkan konstanta khusus `wp-config.php` lama (File Manager
   hPanel) karena berkas itu tidak pernah disalin.

### Record DNS

| Record | Tindakan |
|---|---|
| `A @` | ubah ke `169.58.91.181` |
| `A www` | ubah ke `169.58.91.181` (bila www dipakai). Bila `www` berupa CNAME ke `*.cdn.hstgr.net` (CDN Hostinger), matikan CDN di hPanel dan ganti CNAME itu dengan A |
| `AAAA @`, `AAAA www` | bila `WPMGR_HOSTING_IPV6` diisi (`2a02:c207:2347:2607::1`): ubah ke nilai itu. Bila kosong: **hapus** |

AAAA lama ke IPv6 shared hosting Hostinger wajib diubah atau dihapus: Let's Encrypt mendahulukan IPv6 saat
validasi HTTP-01 (sertifikat tidak akan terbit), dan pengunjung IPv6 akan tetap ke hosting lama sehingga
isian form tersebar di dua tempat. Cek DNS menahan aktivasi sampai semua resolver (`1.1.1.1`, `8.8.8.8`)
sepakat. CAA yang ada wajib mengizinkan `letsencrypt.org`.

**TTL:** turunkan TTL record ke 300 detik sehari sebelum mengubah DNS bila memungkinkan, supaya jendela
"sebagian pengunjung masih ke hosting lama" sependek mungkin.

**Rollback:** sampai hosting lama dimatikan, kembalikan DNS ke nilai lama. Data yang masuk di VPS sesudah
aktivasi tidak ikut kembali.

### Pemulihan backup manual

Backup harian (tujuan `lokal`) ada di `/var/lib/wpmgr/backup/<site_id>/<stempel>/` (root-only):
`db.sql.gz`, `files.tar.gz` (berisi direktori `files/`), dan `manifest.json` (memuat `sha256_db`,
`sha256_file`, `prefix`, `versi_php`). Retensi: 7 harian + 4 mingguan (`WPMGR_BACKUP_HARIAN` /
`WPMGR_BACKUP_MINGGUAN`). **Backup hanya ada di disk VPS ini; backup off-site belum dibuat.** Pemulihan
**manual** (tidak ada tombol di dashboard). `prod-db-impor` tidak dipakai di sini karena menolak situs yang
sudah aktif (batas satu arah) dan hanya membaca dump dari stdin; karena itu database dipulihkan dengan
`docker exec` ke `wpmgr-prod-db` memakai kredensial root database.

Cari nama situs, `site_id`, dan domain dari state root: `sudo ls /etc/wpmgr-staging/prod/situs/` dan
`sudo cat /etc/wpmgr-staging/prod/situs/<nama>` (`SITE_ID=`, `DOMAIN=`). Lalu sebagai root dengan
`S=<site_id> N=<nama> T=<stempel> D=prd_<nama dengan - menjadi _>`:

```bash
B=/var/lib/wpmgr/backup/$S/$T
cd "$B" && cat manifest.json && sha256sum db.sql.gz files.tar.gz   # cocokkan dengan manifest
docker stop wpp-$N
# Berkas: yang sekarang disisihkan, bukan dihapus
mv /var/lib/wpmgr/hosting/$S/files /var/lib/wpmgr/hosting/$S/files.sebelum-pulih-$(date +%s)
cat "$B/files.tar.gz" | sudo -u wpmgr tar -xzf - -C /var/lib/wpmgr/hosting/$S
# Database
printf '[client]\nuser=root\npassword=%s\n' "$(cat /etc/wpmgr-staging/prod/db-root)" \
  | docker exec -i wpmgr-prod-db sh -c 'umask 077; cat > /run/pulih.cnf'
docker exec wpmgr-prod-db mariadb --defaults-extra-file=/run/pulih.cnf \
  -e "DROP DATABASE \`$D\`; CREATE DATABASE \`$D\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
gunzip -c "$B/db.sql.gz" | docker exec -i wpmgr-prod-db mariadb --defaults-extra-file=/run/pulih.cnf "$D"
docker exec wpmgr-prod-db rm -f /run/pulih.cnf
wpmgr-staging prod-jalan $N
```

`tar` diekstrak sebagai `wpmgr`, sehingga kepemilikan sesuai pemeriksaan skrip. Hak user database situs
tetap ada sesudah `DROP DATABASE`, jadi tidak perlu dibuat ulang. Hapus direktori `files.sebelum-pulih-*`
manual sesudah situs diperiksa. Bila `sha256sum` tidak cocok dengan manifest, jangan pulihkan dari backup
itu; pakai stempel lain. Lakukan satu kali latihan pemulihan pada situs pertama sebelum hosting lamanya
dimatikan.

Karena backup berada di disk yang sama dengan situs, salin keluar VPS secara berkala, mis.
`rsync -a /var/lib/wpmgr/backup/ <tujuan-lain>:/backup-wpmgr/` dari crontab root.

### Melepas site aktif secara manual

Dashboard tidak bisa menghapus situs yang sudah dilayani VPS (dan site-nya tidak bisa dicabut selama ada
baris hosting). Sebagai root, dengan `N`, `S`, `D` seperti di atas dan domain `DOM`:

```bash
rm /etc/nginx/wpmgr-hosting/$DOM.conf && nginx -t && systemctl reload nginx
docker rm -f wpp-$N
# DROP DATABASE `$D`; DROP USER '$D'@'%';  (lewat berkas opsi root seperti pemulihan di atas)
rm -f /etc/wpmgr-staging/prod/situs/$N /etc/wpmgr-staging/prod/db/$N
rm -f /etc/wpmgr-staging/prod/router/conf.d/prd-$N.conf && wpmgr-staging prod-router-muat
psql "$DATABASE_URL" -c "DELETE FROM hosting_vps WHERE site_id = '$S';"
```

Sertifikat domain di `/var/lib/wpmgr/hosting-certs/` dan direktori data `/var/lib/wpmgr/hosting/$S`
dibiarkan; hapus manual bila sudah tidak diperlukan.

### Batas Lapis 4

- `mail()` PHP tidak berfungsi di container (image WordPress tanpa MTA): pakai plugin SMTP ke penyedia
  luar untuk form/notifikasi.
- Salt `wp-config.php` dibuat baru: semua pengguna login ulang sesudah pindah.
- Plugin cache LiteSpeed (Hostinger) tidak aktif di Apache; aman, dan boleh dinonaktifkan sesudah pindah.
- Jangan menjalankan `certbot --nginx` untuk domain yang dihosting dashboard: berkas di
  `/etc/nginx/wpmgr-hosting/` selalu ditimpa `prod-domain`.
- Sertifikat host pratinjau tidak diperpanjang otomatis; **Salin ulang** menerbitkannya lagi.
- Pindah balik (VPS ke hosting lain), multisite, dan WordPress di subfolder tidak didukung.

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

Keterbatasan hosting VPS (Lapis 4): pemulihan backup hanya manual (lihat "Pemulihan backup manual"),
backup hanya ke disk VPS sendiri (off-site belum dibuat), jendela beberapa detik saat reboot sebelum isolasi
iptables produksi terpasang (lihat "Sesudah reboot VPS"), dan pra-cek `nginx -T` membaca `server_name` per
baris (direktif yang dipecah ke beberapa baris di berkas milik site lain hanya terbaca baris pertamanya;
deteksi `conflicting server name` saat `nginx -t` tetap menjadi lapis kedua).

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
| `src/wpmgr/staging/` | Lapis 3: validasi, skrip pembantu, paket biner, rencana, job tarik/uji/dorong/kembalikan, cron |
| `deploy/staging/` | Skrip pembantu root, sudoers, nginx host staging, unit systemd, test bats |
| `src/wpmgr/hosting/` | Lapis 4: klien hosting lama baca-saja, pindah_tarik/pindah_aktifkan, cek DNS, backup, cron |
| `src/wpmgr/web/routes_hosting.py` | API JSON tab Hosting VPS |
| `docs/superpowers/` | Spesifikasi desain dan rencana implementasi lapis ini |
