# WP Manager — Desain Lapis 1: Update Management & SSO

**Tanggal:** 2026-09-20
**Status:** Disetujui untuk implementasi
**Lingkup:** Lapis 1 dari rencana berlapis. Lapis 2–4 (backup, monitoring, staging) di luar dokumen ini.

---

## 1. Tujuan

Membangun dashboard terpusat untuk memelihara ~10–40 site WordPress milik client
(mayoritas company profile, jarang berubah isinya). Lapis 1 menyelesaikan dua
pekerjaan yang paling sering diulang secara manual:

1. **Melihat dan menjalankan update** core, plugin, dan tema di seluruh site dari
   satu layar, termasuk update massal lintas-site.
2. **Masuk ke wp-admin site mana pun dengan satu klik**, tanpa menyimpan atau
   mengetik password.

Kriteria keberhasilan: pekerjaan maintenance rutin yang sekarang berarti membuka
puluhan tab dan login berkali-kali menjadi satu layar dan beberapa klik, dengan
catatan audit atas setiap perubahan yang dilakukan.

---

## 2. Konteks dan Batasan

| Batasan | Nilai | Konsekuensi desain |
|---|---|---|
| Hosting site client | Campuran shared hosting & VPS | Tidak boleh ada asumsi SSH/WP-CLI/root. Semua aksi lewat kode PHP di dalam WordPress. |
| `max_execution_time` shared hosting | Umumnya 30–120 detik | Setiap operasi harus muat dalam satu request pendek, atau dipecah jadi banyak request kecil. |
| Jumlah site | ~10–40, ~25 plugin per site | ±1.000 baris inventaris. Semua data muat di klien; tidak perlu paging server. |
| Server dashboard | VPS kecil milik sendiri (1–2 GB) | Boleh menjalankan proses worker permanen dan cron sistem. |
| Firewall site | Sebagian pakai Wordfence/Cloudflare | Arah masuk (dashboard → site) bisa diblokir; harus terdeteksi saat pairing. |

### Keputusan stack

- **Backend:** Python + FastAPI
- **Frontend:** Jinja2 server-rendered + Alpine.js, **tanpa npm dan tanpa build step**
- **Tabel:** DataGrid custom vanilla JS milik pengguna (`D:\Workspace\datagridcustom`),
  dipakai apa adanya tanpa wrapper
- **Database:** PostgreSQL
- **Plugin companion:** PHP, slug `wp-manager-connector`
- **Package Python:** `wpmgr`

Catatan: Laravel + Filament sempat direkomendasikan karena plugin companion wajib
PHP dan Laravel sudah menyediakan queue serta scheduler bawaan. Pengguna memilih
FastAPI setelah membaca trade-off tersebut. Keputusan final: FastAPI.

---

## 3. Lingkup Lapis 1

### Termasuk

- Pendaftaran (pairing) site dan pencabutannya
- Scan inventaris terjadwal: versi core, plugin, tema, dan versi yang tersedia
- Update satu item dan update massal lintas-site
- SSO ke wp-admin
- Antrean job dengan retry, recovery setelah crash, dan catatan audit
- Login dashboard untuk pengguna internal (pemilik + staf)

### Tidak termasuk (sengaja ditunda)

Backup, staging, traffic analytics, error monitoring, uptime monitoring, portal
untuk client, notifikasi email/WhatsApp, rollback update otomatis, manajemen
lisensi plugin premium.

**Satu-satunya konsesi ke masa depan:** saat pairing, plugin menyimpan
`dashboard_url` dan secret. Kedua field ini tidak dipakai di Lapis 1, tetapi
keberadaannya berarti menambahkan jalur push (Lapis 3) tidak memerlukan pairing
ulang di site yang sudah terpasang.

---

## 4. Arsitektur

```
┌─────────────────────── VPS dashboard ───────────────────────┐
│                                                              │
│  uvicorn (web)            worker (proses terpisah)           │
│  ├ halaman Jinja2         ├ ambil job: FOR UPDATE SKIP       │
│  ├ JSON API utk Alpine    │   LOCKED                         │
│  └ buat baris job → DB    └ panggil site via HTTPS+HMAC      │
│       │                            │                         │
│       └────────── PostgreSQL ──────┘                         │
│                       ▲                                      │
│  cron sistem ─────────┘  (enqueue-scans tiap jam,            │
│                           reap-jobs tiap 5 menit)            │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTPS + HMAC-SHA256
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
   site-1.com         site-2.com         site-N.com
   └ plugin wp-manager-connector (REST /wp-json/wpmgr/v1/*)
```

