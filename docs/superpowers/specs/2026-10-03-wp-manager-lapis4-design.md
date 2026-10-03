# WP Manager — PRD & Desain Lapis 4: Pindah Hosting ke VPS

**Tanggal:** 2026-10-03
**Status:** Desain disetujui pengguna (ringkasan 2026-10-03); spec ini menunggu tinjauan
**Lingkup:** Lapis 4 dari rencana berlapis. Dibangun di atas Lapis 1–3
(`2026-09-20-wp-manager-lapis1-design.md`, `2026-09-21-wp-manager-lapis2-design.md`,
`2026-09-26-wp-manager-lapis3-design.md`), ketiganya sudah di-merge ke `main`. Spec Lapis 1–3
tetap berlaku untuk semua yang tidak diubah di sini.

---

## 1. Tujuan

Tiga site company profile (`dutamakmurabadi.com`, `scaffoldingsurabayamurah.com`,
`rizkycahayaraya.com`) berada di shared hosting Hostinger hPanel tanpa SSH. Hostingnya berakhir
dalam ±7 hari. Lapis 4 memindahkan site seperti itu ke VPS dashboard (`169.58.91.181`) dan
menjalankannya di sana sebagai produksi, dengan memakai ulang mesin staging Lapis 3: tarik lewat
connector, impor MariaDB, router nginx, sertifikat per host, dan skrip pembantu root yang sudah
dikeraskan.

Kriteria keberhasilan:

- Satu klik **Pindahkan ke VPS** menyalin seluruh berkas dan database ke VPS dengan progres yang
  bisa dilanjutkan, tanpa pernah mengubah site lama.
- Hasil salinan bisa dipratinjau dengan HTTPS dan kata sandi sebelum DNS diubah.
- Dashboard menunjukkan record DNS yang harus diubah, lalu memeriksanya sendiri. Begitu DNS
  menunjuk VPS, site diaktifkan otomatis: sertifikat terbit, salinan disegarkan sekali lagi,
  semua pengaman pratinjau dicabut, dan site berstatus **Dihosting di VPS**.
- Site tidak pernah aktif tanpa HTTPS.
- Baris `Site` dan riwayatnya (Lapis 1–3) tetap sama. Pemantauan, update, dan staging Lapis 3
  terus berjalan lewat connector yang sama setelah pindah.
- Backup harian (database dan berkas) dengan retensi 7 harian dan 4 mingguan, plus langkah
  pemulihan manual yang tertulis di README.
- Sampai site lama dimatikan, rollback cukup dengan mengembalikan DNS.
- Sekitar 19 site lain dan ERPNext di VPS yang sama tidak terganggu.

---

## 2. Konteks dan Batasan

Semua batasan Lapis 1–3 tetap berlaku. Batasan tambahan:

| Batasan | Nilai | Konsekuensi desain |
|---|---|---|
| Hosting lama | Hostinger shared, tanpa SSH/MySQL jarak jauh | Data hanya lewat connector 3.0 dengan "Izinkan staging" menyala (endpoint `/staging/*` Lapis 3, hanya yang membaca). |
| Waktu | Hosting lama mati dalam ±7 hari; target implementasi ±5 hari | YAGNI: hanya alur satu arah (lama → VPS); pemulihan backup manual; tujuan backup hanya disk lokal. |
| Ukuran site | Company profile kecil, tanpa email keluar | Desain tetap memakai tarik terpotong yang bisa dilanjutkan, jadi site besar ikut tertangani. |
| VPS | Ubuntu 24.04, nginx host 1.24 (`00-default-catchall.conf`, listener IPv6, ±19 site), Docker 29, ±6,8 GB RAM tersedia, ±140 GB disk kosong | Container produksi dibatasi memori; pembuatan ditolak bila RAM < 2 GB; nginx host disentuh **per domain** lewat satu direktori include khusus dengan `nginx -t` dan rollback. |
| DNS domain klien | Bebas, **bukan** di bawah `halosocia.my.id`; tidak ada wildcard yang bisa dipakai | Sertifikat per domain lewat HTTP-01, terbit hanya setelah DNS terbukti menunjuk VPS. |
| DNS bawaan Hostinger | Zona Hostinger memuat `AAAA @` ke IPv6 Hostinger (terlihat di zona `halosocia.my.id`: `AAAA @ 2a02:4780:6:1512:0:1e2d:4bc3:3`) | AAAA lama wajib dihapus (§8.2). |
| Dashboard di VPS | Belum terpasang; dipasang bersamaan (langkah ops terpisah) | Lapis 4 mensyaratkan Lapis 3 sudah terpasang dan `wpmgr-staging siapkan` sukses. |

---

## 3. Lingkup Lapis 4

### Termasuk

- Satu "hosting VPS" per site: pindahkan (salin), salin ulang, pratinjau, tunggu DNS, aktifkan,
  dan batalkan (hanya sebelum aktif).
- Runtime produksi terpisah dari staging: jaringan `wpmgr-prod`, MariaDB `wpmgr-prod-db`, router
  `wpmgr-prod-router`, container `wpp-<nama>` per site.
- Subperintah `prod-*` baru di skrip pembantu `wpmgr-staging` (§7.3).
- Satu berkas nginx host per domain di `/etc/nginx/wpmgr-hosting/`, dirender dari template tetap.
- Pemeriksaan DNS A/AAAA (dan CAA) untuk domain dan `www` lewat resolver publik.
- Sertifikat Let's Encrypt untuk domain dan `www`, termasuk perpanjangan.
- Backup harian lokal (dump database + tar berkas), retensi 7 harian + 4 mingguan, antarmuka
  `TujuanBackup` dengan satu implementasi `lokal`, dan backup manual dari UI.
- Tab **Hosting VPS**, chip Kesehatan baru, entri log aktivitas, cron, README.

### Tidak termasuk (sengaja ditunda)

- Tombol pemulihan backup di dashboard; pemulihan dilakukan manual dari README (opsi B).
- Tujuan backup di luar VPS (S3/R2). Antarmukanya sudah ada, implementasinya belum.
- Pindah balik (VPS → hosting lain) dan pindah antar-VPS.
- Multisite, WordPress di subfolder (`home`/`siteurl` ber-path), site tanpa HTTPS di hosting lama.
- Pengiriman email lewat `mail()` PHP di VPS (image WordPress tidak punya MTA; §21).
- Melepas site yang sudah aktif dari VPS lewat UI (langkah manual di README).
- Jeda otomatis: tidak pernah berlaku untuk produksi.
- Migrasi konstanta khusus di `wp-config.php` lama, karena berkas itu tidak pernah diekspor
  (aturan Lapis 3).

---

## 4. Arsitektur

```
Internet ─► nginx host (80/443, IPv4+IPv6, milik VPS)
              ├─ sites-enabled/* (±19 site, ERPNext, 00-default-catchall)   tidak diubah
              ├─ sites-enabled/wpmgr-staging.conf  *.staging.halosocia.my.id ─► 127.0.0.1:8090 (Lapis 3)
              └─ sites-enabled/wpmgr-hosting.conf  include /etc/nginx/wpmgr-hosting/*.conf
                    └─ <domain>.conf (satu per site, ditulis `prod-domain`) ─► 127.0.0.1:8091
                                                                            │
  jaringan docker "wpmgr-prod" (br-wpmgrprod, 172.31.251.0/24) ─────────────┤
   ├─ wpmgr-prod-router  nginx: per Host → wpp-<nama>; Basic Auth + noindex hanya saat pratinjau
   ├─ wpmgr-prod-db      MariaDB khusus produksi (satu DB + user per site, volume sendiri)
   └─ wpp-<nama>         php-apache (versi PHP = hosting lama), 512 MB, 1 CPU

jaringan docker "wpmgr-staging" (Lapis 3) ── tidak bisa saling menjangkau dengan wpmgr-prod

wp-manager ──sudo──► /usr/local/sbin/wpmgr-staging   (subperintah Lapis 3 + prod-* baru)
wp-manager ──HMAC, IP dipatok──► connector di hosting LAMA   (hanya endpoint baca /staging/*)
wp-manager ──HMAC, lewat DNS──► connector di container VPS    (sesudah aktif; Lapis 1–3 seperti biasa)
```

Prinsip:

