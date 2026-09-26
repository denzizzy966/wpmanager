# WP Manager Lapis 3 (Staging) — Rencana Implementasi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Memberi setiap site satu salinan staging di VPS dashboard untuk uji update, preview client, tempat bekerja, dan dorong ke produksi dengan snapshot yang bisa dikembalikan.

**Architecture:** Dashboard tetap satu-satunya pengendali. Semua pekerjaan panjang adalah job di antrean Lapis 1 (tipe baru `staging_*`), diproses worker khusus staging, dengan progres yang bisa dilanjutkan. Data berpindah lewat connector 3.0 (endpoint HMAC `wpmgr/v1/staging/*`) dalam potongan ber-hash. Runtime staging (container PHP per site, MariaDB bersama, router nginx, Mailpit) hanya bisa disentuh lewat satu skrip bash `wpmgr-staging` yang dipanggil dengan `sudo -n`; proses dashboard tidak punya akses Docker.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, httpx, bcrypt; PHP 7.4+ (WordPress 5.5+) untuk connector; bash 5 + Docker untuk skrip pembantu; nginx 1.24 (host) dan nginx 1.27-alpine (router); MariaDB 11.4; Mailpit; certbot 2.9; pytest, PHPUnit 9, bats 1.11, WordPress 6.5 di Docker untuk e2e.

**Spec:** `docs/superpowers/specs/2026-09-26-wp-manager-lapis3-design.md`. Spec Lapis 1 dan 2 tetap berlaku untuk semua yang tidak diubah.

## Global Constraints

- Python `>=3.10` (bukan 3.11): tanpa `datetime.UTC`, `match`, `Self`, `ExceptionGroup`. Pakai `timezone.utc`. Semua datetime tz-aware UTC; sesi DB sudah dipatok `timezone=UTC` di `db.py`.
- PHP connector wajib jalan di PHP 7.4: tanpa `match`, named arguments, `str_contains`/`str_starts_with`/`str_ends_with`, union type, `readonly`, nullsafe `?->`, `never`. `intdiv`, `random_bytes`, `hash_equals`, closure boleh.
- Batasan spec §2: RAM VPS 11 GB (±6,4 GB tersedia), disk 193 GB, nginx host milik ERPNext tidak diubah dashboard, Docker butuh sudo, DNS Hostinger tanpa plugin certbot (HTTP-01 per staging), site 5–20 GB, akses ke site klien hanya lewat connector.
- Batas dari spec:
  - staging aktif paling banyak `WPMGR_STAGING_MAKS_AKTIF` (3); container PHP `--memory 384m --cpus 1`;
  - membuat/menjalankan staging ditolak bila RAM tersedia < 2 GiB;
  - tarik ditolak bila sisa disk sesudahnya < 15%;
  - snapshot disimpan `WPMGR_STAGING_SNAPSHOT` (3) terakhir per site;
  - jeda otomatis setelah `WPMGR_STAGING_JEDA_HARI` (3) hari tanpa akses;
  - request ke connector ≤ 30 detik dan ≤ 8 MB; paket file ±8 MB; hash hanya untuk file ≤ 50 MB; ekspor tabel 2.000 baris per potongan; ulang per potongan 3×; `max_attempts` job 3;
  - `.maintenance` berlaku paling lama 15 menit; area sementara connector dibersihkan cron setelah 24 jam.
- Variabel spec §10 dan default-nya: `WPMGR_STAGING_DOMAIN` (kosong = fitur mati), `WPMGR_STAGING_DIR` (`/var/lib/wpmgr/staging`), `WPMGR_STAGING_PEMBANTU` (`/usr/local/sbin/wpmgr-staging`), `WPMGR_STAGING_MAKS_AKTIF` (3), `WPMGR_STAGING_JEDA_HARI` (3), `WPMGR_STAGING_SNAPSHOT` (3), `WPMGR_STAGING_EMAIL_ACME` (kosong; lihat Koreksi #7).
- `nama` staging wajib cocok `[a-z0-9-]{1,40}` (Python `re.fullmatch`, PHP `/^[a-z0-9-]{1,40}\z/`, bash `=~ ^[a-z0-9-]{1,40}$` dengan `LC_ALL=C`). Versi PHP staging dari daftar tetap `7.4 8.0 8.1 8.2 8.3`.
- Regex validasi memakai `[0-9]`/`[a-z0-9-]` dan `fullmatch` (Python) atau `\z` (PHP), tidak pernah `\d` atau `$` (kecuali di bash, lihat butir sebelumnya).
- **Setiap respons connector adalah masukan penyerang.** Dashboard memvalidasi tipe, menjepit angka ke rentang kolom, membuang NUL dan surrogate tunggal sebelum menyimpan teks ke PostgreSQL (`wpmgr.staging.aman.bersih_teks`), membatasi ukuran stream, dan tidak pernah memercayai path dari connector saat menulis ke `files/` (`wpmgr.staging.aman.jalur_di_dalam`). Satu item rusak dilewati dan dicatat sebagai peringatan, tidak menggagalkan job selamanya.
- Di sisi PHP, string dari sistem berkas atau database yang dikembalikan sebagai teks dibersihkan ke UTF-8 sah lewat `WPMGR_Penangkap::potong()` sebelum dipotong. Path yang bukan UTF-8 sah tidak dikirim sebagai path, hanya dihitung sebagai "dilewati".
- Konkurensi: operasi atomik di DB (upsert, `rowcount`, indeks unik parsial) alih-alih baca-lalu-tulis. Di PHP, buat-jika-belum-ada memakai `INSERT IGNORE` + baca ulang, bukan `add_option`. Transient kembali sebagai string.
- Setiap tunggu pada subprocess atau HTTP punya tenggat keras; stream dibatasi ukurannya.
- Pesan galat yang tampil di UI tidak pernah memuat path sistem berkas VPS atau kredensial. Galat skrip pembantu dipetakan ke pesan tetap.
- Setiap route baru punya test akses anonim (401). Semua route JSON dibatasi jumlah barisnya dan memfilter `site_id`.
- Tanpa npm, tanpa CDN. Semua string dari site dirender lewat autoescape Jinja2, `x-text`, atau `esc()`. Dilarang `|safe`, `x-html`, `innerHTML`.
- Semua komentar kode, pesan log, pesan error, dan teks UI berbahasa Indonesia; komentar menjelaskan *mengapa*.
- Kontrak HMAC Lapis 1 tidak berubah: path yang ditandatangani `/wp-json` + route, tanpa query string. Metadata yang mengubah sesuatu selalu di body (ditandatangani), tidak pernah di query atau header.
- **Bukti RED ditempel mentah.** Setiap laporan task menyertakan keluaran test yang gagal sebelum perbaikan dan yang lulus sesudahnya.
- **Konvensi blok kode:** baris `File: <path>` di atas blok kode menunjukkan berkas tujuannya dan bukan isi berkas.
- Perintah (dari akar repo, Git Bash di Windows):
  - Unit: `.venv/Scripts/python -m pytest -m "not integration and not e2e" -q`
  - Integrasi (butuh `docker compose up -d db`): `.venv/Scripts/python -m pytest -m integration -q`
  - E2E (butuh `docker compose up -d`): `.venv/Scripts/python -m pytest -m e2e -q`
  - PHP 8.3: `cd connector && vendor/bin/phpunit`
  - PHP 7.4: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/app" -w /app/connector php:7.4-cli php vendor/bin/phpunit`
  - Skrip pembantu: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" -w /code bats/bats:1.11.0 deploy/staging/tests`
  - Lint: `.venv/Scripts/python -m ruff check .`
- Pesan commit diakhiri baris kosong lalu `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Koreksi terhadap spec yang ditemukan saat menyusun rencana

1. **Worker khusus staging dan aturan klaim baru** (spec §11). Deploy memakai dua worker (`wpmgr-worker@1`, `@2`) dan reaper menganggap job yatim setelah `locked_at` 15 menit. Job tarik 20 GB berjalan berjam-jam: tanpa perubahan, ia memakan satu dari dua worker dan direbut reaper. Karena itu: (a) instans `wpmgr-worker@staging` hanya mengklaim job `staging_*`, worker lain tidak pernah mengklaimnya; (b) handler staging memperbarui `locked_at` setiap potongan (`detak`) dan berhenti bila klaimnya hilang; (c) `staging_tarik` dan `staging_uji_update` hanya membaca produksi, jadi boleh berjalan bersamaan dengan job non-staging di site yang sama, sedangkan `staging_dorong` dan `staging_kembalikan` tetap eksklusif terhadap semua job site itu; (d) paling banyak satu job staging tertunda/berjalan per site, dijaga indeks unik parsial `uq_jobs_staging_aktif` (atomik, bukan periksa-lalu-sisipkan).
2. **Progres berkas disimpan di indeks lokal, bukan `job.payload.file_selesai`** (spec §6.4). Site 20 GB bisa punya 100.000+ berkas; menulis ulang daftar itu ke JSONB setiap potongan terlalu mahal. `<site_id>/indeks.jsonl` (append-only, dipadatkan di akhir) mencatat setiap berkas yang sudah tertulis di `files/`. Melanjutkan = menghitung ulang selisih manifest terhadap indeks. `job.payload["kemajuan"]` tetap menyimpan `tahap`, kursor manifest, `tabel: {nama: kursor}`, `byte_selesai`, `byte_total`, dan commit per potongan.
3. **Pembatalan lewat kolom `staging.batal_diminta_pada`** (spec §6.4). Lapis 1 tidak punya mekanisme batal job. Handler memeriksa kolom ini di antara potongan, membersihkan area sementara, lalu gagal dengan pesan "Dibatalkan".
4. **Kolom tambahan** di luar spec §5.1: `staging.rahasia_router_terenkripsi` (Koreksi #5), `staging.dorong_gagal_pada` (sumber chip `dorong_gagal`), `staging.batal_diminta_pada`, `staging_snapshot.detail jsonb` (mode dorong dan daftar berkas yang ditambahkan dorongan), dan `staging_uji.job_id` dibuat nullable `ON DELETE SET NULL` supaya pemangkasan job tidak menghapus riwayat uji.
5. **SSO dan probe uji melewati Basic Auth dengan tautan bertanda tangan** (spec §7.1, §9). Preview dilindungi Basic Auth, tetapi dashboard hanya menyimpan hash bcrypt, jadi tombol "SSO ke admin staging" dan probe halaman uji update tidak bisa mengetik kata sandi. Router memakai modul `secure_link` nginx: dashboard membuat tautan `/__wpmgr_masuk?e=<exp>&m=<md5>&sso=<token>` dari rahasia per staging (disimpan terenkripsi Fernet), router memverifikasinya lalu memasang cookie yang mematikan `auth_basic` sampai kedaluwarsa (12 jam).
6. **Konfigurasi router dirender skrip pembantu dari template tetap** (spec §7.3 `router-muat`). Skrip berjalan sebagai root; ia tidak boleh memercayai teks konfigurasi nginx yang ditulis user `wpmgr`. Dashboard hanya menyerahkan dua berkas per staging di `<WPMGR_STAGING_DIR>/router/`: satu baris htpasswd bcrypt dan rahasia 64 hex. Keduanya divalidasi regex ketat sebelum dipakai.
7. **Argumen dan konfigurasi skrip pembantu** (spec §7.3):
   - `buat <nama> <versi_php> <site_id>` dan `db-buat <nama> <site_id> <prefix>`, karena skrip harus tahu direktori `files/` dan `$table_prefix`;
   - `wpcli <nama> search-replace <dari> <ke> [--export]`;
   - `status` menambahkan waktu akses terakhir per staging dari log akses router.
   Domain, direktori, UID pemilik, dan email ACME dibaca dari `/etc/wpmgr-staging/staging.conf` milik root, sehingga `WPMGR_STAGING_EMAIL_ACME` tidak dibaca dashboard. Variabelnya tetap diterima `Settings` supaya README tetap sesuai spec §10; README menyebut bahwa nilainya disalin ke `ACME_EMAIL` di berkas itu.
8. **`db-impor` berjalan sebagai user MariaDB staging itu, bukan root.** Nama database dan user adalah `stg_<nama>` dengan `-` diganti `_`, supaya tidak perlu dikutip di setiap pernyataan. SQL berasal dari site produksi yang bisa saja disusupi. Dengan akun root, SQL itu bisa menghapus database staging site lain atau membuat user baru. User `stg_<nama>` hanya punya hak DML/DDL atas database-nya sendiri (tanpa TRIGGER, EVENT, ROUTINE, FILE). Kata sandinya disimpan root-only di `/etc/wpmgr-staging/db/<nama>` selain di `wp-config.php` staging.
9. **`ssl_certificate` di nginx host memakai `map`** atas `$ssl_server_name` yang divalidasi regex (spec §7.2). Memakai `$ssl_server_name` langsung membuat SNI kiriman klien (mis. `../../etc/x`) ikut menentukan path berkas yang dibuka nginx sebagai root.
10. **`.maintenance` berlaku 15 menit lewat `$upgrading = <waktu dibuat> + 300`** (spec §6.3). WordPress sendiri mengabaikan berkas itu 10 menit setelah `$upgrading` (`wp_is_maintenance_mode()`). Selama berkas itu ada, WordPress juga menjawab 503 untuk request REST connector. Karena itu `.maintenance` dan mu-plugin sementara `wpmgr-dorong-aman.php` sama-sama memeriksa header `X-Wpmgr-Lewati` terhadap hash token yang dikirim dashboard di body `tukar` (ditandatangani). Untuk request itu, maintenance dilewati dan hanya plugin connector yang dimuat, tanpa plugin lain dan tanpa tema, supaya plugin yang sedang ditukar setengah jalan tidak membuat connector ikut fatal.
11. **Terapkan dipecah menjadi langkah** `siapkan`/`impor`/`tukar`/`pulihkan`/`selesai`, masing-masing dengan kursor di `keadaan.php` connector, karena batas 30 detik per request (spec §6.1). Rencana dorong (daftar berkas, hash, hapus) diunggah sebagai potongan berjenis `rencana`, karena daftar untuk timpa penuh bisa melebihi 8 MB.
12. **`/staging/snapshot` mengembalikan metadata**: ada/ukuran/mtime untuk path yang akan tertimpa, daftar tabel, dan tanda air. Isi berkas dan tabel diambil lewat `/staging/file` dan `/staging/tabel`, dengan potongan dan hash yang sama seperti tarik (spec §6.1 "dipotong seperti tarik").
13. **Kembalikan snapshot "hanya kode" hanya memulihkan berkas** (spec §8.3). Memulihkan database dari dorongan hanya-kode akan menghapus pesanan/komentar yang masuk sesudahnya, padahal dorongan itu tidak pernah mengubah database. Ekspor database tetap ada di snapshot untuk pemulihan manual, dan dialog Kembalikan menyebut hal ini.
14. **Yang tidak pernah didorong** (spec §6.3):
    - plugin connector (`wp-content/plugins/wp-manager-connector/`), mu-plugin staging, `wp-config.php`, `.maintenance`;
    - tabel `{prefix}wpmgr_*`; opsi produksi `siteurl`, `home`, `blog_public`, dan `wpmgr_*` dipertahankan saat impor timpa penuh, karena staging memaksa `blog_public=0` dan menyimpan data pemantauan salinan lama;
    - timpa penuh tidak menghapus berkas `wp-content/uploads/` yang hanya ada di produksi (lampiran pesanan baru); ia hanya menambah dan menimpa;
    - penulisan di luar `wp-content` dibatasi pada `wp-admin/`, `wp-includes/`, `index.php`, `wp-*.php`, `xmlrpc.php`, `license.txt`, `readme.html`, `.htaccess`.
15. **Potongan unggah paling besar 4 MB**, atau setengah `post_max_size` bila lebih kecil (dilaporkan manifest sebagai `batas_unggah`), karena body 8 MB ditolak PHP di hosting dengan `post_max_size=8M` (spec §6.1).
16. **Log PHP staging dan penanda "diubah" dibaca langsung dari `<site_id>/log/`**, sebuah bind mount milik user `wpmgr`, bukan lewat skrip pembantu. `diubah_pada` (spec §5.1 "dari connector staging") diisi dari berkas `log/diubah` yang ditulis mu-plugin staging pada `save_post`, aktivasi plugin, upgrader, dan sejenisnya.
17. **Mailpit tidak diproksikan utuh** (spec §7.1). UI Mailpit adalah SPA dengan websocket dan path absolut. Dashboard menyediakan daftar email (maks 50) dan isi teksnya per staging lewat API Mailpit, disaring dengan tag `X-Tags: <nama>` yang dipasang mu-plugin staging.
18. **Variabel dev/test tambahan:**
    - `WPMGR_STAGING_PEMBANTU_AWALAN` (bila diisi, dipakai sebagai pengganti `sudo -n <pembantu>`);
    - `WPMGR_STAGING_ROUTER_URL` (default `http://127.0.0.1:8090`, untuk probe uji dan cek router);
    - `WPMGR_STAGING_MAILPIT_URL` (default `http://127.0.0.1:8025`).
    E2E di Windows menjalankan skrip pembantu di dalam container `pembantu` (image `docker:27-cli` + bash) yang memakai socket Docker Desktop. Konfigurasinya memakai `AKAR_LOKAL`/`AKAR_DAEMON` untuk menerjemahkan path bind mount, `PENGGUNA_UID=33`, `TANPA_IPTABLES=1`, dan `TANPA_SERTIFIKAT=1`.
19. **Kelas galat baru** `staging_mati`, `staging_ditolak`, `staging_gagal`. Tanpa `staging_mati`, balasan 403 `wpmgr_staging_mati` dibaca `klasifikasi_respons()` sebagai `auth_error` dan site produksi yang sehat berubah menjadi `needs_reconnect`. Kegagalan kelas staging tidak menimpa `site.last_error`.
20. **`/staging/tanda-air?posts_sejak=<waktu>`** menghitung post yang diubah sejak tarik, supaya pesan "2 post diubah" (spec §8.2) punya angka. Tanda air disimpan dari awal tahap database tarik, bukan akhir, supaya data yang masuk selama ekspor ikut terdeteksi.
21. **Urutan task menjadi 22**:
    - sisi dorong connector dipecah menjadi unggah/snapshot (Task 7) dan terapkan (Task 8), karena masing-masing batas review yang besar;
    - infrastruktur job staging (antrean, worker, detak, batal, log aktivitas) menjadi Task 13 sebelum job pertama;
    - pembungkus pembantu (Task 11) membawa modul validasi `aman.py` yang dipakai semua task sesudahnya.
22. **Staging dengan `WP_CONTENT_DIR` di luar `ABSPATH` atau multisite ditolak** dengan pesan jelas, karena spec §3 mengecualikan multisite dan manifest hanya menelusuri `ABSPATH`.
23. **Ekspor tabel memakai escaping sendiri, bukan `$wpdb->prepare`/`esc_sql`** (spec §6.2 langkah 3). Aturannya sama dengan `mysqli_real_escape_string`, ditambah literal hex untuk kolom biner dan byte bukan UTF-8. Hasilnya deterministik (bisa diuji tanpa MySQL) dan tidak bergantung pada charset koneksi impor; `esc_sql` butuh koneksi aktif dan mengirim byte biner mentah.
24. **Snapshot disimpan di `<site_id>/snapshot/j<job_id>/`** (spec §5.2 `snapshot/<id>/`). Id baris snapshot belum ada saat berkasnya mulai diambil; id job sudah ada dan tetap sama saat job dilanjutkan.

## Yang tidak dapat dipenuhi spec secara harfiah

- **Konsistensi transaksi lintas potongan** (spec §6.2 "dalam transaksi REPEATABLE READ"). Setiap potongan adalah request HTTP tersendiri, jadi transaksi hanya konsisten di dalam satu potongan (maks 2.000 baris). Mitigasinya:
  - tanda air diambil sebelum ekspor database;
  - dorong timpa penuh selalu mengecek ulang tanda air;
  - tabel tanpa primary key diekspor dengan `LIMIT/OFFSET`, yang bisa melewatkan atau menggandakan baris bila tabel itu ditulisi selama tarik. Hal ini dicatat sebagai peringatan di hasil job.
- **Email yang dikirim plugin lewat API HTTP** (SendGrid API, Mailgun API) tidak lewat PHPMailer dan tidak tertangkap Mailpit. Spanduk admin staging menyebut hal ini (sudah ada di spec §17 sebagai risiko).
- **Foreign key antar tabel** yang dirujuk dengan nama asli tetap menunjuk nama asli setelah `RENAME TABLE`. WordPress core dan WooCommerce tidak memakai foreign key; plugin yang memakainya bisa gagal diimpor. Galatnya dilaporkan per pernyataan.

## Review Focus

Lima mode kegagalan yang paling mungkin lolos dari test biasa, masing-masing dengan test di task pemiliknya:

| # | Mode kegagalan | Test | Task |
|---|---|---|---|
| RF1 | Site dengan nama berkas non-ASCII, bukan UTF-8, atau sangat panjang | `ManifestTest::test_nama_non_ascii_bukan_utf8_dan_panjang` (PHP), `test_tarik_nama_non_ascii_dan_panjang` (integrasi) | 3, 14 |
| RF2 | Tabel tanpa primary key dengan kolom biner (NUL, byte bukan UTF-8, `\x1a`) | `TabelTest::test_tanpa_pk_dan_biner_memakai_offset_dan_hex` | 5 |
| RF3 | Produksi berubah selama tarik (berkas diubah/dihapus di antara manifest dan pengambilan, berkas besar berubah di antara rentang) | `test_tarik_produksi_berubah_di_tengah` | 14 |
| RF4 | Disk habis di tengah tarik | `test_tarik_disk_habis_gagal_jelas_dan_bisa_dilanjutkan` | 14 |
| RF5 | Skrip pembantu dipanggil dengan nama yang bertabrakan dengan container yang bukan miliknya | `@test "buat menolak container bernama sama yang bukan milik staging"` | 10 |

## Peta Berkas

**Dashboard (Python), baru:**

| Berkas | Tanggung jawab | Task |
|---|---|---|
| `src/wpmgr/staging/__init__.py` | Paket staging | 11 |
| `src/wpmgr/staging/aman.py` | Validasi nama/path/versi PHP, pembersihan teks, penjepitan angka, penulisan aman di `files/` | 11 |
| `src/wpmgr/staging/pembantu.py` | Pembungkus skrip pembantu: `sudo -n`, tenggat, galat, `status`, berkas akses router, tautan `secure_link` | 11 |
| `src/wpmgr/staging/paket.py` | Format paket biner `WPMGRPAK1` (sama dengan PHP) | 12 |
| `src/wpmgr/staging/rencana.py` | Selisih manifest, pembagian potongan, rencana dorong, tanda air, cek RAM/disk | 12 |
| `src/wpmgr/staging/indeks.py` | Indeks lokal `indeks.jsonl` dan pemindaian `files/` | 12 |
| `src/wpmgr/staging/umum.py` | Detak klaim, batal, kemajuan, ulang per potongan, log aktivitas, status staging | 13 |
| `src/wpmgr/staging/tarik.py` | Job `staging_tarik` | 14 |
| `src/wpmgr/staging/uji.py` | Job `staging_uji_update`, probe halaman, penilaian | 15 |
| `src/wpmgr/staging/dorong.py` | Job `staging_dorong` dan `staging_kembalikan`, snapshot, pemangkasan | 16, 17 |
| `src/wpmgr/staging/cron.py` | Jeda otomatis, perpanjangan sertifikat, pemangkasan disk | 18 |
| `src/wpmgr/web/routes_staging.py` | JSON API staging, SSO staging, email Mailpit | 19 |
| `src/wpmgr/templates/_tab_staging.html`, `src/wpmgr/static/app/staging.js` | Tab Staging | 20 |
| `migrations/versions/a7c8d9e0f1b2_lapis3_job_type.py`, `b8d9e0f1a2c3_lapis3_staging.py` | Enum dan tabel Lapis 3 | 1 |

**Dashboard, diubah:** `config.py`, `models.py` (1); `errors.py`, `jobs/queue.py`, `worker.py`, `jobs/handlers.py` (13–17); `site_client.py` (12); `connector_paket.py` (9); `kunci.py`, `cli.py` (18); `web/app.py`, `web/routes_api.py` (19); `web/routes_pages.py`, `kesehatan.py`, `templates/site_detail.html`, `templates/updates.html`, `static/app/detail.js`, `static/app/updates.js`, `static/app/kesehatan.js` (20); `pyproject.toml`, `.env.example` (1, 11); `deploy/crontab`, `deploy/wpmgr-worker@.service` (13, 18); `README.md` (21); `docker-compose.yml` (22).

**Connector (PHP), baru:** `includes/class-wpmgr-staging.php`, `class-wpmgr-staging-path.php`, `class-wpmgr-staging-paket.php` (2), `class-wpmgr-staging-manifest.php` (3), `class-wpmgr-staging-file.php` (4), `class-wpmgr-staging-tabel.php` (5), `class-wpmgr-staging-tanda-air.php` (6), `class-wpmgr-staging-sql.php`, `class-wpmgr-staging-db.php`, `class-wpmgr-staging-dorong.php` (7, 8), `templates/wpmgr-staging.php.tpl` (9); test `connector/tests/{StagingDasar,Manifest,File,Tabel,TandaAir,Sql,Dorong,Terapkan,ModeStaging}Test.php`.

**Connector, diubah:** `wp-manager-connector.php`, `includes/class-wpmgr-rest.php`, `includes/class-wpmgr-skema.php`, `includes/class-wpmgr-settings.php`, `uninstall.php`, `connector/tests/bootstrap.php`, `connector/tests/SkemaTest.php`.

**Deploy, baru:** `deploy/staging/wpmgr-staging` (skrip pembantu), `deploy/staging/staging.conf.contoh`, `deploy/staging/sudoers-wpmgr-staging`, `deploy/staging/nginx-wpmgr-staging.conf`, `deploy/staging/wpmgr-staging-siapkan.service`, `deploy/staging/tests/pembantu.bats`, `deploy/staging/tests/palsu/{docker,setpriv,certbot,curl,df}`, `.gitattributes`.

**Test Python, baru:**
- unit: `tests/unit/test_{staging_aman,staging_pembantu,staging_paket,staging_rencana,staging_indeks,site_client_staging,staging_uji_nilai,deploy_staging}.py`, `tests/unit/pembantu_palsu.py`;
- integrasi: `tests/integration/staging_palsu.py`, `tests/integration/test_{models_lapis3,staging_antrean,staging_tarik,staging_uji,staging_dorong,staging_kembalikan,staging_cli,api_staging,staging_halaman}.py`;
- e2e: `tests/e2e/test_staging.py`, `tests/e2e/pembantu/Dockerfile`.

## Urutan dan ketergantungan

- Fase A (Task 1): fondasi skema.
- Fase B (Task 2–9): connector 3.0.
- Fase C (Task 10–12): runtime dan pustaka dashboard.
- Fase D (Task 13–17): job.
- Fase E (Task 18–20): cron, API, UI.
- Fase F (Task 21–22): deploy dan e2e.

Setiap task hanya bergantung pada task bernomor lebih kecil. Task 2–9 (PHP) dan 10 (bash) tidak bergantung pada Task 1 dan boleh dikerjakan paralel dengannya.

---

## Fase A — Fondasi

### Task 1: Konfigurasi, model, `JobType`, dan migrasi

**Files:**
- Modify: `pyproject.toml`, `.env.example`, `src/wpmgr/config.py`, `src/wpmgr/models.py`
- Create: `migrations/versions/a7c8d9e0f1b2_lapis3_job_type.py`, `migrations/versions/b8d9e0f1a2c3_lapis3_staging.py`
- Test: `tests/unit/test_config.py` (tambah), `tests/integration/test_models_lapis3.py`

**Interfaces:**
- Produces:
  - `Settings.staging_domain: str | None`, `staging_dir: str`, `staging_pembantu: str`, `staging_pembantu_awalan: str | None`, `staging_maks_aktif: int`, `staging_jeda_hari: int`, `staging_snapshot: int`, `staging_email_acme: str | None`, `staging_router_url: str`, `staging_mailpit_url: str`; properti `staging_aktif -> bool`, `jalur_staging -> Path`.
  - `JobType.staging_tarik`, `staging_uji_update`, `staging_dorong`, `staging_kembalikan`.
  - `JOB_STAGING: frozenset[JobType]` (keempatnya), `JOB_STAGING_BACA: frozenset[JobType]` (`staging_tarik`, `staging_uji_update`).
  - Enum `StatusStaging` (`menyalin`, `siap`, `berjalan_uji`, `mendorong`, `dijeda`, `gagal`).
  - Model `Staging` (tabel `staging`), `StagingSnapshot` (`staging_snapshot`), `StagingUji` (`staging_uji`).
  - Indeks unik parsial `uq_jobs_staging_aktif` di `jobs(site_id)` untuk job staging `pending`/`running`.
  - Dependensi `bcrypt>=4.1`.

- [ ] **Step 1: Tambah dependensi.** Di `pyproject.toml`, tambahkan setelah baris `"requests>=2.32",`:

```toml
    "bcrypt>=4.1",
```

Run: `.venv/Scripts/pip install -e ".[dev]"`. Expected: berakhir dengan `Successfully installed ...` yang memuat `bcrypt` (atau `Requirement already satisfied` bila sudah ada).

- [ ] **Step 2: Tulis test konfigurasi yang gagal.** Tambahkan ke akhir `tests/unit/test_config.py`:

```python
def _env_wajib(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://a:b@localhost/c")
    monkeypatch.setenv("WPMGR_SECRET_KEY", "kunci")
    monkeypatch.setenv("WPMGR_BASE_URL", "https://contoh.test")
    monkeypatch.setenv("WPMGR_SESSION_SECRET", "rahasia")


def test_setelan_staging_default_mati(monkeypatch):
    from pathlib import Path

    _env_wajib(monkeypatch)
    for nama in ("WPMGR_STAGING_DOMAIN", "WPMGR_STAGING_DIR", "WPMGR_STAGING_PEMBANTU",
                 "WPMGR_STAGING_PEMBANTU_AWALAN", "WPMGR_STAGING_MAKS_AKTIF",
                 "WPMGR_STAGING_JEDA_HARI", "WPMGR_STAGING_SNAPSHOT", "WPMGR_STAGING_EMAIL_ACME",
                 "WPMGR_STAGING_ROUTER_URL", "WPMGR_STAGING_MAILPIT_URL"):
        monkeypatch.delenv(nama, raising=False)
    s = Settings(_env_file=None)
    assert s.staging_domain is None
    assert s.staging_aktif is False
    assert s.jalur_staging == Path("/var/lib/wpmgr/staging")
    assert s.staging_pembantu == "/usr/local/sbin/wpmgr-staging"
    assert s.staging_pembantu_awalan is None
    assert (s.staging_maks_aktif, s.staging_jeda_hari, s.staging_snapshot) == (3, 3, 3)
    assert s.staging_email_acme is None
    assert s.staging_router_url == "http://127.0.0.1:8090"
    assert s.staging_mailpit_url == "http://127.0.0.1:8025"


def test_setelan_staging_dari_env(monkeypatch, tmp_path):
    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", " Staging.HaloSocia.my.id. ")
    monkeypatch.setenv("WPMGR_STAGING_DIR", str(tmp_path))
    monkeypatch.setenv("WPMGR_STAGING_MAKS_AKTIF", "5")
    monkeypatch.setenv("WPMGR_STAGING_ROUTER_URL", "http://localhost:8090/")
    s = Settings(_env_file=None)
    assert s.staging_domain == "staging.halosocia.my.id"
    assert s.staging_aktif is True
    assert s.jalur_staging == tmp_path
    assert s.staging_maks_aktif == 5
    assert s.staging_router_url == "http://localhost:8090"


def test_domain_staging_kosong_berarti_mati(monkeypatch):
    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", "   ")
    assert Settings(_env_file=None).staging_aktif is False


@pytest.mark.parametrize("domain", [
    "staging", "-a.b.id", "a_b.id", "a..b.id", "staging.halosocia.my.id\nevil.id", "ex ample.id", "é.id",
])
def test_domain_staging_tidak_sah_ditolak(monkeypatch, domain):
    from pydantic import ValidationError

    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", domain)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("nama,nilai", [
    ("WPMGR_STAGING_MAKS_AKTIF", "0"), ("WPMGR_STAGING_JEDA_HARI", "0"), ("WPMGR_STAGING_SNAPSHOT", "999"),
])
def test_angka_staging_dijepit_validasi(monkeypatch, nama, nilai):
    from pydantic import ValidationError

    _env_wajib(monkeypatch)
    monkeypatch.setenv(nama, nilai)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
```

Tambahkan `import pytest` di puncak berkas bila belum ada.

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_config.py -q`. Expected: test staging gagal dengan `AttributeError: 'Settings' object has no attribute 'staging_domain'` (dan `DID NOT RAISE` untuk test domain tidak sah).

- [ ] **Step 4: Implementasikan konfigurasi.** Ganti isi `src/wpmgr/config.py`:

File: `src/wpmgr/config.py`
```python
import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Tambahkan ke akhir `.env.example`:

```bash

# Lapis 3 -- staging (kosongkan WPMGR_STAGING_DOMAIN untuk mematikan fitur)
# WPMGR_STAGING_DOMAIN=staging.halosocia.my.id
# WPMGR_STAGING_DIR=/var/lib/wpmgr/staging
# WPMGR_STAGING_PEMBANTU=/usr/local/sbin/wpmgr-staging
# WPMGR_STAGING_MAKS_AKTIF=3
# WPMGR_STAGING_JEDA_HARI=3
# WPMGR_STAGING_SNAPSHOT=3
# WPMGR_STAGING_EMAIL_ACME=admin@halosocia.my.id
# Hanya untuk pengembangan/e2e:
# WPMGR_STAGING_PEMBANTU_AWALAN=docker compose exec -T pembantu /usr/local/sbin/wpmgr-staging
# WPMGR_STAGING_ROUTER_URL=http://127.0.0.1:8090
# WPMGR_STAGING_MAILPIT_URL=http://127.0.0.1:8025
```

- [ ] **Step 5: Jalankan test konfigurasi.** Run: `.venv/Scripts/python -m pytest tests/unit/test_config.py -q`. Expected: semua lulus.

- [ ] **Step 6: Tulis test model yang gagal.**

File: `tests/integration/test_models_lapis3.py`
```python
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    JOB_STAGING,
    JOB_STAGING_BACA,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
    Staging,
    StagingSnapshot,
    StagingUji,
    StatusStaging,
)

pytestmark = pytest.mark.integration


def _site_lain(sesi, nama="Lain"):
    s = Site(id=uuid.uuid4(), nama=nama, url=f"https://{uuid.uuid4().hex[:8]}.test",
             status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.commit()
    return s


def test_himpunan_job_staging():
    assert JOB_STAGING == {JobType.staging_tarik, JobType.staging_uji_update,
                           JobType.staging_dorong, JobType.staging_kembalikan}
    assert JOB_STAGING_BACA == {JobType.staging_tarik, JobType.staging_uji_update}


def test_staging_default_dan_kolom(sesi, site):
    st = Staging(site_id=site.id, nama="contoh-test")
    sesi.add(st)
    sesi.commit()
    sesi.refresh(st)
    assert st.status == StatusStaging.menyalin
    assert st.aktif is False
    assert (st.ukuran_file, st.ukuran_db) == (0, 0)
    assert st.dibuat_pada.tzinfo is not None
    assert st.tanda_air is None and st.dorong_gagal_pada is None and st.batal_diminta_pada is None


def test_satu_staging_per_site_dan_nama_unik(sesi, site):
    sesi.add(Staging(site_id=site.id, nama="a"))
    sesi.commit()
    sesi.add(Staging(site_id=site.id, nama="b"))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()
    lain = _site_lain(sesi)
    sesi.add(Staging(site_id=lain.id, nama="a"))
    with pytest.raises(IntegrityError):
        sesi.commit()


def test_hapus_site_menghapus_staging_snapshot_dan_uji(sesi, site):
    job = buat_job(sesi, site.id, JobType.staging_uji_update)
    sesi.add_all([
        Staging(site_id=site.id, nama="c"),
        StagingSnapshot(site_id=site.id, job_id=None, jenis="sebelum_dorong", status="tersedia",
                        ukuran=10, path="x/snapshot/j1"),
        StagingUji(site_id=site.id, job_id=job.id, paket=[], hasil="lolos", pemeriksaan={}),
    ])
    sesi.commit()
    sesi.delete(sesi.get(Site, site.id))
    sesi.commit()
    assert sesi.scalars(select(Staging)).all() == []
    assert sesi.scalars(select(StagingSnapshot)).all() == []
    assert sesi.scalars(select(StagingUji)).all() == []


def test_hapus_job_tidak_menghapus_riwayat_uji(sesi, site):
    job = buat_job(sesi, site.id, JobType.staging_uji_update)
    uji = StagingUji(site_id=site.id, job_id=job.id, paket=[{"tipe": "plugin"}],
                     hasil="gagal", pemeriksaan={"alasan": ["x"]})
    sesi.add(uji)
    sesi.commit()
    sesi.delete(job)
    sesi.commit()
    sesi.refresh(uji)
    assert uji.job_id is None


def test_satu_job_staging_aktif_per_site(sesi, site):
    buat_job(sesi, site.id, JobType.staging_tarik)
    with pytest.raises(IntegrityError):
        buat_job(sesi, site.id, JobType.staging_dorong)
    sesi.rollback()


def test_job_non_staging_tidak_terhalang_indeks(sesi, site):
    buat_job(sesi, site.id, JobType.staging_tarik)
    buat_job(sesi, site.id, JobType.scan_site)
    buat_job(sesi, site.id, JobType.scan_site)
    assert sesi.query(Job).count() == 3


def test_job_staging_baru_boleh_setelah_yang_lama_selesai(sesi, site):
    lama = buat_job(sesi, site.id, JobType.staging_tarik)
    lama.status = JobStatus.failed
    lama.finished_at = datetime.now(timezone.utc)
    sesi.commit()
    buat_job(sesi, site.id, JobType.staging_tarik)
    lain = _site_lain(sesi)
    buat_job(sesi, lain.id, JobType.staging_tarik)
    assert sesi.query(Job).filter(Job.tipe == JobType.staging_tarik).count() == 3
```

- [ ] **Step 7: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_models_lapis3.py -q`. Expected: `ImportError: cannot import name 'JOB_STAGING' from 'wpmgr.models'`.

- [ ] **Step 8: Implementasikan model.** Di `src/wpmgr/models.py`, ganti kelas `JobType`:

```python
class JobType(str, enum.Enum):
    scan_site = "scan_site"
    update_package = "update_package"
    verify_site = "verify_site"
    collect_events = "collect_events"
    collect_traffic = "collect_traffic"
    update_connector = "update_connector"
    staging_tarik = "staging_tarik"
    staging_uji_update = "staging_uji_update"
    staging_dorong = "staging_dorong"
    staging_kembalikan = "staging_kembalikan"


# Job staging diproses worker khusus (Koreksi #1). Tarik dan uji hanya
# membaca produksi, jadi boleh berjalan bersamaan dengan job non-staging di
# site yang sama; dorong dan kembalikan menulis ke produksi dan tidak boleh.
JOB_STAGING = frozenset({
    JobType.staging_tarik, JobType.staging_uji_update,
    JobType.staging_dorong, JobType.staging_kembalikan,
})
JOB_STAGING_BACA = frozenset({JobType.staging_tarik, JobType.staging_uji_update})
```

Setelah kelas `UptimeHasil`, tambahkan:

```python
class StatusStaging(str, enum.Enum):
    menyalin = "menyalin"
    siap = "siap"
    berjalan_uji = "berjalan_uji"
    mendorong = "mendorong"
    dijeda = "dijeda"
    gagal = "gagal"
```

Ganti `__table_args__` kelas `Job`:

```python
    __table_args__ = (
        Index("ix_jobs_status_scheduled_for", "status", "scheduled_for"),
        Index("ix_jobs_site_id_status", "site_id", "status"),
        # Paling banyak satu job staging tertunda/berjalan per site (spec §11).
        # Dijaga di database supaya dua klik bersamaan tidak lolos keduanya.
        Index(
            "uq_jobs_staging_aktif", "site_id", unique=True,
            postgresql_where=text(
                "tipe IN ('staging_tarik', 'staging_uji_update', 'staging_dorong', "
                "'staging_kembalikan') AND status IN ('pending', 'running')"
            ),
        ),
    )
```

Tambahkan ke akhir `src/wpmgr/models.py`:

```python
class Staging(Base):
    """Satu salinan staging per site (spec §5.1)."""

    __tablename__ = "staging"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    nama: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[StatusStaging] = mapped_column(
        Enum(StatusStaging, name="status_staging"), nullable=False,
        default=StatusStaging.menyalin, server_default="menyalin",
    )
    aktif: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    sandi_hash: Mapped[str | None] = mapped_column(Text)
    # Kunci secure_link router untuk SSO dan probe (Koreksi #5), Fernet.
    rahasia_router_terenkripsi: Mapped[bytes | None] = mapped_column(LargeBinary)
    versi_php: Mapped[str | None] = mapped_column(Text)
    ukuran_file: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    ukuran_db: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    ditarik_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tanda_air: Mapped[dict | None] = mapped_column(JSONB)
    diubah_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dibuka_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sertifikat_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    galat: Mapped[str | None] = mapped_column(Text)
    dorong_gagal_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    batal_diminta_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StagingSnapshot(Base):
    __tablename__ = "staging_snapshot"
    __table_args__ = (Index("ix_staging_snapshot_site_dibuat", "site_id", "dibuat_pada"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="SET NULL"))
    jenis: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    ukuran: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    path: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StagingUji(Base):
    __tablename__ = "staging_uji"
    __table_args__ = (Index("ix_staging_uji_site_dibuat", "site_id", "dibuat_pada"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable (Koreksi #4): riwayat uji lebih berharga daripada baris job-nya.
    job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="SET NULL"))
    paket: Mapped[list] = mapped_column(JSONB, nullable=False)
    hasil: Mapped[str] = mapped_column(Text, nullable=False)
    pemeriksaan: Mapped[dict] = mapped_column(JSONB, nullable=False)
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 9: Migrasi nilai enum.**

File: `migrations/versions/a7c8d9e0f1b2_lapis3_job_type.py`
```python
"""lapis 3: nilai job_type staging

Revision ID: a7c8d9e0f1b2
Revises: f1a2b3c4d5e6
Create Date: 2026-09-26 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "a7c8d9e0f1b2"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sama seperti c5a1e2d3f4b6: nilai enum baru tidak boleh dipakai di
    # transaksi yang sama dengan penambahannya, dan revisi berikutnya memakai
    # nilai ini di predikat indeks parsial.
    with op.get_context().autocommit_block():
        for nilai in ("staging_tarik", "staging_uji_update", "staging_dorong", "staging_kembalikan"):
            op.execute(f"ALTER TYPE job_type ADD VALUE IF NOT EXISTS '{nilai}'")


def downgrade() -> None:
    # PostgreSQL tidak menyediakan penghapusan nilai enum.
    pass
```

- [ ] **Step 10: Migrasi tabel.**

File: `migrations/versions/b8d9e0f1a2c3_lapis3_staging.py`
```python
"""lapis 3: tabel staging, snapshot, uji, dan indeks job staging aktif

Revision ID: b8d9e0f1a2c3
Revises: a7c8d9e0f1b2
Create Date: 2026-09-26 00:00:01.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8d9e0f1a2c3"
down_revision: str | Sequence[str] | None = "a7c8d9e0f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS = ("menyalin", "siap", "berjalan_uji", "mendorong", "dijeda", "gagal")


def upgrade() -> None:
    status_staging = postgresql.ENUM(*STATUS, name="status_staging", create_type=False)
    status_staging.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "staging",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("nama", sa.Text(), nullable=False, unique=True),
        sa.Column("status", status_staging, nullable=False, server_default="menyalin"),
        sa.Column("aktif", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sandi_hash", sa.Text()),
        sa.Column("rahasia_router_terenkripsi", sa.LargeBinary()),
        sa.Column("versi_php", sa.Text()),
        sa.Column("ukuran_file", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ukuran_db", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ditarik_pada", sa.DateTime(timezone=True)),
        sa.Column("tanda_air", postgresql.JSONB()),
        sa.Column("diubah_pada", sa.DateTime(timezone=True)),
        sa.Column("dibuka_pada", sa.DateTime(timezone=True)),
        sa.Column("sertifikat_pada", sa.DateTime(timezone=True)),
        sa.Column("galat", sa.Text()),
        sa.Column("dorong_gagal_pada", sa.DateTime(timezone=True)),
        sa.Column("batal_diminta_pada", sa.DateTime(timezone=True)),
        sa.Column("dibuat_pada", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "staging_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("jenis", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("ukuran", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("dibuat_pada", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_staging_snapshot_site_dibuat", "staging_snapshot", ["site_id", "dibuat_pada"])
    op.create_table(
        "staging_uji",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("paket", postgresql.JSONB(), nullable=False),
        sa.Column("hasil", sa.Text(), nullable=False),
        sa.Column("pemeriksaan", postgresql.JSONB(), nullable=False),
        sa.Column("dibuat_pada", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_staging_uji_site_dibuat", "staging_uji", ["site_id", "dibuat_pada"])
    op.create_index(
        "uq_jobs_staging_aktif", "jobs", ["site_id"], unique=True,
        postgresql_where=sa.text(
            "tipe IN ('staging_tarik', 'staging_uji_update', 'staging_dorong', "
            "'staging_kembalikan') AND status IN ('pending', 'running')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_staging_aktif", table_name="jobs")
    op.drop_index("ix_staging_uji_site_dibuat", table_name="staging_uji")
    op.drop_table("staging_uji")
    op.drop_index("ix_staging_snapshot_site_dibuat", table_name="staging_snapshot")
    op.drop_table("staging_snapshot")
    op.drop_table("staging")
    postgresql.ENUM(name="status_staging").drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 11: Jalankan test model dan migrasi.** Run: `.venv/Scripts/python -m pytest tests/integration/test_models_lapis3.py -q`. Expected: `8 passed`. Lalu pada database dev: `.venv/Scripts/python -m alembic upgrade head` lalu `.venv/Scripts/python -m alembic downgrade f1a2b3c4d5e6` lalu `.venv/Scripts/python -m alembic upgrade head`. Expected: ketiganya selesai tanpa galat, `alembic current` mencetak `b8d9e0f1a2c3 (head)`.

- [ ] **Step 12: Seluruh test unit dan integrasi, lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 13: Commit.**

```bash
git add pyproject.toml .env.example src/wpmgr/config.py src/wpmgr/models.py migrations/versions/a7c8d9e0f1b2_lapis3_job_type.py migrations/versions/b8d9e0f1a2c3_lapis3_staging.py tests/unit/test_config.py tests/integration/test_models_lapis3.py
git commit -m "feat(staging): konfigurasi, model, dan migrasi Lapis 3"
```

---

## Fase B — Connector 3.0

### Task 2: Fondasi staging di connector (setelan, fitur, path aman, paket biner, route, versi 3.0.0)

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-staging.php`, `includes/class-wpmgr-staging-path.php`, `includes/class-wpmgr-staging-paket.php`, `connector/tests/StagingDasarTest.php`
- Modify: `connector/wp-manager-connector/wp-manager-connector.php`, `includes/class-wpmgr-rest.php`, `includes/class-wpmgr-skema.php`, `includes/class-wpmgr-settings.php`, `connector/tests/bootstrap.php`, `connector/tests/SkemaTest.php`

**Interfaces:**
- Produces:
  - Opsi `wpmgr_izinkan_staging` (`'1'`/`'0'`, default `'0'`); `WPMGR_Settings::izinkan_staging(): bool`.
  - `WPMGR_Skema::fitur( $monitoring_mati, $staging = false ): array`. Nama `staging` diumumkan hanya bila `$staging` benar.
  - `WPMGR_Staging`:
    - `mode_staging(): bool` (konstanta `WPMGR_STAGING`); `fitur_aktif(): bool` (setelan menyala dan bukan mode staging);
    - `putuskan( $hasil_hmac, $fitur_aktif )` (murni) dan `guard( $request )`, yang mengembalikan `WP_Error` `wpmgr_staging_mati` 403 bila setelan mati;
    - `rute(): array` (jalur => `array( metode, callback )`; diisi Task 3–8) dan `daftarkan_route()`;
    - `root(): string` (`ABSPATH` dengan `/` di akhir); `anggaran_detik(): int` (≤ 20); `bersih( $teks, $n )`; `galat( $kode, $pesan, $status )`;
    - `respons_biner( $isi ): WP_REST_Response` dan filter `sajikan_biner()` pada `rest_pre_serve_request`;
    - konstanta `HOOK_BERSIHKAN = 'wpmgr_staging_bersihkan'`.
  - `WPMGR_Staging_Path`: `normalisasi( $rel )`, `dikecualikan( $rel )`, `boleh_ditulis( $rel )`, `di_dalam( $akar, $abs )`, `untuk_dibaca( $akar, $rel )`, `untuk_ditulis( $akar, $rel )`. Semua penolakan berupa `WP_Error` `wpmgr_staging_path` (400); berkas yang tidak ada berupa `wpmgr_staging_tidak_ada` (404).
  - `WPMGR_Staging_Paket::susun( array $meta, array $isi ): string` dan `urai( $data ): array( $meta, $bagian ) | WP_Error`. Formatnya: `"WPMGRPAK1\n"`, 8 hex panjang meta, `"\n"`, JSON meta, lalu isi bagian berurutan. Setiap `meta['berkas'][i]` membawa `ukuran` dan `sha256` bagiannya. Galat hash berkode `wpmgr_staging_hash` (422), galat bentuk berkode `wpmgr_staging_paket` (400).
  - Connector versi `3.0.0`, `WPMGR_VERSI_SKEMA` 3.

- [ ] **Step 1: Stub WordPress untuk test.** Di `connector/tests/bootstrap.php`, tambahkan sebelum deretan `require_once` di akhir:

```php
// Respons REST minimal untuk WPMGR_Staging::respons_biner()/sajikan_biner().
if ( ! class_exists( 'WP_HTTP_Response' ) ) {
    class WP_HTTP_Response {
        public $data;
        public $headers = array();
        public $status  = 200;

        public function __construct( $data = null, $status = 200, $headers = array() ) {
            $this->data    = $data;
            $this->status  = $status;
            $this->headers = $headers;
        }

        public function get_data() {
            return $this->data;
        }

        public function header( $kunci, $nilai, $ganti = true ) {
            $this->headers[ $kunci ] = $nilai;
        }

        public function get_headers() {
            return $this->headers;
        }
    }
}
if ( ! class_exists( 'WP_REST_Response' ) ) {
    class WP_REST_Response extends WP_HTTP_Response {
    }
}
```

dan tambahkan di akhir deretan `require_once`:

```php
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-path.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-paket.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging.php';
```

- [ ] **Step 2: Tulis test yang gagal.**

File: `connector/tests/StagingDasarTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class StagingDasarTest extends TestCase {

    private $akar;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-stg-' . bin2hex( random_bytes( 6 ) ) . '/';
        mkdir( $this->akar . 'wp-content/uploads', 0777, true );
    }

    protected function tearDown(): void {
        self::hapus( rtrim( $this->akar, '/' ) );
        $GLOBALS['wpmgr_test_opsi'] = array();
    }

    public static function hapus( $jalur ) {
        if ( is_link( $jalur ) || is_file( $jalur ) ) {
            @unlink( $jalur );
            return;
        }
        if ( ! is_dir( $jalur ) ) {
            return;
        }
        foreach ( scandir( $jalur ) as $n ) {
            if ( '.' !== $n && '..' !== $n ) {
                self::hapus( $jalur . '/' . $n );
            }
        }
        @rmdir( $jalur );
    }

    private function symlink_atau_lewati( $target, $tautan ) {
        if ( ! @symlink( $target, $tautan ) ) {
            $this->markTestSkipped( 'Sistem ini tidak mengizinkan symlink (Windows tanpa Developer Mode).' );
        }
    }

    public function test_putuskan_mendahulukan_penolakan_hmac(): void {
        $tolak = new WP_Error( 'wpmgr_ditolak', 'x', array( 'status' => 401 ) );
        $this->assertSame( $tolak, WPMGR_Staging::putuskan( $tolak, true ) );
        $this->assertSame( $tolak, WPMGR_Staging::putuskan( $tolak, false ) );
    }

    public function test_putuskan_403_bila_staging_mati(): void {
        $hasil = WPMGR_Staging::putuskan( true, false );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_mati', $hasil->get_error_code() );
        $this->assertSame( array( 'status' => 403 ), $hasil->get_error_data() );
        $this->assertTrue( WPMGR_Staging::putuskan( true, true ) );
    }

    public function test_izinkan_staging_default_mati(): void {
        $this->assertFalse( WPMGR_Settings::izinkan_staging() );
        $GLOBALS['wpmgr_test_opsi']['wpmgr_izinkan_staging'] = '1';
        $this->assertTrue( WPMGR_Settings::izinkan_staging() );
        $this->assertTrue( WPMGR_Staging::fitur_aktif() );
        $GLOBALS['wpmgr_test_opsi']['wpmgr_izinkan_staging'] = '0';
        $this->assertFalse( WPMGR_Staging::fitur_aktif() );
    }

    public function test_fitur_staging_hanya_bila_diizinkan(): void {
        $this->assertSame( array( 'self_update', 'events', 'traffic' ), WPMGR_Skema::fitur( false ) );
        $this->assertSame( array( 'self_update', 'events', 'traffic', 'staging' ), WPMGR_Skema::fitur( false, true ) );
        $this->assertSame( array( 'self_update', 'staging' ), WPMGR_Skema::fitur( true, true ) );
    }

    public function path_berbahaya(): array {
        return array(
            'kosong'          => array( '' ),
            'absolut'         => array( '/etc/passwd' ),
            'naik'            => array( '../x' ),
            'naik di tengah'  => array( 'a/../b' ),
            'segmen kosong'   => array( 'a//b' ),
            'titik'           => array( 'a/./b' ),
            'drive windows'   => array( 'C:/x' ),
            'backslash'       => array( 'a\\b' ),
            'nul'             => array( "a\0b" ),
            'baris baru'      => array( "a\nb" ),
            'terlalu panjang' => array( str_repeat( 'a', 1025 ) ),
            'segmen panjang'  => array( 'a/' . str_repeat( 'b', 256 ) ),
            'bukan utf8'      => array( "\xff.txt" ),
            'slash akhir'     => array( 'a/' ),
        );
    }

    /** @dataProvider path_berbahaya */
    public function test_normalisasi_menolak( $path ): void {
        $hasil = WPMGR_Staging_Path::normalisasi( $path );
        $this->assertInstanceOf( WP_Error::class, $hasil );
        $this->assertSame( 'wpmgr_staging_path', $hasil->get_error_code() );
    }

    public function test_normalisasi_menerima_path_sah(): void {
        foreach ( array( 'wp-content/uploads/ü-berkas.txt', '.htaccess', 'wp-content/plugins/a b/c.php',
                         'wp-content/uploads/' . str_repeat( 'é', 120 ) . '.jpg' ) as $p ) {
            $this->assertSame( $p, WPMGR_Staging_Path::normalisasi( $p ) );
        }
    }

    public function test_dikecualikan(): void {
        foreach ( array( 'wp-config.php', '.maintenance', 'debug.log', 'wp-content/debug.LOG',
                         'wp-content/cache/a/b.html', 'wp-content/cache', 'wp-content/wpmgr-dorong/x/y',
                         'wp-content/updraft/b.zip', 'wp-content/ai1wm-backups/a.wpress',
                         'wp-content/backups-dup-lite/a.zip', 'wp-content/wpvividbackups/a.zip' ) as $p ) {
            $this->assertTrue( WPMGR_Staging_Path::dikecualikan( $p ), $p );
        }
        foreach ( array( 'index.php', 'wp-content/uploads/cache/a.jpg', 'wp-content/plugins/updraft/x.php',
                         'wp-content/themes/a/logs.php', 'wp-config-sample.php' ) as $p ) {
            $this->assertFalse( WPMGR_Staging_Path::dikecualikan( $p ), $p );
        }
    }

    public function test_boleh_ditulis(): void {
        foreach ( array( 'wp-content/themes/x/style.css', 'wp-admin/index.php', 'wp-includes/version.php',
                         'index.php', 'wp-login.php', 'wp-settings.php', '.htaccess', 'xmlrpc.php',
                         'license.txt', 'readme.html', 'wp-content/uploads/2026/09/a.jpg' ) as $p ) {
            $this->assertTrue( WPMGR_Staging_Path::boleh_ditulis( $p ), $p );
        }
        foreach ( array( 'wp-config.php', 'wp-content/plugins/wp-manager-connector/wp-manager-connector.php',
                         'wp-content/mu-plugins/wpmgr-staging.php', 'wp-content/mu-plugins/wpmgr-dorong-aman.php',
                         'lain.php', 'google123.html', 'foo/bar.php', '.maintenance', 'wp-content/cache/a',
                         'debug.log', '../index.php', 'WP-LOGIN.PHP' ) as $p ) {
            $this->assertFalse( WPMGR_Staging_Path::boleh_ditulis( $p ), $p );
        }
    }

    public function test_untuk_dibaca(): void {
        file_put_contents( $this->akar . 'index.php', '<?php' );
        $this->assertSame( $this->akar . 'index.php', WPMGR_Staging_Path::untuk_dibaca( $this->akar, 'index.php' ) );
        $hilang = WPMGR_Staging_Path::untuk_dibaca( $this->akar, 'tidak-ada.php' );
        $this->assertSame( 'wpmgr_staging_tidak_ada', $hilang->get_error_code() );
        file_put_contents( $this->akar . 'wp-config.php', '<?php' );
        $this->assertSame( 'wpmgr_staging_path',
            WPMGR_Staging_Path::untuk_dibaca( $this->akar, 'wp-config.php' )->get_error_code() );
    }

    public function test_untuk_dibaca_menolak_symlink_keluar(): void {
        $luar = sys_get_temp_dir() . '/wpmgr-luar-' . bin2hex( random_bytes( 4 ) ) . '.txt';
        file_put_contents( $luar, 'rahasia' );
        try {
            $this->symlink_atau_lewati( $luar, $this->akar . 'wp-content/uploads/tautan.txt' );
            $hasil = WPMGR_Staging_Path::untuk_dibaca( $this->akar, 'wp-content/uploads/tautan.txt' );
            $this->assertInstanceOf( WP_Error::class, $hasil );
        } finally {
            @unlink( $luar );
        }
    }

    public function test_untuk_ditulis_menolak_leluhur_symlink_keluar(): void {
        $luar = sys_get_temp_dir() . '/wpmgr-luar-' . bin2hex( random_bytes( 4 ) );
        mkdir( $luar );
        try {
            $this->symlink_atau_lewati( $luar, $this->akar . 'wp-content/uploads/luar' );
            $hasil = WPMGR_Staging_Path::untuk_ditulis( $this->akar, 'wp-content/uploads/luar/baru/x.php' );
            $this->assertInstanceOf( WP_Error::class, $hasil );
        } finally {
            self::hapus( $luar );
        }
    }

    public function test_untuk_ditulis_path_baru_di_dalam(): void {
        $this->assertSame( $this->akar . 'wp-content/uploads/2026/x.jpg',
            WPMGR_Staging_Path::untuk_ditulis( $this->akar, 'wp-content/uploads/2026/x.jpg' ) );
        $this->assertInstanceOf( WP_Error::class, WPMGR_Staging_Path::untuk_ditulis( $this->akar, 'wp-config.php' ) );
    }

    public function test_paket_bolak_balik(): void {
        $isi   = array( '', "biner\0\xff\x1a\n'\"" );
        $data  = WPMGR_Staging_Paket::susun( array( 'jenis' => 'uji', 'berkas' => array(
            array( 'path' => 'a.txt' ), array( 'path' => 'wp-content/ü.bin' ),
        ) ), $isi );
        $this->assertSame( "WPMGRPAK1\n", substr( $data, 0, 10 ) );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( $data );
        $this->assertSame( $isi, $bagian );
        $this->assertSame( 'uji', $meta['jenis'] );
        $this->assertSame( 0, $meta['berkas'][0]['ukuran'] );
        $this->assertSame( hash( 'sha256', $isi[1] ), $meta['berkas'][1]['sha256'] );
        $this->assertSame( 'wp-content/ü.bin', $meta['berkas'][1]['path'] );
    }

    public function test_paket_rusak_ditolak(): void {
        $data = WPMGR_Staging_Paket::susun( array( 'berkas' => array( array( 'path' => 'a' ) ) ), array( 'abcdef' ) );
        $this->assertSame( 'wpmgr_staging_paket', WPMGR_Staging_Paket::urai( 'BUKANPAKET' )->get_error_code() );
        $this->assertSame( 'wpmgr_staging_paket', WPMGR_Staging_Paket::urai( "WPMGRPAK1\nzzzzzzzz\n{}" )->get_error_code() );
        $this->assertSame( 'wpmgr_staging_paket', WPMGR_Staging_Paket::urai( "WPMGRPAK1\n7fffffff\n{}" )->get_error_code() );
        $this->assertSame( 'wpmgr_staging_paket', WPMGR_Staging_Paket::urai( $data . 'x' )->get_error_code() );
        $this->assertSame( 'wpmgr_staging_paket', WPMGR_Staging_Paket::urai( substr( $data, 0, -1 ) )->get_error_code() );
        $rusak = substr( $data, 0, -1 ) . 'X';
        $galat = WPMGR_Staging_Paket::urai( $rusak );
        $this->assertSame( 'wpmgr_staging_hash', $galat->get_error_code() );
        $this->assertSame( array( 'status' => 422 ), $galat->get_error_data() );
    }

    public function test_respons_biner_disajikan_apa_adanya(): void {
        $r = WPMGR_Staging::respons_biner( "isi\0biner" );
        $this->assertSame( 'application/octet-stream', $r->get_headers()['Content-Type'] );
        $this->assertSame( hash( 'sha256', "isi\0biner" ), $r->get_headers()['X-Wpmgr-Sha256'] );
        ob_start();
        $disajikan = WPMGR_Staging::sajikan_biner( false, $r, null, null );
        $keluar    = ob_get_clean();
        $this->assertTrue( $disajikan );
        $this->assertSame( "isi\0biner", $keluar );
        // Respons JSON biasa tidak disentuh.
        $this->assertFalse( WPMGR_Staging::sajikan_biner( false, new WP_REST_Response( array( 'a' => 1 ) ), null, null ) );
    }
}
```

Ubah `test_fitur_yang_diumumkan` di `connector/tests/SkemaTest.php` menjadi:

```php
    public function test_fitur_yang_diumumkan(): void {
        $this->assertSame( array( 'self_update', 'events', 'traffic' ), WPMGR_Skema::fitur( false ) );
        // Self-update bukan pemantauan: tetap tersedia walau pemantauan dimatikan.
        $this->assertSame( array( 'self_update' ), WPMGR_Skema::fitur( true ) );
        $this->assertSame( array( 'self_update', 'staging' ), WPMGR_Skema::fitur( true, true ) );
    }
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `cd connector && vendor/bin/phpunit`. Expected: fatal `Failed opening required '.../class-wpmgr-staging-path.php'`.

- [ ] **Step 4: Kelas path.**

File: `connector/wp-manager-connector/includes/class-wpmgr-staging-path.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Aturan path staging (spec §13): hanya path relatif di dalam root
 * WordPress, tanpa `..`, tanpa symlink keluar, dan `wp-config.php` tidak
 * pernah dibaca maupun ditulis. Dashboard menerapkan aturan yang sama
 * (wpmgr.staging.aman); keduanya harus tetap sama ketatnya.
 */
class WPMGR_Staging_Path {

    const MAKS_PANJANG  = 1024;
    const MAKS_SEGMEN   = 255;
    const BACKUP_KONTEN = array( 'updraft', 'ai1wm-backups', 'wpvividbackups' );
    const TIDAK_PERNAH_DITULIS = array(
        'wp-config.php', '.maintenance',
        'wp-content/mu-plugins/wpmgr-staging.php', 'wp-content/mu-plugins/wpmgr-dorong-aman.php',
    );
    const AKAR_INTI = array( 'index.php', 'xmlrpc.php', 'license.txt', 'readme.html', '.htaccess' );

    private static function tolak( $pesan ) {
        return new WP_Error( 'wpmgr_staging_path', $pesan, array( 'status' => 400 ) );
    }

    public static function normalisasi( $rel ) {
        if ( ! is_string( $rel ) || '' === $rel ) {
            return self::tolak( 'Path kosong.' );
        }
        if ( strlen( $rel ) > self::MAKS_PANJANG ) {
            return self::tolak( 'Path terlalu panjang.' );
        }
        if ( 1 !== preg_match( '//u', $rel ) ) {
            return self::tolak( 'Path bukan UTF-8 yang sah.' );
        }
        if ( preg_match( '/[\x00-\x1f\x7f\\\\]/', $rel ) ) {
            return self::tolak( 'Path memuat karakter terlarang.' );
        }
        if ( '/' === $rel[0] || preg_match( '/^[A-Za-z]:/', $rel ) ) {
            return self::tolak( 'Path absolut ditolak.' );
        }
        foreach ( explode( '/', $rel ) as $segmen ) {
            if ( '' === $segmen || '.' === $segmen || '..' === $segmen ) {
                return self::tolak( 'Path memuat segmen terlarang.' );
            }
            if ( strlen( $segmen ) > self::MAKS_SEGMEN ) {
                return self::tolak( 'Nama berkas terlalu panjang.' );
            }
        }
        return $rel;
    }

    /** Tidak pernah disalin ke staging maupun didorong (spec §6.2 langkah 1). */
    public static function dikecualikan( $rel ) {
        $rel = (string) $rel;
        if ( 'wp-config.php' === $rel || '.maintenance' === $rel ) {
            return true;
        }
        if ( '.log' === strtolower( substr( $rel, -4 ) ) ) {
            return true;
        }
        foreach ( array( 'wp-content/cache', 'wp-content/wpmgr-dorong' ) as $dir ) {
            if ( $rel === $dir || 0 === strpos( $rel, $dir . '/' ) ) {
                return true;
            }
        }
        $bagian = explode( '/', $rel );
        if ( count( $bagian ) >= 2 && 'wp-content' === $bagian[0] ) {
            if ( in_array( $bagian[1], self::BACKUP_KONTEN, true ) || 0 === strpos( $bagian[1], 'backups-dup-' ) ) {
                return true;
            }
        }
        return false;
    }

    /**
     * Penulisan di luar wp-content dibatasi pada berkas inti WordPress
     * (spec §13). Plugin connector sendiri tidak pernah ditimpa: menimpanya
     * di tengah request yang sedang ia layani adalah cara tercepat membuat
     * dorongan tidak bisa dipulihkan.
     */
    public static function boleh_ditulis( $rel ) {
        if ( is_wp_error( self::normalisasi( $rel ) ) || self::dikecualikan( $rel ) ) {
            return false;
        }
        if ( in_array( $rel, self::TIDAK_PERNAH_DITULIS, true ) ) {
            return false;
        }
        if ( 0 === strpos( $rel, 'wp-content/plugins/wp-manager-connector/' ) ) {
            return false;
        }
        foreach ( array( 'wp-content/', 'wp-admin/', 'wp-includes/' ) as $awalan ) {
            if ( 0 === strpos( $rel, $awalan ) ) {
                return true;
            }
        }
        if ( false !== strpos( $rel, '/' ) ) {
            return false;
        }
        return in_array( $rel, self::AKAR_INTI, true ) || 1 === preg_match( '/^wp-[a-z0-9-]+\.php\z/', $rel );
    }

    private static function garis( $p ) {
        return str_replace( '\\', '/', (string) $p );
    }

    /** Apakah $abs (yang harus ada) benar-benar berada di dalam $akar setelah symlink diurai. */
    public static function di_dalam( $akar, $abs ) {
        $akar_nyata = realpath( $akar );
        $nyata      = realpath( $abs );
        if ( false === $akar_nyata || false === $nyata ) {
            return false;
        }
        $akar_nyata = rtrim( self::garis( $akar_nyata ), '/' ) . '/';
        $nyata      = self::garis( $nyata );
        if ( is_dir( $nyata ) ) {
            $nyata = rtrim( $nyata, '/' ) . '/';
        }
        return 0 === strpos( $nyata, $akar_nyata );
    }

    public static function untuk_dibaca( $akar, $rel ) {
        $n = self::normalisasi( $rel );
        if ( is_wp_error( $n ) ) {
            return $n;
        }
        if ( self::dikecualikan( $rel ) ) {
            return self::tolak( 'Path dikecualikan dari staging.' );
        }
        $abs = $akar . $rel;
        if ( is_link( $abs ) ) {
            return self::tolak( 'Symlink tidak disalin.' );
        }
        if ( ! is_file( $abs ) ) {
            return new WP_Error( 'wpmgr_staging_tidak_ada', 'Berkas tidak ada.', array( 'status' => 404 ) );
        }
        if ( ! self::di_dalam( $akar, $abs ) ) {
            return self::tolak( 'Path keluar dari root WordPress.' );
        }
        return $abs;
    }

    public static function untuk_ditulis( $akar, $rel ) {
        if ( ! self::boleh_ditulis( $rel ) ) {
            return self::tolak( 'Path tidak boleh ditulis oleh dorongan staging.' );
        }
        $abs = $akar . $rel;
        if ( is_link( $abs ) ) {
            return self::tolak( 'Path tujuan adalah symlink.' );
        }
        // Leluhur terdekat yang sudah ada harus berada di dalam akar: satu
        // direktori symlink ke luar membuat penulisan mendarat di luar
        // WordPress walau path relatifnya bersih.
        $dir = dirname( $abs );
        while ( ! file_exists( $dir ) && strlen( $dir ) > strlen( rtrim( $akar, '/' ) ) ) {
            $dir = dirname( $dir );
        }
        if ( ! self::di_dalam( $akar, $dir ) ) {
            return self::tolak( 'Path keluar dari root WordPress.' );
        }
        return $abs;
    }
}
```

- [ ] **Step 5: Kelas paket.**

File: `connector/wp-manager-connector/includes/class-wpmgr-staging-paket.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Paket biner staging: beberapa bagian (isi berkas, rentang berkas besar,
 * atau SQL) dalam satu body, masing-masing dengan sha256 sendiri (spec §6.2
 * "hash per potongan"). Format yang sama dipakai dua arah -- balasan
 * /staging/file dan /staging/tabel, serta body /staging/unggah -- dan
 * diurai dengan aturan yang sama oleh wpmgr.staging.paket di dashboard.
 *
 *   "WPMGRPAK1\n" + 8 hex panjang meta + "\n" + meta JSON + bagian...
 */
class WPMGR_Staging_Paket {

    const MAGIC     = "WPMGRPAK1\n";
    const MAKS_META = 4194304;

    private static function rusak( $pesan ) {
        return new WP_Error( 'wpmgr_staging_paket', $pesan, array( 'status' => 400 ) );
    }

    public static function susun( array $meta, array $isi ) {
        $isi    = array_values( $isi );
        $berkas = ( isset( $meta['berkas'] ) && is_array( $meta['berkas'] ) ) ? array_values( $meta['berkas'] ) : array();
        if ( count( $berkas ) !== count( $isi ) ) {
            throw new InvalidArgumentException( 'Jumlah entri meta dan bagian paket tidak sama.' );
        }
        foreach ( $isi as $i => $data ) {
            $berkas[ $i ]['ukuran'] = strlen( $data );
            $berkas[ $i ]['sha256'] = hash( 'sha256', $data );
        }
        $meta['berkas'] = $berkas;
        $json = json_encode( $meta, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE );
        if ( false === $json || strlen( $json ) > self::MAKS_META ) {
            throw new InvalidArgumentException( 'Meta paket tidak dapat dikodekan.' );
        }
        return self::MAGIC . sprintf( '%08x', strlen( $json ) ) . "\n" . $json . implode( '', $isi );
    }

    public static function urai( $data ) {
        $data  = (string) $data;
        $awal  = strlen( self::MAGIC );
        $total = strlen( $data );
        if ( $total < $awal + 9 || 0 !== strncmp( $data, self::MAGIC, $awal ) ) {
            return self::rusak( 'Bukan paket staging.' );
        }
        $hex = substr( $data, $awal, 8 );
        if ( 1 !== preg_match( '/^[0-9a-f]{8}\z/', $hex ) || "\n" !== $data[ $awal + 8 ] ) {
            return self::rusak( 'Kepala paket rusak.' );
        }
        $panjang = hexdec( $hex );
        if ( $panjang > self::MAKS_META || $awal + 9 + $panjang > $total ) {
            return self::rusak( 'Panjang meta paket tidak sah.' );
        }
        $meta = json_decode( substr( $data, $awal + 9, $panjang ), true );
        if ( ! is_array( $meta ) || ! isset( $meta['berkas'] ) || ! is_array( $meta['berkas'] ) ) {
            return self::rusak( 'Meta paket tidak sah.' );
        }
        $posisi = $awal + 9 + $panjang;
        $bagian = array();
        foreach ( $meta['berkas'] as $b ) {
            if ( ! is_array( $b ) || ! isset( $b['ukuran'], $b['sha256'] ) || ! is_int( $b['ukuran'] )
                || $b['ukuran'] < 0 || ! is_string( $b['sha256'] ) ) {
                return self::rusak( 'Entri paket tidak sah.' );
            }
            if ( $posisi + $b['ukuran'] > $total ) {
                return self::rusak( 'Paket terpotong.' );
            }
            $isi = 0 === $b['ukuran'] ? '' : (string) substr( $data, $posisi, $b['ukuran'] );
            if ( ! hash_equals( strtolower( $b['sha256'] ), hash( 'sha256', $isi ) ) ) {
                return new WP_Error( 'wpmgr_staging_hash', 'Hash potongan tidak cocok.', array( 'status' => 422 ) );
            }
            $bagian[] = $isi;
            $posisi  += $b['ukuran'];
        }
        if ( $posisi !== $total ) {
            return self::rusak( 'Ada data sisa di akhir paket.' );
        }
        return array( $meta, $bagian );
    }
}
```

- [ ] **Step 6: Kelas staging.**

File: `connector/wp-manager-connector/includes/class-wpmgr-staging.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Pintu masuk endpoint /staging/* (spec §6.1). Semua endpoint mati kecuali
 * admin site menyalakan "Izinkan staging", dan connector di site staging
 * sendiri (WPMGR_STAGING) tidak pernah mengumumkan atau melayaninya.
 */
class WPMGR_Staging {

    const KUNCI_BINER    = '__wpmgr_biner';
    const HOOK_BERSIHKAN = 'wpmgr_staging_bersihkan';

    private static $biner = null;

    public static function mode_staging() {
        return defined( 'WPMGR_STAGING' ) && WPMGR_STAGING;
    }

    public static function fitur_aktif() {
        return WPMGR_Settings::izinkan_staging() && ! self::mode_staging();
    }

    public static function root() {
        return rtrim( str_replace( '\\', '/', ABSPATH ), '/' ) . '/';
    }

    /**
     * Sisa waktu yang aman dipakai satu request. Hosting murah memakai
     * max_execution_time 30 detik; delapan detik disisakan untuk bootstrap
     * WordPress dan pengiriman respons.
     */
    public static function anggaran_detik() {
        $batas = (int) ini_get( 'max_execution_time' );
        if ( $batas <= 0 ) {
            return 20;
        }
        return max( 5, min( 20, $batas - 8 ) );
    }

    public static function bersih( $teks, $panjang ) {
        return WPMGR_Penangkap::potong( $teks, $panjang );
    }

    public static function galat( $kode, $pesan, $status ) {
        return new WP_Error( $kode, $pesan, array( 'status' => $status ) );
    }

    /** Murni: HMAC diperiksa lebih dulu supaya pihak tak dikenal tidak bisa menebak setelan. */
    public static function putuskan( $hasil_hmac, $fitur_aktif ) {
        if ( true !== $hasil_hmac ) {
            return $hasil_hmac;
        }
        if ( ! $fitur_aktif ) {
            return self::galat( 'wpmgr_staging_mati',
                'Staging tidak diizinkan di site ini. Aktifkan "Izinkan staging" di Pengaturan -> WP Manager.', 403 );
        }
        return true;
    }

    public static function guard( $request ) {
        return self::putuskan( WPMGR_REST::guard( $request ), self::fitur_aktif() );
    }

    /** Jalur => array( metode, nama callback di kelas ini ). Diisi Task 3–8. */
    public static function rute() {
        return array();
    }

    public static function daftarkan_route() {
        foreach ( self::rute() as $jalur => $r ) {
            register_rest_route( WPMGR_REST::NS, $jalur, array(
                'methods'             => $r[0],
                'callback'            => array( __CLASS__, $r[1] ),
                'permission_callback' => array( __CLASS__, 'guard' ),
            ) );
        }
    }

    /**
     * Balasan biner (isi berkas, SQL) tanpa base64: 8 MB isi tetap 8 MB di
     * kabel. Isinya ditahan di sini dan dicetak oleh sajikan_biner() pada
     * rest_pre_serve_request, setelah WordPress mengirim header respons
     * (termasuk header anti-cache dari rest_post_dispatch).
     */
    public static function respons_biner( $isi ) {
        self::$biner = (string) $isi;
        $r = new WP_REST_Response( array( self::KUNCI_BINER => true ) );
        $r->header( 'Content-Type', 'application/octet-stream' );
        $r->header( 'Content-Length', (string) strlen( self::$biner ) );
        $r->header( 'X-Wpmgr-Sha256', hash( 'sha256', self::$biner ) );
        return $r;
    }

    public static function sajikan_biner( $served, $result, $request, $server ) {
        if ( $served || null === self::$biner || ! ( $result instanceof WP_HTTP_Response ) ) {
            return $served;
        }
        $data = $result->get_data();
        if ( ! is_array( $data ) || empty( $data[ self::KUNCI_BINER ] ) ) {
            return $served;
        }
        echo self::$biner; // phpcs:ignore WordPress.Security.EscapeOutput -- isi biner, bukan HTML
        self::$biner = null;
        return true;
    }
}
```

- [ ] **Step 7: Setelan "Izinkan staging".** Di `includes/class-wpmgr-settings.php`, tambahkan konstanta setelah `OPT_XFF`:

```php
    const OPT_STAGING   = 'wpmgr_izinkan_staging';
```

tambahkan metode setelah `percayai_xff()`:

```php
    /** Default mati (spec §6.1): admin site yang memutuskan datanya boleh disalin. */
    public static function izinkan_staging() {
        return '1' === (string) get_option( self::OPT_STAGING, '0' );
    }
```

di `tangani_simpan()`, tepat sebelum blok `if ( isset( $_POST['wpmgr_simpan_setelan'] ) ) {`:

```php
        if ( isset( $_POST['wpmgr_simpan_staging'] ) ) {
            check_admin_referer( 'wpmgr_staging' );
            update_option( self::OPT_STAGING, empty( $_POST['wpmgr_izinkan_staging'] ) ? '0' : '1', false );
            set_transient( 'wpmgr_pesan', 'Pengaturan staging disimpan.', 30 );
            wp_safe_redirect( admin_url( 'options-general.php?page=wpmgr' ) );
            exit;
        }
```

dan di `render()`, sebelum `</div>` penutup `wrap`:

```php
            <h2>Staging</h2>
            <?php if ( WPMGR_Staging::mode_staging() ) : ?>
                <p>Site ini adalah <strong>salinan staging</strong> yang dikelola dashboard WP Manager.
                   Pemantauan dan endpoint staging dimatikan di sini.</p>
            <?php else : ?>
                <form method="post">
                    <?php wp_nonce_field( 'wpmgr_staging' ); ?>
                    <input type="hidden" name="wpmgr_simpan_staging" value="1">
                    <p>
                        <label>
                            <input type="checkbox" name="wpmgr_izinkan_staging" value="1" <?php checked( self::izinkan_staging() ); ?>>
                            Izinkan staging
                        </label>
                    </p>
                    <p class="description">
                        Bila diaktifkan, dashboard WP Manager dapat menyalin seluruh berkas dan database site ini
                        ke server staging, lalu mendorong perubahan dari staging kembali ke site ini (dengan
                        snapshot sebelumnya). Matikan kapan saja untuk menutup akses tersebut.
                    </p>
                    <?php submit_button( 'Simpan pengaturan staging' ); ?>
                </form>
            <?php endif; ?>
```

- [ ] **Step 8: Fitur, route, dan versi.** Di `includes/class-wpmgr-skema.php`, ganti `fitur()`:

```php
    public static function fitur( $monitoring_mati, $staging = false ) {
        $fitur = array( 'self_update' );
        if ( ! $monitoring_mati ) {
            $fitur[] = 'events';
            $fitur[] = 'traffic';
        }
        if ( $staging ) {
            $fitur[] = 'staging';
        }
        return $fitur;
    }
```

Di `hapus_semua()`, ganti daftar opsi di `foreach ( array( 'wpmgr_site_id', ... ) as $opsi )` menjadi:

```php
        foreach ( array( 'wpmgr_site_id', 'wpmgr_secret', 'wpmgr_dashboard_url', self::OPT_VERSI,
                         'wpmgr_percayai_xff', 'wpmgr_izinkan_staging', 'wpmgr_dorong_kunci' ) as $opsi ) {
```

Di `includes/class-wpmgr-rest.php`, di `ping()` dan `inventory()`, ganti `WPMGR_Skema::fitur( WPMGR_Skema::monitoring_mati() )` menjadi `WPMGR_Skema::fitur( WPMGR_Skema::monitoring_mati(), WPMGR_Staging::fitur_aktif() )`, dan tambahkan baris terakhir di `daftarkan_route()`:

```php
        WPMGR_Staging::daftarkan_route();
```

Di `wp-manager-connector.php`: ubah header `Version:     2.0.0` menjadi `Version:     3.0.0`, `define( 'WPMGR_VERSION', '2.0.0' );` menjadi `define( 'WPMGR_VERSION', '3.0.0' );`, `define( 'WPMGR_VERSI_SKEMA', 2 );` menjadi `define( 'WPMGR_VERSI_SKEMA', 3 );`. Tambahkan setelah `require_once WPMGR_DIR . 'includes/class-wpmgr-sso.php';`:

```php
require_once WPMGR_DIR . 'includes/class-wpmgr-staging-path.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-staging-paket.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-staging.php';
```

dan setelah baris `add_filter( 'rest_post_dispatch', ... );`:

```php
add_filter( 'rest_pre_serve_request', array( 'WPMGR_Staging', 'sajikan_biner' ), 10, 4 );
```

- [ ] **Step 9: PHPUnit (8.3 dan 7.4).** Run: `cd connector && vendor/bin/phpunit`, lalu perintah PHP 7.4 dari Global Constraints. Expected: keduanya `OK`, dengan dua test symlink `skipped` bila Windows tidak mengizinkan symlink (di container PHP 7.4 keduanya berjalan).

- [ ] **Step 10: Commit.**

```bash
git add connector
git commit -m "feat(connector): fondasi staging 3.0 (setelan, path aman, paket biner, guard)"
```

---

### Task 3: Endpoint `/staging/manifest`

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-staging-manifest.php`, `connector/tests/ManifestTest.php`
- Modify: `includes/class-wpmgr-staging.php`, `wp-manager-connector.php`, `connector/tests/bootstrap.php`

**Interfaces:**
- Consumes: Task 2 (`WPMGR_Staging_Path`, `WPMGR_Staging::bersih`, `rute()`).
- Produces:
  - `GET /wp-json/wpmgr/v1/staging/manifest?kursor=<path>&batas=<n>` (HMAC) → `{berkas: [{path, ukuran, mtime, hash|null}], dilewati: [{path, alasan}] (maks 50), jumlah_dilewati, kursor: string|null, lagi: bool, info?}`. `info` hanya ada di halaman pertama (kursor kosong).
  - Bentuk `info`:
    - `{php, wp, table_prefix, charset, home, siteurl, multisite, konten_di_luar, batas_unggah}`;
    - `tabel: [{nama, baris, ukuran, mesin, pk: [kolom]}]`, `tabel_dilewati: int`.
  - `WPMGR_Staging_Manifest`:
    - penelusuran: `jalan( $akar, $kursor, $batas, $tenggat ): array`, `batas( $n ): int` (1..5000, default 5000);
    - info dan tabel: `info( $wpdb, $akar, $konten ): array`, `tabel( $wpdb ): array( $tabel, $dilewati )`, `pk( $wpdb, $tabel ): array`, `nama_tabel_sah( $nama, $prefix ): bool`;
    - batas unggah: `ke_byte( $ini ): int`, `batas_unggah( $post_max_size ): int` (setengah `post_max_size`, dijepit 256 KB..4 MB).
  - Tunable statis: `$maks_hash` (50 MB), `$anggaran_hash` (512 MB per request).

Penelusuran adalah DFS dengan nama diurutkan `strcmp` per direktori. Kursor adalah path berkas terakhir yang sudah dikirim. Pada request berikutnya, di setiap tingkat yang masih "selaras" dengan kursor, nama yang lebih kecil dari komponen kursor dilewati, direktori yang sama dimasuki, dan berkas kursor sendiri dilewati. Membandingkan path utuh dengan `strcmp` tidak cukup: `"a-b.txt" < "a/x.txt"` secara byte, padahal DFS mengunjungi isi `a/` lebih dulu. Setiap request dijamin mengirim minimal satu berkas (tenggat dan anggaran hash hanya diperiksa setelah ada berkas), sehingga kursor selalu maju.

- [ ] **Step 1: Tulis test yang gagal.** Tambahkan `require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-manifest.php';` ke akhir `bootstrap.php`.

File: `connector/tests/ManifestTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

if ( ! function_exists( 'get_bloginfo' ) ) {
    function get_bloginfo( $apa = '' ) {
        return 'version' === $apa ? '6.5' : '';
    }
}
if ( ! function_exists( 'home_url' ) ) {
    function home_url() {
        return 'https://contoh.test';
    }
}
if ( ! function_exists( 'site_url' ) ) {
    function site_url() {
        return 'https://contoh.test/wp';
    }
}
if ( ! function_exists( 'is_multisite' ) ) {
    function is_multisite() {
        return false;
    }
}

final class WPMGR_FakeWpdbManifest {
    public $prefix  = 'wp_';
    public $charset = 'utf8mb4';
    public $jawaban = array();

    public function esc_like( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function prepare( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        return vsprintf( str_replace( '%s', "'%s'", $sql ), $args );
    }

    public function get_results( $sql, $format = null ) {
        foreach ( $this->jawaban as $pola => $hasil ) {
            if ( false !== strpos( $sql, $pola ) ) {
                return $hasil;
            }
        }
        return array();
    }
}

final class ManifestTest extends TestCase {

    private $akar;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-man-' . bin2hex( random_bytes( 6 ) ) . '/';
        mkdir( $this->akar, 0777, true );
        WPMGR_Staging_Manifest::$maks_hash     = 52428800;
        WPMGR_Staging_Manifest::$anggaran_hash = 536870912;
    }

    protected function tearDown(): void {
        StagingDasarTest::hapus( rtrim( $this->akar, '/' ) );
    }

    private function tulis( $rel, $isi = 'x' ) {
        $abs = $this->akar . $rel;
        if ( ! is_dir( dirname( $abs ) ) ) {
            mkdir( dirname( $abs ), 0777, true );
        }
        return false !== @file_put_contents( $abs, $isi );
    }

    private function jalan_semua( $batas ) {
        $semua  = array();
        $kursor = '';
        for ( $i = 0; $i < 100; $i++ ) {
            $h = WPMGR_Staging_Manifest::jalan( $this->akar, $kursor, $batas, microtime( true ) + 30 );
            foreach ( $h['berkas'] as $b ) {
                $semua[] = $b['path'];
            }
            if ( ! $h['lagi'] ) {
                return $semua;
            }
            $kursor = $h['kursor'];
        }
        $this->fail( 'Paging tidak berhenti.' );
    }

    public function test_menelusuri_dan_mengecualikan(): void {
        foreach ( array( 'index.php', 'wp-config.php', 'debug.log', 'wp-content/cache/a.html',
                         'wp-content/updraft/b.zip', 'wp-content/backups-dup-lite/c.zip',
                         'wp-content/wpmgr-dorong/d/e', 'wp-content/themes/t/style.css',
                         'wp-content/uploads/2026/09/f.jpg' ) as $p ) {
            $this->tulis( $p, 'isi-' . $p );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertFalse( $h['lagi'] );
        $this->assertNull( $h['kursor'] );
        $this->assertSame(
            array( 'index.php', 'wp-content/themes/t/style.css', 'wp-content/uploads/2026/09/f.jpg' ),
            array_column( $h['berkas'], 'path' )
        );
        $this->assertSame( hash( 'sha256', 'isi-index.php' ), $h['berkas'][0]['hash'] );
        $this->assertSame( strlen( 'isi-index.php' ), $h['berkas'][0]['ukuran'] );
        $this->assertIsInt( $h['berkas'][0]['mtime'] );
    }

    public function test_berkas_besar_tanpa_hash(): void {
        WPMGR_Staging_Manifest::$maks_hash = 5;
        $this->tulis( 'kecil.txt', '12345' );
        $this->tulis( 'besar.bin', '123456' );
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $per_path = array_column( $h['berkas'], 'hash', 'path' );
        $this->assertNull( $per_path['besar.bin'] );
        $this->assertSame( hash( 'sha256', '12345' ), $per_path['kecil.txt'] );
    }

    public function test_paging_mengikuti_urutan_dfs_tanpa_duplikat(): void {
        foreach ( array( 'a/x.txt', 'a/y.txt', 'a-b.txt', 'b.txt', 'c/d/e.txt' ) as $p ) {
            $this->tulis( $p );
        }
        $harapan = array( 'a/x.txt', 'a/y.txt', 'a-b.txt', 'b.txt', 'c/d/e.txt' );
        $this->assertSame( $harapan, $this->jalan_semua( 5000 ) );
        $this->assertSame( $harapan, $this->jalan_semua( 2 ) );
        $this->assertSame( $harapan, $this->jalan_semua( 1 ) );
    }

    public function test_kursor_berkas_yang_sudah_dihapus_tetap_maju(): void {
        foreach ( array( 'a/w.txt', 'a/z.txt', 'b.txt' ) as $p ) {
            $this->tulis( $p );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, 'a/xx.txt', 5000, microtime( true ) + 30 );
        $this->assertSame( array( 'a/z.txt', 'b.txt' ), array_column( $h['berkas'], 'path' ) );
    }

    public function test_nama_non_ascii_bukan_utf8_dan_panjang(): void {
        $panjang = 'wp-content/uploads/' . str_repeat( 'é', 100 ) . '.txt';
        $this->tulis( 'wp-content/uploads/ü-berkas.txt' );
        $this->tulis( $panjang );
        $this->tulis( "wp-content/uploads/\xff\xfe.txt" );
        // Windows menyimpan nama berkas sebagai UTF-16 dan PHP mengembalikannya
        // sudah dikonversi; hanya di Linux nama bukan UTF-8 benar-benar ada.
        $ada_bukan_utf8 = false;
        foreach ( scandir( $this->akar . 'wp-content/uploads' ) as $n ) {
            $ada_bukan_utf8 = $ada_bukan_utf8 || 1 !== preg_match( '//u', $n );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $path = array_column( $h['berkas'], 'path' );
        $this->assertContains( 'wp-content/uploads/ü-berkas.txt', $path );
        $this->assertContains( $panjang, $path );
        foreach ( $path as $p ) {
            $this->assertSame( 1, preg_match( '//u', $p ) );
        }
        if ( $ada_bukan_utf8 ) {
            $this->assertGreaterThanOrEqual( 1, $h['jumlah_dilewati'] );
            $this->assertSame( 'nama_bukan_utf8', $h['dilewati'][0]['alasan'] );
        }
        // Seluruh hasil tetap bisa dikodekan JSON: satu nama rusak tidak boleh
        // membuat json_encode() gagal dan membungkam seluruh manifest.
        $this->assertNotFalse( json_encode( $h ) );
    }

    public function test_symlink_dilewati(): void {
        $this->tulis( 'asli.txt' );
        if ( ! @symlink( $this->akar . 'asli.txt', $this->akar . 'tautan.txt' ) ) {
            $this->markTestSkipped( 'Symlink tidak didukung di sistem ini.' );
        }
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertSame( array( 'asli.txt' ), array_column( $h['berkas'], 'path' ) );
        $this->assertSame( 'symlink', $h['dilewati'][0]['alasan'] );
    }

    public function test_anggaran_hash_dan_tenggat_menjamin_kemajuan(): void {
        $this->tulis( 'a.txt', '1234' );
        $this->tulis( 'b.txt', '1234' );
        WPMGR_Staging_Manifest::$anggaran_hash = 5;
        $h = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) + 30 );
        $this->assertSame( array( 'a.txt' ), array_column( $h['berkas'], 'path' ) );
        $this->assertTrue( $h['lagi'] );
        $this->assertSame( 'a.txt', $h['kursor'] );

        WPMGR_Staging_Manifest::$anggaran_hash = 536870912;
        $lewat = WPMGR_Staging_Manifest::jalan( $this->akar, '', 5000, microtime( true ) - 1 );
        $this->assertCount( 1, $lewat['berkas'] );
        $this->assertTrue( $lewat['lagi'] );
    }

    public function test_batas_dijepit(): void {
        $this->assertSame( 5000, WPMGR_Staging_Manifest::batas( null ) );
        $this->assertSame( 5000, WPMGR_Staging_Manifest::batas( 0 ) );
        $this->assertSame( 5000, WPMGR_Staging_Manifest::batas( 99999 ) );
        $this->assertSame( 20, WPMGR_Staging_Manifest::batas( '20' ) );
    }

    public function test_info_dan_tabel(): void {
        $wpdb = new WPMGR_FakeWpdbManifest();
        $wpdb->jawaban = array(
            'SHOW TABLE STATUS' => array(
                array( 'Name' => 'wp_posts', 'Rows' => '12', 'Data_length' => '1000', 'Index_length' => '24', 'Engine' => 'InnoDB' ),
                array( 'Name' => 'wp_tampilan', 'Rows' => null, 'Data_length' => null, 'Index_length' => null, 'Engine' => null ),
                array( 'Name' => 'wp_bad-name', 'Rows' => '1', 'Data_length' => '1', 'Index_length' => '0', 'Engine' => 'MyISAM' ),
                array( 'Name' => 'lain_posts', 'Rows' => '1', 'Data_length' => '1', 'Index_length' => '0', 'Engine' => 'MyISAM' ),
            ),
            'SHOW KEYS FROM `wp_posts`' => array(
                array( 'Column_name' => 'ID', 'Seq_in_index' => '1' ),
            ),
        );
        $info = WPMGR_Staging_Manifest::info( $wpdb, $this->akar, rtrim( $this->akar, '/' ) . '/wp-content' );
        $this->assertSame( 'wp_', $info['table_prefix'] );
        $this->assertSame( 'https://contoh.test', $info['home'] );
        $this->assertFalse( $info['konten_di_luar'] );
        $this->assertSame( array( array( 'nama' => 'wp_posts', 'baris' => 12, 'ukuran' => 1024,
                                         'mesin' => 'InnoDB', 'pk' => array( 'ID' ) ) ), $info['tabel'] );
        $this->assertSame( 2, $info['tabel_dilewati'] );
        $luar = WPMGR_Staging_Manifest::info( $wpdb, $this->akar, sys_get_temp_dir() . '/konten-lain' );
        $this->assertTrue( $luar['konten_di_luar'] );
    }

    public function test_pk_komposit_berurutan(): void {
        $wpdb = new WPMGR_FakeWpdbManifest();
        $wpdb->jawaban = array( 'SHOW KEYS FROM `wp_x`' => array(
            array( 'Column_name' => 'b', 'Seq_in_index' => '2' ),
            array( 'Column_name' => 'a', 'Seq_in_index' => '1' ),
        ) );
        $this->assertSame( array( 'a', 'b' ), WPMGR_Staging_Manifest::pk( $wpdb, 'wp_x' ) );
    }

    public function test_batas_unggah(): void {
        $this->assertSame( 4194304, WPMGR_Staging_Manifest::batas_unggah( '8M' ) );
        $this->assertSame( 4194304, WPMGR_Staging_Manifest::batas_unggah( '64M' ) );
        $this->assertSame( 1048576, WPMGR_Staging_Manifest::batas_unggah( '2M' ) );
        $this->assertSame( 262144, WPMGR_Staging_Manifest::batas_unggah( '100K' ) );
        $this->assertSame( 4194304, WPMGR_Staging_Manifest::batas_unggah( '0' ) );
        $this->assertSame( 2147483648, WPMGR_Staging_Manifest::ke_byte( '2G' ) );
    }
}
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `cd connector && vendor/bin/phpunit --filter ManifestTest`. Expected: fatal `Failed opening required '.../class-wpmgr-staging-manifest.php'`.

- [ ] **Step 3: Implementasikan.**

File: `connector/wp-manager-connector/includes/class-wpmgr-staging-manifest.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Manifest untuk tarik (spec §6.2 langkah 1): daftar berkas dengan ukuran,
 * mtime, dan sha256 (hanya ≤ 50 MB), dipaging lewat kursor supaya site
 * dengan ratusan ribu berkas tetap muat dalam batas 30 detik per request.
 */
class WPMGR_Staging_Manifest {

    const BATAS_ENTRI    = 5000;
    const MAKS_KEDALAMAN = 64;
    const MAKS_CONTOH    = 50;

    public static $maks_hash     = 52428800;
    public static $anggaran_hash = 536870912;

    public static function batas( $nilai ) {
        $n = (int) $nilai;
        return ( $n < 1 || $n > self::BATAS_ENTRI ) ? self::BATAS_ENTRI : $n;
    }

    public static function jalan( $akar, $kursor, $batas, $tenggat ) {
        $ctx = array(
            'berkas'          => array(),
            'dilewati'        => array(),
            'jumlah_dilewati' => 0,
            'batas'           => self::batas( $batas ),
            'tenggat'         => (float) $tenggat,
            'terhash'         => 0,
            'berhenti'        => false,
        );
        $kursor = (string) $kursor;
        $bagian = ( '' === $kursor ) ? array() : explode( '/', $kursor );
        self::telusuri( $akar, '', $bagian, ! empty( $bagian ), $ctx, 0 );
        $jumlah = count( $ctx['berkas'] );
        return array(
            'berkas'          => $ctx['berkas'],
            'dilewati'        => $ctx['dilewati'],
            'jumlah_dilewati' => $ctx['jumlah_dilewati'],
            'kursor'          => ( $ctx['berhenti'] && $jumlah ) ? $ctx['berkas'][ $jumlah - 1 ]['path'] : null,
            'lagi'            => $ctx['berhenti'] && $jumlah > 0,
        );
    }

    private static function lewati( array &$ctx, $rel, $alasan ) {
        $ctx['jumlah_dilewati']++;
        if ( count( $ctx['dilewati'] ) < self::MAKS_CONTOH ) {
            // Nama yang dilewati bisa berupa byte bukan UTF-8; hanya versi
            // yang sudah dibersihkan yang dikirim, sebagai teks tampilan.
            $ctx['dilewati'][] = array( 'path' => WPMGR_Staging::bersih( $rel, 300 ), 'alasan' => $alasan );
        }
    }

    /** Tenggat dan anggaran hanya berlaku setelah ada berkas: kursor harus selalu maju. */
    private static function habis( array $ctx ) {
        return count( $ctx['berkas'] ) > 0 && microtime( true ) >= $ctx['tenggat'];
    }

    private static function telusuri( $akar, $rel_dir, array $kursor, $selaras, array &$ctx, $kedalaman ) {
        if ( $kedalaman > self::MAKS_KEDALAMAN ) {
            self::lewati( $ctx, rtrim( $rel_dir, '/' ), 'terlalu_dalam' );
            return;
        }
        $nama = @scandir( $akar . $rel_dir ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $nama ) {
            self::lewati( $ctx, rtrim( $rel_dir, '/' ), 'tidak_terbaca' );
            return;
        }
        sort( $nama, SORT_STRING );
        $target   = ( $selaras && isset( $kursor[ $kedalaman ] ) ) ? $kursor[ $kedalaman ] : null;
        $terakhir = count( $kursor ) - 1;
        foreach ( $nama as $n ) {
            if ( $ctx['berhenti'] ) {
                return;
            }
            if ( '.' === $n || '..' === $n ) {
                continue;
            }
            $masuk_selaras = false;
            if ( null !== $target ) {
                $banding = strcmp( $n, $target );
                if ( $banding < 0 ) {
                    continue;
                }
                if ( 0 === $banding ) {
                    if ( $kedalaman === $terakhir ) {
                        continue;
                    }
                    $masuk_selaras = true;
                }
            }
            $rel = $rel_dir . $n;
            $abs = $akar . $rel;
            if ( 1 !== preg_match( '//u', $n ) ) {
                self::lewati( $ctx, $rel, 'nama_bukan_utf8' );
                continue;
            }
            if ( is_link( $abs ) ) {
                self::lewati( $ctx, $rel, 'symlink' );
                continue;
            }
            if ( is_dir( $abs ) ) {
                if ( WPMGR_Staging_Path::dikecualikan( $rel ) ) {
                    continue;
                }
                if ( self::habis( $ctx ) ) {
                    $ctx['berhenti'] = true;
                    return;
                }
                self::telusuri( $akar, $rel . '/', $kursor, $masuk_selaras, $ctx, $kedalaman + 1 );
                continue;
            }
            if ( ! is_file( $abs ) || WPMGR_Staging_Path::dikecualikan( $rel ) ) {
                continue;
            }
            if ( is_wp_error( WPMGR_Staging_Path::normalisasi( $rel ) ) ) {
                self::lewati( $ctx, $rel, 'path_tidak_sah' );
                continue;
            }
            $ukuran = @filesize( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            $mtime  = @filemtime( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( false === $ukuran || false === $mtime || ! is_readable( $abs ) ) {
                self::lewati( $ctx, $rel, 'tidak_terbaca' );
                continue;
            }
            $perlu_hash = $ukuran <= self::$maks_hash;
            if ( self::habis( $ctx )
                || ( $perlu_hash && $ctx['terhash'] > 0 && $ctx['terhash'] + $ukuran > self::$anggaran_hash ) ) {
                $ctx['berhenti'] = true;
                return;
            }
            $hash = null;
            if ( $perlu_hash ) {
                $hash = @hash_file( 'sha256', $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
                if ( false === $hash ) {
                    self::lewati( $ctx, $rel, 'tidak_terbaca' );
                    continue;
                }
                $ctx['terhash'] += $ukuran;
            }
            $ctx['berkas'][] = array( 'path' => $rel, 'ukuran' => (int) $ukuran, 'mtime' => (int) $mtime, 'hash' => $hash );
            if ( count( $ctx['berkas'] ) >= $ctx['batas'] ) {
                $ctx['berhenti'] = true;
                return;
            }
        }
    }

    public static function nama_tabel_sah( $nama, $prefix ) {
        return 1 === preg_match( '/^[A-Za-z0-9_$]{1,64}\z/', (string) $nama ) && 0 === strpos( (string) $nama, (string) $prefix );
    }

    public static function pk( $wpdb, $tabel ) {
        $kunci = $wpdb->get_results( "SHOW KEYS FROM `{$tabel}` WHERE Key_name = 'PRIMARY'", ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL -- nama tabel sudah divalidasi regex
        $kunci = is_array( $kunci ) ? $kunci : array();
        usort( $kunci, function ( $a, $b ) {
            return (int) $a['Seq_in_index'] - (int) $b['Seq_in_index'];
        } );
        $kolom = array();
        foreach ( $kunci as $k ) {
            $nama = isset( $k['Column_name'] ) ? (string) $k['Column_name'] : '';
            if ( 1 !== preg_match( '/^[A-Za-z0-9_$]{1,64}\z/', $nama ) ) {
                // Nama kolom yang tidak bisa kita kutip dengan aman: perlakukan
                // tabel ini sebagai tanpa PK (LIMIT/OFFSET).
                return array();
            }
            $kolom[] = $nama;
        }
        return $kolom;
    }

    public static function tabel( $wpdb ) {
        $baris    = $wpdb->get_results( $wpdb->prepare( 'SHOW TABLE STATUS LIKE %s', $wpdb->esc_like( $wpdb->prefix ) . '%' ), ARRAY_A );
        $hasil    = array();
        $dilewati = 0;
        foreach ( (array) $baris as $b ) {
            $nama = isset( $b['Name'] ) ? (string) $b['Name'] : '';
            if ( ! self::nama_tabel_sah( $nama, $wpdb->prefix ) ) {
                $dilewati++;
                continue;
            }
            if ( empty( $b['Engine'] ) ) {
                continue; // VIEW: tidak diekspor, dibuat ulang oleh plugin pemiliknya.
            }
            if ( count( $hasil ) >= 2000 ) {
                $dilewati++;
                continue;
            }
            $hasil[] = array(
                'nama'   => $nama,
                'baris'  => (int) $b['Rows'],
                'ukuran' => (int) $b['Data_length'] + (int) $b['Index_length'],
                'mesin'  => (string) $b['Engine'],
                'pk'     => self::pk( $wpdb, $nama ),
            );
        }
        return array( $hasil, $dilewati );
    }

    public static function ke_byte( $nilai ) {
        $nilai = trim( (string) $nilai );
        if ( ! preg_match( '/^([0-9]+)\s*([KkMmGg]?)\z/', $nilai, $m ) ) {
            return 0;
        }
        $n    = (int) $m[1];
        $kali = array( '' => 1, 'k' => 1024, 'm' => 1048576, 'g' => 1073741824 );
        return $n * $kali[ strtolower( $m[2] ) ];
    }

    /** Koreksi #15: body 8 MB ditolak hosting dengan post_max_size=8M. */
    public static function batas_unggah( $post_max_size ) {
        $b = self::ke_byte( $post_max_size );
        if ( $b <= 0 ) {
            return 4194304;
        }
        return max( 262144, min( 4194304, intdiv( $b, 2 ) ) );
    }

    public static function info( $wpdb, $akar, $konten ) {
        list( $tabel, $dilewati ) = self::tabel( $wpdb );
        $konten = rtrim( str_replace( '\\', '/', (string) $konten ), '/' ) . '/';
        $akar   = rtrim( str_replace( '\\', '/', (string) $akar ), '/' ) . '/';
        return array(
            'php'            => PHP_VERSION,
            'wp'             => (string) get_bloginfo( 'version' ),
            'table_prefix'   => (string) $wpdb->prefix,
            'charset'        => (string) $wpdb->charset,
            'home'           => (string) home_url(),
            'siteurl'        => (string) site_url(),
            'multisite'      => (bool) is_multisite(),
            'konten_di_luar' => 0 !== strpos( $konten, (string) $akar ),
            'batas_unggah'   => self::batas_unggah( ini_get( 'post_max_size' ) ),
            'tabel'          => $tabel,
            'tabel_dilewati' => $dilewati,
        );
    }
}
```

Di `class-wpmgr-staging.php`, ganti `rute()` dan tambahkan callback:

```php
    public static function rute() {
        return array(
            '/staging/manifest' => array( 'GET', 'manifest' ),
        );
    }

    public static function manifest( $request ) {
        $kursor = (string) $request->get_param( 'kursor' );
        if ( '' !== $kursor && is_wp_error( WPMGR_Staging_Path::normalisasi( $kursor ) ) ) {
            return self::galat( 'wpmgr_staging_path', 'Kursor manifest tidak sah.', 400 );
        }
        $hasil = WPMGR_Staging_Manifest::jalan( self::root(), $kursor, $request->get_param( 'batas' ),
            microtime( true ) + self::anggaran_detik() );
        if ( '' === $kursor ) {
            global $wpdb;
            $hasil['info'] = WPMGR_Staging_Manifest::info( $wpdb, self::root(), WP_CONTENT_DIR );
        }
        return rest_ensure_response( $hasil );
    }
```

Tambahkan `require_once WPMGR_DIR . 'includes/class-wpmgr-staging-manifest.php';` ke berkas utama setelah `class-wpmgr-staging-paket.php`.

- [ ] **Step 4: PHPUnit 8.3 dan 7.4.** Expected: `OK`. Di container PHP 7.4 (Linux), test nama bukan UTF-8 dan symlink ikut berjalan.

- [ ] **Step 5: Commit.**

```bash
git add connector
git commit -m "feat(connector): manifest staging dengan kursor DFS dan anggaran hash"
```

---

### Task 4: Endpoint `/staging/file`

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-staging-file.php`, `connector/tests/FileTest.php`
- Modify: `includes/class-wpmgr-staging.php`, `wp-manager-connector.php`, `connector/tests/bootstrap.php`

**Interfaces:**
- Consumes: Task 2 (`WPMGR_Staging_Path::untuk_dibaca`, `WPMGR_Staging_Paket::susun`, `respons_biner`).
- Produces:
  - `POST /wp-json/wpmgr/v1/staging/file` (HMAC), dengan body berupa salah satu dari:
    - `{"berkas": [path, ...]}` (1..2000 path, total isi ≤ 8 MB). Balasan biner berupa paket dengan `meta.berkas[i] = {path, mtime, ukuran, sha256}` atau `{path, hilang: true, ukuran: 0, sha256}` untuk berkas yang terhapus sejak manifest.
    - `{"rentang": {"path", "dari", "panjang"}}` (`panjang` 1..8 MB). Balasan biner berupa paket satu bagian dengan `meta.berkas[0] = {path, dari, total, mtime, ukuran, sha256}`.
  - Galat:
    - path berbahaya atau dikecualikan menghasilkan 400 `wpmgr_staging_path` untuk seluruh request (dashboard tidak pernah memintanya; bila ia meminta, ada yang salah);
    - melebihi batas menghasilkan 413 `wpmgr_staging_terlalu_besar`;
    - body salah menghasilkan 400 `wpmgr_staging_permintaan`.
  - `WPMGR_Staging_File::ambil( $akar, $p ): string|WP_Error`, `rentang( $akar, $r )`; tunable statis `$maks_paket = 8388608`.

- [ ] **Step 1: Tulis test yang gagal.** Tambahkan `require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-file.php';` ke akhir `bootstrap.php`.

File: `connector/tests/FileTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class FileTest extends TestCase {

    private $akar;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-file-' . bin2hex( random_bytes( 6 ) ) . '/';
        mkdir( $this->akar . 'wp-content/uploads', 0777, true );
        file_put_contents( $this->akar . 'index.php', '<?php // indeks' );
        file_put_contents( $this->akar . 'wp-content/uploads/biner.bin', "\0\xff\x1a" . str_repeat( 'z', 100 ) );
        file_put_contents( $this->akar . 'wp-config.php', '<?php // rahasia' );
        WPMGR_Staging_File::$maks_paket = 8388608;
    }

    protected function tearDown(): void {
        StagingDasarTest::hapus( rtrim( $this->akar, '/' ) );
    }

    public function test_paket_beberapa_berkas(): void {
        $data = WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'index.php', 'wp-content/uploads/biner.bin' ) ) );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( $data );
        $this->assertSame( '<?php // indeks', $bagian[0] );
        $this->assertSame( "\0\xff\x1a" . str_repeat( 'z', 100 ), $bagian[1] );
        $this->assertSame( 'index.php', $meta['berkas'][0]['path'] );
        $this->assertSame( filemtime( $this->akar . 'index.php' ), $meta['berkas'][0]['mtime'] );
        $this->assertArrayNotHasKey( 'hilang', $meta['berkas'][0] );
    }

    public function test_berkas_yang_hilang_ditandai(): void {
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai(
            WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'sudah-dihapus.php', 'index.php' ) ) )
        );
        $this->assertTrue( $meta['berkas'][0]['hilang'] );
        $this->assertSame( '', $bagian[0] );
        $this->assertSame( '<?php // indeks', $bagian[1] );
    }

    public function test_path_berbahaya_menolak_seluruh_permintaan(): void {
        foreach ( array( 'wp-config.php', '../index.php', '/etc/passwd', 'wp-content/cache/a' ) as $p ) {
            $hasil = WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'index.php', $p ) ) );
            $this->assertInstanceOf( WP_Error::class, $hasil, $p );
        }
    }

    public function test_body_salah(): void {
        foreach ( array( null, 'x', array(), array( 'berkas' => array() ), array( 'berkas' => array( 5 ) ),
                         array( 'berkas' => array_fill( 0, 2001, 'index.php' ) ),
                         array( 'rentang' => array( 'path' => 'index.php', 'dari' => '0', 'panjang' => 5 ) ),
                         array( 'rentang' => array( 'path' => 'index.php', 'dari' => -1, 'panjang' => 5 ) ),
                         array( 'rentang' => array( 'path' => 'index.php', 'dari' => 0, 'panjang' => 0 ) ) ) as $body ) {
            $hasil = WPMGR_Staging_File::ambil( $this->akar, $body );
            $this->assertInstanceOf( WP_Error::class, $hasil );
            $this->assertSame( 'wpmgr_staging_permintaan', $hasil->get_error_code() );
        }
    }

    public function test_melebihi_batas_paket(): void {
        WPMGR_Staging_File::$maks_paket = 20;
        $hasil = WPMGR_Staging_File::ambil( $this->akar, array( 'berkas' => array( 'index.php', 'wp-content/uploads/biner.bin' ) ) );
        $this->assertSame( 'wpmgr_staging_terlalu_besar', $hasil->get_error_code() );
        $this->assertSame( array( 'status' => 413 ), $hasil->get_error_data() );
    }

    public function test_rentang_berkas_besar(): void {
        $isi = "\0\xff\x1a" . str_repeat( 'z', 100 );
        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'wp-content/uploads/biner.bin', 'dari' => 2, 'panjang' => 10 ) ) ) );
        $this->assertSame( substr( $isi, 2, 10 ), $bagian[0] );
        $this->assertSame( 2, $meta['berkas'][0]['dari'] );
        $this->assertSame( strlen( $isi ), $meta['berkas'][0]['total'] );

        list( $meta, $bagian ) = WPMGR_Staging_Paket::urai( WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'wp-content/uploads/biner.bin', 'dari' => 100, 'panjang' => 50 ) ) ) );
        $this->assertSame( substr( $isi, 100 ), $bagian[0] );

        list( , $bagian ) = WPMGR_Staging_Paket::urai( WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'wp-content/uploads/biner.bin', 'dari' => 500, 'panjang' => 5 ) ) ) );
        $this->assertSame( '', $bagian[0] );
    }

    public function test_rentang_melebihi_batas(): void {
        WPMGR_Staging_File::$maks_paket = 8;
        $hasil = WPMGR_Staging_File::ambil( $this->akar,
            array( 'rentang' => array( 'path' => 'index.php', 'dari' => 0, 'panjang' => 9 ) ) );
        $this->assertSame( 'wpmgr_staging_terlalu_besar', $hasil->get_error_code() );
    }
}
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: fatal `Failed opening required '.../class-wpmgr-staging-file.php'`.

- [ ] **Step 3: Implementasikan.**

File: `connector/wp-manager-connector/includes/class-wpmgr-staging-file.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Isi berkas untuk tarik dan snapshot (spec §6.2 langkah 2): satu paket
 * berisi beberapa berkas kecil, atau satu rentang byte berkas besar.
 */
class WPMGR_Staging_File {

    const MAKS_JUMLAH = 2000;

    public static $maks_paket = 8388608;

    private static function salah( $pesan ) {
        return WPMGR_Staging::galat( 'wpmgr_staging_permintaan', $pesan, 400 );
    }

    private static function terlalu_besar() {
        return WPMGR_Staging::galat( 'wpmgr_staging_terlalu_besar', 'Permintaan melebihi batas 8 MB per potongan.', 413 );
    }

    public static function ambil( $akar, $p ) {
        if ( ! is_array( $p ) ) {
            return self::salah( 'Body permintaan bukan objek.' );
        }
        if ( isset( $p['rentang'] ) ) {
            return self::rentang( $akar, $p['rentang'] );
        }
        if ( ! isset( $p['berkas'] ) || ! is_array( $p['berkas'] ) || count( $p['berkas'] ) < 1
            || count( $p['berkas'] ) > self::MAKS_JUMLAH ) {
            return self::salah( 'Daftar berkas kosong atau terlalu panjang.' );
        }
        $meta  = array( 'berkas' => array() );
        $isi   = array();
        $total = 0;
        foreach ( array_values( $p['berkas'] ) as $rel ) {
            if ( ! is_string( $rel ) ) {
                return self::salah( 'Path berkas bukan string.' );
            }
            $abs = WPMGR_Staging_Path::untuk_dibaca( $akar, $rel );
            if ( is_wp_error( $abs ) ) {
                if ( 'wpmgr_staging_tidak_ada' === $abs->get_error_code() ) {
                    // Terhapus sejak manifest dibuat: dashboard menghapusnya di staging.
                    $meta['berkas'][] = array( 'path' => $rel, 'hilang' => true );
                    $isi[]            = '';
                    continue;
                }
                return $abs;
            }
            clearstatcache( true, $abs );
            $ukuran = (int) @filesize( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( $total + $ukuran > self::$maks_paket ) {
                return self::terlalu_besar();
            }
            $data = @file_get_contents( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( false === $data ) {
                return WPMGR_Staging::galat( 'wpmgr_staging_baca', 'Berkas tidak dapat dibaca.', 500 );
            }
            // Berkas bisa tumbuh di antara filesize() dan pembacaan.
            if ( $total + strlen( $data ) > self::$maks_paket ) {
                return self::terlalu_besar();
            }
            $total           += strlen( $data );
            $meta['berkas'][] = array( 'path' => $rel, 'mtime' => (int) @filemtime( $abs ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            $isi[]            = $data;
        }
        return WPMGR_Staging_Paket::susun( $meta, $isi );
    }

    public static function rentang( $akar, $r ) {
        if ( ! is_array( $r ) || ! isset( $r['path'], $r['dari'], $r['panjang'] ) || ! is_string( $r['path'] )
            || ! is_int( $r['dari'] ) || ! is_int( $r['panjang'] ) || $r['dari'] < 0 || $r['panjang'] < 1 ) {
            return self::salah( 'Rentang tidak sah.' );
        }
        if ( $r['panjang'] > self::$maks_paket ) {
            return self::terlalu_besar();
        }
        $abs = WPMGR_Staging_Path::untuk_dibaca( $akar, $r['path'] );
        if ( is_wp_error( $abs ) ) {
            return $abs;
        }
        clearstatcache( true, $abs );
        $total = (int) @filesize( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        $mtime = (int) @filemtime( $abs ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        $data  = '';
        if ( $r['dari'] < $total ) {
            $h = @fopen( $abs, 'rb' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( false === $h ) {
                return WPMGR_Staging::galat( 'wpmgr_staging_baca', 'Berkas tidak dapat dibaca.', 500 );
            }
            fseek( $h, $r['dari'] );
            while ( strlen( $data ) < $r['panjang'] && ! feof( $h ) ) {
                $potong = fread( $h, min( 1048576, $r['panjang'] - strlen( $data ) ) );
                if ( false === $potong || '' === $potong ) {
                    break;
                }
                $data .= $potong;
            }
            fclose( $h );
        }
        return WPMGR_Staging_Paket::susun(
            array( 'berkas' => array( array( 'path' => $r['path'], 'dari' => $r['dari'], 'total' => $total, 'mtime' => $mtime ) ) ),
            array( $data )
        );
    }
}
```

Di `class-wpmgr-staging.php`, tambahkan entri `'/staging/file' => array( 'POST', 'file' ),` ke array `rute()`, dan callback:

```php
    public static function file( $request ) {
        $hasil = WPMGR_Staging_File::ambil( self::root(), json_decode( $request->get_body(), true ) );
        return is_wp_error( $hasil ) ? $hasil : self::respons_biner( $hasil );
    }
```

Tambahkan `require_once WPMGR_DIR . 'includes/class-wpmgr-staging-file.php';` ke berkas utama setelah `class-wpmgr-staging-manifest.php`.

- [ ] **Step 4: PHPUnit 8.3 dan 7.4.** Expected: `OK`.

- [ ] **Step 5: Commit.**

```bash
git add connector
git commit -m "feat(connector): endpoint staging/file (paket dan rentang byte)"
```

---

### Task 5: Endpoint `/staging/tabel`

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-staging-tabel.php`, `connector/tests/TabelTest.php`
- Modify: `includes/class-wpmgr-staging.php`, `wp-manager-connector.php`, `connector/tests/bootstrap.php`

**Interfaces:**
- Consumes: Task 3 (`WPMGR_Staging_Manifest::nama_tabel_sah`, `pk`), Task 2 (paket).
- Produces:
  - `POST /wp-json/wpmgr/v1/staging/tabel` (HMAC), body `{"tabel": "wp_posts", "kursor": "<opak>"|""}`. Balasan berupa paket biner satu bagian (`path: "sql"`) dengan `meta = {tabel, kursor: string|null, selesai: bool, baris: int}`.
  - Isi SQL:
    - potongan pertama (kursor kosong) diawali `DROP TABLE IF EXISTS` dan hasil `SHOW CREATE TABLE`;
    - sisanya `INSERT INTO \`t\` (\`k1\`,…) VALUES (…),(…);` dengan pernyataan ≤ 1 MB dan setiap pernyataan diakhiri `";\n"`;
    - string di-escape seperti `mysqli_real_escape_string` (tidak ada baris baru mentah di dalam literal), kolom biner (`*blob`, `*binary`, `bit`) dan string yang bukan UTF-8 sah ditulis sebagai literal hex `0x…`, dan `NULL` sebagai `NULL`.
  - Kursor opak: base64(JSON), berbentuk `{"pk": [{"s": nilai}|{"x": hex}, …]}` untuk tabel ber-PK atau `{"o": offset}` untuk tabel tanpa PK.
  - `WPMGR_Staging_Tabel`:
    - ekspor: `ekspor( $wpdb, $tabel, $kursor ): array( sql, kursor, selesai, baris ) | WP_Error`, `kolom( $wpdb, $tabel ): array( nama => tipe )`;
    - literal dan escaping: `esc( $s )`, `nilai( $v, $tipe )`, `literal_kunci( $entri, $tipe )`;
    - kursor: `kode_kursor( $data )`, `urai_kursor( $kursor ): array|null|false`.
  - Tunable statis: `$baris` (2000), `$sub` (200 baris per SELECT), `$maks_byte` (6 MB SQL per potongan). Satu baris yang lebih besar dari batas tetap dikirim utuh.

- [ ] **Step 1: Tulis test yang gagal.** Tambahkan `require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-tabel.php';` ke akhir `bootstrap.php`.

File: `connector/tests/TabelTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

if ( ! defined( 'ARRAY_N' ) ) {
    define( 'ARRAY_N', 'ARRAY_N' );
}

final class WPMGR_FakeWpdbTabel {
    public $prefix = 'wp_';
    public $kueri  = array();
    public $kolom  = array();
    public $pk     = array();
    public $baris  = array();
    public $ada    = true;
    public $buat   = "CREATE TABLE `wp_x` (\n  `id` bigint(20) NOT NULL\n) ENGINE=InnoDB";

    public function esc_like( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function prepare( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        return vsprintf( str_replace( '%s', "'%s'", $sql ), $args );
    }

    public function get_var( $sql ) {
        $this->kueri[] = $sql;
        return $this->ada && preg_match( "/LIKE '(.*)'/", $sql, $m ) ? stripslashes( $m[1] ) : null;
    }

    public function get_row( $sql, $format = null ) {
        $this->kueri[] = $sql;
        return array( 'wp_x', $this->buat );
    }

    public function query( $sql ) {
        $this->kueri[] = $sql;
        return true;
    }

    public function get_results( $sql, $format = null ) {
        $this->kueri[] = $sql;
        if ( 0 === strpos( $sql, 'SHOW COLUMNS' ) ) {
            return $this->kolom;
        }
        if ( 0 === strpos( $sql, 'SHOW KEYS' ) ) {
            return $this->pk;
        }
        // SELECT tiruan: cukup untuk PK bulat tunggal dan LIMIT/OFFSET.
        $baris = $this->baris;
        if ( preg_match( '/WHERE `([a-z_]+)` > (-?[0-9]+) /', $sql, $m ) ) {
            $baris = array_values( array_filter( $baris, function ( $b ) use ( $m ) {
                return (int) $b[ $m[1] ] > (int) $m[2];
            } ) );
        }
        preg_match( '/LIMIT ([0-9]+)(?: OFFSET ([0-9]+))?/', $sql, $m );
        return array_slice( $baris, isset( $m[2] ) ? (int) $m[2] : 0, (int) $m[1] );
    }
}

final class TabelTest extends TestCase {

    protected function setUp(): void {
        WPMGR_Staging_Tabel::$baris     = 2000;
        WPMGR_Staging_Tabel::$sub       = 200;
        WPMGR_Staging_Tabel::$maks_byte = 6291456;
    }

    private function wpdb_ber_pk( $n ) {
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'id', 'Type' => 'bigint(20) unsigned' ), array( 'Field' => 'judul', 'Type' => 'text' ) );
        $w->pk    = array( array( 'Column_name' => 'id', 'Seq_in_index' => '1' ) );
        for ( $i = 1; $i <= $n; $i++ ) {
            $w->baris[] = array( 'id' => (string) $i, 'judul' => "judul $i" );
        }
        return $w;
    }

    public function test_esc_meniru_real_escape_string(): void {
        $this->assertSame( "a\\'b\\\"c\\\\d\\0e\\nf\\rg\\Zh", WPMGR_Staging_Tabel::esc( "a'b\"c\\d\0e\nf\rg\x1ah" ) );
        $this->assertSame( 'ü😀', WPMGR_Staging_Tabel::esc( 'ü😀' ) );
    }

    public function test_nilai_per_tipe(): void {
        $this->assertSame( 'NULL', WPMGR_Staging_Tabel::nilai( null, 'text' ) );
        $this->assertSame( "'abc'", WPMGR_Staging_Tabel::nilai( 'abc', 'varchar(10)' ) );
        $this->assertSame( "'12'", WPMGR_Staging_Tabel::nilai( '12', 'bigint(20) unsigned' ) );
        $this->assertSame( '0xff', WPMGR_Staging_Tabel::nilai( "\xff", 'varchar(10)' ) );
        $this->assertSame( '0x61', WPMGR_Staging_Tabel::nilai( 'a', 'longblob' ) );
        $this->assertSame( '0x01', WPMGR_Staging_Tabel::nilai( "\x01", 'bit(1)' ) );
        $this->assertSame( "''", WPMGR_Staging_Tabel::nilai( '', 'varbinary(16)' ) );
    }

    public function test_potongan_pertama_membawa_create_lalu_berlanjut_dengan_kursor(): void {
        WPMGR_Staging_Tabel::$baris = 3;
        WPMGR_Staging_Tabel::$sub   = 2;
        $w = $this->wpdb_ber_pk( 5 );
        $a = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertStringStartsWith( "DROP TABLE IF EXISTS `wp_x`;\nCREATE TABLE `wp_x`", $a['sql'] );
        $this->assertStringContainsString( "INSERT INTO `wp_x` (`id`,`judul`) VALUES\n('1','judul 1'),\n('2','judul 2')", $a['sql'] );
        $this->assertStringContainsString( "('3','judul 3');\n", $a['sql'] );
        $this->assertSame( 3, $a['baris'] );
        $this->assertFalse( $a['selesai'] );
        $this->assertContains( 'SELECT * FROM `wp_x` ORDER BY `id` LIMIT 2', $w->kueri );
        $this->assertContains( 'SELECT * FROM `wp_x` WHERE `id` > 2 ORDER BY `id` LIMIT 1', $w->kueri );
        $this->assertContains( 'START TRANSACTION WITH CONSISTENT SNAPSHOT', $w->kueri );
        $this->assertSame( array( 'pk' => array( array( 's' => '3' ) ) ), WPMGR_Staging_Tabel::urai_kursor( $a['kursor'] ) );

        $b = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $a['kursor'] );
        $this->assertStringNotContainsString( 'DROP TABLE', $b['sql'] );
        $this->assertStringContainsString( "('4','judul 4'),\n('5','judul 5');\n", $b['sql'] );
        $this->assertTrue( $b['selesai'] );
        $this->assertNull( $b['kursor'] );
        $this->assertSame( 2, $b['baris'] );
    }

    public function test_tanpa_pk_dan_biner_memakai_offset_dan_hex(): void {
        WPMGR_Staging_Tabel::$sub = 2;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'data', 'Type' => 'mediumblob' ), array( 'Field' => 'catatan', 'Type' => 'varchar(191)' ) );
        $w->baris = array(
            array( 'data' => "\0\xff\x1a", 'catatan' => "it's" ),
            array( 'data' => null, 'catatan' => "baris\nbaru" ),
            array( 'data' => '', 'catatan' => "\xc3\x28" ),
        );
        $a = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertContains( 'SELECT * FROM `wp_x` LIMIT 2 OFFSET 0', $w->kueri );
        $this->assertContains( 'SELECT * FROM `wp_x` LIMIT 2 OFFSET 2', $w->kueri );
        $this->assertStringContainsString( "(0x00ff1a,'it\\'s')", $a['sql'] );
        $this->assertStringContainsString( "(NULL,'baris\\nbaru')", $a['sql'] );
        $this->assertStringContainsString( "('',0xc328)", $a['sql'] );
        $this->assertTrue( $a['selesai'] );
        $this->assertSame( 3, $a['baris'] );
        // Tidak ada baris baru mentah di dalam literal: setiap pernyataan
        // berakhir tepat di ";\n", syarat pemecah pernyataan di Task 7.
        foreach ( explode( ";\n", rtrim( $a['sql'] ) ) as $pernyataan ) {
            $this->assertStringNotContainsString( "'baris\nbaru'", $pernyataan );
        }
        $this->assertSame( 1, preg_match( '//u', $a['sql'] ) );
    }

    public function test_kursor_offset_melanjutkan(): void {
        WPMGR_Staging_Tabel::$baris = 2;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'a', 'Type' => 'int(11)' ) );
        $w->baris = array( array( 'a' => '7' ), array( 'a' => '8' ), array( 'a' => '9' ) );
        $a = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertSame( array( 'o' => 2 ), WPMGR_Staging_Tabel::urai_kursor( $a['kursor'] ) );
        $b = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $a['kursor'] );
        $this->assertStringContainsString( "('9');\n", $b['sql'] );
        $this->assertTrue( $b['selesai'] );
    }

    public function test_batas_byte_memotong_tanpa_duplikat(): void {
        WPMGR_Staging_Tabel::$maks_byte = 10;
        $w     = $this->wpdb_ber_pk( 4 );
        $semua = '';
        $kursor = '';
        for ( $i = 0; $i < 10; $i++ ) {
            $h      = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $kursor );
            $semua .= $h['sql'];
            if ( $h['selesai'] ) {
                break;
            }
            $this->assertGreaterThanOrEqual( 1, $h['baris'] );
            $kursor = $h['kursor'];
        }
        for ( $n = 1; $n <= 4; $n++ ) {
            $this->assertSame( 1, substr_count( $semua, "'judul $n'" ) );
        }
    }

    public function test_kunci_bukan_utf8_menjadi_hex_di_kursor_dan_where(): void {
        WPMGR_Staging_Tabel::$baris = 1;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'kunci', 'Type' => 'binary(2)' ) );
        $w->pk    = array( array( 'Column_name' => 'kunci', 'Seq_in_index' => '1' ) );
        $w->baris = array( array( 'kunci' => "\xff\x01" ), array( 'kunci' => "\xff\x02" ) );
        $a = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertSame( array( 'pk' => array( array( 'x' => 'ff01' ) ) ), WPMGR_Staging_Tabel::urai_kursor( $a['kursor'] ) );
        WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $a['kursor'] );
        $this->assertContains( 'SELECT * FROM `wp_x` WHERE `kunci` > 0xff01 ORDER BY `kunci` LIMIT 1', $w->kueri );
    }

    public function test_kursor_rusak_ditolak(): void {
        $w = $this->wpdb_ber_pk( 2 );
        foreach ( array( 'bukan base64!!', base64_encode( '{"pk":[{"s":"1 OR 1=1"}]}' ),
                         base64_encode( '{"pk":[{"s":"1"},{"s":"2"}]}' ), base64_encode( '{"o":-1}' ),
                         base64_encode( '{"pk":[{"x":"zz"}]}' ), base64_encode( '"teks"' ) ) as $k ) {
            $hasil = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', $k );
            $this->assertInstanceOf( WP_Error::class, $hasil, $k );
            $this->assertSame( 'wpmgr_staging_kursor', $hasil->get_error_code() );
        }
    }

    public function test_tabel_tidak_sah_atau_tidak_ada(): void {
        $w = $this->wpdb_ber_pk( 1 );
        foreach ( array( 'wp_x; DROP TABLE wp_users', 'lain_x', '', null, 'wp_`x' ) as $t ) {
            $this->assertSame( 'wpmgr_staging_tabel', WPMGR_Staging_Tabel::ekspor( $w, $t, '' )->get_error_code() );
        }
        $w->ada = false;
        $galat  = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' );
        $this->assertSame( array( 'status' => 404 ), $galat->get_error_data() );
    }
}
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: fatal `Failed opening required '.../class-wpmgr-staging-tabel.php'`.

- [ ] **Step 3: Implementasikan.**

File: `connector/wp-manager-connector/includes/class-wpmgr-staging-tabel.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Ekspor satu potongan tabel sebagai SQL yang bisa diimpor MariaDB (spec
 * §6.2 langkah 3). Escaping dikerjakan sendiri, bukan lewat $wpdb->prepare,
 * supaya hasilnya deterministik (bisa diuji tanpa koneksi MySQL) dan tahan
 * data biner: nilai biner dan byte bukan UTF-8 ditulis sebagai literal hex,
 * sehingga tidak ada byte mentah yang bergantung pada charset koneksi impor.
 */
class WPMGR_Staging_Tabel {

    const MAKS_PERNYATAAN = 1048576;
    const MAKS_KURSOR     = 8192;

    public static $baris     = 2000;
    public static $sub       = 200;
    public static $maks_byte = 6291456;

    private static function galat_tabel( $pesan, $status ) {
        return WPMGR_Staging::galat( 'wpmgr_staging_tabel', $pesan, $status );
    }

    private static function galat_kursor() {
        return WPMGR_Staging::galat( 'wpmgr_staging_kursor', 'Kursor tabel tidak sah.', 400 );
    }

    /** Sama dengan mysqli_real_escape_string(): tidak ada baris baru mentah di literal. */
    public static function esc( $s ) {
        return strtr( (string) $s, array(
            '\\'   => '\\\\',
            "\0"   => '\\0',
            "\n"   => '\\n',
            "\r"   => '\\r',
            "'"    => "\\'",
            '"'    => '\\"',
            "\x1a" => '\\Z',
        ) );
    }

    public static function biner( $tipe ) {
        return 1 === preg_match( '/^(tiny|medium|long)?blob|^(var)?binary|^bit/i', (string) $tipe );
    }

    public static function bulat( $tipe ) {
        return 1 === preg_match( '/^(tiny|small|medium|big)?int|^integer/i', (string) $tipe );
    }

    private static function utf8( $v ) {
        return 1 === preg_match( '//u', $v );
    }

    public static function nilai( $v, $tipe ) {
        if ( null === $v ) {
            return 'NULL';
        }
        $v = (string) $v;
        if ( self::biner( $tipe ) || ! self::utf8( $v ) ) {
            return '' === $v ? "''" : '0x' . bin2hex( $v );
        }
        return "'" . self::esc( $v ) . "'";
    }

    public static function kode_kursor( $data ) {
        return base64_encode( json_encode( $data ) );
    }

    /** null = mulai dari awal; false = rusak. */
    public static function urai_kursor( $kursor ) {
        $kursor = (string) $kursor;
        if ( '' === $kursor ) {
            return null;
        }
        if ( strlen( $kursor ) > self::MAKS_KURSOR || 1 !== preg_match( '/^[A-Za-z0-9+\/=]+\z/', $kursor ) ) {
            return false;
        }
        $data = json_decode( (string) base64_decode( $kursor, true ), true );
        if ( ! is_array( $data ) ) {
            return false;
        }
        if ( array_key_exists( 'o', $data ) ) {
            return ( is_int( $data['o'] ) && $data['o'] >= 0 ) ? array( 'o' => $data['o'] ) : false;
        }
        if ( isset( $data['pk'] ) && is_array( $data['pk'] ) && ! empty( $data['pk'] ) ) {
            foreach ( $data['pk'] as $v ) {
                $sah = is_array( $v ) && ( ( isset( $v['s'] ) && is_string( $v['s'] ) ) || ( isset( $v['x'] ) && is_string( $v['x'] ) ) );
                if ( ! $sah ) {
                    return false;
                }
            }
            return array( 'pk' => array_values( $data['pk'] ) );
        }
        return false;
    }

    public static function literal_kunci( $entri, $tipe ) {
        if ( isset( $entri['x'] ) ) {
            if ( 1 !== preg_match( '/^(?:[0-9a-f]{2})*\z/', $entri['x'] ) ) {
                return false;
            }
            return '' === $entri['x'] ? "''" : '0x' . $entri['x'];
        }
        $s = (string) $entri['s'];
        if ( self::bulat( $tipe ) ) {
            // Angka ditulis tanpa kutip: membandingkan kolom BIGINT dengan
            // string memaksa perbandingan floating point dan meleset di atas 2^53.
            return 1 === preg_match( '/^-?[0-9]{1,20}\z/', $s ) ? $s : false;
        }
        return "'" . self::esc( $s ) . "'";
    }

    public static function kolom( $wpdb, $tabel ) {
        $baris = $wpdb->get_results( "SHOW COLUMNS FROM `{$tabel}`", ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL -- nama tabel sudah divalidasi
        $hasil = array();
        foreach ( (array) $baris as $b ) {
            $nama = isset( $b['Field'] ) ? (string) $b['Field'] : '';
            if ( 1 !== preg_match( '/^[^\x00-\x1f`]{1,64}\z/u', $nama ) ) {
                return array();
            }
            $hasil[ $nama ] = isset( $b['Type'] ) ? (string) $b['Type'] : '';
        }
        return $hasil;
    }

    private static function posisi_berikut( array $pk, array $row, $lama ) {
        if ( empty( $pk ) ) {
            return array( 'o' => ( is_array( $lama ) && isset( $lama['o'] ) ? $lama['o'] : 0 ) + 1 );
        }
        $nilai = array();
        foreach ( $pk as $k ) {
            $v       = (string) $row[ $k ];
            $nilai[] = self::utf8( $v ) ? array( 's' => $v ) : array( 'x' => bin2hex( $v ) );
        }
        return array( 'pk' => $nilai );
    }

    private static function kueri( $tabel, array $pk, array $kolom, $posisi, $batas ) {
        if ( empty( $pk ) ) {
            if ( is_array( $posisi ) && isset( $posisi['pk'] ) ) {
                return false;
            }
            $offset = ( is_array( $posisi ) && isset( $posisi['o'] ) ) ? (int) $posisi['o'] : 0;
            return "SELECT * FROM `{$tabel}` LIMIT " . (int) $batas . ' OFFSET ' . $offset;
        }
        $urut  = '`' . implode( '`,`', $pk ) . '`';
        $where = '';
        if ( is_array( $posisi ) ) {
            if ( ! isset( $posisi['pk'] ) || count( $posisi['pk'] ) !== count( $pk ) ) {
                return false;
            }
            $lit = array();
            foreach ( $pk as $i => $k ) {
                $l = self::literal_kunci( $posisi['pk'][ $i ], isset( $kolom[ $k ] ) ? $kolom[ $k ] : '' );
                if ( false === $l ) {
                    return false;
                }
                $lit[] = $l;
            }
            $where = 1 === count( $pk )
                ? " WHERE `{$pk[0]}` > {$lit[0]}"
                : " WHERE ({$urut}) > (" . implode( ',', $lit ) . ')';
        }
        return "SELECT * FROM `{$tabel}`{$where} ORDER BY {$urut} LIMIT " . (int) $batas;
    }

    public static function ekspor( $wpdb, $tabel, $kursor ) {
        if ( ! is_string( $tabel ) || ! WPMGR_Staging_Manifest::nama_tabel_sah( $tabel, $wpdb->prefix ) ) {
            return self::galat_tabel( 'Nama tabel tidak sah.', 400 );
        }
        if ( $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $wpdb->esc_like( $tabel ) ) ) !== $tabel ) {
            return self::galat_tabel( 'Tabel tidak ditemukan.', 404 );
        }
        $posisi = self::urai_kursor( $kursor );
        if ( false === $posisi ) {
            return self::galat_kursor();
        }
        $kolom = self::kolom( $wpdb, $tabel );
        if ( empty( $kolom ) ) {
            return self::galat_tabel( 'Kolom tabel tidak dapat dibaca.', 500 );
        }
        $pk = WPMGR_Staging_Manifest::pk( $wpdb, $tabel );
        foreach ( $pk as $k ) {
            if ( ! isset( $kolom[ $k ] ) ) {
                $pk = array();
                break;
            }
        }
        if ( false === self::kueri( $tabel, $pk, $kolom, $posisi, 1 ) ) {
            return self::galat_kursor();
        }

        $sql = '';
        if ( null === $posisi ) {
            $buat = $wpdb->get_row( "SHOW CREATE TABLE `{$tabel}`", ARRAY_N ); // phpcs:ignore WordPress.DB.PreparedSQL
            if ( ! is_array( $buat ) || empty( $buat[1] ) ) {
                return self::galat_tabel( 'Struktur tabel tidak dapat dibaca.', 500 );
            }
            $sql .= "DROP TABLE IF EXISTS `{$tabel}`;\n" . $buat[1] . ";\n";
        }

        // Konsisten di dalam satu potongan (spec §6.2); lihat "Yang tidak
        // dapat dipenuhi" di rencana untuk konsistensi lintas potongan.
        $wpdb->query( 'SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ' );
        $wpdb->query( 'START TRANSACTION WITH CONSISTENT SNAPSHOT' );

        $daftar     = '`' . implode( '`,`', array_keys( $kolom ) ) . '`';
        $akhir      = $posisi;
        $jumlah     = 0;
        $selesai    = false;
        $penuh      = false;
        $pernyataan = '';
        while ( ! $penuh && $jumlah < self::$baris ) {
            $batas = min( self::$sub, self::$baris - $jumlah );
            $rows  = $wpdb->get_results( self::kueri( $tabel, $pk, $kolom, $akhir, $batas ), ARRAY_A );
            if ( ! is_array( $rows ) ) {
                $wpdb->query( 'ROLLBACK' );
                return self::galat_tabel( 'Isi tabel tidak dapat dibaca.', 500 );
            }
            foreach ( $rows as $row ) {
                $nilai = array();
                foreach ( $kolom as $k => $tipe ) {
                    $nilai[] = self::nilai( array_key_exists( $k, $row ) ? $row[ $k ] : null, $tipe );
                }
                $tuple       = '(' . implode( ',', $nilai ) . ')';
                $pernyataan  = ( '' === $pernyataan )
                    ? "INSERT INTO `{$tabel}` ({$daftar}) VALUES\n" . $tuple
                    : $pernyataan . ",\n" . $tuple;
                if ( strlen( $pernyataan ) >= self::MAKS_PERNYATAAN ) {
                    $sql       .= $pernyataan . ";\n";
                    $pernyataan = '';
                }
                $jumlah++;
                $akhir = self::posisi_berikut( $pk, $row, $akhir );
                if ( strlen( $sql ) + strlen( $pernyataan ) >= self::$maks_byte ) {
                    $penuh = true;
                    break;
                }
            }
            if ( ! $penuh && count( $rows ) < $batas ) {
                $selesai = true;
                break;
            }
        }
        if ( '' !== $pernyataan ) {
            $sql .= $pernyataan . ";\n";
        }
        $wpdb->query( 'COMMIT' );
        return array(
            'sql'     => $sql,
            'kursor'  => $selesai ? null : self::kode_kursor( $akhir ),
            'selesai' => $selesai,
            'baris'   => $jumlah,
        );
    }
}
```

Di `class-wpmgr-staging.php`, tambahkan `'/staging/tabel' => array( 'POST', 'tabel' ),` ke `rute()` dan callback:

```php
    public static function tabel( $request ) {
        global $wpdb;
        $p = json_decode( $request->get_body(), true );
        if ( ! is_array( $p ) ) {
            return self::galat( 'wpmgr_staging_permintaan', 'Body permintaan bukan objek.', 400 );
        }
        $nama   = isset( $p['tabel'] ) ? $p['tabel'] : null;
        $kursor = ( isset( $p['kursor'] ) && is_string( $p['kursor'] ) ) ? $p['kursor'] : '';
        $hasil  = WPMGR_Staging_Tabel::ekspor( $wpdb, $nama, $kursor );
        if ( is_wp_error( $hasil ) ) {
            return $hasil;
        }
        return self::respons_biner( WPMGR_Staging_Paket::susun(
            array(
                'tabel'   => $nama,
                'kursor'  => $hasil['kursor'],
                'selesai' => $hasil['selesai'],
                'baris'   => $hasil['baris'],
                'berkas'  => array( array( 'path' => 'sql' ) ),
            ),
            array( $hasil['sql'] )
        ) );
    }
```

Tambahkan `require_once WPMGR_DIR . 'includes/class-wpmgr-staging-tabel.php';` ke berkas utama setelah `class-wpmgr-staging-file.php`.

- [ ] **Step 4: PHPUnit 8.3 dan 7.4.** Expected: `OK`. Keterimpanan (importability) oleh MariaDB sungguhan dibuktikan e2e Task 22, yang mengimpor hasil ekspor ke MariaDB staging.

- [ ] **Step 5: Commit.**

```bash
git add connector
git commit -m "feat(connector): ekspor tabel staging per potongan PK/offset, aman biner"
```

---

### Task 6: Endpoint `/staging/tanda-air`

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-staging-tanda-air.php`, `connector/tests/TandaAirTest.php`
- Modify: `includes/class-wpmgr-staging.php`, `wp-manager-connector.php`, `connector/tests/bootstrap.php`

**Interfaces:**
- Produces:
  - `GET /wp-json/wpmgr/v1/staging/tanda-air?posts_sejak=YYYY-MM-DD HH:MM:SS&posts_maks=<id>` (HMAC) → `{sumber: {...}, diambil: <detik unix>}`.
  - Isi `sumber`, masing-masing ada hanya bila sumbernya ada di site:
    - `posts`: `{maks_id, jumlah, diubah, diubah_sejak|null}`, tanpa revisi, auto-draft, pesanan, dan Flamingo;
    - `comments`, `users`: `{maks_id, jumlah}`;
    - `pesanan`: `{maks_id, jumlah, sumber: "hpos"|"posts"}`, dari `wc_orders` bila ada, selain itu `posts` bertipe `shop_order` bila tabel WooCommerce ada;
    - `gravity_forms` (`gf_entry.id`), `wpforms` (`wpforms_entries.entry_id`), `fluent_forms` (`fluentform_submissions.id`): `{maks_id, jumlah}`;
    - `flamingo`: `{maks_id, jumlah}`, posts bertipe `flamingo_inbound`, hanya bila ada isinya.
  - `diubah_sejak` adalah jumlah post dengan `ID <= posts_maks` dan `post_modified_gmt > posts_sejak`, yaitu post lama yang diubah (Koreksi #20). Nilainya `null` bila parameter tidak sah.
  - `WPMGR_Staging_TandaAir::kumpulkan( $wpdb, $posts_sejak, $posts_maks ): array`, `ada_tabel( $wpdb, $nama ): bool`.

- [ ] **Step 1: Tulis test yang gagal.** Tambahkan `require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-tanda-air.php';` ke akhir `bootstrap.php`.

File: `connector/tests/TandaAirTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class WPMGR_FakeWpdbTandaAir {
    public $prefix = 'wp_';
    public $tabel  = array( 'wp_posts', 'wp_comments', 'wp_users' );
    public $kueri  = array();
    public $baris  = array();
    public $nilai  = array();

    public function esc_like( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function prepare( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        $sql  = str_replace( array( '%s', '%d' ), array( "'%s'", '%d' ), $sql );
        return vsprintf( $sql, $args );
    }

    public function get_var( $sql ) {
        $this->kueri[] = $sql;
        if ( preg_match( "/^SHOW TABLES LIKE '(.*)'\z/", $sql, $m ) ) {
            $nama = stripslashes( $m[1] );
            return in_array( $nama, $this->tabel, true ) ? $nama : null;
        }
        foreach ( $this->nilai as $pola => $v ) {
            if ( false !== strpos( $sql, $pola ) ) {
                return $v;
            }
        }
        return '0';
    }

    public function get_row( $sql, $format = null ) {
        $this->kueri[] = $sql;
        foreach ( $this->baris as $pola => $v ) {
            if ( false !== strpos( $sql, $pola ) ) {
                return $v;
            }
        }
        return array( 'maks' => null, 'jumlah' => '0', 'diubah' => null );
    }
}

final class TandaAirTest extends TestCase {

    private function wpdb() {
        $w        = new WPMGR_FakeWpdbTandaAir();
        $w->baris = array(
            'FROM wp_posts WHERE post_type NOT IN' => array( 'maks' => '120', 'jumlah' => '80', 'diubah' => '2026-09-26 01:02:03' ),
            'FROM wp_comments'                     => array( 'maks' => '55', 'jumlah' => '40' ),
            'FROM wp_users'                        => array( 'maks' => '3', 'jumlah' => '3' ),
        );
        return $w;
    }

    public function test_sumber_inti_dan_nilai_bulat(): void {
        $h = WPMGR_Staging_TandaAir::kumpulkan( $this->wpdb(), '', 0 );
        $this->assertSame( array( 'maks_id' => 120, 'jumlah' => 80, 'diubah' => '2026-09-26 01:02:03', 'diubah_sejak' => null ),
            $h['sumber']['posts'] );
        $this->assertSame( array( 'maks_id' => 55, 'jumlah' => 40 ), $h['sumber']['comments'] );
        $this->assertSame( array( 'maks_id' => 3, 'jumlah' => 3 ), $h['sumber']['users'] );
        foreach ( array( 'pesanan', 'gravity_forms', 'wpforms', 'fluent_forms', 'flamingo' ) as $tidak ) {
            $this->assertArrayNotHasKey( $tidak, $h['sumber'] );
        }
        $this->assertIsInt( $h['diambil'] );
    }

    public function test_pesanan_hpos_didahulukan(): void {
        $w          = $this->wpdb();
        $w->tabel[] = 'wp_wc_orders';
        $w->tabel[] = 'wp_woocommerce_order_items';
        $w->baris['FROM wp_wc_orders'] = array( 'maks' => '900', 'jumlah' => '850' );
        $h = WPMGR_Staging_TandaAir::kumpulkan( $w, '', 0 );
        $this->assertSame( array( 'maks_id' => 900, 'jumlah' => 850, 'sumber' => 'hpos' ), $h['sumber']['pesanan'] );
    }

    public function test_pesanan_lama_dari_posts(): void {
        $w          = $this->wpdb();
        $w->tabel[] = 'wp_woocommerce_order_items';
        $w->baris["post_type IN ('shop_order')"] = array( 'maks' => '77', 'jumlah' => '12' );
        $h = WPMGR_Staging_TandaAir::kumpulkan( $w, '', 0 );
        $this->assertSame( array( 'maks_id' => 77, 'jumlah' => 12, 'sumber' => 'posts' ), $h['sumber']['pesanan'] );
    }

    public function test_tabel_form_yang_ada(): void {
        $w = $this->wpdb();
        array_push( $w->tabel, 'wp_gf_entry', 'wp_wpforms_entries', 'wp_fluentform_submissions' );
        $w->baris['MAX(`id`) AS maks, COUNT(*) AS jumlah FROM wp_gf_entry']             = array( 'maks' => '5', 'jumlah' => '5' );
        $w->baris['MAX(`entry_id`) AS maks, COUNT(*) AS jumlah FROM wp_wpforms_entries'] = array( 'maks' => '9', 'jumlah' => '8' );
        $w->baris['FROM wp_fluentform_submissions']                                     = array( 'maks' => '2', 'jumlah' => '2' );
        $h = WPMGR_Staging_TandaAir::kumpulkan( $w, '', 0 );
        $this->assertSame( array( 'maks_id' => 5, 'jumlah' => 5 ), $h['sumber']['gravity_forms'] );
        $this->assertSame( array( 'maks_id' => 9, 'jumlah' => 8 ), $h['sumber']['wpforms'] );
        $this->assertSame( array( 'maks_id' => 2, 'jumlah' => 2 ), $h['sumber']['fluent_forms'] );
    }

    public function test_flamingo_hanya_bila_ada_isi(): void {
        $w = $this->wpdb();
        $w->baris["post_type = 'flamingo_inbound'"] = array( 'maks' => '40', 'jumlah' => '6' );
        $this->assertSame( array( 'maks_id' => 40, 'jumlah' => 6 ),
            WPMGR_Staging_TandaAir::kumpulkan( $w, '', 0 )['sumber']['flamingo'] );
        $this->assertArrayNotHasKey( 'flamingo', WPMGR_Staging_TandaAir::kumpulkan( $this->wpdb(), '', 0 )['sumber'] );
    }

    public function test_posts_diubah_sejak(): void {
        $w        = $this->wpdb();
        $w->nilai = array( "post_modified_gmt > '2026-09-20 00:00:00'" => '4' );
        $h = WPMGR_Staging_TandaAir::kumpulkan( $w, '2026-09-20 00:00:00', 100 );
        $this->assertSame( 4, $h['sumber']['posts']['diubah_sejak'] );
        $semua = implode( "\n", $w->kueri );
        $this->assertStringContainsString( 'ID <= 100', $semua );
    }

    public function test_posts_sejak_tidak_sah_diabaikan(): void {
        foreach ( array( "2026-09-20' OR '1'='1", '2026-09-20', '2026-09-20 00:00:00x' ) as $sejak ) {
            $w = $this->wpdb();
            $h = WPMGR_Staging_TandaAir::kumpulkan( $w, $sejak, 100 );
            $this->assertNull( $h['sumber']['posts']['diubah_sejak'] );
            $this->assertStringNotContainsString( 'post_modified_gmt >', implode( "\n", $w->kueri ) );
        }
    }
}
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Expected: fatal `Failed opening required '.../class-wpmgr-staging-tanda-air.php'`.

- [ ] **Step 3: Implementasikan.**

File: `connector/wp-manager-connector/includes/class-wpmgr-staging-tanda-air.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Tanda air data baru produksi (spec §8.2): angka yang cukup untuk
 * mengatakan "ada pesanan/komentar/user/isian form baru sejak staging
 * ditarik" tanpa membandingkan isi tabel. Sumber hanya dilaporkan bila
 * tabelnya ada, supaya plugin yang tidak terpasang tidak tampak sebagai
 * "0 entri" yang kemudian dibandingkan.
 */
class WPMGR_Staging_TandaAir {

    const FORM = array(
        'gravity_forms' => array( 'gf_entry', 'id' ),
        'wpforms'       => array( 'wpforms_entries', 'entry_id' ),
        'fluent_forms'  => array( 'fluentform_submissions', 'id' ),
    );

    public static function ada_tabel( $wpdb, $nama ) {
        return $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $wpdb->esc_like( $nama ) ) ) === $nama;
    }

    private static function maks_jumlah( $baris ) {
        $baris = is_array( $baris ) ? $baris : array();
        return array(
            'maks_id' => isset( $baris['maks'] ) ? (int) $baris['maks'] : 0,
            'jumlah'  => isset( $baris['jumlah'] ) ? (int) $baris['jumlah'] : 0,
        );
    }

    public static function kumpulkan( $wpdb, $posts_sejak, $posts_maks ) {
        $p      = $wpdb->prefix;
        $sumber = array();

        $posts = $wpdb->get_row(
            "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah, MAX(post_modified_gmt) AS diubah FROM {$p}posts"
            . " WHERE post_type NOT IN ('revision','shop_order','shop_order_refund','shop_order_placehold','flamingo_inbound')"
            . " AND post_status <> 'auto-draft'",
            ARRAY_A
        );
        $sumber['posts']                 = self::maks_jumlah( $posts );
        $sumber['posts']['diubah']       = ( is_array( $posts ) && null !== $posts['diubah'] ) ? (string) $posts['diubah'] : '';
        $sumber['posts']['diubah_sejak'] = null;
        $sejak = (string) $posts_sejak;
        if ( 1 === preg_match( '/^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\z/', $sejak ) && (int) $posts_maks > 0 ) {
            $sumber['posts']['diubah_sejak'] = (int) $wpdb->get_var( $wpdb->prepare(
                "SELECT COUNT(*) FROM {$p}posts WHERE ID <= %d AND post_modified_gmt > %s"
                . " AND post_type NOT IN ('revision','shop_order','shop_order_refund','shop_order_placehold','flamingo_inbound')",
                (int) $posts_maks, $sejak
            ) );
        }

        $sumber['comments'] = self::maks_jumlah( $wpdb->get_row( "SELECT MAX(comment_ID) AS maks, COUNT(*) AS jumlah FROM {$p}comments", ARRAY_A ) );
        $sumber['users']    = self::maks_jumlah( $wpdb->get_row( "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah FROM {$p}users", ARRAY_A ) );

        if ( self::ada_tabel( $wpdb, $p . 'wc_orders' ) ) {
            $sumber['pesanan'] = self::maks_jumlah( $wpdb->get_row(
                "SELECT MAX(id) AS maks, COUNT(*) AS jumlah FROM {$p}wc_orders WHERE type = 'shop_order'", ARRAY_A ) );
            $sumber['pesanan']['sumber'] = 'hpos';
        } elseif ( self::ada_tabel( $wpdb, $p . 'woocommerce_order_items' ) ) {
            $sumber['pesanan'] = self::maks_jumlah( $wpdb->get_row(
                "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah FROM {$p}posts WHERE post_type IN ('shop_order')", ARRAY_A ) );
            $sumber['pesanan']['sumber'] = 'posts';
        }

        foreach ( self::FORM as $kunci => $def ) {
            if ( self::ada_tabel( $wpdb, $p . $def[0] ) ) {
                $sumber[ $kunci ] = self::maks_jumlah( $wpdb->get_row(
                    "SELECT MAX(`{$def[1]}`) AS maks, COUNT(*) AS jumlah FROM {$p}{$def[0]}", ARRAY_A ) );
            }
        }

        $flamingo = self::maks_jumlah( $wpdb->get_row(
            "SELECT MAX(ID) AS maks, COUNT(*) AS jumlah FROM {$p}posts WHERE post_type = 'flamingo_inbound'", ARRAY_A ) );
        if ( $flamingo['jumlah'] > 0 ) {
            $sumber['flamingo'] = $flamingo;
        }

        return array( 'sumber' => $sumber, 'diambil' => time() );
    }
}
```

Di `class-wpmgr-staging.php`, tambahkan `'/staging/tanda-air' => array( 'GET', 'tanda_air' ),` ke `rute()` dan callback:

```php
    public static function tanda_air( $request ) {
        global $wpdb;
        return rest_ensure_response( WPMGR_Staging_TandaAir::kumpulkan(
            $wpdb, (string) $request->get_param( 'posts_sejak' ), (int) $request->get_param( 'posts_maks' )
        ) );
    }
```

Tambahkan `require_once WPMGR_DIR . 'includes/class-wpmgr-staging-tanda-air.php';` ke berkas utama setelah `class-wpmgr-staging-tabel.php`.

- [ ] **Step 4: PHPUnit 8.3 dan 7.4.** Expected: `OK`.

- [ ] **Step 5: Commit.**

```bash
git add connector
git commit -m "feat(connector): tanda air data baru untuk staging"
```

---

### Task 7: Sisi dorong connector — pemecah SQL, area sementara, `/staging/unggah`, `/staging/snapshot`, `/staging/bersihkan`

**Files:**
- Create: `connector/wp-manager-connector/includes/class-wpmgr-staging-sql.php`, `includes/class-wpmgr-staging-db.php`, `includes/class-wpmgr-staging-dorong.php`, `connector/tests/SqlTest.php`, `connector/tests/DorongTest.php`
- Modify: `includes/class-wpmgr-staging.php`, `includes/class-wpmgr-skema.php`, `wp-manager-connector.php`, `connector/tests/bootstrap.php`

**Interfaces:**
- Consumes: Task 2 (path, paket), Task 3 (`Manifest::tabel`), Task 6 (`TandaAir::kumpulkan`).
- Produces:
  - `WPMGR_Staging_Sql` (pemecah bertahap, sadar kutip dan komentar):
    - `new WPMGR_Staging_Sql( $offset_awal = 0 )`, `->tambah( $data ): array( array( $pernyataan, $offset_akhir_absolut ), ... ) | WP_Error`, `->sisa(): string`;
    - statis `ubah( $pernyataan, $prefix, $awalan = 'wpmgr_tmp_' ): string|null|WP_Error`. Nilai `null` berarti dilewati (`SET`, `LOCK/UNLOCK TABLES`, komentar, tabel `{prefix}wpmgr_*`). String berarti pernyataan dengan nama tabel diganti. Pernyataan lain menghasilkan `WP_Error` `wpmgr_staging_sql`.
  - `WPMGR_Staging_Db` (pembungkus `$wpdb`; impor memakai `mysqli_query` langsung):
    - `kueri( $sql ): true|string`, `kolom( $sql ): array`, `nilai( $sql )`;
    - `siapkan( $sql, ...$args ): string`, `suka( $t ): string`, `prefix(): string`, `opsi(): string`.
  - `WPMGR_Staging_Dorong` (instans; `$akar` = root WordPress, `$dasar` = `wp-content/wpmgr-dorong/`):
    - `__construct( $akar, $dasar, $db, $detik )`, statis `id_sah( $id )`;
    - unggah dan snapshot: `unggah( $data ): array|WP_Error`, `snapshot_berkas( $paths ): array|WP_Error`;
    - pembersihan dan keadaan: `bersihkan( $id ): array{lagi}|WP_Error`, `cron()`, `keadaan( $id ): array|null`;
    - kunci: `kunci( $id ): true|WP_Error`, `lepas_kunci( $id )`.
    Konstanta `KEPALA = "<?php exit; ?>\n"` dan `MAKS_UNGGAH = 8454144` (8 MB + 64 KB meta).
  - Endpoint (HMAC):
    - `POST /staging/unggah`, dengan body berupa paket biner bermeta `{dorong_id: 32 hex, nomor: 0..99999, jenis: berkas|rentang|sql|rencana}` → `{ok, nomor, sha256}`;
    - `POST /staging/snapshot` `{paths: [...≤5000], awal: bool}` → `{berkas: [{path, ada, ukuran?, mtime?}], tabel?, tanda_air?}`;
    - `POST /staging/bersihkan` `{dorong_id}` → `{lagi: bool}`.
  - Kunci dorong berupa opsi `wpmgr_dorong_kunci` = `"<id>|<detik>"`, diambil lewat `INSERT IGNORE` lalu dibaca ulang. Kunci yang basi (> 24 jam) direbut dengan `UPDATE ... WHERE option_value = <lama>`. Dorongan lain yang masih memegang kunci menghasilkan 409 `wpmgr_staging_sibuk`.
  - Cron `WPMGR_Staging::HOOK_BERSIHKAN` (tiap jam) menghapus area yang tidak disentuh 24 jam.

Setiap berkas di area sementara (potongan, rencana, SQL, keadaan) ditulis dengan kepala `<?php exit; ?>` dan berakhiran `.php`. Direktori itu berada di bawah `wp-content`, yang bisa diakses web. Di nginx, `.htaccess` diabaikan, jadi yang benar-benar menjaga data pribadi di sana adalah PHP yang langsung `exit` bila berkas dibuka lewat web, ditambah id dorongan 128 bit yang tidak bisa ditebak.

- [ ] **Step 1: Tulis test pemecah SQL yang gagal.** Tambahkan ke akhir `bootstrap.php`:

```php
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-sql.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-db.php';
require_once __DIR__ . '/../wp-manager-connector/includes/class-wpmgr-staging-dorong.php';
```

File: `connector/tests/SqlTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class SqlTest extends TestCase {

    const CONTOH = "/*!40101 SET NAMES utf8mb4 */;\n"
        . "-- komentar; dengan titik koma\n"
        . "# komentar lain;\n"
        . "DROP TABLE IF EXISTS `wp_posts`;\n"
        . "CREATE TABLE `wp_posts` (\n  `ID` bigint(20) NOT NULL, `a` text /* komentar; */\n);\n"
        . "INSERT INTO `wp_posts` (`ID`,`a`) VALUES\n(1,'titik;koma'),\n(2,'kutip \\' dan \\\\'),\n(3,'ganda '' kutip'),\n(4,\"ganda \\\" ; \"),\n(5,0x3b27);\n"
        . "INSERT INTO `wp_x`; `y`;";

    private function pecah_sekaligus( $data ) {
        $p     = new WPMGR_Staging_Sql();
        $hasil = $p->tambah( $data );
        return array( $hasil, $p->sisa() );
    }

    public function test_memecah_dengan_sadar_kutip_dan_komentar(): void {
        list( $hasil, $sisa ) = $this->pecah_sekaligus( self::CONTOH );
        $teks = array_map( function ( $h ) {
            return trim( $h[0] );
        }, $hasil );
        $this->assertSame( '/*!40101 SET NAMES utf8mb4 */', $teks[0] );
        $this->assertStringEndsWith( 'DROP TABLE IF EXISTS `wp_posts`', $teks[1] );
        $this->assertStringStartsWith( 'CREATE TABLE `wp_posts`', $teks[2] );
        $this->assertStringContainsString( "(5,0x3b27)", $teks[3] );
        $this->assertStringContainsString( "'titik;koma'", $teks[3] );
        $this->assertSame( 'INSERT INTO `wp_x`', $teks[4] );
        $this->assertSame( '`y`', $teks[5] );
        $this->assertCount( 6, $hasil );
        $this->assertSame( '', $sisa );
        $akhir = end( $hasil );
        $this->assertSame( strlen( self::CONTOH ), $akhir[1] );
    }

    public function test_hasil_sama_walau_diumpankan_per_byte(): void {
        list( $sekaligus ) = $this->pecah_sekaligus( self::CONTOH );
        $p      = new WPMGR_Staging_Sql();
        $bertahap = array();
        for ( $i = 0; $i < strlen( self::CONTOH ); $i++ ) {
            foreach ( $p->tambah( self::CONTOH[ $i ] ) as $h ) {
                $bertahap[] = $h;
            }
        }
        $this->assertSame( $sekaligus, $bertahap );
    }

    public function test_offset_awal_dan_sisa(): void {
        $p     = new WPMGR_Staging_Sql( 1000 );
        $hasil = $p->tambah( "SET a=1;\nINSERT INTO `wp_x` VALUES ('belum" );
        $this->assertSame( array( array( 'SET a=1', 1008 ) ), $hasil );
        $this->assertSame( "\nINSERT INTO `wp_x` VALUES ('belum", $p->sisa() );
    }

    public function test_ubah_mengganti_nama_tabel(): void {
        $this->assertSame( 'DROP TABLE IF EXISTS `wpmgr_tmp_wp_posts`',
            WPMGR_Staging_Sql::ubah( "\n-- x\nDROP TABLE IF EXISTS `wp_posts`", 'wp_' ) );
        $this->assertSame( "CREATE TABLE `wpmgr_tmp_wp_posts` (\n `ID` int)",
            WPMGR_Staging_Sql::ubah( "CREATE TABLE `wp_posts` (\n `ID` int)", 'wp_' ) );
        $this->assertSame( "INSERT INTO `wpmgr_tmp_wp_posts` (`ID`) VALUES (1)",
            WPMGR_Staging_Sql::ubah( "INSERT INTO `wp_posts` (`ID`) VALUES (1)", 'wp_' ) );
        // /*!40000 ... */ adalah komentar berversi; pemecah memperlakukannya
        // sebagai komentar, jadi ALTER di dalamnya dilewati (hanya optimasi).
        $this->assertNull( WPMGR_Staging_Sql::ubah( '/*!40000 ALTER TABLE `wp_posts` DISABLE KEYS */', 'wp_' ) );
        $this->assertSame( 'ALTER TABLE `wpmgr_tmp_wp_posts` ENABLE KEYS',
            WPMGR_Staging_Sql::ubah( 'ALTER TABLE `wp_posts` ENABLE KEYS', 'wp_' ) );
    }

    public function test_ubah_melewati_yang_tidak_perlu(): void {
        foreach ( array( 'SET NAMES utf8mb4', 'set foreign_key_checks=0', '/*!40101 SET NAMES utf8 */',
                         'LOCK TABLES `wp_posts` WRITE', 'UNLOCK TABLES', "  \n-- hanya komentar\n",
                         'INSERT INTO `wp_wpmgr_errors` VALUES (1)', 'DROP TABLE IF EXISTS `wp_wpmgr_traffic`' ) as $s ) {
            $this->assertNull( WPMGR_Staging_Sql::ubah( $s, 'wp_' ), $s );
        }
    }

    public function test_ubah_menolak_pernyataan_lain(): void {
        foreach ( array( 'DELETE FROM `wp_posts`', 'UPDATE `wp_options` SET a=1', "GRANT ALL ON *.* TO 'x'",
                         'CREATE TRIGGER t BEFORE INSERT ON `wp_posts` FOR EACH ROW SET @a=1',
                         'INSERT INTO `lain_posts` VALUES (1)', 'INSERT INTO wp_posts VALUES (1)',
                         'DROP DATABASE wp', 'ALTER TABLE `wp_posts` ADD COLUMN x int',
                         'CREATE TABLE `wp_' . str_repeat( 'a', 52 ) . '` (a int)' ) as $s ) {
            $hasil = WPMGR_Staging_Sql::ubah( $s, 'wp_' );
            $this->assertInstanceOf( WP_Error::class, $hasil, $s );
            $this->assertSame( 'wpmgr_staging_sql', $hasil->get_error_code() );
        }
    }

    public function test_keluaran_ekspor_tabel_terpecah_benar(): void {
        WPMGR_Staging_Tabel::$baris     = 2000;
        WPMGR_Staging_Tabel::$sub       = 200;
        WPMGR_Staging_Tabel::$maks_byte = 6291456;
        $w        = new WPMGR_FakeWpdbTabel();
        $w->kolom = array( array( 'Field' => 'a', 'Type' => 'longtext' ) );
        $w->baris = array( array( 'a' => "x;\n'y'\\" ), array( 'a' => "\0;\x1a" ) );
        $sql      = WPMGR_Staging_Tabel::ekspor( $w, 'wp_x', '' )['sql'];
        list( $hasil, $sisa ) = $this->pecah_sekaligus( $sql );
        $this->assertCount( 3, $hasil );
        $this->assertSame( '', trim( $sisa ) );
        $this->assertStringStartsWith( 'INSERT INTO `wpmgr_tmp_wp_x`', WPMGR_Staging_Sql::ubah( $hasil[2][0], 'wp_' ) );
    }
}
```

- [ ] **Step 2: Tulis test area dorong yang gagal.**

File: `connector/tests/DorongTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

/** DB tiruan: cukup untuk kunci opsi, SHOW TABLES, DROP/CREATE/RENAME. */
final class WPMGR_FakeDbDorong {
    public $kueri      = array();
    public $tabel      = array();
    public $opsi       = array();
    public $gagal_pada = null;

    public function prefix() {
        return 'wp_';
    }

    public function opsi() {
        return 'wp_options';
    }

    public function suka( $t ) {
        return addcslashes( $t, '_%\\' );
    }

    public function siapkan( $sql ) {
        $args = array_slice( func_get_args(), 1 );
        $args = array_map( function ( $a ) {
            return is_int( $a ) ? $a : addslashes( (string) $a );
        }, $args );
        return vsprintf( str_replace( '%s', "'%s'", $sql ), $args );
    }

    private static function pola_like( $like ) {
        $r = preg_quote( stripslashes( str_replace( array( '\\_', '\\%' ), array( "\x01", "\x02" ), $like ) ), '/' );
        $r = str_replace( array( '%', '_', "\x01", "\x02" ), array( '.*', '.', '_', '%' ), $r );
        return '/^' . $r . '\z/';
    }

    public function kolom( $sql ) {
        $this->kueri[] = $sql;
        if ( preg_match( "/^SHOW TABLES LIKE '(.*)'\z/", $sql, $m ) ) {
            $pola = self::pola_like( $m[1] );
            return array_values( array_filter( $this->tabel, function ( $t ) use ( $pola ) {
                return 1 === preg_match( $pola, $t );
            } ) );
        }
        return array();
    }

    public function nilai( $sql ) {
        $this->kueri[] = $sql;
        if ( false !== strpos( $sql, "option_name = 'wpmgr_dorong_kunci'" ) ) {
            return isset( $this->opsi['wpmgr_dorong_kunci'] ) ? $this->opsi['wpmgr_dorong_kunci'] : null;
        }
        return null;
    }

    public function kueri( $sql ) {
        $this->kueri[] = $sql;
        if ( null !== $this->gagal_pada && false !== strpos( $sql, $this->gagal_pada ) ) {
            return 'galat tiruan';
        }
        if ( preg_match( "/^INSERT IGNORE INTO wp_options .*VALUES \('wpmgr_dorong_kunci', '([^']*)'/", $sql, $m ) ) {
            if ( ! isset( $this->opsi['wpmgr_dorong_kunci'] ) ) {
                $this->opsi['wpmgr_dorong_kunci'] = $m[1];
            }
        } elseif ( preg_match( "/^UPDATE wp_options SET option_value = '([^']*)' WHERE option_name = 'wpmgr_dorong_kunci' AND option_value = '([^']*)'/", $sql, $m ) ) {
            if ( isset( $this->opsi['wpmgr_dorong_kunci'] ) && $this->opsi['wpmgr_dorong_kunci'] === $m[2] ) {
                $this->opsi['wpmgr_dorong_kunci'] = $m[1];
            }
        } elseif ( preg_match( "/^DELETE FROM wp_options WHERE option_name = 'wpmgr_dorong_kunci' AND option_value LIKE '([0-9a-f]+)\|%'/", $sql, $m ) ) {
            if ( isset( $this->opsi['wpmgr_dorong_kunci'] ) && 0 === strpos( $this->opsi['wpmgr_dorong_kunci'], $m[1] . '|' ) ) {
                unset( $this->opsi['wpmgr_dorong_kunci'] );
            }
        } elseif ( preg_match( '/^DROP TABLE IF EXISTS `([^`]+)`/', $sql, $m ) ) {
            $this->tabel = array_values( array_diff( $this->tabel, array( $m[1] ) ) );
        } elseif ( preg_match( '/^CREATE TABLE `([^`]+)`/', $sql, $m ) ) {
            $this->tabel[] = $m[1];
        } elseif ( preg_match( '/^RENAME TABLE (.*)\z/s', $sql, $m ) ) {
            $tabel = $this->tabel;
            foreach ( explode( ', ', $m[1] ) as $pasang ) {
                if ( ! preg_match( '/^`([^`]+)` TO `([^`]+)`\z/', $pasang, $p ) || ! in_array( $p[1], $tabel, true )
                    || in_array( $p[2], $tabel, true ) ) {
                    return 'RENAME gagal: ' . $pasang;
                }
                $tabel = array_values( array_diff( $tabel, array( $p[1] ) ) );
                $tabel[] = $p[2];
            }
            $this->tabel = $tabel;
        }
        return true;
    }
}

final class DorongTest extends TestCase {

    const ID  = '0123456789abcdef0123456789abcdef';
    const ID2 = 'fedcba9876543210fedcba9876543210';

    private $akar;
    private $db;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-drg-' . bin2hex( random_bytes( 6 ) ) . '/';
        mkdir( $this->akar . 'wp-content/themes/t', 0777, true );
        file_put_contents( $this->akar . 'wp-content/themes/t/style.css', 'lama' );
        $this->db = new WPMGR_FakeDbDorong();
    }

    protected function tearDown(): void {
        StagingDasarTest::hapus( rtrim( $this->akar, '/' ) );
    }

    private function dorong() {
        return new WPMGR_Staging_Dorong( $this->akar, $this->akar . 'wp-content/wpmgr-dorong/', $this->db, 20 );
    }

    private function paket( $id, $nomor, $jenis, array $berkas, array $isi ) {
        return WPMGR_Staging_Paket::susun(
            array( 'dorong_id' => $id, 'nomor' => $nomor, 'jenis' => $jenis, 'berkas' => $berkas ), $isi );
    }

    public function test_unggah_menyimpan_potongan_terlindung_dan_idempoten(): void {
        $data  = $this->paket( self::ID, 0, 'berkas', array( array( 'path' => 'wp-content/themes/t/style.css', 'mtime' => 5 ) ), array( 'baru' ) );
        $hasil = $this->dorong()->unggah( $data );
        $this->assertSame( array( 'ok' => true, 'nomor' => 0, 'sha256' => hash( 'sha256', $data ) ), $hasil );
        $berkas = $this->akar . 'wp-content/wpmgr-dorong/' . self::ID . '/potongan/000000.php';
        $this->assertSame( WPMGR_Staging_Dorong::KEPALA . $data, file_get_contents( $berkas ) );
        $this->assertFileExists( $this->akar . 'wp-content/wpmgr-dorong/.htaccess' );
        $this->assertFileExists( $this->akar . 'wp-content/wpmgr-dorong/index.php' );
        $this->assertSame( 'mengunggah', $this->dorong()->keadaan( self::ID )['status'] );
        $this->assertSame( $hasil, $this->dorong()->unggah( $data ) );
        $this->assertStringStartsWith( self::ID . '|', $this->db->opsi['wpmgr_dorong_kunci'] );
    }

    public function test_unggah_menolak_meta_tidak_sah(): void {
        $d = $this->dorong();
        $this->assertSame( 400, $d->unggah( $this->paket( 'bukan-id', 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) )->get_error_data()['status'] );
        $this->assertSame( 400, $d->unggah( $this->paket( self::ID, -1, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) )->get_error_data()['status'] );
        $this->assertSame( 400, $d->unggah( $this->paket( self::ID, 1, 'eval', array( array( 'path' => 'sql' ) ), array( 'x' ) ) )->get_error_data()['status'] );
        $this->assertSame( 400, $d->unggah( $this->paket( self::ID, 1, 'berkas', array( array( 'path' => 'wp-config.php' ) ), array( 'x' ) ) )->get_error_data()['status'] );
        $this->assertSame( 400, $d->unggah( $this->paket( self::ID, 1, 'berkas', array( array( 'path' => '../x.php' ) ), array( 'x' ) ) )->get_error_data()['status'] );
        $this->assertSame( 'wpmgr_staging_hash', $d->unggah( substr( $this->paket( self::ID, 1, 'sql', array( array( 'path' => 'sql' ) ), array( 'xy' ) ), 0, -1 ) . 'z' )->get_error_code() );
        $this->assertSame( 413, $d->unggah( str_repeat( 'a', WPMGR_Staging_Dorong::MAKS_UNGGAH + 1 ) )->get_error_data()['status'] );
        $this->assertDirectoryDoesNotExist( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
    }

    public function test_kunci_menahan_dorongan_lain_dan_basi_direbut(): void {
        $d = $this->dorong();
        $this->assertTrue( $d->kunci( self::ID ) );
        $galat = $d->kunci( self::ID2 );
        $this->assertSame( 'wpmgr_staging_sibuk', $galat->get_error_code() );
        $this->assertSame( 409, $galat->get_error_data()['status'] );
        $this->db->opsi['wpmgr_dorong_kunci'] = self::ID . '|' . ( time() - 90000 );
        $this->assertTrue( $d->kunci( self::ID2 ) );
        $this->assertStringStartsWith( self::ID2 . '|', $this->db->opsi['wpmgr_dorong_kunci'] );
        $d->lepas_kunci( self::ID2 );
        $this->assertArrayNotHasKey( 'wpmgr_dorong_kunci', $this->db->opsi );
    }

    public function test_snapshot_berkas(): void {
        $hasil = $this->dorong()->snapshot_berkas( array( 'wp-content/themes/t/style.css', 'wp-content/themes/t/baru.php' ) );
        $this->assertSame( 'wp-content/themes/t/style.css', $hasil[0]['path'] );
        $this->assertTrue( $hasil[0]['ada'] );
        $this->assertSame( 4, $hasil[0]['ukuran'] );
        $this->assertSame( array( 'path' => 'wp-content/themes/t/baru.php', 'ada' => false ), $hasil[1] );
        foreach ( array( array( 'wp-config.php' ), array( '../x' ), 'bukan-daftar', array_fill( 0, 5001, 'index.php' ) ) as $p ) {
            $this->assertInstanceOf( WP_Error::class, $this->dorong()->snapshot_berkas( $p ) );
        }
    }

    public function test_bersihkan_menghapus_area_tabel_sementara_dan_kunci(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'SELECT 1;' ) ) );
        $this->db->tabel = array( 'wp_posts', 'wpmgr_tmp_wp_posts', 'wpmgr_old_wp_posts' );
        $this->assertSame( array( 'lagi' => false ), $d->bersihkan( self::ID ) );
        $this->assertDirectoryDoesNotExist( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
        $this->assertSame( array( 'wp_posts', 'wpmgr_old_wp_posts' ), $this->db->tabel );
        $this->assertArrayNotHasKey( 'wpmgr_dorong_kunci', $this->db->opsi );
    }

    public function test_bersihkan_menolak_saat_menukar(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $k           = $d->keadaan( self::ID );
        $k['status'] = 'menukar';
        $d->simpan_keadaan( self::ID, $k );
        $this->assertSame( 'wpmgr_staging_sibuk', $d->bersihkan( self::ID )->get_error_code() );
        $this->assertSame( 400, $d->bersihkan( 'x' )->get_error_data()['status'] );
    }

    public function test_cron_menghapus_area_berumur_24_jam(): void {
        $d = $this->dorong();
        $d->unggah( $this->paket( self::ID, 0, 'sql', array( array( 'path' => 'sql' ) ), array( 'x' ) ) );
        $d->cron();
        $this->assertDirectoryExists( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
        $k           = $d->keadaan( self::ID );
        $k['diubah'] = time() - 90000;
        $d->simpan_keadaan( self::ID, $k );
        $d->cron();
        $this->assertDirectoryDoesNotExist( $this->akar . 'wp-content/wpmgr-dorong/' . self::ID );
    }
}
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `cd connector && vendor/bin/phpunit --filter 'SqlTest|DorongTest'`. Expected: fatal `Failed opening required '.../class-wpmgr-staging-sql.php'`.

- [ ] **Step 4: Pemecah SQL.**

File: `connector/wp-manager-connector/includes/class-wpmgr-staging-sql.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Pemecah SQL bertahap untuk impor dorongan (spec §6.3 langkah 6). Sadar
 * kutip tunggal/ganda (dengan escape backslash dan kutip ganda-dobel),
 * backtick, serta komentar `-- `, `#`, dan blok. Offset akhir setiap
 * pernyataan dilaporkan supaya impor bisa dilanjutkan tepat di batas
 * pernyataan pada request berikutnya.
 *
 * Hanya bentuk yang dihasilkan pengekspor kita sendiri (Task 5),
 * `wp search-replace --export`, dan mysqldump yang diterima ubah():
 * DROP/CREATE/INSERT/ALTER ... KEYS atas tabel ber-prefix site ini.
 */
class WPMGR_Staging_Sql {

    const MAKS_PERNYATAAN = 67108864;
    const NORMAL          = 0;
    const KUTIP_TUNGGAL   = 1;
    const KUTIP_GANDA     = 2;
    const BACKTICK        = 3;
    const KOMENTAR_BARIS  = 4;
    const KOMENTAR_BLOK   = 5;

    private $buffer  = '';
    private $awal    = 0;
    private $i       = 0;
    private $mulai   = 0;
    private $keadaan = self::NORMAL;

    public function __construct( $offset_awal = 0 ) {
        $this->awal = (int) $offset_awal;
    }

    public function sisa() {
        return (string) substr( $this->buffer, $this->mulai );
    }

    public function tambah( $data ) {
        $this->buffer .= (string) $data;
        $n     = strlen( $this->buffer );
        $hasil = array();
        $kutip = array( self::KUTIP_TUNGGAL => "'", self::KUTIP_GANDA => '"', self::BACKTICK => '`' );
        while ( $this->i < $n ) {
            $s = $this->keadaan;
            if ( self::NORMAL === $s ) {
                $this->i += strcspn( $this->buffer, ";'\"`-#/", $this->i );
                if ( $this->i >= $n ) {
                    break;
                }
                $c = $this->buffer[ $this->i ];
                if ( ';' === $c ) {
                    $hasil[]     = array( substr( $this->buffer, $this->mulai, $this->i - $this->mulai ), $this->awal + $this->i + 1 );
                    $this->i++;
                    $this->mulai = $this->i;
                } elseif ( "'" === $c ) {
                    $this->keadaan = self::KUTIP_TUNGGAL;
                    $this->i++;
                } elseif ( '"' === $c ) {
                    $this->keadaan = self::KUTIP_GANDA;
                    $this->i++;
                } elseif ( '`' === $c ) {
                    $this->keadaan = self::BACKTICK;
                    $this->i++;
                } elseif ( '#' === $c ) {
                    $this->keadaan = self::KOMENTAR_BARIS;
                    $this->i++;
                } elseif ( '-' === $c ) {
                    if ( $this->i + 2 >= $n ) {
                        break; // tunggu data: "--" hanya komentar bila diikuti spasi
                    }
                    if ( '-' === $this->buffer[ $this->i + 1 ] && ctype_space( $this->buffer[ $this->i + 2 ] ) ) {
                        $this->keadaan = self::KOMENTAR_BARIS;
                        $this->i      += 2;
                    } else {
                        $this->i++;
                    }
                } else { // '/'
                    if ( $this->i + 1 >= $n ) {
                        break;
                    }
                    if ( '*' === $this->buffer[ $this->i + 1 ] ) {
                        $this->keadaan = self::KOMENTAR_BLOK;
                        $this->i      += 2;
                    } else {
                        $this->i++;
                    }
                }
            } elseif ( isset( $kutip[ $s ] ) ) {
                $q        = $kutip[ $s ];
                $this->i += strcspn( $this->buffer, self::BACKTICK === $s ? $q : $q . '\\', $this->i );
                if ( $this->i >= $n ) {
                    break;
                }
                if ( '\\' === $this->buffer[ $this->i ] ) {
                    if ( $this->i + 1 >= $n ) {
                        break;
                    }
                    $this->i += 2;
                    continue;
                }
                if ( $this->i + 1 >= $n ) {
                    break; // perlu satu karakter lagi: '' adalah kutip yang di-escape
                }
                if ( $q === $this->buffer[ $this->i + 1 ] ) {
                    $this->i += 2;
                } else {
                    $this->keadaan = self::NORMAL;
                    $this->i++;
                }
            } elseif ( self::KOMENTAR_BARIS === $s ) {
                $p = strpos( $this->buffer, "\n", $this->i );
                if ( false === $p ) {
                    $this->i = $n;
                    break;
                }
                $this->i       = $p + 1;
                $this->keadaan = self::NORMAL;
            } else {
                $p = strpos( $this->buffer, '*/', $this->i );
                if ( false === $p ) {
                    $this->i = max( $this->i, $n - 1 );
                    break;
                }
                $this->i       = $p + 2;
                $this->keadaan = self::NORMAL;
            }
        }
        if ( $this->i - $this->mulai > self::MAKS_PERNYATAAN ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql', 'Satu pernyataan SQL melebihi 64 MB.', 400 );
        }
        // Buang bagian yang sudah selesai supaya buffer tidak tumbuh terus.
        if ( $this->mulai > 1048576 ) {
            $this->buffer = (string) substr( $this->buffer, $this->mulai );
            $this->awal  += $this->mulai;
            $this->i     -= $this->mulai;
            $this->mulai  = 0;
        }
        return $hasil;
    }

    /** Buang spasi dan komentar di awal. Komentar berversi /*!...*\/ juga dibuang. */
    public static function tanpa_komentar_awal( $s ) {
        $s = (string) $s;
        while ( true ) {
            $s = ltrim( $s );
            if ( 0 === strpos( $s, '--' ) || 0 === strpos( $s, '#' ) ) {
                $p = strpos( $s, "\n" );
                $s = false === $p ? '' : substr( $s, $p + 1 );
            } elseif ( 0 === strpos( $s, '/*' ) ) {
                $p = strpos( $s, '*/' );
                $s = false === $p ? '' : substr( $s, $p + 2 );
            } else {
                return rtrim( $s );
            }
        }
    }

    public static function ubah( $pernyataan, $prefix, $awalan = 'wpmgr_tmp_' ) {
        $s = self::tanpa_komentar_awal( $pernyataan );
        if ( '' === $s || 1 === preg_match( '/^(SET\s|LOCK\s+TABLES\s|UNLOCK\s+TABLES\b)/i', $s ) ) {
            return null;
        }
        $pola = '/^(DROP\s+TABLE\s+IF\s+EXISTS|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|INSERT\s+INTO|ALTER\s+TABLE)\s+`([A-Za-z0-9_$]{1,64})`/i';
        if ( 1 !== preg_match( $pola, $s, $m, PREG_OFFSET_CAPTURE ) ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql',
                'Pernyataan SQL tidak diizinkan: ' . WPMGR_Staging::bersih( substr( $s, 0, 60 ), 60 ), 400 );
        }
        $nama = $m[2][0];
        if ( 0 !== strpos( $nama, $prefix ) ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql', 'Tabel di luar prefix site ini: ' . $nama, 400 );
        }
        if ( 0 === strpos( $nama, $prefix . 'wpmgr_' ) ) {
            return null; // Koreksi #14: data pemantauan produksi tidak ditimpa salinan lama.
        }
        if ( 0 === stripos( $m[1][0], 'ALTER' )
            && 1 !== preg_match( '/^ALTER\s+TABLE\s+`[^`]+`\s+(DISABLE|ENABLE)\s+KEYS\z/i', $s ) ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql', 'ALTER TABLE hanya boleh DISABLE/ENABLE KEYS.', 400 );
        }
        $baru = $awalan . $nama;
        if ( strlen( $baru ) > 64 ) {
            return WPMGR_Staging::galat( 'wpmgr_staging_sql', 'Nama tabel terlalu panjang untuk tabel sementara: ' . $nama, 400 );
        }
        return substr( $s, 0, $m[2][1] ) . $baru . substr( $s, $m[2][1] + strlen( $nama ) );
    }
}
```

- [ ] **Step 5: Pembungkus DB.**

File: `connector/wp-manager-connector/includes/class-wpmgr-staging-db.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Akses database untuk dorong. Pernyataan impor dijalankan lewat
 * mysqli_query() langsung, bukan $wpdb->query(): wpdb memeriksa ulang
 * charset setiap query yang memuat byte non-ASCII (strip_invalid_text),
 * yang untuk INSERT multi-megabyte berarti query SHOW FULL COLUMNS
 * tambahan dan regex atas seluruh isi.
 */
class WPMGR_Staging_Db {

    private $wpdb;

    public function __construct( $wpdb ) {
        $this->wpdb = $wpdb;
    }

    public function prefix() {
        return (string) $this->wpdb->prefix;
    }

    public function opsi() {
        return (string) $this->wpdb->options;
    }

    public function kueri( $sql ) {
        $dbh = $this->wpdb->dbh;
        if ( $dbh instanceof mysqli ) {
            $hasil = mysqli_query( $dbh, $sql );
            if ( false === $hasil ) {
                return WPMGR_Staging::bersih( mysqli_error( $dbh ), 300 );
            }
            if ( $hasil instanceof mysqli_result ) {
                mysqli_free_result( $hasil );
            }
            return true;
        }
        $hasil = $this->wpdb->query( $sql ); // phpcs:ignore WordPress.DB.PreparedSQL
        return false === $hasil ? WPMGR_Staging::bersih( (string) $this->wpdb->last_error, 300 ) : true;
    }

    public function kolom( $sql ) {
        return (array) $this->wpdb->get_col( $sql ); // phpcs:ignore WordPress.DB.PreparedSQL
    }

    public function nilai( $sql ) {
        return $this->wpdb->get_var( $sql ); // phpcs:ignore WordPress.DB.PreparedSQL
    }

    public function siapkan( $sql ) {
        return call_user_func_array( array( $this->wpdb, 'prepare' ), func_get_args() );
    }

    public function suka( $t ) {
        return $this->wpdb->esc_like( $t );
    }
}
```

- [ ] **Step 6: Area dorong.**

File: `connector/wp-manager-connector/includes/class-wpmgr-staging-dorong.php`
```php
<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Sisi penerima dorong (spec §6.3): area sementara wp-content/wpmgr-dorong/<id>/,
 * kunci satu-dorongan-per-site, dan (Task 8) langkah terapkan.
 *
 *   potongan/000000.php   paket unggahan apa adanya (kepala exit)
 *   keadaan.php           status dan kursor langkah (kepala exit + JSON)
 *   rencana.php, db.php   rencana dan SQL yang dirakit dari potongan
 *   baru/<path>           berkas baru yang menunggu ditukar
 *   lama/<path>           berkas produksi yang tergeser (untuk pemulihan)
 *   jurnal.php            catatan tulis-lebih-dulu setiap operasi tukar
 */
class WPMGR_Staging_Dorong {

    const KEPALA      = "<?php exit; ?>\n";
    const MAKS_UNGGAH = 8454144;
    const KUNCI       = 'wpmgr_dorong_kunci';
    const UMUR_KUNCI  = 86400;
    const JENIS       = array( 'berkas', 'rentang', 'sql', 'rencana' );

    protected $akar;
    protected $dasar;
    protected $db;
    protected $tenggat;

    public function __construct( $akar, $dasar, $db, $detik ) {
        $this->akar    = $akar;
        $this->dasar   = rtrim( str_replace( '\\', '/', $dasar ), '/' ) . '/';
        $this->db      = $db;
        $this->tenggat = microtime( true ) + (int) $detik;
    }

    public static function id_sah( $id ) {
        return is_string( $id ) && 1 === preg_match( '/^[0-9a-f]{32}\z/', $id );
    }

    protected function galat( $kode, $pesan, $status ) {
        return WPMGR_Staging::galat( $kode, $pesan, $status );
    }

    protected function waktu_habis() {
        return microtime( true ) >= $this->tenggat;
    }

    public function dir( $id ) {
        return $this->dasar . $id . '/';
    }

    protected function pastikan_dir( $dir ) {
        return is_dir( $dir ) || @mkdir( $dir, 0755, true ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
    }

    protected function pastikan_dasar() {
        $this->pastikan_dir( $this->dasar );
        if ( ! file_exists( $this->dasar . 'index.php' ) ) {
            @file_put_contents( $this->dasar . 'index.php', "<?php\n// Silence is golden.\n" ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
        if ( ! file_exists( $this->dasar . '.htaccess' ) ) {
            @file_put_contents( $this->dasar . '.htaccess', // phpcs:ignore WordPress.PHP.NoSilencedErrors
                "<IfModule mod_authz_core.c>\nRequire all denied\n</IfModule>\n<IfModule !mod_authz_core.c>\nDeny from all\n</IfModule>\n" );
        }
    }

    public function tulis_terlindung( $berkas, $data ) {
        $this->pastikan_dir( dirname( $berkas ) );
        $sementara = $berkas . '.' . bin2hex( random_bytes( 4 ) ) . '.tmp';
        if ( false === @file_put_contents( $sementara, self::KEPALA . $data ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            return false;
        }
        return @rename( $sementara, $berkas ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
    }

    public function baca_terlindung( $berkas ) {
        $data = @file_get_contents( $berkas ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $data || 0 !== strncmp( $data, self::KEPALA, strlen( self::KEPALA ) ) ) {
            return false;
        }
        return (string) substr( $data, strlen( self::KEPALA ) );
    }

    public function keadaan( $id ) {
        $data = $this->baca_terlindung( $this->dir( $id ) . 'keadaan.php' );
        $k    = false === $data ? null : json_decode( $data, true );
        return is_array( $k ) ? $k : null;
    }

    public function simpan_keadaan( $id, array $k ) {
        return $this->tulis_terlindung( $this->dir( $id ) . 'keadaan.php', json_encode( $k ) );
    }

    protected function sentuh( $id, array $k ) {
        $k['diubah'] = time();
        $this->simpan_keadaan( $id, $k );
        return $k;
    }

    /** INSERT IGNORE + baca ulang (bukan add_option, yang upsert). */
    public function kunci( $id ) {
        $o     = $this->db->opsi();
        $nilai = $id . '|' . time();
        $this->db->kueri( $this->db->siapkan(
            "INSERT IGNORE INTO {$o} (option_name, option_value, autoload) VALUES (%s, %s, 'no')", self::KUNCI, $nilai ) );
        $sekarang = (string) $this->db->nilai( $this->db->siapkan( "SELECT option_value FROM {$o} WHERE option_name = %s", self::KUNCI ) );
        if ( 0 === strpos( $sekarang, $id . '|' ) ) {
            return true;
        }
        $bagian = explode( '|', $sekarang );
        if ( 2 === count( $bagian ) && (int) $bagian[1] < time() - self::UMUR_KUNCI ) {
            // Pemegang lama tidak menyentuh kuncinya 24 jam: direbut secara
            // atomik, hanya bila nilainya masih sama dengan yang kita baca.
            $this->db->kueri( $this->db->siapkan(
                "UPDATE {$o} SET option_value = %s WHERE option_name = %s AND option_value = %s", $nilai, self::KUNCI, $sekarang ) );
            $ulang = (string) $this->db->nilai( $this->db->siapkan( "SELECT option_value FROM {$o} WHERE option_name = %s", self::KUNCI ) );
            if ( 0 === strpos( $ulang, $id . '|' ) ) {
                return true;
            }
        }
        return $this->galat( 'wpmgr_staging_sibuk', 'Dorongan lain sedang berlangsung di site ini.', 409 );
    }

    public function lepas_kunci( $id ) {
        $o = $this->db->opsi();
        $this->db->kueri( $this->db->siapkan(
            "DELETE FROM {$o} WHERE option_name = %s AND option_value LIKE %s", self::KUNCI, $this->db->suka( $id . '|' ) . '%' ) );
    }

    public function unggah( $data ) {
        $data = (string) $data;
        if ( strlen( $data ) > self::MAKS_UNGGAH ) {
            return $this->galat( 'wpmgr_staging_terlalu_besar', 'Potongan unggahan melebihi 8 MB.', 413 );
        }
        $urai = WPMGR_Staging_Paket::urai( $data );
        if ( is_wp_error( $urai ) ) {
            return $urai;
        }
        $meta  = $urai[0];
        $id    = isset( $meta['dorong_id'] ) ? $meta['dorong_id'] : '';
        $nomor = isset( $meta['nomor'] ) ? $meta['nomor'] : null;
        $jenis = isset( $meta['jenis'] ) ? $meta['jenis'] : '';
        if ( ! self::id_sah( $id ) || ! is_int( $nomor ) || $nomor < 0 || $nomor > 99999
            || ! in_array( $jenis, self::JENIS, true ) ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Meta potongan tidak sah.', 400 );
        }
        if ( in_array( $jenis, array( 'berkas', 'rentang' ), true ) ) {
            foreach ( $meta['berkas'] as $b ) {
                if ( ! isset( $b['path'] ) || ! WPMGR_Staging_Path::boleh_ditulis( $b['path'] ) ) {
                    return $this->galat( 'wpmgr_staging_path', 'Path potongan tidak boleh ditulis.', 400 );
                }
            }
        }
        $kunci = $this->kunci( $id );
        if ( is_wp_error( $kunci ) ) {
            return $kunci;
        }
        $this->pastikan_dasar();
        $k = $this->keadaan( $id );
        if ( null === $k ) {
            $k = array( 'status' => 'mengunggah', 'dibuat' => time() );
        } elseif ( 'mengunggah' !== $k['status'] ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Dorongan ini sudah melewati tahap unggah.', 409 );
        }
        if ( ! $this->tulis_terlindung( $this->dir( $id ) . 'potongan/' . sprintf( '%06d', $nomor ) . '.php', $data ) ) {
            return $this->galat( 'wpmgr_staging_tulis', 'Potongan tidak dapat disimpan (disk penuh atau izin).', 500 );
        }
        $this->sentuh( $id, $k );
        return array( 'ok' => true, 'nomor' => $nomor, 'sha256' => hash( 'sha256', $data ) );
    }

    public function snapshot_berkas( $paths ) {
        if ( ! is_array( $paths ) || count( $paths ) > 5000 ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Daftar path snapshot tidak sah.', 400 );
        }
        $hasil = array();
        foreach ( array_values( $paths ) as $rel ) {
            if ( ! is_string( $rel ) || ! WPMGR_Staging_Path::boleh_ditulis( $rel ) ) {
                return $this->galat( 'wpmgr_staging_path', 'Path snapshot tidak sah.', 400 );
            }
            $abs = $this->akar . $rel;
            clearstatcache( true, $abs );
            if ( is_link( $abs ) || ! is_file( $abs ) ) {
                $hasil[] = array( 'path' => $rel, 'ada' => false );
                continue;
            }
            $hasil[] = array( 'path' => $rel, 'ada' => true, 'ukuran' => (int) filesize( $abs ), 'mtime' => (int) filemtime( $abs ) );
        }
        return $hasil;
    }

    /** Menghapus pohon direktori; false bila waktu habis sebelum selesai. */
    protected function hapus_rekursif( $dir ) {
        if ( ! file_exists( $dir ) && ! is_link( $dir ) ) {
            return true;
        }
        if ( is_link( $dir ) || is_file( $dir ) ) {
            return @unlink( $dir ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
        $it = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator( $dir, FilesystemIterator::SKIP_DOTS ),
            RecursiveIteratorIterator::CHILD_FIRST
        );
        $n = 0;
        foreach ( $it as $f ) {
            $jalur = $f->getPathname();
            if ( $f->isDir() && ! $f->isLink() ) {
                @rmdir( $jalur ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            } else {
                @unlink( $jalur ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
            if ( 0 === ( ++$n % 200 ) && $this->waktu_habis() ) {
                return false;
            }
        }
        return @rmdir( $dir ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
    }

    protected function tabel_dengan_awalan( $awalan ) {
        $semua = $this->db->kolom( "SHOW TABLES LIKE '" . $this->db->suka( $awalan ) . "%'" );
        return array_values( array_filter( array_map( 'strval', $semua ), function ( $t ) {
            return 1 === preg_match( '/^[A-Za-z0-9_$]{1,64}\z/', $t );
        } ) );
    }

    protected function hapus_tabel( $awalan ) {
        foreach ( $this->tabel_dengan_awalan( $awalan ) as $t ) {
            $this->db->kueri( "DROP TABLE IF EXISTS `{$t}`" );
        }
    }

    public function bersihkan( $id ) {
        if ( ! self::id_sah( $id ) ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Id dorongan tidak sah.', 400 );
        }
        $k = $this->keadaan( $id );
        if ( null !== $k && 'menukar' === $k['status'] ) {
            return $this->galat( 'wpmgr_staging_sibuk', 'Dorongan sedang diterapkan; pulihkan dulu.', 409 );
        }
        if ( ! $this->hapus_rekursif( rtrim( $this->dir( $id ), '/' ) ) ) {
            return array( 'lagi' => true );
        }
        $this->hapus_tabel( 'wpmgr_tmp_' );
        if ( null !== $k && in_array( $k['status'], array( 'selesai', 'ditukar' ), true ) ) {
            $this->hapus_tabel( 'wpmgr_old_' );
        }
        $this->lepas_kunci( $id );
        return array( 'lagi' => false );
    }

    /** WP-Cron tiap jam: area yang tidak disentuh 24 jam dibuang. */
    public function cron() {
        if ( ! is_dir( $this->dasar ) ) {
            return;
        }
        foreach ( (array) scandir( $this->dasar ) as $id ) {
            if ( ! self::id_sah( $id ) || $this->waktu_habis() ) {
                continue;
            }
            $k      = $this->keadaan( $id );
            $diubah = null !== $k && isset( $k['diubah'] ) ? (int) $k['diubah'] : (int) @filemtime( $this->dir( $id ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( $diubah < time() - 86400 && ( null === $k || 'menukar' !== $k['status'] ) ) {
                $this->bersihkan( $id );
            }
        }
    }
}
```

- [ ] **Step 7: Route, cron, dan jadwal.** Di `class-wpmgr-staging.php`, tambahkan tiga entri ke `rute()`:

```php
            '/staging/snapshot'  => array( 'POST', 'snapshot' ),
            '/staging/unggah'    => array( 'POST', 'unggah' ),
            '/staging/bersihkan' => array( 'POST', 'bersihkan' ),
```

dan metode:

```php
    public static function dorong() {
        return new WPMGR_Staging_Dorong(
            self::root(),
            rtrim( str_replace( '\\', '/', WP_CONTENT_DIR ), '/' ) . '/wpmgr-dorong/',
            new WPMGR_Staging_Db( $GLOBALS['wpdb'] ),
            self::anggaran_detik()
        );
    }

    private static function body_json( $request ) {
        $p = json_decode( $request->get_body(), true );
        return is_array( $p ) ? $p : null;
    }

    public static function unggah( $request ) {
        return rest_ensure_response( self::dorong()->unggah( $request->get_body() ) );
    }

    public static function snapshot( $request ) {
        global $wpdb;
        $p = self::body_json( $request );
        if ( null === $p ) {
            return self::galat( 'wpmgr_staging_permintaan', 'Body permintaan bukan objek.', 400 );
        }
        $berkas = self::dorong()->snapshot_berkas( isset( $p['paths'] ) ? $p['paths'] : null );
        if ( is_wp_error( $berkas ) ) {
            return $berkas;
        }
        $hasil = array( 'berkas' => $berkas );
        if ( ! empty( $p['awal'] ) ) {
            list( $hasil['tabel'] ) = WPMGR_Staging_Manifest::tabel( $wpdb );
            $hasil['tanda_air']     = WPMGR_Staging_TandaAir::kumpulkan( $wpdb, '', 0 );
        }
        return rest_ensure_response( $hasil );
    }

    public static function bersihkan( $request ) {
        $p = self::body_json( $request );
        return rest_ensure_response( self::dorong()->bersihkan( isset( $p['dorong_id'] ) ? $p['dorong_id'] : '' ) );
    }

    public static function cron_bersihkan() {
        try {
            if ( isset( $GLOBALS['wpdb'] ) ) {
                self::dorong()->cron();
            }
        } catch ( \Throwable $e ) {
            unset( $e );
        }
    }
```

`rest_ensure_response()` meneruskan `WP_Error` apa adanya, jadi galat dari `unggah()`/`bersihkan()` tetap menjadi respons galat REST.

Di `includes/class-wpmgr-skema.php`, tambahkan di akhir `migrasi()`:

```php
        if ( ! wp_next_scheduled( WPMGR_Staging::HOOK_BERSIHKAN ) ) {
            wp_schedule_event( time() + HOUR_IN_SECONDS, 'hourly', WPMGR_Staging::HOOK_BERSIHKAN );
        }
```

dan di akhir `hapus_semua()`:

```php
        wp_clear_scheduled_hook( 'wpmgr_staging_bersihkan' );
```

Di berkas utama, tambahkan setelah `class-wpmgr-staging-tanda-air.php`:

```php
require_once WPMGR_DIR . 'includes/class-wpmgr-staging-sql.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-staging-db.php';
require_once WPMGR_DIR . 'includes/class-wpmgr-staging-dorong.php';
```

dan setelah `add_action( WPMGR_Skema::HOOK_PANGKAS, ... );`:

```php
add_action( WPMGR_Staging::HOOK_BERSIHKAN, array( 'WPMGR_Staging', 'cron_bersihkan' ) );
```

- [ ] **Step 8: PHPUnit 8.3 dan 7.4.** Expected: `OK`.

- [ ] **Step 9: Commit.**

```bash
git add connector
git commit -m "feat(connector): area dorong, kunci, unggah potongan, snapshot, dan pemecah SQL"
```

---

### Task 8: `/staging/terapkan` — siapkan, impor, tukar, pulihkan, selesai

**Files:**
- Modify: `connector/wp-manager-connector/includes/class-wpmgr-staging-dorong.php`, `includes/class-wpmgr-staging.php`
- Create: `connector/tests/TerapkanTest.php`

**Interfaces:**
- Consumes: Task 7 (area, kunci, `WPMGR_Staging_Sql`, `WPMGR_Staging_Db`).
- Produces:
  - `POST /wp-json/wpmgr/v1/staging/terapkan` (HMAC) dengan body `{dorong_id, langkah, ...}`. Langkahnya:
    - `siapkan` `{jumlah_potongan, sha256_rencana}`: merakit potongan, mengekstrak berkas ke `baru/`, lalu memverifikasi ukuran dan sha256 setiap berkas terhadap rencana;
    - `impor`: mengimpor `db.php` ke tabel `wpmgr_tmp_*` lalu mempertahankan opsi produksi (Koreksi #14);
    - `tukar` `{token}`: memasang `.maintenance` dan mu-plugin pengaman, menukar berkas per operasi dengan jurnal tulis-lebih-dulu, lalu menjalankan satu `RENAME TABLE`. Kegagalan di tengah memicu pemulihan otomatis dan dibalas galat;
    - `pulihkan` `{token}`: membalik jurnal;
    - `selesai`: menandai dorongan selesai.
  - Setiap langkah membalas `{selesai: bool, status, kemajuan?}`. `selesai=false` berarti anggaran waktu habis dan dashboard mengulang langkah yang sama. Setiap request dijamin maju minimal satu unit (satu potongan, satu berkas, satu pernyataan, satu operasi).
  - Rencana (JSON, diunggah sebagai potongan `rencana`): `{"versi": 1, "berkas": [{path, ukuran, sha256, mtime}], "hapus": [path], "sql": bool, "charset": "utf8mb4"|"utf8"|"utf8mb3"|"latin1"}`.
  - Status di `keadaan.php`: `mengunggah → menyiapkan → siap → (mengimpor → terimpor) → menukar → ditukar → selesai`, atau `dipulihkan`.
  - `WPMGR_Staging_Dorong`:
    - konstruktor `__construct( $akar, $dasar, $db, $detik, $dir_mu = null )`;
    - langkah: `terapkan( $p ): array|WP_Error`, `siapkan()`, `impor()`, `tukar()`, `pulihkan( $id, $token, $paksa )`, `selesai()`;
    - pengaman dan urutan: `operasi( $rencana ): array( array( aksi, path ) )`, `isi_maintenance( $hash, $sampai ): string`, `isi_mu_aman( $hash ): string`.

Operasi dan cara membaliknya, supaya pemulihan benar dari titik mana pun:

| Operasi | Maju | Balik |
|---|---|---|
| `ganti` | bila tujuan ada dan `lama/` belum: tujuan → `lama/`; lalu `baru/` → tujuan | bila `baru/` masih ada: pulihkan `lama/` → tujuan bila ada. Bila tidak: tujuan → `baru/`, lalu `lama/` → tujuan bila ada |
| `hapus` | bila tujuan ada dan `lama/` belum: tujuan → `lama/` | `lama/` → tujuan bila tujuan kosong |
| `db` | `RENAME TABLE t TO wpmgr_old_t, wpmgr_tmp_t TO t, …` | `t TO wpmgr_tmp_t, wpmgr_old_t TO t` untuk tabel yang punya `old`; `t TO wpmgr_tmp_t` untuk tabel baru. Dijalankan hanya bila keadaan tabel menunjukkan penukaran pernah terjadi |

Operasi `ganti` untuk `wp-content/mu-plugins/` dikerjakan paling akhir, karena mu-plugin dimuat di setiap request, termasuk request tukar berikutnya.

- [ ] **Step 1: Tulis test yang gagal.**

File: `connector/tests/TerapkanTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class TerapkanTest extends TestCase {

    const ID    = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const TOKEN = '0123456789abcdef0123456789abcdef';

    private $akar;
    private $db;

    protected function setUp(): void {
        $this->akar = sys_get_temp_dir() . '/wpmgr-trp-' . bin2hex( random_bytes( 6 ) ) . '/';
        foreach ( array(
            'index.php'                      => '<?php // inti',
            'wp-content/themes/t/style.css'  => 'lama',
            'wp-content/themes/t/hapus.php'  => 'dihapus',
            'wp-content/plugins/p/p.php'     => 'p lama',
        ) as $rel => $isi ) {
            if ( ! is_dir( dirname( $this->akar . $rel ) ) ) {
                mkdir( dirname( $this->akar . $rel ), 0777, true );
            }
            file_put_contents( $this->akar . $rel, $isi );
        }
        mkdir( $this->akar . 'wp-content/mu-plugins', 0777, true );
        $this->db        = new WPMGR_FakeDbDorong();
        $this->db->tabel = array( 'wp_posts', 'wp_options' );
    }

    protected function tearDown(): void {
        StagingDasarTest::hapus( rtrim( $this->akar, '/' ) );
    }

    private function dorong( $detik = 20 ) {
        return new WPMGR_Staging_Dorong( $this->akar, $this->akar . 'wp-content/wpmgr-dorong/', $this->db,
            $detik, $this->akar . 'wp-content/mu-plugins/' );
    }

    private function paket( $nomor, $jenis, array $berkas, array $isi ) {
        return WPMGR_Staging_Paket::susun(
            array( 'dorong_id' => self::ID, 'nomor' => $nomor, 'jenis' => $jenis, 'berkas' => $berkas ), $isi );
    }

    /** Unggah seperti dashboard; berkas besar dikirim dalam dua rentang, SQL dan rencana dalam dua potongan. */
    private function unggah( array $baru, array $hapus, $sql ) {
        $d       = $this->dorong();
        $nomor   = 0;
        $rencana = array( 'versi' => 1, 'berkas' => array(), 'hapus' => $hapus, 'sql' => null !== $sql, 'charset' => 'utf8mb4' );
        foreach ( $baru as $rel => $isi ) {
            $rencana['berkas'][] = array( 'path' => $rel, 'ukuran' => strlen( $isi ), 'sha256' => hash( 'sha256', $isi ), 'mtime' => 1700000000 );
            if ( strlen( $isi ) > 8 ) {
                $separuh = intdiv( strlen( $isi ), 2 );
                $d->unggah( $this->paket( $nomor++, 'rentang', array( array( 'path' => $rel, 'dari' => 0 ) ), array( substr( $isi, 0, $separuh ) ) ) );
                $d->unggah( $this->paket( $nomor++, 'rentang', array( array( 'path' => $rel, 'dari' => $separuh ) ), array( substr( $isi, $separuh ) ) ) );
            } else {
                $d->unggah( $this->paket( $nomor++, 'berkas', array( array( 'path' => $rel, 'mtime' => 1700000000 ) ), array( $isi ) ) );
            }
        }
        if ( null !== $sql ) {
            $separuh = intdiv( strlen( $sql ), 2 );
            $d->unggah( $this->paket( $nomor++, 'sql', array( array( 'path' => 'sql' ) ), array( substr( $sql, 0, $separuh ) ) ) );
            $d->unggah( $this->paket( $nomor++, 'sql', array( array( 'path' => 'sql' ) ), array( substr( $sql, $separuh ) ) ) );
        }
        $json = json_encode( $rencana );
        $d->unggah( $this->paket( $nomor++, 'rencana', array( array( 'path' => 'rencana' ) ), array( substr( $json, 0, 10 ) ) ) );
        $d->unggah( $this->paket( $nomor++, 'rencana', array( array( 'path' => 'rencana' ) ), array( substr( $json, 10 ) ) ) );
        return array( $nomor, hash( 'sha256', $json ) );
    }

    private function sampai_selesai( $langkah, array $tambahan = array(), $detik = 20 ) {
        for ( $i = 0; $i < 200; $i++ ) {
            $h = $this->dorong( $detik )->terapkan( array_merge( array( 'dorong_id' => self::ID, 'langkah' => $langkah ), $tambahan ) );
            if ( is_wp_error( $h ) || $h['selesai'] ) {
                return $h;
            }
        }
        $this->fail( 'Langkah ' . $langkah . ' tidak pernah selesai.' );
    }

    private function sql_contoh() {
        return "SET NAMES utf8mb4;\nDROP TABLE IF EXISTS `wp_posts`;\nCREATE TABLE `wp_posts` (`ID` int);\n"
            . "INSERT INTO `wp_posts` (`ID`) VALUES (1),(2);\nINSERT INTO `wp_posts` (`ID`) VALUES (3);\n"
            . "DROP TABLE IF EXISTS `wp_options`;\nCREATE TABLE `wp_options` (`option_name` varchar(191));\n"
            . "INSERT INTO `wp_wpmgr_errors` VALUES (1);\n";
    }

    public function test_alur_lengkap_berkas_dan_database(): void {
        $baru = array(
            'wp-content/themes/t/style.css' => 'baru-besar-sekali',
            'wp-content/themes/t/baru.php'  => '<?php //',
            'wp-content/mu-plugins/m.php'   => '<?php //m',
        );
        list( $n, $sha ) = $this->unggah( $baru, array( 'wp-content/themes/t/hapus.php' ), $this->sql_contoh() );

        $h = $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->assertSame( 'siap', $h['status'] );
        $dir = $this->akar . 'wp-content/wpmgr-dorong/' . self::ID . '/';
        $this->assertSame( 'baru-besar-sekali', file_get_contents( $dir . 'baru/wp-content/themes/t/style.css' ) );
        $this->assertFileDoesNotExist( $dir . 'baru/wp-content/themes/t/style.css.wpmgr-bagian' );

        $this->assertSame( 'terimpor', $this->sampai_selesai( 'impor' )['status'] );
        $semua = implode( "\n", $this->db->kueri );
        $this->assertStringContainsString( 'CREATE TABLE `wpmgr_tmp_wp_posts`', $semua );
        $this->assertStringContainsString( 'INSERT INTO `wpmgr_tmp_wp_posts` (`ID`) VALUES (3)', $semua );
        $this->assertStringNotContainsString( 'wp_wpmgr_errors', $semua );
        $this->assertStringContainsString( 'UPDATE `wpmgr_tmp_wp_options` t JOIN `wp_options` o', $semua );
        $this->assertStringContainsString( "SET SESSION sql_mode = 'NO_AUTO_VALUE_ON_ZERO'", $semua );

        // Satu operasi per request: pengaman terlihat di tengah jalan.
        $tengah = $this->dorong( 0 )->terapkan( array( 'dorong_id' => self::ID, 'langkah' => 'tukar', 'token' => self::TOKEN ) );
        $this->assertFalse( $tengah['selesai'] );
        $m = file_get_contents( $this->akar . '.maintenance' );
        $this->assertStringContainsString( '$upgrading = ' . ( $this->dorong()->keadaan( self::ID )['maintenance_dibuat'] + 300 ) . ';', $m );
        $this->assertStringContainsString( hash( 'sha256', self::TOKEN ), $m );
        $this->assertFileExists( $this->akar . 'wp-content/mu-plugins/wpmgr-dorong-aman.php' );

        $h = $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'ditukar', $h['status'] );
        $this->assertSame( 'baru-besar-sekali', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertSame( '<?php //', file_get_contents( $this->akar . 'wp-content/themes/t/baru.php' ) );
        $this->assertFileDoesNotExist( $this->akar . 'wp-content/themes/t/hapus.php' );
        $this->assertSame( 'p lama', file_get_contents( $this->akar . 'wp-content/plugins/p/p.php' ) );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
        $this->assertFileDoesNotExist( $this->akar . 'wp-content/mu-plugins/wpmgr-dorong-aman.php' );
        $this->assertContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        $this->assertContains( 'wp_posts', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_tmp_wp_posts', $this->db->tabel );

        $this->assertSame( 'selesai', $this->dorong()->terapkan( array( 'dorong_id' => self::ID, 'langkah' => 'selesai' ) )['status'] );
        $this->assertSame( array( 'lagi' => false ), $this->dorong()->bersihkan( self::ID ) );
        $this->assertNotContains( 'wpmgr_old_wp_posts', $this->db->tabel );
    }

    public function test_impor_berlanjut_tepat_di_batas_pernyataan(): void {
        list( $n, $sha ) = $this->unggah( array(), array(), $this->sql_contoh() );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->assertSame( 'terimpor', $this->sampai_selesai( 'impor', array(), 0 )['status'] );
        $sisip = array_filter( $this->db->kueri, function ( $q ) {
            return 0 === strpos( $q, 'INSERT INTO `wpmgr_tmp_wp_posts`' );
        } );
        $this->assertCount( 2, $sisip );
    }

    public function test_siapkan_menolak_potongan_kurang_dan_rencana_salah(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/a.css' => 'a' ), array(), null );
        $kurang = $this->dorong()->terapkan( array( 'dorong_id' => self::ID, 'langkah' => 'siapkan', 'jumlah_potongan' => $n + 1, 'sha256_rencana' => $sha ) );
        $this->assertSame( 'wpmgr_staging_kurang', $kurang->get_error_code() );
        $salah = $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => str_repeat( '0', 64 ) ) );
        $this->assertSame( 'wpmgr_staging_rencana', $salah->get_error_code() );
    }

    public function test_hash_berkas_tidak_cocok_ditolak_saat_verifikasi(): void {
        $d       = $this->dorong();
        $d->unggah( $this->paket( 0, 'berkas', array( array( 'path' => 'wp-content/themes/t/a.css' ) ), array( 'isi asli' ) ) );
        $json    = json_encode( array( 'versi' => 1, 'berkas' => array( array( 'path' => 'wp-content/themes/t/a.css',
            'ukuran' => 8, 'sha256' => hash( 'sha256', 'isi lain' ), 'mtime' => 1 ) ), 'hapus' => array(), 'sql' => false, 'charset' => 'utf8mb4' ) );
        $d->unggah( $this->paket( 1, 'rencana', array( array( 'path' => 'rencana' ) ), array( $json ) ) );
        $h = $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => 2, 'sha256_rencana' => hash( 'sha256', $json ) ) );
        $this->assertSame( 'wpmgr_staging_verifikasi', $h->get_error_code() );
    }

    public function test_tukar_gagal_di_tengah_dipulihkan_otomatis(): void {
        // Induk tujuan berupa berkas: direktori untuk berkas baru tidak bisa dibuat.
        file_put_contents( $this->akar . 'wp-content/themes/t/penghalang', 'berkas' );
        list( $n, $sha ) = $this->unggah( array(
            'wp-content/themes/t/style.css'        => 'baru',
            'wp-content/themes/t/penghalang/x.css' => 'x',
        ), array( 'wp-content/themes/t/hapus.php' ), null );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $h = $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertInstanceOf( WP_Error::class, $h );
        $this->assertSame( 'wpmgr_staging_tukar', $h->get_error_code() );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertSame( 'dihapus', file_get_contents( $this->akar . 'wp-content/themes/t/hapus.php' ) );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
        $this->assertSame( 'dipulihkan', $this->dorong()->keadaan( self::ID )['status'] );
    }

    public function test_rename_tabel_gagal_memulihkan_berkas(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/style.css' => 'baru' ), array(), $this->sql_contoh() );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->sampai_selesai( 'impor' );
        $this->db->gagal_pada = 'RENAME TABLE `wp_posts` TO `wpmgr_old_wp_posts`';
        $h = $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'wpmgr_staging_tukar', $h->get_error_code() );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertContains( 'wp_posts', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        $this->assertFileDoesNotExist( $this->akar . '.maintenance' );
    }

    public function test_token_berbeda_ditolak(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/a.css' => 'a', 'wp-content/themes/t/b.css' => 'b' ), array(), null );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->dorong( 0 )->terapkan( array( 'dorong_id' => self::ID, 'langkah' => 'tukar', 'token' => self::TOKEN ) );
        $lain = $this->dorong()->terapkan( array( 'dorong_id' => self::ID, 'langkah' => 'tukar', 'token' => str_repeat( 'f', 32 ) ) );
        $this->assertSame( 403, $lain->get_error_data()['status'] );
        $this->assertSame( 403, $this->dorong()->terapkan( array( 'dorong_id' => self::ID, 'langkah' => 'pulihkan', 'token' => str_repeat( 'f', 32 ) ) )->get_error_data()['status'] );
    }

    public function test_pulihkan_setelah_ditukar_mengembalikan_semuanya(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/style.css' => 'baru', 'wp-content/themes/t/baru.php' => 'b' ),
            array( 'wp-content/themes/t/hapus.php' ), $this->sql_contoh() );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->sampai_selesai( 'impor' );
        $this->sampai_selesai( 'tukar', array( 'token' => self::TOKEN ) );
        $h = $this->sampai_selesai( 'pulihkan', array( 'token' => self::TOKEN ) );
        $this->assertSame( 'dipulihkan', $h['status'] );
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertSame( 'dihapus', file_get_contents( $this->akar . 'wp-content/themes/t/hapus.php' ) );
        $this->assertFileDoesNotExist( $this->akar . 'wp-content/themes/t/baru.php' );
        $this->assertContains( 'wp_posts', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_old_wp_posts', $this->db->tabel );
        $this->assertNotContains( 'wpmgr_tmp_wp_posts', $this->db->tabel );
    }

    public function test_cron_memulihkan_tukar_yang_macet(): void {
        list( $n, $sha ) = $this->unggah( array( 'wp-content/themes/t/style.css' => 'baru', 'wp-content/themes/t/b.css' => 'b' ), array(), null );
        $this->sampai_selesai( 'siapkan', array( 'jumlah_potongan' => $n, 'sha256_rencana' => $sha ) );
        $this->dorong( 0 )->terapkan( array( 'dorong_id' => self::ID, 'langkah' => 'tukar', 'token' => self::TOKEN ) );
        $this->assertSame( 'baru', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $d           = $this->dorong();
        $k           = $d->keadaan( self::ID );
        $k['diubah'] = time() - 1000;
        $d->simpan_keadaan( self::ID, $k );
        $d->cron();
        $this->assertSame( 'lama', file_get_contents( $this->akar . 'wp-content/themes/t/style.css' ) );
        $this->assertSame( 'dipulihkan', $d->keadaan( self::ID )['status'] );
    }

    public function test_urutan_operasi_mu_plugin_terakhir(): void {
        $ops = $this->dorong()->operasi( array(
            'berkas' => array( array( 'path' => 'wp-content/mu-plugins/a.php' ), array( 'path' => 'wp-content/themes/t/a.css' ) ),
            'hapus'  => array( 'wp-content/mu-plugins/b.php', 'wp-content/plugins/p/x.php' ),
        ) );
        $this->assertSame( array(
            array( 'ganti', 'wp-content/themes/t/a.css' ), array( 'ganti', 'wp-content/mu-plugins/a.php' ),
            array( 'hapus', 'wp-content/plugins/p/x.php' ), array( 'hapus', 'wp-content/mu-plugins/b.php' ),
        ), $ops );
    }

    public function test_isi_maintenance_dan_mu_aman(): void {
        $m = WPMGR_Staging_Dorong::isi_maintenance( str_repeat( 'a', 64 ), 1700000300 );
        $this->assertStringStartsWith( '<?php', $m );
        $this->assertStringContainsString( '$upgrading = 1700000300;', $m );
        $this->assertStringContainsString( "HTTP_X_WPMGR_LEWATI", $m );
        $mu = WPMGR_Staging_Dorong::isi_mu_aman( str_repeat( 'a', 64 ) );
        $this->assertStringContainsString( "'wp-manager-connector/wp-manager-connector.php'", $mu );
        $this->assertStringContainsString( 'pre_option_template', $mu );
        $this->assertStringContainsString( 'pre_option_stylesheet', $mu );
    }
}
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `cd connector && vendor/bin/phpunit --filter TerapkanTest`. Expected: `Error: Call to undefined method WPMGR_Staging_Dorong::terapkan()`.

- [ ] **Step 3: Implementasikan.** Di `class-wpmgr-staging-dorong.php`, ganti konstruktor dan tambahkan properti:

```php
    protected $dir_mu;
    protected $rencana_cache = null;

    public function __construct( $akar, $dasar, $db, $detik, $dir_mu = null ) {
        $this->akar    = $akar;
        $this->dasar   = rtrim( str_replace( '\\', '/', $dasar ), '/' ) . '/';
        $this->db      = $db;
        $this->tenggat = microtime( true ) + (int) $detik;
        $this->dir_mu  = null === $dir_mu ? $akar . 'wp-content/mu-plugins/' : rtrim( $dir_mu, '/\\' ) . '/';
    }
```

Ganti isi loop di `cron()` agar tukar yang macet dipulihkan (sebelum cek 24 jam):

```php
            $k      = $this->keadaan( $id );
            $diubah = null !== $k && isset( $k['diubah'] ) ? (int) $k['diubah'] : (int) @filemtime( $this->dir( $id ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            if ( null !== $k && 'menukar' === $k['status'] && $diubah < time() - 900 ) {
                // Dashboard berhenti di tengah tukar (worker mati, jaringan
                // putus): site tidak boleh dibiarkan setengah tertukar.
                $this->pulihkan( $id, null, true );
                continue;
            }
            if ( $diubah < time() - 86400 && ( null === $k || 'menukar' !== $k['status'] ) ) {
                $this->bersihkan( $id );
            }
```

Tambahkan metode berikut ke kelas:

```php
    const CHARSET = array( 'utf8mb4', 'utf8', 'utf8mb3', 'latin1' );
    const TANDA_MAINTENANCE = 'Dipasang WP Manager selama dorongan staging diterapkan';

    protected function lagi( $id, array $k ) {
        $k = $this->sentuh( $id, $k );
        return array(
            'selesai'  => false,
            'status'   => $k['status'],
            'kemajuan' => array(
                'ekstrak'      => isset( $k['ekstrak'] ) ? $k['ekstrak'] : 0,
                'verifikasi'   => isset( $k['verifikasi'] ) ? $k['verifikasi'] : 0,
                'impor_posisi' => isset( $k['impor_posisi'] ) ? $k['impor_posisi'] : 0,
                'tukar'        => isset( $k['tukar'] ) ? $k['tukar'] : 0,
            ),
        );
    }

    public function terapkan( $p ) {
        if ( ! is_array( $p ) || ! isset( $p['dorong_id'], $p['langkah'] ) || ! self::id_sah( $p['dorong_id'] ) ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Permintaan terapkan tidak sah.', 400 );
        }
        $id = $p['dorong_id'];
        $k  = $this->keadaan( $id );
        if ( null === $k ) {
            return $this->galat( 'wpmgr_staging_tidak_ada', 'Dorongan tidak ditemukan.', 404 );
        }
        $kunci = $this->kunci( $id );
        if ( is_wp_error( $kunci ) ) {
            return $kunci;
        }
        $token = isset( $p['token'] ) ? $p['token'] : null;
        switch ( $p['langkah'] ) {
            case 'siapkan':
                return $this->siapkan( $id, $k, $p );
            case 'impor':
                return $this->impor( $id, $k );
            case 'tukar':
                return $this->tukar( $id, $k, $token );
            case 'pulihkan':
                return $this->pulihkan( $id, $token, false );
            case 'selesai':
                return $this->selesai( $id, $k );
        }
        return $this->galat( 'wpmgr_staging_permintaan', 'Langkah tidak dikenal.', 400 );
    }

    protected function potongan( $id, $i ) {
        return $this->dir( $id ) . 'potongan/' . sprintf( '%06d', $i ) . '.php';
    }

    /** Tulis bagian ke posisi tertentu; memotong sisa percobaan sebelumnya supaya idempoten. */
    protected function tambahkan( $berkas, $data, $posisi ) {
        $kepala = strlen( self::KEPALA );
        $h      = @fopen( $berkas, 'c+b' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $h ) {
            return false;
        }
        $ukuran = (int) fstat( $h )['size'];
        if ( 0 === $ukuran ) {
            fwrite( $h, self::KEPALA );
            $ukuran = $kepala;
        }
        if ( $ukuran - $kepala < $posisi ) {
            fclose( $h );
            return false;
        }
        ftruncate( $h, $kepala + $posisi );
        fseek( $h, $kepala + $posisi );
        $ok = strlen( $data ) === fwrite( $h, $data );
        fclose( $h );
        return $ok;
    }

    protected function ekstrak( $id, array $k, array $meta, array $bagian ) {
        $dir = $this->dir( $id );
        if ( 'rencana' === $meta['jenis'] || 'sql' === $meta['jenis'] ) {
            $kunci  = 'rencana' === $meta['jenis'] ? 'rencana_byte' : 'sql_byte';
            $berkas = $dir . ( 'rencana' === $meta['jenis'] ? 'rencana.php' : 'db.php' );
            if ( ! $this->tambahkan( $berkas, $bagian[0], $k[ $kunci ] ) ) {
                return $this->galat( 'wpmgr_staging_tulis', 'Potongan tidak dapat dirakit.', 500 );
            }
            $k[ $kunci ] += strlen( $bagian[0] );
            return $k;
        }
        foreach ( $meta['berkas'] as $i => $b ) {
            if ( ! isset( $b['path'] ) || ! WPMGR_Staging_Path::boleh_ditulis( $b['path'] ) ) {
                return $this->galat( 'wpmgr_staging_path', 'Path potongan tidak boleh ditulis.', 400 );
            }
            $tujuan = $dir . 'baru/' . $b['path'];
            $this->pastikan_dir( dirname( $tujuan ) );
            if ( 'rentang' === $meta['jenis'] ) {
                $dari = isset( $b['dari'] ) && is_int( $b['dari'] ) ? $b['dari'] : -1;
                $sb   = $tujuan . '.wpmgr-bagian';
                $ada  = is_file( $sb ) ? (int) filesize( $sb ) : 0;
                if ( $dari < 0 || $dari > $ada ) {
                    return $this->galat( 'wpmgr_staging_urutan', 'Rentang berkas tidak berurutan.', 409 );
                }
                $h = @fopen( $sb, 'c+b' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
                if ( false === $h ) {
                    return $this->galat( 'wpmgr_staging_tulis', 'Berkas sementara tidak dapat ditulis.', 500 );
                }
                ftruncate( $h, $dari );
                fseek( $h, $dari );
                $ok = strlen( $bagian[ $i ] ) === fwrite( $h, $bagian[ $i ] );
                fclose( $h );
            } else {
                $ok = false !== @file_put_contents( $tujuan, $bagian[ $i ] ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
            if ( ! $ok ) {
                return $this->galat( 'wpmgr_staging_tulis', 'Berkas sementara tidak dapat ditulis (disk penuh?).', 500 );
            }
        }
        return $k;
    }

    protected function rencana( $id, array $k ) {
        if ( null !== $this->rencana_cache ) {
            return $this->rencana_cache;
        }
        $salah = $this->galat( 'wpmgr_staging_rencana', 'Rencana dorong rusak atau tidak cocok.', 422 );
        $data  = $this->baca_terlindung( $this->dir( $id ) . 'rencana.php' );
        if ( false === $data || empty( $k['sha256_rencana'] ) || ! hash_equals( $k['sha256_rencana'], hash( 'sha256', $data ) ) ) {
            return $salah;
        }
        $r = json_decode( $data, true );
        if ( ! is_array( $r ) || 1 !== ( isset( $r['versi'] ) ? $r['versi'] : 0 ) || ! isset( $r['berkas'], $r['hapus'] )
            || ! is_array( $r['berkas'] ) || ! is_array( $r['hapus'] ) || ! isset( $r['sql'] ) || ! is_bool( $r['sql'] )
            || ! isset( $r['charset'] ) || ! in_array( $r['charset'], self::CHARSET, true ) ) {
            return $salah;
        }
        foreach ( $r['berkas'] as $b ) {
            if ( ! is_array( $b ) || ! isset( $b['path'], $b['ukuran'], $b['sha256'] ) || ! WPMGR_Staging_Path::boleh_ditulis( $b['path'] )
                || ! is_int( $b['ukuran'] ) || $b['ukuran'] < 0 || 1 !== preg_match( '/^[0-9a-f]{64}\z/', (string) $b['sha256'] ) ) {
                return $salah;
            }
        }
        foreach ( $r['hapus'] as $h ) {
            if ( ! WPMGR_Staging_Path::boleh_ditulis( $h ) ) {
                return $salah;
            }
        }
        $this->rencana_cache = $r;
        return $r;
    }

    public function siapkan( $id, array $k, array $p ) {
        if ( ! in_array( $k['status'], array( 'mengunggah', 'menyiapkan' ), true ) ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Dorongan sudah disiapkan.', 409 );
        }
        if ( 'mengunggah' === $k['status'] ) {
            $n   = isset( $p['jumlah_potongan'] ) ? $p['jumlah_potongan'] : 0;
            $sha = isset( $p['sha256_rencana'] ) ? $p['sha256_rencana'] : '';
            if ( ! is_int( $n ) || $n < 1 || $n > 100000 || ! is_string( $sha ) || 1 !== preg_match( '/^[0-9a-f]{64}\z/', $sha ) ) {
                return $this->galat( 'wpmgr_staging_permintaan', 'Jumlah potongan atau hash rencana tidak sah.', 400 );
            }
            for ( $i = 0; $i < $n; $i++ ) {
                if ( ! is_file( $this->potongan( $id, $i ) ) ) {
                    return $this->galat( 'wpmgr_staging_kurang', 'Potongan ' . $i . ' belum diunggah.', 409 );
                }
            }
            $k = $this->sentuh( $id, array_merge( $k, array(
                'status' => 'menyiapkan', 'jumlah_potongan' => $n, 'sha256_rencana' => $sha,
                'ekstrak' => 0, 'rencana_byte' => 0, 'sql_byte' => 0, 'verifikasi' => 0,
            ) ) );
        }
        $maju = false;
        while ( $k['ekstrak'] < $k['jumlah_potongan'] ) {
            if ( $maju && $this->waktu_habis() ) {
                return $this->lagi( $id, $k );
            }
            $data = $this->baca_terlindung( $this->potongan( $id, $k['ekstrak'] ) );
            $urai = false === $data ? $this->galat( 'wpmgr_staging_paket', 'Potongan tersimpan rusak.', 500 ) : WPMGR_Staging_Paket::urai( $data );
            if ( is_wp_error( $urai ) ) {
                return $urai;
            }
            $hasil = $this->ekstrak( $id, $k, $urai[0], $urai[1] );
            if ( is_wp_error( $hasil ) ) {
                return $hasil;
            }
            $k = $hasil;
            $k['ekstrak']++;
            $k    = $this->sentuh( $id, $k );
            $maju = true;
        }
        $r = $this->rencana( $id, $k );
        if ( is_wp_error( $r ) ) {
            return $r;
        }
        $dir = $this->dir( $id );
        foreach ( $r['berkas'] as $b ) {
            $sb = $dir . 'baru/' . $b['path'] . '.wpmgr-bagian';
            if ( is_file( $sb ) ) {
                @rename( $sb, $dir . 'baru/' . $b['path'] ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
        }
        $jumlah = count( $r['berkas'] );
        while ( $k['verifikasi'] < $jumlah ) {
            if ( $maju && $this->waktu_habis() ) {
                return $this->lagi( $id, $k );
            }
            $b = $r['berkas'][ $k['verifikasi'] ];
            $f = $dir . 'baru/' . $b['path'];
            clearstatcache( true, $f );
            if ( ! is_file( $f ) || (int) filesize( $f ) !== $b['ukuran'] || ! hash_equals( $b['sha256'], (string) hash_file( 'sha256', $f ) ) ) {
                return $this->galat( 'wpmgr_staging_verifikasi',
                    'Berkas tidak lengkap atau berbeda dari rencana: ' . WPMGR_Staging::bersih( $b['path'], 200 ), 422 );
            }
            if ( isset( $b['mtime'] ) && is_int( $b['mtime'] ) ) {
                @touch( $f, $b['mtime'] ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            }
            $k['verifikasi']++;
            $maju = true;
        }
        $k['status']  = 'siap';
        $k['sql']     = $r['sql'];
        $k['charset'] = $r['charset'];
        $this->sentuh( $id, $k );
        return array( 'selesai' => true, 'status' => 'siap' );
    }

    protected function sesi_impor( $charset ) {
        foreach ( array( "SET NAMES {$charset}", 'SET FOREIGN_KEY_CHECKS=0', 'SET UNIQUE_CHECKS=0',
                         "SET SESSION sql_mode = 'NO_AUTO_VALUE_ON_ZERO'" ) as $q ) {
            $r = $this->db->kueri( $q );
            if ( true !== $r ) {
                return $r;
            }
        }
        return true;
    }

    /** Koreksi #14: alamat, visibilitas mesin pencari, dan opsi connector produksi tidak ikut ditimpa. */
    protected function pertahankan_opsi() {
        $p    = $this->db->prefix();
        $tmp  = 'wpmgr_tmp_' . $p . 'options';
        $asli = $p . 'options';
        if ( ! in_array( $tmp, $this->tabel_dengan_awalan( 'wpmgr_tmp_' ), true ) ) {
            return true;
        }
        $syarat = "option_name IN ('siteurl','home','blog_public') OR option_name LIKE 'wpmgr\\\\_%'";
        $r      = $this->db->kueri( "INSERT IGNORE INTO `{$tmp}` (option_name, option_value, autoload)"
            . " SELECT option_name, option_value, autoload FROM `{$asli}` WHERE {$syarat}" );
        if ( true !== $r ) {
            return $r;
        }
        return $this->db->kueri( "UPDATE `{$tmp}` t JOIN `{$asli}` o ON o.option_name = t.option_name"
            . " SET t.option_value = o.option_value WHERE t.{$syarat}" );
    }

    public function impor( $id, array $k ) {
        if ( empty( $k['sql'] ) || ! in_array( $k['status'], array( 'siap', 'mengimpor' ), true ) ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Dorongan ini tidak menunggu impor database.', 409 );
        }
        if ( 'siap' === $k['status'] ) {
            $this->hapus_tabel( 'wpmgr_tmp_' );
            $k = $this->sentuh( $id, array_merge( $k, array( 'status' => 'mengimpor', 'impor_posisi' => 0 ) ) );
        }
        $r = $this->sesi_impor( $k['charset'] );
        if ( true !== $r ) {
            return $this->galat( 'wpmgr_staging_impor', 'Sesi impor tidak dapat disiapkan: ' . $r, 500 );
        }
        $h = @fopen( $this->dir( $id ) . 'db.php', 'rb' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        if ( false === $h ) {
            return $this->galat( 'wpmgr_staging_impor', 'Berkas SQL dorongan tidak ada.', 500 );
        }
        fseek( $h, strlen( self::KEPALA ) + (int) $k['impor_posisi'] );
        $pemecah = new WPMGR_Staging_Sql( (int) $k['impor_posisi'] );
        $prefix  = $this->db->prefix();
        while ( ! feof( $h ) ) {
            $hasil = $pemecah->tambah( (string) fread( $h, 1048576 ) );
            if ( is_wp_error( $hasil ) ) {
                fclose( $h );
                return $hasil;
            }
            foreach ( $hasil as $satu ) {
                $ubah = WPMGR_Staging_Sql::ubah( $satu[0], $prefix );
                if ( is_wp_error( $ubah ) ) {
                    fclose( $h );
                    return $ubah;
                }
                if ( null !== $ubah ) {
                    $r = $this->db->kueri( $ubah );
                    if ( true !== $r ) {
                        fclose( $h );
                        return $this->galat( 'wpmgr_staging_impor', 'Impor database gagal: ' . $r, 500 );
                    }
                }
                $k['impor_posisi'] = $satu[1];
                // Diperiksa setelah satu pernyataan selesai: setiap request maju.
                if ( $this->waktu_habis() ) {
                    fclose( $h );
                    return $this->lagi( $id, $k );
                }
            }
        }
        fclose( $h );
        if ( '' !== WPMGR_Staging_Sql::tanpa_komentar_awal( $pemecah->sisa() ) ) {
            return $this->galat( 'wpmgr_staging_impor', 'Berkas SQL terpotong di akhir.', 422 );
        }
        $r = $this->pertahankan_opsi();
        if ( true !== $r ) {
            return $this->galat( 'wpmgr_staging_impor', 'Opsi produksi tidak dapat dipertahankan: ' . $r, 500 );
        }
        $k['status'] = 'terimpor';
        $this->sentuh( $id, $k );
        return array( 'selesai' => true, 'status' => 'terimpor' );
    }

    public function operasi( array $rencana ) {
        $ops = array();
        foreach ( array( 'ganti', 'hapus' ) as $aksi ) {
            $daftar = 'ganti' === $aksi
                ? array_map( function ( $b ) {
                    return $b['path'];
                }, $rencana['berkas'] )
                : $rencana['hapus'];
            $biasa = array();
            $mu    = array();
            foreach ( $daftar as $path ) {
                if ( 0 === strpos( $path, 'wp-content/mu-plugins/' ) ) {
                    $mu[] = array( $aksi, $path );
                } else {
                    $biasa[] = array( $aksi, $path );
                }
            }
            $ops = array_merge( $ops, $biasa, $mu );
        }
        return $ops;
    }

    public static function isi_maintenance( $hash, $sampai ) {
        return "<?php\n// " . self::TANDA_MAINTENANCE . ".\n"
            . "// WordPress mengabaikan berkas ini 10 menit setelah \$upgrading, jadi batasnya 15 menit sejak dibuat.\n"
            . '$upgrading = ' . (int) $sampai . ";\n"
            . "if ( isset( \$_SERVER['HTTP_X_WPMGR_LEWATI'] ) && hash_equals( '" . $hash . "', hash( 'sha256', (string) \$_SERVER['HTTP_X_WPMGR_LEWATI'] ) ) ) {\n"
            . "    \$upgrading = 0;\n"
            . "}\n";
    }

    public static function isi_mu_aman( $hash ) {
        return "<?php\n/**\n * Plugin Name: WP Manager — pengaman dorong (sementara)\n"
            . " * Description: Dipasang selama dorongan staging diterapkan dan dihapus sesudahnya.\n */\n"
            . "if ( isset( \$_SERVER['HTTP_X_WPMGR_LEWATI'] ) && hash_equals( '" . $hash . "', hash( 'sha256', (string) \$_SERVER['HTTP_X_WPMGR_LEWATI'] ) ) ) {\n"
            . "    add_filter( 'option_active_plugins', function () {\n"
            . "        return array( 'wp-manager-connector/wp-manager-connector.php' );\n"
            . "    }, PHP_INT_MAX );\n"
            . "    add_filter( 'site_option_active_sitewide_plugins', function () {\n"
            . "        return array();\n"
            . "    }, PHP_INT_MAX );\n"
            . "    add_filter( 'pre_option_template', function () {\n"
            . "        return 'wpmgr-tanpa-tema';\n"
            . "    } );\n"
            . "    add_filter( 'pre_option_stylesheet', function () {\n"
            . "        return 'wpmgr-tanpa-tema';\n"
            . "    } );\n"
            . "}\n";
    }

    protected function pasang_pengaman( array $k ) {
        @file_put_contents( $this->akar . '.maintenance', self::isi_maintenance( $k['token_hash'], (int) $k['maintenance_dibuat'] + 300 ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        // mu-plugins yang terkunci bukan alasan gagal: tanpa pengaman ini,
        // request tukar berikutnya tetap berjalan, hanya dengan plugin lain ikut dimuat.
        if ( $this->pastikan_dir( $this->dir_mu ) ) {
            @file_put_contents( $this->dir_mu . 'wpmgr-dorong-aman.php', self::isi_mu_aman( $k['token_hash'] ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
    }

    protected function lepas_pengaman() {
        $m = $this->akar . '.maintenance';
        if ( is_file( $m ) && false !== strpos( (string) @file_get_contents( $m ), self::TANDA_MAINTENANCE ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            @unlink( $m ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
        if ( is_file( $this->dir_mu . 'wpmgr-dorong-aman.php' ) ) {
            @unlink( $this->dir_mu . 'wpmgr-dorong-aman.php' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
    }

    protected function pindah( $dari, $ke ) {
        $this->pastikan_dir( dirname( $ke ) );
        if ( @rename( $dari, $ke ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            return true;
        }
        // Lintas filesystem (wp-content di mount lain): salin lalu hapus.
        if ( is_file( $dari ) && ! is_dir( $ke ) && @copy( $dari, $ke ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
            @unlink( $dari ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
            return true;
        }
        return false;
    }

    protected function jurnal( $id, array $entri ) {
        $berkas = $this->dir( $id ) . 'jurnal.php';
        if ( ! is_file( $berkas ) ) {
            @file_put_contents( $berkas, self::KEPALA ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
        }
        return false !== @file_put_contents( $berkas, json_encode( $entri ) . "\n", FILE_APPEND | LOCK_EX ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
    }

    protected function baca_jurnal( $id ) {
        $data = $this->baca_terlindung( $this->dir( $id ) . 'jurnal.php' );
        $hasil = array();
        foreach ( explode( "\n", false === $data ? '' : $data ) as $baris ) {
            $e = json_decode( $baris, true );
            if ( is_array( $e ) && isset( $e['aksi'] ) ) {
                $hasil[] = $e;
            }
        }
        return $hasil;
    }

    protected function jalankan_op( $id, array $op ) {
        list( $aksi, $rel ) = $op;
        $tujuan = WPMGR_Staging_Path::untuk_ditulis( $this->akar, $rel );
        if ( is_wp_error( $tujuan ) ) {
            return $tujuan;
        }
        $baru = $this->dir( $id ) . 'baru/' . $rel;
        $lama = $this->dir( $id ) . 'lama/' . $rel;
        if ( ! $this->jurnal( $id, array( 'aksi' => $aksi, 'path' => $rel ) ) ) {
            return $this->galat( 'wpmgr_staging_tulis', 'Jurnal dorong tidak dapat ditulis.', 500 );
        }
        if ( file_exists( $tujuan ) && ! is_file( $lama ) ) {
            if ( 'ganti' === $aksi && ! is_file( $baru ) ) {
                return true; // sudah ditukar pada request sebelumnya
            }
            if ( ! $this->pindah( $tujuan, $lama ) ) {
                return $this->galat( 'wpmgr_staging_tukar', 'Berkas produksi tidak dapat dipindahkan: ' . WPMGR_Staging::bersih( $rel, 200 ), 500 );
            }
        }
        if ( 'ganti' === $aksi && is_file( $baru ) && ! $this->pindah( $baru, $tujuan ) ) {
            return $this->galat( 'wpmgr_staging_tukar', 'Berkas baru tidak dapat dipasang: ' . WPMGR_Staging::bersih( $rel, 200 ), 500 );
        }
        return true;
    }

    protected function tukar_db( $id ) {
        $tmp = $this->tabel_dengan_awalan( 'wpmgr_tmp_' );
        if ( empty( $tmp ) ) {
            return $this->galat( 'wpmgr_staging_tukar', 'Tabel hasil impor tidak ditemukan.', 500 );
        }
        $this->hapus_tabel( 'wpmgr_old_' );
        $ada    = array_flip( $this->tabel_dengan_awalan( $this->db->prefix() ) );
        $pasang = array();
        $catat  = array();
        foreach ( $tmp as $t ) {
            $asli = substr( $t, strlen( 'wpmgr_tmp_' ) );
            if ( isset( $ada[ $asli ] ) ) {
                $pasang[] = "`{$asli}` TO `wpmgr_old_{$asli}`";
            }
            $pasang[] = "`{$t}` TO `{$asli}`";
            $catat[]  = array( $asli, isset( $ada[ $asli ] ) ? 1 : 0 );
        }
        if ( ! $this->jurnal( $id, array( 'aksi' => 'db', 'tabel' => $catat ) ) ) {
            return $this->galat( 'wpmgr_staging_tulis', 'Jurnal dorong tidak dapat ditulis.', 500 );
        }
        // Satu pernyataan: MySQL menukar semua nama secara atomik.
        $r = $this->db->kueri( 'RENAME TABLE ' . implode( ', ', $pasang ) );
        return true === $r ? true : $this->galat( 'wpmgr_staging_tukar', 'Penukaran tabel gagal: ' . $r, 500 );
    }

    public function tukar( $id, array $k, $token ) {
        if ( ! is_string( $token ) || 1 !== preg_match( '/^[0-9a-f]{32,64}\z/', $token ) ) {
            return $this->galat( 'wpmgr_staging_permintaan', 'Token tukar tidak sah.', 400 );
        }
        $boleh = empty( $k['sql'] ) ? array( 'siap', 'menukar' ) : array( 'terimpor', 'menukar' );
        if ( ! in_array( $k['status'], $boleh, true ) ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Dorongan belum siap ditukar.', 409 );
        }
        if ( 'menukar' !== $k['status'] ) {
            $k = $this->sentuh( $id, array_merge( $k, array(
                'status' => 'menukar', 'token_hash' => hash( 'sha256', $token ), 'tukar' => 0,
                'maintenance_dibuat' => time(), 'db_ditukar' => false,
            ) ) );
            $this->pasang_pengaman( $k );
        } elseif ( ! hash_equals( (string) $k['token_hash'], hash( 'sha256', $token ) ) ) {
            return $this->galat( 'wpmgr_staging_token', 'Token tukar tidak cocok.', 403 );
        }
        $r = $this->rencana( $id, $k );
        if ( is_wp_error( $r ) ) {
            $this->pulihkan( $id, null, true );
            return $r;
        }
        $ops  = $this->operasi( $r );
        $maju = false;
        while ( $k['tukar'] < count( $ops ) ) {
            if ( $maju && $this->waktu_habis() ) {
                return $this->lagi( $id, $k );
            }
            $hasil = $this->jalankan_op( $id, $ops[ $k['tukar'] ] );
            if ( is_wp_error( $hasil ) ) {
                $this->pulihkan( $id, null, true );
                return $hasil;
            }
            $k['tukar']++;
            $maju = true;
            if ( 0 === $k['tukar'] % 50 ) {
                $k = $this->sentuh( $id, $k );
            }
        }
        if ( ! empty( $k['sql'] ) && empty( $k['db_ditukar'] ) ) {
            $hasil = $this->tukar_db( $id );
            if ( is_wp_error( $hasil ) ) {
                $this->sentuh( $id, $k );
                $this->pulihkan( $id, null, true );
                return $hasil;
            }
            $k['db_ditukar'] = true;
        }
        $this->lepas_pengaman();
        $k['status'] = 'ditukar';
        $this->sentuh( $id, $k );
        return array( 'selesai' => true, 'status' => 'ditukar' );
    }

    protected function balikkan( $id, array $e ) {
        if ( 'db' === $e['aksi'] ) {
            $ada    = array_flip( array_merge(
                $this->tabel_dengan_awalan( $this->db->prefix() ),
                $this->tabel_dengan_awalan( 'wpmgr_old_' ),
                $this->tabel_dengan_awalan( 'wpmgr_tmp_' )
            ) );
            $pasang = array();
            foreach ( (array) $e['tabel'] as $t ) {
                $asli = (string) $t[0];
                if ( 1 !== preg_match( '/^[A-Za-z0-9_$]{1,54}\z/', $asli ) ) {
                    continue;
                }
                if ( (int) $t[1] && isset( $ada[ 'wpmgr_old_' . $asli ] ) && isset( $ada[ $asli ] ) && ! isset( $ada[ 'wpmgr_tmp_' . $asli ] ) ) {
                    $pasang[] = "`{$asli}` TO `wpmgr_tmp_{$asli}`";
                    $pasang[] = "`wpmgr_old_{$asli}` TO `{$asli}`";
                } elseif ( ! (int) $t[1] && isset( $ada[ $asli ] ) && ! isset( $ada[ 'wpmgr_tmp_' . $asli ] ) ) {
                    $pasang[] = "`{$asli}` TO `wpmgr_tmp_{$asli}`";
                }
            }
            if ( empty( $pasang ) ) {
                return true;
            }
            $r = $this->db->kueri( 'RENAME TABLE ' . implode( ', ', $pasang ) );
            return true === $r ? true : 'Tabel tidak dapat dikembalikan: ' . $r;
        }
        $rel = (string) $e['path'];
        if ( ! WPMGR_Staging_Path::boleh_ditulis( $rel ) ) {
            return true;
        }
        $tujuan = $this->akar . $rel;
        $baru   = $this->dir( $id ) . 'baru/' . $rel;
        $lama   = $this->dir( $id ) . 'lama/' . $rel;
        if ( 'ganti' === $e['aksi'] && ! is_file( $baru ) && is_file( $tujuan ) ) {
            if ( ! $this->pindah( $tujuan, $baru ) ) {
                return 'Berkas tidak dapat dikembalikan: ' . WPMGR_Staging::bersih( $rel, 200 );
            }
        }
        if ( is_file( $lama ) && ! file_exists( $tujuan ) && ! $this->pindah( $lama, $tujuan ) ) {
            return 'Berkas tidak dapat dikembalikan: ' . WPMGR_Staging::bersih( $rel, 200 );
        }
        return true;
    }

    public function pulihkan( $id, $token, $paksa ) {
        $k = $this->keadaan( $id );
        if ( null === $k ) {
            return $this->galat( 'wpmgr_staging_tidak_ada', 'Dorongan tidak ditemukan.', 404 );
        }
        if ( ! $paksa && ( empty( $k['token_hash'] ) || ! is_string( $token )
            || ! hash_equals( (string) $k['token_hash'], hash( 'sha256', $token ) ) ) ) {
            return $this->galat( 'wpmgr_staging_token', 'Token pemulihan tidak cocok.', 403 );
        }
        if ( ! in_array( $k['status'], array( 'menukar', 'ditukar' ), true ) ) {
            $this->hapus_tabel( 'wpmgr_tmp_' );
            return array( 'selesai' => true, 'status' => $k['status'] );
        }
        $jurnal  = $this->baca_jurnal( $id );
        $sudah   = isset( $k['pulih'] ) ? (int) $k['pulih'] : 0;
        $masalah = isset( $k['masalah_pulih'] ) ? (array) $k['masalah_pulih'] : array();
        $maju    = false;
        for ( $i = count( $jurnal ) - 1 - $sudah; $i >= 0; $i-- ) {
            if ( ! $paksa && $maju && $this->waktu_habis() ) {
                $k['pulih']         = $sudah;
                $k['masalah_pulih'] = $masalah;
                return $this->lagi( $id, $k );
            }
            $r = $this->balikkan( $id, $jurnal[ $i ] );
            if ( true !== $r && count( $masalah ) < 20 ) {
                $masalah[] = $r;
            }
            $sudah++;
            $maju = true;
        }
        $this->lepas_pengaman();
        $this->hapus_tabel( 'wpmgr_tmp_' );
        $k['status']        = 'dipulihkan';
        $k['pulih']         = $sudah;
        $k['masalah_pulih'] = $masalah;
        $this->sentuh( $id, $k );
        return array( 'selesai' => true, 'status' => 'dipulihkan', 'masalah' => $masalah );
    }

    public function selesai( $id, array $k ) {
        if ( 'ditukar' !== $k['status'] && 'selesai' !== $k['status'] ) {
            return $this->galat( 'wpmgr_staging_urutan', 'Dorongan belum ditukar.', 409 );
        }
        $k['status'] = 'selesai';
        $this->sentuh( $id, $k );
        return array( 'selesai' => true, 'status' => 'selesai' );
    }
```

Di `class-wpmgr-staging.php`, tambahkan `'/staging/terapkan' => array( 'POST', 'terapkan' ),` ke `rute()`, ubah `dorong()` agar memberi direktori mu-plugins:

```php
    public static function dorong() {
        return new WPMGR_Staging_Dorong(
            self::root(),
            rtrim( str_replace( '\\', '/', WP_CONTENT_DIR ), '/' ) . '/wpmgr-dorong/',
            new WPMGR_Staging_Db( $GLOBALS['wpdb'] ),
            self::anggaran_detik(),
            defined( 'WPMU_PLUGIN_DIR' ) ? WPMU_PLUGIN_DIR : WP_CONTENT_DIR . '/mu-plugins'
        );
    }
```

dan callback:

```php
    public static function terapkan( $request ) {
        return rest_ensure_response( self::dorong()->terapkan( self::body_json( $request ) ) );
    }
```

- [ ] **Step 4: PHPUnit 8.3 dan 7.4.** Expected: `OK` di keduanya.

- [ ] **Step 5: Commit.**

```bash
git add connector
git commit -m "feat(connector): terapkan dorong bertahap dengan jurnal, pengaman maintenance, dan pemulihan"
```

---

### Task 9: Mode staging connector dan template mu-plugin `wpmgr-staging.php`

**Files:**
- Create: `connector/wp-manager-connector/templates/wpmgr-staging.php.tpl`, `connector/tests/ModeStagingTest.php`
- Modify: `connector/wp-manager-connector/includes/class-wpmgr-skema.php`, `src/wpmgr/connector_paket.py`, `tests/unit/test_connector_paket.py`

**Interfaces:**
- Consumes: Task 2 (`WPMGR_Staging::mode_staging`, `fitur_aktif`).
- Produces:
  - `WPMGR_Skema::monitoring_mati_dari( $disable, $staging ): bool` (murni). `monitoring_mati()` kini juga benar bila `WPMGR_STAGING` terdefinisi, sehingga connector staging tidak memasang penangkap, pencatat login, dan penghitung traffic, serta hanya mengumumkan `self_update`. SSO tidak berubah.
  - Template `templates/wpmgr-staging.php.tpl` dengan placeholder tunggal `__WPMGR_NAMA__`. Template ikut di zip connector tetapi tidak pernah dipasang oleh connector sendiri.
  - Python `wpmgr.connector_paket`:
    - `NAMA_TEMPLATE_STAGING = "templates/wpmgr-staging.php.tpl"`;
    - `baca_template_staging(sumber: Path | None = None) -> str`;
    - `isi_mu_plugin_staging(nama: str, sumber: Path | None = None) -> str`, yang melempar `ValueError` bila `nama` tidak cocok `[a-z0-9-]{1,40}`.

Isi mu-plugin staging (spec §7.4):
- semua email lewat SMTP `wpmgr-stg-mail:1025` tanpa autentikasi/TLS, dengan header `X-Tags: <nama>` untuk penyaringan di Mailpit (Koreksi #17);
- `blog_public` dipaksa `0`;
- spanduk admin dan simpul admin bar "STAGING";
- penanda perubahan ditulis ke `/wpmgr-log/diubah` (Koreksi #16);
- diam bila `WPMGR_STAGING` tidak terdefinisi, sehingga salinan yang nyasar ke produksi tidak berbuat apa-apa.

- [ ] **Step 1: Tulis test yang gagal.**

File: `connector/tests/ModeStagingTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

final class ModeStagingTest extends TestCase {

    private function template() {
        return file_get_contents( __DIR__ . '/../wp-manager-connector/templates/wpmgr-staging.php.tpl' );
    }

    public function test_monitoring_mati_di_staging(): void {
        $this->assertFalse( WPMGR_Skema::monitoring_mati_dari( false, false ) );
        $this->assertTrue( WPMGR_Skema::monitoring_mati_dari( true, false ) );
        $this->assertTrue( WPMGR_Skema::monitoring_mati_dari( false, true ) );
        // Connector staging: pemantauan mati dan fitur staging tidak diumumkan.
        $this->assertSame( array( 'self_update' ), WPMGR_Skema::fitur( WPMGR_Skema::monitoring_mati_dari( false, true ), false ) );
    }

    public function test_template_punya_semua_pengaman(): void {
        $isi = $this->template();
        $this->assertStringStartsWith( '<?php', $isi );
        $this->assertSame( 1, substr_count( $isi, '__WPMGR_NAMA__' ) );
        foreach ( array( "defined( 'WPMGR_STAGING' )", "'wpmgr-stg-mail'", '1025', 'X-Tags',
                         'phpmailer_init', 'pre_option_blog_public', 'admin_notices', 'STAGING',
                         'kredensial produksi', '/wpmgr-log/diubah', 'upgrader_process_complete', 'save_post' ) as $harus ) {
            $this->assertStringContainsString( $harus, $isi, $harus );
        }
    }

    public function test_template_valid_php_setelah_diisi(): void {
        $isi    = str_replace( '__WPMGR_NAMA__', 'contoh-id', $this->template() );
        $berkas = sys_get_temp_dir() . '/wpmgr-tpl-' . getmypid() . '.php';
        file_put_contents( $berkas, $isi );
        exec( escapeshellarg( PHP_BINARY ) . ' -l ' . escapeshellarg( $berkas ) . ' 2>&1', $keluar, $kode );
        unlink( $berkas );
        $this->assertSame( 0, $kode, implode( "\n", $keluar ) );
    }
}
```

Tambahkan ke akhir `tests/unit/test_connector_paket.py`:

```python
def test_template_staging_ikut_zip(tmp_path):
    from wpmgr.connector_paket import NAMA_TEMPLATE_STAGING, sumber_bawaan

    tujuan = tmp_path / "keluar"
    bangun_paket(sumber_bawaan(), tujuan)
    with zipfile.ZipFile(tujuan / NAMA_ZIP) as z:
        assert f"wp-manager-connector/{NAMA_TEMPLATE_STAGING}" in z.namelist()


def test_isi_mu_plugin_staging_mengisi_nama():
    from wpmgr.connector_paket import isi_mu_plugin_staging

    isi = isi_mu_plugin_staging("toko-contoh")
    assert "define( 'WPMGR_STAGING_NAMA', 'toko-contoh' );" in isi
    assert "__WPMGR_NAMA__" not in isi


@pytest.mark.parametrize("nama", ["", "Toko", "a'b", "a" * 41, "toko\n", "../x"])
def test_isi_mu_plugin_staging_menolak_nama_tidak_sah(nama):
    from wpmgr.connector_paket import isi_mu_plugin_staging

    with pytest.raises(ValueError):
        isi_mu_plugin_staging(nama)
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `cd connector && vendor/bin/phpunit --filter ModeStagingTest` dan `.venv/Scripts/python -m pytest tests/unit/test_connector_paket.py -q`. Expected: PHP `Call to undefined method WPMGR_Skema::monitoring_mati_dari()`; Python `ImportError: cannot import name 'NAMA_TEMPLATE_STAGING'`.

- [ ] **Step 3: Mode staging di skema.** Di `includes/class-wpmgr-skema.php`, ganti `monitoring_mati()`:

```php
    public static function monitoring_mati_dari( $disable, $staging ) {
        return (bool) $disable || (bool) $staging;
    }

    /**
     * Connector di salinan staging (WPMGR_STAGING, spec §7.4) tidak
     * memantau apa pun: datanya adalah salinan produksi, dan kejadian di
     * staging bukan kejadian di site client.
     */
    public static function monitoring_mati() {
        return self::monitoring_mati_dari(
            defined( 'WPMGR_DISABLE_MONITORING' ) && WPMGR_DISABLE_MONITORING,
            defined( 'WPMGR_STAGING' ) && WPMGR_STAGING
        );
    }
```

- [ ] **Step 4: Template mu-plugin.**

File: `connector/wp-manager-connector/templates/wpmgr-staging.php.tpl`
```php
<?php
/**
 * Plugin Name: WP Manager — pengaman staging
 * Description: Dipasang dashboard WP Manager di salinan staging. Email ditangkap, mesin pencari ditolak, dan perubahan dicatat. Tidak berbuat apa-apa di luar staging.
 */
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}
// Bila berkas ini ikut tersalin ke produksi, tanpa konstanta dari
// wp-config.php staging ia diam sepenuhnya.
if ( ! defined( 'WPMGR_STAGING' ) || ! WPMGR_STAGING ) {
    return;
}
define( 'WPMGR_STAGING_NAMA', '__WPMGR_NAMA__' );

add_action( 'phpmailer_init', function ( $phpmailer ) {
    // Prioritas terakhir: plugin SMTP produksi (WP Mail SMTP dan sejenisnya)
    // mengatur PHPMailer lebih dulu, lalu ditimpa ke Mailpit di sini.
    $phpmailer->isSMTP();
    $phpmailer->Host        = 'wpmgr-stg-mail';
    $phpmailer->Port        = 1025;
    $phpmailer->SMTPAuth    = false;
    $phpmailer->SMTPSecure  = '';
    $phpmailer->SMTPAutoTLS = false;
    $phpmailer->Username    = '';
    $phpmailer->Password    = '';
    $phpmailer->addCustomHeader( 'X-Tags', WPMGR_STAGING_NAMA );
}, PHP_INT_MAX );

add_filter( 'pre_option_blog_public', function () {
    return '0';
} );

add_action( 'admin_notices', function () {
    echo '<div class="notice notice-warning"><p><strong>STAGING</strong> — payment gateway dan API pihak ketiga '
        . 'memakai kredensial produksi. Email ditangkap dashboard dan tidak dikirim, kecuali plugin yang '
        . 'mengirim lewat API HTTP penyedia email.</p></div>';
} );

add_action( 'admin_bar_menu', function ( $bar ) {
    $bar->add_node( array( 'id' => 'wpmgr-staging', 'title' => 'STAGING' ) );
}, 1 );

// Penanda "staging diubah sejak tarik terakhir" untuk dashboard (spec §8.1
// langkah 2). /wpmgr-log adalah bind mount milik dashboard di container staging.
$wpmgr_tandai_diubah = function () {
    @file_put_contents( '/wpmgr-log/diubah', (string) time() ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
};
foreach ( array( 'save_post', 'deleted_post', 'activated_plugin', 'deactivated_plugin', 'upgrader_process_complete',
                 'switch_theme', 'customize_save_after', 'wp_update_nav_menu', 'add_attachment', 'edit_attachment' ) as $wpmgr_kait ) {
    add_action( $wpmgr_kait, $wpmgr_tandai_diubah );
}
unset( $wpmgr_kait );
```

- [ ] **Step 5: Pembaca template di dashboard.** Di `src/wpmgr/connector_paket.py`, tambahkan setelah `NAMA_MANIFEST`:

```python
NAMA_TEMPLATE_STAGING = "templates/wpmgr-staging.php.tpl"
_PLACEHOLDER_NAMA = "__WPMGR_NAMA__"
# Sama dengan wpmgr.staging.aman.POLA_NAMA; diulang di sini supaya modul
# paket connector tidak bergantung pada paket staging.
_POLA_NAMA_STAGING = re.compile(r"[a-z0-9-]{1,40}")
```

dan fungsi di akhir berkas:

```python
def baca_template_staging(sumber: Path | None = None) -> str:
    return ((sumber or sumber_bawaan()) / NAMA_TEMPLATE_STAGING).read_text(encoding="utf-8")


def isi_mu_plugin_staging(nama: str, sumber: Path | None = None) -> str:
    """Isi wp-content/mu-plugins/wpmgr-staging.php untuk satu staging.

    Nama disisipkan ke literal PHP berkutip tunggal; hanya bentuk nama
    staging yang sah yang boleh lewat, supaya tidak ada kutip atau baris
    baru yang bisa keluar dari literal itu.
    """
    if not isinstance(nama, str) or not _POLA_NAMA_STAGING.fullmatch(nama):
        raise ValueError("Nama staging tidak sah")
    return baca_template_staging(sumber).replace(_PLACEHOLDER_NAMA, nama)
```

- [ ] **Step 6: Jalankan test.** Run: PHPUnit 8.3 dan 7.4, lalu `.venv/Scripts/python -m pytest tests/unit/test_connector_paket.py -q`. Expected: semua lulus.

- [ ] **Step 7: Commit.**

```bash
git add connector src/wpmgr/connector_paket.py tests/unit/test_connector_paket.py
git commit -m "feat(connector): mode staging dan template mu-plugin pengaman staging"
```

---

## Fase C — Runtime dan pustaka dashboard

### Task 10: Skrip pembantu `wpmgr-staging` dan test bats

**Files:**
- Create:
  - skrip dan konfigurasi: `deploy/staging/wpmgr-staging`, `deploy/staging/staging.conf.contoh`, `.gitattributes`;
  - test bats: `deploy/staging/tests/pembantu.bats`;
  - pengganti perintah untuk test: `deploy/staging/tests/palsu/docker`, `palsu/setpriv`, `palsu/certbot`, `palsu/curl`, `palsu/df`, `palsu/iptables`.

**Interfaces:**
- Produces: `/usr/local/sbin/wpmgr-staging <subperintah> [argumen]` (root, lewat `sudo -n`). Galat dicetak ke stderr sebagai satu baris `GALAT <kode>: <pesan>` dengan kode keluar tetap:

| Kode | Keluar | Arti |
|---|---|---|
| `argumen` | 2 | argumen tidak sah |
| `ditolak` | 3 | kondisi menolak (container bukan milik staging, belum `siapkan`, dst.) |
| `docker` | 4 | perintah Docker gagal |
| `sertifikat` | 5 | certbot gagal |
| `impor` | 6 | impor database gagal |
| `konfigurasi` | 7 | `staging.conf` hilang atau tidak sah |
| `wpcli` | 8 | wp-cli gagal |

- Subperintah:
  - `siapkan`: membuat direktori, `digest.lock`, kata sandi root MariaDB, jaringan `wpmgr-staging` (bridge `br-wpmgrstg`, subnet tetap), aturan iptables isolasi, wp-cli, router bawaan, lalu container `wpmgr-stg-db`, `wpmgr-stg-mail`, `wpmgr-stg-router`.
  - `buat <nama> <versi_php> <site_id>`: menjalankan `wp-<nama>` dengan `--memory 384m --memory-swap 384m --cpus 1 --pids-limit 256`, jaringan `wpmgr-staging`, dan bind mount `<site_id>/files`, `/ekspor`, `/log` serta wp-cli. Apache berjalan sebagai UID pemilik direktori staging.
  - `jalan <nama>`, `jeda <nama>`, `hapus <nama>`.
  - `db-buat <nama> <site_id> <prefix>`: membuat database, user, dan kata sandi acak, lalu menulis `wp-config.php` staging sebagai user dashboard.
  - `db-hapus <nama>`.
  - `db-impor <nama>`: SQL dibaca dari stdin; database dibuat ulang oleh root lalu diisi oleh user staging itu.
  - `wpcli <nama> <perintah>`, dengan daftar putih: `search-replace <url> <url> [--export]`, `option update blog_public 0|1`, `plugin|theme update <slug> [--version=X]`, `core update [--version=X]`, `plugin list`, `core version`, `cache flush`.
  - `router-muat`: membaca `<STAGING_DIR>/router/<nama>.{rahasia,htpasswd}`, merender konfigurasi dari template tetap, menjalankan `nginx -t` (gagal berarti konfigurasi lama dipulihkan), lalu reload.
  - `sertifikat <nama>`: HTTP-01 webroot, dengan hasil disalin ke `CERT_DIR/<host>/`.
  - `status`: JSON `{"mem_tersedia", "disk_total", "disk_bebas", "container": {"wp-<nama>": {"berjalan": bool}}, "akses": {"<nama>": epoch}}`.
- Konfigurasi `/etc/wpmgr-staging/staging.conf` (root, `KUNCI=nilai`, kunci dari daftar tetap): `DOMAIN`, `STAGING_DIR`, `KONF_DIR`, `CERT_DIR`, `ACME_DIR`, `LE_DIR`, `LOG_DIR`, `ACME_EMAIL`, `ROUTER_PORT`, `MAIL_PORT`, `SUBNET`, `PENGGUNA_UID`/`PENGGUNA_GID` (opsional; default pemilik `STAGING_DIR`), `AKAR_LOKAL`/`AKAR_DAEMON` (e2e), `TANPA_IPTABLES`, `TANPA_SERTIFIKAT`, `MEMINFO`. `WPMGR_STG_KONF` dan `WPMGR_STG_PATH` hanya untuk test. `sudo` membuang keduanya karena `env_reset`.
- Pinning image: `<KONF_DIR>/digest.lock` berisi `kunci=image@sha256:…` (`php74`…`php83`, `mariadb`, `nginx`, `mailpit`) dan `wpcli=<sha512>`. Entri yang belum ada dibuat pada pemakaian pertama (trust-on-first-use), sesudahnya selalu dipakai apa adanya. Untuk memperbarui, operator menghapus barisnya lalu menjalankan `siapkan` atau `buat`.
- Label container `wpmgr.staging=situs:<nama>` untuk staging dan `wpmgr.staging=layanan:<peran>` untuk layanan bersama. Tidak ada `stop`/`rm`/`exec` terhadap container yang labelnya tidak cocok (RF5).

- [ ] **Step 1: Atribut baris.** Skrip bash dengan CRLF gagal di Linux dengan `$'\r': command not found`.

File: `.gitattributes`
```
deploy/staging/wpmgr-staging text eol=lf
deploy/staging/tests/*.bats text eol=lf
deploy/staging/tests/palsu/* text eol=lf
deploy/staging/*.conf text eol=lf
deploy/staging/*.contoh text eol=lf
```

- [ ] **Step 2: Pengganti perintah untuk test.** Setiap pengganti mencatat argumen sebagai `[arg1][arg2]...` per baris, bentuk yang tidak ambigu dan tidak bergantung versi bash.

File: `deploy/staging/tests/palsu/docker`
```bash
#!/usr/bin/env bash
# docker tiruan untuk test skrip pembantu: mencatat panggilan dan menjawab
# dari berkas di $PALSU (wadah/<nama> = label, wadah/<nama>.image = image).
printf '[%s]' "$@" >> "$PALSU/docker.log"
printf '\n' >> "$PALSU/docker.log"
case "${1-}" in
  inspect)
    nama="${*: -1}"
    [[ -f "$PALSU/wadah/$nama" ]] || exit 1
    if [[ "${3-}" == *Labels* ]]; then
      cat "$PALSU/wadah/$nama"
    else
      cat "$PALSU/wadah/$nama.image" 2>/dev/null || true
    fi
    ;;
  network)
    if [[ "${2-}" == inspect ]]; then
      [[ -f "$PALSU/jaringan" ]] || exit 1
    else
      touch "$PALSU/jaringan"
    fi
    ;;
  image)
    printf 'contoh/image@sha256:%s\n' "$(printf 'c%.0s' $(seq 1 64))"
    ;;
  exec)
    if [[ " $* " == *" -i "* ]]; then
      n=$(( $(cat "$PALSU/n" 2>/dev/null || echo 0) + 1 ))
      echo "$n" > "$PALSU/n"
      cat > "$PALSU/stdin-$n"
    fi
    if [[ "$*" == *"nginx -t"* && -f "$PALSU/nginx-gagal" ]]; then
      exit 1
    fi
    if [[ -f "$PALSU/exec-gagal" ]]; then
      exit 1
    fi
    ;;
  ps)
    cat "$PALSU/ps" 2>/dev/null || true
    ;;
esac
exit 0
```

File: `deploy/staging/tests/palsu/setpriv`
```bash
#!/usr/bin/env bash
printf '[%s]' "$@" >> "$PALSU/setpriv.log"
printf '\n' >> "$PALSU/setpriv.log"
while [[ $# -gt 0 && "$1" != "--" ]]; do shift; done
shift
exec "$@"
```

File: `deploy/staging/tests/palsu/certbot`
```bash
#!/usr/bin/env bash
printf '[%s]' "$@" >> "$PALSU/certbot.log"
printf '\n' >> "$PALSU/certbot.log"
[[ -f "$PALSU/certbot-gagal" ]] && exit 1
konfig="" nama=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config-dir) konfig="$2"; shift ;;
    --cert-name) nama="$2"; shift ;;
  esac
  shift
done
mkdir -p "$konfig/live/$nama"
echo "rantai" > "$konfig/live/$nama/fullchain.pem"
echo "kunci" > "$konfig/live/$nama/privkey.pem"
```

File: `deploy/staging/tests/palsu/curl`
```bash
#!/usr/bin/env bash
printf '[%s]' "$@" >> "$PALSU/curl.log"
printf '\n' >> "$PALSU/curl.log"
keluar="" url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) keluar="$2"; shift ;;
    http*) url="$1" ;;
  esac
  shift
done
if [[ "$url" == *.sha512 ]]; then
  printf 'phar-palsu' | sha512sum | cut -d' ' -f1
else
  printf 'phar-palsu' > "$keluar"
fi
```

File: `deploy/staging/tests/palsu/df`
```bash
#!/usr/bin/env bash
echo "     1B-blocks        Avail"
echo "  200000000000  60000000000"
```

File: `deploy/staging/tests/palsu/iptables`
```bash
#!/usr/bin/env bash
printf '[%s]' "$@" >> "$PALSU/iptables.log"
printf '\n' >> "$PALSU/iptables.log"
[[ "${1-}" == "-C" ]] && exit 1
exit 0
```

- [ ] **Step 3: Tulis test bats yang gagal.**

File: `deploy/staging/tests/pembantu.bats`
```bash
#!/usr/bin/env bats
# Test skrip pembantu dengan docker tiruan. Jalankan:
#   MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" -w /code bats/bats:1.11.0 deploy/staging/tests

setup() {
  SKRIP="$BATS_TEST_DIRNAME/../wpmgr-staging"
  export PALSU="$BATS_TEST_TMPDIR/palsu"
  mkdir -p "$PALSU/wadah"
  export WPMGR_STG_PATH="$BATS_TEST_DIRNAME/palsu:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  export WPMGR_STG_KONF="$BATS_TEST_TMPDIR/staging.conf"
  S="$BATS_TEST_TMPDIR/srv"
  mkdir -p "$S/staging/router" "$S/etc/router/conf.d" "$S/etc/router/htpasswd" "$S/etc/db" "$S/log"
  cat > "$WPMGR_STG_KONF" <<KONF
DOMAIN=staging.contoh.id
STAGING_DIR=$S/staging
KONF_DIR=$S/etc
CERT_DIR=$S/certs
ACME_DIR=$S/acme
LE_DIR=$S/le
LOG_DIR=$S/log
ACME_EMAIL=admin@contoh.id
ROUTER_PORT=127.0.0.1:8090
MAIL_PORT=127.0.0.1:8025
SUBNET=172.31.250.0/24
PENGGUNA_UID=1000
PENGGUNA_GID=1000
MEMINFO=$S/meminfo
TANPA_IPTABLES=1
KONF
  printf 'MemTotal:       11000000 kB\nMemAvailable:    4194304 kB\n' > "$S/meminfo"
  printf 'rootrahasia' > "$S/etc/db-root"
  ID=0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0
  D64="$(printf 'b%.0s' $(seq 1 64))"
  printf 'php81=wordpress@sha256:%s\n' "$D64" > "$S/etc/digest.lock"
  printf 'phar' > "$S/etc/wp-cli.phar"
  touch "$PALSU/jaringan"
}

docker_log() {
  cat "$PALSU/docker.log" 2>/dev/null || true
}

@test "nama staging tidak sah ditolak sebelum docker dipanggil" {
  for nama in "Toko" "../x" "a b" "a;id" "$(printf 'a\nb')" "" "$(printf 'a%.0s' $(seq 1 41))" 'a$(id)'; do
    run "$SKRIP" jalan "$nama"
    [ "$status" -eq 2 ]
    [[ "$output" == *"GALAT argumen"* ]]
  done
  [ -z "$(docker_log)" ]
}

@test "subperintah tak dikenal dan jumlah argumen salah ditolak" {
  run "$SKRIP" shell
  [ "$status" -eq 2 ]
  run "$SKRIP" buat toko 8.1
  [ "$status" -eq 2 ]
  run "$SKRIP" status tambahan
  [ "$status" -eq 2 ]
  [ -z "$(docker_log)" ]
}

@test "versi PHP dan id site tidak sah ditolak" {
  run "$SKRIP" buat toko 9.9 "$ID"
  [ "$status" -eq 2 ]
  run "$SKRIP" buat toko '8.1;id' "$ID"
  [ "$status" -eq 2 ]
  run "$SKRIP" buat toko 8.1 "../$ID"
  [ "$status" -eq 2 ]
  [ -z "$(docker_log)" ]
}

@test "konfigurasi tidak ada atau berisi kunci asing ditolak" {
  run env WPMGR_STG_KONF="$BATS_TEST_TMPDIR/tidak-ada" "$SKRIP" status
  [ "$status" -eq 7 ]
  echo 'PATH=/tmp' >> "$WPMGR_STG_KONF"
  run "$SKRIP" status
  [ "$status" -eq 7 ]
}

@test "buat menjalankan container dengan batas sumber daya dan mount yang tepat" {
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 0 ]
  diharapkan="[run][-d][--name][wp-toko][--label][wpmgr.staging=situs:toko][--network][wpmgr-staging][--restart][unless-stopped][--memory][384m][--memory-swap][384m][--cpus][1][--pids-limit][256][--security-opt][no-new-privileges][-e][APACHE_RUN_USER=#1000][-e][APACHE_RUN_GROUP=#1000][-v][$S/staging/$ID/files:/var/www/html][-v][$S/staging/$ID/ekspor:/wpmgr-ekspor][-v][$S/staging/$ID/log:/wpmgr-log][-v][$S/etc/wp-cli.phar:/usr/local/bin/wp:ro][wordpress@sha256:$D64]"
  grep -qxF "$diharapkan" "$PALSU/docker.log"
  [ -d "$S/staging/$ID/files" ]
}

@test "buat menolak container bernama sama yang bukan milik staging" {
  printf 'situs:lain' > "$PALSU/wadah/wp-toko"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 3 ]
  [[ "$output" == *"bukan milik staging"* ]]
  ! grep -q '^\[run\]' "$PALSU/docker.log"
  ! grep -q '^\[rm\]' "$PALSU/docker.log"
}

@test "buat hanya menjalankan ulang container milik sendiri dengan image yang sama" {
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  printf 'wordpress@sha256:%s' "$D64" > "$PALSU/wadah/wp-toko.image"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 0 ]
  grep -qxF "[start][wp-toko]" "$PALSU/docker.log"
  ! grep -q '^\[run\]' "$PALSU/docker.log"
}

@test "jalan dan jeda menolak container asing" {
  printf 'layanan:db' > "$PALSU/wadah/wp-toko"
  run "$SKRIP" jeda toko
  [ "$status" -eq 3 ]
  ! grep -q '^\[stop\]' "$PALSU/docker.log"
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  run "$SKRIP" jeda toko
  [ "$status" -eq 0 ]
  grep -qxF "[stop][-t][20][wp-toko]" "$PALSU/docker.log"
}

@test "wpcli menolak perintah di luar daftar putih" {
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  for args in "eval phpinfo();" "db export" "plugin install x" "option update siteurl http://x" \
              "option update blog_public 2" "config set x y"; do
    read -r -a a <<< "$args"
    run "$SKRIP" wpcli toko "${a[@]}"
    [ "$status" -eq 2 ]
  done
  run "$SKRIP" wpcli toko plugin update --exec=id
  [ "$status" -eq 2 ]
  run "$SKRIP" wpcli toko plugin update akismet '--version=1.0;id'
  [ "$status" -eq 2 ]
  run "$SKRIP" wpcli toko search-replace 'https://a.id' "https://b.id'; DROP"
  [ "$status" -eq 2 ]
  run "$SKRIP" wpcli toko search-replace 'https://a.id' 'https://b.id' --network
  [ "$status" -eq 2 ]
  ! grep -q '^\[exec\]' "$PALSU/docker.log"
}

@test "wpcli menjalankan perintah yang diizinkan persis" {
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  run "$SKRIP" wpcli toko search-replace 'https://toko.id' 'https://toko.staging.contoh.id' --export
  [ "$status" -eq 0 ]
  grep -qxF "[exec][-u][1000:1000][wp-toko][php][-d][memory_limit=512M][/usr/local/bin/wp][--path=/var/www/html][search-replace][https://toko.id][https://toko.staging.contoh.id][--all-tables-with-prefix][--skip-columns=guid][--precise][--skip-plugins][--skip-themes][--export=/wpmgr-ekspor/dorong.sql]" "$PALSU/docker.log"
  run "$SKRIP" wpcli toko plugin update akismet --version=5.3.1
  [ "$status" -eq 0 ]
  grep -qxF "[exec][-u][1000:1000][wp-toko][php][-d][memory_limit=512M][/usr/local/bin/wp][--path=/var/www/html][plugin][update][akismet][--version=5.3.1]" "$PALSU/docker.log"
  run "$SKRIP" wpcli toko option update blog_public 0
  [ "$status" -eq 0 ]
}

@test "wpcli gagal dilaporkan dengan kode 8" {
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  touch "$PALSU/exec-gagal"
  run "$SKRIP" wpcli toko core version
  [ "$status" -eq 8 ]
  [[ "$output" == *"GALAT wpcli"* ]]
}

@test "db-buat menulis wp-config staging sebagai user dashboard dengan hak DB terbatas" {
  mkdir -p "$S/staging/$ID/files"
  run "$SKRIP" db-buat toko-a "$ID" wp_
  [ "$status" -eq 0 ]
  cfg="$S/staging/$ID/files/wp-config.php"
  grep -qF "define( 'DB_NAME', 'stg_toko_a' );" "$cfg"
  grep -qF "define( 'DB_HOST', 'wpmgr-stg-db' );" "$cfg"
  grep -qF "\$table_prefix = 'wp_';" "$cfg"
  grep -qF "define( 'WP_HOME', 'https://toko-a.staging.contoh.id' );" "$cfg"
  grep -qF "define( 'WPMGR_STAGING', true );" "$cfg"
  grep -qF "define( 'WPMGR_DISABLE_MONITORING', true );" "$cfg"
  grep -qF "define( 'DISABLE_WP_CRON', true );" "$cfg"
  grep -qF "'/wpmgr-log/php-error.log'" "$cfg"
  grep -qF "[--reuid=1000][--regid=1000][--clear-groups][--][tee][$cfg]" "$PALSU/setpriv.log"
  sql="$(cat "$PALSU"/stdin-2)"
  [[ "$sql" == *"GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, LOCK TABLES, CREATE TEMPORARY TABLES, REFERENCES, CREATE VIEW, SHOW VIEW ON \`stg_toko_a\`.*"* ]]
  [[ "$sql" != *"GRANT ALL"* ]]
  pw="$(cat "$S/etc/db/toko-a")"
  [[ "$pw" =~ ^[0-9a-f]{48}$ ]]
  grep -qF "define( 'DB_PASSWORD', '$pw' );" "$cfg"
  run "$SKRIP" db-buat toko "$ID" "wp_'; x"
  [ "$status" -eq 2 ]
}

@test "db-impor membuat ulang database sebagai root lalu mengimpor sebagai user staging" {
  printf 'layanan:db' > "$PALSU/wadah/wpmgr-stg-db"
  printf 'sandi-user' > "$S/etc/db/toko"
  run bash -c "printf 'INSERT INTO t VALUES (1);' | '$SKRIP' db-impor toko"
  [ "$status" -eq 0 ]
  [[ "$(cat "$PALSU/stdin-1")" == *"password=rootrahasia"* ]]
  [[ "$(cat "$PALSU/stdin-2")" == *'DROP DATABASE IF EXISTS `stg_toko`; CREATE DATABASE `stg_toko`'* ]]
  [[ "$(cat "$PALSU/stdin-3")" == *"user=stg_toko"* && "$(cat "$PALSU/stdin-3")" == *"password=sandi-user"* ]]
  [ "$(cat "$PALSU/stdin-4")" = "INSERT INTO t VALUES (1);" ]
  grep -q '^\[exec\]\[-i\]\[wpmgr-stg-db\]\[mariadb\]\[--defaults-extra-file=/run/wpmgr-klien-[0-9-]*\.cnf\]\[--binary-mode\]\[--max-allowed-packet=64M\]\[stg_toko\]$' "$PALSU/docker.log"
  # Kata sandi tidak pernah muncul di argumen proses.
  ! grep -q 'sandi-user\|rootrahasia' "$PALSU/docker.log"
}

@test "db-impor ditolak bila db-buat belum dijalankan" {
  run "$SKRIP" db-impor toko
  [ "$status" -eq 3 ]
}

@test "router-muat merender template tetap dan menolak htpasswd berbahaya" {
  rahasia="$(printf 'e%.0s' $(seq 1 64))"
  hash='$2b$10$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ01234'
  printf '%s' "$rahasia" > "$S/staging/router/toko.rahasia"
  printf 'staging:%s\n' "$hash" > "$S/staging/router/toko.htpasswd"
  run "$SKRIP" router-muat
  [ "$status" -eq 0 ]
  konf="$S/etc/router/conf.d/stg-toko.conf"
  grep -qF 'server_name toko.staging.contoh.id;' "$konf"
  grep -qF "secure_link_md5 \"\$secure_link_expires\$host $rahasia\";" "$konf"
  grep -qF 'add_header X-Robots-Tag "noindex, nofollow" always;' "$konf"
  grep -qF 'auth_basic_user_file /etc/nginx/wpmgr-htpasswd/toko;' "$konf"
  grep -qF 'set $wpmgr_hulu wp-toko;' "$konf"
  grep -qxF "staging:$hash" "$S/etc/router/htpasswd/toko"
  grep -qxF "[exec][wpmgr-stg-router][nginx][-t]" "$PALSU/docker.log"
  grep -qxF "[exec][wpmgr-stg-router][nginx][-s][reload]" "$PALSU/docker.log"

  printf 'staging:%s\n}\nserver { listen 81; }\n' "$hash" > "$S/staging/router/toko.htpasswd"
  run "$SKRIP" router-muat
  [ "$status" -eq 2 ]
  printf 'staging:%s\n' "$hash" > "$S/staging/router/toko.htpasswd"
  printf 'bukan-hex' > "$S/staging/router/toko.rahasia"
  run "$SKRIP" router-muat
  [ "$status" -eq 2 ]
}

@test "router-muat memulihkan konfigurasi lama bila nginx -t gagal" {
  printf 'lama' > "$S/etc/router/conf.d/stg-lama.conf"
  printf '%s' "$(printf 'e%.0s' $(seq 1 64))" > "$S/staging/router/toko.rahasia"
  printf 'staging:%s\n' '$2b$10$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ01234' > "$S/staging/router/toko.htpasswd"
  touch "$PALSU/nginx-gagal"
  run "$SKRIP" router-muat
  [ "$status" -eq 4 ]
  [ "$(cat "$S/etc/router/conf.d/stg-lama.conf")" = "lama" ]
  [ ! -e "$S/etc/router/conf.d/stg-toko.conf" ]
  ! grep -q 'reload' "$PALSU/docker.log"
}

@test "sertifikat memakai webroot dan direktori milik wpmgr lalu menyalin hasilnya" {
  run "$SKRIP" sertifikat toko
  [ "$status" -eq 0 ]
  grep -qxF "[certonly][--non-interactive][--agree-tos][-m][admin@contoh.id][--webroot][-w][$S/acme][-d][toko.staging.contoh.id][--cert-name][toko.staging.contoh.id][--config-dir][$S/le/config][--work-dir][$S/le/work][--logs-dir][$S/le/logs][--keep-until-expiring]" "$PALSU/certbot.log"
  [ "$(cat "$S/certs/toko.staging.contoh.id/fullchain.pem")" = "rantai" ]
  touch "$PALSU/certbot-gagal"
  run "$SKRIP" sertifikat toko
  [ "$status" -eq 5 ]
}

@test "status mencetak JSON dengan memori, disk, container, dan akses" {
  printf 'wp-toko|running\nwp-lain|exited\nwpmgr-stg-db|running\nwp-JAHAT|running\n' > "$PALSU/ps"
  touch -d @1790000000 "$S/log/toko.log"
  run "$SKRIP" status
  [ "$status" -eq 0 ]
  [ "$output" = '{"mem_tersedia":4294967296,"disk_total":200000000000,"disk_bebas":60000000000,"container":{"wp-toko":{"berjalan":true},"wp-lain":{"berjalan":false}},"akses":{"toko":1790000000}}' ]
}

@test "hapus hanya menyentuh container milik staging" {
  printf 'layanan:router' > "$PALSU/wadah/wp-toko"
  run "$SKRIP" hapus toko
  [ "$status" -eq 3 ]
  ! grep -q '^\[rm\]' "$PALSU/docker.log"
}

@test "siapkan idempoten dan memasang aturan isolasi" {
  sed -i '/^TANPA_IPTABLES=/d' "$WPMGR_STG_KONF"
  rm -f "$PALSU/jaringan" "$S/etc/wp-cli.phar" "$S/etc/db-root"
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\nmailpit=p@sha256:%s\n' "$D64" "$D64" "$D64" >> "$S/etc/digest.lock"
  run "$SKRIP" siapkan
  [ "$status" -eq 0 ]
  grep -qxF "[network][create][--driver][bridge][--subnet][172.31.250.0/24][--opt][com.docker.network.bridge.name=br-wpmgrstg][--label][wpmgr.staging=layanan:jaringan][wpmgr-staging]" "$PALSU/docker.log"
  grep -qxF "[-I][INPUT][-i][br-wpmgrstg][-j][DROP]" "$PALSU/iptables.log"
  grep -qxF "[-I][DOCKER-USER][-i][br-wpmgrstg][!][-o][br-wpmgrstg][-d][172.16.0.0/12][-j][DROP]" "$PALSU/iptables.log"
  [[ "$(cat "$S/etc/db-root")" =~ ^[0-9a-f]{64}$ ]]
  grep -q '^wpcli=[0-9a-f]\{128\}$' "$S/etc/digest.lock"
  grep -q '^\[run\]\[-d\]\[--name\]\[wpmgr-stg-router\]' "$PALSU/docker.log"
  grep -q '\[-p\]\[127.0.0.1:8090:80\]' "$PALSU/docker.log"
  ! grep -q 'MARIADB_ROOT_PASSWORD=' "$PALSU/docker.log"
  grep -qF 'return 444;' "$S/etc/router/conf.d/00-bawaan.conf"
}

@test "digest.lock yang sudah ada dipakai tanpa unduh ulang" {
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 0 ]
  ! grep -q '^\[pull\]' "$PALSU/docker.log"
}

@test "AKAR_DAEMON menerjemahkan path bind mount untuk Docker Desktop" {
  printf 'AKAR_LOKAL=%s\nAKAR_DAEMON=/run/desktop/mnt/host/d/repo/var\n' "$S" >> "$WPMGR_STG_KONF"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 0 ]
  grep -qF "[-v][/run/desktop/mnt/host/d/repo/var/staging/$ID/files:/var/www/html]" "$PALSU/docker.log"
}
```

- [ ] **Step 4: Jalankan dan pastikan gagal.** Run: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" -w /code bats/bats:1.11.0 deploy/staging/tests`. Expected: semua test gagal dengan `No such file or directory` untuk `deploy/staging/wpmgr-staging`.

- [ ] **Step 5: Tulis skrip pembantu.**

File: `deploy/staging/wpmgr-staging`
```bash
#!/usr/bin/env bash
# wpmgr-staging -- satu-satunya jembatan dashboard WP Manager ke Docker
# (spec §7.3). Dijalankan root lewat `sudo -n` oleh user dashboard.
#
# Aturan skrip ini:
#   - setiap argumen divalidasi dengan pola tetap sebelum dipakai;
#   - perintah Docker selalu disusun sebagai array, tidak pernah lewat eval
#     atau string yang diurai ulang oleh shell;
#   - berkas di direktori milik user dashboard dibaca/ditulis SEBAGAI user
#     itu (setpriv), karena ia bisa mengganti apa pun di sana dengan symlink;
#   - container yang labelnya bukan milik staging tidak pernah disentuh.
set -euo pipefail
umask 077
export LC_ALL=C
PATH="${WPMGR_STG_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
KONF="${WPMGR_STG_KONF:-/etc/wpmgr-staging/staging.conf}"

VERSI_PHP=(7.4 8.0 8.1 8.2 8.3)
WPCLI_VERSI="2.11.0"
JARINGAN="wpmgr-staging"
JEMBATAN="br-wpmgrstg"
OPSI_KLIEN=""

galat() {
  local kode="$1"
  shift
  printf 'GALAT %s: %s\n' "$kode" "$*" >&2
  case "$kode" in
    argumen) exit 2 ;;
    ditolak) exit 3 ;;
    docker) exit 4 ;;
    sertifikat) exit 5 ;;
    impor) exit 6 ;;
    konfigurasi) exit 7 ;;
    wpcli) exit 8 ;;
    *) exit 1 ;;
  esac
}

# ---- konfigurasi ---------------------------------------------------------

muat_konf() {
  [[ -r "$KONF" ]] || galat konfigurasi "berkas konfigurasi tidak ada"
  local baris kunci nilai
  while IFS= read -r baris || [[ -n "$baris" ]]; do
    [[ -z "$baris" || "$baris" == \#* ]] && continue
    [[ "$baris" =~ ^([A-Z_]+)=(.*)$ ]] || galat konfigurasi "baris konfigurasi tidak dikenal"
    kunci="${BASH_REMATCH[1]}"
    nilai="${BASH_REMATCH[2]}"
    case "$kunci" in
      DOMAIN|STAGING_DIR|KONF_DIR|CERT_DIR|ACME_DIR|LE_DIR|LOG_DIR|ACME_EMAIL|ROUTER_PORT|MAIL_PORT|SUBNET|\
PENGGUNA_UID|PENGGUNA_GID|AKAR_LOKAL|AKAR_DAEMON|TANPA_IPTABLES|TANPA_SERTIFIKAT|MEMINFO)
        printf -v "K_$kunci" '%s' "$nilai" ;;
      *) galat konfigurasi "kunci konfigurasi tidak dikenal: $kunci" ;;
    esac
  done < "$KONF"

  DOMAIN="${K_DOMAIN:-}"
  STAGING_DIR="${K_STAGING_DIR:-/var/lib/wpmgr/staging}"
  KONF_DIR="${K_KONF_DIR:-/etc/wpmgr-staging}"
  CERT_DIR="${K_CERT_DIR:-/var/lib/wpmgr/certs}"
  ACME_DIR="${K_ACME_DIR:-/var/lib/wpmgr/acme}"
  LE_DIR="${K_LE_DIR:-/var/lib/wpmgr/letsencrypt}"
  LOG_DIR="${K_LOG_DIR:-/var/log/wpmgr-staging}"
  ACME_EMAIL="${K_ACME_EMAIL:-}"
  ROUTER_PORT="${K_ROUTER_PORT:-127.0.0.1:8090}"
  MAIL_PORT="${K_MAIL_PORT:-127.0.0.1:8025}"
  SUBNET="${K_SUBNET:-172.31.250.0/24}"
  AKAR_LOKAL="${K_AKAR_LOKAL:-}"
  AKAR_DAEMON="${K_AKAR_DAEMON:-}"
  TANPA_IPTABLES="${K_TANPA_IPTABLES:-0}"
  TANPA_SERTIFIKAT="${K_TANPA_SERTIFIKAT:-0}"
  MEMINFO="${K_MEMINFO:-/proc/meminfo}"
  LOCK="$KONF_DIR/digest.lock"

  [[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] \
    || galat konfigurasi "DOMAIN tidak sah"
  local p
  for p in "$STAGING_DIR" "$KONF_DIR" "$CERT_DIR" "$ACME_DIR" "$LE_DIR" "$LOG_DIR" "$MEMINFO"; do
    [[ "$p" == /* && "$p" != *..* ]] || galat konfigurasi "path konfigurasi harus absolut"
  done
  [[ -z "$ACME_EMAIL" || "$ACME_EMAIL" =~ ^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}$ ]] \
    || galat konfigurasi "ACME_EMAIL tidak sah"
  [[ "$ROUTER_PORT" =~ ^127\.0\.0\.1:[0-9]{2,5}$ && "$MAIL_PORT" =~ ^127\.0\.0\.1:[0-9]{2,5}$ ]] \
    || galat konfigurasi "port router/mail harus di 127.0.0.1"
  [[ "$SUBNET" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || galat konfigurasi "SUBNET tidak sah"

  if [[ -n "${K_PENGGUNA_UID:-}" ]]; then
    UID_W="$K_PENGGUNA_UID"
    GID_W="${K_PENGGUNA_GID:-$K_PENGGUNA_UID}"
  else
    UID_W="$(stat -c %u "$STAGING_DIR")"
    GID_W="$(stat -c %g "$STAGING_DIR")"
  fi
  [[ "$UID_W" =~ ^[0-9]{1,10}$ && "$GID_W" =~ ^[0-9]{1,10}$ && "$UID_W" != 0 ]] \
    || galat konfigurasi "pemilik STAGING_DIR tidak boleh root"
}

# ---- validasi ------------------------------------------------------------
# Di bash, `$` di [[ =~ ]] hanya cocok di akhir string, dan kelas karakter
# ASCII (LC_ALL=C) tidak memuat baris baru, jadi argumen "nama\n" gagal.

cek_nama() { [[ "${1-}" =~ ^[a-z0-9-]{1,40}$ ]] || galat argumen "nama staging tidak sah"; }
cek_id() {
  [[ "${1-}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || galat argumen "id site tidak sah"
}
cek_versi() {
  local v
  for v in "${VERSI_PHP[@]}"; do
    [[ "${1-}" == "$v" ]] && return 0
  done
  galat argumen "versi PHP tidak didukung"
}
cek_prefix() { [[ "${1-}" =~ ^[A-Za-z0-9_]{1,20}$ ]] || galat argumen "prefix tabel tidak sah"; }
cek_url() {
  [[ "${1-}" =~ ^https?://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]{0,200})?$ ]] || galat argumen "URL tidak sah"
}
cek_slug() { [[ "${1-}" =~ ^[a-z0-9][a-z0-9._-]{0,99}$ ]] || galat argumen "slug paket tidak sah"; }

# ---- bantuan -------------------------------------------------------------

acak() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }

nama_db() { printf 'stg_%s' "${1//-/_}"; }

sbg_pengguna() { setpriv --reuid="$UID_W" --regid="$GID_W" --clear-groups -- "$@"; }

jalur_daemon() {
  local p="$1"
  if [[ -n "$AKAR_DAEMON" && -n "$AKAR_LOKAL" && "$p" == "$AKAR_LOKAL"/* ]]; then
    printf '%s%s' "$AKAR_DAEMON" "${p#"$AKAR_LOKAL"}"
  else
    printf '%s' "$p"
  fi
}

# 0 = ada dan milik label ini; 1 = tidak ada; galat bila ada tetapi milik lain.
pastikan_milik() {
  local label
  if ! label="$(docker inspect --format '{{ index .Config.Labels "wpmgr.staging" }}' "$1" 2>/dev/null)"; then
    return 1
  fi
  [[ "$label" == "$2" ]] || galat ditolak "container $1 sudah ada dan bukan milik staging"
  return 0
}

image() {
  local kunci="$1" tag="$2" baris
  baris="$(grep -E "^${kunci}=" "$LOCK" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  if [[ -z "$baris" ]]; then
    docker pull --quiet "$tag" >/dev/null || galat docker "image $tag tidak dapat diunduh"
    baris="$(docker image inspect --format '{{ index .RepoDigests 0 }}' "$tag")" || galat docker "digest $tag tidak terbaca"
    [[ "$baris" =~ ^[a-z0-9./_-]+@sha256:[0-9a-f]{64}$ ]] || galat docker "digest $tag tidak dikenali"
    printf '%s=%s\n' "$kunci" "$baris" >> "$LOCK"
  fi
  [[ "$baris" =~ ^[a-z0-9./_-]+@sha256:[0-9a-f]{64}$ ]] || galat konfigurasi "digest.lock rusak untuk $kunci"
  printf '%s' "$baris"
}

hapus_opsi_klien() {
  if [[ -n "$OPSI_KLIEN" ]]; then
    docker exec wpmgr-stg-db rm -f "$OPSI_KLIEN" >/dev/null 2>&1 || true
  fi
}

# Kata sandi MariaDB masuk lewat berkas opsi di dalam container (stdin),
# bukan argumen/env proses, supaya tidak terbaca user lain lewat /proc.
opsi_klien() {
  OPSI_KLIEN="/run/wpmgr-klien-$$-$RANDOM.cnf"
  trap hapus_opsi_klien EXIT
  printf '[client]\nuser=%s\npassword=%s\n' "$1" "$2" \
    | docker exec -i wpmgr-stg-db sh -c "umask 077 && cat > $OPSI_KLIEN" \
    || galat docker "database staging tidak dapat dihubungi"
}

sql_root() {
  opsi_klien root "$(cat "$KONF_DIR/db-root")"
  printf '%s' "$1" | docker exec -i wpmgr-stg-db mariadb --defaults-extra-file="$OPSI_KLIEN" \
    || galat docker "perintah database gagal"
  hapus_opsi_klien
  OPSI_KLIEN=""
}

pastikan_siap() {
  docker network inspect "$JARINGAN" >/dev/null 2>&1 || galat ditolak "jalankan 'wpmgr-staging siapkan' dulu"
  [[ -s "$KONF_DIR/wp-cli.phar" ]] || galat ditolak "jalankan 'wpmgr-staging siapkan' dulu"
}

# ---- siapkan -------------------------------------------------------------

pasang_iptables() {
  # Container staging boleh ke internet (update plugin), tetapi tidak ke host
  # (PostgreSQL dashboard, ERPNext) maupun jaringan privat mana pun.
  iptables -C INPUT -i "$JEMBATAN" -j DROP 2>/dev/null || iptables -I INPUT -i "$JEMBATAN" -j DROP
  local j
  for j in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10; do
    iptables -C DOCKER-USER -i "$JEMBATAN" ! -o "$JEMBATAN" -d "$j" -j DROP 2>/dev/null \
      || iptables -I DOCKER-USER -i "$JEMBATAN" ! -o "$JEMBATAN" -d "$j" -j DROP
  done
}

pastikan_wpcli() {
  local phar="$KONF_DIR/wp-cli.phar" terkunci ada
  local url="https://github.com/wp-cli/wp-cli/releases/download/v${WPCLI_VERSI}/wp-cli-${WPCLI_VERSI}.phar"
  terkunci="$(grep -E '^wpcli=' "$LOCK" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  if [[ ! -s "$phar" ]]; then
    curl -fsSL --max-time 120 -o "$phar.tmp" "$url" || galat docker "wp-cli tidak dapat diunduh"
    if [[ -z "$terkunci" ]]; then
      terkunci="$(curl -fsSL --max-time 30 "$url.sha512" | awk '{print $1}')" || galat docker "hash wp-cli tidak dapat diunduh"
      [[ "$terkunci" =~ ^[0-9a-f]{128}$ ]] || galat docker "hash wp-cli tidak dikenali"
      printf 'wpcli=%s\n' "$terkunci" >> "$LOCK"
    fi
    mv "$phar.tmp" "$phar"
    chmod 0644 "$phar"
  fi
  ada="$(sha512sum "$phar" | cut -d' ' -f1)"
  if [[ -n "$terkunci" && "$ada" != "$terkunci" ]]; then
    rm -f "$phar"
    galat docker "hash wp-cli tidak cocok dengan digest.lock"
  fi
}

tulis_router_bawaan() {
  cat > "$KONF_DIR/router/conf.d/00-bawaan.conf" <<'KONF'
log_format wpmgr_akses '$msec';
resolver 127.0.0.11 valid=10s ipv6=off;
server {
    listen 80 default_server;
    server_name _;
    return 444;
}
KONF
  chmod 0644 "$KONF_DIR/router/conf.d/00-bawaan.conf"
}

jalankan_layanan() {
  local nama="$1" peran="$2"
  shift 2
  if pastikan_milik "$nama" "layanan:$peran"; then
    docker start "$nama" >/dev/null || galat docker "$nama tidak dapat dijalankan"
  else
    docker run -d --name "$nama" --label "wpmgr.staging=layanan:$peran" --network "$JARINGAN" \
      --restart unless-stopped "$@" >/dev/null || galat docker "$nama tidak dapat dibuat"
  fi
}

cmd_siapkan() {
  install -d -m 0755 "$KONF_DIR" "$KONF_DIR/router" "$KONF_DIR/router/conf.d" "$KONF_DIR/router/htpasswd" \
    "$CERT_DIR" "$ACME_DIR" "$LOG_DIR"
  install -d -m 0700 "$KONF_DIR/db" "$LE_DIR"
  [[ -f "$LOCK" ]] || install -m 0644 /dev/null "$LOCK"
  if [[ ! -s "$KONF_DIR/db-root" ]]; then
    acak 32 > "$KONF_DIR/db-root"
    chmod 0600 "$KONF_DIR/db-root"
  fi
  if ! docker network inspect "$JARINGAN" >/dev/null 2>&1; then
    docker network create --driver bridge --subnet "$SUBNET" --opt "com.docker.network.bridge.name=$JEMBATAN" \
      --label wpmgr.staging=layanan:jaringan "$JARINGAN" >/dev/null || galat docker "jaringan staging tidak dapat dibuat"
  fi
  [[ "$TANPA_IPTABLES" == 1 ]] || pasang_iptables
  pastikan_wpcli
  tulis_router_bawaan

  local env_db
  env_db="$(mktemp)"
  printf 'MARIADB_ROOT_PASSWORD=%s\n' "$(cat "$KONF_DIR/db-root")" > "$env_db"
  jalankan_layanan wpmgr-stg-db db --memory 1g --env-file "$env_db" -v wpmgr-stg-db:/var/lib/mysql \
    "$(image mariadb mariadb:11.4)" --innodb-buffer-pool-size=256M --max-allowed-packet=64M
  rm -f "$env_db"
  jalankan_layanan wpmgr-stg-mail mail --memory 128m -p "$MAIL_PORT:8025" "$(image mailpit axllent/mailpit:latest)"
  jalankan_layanan wpmgr-stg-router router --memory 128m -p "$ROUTER_PORT:80" \
    -v "$(jalur_daemon "$KONF_DIR/router/conf.d"):/etc/nginx/conf.d:ro" \
    -v "$(jalur_daemon "$KONF_DIR/router/htpasswd"):/etc/nginx/wpmgr-htpasswd:ro" \
    -v "$(jalur_daemon "$LOG_DIR"):/var/log/wpmgr-akses" \
    "$(image nginx nginx:1.27-alpine)"
  echo "staging siap"
}

# ---- container per staging -----------------------------------------------

cmd_buat() {
  cek_nama "${1-}"
  cek_versi "${2-}"
  cek_id "${3-}"
  local nama="$1" versi="$2" situs="$STAGING_DIR/$3" wadah="wp-$1" img sekarang
  pastikan_siap
  img="$(image "php${versi/./}" "wordpress:php${versi}-apache")"
  sbg_pengguna mkdir -p "$situs/files" "$situs/ekspor" "$situs/log"
  if pastikan_milik "$wadah" "situs:$nama"; then
    sekarang="$(docker inspect --format '{{ .Config.Image }}' "$wadah")"
    if [[ "$sekarang" == "$img" ]]; then
      docker start "$wadah" >/dev/null || galat docker "container $wadah tidak dapat dijalankan"
      return 0
    fi
    docker rm -f "$wadah" >/dev/null || galat docker "container lama $wadah tidak dapat dihapus"
  fi
  docker run -d --name "$wadah" --label "wpmgr.staging=situs:$nama" --network "$JARINGAN" \
    --restart unless-stopped --memory 384m --memory-swap 384m --cpus 1 --pids-limit 256 \
    --security-opt no-new-privileges \
    -e "APACHE_RUN_USER=#$UID_W" -e "APACHE_RUN_GROUP=#$GID_W" \
    -v "$(jalur_daemon "$situs/files"):/var/www/html" \
    -v "$(jalur_daemon "$situs/ekspor"):/wpmgr-ekspor" \
    -v "$(jalur_daemon "$situs/log"):/wpmgr-log" \
    -v "$(jalur_daemon "$KONF_DIR/wp-cli.phar"):/usr/local/bin/wp:ro" \
    "$img" >/dev/null || galat docker "container $wadah tidak dapat dibuat"
}

cmd_jalan() {
  cek_nama "${1-}"
  pastikan_milik "wp-$1" "situs:$1" || galat ditolak "staging $1 belum dibuat"
  docker start "wp-$1" >/dev/null || galat docker "staging $1 tidak dapat dijalankan"
}

cmd_jeda() {
  cek_nama "${1-}"
  pastikan_milik "wp-$1" "situs:$1" || galat ditolak "staging $1 belum dibuat"
  docker stop -t 20 "wp-$1" >/dev/null || galat docker "staging $1 tidak dapat dijeda"
}

muat_ulang_router() {
  if pastikan_milik wpmgr-stg-router layanan:router; then
    docker exec wpmgr-stg-router nginx -s reload >/dev/null 2>&1 || true
  fi
}

cmd_hapus() {
  cek_nama "${1-}"
  if pastikan_milik "wp-$1" "situs:$1"; then
    docker rm -f "wp-$1" >/dev/null || galat docker "staging $1 tidak dapat dihapus"
  fi
  rm -f "$KONF_DIR/router/conf.d/stg-$1.conf" "$KONF_DIR/router/htpasswd/$1" "$LOG_DIR/$1.log"
  muat_ulang_router
}

# ---- database ------------------------------------------------------------

wp_config() {
  local nama="$1" db="$2" pw="$3" prefix="$4" k
  printf '%s\n' "<?php" \
    "// Dibuat oleh wpmgr-staging untuk staging $nama. Jangan disalin ke produksi." \
    "define( 'DB_NAME', '$db' );" \
    "define( 'DB_USER', '$db' );" \
    "define( 'DB_PASSWORD', '$pw' );" \
    "define( 'DB_HOST', 'wpmgr-stg-db' );" \
    "define( 'DB_CHARSET', 'utf8mb4' );" \
    "define( 'DB_COLLATE', '' );" \
    "\$table_prefix = '$prefix';" \
    "define( 'WP_HOME', 'https://$nama.$DOMAIN' );" \
    "define( 'WP_SITEURL', 'https://$nama.$DOMAIN' );" \
    "define( 'WPMGR_STAGING', true );" \
    "define( 'WPMGR_DISABLE_MONITORING', true );" \
    "define( 'DISABLE_WP_CRON', true );" \
    "define( 'WP_ENVIRONMENT_TYPE', 'staging' );" \
    "define( 'AUTOMATIC_UPDATER_DISABLED', true );" \
    "define( 'WP_DEBUG', false );" \
    "@ini_set( 'display_errors', '0' );" \
    "@ini_set( 'log_errors', '1' );" \
    "@ini_set( 'error_log', '/wpmgr-log/php-error.log' );" \
    "// Router staging selalu mengirim X-Forwarded-Proto https (TLS diakhiri nginx host)." \
    "if ( isset( \$_SERVER['HTTP_X_FORWARDED_PROTO'] ) && 'https' === \$_SERVER['HTTP_X_FORWARDED_PROTO'] ) {" \
    "    \$_SERVER['HTTPS'] = 'on';" \
    "}"
  for k in AUTH_KEY SECURE_AUTH_KEY LOGGED_IN_KEY NONCE_KEY AUTH_SALT SECURE_AUTH_SALT LOGGED_IN_SALT NONCE_SALT; do
    printf "define( '%s', '%s' );\n" "$k" "$(acak 32)"
  done
  printf '%s\n' "if ( ! defined( 'ABSPATH' ) ) {" "    define( 'ABSPATH', __DIR__ . '/' );" "}" \
    "require_once ABSPATH . 'wp-settings.php';"
}

cmd_db_buat() {
  cek_nama "${1-}"
  cek_id "${2-}"
  cek_prefix "${3-}"
  local nama="$1" db pw
  db="$(nama_db "$nama")"
  pw="$(acak 24)"
  sql_root "CREATE DATABASE IF NOT EXISTS \`$db\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$db'@'%' IDENTIFIED BY '$pw';
ALTER USER '$db'@'%' IDENTIFIED BY '$pw';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, LOCK TABLES, CREATE TEMPORARY TABLES, REFERENCES, CREATE VIEW, SHOW VIEW ON \`$db\`.* TO '$db'@'%';"
  install -d -m 0700 "$KONF_DIR/db"
  printf '%s' "$pw" > "$KONF_DIR/db/$nama"
  chmod 0600 "$KONF_DIR/db/$nama"
  wp_config "$nama" "$db" "$pw" "$3" | sbg_pengguna tee "$STAGING_DIR/$2/files/wp-config.php" >/dev/null \
    || galat ditolak "wp-config.php staging tidak dapat ditulis"
}

cmd_db_hapus() {
  cek_nama "${1-}"
  local db
  db="$(nama_db "$1")"
  sql_root "DROP DATABASE IF EXISTS \`$db\`; DROP USER IF EXISTS '$db'@'%';"
  rm -f "$KONF_DIR/db/$1"
}

cmd_db_impor() {
  cek_nama "${1-}"
  local nama="$1" db
  db="$(nama_db "$nama")"
  [[ -s "$KONF_DIR/db/$nama" ]] || galat ditolak "database staging belum dibuat (db-buat)"
  sql_root "DROP DATABASE IF EXISTS \`$db\`; CREATE DATABASE \`$db\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
  # SQL berasal dari site produksi yang bisa saja disusupi: diimpor sebagai
  # user staging ini, yang hanya berhak atas database-nya sendiri.
  opsi_klien "$db" "$(cat "$KONF_DIR/db/$nama")"
  docker exec -i wpmgr-stg-db mariadb --defaults-extra-file="$OPSI_KLIEN" --binary-mode \
    --max-allowed-packet=64M "$db" || galat impor "impor database staging gagal"
}

# ---- wp-cli --------------------------------------------------------------

cmd_wpcli() {
  cek_nama "${1-}"
  local nama="$1"
  shift
  local argumen=()
  case "${1-} ${2-}" in
    "search-replace "*)
      cek_url "${2-}"
      cek_url "${3-}"
      argumen=(search-replace "$2" "$3" --all-tables-with-prefix --skip-columns=guid --precise --skip-plugins --skip-themes)
      if [[ $# -eq 4 && "$4" == "--export" ]]; then
        argumen+=(--export=/wpmgr-ekspor/dorong.sql)
      elif [[ $# -ne 3 ]]; then
        galat argumen "argumen search-replace tidak sah"
      fi
      ;;
    "option update")
      [[ $# -eq 4 && "$3" == "blog_public" && "$4" =~ ^[01]$ ]] || galat argumen "hanya blog_public 0/1 yang boleh diubah"
      argumen=(option update blog_public "$4" --skip-plugins --skip-themes)
      ;;
    "plugin update"|"theme update")
      cek_slug "${3-}"
      argumen=("$1" update "$3")
      if [[ $# -eq 4 ]]; then
        [[ "$4" =~ ^--version=[0-9A-Za-z][0-9A-Za-z._-]{0,29}$ ]] || galat argumen "versi paket tidak sah"
        argumen+=("$4")
      elif [[ $# -ne 3 ]]; then
        galat argumen "argumen update tidak sah"
      fi
      ;;
    "core update")
      argumen=(core update)
      if [[ $# -eq 3 ]]; then
        [[ "$3" =~ ^--version=[0-9][0-9A-Za-z._-]{0,29}$ ]] || galat argumen "versi core tidak sah"
        argumen+=("$3")
      elif [[ $# -ne 2 ]]; then
        galat argumen "argumen update core tidak sah"
      fi
      ;;
    "plugin list")
      [[ $# -eq 2 ]] || galat argumen "argumen plugin list tidak sah"
      argumen=(plugin list --format=json)
      ;;
    "core version"|"cache flush")
      [[ $# -eq 2 ]] || galat argumen "argumen wp-cli tidak sah"
      argumen=("$1" "$2")
      ;;
    *)
      galat argumen "perintah wp-cli tidak ada di daftar putih"
      ;;
  esac
  pastikan_milik "wp-$nama" "situs:$nama" || galat ditolak "staging $nama belum dibuat"
  docker exec -u "$UID_W:$GID_W" "wp-$nama" php -d memory_limit=512M /usr/local/bin/wp --path=/var/www/html \
    "${argumen[@]}" || galat wpcli "wp-cli gagal"
}

# ---- router --------------------------------------------------------------

konf_router() {
  local n="$1" r="$2"
  cat <<KONF
server {
    listen 80;
    server_name $n.$DOMAIN;
    client_max_body_size 64m;
    access_log /var/log/wpmgr-akses/$n.log wpmgr_akses;
    add_header X-Robots-Tag "noindex, nofollow" always;

    # Cookie bertanda tangan dari /__wpmgr_masuk mematikan Basic Auth
    # sampai kedaluwarsa (SSO dan probe dashboard, Koreksi #5).
    secure_link \$cookie_wpmgr_stg_m,\$cookie_wpmgr_stg_e;
    secure_link_md5 "\$secure_link_expires\$host $r";
    set \$wpmgr_auth "Staging $n";
    if (\$secure_link = "1") {
        set \$wpmgr_auth off;
    }
    auth_basic \$wpmgr_auth;
    auth_basic_user_file /etc/nginx/wpmgr-htpasswd/$n;

    location = /__wpmgr_masuk {
        auth_basic off;
        secure_link \$arg_m,\$arg_e;
        secure_link_md5 "\$secure_link_expires\$host $r";
        if (\$secure_link != "1") {
            return 403;
        }
        add_header Set-Cookie "wpmgr_stg_m=\$arg_m; Path=/; Max-Age=43200; Secure; HttpOnly; SameSite=Lax" always;
        add_header Set-Cookie "wpmgr_stg_e=\$arg_e; Path=/; Max-Age=43200; Secure; HttpOnly; SameSite=Lax" always;
        add_header X-Robots-Tag "noindex, nofollow" always;
        return 302 /?wpmgr_sso=\$arg_sso;
    }

    location / {
        set \$wpmgr_hulu wp-$n;
        proxy_pass http://\$wpmgr_hulu;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}
KONF
}

cmd_router_muat() {
  local sumber="$STAGING_DIR/router" tujuan="$KONF_DIR/router" baru cadangan f nama rahasia baris
  local pola_htpasswd='^staging:\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$'
  baru="$(mktemp -d)"
  cadangan="$(mktemp -d)"
  shopt -s nullglob
  for f in "$sumber"/*.rahasia; do
    nama="$(basename "$f" .rahasia)"
    cek_nama "$nama"
    rahasia="$(sbg_pengguna cat "$f")" || galat argumen "rahasia router $nama tidak terbaca"
    [[ "$rahasia" =~ ^[0-9a-f]{64}$ ]] || galat argumen "rahasia router $nama tidak sah"
    baris="$(sbg_pengguna cat "$sumber/$nama.htpasswd")" || galat argumen "htpasswd $nama tidak ada"
    [[ "$baris" =~ $pola_htpasswd ]] || galat argumen "htpasswd $nama tidak sah"
    konf_router "$nama" "$rahasia" > "$baru/stg-$nama.conf"
    printf '%s\n' "$baris" > "$baru/$nama.htpasswd"
  done
  mkdir -p "$cadangan/conf.d" "$cadangan/htpasswd"
  cp -a "$tujuan/conf.d/." "$cadangan/conf.d/" 2>/dev/null || true
  cp -a "$tujuan/htpasswd/." "$cadangan/htpasswd/" 2>/dev/null || true
  rm -f "$tujuan/conf.d/"stg-*.conf "$tujuan/htpasswd/"*
  for f in "$baru"/stg-*.conf; do
    install -m 0644 "$f" "$tujuan/conf.d/"
  done
  for f in "$baru"/*.htpasswd; do
    install -m 0644 "$f" "$tujuan/htpasswd/$(basename "$f" .htpasswd)"
  done
  if ! docker exec wpmgr-stg-router nginx -t >/dev/null 2>&1; then
    rm -f "$tujuan/conf.d/"stg-*.conf "$tujuan/htpasswd/"*
    cp -a "$cadangan/conf.d/." "$tujuan/conf.d/" 2>/dev/null || true
    cp -a "$cadangan/htpasswd/." "$tujuan/htpasswd/" 2>/dev/null || true
    galat docker "konfigurasi router ditolak nginx -t; konfigurasi lama dipulihkan"
  fi
  docker exec wpmgr-stg-router nginx -s reload >/dev/null || galat docker "router tidak dapat dimuat ulang"
  rm -rf "$baru" "$cadangan"
}

# ---- sertifikat ----------------------------------------------------------

cmd_sertifikat() {
  cek_nama "${1-}"
  local host="$1.$DOMAIN"
  if [[ "$TANPA_SERTIFIKAT" == 1 ]]; then
    echo "sertifikat dilewati (TANPA_SERTIFIKAT=1)"
    return 0
  fi
  local email=(--register-unsafely-without-email)
  [[ -n "$ACME_EMAIL" ]] && email=(-m "$ACME_EMAIL")
  certbot certonly --non-interactive --agree-tos "${email[@]}" --webroot -w "$ACME_DIR" -d "$host" \
    --cert-name "$host" --config-dir "$LE_DIR/config" --work-dir "$LE_DIR/work" --logs-dir "$LE_DIR/logs" \
    --keep-until-expiring >/dev/null 2>&1 || galat sertifikat "sertifikat untuk $host belum dapat diterbitkan"
  install -d -m 0755 "$CERT_DIR/$host"
  install -m 0644 "$LE_DIR/config/live/$host/fullchain.pem" "$CERT_DIR/$host/fullchain.pem"
  install -m 0600 "$LE_DIR/config/live/$host/privkey.pem" "$CERT_DIR/$host/privkey.pem"
}

# ---- status --------------------------------------------------------------

cmd_status() {
  local mem total bebas daftar wadah="" pisah="" nama keadaan berjalan akses="" pisah2="" f n t
  # %.0f, bukan %d: awk busybox memotong %d ke 32 bit (-2147483648 untuk 4 GiB).
  mem="$(awk '/^MemAvailable:/ { printf "%.0f", $2 * 1024 }' "$MEMINFO")"
  read -r total bebas < <(df -B1 --output=size,avail "$STAGING_DIR" | tail -n 1)
  [[ "$mem" =~ ^[0-9]+$ && "$total" =~ ^[0-9]+$ && "$bebas" =~ ^[0-9]+$ ]] || galat docker "status sistem tidak terbaca"
  daftar="$(docker ps -a --filter label=wpmgr.staging --format '{{.Names}}|{{.State}}')" || galat docker "daftar container tidak terbaca"
  while IFS='|' read -r nama keadaan; do
    [[ "$nama" =~ ^wp-[a-z0-9-]{1,40}$ ]] || continue
    berjalan=false
    [[ "$keadaan" == running ]] && berjalan=true
    wadah+="$pisah\"$nama\":{\"berjalan\":$berjalan}"
    pisah=","
  done <<< "$daftar"
  shopt -s nullglob
  for f in "$LOG_DIR"/*.log; do
    n="$(basename "$f" .log)"
    [[ "$n" =~ ^[a-z0-9-]{1,40}$ ]] || continue
    t="$(stat -c %Y "$f")"
    # Log akses hanya dipakai untuk waktu akses terakhir (jeda otomatis);
    # dipangkas bila besar dengan mtime dipertahankan.
    if [[ "$(stat -c %s "$f")" -gt 1048576 ]]; then
      : > "$f"
      touch -d "@$t" "$f"
    fi
    akses+="$pisah2\"$n\":$t"
    pisah2=","
  done
  printf '{"mem_tersedia":%s,"disk_total":%s,"disk_bebas":%s,"container":{%s},"akses":{%s}}\n' \
    "$mem" "$total" "$bebas" "$wadah" "$akses"
}

# ---- utama ---------------------------------------------------------------

utama() {
  local perintah="${1-}"
  [[ $# -gt 0 ]] && shift
  muat_konf
  case "$perintah" in
    siapkan|router-muat|status)
      [[ $# -eq 0 ]] || galat argumen "$perintah tidak menerima argumen" ;;
    buat|db-buat)
      [[ $# -eq 3 ]] || galat argumen "pemakaian: $perintah <nama> <arg2> <arg3>" ;;
    jalan|jeda|hapus|db-hapus|db-impor|sertifikat)
      [[ $# -eq 1 ]] || galat argumen "pemakaian: $perintah <nama>" ;;
    wpcli)
      [[ $# -ge 3 && $# -le 5 ]] || galat argumen "pemakaian: wpcli <nama> <perintah...>" ;;
    *)
      galat argumen "subperintah tidak dikenal" ;;
  esac
  case "$perintah" in
    siapkan) cmd_siapkan ;;
    buat) cmd_buat "$@" ;;
    jalan) cmd_jalan "$@" ;;
    jeda) cmd_jeda "$@" ;;
    hapus) cmd_hapus "$@" ;;
    db-buat) cmd_db_buat "$@" ;;
    db-hapus) cmd_db_hapus "$@" ;;
    db-impor) cmd_db_impor "$@" ;;
    wpcli) cmd_wpcli "$@" ;;
    router-muat) cmd_router_muat ;;
    sertifikat) cmd_sertifikat "$@" ;;
    status) cmd_status ;;
  esac
}

utama "$@"
```

- [ ] **Step 6: Contoh konfigurasi.**

File: `deploy/staging/staging.conf.contoh`
```
# /etc/wpmgr-staging/staging.conf -- milik root, 0644. Dibaca skrip wpmgr-staging.
DOMAIN=staging.halosocia.my.id
STAGING_DIR=/var/lib/wpmgr/staging
KONF_DIR=/etc/wpmgr-staging
CERT_DIR=/var/lib/wpmgr/certs
ACME_DIR=/var/lib/wpmgr/acme
LE_DIR=/var/lib/wpmgr/letsencrypt
LOG_DIR=/var/log/wpmgr-staging
ACME_EMAIL=admin@halosocia.my.id
ROUTER_PORT=127.0.0.1:8090
MAIL_PORT=127.0.0.1:8025
SUBNET=172.31.250.0/24
```

- [ ] **Step 7: Bit eksekusi.** Windows tidak menyimpan bit eksekusi di checkout. Tanpa bit ini, bats dan sudo gagal dengan `Permission denied`. Run:

```bash
git add .gitattributes deploy/staging
git update-index --chmod=+x deploy/staging/wpmgr-staging deploy/staging/tests/palsu/docker deploy/staging/tests/palsu/setpriv deploy/staging/tests/palsu/certbot deploy/staging/tests/palsu/curl deploy/staging/tests/palsu/df deploy/staging/tests/palsu/iptables
```

Di container bats, berkas di bind mount Windows tampil dengan mode 0777, jadi test tetap bisa dijalankan sebelum commit.

- [ ] **Step 8: Jalankan bats.** Run: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" -w /code bats/bats:1.11.0 deploy/staging/tests`. Expected: `22 tests, 0 failures`. Lalu `docker run --rm -v "$(pwd -W):/code" koalaman/shellcheck:stable /code/deploy/staging/wpmgr-staging` (dengan `MSYS_NO_PATHCONV=1`). Expected: tanpa temuan tingkat error.

- [ ] **Step 9: Commit.**

```bash
git add .gitattributes deploy/staging
git commit -m "feat(staging): skrip pembantu wpmgr-staging dengan validasi ketat dan test bats"
```

---

### Task 11: Validasi `wpmgr.staging.aman` dan pembungkus `wpmgr.staging.pembantu`

**Files:**
- Create: `src/wpmgr/staging/__init__.py`, `src/wpmgr/staging/aman.py`, `src/wpmgr/staging/pembantu.py`, `tests/unit/test_staging_aman.py`, `tests/unit/test_staging_pembantu.py`, `tests/unit/pembantu_palsu.py`

**Interfaces:**
- Consumes: Task 1 (`Settings.staging_*`), Task 10 (kode keluar dan bentuk `status`).
- Produces (`wpmgr.staging.aman`):
  - konstanta: `POLA_NAMA`, `POLA_ID_DORONG`, `POLA_TABEL`, `POLA_SHA256`, `VERSI_PHP`, `DILINDUNGI_STAGING`;
  - `class PathTidakAman(ValueError)`;
  - nama dan versi: `nama_sah(nama) -> bool`, `nama_dari_url(url: str) -> str`, `versi_php_staging(versi: str | None) -> tuple[str, bool]` (bool = peringatan);
  - pembersihan nilai: `bersih_teks(nilai, panjang: int) -> str | None` (buang NUL dan surrogate tunggal, lalu potong), `angka(nilai, bawah: int, atas: int) -> int | None` (tolak `bool`/float/teks bukan angka, jepit ke rentang);
  - path: `path_sah(p) -> str` (melempar `PathTidakAman`), `dikecualikan(p) -> bool`, `boleh_didorong(p) -> bool` (cermin `WPMGR_Staging_Path`), `jalur_di_dalam(akar: Path, relatif: str) -> Path` (melempar `PathTidakAman` bila melewati symlink atau keluar dari akar).
- Produces (`wpmgr.staging.pembantu`):
  - `class GalatPembantu(Exception)` dengan `kode: str` (`argumen|ditolak|docker|sertifikat|impor|konfigurasi|wpcli|waktu|status|lain`) dan `pesan: str` (aman untuk UI);
  - `@dataclass(frozen=True) StatusPembantu(mem_tersedia: int, disk_total: int, disk_bebas: int, container: dict[str, bool], akses: dict[str, int])`;
  - `class Pembantu`:
    - pembuatan: `Pembantu(perintah: list[str])`, `Pembantu.dari_setelan(s=None)`;
    - pemanggilan umum: `jalankan(*argumen, masukan: list[Path] = (), timeout=120) -> str`;
    - subperintah: `siapkan()`, `buat(nama, versi_php, site_id)`, `jalan(nama)`, `jeda(nama)`, `hapus(nama)`, `db_buat(nama, site_id, prefix)`, `db_hapus(nama)`, `db_impor(nama, berkas: list[Path])`, `wpcli(nama, *argumen) -> str`, `router_muat()`, `sertifikat(nama)`, `status() -> StatusPembantu`;
  - fungsi modul:
    - status: `urai_status(teks: str) -> StatusPembantu`;
    - sandi dan akses router: `sandi_baru() -> str`, `hash_sandi(sandi) -> str` (bcrypt), `tulis_akses_router(dir_staging: Path, nama, sandi_hash, rahasia)`, `hapus_akses_router(dir_staging: Path, nama)`;
    - tautan bertanda tangan: `tautan_masuk(rahasia, host, token_sso, sekarang: int, umur=43200) -> str`, `cookie_akses(rahasia, host, sekarang: int, umur=43200) -> dict[str, str]`.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/unit/pembantu_palsu.py`
```python
"""Skrip pembantu tiruan: dipanggil sebagai `python pembantu_palsu.py <subperintah> ...`."""

import json
import os
import sys
import time


def main() -> int:
    argumen = sys.argv[1:]
    masukan = sys.stdin.buffer.read()
    catatan = os.environ.get("PALSU_CATATAN")
    if catatan:
        with open(catatan, "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": argumen, "stdin": masukan.decode("utf-8", "replace")}) + "\n")
    perintah = argumen[0] if argumen else ""
    if perintah == "status":
        sys.stdout.write(os.environ.get("PALSU_STATUS", "{}"))
    elif perintah == "tidur":
        time.sleep(30)
    elif perintah == "gagal":
        sys.stderr.write("baris lain\nGALAT ditolak: container wp-x sudah ada dan bukan milik staging\n")
        return 3
    elif perintah == "gagal-tanpa-pesan":
        return 4
    elif perintah == "berisik":
        sys.stdout.write("x" * (5 * 1024 * 1024))
    else:
        sys.stdout.write("ok " + " ".join(argumen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

File: `tests/unit/test_staging_aman.py`
```python
import os
from pathlib import Path

import pytest

from wpmgr.staging.aman import (
    PathTidakAman,
    angka,
    bersih_teks,
    boleh_didorong,
    dikecualikan,
    jalur_di_dalam,
    nama_dari_url,
    nama_sah,
    path_sah,
    versi_php_staging,
)


@pytest.mark.parametrize("url,nama", [
    ("https://www.Toko-Contoh.co.id", "toko-contoh-co-id"),
    ("https://klinik.halosocia.my.id/", "klinik-halosocia-my-id"),
    ("https://xn--caf-dma.id", "xn-caf-dma-id"),
    ("https://" + "a" * 60 + ".id", "a" * 40),
    ("https://-.id", "id"),
    ("bukan url", "situs"),
])
def test_nama_dari_url(url, nama):
    hasil = nama_dari_url(url)
    assert hasil == nama
    assert nama_sah(hasil)


@pytest.mark.parametrize("nama,sah", [
    ("toko-1", True), ("a" * 40, True), ("a" * 41, False), ("Toko", False), ("toko\n", False),
    ("", False), ("../x", False), ("toko_1", False), (None, False), ("٣", False),
])
def test_nama_sah(nama, sah):
    assert nama_sah(nama) is sah


@pytest.mark.parametrize("versi,hasil", [
    ("8.1.29", ("8.1", False)), ("7.4.33", ("7.4", False)), ("8.3.0", ("8.3", False)),
    ("7.2.34", ("7.4", True)), ("8.4.1", ("8.3", True)), (None, ("8.1", True)), ("abc", ("8.1", True)),
])
def test_versi_php_staging(versi, hasil):
    assert versi_php_staging(versi) == hasil


def test_bersih_teks():
    assert bersih_teks("a\x00b", 10) == "ab"
    assert bersih_teks("a\ud800b", 10) == "a?b"
    assert bersih_teks("é" * 10, 3) == "ééé"
    assert bersih_teks(None, 3) is None
    assert bersih_teks(12, 5) == "12"


@pytest.mark.parametrize("nilai,hasil", [
    (5, 5), ("7", 7), (-3, 0), (10**20, 1000), ("12a", None), (True, None), (1.5, None), (None, None), ("٣", None),
])
def test_angka(nilai, hasil):
    assert angka(nilai, 0, 1000) == hasil


@pytest.mark.parametrize("p", [
    "", "/etc/passwd", "../x", "a/../b", "a//b", "a/./b", "C:/x", "a\\b", "a\x00b", "a\nb",
    "a" * 1025, "a/" + "b" * 256, "a/", "a\ud800", 5, None,
])
def test_path_sah_menolak(p):
    with pytest.raises(PathTidakAman):
        path_sah(p)


def test_path_sah_menerima():
    for p in ("wp-content/uploads/ü-berkas.txt", ".htaccess", "wp-content/plugins/a b/c.php",
              "wp-content/uploads/" + "é" * 120 + ".jpg"):
        assert path_sah(p) == p


def test_dikecualikan_cermin_connector():
    for p in ("wp-config.php", ".maintenance", "debug.log", "wp-content/debug.LOG", "wp-content/cache/a",
              "wp-content/wpmgr-dorong/x", "wp-content/updraft/b.zip", "wp-content/backups-dup-lite/a"):
        assert dikecualikan(p), p
    for p in ("index.php", "wp-content/uploads/cache/a.jpg", "wp-content/plugins/updraft/x.php"):
        assert not dikecualikan(p), p


def test_boleh_didorong_cermin_connector():
    for p in ("wp-content/themes/x/style.css", "wp-admin/index.php", "wp-includes/version.php", "index.php",
              "wp-login.php", ".htaccess", "xmlrpc.php"):
        assert boleh_didorong(p), p
    for p in ("wp-config.php", "wp-content/plugins/wp-manager-connector/x.php",
              "wp-content/mu-plugins/wpmgr-staging.php", "lain.php", "foo/bar.php", "WP-LOGIN.PHP", "../x"):
        assert not boleh_didorong(p), p


def test_jalur_di_dalam(tmp_path):
    akar = tmp_path / "files"
    akar.mkdir()
    assert jalur_di_dalam(akar, "wp-content/uploads/a.jpg") == akar / "wp-content" / "uploads" / "a.jpg"
    with pytest.raises(PathTidakAman):
        jalur_di_dalam(akar, "../luar.txt")


def test_jalur_di_dalam_menolak_symlink(tmp_path):
    akar = tmp_path / "files"
    (akar / "wp-content").mkdir(parents=True)
    luar = tmp_path / "luar"
    luar.mkdir()
    try:
        os.symlink(luar, akar / "wp-content" / "uploads", target_is_directory=True)
    except OSError:
        pytest.skip("symlink tidak diizinkan di sistem ini")
    with pytest.raises(PathTidakAman):
        jalur_di_dalam(akar, "wp-content/uploads/x.php")
    assert not (luar / "x.php").exists()
    assert isinstance(jalur_di_dalam(akar, "wp-content/lain.txt"), Path)
```

File: `tests/unit/test_staging_pembantu.py`
```python
import base64
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import bcrypt
import pytest

from wpmgr.config import Settings
from wpmgr.staging.pembantu import (
    BATAS_KELUARAN,
    GalatPembantu,
    Pembantu,
    cookie_akses,
    hapus_akses_router,
    hash_sandi,
    sandi_baru,
    tautan_masuk,
    tulis_akses_router,
    urai_status,
)

PALSU = Path(__file__).with_name("pembantu_palsu.py")
ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


@pytest.fixture
def catatan(tmp_path, monkeypatch):
    berkas = tmp_path / "catatan.jsonl"
    monkeypatch.setenv("PALSU_CATATAN", str(berkas))

    def baca():
        if not berkas.exists():
            return []
        return [json.loads(b) for b in berkas.read_text(encoding="utf-8").splitlines()]

    return baca


@pytest.fixture
def pembantu():
    return Pembantu([sys.executable, str(PALSU)])


def _settings(monkeypatch, **env):
    for k, v in {"DATABASE_URL": "postgresql+psycopg://a:b@localhost/c", "WPMGR_SECRET_KEY": "k",
                 "WPMGR_BASE_URL": "https://d.test", "WPMGR_SESSION_SECRET": "s", **env}.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def test_dari_setelan_memakai_sudo_n(monkeypatch):
    monkeypatch.delenv("WPMGR_STAGING_PEMBANTU_AWALAN", raising=False)
    assert Pembantu.dari_setelan(_settings(monkeypatch)).perintah == ["sudo", "-n", "/usr/local/sbin/wpmgr-staging"]


def test_dari_setelan_dengan_awalan_dev(monkeypatch):
    s = _settings(monkeypatch, WPMGR_STAGING_PEMBANTU_AWALAN="docker compose exec -T pembantu /usr/local/sbin/wpmgr-staging")
    assert Pembantu.dari_setelan(s).perintah == [
        "docker", "compose", "exec", "-T", "pembantu", "/usr/local/sbin/wpmgr-staging"]


def test_argumen_diteruskan_persis(pembantu, catatan):
    assert pembantu.buat("toko", "8.1", ID) == f"ok buat toko 8.1 {ID}"
    assert pembantu.wpcli("toko", "plugin", "update", "akismet", "--version=5.3") == \
        "ok wpcli toko plugin update akismet --version=5.3"
    assert catatan()[0]["argv"] == ["buat", "toko", "8.1", ID]


@pytest.mark.parametrize("panggil", [
    lambda p: p.buat("Toko", "8.1", ID),
    lambda p: p.buat("toko", "9.9", ID),
    lambda p: p.buat("toko", "8.1", "../x"),
    lambda p: p.db_buat("toko", ID, "wp_'"),
    lambda p: p.jalan("toko;id"),
    lambda p: p.wpcli("toko\n", "core", "version"),
])
def test_validasi_sebelum_subprocess(pembantu, catatan, panggil):
    with pytest.raises(ValueError):
        panggil(pembantu)
    assert catatan() == []


def test_galat_dipetakan_ke_pesan_aman(pembantu):
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalankan("gagal")
    assert e.value.kode == "ditolak"
    assert e.value.pesan == "container wp-x sudah ada dan bukan milik staging"
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalankan("gagal-tanpa-pesan")
    assert e.value.kode == "docker"
    assert "Docker" in e.value.pesan


def test_perintah_tidak_ada_menjadi_galat(tmp_path):
    with pytest.raises(GalatPembantu) as e:
        Pembantu([str(tmp_path / "tidak-ada")]).jalankan("status")
    assert e.value.kode == "lain"
    assert str(tmp_path) not in e.value.pesan


def test_tenggat_keras(pembantu):
    mulai = time.monotonic()
    with pytest.raises(GalatPembantu) as e:
        pembantu.jalankan("tidur", timeout=1)
    assert e.value.kode == "waktu"
    assert time.monotonic() - mulai < 15


def test_keluaran_dibatasi(pembantu):
    assert len(pembantu.jalankan("berisik")) == BATAS_KELUARAN


def test_db_impor_mengalirkan_berkas_berurutan(pembantu, catatan, tmp_path):
    a, b = tmp_path / "a.sql", tmp_path / "b.sql"
    a.write_bytes(b"SET NAMES utf8mb4;\n")
    b.write_bytes(b"INSERT INTO t VALUES (1);\n")
    pembantu.db_impor("toko", [a, b])
    assert catatan()[0] == {"argv": ["db-impor", "toko"], "stdin": "SET NAMES utf8mb4;\nINSERT INTO t VALUES (1);\n"}


def test_status_diurai_dan_disaring(pembantu, monkeypatch):
    monkeypatch.setenv("PALSU_STATUS", json.dumps({
        "mem_tersedia": 4294967296, "disk_total": 200, "disk_bebas": 60,
        "container": {"wp-toko": {"berjalan": True}, "wp-lain": {"berjalan": "ya"}, "wp-../x": {"berjalan": True},
                      "wpmgr-stg-db": {"berjalan": True}},
        "akses": {"toko": 1790000000, "JAHAT": 1, "lain": "x"},
    }))
    s = pembantu.status()
    assert (s.mem_tersedia, s.disk_total, s.disk_bebas) == (4294967296, 200, 60)
    assert s.container == {"toko": True, "lain": False}
    assert s.akses == {"toko": 1790000000}


@pytest.mark.parametrize("teks", [
    "bukan json", "[]", json.dumps({"mem_tersedia": -1, "disk_total": 1, "disk_bebas": 1}),
    json.dumps({"mem_tersedia": True, "disk_total": 1, "disk_bebas": 1}), json.dumps({"disk_total": 1}),
])
def test_status_rusak_ditolak(teks):
    with pytest.raises(GalatPembantu) as e:
        urai_status(teks)
    assert e.value.kode == "status"


def test_sandi_dan_htpasswd():
    sandi = sandi_baru()
    assert len(sandi) >= 16
    h = hash_sandi(sandi)
    assert re.fullmatch(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}", h)
    assert bcrypt.checkpw(sandi.encode(), h.encode())


def test_tulis_dan_hapus_akses_router(tmp_path):
    h = hash_sandi("rahasia-preview")
    tulis_akses_router(tmp_path, "toko", h, "e" * 64)
    assert (tmp_path / "router" / "toko.htpasswd").read_bytes() == f"staging:{h}\n".encode()
    assert (tmp_path / "router" / "toko.rahasia").read_bytes() == b"e" * 64
    with pytest.raises(ValueError):
        tulis_akses_router(tmp_path, "Toko", h, "e" * 64)
    with pytest.raises(ValueError):
        tulis_akses_router(tmp_path, "toko", h + "\nserver {", "e" * 64)
    with pytest.raises(ValueError):
        tulis_akses_router(tmp_path, "toko", h, "bukan-hex")
    hapus_akses_router(tmp_path, "toko")
    assert not (tmp_path / "router" / "toko.htpasswd").exists()


def test_tautan_masuk_sesuai_secure_link_nginx():
    rahasia, host = "e" * 64, "toko.staging.contoh.id"
    tautan = tautan_masuk(rahasia, host, "abc.def+/", 1_790_000_000)
    e = 1_790_000_000 + 43200
    m = base64.urlsafe_b64encode(hashlib.md5(f"{e}{host} {rahasia}".encode()).digest()).decode().rstrip("=")
    assert tautan == f"/__wpmgr_masuk?e={e}&m={m}&sso=abc.def%2B%2F"
    assert cookie_akses(rahasia, host, 1_790_000_000) == {"wpmgr_stg_m": m, "wpmgr_stg_e": str(e)}
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_staging_aman.py tests/unit/test_staging_pembantu.py -q`. Expected: `ModuleNotFoundError: No module named 'wpmgr.staging'`.

- [ ] **Step 3: Modul validasi.**

File: `src/wpmgr/staging/__init__.py`
```python
"""Lapis 3: salinan staging per site (spec 2026-09-26-wp-manager-lapis3-design.md)."""
```

File: `src/wpmgr/staging/aman.py`
```python
"""Validasi dan pembersihan untuk staging (spec §13).

Semua yang datang dari connector -- path, nama tabel, angka, teks -- adalah
masukan penyerang: site produksi bisa saja sudah disusupi. Aturan path di
sini adalah cermin WPMGR_Staging_Path di connector; keduanya harus tetap
sama ketatnya.
"""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

POLA_NAMA = re.compile(r"[a-z0-9-]{1,40}")
POLA_ID_DORONG = re.compile(r"[0-9a-f]{32}")
POLA_TABEL = re.compile(r"[A-Za-z0-9_$]{1,64}")
POLA_SHA256 = re.compile(r"[0-9a-f]{64}")
_POLA_ANGKA = re.compile(r"-?[0-9]{1,20}")
_POLA_VERSI = re.compile(r"([0-9]{1,2})\.([0-9]{1,2})")
_KENDALI = re.compile(r"[\x00-\x1f\x7f\\]")
_POLA_WP_AKAR = re.compile(r"wp-[a-z0-9-]+\.php")

VERSI_PHP = ("7.4", "8.0", "8.1", "8.2", "8.3")
VERSI_PHP_BAWAAN = "8.1"
MAKS_PATH = 1024
MAKS_SEGMEN = 255
BACKUP_KONTEN = frozenset({"updraft", "ai1wm-backups", "wpvividbackups"})
# Berkas milik staging sendiri: tidak pernah dihapus/ditimpa saat tarik.
DILINDUNGI_STAGING = frozenset({"wp-config.php", "wp-content/mu-plugins/wpmgr-staging.php"})
TIDAK_PERNAH_DITULIS = frozenset({
    "wp-config.php", ".maintenance",
    "wp-content/mu-plugins/wpmgr-staging.php", "wp-content/mu-plugins/wpmgr-dorong-aman.php",
})
AKAR_INTI = frozenset({"index.php", "xmlrpc.php", "license.txt", "readme.html", ".htaccess"})


class PathTidakAman(ValueError):
    pass


def nama_sah(nama) -> bool:
    return isinstance(nama, str) and POLA_NAMA.fullmatch(nama) is not None


def nama_dari_url(url: str) -> str:
    """Label subdomain staging dari host produksi (spec §5.1)."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        host = ""
    host = host.removeprefix("www.")
    nama = re.sub(r"[^a-z0-9-]+", "-", host.replace(".", "-"))
    nama = re.sub(r"-{2,}", "-", nama).strip("-")[:40].strip("-")
    return nama or "situs"


def versi_php_staging(versi: str | None) -> tuple[str, bool]:
    """Versi image staging: sama dengan produksi, atau terdekat yang lebih tinggi (spec §7.1)."""
    cocok = _POLA_VERSI.match(versi or "")
    if cocok is None:
        return VERSI_PHP_BAWAAN, True
    diminta = (int(cocok.group(1)), int(cocok.group(2)))
    for v in VERSI_PHP:
        besar, kecil = (int(x) for x in v.split("."))
        if (besar, kecil) == diminta:
            return v, False
        if (besar, kecil) > diminta:
            return v, True
    return VERSI_PHP[-1], True


def bersih_teks(nilai, panjang: int) -> str | None:
    """Teks yang aman disimpan di kolom text PostgreSQL.

    PostgreSQL menolak NUL, dan psycopg menolak surrogate tunggal (bisa
    dikirim lewat escape \\ud800 di JSON). Keduanya dibuang di sini, sebelum
    dipotong, supaya satu nilai beracun tidak menggagalkan seluruh commit.
    """
    if nilai is None:
        return None
    teks = nilai if isinstance(nilai, str) else str(nilai)
    teks = teks.encode("utf-8", "replace").decode("utf-8").replace("\x00", "")
    return teks[:panjang]


def angka(nilai, bawah: int, atas: int) -> int | None:
    if isinstance(nilai, bool):
        return None
    if isinstance(nilai, int):
        n = nilai
    elif isinstance(nilai, str) and _POLA_ANGKA.fullmatch(nilai):
        n = int(nilai)
    else:
        return None
    return max(bawah, min(atas, n))


def path_sah(p) -> str:
    if not isinstance(p, str) or not p:
        raise PathTidakAman("Path kosong atau bukan teks")
    try:
        mentah = p.encode("utf-8")
    except UnicodeEncodeError:
        raise PathTidakAman("Path bukan UTF-8 yang sah") from None
    if len(mentah) > MAKS_PATH:
        raise PathTidakAman("Path terlalu panjang")
    if _KENDALI.search(p):
        raise PathTidakAman("Path memuat karakter terlarang")
    if p.startswith("/") or re.match(r"[A-Za-z]:", p):
        raise PathTidakAman("Path absolut ditolak")
    for segmen in p.split("/"):
        if segmen in ("", ".", ".."):
            raise PathTidakAman("Path memuat segmen terlarang")
        if len(segmen.encode("utf-8")) > MAKS_SEGMEN:
            raise PathTidakAman("Nama berkas terlalu panjang")
    return p


def dikecualikan(p: str) -> bool:
    if p in ("wp-config.php", ".maintenance") or p.lower().endswith(".log"):
        return True
    for d in ("wp-content/cache", "wp-content/wpmgr-dorong"):
        if p == d or p.startswith(d + "/"):
            return True
    bagian = p.split("/")
    return len(bagian) >= 2 and bagian[0] == "wp-content" and (
        bagian[1] in BACKUP_KONTEN or bagian[1].startswith("backups-dup-")
    )


def boleh_didorong(p) -> bool:
    try:
        path_sah(p)
    except PathTidakAman:
        return False
    if dikecualikan(p) or p in TIDAK_PERNAH_DITULIS:
        return False
    if p.startswith("wp-content/plugins/wp-manager-connector/"):
        return False
    if p.startswith(("wp-content/", "wp-admin/", "wp-includes/")):
        return True
    if "/" in p:
        return False
    return p in AKAR_INTI or _POLA_WP_AKAR.fullmatch(p) is not None


def jalur_di_dalam(akar: Path, relatif: str) -> Path:
    """Path tulis di bawah `akar`, tidak pernah lewat symlink dan tidak pernah keluar."""
    path_sah(relatif)
    bagian = relatif.split("/")
    sekarang = akar
    for b in bagian:
        sekarang = sekarang / b
        if sekarang.is_symlink():
            raise PathTidakAman("Path melewati symlink")
    akar_nyata = Path(os.path.realpath(akar))
    induk = Path(os.path.realpath(sekarang.parent))
    if induk != akar_nyata and akar_nyata not in induk.parents:
        raise PathTidakAman("Path keluar dari akar staging")
    return sekarang
```

- [ ] **Step 4: Pembungkus pembantu.**

File: `src/wpmgr/staging/pembantu.py`
```python
"""Pembungkus skrip pembantu wpmgr-staging (spec §7.3, Task 10).

Proses dashboard tidak punya akses Docker. Semua yang menyentuh container
lewat satu skrip root yang dipanggil `sudo -n`, dengan argumen yang sudah
divalidasi di sini (lapis pertama) dan divalidasi lagi di skrip (lapis
yang menentukan). Setiap panggilan punya tenggat keras, dan keluarannya
dibatasi ukurannya.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import bcrypt

from wpmgr.config import Settings, get_settings
from wpmgr.staging.aman import POLA_NAMA, VERSI_PHP, angka, bersih_teks, nama_sah

KODE_KELUAR = {2: "argumen", 3: "ditolak", 4: "docker", 5: "sertifikat", 6: "impor",
               7: "konfigurasi", 8: "wpcli"}
PESAN_UMUM = {
    "argumen": "Skrip pembantu menolak argumen permintaan ini.",
    "ditolak": "Skrip pembantu menolak permintaan ini.",
    "docker": "Perintah Docker di server staging gagal.",
    "sertifikat": "Sertifikat staging belum dapat diterbitkan.",
    "impor": "Impor database staging gagal.",
    "konfigurasi": "Konfigurasi skrip pembantu di server belum lengkap.",
    "wpcli": "Perintah wp-cli di staging gagal.",
    "lain": "Skrip pembantu tidak dapat dijalankan (periksa pemasangan dan sudoers).",
}
_POLA_GALAT = re.compile(r"GALAT ([a-z]+): (.{1,300})")
_POLA_PREFIX = re.compile(r"[A-Za-z0-9_]{1,20}")
_POLA_HASH = re.compile(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}")
_POLA_RAHASIA = re.compile(r"[0-9a-f]{64}")

BATAS_KELUARAN = 4 * 1024 * 1024
TIMEOUT_BAWAAN = 120
TIMEOUT_SIAPKAN = 900
TIMEOUT_BUAT = 600
TIMEOUT_IMPOR = 3 * 3600
TIMEOUT_WPCLI = 900
TIMEOUT_SERTIFIKAT = 300
UMUR_TAUTAN = 12 * 3600


class GalatPembantu(Exception):
    def __init__(self, kode: str, pesan: str) -> None:
        super().__init__(pesan)
        self.kode = kode
        self.pesan = pesan


@dataclass(frozen=True)
class StatusPembantu:
    mem_tersedia: int
    disk_total: int
    disk_bebas: int
    container: dict[str, bool]
    akses: dict[str, int]


def _cek_nama(nama) -> str:
    if not nama_sah(nama):
        raise ValueError("Nama staging tidak sah")
    return nama


def _cek_id(site_id) -> str:
    teks = str(site_id)
    if str(uuid.UUID(teks)) != teks:
        raise ValueError("Id site tidak sah")
    return teks


def urai_status(teks: str) -> StatusPembantu:
    try:
        data = json.loads(teks)
    except ValueError:
        raise GalatPembantu("status", "Status server staging tidak terbaca.") from None
    if not isinstance(data, dict):
        raise GalatPembantu("status", "Status server staging tidak terbaca.")
    nilai = {}
    for kunci in ("mem_tersedia", "disk_total", "disk_bebas"):
        mentah = data.get(kunci)
        n = angka(mentah, 0, 2**53)
        if n is None or not isinstance(mentah, int) or mentah < 0:
            raise GalatPembantu("status", "Status server staging tidak lengkap.")
        nilai[kunci] = n
    container = {}
    for k, v in (data.get("container") or {}).items() if isinstance(data.get("container"), dict) else ():
        if isinstance(k, str) and k.startswith("wp-") and POLA_NAMA.fullmatch(k[3:]) and isinstance(v, dict):
            container[k[3:]] = v.get("berjalan") is True
    akses = {}
    for k, v in (data.get("akses") or {}).items() if isinstance(data.get("akses"), dict) else ():
        n = angka(v, 0, 2**40) if isinstance(v, int) else None
        if isinstance(k, str) and POLA_NAMA.fullmatch(k) and n is not None:
            akses[k] = n
    return StatusPembantu(container=container, akses=akses, **nilai)


class Pembantu:
    def __init__(self, perintah: list[str]) -> None:
        self.perintah = list(perintah)

    @classmethod
    def dari_setelan(cls, s: Settings | None = None) -> "Pembantu":
        s = s or get_settings()
        if s.staging_pembantu_awalan:
            return cls(shlex.split(s.staging_pembantu_awalan))
        return cls(["sudo", "-n", s.staging_pembantu])

    def _galat(self, kode_keluar: int, stderr: str) -> GalatPembantu:
        kode = KODE_KELUAR.get(kode_keluar, "lain")
        pesan = None
        for baris in reversed(stderr.splitlines()):
            cocok = _POLA_GALAT.fullmatch(baris.strip())
            if cocok and cocok.group(1) == kode:
                pesan = bersih_teks(cocok.group(2), 300)
                break
        return GalatPembantu(kode, pesan or PESAN_UMUM[kode])

    def jalankan(self, *argumen: str, masukan: Iterable[Path] = (), timeout: float = TIMEOUT_BAWAAN) -> str:
        masukan = list(masukan)
        with tempfile.TemporaryFile() as keluar, tempfile.TemporaryFile() as galat:
            try:
                proses = subprocess.Popen(
                    [*self.perintah, *argumen],
                    stdin=subprocess.PIPE if masukan else subprocess.DEVNULL,
                    stdout=keluar, stderr=galat,
                )
            except OSError:
                # Pesan OSError memuat path biner; UI hanya mendapat pesan tetap.
                raise GalatPembantu("lain", PESAN_UMUM["lain"]) from None
            habis = threading.Event()

            def bunuh() -> None:
                habis.set()
                proses.kill()

            pengawas = threading.Timer(timeout, bunuh)
            pengawas.daemon = True
            pengawas.start()
            try:
                if masukan:
                    try:
                        for berkas in masukan:
                            with open(berkas, "rb") as f:
                                shutil.copyfileobj(f, proses.stdin, 1 << 20)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        # Proses berhenti lebih dulu; kode keluarnya yang menjelaskan.
                        pass
                    finally:
                        try:
                            proses.stdin.close()
                        except OSError:
                            pass
                kode = proses.wait()
            finally:
                pengawas.cancel()
            if habis.is_set():
                raise GalatPembantu("waktu", f"Skrip pembantu tidak selesai dalam {int(timeout)} detik.")
            keluar.seek(0)
            teks = keluar.read(BATAS_KELUARAN).decode("utf-8", "replace")
            galat.seek(0)
            teks_galat = galat.read(64 * 1024).decode("utf-8", "replace")
        if kode != 0:
            raise self._galat(kode, teks_galat)
        return teks

    def siapkan(self) -> str:
        return self.jalankan("siapkan", timeout=TIMEOUT_SIAPKAN)

    def buat(self, nama: str, versi_php: str, site_id) -> str:
        _cek_nama(nama)
        if versi_php not in VERSI_PHP:
            raise ValueError("Versi PHP staging tidak didukung")
        return self.jalankan("buat", nama, versi_php, _cek_id(site_id), timeout=TIMEOUT_BUAT)

    def jalan(self, nama: str) -> str:
        return self.jalankan("jalan", _cek_nama(nama))

    def jeda(self, nama: str) -> str:
        return self.jalankan("jeda", _cek_nama(nama))

    def hapus(self, nama: str) -> str:
        return self.jalankan("hapus", _cek_nama(nama))

    def db_buat(self, nama: str, site_id, prefix: str) -> str:
        _cek_nama(nama)
        if not isinstance(prefix, str) or not _POLA_PREFIX.fullmatch(prefix):
            raise ValueError("Prefix tabel tidak sah")
        return self.jalankan("db-buat", nama, _cek_id(site_id), prefix)

    def db_hapus(self, nama: str) -> str:
        return self.jalankan("db-hapus", _cek_nama(nama))

    def db_impor(self, nama: str, berkas: list[Path]) -> str:
        return self.jalankan("db-impor", _cek_nama(nama), masukan=berkas, timeout=TIMEOUT_IMPOR)

    def wpcli(self, nama: str, *argumen: str) -> str:
        return self.jalankan("wpcli", _cek_nama(nama), *argumen, timeout=TIMEOUT_WPCLI)

    def router_muat(self) -> str:
        return self.jalankan("router-muat")

    def sertifikat(self, nama: str) -> str:
        return self.jalankan("sertifikat", _cek_nama(nama), timeout=TIMEOUT_SERTIFIKAT)

    def status(self) -> StatusPembantu:
        return urai_status(self.jalankan("status"))


def sandi_baru() -> str:
    return secrets.token_urlsafe(12)


def hash_sandi(sandi: str) -> str:
    return bcrypt.hashpw(sandi.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")


def _tulis_atomik(path: Path, teks: str) -> None:
    sementara = path.with_name(path.name + ".tmp")
    # Byte apa adanya: write_text() di Windows mengubah "\n" menjadi "\r\n",
    # dan skrip pembantu (Linux) menolak baris htpasswd yang berakhiran "\r".
    sementara.write_bytes(teks.encode("ascii"))
    os.chmod(sementara, 0o600)
    os.replace(sementara, path)


def tulis_akses_router(dir_staging: Path, nama: str, sandi_hash: str, rahasia: str) -> None:
    """Dua berkas yang dibaca `router-muat` (Koreksi #6); isinya divalidasi di dua sisi."""
    _cek_nama(nama)
    if not isinstance(sandi_hash, str) or not _POLA_HASH.fullmatch(sandi_hash):
        raise ValueError("Hash kata sandi tidak sah")
    if not isinstance(rahasia, str) or not _POLA_RAHASIA.fullmatch(rahasia):
        raise ValueError("Rahasia router tidak sah")
    d = Path(dir_staging) / "router"
    d.mkdir(parents=True, exist_ok=True)
    _tulis_atomik(d / f"{nama}.htpasswd", f"staging:{sandi_hash}\n")
    _tulis_atomik(d / f"{nama}.rahasia", rahasia)


def hapus_akses_router(dir_staging: Path, nama: str) -> None:
    _cek_nama(nama)
    for akhiran in (".htpasswd", ".rahasia"):
        (Path(dir_staging) / "router" / f"{nama}{akhiran}").unlink(missing_ok=True)


def _md5_tautan(rahasia: str, host: str, kedaluwarsa: int) -> str:
    # Sama persis dengan `secure_link_md5 "$secure_link_expires$host <rahasia>"`
    # di template router: base64url tanpa '='.
    inti = hashlib.md5(f"{kedaluwarsa}{host} {rahasia}".encode()).digest()
    return base64.urlsafe_b64encode(inti).decode("ascii").rstrip("=")


def tautan_masuk(rahasia: str, host: str, token_sso: str, sekarang: int, umur: int = UMUR_TAUTAN) -> str:
    e = int(sekarang) + umur
    return f"/__wpmgr_masuk?e={e}&m={_md5_tautan(rahasia, host, e)}&sso={quote(token_sso, safe='')}"


def cookie_akses(rahasia: str, host: str, sekarang: int, umur: int = UMUR_TAUTAN) -> dict[str, str]:
    e = int(sekarang) + umur
    return {"wpmgr_stg_m": _md5_tautan(rahasia, host, e), "wpmgr_stg_e": str(e)}
```

- [ ] **Step 5: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/unit/test_staging_aman.py tests/unit/test_staging_pembantu.py -q`. Expected: semua lulus (test symlink `skipped` di Windows tanpa Developer Mode). Lalu `ruff check .`.

- [ ] **Step 6: Commit.**

```bash
git add src/wpmgr/staging tests/unit/test_staging_aman.py tests/unit/test_staging_pembantu.py tests/unit/pembantu_palsu.py
git commit -m "feat(staging): validasi path/nama dan pembungkus skrip pembantu"
```

---

### Task 12: Metode staging `SiteClient`, format paket, indeks lokal, dan perencanaan murni

**Files:**
- Create: `src/wpmgr/staging/paket.py`, `src/wpmgr/staging/rencana.py`, `src/wpmgr/staging/indeks.py`, `tests/unit/test_staging_paket.py`, `tests/unit/test_staging_rencana.py`, `tests/unit/test_staging_indeks.py`, `tests/unit/test_site_client_staging.py`
- Modify: `src/wpmgr/errors.py`, `src/wpmgr/site_client.py`, `src/wpmgr/fitur.py`

**Interfaces:**
- Consumes: Task 2–8 (bentuk endpoint), Task 11 (`aman`).
- Produces:
  - `wpmgr.errors`:
    - konstanta kelas galat `STAGING_MATI = "staging_mati"`, `STAGING_DITOLAK = "staging_ditolak"`, `STAGING_GAGAL = "staging_gagal"`, `TERLALU_BESAR = "terlalu_besar"`, `BERKAS_HILANG = "berkas_hilang"`;
    - `KELAS_STAGING` (kelimanya);
    - kode connector `KODE_STAGING_MATI = "wpmgr_staging_mati"`, `KODE_TERLALU_BESAR = "wpmgr_staging_terlalu_besar"`, `KODE_STAGING_TIDAK_ADA = "wpmgr_staging_tidak_ada"`.
    `klasifikasi_respons` memetakan ketiga kode itu sebelum cabang 401/403 dan 4xx.
  - `wpmgr.fitur.STAGING = "staging"`.
  - `wpmgr.staging.paket`: `MAGIC`, `class PaketRusak(ValueError)`, `susun(meta: dict, bagian: list[bytes]) -> bytes`, `urai(data: bytes) -> tuple[dict, list[bytes]]`.
  - `wpmgr.staging.rencana`:
    - entri dan selisih: `@dataclass(frozen=True) Entri(path, ukuran, mtime, hash)`, `entri_dari(item) -> Entri | None`, `berubah(a, b) -> bool`, `@dataclass Selisih(baru, berubah, hapus)` dengan properti `diambil` dan `byte`, `selisih(produksi, lokal) -> Selisih`;
    - potongan: `@dataclass(frozen=True) Potongan(jenis, berkas, dari, panjang)` dengan properti `ukuran`, `bagi_potongan(entri, ukuran_paket=8 MiB, maks_berkas=500) -> list[Potongan]`;
    - rencana dorong: `AWALAN_KODE`, `@dataclass RencanaDorong(ganti, hapus, db)` dengan properti `byte`, `rencana_dorong(mode, staging, produksi) -> RencanaDorong`;
    - tanda air: `urai_tanda_air(data) -> dict | None`, `bandingkan_tanda_air(lama, baru) -> list[str]`;
    - pemeriksaan sumber daya: `cek_ram(status) -> str | None`, `cek_disk(status, tambahan: int) -> str | None`, `cek_maks_aktif(jumlah_aktif, maks) -> str | None`;
    - `format_byte(n) -> str` (mis. `"1,2 GB"`).
  - `wpmgr.staging.indeks`: `class Indeks(berkas: Path)` dengan `muat() -> dict[str, Entri]`, `catat(entri)`, `catat_hapus(path)`, `padatkan(isi)`; `sha256_berkas(path) -> str`; `pindai_lokal(akar: Path, indeks: dict[str, Entri]) -> dict[str, Entri]`.
  - `SiteClient`:
    - baca: `staging_manifest(kursor=None, batas=5000) -> dict`, `staging_file(paths) -> tuple[dict, list[bytes]]`, `staging_rentang(path, dari, panjang) -> tuple[dict, list[bytes]]`, `staging_tabel(tabel, kursor) -> tuple[dict, list[bytes]]`, `staging_tanda_air(posts_sejak=None, posts_maks=None) -> dict`, `staging_snapshot(paths, awal=False) -> dict`;
    - tulis: `staging_unggah(isi: bytes) -> dict`, `staging_terapkan(badan: dict, token_lewati: str | None = None) -> dict`, `staging_bersihkan(dorong_id) -> dict`.
    Setiap respons dibaca sebagai stream dengan batas (JSON 32 MB, biner 12 MB). Paket yang rusak atau hash-nya tidak cocok menjadi `SiteError(BAD_RESPONSE)`, sehingga bisa diulang.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/unit/test_staging_paket.py`
```python
import hashlib
import json

import pytest

from wpmgr.staging.paket import MAGIC, PaketRusak, susun, urai


def test_bolak_balik_dengan_hash_per_bagian():
    bagian = [b"", b"biner\x00\xff\x1a\n'\""]
    data = susun({"jenis": "uji", "berkas": [{"path": "a"}, {"path": "wp-content/ü.bin"}]}, bagian)
    assert data.startswith(MAGIC)
    meta, hasil = urai(data)
    assert hasil == bagian
    assert meta["berkas"][1]["sha256"] == hashlib.sha256(bagian[1]).hexdigest()
    assert meta["berkas"][0]["ukuran"] == 0
    assert meta["berkas"][1]["path"] == "wp-content/ü.bin"


def _kepala(meta: dict) -> bytes:
    j = json.dumps(meta).encode()
    return MAGIC + f"{len(j):08x}\n".encode() + j


@pytest.mark.parametrize("data", [
    b"", b"BUKAN", MAGIC + b"zzzzzzzz\n{}", MAGIC + b"7fffffff\n{}",
    _kepala([]), _kepala({"berkas": "x"}),
    _kepala({"berkas": [{"ukuran": True, "sha256": "0" * 64}]}),
    _kepala({"berkas": [{"ukuran": 5, "sha256": "0" * 64}]}) + b"abc",
    _kepala({"berkas": [{"ukuran": 1, "sha256": "bukan-hex"}]}) + b"a",
])
def test_paket_rusak_ditolak(data):
    with pytest.raises(PaketRusak):
        urai(data)


def test_hash_salah_dan_sisa_data_ditolak():
    data = susun({"berkas": [{"path": "a"}]}, [b"abcdef"])
    with pytest.raises(PaketRusak):
        urai(data[:-1] + b"X")
    with pytest.raises(PaketRusak):
        urai(data + b"x")
```

File: `tests/unit/test_staging_rencana.py`
```python
from wpmgr.staging.pembantu import StatusPembantu
from wpmgr.staging.rencana import (
    Entri,
    bagi_potongan,
    bandingkan_tanda_air,
    cek_disk,
    cek_maks_aktif,
    cek_ram,
    entri_dari,
    format_byte,
    rencana_dorong,
    selisih,
    urai_tanda_air,
)

H1, H2 = "1" * 64, "2" * 64
GB = 1024**3


def e(path, ukuran=10, mtime=100, h=H1):
    return Entri(path, ukuran, mtime, h)


def test_entri_dari_menolak_masukan_berbahaya():
    assert entri_dari({"path": "a.php", "ukuran": 3, "mtime": 5, "hash": H1}) == Entri("a.php", 3, 5, H1)
    assert entri_dari({"path": "big.bin", "ukuran": 3, "mtime": 5, "hash": None}).hash is None
    for buruk in ({"path": "../x", "ukuran": 1, "mtime": 1}, {"path": "wp-config.php", "ukuran": 1, "mtime": 1},
                  {"path": "a", "ukuran": -1, "mtime": 1}, {"path": "a", "ukuran": True, "mtime": 1},
                  {"path": "a", "ukuran": 1, "mtime": "1"}, {"path": "a", "ukuran": 2**51, "mtime": 1},
                  {"path": "a", "ukuran": 1, "mtime": 1, "hash": "XYZ"}, {"path": "a\ud800", "ukuran": 1, "mtime": 1},
                  "bukan-dict", None):
        assert entri_dari(buruk) is None, buruk


def test_selisih_baru_berubah_hapus_dan_perlindungan():
    produksi = {p.path: p for p in [e("a"), e("b", h=H2), e("besar", ukuran=99, mtime=7, h=None), e("c")]}
    lokal = {p.path: p for p in [e("b"), e("besar", ukuran=99, mtime=8, h=None), e("c"), e("lama"),
                                 e("wp-config.php"), e("wp-content/mu-plugins/wpmgr-staging.php")]}
    s = selisih(produksi, lokal)
    assert [x.path for x in s.baru] == ["a"]
    assert [x.path for x in s.berubah] == ["b", "besar"]
    assert s.hapus == ["lama"]
    assert s.byte == 10 + 10 + 99


def test_hash_menang_atas_mtime_bila_keduanya_ada():
    s = selisih({"a": e("a", mtime=1)}, {"a": e("a", mtime=2)})
    assert s.berubah == []


def test_bagi_potongan_paket_dan_rentang():
    entri = [e("a", 3), e("b", 4), e("besar", 20, h=None), e("c", 5), e("d", 0)]
    p = bagi_potongan(entri, ukuran_paket=8, maks_berkas=2)
    ringkas = [(x.jenis, tuple(b.path for b in x.berkas), x.dari, x.panjang) for x in p]
    # Berkas besar langsung menjadi rentang; paket kecil yang sedang dikumpulkan
    # baru dikirim saat penuh (ukuran atau jumlah berkas).
    assert ringkas == [
        ("rentang", ("besar",), 0, 8), ("rentang", ("besar",), 8, 8), ("rentang", ("besar",), 16, 4),
        ("paket", ("a", "b"), 0, 0),
        ("paket", ("c", "d"), 0, 0),
    ]
    assert [x.ukuran for x in p] == [8, 8, 4, 7, 5]


def test_rencana_hanya_kode():
    staging = {x.path: x for x in [
        e("wp-content/themes/t/style.css", h=H2), e("wp-content/plugins/p/baru.php"),
        e("wp-content/uploads/2026/baru.jpg"), e("wp-content/uploads/2026/ubah.jpg", h=H2),
        e("wp-content/plugins/wp-manager-connector/x.php", h=H2), e("wp-content/mu-plugins/wpmgr-staging.php"),
        e("wp-includes/version.php", h=H2), e("wp-config.php", h=H2),
    ]}
    produksi = {x.path: x for x in [
        e("wp-content/themes/t/style.css"), e("wp-content/plugins/p/lama.php"),
        e("wp-content/uploads/2026/ubah.jpg"), e("wp-content/plugins/wp-manager-connector/x.php"),
        e("wp-includes/version.php"), e("wp-content/uploads/pesanan.pdf"),
    ]}
    r = rencana_dorong("hanya_kode", staging, produksi)
    assert [x.path for x in r.ganti] == [
        "wp-content/plugins/p/baru.php", "wp-content/themes/t/style.css", "wp-content/uploads/2026/baru.jpg"]
    assert r.hapus == ["wp-content/plugins/p/lama.php"]
    assert r.db is False


def test_rencana_timpa_penuh_tidak_menghapus_uploads_produksi():
    staging = {x.path: x for x in [e("index.php", h=H2), e("wp-content/themes/t/a.css"), e("lain.php")]}
    produksi = {x.path: x for x in [e("index.php"), e("wp-content/themes/t/b.css"),
                                    e("wp-content/uploads/pesanan.pdf"), e("google123.html")]}
    r = rencana_dorong("timpa_penuh", staging, produksi)
    assert [x.path for x in r.ganti] == ["index.php", "wp-content/themes/t/a.css"]
    assert r.hapus == ["wp-content/themes/t/b.css"]
    assert r.db is True


def test_tanda_air_diurai_dan_dibandingkan():
    lama = urai_tanda_air({"sumber": {
        "posts": {"maks_id": 100, "jumlah": 50, "diubah": "2026-09-20 00:00:00", "diubah_sejak": None},
        "comments": {"maks_id": 10, "jumlah": 8}, "users": {"maks_id": 3, "jumlah": 3},
        "pesanan": {"maks_id": 500, "jumlah": 400, "sumber": "hpos"},
    }})
    baru = urai_tanda_air({"sumber": {
        "posts": {"maks_id": 100, "jumlah": 50, "diubah": "2026-09-25 00:00:00", "diubah_sejak": 2},
        "comments": {"maks_id": 22, "jumlah": 20}, "users": {"maks_id": 4, "jumlah": 4},
        "pesanan": {"maks_id": 503, "jumlah": 403, "sumber": "hpos"},
        "gravity_forms": {"maks_id": 5, "jumlah": 5}, "sumber_aneh": {"maks_id": 1, "jumlah": 1},
    }})
    assert "sumber_aneh" not in baru["sumber"]
    assert bandingkan_tanda_air(lama, baru) == [
        "3 pesanan baru", "12 komentar baru", "1 user baru",
        "sumber baru: isian Gravity Forms (5 entri)", "2 post diubah",
    ]
    assert bandingkan_tanda_air(lama, lama) == []


def test_tanda_air_rusak_dan_tidak_ada():
    assert urai_tanda_air({"sumber": {"posts": {"maks_id": "x", "jumlah": 1}}}) == {"sumber": {}}
    assert urai_tanda_air("bukan") is None
    assert bandingkan_tanda_air(None, {"sumber": {}})[0].startswith("Tanda air saat tarik tidak tersedia")


def test_cek_sumber_daya_dengan_angka_jelas():
    st = StatusPembantu(mem_tersedia=int(1.5 * GB), disk_total=100 * GB, disk_bebas=20 * GB, container={}, akses={})
    assert cek_ram(st) == "RAM tersedia di VPS 1,5 GB; minimal 2,0 GB untuk menjalankan staging."
    assert cek_disk(st, 6 * GB) == "Sisa disk sesudah tarik akan 14,0 GB (14% dari 100,0 GB); minimal 15%."
    assert cek_disk(st, 4 * GB) is None
    assert cek_ram(StatusPembantu(3 * GB, 1, 1, {}, {})) is None
    assert cek_maks_aktif(3, 3) == "Sudah ada 3 staging aktif (batas 3). Jeda salah satu dulu."
    assert cek_maks_aktif(2, 3) is None


def test_format_byte():
    assert format_byte(0) == "0 B"
    assert format_byte(1536) == "1,5 KB"
    assert format_byte(int(2.25 * GB)) == "2,2 GB"
```

File: `tests/unit/test_staging_indeks.py`
```python
import hashlib
import os

import pytest

from wpmgr.staging.indeks import Indeks, pindai_lokal
from wpmgr.staging.rencana import Entri


def test_catat_muat_hapus_dan_padatkan(tmp_path):
    ind = Indeks(tmp_path / "indeks.jsonl")
    assert ind.muat() == {}
    ind.catat(Entri("a.php", 3, 5, "1" * 64))
    ind.catat(Entri("b.php", 4, 6, None))
    ind.catat(Entri("a.php", 7, 8, "2" * 64))
    ind.catat_hapus("b.php")
    with open(tmp_path / "indeks.jsonl", "a", encoding="utf-8") as f:
        f.write("bukan json\n")
        f.write('{"p": "../x", "u": 1, "m": 1}\n')
        f.write('{"p": "c.php", "u": 1')  # baris terpotong karena crash
    isi = ind.muat()
    assert isi == {"a.php": Entri("a.php", 7, 8, "2" * 64)}
    ind.padatkan(isi)
    assert (tmp_path / "indeks.jsonl").read_text(encoding="utf-8").count("\n") == 1
    assert ind.muat() == isi


def test_pindai_lokal_memakai_ulang_hash_indeks(tmp_path):
    akar = tmp_path / "files"
    (akar / "wp-content").mkdir(parents=True)
    (akar / "index.php").write_bytes(b"<?php")
    (akar / "wp-content" / "debug.log").write_bytes(b"log")
    (akar / "wp-content" / "a.css").write_bytes(b"body{}")
    os.utime(akar / "index.php", (1000, 1000))
    lama = {"index.php": Entri("index.php", 5, 1000, "f" * 64)}
    hasil = pindai_lokal(akar, lama)
    assert set(hasil) == {"index.php", "wp-content/a.css"}
    assert hasil["index.php"].hash == "f" * 64
    assert hasil["wp-content/a.css"].hash == hashlib.sha256(b"body{}").hexdigest()


def test_pindai_lokal_melewati_symlink(tmp_path):
    akar = tmp_path / "files"
    akar.mkdir()
    (tmp_path / "luar.txt").write_bytes(b"x")
    try:
        os.symlink(tmp_path / "luar.txt", akar / "tautan.txt")
    except OSError:
        pytest.skip("symlink tidak diizinkan di sistem ini")
    assert pindai_lokal(akar, {}) == {}
```

File: `tests/unit/test_site_client_staging.py`
```python
import json

import httpx
import pytest

from wpmgr.errors import (
    BAD_RESPONSE,
    BERKAS_HILANG,
    STAGING_MATI,
    TERLALU_BESAR,
    TRANSIENT,
    UNKNOWN,
    SiteError,
)
from wpmgr.signing import verify
from wpmgr.site_client import SiteClient
from wpmgr.staging.paket import susun

SECRET = "f" * 64


def klien(handler):
    return SiteClient("https://contoh.test", "s1", SECRET, client=httpx.Client(transport=httpx.MockTransport(handler)))


def _cek_tanda_tangan(r: httpx.Request):
    assert verify(SECRET, r.headers["X-Wpmgr-Signature"], r.method, r.url.path, int(r.headers["X-Wpmgr-Timestamp"]),
                  r.headers["X-Wpmgr-Nonce"], r.content)


def test_manifest_query_tidak_ditandatangani():
    diminta = []

    def h(r):
        diminta.append(r)
        _cek_tanda_tangan(r)
        return httpx.Response(200, json={"berkas": [], "lagi": False})

    assert klien(h).staging_manifest("a/b c.txt", 100) == {"berkas": [], "lagi": False}
    assert diminta[0].url.path == "/wp-json/wpmgr/v1/staging/manifest"
    assert diminta[0].url.params["kursor"] == "a/b c.txt"
    assert diminta[0].url.params["batas"] == "100"


def test_file_mengurai_paket():
    data = susun({"berkas": [{"path": "a.txt", "mtime": 5}]}, [b"isi"])

    def h(r):
        _cek_tanda_tangan(r)
        assert json.loads(r.content) == {"berkas": ["a.txt"]}
        return httpx.Response(200, content=data, headers={"Content-Type": "application/octet-stream"})

    meta, bagian = klien(h).staging_file(["a.txt"])
    assert bagian == [b"isi"]
    assert meta["berkas"][0]["mtime"] == 5


def test_paket_rusak_dan_html_menjadi_bad_response():
    data = susun({"berkas": [{"path": "a"}]}, [b"isi"])
    for balasan in (data[:-1] + b"X", b"<!DOCTYPE html><p>cache</p>"):
        with pytest.raises(SiteError) as e:
            klien(lambda r, b=balasan: httpx.Response(200, content=b)).staging_file(["a"])
        assert e.value.error_class == BAD_RESPONSE


def test_respons_melebihi_batas_dipotong():
    besar = b"W" * (13 * 1024 * 1024)
    with pytest.raises(SiteError) as e:
        klien(lambda r: httpx.Response(200, content=besar)).staging_file(["a"])
    assert e.value.error_class == BAD_RESPONSE
    assert "batas" in e.value.pesan


def test_staging_mati_dan_terlalu_besar_diklasifikasikan():
    mati = {"code": "wpmgr_staging_mati", "message": "Staging tidak diizinkan", "data": {"status": 403}}
    with pytest.raises(SiteError) as e:
        klien(lambda r: httpx.Response(403, json=mati)).staging_manifest()
    assert e.value.error_class == STAGING_MATI
    besar = {"code": "wpmgr_staging_terlalu_besar", "message": "x", "data": {"status": 413}}
    with pytest.raises(SiteError) as e:
        klien(lambda r: httpx.Response(413, json=besar)).staging_file(["a"])
    assert e.value.error_class == TERLALU_BESAR
    hilang = {"code": "wpmgr_staging_tidak_ada", "message": "Berkas tidak ada.", "data": {"status": 404}}
    with pytest.raises(SiteError) as e:
        klien(lambda r: httpx.Response(404, json=hilang)).staging_rentang("a", 0, 10)
    assert e.value.error_class == BERKAS_HILANG


def test_unggah_biner_dan_terapkan_dengan_header_lewati():
    diminta = []

    def h(r):
        diminta.append(r)
        _cek_tanda_tangan(r)
        return httpx.Response(200, json={"ok": True})

    k = klien(h)
    k.staging_unggah(b"WPMGRPAK1\n\x00\xff")
    k.staging_terapkan({"dorong_id": "a" * 32, "langkah": "tukar", "token": "b" * 32}, token_lewati="b" * 32)
    assert diminta[0].headers["Content-Type"] == "application/octet-stream"
    assert diminta[0].content == b"WPMGRPAK1\n\x00\xff"
    assert diminta[1].headers["X-Wpmgr-Lewati"] == "b" * 32
    assert "X-Wpmgr-Lewati" not in diminta[0].headers


def test_timeout_unggah_unknown_manifest_transient():
    def h(r):
        raise httpx.ReadTimeout("lambat", request=r)

    with pytest.raises(SiteError) as e:
        klien(h).staging_unggah(b"x")
    assert e.value.error_class == UNKNOWN
    with pytest.raises(SiteError) as e:
        klien(h).staging_manifest()
    assert e.value.error_class == TRANSIENT
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_staging_paket.py tests/unit/test_staging_rencana.py tests/unit/test_staging_indeks.py tests/unit/test_site_client_staging.py -q`. Expected: `ModuleNotFoundError: No module named 'wpmgr.staging.paket'` dan `ImportError: cannot import name 'STAGING_MATI'`.

- [ ] **Step 3: Kelas galat dan fitur.** Di `src/wpmgr/errors.py`, tambahkan setelah `INTERNAL_ERROR = "internal_error"`:

```python
# Lapis 3. Tidak satu pun mengubah status site produksi: staging yang mati,
# ditolak, atau gagal bukan diagnosis tentang koneksi ke site.
STAGING_MATI = "staging_mati"
STAGING_DITOLAK = "staging_ditolak"
STAGING_GAGAL = "staging_gagal"
# 413 dari connector: satu permintaan potongan melebihi 8 MB (berkas tumbuh
# sejak manifest). Pemanggil memecah permintaannya, bukan mengulang.
TERLALU_BESAR = "terlalu_besar"
# 404 wpmgr_staging_tidak_ada: berkas dihapus di produksi sejak manifest.
# Bukan "connector hilang"; berkas itu dihapus juga di staging.
BERKAS_HILANG = "berkas_hilang"
KELAS_STAGING = frozenset({STAGING_MATI, STAGING_DITOLAK, STAGING_GAGAL, TERLALU_BESAR, BERKAS_HILANG})
```

setelah `KODE_PAKET_RUSAK = "wpmgr_paket_rusak"`:

```python
KODE_STAGING_MATI = "wpmgr_staging_mati"
KODE_TERLALU_BESAR = "wpmgr_staging_terlalu_besar"
KODE_STAGING_TIDAK_ADA = "wpmgr_staging_tidak_ada"
```

dan di awal badan `klasifikasi_respons`, setelah `kode = kode_plugin(body)`:

```python
    if kode == KODE_STAGING_MATI:
        # Admin site mematikan "Izinkan staging". Tanpa cabang ini 403 ini
        # dibaca auth_error dan site sehat berubah menjadi needs_reconnect.
        return STAGING_MATI
    if kode == KODE_TERLALU_BESAR:
        return TERLALU_BESAR
    if kode == KODE_STAGING_TIDAK_ADA:
        return BERKAS_HILANG
```

Di `src/wpmgr/fitur.py`, tambahkan `STAGING = "staging"` setelah `SELF_UPDATE`.

- [ ] **Step 4: Format paket.**

File: `src/wpmgr/staging/paket.py`
```python
"""Format paket biner staging, sama dengan WPMGR_Staging_Paket di connector.

    b"WPMGRPAK1\\n" + 8 hex panjang meta + b"\\n" + meta JSON + bagian...

Setiap meta["berkas"][i] membawa `ukuran` dan `sha256` bagiannya (spec §6.2
"hash per potongan"). Paket dari connector adalah masukan penyerang: setiap
bentuk yang menyimpang ditolak sebagai PaketRusak, bukan dicoba dipahami.
"""

import hashlib
import hmac
import json
import re

MAGIC = b"WPMGRPAK1\n"
MAKS_META = 4 * 1024 * 1024
_POLA_HEX8 = re.compile(rb"[0-9a-f]{8}")
_POLA_SHA = re.compile(r"[0-9a-f]{64}")


class PaketRusak(ValueError):
    pass


def susun(meta: dict, bagian: list[bytes]) -> bytes:
    berkas = [dict(b) for b in meta.get("berkas", [])]
    if len(berkas) != len(bagian):
        raise ValueError("Jumlah entri meta dan bagian paket tidak sama")
    for b, isi in zip(berkas, bagian):
        b["ukuran"] = len(isi)
        b["sha256"] = hashlib.sha256(isi).hexdigest()
    kepala = json.dumps({**meta, "berkas": berkas}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(kepala) > MAKS_META:
        raise ValueError("Meta paket terlalu besar")
    return MAGIC + f"{len(kepala):08x}\n".encode("ascii") + kepala + b"".join(bagian)


def urai(data: bytes) -> tuple[dict, list[bytes]]:
    awal = len(MAGIC)
    if len(data) < awal + 9 or not data.startswith(MAGIC):
        raise PaketRusak("bukan paket staging")
    hex8 = data[awal:awal + 8]
    if not _POLA_HEX8.fullmatch(hex8) or data[awal + 8:awal + 9] != b"\n":
        raise PaketRusak("kepala paket rusak")
    panjang = int(hex8, 16)
    if panjang > MAKS_META or awal + 9 + panjang > len(data):
        raise PaketRusak("panjang meta tidak sah")
    try:
        meta = json.loads(data[awal + 9:awal + 9 + panjang])
    except ValueError:
        raise PaketRusak("meta paket bukan JSON") from None
    if not isinstance(meta, dict) or not isinstance(meta.get("berkas"), list):
        raise PaketRusak("meta paket tidak sah")
    posisi = awal + 9 + panjang
    bagian = []
    for b in meta["berkas"]:
        if not isinstance(b, dict):
            raise PaketRusak("entri paket tidak sah")
        ukuran, sha = b.get("ukuran"), b.get("sha256")
        if isinstance(ukuran, bool) or not isinstance(ukuran, int) or ukuran < 0 \
                or not isinstance(sha, str) or not _POLA_SHA.fullmatch(sha):
            raise PaketRusak("entri paket tidak sah")
        if posisi + ukuran > len(data):
            raise PaketRusak("paket terpotong")
        isi = data[posisi:posisi + ukuran]
        if not hmac.compare_digest(hashlib.sha256(isi).hexdigest(), sha):
            raise PaketRusak("hash potongan tidak cocok")
        bagian.append(isi)
        posisi += ukuran
    if posisi != len(data):
        raise PaketRusak("ada data sisa di akhir paket")
    return meta, bagian
```

- [ ] **Step 5: Perencanaan murni.**

File: `src/wpmgr/staging/rencana.py`
```python
"""Logika murni staging: selisih manifest, potongan, rencana dorong,
tanda air, dan pemeriksaan sumber daya. Tanpa I/O, supaya bisa diuji
tuntas dan tetap sama di tarik, dorong, dan kembalikan.
"""

from dataclasses import dataclass, field

from wpmgr.staging.aman import (
    DILINDUNGI_STAGING,
    POLA_SHA256,
    PathTidakAman,
    boleh_didorong,
    dikecualikan,
    path_sah,
)

UKURAN_PAKET = 8 * 1024 * 1024
MAKS_BERKAS_PAKET = 500
MAKS_UKURAN = 2**50
MAKS_MTIME = 2**40
RAM_MINIMUM = 2 * 1024**3
SISA_DISK_MINIMUM = 0.15
AWALAN_KODE = ("wp-content/themes/", "wp-content/plugins/", "wp-content/mu-plugins/")
AWALAN_UPLOADS = "wp-content/uploads/"


@dataclass(frozen=True)
class Entri:
    path: str
    ukuran: int
    mtime: int
    hash: str | None


def _bulat(nilai, atas: int) -> int | None:
    if isinstance(nilai, bool) or not isinstance(nilai, int) or not 0 <= nilai <= atas:
        return None
    return nilai


def entri_dari(item) -> Entri | None:
    """Satu entri manifest dari connector, atau None bila ada yang tidak beres."""
    if not isinstance(item, dict):
        return None
    try:
        path = path_sah(item.get("path"))
    except PathTidakAman:
        return None
    if dikecualikan(path):
        return None
    ukuran = _bulat(item.get("ukuran"), MAKS_UKURAN)
    mtime = _bulat(item.get("mtime"), MAKS_MTIME)
    h = item.get("hash")
    if ukuran is None or mtime is None:
        return None
    if h is not None and (not isinstance(h, str) or not POLA_SHA256.fullmatch(h)):
        return None
    return Entri(path, ukuran, mtime, h)


def berubah(a: Entri, b: Entri) -> bool:
    if a.hash and b.hash:
        return a.hash != b.hash
    return a.ukuran != b.ukuran or a.mtime != b.mtime


@dataclass
class Selisih:
    baru: list[Entri] = field(default_factory=list)
    berubah: list[Entri] = field(default_factory=list)
    hapus: list[str] = field(default_factory=list)

    @property
    def diambil(self) -> list[Entri]:
        return self.baru + self.berubah

    @property
    def byte(self) -> int:
        return sum(x.ukuran for x in self.diambil)


def selisih(produksi: dict[str, Entri], lokal: dict[str, Entri]) -> Selisih:
    hasil = Selisih()
    for path in sorted(produksi):
        e = produksi[path]
        if path not in lokal:
            hasil.baru.append(e)
        elif berubah(e, lokal[path]):
            hasil.berubah.append(e)
    hasil.hapus = sorted(p for p in lokal if p not in produksi and p not in DILINDUNGI_STAGING)
    return hasil


@dataclass(frozen=True)
class Potongan:
    jenis: str
    berkas: tuple[Entri, ...]
    dari: int = 0
    panjang: int = 0

    @property
    def ukuran(self) -> int:
        return self.panjang if self.jenis == "rentang" else sum(e.ukuran for e in self.berkas)


def bagi_potongan(entri: list[Entri], ukuran_paket: int = UKURAN_PAKET,
                  maks_berkas: int = MAKS_BERKAS_PAKET) -> list[Potongan]:
    hasil: list[Potongan] = []
    kumpulan: list[Entri] = []
    total = 0
    for e in entri:
        if e.ukuran > ukuran_paket:
            for dari in range(0, e.ukuran, ukuran_paket):
                hasil.append(Potongan("rentang", (e,), dari, min(ukuran_paket, e.ukuran - dari)))
            continue
        if kumpulan and (total + e.ukuran > ukuran_paket or len(kumpulan) >= maks_berkas):
            hasil.append(Potongan("paket", tuple(kumpulan)))
            kumpulan, total = [], 0
        kumpulan.append(e)
        total += e.ukuran
    if kumpulan:
        hasil.append(Potongan("paket", tuple(kumpulan)))
    return hasil


@dataclass
class RencanaDorong:
    ganti: list[Entri]
    hapus: list[str]
    db: bool

    @property
    def byte(self) -> int:
        return sum(e.ukuran for e in self.ganti)


def rencana_dorong(mode: str, staging: dict[str, Entri], produksi: dict[str, Entri]) -> RencanaDorong:
    """Berkas yang ditulis/dihapus di produksi (spec §6.3 langkah 1, Koreksi #14)."""
    if mode not in ("hanya_kode", "timpa_penuh"):
        raise ValueError("Mode dorong tidak dikenal")

    def beda(p: str) -> bool:
        return p not in produksi or berubah(staging[p], produksi[p])

    if mode == "hanya_kode":
        ganti = [staging[p] for p in sorted(staging) if boleh_didorong(p) and (
            (p.startswith(AWALAN_KODE) and beda(p)) or (p.startswith(AWALAN_UPLOADS) and p not in produksi))]
        hapus = [p for p in sorted(produksi) if boleh_didorong(p) and p.startswith(AWALAN_KODE) and p not in staging]
        return RencanaDorong(ganti, hapus, False)
    ganti = [staging[p] for p in sorted(staging) if boleh_didorong(p) and beda(p)]
    hapus = [p for p in sorted(produksi)
             if boleh_didorong(p) and p not in staging and not p.startswith(AWALAN_UPLOADS)]
    return RencanaDorong(ganti, hapus, True)


LABEL_SUMBER = {
    "pesanan": "pesanan", "comments": "komentar", "users": "user",
    "gravity_forms": "isian Gravity Forms", "wpforms": "isian WPForms",
    "fluent_forms": "isian Fluent Forms", "flamingo": "pesan Contact Form 7 (Flamingo)", "posts": "post",
}
URUTAN_SUMBER = ("pesanan", "comments", "users", "gravity_forms", "wpforms", "fluent_forms", "flamingo", "posts")
_MAKS_ANGKA = 2**62


def urai_tanda_air(data) -> dict | None:
    """Tanda air dari connector dalam bentuk yang dijamin: hanya sumber dikenal, angka sah."""
    if not isinstance(data, dict) or not isinstance(data.get("sumber"), dict):
        return None
    sumber = {}
    for kunci, nilai in data["sumber"].items():
        if kunci not in LABEL_SUMBER or not isinstance(nilai, dict):
            continue
        maks_id = _bulat(nilai.get("maks_id"), _MAKS_ANGKA)
        jumlah = _bulat(nilai.get("jumlah"), _MAKS_ANGKA)
        if maks_id is None or jumlah is None:
            continue
        bersih = {"maks_id": maks_id, "jumlah": jumlah}
        if kunci == "posts":
            diubah = nilai.get("diubah")
            bersih["diubah"] = diubah[:19] if isinstance(diubah, str) else ""
            bersih["diubah_sejak"] = _bulat(nilai.get("diubah_sejak"), _MAKS_ANGKA)
        sumber[kunci] = bersih
    return {"sumber": sumber}


def bandingkan_tanda_air(lama: dict | None, baru: dict) -> list[str]:
    """Daftar data baru di produksi sejak tarik, dalam kalimat (spec §8.2)."""
    if not lama or not isinstance(lama.get("sumber"), dict):
        return ["Tanda air saat tarik tidak tersedia; data baru di produksi tidak dapat diperiksa."]
    ls, bs = lama["sumber"], baru.get("sumber", {})
    hasil = []
    for kunci in URUTAN_SUMBER:
        b = bs.get(kunci)
        if b is None:
            continue
        label = LABEL_SUMBER[kunci]
        l = ls.get(kunci)
        if l is None:
            if b["jumlah"] > 0:
                hasil.append(f"sumber baru: {label} ({b['jumlah']} entri)")
            continue
        if b["maks_id"] > l["maks_id"]:
            hasil.append(f"{max(1, b['jumlah'] - l['jumlah'])} {label} baru")
        if kunci == "posts":
            if b.get("diubah_sejak"):
                hasil.append(f"{b['diubah_sejak']} post diubah")
            elif b.get("diubah", "") > l.get("diubah", ""):
                hasil.append("ada post yang diubah")
    return hasil


def format_byte(n: int) -> str:
    n = max(0, int(n))
    if n < 1024:
        return f"{n} B"
    nilai = float(n)
    for satuan in ("KB", "MB", "GB", "TB"):
        nilai /= 1024
        if nilai < 1024 or satuan == "TB":
            return f"{nilai:.1f} {satuan}".replace(".", ",")
    return f"{n} B"


def cek_ram(status) -> str | None:
    if status.mem_tersedia < RAM_MINIMUM:
        return (f"RAM tersedia di VPS {format_byte(status.mem_tersedia)}; "
                f"minimal {format_byte(RAM_MINIMUM)} untuk menjalankan staging.")
    return None


def cek_disk(status, tambahan: int) -> str | None:
    total = max(1, status.disk_total)
    sisa = status.disk_bebas - max(0, tambahan)
    if sisa / total < SISA_DISK_MINIMUM:
        return (f"Sisa disk sesudah tarik akan {format_byte(max(0, sisa))} "
                f"({int(max(0, sisa) * 100 // total)}% dari {format_byte(total)}); minimal 15%.")
    return None


def cek_maks_aktif(jumlah_aktif: int, maks: int) -> str | None:
    if jumlah_aktif >= maks:
        return f"Sudah ada {jumlah_aktif} staging aktif (batas {maks}). Jeda salah satu dulu."
    return None
```

- [ ] **Step 6: Indeks lokal.**

File: `src/wpmgr/staging/indeks.py`
```python
"""Indeks berkas staging (Koreksi #2).

Setiap berkas yang selesai ditulis ke files/ dicatat satu baris JSON
(append-only), sehingga tarik yang terputus dilanjutkan dengan menghitung
ulang selisih manifest terhadap indeks, tanpa mengunduh ulang dan tanpa
menulis daftar ratusan ribu path ke job.payload setiap potongan.
"""

import hashlib
import json
import os
from pathlib import Path

from wpmgr.staging.aman import PathTidakAman, dikecualikan, path_sah
from wpmgr.staging.rencana import Entri, entri_dari


class Indeks:
    def __init__(self, berkas: Path) -> None:
        self.berkas = Path(berkas)

    def muat(self) -> dict[str, Entri]:
        hasil: dict[str, Entri] = {}
        if not self.berkas.exists():
            return hasil
        with open(self.berkas, encoding="utf-8", errors="replace") as f:
            for baris in f:
                try:
                    d = json.loads(baris)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                if d.get("hapus") == 1 and isinstance(d.get("p"), str):
                    hasil.pop(d["p"], None)
                    continue
                e = entri_dari({"path": d.get("p"), "ukuran": d.get("u"), "mtime": d.get("m"), "hash": d.get("h")})
                if e is not None:
                    hasil[e.path] = e
        return hasil

    def _tambah(self, data: dict) -> None:
        self.berkas.parent.mkdir(parents=True, exist_ok=True)
        with open(self.berkas, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def catat(self, e: Entri) -> None:
        self._tambah({"p": e.path, "u": e.ukuran, "m": e.mtime, "h": e.hash})

    def catat_hapus(self, path: str) -> None:
        self._tambah({"p": path, "hapus": 1})

    def padatkan(self, isi: dict[str, Entri]) -> None:
        sementara = self.berkas.with_name(self.berkas.name + ".tmp")
        with open(sementara, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps({"p": e.path, "u": e.ukuran, "m": e.mtime, "h": e.hash}, ensure_ascii=False) + "\n" for e in isi.values())
            f.flush()
            os.fsync(f.fileno())
        os.replace(sementara, self.berkas)


def sha256_berkas(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for potong in iter(lambda: f.read(1 << 20), b""):
            h.update(potong)
    return h.hexdigest()


def pindai_lokal(akar: Path, indeks: dict[str, Entri]) -> dict[str, Entri]:
    """Isi files/ staging saat ini. Hash diambil dari indeks bila ukuran dan mtime sama."""
    hasil: dict[str, Entri] = {}
    akar = Path(akar)
    for dirpath, dirnames, filenames in os.walk(akar, followlinks=False):
        dasar = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not (dasar / d).is_symlink()]
        for nama in filenames:
            jalur = dasar / nama
            if jalur.is_symlink():
                continue
            relatif = jalur.relative_to(akar).as_posix()
            try:
                path_sah(relatif)
            except PathTidakAman:
                continue
            if dikecualikan(relatif):
                continue
            st = jalur.stat()
            lama = indeks.get(relatif)
            if lama is not None and lama.ukuran == st.st_size and lama.mtime == int(st.st_mtime) and lama.hash:
                h = lama.hash
            else:
                h = sha256_berkas(jalur)
            hasil[relatif] = Entri(relatif, st.st_size, int(st.st_mtime), h)
    return hasil
```

- [ ] **Step 7: Metode `SiteClient`.** Di `src/wpmgr/site_client.py`, tambahkan impor `from wpmgr.staging import paket` dan konstanta setelah `TIMEOUT_SELF_UPDATE`:

```python
TIMEOUT_STAGING = 45.0
TIMEOUT_STAGING_TERAPKAN = 60.0
BATAS_JSON_STAGING = 32 * 1024 * 1024
BATAS_BINER_STAGING = 12 * 1024 * 1024
```

Tambahkan metode ke kelas `SiteClient`:

```python
    def _kirim(self, method: str, path: str, body: bytes, timeout: float, berefek: bool, query: str,
               content_type: str, header_tambahan: dict | None, batas_byte: int) -> tuple[int, dict, bytes]:
        """Seperti _panggil(), tetapi body dibaca sebagai stream dengan batas.

        Endpoint staging mengembalikan sampai 8 MB per potongan; connector
        yang disusupi bisa mengirim apa saja, jadi pembacaan berhenti di
        batas alih-alih menampung seluruhnya di memori.
        """
        timestamp = int(time.time())
        nonce = new_nonce()
        headers = {
            "X-Wpmgr-Site": self.site_id,
            "X-Wpmgr-Timestamp": str(timestamp),
            "X-Wpmgr-Nonce": nonce,
            "X-Wpmgr-Signature": sign(self.secret_hex, method, path, timestamp, nonce, body),
            "Accept": "application/json, application/octet-stream",
            **(header_tambahan or {}),
        }
        if body:
            headers["Content-Type"] = content_type
        try:
            with self._client.stream(method, f"{self.base_url}{path}{query}", content=body or None,
                                     headers=headers, timeout=timeout) as resp:
                isi = bytearray()
                for potong in resp.iter_bytes():
                    isi.extend(potong)
                    if len(isi) > batas_byte:
                        raise SiteError(BAD_RESPONSE, f"Respons connector melebihi batas {batas_byte} byte")
                return resp.status_code, dict(resp.headers), bytes(isi)
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise SiteError(TRANSIENT, f"timeout koneksi setelah {timeout} detik") from exc
        except httpx.TimeoutException as exc:
            raise SiteError(UNKNOWN if berefek else TRANSIENT, f"timeout setelah {timeout} detik") from exc
        except httpx.HTTPError as exc:
            raise SiteError(TRANSIENT, f"kesalahan koneksi: {exc}") from exc

    def _galat_dari(self, status: int, headers: dict, isi: bytes) -> SiteError | None:
        teks = isi[:65536].decode("utf-8", "replace")
        kelas = klasifikasi_respons(status, headers, teks)
        if kelas is None:
            return None
        return SiteError(kelas, (pesan_plugin(teks) or teks)[:500])

    def _staging_json(self, method: str, route: str, badan: dict | None = None, query: str = "",
                      berefek: bool = False, timeout: float = TIMEOUT_STAGING,
                      header_tambahan: dict | None = None) -> dict:
        body = json.dumps(badan, separators=(",", ":")).encode("utf-8") if badan is not None else b""
        status, headers, isi = self._kirim(method, f"{PREFIX}{route}", body, timeout, berefek, query,
                                           "application/json", header_tambahan, BATAS_JSON_STAGING)
        galat = self._galat_dari(status, headers, isi)
        if galat is not None:
            raise galat
        try:
            data = json.loads(isi)
        except ValueError as exc:
            raise SiteError(BAD_RESPONSE, isi[:500].decode("utf-8", "replace")) from exc
        if not isinstance(data, dict):
            raise SiteError(BAD_RESPONSE, "Balasan connector bukan objek JSON")
        return data

    def _staging_paket(self, route: str, badan: dict) -> tuple[dict, list[bytes]]:
        body = json.dumps(badan, separators=(",", ":")).encode("utf-8")
        status, headers, isi = self._kirim("POST", f"{PREFIX}{route}", body, TIMEOUT_STAGING, False, "",
                                           "application/json", None, BATAS_BINER_STAGING)
        if status == 200 and isi.startswith(paket.MAGIC):
            try:
                return paket.urai(isi)
            except paket.PaketRusak as exc:
                raise SiteError(BAD_RESPONSE, f"Paket staging rusak: {exc}") from exc
        galat = self._galat_dari(status, headers, isi)
        raise galat or SiteError(BAD_RESPONSE, "Balasan connector bukan paket staging")

    def staging_manifest(self, kursor: str | None = None, batas: int = 5000) -> dict:
        param = {"batas": str(batas)}
        if kursor:
            param["kursor"] = kursor
        return self._staging_json("GET", "/staging/manifest", query="?" + urlencode(param))

    def staging_file(self, paths: list[str]) -> tuple[dict, list[bytes]]:
        return self._staging_paket("/staging/file", {"berkas": list(paths)})

    def staging_rentang(self, path: str, dari: int, panjang: int) -> tuple[dict, list[bytes]]:
        return self._staging_paket("/staging/file", {"rentang": {"path": path, "dari": dari, "panjang": panjang}})

    def staging_tabel(self, tabel: str, kursor: str | None) -> tuple[dict, list[bytes]]:
        return self._staging_paket("/staging/tabel", {"tabel": tabel, "kursor": kursor or ""})

    def staging_tanda_air(self, posts_sejak: str | None = None, posts_maks: int | None = None) -> dict:
        param = {}
        if posts_sejak and posts_maks:
            param = {"posts_sejak": posts_sejak, "posts_maks": str(posts_maks)}
        return self._staging_json("GET", "/staging/tanda-air", query="?" + urlencode(param) if param else "")

    def staging_snapshot(self, paths: list[str], awal: bool = False) -> dict:
        return self._staging_json("POST", "/staging/snapshot", {"paths": list(paths), "awal": awal})

    def staging_unggah(self, isi: bytes) -> dict:
        status, headers, balasan = self._kirim("POST", f"{PREFIX}/staging/unggah", isi, TIMEOUT_STAGING, True, "",
                                               "application/octet-stream", None, BATAS_JSON_STAGING)
        galat = self._galat_dari(status, headers, balasan)
        if galat is not None:
            raise galat
        try:
            data = json.loads(balasan)
        except ValueError as exc:
            raise SiteError(BAD_RESPONSE, balasan[:500].decode("utf-8", "replace")) from exc
        if not isinstance(data, dict):
            raise SiteError(BAD_RESPONSE, "Balasan unggah bukan objek JSON")
        return data

    def staging_terapkan(self, badan: dict, token_lewati: str | None = None) -> dict:
        header = {"X-Wpmgr-Lewati": token_lewati} if token_lewati else None
        return self._staging_json("POST", "/staging/terapkan", badan, berefek=True,
                                  timeout=TIMEOUT_STAGING_TERAPKAN, header_tambahan=header)

    def staging_bersihkan(self, dorong_id: str) -> dict:
        return self._staging_json("POST", "/staging/bersihkan", {"dorong_id": dorong_id}, berefek=True)
```

`X-Wpmgr-Lewati` tidak ditandatangani, dan memang tidak perlu. Token yang sama sudah dikirim di body `tukar` yang ditandatangani, dan header itu hanya membuka pintu `.maintenance` untuk request ini, bukan memberi kewenangan apa pun.

- [ ] **Step 8: Jalankan test.** Run keempat berkas test di Step 2, lalu seluruh unit test dan `ruff check .`. Expected: semua lulus. 

- [ ] **Step 9: Commit.**

```bash
git add src/wpmgr/errors.py src/wpmgr/fitur.py src/wpmgr/site_client.py src/wpmgr/staging tests/unit/test_staging_paket.py tests/unit/test_staging_rencana.py tests/unit/test_staging_indeks.py tests/unit/test_site_client_staging.py
git commit -m "feat(staging): klien connector staging, paket biner, indeks, dan perencanaan"
```

---

## Fase D — Job staging

### Task 13: Infrastruktur job staging — klaim antrean, worker khusus, detak, batal, log aktivitas

**Files:**
- Create: `src/wpmgr/staging/umum.py`, `tests/integration/test_staging_antrean.py`
- Modify: `src/wpmgr/jobs/queue.py`, `src/wpmgr/worker.py`, `deploy/wpmgr-worker@.service`, `tests/integration/conftest.py`

**Interfaces:**
- Consumes: Task 1 (`JOB_STAGING`, `Staging`), Task 11 (`GalatPembantu`, `Pembantu`), Task 12 (kelas galat).
- Produces:
  - `wpmgr.jobs.queue.ambil_job(sesi, worker, jenis: str | None = None)`, dengan `jenis` berupa `None` (semua), `"staging"`, atau `"umum"`. Klaim yang baru mengizinkan `staging_tarik`/`staging_uji_update` berjalan bersamaan dengan job non-staging di site yang sama (Koreksi #1).
  - `wpmgr.worker`: `jenis_worker(instans: str) -> str`; `proses_satu(sesi, worker, buat_klien_fn=buat_klien, jenis=None)`. `main()` membaca `WPMGR_WORKER_INSTANS`, sehingga instans `staging*` hanya mengklaim job staging dan instans lain tidak pernah mengklaimnya. Kegagalan kelas `KELAS_STAGING` tidak menimpa `site.last_error`.
  - `wpmgr.staging.umum`:
    - pengecualian: `KlaimHilang`, `Dibatalkan`; `galat_ditolak(pesan) -> SiteError`, `galat_gagal(pesan) -> SiteError`;
    - runtime dan lokasi: `buat_pembantu() -> Pembantu` (ditambal di test), `buat_http() -> httpx.Client` (ditambal di test), `sekarang()`, `dir_site(site_id) -> Path`, `host_staging(staging)`, `url_staging(staging)`;
    - penanda perubahan staging (Koreksi #16): `baca_diubah(site_id) -> datetime | None`, `perbarui_diubah(staging) -> None`;
    - kendali job: `detak(sesi, job)`, `harus_berhenti() -> bool`, `periksa_batal(sesi, staging)`, `titik_potongan(sesi, job, staging)`;
    - kemajuan dan ulang: `kemajuan(job) -> dict`, `simpan_kemajuan(sesi, job, **perubahan) -> dict`, `ulangi(fungsi, *args, kali=3, tidur=time.sleep, **kwargs)`;
    - log dan galat: `catat_aktivitas(sesi, site_id, job, pesan, detail=None, level="info", user_id=None)`, `pesan_os(exc: OSError) -> str`;
    - pembungkus handler: `muat_staging(sesi, job) -> tuple[Site, Staging]`, `jalankan_staging(sesi, job, inti, status_kerja: StatusStaging, nama: str)`. `inti(sesi, job, site, staging) -> dict` dipanggil di dalam pembungkus, yang mengurus status staging, galat, batal, dan pembersihan `batal_diminta_pada`.
  - Fixture integrasi `staging_aktif` (env domain dan direktori ke `tmp_path`) dan `site_staging` (site ber-secret asli dan baris `Staging` bernama `contoh-test`).

- [ ] **Step 1: Fixture.** Tambahkan ke akhir `tests/integration/conftest.py`:

```python
@pytest.fixture
def staging_aktif(tmp_path, monkeypatch):
    """Fitur staging menyala dengan WPMGR_STAGING_DIR di direktori sementara."""
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", "staging.contoh.id")
    monkeypatch.setenv("WPMGR_STAGING_DIR", str(tmp_path / "stg"))
    get_settings.cache_clear()
    yield tmp_path / "stg"
    get_settings.cache_clear()


@pytest.fixture
def site_staging(sesi, site, staging_aktif):
    from wpmgr.crypto import enkripsi_secret
    from wpmgr.models import Staging
    from wpmgr.staging.pembantu import hash_sandi

    site.secret_terenkripsi = enkripsi_secret("f" * 64)
    site.fitur = ["self_update", "staging"]
    st = Staging(site_id=site.id, nama="contoh-test", sandi_hash=hash_sandi("rahasia-preview"),
                 rahasia_router_terenkripsi=enkripsi_secret("e" * 64))
    sesi.add(st)
    sesi.commit()
    return st
```

- [ ] **Step 2: Tulis test yang gagal.**

File: `tests/integration/test_staging_antrean.py`
```python
import errno
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from wpmgr.errors import STAGING_GAGAL, TRANSIENT, UPGRADE_FAILED, SiteError
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import ambil_job, buat_job
from wpmgr.models import ActivityLog, Job, JobStatus, JobType, StatusStaging
from wpmgr.staging import umum
from wpmgr.staging.pembantu import GalatPembantu
from wpmgr.worker import jenis_worker, proses_satu

pytestmark = pytest.mark.integration


def _berjalan(sesi, site, tipe, oleh="w-lain"):
    job = buat_job(sesi, site.id, tipe)
    job.status = JobStatus.running
    job.locked_by = oleh
    job.locked_at = datetime.now(timezone.utc)
    sesi.commit()
    return job


def test_jenis_worker():
    assert jenis_worker("staging") == "staging"
    assert jenis_worker("staging2") == "staging"
    assert jenis_worker("1") == "umum"
    assert jenis_worker("") == "umum"


def test_tarik_boleh_berjalan_bersama_scan_di_site_yang_sama(sesi, site):
    _berjalan(sesi, site, JobType.scan_site)
    tarik = buat_job(sesi, site.id, JobType.staging_tarik)
    assert ambil_job(sesi, "w1", "staging").id == tarik.id


def test_dorong_menunggu_job_lain_di_site_yang_sama(sesi, site):
    _berjalan(sesi, site, JobType.scan_site)
    buat_job(sesi, site.id, JobType.staging_dorong)
    assert ambil_job(sesi, "w1", "staging") is None


def test_job_umum_menunggu_dorong_tetapi_tidak_menunggu_tarik(sesi, site):
    tarik = _berjalan(sesi, site, JobType.staging_tarik)
    scan = buat_job(sesi, site.id, JobType.scan_site)
    assert ambil_job(sesi, "w1", "umum").id == scan.id
    scan.status = JobStatus.success
    tarik.tipe = JobType.staging_dorong
    sesi.commit()
    buat_job(sesi, site.id, JobType.verify_site)
    assert ambil_job(sesi, "w1", "umum") is None


def test_jenis_worker_memisahkan_klaim(sesi, site):
    tarik = buat_job(sesi, site.id, JobType.staging_tarik)
    scan = buat_job(sesi, site.id, JobType.scan_site, scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert ambil_job(sesi, "w1", "staging").id == tarik.id
    assert ambil_job(sesi, "w2", "umum").id == scan.id


def test_tanpa_jenis_mengklaim_apa_saja(sesi, site):
    tarik = buat_job(sesi, site.id, JobType.staging_tarik)
    assert ambil_job(sesi, "w1").id == tarik.id


def test_detak_memperbarui_locked_at_dan_mendeteksi_klaim_hilang(sesi, site):
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    job = ambil_job(sesi, "w1", "staging")
    sesi.execute(text("UPDATE jobs SET locked_at = now() - interval '20 minutes' WHERE id = :i"), {"i": job.id})
    sesi.commit()
    umum.detak(sesi, job)
    sesi.refresh(job)
    assert job.locked_at > datetime.now(timezone.utc) - timedelta(minutes=1)
    sesi.execute(text("UPDATE jobs SET locked_by = 'w-lain' WHERE id = :i"), {"i": job.id})
    sesi.commit()
    with pytest.raises(umum.KlaimHilang):
        umum.detak(sesi, job)


def test_detak_diam_untuk_job_yang_belum_diklaim(sesi, site):
    umum.detak(sesi, buat_job(sesi, site.id, JobType.staging_tarik))


def test_simpan_kemajuan_menggabungkan(sesi, site):
    job = buat_job(sesi, site.id, JobType.staging_tarik, {"konfirmasi": True})
    umum.simpan_kemajuan(sesi, job, tahap="berkas", byte_selesai=10)
    umum.simpan_kemajuan(sesi, job, byte_selesai=20)
    sesi.expire_all()
    job = sesi.get(Job, job.id)
    assert job.payload["konfirmasi"] is True
    assert job.payload["kemajuan"]["tahap"] == "berkas"
    assert job.payload["kemajuan"]["byte_selesai"] == 20


def test_ulangi_potongan():
    panggilan = []

    def gagal_dua_kali():
        panggilan.append(1)
        if len(panggilan) < 3:
            raise SiteError(TRANSIENT, "putus")
        return "ok"

    assert umum.ulangi(gagal_dua_kali, tidur=lambda d: None) == "ok"
    panggilan.clear()

    def selalu_gagal():
        panggilan.append(1)
        raise SiteError(TRANSIENT, "x")

    with pytest.raises(SiteError):
        umum.ulangi(selalu_gagal, tidur=lambda d: None)
    assert len(panggilan) == 3
    panggilan.clear()

    def tidak_diulang():
        panggilan.append(1)
        raise SiteError(UPGRADE_FAILED, "tidak")

    with pytest.raises(SiteError):
        umum.ulangi(tidak_diulang, tidur=lambda d: None)
    assert len(panggilan) == 1


def test_periksa_batal(sesi, site_staging):
    umum.periksa_batal(sesi, site_staging)
    site_staging.batal_diminta_pada = datetime.now(timezone.utc)
    sesi.commit()
    with pytest.raises(umum.Dibatalkan):
        umum.periksa_batal(sesi, site_staging)


def test_muat_staging_menolak_bila_fitur_mati(sesi, site, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    get_settings.cache_clear()
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    with pytest.raises(SiteError) as e:
        umum.muat_staging(sesi, job)
    assert e.value.error_class == "staging_ditolak"


def _jalankan(sesi, site_staging, inti):
    job = buat_job(sesi, site_staging.site_id, JobType.staging_tarik)
    return job, lambda: umum.jalankan_staging(sesi, job, inti, StatusStaging.menyalin, "Tarik staging")


def test_pembungkus_galat_pembantu_menjadi_status_gagal(sesi, site_staging):
    def inti(sesi, job, site, staging):
        assert staging.status == StatusStaging.menyalin
        raise GalatPembantu("docker", "Perintah Docker di server staging gagal.")

    _, jalan = _jalankan(sesi, site_staging, inti)
    with pytest.raises(SiteError) as e:
        jalan()
    assert e.value.error_class == STAGING_GAGAL
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal
    assert site_staging.galat == "Perintah Docker di server staging gagal."


def test_pembungkus_disk_penuh_tanpa_path(sesi, site_staging):
    def inti(sesi, job, site, staging):
        raise OSError(errno.ENOSPC, "No space left on device", "/var/lib/wpmgr/staging/x/files/a")

    _, jalan = _jalankan(sesi, site_staging, inti)
    with pytest.raises(SiteError) as e:
        jalan()
    sesi.refresh(site_staging)
    assert "Disk VPS penuh" in site_staging.galat
    assert "/var/lib" not in site_staging.galat and "/var/lib" not in e.value.pesan


def test_pembungkus_batal(sesi, site_staging):
    site_staging.ditarik_pada = datetime.now(timezone.utc)
    sesi.commit()

    def inti(sesi, job, site, staging):
        staging.batal_diminta_pada = datetime.now(timezone.utc)
        sesi.commit()
        umum.periksa_batal(sesi, staging)

    _, jalan = _jalankan(sesi, site_staging, inti)
    with pytest.raises(SiteError):
        jalan()
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap
    assert site_staging.batal_diminta_pada is None
    assert site_staging.galat == "Dibatalkan oleh pengguna."


def test_pembungkus_sukses_membersihkan_galat(sesi, site_staging):
    site_staging.galat = "lama"
    sesi.commit()
    _, jalan = _jalankan(sesi, site_staging, lambda sesi, job, site, staging: {"ok": True})
    assert jalan() == {"ok": True}
    sesi.refresh(site_staging)
    assert site_staging.galat is None


def test_galat_sementara_yang_masih_diulang_tidak_menandai_gagal(sesi, site_staging):
    def inti(sesi, job, site, staging):
        raise SiteError(TRANSIENT, "koneksi putus")

    job, jalan = _jalankan(sesi, site_staging, inti)
    job.attempts = 1
    sesi.commit()
    with pytest.raises(SiteError):
        jalan()
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.menyalin
    assert "dilanjutkan" in site_staging.galat


def test_kegagalan_staging_tidak_menimpa_last_error_site(sesi, site_staging, monkeypatch):
    from wpmgr.models import Site

    site = sesi.get(Site, site_staging.site_id)
    site.last_error = "galat lama yang asli"
    sesi.commit()

    def inti_gagal(sesi, job, klien):
        raise umum.galat_gagal("Staging gagal karena sesuatu.")

    monkeypatch.setitem(handlers.HANDLER, JobType.staging_tarik, inti_gagal)
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.refresh(site)
    sesi.refresh(job)
    assert job.status == JobStatus.failed
    assert site.last_error == "galat lama yang asli"


def test_penanda_diubah_dibaca_dan_dijaga(sesi, site_staging, staging_aktif):
    assert umum.baca_diubah(site_staging.site_id) is None
    log = staging_aktif / str(site_staging.site_id) / "log"
    log.mkdir(parents=True)
    (log / "diubah").write_text("bukan angka", encoding="ascii")
    assert umum.baca_diubah(site_staging.site_id) is None
    (log / "diubah").write_text("1790000000", encoding="ascii")
    umum.perbarui_diubah(site_staging)
    assert site_staging.diubah_pada == datetime.fromtimestamp(1790000000, tz=timezone.utc)


def test_catat_aktivitas_menyebut_pengguna(sesi, site, pengguna_uji):
    job = buat_job(sesi, site.id, JobType.staging_tarik, dibuat_oleh=pengguna_uji.id)
    umum.catat_aktivitas(sesi, site.id, job, "Staging disegarkan", {"ukuran": 5})
    sesi.commit()
    log = sesi.query(ActivityLog).one()
    assert log.pesan == "Staging disegarkan oleh a@b.test"
    assert log.user_id == pengguna_uji.id
    assert log.detail == {"ukuran": 5}
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_staging_antrean.py -q`. Expected: `ImportError: cannot import name 'jenis_worker' from 'wpmgr.worker'`.

- [ ] **Step 4: Klaim antrean.** Di `src/wpmgr/jobs/queue.py`, ganti `SQL_AMBIL`:

```python
_STAGING = "('staging_tarik', 'staging_uji_update', 'staging_dorong', 'staging_kembalikan')"
_STAGING_BACA = "('staging_tarik', 'staging_uji_update')"

# Satu job berjalan per site, dengan dua pengecualian (Koreksi #1): tarik
# dan uji staging hanya membaca produksi, jadi tidak menahan dan tidak
# ditahan job non-staging. Dorong/kembalikan menulis ke produksi dan tetap
# eksklusif terhadap semuanya. `:jenis` memisahkan worker staging (job
# berjam-jam) dari worker umum.
SQL_AMBIL = text(
    f"""
    UPDATE jobs
       SET status       = 'running',
           locked_at    = now(),
           locked_by    = :worker,
           started_at   = now(),
           attempts     = attempts + 1
     WHERE id = (
           SELECT j.id
             FROM jobs j
             JOIN sites s ON s.id = j.site_id
            WHERE j.status = 'pending'
              AND j.scheduled_for <= now()
              AND s.status <> 'disabled'
              AND (CAST(:jenis AS text) IS NULL
                   OR (CAST(:jenis AS text) = 'staging') = (j.tipe IN {_STAGING}))
              AND NOT EXISTS (
                    SELECT 1 FROM jobs j2
                     WHERE j2.site_id = j.site_id
                       AND j2.status = 'running'
                       AND NOT (j.tipe IN {_STAGING_BACA} AND j2.tipe NOT IN {_STAGING})
                       AND NOT (j2.tipe IN {_STAGING_BACA} AND j.tipe NOT IN {_STAGING}))
            ORDER BY j.scheduled_for
              FOR UPDATE OF j, s SKIP LOCKED
            LIMIT 1)
    RETURNING id
    """
# Nilai bawaan None: pemanggil lama (dan test antrean Lapis 1) yang hanya
# mengirim :worker tetap mendapat perilaku "klaim apa saja".
).bindparams(bindparam("jenis", value=None, type_=String))
```

dengan impor `from sqlalchemy import String, bindparam, select, text` (menggantikan `from sqlalchemy import select, text`), dan ganti `ambil_job`:

```python
def ambil_job(sesi: Session, worker: str, jenis: str | None = None) -> Job | None:
    baris = sesi.execute(SQL_AMBIL, {"worker": worker, "jenis": jenis}).first()
    sesi.commit()
    if baris is None:
        return None
    return sesi.get(Job, baris[0], populate_existing=True)
```

- [ ] **Step 5: Worker.** Di `src/wpmgr/worker.py`: tambahkan `import os`, tambahkan `KELAS_STAGING` ke impor dari `wpmgr.errors`, lalu tambahkan fungsi setelah `_tangani_sinyal`:

```python
def jenis_worker(instans: str) -> str:
    """Instans systemd `wpmgr-worker@staging*` hanya mengambil job staging."""
    return "staging" if instans.startswith("staging") else "umum"
```

Ubah signature dan baris pertama `proses_satu`:

```python
def proses_satu(sesi: Session, worker: str, buat_klien_fn=buat_klien, jenis: str | None = None) -> bool:
    job = ambil_job(sesi, worker, jenis)
```

Di `_catat_kegagalan`, ganti `site.last_error = exc.pesan[:2000]` dengan:

```python
    if kelas not in KELAS_STAGING:
        # Staging yang gagal bukan kabar tentang site produksi: galatnya
        # disimpan di staging.galat, bukan menimpa galat koneksi site.
        site.last_error = exc.pesan[:2000]
```

Di `main()`, ganti `worker = worker_id()` dan baris `log.info("Worker %s mulai", worker)` dengan:

```python
    worker = worker_id()
    jenis = jenis_worker(os.environ.get("WPMGR_WORKER_INSTANS", ""))
    log.info("Worker %s (%s) mulai", worker, jenis)
```

dan panggilan `proses_satu(sesi, worker, buat_klien)` di loop dengan `proses_satu(sesi, worker, buat_klien, jenis)`.

Di `deploy/wpmgr-worker@.service`, tambahkan baris setelah `EnvironmentFile=/opt/wpmgr/.env`:

```ini
# Instans `staging` (wpmgr-worker@staging) hanya memproses job staging, yang
# bisa berjalan berjam-jam; instans lain tidak pernah mengambilnya.
Environment=WPMGR_WORKER_INSTANS=%i
```

- [ ] **Step 6: Modul umum.**

File: `src/wpmgr/staging/umum.py`
```python
"""Bagian bersama semua job staging (spec §6.4, §11, Koreksi #1–#3)."""

import errno
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from wpmgr.config import get_settings
from wpmgr.errors import (
    DAPAT_DIULANG,
    STAGING_DITOLAK,
    STAGING_GAGAL,
    TRANSIENT,
    UNKNOWN,
    SiteError,
)
from wpmgr.models import ActivityLog, Job, JobStatus, Site, Staging, StatusStaging, User
from wpmgr.staging.aman import angka, bersih_teks
from wpmgr.staging.pembantu import GalatPembantu, Pembantu

ULANG_POTONGAN = 3
JEDA_ULANG = (2, 5)
UA = "WP-Manager-Staging/3.0"


class KlaimHilang(Exception):
    """Reaper atau worker lain sudah mengambil alih job ini."""


class Dibatalkan(Exception):
    """Pengguna meminta pembatalan (staging.batal_diminta_pada)."""


def galat_ditolak(pesan: str) -> SiteError:
    return SiteError(STAGING_DITOLAK, pesan)


def galat_gagal(pesan: str) -> SiteError:
    return SiteError(STAGING_GAGAL, pesan)


def buat_pembantu() -> Pembantu:
    return Pembantu.dari_setelan()


def buat_http() -> httpx.Client:
    """Klien HTTP untuk probe staging dan cek halaman utama produksi."""
    return httpx.Client(follow_redirects=False, timeout=30.0, headers={"User-Agent": UA})


def sekarang() -> datetime:
    return datetime.now(timezone.utc)


def dir_site(site_id) -> Path:
    return get_settings().jalur_staging / str(site_id)


def host_staging(staging: Staging) -> str:
    return f"{staging.nama}.{get_settings().staging_domain}"


def url_staging(staging: Staging) -> str:
    return f"https://{host_staging(staging)}"


def baca_diubah(site_id) -> datetime | None:
    """Waktu terakhir staging diubah, dari penanda yang ditulis mu-plugin staging."""
    try:
        teks = (dir_site(site_id) / "log" / "diubah").read_text(encoding="ascii", errors="replace").strip()[:20]
    except OSError:
        return None
    detik = angka(teks, 0, 2**40)
    return None if detik is None else datetime.fromtimestamp(detik, tz=timezone.utc)


def perbarui_diubah(staging: Staging) -> None:
    d = baca_diubah(staging.site_id)
    if d is not None and (staging.diubah_pada is None or d > staging.diubah_pada):
        staging.diubah_pada = d


def detak(sesi: Session, job: Job) -> None:
    """Perpanjang klaim (locked_at) supaya reaper tidak merebut job yang berjalan lama.

    Job yang dipanggil langsung tanpa klaim (test, pemanggilan manual) tidak
    punya locked_by dan tidak perlu detak.
    """
    if job.locked_by is None:
        return
    n = sesi.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == JobStatus.running, Job.locked_by == job.locked_by)
        .values(locked_at=func.now())
    ).rowcount
    sesi.commit()
    if n == 0:
        raise KlaimHilang(f"Klaim job {job.id} sudah tidak dipegang {job.locked_by}")


def harus_berhenti() -> bool:
    from wpmgr import worker

    return worker._berhenti


def periksa_batal(sesi: Session, staging: Staging) -> None:
    diminta = sesi.scalar(select(Staging.batal_diminta_pada).where(Staging.id == staging.id))
    if diminta is not None:
        raise Dibatalkan()


def titik_potongan(sesi: Session, job: Job, staging: Staging | None) -> None:
    """Dipanggil di antara potongan: batal, penghentian worker, dan detak.

    `staging` None hanya untuk kembalikan setelah staging dihapus: tidak ada
    kolom batal yang bisa diperiksa.
    """
    if staging is not None:
        periksa_batal(sesi, staging)
    if harus_berhenti():
        # Berhenti karena deploy/restart bukan kegagalan: jatah percobaan
        # tidak dihabiskan, dan progres di payload membuat job melanjutkan.
        job.attempts = max(0, job.attempts - 1)
        sesi.commit()
        raise SiteError(TRANSIENT, "Worker dihentikan; job staging dilanjutkan otomatis.")
    detak(sesi, job)


def kemajuan(job: Job) -> dict:
    return dict((job.payload or {}).get("kemajuan") or {})


def simpan_kemajuan(sesi: Session, job: Job, **perubahan) -> dict:
    payload = dict(job.payload or {})
    k = dict(payload.get("kemajuan") or {})
    k.update(perubahan)
    k["diperbarui"] = sekarang().isoformat()
    payload["kemajuan"] = k
    job.payload = payload
    flag_modified(job, "payload")
    sesi.commit()
    detak(sesi, job)
    return k


def ulangi(fungsi, *args, kali: int = ULANG_POTONGAN, tidur=time.sleep, **kwargs):
    """Satu potongan diulang sampai 3 kali (spec §12), hanya untuk galat yang layak diulang."""
    for percobaan in range(1, kali + 1):
        try:
            return fungsi(*args, **kwargs)
        except SiteError as exc:
            if (exc.error_class not in DAPAT_DIULANG and exc.error_class != UNKNOWN) or percobaan == kali:
                raise
            tidur(JEDA_ULANG[min(percobaan - 1, len(JEDA_ULANG) - 1)])
    raise AssertionError("tidak tercapai")


def catat_aktivitas(sesi: Session, site_id, job: Job | None, pesan: str, detail: dict | None = None,
                    level: str = "info", user_id=None) -> None:
    uid = user_id if user_id is not None else (job.dibuat_oleh if job is not None else None)
    email = None
    if uid is not None:
        u = sesi.get(User, uid)
        email = u.email if u is not None else None
    teks = f"{pesan} oleh {email}" if email else pesan
    sesi.add(ActivityLog(site_id=site_id, job_id=job.id if job is not None else None, user_id=uid,
                         level=level, pesan=teks[:500], detail=detail))


def pesan_os(exc: OSError) -> str:
    """Pesan UI untuk galat berkas. Teks OSError memuat path VPS, jadi tidak pernah dipakai."""
    if exc.errno == errno.ENOSPC:
        return ("Disk VPS penuh saat menulis staging. Kosongkan ruang lalu jalankan lagi; "
                "berkas yang sudah tersalin tidak diunduh ulang.")
    if exc.errno in (errno.EACCES, errno.EPERM):
        return "Izin berkas di server staging tidak cukup."
    return f"Galat berkas di server staging (errno {exc.errno})."


def muat_staging(sesi: Session, job: Job) -> tuple[Site, Staging]:
    if not get_settings().staging_aktif:
        raise galat_ditolak("Fitur staging tidak aktif (WPMGR_STAGING_DOMAIN kosong).")
    site = sesi.get(Site, job.site_id)
    staging = sesi.scalar(select(Staging).where(Staging.site_id == job.site_id))
    if staging is None:
        raise galat_ditolak("Staging untuk site ini belum dibuat.")
    return site, staging


def _tandai(sesi: Session, staging_id, status: StatusStaging, galat: str | None) -> None:
    sesi.rollback()
    st = sesi.get(Staging, staging_id, populate_existing=True)
    st.status = status
    st.galat = bersih_teks(galat, 1000)
    st.batal_diminta_pada = None
    sesi.commit()


def jalankan_staging(sesi: Session, job: Job, inti, status_kerja: StatusStaging, nama: str) -> dict:
    site, staging = muat_staging(sesi, job)
    staging_id = staging.id
    staging.status = status_kerja
    staging.galat = None
    sesi.commit()
    try:
        periksa_batal(sesi, staging)
        hasil = inti(sesi, job, site, staging)
    except Dibatalkan:
        sesi.rollback()
        st = sesi.get(Staging, staging_id, populate_existing=True)
        status = StatusStaging.siap if st.ditarik_pada else StatusStaging.gagal
        _tandai(sesi, staging_id, status, "Dibatalkan oleh pengguna.")
        catat_aktivitas(sesi, site.id, job, f"{nama} dibatalkan", level="warning")
        sesi.commit()
        raise galat_gagal("Dibatalkan oleh pengguna.") from None
    except KlaimHilang:
        raise
    except GalatPembantu as exc:
        _tandai(sesi, staging_id, StatusStaging.gagal, exc.pesan)
        raise galat_gagal(exc.pesan) from None
    except SiteError as exc:
        akhir = exc.error_class not in DAPAT_DIULANG or job.attempts >= job.max_attempts
        if akhir:
            _tandai(sesi, staging_id, StatusStaging.gagal, exc.pesan)
        else:
            _tandai(sesi, staging_id, status_kerja, f"Terputus, dilanjutkan otomatis: {exc.pesan}")
        raise
    except OSError as exc:
        pesan = pesan_os(exc)
        _tandai(sesi, staging_id, StatusStaging.gagal, pesan)
        raise galat_gagal(pesan) from None
    st = sesi.get(Staging, staging_id, populate_existing=True)
    st.galat = None
    st.batal_diminta_pada = None
    sesi.commit()
    return hasil
```

`inti` bertanggung jawab memasang status akhirnya sendiri (mis. `siap`) sebelum kembali.

- [ ] **Step 7: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/integration/test_staging_antrean.py tests/integration/test_worker.py tests/integration/test_queue.py tests/integration/test_reaper.py -q`. Expected: semua lulus. Test worker dan antrean Lapis 1 tetap lulus, karena `jenis=None` mempertahankan perilaku lama untuk job non-staging.

- [ ] **Step 8: Commit.**

```bash
git add src/wpmgr/jobs/queue.py src/wpmgr/worker.py src/wpmgr/staging/umum.py deploy/wpmgr-worker@.service tests/integration/conftest.py tests/integration/test_staging_antrean.py
git commit -m "feat(staging): klaim antrean dan worker khusus staging, detak, batal, dan log aktivitas"
```

---

### Task 14: Job `staging_tarik`

**Files:**
- Create: `src/wpmgr/staging/tarik.py`, `tests/integration/staging_palsu.py`, `tests/integration/test_staging_tarik.py`
- Modify: `src/wpmgr/jobs/handlers.py`

**Interfaces:**
- Consumes:
  - Task 9 (`isi_mu_plugin_staging`);
  - Task 11 (`aman`, `Pembantu`, `tulis_akses_router`);
  - Task 12 (`SiteClient.staging_*`, `rencana`, `Indeks`);
  - Task 13 (`umum`).
- Produces (`wpmgr.staging.tarik`):
  - validasi dan data: `urai_info(info) -> dict`, `urls_produksi(info) -> list[str]`, `jumlah_aktif(sesi, staging) -> int`;
  - penulisan berkas: `tulis_berkas(files: Path, path: str, isi: bytes, mtime: int) -> None`;
  - dipakai ulang dorong (Task 16): `ambil_manifest(sesi, job, staging, klien, dir_kerja, k) -> dict`, `class Salin(sesi, job, staging, klien, files, indeks, lokal, k)` dengan `paket(berkas)` dan `besar(entri)`, `ekspor_db(sesi, job, staging, klien, dir_sql, info, k, tahap_berikut) -> dict`, `urai_tabel(daftar, prefix) -> list[dict]`;
  - job: `tarik(sesi, job, site, staging, klien, pb, akhir_status=True) -> dict` (dipakai ulang uji update), `tangani_staging_tarik(sesi, job, klien) -> dict`, dan `HANDLER[JobType.staging_tarik]`.
  - `urai_info()` mengembalikan `{prefix, home, siteurl, charset, php, versi_php, php_peringatan, batas_unggah, tabel: [{nama, baris, ukuran, pk}], ukuran_db}`.
  - `job.payload["kemajuan"]`:
    - `tahap` ∈ `manifest|berkas|tanda_air|db|impor|penyiapan|sertifikat`;
    - `manifest_kursor`, `info`, `byte_total`, `byte_selesai`, `berkas_dilewati`;
    - `tabel: {nama: kursor}`, `tabel_seq: {nama: n}`, `tabel_selesai: [nama]`, `tanda_air`;
    - `peringatan: [str]` (maks 50), `mulai`, `diperbarui`.
  - Tata letak disk per site: `files/`, `tarik/manifest.jsonl`, `tarik/prelude.sql`, `tarik/db/<idx>-<seq>.sql`, `indeks.jsonl`, `log/`, `ekspor/`.
- Test helper `tests/integration/staging_palsu.py`:
  - `SECRET`;
  - `ProduksiPalsu`, dengan atribut yang bisa diatur test (`berkas`, `tabel`, `pk`, `info`, `tanda_air`, `halaman`, `maks_hash`, `maks_paket`, `jadwal_gagal`, `sebelum`, `ekstra_manifest`, `gagal_langkah`, `halaman_utama`) dan penghitung/perekam (`hitung`, `diminta`, `unggahan`, `langkah`, `sql_diterapkan`);
  - metode `klien(site) -> SiteClient`, `http() -> httpx.Client`;
  - `PembantuPalsu(dir_staging)` dengan `panggilan`, `sql`, `status_palsu`, `gagal`, `saat_wpcli`, `nama_panggilan()`.
  Dipakai Task 14–17.

Urutan tarik (spec §6.2):
1. manifest (berpaging, disimpan ke berkas lokal);
2. cek RAM, batas aktif, dan disk;
3. hapus berkas yang hilang di produksi, lalu ambil yang baru/berubah per paket atau per rentang;
4. tanda air;
5. ekspor tabel per potongan;
6. `db-buat` + `db-impor`;
7. mu-plugin, akses router, `router-muat`, `buat`, `search-replace`, `blog_public=0`, `cache flush`;
8. sertifikat (gagal tidak menggagalkan tarik).

Setiap tahap mencatat kemajuan dan melakukan commit, sehingga job yang diulang melanjutkan dari tahap dan kursornya. Satu entri yang rusak dilewati dan dihitung, tidak menggagalkan job.

- [ ] **Step 1: Tiruan connector dan pembantu.**

File: `tests/integration/staging_palsu.py`
```python
"""Tiruan connector produksi dan skrip pembantu untuk test integrasi staging."""

import hashlib
import json
from pathlib import Path

import httpx

from wpmgr.signing import verify
from wpmgr.site_client import SiteClient
from wpmgr.staging import paket
from wpmgr.staging.pembantu import StatusPembantu

GB = 1024**3
SECRET = "f" * 64
AWALAN = "/wp-json/wpmgr/v1"


def _json(kode: int, data) -> httpx.Response:
    return httpx.Response(kode, json=data)


def _galat(kode: int, kode_wp: str, pesan: str) -> httpx.Response:
    return _json(kode, {"code": kode_wp, "message": pesan, "data": {"status": kode}})


class ProduksiPalsu:
    """Connector 3.0 tiruan di atas kamus berkas dan potongan SQL per tabel."""

    def __init__(self) -> None:
        self.berkas: dict[str, tuple[bytes, int]] = {}
        self.tabel: dict[str, list[bytes]] = {}
        self.pk: dict[str, list[str]] = {}
        self.info = {"php": "8.1.29", "wp": "6.5", "table_prefix": "wp_", "charset": "utf8mb4",
                     "home": "https://contoh.test", "siteurl": "https://contoh.test", "multisite": False,
                     "konten_di_luar": False, "batas_unggah": 4194304, "tabel_dilewati": 0}
        self.tanda_air = {"sumber": {
            "posts": {"maks_id": 10, "jumlah": 5, "diubah": "2026-09-20 00:00:00", "diubah_sejak": None},
            "comments": {"maks_id": 3, "jumlah": 2}, "users": {"maks_id": 1, "jumlah": 1},
        }}
        self.halaman = 2
        self.maks_hash = 50 * 1024 * 1024
        self.maks_paket = 8 * 1024 * 1024
        self.jadwal_gagal: dict[str, set[int]] = {}
        self.sebelum: dict = {}
        self.ekstra_manifest: list = []
        self.jumlah_dilewati = 0
        self.gagal_langkah: str | None = None
        self.halaman_utama = 200
        self.hitung: dict[str, int] = {}
        self.diminta: list[tuple[str, object]] = []
        self.unggahan: dict[int, bytes] = {}
        self.langkah: list[str] = []
        self.sql_diterapkan = b""
        self._rencana: dict | None = None
        self._isi_baru: dict[str, bytes] = {}
        self._sql = b""

    def klien(self, site) -> SiteClient:
        return SiteClient(site.url, str(site.id), SECRET,
                          client=httpx.Client(transport=httpx.MockTransport(self.tangani)))

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(self.halaman_utama, text="<html><title>Produksi</title></html>")))

    def _entri(self, path: str) -> dict:
        isi, mtime = self.berkas[path]
        return {"path": path, "ukuran": len(isi), "mtime": mtime,
                "hash": hashlib.sha256(isi).hexdigest() if len(isi) <= self.maks_hash else None}

    def tangani(self, r: httpx.Request) -> httpx.Response:
        route = r.url.path.removeprefix(AWALAN)
        h = r.headers
        if not verify(SECRET, h.get("X-Wpmgr-Signature", ""), r.method, r.url.path,
                      int(h.get("X-Wpmgr-Timestamp", "0")), h.get("X-Wpmgr-Nonce", ""), r.content):
            return _galat(401, "wpmgr_ditolak", "Tanda tangan tidak cocok.")
        n = self.hitung[route] = self.hitung.get(route, 0) + 1
        if n in self.jadwal_gagal.get(route, set()):
            return httpx.Response(500, text="galat sementara")
        badan = json.loads(r.content) if r.content and route != "/staging/unggah" else None
        if route in self.sebelum:
            self.sebelum[route](self, n, badan)
        self.diminta.append((route, badan if badan is not None else dict(r.url.params)))
        return getattr(self, "_" + route.removeprefix("/staging/").replace("-", "_"))(r, badan)

    def _manifest(self, r, badan):
        kursor = r.url.params.get("kursor")
        urut = [p for p in sorted(self.berkas) if kursor is None or p > kursor]
        halaman, lagi = urut[:self.halaman], len(urut) > self.halaman
        data = {"berkas": [self._entri(p) for p in halaman], "dilewati": [], "jumlah_dilewati": 0,
                "kursor": halaman[-1] if lagi else None, "lagi": lagi}
        if kursor is None:
            data["berkas"] += self.ekstra_manifest
            data["jumlah_dilewati"] = self.jumlah_dilewati
            data["info"] = {**self.info, "tabel": [
                {"nama": t, "baris": 1, "ukuran": sum(len(c) for c in isi), "mesin": "InnoDB",
                 "pk": self.pk.get(t, ["id"])} for t, isi in sorted(self.tabel.items())]}
        return _json(200, data)

    def _file(self, r, badan):
        if "rentang" in badan:
            x = badan["rentang"]
            if x["path"] not in self.berkas:
                return _galat(404, "wpmgr_staging_tidak_ada", "Berkas tidak ada.")
            isi, mtime = self.berkas[x["path"]]
            bagian = isi[x["dari"]:x["dari"] + x["panjang"]]
            return httpx.Response(200, content=paket.susun({"berkas": [
                {"path": x["path"], "dari": x["dari"], "total": len(isi), "mtime": mtime}]}, [bagian]))
        meta, isi = [], []
        for p in badan["berkas"]:
            if p in self.berkas:
                meta.append({"path": p, "mtime": self.berkas[p][1]})
                isi.append(self.berkas[p][0])
            else:
                meta.append({"path": p, "hilang": True})
                isi.append(b"")
        if sum(len(x) for x in isi) > self.maks_paket:
            return _galat(413, "wpmgr_staging_terlalu_besar", "Permintaan melebihi batas 8 MB per potongan.")
        return httpx.Response(200, content=paket.susun({"berkas": meta}, isi))

    def _tabel(self, r, badan):
        nama, idx = badan["tabel"], int(badan["kursor"] or 0)
        potongan = self.tabel[nama]
        selesai = idx >= len(potongan) - 1
        meta = {"tabel": nama, "kursor": None if selesai else str(idx + 1), "selesai": selesai, "baris": 1,
                "berkas": [{"path": "sql"}]}
        return httpx.Response(200, content=paket.susun(meta, [potongan[idx]]))

    def _tanda_air(self, r, badan):
        return _json(200, {**self.tanda_air, "diambil": 1790000000})

    def _snapshot(self, r, badan):
        hasil = {"berkas": [
            {"path": p, "ada": True, "ukuran": len(self.berkas[p][0]), "mtime": self.berkas[p][1]}
            if p in self.berkas else {"path": p, "ada": False} for p in badan["paths"]]}
        if badan.get("awal"):
            hasil["tabel"] = [{"nama": t, "baris": 1, "ukuran": 1, "mesin": "InnoDB", "pk": self.pk.get(t, ["id"])}
                              for t in sorted(self.tabel)]
            hasil["tanda_air"] = {**self.tanda_air, "diambil": 1790000000}
        return _json(200, hasil)

    def _unggah(self, r, badan):
        meta, _ = paket.urai(r.content)
        self.unggahan[meta["nomor"]] = r.content
        return _json(200, {"ok": True, "nomor": meta["nomor"], "sha256": hashlib.sha256(r.content).hexdigest()})

    def _rakit(self, badan) -> None:
        rencana, self._sql, self._isi_baru = b"", b"", {}
        for nomor in sorted(self.unggahan):
            meta, bagian = paket.urai(self.unggahan[nomor])
            if meta["jenis"] == "rencana":
                rencana += bagian[0]
            elif meta["jenis"] == "sql":
                self._sql += bagian[0]
            else:
                for m, isi in zip(meta["berkas"], bagian):
                    self._isi_baru[m["path"]] = self._isi_baru.get(m["path"], b"") + isi
        assert hashlib.sha256(rencana).hexdigest() == badan["sha256_rencana"]
        self._rencana = json.loads(rencana)

    def _terapkan(self, r, badan):
        langkah = badan["langkah"]
        self.langkah.append(langkah)
        if self.gagal_langkah == langkah:
            return _galat(500, "wpmgr_staging_tukar", "Berkas baru tidak dapat dipasang: wp-content/x")
        if langkah == "siapkan":
            self._rakit(badan)
            return _json(200, {"selesai": True, "status": "siap"})
        if langkah == "impor":
            return _json(200, {"selesai": True, "status": "terimpor"})
        if langkah == "tukar":
            for b in self._rencana["berkas"]:
                self.berkas[b["path"]] = (self._isi_baru[b["path"]], b["mtime"])
            for p in self._rencana["hapus"]:
                self.berkas.pop(p, None)
            if self._rencana["sql"]:
                self.sql_diterapkan = self._sql
            return _json(200, {"selesai": True, "status": "ditukar"})
        return _json(200, {"selesai": True, "status": {"pulihkan": "dipulihkan", "selesai": "selesai"}[langkah]})

    def _bersihkan(self, r, badan):
        return _json(200, {"lagi": False})


class PembantuPalsu:
    """Skrip pembantu tiruan: mencatat panggilan dan menulis apa yang ditulis skrip asli."""

    def __init__(self, dir_staging: Path) -> None:
        self.dir = Path(dir_staging)
        self.panggilan: list[tuple] = []
        self.sql = b""
        self.status_palsu = StatusPembantu(8 * GB, 200 * GB, 150 * GB, {}, {})
        self.gagal: dict[str, Exception] = {}
        self.saat_wpcli = None

    def _catat(self, nama: str, *argumen) -> None:
        self.panggilan.append((nama, *argumen))
        if nama in self.gagal:
            raise self.gagal[nama]

    def nama_panggilan(self) -> list[str]:
        return [p[0] for p in self.panggilan]

    def siapkan(self):
        self._catat("siapkan")

    def buat(self, nama, versi, site_id):
        self._catat("buat", nama, versi, str(site_id))

    def jalan(self, nama):
        self._catat("jalan", nama)

    def jeda(self, nama):
        self._catat("jeda", nama)

    def hapus(self, nama):
        self._catat("hapus", nama)

    def db_hapus(self, nama):
        self._catat("db_hapus", nama)

    def db_buat(self, nama, site_id, prefix):
        self._catat("db_buat", nama, str(site_id), prefix)
        berkas = self.dir / str(site_id) / "files" / "wp-config.php"
        berkas.parent.mkdir(parents=True, exist_ok=True)
        berkas.write_bytes(b"<?php // wp-config staging")

    def db_impor(self, nama, berkas):
        self._catat("db_impor", nama, len(berkas))
        self.sql = b"".join(Path(b).read_bytes() for b in berkas)

    def wpcli(self, nama, *argumen):
        self._catat("wpcli", nama, *argumen)
        return (self.saat_wpcli(nama, argumen) if self.saat_wpcli else None) or ""

    def router_muat(self):
        self._catat("router_muat")

    def sertifikat(self, nama):
        self._catat("sertifikat", nama)

    def status(self):
        self._catat("status")
        return self.status_palsu
```

- [ ] **Step 2: Tulis test yang gagal.**

File: `tests/integration/test_staging_tarik.py`
```python
import errno
import hashlib
import os
import uuid
from datetime import datetime, timezone

import pytest
from staging_palsu import GB, PembantuPalsu, ProduksiPalsu

from wpmgr.errors import STAGING_DITOLAK, STAGING_GAGAL, TRANSIENT, SiteError
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
    Staging,
    StatusStaging,
)
from wpmgr.staging import rencana, tarik, umum
from wpmgr.staging.pembantu import GalatPembantu, StatusPembantu

pytestmark = pytest.mark.integration

MTIME = 1_700_000_000


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))


@pytest.fixture
def prod():
    p = ProduksiPalsu()
    p.berkas = {
        "index.php": (b"<?php // indeks", MTIME),
        "wp-content/themes/t/style.css": (b"body{}", MTIME),
        "wp-content/uploads/besar.bin": (bytes(range(256)) * 10, MTIME),
    }
    p.tabel = {
        "wp_posts": [b"DROP TABLE IF EXISTS `wp_posts`;\nCREATE TABLE `wp_posts` (`id` int);\n",
                     b"INSERT INTO `wp_posts` (`id`) VALUES ('1');\n"],
        "wp_options": [b"DROP TABLE IF EXISTS `wp_options`;\nCREATE TABLE `wp_options` (`a` text);\n"],
    }
    return p


@pytest.fixture
def pb(staging_aktif, monkeypatch):
    palsu = PembantuPalsu(staging_aktif)
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


@pytest.fixture(autouse=True)
def potongan_kecil(monkeypatch):
    # Berkas besar.bin (2560 byte) diambil lewat rentang 1000 byte.
    monkeypatch.setattr(rencana, "UKURAN_PAKET", 1000)
    monkeypatch.setattr(tarik, "UKURAN_PAKET", 1000)


def _jalankan(sesi, site_staging, prod, job=None):
    """Jalankan handler langsung; job yang selesai ditandai sukses supaya job staging berikutnya boleh dibuat."""
    site = sesi.get(Site, site_staging.site_id)
    job = job or buat_job(sesi, site.id, JobType.staging_tarik)
    hasil = tarik.tangani_staging_tarik(sesi, job, prod.klien(site))
    job.status = JobStatus.success
    sesi.commit()
    return job, hasil


def _gagalkan_job_tertunda(sesi):
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.failed
    sesi.commit()


def _files(staging_aktif, site_staging):
    return staging_aktif / str(site_staging.site_id) / "files"


def _paths_diminta(prod):
    return [p for route, b in prod.diminta if route == "/staging/file" and "berkas" in b for p in b["berkas"]]


def test_tarik_penuh_membuat_staging(sesi, site_staging, staging_aktif, prod, pb):
    _, hasil = _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    assert (files / "index.php").read_bytes() == b"<?php // indeks"
    assert (files / "wp-content/uploads/besar.bin").read_bytes() == bytes(range(256)) * 10
    assert int(os.stat(files / "index.php").st_mtime) == MTIME
    mu = (files / "wp-content/mu-plugins/wpmgr-staging.php").read_text(encoding="utf-8")
    assert "define( 'WPMGR_STAGING_NAMA', 'contoh-test' );" in mu
    assert sum(1 for route, b in prod.diminta if route == "/staging/file" and "rentang" in b) == 3

    assert pb.sql.startswith(b"SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;")
    assert pb.sql.index(b"CREATE TABLE `wp_options`") < pb.sql.index(b"CREATE TABLE `wp_posts`")
    assert pb.sql.index(b"CREATE TABLE `wp_posts`") < pb.sql.index(b"INSERT INTO `wp_posts`")
    assert ("db_buat", "contoh-test", str(site_staging.site_id), "wp_") in pb.panggilan
    assert ("buat", "contoh-test", "8.1", str(site_staging.site_id)) in pb.panggilan
    url = "https://contoh-test.staging.contoh.id"
    assert ("wpcli", "contoh-test", "search-replace", "https://contoh.test", url) in pb.panggilan
    assert ("wpcli", "contoh-test", "search-replace", "http://contoh.test", url) in pb.panggilan
    assert ("wpcli", "contoh-test", "option", "update", "blog_public", "0") in pb.panggilan
    assert pb.nama_panggilan().index("router_muat") < pb.nama_panggilan().index("buat")
    assert pb.nama_panggilan()[-1] == "sertifikat"

    router = staging_aktif / "router"
    assert (router / "contoh-test.rahasia").read_bytes() == b"e" * 64
    assert (router / "contoh-test.htpasswd").read_bytes().startswith(b"staging:$2b$")

    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap
    assert site_staging.aktif is True
    assert site_staging.versi_php == "8.1"
    assert site_staging.ditarik_pada is not None and site_staging.sertifikat_pada is not None
    assert site_staging.tanda_air["sumber"]["comments"] == {"maks_id": 3, "jumlah": 2}
    assert site_staging.ukuran_file == len(b"<?php // indeks") + len(b"body{}") + 2560
    assert not (staging_aktif / str(site_staging.site_id) / "tarik").exists()
    assert hasil["byte_disalin"] == site_staging.ukuran_file
    log = sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Staging dibuat%")).one()
    assert log.detail["byte_disalin"] == site_staging.ukuran_file


def test_segarkan_inkremental(sesi, site_staging, staging_aktif, prod, pb):
    _jalankan(sesi, site_staging, prod)
    prod.diminta.clear()
    prod.berkas["index.php"] = (b"<?php // berubah", MTIME + 5)
    del prod.berkas["wp-content/themes/t/style.css"]
    prod.berkas["wp-content/uploads/baru.jpg"] = (b"jpg", MTIME)
    _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    assert sorted(_paths_diminta(prod)) == ["index.php", "wp-content/uploads/baru.jpg"]
    assert (files / "index.php").read_bytes() == b"<?php // berubah"
    assert not (files / "wp-content/themes/t/style.css").exists()
    assert (files / "wp-config.php").exists()
    assert (files / "wp-content/mu-plugins/wpmgr-staging.php").exists()
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Staging disegarkan%")).count() == 1


def test_lanjut_setelah_putus_di_tengah(sesi, site_staging, staging_aktif, prod, pb):
    prod.halaman = 10
    prod.berkas.update({f"wp-content/uploads/{i}.txt": (b"x" * 600, MTIME) for i in range(3)})
    prod.jadwal_gagal["/staging/file"] = {2, 3, 4}
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == TRANSIENT
    job = sesi.query(Job).one()
    assert job.payload["kemajuan"]["tahap"] == "berkas"
    assert job.payload["kemajuan"]["byte_selesai"] > 0
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal or "dilanjutkan" in (site_staging.galat or "")
    _jalankan(sesi, site_staging, prod, job=job)
    diminta = _paths_diminta(prod)
    assert len(diminta) == len(set(diminta))
    assert (_files(staging_aktif, site_staging) / "wp-content/uploads/2.txt").read_bytes() == b"x" * 600


def test_tarik_produksi_berubah_di_tengah(sesi, site_staging, staging_aktif, prod, pb):
    prod.berkas["wp-content/uploads/hilang.txt"] = (b"akan dihapus", MTIME)

    def ubah_sebelum_paket(p, n, badan):
        if "berkas" in badan:
            p.berkas["index.php"] = (b"<?php // diubah setelah manifest", MTIME + 9)
            p.berkas.pop("wp-content/uploads/hilang.txt", None)
        elif badan["rentang"]["dari"] == 1000 and not getattr(p, "_sudah", False):
            p._sudah = True
            isi, _ = p.berkas["wp-content/uploads/besar.bin"]
            p.berkas["wp-content/uploads/besar.bin"] = (isi[::-1], MTIME + 1)

    prod.sebelum["/staging/file"] = ubah_sebelum_paket
    _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    assert (files / "index.php").read_bytes() == b"<?php // diubah setelah manifest"
    assert not (files / "wp-content/uploads/hilang.txt").exists()
    assert (files / "wp-content/uploads/besar.bin").read_bytes() == (bytes(range(256)) * 10)[::-1]
    indeks = tarik.Indeks(staging_aktif / str(site_staging.site_id) / "indeks.jsonl").muat()
    assert indeks["index.php"].hash == hashlib.sha256(b"<?php // diubah setelah manifest").hexdigest()
    assert "wp-content/uploads/hilang.txt" not in indeks


def test_tarik_disk_habis_gagal_jelas_dan_bisa_dilanjutkan(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    asli = tarik.tulis_berkas
    tulisan = []

    def tulis_lalu_penuh(files, path, isi, mtime):
        tulisan.append(path)
        if len(tulisan) == 2:
            raise OSError(errno.ENOSPC, "No space left on device", str(files / path))
        asli(files, path, isi, mtime)

    monkeypatch.setattr(tarik, "tulis_berkas", tulis_lalu_penuh)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_GAGAL
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal
    assert "Disk VPS penuh" in site_staging.galat
    assert str(staging_aktif) not in site_staging.galat
    pertama = tulisan[0]
    monkeypatch.setattr(tarik, "tulis_berkas", asli)
    _gagalkan_job_tertunda(sesi)
    prod.diminta.clear()
    _jalankan(sesi, site_staging, prod)
    assert pertama not in _paths_diminta(prod)
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap


def test_tarik_nama_non_ascii_dan_panjang(sesi, site_staging, staging_aktif, prod, pb):
    panjang = "wp-content/uploads/" + "é" * 60 + ".txt"
    prod.berkas["wp-content/uploads/ü-berkas.txt"] = (b"u", MTIME)
    prod.berkas[panjang] = (b"p", MTIME)
    prod.jumlah_dilewati = 2
    prod.ekstra_manifest = [
        {"path": "../evil.php", "ukuran": 1, "mtime": 1, "hash": None},
        {"path": "wp-content/x\x00y", "ukuran": 1, "mtime": 1, "hash": None},
        {"path": "/etc/passwd", "ukuran": 1, "mtime": 1, "hash": None},
        {"path": "wp-config.php", "ukuran": 1, "mtime": 1, "hash": None},
        {"path": "ok.txt", "ukuran": "1", "mtime": 1, "hash": None},
    ]
    _, hasil = _jalankan(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    assert (files / "wp-content/uploads/ü-berkas.txt").read_bytes() == b"u"
    assert (files / panjang).read_bytes() == b"p"
    assert not (files.parent / "evil.php").exists()
    assert "../evil.php" not in _paths_diminta(prod)
    assert any("7 berkas dilewati" in p for p in hasil["peringatan"])


def test_tarik_ditolak_ram_rendah(sesi, site_staging, prod, pb):
    pb.status_palsu = StatusPembantu(int(1.5 * GB), 200 * GB, 150 * GB, {}, {})
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_DITOLAK
    assert "RAM tersedia di VPS 1,5 GB" in e.value.pesan
    assert _paths_diminta(prod) == []


def test_tarik_ditolak_disk_tidak_cukup(sesi, site_staging, prod, pb):
    pb.status_palsu = StatusPembantu(8 * GB, 100 * GB, 15 * GB, {}, {})
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_DITOLAK
    assert "minimal 15%" in e.value.pesan


def test_tarik_ditolak_batas_staging_aktif(sesi, site_staging, prod, pb):
    for i in range(3):
        s = Site(id=uuid.uuid4(), nama=f"S{i}", url=f"https://s{i}.test", status=SiteStatus.active,
                 secret_terenkripsi=b"x")
        sesi.add(s)
        sesi.flush()
        sesi.add(Staging(site_id=s.id, nama=f"s{i}", aktif=True, status=StatusStaging.siap))
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert "Sudah ada 3 staging aktif" in e.value.pesan


def test_tarik_ditolak_tanpa_izin_connector(sesi, site_staging, prod, pb):
    site = sesi.get(Site, site_staging.site_id)
    site.fitur = ["self_update"]
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert "Izinkan staging" in e.value.pesan


def test_ekspor_tabel_dilanjutkan_dari_kursor(sesi, site_staging, prod, pb):
    prod.tabel["wp_posts"].append(b"INSERT INTO `wp_posts` (`id`) VALUES ('2');\n")
    prod.jadwal_gagal["/staging/tabel"] = {3, 4, 5}
    with pytest.raises(SiteError):
        _jalankan(sesi, site_staging, prod)
    job = sesi.query(Job).one()
    _jalankan(sesi, site_staging, prod, job=job)
    assert pb.sql.count(b"VALUES ('1')") == 1
    assert pb.sql.count(b"VALUES ('2')") == 1


@pytest.mark.parametrize("ubah,pesan", [
    ({"table_prefix": "wp_'; DROP"}, "prefix"),
    ({"multisite": True}, "multisite"),
    ({"konten_di_luar": True}, "wp-content"),
    ({"home": "javascript:alert(1)"}, "alamat"),
])
def test_info_berbahaya_ditolak(sesi, site_staging, prod, pb, ubah, pesan):
    prod.info.update(ubah)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert pesan in e.value.pesan


def test_batal_membersihkan_area_tarik(sesi, site_staging, staging_aktif, prod, pb):
    def minta_batal(p, n, badan):
        st = sesi.get(Staging, site_staging.id)
        st.batal_diminta_pada = datetime.now(timezone.utc)
        sesi.commit()

    prod.sebelum["/staging/tanda-air"] = minta_batal
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert "Dibatalkan" in e.value.pesan
    assert not (staging_aktif / str(site_staging.site_id) / "tarik").exists()


def test_galat_pembantu_menandai_gagal_tanpa_bocor(sesi, site_staging, prod, pb):
    pb.gagal["db_impor"] = GalatPembantu("impor", "Impor database staging gagal.")
    with pytest.raises(SiteError):
        _jalankan(sesi, site_staging, prod)
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.gagal
    assert site_staging.galat == "Impor database staging gagal."
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_staging_tarik.py -q`. Expected: `ImportError: cannot import name 'tarik' from 'wpmgr.staging'`.

- [ ] **Step 4: Implementasikan.**

File: `src/wpmgr/staging/tarik.py`
```python
"""Job staging_tarik: buat atau segarkan staging dari produksi (spec §6.2)."""

import errno
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from sqlalchemy import func, select

from wpmgr.config import get_settings
from wpmgr.connector_paket import isi_mu_plugin_staging
from wpmgr.crypto import dekripsi_secret
from wpmgr.errors import BAD_RESPONSE, BERKAS_HILANG, TERLALU_BESAR, SiteError
from wpmgr.fitur import STAGING, punya_fitur
from wpmgr.models import Staging, StatusStaging
from wpmgr.staging import umum
from wpmgr.staging.aman import (
    POLA_TABEL,
    PathTidakAman,
    angka,
    bersih_teks,
    jalur_di_dalam,
    path_sah,
    versi_php_staging,
)
from wpmgr.staging.indeks import Indeks
from wpmgr.staging.pembantu import GalatPembantu, tulis_akses_router
from wpmgr.staging.rencana import (
    UKURAN_PAKET,
    Entri,
    bagi_potongan,
    cek_disk,
    cek_maks_aktif,
    cek_ram,
    entri_dari,
    format_byte,
    selisih,
    urai_tanda_air,
)

MAKS_HALAMAN_MANIFEST = 2000
MAKS_POTONGAN_TABEL = 200_000
MAKS_PERINGATAN = 50
MAKS_TABEL = 2000
CHARSET_SAH = ("utf8mb4", "utf8", "utf8mb3", "latin1")
POLA_PREFIX = re.compile(r"[A-Za-z0-9_]{1,20}")
POLA_URL = re.compile(r"https?://[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/-]{0,200})?")
POLA_KURSOR = re.compile(r"[A-Za-z0-9+/=]{1,8192}")
# Galat berkas yang berarti server staging tidak sehat, bukan satu nama yang
# bermasalah: job dihentikan, bukan berkasnya dilewati.
ERRNO_FATAL = frozenset({errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.EIO})


def _tambah_peringatan(k: dict, teks: str) -> None:
    daftar = k.setdefault("peringatan", [])
    if len(daftar) < MAKS_PERINGATAN:
        daftar.append(bersih_teks(teks, 300))


def urai_tabel(daftar, prefix: str) -> list[dict]:
    """Daftar tabel dari connector: nama sah ber-prefix site, angka dijepit, PK yang bisa dikutip."""
    tabel = []
    for t in daftar if isinstance(daftar, list) else []:
        if not isinstance(t, dict) or len(tabel) >= MAKS_TABEL:
            continue
        nama = t.get("nama")
        if not isinstance(nama, str) or not POLA_TABEL.fullmatch(nama) or not nama.startswith(prefix):
            continue
        pk = t.get("pk") if isinstance(t.get("pk"), list) else []
        # Satu kolom PK yang tidak bisa dikutip aman: perlakukan sebagai tanpa PK.
        pk = pk if all(isinstance(c, str) and POLA_TABEL.fullmatch(c) for c in pk) else []
        tabel.append({"nama": nama, "baris": angka(t.get("baris"), 0, 2**62) or 0,
                      "ukuran": angka(t.get("ukuran"), 0, 2**62) or 0, "pk": pk})
    tabel.sort(key=lambda t: t["nama"])
    return tabel


def urai_info(info) -> dict:
    """Info site dari manifest halaman pertama, divalidasi sebelum dipakai di mana pun."""
    if not isinstance(info, dict):
        raise umum.galat_gagal("Manifest produksi tidak membawa info site.")
    if info.get("multisite") is True:
        raise umum.galat_ditolak("Site multisite belum didukung staging.")
    if info.get("konten_di_luar") is True:
        raise umum.galat_ditolak("Folder wp-content site ini berada di luar folder WordPress; belum didukung staging.")
    prefix = info.get("table_prefix")
    if not isinstance(prefix, str) or not POLA_PREFIX.fullmatch(prefix):
        raise umum.galat_gagal("Table prefix produksi tidak sah.")
    alamat = {}
    for kunci in ("home", "siteurl"):
        nilai = info.get(kunci)
        nilai = nilai.rstrip("/") if isinstance(nilai, str) else ""
        if not POLA_URL.fullmatch(nilai):
            raise umum.galat_gagal(f"Nilai {kunci} produksi bukan alamat yang sah.")
        alamat[kunci] = nilai
    charset = info.get("charset") if info.get("charset") in CHARSET_SAH else "utf8mb4"
    php = info.get("php") if isinstance(info.get("php"), str) else None
    versi, peringatan = versi_php_staging(php)
    tabel = urai_tabel(info.get("tabel"), prefix)
    return {
        "prefix": prefix, "home": alamat["home"], "siteurl": alamat["siteurl"], "charset": charset,
        "php": bersih_teks(php, 40), "versi_php": versi, "php_peringatan": peringatan,
        "batas_unggah": angka(info.get("batas_unggah"), 262144, 4194304) or 4194304,
        "tabel": tabel, "ukuran_db": sum(t["ukuran"] for t in tabel),
    }


def urls_produksi(info: dict) -> list[str]:
    """Alamat produksi yang diganti ke alamat staging, yang terpanjang lebih dulu."""
    urls = set()
    for u in (info["home"], info["siteurl"]):
        urls.add(u)
        if u.startswith("https://"):
            urls.add("http://" + u[len("https://"):])
    return sorted(urls, key=lambda u: (-len(u), u))


def jumlah_aktif(sesi, staging: Staging) -> int:
    return sesi.scalar(select(func.count()).select_from(Staging).where(
        Staging.aktif.is_(True), Staging.id != staging.id)) or 0


def tulis_berkas(files: Path, path: str, isi: bytes, mtime: int) -> None:
    tujuan = jalur_di_dalam(files, path)
    tujuan.parent.mkdir(parents=True, exist_ok=True)
    # Nama sementara pendek di direktori yang sama: akhiran pada nama asli
    # yang sudah 250 byte melanggar batas 255 byte per nama berkas.
    sementara = tujuan.parent / f".wpmgr-{uuid.uuid4().hex[:12]}.tmp"
    try:
        with open(sementara, "wb") as f:
            f.write(isi)
        os.utime(sementara, (mtime, mtime))
        os.replace(sementara, tujuan)
    except BaseException:
        sementara.unlink(missing_ok=True)
        raise


def _hapus_lokal(files: Path, path: str) -> None:
    try:
        jalur_di_dalam(files, path).unlink(missing_ok=True)
    except (PathTidakAman, IsADirectoryError, PermissionError):
        pass


def _galat_per_berkas(exc: OSError) -> bool:
    return exc.errno not in ERRNO_FATAL


def ambil_manifest(sesi, job, staging, klien, dir_kerja: Path, k: dict) -> dict:
    """Manifest produksi berpaging ke dir_kerja/manifest.jsonl (dipakai tarik dan dorong)."""
    berkas = dir_kerja / "manifest.jsonl"
    kursor = k.get("manifest_kursor")
    for _ in range(MAKS_HALAMAN_MANIFEST):
        umum.titik_potongan(sesi, job, staging)
        data = umum.ulangi(klien.staging_manifest, kursor)
        if kursor is None:
            k = umum.simpan_kemajuan(sesi, job, info=urai_info(data.get("info")))
        dilewati = angka(data.get("jumlah_dilewati"), 0, 10**9) or 0
        baris = []
        for item in data.get("berkas") if isinstance(data.get("berkas"), list) else []:
            e = entri_dari(item)
            if e is None:
                dilewati += 1
                continue
            baris.append(json.dumps({"p": e.path, "u": e.ukuran, "m": e.mtime, "h": e.hash}, ensure_ascii=False))
        if baris:
            with open(berkas, "a", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(baris) + "\n")
        lagi = data.get("lagi") is True
        baru = data.get("kursor")
        if lagi:
            try:
                path_sah(baru)
            except PathTidakAman:
                raise umum.galat_gagal("Kursor manifest dari produksi tidak sah.") from None
            if baru == kursor:
                raise umum.galat_gagal("Manifest produksi tidak maju; periksa connector di site.")
        k = umum.simpan_kemajuan(sesi, job, manifest_kursor=baru if lagi else None,
                                 berkas_dilewati=k.get("berkas_dilewati", 0) + dilewati)
        if not lagi:
            return k
        kursor = baru
    raise umum.galat_gagal("Manifest produksi terlalu panjang.")


def _ambil_paket(klien, paths: list[str]) -> tuple[list[dict], list[bytes]]:
    def ambil():
        meta, bagian = klien.staging_file(paths)
        entri = meta.get("berkas")
        if not isinstance(entri, list) or len(entri) != len(paths) or any(
                not isinstance(m, dict) or m.get("path") != p for m, p in zip(entri, paths)):
            raise SiteError(BAD_RESPONSE, "Paket berkas tidak sesuai permintaan.")
        return entri, bagian

    return umum.ulangi(ambil)


def _ambil_rentang(klien, path: str, dari: int, panjang: int) -> tuple[dict, bytes]:
    def ambil():
        meta, bagian = klien.staging_rentang(path, dari, panjang)
        m = (meta.get("berkas") or [None])[0]
        if not isinstance(m, dict) or m.get("path") != path or m.get("dari") != dari \
                or angka(m.get("total"), 0, 2**50) is None or angka(m.get("mtime"), 0, 2**40) is None:
            raise SiteError(BAD_RESPONSE, "Rentang berkas tidak sesuai permintaan.")
        return m, bagian[0]

    return umum.ulangi(ambil)


class Salin:
    """Keadaan penyalinan berkas satu job: tempat menulis, indeks, dan kemajuan."""

    def __init__(self, sesi, job, staging, klien, files: Path, indeks: Indeks, lokal: dict, k: dict) -> None:
        self.sesi, self.job, self.staging, self.klien = sesi, job, staging, klien
        self.files, self.indeks, self.lokal, self.k = files, indeks, lokal, k

    def _maju(self, byte: int) -> None:
        self.k = umum.simpan_kemajuan(self.sesi, self.job, byte_selesai=self.k.get("byte_selesai", 0) + byte,
                                      peringatan=self.k.get("peringatan", []))

    def _catat(self, e: Entri) -> None:
        self.indeks.catat(e)
        self.lokal[e.path] = e

    def _hilang(self, path: str) -> None:
        _hapus_lokal(self.files, path)
        self.indeks.catat_hapus(path)
        self.lokal.pop(path, None)

    def _tulis(self, path: str, isi: bytes, mtime: int) -> bool:
        try:
            tulis_berkas(self.files, path, isi, mtime)
        except PathTidakAman:
            _tambah_peringatan(self.k, f"Path {path} tidak aman; dilewati.")
            return False
        except OSError as exc:
            if not _galat_per_berkas(exc):
                raise
            _tambah_peringatan(self.k, f"Berkas {path} tidak dapat ditulis (errno {exc.errno}); dilewati.")
            return False
        return True

    def paket(self, berkas: tuple[Entri, ...]) -> None:
        paths = [e.path for e in berkas]
        try:
            entri, bagian = _ambil_paket(self.klien, paths)
        except SiteError as exc:
            if exc.error_class != TERLALU_BESAR:
                # Spec §12: galat akhir menyebut berkasnya.
                lain = " dan lainnya" if len(paths) > 1 else ""
                raise SiteError(exc.error_class, f"{exc.pesan} (berkas {paths[0]}{lain})") from exc
            # Berkas tumbuh sejak manifest: diambil satu per satu lewat rentang.
            for e in berkas:
                self.besar(e)
            return
        total = 0
        for e, m, isi in zip(berkas, entri, bagian):
            if m.get("hilang") is True:
                self._hilang(e.path)
                continue
            mtime = angka(m.get("mtime"), 0, 2**40) or e.mtime
            if self._tulis(e.path, isi, mtime):
                self._catat(Entri(e.path, len(isi), mtime, hashlib.sha256(isi).hexdigest()))
                total += len(isi)
        self._maju(total)

    def besar(self, e: Entri) -> None:
        for _ in range(3):
            try:
                tujuan = jalur_di_dalam(self.files, e.path)
            except PathTidakAman:
                _tambah_peringatan(self.k, f"Path {e.path} tidak aman; dilewati.")
                return
            tujuan.parent.mkdir(parents=True, exist_ok=True)
            sementara = tujuan.parent / f".wpmgr-{uuid.uuid4().hex[:12]}.tmp"
            dari, total, mtime, stabil = 0, None, None, True
            h = hashlib.sha256()
            try:
                with open(sementara, "wb") as f:
                    while True:
                        umum.titik_potongan(self.sesi, self.job, self.staging)
                        try:
                            m, isi = _ambil_rentang(self.klien, e.path, dari, UKURAN_PAKET)
                        except SiteError as exc:
                            if exc.error_class != BERKAS_HILANG:
                                raise
                            f.close()
                            sementara.unlink(missing_ok=True)
                            self._hilang(e.path)
                            return
                        if total is None:
                            total, mtime = m["total"], m["mtime"]
                        elif (m["total"], m["mtime"]) != (total, mtime):
                            stabil = False
                            break
                        f.write(isi)
                        h.update(isi)
                        dari += len(isi)
                        self._maju(len(isi))
                        if dari >= total or not isi:
                            break
                if stabil and dari == total:
                    os.utime(sementara, (mtime, mtime))
                    os.replace(sementara, tujuan)
                    self._catat(Entri(e.path, total, mtime, h.hexdigest()))
                    return
            finally:
                sementara.unlink(missing_ok=True)
        _tambah_peringatan(self.k, f"Berkas {e.path} terus berubah selama disalin; dilewati.")
        self._maju(0)


def _sinkron_berkas(sesi, job, staging, klien, akar: Path, produksi: dict, k: dict) -> dict:
    files = akar / "files"
    indeks = Indeks(akar / "indeks.jsonl")
    lokal = indeks.muat()
    beda = selisih(produksi, lokal)
    salin = Salin(sesi, job, staging, klien, files, indeks, lokal, k)
    for path in beda.hapus:
        salin._hilang(path)
    for pot in bagi_potongan(beda.diambil, ukuran_paket=UKURAN_PAKET):
        umum.titik_potongan(sesi, job, staging)
        if pot.jenis == "rentang":
            if pot.dari == 0:
                salin.besar(pot.berkas[0])
        else:
            salin.paket(pot.berkas)
    return umum.simpan_kemajuan(sesi, job, tahap="tanda_air", peringatan=salin.k.get("peringatan", []))


def _ambil_tabel(klien, nama: str, kursor: str | None) -> tuple[bool, str | None, bytes]:
    def ambil():
        meta, bagian = klien.staging_tabel(nama, kursor)
        selesai = meta.get("selesai")
        baru = meta.get("kursor")
        if meta.get("tabel") != nama or not isinstance(selesai, bool) or len(bagian) != 1 or (
                not selesai and (not isinstance(baru, str) or not POLA_KURSOR.fullmatch(baru) or baru == kursor)):
            raise SiteError(BAD_RESPONSE, f"Potongan tabel {nama} tidak sah.")
        return selesai, None if selesai else baru, bagian[0]

    return umum.ulangi(ambil)


def ekspor_db(sesi, job, staging, klien, dir_sql: Path, info: dict, k: dict, tahap_berikut: str) -> dict:
    """Ekspor tabel per potongan ke dir_sql/db/ (dipakai tarik dan snapshot dorong)."""
    db_dir = dir_sql / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    (dir_sql / "prelude.sql").write_bytes(
        f"SET NAMES {info['charset']};\nSET FOREIGN_KEY_CHECKS=0;\nSET UNIQUE_CHECKS=0;\n"
        f"SET SQL_MODE='NO_AUTO_VALUE_ON_ZERO';\n".encode("ascii"))
    for idx, t in enumerate(info["tabel"]):
        nama = t["nama"]
        if nama in k.get("tabel_selesai", []):
            continue
        if not t["pk"]:
            _tambah_peringatan(k, f"Tabel {nama} tanpa primary key disalin dengan LIMIT/OFFSET; "
                                  "baris yang berubah selama tarik bisa terlewat atau ganda.")
        kursor = (k.get("tabel") or {}).get(nama) or None
        seq = (k.get("tabel_seq") or {}).get(nama, 0)
        # Potongan yang tertulis tetapi belum tercatat (terputus setelah
        # menulis) dibuang supaya INSERT tidak tergandakan.
        for sisa in db_dir.glob(f"{idx:04d}-*.sql"):
            if int(sisa.stem.split("-")[1]) >= seq:
                sisa.unlink()
        for _ in range(MAKS_POTONGAN_TABEL):
            umum.titik_potongan(sesi, job, staging)
            try:
                selesai, kursor, sql = _ambil_tabel(klien, nama, kursor)
            except SiteError as exc:
                raise SiteError(exc.error_class, f"{exc.pesan} (tabel {nama})") from exc
            (db_dir / f"{idx:04d}-{seq:06d}.sql").write_bytes(sql)
            seq += 1
            k = umum.simpan_kemajuan(
                sesi, job,
                tabel={**(k.get("tabel") or {}), nama: kursor or ""},
                tabel_seq={**(k.get("tabel_seq") or {}), nama: seq},
                tabel_selesai=list(k.get("tabel_selesai") or []) + ([nama] if selesai else []),
                peringatan=k.get("peringatan", []),
            )
            if selesai:
                break
        else:
            raise umum.galat_gagal(f"Tabel {nama} terlalu besar untuk disalin.")
    return umum.simpan_kemajuan(sesi, job, tahap=tahap_berikut)


def _siapkan_runtime(staging: Staging, site, pb, akar: Path, info: dict) -> None:
    s = get_settings()
    mu = jalur_di_dalam(akar / "files", "wp-content/mu-plugins/wpmgr-staging.php")
    mu.parent.mkdir(parents=True, exist_ok=True)
    mu.write_bytes(isi_mu_plugin_staging(staging.nama).encode("utf-8"))
    tulis_akses_router(s.jalur_staging, staging.nama, staging.sandi_hash,
                       dekripsi_secret(staging.rahasia_router_terenkripsi))
    pb.router_muat()
    pb.buat(staging.nama, info["versi_php"], site.id)
    url = umum.url_staging(staging)
    for asal in urls_produksi(info):
        pb.wpcli(staging.nama, "search-replace", asal, url)
    pb.wpcli(staging.nama, "option", "update", "blog_public", "0")
    pb.wpcli(staging.nama, "cache", "flush")


def tarik(sesi, job, site, staging: Staging, klien, pb, akhir_status: bool = True) -> dict:
    if not punya_fitur(site, STAGING):
        raise umum.galat_ditolak("Connector site ini belum mengizinkan staging. Aktifkan 'Izinkan staging' "
                                 "di Pengaturan -> WP Manager (connector 3.0).")
    if not staging.sandi_hash or not staging.rahasia_router_terenkripsi:
        raise umum.galat_ditolak("Akses preview staging belum dibuat; buat ulang kata sandi preview.")
    akar = umum.dir_site(site.id)
    tarik_dir = akar / "tarik"
    pertama = staging.ditarik_pada is None
    k = umum.kemajuan(job)
    # "tahap" (bukan sekadar kemajuan kosong): uji update memakai kemajuan
    # yang sama dan sudah menyimpan tahap_uji sebelum memanggil tarik().
    if "tahap" not in k:
        shutil.rmtree(tarik_dir, ignore_errors=True)
        k = umum.simpan_kemajuan(sesi, job, tahap="manifest", mulai=umum.sekarang().isoformat(),
                                 byte_selesai=0, byte_total=0, peringatan=[], berkas_dilewati=0)
    for d in (akar / "files", tarik_dir, akar / "log", akar / "ekspor"):
        d.mkdir(parents=True, exist_ok=True)

    try:
        status = pb.status()
        if not staging.aktif:
            pesan = cek_ram(status) or cek_maks_aktif(jumlah_aktif(sesi, staging), get_settings().staging_maks_aktif)
            if pesan:
                raise umum.galat_ditolak(pesan)

        if k["tahap"] == "manifest":
            k = ambil_manifest(sesi, job, staging, klien, tarik_dir, k)
            produksi = Indeks(tarik_dir / "manifest.jsonl").muat()
            lokal = Indeks(akar / "indeks.jsonl").muat()
            beda = selisih(produksi, lokal)
            pesan = cek_disk(status, beda.byte + 2 * k["info"]["ukuran_db"])
            if pesan:
                raise umum.galat_ditolak(pesan)
            if k.get("berkas_dilewati"):
                _tambah_peringatan(k, f"{k['berkas_dilewati']} berkas dilewati (nama bukan UTF-8, symlink, "
                                      "tidak terbaca, atau path tidak sah).")
            if k["info"]["php_peringatan"]:
                _tambah_peringatan(k, f"PHP produksi {k['info']['php'] or 'tidak diketahui'} tidak tersedia; "
                                      f"staging memakai PHP {k['info']['versi_php']}.")
            k = umum.simpan_kemajuan(sesi, job, tahap="berkas", byte_total=beda.byte, byte_selesai=0,
                                     peringatan=k.get("peringatan", []))

        info = k["info"]
        if k["tahap"] == "berkas":
            produksi = Indeks(tarik_dir / "manifest.jsonl").muat()
            k = _sinkron_berkas(sesi, job, staging, klien, akar, produksi, k)

        if k["tahap"] == "tanda_air":
            umum.titik_potongan(sesi, job, staging)
            ta = urai_tanda_air(umum.ulangi(klien.staging_tanda_air))
            if ta is None:
                raise umum.galat_gagal("Tanda air produksi tidak dapat dibaca.")
            k = umum.simpan_kemajuan(sesi, job, tahap="db", tanda_air=ta, tabel={}, tabel_seq={}, tabel_selesai=[])

        if k["tahap"] == "db":
            k = ekspor_db(sesi, job, staging, klien, tarik_dir, info, k, "impor")

        if k["tahap"] == "impor":
            umum.titik_potongan(sesi, job, staging)
            pb.db_buat(staging.nama, site.id, info["prefix"])
            pb.db_impor(staging.nama, [tarik_dir / "prelude.sql", *sorted((tarik_dir / "db").glob("*.sql"))])
            k = umum.simpan_kemajuan(sesi, job, tahap="penyiapan")

        if k["tahap"] == "penyiapan":
            umum.titik_potongan(sesi, job, staging)
            _siapkan_runtime(staging, site, pb, akar, info)
            k = umum.simpan_kemajuan(sesi, job, tahap="sertifikat")
    except umum.Dibatalkan:
        shutil.rmtree(tarik_dir, ignore_errors=True)
        raise

    sertifikat_ok = True
    try:
        pb.sertifikat(staging.nama)
    except GalatPembantu as exc:
        # Staging tetap bisa dibuka lewat SSO dashboard (spec §12); cron
        # renew-staging-certs mencoba lagi.
        sertifikat_ok = False
        _tambah_peringatan(k, f"Sertifikat belum terbit: {exc.pesan}")

    lokal = Indeks(akar / "indeks.jsonl").muat()
    Indeks(akar / "indeks.jsonl").padatkan(lokal)
    (akar / "log" / "diubah").unlink(missing_ok=True)
    shutil.rmtree(tarik_dir, ignore_errors=True)

    st = sesi.get(Staging, staging.id, populate_existing=True)
    sekarang = umum.sekarang()
    if akhir_status:
        st.status = StatusStaging.siap
    st.aktif = True
    st.versi_php = info["versi_php"]
    st.ditarik_pada = sekarang
    st.diubah_pada = None
    st.tanda_air = k["tanda_air"]
    st.ukuran_file = min(sum(e.ukuran for e in lokal.values()), 2**62)
    st.ukuran_db = min(info["ukuran_db"], 2**62)
    if sertifikat_ok:
        st.sertifikat_pada = sekarang
    hasil = {
        "byte_disalin": k.get("byte_selesai", 0),
        "ukuran_file": st.ukuran_file,
        "ukuran_db": st.ukuran_db,
        "versi_php": info["versi_php"],
        "peringatan": k.get("peringatan", []),
    }
    umum.catat_aktivitas(sesi, site.id, job, "Staging dibuat" if pertama else "Staging disegarkan", {
        **hasil, "ukuran_file_teks": format_byte(st.ukuran_file), "peringatan": hasil["peringatan"][:10]})
    sesi.commit()
    return hasil


def tangani_staging_tarik(sesi, job, klien) -> dict:
    def inti(sesi, job, site, staging):
        return tarik(sesi, job, site, staging, klien, umum.buat_pembantu())

    return umum.jalankan_staging(sesi, job, inti, StatusStaging.menyalin, "Tarik staging")
```

Di `src/wpmgr/jobs/handlers.py`, tambahkan impor `from wpmgr.staging.tarik import tangani_staging_tarik` dan entri `JobType.staging_tarik: tangani_staging_tarik,` ke `HANDLER`.

- [ ] **Step 5: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/integration/test_staging_tarik.py -q`. Expected: `16 passed`. Lalu seluruh unit dan integrasi, dan `ruff check .`.

- [ ] **Step 6: Commit.**

```bash
git add src/wpmgr/staging/tarik.py src/wpmgr/jobs/handlers.py tests/integration/staging_palsu.py tests/integration/test_staging_tarik.py
git commit -m "feat(staging): job staging_tarik yang bisa dilanjutkan, inkremental, dan tahan masukan rusak"
```

---

### Task 15: Job `staging_uji_update`

**Files:**
- Create: `src/wpmgr/staging/uji.py`, `tests/unit/test_staging_uji_nilai.py`, `tests/integration/test_staging_uji.py`
- Modify: `src/wpmgr/jobs/handlers.py`

**Interfaces:**
- Consumes: Task 14 (`tarik.tarik`), Task 13 (`umum`), Task 11 (`cookie_akses`), Lapis 2 (`TrafficRincian`).
- Produces (`wpmgr.staging.uji`):
  - paket: `slug_wpcli(tipe, slug) -> str | None`, `urai_paket(daftar) -> list[dict]` (`{tipe, slug, wpcli, dari, ke}`, maks 20; `ValueError` bila tidak sah);
  - halaman dan log: `jalur_uji(sesi, site_id, hari_ini) -> list[str]` (`/`, `/wp-login.php`, dan 3 path teratas traffic plugin 30 hari), `probe(http, dasar, host, cookie, jalur) -> dict` (`{status, judul, ukuran}`), `baca_log_baru(berkas, posisi) -> list[str]`;
  - penilaian: `nilai_uji(sebelum, sesudah, fatal_baru, update_gagal) -> tuple[str, list[str]]`;
  - job: `tangani_staging_uji_update(sesi, job, klien) -> dict` dan `HANDLER[JobType.staging_uji_update]`.
  - Payload job: `{"paket": [{tipe, slug, dari, ke}], "konfirmasi": bool}`. Kemajuan uji disimpan di `kemajuan` dengan kunci `tahap_uji` (`tarik|sebelum|update|sesudah|nilai`), `jalur`, `sebelum`, `sesudah`, `log_posisi`, `update`, dan `fatal_baru`, di samping kunci milik tarik.
  - Baris `StagingUji`: `paket = [{tipe, slug, dari, ke}]`, `hasil = "lolos"|"gagal"`, `pemeriksaan = {halaman: [{jalur, sebelum, sesudah}], fatal_baru, update, alasan}`.

Aturan penilaian (spec §8.1 langkah 7): **lolos** hanya bila semua update berhasil, setiap halaman sesudah update 2xx/3xx, tidak ada baris `PHP Fatal error`/`PHP Parse error` baru di log PHP staging, dan ukuran HTML setiap halaman tidak turun lebih dari 50%. Selain itu hasilnya **gagal**, dengan satu alasan per pemeriksaan. Update yang dijalankan uji sendiri tidak dihitung sebagai "staging diubah pengguna": penanda `log/diubah` dibuang setelah uji.

- [ ] **Step 1: Tulis test murni yang gagal.**

File: `tests/unit/test_staging_uji_nilai.py`
```python
import httpx
import pytest

from wpmgr.staging.uji import baca_log_baru, nilai_uji, probe, slug_wpcli, urai_paket


@pytest.mark.parametrize("tipe,slug,hasil", [
    ("plugin", "akismet/akismet.php", "akismet"), ("plugin", "hello.php", "hello"),
    ("theme", "twentytwentyfour", "twentytwentyfour"), ("core", "core", "core"),
    ("plugin", "--exec/x.php", None), ("theme", "../x", None), ("plugin", "Akismet/a.php", None),
])
def test_slug_wpcli(tipe, slug, hasil):
    assert slug_wpcli(tipe, slug) == hasil


def test_urai_paket():
    hasil = urai_paket([{"tipe": "plugin", "slug": "akismet/akismet.php", "dari": "5.2", "ke": "5.3.1"}])
    assert hasil == [{"tipe": "plugin", "slug": "akismet/akismet.php", "wpcli": "akismet", "dari": "5.2", "ke": "5.3.1"}]
    for buruk in (None, [], [{"tipe": "plugin", "slug": "a/a.php", "ke": "1;id"}],
                  [{"tipe": "eval", "slug": "a", "ke": "1"}], [{"tipe": "plugin", "slug": 5, "ke": "1"}],
                  [{"tipe": "plugin", "slug": "a/a.php", "ke": "1"}] * 21):
        with pytest.raises(ValueError):
            urai_paket(buruk)


H = {"status": 200, "judul": "Beranda", "ukuran": 10000}


def test_nilai_lolos():
    assert nilai_uji({"/": H}, {"/": {**H, "ukuran": 6000}}, [], []) == ("lolos", [])
    assert nilai_uji({"/": H}, {"/": {**H, "status": 302}}, [], [])[0] == "lolos"


def test_nilai_gagal_dengan_alasan_per_pemeriksaan():
    hasil, alasan = nilai_uji(
        {"/": H, "/toko/": H, "/wp-login.php": H},
        {"/": {**H, "status": 500}, "/toko/": {**H, "ukuran": 4000}, "/wp-login.php": {**H, "status": 0, "ukuran": 0}},
        ["PHP Fatal error: x"], [{"slug": "akismet/akismet.php", "ok": False, "pesan": "Perintah wp-cli di staging gagal."}],
    )
    assert hasil == "gagal"
    assert alasan == [
        "Update akismet/akismet.php gagal: Perintah wp-cli di staging gagal.",
        "/ membalas HTTP 500",
        "/toko/ menyusut dari 9,8 KB ke 3,9 KB",
        "/wp-login.php tidak dapat dihubungi",
        "1 error fatal baru di log PHP staging",
    ]


def test_baca_log_baru_hanya_bagian_baru_dan_tanpa_path_container(tmp_path):
    log = tmp_path / "php-error.log"
    log.write_bytes(b"[x] PHP Fatal error:  lama in /var/www/html/a.php:1\n")
    posisi = log.stat().st_size
    with open(log, "ab") as f:
        f.write(b"[y] PHP Warning:  bukan fatal\n")
        f.write(b"[z] PHP Fatal error:  Uncaught Error: x() in /var/www/html/wp-content/plugins/p/p.php:3\n")
        f.write(b"[z] PHP Parse error:  \xff sintaks\n")
    baru = baca_log_baru(log, posisi)
    assert baru == ["[z] PHP Fatal error:  Uncaught Error: x() in wp-content/plugins/p/p.php:3",
                    "[z] PHP Parse error:  � sintaks"]
    assert baca_log_baru(log, 10**9)[0].startswith("[x] PHP Fatal error")
    assert baca_log_baru(tmp_path / "tidak-ada.log", 0) == []


def test_probe_membaca_judul_dan_ukuran():
    diminta = []
    isi = "<html><head><title>\n Toko\tContoh </title></head></html>"

    def h(r):
        diminta.append(r)
        return httpx.Response(200, text=isi)

    hasil = probe(httpx.Client(transport=httpx.MockTransport(h)), "http://127.0.0.1:8090", "t.staging.id",
                  "wpmgr_stg_m=a; wpmgr_stg_e=1", "/toko/")
    assert hasil == {"status": 200, "judul": "Toko Contoh", "ukuran": len(isi.encode())}
    assert diminta[0].headers["Host"] == "t.staging.id"
    assert diminta[0].headers["Cookie"] == "wpmgr_stg_m=a; wpmgr_stg_e=1"
    assert str(diminta[0].url) == "http://127.0.0.1:8090/toko/"


def test_probe_gagal_koneksi():
    def h(r):
        raise httpx.ConnectError("tolak", request=r)

    assert probe(httpx.Client(transport=httpx.MockTransport(h)), "http://x", "h", "", "/") == \
        {"status": 0, "judul": None, "ukuran": 0}
```

- [ ] **Step 2: Tulis test integrasi yang gagal.**

File: `tests/integration/test_staging_uji.py`
```python
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from staging_palsu import PembantuPalsu, ProduksiPalsu

from wpmgr.errors import STAGING_DITOLAK, SiteError
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    Site,
    StagingUji,
    StatusStaging,
    TrafficRincian,
)
from wpmgr.staging import uji, umum
from wpmgr.staging.pembantu import GalatPembantu

pytestmark = pytest.mark.integration

HTML = "<html><title>Beranda</title>" + "x" * 5000 + "</html>"
PAKET = [{"tipe": "plugin", "slug": "akismet/akismet.php", "dari": "5.2", "ke": "5.3.1"}]


class WebStaging:
    """Router staging tiruan: isi halaman berganti setelah update dijalankan."""

    def __init__(self):
        self.fase = "sebelum"
        self.halaman = {"sebelum": {}, "sesudah": {}}
        self.diminta = []

    def http(self):
        return httpx.Client(transport=httpx.MockTransport(self.tangani))

    def tangani(self, r):
        self.diminta.append(r)
        status, isi = self.halaman[self.fase].get(r.url.path, (200, HTML))
        return httpx.Response(status, text=isi)


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))


@pytest.fixture
def prod():
    p = ProduksiPalsu()
    p.berkas = {"index.php": (b"<?php", 1700000000)}
    p.tabel = {"wp_posts": [b"CREATE TABLE `wp_posts` (`id` int);\n"]}
    return p


@pytest.fixture
def web(monkeypatch):
    w = WebStaging()
    monkeypatch.setattr(umum, "buat_http", w.http)
    return w


@pytest.fixture
def pb(staging_aktif, monkeypatch, web):
    palsu = PembantuPalsu(staging_aktif)

    def saat_wpcli(nama, argumen):
        if argumen[:2] == ("plugin", "update"):
            web.fase = "sesudah"

    palsu.saat_wpcli = saat_wpcli
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


def _jalankan(sesi, site_staging, prod, konfirmasi=False):
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_uji_update, {"paket": PAKET, "konfirmasi": konfirmasi})
    return uji.tangani_staging_uji_update(sesi, job, prod.klien(site))


def test_lolos_dengan_halaman_traffic(sesi, site_staging, prod, pb, web):
    for kunci, n in (("/toko/", 50), ("/kontak/", 30), ("/blog/", 20), ("/jarang/", 1), ("javascript:x", 99)):
        sesi.add(TrafficRincian(site_id=site_staging.site_id, tanggal=datetime.now(timezone.utc).date() - timedelta(days=1),
                                sumber="plugin", dimensi="halaman", kunci=kunci, kunjungan=n))
    sesi.commit()
    hasil = _jalankan(sesi, site_staging, prod)
    assert hasil["hasil"] == "lolos"
    assert ("wpcli", "contoh-test", "plugin", "update", "akismet", "--version=5.3.1") in pb.panggilan
    u = sesi.query(StagingUji).one()
    assert u.hasil == "lolos"
    assert [h["jalur"] for h in u.pemeriksaan["halaman"]] == ["/", "/wp-login.php", "/toko/", "/kontak/", "/blog/"]
    assert u.pemeriksaan["halaman"][0]["sebelum"]["judul"] == "Beranda"
    assert u.paket == [{"tipe": "plugin", "slug": "akismet/akismet.php", "dari": "5.2", "ke": "5.3.1"}]
    assert web.diminta[0].headers["Host"] == "contoh-test.staging.contoh.id"
    assert "wpmgr_stg_m=" in web.diminta[0].headers["Cookie"]
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap
    assert site_staging.diubah_pada is None
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Uji update di staging: lolos%")).count() == 1


def test_gagal_karena_fatal_baru(sesi, site_staging, staging_aktif, prod, pb, web):
    log = staging_aktif / str(site_staging.site_id) / "log" / "php-error.log"

    def saat_wpcli(nama, argumen):
        if argumen[:2] == ("plugin", "update"):
            web.fase = "sesudah"
            with open(log, "ab") as f:
                f.write(b"[26-Sep-2026] PHP Fatal error:  Uncaught Error in /var/www/html/wp-content/plugins/akismet/a.php:9\n")

    pb.saat_wpcli = saat_wpcli
    hasil = _jalankan(sesi, site_staging, prod)
    assert hasil["hasil"] == "gagal"
    u = sesi.query(StagingUji).one()
    assert "1 error fatal baru di log PHP staging" in u.pemeriksaan["alasan"]
    assert u.pemeriksaan["fatal_baru"] == [
        "[26-Sep-2026] PHP Fatal error:  Uncaught Error in wp-content/plugins/akismet/a.php:9"]


def test_gagal_karena_halaman_menyusut_lebih_dari_separuh(sesi, site_staging, prod, pb, web):
    web.halaman["sesudah"]["/"] = (200, "<html><title>Beranda</title>" + "x" * 1000 + "</html>")
    assert _jalankan(sesi, site_staging, prod)["hasil"] == "gagal"
    alasan = sesi.query(StagingUji).one().pemeriksaan["alasan"]
    assert any(a.startswith("/ menyusut dari") for a in alasan)


def test_gagal_karena_5xx(sesi, site_staging, prod, pb, web):
    web.halaman["sesudah"]["/wp-login.php"] = (500, "Galat")
    assert _jalankan(sesi, site_staging, prod)["hasil"] == "gagal"
    assert "/wp-login.php membalas HTTP 500" in sesi.query(StagingUji).one().pemeriksaan["alasan"]


def test_gagal_karena_update_gagal(sesi, site_staging, prod, pb, web):
    def saat_wpcli(nama, argumen):
        if argumen[:2] == ("plugin", "update"):
            raise GalatPembantu("wpcli", "Perintah wp-cli di staging gagal.")

    pb.saat_wpcli = saat_wpcli
    assert _jalankan(sesi, site_staging, prod)["hasil"] == "gagal"
    alasan = sesi.query(StagingUji).one().pemeriksaan["alasan"]
    assert alasan[0] == "Update akismet/akismet.php gagal: Perintah wp-cli di staging gagal."


def test_staging_diubah_minta_konfirmasi(sesi, site_staging, staging_aktif, prod, pb, web):
    site_staging.ditarik_pada = datetime(2026, 9, 1, tzinfo=timezone.utc)
    sesi.commit()
    log = staging_aktif / str(site_staging.site_id) / "log"
    log.mkdir(parents=True)
    (log / "diubah").write_text("1790000000", encoding="ascii")
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_staging, prod)
    assert e.value.error_class == STAGING_DITOLAK
    assert "Konfirmasi" in e.value.pesan
    sesi.query(StagingUji).delete()
    for j in sesi.query(Job).all():
        j.status = JobStatus.failed
    sesi.commit()
    assert _jalankan(sesi, site_staging, prod, konfirmasi=True)["hasil"] == "lolos"


def test_payload_paket_rusak_ditolak(sesi, site_staging, prod, pb, web):
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_uji_update, {"paket": [{"tipe": "plugin", "slug": "a/a.php", "ke": "1;id"}]})
    with pytest.raises(SiteError) as e:
        uji.tangani_staging_uji_update(sesi, job, prod.klien(site))
    assert e.value.error_class == STAGING_DITOLAK
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_staging_uji_nilai.py tests/integration/test_staging_uji.py -q`. Expected: `ImportError: cannot import name 'uji' from 'wpmgr.staging'`.

- [ ] **Step 4: Implementasikan.**

File: `src/wpmgr/staging/uji.py`
```python
"""Job staging_uji_update: tarik -> sebelum -> update -> sesudah -> nilai (spec §8.1).

Penilaiannya heuristik (spec §17): halaman hidup, tidak ada fatal baru, dan
tidak ada halaman yang menyusut drastis. Hasilnya tidak mengunci tombol
update produksi; keputusan tetap di pengguna.
"""

import os
import re
import time
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select

from wpmgr.config import get_settings
from wpmgr.crypto import dekripsi_secret
from wpmgr.models import Staging, StagingUji, StatusStaging, TrafficRincian
from wpmgr.staging import tarik as tarik_mod
from wpmgr.staging import umum
from wpmgr.staging.aman import bersih_teks
from wpmgr.staging.pembantu import GalatPembantu, cookie_akses
from wpmgr.staging.rencana import format_byte

JALUR_TETAP = ("/", "/wp-login.php")
JUMLAH_JALUR_TRAFFIC = 3
MAKS_PAKET = 20
BATAS_HTML = 5 * 1024 * 1024
BATAS_LOG = 1024 * 1024
MAKS_FATAL = 10
TENGGAT_SIAP = 60
POLA_JUDUL = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
POLA_FATAL = re.compile(r"PHP (?:Fatal error|Parse error)")
POLA_JALUR = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,199}")
POLA_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}")
POLA_VERSI = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,29}")


def slug_wpcli(tipe: str, slug: str) -> str | None:
    """Nama paket untuk wp-cli. Slug plugin Lapis 1 adalah 'dir/berkas.php'."""
    if tipe == "core":
        return "core"
    if tipe == "plugin":
        nama = slug.split("/", 1)[0] if "/" in slug else (slug.removesuffix(".php"))
    else:
        nama = slug
    return nama if POLA_SLUG.fullmatch(nama) else None


def urai_paket(daftar) -> list[dict]:
    if not isinstance(daftar, list) or not 1 <= len(daftar) <= MAKS_PAKET:
        raise ValueError("Daftar paket uji kosong atau terlalu panjang.")
    hasil = []
    for p in daftar:
        p = p if isinstance(p, dict) else {}
        tipe, slug, ke = p.get("tipe"), p.get("slug"), p.get("ke")
        if tipe not in ("plugin", "theme", "core") or not isinstance(slug, str) \
                or not isinstance(ke, str) or not POLA_VERSI.fullmatch(ke):
            raise ValueError("Paket uji tidak sah.")
        wp = slug_wpcli(tipe, slug)
        if wp is None:
            raise ValueError("Slug paket uji tidak sah.")
        dari = p.get("dari")
        hasil.append({"tipe": tipe, "slug": bersih_teks(slug, 200), "wpcli": wp,
                      "dari": bersih_teks(dari, 30) if isinstance(dari, str) else None, "ke": ke})
    return hasil


def jalur_uji(sesi, site_id, hari_ini) -> list[str]:
    jumlah = func.sum(TrafficRincian.kunjungan)
    baris = sesi.execute(
        select(TrafficRincian.kunci, jumlah)
        .where(TrafficRincian.site_id == site_id, TrafficRincian.sumber == "plugin",
               TrafficRincian.dimensi == "halaman", TrafficRincian.tanggal >= hari_ini - timedelta(days=30))
        .group_by(TrafficRincian.kunci)
        .order_by(jumlah.desc(), TrafficRincian.kunci)
        .limit(20)
    ).all()
    hasil = list(JALUR_TETAP)
    for kunci, _ in baris:
        if len(hasil) >= len(JALUR_TETAP) + JUMLAH_JALUR_TRAFFIC:
            break
        if isinstance(kunci, str) and POLA_JALUR.fullmatch(kunci) and not kunci.startswith("//") and kunci not in hasil:
            hasil.append(kunci)
    return hasil


def probe(http: httpx.Client, dasar: str, host: str, cookie: str, jalur: str) -> dict:
    try:
        with http.stream("GET", dasar + jalur, headers={"Host": host, "Cookie": cookie, "Accept": "text/html"}) as r:
            isi = bytearray()
            for potong in r.iter_bytes():
                isi.extend(potong)
                if len(isi) >= BATAS_HTML:
                    break
            status = r.status_code
    except httpx.HTTPError:
        return {"status": 0, "judul": None, "ukuran": 0}
    teks = bytes(isi[:512 * 1024]).decode("utf-8", "replace")
    cocok = POLA_JUDUL.search(teks)
    judul = bersih_teks(re.sub(r"\s+", " ", cocok.group(1)).strip(), 200) if cocok else None
    return {"status": status, "judul": judul, "ukuran": len(isi)}


def tunggu_siap(http, dasar, host, cookie, tidur=time.sleep) -> None:
    """Container baru dibuat/diperbarui butuh beberapa detik sebelum Apache menjawab."""
    batas = time.monotonic() + TENGGAT_SIAP
    while True:
        if probe(http, dasar, host, cookie, "/")["status"] not in (0, 502, 503, 504) or time.monotonic() >= batas:
            return
        tidur(2)


def ukuran_log(berkas: Path) -> int:
    try:
        return os.path.getsize(berkas)
    except OSError:
        return 0


def baca_log_baru(berkas: Path, posisi: int) -> list[str]:
    try:
        with open(berkas, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() < posisi:
                posisi = 0  # log dipangkas/diputar sejak "sebelum"
            f.seek(posisi)
            data = f.read(BATAS_LOG)
    except OSError:
        return []
    hasil = []
    for baris in data.decode("utf-8", "replace").splitlines():
        if POLA_FATAL.search(baris):
            # Path di dalam container, bukan path VPS; dipendekkan supaya terbaca.
            hasil.append(bersih_teks(baris.replace("/var/www/html/", ""), 300))
            if len(hasil) >= MAKS_FATAL:
                break
    return hasil


def nilai_uji(sebelum: dict, sesudah: dict, fatal_baru: list, update_gagal: list) -> tuple[str, list[str]]:
    alasan = [f"Update {u['slug']} gagal: {u['pesan']}" for u in update_gagal]
    for jalur, h in sesudah.items():
        status = h.get("status") or 0
        if not 200 <= status < 400:
            alasan.append(f"{jalur} membalas HTTP {status}" if status else f"{jalur} tidak dapat dihubungi")
            continue
        s = sebelum.get(jalur) or {}
        if s.get("ukuran", 0) > 0 and h.get("ukuran", 0) < s["ukuran"] * 0.5:
            alasan.append(f"{jalur} menyusut dari {format_byte(s['ukuran'])} ke {format_byte(h.get('ukuran', 0))}")
    if fatal_baru:
        alasan.append(f"{len(fatal_baru)} error fatal baru di log PHP staging")
    return ("lolos" if not alasan else "gagal"), alasan


def uji(sesi, job, site, staging: Staging, klien, pb) -> dict:
    try:
        paket = urai_paket((job.payload or {}).get("paket"))
    except ValueError as exc:
        raise umum.galat_ditolak(str(exc)) from None
    umum.perbarui_diubah(staging)
    sesi.commit()
    k = umum.kemajuan(job)
    if "tahap_uji" not in k:
        if staging.diubah_pada and staging.ditarik_pada and staging.diubah_pada > staging.ditarik_pada \
                and not (job.payload or {}).get("konfirmasi"):
            raise umum.galat_ditolak("Staging diubah sejak tarik terakhir; perubahan itu akan tertimpa oleh uji ini. "
                                     "Konfirmasi dulu untuk melanjutkan.")
        k = umum.simpan_kemajuan(sesi, job, tahap_uji="tarik")

    akar = umum.dir_site(site.id)
    log = akar / "log" / "php-error.log"
    if k["tahap_uji"] == "tarik":
        tarik_mod.tarik(sesi, job, site, staging, klien, pb, akhir_status=False)
        k = umum.simpan_kemajuan(sesi, job, tahap_uji="sebelum")

    host = umum.host_staging(staging)
    dasar = get_settings().staging_router_url
    cookie = "; ".join(f"{a}={b}" for a, b in cookie_akses(
        dekripsi_secret(staging.rahasia_router_terenkripsi), host, int(time.time())).items())
    http = umum.buat_http()
    try:
        if k["tahap_uji"] == "sebelum":
            tunggu_siap(http, dasar, host, cookie)
            jalur = jalur_uji(sesi, site.id, umum.sekarang().date())
            k = umum.simpan_kemajuan(sesi, job, tahap_uji="update", jalur=jalur, update=[],
                                     sebelum={j: probe(http, dasar, host, cookie, j) for j in jalur},
                                     log_posisi=ukuran_log(log))
        if k["tahap_uji"] == "update":
            hasil_update = list(k.get("update") or [])
            for p in paket[len(hasil_update):]:
                umum.titik_potongan(sesi, job, staging)
                argumen = (("core", "update", f"--version={p['ke']}") if p["tipe"] == "core"
                           else (p["tipe"], "update", p["wpcli"], f"--version={p['ke']}"))
                try:
                    pb.wpcli(staging.nama, *argumen)
                    hasil_update.append({"slug": p["slug"], "ok": True, "pesan": None})
                except GalatPembantu as exc:
                    hasil_update.append({"slug": p["slug"], "ok": False, "pesan": exc.pesan})
                k = umum.simpan_kemajuan(sesi, job, update=hasil_update)
            try:
                pb.wpcli(staging.nama, "cache", "flush")
            except GalatPembantu:
                pass
            k = umum.simpan_kemajuan(sesi, job, tahap_uji="sesudah")
        if k["tahap_uji"] == "sesudah":
            tunggu_siap(http, dasar, host, cookie)
            k = umum.simpan_kemajuan(sesi, job, tahap_uji="nilai",
                                     sesudah={j: probe(http, dasar, host, cookie, j) for j in k["jalur"]},
                                     fatal_baru=baca_log_baru(log, k["log_posisi"]))
    finally:
        http.close()

    hasil, alasan = nilai_uji(k["sebelum"], k["sesudah"], k["fatal_baru"], [u for u in k["update"] if not u["ok"]])
    paket_simpan = [{"tipe": p["tipe"], "slug": p["slug"], "dari": p["dari"], "ke": p["ke"]} for p in paket]
    sesi.add(StagingUji(site_id=site.id, job_id=job.id, paket=paket_simpan, hasil=hasil, pemeriksaan={
        "halaman": [{"jalur": j, "sebelum": k["sebelum"].get(j), "sesudah": k["sesudah"].get(j)} for j in k["jalur"]],
        "fatal_baru": k["fatal_baru"], "update": k["update"], "alasan": alasan,
    }))
    st = sesi.get(Staging, staging.id, populate_existing=True)
    st.status = StatusStaging.siap
    # Update yang dijalankan uji ini bukan perubahan pengguna.
    (akar / "log" / "diubah").unlink(missing_ok=True)
    st.diubah_pada = None
    umum.catat_aktivitas(sesi, site.id, job, f"Uji update di staging: {hasil}",
                         {"paket": paket_simpan, "alasan": alasan[:10]})
    sesi.commit()
    return {"hasil": hasil, "alasan": alasan}


def tangani_staging_uji_update(sesi, job, klien) -> dict:
    def inti(sesi, job, site, staging):
        return uji(sesi, job, site, staging, klien, umum.buat_pembantu())

    return umum.jalankan_staging(sesi, job, inti, StatusStaging.berjalan_uji, "Uji update di staging")
```

Di `src/wpmgr/jobs/handlers.py`, tambahkan `from wpmgr.staging.uji import tangani_staging_uji_update` dan entri `JobType.staging_uji_update: tangani_staging_uji_update,`.

- [ ] **Step 5: Jalankan test.** Expected: unit `test_staging_uji_nilai.py` dan integrasi `test_staging_uji.py` lulus seluruhnya; `ruff check .` bersih.

- [ ] **Step 6: Commit.**

```bash
git add src/wpmgr/staging/uji.py src/wpmgr/jobs/handlers.py tests/unit/test_staging_uji_nilai.py tests/integration/test_staging_uji.py
git commit -m "feat(staging): uji update di staging dengan probe halaman dan log fatal"
```

---

### Task 16: Job `staging_dorong`

**Files:**
- Create: `src/wpmgr/staging/dorong.py`, `tests/integration/test_staging_dorong.py`
- Modify: `src/wpmgr/jobs/handlers.py`

**Interfaces:**
- Consumes:
  - Task 14 (`ambil_manifest`, `Salin`, `ekspor_db`, `urai_tabel`);
  - Task 12 (`rencana_dorong`, `bandingkan_tanda_air`, `pindai_lokal`, `paket.susun`, `SiteClient.staging_*`);
  - Task 13 (`umum`).
- Produces (`wpmgr.staging.dorong`):
  - konstanta: `MODE = ("hanya_kode", "timpa_penuh")`, `LABEL_MODE`, `UKURAN_UNGGAH = 4 MiB`;
  - sumber dan rencana unggah: `@dataclass SumberDorong(ganti: list[Entri], akar_berkas: Path, hapus: list[str], sql: list[Path], charset: str)`, `rencana_connector(sumber) -> bytes`, `@dataclass(frozen=True) Unggahan(jenis, berkas, dari, panjang)`, `rencana_unggah(sumber, rencana_json, ukuran) -> list[Unggahan]`, `baca_gabungan(berkas, dari, panjang) -> bytes`, `isi_unggahan(sumber, u, rencana_json) -> tuple[list[dict], list[bytes]]`;
  - langkah ke produksi: `unggah_semua(sesi, job, staging, klien, sumber, dorong_id, ukuran, k) -> dict`, `terapkan(sesi, job, staging, klien, dorong_id, jumlah, sha, ada_sql, token) -> None` (memanggil `pulihkan` sendiri bila `tukar` gagal), `bersihkan(klien, dorong_id)`, `cek_halaman(url) -> int`;
  - snapshot: `pangkas_snapshot(sesi, site_id, n) -> int`, `ukuran_dir(path) -> int`;
  - job: `tangani_staging_dorong(sesi, job, klien) -> dict` dan `HANDLER[JobType.staging_dorong]`.
  - Payload: `{"mode": "hanya_kode"|"timpa_penuh", "konfirmasi_nama": str|None}`.
  - Kemajuan:
    - `tahap_dorong` ∈ `tanda_air|manifest|rencana|snapshot_berkas|snapshot_db|snapshot_catat|unggah|cek_ulang|terapkan|cek`;
    - `dorong_id` (32 hex), `token` (32 hex), `tanda_air_dorong`, `perubahan`;
    - `snapshot_tabel`, `snapshot_id`, `unggah_nomor`, `jumlah_potongan`, ditambah kunci tarik (`info`, `manifest_kursor`, `tabel*`, `byte_*`).
  - Disk:
    - `<site_id>/dorong/` berisi `manifest.jsonl`, `rencana.json`, `dorong.sql`, dan dihapus setelah selesai;
    - `<site_id>/snapshot/j<job_id>/` berisi `berkas/`, `indeks.jsonl`, `prelude.sql`, `db/`, `meta.json`;
    - `meta.json` berisi `{mode, baru, diganti, dihapus, charset, prefix, home, batas_unggah, tanda_air}`.
  - Baris `StagingSnapshot(jenis="sebelum_dorong", status="tersedia", path="<site_id>/snapshot/j<job_id>", detail={mode, jumlah_berkas, baru, perubahan})`.

Urutan (spec §6.3):
1. tanda air, hanya untuk timpa penuh; ditolak tanpa konfirmasi nama bila ada data baru;
2. manifest produksi;
3. rencana dari pindaian `files/` staging, dan untuk timpa penuh `wp search-replace --export` URL staging → URL produksi;
4. snapshot berkas yang akan tertimpa/terhapus, lalu ekspor DB produksi;
5. unggah potongan;
6. tanda air ulang, hanya untuk timpa penuh; batal dan bersihkan bila berubah;
7. terapkan `siapkan`/`impor`/`tukar`; gagal berarti `pulihkan`;
8. cek halaman utama. Gagal berarti `dorong_gagal_pada` diisi (chip merah), bukan job gagal.
Snapshot dipangkas ke `WPMGR_STAGING_SNAPSHOT` terakhir.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/integration/test_staging_dorong.py`
```python
import json
import os
import time

import pytest
from staging_palsu import PembantuPalsu, ProduksiPalsu

from wpmgr.errors import STAGING_DITOLAK, STAGING_GAGAL, SiteError
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    Site,
    Staging,
    StagingSnapshot,
    StatusStaging,
)
from wpmgr.staging import dorong, tarik, umum
from wpmgr.staging.rencana import Entri

pytestmark = pytest.mark.integration

MTIME = 1_700_000_000
SQL_EKSPOR = b"DROP TABLE IF EXISTS `wp_posts`;\nCREATE TABLE `wp_posts` (`id` int);\n"


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))


@pytest.fixture
def prod(monkeypatch):
    p = ProduksiPalsu()
    p.berkas = {
        "index.php": (b"<?php // inti", MTIME),
        "wp-content/themes/t/style.css": (b"body{}", MTIME),
        "wp-content/plugins/p/p.php": (b"<?php //p", MTIME),
        "wp-content/uploads/lama.jpg": (b"jpg", MTIME),
    }
    p.tabel = {"wp_posts": [b"CREATE TABLE `wp_posts` (`id` int);\n"]}
    monkeypatch.setattr(umum, "buat_http", p.http)
    return p


@pytest.fixture
def pb(staging_aktif, monkeypatch, site_staging):
    palsu = PembantuPalsu(staging_aktif)
    ekspor = staging_aktif / str(site_staging.site_id) / "ekspor" / "dorong.sql"

    def saat_wpcli(nama, argumen):
        if "--export" in argumen:
            ekspor.write_bytes(SQL_EKSPOR)

    palsu.saat_wpcli = saat_wpcli
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


def _selesaikan(sesi):
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.success
    sesi.commit()


def _tarik(sesi, site_staging, prod):
    site = sesi.get(Site, site_staging.site_id)
    tarik.tangani_staging_tarik(sesi, buat_job(sesi, site.id, JobType.staging_tarik), prod.klien(site))
    _selesaikan(sesi)


def _dorong(sesi, site_staging, prod, mode, konfirmasi=None, job=None):
    site = sesi.get(Site, site_staging.site_id)
    job = job or buat_job(sesi, site.id, JobType.staging_dorong, {"mode": mode, "konfirmasi_nama": konfirmasi})
    hasil = dorong.tangani_staging_dorong(sesi, job, prod.klien(site))
    _selesaikan(sesi)
    return hasil


def _files(staging_aktif, site_staging):
    return staging_aktif / str(site_staging.site_id) / "files"


def _ubah_staging(files):
    baru = int(time.time())
    (files / "wp-content/themes/t/style.css").write_bytes(b"body{color:red}")
    os.utime(files / "wp-content/themes/t/style.css", (baru, baru))
    (files / "wp-content/plugins/p/baru.php").write_bytes(b"<?php //baru")
    (files / "wp-content/plugins/p/p.php").unlink()
    (files / "wp-content/uploads/baru.jpg").write_bytes(b"jpg2")
    (files / "index.php").write_bytes(b"<?php // inti diubah di staging")


def test_dorong_hanya_kode(sesi, site_staging, staging_aktif, prod, pb):
    _tarik(sesi, site_staging, prod)
    _ubah_staging(_files(staging_aktif, site_staging))
    prod.berkas["wp-content/uploads/pesanan.pdf"] = (b"pdf", MTIME)
    tanda_air_sebelum = prod.hitung.get("/staging/tanda-air")

    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")

    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"
    assert prod.berkas["wp-content/plugins/p/baru.php"][0] == b"<?php //baru"
    assert "wp-content/plugins/p/p.php" not in prod.berkas
    assert prod.berkas["wp-content/uploads/baru.jpg"][0] == b"jpg2"
    assert prod.berkas["index.php"][0] == b"<?php // inti"
    assert prod.berkas["wp-content/uploads/pesanan.pdf"][0] == b"pdf"
    assert "wp-content/mu-plugins/wpmgr-staging.php" not in prod.berkas
    assert prod.langkah == ["siapkan", "tukar", "selesai"]
    assert prod.hitung.get("/staging/tanda-air") == tanda_air_sebelum
    assert any(route == "/staging/bersihkan" for route, _ in prod.diminta)

    snap = sesi.query(StagingSnapshot).one()
    assert snap.status == "tersedia" and snap.jenis == "sebelum_dorong"
    assert snap.detail["mode"] == "hanya_kode"
    d = staging_aktif / snap.path
    assert (d / "berkas/wp-content/themes/t/style.css").read_bytes() == b"body{}"
    assert (d / "berkas/wp-content/plugins/p/p.php").read_bytes() == b"<?php //p"
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert sorted(meta["baru"]) == ["wp-content/plugins/p/baru.php", "wp-content/uploads/baru.jpg"]
    assert any(p.name.endswith(".sql") for p in (d / "db").iterdir())
    assert hasil["dorong_gagal"] is False
    sesi.refresh(site_staging)
    assert site_staging.status == StatusStaging.siap
    assert not (staging_aktif / str(site_staging.site_id) / "dorong").exists()
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Dorong ke produksi (hanya kode)%")).count() == 1


def test_timpa_penuh_ditolak_karena_data_baru(sesi, site_staging, prod, pb):
    _tarik(sesi, site_staging, prod)
    prod.tanda_air["sumber"]["comments"] = {"maks_id": 5, "jumlah": 4}
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert e.value.error_class == STAGING_DITOLAK
    assert "2 komentar baru" in e.value.pesan
    assert "Ketik nama site" in e.value.pesan
    assert prod.unggahan == {}
    assert prod.langkah == []


def test_timpa_penuh_dengan_konfirmasi_nama(sesi, site_staging, staging_aktif, prod, pb):
    _tarik(sesi, site_staging, prod)
    _ubah_staging(_files(staging_aktif, site_staging))
    prod.tanda_air["sumber"]["comments"] = {"maks_id": 5, "jumlah": 4}
    prod.berkas["wp-content/uploads/pesanan.pdf"] = (b"pdf", MTIME)
    prod.berkas["google123.html"] = (b"verifikasi", MTIME)
    _dorong(sesi, site_staging, prod, "timpa_penuh", konfirmasi="Contoh")
    assert ("wpcli", "contoh-test", "search-replace", "https://contoh-test.staging.contoh.id",
            "https://contoh.test", "--export") in pb.panggilan
    assert prod.sql_diterapkan == SQL_EKSPOR
    assert prod.langkah == ["siapkan", "impor", "tukar", "selesai"]
    assert prod.berkas["index.php"][0] == b"<?php // inti diubah di staging"
    assert prod.berkas["wp-content/uploads/pesanan.pdf"][0] == b"pdf"
    assert prod.berkas["google123.html"][0] == b"verifikasi"
    log = sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Dorong ke produksi (timpa penuh)%")).one()
    assert log.detail["perubahan"] == ["2 komentar baru"]


def test_tanda_air_berubah_di_tengah_membatalkan(sesi, site_staging, staging_aktif, prod, pb):
    _tarik(sesi, site_staging, prod)
    _ubah_staging(_files(staging_aktif, site_staging))

    def user_baru(p, n, badan):
        p.tanda_air["sumber"]["users"] = {"maks_id": 2, "jumlah": 2}

    prod.sebelum["/staging/unggah"] = user_baru
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "timpa_penuh")
    assert e.value.error_class == STAGING_GAGAL
    assert "berubah selama dorong" in e.value.pesan and "1 user baru" in e.value.pesan
    assert "tukar" not in prod.langkah
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    assert any(route == "/staging/bersihkan" for route, _ in prod.diminta)


def test_halaman_utama_gagal_menyalakan_dorong_gagal(sesi, site_staging, staging_aktif, prod, pb):
    _tarik(sesi, site_staging, prod)
    _ubah_staging(_files(staging_aktif, site_staging))
    prod.halaman_utama = 500
    hasil = _dorong(sesi, site_staging, prod, "hanya_kode")
    assert hasil["dorong_gagal"] is True
    assert hasil["halaman_utama"] == 500
    sesi.refresh(site_staging)
    assert site_staging.dorong_gagal_pada is not None
    log = sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Dorong ke produksi%HTTP 500%")).one()
    assert log.level == "error"


def test_terapkan_gagal_dipulihkan(sesi, site_staging, staging_aktif, prod, pb):
    _tarik(sesi, site_staging, prod)
    _ubah_staging(_files(staging_aktif, site_staging))
    prod.gagal_langkah = "tukar"
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert e.value.error_class == STAGING_GAGAL
    assert "dipulihkan" in e.value.pesan
    assert "pulihkan" in prod.langkah
    sesi.refresh(site_staging)
    assert site_staging.dorong_gagal_pada is None


def test_snapshot_dipangkas_ke_n(sesi, site_staging, staging_aktif, prod, pb, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_SNAPSHOT", "2")
    get_settings.cache_clear()
    _tarik(sesi, site_staging, prod)
    files = _files(staging_aktif, site_staging)
    for i in range(3):
        (files / "wp-content/themes/t/style.css").write_bytes(f"body{{z-index:{i}}}".encode())
        _dorong(sesi, site_staging, prod, "hanya_kode")
    snap = sesi.query(StagingSnapshot).order_by(StagingSnapshot.id).all()
    assert [s.status for s in snap] == ["dipangkas", "tersedia", "tersedia"]
    assert not (staging_aktif / snap[0].path).exists()
    assert (staging_aktif / snap[2].path).exists()


def test_unggah_dilanjutkan_setelah_putus(sesi, site_staging, staging_aktif, prod, pb):
    _tarik(sesi, site_staging, prod)
    _ubah_staging(_files(staging_aktif, site_staging))
    prod.jadwal_gagal["/staging/unggah"] = {2, 3, 4}
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_dorong, {"mode": "hanya_kode"})
    with pytest.raises(SiteError):
        dorong.tangani_staging_dorong(sesi, job, prod.klien(site))
    dorong.tangani_staging_dorong(sesi, job, prod.klien(site))
    assert prod.hitung["/staging/unggah"] == len(prod.unggahan) + 3
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"


def test_batal_dorong_membersihkan_area_sementara(sesi, site_staging, staging_aktif, prod, pb):
    _tarik(sesi, site_staging, prod)
    _ubah_staging(_files(staging_aktif, site_staging))

    def minta_batal(p, n, badan):
        st = sesi.get(Staging, site_staging.id)
        st.batal_diminta_pada = umum.sekarang()
        sesi.commit()

    prod.sebelum["/staging/unggah"] = minta_batal
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert "Dibatalkan" in e.value.pesan
    assert any(route == "/staging/bersihkan" for route, _ in prod.diminta)
    assert not (staging_aktif / str(site_staging.site_id) / "dorong").exists()
    assert prod.langkah == []


def test_mode_tidak_sah_dan_staging_dijeda_ditolak(sesi, site_staging, prod, pb):
    _tarik(sesi, site_staging, prod)
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "semuanya")
    assert e.value.error_class == STAGING_DITOLAK
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.failed
    st = sesi.get(Staging, site_staging.id)
    st.aktif = False
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _dorong(sesi, site_staging, prod, "hanya_kode")
    assert "dijeda" in e.value.pesan


def test_rencana_unggah_dan_baca_gabungan(tmp_path):
    a, b = tmp_path / "a.sql", tmp_path / "b.sql"
    a.write_bytes(b"12345")
    b.write_bytes(b"6789")
    assert dorong.baca_gabungan([a, b], 3, 4) == b"4567"
    assert dorong.baca_gabungan([a, b], 0, 100) == b"123456789"
    sumber = dorong.SumberDorong(ganti=[Entri("x.php", 10, 1, "0" * 64)], akar_berkas=tmp_path, hapus=[],
                                 sql=[a, b], charset="utf8mb4")
    daftar = dorong.rencana_unggah(sumber, b"R" * 7, 4)
    assert [(u.jenis, u.dari, u.panjang) for u in daftar] == [
        ("rentang", 0, 4), ("rentang", 4, 4), ("rentang", 8, 2),
        ("sql", 0, 4), ("sql", 4, 4), ("sql", 8, 1),
        ("rencana", 0, 4), ("rencana", 4, 3),
    ]
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_staging_dorong.py -q`. Expected: `ImportError: cannot import name 'dorong' from 'wpmgr.staging'`.

- [ ] **Step 3: Implementasikan.**

File: `src/wpmgr/staging/dorong.py`
```python
"""Job staging_dorong (spec §6.3) dan jalur bersama untuk staging_kembalikan (§8.3)."""

import hashlib
import json
import os
import secrets
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import select

from wpmgr.config import get_settings
from wpmgr.errors import BAD_RESPONSE, SiteError
from wpmgr.models import Staging, StagingSnapshot, StatusStaging
from wpmgr.staging import umum
from wpmgr.staging.aman import PathTidakAman, bersih_teks, jalur_di_dalam
from wpmgr.staging.indeks import Indeks, pindai_lokal
from wpmgr.staging.paket import susun
from wpmgr.staging.rencana import (
    Entri,
    bagi_potongan,
    bandingkan_tanda_air,
    entri_dari,
    format_byte,
    rencana_dorong,
    selisih,
    urai_tanda_air,
)
from wpmgr.staging.tarik import Salin, ambil_manifest, ekspor_db, urai_tabel

MODE = ("hanya_kode", "timpa_penuh")
LABEL_MODE = {"hanya_kode": "hanya kode", "timpa_penuh": "timpa penuh"}
UKURAN_UNGGAH = 4 * 1024 * 1024
MAKS_LANGKAH = 2000
BATCH_SNAPSHOT = 5000


@dataclass
class SumberDorong:
    ganti: list[Entri]
    akar_berkas: Path
    hapus: list[str]
    sql: list[Path]
    charset: str


@dataclass(frozen=True)
class Unggahan:
    jenis: str
    berkas: tuple = ()
    dari: int = 0
    panjang: int = 0


def rencana_connector(sumber: SumberDorong) -> bytes:
    """Rencana yang diverifikasi connector sebelum menulis apa pun (Task 8)."""
    return json.dumps({
        "versi": 1,
        "berkas": [{"path": e.path, "ukuran": e.ukuran, "sha256": e.hash, "mtime": e.mtime} for e in sumber.ganti],
        "hapus": list(sumber.hapus),
        "sql": bool(sumber.sql),
        "charset": sumber.charset,
    }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def rencana_unggah(sumber: SumberDorong, rencana_json: bytes, ukuran: int) -> list[Unggahan]:
    """Daftar potongan yang deterministik: melanjutkan cukup dengan nomor potongan."""
    daftar = [Unggahan("berkas" if p.jenis == "paket" else "rentang", p.berkas, p.dari, p.panjang)
              for p in bagi_potongan(sumber.ganti, ukuran_paket=ukuran)]
    total_sql = sum(p.stat().st_size for p in sumber.sql)
    daftar += [Unggahan("sql", (), d, min(ukuran, total_sql - d)) for d in range(0, total_sql, ukuran)]
    daftar += [Unggahan("rencana", (), d, min(ukuran, len(rencana_json) - d)) for d in range(0, len(rencana_json), ukuran)]
    return daftar


def baca_gabungan(berkas: list[Path], dari: int, panjang: int) -> bytes:
    hasil = bytearray()
    posisi = 0
    for b in berkas:
        n = b.stat().st_size
        if dari < posisi + n and len(hasil) < panjang:
            with open(b, "rb") as f:
                f.seek(max(0, dari - posisi))
                hasil.extend(f.read(panjang - len(hasil)))
        posisi += n
        if len(hasil) >= panjang:
            break
    return bytes(hasil)


def isi_unggahan(sumber: SumberDorong, u: Unggahan, rencana_json: bytes) -> tuple[list[dict], list[bytes]]:
    if u.jenis == "berkas":
        return ([{"path": e.path, "mtime": e.mtime} for e in u.berkas],
                [jalur_di_dalam(sumber.akar_berkas, e.path).read_bytes() for e in u.berkas])
    if u.jenis == "rentang":
        e = u.berkas[0]
        with open(jalur_di_dalam(sumber.akar_berkas, e.path), "rb") as f:
            f.seek(u.dari)
            return [{"path": e.path, "dari": u.dari}], [f.read(u.panjang)]
    if u.jenis == "sql":
        return [{"path": "sql"}], [baca_gabungan(sumber.sql, u.dari, u.panjang)]
    return [{"path": "rencana"}], [rencana_json[u.dari:u.dari + u.panjang]]


def unggah_semua(sesi, job, staging, klien, sumber: SumberDorong, dorong_id: str, ukuran: int, k: dict) -> dict:
    rencana_json = rencana_connector(sumber)
    daftar = rencana_unggah(sumber, rencana_json, ukuran)
    for nomor in range(k.get("unggah_nomor", 0), len(daftar)):
        umum.titik_potongan(sesi, job, staging)
        meta, isi = isi_unggahan(sumber, daftar[nomor], rencana_json)
        data = susun({"dorong_id": dorong_id, "nomor": nomor, "jenis": daftar[nomor].jenis, "berkas": meta}, isi)

        def kirim(data=data, nomor=nomor):
            h = klien.staging_unggah(data)
            if h.get("ok") is not True or h.get("nomor") != nomor:
                raise SiteError(BAD_RESPONSE, "Balasan unggah tidak sesuai potongan yang dikirim.")
            return h

        umum.ulangi(kirim)
        k = umum.simpan_kemajuan(sesi, job, unggah_nomor=nomor + 1, jumlah_potongan=len(daftar),
                                 byte_selesai=k.get("byte_selesai", 0) + sum(len(x) for x in isi))
    return k


def _langkah(sesi, job, klien, badan: dict, token: str | None = None) -> dict:
    for _ in range(MAKS_LANGKAH):
        umum.detak(sesi, job)
        h = umum.ulangi(klien.staging_terapkan, badan, token)
        if h.get("selesai") is True:
            return h
    raise umum.galat_gagal(f"Langkah {badan['langkah']} di produksi tidak selesai.")


def bersihkan(klien, dorong_id: str) -> None:
    """Area sementara connector dibuang; gagal di sini dibereskan cron connector (24 jam)."""
    for _ in range(100):
        try:
            if klien.staging_bersihkan(dorong_id).get("lagi") is not True:
                return
        except SiteError:
            return


def cek_halaman(url: str) -> int:
    http = umum.buat_http()
    try:
        return http.get(url.rstrip("/") + "/").status_code
    except httpx.HTTPError:
        return 0
    finally:
        http.close()


def terapkan(sesi, job, staging, klien, site_url: str, dorong_id: str, jumlah: int, sha: str,
             ada_sql: bool, token: str) -> None:
    dasar = {"dorong_id": dorong_id}
    _langkah(sesi, job, klien, {**dasar, "langkah": "siapkan", "jumlah_potongan": jumlah, "sha256_rencana": sha})
    if ada_sql:
        _langkah(sesi, job, klien, {**dasar, "langkah": "impor"})
    try:
        _langkah(sesi, job, klien, {**dasar, "langkah": "tukar", "token": token}, token)
    except SiteError as exc:
        pesan = bersih_teks(exc.pesan, 300)
        try:
            _langkah(sesi, job, klien, {**dasar, "langkah": "pulihkan", "token": token}, token)
            pulih = True
        except SiteError:
            pulih = False
        status = cek_halaman(site_url)
        # staging bisa None: kembalikan tetap boleh setelah staging dihapus (Task 17).
        if staging is not None and (not pulih or not 200 <= status < 400):
            sesi.get(Staging, staging.id, populate_existing=True).dorong_gagal_pada = umum.sekarang()
        sesi.commit()
        bersihkan(klien, dorong_id)
        if pulih:
            raise umum.galat_gagal(f"Terapkan di produksi gagal: {pesan}. Produksi sudah dipulihkan dari "
                                   "salinan lokal connector.") from None
        raise umum.galat_gagal(f"Terapkan di produksi gagal dan pemulihan tidak terkonfirmasi: {pesan}. "
                               "Periksa site lalu gunakan Kembalikan.") from None


def ukuran_dir(path: Path) -> int:
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for n in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, n))
            except OSError:
                pass
    return total


def pangkas_snapshot(sesi, site_id, n: int) -> int:
    daftar = sesi.scalars(
        select(StagingSnapshot)
        .where(StagingSnapshot.site_id == site_id, StagingSnapshot.status.in_(("tersedia", "dipakai")))
        .order_by(StagingSnapshot.dibuat_pada.desc(), StagingSnapshot.id.desc())
    ).all()
    dipangkas = 0
    for s in daftar[n:]:
        try:
            shutil.rmtree(jalur_di_dalam(get_settings().jalur_staging, s.path), ignore_errors=True)
        except PathTidakAman:
            pass
        s.status = "dipangkas"
        dipangkas += 1
    return dipangkas


def _tanda_air_sekarang(klien, lama: dict | None) -> dict:
    posts = ((lama or {}).get("sumber") or {}).get("posts") or {}
    ta = urai_tanda_air(umum.ulangi(klien.staging_tanda_air, posts.get("diubah") or None, posts.get("maks_id") or None))
    if ta is None:
        raise umum.galat_gagal("Tanda air produksi tidak dapat dibaca.")
    return ta


def _simpan_rencana(berkas: Path, ganti: list[Entri], hapus: list[str], sql: list[Path]) -> None:
    berkas.write_text(json.dumps({"ganti": [[e.path, e.ukuran, e.mtime, e.hash] for e in ganti],
                                  "hapus": hapus, "sql": [p.name for p in sql]}, ensure_ascii=False), encoding="utf-8")


def _muat_rencana(kerja: Path) -> tuple[list[Entri], list[str], list[Path]]:
    data = json.loads((kerja / "rencana.json").read_text(encoding="utf-8"))
    return ([Entri(*x) for x in data["ganti"]], data["hapus"], [kerja / n for n in data["sql"]])


def _snapshot_berkas(sesi, job, staging, klien, snap: Path, ganti, hapus, mode, info, k) -> dict:
    paths = [e.path for e in ganti] + list(hapus)
    set_ganti = {e.path for e in ganti}
    ada: dict[str, Entri] = {}
    baru: list[str] = []
    tabel = None
    for i in range(0, max(1, len(paths)), BATCH_SNAPSHOT):
        batch = paths[i:i + BATCH_SNAPSHOT]

        def minta(batch=batch, awal=(i == 0)):
            resp = klien.staging_snapshot(batch, awal)
            daftar = resp.get("berkas")
            if not isinstance(daftar, list) or len(daftar) != len(batch) or any(
                    not isinstance(m, dict) or m.get("path") != p for m, p in zip(daftar, batch)):
                raise SiteError(BAD_RESPONSE, "Balasan snapshot tidak sesuai permintaan.")
            return resp

        resp = umum.ulangi(minta)
        for m, p in zip(resp["berkas"], batch):
            if m.get("ada") is True:
                e = entri_dari({"path": p, "ukuran": m.get("ukuran"), "mtime": m.get("mtime"), "hash": None})
                if e is not None:
                    ada[p] = e
            elif p in set_ganti:
                baru.append(p)
        if i == 0:
            tabel = urai_tabel(resp.get("tabel"), info["prefix"])
            if not tabel:
                raise umum.galat_gagal("Daftar tabel produksi untuk snapshot kosong.")
    berkas_dir = snap / "berkas"
    berkas_dir.mkdir(parents=True, exist_ok=True)
    indeks = Indeks(snap / "indeks.jsonl")
    lokal = indeks.muat()
    salin = Salin(sesi, job, staging, klien, berkas_dir, indeks, lokal, k)
    for pot in bagi_potongan(selisih(ada, lokal).diambil):
        umum.titik_potongan(sesi, job, staging)
        if pot.jenis == "rentang":
            if pot.dari == 0:
                salin.besar(pot.berkas[0])
        else:
            salin.paket(pot.berkas)
    (snap / "meta.json").write_text(json.dumps({
        "mode": mode, "baru": sorted(baru), "diganti": sorted(p for p in ada if p in set_ganti),
        "dihapus": sorted(p for p in ada if p not in set_ganti), "charset": info["charset"],
        "prefix": info["prefix"], "home": info["home"], "batas_unggah": info["batas_unggah"],
        "tanda_air": k.get("tanda_air_dorong"),
    }, ensure_ascii=False), encoding="utf-8")
    return umum.simpan_kemajuan(sesi, job, tahap_dorong="snapshot_db", snapshot_tabel=tabel,
                                peringatan=salin.k.get("peringatan", []))


def dorong(sesi, job, site, staging: Staging, klien, pb) -> dict:
    p = job.payload or {}
    mode = p.get("mode")
    if mode not in MODE:
        raise umum.galat_ditolak("Mode dorong tidak dikenal.")
    if staging.ditarik_pada is None or not staging.tanda_air:
        raise umum.galat_ditolak("Tarik staging dulu sebelum mendorong.")
    if not staging.aktif:
        raise umum.galat_ditolak("Staging sedang dijeda; jalankan staging dulu.")
    akar = umum.dir_site(site.id)
    kerja = akar / "dorong"
    k = umum.kemajuan(job)
    if "tahap_dorong" not in k:
        shutil.rmtree(kerja, ignore_errors=True)
        k = umum.simpan_kemajuan(sesi, job, tahap_dorong="tanda_air", dorong_id=uuid.uuid4().hex,
                                 token=secrets.token_hex(16), mulai=umum.sekarang().isoformat(),
                                 byte_selesai=0, byte_total=0, peringatan=[])
    kerja.mkdir(parents=True, exist_ok=True)

    if k["tahap_dorong"] == "tanda_air":
        perubahan: list[str] = []
        if mode == "timpa_penuh":
            ta = _tanda_air_sekarang(klien, staging.tanda_air)
            perubahan = bandingkan_tanda_air(staging.tanda_air, ta)
            if perubahan and p.get("konfirmasi_nama") != site.nama:
                raise umum.galat_ditolak("Produksi punya data baru sejak staging ditarik: " + ", ".join(perubahan)
                                         + ". Timpa penuh akan menghapusnya. Ketik nama site untuk tetap menimpa.")
            k = umum.simpan_kemajuan(sesi, job, tanda_air_dorong=ta)
        k = umum.simpan_kemajuan(sesi, job, tahap_dorong="manifest", perubahan=perubahan)

    if k["tahap_dorong"] == "manifest":
        k = ambil_manifest(sesi, job, staging, klien, kerja, k)
        k = umum.simpan_kemajuan(sesi, job, tahap_dorong="rencana")
    info = k["info"]

    if k["tahap_dorong"] == "rencana":
        staging_entri = pindai_lokal(akar / "files", Indeks(akar / "indeks.jsonl").muat())
        r = rencana_dorong(mode, staging_entri, Indeks(kerja / "manifest.jsonl").muat())
        sql: list[Path] = []
        if r.db:
            ekspor = akar / "ekspor" / "dorong.sql"
            ekspor.unlink(missing_ok=True)
            pb.wpcli(staging.nama, "search-replace", umum.url_staging(staging), info["home"], "--export")
            if not ekspor.is_file() or ekspor.stat().st_size == 0:
                raise umum.galat_gagal("Ekspor database staging kosong.")
            shutil.move(str(ekspor), str(kerja / "dorong.sql"))
            sql = [kerja / "dorong.sql"]
        _simpan_rencana(kerja / "rencana.json", r.ganti, r.hapus, sql)
        k = umum.simpan_kemajuan(sesi, job, tahap_dorong="snapshot_berkas", byte_total=r.byte,
                                 jumlah_ganti=len(r.ganti), jumlah_hapus=len(r.hapus), db=r.db)
    ganti, hapus, sql = _muat_rencana(kerja)
    if not ganti and not hapus and not sql:
        st = sesi.get(Staging, staging.id, populate_existing=True)
        st.status = StatusStaging.siap
        umum.catat_aktivitas(sesi, site.id, job, f"Dorong ke produksi ({LABEL_MODE[mode]}): tidak ada perubahan")
        sesi.commit()
        shutil.rmtree(kerja, ignore_errors=True)
        return {"mode": mode, "berkas": 0, "hapus": 0, "db": False, "dorong_gagal": False, "halaman_utama": None}

    snap = akar / "snapshot" / f"j{job.id}"
    if k["tahap_dorong"] == "snapshot_berkas":
        k = _snapshot_berkas(sesi, job, staging, klien, snap, ganti, hapus, mode, info, k)
    if k["tahap_dorong"] == "snapshot_db":
        k = ekspor_db(sesi, job, staging, klien, snap, {"charset": info["charset"], "tabel": k["snapshot_tabel"]},
                      k, "snapshot_catat")
        k = umum.simpan_kemajuan(sesi, job, tahap_dorong="snapshot_catat")
    if k["tahap_dorong"] == "snapshot_catat":
        meta = json.loads((snap / "meta.json").read_text(encoding="utf-8"))
        row = StagingSnapshot(site_id=site.id, job_id=job.id, jenis="sebelum_dorong", status="tersedia",
                              ukuran=ukuran_dir(snap), path=f"{site.id}/snapshot/j{job.id}",
                              detail={"mode": mode, "jumlah_berkas": len(meta["diganti"]) + len(meta["dihapus"]),
                                      "baru": len(meta["baru"]), "perubahan": k.get("perubahan", [])})
        sesi.add(row)
        sesi.commit()
        k = umum.simpan_kemajuan(sesi, job, tahap_dorong="unggah", snapshot_id=row.id, unggah_nomor=0)

    sumber = SumberDorong(ganti, akar / "files", hapus, sql, info["charset"])
    if k["tahap_dorong"] == "unggah":
        k = unggah_semua(sesi, job, staging, klien, sumber, k["dorong_id"], min(UKURAN_UNGGAH, info["batas_unggah"]), k)
        k = umum.simpan_kemajuan(sesi, job, tahap_dorong="cek_ulang")

    if k["tahap_dorong"] == "cek_ulang":
        if mode == "timpa_penuh":
            beda = bandingkan_tanda_air(k["tanda_air_dorong"], _tanda_air_sekarang(klien, None))
            if beda:
                bersihkan(klien, k["dorong_id"])
                raise umum.galat_gagal("Data produksi berubah selama dorong (" + ", ".join(beda)
                                       + "); dorong dibatalkan dan produksi tidak diubah.")
        k = umum.simpan_kemajuan(sesi, job, tahap_dorong="terapkan")

    if k["tahap_dorong"] == "terapkan":
        terapkan(sesi, job, staging, klien, site.url, k["dorong_id"], k["jumlah_potongan"],
                 hashlib.sha256(rencana_connector(sumber)).hexdigest(), bool(sql), k["token"])
        k = umum.simpan_kemajuan(sesi, job, tahap_dorong="cek")

    status = cek_halaman(site.url)
    try:
        umum.ulangi(klien.staging_terapkan, {"dorong_id": k["dorong_id"], "langkah": "selesai"})
    except SiteError:
        pass
    bersihkan(klien, k["dorong_id"])
    ok = 200 <= status < 400
    st = sesi.get(Staging, staging.id, populate_existing=True)
    st.status = StatusStaging.siap
    st.dorong_gagal_pada = None if ok else umum.sekarang()
    pangkas_snapshot(sesi, site.id, get_settings().staging_snapshot)
    detail = {"mode": mode, "berkas": len(ganti), "hapus": len(hapus), "db": bool(sql),
              "ukuran": format_byte(sum(e.ukuran for e in ganti)), "perubahan": k.get("perubahan", []),
              "snapshot_id": k.get("snapshot_id"), "halaman_utama": status}
    judul = f"Dorong ke produksi ({LABEL_MODE[mode]})"
    if not ok:
        judul += f": halaman utama membalas HTTP {status}" if status else ": halaman utama tidak dapat dihubungi"
    umum.catat_aktivitas(sesi, site.id, job, judul, detail, level="info" if ok else "error")
    sesi.commit()
    shutil.rmtree(kerja, ignore_errors=True)
    return {**detail, "dorong_gagal": not ok}


def tangani_staging_dorong(sesi, job, klien) -> dict:
    def inti(sesi, job, site, staging):
        try:
            return dorong(sesi, job, site, staging, klien, umum.buat_pembantu())
        except umum.Dibatalkan:
            # Batal hanya diperiksa di antara potongan, tidak pernah di tengah
            # tukar; area sementara di produksi dan di VPS dibuang (spec §6.4).
            dorong_id = umum.kemajuan(job).get("dorong_id")
            if dorong_id:
                bersihkan(klien, dorong_id)
            shutil.rmtree(umum.dir_site(site.id) / "dorong", ignore_errors=True)
            raise

    return umum.jalankan_staging(sesi, job, inti, StatusStaging.mendorong, "Dorong ke produksi")
```

Di `src/wpmgr/jobs/handlers.py`, tambahkan `from wpmgr.staging.dorong import tangani_staging_dorong` dan entri `JobType.staging_dorong: tangani_staging_dorong,`.

- [ ] **Step 4: Jalankan test.** Expected: `test_staging_dorong.py` lulus seluruhnya, seluruh integrasi tetap hijau, `ruff check .` bersih.

- [ ] **Step 5: Commit.**

```bash
git add src/wpmgr/staging/dorong.py src/wpmgr/jobs/handlers.py tests/integration/test_staging_dorong.py
git commit -m "feat(staging): dorong ke produksi dengan tanda air, snapshot, unggah bertahap, dan pemulihan"
```

---

### Task 17: Job `staging_kembalikan`, eksklusivitas per site, dan log aktivitas lengkap

**Files:**
- Modify: `src/wpmgr/staging/dorong.py`, `src/wpmgr/jobs/handlers.py`
- Create: `tests/integration/test_staging_kembalikan.py`

**Interfaces:**
- Consumes: Task 16 (`SumberDorong`, `unggah_semua`, `terapkan`, `bersihkan`, `cek_halaman`, `rencana_connector`), Task 12 (`Indeks`, `boleh_didorong`).
- Produces:
  - `wpmgr.staging.dorong.urai_meta_snapshot(teks) -> dict` (validasi `meta.json`), `kembalikan(sesi, job, site, staging | None, klien) -> dict`, `tangani_staging_kembalikan(sesi, job, klien) -> dict`, dan `HANDLER[JobType.staging_kembalikan]`.
  - Payload: `{"snapshot_id": int, "konfirmasi_nama": str}`. Nama harus sama persis dengan `site.nama` (spec §8.3). Tanda air tidak dicek.
  - Isi yang didorong ulang:
    - `ganti` adalah semua berkas di `snapshot/j<id>/berkas` (versi produksi lama);
    - `hapus` adalah `meta.baru` (berkas yang ditambahkan dorongan);
    - database hanya untuk snapshot timpa penuh (Koreksi #13), berupa `prelude.sql` + `db/*.sql`.
  - Snapshot yang dipakai berstatus `dipakai`. `dorong_gagal_pada` dibersihkan bila halaman utama 2xx/3xx. Log aktivitas: `Produksi dikembalikan dari snapshot #<id> oleh <email>`.
  - Kembalikan tetap bisa dijalankan setelah staging dihapus (baris `Staging` tidak ada). Status staging hanya diurus bila barisnya ada.

Log aktivitas untuk semua tindakan staging (spec §8.4):

| Tindakan | Tempat | Pesan |
|---|---|---|
| buat / segarkan | Task 14 | `Staging dibuat` / `Staging disegarkan` |
| uji | Task 15 | `Uji update di staging: lolos|gagal` |
| dorong | Task 16 | `Dorong ke produksi (hanya kode|timpa penuh)` |
| kembalikan | Task 17 | `Produksi dikembalikan dari snapshot #<id>` |
| jeda / jalankan / hapus / buat ulang kata sandi / batal | Task 19 (API) | `Staging dijeda`, `Staging dijalankan`, `Staging dihapus`, `Kata sandi preview dibuat ulang`, `Pembatalan job staging diminta` |
| jeda otomatis | Task 18 (cron) | `Staging dijeda otomatis setelah N hari tanpa akses` |

Setiap pesan diakhiri `oleh <email>` bila pengguna diketahui (`umum.catat_aktivitas`).

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/integration/test_staging_kembalikan.py`
```python
import pytest
from sqlalchemy.exc import IntegrityError
from staging_palsu import PembantuPalsu, ProduksiPalsu

from wpmgr.errors import STAGING_DITOLAK, SiteError
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    Site,
    Staging,
    StagingSnapshot,
)
from wpmgr.staging import dorong, tarik, umum

pytestmark = pytest.mark.integration

MTIME = 1_700_000_000
SQL_EKSPOR = b"DROP TABLE IF EXISTS `wp_posts`;\nCREATE TABLE `wp_posts` (`id` int);\n"


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))


@pytest.fixture
def prod(monkeypatch):
    p = ProduksiPalsu()
    p.berkas = {
        "index.php": (b"<?php // inti", MTIME),
        "wp-content/themes/t/style.css": (b"body{}", MTIME),
    }
    p.tabel = {"wp_posts": [b"DROP TABLE IF EXISTS `wp_posts`;\nCREATE TABLE `wp_posts` (`id` int);\n",
                            b"INSERT INTO `wp_posts` (`id`) VALUES ('7');\n"]}
    monkeypatch.setattr(umum, "buat_http", p.http)
    return p


@pytest.fixture
def pb(staging_aktif, monkeypatch, site_staging):
    palsu = PembantuPalsu(staging_aktif)
    ekspor = staging_aktif / str(site_staging.site_id) / "ekspor" / "dorong.sql"
    palsu.saat_wpcli = lambda nama, argumen: ekspor.write_bytes(SQL_EKSPOR) if "--export" in argumen else None
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


def _selesaikan(sesi):
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.success
    sesi.commit()


def _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, mode):
    site = sesi.get(Site, site_staging.site_id)
    tarik.tangani_staging_tarik(sesi, buat_job(sesi, site.id, JobType.staging_tarik), prod.klien(site))
    _selesaikan(sesi)
    files = staging_aktif / str(site.id) / "files"
    (files / "wp-content/themes/t/style.css").write_bytes(b"body{color:red}")
    (files / "wp-content/themes/t/baru.php").write_bytes(b"<?php //baru")
    job = buat_job(sesi, site.id, JobType.staging_dorong, {"mode": mode, "konfirmasi_nama": "Contoh"})
    dorong.tangani_staging_dorong(sesi, job, prod.klien(site))
    _selesaikan(sesi)
    return site, sesi.query(StagingSnapshot).one()


def _kembalikan(sesi, site, prod, snap_id, nama="Contoh"):
    job = buat_job(sesi, site.id, JobType.staging_kembalikan, {"snapshot_id": snap_id, "konfirmasi_nama": nama})
    hasil = dorong.tangani_staging_kembalikan(sesi, job, prod.klien(site))
    _selesaikan(sesi)
    return hasil


def test_kembalikan_hanya_kode_memulihkan_berkas_tanpa_database(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{color:red}"
    prod.langkah.clear()
    prod.unggahan.clear()
    hasil = _kembalikan(sesi, site, prod, snap.id)
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"
    assert "wp-content/themes/t/baru.php" not in prod.berkas
    assert "impor" not in prod.langkah
    assert prod.sql_diterapkan == b""
    assert hasil["db"] is False
    sesi.refresh(snap)
    assert snap.status == "dipakai"
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan == f"Produksi dikembalikan dari snapshot #{snap.id}").count() == 1


def test_kembalikan_timpa_penuh_memulihkan_database(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "timpa_penuh")
    assert prod.sql_diterapkan == SQL_EKSPOR
    prod.langkah.clear()
    prod.unggahan.clear()
    _kembalikan(sesi, site, prod, snap.id)
    assert prod.langkah == ["siapkan", "impor", "tukar", "selesai"]
    assert b"VALUES ('7')" in prod.sql_diterapkan
    assert prod.sql_diterapkan.startswith(b"SET NAMES utf8mb4;")


def test_kembalikan_tidak_memeriksa_tanda_air(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    sebelum = prod.hitung.get("/staging/tanda-air")
    prod.tanda_air["sumber"]["comments"] = {"maks_id": 99, "jumlah": 99}
    _kembalikan(sesi, site, prod, snap.id)
    assert prod.hitung.get("/staging/tanda-air") == sebelum


@pytest.mark.parametrize("nama", ["", "contoh", "Contoh ", None])
def test_kembalikan_butuh_nama_site_persis(sesi, site_staging, staging_aktif, prod, pb, nama):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    with pytest.raises(SiteError) as e:
        _kembalikan(sesi, site, prod, snap.id, nama=nama)
    assert e.value.error_class == STAGING_DITOLAK


def test_snapshot_dipangkas_atau_milik_site_lain_ditolak(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    snap.status = "dipangkas"
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _kembalikan(sesi, site, prod, snap.id)
    assert "dipangkas" in e.value.pesan
    for j in sesi.query(Job).filter(Job.status == JobStatus.pending).all():
        j.status = JobStatus.failed
    sesi.commit()
    with pytest.raises(SiteError):
        _kembalikan(sesi, site, prod, 999999)


def test_meta_snapshot_rusak_ditolak(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    (staging_aktif / snap.path / "meta.json").write_text('{"mode": "eval", "baru": ["../../etc/x"]}', encoding="utf-8")
    with pytest.raises(SiteError) as e:
        _kembalikan(sesi, site, prod, snap.id)
    assert "Snapshot rusak" in e.value.pesan


def test_kembalikan_setelah_staging_dihapus(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    sesi.delete(sesi.get(Staging, site_staging.id))
    sesi.commit()
    _kembalikan(sesi, site, prod, snap.id)
    assert prod.berkas["wp-content/themes/t/style.css"][0] == b"body{}"


def test_dorong_gagal_dibersihkan_setelah_kembalikan(sesi, site_staging, staging_aktif, prod, pb):
    site, snap = _siapkan_dorongan(sesi, site_staging, staging_aktif, prod, "hanya_kode")
    st = sesi.get(Staging, site_staging.id)
    st.dorong_gagal_pada = umum.sekarang()
    sesi.commit()
    _kembalikan(sesi, site, prod, snap.id)
    sesi.refresh(st)
    assert st.dorong_gagal_pada is None


def test_kembalikan_tidak_bisa_diantrekan_saat_dorong_tertunda(sesi, site_staging):
    buat_job(sesi, site_staging.site_id, JobType.staging_dorong, {"mode": "hanya_kode"})
    with pytest.raises(IntegrityError):
        buat_job(sesi, site_staging.site_id, JobType.staging_kembalikan, {"snapshot_id": 1})
    sesi.rollback()
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_staging_kembalikan.py -q`. Expected: `AttributeError: module 'wpmgr.staging.dorong' has no attribute 'tangani_staging_kembalikan'`.

- [ ] **Step 3: Implementasikan.** Tambahkan ke `src/wpmgr/staging/dorong.py` (impor tambahan: `from wpmgr.models import Site`, `from wpmgr.staging.aman import angka, boleh_didorong`, `from wpmgr.staging.pembantu import GalatPembantu`):

```python
CHARSET_SAH = ("utf8mb4", "utf8", "utf8mb3", "latin1")


def urai_meta_snapshot(teks: str) -> dict:
    """meta.json snapshot ditulis dashboard sendiri, tetapi tetap divalidasi sebelum dipakai."""
    try:
        meta = json.loads(teks)
    except ValueError:
        meta = None
    if not isinstance(meta, dict) or meta.get("mode") not in MODE or not isinstance(meta.get("baru"), list) \
            or meta.get("charset") not in CHARSET_SAH:
        raise umum.galat_gagal("Snapshot rusak: meta.json tidak sah.")
    baru = [p for p in meta["baru"] if isinstance(p, str) and boleh_didorong(p)]
    if len(baru) != len(meta["baru"]):
        raise umum.galat_gagal("Snapshot rusak: daftar berkas baru memuat path tidak sah.")
    return {"mode": meta["mode"], "baru": baru, "charset": meta["charset"],
            "batas_unggah": angka(meta.get("batas_unggah"), 262144, 4194304) or 4194304}


def kembalikan(sesi, job, site, staging: Staging | None, klien) -> dict:
    p = job.payload or {}
    snap_id = angka(p.get("snapshot_id"), 1, 2**62)
    row = sesi.get(StagingSnapshot, snap_id) if snap_id else None
    if row is None or row.site_id != site.id:
        raise umum.galat_ditolak("Snapshot tidak ditemukan.")
    if row.status not in ("tersedia", "dipakai"):
        raise umum.galat_ditolak("Snapshot sudah dipangkas dan tidak bisa dipakai.")
    if p.get("konfirmasi_nama") != site.nama:
        raise umum.galat_ditolak("Ketik nama site persis untuk mengembalikan produksi dari snapshot.")
    try:
        snap = jalur_di_dalam(get_settings().jalur_staging, row.path)
        meta = urai_meta_snapshot((snap / "meta.json").read_text(encoding="utf-8"))
    except (PathTidakAman, OSError):
        raise umum.galat_gagal("Snapshot rusak: berkas snapshot tidak ditemukan di server.") from None

    k = umum.kemajuan(job)
    if "tahap_balik" not in k:
        k = umum.simpan_kemajuan(sesi, job, tahap_balik="unggah", dorong_id=uuid.uuid4().hex,
                                 token=secrets.token_hex(16), unggah_nomor=0, byte_selesai=0,
                                 mulai=umum.sekarang().isoformat())
    ganti = sorted(Indeks(snap / "indeks.jsonl").muat().values(), key=lambda e: e.path)
    sql = [snap / "prelude.sql", *sorted((snap / "db").glob("*.sql"))] if meta["mode"] == "timpa_penuh" else []
    sumber = SumberDorong(ganti, snap / "berkas", meta["baru"], sql, meta["charset"])

    if k["tahap_balik"] == "unggah":
        k = unggah_semua(sesi, job, staging, klien, sumber, k["dorong_id"], min(UKURAN_UNGGAH, meta["batas_unggah"]), k)
        k = umum.simpan_kemajuan(sesi, job, tahap_balik="terapkan")
    if k["tahap_balik"] == "terapkan":
        terapkan(sesi, job, staging, klien, site.url, k["dorong_id"], k["jumlah_potongan"],
                 hashlib.sha256(rencana_connector(sumber)).hexdigest(), bool(sql), k["token"])
        k = umum.simpan_kemajuan(sesi, job, tahap_balik="cek")

    status = cek_halaman(site.url)
    try:
        umum.ulangi(klien.staging_terapkan, {"dorong_id": k["dorong_id"], "langkah": "selesai"})
    except SiteError:
        pass
    bersihkan(klien, k["dorong_id"])
    ok = 200 <= status < 400
    row = sesi.get(StagingSnapshot, row.id, populate_existing=True)
    row.status = "dipakai"
    if staging is not None:
        st = sesi.get(Staging, staging.id, populate_existing=True)
        st.status = StatusStaging.siap
        st.dorong_gagal_pada = None if ok else umum.sekarang()
    detail = {"snapshot_id": row.id, "mode": meta["mode"], "berkas": len(ganti), "hapus": len(meta["baru"]),
              "db": bool(sql), "halaman_utama": status}
    judul = f"Produksi dikembalikan dari snapshot #{row.id}"
    if not ok:
        judul += f": halaman utama membalas HTTP {status}" if status else ": halaman utama tidak dapat dihubungi"
    umum.catat_aktivitas(sesi, site.id, job, judul, detail, level="info" if ok else "error")
    sesi.commit()
    return {**detail, "dorong_gagal": not ok}


def tangani_staging_kembalikan(sesi, job, klien) -> dict:
    staging = sesi.scalar(select(Staging).where(Staging.site_id == job.site_id))
    if staging is not None:
        return umum.jalankan_staging(sesi, job, lambda s, j, site, st: kembalikan(s, j, site, st, klien),
                                     StatusStaging.mendorong, "Kembalikan produksi")
    # Staging sudah dihapus tetapi snapshot produksi masih ada: tetap bisa dipulihkan.
    if not get_settings().staging_aktif:
        raise umum.galat_ditolak("Fitur staging tidak aktif (WPMGR_STAGING_DOMAIN kosong).")
    try:
        return kembalikan(sesi, job, sesi.get(Site, job.site_id), None, klien)
    except OSError as exc:
        raise umum.galat_gagal(umum.pesan_os(exc)) from None
    except GalatPembantu as exc:
        raise umum.galat_gagal(exc.pesan) from None
```

Di `src/wpmgr/jobs/handlers.py`, ubah impor menjadi `from wpmgr.staging.dorong import tangani_staging_dorong, tangani_staging_kembalikan` dan tambahkan entri `JobType.staging_kembalikan: tangani_staging_kembalikan,`.

- [ ] **Step 4: Jalankan test.** Expected: `test_staging_kembalikan.py` lulus, seluruh integrasi hijau, `ruff check .` bersih.

- [ ] **Step 5: Commit.**

```bash
git add src/wpmgr/staging/dorong.py src/wpmgr/jobs/handlers.py tests/integration/test_staging_kembalikan.py
git commit -m "feat(staging): kembalikan produksi dari snapshot lewat jalur dorong yang sama"
```

---

## Fase E — Cron, API, dan UI

### Task 18: Perintah cron `staging-jeda-otomatis`, `renew-staging-certs`, `prune-staging`

**Files:**
- Create: `src/wpmgr/staging/cron.py`, `tests/integration/test_staging_cli.py`
- Modify: `src/wpmgr/kunci.py`, `src/wpmgr/cli.py`, `deploy/crontab`

**Interfaces:**
- Consumes: Task 11 (`Pembantu`), Task 13 (`umum`), Task 16 (`pangkas_snapshot`).
- Produces:
  - `wpmgr.kunci`: `KUNCI_STAGING_JEDA = 72_140_006`, `KUNCI_STAGING_SERTIFIKAT = 72_140_007`, `KUNCI_STAGING_PANGKAS = 72_140_008`.
  - `wpmgr.staging.cron`:
    - `jeda_otomatis(sesi, pb, sekarang) -> int`: jeda staging aktif yang akses terakhirnya (maks dari `dibuka_pada`, `ditarik_pada`, dan log akses router) lebih tua dari `WPMGR_STAGING_JEDA_HARI`; staging dengan job staging tertunda/berjalan dilewati; `dibuka_pada` ikut diperbarui dari log router;
    - `perpanjang_sertifikat(sesi, pb, sekarang) -> dict` (`{"berhasil", "gagal"}`);
    - `pangkas_staging(sesi, pb, sekarang) -> dict` (`{"snapshot", "direktori", "sementara", "router"}`).
  - `wpmgr.cli`: `staging_jeda_otomatis() -> int`, `renew_staging_certs() -> dict | None`, `prune_staging() -> dict | None`, dengan subperintah `staging-jeda-otomatis`, `renew-staging-certs`, `prune-staging`. Ketiganya tidak berbuat apa-apa bila `WPMGR_STAGING_DOMAIN` kosong, dan melewati putaran bila advisory lock masih dipegang.
  - `deploy/crontab`: jeda tiap jam (menit 10), sertifikat harian 03:20, pangkas harian 03:40.

`pangkas_staging` membersihkan:
- snapshot melebihi `WPMGR_STAGING_SNAPSHOT` per site;
- direktori `<uuid>` tanpa baris `Staging` dan tanpa snapshot yang masih dipakai, dihapus seluruhnya. Bila snapshot masih ada, hanya subdirektori selain `snapshot/` yang dihapus;
- `tarik/` dan `dorong/` yang berumur lebih dari 24 jam tanpa job staging tertunda/berjalan;
- berkas `router/<nama>.*` tanpa baris `Staging`, lalu `router-muat`.

Semua path yang dihapus disusun dari `jalur_di_dalam()` di bawah `WPMGR_STAGING_DIR`, dan hanya nama direktori berbentuk UUID yang dipertimbangkan.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/integration/test_staging_cli.py`
```python
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker
from staging_palsu import GB, PembantuPalsu

from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    JobType,
    Site,
    SiteStatus,
    Staging,
    StagingSnapshot,
    StatusStaging,
)
from wpmgr.staging import cron
from wpmgr.staging.pembantu import GalatPembantu, StatusPembantu

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _staging(sesi, nama, **kolom):
    s = Site(id=uuid.uuid4(), nama=nama, url=f"https://{nama}.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.flush()
    st = Staging(site_id=s.id, nama=nama, **kolom)
    sesi.add(st)
    sesi.commit()
    return st


def test_jeda_otomatis(sesi, staging_aktif):
    lama = SEKARANG - timedelta(days=5)
    diam = _staging(sesi, "diam", aktif=True, status=StatusStaging.siap, dibuka_pada=lama, ditarik_pada=lama)
    ramai = _staging(sesi, "ramai", aktif=True, status=StatusStaging.siap, dibuka_pada=lama, ditarik_pada=lama)
    sibuk = _staging(sesi, "sibuk", aktif=True, status=StatusStaging.siap, dibuka_pada=lama, ditarik_pada=lama)
    buat_job(sesi, sibuk.site_id, JobType.staging_tarik)
    pb = PembantuPalsu(staging_aktif)
    pb.status_palsu = StatusPembantu(8 * GB, 1, 1, {}, {"ramai": int((SEKARANG - timedelta(hours=2)).timestamp())})

    assert cron.jeda_otomatis(sesi, pb, SEKARANG) == 1
    assert ("jeda", "diam") in pb.panggilan
    assert ("jeda", "ramai") not in pb.panggilan and ("jeda", "sibuk") not in pb.panggilan
    for st in (diam, ramai, sibuk):
        sesi.refresh(st)
    assert (diam.aktif, diam.status) == (False, StatusStaging.dijeda)
    assert ramai.aktif is True
    assert ramai.dibuka_pada == SEKARANG - timedelta(hours=2)
    log = sesi.query(ActivityLog).filter(ActivityLog.site_id == diam.site_id).one()
    assert log.pesan == "Staging dijeda otomatis setelah 3 hari tanpa akses"


def test_jeda_otomatis_galat_pembantu_tidak_menghentikan_putaran(sesi, staging_aktif):
    lama = SEKARANG - timedelta(days=9)
    a = _staging(sesi, "a", aktif=True, status=StatusStaging.siap, ditarik_pada=lama)
    b = _staging(sesi, "b", aktif=True, status=StatusStaging.siap, ditarik_pada=lama)
    pb = PembantuPalsu(staging_aktif)
    asli = pb.jeda

    def jeda(nama):
        if nama == "a":
            raise GalatPembantu("docker", "Perintah Docker di server staging gagal.")
        asli(nama)

    pb.jeda = jeda
    assert cron.jeda_otomatis(sesi, pb, SEKARANG) == 1
    sesi.refresh(a)
    sesi.refresh(b)
    assert a.aktif is True and b.aktif is False


def test_perpanjang_sertifikat(sesi, staging_aktif):
    x = _staging(sesi, "x", ditarik_pada=SEKARANG)
    y = _staging(sesi, "y", ditarik_pada=SEKARANG)
    _staging(sesi, "belum-ditarik")
    pb = PembantuPalsu(staging_aktif)
    asli = pb.sertifikat

    def sertifikat(nama):
        if nama == "y":
            raise GalatPembantu("sertifikat", "sertifikat untuk y.staging.contoh.id belum dapat diterbitkan")
        asli(nama)

    pb.sertifikat = sertifikat
    assert cron.perpanjang_sertifikat(sesi, pb, SEKARANG) == {"berhasil": 1, "gagal": 1}
    sesi.refresh(x)
    sesi.refresh(y)
    assert x.sertifikat_pada == SEKARANG
    assert y.sertifikat_pada is None
    assert sesi.query(ActivityLog).filter(ActivityLog.level == "warning").count() == 1


def test_pangkas_staging(sesi, staging_aktif, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_SNAPSHOT", "1")
    get_settings.cache_clear()
    hidup = _staging(sesi, "hidup")
    akar = staging_aktif
    (akar / str(hidup.site_id) / "tarik").mkdir(parents=True)
    tua = time.time() - 2 * 86400
    os.utime(akar / str(hidup.site_id) / "tarik", (tua, tua))
    (akar / str(hidup.site_id) / "dorong").mkdir()
    for i in range(2):
        d = akar / str(hidup.site_id) / "snapshot" / f"j{i}"
        d.mkdir(parents=True)
        sesi.add(StagingSnapshot(site_id=hidup.site_id, jenis="sebelum_dorong", status="tersedia", ukuran=1,
                                 path=f"{hidup.site_id}/snapshot/j{i}", dibuat_pada=SEKARANG - timedelta(days=2 - i)))
    yatim = akar / str(uuid.uuid4())
    (yatim / "files").mkdir(parents=True)
    tanpa_staging = Site(id=uuid.uuid4(), nama="ts", url="https://ts.test", status=SiteStatus.active,
                         secret_terenkripsi=b"x")
    sesi.add(tanpa_staging)
    sesi.flush()
    sisa = akar / str(tanpa_staging.id)
    (sisa / "files").mkdir(parents=True)
    (sisa / "snapshot" / "j9").mkdir(parents=True)
    sesi.add(StagingSnapshot(site_id=tanpa_staging.id, jenis="sebelum_dorong", status="tersedia", ukuran=1,
                             path=f"{tanpa_staging.id}/snapshot/j9"))
    (akar / "bukan-uuid").mkdir()
    (akar / "router").mkdir()
    (akar / "router" / "hidup.rahasia").write_bytes(b"e" * 64)
    (akar / "router" / "hilang.rahasia").write_bytes(b"e" * 64)
    (akar / "router" / "hilang.htpasswd").write_bytes(b"x")
    sesi.commit()
    pb = PembantuPalsu(akar)

    hasil = cron.pangkas_staging(sesi, pb, SEKARANG)

    assert hasil == {"snapshot": 1, "direktori": 2, "sementara": 1, "router": 2}
    assert not (akar / str(hidup.site_id) / "tarik").exists()
    assert (akar / str(hidup.site_id) / "dorong").exists()
    assert not (akar / str(hidup.site_id) / "snapshot" / "j0").exists()
    assert (akar / str(hidup.site_id) / "snapshot" / "j1").exists()
    assert not yatim.exists()
    assert not (sisa / "files").exists() and (sisa / "snapshot" / "j9").exists()
    assert (akar / "bukan-uuid").exists()
    assert not (akar / "router" / "hilang.rahasia").exists()
    assert (akar / "router" / "hidup.rahasia").exists()
    assert ("router_muat",) in pb.panggilan


@pytest.fixture
def cli(engine, monkeypatch, staging_aktif):
    from wpmgr import cli as modul
    from wpmgr import db

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    monkeypatch.setattr(db, "engine", engine)
    pb = PembantuPalsu(staging_aktif)
    monkeypatch.setattr(modul.Pembantu, "dari_setelan", classmethod(lambda cls, s=None: pb))
    return modul


def test_cli_dilewati_bila_kunci_dipegang(cli, engine):
    from wpmgr.kunci import KUNCI_STAGING_JEDA, kunci_advisory

    with kunci_advisory(engine, KUNCI_STAGING_JEDA) as dapat:
        assert dapat
        assert cli.staging_jeda_otomatis() == 0


def test_cli_dilewati_bila_fitur_mati(cli, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    get_settings.cache_clear()
    assert cli.staging_jeda_otomatis() == 0
    assert cli.renew_staging_certs() is None
    assert cli.prune_staging() is None


def test_cli_main_mengenal_subperintah(cli):
    for perintah in ("staging-jeda-otomatis", "renew-staging-certs", "prune-staging"):
        assert cli.main([perintah]) == 0
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_staging_cli.py -q`. Expected: `ImportError: cannot import name 'cron' from 'wpmgr.staging'`.

- [ ] **Step 3: Implementasikan.** Tambahkan ke `src/wpmgr/kunci.py` setelah `KUNCI_GEOIP`:

```python
KUNCI_STAGING_JEDA = 72_140_006
KUNCI_STAGING_SERTIFIKAT = 72_140_007
KUNCI_STAGING_PANGKAS = 72_140_008
```

File: `src/wpmgr/staging/cron.py`
```python
"""Perintah cron staging (spec §11): jeda otomatis, sertifikat, pemangkasan disk."""

import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from wpmgr.config import get_settings
from wpmgr.models import (
    JOB_STAGING,
    Job,
    JobStatus,
    Staging,
    StagingSnapshot,
    StatusStaging,
)
from wpmgr.staging import umum
from wpmgr.staging.aman import POLA_NAMA, PathTidakAman, jalur_di_dalam
from wpmgr.staging.dorong import pangkas_snapshot
from wpmgr.staging.pembantu import GalatPembantu

log = logging.getLogger("wpmgr.staging.cron")
UMUR_SEMENTARA = timedelta(hours=24)


def _ada_job_staging(sesi, site_id) -> bool:
    return sesi.scalar(select(Job.id).where(
        Job.site_id == site_id, Job.tipe.in_(JOB_STAGING),
        Job.status.in_((JobStatus.pending, JobStatus.running))).limit(1)) is not None


def jeda_otomatis(sesi, pb, sekarang: datetime) -> int:
    hari = get_settings().staging_jeda_hari
    batas = sekarang - timedelta(days=hari)
    status = pb.status()
    n = 0
    for st in sesi.scalars(select(Staging).where(Staging.aktif.is_(True)).order_by(Staging.nama)).all():
        akses = status.akses.get(st.nama)
        if akses is not None:
            waktu_akses = datetime.fromtimestamp(akses, tz=timezone.utc)
            if st.dibuka_pada is None or waktu_akses > st.dibuka_pada:
                st.dibuka_pada = waktu_akses
        terakhir = max((t for t in (st.dibuka_pada, st.ditarik_pada) if t is not None), default=None)
        if (terakhir is not None and terakhir >= batas) or _ada_job_staging(sesi, st.site_id):
            sesi.commit()
            continue
        try:
            pb.jeda(st.nama)
        except GalatPembantu as exc:
            log.warning("Staging %s tidak dapat dijeda otomatis: %s", st.nama, exc.pesan)
            sesi.commit()
            continue
        st.aktif = False
        if st.status == StatusStaging.siap:
            st.status = StatusStaging.dijeda
        umum.catat_aktivitas(sesi, st.site_id, None, f"Staging dijeda otomatis setelah {hari} hari tanpa akses")
        sesi.commit()
        n += 1
    return n


def perpanjang_sertifikat(sesi, pb, sekarang: datetime) -> dict:
    hasil = {"berhasil": 0, "gagal": 0}
    for st in sesi.scalars(select(Staging).where(Staging.ditarik_pada.is_not(None)).order_by(Staging.nama)).all():
        try:
            pb.sertifikat(st.nama)
        except GalatPembantu as exc:
            hasil["gagal"] += 1
            umum.catat_aktivitas(sesi, st.site_id, None, f"Sertifikat staging belum dapat diterbitkan: {exc.pesan}",
                                 level="warning")
            sesi.commit()
            continue
        hasil["berhasil"] += 1
        # certbot --keep-until-expiring tidak memberi tahu apakah sertifikat
        # benar-benar diperbarui; yang dicatat hanya penerbitan pertama.
        if st.sertifikat_pada is None:
            st.sertifikat_pada = sekarang
        sesi.commit()
    return hasil


def _uuid(nama: str) -> bool:
    try:
        return str(uuid.UUID(nama)) == nama
    except ValueError:
        return False


def _tua(path: Path, sekarang: datetime) -> bool:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < sekarang - UMUR_SEMENTARA
    except OSError:
        return False


def pangkas_staging(sesi, pb, sekarang: datetime) -> dict:
    s = get_settings()
    akar = s.jalur_staging
    hasil = {"snapshot": 0, "direktori": 0, "sementara": 0, "router": 0}

    site_snapshot = set(sesi.scalars(select(StagingSnapshot.site_id).where(
        StagingSnapshot.status.in_(("tersedia", "dipakai")))).all())
    for site_id in sorted(site_snapshot, key=str):
        hasil["snapshot"] += pangkas_snapshot(sesi, site_id, s.staging_snapshot)
    sesi.commit()
    site_snapshot = set(sesi.scalars(select(StagingSnapshot.site_id).where(
        StagingSnapshot.status.in_(("tersedia", "dipakai")))).all())
    stagings = {st.site_id: st for st in sesi.scalars(select(Staging)).all()}

    if akar.is_dir():
        for d in sorted(akar.iterdir()):
            if not d.is_dir() or d.is_symlink() or not _uuid(d.name):
                continue
            site_id = uuid.UUID(d.name)
            if site_id not in stagings:
                if site_id not in site_snapshot:
                    shutil.rmtree(d, ignore_errors=True)
                else:
                    for anak in d.iterdir():
                        if anak.name == "snapshot":
                            continue
                        if anak.is_dir() and not anak.is_symlink():
                            shutil.rmtree(anak, ignore_errors=True)
                        else:
                            anak.unlink(missing_ok=True)
                hasil["direktori"] += 1
                continue
            if _ada_job_staging(sesi, site_id):
                continue
            for nama in ("tarik", "dorong"):
                try:
                    sementara = jalur_di_dalam(akar, f"{d.name}/{nama}")
                except PathTidakAman:
                    continue
                if sementara.is_dir() and _tua(sementara, sekarang):
                    shutil.rmtree(sementara, ignore_errors=True)
                    hasil["sementara"] += 1

    router = akar / "router"
    nama_hidup = {st.nama for st in stagings.values()}
    if router.is_dir():
        for f in sorted(router.iterdir()):
            nama = f.name.rsplit(".", 1)[0]
            if f.is_file() and f.suffix in (".rahasia", ".htpasswd") and POLA_NAMA.fullmatch(nama) \
                    and nama not in nama_hidup:
                f.unlink(missing_ok=True)
                hasil["router"] += 1
        if hasil["router"]:
            try:
                pb.router_muat()
            except GalatPembantu as exc:
                log.warning("router-muat setelah pemangkasan gagal: %s", exc.pesan)
    return hasil
```

Di `src/wpmgr/cli.py`, tambahkan impor:

```python
from wpmgr.kunci import KUNCI_STAGING_JEDA, KUNCI_STAGING_PANGKAS, KUNCI_STAGING_SERTIFIKAT
from wpmgr.staging.cron import jeda_otomatis, pangkas_staging, perpanjang_sertifikat
from wpmgr.staging.pembantu import Pembantu
```

(gabungkan ke blok `from wpmgr.kunci import (...)` yang sudah ada), lalu fungsi:

```python
def _staging_mati() -> bool:
    if not get_settings().staging_aktif:
        print("Staging tidak aktif (WPMGR_STAGING_DOMAIN kosong); dilewati")
        return True
    return False


def staging_jeda_otomatis() -> int:
    if _staging_mati():
        return 0
    with kunci_advisory(db.engine, KUNCI_STAGING_JEDA) as dapat:
        if not dapat:
            print("Jeda otomatis staging lain masih berjalan; dilewati")
            return 0
        with get_session() as sesi:
            n = jeda_otomatis(sesi, Pembantu.dari_setelan(), datetime.now(timezone.utc))
    print(f"{n} staging dijeda otomatis")
    return n


def renew_staging_certs() -> dict | None:
    if _staging_mati():
        return None
    with kunci_advisory(db.engine, KUNCI_STAGING_SERTIFIKAT) as dapat:
        if not dapat:
            print("Perpanjangan sertifikat staging lain masih berjalan; dilewati")
            return None
        with get_session() as sesi:
            hasil = perpanjang_sertifikat(sesi, Pembantu.dari_setelan(), datetime.now(timezone.utc))
    print(f"Sertifikat staging: {hasil['berhasil']} berhasil, {hasil['gagal']} gagal")
    return hasil


def prune_staging() -> dict | None:
    if _staging_mati():
        return None
    with kunci_advisory(db.engine, KUNCI_STAGING_PANGKAS) as dapat:
        if not dapat:
            print("Pemangkasan staging lain masih berjalan; dilewati")
            return None
        with get_session() as sesi:
            hasil = pangkas_staging(sesi, Pembantu.dari_setelan(), datetime.now(timezone.utc))
    print(", ".join(f"{k}: {v}" for k, v in hasil.items()))
    return hasil
```

Di `main()`, tambahkan parser `sub.add_parser("staging-jeda-otomatis")`, `sub.add_parser("renew-staging-certs")`, `sub.add_parser("prune-staging")`, dan cabang:

```python
    elif args.perintah == "staging-jeda-otomatis":
        staging_jeda_otomatis()
    elif args.perintah == "renew-staging-certs":
        renew_staging_certs()
    elif args.perintah == "prune-staging":
        prune_staging()
```

Di `deploy/crontab`, tambahkan di akhir:

```
# Lapis 3 -- staging
10   * * * *  cd /opt/wpmgr && .venv/bin/python -m wpmgr.cli staging-jeda-otomatis >> /var/log/wpmgr/cron.log 2>&1
20   3 * * *  cd /opt/wpmgr && .venv/bin/python -m wpmgr.cli renew-staging-certs   >> /var/log/wpmgr/cron.log 2>&1
40   3 * * *  cd /opt/wpmgr && .venv/bin/python -m wpmgr.cli prune-staging         >> /var/log/wpmgr/cron.log 2>&1
```

- [ ] **Step 4: Jalankan test.** Expected: `test_staging_cli.py` lulus, `test_cli.py` tetap lulus, `ruff check .` bersih.

- [ ] **Step 5: Commit.**

```bash
git add src/wpmgr/kunci.py src/wpmgr/cli.py src/wpmgr/staging/cron.py deploy/crontab tests/integration/test_staging_cli.py
git commit -m "feat(staging): cron jeda otomatis, perpanjangan sertifikat, dan pemangkasan"
```

---

### Task 19: API staging (buat/segarkan, jalan, jeda, hapus, dorong, kembalikan, sandi, uji, batal, SSO, email)

**Files:**
- Create: `src/wpmgr/web/routes_staging.py`, `tests/integration/test_api_staging.py`
- Modify: `src/wpmgr/web/app.py`, `src/wpmgr/web/routes_api.py`

**Interfaces:**
- Consumes: Task 11–18.
- Produces (semua butuh sesi login, `401` untuk anonim; POST/DELETE melewati middleware asal Lapis 1). Bila `WPMGR_STAGING_DOMAIN` kosong, semua route membalas 404 kecuali `GET .../staging`, yang membalas `{"aktif_fitur": false}`.

| Metode dan path | Isi |
|---|---|
| `GET /api/sites/{id}/staging` | `{aktif_fitur, izin_connector, staging, job, snapshot[≤50], uji[≤20]}` |
| `POST /api/sites/{id}/staging` `{konfirmasi}` | Buat (kata sandi preview dibalas sekali) atau segarkan → `{job_id, sandi, pengguna}` |
| `POST /api/sites/{id}/staging/jalan` | Cek RAM dan batas aktif, lalu `jalan` |
| `POST /api/sites/{id}/staging/jeda` | `jeda` |
| `DELETE /api/sites/{id}/staging` | Hapus container, database, akses router, dan berkas; snapshot dipertahankan |
| `GET /api/sites/{id}/staging/tanda-air` | `{perubahan: [str]}`, dari connector produksi secara langsung |
| `POST /api/sites/{id}/staging/dorong` `{mode, konfirmasi_nama}` | Antrekan `staging_dorong` |
| `POST /api/sites/{id}/staging/kembalikan` `{snapshot_id, konfirmasi_nama}` | Antrekan `staging_kembalikan` (nama harus persis) |
| `POST /api/sites/{id}/staging/sandi` | Kata sandi baru → `{sandi, pengguna}` (sekali) |
| `POST /api/sites/{id}/staging/uji` `{paket, konfirmasi}` | Antrekan `staging_uji_update` |
| `POST /api/staging/uji` `{items: [{site_id, tipe, slug, ke_versi}], konfirmasi}` | Dari halaman Update; satu job per site |
| `GET /api/sites/{id}/staging/uji` | Riwayat uji (≤50) |
| `GET /api/sites/{id}/staging/snapshot` | Snapshot (≤50) |
| `POST /api/sites/{id}/staging/batal` | Isi `batal_diminta_pada` |
| `GET /api/sites/{id}/staging/sso` | `{url}` ke `/__wpmgr_masuk` staging; memperbarui `dibuka_pada` |
| `GET /api/sites/{id}/staging/email` | ≤50 email Mailpit bertag nama staging |
| `GET /api/sites/{id}/staging/email/{msg_id}` | Isi teks satu email (tag harus cocok) |

  - `wpmgr.web.routes_staging.ringkas_kemajuan(job, sekarang=None) -> dict` berisi `{tahap, label, persen, byte_selesai, byte_total, teks}`. Dipakai `/api/jobs/active` (field baru `progres` untuk job staging).
  - `/api/packages` mendapat field `uji`: `{hasil, dibuat_pada, alasan}` dari `StagingUji` terbaru yang memuat paket itu ke `versi_tersedia`, atau `null`.
  - Konflik (job staging lain aktif, staging belum ada, izin connector mati, RAM/batas aktif) dibalas 409 dengan pesan yang bisa dibaca. Galat skrip pembantu dibalas 502 dengan `GalatPembantu.pesan`.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/integration/test_api_staging.py`
```python
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import httpx
import pytest
from fastapi.testclient import TestClient
from staging_palsu import GB, PembantuPalsu, ProduksiPalsu

from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    PackageType,
    Site,
    SitePackage,
    SiteStatus,
    Staging,
    StagingSnapshot,
    StagingUji,
    StatusStaging,
)
from wpmgr.staging import umum
from wpmgr.staging.pembantu import GalatPembantu, StatusPembantu

pytestmark = pytest.mark.integration

ROUTE = [
    ("GET", "/api/sites/{id}/staging"), ("POST", "/api/sites/{id}/staging"), ("DELETE", "/api/sites/{id}/staging"),
    ("POST", "/api/sites/{id}/staging/jalan"), ("POST", "/api/sites/{id}/staging/jeda"),
    ("GET", "/api/sites/{id}/staging/tanda-air"), ("POST", "/api/sites/{id}/staging/dorong"),
    ("POST", "/api/sites/{id}/staging/kembalikan"), ("POST", "/api/sites/{id}/staging/sandi"),
    ("POST", "/api/sites/{id}/staging/uji"), ("POST", "/api/staging/uji"), ("GET", "/api/sites/{id}/staging/uji"),
    ("GET", "/api/sites/{id}/staging/snapshot"), ("POST", "/api/sites/{id}/staging/batal"),
    ("GET", "/api/sites/{id}/staging/sso"), ("GET", "/api/sites/{id}/staging/email"),
    ("GET", "/api/sites/{id}/staging/email/abc123"),
]


@pytest.fixture
def pb(staging_aktif, monkeypatch):
    palsu = PembantuPalsu(staging_aktif)
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


@pytest.fixture
def siap(sesi, site_staging):
    site_staging.ditarik_pada = datetime.now(timezone.utc)
    site_staging.aktif = True
    site_staging.status = StatusStaging.siap
    site_staging.tanda_air = {"sumber": {"comments": {"maks_id": 3, "jumlah": 2}}}
    sesi.commit()
    return site_staging


@pytest.mark.parametrize("metode,path", ROUTE)
def test_anonim_ditolak(engine, staging_aktif, metode, path):
    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), base_url="https://testserver")
    r = anon.request(metode, path.format(id=uuid.uuid4()), json={})
    assert r.status_code == 401


def test_fitur_mati(klien_web, site, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    get_settings.cache_clear()
    assert klien_web.get(f"/api/sites/{site.id}/staging").json() == {"aktif_fitur": False}
    assert klien_web.post(f"/api/sites/{site.id}/staging", json={}).status_code == 404


def test_buat_staging_membalas_sandi_sekali(klien_web, sesi, site, staging_aktif, pb):
    site.url = "https://www.Toko-Contoh.co.id"
    site.fitur = ["staging"]
    sesi.commit()
    r = klien_web.post(f"/api/sites/{site.id}/staging", json={})
    assert r.status_code == 200
    d = r.json()
    assert d["pengguna"] == "staging" and len(d["sandi"]) >= 16
    st = sesi.query(Staging).one()
    assert st.nama == "toko-contoh-co-id"
    assert bcrypt.checkpw(d["sandi"].encode(), st.sandi_hash.encode())
    job = sesi.get(Job, d["job_id"])
    assert job.tipe == JobType.staging_tarik
    job.status = JobStatus.success
    sesi.commit()
    kedua = klien_web.post(f"/api/sites/{site.id}/staging", json={}).json()
    assert kedua["sandi"] is None


def test_nama_bentrok_diberi_akhiran(klien_web, sesi, site, staging_aktif, pb):
    lain = Site(id=uuid.uuid4(), nama="L", url="https://lain.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(lain)
    sesi.flush()
    sesi.add(Staging(site_id=lain.id, nama="toko-id"))
    site.url = "https://toko.id"
    site.fitur = ["staging"]
    sesi.commit()
    klien_web.post(f"/api/sites/{site.id}/staging", json={})
    assert sesi.query(Staging).filter(Staging.site_id == site.id).one().nama == "toko-id-2"


def test_buat_ditolak_tanpa_izin_connector_dan_batas_aktif(klien_web, sesi, site, staging_aktif, pb):
    r = klien_web.post(f"/api/sites/{site.id}/staging", json={})
    assert r.status_code == 409 and "Izinkan staging" in r.json()["detail"]
    site.fitur = ["staging"]
    for i in range(3):
        s = Site(id=uuid.uuid4(), nama=f"S{i}", url=f"https://s{i}.test", status=SiteStatus.active, secret_terenkripsi=b"x")
        sesi.add(s)
        sesi.flush()
        sesi.add(Staging(site_id=s.id, nama=f"s{i}", aktif=True))
    sesi.commit()
    r = klien_web.post(f"/api/sites/{site.id}/staging", json={})
    assert r.status_code == 409 and "3 staging aktif" in r.json()["detail"]


def test_segarkan_butuh_konfirmasi_bila_staging_diubah(klien_web, sesi, siap, pb):
    siap.diubah_pada = siap.ditarik_pada + timedelta(hours=1)
    sesi.commit()
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging", json={})
    assert r.status_code == 409 and "diubah" in r.json()["detail"]
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging", json={"konfirmasi": True}).status_code == 200


def test_job_ganda_ditolak(klien_web, sesi, siap, pb):
    buat_job(sesi, siap.site_id, JobType.staging_dorong, {"mode": "hanya_kode"})
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging", json={})
    assert r.status_code == 409 and "pekerjaan staging" in r.json()["detail"]


def test_status_staging_dan_kemajuan_job(klien_web, sesi, siap, pb):
    job = buat_job(sesi, siap.site_id, JobType.staging_tarik)
    job.payload = {"kemajuan": {"tahap": "berkas", "byte_total": 200 * 1024**2, "byte_selesai": 50 * 1024**2,
                                "mulai": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}}
    sesi.commit()
    d = klien_web.get(f"/api/sites/{siap.site_id}/staging").json()
    assert d["aktif_fitur"] is True
    assert d["staging"]["url"] == "https://contoh-test.staging.contoh.id"
    assert d["staging"]["status"] == "siap"
    assert d["job"]["progres"]["persen"] == 25
    assert d["job"]["progres"]["teks"].startswith("Menyalin berkas 25% · 50,0 MB dari 200,0 MB · sisa ±30 menit")
    aktif = klien_web.get("/api/jobs/active").json()
    assert aktif[0]["progres"].startswith("Menyalin berkas 25%")


def test_jalan_dan_jeda(klien_web, sesi, siap, pb):
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/jeda").status_code == 200
    sesi.refresh(siap)
    assert (siap.aktif, siap.status) == (False, StatusStaging.dijeda)
    pb.status_palsu = StatusPembantu(int(1.5 * GB), 1, 1, {}, {})
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/jalan")
    assert r.status_code == 409 and "RAM tersedia" in r.json()["detail"]
    pb.status_palsu = StatusPembantu(8 * GB, 1, 1, {}, {})
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/jalan").status_code == 200
    sesi.refresh(siap)
    assert (siap.aktif, siap.status) == (True, StatusStaging.siap)
    assert ("jeda", "contoh-test") in pb.panggilan and ("jalan", "contoh-test") in pb.panggilan
    pesan = [a.pesan for a in sesi.query(ActivityLog).order_by(ActivityLog.id)]
    assert pesan == ["Staging dijeda oleh a@b.test", "Staging dijalankan oleh a@b.test"]


def test_galat_pembantu_menjadi_502(klien_web, siap, pb):
    pb.gagal["jeda"] = GalatPembantu("docker", "Perintah Docker di server staging gagal.")
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/jeda")
    assert r.status_code == 502
    assert r.json()["detail"] == "Perintah Docker di server staging gagal."


def test_hapus_mempertahankan_snapshot(klien_web, sesi, siap, staging_aktif, pb):
    akar = staging_aktif / str(siap.site_id)
    (akar / "files").mkdir(parents=True)
    (akar / "snapshot" / "j1").mkdir(parents=True)
    (staging_aktif / "router").mkdir()
    (staging_aktif / "router" / "contoh-test.rahasia").write_bytes(b"e" * 64)
    r = klien_web.delete(f"/api/sites/{siap.site_id}/staging")
    assert r.status_code == 200
    assert [p[0] for p in pb.panggilan] == ["hapus", "db_hapus", "router_muat"]
    assert not (akar / "files").exists() and (akar / "snapshot" / "j1").exists()
    assert not (staging_aktif / "router" / "contoh-test.rahasia").exists()
    assert sesi.query(Staging).count() == 0
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Staging dihapus%")).count() == 1


def test_hapus_ditolak_saat_job_berjalan(klien_web, sesi, siap, pb):
    buat_job(sesi, siap.site_id, JobType.staging_tarik)
    assert klien_web.delete(f"/api/sites/{siap.site_id}/staging").status_code == 409
    assert pb.panggilan == []


def test_dorong_mengantrekan_job(klien_web, sesi, siap, pb):
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/dorong", json={"mode": "semua"}).status_code == 422
    r = klien_web.post(f"/api/sites/{siap.site_id}/staging/dorong",
                       json={"mode": "timpa_penuh", "konfirmasi_nama": "Contoh"})
    job = sesi.get(Job, r.json()["job_id"])
    assert (job.tipe, job.payload) == (JobType.staging_dorong, {"mode": "timpa_penuh", "konfirmasi_nama": "Contoh"})


def test_tanda_air_langsung_dari_produksi(klien_web, sesi, siap, pb, monkeypatch):
    prod = ProduksiPalsu()
    prod.tanda_air = {"sumber": {"comments": {"maks_id": 10, "jumlah": 9}}}
    monkeypatch.setattr("wpmgr.web.routes_staging.buat_klien", prod.klien)
    assert klien_web.get(f"/api/sites/{siap.site_id}/staging/tanda-air").json() == {"perubahan": ["7 komentar baru"]}


def test_kembalikan_butuh_nama_dan_snapshot_milik_site(klien_web, sesi, siap, pb):
    snap = StagingSnapshot(site_id=siap.site_id, jenis="sebelum_dorong", status="tersedia", ukuran=1, path="x")
    sesi.add(snap)
    sesi.commit()
    dasar = f"/api/sites/{siap.site_id}/staging/kembalikan"
    assert klien_web.post(dasar, json={"snapshot_id": snap.id, "konfirmasi_nama": "contoh"}).status_code == 422
    assert klien_web.post(dasar, json={"snapshot_id": 999999, "konfirmasi_nama": "Contoh"}).status_code == 404
    r = klien_web.post(dasar, json={"snapshot_id": snap.id, "konfirmasi_nama": "Contoh"})
    assert sesi.get(Job, r.json()["job_id"]).tipe == JobType.staging_kembalikan


def test_sandi_baru_ditampilkan_sekali(klien_web, sesi, siap, staging_aktif, pb):
    lama = siap.sandi_hash
    d = klien_web.post(f"/api/sites/{siap.site_id}/staging/sandi").json()
    sesi.refresh(siap)
    assert siap.sandi_hash != lama
    assert bcrypt.checkpw(d["sandi"].encode(), siap.sandi_hash.encode())
    baris = (staging_aktif / "router" / "contoh-test.htpasswd").read_bytes()
    assert baris == f"staging:{siap.sandi_hash}\n".encode()
    assert ("router_muat",) in pb.panggilan
    assert "sandi" not in klien_web.get(f"/api/sites/{siap.site_id}/staging").json()["staging"]
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("Kata sandi preview dibuat ulang%")).count() == 1


def test_uji_per_site_dan_dari_halaman_update(klien_web, sesi, siap, pb):
    sesi.add(SitePackage(site_id=siap.site_id, tipe=PackageType.plugin, slug="akismet/akismet.php", nama="Akismet",
                         versi_terpasang="5.2", versi_tersedia="5.3.1", last_scan_at=datetime.now(timezone.utc)))
    sesi.commit()
    r = klien_web.post("/api/staging/uji", json={"items": [
        {"site_id": str(siap.site_id), "tipe": "plugin", "slug": "akismet/akismet.php", "ke_versi": "5.3.1"}]})
    assert r.status_code == 200
    job = sesi.get(Job, r.json()["job_ids"][0])
    assert job.payload["paket"] == [{"tipe": "plugin", "slug": "akismet/akismet.php", "dari": "5.2", "ke": "5.3.1"}]
    job.status = JobStatus.success
    sesi.commit()
    buruk = klien_web.post(f"/api/sites/{siap.site_id}/staging/uji",
                           json={"paket": [{"tipe": "plugin", "slug": "a/a.php", "ke": "1;id"}]})
    assert buruk.status_code == 422
    tanpa = klien_web.post("/api/staging/uji", json={"items": [
        {"site_id": str(uuid.uuid4()), "tipe": "plugin", "slug": "a/a.php", "ke_versi": "1"}]})
    assert tanpa.status_code == 404


def test_lencana_uji_di_daftar_paket(klien_web, sesi, siap):
    sesi.add(SitePackage(site_id=siap.site_id, tipe=PackageType.plugin, slug="akismet/akismet.php", nama="Akismet",
                         versi_terpasang="5.2", versi_tersedia="5.3.1", last_scan_at=datetime.now(timezone.utc)))
    sesi.add(StagingUji(site_id=siap.site_id, paket=[{"tipe": "plugin", "slug": "akismet/akismet.php", "dari": "5.2",
                                                      "ke": "5.3.1"}], hasil="gagal",
                        pemeriksaan={"alasan": ["/ membalas HTTP 500"]}))
    sesi.commit()
    paket = klien_web.get("/api/packages").json()[0]
    assert paket["uji"]["hasil"] == "gagal"
    assert paket["uji"]["alasan"] == "/ membalas HTTP 500"


def test_batal(klien_web, sesi, siap):
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/batal").status_code == 409
    buat_job(sesi, siap.site_id, JobType.staging_tarik)
    assert klien_web.post(f"/api/sites/{siap.site_id}/staging/batal").status_code == 200
    sesi.refresh(siap)
    assert siap.batal_diminta_pada is not None


def test_sso_staging(klien_web, sesi, siap):
    url = klien_web.get(f"/api/sites/{siap.site_id}/staging/sso").json()["url"]
    assert url.startswith("https://contoh-test.staging.contoh.id/__wpmgr_masuk?e=")
    assert "&m=" in url and "&sso=" in url
    sesi.refresh(siap)
    assert siap.dibuka_pada is not None
    assert sesi.query(ActivityLog).filter(ActivityLog.pesan.like("SSO staging dibuka%")).count() == 1


def test_email_mailpit_disaring_per_staging(klien_web, siap, monkeypatch):
    diminta = []

    def mailpit(r):
        diminta.append(r)
        if r.url.path == "/api/v1/search":
            return httpx.Response(200, json={"messages": [
                {"ID": "abc123", "From": {"Address": "<b>x@y.id</b>"}, "To": [{"Address": "c@d.id"}],
                 "Subject": "Pesanan \x00baru", "Created": "2026-09-26T01:02:03Z", "Tags": ["contoh-test"],
                 "Snippet": "halo"},
                {"ID": "../jahat", "From": {}, "To": [], "Subject": "x", "Tags": ["contoh-test"]},
            ], "total": 2})
        if r.url.path == "/api/v1/message/abc123":
            return httpx.Response(200, json={"ID": "abc123", "Subject": "Pesanan", "From": {"Address": "x@y.id"},
                                             "To": [{"Address": "c@d.id"}], "Date": "2026-09-26T01:02:03Z",
                                             "Text": "isi", "HTML": "<script>", "Tags": ["contoh-test"]})
        if r.url.path == "/api/v1/message/lain99":
            return httpx.Response(200, json={"ID": "lain99", "Tags": ["staging-lain"], "Text": "rahasia"})
        return httpx.Response(404)

    monkeypatch.setattr(umum, "buat_http", lambda: httpx.Client(transport=httpx.MockTransport(mailpit)))
    daftar = klien_web.get(f"/api/sites/{siap.site_id}/staging/email").json()
    assert daftar == [{"id": "abc123", "dari": "<b>x@y.id</b>", "ke": ["c@d.id"], "subjek": "Pesanan baru",
                       "waktu": "2026-09-26T01:02:03Z", "cuplikan": "halo"}]
    assert diminta[0].url.params["query"] == 'tag:"contoh-test"'
    isi = klien_web.get(f"/api/sites/{siap.site_id}/staging/email/abc123").json()
    assert isi["teks"] == "isi" and "html" not in isi
    assert klien_web.get(f"/api/sites/{siap.site_id}/staging/email/lain99").status_code == 404
    assert klien_web.get(f"/api/sites/{siap.site_id}/staging/email/..%2Fx").status_code == 404


def test_daftar_uji_dan_snapshot_dibatasi(klien_web, sesi, siap):
    for i in range(60):
        sesi.add(StagingSnapshot(site_id=siap.site_id, jenis="sebelum_dorong", status="tersedia", ukuran=i, path=f"s{i}"))
        sesi.add(StagingUji(site_id=siap.site_id, paket=[], hasil="lolos", pemeriksaan={"alasan": []}))
    sesi.commit()
    assert len(klien_web.get(f"/api/sites/{siap.site_id}/staging/snapshot").json()) == 50
    assert len(klien_web.get(f"/api/sites/{siap.site_id}/staging/uji").json()) == 50
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_api_staging.py -q`. Expected: sebagian besar gagal dengan 404 (route belum ada) dan test anonim menerima 404, bukan 401.

- [ ] **Step 3: Implementasikan route.**

File: `src/wpmgr/web/routes_staging.py`
```python
"""JSON API staging (spec §9, Task 19)."""

import json
import re
import secrets
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from wpmgr import db
from wpmgr.config import get_settings
from wpmgr.crypto import dekripsi_secret, enkripsi_secret
from wpmgr.errors import SiteError
from wpmgr.fitur import STAGING, punya_fitur
from wpmgr.jobs.handlers import buat_klien
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    JOB_STAGING,
    Job,
    JobStatus,
    JobType,
    PackageType,
    Site,
    SitePackage,
    Staging,
    StagingSnapshot,
    StagingUji,
    StatusStaging,
    User,
)
from wpmgr.sso import buat_token
from wpmgr.staging import umum
from wpmgr.staging.aman import angka, bersih_teks, jalur_di_dalam, nama_dari_url
from wpmgr.staging.dorong import MODE
from wpmgr.staging.pembantu import (
    GalatPembantu,
    hapus_akses_router,
    hash_sandi,
    sandi_baru,
    tautan_masuk,
    tulis_akses_router,
)
from wpmgr.staging.rencana import (
    bandingkan_tanda_air,
    cek_maks_aktif,
    cek_ram,
    format_byte,
    urai_tanda_air,
)
from wpmgr.staging.uji import urai_paket
from wpmgr.web.auth import pengguna_api

router = APIRouter()
PenggunaApi = Annotated[User, Depends(pengguna_api)]

BATAS_DAFTAR = 50
BATAS_UJI_RINGKAS = 20
BATAS_EMAIL_BYTE = 2 * 1024 * 1024
POLA_ID_EMAIL = re.compile(r"[A-Za-z0-9]{1,64}")
TEKS_STATUS = {"menyalin": "Menyalin dari produksi", "siap": "Siap", "berjalan_uji": "Menjalankan uji update",
               "mendorong": "Mendorong ke produksi", "dijeda": "Dijeda", "gagal": "Gagal"}
LABEL_TAHAP = {
    "mulai": "Menunggu giliran", "manifest": "Membaca daftar berkas", "berkas": "Menyalin berkas",
    "tanda_air": "Membaca tanda air", "db": "Menyalin database", "impor": "Mengimpor database",
    "penyiapan": "Menyiapkan container", "sertifikat": "Menerbitkan sertifikat", "sebelum": "Memeriksa halaman",
    "update": "Menjalankan update", "sesudah": "Memeriksa halaman sesudah update", "nilai": "Menilai hasil",
    "rencana": "Menyusun rencana", "snapshot_berkas": "Snapshot berkas produksi",
    "snapshot_db": "Snapshot database produksi", "snapshot_catat": "Mencatat snapshot",
    "unggah": "Mengunggah ke produksi", "cek_ulang": "Memeriksa ulang data baru", "terapkan": "Menerapkan di produksi",
    "cek": "Memeriksa halaman utama",
}


def _iso(nilai):
    return nilai.isoformat() if nilai else None


def ringkas_kemajuan(job: Job, sekarang: datetime | None = None) -> dict:
    """Persen, MB, dan perkiraan sisa waktu untuk strip progres (spec §9)."""
    sekarang = sekarang or datetime.now(timezone.utc)
    k = (job.payload or {}).get("kemajuan") or {}
    tahap_uji = k.get("tahap_uji")
    tahap = k.get("tahap_dorong") or k.get("tahap_balik") or (tahap_uji if tahap_uji not in (None, "tarik") else None) \
        or k.get("tahap") or "mulai"
    tahap = tahap if tahap in LABEL_TAHAP else "mulai"
    total = angka(k.get("byte_total"), 0, 2**62) or 0
    selesai = angka(k.get("byte_selesai"), 0, 2**62) or 0
    if total:
        selesai = min(selesai, total)
    persen = int(selesai * 100 // total) if total else None
    teks = LABEL_TAHAP[tahap]
    if total and tahap in ("berkas", "unggah", "snapshot_berkas"):
        teks += f" {persen}% · {format_byte(selesai)} dari {format_byte(total)}"
        try:
            mulai = datetime.fromisoformat(str(k.get("mulai")))
        except ValueError:
            mulai = None
        if mulai is not None and mulai.tzinfo is not None and 0 < selesai < total:
            detik = (sekarang - mulai).total_seconds()
            if detik > 0:
                sisa = (total - selesai) / (selesai / detik)
                teks += f" · sisa ±{max(1, round(sisa / 60))} menit"
    return {"tahap": tahap, "label": LABEL_TAHAP[tahap], "persen": persen, "byte_selesai": selesai,
            "byte_total": total, "teks": teks}


def _fitur() -> None:
    if not get_settings().staging_aktif:
        raise HTTPException(status_code=404, detail="Fitur staging tidak aktif")


def _site(sesi, site_id: uuid.UUID) -> Site:
    site = sesi.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site tidak ditemukan")
    return site


def _staging(sesi, site_id: uuid.UUID) -> Staging:
    st = sesi.scalar(select(Staging).where(Staging.site_id == site_id))
    if st is None:
        raise HTTPException(status_code=409, detail="Staging untuk site ini belum dibuat.")
    return st


def _job_aktif(sesi, site_id) -> Job | None:
    return sesi.scalar(select(Job).where(
        Job.site_id == site_id, Job.tipe.in_(JOB_STAGING),
        Job.status.in_((JobStatus.pending, JobStatus.running))).order_by(Job.id.desc()).limit(1))


def _tolak_bila_sibuk(sesi, site_id) -> None:
    if _job_aktif(sesi, site_id) is not None:
        raise HTTPException(status_code=409, detail="Tunggu pekerjaan staging yang sedang berjalan selesai.")


def _antrekan(sesi, site_id, tipe: JobType, payload: dict, pengguna: User) -> Job:
    try:
        return buat_job(sesi, site_id, tipe, payload, dibuat_oleh=pengguna.id)
    except IntegrityError:
        sesi.rollback()
        raise HTTPException(status_code=409,
                            detail="Masih ada pekerjaan staging yang tertunda atau berjalan untuk site ini.") from None


def _pembantu_gagal(exc: GalatPembantu) -> HTTPException:
    return HTTPException(status_code=502, detail=exc.pesan)


def _dict_staging(st: Staging) -> dict:
    return {
        "nama": st.nama, "status": st.status.value, "status_teks": TEKS_STATUS[st.status.value], "aktif": st.aktif,
        "url": umum.url_staging(st), "pengguna": "staging", "versi_php": st.versi_php,
        "ukuran_file_teks": format_byte(st.ukuran_file), "ukuran_db_teks": format_byte(st.ukuran_db),
        "ditarik_pada": _iso(st.ditarik_pada), "diubah_pada": _iso(st.diubah_pada), "dibuka_pada": _iso(st.dibuka_pada),
        "sertifikat_pada": _iso(st.sertifikat_pada), "galat": st.galat, "dorong_gagal_pada": _iso(st.dorong_gagal_pada),
        "batal_diminta": st.batal_diminta_pada is not None,
        "diubah_sejak_tarik": bool(st.diubah_pada and st.ditarik_pada and st.diubah_pada > st.ditarik_pada),
    }


def _dict_snapshot(s: StagingSnapshot) -> dict:
    detail = s.detail or {}
    return {"id": s.id, "status": s.status, "jenis": s.jenis, "ukuran_teks": format_byte(s.ukuran),
            "mode": detail.get("mode"), "jumlah_berkas": detail.get("jumlah_berkas"),
            "perubahan": detail.get("perubahan") or [], "dibuat_pada": _iso(s.dibuat_pada)}


def _dict_uji(u: StagingUji) -> dict:
    return {"id": u.id, "hasil": u.hasil, "paket": u.paket, "alasan": (u.pemeriksaan or {}).get("alasan") or [],
            "halaman": (u.pemeriksaan or {}).get("halaman") or [], "dibuat_pada": _iso(u.dibuat_pada)}


def _daftar_snapshot(sesi, site_id, batas: int) -> list[dict]:
    return [_dict_snapshot(s) for s in sesi.scalars(
        select(StagingSnapshot).where(StagingSnapshot.site_id == site_id)
        .order_by(StagingSnapshot.dibuat_pada.desc(), StagingSnapshot.id.desc()).limit(batas))]


def _daftar_uji(sesi, site_id, batas: int) -> list[dict]:
    return [_dict_uji(u) for u in sesi.scalars(
        select(StagingUji).where(StagingUji.site_id == site_id)
        .order_by(StagingUji.dibuat_pada.desc(), StagingUji.id.desc()).limit(batas))]


@router.get("/api/sites/{site_id}/staging")
def status_staging(site_id: uuid.UUID, pengguna: PenggunaApi):
    if not get_settings().staging_aktif:
        return {"aktif_fitur": False}
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        st = sesi.scalar(select(Staging).where(Staging.site_id == site_id))
        if st is not None:
            umum.perbarui_diubah(st)
            sesi.commit()
        job = _job_aktif(sesi, site_id)
        return {
            "aktif_fitur": True,
            "izin_connector": punya_fitur(site, STAGING),
            "staging": _dict_staging(st) if st is not None else None,
            "job": {"id": job.id, "tipe": job.tipe.value, "status": job.status.value, "progres": ringkas_kemajuan(job)}
            if job is not None else None,
            "snapshot": _daftar_snapshot(sesi, site_id, BATAS_DAFTAR),
            "uji": _daftar_uji(sesi, site_id, BATAS_UJI_RINGKAS),
        }


class PermintaanBuat(BaseModel):
    konfirmasi: bool = False


def _nama_unik(sesi, dasar: str) -> str:
    kandidat = dasar
    for i in range(2, 100):
        if sesi.scalar(select(Staging.id).where(Staging.nama == kandidat)) is None:
            return kandidat
        akhiran = f"-{i}"
        kandidat = dasar[:40 - len(akhiran)].rstrip("-") + akhiran
    raise HTTPException(status_code=409, detail="Nama staging untuk site ini tidak dapat ditentukan.")


@router.post("/api/sites/{site_id}/staging")
def buat_atau_segarkan(site_id: uuid.UUID, req: PermintaanBuat, pengguna: PenggunaApi):
    _fitur()
    s = get_settings()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        if not punya_fitur(site, STAGING):
            raise HTTPException(status_code=409, detail="Connector site ini belum mengizinkan staging. Aktifkan "
                                                        "'Izinkan staging' di Pengaturan -> WP Manager (connector 3.0).")
        st = sesi.scalar(select(Staging).where(Staging.site_id == site_id))
        sandi = None
        if st is None:
            aktif = sesi.scalar(select(func.count()).select_from(Staging).where(Staging.aktif.is_(True))) or 0
            pesan = cek_maks_aktif(aktif, s.staging_maks_aktif)
            if pesan:
                raise HTTPException(status_code=409, detail=pesan)
            _tolak_bila_sibuk(sesi, site_id)
            sandi = sandi_baru()
            st = Staging(site_id=site.id, nama=_nama_unik(sesi, nama_dari_url(site.url)), sandi_hash=hash_sandi(sandi),
                         rahasia_router_terenkripsi=enkripsi_secret(secrets.token_hex(32)))
            sesi.add(st)
            try:
                sesi.commit()
            except IntegrityError:
                sesi.rollback()
                raise HTTPException(status_code=409, detail="Staging untuk site ini sedang dibuat.") from None
        else:
            umum.perbarui_diubah(st)
            sesi.commit()
            if st.diubah_pada and st.ditarik_pada and st.diubah_pada > st.ditarik_pada and not req.konfirmasi:
                raise HTTPException(status_code=409, detail="Staging diubah sejak tarik terakhir; perubahan itu akan "
                                                            "tertimpa. Konfirmasi untuk tetap menyegarkan.")
        job = _antrekan(sesi, site.id, JobType.staging_tarik, {}, pengguna)
        return {"job_id": job.id, "sandi": sandi, "pengguna": "staging" if sandi else None}


@router.post("/api/sites/{site_id}/staging/jalan")
def jalankan(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    s = get_settings()
    with db.SessionLocal() as sesi:
        st = _staging(sesi, site_id)
        if st.aktif:
            return {"ok": True}
        pb = umum.buat_pembantu()
        try:
            status = pb.status()
            aktif = sesi.scalar(select(func.count()).select_from(Staging).where(Staging.aktif.is_(True))) or 0
            pesan = cek_ram(status) or cek_maks_aktif(aktif, s.staging_maks_aktif)
            if pesan:
                raise HTTPException(status_code=409, detail=pesan)
            pb.jalan(st.nama)
        except GalatPembantu as exc:
            raise _pembantu_gagal(exc) from None
        st.aktif = True
        if st.status == StatusStaging.dijeda:
            st.status = StatusStaging.siap
        umum.catat_aktivitas(sesi, site_id, None, "Staging dijalankan", user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


@router.post("/api/sites/{site_id}/staging/jeda")
def jeda(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        st = _staging(sesi, site_id)
        _tolak_bila_sibuk(sesi, site_id)
        try:
            umum.buat_pembantu().jeda(st.nama)
        except GalatPembantu as exc:
            raise _pembantu_gagal(exc) from None
        st.aktif = False
        if st.status == StatusStaging.siap:
            st.status = StatusStaging.dijeda
        umum.catat_aktivitas(sesi, site_id, None, "Staging dijeda", user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


@router.delete("/api/sites/{site_id}/staging")
def hapus(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    s = get_settings()
    with db.SessionLocal() as sesi:
        st = _staging(sesi, site_id)
        _tolak_bila_sibuk(sesi, site_id)
        pb = umum.buat_pembantu()
        try:
            pb.hapus(st.nama)
            pb.db_hapus(st.nama)
            hapus_akses_router(s.jalur_staging, st.nama)
            pb.router_muat()
        except GalatPembantu as exc:
            raise _pembantu_gagal(exc) from None
        # Snapshot adalah cadangan produksi: dipertahankan sampai dipangkas.
        for nama in ("files", "tarik", "dorong", "ekspor", "log", "indeks.jsonl"):
            p = jalur_di_dalam(s.jalur_staging, f"{site_id}/{nama}")
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        nama = st.nama
        sesi.delete(st)
        umum.catat_aktivitas(sesi, site_id, None, f"Staging dihapus ({nama})", user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


@router.get("/api/sites/{site_id}/staging/tanda-air")
def tanda_air(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        st = _staging(sesi, site_id)
        posts = ((st.tanda_air or {}).get("sumber") or {}).get("posts") or {}
        try:
            baru = urai_tanda_air(buat_klien(site).staging_tanda_air(posts.get("diubah") or None,
                                                                     posts.get("maks_id") or None))
        except SiteError as exc:
            raise HTTPException(status_code=502, detail=f"Tanda air produksi tidak dapat dibaca: "
                                                        f"{bersih_teks(exc.pesan, 200)}") from None
        if baru is None:
            raise HTTPException(status_code=502, detail="Tanda air produksi tidak dapat dibaca.")
        return {"perubahan": bandingkan_tanda_air(st.tanda_air, baru)}


class PermintaanDorong(BaseModel):
    mode: str
    konfirmasi_nama: str | None = None


@router.post("/api/sites/{site_id}/staging/dorong")
def antrekan_dorong(site_id: uuid.UUID, req: PermintaanDorong, pengguna: PenggunaApi):
    _fitur()
    if req.mode not in MODE:
        raise HTTPException(status_code=422, detail="Mode dorong harus hanya_kode atau timpa_penuh.")
    with db.SessionLocal() as sesi:
        st = _staging(sesi, site_id)
        if st.ditarik_pada is None:
            raise HTTPException(status_code=409, detail="Tarik staging dulu sebelum mendorong.")
        job = _antrekan(sesi, site_id, JobType.staging_dorong,
                        {"mode": req.mode, "konfirmasi_nama": (req.konfirmasi_nama or "")[:200] or None}, pengguna)
        return {"job_id": job.id}


class PermintaanKembalikan(BaseModel):
    snapshot_id: int
    konfirmasi_nama: str


@router.post("/api/sites/{site_id}/staging/kembalikan")
def antrekan_kembalikan(site_id: uuid.UUID, req: PermintaanKembalikan, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        snap = sesi.get(StagingSnapshot, req.snapshot_id)
        if snap is None or snap.site_id != site_id or snap.status not in ("tersedia", "dipakai"):
            raise HTTPException(status_code=404, detail="Snapshot tidak ditemukan atau sudah dipangkas.")
        if req.konfirmasi_nama != site.nama:
            raise HTTPException(status_code=422, detail="Ketik nama site persis untuk mengembalikan produksi.")
        job = _antrekan(sesi, site_id, JobType.staging_kembalikan,
                        {"snapshot_id": snap.id, "konfirmasi_nama": req.konfirmasi_nama}, pengguna)
        return {"job_id": job.id}


@router.post("/api/sites/{site_id}/staging/sandi")
def sandi_preview_baru(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    s = get_settings()
    with db.SessionLocal() as sesi:
        st = _staging(sesi, site_id)
        sandi = sandi_baru()
        hash_baru = hash_sandi(sandi)
        if st.ditarik_pada is not None:
            try:
                tulis_akses_router(s.jalur_staging, st.nama, hash_baru, dekripsi_secret(st.rahasia_router_terenkripsi))
                umum.buat_pembantu().router_muat()
            except GalatPembantu as exc:
                raise _pembantu_gagal(exc) from None
        st.sandi_hash = hash_baru
        umum.catat_aktivitas(sesi, site_id, None, "Kata sandi preview dibuat ulang", user_id=pengguna.id)
        sesi.commit()
    return {"sandi": sandi, "pengguna": "staging"}


class PermintaanUji(BaseModel):
    paket: list[dict]
    konfirmasi: bool = False


def _antrekan_uji(sesi, site_id, paket: list[dict], konfirmasi: bool, pengguna: User) -> Job:
    try:
        urai_paket(paket)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    st = _staging(sesi, site_id)
    umum.perbarui_diubah(st)
    sesi.commit()
    if st.diubah_pada and st.ditarik_pada and st.diubah_pada > st.ditarik_pada and not konfirmasi:
        raise HTTPException(status_code=409, detail="Staging diubah sejak tarik terakhir; perubahan itu akan "
                                                    "tertimpa oleh uji. Konfirmasi untuk melanjutkan.")
    return _antrekan(sesi, site_id, JobType.staging_uji_update, {"paket": paket, "konfirmasi": konfirmasi}, pengguna)


@router.post("/api/sites/{site_id}/staging/uji")
def uji_site(site_id: uuid.UUID, req: PermintaanUji, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        _site(sesi, site_id)
        return {"job_id": _antrekan_uji(sesi, site_id, req.paket, req.konfirmasi, pengguna).id}


class ItemUji(BaseModel):
    site_id: uuid.UUID
    tipe: PackageType
    slug: str
    ke_versi: str


class PermintaanUjiBanyak(BaseModel):
    items: list[ItemUji]
    konfirmasi: bool = False


@router.post("/api/staging/uji")
def uji_banyak(req: PermintaanUjiBanyak, pengguna: PenggunaApi):
    _fitur()
    per_site: dict[uuid.UUID, list[dict]] = {}
    with db.SessionLocal() as sesi:
        # Seluruh item divalidasi sebelum satu job pun dibuat (R46 Lapis 1).
        for item in req.items:
            _site(sesi, item.site_id)
            dari = sesi.scalar(select(SitePackage.versi_terpasang).where(
                SitePackage.site_id == item.site_id, SitePackage.tipe == item.tipe, SitePackage.slug == item.slug))
            per_site.setdefault(item.site_id, []).append(
                {"tipe": item.tipe.value, "slug": item.slug, "dari": dari, "ke": item.ke_versi})
        for site_id, paket in per_site.items():
            try:
                urai_paket(paket)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            _staging(sesi, site_id)
            _tolak_bila_sibuk(sesi, site_id)
        ids = [_antrekan_uji(sesi, site_id, paket, req.konfirmasi, pengguna).id for site_id, paket in per_site.items()]
    return {"job_ids": ids}


@router.get("/api/sites/{site_id}/staging/uji")
def daftar_uji(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        _site(sesi, site_id)
        return _daftar_uji(sesi, site_id, BATAS_DAFTAR)


@router.get("/api/sites/{site_id}/staging/snapshot")
def daftar_snapshot(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        _site(sesi, site_id)
        return _daftar_snapshot(sesi, site_id, BATAS_DAFTAR)


@router.post("/api/sites/{site_id}/staging/batal")
def batal(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        st = _staging(sesi, site_id)
        if _job_aktif(sesi, site_id) is None:
            raise HTTPException(status_code=409, detail="Tidak ada pekerjaan staging yang bisa dibatalkan.")
        st.batal_diminta_pada = datetime.now(timezone.utc)
        umum.catat_aktivitas(sesi, site_id, None, "Pembatalan job staging diminta", user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


@router.get("/api/sites/{site_id}/staging/sso")
def sso_staging(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        st = _staging(sesi, site_id)
        if st.ditarik_pada is None:
            raise HTTPException(status_code=409, detail="Staging belum selesai disalin.")
        host = umum.host_staging(st)
        token = buat_token(dekripsi_secret(site.secret_terenkripsi), str(site.id))
        url = f"https://{host}" + tautan_masuk(dekripsi_secret(st.rahasia_router_terenkripsi), host, token, int(time.time()))
        st.dibuka_pada = datetime.now(timezone.utc)
        umum.catat_aktivitas(sesi, site_id, None, "SSO staging dibuka", user_id=pengguna.id)
        sesi.commit()
        return {"url": url}


def _alamat(nilai) -> str:
    if not isinstance(nilai, dict):
        return ""
    return bersih_teks(nilai.get("Address"), 320) or ""


def _mailpit(path: str, params: dict | None = None) -> dict:
    http = umum.buat_http()
    try:
        with http.stream("GET", get_settings().staging_mailpit_url + path, params=params, timeout=10) as r:
            isi = bytearray()
            for potong in r.iter_bytes():
                isi.extend(potong)
                if len(isi) > BATAS_EMAIL_BYTE:
                    raise HTTPException(status_code=502, detail="Balasan Mailpit terlalu besar.")
            status = r.status_code
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Kotak email staging tidak dapat dibaca.") from None
    finally:
        http.close()
    if status == 404:
        raise HTTPException(status_code=404, detail="Email tidak ditemukan.")
    try:
        data = json.loads(bytes(isi))
    except ValueError:
        data = None
    if status != 200 or not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Kotak email staging tidak dapat dibaca.")
    return data


@router.get("/api/sites/{site_id}/staging/email")
def daftar_email(site_id: uuid.UUID, pengguna: PenggunaApi):
    _fitur()
    with db.SessionLocal() as sesi:
        st = _staging(sesi, site_id)
        nama = st.nama
    data = _mailpit("/api/v1/search", {"query": f'tag:"{nama}"', "limit": str(BATAS_DAFTAR)})
    hasil = []
    for m in data.get("messages") if isinstance(data.get("messages"), list) else []:
        if not isinstance(m, dict) or not isinstance(m.get("ID"), str) or not POLA_ID_EMAIL.fullmatch(m["ID"]):
            continue
        if nama not in (m.get("Tags") or []):
            continue
        hasil.append({
            "id": m["ID"], "dari": _alamat(m.get("From")),
            "ke": [_alamat(t) for t in m.get("To") or [] if isinstance(t, dict)][:10],
            "subjek": bersih_teks(m.get("Subject"), 300) or "", "waktu": bersih_teks(m.get("Created"), 40),
            "cuplikan": bersih_teks(m.get("Snippet"), 300) or "",
        })
        if len(hasil) >= BATAS_DAFTAR:
            break
    return hasil


@router.get("/api/sites/{site_id}/staging/email/{msg_id}")
def isi_email(site_id: uuid.UUID, msg_id: str, pengguna: PenggunaApi):
    _fitur()
    if not POLA_ID_EMAIL.fullmatch(msg_id):
        raise HTTPException(status_code=404, detail="Email tidak ditemukan.")
    with db.SessionLocal() as sesi:
        nama = _staging(sesi, site_id).nama
    m = _mailpit(f"/api/v1/message/{msg_id}")
    # Satu Mailpit untuk semua staging: email milik staging lain tidak boleh terbaca lewat site ini.
    if nama not in (m.get("Tags") or []):
        raise HTTPException(status_code=404, detail="Email tidak ditemukan.")
    return {"id": msg_id, "subjek": bersih_teks(m.get("Subject"), 300) or "", "dari": _alamat(m.get("From")),
            "ke": [_alamat(t) for t in m.get("To") or [] if isinstance(t, dict)][:10],
            "waktu": bersih_teks(m.get("Date"), 40), "teks": bersih_teks(m.get("Text"), 200_000) or ""}
```

Di `src/wpmgr/web/app.py`, tambahkan setelah impor router lain:

```python
    from wpmgr.web.routes_staging import router as staging_router
```

dan `app.include_router(staging_router)` sebelum `app.include_router(pages_router)`.

Di `src/wpmgr/web/routes_api.py`:
- tambahkan impor `from wpmgr.models import JOB_STAGING, StagingUji` (gabungkan ke blok impor models yang ada) dan `from wpmgr.web.routes_staging import ringkas_kemajuan` (impor di dalam fungsi `job_aktif` untuk menghindari impor melingkar saat app dibangun);
- di `job_aktif()`, tambahkan field ke dict per job:

```python
                "progres": ringkas_kemajuan(j)["teks"] if j.tipe in JOB_STAGING else None,
```

- di `daftar_paket()`, sebelum `return`, bangun peta hasil uji lalu tambahkan field `uji` ke setiap baris:

```python
        baris = sesi.execute(q).all()
        site_ids = {p.site_id for p, _, _ in baris}
        uji_terbaru: dict[tuple, dict] = {}
        if site_ids:
            for u in sesi.scalars(select(StagingUji).where(StagingUji.site_id.in_(site_ids))
                                  .order_by(StagingUji.dibuat_pada.desc(), StagingUji.id.desc()).limit(500)):
                for pk in u.paket or []:
                    if not isinstance(pk, dict):
                        continue
                    kunci = (u.site_id, pk.get("tipe"), pk.get("slug"), pk.get("ke"))
                    uji_terbaru.setdefault(kunci, {
                        "hasil": u.hasil, "dibuat_pada": _waktu(u.dibuat_pada),
                        "alasan": ((u.pemeriksaan or {}).get("alasan") or [None])[0],
                    })
```

dan di dict per paket:

```python
                "uji": uji_terbaru.get((p.site_id, p.tipe.value, p.slug, p.versi_tersedia)),
```

(ganti `for p, site_nama, client_nama in sesi.execute(q).all()` dengan `for p, site_nama, client_nama in baris`).

- [ ] **Step 4: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/integration/test_api_staging.py tests/integration/test_api.py tests/integration/test_csrf.py -q`. Expected: semua lulus. Lalu seluruh unit, integrasi, dan `ruff check .`.

- [ ] **Step 5: Commit.**

```bash
git add src/wpmgr/web/routes_staging.py src/wpmgr/web/app.py src/wpmgr/web/routes_api.py tests/integration/test_api_staging.py
git commit -m "feat(staging): API staging, SSO lewat tautan bertanda tangan, dan email Mailpit per staging"
```

---

### Task 20: UI — tab Staging, tombol "Uji di staging dulu", chip Kesehatan, strip progres

**Files:**
- Create: `src/wpmgr/templates/_tab_staging.html`, `src/wpmgr/static/app/staging.js`, `tests/integration/test_staging_halaman.py`
- Modify: `src/wpmgr/web/routes_pages.py`, `src/wpmgr/templates/site_detail.html`, `src/wpmgr/static/app/detail.js`, `src/wpmgr/templates/updates.html`, `src/wpmgr/static/app/updates.js`, `src/wpmgr/kesehatan.py`, `src/wpmgr/static/app/kesehatan.js`, `src/wpmgr/static/app/app.css`

**Interfaces:**
- Consumes: Task 19 (API).
- Produces:
  - `TAB_DETAIL` mendapat `("staging", "Staging")` setelah Traffic. Tab hanya tampil bila `WPMGR_STAGING_DOMAIN` diisi; lencana `!` bila staging `gagal` atau `dorong_gagal_pada` terisi.
  - Komponen Alpine `tabStaging(siteId)`. Nama site dibaca dari `data-nama`, tidak pernah disisipkan ke ekspresi. Tab ini menampilkan:
    - status, progres job (polling 3 detik selama ada job), dan tombol Batalkan;
    - alamat preview, pengguna `staging`, dan kata sandi baru sekali tampil dengan tombol salin;
    - ukuran, waktu tarik terakhir, dan status sertifikat ("sedang diterbitkan" bila belum ada);
    - tombol Buat/Segarkan (dengan konfirmasi bila staging diubah), Jeda/Jalankan, Masuk ke admin staging (SSO), Dorong ke produksi, Buat ulang kata sandi, Hapus;
    - dialog dorong: pilihan hanya kode/timpa penuh, tombol "Periksa data baru di produksi", daftar perubahan, dan isian nama site bila timpa penuh punya data baru;
    - kotak email (daftar dan isi teks);
    - riwayat uji dan daftar snapshot dengan Kembalikan (isian nama site).
  - Halaman Update:
    - tombol **Uji di staging dulu** untuk baris terpilih (`POST /api/staging/uji`, dengan dialog konfirmasi bila server meminta);
    - kolom **Uji staging** berisi lencana `Lolos uji`/`Gagal uji` (kelas dari daftar tetap, alasan lewat `esc()`);
    - strip progres menampilkan `progres`.
  - `wpmgr.kesehatan`:
    - `URUTAN_CHIP` menjadi `mati, perlu_diperiksa, dorong_gagal, diserang, error_baru, ssl, koneksi, penangkap_terbatas, staging_gagal, traffic_anjlok, traffic_melonjak, connector_usang`;
    - `TINGKAT_MASALAH["dorong_gagal"] = 1`, `["staging_gagal"] = 2`;
    - `TAB_MASALAH[...] = "staging"`;
    - baris mendapat `staging_status`.
  - `kesehatan.js` dengan label `dorong ke produksi gagal` dan `staging gagal`.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/integration/test_staging_halaman.py`
```python
import uuid
from datetime import datetime, timezone

import pytest

from wpmgr.kesehatan import TAB_MASALAH, TINGKAT_MASALAH, URUTAN_CHIP, susun_kesehatan
from wpmgr.models import Site, SiteStatus, Staging, StatusStaging

pytestmark = pytest.mark.integration


def test_tab_staging_tampil_bila_fitur_aktif(klien_web, sesi, site, staging_aktif):
    site.nama = '<script>alert("x")</script>'
    sesi.commit()
    r = klien_web.get(f"/sites/{site.id}?tab=staging")
    assert r.status_code == 200
    assert "tabStaging(" in r.text
    assert "detailSite('" in r.text and "', 'staging')" in r.text
    assert 'data-nama="&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;"' in r.text
    assert "<script>alert" not in r.text
    assert "/static/app/staging.js" in r.text


def test_tab_staging_tersembunyi_bila_fitur_mati(klien_web, site, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    get_settings.cache_clear()
    r = klien_web.get(f"/sites/{site.id}?tab=staging")
    assert "tabStaging(" not in r.text
    assert "', 'ringkasan')" in r.text


def test_lencana_tab_staging(klien_web, sesi, site_staging):
    site_staging.dorong_gagal_pada = datetime.now(timezone.utc)
    sesi.commit()
    r = klien_web.get(f"/sites/{site_staging.site_id}")
    awal = r.text.index("Staging")
    assert '<span class="lencana">!</span>' in r.text[awal:awal + 200]


def test_halaman_update_punya_tombol_uji(klien_web):
    r = klien_web.get("/updates")
    assert "Uji di staging dulu" in r.text


def test_chip_kesehatan_staging(sesi, staging_aktif):
    assert URUTAN_CHIP.index("dorong_gagal") < URUTAN_CHIP.index("diserang")
    assert (TINGKAT_MASALAH["dorong_gagal"], TINGKAT_MASALAH["staging_gagal"]) == (1, 2)
    assert TAB_MASALAH["dorong_gagal"] == TAB_MASALAH["staging_gagal"] == "staging"
    sites = {}
    for nama in ("Gagal", "Dorong", "Sehat"):
        s = Site(id=uuid.uuid4(), nama=nama, url=f"https://{nama.lower()}.test", status=SiteStatus.active,
                 secret_terenkripsi=b"x", fitur=["self_update", "events", "traffic"], connector_version="3.0.0")
        sesi.add(s)
        sites[nama] = s
    sesi.flush()
    sesi.add_all([
        Staging(site_id=sites["Gagal"].id, nama="gagal", status=StatusStaging.gagal),
        Staging(site_id=sites["Dorong"].id, nama="dorong", status=StatusStaging.siap,
                dorong_gagal_pada=datetime.now(timezone.utc)),
        Staging(site_id=sites["Sehat"].id, nama="sehat", status=StatusStaging.siap),
    ])
    sesi.commit()
    hasil = susun_kesehatan(sesi)
    per_nama = {b["nama"]: b for b in hasil["baris"]}
    assert per_nama["Dorong"]["masalah"] == ["dorong_gagal"]
    assert (per_nama["Dorong"]["tingkat"], per_nama["Dorong"]["tab"]) == (1, "staging")
    assert per_nama["Gagal"]["masalah"] == ["staging_gagal"]
    assert per_nama["Gagal"]["tingkat"] == 2
    assert per_nama["Sehat"]["staging_status"] == "siap"
    assert hasil["chip"]["dorong_gagal"] == 1 and hasil["chip"]["staging_gagal"] == 1
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_staging_halaman.py -q`. Expected: gagal (`KeyError: 'dorong_gagal'`, `tabStaging(` tidak ada).

- [ ] **Step 3: Halaman detail.** Di `src/wpmgr/web/routes_pages.py`:
- tambahkan `Staging, StatusStaging` ke impor dari `wpmgr.models`;
- ubah `TAB_DETAIL` menjadi:

```python
TAB_DETAIL = [
    ("ringkasan", "Ringkasan"), ("paket", "Paket"), ("uptime", "Uptime"),
    ("error", "Error"), ("login", "Login"), ("traffic", "Traffic"), ("staging", "Staging"),
    ("aktivitas", "Aktivitas"),
]
```

Di `halaman_detail`, ganti dua baris pertama badan fungsi dengan:

```python
    staging_aktif = get_settings().staging_aktif
    tab_detail = [t for t in TAB_DETAIL if t[0] != "staging" or staging_aktif]
    sah = {k for k, _ in tab_detail}
    # Hanya nilai dari daftar putih yang boleh masuk ke ekspresi Alpine di template.
    tab = tab if tab in sah else "ringkasan"
```

di dalam blok `with db.SessionLocal() as sesi:`, setelah `anomali = ...`:

```python
        staging = sesi.scalar(select(Staging).where(Staging.site_id == site_id)) if staging_aktif else None
```

tambahkan ke `lencana`:

```python
        "staging": "!" if staging is not None and (
            staging.status == StatusStaging.gagal or staging.dorong_gagal_pada is not None) else "",
```

dan ganti `"tab_detail": TAB_DETAIL` di konteks template dengan `"tab_detail": tab_detail, "staging_aktif": staging_aktif`.

Di `src/wpmgr/templates/site_detail.html`, tambahkan sebelum `<section x-show="tab === 'aktivitas'">`:

```html
  {% if staging_aktif %}{% include "_tab_staging.html" %}{% endif %}
```

dan setelah `<script src="/static/app/detail.js"></script>`:

```html
{% if staging_aktif %}<script src="/static/app/staging.js"></script>{% endif %}
```

Di `src/wpmgr/static/app/detail.js`, ubah baris pertama menjadi:

```javascript
const TAB_SAH = ['ringkasan', 'paket', 'uptime', 'error', 'login', 'traffic', 'staging', 'aktivitas'];
```

- [ ] **Step 4: Template tab.**

File: `src/wpmgr/templates/_tab_staging.html`
```html
{# Tab Staging. Semua nilai dari site dan connector dirender lewat x-text;
   nama site dibaca dari data-nama (autoescape Jinja), bukan disisipkan ke
   ekspresi Alpine. #}
<section x-show="tab === 'staging'">
  <div x-data="tabStaging('{{ site.id }}')" x-init="mulai($el.dataset.nama)" data-nama="{{ site.nama }}">
    <p class="galat" x-show="galat" x-text="galat" role="alert"></p>
    <p class="info" x-show="info" x-text="info" role="status"></p>

    <template x-if="data && !data.izin_connector">
      <p class="galat">Connector site ini belum mengizinkan staging. Aktifkan <em>Izinkan staging</em> di
         <em>Pengaturan → WP Manager</em> di wp-admin site ini (connector 3.0).</p>
    </template>

    <template x-if="sandi">
      <div class="panel">
        <p><strong>Kata sandi preview</strong> (hanya ditampilkan sekali): pengguna <code>staging</code>,
           kata sandi <code x-text="sandi"></code>
           <button type="button" @click="salin(sandi)">Salin</button></p>
      </div>
    </template>

    <template x-if="data && data.job">
      <div class="panel">
        <p><strong x-text="teksJob(data.job.tipe)"></strong> — <span x-text="data.job.progres.teks"></span></p>
        <div class="batang-progres"><div :style="`width:${data.job.progres.persen || 0}%`"></div></div>
        <button type="button" @click="batal()" :disabled="data.staging && data.staging.batal_diminta">Batalkan</button>
      </div>
    </template>

    <template x-if="data && !data.staging">
      <div>
        <p>Site ini belum punya staging.</p>
        <button type="button" @click="buatAtauSegarkan(false)" :disabled="!data.izin_connector || !!data.job">Buat staging</button>
      </div>
    </template>

    <template x-if="data && data.staging">
      <div>
        <p>Status: <strong x-text="data.staging.status_teks"></strong>
           <span x-show="data.staging.galat" class="galat" x-text="'— ' + data.staging.galat"></span></p>
        <p class="galat" x-show="data.staging.dorong_gagal_pada">
          Halaman utama produksi bermasalah setelah dorongan terakhir. Periksa site, lalu gunakan
          <strong>Kembalikan</strong> pada snapshot terbaru bila perlu.</p>
        <p>Preview: <a :href="aman(data.staging.url)" target="_blank" rel="noopener noreferrer" x-text="data.staging.url"></a>
           · pengguna <code>staging</code>
           · <span x-text="data.staging.sertifikat_pada ? 'HTTPS aktif' : 'sertifikat sedang diterbitkan'"></span></p>
        <p class="redup">Berkas <span x-text="data.staging.ukuran_file_teks"></span> · database
           <span x-text="data.staging.ukuran_db_teks"></span> · PHP <span x-text="data.staging.versi_php || '—'"></span>
           · tarik terakhir <span x-text="waktu(data.staging.ditarik_pada)"></span>
           <span x-show="data.staging.diubah_sejak_tarik">· diubah sesudahnya</span></p>
        <div class="toolbar">
          <button type="button" @click="buatAtauSegarkan(data.staging.diubah_sejak_tarik)" :disabled="!!data.job">Segarkan dari produksi</button>
          <button type="button" x-show="data.staging.aktif" @click="aksi('jeda', 'Staging dijeda.')" :disabled="!!data.job">Jeda</button>
          <button type="button" x-show="!data.staging.aktif" @click="aksi('jalan', 'Staging dijalankan.')">Jalankan</button>
          <button type="button" @click="sso()" :disabled="!data.staging.ditarik_pada || !data.staging.aktif">Masuk admin staging</button>
          <button type="button" @click="dialogDorong = true" :disabled="!!data.job || !data.staging.ditarik_pada">Dorong ke produksi</button>
          <button type="button" @click="sandiBaru()">Buat ulang kata sandi</button>
          <button type="button" @click="hapus()" :disabled="!!data.job">Hapus staging</button>
        </div>

        <div class="panel" x-show="dialogDorong">
          <h3>Dorong ke produksi</h3>
          <p><label><input type="radio" value="hanya_kode" x-model="mode"> Hanya kode — tema, plugin, mu-plugin, dan
             berkas upload baru. Database produksi tidak disentuh (aman untuk toko online).</label></p>
          <p><label><input type="radio" value="timpa_penuh" x-model="mode"> Timpa penuh — semua berkas dan database
             staging menggantikan produksi.</label></p>
          <p class="redup">Snapshot produksi selalu dibuat sebelum dorongan dan bisa dikembalikan.</p>
          <button type="button" @click="cekTandaAir()">Periksa data baru di produksi</button>
          <template x-if="perubahan !== null">
            <div>
              <p x-show="perubahan.length === 0">Tidak ada data baru di produksi sejak staging ditarik.</p>
              <ul x-show="perubahan.length"><template x-for="p in perubahan" :key="p"><li x-text="p"></li></template></ul>
            </div>
          </template>
          <p x-show="mode === 'timpa_penuh'">
            <label>Ketik nama site untuk menimpa data baru di produksi:
              <input x-model="konfirmasiNama" autocomplete="off"></label></p>
          <button type="button" @click="dorong()">Dorong</button>
          <button type="button" @click="dialogDorong = false">Batal</button>
        </div>

        <h3>Email staging</h3>
        <button type="button" @click="muatEmail()">Muat email</button>
        <p class="redup" x-show="email && email.length === 0">Belum ada email yang ditangkap.</p>
        <table x-show="email && email.length">
          <tr><th>Waktu</th><th>Dari</th><th>Ke</th><th>Subjek</th><th></th></tr>
          <template x-for="m in email || []" :key="m.id">
            <tr><td x-text="waktu(m.waktu)"></td><td x-text="m.dari"></td><td x-text="m.ke.join(', ')"></td>
                <td x-text="m.subjek"></td><td><button type="button" @click="bukaEmail(m.id)">Buka</button></td></tr>
          </template>
        </table>
        <template x-if="emailTerbuka">
          <div class="panel"><h4 x-text="emailTerbuka.subjek"></h4><pre class="pesan-detail" x-text="emailTerbuka.teks"></pre></div>
        </template>
      </div>
    </template>

    <h3>Uji update</h3>
    <p class="redup">Jalankan dari halaman <a href="/updates">Update</a> dengan tombol <em>Uji di staging dulu</em>.
       Penilaian bersifat heuristik; keputusan update produksi tetap di tangan Anda.</p>
    <p class="redup" x-show="data && data.uji.length === 0">Belum ada uji.</p>
    <table x-show="data && data.uji.length">
      <tr><th>Waktu</th><th>Paket</th><th>Hasil</th><th>Alasan</th></tr>
      <template x-for="u in (data ? data.uji : [])" :key="u.id">
        <tr>
          <td x-text="waktu(u.dibuat_pada)"></td>
          <td x-text="u.paket.map((p) => `${p.slug} ${p.dari || '?'} → ${p.ke}`).join(', ')"></td>
          <td><span :class="u.hasil === 'lolos' ? 'dg-badge-success' : 'dg-badge-danger'" x-text="u.hasil === 'lolos' ? 'Lolos' : 'Gagal'"></span></td>
          <td x-text="u.alasan.join('; ') || '—'"></td>
        </tr>
      </template>
    </table>

    <h3>Snapshot produksi</h3>
    <p class="redup" x-show="data && data.snapshot.length === 0">Belum ada snapshot.</p>
    <table x-show="data && data.snapshot.length">
      <tr><th>Waktu</th><th>Mode dorong</th><th>Ukuran</th><th>Status</th><th></th></tr>
      <template x-for="s in (data ? data.snapshot : [])" :key="s.id">
        <tr>
          <td x-text="waktu(s.dibuat_pada)"></td>
          <td x-text="s.mode === 'timpa_penuh' ? 'timpa penuh (berkas + database)' : 'hanya kode (berkas saja)'"></td>
          <td x-text="s.ukuran_teks"></td><td x-text="s.status"></td>
          <td><button type="button" x-show="s.status !== 'dipangkas'" @click="kembalikan(s.id, s.mode)" :disabled="!!(data && data.job)">Kembalikan</button></td>
        </tr>
      </template>
    </table>
  </div>
</section>
```

- [ ] **Step 5: Komponen Alpine.**

File: `src/wpmgr/static/app/staging.js`
```javascript
const TEKS_JOB_STAGING = {
  staging_tarik: 'Menyalin dari produksi', staging_uji_update: 'Uji update di staging',
  staging_dorong: 'Mendorong ke produksi', staging_kembalikan: 'Mengembalikan produksi dari snapshot',
};

function tabStaging(siteId) {
  return {
    siteId,
    namaSite: '',
    data: null,
    galat: '',
    info: '',
    sandi: '',
    dialogDorong: false,
    mode: 'hanya_kode',
    perubahan: null,
    konfirmasiNama: '',
    email: null,
    emailTerbuka: null,
    _timer: null,

    mulai(nama) {
      this.namaSite = nama || '';
      this.muat();
    },

    dasar() { return `/api/sites/${this.siteId}/staging`; },

    async muat() {
      try {
        const r = await fetch(this.dasar());
        if (!r.ok) throw new Error(await pesanGalat(r));
        this.data = await r.json();
      } catch (e) {
        this.galat = `Data staging tidak dapat dimuat. ${e.message}`;
        return;
      }
      clearTimeout(this._timer);
      if (this.data.job) this._timer = setTimeout(() => this.muat(), 3000);
    },

    async kirim(method, url, body) {
      const r = await fetch(url, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) {
        const e = new Error(await pesanGalat(r));
        e.status = r.status;
        throw e;
      }
      return r.json();
    },

    async buatAtauSegarkan(perluKonfirmasi) {
      this.galat = '';
      this.info = '';
      if (perluKonfirmasi && !window.confirm('Staging diubah sejak tarik terakhir. Perubahan di staging akan tertimpa. Lanjutkan?')) return;
      try {
        const d = await this.kirim('POST', this.dasar(), { konfirmasi: !!perluKonfirmasi });
        if (d.sandi) this.sandi = d.sandi;
        this.info = 'Penyalinan dari produksi diantrekan.';
      } catch (e) {
        this.galat = `Staging tidak dapat dibuat atau disegarkan. ${e.message}`;
      }
      this.muat();
    },

    async aksi(nama, sukses) {
      this.galat = '';
      try {
        await this.kirim('POST', `${this.dasar()}/${nama}`);
        this.info = sukses;
      } catch (e) {
        this.galat = e.message;
      }
      this.muat();
    },

    async batal() {
      await this.aksi('batal', 'Pembatalan diminta; job berhenti di antara potongan.');
    },

    async sso() {
      try {
        const d = await this.kirim('GET', `${this.dasar()}/sso`);
        window.open(d.url, '_blank', 'noopener');
      } catch (e) {
        this.galat = `SSO staging gagal. ${e.message}`;
      }
    },

    async sandiBaru() {
      if (!window.confirm('Kata sandi preview lama langsung tidak berlaku. Lanjutkan?')) return;
      try {
        this.sandi = (await this.kirim('POST', `${this.dasar()}/sandi`)).sandi;
      } catch (e) {
        this.galat = `Kata sandi tidak dapat dibuat ulang. ${e.message}`;
      }
    },

    async hapus() {
      if (!window.confirm('Hapus staging ini? Snapshot produksi tetap disimpan.')) return;
      try {
        await this.kirim('DELETE', this.dasar());
        this.info = 'Staging dihapus.';
      } catch (e) {
        this.galat = `Staging tidak dihapus. ${e.message}`;
      }
      this.muat();
    },

    async cekTandaAir() {
      this.perubahan = null;
      try {
        this.perubahan = (await this.kirim('GET', `${this.dasar()}/tanda-air`)).perubahan;
      } catch (e) {
        this.galat = e.message;
      }
    },

    async dorong() {
      this.galat = '';
      try {
        await this.kirim('POST', `${this.dasar()}/dorong`,
          { mode: this.mode, konfirmasi_nama: this.mode === 'timpa_penuh' ? this.konfirmasiNama : null });
        this.dialogDorong = false;
        this.info = 'Dorongan diantrekan. Snapshot produksi dibuat lebih dulu.';
      } catch (e) {
        this.galat = `Dorongan tidak diantrekan. ${e.message}`;
      }
      this.muat();
    },

    async kembalikan(id, mode) {
      const catatan = mode === 'timpa_penuh'
        ? 'Berkas dan database produksi akan dikembalikan ke kondisi sebelum dorongan.'
        : 'Hanya berkas yang dikembalikan; database tidak disentuh.';
      const nama = window.prompt(`${catatan}\nKetik nama site untuk melanjutkan:`);
      if (nama === null) return;
      try {
        await this.kirim('POST', `${this.dasar()}/kembalikan`, { snapshot_id: id, konfirmasi_nama: nama });
        this.info = 'Pengembalian diantrekan.';
      } catch (e) {
        this.galat = `Pengembalian tidak diantrekan. ${e.message}`;
      }
      this.muat();
    },

    async muatEmail() {
      try {
        this.email = await this.kirim('GET', `${this.dasar()}/email`);
      } catch (e) {
        this.galat = e.message;
      }
    },

    async bukaEmail(id) {
      try {
        this.emailTerbuka = await this.kirim('GET', `${this.dasar()}/email/${encodeURIComponent(id)}`);
      } catch (e) {
        this.galat = e.message;
      }
    },

    async salin(teks) {
      try {
        await navigator.clipboard.writeText(teks);
        this.info = 'Kata sandi disalin.';
      } catch (e) {
        this.galat = 'Salin manual: clipboard tidak diizinkan browser.';
      }
    },

    aman(url) { return typeof url === 'string' && url.startsWith('https://') ? url : '#'; },
    teksJob(tipe) { return TEKS_JOB_STAGING[tipe] || tipe; },
    waktu(iso) { return iso ? new Date(iso).toLocaleString('id-ID') : '—'; },
  };
}
```

Tambahkan ke akhir `src/wpmgr/static/app/app.css`:

```css
.batang-progres{height:.5rem;background:#eef0f3;border-radius:.25rem;overflow:hidden;margin:.4rem 0}
.batang-progres div{height:100%;background:#5b8def}
```

- [ ] **Step 6: Halaman Update.** Di `src/wpmgr/templates/updates.html`, tambahkan tombol setelah tombol "Update ... item terpilih":

```html
    <button @click="ujiStaging()" :disabled="terpilih.length === 0 || berjalan">Uji di staging dulu</button>
```

tambahkan `<p class="info" x-show="info" x-text="info" role="status"></p>` setelah paragraf galat, dan ganti isi `<div>` di dalam `template x-for="j in progres"` dengan:

```html
      <div>
        <span x-text="j.site_nama"></span> —
        <span x-text="j.slug || j.tipe"></span> —
        <span x-text="j.status"></span>
        <span x-show="j.progres" x-text="' — ' + j.progres"></span>
      </div>
```

Di `src/wpmgr/static/app/updates.js`, tambahkan properti `info: '',` setelah `galat: '',`, tambahkan kolom setelah kolom `versi_tersedia`:

```javascript
          {
            dataField: 'uji',
            caption: 'Uji staging',
            // Kelas dari dua string tetap; alasan berasal dari hasil uji (teks
            // halaman staging yang bisa dikendalikan site) sehingga lewat esc().
            cellTemplate: (nilai) => (nilai
              ? `<span class="${nilai.hasil === 'lolos' ? 'dg-badge-success' : 'dg-badge-danger'}" title="${esc(nilai.alasan || '')}">${nilai.hasil === 'lolos' ? 'Lolos uji' : 'Gagal uji'}</span>`
              : ''),
          },
```

dan metode:

```javascript
    async ujiStaging(konfirmasi = false) {
      if (!this.terpilih.length) return;
      this.galat = '';
      this.info = '';
      const items = this.terpilih.map((b) => ({
        site_id: b.site_id, tipe: b.tipe, slug: b.slug, ke_versi: b.versi_tersedia,
      }));
      let respons;
      try {
        respons = await fetch('/api/staging/uji', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items, konfirmasi }),
        });
      } catch (e) {
        this.galat = `Gagal menghubungi server: ${e.message}`;
        return;
      }
      if (respons.status === 409 && !konfirmasi) {
        const pesan = await pesanGalat(respons);
        if (pesan.includes('Konfirmasi') && window.confirm(pesan)) {
          await this.ujiStaging(true);
          return;
        }
        this.galat = `Uji tidak dijalankan. ${pesan}`;
        return;
      }
      if (!respons.ok) {
        this.galat = `Uji tidak dijalankan. ${await pesanGalat(respons)}`;
        return;
      }
      this.info = 'Uji di staging diantrekan. Hasilnya tampil di kolom Uji staging setelah selesai.';
      this.berjalan = true;
      this.pantau();
    },
```

- [ ] **Step 7: Kesehatan.** Di `src/wpmgr/kesehatan.py`, ganti impor models menjadi:

```python
from wpmgr.models import (
    CatatanError,
    Site,
    SiteStatus,
    Staging,
    StatusStaging,
    TrafficHarian,
    UptimeStatus,
)
```

lalu ganti ketiga konstanta:

```python
URUTAN_CHIP = [
    "mati", "perlu_diperiksa", "dorong_gagal", "diserang", "error_baru", "ssl", "koneksi",
    "penangkap_terbatas", "staging_gagal", "traffic_anjlok", "traffic_melonjak", "connector_usang",
]
TINGKAT_MASALAH = {
    "mati": 1, "perlu_diperiksa": 1, "dorong_gagal": 1,
    "diserang": 2, "error_baru": 2, "ssl": 2, "koneksi": 2, "penangkap_terbatas": 2, "staging_gagal": 2,
    "traffic_anjlok": 3, "traffic_melonjak": 3, "connector_usang": 3,
}
TAB_MASALAH = {
    "mati": "uptime", "perlu_diperiksa": "login", "dorong_gagal": "staging", "diserang": "login",
    "error_baru": "error", "ssl": "uptime", "koneksi": "ringkasan", "penangkap_terbatas": "ringkasan",
    "staging_gagal": "staging", "traffic_anjlok": "traffic", "traffic_melonjak": "traffic",
    "connector_usang": "ringkasan",
}
```

Di `susun_kesehatan`, setelah `traffic = _traffic_kemarin(...)`:

```python
    stagings = {st.site_id: st for st in sesi.scalars(select(Staging).where(Staging.site_id.in_(site_ids)))} \
        if site_ids else {}
```

di dalam loop, setelah cabang `connector_usang`:

```python
        st = stagings.get(site.id)
        if st is not None and st.dorong_gagal_pada is not None:
            masalah.append("dorong_gagal")
        if st is not None and st.status == StatusStaging.gagal:
            masalah.append("staging_gagal")
```

dan di dict baris: `"staging_status": st.status.value if st is not None else None,`.

Di `src/wpmgr/static/app/kesehatan.js`, ganti tiga konstanta pertama:

```javascript
const URUTAN_CHIP = [
  'mati', 'perlu_diperiksa', 'dorong_gagal', 'diserang', 'error_baru', 'ssl', 'koneksi',
  'penangkap_terbatas', 'staging_gagal', 'traffic_anjlok', 'traffic_melonjak', 'connector_usang',
];
const LABEL_CHIP = {
  mati: 'mati', perlu_diperiksa: 'perlu diperiksa', dorong_gagal: 'dorong ke produksi gagal', diserang: 'diserang',
  error_baru: 'error baru', ssl: 'SSL bermasalah', koneksi: 'koneksi bermasalah',
  penangkap_terbatas: 'penangkap terbatas', staging_gagal: 'staging gagal', traffic_anjlok: 'traffic anjlok',
  traffic_melonjak: 'traffic melonjak', connector_usang: 'connector usang',
};
const TINGKAT_CHIP = {
  mati: 1, perlu_diperiksa: 1, dorong_gagal: 1, diserang: 2, error_baru: 2, ssl: 2, koneksi: 2,
  penangkap_terbatas: 2, staging_gagal: 2, traffic_anjlok: 3, traffic_melonjak: 3, connector_usang: 3,
};
```

- [ ] **Step 8: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/integration/test_staging_halaman.py tests/integration/test_kesehatan.py tests/integration/test_pages.py tests/unit/test_template_aman.py -q`. Expected: semua lulus. `test_template_aman.py` tetap hijau karena tidak ada `x-html`, `|safe`, atau `innerHTML`.

- [ ] **Step 9: Periksa di browser.** Jalankan web lokal dengan `WPMGR_STAGING_DOMAIN=staging.test` (lihat README "Menjalankan web"), buka detail site → tab Staging, lalu halaman Update dan Kesehatan. Expected: tab tampil tanpa galat konsol, tombol Buat staging nonaktif untuk site yang connector-nya belum mengizinkan, dan chip baru muncul untuk data uji.

- [ ] **Step 10: Commit.**

```bash
git add src/wpmgr/web/routes_pages.py src/wpmgr/templates/site_detail.html src/wpmgr/templates/_tab_staging.html src/wpmgr/templates/updates.html src/wpmgr/static/app/staging.js src/wpmgr/static/app/detail.js src/wpmgr/static/app/updates.js src/wpmgr/static/app/kesehatan.js src/wpmgr/static/app/app.css src/wpmgr/kesehatan.py tests/integration/test_staging_halaman.py
git commit -m "feat(staging): tab Staging, uji dari halaman Update, chip Kesehatan, dan strip progres"
```

---

## Fase F — Deploy dan e2e

### Task 21: Berkas deploy staging (nginx host, sudoers, systemd) dan README

**Files:**
- Create:
  - konfigurasi host: `deploy/staging/nginx-wpmgr-staging.conf`, `deploy/staging/sudoers-wpmgr-staging`, `deploy/staging/wpmgr-staging-siapkan.service`;
  - test: `tests/unit/test_deploy_staging.py`.
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 10 (skrip, `staging.conf.contoh`), Task 13 (instans worker `staging`), Task 18 (crontab).
- Produces:
  - `nginx-wpmgr-staging.conf`:
    - `map $ssl_server_name` → host staging tervalidasi (Koreksi #9);
    - server 80 dengan `/.well-known/acme-challenge/` → `/var/lib/wpmgr/acme` dan redirect 301 selain itu;
    - server 443 `ssl http2` dengan `ssl_certificate /var/lib/wpmgr/certs/$wpmgr_stg_host/fullchain.pem`, `include /etc/letsencrypt/options-ssl-nginx.conf`, `client_max_body_size 64M`, `proxy_pass http://127.0.0.1:8090`.
  - `sudoers-wpmgr-staging`: `wpmgr ALL=(root) NOPASSWD: /usr/local/sbin/wpmgr-staging` plus `env_reset`.
  - `wpmgr-staging-siapkan.service`: oneshot `wpmgr-staging siapkan` saat boot, setelah Docker. Aturan iptables tidak bertahan setelah reboot.
  - README bagian **Staging (Lapis 3)** berisi langkah pemasangan spec §15, variabel §10, cron §11, worker `wpmgr-worker@staging`, verifikasi, dan keterbatasan.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/unit/test_deploy_staging.py`
```python
import re
from pathlib import Path

AKAR = Path(__file__).resolve().parents[2]
STAGING = AKAR / "deploy" / "staging"


def _teks(nama: str) -> str:
    return (STAGING / nama).read_text(encoding="utf-8")


def test_nginx_host_memuat_direktif_wajib():
    t = _teks("nginx-wpmgr-staging.conf")
    for wajib in (
        "map $ssl_server_name $wpmgr_stg_host {",
        r"~^(?<wpmgr_stg_nama>[a-z0-9-]{1,40})\.staging\.halosocia\.my\.id$",
        "listen 80;",
        "listen 443 ssl http2;",
        "location ^~ /.well-known/acme-challenge/ {",
        "root /var/lib/wpmgr/acme;",
        "return 301 https://$host$request_uri;",
        "ssl_certificate     /var/lib/wpmgr/certs/$wpmgr_stg_host/fullchain.pem;",
        "ssl_certificate_key /var/lib/wpmgr/certs/$wpmgr_stg_host/privkey.pem;",
        "include /etc/letsencrypt/options-ssl-nginx.conf;",
        "client_max_body_size 64M;",
        "proxy_pass http://127.0.0.1:8090;",
        "proxy_set_header X-Forwarded-Proto https;",
    ):
        assert wajib in t, wajib
    # SNI mentah tidak boleh menjadi bagian path berkas yang dibuka root.
    assert "certs/$ssl_server_name" not in t
    assert "default_server" not in t


def test_sudoers_hanya_untuk_skrip_pembantu():
    baris = [b for b in _teks("sudoers-wpmgr-staging").splitlines() if b and not b.startswith("#")]
    assert baris == [
        "Defaults!/usr/local/sbin/wpmgr-staging env_reset",
        "wpmgr ALL=(root) NOPASSWD: /usr/local/sbin/wpmgr-staging",
    ]


def test_unit_siapkan_setelah_docker():
    t = _teks("wpmgr-staging-siapkan.service")
    assert "After=docker.service" in t and "Requires=docker.service" in t
    assert "ExecStart=/usr/local/sbin/wpmgr-staging siapkan" in t
    assert "Type=oneshot" in t


def test_contoh_konfigurasi_hanya_kunci_yang_dikenal_skrip():
    skrip = _teks("wpmgr-staging")
    dikenal = set(re.search(r"case \"\$kunci\" in\n\s+(.+?)\)", skrip, re.DOTALL).group(1)
                  .replace("\\\n", "").replace(" ", "").split("|"))
    for baris in _teks("staging.conf.contoh").splitlines():
        if baris and not baris.startswith("#"):
            assert baris.split("=", 1)[0] in dikenal, baris


def test_crontab_dan_worker_staging():
    crontab = (AKAR / "deploy" / "crontab").read_text(encoding="utf-8")
    for perintah in ("staging-jeda-otomatis", "renew-staging-certs", "prune-staging"):
        assert f"wpmgr.cli {perintah}" in crontab
    assert "Environment=WPMGR_WORKER_INSTANS=%i" in (AKAR / "deploy" / "wpmgr-worker@.service").read_text(encoding="utf-8")


def test_readme_menjelaskan_pemasangan():
    readme = (AKAR / "README.md").read_text(encoding="utf-8")
    for wajib in ("## Staging (Lapis 3)", "wpmgr-worker@staging", "/etc/sudoers.d/wpmgr-staging",
                  "visudo -cf", "wpmgr-staging siapkan", "/etc/nginx/sites-enabled/wpmgr-staging.conf",
                  "WPMGR_STAGING_DOMAIN", "Izinkan staging", "digest.lock"):
        assert wajib in readme, wajib
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_deploy_staging.py -q`. Expected: `FileNotFoundError` untuk `nginx-wpmgr-staging.conf`.

- [ ] **Step 3: Berkas deploy.**

File: `deploy/staging/nginx-wpmgr-staging.conf`
```nginx
# /etc/nginx/sites-enabled/wpmgr-staging.conf -- dipasang SEKALI (spec §7.2).
# Semua <nama>.staging.halosocia.my.id diteruskan ke router staging di
# 127.0.0.1:8090. Sertifikat dibaca per handshake dari
# /var/lib/wpmgr/certs/<host>/, jadi staging baru tidak butuh reload nginx
# host. Selama sertifikat sebuah staging belum terbit, handshake untuk nama
# itu ditolak; staging tetap bisa dibuka lewat SSO dashboard setelah terbit.
#
# Nama dari SNI divalidasi lewat map sebelum menjadi bagian path: SNI dikirim
# klien dan bisa berisi apa saja, termasuk "../".

map $ssl_server_name $wpmgr_stg_host {
    ~^(?<wpmgr_stg_nama>[a-z0-9-]{1,40})\.staging\.halosocia\.my\.id$  $wpmgr_stg_nama.staging.halosocia.my.id;
    default                                                            tidak-ada;
}

server {
    listen 80;
    listen [::]:80;
    server_name ~^[a-z0-9-]{1,40}\.staging\.halosocia\.my\.id$;

    # HTTP-01 untuk certbot --webroot milik skrip pembantu.
    location ^~ /.well-known/acme-challenge/ {
        root /var/lib/wpmgr/acme;
        default_type text/plain;
        try_files $uri =404;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ~^[a-z0-9-]{1,40}\.staging\.halosocia\.my\.id$;

    ssl_certificate     /var/lib/wpmgr/certs/$wpmgr_stg_host/fullchain.pem;
    ssl_certificate_key /var/lib/wpmgr/certs/$wpmgr_stg_host/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;

    client_max_body_size 64M;
    add_header X-Robots-Tag "noindex, nofollow" always;

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

File: `deploy/staging/sudoers-wpmgr-staging`
```
# /etc/sudoers.d/wpmgr-staging -- root:root 0440.
# Periksa sebelum dipakai: visudo -cf /etc/sudoers.d/wpmgr-staging
# User dashboard hanya boleh menjalankan skrip pembantu; skrip itu sendiri
# memvalidasi setiap subperintah dan argumen (spec §7.3).
Defaults!/usr/local/sbin/wpmgr-staging env_reset
wpmgr ALL=(root) NOPASSWD: /usr/local/sbin/wpmgr-staging
```

File: `deploy/staging/wpmgr-staging-siapkan.service`
```ini
# Aturan iptables isolasi staging tidak bertahan setelah reboot; unit ini
# menjalankan `wpmgr-staging siapkan` (idempoten) setiap boot setelah Docker.
[Unit]
Description=Siapkan jaringan, isolasi, dan layanan staging WP Manager
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/wpmgr-staging siapkan
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: README.** Sisipkan bagian berikut ke `README.md` tepat sebelum `## Keterbatasan yang diketahui`:

````markdown
## Staging (Lapis 3)

Setiap site bisa punya satu salinan staging di VPS dashboard, untuk menguji update, preview client,
bekerja, dan mendorong hasil ke produksi dengan snapshot yang bisa dikembalikan. Staging berjalan di
Docker, tetapi proses dashboard **tidak** punya akses Docker. Semua lewat satu skrip root
`/usr/local/sbin/wpmgr-staging` yang dipanggil `sudo -n`. Fitur ini mati selama `WPMGR_STAGING_DOMAIN`
kosong.

**Prasyarat VPS:** Docker (butuh sudo), nginx host, certbot 2.9, `setpriv` (paket util-linux), dan
wildcard DNS `*.staging.<domain>` yang mengarah ke IP VPS.

**Pemasangan (sekali, sebagai root, dari `/opt/wpmgr`):**

```bash
# 1. Skema, paket connector 3.0, lalu "Perbarui connector" di halaman Site
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m wpmgr.cli build-connector

# 2. Skrip pembantu, konfigurasinya, dan sudoers
install -o root -g root -m 0755 deploy/staging/wpmgr-staging /usr/local/sbin/wpmgr-staging
install -d -o root -g root -m 0755 /etc/wpmgr-staging
install -o root -g root -m 0644 deploy/staging/staging.conf.contoh /etc/wpmgr-staging/staging.conf
#    isi DOMAIN dan ACME_EMAIL (sama dengan WPMGR_STAGING_EMAIL_ACME) di staging.conf
install -d -o wpmgr -g wpmgr -m 0700 /var/lib/wpmgr/staging
install -o root -g root -m 0440 deploy/staging/sudoers-wpmgr-staging /etc/sudoers.d/wpmgr-staging
visudo -cf /etc/sudoers.d/wpmgr-staging

# 3. Jaringan, isolasi iptables, MariaDB, Mailpit, router, wp-cli (idempoten)
wpmgr-staging siapkan
cp deploy/staging/wpmgr-staging-siapkan.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable wpmgr-staging-siapkan

# 4. nginx host (SEKALI; staging baru tidak butuh reload)
cp deploy/staging/nginx-wpmgr-staging.conf /etc/nginx/sites-enabled/wpmgr-staging.conf
nginx -t && systemctl reload nginx

# 5. Variabel .env, cron, dan worker khusus staging
crontab -u wpmgr deploy/crontab
cp deploy/wpmgr-worker@.service /etc/systemd/system/ && systemctl daemon-reload
systemctl enable --now wpmgr-worker@staging
systemctl restart 'wpmgr-worker@*' wpmgr-web
```

Lalu aktifkan **Izinkan staging** di **Pengaturan → WP Manager** di wp-admin setiap site yang akan
distaging. Tanpa setelan itu connector membalas 403 untuk semua endpoint staging.

Periksa hasilnya dengan `sudo -u wpmgr sudo -n /usr/local/sbin/wpmgr-staging status`. Perintah ini
harus mencetak JSON berisi memori, disk, dan container.

Catatan:

- **`wpmgr-worker@staging` wajib.** Job staging hanya diambil instans worker yang namanya diawali
  `staging`, karena tarik site 20 GB bisa berjalan berjam-jam dan tidak boleh memakan worker umum.
  Tarik dan uji boleh berjalan bersamaan dengan scan/update site yang sama. Dorong dan kembalikan
  tidak boleh.
- **`/etc/letsencrypt/options-ssl-nginx.conf`** dibuat certbot `--nginx` dan dipakai site lain di
  `sites-enabled`. Bila berkas itu tidak ada di VPS, ganti baris `include` dengan baris `ssl_protocols`/
  `ssl_ciphers` dari server block site lain sebelum `nginx -t`.
- **Image dipin lewat digest** di `/etc/wpmgr-staging/digest.lock`, yang diisi pada pemakaian pertama
  setiap image. Untuk memperbarui image (mis. rilis keamanan PHP), hapus barisnya lalu jalankan
  `wpmgr-staging siapkan`. Container staging memakai image baru pada tarik berikutnya.
- **Isolasi:** container staging boleh ke internet (update plugin), tetapi tidak ke host atau jaringan
  privat. Aturan itu ada di rantai `INPUT` dan `DOCKER-USER` untuk jembatan `br-wpmgrstg`.
- **Kata sandi preview** ditampilkan sekali saat staging dibuat atau kata sandinya dibuat ulang
  (pengguna `staging`). Tombol **Masuk admin staging** melewati Basic Auth dengan tautan bertanda
  tangan yang berlaku 12 jam.
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

Test skrip pembantu berjalan di container bats dengan `docker` tiruan:

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" -w /code bats/bats:1.11.0 deploy/staging/tests
```

Keterbatasan staging:

- Konsistensi database hanya dijamin per potongan (2.000 baris). Tabel tanpa primary key yang
  ditulisi selama tarik bisa kehilangan atau menggandakan baris, dan job mencatatnya sebagai peringatan.
- Tanda air hanya mengenal posts, komentar, user, pesanan WooCommerce, Gravity Forms, WPForms, Fluent
  Forms, dan Flamingo. Data plugin lain bisa tertimpa oleh **timpa penuh**; snapshot tetap
  memungkinkan pengembalian.
- **Kembalikan** snapshot dari dorongan *hanya kode* memulihkan berkas saja. Ekspor database di snapshot
  itu disimpan untuk pemulihan manual.
- Multisite dan site dengan `wp-content` di luar folder WordPress belum didukung.
````

Tambahkan juga ke tabel "Struktur repo (ringkas)":

```markdown
| `src/wpmgr/staging/` | Lapis 3: validasi, skrip pembantu, paket biner, rencana, job tarik/uji/dorong/kembalikan, cron |
| `deploy/staging/` | Skrip pembantu root, sudoers, nginx host staging, unit systemd, test bats |
```

- [ ] **Step 5: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/unit/test_deploy_staging.py -q`. Expected: `6 passed`. Lalu periksa sintaks dengan nginx 1.24 yang sama dengan VPS (ssl_certificate berbasis variabel tidak dibaca saat `-t`, jadi tidak butuh sertifikat):

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)/deploy/staging/nginx-wpmgr-staging.conf:/etc/nginx/conf.d/staging.conf:ro" nginx:1.24 sh -c 'mkdir -p /etc/letsencrypt && touch /etc/letsencrypt/options-ssl-nginx.conf && nginx -t'
```

Expected: `nginx: the configuration file /etc/nginx/nginx.conf syntax is ok` dan `test is successful`.

- [ ] **Step 6: Commit.**

```bash
git add deploy/staging/nginx-wpmgr-staging.conf deploy/staging/sudoers-wpmgr-staging deploy/staging/wpmgr-staging-siapkan.service README.md tests/unit/test_deploy_staging.py
git commit -m "docs(staging): nginx host, sudoers, unit siapkan, dan panduan pemasangan"
```

---

### Task 22: E2E staging terhadap WordPress asli dan runtime staging lokal

**Files:**
- Create: `tests/e2e/pembantu/Dockerfile`, `tests/e2e/test_staging.py`
- Modify: `docker-compose.yml`, `.gitignore`, `README.md`

**Interfaces:**
- Consumes: semua task sebelumnya.
- Produces:
  - Service compose `pembantu` (profil `staging`), dibangun dari `docker:27-cli` + bash/coreutils/setpriv. Service ini memasang socket Docker, skrip `deploy/staging/wpmgr-staging` (read-only), dan `./var/e2e-stg` di `/srv/wpmgr`.
  - Dashboard di pytest memanggil skrip lewat `WPMGR_STAGING_PEMBANTU_AWALAN="docker compose --profile staging exec -T pembantu /usr/local/sbin/wpmgr-staging"` (Koreksi #18). Tidak ada `sudo` dan tidak ada perubahan pada jalur produksi.
  - `staging.conf` e2e: `DOMAIN=staging.test`, semua direktori di bawah `/srv/wpmgr`, `AKAR_LOKAL=/srv/wpmgr`, `AKAR_DAEMON=<Source bind mount menurut docker inspect>` (path Windows `D:\...` diterjemahkan ke `/run/desktop/mnt/host/d/...`), `PENGGUNA_UID=33`, `TANPA_IPTABLES=1`, `TANPA_SERTIFIKAT=1`.
  - Router di `http://localhost:8090` dan Mailpit di `http://localhost:8025`.

Skenario spec §14.5 dijalankan berurutan dalam satu test, karena fixture `sesi` e2e mengosongkan tabel setelah setiap test:
1. tarik penuh (preview dengan Basic Auth dan `X-Robots-Tag`, SSO lewat `/__wpmgr_masuk`, email tertangkap Mailpit);
2. segarkan inkremental;
3. uji update plugin yang lolos;
4. uji update dengan plugin yang sengaja fatal di staging;
5. dorong hanya kode;
6. timpa penuh ditolak karena komentar baru;
7. kembalikan snapshot.

Skenario 3–4 butuh internet (wordpress.org); tanpa internet keduanya di-skip dengan pesan jelas, tetapi skenario lain tetap berjalan. Semua penantian memakai `tunggu_hingga`, tanpa `sleep` tetap.

- [ ] **Step 1: Image pembantu dan service compose.**

File: `tests/e2e/pembantu/Dockerfile`
```dockerfile
# Menjalankan skrip pembantu staging di Linux untuk e2e di Docker Desktop.
# Hanya untuk test: di VPS skrip dijalankan root langsung lewat sudo.
FROM docker:27-cli
RUN apk add --no-cache bash coreutils curl grep setpriv
```

Tambahkan ke `docker-compose.yml` di bawah `services:` (setelah `wpcli`):

```yaml
  # E2E staging (Task 22): skrip pembantu dijalankan di Linux dengan socket
  # Docker Desktop. Profil `staging` supaya `docker compose up -d` biasa
  # tidak membangunnya.
  pembantu:
    profiles: ["staging"]
    build: ./tests/e2e/pembantu
    entrypoint: ["sh", "-c", "sleep infinity"]
    environment:
      WPMGR_STG_KONF: /srv/wpmgr/staging.conf
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./deploy/staging/wpmgr-staging:/usr/local/sbin/wpmgr-staging:ro
      - ./var/e2e-stg:/srv/wpmgr
```

Tambahkan ke `.gitignore`:

```
var/e2e-stg/
```

- [ ] **Step 2: Tulis test e2e.**

File: `tests/e2e/test_staging.py`
```python
"""E2E Lapis 3 (spec §14.5): WordPress e2e sebagai produksi, runtime staging di Docker Desktop."""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from wpmgr.config import get_settings
from wpmgr.crypto import dekripsi_secret, enkripsi_secret
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    JobStatus,
    JobType,
    Staging,
    StagingSnapshot,
    StagingUji,
    StatusStaging,
)
from wpmgr.sso import buat_token
from wpmgr.staging.pembantu import cookie_akses, hash_sandi, tautan_masuk

from .conftest import (
    AKAR_REPO,
    hapus_di_kontainer,
    jalankan_sampai_selesai,
    klien_http,
    tulis_di_kontainer,
    tunggu_hingga,
    wpcli,
)

pytestmark = pytest.mark.e2e

NAMA = "uji-e2e"
DOMAIN = "staging.test"
HOST = f"{NAMA}.{DOMAIN}"
ROUTER = "http://localhost:8090"
MAILPIT = "http://localhost:8025"
SANDI = "sandi-preview-e2e"
RAHASIA = "e" * 64
AKAR_E2E = AKAR_REPO / "var" / "e2e-stg"
AWALAN = "docker compose --profile staging exec -T pembantu /usr/local/sbin/wpmgr-staging"
JEBAKAN = "/var/www/html/wp-content/mu-plugins/wpmgr-e2e-jebakan.php"


def _docker(*args: str, check: bool = True) -> str:
    hasil = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=600, check=False)
    if check and hasil.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} gagal: {hasil.stderr}")
    return hasil.stdout.strip()


def _bersihkan_runtime() -> None:
    nama = _docker("ps", "-aq", "--filter", "label=wpmgr.staging", check=False).split()
    if nama:
        _docker("rm", "-f", *nama, check=False)
    _docker("network", "rm", "wpmgr-staging", check=False)
    _docker("volume", "rm", "wpmgr-stg-db", check=False)


def _akar_daemon() -> str:
    cid = subprocess.run(["docker", "compose", "--profile", "staging", "ps", "-q", "pembantu"],
                         capture_output=True, text=True, check=True).stdout.strip()
    sumber = _docker("inspect", "--format",
                     '{{range .Mounts}}{{if eq .Destination "/srv/wpmgr"}}{{.Source}}{{end}}{{end}}', cid)
    cocok = re.match(r"([A-Za-z]):[\\/](.*)", sumber)
    if cocok:
        # Docker Desktop (WSL2) menerima path Windows sebagai /run/desktop/mnt/host/<drive>/...
        return f"/run/desktop/mnt/host/{cocok.group(1).lower()}/" + cocok.group(2).replace("\\", "/")
    return sumber


@pytest.fixture(scope="module")
def runtime_staging():
    _bersihkan_runtime()
    shutil.rmtree(AKAR_E2E, ignore_errors=True)
    (AKAR_E2E / "staging").mkdir(parents=True)
    subprocess.run(["docker", "compose", "--profile", "staging", "up", "-d", "--build", "pembantu"],
                   check=True, capture_output=True, timeout=600)
    konf = "\n".join([
        f"DOMAIN={DOMAIN}", "STAGING_DIR=/srv/wpmgr/staging", "KONF_DIR=/srv/wpmgr/etc",
        "CERT_DIR=/srv/wpmgr/certs", "ACME_DIR=/srv/wpmgr/acme", "LE_DIR=/srv/wpmgr/le", "LOG_DIR=/srv/wpmgr/log",
        "ROUTER_PORT=127.0.0.1:8090", "MAIL_PORT=127.0.0.1:8025", "SUBNET=172.31.250.0/24",
        "PENGGUNA_UID=33", "PENGGUNA_GID=33", "AKAR_LOKAL=/srv/wpmgr", f"AKAR_DAEMON={_akar_daemon()}",
        "TANPA_IPTABLES=1", "TANPA_SERTIFIKAT=1",
    ]) + "\n"
    (AKAR_E2E / "staging.conf").write_bytes(konf.encode("ascii"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WPMGR_STAGING_DOMAIN", DOMAIN)
        mp.setenv("WPMGR_STAGING_DIR", str(AKAR_E2E / "staging"))
        mp.setenv("WPMGR_STAGING_PEMBANTU_AWALAN", AWALAN)
        mp.setenv("WPMGR_STAGING_ROUTER_URL", ROUTER)
        mp.setenv("WPMGR_STAGING_MAILPIT_URL", MAILPIT)
        get_settings.cache_clear()
        from wpmgr.staging.pembantu import Pembantu

        Pembantu.dari_setelan().siapkan()
        yield
        get_settings.cache_clear()
    _bersihkan_runtime()


def _halaman(jalur: str = "/", **kw) -> httpx.Response:
    return httpx.get(f"{ROUTER}{jalur}", headers={"Host": HOST, **kw.pop("headers", {})}, timeout=30, **kw)


def _jalankan(sesi, site, tipe, payload=None, batas=5):
    job = buat_job(sesi, site.id, tipe, payload or {})
    jalankan_sampai_selesai(sesi, job, batas=batas)
    return job


def _files() -> Path:
    return AKAR_E2E / "staging"


def _ada_internet() -> bool:
    try:
        return httpx.get("https://api.wordpress.org/plugins/info/1.0/hello-dolly.json", timeout=10).status_code == 200
    except httpx.HTTPError:
        return False


def test_alur_staging_lengkap(sesi, site_terpasang, runtime_staging):
    site = site_terpasang
    # --- Persiapan: connector 3.0 mengizinkan staging --------------------
    wpcli("option", "update", "wpmgr_izinkan_staging", "1")
    fitur = klien_http(site).ping()["fitur"]
    assert "staging" in fitur
    site.fitur = fitur
    sesi.add(Staging(site_id=site.id, nama=NAMA, sandi_hash=hash_sandi(SANDI),
                     rahasia_router_terenkripsi=enkripsi_secret(RAHASIA)))
    sesi.commit()
    files = _files() / str(site.id) / "files"

    # --- 1. Tarik penuh ---------------------------------------------------
    job = _jalankan(sesi, site, JobType.staging_tarik)
    assert job.status == JobStatus.success, job.error
    st = sesi.query(Staging).one()
    assert (st.status, st.aktif, st.versi_php) == (StatusStaging.siap, True, "8.1")
    assert (files / "wp-includes" / "version.php").exists()
    assert "define( 'DB_HOST', 'wpmgr-stg-db' );" in (files / "wp-config.php").read_text(encoding="utf-8")
    assert tunggu_hingga(lambda: _halaman(auth=("staging", SANDI)).status_code == 200, 90)
    beranda = _halaman(auth=("staging", SANDI))
    assert "<title>" in beranda.text and "Uji" in beranda.text
    assert "noindex" in beranda.headers.get("x-robots-tag", "")
    assert _halaman().status_code == 401
    assert _halaman(auth=("staging", "salah")).status_code == 401

    # Cookie secure_link membuka preview tanpa kata sandi (probe dan SSO).
    kue = "; ".join(f"{a}={b}" for a, b in cookie_akses(RAHASIA, HOST, int(time.time())).items())
    assert _halaman(headers={"Cookie": kue}).status_code == 200
    token = buat_token(dekripsi_secret(site.secret_terenkripsi), str(site.id))
    masuk = _halaman(tautan_masuk(RAHASIA, HOST, token, int(time.time())), follow_redirects=False)
    assert masuk.status_code == 302 and masuk.headers["location"].startswith("/?wpmgr_sso=")
    assert "wpmgr_stg_m=" in masuk.headers.get("set-cookie", "")

    # Email dari staging tertangkap Mailpit dengan tag nama staging.
    httpx.post(f"{ROUTER}/wp-login.php?action=lostpassword", headers={"Host": HOST},
               auth=("staging", SANDI), data={"user_login": "admin", "redirect_to": ""}, timeout=30)
    assert tunggu_hingga(lambda: httpx.get(f"{MAILPIT}/api/v1/search", params={"query": f'tag:"{NAMA}"'},
                                           timeout=10).json().get("messages"), 30)

    # --- 2. Segarkan inkremental ----------------------------------------
    tulis_di_kontainer("/var/www/html/wp-content/uploads/e2e-baru.txt", "baru dari produksi")
    job = _jalankan(sesi, site, JobType.staging_tarik)
    assert job.status == JobStatus.success, job.error
    assert (files / "wp-content" / "uploads" / "e2e-baru.txt").read_text(encoding="utf-8") == "baru dari produksi"
    assert job.payload["kemajuan"]["byte_selesai"] < 1024 * 1024

    # --- 3–4. Uji update (butuh wordpress.org) ----------------------------
    if _ada_internet():
        wpcli("plugin", "install", "hello-dolly", "--version=1.6", "--force", "--activate")
        paket = [{"tipe": "plugin", "slug": "hello-dolly/hello.php", "dari": "1.6", "ke": "1.7.2"}]
        job = _jalankan(sesi, site, JobType.staging_uji_update, {"paket": paket})
        assert job.status == JobStatus.success, job.error
        uji = sesi.query(StagingUji).order_by(StagingUji.id.desc()).first()
        assert uji.hasil == "lolos", uji.pemeriksaan

        tulis_di_kontainer(JEBAKAN, (
            "<?php\n"
            "add_action( 'plugins_loaded', function () {\n"
            "    if ( ! defined( 'WPMGR_STAGING' ) ) { return; }\n"
            "    $d = get_file_data( WP_PLUGIN_DIR . '/hello-dolly/hello.php', array( 'v' => 'Version' ) );\n"
            "    if ( version_compare( $d['v'], '1.7.2', '>=' ) ) { wpmgr_e2e_fungsi_tidak_ada(); }\n"
            "} );\n"
        ))
        try:
            job = _jalankan(sesi, site, JobType.staging_uji_update, {"paket": paket})
            assert job.status == JobStatus.success, job.error
            uji = sesi.query(StagingUji).order_by(StagingUji.id.desc()).first()
            assert uji.hasil == "gagal"
            assert any("error fatal baru" in a for a in uji.pemeriksaan["alasan"])
        finally:
            hapus_di_kontainer(JEBAKAN)
        # Samakan staging dengan produksi lagi sebelum dorong.
        assert _jalankan(sesi, site, JobType.staging_tarik).status == JobStatus.success
    else:
        print("Uji update dilewati: wordpress.org tidak dapat dihubungi dari mesin ini.")

    # --- 5. Dorong hanya kode --------------------------------------------
    tema = wpcli("theme", "list", "--status=active", "--field=name")
    gaya = files / "wp-content" / "themes" / tema / "style.css"
    asli = gaya.read_bytes()
    gaya.write_bytes(asli + b"\n/* e2e-dorong */\n")
    job = _jalankan(sesi, site, JobType.staging_dorong, {"mode": "hanya_kode"})
    assert job.status == JobStatus.success, job.error
    assert job.hasil["dorong_gagal"] is False
    di_produksi = subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "cat", f"/var/www/html/wp-content/themes/{tema}/style.css"],
        capture_output=True, check=True).stdout
    assert b"/* e2e-dorong */" in di_produksi
    assert httpx.get(site.url, timeout=30).status_code < 400
    snap = sesi.query(StagingSnapshot).one()
    assert snap.status == "tersedia"

    # --- 6. Timpa penuh ditolak karena komentar baru ----------------------
    wpcli("comment", "create", "--comment_post_ID=1", "--comment_content=komentar e2e", "--comment_approved=1")
    job = _jalankan(sesi, site, JobType.staging_dorong, {"mode": "timpa_penuh"})
    assert job.status == JobStatus.failed
    assert "komentar baru" in job.error
    assert b"/* e2e-dorong */" in subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "cat", f"/var/www/html/wp-content/themes/{tema}/style.css"],
        capture_output=True, check=True).stdout

    # --- 7. Kembalikan snapshot -------------------------------------------
    job = _jalankan(sesi, site, JobType.staging_kembalikan, {"snapshot_id": snap.id, "konfirmasi_nama": site.nama})
    assert job.status == JobStatus.success, job.error
    kembali = subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "cat", f"/var/www/html/wp-content/themes/{tema}/style.css"],
        capture_output=True, check=True).stdout
    assert kembali.replace(b"\r\n", b"\n") == asli.replace(b"\r\n", b"\n")
    sesi.refresh(snap)
    assert snap.status == "dipakai"
    assert httpx.get(site.url, timeout=30).status_code < 400
    assert os.path.isdir(_files() / str(site.id) / "snapshot")
```

- [ ] **Step 3: Jalankan e2e.** Run:

```bash
docker compose up -d db wp wpdb wpcli
.venv/Scripts/python -m pytest tests/e2e/test_staging.py -m e2e -q -s
```

Expected: `1 passed`. Waktu jalan pertama beberapa menit, karena menarik image `wordpress:php8.1-apache`, `mariadb:11.4`, `nginx:1.27-alpine`, dan `axllent/mailpit`. Jalankan juga seluruh e2e (`pytest tests/e2e -m e2e -q`) untuk memastikan test Lapis 1–2 tetap lulus dengan connector 3.0.

Bila test gagal di langkah preview, kumpulkan diagnosis sebelum mengubah kode: `docker logs wpmgr-stg-router`, `docker logs wp-uji-e2e`, dan isi `var/e2e-stg/etc/router/conf.d/`.

- [ ] **Step 4: Angka test di README.** Perbarui angka di bagian "Menjalankan test" README dengan jumlah dari keluaran terakhir:
  - unit: `pytest -m "not integration and not e2e"`;
  - integrasi: `pytest tests/integration -m integration`;
  - e2e: `pytest tests/e2e -m e2e`;
  - PHP: `vendor/bin/phpunit`.

Tambahkan juga perintah PHP 7.4 dan perintah bats dari Global Constraints ke bagian itu.

- [ ] **Step 5: Verifikasi akhir.** Jalankan unit, integrasi, e2e, PHPUnit 8.3 dan 7.4, bats, serta `ruff check .`. Expected: semuanya hijau. Tempel keluaran mentahnya di laporan task.

- [ ] **Step 6: Commit.**

```bash
git add docker-compose.yml .gitignore tests/e2e/pembantu/Dockerfile tests/e2e/test_staging.py README.md
git commit -m "test(staging): e2e tarik, segarkan, uji update, dorong, tolak timpa penuh, dan kembalikan"
```

- [ ] **Step 7: Verifikasi manual bersama pengguna di VPS (spec §14.6).** Ikuti bagian README "Staging (Lapis 3)" untuk:
  - memasang skrip, sudoers, `wpmgr-staging siapkan`, berkas nginx, dan worker `staging`;
  - membuat staging dari satu site kecil, lalu menunggu sertifikat pertama terbit (`renew-staging-certs` atau otomatis di akhir tarik);
  - membuka preview dengan kata sandi, menjalankan uji update, dan mendorong hanya kode.
  Catat hasil setiap langkah di laporan.

---

## Tinjauan mandiri rencana terhadap spec

**Cakupan per bagian spec:**

| Spec | Task |
|---|---|
| §1 tujuan, §3 lingkup | seluruh rencana; yang ditunda tetap di luar |
| §2 batasan (RAM 2 GB, 3 aktif, 384 MB, disk 15%, snapshot 3, jeda 3 hari, nginx host tak disentuh, sudo) | 10, 12 (`cek_ram`/`cek_disk`/`cek_maks_aktif`), 14, 16, 18, 19, 21 |
| §4 arsitektur | 10 (runtime), 11 (jembatan sudo), 13 (job), 2–8 (connector) |
| §5.1 model | 1 (+ Koreksi #4) |
| §5.2 disk | 14 (`files/`, `tarik/`, `indeks.jsonl`, `log/`, `ekspor/`), 16 (`dorong/`, `snapshot/j<job>/`), 10 (certs, acme, db per staging) |
| §5.3 area connector dan `.maintenance` | 7, 8 |
| §6.1 delapan endpoint, setelan mati default, ≤30 detik/≤8 MB, staging tidak mengumumkan | 2–9 |
| §6.2 tarik (manifest, pengecualian, hash ≤50 MB, selisih, paket/rentang, ulang 3×, tabel per PK, penyiapan) | 3, 4, 5, 12, 14 |
| §6.3 dorong (dua mode, tanda air, snapshot, unggah, cek ulang, terapkan atomik, cek halaman) | 7, 8, 12, 16 |
| §6.4 progres dan melanjutkan, batal | 13, 14, 16 (+ Koreksi #2, #3) |
| §7.1–7.5 runtime, nginx host, sertifikat, skrip pembantu, pengaman, batas | 9, 10, 11, 18, 21 |
| §8.1 uji update | 15, 19, 20 |
| §8.2 tanda air | 6, 12, 16 |
| §8.3 snapshot dan Kembalikan | 16, 17 (+ Koreksi #13) |
| §8.4 log aktivitas | 14–19 (tabel di Task 17) |
| §9 UI | 20 |
| §10 konfigurasi | 1 (+ Koreksi #7, #18) |
| §11 job dan cron, eksklusif per site | 1 (indeks unik parsial), 13 (klaim), 14–18 |
| §12 penanganan error | 13 (`jalankan_staging`, `pesan_os`), 14 (ulang, galat bernama berkas/tabel), 16 (pulihkan, `dorong_gagal`) |
| §13 keamanan | 2 (path), 6/7 (HMAC, area terlindung), 10 (validasi, label, setpriv, user DB terbatas), 11 (validasi lapis kedua), 19 (login, email per tag), 21 (map SNI) |
| §14 strategi test | unit 11–12/15/21, PHPUnit 2–9, integrasi 1/13–20, bats 10, e2e 22, manual 22 Step 7 |
| §15 deployment, §18 selesai | 21, 22 |

**Pemindaian placeholder:** tidak ada "TODO", "TBD", atau "mirip Task N" di langkah-langkah. Satu-satunya placeholder yang disengaja adalah `__WPMGR_NAMA__` di template mu-plugin (Task 9), yang diganti `isi_mu_plugin_staging()` setelah nama divalidasi. Angka jumlah test di README (Task 22 Step 4) memang diisi dari keluaran test sungguhan.

**Konsistensi nama dan signature lintas task:**
- `umum.jalankan_staging(sesi, job, inti, status_kerja, nama)` dipakai tarik, uji, dorong, dan kembalikan. `inti(sesi, job, site, staging)` mengembalikan dict.
- `tarik.tarik(..., akhir_status)` dipakai uji. `ambil_manifest`, `Salin`, `ekspor_db(..., tahap_berikut)`, dan `urai_tabel` dipakai dorong.
- Kunci kemajuan dipisah per job: `tahap` (tarik), `tahap_uji`, `tahap_dorong`, `tahap_balik`. `ringkas_kemajuan` (Task 19) membaca urutan yang sama.
- Kontrak connector ↔ dashboard:
  - format paket `WPMGRPAK1` sama di PHP (Task 2) dan Python (Task 12);
  - rencana dorong `{versi, berkas[{path, ukuran, sha256, mtime}], hapus, sql, charset}` dihasilkan `rencana_connector()` (Task 16) dan divalidasi `WPMGR_Staging_Dorong::rencana()` (Task 8);
  - jenis potongan `berkas|rentang|sql|rencana` sama di kedua sisi.
- Kelas galat `STAGING_MATI`, `STAGING_DITOLAK`, `STAGING_GAGAL`, `TERLALU_BESAR`, `BERKAS_HILANG` (Task 12) dipetakan dari kode connector `wpmgr_staging_mati`, `wpmgr_staging_terlalu_besar`, `wpmgr_staging_tidak_ada` (Task 2–4).
- Label container `situs:<nama>`/`layanan:<peran>`, format `status`, dan kode keluar skrip (Task 10) cocok dengan `urai_status`, `KODE_KELUAR`, dan `PembantuPalsu` (Task 11, 14).

**Review Focus:** kelima mode kegagalan di tabel awal punya test bernama di task pemiliknya (3, 5, 10, 14). Reviewer task-task itu diminta memeriksa bahwa test tersebut ditulis lebih dulu dan terbukti RED.