**Aturan pemisahan:** proses web tidak pernah melakukan panggilan HTTP ke site
client. Tugasnya hanya menulis baris job dan langsung membalas. Seluruh komunikasi
keluar adalah milik worker.

### Kenapa PostgreSQL

Dua proses menulis ke database yang sama. SQLite mengunci seluruh database saat
menulis, sehingga worker yang menyimpan hasil scan akan memblokir request web.
Lebih menentukan lagi: PostgreSQL menyediakan `SELECT ... FOR UPDATE SKIP LOCKED`,
yang menyelesaikan masalah "dua worker mengambil job yang sama" tanpa logika
penguncian buatan sendiri dan tanpa Redis.

---

## 5. Model Data

Urutan pembuatan tabel mengikuti urutan di bawah (ada foreign key maju dari `jobs`
ke `users`).

```sql
CREATE TYPE site_status AS ENUM (
  'pending_pair',      -- kunci sudah dibuat, plugin belum konfirmasi
  'active',
  'needs_reconnect',   -- tanda tangan ditolak / connector hilang
  'blocked',           -- IP dashboard diblokir firewall site
  'unreachable',       -- gagal dihubungi berulang kali
  'disabled'           -- dinonaktifkan manual
);

CREATE TYPE package_type AS ENUM ('core', 'plugin', 'theme');

CREATE TYPE job_type AS ENUM ('scan_site', 'update_package', 'verify_site');

CREATE TYPE job_status AS ENUM (
  'pending', 'running', 'success', 'failed', 'unknown'
);

CREATE TABLE users (
  id            uuid PRIMARY KEY,
  email         text NOT NULL UNIQUE,
  password_hash text NOT NULL,                  -- argon2id
  nama          text NOT NULL,
  role          text NOT NULL DEFAULT 'admin',  -- admin | staff
  dibuat_pada   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clients (
  id          uuid PRIMARY KEY,
  nama        text NOT NULL,
  kontak      text,
  catatan     text,
  dibuat_pada timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sites (
  id                 uuid PRIMARY KEY,
  client_id          uuid REFERENCES clients(id) ON DELETE SET NULL,
  nama               text NOT NULL,
  url                text NOT NULL UNIQUE,      -- wajib https://
  status             site_status NOT NULL DEFAULT 'pending_pair',
  secret_terenkripsi bytea NOT NULL,            -- Fernet; kunci dari env
  connector_version  text,
  wp_version         text,
  php_version        text,
  last_seen_at       timestamptz,
  last_scan_at       timestamptz,
  last_error         text,
  dibuat_pada        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE site_packages (
  id               bigserial PRIMARY KEY,
  site_id          uuid NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  tipe             package_type NOT NULL,
  slug             text NOT NULL,
  nama             text NOT NULL,
  versi_terpasang  text NOT NULL,
  versi_tersedia   text,                        -- NULL = sudah terbaru
  aktif            boolean NOT NULL DEFAULT true,
  auto_update      boolean NOT NULL DEFAULT false,
  last_scan_at     timestamptz NOT NULL,
  UNIQUE (site_id, tipe, slug)
);
CREATE INDEX ON site_packages (site_id);
CREATE INDEX ON site_packages (slug);
CREATE INDEX ON site_packages (versi_tersedia) WHERE versi_tersedia IS NOT NULL;

CREATE TABLE jobs (
  id             bigserial PRIMARY KEY,
  site_id        uuid NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  tipe           job_type NOT NULL,
  payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
  status         job_status NOT NULL DEFAULT 'pending',
  attempts       int NOT NULL DEFAULT 0,
  max_attempts   int NOT NULL DEFAULT 3,
  scheduled_for  timestamptz NOT NULL DEFAULT now(),
  locked_at      timestamptz,
  locked_by      text,                          -- identitas worker: hostname:pid
  started_at     timestamptz,
  finished_at    timestamptz,
  hasil          jsonb,
  error          text,
  error_class    text,                          -- lihat bagian 9
  dibuat_pada    timestamptz NOT NULL DEFAULT now(),
  dibuat_oleh    uuid REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX ON jobs (status, scheduled_for);
CREATE INDEX ON jobs (site_id, status);

CREATE TABLE activity_log (
  id          bigserial PRIMARY KEY,
  site_id     uuid REFERENCES sites(id) ON DELETE CASCADE,
  job_id      bigint REFERENCES jobs(id) ON DELETE SET NULL,
  user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
  level       text NOT NULL,                    -- info | warning | error
  pesan       text NOT NULL,
  detail      jsonb,
  dibuat_pada timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON activity_log (site_id, dibuat_pada DESC);
```