- **Dashboard tetap satu-satunya pengendali.** Semua pekerjaan panjang adalah job di antrean
  Lapis 1 dan diproses `wpmgr-worker@staging` (Koreksi #1 Lapis 3).
- **Site lama tidak pernah diubah.** Klien hosting lama hanya mengekspos metode baca connector
  (§10.1).
- **Database produksi tidak diubah untuk pengaman pratinjau.** Pengaman hanya hidup di
  `wp-config.php` (ditulis skrip pembantu), mu-plugin yang dijaga konstanta, dan konfigurasi
  router. Mencabutnya tidak butuh pengembalian nilai database (§7.6).
- **Batas satu arah dijaga dua lapis.** Sesudah site dilayani VPS, dashboard menolak setiap
  tarik (`hosting_vps.dilayani_vps_pada`), dan skrip pembantu menolak impor/hapus/tulis ulang
  config (`MODE=aktif` di state root).
- **Produksi terpisah dari staging**: jaringan, MariaDB, router, label Docker, direktori data,
  dan direktori sertifikat (§7.2).

### 4.1 Yang dipakai ulang dari Lapis 3

| Bagian | Fungsi yang dipakai | Perubahan |
|---|---|---|
| Mesin tarik | `tarik.ambil_manifest`, `tarik.Salin` (`_sinkron_berkas`), `tarik.ekspor_db`, `tarik.urai_info`, `tarik._bangun_ulang_indeks`, `tarik.kebutuhan_disk`, `rencana.selisih`/`bagi_potongan`/`cek_disk`/`cek_ram`, `indeks.Indeks` | Urutan tahap `manifest → berkas → tanda_air → db → impor → penyiapan` dipindah ke `tarik.tarik_inti(sesi, job, site, klien, tujuan, k)` dengan parameter `TujuanSalinan` (§10.2). `tarik.tarik()` staging memanggilnya dengan `TujuanStaging` dan perilakunya tidak berubah. |
| Validasi dan I/O aman | `aman.nama_dari_url`, `aman.tulis_atomik`, `aman.hapus_berkas`, `aman.baca_terbatas`, `aman.bersih_teks`, `aman.jalur_di_dalam`, `sql_impor.periksa_sql`/`sesuaikan_mariadb` | Tambah `aman.domain_sah()` (§7.3). |
| Infrastruktur job | `umum.ulangi`, `umum.detak`, `umum.detak_latar`, `umum.simpan_kemajuan`, `umum.kemajuan`, `umum.catat_aktivitas`, `umum.pesan_os`, `umum.GalatDitolakTanpaUbah`, `umum.Dibatalkan`, `umum.KlaimHilang`, `umum.GalatBerhenti`, `queue.akan_diulang`, `queue.dalam_batas_pemulihan` | `umum.titik_potongan(sesi, job, baris)` dan `umum.periksa_batal` menerima baris apa pun yang punya `id` dan `batal_diminta_pada` (`Staging` atau `HostingVps`). Query batal memakai `type(baris)`. |
| Pembungkus pembantu | `pembantu.Pembantu.jalankan` (tenggat, batas keluaran, pesan tetap), `hash_sandi`, `sandi_baru`, `_tulis_atomik` | Metode `prod_*` baru; `KODE_KELUAR` dan `PESAN_UMUM` ditambah `nginx` (10) dan `backup` (11). |
| Pemangkasan direktori | `dorong.ke_nisan`/`hapus_nisan`, `cron._kunci_site`, `cron._dir_nyata` | Dipakai untuk menghapus `HOSTING_DIR/<site_id>` saat pindah dibatalkan. |
| API/UI | pola `routes_staging` (`_kunci`, `_simpan_job`, `_pembantu_gagal`, `ringkas_kemajuan`), strip progres job | `LABEL_TAHAP` ditambah tahap hosting. |
| Skrip pembantu (bash) | `muat_konf`, `galat`, `dibatasi`, `sbg_pengguna`, `cek_mount_root`, `image` (`digest.lock`), `opsi_klien`/`sql_root`, `tulis_wp_config`, `pastikan_brnf`, pola `pasang_isolasi_antar`, pola cadangan/pulih `cmd_router_muat`, jebakan `bersihkan` | Digeneralisasi dengan parameter, tanpa mengubah perilaku staging (§7.3.1). `cmd_sertifikat` dipakai apa adanya untuk host pratinjau. |
| Mu-plugin | pola `templates/wpmgr-staging.php.tpl` (diam tanpa konstanta) | Template baru `wpmgr-pratinjau.php.tpl` (§7.6). |

Putusan Lapis 3 yang tidak berlaku: **R25** (secret connector per staging). Database yang
disalin memuat `wpmgr_secret` produksi dan dibiarkan apa adanya, karena salinan ini *akan*
menjadi produksi dan dashboard harus tetap bisa bicara dengannya lewat secret `Site` yang sama.

---

## 5. Model Data

### 5.1 Dashboard (PostgreSQL)

**`hosting_vps`** (satu baris per site, `site_id` unik, FK `ON DELETE CASCADE`)

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | uuid PK | |
| `site_id` | uuid unik | |
| `nama` | text unik | `^[a-z0-9-]{1,36}$`, dari `aman.nama_dari_url(site.url)` dipotong 36. Container `wpp-<nama>`, host pratinjau `vps-<nama>.<WPMGR_STAGING_DOMAIN>` |
| `domain` | text unik | host `site.url` tanpa `www.`, huruf kecil ASCII (punycode), lolos `aman.domain_sah` |
| `dengan_www` | bool | `www.<domain>` punya record A/CNAME saat Pindahkan, atau `home` memakai `www` |
| `status` | enum `StatusHosting` | `menyalin`, `pratinjau`, `menunggu_dns`, `mengaktifkan`, `aktif`, `gagal` |
| `gagal_asal` | text null | `salinan` (salinan VPS setengah jadi; site lama tetap produksi) atau `produksi` (sudah dilayani VPS); CHECK seperti `ck_staging_gagal_asal` |
| `ip_lama` | text | IPv4 hosting lama saat Pindahkan; semua request ke hosting lama dipatok ke IP ini (§10.1) |
| `sandi_hash` | text null | bcrypt kata sandi pratinjau (pengguna `pratinjau`), ditampilkan sekali |
| `versi_php` | text null | |
| `ukuran_file`, `ukuran_db` | bigint | byte |
| `ditarik_pada` | timestamptz null | tarik terakhir yang selesai |
| `pratinjau_sertifikat_pada` | timestamptz null | sertifikat host pratinjau terbit |
| `dns_dicek_pada` | timestamptz null | |
| `dns_hasil` | jsonb null | hasil cek terakhir (§8.3): per nama dan jenis, nilai yang terlihat, `ok`, kode pesan |
| `sertifikat_pada` | timestamptz null | sertifikat domain terbit/diperpanjang |
| `sertifikat_gagal_pada` | timestamptz null | kegagalan terbit terakhir (backoff §8.4) |
| `sertifikat_gagal_kali` | int default 0 | kegagalan beruntun; 0 saat sukses |
| `dilayani_vps_pada` | timestamptz null | **ditulis lebih dulu** sebelum `prod-aktifkan` dikirim. Selama terisi, tarik dan batalkan ditolak selamanya |
| `aktif_pada` | timestamptz null | verifikasi aktivasi sukses |
| `backup_terakhir_pada` | timestamptz null | backup sukses terakhir |
| `backup_gagal_pada` | timestamptz null | kegagalan final backup terakhir; dikosongkan saat backup sukses |
| `galat` | text null | pesan tetap terakhir |
| `batal_diminta_pada` | timestamptz null | dipakai `umum.titik_potongan` |
| `dibuat_pada` | timestamptz | |

**`hosting_backup`**

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | bigint PK | |
| `site_id` | uuid FK cascade | |
| `job_id` | bigint FK null `ON DELETE SET NULL` | |
| `tujuan` | text | `lokal` |
| `stempel` | text | `^[0-9]{8}T[0-9]{6}Z$` (UTC), unik per (`site_id`, `tujuan`) |
| `status` | text | `tersedia`, `dipangkas` |
| `manual` | bool | dari tombol "Backup sekarang" |
| `ukuran_db`, `ukuran_file` | bigint | byte terkompresi |
| `sha256_db`, `sha256_file` | text | dari keluaran skrip pembantu, `^[0-9a-f]{64}$` |
| `dibuat_pada` | timestamptz | |

Indeks `ix_hosting_backup_site_dibuat (site_id, dibuat_pada)`.

**`JobType` baru:** `pindah_tarik`, `pindah_aktifkan`, `backup_hosting`.

**Konstanta di `models.py`:**
- `JOB_HOSTING = {pindah_tarik, pindah_aktifkan, backup_hosting}`;
- `JOB_RUNTIME = JOB_STAGING | JOB_HOSTING`, dipakai worker (routing `jenis='staging'`, F26,
  pengecualian `sentuh_site`/F8, pesan tetap F12) dan reaper;
- `JOB_RUNTIME_BACA = JOB_STAGING_BACA | {pindah_tarik}`.

**Indeks unik parsial `uq_jobs_hosting_aktif`** di `jobs(site_id)` untuk tipe `JOB_HOSTING` dengan
status `pending`/`running`: paling banyak satu job hosting per site, dijaga atomik.

### 5.2 Migrasi

Mengikuti pola Lapis 3 (enum di migrasi terpisah, karena `ALTER TYPE ... ADD VALUE` tidak boleh
dipakai dalam transaksi yang sama dengan pemakaiannya):

1. `<rev>_lapis4_job_type.py`: `ALTER TYPE job_type ADD VALUE` ×3.
2. `<rev>_lapis4_hosting.py`: enum `status_hosting`, tabel `hosting_vps`, `hosting_backup`, indeks
   `uq_jobs_hosting_aktif`.

Downgrade: hapus tabel, enum, dan indeks. Nilai enum `job_type` dibiarkan, seperti Lapis 3.

### 5.3 Disk VPS

```
WPMGR_HOSTING_DIR (default /var/lib/wpmgr/hosting), 0700 milik wpmgr
  <site_id>/
    files/            root WordPress produksi (bind mount ke wpp-<nama>)
    log/              bind mount /wpmgr-log (log PHP)
    tarik/            area kerja tarik (sementara)
    indeks.jsonl      indeks lokal Lapis 3 (Koreksi #2)
  router/<nama>.htpasswd   satu baris bcrypt kiriman dashboard (hanya saat pratinjau)

/etc/wpmgr-staging/prod/            0700 root
  situs/<nama>        state root: SITE_ID, DOMAIN, WWW, PREFIX, MODE (pratinjau|aktif)
  db/<nama>           kata sandi DB site (0600)
  db-root             kata sandi root wpmgr-prod-db (0600)
  router/conf.d/, router/htpasswd/   hasil render template router (dipasang read-only)
  php.ini             batas PHP produksi (dipasang read-only)
  nginx.lock          flock untuk semua perubahan nginx host
  nginx-cadangan/     salinan berkas nginx sebelum ditimpa

/etc/nginx/wpmgr-hosting/<domain>.conf     0644 root (dirender skrip pembantu)
/var/lib/wpmgr/hosting-certs/<domain>/     0700 root; fullchain.pem 0644, privkey.pem 0600 root:root
/var/lib/wpmgr/certs/vps-<nama>.<DOMAIN>/  sertifikat host pratinjau (`cmd_sertifikat` Lapis 3)
/var/lib/wpmgr/backup/<site_id>/<stempel>/ 0700 root; db.sql.gz, files.tar.gz, manifest.json (0600)
```

Volume Docker `wpmgr-prod-db` menyimpan database produksi: database dan user `prd_<nama>`
(`-` diganti `_`), dengan hak yang sama dengan user staging (Koreksi #8 Lapis 3: DML/DDL atas
database sendiri, tanpa TRIGGER/EVENT/ROUTINE/FILE).

---

## 6. Alur End-to-End (satu site)

1. **Siapkan (manual, sekali per site).** Site lama sudah terdaftar dan terpasang connector 3.0
   lewat wp-admin, pairing sukses, dan "Izinkan staging" dinyalakan di Pengaturan → WP Manager.
2. **Pindahkan ke VPS** (tab Hosting VPS). Dashboard:
   - menurunkan `domain` dan `dengan_www` dari `site.url`;
   - me-resolve A domain lewat resolver publik untuk mengisi `ip_lama`, dan menolak bila domain
     sudah menunjuk VPS;
   - membuat baris `hosting_vps` (status `menyalin`) dan kata sandi pratinjau (ditampilkan sekali);
   - mengantrekan `pindah_tarik`.
3. **Tarik** (`pindah_tarik`, §10.3). Mesin tarik Lapis 3 menyalin semua berkas dan tabel dari
   IP lama ke `HOSTING_DIR/<site_id>`. Prosesnya bisa dilanjutkan. Container `wpp-<nama>` dibuat,
   `wp-config.php` dibuat dalam mode pratinjau, mu-plugin pratinjau dipasang, sertifikat host
   pratinjau diterbitkan, lalu berkas nginx domain ditulis dalam mode pratinjau. Status menjadi
   `pratinjau`.
4. **Pratinjau.** Pengguna membuka `https://vps-<nama>.staging.halosocia.my.id` (Basic Auth,
   noindex, email diblokir, WP-Cron mati). Cara kedua: arahkan domain asli ke IP VPS lewat berkas
   hosts; browser akan memperingatkan sertifikat (sertifikat pratinjau) dan pengguna melanjutkan.
   **Salin ulang** tersedia selama belum aktif.
5. **Lanjut ke DNS.** Pengguna menekan **Pratinjau sudah benar, lanjut ke DNS**, dan status
   menjadi `menunggu_dns`. UI menampilkan record yang harus diubah di hPanel:
   - `A @` → IPv4 VPS;
   - `A www` → IPv4 VPS (bila `dengan_www`);
   - hapus setiap `AAAA` untuk `@` dan `www`, kecuali `WPMGR_HOSTING_IPV6` diisi dan AAAA
     diarahkan ke sana (§8.2).

   Cron `hosting-cek-dns` memeriksa tiap 10 menit. Tombol **Periksa DNS & aktifkan sekarang**
   menjalankan cek yang sama segera.
6. **Aktifkan** (`pindah_aktifkan`, §10.4), diantrekan otomatis begitu cek DNS lolos:
   1. cek DNS ulang;
   2. terbitkan sertifikat domain + `www` (HTTP-01);
   3. tarik inkremental terakhir dari IP lama;
   4. **tukar**: hapus mu-plugin pratinjau, lalu `prod-aktifkan` (wp-config mode aktif, router
      tanpa Basic Auth/noindex/host pratinjau, nginx domain mode aktif dengan sertifikat asli,
      `MODE=aktif`);
   5. verifikasi HTTPS lewat nginx host;
   6. status `aktif` ("Dihosting di VPS"), backup pertama diantrekan.
7. **Selesai.** Hosting lama boleh dimatikan. Sebelum itu, rollback = kembalikan DNS ke hosting
   lama (yang tidak pernah diubah). Sesudah langkah 6.4, data baru masuk di VPS: rollback DNS
   berarti data itu tidak ikut kembali (§21).

`Site` tidak berubah: `site.url` tetap `https://<domain>`, secret tetap secret produksi.
Sebelum DNS berubah, scan/uptime/monitoring menuju hosting lama. Sesudahnya, semuanya menuju
VPS tanpa konfigurasi tambahan.

---

## 7. Runtime Produksi di VPS

### 7.1 Container

- **`wpp-<nama>`**: `wordpress:php<ver>-apache` dipin digest lewat `digest.lock` yang sama dengan
  staging. Versi PHP dari `aman.versi_php_staging` (7.4–8.3). Opsi container:
  - `--memory 512m --memory-swap 512m --cpus 1 --pids-limit 256`;
  - `--user <UID_W>:<GID_W> --cap-drop ALL --security-opt no-new-privileges`;
  - `--sysctl net.ipv4.ip_unprivileged_port_start=0`;
  - `--restart unless-stopped`, label `wpmgr.hosting=situs:<nama>`, jaringan `wpmgr-prod`.

  Mount, semuanya `--mount` (bukan `-v`):
  - `files/` → `/var/www/html`;
  - `log/` → `/wpmgr-log`;
  - `KONF_DIR/prod/php.ini` → `/usr/local/etc/php/conf.d/zz-wpmgr.ini` read-only.

  Tanpa mount ekspor dan tanpa wp-cli, karena tidak ada wp-cli di jalur produksi.
- **`php.ini`** (isi tetap, ditulis `prod-siapkan`): `upload_max_filesize=64M`,
  `post_max_size=64M`, `memory_limit=256M`, `max_execution_time=120`, `expose_php=Off`. Bawaan
  image (upload 2 MB) tidak cukup untuk produksi.
- **`wpmgr-prod-db`**: `mariadb:11.4` (digest yang sama dengan staging), `--memory 768m`,
  `--innodb-buffer-pool-size=256M --max-allowed-packet=64M --local-infile=0`, volume
  `wpmgr-prod-db`, alamat tetap, tanpa port ke host. Kata sandi root di `KONF_DIR/prod/db-root`,
  berbeda dengan staging.
- **`wpmgr-prod-router`**: `nginx:1.27-alpine`, hanya `127.0.0.1:8091` (`PROD_ROUTER_PORT`),
  `--memory 128m`. Mount `KONF_DIR/prod/router/conf.d` dan `htpasswd` read-only.
- **IP klien asli:** host nginx mengirim `X-Forwarded-For`, dan router meneruskannya dengan
  `$proxy_add_x_forwarded_for`. Image WordPress resmi memasang `mod_remoteip` yang memercayai
  172.16.0.0/12 sebagai proxy, sehingga `REMOTE_ADDR` di PHP adalah IP pengunjung. Ini penting
  supaya plugin keamanan tidak memblokir IP router (asumsi A2, diverifikasi di e2e).

Anggaran RAM: 3 site × 512 MB + 768 MB DB + 128 MB router ≈ 2,4 GB. `prod-buat` ditolak bila RAM
tersedia < 2 GB (`rencana.cek_ram` atas `prod-status`).

### 7.2 Isolasi jaringan dan database dari staging

| Lapisan | Staging (Lapis 3) | Produksi (Lapis 4) |
|---|---|---|
| Jaringan / jembatan | `wpmgr-staging` / `br-wpmgrstg`, 172.31.250.0/24 | `wpmgr-prod` / `br-wpmgrprod`, `PROD_SUBNET` 172.31.251.0/24 |
| Label Docker | `wpmgr.staging=...` | `wpmgr.hosting=...` (subperintah staging tidak pernah menyentuh label ini, dan sebaliknya; `pastikan_milik` diberi kunci label) |
| Nama container | `wp-<nama>`, `wpmgr-stg-*` | `wpp-<nama>`, `wpmgr-prod-*` |
| MariaDB | `wpmgr-stg-db`, volume `wpmgr-stg-db`, user `stg_*` | `wpmgr-prod-db`, volume `wpmgr-prod-db`, user `prd_*`, root berbeda |
| Data | `WPMGR_STAGING_DIR` | `WPMGR_HOSTING_DIR` |
| Sertifikat | `/var/lib/wpmgr/certs` (0640 grup nginx, path variabel) | `/var/lib/wpmgr/hosting-certs` (0600 root, path tetap; dibaca master nginx) |

Aturan iptables `pasang_iptables_prod` (dipasang `prod-siapkan`, idempoten, setelah
`pastikan_brnf`):

1. `INPUT -i br-wpmgrprod -d <IP_PUBLIK> -p tcp -m multiport --dports 80,443 -j ACCEPT`.
   Ini pengecualian **loopback**: WordPress memanggil URL-nya sendiri (spawn WP-Cron, cek
   loopback Site Health, proses latar Elementor/WooCommerce lewat `admin-ajax.php`). Setelah aktif,
   domain me-resolve ke IP publik VPS. Tanpa aturan ini semua panggilan itu dibuang. Port 80/443
   nginx host sudah terbuka untuk seluruh internet, jadi pengecualian ini tidak membuka
   apa pun yang baru (asumsi A5).
2. `INPUT -i br-wpmgrprod -j DROP`: tidak ada akses lain ke host (PostgreSQL dashboard, ERPNext,
   Redis, dan sebagainya).
3. `DOCKER-USER -i br-wpmgrprod ! -o br-wpmgrprod -d <10/8, 172.16/12, 192.168/16, 169.254/16,
   100.64/10> -j DROP`. Ini termasuk subnet staging. Aturan staging yang sudah ada membuang arah
   sebaliknya. Docker sendiri juga mengisolasi jembatan yang berbeda.
4. Rantai `WPMGR-PROD-ANTAR` (pola `pasang_isolasi_antar`): ESTABLISHED/RELATED, router → mana pun
   tcp/80, mana pun → db tcp/3306, lalu DROP. Situs A tidak bisa membuka `http://wpp-B/`.
   Situs A bisa mencapai db:3306, tetapi hanya dengan kredensialnya sendiri.

Internet keluar diizinkan (update plugin, API pihak ketiga, SMTP eksternal).

### 7.3 Skrip pembantu: subperintah `prod-*` di `wpmgr-staging`

**Keputusan: subperintah baru di skrip yang sama, bukan skrip saudara.**

Alasannya:

- Fungsi yang sensitif keamanan sudah ada dan teruji 51 test bats: `muat_konf`, `galat` dengan
  kode keluar tetap, `dibatasi` (tenggat F10), `sbg_pengguna` (setpriv), `cek_mount_*` (F2/R13),
  `pastikan_milik` (RF5/F14), `image`/`digest.lock`, `opsi_klien` (sandi tidak lewat argv),
  `tulis_wp_config` (no-follow, I3), jebakan `bersihkan`.
- Skrip saudara harus menyalin ±400 baris itu, atau keduanya harus `source` pustaka bersama.
  Pustaka itu sendiri harus divalidasi sebagai milik root dan bukan symlink setiap dijalankan,
  dan itu permukaan serangan baru.
- Entri sudoers, berkas konfigurasi, unit systemd, dan harness bats (`tests/palsu/*`) sudah ada.
  Tidak ada yang perlu dipasang baru.

Mitigasi risiko "skrip makin besar": semua kode produksi berada di satu bagian `# ---- produksi`,
memakai label, jaringan, dan direktori sendiri, dan seluruh test bats staging yang ada wajib
tetap hijau tanpa diubah.

Subperintah produksi **tidak aktif** bila `HOSTING_DIR` tidak diisi di `staging.conf`. Dalam
keadaan itu `prod-siapkan` keluar 0 dengan pesan "hosting tidak dikonfigurasi; dilewati",
subperintah lain keluar `GALAT konfigurasi`.

#### 7.3.1 Generalisasi fungsi yang ada (perilaku staging tetap)

- `cek_mount_pengguna <jalur> [basis]`: basis default `STAGING_DIR`; produksi memakai `HOSTING_DIR`.
- `pastikan_milik <wadah> <label> [kunci]`: kunci default `wpmgr.staging`.
- `jalankan_layanan`: menerima jaringan dan kunci label.
- `opsi_klien`/`sql_root`: menerima nama container db dan berkas sandi root.
- `tulis_wp_config <generator> ...`: generator `wp_config` (staging) atau `wp_config_prod`.

#### 7.3.2 Validasi argumen baru

| Argumen | Aturan (bash, `LC_ALL=C`) |
|---|---|
| `<nama>` produksi | `^[a-z0-9-]{1,36}$` (`cek_nama_prod`), supaya `vps-<nama>` tetap ≤ 40 |
| `<domain>` | `^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$`, panjang ≤ 253, tidak diawali `www.`, tidak sama dengan `DOMAIN` dan tidak berakhiran `.${DOMAIN}`, dan tidak dipakai berkas state situs lain |
| `<www>` | `0` atau `1` |
| `<versi_php>`, `<site_id>`, `<prefix>` | `cek_versi`, `cek_id`, `cek_prefix` Lapis 3 |
| `<stempel>` | `^[0-9]{8}T[0-9]{6}Z$` |
| htpasswd pratinjau | `^pratinjau:\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$`, dibaca sebagai user dashboard dengan `head -c 1024` |

Python memvalidasi ulang dengan aturan yang sama (`aman.domain_sah`, `fullmatch`) sebelum
memanggil, sesuai prinsip dua lapis Lapis 3.

#### 7.3.3 Daftar subperintah

| Subperintah | Argumen | Isi | Tenggat |
|---|---|---|---|
| `prod-siapkan` | — | Buat direktori §5.3 dengan mode yang benar, `db-root`, `php.ini`, jaringan `wpmgr-prod`, iptables §7.2, `pastikan_brnf`, layanan `wpmgr-prod-db` dan `wpmgr-prod-router` (alamat tetap: broadcast-1 router, -3 db). Idempoten. | 960 s |
| `prod-buat` | `<nama> <versi_php> <site_id> <domain> <www>` | Tolak bila RAM < 2 GB tidak dicek di sini (dicek dashboard). Tulis/validasi state `situs/<nama>`: bila sudah ada, `SITE_ID` dan `DOMAIN` harus sama. Tolak bila domain sudah dipakai state lain. Buat `files/`, `log/` sebagai user, `cek_mount_pengguna` dengan basis `HOSTING_DIR`, buat/jalankan `wpp-<nama>` (§7.1). Bila `MODE=aktif`: hanya `start` container yang sudah ada; membuat ulang ditolak. | 660 s |
| `prod-jalan` | `<nama>` | `pastikan_milik` + `cek_mount_wadah_prod penuh` + `start`. Dipakai sesudah pemulihan manual. | 180 s |
| `prod-hapus` | `<nama>` | **Ditolak bila `MODE=aktif`.** Hapus container, DB dan user, berkas router, berkas nginx domain (dengan uji dan reload), dan state. | 180 s |
| `prod-db-buat` | `<nama> <prefix>` | **Ditolak bila `MODE=aktif`.** DB/user `prd_<nama>`, sandi acak root-only, simpan `PREFIX` ke state, tulis `wp-config.php` mode **pratinjau** (§7.6) lewat `tulis_wp_config` (sebagai user, tanpa mengikuti symlink). | 180 s |
| `prod-db-impor` | `<nama>` (SQL dari stdin) | **Ditolak bila `MODE=aktif`.** Pola `cmd_db_impor`: DROP/CREATE lalu impor sebagai user situs, `--binary-mode --local-infile=0`. | 3 jam |
| `prod-router-muat` | — | Render ulang semua situs dari state root + htpasswd kiriman dashboard (hanya untuk `MODE=pratinjau`), cadangan, `nginx -t` di router, reload, pulihkan otomatis lewat jebakan EXIT (pola `cmd_router_muat`). | 180 s |
| `prod-domain` | `<nama>` | Render berkas nginx host domain dari state (mode `pratinjau` atau `aktif`) dengan prosedur aman §7.4. | 180 s |
| `prod-sertifikat` | `<nama>` | `certbot certonly --webroot -w ACME_DIR -d <domain> [-d www.<domain>] --cert-name <domain> --keep-until-expiring`, dengan `--config-dir/--work-dir/--logs-dir` di `LE_DIR` yang sama dengan staging (nama sertifikat tidak bertabrakan). Pasang ke `PROD_CERT_DIR/<domain>/` (fullchain 0644, privkey 0600 root:root) setelah `cek_mount_root` atas direktorinya. Bila `MODE=aktif` dan `fullchain.pem` berubah: `nginx -t` lalu reload. Keluaran: `terbit` / `tetap` / `diperbarui`. | 360 s |
| `prod-aktifkan` | `<nama>` | Prasyarat (keluar **3 `ditolak` tanpa mengubah apa pun** bila gagal): state ada, container ada, sertifikat domain ada (berkas biasa, milik root). Lalu, berurutan dan idempoten: (1) `wp-config.php` mode aktif; (2) router dirender dengan situs ini mode aktif + reload; (3) nginx domain mode aktif (§7.4); (4) `MODE=aktif` ditulis ke state. Kegagalan di (3) memulihkan nginx lama; (1)–(2) dibiarkan karena domain belum dilayani HTTPS asli. | 360 s |
| `prod-backup` | `<nama> <stempel>` | §9.2. Idempoten per stempel. | 3 jam |
| `prod-backup-hapus` | `<nama> <stempel>` | Hapus `BACKUP_DIR/<site_id>/<stempel>` (direktori milik root, bukan symlink). Tidak ada → sukses. | 180 s |
| `prod-status` | — | JSON `{mem_tersedia, disk_total, disk_bebas, backup_total, backup_bebas, container:{<nama>: {berjalan}}}`. Disk dari `df HOSTING_DIR` dan `df BACKUP_DIR`. | 180 s |

`sertifikat vps-<nama>` (Lapis 3, tidak diubah) menerbitkan sertifikat host pratinjau lewat server
port 80 wildcard staging yang sudah ada.

**Kode keluar baru:** 10 `nginx` (uji/reload nginx host gagal; berkas lama dipulihkan) dan
11 `backup`. Cerminnya ada di `pembantu.KODE_KELUAR`. `PESAN_UMUM` dan `AKSI` ditambah teks
tetap per subperintah.

**Kunci konfigurasi baru di `staging.conf`** (masuk daftar putih `muat_konf`, path lolos
`POLA_JALUR`):

| Kunci | Default | Arti |
|---|---|---|
| `HOSTING_DIR` | kosong (produksi mati) | mis. `/var/lib/wpmgr/hosting`, milik user dashboard |
| `PROD_SUBNET` | `172.31.251.0/24` | divalidasi seperti `SUBNET`; wajib tidak beririsan dengan `SUBNET` |
| `PROD_ROUTER_PORT` | `127.0.0.1:8091` | wajib di 127.0.0.1 |
| `PROD_CERT_DIR` | `/var/lib/wpmgr/hosting-certs` | |
| `NGINX_HOSTING_DIR` | `/etc/nginx/wpmgr-hosting` | |
| `BACKUP_DIR` | `/var/lib/wpmgr/backup` | |
| `IP_PUBLIK` | wajib bila `HOSTING_DIR` diisi | IPv4 publik VPS, untuk aturan loopback |
| `NGINX_UJI_SAJA` | 0 | kait test: `nginx -t` tanpa reload |
| `SERTIFIKAT_SENDIRI` | 0 | kait test: sertifikat self-signed lewat `openssl` |

### 7.4 nginx host per domain

**Pemasangan sekali (manual):** `/etc/nginx/sites-enabled/wpmgr-hosting.conf` berisi satu baris
`include /etc/nginx/wpmgr-hosting/*.conf;`. Glob tanpa berkas sah di nginx.

**Template mode `pratinjau`** (ditulis di akhir `pindah_tarik`):

```nginx
# Dibuat `wpmgr-staging prod-domain <nama>`. Jangan diedit; selalu ditimpa.
server {
    listen 80;
    listen [::]:80;
    server_name <domain> www.<domain>;          # www hanya bila WWW=1
    location ^~ /.well-known/acme-challenge/ {
        root <ACME_DIR>;
        default_type text/plain;
        try_files $uri =404;
    }
    location / { return 301 https://$host$request_uri; }
}
# Hanya bila sertifikat host pratinjau sudah ada:
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name vps-<nama>.<DOMAIN> <domain> www.<domain>;
    ssl_certificate     <CERT_DIR>/vps-<nama>.<DOMAIN>/fullchain.pem;
    ssl_certificate_key <CERT_DIR>/vps-<nama>.<DOMAIN>/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    client_max_body_size 64M;
    location / { <blok proxy> }
}
```

**Template mode `aktif`:** server port 80 yang sama. Server 443 hanya untuk `<domain>
www.<domain>`, dengan `PROD_CERT_DIR/<domain>/{fullchain,privkey}.pem` (path tetap, dibaca master
saat reload).

`<blok proxy>`: `proxy_pass http://<PROD_ROUTER_PORT>; proxy_http_version 1.1;
proxy_set_header Host $host; X-Real-IP $remote_addr; X-Forwarded-For
$proxy_add_x_forwarded_for; X-Forwarded-Proto https; proxy_read_timeout 300s;
proxy_send_timeout 300s;`.

Catatan template:

- Tanpa `default_server` dan tanpa `http2`. Alasan `http2` sama dengan berkas staging: di nginx
  1.24, `http2` adalah opsi per soket.
- Host pratinjau memakai nama tepat. Nama tepat mengalahkan wildcard `*.staging.halosocia.my.id`
  milik berkas staging, baik untuk pemilihan SNI maupun `Host`. Tantangan ACME port 80 untuk host
  pratinjau tetap dilayani server wildcard staging (webroot sama).
- Domain asli ikut di server 443 pratinjau supaya pratinjau lewat berkas hosts berfungsi, dengan
  peringatan sertifikat. Bila DNS sudah berpindah sebelum aktivasi, pengunjung mendapat peringatan
  sertifikat lalu Basic Auth, bukan isi produksi.

**Prosedur `prod-domain`** (di bawah `flock KONF_DIR/prod/nginx.lock`):

1. **Pra-cek kepemilikan nama.** Jalankan `nginx -T`, lalu ikuti penanda `# configuration file
   <path>:`. Tolak (`GALAT ditolak`, tanpa perubahan) bila ada berkas di luar `NGINX_HOSTING_DIR`
   yang mendeklarasikan `server_name` berupa `<domain>`, `www.<domain>`, `.<domain>`, atau
   `*.<domain>`. Domain yang sudah dilayani site lain di VPS tidak pernah direbut.
2. Render ke berkas sementara `.<domain>.baru` di `NGINX_HOSTING_DIR`. Nama ini tidak cocok
   dengan glob `*.conf`.
3. Salin berkas lama (bila ada) ke `nginx-cadangan/`, set `NGINX_CADANGAN` (dipulihkan jebakan
   EXIT seperti `ROUTER_CADANGAN`), lalu `mv` berkas baru ke `<domain>.conf`.
4. `nginx -t` dengan stderr ditangkap. Gagal bila kode keluar ≠ 0 **atau** ada peringatan
   `conflicting server name` yang menyebut `<domain>`/`www.<domain>`. Peringatan itu tidak
   menggagalkan `nginx -t`, tetapi berarti salah satu server diam-diam diabaikan.
5. `systemctl reload nginx`. Gagal → pulihkan berkas lama, `nginx -t`, reload lagi, lalu
   `GALAT nginx`.
6. Sukses → hapus cadangan, kosongkan `NGINX_CADANGAN`.

Setiap kegagalan meninggalkan berkas yang sama dengan sebelum perintah. Konfigurasi yang
berjalan tidak pernah dimuat ulang dengan berkas yang gagal diuji, jadi ±19 site lain tidak
terpengaruh.

**`00-default-catchall.conf`** tidak disentuh. Berkas domain tidak pernah memakai
`default_server`, jadi catchall tetap menangani nama yang tidak dikenal (perilaku 443-nya:
asumsi A4).

### 7.5 Router produksi

Satu berkas `conf.d/prd-<nama>.conf` per situs, dari template tetap di skrip:

```nginx
server {
    listen 80;
    server_name <domain> www.<domain> [vps-<nama>.<DOMAIN>];      # host pratinjau hanya mode pratinjau
    client_max_body_size 64m;
    absolute_redirect off;
    # Hanya mode pratinjau:
    auth_basic "Pratinjau <nama>";
    auth_basic_user_file /etc/nginx/wpmgr-htpasswd/<nama>;
    add_header X-Robots-Tag "noindex, nofollow" always;
    location / {
        set $wpmgr_hulu wpp-<nama>;
        proxy_pass http://$wpmgr_hulu;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}
```

Ditambah `00-bawaan.conf` (`return 444` default dan resolver Docker), sama seperti staging. Tanpa
`secure_link`/SSO: dashboard tidak perlu masuk ke pratinjau (§21).

### 7.6 Pengaman pratinjau dan pencabutannya

`wp-config.php` produksi selalu dibuat skrip pembantu (`wp_config_prod`). Isi bersama: kredensial
`prd_<nama>` di `wpmgr-prod-db`, `$table_prefix` dari state, salt baru, `WP_DEBUG` mati dengan log
ke `/wpmgr-log/php-error.log`, dan potongan `HTTP_X_FORWARDED_PROTO` → `HTTPS=on` seperti staging.
Tanpa `WP_HOME`/`WP_SITEURL`, sehingga nilai dari database (sama dengan hosting lama) yang berlaku.

Mode **pratinjau** menambahkan:

```php
define( 'WPMGR_PRATINJAU', true );
define( 'WPMGR_PRATINJAU_HOST', 'vps-<nama>.<DOMAIN>' );
define( 'WPMGR_DOMAIN', '<domain>' );
define( 'DISABLE_WP_CRON', true );
define( 'AUTOMATIC_UPDATER_DISABLED', true );
if ( isset( $_SERVER['HTTP_HOST'] ) && WPMGR_PRATINJAU_HOST === $_SERVER['HTTP_HOST'] ) {
    define( 'WP_HOME', 'https://' . WPMGR_PRATINJAU_HOST );
    define( 'WP_SITEURL', 'https://' . WPMGR_PRATINJAU_HOST );
}
```

Mu-plugin `wp-content/mu-plugins/wpmgr-pratinjau.php` ditulis dashboard (lewat
`aman.tulis_atomik`) dari template baru `connector/wp-manager-connector/templates/wpmgr-pratinjau.php.tpl`
melalui `connector_paket.isi_mu_plugin_pratinjau()`. Template ini tanpa placeholder; semua nilai
dibaca dari konstanta. Isinya:

- diam sepenuhnya bila `WPMGR_PRATINJAU` tidak didefinisikan (pola `wpmgr-staging.php.tpl`);
- `pre_wp_mail` → `false` (WP ≥ 5.7), dan `phpmailer_init` prioritas `PHP_INT_MAX` yang
  mengosongkan penerima (WP lebih lama);
- `pre_option_blog_public` → `'0'` dan `wp_robots` noindex;
- spanduk admin dan simpul admin bar "PRATINJAU VPS — email diblokir, cron mati";
- hanya untuk request ke `WPMGR_PRATINJAU_HOST`: `ob_start` yang mengganti `https://<domain>`,
  `https://www.<domain>`, dan bentuk ber-escape JSON (`https:\/\/<domain>`) dengan host pratinjau.
  Tanpa ini gambar dan tautan di konten mengarah ke hosting lama.

**Pencabutan saat aktivasi:**

| Pengaman | Letak | Dicabut oleh | Diverifikasi |
|---|---|---|---|
| Basic Auth | router (mode pratinjau) | `prod-aktifkan` (2) | GET tanpa kredensial bukan 401 |
| noindex | header router + mu-plugin | `prod-aktifkan` (2) + hapus mu-plugin | header `X-Robots-Tag` tidak memuat `noindex` dari router |
| Email diblokir | mu-plugin (dijaga `WPMGR_PRATINJAU`) | dashboard menghapus berkasnya; `prod-aktifkan` (1) menghapus konstantanya | berkas tidak ada |
| WP-Cron mati | `DISABLE_WP_CRON` di wp-config | `prod-aktifkan` (1) | `wp-config.php` tidak memuat `WPMGR_PRATINJAU` (dibaca dashboard lewat `aman.baca_terbatas`) |
| Host pratinjau | wp-config, router, nginx | `prod-aktifkan` (1)–(3) | |
| Jeda otomatis | tidak pernah dipasang | — | cron jeda Lapis 3 hanya membaca tabel `staging` |
| Database | **tidak pernah diubah** (tanpa `blog_public=0`, tanpa search-replace) | — | — |

Mu-plugin dihapus dashboard **sebelum** `prod-aktifkan`. Bila penghapusan gagal, berkas yang
tertinggal tetap diam karena konstanta sudah hilang. Verifikasi mencatatnya sebagai peringatan,
bukan galat.

---

## 8. DNS dan Sertifikat

### 8.1 Pemeriksaan

Modul `wpmgr/hosting/dns.py`, dependensi baru **`dnspython>=2.6`**. `socket.getaddrinfo` memakai
resolver lokal, `/etc/hosts`, dan cache, dan tidak bisa menanyakan resolver tertentu atau CAA.

`periksa_dns(hosting, resolver=None) -> HasilDns`:

- Resolver dari `WPMGR_HOSTING_RESOLVER` (default `1.1.1.1,8.8.8.8`). Setiap resolver ditanya
  A dan AAAA untuk `<domain>` dan, bila `dengan_www`, `www.<domain>`. CNAME diikuti resolver.
  Tenggat 3 detik per kueri, total ≤ 10 detik.
- Satu kueri CAA untuk `<domain>` (dan induk sampai zona; cukup berhenti di jawaban pertama).
- Nilai dari DNS adalah masukan luar: hanya alamat yang lolos `ipaddress` yang disimpan, paling
  banyak 8 per jenis. Pesan UI berasal dari kode tetap.

**Lolos** hanya bila, untuk **setiap** resolver dan setiap nama:

- himpunan A tidak kosong dan sama persis dengan `{WPMGR_HOSTING_IPV4}`;
- himpunan AAAA kosong, atau (`WPMGR_HOSTING_IPV6` diisi dan himpunannya `{WPMGR_HOSTING_IPV6}`);
- CAA kosong, atau ada `issue`/`issuewild` yang memuat `letsencrypt.org`.

Resolver yang berbeda pendapat berarti belum lolos. Pesan: "DNS sedang menyebar".

### 8.2 Penanganan A dan AAAA

nginx host mendengarkan `[::]:80`/`[::]:443`. Zona Hostinger memuat `AAAA` ke IPv6 shared
hosting Hostinger secara bawaan (lihat §2). Bila pengguna hanya mengganti A:

- Let's Encrypt mendahulukan IPv6 saat validasi HTTP-01. Tantangan lalu sampai ke hosting lama
  dan gagal, sehingga sertifikat tidak pernah terbit (asumsi A14).
- Pengunjung ber-IPv6 tetap ke hosting lama, pengunjung IPv4 ke VPS. Hasilnya split-brain: isian
  form dan komentar tersebar di dua tempat.

Karena itu AAAA lama **wajib dihapus** dan cek DNS menahan aktivasi sampai AAAA bersih. UI
menampilkan baris "Hapus AAAA `@` = `<nilai yang terlihat>`" per record. Bila VPS punya IPv6
publik dan operator mengisi `WPMGR_HOSTING_IPV6`, instruksinya menjadi "ubah AAAA ke `<IPv6 VPS>`",
dan cek DNS menerima AAAA yang menunjuk ke IPv6 VPS itu. VPS sasaran **punya** IPv6 publik
`2a02:c207:2347:2607::1`, dan nginx host sudah mendengarkan `[::]`. Deploy di VPS ini mengisi
`WPMGR_HOSTING_IPV6` dengan nilai itu, jadi AAAA diubah, tidak dihapus (A10 sudah diverifikasi). `ip_lama` hanya IPv4, karena koneksi ke hosting lama cukup lewat IPv4.

### 8.3 Penyimpanan hasil

`hosting_vps.dns_hasil` = `{"ok": bool, "dicek": iso, "nama": [{"nama": "@|www", "jenis": "A|AAAA|CAA",
"terlihat": [...], "harus": [...], "ok": bool, "kode": "cocok|kurang|lebih|hapus|beda_resolver|caa"}]}`.
UI menyusun instruksi dari kode, bukan dari teks bebas.

### 8.4 Sertifikat

- **Host pratinjau:** `sertifikat vps-<nama>` (Lapis 3) di tahap `pratinjau` `pindah_tarik`. Bila
  gagal, pratinjau HTTPS belum tersedia; dicatat sebagai peringatan dan dicoba lagi pada
  **Salin ulang**. Tidak diperpanjang otomatis (umur 90 hari ≫ masa pratinjau).
- **Domain:** `prod-sertifikat` **hanya** dipanggil di dalam `pindah_aktifkan`, sesudah cek DNS
  lolos pada percobaan yang sama. Site tidak pernah aktif tanpa sertifikat, karena `prod-aktifkan`
  menolak dengan keluar 3 bila berkasnya tidak ada.
- **Backoff kegagalan:** batas Let's Encrypt adalah 5 validasi gagal per akun per hostname per jam.
  Setelah gagal ke-*k*, pengantrean otomatis berikutnya tidak sebelum
  `sertifikat_gagal_pada + min(2^(k-1) jam, 6 jam)`. Tombol manual mengabaikan backoff hanya bila
  sudah lewat ≥ 15 menit.
- **Perpanjangan:** cron `renew-hosting-certs` harian memanggil `prod-sertifikat` untuk setiap
  baris dengan `dilayani_vps_pada` terisi. Skrip me-reload nginx hanya bila sertifikat berubah.
  Chip `ssl` Lapis 2 (kedaluwarsa dari pemeriksaan TLS publik) sudah memberi peringatan bila
  perpanjangan terus gagal, jadi tidak ada chip baru untuk ini.

---

## 9. Backup

### 9.1 Antarmuka `TujuanBackup`

`src/wpmgr/hosting/backup.py`:

```python
@dataclass(frozen=True)
class HasilBackup:
    ukuran_db: int
    ukuran_file: int
    sha256_db: str
    sha256_file: str

class TujuanBackup(Protocol):
    kode: str                                                   # disimpan di hosting_backup.tujuan
    def buat(self, hosting: HostingVps, stempel: str) -> HasilBackup: ...
    def hapus(self, hosting: HostingVps, stempel: str) -> None: ...   # idempoten

class TujuanLokal:            # kode = "lokal"; memanggil prod-backup / prod-backup-hapus
    def __init__(self, pb: Pembantu) -> None: ...

TUJUAN = {"lokal": TujuanLokal}
def tujuan_dari_setelan(pb) -> TujuanBackup     # WPMGR_BACKUP_TUJUAN
```

Tujuan di luar VPS (S3/R2) nanti menjadi implementasi kedua. Isi backup hanya bisa dibaca root,
jadi implementasi seperti itu butuh subperintah pengirim di skrip pembantu. Itu di luar lingkup
dan antarmuka di atas tidak perlu berubah.

### 9.2 Isi dan format (`prod-backup <nama> <stempel>`)

```
/var/lib/wpmgr/backup/<site_id>/<stempel>/     0700 root
  db.sql.gz       mariadb-dump --single-transaction --quick --hex-blob --no-tablespaces
                  --default-character-set=utf8mb4 prd_<nama>, dijalankan root di wpmgr-prod-db
                  (opsi_klien), di-gzip di host
  files.tar.gz    tar -C <HOSTING_DIR>/<site_id> --numeric-owner -czf - files
                  dijalankan SEBAGAI user dashboard (sbg_pengguna), keluaran ditulis root
  manifest.json   {"versi":1,"site_id","nama","domain","stempel","versi_php","prefix",
                   "ukuran_db","ukuran_file","sha256_db","sha256_file"}
```

- `files/` bisa ditulis kode situs yang mungkin disusupi. `tar` sebagai user dashboard tidak
  mengikuti symlink dan tidak bisa membaca apa pun yang tidak bisa dibaca user itu, termasuk saat
  direktori ditukar di tengah jalan. Root hanya menerima aliran byte.
- Tulis ke `.<stempel>.tmp/`, lalu `rename` ke `<stempel>/`. Bila `<stempel>/` sudah ada dengan
  `manifest.json` sah, keluarannya langsung dicetak (idempoten untuk percobaan ulang). Sisa
  `.tmp` dihapus dulu.
- `wp-config.php` (mode aktif) ikut dalam `files.tar.gz`. Kredensial DB-nya tetap sah setelah
  dipulihkan karena user DB tidak dibuat ulang.
- Keluaran stdout: isi `manifest.json`, divalidasi ketat oleh `TujuanLokal`.
- Container tidak pernah memasang `BACKUP_DIR`. Dashboard tidak bisa membacanya; hanya
  metadatanya yang tercatat di `hosting_backup`.

### 9.3 Retensi (7 harian + 4 mingguan)

Fungsi murni `pilih_simpan(backups, harian=7, mingguan=4) -> set[id]`, atas backup `tersedia`
(manual ikut dihitung):

- harian: untuk 7 **tanggal UTC berbeda terbaru** yang punya backup, simpan yang terbaru per tanggal;
- mingguan: untuk 4 **minggu ISO berbeda terbaru**, simpan yang terbaru per minggu;
- backup terbaru selalu disimpan; sisanya dipangkas.

"Tanggal berbeda terbaru", bukan "7 hari kalender terakhir". Bila backup gagal beberapa hari,
backup lama bertahan lebih lama.

Pemangkasan **hanya** berjalan di akhir `backup_hosting` yang sukses: `TujuanBackup.hapus` lalu
baris ditandai `dipangkas`. Bila hapus gagal, baris tetap `tersedia` dan dicoba lagi pada backup
berikutnya. Backup yang gagal tidak pernah memangkas apa pun.

### 9.4 Pemulihan manual (README, opsi B)

Sebagai root, dengan `S=<site_id> N=<nama> T=<stempel> D=prd_<nama dengan - menjadi _>`:

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

`tar` diekstrak sebagai `wpmgr`, sehingga kepemilikan sama dengan yang diharapkan
`cek_mount_pengguna`. Direktori `files.sebelum-pulih-*` dihapus manual setelah site diperiksa.

---

## 10. Job dan Mesin Keadaan

### 10.1 Klien hosting lama (dipatok IP, hanya baca)

Sesudah DNS berpindah, `site.url` me-resolve ke VPS sendiri. Tarik ulang di aktivasi harus tetap
mengambil dari hosting lama. Karena itu:

- `SiteClient` mendapat parameter `alamat_tetap: str | None`. Bila diisi, request dikirim ke
  `https://<ip>` dengan header `Host: <domain>` dan `extensions={"sni_hostname": <domain>}`.
  Sertifikat tetap diverifikasi terhadap nama domain (asumsi A1). Kontrak HMAC tidak berubah
  karena path yang ditandatangani tidak memuat host.
- `hosting.umum.klien_lama(site, hosting)` membungkusnya dalam `KlienLamaBacaSaja`, yang hanya
  meneruskan `staging_manifest`, `staging_file`, `staging_rentang`, `staging_tabel`,
  `staging_tanda_air`, dan `ping`. Metode tulis (`staging_unggah`, `staging_terapkan`, update)
  tidak ada di objek itu, jadi site lama tidak bisa diubah.
- `ip_lama` ditolak bila sama dengan `WPMGR_HOSTING_IPV4`, privat, atau loopback.
- Handler hosting mengabaikan klien bawaan yang diberikan worker (`buat_klien_fn(site)`).

### 10.2 `TujuanSalinan` (refaktor `tarik.py`)

```python
@dataclass
class TujuanSalinan:
    akar: Path                         # <dir>/<site_id>
    baris: Staging | HostingVps        # untuk titik_potongan (batal)
    status_sumber: Callable[[], object]          # pb.status() / pb.prod_status()
    cek_awal: Callable[[object], str | None]     # RAM, maks aktif (staging) / RAM (hosting)
    periksa_info: Callable[[dict], None]         # hosting: https, host = domain/www, tanpa path
    sql_tambahan: Callable[[Session, dict, Path], None]   # staging: berkas_secret_staging; hosting: no-op
    impor: Callable[[dict, list[Path]], None]    # db_buat+db_impor / prod_db_buat+prod_db_impor
    siapkan_runtime: Callable[[Session, Job, dict], None]
```

`tarik_inti` menjalankan tahap Lapis 3 tanpa perubahan semantik. Itu termasuk R21 (penolakan
sebelum salinan disentuh = tanpa ubah), F3 (indeks dibangun ulang dari `files/` di awal tarik
baru), anggaran byte, dan peringatan. Test staging yang ada adalah jaring pengamannya. `tarik()`
staging tetap memegang finalisasi baris `Staging` dan sertifikatnya.

`TujuanHosting` (`hosting/pindah.py`):

- `periksa_info` menolak, sebagai `GalatDitolakTanpaUbah` sebelum salinan disentuh:
  - `home`/`siteurl` yang bukan `https://`;
  - `home`/`siteurl` ber-path;
  - host yang bukan `<domain>`/`www.<domain>`;
  - `www.` di `home` tanpa `dengan_www`.

  Multisite dan `wp-content` di luar `ABSPATH` sudah ditolak `urai_info`.
- `siapkan_runtime`, di dalam `umum.detak_latar`:
  1. tulis mu-plugin pratinjau;
  2. tulis `HOSTING_DIR/router/<nama>.htpasswd` (`pratinjau:<bcrypt>`, via `pembantu._tulis_atomik`);
  3. `prod_buat`;
  4. `prod_router_muat`.

  Tanpa search-replace dan tanpa wp-cli.
- Tahap tambahan `pratinjau` sesudah `penyiapan`: `sertifikat vps-<nama>` (gagal = peringatan),
  lalu `prod_domain` (gagal = galat).

### 10.3 `pindah_tarik`

Tahap (di `kemajuan.tahap`): `manifest → berkas → tanda_air → db → impor → penyiapan → pratinjau`.

- Gerbang:
  - fitur hosting aktif;
  - `punya_fitur(site, STAGING)`;
  - `dilayani_vps_pada IS NULL` (**selalu**, juga pada percobaan ulang);
  - status awal `menyalin`, `pratinjau`, `menunggu_dns`, atau `gagal` asal `salinan`;
  - RAM ≥ 2 GB dan disk sesudah tarik ≥ 15% (`rencana.cek_ram`/`cek_disk` atas `prod-status`).
- Sukses: `status=pratinjau`, `ditarik_pada`, ukuran, `versi_php`, `galat=NULL`, log aktivitas
  "Salinan VPS dibuat/disegarkan".
- Gagal final setelah salinan disentuh: `gagal` asal `salinan`. Penolakan sebelum salinan
  disentuh: status sebelum job. Site lama tidak terpengaruh dalam kedua kasus.
- Dapat dibatalkan (`batal_diminta_pada`) di antara potongan.
- Kelas worker: termasuk `JOB_RUNTIME_BACA`. Boleh berjalan bersamaan dengan job non-runtime site
  yang sama (scan/monitoring, yang ke hosting lama), dan diserialkan terhadap job staging/hosting
  lain.

### 10.4 `pindah_aktifkan`

Payload: `{tanpa_tarik_ulang: bool}`. Langkah di `kemajuan.langkah_aktifkan`, ditulis **sebelum**
setiap langkah dijalankan:

| Langkah | Isi | Bila gagal |
|---|---|---|
| `dns` | `periksa_dns`; simpan `dns_hasil` | belum lolos → `GalatDitolakTanpaUbah`, status kembali `menunggu_dns` |
| `sertifikat` | `prod_domain` (idempoten, memastikan port 80 ACME) lalu `prod_sertifikat` | `sertifikat_gagal_pada/kali` diisi; `GalatDitolakTanpaUbah`, status `menunggu_dns`, backoff §8.4 |
| `tarik` | `tarik_inti` inkremental dari `klien_lama`; dilewati bila `tanpa_tarik_ulang` | sementara → diulang; final → `gagal` asal `salinan` (site lama masih produksi bagi resolver yang belum berpindah; DNS bisa dikembalikan) |
| `tukar` | commit `tukar_pada` dan `dilayani_vps_pada` **lebih dulu**; hapus mu-plugin; `prod_aktifkan` | keluar 3 (`ditolak`, pasti tanpa perubahan): `tukar_pada`/`dilayani_vps_pada` dikosongkan, `GalatDitolakTanpaUbah`, status `menunggu_dns`. Galat lain (tenggat, docker, nginx sesudah reload, tidak diketahui) = **produksi tersentuh** |
| `verifikasi` | GET `https://<domain>/` (dan `www`) dipatok ke `127.0.0.1` dengan SNI domain: 2xx/3xx, bukan 401, tanpa `X-Robots-Tag: noindex` dari router; `wp-config.php` tanpa `WPMGR_PRATINJAU` | diulang (aturan R26 di bawah) |
| `beres` | `status=aktif`, `aktif_pada`, log aktivitas "Site dihosting di VPS", antrekan `backup_hosting` | — |

Percobaan ulang yang melanjutkan job:

- sesudah `tukar`, langkah `dns`/`sertifikat`/`tarik` dilewati;
- sebelum `tukar`, dimulai lagi dari `dns`, karena DNS bisa saja dikembalikan pengguna.

**Aturan ulang:**

- **F26:** `UNKNOWN` diperlakukan `TRANSIENT`, karena setiap langkah idempoten (`prod-aktifkan`,
  `prod-domain`, `prod-sertifikat` aman diulang).
- **R15:** galat yang sifatnya pasti (cek DNS belum lolos, sertifikat ditolak CA, prasyarat
  `prod-aktifkan` keluar 3, info site ditolak) ditampilkan dengan pesan tetap dan **tidak**
  diulang segera. Cron dan pengguna yang menjadwalkan ulang, dengan backoff sertifikat.
- **Gaya R26 (produksi tersentuh):** `queue.menyentuh_produksi` diperluas untuk `pindah_aktifkan`
  dengan `langkah_aktifkan ∈ {tukar, verifikasi, beres}`. `_JOB_PRODUKSI` dan `_mulai_tukar`
  dipakai ulang lewat `tukar_pada`. Selama `dalam_batas_pemulihan` (24 jam sejak `tukar_pada`),
  galat sementara/tidak diketahui diulang tiap ≤ 15 menit tanpa berhenti di `max_attempts`; ini
  berlaku di worker, pembungkus, dan reaper. Lewat 24 jam: final dengan `status=gagal` asal
  `produksi`, chip `hosting_gagal`, dan pesan tetap "Situs sudah dilayani VPS tetapi pemeriksaan
  akhir gagal. Periksa situs; bila rusak, arahkan DNS kembali ke hosting lama (masih utuh)."
  Tombol **Periksa ulang** mengantrekan `pindah_aktifkan` baru yang langsung ke `verifikasi`.
- **Batal:** hanya berlaku sebelum `tukar` (`boleh_batal`, pola dorong Lapis 3).
- **`_menahan`:** `pindah_aktifkan` tertunda yang sudah memulai `tukar` menahan job non-runtime di
  site itu (pola I1 Lapis 3).
- Kelas worker: tidak termasuk BACA, jadi eksklusif terhadap semua job site itu.

`tanpa_tarik_ulang` untuk hosting lama yang sudah tidak terjangkau: API mensyaratkan pengguna
mengetik domain sebagai konfirmasi. Data sejak tarik terakhir hilang, dan UI menyebut tanggal
`ditarik_pada`.

### 10.5 `backup_hosting`

- Gerbang: `dilayani_vps_pada IS NOT NULL`. Disk `BACKUP_DIR` sesudah backup harus ≥ 15%; taksiran
  memakai `ukuran_file + ukuran_db` (tanpa kompresi, konservatif) lewat `rencana.cek_disk`.
- Langkah:
  1. `stempel` (UTC, dari mulai percobaan pertama, disimpan di kemajuan);
  2. `TujuanBackup.buat`;
  3. sisipkan baris `hosting_backup` (unik per stempel, jadi percobaan ulang aman);
  4. pangkas §9.3;
  5. `backup_terakhir_pada`, `backup_gagal_pada=NULL`.
- Gagal final: `backup_gagal_pada` diisi, log aktivitas level `error`, chip `backup_gagal`.
  Backup lama tidak disentuh.
- `max_attempts` 3, F26. Tidak menyentuh produksi, jadi tanpa aturan R26.
- Tidak mengubah `hosting_vps.status`.
- Kelas worker: bukan BACA, sehingga tidak berjalan bersamaan dengan `staging_dorong` ke produksi
  VPS atau job lain di site itu.

### 10.6 Mesin keadaan `hosting_vps.status`

```
            Pindahkan                    sukses                lanjut DNS
(tidak ada) ─────────► menyalin ──────────────► pratinjau ───────────────► menunggu_dns
                        │   ▲  salin ulang        ▲   ▲    kembali ke         │  ▲
                        │   └─────────────────────┘   └──── pratinjau ───────┘  │
                        │ gagal final                                            │ DNS lolos (cron/tombol)
                        ▼                                                        ▼
                      gagal(salinan) ◄──── gagal final sebelum tukar ──── mengaktifkan
                        │ salin ulang → menyalin                          │      │ ditolak tanpa ubah
                        │ aktifkan (bila DNS lolos) → mengaktifkan        │      └──► menunggu_dns
                                                                          │ sukses
                                          gagal(produksi) ◄── lewat 24 j ─┤
                                                │ periksa ulang            ▼
                                                └──────────────────────► aktif
```

`mengaktifkan` adalah status kerja tambahan di luar lima status ringkasan desain. Selama status
ini, UI meminta pengguna tidak mengubah DNS, dan salin ulang/hapus ditolak.

Pembungkus `hosting.umum.jalankan_hosting` mengikuti pola `umum.jalankan_staging`:
- mencatat status awal sekali di kemajuan;
- memetakan `Dibatalkan`, `GalatDitolakTanpaUbah`, `GalatPembantu`, `SiteError`, `OSError`
  (pesan tetap `pesan_os`), dan pengecualian lain (F12, pesan tetap);
- keputusan final/ulang selalu lewat `queue.akan_diulang`.

Reaper (`jobs/reaper.py`) mendapat `_lepas_hosting` dengan pemetaan status yang sama untuk job
yatim yang final.

### 10.7 Konkurensi dan kunci

- `queue.py`: tuple SQL `_STAGING` menjadi `_RUNTIME` (staging + hosting), dan `_STAGING_BACA`
  ditambah `pindah_tarik`. Keduanya dipakai `SQL_AMBIL`, `_BENTROK`, dan `_menahan`.
- Kontrak kunci cron/route Lapis 3 diperluas: `sites` FOR NO KEY UPDATE, lalu `hosting_vps` FOR
  UPDATE. Bila keduanya perlu, `staging` dikunci sesudah `hosting_vps`.
- Penghapusan site (`routes_api` dan `bersihkan_untuk_hapus_site`) **ditolak** selama ada baris
  `hosting_vps`. Pesannya: "Batalkan pindah hosting dulu", atau untuk yang sudah dilayani VPS:
  "Site ini dihosting di VPS; lepas manual (README)". FK cascade tidak boleh meninggalkan
  container yatim.
- Nama: `hosting_vps.nama` dipilih supaya `vps-<nama>` bukan `staging.nama` mana pun.
  `routes_staging._nama_unik` juga melewati label `vps-<n>` untuk setiap `hosting_vps.nama = n`.

---

## 11. API

Semua route butuh login (`pengguna_api`), punya test akses anonim (401), dan memfilter `site_id`.
Bila fitur mati, GET menjawab `{aktif_fitur: false}` dan route lain menjawab 404. Galat skrip
pembantu → 502 dengan `GalatPembantu.pesan` (teks tetap). Modul: `src/wpmgr/web/routes_hosting.py`.

| Route | Metode | Isi |
|---|---|---|
| `/api/sites/{id}/hosting` | GET | status, `status_teks`, domain, `dengan_www`, URL pratinjau, pengguna `pratinjau`, ukuran, waktu-waktu §5.1, `galat`, `gagal_asal` (hanya bila gagal), `dns_hasil` + instruksi (`[{jenis, nama, aksi: ubah\|hapus, nilai}]`), backoff sertifikat (`coba_lagi_pada`), job aktif + `ringkas_kemajuan`, backup ≤ 50 terbaru |
| `/api/sites/{id}/hosting` | POST | Pindahkan: cek fitur, izin connector, `ip_lama` (§10.1), nama unik; buat baris + kata sandi (ditampilkan sekali) + `pindah_tarik` dalam satu commit (`uq_jobs_hosting_aktif` sebagai penjaga terakhir) |
| `/api/sites/{id}/hosting/tarik` | POST | Salin ulang (status `pratinjau`/`menunggu_dns`/`gagal` asal `salinan`, `dilayani_vps_pada IS NULL`) |
| `/api/sites/{id}/hosting/lanjut-dns` | POST | `pratinjau` → `menunggu_dns` |
| `/api/sites/{id}/hosting/kembali-pratinjau` | POST | `menunggu_dns` → `pratinjau` (menghentikan aktivasi otomatis) |
| `/api/sites/{id}/hosting/aktifkan` | POST | body `{tanpa_tarik_ulang?: bool, konfirmasi?: str}`. Cek DNS sinkron (≤ 10 s); lolos + backoff mengizinkan → antrekan `pindah_aktifkan`. Selain itu 409 dengan `dns_hasil`. Juga dipakai **Periksa ulang** untuk `gagal` asal `produksi` |
| `/api/sites/{id}/hosting/sandi` | POST | kata sandi pratinjau baru (ditampilkan sekali), htpasswd + `prod_router_muat`; ditolak sesudah `dilayani_vps_pada` |
| `/api/sites/{id}/hosting/batal` | POST | isi `batal_diminta_pada` (berlaku sebelum `tukar`) |
| `/api/sites/{id}/hosting` | DELETE | Batalkan pindah: body `{konfirmasi: <domain>}`; ditolak bila `dilayani_vps_pada` terisi atau ada job hosting; `prod_hapus`, hapus htpasswd, `HOSTING_DIR/<site_id>` lewat nisan, baris `hosting_vps`/`hosting_backup` |
| `/api/sites/{id}/hosting/backup` | GET | daftar backup (≤ 50): stempel, waktu, ukuran db/berkas, manual, status |
| `/api/sites/{id}/hosting/backup` | POST | antrekan `backup_hosting` manual (`manual=true` di payload) |

Tidak ada route pemulihan, unduh backup, atau baca isi backup.

---

## 12. Antarmuka Pengguna

Tab **Hosting VPS** di detail site, setelah Staging, dengan `_tab_hosting.html` dan
`static/app/hosting.js`. Satu panel per status:

- **Belum ada:** penjelasan singkat, syarat (connector 3.0 + Izinkan staging), dan tombol
  **Pindahkan ke VPS**.
- **`menyalin`:** strip progres job (persen, MB, perkiraan sisa), tombol **Batal**.
- **`pratinjau`:** URL pratinjau, pengguna `pratinjau`, kata sandi (sekali), **Buat ulang kata
  sandi**, cara pratinjau lewat berkas hosts (baris `169.58.91.181 <domain> www.<domain>` dan
  catatan peringatan sertifikat), **Salin ulang**, **Pratinjau sudah benar, lanjut ke DNS**,
  **Batalkan pindah**.
- **`menunggu_dns`:** tabel instruksi DNS (§8.2), hasil cek terakhir dengan waktu, **Periksa DNS &
  aktifkan sekarang**, keterangan backoff sertifikat bila ada, dan **Kembali ke pratinjau**.
  Catatan: "Turunkan TTL record ke 300 detik sehari sebelumnya bila memungkinkan".
- **`mengaktifkan`:** progres per langkah dan peringatan "jangan ubah DNS".
- **`aktif`:** "Dihosting di VPS sejak …", tanggal sertifikat, tabel backup (waktu, ukuran,
  manual/harian), **Backup sekarang**, tautan ke bagian README tentang pemulihan manual, dan
  pengingat "hosting lama boleh dimatikan".
- **`gagal`:** galat (teks tetap), lalu tombol sesuai asal: **Salin ulang** (salinan) atau
  **Periksa ulang** (produksi).

Semua teks dari site, DNS, dan job dirender ter-escape (`x-text`, autoescape). Penjaga
`test_template_aman.py` tetap berlaku.

---

## 13. Kesehatan

`kesehatan.masalah_hosting(h, sekarang)`. Chip baru:

| Chip | Tingkat | Menyala bila | Tab |
|---|---|---|---|
| `hosting_gagal` | 1 | `status=gagal` dan `gagal_asal=produksi` | hosting |
| `pindah_gagal` | 2 | `status=gagal` dan asal bukan `produksi` | hosting |
| `backup_gagal` | 2 | `dilayani_vps_pada` terisi dan (`backup_gagal_pada` terisi, atau backup sukses terakhir — atau `aktif_pada` bila belum pernah — lebih dari 36 jam lalu) | hosting |

`URUTAN_CHIP`, `TINGKAT_MASALAH`, dan `TAB_MASALAH` diperluas. Baris Kesehatan mendapat
`hosting_status`. Chip `ssl`, `mati`, dan sebagainya dari Lapis 2 berlaku apa adanya untuk site
yang dihosting.

---

## 14. Konfigurasi Baru

| Variabel | Default | Arti |
|---|---|---|
| `WPMGR_HOSTING_IPV4` | kosong (fitur mati) | IPv4 publik VPS, mis. `169.58.91.181`. Fitur juga mensyaratkan `WPMGR_STAGING_DOMAIN` |
| `WPMGR_HOSTING_IPV6` | kosong | IPv6 publik VPS untuk AAAA; kosong = AAAA wajib dihapus |
| `WPMGR_HOSTING_DIR` | `/var/lib/wpmgr/hosting` | sama dengan `HOSTING_DIR` di `staging.conf` |
| `WPMGR_HOSTING_RESOLVER` | `1.1.1.1,8.8.8.8` | IP resolver publik, dipisah koma, divalidasi `ipaddress` |
| `WPMGR_BACKUP_TUJUAN` | `lokal` | kunci `TUJUAN` |
| `WPMGR_BACKUP_HARIAN` | 7 | 1–60 |
| `WPMGR_BACKUP_MINGGUAN` | 4 | 0–52 |

`Settings.hosting_aktif = staging_aktif and bool(hosting_ipv4)`, dan `Settings.jalur_hosting`.
`.env.example` diperbarui.

---

## 15. Cron

| Perintah CLI | Jadwal | Isi |
|---|---|---|
| `hosting-cek-dns` | `*/10 * * * *` | Untuk setiap baris `menunggu_dns`: `periksa_dns`, simpan hasil. Bila lolos, backoff mengizinkan, dan tidak ada job hosting aktif: antrekan `pindah_aktifkan` di bawah kontrak kunci §10.7 |
| `backup-hosting` | `30 2 * * *` | Antrekan `backup_hosting` untuk setiap baris dengan `dilayani_vps_pada` terisi; pelanggaran `uq_jobs_hosting_aktif` = dilewati |
| `renew-hosting-certs` | `50 3 * * *` | `prod_sertifikat` per baris yang dilayani VPS; isi `sertifikat_pada` bila `terbit`/`diperbarui`; kegagalan → log + aktivitas `warning` |

Setiap perintah memakai kunci advisory cron (`wpmgr.kunci`) seperti perintah staging, dan keluar
diam-diam bila fitur mati. Zona waktu sistem VPS adalah `Europe/Berlin`. Karena itu `deploy/crontab` memakai
`CRON_TZ=Asia/Jakarta` untuk baris hosting, sehingga jadwal di tabel ini adalah WIB (A15 terverifikasi). Jendela retensi
dihitung dalam UTC.

---

## 16. Penanganan Error

| Keadaan | Perilaku |
|---|---|
| Connector lama putus/timeout di tengah tarik | ulangi potongan (3×), lalu job diulang dari kemajuan; site lama tidak tersentuh |
| Hosting lama sudah tidak terjangkau saat aktivasi | langkah `tarik` gagal; UI menawarkan **Aktifkan tanpa salin ulang** dengan konfirmasi domain |
| Domain sudah menunjuk VPS saat Pindahkan | ditolak: `ip_lama` tidak bisa ditentukan |
| DNS belum/sebagian berpindah, resolver berbeda, AAAA lama, CAA | tetap `menunggu_dns`, instruksi per record |
| Sertifikat gagal | tetap `menunggu_dns`, backoff 1→2→4→6 jam; site tidak pernah aktif tanpa HTTPS |
| `prod-domain`: `nginx -t` gagal, nama bentrok, reload gagal | berkas lama dipulihkan, `GALAT nginx`/`ditolak`, pesan tetap "Konfigurasi nginx domain ditolak; site lain tidak terpengaruh" |
| `prod-aktifkan` ditolak (keluar 3) | penanda tulis-lebih-dulu dihapus, status `menunggu_dns` |
| Terputus sesudah `tukar` dikirim | diulang ≤ 15 menit sampai 24 jam (R26), lalu `gagal` asal `produksi` + chip tingkat 1 |
| Verifikasi gagal (500, 401) | sama dengan baris di atas; rollback = DNS kembali ke hosting lama |
| Disk/RAM tidak cukup | ditolak sebelum mulai dengan angka yang jelas (`cek_disk`/`cek_ram`) |
| Backup gagal | log `error` + chip `backup_gagal`; tidak ada pemangkasan; backup lama utuh |
| Skrip pembantu tenggat/galat tak terduga | pesan tetap per subperintah (`AKSI`), stderr hanya ke log server (F20) |
| Worker dihentikan saat deploy | `GalatBerhenti`, dilanjutkan otomatis tanpa memakan jatah percobaan |

---

## 17. Keamanan dan Batas Kepercayaan

**Batas kepercayaan:**

| Pihak | Dipercaya untuk | Tidak dipercaya untuk |
|---|---|---|
| Connector hosting lama | menyajikan isi site-nya sendiri | apa pun yang ditulis ke disk VPS: semua balasan lewat `rencana`/`aman`, SQL lewat `periksa_sql`, impor sebagai user DB situs saja |
| Kode PHP di `wpp-<nama>` | melayani site-nya | host, situs lain, staging, DB situs lain (iptables §7.2, user DB terbatas, mount terbatas, UID dashboard tanpa capability); semua yang ditulisnya ke `files/`/`log/` dibaca dashboard lewat `aman` dan root lewat `setpriv` |
| Proses dashboard (`wpmgr`) | mengatur alur | Docker, nginx host, sertifikat, backup: hanya lewat subperintah `prod-*` dengan argumen tervalidasi; tidak bisa menghapus/menimpa situs `MODE=aktif` atau merebut domain yang sudah dilayani konfigurasi nginx lain |
| DNS publik | memberi tahu ke mana domain menunjuk | nilai mentahnya tidak pernah ditampilkan atau dipakai tanpa validasi `ipaddress` |
| Root (skrip pembantu) | satu-satunya penulis nginx host, sertifikat, state, backup | — |

**Aturan:**

- Setiap argumen skrip divalidasi (§7.3.2) di Python dan di bash. Perintah disusun sebagai array,
  tanpa `eval`.
- I/O tanpa mengikuti symlink:
  - dashboard menulis `files/` hanya lewat `aman`;
  - root menulis `wp-config.php` lewat `tulis_wp_config` (sebagai user, `mktemp` + `mv -fT`);
  - root membaca `files/` hanya lewat `tar` sebagai user;
  - sumber mount diperiksa `cek_mount_pengguna`/`cek_mount_root`;
  - direktori sertifikat dan backup diperiksa `cek_mount_root` sebelum ditulis.
- `BACKUP_DIR`, `PROD_CERT_DIR`, dan `KONF_DIR/prod` milik root 0700. Container tidak pernah
  memasangnya, dan dashboard tidak bisa membacanya.
- Kunci privat domain 0600 root:root (path tetap, dibaca master nginx). Ini lebih ketat daripada
  staging (0640 grup nginx).
- Kata sandi pratinjau: bcrypt di DB, htpasswd di router, ditampilkan sekali. Kata sandi DB situs
  dan root prod hanya di `KONF_DIR/prod` dan `wp-config.php`, tidak pernah ke dashboard atau
  browser.
- Secret connector produksi ada di database salinan. Ini sengaja (§4.1); trust level-nya sama
  dengan produksi itu sendiri.
- Pesan UI selalu teks tetap: tanpa path VPS, stderr, atau nilai mentah DNS/connector.
- `nginx -T` dan template tetap mencegah dashboard (bahkan yang disusupi) membuat konfigurasi
  nginx bebas. Yang bisa ia lakukan hanya menambah server untuk domain baru yang belum dipakai
  site lain, dan itu tidak berpengaruh sampai DNS domain itu menunjuk VPS.

---

## 18. Strategi Testing

### 18.1 Unit (Python)

- `aman.domain_sah`: huruf besar, titik akhir, baris baru, `..`, label > 63, total > 253,
  di bawah `staging.halosocia.my.id`, punycode.
- Turunan `domain`/`dengan_www`/`nama` dan tabrakan nama `vps-*` dengan staging.
- `periksa_dns` dengan resolver tiruan: A cocok, A ganda, AAAA lama, AAAA = IPv6 VPS, www lewat
  CNAME, resolver berbeda, CAA tanpa/ dengan letsencrypt, timeout.
- Backoff sertifikat.
- `pilih_simpan`: celah hari, pergantian tahun ISO, backup manual, yang terbaru selalu disimpan.
- `TujuanLokal`: urai keluaran (manifest rusak/melebihi batas ditolak).
- `Pembantu.prod_*`: validasi argumen dan pemetaan kode keluar 10/11.
- `periksa_info` hosting: http, path, host lain, `www`.
- `SiteClient(alamat_tetap=...)`: URL ke IP, header `Host`, `sni_hostname` (server TLS lokal
  dengan sertifikat untuk nama uji).
- `KlienLamaBacaSaja` tidak punya metode tulis.
- `ringkas_kemajuan` untuk tahap hosting.

### 18.2 PHPUnit (PHP 8.3 dan 7.4)

`PratinjauTest`:
- diam tanpa `WPMGR_PRATINJAU`;
- `pre_wp_mail` false;
- penerima PHPMailer dikosongkan;
- `blog_public` 0;
- penggantian URL hanya untuk host pratinjau, termasuk bentuk ber-escape JSON.

### 18.3 Integrasi (PostgreSQL)

Dengan connector dan skrip pembantu tiruan (`staging_palsu.py` diperluas):

- `pindah_tarik`: penuh, terputus lalu dilanjutkan, batal, ditolak bila `dilayani_vps_pada`.
- `pindah_aktifkan`:
  - DNS belum lolos (status tetap);
  - sertifikat gagal (backoff, tanpa tarik);
  - tarik dipatok ke `ip_lama`;
  - keluar 3 di `tukar` (penanda dihapus);
  - terputus sesudah `tukar` diulang melewati `max_attempts` sampai 24 jam lalu `gagal` produksi;
  - `tanpa_tarik_ulang` wajib konfirmasi;
  - verifikasi gagal.
- `backup_hosting`: sukses + pangkas; gagal tidak memangkas; disk penuh ditolak; idempoten per
  stempel.
- Antrean:
  - `pindah_tarik` berjalan bersama scan;
  - `backup_hosting` eksklusif;
  - `_menahan` sesudah `tukar`;
  - `uq_jobs_hosting_aktif`;
  - worker umum tidak mengklaim job hosting.
- Reaper `_lepas_hosting`.
- Cron: cek DNS mengantrekan sekali, backup harian, sertifikat.
- API: semua 401; alur status; DELETE ditolak sesudah dilayani VPS.
- Penghapusan site ditolak.
- Chip Kesehatan.
- Test `staging_tarik` Lapis 3 yang ada tetap hijau setelah refaktor `tarik_inti`.

### 18.4 Skrip pembantu (bats)

Palsu baru: `nginx` (mode `-t` sukses/gagal/peringatan bentrok, `-T` dengan berkas lain),
`systemctl`, `flock`, `openssl`. Test:

- Argumen tidak sah ditolak di setiap `prod-*`.
- `prod-hapus`, `prod-db-buat`, dan `prod-db-impor` ditolak saat `MODE=aktif`.
- `prod-buat` menolak domain yang dipakai state lain dan container berlabel lain; perintah docker
  persis sesuai harapan (memori, user, cap-drop, mount).
- `prod-domain`: pra-cek `nginx -T` menolak nama milik berkas lain; `nginx -t` gagal →
  berkas lama pulih; peringatan `conflicting server name` → pulih; reload gagal → pulih + reload;
  tidak pernah menulis `default_server`; flock dipegang.
- `prod-sertifikat`: izin 0600 root:root; reload hanya bila berubah.
- `prod-aktifkan`: prasyarat gagal keluar 3 tanpa perubahan apa pun; urutan (1)–(4); idempoten.
- `prod-backup`: `tar` dijalankan lewat `setpriv` sebagai UID dashboard; `.tmp` → final; stempel
  sama idempoten; stempel tidak sah ditolak.
- `prod-backup-hapus` menolak symlink.
- `prod-siapkan`: aturan iptables (loopback ACCEPT sebelum DROP, rantai antar), keluar 0 bila
  `HOSTING_DIR` kosong.
- Subperintah staging menolak container berlabel `wpmgr.hosting`.
- Seluruh 51 test staging lama tetap lulus.

### 18.5 E2E (Docker lokal)

Image `tests/e2e/pembantu` ditambah `nginx` dan `openssl`. Konfigurasi:
- `NGINX_UJI_SAJA=1`: `nginx -t` sungguhan atas `nginx.conf` minimal yang meng-include
  `NGINX_HOSTING_DIR`;
- `SERTIFIKAT_SENDIRI=1`.

Alur:
1. Pindahkan WordPress e2e yang ada (domain uji `pindah-e2e.test`).
2. Pratinjau lewat router `127.0.0.1:8091` dengan `Host: vps-<nama>.<domain staging>`: tanpa
   kredensial 401, dengan kredensial 200 + `noindex`, email diblokir (`wp_mail` false lewat
   halaman uji).
3. Cek DNS dengan resolver tiruan (injeksi).
4. Aktifkan.
5. Lewat router dengan `Host: pindah-e2e.test`: 200, tanpa 401/noindex, mu-plugin hilang,
   `wp-config.php` tanpa `WPMGR_PRATINJAU`, `REMOTE_ADDR` = IP dari `X-Forwarded-For` (asumsi A2).
6. Backup → berkas ada dan sha256 cocok dengan manifest.
7. Tarik sesudah aktif ditolak (dashboard dan skrip).

### 18.6 Verifikasi manual bersama pengguna di VPS

1. `prod-siapkan`, pasang `wpmgr-hosting.conf`, `nginx -t`.
2. Pindahkan `rizkycahayaraya.com`.
3. Pratinjau (URL + berkas hosts).
4. Ubah DNS (A + hapus AAAA).
5. Aktivasi otomatis.
6. Cek dari ponsel ber-IPv6 dan IPv4.
7. Backup manual.
8. Satu kali latihan pemulihan §9.4 di site itu sebelum hosting lama dimatikan.

Setelah itu dua site lainnya.

---

## 19. Deployment dan Perubahan README

1. Merge, `alembic upgrade head`, `pip install -e .` (dnspython). Connector tidak perlu diperbarui:
   template pratinjau hanya dibaca dashboard.
2. Pasang ulang `/usr/local/sbin/wpmgr-staging` (root:root 0755). Sudoers tidak berubah.
3. Tambah kunci §7.3.3 ke `/etc/wpmgr-staging/staging.conf`. Siapkan direktori:
   `install -d -o wpmgr -g wpmgr -m 0700 /var/lib/wpmgr/hosting`.
4. `wpmgr-staging prod-siapkan`. Tambah baris kedua `ExecStart=/usr/local/sbin/wpmgr-staging
   prod-siapkan` ke `wpmgr-staging-siapkan.service`, lalu `systemctl daemon-reload`.
5. Pra-cek nginx:
   - `grep -rn 'allow\|deny' /etc/nginx/sites-enabled` (asumsi A5: tidak ada vhost yang memercayai
     rentang privat);
   - `nginx -T | grep -n server_name` untuk tiga domain (harus kosong).

   Lalu pasang `deploy/staging/nginx-wpmgr-hosting.conf` ke `/etc/nginx/sites-enabled/`,
   `install -d -m 0755 /etc/nginx/wpmgr-hosting`, `nginx -t && systemctl reload nginx`.
6. Isi variabel §14 di `.env`. Tambah cron §15 ke `deploy/crontab`. Restart web dan worker
   (termasuk `wpmgr-worker@staging`).

**README:** bagian baru **"Pindah hosting (Lapis 4)"** setelah "Staging (Lapis 3)", berisi:
- prasyarat;
- langkah pemasangan di atas;
- alur pengguna;
- tabel record DNS dan alasan AAAA;
- catatan TTL;
- pemulihan backup manual §9.4;
- melepas site aktif secara manual (hapus berkas nginx domain + reload, `docker rm -f wpp-<nama>`,
  `DROP DATABASE`, hapus state, lalu `DELETE FROM hosting_vps` lewat `psql`);
- batas §21.

"Keterbatasan yang diketahui" diperbarui. Struktur repo menyebut `src/wpmgr/hosting/`.

**Berkas baru:**
- `src/wpmgr/hosting/{__init__,umum,dns,pindah,backup,cron}.py`
- `src/wpmgr/web/routes_hosting.py`
- `src/wpmgr/templates/_tab_hosting.html`, `src/wpmgr/static/app/hosting.js`
- `connector/wp-manager-connector/templates/wpmgr-pratinjau.php.tpl`, `connector/tests/PratinjauTest.php`
- `deploy/staging/nginx-wpmgr-hosting.conf`
- `deploy/staging/tests/palsu/{nginx,systemctl,flock,openssl}`
- migrasi §5.2
- test §18

**Berkas diubah:**
- `models.py`, `config.py`, `jobs/queue.py`, `jobs/reaper.py`, `worker.py`, `jobs/handlers.py`
- `site_client.py`
- `staging/tarik.py`, `staging/umum.py`, `staging/pembantu.py`, `connector_paket.py`
- `web/routes_staging.py` (`_nama_unik`, `ringkas_kemajuan`), `web/routes_api.py` (hapus site), `web/app.py`
- `kesehatan.py`, `cli.py`
- `templates/site_detail.html`, `static/app/detail.js`, `static/app/kesehatan.js`
- `deploy/staging/wpmgr-staging`, `deploy/staging/staging.conf.contoh`,
  `deploy/staging/wpmgr-staging-siapkan.service`, `deploy/crontab`
- `pyproject.toml`, `.env.example`, `README.md`, `docker-compose.yml`, `tests/e2e/pembantu/Dockerfile`

---

## 20. Catatan Keputusan

| Keputusan | Alasan |
|---|---|
| Container + mesin Lapis 3, bukan native di host (pendekatan A) | Disetujui pengguna; isolasi, hardening, dan tarik sudah teruji. |
| Subperintah `prod-*` di `wpmgr-staging`, bukan skrip saudara | Fungsi keamanan, sudoers, konfigurasi, dan bats dipakai ulang; tanpa pustaka bersama yang harus divalidasi (§7.3). |
| Pengaman pratinjau di luar database | Aktivasi tidak perlu mengembalikan nilai DB; tidak ada risiko `blog_public=0` atau URL pratinjau tertinggal di produksi. |
| Tanpa search-replace | Database tetap memakai URL asli; host pratinjau ditangani `WP_HOME` bersyarat + penggantian keluaran. |
| Host pratinjau `vps-<nama>.<staging domain>` lewat server nginx bernama tepat | Wildcard DNS dan port 80 ACME staging sudah ada; nama tepat mengalahkan wildcard tanpa mengubah berkas staging; tidak melewati router staging (isolasi). |
| Berkas nginx dua mode (pratinjau/aktif), port 80 ACME sejak pratinjau | Tantangan HTTP-01 siap begitu DNS berpindah; aktivasi hanya mengganti satu berkas. |
| Pra-cek `nginx -T` + deteksi `conflicting server name` + rollback | `nginx -t` sendiri tidak gagal pada nama ganda; domain milik site lain tidak boleh direbut diam-diam. |
| Aktivasi otomatis setelah DNS lolos | Begitu DNS berpindah, setiap menit tanpa aktivasi adalah gangguan bagi pengunjung. |
| `ip_lama` dipatok + klien baca-saja | Tarik ulang sesudah DNS berpindah tetap mengambil dari hosting lama; site lama tidak mungkin diubah. |
| Penanda `dilayani_vps_pada` (dashboard) + `MODE=aktif` (root) | Data baru di VPS tidak pernah ditimpa salinan lama, bahkan oleh dashboard yang disusupi. |
| AAAA wajib dihapus | Let's Encrypt mendahulukan IPv6 dan pengunjung IPv6 akan terbelah ke hosting lama. |
| Pengecualian loopback ke 80/443 IP publik | WP-Cron, Site Health, dan proses latar plugin bergantung pada request ke diri sendiri. |
| Backup root-only, `tar` sebagai user | Container dan dashboard tidak bisa membaca/merusak backup; root tidak pernah menelusuri pohon yang dikendalikan situs. |
| Status kerja `mengaktifkan` | Diperlukan untuk aturan R26 dan untuk mencegah salin ulang/hapus selama peralihan. |
| `dnspython` | Satu-satunya cara wajar menanyakan resolver publik tertentu dan CAA dari Python. |

---

## 21. Risiko dan Batas Terbuka

| Risiko/batas | Dampak | Mitigasi |
|---|---|---|
| Jendela antara DNS berpindah dan aktivasi | Pengunjung yang resolvernya sudah berpindah melihat peringatan sertifikat (≤ 10 menit + terbit + tarik ulang) | Tombol cek segera; turunkan TTL lebih dulu; aktivasi otomatis |
| Data yang masuk ke hosting lama sesudah tarik terakhir (pengunjung dengan DNS basi) | Isian form/komentar itu tidak ikut | Company profile jarang menulis; README menyarankan memeriksa entri form di hosting lama sebelum mematikannya |
| Rollback DNS sesudah aktif | Data baru di VPS tidak kembali ke hosting lama | Dijelaskan di UI dan README |
| Backup di disk yang sama dengan situs | Kerusakan disk/VPS menghilangkan situs dan backup sekaligus | `TujuanBackup` siap untuk S3/R2; sementara itu README menyarankan menyalin backup keluar secara berkala (`rsync` root) |
| `mail()` tidak berfungsi di container | Form/notifikasi yang memakai `mail()` diam-diam gagal | Pengguna menyatakan site tidak mengirim email; README: pakai plugin SMTP ke penyedia luar |
| Konstanta khusus `wp-config.php` lama hilang; salt baru | Plugin yang bergantung konstanta bisa berubah perilaku; semua pengguna harus login ulang | README: bandingkan `wp-config.php` lama lewat File Manager hPanel sebelum aktivasi |
| Plugin khusus Hostinger/LiteSpeed | Fitur cache LiteSpeed tidak aktif di Apache | Aman (blok `<IfModule LiteSpeed>`); boleh dinonaktifkan setelah pindah |
| IP hosting lama berubah sebelum aktivasi | Tarik ulang gagal | **Aktifkan tanpa salin ulang** dengan konfirmasi |
| RAM VPS bersama ERPNext | Tiga situs + DB ≈ 2,4 GB | Batas memori per container; penolakan < 2 GB |
| Certbot `--nginx` dijalankan admin lain untuk domain yang sama | Bisa menyentuh berkas di `wpmgr-hosting/` | README: jangan; berkas ditimpa ulang pada `prod-domain` berikutnya |
| Folder site lain (addon domain) di dalam `public_html` lama | Ikut tersalin, memakan disk | Peringatan ukuran di hasil tarik; tidak berbahaya |
| Status `vps-<nama>` sertifikat pratinjau tidak diperpanjang | Pratinjau > 90 hari kehilangan HTTPS | Salin ulang menerbitkan ulang |

---

## 22. Asumsi yang Belum Diverifikasi di Kode/VPS

| # | Asumsi | Cara verifikasi |
|---|---|---|
| A1 | httpx/httpcore yang terpasang menghormati `extensions={"sni_hostname": ...}` untuk verifikasi nama sertifikat | unit test §18.1 dengan server TLS lokal |
| A2 | Image `wordpress:php*-apache` resmi mengaktifkan `mod_remoteip` dan memercayai 172.16.0.0/12 | e2e §18.5 langkah 5; bila tidak, `php.ini` tidak cukup: tambah konfigurasi Apache read-only |
| A3 | Zona ketiga domain klien punya AAAA ke Hostinger seperti zona `halosocia.my.id` | lihat zona di hPanel saat langkah DNS |
| A4 | `00-default-catchall.conf` tidak mendeklarasikan nama domain klien dan menangani SNI tak dikenal di 443 | `nginx -T` di VPS |
| A5 | Tidak ada vhost di nginx host yang memercayai sumber 172.16.0.0/12 (aturan loopback) | `grep -rn 'allow' /etc/nginx` |
| A6 | `HOSTING_DIR` dan `BACKUP_DIR` berada di filesystem dengan ±140 GB kosong | `df` |
| A7 | Hostinger menerima koneksi langsung ke IP A-record dengan SNI domain (tanpa CDN yang menolak) | `curl --resolve <domain>:443:<ip> https://<domain>/wp-json/` sebelum Pindahkan |
| A8 | Connector 3.0 dengan "Izinkan staging" berjalan di ketiga hosting lama (batas PHP Hostinger) | pairing + tarik pertama |
| A9 | `/etc/letsencrypt/options-ssl-nginx.conf` ada di VPS (sudah dicatat README Lapis 3) | `ls` |
| A10 | ~~VPS tidak punya IPv6 publik~~ **Terverifikasi 2026-10-03:** VPS punya `2a02:c207:2347:2607::1`. AAAA diubah ke IPv6 itu (lihat §8.2). | `ip -6 addr` |
| A11 | `wp-config.php` lama tidak memuat konstanta penting | dicek manual per site |
| A12 | **Terverifikasi sebagian 2026-10-03.**<br>• `dutamakmurabadi.com` dan `scaffoldingsurabayamurah.com`: `www` adalah CNAME ke apex; A dan AAAA mengarah ke shared hosting Hostinger.<br>• `rizkycahayaraya.com` memakai **CDN Hostinger**: `www` adalah CNAME ke `*.cdn.hstgr.net`, dan A di apex berisi IP CDN.<br>CDN harus dimatikan di hPanel sebelum pindah DNS. Instruksi DNS harus mencakup penggantian CNAME CDN itu. Cek DNS menolak CNAME yang masih ke `hstgr.net`. | hPanel / `dns.google` |
| A13 | Wildcard `*.staging.halosocia.my.id` melayani label `vps-<nama>` (satu label, tidak butuh wildcard bertingkat) | sudah terverifikasi untuk staging; sama |
| A14 | Let's Encrypt mendahulukan IPv6 saat validasi HTTP-01 bila ada AAAA | dokumentasi LE; desain tidak bergantung padanya karena AAAA wajib bersih |
| A15 | **Terverifikasi 2026-10-03:** zona waktu VPS adalah `Europe/Berlin`. Crontab backup memakai `CRON_TZ=Asia/Jakarta`, sehingga backup berjalan pukul 02:30 WIB. | `timedatectl` |

---

## 23. Review Focus

Mode kegagalan yang paling mungkin lolos dari test biasa, masing-masing dengan test di bagian
pemiliknya:

| # | Mode kegagalan | Test |
|---|---|---|
| RF1 | Cek DNS lolos padahal AAAA lama masih ada atau resolver belum sepakat, lalu sertifikat gagal atau pengunjung terbelah | `test_dns_aaaa_lama_menahan_aktivasi`, `test_dns_resolver_berbeda_belum_lolos` |
| RF2 | Tarik dari salinan lama menimpa produksi yang sudah dilayani VPS (klik ganda, cron, percobaan ulang, dashboard disusupi) | `test_tarik_ditolak_sesudah_dilayani_vps`, `test_aktifkan_terputus_saat_tukar_tidak_bisa_tarik_lagi`, bats `@test "prod-db-impor menolak MODE=aktif"` |
| RF3 | Berkas nginx domain merusak nginx host atau merebut nama milik site lain | bats `@test "prod-domain memulihkan berkas lama bila nginx -t gagal"`, `@test "prod-domain menolak conflicting server name"`, `@test "prod-domain menolak nama milik berkas lain di nginx -T"` |
| RF4 | Tarik ulang saat aktivasi diam-diam menyalin dari VPS sendiri karena DNS sudah berpindah | `test_aktifkan_tarik_dipatok_ke_ip_lama`, `test_klien_lama_tanpa_metode_tulis` |
| RF5 | Proses mati di antara `prod-aktifkan` dan pencatatannya, sehingga job berhenti di `max_attempts` dengan produksi setengah beralih | `test_aktifkan_terputus_sesudah_tukar_diulang_sampai_24_jam`, `test_prod_aktifkan_keluar_3_menghapus_penanda` |
| RF6 | Backup yang gagal memangkas backup baik terakhir, atau retensi salah di batas minggu/tahun | `test_backup_gagal_tidak_memangkas`, `test_pilih_simpan_pergantian_tahun_iso` |
| RF7 | Root mengikuti symlink yang ditanam situs saat backup atau menulis konfigurasi | bats `@test "prod-backup menjalankan tar sebagai UID dashboard"`, `@test "prod-db-buat tidak mengikuti symlink wp-config.php"` |
| RF8 | Pengaman pratinjau tertinggal sesudah aktif (Basic Auth, noindex, email/cron mati) | e2e langkah 5, `test_verifikasi_gagal_bila_wpmgr_pratinjau_tersisa` |

Reviewer task-task pemilik diminta memeriksa bahwa test ini ditulis lebih dulu dan terbukti RED.

---

## 24. Definisi Selesai dan Urutan Kerja

Urutan kerja (±5 hari; pemasangan Lapis 1–3 di VPS berjalan paralel bersama pengguna):

1. **Hari 1:** skrip pembantu `prod-*` + bats.
2. **Hari 2:** model/migrasi, refaktor `tarik_inti`, klien dipatok, `pindah_tarik`.
3. **Hari 3:** DNS, `pindah_aktifkan`, sertifikat, nginx, mu-plugin pratinjau.
4. **Hari 4:** backup + retensi, cron, API, tab UI, chip.
5. **Hari 5:** e2e, README, migrasi `rizkycahayaraya.com`, lalu dua site lainnya.

**Cadangan** (dari desain): bila Lapis 4 belum siap di hari 5, migrasi manual lewat FTP, lalu
pengembangan dilanjutkan.

Selesai bila:

- Semua test unit, integrasi, PHPUnit (8.3, 7.4), bats (lama + baru), dan e2e lulus; ruff bersih.
- Review per task dan review akhir tanpa temuan Critical/Important yang terbuka.
- README memuat bagian Lapis 4 lengkap, termasuk pemulihan manual.
- Verifikasi manual §18.6 dijalankan bersama pengguna, termasuk satu latihan pemulihan backup.
