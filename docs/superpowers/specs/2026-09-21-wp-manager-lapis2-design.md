# WP Manager — PRD & Desain Lapis 2: Monitoring

**Tanggal:** 2026-09-21
**Status:** Disetujui untuk implementasi
**Lingkup:** Lapis 2 dari rencana berlapis. Dibangun di atas Lapis 1
(`2026-09-20-wp-manager-lapis1-design.md`), yang sudah selesai dan di-merge ke `main`.
Backup dan staging tetap di luar dokumen ini.

---

## 1. Tujuan

Lapis 1 menjawab "apa yang perlu di-update" dan "bagaimana masuk ke wp-admin".
Lapis 2 menjawab pertanyaan yang sekarang hanya terjawab ketika client menelepon:

1. **Apakah site ini hidup?** Mati, lambat, atau sertifikat SSL hampir habis.
2. **Apakah site ini rusak?** Fatal error PHP, warning, error database, dan
   terutama: **apakah update yang baru saja dijalankan merusaknya?**
3. **Apakah site ini diserang, atau sudah dibobol?** Riwayat login berhasil dan
   gagal lengkap dengan IP, negara, dan browser, serta kemunculan administrator
   baru.
4. **Berapa pengunjungnya?** Untuk laporan bulanan ke client, untuk mendeteksi
   traffic yang anjlok atau melonjak, dan untuk gambaran site mana yang ramai.

Kriteria keberhasilan: setiap pagi, satu halaman **Kesehatan** menunjukkan dalam
tiga detik site mana yang bermasalah dan apa masalahnya, dan laporan bulanan untuk
client bisa dicetak tanpa menyusun data secara manual.

---

## 2. Konteks dan Batasan

Semua batasan Lapis 1 (bagian 2) tetap berlaku: hosting campuran shared + VPS,
`max_execution_time` 30–120 detik, 10–40 site, VPS dashboard 1–2 GB, sebagian site
di balik Wordfence/Cloudflare, stack FastAPI + Jinja2 + Alpine.js + DataGrid
milik pengguna, tanpa npm dan tanpa CDN. Batasan tambahan:

| Batasan | Nilai | Konsekuensi desain |
|---|---|---|
| Notifikasi | **Tidak ada** — hanya dashboard | Halaman Kesehatan harus membuat masalah terlihat tanpa dicari, dan menyegarkan dirinya sendiri. Latensi pengambilan data 15–60 menit dapat diterima. |
| Cache halaman | Site company profile umumnya memakai LiteSpeed Cache atau sejenisnya | Kunjungan yang dilayani dari cache tidak menjalankan PHP. Penghitung traffic harus berjalan di browser. |
| Koneksi keluar dari site | Sebagian shared hosting memblokirnya | Tetap pull-primary: dashboard yang memulai setiap pertukaran data, termasuk pengiriman paket connector baru. |
| Cloudflare | Sebagian site di baliknya | IP asli pengunjung ada di header, dan header itu hanya boleh dipercaya dari jaringan Cloudflare. |
| Versi WordPress minimum connector | 5.5 | `Plugin_Upgrader::install()` dengan `overwrite_package` (bagian 11) baru tersedia sejak 5.5. |

---

## 3. Lingkup Lapis 2

### Termasuk

- **Uptime:** cek halaman depan tiap 5 menit, insiden, persentase uptime, waktu
  respons, masa berlaku SSL
- **Error PHP:** fatal, exception tak tertangkap, warning, error database; digabung
  per sidik jari; diatribusikan ke plugin/tema/core; dikaitkan dengan update
- **Riwayat login dan deteksi serangan:** login berhasil, login gagal teragregasi,
  administrator baru; status keamanan per site; grid IP penyerang lintas site
- **Traffic:** penghitung di plugin (semua site) dan Google Analytics 4 (opsional
  per site), ditampilkan berdampingan; deteksi anomali; laporan bulanan siap cetak
- **Pembaruan connector dari dashboard** (self-update), pengumuman fitur connector,
  dan unduhan zip connector untuk pemasangan manual
- **Halaman Kesehatan** sebagai halaman utama dashboard; detail site bertab
- Retensi data monitoring, retensi tabel `jobs`, dan indeks `activity_log`
  (dua item R60 Lapis 1 yang menjadi wajib karena volume job Lapis 2)

### Tidak termasuk (sengaja ditunda)

Notifikasi dalam bentuk apa pun (Telegram, email, WhatsApp); memblokir IP atau
firewall buatan sendiri (diserahkan ke Wordfence/Cloudflare/hosting); keyword
check pada isi halaman; pemantauan dari banyak lokasi; statistik negara pengunjung
traffic; parameter UTM; backup; staging; portal client. Item R60 Lapis 1 selain
yang disebut di atas tetap ditunda (halaman `/settings`, polling pairing, grid job
di `/activity`, menonaktifkan site dari UI, pencabutan sesi saat logout, visibilitas
job gagal di strip progres, batas transaksi di `proses_satu`).

---

## 4. Arsitektur

```
┌──────────────────────────── VPS dashboard ─────────────────────────────┐
│                                                                         │
│  uvicorn (web)               worker (Lapis 1, job baru)                 │
│  ├ Kesehatan, detail site    ├ collect_events   ──/events (HMAC)──┐     │
│  ├ Keamanan, laporan         ├ collect_traffic  ──/traffic (HMAC)─┤     │
│  └ JSON API                  └ update_connector ──/self-update ───┤     │
│         │                            │                             │     │
│         └──────────── PostgreSQL ────┘                             │     │
│                           ▲                                        │     │
│  cron sistem ─────────────┤                                        │     │
│   ├ check-uptime   (5 mnt) ──GET halaman depan, tanpa HMAC─────────┤     │
│   ├ enqueue-monitoring (15 mnt), enqueue-traffic (jam)             │     │
│   ├ check-ssl, collect-ga4, prune-monitoring (harian) ──► GA4 API  │     │
│   └ update-geoip (bulanan) ──► download.db-ip.com                  │     │
└────────────────────────────────────────────────────────────────────┼─────┘
                                                                     ▼
   site klien — plugin wp-manager-connector 2.x
   ├ mu-plugins/wpmgr-penangkap.php  → penangkap error, dimuat paling awal
   ├ hook wp_login / wp_login_failed / user_register / set_user_role
   ├ script beacon di footer  → POST /wp-json/wpmgr/v1/hit (publik)
   └ tabel wp_wpmgr_* (dipangkas > 30 hari)
```

**Aturan pemisahan Lapis 1 tetap berlaku:** proses web tidak pernah memanggil site
klien atau API luar.

**Dua jalur eksekusi, dipilih dengan sengaja:**

- **Lewat job engine** (`collect_events`, `collect_traffic`, `update_connector`):
  semua yang berbicara dengan connector memakai HMAC, klasifikasi error, dan
  eksklusivitas satu-job-per-site dari Lapis 1. Eksklusivitas itu justru
  diinginkan: pengambilan data tidak berjalan bersamaan dengan update di site yang
  sama.
- **Lewat perintah cron langsung** (`check-uptime`, `check-ssl`, `collect-ga4`):
  pekerjaan yang tidak menyentuh connector. Memasukkannya ke antrean hanya
  membuatnya tertahan di belakang job update yang berjalan beberapa menit, padahal
  cek uptime yang terlambat 5 menit sudah kehilangan maknanya.

---

## 5. Model Data

### 5.1 Dashboard (PostgreSQL)