### Alasan bentuk data

**`site_packages` datar, satu baris per paket per site.** Menyimpan hasil scan
sebagai blob JSON di `sites` akan menghasilkan lebih sedikit baris, tetapi membuat
pertanyaan seperti "site mana yang masih memakai Elementor di bawah 3.20"
memerlukan pengolahan di aplikasi. Bentuk datar menjadikannya satu klausa `WHERE`,
dan memungkinkan DataGrid melakukan grouping tanpa transformasi di JavaScript.
Dengan ±1.000 baris tidak ada alasan performa untuk mendenormalisasi.

**Secret dienkripsi di kolom dengan kunci dari environment variable**
(`WPMGR_SECRET_KEY`), bukan dari database. Dump database yang bocor tidak memberi
kemampuan memerintah site client. Kunci dan ciphertext harus bisa bocor terpisah,
kalau tidak enkripsinya hanya dekorasi.

**`locked_by` menyimpan identitas worker, bukan boolean.** Job yang ditinggal mati
oleh worker dikenali sebagai `running` yang `locked_at`-nya kedaluwarsa, lalu
dikembalikan ke antrean oleh reaper (bagian 7.6).

---

## 6. Protokol Dashboard ↔ Plugin

### 6.1 Endpoint plugin

Namespace `/wp-json/wpmgr/v1/`. Semua endpoint REST menolak request yang tidak
lolos verifikasi HMAC.

| Method | Path | Body | Respons |
|---|---|---|---|
| GET | `/ping` | — | `{connector_version, wp_version, php_version, site_url}` |
| GET | `/inventory` | — | `{core: {...}, plugins: [...], themes: [...]}` |
| POST | `/update` | `{tipe, slug, ke_versi}` | `{ok, versi_sebelum, versi_sesudah, pesan}` |

Ditambah satu endpoint non-REST: `GET /?wpmgr_sso=<token>`, ditangani di hook
`init` (lihat 6.4).

Bentuk item pada `/inventory`:

```json
{
  "tipe": "plugin",
  "slug": "elementor/elementor.php",
  "nama": "Elementor",
  "versi_terpasang": "3.18.3",
  "versi_tersedia": "3.20.1",
  "aktif": true,
  "auto_update": false
}
```

`versi_tersedia` bernilai `null` bila sudah terbaru. Sebelum membaca transient
update, plugin memanggil `wp_update_plugins()`, `wp_update_themes()`, dan
`wp_version_check()` supaya datanya tidak basi.

### 6.2 Skema tanda tangan HMAC

Setiap request dari dashboard membawa empat header:

```
X-Wpmgr-Site:      <site_id uuid>
X-Wpmgr-Timestamp: <unix seconds>
X-Wpmgr-Nonce:     <32 karakter hex, 16 byte acak>
X-Wpmgr-Signature: <hex hmac_sha256(secret, canonical)>
```

Canonical string, dipisahkan newline LF (`\n`), tanpa newline di akhir:

```
METHOD \n PATH \n TIMESTAMP \n NONCE \n sha256_hex(body)
```

- `METHOD` huruf kapital (`GET`, `POST`)
- `PATH` adalah path setelah host, termasuk namespace, tanpa query string
  (contoh: `/wp-json/wpmgr/v1/inventory`)
- `sha256_hex(body)` dihitung atas body mentah; untuk body kosong, gunakan sha256
  dari string kosong

Plugin menolak request bila:

1. `X-Wpmgr-Site` tidak sama dengan `site_id` tersimpan → 401
2. `|now - timestamp| > 300` detik → 401
3. Nonce sudah ada sebagai transient → 401 (transient disimpan 600 detik)
4. Tanda tangan tidak cocok → 401

Perbandingan tanda tangan **wajib** memakai `hash_equals()`, bukan `===`.

