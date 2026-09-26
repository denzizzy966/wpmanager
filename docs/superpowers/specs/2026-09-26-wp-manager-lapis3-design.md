# WP Manager — PRD & Desain Lapis 3: Staging

**Tanggal:** 2026-09-26
**Status:** Menunggu tinjauan pengguna
**Lingkup:** Lapis 3 dari rencana berlapis. Dibangun di atas Lapis 1
(`2026-09-20-wp-manager-lapis1-design.md`) dan Lapis 2
(`2026-09-21-wp-manager-lapis2-design.md`), keduanya sudah di-merge ke `main`.
Backup terjadwal, notifikasi, dan portal client tetap di luar dokumen ini.

---

## 1. Tujuan

Lapis 1 menyisakan satu risiko yang sengaja tidak dimitigasi: **update bisa merusak
site client di produksi** (Lapis 1 §14). Lapis 3 memberi setiap site satu salinan
staging di VPS dashboard untuk empat keperluan:

1. **Uji update dulu.** Update plugin/tema/core dijalankan di staging, lalu dicek
   otomatis (site hidup, tidak ada error fatal baru, halaman tidak kosong) sebelum
   Anda memutuskan menjalankannya di produksi.
2. **Preview untuk client.** Client membuka `<nama>.staging.halosocia.my.id` dengan
   kata sandi untuk melihat perubahan sebelum dipublikasikan.
3. **Tempat bekerja.** Anda mengubah desain, konten, atau plugin di staging, lalu
   mendorong hasilnya ke produksi.
4. **Salinan cadangan.** Sebelum setiap dorongan ke produksi, snapshot produksi
   disimpan dan bisa dikembalikan dengan satu tombol.

Kriteria keberhasilan:

- Satu klik membuat atau menyegarkan staging dari produksi, dan menampilkan progres.
- Uji update memberi hasil **Lolos/Gagal** dengan alasan yang bisa dibaca.
- Client bisa membuka preview tanpa akun dashboard.
- Dorongan ke produksi **tidak pernah diam-diam menghapus data baru** (pesanan,
  komentar, user, isian form) dan **selalu bisa dikembalikan**.
- ERPNext dan layanan lain di VPS yang sama tidak terganggu.

---

## 2. Konteks dan Batasan

Semua batasan Lapis 1 dan 2 tetap berlaku (hosting campuran, `max_execution_time`
30–120 detik, stack FastAPI + Jinja2 + Alpine.js + DataGrid, tanpa npm/CDN).
Batasan tambahan, hasil pemeriksaan VPS pada 2026-09-26:

| Batasan | Nilai | Konsekuensi desain |
|---|---|---|
| Host staging | VPS dashboard (Contabo, Ubuntu 24.04, 6 vCPU) | Staging berjalan di Docker di VPS yang sama dengan dashboard, ERPNext, dan ~19 layanan lain. |
| RAM | 11 GB total, ±6,4 GB tersedia | Satu MariaDB bersama untuk semua staging; batas 3 staging aktif; container PHP dibatasi 384 MB; staging baru ditolak bila RAM tersedia < 2 GB; jeda otomatis setelah 3 hari tidak dibuka. |
| Disk | 193 GB, ±140 GB kosong | Tarik ditolak bila sisa disk sesudahnya < 15%. Snapshot dipangkas ke 3 terakhir per site. |
| Reverse proxy | nginx 1.24 host milik ERPNext + ~19 `sites-enabled` | Tanpa Caddy. Satu berkas nginx terpisah dipasang sekali; dashboard tidak pernah mengubah nginx host. |
| DNS | Hostinger (`dns-parking.com`); wildcard `*.staging.halosocia.my.id` → `169.58.91.181` sudah aktif (diverifikasi) | Tidak ada plugin certbot untuk DNS Hostinger → **tanpa sertifikat wildcard**. Sertifikat per staging via HTTP-01. |
| Docker | Terpasang; butuh sudo | Dashboard tidak mendapat akses Docker langsung (setara root). Satu skrip pembantu lewat sudoers. |
| Ukuran site | Campuran, ada site 5–20 GB | Transfer dipotong, dapat dilanjutkan, dan inkremental; salinan pertama site besar lama. |
| Akses ke hosting client | Hanya lewat connector (HMAC) | Tanpa SSH/SFTP/akses MySQL jarak jauh; ekspor database dengan PHP sendiri. |