```sql
ALTER TYPE job_type ADD VALUE 'collect_events';
ALTER TYPE job_type ADD VALUE 'collect_traffic';
ALTER TYPE job_type ADD VALUE 'update_connector';

CREATE TYPE uptime_status AS ENUM ('belum_dicek', 'naik', 'mati', 'terblokir');
CREATE TYPE uptime_hasil  AS ENUM ('naik', 'gagal', 'terblokir');

ALTER TABLE sites
  ADD COLUMN fitur                text[] NOT NULL DEFAULT '{}',  -- diumumkan connector (bagian 11.3)
  ADD COLUMN mode_penangkap       text,          -- 'penuh' | 'terbatas' | NULL
  ADD COLUMN percayai_xff         boolean,       -- cermin setelan di plugin (bagian 9.2)
  ADD COLUMN events_kursor        text,          -- kursor buram milik site (bagian 6.2)
  ADD COLUMN traffic_diambil_pada timestamptz,
  ADD COLUMN uptime_status        uptime_status NOT NULL DEFAULT 'belum_dicek',
  ADD COLUMN uptime_sejak         timestamptz,
  ADD COLUMN uptime_gagal_beruntun int NOT NULL DEFAULT 0,
  ADD COLUMN ssl_kedaluwarsa      timestamptz,
  ADD COLUMN ssl_dicek_pada       timestamptz,
  ADD COLUMN ssl_error            text,
  ADD COLUMN keamanan_diperiksa_pada timestamptz, -- tombol "Sudah diperiksa"
  ADD COLUMN ga4_property_id      text,
  ADD COLUMN ga4_diambil_pada     timestamptz,
  ADD COLUMN ga4_error            text;

CREATE TABLE uptime_putaran (
  id                 bigserial PRIMARY KEY,
  mulai              timestamptz NOT NULL DEFAULT now(),
  jumlah_site        int NOT NULL,
  jumlah_gagal       int NOT NULL,
  gangguan_dashboard boolean NOT NULL           -- aturan 80% (bagian 7.2)
);

CREATE TABLE uptime_checks (
  id          bigserial PRIMARY KEY,
  putaran_id  bigint NOT NULL REFERENCES uptime_putaran(id) ON DELETE CASCADE,
  site_id     uuid NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  dicek_pada  timestamptz NOT NULL DEFAULT now(),
  hasil       uptime_hasil NOT NULL,
  http_status int,
  waktu_ms    int,
  pesan       text
);
CREATE INDEX ON uptime_checks (site_id, dicek_pada DESC);

CREATE TABLE uptime_insiden (
  id          bigserial PRIMARY KEY,
  site_id     uuid NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  mulai       timestamptz NOT NULL,
  selesai     timestamptz,                      -- NULL = masih mati
  penyebab    text NOT NULL,
  http_status int
);
CREATE INDEX ON uptime_insiden (site_id, mulai DESC);
CREATE UNIQUE INDEX ON uptime_insiden (site_id) WHERE selesai IS NULL;

CREATE TABLE site_errors (
  id                    bigserial PRIMARY KEY,
  site_id               uuid NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  sidik_jari            text NOT NULL,
  tingkat               text NOT NULL,          -- fatal | warning | database
  komponen_tipe         text NOT NULL,          -- plugin | mu-plugin | theme | core | lainnya
  komponen_slug         text,                   -- nama direktori; NULL untuk core/lainnya
  pesan                 text NOT NULL,          -- contoh pesan asli
  file                  text,                   -- relatif terhadap ABSPATH
  baris                 int,
  konteks               jsonb,                  -- {path, jenis_request}
  jumlah                bigint NOT NULL,        -- hitungan di site (jendela 30 hari)
  pertama_terlihat      timestamptz NOT NULL,
  terakhir_terlihat     timestamptz NOT NULL,
  setelah_update        jsonb,                  -- {slug, versi_sebelum, versi_sesudah, job_id, waktu}
  ditandai_selesai_pada timestamptz,
  UNIQUE (site_id, sidik_jari)
);
CREATE INDEX ON site_errors (site_id, terakhir_terlihat DESC);

CREATE TABLE login_events (
  id            bigserial PRIMARY KEY,
  site_id       uuid NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  id_di_site    bigint NOT NULL,                -- untuk upsert idempoten
  waktu         timestamptz NOT NULL,
  jenis         text NOT NULL,                  -- berhasil | admin_baru | jadi_admin
  username      text NOT NULL,
  role          text,
  ip            inet,
  lewat_cloudflare boolean NOT NULL DEFAULT false,
  negara        text,                           -- ISO-3166 alpha-2, diisi dashboard
  user_agent    text,
  jalur         text,                           -- form | xmlrpc | app_password | sso | rest
  UNIQUE (site_id, id_di_site)
);
CREATE INDEX ON login_events (site_id, waktu DESC);

CREATE TABLE login_gagal (
  id            bigserial PRIMARY KEY,
  site_id       uuid NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  jam           timestamptz NOT NULL,           -- awal jam, UTC
  ip            inet,                           -- NULL = baris "(IP lain)"
  username      text NOT NULL,
  jalur         text NOT NULL,
  jumlah        int NOT NULL,
  user_agent    text,                           -- contoh
  negara        text,
  UNIQUE (site_id, jam, ip, username, jalur)
);
CREATE INDEX ON login_gagal (site_id, jam DESC);
CREATE INDEX ON login_gagal (ip, jam DESC);

CREATE TABLE traffic_harian (
  site_id    uuid NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  tanggal    date NOT NULL,                     -- zona waktu site / property GA4
  sumber     text NOT NULL,                     -- plugin | ga4
  kunjungan  int NOT NULL,
  pengunjung int NOT NULL,
  PRIMARY KEY (site_id, tanggal, sumber)
);

CREATE TABLE traffic_rincian (
  site_id    uuid NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  tanggal    date NOT NULL,
  sumber     text NOT NULL,                     -- plugin | ga4
  dimensi    text NOT NULL,                     -- halaman | asal | perangkat
  kunci      text NOT NULL,                     -- path | 'pencarian:google.com' | 'mobile'
  kunjungan  int NOT NULL,
  PRIMARY KEY (site_id, tanggal, sumber, dimensi, kunci)
);

CREATE INDEX ON activity_log (site_id, dibuat_pada DESC);  -- R60 Lapis 1
```

**Retensi dashboard** (`prune-monitoring`, harian): `uptime_checks`,
`uptime_putaran`, `login_events`, dan `login_gagal` dihapus setelah 90 hari;
`site_errors` dengan `terakhir_terlihat` lebih dari 90 hari dihapus; job
`collect_events`, `collect_traffic`, `scan_site`, dan `verify_site` yang sudah
selesai lebih dari 14 hari dihapus. **Disimpan permanen:** `uptime_insiden`,
`traffic_harian`, `traffic_rincian`, job `update_package` dan `update_connector`,
serta `activity_log`, karena semuanya dibutuhkan laporan bulanan dan jejak audit.

### 5.2 Site klien (MySQL, prefix `$wpdb->prefix`)

Dibuat dan dimigrasikan lewat `dbDelta()` oleh pemeriksaan versi skema di setiap
pemuatan plugin (bagian 11.4), **bukan** oleh activation hook, karena activation
hook tidak berjalan saat plugin di-update.

| Tabel | Kunci unik | Isi |
|---|---|---|
| `wpmgr_errors` | `sidik_jari` | Satu baris per error unik: tingkat, komponen, pesan, file, baris, konteks, jumlah, pertama/terakhir, `diubah` |
| `wpmgr_logins` | `id` (auto) | Login berhasil dan kejadian administrator, satu baris per kejadian |
| `wpmgr_login_gagal` | `(jam, ip, username, jalur)` | Login gagal teragregasi per jam, dengan `jumlah`, contoh UA, `diubah` |
| `wpmgr_traffic` | `(tanggal, dimensi, kunci)` | Rangkuman harian: `dimensi` ∈ total, halaman, asal, perangkat; kolom `kunjungan`, `pengunjung` |
| `wpmgr_pengunjung` | `(tanggal, hash)` | Hash pengunjung hari ini dan kemarin saja, beserta hitungan hit-nya |

Kolom `diubah` diperbarui setiap kali baris ditulis dan menjadi dasar kursor
`/events`. Retensi di site: baris lebih tua dari 30 hari dipangkas oleh WP-Cron
harian milik plugin; `wpmgr_pengunjung` hanya menyimpan hari ini dan kemarin.

### Alasan bentuk data

**Satu tabel `traffic_rincian` untuk halaman, asal, dan perangkat**, bukan tiga
tabel. Ketiganya berbentuk sama (hitungan per kunci per hari), diambil dengan cara
yang sama, dan dilaporkan dengan cara yang sama. Tiga tabel hanya melipatgandakan
kode upsert dan pemangkasan.

**Traffic GA4 dan plugin disimpan di tabel yang sama dengan kolom `sumber`**, tidak
pernah dijumlahkan. Grafik berdampingan dan laporan bulanan cukup memfilter kolom
itu.