**Site wajib HTTPS.** Dashboard menolak mendaftarkan URL berskema `http://`. Tanpa
TLS, tanda tangan tetap mencegah pemalsuan request, tetapi isi respons dan token
SSO dapat dibaca siapa pun di jalur.

### 6.3 Alur pairing

1. Dashboard: form **Tambah Site** (nama, URL, client). Sistem membuat `site_id`
   (uuid4) dan secret 32 byte acak, menyimpan site dengan status `pending_pair`,
   lalu menampilkan **kunci koneksi**:
   `base64url(site_id + ":" + secret_hex + ":" + dashboard_url)`.
2. Pengguna memasang plugin di site, membuka **Pengaturan → WP Manager**, menempel
   kunci, menyimpan.
3. Plugin menyimpan `site_id`, secret, dan `dashboard_url` di `wp_options`; membuat
   user `wpmgr` (lihat 6.4); lalu memanggil
   `POST {dashboard_url}/api/pair/confirm` dengan tanda tangan HMAC dan body
   `{connector_version, wp_version, php_version}`.
4. Dashboard memverifikasi tanda tangan, mencatat versi yang dilaporkan, lalu
   **membuat job `verify_site` dengan `scheduled_for = now()`** — bukan memanggil
   site langsung dari proses web (lihat aturan pemisahan di bagian 4). Worker
   mengambil job itu dalam hitungan detik dan memanggil `GET /ping` untuk menguji
   arah masuk. Halaman pairing mem-polling `/api/sites` sampai statusnya berubah.
   - Kedua arah berhasil → status `active`
   - Arah masuk gagal dengan 403 berpenanda firewall → status `blocked`, pesan
     "IP dashboard diblokir; tambahkan ke allowlist Wordfence/Cloudflare"
   - Arah masuk gagal dengan sebab lain → status `unreachable` dengan detail error

Inilah satu-satunya pemakaian job `verify_site` di Lapis 1.

Langkah 4 sengaja menguji arah yang berlawanan dari langkah 3. Arah masuk adalah
yang dipakai seluruh Lapis 1 dan yang paling sering diblokir; mengujinya saat
pengguna masih membuka wp-admin site tersebut berarti masalah ditemukan ketika
masih mudah diperbaiki, bukan berminggu-minggu kemudian lewat scan yang diam-diam
gagal.

**Pencabutan:** menghapus site di dashboard menghapus barisnya berikut secret-nya.
Plugin di sisi site harus dihapus manual; selama secret di dashboard sudah hilang,
tidak ada pihak yang dapat memerintah site itu.

### 6.4 User `wpmgr` dan SSO

Saat pairing, plugin membuat user WordPress `wpmgr` berperan `administrator`
dengan password acak 64 karakter yang tidak pernah dipakai dan tidak disimpan di
mana pun. Bila user sudah ada, plugin memakainya kembali.

User ini **tidak disembunyikan** dari daftar user WordPress. Menyembunyikan akun
administrator adalah pola perilaku malware dan akan merusak kepercayaan client bila
ditemukan.

Alasan memakai user khusus, bukan akun admin client: jejak di log site menjadi
jelas (perubahan berasal dari maintainer, bukan pemilik), pencabutan akses cukup
dengan menghapus satu user, dan akses tidak ikut hilang saat client menghapus akun
pribadinya.

Alur SSO saat pengguna menekan tombol **Masuk**:

```
dashboard:
  payload = {site_id, exp: now + 60, nonce: <16 byte hex>}
  body    = base64url(json(payload))
  token   = body + "." + hex(hmac_sha256(secret, body))
  redirect 302 ke https://client.com/?wpmgr_sso=<token>

plugin (hook 'init', prioritas awal):
  1. verifikasi tanda tangan dengan hash_equals()
  2. tolak bila now > exp
  3. tolak bila transient wpmgr_sso_<nonce> sudah ada
  4. set transient wpmgr_sso_<nonce> (TTL 120 detik) SEBELUM login
  5. wp_set_auth_cookie(id_user_wpmgr)
  6. wp_safe_redirect(admin_url()); exit
```

Token pasti bocor: ia tercatat di access log server dan dapat ikut terkirim pada
header `Referer`. Karena itu pertahanannya bukan kerahasiaan, melainkan umur 60
detik dan sifat sekali-pakai. Nonce ditandai terpakai **sebelum** cookie diset,
sehingga request yang gagal di tengah tidak menyisakan token yang masih dapat
dipakai ulang.