---

## 3. Lingkup Lapis 3

### Termasuk

- Satu staging per site: buat, segarkan (inkremental), jeda, jalankan, hapus.
- Transfer tarik (produksi → staging) dan dorong (staging → produksi) lewat
  connector 3.0, dipotong, dengan hash per potongan, dapat dilanjutkan.
- Dorong **hanya kode** atau **timpa penuh**, dengan pengecekan tanda air data baru
  di produksi.
- Snapshot produksi sebelum setiap dorong dan tombol **Kembalikan**.
- Uji update di staging dengan penilaian otomatis sebelum/sesudah.
- Preview dengan subdomain, HTTPS, kata sandi (HTTP Basic), dan noindex.
- Penangkap email (Mailpit) dan pengaman staging (cron mati, noindex, connector
  staging tidak mengirim data monitoring).
- Tab **Staging** di detail site, chip baru di Kesehatan, entri log aktivitas.
- Skrip pembantu sudoers, konfigurasi nginx host, dan panduan pemasangan di README.

### Tidak termasuk (sengaja ditunda)

Penggabungan database per tabel; staging di hosting client; lebih dari satu staging
per site; mode sandbox otomatis untuk payment gateway/API pihak ketiga; uji visual
berbasis screenshot; staging untuk multisite; backup terjadwal ke storage luar;
notifikasi; portal client.

---

## 4. Arsitektur

```
Internet ─► nginx host (port 80/443, milik VPS)
              ├─ erp.halosocia.my.id          ─► ERPNext (tidak diubah)
              ├─ <dashboard>.halosocia.my.id  ─► wp-manager (FastAPI)
              └─ *.staging.halosocia.my.id    ─► 127.0.0.1:8090
                                                  │
  jaringan docker "wpmgr-staging" (internal) ─────┤
   ├─ wpmgr-stg-router  nginx: per Host → wp-<nama>, Basic Auth, noindex
   ├─ wpmgr-stg-db      MariaDB bersama (satu DB + user per staging)
   ├─ wpmgr-stg-mail    Mailpit (SMTP internal)
   └─ wp-<nama>         php-apache (versi PHP = produksi), 384 MB, 1 CPU

wp-manager ──sudo──► /usr/local/sbin/wpmgr-staging (skrip pembantu, satu-satunya jembatan ke Docker)
wp-manager ◄─HMAC──► connector 3.0 di hosting client (ekspor, manifest, unggah, terapkan, tanda air)
```

Prinsip:

- **Dashboard satu-satunya pengendali.** Semua pekerjaan panjang adalah job di antrean
  Lapis 1, dengan progres, log, dan pengulangan.
- **Tidak ada akses Docker langsung dari proses dashboard.** Skrip pembantu menerima
  subperintah tetap dengan argumen tervalidasi.
- **Container staging terisolasi.** Tidak bisa menjangkau PostgreSQL dashboard,
  ERPNext, atau layanan host lain; hanya internet keluar.
- **nginx host disentuh sekali saat setup.** Staging baru tidak memerlukan reload
  nginx host karena sertifikat dibaca lewat variabel `$ssl_server_name`.

---

## 5. Model Data

### 5.1 Dashboard (PostgreSQL)

**`staging`** (satu baris per site, `site_id` unik, FK `ON DELETE CASCADE`)

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | uuid PK | |
| `site_id` | uuid unik | |
| `nama` | text unik | label subdomain, `^[a-z0-9-]{1,40}$`, diturunkan dari host produksi |
| `status` | enum `StatusStaging` | `menyalin`, `siap`, `berjalan_uji`, `mendorong`, `dijeda`, `gagal` |
| `aktif` | bool | container berjalan |
| `sandi_hash` | text | bcrypt kata sandi preview |
| `versi_php` | text | versi image yang dipakai |
| `ukuran_file` | bigint | byte |
| `ukuran_db` | bigint | byte |
| `ditarik_pada` | timestamptz | tarik terakhir yang selesai |
| `tanda_air` | jsonb | tanda air produksi saat tarik terakhir (§8.2) |
| `diubah_pada` | timestamptz null | waktu terakhir staging diubah (dari connector staging) |
| `dibuka_pada` | timestamptz null | akses preview/SSO terakhir, untuk jeda otomatis |
| `sertifikat_pada` | timestamptz null | sertifikat terbit/diperpanjang |
| `galat` | text null | pesan galat terakhir |
| `dibuat_pada` | timestamptz | |