**`login_gagal` teragregasi per jam, bukan per percobaan.** Satu request
`xmlrpc.php` `system.multicall` bisa membawa ratusan percobaan. Menyimpan per
percobaan berarti satu serangan menghasilkan puluhan ribu baris per jam.

---

## 6. Protokol Dashboard ↔ Plugin

### 6.1 Endpoint baru dan yang berubah

Skema HMAC Lapis 1 (bagian 6.2 spec Lapis 1) berlaku untuk semua endpoint di bawah,
kecuali `/hit`.

| Method | Path | Body | Respons |
|---|---|---|---|
| GET | `/ping` *(berubah)* | — | Lapis 1 + `fitur`, `mode_penangkap`, `percayai_xff`, `versi_skema` |
| GET | `/inventory` *(berubah)* | — | Lapis 1 + `fitur` |
| GET | `/events?kursor=<k>&batas=500` | — | `{errors: [...], logins: [...], login_gagal: [...], kursor, lagi}` |
| GET | `/traffic?dari=YYYY-MM-DD` | — | `{zona_waktu, hari: [{tanggal, total, halaman, asal, perangkat}]}` |
| POST | `/self-update` | `{versi, sha256, zip_b64}` | `{ok, versi_sebelum, versi_sesudah, pesan}` |
| POST | `/hit` | `{p, r}` sebagai `text/plain` | `204`, **publik, tanpa HMAC** |

**Semua endpoint `wpmgr` mengirim header anti-cache**: `nocache_headers()`,
`Cache-Control: no-store, private`, dan `X-LiteSpeed-Cache-Control: no-cache`.
LiteSpeed Cache secara default ikut meng-cache respons REST API GET; tanpa header
ini `/events` dan `/traffic` bisa mengembalikan data basi yang tampak sah. Aturan
ini **juga diterapkan ke `/ping` dan `/inventory` Lapis 1**, yang selama ini tidak
mengirimnya.

### 6.2 Kursor `/events`

Kursor adalah string buram milik site (`<unix_detik>:<id>`), dikembalikan apa
adanya oleh dashboard pada pengambilan berikutnya. Dashboard tidak pernah
menafsirkan isinya, jadi jam site dan jam dashboard yang tidak sinkron tidak
berpengaruh.

Site mengembalikan baris dengan `diubah >= (detik_kursor - 2)`, diurutkan menurut
`(diubah, id)`, paling banyak `batas` baris per jenis. Tumpang-tindih dua detik
menutup kasus baris yang ditulis pada detik yang sama dengan pengambilan
sebelumnya; duplikat yang dihasilkannya tidak berbahaya karena **setiap upsert di
dashboard idempoten**:

| Jenis | Kunci upsert di dashboard | Perilaku |
|---|---|---|
| `errors` | `(site_id, sidik_jari)` | Semua kolom ditimpa nilai terbaru dari site, kecuali `ditandai_selesai_pada` dan `setelah_update` |
| `logins` | `(site_id, id_di_site)` | Sisipkan bila belum ada |
| `login_gagal` | `(site_id, jam, ip, username, jalur)` | `jumlah` ditimpa nilai dari site (bukan ditambah) |

Bila `lagi = true`, handler langsung meminta halaman berikutnya dalam job yang
sama, paling banyak 10 halaman per job.

### 6.3 `/traffic`

Dashboard meminta `dari = kemarin` (tanggal site) setiap jam. Site mengembalikan
rangkuman per hari; dashboard **mengganti** baris `sumber = 'plugin'` untuk
tanggal-tanggal itu (hapus lalu sisipkan dalam satu transaksi). Tanggal memakai
zona waktu site (`wp_timezone()`), karena "hari" dalam laporan ke client adalah
hari menurut client.

### 6.4 `/self-update`

Lihat bagian 11. Body berukuran sekitar 100–150 KB. Tanda tangan HMAC Lapis 1
mencakup SHA-256 body, jadi zip yang diubah di tengah jalan ditolak sebelum
connector membacanya.

### 6.5 `/hit` (publik)

Satu-satunya endpoint `wpmgr` tanpa HMAC, karena dipanggil oleh browser
pengunjung. Rinciannya di bagian 10.1. Endpoint ini tidak pernah membaca atau
mengembalikan data; akibat terburuk penyalahgunaannya adalah angka traffic yang
salah.

---

## 7. Uptime

### 7.1 Pengecekan

`python -m wpmgr.cli check-uptime`, dijalankan cron setiap 5 menit.

- Dijaga `pg_try_advisory_lock`: bila putaran sebelumnya masih berjalan, putaran
  ini keluar tanpa melakukan apa pun.
- Mengecek semua site yang statusnya bukan `disabled`, paralel dengan 10 thread.
- `GET` ke `sites.url`, timeout 15 detik, mengikuti redirect sampai 5 kali,
  verifikasi TLS aktif, `User-Agent: WPManager-Uptime/2.0`.

| Hasil | `uptime_hasil` |
|---|---|
| 2xx setelah redirect | `naik` |
| 403 atau 503 dengan penanda Cloudflare/Wordfence (memakai deteksi firewall Lapis 1) | `terblokir` |
| 4xx/5xx lain, timeout, koneksi ditolak, DNS gagal, sertifikat invalid, redirect lebih dari 5 kali | `gagal` |

Halaman "Error establishing a database connection" dan "There has been a critical
error" sudah membalas 5xx, jadi tidak perlu pencocokan isi halaman.

### 7.2 Aturan penilaian

1. **Aturan gangguan dashboard.** Setiap putaran dicatat di `uptime_putaran`. Bila
   putaran mencakup minimal 5 site dan lebih dari 80% gagal, putaran itu ditandai
   `gangguan_dashboard`. Hasil ceknya tetap disimpan, tetapi **tidak ada** penghitung
   atau status site yang berubah.
2. **Dua kegagalan berturut-turut.** `uptime_gagal_beruntun` bertambah pada setiap
   `gagal`. Site baru menjadi `mati` pada hitungan ke-2. Insiden dibuka dengan
   `mulai` = waktu kegagalan pertama dalam deret itu. Satu kegagalan tunggal
   (misalnya maintenance mode 503 selama update plugin Lapis 1) tidak menghasilkan
   insiden.
3. **Pulih.** Hasil `naik` mengembalikan penghitung ke 0. Bila site sedang `mati`,
   insidennya ditutup dan status menjadi `naik`.
4. **Terblokir.** Status menjadi `terblokir` tanpa insiden, dan penghitung gagal
   tidak berubah. Yang diblokir adalah IP dashboard, bukan pengunjung.

### 7.3 Angka turunan

- **Persentase uptime** 24 jam / 7 hari / 30 hari = `naik / (naik + gagal)`, tanpa
  menghitung putaran `gangguan_dashboard` dan hasil `terblokir`.
- **Waktu respons** = rata-rata `waktu_ms` dari cek `naik`.

### 7.4 SSL

`python -m wpmgr.cli check-ssl`, harian. Membuka koneksi TLS ke host:443 dengan
SNI, membaca `notAfter` sertifikat ke `ssl_kedaluwarsa`. Kegagalan verifikasi
dicatat di `ssl_error`. Peringatan muncul bila sisa masa berlaku kurang dari 14
hari, atau bila `ssl_error` terisi.

---

## 8. Error PHP

### 8.1 Pemasangan penangkap

Connector menulis `wp-content/mu-plugins/wpmgr-penangkap.php` saat migrasi skema
(bagian 11.4). File itu hanya berisi pemuat kecil:

1. Keluar diam-diam bila konstanta `WPMGR_DISABLE_MONITORING` bernilai true.
2. Keluar diam-diam bila `wp-manager-connector/wp-manager-connector.php` tidak ada
   di option `active_plugins`, atau file kelas penangkapnya sudah tidak ada. Plugin
   yang dinonaktifkan atau dihapus berarti pemantauan berhenti.
3. Memuat `includes/class-wpmgr-penangkap.php` dari direktori plugin dan
   memanggil `WPMGR_Penangkap::pasang()`.