Setiap penggunaan SSO dicatat di `activity_log` beserta `user_id` pengguna
dashboard yang menekannya.

### 6.5 Eksekusi update

Job `update_package` berisi payload `{tipe, slug, dari_versi, ke_versi}`.
**Satu job = satu paket = satu request HTTP.** Tidak pernah ada batch dalam satu
panggilan.

Alasannya: request batch akan menabrak `max_execution_time` shared hosting dan mati
di tengah, meninggalkan keadaan yang tidak diketahui — paket keberapa yang sempat
ter-update tidak terjawab. Satu request per paket membuat tiap panggilan selesai
dalam hitungan detik, tiap hasil tercatat terpisah, dan kegagalan pada item ke-6
dapat dilanjutkan dari item ke-6 tanpa protokol chunking apa pun.

Implementasi di plugin memakai `Plugin_Upgrader`, `Theme_Upgrader`, atau
`Core_Upgrader` dengan `Automatic_Upgrader_Skin`, setelah
`require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php'`.

**Endpoint update wajib idempoten.** Sebelum bekerja, plugin membaca versi
terpasang; bila sudah sama dengan `ke_versi`, ia langsung membalas
`{ok: true, pesan: "sudah di versi tersebut"}` tanpa melakukan apa pun. Ini
membuat retry aman secara definisi, karena skenario "update berhasil tetapi
respons tidak sampai" pasti terjadi cepat atau lambat.

Setelah job update sukses, worker memperbarui baris `site_packages` terkait dari
nilai `versi_sesudah` pada respons.

---

## 7. Job Engine

### 7.1 Pengambilan job