**`staging_snapshot`**

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | bigint PK | |
| `site_id` | uuid FK cascade | |
| `job_id` | bigint FK null | dorongan yang membuatnya |
| `jenis` | text | `sebelum_dorong` |
| `status` | text | `tersedia`, `dipakai`, `dipangkas` |
| `ukuran` | bigint | |
| `path` | text | relatif terhadap `WPMGR_STAGING_DIR` |
| `dibuat_pada` | timestamptz | |

**`staging_uji`**

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | bigint PK | |
| `site_id` | uuid FK cascade | |
| `job_id` | bigint FK | |
| `paket` | jsonb | `[{tipe, slug, dari, ke}]` |
| `hasil` | text | `lolos`, `gagal` |
| `pemeriksaan` | jsonb | per halaman: status, judul, ukuran sebelum/sesudah; error fatal baru |
| `dibuat_pada` | timestamptz | |

**`JobType` baru:** `staging_tarik`, `staging_uji_update`, `staging_dorong`,
`staging_kembalikan`. Progres per potongan disimpan di `job.payload` (§6.4).

### 5.2 Disk VPS

```
WPMGR_STAGING_DIR (default /var/lib/wpmgr/staging), izin 0700 milik user wpmgr
  <site_id>/
    files/            salinan root WordPress (bind mount ke wp-<nama>)
    tarik/            potongan sementara selama tarik
    dorong/           potongan sementara selama dorong
    snapshot/<id>/    file yang akan tertimpa + ekspor database produksi
/var/lib/wpmgr/certs/<nama>.staging.halosocia.my.id/{fullchain,privkey}.pem
/var/lib/wpmgr/acme/  webroot HTTP-01
```

Database staging berada di volume `wpmgr-stg-db` (MariaDB bersama), satu database dan
satu user per staging (`stg_<nama>`). Kata sandinya acak, dibuat oleh skrip pembantu,
dan hanya tersimpan di `wp-config.php` staging; dashboard tidak menyimpannya karena
impor dijalankan skrip pembantu lewat akun root MariaDB di dalam container.

### 5.3 Site klien

Tidak ada tabel baru. Connector memakai direktori sementara
`wp-content/wpmgr-dorong/<id>/` selama dorong (dibersihkan setelah selesai dan oleh
cron setelah 24 jam) dan berkas `.maintenance` WordPress saat menerapkan.

---

## 6. Protokol Dashboard ↔ Connector

### 6.1 Endpoint baru (semua HMAC, `wpmgr/v1/staging/...`)

| Endpoint | Metode | Isi |
|---|---|---|
| `/staging/manifest` | GET | daftar file `{path, ukuran, mtime, hash?}`, daftar tabel `{nama, baris, ukuran, pk}`, versi PHP, `table_prefix`, `home`/`siteurl` |
| `/staging/file` | POST | minta satu paket file atau satu rentang byte file besar; balasan biner dengan hash |
| `/staging/tabel` | POST | ekspor satu potongan tabel (2.000 baris per rentang PK, atau `LIMIT/OFFSET` bila tanpa PK) |
| `/staging/tanda-air` | GET | tanda air produksi (§8.2) |
| `/staging/snapshot` | POST | ekspor database dan paket file yang akan tertimpa, dipotong seperti tarik |
| `/staging/unggah` | POST | terima satu potongan dorongan ke area sementara, verifikasi hash |
| `/staging/terapkan` | POST | terapkan dorongan yang sudah lengkap (§6.3) |
| `/staging/bersihkan` | POST | hapus area sementara |

Semua endpoint hanya aktif bila setelan admin **"Izinkan staging"** menyala (default
mati) dan fitur `staging` diumumkan di `/ping`. Setiap request dibatasi ≤ 30 detik
dan ≤ 8 MB. Connector di site staging sendiri (konstanta `WPMGR_STAGING`) tidak
mengumumkan fitur `staging`.

