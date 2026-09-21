# WP Manager

Dashboard terpusat untuk memelihara puluhan site WordPress milik client dari satu
tempat: melihat inventaris plugin/tema/core di seluruh site, menjalankan update
(termasuk update massal lintas-site) lewat plugin connector yang terpasang di
tiap site, dan masuk ke wp-admin site mana pun dengan satu klik lewat SSO
bertanda tangan — tanpa dashboard pernah menyimpan atau mengetik password
wp-admin site tersebut. Backend Python/FastAPI menjalankan web dan worker
antrean job terpisah; plugin PHP `wp-manager-connector` di sisi site
mengekspos endpoint REST yang diverifikasi dengan HMAC.

## Prasyarat

- **Python 3.10+** (proyek ini dikembangkan dan diuji dengan 3.10.6; lihat `requires-python` di `pyproject.toml`)
- **PostgreSQL 16** — versi yang dipakai `docker-compose.yml` untuk dev lokal; produksi boleh memakai instance PostgreSQL 16 mana pun
- **Docker + Docker Compose** — untuk menjalankan PostgreSQL dan (untuk test e2e) WordPress + MariaDB di lokal. Tidak dibutuhkan di server produksi dashboard itu sendiri.
- **PHP 8.1+ dan Composer** — untuk menjalankan test PHPUnit plugin connector (`connector/`)
- **Site WordPress klien harus memakai permalink "cantik"** (bukan "Plain"). Lihat catatan di bawah — ini bukan sekadar preferensi kosmetik.

## Setup lokal

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # Windows; di Linux/macOS: .venv/bin/pip
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
proses lokal mesin Anda selama perintah berjalan. Untuk pembuatan user rutin
di server produksi, pertimbangkan menjalankannya dari sesi yang tidak
disimpan riwayatnya, atau ganti password lewat fitur dashboard setelah akun
pertama dibuat.

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
tetap mengumpulkan 84 dari 193 test, termasuk 11 test e2e yang butuh
kontainer WordPress menyala. Di clone segar tanpa Docker jalan, ini gagal
dengan cara yang tidak ada hubungannya dengan perubahan yang sedang diuji.
Gunakan tiga perintah berikut, sesuai apa yang tersedia:

```bash
# Unit — tidak butuh service apa pun (73 test)
.venv/Scripts/python -m pytest -m "not integration and not e2e"

# Integrasi — butuh PostgreSQL (109 test)
docker compose up -d db
.venv/Scripts/python -m pytest tests/integration -m integration

# End-to-end — butuh kontainer WordPress + MariaDB (11 test)
docker compose up -d db wp wpdb wpcli
.venv/Scripts/python -m pytest tests/e2e -m e2e
```

Dan untuk plugin connector PHP (44 test):

```bash
cd connector && php vendor/bin/phpunit
```

(`composer install` sekali di `connector/` dulu jika `vendor/` belum ada.)

## Menambahkan site dan memasang plugin connector

1. Di dashboard, buka **Sites → Tambah Site**, isi nama dan URL (`https://`
   wajib). Dashboard membuatkan secret dan menampilkan satu **kunci koneksi**
   — kunci ini hanya ditampilkan sekali, salin sebelum berpindah halaman.
2. Bungkus plugin connector dari sumber (ini yang diunggah ke site klien;
   jangan unggah folder `connector/` mentah-mentah):

   ```bash
   cd connector && zip -r ../wp-manager-connector.zip wp-manager-connector
   ```

   `connector/vendor/` adalah dependency development (PHPUnit dkk) yang
   dipasang lewat Composer hanya untuk menjalankan test — folder ini **tidak
   boleh ikut masuk ke dalam zip** yang diunggah ke site klien.
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

Berkas siap pakai ada di `deploy/`:

```bash
# sebagai root, setelah /opt/wpmgr berisi clone repo + .venv + .env terisi
useradd -r -s /usr/sbin/nologin wpmgr    # jika user sistem belum ada
chown -R wpmgr:wpmgr /opt/wpmgr
mkdir -p /var/log/wpmgr && chown wpmgr:wpmgr /var/log/wpmgr

cp deploy/wpmgr-web.service deploy/wpmgr-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wpmgr-web wpmgr-worker

crontab -u wpmgr deploy/crontab

cp deploy/nginx.conf /etc/nginx/sites-available/wpmgr.conf
ln -s /etc/nginx/sites-available/wpmgr.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Sesuaikan `server_name` dan path sertifikat di `deploy/nginx.conf`, dan
jalankan `certbot` (atau setara) untuk menerbitkan sertifikat sebelum
`nginx -t` akan lulus dengan path tersebut.

Beberapa detail di berkas-berkas ini tidak kosmetik:

- **`wpmgr-worker.service` mengatur `TimeoutStopSec=240`**, sengaja lebih
  panjang dari `TIMEOUT_UPDATE` (180 detik) yang dipakai `SiteClient` untuk
  request update. Saat `systemctl restart wpmgr-worker` dikirim di tengah
  worker sedang mengeksekusi update, worker perlu waktu untuk sampai ke titik
  aman (commit/rollback) sebelum systemd kehabisan sabar dan mengirim
  `SIGKILL`. Memotongnya lebih pendek dari 180 detik berarti restart bisa
  membunuh worker persis di tengah panggilan HTTP ke site, meninggalkan site
  dalam keadaan yang harus ditebak-tebak oleh reaper berikutnya.
- **`nginx.conf` mengirim header `X-Real-IP`.** Rate limiter di
  `/api/pair/confirm` (10 percobaan/menit) membaca header ini, dan hanya
  mempercayainya ketika koneksi TCP datang dari loopback — yaitu dari nginx
  sendiri. Kalau reverse proxy di depan aplikasi tidak mengirim header ini,
  limiter melihat `127.0.0.1` untuk setiap request yang masuk, dan batas
  yang seharusnya per-penyerang berubah jadi kuota global bersama: satu
  penyerang yang sengaja memicu 429 berulang bisa mengunci semua percobaan
  pairing site lain yang sah.
- **`deploy/crontab` menjalankan `enqueue-scans` setiap jam** (membuat job
  scan inventaris untuk site yang belum punya job scan tertunda) **dan
  `reap-jobs` setiap 5 menit** (memulihkan job yang worker-nya mati di
  tengah jalan). Keduanya lewat Python di virtualenv yang sama dengan
  service, output ditambahkan ke `/var/log/wpmgr/cron.log`.

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

## Struktur repo (ringkas)

| Path | Isi |
|---|---|
| `src/wpmgr/` | Aplikasi Python: web (FastAPI), worker, job handler, klien HTTP ke site |
| `connector/wp-manager-connector/` | Plugin WordPress yang dipasang di tiap site klien |
| `migrations/` | Migrasi Alembic |
| `tests/unit/`, `tests/integration/`, `tests/e2e/` | Tiga lapis test Python (lihat bagian test di atas) |
| `connector/tests/` | Test PHPUnit plugin connector |
| `deploy/` | Berkas siap salin ke VPS: unit systemd, crontab, konfigurasi nginx |
| `docs/superpowers/` | Spesifikasi desain dan rencana implementasi lapis ini |