```sql
SELECT * FROM jobs
WHERE status = 'pending'
  AND scheduled_for <= now()
  AND NOT EXISTS (
    SELECT 1 FROM jobs j2
    WHERE j2.site_id = jobs.site_id AND j2.status = 'running')
ORDER BY scheduled_for
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

Worker lalu menandai `status='running'`, `locked_at=now()`,
`locked_by='<hostname>:<pid>'`, `started_at=now()`, dan `attempts = attempts + 1`.

Klausa `NOT EXISTS` menegakkan aturan **maksimum satu job berjalan per site**.
Worker boleh memproses banyak site secara paralel, tetapi tidak boleh dua job pada
site yang sama. `Plugin_Upgrader` bekerja dengan mengunduh zip ke direktori
sementara, menghapus direktori plugin lama, lalu mengekstrak yang baru; dua
upgrader yang berjalan bersamaan di satu site akan saling menghapus direktori
sementara dan menghasilkan plugin yang rusak separuh. WordPress mengasumsikan hanya
satu administrator yang meng-update pada satu waktu, dan asumsi itu harus
ditegakkan dari sisi kita.

Akibatnya, delapan job update pada site A antre berurutan, sementara site B dan C
tetap berjalan paralel.

### 7.2 Timeout HTTP

| Job | Timeout |
|---|---|
| `verify_site` (ping) | 15 detik |
| `scan_site` (inventory) | 60 detik |
| `update_package` | 180 detik |

### 7.3 Retry dan backoff

Pada error yang dapat diulang: `status` kembali ke `pending` dan
`scheduled_for = now() + (2 ^ attempts) menit`. Setelah `attempts >= max_attempts`,
job ditandai `failed`.

### 7.4 Penanganan timeout: bertanya, bukan menebak

Timeout **tidak berarti gagal**; artinya *tidak diketahui*. Saat `update_package`
timeout:

1. Job ditandai `unknown`
2. Worker segera menjalankan `scan_site` untuk site tersebut
3. Bila versi paket ternyata sudah sama dengan `ke_versi` → job diubah `success`
4. Bila belum → job kembali ke `pending` untuk retry, bila jatah percobaan tersisa

Sistem yang menebak pada kasus ini akan menampilkan keadaan palsu di dashboard, dan
dashboard yang berbohong lebih buruk daripada tidak ada dashboard sama sekali.

### 7.5 Penjadwalan

Memakai cron sistem VPS, **bukan** scheduler di dalam worker:

```
0   *  * * *   python -m wpmgr.cli enqueue-scans
*/5 *  * * *   python -m wpmgr.cli reap-jobs
```

Bila scheduler hidup di dalam worker dan suatu saat dijalankan dua worker, setiap
jadwal akan tereksekusi dua kali. Cron sistem hanya ada satu instance, jadi masalah
itu tidak pernah muncul.

`enqueue-scans` membuat job `scan_site` untuk setiap site berstatus `active` yang
belum memiliki `scan_site` tertunda.

### 7.6 Reaper

`reap-jobs` mencari job berstatus `running` dengan
`locked_at < now() - interval '15 minutes'`. Job seperti itu ditinggalkan worker
yang mati. Bila masih ada jatah percobaan, job dikembalikan ke `pending`; bila
tidak, ditandai `unknown` dan dicatat di `activity_log`.

---

## 8. Antarmuka Pengguna

Server-rendered Jinja2 + Alpine.js. DataGrid custom dimuat sebagai file statis dari
`static/vendor/datagrid/`. Tidak ada npm, tidak ada bundler.

### 8.1 Layar

| Layar | Route | Isi |
|---|---|---|
| **Semua Update** | `/` | Grid: satu baris per paket yang punya update, lintas semua site. Kolom: Client, Site, Tipe, Nama, Terpasang, Tersedia, Terakhir Scan. Bulk select → tombol "Update Terpilih" |
| **Daftar Site** | `/sites` | Grid: nama, client, URL, versi WP, PHP, status koneksi, jumlah update tertunda, terakhir terlihat. Aksi per baris: **Masuk** (SSO), **Scan** |
| **Detail Site** | `/sites/{id}` | Info site + grid inventaris lengkap (termasuk yang sudah terbaru) + riwayat aktivitas |
| **Aktivitas** | `/activity` | Grid job: tipe, site, status, percobaan, error, waktu |
| **Tambah Site** | `/sites/new` | Form pairing + tampilan kunci koneksi + unduhan plugin |
| **Pengaturan** | `/settings` | Manajemen user dashboard |

### 8.2 JSON API

Enam route pertama dipanggil Alpine dari halaman dashboard dan memerlukan sesi
login. Route terakhir dipanggil plugin dari site client dan diamankan HMAC.

| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/packages` | Seluruh baris `site_packages` + nama site dan client, untuk `dataSource` grid |
| GET | `/api/sites` | Daftar site untuk grid |
| POST | `/api/jobs/update` | Body `{items: [{site_id, tipe, slug, ke_versi}]}` → membuat N job, membalas daftar id |
| POST | `/api/jobs/scan` | Body `{site_id}` → membuat job `scan_site` |
| GET | `/api/jobs/active` | Job berstatus `pending`/`running`, untuk polling progres |
| GET | `/api/sso/{site_id}` | Membalas URL redirect SSO; token dibuat pada saat itu juga |
| POST | `/api/pair/confirm` | Dipanggil plugin saat pairing; diamankan HMAC, bukan sesi login |

### 8.3 Integrasi DataGrid

Memakai API yang sudah ada tanpa memodifikasi komponen:

- **`dataSource`** diisi sekali dari `/api/packages`; ±1.000 baris seluruhnya di
  klien, tanpa paging server
- **`cellTemplate`** dengan kelas `dg-badge-*` untuk status koneksi dan penanda
  update
- **`onSelectionChanged`** menulis `keys` ke Alpine store; tombol toolbar
  menampilkan jumlah item terpilih
- **Grouping drag-to-group** memberi dua sudut pandang tanpa kode tambahan: group
  by *Site* menjawab "apa yang harus dikerjakan di site ini", group by *Nama
  paket* menjawab "plugin ini usang di berapa site"
- **Export CSV/Excel** bawaan grid dipakai untuk laporan maintenance ke client

Setelah job dibuat, Alpine mem-polling `/api/jobs/active` setiap 2 detik dan
menggambar strip progres di atas grid. Saat semua job selesai, `dataSource` dimuat
ulang dan `grid.refresh()` dipanggil.

---

## 9. Penanganan Error

Setiap kegagalan diklasifikasikan ke `jobs.error_class`. Klasifikasi menentukan
apakah retry masuk akal.