Must-use plugin dimuat sebelum plugin biasa, jadi fatal error saat plugin lain
dimuat pun tertangkap. **Bila direktori `mu-plugins` tidak dapat ditulisi**, plugin
utama memanggil `WPMGR_Penangkap::pasang()` sendiri dan melaporkan
`mode_penangkap = 'terbatas'` lewat `/ping`. Pada mode ini fatal error dari plugin
yang dimuat sebelum connector tidak tertangkap. Uninstall menghapus file mu-plugin.

### 8.2 Yang ditangkap

| Sumber | Mekanisme | `tingkat` |
|---|---|---|
| `E_ERROR`, `E_PARSE`, `E_CORE_ERROR`, `E_COMPILE_ERROR`, `E_USER_ERROR`, exception tak tertangkap | `register_shutdown_function` + `error_get_last()` | `fatal` |
| `E_WARNING`, `E_USER_WARNING`, `E_CORE_WARNING`, `E_COMPILE_WARNING` | `set_error_handler`, **dirantai** | `warning` |
| `$wpdb->last_error` tidak kosong di akhir request | fungsi shutdown | `database` |

Notice, deprecation, dan strict **tidak** ditangkap.

**Merantai error handler:** handler sebelumnya (bila ada) disimpan dan dipanggil
dengan argumen yang sama, dan nilai kembaliannya diteruskan. Bila tidak ada handler
sebelumnya, penangkap mengembalikan `false` sehingga penanganan standar PHP
(log, tampilan) tetap berjalan. Perilaku PHP di site tidak berubah sama sekali.

### 8.3 Prinsip: penangkap tidak boleh merusak site

- Selama request, kejadian hanya ditampung di array statis. Penulisan ke database
  terjadi **sekali** di fungsi shutdown, dengan satu `INSERT ... ON DUPLICATE KEY
  UPDATE`.
- Seluruh kode penangkap dibungkus `try/catch (\Throwable)` dan tidak pernah
  melempar.
- Bila database tidak tersedia, penangkap diam; kasus ini sudah tertangkap uptime
  sebagai 5xx.
- Batas keras: maksimal 20 sidik jari baru per request dan 500 baris per site.
  Setelah 500 baris, sidik jari baru diabaikan sampai pemangkasan harian memberi
  ruang (baris dengan `terakhir_terlihat` paling lama dibuang lebih dulu).

### 8.4 Sidik jari dan atribusi

- **Sidik jari** = `md5(tingkat | file_relatif | baris | pesan_ternormalisasi)`.
  Normalisasi pesan: setiap deret digit diganti `N`, dan path absolut dipangkas
  menjadi relatif terhadap `ABSPATH`. "Allowed memory size of 268435456 bytes
  exhausted (tried to allocate 20480 bytes)" dan "…40960 bytes" menjadi satu error.
- **Konteks** = path URL tanpa query string (query string bisa memuat token) dan
  jenis request: `depan`, `admin`, `ajax`, `cron`, `rest`, `cli`.
- **Atribusi** dari path file relatif terhadap `WP_CONTENT_DIR`:
  - `plugins/<dir>/…` → `plugin`, slug `<dir>`
  - `plugins/<file>.php` (plugin satu file) → `plugin`, slug `<file>.php`
  - `mu-plugins/…` → `mu-plugin`
  - `themes/<dir>/…` → `theme`, slug `<dir>`
  - `ABSPATH/wp-includes/…` atau `wp-admin/…` → `core`
  - selain itu → `lainnya`

  Dashboard mencocokkan slug direktori dengan `site_packages.slug` Lapis 1:
  plugin `<dir>` cocok dengan slug yang diawali `<dir>/`, dan tema cocok dengan slug
  yang sama persis.

### 8.5 Dikaitkan dengan update

Saat dashboard menyisipkan sidik jari **baru**, ia mencari job `update_package`
sukses untuk site yang sama, untuk paket yang cocok dengan komponen error, yang
selesai dalam 60 menit **sebelum** `pertama_terlihat`. Bila ada, `setelah_update`
diisi `{slug, versi_sebelum, versi_sesudah, job_id, waktu}`, dan UI menampilkan
"muncul setelah update ke v…". Kaitan ini hanya dihitung sekali, saat penyisipan.

### 8.6 Status turunan di dashboard

| Status | Kondisi (dievaluasi berurutan) |
|---|---|
| **Selesai** | `ditandai_selesai_pada IS NOT NULL` dan `terakhir_terlihat <= ditandai_selesai_pada` |
| **Baru** | `pertama_terlihat` dalam 24 jam terakhir |
| **Masih terjadi** | `terakhir_terlihat` dalam 24 jam terakhir |
| **Berhenti** | selain itu |

Error yang sudah ditandai selesai lalu muncul lagi otomatis kembali ke **Masih
terjadi**, karena `terakhir_terlihat` melewati `ditandai_selesai_pada`. Hanya
tingkat `fatal` dan `database` berstatus Baru/Masih terjadi yang menyalakan chip
"error baru" di halaman Kesehatan. Warning hanya terlihat di tab Error.

---

## 9. Riwayat Login dan Deteksi Serangan

### 9.1 Yang dicatat plugin

| Kejadian | Hook | Disimpan ke |
|---|---|---|
| Login berhasil | `wp_login` | `wpmgr_logins`, jenis `berhasil` |
| Login gagal | `wp_login_failed`, `application_password_failed_authentication` | `wpmgr_login_gagal` (agregat per jam) |
| User baru dengan role administrator | `user_register` | `wpmgr_logins`, jenis `admin_baru` |
| User dinaikkan menjadi administrator | `set_user_role`, `add_user_role` | `wpmgr_logins`, jenis `jadi_admin` |

**Jalur** ditentukan dari konteks request: konstanta `XMLRPC_REQUEST` →
`xmlrpc`; autentikasi application password → `app_password`; `REST_REQUEST` →
`rest`; selain itu → `form`.

Login SSO dari dashboard tidak lewat `wp_login`, karena SSO Lapis 1 memasang
cookie langsung dengan `wp_set_auth_cookie()`. Karena itu kode SSO mencatat
kejadian `berhasil` berjalur `sso` sendiri, tepat sebelum memasang cookie.

- **User `wpmgr` milik connector dikecualikan** dari `admin_baru`/`jadi_admin`.
  User itu dibuat connector sendiri saat pairing (Lapis 1 bagian 6.4); tanpa
  pengecualian ini setiap site baru langsung berstatus merah.
- Login gagal ditulis di fungsi shutdown dengan satu upsert per request, sama
  seperti penangkap error. Satu request `system.multicall` dengan 500 percobaan
  menjadi satu penulisan.
- Batas keras: 2.000 baris `wpmgr_login_gagal` per jam. Selebihnya digabung ke
  baris dengan `ip = NULL` dan username `(lainnya)`.
- Password tidak pernah dicatat dalam bentuk apa pun.
- User-agent dipotong 255 karakter; username dipotong 60 karakter.

### 9.2 Menentukan IP

1. Default: `$_SERVER['REMOTE_ADDR']`.
2. Bila `REMOTE_ADDR` berada di rentang IP Cloudflare (daftar IPv4/IPv6 dibawa
   plugin di `includes/cloudflare-ip.php` dan ikut diperbarui lewat update
   connector), pakai `CF-Connecting-IP` dan tandai `lewat_cloudflare`.
3. Bila setelan plugin **"Site ini di balik proxy/load balancer"** aktif, pakai
   entri **paling kanan** `X-Forwarded-For` yang bukan IP privat.
4. Nilai yang bukan IP valid dibuang, dan pengambilan jatuh kembali ke langkah 1.

Setelan langkah 3 disimpan di **plugin** (halaman pengaturan WP Manager di
wp-admin), karena penentuan IP terjadi di site. Dashboard hanya menampilkan
nilainya dari `/ping` (`percayai_xff`) di tab Ringkasan, dengan tautan SSO ke
halaman pengaturan itu. Ini menggantikan "saklar di dashboard" pada sesi desain:
saklar di dashboard membutuhkan endpoint konfigurasi tambahan hanya untuk satu
boolean.

### 9.3 Pengayaan di dashboard

- **Negara:** `negara` diisi saat upsert, dari database DB-IP Lite Country (format
  MMDB, dibaca dengan paket `maxminddb`). File diunduh ulang bulanan oleh
  `python -m wpmgr.cli update-geoip` ke `WPMGR_GEOIP_PATH`. Bila file tidak ada,
  kolom negara kosong dan fitur lain tetap berjalan. Lisensi CC BY 4.0 mewajibkan
  atribusi "IP geolocation by DB-IP" di halaman Keamanan.