### 6.2 Tarik (produksi → staging)

1. **Manifest.** Hash dihitung untuk file ≤ 50 MB; file lebih besar dibandingkan dengan
   ukuran + `mtime`. Dikecualikan: `wp-config.php`, `wp-content/cache/`,
   `wp-content/wpmgr-dorong/`, folder backup plugin backup yang umum
   (`updraft`, `ai1wm-backups`, `backups-dup-*`, `wpvividbackups`), berkas `*.log`.
2. **File.** Dashboard menghitung selisih terhadap `files/` staging dan hanya meminta
   file baru/berubah; file yang hilang di produksi dihapus di staging. Paket ±8 MB,
   file besar per rentang byte, hash per potongan, maksimal 3 kali ulang.
3. **Database.** Per tabel, per rentang PK, dalam transaksi `REPEATABLE READ` bila
   InnoDB. Hasil berupa pernyataan SQL `INSERT` yang di-escape dengan
   `$wpdb->prepare`/`esc_sql`, ditambah `CREATE TABLE` dari `SHOW CREATE TABLE`.
4. **Penyiapan.** Impor ke database staging; `wp-config.php` staging dibuat dari
   template (kredensial staging, `WPMGR_STAGING`, `DISABLE_WP_CRON`, `WP_HOME`/
   `WP_SITEURL`); `wp search-replace` URL produksi → URL staging (aman untuk data
   serialized); `blog_public=0`; mu-plugin `wpmgr-staging.php` dipasang. Tanda air
   produksi disimpan ke `staging.tanda_air`.

### 6.3 Dorong (staging → produksi)

1. Pilih **hanya kode** (`wp-content/themes`, `wp-content/plugins`,
   `wp-content/mu-plugins` kecuali mu-plugin staging, dan file uploads yang baru) atau
   **timpa penuh** (semua file kecuali `wp-config.php`, plus database dengan URL
   diganti balik).
2. Untuk timpa penuh: cek tanda air (§8.2).
3. **Snapshot** produksi (§8.3).
4. **Unggah** potongan ke `wp-content/wpmgr-dorong/<id>/`, hash tiap potongan. Situs
   belum berubah.
5. **Cek tanda air sekali lagi** tepat sebelum terapkan (timpa penuh); batal bila
   berubah.
6. **Terapkan** oleh connector: `.maintenance` (batas 15 menit) → tukar file (rename
   per direktori, atau salin+hapus) → impor database ke tabel `wpmgr_tmp_*` lalu
   `RENAME TABLE` bersama → hapus `.maintenance`. Gagal di mana pun → pulihkan dari
   snapshot lokal di area sementara, hapus `.maintenance`, balas galat.
7. Dashboard mengecek halaman utama produksi (2xx/3xx). Gagal → chip merah
   `dorong_gagal` dan tombol **Kembalikan**.

### 6.4 Progres dan melanjutkan

`job.payload` menyimpan `{tahap, file_selesai: [...], tabel: {nama: pk_terakhir},
byte_selesai, byte_total}`, ditulis setiap potongan (commit per potongan). Pengulangan
job melanjutkan dari posisi itu. Pembatalan menghentikan job di antara potongan dan
membersihkan area sementara.

---

## 7. Runtime Staging di VPS

### 7.1 Container

- `wp-<nama>`: `wordpress:php<ver>-apache` dipin ke digest, ditambah `wp-cli`, limit
  384 MB dan 1 CPU, bind mount `files/`. Versi PHP mengikuti produksi (7.4–8.3); bila
  tidak tersedia, versi terdekat yang lebih tinggi dengan peringatan.
- `wpmgr-stg-db`: MariaDB bersama, tidak membuka port ke host.
- `wpmgr-stg-router`: nginx, hanya `127.0.0.1:8090`; satu berkas konfigurasi per staging
  dari template tetap, `auth_basic` dengan `htpasswd` per staging, header
  `X-Robots-Tag: noindex, nofollow`.
- `wpmgr-stg-mail`: Mailpit; UI hanya diakses dashboard (proxy lewat route dashboard
  yang butuh login).

### 7.2 nginx host dan sertifikat