| Kondisi | `error_class` | Retry? | Tindakan |
|---|---|---|---|
| 401/403 dari endpoint plugin (tanda tangan ditolak) | `auth_error` | Tidak | Site → `needs_reconnect` |
| 404 di `/wp-json/wpmgr/v1/*` | `connector_missing` | Tidak | Site → `needs_reconnect`, pesan "plugin nonaktif atau terhapus" |
| 403 dengan penanda Cloudflare/Wordfence di body atau header | `blocked` | Tidak | Site → `blocked`, pesan berisi instruksi allowlist IP |
| Timeout | `unknown` | Lihat 7.4 | Scan ulang, biarkan kenyataan yang memutuskan |
| 5xx, koneksi ditolak, DNS gagal | `transient` | Ya | Backoff; setelah jatah habis, site → `unreachable` |
| Respons bukan JSON valid | `bad_response` | Ya, sekali | Sering disebabkan plugin lain yang mencetak output; catat 500 karakter pertama |
| Upgrader WP mengembalikan `WP_Error` | `upgrade_failed` | Tidak | Catat pesan asli WordPress apa adanya |

Mengulang `auth_error` tiga kali hanya menghasilkan tiga baris log identik dan
menunda pengguna melihat masalah yang sebenarnya.

Semua kegagalan menulis ke `activity_log` dengan level `error`, menyertakan respons
mentah yang dipotong pada 500 karakter.

---

## 10. Keamanan

1. **Tidak ada password WordPress yang pernah disimpan.** Akses berbasis HMAC dan
   cookie berumur pendek.
2. **Secret berbeda untuk setiap site.** Satu site yang jebol tidak memberi akses
   ke site lain.
3. **Secret dienkripsi di database dashboard** (Fernet), dengan kunci dari
   `WPMGR_SECRET_KEY` di environment.
4. **Secret di sisi site disimpan plaintext di `wp_options`, secara sadar.** Siapa
   pun yang dapat membaca `wp_options` sudah menguasai site itu sepenuhnya;
   mengenkripsinya hanya memindahkan kunci ke file lain di server yang sama.
   Enkripsi bernilai bila ciphertext dan kunci dapat terpisah, dan di sini tidak
   bisa.
5. **Perlindungan replay:** jendela timestamp ±300 detik ditambah nonce sekali
   pakai.
6. **Token SSO:** umur 60 detik, sekali pakai, nonce ditandai sebelum login.
7. **HTTPS wajib** untuk setiap site terdaftar.
8. **Login wajib** untuk semua route dashboard kecuali `/api/pair/confirm`, yang
   justru diamankan HMAC.
9. **Rate limit** pada `/api/pair/confirm`: 10 percobaan per menit per IP.

---

## 11. Strategi Testing

### 11.1 Test vector HMAC lintas bahasa

Satu file `tests/fixtures/hmac-test-vectors.json` berisi pasangan masukan
(`method`, `path`, `timestamp`, `nonce`, `body`, `secret`) dan tanda tangan yang
diharapkan. Test Python dan test PHP membaca **file yang sama**.

Ini menutup sumber bug paling mahal pada sistem lintas-bahasa seperti ini: kedua
sisi menyusun canonical string sedikit berbeda, dan satu-satunya gejala adalah 401
tanpa petunjuk. Dengan test vector bersama, perubahan di satu sisi langsung merah
di sisi itu, bukan muncul sebagai 401 misterius berminggu-minggu kemudian.

### 11.2 Tiga lapis test

**Unit (pytest, cepat):**
- Penyusunan dan verifikasi tanda tangan HMAC
- Pembuatan dan verifikasi token SSO, termasuk kedaluwarsa dan pemakaian ulang
- Klasifikasi error dari berbagai bentuk respons
- Enkripsi dan dekripsi secret

**Integrasi engine (pytest + PostgreSQL di Docker):**
- Dua worker tidak pernah mengambil job yang sama
- Aturan satu job per site ditegakkan
- Reaper mengembalikan job yatim
- Backoff dan batas percobaan
- Alur timeout → `unknown` → scan ulang → resolusi

**End-to-end (docker-compose, lambat):**
- 2–3 kontainer WordPress asli dengan plugin connector di-mount
- Alur: pairing → scan → pasang plugin versi lama → update lewat dashboard →
  verifikasi versi benar-benar naik
- SSO: ikuti redirect, pastikan mendarat di wp-admin dalam keadaan login
- Idempotensi: kirim job update yang sama dua kali, pastikan yang kedua no-op