- **User-agent** diurai oleh fungsi kecil di dashboard menjadi peramban (Chrome,
  Edge, Firefox, Safari, Opera, Samsung Internet, lainnya) dan sistem operasi
  (Windows, macOS, Android, iOS, Linux, lainnya). UA kosong atau yang cocok dengan
  `curl`, `wget`, `python-requests`, `python-urllib`, `Go-http-client`, `Java/`,
  `okhttp`, `libwww-perl`, `PostmanRuntime` ditandai **skrip**.

### 9.4 Status keamanan per site

Dievaluasi saat dibaca, dengan urutan prioritas:

| Status | Kondisi |
|---|---|
| 🔴 **Perlu diperiksa** | Ada salah satu kejadian berikut **setelah** `keamanan_diperiksa_pada` (atau sejak awal bila kosong): (a) login `berhasil` non-SSO dari IP yang punya ≥5 percobaan gagal dalam 24 jam sebelumnya; (b) login `berhasil` non-SSO oleh user yang pernah login sebelumnya, dari negara yang belum pernah tercatat untuk username itu dalam 90 hari, bila negaranya diketahui; (c) kejadian `admin_baru` atau `jadi_admin` |
| 🟡 **Diserang** | Dalam 60 menit terakhir (baris `login_gagal` dengan `jam >= now() - 1 jam`): total `jumlah` ≥ 50, atau satu IP ≥ 20 |
| 🟢 **Aman** | Selain itu |

Ambang (5, 50, 20, 60 menit, 90 hari) adalah konstanta di satu modul
(`wpmgr/keamanan.py`), bukan setelan UI.

**Tombol "Sudah diperiksa"** di tab Login mengisi `keamanan_diperiksa_pada = now()`
dan menulis `activity_log` beserta user dashboard yang menekannya. Status merah
tidak pernah padam sendiri.

**Pencatatan SSO di dashboard:** endpoint SSO Lapis 1 (`/api/sso/{site_id}`)
menulis `activity_log` level `info` berisi user dashboard yang meminta token,
sehingga setiap login berjalur `sso` di site bisa ditelusuri ke orangnya.

### 9.5 Grid IP penyerang (halaman Keamanan)

Agregat `login_gagal` 24 jam terakhir lintas site, per IP: total percobaan, jumlah
site yang diserang, negara, username yang paling sering dicoba, jalur, dan tanda
**skrip**. IP yang menyerang banyak site sekaligus hampir selalu botnet; grid ini
bisa diekspor untuk dimasukkan ke daftar blokir Wordfence atau Cloudflare.

---

## 10. Traffic

### 10.1 Penghitung di plugin

**Script** (inline, dipasang di `wp_footer`, kurang dari 1 KB, tanpa cookie,
tidak dipasang untuk user yang sedang login saat halaman dirender tanpa cache):

```js
(function(){try{var d=JSON.stringify({p:location.pathname,r:document.referrer});
navigator.sendBeacon&&navigator.sendBeacon(URL_HIT,new Blob([d],{type:'text/plain'}));}catch(e){}})();
```

`URL_HIT` diisi dari `rest_url('wpmgr/v1/hit')` saat render. `text/plain` dipakai
supaya tidak ada preflight CORS; endpoint membaca body mentah lewat
`$request->get_body()`.

**Endpoint `/hit`**, berurutan, berhenti di langkah mana pun yang menolak:

1. Tolak bila `WPMGR_DISABLE_MONITORING`.
2. Tolak bila body lebih dari 2 KB atau bukan JSON `{p, r}` berupa string.
3. Abaikan bila pengirimnya user yang sedang login: diperiksa lewat
   `wp_validate_auth_cookie( '', 'logged_in' )`, karena autentikasi cookie REST
   tanpa nonce selalu menganggap user = 0.
4. Abaikan bila user-agent kosong atau cocok dengan pola bot/headless (`bot`,
   `crawl`, `spider`, `slurp`, `HeadlessChrome`, `Lighthouse`, `PhantomJS`, dan
   sejenisnya).
5. `hash = sha1(garam_harian | ip | user_agent)`. Garam harian adalah 32 byte acak
   yang disimpan di option dan diganti setiap hari. Hash hari sebelumnya dihapus
   oleh pemangkasan, sehingga pengunjung tidak dapat dilacak lintas hari dan IP
   tidak pernah disimpan.
6. Upsert `wpmgr_pengunjung (tanggal, hash)` dengan `hit = hit + 1`. Bila baris
   baru, pengunjung ini unik untuk hari itu. Bila `hit` melewati **200**, hit
   diabaikan. Ini batas penyalahgunaan: satu klien tidak bisa menggelembungkan
   angka lebih dari 200 kunjungan per hari.
7. Satu `INSERT ... ON DUPLICATE KEY UPDATE` multi-baris ke `wpmgr_traffic` untuk
   empat kunci: `total`, `halaman:<path>`, `asal:<kategori>:<domain>`,
   `perangkat:<jenis>`. `pengunjung` bertambah 1 hanya untuk hit dari hash baru.

**Aturan kunci:**

- **Path** dipangkas 191 karakter dan dinormalkan (tanpa query string dan fragmen,
  tanpa garis miring ganda). Bila hari itu sudah ada 1.000 path berbeda, path baru
  dicatat sebagai `(lainnya)`. Jumlah path per hari di-cache di object cache atau
  transient berumur satu hari.
- **Asal:** domain perujuk sama dengan host site → tidak dicatat sebagai asal;
  kosong → `langsung`; Google/Bing/Yahoo/DuckDuckGo/Yandex/Baidu/Ecosia →
  `pencarian`; Facebook/Instagram/X/t.co/LinkedIn/TikTok/YouTube/Pinterest/
  WhatsApp/Telegram → `sosial`; selain itu → `site_lain` beserta domainnya.
- **Perangkat** dari user-agent: `tablet` (iPad, Android tanpa `Mobile`),
  `mobile`, `desktop`.

### 10.2 Google Analytics 4

- **Kredensial:** satu service account untuk semua site. Path file JSON-nya di
  `WPMGR_GA4_CREDENTIALS`; bila kosong, fitur GA4 tersembunyi seluruhnya.
- **Per site:** email service account ditambahkan sebagai **Viewer** di property
  GA4 client, lalu `ga4_property_id` diisi di tab Ringkasan (angka saja,
  divalidasi `^\d{6,12}$`).
- **Pengambilan:** `python -m wpmgr.cli collect-ga4`, harian. Mengambil 3 hari
  terakhir (GA4 memfinalkan data dalam 24–48 jam) lewat Analytics Data API
  `properties/{id}:runReport`, dengan `httpx` dan token dari `google-auth`. Tanpa
  library gRPC.
- **Pemetaan ke tabel yang sama** dengan `sumber = 'ga4'`:

  | Data GA4 | Tujuan |
  |---|---|
  | `screenPageViews`, `totalUsers` per `date` | `traffic_harian.kunjungan`, `pengunjung` |
  | `pagePath` 50 teratas per hari | `traffic_rincian` dimensi `halaman` |
  | `sessionDefaultChannelGroup`: Organic Search/Paid Search → `pencarian`; Organic Social/Paid Social → `sosial`; Direct → `langsung`; Referral → `site_lain`; lainnya → `lainnya` | dimensi `asal` |
  | `deviceCategory` | dimensi `perangkat` |

- **Kesalahan:** 403 → `ga4_error = "Service account belum ditambahkan sebagai
  Viewer di property ini"`; 429 atau kuota habis → dicoba lagi besok; lainnya →
  pesan asli. Sukses mengosongkan `ga4_error` dan mengisi `ga4_diambil_pada`.

### 10.3 Deteksi anomali

Per site, memakai sumber `plugin` (atau `ga4` bila data plugin tidak ada):
`kemarin` dibandingkan **median 14 hari sebelumnya**. Hanya dinilai bila median ≥
20 kunjungan per hari.

- `kemarin < 0,5 × median` → **Anjlok**
- `kemarin > 4 × median` → **Melonjak**

### 10.4 Tampilan dan laporan