Berkas `/etc/nginx/sites-enabled/wpmgr-staging.conf` (dipasang manual sekali, contoh
di README):

- port 80: `/.well-known/acme-challenge/` → `/var/lib/wpmgr/acme/`, selain itu
  redirect ke HTTPS;
- port 443: `ssl_certificate /var/lib/wpmgr/certs/$ssl_server_name/fullchain.pem`,
  `proxy_pass http://127.0.0.1:8090`, `client_max_body_size 64M`.

Dashboard menerbitkan sertifikat per staging dengan
`certbot certonly --webroot -w /var/lib/wpmgr/acme --config-dir/--work-dir/--logs-dir`
milik wpmgr (tidak menyentuh sertifikat situs lain), lalu menyalin hasilnya ke
`/var/lib/wpmgr/certs/<host>/`. Perpanjangan lewat cron `renew-staging-certs`. Selama
sertifikat belum ada, nginx host menolak handshake untuk nama itu; UI menampilkan
"sertifikat sedang diterbitkan".

### 7.3 Skrip pembantu `/usr/local/sbin/wpmgr-staging`

Dijalankan lewat `sudo -n` dengan entri sudoers `NOPASSWD` hanya untuk skrip ini.
Subperintah:

| Subperintah | Argumen |
|---|---|
| `siapkan` | — (buat jaringan, router, db, mail bila belum ada) |
| `buat <nama> <versi_php>` | |
| `jalan <nama>` / `jeda <nama>` / `hapus <nama>` | |
| `db-buat <nama>` / `db-hapus <nama>` | kata sandi dibuat acak oleh skrip, ditulis ke `wp-config.php` staging |
| `db-impor <nama>` | SQL dari stdin |
| `wpcli <nama> <perintah>` | perintah dari daftar putih: `search-replace`, `option update`, `plugin update`, `theme update`, `core update`, `plugin list`, `core version`, `cache flush` |
| `router-muat` | tulis ulang konfigurasi router dari berkas yang disiapkan dashboard, `nginx -t`, reload |
| `sertifikat <nama>` | terbitkan/perpanjang |
| `status` | JSON: container, memori, disk |

Setiap `<nama>` wajib cocok `^[a-z0-9-]{1,40}$`, versi PHP dari daftar tetap, dan tidak
ada argumen yang diteruskan ke shell tanpa kutipan. Skrip ditulis dalam bash dengan
`set -euo pipefail`.

### 7.4 Pengaman di dalam staging

- mu-plugin `wpmgr-staging.php`: semua email ke Mailpit; spanduk admin "STAGING —
  payment gateway dan API pihak ketiga memakai kredensial produksi".
- `DISABLE_WP_CRON`, `blog_public=0`, noindex dari router.
- Connector staging: tidak mengumumkan `events`/`traffic`/`staging`, tidak memasang
  penangkap dan pencatat login; SSO dari dashboard tetap berfungsi.
- Internet keluar diizinkan (update plugin butuh wordpress.org dan server lisensi).

### 7.5 Batas sumber daya

- Paling banyak `WPMGR_STAGING_MAKS_AKTIF` (default 3) staging aktif.
- Jeda otomatis setelah `WPMGR_STAGING_JEDA_HARI` (default 3) hari tanpa akses.
- Menjalankan/membuat staging ditolak bila RAM tersedia < 2 GB (dari `status`).
- Tarik ditolak bila sisa disk sesudahnya < 15%.

---

## 8. Alur

### 8.1 Uji update

1. Dari halaman update Lapis 1 atau tab Staging: pilih paket, klik **Uji di staging
   dulu**.
2. Bila staging diubah sejak tarik terakhir (`diubah_pada > ditarik_pada`), minta
   konfirmasi karena perubahan akan tertimpa.
3. Tarik inkremental dari produksi.
4. Rekam kondisi **sebelum** untuk halaman utama, `/wp-login.php`, dan 3 path teratas
   dari traffic Lapis 2 (bila ada): status HTTP, judul `<title>`, ukuran HTML.
5. Jalankan update lewat `wpcli`.
6. Rekam kondisi **sesudah**, dan baca log PHP staging untuk error fatal baru.
7. **Lolos** bila semua halaman 2xx/3xx, tidak ada fatal baru, dan ukuran HTML tiap
   halaman tidak turun > 50%. Selain itu **Gagal** dengan alasan per pemeriksaan.