WordPress tidak dapat di-mock dengan jujur untuk kasus update, karena seluruh nilai
sistem ini bergantung pada `Plugin_Upgrader` yang berperilaku benar terhadap
filesystem nyata. Test e2e lambat, tetapi hanya ia yang menjawab pertanyaan
"apakah sistem ini bekerja".

### 11.3 Test PHP untuk plugin

PHPUnit dengan WordPress test suite untuk: verifikasi HMAC, penolakan replay,
verifikasi token SSO, dan idempotensi endpoint update.

---

## 12. Deployment

Tiga unit pada VPS:

```
systemd: wpmgr-web.service      → uvicorn wpmgr.web:app
systemd: wpmgr-worker.service   → python -m wpmgr.worker
cron:    enqueue-scans (tiap jam), reap-jobs (tiap 5 menit)
```

PostgreSQL pada host yang sama. Nginx sebagai reverse proxy dengan TLS.

Environment yang wajib ada: `WPMGR_SECRET_KEY`, `DATABASE_URL`, `WPMGR_BASE_URL`.

Distribusi plugin: dashboard menyediakan `wp-manager-connector.zip` pada halaman
**Tambah Site** untuk diunduh dan dipasang lewat wp-admin.

---

## 13. Catatan Keputusan

| Keputusan | Pilihan | Alasan |
|---|---|---|
| Framework | FastAPI | Pilihan pengguna setelah membaca trade-off Laravel |
| Frontend | Jinja2 + Alpine, tanpa React | DataGrid yang dipakai adalah komponen imperatif vanilla; React menuntut wrapper dan state grid tetap berada di luar React |
| Database | PostgreSQL | Dua proses penulis; `FOR UPDATE SKIP LOCKED` menyelesaikan antrean job tanpa Redis |
| Arah komunikasi | Pull-primary | Perintah harus segera dieksekusi; WP cron hanya menyala saat ada pengunjung, dan site company profile sering sepi berjam-jam |
| Push (Lapis 3) | Kabel disiapkan, belum dipakai | `dashboard_url` + secret disimpan saat pairing; menambah push nanti tidak memerlukan pairing ulang di site yang sudah terpasang |
| Granularitas update | Satu paket per request | Menghindari `max_execution_time`; memberi pemecahan pekerjaan tanpa protokol chunking |
| User SSO | User khusus `wpmgr` | Jejak audit jelas, pencabutan mudah, tidak bergantung pada akun pribadi client |
| Antrean | Tabel di PostgreSQL | Job harus selamat dari restart; `BackgroundTasks` FastAPI hidup di memori proses web |

---

## 14. Risiko yang Diketahui

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Firewall site memblokir IP dashboard | Site tidak dapat dikelola | Dideteksi saat pairing dengan pesan spesifik; allowlist IP |
| Update merusak site client | Site rusak di produksi | **Tidak dimitigasi di Lapis 1.** Inilah alasan staging menjadi lapis berikutnya. Sementara itu: update bertahap, dan `activity_log` mencatat versi sebelum dan sesudah untuk rollback manual |
| Plugin connector dinonaktifkan client | Site hilang dari dashboard | Terdeteksi sebagai `connector_missing`, status `needs_reconnect` |
| Shared hosting terlalu lambat untuk update besar | Timeout berulang | Alur `unknown` memastikan keadaan sebenarnya diverifikasi, bukan ditebak |
| Secret dashboard bocor | Seluruh site dapat dikendalikan penyerang | Enkripsi kolom dengan kunci di environment; rotasi secret per site tersedia lewat pairing ulang |

---

## 15. Definisi Selesai untuk Lapis 1

1. Site dapat dipasangkan dari nol dalam waktu di bawah dua menit, dan kegagalan
   konektivitas dilaporkan dengan pesan yang dapat ditindaklanjuti.
2. Scan terjadwal berjalan otomatis setiap jam dan mengisi grid.
3. Update massal lintas-site dapat dijalankan dari satu layar, dengan progres yang
   terlihat dan hasil per item yang tercatat.
4. SSO membawa pengguna masuk ke wp-admin dalam satu klik.
5. Worker yang dimatikan paksa di tengah job tidak meninggalkan job hantu maupun
   keadaan palsu di dashboard.
6. Ketiga lapis test hijau, termasuk e2e terhadap WordPress asli di Docker.