- **Tab Traffic:** dua panel berdampingan (plugin | Google Analytics), masing-masing
  berisi grafik batang 30 hari, total kunjungan dan pengunjung, halaman teratas,
  asal, dan perangkat. Keterangan tetap di bawahnya: "Angka GA biasanya lebih kecil
  karena tidak menghitung pengunjung yang memakai ad-blocker atau menolak cookie.
  Keduanya benar menurut cara hitungnya masing-masing." Panel GA hanya tampil bila
  `ga4_property_id` terisi.
- **Laporan bulanan** `/sites/{id}/laporan/{YYYY-MM}`: halaman dengan CSS cetak,
  dicetak ke PDF lewat browser. Isi: traffic bulan itu per sumber (total, grafik
  harian, 10 halaman teratas, asal, perangkat), uptime bulan itu (persentase,
  jumlah dan durasi insiden), dan update yang dikerjakan (baris `activity_log`
  update sukses Lapis 1: paket, versi sebelum dan sesudah, tanggal).

---

## 11. Pembaruan Connector

### 11.1 Paket

`python -m wpmgr.cli build-connector` (dijalankan saat deploy, dicantumkan di
README) membuat `WPMGR_VAR_DIR/connector/wp-manager-connector.zip` dari
`connector/wp-manager-connector/`, dan menulis `manifest.json` berisi `versi` (dari
header plugin), `sha256`, `ukuran`, dan `dibangun_pada`. Direktori `tests/` dan
`vendor/` tidak ikut. Zip yang sama bisa diunduh operator dari
`/connector/unduh` (butuh login) dan ditautkan dari halaman Tambah Site.

### 11.2 Self-update

Job `update_connector` (`max_attempts = 3`) membaca zip dan manifest, lalu
memanggil `POST /self-update` dengan `{versi, sha256, zip_b64}`, timeout 180
detik. Connector:

1. Memverifikasi `sha256(base64_decode(zip_b64)) == sha256`.
2. Bila `versi` ≤ versi yang terpasang (dibandingkan dengan `version_compare`),
   membalas `ok` tanpa perubahan (idempoten).
3. Mengambil lock `wpmgr_update` Lapis 1 (R56). Bila sedang dipegang: 409
   `wpmgr_sibuk`.
4. Menulis zip ke file sementara (`wp_tempnam`), lalu memasangnya dengan
   `Plugin_Upgrader::install( $file, array( 'overwrite_package' => true ) )` dan
   `Automatic_Upgrader_Skin`. WordPress menerima paket berupa path file lokal tanpa
   mengunduh apa pun.
5. Menghapus file sementara dan melepas lock di `finally`.
6. Membaca versi baru dari header file di disk (`get_file_data`), karena konstanta
   `WPMGR_VERSION` di request ini masih milik kode lama.

**Wajib dibuktikan, bukan diasumsikan (pelajaran C1 Lapis 1):** bahwa
`install()` dengan `overwrite_package` tidak menonaktifkan plugin yang sedang
aktif. Klaim ini dicek ke source core sebelum implementasi dan dibuktikan dengan
test e2e: connector tetap aktif dan membalas `/ping` dengan versi baru setelah
self-update.

### 11.3 Pengumuman fitur

`/ping` dan `/inventory` connector 2.x melaporkan `fitur`, misalnya
`["events", "traffic", "self_update"]`. Dashboard menyimpannya ke `sites.fitur`
setiap kali `verify_site` atau `scan_site` sukses. Penjadwalan (bagian 12) hanya
membuat job untuk site yang mengumumkan fitur terkait.

Ini wajib, bukan hiasan. Connector 1.x tidak punya `/events`, dan WordPress akan
membalas 404 `rest_no_route`. Klasifikasi Lapis 1 (R53) membaca 404 tanpa kode
`wpmgr_` sebagai `connector_missing` dan akan menandai site `needs_reconnect`,
padahal connector-nya ada dan sehat. Site tanpa fitur tampil dengan chip
"connector usang", bukan status error.

### 11.4 Migrasi di sisi site

Activation hook tidak berjalan saat plugin di-update. Karena itu, pada setiap
pemuatan (`plugins_loaded`), connector membandingkan option `wpmgr_versi_skema`
dengan konstanta `WPMGR_VERSI_SKEMA`. Bila berbeda, connector menjalankan
`dbDelta()` untuk tabel-tabel `wpmgr_*`, (re)menulis file mu-plugin penangkap,
menjadwalkan WP-Cron pemangkasan harian, lalu memperbarui option-nya. Pemeriksaan
ini murah (satu `get_option` yang sudah di-autoload).

### 11.5 Transisi dari connector 1.x

Site yang sudah memasang connector 1.x membutuhkan **satu kali upload manual**
connector 2.x lewat wp-admin, karena 1.x belum punya `/self-update`. Setelah itu
semua pembaruan lewat dashboard. Aksi massal "Perbarui connector" hanya
ditawarkan untuk site yang mengumumkan fitur `self_update`.

---

## 12. Job Engine dan Penjadwalan

### 12.1 Job baru

| Job | Handler | `max_attempts` | Timeout HTTP |
|---|---|---|---|
| `collect_events` | `/events`, sampai 10 halaman; upsert (bagian 6.2); pengayaan negara; kaitan update (8.5); memperbarui `events_kursor` | 1 | 30 dtk per halaman |
| `collect_traffic` | `/traffic?dari=kemarin`; ganti baris `plugin` (6.3); memperbarui `traffic_diambil_pada` | 1 | 30 dtk |
| `update_connector` | `/self-update` (11.2); memperbarui `connector_version`; lalu membuat `verify_site` supaya `fitur` segera diperbarui | 3 | 180 dtk |

Job pengambilan berkala tidak di-retry: periode berikutnya sudah menjadi retry-nya.
Kegagalannya tetap melewati klasifikasi dan aturan status site Lapis 1 (R53–R55),
sehingga site yang benar-benar rusak tetap berpindah ke `unreachable`, dan scan per
jam Lapis 1 memulihkannya.

`update_connector` memakai klasifikasi `/update` Lapis 1, termasuk `wpmgr_sibuk` →
`transient`. Kode error baru dari connector: `wpmgr_paket_rusak` (hash tidak cocok,
400) → `bad_response`; `wpmgr_pasang_gagal` (500) → `upgrade_failed`.

### 12.2 Perintah cron baru

```
*/5 * * * *  python -m wpmgr.cli check-uptime
*/15 * * * * python -m wpmgr.cli enqueue-monitoring
5   * * * *  python -m wpmgr.cli enqueue-traffic
30  2 * * *  python -m wpmgr.cli check-ssl
45  2 * * *  python -m wpmgr.cli collect-ga4
15  3 * * *  python -m wpmgr.cli prune-monitoring
0   4 1 * *  python -m wpmgr.cli update-geoip
```

(Diawali `cd /opt/wpmgr && .venv/bin/` seperti crontab Lapis 1.)

- `enqueue-monitoring`: untuk setiap site `active` dengan `'events' = ANY(fitur)`
  yang belum punya `collect_events` berstatus `pending`/`running`, buat satu job.
- `enqueue-traffic`: sama, untuk `'traffic'` dan `collect_traffic`.
- `check-uptime`, `check-ssl`, `collect-ga4`, `prune-monitoring`, dan
  `update-geoip` tidak membuat job; semuanya bekerja langsung dan dijaga advisory
  lock masing-masing.

**Volume:** 40 site × (96 + 24) = ±4.800 job per hari. Tanpa pemangkasan, tabel
`jobs` mencapai ±1,7 juta baris per tahun. Karena itu retensi job (R60 Lapis 1)
menjadi wajib di Lapis 2 (bagian 5.1).

**Worker:** template `wpmgr-worker@.service` dari Lapis 1 memungkinkan beberapa
worker. Dua worker disarankan, supaya pengambilan berkala tidak menunda update
yang diklik operator.

---

## 13. Antarmuka Pengguna

### 13.1 Navigasi dan layar