8. Simpan ke `staging_uji`; tampilkan lencana di samping tombol update produksi (tidak
   mengunci).

### 8.2 Tanda air data baru

Tanda air adalah JSON berisi, untuk setiap sumber yang ada di site:

| Sumber | Nilai |
|---|---|
| `posts` | `MAX(ID)`, `COUNT(*)`, `MAX(post_modified_gmt)` |
| `comments` | `MAX(comment_ID)`, `COUNT(*)` |
| `users` | `MAX(ID)`, `COUNT(*)` |
| pesanan WooCommerce | `wc_orders` (HPOS) atau `posts` bertipe `shop_order`: `MAX(id)`, `COUNT(*)` |
| entri form | tabel yang ada dari: Gravity Forms (`gf_entry`), WPForms (`wpforms_entries`), Fluent Forms (`fluentform_submissions`), Contact Form 7 + Flamingo (`posts` bertipe `flamingo_inbound`) |

Pembandingan menghasilkan daftar yang bisa dibaca ("3 pesanan baru, 12 komentar, 1 user,
2 post diubah"). Timpa penuh ditolak bila daftar tidak kosong, kecuali pengguna
mengetik nama site sebagai konfirmasi. Hanya kode tidak memerlukan pengecekan ini.

### 8.3 Snapshot dan Kembalikan

- Snapshot dibuat sebelum setiap dorong: ekspor database produksi + file yang akan
  tertimpa (untuk hanya kode: direktori tema/plugin/mu-plugin; untuk timpa penuh: semua
  file kecuali uploads yang tidak berubah).
- Disimpan paling banyak `WPMGR_STAGING_SNAPSHOT` (default 3) per site; yang lebih lama
  dihapus dari disk dan ditandai `dipangkas`.
- **Kembalikan** memakai jalur dorong yang sama dengan isi snapshot sebagai sumber, tanpa
  pengecekan tanda air (tujuannya memang mengembalikan kondisi lama), dengan konfirmasi
  mengetik nama site.

### 8.4 Log aktivitas

Setiap buat, segarkan, uji, dorong, kembalikan, jeda, hapus, dan buat ulang kata sandi
dicatat di `activity_log` Lapis 1: pengguna, waktu, jenis, ringkasan (paket, jenis
dorong, ringkasan tanda air, ukuran).

---

## 9. Antarmuka Pengguna

- **Tab Staging** di detail site (setelah Traffic): status dan progres, alamat preview,
  tombol salin/buat ulang kata sandi (ditampilkan sekali), ukuran, tarik terakhir, SSO
  ke admin staging, kotak email Mailpit, tombol **Buat/Segarkan**, **Jeda/Jalankan**,
  **Dorong ke produksi** (dialog pilihan hanya kode/timpa penuh + hasil tanda air),
  **Hapus**; daftar hasil uji update; daftar snapshot dengan **Kembalikan**.
- **Halaman update Lapis 1:** tombol **Uji di staging dulu** per paket/pilihan dan lencana
  hasil uji terakhir.
- **Halaman Kesehatan:** chip `staging_gagal` (tingkat 2) dan `dorong_gagal` (tingkat 1).
- **Strip progres job** Lapis 1 menampilkan persen, MB, dan perkiraan sisa waktu untuk
  job staging.
- Semua string dari site dirender ter-escape (`x-text`, autoescape Jinja); penjaga
  `test_template_aman.py` tetap berlaku.

---

## 10. Konfigurasi Baru

| Variabel | Default | Arti |
|---|---|---|
| `WPMGR_STAGING_DOMAIN` | kosong (fitur mati) | mis. `staging.halosocia.my.id` |
| `WPMGR_STAGING_DIR` | `/var/lib/wpmgr/staging` | |
| `WPMGR_STAGING_PEMBANTU` | `/usr/local/sbin/wpmgr-staging` | |
| `WPMGR_STAGING_MAKS_AKTIF` | 3 | |
| `WPMGR_STAGING_JEDA_HARI` | 3 | |
| `WPMGR_STAGING_SNAPSHOT` | 3 | |
| `WPMGR_STAGING_EMAIL_ACME` | kosong | email Let's Encrypt |

Bila `WPMGR_STAGING_DOMAIN` kosong, semua UI dan job staging disembunyikan/ditolak.

---

## 11. Job dan Cron

| Job | Isi |
|---|---|
| `staging_tarik` | buat (bila belum ada) atau segarkan staging |
| `staging_uji_update` | tarik → sebelum → update → sesudah → nilai |
| `staging_dorong` | tanda air → snapshot → unggah → tanda air lagi → terapkan → cek |
| `staging_kembalikan` | unggah isi snapshot → terapkan → cek |

Semua job staging untuk satu site saling eksklusif (paling banyak satu job staging
berjalan per site) dan dijalankan dengan `max_attempts` 3 memakai progres §6.4.

Cron baru: `staging-jeda-otomatis` (tiap jam), `renew-staging-certs` (harian),
`prune-staging` (harian: area sementara yatim, snapshot berlebih).

---

## 12. Penanganan Error

| Keadaan | Perilaku |
|---|---|
| Request ke connector gagal/timeout | ulangi potongan (3×); lalu job gagal, progres tetap, bisa diulang |
| Hash potongan tidak cocok | minta ulang (3×); lalu gagal dengan nama file/tabel |
| Disk/RAM tidak cukup | ditolak sebelum mulai dengan pesan angka yang jelas |
| Skrip pembantu gagal | galat ditampilkan, status `gagal`, tombol Hapus lalu Buat |
| Terapkan gagal di produksi | connector memulihkan dari snapshot lokal; dashboard cek halaman utama; bila tetap gagal → chip `dorong_gagal` + Kembalikan |
| `.maintenance` tertinggal | batas waktu 15 menit di berkas; WordPress mengabaikannya setelahnya |
| Sertifikat gagal terbit | staging tetap bisa dibuka lewat SSO dashboard; galat ditampilkan; dicoba ulang oleh cron |
| Tanda air berubah di tengah dorong | batal sebelum terapkan, area sementara dibersihkan |

---

## 13. Keamanan

- Endpoint connector staging: HMAC, mati secara default, bisa dimatikan admin site.
- Path: wajib di dalam root WordPress; `..`, path absolut, dan symlink ke luar ditolak di
  connector dan di dashboard; `wp-config.php` tidak pernah diekspor/ditimpa; penulisan di
  luar `wp-content` dibatasi pada file inti WordPress.
- Data ekspor (berisi data pribadi) hanya di disk VPS dengan izin 0700, tidak pernah
  dicatat ke log, dipangkas sesuai N snapshot.
- Kata sandi preview: bcrypt di DB, `htpasswd` di router, ditampilkan sekali.
- Kredensial database staging: acak per staging, hanya di `wp-config.php` staging,
  tidak disimpan dashboard dan tidak pernah dikirim ke browser.
- Proses dashboard tanpa grup `docker`; skrip pembantu dengan daftar subperintah dan
  validasi argumen ketat, diuji dengan input berbahaya.
- Container staging di jaringan internal terpisah dari dashboard dan ERPNext.
- Route UI Mailpit dan SSO staging hanya lewat dashboard yang login.

---

## 14. Strategi Testing

### 14.1 Unit (Python)
Selisih manifest, pembagian potongan, penilaian uji update, pembandingan tanda air,
validasi nama/path/versi PHP, pembangkitan konfigurasi router dan `htpasswd`, rencana
dorong (hanya kode vs timpa penuh).

### 14.2 PHPUnit (tanpa WordPress, PHP 8.3 dan 7.4)
Manifest dan pengecualian, ekspor potongan tabel dengan/tanpa PK, escaping SQL,
penolakan path berbahaya, tanda air, unggah+verifikasi hash, terapkan atomik dan
pemulihan saat gagal (direktori sementara dan `$wpdb` tiruan), setelan izin staging.

### 14.3 Integrasi (PostgreSQL)
Job tarik/uji/dorong/kembalikan dengan connector dan skrip pembantu tiruan: melanjutkan
setelah putus, penolakan timpa penuh karena data baru, pembatalan saat tanda air
berubah, pemangkasan snapshot, batas staging aktif, penolakan RAM/disk, eksklusivitas
job per site.

### 14.4 Skrip pembantu
Test shell (`bats`) dengan `docker` tiruan: argumen tidak sah ditolak, perintah yang
dijalankan persis sesuai harapan.

### 14.5 E2E (Docker lokal)
Tarik penuh dari WordPress e2e yang ada; segarkan inkremental; uji update plugin yang
lolos dan satu plugin yang sengaja fatal; dorong hanya kode; timpa penuh ditolak karena
komentar baru; kembalikan snapshot.

### 14.6 Verifikasi manual bersama pengguna di VPS
Pasang skrip pembantu dan sudoers, berkas nginx, sertifikat pertama; buat staging dari
satu site kecil; buka preview dengan kata sandi; uji update; dorong hanya kode.

---

## 15. Deployment

1. Merge, `alembic upgrade head`, build connector 3.0 (`wpmgr build-connector`), update
   connector ke site lewat dashboard.
2. Pasang skrip pembantu ke `/usr/local/sbin/wpmgr-staging` (root:root 0755) dan
   sudoers `/etc/sudoers.d/wpmgr-staging` untuk user dashboard.
3. `sudo wpmgr-staging siapkan`.
4. Pasang `/etc/nginx/sites-enabled/wpmgr-staging.conf`, `nginx -t`, reload (sekali).
5. Isi variabel §10, tambahkan cron §11.
6. Aktifkan "Izinkan staging" di admin site yang akan distaging.

---

## 16. Catatan Keputusan

| Keputusan | Alasan |
|---|---|
| Staging di VPS dashboard | Satu cara untuk semua site; tidak bergantung pada fitur hosting client. |
| Transfer lewat connector, bukan SSH | Shared hosting sering tanpa SSH/MySQL jarak jauh; tidak menyimpan kredensial server client; konsisten dengan HMAC Lapis 1–2. |
| nginx host tetap pintu depan, tanpa Caddy | Port 80/443 milik nginx ERPNext; satu berkas terpisah tidak tersentuh `bench setup nginx`. |
| Sertifikat per staging via HTTP-01 + `$ssl_server_name` | DNS Hostinger tanpa plugin certbot; tanpa reload nginx host per staging. |
| MariaDB bersama | Hemat RAM di VPS 11 GB yang juga menjalankan ERPNext. |
| Skrip pembantu sudoers | Grup `docker` setara root pada VPS yang menjalankan ERPNext. |
| Dorong pilih per push + tanda air | Timpa penuh aman untuk company profile; hanya kode aman untuk toko online; tanda air mencegah kehilangan data diam-diam. |
| Uji update tidak mengunci | Keputusan tetap di pengguna; penilaian otomatis bersifat heuristik. |

---

## 17. Risiko yang Diketahui

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Salinan pertama site 20 GB lama | Menit–jam | Progres, bisa dilanjutkan, inkremental sesudahnya |
| Tanda air tidak mengenal tabel plugin tertentu | Data plugin itu bisa tertimpa saat timpa penuh | Daftar tabel dikenal di §8.2; peringatan umum di dialog; snapshot untuk kembalikan |
| Staging memakai kredensial produksi (payment/API) | Aksi di staging bisa menyentuh layanan nyata | Spanduk peringatan; email ditangkap; cron mati |
| Heuristik uji update | Kerusakan halus tidak terdeteksi | Hasil disebut heuristik; preview manual tetap tersedia |
| RAM VPS bersama ERPNext | ERPNext melambat | Batas aktif, limit container, penolakan < 2 GB, jeda otomatis |
| Batas Let's Encrypt | 50 sertifikat/domain/minggu | Satu sertifikat per staging, jauh di bawah batas |

---

## 18. Definisi Selesai untuk Lapis 3

- Semua test unit, integrasi, PHPUnit (8.3 dan 7.4), test skrip pembantu, dan e2e lulus;
  ruff bersih.
- Review per task dan review akhir seluruh branch tanpa temuan Critical/Important yang
  terbuka.
- README berisi panduan pemasangan skrip pembantu, sudoers, nginx, sertifikat, variabel,
  dan cron.
- Verifikasi manual §14.6 dijalankan bersama pengguna di VPS.