| Layar | Route | Isi |
|---|---|---|
| **Kesehatan** | `/` | Baris chip hitungan + grid satu baris per site (bagian 13.2). Menyegarkan data tiap 60 detik selama terbuka. |
| **Update** | `/updates` | Halaman "Semua Update" Lapis 1, dipindah dari `/` |
| **Site** | `/sites` | Grid Lapis 1 + kolom Versi connector |
| **Detail site** | `/sites/{id}` | Tab: Ringkasan · Paket · Uptime · Error · Login · Traffic · Aktivitas. Tab bermasalah diberi badge angka. Tab aktif dipilih lewat `?tab=`, dan tab terakhir diingat per browser (`localStorage`, dibungkus try/catch). |
| **Keamanan** | `/keamanan` | Grid IP penyerang lintas site (bagian 9.5) + atribusi DB-IP |
| **Aktivitas** | `/activity` | Tetap seperti Lapis 1 |
| **Laporan bulanan** | `/sites/{id}/laporan/{YYYY-MM}` | Bagian 10.4 |
| **Unduh connector** | `/connector/unduh` | Zip connector terbaru |

### 13.2 Halaman Kesehatan

**Kolom grid:** Site · Uptime (status + persentase 24 jam) · Keamanan · Error
(jumlah fatal/database Baru + Masih terjadi, dan penanda "setelah update") ·
Traffic kemarin (angka + penanda Anjlok/Melonjak) · SSL (sisa hari bila < 14 atau
error) · Koneksi (status Lapis 1) · Connector (versi, penanda usang).

**Chip dan tingkat keparahannya.** Grid diurutkan menurut tingkat terparah per site,
lalu nama site:

| Tingkat | Chip |
|---|---|
| 🔴 1 | "N mati", "N perlu diperiksa" |
| 🟡 2 | "N diserang", "N error baru", "N SSL < 14 hari", "N koneksi bermasalah" (status Lapis 1 selain `active`), "N penangkap terbatas" |
| 🔵 3 | "N traffic anjlok", "N traffic melonjak", "N connector usang" |
| 🟢 4 | — (tanpa chip; site sehat) |

Mengklik chip memfilter grid ke site dengan kondisi itu; mengklik lagi melepas
filter. Mengklik baris membuka detail site pada tab yang sesuai dengan masalah
terparahnya.

### 13.3 JSON API baru

Semua memerlukan sesi login dan mengikuti pemeriksaan Origin/Referer Lapis 1 untuk
method yang mengubah data.

| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/kesehatan` | Baris grid + hitungan chip |
| GET | `/api/sites/{id}/uptime?hari=30` | Cek per jam (agregat), insiden, angka turunan, SSL |
| GET | `/api/sites/{id}/errors` | Daftar error dengan status turunan |
| POST | `/api/sites/{id}/errors/{error_id}/selesai` | Tandai selesai |
| GET | `/api/sites/{id}/logins?hari=30` | Login berhasil, kejadian admin, agregat gagal, status keamanan |
| POST | `/api/sites/{id}/keamanan/diperiksa` | Tombol "Sudah diperiksa" |
| GET | `/api/sites/{id}/traffic?hari=30` | Data dua sumber |
| PUT | `/api/sites/{id}/ga4` | `{property_id}` (kosong = hapus) |
| GET | `/api/keamanan/penyerang?jam=24` | Grid IP penyerang |
| POST | `/api/jobs/update-connector` | `{site_ids: [...]}`, semua-atau-tidak-sama-sekali seperti R46 |

### 13.4 Grafik

Grafik batang SVG sederhana yang dirender template, tanpa library grafik. Aturan
Lapis 1 tetap: tanpa npm, tanpa CDN.

---

## 14. Penanganan Error

| Kondisi | Tindakan |
|---|---|
| `/events` atau `/traffic` gagal | Klasifikasi Lapis 1; job gagal final (tanpa retry); kursor tidak maju; data diambil ulang pada periode berikutnya |
| Site tidak mengumumkan fitur | Tidak ada job; chip "connector usang" |
| Dashboard tidak mengambil data lebih dari 30 hari | Data di site yang sudah dipangkas hilang; grafik menampilkan celah, bukan nol |
| Putaran uptime > 80% gagal | Putaran ditandai `gangguan_dashboard`; tidak ada perubahan status site |
| DB-IP tidak tersedia | `negara` kosong; aturan (b) status keamanan dilewati |
| GA4 403 / kuota / lainnya | `ga4_error` dengan pesan yang bisa ditindaklanjuti; dicoba lagi besok |
| Self-update: hash tidak cocok | 400 `wpmgr_paket_rusak`, tidak ada yang dipasang |
| Self-update: pemasangan gagal | 500 `wpmgr_pasang_gagal` dengan pesan WordPress apa adanya |
| Batas keras tercapai di site (error, login, path, hit) | Kelebihan diabaikan atau digabung ke baris `(lainnya)`; tidak pernah menjadi error |

Semua kegagalan job tetap menulis `activity_log` seperti Lapis 1.

---

## 15. Keamanan

1. **Semua string dari site adalah masukan penyerang.** Username yang dicoba
   brute force, user-agent, pesan error, path halaman, domain perujuk, dan path
   GA4 dikendalikan pihak luar. Semuanya dirender lewat autoescape Jinja2 atau
   `esc()`/`textContent` di `cellTemplate` DataGrid (R47 Lapis 1), tanpa
   pengecualian. Wajib ada test yang merender username
   `<img src=x onerror=alert(1)>` dan memastikan hasilnya ter-escape.
2. **`/hit` publik** tetapi hanya-tulis, dengan batas ukuran body, batas 200 hit per
   pengunjung per hari, batas 1.000 path per hari, dan tidak pernah memantulkan
   masukan.
3. **`/self-update` dilindungi HMAC Lapis 1** ditambah verifikasi SHA-256 isi paket.
   Hanya pemegang secret site yang bisa memasang kode.
4. **Kredensial GA4** berupa file di VPS dengan izin `0600` milik user `wpmgr`,
   ditunjuk lewat env. Tidak disimpan di database dan tidak pernah dikirim ke browser.
5. **Privasi:** traffic tidak menyimpan IP atau cookie pengunjung. IP login
   disimpan 30 hari di site dan 90 hari di dashboard untuk kepentingan keamanan
   yang sah.
6. **Header IP hanya dipercaya dari sumber yang terbukti** (bagian 9.2). Tanpa
   aturan ini penyerang dapat memalsukan IP di log.
7. **Tidak ada endpoint dashboard publik baru.** Seluruh arus data tetap dimulai
   dashboard.

---

## 16. Strategi Testing

Tiga lapis Lapis 1 ditambah PHPUnit, dengan aturan yang sama: bukti RED ditempel
mentah, dan setiap klaim tentang perilaku WordPress dicek ke source core.

### 16.1 Unit (Python)

- Penilaian uptime: dua kegagalan beruntun, aturan 80%, terblokir tanpa insiden,
  penutupan insiden.
- Aturan status keamanan (a), (b), (c), pengecualian SSO dan `wpmgr`, efek tombol
  "Sudah diperiksa".
- Deteksi anomali (median, ambang, basis < 20).
- Penguraian user-agent dan penanda skrip.
- Status turunan error, termasuk terbuka kembali setelah ditandai selesai.
- Pencocokan komponen error dengan `site_packages`.
- Pemetaan channel GA4 dan penanganan 403/429 dengan `httpx.MockTransport`.

### 16.2 PHPUnit (tanpa WordPress)

- Error handler berantai: handler sebelumnya tetap dipanggil dan nilainya
  diteruskan; `false` bila tidak ada handler sebelumnya.
- Normalisasi pesan dan sidik jari; atribusi komponen dari path.
- Penentuan IP: `CF-Connecting-IP` palsu dari luar rentang Cloudflare diabaikan;
  IPv6; `X-Forwarded-For` hanya bila setelan aktif; nilai bukan IP dibuang.
- Pengelompokan asal dan perangkat; normalisasi path.
- Semua batas keras.

### 16.3 Integrasi (PostgreSQL)

- Upsert `/events` idempoten: memproses payload yang sama dua kali menghasilkan
  keadaan yang sama; kursor maju.
- Penggantian traffic per tanggal; pemetaan GA4 ke tabel yang sama.
- Kaitan error dengan update (jendela 60 menit, sebelum/sesudah batas).
- `enqueue-monitoring` / `enqueue-traffic` hanya untuk site `active` dengan fitur
  yang sesuai, tanpa duplikat.
- `prune-monitoring`: yang dihapus dan yang **dipertahankan**.
- `/api/kesehatan`: urutan keparahan dan hitungan chip.
- Escape string berbahaya di setiap halaman dan respons baru.

### 16.4 E2E (WordPress asli di Docker)

- Plugin uji yang memicu fatal error → error muncul di dashboard dengan atribusi
  plugin yang benar, dan mode penangkap `penuh`.
- Login gagal lewat `wp-login.php` dan `xmlrpc.php` `system.multicall` → hitungan
  agregat benar; login berhasil tercatat.
- Pembuatan user administrator baru → status 🔴 Perlu diperiksa; user `wpmgr` tidak
  memicunya.
- Script beacon ada di HTML halaman depan; POST ke `/hit` → angka traffic masuk ke
  dashboard lewat `collect_traffic`; request dengan cookie login tidak terhitung.
- **Self-update: connector tetap aktif, membalas `/ping` dengan versi baru, dan
  tabel/mu-plugin versi baru terpasang.**
- Header anti-cache ada di respons `/ping`, `/inventory`, `/events`, `/traffic`.
- Container WordPress dihentikan → site tercatat `mati` setelah dua putaran uptime,
  dan insiden ditutup setelah container dinyalakan lagi.
- Connector lama (tanpa `fitur`) tidak mendapat job pengambilan dan tidak berubah
  status menjadi `needs_reconnect`.

### 16.5 Verifikasi manual

GA4 dengan property sungguhan tidak bisa diuji otomatis. Rencana implementasi
memuat satu langkah verifikasi manual bersama pengguna, memakai property GA4 salah
satu site client.

---

## 17. Deployment

Tambahan terhadap Lapis 1:

- **Paket Python baru:** `maxminddb`, `google-auth`, dan `requests` (transport
  yang dipakai `google-auth` untuk menukar token service account).
- **Env baru (semuanya opsional):**
  - `WPMGR_VAR_DIR` (default `./var`), untuk zip connector dan database GeoIP;
  - `WPMGR_GEOIP_PATH` (default `$WPMGR_VAR_DIR/geoip/dbip-country-lite.mmdb`);
  - `WPMGR_GA4_CREDENTIALS` (path file JSON service account).
- **Setiap deploy:** `alembic upgrade head` lalu
  `python -m wpmgr.cli build-connector`.
- **Sekali setelah deploy pertama:** `python -m wpmgr.cli update-geoip`.
- **Crontab:** tambahkan baris bagian 12.2.
- **Worker:** jalankan `wpmgr-worker@1` dan `wpmgr-worker@2`.
- **Migrasi Alembic** memakai `ALTER TYPE ... ADD VALUE` untuk `job_type`. Pada
  PostgreSQL, nilai enum baru tidak bisa dipakai di transaksi yang sama dengan
  penambahannya, jadi penambahan nilai enum diletakkan di revisi tersendiri
  (atau di blok `autocommit_block()` Alembic).

---

## 18. Catatan Keputusan

| Keputusan | Alasan |
|---|---|
| Tanpa notifikasi | Pilihan pengguna. Akibatnya halaman Kesehatan wajib menampilkan masalah tanpa dicari, dan latensi pengambilan 15–60 menit dapat diterima. |
| Pull untuk semua data, termasuk paket connector | Konsisten dengan Lapis 1; tetap berfungsi di hosting yang memblokir koneksi keluar; tidak ada endpoint dashboard publik baru. Kabel push Lapis 1 tetap tidak dipakai. |
| Uptime dan GA4 lewat cron, bukan job engine | Tidak menyentuh connector, dan tidak boleh tertahan di belakang job update di site yang sama. |
| Penghitung traffic sendiri + GA4, berdampingan | Pilihan pengguna. Tidak pernah dijumlah atau dirata-rata; perbedaan angka dijelaskan di halaman. |
| Beacon JS, bukan hitungan di PHP | Halaman yang di-cache tidak menjalankan PHP. |
| Batas penyalahgunaan `/hit` per pengunjung per hari, bukan per menit | Memakai baris `wpmgr_pengunjung` yang memang sudah ditulis, jadi tidak ada penulisan tambahan per hit. |
| Error per sidik jari | Tabel per kejadian membengkak oleh warning berulang dari plugin lama. |
| Chip "error baru" hanya untuk fatal dan database | Warning terlalu sering dan jarang berarti site rusak; tetap terlihat di tab Error. |
| Status keamanan tiga tingkat | "Diserang" adalah kondisi latar hampir semua site WordPress; sinyal "mungkin jebol" harus terpisah dan tidak padam sendiri. |
| Setelan `X-Forwarded-For` di plugin, bukan di dashboard | Penentuan IP terjadi di site; saklar di dashboard membutuhkan endpoint konfigurasi tambahan hanya untuk satu boolean. Menyimpang dari sesi desain, dicatat di sini. |
| Fitur diumumkan connector, bukan ditebak dari nomor versi | Mencegah connector lama salah diklasifikasi `connector_missing` (bagian 11.3). |
| Migrasi skema di site lewat pemeriksaan versi saat pemuatan | Activation hook tidak berjalan saat plugin di-update. |
| Tidak memblokir IP | Firewall buatan sendiri adalah proyek jauh lebih besar dan berisiko mengunci pengunjung sah; Wordfence/Cloudflare sudah melakukannya. |

---

## 19. Risiko yang Diketahui

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Beban `/hit` di shared hosting | Satu request PHP ringan per kunjungan | Dua query per hit; traffic company profile rendah; `WPMGR_DISABLE_MONITORING` sebagai saklar darurat |
| Daftar IP Cloudflare berubah | IP pengunjung lewat Cloudflare tercatat sebagai IP Cloudflare | Daftar ikut diperbarui lewat self-update connector |
| Host mengunci `mu-plugins` | Fatal error plugin yang dimuat lebih awal tidak tertangkap | Mode `terbatas` ditampilkan jelas di dashboard |
| `overwrite_package` berperilaku berbeda di versi WordPress tertentu | Connector nonaktif setelah self-update | Diverifikasi di source core dan e2e; WordPress minimum 5.5 |
| Plugin cache meng-cache respons REST | Data monitoring basi | Header anti-cache di semua endpoint `wpmgr` (bagian 6.1) |
| Angka plugin dan GA4 berbeda | Client bingung | Ditampilkan berdampingan, dengan keterangan tetap di halaman dan laporan |
| Kredensial GA4 bocor | Pihak luar bisa membaca data analytics client | Izin file `0600`, akses Viewer saja, satu service account yang bisa dicabut |
| Site mati lebih dari 30 hari | Data monitoring di site hilang | Diterima; ditampilkan sebagai celah |

---

## 20. Definisi Selesai untuk Lapis 2

1. Halaman Kesehatan menjadi halaman utama; chip dan urutan keparahan sesuai bagian
   13.2; data menyegarkan diri tiap 60 detik.
2. Site yang dimatikan tercatat `mati` setelah dua putaran, insidennya tercatat dan
   ditutup saat pulih; putaran > 80% gagal tidak mengubah status apa pun.
3. SSL yang tersisa < 14 hari atau invalid tampil sebagai peringatan.
4. Fatal error dari plugin muncul di dashboard dengan atribusi yang benar; error yang
   muncul dalam 60 menit setelah update plugin itu diberi tanda "setelah update".
5. Login berhasil, login gagal (termasuk `xmlrpc.php` multicall), dan administrator
   baru tercatat dengan IP, negara, dan browser; status keamanan tiga tingkat
   berfungsi, termasuk tombol "Sudah diperiksa".
6. Traffic plugin terkumpul di semua site ber-connector 2.x; GA4 terkumpul untuk site
   ber-Property ID; keduanya tampil berdampingan; anomali terdeteksi; laporan bulanan
   bisa dicetak.
7. Connector bisa diperbarui dari dashboard dan tetap aktif setelahnya; connector 1.x
   tidak menghasilkan kesalahan klasifikasi.
8. Semua string dari site ter-escape di setiap tampilan.
9. Retensi berjalan; tabel `jobs` tidak tumbuh tanpa batas.
10. Semua lapis test hijau, dan verifikasi GA4 manual sudah dilakukan bersama pengguna.
