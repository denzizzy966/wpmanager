# WP Manager Lapis 4 (Pindah Hosting) — Rencana Implementasi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Memindahkan site company profile dari shared hosting (tanpa SSH) ke VPS dashboard dan menjalankannya di sana sebagai produksi: salin lewat connector, pratinjau ber-HTTPS dengan kata sandi, cek DNS otomatis, aktivasi dengan sertifikat per domain, lalu backup harian.

**Architecture:** Dashboard tetap satu-satunya pengendali. Tiga tipe job baru (`pindah_tarik`, `pindah_aktifkan`, `backup_hosting`) diproses `wpmgr-worker@staging` lewat antrean Lapis 1 dan memakai ulang mesin tarik Lapis 3 lewat `tarik.tarik_inti` dengan `TujuanSalinan`. Runtime produksi (jaringan `wpmgr-prod`, MariaDB `wpmgr-prod-db`, router `wpmgr-prod-router`, container `wpp-<nama>`) dan satu berkas nginx host per domain hanya bisa disentuh lewat subperintah `prod-*` baru di skrip root `wpmgr-staging`. Klien ke hosting lama dipatok ke IP lamanya dan hanya bisa membaca.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, httpx/httpcore, dnspython 2.6, bcrypt; PHP 7.4+ (mu-plugin pratinjau); bash 5 + Docker 29 untuk skrip pembantu; nginx 1.24 (host) dan nginx 1.27-alpine (router); MariaDB 11.4; certbot; pytest, PHPUnit 9, bats 1.11, shellcheck.

**Spec:** `docs/superpowers/specs/2026-10-03-wp-manager-lapis4-design.md` (mengikat). Spec Lapis 1–3 dan rencana Lapis 3 (`docs/superpowers/plans/2026-09-26-wp-manager-lapis3.md`) tetap berlaku untuk semua yang tidak diubah di sini.

**Tenggat:** tiga site harus pindah dalam ±5 hari. Urutan task mendahulukan jalur inti **tarik → pratinjau → DNS → aktifkan** (Task 1–12). UI (Task 13), backup (Task 14), dokumentasi (Task 15), dan e2e (Task 16) menyusul.

## Global Constraints

Setiap task wajib mematuhi seluruh butir ini. Butir yang ditandai (L3) adalah putusan Lapis 3 yang berlaku apa adanya.

- Python `>=3.10` (bukan 3.11): tanpa `datetime.UTC`, `match`, `Self`, `ExceptionGroup`. Pakai `timezone.utc`. **Semua datetime tz-aware UTC**; sesi DB sudah dipatok `timezone=UTC` di `db.py`. Stempel backup dibentuk dari `datetime.now(timezone.utc)`.
- PHP mu-plugin wajib jalan di PHP 7.4: tanpa `match`, named arguments, `str_contains`/`str_starts_with`/`str_ends_with`, union type, `readonly`, nullsafe `?->`, `never`. Closure, `strtr`, `PHP_INT_MAX` boleh.
- **Pesan UI selalu teks tetap.** Field yang tampil di UI (`hosting_vps.galat`, `job.error`, `detail` HTTPException, `ActivityLog.pesan`/`detail`) tidak pernah memuat stderr skrip, path VPS, kredensial, `str(exc)`, nilai mentah DNS, atau teks respons connector. Galat skrip pembantu dipetakan ke `GalatPembantu.pesan` (teks tetap dari `PESAN_UMUM`/`AKSI`, putusan F20). Galat connector di job hosting dipetakan ke `hosting.umum.PESAN_KELAS` per kelas; teks aslinya hanya ke log server lewat `bersih_teks(..., 500)`. Galat berkas memakai `staging.umum.pesan_os` (L3). Pengecualian tak terduga memakai `PESAN_TAK_TERDUGA` (F12, L3). Nilai DNS hanya disimpan sesudah lolos `ipaddress`.
- **I/O berkas di pohon yang bisa ditulis container** (`HOSTING_DIR/<site_id>/files/`, `log/`) hanya lewat `wpmgr.staging.aman` (`tulis_atomik`, `hapus_berkas`, `baca_terbatas`, `buka_baca`, `jalur_di_dalam`) — tanpa mengikuti symlink (F1, L3). Direktori di luar pohon itu (`tarik/`, `indeks.jsonl`, `router/`) boleh I/O biasa. Penghapusan direktori lewat nisan (`dorong.ke_nisan`/`hapus_nisan`).
- **Urutan kunci baris:** `sites` FOR NO KEY UPDATE (`cron._kunci_site`) lebih dulu, lalu `hosting_vps` FOR UPDATE, lalu (bila perlu) `staging` FOR UPDATE. Dipegang sampai commit perubahan status/penyisipan job. Reaper hanya mengunci `jobs` lalu `hosting_vps` (SKIP LOCKED), seperti `_lepas_staging` (L3). Panggilan jaringan (cek DNS) dilakukan **di luar** kunci; keadaan diperiksa ulang sesudah kunci diambil.
- **Validasi argumen skrip root dua lapis.** Python memvalidasi setiap argumen dengan `re.fullmatch`/`ipaddress`/daftar tetap sebelum `subprocess`; bash memvalidasi lagi dengan `[[ =~ ^...$ ]]` di bawah `LC_ALL=C`. Pola tetap: `<nama>` produksi `[a-z0-9-]{1,36}`; domain `([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]`, ≤ 253, tidak diawali `www.`, tidak sama/di bawah `WPMGR_STAGING_DOMAIN`; `<www>` `0|1`; versi PHP dari `7.4 8.0 8.1 8.2 8.3`; `<site_id>` UUID kecil; `<prefix>` `[A-Za-z0-9_]{1,20}`; `<stempel>` `[0-9]{8}T[0-9]{6}Z`; htpasswd `pratinjau:\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}`. Regex Python memakai `[0-9]` dan `fullmatch`, tidak pernah `\d` atau `$`. Perintah disusun sebagai array; tanpa `eval`. Pesan galat bash tidak pernah menggemakan masukan mentah.
- **Setiap tunggu punya tenggat keras, setiap aliran punya batas byte.** Subprocess lewat `Pembantu.jalankan` (tenggat + `BATAS_KELUARAN`), HTTP lewat `site_client.minta_bertenggat` (tenggat total + `batas_byte`), DNS lewat `lifetime` per kueri ≤ 3 detik dan total ≤ 10 detik, berkas dari container lewat `baca_terbatas`. Di bash setiap docker/certbot/openssl/nginx/setpriv/systemctl lewat `dibatasi` (F10b, L3); `flock` memakai `-w`.
- **Aturan ulang (L3, diperluas):**
  - **R15:** galat yang sifatnya pasti (cek DNS belum lolos, sertifikat ditolak CA, prasyarat `prod-aktifkan` keluar 3, info site ditolak, disk/RAM kurang) dilempar `staging.umum.GalatDitolakTanpaUbah` dengan pesan tetap dan **tidak** diulang segera; cron/pengguna yang menjadwalkan ulang (dengan backoff sertifikat).
  - **F26:** `UNKNOWN` diperlakukan `TRANSIENT` untuk semua job runtime (staging + hosting), di worker dan di pembungkus.
  - **R26:** `pindah_aktifkan` dengan `kemajuan.langkah_aktifkan ∈ {tukar, verifikasi, beres}` dianggap menyentuh produksi (`queue.menyentuh_produksi`). Selama `queue.dalam_batas_pemulihan` (24 jam sejak `kemajuan.tukar_pada`), galat sementara/tidak diketahui diulang tiap ≤ 15 menit tanpa berhenti di `max_attempts` — di worker, pembungkus `hosting.umum.jalankan_hosting`, dan reaper. Lewat 24 jam: final, `status=gagal` asal `produksi`, pesan `PESAN_PRODUKSI_GAGAL`.
  - Keputusan "final atau diulang" selalu lewat `queue.akan_diulang` (satu sumber kebenaran, L3).
- **Batas satu arah:** sesudah `hosting_vps.dilayani_vps_pada` terisi, dashboard menolak setiap tarik dan batal pindah; skrip menolak `prod-db-buat`, `prod-db-impor`, `prod-hapus` saat `MODE=aktif`. `dilayani_vps_pada` dan `kemajuan.tukar_pada` di-commit **sebelum** `prod-aktifkan` dikirim.
- Konkurensi: operasi atomik di DB (indeks unik parsial `uq_jobs_hosting_aktif`, `INSERT ... ON CONFLICT DO NOTHING`, `rowcount`) alih-alih baca-lalu-tulis (L3).
- Setiap route baru punya test akses anonim (401). Semua route JSON dibatasi jumlah barisnya (≤ 50) dan memfilter `site_id`. Fitur mati: GET menjawab `{"aktif_fitur": false}`, route lain 404.
- Tanpa npm, tanpa CDN. Semua string dari site, DNS, dan job dirender lewat autoescape Jinja2 atau `x-text`. Dilarang `|safe`, `x-html`, `innerHTML` (`tests/unit/test_template_aman.py` tetap berlaku).
- Semua komentar kode, pesan log, pesan error, dan teks UI berbahasa Indonesia; komentar menjelaskan *mengapa*.
- Kontrak HMAC Lapis 1 tidak berubah: path yang ditandatangani `/wp-json` + route, tanpa query string dan tanpa host.
- Skrip pembantu: semua kode produksi di bagian `# ---- produksi` dan memakai label `wpmgr.hosting`, jaringan `wpmgr-prod`, direktori `HOSTING_DIR`/`KONF_DIR/prod`. **Seluruh 51 test bats staging yang ada wajib tetap lulus tanpa diubah.** Berkas tiruan baru di `deploy/staging/tests/palsu/` wajib `git update-index --chmod=+x`.
- **Bukti RED ditempel mentah (L3).** Setiap laporan task menyertakan keluaran test yang gagal sebelum perbaikan dan yang lulus sesudahnya.
- **Konvensi blok kode (L3):** baris `File: <path>` di atas blok kode menunjukkan berkas tujuannya dan bukan isi berkas.
- Perintah (dari akar repo, Git Bash di Windows):
  - Unit: `.venv/Scripts/python -m pytest -m "not integration and not e2e" -q`
  - Integrasi (butuh `docker compose up -d db`): `.venv/Scripts/python -m pytest -m integration -q`
  - E2E (butuh `docker compose up -d` dan profil `staging`): `.venv/Scripts/python -m pytest -m e2e -q`
  - PHP 8.3: `cd connector && vendor/bin/phpunit`
  - PHP 7.4: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/app" -w /app/connector php:7.4-cli php vendor/bin/phpunit`
  - Skrip pembantu: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" -w /code bats/bats:1.11.0 deploy/staging/tests`
  - Shellcheck: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" koalaman/shellcheck:stable /code/deploy/staging/wpmgr-staging /code/deploy/staging/tests/palsu/nginx /code/deploy/staging/tests/palsu/systemctl /code/deploy/staging/tests/palsu/flock /code/deploy/staging/tests/palsu/openssl` (tanpa temuan tingkat error)
  - Lint: `.venv/Scripts/python -m ruff check .`
- Pesan commit diakhiri baris kosong lalu `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit di `main`; tidak di-push.

## Koreksi terhadap spec yang ditemukan saat menyusun rencana

1. **`prod_buat` dipanggil di tahap impor, sebelum `prod_db_buat`** (spec §10.2). `prod-db-buat <nama> <prefix>` mencari `files/` lewat state `situs/<nama>`, dan state itu dibuat `prod-buat`. Karena impor berjalan sebelum penyiapan runtime, `TujuanHosting.impor` memanggil `prod_buat` (idempoten) lalu `prod_db_buat` dan `prod_db_impor`. `siapkan_runtime` tetap memanggil `prod_buat` lagi seperti spec. Berkas sudah tersalin pada tahap itu, jadi entrypoint image WordPress tidak menyalin WordPress bawaan ke `files/`.
2. **Backup pertama diantrekan cron `hosting-cek-dns`, bukan langkah `beres`** (spec §10.4). Langkah `beres` berjalan di dalam job `pindah_aktifkan` yang masih `running`, sehingga `uq_jobs_hosting_aktif` menolak `backup_hosting` baru. Cron `hosting-cek-dns` (tiap 10 menit) mengantrekan backup untuk baris `aktif` yang belum pernah dibackup dan belum pernah gagal backup.
3. **Rantai `WPMGR-PROD-MASUK` untuk INPUT** (spec §7.2 aturan 1–2). Dua aturan `-I INPUT` yang dipasang terpisah bisa terbalik urutannya bila salah satunya hilang. Rantai sendiri (pola `pasang_isolasi_antar`) dikosongkan dan diisi ulang setiap `prod-siapkan`: ESTABLISHED/RELATED, loopback 80/443 ke `IP_PUBLIK`, lalu DROP. Aturan ESTABLISHED/RELATED wajib: balasan container untuk koneksi yang dibuka host (nginx host → router `127.0.0.1:8091`, docker-proxy) juga melewati INPUT dengan `-i br-wpmgrprod`.
4. **Aturan INPUT staging Lapis 3 menolak balasan koneksi host** (temuan saat menyusun Koreksi #3). `-I INPUT -i br-wpmgrstg -j DROP` juga membuang SYN-ACK dari router staging ke docker-proxy/nginx host, sehingga router staging tidak terjangkau begitu iptables aktif (e2e Lapis 3 memakai `TANPA_IPTABLES=1`, jadi tidak terlihat). Task 1 menambahkan `ESTABLISHED,RELATED -j ACCEPT` di atas DROP itu; test bats lama tetap lulus tanpa diubah.
5. **`prod-aktifkan` keluar 3 hanya bila belum ada perubahan.** Pra-cek nama `nginx -T` dijalankan sebagai prasyarat. Sesudah perubahan pertama (`SUDAH_BERUBAH=1`), setiap `galat ditolak` dilaporkan sebagai `internal` (9), sehingga dashboard tidak pernah menghapus penanda tulis-lebih-dulu untuk situs yang setengah beralih.
6. **Mu-plugin pratinjau ditulis ulang bila `prod-aktifkan` keluar 3** (spec §7.6). Dashboard menghapus mu-plugin sebelum `prod-aktifkan`. Bila skrip menolak tanpa perubahan, situs tetap pratinjau (konstanta `WPMGR_PRATINJAU` masih ada), jadi pemblokir email harus dipasang lagi sebelum status kembali `menunggu_dns`.
7. **Pesan galat hosting dipetakan per kelas** (spec §17). `hosting.umum.pesan_ui` memakai teks tetap untuk galat dengan `kode` connector dan untuk kelas `transient`, `unknown`, `bad_response`, `auth_error`, `blocked`, `connector_missing`, `staging_mati`, `terlalu_besar`, `berkas_hilang`. Pesan yang disusun dashboard sendiri (`GalatDitolakTanpaUbah`, `galat_gagal`/`galat_ditolak` tanpa `kode`) diteruskan setelah `bersih_teks`.
8. **`TujuanSalinan` punya tiga field tambahan** (spec §10.2): `dilindungi` (berkas milik tujuan yang tidak pernah dihapus/ditimpa tarik; staging `DILINDUNGI_STAGING`, hosting `wp-config.php` + mu-plugin pratinjau), `subdir` (staging `files log ekspor`, hosting `files log`), dan `tahap_akhir` (staging `sertifikat`, hosting `pratinjau`). `rencana.selisih` mendapat parameter `dilindungi` dengan default lama. `tarik_inti(sesi, job, site, klien, tujuan, k)` mengembalikan `k` terakhir.
9. **`dorong.hapus_dir_staging(relatif, akar=None)` dan `hapus_nisan` memakai `akar` yang diberikan.** Sebelumnya `hapus_nisan(akar, ...)` selalu menghapus relatif terhadap `WPMGR_STAGING_DIR`, sehingga nisan di `WPMGR_HOSTING_DIR` tidak pernah terhapus.
10. **Kode DNS tambahan `cname`** (spec §8.3 + A12). A yang sampai lewat CNAME ke nama lain (mis. `*.cdn.hstgr.net`) dan tidak memuat IPv4 VPS mendapat kode `cname`, supaya UI menyuruh menghapus CNAME lalu membuat A.
11. **Status sesudah aktivasi ditolak tanpa ubah = status sebelum job** (spec §10.4 "status kembali `menunggu_dns`"). Biasanya `menunggu_dns`; aktivasi yang dimulai dari `gagal` asal `salinan` kembali ke `gagal` asal `salinan` supaya salinan setengah jadi tidak tersembunyi.
12. **`WPMGR_HOSTING_DIR` tidak boleh sama dengan, di dalam, atau memuat `WPMGR_STAGING_DIR`** (validator `Settings` dan `muat_konf`). Pemangkasan staging menghapus setiap direktori UUID di `STAGING_DIR` yang tidak punya baris `Staging`; bila kedua direktori berimpit, situs produksi ikut terhapus.
13. **"Periksa ulang" memberi jendela R26 baru.** Job `pindah_aktifkan` yang dimulai saat `dilayani_vps_pada` sudah terisi langsung ke `verifikasi` dan menulis `tukar_pada` baru.
14. **State root `situs/<nama>` menyimpan `PHP=<versi>`** (spec §5.3 tidak mencantumkannya). `manifest.json` backup wajib memuat `versi_php`, dan skrip root tidak punya sumber lain yang dapat dipercaya.
15. **`prod-status`** mencetak kunci `container` sebagai `<nama>` (tanpa awalan `wpp-`), sesuai spec §7.3.3.
16. **`GalatPembantu` di job hosting:** `backup_hosting` dan `pindah_aktifkan` sesudah tukar → `TRANSIENT` (F26/R26); selain itu final (`staging_gagal`), sama dengan staging.
17. **E2E memakai klien hosting lama tiruan** (`hosting.umum.klien_lama` di-monkeypatch ke `klien_http`) dan pemeriksa verifikasi lewat router `127.0.0.1:8091`, karena WordPress e2e berbicara HTTP polos di `localhost:8081` dan tidak ada nginx host.

## Yang tidak dapat dipenuhi spec secara harfiah

- **Pra-cek `nginx -T` membaca `server_name` per baris.** Direktif `server_name` yang dipecah ke beberapa baris di berkas milik site lain hanya dibaca baris pertamanya. Deteksi `conflicting server name` di `nginx -t` tetap menjadi lapis kedua.
- **Salt `wp-config.php` dibuat ulang** pada setiap `prod-db-buat` dan `prod-aktifkan`, termasuk percobaan ulang `prod-aktifkan`; pengguna yang sedang login harus login ulang setiap kali (spec §7.6 "salt baru").
- **CAA dinilai persis seperti spec §8.1** (`issue` atau `issuewild` memuat `letsencrypt.org`), bukan menurut aturan penuh RFC 8659.

## Review Focus

Lima mode kegagalan yang paling mungkin lolos dari test biasa (di luar RF1–RF8 spec §23, yang test-nya juga wajib ada di task pemiliknya), masing-masing dengan test di task pemiliknya:

| # | Mode kegagalan | Test | Task |
|---|---|---|---|
| RFP1 | `WPMGR_HOSTING_DIR` berimpit dengan `WPMGR_STAGING_DIR`; `prune-staging` menghapus situs produksi | `test_hosting_dir_berimpit_dengan_staging_dir_ditolak` (unit), bats `@test "HOSTING_DIR di dalam STAGING_DIR ditolak"` | 4, 1 |
| RFP2 | `prod_db_buat` dipanggil sebelum `prod_buat` (state belum ada), sehingga tarik pertama selalu gagal di tahap impor | `test_prod_buat_sebelum_prod_db_buat` | 8 |
| RFP3 | `prod-aktifkan` keluar 3 sesudah dashboard menghapus mu-plugin pratinjau: situs pratinjau kehilangan pemblokir email, atau penanda tulis-lebih-dulu dihapus padahal skrip sudah mengubah sesuatu | `test_tukar_ditolak_memasang_ulang_mu_plugin`, bats `@test "prod-aktifkan: penolakan sesudah perubahan pertama bukan kode 3"` | 10, 3 |
| RFP4 | Langkah `beres` menyisipkan job `backup_hosting` di bawah `uq_jobs_hosting_aktif`, sehingga aktivasi yang sudah berhasil berakhir `internal_error`; atau backup pertama tidak pernah dibuat | `test_beres_tidak_menyisipkan_job_lain`, `test_cek_dns_mengantrekan_backup_pertama_sekali` | 10, 14 |
| RFP5 | Pra-cek `nginx -T` salah membaca kepemilikan: berkas di direktori berawalan mirip (`wpmgr-hosting-lain/`) dianggap milik sendiri, atau `server_name` berhuruf besar lolos | bats `@test "prod-domain: pra-cek tidak tertipu direktori berawalan mirip atau huruf besar"` | 3 |

Reviewer task-task pemilik diminta memeriksa bahwa test ini (dan test RF1–RF8 spec yang tercantum di task) ditulis lebih dulu dan terbukti RED.

## Peta Berkas

**Dashboard (Python), baru:**

| Berkas | Tanggung jawab | Task |
|---|---|---|
| `src/wpmgr/hosting/__init__.py` | Paket hosting | 5 |
| `src/wpmgr/hosting/umum.py` | Klien hosting lama baca-saja, direktori/host pratinjau, pembungkus `jalankan_hosting`, aturan status | 5, 6 |
| `src/wpmgr/hosting/pindah.py` | `TujuanHosting`, job `pindah_tarik` dan `pindah_aktifkan`, verifikasi | 8, 10 |
| `src/wpmgr/hosting/dns.py` | `periksa_dns`, IP lama, instruksi record, backoff sertifikat | 9 |
| `src/wpmgr/hosting/cron.py` | Cek DNS, perpanjangan sertifikat, backup harian | 12, 14 |
| `src/wpmgr/hosting/backup.py` | `TujuanBackup`, `TujuanLokal`, retensi, job `backup_hosting` | 14 |
| `src/wpmgr/web/routes_hosting.py` | JSON API hosting | 11, 14 |
| `src/wpmgr/templates/_tab_hosting.html`, `src/wpmgr/static/app/hosting.js` | Tab Hosting VPS | 13, 14 |
| `migrations/versions/e1f2a3b4c5d7_lapis4_job_type.py`, `f2a3b4c5d6e8_lapis4_hosting.py` | Enum dan tabel Lapis 4 | 4 |

**Dashboard, diubah:** `pyproject.toml`, `.env.example`, `config.py`, `models.py`, `staging/aman.py` (4); `staging/pembantu.py`, `site_client.py` (5); `jobs/queue.py`, `jobs/reaper.py`, `worker.py`, `staging/umum.py` (6); `staging/tarik.py`, `staging/rencana.py` (7); `connector_paket.py`, `jobs/handlers.py` (8, 10, 14); `web/routes_staging.py`, `web/routes_api.py` (lewat `bersihkan_untuk_hapus_site`), `web/app.py`, `staging/dorong.py` (11); `kunci.py`, `cli.py`, `deploy/crontab` (12, 14); `web/routes_pages.py`, `templates/site_detail.html`, `static/app/detail.js`, `kesehatan.py`, `static/app/kesehatan.js` (13).

**Connector, baru:** `connector/wp-manager-connector/templates/wpmgr-pratinjau.php.tpl`, `connector/tests/PratinjauTest.php` (8).

**Deploy:** `deploy/staging/wpmgr-staging`, `deploy/staging/tests/pembantu.bats`, `deploy/staging/tests/palsu/docker` (1–3, 14); baru `deploy/staging/tests/palsu/{nginx,systemctl,flock,openssl}` (3); `deploy/staging/staging.conf.contoh`, `deploy/staging/wpmgr-staging-siapkan.service`, baru `deploy/staging/nginx-wpmgr-hosting.conf`, `README.md` (15); `tests/e2e/pembantu/Dockerfile` (16).

**Test Python, baru:**
- unit: `tests/unit/test_hosting_aman.py` (4), `tests/unit/test_hosting_klien.py` (5), `tests/unit/test_hosting_pindah.py` (8), `tests/unit/test_hosting_dns.py` (9), `tests/unit/test_hosting_backup.py` (14), `tests/unit/test_kesehatan_hosting.py` (13); ditambah ke `tests/unit/test_config.py` (4), `tests/unit/test_staging_pembantu.py` (5), `tests/unit/test_staging_rencana.py` (7), `tests/unit/test_connector_paket.py` (8), `tests/unit/test_deploy_staging.py` (12, 15);
- integrasi: `tests/integration/test_models_lapis4.py` (4), `test_hosting_antrean.py` (6), `test_hosting_tarik_inti.py` (7), `test_hosting_pindah.py` (8), `test_hosting_aktifkan.py` (10), `test_api_hosting.py` (11, 14), `test_hosting_cron.py` (12, 14), `test_hosting_halaman.py` (13), `test_hosting_backup.py` (14); `tests/integration/conftest.py` dan `tests/integration/staging_palsu.py` diperluas (6, 8);
- e2e: `tests/e2e/test_hosting.py` (16).

## Urutan dan ketergantungan

- Task 1–3: skrip pembantu (bash). Tidak bergantung pada task Python dan boleh dikerjakan paralel dengan Task 4–5.
- Task 4–7: fondasi dashboard (setelan, model, klien, antrean, refaktor tarik).
- Task 8–12: jalur inti (pindah_tarik, DNS, aktivasi, API, cron). **Sesudah Task 12, satu site bisa dipindahkan lewat API dan cron.**
- Task 13: UI dan chip Kesehatan.
- Task 14: backup (skrip, pustaka, job, cron, API, UI).
- Task 15: README dan berkas deploy.
- Task 16: e2e.

Setiap task hanya bergantung pada task bernomor lebih kecil, kecuali Task 1–3 (bash) yang tidak bergantung pada Task 4–7.

---

## Fase A — Skrip pembantu produksi

### Task 1: Fondasi produksi di skrip pembantu, `prod-siapkan`, dan `prod-status`

**Files:**
- Modify: `deploy/staging/wpmgr-staging`, `deploy/staging/tests/palsu/docker`, `deploy/staging/tests/pembantu.bats` (hanya menambah test di akhir berkas)

**Interfaces:**
- Consumes: fungsi Lapis 3 di `wpmgr-staging` (`muat_konf`, `galat`, `dibatasi`, `dk`, `sbg_pengguna`, `cek_mount_root`, `image`, `acak`, `pastikan_brnf`, `ip_dari_angka`, `jalur_daemon`).
- Produces (dipakai Task 2, 3, 14):
  - Kode keluar baru: `galat nginx` → 10, `galat backup` → 11.
  - Kunci `staging.conf` baru: `HOSTING_DIR` (kosong = produksi mati), `PROD_SUBNET` (`172.31.251.0/24`), `PROD_ROUTER_PORT` (`127.0.0.1:8091`), `PROD_CERT_DIR` (`/var/lib/wpmgr/hosting-certs`), `NGINX_HOSTING_DIR` (`/etc/nginx/wpmgr-hosting`), `BACKUP_DIR` (`/var/lib/wpmgr/backup`), `IP_PUBLIK` (wajib bila `HOSTING_DIR` diisi), `NGINX_UJI_SAJA` (0), `SERTIFIKAT_SENDIRI` (0). Variabel turunan: `KONF_PROD="$KONF_DIR/prod"`, `IP_PROD_ROUTER` (broadcast−1), `IP_PROD_DB` (broadcast−3). Konstanta `JARINGAN_PROD=wpmgr-prod`, `JEMBATAN_PROD=br-wpmgrprod`.
  - Validator: `cek_nama_prod`, `cek_domain`, `cek_www`, `cek_stempel`, `butuh_hosting`.
  - Fungsi tergeneralisasi (perilaku staging tetap): `cek_mount_pengguna <jalur> [basis]`, `pastikan_milik <wadah> <label> [kunci]`, `pastikan_layanan_prod <peran>`, `opsi_klien <user> <sandi> [wadah]` (global `OPSI_WADAH`), `sql_root <sql> [wadah] [berkas_sandi]`, `jalankan_layanan <nama> <peran> <ip> <env_wajib> <jaringan> <kunci> <argumen docker...>`, `tulis_wp_config <generator> <dir> <argumen generator...>`, `tulis_router_bawaan [dir]`.
  - Subperintah: `prod-siapkan` (keluar 0 dengan "hosting tidak dikonfigurasi; dilewati" bila `HOSTING_DIR` kosong), `prod-status` (JSON `{"mem_tersedia","disk_total","disk_bebas","backup_total","backup_bebas","container":{"<nama>":{"berjalan":bool}}}`).
  - Docker tiruan: berkas `wadah/<nama>.hosting` = label `wpmgr.hosting`; `jaringan-prod` = jaringan `wpmgr-prod` sudah ada; `ps-hosting` = keluaran `ps --filter label=wpmgr.hosting`.
  - Helper bats: `DOM`, `wadah_prod <nama> <label>`, `aktifkan_hosting`.

- [ ] **Step 1: Perbarui docker tiruan.** Tiga perubahan, perilaku untuk test lama tidak berubah: label `wpmgr.hosting` dibaca dari `wadah/<nama>.hosting`, jaringan `wpmgr-prod` punya penanda sendiri, dan `ps` berfilter hosting membaca `ps-hosting`.

File: `deploy/staging/tests/palsu/docker` (ganti seluruh isi)
```bash
#!/usr/bin/env bash
# docker tiruan untuk test skrip pembantu: mencatat panggilan dan menjawab
# dari berkas di $PALSU: wadah/<nama> = label wpmgr.staging (berkas ini juga
# penanda "container ada"), wadah/<nama>.hosting = label wpmgr.hosting,
# .image = image, .user = user, .mounts = baris "sumber|tujuan",
# .ip = alamat tetap, .env = baris variabel lingkungan.
printf '[%s]' "$@" >> "$PALSU/docker.log"
printf '\n' >> "$PALSU/docker.log"
case "${1-}" in
  inspect)
    nama="${*: -1}"
    format="" sebelum=""
    for a in "$@"; do
      [[ "$sebelum" == --format ]] && format="$a"
      sebelum="$a"
    done
    if [[ -f "$PALSU/daemon-gagal" ]]; then
      echo "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?" >&2
      exit 1
    fi
    if [[ ! -f "$PALSU/wadah/$nama" ]]; then
      echo "Error: No such container: $nama" >&2
      exit 1
    fi
    case "$format" in
      *IPAMConfig*) cat "$PALSU/wadah/$nama.ip" 2>/dev/null || true ;;
      *Config.Env*) cat "$PALSU/wadah/$nama.env" 2>/dev/null || true ;;
      *wpmgr.hosting*) cat "$PALSU/wadah/$nama.hosting" 2>/dev/null || true ;;
      *Labels*) cat "$PALSU/wadah/$nama" ;;
      *Mounts*) cat "$PALSU/wadah/$nama.mounts" 2>/dev/null || true ;;
      *User*) cat "$PALSU/wadah/$nama.user" 2>/dev/null || true ;;
      *) cat "$PALSU/wadah/$nama.image" 2>/dev/null || true ;;
    esac
    ;;
  network)
    # Jaringan staging memakai penanda lama `jaringan`; jaringan produksi
    # `jaringan-prod`, supaya keduanya bisa diuji terpisah.
    berkas="$PALSU/jaringan"
    [[ " $* " == *" wpmgr-prod "* ]] && berkas="$PALSU/jaringan-prod"
    if [[ "${2-}" == inspect ]]; then
      [[ -f "$berkas" ]] || exit 1
    else
      touch "$berkas"
    fi
    ;;
  image)
    printf 'contoh/image@sha256:%s\n' "$(printf 'c%.0s' $(seq 1 64))"
    ;;
  exec)
    # Impor yang tidak pernah selesai: mencatat pid lalu menunggu lama,
    # untuk test penerusan SIGTERM.
    if [[ -f "$PALSU/exec-lama" && "$*" == *"--binary-mode"* ]]; then
      echo "$$" > "$PALSU/exec-lama.pid"
      exec sleep 300
    fi
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
    if [[ "$*" == *label=wpmgr.hosting* ]]; then
      cat "$PALSU/ps-hosting" 2>/dev/null || true
    else
      cat "$PALSU/ps" 2>/dev/null || true
    fi
    ;;
esac
exit 0
```

- [ ] **Step 2: Tulis test bats yang gagal.** Tambahkan ke **akhir** `deploy/staging/tests/pembantu.bats` (test lama tidak disentuh):

File: `deploy/staging/tests/pembantu.bats` (tambahkan di akhir)
```bash

# ---- produksi (Lapis 4) --------------------------------------------------------

DOM=toko.co.id

# Container tiruan yang hanya berlabel wpmgr.hosting (label staging kosong).
wadah_prod() {
  : > "$PALSU/wadah/$1"
  printf '%s' "$2" > "$PALSU/wadah/$1.hosting"
}

# Menyalakan produksi di staging.conf test dan meniru keadaan sesudah
# `prod-siapkan`: direktori root, sandi root DB, php.ini, jaringan, layanan.
aktifkan_hosting() {
  mkdir -p "$S/hosting/router" "$S/hcerts" "$S/nginx-hosting" "$S/backup" \
    "$S/etc/prod/situs" "$S/etc/prod/db" "$S/etc/prod/router/conf.d" "$S/etc/prod/router/htpasswd" \
    "$S/etc/prod/nginx-cadangan"
  chown 1000:1000 "$S/hosting" "$S/hosting/router"
  cat >> "$WPMGR_STG_KONF" <<KONF
HOSTING_DIR=$S/hosting
PROD_CERT_DIR=$S/hcerts
NGINX_HOSTING_DIR=$S/nginx-hosting
BACKUP_DIR=$S/backup
IP_PUBLIK=169.58.91.181
KONF
  printf 'prodrahasia' > "$S/etc/prod/db-root"
  printf '; php.ini tiruan\n' > "$S/etc/prod/php.ini"
  touch "$PALSU/jaringan-prod"
  wadah_prod wpmgr-prod-db layanan:db
  wadah_prod wpmgr-prod-router layanan:router
}

@test "prod-siapkan dilewati dan prod-* lain ditolak bila HOSTING_DIR kosong" {
  run "$SKRIP" prod-siapkan
  [ "$status" -eq 0 ]
  [[ "$output" == *"hosting tidak dikonfigurasi; dilewati"* ]]
  [ -z "$(docker_log)" ]
  run "$SKRIP" prod-status
  [ "$status" -eq 7 ]
  run "$SKRIP" prod-status tambahan
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-siapkan tambahan
  [ "$status" -eq 2 ]
}

@test "konfigurasi hosting divalidasi: subnet beririsan, port router, dan IP publik" {
  aktifkan_hosting
  run "$SKRIP" prod-status
  [ "$status" -eq 0 ]
  echo 'PROD_SUBNET=172.31.250.128/25' >> "$WPMGR_STG_KONF"
  run "$SKRIP" prod-status
  [ "$status" -eq 7 ]
  [[ "$output" == *"beririsan"* ]]
  sed -i '/^PROD_SUBNET=/d' "$WPMGR_STG_KONF"
  echo 'PROD_ROUTER_PORT=0.0.0.0:8091' >> "$WPMGR_STG_KONF"
  run "$SKRIP" prod-status
  [ "$status" -eq 7 ]
  sed -i '/^PROD_ROUTER_PORT=/d' "$WPMGR_STG_KONF"
  sed -i 's/^IP_PUBLIK=.*/IP_PUBLIK=169.58.91.300/' "$WPMGR_STG_KONF"
  run "$SKRIP" prod-status
  [ "$status" -eq 7 ]
  sed -i '/^IP_PUBLIK=/d' "$WPMGR_STG_KONF"
  run "$SKRIP" prod-status
  [ "$status" -eq 7 ]
  [[ "$output" == *"IP_PUBLIK"* ]]
}

@test "HOSTING_DIR di dalam STAGING_DIR ditolak" {
  aktifkan_hosting
  for salah in "$S/staging/hosting" "$S/staging" "$S"; do
    sed -i "s#^HOSTING_DIR=.*#HOSTING_DIR=$salah#" "$WPMGR_STG_KONF"
    run "$SKRIP" prod-status
    [ "$status" -eq 7 ]
  done
}

@test "prod-siapkan membuat direktori, jaringan, layanan, dan isolasi produksi" {
  aktifkan_hosting
  rm -rf "$S/etc/prod" "$PALSU/jaringan-prod" "$PALSU/wadah/wpmgr-prod-db" "$PALSU/wadah/wpmgr-prod-db.hosting" \
    "$PALSU/wadah/wpmgr-prod-router" "$PALSU/wadah/wpmgr-prod-router.hosting"
  sed -i '/^TANPA_IPTABLES=/d' "$WPMGR_STG_KONF"
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\n' "$D64" "$D64" >> "$S/etc/digest.lock"
  run "$SKRIP" prod-siapkan
  [ "$status" -eq 0 ]
  [[ "$output" == *"hosting siap"* ]]
  [ "$(stat -c %a "$S/etc/prod")" = 700 ]
  [ "$(stat -c %a "$S/etc/prod/situs")" = 700 ]
  [ "$(stat -c %a "$S/etc/prod/db")" = 700 ]
  [ "$(stat -c %a "$S/hcerts")" = 700 ]
  [ "$(stat -c %a "$S/backup")" = 700 ]
  [ "$(stat -c %a "$S/etc/prod/nginx.lock")" = 600 ]
  [[ "$(cat "$S/etc/prod/db-root")" =~ ^[0-9a-f]{64}$ ]]
  [ "$(stat -c %a "$S/etc/prod/db-root")" = 600 ]
  for baris in 'upload_max_filesize = 64M' 'post_max_size = 64M' 'memory_limit = 256M' \
               'max_execution_time = 120' 'expose_php = Off'; do
    grep -qxF "$baris" "$S/etc/prod/php.ini"
  done
  [ "$(stat -c %a "$S/etc/prod/php.ini")" = 644 ]
  grep -qF 'return 444;' "$S/etc/prod/router/conf.d/00-bawaan.conf"
  [ -d "$S/hosting/router" ]
  grep -qxF "[network][create][--driver][bridge][--subnet][172.31.251.0/24][--opt][com.docker.network.bridge.name=br-wpmgrprod][--label][wpmgr.hosting=layanan:jaringan][wpmgr-prod]" "$PALSU/docker.log"
  grep -q '^\[run\]\[-d\]\[--name\]\[wpmgr-prod-db\]\[--label\]\[wpmgr.hosting=layanan:db\]\[--network\]\[wpmgr-prod\]\[--ip\]\[172.31.251.252\]\[--restart\]\[unless-stopped\]\[--memory\]\[768m\]\[--env-file\]\[[^]]*\]\[-v\]\[wpmgr-prod-db:/var/lib/mysql\]\[m@sha256:b\{64\}\]\[--innodb-buffer-pool-size=256M\]\[--max-allowed-packet=64M\]\[--local-infile=0\]$' "$PALSU/docker.log"
  grep -qxF "[run][-d][--name][wpmgr-prod-router][--label][wpmgr.hosting=layanan:router][--network][wpmgr-prod][--ip][172.31.251.254][--restart][unless-stopped][--memory][128m][-p][127.0.0.1:8091:80][-v][$S/etc/prod/router/conf.d:/etc/nginx/conf.d:ro][-v][$S/etc/prod/router/htpasswd:/etc/nginx/wpmgr-htpasswd:ro][n@sha256:$D64]" "$PALSU/docker.log"
  ! grep -q 'MARIADB_ROOT_PASSWORD=\|wpmgr-stg-' "$PALSU/docker.log" || false
  masuk='[-A][WPMGR-PROD-MASUK][-m][conntrack][--ctstate][ESTABLISHED,RELATED][-j][ACCEPT]
[-A][WPMGR-PROD-MASUK][-d][169.58.91.181][-p][tcp][-m][multiport][--dports][80,443][-j][ACCEPT]
[-A][WPMGR-PROD-MASUK][-j][DROP]'
  [ "$(grep '^\[-A\]\[WPMGR-PROD-MASUK\]' "$PALSU/iptables.log")" = "$masuk" ]
  antar='[-A][WPMGR-PROD-ANTAR][-m][conntrack][--ctstate][ESTABLISHED,RELATED][-j][ACCEPT]
[-A][WPMGR-PROD-ANTAR][-s][172.31.251.254][-p][tcp][--dport][80][-j][ACCEPT]
[-A][WPMGR-PROD-ANTAR][-d][172.31.251.252][-p][tcp][--dport][3306][-j][ACCEPT]
[-A][WPMGR-PROD-ANTAR][-j][DROP]'
  [ "$(grep '^\[-A\]\[WPMGR-PROD-ANTAR\]' "$PALSU/iptables.log")" = "$antar" ]
  grep -qxF "[-I][DOCKER-USER][-i][br-wpmgrprod][!][-o][br-wpmgrprod][-d][172.16.0.0/12][-j][DROP]" "$PALSU/iptables.log"
  grep -qxF "[-I][DOCKER-USER][-i][br-wpmgrprod][-o][br-wpmgrprod][-j][WPMGR-PROD-ANTAR]" "$PALSU/iptables.log"
  # Lompatan INPUT dipasang sesudah rantainya terisi penuh (-F lalu -A).
  [ "$(grep -n '^\[-I\]\[INPUT\]\[-i\]\[br-wpmgrprod\]\[-j\]\[WPMGR-PROD-MASUK\]$' "$PALSU/iptables.log" | cut -d: -f1)" \
    -gt "$(grep -n '^\[-A\]\[WPMGR-PROD-MASUK\]\[-j\]\[DROP\]$' "$PALSU/iptables.log" | cut -d: -f1)" ]
  [ "$(grep -n '^\[-F\]\[WPMGR-PROD-MASUK\]$' "$PALSU/iptables.log" | cut -d: -f1)" \
    -lt "$(grep -n '^\[-A\]\[WPMGR-PROD-MASUK\]' "$PALSU/iptables.log" | head -1 | cut -d: -f1)" ]
  ! grep -q 'br-wpmgrstg\|WPMGR-STG' "$PALSU/iptables.log" || false
}

@test "prod-siapkan idempoten: layanan yang sesuai hanya dijalankan ulang, sandi root tidak ditimpa" {
  aktifkan_hosting
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\n' "$D64" "$D64" >> "$S/etc/digest.lock"
  printf '172.31.251.252' > "$PALSU/wadah/wpmgr-prod-db.ip"
  printf '172.31.251.254' > "$PALSU/wadah/wpmgr-prod-router.ip"
  run "$SKRIP" prod-siapkan
  [ "$status" -eq 0 ]
  grep -qxF "[start][wpmgr-prod-db]" "$PALSU/docker.log"
  grep -qxF "[start][wpmgr-prod-router]" "$PALSU/docker.log"
  ! grep -q '^\[run\]\|^\[rm\]\|^\[network\]\[create\]' "$PALSU/docker.log" || false
  [ "$(cat "$S/etc/prod/db-root")" = prodrahasia ]
}

@test "prod-siapkan menolak HOSTING_DIR yang bukan milik user dashboard atau berupa symlink" {
  aktifkan_hosting
  chown 0:0 "$S/hosting"
  run "$SKRIP" prod-siapkan
  [ "$status" -eq 7 ]
  rm -rf "$S/hosting"
  mkdir -p "$S/lain"
  chown 1000:1000 "$S/lain"
  ln -s "$S/lain" "$S/hosting"
  run "$SKRIP" prod-siapkan
  [ "$status" -eq 7 ]
  [ -z "$(docker_log)" ]
}

@test "prod-status mencetak JSON memori, disk hosting, disk backup, dan container produksi saja" {
  aktifkan_hosting
  printf 'wpp-toko|running\nwpp-lain|exited\nwpmgr-prod-db|running\nwpp-JAHAT|running\n' > "$PALSU/ps-hosting"
  printf 'wp-staging|running\n' > "$PALSU/ps"
  run "$SKRIP" prod-status
  [ "$status" -eq 0 ]
  [ "$output" = '{"mem_tersedia":4294967296,"disk_total":200000000000,"disk_bebas":60000000000,"backup_total":200000000000,"backup_bebas":60000000000,"container":{"toko":{"berjalan":true},"lain":{"berjalan":false}}}' ]
  grep -qxF "[ps][-a][--filter][label=wpmgr.hosting][--format][{{.Names}}|{{.State}}]" "$PALSU/docker.log"
}

@test "subperintah staging menolak container yang hanya berlabel wpmgr.hosting" {
  wadah_prod wp-toko situs:toko
  run "$SKRIP" jalan toko
  [ "$status" -eq 3 ]
  [[ "$output" == *"bukan milik staging"* ]]
  run "$SKRIP" hapus toko
  [ "$status" -eq 3 ]
  ! grep -q '^\[start\]\|^\[rm\]' "$PALSU/docker.log" || false
}

@test "siapkan staging menerima balasan koneksi host di atas DROP INPUT (Koreksi #4)" {
  sed -i '/^TANPA_IPTABLES=/d' "$WPMGR_STG_KONF"
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\nmailpit=p@sha256:%s\n' "$D64" "$D64" "$D64" >> "$S/etc/digest.lock"
  rm -f "$S/etc/wp-cli.phar"
  run "$SKRIP" siapkan
  [ "$status" -eq 0 ]
  drop="$(grep -n '^\[-I\]\[INPUT\]\[-i\]\[br-wpmgrstg\]\[-j\]\[DROP\]$' "$PALSU/iptables.log" | cut -d: -f1)"
  terima="$(grep -n '^\[-I\]\[INPUT\]\[-i\]\[br-wpmgrstg\]\[-m\]\[conntrack\]\[--ctstate\]\[ESTABLISHED,RELATED\]\[-j\]\[ACCEPT\]$' "$PALSU/iptables.log" | cut -d: -f1)"
  # -I memasang di posisi teratas: yang dipasang belakangan berada di atas.
  [ "$terima" -gt "$drop" ]
  grep -qxF "[-D][INPUT][-i][br-wpmgrstg][-m][conntrack][--ctstate][ESTABLISHED,RELATED][-j][ACCEPT]" "$PALSU/iptables.log"
}
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" -w /code bats/bats:1.11.0 deploy/staging/tests`. Expected: 51 test lama `ok`; test baru `not ok` (mis. `not ok 52 prod-siapkan dilewati dan prod-* lain ditolak bila HOSTING_DIR kosong` karena `GALAT argumen: subperintah tidak dikenal`, dan kunci `HOSTING_DIR` ditolak `GALAT konfigurasi: kunci konfigurasi tidak dikenal`).

- [ ] **Step 4: Implementasikan fondasi di skrip.** Semua perubahan di `deploy/staging/wpmgr-staging`.

(a) Di komentar kepala, ganti baris kode keluar:

```bash
# Kode keluar (stderr: satu baris `GALAT <kode>: <pesan>`):
#   2 argumen, 3 ditolak, 4 docker, 5 sertifikat, 6 impor, 7 konfigurasi,
#   8 wpcli, 9 internal (kegagalan tak terduga yang tertangkap `set -e`;
#   kode keluar mentah perintah seperti awk/install tidak pernah diteruskan),
#   10 nginx (uji/reload nginx host gagal; berkas lama dipulihkan), 11 backup.
```

(b) Sesudah baris `JEMBATAN="br-wpmgrstg"`, tambahkan:

```bash
JARINGAN_PROD="wpmgr-prod"
JEMBATAN_PROD="br-wpmgrprod"
# Container db tempat berkas opsi klien MariaDB sedang dibuat (lihat
# opsi_klien); dipakai jebakan EXIT untuk menghapusnya.
OPSI_WADAH="wpmgr-stg-db"
```

(c) Di `galat()`, tambahkan dua cabang sebelum `*) exit 9 ;;`:

```bash
    nginx) exit 10 ;;
    backup) exit 11 ;;
```

(d) Di `bersihkan()`, ganti baris `timeout -k 5 20 docker exec wpmgr-stg-db rm -f "$OPSI_KLIEN" ...` dengan:

```bash
    timeout -k 5 20 docker exec "$OPSI_WADAH" rm -f "$OPSI_KLIEN" >/dev/null 2>&1 || true
```

(e) Di `muat_konf()`, ganti blok `case "$kunci" in ... esac` dengan:

```bash
    case "$kunci" in
      DOMAIN|STAGING_DIR|KONF_DIR|CERT_DIR|ACME_DIR|LE_DIR|LOG_DIR|ACME_EMAIL|ROUTER_PORT|MAIL_PORT|SUBNET|\
PENGGUNA_UID|PENGGUNA_GID|AKAR_LOKAL|AKAR_DAEMON|TANPA_IPTABLES|TANPA_SERTIFIKAT|MEMINFO|BRNF|NGINX_GROUP|\
HOSTING_DIR|PROD_SUBNET|PROD_ROUTER_PORT|PROD_CERT_DIR|NGINX_HOSTING_DIR|BACKUP_DIR|IP_PUBLIK|NGINX_UJI_SAJA|\
SERTIFIKAT_SENDIRI)
        printf -v "K_$kunci" '%s' "$nilai" ;;
      *) galat konfigurasi "kunci konfigurasi tidak dikenal" ;;
    esac
```

Lalu, tepat sesudah baris `LOCK="$KONF_DIR/digest.lock"`, tambahkan:

```bash
  # Produksi (Lapis 4). HOSTING_DIR kosong = semua subperintah prod-* mati.
  HOSTING_DIR="${K_HOSTING_DIR:-}"
  PROD_SUBNET="${K_PROD_SUBNET:-172.31.251.0/24}"
  PROD_ROUTER_PORT="${K_PROD_ROUTER_PORT:-127.0.0.1:8091}"
  PROD_CERT_DIR="${K_PROD_CERT_DIR:-/var/lib/wpmgr/hosting-certs}"
  NGINX_HOSTING_DIR="${K_NGINX_HOSTING_DIR:-/etc/nginx/wpmgr-hosting}"
  BACKUP_DIR="${K_BACKUP_DIR:-/var/lib/wpmgr/backup}"
  IP_PUBLIK="${K_IP_PUBLIK:-}"
  # Kait test: nginx -t tanpa reload, dan sertifikat self-signed lewat openssl.
  NGINX_UJI_SAJA="${K_NGINX_UJI_SAJA:-0}"
  SERTIFIKAT_SENDIRI="${K_SERTIFIKAT_SENDIRI:-0}"
  KONF_PROD="$KONF_DIR/prod"
```

Ganti baris loop path:

```bash
  for p in "$STAGING_DIR" "$KONF_DIR" "$CERT_DIR" "$ACME_DIR" "$LE_DIR" "$LOG_DIR" "$MEMINFO" "$BRNF" \
           "$PROD_CERT_DIR" "$NGINX_HOSTING_DIR" "$BACKUP_DIR"; do
```

Lalu, tepat sesudah baris `hitung_ip_layanan`, tambahkan:

```bash
  [[ "$PROD_ROUTER_PORT" =~ ^127\.0\.0\.1:[0-9]{2,5}$ ]] || galat konfigurasi "PROD_ROUTER_PORT harus di 127.0.0.1"
  [[ "$PROD_SUBNET" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || galat konfigurasi "PROD_SUBNET tidak sah"
  hitung_ip_prod
  [[ "$NGINX_UJI_SAJA" =~ ^[01]$ && "$SERTIFIKAT_SENDIRI" =~ ^[01]$ ]] || galat konfigurasi "kait test hosting tidak sah"
  if [[ -n "$HOSTING_DIR" ]]; then
    [[ "$HOSTING_DIR" =~ $POLA_JALUR && "$HOSTING_DIR" != *..* ]] || galat konfigurasi "path konfigurasi tidak sah"
    # Pemangkasan staging menghapus setiap direktori UUID di STAGING_DIR yang
    # tidak punya baris Staging; situs produksi tidak boleh berada di sana
    # (RFP1), dan sebaliknya.
    [[ "$HOSTING_DIR" != "$STAGING_DIR" && "$HOSTING_DIR" != "$STAGING_DIR"/* && "$STAGING_DIR" != "$HOSTING_DIR"/* ]] \
      || galat konfigurasi "HOSTING_DIR tidak boleh berimpit dengan STAGING_DIR"
    [[ -n "$IP_PUBLIK" ]] || galat konfigurasi "IP_PUBLIK wajib diisi bila HOSTING_DIR diisi"
    [[ "$IP_PUBLIK" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || galat konfigurasi "IP_PUBLIK tidak sah"
    angka_ip "$IP_PUBLIK" >/dev/null
  fi
```

(f) Sesudah fungsi `hitung_ip_layanan()`, tambahkan:

```bash
# IPv4 bertitik ke bilangan 32 bit; setiap oktet 0..255.
angka_ip() {
  local a b c d o
  IFS=. read -r a b c d <<< "$1"
  for o in "$a" "$b" "$c" "$d"; do
    [[ "$o" =~ ^[0-9]{1,3}$ ]] && (( 10#$o <= 255 )) || galat konfigurasi "alamat IPv4 tidak sah"
  done
  printf '%d' $(( (10#$a << 24) | (10#$b << 16) | (10#$c << 8) | 10#$d ))
}

# Alamat tetap layanan produksi di PROD_SUBNET (pola hitung_ip_layanan):
# broadcast-1 router, broadcast-3 db. PROD_SUBNET tidak boleh beririsan
# dengan SUBNET staging: isolasi antar-jembatan bergantung pada keduanya
# berbeda.
hitung_ip_prod() {
  local pref="${PROD_SUBNET#*/}" pref_stg="${SUBNET#*/}" n n_stg m jaringan siaran kecil
  n="$(angka_ip "${PROD_SUBNET%/*}")"
  (( 10#$pref >= 16 && 10#$pref <= 28 )) || galat konfigurasi "PROD_SUBNET harus berukuran /16 sampai /28"
  m=$(( (0xFFFFFFFF << (32 - 10#$pref)) & 0xFFFFFFFF ))
  jaringan=$(( n & m ))
  siaran=$(( jaringan | (~m & 0xFFFFFFFF) ))
  IP_PROD_ROUTER="$(ip_dari_angka $(( siaran - 1 )))"
  IP_PROD_DB="$(ip_dari_angka $(( siaran - 3 )))"
  n_stg="$(angka_ip "${SUBNET%/*}")"
  kecil=$(( 10#$pref < 10#$pref_stg ? 10#$pref : 10#$pref_stg ))
  m=$(( (0xFFFFFFFF << (32 - kecil)) & 0xFFFFFFFF ))
  (( (n & m) != (n_stg & m) )) || galat konfigurasi "PROD_SUBNET beririsan dengan SUBNET staging"
}
```

(g) Sesudah fungsi `cek_slug()`, tambahkan:

```bash
# Nama situs produksi maksimal 36, supaya host pratinjau vps-<nama> tetap <= 40
# (batas cek_nama untuk `sertifikat`).
cek_nama_prod() { [[ "${1-}" =~ ^[a-z0-9-]{1,36}$ ]] || galat argumen "nama situs tidak sah"; }
cek_domain() {
  local d="${1-}"
  [[ "$d" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$ ]] || galat argumen "domain tidak sah"
  (( ${#d} <= 253 )) || galat argumen "domain tidak sah"
  [[ "$d" != www.* ]] || galat argumen "domain tidak boleh diawali www."
  [[ "$d" != "$DOMAIN" && "$d" != *".$DOMAIN" ]] || galat argumen "domain tidak boleh di bawah domain staging"
}
cek_www() { [[ "${1-}" =~ ^[01]$ ]] || galat argumen "penanda www tidak sah"; }
cek_stempel() { [[ "${1-}" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || galat argumen "stempel backup tidak sah"; }
butuh_hosting() { [[ -n "$HOSTING_DIR" ]] || galat konfigurasi "hosting tidak dikonfigurasi (HOSTING_DIR kosong)"; }
```

(h) Ganti dua baris pertama isi `cek_mount_pengguna()`:

```bash
cek_mount_pengguna() {
  local jalur="$1" basis="${2:-$STAGING_DIR}" sisa p komponen pemilik bagian
  [[ "$jalur" == "$basis"/* ]] || galat ditolak "direktori staging tidak sah"
  sisa="${jalur#"$basis"/}"
  p="$basis"
```

(sisa fungsi tidak berubah).

(i) Ganti `pastikan_milik()` dan tambahkan `pastikan_layanan_prod()` sesudah `pastikan_layanan()`:

```bash
pastikan_milik() {
  local kunci="${3:-wpmgr.staging}" label rc=0 err pemilik=staging
  [[ "$kunci" == wpmgr.hosting ]] && pemilik=hosting
  err="$(mktemp)"
  SEMENTARA+=("$err")
  label="$(dk inspect --type container --format "{{ index .Config.Labels \"$kunci\" }}" "$1" 2>"$err")" || rc=$?
  if (( rc != 0 )); then
    if (( rc == 1 )) && grep -q 'No such' "$err"; then
      return 1
    fi
    galat docker "status container tidak terbaca"
  fi
  [[ "$label" == "$2" ]] || galat ditolak "container sudah ada dan bukan milik $pemilik"
  return 0
}
```

```bash
# Layanan bersama produksi: exec hanya bila labelnya milik hosting (F14).
pastikan_layanan_prod() {
  pastikan_milik "wpmgr-prod-$1" "layanan:$1" wpmgr.hosting || galat ditolak "layanan hosting belum disiapkan"
}
```

(j) Ganti `opsi_klien()`, `hapus_opsi_klien()`, dan `sql_root()`:

```bash
# Kata sandi MariaDB masuk lewat berkas opsi di dalam container (stdin),
# bukan argumen/env proses, supaya tidak terbaca user lain lewat /proc.
# $3: container db (staging bawaan; produksi wpmgr-prod-db).
opsi_klien() {
  local wadah="${3:-wpmgr-stg-db}" pesan="database staging tidak dapat dihubungi"
  if [[ "$wadah" == wpmgr-prod-db ]]; then
    pastikan_layanan_prod db
    pesan="database hosting tidak dapat dihubungi"
  else
    pastikan_layanan db
  fi
  OPSI_WADAH="$wadah"
  OPSI_KLIEN="/run/wpmgr-klien-$$-$RANDOM.cnf"
  # Path diteruskan sebagai argumen posisi, tidak disisipkan ke teks skrip sh.
  # shellcheck disable=SC2016 # $1 memang harus diurai oleh sh di container.
  printf '[client]\nuser=%s\npassword=%s\n' "$1" "$2" \
    | dk exec -i "$wadah" sh -c 'umask 077 && cat > "$1"' sh "$OPSI_KLIEN" \
    || galat docker "$pesan"
}

hapus_opsi_klien() {
  if [[ -n "$OPSI_KLIEN" ]]; then
    dk exec "$OPSI_WADAH" rm -f "$OPSI_KLIEN" >/dev/null 2>&1 || true
    OPSI_KLIEN=""
  fi
}

# $2: container db, $3: berkas kata sandi root (keduanya staging bila kosong).
sql_root() {
  local wadah="${2:-wpmgr-stg-db}" berkas="${3:-$KONF_DIR/db-root}" sandi
  sandi="$(cat "$berkas")" || galat konfigurasi "kata sandi root database tidak ada"
  opsi_klien root "$sandi" "$wadah"
  printf '%s' "$1" | dk exec -i "$wadah" mariadb --defaults-extra-file="$OPSI_KLIEN" \
    || galat docker "perintah database gagal"
  hapus_opsi_klien
}
```

(k) Ganti `pasang_iptables()` (Koreksi #4):

```bash
pasang_iptables() {
  # Container staging boleh ke internet (update plugin), tetapi tidak ke host
  # (PostgreSQL dashboard, ERPNext) maupun jaringan privat mana pun.
  iptables -C INPUT -i "$JEMBATAN" -j DROP 2>/dev/null || iptables -I INPUT -i "$JEMBATAN" -j DROP
  # Balasan container untuk koneksi yang dibuka HOST (docker-proxy dan nginx
  # host ke router 127.0.0.1:8090) juga masuk INPUT lewat jembatan ini; tanpa
  # aturan ini router staging tidak terjangkau. Selalu dicabut lalu dipasang
  # ulang di posisi teratas supaya tetap di atas DROP (Koreksi #4 Lapis 4).
  iptables -D INPUT -i "$JEMBATAN" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
  iptables -I INPUT -i "$JEMBATAN" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  local j
  for j in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10; do
    iptables -C DOCKER-USER -i "$JEMBATAN" ! -o "$JEMBATAN" -d "$j" -j DROP 2>/dev/null \
      || iptables -I DOCKER-USER -i "$JEMBATAN" ! -o "$JEMBATAN" -d "$j" -j DROP
  done
  pasang_isolasi_antar
}
```

(l) Ganti `tulis_router_bawaan()`:

```bash
# $1: direktori conf.d router (staging bila kosong).
tulis_router_bawaan() {
  local dir="${1:-$KONF_DIR/router/conf.d}"
  cat > "$dir/00-bawaan.conf" <<'KONF'
log_format wpmgr_akses '$msec';
resolver 127.0.0.11 valid=10s ipv6=off;
server {
    listen 80 default_server;
    server_name _;
    return 444;
}
KONF
  chmod 0644 "$dir/00-bawaan.conf"
}
```

(m) Ganti `jalankan_layanan()`:

```bash
# Layanan yang sudah ada hanya dijalankan ulang bila alamat tetapnya sudah
# sesuai dan (bila diminta lewat $4) variabel lingkungan wajibnya ada;
# selain itu (container lama tanpa alamat tetap, atau tanpa auth Mailpit)
# dibuat ulang. Data db ada di volume bernama, jadi ikut bertahan.
# $5 jaringan dan $6 kunci label: staging (wpmgr-staging, wpmgr.staging) atau
# produksi (wpmgr-prod, wpmgr.hosting).
jalankan_layanan() {
  local nama="$1" peran="$2" ip="$3" env_wajib="$4" jaringan="$5" kunci="$6" ip_lama env_ada cocok=1
  shift 6
  if pastikan_milik "$nama" "layanan:$peran" "$kunci"; then
    ip_lama="$(dk inspect --type container \
      --format "{{with index .NetworkSettings.Networks \"$jaringan\"}}{{with .IPAMConfig}}{{.IPv4Address}}{{end}}{{end}}" \
      "$nama")" || galat docker "layanan staging tidak terbaca"
    [[ "$ip_lama" == "$ip" ]] || cocok=0
    if [[ -n "$env_wajib" ]]; then
      env_ada="$(dk inspect --type container --format '{{range .Config.Env}}{{println .}}{{end}}' "$nama")" \
        || galat docker "layanan staging tidak terbaca"
      [[ $'\n'"$env_ada"$'\n' == *$'\n'"$env_wajib"$'\n'* ]] || cocok=0
    fi
    if (( cocok )); then
      dk start "$nama" >/dev/null || galat docker "layanan staging tidak dapat dijalankan"
      return 0
    fi
    dk rm -f "$nama" >/dev/null || galat docker "layanan lama tidak dapat dihapus"
  fi
  dk run -d --name "$nama" --label "$kunci=layanan:$peran" --network "$jaringan" --ip "$ip" \
    --restart unless-stopped "$@" >/dev/null || galat docker "layanan staging tidak dapat dibuat"
}
```

Di `cmd_siapkan()`, ganti tiga pemanggilan `jalankan_layanan` (argumen docker tidak berubah, hanya `"$JARINGAN" wpmgr.staging` disisipkan sesudah argumen ke-4):

```bash
  jalankan_layanan wpmgr-stg-db db "$IP_DB" "" "$JARINGAN" wpmgr.staging --memory 1g --env-file "$env_db" \
    -v wpmgr-stg-db:/var/lib/mysql "$img_db" --innodb-buffer-pool-size=256M --max-allowed-packet=64M --local-infile=0
```

```bash
  jalankan_layanan wpmgr-stg-mail mail "$IP_MAIL" "MP_UI_AUTH=$auth_mail" "$JARINGAN" wpmgr.staging --memory 128m \
    --env-file "$env_mail" -p "$MAIL_PORT:8025" "$img_mail"
```

```bash
  jalankan_layanan wpmgr-stg-router router "$IP_ROUTER" "" "$JARINGAN" wpmgr.staging --memory 128m -p "$ROUTER_PORT:80" \
    -v "$m_konf:/etc/nginx/conf.d:ro" \
    -v "$m_htpasswd:/etc/nginx/wpmgr-htpasswd:ro" \
    -v "$m_log:/var/log/wpmgr-akses" \
    "$img_router"
```

(n) Ganti `tulis_wp_config()` dan pemanggilnya di `cmd_db_buat()`:

```bash
# Ditulis SEBAGAI user dashboard tanpa mengikuti symlink: berkas sementara
# baru (mktemp: O_EXCL, nama acak, di direktori yang sama) lalu `mv -fT`
# menggantikan wp-config.php. rename() mengganti symlink itu sendiri, tidak
# pernah menulis ke tujuannya. Direktori files/ bisa diubah user dashboard,
# jadi `tee` langsung ke wp-config.php akan mengikuti symlink yang ditanam
# di sana (putusan I3). $1 generator isi (wp_config atau wp_config_prod),
# $2 direktori files/, sisanya argumen generator.
tulis_wp_config() {
  local generator="$1" dir="$2" sementara
  shift 2
  sementara="$(sbg_pengguna mktemp -p "$dir" .wp-config.XXXXXXXXXX)" \
    || galat ditolak "wp-config.php tidak dapat ditulis"
  if "$generator" "$@" | sbg_pengguna tee "$sementara" >/dev/null \
    && sbg_pengguna mv -fT -- "$sementara" "$dir/wp-config.php"; then
    return 0
  fi
  sbg_pengguna rm -f -- "$sementara" || true
  galat ditolak "wp-config.php tidak dapat ditulis"
}
```

Di `cmd_db_buat()`, ganti baris terakhir:

```bash
  tulis_wp_config wp_config "$STAGING_DIR/$2/files" "$nama" "$db" "$pw" "$3"
```

(o) Tambahkan bagian produksi tepat sebelum `# ---- utama ---------...`:

```bash
# ---- produksi --------------------------------------------------------------
# Semua yang di bawah ini milik hosting VPS (Lapis 4): label wpmgr.hosting,
# jaringan wpmgr-prod, MariaDB dan router sendiri, direktori HOSTING_DIR dan
# KONF_DIR/prod. Subperintah staging tidak pernah menyentuh label ini dan
# sebaliknya (pastikan_milik dengan kunci label).

# Isolasi jembatan produksi (spec §7.2). Rantai sendiri yang dikosongkan dan
# diisi ulang setiap kali, supaya urutan ACCEPT-sebelum-DROP selalu benar
# (Koreksi #3):
# - INPUT: hanya balasan koneksi yang dibuka host dan "loopback" ke 80/443 IP
#   publik VPS (WP-Cron, Site Health, proses latar plugin memanggil URL situs
#   sendiri). Port itu sudah terbuka untuk seluruh internet; selebihnya
#   (PostgreSQL dashboard, ERPNext, Redis) dibuang.
# - DOCKER-USER: tidak ke jaringan privat mana pun, termasuk subnet staging.
# - Antar-container: router -> situs tcp/80, situs -> db tcp/3306, sisanya
#   dibuang (situs A tidak bisa membuka http://wpp-B/).
pasang_iptables_prod() {
  local j masuk=WPMGR-PROD-MASUK antar=WPMGR-PROD-ANTAR
  iptables -N "$masuk" 2>/dev/null || iptables -n -L "$masuk" >/dev/null \
    || galat docker "rantai iptables hosting tidak dapat dibuat"
  iptables -F "$masuk"
  iptables -A "$masuk" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  iptables -A "$masuk" -d "$IP_PUBLIK" -p tcp -m multiport --dports 80,443 -j ACCEPT
  iptables -A "$masuk" -j DROP
  iptables -C INPUT -i "$JEMBATAN_PROD" -j "$masuk" 2>/dev/null \
    || iptables -I INPUT -i "$JEMBATAN_PROD" -j "$masuk"
  for j in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10; do
    iptables -C DOCKER-USER -i "$JEMBATAN_PROD" ! -o "$JEMBATAN_PROD" -d "$j" -j DROP 2>/dev/null \
      || iptables -I DOCKER-USER -i "$JEMBATAN_PROD" ! -o "$JEMBATAN_PROD" -d "$j" -j DROP
  done
  iptables -N "$antar" 2>/dev/null || iptables -n -L "$antar" >/dev/null \
    || galat docker "rantai iptables hosting tidak dapat dibuat"
  iptables -F "$antar"
  iptables -A "$antar" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  iptables -A "$antar" -s "$IP_PROD_ROUTER" -p tcp --dport 80 -j ACCEPT
  iptables -A "$antar" -d "$IP_PROD_DB" -p tcp --dport 3306 -j ACCEPT
  iptables -A "$antar" -j DROP
  iptables -C DOCKER-USER -i "$JEMBATAN_PROD" -o "$JEMBATAN_PROD" -j "$antar" 2>/dev/null \
    || iptables -I DOCKER-USER -i "$JEMBATAN_PROD" -o "$JEMBATAN_PROD" -j "$antar"
}

# Batas PHP situs produksi (spec §7.1); bawaan image (upload 2 MB) tidak cukup.
tulis_php_ini_prod() {
  printf '%s\n' "; Dibuat wpmgr-staging prod-siapkan. Jangan diedit; selalu ditimpa." \
    "upload_max_filesize = 64M" "post_max_size = 64M" "memory_limit = 256M" \
    "max_execution_time = 120" "expose_php = Off" > "$KONF_PROD/php.ini"
  chmod 0644 "$KONF_PROD/php.ini"
}

cmd_prod_siapkan() {
  if [[ -z "$HOSTING_DIR" ]]; then
    echo "hosting tidak dikonfigurasi; dilewati"
    return 0
  fi
  local pemilik env_db sandi img_db img_router m_konf m_htpasswd
  # HOSTING_DIR dibuat operator (install -d -o wpmgr): direktori asli milik
  # user dashboard, karena dashboard menulis files/ dan router/ di bawahnya.
  [[ -d "$HOSTING_DIR" && ! -L "$HOSTING_DIR" ]] || galat konfigurasi "HOSTING_DIR tidak ada atau berupa symlink"
  pemilik="$(stat -c %u -- "$HOSTING_DIR")"
  [[ "$pemilik" == "$UID_W" ]] || galat konfigurasi "HOSTING_DIR bukan milik user dashboard"
  install -d -m 0700 "$KONF_PROD" "$KONF_PROD/situs" "$KONF_PROD/db" "$KONF_PROD/nginx-cadangan" \
    "$PROD_CERT_DIR" "$BACKUP_DIR"
  install -d -m 0755 "$KONF_PROD/router" "$KONF_PROD/router/conf.d" "$KONF_PROD/router/htpasswd" "$NGINX_HOSTING_DIR"
  [[ -f "$LOCK" ]] || install -m 0644 /dev/null "$LOCK"
  [[ -f "$KONF_PROD/nginx.lock" ]] || install -m 0600 /dev/null "$KONF_PROD/nginx.lock"
  if [[ ! -s "$KONF_PROD/db-root" ]]; then
    acak 32 > "$KONF_PROD/db-root"
    chmod 0600 "$KONF_PROD/db-root"
  fi
  tulis_php_ini_prod
  sbg_pengguna mkdir -p "$HOSTING_DIR/router" 2>/dev/null || galat konfigurasi "HOSTING_DIR/router tidak dapat dibuat"
  if ! dk network inspect "$JARINGAN_PROD" >/dev/null 2>&1; then
    dk network create --driver bridge --subnet "$PROD_SUBNET" --opt "com.docker.network.bridge.name=$JEMBATAN_PROD" \
      --label wpmgr.hosting=layanan:jaringan "$JARINGAN_PROD" >/dev/null || galat docker "jaringan hosting tidak dapat dibuat"
  fi
  if [[ "$TANPA_IPTABLES" != 1 ]]; then
    pastikan_brnf
    pasang_iptables_prod
  fi
  tulis_router_bawaan "$KONF_PROD/router/conf.d"
  sandi="$(cat "$KONF_PROD/db-root")"
  img_db="$(image mariadb mariadb:11.4)"
  img_router="$(image nginx nginx:1.27-alpine)"
  cek_mount_root "$KONF_PROD/router/conf.d" d "$KONF_DIR"
  cek_mount_root "$KONF_PROD/router/htpasswd" d "$KONF_DIR"
  m_konf="$(jalur_daemon "$KONF_PROD/router/conf.d")"
  m_htpasswd="$(jalur_daemon "$KONF_PROD/router/htpasswd")"
  env_db="$(mktemp)"
  SEMENTARA+=("$env_db")
  printf 'MARIADB_ROOT_PASSWORD=%s\n' "$sandi" > "$env_db"
  jalankan_layanan wpmgr-prod-db db "$IP_PROD_DB" "" "$JARINGAN_PROD" wpmgr.hosting --memory 768m \
    --env-file "$env_db" -v wpmgr-prod-db:/var/lib/mysql \
    "$img_db" --innodb-buffer-pool-size=256M --max-allowed-packet=64M --local-infile=0
  rm -f "$env_db"
  # Router produksi berjalan sebagai root di container: hanya memasang hasil
  # render template tetap (ditulis root) read-only, seperti router staging.
  jalankan_layanan wpmgr-prod-router router "$IP_PROD_ROUTER" "" "$JARINGAN_PROD" wpmgr.hosting --memory 128m \
    -p "$PROD_ROUTER_PORT:80" -v "$m_konf:/etc/nginx/conf.d:ro" -v "$m_htpasswd:/etc/nginx/wpmgr-htpasswd:ro" \
    "$img_router"
  echo "hosting siap"
}

cmd_prod_status() {
  butuh_hosting
  local mem total bebas b_total b_bebas daftar wadah="" pisah="" nama keadaan berjalan
  mem="$(awk '/^MemAvailable:/ { printf "%.0f", $2 * 1024 }' "$MEMINFO")"
  read -r total bebas < <(df -B1 --output=size,avail "$HOSTING_DIR" | tail -n 1)
  read -r b_total b_bebas < <(df -B1 --output=size,avail "$BACKUP_DIR" | tail -n 1)
  [[ "$mem" =~ ^[0-9]+$ && "$total" =~ ^[0-9]+$ && "$bebas" =~ ^[0-9]+$ && "$b_total" =~ ^[0-9]+$ \
     && "$b_bebas" =~ ^[0-9]+$ ]] || galat docker "status sistem tidak terbaca"
  daftar="$(dk ps -a --filter label=wpmgr.hosting --format '{{.Names}}|{{.State}}')" \
    || galat docker "daftar container tidak terbaca"
  while IFS='|' read -r nama keadaan; do
    [[ "$nama" =~ ^wpp-[a-z0-9-]{1,36}$ ]] || continue
    berjalan=false
    [[ "$keadaan" == running ]] && berjalan=true
    wadah+="$pisah\"${nama#wpp-}\":{\"berjalan\":$berjalan}"
    pisah=","
  done <<< "$daftar"
  printf '{"mem_tersedia":%s,"disk_total":%s,"disk_bebas":%s,"backup_total":%s,"backup_bebas":%s,"container":{%s}}\n' \
    "$mem" "$total" "$bebas" "$b_total" "$b_bebas" "$wadah"
}
```

(p) Di `utama()`, ganti blok validasi jumlah argumen, blok tenggat, dan blok dispatch dengan:

```bash
  case "$perintah" in
    siapkan|router-muat|status|mail-kredensial|prod-siapkan|prod-status)
      [[ $# -eq 0 ]] || galat argumen "subperintah ini tidak menerima argumen" ;;
    buat|db-buat)
      [[ $# -eq 3 ]] || galat argumen "jumlah argumen salah" ;;
    jalan|jeda|hapus|db-hapus|db-impor|sertifikat)
      [[ $# -eq 1 ]] || galat argumen "jumlah argumen salah" ;;
    wpcli)
      [[ $# -ge 3 && $# -le 5 ]] || galat argumen "jumlah argumen salah" ;;
    *)
      galat argumen "subperintah tidak dikenal" ;;
  esac
  case "$perintah" in
    siapkan|prod-siapkan) atur_tenggat "$WAKTU_SIAPKAN" ;;
    buat) atur_tenggat "$WAKTU_BUAT" ;;
    db-impor) atur_tenggat "$WAKTU_IMPOR" ;;
    wpcli) atur_tenggat "$WAKTU_WPCLI" ;;
    sertifikat) atur_tenggat "$WAKTU_SERTIFIKAT" ;;
    *) atur_tenggat "$WAKTU_BAWAAN" ;;
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
    mail-kredensial) cmd_mail_kredensial ;;
    prod-siapkan) cmd_prod_siapkan ;;
    prod-status) cmd_prod_status ;;
  esac
```

- [ ] **Step 5: Jalankan bats dan shellcheck.** Run: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" -w /code bats/bats:1.11.0 deploy/staging/tests`. Expected: `60 tests, 0 failures` (51 lama + 9 baru). Lalu perintah shellcheck dari Global Constraints (tiruan `nginx`, `systemctl`, `flock`, `openssl` baru dibuat di Task 3; jalankan hanya untuk `/code/deploy/staging/wpmgr-staging`). Expected: tanpa temuan tingkat error.

- [ ] **Step 6: Commit.**

```bash
git add deploy/staging/wpmgr-staging deploy/staging/tests/palsu/docker deploy/staging/tests/pembantu.bats
git commit -m "feat(hosting): fondasi produksi di skrip pembantu, prod-siapkan, prod-status

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Container, database, dan router produksi (`prod-buat`, `prod-jalan`, `prod-db-buat`, `prod-db-impor`, `prod-router-muat`)

**Files:**
- Modify: `deploy/staging/wpmgr-staging`, `deploy/staging/tests/pembantu.bats` (tambah di akhir)

**Interfaces:**
- Consumes (Task 1): `cek_nama_prod`, `cek_domain`, `cek_www`, `butuh_hosting`, `cek_mount_pengguna <jalur> [basis]`, `pastikan_milik <wadah> <label> [kunci]`, `pastikan_layanan_prod`, `opsi_klien <user> <sandi> [wadah]`, `sql_root <sql> [wadah] [berkas]`, `tulis_wp_config <generator> <dir> ...`, `KONF_PROD`, `JARINGAN_PROD`, helper bats `DOM`, `wadah_prod`, `aktifkan_hosting`.
- Produces (dipakai Task 3, 14):
  - State root `KONF_PROD/situs/<nama>` (0600): baris `SITE_ID=`, `DOMAIN=`, `WWW=0|1`, `PREFIX=` (kosong sampai `prod-db-buat`), `PHP=<versi>` (Koreksi #14), `MODE=pratinjau|aktif`.
  - Fungsi: `muat_state <nama>` (0 = ada, mengisi `S_SITE_ID S_DOMAIN S_WWW S_PREFIX S_PHP S_MODE`; 1 = tidak ada; galat konfigurasi bila rusak), `tulis_state <nama>` (dari `S_*`, atomik), `domain_dipakai_lain <domain> <nama>`, `nama_db_prod <nama>` (`prd_` + `-`→`_`), `pastikan_siap_prod`, `wp_config_prod <nama> <pratinjau|aktif>` (memakai `S_*`), `cek_mount_wadah_prod <wadah> <struktur|penuh>`, `konf_router_prod <nama> <domain> <www> <mode>`, `muat_router_prod [nama_aktif]` (render semua situs; `nama_aktif` dirender mode aktif walau state-nya masih pratinjau).
  - `pulihkan_router` memakai global `ROUTER_TUJUAN` (kosong = `KONF_DIR/router`) dan `ROUTER_AWALAN` (`stg-` atau `prd-`).
  - Subperintah: `prod-buat <nama> <versi_php> <site_id> <domain> <www>`, `prod-jalan <nama>`, `prod-db-buat <nama> <prefix>`, `prod-db-impor <nama>` (SQL di stdin), `prod-router-muat`.
  - Helper bats: `tulis_state_prod <nama> <mode> [domain] [site_id]`, `buat_situs_prod`, `mounts_prod <site_id>`, `HTPASSWD` (baris pratinjau sah).

- [ ] **Step 1: Tulis test bats yang gagal.** Tambahkan ke akhir `deploy/staging/tests/pembantu.bats`:

File: `deploy/staging/tests/pembantu.bats` (tambahkan di akhir)
```bash

HTPASSWD='pratinjau:$2b$10$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ01234'

# State root situs produksi seperti ditulis prod-buat (+ prefix dari prod-db-buat).
tulis_state_prod() {
  printf 'SITE_ID=%s\nDOMAIN=%s\nWWW=1\nPREFIX=wp_\nPHP=8.1\nMODE=%s\n' "${4:-$ID}" "${3:-$DOM}" "$2" \
    > "$S/etc/prod/situs/$1"
  chmod 0600 "$S/etc/prod/situs/$1"
}

# Direktori situs produksi milik user dashboard (UID 1000), seperti dibuat prod-buat.
buat_situs_prod() {
  mkdir -p "$S/hosting/$ID/files" "$S/hosting/$ID/log"
  chown 1000:1000 "$S/hosting/$ID" "$S/hosting/$ID/files" "$S/hosting/$ID/log"
}

# Jawaban `docker inspect .Mounts` untuk wpp-toko: mount buatan prod-buat bagi site $1.
mounts_prod() {
  printf '%s|/var/www/html\n%s|/wpmgr-log\n%s|/usr/local/etc/php/conf.d/zz-wpmgr.ini\n' \
    "$S/hosting/$1/files" "$S/hosting/$1/log" "$S/etc/prod/php.ini" > "$PALSU/wadah/wpp-toko.mounts"
}

@test "prod-buat menolak argumen tidak sah sebelum docker dipanggil" {
  aktifkan_hosting
  l63="$(printf 'a%.0s' $(seq 1 63))"
  for domain in "www.$DOM" "Toko.co.id" "$DOM." "toko..co.id" "a.staging.contoh.id" "staging.contoh.id" \
                "$(printf 'toko.co.id\nx.id')" "-toko.co.id" "toko.c" "$l63.$l63.$l63.$l63.id" "toko_x.co.id"; do
    run "$SKRIP" prod-buat toko 8.1 "$ID" "$domain" 1
    [ "$status" -eq 2 ]
  done
  run "$SKRIP" prod-buat "$(printf 'a%.0s' $(seq 1 37))" 8.1 "$ID" "$DOM" 1
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-buat Toko 8.1 "$ID" "$DOM" 1
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-buat toko 9.9 "$ID" "$DOM" 1
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-buat toko 8.1 "../$ID" "$DOM" 1
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-buat toko 8.1 "$ID" "$DOM" 2
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-buat toko 8.1 "$ID" "$DOM"
  [ "$status" -eq 2 ]
  [ -z "$(docker_log)" ]
  [ -z "$(ls -A "$S/etc/prod/situs")" ]
}

@test "prod-buat menjalankan wpp-<nama> dengan batas dan mount produksi lalu menulis state" {
  aktifkan_hosting
  run "$SKRIP" prod-buat toko 8.1 "$ID" "$DOM" 1
  [ "$status" -eq 0 ]
  diharapkan="[run][-d][--name][wpp-toko][--label][wpmgr.hosting=situs:toko][--network][wpmgr-prod][--restart][unless-stopped][--memory][512m][--memory-swap][512m][--cpus][1][--pids-limit][256][--user][1000:1000][--cap-drop][ALL][--sysctl][net.ipv4.ip_unprivileged_port_start=0][--security-opt][no-new-privileges][--mount][type=bind,src=$S/hosting/$ID/files,dst=/var/www/html][--mount][type=bind,src=$S/hosting/$ID/log,dst=/wpmgr-log][--mount][type=bind,src=$S/etc/prod/php.ini,dst=/usr/local/etc/php/conf.d/zz-wpmgr.ini,readonly][wordpress@sha256:$D64]"
  grep -qxF "$diharapkan" "$PALSU/docker.log"
  ! grep -q 'wpmgr-ekspor\|/usr/local/bin/wp\|wpmgr.staging=' "$PALSU/docker.log" || false
  [ "$(cat "$S/etc/prod/situs/toko")" = "$(printf 'SITE_ID=%s\nDOMAIN=%s\nWWW=1\nPREFIX=\nPHP=8.1\nMODE=pratinjau' "$ID" "$DOM")" ]
  [ "$(stat -c %a "$S/etc/prod/situs/toko")" = 600 ]
  [ "$(stat -c %u "$S/hosting/$ID/files")" = 1000 ]
  grep -q "^\[--reuid=1000\]\[--regid=1000\]\[--clear-groups\]\[--\]\[mkdir\]\[-p\]\[$S/hosting/$ID/files\]\[$S/hosting/$ID/log\]$" "$PALSU/setpriv.log"
}

@test "prod-buat menolak domain milik state lain dan state milik site atau domain lain" {
  aktifkan_hosting
  LAIN=11111111-2222-3333-4444-555555555555
  tulis_state_prod lain pratinjau "$DOM" "$LAIN"
  run "$SKRIP" prod-buat toko 8.1 "$ID" "$DOM" 1
  [ "$status" -eq 3 ]
  rm -f "$S/etc/prod/situs/lain"
  tulis_state_prod toko pratinjau "$DOM" "$LAIN"
  run "$SKRIP" prod-buat toko 8.1 "$ID" "$DOM" 1
  [ "$status" -eq 3 ]
  tulis_state_prod toko pratinjau lain.co.id
  run "$SKRIP" prod-buat toko 8.1 "$ID" "$DOM" 1
  [ "$status" -eq 3 ]
  ! grep -q '^\[run\]\|^\[rm\]' "$PALSU/docker.log" || false
}

@test "prod-buat menolak container bernama sama yang bukan milik hosting" {
  aktifkan_hosting
  printf 'situs:toko' > "$PALSU/wadah/wpp-toko"
  run "$SKRIP" prod-buat toko 8.1 "$ID" "$DOM" 1
  [ "$status" -eq 3 ]
  [[ "$output" == *"bukan milik hosting"* ]]
  ! grep -q '^\[run\]\|^\[rm\]\|^\[start\]' "$PALSU/docker.log" || false
}

@test "prod-buat pada MODE=aktif hanya menjalankan container yang ada dan tidak pernah membuat ulang" {
  aktifkan_hosting
  buat_situs_prod
  tulis_state_prod toko aktif
  wadah_prod wpp-toko situs:toko
  printf 'wordpress@sha256:lama' > "$PALSU/wadah/wpp-toko.image"
  printf '1000:1000' > "$PALSU/wadah/wpp-toko.user"
  mounts_prod "$ID"
  run "$SKRIP" prod-buat toko 8.2 "$ID" "$DOM" 0
  [ "$status" -eq 0 ]
  grep -qxF "[start][wpp-toko]" "$PALSU/docker.log"
  ! grep -q '^\[run\]\|^\[rm\]' "$PALSU/docker.log" || false
  grep -qxF 'MODE=aktif' "$S/etc/prod/situs/toko"
  grep -qxF 'PHP=8.1' "$S/etc/prod/situs/toko"
  rm -f "$PALSU/wadah/wpp-toko" "$PALSU/wadah/wpp-toko.hosting"
  : > "$PALSU/docker.log"
  run "$SKRIP" prod-buat toko 8.1 "$ID" "$DOM" 1
  [ "$status" -eq 3 ]
  ! grep -q '^\[run\]' "$PALSU/docker.log" || false
}

@test "prod-jalan memeriksa sumber mount sebelum start" {
  aktifkan_hosting
  buat_situs_prod
  wadah_prod wpp-toko situs:toko
  mounts_prod "$ID"
  run "$SKRIP" prod-jalan toko
  [ "$status" -eq 0 ]
  grep -qxF "[start][wpp-toko]" "$PALSU/docker.log"
  : > "$PALSU/docker.log"
  # Mount asing.
  printf '/etc|/host-etc\n' >> "$PALSU/wadah/wpp-toko.mounts"
  run "$SKRIP" prod-jalan toko
  [ "$status" -eq 3 ]
  # Bentuk mount staging (ekspor + wp-cli) bukan buatan prod-buat.
  printf '%s|/var/www/html\n%s|/wpmgr-log\n%s|/wpmgr-ekspor\n%s|/usr/local/etc/php/conf.d/zz-wpmgr.ini\n' \
    "$S/hosting/$ID/files" "$S/hosting/$ID/log" "$S/hosting/$ID/ekspor" "$S/etc/prod/php.ini" > "$PALSU/wadah/wpp-toko.mounts"
  run "$SKRIP" prod-jalan toko
  [ "$status" -eq 3 ]
  # files/ berupa symlink.
  mounts_prod "$ID"
  mkdir -p "$S/lain"
  chown 1000:1000 "$S/lain"
  rmdir "$S/hosting/$ID/files"
  ln -s "$S/lain" "$S/hosting/$ID/files"
  run "$SKRIP" prod-jalan toko
  [ "$status" -eq 3 ]
  ! grep -q '^\[start\]' "$PALSU/docker.log" || false
  # Container milik staging dengan nama yang sama.
  printf 'situs:toko' > "$PALSU/wadah/wpp-toko"
  rm -f "$PALSU/wadah/wpp-toko.hosting"
  run "$SKRIP" prod-jalan toko
  [ "$status" -eq 3 ]
}

@test "prod-db-buat menulis wp-config pratinjau sebagai user dashboard dengan hak DB terbatas" {
  aktifkan_hosting
  buat_situs_prod
  tulis_state_prod toko-a pratinjau
  run "$SKRIP" prod-db-buat toko-a wpx_
  [ "$status" -eq 0 ]
  cfg="$S/hosting/$ID/files/wp-config.php"
  grep -qF "define( 'DB_NAME', 'prd_toko_a' );" "$cfg"
  grep -qF "define( 'DB_USER', 'prd_toko_a' );" "$cfg"
  grep -qF "define( 'DB_HOST', 'wpmgr-prod-db' );" "$cfg"
  grep -qF "\$table_prefix = 'wpx_';" "$cfg"
  grep -qF "define( 'WPMGR_PRATINJAU', true );" "$cfg"
  grep -qF "define( 'WPMGR_PRATINJAU_HOST', 'vps-toko-a.staging.contoh.id' );" "$cfg"
  grep -qF "define( 'WPMGR_DOMAIN', 'toko.co.id' );" "$cfg"
  grep -qF "define( 'DISABLE_WP_CRON', true );" "$cfg"
  grep -qF "define( 'AUTOMATIC_UPDATER_DISABLED', true );" "$cfg"
  grep -qF "if ( isset( \$_SERVER['HTTP_HOST'] ) && WPMGR_PRATINJAU_HOST === \$_SERVER['HTTP_HOST'] ) {" "$cfg"
  grep -qxF "    define( 'WP_HOME', 'https://' . WPMGR_PRATINJAU_HOST );" "$cfg"
  grep -qF "'/wpmgr-log/php-error.log'" "$cfg"
  grep -qF "\$_SERVER['HTTPS'] = 'on';" "$cfg"
  # Tanpa WP_HOME tetap: nilai home/siteurl dari database (hosting lama) yang berlaku.
  ! grep -q "^define( 'WP_HOME'\|WPMGR_STAGING\|WPMGR_DISABLE_MONITORING" "$cfg" || false
  grep -q "^\[--reuid=1000\]\[--regid=1000\]\[--clear-groups\]\[--\]\[mv\]\[-fT\]\[--\]\[$S/hosting/$ID/files/.wp-config.[A-Za-z0-9]*\]\[$cfg\]$" "$PALSU/setpriv.log"
  [[ "$(cat "$PALSU/stdin-1")" == *"password=prodrahasia"* ]]
  sql="$(cat "$PALSU/stdin-2")"
  [[ "$sql" == *"GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, LOCK TABLES, CREATE TEMPORARY TABLES, REFERENCES, CREATE VIEW, SHOW VIEW ON \`prd_toko_a\`.*"* ]]
  [[ "$sql" != *TRIGGER* && "$sql" != *EVENT* && "$sql" != *ROUTINE* && "$sql" != *FILE* && "$sql" != *"GRANT ALL"* ]]
  grep -q '^\[exec\]\[-i\]\[wpmgr-prod-db\]\[mariadb\]' "$PALSU/docker.log"
  ! grep -q 'wpmgr-stg-db' "$PALSU/docker.log" || false
  pw="$(cat "$S/etc/prod/db/toko-a")"
  [[ "$pw" =~ ^[0-9a-f]{48}$ ]]
  [ "$(stat -c %a "$S/etc/prod/db/toko-a")" = 600 ]
  grep -qF "define( 'DB_PASSWORD', '$pw' );" "$cfg"
  grep -qxF 'PREFIX=wpx_' "$S/etc/prod/situs/toko-a"
  run "$SKRIP" prod-db-buat toko-a "wp_'; x"
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-db-buat belum wp_
  [ "$status" -eq 3 ]
}

@test "prod-db-buat tidak mengikuti symlink wp-config.php" {
  aktifkan_hosting
  buat_situs_prod
  tulis_state_prod toko pratinjau
  printf 'JANGAN-DISENTUH' > "$S/target-luar"
  ln -s "$S/target-luar" "$S/hosting/$ID/files/wp-config.php"
  run "$SKRIP" prod-db-buat toko wp_
  [ "$status" -eq 0 ]
  [ "$(cat "$S/target-luar")" = "JANGAN-DISENTUH" ]
  [ ! -L "$S/hosting/$ID/files/wp-config.php" ]
  grep -qF "define( 'DB_NAME', 'prd_toko' );" "$S/hosting/$ID/files/wp-config.php"
}

@test "prod-db-impor menolak MODE=aktif" {
  aktifkan_hosting
  buat_situs_prod
  tulis_state_prod toko aktif
  printf 'sandi-situs' > "$S/etc/prod/db/toko"
  run bash -c "printf 'DROP DATABASE prd_toko;' | '$SKRIP' prod-db-impor toko"
  [ "$status" -eq 3 ]
  run "$SKRIP" prod-db-buat toko wp_
  [ "$status" -eq 3 ]
  ! grep -q '^\[exec\]' "$PALSU/docker.log" || false
  [ ! -e "$S/hosting/$ID/files/wp-config.php" ]
}

@test "prod-db-impor membuat ulang database sebagai root produksi lalu mengimpor sebagai user situs" {
  aktifkan_hosting
  buat_situs_prod
  tulis_state_prod toko pratinjau
  run bash -c "printf 'SELECT 1;' | '$SKRIP' prod-db-impor toko"
  [ "$status" -eq 3 ]
  printf 'sandi-situs' > "$S/etc/prod/db/toko"
  run bash -c "printf 'INSERT INTO t VALUES (1);' | '$SKRIP' prod-db-impor toko"
  [ "$status" -eq 0 ]
  [[ "$(cat "$PALSU/stdin-1")" == *"password=prodrahasia"* ]]
  [[ "$(cat "$PALSU/stdin-2")" == *'SET GLOBAL local_infile=0; DROP DATABASE IF EXISTS `prd_toko`; CREATE DATABASE `prd_toko`'* ]]
  [[ "$(cat "$PALSU/stdin-3")" == *"user=prd_toko"* && "$(cat "$PALSU/stdin-3")" == *"password=sandi-situs"* ]]
  [ "$(cat "$PALSU/stdin-4")" = "INSERT INTO t VALUES (1);" ]
  grep -q '^\[exec\]\[-i\]\[wpmgr-prod-db\]\[mariadb\]\[--defaults-extra-file=/run/wpmgr-klien-[0-9-]*\.cnf\]\[--binary-mode\]\[--local-infile=0\]\[--max-allowed-packet=64M\]\[prd_toko\]$' "$PALSU/docker.log"
  ! grep -q 'sandi-situs\|prodrahasia' "$PALSU/docker.log" || false
  # Berkas opsi klien dihapus dari container produksi, bukan staging.
  grep -q '^\[exec\]\[wpmgr-prod-db\]\[rm\]\[-f\]\[/run/wpmgr-klien-' "$PALSU/docker.log"
}

@test "prod-router-muat merender pratinjau dengan Basic Auth dan aktif tanpa pengaman" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  printf 'SITE_ID=%s\nDOMAIN=lain.id\nWWW=0\nPREFIX=wp_\nPHP=8.1\nMODE=aktif\n' \
    11111111-2222-3333-4444-555555555555 > "$S/etc/prod/situs/lain"
  # Situs pratinjau yang htpasswd-nya belum ditulis dashboard dilewati.
  printf 'SITE_ID=%s\nDOMAIN=baru.id\nWWW=0\nPREFIX=\nPHP=8.1\nMODE=pratinjau\n' \
    22222222-3333-4444-5555-666666666666 > "$S/etc/prod/situs/baru"
  printf '%s\n' "$HTPASSWD" > "$S/hosting/router/toko.htpasswd"
  run "$SKRIP" prod-router-muat
  [ "$status" -eq 0 ]
  c="$S/etc/prod/router/conf.d/prd-toko.conf"
  grep -qxF "    server_name toko.co.id www.toko.co.id vps-toko.staging.contoh.id;" "$c"
  grep -qxF '    auth_basic "Pratinjau toko";' "$c"
  grep -qxF "    auth_basic_user_file /etc/nginx/wpmgr-htpasswd/toko;" "$c"
  grep -qxF '    add_header X-Robots-Tag "noindex, nofollow" always;' "$c"
  grep -qxF '        set $wpmgr_hulu wpp-toko;' "$c"
  grep -qxF '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' "$c"
  grep -qxF '    absolute_redirect off;' "$c"
  l="$S/etc/prod/router/conf.d/prd-lain.conf"
  grep -qxF "    server_name lain.id;" "$l"
  ! grep -q 'auth_basic\|X-Robots-Tag\|vps-' "$l" || false
  [ ! -e "$S/etc/prod/router/conf.d/prd-baru.conf" ]
  [ "$(cat "$S/etc/prod/router/htpasswd/toko")" = "$HTPASSWD" ]
  [ ! -e "$S/etc/prod/router/htpasswd/lain" ]
  grep -qxF "[exec][wpmgr-prod-router][nginx][-t]" "$PALSU/docker.log"
  grep -qxF "[exec][wpmgr-prod-router][nginx][-s][reload]" "$PALSU/docker.log"
  ! grep -q 'wpmgr-stg-router' "$PALSU/docker.log" || false
}

@test "prod-router-muat menolak htpasswd berbahaya dan memulihkan konfigurasi lama bila nginx -t gagal" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  printf 'lama' > "$S/etc/prod/router/conf.d/prd-lama.conf"
  printf 'pratinjau:bukan-bcrypt\n' > "$S/hosting/router/toko.htpasswd"
  run "$SKRIP" prod-router-muat
  [ "$status" -eq 2 ]
  [ "$(cat "$S/etc/prod/router/conf.d/prd-lama.conf")" = "lama" ]
  printf '%s\n' "$HTPASSWD" > "$S/hosting/router/toko.htpasswd"
  touch "$PALSU/nginx-gagal"
  run "$SKRIP" prod-router-muat
  [ "$status" -eq 4 ]
  [ "$(cat "$S/etc/prod/router/conf.d/prd-lama.conf")" = "lama" ]
  [ ! -e "$S/etc/prod/router/conf.d/prd-toko.conf" ]
  ! grep -q 'reload' "$PALSU/docker.log" || false
}
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: perintah bats dari Global Constraints. Expected: 60 test dari Task 1 `ok`; 12 test baru `not ok` (subperintah `prod-buat` dan seterusnya belum dikenal: `GALAT argumen: subperintah tidak dikenal`).

- [ ] **Step 3: Implementasikan.** Semua di `deploy/staging/wpmgr-staging`.

(a) Ganti deklarasi `ROUTER_CADANGAN=""` dan fungsi `pulihkan_router()`:

```bash
ROUTER_CADANGAN=""
# Router yang sedang dipasang ulang: KONF_DIR/router + awalan stg- (staging)
# atau KONF_DIR/prod/router + awalan prd- (produksi).
ROUTER_TUJUAN=""
ROUTER_AWALAN="stg-"
```

```bash
# Mengembalikan konfigurasi router lama; dipanggil dari jebakan EXIT selama
# ROUTER_CADANGAN terisi, sebelum direktori sementara (cadangannya) dihapus.
pulihkan_router() {
  local tujuan="${ROUTER_TUJUAN:-$KONF_DIR/router}"
  rm -f "$tujuan/conf.d/${ROUTER_AWALAN}"*.conf "$tujuan/htpasswd/"* 2>/dev/null || true
  cp -a "$ROUTER_CADANGAN/conf.d/." "$tujuan/conf.d/" 2>/dev/null || true
  cp -a "$ROUTER_CADANGAN/htpasswd/." "$tujuan/htpasswd/" 2>/dev/null || true
  ROUTER_CADANGAN=""
}
```

(b) Di bagian `# ---- produksi`, sesudah `cmd_prod_status()`, tambahkan:

```bash
# ---- state situs produksi ---------------------------------------------------
# KONF_PROD/situs/<nama>: satu-satunya catatan root tentang situs produksi.
# Ditulis hanya skrip ini (0600 root) dan dibaca dengan parser ketat:
# dashboard tidak bisa mengubah SITE_ID, DOMAIN, atau MODE situs mana pun.

POLA_ID='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
POLA_DOMAIN_PROD='^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$'

# 0 = state ada dan sah (S_* terisi); 1 = tidak ada; galat bila rusak.
muat_state() {
  local berkas="$KONF_PROD/situs/$1" baris
  S_SITE_ID="" S_DOMAIN="" S_WWW="" S_PREFIX="" S_PHP="" S_MODE=""
  [[ -e "$berkas" || -L "$berkas" ]] || return 1
  [[ -f "$berkas" && ! -L "$berkas" ]] || galat konfigurasi "state situs rusak"
  while IFS= read -r baris || [[ -n "$baris" ]]; do
    [[ "$baris" =~ ^(SITE_ID|DOMAIN|WWW|PREFIX|PHP|MODE)=(.*)$ ]] || galat konfigurasi "state situs rusak"
    printf -v "S_${BASH_REMATCH[1]}" '%s' "${BASH_REMATCH[2]}"
  done < "$berkas"
  [[ "$S_SITE_ID" =~ $POLA_ID && "$S_DOMAIN" =~ $POLA_DOMAIN_PROD && "$S_WWW" =~ ^[01]$ \
     && "$S_MODE" =~ ^(pratinjau|aktif)$ ]] || galat konfigurasi "state situs rusak"
  [[ -z "$S_PREFIX" || "$S_PREFIX" =~ ^[A-Za-z0-9_]{1,20}$ ]] || galat konfigurasi "state situs rusak"
  [[ -z "$S_PHP" || " ${VERSI_PHP[*]} " == *" $S_PHP "* ]] || galat konfigurasi "state situs rusak"
  return 0
}

tulis_state() {
  local sementara
  install -d -m 0700 "$KONF_PROD/situs"
  sementara="$(mktemp "$KONF_PROD/situs/.$1.XXXXXX")"
  SEMENTARA+=("$sementara")
  printf 'SITE_ID=%s\nDOMAIN=%s\nWWW=%s\nPREFIX=%s\nPHP=%s\nMODE=%s\n' \
    "$S_SITE_ID" "$S_DOMAIN" "$S_WWW" "$S_PREFIX" "$S_PHP" "$S_MODE" > "$sementara"
  chmod 0600 "$sementara"
  mv -fT "$sementara" "$KONF_PROD/situs/$1"
}

# 0 bila domain $1 sudah tercatat di state situs selain $2.
domain_dipakai_lain() {
  local f
  shopt -s nullglob
  for f in "$KONF_PROD"/situs/*; do
    [[ "${f##*/}" == "$2" ]] && continue
    grep -qxF "DOMAIN=$1" "$f" && return 0
  done
  return 1
}

nama_db_prod() { printf 'prd_%s' "${1//-/_}"; }

pastikan_siap_prod() {
  dk network inspect "$JARINGAN_PROD" >/dev/null 2>&1 || galat ditolak "jalankan 'wpmgr-staging prod-siapkan' dulu"
  [[ -s "$KONF_PROD/php.ini" ]] || galat ditolak "jalankan 'wpmgr-staging prod-siapkan' dulu"
}

# ---- container per situs produksi ------------------------------------------

# Sumber bind mount wpp-<nama> harus persis files/ dan log/ milik SATU
# HOSTING_DIR/<site_id> ditambah php.ini root read-only (pola cek_mount_wadah
# staging, putusan R13). Mode `struktur` mengisi MOUNT_ID; `penuh` juga
# memeriksa setiap sumber.
cek_mount_wadah_prod() {
  local wadah="$1" mode="$2" daftar sumber tujuan lokal sub s id="" terlihat=" " p ini=""
  local pengguna=()
  daftar="$(dk inspect --type container --format '{{range .Mounts}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}' "$wadah")" \
    || galat docker "container tidak terbaca"
  while IFS='|' read -r sumber tujuan; do
    [[ -z "$sumber" && -z "$tujuan" ]] && continue
    [[ "$sumber" =~ $POLA_JALUR && "$sumber" != *..* ]] || galat ditolak "sumber mount container tidak sah"
    lokal="$(jalur_lokal "$sumber")"
    case "$tujuan" in
      /var/www/html) sub=files ;;
      /wpmgr-log) sub=log ;;
      /usr/local/etc/php/conf.d/zz-wpmgr.ini)
        [[ "$lokal" == "$KONF_PROD/php.ini" && -z "$ini" ]] || galat ditolak "sumber mount container tidak sah"
        ini="$lokal"
        continue ;;
      *) galat ditolak "container memuat mount yang tidak dikenal" ;;
    esac
    [[ "$terlihat" != *" $sub "* ]] || galat ditolak "sumber mount container tidak sah"
    terlihat+="$sub "
    [[ "$lokal" == "$HOSTING_DIR"/*/"$sub" ]] || galat ditolak "sumber mount container tidak sah"
    s="${lokal#"$HOSTING_DIR"/}"
    s="${s%/"$sub"}"
    [[ "$s" =~ $POLA_ID ]] || galat ditolak "sumber mount container tidak sah"
    [[ -z "$id" || "$id" == "$s" ]] || galat ditolak "sumber mount container tidak sah"
    id="$s"
    pengguna+=("$lokal")
  done <<< "$daftar"
  [[ "$terlihat" == *" files "* && "$terlihat" == *" log "* && -n "$ini" ]] \
    || galat ditolak "sumber mount container tidak lengkap"
  MOUNT_ID="$id"
  if [[ "$mode" == penuh ]]; then
    for p in "${pengguna[@]}"; do
      cek_mount_pengguna "$p" "$HOSTING_DIR"
    done
    cek_mount_root "$ini" f "$KONF_DIR"
  fi
}

cmd_prod_buat() {
  cek_nama_prod "${1-}"
  cek_versi "${2-}"
  cek_id "${3-}"
  cek_domain "${4-}"
  cek_www "${5-}"
  butuh_hosting
  local nama="$1" versi="$2" wadah="wpp-$1" situs img sekarang pemakai d m_files m_log m_ini
  pastikan_siap_prod
  if muat_state "$nama"; then
    [[ "$S_SITE_ID" == "$3" && "$S_DOMAIN" == "$4" ]] \
      || galat ditolak "nama situs ini sudah dipakai site atau domain lain"
    if [[ "$S_MODE" == pratinjau && ( "$S_WWW" != "$5" || "$S_PHP" != "$versi" ) ]]; then
      S_WWW="$5"
      S_PHP="$versi"
      tulis_state "$nama"
    fi
  else
    ! domain_dipakai_lain "$4" "$nama" || galat ditolak "domain sudah dipakai situs lain"
    S_SITE_ID="$3" S_DOMAIN="$4" S_WWW="$5" S_PREFIX="" S_PHP="$versi" S_MODE=pratinjau
    tulis_state "$nama"
  fi
  # Situs aktif tetap memakai versi PHP saat diaktifkan; mengganti image
  # berarti membuat ulang container produksi, dan itu langkah manual.
  [[ "$S_MODE" == pratinjau ]] || versi="$S_PHP"
  situs="$HOSTING_DIR/$S_SITE_ID"
  img="$(image "php${versi/./}" "wordpress:php${versi}-apache")"
  sbg_pengguna mkdir -p "$situs/files" "$situs/log" 2>/dev/null \
    || galat ditolak "direktori situs tidak dapat disiapkan"
  for d in files log; do
    cek_mount_pengguna "$situs/$d" "$HOSTING_DIR"
  done
  cek_mount_root "$KONF_PROD/php.ini" f "$KONF_DIR"
  m_files="$(jalur_daemon "$situs/files")"
  m_log="$(jalur_daemon "$situs/log")"
  m_ini="$(jalur_daemon "$KONF_PROD/php.ini")"
  if pastikan_milik "$wadah" "situs:$nama" wpmgr.hosting; then
    cek_mount_wadah_prod "$wadah" struktur
    if [[ "$S_MODE" == aktif ]]; then
      # Situs yang sudah dilayani VPS tidak pernah dibuat ulang lewat dashboard.
      [[ "$MOUNT_ID" == "$S_SITE_ID" ]] || galat ditolak "container situs aktif tidak cocok dengan state"
      cek_mount_wadah_prod "$wadah" penuh
      dk start "$wadah" >/dev/null || galat docker "container situs tidak dapat dijalankan"
      return 0
    fi
    sekarang="$(dk inspect --type container --format '{{ .Config.Image }}' "$wadah")" \
      || galat docker "container tidak terbaca"
    pemakai="$(dk inspect --type container --format '{{ .Config.User }}' "$wadah")" \
      || galat docker "container tidak terbaca"
    if [[ "$sekarang" == "$img" && "$pemakai" == "$UID_W:$GID_W" && "$MOUNT_ID" == "$S_SITE_ID" ]]; then
      cek_mount_wadah_prod "$wadah" penuh
      dk start "$wadah" >/dev/null || galat docker "container situs tidak dapat dijalankan"
      return 0
    fi
    dk rm -f "$wadah" >/dev/null || galat docker "container lama tidak dapat dihapus"
  elif [[ "$S_MODE" == aktif ]]; then
    galat ditolak "situs aktif tanpa container; pulihkan manual (README)"
  fi
  # Opsi keamanan sama dengan staging (putusan R13): UID dashboard tanpa
  # capability, --mount (sumber hilang = galat), batas memori produksi.
  dk run -d --name "$wadah" --label "wpmgr.hosting=situs:$nama" --network "$JARINGAN_PROD" \
    --restart unless-stopped --memory 512m --memory-swap 512m --cpus 1 --pids-limit 256 \
    --user "$UID_W:$GID_W" --cap-drop ALL --sysctl net.ipv4.ip_unprivileged_port_start=0 \
    --security-opt no-new-privileges \
    --mount "type=bind,src=$m_files,dst=/var/www/html" \
    --mount "type=bind,src=$m_log,dst=/wpmgr-log" \
    --mount "type=bind,src=$m_ini,dst=/usr/local/etc/php/conf.d/zz-wpmgr.ini,readonly" \
    "$img" >/dev/null || galat docker "container situs tidak dapat dibuat"
}

cmd_prod_jalan() {
  cek_nama_prod "${1-}"
  butuh_hosting
  pastikan_milik "wpp-$1" "situs:$1" wpmgr.hosting || galat ditolak "situs belum dibuat"
  cek_mount_wadah_prod "wpp-$1" penuh
  dk start "wpp-$1" >/dev/null || galat docker "situs tidak dapat dijalankan"
}

# ---- database dan wp-config produksi -----------------------------------------

# wp-config.php produksi (spec §7.6). Tanpa WP_HOME/WP_SITEURL tetap: nilai
# dari database (sama dengan hosting lama) yang berlaku. Mode pratinjau
# menambah pengaman yang dicabut prod-aktifkan; database tidak pernah diubah
# untuk pengaman. Memakai S_* dari muat_state.
wp_config_prod() {
  local nama="$1" mode="$2" db pw k garam
  db="$(nama_db_prod "$nama")"
  pw="$(cat "$KONF_PROD/db/$nama")"
  printf '%s\n' "<?php" \
    "// Dibuat oleh wpmgr-staging untuk situs $nama ($S_DOMAIN). Jangan diedit; selalu ditimpa." \
    "define( 'DB_NAME', '$db' );" \
    "define( 'DB_USER', '$db' );" \
    "define( 'DB_PASSWORD', '$pw' );" \
    "define( 'DB_HOST', 'wpmgr-prod-db' );" \
    "define( 'DB_CHARSET', 'utf8mb4' );" \
    "define( 'DB_COLLATE', '' );" \
    "\$table_prefix = '$S_PREFIX';" \
    "define( 'WP_DEBUG', false );" \
    "@ini_set( 'display_errors', '0' );" \
    "@ini_set( 'log_errors', '1' );" \
    "@ini_set( 'error_log', '/wpmgr-log/php-error.log' );" \
    "// Router hosting selalu mengirim X-Forwarded-Proto https (TLS diakhiri nginx host)." \
    "if ( isset( \$_SERVER['HTTP_X_FORWARDED_PROTO'] ) && 'https' === \$_SERVER['HTTP_X_FORWARDED_PROTO'] ) {" \
    "    \$_SERVER['HTTPS'] = 'on';" \
    "}"
  if [[ "$mode" == pratinjau ]]; then
    printf '%s\n' \
      "define( 'WPMGR_PRATINJAU', true );" \
      "define( 'WPMGR_PRATINJAU_HOST', 'vps-$nama.$DOMAIN' );" \
      "define( 'WPMGR_DOMAIN', '$S_DOMAIN' );" \
      "define( 'DISABLE_WP_CRON', true );" \
      "define( 'AUTOMATIC_UPDATER_DISABLED', true );" \
      "if ( isset( \$_SERVER['HTTP_HOST'] ) && WPMGR_PRATINJAU_HOST === \$_SERVER['HTTP_HOST'] ) {" \
      "    define( 'WP_HOME', 'https://' . WPMGR_PRATINJAU_HOST );" \
      "    define( 'WP_SITEURL', 'https://' . WPMGR_PRATINJAU_HOST );" \
      "}"
  fi
  for k in AUTH_KEY SECURE_AUTH_KEY LOGGED_IN_KEY NONCE_KEY AUTH_SALT SECURE_AUTH_SALT LOGGED_IN_SALT NONCE_SALT; do
    garam="$(acak 32)"
    printf "define( '%s', '%s' );\n" "$k" "$garam"
  done
  printf '%s\n' "if ( ! defined( 'ABSPATH' ) ) {" "    define( 'ABSPATH', __DIR__ . '/' );" "}" \
    "require_once ABSPATH . 'wp-settings.php';"
}

cmd_prod_db_buat() {
  cek_nama_prod "${1-}"
  cek_prefix "${2-}"
  butuh_hosting
  local nama="$1" db pw
  muat_state "$nama" || galat ditolak "situs belum dibuat (prod-buat)"
  [[ "$S_MODE" != aktif ]] || galat ditolak "situs sudah aktif; database tidak boleh dibuat ulang"
  db="$(nama_db_prod "$nama")"
  pw="$(acak 24)"
  # Hak sama dengan user staging (Koreksi #8 Lapis 3): DML/DDL atas database
  # sendiri, tanpa TRIGGER/EVENT/ROUTINE/FILE dan tanpa GRANT OPTION.
  sql_root "CREATE DATABASE IF NOT EXISTS \`$db\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$db'@'%' IDENTIFIED BY '$pw';
ALTER USER '$db'@'%' IDENTIFIED BY '$pw';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, LOCK TABLES, CREATE TEMPORARY TABLES, REFERENCES, CREATE VIEW, SHOW VIEW ON \`$db\`.* TO '$db'@'%';" \
    wpmgr-prod-db "$KONF_PROD/db-root"
  install -d -m 0700 "$KONF_PROD/db"
  printf '%s' "$pw" > "$KONF_PROD/db/$nama"
  chmod 0600 "$KONF_PROD/db/$nama"
  S_PREFIX="$2"
  tulis_state "$nama"
  tulis_wp_config wp_config_prod "$HOSTING_DIR/$S_SITE_ID/files" "$nama" pratinjau
}

cmd_prod_db_impor() {
  cek_nama_prod "${1-}"
  butuh_hosting
  local nama="$1" db sandi
  muat_state "$nama" || galat ditolak "situs belum dibuat (prod-buat)"
  # Batas satu arah (spec §4): data situs yang sudah dilayani VPS tidak
  # pernah ditimpa salinan dari hosting lama, sekalipun dashboard meminta.
  [[ "$S_MODE" != aktif ]] || galat ditolak "situs sudah aktif; impor ditolak"
  [[ -s "$KONF_PROD/db/$nama" ]] || galat ditolak "database situs belum dibuat (prod-db-buat)"
  db="$(nama_db_prod "$nama")"
  sandi="$(cat "$KONF_PROD/db/$nama")"
  sql_root "SET GLOBAL local_infile=0; DROP DATABASE IF EXISTS \`$db\`; CREATE DATABASE \`$db\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" \
    wpmgr-prod-db "$KONF_PROD/db-root"
  # SQL dari hosting lama (bisa saja disusupi) diimpor sebagai user situs ini.
  opsi_klien "$db" "$sandi" wpmgr-prod-db
  dk exec -i wpmgr-prod-db mariadb --defaults-extra-file="$OPSI_KLIEN" --binary-mode --local-infile=0 \
    --max-allowed-packet=64M "$db" || galat impor "impor database situs gagal"
}

# ---- router produksi ----------------------------------------------------------

# Template tetap router produksi (spec §7.5). Basic Auth dan noindex hanya
# selama pratinjau; host pratinjau vps-<nama>.<DOMAIN> juga hanya saat itu.
konf_router_prod() {
  local nama="$1" domain="$2" www="$3" mode="$4" nama_server="$2"
  if [[ "$www" == 1 ]]; then
    nama_server+=" www.$domain"
  fi
  if [[ "$mode" == pratinjau ]]; then
    nama_server+=" vps-$nama.$DOMAIN"
  fi
  cat <<KONF
# Dibuat wpmgr-staging prod-router-muat untuk situs $nama ($mode). Jangan diedit.
server {
    listen 80;
    server_name $nama_server;
    client_max_body_size 64m;
    absolute_redirect off;
KONF
  if [[ "$mode" == pratinjau ]]; then
    cat <<KONF
    auth_basic "Pratinjau $nama";
    auth_basic_user_file /etc/nginx/wpmgr-htpasswd/$nama;
    add_header X-Robots-Tag "noindex, nofollow" always;
KONF
  fi
  cat <<KONF
    location / {
        set \$wpmgr_hulu wpp-$nama;
        proxy_pass http://\$wpmgr_hulu;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}
KONF
}

# Render ulang SEMUA situs dari state root (pola cmd_router_muat, Koreksi #6
# Lapis 3): dari dashboard hanya diambil satu baris htpasswd bcrypt per situs
# pratinjau, dibaca sebagai user dashboard dengan batas ukuran. Situs
# pratinjau tanpa htpasswd dilewati (belum siap), bukan menggagalkan router
# untuk situs lain. $1 (opsional): situs yang dirender mode aktif walau
# state-nya masih pratinjau (prod-aktifkan langkah 2). Mengubah S_* (memuat
# state setiap situs); pemanggil memuat ulang state-nya sendiri sesudahnya.
muat_router_prod() {
  local nama_aktif="${1-}" tujuan="$KONF_PROD/router" baru cadangan f n mode baris
  # shellcheck disable=SC2016 # `$` di sini literal regex (awalan bcrypt), bukan ekspansi.
  local pola_htpasswd='^pratinjau:\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$'
  pastikan_layanan_prod router
  baru="$(mktemp -d)"
  SEMENTARA+=("$baru")
  cadangan="$(mktemp -d)"
  SEMENTARA+=("$cadangan")
  shopt -s nullglob
  for f in "$KONF_PROD"/situs/*; do
    n="${f##*/}"
    [[ "$n" =~ ^[a-z0-9-]{1,36}$ ]] || continue
    muat_state "$n" || continue
    mode="$S_MODE"
    if [[ "$n" == "$nama_aktif" ]]; then
      mode=aktif
    fi
    if [[ "$mode" == pratinjau ]]; then
      [[ -e "$HOSTING_DIR/router/$n.htpasswd" || -L "$HOSTING_DIR/router/$n.htpasswd" ]] || continue
      baris="$(sbg_pengguna head -c 1024 "$HOSTING_DIR/router/$n.htpasswd" 2>/dev/null)" \
        || galat argumen "htpasswd pratinjau tidak terbaca"
      [[ "$baris" =~ $pola_htpasswd ]] || galat argumen "htpasswd pratinjau tidak sah"
      printf '%s\n' "$baris" > "$baru/$n.htpasswd"
    fi
    konf_router_prod "$n" "$S_DOMAIN" "$S_WWW" "$mode" > "$baru/prd-$n.conf"
  done
  mkdir -p "$cadangan/conf.d" "$cadangan/htpasswd"
  cp -a "$tujuan/conf.d/." "$cadangan/conf.d/" 2>/dev/null || galat docker "cadangan konfigurasi router gagal"
  cp -a "$tujuan/htpasswd/." "$cadangan/htpasswd/" 2>/dev/null || galat docker "cadangan konfigurasi router gagal"
  # Sejak baris ini konfigurasi aktif diubah; kegagalan apa pun sesudahnya
  # memulihkan cadangan lewat jebakan EXIT.
  ROUTER_TUJUAN="$tujuan"
  ROUTER_AWALAN="prd-"
  ROUTER_CADANGAN="$cadangan"
  rm -f "$tujuan/conf.d/"prd-*.conf "$tujuan/htpasswd/"*
  for f in "$baru"/prd-*.conf; do
    install -m 0644 "$f" "$tujuan/conf.d/" 2>/dev/null \
      || galat docker "konfigurasi router hosting tidak dapat dipasang; konfigurasi lama dipulihkan"
  done
  for f in "$baru"/*.htpasswd; do
    n="$(basename "$f" .htpasswd)"
    install -m 0644 "$f" "$tujuan/htpasswd/$n" 2>/dev/null \
      || galat docker "konfigurasi router hosting tidak dapat dipasang; konfigurasi lama dipulihkan"
  done
  dk exec wpmgr-prod-router nginx -t >/dev/null 2>&1 \
    || galat docker "konfigurasi router hosting ditolak nginx -t; konfigurasi lama dipulihkan"
  dk exec wpmgr-prod-router nginx -s reload >/dev/null \
    || galat docker "router hosting tidak dapat dimuat ulang; konfigurasi lama dipulihkan"
  ROUTER_CADANGAN=""
}

cmd_prod_router_muat() {
  butuh_hosting
  muat_router_prod
}
```

(c) Di `utama()`, ketiga blok `case` menjadi (tambahan dibanding Task 1 ditandai komentar tidak perlu; tulis apa adanya):

```bash
  case "$perintah" in
    siapkan|router-muat|status|mail-kredensial|prod-siapkan|prod-status|prod-router-muat)
      [[ $# -eq 0 ]] || galat argumen "subperintah ini tidak menerima argumen" ;;
    buat|db-buat)
      [[ $# -eq 3 ]] || galat argumen "jumlah argumen salah" ;;
    jalan|jeda|hapus|db-hapus|db-impor|sertifikat|prod-jalan|prod-db-impor)
      [[ $# -eq 1 ]] || galat argumen "jumlah argumen salah" ;;
    prod-db-buat)
      [[ $# -eq 2 ]] || galat argumen "jumlah argumen salah" ;;
    prod-buat)
      [[ $# -eq 5 ]] || galat argumen "jumlah argumen salah" ;;
    wpcli)
      [[ $# -ge 3 && $# -le 5 ]] || galat argumen "jumlah argumen salah" ;;
    *)
      galat argumen "subperintah tidak dikenal" ;;
  esac
  case "$perintah" in
    siapkan|prod-siapkan) atur_tenggat "$WAKTU_SIAPKAN" ;;
    buat|prod-buat) atur_tenggat "$WAKTU_BUAT" ;;
    db-impor|prod-db-impor) atur_tenggat "$WAKTU_IMPOR" ;;
    wpcli) atur_tenggat "$WAKTU_WPCLI" ;;
    sertifikat) atur_tenggat "$WAKTU_SERTIFIKAT" ;;
    *) atur_tenggat "$WAKTU_BAWAAN" ;;
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
    mail-kredensial) cmd_mail_kredensial ;;
    prod-siapkan) cmd_prod_siapkan ;;
    prod-status) cmd_prod_status ;;
    prod-buat) cmd_prod_buat "$@" ;;
    prod-jalan) cmd_prod_jalan "$@" ;;
    prod-db-buat) cmd_prod_db_buat "$@" ;;
    prod-db-impor) cmd_prod_db_impor "$@" ;;
    prod-router-muat) cmd_prod_router_muat ;;
  esac
```

- [ ] **Step 4: Jalankan bats dan shellcheck.** Run: perintah bats. Expected: `72 tests, 0 failures`. Lalu shellcheck untuk `/code/deploy/staging/wpmgr-staging`. Expected: tanpa temuan tingkat error.

- [ ] **Step 5: Commit.**

```bash
git add deploy/staging/wpmgr-staging deploy/staging/tests/pembantu.bats
git commit -m "feat(hosting): container, database, dan router produksi di skrip pembantu

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: nginx host per domain, sertifikat domain, aktivasi, dan hapus (`prod-domain`, `prod-sertifikat`, `prod-aktifkan`, `prod-hapus`)

**Files:**
- Create: `deploy/staging/tests/palsu/nginx`, `deploy/staging/tests/palsu/systemctl`, `deploy/staging/tests/palsu/flock`, `deploy/staging/tests/palsu/openssl`
- Modify: `deploy/staging/wpmgr-staging`, `deploy/staging/tests/pembantu.bats` (tambah di akhir)

**Interfaces:**
- Consumes (Task 1–2): `muat_state`, `tulis_state`, `S_*`, `nama_db_prod`, `wp_config_prod`, `tulis_wp_config`, `muat_router_prod [nama_aktif]`, `pastikan_milik ... wpmgr.hosting`, `sql_root ... wpmgr-prod-db`, `cek_mount_root`, `KONF_PROD`, `NGINX_HOSTING_DIR`, `PROD_CERT_DIR`, `PROD_ROUTER_PORT`, `NGINX_UJI_SAJA`, `SERTIFIKAT_SENDIRI`, helper bats `tulis_state_prod`, `buat_situs_prod`, `wadah_prod`, `aktifkan_hosting`, `HTPASSWD`.
- Produces:
  - Global `SUDAH_BERUBAH` (0/1; `galat ditolak` sesudah 1 menjadi `internal`), `NGINX_TUJUAN`, `NGINX_CADANGAN` (dipulihkan jebakan EXIT).
  - Fungsi: `kunci_nginx` (`flock -w 120` pada `KONF_PROD/nginx.lock`, fd 9), `uji_nginx <domain|"">`, `muat_ulang_nginx`, `pra_cek_nama <domain>`, `konf_domain <nama> <pratinjau|aktif>`, `pasang_domain <nama> <mode>`, `hapus_domain <domain>`, `pulihkan_nginx`, `sertifikat_domain_ada`.
  - Subperintah: `prod-domain <nama>`, `prod-sertifikat <nama>` (stdout satu baris `terbit`/`tetap`/`diperbarui`), `prod-aktifkan <nama>` (stdout `aktif`; keluar 3 hanya bila belum ada yang diubah), `prod-hapus <nama>`.
  - Tiruan: `nginx` (`-T` mencetak `$PALSU/nginx-T`; `-t` gagal bila `$PALSU/nginx-t-gagal`, mencetak `$PALSU/nginx-t-peringatan` ke stderr), `systemctl` (`reload` gagal bila `$PALSU/reload-gagal`), `flock` (gagal bila `$PALSU/flock-gagal`), `openssl` (menulis `-out`/`-keyout`). Semua mencatat argumen ke `$PALSU/<nama>.log`.

- [ ] **Step 1: Tiruan perintah baru.**

File: `deploy/staging/tests/palsu/nginx`
```bash
#!/usr/bin/env bash
# nginx host tiruan untuk prod-domain: -T mencetak $PALSU/nginx-T (konfigurasi
# lengkap tiruan), -t gagal bila $PALSU/nginx-t-gagal ada dan mencetak isi
# $PALSU/nginx-t-peringatan ke stderr bila ada (nginx -t tetap keluar 0 pada
# peringatan "conflicting server name").
printf '[%s]' "$@" >> "$PALSU/nginx.log"
printf '\n' >> "$PALSU/nginx.log"
case "${1-}" in
  -T)
    if [[ -f "$PALSU/nginx-T" ]]; then
      cat "$PALSU/nginx-T"
    else
      printf '# configuration file /etc/nginx/nginx.conf:\nhttp {\n}\n'
    fi
    ;;
  -t)
    if [[ -f "$PALSU/nginx-t-peringatan" ]]; then
      cat "$PALSU/nginx-t-peringatan" >&2
    fi
    if [[ -f "$PALSU/nginx-t-gagal" ]]; then
      echo "nginx: [emerg] konfigurasi tiruan gagal" >&2
      exit 1
    fi
    echo "nginx: configuration file /etc/nginx/nginx.conf test is successful" >&2
    ;;
esac
exit 0
```

File: `deploy/staging/tests/palsu/systemctl`
```bash
#!/usr/bin/env bash
# systemctl tiruan: mencatat argumen; `reload` gagal bila $PALSU/reload-gagal ada.
printf '[%s]' "$@" >> "$PALSU/systemctl.log"
printf '\n' >> "$PALSU/systemctl.log"
if [[ "${1-}" == reload && -f "$PALSU/reload-gagal" ]]; then
  exit 1
fi
exit 0
```

File: `deploy/staging/tests/palsu/flock`
```bash
#!/usr/bin/env bash
# flock tiruan: mencatat argumen; gagal (kunci dipegang pihak lain) bila
# $PALSU/flock-gagal ada.
printf '[%s]' "$@" >> "$PALSU/flock.log"
printf '\n' >> "$PALSU/flock.log"
if [[ -f "$PALSU/flock-gagal" ]]; then
  exit 1
fi
exit 0
```

File: `deploy/staging/tests/palsu/openssl`
```bash
#!/usr/bin/env bash
# openssl tiruan untuk SERTIFIKAT_SENDIRI: menulis rantai dan kunci tiruan ke
# berkas -out dan -keyout.
printf '[%s]' "$@" >> "$PALSU/openssl.log"
printf '\n' >> "$PALSU/openssl.log"
keluar="" kunci=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -out) keluar="$2"; shift ;;
    -keyout) kunci="$2"; shift ;;
  esac
  shift
done
if [[ -n "$keluar" ]]; then
  echo "rantai-sendiri" > "$keluar"
fi
if [[ -n "$kunci" ]]; then
  echo "kunci-sendiri" > "$kunci"
fi
exit 0
```

Run: `git add deploy/staging/tests/palsu/{nginx,systemctl,flock,openssl} && git update-index --chmod=+x deploy/staging/tests/palsu/nginx deploy/staging/tests/palsu/systemctl deploy/staging/tests/palsu/flock deploy/staging/tests/palsu/openssl`. Expected: tanpa keluaran.

- [ ] **Step 2: Tulis test bats yang gagal.** Tambahkan ke akhir `deploy/staging/tests/pembantu.bats`:

File: `deploy/staging/tests/pembantu.bats` (tambahkan di akhir)
```bash

NGF() { printf '%s' "$S/nginx-hosting/$DOM.conf"; }

# Situs siap diaktifkan: direktori, state pratinjau, sandi DB, container,
# router, htpasswd, dan sertifikat domain milik root.
siap_aktifkan() {
  aktifkan_hosting
  buat_situs_prod
  tulis_state_prod toko pratinjau
  printf 'sandi-situs' > "$S/etc/prod/db/toko"
  wadah_prod wpp-toko situs:toko
  printf '%s\n' "$HTPASSWD" > "$S/hosting/router/toko.htpasswd"
  mkdir -p "$S/hcerts/$DOM"
  chmod 0700 "$S/hcerts/$DOM"
  printf 'rantai' > "$S/hcerts/$DOM/fullchain.pem"
  printf 'kunci' > "$S/hcerts/$DOM/privkey.pem"
  printf 'ASLI' > "$S/hosting/$ID/files/wp-config.php"
}

@test "argumen prod-domain, prod-sertifikat, prod-aktifkan, prod-hapus divalidasi" {
  aktifkan_hosting
  run "$SKRIP" prod-domain Toko
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-sertifikat ""
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-aktifkan toko lain
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-hapus "../x"
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-domain belum-ada
  [ "$status" -eq 3 ]
  [ -z "$(docker_log)" ]
  [ ! -e "$PALSU/nginx.log" ]
}

@test "prod-domain menulis berkas pratinjau port 80 dengan ACME, tanpa default_server dan http2, di bawah flock" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 0 ]
  f="$(NGF)"
  grep -qxF '    listen 80;' "$f"
  grep -qxF '    listen [::]:80;' "$f"
  grep -qxF "    server_name $DOM www.$DOM;" "$f"
  grep -qxF '    location ^~ /.well-known/acme-challenge/ {' "$f"
  grep -qxF "        root $S/acme;" "$f"
  grep -qxF '        return 301 https://$host$request_uri;' "$f"
  ! grep -q 'listen 443\|default_server\|http2' "$f" || false
  [ "$(stat -c %a "$f")" = 644 ]
  [ -z "$(ls -A "$S/nginx-hosting" | grep '^\.' || true)" ]
  grep -qxF '[-w][120][9]' "$PALSU/flock.log"
  [ "$(cat "$PALSU/nginx.log")" = "$(printf '[-T]\n[-t]')" ]
  grep -qxF '[reload][nginx]' "$PALSU/systemctl.log"
  [ -z "$(ls -A "$S/etc/prod/nginx-cadangan")" ]
}

@test "prod-domain pratinjau menambah server 443 bila sertifikat host pratinjau ada" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  mkdir -p "$S/certs/vps-toko.staging.contoh.id"
  printf 'x' > "$S/certs/vps-toko.staging.contoh.id/fullchain.pem"
  printf 'x' > "$S/certs/vps-toko.staging.contoh.id/privkey.pem"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 0 ]
  f="$(NGF)"
  grep -qxF '    listen 443 ssl;' "$f"
  grep -qxF '    listen [::]:443 ssl;' "$f"
  grep -qxF "    server_name vps-toko.staging.contoh.id $DOM www.$DOM;" "$f"
  grep -qxF "    ssl_certificate     $S/certs/vps-toko.staging.contoh.id/fullchain.pem;" "$f"
  grep -qxF "    ssl_certificate_key $S/certs/vps-toko.staging.contoh.id/privkey.pem;" "$f"
  grep -qxF '    include /etc/letsencrypt/options-ssl-nginx.conf;' "$f"
  grep -qxF '    client_max_body_size 64M;' "$f"
  grep -qxF '        proxy_pass http://127.0.0.1:8091;' "$f"
  grep -qxF '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' "$f"
  grep -qxF '        proxy_set_header X-Forwarded-Proto https;' "$f"
  ! grep -q 'default_server\|http2' "$f" || false
}

@test "prod-domain memulihkan berkas lama bila nginx -t gagal" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  printf 'LAMA' > "$(NGF)"
  touch "$PALSU/nginx-t-gagal"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 10 ]
  [[ "$output" == *"GALAT nginx"* ]]
  [ "$(cat "$(NGF)")" = "LAMA" ]
  [ ! -e "$PALSU/systemctl.log" ]
  [ -z "$(ls -A "$S/nginx-hosting" | grep '^\.' || true)" ]
}

@test "prod-domain menolak conflicting server name" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  printf 'nginx: [warn] conflicting server name "www.%s" on 0.0.0.0:80, ignored\n' "$DOM" > "$PALSU/nginx-t-peringatan"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 10 ]
  [ ! -e "$(NGF)" ]
  [ ! -e "$PALSU/systemctl.log" ]
  # Peringatan untuk nama lain tidak menyangkut domain ini.
  printf 'nginx: [warn] conflicting server name "lain.id" on 0.0.0.0:80, ignored\n' > "$PALSU/nginx-t-peringatan"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 0 ]
  [ -e "$(NGF)" ]
}

@test "prod-domain menolak nama milik berkas lain di nginx -T" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  for nama in "$DOM" "www.$DOM" ".$DOM" "*.$DOM"; do
    printf '# configuration file /etc/nginx/nginx.conf:\nhttp {\n}\n# configuration file /etc/nginx/sites-enabled/lain.conf:\nserver {\n    listen 80;\n    server_name lain.id %s;\n}\n' \
      "$nama" > "$PALSU/nginx-T"
    run "$SKRIP" prod-domain toko
    [ "$status" -eq 3 ]
    [ ! -e "$(NGF)" ]
  done
  ! grep -qxF '[-t]' "$PALSU/nginx.log" || false
  [ ! -e "$PALSU/systemctl.log" ]
}

@test "prod-domain: pra-cek tidak tertipu direktori berawalan mirip atau huruf besar" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  printf '# configuration file %s/nginx-hosting-lain/x.conf:\nserver {\n    server_name %s;\n}\n' "$S" "$DOM" > "$PALSU/nginx-T"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 3 ]
  printf '# configuration file /etc/nginx/sites-enabled/a.conf:\nserver {\n    server_name  TOKO.CO.ID ;\n}\n' > "$PALSU/nginx-T"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 3 ]
  # Berkas milik sendiri di NGINX_HOSTING_DIR dan baris komentar tidak dihitung.
  printf '# configuration file %s/nginx-hosting/%s.conf:\nserver {\n    server_name %s www.%s;\n}\n# configuration file /etc/nginx/sites-enabled/b.conf:\nserver {\n    # server_name %s;\n    server_name b.id;\n}\n' \
    "$S" "$DOM" "$DOM" "$DOM" "$DOM" > "$PALSU/nginx-T"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 0 ]
}

@test "prod-domain: reload gagal memulihkan berkas lama lalu reload lagi" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  printf 'LAMA' > "$(NGF)"
  touch "$PALSU/reload-gagal"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 10 ]
  [ "$(cat "$(NGF)")" = "LAMA" ]
  [ "$(grep -c '^\[reload\]\[nginx\]$' "$PALSU/systemctl.log")" -eq 2 ]
  [ "$(grep -c '^\[-t\]$' "$PALSU/nginx.log")" -eq 2 ]
}

@test "NGINX_UJI_SAJA=1 menguji tanpa reload" {
  aktifkan_hosting
  echo 'NGINX_UJI_SAJA=1' >> "$WPMGR_STG_KONF"
  tulis_state_prod toko pratinjau
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 0 ]
  grep -qxF '[-t]' "$PALSU/nginx.log"
  [ ! -e "$PALSU/systemctl.log" ]
}

@test "prod-domain MODE=aktif tanpa sertifikat domain ditolak" {
  aktifkan_hosting
  tulis_state_prod toko aktif
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 3 ]
  [ ! -e "$(NGF)" ]
}

@test "prod-sertifikat memasang sertifikat 0600 root:root dan melaporkan terbit, tetap, diperbarui" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 0 ]
  [ "$output" = "terbit" ]
  grep -qF "[--webroot][-w][$S/acme][-d][$DOM][-d][www.$DOM][--cert-name][$DOM][--config-dir][$S/le/config]" "$PALSU/certbot.log"
  grep -qF '[--keep-until-expiring]' "$PALSU/certbot.log"
  [ "$(cat "$S/hcerts/$DOM/fullchain.pem")" = "rantai" ]
  [ "$(stat -c %a "$S/hcerts/$DOM")" = 700 ]
  [ "$(stat -c %a "$S/hcerts/$DOM/fullchain.pem")" = 644 ]
  [ "$(stat -c %a:%u:%g "$S/hcerts/$DOM/privkey.pem")" = "600:0:0" ]
  [ ! -e "$PALSU/systemctl.log" ]
  run "$SKRIP" prod-sertifikat toko
  [ "$output" = "tetap" ]
  # Situs aktif: sertifikat yang berubah membuat nginx diuji lalu di-reload.
  printf 'lama' > "$S/hcerts/$DOM/fullchain.pem"
  sed -i 's/^MODE=.*/MODE=aktif/' "$S/etc/prod/situs/toko"
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 0 ]
  [ "$output" = "diperbarui" ]
  grep -qxF '[reload][nginx]' "$PALSU/systemctl.log"
  grep -qxF '[-t]' "$PALSU/nginx.log"
  touch "$PALSU/certbot-gagal"
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 5 ]
}

@test "prod-sertifikat SERTIFIKAT_SENDIRI memakai openssl" {
  aktifkan_hosting
  echo 'SERTIFIKAT_SENDIRI=1' >> "$WPMGR_STG_KONF"
  tulis_state_prod toko pratinjau
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 0 ]
  [ "$output" = "terbit" ]
  grep -qF "[-subj][/CN=$DOM][-addext][subjectAltName=DNS:$DOM,DNS:www.$DOM]" "$PALSU/openssl.log"
  [ "$(cat "$S/hcerts/$DOM/fullchain.pem")" = "rantai-sendiri" ]
  [ ! -e "$PALSU/certbot.log" ]
  run "$SKRIP" prod-sertifikat toko
  [ "$output" = "tetap" ]
}

@test "prod-aktifkan: prasyarat gagal keluar 3 tanpa perubahan apa pun" {
  siap_aktifkan
  rm -f "$S/hcerts/$DOM/privkey.pem"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 3 ]
  ln -s /etc/passwd "$S/hcerts/$DOM/privkey.pem"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 3 ]
  rm -f "$S/hcerts/$DOM/privkey.pem"
  printf 'kunci' > "$S/hcerts/$DOM/privkey.pem"
  rm -f "$PALSU/wadah/wpp-toko" "$PALSU/wadah/wpp-toko.hosting"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 3 ]
  wadah_prod wpp-toko situs:toko
  printf '# configuration file /etc/nginx/sites-enabled/lain.conf:\nserver {\n    server_name %s;\n}\n' "$DOM" > "$PALSU/nginx-T"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 3 ]
  [ "$(cat "$S/hosting/$ID/files/wp-config.php")" = "ASLI" ]
  grep -qxF 'MODE=pratinjau' "$S/etc/prod/situs/toko"
  [ ! -e "$(NGF)" ]
  [ ! -e "$S/etc/prod/router/conf.d/prd-toko.conf" ]
  ! grep -q '^\[exec\]' "$PALSU/docker.log" || false
  [ ! -e "$PALSU/systemctl.log" ]
}

@test "prod-aktifkan menulis wp-config aktif, router aktif, nginx aktif, lalu MODE=aktif, dan idempoten" {
  siap_aktifkan
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 0 ]
  [ "$output" = "aktif" ]
  cfg="$S/hosting/$ID/files/wp-config.php"
  grep -qF "define( 'DB_NAME', 'prd_toko' );" "$cfg"
  grep -qF "define( 'DB_PASSWORD', 'sandi-situs' );" "$cfg"
  ! grep -q 'WPMGR_PRATINJAU\|DISABLE_WP_CRON\|WP_HOME' "$cfg" || false
  r="$S/etc/prod/router/conf.d/prd-toko.conf"
  grep -qxF "    server_name $DOM www.$DOM;" "$r"
  ! grep -q 'auth_basic\|X-Robots-Tag\|vps-' "$r" || false
  f="$(NGF)"
  grep -qxF "    server_name $DOM www.$DOM;" "$f"
  grep -qxF "    ssl_certificate     $S/hcerts/$DOM/fullchain.pem;" "$f"
  grep -qxF "    ssl_certificate_key $S/hcerts/$DOM/privkey.pem;" "$f"
  ! grep -q 'vps-' "$f" || false
  grep -qxF 'MODE=aktif' "$S/etc/prod/situs/toko"
  grep -qxF 'PREFIX=wp_' "$S/etc/prod/situs/toko"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 0 ]
  grep -qxF 'MODE=aktif' "$S/etc/prod/situs/toko"
}

@test "prod-aktifkan: penolakan sesudah perubahan pertama bukan kode 3" {
  siap_aktifkan
  # Router produksi bukan milik hosting: langkah (2) ditolak sesudah wp-config diubah.
  rm -f "$PALSU/wadah/wpmgr-prod-router.hosting"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 9 ]
  [[ "$output" == *"GALAT internal"* ]]
  ! grep -q 'WPMGR_PRATINJAU' "$S/hosting/$ID/files/wp-config.php" || false
  [ "$(cat "$S/hosting/$ID/files/wp-config.php")" != "ASLI" ]
  grep -qxF 'MODE=pratinjau' "$S/etc/prod/situs/toko"
}

@test "prod-hapus menolak MODE=aktif" {
  siap_aktifkan
  sed -i 's/^MODE=.*/MODE=aktif/' "$S/etc/prod/situs/toko"
  printf 'AKTIF' > "$(NGF)"
  run "$SKRIP" prod-hapus toko
  [ "$status" -eq 3 ]
  [ "$(cat "$(NGF)")" = "AKTIF" ]
  [ -e "$S/etc/prod/situs/toko" ]
  ! grep -q '^\[rm\]\|^\[exec\]' "$PALSU/docker.log" || false
}

@test "prod-hapus menghapus container, database, router, berkas nginx domain, dan state" {
  siap_aktifkan
  printf 'PRATINJAU' > "$(NGF)"
  printf 'x' > "$S/etc/prod/router/conf.d/prd-toko.conf"
  printf 'x' > "$S/etc/prod/router/htpasswd/toko"
  run "$SKRIP" prod-hapus toko
  [ "$status" -eq 0 ]
  grep -qxF "[rm][-f][wpp-toko]" "$PALSU/docker.log"
  [[ "$(cat "$PALSU/stdin-2")" == *"DROP DATABASE IF EXISTS \`prd_toko\`; DROP USER IF EXISTS 'prd_toko'@'%';"* ]]
  [ ! -e "$(NGF)" ]
  [ ! -e "$S/etc/prod/router/conf.d/prd-toko.conf" ]
  [ ! -e "$S/etc/prod/router/htpasswd/toko" ]
  [ ! -e "$S/etc/prod/db/toko" ]
  [ ! -e "$S/etc/prod/situs/toko" ]
  grep -qxF '[-t]' "$PALSU/nginx.log"
  grep -qxF '[reload][nginx]' "$PALSU/systemctl.log"
  grep -qxF "[exec][wpmgr-prod-router][nginx][-s][reload]" "$PALSU/docker.log"
}
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: perintah bats. Expected: 72 test sebelumnya `ok`; 17 test baru `not ok` (`GALAT argumen: subperintah tidak dikenal`).

- [ ] **Step 4: Implementasikan.** Semua di `deploy/staging/wpmgr-staging`.

(a) Sesudah baris `WAKTU_SERTIFIKAT=360   # TIMEOUT_SERTIFIKAT 300 + 60`, tambahkan:

```bash
WAKTU_AKTIFKAN=360     # TIMEOUT_AKTIFKAN 300 + 60
```

Sesudah deklarasi `ROUTER_AWALAN="stg-"` (Task 2), tambahkan:

```bash
# Berkas nginx domain yang sedang diganti dan salinan lamanya (kosong = tidak
# ada berkas lama); dipulihkan jebakan EXIT selama NGINX_TUJUAN terisi.
NGINX_TUJUAN=""
NGINX_CADANGAN=""
# prod-aktifkan: 1 sesudah perubahan pertama (Koreksi #5).
SUDAH_BERUBAH=0
```

(b) Di `galat()`, tepat sesudah `shift`, tambahkan:

```bash
  # prod-aktifkan: sesudah perubahan pertama, "ditolak" tidak lagi berarti
  # "tanpa perubahan" (kontrak keluar 3 yang dipakai dashboard untuk
  # menghapus penanda tulis-lebih-dulu), jadi dilaporkan sebagai internal.
  if [[ "$kode" == ditolak && "$SUDAH_BERUBAH" == 1 ]]; then
    kode=internal
  fi
```

(c) Di `bersihkan()`, tepat sesudah blok `if [[ -n "$ROUTER_CADANGAN" ]]; then ... fi`, tambahkan:

```bash
  if [[ -n "$NGINX_TUJUAN" ]]; then
    pulihkan_nginx
  fi
```

(d) Di bagian produksi, sesudah `cmd_prod_router_muat()`, tambahkan:

```bash
# ---- nginx host per domain (spec §7.4) ------------------------------------------

# Semua perubahan nginx host diserialkan satu kunci (flock pada fd 9; kunci
# dilepas saat skrip keluar).
kunci_nginx() {
  exec 9>>"$KONF_PROD/nginx.lock"
  flock -w 120 9 || galat nginx "konfigurasi nginx sedang diubah proses lain"
}

# nginx -t dengan stderr ditangkap. Gagal bila kode keluar bukan 0 ATAU ada
# peringatan "conflicting server name" untuk domain ini: peringatan itu tidak
# menggagalkan nginx -t, tetapi berarti salah satu server diam-diam diabaikan.
uji_nginx() {
  local domain="$1" keluaran rc=0
  keluaran="$(dibatasi nginx -t 2>&1)" || rc=$?
  (( rc == 0 )) || return 1
  if [[ -n "$domain" ]] && grep -qiF -e "conflicting server name \"$domain\"" \
       -e "conflicting server name \"www.$domain\"" <<< "$keluaran"; then
    return 1
  fi
  return 0
}

muat_ulang_nginx() {
  if [[ "$NGINX_UJI_SAJA" == 1 ]]; then
    return 0
  fi
  dibatasi systemctl reload nginx >/dev/null 2>&1
}

pulihkan_nginx() {
  if [[ -n "$NGINX_CADANGAN" ]]; then
    cp -f "$NGINX_CADANGAN" "$NGINX_TUJUAN" 2>/dev/null || true
  else
    rm -f "$NGINX_TUJUAN" 2>/dev/null || true
  fi
  NGINX_TUJUAN=""
  NGINX_CADANGAN=""
}

# Pra-cek kepemilikan nama (spec §7.4 langkah 1): domain yang sudah dilayani
# berkas lain di nginx host (di luar NGINX_HOSTING_DIR) tidak pernah direbut.
# Penanda `# configuration file <path>:` dari nginx -T menentukan berkas
# asal; perbandingan prefiks memakai "/" di akhir supaya direktori berawalan
# mirip (wpmgr-hosting-lain/) tidak dianggap milik sendiri (RFP5).
pra_cek_nama() {
  local domain="$1" konfig hasil
  konfig="$(dibatasi nginx -T 2>/dev/null)" || galat nginx "konfigurasi nginx host tidak terbaca"
  hasil="$(awk -v dir="$NGINX_HOSTING_DIR/" -v d="$domain" '
    /^# configuration file / { f = $4; sub(/:$/, "", f); luar = (index(f, dir) != 1); next }
    luar {
      baris = tolower($0)
      sub(/#.*/, "", baris)
      if (baris !~ /^[ \t]*server_name[ \t]/) next
      sub(/^[ \t]*server_name[ \t]+/, "", baris)
      sub(/;.*/, "", baris)
      n = split(baris, nama, /[ \t]+/)
      for (i = 1; i <= n; i++) {
        x = nama[i]
        if (x == d || x == "www." d || x == "." d || x == "*." d) { print "bentrok"; exit }
      }
    }' <<< "$konfig")"
  [[ "$hasil" != bentrok ]] || galat ditolak "domain sudah dilayani konfigurasi nginx lain di server ini"
}

# Template tetap berkas nginx domain (spec §7.4). Port 80 melayani tantangan
# ACME sejak pratinjau. Server 443: mode aktif dengan sertifikat domain (path
# tetap, dibaca master saat reload); mode pratinjau hanya bila sertifikat
# host pratinjau sudah ada, dengan domain asli ikut supaya pratinjau lewat
# berkas hosts berfungsi. Tanpa default_server dan tanpa http2 (opsi per
# soket di nginx 1.24). Memakai S_*.
konf_domain() {
  local nama="$1" mode="$2" nama80="$S_DOMAIN" nama443 sert kunci pratinjau="vps-$1.$DOMAIN"
  if [[ "$S_WWW" == 1 ]]; then
    nama80+=" www.$S_DOMAIN"
  fi
  cat <<KONF
# Dibuat wpmgr-staging prod-domain $nama ($mode). Jangan diedit; selalu ditimpa.
server {
    listen 80;
    listen [::]:80;
    server_name $nama80;
    location ^~ /.well-known/acme-challenge/ {
        root $ACME_DIR;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / {
        return 301 https://\$host\$request_uri;
    }
}
KONF
  if [[ "$mode" == aktif ]]; then
    sert="$PROD_CERT_DIR/$S_DOMAIN/fullchain.pem"
    kunci="$PROD_CERT_DIR/$S_DOMAIN/privkey.pem"
    nama443="$nama80"
  elif [[ -f "$CERT_DIR/$pratinjau/fullchain.pem" && -f "$CERT_DIR/$pratinjau/privkey.pem" ]]; then
    sert="$CERT_DIR/$pratinjau/fullchain.pem"
    kunci="$CERT_DIR/$pratinjau/privkey.pem"
    nama443="$pratinjau $nama80"
  else
    return 0
  fi
  cat <<KONF
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $nama443;
    ssl_certificate     $sert;
    ssl_certificate_key $kunci;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    client_max_body_size 64M;
    location / {
        proxy_pass http://$PROD_ROUTER_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
KONF
}

# Prosedur aman spec §7.4 langkah 1-6, di bawah kunci_nginx. Setiap kegagalan
# meninggalkan berkas yang sama dengan sebelum perintah, dan konfigurasi yang
# berjalan tidak pernah dimuat ulang dengan berkas yang gagal diuji.
pasang_domain() {
  local nama="$1" mode="$2" tujuan baru cadangan
  tujuan="$NGINX_HOSTING_DIR/$S_DOMAIN.conf"
  # Nama sementara tidak cocok dengan glob include *.conf.
  baru="$NGINX_HOSTING_DIR/.$S_DOMAIN.baru"
  cadangan="$KONF_PROD/nginx-cadangan/$S_DOMAIN.conf"
  cek_mount_root "$NGINX_HOSTING_DIR" d "$(dirname -- "$NGINX_HOSTING_DIR")"
  pra_cek_nama "$S_DOMAIN"
  SEMENTARA+=("$baru")
  konf_domain "$nama" "$mode" > "$baru"
  chmod 0644 "$baru"
  install -d -m 0700 "$KONF_PROD/nginx-cadangan"
  NGINX_CADANGAN=""
  if [[ -f "$tujuan" ]]; then
    cp -f "$tujuan" "$cadangan"
    NGINX_CADANGAN="$cadangan"
  fi
  NGINX_TUJUAN="$tujuan"
  mv -fT "$baru" "$tujuan"
  uji_nginx "$S_DOMAIN" || galat nginx "konfigurasi nginx domain ditolak; berkas lama dipulihkan"
  if ! muat_ulang_nginx; then
    pulihkan_nginx
    uji_nginx "" || true
    muat_ulang_nginx || true
    galat nginx "nginx host tidak dapat dimuat ulang; berkas lama dipulihkan"
  fi
  rm -f "$cadangan"
  NGINX_TUJUAN=""
  NGINX_CADANGAN=""
}

# Menghapus berkas nginx domain (prod-hapus) dengan uji dan reload yang sama.
hapus_domain() {
  local tujuan="$NGINX_HOSTING_DIR/$1.conf" cadangan="$KONF_PROD/nginx-cadangan/$1.conf"
  [[ -f "$tujuan" ]] || return 0
  install -d -m 0700 "$KONF_PROD/nginx-cadangan"
  cp -f "$tujuan" "$cadangan"
  NGINX_TUJUAN="$tujuan"
  NGINX_CADANGAN="$cadangan"
  rm -f "$tujuan"
  uji_nginx "" || galat nginx "nginx -t gagal sesudah berkas domain dihapus; berkas dipulihkan"
  if ! muat_ulang_nginx; then
    pulihkan_nginx
    muat_ulang_nginx || true
    galat nginx "nginx host tidak dapat dimuat ulang; berkas dipulihkan"
  fi
  rm -f "$cadangan"
  NGINX_TUJUAN=""
  NGINX_CADANGAN=""
}

# Sertifikat domain terpasang: berkas biasa milik root (bukan symlink).
sertifikat_domain_ada() {
  local d="$PROD_CERT_DIR/$S_DOMAIN" f
  for f in "$d/fullchain.pem" "$d/privkey.pem"; do
    [[ -f "$f" && ! -L "$f" ]] || return 1
    [[ "$(stat -c %u -- "$f")" == 0 ]] || return 1
  done
  return 0
}

cmd_prod_domain() {
  cek_nama_prod "${1-}"
  butuh_hosting
  muat_state "$1" || galat ditolak "situs belum dibuat (prod-buat)"
  if [[ "$S_MODE" == aktif ]]; then
    sertifikat_domain_ada || galat ditolak "sertifikat domain belum ada"
  fi
  kunci_nginx
  pasang_domain "$1" "$S_MODE"
}

# ---- sertifikat domain -----------------------------------------------------------

# HTTP-01 webroot untuk domain dan www (spec §8.4). LE_DIR dipakai bersama
# staging; nama sertifikat = domain, jadi tidak bertabrakan dengan staging.
# Kunci privat 0600 root:root (path tetap, dibaca master nginx).
cmd_prod_sertifikat() {
  cek_nama_prod "${1-}"
  butuh_hosting
  local nama="$1" domain d sementara hasil san
  local email=(--register-unsafely-without-email) nama_host=()
  muat_state "$nama" || galat ditolak "situs belum dibuat (prod-buat)"
  domain="$S_DOMAIN"
  for d in "$PROD_CERT_DIR" "$ACME_DIR" "$LE_DIR"; do
    cek_mount_root "$d" d "$(dirname -- "$d")"
  done
  sementara="$(mktemp -d)"
  SEMENTARA+=("$sementara")
  if [[ "$SERTIFIKAT_SENDIRI" == 1 ]]; then
    if [[ -f "$PROD_CERT_DIR/$domain/fullchain.pem" ]]; then
      echo "tetap"
      return 0
    fi
    san="DNS:$domain"
    if [[ "$S_WWW" == 1 ]]; then
      san+=",DNS:www.$domain"
    fi
    dibatasi openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=$domain" \
      -addext "subjectAltName=$san" -keyout "$sementara/privkey.pem" -out "$sementara/fullchain.pem" \
      >/dev/null 2>&1 || galat sertifikat "sertifikat uji tidak dapat dibuat"
  else
    if [[ -n "$ACME_EMAIL" ]]; then
      email=(-m "$ACME_EMAIL")
    fi
    nama_host=(-d "$domain")
    if [[ "$S_WWW" == 1 ]]; then
      nama_host+=(-d "www.$domain")
    fi
    dibatasi certbot certonly --non-interactive --agree-tos "${email[@]}" --webroot -w "$ACME_DIR" \
      "${nama_host[@]}" --cert-name "$domain" --config-dir "$LE_DIR/config" --work-dir "$LE_DIR/work" \
      --logs-dir "$LE_DIR/logs" --keep-until-expiring >/dev/null 2>&1 \
      || galat sertifikat "sertifikat domain belum dapat diterbitkan"
    cp -L "$LE_DIR/config/live/$domain/fullchain.pem" "$sementara/fullchain.pem"
    cp -L "$LE_DIR/config/live/$domain/privkey.pem" "$sementara/privkey.pem"
  fi
  hasil=terbit
  if [[ -f "$PROD_CERT_DIR/$domain/fullchain.pem" ]]; then
    if cmp -s "$sementara/fullchain.pem" "$PROD_CERT_DIR/$domain/fullchain.pem"; then
      hasil=tetap
    else
      hasil=diperbarui
    fi
  fi
  if [[ "$hasil" != tetap ]]; then
    install -d -o root -g root -m 0700 "$PROD_CERT_DIR/$domain"
    cek_mount_root "$PROD_CERT_DIR/$domain" d "$PROD_CERT_DIR"
    install -o root -g root -m 0644 "$sementara/fullchain.pem" "$PROD_CERT_DIR/$domain/fullchain.pem"
    install -o root -g root -m 0600 "$sementara/privkey.pem" "$PROD_CERT_DIR/$domain/privkey.pem"
    if [[ "$S_MODE" == aktif ]]; then
      kunci_nginx
      uji_nginx "$domain" || galat nginx "nginx -t gagal dengan sertifikat baru"
      muat_ulang_nginx || galat nginx "nginx host tidak dapat dimuat ulang"
    fi
  fi
  echo "$hasil"
}

# ---- aktivasi -----------------------------------------------------------------

# Spec §7.3.3 prod-aktifkan. Prasyarat (keluar 3, pasti tanpa perubahan):
# state, database, container, sertifikat domain, dan pra-cek nama nginx.
# Lalu berurutan dan idempoten: (1) wp-config aktif, (2) router aktif +
# reload, (3) nginx domain aktif (gagal = berkas lama dipulihkan), (4)
# MODE=aktif. Sesudah (1), penolakan apa pun bukan lagi kode 3.
cmd_prod_aktifkan() {
  cek_nama_prod "${1-}"
  butuh_hosting
  local nama="$1"
  muat_state "$nama" || galat ditolak "situs belum dibuat (prod-buat)"
  [[ -n "$S_PREFIX" && -s "$KONF_PROD/db/$nama" ]] || galat ditolak "database situs belum dibuat"
  pastikan_milik "wpp-$nama" "situs:$nama" wpmgr.hosting || galat ditolak "container situs belum dibuat"
  sertifikat_domain_ada || galat ditolak "sertifikat domain belum ada"
  kunci_nginx
  pra_cek_nama "$S_DOMAIN"
  SUDAH_BERUBAH=1
  tulis_wp_config wp_config_prod "$HOSTING_DIR/$S_SITE_ID/files" "$nama" aktif
  muat_router_prod "$nama"
  # muat_router_prod memuat state setiap situs (S_*); muat ulang milik situs ini.
  muat_state "$nama" || galat internal "state situs hilang"
  pasang_domain "$nama" aktif
  S_MODE=aktif
  tulis_state "$nama"
  echo "aktif"
}

# ---- hapus situs produksi (batal pindah) ---------------------------------------

cmd_prod_hapus() {
  cek_nama_prod "${1-}"
  butuh_hosting
  local nama="$1" db
  if muat_state "$nama"; then
    # Situs yang sudah dilayani VPS dilepas manual (README), tidak lewat dashboard.
    [[ "$S_MODE" != aktif ]] || galat ditolak "situs sudah aktif; lepas manual (README)"
    kunci_nginx
    hapus_domain "$S_DOMAIN"
  fi
  if pastikan_milik "wpp-$nama" "situs:$nama" wpmgr.hosting; then
    dk rm -f "wpp-$nama" >/dev/null || galat docker "container situs tidak dapat dihapus"
  fi
  db="$(nama_db_prod "$nama")"
  sql_root "DROP DATABASE IF EXISTS \`$db\`; DROP USER IF EXISTS '$db'@'%';" wpmgr-prod-db "$KONF_PROD/db-root"
  rm -f "$KONF_PROD/db/$nama" "$KONF_PROD/router/conf.d/prd-$nama.conf" "$KONF_PROD/router/htpasswd/$nama"
  if pastikan_milik wpmgr-prod-router layanan:router wpmgr.hosting; then
    dk exec wpmgr-prod-router nginx -s reload >/dev/null 2>&1 || true
  fi
  rm -f "$KONF_PROD/situs/$nama"
}
```

(e) Di `utama()`:
- blok jumlah argumen: ganti baris `jalan|jeda|hapus|db-hapus|db-impor|sertifikat|prod-jalan|prod-db-impor)` dengan `jalan|jeda|hapus|db-hapus|db-impor|sertifikat|prod-jalan|prod-db-impor|prod-domain|prod-sertifikat|prod-aktifkan|prod-hapus)`;
- blok tenggat: ganti `sertifikat) atur_tenggat "$WAKTU_SERTIFIKAT" ;;` dengan dua baris `sertifikat|prod-sertifikat) atur_tenggat "$WAKTU_SERTIFIKAT" ;;` dan `prod-aktifkan) atur_tenggat "$WAKTU_AKTIFKAN" ;;`;
- blok dispatch: sesudah `prod-router-muat) cmd_prod_router_muat ;;` tambahkan:

```bash
    prod-domain) cmd_prod_domain "$@" ;;
    prod-sertifikat) cmd_prod_sertifikat "$@" ;;
    prod-aktifkan) cmd_prod_aktifkan "$@" ;;
    prod-hapus) cmd_prod_hapus "$@" ;;
```

- [ ] **Step 5: Jalankan bats dan shellcheck.** Run: perintah bats. Expected: `89 tests, 0 failures`. Lalu perintah shellcheck dari Global Constraints (skrip + empat tiruan baru). Expected: tanpa temuan tingkat error.

- [ ] **Step 6: Commit.**

```bash
git add deploy/staging/wpmgr-staging deploy/staging/tests/pembantu.bats deploy/staging/tests/palsu/nginx deploy/staging/tests/palsu/systemctl deploy/staging/tests/palsu/flock deploy/staging/tests/palsu/openssl
git commit -m "feat(hosting): nginx domain, sertifikat, aktivasi, dan hapus situs di skrip pembantu

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Fase B — Fondasi dashboard

### Task 4: Setelan, model, migrasi, dan validasi domain

**Files:**
- Modify: `pyproject.toml`, `.env.example`, `src/wpmgr/config.py`, `src/wpmgr/models.py`, `src/wpmgr/staging/aman.py`
- Create: `migrations/versions/e1f2a3b4c5d7_lapis4_job_type.py`, `migrations/versions/f2a3b4c5d6e8_lapis4_hosting.py`
- Test: `tests/unit/test_config.py` (tambah), `tests/unit/test_hosting_aman.py`, `tests/integration/test_models_lapis4.py`

**Interfaces:**
- Consumes: `Settings` Lapis 3 (`staging_domain`, `staging_dir`, `staging_aktif`), `aman.nama_dari_url`.
- Produces:
  - `Settings.hosting_ipv4: str | None`, `hosting_ipv6: str | None`, `hosting_dir: str`, `hosting_resolver: str`, `backup_tujuan: str`, `backup_harian: int` (1–60), `backup_mingguan: int` (0–52); properti `hosting_aktif -> bool` (`staging_aktif and bool(hosting_ipv4)`), `jalur_hosting -> Path`, `daftar_resolver -> list[str]`. Validator: `hosting_dir` tidak berimpit dengan `staging_dir` (Koreksi #12).
  - `JobType.pindah_tarik`, `JobType.pindah_aktifkan`, `JobType.backup_hosting`; `JOB_HOSTING`, `JOB_RUNTIME = JOB_STAGING | JOB_HOSTING`, `JOB_RUNTIME_BACA = JOB_STAGING_BACA | {pindah_tarik}` (semua `frozenset[JobType]`).
  - Enum `StatusHosting` (`menyalin`, `pratinjau`, `menunggu_dns`, `mengaktifkan`, `aktif`, `gagal`); model `HostingVps` (tabel `hosting_vps`) dan `HostingBackup` (tabel `hosting_backup`); indeks unik parsial `uq_jobs_hosting_aktif`.
  - `aman.POLA_NAMA_PROD` (`[a-z0-9-]{1,36}`), `aman.domain_sah(domain, staging_domain=None) -> bool`, `aman.host_dari_url(url) -> str | None` (huruf kecil, punycode), `aman.nama_prod_dari_url(url) -> str`.
  - Dependensi `dnspython>=2.6`.

- [ ] **Step 1: Dependensi.** Di `pyproject.toml`, tambahkan setelah `"bcrypt>=4.1",`:

```toml
    "dnspython>=2.6",
```

Run: `.venv/Scripts/pip install -e ".[dev]"`. Expected: berakhir dengan `Successfully installed ...` yang memuat `dnspython` (atau `Requirement already satisfied`).

- [ ] **Step 2: Tulis test unit yang gagal.**

Tambahkan ke akhir `tests/unit/test_config.py` (memakai `_env_wajib` yang sudah ada di berkas itu):

```python
def test_setelan_hosting_default_mati(monkeypatch):
    from pathlib import Path

    _env_wajib(monkeypatch)
    for nama in ("WPMGR_HOSTING_IPV4", "WPMGR_HOSTING_IPV6", "WPMGR_HOSTING_DIR", "WPMGR_HOSTING_RESOLVER",
                 "WPMGR_BACKUP_TUJUAN", "WPMGR_BACKUP_HARIAN", "WPMGR_BACKUP_MINGGUAN", "WPMGR_STAGING_DOMAIN"):
        monkeypatch.delenv(nama, raising=False)
    s = Settings(_env_file=None)
    assert s.hosting_ipv4 is None and s.hosting_ipv6 is None
    assert s.hosting_aktif is False
    assert s.jalur_hosting == Path("/var/lib/wpmgr/hosting")
    assert s.daftar_resolver == ["1.1.1.1", "8.8.8.8"]
    assert (s.backup_tujuan, s.backup_harian, s.backup_mingguan) == ("lokal", 7, 4)


def test_setelan_hosting_dari_env(monkeypatch, tmp_path):
    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", "staging.halosocia.my.id")
    monkeypatch.setenv("WPMGR_STAGING_DIR", str(tmp_path / "stg"))
    monkeypatch.setenv("WPMGR_HOSTING_IPV4", " 169.58.91.181 ")
    monkeypatch.setenv("WPMGR_HOSTING_IPV6", "2a02:c207:2347:2607:0:0:0:1")
    monkeypatch.setenv("WPMGR_HOSTING_DIR", str(tmp_path / "hosting"))
    monkeypatch.setenv("WPMGR_HOSTING_RESOLVER", "9.9.9.9, 1.0.0.1")
    s = Settings(_env_file=None)
    assert s.hosting_ipv4 == "169.58.91.181"
    assert s.hosting_ipv6 == "2a02:c207:2347:2607::1"
    assert s.hosting_aktif is True
    assert s.daftar_resolver == ["9.9.9.9", "1.0.0.1"]


def test_hosting_mati_tanpa_domain_staging(monkeypatch):
    _env_wajib(monkeypatch)
    monkeypatch.delenv("WPMGR_STAGING_DOMAIN", raising=False)
    monkeypatch.setenv("WPMGR_HOSTING_IPV4", "169.58.91.181")
    assert Settings(_env_file=None).hosting_aktif is False


@pytest.mark.parametrize("nama,nilai", [
    ("WPMGR_HOSTING_IPV4", "169.58.91"), ("WPMGR_HOSTING_IPV4", "2a02::1"), ("WPMGR_HOSTING_IPV6", "169.58.91.181"),
    ("WPMGR_HOSTING_RESOLVER", "1.1.1.1,dns.google"), ("WPMGR_HOSTING_RESOLVER", " , "),
    ("WPMGR_BACKUP_HARIAN", "0"), ("WPMGR_BACKUP_HARIAN", "61"), ("WPMGR_BACKUP_MINGGUAN", "53"),
    ("WPMGR_BACKUP_TUJUAN", "S3!"),
])
def test_setelan_hosting_tidak_sah_ditolak(monkeypatch, nama, nilai):
    from pydantic import ValidationError

    _env_wajib(monkeypatch)
    monkeypatch.setenv(nama, nilai)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("staging,hosting", [
    ("/var/lib/wpmgr/staging", "/var/lib/wpmgr/staging"),
    ("/var/lib/wpmgr/staging", "/var/lib/wpmgr/staging/hosting"),
    ("/var/lib/wpmgr/staging", "/var/lib/wpmgr"),
    ("/var/lib/wpmgr/staging", "/var/lib/wpmgr/staging/"),
])
def test_hosting_dir_berimpit_dengan_staging_dir_ditolak(monkeypatch, staging, hosting):
    from pydantic import ValidationError

    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DIR", staging)
    monkeypatch.setenv("WPMGR_HOSTING_DIR", hosting)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_hosting_dir_berawalan_sama_tetapi_terpisah_diterima(monkeypatch):
    _env_wajib(monkeypatch)
    monkeypatch.setenv("WPMGR_STAGING_DIR", "/var/lib/wpmgr/staging")
    monkeypatch.setenv("WPMGR_HOSTING_DIR", "/var/lib/wpmgr/staging-hosting")
    assert Settings(_env_file=None).hosting_dir == "/var/lib/wpmgr/staging-hosting"
```

File: `tests/unit/test_hosting_aman.py`
```python
import pytest

from wpmgr.staging.aman import domain_sah, host_dari_url, nama_prod_dari_url

STG = "staging.halosocia.my.id"


@pytest.mark.parametrize("domain", [
    "dutamakmurabadi.com", "scaffoldingsurabayamurah.com", "rizkycahayaraya.com", "toko.co.id",
    "xn--bcher-kva.de", "a-b.c-d.id", "halosocia.my.id",
])
def test_domain_sah_diterima(domain):
    assert domain_sah(domain, STG)


@pytest.mark.parametrize("domain", [
    "Toko.co.id", "toko.co.id.", "toko.co.id\n", "toko..co.id", "-toko.co.id", "toko-.co.id", "toko",
    "toko.c", "toko.1d", "toko_x.co.id", "www.toko.co.id", "a" * 64 + ".id",
    ".".join(["a" * 63] * 4) + ".id", STG, "vps-x." + STG, "", None, 123, "toko.co.id/", "toko co.id",
])
def test_domain_sah_ditolak(domain):
    assert not domain_sah(domain, STG)


def test_domain_sah_tanpa_domain_staging():
    assert domain_sah("staging.halosocia.my.id")


@pytest.mark.parametrize("url,host", [
    ("https://Toko.CO.id/", "toko.co.id"),
    ("https://www.toko.co.id", "www.toko.co.id"),
    ("https://bücher.de/", "xn--bcher-kva.de"),
    ("https://toko.co.id:8443/x", "toko.co.id"),
    ("bukan url", None),
    ("https://", None),
    ("https://" + "a" * 70 + ".id", None),
])
def test_host_dari_url(url, host):
    assert host_dari_url(url) == host


@pytest.mark.parametrize("url,nama", [
    ("https://www.toko.co.id", "toko-co-id"),
    ("https://scaffoldingsurabayamurah.com", "scaffoldingsurabayamurah-com"),
    ("https://" + "a" * 40 + ".com", "a" * 36),
    ("https://abcdefghijklmnopqrstuvwxyz-0123456789.com", "abcdefghijklmnopqrstuvwxyz-012345678"),
])
def test_nama_prod_dari_url(url, nama):
    hasil = nama_prod_dari_url(url)
    assert hasil == nama
    assert len(hasil) <= 36 and not hasil.endswith("-")
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_config.py tests/unit/test_hosting_aman.py -q`. Expected: `ImportError: cannot import name 'domain_sah' from 'wpmgr.staging.aman'` dan test konfigurasi gagal dengan `AttributeError: 'Settings' object has no attribute 'hosting_ipv4'` / `DID NOT RAISE`.

- [ ] **Step 4: Implementasikan setelan.** Di `src/wpmgr/config.py`:

Ganti blok impor di puncak:

```python
import ipaddress
import os
import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
```

Sesudah field `staging_mailpit_url`, tambahkan:

```python
    # Lapis 4 (spec §14). IPv4 VPS kosong mematikan fitur hosting VPS; fitur
    # juga butuh WPMGR_STAGING_DOMAIN (host pratinjau vps-<nama>.<domain staging>).
    hosting_ipv4: str | None = Field(default=None, validation_alias="WPMGR_HOSTING_IPV4")
    hosting_ipv6: str | None = Field(default=None, validation_alias="WPMGR_HOSTING_IPV6")
    hosting_dir: str = Field(default="/var/lib/wpmgr/hosting", validation_alias="WPMGR_HOSTING_DIR")
    hosting_resolver: str = Field(default="1.1.1.1,8.8.8.8", validation_alias="WPMGR_HOSTING_RESOLVER")
    backup_tujuan: str = Field(default="lokal", validation_alias="WPMGR_BACKUP_TUJUAN")
    backup_harian: int = Field(default=7, ge=1, le=60, validation_alias="WPMGR_BACKUP_HARIAN")
    backup_mingguan: int = Field(default=4, ge=0, le=52, validation_alias="WPMGR_BACKUP_MINGGUAN")
```

Sesudah validator `_kosong_jadi_none`, tambahkan:

```python
    @field_validator("hosting_ipv4", mode="before")
    @classmethod
    def _ipv4_hosting(cls, v):
        teks = "" if v is None else str(v).strip()
        if not teks:
            return None
        try:
            return str(ipaddress.IPv4Address(teks))
        except ValueError:
            raise ValueError("WPMGR_HOSTING_IPV4 bukan alamat IPv4 yang sah") from None

    @field_validator("hosting_ipv6", mode="before")
    @classmethod
    def _ipv6_hosting(cls, v):
        teks = "" if v is None else str(v).strip()
        if not teks:
            return None
        try:
            return ipaddress.IPv6Address(teks).compressed
        except ValueError:
            raise ValueError("WPMGR_HOSTING_IPV6 bukan alamat IPv6 yang sah") from None

    @field_validator("hosting_resolver")
    @classmethod
    def _resolver(cls, v: str) -> str:
        # Alamat resolver publik dipakai langsung sebagai nameserver dnspython;
        # nama host di sini akan butuh resolver lain untuk diselesaikan.
        bagian = [b.strip() for b in v.split(",") if b.strip()]
        if not 1 <= len(bagian) <= 5:
            raise ValueError("WPMGR_HOSTING_RESOLVER harus berisi 1-5 alamat IP")
        try:
            return ",".join(str(ipaddress.ip_address(b)) for b in bagian)
        except ValueError:
            raise ValueError("WPMGR_HOSTING_RESOLVER hanya boleh berisi alamat IP") from None

    @field_validator("backup_tujuan")
    @classmethod
    def _tujuan_backup(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9]{1,20}", v):
            raise ValueError("WPMGR_BACKUP_TUJUAN tidak sah")
        return v

    @model_validator(mode="after")
    def _dir_terpisah(self):
        # Koreksi #12 (RFP1): pemangkasan staging menghapus setiap direktori
        # UUID di WPMGR_STAGING_DIR tanpa baris Staging. Situs produksi tidak
        # boleh pernah berada di pohon itu, dan sebaliknya.
        h = os.path.normpath(self.hosting_dir)
        s = os.path.normpath(self.staging_dir)
        if h == s or h.startswith(s + os.sep) or s.startswith(h + os.sep):
            raise ValueError("WPMGR_HOSTING_DIR tidak boleh berimpit dengan WPMGR_STAGING_DIR")
        return self
```

Sesudah properti `jalur_staging`, tambahkan:

```python
    @property
    def hosting_aktif(self) -> bool:
        return self.staging_aktif and bool(self.hosting_ipv4)

    @property
    def jalur_hosting(self) -> Path:
        return Path(self.hosting_dir)

    @property
    def daftar_resolver(self) -> list[str]:
        return self.hosting_resolver.split(",")
```

Tambahkan ke akhir `.env.example`:

```bash

# Lapis 4 -- pindah hosting ke VPS (kosongkan WPMGR_HOSTING_IPV4 untuk mematikan fitur;
# fitur juga butuh WPMGR_STAGING_DOMAIN)
# WPMGR_HOSTING_IPV4=169.58.91.181
# WPMGR_HOSTING_IPV6=2a02:c207:2347:2607::1
# WPMGR_HOSTING_DIR=/var/lib/wpmgr/hosting
# WPMGR_HOSTING_RESOLVER=1.1.1.1,8.8.8.8
# WPMGR_BACKUP_TUJUAN=lokal
# WPMGR_BACKUP_HARIAN=7
# WPMGR_BACKUP_MINGGUAN=4
```

- [ ] **Step 5: Implementasikan validasi domain.** Di `src/wpmgr/staging/aman.py`, sesudah `POLA_SHA256`, tambahkan:

```python
# Nama situs produksi: vps-<nama> (host pratinjau) tetap <= 40 karakter.
POLA_NAMA_PROD = re.compile(r"[a-z0-9-]{1,36}")
# Sama dengan cek_domain di skrip pembantu (spec §7.3.2): label huruf kecil
# ASCII, TLD diawali huruf. Huruf besar, titik akhir, dan baris baru ditolak
# (fullmatch), bukan dinormalkan diam-diam.
POLA_DOMAIN_HOSTING = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]"
)
```

Sesudah fungsi `nama_dari_url()`, tambahkan:

```python
def domain_sah(domain, staging_domain: str | None = None) -> bool:
    """Domain situs yang boleh dipindahkan (spec §7.3.2); cermin `cek_domain` di skrip."""
    if not isinstance(domain, str) or len(domain) > 253 or not POLA_DOMAIN_HOSTING.fullmatch(domain):
        return False
    if domain.startswith("www."):
        return False
    if staging_domain and (domain == staging_domain or domain.endswith("." + staging_domain)):
        return False
    return True


def host_dari_url(url) -> str | None:
    """Host URL site dalam huruf kecil ASCII (IDN menjadi punycode), atau None."""
    try:
        host = urlsplit(url).hostname if isinstance(url, str) else None
    except ValueError:
        return None
    if not host:
        return None
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None


def nama_prod_dari_url(url: str) -> str:
    """Nama situs produksi (spec §5.1): nama staging dipotong 36 karakter."""
    return nama_dari_url(url)[:36].strip("-") or "situs"
```

- [ ] **Step 6: Jalankan test unit.** Run: `.venv/Scripts/python -m pytest tests/unit/test_config.py tests/unit/test_hosting_aman.py -q`. Expected: semua lulus.

- [ ] **Step 7: Tulis test model yang gagal.**

File: `tests/integration/test_models_lapis4.py`
```python
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    JOB_HOSTING,
    JOB_RUNTIME,
    JOB_RUNTIME_BACA,
    JOB_STAGING,
    HostingBackup,
    HostingVps,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
    StatusHosting,
)

pytestmark = pytest.mark.integration


def _site_lain(sesi):
    s = Site(id=uuid.uuid4(), nama="Lain", url=f"https://{uuid.uuid4().hex[:8]}.test",
             status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(s)
    sesi.commit()
    return s


def _hosting(site_id, nama="toko-co-id", domain="toko.co.id"):
    return HostingVps(site_id=site_id, nama=nama, domain=domain, dengan_www=True, ip_lama="93.184.216.34")


def test_himpunan_job_runtime():
    assert JOB_HOSTING == {JobType.pindah_tarik, JobType.pindah_aktifkan, JobType.backup_hosting}
    assert JOB_RUNTIME == JOB_STAGING | JOB_HOSTING
    assert JOB_RUNTIME_BACA == {JobType.staging_tarik, JobType.staging_uji_update, JobType.pindah_tarik}


def test_hosting_default(sesi, site):
    h = _hosting(site.id)
    sesi.add(h)
    sesi.commit()
    sesi.refresh(h)
    assert h.status == StatusHosting.menyalin
    assert (h.ukuran_file, h.ukuran_db, h.sertifikat_gagal_kali) == (0, 0, 0)
    assert h.dilayani_vps_pada is None and h.aktif_pada is None and h.dns_hasil is None
    assert h.dibuat_pada.tzinfo is not None


def test_satu_hosting_per_site_nama_dan_domain_unik(sesi, site):
    sesi.add(_hosting(site.id))
    sesi.commit()
    sesi.add(_hosting(site.id, nama="b", domain="b.co.id"))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()
    lain = _site_lain(sesi)
    sesi.add(_hosting(lain.id, domain="lain.co.id"))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()
    sesi.add(_hosting(lain.id, nama="lain"))
    with pytest.raises(IntegrityError):
        sesi.commit()


def test_gagal_asal_dibatasi(sesi, site):
    h = _hosting(site.id)
    h.gagal_asal = "entah"
    sesi.add(h)
    with pytest.raises(IntegrityError):
        sesi.commit()


def test_hapus_site_menghapus_hosting_dan_backup(sesi, site):
    sesi.add(_hosting(site.id))
    sesi.add(HostingBackup(site_id=site.id, tujuan="lokal", stempel="20261003T023000Z", status="tersedia",
                           manual=False, ukuran_db=1, ukuran_file=2, sha256_db="a" * 64, sha256_file="b" * 64))
    sesi.commit()
    sesi.delete(sesi.get(Site, site.id))
    sesi.commit()
    assert sesi.scalars(select(HostingVps)).all() == []
    assert sesi.scalars(select(HostingBackup)).all() == []


def test_backup_unik_per_stempel_dan_job_set_null(sesi, site):
    job = buat_job(sesi, site.id, JobType.backup_hosting)
    b = HostingBackup(site_id=site.id, job_id=job.id, tujuan="lokal", stempel="20261003T023000Z",
                      status="tersedia", manual=True, ukuran_db=1, ukuran_file=2,
                      sha256_db="a" * 64, sha256_file="b" * 64)
    sesi.add(b)
    sesi.commit()
    sesi.add(HostingBackup(site_id=site.id, tujuan="lokal", stempel="20261003T023000Z", status="tersedia",
                           manual=False, ukuran_db=1, ukuran_file=2, sha256_db="a" * 64, sha256_file="b" * 64))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()
    sesi.delete(sesi.get(Job, job.id))
    sesi.commit()
    sesi.refresh(b)
    assert b.job_id is None


def test_satu_job_hosting_aktif_per_site(sesi, site):
    buat_job(sesi, site.id, JobType.pindah_tarik)
    with pytest.raises(IntegrityError):
        buat_job(sesi, site.id, JobType.backup_hosting)
    sesi.rollback()


def test_job_hosting_dan_staging_punya_indeks_masing_masing(sesi, site):
    buat_job(sesi, site.id, JobType.pindah_tarik)
    buat_job(sesi, site.id, JobType.staging_tarik)
    buat_job(sesi, site.id, JobType.scan_site)
    assert sesi.query(Job).count() == 3


def test_job_hosting_baru_boleh_setelah_yang_lama_selesai(sesi, site):
    lama = buat_job(sesi, site.id, JobType.pindah_aktifkan)
    lama.status = JobStatus.failed
    lama.finished_at = datetime.now(timezone.utc)
    sesi.commit()
    buat_job(sesi, site.id, JobType.pindah_aktifkan)
    assert sesi.query(Job).filter(Job.tipe == JobType.pindah_aktifkan).count() == 2
```

- [ ] **Step 8: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_models_lapis4.py -q`. Expected: `ImportError: cannot import name 'JOB_HOSTING' from 'wpmgr.models'`.

- [ ] **Step 9: Implementasikan model.** Di `src/wpmgr/models.py`:

Tambahkan tiga anggota di akhir kelas `JobType`:

```python
    pindah_tarik = "pindah_tarik"
    pindah_aktifkan = "pindah_aktifkan"
    backup_hosting = "backup_hosting"
```

Sesudah definisi `JOB_STAGING_BACA`, tambahkan:

```python
# Lapis 4. Job hosting juga diproses worker staging (spec §5.1): satu kelas
# "runtime" yang diserialkan per site. pindah_tarik hanya membaca hosting
# lama, jadi boleh berjalan bersama job non-runtime di site yang sama;
# pindah_aktifkan dan backup_hosting eksklusif terhadap semuanya.
JOB_HOSTING = frozenset({JobType.pindah_tarik, JobType.pindah_aktifkan, JobType.backup_hosting})
JOB_RUNTIME = JOB_STAGING | JOB_HOSTING
JOB_RUNTIME_BACA = JOB_STAGING_BACA | frozenset({JobType.pindah_tarik})
```

Sesudah kelas `StatusStaging`, tambahkan:

```python
class StatusHosting(str, enum.Enum):
    menyalin = "menyalin"
    pratinjau = "pratinjau"
    menunggu_dns = "menunggu_dns"
    mengaktifkan = "mengaktifkan"
    aktif = "aktif"
    gagal = "gagal"
```

Di `Job.__table_args__`, tambahkan sesudah indeks `uq_jobs_staging_aktif` (masih di dalam tuple):

```python
        # Lapis 4: paling banyak satu job hosting tertunda/berjalan per site.
        Index(
            "uq_jobs_hosting_aktif", "site_id", unique=True,
            postgresql_where=text(
                "tipe IN ('pindah_tarik', 'pindah_aktifkan', 'backup_hosting') "
                "AND status IN ('pending', 'running')"
            ),
        ),
```

Tambahkan ke akhir berkas:

```python
class HostingVps(Base):
    """Satu hosting VPS per site (spec Lapis 4 §5.1)."""

    __tablename__ = "hosting_vps"
    __table_args__ = (
        CheckConstraint("gagal_asal IS NULL OR gagal_asal IN ('salinan', 'produksi')",
                        name="ck_hosting_vps_gagal_asal"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    nama: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    domain: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    dengan_www: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    status: Mapped[StatusHosting] = mapped_column(
        Enum(StatusHosting, name="status_hosting"), nullable=False,
        default=StatusHosting.menyalin, server_default="menyalin",
    )
    # 'salinan' (salinan VPS setengah jadi; site lama tetap produksi) atau
    # 'produksi' (sudah dilayani VPS). NULL bila tidak gagal.
    gagal_asal: Mapped[str | None] = mapped_column(Text)
    ip_lama: Mapped[str] = mapped_column(Text, nullable=False)
    sandi_hash: Mapped[str | None] = mapped_column(Text)
    versi_php: Mapped[str | None] = mapped_column(Text)
    ukuran_file: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    ukuran_db: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    ditarik_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pratinjau_sertifikat_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dns_dicek_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dns_hasil: Mapped[dict | None] = mapped_column(JSONB)
    sertifikat_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sertifikat_gagal_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sertifikat_gagal_kali: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Ditulis LEBIH DULU sebelum prod-aktifkan dikirim. Selama terisi, tarik
    # dan batal pindah ditolak selamanya (batas satu arah, spec §4).
    dilayani_vps_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aktif_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backup_terakhir_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backup_gagal_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    galat: Mapped[str | None] = mapped_column(Text)
    batal_diminta_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HostingBackup(Base):
    __tablename__ = "hosting_backup"
    __table_args__ = (
        Index("ix_hosting_backup_site_dibuat", "site_id", "dibuat_pada"),
        UniqueConstraint("site_id", "tujuan", "stempel", name="uq_hosting_backup_stempel"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="SET NULL"))
    tujuan: Mapped[str] = mapped_column(Text, nullable=False)
    stempel: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    ukuran_db: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    ukuran_file: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    sha256_db: Mapped[str] = mapped_column(Text, nullable=False)
    sha256_file: Mapped[str] = mapped_column(Text, nullable=False)
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 10: Migrasi nilai enum.**

File: `migrations/versions/e1f2a3b4c5d7_lapis4_job_type.py`
```python
"""lapis 4: nilai job_type hosting

Revision ID: e1f2a3b4c5d7
Revises: d0e1f2a3b4c5
Create Date: 2026-10-03 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "e1f2a3b4c5d7"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Pola a7c8d9e0f1b2: nilai enum baru tidak boleh dipakai di transaksi yang
    # sama dengan penambahannya, dan revisi berikutnya memakainya di predikat
    # indeks parsial.
    with op.get_context().autocommit_block():
        for nilai in ("pindah_tarik", "pindah_aktifkan", "backup_hosting"):
            op.execute(f"ALTER TYPE job_type ADD VALUE IF NOT EXISTS '{nilai}'")


def downgrade() -> None:
    # PostgreSQL tidak menyediakan penghapusan nilai enum.
    pass
```

- [ ] **Step 11: Migrasi tabel.**

File: `migrations/versions/f2a3b4c5d6e8_lapis4_hosting.py`
```python
"""lapis 4: tabel hosting_vps, hosting_backup, dan indeks job hosting aktif

Revision ID: f2a3b4c5d6e8
Revises: e1f2a3b4c5d7
Create Date: 2026-10-03 00:00:01.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a3b4c5d6e8"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS = ("menyalin", "pratinjau", "menunggu_dns", "mengaktifkan", "aktif", "gagal")


def upgrade() -> None:
    status_hosting = postgresql.ENUM(*STATUS, name="status_hosting", create_type=False)
    status_hosting.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "hosting_vps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("nama", sa.Text(), nullable=False, unique=True),
        sa.Column("domain", sa.Text(), nullable=False, unique=True),
        sa.Column("dengan_www", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", status_hosting, nullable=False, server_default="menyalin"),
        sa.Column("gagal_asal", sa.Text()),
        sa.Column("ip_lama", sa.Text(), nullable=False),
        sa.Column("sandi_hash", sa.Text()),
        sa.Column("versi_php", sa.Text()),
        sa.Column("ukuran_file", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ukuran_db", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ditarik_pada", sa.DateTime(timezone=True)),
        sa.Column("pratinjau_sertifikat_pada", sa.DateTime(timezone=True)),
        sa.Column("dns_dicek_pada", sa.DateTime(timezone=True)),
        sa.Column("dns_hasil", postgresql.JSONB()),
        sa.Column("sertifikat_pada", sa.DateTime(timezone=True)),
        sa.Column("sertifikat_gagal_pada", sa.DateTime(timezone=True)),
        sa.Column("sertifikat_gagal_kali", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dilayani_vps_pada", sa.DateTime(timezone=True)),
        sa.Column("aktif_pada", sa.DateTime(timezone=True)),
        sa.Column("backup_terakhir_pada", sa.DateTime(timezone=True)),
        sa.Column("backup_gagal_pada", sa.DateTime(timezone=True)),
        sa.Column("galat", sa.Text()),
        sa.Column("batal_diminta_pada", sa.DateTime(timezone=True)),
        sa.Column("dibuat_pada", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("gagal_asal IS NULL OR gagal_asal IN ('salinan', 'produksi')",
                           name="ck_hosting_vps_gagal_asal"),
    )
    op.create_table(
        "hosting_backup",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("tujuan", sa.Text(), nullable=False),
        sa.Column("stempel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("manual", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("ukuran_db", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ukuran_file", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256_db", sa.Text(), nullable=False),
        sa.Column("sha256_file", sa.Text(), nullable=False),
        sa.Column("dibuat_pada", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("site_id", "tujuan", "stempel", name="uq_hosting_backup_stempel"),
    )
    op.create_index("ix_hosting_backup_site_dibuat", "hosting_backup", ["site_id", "dibuat_pada"])
    op.create_index(
        "uq_jobs_hosting_aktif", "jobs", ["site_id"], unique=True,
        postgresql_where=sa.text(
            "tipe IN ('pindah_tarik', 'pindah_aktifkan', 'backup_hosting') "
            "AND status IN ('pending', 'running')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_hosting_aktif", table_name="jobs")
    op.drop_index("ix_hosting_backup_site_dibuat", table_name="hosting_backup")
    op.drop_table("hosting_backup")
    op.drop_table("hosting_vps")
    postgresql.ENUM(name="status_hosting").drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 12: Jalankan test model dan migrasi.** Run: `.venv/Scripts/python -m pytest tests/integration/test_models_lapis4.py -q`. Expected: `9 passed`. Lalu pada database dev: `.venv/Scripts/python -m alembic upgrade head`, `.venv/Scripts/python -m alembic downgrade d0e1f2a3b4c5`, `.venv/Scripts/python -m alembic upgrade head`. Expected: ketiganya tanpa galat; `alembic current` mencetak `f2a3b4c5d6e8 (head)`.

- [ ] **Step 13: Seluruh test unit dan integrasi, lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 14: Commit.**

```bash
git add pyproject.toml .env.example src/wpmgr/config.py src/wpmgr/models.py src/wpmgr/staging/aman.py migrations/versions/e1f2a3b4c5d7_lapis4_job_type.py migrations/versions/f2a3b4c5d6e8_lapis4_hosting.py tests/unit/test_config.py tests/unit/test_hosting_aman.py tests/integration/test_models_lapis4.py
git commit -m "feat(hosting): setelan, model, migrasi, dan validasi domain Lapis 4

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Pembungkus `prod_*`, `SiteClient` dipatok IP, dan klien hosting lama baca-saja

**Files:**
- Modify: `src/wpmgr/staging/pembantu.py`, `src/wpmgr/site_client.py`, `tests/unit/pembantu_palsu.py`
- Create: `src/wpmgr/hosting/__init__.py`, `src/wpmgr/hosting/umum.py`
- Test: `tests/unit/test_staging_pembantu.py` (tambah), `tests/unit/test_hosting_klien.py`

**Interfaces:**
- Consumes (Task 4): `aman.POLA_NAMA_PROD`, `aman.domain_sah`, `Settings.staging_domain`, `Settings.hosting_ipv4`; Lapis 3: `Pembantu.jalankan`, `_cek_id`, `_tulis_atomik`, `GalatPembantu`, `PESAN_TIDAK_TUNTAS`, `site_client.minta_bertenggat`, `site_client.buat_klien_staging`, `crypto.dekripsi_secret`, `staging.umum.galat_ditolak`.
- Produces:
  - `pembantu.KODE_KELUAR[10] == "nginx"`, `[11] == "backup"`; `PESAN_UMUM["nginx"]`, `PESAN_UMUM["backup"]`; `AKSI` untuk setiap `prod-*`; `TIMEOUT_AKTIFKAN = 300`, `TIMEOUT_BACKUP = 3 * 3600`; `HASIL_SERTIFIKAT = frozenset({"terbit", "tetap", "diperbarui"})`; `PENGGUNA_PRATINJAU = "pratinjau"`.
  - `@dataclass(frozen=True) StatusProd(mem_tersedia, disk_total, disk_bebas, backup_total, backup_bebas: int, container: dict[str, bool])`, `urai_status_prod(teks) -> StatusProd`.
  - Metode `Pembantu`: `prod_siapkan()`, `prod_buat(nama, versi_php, site_id, domain, www: bool)`, `prod_jalan(nama)`, `prod_hapus(nama)`, `prod_db_buat(nama, prefix)`, `prod_db_impor(nama, berkas: list[Path])`, `prod_router_muat()`, `prod_domain(nama)`, `prod_sertifikat(nama) -> str` (salah satu `HASIL_SERTIFIKAT`), `prod_aktifkan(nama)`, `prod_backup(nama, stempel) -> str` (stdout mentah, diurai Task 14), `prod_backup_hapus(nama, stempel)`, `prod_status() -> StatusProd`. Argumen tidak sah → `ValueError` sebelum subprocess.
  - `tulis_htpasswd_pratinjau(dir_hosting: Path, nama: str, sandi_hash: str) -> None` (menulis `<dir_hosting>/router/<nama>.htpasswd` berisi `pratinjau:<hash>\n`), `hapus_htpasswd_pratinjau(dir_hosting, nama) -> None`.
  - `SiteClient(..., alamat_tetap: str | None = None)`: bila diisi (IPv4), setiap permintaan dikirim ke `https://<ip>[:port]` dengan header `Host: <host>[:port]` dan `extensions={"sni_hostname": <host>}`; atribut publik `SiteClient.alamat_tetap`.
  - `minta_bertenggat(..., ekstensi: dict | None = None)`.
  - `hosting.umum.METODE_BACA`, `KlienLamaBacaSaja(klien)` (hanya `ping`, `staging_manifest`, `staging_file`, `staging_rentang`, `staging_tabel`, `staging_tanda_air`, properti `alamat`), `alamat_lama_sah(ip) -> bool`, `buat_http_lama() -> httpx.Client`, `klien_lama(site, hosting) -> KlienLamaBacaSaja` (`SiteError(staging_ditolak)` bila `ip_lama` tidak sah), `PESAN_IP_LAMA`.
  - `pembantu_palsu.py`: env `PALSU_STDOUT` mengganti stdout perintah apa pun.

- [ ] **Step 1: Perluas skrip pembantu tiruan unit.** Di `tests/unit/pembantu_palsu.py`, tepat sesudah blok `keluar = os.environ.get("PALSU_KELUAR") ... return int(keluar)`, tambahkan:

```python
    stdout = os.environ.get("PALSU_STDOUT")
    if stdout is not None:
        sys.stdout.write(stdout)
        return 0
```

- [ ] **Step 2: Tulis test pembungkus yang gagal.** Di `tests/unit/test_staging_pembantu.py`, tambahkan nama `AKSI`, `HASIL_SERTIFIKAT`, `StatusProd`, `hapus_htpasswd_pratinjau`, `tulis_htpasswd_pratinjau`, `urai_status_prod` ke blok `from wpmgr.staging.pembantu import (...)` yang sudah ada (urut abjad seperti isinya sekarang), lalu tambahkan ke akhir berkas:

```python
# ---- Lapis 4: subperintah prod-* -------------------------------------------------

HASH = "$2b$10$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ01234"


def test_kode_keluar_dan_aksi_hosting():
    assert KODE_KELUAR[10] == "nginx" and KODE_KELUAR[11] == "backup"
    assert PESAN_UMUM["nginx"] == "Konfigurasi nginx domain ditolak; site lain tidak terpengaruh."
    assert PESAN_UMUM["backup"] == "Backup situs gagal dibuat."
    for sub in ("prod-siapkan", "prod-buat", "prod-jalan", "prod-hapus", "prod-db-buat", "prod-db-impor",
                "prod-router-muat", "prod-domain", "prod-sertifikat", "prod-aktifkan", "prod-backup",
                "prod-backup-hapus", "prod-status"):
        assert sub in AKSI
    assert HASIL_SERTIFIKAT == {"terbit", "tetap", "diperbarui"}


def test_prod_argumen_diteruskan_persis(pembantu, catatan, tmp_path):
    sql = tmp_path / "a.sql"
    sql.write_bytes(b"SELECT 1;")
    pembantu.prod_siapkan()
    pembantu.prod_buat("toko-co-id", "8.1", ID, "toko.co.id", True)
    pembantu.prod_buat("toko-co-id", "7.4", ID, "toko.co.id", False)
    pembantu.prod_jalan("toko-co-id")
    pembantu.prod_hapus("toko-co-id")
    pembantu.prod_db_buat("toko-co-id", "wp_")
    pembantu.prod_db_impor("toko-co-id", [sql])
    pembantu.prod_router_muat()
    pembantu.prod_domain("toko-co-id")
    pembantu.prod_aktifkan("toko-co-id")
    pembantu.prod_backup("toko-co-id", "20261003T023000Z")
    pembantu.prod_backup_hapus("toko-co-id", "20261003T023000Z")
    argv = [c["argv"] for c in catatan()]
    assert argv == [
        ["prod-siapkan"],
        ["prod-buat", "toko-co-id", "8.1", ID, "toko.co.id", "1"],
        ["prod-buat", "toko-co-id", "7.4", ID, "toko.co.id", "0"],
        ["prod-jalan", "toko-co-id"],
        ["prod-hapus", "toko-co-id"],
        ["prod-db-buat", "toko-co-id", "wp_"],
        ["prod-db-impor", "toko-co-id"],
        ["prod-router-muat"],
        ["prod-domain", "toko-co-id"],
        ["prod-aktifkan", "toko-co-id"],
        ["prod-backup", "toko-co-id", "20261003T023000Z"],
        ["prod-backup-hapus", "toko-co-id", "20261003T023000Z"],
    ]
    assert catatan()[6]["stdin"] == "SELECT 1;"


@pytest.mark.parametrize("panggil", [
    lambda p: p.prod_buat("Toko", "8.1", ID, "toko.co.id", True),
    lambda p: p.prod_buat("a" * 37, "8.1", ID, "toko.co.id", True),
    lambda p: p.prod_buat("toko", "9.9", ID, "toko.co.id", True),
    lambda p: p.prod_buat("toko", "8.1", "bukan-uuid", "toko.co.id", True),
    lambda p: p.prod_buat("toko", "8.1", ID, "www.toko.co.id", True),
    lambda p: p.prod_buat("toko", "8.1", ID, "Toko.co.id", True),
    lambda p: p.prod_buat("toko", "8.1", ID, "toko.co.id\n", True),
    lambda p: p.prod_buat("toko", "8.1", ID, "toko.co.id", "1"),
    lambda p: p.prod_db_buat("toko", "wp_'; x"),
    lambda p: p.prod_backup("toko", "2026-10-03"),
    lambda p: p.prod_backup_hapus("toko", "20261003T023000Z\n"),
    lambda p: p.prod_domain("../x"),
    lambda p: p.prod_sertifikat(""),
    lambda p: p.prod_aktifkan(None),
])
def test_prod_validasi_sebelum_subprocess(pembantu, catatan, panggil):
    with pytest.raises(ValueError):
        panggil(pembantu)
    assert catatan() == []


def test_prod_domain_di_bawah_domain_staging_ditolak(pembantu, catatan, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", "staging.halosocia.my.id")
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        pembantu.prod_buat("toko", "8.1", ID, "vps-x.staging.halosocia.my.id", True)
    assert catatan() == []


@pytest.mark.parametrize("kode_keluar,kode,pesan", [
    (10, "nginx", "Memasang konfigurasi nginx domain gagal. Konfigurasi nginx domain ditolak; site lain tidak "
                  "terpengaruh."),
    (11, "backup", "Membuat backup situs gagal. Backup situs gagal dibuat."),
    (3, "ditolak", "Mengaktifkan situs gagal. Skrip pembantu menolak permintaan ini."),
])
def test_prod_kode_keluar_menjadi_pesan_tetap(pembantu, catatan, monkeypatch, kode_keluar, kode, pesan):
    monkeypatch.setenv("PALSU_KELUAR", str(kode_keluar))
    monkeypatch.setenv("PALSU_STDERR", "GALAT x: /etc/nginx/wpmgr-hosting/rahasia.conf sandi=abc")
    panggil = {"nginx": lambda: pembantu.prod_domain("toko"), "backup": lambda: pembantu.prod_backup(
        "toko", "20261003T023000Z"), "ditolak": lambda: pembantu.prod_aktifkan("toko")}[kode]
    with pytest.raises(GalatPembantu) as e:
        panggil()
    assert e.value.kode == kode
    assert e.value.pesan == pesan
    assert "/etc/nginx" not in e.value.pesan and "sandi" not in e.value.pesan


@pytest.mark.parametrize("keluaran,hasil", [("terbit\n", "terbit"), ("tetap", "tetap"), ("diperbarui\n", "diperbarui")])
def test_prod_sertifikat_hasil_tetap(pembantu, catatan, monkeypatch, keluaran, hasil):
    monkeypatch.setenv("PALSU_STDOUT", keluaran)
    assert pembantu.prod_sertifikat("toko") == hasil


@pytest.mark.parametrize("keluaran", ["TERBIT", "terbit\nlagi", "", "ok"])
def test_prod_sertifikat_keluaran_lain_ditolak(pembantu, catatan, monkeypatch, keluaran):
    monkeypatch.setenv("PALSU_STDOUT", keluaran)
    with pytest.raises(GalatPembantu):
        pembantu.prod_sertifikat("toko")


def test_status_prod_diurai_dan_disaring(pembantu, monkeypatch):
    monkeypatch.setenv("PALSU_STDOUT", json.dumps({
        "mem_tersedia": 4294967296, "disk_total": 200, "disk_bebas": 60, "backup_total": 300, "backup_bebas": 90,
        "container": {"toko": {"berjalan": True}, "lain": {"berjalan": False}, "JAHAT": {"berjalan": True},
                      "a_b": {"berjalan": True}, "b": "ya"}}))
    st = pembantu.prod_status()
    assert st == StatusProd(4294967296, 200, 60, 300, 90, {"toko": True, "lain": False})


@pytest.mark.parametrize("teks", [
    "bukan json", "[]",
    json.dumps({"mem_tersedia": 1, "disk_total": 2, "disk_bebas": 3, "backup_total": 4, "container": {}}),
    json.dumps({"mem_tersedia": -1, "disk_total": 2, "disk_bebas": 3, "backup_total": 4, "backup_bebas": 5}),
    json.dumps({"mem_tersedia": "1", "disk_total": 2, "disk_bebas": 3, "backup_total": 4, "backup_bebas": 5}),
])
def test_status_prod_rusak_ditolak(teks):
    with pytest.raises(GalatPembantu):
        urai_status_prod(teks)


def test_htpasswd_pratinjau(tmp_path):
    tulis_htpasswd_pratinjau(tmp_path, "toko-co-id", HASH)
    berkas = tmp_path / "router" / "toko-co-id.htpasswd"
    assert berkas.read_bytes() == f"pratinjau:{HASH}\n".encode()
    for nama, h in (("Toko", HASH), ("toko", "bukan-bcrypt"), ("toko", HASH + "\n"), ("a" * 37, HASH)):
        with pytest.raises(ValueError):
            tulis_htpasswd_pratinjau(tmp_path, nama, h)
    hapus_htpasswd_pratinjau(tmp_path, "toko-co-id")
    assert not berkas.exists()
    hapus_htpasswd_pratinjau(tmp_path, "toko-co-id")
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_staging_pembantu.py -q`. Expected: `ImportError: cannot import name 'HASIL_SERTIFIKAT' from 'wpmgr.staging.pembantu'` (seluruh berkas gagal dikoleksi).

- [ ] **Step 4: Implementasikan pembungkus.** Di `src/wpmgr/staging/pembantu.py`:

Ganti baris impor `from wpmgr.staging.aman import ...`:

```python
from wpmgr.staging.aman import POLA_NAMA, POLA_NAMA_PROD, VERSI_PHP, angka, bersih_teks, domain_sah, nama_sah
```

Ganti `KODE_KELUAR`, `PESAN_UMUM`, dan `AKSI`:

```python
# Cermin `galat()` di deploy/staging/wpmgr-staging.
KODE_KELUAR = {2: "argumen", 3: "ditolak", 4: "docker", 5: "sertifikat", 6: "impor",
               7: "konfigurasi", 8: "wpcli", 9: "internal", 10: "nginx", 11: "backup"}
PESAN_UMUM = {
    "argumen": "Skrip pembantu menolak argumen permintaan ini.",
    "ditolak": "Skrip pembantu menolak permintaan ini.",
    "docker": "Perintah Docker di server staging gagal.",
    "sertifikat": "Sertifikat staging belum dapat diterbitkan.",
    "impor": "Impor database staging gagal.",
    "konfigurasi": "Konfigurasi skrip pembantu di server belum lengkap.",
    "wpcli": "Perintah wp-cli di staging gagal.",
    "internal": "Skrip pembantu mengalami galat tak terduga; lihat log server.",
    "nginx": "Konfigurasi nginx domain ditolak; site lain tidak terpengaruh.",
    "backup": "Backup situs gagal dibuat.",
    "lain": "Skrip pembantu tidak dapat dijalankan (periksa pemasangan dan sudoers).",
}
# Awalan pesan UI per subperintah, supaya pengguna tahu langkah mana yang gagal
# tanpa pernah melihat stderr.
AKSI = {
    "siapkan": "Menyiapkan layanan staging",
    "buat": "Membuat container staging",
    "jalan": "Menjalankan staging",
    "jeda": "Menjeda staging",
    "hapus": "Menghapus staging",
    "db-buat": "Membuat database staging",
    "db-hapus": "Menghapus database staging",
    "db-impor": "Mengimpor database staging",
    "wpcli": "Menjalankan wp-cli di staging",
    "router-muat": "Memuat ulang router staging",
    "sertifikat": "Menerbitkan sertifikat staging",
    "status": "Membaca status server staging",
    "mail-kredensial": "Membaca kredensial kotak email staging",
    "prod-siapkan": "Menyiapkan layanan hosting",
    "prod-buat": "Membuat container situs",
    "prod-jalan": "Menjalankan situs",
    "prod-hapus": "Menghapus situs hosting",
    "prod-db-buat": "Membuat database situs",
    "prod-db-impor": "Mengimpor database situs",
    "prod-router-muat": "Memuat ulang router hosting",
    "prod-domain": "Memasang konfigurasi nginx domain",
    "prod-sertifikat": "Menerbitkan sertifikat domain",
    "prod-aktifkan": "Mengaktifkan situs",
    "prod-backup": "Membuat backup situs",
    "prod-backup-hapus": "Menghapus backup situs",
    "prod-status": "Membaca status server hosting",
}
```

Sesudah `_POLA_KREDENSIAL_MAIL`, tambahkan:

```python
_POLA_STEMPEL = re.compile(r"[0-9]{8}T[0-9]{6}Z")
# Keluaran prod-sertifikat: satu kata dari daftar tetap (spec §7.3.3).
HASIL_SERTIFIKAT = frozenset({"terbit", "tetap", "diperbarui"})
PENGGUNA_PRATINJAU = "pratinjau"
```

Sesudah `TIMEOUT_SERTIFIKAT = 300`, tambahkan:

```python
TIMEOUT_AKTIFKAN = 300
TIMEOUT_BACKUP = 3 * 3600
```

Sesudah kelas `StatusPembantu`, tambahkan:

```python
@dataclass(frozen=True)
class StatusProd:
    mem_tersedia: int
    disk_total: int
    disk_bebas: int
    backup_total: int
    backup_bebas: int
    container: dict[str, bool]
```

Sesudah `_cek_id()`, tambahkan:

```python
def _cek_nama_prod(nama) -> str:
    if not isinstance(nama, str) or not POLA_NAMA_PROD.fullmatch(nama):
        raise ValueError("Nama situs tidak sah")
    return nama


def _cek_domain(domain) -> str:
    if not domain_sah(domain, get_settings().staging_domain):
        raise ValueError("Domain tidak sah")
    return domain


def _cek_stempel(stempel) -> str:
    if not isinstance(stempel, str) or not _POLA_STEMPEL.fullmatch(stempel):
        raise ValueError("Stempel backup tidak sah")
    return stempel
```

Sesudah `urai_status()`, tambahkan:

```python
def urai_status_prod(teks: str) -> StatusProd:
    """JSON `prod-status`; angka wajib bilangan bulat >= 0, container disaring nama sah."""
    try:
        data = json.loads(teks)
    except ValueError:
        raise GalatPembantu("status", "Status server hosting tidak terbaca.") from None
    if not isinstance(data, dict):
        raise GalatPembantu("status", "Status server hosting tidak terbaca.")
    nilai = {}
    for kunci in ("mem_tersedia", "disk_total", "disk_bebas", "backup_total", "backup_bebas"):
        mentah = data.get(kunci)
        # `angka` menjepit nilai negatif ke 0; di sini nilai negatif berarti
        # keluaran rusak, jadi ditolak lebih dulu.
        if not isinstance(mentah, int) or isinstance(mentah, bool) or mentah < 0:
            raise GalatPembantu("status", "Status server hosting tidak lengkap.")
        nilai[kunci] = min(mentah, 2**53)
    container = {}
    mentah_c = data.get("container")
    for k, v in mentah_c.items() if isinstance(mentah_c, dict) else ():
        if isinstance(k, str) and POLA_NAMA_PROD.fullmatch(k) and isinstance(v, dict):
            container[k] = v.get("berjalan") is True
    return StatusProd(container=container, **nilai)
```

Di kelas `Pembantu`, sesudah `mail_kredensial()`, tambahkan:

```python
    # ---- produksi (Lapis 4, spec §7.3.3) -------------------------------------

    def prod_siapkan(self) -> str:
        return self.jalankan("prod-siapkan", timeout=TIMEOUT_SIAPKAN)

    def prod_buat(self, nama: str, versi_php: str, site_id, domain: str, www: bool) -> str:
        _cek_nama_prod(nama)
        if versi_php not in VERSI_PHP:
            raise ValueError("Versi PHP situs tidak didukung")
        if not isinstance(www, bool):
            raise ValueError("Penanda www tidak sah")
        return self.jalankan("prod-buat", nama, versi_php, _cek_id(site_id), _cek_domain(domain),
                             "1" if www else "0", timeout=TIMEOUT_BUAT)

    def prod_jalan(self, nama: str) -> str:
        return self.jalankan("prod-jalan", _cek_nama_prod(nama))

    def prod_hapus(self, nama: str) -> str:
        return self.jalankan("prod-hapus", _cek_nama_prod(nama))

    def prod_db_buat(self, nama: str, prefix: str) -> str:
        _cek_nama_prod(nama)
        if not isinstance(prefix, str) or not _POLA_PREFIX.fullmatch(prefix):
            raise ValueError("Prefix tabel tidak sah")
        return self.jalankan("prod-db-buat", nama, prefix)

    def prod_db_impor(self, nama: str, berkas: list[Path]) -> str:
        return self.jalankan("prod-db-impor", _cek_nama_prod(nama), masukan=berkas, timeout=TIMEOUT_IMPOR)

    def prod_router_muat(self) -> str:
        return self.jalankan("prod-router-muat")

    def prod_domain(self, nama: str) -> str:
        return self.jalankan("prod-domain", _cek_nama_prod(nama))

    def prod_sertifikat(self, nama: str) -> str:
        teks = self.jalankan("prod-sertifikat", _cek_nama_prod(nama), timeout=TIMEOUT_SERTIFIKAT).strip()
        if teks not in HASIL_SERTIFIKAT:
            raise GalatPembantu("lain", PESAN_TIDAK_TUNTAS)
        return teks

    def prod_aktifkan(self, nama: str) -> str:
        return self.jalankan("prod-aktifkan", _cek_nama_prod(nama), timeout=TIMEOUT_AKTIFKAN)

    def prod_backup(self, nama: str, stempel: str) -> str:
        return self.jalankan("prod-backup", _cek_nama_prod(nama), _cek_stempel(stempel), timeout=TIMEOUT_BACKUP)

    def prod_backup_hapus(self, nama: str, stempel: str) -> str:
        return self.jalankan("prod-backup-hapus", _cek_nama_prod(nama), _cek_stempel(stempel))

    def prod_status(self) -> StatusProd:
        return urai_status_prod(self.jalankan("prod-status"))
```

Sesudah `hapus_akses_router()`, tambahkan:

```python
def tulis_htpasswd_pratinjau(dir_hosting: Path, nama: str, sandi_hash: str) -> None:
    """Satu baris htpasswd pratinjau yang dibaca `prod-router-muat` (spec §10.2); divalidasi di dua sisi."""
    _cek_nama_prod(nama)
    if not isinstance(sandi_hash, str) or not _POLA_HASH.fullmatch(sandi_hash):
        raise ValueError("Hash kata sandi tidak sah")
    d = Path(dir_hosting) / "router"
    d.mkdir(parents=True, exist_ok=True)
    _tulis_atomik(d / f"{nama}.htpasswd", f"{PENGGUNA_PRATINJAU}:{sandi_hash}\n")


def hapus_htpasswd_pratinjau(dir_hosting: Path, nama: str) -> None:
    _cek_nama_prod(nama)
    (Path(dir_hosting) / "router" / f"{nama}.htpasswd").unlink(missing_ok=True)
```

- [ ] **Step 5: Jalankan test pembungkus.** Run: `.venv/Scripts/python -m pytest tests/unit/test_staging_pembantu.py -q`. Expected: semua lulus (test Lapis 3 tetap lulus).

- [ ] **Step 6: Tulis test klien yang gagal.**

File: `tests/unit/test_hosting_klien.py`
```python
import json
import ssl
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import httpx
import pytest

from wpmgr.crypto import enkripsi_secret
from wpmgr.errors import STAGING_DITOLAK, SiteError
from wpmgr.hosting import umum
from wpmgr.hosting.umum import METODE_BACA, KlienLamaBacaSaja, alamat_lama_sah, klien_lama
from wpmgr.signing import verify
from wpmgr.site_client import SiteClient

SECRET = "f" * 64
IP_LAMA = "93.184.216.34"


def _cek_tanda_tangan(r: httpx.Request):
    assert verify(SECRET, r.headers["X-Wpmgr-Signature"], r.method, r.url.path, int(r.headers["X-Wpmgr-Timestamp"]),
                  r.headers["X-Wpmgr-Nonce"], r.content)


def test_alamat_tetap_mengirim_ke_ip_dengan_host_dan_sni():
    diminta = []

    def h(r):
        diminta.append(r)
        _cek_tanda_tangan(r)
        return httpx.Response(200, json={"berkas": [], "lagi": False, "ok": True})

    k = SiteClient("https://www.toko.co.id", "s1", SECRET, client=httpx.Client(transport=httpx.MockTransport(h)),
                   alamat_tetap=IP_LAMA)
    assert k.alamat_tetap == IP_LAMA
    k.ping()
    k.staging_manifest(None, batas=10)
    for r in diminta:
        assert r.url.scheme == "https" and r.url.host == IP_LAMA
        assert r.headers["host"] == "www.toko.co.id"
        assert r.extensions["sni_hostname"] == "www.toko.co.id"
    assert diminta[1].url.path == "/wp-json/wpmgr/v1/staging/manifest"


def test_tanpa_alamat_tetap_perilaku_lama():
    diminta = []

    def h(r):
        diminta.append(r)
        return httpx.Response(200, json={"ok": True})

    SiteClient("https://toko.co.id", "s1", SECRET, client=httpx.Client(transport=httpx.MockTransport(h))).ping()
    assert diminta[0].url.host == "toko.co.id"
    assert "sni_hostname" not in diminta[0].extensions


@pytest.mark.parametrize("alamat", ["toko.co.id", "::1", "2a02::1", "1.2.3", ""])
def test_alamat_tetap_harus_ipv4(alamat):
    with pytest.raises(ValueError):
        SiteClient("https://toko.co.id", "s1", SECRET, alamat_tetap=alamat)


# ---- A1: SNI dan verifikasi nama sertifikat lewat server TLS lokal ----------------


def _pem(tmp_path, nama_berkas: str, data: bytes):
    p = tmp_path / nama_berkas
    p.write_bytes(data)
    return p


def _sertifikat(tmp_path, nama: str):
    """CA uji + sertifikat daun untuk `nama` (ekstensi lengkap, lolos mode X509 strict)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    awal = datetime.now(timezone.utc) - timedelta(hours=1)
    akhir = awal + timedelta(days=2)
    kunci_ca = ec.generate_private_key(ec.SECP256R1())
    nama_ca = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CA uji WP Manager")])
    ca = (x509.CertificateBuilder().subject_name(nama_ca).issuer_name(nama_ca)
          .public_key(kunci_ca.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(awal).not_valid_after(akhir)
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                       crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(kunci_ca.public_key()), critical=False)
          .sign(kunci_ca, hashes.SHA256()))
    kunci = ec.generate_private_key(ec.SECP256R1())
    daun = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nama)]))
            .issuer_name(nama_ca).public_key(kunci.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(awal).not_valid_after(akhir)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(nama)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(kunci_ca.public_key()), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(kunci.public_key()), critical=False)
            .sign(kunci_ca, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    return (_pem(tmp_path, f"ca-{nama}.pem", ca.public_bytes(pem)),
            _pem(tmp_path, f"daun-{nama}.pem", daun.public_bytes(pem)),
            _pem(tmp_path, f"kunci-{nama}.pem", kunci.private_bytes(
                pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())))


class _Penangan(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 -- nama metode milik http.server
        self.server.host_diterima.append(self.headers.get("Host"))
        isi = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(isi)))
        self.end_headers()
        self.wfile.write(isi)

    def log_message(self, *argumen):
        pass


class _ServerDiam(HTTPServer):
    def handle_error(self, request, client_address):
        # Jabat tangan yang sengaja ditolak klien (sertifikat salah) bukan galat test.
        pass


@contextmanager
def _server_tls(daun, kunci):
    srv = _ServerDiam(("127.0.0.1", 0), _Penangan)
    srv.host_diterima, srv.sni = [], []
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(daun), str(kunci))
    ctx.sni_callback = lambda sock, nama, c: srv.sni.append(nama)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def test_sni_memverifikasi_sertifikat_terhadap_nama_domain(tmp_path):
    ca, daun, kunci = _sertifikat(tmp_path, "nama-uji.test")
    with _server_tls(daun, kunci) as srv:
        port = srv.server_address[1]
        http = httpx.Client(verify=ssl.create_default_context(cafile=str(ca)))
        k = SiteClient(f"https://nama-uji.test:{port}", "s1", SECRET, client=http, alamat_tetap="127.0.0.1")
        assert k.ping() == {"ok": True}
        assert k.staging_tanda_air() == {"ok": True}
    assert srv.sni[:2] == ["nama-uji.test", "nama-uji.test"]
    assert srv.host_diterima == [f"nama-uji.test:{port}", f"nama-uji.test:{port}"]


def test_sertifikat_nama_lain_ditolak_walau_ip_benar(tmp_path):
    ca, daun, kunci = _sertifikat(tmp_path, "lain.test")
    with _server_tls(daun, kunci) as srv:
        port = srv.server_address[1]
        http = httpx.Client(verify=ssl.create_default_context(cafile=str(ca)))
        k = SiteClient(f"https://nama-uji.test:{port}", "s1", SECRET, client=http, alamat_tetap="127.0.0.1")
        with pytest.raises(SiteError):
            k.ping()
    assert srv.host_diterima == []


# ---- klien hosting lama ----------------------------------------------------------------


def _site_dan_hosting(ip=IP_LAMA):
    site = SimpleNamespace(url="https://www.toko.co.id", id=uuid.uuid4(), secret_terenkripsi=enkripsi_secret(SECRET))
    return site, SimpleNamespace(ip_lama=ip)


@pytest.fixture
def ipv4_vps(monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_HOSTING_IPV4", "169.58.91.181")
    get_settings.cache_clear()


def test_klien_lama_tanpa_metode_tulis(ipv4_vps):
    site, h = _site_dan_hosting()
    k = klien_lama(site, h)
    assert isinstance(k, KlienLamaBacaSaja)
    for nama in ("staging_unggah", "staging_terapkan", "staging_bersihkan", "staging_snapshot", "update",
                 "self_update", "inventory", "events", "traffic"):
        assert not hasattr(k, nama), nama
    publik = {m for m in dir(k) if not m.startswith("_")}
    assert publik == set(METODE_BACA) | {"alamat"}
    assert k.alamat == IP_LAMA


def test_klien_lama_dipatok_ke_ip_lama(ipv4_vps, monkeypatch):
    diminta = []

    def h(r):
        diminta.append(r)
        _cek_tanda_tangan(r)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(umum, "buat_http_lama", lambda: httpx.Client(transport=httpx.MockTransport(h)))
    site, hosting = _site_dan_hosting()
    klien_lama(site, hosting).ping()
    assert diminta[0].url.host == IP_LAMA
    assert diminta[0].headers["host"] == "www.toko.co.id"
    assert diminta[0].extensions["sni_hostname"] == "www.toko.co.id"


@pytest.mark.parametrize("ip", [None, "", "10.0.0.5", "127.0.0.1", "192.168.1.1", "169.58.91.181", "2a02::1",
                                "100.64.1.1", "203.0.113.5", "001.2.3.4"])
def test_alamat_lama_tidak_sah_ditolak(ipv4_vps, ip):
    assert alamat_lama_sah(ip) is False
    site, h = _site_dan_hosting(ip)
    with pytest.raises(SiteError) as e:
        klien_lama(site, h)
    assert e.value.error_class == STAGING_DITOLAK


def test_alamat_lama_publik_diterima(ipv4_vps):
    assert alamat_lama_sah(IP_LAMA) is True
```

- [ ] **Step 7: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_hosting_klien.py -q`. Expected: `ModuleNotFoundError: No module named 'wpmgr.hosting'`.

- [ ] **Step 8: Implementasikan `SiteClient` dipatok IP.** Di `src/wpmgr/site_client.py`:

Tambahkan ke blok impor standar:

```python
import ipaddress
from urllib.parse import urlencode, urlsplit
```

(ganti baris `from urllib.parse import urlencode` yang lama).

Ubah tanda tangan `minta_bertenggat` dan pemanggilan `http.stream`:

```python
def minta_bertenggat(http: httpx.Client, method: str, url: str, *, headers: dict, content: bytes | None = None,
                     timeout: float, tenggat: float, batas_byte: int, potong: bool = False,
                     periksa=None, ekstensi: dict | None = None) -> tuple[int, dict, bytes]:
```

Tambahkan satu kalimat ke akhir docstring-nya: `` `ekstensi` (mis. `sni_hostname`) diteruskan ke httpx bersama ekstensi `trace`. `` Lalu di `kerja()`, ganti baris `with http.stream(...)`:

```python
            with http.stream(method, url, content=content, headers=headers, timeout=timeout,
                             extensions={**(ekstensi or {}), "trace": trace}) as resp:
```

Ganti `SiteClient.__init__`:

```python
class SiteClient:
    def __init__(
        self, base_url: str, site_id: str, secret_hex: str, client: httpx.Client | None = None,
        klien_staging: httpx.Client | None = None, alamat_tetap: str | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("URL site wajib berskema https://")
        self.base_url = base_url.rstrip("/")
        self.site_id = site_id
        self.secret_hex = secret_hex
        self._client = client or httpx.Client(follow_redirects=False)
        # Klien yang disuntikkan (test dengan MockTransport) dipakai juga
        # untuk staging bila klien staging tidak diberikan tersendiri.
        self._klien_staging = klien_staging or client
        # Lapis 4 (spec §10.1): sesudah DNS berpindah, domain menunjuk VPS
        # sendiri. Klien hosting lama dipatok ke IP lamanya: koneksi ke IP itu,
        # tetapi header Host dan SNI tetap domain, sehingga sertifikat tetap
        # diverifikasi terhadap nama domain (asumsi A1). Path yang
        # ditandatangani tidak memuat host, jadi kontrak HMAC tidak berubah.
        self.alamat_tetap: str | None = None
        self._url_kirim = self.base_url
        self._header_host: dict[str, str] = {}
        self._ekstensi: dict[str, str] = {}
        if alamat_tetap is not None:
            ip = ipaddress.IPv4Address(alamat_tetap)
            bagian = urlsplit(self.base_url)
            host = bagian.hostname
            if not host:
                raise ValueError("URL site tanpa host")
            port = f":{bagian.port}" if bagian.port else ""
            self.alamat_tetap = str(ip)
            self._url_kirim = f"https://{ip}{port}{bagian.path}"
            self._header_host = {"Host": f"{host}{port}"}
            self._ekstensi = {"sni_hostname": host}
```

Di `_panggil`, sesudah blok `if body: headers["Content-Type"] = ...`, tambahkan `headers.update(self._header_host)`, lalu ganti pemanggilan `self._client.request(...)`:

```python
            resp = self._client.request(
                method, f"{self._url_kirim}{path}{query}", content=body or None,
                headers=headers, timeout=timeout, extensions=self._ekstensi or None,
            )
```

Di `_kirim`, tambahkan `**self._header_host,` tepat sebelum `**(header_tambahan or {}),` di kamus `headers`, lalu ganti pemanggilan `minta_bertenggat`:

```python
            return minta_bertenggat(self._staging_http, method, f"{self._url_kirim}{path}{query}",
                                    headers=headers, content=body or None, timeout=timeout, tenggat=tenggat,
                                    batas_byte=batas_byte, periksa=tolak_kompresi, ekstensi=self._ekstensi)
```

- [ ] **Step 9: Implementasikan klien hosting lama.**

File: `src/wpmgr/hosting/__init__.py`
```python
"""Hosting VPS (Lapis 4): pindah site dari shared hosting ke VPS dashboard."""
```

File: `src/wpmgr/hosting/umum.py`
```python
"""Bagian bersama hosting VPS (spec Lapis 4 §10).

Bagian ini: klien connector hosting LAMA. Ia dipatok ke IP lama (supaya
tarik ulang sesudah DNS berpindah tetap mengambil dari hosting lama, RF4)
dan hanya punya metode baca (supaya site lama tidak mungkin diubah).
"""

import ipaddress

import httpx

from wpmgr.config import get_settings
from wpmgr.crypto import dekripsi_secret
from wpmgr.site_client import SiteClient, buat_klien_staging
from wpmgr.staging import umum as stg

PESAN_IP_LAMA = ("Alamat IP hosting lama tidak sah (kosong, privat, atau sama dengan VPS); "
                 "batalkan pindah lalu mulai lagi.")
METODE_BACA = ("ping", "staging_manifest", "staging_file", "staging_rentang", "staging_tabel", "staging_tanda_air")


class KlienLamaBacaSaja:
    """Klien connector hosting lama yang hanya bisa membaca (spec §10.1).

    Metode tulis (`staging_unggah`, `staging_terapkan`, `staging_bersihkan`,
    `update`, `self_update`) sengaja tidak ada: handler hosting yang keliru
    memanggilnya gagal dengan AttributeError, bukan mengubah site lama.
    """

    def __init__(self, klien: SiteClient) -> None:
        self._klien = klien

    @property
    def alamat(self) -> str | None:
        return self._klien.alamat_tetap

    def ping(self) -> dict:
        return self._klien.ping()

    def staging_manifest(self, kursor: str | None = None, batas: int = 5000) -> dict:
        return self._klien.staging_manifest(kursor, batas=batas)

    def staging_file(self, paths: list[str]):
        return self._klien.staging_file(paths)

    def staging_rentang(self, path: str, dari: int, panjang: int):
        return self._klien.staging_rentang(path, dari, panjang)

    def staging_tabel(self, tabel: str, kursor: str | None):
        return self._klien.staging_tabel(tabel, kursor)

    def staging_tanda_air(self, posts_sejak: str | None = None, posts_maks: int | None = None) -> dict:
        return self._klien.staging_tanda_air(posts_sejak, posts_maks)


def alamat_lama_sah(ip) -> bool:
    """IPv4 publik (bukan privat/loopback/dokumentasi) yang bukan IPv4 VPS sendiri."""
    if not isinstance(ip, str):
        return False
    try:
        alamat = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    if not alamat.is_global or str(alamat) != ip:
        return False
    return ip != get_settings().hosting_ipv4


def buat_http_lama() -> httpx.Client:
    """Klien httpx untuk hosting lama: tanpa keep-alive (tenggat total, putusan F11)."""
    return buat_klien_staging()


def klien_lama(site, hosting) -> KlienLamaBacaSaja:
    if not alamat_lama_sah(hosting.ip_lama):
        raise stg.galat_ditolak(PESAN_IP_LAMA)
    http = buat_http_lama()
    klien = SiteClient(site.url, str(site.id), dekripsi_secret(site.secret_terenkripsi), client=http,
                       klien_staging=http, alamat_tetap=hosting.ip_lama)
    return KlienLamaBacaSaja(klien)
```

- [ ] **Step 10: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/unit/test_hosting_klien.py tests/unit/test_site_client.py tests/unit/test_site_client_staging.py tests/unit/test_staging_pembantu.py -q`. Expected: semua lulus. Bila `test_sni_memverifikasi_sertifikat_terhadap_nama_domain` gagal karena httpcore mengabaikan `sni_hostname`, asumsi A1 tidak berlaku: hentikan task dan laporkan (jangan melonggarkan verifikasi).

- [ ] **Step 11: Seluruh test unit, lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 12: Commit.**

```bash
git add src/wpmgr/staging/pembantu.py src/wpmgr/site_client.py src/wpmgr/hosting/__init__.py src/wpmgr/hosting/umum.py tests/unit/pembantu_palsu.py tests/unit/test_staging_pembantu.py tests/unit/test_hosting_klien.py
git commit -m "feat(hosting): pembungkus prod-*, SiteClient dipatok IP, klien hosting lama baca-saja

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Infrastruktur job hosting — antrean runtime, worker, titik potongan, pembungkus `jalankan_hosting`, reaper

**Files:**
- Modify: `src/wpmgr/jobs/queue.py`, `src/wpmgr/worker.py`, `src/wpmgr/jobs/reaper.py`, `src/wpmgr/staging/umum.py`, `src/wpmgr/hosting/umum.py`, `tests/integration/conftest.py`
- Test: `tests/integration/test_hosting_antrean.py`

**Interfaces:**
- Consumes (Task 4–5): `JOB_HOSTING`, `JOB_RUNTIME`, `HostingVps`, `StatusHosting`, `Settings.hosting_aktif`, `Settings.jalur_hosting`, `Settings.staging_domain`, `hosting.umum` bagian klien; Lapis 3: `staging.umum.*` (`Dibatalkan`, `KlaimHilang`, `GalatDitolakTanpaUbah`, `GalatBerhenti`, `GalatDibatalkan`, `kemajuan`, `simpan_kemajuan`, `catat_aktivitas`, `pesan_os`, `galat_gagal`, `galat_ditolak`, `PESAN_TAK_TERDUGA`, `PESAN_DIBATALKAN`, `salinan_belum_disentuh`), `queue.akan_diulang`, `queue.dalam_batas_pemulihan`.
- Produces:
  - `queue._RUNTIME`, `queue._RUNTIME_BACA` (string SQL), `queue.LANGKAH_AKTIFKAN_SESUDAH_TUKAR = frozenset({"tukar", "verifikasi", "beres"})`; `queue.menyentuh_produksi(job)` benar juga untuk `pindah_aktifkan` dengan `kemajuan.langkah_aktifkan` di himpunan itu; `_menahan` menahan job non-runtime selama `pindah_aktifkan` tertunda sudah memulai tukar.
  - Worker: job `JOB_RUNTIME` diklaim worker `jenis="staging"`, tidak memulihkan/menyentuh status site, dan memakai pesan tetap untuk galat internal.
  - `staging.umum.periksa_batal(sesi, baris)` dan `titik_potongan(sesi, job, baris)` menerima `Staging` atau `HostingVps`; `staging.umum._batal_diminta(sesi, baris_id, kelas=Staging)`.
  - `hosting.umum`: `ASAL_SALINAN`, `ASAL_PRODUKSI`, `PESAN_FITUR_MATI`, `PESAN_BELUM_ADA`, `PESAN_TERHENTI`, `PESAN_PRODUKSI_GAGAL`, `PESAN_BATAL_TENGAH`, `PESAN_LAIN`, `PESAN_KELAS`, `PESAN_KODE`, `STATUS_KERJA`, `STATUS_KERJA_SEMUA`, kelas `GalatHosting(SiteError)` (pesan tetap milik kode hosting; diteruskan `pesan_ui` apa adanya), `sekarang()`, `dir_hosting(site_id) -> Path`, `host_pratinjau(h) -> str`, `url_pratinjau(h) -> str`, `muat_hosting(sesi, job) -> (Site, HostingVps)`, `pesan_ui(exc) -> str`, `kelas_pembantu(job) -> str`, `catat_status_awal(sesi, job, h)`, `status_sebelum(job, h) -> (StatusHosting, asal)`, `status_gagal_final(job, h, pesan) -> (StatusHosting, asal, galat)`, `jalankan_hosting(sesi, job, inti, nama, boleh_batal=None) -> dict` (`inti(sesi, job, site, hosting) -> dict`).
  - `reaper._lepas_hosting(sesi, job)`.
  - Fixture integrasi: `hosting_aktif` (fitur hosting menyala, `WPMGR_HOSTING_DIR` di direktori sementara; nilai = path itu) dan `site_hosting` (site `https://toko.co.id` dengan fitur staging dan baris `HostingVps` `toko-co-id`/`toko.co.id`, `dengan_www=True`, `ip_lama=93.184.216.34`, sandi pratinjau `rahasia-pratinjau`).

- [ ] **Step 1: Fixture integrasi.** Tambahkan ke akhir `tests/integration/conftest.py`:

```python
@pytest.fixture
def hosting_aktif(tmp_path, monkeypatch):
    """Fitur hosting VPS menyala (butuh domain staging) dengan WPMGR_HOSTING_DIR sementara."""
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_STAGING_DOMAIN", "staging.contoh.id")
    monkeypatch.setenv("WPMGR_STAGING_DIR", str(tmp_path / "stg"))
    monkeypatch.setenv("WPMGR_HOSTING_IPV4", "169.58.91.181")
    monkeypatch.setenv("WPMGR_HOSTING_DIR", str(tmp_path / "hosting"))
    get_settings.cache_clear()
    yield tmp_path / "hosting"
    get_settings.cache_clear()


@pytest.fixture
def site_hosting(sesi, site, hosting_aktif):
    from wpmgr.crypto import enkripsi_secret
    from wpmgr.models import HostingVps
    from wpmgr.staging.pembantu import hash_sandi

    site.url = "https://toko.co.id"
    site.secret_terenkripsi = enkripsi_secret("f" * 64)
    site.fitur = ["self_update", "staging"]
    h = HostingVps(site_id=site.id, nama="toko-co-id", domain="toko.co.id", dengan_www=True,
                   ip_lama="93.184.216.34", sandi_hash=hash_sandi("rahasia-pratinjau"))
    sesi.add(h)
    sesi.commit()
    return h
```

- [ ] **Step 2: Tulis test yang gagal.**

File: `tests/integration/test_hosting_antrean.py`
```python
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from wpmgr.errors import AUTH_ERROR, BAD_RESPONSE, STAGING_DITOLAK, STAGING_GAGAL, TRANSIENT, SiteError
from wpmgr.hosting import umum as hu
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import ambil_job, buat_job, dalam_batas_pemulihan, menyentuh_produksi
from wpmgr.jobs.reaper import pulihkan_job_yatim
from wpmgr.models import (
    ActivityLog,
    HostingVps,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
    StatusHosting,
)
from wpmgr.staging import umum as stg
from wpmgr.staging.pembantu import GalatPembantu
from wpmgr.worker import proses_satu

pytestmark = pytest.mark.integration

SEKARANG = datetime.now(timezone.utc)


def _berjalan(sesi, site, tipe, oleh="w-lain", payload=None):
    job = buat_job(sesi, site.id, tipe, payload)
    job.status = JobStatus.running
    job.locked_by = oleh
    job.locked_at = datetime.now(timezone.utc)
    sesi.commit()
    return job


def _h(sesi, site_hosting):
    return sesi.get(HostingVps, site_hosting.id, populate_existing=True)


# ---- antrean ----------------------------------------------------------------------


def test_pindah_tarik_berjalan_bersama_scan(sesi, site):
    _berjalan(sesi, site, JobType.scan_site)
    j = buat_job(sesi, site.id, JobType.pindah_tarik)
    assert ambil_job(sesi, "w1", "staging").id == j.id


@pytest.mark.parametrize("tipe", [JobType.pindah_aktifkan, JobType.backup_hosting])
def test_aktifkan_dan_backup_eksklusif(sesi, site, tipe):
    _berjalan(sesi, site, JobType.scan_site)
    buat_job(sesi, site.id, tipe)
    assert ambil_job(sesi, "w1", "staging") is None


def test_job_runtime_diserialkan_antara_staging_dan_hosting(sesi, site):
    _berjalan(sesi, site, JobType.staging_tarik)
    buat_job(sesi, site.id, JobType.pindah_tarik)
    assert ambil_job(sesi, "w1", "staging") is None


def test_worker_umum_tidak_mengklaim_job_hosting(sesi, site):
    j = buat_job(sesi, site.id, JobType.pindah_tarik)
    assert ambil_job(sesi, "w1", "umum") is None
    assert ambil_job(sesi, "w1", "staging").id == j.id


def test_aktifkan_sesudah_tukar_menahan_job_umum(sesi, site):
    j = buat_job(sesi, site.id, JobType.pindah_aktifkan,
                 {"kemajuan": {"langkah_aktifkan": "tukar", "tukar_pada": SEKARANG.isoformat()}})
    j.scheduled_for = SEKARANG + timedelta(minutes=10)
    sesi.commit()
    scan = buat_job(sesi, site.id, JobType.scan_site)
    assert ambil_job(sesi, "w1", "umum") is None
    j.payload = {"kemajuan": {"langkah_aktifkan": "tarik"}}
    flag_modified(j, "payload")
    sesi.commit()
    assert ambil_job(sesi, "w1", "umum").id == scan.id


@pytest.mark.parametrize("langkah,hasil", [
    ("tukar", True), ("verifikasi", True), ("beres", True), ("dns", False), ("sertifikat", False),
    ("tarik", False), (None, False),
])
def test_menyentuh_produksi_aktifkan(langkah, hasil):
    job = Job(tipe=JobType.pindah_aktifkan, payload={"kemajuan": {"langkah_aktifkan": langkah}})
    assert menyentuh_produksi(job) is hasil
    assert menyentuh_produksi(Job(tipe=JobType.backup_hosting, payload={"kemajuan": {"langkah_aktifkan": "tukar"}})) \
        is False


def test_batas_pemulihan_24_jam_aktifkan():
    def job(jam):
        return Job(tipe=JobType.pindah_aktifkan, payload={"kemajuan": {
            "langkah_aktifkan": "verifikasi", "tukar_pada": (SEKARANG - timedelta(hours=jam)).isoformat()}})

    assert dalam_batas_pemulihan(job(23)) is True
    assert dalam_batas_pemulihan(job(25)) is False


def test_titik_potongan_membaca_batal_hosting(sesi, site_hosting):
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    stg.titik_potongan(sesi, job, site_hosting)
    site_hosting.batal_diminta_pada = SEKARANG
    sesi.commit()
    with pytest.raises(stg.Dibatalkan):
        stg.titik_potongan(sesi, job, site_hosting)


# ---- pembungkus jalankan_hosting ----------------------------------------------------


def test_muat_hosting_fitur_mati(sesi, site):
    job = buat_job(sesi, site.id, JobType.pindah_tarik)
    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, lambda *a: {}, "Salin ke VPS")
    assert e.value.error_class == STAGING_DITOLAK
    assert e.value.pesan == hu.PESAN_FITUR_MATI


def test_sukses_membersihkan_galat(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    site_hosting.galat = "lama"
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)

    def inti(sesi, job, site, h):
        assert h.status == StatusHosting.menyalin
        h.status = StatusHosting.pratinjau
        sesi.commit()
        return {"ok": 1}

    assert hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS") == {"ok": 1}
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat, h.gagal_asal) == (StatusHosting.pratinjau, None, None)
    assert stg.kemajuan(job)["status_hosting_awal"] == "pratinjau"


def test_ditolak_tanpa_ubah_mengembalikan_status_awal(sesi, site_hosting):
    site_hosting.status = StatusHosting.menunggu_dns
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_aktifkan)

    def inti(sesi, job, site, h):
        raise stg.GalatDitolakTanpaUbah("DNS belum menunjuk VPS.")

    with pytest.raises(stg.GalatDitolakTanpaUbah):
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat, h.gagal_asal) == (StatusHosting.menunggu_dns, "DNS belum menunjuk VPS.", None)


def test_galat_pembantu_sebelum_tukar_final_gagal_salinan(sesi, site_hosting):
    site_hosting.status = StatusHosting.menunggu_dns
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_aktifkan)

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, langkah_aktifkan="tarik")
        raise GalatPembantu("docker", "Membuat container situs gagal. Perintah Docker di server staging gagal.")

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    assert e.value.error_class == STAGING_GAGAL
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal) == (StatusHosting.gagal, "salinan")
    assert h.galat == "Membuat container situs gagal. Perintah Docker di server staging gagal."


def _sesudah_tukar(sesi, site_hosting, jam_lalu=0.0, attempts=3):
    site_hosting.status = StatusHosting.menunggu_dns
    site_hosting.dilayani_vps_pada = SEKARANG - timedelta(hours=jam_lalu)
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_aktifkan, {"kemajuan": {
        "langkah_aktifkan": "tukar", "tukar_pada": (SEKARANG - timedelta(hours=jam_lalu)).isoformat(),
        "status_hosting_awal": "menunggu_dns"}})
    job.attempts = attempts
    sesi.commit()
    return job


def test_galat_pembantu_sesudah_tukar_diulang_melewati_max_attempts(sesi, site_hosting):
    job = _sesudah_tukar(sesi, site_hosting)

    def inti(sesi, job, site, h):
        raise GalatPembantu("waktu", "Skrip pembantu tidak selesai dalam 300 detik.")

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    assert e.value.error_class == TRANSIENT
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.mengaktifkan
    assert h.galat == "Terputus, dilanjutkan otomatis: Skrip pembantu tidak selesai dalam 300 detik."


def test_lewat_24_jam_menjadi_gagal_produksi_dengan_pesan_tetap(sesi, site_hosting):
    job = _sesudah_tukar(sesi, site_hosting, jam_lalu=25)

    def inti(sesi, job, site, h):
        raise SiteError(TRANSIENT, "Situs belum menjawab HTTPS dengan benar.")

    with pytest.raises(SiteError):
        hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "produksi", hu.PESAN_PRODUKSI_GAGAL)


def test_batal_sebelum_inti_mengembalikan_status_awal(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    site_hosting.batal_diminta_pada = SEKARANG
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    with pytest.raises(stg.GalatDibatalkan):
        hu.jalankan_hosting(sesi, job, lambda *a: pytest.fail("inti tidak boleh berjalan"), "Salin ke VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.batal_diminta_pada) == (StatusHosting.pratinjau, None)
    assert sesi.scalar(select(ActivityLog.pesan).where(ActivityLog.job_id == job.id)) == "Salin ke VPS dibatalkan"


def test_batal_sesudah_salinan_disentuh_menandai_gagal_salinan(sesi, site_hosting):
    site_hosting.status = StatusHosting.pratinjau
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)

    def inti(sesi, job, site, h):
        stg.simpan_kemajuan(sesi, job, tahap="berkas")
        raise stg.Dibatalkan()

    with pytest.raises(stg.GalatDibatalkan):
        hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", hu.PESAN_BATAL_TENGAH)


def test_backup_gagal_final_tidak_mengubah_status(sesi, site_hosting):
    site_hosting.status = StatusHosting.aktif
    site_hosting.dilayani_vps_pada = SEKARANG
    sesi.commit()
    job = buat_job(sesi, site_hosting.site_id, JobType.backup_hosting)
    job.attempts = job.max_attempts
    sesi.commit()

    def inti(sesi, job, site, h):
        raise GalatPembantu("backup", "Membuat backup situs gagal. Backup situs gagal dibuat.")

    with pytest.raises(SiteError) as e:
        hu.jalankan_hosting(sesi, job, inti, "Backup situs")
    assert e.value.error_class == TRANSIENT
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.aktif and h.galat is None
    assert h.backup_gagal_pada is not None


def test_galat_connector_tidak_bocor_ke_ui_dan_site_tidak_disentuh(sesi, site_hosting, monkeypatch):
    mentah = "<html>Fatal error in /home/u123/public_html/wp-config.php</html>"

    def inti(sesi, job, site, h):
        raise SiteError(BAD_RESPONSE, mentah)

    monkeypatch.setitem(handlers.HANDLER, JobType.pindah_tarik,
                        lambda sesi, job, klien: hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS"))
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    assert proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.expire_all()
    h = _h(sesi, site_hosting)
    j = sesi.get(Job, job.id)
    assert h.galat == f"Terputus, dilanjutkan otomatis: {hu.PESAN_KELAS[BAD_RESPONSE]}"
    assert "public_html" not in (j.error or "")
    for log in sesi.scalars(select(ActivityLog)).all():
        assert "public_html" not in log.pesan and "public_html" not in str(log.detail)
    site = sesi.get(Site, site_hosting.site_id)
    assert site.status == SiteStatus.active and site.last_error is None


def test_pesan_ui_meneruskan_pesan_tetap_milik_hosting():
    pesan = "Situs belum menjawab HTTPS dengan benar."
    assert hu.pesan_ui(hu.GalatHosting(TRANSIENT, pesan)) == pesan
    assert hu.pesan_ui(SiteError(TRANSIENT, "koneksi gagal: [Errno 111] 10.0.0.5")) == hu.PESAN_KELAS[TRANSIENT]
    dari_connector = SiteError(STAGING_GAGAL, "pesan connector", kode="wpmgr_staging_path")
    assert hu.pesan_ui(dari_connector) == hu.PESAN_KODE[STAGING_GAGAL]
    assert hu.pesan_ui(stg.galat_gagal("Manifest produksi melebihi batas.")) == "Manifest produksi melebihi batas."


def test_kegagalan_akhir_hosting_tidak_mengubah_status_site(sesi, site_hosting, monkeypatch):
    def inti(sesi, job, site, h):
        raise SiteError(AUTH_ERROR, "403 dari hosting lama")

    monkeypatch.setitem(handlers.HANDLER, JobType.pindah_tarik,
                        lambda sesi, job, klien: hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS"))
    buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    proses_satu(sesi, "w1", buat_klien_fn=lambda s: None, jenis="staging")
    sesi.expire_all()
    site = sesi.get(Site, site_hosting.site_id)
    assert site.status == SiteStatus.active and site.last_error is None
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat) == (StatusHosting.gagal, hu.PESAN_KELAS[AUTH_ERROR])


# ---- reaper ----------------------------------------------------------------------------


def _yatim(sesi, site_hosting, tipe, payload=None, attempts=None):
    job = buat_job(sesi, site_hosting.site_id, tipe, payload)
    job = ambil_job(sesi, "w-mati", "staging")
    job.attempts = job.max_attempts if attempts is None else attempts
    job.locked_at = SEKARANG - timedelta(minutes=30)
    sesi.commit()
    return job


def test_reaper_melepas_hosting_menyalin(sesi, site_hosting):
    site_hosting.status = StatusHosting.menyalin
    sesi.commit()
    job = _yatim(sesi, site_hosting, JobType.pindah_tarik)
    assert pulihkan_job_yatim(sesi) == 1
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.unknown
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", hu.PESAN_TERHENTI)


def test_reaper_aktifkan_sesudah_tukar_dalam_24_jam_diulang(sesi, site_hosting):
    site_hosting.status = StatusHosting.mengaktifkan
    site_hosting.dilayani_vps_pada = SEKARANG
    sesi.commit()
    job = _yatim(sesi, site_hosting, JobType.pindah_aktifkan,
                 {"kemajuan": {"langkah_aktifkan": "tukar", "tukar_pada": SEKARANG.isoformat()}})
    pulihkan_job_yatim(sesi)
    sesi.expire_all()
    assert sesi.get(Job, job.id).status == JobStatus.pending
    assert _h(sesi, site_hosting).status == StatusHosting.mengaktifkan


def test_reaper_aktifkan_sesudah_24_jam_gagal_produksi(sesi, site_hosting):
    site_hosting.status = StatusHosting.mengaktifkan
    site_hosting.dilayani_vps_pada = SEKARANG - timedelta(hours=30)
    sesi.commit()
    _yatim(sesi, site_hosting, JobType.pindah_aktifkan, {"kemajuan": {
        "langkah_aktifkan": "verifikasi", "tukar_pada": (SEKARANG - timedelta(hours=30)).isoformat()}})
    pulihkan_job_yatim(sesi)
    sesi.expire_all()
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "produksi", hu.PESAN_PRODUKSI_GAGAL)


def test_reaper_backup_menandai_backup_gagal(sesi, site_hosting):
    site_hosting.status = StatusHosting.aktif
    site_hosting.dilayani_vps_pada = SEKARANG
    sesi.commit()
    _yatim(sesi, site_hosting, JobType.backup_hosting)
    pulihkan_job_yatim(sesi)
    sesi.expire_all()
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.aktif and h.backup_gagal_pada is not None

```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_hosting_antrean.py -q`. Expected: koleksi berhasil, lalu banyak kegagalan, antara lain `AttributeError: module 'wpmgr.hosting.umum' has no attribute 'jalankan_hosting'`, `test_worker_umum_tidak_mengklaim_job_hosting` gagal karena worker umum mengklaim `pindah_tarik`, dan `test_menyentuh_produksi_aktifkan[tukar-True]` gagal (`False is True`).

- [ ] **Step 4: Antrean runtime.** Di `src/wpmgr/jobs/queue.py`, ganti definisi `_STAGING`, `_STAGING_BACA`, `_BENTROK`, dan fungsi `_menahan` dengan:

```python
# Job runtime (staging Lapis 3 + hosting Lapis 4) diproses worker staging dan
# diserialkan satu sama lain per site. Yang hanya membaca produksi/hosting
# lama (_RUNTIME_BACA) boleh berjalan bersama job non-runtime site yang sama.
_RUNTIME = ("('staging_tarik', 'staging_uji_update', 'staging_dorong', 'staging_kembalikan', "
            "'pindah_tarik', 'pindah_aktifkan', 'backup_hosting')")
_RUNTIME_BACA = "('staging_tarik', 'staging_uji_update', 'pindah_tarik')"
# Pasangan (j, j2) yang TIDAK boleh berjalan bersamaan di satu site.
_BENTROK = (
    f"NOT (j.tipe IN {_RUNTIME_BACA} AND j2.tipe NOT IN {_RUNTIME})"
    f" AND NOT (j2.tipe IN {_RUNTIME_BACA} AND j.tipe NOT IN {_RUNTIME})"
)


# Job yang menahan job lain di site yang sama: yang sedang berjalan, dan (I1)
# job TERTUNDA yang sudah menyentuh produksi -- dorong/kembalikan dengan
# `unggah_mulai` atau `langkah_terapkan` di kemajuan, atau pindah_aktifkan
# yang sudah memulai tukar (`langkah_aktifkan` tukar/verifikasi/beres).
# Produksi bisa setengah diterapkan/beralih, jadi job Lapis 1 (update, scan,
# ...) tidak boleh berjalan di atasnya. Hanya berlaku bagi kandidat
# non-runtime (`:kand` = alias tabel kandidat); job runtime lain tidak bisa
# berdampingan dengannya (indeks unik per jenis dan _BENTROK).
def _menahan(kand: str) -> str:
    return (
        "(j2.status = 'running'"
        " OR (j2.status = 'pending'"
        f" AND {kand}.tipe NOT IN {_RUNTIME}"
        " AND ((j2.tipe IN ('staging_dorong', 'staging_kembalikan')"
        " AND (j2.payload #> '{kemajuan,langkah_terapkan}' IS NOT NULL"
        " OR (j2.payload #>> '{kemajuan,unggah_mulai}') = 'true'))"
        " OR (j2.tipe = 'pindah_aktifkan'"
        " AND (j2.payload #>> '{kemajuan,langkah_aktifkan}') IN ('tukar', 'verifikasi', 'beres')))))"
    )
```

Di `SQL_AMBIL`, ganti `(j.tipe IN {_STAGING})` dengan `(j.tipe IN {_RUNTIME})`, dan ganti komentar di atasnya: `` `:jenis` memisahkan worker staging (job runtime berjam-jam: staging dan hosting) dari worker umum. ``

Ganti blok `# ---- pemulihan produksi sesudah tukar (putusan R26)` sampai akhir `menyentuh_produksi` dengan:

```python
# ---- pemulihan produksi sesudah tukar (putusan R26) ----------------------------

# Langkah terapkan sejak tukar dikirim ke produksi (tulis-lebih-dulu di dorong).
LANGKAH_SESUDAH_TUKAR = frozenset({"tukar", "pulihkan", "dipulihkan", "selesai", "beres"})
# Lapis 4: langkah pindah_aktifkan sejak prod-aktifkan dikirim (spec §10.4).
LANGKAH_AKTIFKAN_SESUDAH_TUKAR = frozenset({"tukar", "verifikasi", "beres"})
_JOB_PRODUKSI = frozenset({JobType.staging_dorong, JobType.staging_kembalikan})
# Produksi yang setengah ditukar tidak boleh dibiarkan bergantung pada WP-Cron:
# job dicoba lagi terus (jeda dibatasi) sampai batas ini, tidak berhenti di max_attempts.
JEDA_PEMULIHAN_MAKS_MENIT = 15
BATAS_PEMULIHAN = timedelta(hours=24)


def menyentuh_produksi(job: Job) -> bool:
    """Job yang sudah mengirim perubahan ke produksi dan tidak terbukti dipulihkan (R26).

    Dorong/kembalikan sesudah tukar, atau pindah_aktifkan sesudah
    `prod-aktifkan` dikirim (situs mungkin sudah dilayani VPS setengah jalan).
    """
    k = (job.payload or {}).get("kemajuan") or {}
    if job.tipe == JobType.pindah_aktifkan:
        return k.get("langkah_aktifkan") in LANGKAH_AKTIFKAN_SESUDAH_TUKAR
    if job.tipe not in _JOB_PRODUKSI:
        return False
    return k.get("langkah_terapkan") in LANGKAH_SESUDAH_TUKAR and not k.get("pulih_terkonfirmasi")
```

- [ ] **Step 5: Worker.** Di `src/wpmgr/worker.py`, ganti impor `JOB_STAGING` dengan `JOB_RUNTIME` dan ketiga pemakaiannya (`if job.tipe not in JOB_STAGING:` sebelum `_pulihkan_status`, `if job.tipe in JOB_STAGING:` di `_catat_kesalahan_internal`, dan `sentuh_site = job.tipe not in JOB_STAGING and ...`) dengan `JOB_RUNTIME`. Ganti docstring `jenis_worker`:

```python
    """Instans systemd `wpmgr-worker@staging*` hanya mengambil job runtime (staging dan hosting)."""
```

Tambahkan kalimat ke komentar di atas `_pulihkan_status(sesi, site)`: `Job hosting juga tidak: ia berbicara ke hosting lama lewat IP yang dipatok, atau ke skrip root.`

- [ ] **Step 6: Titik potongan untuk baris apa pun.** Di `src/wpmgr/staging/umum.py`, ganti `_batal_diminta`, `periksa_batal`, dan `titik_potongan`:

```python
def _batal_diminta(sesi: Session, baris_id, kelas=Staging) -> bool:
    return sesi.scalar(select(kelas.batal_diminta_pada).where(kelas.id == baris_id)) is not None


def periksa_batal(sesi: Session, baris) -> None:
    """`baris`: Staging atau HostingVps (kolom `id` dan `batal_diminta_pada`), dibaca ulang dari DB."""
    if _batal_diminta(sesi, baris.id, type(baris)):
        raise Dibatalkan()


def titik_potongan(sesi: Session, job: Job, baris) -> None:
    """Dipanggil di antara potongan: batal, penghentian worker, dan detak.

    `baris` adalah pemilik kolom batal (Staging atau HostingVps). None hanya
    untuk kembalikan setelah staging dihapus: tidak ada kolom batal yang bisa
    diperiksa.
    """
    if baris is not None:
        periksa_batal(sesi, baris)
    if harus_berhenti():
        # Berhenti karena deploy/restart bukan kegagalan: jatah percobaan
        # tidak dihabiskan, dan progres di payload membuat job melanjutkan.
        job.attempts = max(0, job.attempts - 1)
        sesi.commit()
        raise GalatBerhenti()
    detak(sesi, job)
```

- [ ] **Step 7: Pembungkus job hosting.** Ganti seluruh isi `src/wpmgr/hosting/umum.py` (bagian klien dari Task 5 tetap sama, hanya dipindah di bawah impor baru):

File: `src/wpmgr/hosting/umum.py`
```python
"""Bagian bersama hosting VPS (spec Lapis 4 §10).

- Klien connector hosting LAMA: dipatok ke IP lama (tarik ulang sesudah DNS
  berpindah tetap mengambil dari hosting lama, RF4) dan hanya bisa membaca.
- Pembungkus job hosting `jalankan_hosting` (pola `staging.umum.jalankan_staging`):
  status kerja, batal, asal `gagal`, dan pemetaan galat ke pesan tetap.
"""

import ipaddress
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from wpmgr.config import get_settings
from wpmgr.crypto import dekripsi_secret
from wpmgr.errors import (
    AUTH_ERROR,
    BAD_RESPONSE,
    BERKAS_HILANG,
    BLOCKED,
    CONNECTOR_MISSING,
    STAGING_DITOLAK,
    STAGING_GAGAL,
    STAGING_MATI,
    TERLALU_BESAR,
    TRANSIENT,
    UNKNOWN,
    SiteError,
)
from wpmgr.jobs.queue import akan_diulang, menyentuh_produksi
from wpmgr.models import HostingVps, Job, JobType, Site, StatusHosting
from wpmgr.site_client import SiteClient, buat_klien_staging
from wpmgr.staging import umum as stg
from wpmgr.staging.aman import bersih_teks
from wpmgr.staging.pembantu import GalatPembantu

log = logging.getLogger("wpmgr.hosting.umum")

# ---- klien hosting lama (spec §10.1) ---------------------------------------------

PESAN_IP_LAMA = ("Alamat IP hosting lama tidak sah (kosong, privat, atau sama dengan VPS); "
                 "batalkan pindah lalu mulai lagi.")
METODE_BACA = ("ping", "staging_manifest", "staging_file", "staging_rentang", "staging_tabel", "staging_tanda_air")


class KlienLamaBacaSaja:
    """Klien connector hosting lama yang hanya bisa membaca (spec §10.1).

    Metode tulis (`staging_unggah`, `staging_terapkan`, `staging_bersihkan`,
    `update`, `self_update`) sengaja tidak ada: handler hosting yang keliru
    memanggilnya gagal dengan AttributeError, bukan mengubah site lama.
    """

    def __init__(self, klien: SiteClient) -> None:
        self._klien = klien

    @property
    def alamat(self) -> str | None:
        return self._klien.alamat_tetap

    def ping(self) -> dict:
        return self._klien.ping()

    def staging_manifest(self, kursor: str | None = None, batas: int = 5000) -> dict:
        return self._klien.staging_manifest(kursor, batas=batas)

    def staging_file(self, paths: list[str]):
        return self._klien.staging_file(paths)

    def staging_rentang(self, path: str, dari: int, panjang: int):
        return self._klien.staging_rentang(path, dari, panjang)

    def staging_tabel(self, tabel: str, kursor: str | None):
        return self._klien.staging_tabel(tabel, kursor)

    def staging_tanda_air(self, posts_sejak: str | None = None, posts_maks: int | None = None) -> dict:
        return self._klien.staging_tanda_air(posts_sejak, posts_maks)


def alamat_lama_sah(ip) -> bool:
    """IPv4 publik (bukan privat/loopback/dokumentasi) yang bukan IPv4 VPS sendiri."""
    if not isinstance(ip, str):
        return False
    try:
        alamat = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    if not alamat.is_global or str(alamat) != ip:
        return False
    return ip != get_settings().hosting_ipv4


def buat_http_lama() -> httpx.Client:
    """Klien httpx untuk hosting lama: tanpa keep-alive (tenggat total, putusan F11)."""
    return buat_klien_staging()


def klien_lama(site, hosting) -> KlienLamaBacaSaja:
    if not alamat_lama_sah(hosting.ip_lama):
        raise stg.galat_ditolak(PESAN_IP_LAMA)
    http = buat_http_lama()
    klien = SiteClient(site.url, str(site.id), dekripsi_secret(site.secret_terenkripsi), client=http,
                       klien_staging=http, alamat_tetap=hosting.ip_lama)
    return KlienLamaBacaSaja(klien)


# ---- status dan pesan ---------------------------------------------------------------

ASAL_SALINAN = "salinan"
ASAL_PRODUKSI = "produksi"
PESAN_FITUR_MATI = "Fitur hosting VPS tidak aktif (WPMGR_HOSTING_IPV4 atau WPMGR_STAGING_DOMAIN kosong)."
PESAN_BELUM_ADA = "Pindah hosting untuk site ini belum dimulai."
PESAN_TERHENTI = "Proses terhenti tak terduga; coba lagi."
PESAN_PRODUKSI_GAGAL = ("Situs sudah dilayani VPS tetapi pemeriksaan akhir gagal. Periksa situs; bila rusak, "
                        "arahkan DNS kembali ke hosting lama (masih utuh).")
PESAN_BATAL_TENGAH = "Salin ke VPS dibatalkan di tengah; salinan VPS belum utuh, salin ulang."
PESAN_LAIN = "Pindah hosting gagal; lihat log server."
# Global Constraints / Koreksi #7: teks respons connector tidak pernah tampil di UI.
PESAN_KELAS = {
    TRANSIENT: "Hosting lama tidak dapat dihubungi saat ini.",
    UNKNOWN: "Hosting lama tidak menjawab tuntas.",
    BAD_RESPONSE: "Balasan connector hosting lama tidak sesuai.",
    AUTH_ERROR: "Connector hosting lama menolak tanda tangan dashboard; periksa pairing site.",
    BLOCKED: "Permintaan ke hosting lama diblokir firewall.",
    CONNECTOR_MISSING: "Connector di hosting lama tidak ditemukan.",
    STAGING_MATI: "'Izinkan staging' di connector hosting lama mati.",
    TERLALU_BESAR: "Potongan dari hosting lama melebihi batas.",
    BERKAS_HILANG: "Berkas di hosting lama hilang selama penyalinan.",
}
# Galat dengan `kode` connector (WP_Error): pesannya dari connector, bukan dashboard.
PESAN_KODE = {
    STAGING_GAGAL: "Connector hosting lama menolak permintaan penyalinan.",
    STAGING_DITOLAK: "Hosting lama menolak permintaan saat ini; coba lagi nanti.",
}
STATUS_KERJA = {JobType.pindah_tarik: StatusHosting.menyalin, JobType.pindah_aktifkan: StatusHosting.mengaktifkan}
STATUS_KERJA_SEMUA = frozenset(STATUS_KERJA.values())
_TETAP = object()


class GalatHosting(SiteError):
    """Galat yang pesannya disusun kode hosting sendiri (teks tetap): diteruskan apa adanya ke UI."""


def sekarang() -> datetime:
    return datetime.now(timezone.utc)


def dir_hosting(site_id) -> Path:
    return get_settings().jalur_hosting / str(site_id)


def host_pratinjau(hosting) -> str:
    return f"vps-{hosting.nama}.{get_settings().staging_domain}"


def url_pratinjau(hosting) -> str:
    return f"https://{host_pratinjau(hosting)}"


def muat_hosting(sesi: Session, job: Job) -> tuple[Site, HostingVps]:
    if not get_settings().hosting_aktif:
        raise stg.galat_ditolak(PESAN_FITUR_MATI)
    site = sesi.get(Site, job.site_id)
    h = sesi.scalar(select(HostingVps).where(HostingVps.site_id == job.site_id))
    if h is None:
        raise stg.galat_ditolak(PESAN_BELUM_ADA)
    return site, h


def pesan_ui(exc: SiteError) -> str:
    """Pesan tetap untuk UI dari galat job hosting (Koreksi #7)."""
    if isinstance(exc, (GalatHosting, stg.GalatDitolakTanpaUbah, stg.GalatBerhenti, stg.GalatDibatalkan)):
        return exc.pesan
    if exc.kode is not None:
        return PESAN_KODE.get(exc.error_class) or PESAN_KELAS.get(exc.error_class, PESAN_LAIN)
    if exc.error_class in (STAGING_GAGAL, STAGING_DITOLAK):
        # Disusun dashboard sendiri (galat_gagal/galat_ditolak tanpa kode).
        return bersih_teks(exc.pesan, 1000) or PESAN_LAIN
    return PESAN_KELAS.get(exc.error_class, PESAN_LAIN)


def kelas_pembantu(job: Job) -> str:
    """Kelas antrean untuk GalatPembantu (Koreksi #16).

    Backup dan pindah_aktifkan sesudah tukar: sementara (F26/R26), karena
    setiap subperintah idempoten dan produksi tidak boleh ditinggal setengah
    beralih. Selain itu final, seperti staging.
    """
    if job.tipe == JobType.backup_hosting or menyentuh_produksi(job):
        return TRANSIENT
    return STAGING_GAGAL


def catat_status_awal(sesi: Session, job: Job, h: HostingVps) -> None:
    """Status, asal, dan galat hosting sebelum job ini, dicatat SEKALI (percobaan ulang melihat status kerja)."""
    if "status_hosting_awal" not in stg.kemajuan(job):
        stg.simpan_kemajuan(sesi, job, status_hosting_awal=h.status.value, gagal_asal_awal=h.gagal_asal,
                            galat_hosting_awal=h.galat)


def status_sebelum(job: Job, h: HostingVps) -> tuple[StatusHosting, str | None]:
    """(status, asal) sebelum job ini, untuk penolakan tanpa ubah dan batal (Koreksi #11)."""
    k = stg.kemajuan(job)
    awal = k.get("status_hosting_awal")
    if awal == StatusHosting.gagal.value:
        return StatusHosting.gagal, k.get("gagal_asal_awal")
    if awal in (StatusHosting.pratinjau.value, StatusHosting.menunggu_dns.value, StatusHosting.aktif.value):
        return StatusHosting(awal), None
    # Tidak tercatat, atau status kerja yang basi.
    if h.dilayani_vps_pada is not None:
        return StatusHosting.gagal, ASAL_PRODUKSI
    if job.tipe == JobType.pindah_aktifkan:
        return StatusHosting.menunggu_dns, None
    if h.ditarik_pada is not None:
        return StatusHosting.pratinjau, None
    return StatusHosting.gagal, ASAL_SALINAN


def status_gagal_final(job: Job, h: HostingVps, pesan: str) -> tuple[StatusHosting, str, str]:
    """(status, asal, galat) untuk kegagalan FINAL (pembungkus dan reaper).

    Sudah dilayani VPS (tukar dikirim): 'produksi' dengan pesan tetap yang
    menyuruh memeriksa situs atau mengembalikan DNS. Belum: 'salinan' (site
    lama masih produksi bagi resolver yang belum berpindah).
    """
    if h.dilayani_vps_pada is not None:
        return StatusHosting.gagal, ASAL_PRODUKSI, PESAN_PRODUKSI_GAGAL
    return StatusHosting.gagal, ASAL_SALINAN, pesan


def _tandai(sesi: Session, hosting_id, status: StatusHosting, galat: str | None, asal=_TETAP,
            bersihkan_batal: bool = True) -> None:
    sesi.rollback()
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if h is None:
        return
    h.status = status
    h.galat = bersih_teks(galat, 1000)
    if asal is not _TETAP:
        h.gagal_asal = asal if status == StatusHosting.gagal else None
    if bersihkan_batal:
        h.batal_diminta_pada = None
    sesi.commit()


def _tandai_backup_gagal(sesi: Session, hosting_id) -> None:
    sesi.rollback()
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if h is not None:
        h.backup_gagal_pada = sekarang()
        sesi.commit()


def _gagal_final(sesi: Session, job: Job, hosting_id, pesan: str) -> None:
    sesi.rollback()
    if job.tipe == JobType.backup_hosting:
        # Backup tidak pernah mengubah status hosting (spec §10.5).
        _tandai_backup_gagal(sesi, hosting_id)
        return
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if h is None:
        return
    status, asal, galat = status_gagal_final(job, h, pesan)
    _tandai(sesi, hosting_id, status, galat, asal=asal)


def _batalkan(sesi: Session, job: Job, site_id, hosting_id, nama: str) -> stg.GalatDibatalkan:
    sesi.rollback()
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if job.tipe in (JobType.pindah_tarik, JobType.pindah_aktifkan) and not stg.salinan_belum_disentuh(job):
        # Salinan VPS sudah mulai ditulis: belum utuh sampai disalin ulang.
        status, asal, galat = StatusHosting.gagal, ASAL_SALINAN, PESAN_BATAL_TENGAH
    else:
        status, asal = status_sebelum(job, h)
        galat = stg.PESAN_DIBATALKAN
    _tandai(sesi, hosting_id, status, galat, asal=asal)
    stg.catat_aktivitas(sesi, site_id, job, f"{nama} dibatalkan", level="warning")
    sesi.commit()
    return stg.GalatDibatalkan()


def _putuskan(sesi: Session, job: Job, hosting_id, site_id, exc: SiteError, status_kerja, batal_berlaku,
              nama: str) -> None:
    """Final atau diulang, persis seperti worker (queue.akan_diulang; F26: UNKNOWN = TRANSIENT)."""
    sesi.rollback()
    if batal_berlaku() and stg._batal_diminta(sesi, hosting_id, HostingVps):
        raise _batalkan(sesi, job, site_id, hosting_id, nama)
    kelas = TRANSIENT if exc.error_class == UNKNOWN else exc.error_class
    if akan_diulang(job, kelas):
        if status_kerja is not None:
            _tandai(sesi, hosting_id, status_kerja, f"Terputus, dilanjutkan otomatis: {exc.pesan}",
                    bersihkan_batal=False)
    else:
        _gagal_final(sesi, job, hosting_id, exc.pesan)


def jalankan_hosting(sesi: Session, job: Job, inti, nama: str, boleh_batal=None) -> dict:
    """Pembungkus bersama job hosting (spec §10.6).

    `inti(sesi, job, site, hosting) -> dict`. `boleh_batal(job) -> bool`
    (opsional): False berarti permintaan batal tidak lagi berlaku (aktivasi
    sesudah tukar). Backup tidak bisa dibatalkan dan tidak mengubah status.
    """
    site, h = muat_hosting(sesi, job)
    hosting_id, site_id, job_id = h.id, site.id, job.id
    backup = job.tipe == JobType.backup_hosting
    status_kerja = STATUS_KERJA.get(job.tipe)
    catat_status_awal(sesi, job, h)
    if status_kerja is not None:
        h.status = status_kerja
        h.galat = None
    sesi.commit()

    def batal_berlaku() -> bool:
        return not backup and (boleh_batal is None or boleh_batal(job))

    try:
        if batal_berlaku():
            stg.periksa_batal(sesi, h)
        hasil = inti(sesi, job, site, h)
    except stg.Dibatalkan:
        raise _batalkan(sesi, job, site_id, hosting_id, nama) from None
    except stg.KlaimHilang:
        raise
    except stg.GalatDitolakTanpaUbah as exc:
        sesi.rollback()
        if backup:
            _tandai_backup_gagal(sesi, hosting_id)
        else:
            h2 = sesi.get(HostingVps, hosting_id, populate_existing=True)
            status, asal = status_sebelum(job, h2)
            _tandai(sesi, hosting_id, status, exc.pesan, asal=asal)
        raise
    except GalatPembantu as exc:
        # GalatPembantu.pesan sudah teks tetap (F20).
        galat = SiteError(kelas_pembantu(job), exc.pesan)
        _putuskan(sesi, job, hosting_id, site_id, galat, status_kerja, batal_berlaku, nama)
        raise galat from None
    except SiteError as exc:
        pesan = pesan_ui(exc)
        if pesan != exc.pesan:
            log.warning("Job hosting %s (%s) gagal: %s", job_id, nama, bersih_teks(exc.pesan, 500))
            exc.pesan = pesan
            exc.args = (pesan,)
        _putuskan(sesi, job, hosting_id, site_id, exc, status_kerja, batal_berlaku, nama)
        raise
    except OSError as exc:
        pesan = stg.pesan_os(exc)
        _gagal_final(sesi, job, hosting_id, pesan)
        raise stg.galat_gagal(pesan) from None
    except Exception:
        # Bug dashboard (F12): teks pengecualian hanya ke log server.
        log.exception("Galat tak terduga pada %s (job %s)", nama, job_id)
        _gagal_final(sesi, job, hosting_id, stg.PESAN_TAK_TERDUGA)
        raise
    h = sesi.get(HostingVps, hosting_id, populate_existing=True)
    if h is not None and not backup:
        h.galat = None
        if h.status != StatusHosting.gagal:
            h.gagal_asal = None
        h.batal_diminta_pada = None
    sesi.commit()
    return hasil
```

- [ ] **Step 8: Reaper.** Di `src/wpmgr/jobs/reaper.py`:

Ganti impor model menjadi:

```python
from wpmgr.models import (
    JOB_HOSTING,
    JOB_STAGING,
    ActivityLog,
    HostingVps,
    Job,
    JobStatus,
    JobType,
    Staging,
    StatusStaging,
)
```

dan tambahkan `from wpmgr.hosting import umum as hosting_umum` sesudah `from wpmgr.staging import umum`.

Sesudah `_lepas_staging`, tambahkan:

```python
def _lepas_hosting(sesi: Session, job: Job) -> None:
    """Hosting milik job yatim yang tidak akan diulang (pola `_lepas_staging`, spec §10.6).

    Aturan status sama dengan pembungkus (`hosting.umum.status_gagal_final`).
    Backup tidak pernah mengubah status; kegagalannya ditandai
    `backup_gagal_pada`. Urutan kunci: jobs lalu hosting_vps.
    """
    h = sesi.scalar(select(HostingVps).where(HostingVps.site_id == job.site_id).with_for_update())
    if h is None:
        return
    if job.tipe == JobType.backup_hosting:
        h.backup_gagal_pada = hosting_umum.sekarang()
        return
    if h.status not in hosting_umum.STATUS_KERJA_SEMUA:
        return
    status, asal, galat = hosting_umum.status_gagal_final(job, h, hosting_umum.PESAN_TERHENTI)
    h.status = status
    h.gagal_asal = asal
    h.galat = galat
    h.batal_diminta_pada = None
```

Di `pulihkan_job_yatim`, ganti:

```python
            if job.tipe in JOB_STAGING:
                _lepas_staging(sesi, job)
```

dengan:

```python
            if job.tipe in JOB_STAGING:
                _lepas_staging(sesi, job)
            elif job.tipe in JOB_HOSTING:
                _lepas_hosting(sesi, job)
```

- [ ] **Step 9: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/integration/test_hosting_antrean.py tests/integration/test_staging_antrean.py tests/integration/test_queue.py tests/integration/test_reaper.py tests/integration/test_worker.py -q`. Expected: semua lulus.

- [ ] **Step 10: Seluruh test unit dan integrasi, lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 11: Commit.**

```bash
git add src/wpmgr/jobs/queue.py src/wpmgr/worker.py src/wpmgr/jobs/reaper.py src/wpmgr/staging/umum.py src/wpmgr/hosting/umum.py tests/integration/conftest.py tests/integration/test_hosting_antrean.py
git commit -m "feat(hosting): antrean runtime, worker, titik potongan, pembungkus job hosting, reaper

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Refaktor `tarik.tarik_inti` dengan `TujuanSalinan` (perilaku staging tetap)

**Files:**
- Modify: `src/wpmgr/staging/tarik.py`, `src/wpmgr/staging/rencana.py`
- Test: `tests/unit/test_staging_rencana.py` (tambah), `tests/integration/test_hosting_tarik_inti.py`

**Interfaces:**
- Consumes (Task 6): `staging.umum.titik_potongan(sesi, job, baris)` menerima baris apa pun dengan `id` dan `batal_diminta_pada`. Lapis 3: seluruh `tarik.py`.
- Produces:
  - `rencana.selisih(produksi, lokal, dilindungi: frozenset[str] = DILINDUNGI_STAGING) -> Selisih`.
  - `tarik.TujuanSalinan` (dataclass): `akar: Path`, `baris` (Staging/HostingVps), `status_sumber: Callable[[], object]`, `cek_awal: Callable[[object], str | None]`, `periksa_info: Callable[[dict], None]`, `sql_tambahan: Callable[[Session, dict, Path], None]`, `impor: Callable[[dict, list[Path]], None]`, `siapkan_runtime: Callable[[Session, Job, dict], None]`, `dilindungi: frozenset[str] = DILINDUNGI_STAGING`, `subdir: tuple[str, ...] = ("files", "log", "ekspor")`, `tahap_akhir: str = "sertifikat"` (Koreksi #8).
  - `tarik.tarik_inti(sesi, job, site, klien, tujuan: TujuanSalinan, k: dict) -> dict`: menjalankan `manifest → berkas → tanda_air → db → impor → penyiapan`, lalu menyimpan `tahap = tujuan.tahap_akhir`; mengembalikan kemajuan terakhir (memuat `info`, `tanda_air`, `peringatan`, `byte_selesai`). `tujuan.periksa_info(k["info"])` dipanggil sesudah manifest lengkap, sebelum salinan disentuh; galat `GalatDitolakTanpaUbah` diteruskan.
  - `tarik._bangun_ulang_indeks(sesi, job, akar, peringatan, dilindungi=DILINDUNGI_STAGING)`, `tarik._sinkron_berkas(sesi, job, baris, klien, akar, produksi, k, dilindungi=DILINDUNGI_STAGING)`.
  - `tarik.tarik(...)` (staging) tidak berubah tanda tangan maupun perilakunya.

- [ ] **Step 1: Tulis test yang gagal.**

Tambahkan ke akhir `tests/unit/test_staging_rencana.py`:

```python
def test_selisih_dengan_daftar_dilindungi_milik_tujuan():
    dilindungi = frozenset({"wp-config.php", "wp-content/mu-plugins/wpmgr-pratinjau.php"})
    produksi = {"wp-content/mu-plugins/wpmgr-pratinjau.php": e("wp-content/mu-plugins/wpmgr-pratinjau.php", h=H2),
                "wp-content/mu-plugins/wpmgr-staging.php": e("wp-content/mu-plugins/wpmgr-staging.php")}
    lokal = {"wp-content/mu-plugins/wpmgr-pratinjau.php": e("wp-content/mu-plugins/wpmgr-pratinjau.php")}
    s = selisih(produksi, lokal, dilindungi)
    # Berkas milik tujuan tidak ditimpa; berkas staging bukan milik tujuan ini, jadi ikut disalin.
    assert [x.path for x in s.diambil] == ["wp-content/mu-plugins/wpmgr-staging.php"]
    assert s.hapus == []
```

File: `tests/integration/test_hosting_tarik_inti.py`
```python
import pytest
from staging_palsu import GB, ProduksiPalsu

from wpmgr.jobs.queue import buat_job
from wpmgr.models import JobType, Site
from wpmgr.staging import rencana, tarik, umum
from wpmgr.staging.pembantu import StatusPembantu

pytestmark = pytest.mark.integration

MTIME = 1_700_000_000
MILIK = "wp-content/mu-plugins/milik-tujuan.php"


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))
    monkeypatch.setattr(rencana, "UKURAN_PAKET", 1000)
    monkeypatch.setattr(tarik, "UKURAN_PAKET", 1000)


@pytest.fixture
def prod():
    p = ProduksiPalsu()
    p.berkas = {
        "index.php": (b"<?php // indeks", MTIME),
        "wp-content/uploads/besar.bin": (bytes(range(256)) * 10, MTIME),
        MILIK: (b"<?php // versi produksi", MTIME),
    }
    p.tabel = {
        "wp_posts": [b"DROP TABLE IF EXISTS `wp_posts`;\nCREATE TABLE `wp_posts` (`id` int);\n",
                     b"INSERT INTO `wp_posts` (`id`) VALUES ('1');\n"],
        "wp_options": [b"DROP TABLE IF EXISTS `wp_options`;\nCREATE TABLE `wp_options` (`a` text);\n"],
    }
    return p


def _tujuan(akar, baris, panggilan, **ganti):
    status = StatusPembantu(8 * GB, 200 * GB, 150 * GB, {}, {})
    dasar = dict(
        akar=akar, baris=baris,
        status_sumber=lambda: panggilan.append("status") or status,
        cek_awal=lambda st: panggilan.append("cek_awal"),
        periksa_info=lambda info: panggilan.append(("info", info["home"])),
        sql_tambahan=lambda sesi, info, d: panggilan.append("sql_tambahan"),
        impor=lambda info, berkas: panggilan.append(("impor", len(berkas))),
        siapkan_runtime=lambda sesi, job, info: panggilan.append("siapkan"),
        dilindungi=frozenset({"wp-config.php", MILIK}),
        subdir=("files", "log"),
        tahap_akhir="pratinjau",
    )
    dasar.update(ganti)
    return tarik.TujuanSalinan(**dasar)


def test_tarik_inti_memanggil_tujuan_berurutan_dan_menjaga_berkas_milik_tujuan(sesi, site_staging, prod, tmp_path):
    akar = tmp_path / "tujuan" / str(site_staging.site_id)
    (akar / "files" / "wp-content" / "mu-plugins").mkdir(parents=True)
    (akar / "files" / MILIK).write_bytes(b"<?php // milik tujuan")
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    panggilan: list = []
    k = tarik.tarik_inti(sesi, job, site, prod.klien(site), _tujuan(akar, site_staging, panggilan), umum.kemajuan(job))
    assert panggilan == ["status", "cek_awal", ("info", "https://contoh.test"), "sql_tambahan", ("impor", 4), "siapkan"]
    assert k["tahap"] == "pratinjau"
    assert k["info"]["prefix"] == "wp_" and k["tanda_air"]["sumber"]["comments"]["jumlah"] == 2
    assert (akar / "files" / "index.php").read_bytes() == b"<?php // indeks"
    assert (akar / "files" / "wp-content/uploads/besar.bin").read_bytes() == bytes(range(256)) * 10
    assert (akar / "files" / MILIK).read_bytes() == b"<?php // milik tujuan"
    assert (akar / "log").is_dir() and not (akar / "ekspor").exists()
    # Melanjutkan sesudah tahap akhir tidak mengulang apa pun.
    panggilan.clear()
    k2 = tarik.tarik_inti(sesi, job, site, prod.klien(site), _tujuan(akar, site_staging, panggilan), k)
    assert panggilan == ["status", "cek_awal"] and k2["tahap"] == "pratinjau"


def test_periksa_info_menolak_sebelum_salinan_disentuh(sesi, site_staging, prod, tmp_path):
    akar = tmp_path / "tujuan" / str(site_staging.site_id)
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_tarik)

    def tolak(info):
        raise umum.GalatDitolakTanpaUbah("Site lama memakai http.")

    with pytest.raises(umum.GalatDitolakTanpaUbah):
        tarik.tarik_inti(sesi, job, site, prod.klien(site), _tujuan(akar, site_staging, [], periksa_info=tolak),
                         umum.kemajuan(job))
    assert not any(route == "/staging/file" for route, _ in prod.diminta)
    assert list((akar / "files").iterdir()) == []


def test_cek_awal_menolak_tanpa_ubah(sesi, site_staging, prod, tmp_path):
    akar = tmp_path / "tujuan" / str(site_staging.site_id)
    site = sesi.get(Site, site_staging.site_id)
    job = buat_job(sesi, site.id, JobType.staging_tarik)
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        tarik.tarik_inti(sesi, job, site, prod.klien(site),
                         _tujuan(akar, site_staging, [], cek_awal=lambda st: "RAM kurang."), umum.kemajuan(job))
    assert e.value.pesan == "RAM kurang."
    assert prod.diminta == []
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_staging_rencana.py tests/integration/test_hosting_tarik_inti.py -q`. Expected: `TypeError: selisih() takes 2 positional arguments but 3 were given` dan `AttributeError: module 'wpmgr.staging.tarik' has no attribute 'TujuanSalinan'`.

- [ ] **Step 3: `selisih` dengan daftar dilindungi.** Di `src/wpmgr/staging/rencana.py`, ganti fungsi `selisih`:

```python
def selisih(produksi: dict[str, Entri], lokal: dict[str, Entri],
            dilindungi: frozenset[str] = DILINDUNGI_STAGING) -> Selisih:
    """`dilindungi`: berkas milik tujuan salinan (staging atau hosting) yang tidak pernah ditimpa/dihapus tarik."""
    hasil = Selisih()
    for path in sorted(produksi):
        if path in dilindungi:
            # Berkas milik tujuan sendiri tidak pernah ditimpa oleh tarik,
            # sekalipun produksi punya berkas bernama sama.
            continue
        e = produksi[path]
        if path not in lokal:
            hasil.baru.append(e)
        elif berubah(e, lokal[path]):
            hasil.berubah.append(e)
    hasil.hapus = sorted(p for p in lokal if p not in produksi and p not in dilindungi)
    return hasil
```

- [ ] **Step 4: `TujuanSalinan` dan `tarik_inti`.** Di `src/wpmgr/staging/tarik.py`:

Tambahkan ke impor standar:

```python
from collections.abc import Callable
from dataclasses import dataclass
```

Ganti fungsi `_sinkron_berkas`:

```python
def _sinkron_berkas(sesi, job, baris, klien, akar: Path, produksi: dict, k: dict,
                    dilindungi: frozenset[str] = DILINDUNGI_STAGING) -> dict:
    indeks = Indeks(akar / "indeks.jsonl")
    lokal = indeks.muat()
    beda = selisih(produksi, lokal, dilindungi)
    salin = Salin(sesi, job, baris, klien, akar / "files", indeks, lokal, k)
    for i, path in enumerate(beda.hapus):
        if i % HAPUS_PER_TITIK == 0:
            umum.titik_potongan(sesi, job, baris)
        salin.hapus(path)
    for pot in bagi_potongan(beda.diambil, ukuran_paket=UKURAN_PAKET):
        if pot.jenis == "rentang":
            # Satu berkas besar menjadi beberapa potongan rencana; diambil
            # sekali, mulai dari potongan pertamanya (lihat bagi_potongan).
            if pot.dari == 0:
                salin.besar(pot.berkas[0])
        else:
            salin.paket(pot.berkas)
    salin.rapikan()
    return _simpan(sesi, job, salin.k, tahap="tanda_air")
```

Ganti tanda tangan dan baris terakhir `_bangun_ulang_indeks`:

```python
def _bangun_ulang_indeks(sesi, job, akar: Path, peringatan: list[str],
                         dilindungi: frozenset[str] = DILINDUNGI_STAGING) -> None:
```

```python
    # Berkas milik tujuan sendiri tidak pernah menjadi bagian salinan.
    indeks.padatkan({p: e for p, e in lokal.items() if p not in dilindungi})
```

(isi lain fungsi itu tidak berubah).

Sesudah `berkas_secret_staging()` dan sebelum `def tarik(`, tambahkan:

```python
# ---- mesin salin bersama (spec Lapis 4 §10.2) -----------------------------------


@dataclass
class TujuanSalinan:
    """Ke mana dan bagaimana tarik menyalin: staging (Lapis 3) atau hosting VPS (Lapis 4).

    Mesin tarik (`tarik_inti`) tidak tahu tujuannya; yang berbeda hanya
    sumber status server, gerbang awal, pemeriksaan info site, SQL tambahan,
    impor, dan penyiapan runtime. `baris` dipakai titik potongan (kolom
    `batal_diminta_pada`). `dilindungi`: berkas milik tujuan di files/ yang
    tidak pernah ditimpa atau dihapus tarik.
    """

    akar: Path
    baris: object
    status_sumber: Callable[[], object]
    cek_awal: Callable[[object], str | None]
    periksa_info: Callable[[dict], None]
    sql_tambahan: Callable[[object, dict, Path], None]
    impor: Callable[[dict, list[Path]], None]
    siapkan_runtime: Callable[[object, object, dict], None]
    dilindungi: frozenset = DILINDUNGI_STAGING
    subdir: tuple = ("files", "log", "ekspor")
    tahap_akhir: str = "sertifikat"


def tarik_inti(sesi, job, site, klien, tujuan: TujuanSalinan, k: dict) -> dict:
    """Tahap salin bersama: manifest -> berkas -> tanda_air -> db -> impor -> penyiapan.

    Semantik Lapis 3 tidak berubah: R21 (penolakan sebelum salinan disentuh =
    tanpa ubah), F3 (indeks dibangun ulang dari files/ di awal tarik baru),
    anggaran byte, dan peringatan. `k` adalah kemajuan job saat dipanggil;
    nilai kembali adalah kemajuan terakhir dengan `tahap = tujuan.tahap_akhir`.
    """
    akar = tujuan.akar
    tarik_dir = akar / "tarik"
    # "tahap" (bukan sekadar kemajuan kosong): uji update memakai kemajuan
    # yang sama dan sudah menyimpan tahap_uji sebelum memanggil tarik().
    baru = "tahap" not in k
    for d in tujuan.subdir:
        (akar / d).mkdir(parents=True, exist_ok=True)

    try:
        status = tujuan.status_sumber()
        pesan = tujuan.cek_awal(status)
        if pesan:
            raise _tolak(job, pesan)

        if baru:
            shutil.rmtree(tarik_dir, ignore_errors=True)
            peringatan: list[str] = []
            _bangun_ulang_indeks(sesi, job, akar, peringatan, tujuan.dilindungi)
            k = umum.simpan_kemajuan(sesi, job, tahap="manifest", mulai=umum.sekarang().isoformat(),
                                     byte_selesai=0, byte_diterima=0, byte_total=0,
                                     peringatan=peringatan[:MAKS_PERINGATAN], berkas_dilewati=0)
        tarik_dir.mkdir(parents=True, exist_ok=True)

        if k["tahap"] == "manifest":
            k = ambil_manifest(sesi, job, tujuan.baris, klien, tarik_dir, k)
            # Info site yang tidak bisa dilayani tujuan ditolak di sini, sebelum
            # salinan disentuh (R21): tahap masih "manifest".
            tujuan.periksa_info(k["info"])
            produksi = _muat_manifest(tarik_dir)
            lokal = Indeks(akar / "indeks.jsonl").muat()
            beda = selisih(produksi, lokal, tujuan.dilindungi)
            pesan = cek_disk(status, kebutuhan_disk(beda.byte, k["info"]["ukuran_db"]))
            if pesan:
                raise _tolak(job, pesan)
            info = k["info"]
            if k.get("berkas_dilewati"):
                _tambah_peringatan(k, f"{k['berkas_dilewati']} berkas dilewati (nama bukan UTF-8, symlink, "
                                      "tidak terbaca, atau path tidak sah).")
            if info["tabel_dilewati"]:
                _tambah_peringatan(k, f"{info['tabel_dilewati']} tabel produksi dilewati (view rusak, "
                                      "nama tidak sah, atau lebih dari 2000 tabel).")
            if info["php_peringatan"]:
                _tambah_peringatan(k, f"PHP produksi {info['php'] or 'tidak diketahui'} tidak tersedia; "
                                      f"salinan memakai PHP {info['versi_php']}.")
            k = _simpan(sesi, job, k, tahap="berkas", byte_total=beda.byte, byte_selesai=0, byte_diterima=0)

        info = k["info"]
        if k["tahap"] == "berkas":
            produksi = _muat_manifest(tarik_dir)
            k = _sinkron_berkas(sesi, job, tujuan.baris, klien, akar, produksi, k, tujuan.dilindungi)

        if k["tahap"] == "tanda_air":
            umum.titik_potongan(sesi, job, tujuan.baris)
            # Diambil sebelum ekspor database (Koreksi #20 Lapis 3): data yang
            # masuk selama ekspor ikut terdeteksi sebagai "baru" saat dorong.
            ta = urai_tanda_air(umum.ulangi(klien.staging_tanda_air))
            if ta is None:
                raise umum.galat_gagal("Tanda air produksi tidak dapat dibaca.")
            k = _simpan(sesi, job, k, tahap="db", tanda_air=ta, tabel={}, tabel_seq={}, tabel_selesai=[],
                        db_diterima=0)

        if k["tahap"] == "db":
            k = ekspor_db(sesi, job, tujuan.baris, klien, tarik_dir, info, k, "impor")

        if k["tahap"] == "impor":
            umum.titik_potongan(sesi, job, tujuan.baris)
            # Impor yang gagal/terputus meninggalkan database setengah terisi;
            # tahap ini selalu diulang utuh: pembuatan DB idempoten, dan impor
            # di skrip pembantu membuang lalu membuat ulang database dulu.
            tujuan.sql_tambahan(sesi, info, tarik_dir / "db")
            with umum.detak_latar(sesi, job):
                tujuan.impor(info, [tarik_dir / "prelude.sql", *sorted((tarik_dir / "db").glob("*.sql"))])
            k = _simpan(sesi, job, k, tahap="penyiapan")

        if k["tahap"] == "penyiapan":
            umum.titik_potongan(sesi, job, tujuan.baris)
            tujuan.siapkan_runtime(sesi, job, info)
            k = _simpan(sesi, job, k, tahap=tujuan.tahap_akhir)
    except umum.Dibatalkan:
        shutil.rmtree(tarik_dir, ignore_errors=True)
        raise
    except umum.GalatDitolakTanpaUbah:
        raise
    except SiteError as exc:
        # Penolakan dari tahap manifest (multisite, wp-content di luar
        # ABSPATH, connector menolak) sebelum salinan disentuh: tanpa ubah (R21).
        if exc.error_class == STAGING_DITOLAK and salinan_belum_disentuh(job):
            raise umum.GalatDitolakTanpaUbah(exc.pesan) from None
        raise
    except PathTidakAman:
        # Container menukar direktori di files/ dengan symlink di sela
        # pemeriksaan; pesan tetap, tanpa path VPS. Tarik berikutnya menghapus
        # symlink itu (_bangun_ulang_indeks).
        raise umum.galat_gagal("Struktur folder salinan tidak aman (ada symlink yang ditukar selama tarik); "
                               "jalankan tarik lagi.") from None
    return k


def _tujuan_staging(sesi, job, site, staging: Staging, pb) -> TujuanSalinan:
    """Tujuan salinan Lapis 3: staging `<nama>.<domain staging>` (perilaku tidak berubah)."""
    akar = umum.dir_site(site.id)

    def cek_awal(status) -> str | None:
        if staging.aktif:
            return None
        return cek_ram(status) or cek_maks_aktif(jumlah_aktif(sesi, staging), get_settings().staging_maks_aktif)

    def impor(info: dict, berkas: list[Path]) -> None:
        pb.db_buat(staging.nama, site.id, info["prefix"])
        pb.db_impor(staging.nama, berkas)

    return TujuanSalinan(
        akar=akar, baris=staging, status_sumber=pb.status, cek_awal=cek_awal,
        periksa_info=lambda info: None,
        sql_tambahan=lambda sesi_, info, db_dir: berkas_secret_staging(sesi_, staging, info, db_dir),
        impor=impor,
        siapkan_runtime=lambda sesi_, job_, info: _siapkan_runtime(sesi_, job_, staging, site, pb, akar, info),
    )
```

Ganti fungsi `tarik()` seluruhnya:

```python
def tarik(sesi, job, site, staging: Staging, klien, pb, akhir_status: bool = True) -> dict:
    if not punya_fitur(site, STAGING):
        raise _tolak(job, "Connector site ini belum mengizinkan staging. Aktifkan 'Izinkan staging' "
                          "di Pengaturan -> WP Manager (connector 3.0).")
    if not staging.sandi_hash or not staging.rahasia_router_terenkripsi:
        raise _tolak(job, "Akses preview staging belum dibuat; buat ulang kata sandi preview.")
    akar = umum.dir_site(site.id)
    tarik_dir = akar / "tarik"
    pertama = staging.ditarik_pada is None
    k = tarik_inti(sesi, job, site, klien, _tujuan_staging(sesi, job, site, staging, pb), umum.kemajuan(job))
    info = k["info"]

    sertifikat_ok = True
    try:
        with umum.detak_latar(sesi, job):
            pb.sertifikat(staging.nama)
    except GalatPembantu as exc:
        # Staging tetap bisa dibuka lewat SSO dashboard (spec §12); cron
        # renew-staging-certs mencoba lagi. exc.pesan adalah teks tetap.
        sertifikat_ok = False
        _tambah_peringatan(k, f"Sertifikat belum terbit: {exc.pesan}")

    indeks = Indeks(akar / "indeks.jsonl")
    lokal = indeks.muat()
    indeks.padatkan(lokal)
    try:
        # log/ di-bind mount ke container: dihapus hanya lewat `aman`.
        hapus_berkas(akar, "log/diubah")
    except (PathTidakAman, OSError):
        pass
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
        "peringatan": list(k.get("peringatan") or []),
    }
    umum.catat_aktivitas(sesi, site.id, job, "Staging dibuat" if pertama else "Staging disegarkan", {
        **hasil, "ukuran_file_teks": format_byte(st.ukuran_file), "peringatan": hasil["peringatan"][:10]})
    sesi.commit()
    return hasil
```

Catatan: dua teks pesan di `tarik_inti` berubah sedikit dari Lapis 3 ("staging memakai PHP" → "salinan memakai PHP", "Struktur folder staging" → "Struktur folder salinan") karena dipakai kedua tujuan. Tidak ada test Lapis 3 yang mencocokkan kedua teks lama itu (`grep -rn "memakai PHP\|Struktur folder staging" tests/` kosong).

- [ ] **Step 5: Jalankan test, termasuk jaring pengaman staging.** Run: `.venv/Scripts/python -m pytest tests/unit/test_staging_rencana.py tests/integration/test_hosting_tarik_inti.py tests/integration/test_staging_tarik.py tests/integration/test_staging_uji.py tests/integration/test_staging_dorong.py tests/integration/test_staging_kembalikan.py tests/integration/test_staging_antrean.py -q`. Expected: semua lulus, tanpa satu pun test Lapis 3 diubah.

- [ ] **Step 6: Seluruh test unit dan integrasi, lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 7: Commit.**

```bash
git add src/wpmgr/staging/tarik.py src/wpmgr/staging/rencana.py tests/unit/test_staging_rencana.py tests/integration/test_hosting_tarik_inti.py
git commit -m "refactor(staging): mesin tarik_inti dengan TujuanSalinan untuk staging dan hosting

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Fase C — Jalur inti

### Task 8: Mu-plugin pratinjau dan job `pindah_tarik`

**Files:**
- Create: `connector/wp-manager-connector/templates/wpmgr-pratinjau.php.tpl`, `connector/tests/PratinjauTest.php`, `src/wpmgr/hosting/pindah.py`
- Modify: `src/wpmgr/connector_paket.py`, `src/wpmgr/jobs/handlers.py`, `tests/integration/staging_palsu.py`
- Test: `tests/unit/test_connector_paket.py` (tambah), `tests/unit/test_hosting_pindah.py`, `tests/integration/test_hosting_pindah.py`

**Interfaces:**
- Consumes (Task 4–7): `HostingVps`, `StatusHosting`, `hosting.umum` (`klien_lama`, `jalankan_hosting`, `dir_hosting`, `sekarang`, `ASAL_SALINAN`), `tarik.TujuanSalinan`, `tarik.tarik_inti`, `tarik._tolak`, `tarik._tambah_peringatan`, `Pembantu.prod_*`, `StatusProd`, `tulis_htpasswd_pratinjau`, `staging.umum` (`detak_latar`, `titik_potongan`, `simpan_kemajuan`, `kemajuan`, `catat_aktivitas`, `buat_pembantu`, `GalatDitolakTanpaUbah`, `galat_gagal`, `KlaimHilang`), `rencana.RAM_MINIMUM`, `rencana.format_byte`, `aman.tulis_atomik`, `indeks.Indeks`, fixture `hosting_aktif`, `site_hosting`.
- Produces:
  - Template `wpmgr-pratinjau.php.tpl` (tanpa placeholder; diam tanpa `WPMGR_PRATINJAU`; `pre_wp_mail` → false; `phpmailer_init` prioritas `PHP_INT_MAX` mengosongkan penerima; `pre_option_blog_public` → `'0'`; `wp_robots` noindex/nofollow; spanduk admin dan simpul admin bar; `ob_start` pengganti URL hanya untuk host `WPMGR_PRATINJAU_HOST`).
  - `connector_paket.NAMA_TEMPLATE_PRATINJAU`, `connector_paket.isi_mu_plugin_pratinjau(sumber: Path | None = None) -> str`.
  - `hosting.pindah`: `MU_PLUGIN_PRATINJAU = "wp-content/mu-plugins/wpmgr-pratinjau.php"`, `DILINDUNGI_HOSTING`, pesan `PESAN_IZIN`, `PESAN_SUDAH_DILAYANI`, `PESAN_SANDI_BELUM`, `PESAN_STATUS_TARIK`, `PESAN_HTTP`, `PESAN_SUBFOLDER`, `PESAN_HOST_LAIN`, `PESAN_WWW`, `PESAN_MU_PLUGIN`; fungsi `cek_ram_hosting(status) -> str | None`, `periksa_info_hosting(h, info) -> None`, `tujuan_hosting(sesi, job, site, h, pb) -> TujuanSalinan`, `tulis_mu_plugin(akar: Path) -> None`, `salin(sesi, job, site, h, klien, pb) -> dict` (kemajuan), `rampungkan_salinan(sesi, job, site, h, k, sertifikat_ok: bool | None) -> dict` (hasil), `pindah_tarik(sesi, job, site, h, klien, pb) -> dict`, `bersihkan_bila_final(sesi, site_id)`, `tangani_pindah_tarik(sesi, job, klien) -> dict`.
  - `handlers.HANDLER[JobType.pindah_tarik] = tangani_pindah_tarik`.
  - `staging_palsu.PembantuHostingPalsu(dir_hosting)` (turunan `PembantuPalsu`): atribut `status_prod: StatusProd`, `state: dict[str, dict]`, `sertifikat_hasil: str`; metode `prod_status`, `prod_buat`, `prod_db_buat` (menolak bila state belum ada atau `mode == "aktif"`; menulis `files/wp-config.php` berisi `WPMGR_PRATINJAU`), `prod_db_impor` (menolak `mode == "aktif"`), `prod_router_muat`, `prod_domain`, `prod_sertifikat`, `prod_aktifkan` (menulis `wp-config.php` tanpa `WPMGR_PRATINJAU`, `mode = "aktif"`), `prod_hapus` (menolak `mode == "aktif"`), `prod_backup`, `prod_backup_hapus`; semua mencatat lewat `_catat` dan mematuhi `gagal[<nama metode>]`.

- [ ] **Step 1: Tulis test PHP yang gagal.**

File: `connector/tests/PratinjauTest.php`
```php
<?php
use PHPUnit\Framework\TestCase;

/**
 * Mu-plugin pratinjau VPS (spec Lapis 4 §7.6, §18.2). Dijalankan di proses
 * PHP terpisah dengan stub add_filter/add_action, supaya stub WordPress milik
 * bootstrap test tidak ikut dan perilaku `return` di puncak berkas teruji.
 */
final class PratinjauTest extends TestCase {

    private function templat(): string {
        return __DIR__ . '/../wp-manager-connector/templates/wpmgr-pratinjau.php.tpl';
    }

    private function jalankan( string $awal, string $akhir ): array {
        $skrip  = sys_get_temp_dir() . '/wpmgr-pratinjau-' . getmypid() . '-' . mt_rand() . '.php';
        $kepala = <<<'PHP'
<?php
define( 'ABSPATH', '/tmp/' );
$GLOBALS['wpmgr_kait'] = array();
function add_filter( $nama, $fungsi, $prioritas = 10, $argumen = 1 ) {
    $GLOBALS['wpmgr_kait'][ $nama ][] = array( $fungsi, $prioritas );
    return true;
}
function add_action( $nama, $fungsi, $prioritas = 10, $argumen = 1 ) {
    return add_filter( $nama, $fungsi, $prioritas, $argumen );
}
function esc_html( $teks ) {
    return htmlspecialchars( $teks, ENT_QUOTES );
}
function wpmgr_kait( $nama ) {
    return $GLOBALS['wpmgr_kait'][ $nama ][0];
}
PHP;
        file_put_contents( $skrip, $kepala . "\n" . $awal . "\ninclude " . var_export( $this->templat(), true )
            . ";\n" . $akhir . "\n" );
        exec( escapeshellarg( PHP_BINARY ) . ' ' . escapeshellarg( $skrip ) . ' 2>&1', $keluar, $kode );
        unlink( $skrip );
        $this->assertSame( 0, $kode, implode( "\n", $keluar ) );
        return $keluar;
    }

    private function konstanta( string $host ): string {
        return "define( 'WPMGR_PRATINJAU', true );\n"
            . "define( 'WPMGR_PRATINJAU_HOST', 'vps-toko.staging.contoh.id' );\n"
            . "define( 'WPMGR_DOMAIN', 'toko.co.id' );\n"
            . "\$_SERVER['HTTP_HOST'] = " . var_export( $host, true ) . ";\n";
    }

    public function test_template_tanpa_placeholder_dan_valid_php(): void {
        $isi = file_get_contents( $this->templat() );
        $this->assertStringStartsWith( '<?php', $isi );
        $this->assertStringNotContainsString( '__WPMGR_', $isi );
        exec( escapeshellarg( PHP_BINARY ) . ' -l ' . escapeshellarg( $this->templat() ) . ' 2>&1', $keluar, $kode );
        $this->assertSame( 0, $kode, implode( "\n", $keluar ) );
    }

    public function test_diam_tanpa_konstanta(): void {
        $keluar = $this->jalankan( "\$_SERVER['HTTP_HOST'] = 'vps-toko.staging.contoh.id';",
            "echo count( \$GLOBALS['wpmgr_kait'] ), '|', ob_get_level();" );
        $this->assertSame( array( '0|0' ), $keluar );
    }

    public function test_pre_wp_mail_false_dengan_prioritas_terakhir(): void {
        $keluar = $this->jalankan( $this->konstanta( 'toko.co.id' ),
            "\$k = wpmgr_kait( 'pre_wp_mail' ); echo json_encode( array( \$k[0]( null, array() ), \$k[1] === PHP_INT_MAX ) );" );
        $this->assertSame( array( '[false,true]' ), $keluar );
    }

    public function test_penerima_phpmailer_dikosongkan(): void {
        $awal = $this->konstanta( 'toko.co.id' )
            . "class PhpMailerTiruan { public \$dikosongkan = false; "
            . "public function clearAllRecipients() { \$this->dikosongkan = true; } }";
        $keluar = $this->jalankan( $awal,
            "\$m = new PhpMailerTiruan(); \$k = wpmgr_kait( 'phpmailer_init' ); \$k[0]( \$m ); "
            . "echo json_encode( array( \$m->dikosongkan, \$k[1] === PHP_INT_MAX ) );" );
        $this->assertSame( array( '[true,true]' ), $keluar );
    }

    public function test_blog_public_nol_dan_noindex(): void {
        $keluar = $this->jalankan( $this->konstanta( 'toko.co.id' ),
            "\$b = wpmgr_kait( 'pre_option_blog_public' ); \$r = wpmgr_kait( 'wp_robots' ); "
            . "echo json_encode( array( \$b[0](), \$r[0]( array( 'max-image-preview' => 'large' ) ) ) );" );
        $this->assertSame( array( '["0",{"max-image-preview":"large","noindex":true,"nofollow":true}]' ), $keluar );
    }

    public function test_spanduk_dan_admin_bar(): void {
        $awal   = $this->konstanta( 'toko.co.id' )
            . "class BarTiruan { public \$simpul = array(); public function add_node( \$n ) { \$this->simpul[] = \$n; } }";
        $keluar = $this->jalankan( $awal,
            "\$a = wpmgr_kait( 'admin_notices' ); \$a[0](); \$bar = new BarTiruan(); \$m = wpmgr_kait( 'admin_bar_menu' ); "
            . "\$m[0]( \$bar ); echo \"\\n\", \$bar->simpul[0]['title'];" );
        $this->assertStringContainsString( 'PRATINJAU VPS', $keluar[0] );
        $this->assertStringContainsString( 'email diblokir, cron mati', $keluar[0] );
        $this->assertSame( 'PRATINJAU VPS — email diblokir, cron mati', $keluar[1] );
    }

    public function test_url_diganti_hanya_untuk_host_pratinjau(): void {
        $html  = 'a https://toko.co.id/x b https://www.toko.co.id/y c https:\\/\\/toko.co.id\\/z d https://lain.id/';
        $akhir = 'echo ' . var_export( $html, true ) . '; while ( ob_get_level() > 0 ) { ob_end_flush(); }';
        $this->assertSame(
            array( 'a https://vps-toko.staging.contoh.id/x b https://vps-toko.staging.contoh.id/y '
                . 'c https:\\/\\/vps-toko.staging.contoh.id\\/z d https://lain.id/' ),
            $this->jalankan( $this->konstanta( 'vps-toko.staging.contoh.id' ), $akhir )
        );
        $this->assertSame( array( $html ), $this->jalankan( $this->konstanta( 'toko.co.id' ), $akhir ) );
    }
}
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `cd connector && vendor/bin/phpunit --filter PratinjauTest`. Expected: 7 test gagal (`file_get_contents(...wpmgr-pratinjau.php.tpl): Failed to open stream` / kode keluar bukan 0).

- [ ] **Step 3: Template mu-plugin.**

File: `connector/wp-manager-connector/templates/wpmgr-pratinjau.php.tpl`
```php
<?php
/**
 * Plugin Name: WP Manager — pengaman pratinjau VPS
 * Description: Dipasang dashboard WP Manager selama pratinjau pindah hosting. Email diblokir, mesin pencari ditolak, dan tautan diarahkan ke host pratinjau. Diam sepenuhnya di luar pratinjau.
 */
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}
// Tanpa konstanta dari wp-config.php mode pratinjau, berkas ini diam
// sepenuhnya: aktivasi mencabut konstanta itu, jadi berkas yang tertinggal
// sesudah aktivasi tidak berbuat apa-apa (spec Lapis 4 §7.6).
if ( ! defined( 'WPMGR_PRATINJAU' ) || ! WPMGR_PRATINJAU ) {
    return;
}

// Email tidak pernah keluar dari salinan pratinjau. WordPress >= 5.7:
// pre_wp_mail menghentikan wp_mail() sebelum PHPMailer disentuh.
add_filter( 'pre_wp_mail', function () {
    return false;
}, PHP_INT_MAX );

// WordPress lebih lama: penerima dikosongkan di saat terakhir (sesudah
// plugin SMTP mengatur PHPMailer), sehingga PHPMailer menolak mengirim.
add_action( 'phpmailer_init', function ( $phpmailer ) {
    $phpmailer->clearAllRecipients();
}, PHP_INT_MAX );

// Mesin pencari ditolak tanpa mengubah database (blog_public tetap nilai asli).
add_filter( 'pre_option_blog_public', function () {
    return '0';
} );

add_filter( 'wp_robots', function ( $robots ) {
    $robots['noindex']  = true;
    $robots['nofollow'] = true;
    return $robots;
} );

add_action( 'admin_notices', function () {
    // esc_html() walau literal tetap: berkas ini tinggal di salinan site
    // client dan mengikuti aturan "semua string lewat esc()" dashboard.
    echo '<div class="notice notice-warning"><p><strong>' . esc_html( 'PRATINJAU VPS' ) . '</strong> — '
        . esc_html( 'email diblokir, cron mati. Ini salinan pindah hosting; situs asli masih dilayani hosting lama.' )
        . '</p></div>';
} );

add_action( 'admin_bar_menu', function ( $bar ) {
    $bar->add_node( array( 'id' => 'wpmgr-pratinjau', 'title' => 'PRATINJAU VPS — email diblokir, cron mati' ) );
}, 1 );

// Hanya untuk request ke host pratinjau: URL domain asli di keluaran
// (termasuk bentuk ber-escape JSON) diganti host pratinjau. Database tidak
// diubah; tanpa ini gambar dan tautan di konten mengarah ke hosting lama.
if ( defined( 'WPMGR_PRATINJAU_HOST' ) && defined( 'WPMGR_DOMAIN' )
    && isset( $_SERVER['HTTP_HOST'] ) && WPMGR_PRATINJAU_HOST === $_SERVER['HTTP_HOST'] ) {
    ob_start( function ( $html ) {
        $ke    = 'https://' . WPMGR_PRATINJAU_HOST;
        $ganti = array();
        foreach ( array( 'https://www.' . WPMGR_DOMAIN, 'https://' . WPMGR_DOMAIN ) as $asal ) {
            $ganti[ $asal ] = $ke;
            $ganti[ str_replace( '/', '\\/', $asal ) ] = str_replace( '/', '\\/', $ke );
        }
        // strtr mendahulukan kunci terpanjang, jadi www.<domain> tidak terpotong.
        return strtr( $html, $ganti );
    } );
}
```

- [ ] **Step 4: Jalankan PHPUnit di PHP 8.3 dan 7.4.** Run: `cd connector && vendor/bin/phpunit` lalu perintah PHP 7.4 dari Global Constraints. Expected: keduanya `OK` (termasuk 7 test baru).

- [ ] **Step 5: Tulis test Python yang gagal.**

Tambahkan ke akhir `tests/unit/test_connector_paket.py`:

```python
def test_isi_mu_plugin_pratinjau_tanpa_placeholder():
    from wpmgr.connector_paket import isi_mu_plugin_pratinjau

    isi = isi_mu_plugin_pratinjau()
    assert isi.startswith("<?php")
    for harus in ("WPMGR_PRATINJAU", "pre_wp_mail", "phpmailer_init", "pre_option_blog_public", "wp_robots",
                  "WPMGR_PRATINJAU_HOST", "ob_start"):
        assert harus in isi, harus
    assert "__WPMGR_" not in isi
```

File: `tests/unit/test_hosting_pindah.py`
```python
from types import SimpleNamespace

import pytest

from wpmgr.hosting.pindah import (
    PESAN_HOST_LAIN,
    PESAN_HTTP,
    PESAN_SUBFOLDER,
    PESAN_WWW,
    cek_ram_hosting,
    periksa_info_hosting,
)
from wpmgr.staging.pembantu import StatusProd
from wpmgr.staging.umum import GalatDitolakTanpaUbah

GB = 1024**3


@pytest.mark.parametrize("home,siteurl,www,pesan", [
    ("http://toko.co.id", "https://toko.co.id", True, PESAN_HTTP),
    ("https://toko.co.id", "http://toko.co.id", True, PESAN_HTTP),
    ("https://toko.co.id/blog", "https://toko.co.id/blog", True, PESAN_SUBFOLDER),
    ("https://toko.co.id:8443", "https://toko.co.id:8443", True, PESAN_SUBFOLDER),
    ("https://lain.id", "https://lain.id", True, PESAN_HOST_LAIN),
    ("https://toko.co.id.lain.id", "https://toko.co.id.lain.id", True, PESAN_HOST_LAIN),
    ("https://www.toko.co.id", "https://www.toko.co.id", False, PESAN_WWW),
])
def test_periksa_info_hosting_menolak(home, siteurl, www, pesan):
    h = SimpleNamespace(domain="toko.co.id", dengan_www=www)
    with pytest.raises(GalatDitolakTanpaUbah) as e:
        periksa_info_hosting(h, {"home": home, "siteurl": siteurl})
    assert e.value.pesan == pesan


@pytest.mark.parametrize("home,www", [
    ("https://toko.co.id", False), ("https://www.toko.co.id", True), ("https://TOKO.co.id/", False),
])
def test_periksa_info_hosting_menerima(home, www):
    periksa_info_hosting(SimpleNamespace(domain="toko.co.id", dengan_www=www), {"home": home, "siteurl": home})


def test_cek_ram_hosting():
    assert cek_ram_hosting(StatusProd(3 * GB, 1, 1, 1, 1, {})) is None
    pesan = cek_ram_hosting(StatusProd(1 * GB, 1, 1, 1, 1, {}))
    assert pesan.startswith("RAM tersedia di VPS 1,0 GB; minimal 2,0 GB")
```

Tambahkan ke akhir `tests/integration/staging_palsu.py`:

```python
class PembantuHostingPalsu(PembantuPalsu):
    """Subperintah prod-* tiruan (Lapis 4) yang meniru aturan state skrip asli.

    `state[nama]` dibuat `prod_buat`; `prod_db_buat` tanpa state ditolak
    seperti skrip (Koreksi #1), dan impor/hapus ditolak sesudah `mode` aktif.
    """

    def __init__(self, dir_hosting: Path) -> None:
        super().__init__(dir_hosting)
        self.status_prod = StatusProd(8 * GB, 200 * GB, 150 * GB, 200 * GB, 150 * GB, {})
        self.state: dict[str, dict] = {}
        self.sertifikat_hasil = "terbit"

    def _tolak(self, aksi: str) -> GalatPembantu:
        return GalatPembantu("ditolak", f"{aksi} gagal. Skrip pembantu menolak permintaan ini.")

    def _wp_config(self, nama: str, isi: bytes) -> None:
        berkas = self.dir / self.state[nama]["site_id"] / "files" / "wp-config.php"
        berkas.parent.mkdir(parents=True, exist_ok=True)
        berkas.write_bytes(isi)

    def prod_status(self):
        self._catat("prod_status")
        return self.status_prod

    def prod_buat(self, nama, versi, site_id, domain, www):
        self._catat("prod_buat", nama, versi, str(site_id), domain, www)
        st = self.state.setdefault(nama, {"site_id": str(site_id), "domain": domain, "mode": "pratinjau",
                                          "prefix": "", "php": versi})
        if st["site_id"] != str(site_id) or st["domain"] != domain:
            raise self._tolak("Membuat container situs")

    def prod_db_buat(self, nama, prefix):
        self._catat("prod_db_buat", nama, prefix)
        st = self.state.get(nama)
        if st is None or st["mode"] == "aktif":
            raise self._tolak("Membuat database situs")
        st["prefix"] = prefix
        self._wp_config(nama, b"<?php define( 'WPMGR_PRATINJAU', true ); // pratinjau")

    def prod_db_impor(self, nama, berkas):
        self._catat("prod_db_impor", nama, len(berkas))
        st = self.state.get(nama)
        if st is None or st["mode"] == "aktif":
            raise self._tolak("Mengimpor database situs")
        self.sql = b"".join(Path(b).read_bytes() for b in berkas)

    def prod_router_muat(self):
        self._catat("prod_router_muat")

    def prod_domain(self, nama):
        self._catat("prod_domain", nama)

    def prod_sertifikat(self, nama):
        self._catat("prod_sertifikat", nama)
        return self.sertifikat_hasil

    def prod_aktifkan(self, nama):
        self._catat("prod_aktifkan", nama)
        self.state[nama]["mode"] = "aktif"
        self._wp_config(nama, b"<?php // aktif")
        return "aktif"

    def prod_hapus(self, nama):
        self._catat("prod_hapus", nama)
        if self.state.get(nama, {}).get("mode") == "aktif":
            raise self._tolak("Menghapus situs hosting")
        self.state.pop(nama, None)

    def prod_backup(self, nama, stempel):
        self._catat("prod_backup", nama, stempel)
        st = self.state[nama]
        return json.dumps({"versi": 1, "site_id": st["site_id"], "nama": nama, "domain": st["domain"],
                           "stempel": stempel, "versi_php": st["php"], "prefix": st["prefix"],
                           "ukuran_db": 100, "ukuran_file": 200, "sha256_db": "a" * 64,
                           "sha256_file": "b" * 64}) + "\n"

    def prod_backup_hapus(self, nama, stempel):
        self._catat("prod_backup_hapus", nama, stempel)
```

dan ubah baris impor di puncak berkas yang sama:

```python
from wpmgr.staging.pembantu import GalatPembantu, StatusPembantu, StatusProd
```

File: `tests/integration/test_hosting_pindah.py`
```python
from datetime import datetime, timezone

import httpx
import pytest
from staging_palsu import GB, PembantuHostingPalsu, ProduksiPalsu

from wpmgr.connector_paket import isi_mu_plugin_pratinjau
from wpmgr.errors import STAGING_GAGAL, TRANSIENT, SiteError
from wpmgr.hosting import pindah
from wpmgr.hosting import umum as hu
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import buat_job
from wpmgr.models import ActivityLog, HostingVps, JobStatus, JobType, StatusHosting
from wpmgr.staging import rencana, tarik, umum
from wpmgr.staging.pembantu import GalatPembantu, StatusProd

pytestmark = pytest.mark.integration

MTIME = 1_700_000_000
IP_LAMA = "93.184.216.34"


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))
    monkeypatch.setattr(rencana, "UKURAN_PAKET", 1000)
    monkeypatch.setattr(tarik, "UKURAN_PAKET", 1000)


@pytest.fixture
def prod():
    p = ProduksiPalsu()
    p.info = {**p.info, "home": "https://toko.co.id", "siteurl": "https://toko.co.id"}
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
def pb(hosting_aktif, monkeypatch):
    palsu = PembantuHostingPalsu(hosting_aktif)
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


@pytest.fixture
def lama(prod, monkeypatch):
    """Klien hosting lama ASLI (dipatok IP) di atas transport tiruan; mencatat (host URL, header Host)."""
    host: list[tuple[str, str]] = []

    def tangani(r):
        host.append((r.url.host, r.headers.get("host")))
        return prod.tangani(r)

    monkeypatch.setattr(hu, "buat_http_lama", lambda: httpx.Client(transport=httpx.MockTransport(tangani)))
    return host


def _jalankan(sesi, site_hosting, job=None):
    job = job or buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    hasil = pindah.tangani_pindah_tarik(sesi, job, None)
    job.status = JobStatus.success
    sesi.commit()
    return job, hasil


def _h(sesi, site_hosting):
    return sesi.get(HostingVps, site_hosting.id, populate_existing=True)


def _files(hosting_aktif, site_hosting):
    return hosting_aktif / str(site_hosting.site_id) / "files"


def test_handler_terdaftar():
    assert handlers.HANDLER[JobType.pindah_tarik] is pindah.tangani_pindah_tarik


def test_pindah_tarik_penuh(sesi, site_hosting, hosting_aktif, prod, pb, lama):
    _, hasil = _jalankan(sesi, site_hosting)
    files = _files(hosting_aktif, site_hosting)
    assert (files / "index.php").read_bytes() == b"<?php // indeks"
    assert (files / "wp-content/uploads/besar.bin").read_bytes() == bytes(range(256)) * 10
    mu = files / "wp-content/mu-plugins/wpmgr-pratinjau.php"
    assert mu.read_text(encoding="utf-8") == isi_mu_plugin_pratinjau()
    assert (hosting_aktif / "router" / "toko-co-id.htpasswd").read_bytes().startswith(b"pratinjau:$2b$")
    sid = str(site_hosting.site_id)
    assert pb.nama_panggilan() == ["prod_status", "prod_buat", "prod_db_buat", "prod_db_impor", "prod_buat",
                                   "prod_router_muat", "sertifikat", "prod_domain"]
    assert ("prod_buat", "toko-co-id", "8.1", sid, "toko.co.id", True) in pb.panggilan
    assert ("prod_db_buat", "toko-co-id", "wp_") in pb.panggilan
    assert ("sertifikat", "vps-toko-co-id") in pb.panggilan
    assert ("prod_domain", "toko-co-id") in pb.panggilan
    assert pb.sql.startswith(b"SET NAMES utf8mb4;")
    # Putusan R25 tidak berlaku: secret connector produksi dibiarkan apa adanya (spec §4.1).
    assert b"wpmgr_secret" not in pb.sql
    assert lama and all(x == (IP_LAMA, "toko.co.id") for x in lama)
    akar = hosting_aktif / sid
    assert not (akar / "tarik").exists() and not (akar / "ekspor").exists()
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.pratinjau and h.galat is None
    assert h.ditarik_pada is not None and h.pratinjau_sertifikat_pada is not None
    assert h.versi_php == "8.1"
    assert h.ukuran_file == len(b"<?php // indeks") + len(b"body{}") + 2560
    assert hasil["ukuran_file"] == h.ukuran_file
    log = sesi.query(ActivityLog).filter(ActivityLog.site_id == site_hosting.site_id).all()
    assert [x.pesan for x in log] == ["Salinan VPS dibuat"]


def test_prod_buat_sebelum_prod_db_buat(sesi, site_hosting, prod, pb, lama):
    _jalankan(sesi, site_hosting)
    nama = pb.nama_panggilan()
    assert nama.index("prod_buat") < nama.index("prod_db_buat")
    assert _h(sesi, site_hosting).status == StatusHosting.pratinjau


@pytest.mark.parametrize("status", [StatusHosting.aktif, StatusHosting.pratinjau])
def test_tarik_ditolak_sesudah_dilayani_vps(sesi, site_hosting, prod, pb, lama, status):
    site_hosting.status = status
    site_hosting.dilayani_vps_pada = datetime.now(timezone.utc)
    sesi.commit()
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _jalankan(sesi, site_hosting)
    assert e.value.pesan == pindah.PESAN_SUDAH_DILAYANI
    assert lama == [] and pb.panggilan == []
    h = _h(sesi, site_hosting)
    assert h.status == status and h.galat == pindah.PESAN_SUDAH_DILAYANI


def test_pindah_tarik_terputus_lalu_dilanjutkan(sesi, site_hosting, hosting_aktif, prod, pb, lama):
    prod.jadwal_gagal = {"/staging/file": {1, 2, 3}}
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    with pytest.raises(SiteError) as e:
        pindah.tangani_pindah_tarik(sesi, job, None)
    assert e.value.error_class == TRANSIENT
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.menyalin
    assert h.galat == f"Terputus, dilanjutkan otomatis: {hu.PESAN_KELAS[TRANSIENT]}"
    manifest_sebelum = prod.hitung["/staging/manifest"]
    _jalankan(sesi, site_hosting, job)
    assert prod.hitung["/staging/manifest"] == manifest_sebelum
    assert (_files(hosting_aktif, site_hosting) / "index.php").exists()
    assert _h(sesi, site_hosting).status == StatusHosting.pratinjau


def test_batal_di_antara_potongan_menandai_salinan_belum_utuh(sesi, site_hosting, hosting_aktif, prod, pb, lama):
    def minta_batal(p, n, badan):
        h = sesi.get(HostingVps, site_hosting.id)
        h.batal_diminta_pada = datetime.now(timezone.utc)
        sesi.commit()

    prod.sebelum["/staging/tanda-air"] = minta_batal
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_hosting)
    assert "Dibatalkan" in e.value.pesan
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", hu.PESAN_BATAL_TENGAH)
    assert not (hosting_aktif / str(site_hosting.site_id) / "tarik").exists()


def test_info_http_ditolak_tanpa_ubah(sesi, site_hosting, prod, pb, lama):
    site_hosting.status = StatusHosting.pratinjau
    site_hosting.ditarik_pada = datetime.now(timezone.utc)
    sesi.commit()
    prod.info = {**prod.info, "home": "http://toko.co.id"}
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _jalankan(sesi, site_hosting)
    assert not any(route == "/staging/file" for route, _ in prod.diminta)
    h = _h(sesi, site_hosting)
    assert (h.status, h.galat) == (StatusHosting.pratinjau, pindah.PESAN_HTTP)


def test_ram_kurang_ditolak_tanpa_ubah(sesi, site_hosting, prod, pb, lama):
    pb.status_prod = StatusProd(1 * GB, 200 * GB, 150 * GB, 200 * GB, 150 * GB, {})
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _jalankan(sesi, site_hosting)
    assert "minimal 2,0 GB" in e.value.pesan
    assert lama == []


def test_sertifikat_pratinjau_gagal_menjadi_peringatan(sesi, site_hosting, prod, pb, lama):
    pb.gagal["sertifikat"] = GalatPembantu("sertifikat", "Menerbitkan sertifikat staging gagal. Sertifikat "
                                                          "staging belum dapat diterbitkan.")
    _, hasil = _jalankan(sesi, site_hosting)
    assert any(p.startswith("Sertifikat pratinjau belum terbit") for p in hasil["peringatan"])
    assert "prod_domain" in pb.nama_panggilan()
    h = _h(sesi, site_hosting)
    assert h.status == StatusHosting.pratinjau and h.pratinjau_sertifikat_pada is None


def test_prod_domain_gagal_menandai_gagal_salinan(sesi, site_hosting, hosting_aktif, prod, pb, lama):
    pesan = "Memasang konfigurasi nginx domain gagal. Konfigurasi nginx domain ditolak; site lain tidak terpengaruh."
    pb.gagal["prod_domain"] = GalatPembantu("nginx", pesan)
    with pytest.raises(SiteError) as e:
        _jalankan(sesi, site_hosting)
    assert e.value.error_class == STAGING_GAGAL
    h = _h(sesi, site_hosting)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "salinan", pesan)
    assert not (hosting_aktif / str(site_hosting.site_id) / "tarik").exists()


def test_salin_ulang_menjaga_wp_config_dan_menulis_ulang_mu_plugin(sesi, site_hosting, hosting_aktif, prod, pb, lama):
    _jalankan(sesi, site_hosting)
    files = _files(hosting_aktif, site_hosting)
    mu = files / "wp-content/mu-plugins/wpmgr-pratinjau.php"
    mu.write_bytes(b"<?php // dirusak")
    prod.berkas["index.php"] = (b"<?php // indeks baru", MTIME + 1)
    _jalankan(sesi, site_hosting)
    assert (files / "index.php").read_bytes() == b"<?php // indeks baru"
    assert (files / "wp-config.php").exists()
    assert mu.read_text(encoding="utf-8") == isi_mu_plugin_pratinjau()
    pesan = [x.pesan for x in sesi.query(ActivityLog).filter(ActivityLog.site_id == site_hosting.site_id)
             .order_by(ActivityLog.id)]
    assert pesan == ["Salinan VPS dibuat", "Salinan VPS disegarkan"]
```

- [ ] **Step 6: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_connector_paket.py tests/unit/test_hosting_pindah.py tests/integration/test_hosting_pindah.py -q`. Expected: `ImportError: cannot import name 'isi_mu_plugin_pratinjau'` dan `ModuleNotFoundError: No module named 'wpmgr.hosting.pindah'`.

- [ ] **Step 7: Implementasikan.** Di `src/wpmgr/connector_paket.py`, sesudah `NAMA_TEMPLATE_STAGING`, tambahkan:

```python
NAMA_TEMPLATE_PRATINJAU = "templates/wpmgr-pratinjau.php.tpl"
```

dan di akhir berkas:

```python
def isi_mu_plugin_pratinjau(sumber: Path | None = None) -> str:
    """Isi wp-content/mu-plugins/wpmgr-pratinjau.php (spec Lapis 4 §7.6).

    Tanpa placeholder: semua nilai (host pratinjau, domain) dibaca dari
    konstanta yang ditulis skrip pembantu di wp-config.php, jadi tidak ada
    masukan yang disisipkan ke kode PHP.
    """
    return ((sumber or sumber_bawaan()) / NAMA_TEMPLATE_PRATINJAU).read_text(encoding="utf-8")
```

File: `src/wpmgr/hosting/pindah.py`
```python
"""Job pindah_tarik (spec Lapis 4 §10.2-10.3): salin site dari hosting lama ke VPS.

Mesin tarik Lapis 3 (`tarik.tarik_inti`) menyalin ke HOSTING_DIR/<site_id>
lewat `TujuanHosting`; klien hosting lama dipatok IP dan baca-saja. Site lama
tidak pernah diubah. Sesudah `dilayani_vps_pada` terisi, setiap tarik ditolak
sebelum klien lama dihubungi (RF2).
"""

import shutil
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select

from wpmgr.config import get_settings
from wpmgr.connector_paket import isi_mu_plugin_pratinjau
from wpmgr.fitur import STAGING, punya_fitur
from wpmgr.hosting import umum as hu
from wpmgr.models import HostingVps, StatusHosting
from wpmgr.staging import tarik
from wpmgr.staging import umum as stg
from wpmgr.staging.aman import PathTidakAman, tulis_atomik
from wpmgr.staging.indeks import Indeks
from wpmgr.staging.pembantu import GalatPembantu, tulis_htpasswd_pratinjau
from wpmgr.staging.rencana import RAM_MINIMUM, format_byte

MU_PLUGIN_PRATINJAU = "wp-content/mu-plugins/wpmgr-pratinjau.php"
# Berkas milik salinan VPS sendiri: tidak pernah ditimpa atau dihapus tarik.
DILINDUNGI_HOSTING = frozenset({"wp-config.php", MU_PLUGIN_PRATINJAU})
PESAN_IZIN = ("Connector hosting lama belum mengizinkan staging. Aktifkan 'Izinkan staging' di "
              "Pengaturan -> WP Manager (connector 3.0).")
PESAN_SUDAH_DILAYANI = "Site ini sudah dilayani VPS; salinan dari hosting lama tidak boleh menimpanya."
PESAN_SANDI_BELUM = "Kata sandi pratinjau belum dibuat; buat ulang kata sandi pratinjau."
PESAN_STATUS_TARIK = "Salin ke VPS hanya bisa dari status pratinjau, menunggu DNS, atau gagal menyalin."
PESAN_HTTP = "Site lama memakai http (bukan https) di home/siteurl; aktifkan HTTPS di hosting lama dulu."
PESAN_SUBFOLDER = "WordPress di subfolder atau di port lain (home/siteurl ber-path) belum didukung pindah hosting."
PESAN_HOST_LAIN = "Alamat home/siteurl site lama tidak cocok dengan domain site ini."
PESAN_WWW = ("Site lama memakai www di home/siteurl, tetapi www tidak terdaftar untuk domain ini; "
             "batalkan pindah lalu mulai lagi.")
PESAN_MU_PLUGIN = "Folder mu-plugins salinan VPS tidak aman (berupa symlink); salin ulang untuk memulihkannya."
STATUS_BOLEH_TARIK = ("menyalin", "pratinjau", "menunggu_dns")


def cek_ram_hosting(status) -> str | None:
    """RAM >= 2 GB sebelum menyalin (spec §10.3); ERPNext dan ±19 site lain berbagi VPS ini."""
    if status.mem_tersedia < RAM_MINIMUM:
        return (f"RAM tersedia di VPS {format_byte(status.mem_tersedia)}; "
                f"minimal {format_byte(RAM_MINIMUM)} untuk menjalankan situs.")
    return None


def periksa_info_hosting(h, info: dict) -> None:
    """Info site lama yang tidak bisa dilayani VPS ditolak sebelum salinan disentuh (spec §10.2)."""
    for kunci in ("home", "siteurl"):
        bagian = urlsplit(info[kunci])
        if bagian.scheme != "https":
            raise stg.GalatDitolakTanpaUbah(PESAN_HTTP)
        try:
            port = bagian.port
        except ValueError:
            port = -1
        if bagian.path not in ("", "/") or port is not None:
            raise stg.GalatDitolakTanpaUbah(PESAN_SUBFOLDER)
        host = (bagian.hostname or "").lower()
        if host not in (h.domain, f"www.{h.domain}"):
            raise stg.GalatDitolakTanpaUbah(PESAN_HOST_LAIN)
        if host == f"www.{h.domain}" and not h.dengan_www:
            raise stg.GalatDitolakTanpaUbah(PESAN_WWW)


def tulis_mu_plugin(akar: Path) -> None:
    """Mu-plugin pratinjau ke files/ salinan (lewat `aman`: tidak mengikuti symlink)."""
    try:
        tulis_atomik(akar / "files", MU_PLUGIN_PRATINJAU, isi_mu_plugin_pratinjau().encode("utf-8"))
    except PathTidakAman:
        raise stg.galat_gagal(PESAN_MU_PLUGIN) from None


def tujuan_hosting(sesi, job, site, h: HostingVps, pb) -> tarik.TujuanSalinan:
    akar = hu.dir_hosting(site.id)
    nama, domain, www, site_id, sandi_hash = h.nama, h.domain, h.dengan_www, site.id, h.sandi_hash

    def impor(info: dict, berkas: list[Path]) -> None:
        # Koreksi #1: prod-db-buat menemukan files/ lewat state root yang
        # dibuat prod-buat, jadi prod-buat (idempoten) dipanggil lebih dulu.
        pb.prod_buat(nama, info["versi_php"], site_id, domain, www)
        pb.prod_db_buat(nama, info["prefix"])
        pb.prod_db_impor(nama, berkas)

    def siapkan(sesi_, job_, info: dict) -> None:
        # Tanpa search-replace dan tanpa wp-cli (spec §10.2): host pratinjau
        # ditangani WP_HOME bersyarat di wp-config dan penggantian keluaran
        # di mu-plugin.
        with stg.detak_latar(sesi_, job_):
            tulis_mu_plugin(akar)
            tulis_htpasswd_pratinjau(get_settings().jalur_hosting, nama, sandi_hash)
            pb.prod_buat(nama, info["versi_php"], site_id, domain, www)
            pb.prod_router_muat()

    return tarik.TujuanSalinan(
        akar=akar, baris=h, status_sumber=pb.prod_status, cek_awal=cek_ram_hosting,
        periksa_info=lambda info: periksa_info_hosting(h, info),
        # Putusan R25 tidak berlaku (spec §4.1): salinan ini AKAN menjadi
        # produksi dan memakai secret connector produksi yang sama.
        sql_tambahan=lambda sesi_, info, db_dir: None,
        impor=impor, siapkan_runtime=siapkan, dilindungi=DILINDUNGI_HOSTING,
        subdir=("files", "log"), tahap_akhir="pratinjau",
    )


def salin(sesi, job, site, h: HostingVps, klien, pb) -> dict:
    """Gerbang lalu `tarik_inti` ke HOSTING_DIR/<site_id>; dipakai pindah_tarik dan langkah tarik aktivasi."""
    if h.dilayani_vps_pada is not None:
        # RF2: selalu, juga pada percobaan ulang, sebelum hosting lama dihubungi.
        raise stg.GalatDitolakTanpaUbah(PESAN_SUDAH_DILAYANI)
    if not punya_fitur(site, STAGING):
        raise tarik._tolak(job, PESAN_IZIN)
    if not h.sandi_hash:
        raise tarik._tolak(job, PESAN_SANDI_BELUM)
    return tarik.tarik_inti(sesi, job, site, klien, tujuan_hosting(sesi, job, site, h, pb), stg.kemajuan(job))


def rampungkan_salinan(sesi, job, site, h: HostingVps, k: dict, sertifikat_ok: bool | None) -> dict:
    """Padatkan indeks, buang area kerja, dan catat ukuran salinan di baris hosting."""
    akar = hu.dir_hosting(site.id)
    indeks = Indeks(akar / "indeks.jsonl")
    lokal = indeks.muat()
    indeks.padatkan(lokal)
    shutil.rmtree(akar / "tarik", ignore_errors=True)
    info = k["info"]
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    sekarang = hu.sekarang()
    baris.ditarik_pada = sekarang
    baris.versi_php = info["versi_php"]
    baris.ukuran_file = min(sum(e.ukuran for e in lokal.values()), 2**62)
    baris.ukuran_db = min(info["ukuran_db"], 2**62)
    if sertifikat_ok:
        baris.pratinjau_sertifikat_pada = sekarang
    sesi.commit()
    return {"byte_disalin": k.get("byte_selesai", 0), "ukuran_file": baris.ukuran_file,
            "ukuran_db": baris.ukuran_db, "versi_php": info["versi_php"],
            "peringatan": list(k.get("peringatan") or [])}


def pindah_tarik(sesi, job, site, h: HostingVps, klien, pb) -> dict:
    if h.dilayani_vps_pada is not None:
        raise stg.GalatDitolakTanpaUbah(PESAN_SUDAH_DILAYANI)
    k_awal = stg.kemajuan(job)
    awal = k_awal.get("status_hosting_awal")
    if not (awal in STATUS_BOLEH_TARIK or (awal == "gagal" and k_awal.get("gagal_asal_awal") == hu.ASAL_SALINAN)):
        raise stg.GalatDitolakTanpaUbah(PESAN_STATUS_TARIK)
    pertama = h.ditarik_pada is None
    k = salin(sesi, job, site, h, klien, pb)
    sertifikat_ok = None
    if k.get("tahap") == "pratinjau":
        stg.titik_potongan(sesi, job, h)
        sertifikat_ok = True
        with stg.detak_latar(sesi, job):
            try:
                # Host pratinjau lewat server port 80 wildcard staging (spec §8.4).
                pb.sertifikat(f"vps-{h.nama}")
            except GalatPembantu as exc:
                # Pratinjau HTTPS belum tersedia; dicoba lagi pada Salin ulang.
                sertifikat_ok = False
                tarik._tambah_peringatan(k, f"Sertifikat pratinjau belum terbit: {exc.pesan}")
            pb.prod_domain(h.nama)
        k = stg.simpan_kemajuan(sesi, job, tahap="selesai", peringatan=list(k.get("peringatan") or []))
    hasil = rampungkan_salinan(sesi, job, site, h, k, sertifikat_ok)
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    baris.status = StatusHosting.pratinjau
    stg.catat_aktivitas(sesi, site.id, job, "Salinan VPS dibuat" if pertama else "Salinan VPS disegarkan", {
        **hasil, "ukuran_file_teks": format_byte(hasil["ukuran_file"]), "peringatan": hasil["peringatan"][:10]})
    sesi.commit()
    return hasil


def bersihkan_bila_final(sesi, site_id) -> None:
    """Hapus area kerja tarik/ bila kegagalan job ini final (pola `tarik.bersihkan_bila_final`)."""
    try:
        sesi.rollback()
        status = sesi.scalar(select(HostingVps.status).where(HostingVps.site_id == site_id))
    except Exception:  # noqa: BLE001 -- galat asli yang dilempar ulang lebih penting
        return
    if status is not None and status not in hu.STATUS_KERJA_SEMUA:
        shutil.rmtree(hu.dir_hosting(site_id) / "tarik", ignore_errors=True)


def tangani_pindah_tarik(sesi, job, klien) -> dict:
    """Handler worker. Klien bawaan worker (ke site.url) diabaikan: hosting lama lewat IP yang dipatok."""
    def inti(sesi, job, site, h):
        return pindah_tarik(sesi, job, site, h, hu.klien_lama(site, h), stg.buat_pembantu())

    site_id = job.site_id
    try:
        return hu.jalankan_hosting(sesi, job, inti, "Salin ke VPS")
    except stg.KlaimHilang:
        raise
    except Exception:
        bersihkan_bila_final(sesi, site_id)
        raise
```

Di `src/wpmgr/jobs/handlers.py`, tambahkan impor `from wpmgr.hosting.pindah import tangani_pindah_tarik` dan entri `JobType.pindah_tarik: tangani_pindah_tarik,` di akhir kamus `HANDLER`.

- [ ] **Step 8: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/unit/test_connector_paket.py tests/unit/test_hosting_pindah.py tests/integration/test_hosting_pindah.py tests/integration/test_hosting_tarik_inti.py -q`. Expected: semua lulus.

- [ ] **Step 9: Seluruh test unit, integrasi, PHPUnit (8.3, 7.4), lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 10: Commit.**

```bash
git add connector/wp-manager-connector/templates/wpmgr-pratinjau.php.tpl connector/tests/PratinjauTest.php src/wpmgr/connector_paket.py src/wpmgr/hosting/pindah.py src/wpmgr/jobs/handlers.py tests/integration/staging_palsu.py tests/unit/test_connector_paket.py tests/unit/test_hosting_pindah.py tests/integration/test_hosting_pindah.py
git commit -m "feat(hosting): mu-plugin pratinjau dan job pindah_tarik

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Pemeriksaan DNS, IP lama, instruksi record, dan backoff sertifikat

**Files:**
- Create: `src/wpmgr/hosting/dns.py`
- Test: `tests/unit/test_hosting_dns.py`

**Interfaces:**
- Consumes (Task 4–5): `Settings.hosting_ipv4`, `hosting_ipv6`, `daftar_resolver`; `hosting.umum.alamat_lama_sah`; dependensi `dnspython`.
- Produces (`wpmgr.hosting.dns`):
  - Konstanta `TENGGAT_KUERI = 3.0`, `TENGGAT_TOTAL = 10.0`, `MAKS_NILAI = 8`, `JEDA_SERTIFIKAT_MAKS = timedelta(hours=6)`, `JEDA_MANUAL = timedelta(minutes=15)`, `PESAN_LOLOS`, `PESAN_MENYEBAR = "DNS sedang menyebar"`, `PESAN_BELUM`, `NILAI_CAA = '0 issue "letsencrypt.org"'`.
  - `@dataclass(frozen=True) Jawaban(nilai: tuple[str, ...] = (), cname: str | None = None)`.
  - `PenanyaDns.tanya(resolver, nama, jenis, batas) -> Jawaban | None` (None = resolver tidak menjawab/timeout; NXDOMAIN = `Jawaban()`); `buat_penanya() -> PenanyaDns`.
  - `@dataclass HasilDns(ok: bool, dicek: str, nama: list[dict])`, properti `pesan`, metode `ke_json() -> dict` (bentuk `hosting_vps.dns_hasil`, spec §8.3). Setiap item: `{"nama": "@"|"www", "jenis": "A"|"AAAA"|"CAA", "terlihat": [...], "harus": [...], "ok": bool, "kode": "cocok"|"kurang"|"lebih"|"hapus"|"beda_resolver"|"caa"|"cname"}`.
  - `periksa_dns(hosting, resolver=None, penanya=None, sekarang=None) -> HasilDns` (`hosting` cukup punya `domain`, `dengan_www`).
  - `ip_lama_dari_dns(domain, penanya=None, resolver=None) -> str | None`, `ada_www(domain, penanya=None, resolver=None) -> bool`.
  - `instruksi(hosting, ipv4, ipv6) -> list[dict]` (`{"jenis","nama","aksi": "ubah"|"hapus","nilai","ok"}`, plus `"cname": True` pada A yang lewat CNAME).
  - `coba_lagi_pada(hosting) -> datetime | None`, `backoff_mengizinkan(hosting, sekarang, manual: bool) -> bool` (spec §8.4).

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/unit/test_hosting_dns.py`
```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting.dns import (
    PESAN_LOLOS,
    PESAN_MENYEBAR,
    Jawaban,
    ada_www,
    backoff_mengizinkan,
    coba_lagi_pada,
    instruksi,
    ip_lama_dari_dns,
    periksa_dns,
)

VPS = "169.58.91.181"
VPS6 = "2a02:c207:2347:2607::1"
LAMA = "93.184.216.34"
LAMA6 = "2a02:4780:6:1512:0:1e2d:4bc3:3"
R1, R2 = "1.1.1.1", "8.8.8.8"


@pytest.fixture
def setelan(monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_HOSTING_IPV4", VPS)
    monkeypatch.delenv("WPMGR_HOSTING_IPV6", raising=False)
    monkeypatch.setenv("WPMGR_HOSTING_RESOLVER", f"{R1},{R2}")
    get_settings.cache_clear()


@pytest.fixture
def ipv6_vps(setelan, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.setenv("WPMGR_HOSTING_IPV6", VPS6)
    get_settings.cache_clear()


class PenanyaPalsu:
    """Jawaban per (resolver, nama, jenis); resolver None = semua resolver."""

    def __init__(self, jawaban: dict) -> None:
        self.jawaban = jawaban
        self.diminta: list[tuple] = []

    def tanya(self, resolver, nama, jenis, batas):
        self.diminta.append((resolver, nama, jenis, batas))
        if (resolver, nama, jenis) in self.jawaban:
            return self.jawaban[(resolver, nama, jenis)]
        return self.jawaban.get((None, nama, jenis), Jawaban())


def _h(www=True):
    return SimpleNamespace(domain="toko.co.id", dengan_www=www, dns_hasil=None,
                           sertifikat_gagal_kali=0, sertifikat_gagal_pada=None)


def _item(hasil, nama, jenis):
    return next(x for x in hasil.nama if x["nama"] == nama and x["jenis"] == jenis)


def _sudah_pindah(**lain):
    j = {(None, "toko.co.id", "A"): Jawaban((VPS,)), (None, "www.toko.co.id", "A"): Jawaban((VPS,))}
    j.update(lain)
    return PenanyaPalsu(j)


def test_dns_lolos_bila_a_cocok_aaaa_kosong_caa_kosong(setelan):
    p = _sudah_pindah()
    hasil = periksa_dns(_h(), penanya=p)
    assert hasil.ok is True and hasil.pesan == PESAN_LOLOS
    assert {(x["nama"], x["jenis"]) for x in hasil.nama} == {
        ("@", "A"), ("@", "AAAA"), ("www", "A"), ("www", "AAAA"), ("@", "CAA")}
    assert all(b <= dns_mod.TENGGAT_KUERI for *_, b in p.diminta)
    # Setiap resolver ditanya untuk setiap nama dan jenis.
    assert {(r, n, j) for r, n, j, _ in p.diminta if j in ("A", "AAAA")} == {
        (r, n, j) for r in (R1, R2) for n in ("toko.co.id", "www.toko.co.id") for j in ("A", "AAAA")}
    assert hasil.ke_json()["ok"] is True and hasil.ke_json()["dicek"]


def test_tanpa_www_hanya_apex_diperiksa(setelan):
    hasil = periksa_dns(_h(www=False), penanya=_sudah_pindah())
    assert {x["nama"] for x in hasil.nama} == {"@"}


def test_dns_a_ganda_belum_lolos(setelan):
    hasil = periksa_dns(_h(), penanya=PenanyaPalsu({
        (None, "toko.co.id", "A"): Jawaban((VPS, LAMA)), (None, "www.toko.co.id", "A"): Jawaban((VPS,))}))
    a = _item(hasil, "@", "A")
    assert (a["ok"], a["kode"], a["terlihat"], a["harus"]) == (False, "lebih", sorted([VPS, LAMA]), [VPS])
    assert hasil.ok is False


def test_dns_aaaa_lama_menahan_aktivasi(setelan):
    p = _sudah_pindah()
    p.jawaban[(None, "toko.co.id", "AAAA")] = Jawaban((LAMA6,))
    hasil = periksa_dns(_h(), penanya=p)
    aaaa = _item(hasil, "@", "AAAA")
    assert (aaaa["ok"], aaaa["kode"], aaaa["terlihat"], aaaa["harus"]) == (False, "hapus", [LAMA6], [])
    assert hasil.ok is False


def test_dns_aaaa_ke_ipv6_vps_diterima(ipv6_vps):
    p = _sudah_pindah()
    p.jawaban[(None, "toko.co.id", "AAAA")] = Jawaban((VPS6,))
    p.jawaban[(None, "www.toko.co.id", "AAAA")] = Jawaban((VPS6,))
    assert periksa_dns(_h(), penanya=p).ok is True
    p.jawaban[(None, "www.toko.co.id", "AAAA")] = Jawaban((LAMA6,))
    hasil = periksa_dns(_h(), penanya=p)
    assert _item(hasil, "www", "AAAA")["kode"] == "kurang" and hasil.ok is False


def test_dns_www_lewat_cname_ke_apex_lolos(setelan):
    p = _sudah_pindah()
    p.jawaban[(None, "www.toko.co.id", "A")] = Jawaban((VPS,), cname="toko.co.id")
    assert periksa_dns(_h(), penanya=p).ok is True


def test_dns_cname_cdn_hostinger_ditandai(setelan):
    p = _sudah_pindah()
    p.jawaban[(None, "www.toko.co.id", "A")] = Jawaban(("185.10.10.10",), cname="toko.co.id.cdn.hstgr.net")
    hasil = periksa_dns(_h(), penanya=p)
    assert _item(hasil, "www", "A")["kode"] == "cname" and hasil.ok is False


def test_dns_resolver_berbeda_belum_lolos(setelan):
    p = _sudah_pindah()
    p.jawaban[(R2, "toko.co.id", "A")] = Jawaban((LAMA,))
    hasil = periksa_dns(_h(), penanya=p)
    a = _item(hasil, "@", "A")
    assert (a["ok"], a["kode"], a["terlihat"]) == (False, "beda_resolver", sorted([VPS, LAMA]))
    assert hasil.pesan == PESAN_MENYEBAR


def test_dns_resolver_tidak_menjawab_belum_lolos(setelan):
    p = _sudah_pindah()
    p.jawaban[(R1, "www.toko.co.id", "AAAA")] = None
    hasil = periksa_dns(_h(), penanya=p)
    assert _item(hasil, "www", "AAAA")["kode"] == "beda_resolver" and hasil.ok is False


@pytest.mark.parametrize("caa,ok", [
    ((), True), (("issue letsencrypt.org",), True), (("issuewild letsencrypt.org",), True),
    (("issue sectigo.com",), False), (("iodef mailto:a@b.id",), False),
])
def test_dns_caa(setelan, caa, ok):
    p = _sudah_pindah()
    p.jawaban[(R1, "toko.co.id", "CAA")] = Jawaban(caa)
    hasil = periksa_dns(_h(), penanya=p)
    c = _item(hasil, "@", "CAA")
    assert c["ok"] is ok and hasil.ok is ok
    assert c["kode"] == ("cocok" if ok else "caa")


def test_dns_caa_dari_induk_zona(setelan):
    p = _sudah_pindah()
    p.jawaban[(R1, "co.id", "CAA")] = Jawaban(("issue sectigo.com",))
    assert _item(periksa_dns(_h(), penanya=p), "@", "CAA")["kode"] == "caa"


def test_dns_tenggat_total_tidak_terlewati(setelan, monkeypatch):
    jam = [0.0]
    monkeypatch.setattr(dns_mod, "_jam", lambda: jam[0])

    class Lambat:
        n = 0

        def tanya(self, resolver, nama, jenis, batas):
            assert batas <= dns_mod.TENGGAT_KUERI
            self.n += 1
            jam[0] += 3.0
            return Jawaban((VPS,)) if jenis == "A" else Jawaban()

    p = Lambat()
    hasil = periksa_dns(_h(), penanya=p)
    assert p.n == 4
    assert hasil.ok is False


def test_penanya_yang_melempar_dianggap_tidak_menjawab(setelan):
    class Rusak:
        def tanya(self, *argumen):
            raise RuntimeError("galat tak terduga")

    hasil = periksa_dns(_h(), penanya=Rusak())
    assert hasil.ok is False and _item(hasil, "@", "A")["kode"] == "beda_resolver"


# ---- IP lama dan www ---------------------------------------------------------------


def test_ip_lama_dari_dns(setelan):
    assert ip_lama_dari_dns("toko.co.id", penanya=PenanyaPalsu({(None, "toko.co.id", "A"): Jawaban((LAMA,))})) == LAMA
    # Domain sudah menunjuk VPS: ip_lama tidak bisa ditentukan (spec §16).
    assert ip_lama_dari_dns("toko.co.id", penanya=PenanyaPalsu({
        (None, "toko.co.id", "A"): Jawaban((LAMA, VPS))})) is None
    assert ip_lama_dari_dns("toko.co.id", penanya=PenanyaPalsu({
        (None, "toko.co.id", "A"): Jawaban(("10.0.0.5",))})) is None
    assert ip_lama_dari_dns("toko.co.id", penanya=PenanyaPalsu({})) is None
    # Resolver pertama tidak menjawab: resolver berikutnya dipakai.
    assert ip_lama_dari_dns("toko.co.id", penanya=PenanyaPalsu({
        (R1, "toko.co.id", "A"): None, (R2, "toko.co.id", "A"): Jawaban((LAMA,))})) == LAMA


def test_ada_www(setelan):
    assert ada_www("toko.co.id", penanya=PenanyaPalsu({(None, "www.toko.co.id", "A"): Jawaban((LAMA,))})) is True
    assert ada_www("toko.co.id", penanya=PenanyaPalsu({})) is False


# ---- instruksi record ----------------------------------------------------------------


def test_instruksi_hapus_aaaa_lama_dan_ubah_a(setelan):
    p = _sudah_pindah()
    p.jawaban[(None, "toko.co.id", "A")] = Jawaban((LAMA,))
    p.jawaban[(None, "toko.co.id", "AAAA")] = Jawaban((LAMA6,))
    h = _h()
    h.dns_hasil = periksa_dns(h, penanya=p).ke_json()
    daftar = instruksi(h, VPS, None)
    assert {"jenis": "A", "nama": "@", "aksi": "ubah", "nilai": VPS, "ok": False, "cname": False} in daftar
    assert {"jenis": "A", "nama": "www", "aksi": "ubah", "nilai": VPS, "ok": True, "cname": False} in daftar
    assert {"jenis": "AAAA", "nama": "@", "aksi": "hapus", "nilai": LAMA6, "ok": False} in daftar
    assert {"jenis": "AAAA", "nama": "www", "aksi": "hapus", "nilai": "", "ok": True} in daftar


def test_instruksi_ipv6_vps_dan_caa(ipv6_vps):
    p = _sudah_pindah()
    p.jawaban[(R1, "toko.co.id", "CAA")] = Jawaban(("issue sectigo.com",))
    h = _h(www=False)
    h.dns_hasil = periksa_dns(h, penanya=p).ke_json()
    daftar = instruksi(h, VPS, VPS6)
    assert {"jenis": "AAAA", "nama": "@", "aksi": "ubah", "nilai": VPS6, "ok": True} in daftar
    assert {"jenis": "CAA", "nama": "@", "aksi": "ubah", "nilai": dns_mod.NILAI_CAA, "ok": False} in daftar
    assert not any(x["nama"] == "www" for x in daftar)


def test_instruksi_tanpa_cek_dan_nilai_rusak_dibuang(setelan):
    h = _h()
    daftar = instruksi(h, VPS, None)
    assert [(x["jenis"], x["nama"], x["aksi"]) for x in daftar] == [
        ("A", "@", "ubah"), ("AAAA", "@", "hapus"), ("A", "www", "ubah"), ("AAAA", "www", "hapus")]
    h.dns_hasil = {"nama": [{"nama": "@", "jenis": "AAAA", "terlihat": ["<script>", LAMA6], "ok": False,
                             "kode": "hapus"}, "rusak"]}
    nilai = [x["nilai"] for x in instruksi(h, VPS, None) if x["jenis"] == "AAAA" and x["nama"] == "@"]
    assert nilai == [LAMA6]


# ---- backoff sertifikat -------------------------------------------------------------------

T0 = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("kali,jam", [(1, 1), (2, 2), (3, 4), (4, 6), (5, 6), (9, 6)])
def test_coba_lagi_pada(kali, jam):
    h = SimpleNamespace(sertifikat_gagal_kali=kali, sertifikat_gagal_pada=T0)
    assert coba_lagi_pada(h) == T0 + timedelta(hours=jam)


def test_backoff_mengizinkan():
    belum = SimpleNamespace(sertifikat_gagal_kali=0, sertifikat_gagal_pada=None)
    assert coba_lagi_pada(belum) is None and backoff_mengizinkan(belum, T0, manual=False)
    h = SimpleNamespace(sertifikat_gagal_kali=3, sertifikat_gagal_pada=T0)
    assert backoff_mengizinkan(h, T0 + timedelta(hours=3), manual=False) is False
    assert backoff_mengizinkan(h, T0 + timedelta(hours=4), manual=False) is True
    # Tombol manual mengabaikan backoff hanya sesudah >= 15 menit (batas Let's Encrypt).
    assert backoff_mengizinkan(h, T0 + timedelta(minutes=10), manual=True) is False
    assert backoff_mengizinkan(h, T0 + timedelta(minutes=15), manual=True) is True
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_hosting_dns.py -q`. Expected: `ModuleNotFoundError: No module named 'wpmgr.hosting.dns'`.

- [ ] **Step 3: Implementasikan.**

File: `src/wpmgr/hosting/dns.py`
```python
"""Pemeriksaan DNS sebelum aktivasi hosting VPS (spec Lapis 4 §8).

Resolver publik tertentu (WPMGR_HOSTING_RESOLVER) ditanya langsung lewat
dnspython: socket.getaddrinfo memakai resolver lokal, /etc/hosts, dan cache,
dan tidak bisa menanyakan resolver tertentu maupun CAA. Nilai dari DNS adalah
masukan luar: hanya alamat yang lolos `ipaddress` yang disimpan, paling banyak
MAKS_NILAI per jenis, dan UI menyusun teks dari kode tetap.
"""

import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import dns.exception
import dns.resolver

from wpmgr.config import get_settings
from wpmgr.hosting.umum import alamat_lama_sah

log = logging.getLogger("wpmgr.hosting.dns")

TENGGAT_KUERI = 3.0
TENGGAT_TOTAL = 10.0
MAKS_NILAI = 8
JEDA_SERTIFIKAT_MAKS = timedelta(hours=6)
JEDA_MANUAL = timedelta(minutes=15)
POLA_CAA = re.compile(r"[a-z0-9]{1,15} [\x21-\x7e][\x20-\x7e]{0,254}")
CAA_LETSENCRYPT = "letsencrypt.org"
NILAI_CAA = '0 issue "letsencrypt.org"'
PESAN_LOLOS = "DNS sudah menunjuk VPS."
PESAN_MENYEBAR = "DNS sedang menyebar"
PESAN_BELUM = "DNS belum menunjuk VPS; ubah record sesuai tabel."


def _jam() -> float:
    return time.monotonic()


@dataclass(frozen=True)
class Jawaban:
    nilai: tuple[str, ...] = ()
    # Nama kanonik bila jawaban datang lewat CNAME ke nama lain.
    cname: str | None = None


def _nilai_rdata(jenis: str, rd) -> str | None:
    try:
        if jenis == "A":
            return str(ipaddress.IPv4Address(rd.address))
        if jenis == "AAAA":
            return ipaddress.IPv6Address(rd.address).compressed
        if jenis == "CAA":
            teks = f"{rd.tag.decode('ascii').lower()} {rd.value.decode('ascii').lower()}"
            return teks if POLA_CAA.fullmatch(teks) else None
    except (ValueError, AttributeError, UnicodeDecodeError):
        return None
    return None


class PenanyaDns:
    """Satu kueri ke satu resolver publik tertentu, dengan batas waktu sendiri."""

    def tanya(self, resolver: str, nama: str, jenis: str, batas: float) -> Jawaban | None:
        r = dns.resolver.Resolver(configure=False)
        r.nameservers = [resolver]
        r.timeout = batas
        r.lifetime = batas
        try:
            jawab = r.resolve(nama, jenis, raise_on_no_answer=False, search=False)
        except dns.resolver.NXDOMAIN:
            return Jawaban()
        except dns.exception.DNSException:
            return None
        nilai: list[str] = []
        for rd in jawab.rrset or ():
            teks = _nilai_rdata(jenis, rd)
            if teks is not None and teks not in nilai:
                nilai.append(teks)
            if len(nilai) >= MAKS_NILAI:
                break
        kanonik = str(jawab.canonical_name).rstrip(".").lower()
        return Jawaban(tuple(nilai), kanonik if kanonik != nama.lower() else None)


def buat_penanya() -> PenanyaDns:
    return PenanyaDns()


@dataclass
class HasilDns:
    ok: bool
    dicek: str
    nama: list[dict] = field(default_factory=list)

    @property
    def pesan(self) -> str:
        if self.ok:
            return PESAN_LOLOS
        if any(x["kode"] == "beda_resolver" for x in self.nama):
            return PESAN_MENYEBAR
        return PESAN_BELUM

    def ke_json(self) -> dict:
        return {"ok": self.ok, "dicek": self.dicek, "nama": self.nama}


def _penanya_bertenggat(penanya):
    akhir = _jam() + TENGGAT_TOTAL

    def tanya(resolver: str, nama: str, jenis: str) -> Jawaban | None:
        sisa = akhir - _jam()
        if sisa <= 0:
            return None
        try:
            return penanya.tanya(resolver, nama, jenis, min(TENGGAT_KUERI, sisa))
        except Exception:  # noqa: BLE001 -- resolver yang rusak = tidak menjawab
            log.warning("Kueri DNS %s %s ke %s gagal", jenis, nama, resolver, exc_info=True)
            return None

    return tanya


def _nilai_item(label: str, jenis: str, fqdn: str, domain: str, jawaban: list, ipv4: str,
                ipv6: str | None) -> dict:
    harus = [ipv4] if jenis == "A" else ([ipv6] if ipv6 else [])
    terlihat = sorted({v for j in jawaban if j is not None for v in j.nilai})[:MAKS_NILAI]
    dasar = {"nama": label, "jenis": jenis, "terlihat": terlihat, "harus": harus}
    # Resolver yang tidak menjawab atau berbeda pendapat = DNS belum stabil.
    if any(j is None for j in jawaban) or len({frozenset(j.nilai) for j in jawaban}) > 1:
        return {**dasar, "ok": False, "kode": "beda_resolver"}
    nilai = set(jawaban[0].nilai)
    if jenis == "A":
        if nilai == {ipv4}:
            kode = "cocok"
        elif ipv4 in nilai:
            kode = "lebih"
        elif any(j.cname and j.cname not in (fqdn, domain) for j in jawaban):
            # A12: www lewat CNAME ke CDN hosting lama; CNAME harus diganti A.
            kode = "cname"
        else:
            kode = "kurang"
    elif not nilai or (ipv6 and nilai == {ipv6}):
        kode = "cocok"
    elif ipv6:
        kode = "lebih" if ipv6 in nilai else "kurang"
    else:
        # §8.2: AAAA lama wajib dihapus bila VPS tanpa IPv6 di setelan.
        kode = "hapus"
    return {**dasar, "ok": kode == "cocok", "kode": kode}


def _periksa_caa(resolver: str, domain: str, tanya) -> dict:
    """CAA domain, lalu induknya sampai zona; berhenti di jawaban pertama (spec §8.1)."""
    dasar = {"nama": "@", "jenis": "CAA", "terlihat": [], "harus": []}
    label = domain.split(".")
    for i in range(len(label) - 1):
        j = tanya(resolver, ".".join(label[i:]), "CAA")
        if j is None:
            return {**dasar, "ok": False, "kode": "beda_resolver"}
        if j.nilai:
            ok = any(v.split(" ", 1)[0] in ("issue", "issuewild") and CAA_LETSENCRYPT in v for v in j.nilai)
            return {**dasar, "ok": ok, "kode": "cocok" if ok else "caa"}
    return {**dasar, "ok": True, "kode": "cocok"}


def periksa_dns(hosting, resolver=None, penanya=None, sekarang: datetime | None = None) -> HasilDns:
    """Lolos hanya bila SETIAP resolver dan setiap nama: A = {IPv4 VPS}, AAAA kosong atau
    {IPv6 VPS}, dan CAA kosong atau mengizinkan Let's Encrypt (spec §8.1)."""
    s = get_settings()
    tanya = _penanya_bertenggat(penanya or buat_penanya())
    daftar = list(resolver or s.daftar_resolver)
    sekarang = sekarang or datetime.now(timezone.utc)
    nama = [("@", hosting.domain)] + ([("www", f"www.{hosting.domain}")] if hosting.dengan_www else [])
    item = []
    for label, fqdn in nama:
        for jenis in ("A", "AAAA"):
            jawaban = [tanya(r, fqdn, jenis) for r in daftar]
            item.append(_nilai_item(label, jenis, fqdn, hosting.domain, jawaban, s.hosting_ipv4, s.hosting_ipv6))
    item.append(_periksa_caa(daftar[0], hosting.domain, tanya))
    return HasilDns(ok=all(x["ok"] for x in item), dicek=sekarang.isoformat(), nama=item)


def ip_lama_dari_dns(domain: str, penanya=None, resolver=None) -> str | None:
    """IPv4 hosting lama dari A domain (spec §6 langkah 2).

    None bila tidak ada resolver yang menjawab, tidak ada IPv4 publik, atau
    domain sudah (sebagian) menunjuk VPS -- ip_lama lalu tidak bisa ditentukan.
    """
    s = get_settings()
    tanya = _penanya_bertenggat(penanya or buat_penanya())
    for r in resolver or s.daftar_resolver:
        j = tanya(r, domain, "A")
        if j is None or not j.nilai:
            continue
        if s.hosting_ipv4 in j.nilai:
            return None
        return next((ip for ip in sorted(j.nilai) if alamat_lama_sah(ip)), None)
    return None


def ada_www(domain: str, penanya=None, resolver=None) -> bool:
    """`www.<domain>` punya A (langsung atau lewat CNAME) di salah satu resolver."""
    tanya = _penanya_bertenggat(penanya or buat_penanya())
    for r in resolver or get_settings().daftar_resolver:
        j = tanya(r, f"www.{domain}", "A")
        if j is not None and j.nilai:
            return True
    return False


def _ipv6_sah(nilai) -> bool:
    try:
        return isinstance(nilai, str) and ipaddress.IPv6Address(nilai).compressed == nilai
    except ValueError:
        return False


def instruksi(hosting, ipv4: str, ipv6: str | None) -> list[dict]:
    """Record yang harus ada di DNS domain (spec §6 langkah 5, §8.2), ditandai dari cek terakhir.

    `dns_hasil` ditulis dashboard sendiri, tetapi tetap dibaca defensif: nilai
    yang tampil hanya alamat IPv6 yang lolos `ipaddress`.
    """
    hasil = {}
    for x in ((hosting.dns_hasil or {}).get("nama") or []):
        if isinstance(x, dict):
            hasil[(x.get("nama"), x.get("jenis"))] = x
    daftar = []
    for label in ["@"] + (["www"] if hosting.dengan_www else []):
        a = hasil.get((label, "A")) or {}
        daftar.append({"jenis": "A", "nama": label, "aksi": "ubah", "nilai": ipv4, "ok": a.get("ok") is True,
                       "cname": a.get("kode") == "cname"})
        aaaa = hasil.get((label, "AAAA")) or {}
        if ipv6:
            daftar.append({"jenis": "AAAA", "nama": label, "aksi": "ubah", "nilai": ipv6, "ok": aaaa.get("ok") is True})
            continue
        terlihat = [v for v in (aaaa.get("terlihat") or []) if _ipv6_sah(v)]
        if terlihat and aaaa.get("ok") is not True:
            daftar.extend({"jenis": "AAAA", "nama": label, "aksi": "hapus", "nilai": v, "ok": False} for v in terlihat)
        else:
            daftar.append({"jenis": "AAAA", "nama": label, "aksi": "hapus", "nilai": "", "ok": aaaa.get("ok") is True})
    caa = hasil.get(("@", "CAA")) or {}
    if caa.get("kode") == "caa":
        daftar.append({"jenis": "CAA", "nama": "@", "aksi": "ubah", "nilai": NILAI_CAA, "ok": False})
    return daftar


# ---- backoff sertifikat (spec §8.4) ------------------------------------------------


def coba_lagi_pada(hosting) -> datetime | None:
    """Batas Let's Encrypt (5 validasi gagal per hostname per jam): sesudah gagal
    ke-k, pengantrean otomatis berikutnya tidak sebelum gagal_pada + min(2^(k-1) jam, 6 jam)."""
    kali = hosting.sertifikat_gagal_kali or 0
    if kali <= 0 or hosting.sertifikat_gagal_pada is None:
        return None
    return hosting.sertifikat_gagal_pada + min(timedelta(hours=2 ** min(kali - 1, 10)), JEDA_SERTIFIKAT_MAKS)


def backoff_mengizinkan(hosting, sekarang: datetime, manual: bool) -> bool:
    batas = coba_lagi_pada(hosting)
    if batas is None or sekarang >= batas:
        return True
    return manual and sekarang >= hosting.sertifikat_gagal_pada + JEDA_MANUAL
```

- [ ] **Step 4: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/unit/test_hosting_dns.py -q`. Expected: semua lulus.

- [ ] **Step 5: Seluruh test unit, lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 6: Commit.**

```bash
git add src/wpmgr/hosting/dns.py tests/unit/test_hosting_dns.py
git commit -m "feat(hosting): pemeriksaan DNS, IP lama, instruksi record, dan backoff sertifikat

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Job `pindah_aktifkan`

**Files:**
- Modify: `src/wpmgr/hosting/pindah.py`, `src/wpmgr/jobs/handlers.py`
- Test: `tests/integration/test_hosting_aktifkan.py`

**Interfaces:**
- Consumes (Task 5–9): `hosting.umum` (`jalankan_hosting`, `klien_lama`, `dir_hosting`, `sekarang`, `GalatHosting`, `STATUS_KERJA_SEMUA`), `queue.LANGKAH_AKTIFKAN_SESUDAH_TUKAR`, `hosting.dns.periksa_dns`, `hosting.dns.buat_penanya`, `HasilDns.pesan`, `hosting.pindah` (`salin`, `rampungkan_salinan`, `tulis_mu_plugin`, `MU_PLUGIN_PRATINJAU`, `bersihkan_bila_final`, `PESAN_SUDAH_DILAYANI`), `Pembantu.prod_domain`, `prod_sertifikat`, `prod_aktifkan`, `site_client.minta_bertenggat(..., ekstensi=)`, `aman.hapus_berkas`, `aman.baca_terbatas`, fixture `site_hosting`, `hosting_aktif`, `staging_palsu.PembantuHostingPalsu`, `ProduksiPalsu`.
- Produces (`wpmgr.hosting.pindah`):
  - `LANGKAH_AKTIFKAN = ("dns", "sertifikat", "tarik", "tukar", "verifikasi", "beres")`, `ALAMAT_VERIFIKASI = "127.0.0.1"`, `BATAS_WP_CONFIG`, pesan `PESAN_STATUS_AKTIFKAN`, `PESAN_NGINX`, `PESAN_SERTIFIKAT`, `PESAN_TUKAR_DITOLAK`, `PESAN_VERIFIKASI`.
  - `ambil_halaman_verifikasi(host) -> tuple[int, dict] | None` (GET `https://127.0.0.1/` dengan `Host` dan SNI `host`, sertifikat diverifikasi; header huruf kecil), `verifikasi(h, akar: Path) -> list[str]` (masalah; kosong = lolos), `boleh_batal_aktifkan(job) -> bool`, `aktifkan(sesi, job, site, h, pb) -> dict`, `tangani_pindah_aktifkan(sesi, job, klien) -> dict`.
  - Kemajuan job: `langkah_aktifkan` (ditulis SEBELUM tiap langkah), `tukar_pada` (ISO, ditulis bersama `dilayani_vps_pada`), `tukar_dikirim` (True sebelum `prod_aktifkan` pertama dikirim).
  - Payload job: `tanpa_tarik_ulang: bool`.
  - `handlers.HANDLER[JobType.pindah_aktifkan] = tangani_pindah_aktifkan`.

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/integration/test_hosting_aktifkan.py`
```python
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm.attributes import flag_modified
from staging_palsu import PembantuHostingPalsu, ProduksiPalsu

from wpmgr.errors import TRANSIENT, SiteError
from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting import pindah
from wpmgr.hosting import umum as hu
from wpmgr.hosting.dns import Jawaban
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import akan_diulang, buat_job
from wpmgr.models import ActivityLog, HostingVps, Job, JobStatus, JobType, StatusHosting
from wpmgr.staging import rencana, tarik, umum
from wpmgr.staging.pembantu import GalatPembantu

pytestmark = pytest.mark.integration

MTIME = 1_700_000_000
IP_LAMA = "93.184.216.34"
VPS = "169.58.91.181"
MU = "wp-content/mu-plugins/wpmgr-pratinjau.php"


@pytest.fixture(autouse=True)
def cepat(monkeypatch):
    monkeypatch.setattr(umum, "JEDA_ULANG", (0, 0))
    monkeypatch.setattr(rencana, "UKURAN_PAKET", 1000)
    monkeypatch.setattr(tarik, "UKURAN_PAKET", 1000)


@pytest.fixture
def prod():
    p = ProduksiPalsu()
    p.info = {**p.info, "home": "https://toko.co.id", "siteurl": "https://toko.co.id"}
    p.berkas = {"index.php": (b"<?php // indeks", MTIME)}
    p.tabel = {"wp_options": [b"DROP TABLE IF EXISTS `wp_options`;\nCREATE TABLE `wp_options` (`a` text);\n"]}
    return p


@pytest.fixture
def pb(hosting_aktif, monkeypatch):
    palsu = PembantuHostingPalsu(hosting_aktif)
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


@pytest.fixture
def lama(prod, monkeypatch):
    host: list[tuple[str, str]] = []

    def tangani(r):
        host.append((r.url.host, r.headers.get("host")))
        return prod.tangani(r)

    monkeypatch.setattr(hu, "buat_http_lama", lambda: httpx.Client(transport=httpx.MockTransport(tangani)))
    return host


class PenanyaDnsPalsu:
    """DNS publik tiruan: A ke VPS; AAAA lama di apex bila `aaaa_lama`."""

    def __init__(self) -> None:
        self.aaaa_lama = False
        self.n = 0

    def tanya(self, resolver, nama, jenis, batas):
        self.n += 1
        if jenis == "A":
            return Jawaban((VPS,))
        if jenis == "AAAA" and self.aaaa_lama and nama == "toko.co.id":
            return Jawaban(("2a02:4780:6:1512:0:1e2d:4bc3:3",))
        return Jawaban()


@pytest.fixture
def dns_palsu(monkeypatch):
    p = PenanyaDnsPalsu()
    monkeypatch.setattr(dns_mod, "buat_penanya", lambda: p)
    return p


class HalamanPalsu:
    def __init__(self) -> None:
        self.jawaban = (200, {})
        self.host: list[str] = []

    def __call__(self, host):
        self.host.append(host)
        return self.jawaban


@pytest.fixture
def halaman(monkeypatch):
    h = HalamanPalsu()
    monkeypatch.setattr(pindah, "ambil_halaman_verifikasi", h)
    return h


@pytest.fixture
def siap(sesi, site_hosting, prod, pb, lama, dns_palsu, halaman):
    """Salinan VPS sudah dibuat (pindah_tarik sungguhan) dan pengguna sudah lanjut ke DNS."""
    job = buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    pindah.tangani_pindah_tarik(sesi, job, None)
    job.status = JobStatus.success
    h = sesi.get(HostingVps, site_hosting.id, populate_existing=True)
    h.status = StatusHosting.menunggu_dns
    sesi.commit()
    pb.panggilan.clear()
    lama.clear()
    return h


def _aktifkan(sesi, h, payload=None, job=None):
    job = job or buat_job(sesi, h.site_id, JobType.pindah_aktifkan, payload or {})
    return job, pindah.tangani_pindah_aktifkan(sesi, job, None)


def _h(sesi, h):
    return sesi.get(HostingVps, h.id, populate_existing=True)


def _files(hosting_aktif, h):
    return hosting_aktif / str(h.site_id) / "files"


def test_handler_terdaftar():
    assert handlers.HANDLER[JobType.pindah_aktifkan] is pindah.tangani_pindah_aktifkan


def test_aktifkan_sukses_penuh(sesi, siap, hosting_aktif, pb, lama, halaman):
    job, hasil = _aktifkan(sesi, siap)
    nama = pb.nama_panggilan()
    assert nama.index("prod_domain") < nama.index("prod_sertifikat") < nama.index("prod_db_impor") \
        < nama.index("prod_aktifkan")
    assert not (_files(hosting_aktif, siap) / MU).exists()
    assert halaman.host == ["toko.co.id", "www.toko.co.id"]
    h = _h(sesi, siap)
    assert h.status == StatusHosting.aktif and h.galat is None and h.gagal_asal is None
    assert h.aktif_pada is not None and h.dilayani_vps_pada is not None and h.sertifikat_pada is not None
    assert h.dns_hasil["ok"] is True and h.dns_dicek_pada is not None
    k = umum.kemajuan(job)
    assert k["langkah_aktifkan"] == "beres" and k["tukar_pada"]
    assert hasil["domain"] == "toko.co.id"
    log = [x.pesan for x in sesi.query(ActivityLog).filter(ActivityLog.job_id == job.id)]
    assert log == ["Site dihosting di VPS"]


def test_beres_tidak_menyisipkan_job_lain(sesi, siap, pb, lama, halaman):
    job, _ = _aktifkan(sesi, siap)
    assert {j.tipe for j in sesi.query(Job).all()} == {JobType.pindah_tarik, JobType.pindah_aktifkan}
    assert sesi.query(Job).filter(Job.tipe == JobType.backup_hosting).count() == 0


def test_aktifkan_dns_belum_lolos_status_tetap(sesi, siap, pb, dns_palsu):
    dns_palsu.aaaa_lama = True
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _aktifkan(sesi, siap)
    h = _h(sesi, siap)
    assert h.status == StatusHosting.menunggu_dns
    assert h.galat == dns_mod.PESAN_BELUM
    assert h.dns_hasil["ok"] is False and h.dilayani_vps_pada is None
    assert pb.panggilan == []


def test_aktifkan_sertifikat_gagal_backoff_tanpa_tarik(sesi, siap, pb, lama):
    pb.gagal["prod_sertifikat"] = GalatPembantu("sertifikat", "Menerbitkan sertifikat domain gagal. Sertifikat "
                                                               "staging belum dapat diterbitkan.")
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _aktifkan(sesi, siap)
    h = _h(sesi, siap)
    assert (h.status, h.galat) == (StatusHosting.menunggu_dns, pindah.PESAN_SERTIFIKAT)
    assert h.sertifikat_gagal_kali == 1 and h.sertifikat_gagal_pada is not None
    assert lama == [] and "prod_aktifkan" not in pb.nama_panggilan()


def test_aktifkan_tarik_dipatok_ke_ip_lama(sesi, siap, hosting_aktif, prod, lama, halaman):
    prod.berkas["index.php"] = (b"<?php // data terakhir", MTIME + 5)
    _aktifkan(sesi, siap)
    assert lama and all(x == (IP_LAMA, "toko.co.id") for x in lama)
    assert (_files(hosting_aktif, siap) / "index.php").read_bytes() == b"<?php // data terakhir"


def test_prod_aktifkan_keluar_3_menghapus_penanda(sesi, siap, pb):
    pb.gagal["prod_aktifkan"] = GalatPembantu("ditolak", "Mengaktifkan situs gagal. Skrip pembantu menolak "
                                                         "permintaan ini.")
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _aktifkan(sesi, siap, job=job)
    h = _h(sesi, siap)
    assert h.dilayani_vps_pada is None
    assert (h.status, h.galat) == (StatusHosting.menunggu_dns, pindah.PESAN_TUKAR_DITOLAK)
    k = umum.kemajuan(sesi.get(Job, job.id, populate_existing=True))
    assert k["langkah_aktifkan"] == "dns" and k["tukar_pada"] is None and k["tukar_dikirim"] is False


def test_tukar_ditolak_memasang_ulang_mu_plugin(sesi, siap, hosting_aktif, pb):
    pb.gagal["prod_aktifkan"] = GalatPembantu("ditolak", "Mengaktifkan situs gagal. Skrip pembantu menolak "
                                                         "permintaan ini.")
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _aktifkan(sesi, siap)
    # Situs masih pratinjau (konstanta WPMGR_PRATINJAU ada): pemblokir email wajib ada lagi.
    assert (_files(hosting_aktif, siap) / MU).exists()


def test_aktifkan_terputus_sesudah_tukar_diulang_sampai_24_jam(sesi, siap, pb):
    pb.gagal["prod_aktifkan"] = GalatPembantu("waktu", "Skrip pembantu tidak selesai dalam 300 detik.")
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError) as e:
        _aktifkan(sesi, siap, job=job)
    assert e.value.error_class == TRANSIENT
    job = sesi.get(Job, job.id, populate_existing=True)
    assert akan_diulang(job, TRANSIENT) is True
    h = _h(sesi, siap)
    assert h.status == StatusHosting.mengaktifkan and h.dilayani_vps_pada is not None
    tukar_pada = umum.kemajuan(job)["tukar_pada"]
    # Percobaan berikutnya: hanya prod-aktifkan yang dikirim ulang; DNS, sertifikat, tarik dilewati.
    pb.panggilan.clear()
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap, job=job)
    assert pb.nama_panggilan() == ["prod_aktifkan"]
    job = sesi.get(Job, job.id, populate_existing=True)
    assert umum.kemajuan(job)["tukar_pada"] == tukar_pada
    # Lewat 24 jam sejak tukar: final, gagal asal produksi dengan pesan tetap.
    payload = dict(job.payload)
    payload["kemajuan"] = {**payload["kemajuan"],
                           "tukar_pada": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()}
    job.payload = payload
    flag_modified(job, "payload")
    sesi.commit()
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap, job=job)
    h = _h(sesi, siap)
    assert (h.status, h.gagal_asal, h.galat) == (StatusHosting.gagal, "produksi", hu.PESAN_PRODUKSI_GAGAL)


def test_aktifkan_terputus_saat_tukar_tidak_bisa_tarik_lagi(sesi, siap, pb, lama):
    pb.gagal["prod_aktifkan"] = GalatPembantu("docker", "Mengaktifkan situs gagal. Perintah Docker di server "
                                                        "staging gagal.")
    job = buat_job(sesi, siap.site_id, JobType.pindah_aktifkan)
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap, job=job)
    job.status = JobStatus.failed
    sesi.commit()
    lama.clear()
    tarik_job = buat_job(sesi, siap.site_id, JobType.pindah_tarik)
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        pindah.tangani_pindah_tarik(sesi, tarik_job, None)
    assert e.value.pesan == pindah.PESAN_SUDAH_DILAYANI
    assert lama == []


def test_tanpa_tarik_ulang_melewati_tarik(sesi, siap, prod, pb, lama, halaman):
    _aktifkan(sesi, siap, {"tanpa_tarik_ulang": True})
    assert lama == []
    assert "prod_db_impor" not in pb.nama_panggilan() and "prod_aktifkan" in pb.nama_panggilan()
    assert _h(sesi, siap).status == StatusHosting.aktif


def test_verifikasi_gagal_bila_wpmgr_pratinjau_tersisa(sesi, siap, pb, halaman):
    # prod-aktifkan "berhasil" tetapi wp-config.php masih mode pratinjau.
    pb.prod_aktifkan = lambda nama: pb._catat("prod_aktifkan", nama)
    with pytest.raises(SiteError) as e:
        _aktifkan(sesi, siap)
    assert e.value.error_class == TRANSIENT and e.value.pesan == pindah.PESAN_VERIFIKASI
    h = _h(sesi, siap)
    assert h.status == StatusHosting.mengaktifkan
    assert h.galat == f"Terputus, dilanjutkan otomatis: {pindah.PESAN_VERIFIKASI}"


@pytest.mark.parametrize("jawaban", [(401, {}), (500, {}), (200, {"x-robots-tag": "noindex, nofollow"}), None])
def test_verifikasi_gagal_untuk_401_500_noindex_atau_tanpa_jawaban(sesi, siap, halaman, jawaban):
    halaman.jawaban = jawaban
    with pytest.raises(SiteError):
        _aktifkan(sesi, siap)
    assert _h(sesi, siap).status == StatusHosting.mengaktifkan


def test_periksa_ulang_langsung_verifikasi(sesi, siap, pb, lama, dns_palsu, halaman):
    siap.status = StatusHosting.gagal
    siap.gagal_asal = "produksi"
    siap.dilayani_vps_pada = datetime.now(timezone.utc) - timedelta(days=2)
    sesi.commit()
    (_files(hu.get_settings().jalur_hosting, siap) / "wp-config.php").write_bytes(b"<?php // aktif")
    job, _ = _aktifkan(sesi, siap)
    assert pb.panggilan == [] and lama == [] and dns_palsu.n == 0
    assert halaman.host == ["toko.co.id", "www.toko.co.id"]
    k = umum.kemajuan(job)
    tukar = datetime.fromisoformat(k["tukar_pada"])
    assert datetime.now(timezone.utc) - tukar < timedelta(minutes=5)
    h = _h(sesi, siap)
    assert (h.status, h.gagal_asal) == (StatusHosting.aktif, None)


def test_batal_hanya_berlaku_sebelum_tukar():
    def job(langkah):
        return Job(tipe=JobType.pindah_aktifkan, payload={"kemajuan": {"langkah_aktifkan": langkah}})

    assert pindah.boleh_batal_aktifkan(job(None)) and pindah.boleh_batal_aktifkan(job("tarik"))
    assert not pindah.boleh_batal_aktifkan(job("tukar")) and not pindah.boleh_batal_aktifkan(job("verifikasi"))


def test_status_awal_pratinjau_ditolak(sesi, siap, pb):
    siap.status = StatusHosting.pratinjau
    sesi.commit()
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _aktifkan(sesi, siap)
    assert e.value.pesan == pindah.PESAN_STATUS_AKTIFKAN
    assert _h(sesi, siap).status == StatusHosting.pratinjau and pb.panggilan == []
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_hosting_aktifkan.py -q`. Expected: `AttributeError: module 'wpmgr.hosting.pindah' has no attribute 'tangani_pindah_aktifkan'` (fixture `halaman` gagal karena `ambil_halaman_verifikasi` belum ada).

- [ ] **Step 3: Implementasikan.** Di `src/wpmgr/hosting/pindah.py`:

Ganti blok impor dengan:

```python
import logging
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from wpmgr.config import get_settings
from wpmgr.connector_paket import isi_mu_plugin_pratinjau
from wpmgr.errors import TRANSIENT
from wpmgr.fitur import STAGING, punya_fitur
from wpmgr.hosting import dns
from wpmgr.hosting import umum as hu
from wpmgr.jobs.queue import LANGKAH_AKTIFKAN_SESUDAH_TUKAR
from wpmgr.models import HostingVps, StatusHosting
from wpmgr.site_client import MelebihiBatas, TanpaHasil, TenggatHabis, minta_bertenggat
from wpmgr.staging import tarik
from wpmgr.staging import umum as stg
from wpmgr.staging.aman import PathTidakAman, baca_terbatas, hapus_berkas, tulis_atomik
from wpmgr.staging.indeks import Indeks
from wpmgr.staging.pembantu import GalatPembantu, tulis_htpasswd_pratinjau
from wpmgr.staging.rencana import RAM_MINIMUM, format_byte

log = logging.getLogger("wpmgr.hosting.pindah")
```

Tambahkan ke akhir berkas:

```python
# ---- pindah_aktifkan (spec §10.4) ---------------------------------------------------

LANGKAH_AKTIFKAN = ("dns", "sertifikat", "tarik", "tukar", "verifikasi", "beres")
ALAMAT_VERIFIKASI = "127.0.0.1"
TIMEOUT_VERIFIKASI = 15.0
TENGGAT_VERIFIKASI = 20.0
BATAS_VERIFIKASI = 64 * 1024
BATAS_WP_CONFIG = 256 * 1024
UA_VERIFIKASI = "WP-Manager-Hosting/4.0"
PESAN_STATUS_AKTIFKAN = "Aktivasi hanya bisa dari status menunggu DNS atau gagal."
PESAN_NGINX = "Konfigurasi nginx domain belum dapat dipasang; aktivasi ditunda."
PESAN_SERTIFIKAT = "Sertifikat domain belum dapat diterbitkan; dicoba lagi otomatis sesudah jeda."
PESAN_TUKAR_DITOLAK = ("VPS menolak mengaktifkan situs (sertifikat, database, atau container belum siap); "
                       "tidak ada yang diubah.")
PESAN_VERIFIKASI = "Situs belum menjawab HTTPS dengan benar lewat VPS; diperiksa lagi otomatis."
STATUS_BOLEH_AKTIFKAN = ("menunggu_dns", "gagal")


def ambil_halaman_verifikasi(host: str) -> tuple[int, dict] | None:
    """GET https://<host>/ ke nginx host lokal dengan SNI host (spec §10.4).

    Sertifikat diverifikasi terhadap nama host: situs yang hanya punya
    sertifikat pratinjau tidak lolos. None = tidak ada jawaban yang sah.
    """
    with httpx.Client(follow_redirects=False, limits=httpx.Limits(max_keepalive_connections=0)) as http:
        try:
            status, header, _ = minta_bertenggat(
                http, "GET", f"https://{ALAMAT_VERIFIKASI}/",
                headers={"Host": host, "User-Agent": UA_VERIFIKASI, "Connection": "close",
                         "Accept-Encoding": "identity"},
                timeout=TIMEOUT_VERIFIKASI, tenggat=TENGGAT_VERIFIKASI, batas_byte=BATAS_VERIFIKASI, potong=True,
                ekstensi={"sni_hostname": host})
        except (httpx.HTTPError, TenggatHabis, TanpaHasil, MelebihiBatas):
            return None
    return status, {str(k).lower(): str(v) for k, v in header.items()}


def verifikasi(h, akar: Path) -> list[str]:
    """Masalah aktivasi (kosong = lolos): HTTPS 2xx/3xx tanpa 401 dan tanpa noindex, wp-config tanpa pratinjau."""
    masalah = []
    for host in [h.domain] + ([f"www.{h.domain}"] if h.dengan_www else []):
        jawab = ambil_halaman_verifikasi(host)
        if jawab is None:
            masalah.append(f"{host}: tidak menjawab HTTPS dengan sertifikat yang sah")
            continue
        status, header = jawab
        if status == 401:
            masalah.append(f"{host}: masih meminta kata sandi pratinjau")
        elif not 200 <= status < 400:
            masalah.append(f"{host}: HTTP {status}")
        elif "noindex" in header.get("x-robots-tag", "").lower():
            masalah.append(f"{host}: masih mengirim X-Robots-Tag noindex")
    try:
        isi = baca_terbatas(akar / "files", "wp-config.php", BATAS_WP_CONFIG)
    except (PathTidakAman, OSError):
        masalah.append("wp-config.php situs tidak terbaca")
    else:
        if b"WPMGR_PRATINJAU" in isi:
            masalah.append("wp-config.php masih dalam mode pratinjau")
    return masalah


def boleh_batal_aktifkan(job) -> bool:
    """Batal hanya berlaku sebelum tukar (pola dorong Lapis 3)."""
    return stg.kemajuan(job).get("langkah_aktifkan") not in LANGKAH_AKTIFKAN_SESUDAH_TUKAR


def _langkah(sesi, job, langkah: str, **lain) -> dict:
    # Ditulis SEBELUM langkahnya dijalankan (spec §10.4).
    return stg.simpan_kemajuan(sesi, job, langkah_aktifkan=langkah, **lain)


def _sebelum_tukar(sesi, job, site, h: HostingVps, pb) -> None:
    """dns -> sertifikat -> tarik. Sebelum tukar, percobaan ulang selalu mulai lagi dari dns."""
    _langkah(sesi, job, "dns")
    hasil = dns.periksa_dns(h)
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    baris.dns_hasil = hasil.ke_json()
    baris.dns_dicek_pada = hu.sekarang()
    sesi.commit()
    if not hasil.ok:
        raise stg.GalatDitolakTanpaUbah(hasil.pesan)

    _langkah(sesi, job, "sertifikat")
    nginx_gagal = sertifikat_gagal = False
    with stg.detak_latar(sesi, job):
        try:
            # Idempoten; memastikan port 80 melayani tantangan ACME domain.
            pb.prod_domain(h.nama)
        except GalatPembantu as exc:
            nginx_gagal = True
            log.warning("prod-domain %s gagal sebelum sertifikat: %s", h.nama, exc.kode)
        if not nginx_gagal:
            try:
                pb.prod_sertifikat(h.nama)
            except GalatPembantu as exc:
                sertifikat_gagal = True
                log.warning("Sertifikat domain %s gagal: %s", h.domain, exc.kode)
    if nginx_gagal:
        raise stg.GalatDitolakTanpaUbah(PESAN_NGINX)
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    if sertifikat_gagal:
        # R15 + backoff §8.4: tidak diulang segera; cron/tombol menjadwalkan ulang.
        baris.sertifikat_gagal_kali = (baris.sertifikat_gagal_kali or 0) + 1
        baris.sertifikat_gagal_pada = hu.sekarang()
        sesi.commit()
        raise stg.GalatDitolakTanpaUbah(PESAN_SERTIFIKAT)
    baris.sertifikat_pada = hu.sekarang()
    baris.sertifikat_gagal_kali = 0
    baris.sertifikat_gagal_pada = None
    sesi.commit()

    _langkah(sesi, job, "tarik")
    if not (job.payload or {}).get("tanpa_tarik_ulang"):
        # Data terakhir dari hosting lama, lewat IP yang dipatok (RF4): DNS
        # domain sudah menunjuk VPS sendiri pada titik ini.
        k = salin(sesi, job, site, h, hu.klien_lama(site, h), pb)
        rampungkan_salinan(sesi, job, site, h, k, None)


def _mulai_tukar(sesi, job, h: HostingVps) -> None:
    """Tulis-lebih-dulu (RF5): penanda di-commit sebelum perintah apa pun dikirim ke VPS."""
    sekarang = hu.sekarang()
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    baris.dilayani_vps_pada = sekarang
    _langkah(sesi, job, "tukar", tukar_pada=sekarang.isoformat(), tukar_dikirim=False)


def _kirim_tukar(sesi, job, site, h: HostingVps, pb) -> None:
    akar = hu.dir_hosting(site.id)
    pertama = not stg.kemajuan(job).get("tukar_dikirim")
    try:
        # Dihapus SEBELUM prod-aktifkan; berkas yang tertinggal tetap diam
        # karena konstantanya dicabut prod-aktifkan (spec §7.6).
        hapus_berkas(akar / "files", MU_PLUGIN_PRATINJAU)
    except (PathTidakAman, OSError):
        k = stg.kemajuan(job)
        tarik._tambah_peringatan(k, "Mu-plugin pratinjau tidak dapat dihapus; berkasnya diam tanpa konstanta.")
        stg.simpan_kemajuan(sesi, job, peringatan=k["peringatan"])
    stg.simpan_kemajuan(sesi, job, tukar_dikirim=True)
    try:
        with stg.detak_latar(sesi, job):
            pb.prod_aktifkan(h.nama)
    except GalatPembantu as exc:
        if exc.kode != "ditolak" or not pertama:
            # Hasil tidak pasti (atau kiriman ulang): produksi tersentuh, R26.
            raise
        # Keluar 3 pada kiriman pertama: pasti tanpa perubahan (Koreksi #5).
        # Situs masih pratinjau: penanda dicabut dan pemblokir email dipasang
        # lagi sebelum status kembali menunggu DNS (Koreksi #6).
        baris = sesi.get(HostingVps, h.id, populate_existing=True)
        baris.dilayani_vps_pada = None
        _langkah(sesi, job, "dns", tukar_pada=None, tukar_dikirim=False)
        tulis_mu_plugin(akar)
        raise stg.GalatDitolakTanpaUbah(PESAN_TUKAR_DITOLAK) from None
    _langkah(sesi, job, "verifikasi")


def _verifikasi(sesi, job, site, h: HostingVps) -> None:
    akar = hu.dir_hosting(site.id)
    masalah = verifikasi(h, akar)
    if masalah:
        # Rincian (nama host milik dashboard, kode HTTP) hanya ke log server.
        log.warning("Verifikasi aktivasi %s gagal: %s", h.domain, "; ".join(masalah))
        raise hu.GalatHosting(TRANSIENT, PESAN_VERIFIKASI)
    try:
        baca_terbatas(akar / "files", MU_PLUGIN_PRATINJAU, 1)
    except (PathTidakAman, OSError):
        pass
    else:
        k = stg.kemajuan(job)
        tarik._tambah_peringatan(k, "Mu-plugin pratinjau masih ada di situs (diam tanpa konstanta); hapus manual.")
        stg.simpan_kemajuan(sesi, job, peringatan=k["peringatan"])
    _langkah(sesi, job, "beres")


def _beres(sesi, job, site, h: HostingVps) -> dict:
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    sekarang = hu.sekarang()
    baris.status = StatusHosting.aktif
    baris.aktif_pada = sekarang
    baris.galat = None
    baris.gagal_asal = None
    k = stg.kemajuan(job)
    hasil = {"domain": baris.domain, "aktif_pada": sekarang.isoformat(),
             "peringatan": list(k.get("peringatan") or [])[:10]}
    stg.catat_aktivitas(sesi, site.id, job, "Site dihosting di VPS", hasil)
    sesi.commit()
    # Backup pertama diantrekan cron hosting-cek-dns (Koreksi #2): job ini
    # masih memegang uq_jobs_hosting_aktif sampai worker menandainya sukses.
    return hasil


def aktifkan(sesi, job, site, h: HostingVps, pb) -> dict:
    k = stg.kemajuan(job)
    langkah = k.get("langkah_aktifkan")
    if langkah not in LANGKAH_AKTIFKAN_SESUDAH_TUKAR:
        if h.dilayani_vps_pada is not None:
            # "Periksa ulang" untuk gagal asal produksi: langsung verifikasi,
            # dengan jendela R26 baru (Koreksi #13). Tanpa tarik dan tukar ulang.
            _langkah(sesi, job, "verifikasi", tukar_pada=hu.sekarang().isoformat())
        else:
            awal = k.get("status_hosting_awal")
            if awal not in STATUS_BOLEH_AKTIFKAN:
                raise stg.GalatDitolakTanpaUbah(PESAN_STATUS_AKTIFKAN)
            _sebelum_tukar(sesi, job, site, h, pb)
            _mulai_tukar(sesi, job, h)
    if stg.kemajuan(job).get("langkah_aktifkan") == "tukar":
        _kirim_tukar(sesi, job, site, h, pb)
    if stg.kemajuan(job).get("langkah_aktifkan") == "verifikasi":
        _verifikasi(sesi, job, site, h)
    return _beres(sesi, job, site, h)


def tangani_pindah_aktifkan(sesi, job, klien) -> dict:
    """Handler worker; klien bawaan worker diabaikan (lihat tangani_pindah_tarik)."""
    def inti(sesi, job, site, h):
        return aktifkan(sesi, job, site, h, stg.buat_pembantu())

    site_id = job.site_id
    try:
        return hu.jalankan_hosting(sesi, job, inti, "Aktivasi hosting VPS", boleh_batal=boleh_batal_aktifkan)
    except stg.KlaimHilang:
        raise
    except Exception:
        bersihkan_bila_final(sesi, site_id)
        raise
```

Di `src/wpmgr/jobs/handlers.py`, ubah impor menjadi `from wpmgr.hosting.pindah import tangani_pindah_aktifkan, tangani_pindah_tarik` dan tambahkan `JobType.pindah_aktifkan: tangani_pindah_aktifkan,` ke `HANDLER`.

- [ ] **Step 4: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/integration/test_hosting_aktifkan.py tests/integration/test_hosting_pindah.py tests/integration/test_hosting_antrean.py -q`. Expected: semua lulus.

- [ ] **Step 5: Seluruh test unit dan integrasi, lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 6: Commit.**

```bash
git add src/wpmgr/hosting/pindah.py src/wpmgr/jobs/handlers.py tests/integration/test_hosting_aktifkan.py
git commit -m "feat(hosting): job pindah_aktifkan dengan tulis-lebih-dulu, verifikasi, dan pemulihan R26

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: API hosting, nama unik lintas staging, dan penjaga hapus site

**Files:**
- Create: `src/wpmgr/web/routes_hosting.py`
- Modify: `src/wpmgr/web/app.py`, `src/wpmgr/web/routes_staging.py`, `src/wpmgr/staging/dorong.py`
- Test: `tests/integration/test_api_hosting.py`

**Interfaces:**
- Consumes (Task 4–10): `HostingVps`, `HostingBackup`, `StatusHosting`, `JOB_HOSTING`, `Settings.hosting_aktif`/`hosting_ipv4`/`hosting_ipv6`/`jalur_hosting`/`staging_domain`, `aman.domain_sah`, `aman.host_dari_url`, `aman.nama_prod_dari_url`, `hosting.dns` (`ip_lama_dari_dns`, `ada_www`, `periksa_dns`, `instruksi`, `coba_lagi_pada`, `backoff_mengizinkan`), `hosting.umum` (`url_pratinjau`, `ASAL_SALINAN`), `hosting.pindah` (`PESAN_STATUS_TARIK`, `PESAN_STATUS_AKTIFKAN`, `PESAN_SUDAH_DILAYANI`, `PESAN_IZIN`, `boleh_batal_aktifkan`), pembantu (`tulis_htpasswd_pratinjau`, `hapus_htpasswd_pratinjau`, `hash_sandi`, `sandi_baru`, `PENGGUNA_PRATINJAU`, `GalatPembantu`, `Pembantu.prod_router_muat`, `prod_hapus`), `staging.cron` (`_kunci_site`, `_dir_nyata`, `_ke_nisan`, `_hapus_nisan`), `staging.umum.buat_pembantu`/`catat_aktivitas`, `web.auth.pengguna_api`; fixture `klien_web`, `site_hosting`, `hosting_aktif`, `staging_palsu.PembantuHostingPalsu`.
- Produces:
  - Route (`routes_hosting.router`): `GET/POST/DELETE /api/sites/{id}/hosting`, `POST /api/sites/{id}/hosting/tarik`, `/lanjut-dns`, `/kembali-pratinjau`, `/aktifkan` (body `{tanpa_tarik_ulang?: bool, konfirmasi?: str}`; DNS gagal → 409 `{"detail", "dns_hasil"}`), `/sandi`, `/batal`. GET menjawab `{"aktif_fitur", "izin_connector", "ipv4", "ipv6", "hosting": {...} | null, "job": {"id","tipe","status","progres"} | null, "backup": [...]}`.
  - `routes_hosting`: `TEKS_STATUS`, `BATAS_DAFTAR = 50`, `dict_hosting(h, s) -> dict`, `daftar_backup(sesi, site_id) -> list[dict]`, `job_aktif_hosting(sesi, site_id) -> Job | None`, `kunci_hosting(sesi, site_id) -> HostingVps | None`, `fitur_hosting() -> Settings`, `site_atau_404(sesi, site_id) -> Site`, `simpan_job_hosting(sesi, job) -> Job`, `tolak_bila_sibuk(sesi, site_id)`, pesan `PESAN_*` (dipakai Task 13–14).
  - `routes_staging.ringkas_kemajuan` mengenal `langkah_aktifkan` (selain `tarik`), `tahap_backup`, dan tahap `pratinjau`; `routes_staging._nama_unik` melewati `vps-<n>` untuk setiap `hosting_vps.nama = n`.
  - `routes_staging.bersihkan_untuk_hapus_site` menolak (409) selama ada baris `hosting_vps`: `PESAN_HAPUS_SITE_HOSTING = "Batalkan pindah hosting dulu."`, `PESAN_HAPUS_SITE_DIHOSTING = "Site ini dihosting di VPS; lepas manual (README)."`.
  - `dorong.hapus_dir_staging(relatif, akar=None)`; `dorong.hapus_nisan(akar, nisan)` menghapus relatif terhadap `akar` (Koreksi #9).

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/integration/test_api_hosting.py`
```python
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from staging_palsu import PembantuHostingPalsu

from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting.dns import HasilDns
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    ActivityLog,
    HostingVps,
    Job,
    JobStatus,
    JobType,
    Site,
    SiteStatus,
    Staging,
    StatusHosting,
)
from wpmgr.staging import umum
from wpmgr.staging.pembantu import GalatPembantu
from wpmgr.web import routes_hosting, routes_staging
from wpmgr.web.routes_staging import ringkas_kemajuan

pytestmark = pytest.mark.integration

IP_LAMA = "93.184.216.34"
ROUTE = [
    ("GET", "/api/sites/{id}/hosting"), ("POST", "/api/sites/{id}/hosting"), ("DELETE", "/api/sites/{id}/hosting"),
    ("POST", "/api/sites/{id}/hosting/tarik"), ("POST", "/api/sites/{id}/hosting/lanjut-dns"),
    ("POST", "/api/sites/{id}/hosting/kembali-pratinjau"), ("POST", "/api/sites/{id}/hosting/aktifkan"),
    ("POST", "/api/sites/{id}/hosting/sandi"), ("POST", "/api/sites/{id}/hosting/batal"),
]


@pytest.fixture
def pb(hosting_aktif, monkeypatch):
    palsu = PembantuHostingPalsu(hosting_aktif)
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


@pytest.fixture
def dns_lama(monkeypatch):
    """DNS publik tiruan untuk Pindahkan: A domain ke hosting lama, www ada."""
    keadaan = {"ip": IP_LAMA, "www": True}
    monkeypatch.setattr(dns_mod, "ip_lama_dari_dns", lambda domain, **kw: keadaan["ip"])
    monkeypatch.setattr(dns_mod, "ada_www", lambda domain, **kw: keadaan["www"])
    return keadaan


@pytest.fixture
def cek_dns(monkeypatch):
    keadaan = {"ok": True, "n": 0}

    def periksa(h, **kw):
        keadaan["n"] += 1
        item = [{"nama": "@", "jenis": "A", "terlihat": ["169.58.91.181"], "harus": ["169.58.91.181"],
                 "ok": keadaan["ok"], "kode": "cocok" if keadaan["ok"] else "kurang"}]
        return HasilDns(ok=keadaan["ok"], dicek=datetime.now(timezone.utc).isoformat(), nama=item)

    monkeypatch.setattr(dns_mod, "periksa_dns", periksa)
    return keadaan


@pytest.fixture
def site_baru(sesi, site, hosting_aktif):
    site.url = "https://www.toko.co.id"
    site.fitur = ["self_update", "staging"]
    sesi.commit()
    return site


def _status(sesi, h, status, **lain):
    h.status = status
    for k, v in lain.items():
        setattr(h, k, v)
    sesi.commit()


def _h(sesi, site_id):
    return sesi.scalar(select(HostingVps).where(HostingVps.site_id == site_id)
                       .execution_options(populate_existing=True))


@pytest.mark.parametrize("metode,path", ROUTE)
def test_anonim_ditolak(engine, hosting_aktif, metode, path):
    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), base_url="https://testserver")
    assert anon.request(metode, path.format(id=uuid.uuid4()), json={}).status_code == 401


def test_fitur_mati(klien_web, site, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_HOSTING_IPV4", raising=False)
    get_settings.cache_clear()
    assert klien_web.get(f"/api/sites/{site.id}/hosting").json() == {"aktif_fitur": False}
    assert klien_web.post(f"/api/sites/{site.id}/hosting", json={}).status_code == 404
    assert klien_web.post(f"/api/sites/{site.id}/hosting/aktifkan", json={}).status_code == 404


# ---- Pindahkan ---------------------------------------------------------------------------


def test_pindahkan_membuat_baris_sandi_sekali_dan_job(klien_web, sesi, site_baru, pb, dns_lama):
    r = klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["pengguna"] == "pratinjau" and len(d["sandi"]) >= 16
    h = _h(sesi, site_baru.id)
    assert (h.nama, h.domain, h.dengan_www, h.ip_lama, h.status) == (
        "toko-co-id", "toko.co.id", True, IP_LAMA, StatusHosting.menyalin)
    assert bcrypt.checkpw(d["sandi"].encode(), h.sandi_hash.encode())
    job = sesi.get(Job, d["job_id"])
    assert job.tipe == JobType.pindah_tarik and job.dibuat_oleh is not None
    data = klien_web.get(f"/api/sites/{site_baru.id}/hosting").json()
    assert data["hosting"]["url_pratinjau"] == "https://vps-toko-co-id.staging.contoh.id"
    assert data["hosting"]["status_teks"] == "Menyalin ke VPS"
    assert "sandi" not in str(data["hosting"]) and "sandi_hash" not in data["hosting"]
    assert data["job"]["tipe"] == "pindah_tarik"
    assert [i["jenis"] for i in data["hosting"]["instruksi"]] == ["A", "AAAA", "A", "AAAA"]
    assert klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={}).status_code == 409


def test_pindahkan_ditolak_tanpa_izin_ip_lama_atau_domain_staging(klien_web, sesi, site_baru, pb, dns_lama):
    site_baru.fitur = ["self_update"]
    sesi.commit()
    r = klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={})
    assert r.status_code == 409 and "Izinkan staging" in r.json()["detail"]
    site_baru.fitur = ["staging"]
    sesi.commit()
    dns_lama["ip"] = None
    r = klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={})
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_IP_LAMA
    dns_lama["ip"] = IP_LAMA
    site_baru.url = "https://x.staging.contoh.id"
    sesi.commit()
    r = klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={})
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_DOMAIN
    assert sesi.scalar(select(HostingVps.id)) is None and sesi.query(Job).count() == 0


def test_nama_hosting_menghindari_label_staging_vps(klien_web, sesi, site_baru, pb, dns_lama):
    lain = Site(id=uuid.uuid4(), nama="L", url="https://lain.test", status=SiteStatus.active, secret_terenkripsi=b"x")
    sesi.add(lain)
    sesi.flush()
    sesi.add(Staging(site_id=lain.id, nama="vps-toko-co-id"))
    sesi.commit()
    klien_web.post(f"/api/sites/{site_baru.id}/hosting", json={})
    assert _h(sesi, site_baru.id).nama == "toko-co-id-2"


def test_nama_staging_melewati_label_vps_milik_hosting(sesi, site_hosting):
    assert routes_staging._nama_unik(sesi, "vps-toko-co-id") == "vps-toko-co-id-2"
    assert routes_staging._nama_unik(sesi, "toko-co-id") == "toko-co-id"


# ---- transisi status -----------------------------------------------------------------------


def test_salin_ulang_dan_aturannya(klien_web, sesi, site_hosting, pb):
    url = f"/api/sites/{site_hosting.site_id}/hosting/tarik"
    _status(sesi, site_hosting, StatusHosting.menyalin)
    assert klien_web.post(url).status_code == 409
    _status(sesi, site_hosting, StatusHosting.pratinjau)
    r = klien_web.post(url)
    assert r.status_code == 200 and sesi.get(Job, r.json()["job_id"]).tipe == JobType.pindah_tarik
    assert klien_web.post(url).status_code == 409
    sesi.query(Job).delete()
    _status(sesi, site_hosting, StatusHosting.gagal, gagal_asal="salinan")
    assert klien_web.post(url).status_code == 200
    sesi.query(Job).delete()
    _status(sesi, site_hosting, StatusHosting.gagal, gagal_asal="produksi",
            dilayani_vps_pada=datetime.now(timezone.utc))
    r = klien_web.post(url)
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_SUDAH_DILAYANI


def test_lanjut_dns_dan_kembali_pratinjau(klien_web, sesi, site_hosting):
    dasar = f"/api/sites/{site_hosting.site_id}/hosting"
    assert klien_web.post(f"{dasar}/lanjut-dns").status_code == 409
    _status(sesi, site_hosting, StatusHosting.pratinjau)
    assert klien_web.post(f"{dasar}/lanjut-dns").status_code == 200
    assert _h(sesi, site_hosting.site_id).status == StatusHosting.menunggu_dns
    assert klien_web.post(f"{dasar}/kembali-pratinjau").status_code == 200
    assert _h(sesi, site_hosting.site_id).status == StatusHosting.pratinjau
    assert klien_web.post(f"{dasar}/kembali-pratinjau").status_code == 409


def test_aktifkan_dns_belum_lolos_409_dengan_hasil(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    cek_dns["ok"] = False
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 409
    assert r.json()["dns_hasil"]["ok"] is False and r.json()["detail"] == dns_mod.PESAN_BELUM
    h = _h(sesi, site_hosting.site_id)
    assert h.dns_hasil["ok"] is False and h.dns_dicek_pada is not None
    assert sesi.query(Job).count() == 0


def test_aktifkan_dns_lolos_mengantrekan_job(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 200
    job = sesi.get(Job, r.json()["job_id"])
    assert job.tipe == JobType.pindah_aktifkan
    assert job.payload == {"tanpa_tarik_ulang": False, "manual": True}


def test_aktifkan_tanpa_tarik_ulang_wajib_konfirmasi_domain(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    url = f"/api/sites/{site_hosting.site_id}/hosting/aktifkan"
    r = klien_web.post(url, json={"tanpa_tarik_ulang": True, "konfirmasi": "toko"})
    assert r.status_code == 400 and r.json()["detail"] == routes_hosting.PESAN_KONFIRMASI_DOMAIN
    r = klien_web.post(url, json={"tanpa_tarik_ulang": True, "konfirmasi": "toko.co.id"})
    assert r.status_code == 200
    assert sesi.get(Job, r.json()["job_id"]).payload["tanpa_tarik_ulang"] is True


def test_aktifkan_menghormati_backoff_sertifikat(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns, sertifikat_gagal_kali=2,
            sertifikat_gagal_pada=datetime.now(timezone.utc) - timedelta(minutes=5))
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_BACKOFF
    assert cek_dns["n"] == 0


def test_periksa_ulang_tanpa_cek_dns(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.gagal, gagal_asal="produksi",
            dilayani_vps_pada=datetime.now(timezone.utc))
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 200 and cek_dns["n"] == 0


def test_aktifkan_ditolak_dari_status_lain(klien_web, sesi, site_hosting, cek_dns):
    _status(sesi, site_hosting, StatusHosting.pratinjau)
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/aktifkan", json={})
    assert r.status_code == 409 and cek_dns["n"] == 0


# ---- kata sandi, batal, hapus ------------------------------------------------------------------


def test_sandi_baru_menulis_htpasswd_dan_memuat_router(klien_web, sesi, site_hosting, hosting_aktif, pb):
    _status(sesi, site_hosting, StatusHosting.pratinjau, ditarik_pada=datetime.now(timezone.utc))
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/sandi")
    assert r.status_code == 200
    sandi = r.json()["sandi"]
    baris = (hosting_aktif / "router" / "toko-co-id.htpasswd").read_text().strip()
    assert baris.startswith("pratinjau:") and bcrypt.checkpw(sandi.encode(), baris.split(":", 1)[1].encode())
    assert "prod_router_muat" in pb.nama_panggilan()
    assert bcrypt.checkpw(sandi.encode(), _h(sesi, site_hosting.site_id).sandi_hash.encode())


def test_sandi_gagal_memulihkan_htpasswd_lama(klien_web, sesi, site_hosting, hosting_aktif, pb):
    _status(sesi, site_hosting, StatusHosting.pratinjau, ditarik_pada=datetime.now(timezone.utc))
    lama = site_hosting.sandi_hash
    pb.gagal["prod_router_muat"] = GalatPembantu("docker", "Memuat ulang router hosting gagal. Perintah Docker "
                                                           "di server staging gagal.")
    r = klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/sandi")
    assert r.status_code == 502 and r.json()["detail"].startswith("Memuat ulang router hosting gagal.")
    assert (hosting_aktif / "router" / "toko-co-id.htpasswd").read_text() == f"pratinjau:{lama}\n"
    assert _h(sesi, site_hosting.site_id).sandi_hash == lama


def test_sandi_ditolak_sesudah_dilayani(klien_web, sesi, site_hosting, pb):
    _status(sesi, site_hosting, StatusHosting.aktif, dilayani_vps_pada=datetime.now(timezone.utc))
    assert klien_web.post(f"/api/sites/{site_hosting.site_id}/hosting/sandi").status_code == 409


def test_batal(klien_web, sesi, site_hosting):
    url = f"/api/sites/{site_hosting.site_id}/hosting/batal"
    assert klien_web.post(url).status_code == 409
    buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    assert klien_web.post(url).status_code == 200
    assert _h(sesi, site_hosting.site_id).batal_diminta_pada is not None
    sesi.query(Job).delete()
    sesi.commit()
    buat_job(sesi, site_hosting.site_id, JobType.pindah_aktifkan, {"kemajuan": {"langkah_aktifkan": "tukar"}})
    r = klien_web.post(url)
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_BATAL_SESUDAH_TUKAR


def test_hapus_menolak_konfirmasi_salah_dilayani_atau_sibuk(klien_web, sesi, site_hosting, pb):
    url = f"/api/sites/{site_hosting.site_id}/hosting"
    assert klien_web.request("DELETE", url, json={"konfirmasi": "toko"}).status_code == 400
    buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    assert klien_web.request("DELETE", url, json={"konfirmasi": "toko.co.id"}).status_code == 409
    sesi.query(Job).delete()
    _status(sesi, site_hosting, StatusHosting.aktif, dilayani_vps_pada=datetime.now(timezone.utc))
    r = klien_web.request("DELETE", url, json={"konfirmasi": "toko.co.id"})
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_HAPUS_DILAYANI
    assert pb.panggilan == []


def test_hapus_membongkar_situs_baris_dan_direktori(klien_web, sesi, site_hosting, hosting_aktif, pb):
    akar = hosting_aktif / str(site_hosting.site_id)
    (akar / "files").mkdir(parents=True)
    (akar / "files" / "index.php").write_bytes(b"x")
    (hosting_aktif / "router").mkdir(parents=True, exist_ok=True)
    (hosting_aktif / "router" / "toko-co-id.htpasswd").write_text("pratinjau:x\n")
    r = klien_web.request("DELETE", f"/api/sites/{site_hosting.site_id}/hosting", json={"konfirmasi": "toko.co.id"})
    assert r.status_code == 200
    assert ("prod_hapus", "toko-co-id") in pb.panggilan
    assert _h(sesi, site_hosting.site_id) is None
    assert not akar.exists()
    assert not (hosting_aktif / "router" / "toko-co-id.htpasswd").exists()
    assert not any(p.name.startswith(".hapus-") for p in hosting_aktif.iterdir())


def test_hapus_galat_pembantu_502_tanpa_mengubah(klien_web, sesi, site_hosting, pb):
    pb.gagal["prod_hapus"] = GalatPembantu("ditolak", "Menghapus situs hosting gagal. Skrip pembantu menolak "
                                                      "permintaan ini.")
    r = klien_web.request("DELETE", f"/api/sites/{site_hosting.site_id}/hosting", json={"konfirmasi": "toko.co.id"})
    assert r.status_code == 502
    assert _h(sesi, site_hosting.site_id) is not None


def test_hapus_site_ditolak_selama_ada_hosting(klien_web, sesi, site_hosting):
    r = klien_web.delete(f"/api/sites/{site_hosting.site_id}")
    assert r.status_code == 409 and r.json()["detail"] == routes_staging.PESAN_HAPUS_SITE_HOSTING
    _status(sesi, site_hosting, StatusHosting.aktif, dilayani_vps_pada=datetime.now(timezone.utc))
    r = klien_web.delete(f"/api/sites/{site_hosting.site_id}")
    assert r.status_code == 409 and r.json()["detail"] == routes_staging.PESAN_HAPUS_SITE_DIHOSTING
    assert sesi.get(Site, site_hosting.site_id) is not None


# ---- ringkasan progres -----------------------------------------------------------------------


@pytest.mark.parametrize("kemajuan,tahap,label", [
    ({"langkah_aktifkan": "dns"}, "dns", "Memeriksa DNS"),
    ({"langkah_aktifkan": "tukar"}, "tukar", "Mengaktifkan situs di VPS"),
    ({"langkah_aktifkan": "tarik", "tahap": "berkas"}, "berkas", "Menyalin berkas"),
    ({"tahap": "pratinjau"}, "pratinjau", "Menyiapkan pratinjau (sertifikat dan nginx)"),
    ({"tahap_backup": "backup"}, "backup", "Membuat backup"),
])
def test_ringkas_kemajuan_tahap_hosting(kemajuan, tahap, label):
    p = ringkas_kemajuan(Job(tipe=JobType.pindah_aktifkan, payload={"kemajuan": kemajuan}))
    assert (p["tahap"], p["label"]) == (tahap, label)


def test_get_menampilkan_backup_terbaru_dan_gagal_asal(klien_web, sesi, site_hosting):
    from wpmgr.models import HostingBackup

    for i in range(3):
        sesi.add(HostingBackup(site_id=site_hosting.site_id, tujuan="lokal", stempel=f"2026100{i + 1}T023000Z",
                               status="tersedia", manual=i == 2, ukuran_db=1024, ukuran_file=2048,
                               sha256_db="a" * 64, sha256_file="b" * 64))
    _status(sesi, site_hosting, StatusHosting.gagal, gagal_asal="salinan", galat="Salin ke VPS gagal.")
    d = klien_web.get(f"/api/sites/{site_hosting.site_id}/hosting").json()
    assert [b["stempel"] for b in d["backup"]] == ["20261003T023000Z", "20261002T023000Z", "20261001T023000Z"]
    assert d["backup"][0]["manual"] is True and d["backup"][0]["ukuran_db_teks"] == "1,0 KB"
    assert d["hosting"]["gagal_asal"] == "salinan" and d["hosting"]["galat"] == "Salin ke VPS gagal."
    assert sesi.scalar(select(ActivityLog.id).where(ActivityLog.site_id == site_hosting.site_id)) is None
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/integration/test_api_hosting.py -q`. Expected: `ImportError: cannot import name 'routes_hosting' from 'wpmgr.web'`.

- [ ] **Step 3: `hapus_nisan` memakai akar yang diberikan (Koreksi #9).** Di `src/wpmgr/staging/dorong.py`, ganti dua baris pertama `hapus_dir_staging` dan satu baris di `hapus_nisan`:

```python
def hapus_dir_staging(relatif: str, akar: Path | None = None) -> None:
    """Hapus direktori di bawah `akar` (bawaan WPMGR_STAGING_DIR) tanpa pernah mengikuti symlink."""
```

dan di badan fungsinya ganti `p = jalur_di_dalam(get_settings().jalur_staging, relatif)` dengan:

```python
        p = jalur_di_dalam(akar if akar is not None else get_settings().jalur_staging, relatif)
```

Di `hapus_nisan`, ganti `hapus_dir_staging(nisan)` dengan `hapus_dir_staging(nisan, akar)`.

- [ ] **Step 4: Perubahan `routes_staging`.** Di `src/wpmgr/web/routes_staging.py`:

Tambahkan `HostingVps` ke impor `wpmgr.models`.

Ganti `LABEL_TAHAP` dengan (tambahan Lapis 4 di dua baris terakhir):

```python
LABEL_TAHAP = {
    "mulai": "Menunggu giliran", "manifest": "Membaca daftar berkas", "berkas": "Menyalin berkas",
    "tanda_air": "Membaca tanda air", "db": "Menyalin database", "impor": "Mengimpor database",
    "penyiapan": "Menyiapkan container", "sertifikat": "Menerbitkan sertifikat", "sebelum": "Memeriksa halaman",
    "update": "Menjalankan update", "sesudah": "Memeriksa halaman sesudah update", "nilai": "Menilai hasil",
    "rencana": "Menyusun rencana", "snapshot_berkas": "Snapshot berkas produksi",
    "snapshot_db": "Snapshot database produksi", "snapshot_catat": "Mencatat snapshot",
    "unggah": "Mengunggah ke produksi", "cek_ulang": "Memeriksa ulang data baru", "terapkan": "Menerapkan di produksi",
    "cek": "Memeriksa halaman utama", "tanpa_perubahan": "Tidak ada perubahan untuk didorong",
    "pratinjau": "Menyiapkan pratinjau (sertifikat dan nginx)", "selesai": "Menyelesaikan", "dns": "Memeriksa DNS",
    "tukar": "Mengaktifkan situs di VPS", "verifikasi": "Memeriksa situs lewat HTTPS", "beres": "Menyelesaikan aktivasi",
    "backup": "Membuat backup",
}
```

Di `ringkas_kemajuan`, ganti pemilihan `tahap`:

```python
    tahap_uji = k.get("tahap_uji")
    langkah = k.get("langkah_aktifkan")
    # Uji menjalankan tarik lebih dulu (tahap_uji "tarik"), dan aktivasi
    # hosting juga (langkah "tarik"): selama itu tahap tarik yang ditampilkan.
    tahap = k.get("tahap_dorong") or k.get("tahap_balik") \
        or (tahap_uji if tahap_uji not in (None, "tarik") else None) or k.get("tahap_backup") \
        or (langkah if langkah not in (None, "tarik") else None) or k.get("tahap") or "mulai"
```

Ganti `_nama_unik`:

```python
def _nama_unik(sesi, dasar: str) -> str:
    kandidat = dasar
    for i in range(2, 100):
        # Label vps-<n> dipakai host pratinjau hosting VPS (spec Lapis 4 §10.7).
        dipakai_hosting = kandidat.startswith("vps-") and sesi.scalar(
            select(HostingVps.id).where(HostingVps.nama == kandidat[4:])) is not None
        if not dipakai_hosting and sesi.scalar(select(Staging.id).where(Staging.nama == kandidat)) is None:
            return kandidat
        akhiran = f"-{i}"
        kandidat = dasar[:40 - len(akhiran)].rstrip("-") + akhiran
    raise HTTPException(status_code=409, detail="Nama staging untuk site ini tidak dapat ditentukan.")
```

Sesudah `PESAN_PAKSA_KONFIRMASI`, tambahkan:

```python
# Spec Lapis 4 §10.7: kaskade site tidak boleh meninggalkan container produksi yatim.
PESAN_HAPUS_SITE_HOSTING = "Batalkan pindah hosting dulu."
PESAN_HAPUS_SITE_DIHOSTING = "Site ini dihosting di VPS; lepas manual (README)."
```

Di awal badan `bersihkan_untuk_hapus_site` (sebelum `berjalan = ...`), tambahkan:

```python
    h = sesi.scalar(select(HostingVps).where(HostingVps.site_id == site.id))
    if h is not None:
        raise HTTPException(status_code=409, detail=PESAN_HAPUS_SITE_DIHOSTING if h.dilayani_vps_pada is not None
                            else PESAN_HAPUS_SITE_HOSTING)
```

- [ ] **Step 5: Route hosting.**

File: `src/wpmgr/web/routes_hosting.py`
```python
"""JSON API hosting VPS (spec Lapis 4 §11).

Kunci baris: route yang mengubah status atau mengantrekan job mengikuti
kontrak kunci cron (`staging.cron._kunci_site`): baris `sites` FOR NO KEY
UPDATE, lalu `hosting_vps` FOR UPDATE, dipegang sampai commit. Cek DNS
(jaringan, sampai 10 detik) selalu di luar kunci, dan keadaan diperiksa
ulang sesudah kunci diambil. Semua `detail` galat adalah teks tetap.
"""

import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from wpmgr import db
from wpmgr.config import Settings, get_settings
from wpmgr.fitur import STAGING, punya_fitur
from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting import pindah
from wpmgr.hosting import umum as hu
from wpmgr.models import (
    JOB_HOSTING,
    HostingBackup,
    HostingVps,
    Job,
    JobStatus,
    JobType,
    Site,
    Staging,
    StatusHosting,
    User,
)
from wpmgr.staging import umum as stg
from wpmgr.staging.aman import domain_sah, host_dari_url, nama_prod_dari_url
from wpmgr.staging.cron import _dir_nyata, _hapus_nisan, _ke_nisan, _kunci_site
from wpmgr.staging.pembantu import (
    PENGGUNA_PRATINJAU,
    GalatPembantu,
    hapus_htpasswd_pratinjau,
    hash_sandi,
    sandi_baru,
    tulis_htpasswd_pratinjau,
)
from wpmgr.staging.rencana import format_byte
from wpmgr.web.auth import pengguna_api
from wpmgr.web.routes_staging import ringkas_kemajuan

log = logging.getLogger("wpmgr.web.routes_hosting")
router = APIRouter()
PenggunaApi = Annotated[User, Depends(pengguna_api)]

BATAS_DAFTAR = 50
TEKS_STATUS = {"menyalin": "Menyalin ke VPS", "pratinjau": "Pratinjau", "menunggu_dns": "Menunggu DNS",
               "mengaktifkan": "Mengaktifkan", "aktif": "Dihosting di VPS", "gagal": "Gagal"}
PESAN_BELUM_ADA = "Pindah hosting untuk site ini belum dimulai."
PESAN_SUDAH_ADA = "Pindah hosting untuk site ini sudah dimulai."
PESAN_IZIN = pindah.PESAN_IZIN
PESAN_DOMAIN = ("Domain site ini tidak dapat dipindahkan: URL harus https:// dengan nama domain biasa, "
                "bukan di bawah domain staging.")
PESAN_IP_LAMA = ("Alamat IP hosting lama tidak dapat ditentukan dari DNS publik (domain sudah menunjuk VPS, "
                 "atau DNS tidak menjawab).")
PESAN_BENTROK = "Nama atau domain situs bentrok dengan pindah hosting lain; coba lagi."
PESAN_SIBUK = "Tunggu pekerjaan hosting yang sedang berjalan selesai."
PESAN_SIBUK_SIMPAN = "Masih ada pekerjaan hosting yang tertunda atau berjalan untuk site ini."
PESAN_SUDAH_DILAYANI = pindah.PESAN_SUDAH_DILAYANI
PESAN_STATUS_TARIK = pindah.PESAN_STATUS_TARIK
PESAN_STATUS_AKTIFKAN = pindah.PESAN_STATUS_AKTIFKAN
PESAN_LANJUT_DNS = "Lanjut ke DNS hanya dari status pratinjau."
PESAN_KEMBALI = "Kembali ke pratinjau hanya dari status menunggu DNS."
PESAN_KONFIRMASI_DOMAIN = "Ketik domain persis untuk konfirmasi."
PESAN_BACKOFF = "Sertifikat domain baru saja gagal diterbitkan; tunggu jeda sebelum mencoba lagi."
PESAN_SANDI_DILAYANI = "Kata sandi pratinjau tidak berlaku lagi; situs sudah dilayani VPS."
PESAN_TANPA_JOB = "Tidak ada pekerjaan hosting yang bisa dibatalkan."
PESAN_BATAL_BACKUP = "Backup tidak bisa dibatalkan."
PESAN_BATAL_SESUDAH_TUKAR = "Aktivasi sudah mengubah VPS dan tidak bisa dibatalkan lagi."
PESAN_HAPUS_DILAYANI = "Site ini dihosting di VPS; lepas manual (README)."


def _iso(nilai):
    return nilai.isoformat() if nilai else None


def fitur_hosting() -> Settings:
    s = get_settings()
    if not s.hosting_aktif:
        raise HTTPException(status_code=404, detail="Fitur hosting VPS tidak aktif")
    return s


def site_atau_404(sesi, site_id: uuid.UUID) -> Site:
    site = sesi.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site tidak ditemukan")
    return site


def kunci_hosting(sesi, site_id: uuid.UUID) -> HostingVps | None:
    """Kontrak kunci: sites FOR NO KEY UPDATE lalu hosting_vps FOR UPDATE, sampai commit."""
    _kunci_site(sesi, site_id)
    return sesi.scalar(select(HostingVps).where(HostingVps.site_id == site_id).with_for_update()
                       .execution_options(populate_existing=True))


def _hosting_atau_409(sesi, site_id: uuid.UUID) -> HostingVps:
    h = kunci_hosting(sesi, site_id)
    if h is None:
        raise HTTPException(status_code=409, detail=PESAN_BELUM_ADA)
    return h


def job_aktif_hosting(sesi, site_id) -> Job | None:
    return sesi.scalar(select(Job).where(
        Job.site_id == site_id, Job.tipe.in_(JOB_HOSTING),
        Job.status.in_((JobStatus.pending, JobStatus.running))).order_by(Job.id.desc()).limit(1))


def tolak_bila_sibuk(sesi, site_id) -> None:
    if job_aktif_hosting(sesi, site_id) is not None:
        raise HTTPException(status_code=409, detail=PESAN_SIBUK)


def simpan_job_hosting(sesi, job: Job) -> Job:
    """Sisipkan job dalam satu commit (yang juga melepas kunci); `uq_jobs_hosting_aktif` penjaga terakhir."""
    sesi.add(job)
    try:
        sesi.commit()
    except IntegrityError:
        sesi.rollback()
        raise HTTPException(status_code=409, detail=PESAN_SIBUK_SIMPAN) from None
    return job


def dict_hosting(h: HostingVps, s: Settings) -> dict:
    hasil = {
        "nama": h.nama, "status": h.status.value, "status_teks": TEKS_STATUS[h.status.value], "domain": h.domain,
        "dengan_www": h.dengan_www, "url_pratinjau": hu.url_pratinjau(h), "pengguna": PENGGUNA_PRATINJAU,
        "versi_php": h.versi_php, "ukuran_file_teks": format_byte(h.ukuran_file),
        "ukuran_db_teks": format_byte(h.ukuran_db), "ditarik_pada": _iso(h.ditarik_pada),
        "pratinjau_sertifikat_pada": _iso(h.pratinjau_sertifikat_pada), "dns_dicek_pada": _iso(h.dns_dicek_pada),
        "sertifikat_pada": _iso(h.sertifikat_pada), "sertifikat_gagal_pada": _iso(h.sertifikat_gagal_pada),
        "dilayani_vps_pada": _iso(h.dilayani_vps_pada), "aktif_pada": _iso(h.aktif_pada),
        "backup_terakhir_pada": _iso(h.backup_terakhir_pada), "backup_gagal_pada": _iso(h.backup_gagal_pada),
        "galat": h.galat, "batal_diminta": h.batal_diminta_pada is not None,
        "dns_hasil": h.dns_hasil, "instruksi": dns_mod.instruksi(h, s.hosting_ipv4, s.hosting_ipv6),
        "coba_lagi_pada": _iso(dns_mod.coba_lagi_pada(h)),
    }
    # Asal hanya bermakna untuk status gagal.
    if h.status == StatusHosting.gagal:
        hasil["gagal_asal"] = h.gagal_asal
    return hasil


def daftar_backup(sesi, site_id) -> list[dict]:
    return [{"id": b.id, "stempel": b.stempel, "status": b.status, "manual": b.manual, "tujuan": b.tujuan,
             "ukuran_db_teks": format_byte(b.ukuran_db), "ukuran_file_teks": format_byte(b.ukuran_file),
             "dibuat_pada": _iso(b.dibuat_pada)}
            for b in sesi.scalars(select(HostingBackup).where(HostingBackup.site_id == site_id)
                                  .order_by(HostingBackup.stempel.desc(), HostingBackup.id.desc())
                                  .limit(BATAS_DAFTAR))]


def _nama_unik(sesi, dasar: str) -> str:
    """Nama situs bebas di hosting_vps dan `vps-<nama>` bukan nama staging mana pun (spec §10.7)."""
    kandidat = dasar
    for i in range(2, 100):
        bebas_hosting = sesi.scalar(select(HostingVps.id).where(HostingVps.nama == kandidat)) is None
        bebas_staging = sesi.scalar(select(Staging.id).where(Staging.nama == f"vps-{kandidat}")) is None
        if bebas_hosting and bebas_staging:
            return kandidat
        akhiran = f"-{i}"
        kandidat = dasar[:36 - len(akhiran)].rstrip("-") + akhiran
    raise HTTPException(status_code=409, detail="Nama situs untuk site ini tidak dapat ditentukan.")


# ---- status ------------------------------------------------------------------------------


@router.get("/api/sites/{site_id}/hosting")
def status_hosting(site_id: uuid.UUID, pengguna: PenggunaApi):
    s = get_settings()
    if not s.hosting_aktif:
        return {"aktif_fitur": False}
    with db.SessionLocal() as sesi:
        site = site_atau_404(sesi, site_id)
        h = sesi.scalar(select(HostingVps).where(HostingVps.site_id == site_id))
        job = job_aktif_hosting(sesi, site_id)
        return {
            "aktif_fitur": True,
            "izin_connector": punya_fitur(site, STAGING),
            "ipv4": s.hosting_ipv4, "ipv6": s.hosting_ipv6,
            "hosting": dict_hosting(h, s) if h is not None else None,
            "job": {"id": job.id, "tipe": job.tipe.value, "status": job.status.value,
                    "progres": ringkas_kemajuan(job)} if job is not None else None,
            "backup": daftar_backup(sesi, site_id),
        }


# ---- pindahkan, salin ulang, DNS -------------------------------------------------------------


@router.post("/api/sites/{site_id}/hosting")
def pindahkan(site_id: uuid.UUID, pengguna: PenggunaApi):
    s = fitur_hosting()
    with db.SessionLocal() as sesi:
        site = site_atau_404(sesi, site_id)
        if not punya_fitur(site, STAGING):
            raise HTTPException(status_code=409, detail=PESAN_IZIN)
        url = site.url
    host = host_dari_url(url) if url.startswith("https://") else None
    domain = host.removeprefix("www.") if host else None
    if not domain or not domain_sah(domain, s.staging_domain):
        raise HTTPException(status_code=409, detail=PESAN_DOMAIN)
    # DNS publik di luar kunci (<= 10 detik).
    ip_lama = dns_mod.ip_lama_dari_dns(domain)
    if ip_lama is None:
        raise HTTPException(status_code=409, detail=PESAN_IP_LAMA)
    dengan_www = host.startswith("www.") or dns_mod.ada_www(domain)
    with db.SessionLocal() as sesi:
        site = site_atau_404(sesi, site_id)
        if kunci_hosting(sesi, site_id) is not None:
            raise HTTPException(status_code=409, detail=PESAN_SUDAH_ADA)
        tolak_bila_sibuk(sesi, site_id)
        sandi = sandi_baru()
        h = HostingVps(site_id=site.id, nama=_nama_unik(sesi, nama_prod_dari_url(site.url)), domain=domain,
                       dengan_www=dengan_www, ip_lama=ip_lama, sandi_hash=hash_sandi(sandi),
                       status=StatusHosting.menyalin)
        sesi.add(h)
        try:
            sesi.flush()
        except IntegrityError:
            sesi.rollback()
            raise HTTPException(status_code=409, detail=PESAN_BENTROK) from None
        stg.catat_aktivitas(sesi, site.id, None, f"Pindah hosting ke VPS dimulai ({domain})", user_id=pengguna.id)
        job = simpan_job_hosting(sesi, Job(site_id=site.id, tipe=JobType.pindah_tarik, payload={},
                                           dibuat_oleh=pengguna.id))
        # Kata sandi pratinjau dibalas sekali; yang disimpan hanya hash bcrypt.
        return {"job_id": job.id, "sandi": sandi, "pengguna": PENGGUNA_PRATINJAU}


@router.post("/api/sites/{site_id}/hosting/tarik")
def salin_ulang(site_id: uuid.UUID, pengguna: PenggunaApi):
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site = site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        if h.dilayani_vps_pada is not None:
            raise HTTPException(status_code=409, detail=PESAN_SUDAH_DILAYANI)
        boleh = h.status in (StatusHosting.pratinjau, StatusHosting.menunggu_dns) or (
            h.status == StatusHosting.gagal and h.gagal_asal == hu.ASAL_SALINAN)
        if not boleh:
            raise HTTPException(status_code=409, detail=PESAN_STATUS_TARIK)
        if not punya_fitur(site, STAGING):
            raise HTTPException(status_code=409, detail=PESAN_IZIN)
        tolak_bila_sibuk(sesi, site_id)
        stg.catat_aktivitas(sesi, site_id, None, "Salin ulang ke VPS diminta", user_id=pengguna.id)
        job = simpan_job_hosting(sesi, Job(site_id=site_id, tipe=JobType.pindah_tarik, payload={},
                                           dibuat_oleh=pengguna.id))
        return {"job_id": job.id}


@router.post("/api/sites/{site_id}/hosting/lanjut-dns")
def lanjut_dns(site_id: uuid.UUID, pengguna: PenggunaApi):
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        if h.status != StatusHosting.pratinjau:
            raise HTTPException(status_code=409, detail=PESAN_LANJUT_DNS)
        tolak_bila_sibuk(sesi, site_id)
        h.status = StatusHosting.menunggu_dns
        h.galat = None
        stg.catat_aktivitas(sesi, site_id, None, "Pratinjau disetujui; menunggu DNS", user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


@router.post("/api/sites/{site_id}/hosting/kembali-pratinjau")
def kembali_pratinjau(site_id: uuid.UUID, pengguna: PenggunaApi):
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        if h.status != StatusHosting.menunggu_dns:
            raise HTTPException(status_code=409, detail=PESAN_KEMBALI)
        # Aktivasi tertunda yang belum berjalan akan menolak sendiri (status awal pratinjau).
        h.status = StatusHosting.pratinjau
        stg.catat_aktivitas(sesi, site_id, None, "Kembali ke pratinjau; aktivasi otomatis dihentikan",
                            user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


class PermintaanAktifkan(BaseModel):
    tanpa_tarik_ulang: bool = False
    konfirmasi: str = ""


@router.post("/api/sites/{site_id}/hosting/aktifkan")
def aktifkan(site_id: uuid.UUID, req: PermintaanAktifkan, pengguna: PenggunaApi):
    """Periksa DNS & aktifkan sekarang, juga Periksa ulang untuk gagal asal produksi (spec §11)."""
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = sesi.scalar(select(HostingVps).where(HostingVps.site_id == site_id))
        if h is None:
            raise HTTPException(status_code=409, detail=PESAN_BELUM_ADA)
        if h.status not in (StatusHosting.menunggu_dns, StatusHosting.gagal):
            raise HTTPException(status_code=409, detail=PESAN_STATUS_AKTIFKAN)
        if req.tanpa_tarik_ulang and req.konfirmasi != h.domain:
            raise HTTPException(status_code=400, detail=PESAN_KONFIRMASI_DOMAIN)
        dilayani = h.dilayani_vps_pada is not None
        if not dilayani and not dns_mod.backoff_mengizinkan(h, datetime.now(timezone.utc), manual=True):
            raise HTTPException(status_code=409, detail=PESAN_BACKOFF)
        sasaran = SimpleNamespace(domain=h.domain, dengan_www=h.dengan_www)
    # Cek DNS sinkron di luar kunci; "Periksa ulang" (sudah dilayani) tidak memerlukannya.
    hasil = None if dilayani else dns_mod.periksa_dns(sasaran)
    with db.SessionLocal() as sesi:
        h = _hosting_atau_409(sesi, site_id)
        if h.status not in (StatusHosting.menunggu_dns, StatusHosting.gagal) \
                or (h.dilayani_vps_pada is not None) != dilayani:
            raise HTTPException(status_code=409, detail=PESAN_STATUS_AKTIFKAN)
        tolak_bila_sibuk(sesi, site_id)
        if hasil is not None:
            h.dns_hasil = hasil.ke_json()
            h.dns_dicek_pada = datetime.now(timezone.utc)
            if not hasil.ok:
                sesi.commit()
                return JSONResponse(status_code=409, content={"detail": hasil.pesan, "dns_hasil": hasil.ke_json()})
        stg.catat_aktivitas(sesi, site_id, None, "Aktivasi hosting VPS diminta", user_id=pengguna.id)
        job = simpan_job_hosting(sesi, Job(site_id=site_id, tipe=JobType.pindah_aktifkan, dibuat_oleh=pengguna.id,
                                           payload={"tanpa_tarik_ulang": bool(req.tanpa_tarik_ulang), "manual": True}))
        return {"job_id": job.id, "dns_hasil": hasil.ke_json() if hasil is not None else None}


# ---- kata sandi, batal, batalkan pindah ------------------------------------------------------


@router.post("/api/sites/{site_id}/hosting/sandi")
def sandi_pratinjau_baru(site_id: uuid.UUID, pengguna: PenggunaApi):
    s = fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        if h.dilayani_vps_pada is not None:
            raise HTTPException(status_code=409, detail=PESAN_SANDI_DILAYANI)
        # Tarik menulis htpasswd dari hash yang dibacanya; hash baru bisa tertimpa.
        tolak_bila_sibuk(sesi, site_id)
        sandi = sandi_baru()
        hash_baru = hash_sandi(sandi)
        if h.ditarik_pada is not None:
            try:
                tulis_htpasswd_pratinjau(s.jalur_hosting, h.nama, hash_baru)
                stg.buat_pembantu().prod_router_muat()
            except GalatPembantu as exc:
                # Router berikutnya tidak boleh memasang sandi yang tidak pernah dilihat pengguna.
                if h.sandi_hash:
                    tulis_htpasswd_pratinjau(s.jalur_hosting, h.nama, h.sandi_hash)
                raise HTTPException(status_code=502, detail=exc.pesan) from None
        h.sandi_hash = hash_baru
        stg.catat_aktivitas(sesi, site_id, None, "Kata sandi pratinjau VPS dibuat ulang", user_id=pengguna.id)
        sesi.commit()
    return {"sandi": sandi, "pengguna": PENGGUNA_PRATINJAU}


@router.post("/api/sites/{site_id}/hosting/batal")
def batal(site_id: uuid.UUID, pengguna: PenggunaApi):
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        job = job_aktif_hosting(sesi, site_id)
        if job is None:
            raise HTTPException(status_code=409, detail=PESAN_TANPA_JOB)
        if job.tipe == JobType.backup_hosting:
            raise HTTPException(status_code=409, detail=PESAN_BATAL_BACKUP)
        if job.tipe == JobType.pindah_aktifkan and not pindah.boleh_batal_aktifkan(job):
            raise HTTPException(status_code=409, detail=PESAN_BATAL_SESUDAH_TUKAR)
        h.batal_diminta_pada = datetime.now(timezone.utc)
        stg.catat_aktivitas(sesi, site_id, None, "Pembatalan pekerjaan hosting diminta", user_id=pengguna.id)
        sesi.commit()
    return {"ok": True}


class PermintaanHapus(BaseModel):
    konfirmasi: str = ""


@router.delete("/api/sites/{site_id}/hosting")
def batalkan_pindah(site_id: uuid.UUID, req: PermintaanHapus, pengguna: PenggunaApi):
    s = fitur_hosting()
    akar = s.jalur_hosting
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        if req.konfirmasi != h.domain:
            raise HTTPException(status_code=400, detail=PESAN_KONFIRMASI_DOMAIN)
        if h.dilayani_vps_pada is not None:
            raise HTTPException(status_code=409, detail=PESAN_HAPUS_DILAYANI)
        tolak_bila_sibuk(sesi, site_id)
        nama, domain = h.nama, h.domain
        try:
            stg.buat_pembantu().prod_hapus(nama)
        except GalatPembantu as exc:
            raise HTTPException(status_code=502, detail=exc.pesan) from None
        hapus_htpasswd_pratinjau(akar, nama)
        sesi.execute(delete(HostingBackup).where(HostingBackup.site_id == site_id))
        sesi.delete(h)
        stg.catat_aktivitas(sesi, site_id, None, f"Pindah hosting dibatalkan ({domain})", user_id=pengguna.id)
        # Baris dihapus (commit) lebih dulu, berkas sesudahnya (pola hapus staging).
        sesi.commit()
        nisan = []
        try:
            # Transaksi kedua: kunci sites diambil lagi dan keadaan diperiksa ulang,
            # karena pindah baru bisa dimulai di sela dua transaksi.
            _kunci_site(sesi, site_id)
            nama_dir = str(site_id)
            if sesi.scalar(select(HostingVps.id).where(HostingVps.site_id == site_id)) is None \
                    and job_aktif_hosting(sesi, site_id) is None and _dir_nyata(akar / nama_dir):
                n = _ke_nisan(akar, nama_dir, site_id)
                if n:
                    nisan.append(n)
            sesi.commit()
        except Exception:
            sesi.rollback()
            log.exception("Berkas hosting site %s tidak dapat dipindah sesudah pembatalan", site_id)
    for n in nisan:
        _hapus_nisan(akar, n)
    return {"ok": True}
```

- [ ] **Step 6: Daftarkan router.** Di `src/wpmgr/web/app.py`, sesudah `from wpmgr.web.routes_staging import router as staging_router`, tambahkan `from wpmgr.web.routes_hosting import router as hosting_router`, dan sesudah `app.include_router(staging_router)` tambahkan `app.include_router(hosting_router)`.

- [ ] **Step 7: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/integration/test_api_hosting.py tests/integration/test_api_staging.py tests/integration/test_api.py -q`. Expected: semua lulus.

- [ ] **Step 8: Seluruh test unit dan integrasi, lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 9: Commit.**

```bash
git add src/wpmgr/web/routes_hosting.py src/wpmgr/web/app.py src/wpmgr/web/routes_staging.py src/wpmgr/staging/dorong.py tests/integration/test_api_hosting.py
git commit -m "feat(hosting): API hosting VPS, nama unik lintas staging, penjaga hapus site

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Cron `hosting-cek-dns` dan `renew-hosting-certs`

**Files:**
- Create: `src/wpmgr/hosting/cron.py`
- Modify: `src/wpmgr/kunci.py`, `src/wpmgr/cli.py`, `deploy/crontab`
- Test: `tests/integration/test_hosting_cron.py`, `tests/unit/test_deploy_staging.py` (tambah)

**Interfaces:**
- Consumes (Task 4–11): `HostingVps`, `StatusHosting`, `JOB_HOSTING`, `hosting.dns` (`periksa_dns`, `backoff_mengizinkan`, `Jawaban`, `buat_penanya`), `Pembantu.prod_sertifikat`, `staging.cron._kunci_site`, `staging.umum.catat_aktivitas`, `kunci.kunci_advisory`, fixture `site_hosting`, `hosting_aktif`, `staging_palsu.PembantuHostingPalsu`.
- Produces:
  - `hosting.cron.PESAN_SERTIFIKAT_GAGAL`, `hosting.cron.kunci_hosting(sesi, site_id) -> HostingVps | None` (kontrak kunci), `hosting.cron.ada_job_hosting(sesi, site_id) -> bool`, `hosting.cron.cek_dns_semua(sesi, sekarang, penanya=None) -> {"diperiksa", "diantrekan"}`, `hosting.cron.perpanjang_sertifikat_hosting(sesi, pb, sekarang) -> {"berhasil", "gagal", "diperbarui"}`.
  - `kunci.KUNCI_HOSTING_DNS = 72_140_009`, `kunci.KUNCI_HOSTING_SERTIFIKAT = 72_140_010`.
  - CLI: `cli.hosting_cek_dns() -> dict | None`, `cli.renew_hosting_certs() -> dict | None`, `cli._hosting_mati() -> bool`; subperintah `hosting-cek-dns`, `renew-hosting-certs`.
  - `deploy/crontab`: blok Lapis 4 di akhir berkas, diawali `CRON_TZ=Asia/Jakarta`.

- [ ] **Step 1: Tulis test yang gagal.**

Tambahkan ke akhir `tests/unit/test_deploy_staging.py`:

```python
def test_crontab_cron_tz_hanya_untuk_baris_hosting():
    """CRON_TZ berlaku untuk semua baris sesudahnya: blok hosting wajib di akhir berkas (spec §15, A15)."""
    baris = [b for b in (AKAR / "deploy" / "crontab").read_text(encoding="utf-8").splitlines()
             if b.strip() and not b.startswith("#")]
    tz = [i for i, b in enumerate(baris) if b.startswith("CRON_TZ=")]
    assert len(tz) == 1 and baris[tz[0]] == "CRON_TZ=Asia/Jakarta"
    sebelum, sesudah = baris[:tz[0]], baris[tz[0] + 1:]
    assert not any("hosting" in b for b in sebelum)
    assert sesudah and all("wpmgr.cli hosting-" in b or "wpmgr.cli renew-hosting-certs" in b
                           or "wpmgr.cli backup-hosting" in b for b in sesudah)
    assert any(b.startswith("*/10 * * * *") and "hosting-cek-dns" in b for b in sesudah)
    assert any(b.startswith("50   3 * * *") and "renew-hosting-certs" in b for b in sesudah)
```

File: `tests/integration/test_hosting_cron.py`
```python
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker
from staging_palsu import PembantuHostingPalsu

from wpmgr.hosting import cron
from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting.dns import Jawaban
from wpmgr.jobs.queue import buat_job
from wpmgr.models import ActivityLog, HostingVps, Job, JobType, StatusHosting
from wpmgr.staging.pembantu import GalatPembantu

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)
VPS = "169.58.91.181"


class PenanyaPalsu:
    def __init__(self, a=VPS) -> None:
        self.a = a
        self.n = 0

    def tanya(self, resolver, nama, jenis, batas):
        self.n += 1
        return Jawaban((self.a,)) if jenis == "A" else Jawaban()


def _status(sesi, h, status, **lain):
    h.status = status
    for k, v in lain.items():
        setattr(h, k, v)
    sesi.commit()


def _h(sesi, h):
    return sesi.get(HostingVps, h.id, populate_existing=True)


def test_cek_dns_mengantrekan_aktivasi_sekali(sesi, site_hosting):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    hasil = cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu())
    assert hasil == {"diperiksa": 1, "diantrekan": 1}
    job = sesi.query(Job).one()
    assert job.tipe == JobType.pindah_aktifkan and job.payload == {"tanpa_tarik_ulang": False, "manual": False}
    h = _h(sesi, site_hosting)
    assert h.dns_hasil["ok"] is True and h.dns_dicek_pada == SEKARANG
    assert sesi.query(ActivityLog).filter(ActivityLog.site_id == h.site_id).one().pesan == \
        "DNS sudah menunjuk VPS; aktivasi diantrekan otomatis"
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu()) == {"diperiksa": 1, "diantrekan": 0}
    assert sesi.query(Job).count() == 1


def test_cek_dns_belum_lolos_hanya_menyimpan_hasil(sesi, site_hosting):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu(a="93.184.216.34"))["diantrekan"] == 0
    assert _h(sesi, site_hosting).dns_hasil["ok"] is False
    assert sesi.query(Job).count() == 0


def test_cek_dns_menghormati_backoff_sertifikat(sesi, site_hosting):
    _status(sesi, site_hosting, StatusHosting.menunggu_dns, sertifikat_gagal_kali=1,
            sertifikat_gagal_pada=SEKARANG - timedelta(minutes=30))
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu())["diantrekan"] == 0
    _status(sesi, site_hosting, StatusHosting.menunggu_dns, sertifikat_gagal_pada=SEKARANG - timedelta(hours=1))
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu())["diantrekan"] == 1


def test_cek_dns_hanya_baris_menunggu_dns_dan_tanpa_job_hosting(sesi, site_hosting):
    _status(sesi, site_hosting, StatusHosting.pratinjau)
    p = PenanyaPalsu()
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=p) == {"diperiksa": 0, "diantrekan": 0}
    assert p.n == 0
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    buat_job(sesi, site_hosting.site_id, JobType.pindah_tarik)
    assert cron.cek_dns_semua(sesi, SEKARANG, penanya=PenanyaPalsu())["diantrekan"] == 0
    assert sesi.query(Job).count() == 1


def test_cek_dns_memakai_penanya_bawaan(sesi, site_hosting, monkeypatch):
    p = PenanyaPalsu()
    monkeypatch.setattr(dns_mod, "buat_penanya", lambda: p)
    _status(sesi, site_hosting, StatusHosting.menunggu_dns)
    cron.cek_dns_semua(sesi, SEKARANG)
    assert p.n > 0


def test_perpanjang_sertifikat_hanya_situs_yang_dilayani(sesi, site_hosting, hosting_aktif):
    pb = PembantuHostingPalsu(hosting_aktif)
    assert cron.perpanjang_sertifikat_hosting(sesi, pb, SEKARANG) == {"berhasil": 0, "gagal": 0, "diperbarui": 0}
    _status(sesi, site_hosting, StatusHosting.aktif, dilayani_vps_pada=SEKARANG - timedelta(days=60),
            sertifikat_pada=SEKARANG - timedelta(days=60))
    pb.sertifikat_hasil = "tetap"
    assert cron.perpanjang_sertifikat_hosting(sesi, pb, SEKARANG) == {"berhasil": 1, "gagal": 0, "diperbarui": 0}
    assert _h(sesi, site_hosting).sertifikat_pada == SEKARANG - timedelta(days=60)
    pb.sertifikat_hasil = "diperbarui"
    assert cron.perpanjang_sertifikat_hosting(sesi, pb, SEKARANG)["diperbarui"] == 1
    assert _h(sesi, site_hosting).sertifikat_pada == SEKARANG
    assert ("prod_sertifikat", "toko-co-id") in pb.panggilan


def test_perpanjang_sertifikat_gagal_dicatat_dengan_pesan_tetap(sesi, site_hosting, hosting_aktif):
    _status(sesi, site_hosting, StatusHosting.aktif, dilayani_vps_pada=SEKARANG)
    pb = PembantuHostingPalsu(hosting_aktif)
    pb.gagal["prod_sertifikat"] = GalatPembantu("sertifikat", "x /var/lib/wpmgr rahasia")
    assert cron.perpanjang_sertifikat_hosting(sesi, pb, SEKARANG)["gagal"] == 1
    log = sesi.query(ActivityLog).filter(ActivityLog.site_id == site_hosting.site_id).one()
    assert (log.pesan, log.level) == (cron.PESAN_SERTIFIKAT_GAGAL, "warning")


# ---- CLI -------------------------------------------------------------------------------


@pytest.fixture
def cli(engine, monkeypatch, hosting_aktif):
    from wpmgr import cli as modul
    from wpmgr import db

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    monkeypatch.setattr(db, "engine", engine)
    pb = PembantuHostingPalsu(hosting_aktif)
    monkeypatch.setattr(modul.Pembantu, "dari_setelan", classmethod(lambda cls, s=None: pb))
    monkeypatch.setattr(dns_mod, "buat_penanya", lambda: PenanyaPalsu())
    return modul


def test_cli_hosting_dilewati_bila_kunci_dipegang(cli, engine):
    from wpmgr.kunci import KUNCI_HOSTING_DNS, KUNCI_HOSTING_SERTIFIKAT, kunci_advisory

    with kunci_advisory(engine, KUNCI_HOSTING_DNS) as a, kunci_advisory(engine, KUNCI_HOSTING_SERTIFIKAT) as b:
        assert a and b
        assert cli.hosting_cek_dns() is None
        assert cli.renew_hosting_certs() is None


def test_cli_hosting_dilewati_bila_fitur_mati(cli, monkeypatch):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_HOSTING_IPV4", raising=False)
    get_settings.cache_clear()
    assert cli.hosting_cek_dns() is None
    assert cli.renew_hosting_certs() is None


def test_cli_main_mengenal_subperintah_hosting(cli, sesi, site_hosting):
    for perintah in ("hosting-cek-dns", "renew-hosting-certs"):
        assert cli.main([perintah]) == 0
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_deploy_staging.py tests/integration/test_hosting_cron.py -q`. Expected: `ImportError: cannot import name 'cron' from 'wpmgr.hosting'` dan `test_crontab_cron_tz_hanya_untuk_baris_hosting` gagal (`assert 0 == 1`).

- [ ] **Step 3: Implementasikan.**

File: `src/wpmgr/hosting/cron.py`
```python
"""Perintah cron hosting VPS (spec Lapis 4 §15).

Kontrak kunci sama dengan route hosting: baris `sites` FOR NO KEY UPDATE lalu
`hosting_vps` FOR UPDATE, dipegang sampai commit. Panggilan jaringan (cek
DNS) dan skrip pembantu selalu di luar kunci; keadaan diperiksa ulang sesudah
kunci diambil. Teks galat hanya ke log server; log aktivitas memakai pesan tetap.
"""

import logging
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from wpmgr.hosting import dns as dns_mod
from wpmgr.models import JOB_HOSTING, HostingVps, Job, JobStatus, JobType, StatusHosting
from wpmgr.staging import umum as stg
from wpmgr.staging.cron import _kunci_site
from wpmgr.staging.pembantu import GalatPembantu

log = logging.getLogger("wpmgr.hosting.cron")
PESAN_SERTIFIKAT_GAGAL = "Sertifikat domain belum dapat diperpanjang; lihat log server."


def kunci_hosting(sesi, site_id) -> HostingVps | None:
    _kunci_site(sesi, site_id)
    return sesi.scalar(select(HostingVps).where(HostingVps.site_id == site_id).with_for_update()
                       .execution_options(populate_existing=True))


def ada_job_hosting(sesi, site_id) -> bool:
    return sesi.scalar(select(Job.id).where(
        Job.site_id == site_id, Job.tipe.in_(JOB_HOSTING),
        Job.status.in_((JobStatus.pending, JobStatus.running))).limit(1)) is not None


def cek_dns_semua(sesi, sekarang: datetime, penanya=None) -> dict:
    """Setiap baris `menunggu_dns`: cek DNS; bila lolos, backoff mengizinkan, dan tidak ada
    job hosting aktif, antrekan `pindah_aktifkan` (spec §15)."""
    hasil = {"diperiksa": 0, "diantrekan": 0}
    ids = sesi.scalars(select(HostingVps.id).where(HostingVps.status == StatusHosting.menunggu_dns)
                       .order_by(HostingVps.nama)).all()
    sesi.commit()
    for hid in ids:
        h = sesi.get(HostingVps, hid, populate_existing=True)
        if h is None or h.status != StatusHosting.menunggu_dns:
            continue
        site_id = h.site_id
        sasaran = SimpleNamespace(domain=h.domain, dengan_www=h.dengan_www)
        sesi.commit()
        cek = dns_mod.periksa_dns(sasaran, penanya=penanya, sekarang=sekarang)
        hasil["diperiksa"] += 1
        h = kunci_hosting(sesi, site_id)
        if h is None or h.status != StatusHosting.menunggu_dns:
            sesi.commit()
            continue
        h.dns_hasil = cek.ke_json()
        h.dns_dicek_pada = sekarang
        if cek.ok and dns_mod.backoff_mengizinkan(h, sekarang, manual=False) and not ada_job_hosting(sesi, site_id):
            stg.catat_aktivitas(sesi, site_id, None, "DNS sudah menunjuk VPS; aktivasi diantrekan otomatis")
            sesi.add(Job(site_id=site_id, tipe=JobType.pindah_aktifkan,
                         payload={"tanpa_tarik_ulang": False, "manual": False}))
            try:
                sesi.commit()
                hasil["diantrekan"] += 1
            except IntegrityError:
                # uq_jobs_hosting_aktif: job hosting lain baru saja diantrekan.
                sesi.rollback()
        else:
            sesi.commit()
    return hasil


def perpanjang_sertifikat_hosting(sesi, pb, sekarang: datetime) -> dict:
    """prod-sertifikat untuk setiap situs yang sudah dilayani VPS; skrip me-reload nginx hanya bila berubah."""
    hasil = {"berhasil": 0, "gagal": 0, "diperbarui": 0}
    ids = sesi.scalars(select(HostingVps.id).where(HostingVps.dilayani_vps_pada.is_not(None))
                       .order_by(HostingVps.nama)).all()
    for hid in ids:
        h = sesi.get(HostingVps, hid, populate_existing=True)
        if h is None:
            continue
        nama, site_id = h.nama, h.site_id
        sesi.commit()
        try:
            keluaran = pb.prod_sertifikat(nama)
        except (GalatPembantu, ValueError) as exc:
            hasil["gagal"] += 1
            log.warning("Sertifikat domain %s gagal diperpanjang: %s", nama, getattr(exc, "kode", "argumen"))
            stg.catat_aktivitas(sesi, site_id, None, PESAN_SERTIFIKAT_GAGAL, level="warning")
            sesi.commit()
            continue
        hasil["berhasil"] += 1
        if keluaran in ("terbit", "diperbarui"):
            hasil["diperbarui"] += 1
            h = sesi.get(HostingVps, hid, populate_existing=True)
            if h is not None:
                h.sertifikat_pada = sekarang
                sesi.commit()
    return hasil
```

Di `src/wpmgr/kunci.py`, sesudah `KUNCI_STAGING_PANGKAS`, tambahkan:

```python
KUNCI_HOSTING_DNS = 72_140_009
KUNCI_HOSTING_SERTIFIKAT = 72_140_010
```

Di `src/wpmgr/cli.py`:
- tambahkan `KUNCI_HOSTING_DNS` dan `KUNCI_HOSTING_SERTIFIKAT` ke impor `wpmgr.kunci`, dan `from wpmgr.hosting.cron import cek_dns_semua, perpanjang_sertifikat_hosting`;
- sesudah `prune_staging()`, tambahkan:

```python
def _hosting_mati() -> bool:
    if not get_settings().hosting_aktif:
        print("Hosting VPS tidak aktif (WPMGR_HOSTING_IPV4 atau WPMGR_STAGING_DOMAIN kosong); dilewati")
        return True
    return False


def hosting_cek_dns() -> dict | None:
    if _hosting_mati():
        return None
    with kunci_advisory(db.engine, KUNCI_HOSTING_DNS) as dapat:
        if not dapat:
            print("Cek DNS hosting lain masih berjalan; dilewati")
            return None
        with get_session() as sesi:
            hasil = cek_dns_semua(sesi, datetime.now(timezone.utc))
    print(f"Cek DNS hosting: {hasil['diperiksa']} diperiksa, {hasil['diantrekan']} aktivasi diantrekan")
    return hasil


def renew_hosting_certs() -> dict | None:
    if _hosting_mati():
        return None
    with kunci_advisory(db.engine, KUNCI_HOSTING_SERTIFIKAT) as dapat:
        if not dapat:
            print("Perpanjangan sertifikat hosting lain masih berjalan; dilewati")
            return None
        with get_session() as sesi:
            hasil = perpanjang_sertifikat_hosting(sesi, Pembantu.dari_setelan(), datetime.now(timezone.utc))
    print(f"Sertifikat hosting: {hasil['berhasil']} berhasil ({hasil['diperbarui']} diperbarui), "
          f"{hasil['gagal']} gagal")
    return hasil
```

- di `main()`, tambahkan `sub.add_parser("hosting-cek-dns")` dan `sub.add_parser("renew-hosting-certs")` sesudah `sub.add_parser("prune-staging")`, serta dua cabang sesudah cabang `prune-staging`:

```python
    elif args.perintah == "hosting-cek-dns":
        hosting_cek_dns()
    elif args.perintah == "renew-hosting-certs":
        renew_hosting_certs()
```

Tambahkan ke akhir `deploy/crontab`:

```
# Lapis 4 -- hosting VPS. CRON_TZ berlaku untuk SEMUA baris sesudahnya; zona
# waktu sistem VPS Europe/Berlin, jadi jadwal di bawah ini WIB (spec §15, A15).
# Blok ini wajib tetap di akhir berkas.
CRON_TZ=Asia/Jakarta
*/10 * * * *  cd /opt/wpmgr && .venv/bin/python -m wpmgr.cli hosting-cek-dns       >> /var/log/wpmgr/cron.log 2>&1
50   3 * * *  cd /opt/wpmgr && .venv/bin/python -m wpmgr.cli renew-hosting-certs   >> /var/log/wpmgr/cron.log 2>&1
```

- [ ] **Step 4: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/unit/test_deploy_staging.py tests/integration/test_hosting_cron.py tests/integration/test_staging_cli.py -q`. Expected: semua lulus.

- [ ] **Step 5: Seluruh test unit dan integrasi, lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 6: Commit.**

```bash
git add src/wpmgr/hosting/cron.py src/wpmgr/kunci.py src/wpmgr/cli.py deploy/crontab tests/integration/test_hosting_cron.py tests/unit/test_deploy_staging.py
git commit -m "feat(hosting): cron cek DNS dengan aktivasi otomatis dan perpanjangan sertifikat domain

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Fase D — UI, backup, dokumentasi, e2e

### Task 13: Tab Hosting VPS dan chip Kesehatan

**Files:**
- Create: `src/wpmgr/templates/_tab_hosting.html`, `src/wpmgr/static/app/hosting.js`
- Modify: `src/wpmgr/web/routes_pages.py`, `src/wpmgr/templates/site_detail.html`, `src/wpmgr/static/app/detail.js`, `src/wpmgr/kesehatan.py`, `src/wpmgr/static/app/kesehatan.js`
- Test: `tests/unit/test_kesehatan_hosting.py`, `tests/integration/test_hosting_halaman.py`

**Interfaces:**
- Consumes (Task 4, 11): `HostingVps`, `StatusHosting`, `Settings.hosting_aktif`, API `/api/sites/{id}/hosting*` (bentuk GET dari `routes_hosting.dict_hosting`/`daftar_backup`), `staging.umum.ASAL_PRODUKSI`; JS `pesanGalat` (`util.js`), Alpine.
- Produces:
  - `kesehatan.JENDELA_BACKUP = timedelta(hours=36)`, `kesehatan.masalah_hosting(h, sekarang) -> list[str]`; chip baru `hosting_gagal` (tingkat 1), `pindah_gagal` (2), `backup_gagal` (2), semua ke tab `hosting`; baris Kesehatan mendapat `hosting_status`.
  - `routes_pages.TAB_DETAIL` memuat `("hosting", "Hosting VPS")` sesudah Staging (hanya bila `hosting_aktif`); konteks template `hosting_aktif`; lencana tab `hosting` (`!` bila gagal atau `backup_gagal`).
  - Komponen Alpine `tabHosting(siteId)` dengan metode `muat`, `kirim`, `aksi(nama, sukses)`, `pindahkan`, `salinUlang`, `sandiBaru`, `aktifkan(tanpaTarik)`, `aktifkanTanpaSalin`, `batalkanPindah`, `barisHosts`, `teksAksi`, `bolehBatal`, `teksJob`, `aman`, `waktu`, `salin`; elemen `<div id="hosting-backup">` (diisi tombol di Task 14).

- [ ] **Step 1: Tulis test yang gagal.**

File: `tests/unit/test_kesehatan_hosting.py`
```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wpmgr.kesehatan import TAB_MASALAH, TINGKAT_MASALAH, URUTAN_CHIP, masalah_hosting
from wpmgr.models import StatusHosting

S = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def _h(**k):
    dasar = dict(status=StatusHosting.pratinjau, gagal_asal=None, dilayani_vps_pada=None, aktif_pada=None,
                 backup_terakhir_pada=None, backup_gagal_pada=None)
    dasar.update(k)
    return SimpleNamespace(**dasar)


def test_chip_terdaftar():
    for chip, tingkat in (("hosting_gagal", 1), ("pindah_gagal", 2), ("backup_gagal", 2)):
        assert chip in URUTAN_CHIP and TINGKAT_MASALAH[chip] == tingkat and TAB_MASALAH[chip] == "hosting"
    assert URUTAN_CHIP.index("hosting_gagal") < URUTAN_CHIP.index("diserang")


@pytest.mark.parametrize("h,masalah", [
    (None, []),
    (_h(), []),
    (_h(status=StatusHosting.gagal, gagal_asal="salinan"), ["pindah_gagal"]),
    (_h(status=StatusHosting.gagal, gagal_asal=None), ["pindah_gagal"]),
    (_h(status=StatusHosting.gagal, gagal_asal="produksi", dilayani_vps_pada=S - timedelta(hours=1),
        aktif_pada=None), ["hosting_gagal"]),
    (_h(status=StatusHosting.aktif, dilayani_vps_pada=S - timedelta(days=3), aktif_pada=S - timedelta(days=3),
        backup_terakhir_pada=S - timedelta(hours=30)), []),
    (_h(status=StatusHosting.aktif, dilayani_vps_pada=S - timedelta(days=3), aktif_pada=S - timedelta(days=3),
        backup_terakhir_pada=S - timedelta(hours=37)), ["backup_gagal"]),
    (_h(status=StatusHosting.aktif, dilayani_vps_pada=S - timedelta(days=3), aktif_pada=S - timedelta(hours=2)),
     []),
    (_h(status=StatusHosting.aktif, dilayani_vps_pada=S - timedelta(days=3), aktif_pada=S - timedelta(hours=40)),
     ["backup_gagal"]),
    (_h(status=StatusHosting.aktif, dilayani_vps_pada=S, aktif_pada=S, backup_terakhir_pada=S,
        backup_gagal_pada=S), ["backup_gagal"]),
    (_h(status=StatusHosting.pratinjau, backup_gagal_pada=S), []),
])
def test_masalah_hosting(h, masalah):
    assert masalah_hosting(h, S) == masalah
```

File: `tests/integration/test_hosting_halaman.py`
```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wpmgr.kesehatan import susun_kesehatan
from wpmgr.models import StatusHosting

pytestmark = pytest.mark.integration

AKAR = Path(__file__).resolve().parents[2] / "src" / "wpmgr"


def test_tab_hosting_tampil_bila_fitur_aktif(klien_web, sesi, site, hosting_aktif):
    site.nama = '<script>alert("x")</script>'
    sesi.commit()
    r = klien_web.get(f"/sites/{site.id}?tab=hosting")
    assert r.status_code == 200
    assert "tabHosting(" in r.text and "', 'hosting')" in r.text
    assert "/static/app/hosting.js" in r.text
    assert "Hosting VPS" in r.text
    assert "<script>alert" not in r.text
    # Fixture hosting_aktif juga menyalakan staging: tab Hosting VPS tepat sesudah Staging.
    assert r.text.index("Staging") < r.text.index("Hosting VPS")


def test_tab_hosting_tersembunyi_bila_fitur_mati(klien_web, site, monkeypatch, staging_aktif):
    from wpmgr.config import get_settings

    monkeypatch.delenv("WPMGR_HOSTING_IPV4", raising=False)
    get_settings.cache_clear()
    r = klien_web.get(f"/sites/{site.id}?tab=hosting")
    assert "tabHosting(" not in r.text and "/static/app/hosting.js" not in r.text
    assert "', 'ringkasan')" in r.text


def test_lencana_tab_hosting(klien_web, sesi, site_hosting):
    site_hosting.status = StatusHosting.gagal
    site_hosting.gagal_asal = "salinan"
    sesi.commit()
    r = klien_web.get(f"/sites/{site_hosting.site_id}")
    awal = r.text.index("Hosting VPS")
    assert '<span class="lencana">!</span>' in r.text[awal:awal + 120]


def test_kesehatan_memuat_chip_dan_status_hosting(sesi, site_hosting):
    sekarang = datetime.now(timezone.utc)
    site_hosting.status = StatusHosting.aktif
    site_hosting.dilayani_vps_pada = sekarang - timedelta(days=3)
    site_hosting.aktif_pada = sekarang - timedelta(days=3)
    sesi.commit()
    hasil = susun_kesehatan(sesi, sekarang)
    baris = next(b for b in hasil["baris"] if b["id"] == str(site_hosting.site_id))
    assert baris["hosting_status"] == "aktif"
    assert "backup_gagal" in baris["masalah"] and baris["tab"] == "hosting"
    assert hasil["chip"]["backup_gagal"] == 1


def test_script_hosting_memakai_x_text_dan_tanpa_innerhtml():
    js = (AKAR / "static" / "app" / "hosting.js").read_text(encoding="utf-8")
    tpl = (AKAR / "templates" / "_tab_hosting.html").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "x-html" not in tpl and "|safe" not in tpl
    for kata in ("Pindahkan ke VPS", "Pratinjau sudah benar, lanjut ke DNS", "Periksa DNS &amp; aktifkan sekarang",
                 "Kembali ke pratinjau", "Salin ulang", "Buat ulang kata sandi", "Batalkan pindah", "Periksa ulang",
                 "jangan ubah DNS", "hosting lama boleh dimatikan", "Turunkan TTL"):
        assert kata in tpl, kata
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_kesehatan_hosting.py tests/integration/test_hosting_halaman.py -q`. Expected: `ImportError: cannot import name 'masalah_hosting' from 'wpmgr.kesehatan'`.

- [ ] **Step 3: Chip Kesehatan.** Di `src/wpmgr/kesehatan.py`:

Tambahkan `HostingVps` ke impor `wpmgr.models`. Ganti tiga konstanta chip:

```python
URUTAN_CHIP = [
    "mati", "perlu_diperiksa", "dorong_gagal", "hosting_gagal", "diserang", "error_baru", "ssl", "koneksi",
    "penangkap_terbatas", "staging_gagal", "pindah_gagal", "backup_gagal", "traffic_anjlok", "traffic_melonjak",
    "connector_usang",
]
TINGKAT_MASALAH = {
    "mati": 1, "perlu_diperiksa": 1, "dorong_gagal": 1, "hosting_gagal": 1,
    "diserang": 2, "error_baru": 2, "ssl": 2, "koneksi": 2, "penangkap_terbatas": 2, "staging_gagal": 2,
    "pindah_gagal": 2, "backup_gagal": 2,
    "traffic_anjlok": 3, "traffic_melonjak": 3, "connector_usang": 3,
}
TAB_MASALAH = {
    "mati": "uptime", "perlu_diperiksa": "login", "dorong_gagal": "staging", "hosting_gagal": "hosting",
    "diserang": "login", "error_baru": "error", "ssl": "uptime", "koneksi": "ringkasan",
    "penangkap_terbatas": "ringkasan", "staging_gagal": "staging", "pindah_gagal": "hosting",
    "backup_gagal": "hosting", "traffic_anjlok": "traffic", "traffic_melonjak": "traffic",
    "connector_usang": "ringkasan",
}
# Backup harian berjalan 02:30 WIB; 36 jam memberi satu hari kelonggaran.
JENDELA_BACKUP = timedelta(hours=36)
```

Sesudah `masalah_staging`, tambahkan:

```python
def masalah_hosting(h: HostingVps | None, sekarang: datetime) -> list[str]:
    """Chip hosting VPS satu site (spec Lapis 4 §13).

    - `hosting_gagal`: situs sudah dilayani VPS tetapi aktivasi gagal final
      (asal 'produksi'): pengunjung mungkin melihat situs rusak.
    - `pindah_gagal`: salinan atau aktivasi gagal sebelum dilayani VPS; site
      lama masih produksi.
    - `backup_gagal`: situs dilayani VPS dan backup terakhir gagal, atau backup
      sukses terakhir (atau aktivasi, bila belum pernah) lebih dari 36 jam lalu.
    """
    if h is None:
        return []
    masalah = []
    if h.status == StatusHosting.gagal:
        masalah.append("hosting_gagal" if h.gagal_asal == ASAL_PRODUKSI else "pindah_gagal")
    if h.dilayani_vps_pada is not None:
        acuan = h.backup_terakhir_pada or h.aktif_pada
        if h.backup_gagal_pada is not None or (acuan is not None and sekarang - acuan > JENDELA_BACKUP):
            masalah.append("backup_gagal")
    return masalah
```

Tambahkan `StatusHosting` ke impor `wpmgr.models`. Di `susun_kesehatan`, sesudah blok `stagings = {...}`, tambahkan:

```python
    hostings = {}
    if site_ids and get_settings().hosting_aktif:
        hostings = {h.site_id: h for h in sesi.scalars(select(HostingVps).where(HostingVps.site_id.in_(site_ids)))}
```

sesudah `masalah.extend(masalah_staging(st))`, tambahkan:

```python
        hv = hostings.get(site.id)
        masalah.extend(masalah_hosting(hv, sekarang))
```

dan di kamus baris, sesudah `"staging_status": ...`, tambahkan `"hosting_status": hv.status.value if hv is not None else None,`.

Di `src/wpmgr/static/app/kesehatan.js`, ganti tiga konstanta pertama:

```js
const URUTAN_CHIP = [
  'mati', 'perlu_diperiksa', 'dorong_gagal', 'hosting_gagal', 'diserang', 'error_baru', 'ssl', 'koneksi',
  'penangkap_terbatas', 'staging_gagal', 'pindah_gagal', 'backup_gagal', 'traffic_anjlok', 'traffic_melonjak',
  'connector_usang',
];
const LABEL_CHIP = {
  mati: 'mati', perlu_diperiksa: 'perlu diperiksa', dorong_gagal: 'dorong ke produksi gagal',
  hosting_gagal: 'situs di VPS bermasalah', diserang: 'diserang', error_baru: 'error baru', ssl: 'SSL bermasalah',
  koneksi: 'koneksi bermasalah', penangkap_terbatas: 'penangkap terbatas', staging_gagal: 'staging gagal',
  pindah_gagal: 'pindah hosting gagal', backup_gagal: 'backup gagal', traffic_anjlok: 'traffic anjlok',
  traffic_melonjak: 'traffic melonjak', connector_usang: 'connector usang',
};
const TINGKAT_CHIP = {
  mati: 1, perlu_diperiksa: 1, dorong_gagal: 1, hosting_gagal: 1, diserang: 2, error_baru: 2, ssl: 2, koneksi: 2,
  penangkap_terbatas: 2, staging_gagal: 2, pindah_gagal: 2, backup_gagal: 2, traffic_anjlok: 3,
  traffic_melonjak: 3, connector_usang: 3,
};
```

- [ ] **Step 4: Halaman detail.** Di `src/wpmgr/web/routes_pages.py`:
- tambahkan `HostingVps`, `StatusHosting` ke impor `wpmgr.models` dan `from wpmgr.kesehatan import masalah_hosting` (bila `kesehatan` sudah diimpor, tambahkan namanya saja);
- `TAB_DETAIL` menjadi:

```python
TAB_DETAIL = [
    ("ringkasan", "Ringkasan"), ("paket", "Paket"), ("uptime", "Uptime"),
    ("error", "Error"), ("login", "Login"), ("traffic", "Traffic"), ("staging", "Staging"),
    ("hosting", "Hosting VPS"), ("aktivitas", "Aktivitas"),
]
```

- di `halaman_detail`, ganti dua baris pertama dengan:

```python
    staging_aktif = get_settings().staging_aktif
    hosting_aktif = get_settings().hosting_aktif
    tab_detail = [t for t in TAB_DETAIL
                  if (t[0] != "staging" or staging_aktif) and (t[0] != "hosting" or hosting_aktif)]
```

- sesudah baris `staging = sesi.scalar(...)`, tambahkan:

```python
        hosting = sesi.scalar(select(HostingVps).where(HostingVps.site_id == site_id)) if hosting_aktif else None
```

- di kamus `lencana`, tambahkan entri:

```python
        "hosting": "!" if hosting is not None and (
            hosting.status == StatusHosting.gagal or "backup_gagal" in masalah_hosting(hosting, sekarang)) else "",
```

- di konteks `TemplateResponse`, tambahkan `"hosting_aktif": hosting_aktif,`.

Di `src/wpmgr/templates/site_detail.html`, sesudah baris `{% if staging_aktif %}{% include "_tab_staging.html" %}{% endif %}`, tambahkan:

```html
  {% if hosting_aktif %}{% include "_tab_hosting.html" %}{% endif %}
```

dan sesudah baris script staging, tambahkan:

```html
{% if hosting_aktif %}<script src="/static/app/hosting.js"></script>{% endif %}
```

Di `src/wpmgr/static/app/detail.js`, ganti `TAB_SAH` dan baris pemulihan tab tersimpan:

```js
const TAB_SAH = ['ringkasan', 'paket', 'uptime', 'error', 'login', 'traffic', 'staging', 'hosting', 'aktivitas'];
```

```js
          // Tab Staging/Hosting hanya ada bila fiturnya menyala (script-nya dimuat).
          if (TAB_SAH.includes(t) && (t !== 'staging' || typeof tabStaging === 'function')
              && (t !== 'hosting' || typeof tabHosting === 'function')) this.tab = t;
```

- [ ] **Step 5: Template tab.**

File: `src/wpmgr/templates/_tab_hosting.html`
```html
{# Tab Hosting VPS (spec Lapis 4 §12). Semua nilai dari site, DNS, dan job
   dirender lewat x-text; hanya site.id (UUID buatan server) yang masuk ke
   ekspresi Alpine. #}
<section x-show="tab === 'hosting'">
  <div x-data="tabHosting('{{ site.id }}')" x-init="mulai(tab)">
    <p class="galat" x-show="galat" x-text="galat" role="alert"></p>
    <p class="info" x-show="info" x-text="info" role="status"></p>
    <p class="redup" x-show="!data && !galat">Memuat data hosting…</p>

    <template x-if="sandi">
      <div class="panel">
        <p><strong>Kata sandi pratinjau</strong> (hanya ditampilkan sekali, simpan sekarang): pengguna
           <code>pratinjau</code>, kata sandi <code x-text="sandi"></code>
           <button type="button" @click="salin(sandi)">Salin</button>
           <button type="button" @click="sandi = ''">Tutup</button></p>
      </div>
    </template>

    <template x-if="data && data.job">
      <div class="panel">
        <p><strong x-text="teksJob(data.job.tipe)"></strong>
           <span x-show="data.job.status === 'pending'" class="redup">(menunggu giliran worker)</span>
           — <span x-text="data.job.progres.teks"></span></p>
        <div class="batang-progres" x-show="data.job.progres.persen !== null">
          <div :style="`width:${data.job.progres.persen || 0}%`"></div>
        </div>
        <button type="button" x-show="bolehBatal()" @click="aksi('batal', 'Pembatalan diminta; job berhenti di antara potongan.')"
                :disabled="data.hosting && data.hosting.batal_diminta"
                x-text="data.hosting && data.hosting.batal_diminta ? 'Pembatalan diminta…' : 'Batal'"></button>
      </div>
    </template>

    <template x-if="data && !data.hosting">
      <div>
        <p>Pindahkan site ini dari hosting lama ke VPS dashboard. Site lama tidak pernah diubah; sampai DNS
           diubah, pengunjung tetap dilayani hosting lama.</p>
        <p class="redup">Syarat: connector 3.0 terpasang di site lama, pairing sukses, dan <em>Izinkan staging</em>
           menyala di <em>Pengaturan → WP Manager</em>.</p>
        <p class="galat" x-show="!data.izin_connector">Connector site ini belum mengizinkan staging.</p>
        <button type="button" @click="pindahkan()" :disabled="!data.izin_connector || !!data.job">Pindahkan ke VPS</button>
      </div>
    </template>

    <template x-if="data && data.hosting">
      <div>
        <p>Status: <strong x-text="data.hosting.status_teks"></strong> · domain <code x-text="data.hosting.domain"></code>
           <span class="redup" x-show="data.hosting.ditarik_pada">· disalin <span x-text="waktu(data.hosting.ditarik_pada)"></span>
           (berkas <span x-text="data.hosting.ukuran_file_teks"></span>, database <span x-text="data.hosting.ukuran_db_teks"></span>,
           PHP <span x-text="data.hosting.versi_php || '—'"></span>)</span></p>
        <p class="galat" x-show="data.hosting.galat" x-text="data.hosting.galat"></p>

        <div x-show="data.hosting.status === 'pratinjau'">
          <p>Pratinjau: <a :href="aman(data.hosting.url_pratinjau)" target="_blank" rel="noopener noreferrer"
             x-text="data.hosting.url_pratinjau"></a> · pengguna <code>pratinjau</code> ·
             <span x-text="data.hosting.pratinjau_sertifikat_pada ? 'HTTPS aktif' : 'sertifikat pratinjau belum terbit (salin ulang untuk mencoba lagi)'"></span></p>
          <p class="redup">Email diblokir, mesin pencari ditolak, dan WP-Cron mati selama pratinjau. Cara kedua: tambahkan
             baris ini ke berkas hosts komputer Anda lalu buka domain asli; browser akan memperingatkan sertifikat
             (sertifikat pratinjau), lanjutkan saja. Hapus barisnya sesudah selesai.</p>
          <pre class="pesan-detail" x-text="barisHosts()"></pre>
          <div class="toolbar">
            <button type="button" @click="aksi('lanjut-dns', 'Menunggu DNS; dashboard memeriksa tiap 10 menit.')"
                    :disabled="!!data.job">Pratinjau sudah benar, lanjut ke DNS</button>
            <button type="button" @click="salinUlang()" :disabled="!!data.job">Salin ulang</button>
            <button type="button" @click="sandiBaru()" :disabled="!!data.job">Buat ulang kata sandi</button>
            <button type="button" @click="batalkanPindah()" :disabled="!!data.job">Batalkan pindah</button>
          </div>
        </div>

        <div x-show="data.hosting.status === 'menunggu_dns'">
          <p>Ubah record DNS domain di panel DNS (mis. hPanel) seperti tabel berikut. Dashboard memeriksa DNS tiap
             10 menit dan mengaktifkan situs otomatis begitu DNS menunjuk VPS.</p>
          <table>
            <tr><th>Jenis</th><th>Nama</th><th>Tindakan</th><th>Nilai</th><th>Cek terakhir</th></tr>
            <template x-for="(r, i) in data.hosting.instruksi" :key="i">
              <tr><td x-text="r.jenis"></td><td x-text="r.nama"></td><td x-text="teksAksi(r)"></td>
                  <td><code x-text="r.nilai || '—'"></code></td><td x-text="r.ok ? 'sudah benar' : 'belum'"></td></tr>
            </template>
          </table>
          <p class="redup">Record AAAA lama wajib dihapus (atau diarahkan ke IPv6 VPS bila tabel memintanya):
             Let's Encrypt mendahulukan IPv6, dan pengunjung IPv6 akan tetap ke hosting lama. Bila www berupa CNAME
             ke CDN hosting lama, matikan CDN itu lalu ganti CNAME dengan A.</p>
          <p class="redup">Turunkan TTL record ke 300 detik sehari sebelumnya bila memungkinkan.</p>
          <p>Cek terakhir: <span x-text="waktu(data.hosting.dns_dicek_pada)"></span> —
             <span x-text="teksDns()"></span></p>
          <p class="redup" x-show="data.hosting.coba_lagi_pada">Sertifikat domain gagal terakhir kali; aktivasi
             otomatis menunggu sampai <span x-text="waktu(data.hosting.coba_lagi_pada)"></span>.</p>
          <div class="toolbar">
            <button type="button" @click="aktifkan(false)" :disabled="!!data.job">Periksa DNS &amp; aktifkan sekarang</button>
            <button type="button" @click="aksi('kembali-pratinjau', 'Kembali ke pratinjau; aktivasi otomatis dihentikan.')">Kembali ke pratinjau</button>
            <button type="button" @click="aktifkanTanpaSalin()" :disabled="!!data.job">Aktifkan tanpa salin ulang</button>
          </div>
          <p class="redup">Aktifkan tanpa salin ulang hanya bila hosting lama sudah tidak terjangkau: data sejak salinan
             terakhir (<span x-text="waktu(data.hosting.ditarik_pada)"></span>) tidak ikut.</p>
        </div>

        <div class="panel peringatan" x-show="data.hosting.status === 'mengaktifkan'" role="alert">
          <strong>Sedang mengaktifkan situs di VPS</strong> — jangan ubah DNS sampai selesai.
        </div>

        <div x-show="data.hosting.status === 'aktif'">
          <p>Dihosting di VPS sejak <strong x-text="waktu(data.hosting.aktif_pada)"></strong> · sertifikat domain
             <span x-text="waktu(data.hosting.sertifikat_pada)"></span></p>
          <p class="redup">Situs sudah dilayani VPS: hosting lama boleh dimatikan. Sebelumnya, periksa entri form atau
             komentar yang mungkin masuk ke hosting lama sejak salinan terakhir. Mengembalikan DNS ke hosting lama
             berarti data baru di VPS tidak ikut kembali.</p>
          <p class="redup">Pemulihan backup dilakukan manual; lihat README, bagian "Pindah hosting (Lapis 4)",
             "Pemulihan backup manual".</p>
          <h3>Backup</h3>
          <div id="hosting-backup"></div>
          <p class="galat" x-show="data.hosting.backup_gagal_pada">Backup terakhir gagal
             (<span x-text="waktu(data.hosting.backup_gagal_pada)"></span>).</p>
          <p class="redup" x-show="data.backup.length === 0">Belum ada backup.</p>
          <table x-show="data.backup.length">
            <tr><th>Waktu</th><th>Database</th><th>Berkas</th><th>Jenis</th><th>Status</th></tr>
            <template x-for="b in data.backup" :key="b.id">
              <tr><td x-text="waktu(b.dibuat_pada)"></td><td x-text="b.ukuran_db_teks"></td>
                  <td x-text="b.ukuran_file_teks"></td><td x-text="b.manual ? 'manual' : 'harian'"></td>
                  <td x-text="b.status"></td></tr>
            </template>
          </table>
        </div>

        <div x-show="data.hosting.status === 'gagal'">
          <div class="panel peringatan" x-show="data.hosting.gagal_asal === 'produksi'" role="alert">
            <strong>Situs sudah dilayani VPS tetapi pemeriksaan akhir gagal.</strong> Periksa situs; bila rusak,
            arahkan DNS kembali ke hosting lama (masih utuh).
          </div>
          <div class="toolbar">
            <button type="button" x-show="data.hosting.gagal_asal !== 'produksi'" @click="salinUlang()"
                    :disabled="!!data.job">Salin ulang</button>
            <button type="button" x-show="data.hosting.gagal_asal !== 'produksi'" @click="aktifkan(false)"
                    :disabled="!!data.job">Periksa DNS &amp; aktifkan sekarang</button>
            <button type="button" x-show="data.hosting.gagal_asal === 'produksi'" @click="aktifkan(false)"
                    :disabled="!!data.job">Periksa ulang</button>
            <button type="button" x-show="!data.hosting.dilayani_vps_pada" @click="batalkanPindah()"
                    :disabled="!!data.job">Batalkan pindah</button>
          </div>
        </div>
      </div>
    </template>
  </div>
</section>
```

- [ ] **Step 6: Script tab.**

File: `src/wpmgr/static/app/hosting.js`
```js
const TEKS_JOB_HOSTING = {
  pindah_tarik: 'Menyalin ke VPS', pindah_aktifkan: 'Mengaktifkan di VPS', backup_hosting: 'Backup situs',
};
// Langkah aktivasi sesudah prod-aktifkan dikirim: batal tidak berlaku lagi
// (cermin LANGKAH_AKTIFKAN_SESUDAH_TUKAR di wpmgr.jobs.queue).
const LANGKAH_SESUDAH_TUKAR = ['tukar', 'verifikasi', 'beres'];
const JEDA_POLLING_HOSTING = 3000;
const JEDA_POLLING_DNS = 30000;
const STATUS_BERHENTI_HOSTING = [401, 404];
const MAKS_GAGAL_HOSTING = 5;

function tabHosting(siteId) {
  return {
    siteId,
    tabAktif: false,
    data: null,
    galat: '',
    info: '',
    // Kata sandi pratinjau hanya hidup di state komponen ini sampai ditutup.
    sandi: '',
    _timer: null,
    _memuat: false,
    _gagalBeruntun: 0,
    _galatMuat: '',

    mulai(tab) {
      this.tabAktif = tab === 'hosting';
      this.$watch('tab', (t) => {
        this.tabAktif = t === 'hosting';
        this.sinkron();
      });
      document.addEventListener('visibilitychange', () => this.sinkron());
      this.sinkron();
    },

    terlihat() { return this.tabAktif && document.visibilityState === 'visible'; },

    sinkron() {
      if (this.terlihat()) {
        this.muat();
      } else {
        clearTimeout(this._timer);
        this._timer = null;
      }
    },

    dasar() { return `/api/sites/${this.siteId}/hosting`; },

    jedaPolling() {
      if (!this.data) return null;
      if (this.data.job) return JEDA_POLLING_HOSTING;
      const status = this.data.hosting ? this.data.hosting.status : null;
      if (status === 'menyalin' || status === 'mengaktifkan') return JEDA_POLLING_HOSTING;
      if (status === 'menunggu_dns') return JEDA_POLLING_DNS;
      return null;
    },

    async muat() {
      if (this._memuat) return;
      this._memuat = true;
      clearTimeout(this._timer);
      this._timer = null;
      let lanjut = true;
      try {
        const r = await fetch(this.dasar());
        if (!r.ok) {
          lanjut = !STATUS_BERHENTI_HOSTING.includes(r.status);
          throw new Error(await pesanGalat(r));
        }
        this.data = await r.json();
        this._gagalBeruntun = 0;
        if (this._galatMuat && this.galat === this._galatMuat) this.galat = '';
        this._galatMuat = '';
      } catch (e) {
        this._gagalBeruntun += 1;
        if (this._gagalBeruntun >= MAKS_GAGAL_HOSTING) lanjut = false;
        this.galat = `Data hosting tidak dapat dimuat. ${e.message}`
          + (lanjut ? '' : ' Pembaruan otomatis dihentikan; muat ulang halaman untuk mencoba lagi.');
        this._galatMuat = this.galat;
      } finally {
        this._memuat = false;
      }
      const jeda = this.jedaPolling();
      if (lanjut && jeda && this.terlihat()) this._timer = setTimeout(() => this.muat(), jeda);
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

    mulaiAksi() {
      this.galat = '';
      this.info = '';
    },

    async aksi(nama, sukses) {
      this.mulaiAksi();
      try {
        await this.kirim('POST', `${this.dasar()}/${nama}`);
        this.info = sukses;
      } catch (e) {
        this.galat = e.message;
      }
      this.muat();
    },

    async pindahkan() {
      if (!window.confirm('Salin seluruh berkas dan database site ini ke VPS? Site lama tidak diubah.')) return;
      this.mulaiAksi();
      try {
        const d = await this.kirim('POST', this.dasar(), {});
        this.sandi = d.sandi;
        this.info = 'Penyalinan ke VPS diantrekan.';
      } catch (e) {
        this.galat = `Pindah hosting tidak dapat dimulai. ${e.message}`;
      }
      this.muat();
    },

    async salinUlang() {
      if (!window.confirm('Salin ulang dari hosting lama? Perubahan di salinan VPS akan tertimpa.')) return;
      await this.aksi('tarik', 'Salin ulang diantrekan.');
    },

    async sandiBaru() {
      if (!window.confirm('Kata sandi pratinjau lama langsung tidak berlaku. Lanjutkan?')) return;
      this.mulaiAksi();
      try {
        this.sandi = (await this.kirim('POST', `${this.dasar()}/sandi`)).sandi;
      } catch (e) {
        this.galat = `Kata sandi tidak dapat dibuat ulang. ${e.message}`;
      }
    },

    async aktifkan(tanpaTarik, konfirmasi) {
      this.mulaiAksi();
      try {
        await this.kirim('POST', `${this.dasar()}/aktifkan`,
          { tanpa_tarik_ulang: !!tanpaTarik, konfirmasi: konfirmasi || '' });
        this.info = 'Aktivasi diantrekan; jangan ubah DNS sampai selesai.';
      } catch (e) {
        this.galat = `Belum bisa diaktifkan. ${e.message}`;
      }
      this.muat();
    },

    async aktifkanTanpaSalin() {
      const domain = window.prompt('Data sejak salinan terakhir tidak ikut. Ketik domain untuk konfirmasi:');
      if (domain === null) return;
      await this.aktifkan(true, domain);
    },

    async batalkanPindah() {
      const domain = window.prompt('Container, database, dan salinan di VPS akan dihapus (site lama tidak '
        + 'disentuh). Ketik domain untuk konfirmasi:');
      if (domain === null) return;
      this.mulaiAksi();
      try {
        await this.kirim('DELETE', this.dasar(), { konfirmasi: domain });
        this.info = 'Pindah hosting dibatalkan.';
      } catch (e) {
        this.galat = `Pindah hosting tidak dapat dibatalkan. ${e.message}`;
      }
      this.muat();
    },

    barisHosts() {
      if (!this.data || !this.data.hosting) return '';
      const h = this.data.hosting;
      return `${this.data.ipv4} ${h.domain}${h.dengan_www ? ` www.${h.domain}` : ''}`;
    },

    teksAksi(r) {
      if (r.aksi === 'hapus') return r.nilai ? 'Hapus record ini' : 'Pastikan tidak ada';
      return r.cname ? 'Hapus CNAME, lalu buat A' : 'Ubah atau buat';
    },

    teksDns() {
      const hasil = this.data && this.data.hosting ? this.data.hosting.dns_hasil : null;
      if (!hasil) return 'belum pernah diperiksa';
      return hasil.ok ? 'DNS sudah menunjuk VPS' : 'belum menunjuk VPS sepenuhnya';
    },

    bolehBatal() {
      const job = this.data ? this.data.job : null;
      if (!job || job.tipe === 'backup_hosting') return false;
      return !(job.tipe === 'pindah_aktifkan' && LANGKAH_SESUDAH_TUKAR.includes(job.progres.tahap));
    },

    salin(teks) {
      if (navigator.clipboard) navigator.clipboard.writeText(teks);
    },

    aman(url) { return typeof url === 'string' && url.startsWith('https://') ? url : '#'; },
    teksJob(tipe) { return TEKS_JOB_HOSTING[tipe] || tipe; },
    waktu(iso) { return iso ? new Date(iso).toLocaleString('id-ID') : '—'; },
  };
}
```

- [ ] **Step 7: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/unit/test_kesehatan_hosting.py tests/unit/test_template_aman.py tests/integration/test_hosting_halaman.py tests/integration/test_staging_halaman.py tests/integration/test_kesehatan.py tests/integration/test_pages.py -q`. Expected: semua lulus.

- [ ] **Step 8: Periksa di browser.** Jalankan dashboard lokal (README "Menjalankan web dan worker") dengan `WPMGR_STAGING_DOMAIN` dan `WPMGR_HOSTING_IPV4` terisi, buka `/sites/<id>?tab=hosting`, dan pastikan panel "Belum ada" tampil tanpa galat di konsol browser. Expected: tombol **Pindahkan ke VPS** tampil; konsol tanpa galat JavaScript.

- [ ] **Step 9: Seluruh test unit dan integrasi, lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 10: Commit.**

```bash
git add src/wpmgr/templates/_tab_hosting.html src/wpmgr/static/app/hosting.js src/wpmgr/web/routes_pages.py src/wpmgr/templates/site_detail.html src/wpmgr/static/app/detail.js src/wpmgr/kesehatan.py src/wpmgr/static/app/kesehatan.js tests/unit/test_kesehatan_hosting.py tests/integration/test_hosting_halaman.py
git commit -m "feat(hosting): tab Hosting VPS dan chip Kesehatan hosting

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Backup — `prod-backup`, `TujuanBackup`, retensi, job `backup_hosting`, cron, API, dan tombol UI

**Files:**
- Create: `src/wpmgr/hosting/backup.py`
- Modify: `deploy/staging/wpmgr-staging`, `deploy/staging/tests/palsu/docker`, `deploy/staging/tests/pembantu.bats` (tambah di akhir), `src/wpmgr/jobs/handlers.py`, `src/wpmgr/hosting/cron.py`, `src/wpmgr/kunci.py`, `src/wpmgr/cli.py`, `deploy/crontab`, `src/wpmgr/web/routes_hosting.py`, `src/wpmgr/templates/_tab_hosting.html`, `src/wpmgr/static/app/hosting.js`
- Test: `tests/unit/test_hosting_backup.py`, `tests/integration/test_hosting_backup.py`

**Interfaces:**
- Consumes (Task 1–13): bash `muat_state`, `S_*`, `cek_stempel`, `cek_mount_root`, `cek_mount_pengguna ... "$HOSTING_DIR"`, `sbg_pengguna`, `opsi_klien ... wpmgr-prod-db`, `hapus_opsi_klien`, `nama_db_prod`, `BACKUP_DIR`, helper bats `siap_aktifkan`; Python `Pembantu.prod_backup`/`prod_backup_hapus`/`prod_status`, `StatusProd`, `GalatPembantu`, `aman.POLA_SHA256`, `rencana.SISA_DISK_MINIMUM`, `rencana.format_byte`, `hosting.umum.jalankan_hosting`/`sekarang`, `hosting.cron.kunci_hosting`/`ada_job_hosting`, `routes_hosting` (`fitur_hosting`, `site_atau_404`, `_hosting_atau_409`, `tolak_bila_sibuk`, `simpan_job_hosting`, `daftar_backup`), fixture `site_hosting`, `hosting_aktif`, `klien_web`, `staging_palsu.PembantuHostingPalsu`.
- Produces:
  - Subperintah `prod-backup <nama> <stempel>` (stdout: isi `manifest.json` satu baris; idempoten per stempel) dan `prod-backup-hapus <nama> <stempel>` (tidak ada = sukses; symlink/bukan milik root = ditolak). Bash: `WAKTU_BACKUP=11100`, `POLA_MANIFEST_BACKUP`, `manifest_backup_sah <berkas>`.
  - `hosting.backup`: `POLA_STEMPEL`, `BATAS_MANIFEST = 4096`, `STATUS_TERSEDIA = "tersedia"`, `STATUS_DIPANGKAS = "dipangkas"`, pesan `PESAN_MANIFEST`, `PESAN_BELUM_DILAYANI`, `PESAN_TUJUAN`; `HasilBackup(ukuran_db, ukuran_file, sha256_db, sha256_file)`, `TujuanBackup` (Protocol: `kode`, `buat(hosting, stempel) -> HasilBackup`, `hapus(hosting, stempel) -> None`), `TujuanLokal(pb)`, `TUJUAN = {"lokal": TujuanLokal}`, `tujuan_dari_setelan(pb) -> TujuanBackup`, `stempel_dari(waktu) -> str`, `urai_manifest_backup(teks, hosting, stempel) -> HasilBackup`, `cek_disk_backup(status, tambahan) -> str | None`, `pilih_simpan(backups, harian=7, mingguan=4) -> set`, `pangkas(sesi, h, tujuan) -> int`, `backup_hosting(sesi, job, site, h, pb) -> dict`, `tangani_backup_hosting(sesi, job, klien) -> dict`.
  - `handlers.HANDLER[JobType.backup_hosting] = tangani_backup_hosting`.
  - `hosting.cron.antrekan_backup_harian(sesi, sekarang) -> {"diantrekan", "dilewati"}`, `hosting.cron.antrekan_backup_pertama(sesi) -> int` (Koreksi #2).
  - `kunci.KUNCI_HOSTING_BACKUP = 72_140_011`; CLI `cli.backup_hosting() -> dict | None`, subperintah `backup-hosting`; `cli.hosting_cek_dns()` juga mengantrekan backup pertama.
  - Route `GET /api/sites/{id}/hosting/backup` (`{"backup": [...]}`), `POST /api/sites/{id}/hosting/backup` (`{"job_id"}`; payload job `{"manual": true}`); pesan `routes_hosting.PESAN_BACKUP_BELUM`.
  - UI: tombol **Backup sekarang** dan metode `tabHosting.backupSekarang()`.

- [ ] **Step 1: Docker tiruan menjawab `mariadb-dump`.** Di `deploy/staging/tests/palsu/docker`, di cabang `exec)`, tepat sesudah blok `exec-lama`, tambahkan:

```bash
    if [[ "$*" == *mariadb-dump* ]]; then
      echo "-- dump tiruan"
    fi
```

- [ ] **Step 2: Tulis test bats yang gagal.** Tambahkan ke akhir `deploy/staging/tests/pembantu.bats`:

```bash

STEMPEL=20261003T023000Z

@test "prod-backup menjalankan tar sebagai UID dashboard" {
  siap_aktifkan
  printf 'isi' > "$S/hosting/$ID/files/index.php"
  run "$SKRIP" prod-backup toko "$STEMPEL"
  [ "$status" -eq 0 ]
  d="$S/backup/$ID/$STEMPEL"
  grep -qxF "[--reuid=1000][--regid=1000][--clear-groups][--][tar][-C][$S/hosting/$ID][--numeric-owner][-czf][-][files]" "$PALSU/setpriv.log"
  grep -q '^\[exec\]\[wpmgr-prod-db\]\[mariadb-dump\]\[--defaults-extra-file=/run/wpmgr-klien-[0-9-]*\.cnf\]\[--single-transaction\]\[--quick\]\[--hex-blob\]\[--no-tablespaces\]\[--default-character-set=utf8mb4\]\[prd_toko\]$' "$PALSU/docker.log"
  ! grep -q 'prodrahasia' "$PALSU/docker.log" || false
  [ "$(stat -c %a "$d")" = 700 ]
  for f in db.sql.gz files.tar.gz manifest.json; do
    [ "$(stat -c %a:%u "$d/$f")" = "600:0" ]
  done
  [ "$output" = "$(cat "$d/manifest.json")" ]
  [[ "$output" == '{"versi":1,"site_id":"'"$ID"'","nama":"toko","domain":"toko.co.id","stempel":"'"$STEMPEL"'","versi_php":"8.1","prefix":"wp_",'* ]]
  [[ "$output" == *"\"sha256_db\":\"$(sha256sum "$d/db.sql.gz" | cut -d' ' -f1)\""* ]]
  [[ "$output" == *"\"sha256_file\":\"$(sha256sum "$d/files.tar.gz" | cut -d' ' -f1)\""* ]]
  [[ "$output" == *"\"ukuran_file\":$(stat -c %s "$d/files.tar.gz"),"* ]]
  [ "$(gzip -dc "$d/db.sql.gz")" = "-- dump tiruan" ]
  tar -tzf "$d/files.tar.gz" | grep -qx 'files/index.php'
  [ ! -e "$S/backup/$ID/.$STEMPEL.tmp" ]
}

@test "prod-backup idempoten per stempel dan menolak direktori stempel yang rusak" {
  siap_aktifkan
  run "$SKRIP" prod-backup toko "$STEMPEL"
  [ "$status" -eq 0 ]
  pertama="$output"
  : > "$PALSU/docker.log"
  : > "$PALSU/setpriv.log"
  run "$SKRIP" prod-backup toko "$STEMPEL"
  [ "$status" -eq 0 ]
  [ "$output" = "$pertama" ]
  ! grep -q 'mariadb-dump' "$PALSU/docker.log" || false
  ! grep -q '\[tar\]' "$PALSU/setpriv.log" || false
  printf 'rusak' > "$S/backup/$ID/$STEMPEL/manifest.json"
  run "$SKRIP" prod-backup toko "$STEMPEL"
  [ "$status" -eq 11 ]
}

@test "prod-backup menolak stempel atau nama tidak sah dan situs tanpa database" {
  siap_aktifkan
  for s in 2026-10-03 "$STEMPEL"x "../$STEMPEL" "$(printf '%s\nx' "$STEMPEL")"; do
    run "$SKRIP" prod-backup toko "$s"
    [ "$status" -eq 2 ]
  done
  run "$SKRIP" prod-backup Toko "$STEMPEL"
  [ "$status" -eq 2 ]
  sed -i 's/^PREFIX=.*/PREFIX=/' "$S/etc/prod/situs/toko"
  run "$SKRIP" prod-backup toko "$STEMPEL"
  [ "$status" -eq 3 ]
  [ -z "$(ls -A "$S/backup")" ]
}

@test "prod-backup-hapus menolak symlink" {
  siap_aktifkan
  mkdir -p "$S/backup/$ID" "$S/lain-backup"
  printf 'JANGAN' > "$S/lain-backup/penting"
  ln -s "$S/lain-backup" "$S/backup/$ID/$STEMPEL"
  run "$SKRIP" prod-backup-hapus toko "$STEMPEL"
  [ "$status" -eq 3 ]
  [ "$(cat "$S/lain-backup/penting")" = "JANGAN" ]
  rm -f "$S/backup/$ID/$STEMPEL"
  run "$SKRIP" prod-backup-hapus toko "$STEMPEL"
  [ "$status" -eq 0 ]
  run "$SKRIP" prod-backup toko "$STEMPEL"
  [ -d "$S/backup/$ID/$STEMPEL" ]
  run "$SKRIP" prod-backup-hapus toko "$STEMPEL"
  [ "$status" -eq 0 ]
  [ ! -e "$S/backup/$ID/$STEMPEL" ]
}
```

- [ ] **Step 3: Jalankan dan pastikan gagal.** Run: perintah bats. Expected: 89 test sebelumnya `ok`; 4 test baru `not ok` (`GALAT argumen: subperintah tidak dikenal`).

- [ ] **Step 4: Implementasikan subperintah backup.** Di `deploy/staging/wpmgr-staging`:

Sesudah `WAKTU_AKTIFKAN=360 ...`, tambahkan:

```bash
WAKTU_BACKUP=11100     # TIMEOUT_BACKUP 3 jam + 300
```

Di bagian produksi, sesudah `cmd_prod_hapus()`, tambahkan:

```bash
# ---- backup (spec §9.2) ------------------------------------------------------------

# Satu baris JSON buatan prod-backup sendiri; dipakai untuk mengenali backup
# yang sudah selesai pada percobaan ulang dengan stempel yang sama.
POLA_MANIFEST_BACKUP='^\{"versi":1,"site_id":"[0-9a-f-]{36}","nama":"[a-z0-9-]{1,36}","domain":"[a-z0-9.-]{1,253}","stempel":"[0-9]{8}T[0-9]{6}Z","versi_php":"[0-9.]{0,4}","prefix":"[A-Za-z0-9_]{1,20}","ukuran_db":[0-9]{1,20},"ukuran_file":[0-9]{1,20},"sha256_db":"[0-9a-f]{64}","sha256_file":"[0-9a-f]{64}"\}$'

manifest_backup_sah() {
  local isi
  [[ -f "$1" && ! -L "$1" ]] || return 1
  isi="$(head -c 4096 -- "$1")"
  [[ "$isi" =~ $POLA_MANIFEST_BACKUP ]]
}

# BACKUP_DIR/<site_id>/<stempel>/ milik root 0700: db.sql.gz (mariadb-dump
# sebagai root di wpmgr-prod-db, di-gzip di host), files.tar.gz (tar SEBAGAI
# user dashboard: tidak mengikuti symlink dan tidak membaca apa pun di luar
# hak user itu, termasuk saat direktori ditukar di tengah jalan; root hanya
# menerima aliran byte, RF7), dan manifest.json. Ditulis ke .<stempel>.tmp
# lalu rename; container dan dashboard tidak pernah memasang BACKUP_DIR.
cmd_prod_backup() {
  cek_nama_prod "${1-}"
  cek_stempel "${2-}"
  butuh_hosting
  local nama="$1" stempel="$2" akar dasar sementara db sandi sha_db sha_file ukuran_db ukuran_file
  muat_state "$nama" || galat ditolak "situs belum dibuat (prod-buat)"
  [[ -n "$S_PREFIX" ]] || galat ditolak "database situs belum dibuat"
  cek_mount_root "$BACKUP_DIR" d "$(dirname -- "$BACKUP_DIR")"
  akar="$BACKUP_DIR/$S_SITE_ID"
  install -d -m 0700 "$akar"
  cek_mount_root "$akar" d "$BACKUP_DIR"
  dasar="$akar/$stempel"
  if [[ -e "$dasar" || -L "$dasar" ]]; then
    # Idempoten untuk percobaan ulang: backup yang sudah selesai dicetak lagi.
    [[ -d "$dasar" && ! -L "$dasar" ]] || galat backup "backup dengan stempel ini tidak sah"
    manifest_backup_sah "$dasar/manifest.json" || galat backup "backup dengan stempel ini rusak"
    cat "$dasar/manifest.json"
    return 0
  fi
  sementara="$akar/.$stempel.tmp"
  rm -rf -- "$sementara"
  install -d -m 0700 "$sementara"
  SEMENTARA+=("$sementara")
  db="$(nama_db_prod "$nama")"
  sandi="$(cat "$KONF_PROD/db-root")" || galat konfigurasi "kata sandi root database hosting tidak ada"
  opsi_klien root "$sandi" wpmgr-prod-db
  dk exec wpmgr-prod-db mariadb-dump --defaults-extra-file="$OPSI_KLIEN" --single-transaction --quick \
    --hex-blob --no-tablespaces --default-character-set=utf8mb4 "$db" | gzip -c > "$sementara/db.sql.gz" \
    || galat backup "dump database situs gagal"
  hapus_opsi_klien
  cek_mount_pengguna "$HOSTING_DIR/$S_SITE_ID/files" "$HOSTING_DIR"
  sbg_pengguna tar -C "$HOSTING_DIR/$S_SITE_ID" --numeric-owner -czf - files > "$sementara/files.tar.gz" \
    || galat backup "arsip berkas situs gagal"
  sha_db="$(sha256sum "$sementara/db.sql.gz" | cut -d' ' -f1)"
  sha_file="$(sha256sum "$sementara/files.tar.gz" | cut -d' ' -f1)"
  ukuran_db="$(stat -c %s "$sementara/db.sql.gz")"
  ukuran_file="$(stat -c %s "$sementara/files.tar.gz")"
  printf '{"versi":1,"site_id":"%s","nama":"%s","domain":"%s","stempel":"%s","versi_php":"%s","prefix":"%s","ukuran_db":%s,"ukuran_file":%s,"sha256_db":"%s","sha256_file":"%s"}\n' \
    "$S_SITE_ID" "$nama" "$S_DOMAIN" "$stempel" "$S_PHP" "$S_PREFIX" "$ukuran_db" "$ukuran_file" "$sha_db" \
    "$sha_file" > "$sementara/manifest.json"
  chmod 0600 "$sementara/db.sql.gz" "$sementara/files.tar.gz" "$sementara/manifest.json"
  mv -T "$sementara" "$dasar"
  cat "$dasar/manifest.json"
}

cmd_prod_backup_hapus() {
  cek_nama_prod "${1-}"
  cek_stempel "${2-}"
  butuh_hosting
  muat_state "$1" || galat ditolak "situs belum dibuat (prod-buat)"
  local akar="$BACKUP_DIR/$S_SITE_ID" dasar="$BACKUP_DIR/$S_SITE_ID/$2"
  [[ -e "$dasar" || -L "$dasar" ]] || return 0
  cek_mount_root "$BACKUP_DIR" d "$(dirname -- "$BACKUP_DIR")"
  cek_mount_root "$akar" d "$BACKUP_DIR"
  [[ -d "$dasar" && ! -L "$dasar" ]] || galat ditolak "backup bukan direktori asli"
  [[ "$(stat -c %u -- "$dasar")" == 0 ]] || galat ditolak "backup bukan milik root"
  rm -rf -- "$dasar"
}
```

Di `utama()`: tambahkan pola `prod-backup|prod-backup-hapus)` dengan `[[ $# -eq 2 ]] || galat argumen "jumlah argumen salah" ;;` di blok jumlah argumen (sebelum `wpcli)`), `prod-backup) atur_tenggat "$WAKTU_BACKUP" ;;` di blok tenggat, dan dua baris dispatch:

```bash
    prod-backup) cmd_prod_backup "$@" ;;
    prod-backup-hapus) cmd_prod_backup_hapus "$@" ;;
```

- [ ] **Step 5: Jalankan bats dan shellcheck.** Expected: `93 tests, 0 failures`; shellcheck tanpa temuan tingkat error.

- [ ] **Step 6: Tulis test Python yang gagal.**

File: `tests/unit/test_hosting_backup.py`
```python
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wpmgr.errors import SiteError
from wpmgr.hosting.backup import (
    HasilBackup,
    TujuanLokal,
    cek_disk_backup,
    pilih_simpan,
    stempel_dari,
    tujuan_dari_setelan,
    urai_manifest_backup,
)
from wpmgr.staging.pembantu import GalatPembantu, StatusProd

SID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
H = SimpleNamespace(site_id=SID, nama="toko-co-id")
ST = "20261003T023000Z"


def _b(id_, waktu):
    return SimpleNamespace(id=id_, dibuat_pada=waktu)


def _hari(tahun, bulan, tanggal, jam=2):
    return datetime(tahun, bulan, tanggal, jam, 30, tzinfo=timezone.utc)


def _manifest(**ganti):
    data = {"versi": 1, "site_id": str(SID), "nama": "toko-co-id", "domain": "toko.co.id", "stempel": ST,
            "versi_php": "8.1", "prefix": "wp_", "ukuran_db": 100, "ukuran_file": 200,
            "sha256_db": "a" * 64, "sha256_file": "b" * 64}
    data.update(ganti)
    return json.dumps(data) + "\n"


def test_stempel_utc():
    assert stempel_dari(datetime(2026, 10, 3, 2, 30, tzinfo=timezone.utc)) == ST
    assert stempel_dari(datetime(2026, 10, 3, 9, 30, tzinfo=timezone(timedelta(hours=7)))) == ST


def test_urai_manifest_sah():
    assert urai_manifest_backup(_manifest(), H, ST) == HasilBackup(100, 200, "a" * 64, "b" * 64)


@pytest.mark.parametrize("teks", [
    "bukan json", "[]", _manifest(versi=2), _manifest(site_id=str(uuid.uuid4())), _manifest(nama="lain"),
    _manifest(stempel="20261004T023000Z"), _manifest(ukuran_db=-1), _manifest(ukuran_file="200"),
    _manifest(ukuran_db=True), _manifest(sha256_db="A" * 64), _manifest(sha256_file="b" * 63),
    _manifest(domain="x" * 5000),
])
def test_urai_manifest_rusak_atau_melebihi_batas_ditolak(teks):
    with pytest.raises(GalatPembantu) as e:
        urai_manifest_backup(teks, H, ST)
    assert e.value.kode == "backup"


def test_tujuan_lokal_memanggil_subperintah():
    class Pb:
        def __init__(self):
            self.panggilan = []

        def prod_backup(self, nama, stempel):
            self.panggilan.append(("prod_backup", nama, stempel))
            return _manifest()

        def prod_backup_hapus(self, nama, stempel):
            self.panggilan.append(("prod_backup_hapus", nama, stempel))

    pb = Pb()
    t = TujuanLokal(pb)
    assert t.kode == "lokal"
    assert t.buat(H, ST).sha256_db == "a" * 64
    t.hapus(H, ST)
    assert pb.panggilan == [("prod_backup", "toko-co-id", ST), ("prod_backup_hapus", "toko-co-id", ST)]


def test_tujuan_dari_setelan(monkeypatch):
    from wpmgr.config import get_settings

    assert isinstance(tujuan_dari_setelan(object()), TujuanLokal)
    monkeypatch.setenv("WPMGR_BACKUP_TUJUAN", "s3")
    get_settings.cache_clear()
    with pytest.raises(SiteError):
        tujuan_dari_setelan(object())


def test_cek_disk_backup():
    gb = 1024**3
    assert cek_disk_backup(StatusProd(0, 0, 0, 100 * gb, 50 * gb, {}), 10 * gb) is None
    pesan = cek_disk_backup(StatusProd(0, 0, 0, 100 * gb, 20 * gb, {}), 10 * gb)
    assert pesan.startswith("Sisa disk backup sesudah backup akan 10,0 GB (10% dari 100,0 GB)")


def test_pilih_simpan_celah_hari():
    # Backup gagal tanggal 5-9: yang disimpan 7 TANGGAL berbeda terbaru (13, 12, 11, 10, 4, 3, 2),
    # bukan 7 hari kalender terakhir (yang hanya menyisakan 10-13).
    hari = [1, 2, 3, 4, 10, 11, 12, 13]
    backups = [_b(d, _hari(2026, 10, d)) for d in hari]
    simpan = pilih_simpan(backups, harian=7, mingguan=0)
    assert simpan == {13, 12, 11, 10, 4, 3, 2}


def test_pilih_simpan_satu_per_tanggal_dan_manual_ikut_dihitung():
    backups = [_b(1, _hari(2026, 10, 5, 2)), _b(2, _hari(2026, 10, 5, 14)), _b(3, _hari(2026, 10, 4))]
    assert pilih_simpan(backups, harian=7, mingguan=0) == {2, 3}


def test_pilih_simpan_mingguan():
    # 6 minggu, satu backup per hari: 7 harian + 4 mingguan (terbaru per minggu ISO).
    awal = _hari(2026, 9, 1)
    backups = [_b(i, awal + timedelta(days=i)) for i in range(42)]
    simpan = pilih_simpan(backups, harian=7, mingguan=4)
    harian = {i for i in range(35, 42)}
    mingguan = {max(b.id for b in backups if b.dibuat_pada.isocalendar()[:2] == mg)
                for mg in sorted({b.dibuat_pada.isocalendar()[:2] for b in backups}, reverse=True)[:4]}
    assert simpan == harian | mingguan


def test_pilih_simpan_pergantian_tahun_iso():
    # 2027-01-01 (Jumat) masih minggu ISO 53 tahun 2026; 2027-01-04 minggu 1 tahun 2027.
    backups = [_b(1, _hari(2026, 12, 28)), _b(2, _hari(2027, 1, 1)), _b(3, _hari(2027, 1, 4))]
    assert pilih_simpan(backups, harian=1, mingguan=3) == {3, 2}


def test_pilih_simpan_terbaru_selalu_disimpan():
    backups = [_b(1, _hari(2026, 10, 1)), _b(2, _hari(2026, 10, 2))]
    assert pilih_simpan(backups, harian=1, mingguan=0) == {2}
    assert pilih_simpan([], harian=7, mingguan=4) == set()
```

File: `tests/integration/test_hosting_backup.py`
```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from staging_palsu import GB, PembantuHostingPalsu

from wpmgr.errors import SiteError
from wpmgr.hosting import backup, cron
from wpmgr.jobs import handlers
from wpmgr.jobs.queue import buat_job
from wpmgr.models import ActivityLog, HostingBackup, HostingVps, Job, JobStatus, JobType, StatusHosting
from wpmgr.staging import umum
from wpmgr.staging.pembantu import GalatPembantu, StatusProd
from wpmgr.web import routes_hosting

pytestmark = pytest.mark.integration


@pytest.fixture
def aktif(sesi, site_hosting):
    sekarang = datetime.now(timezone.utc)
    site_hosting.status = StatusHosting.aktif
    site_hosting.dilayani_vps_pada = sekarang - timedelta(days=20)
    site_hosting.aktif_pada = sekarang - timedelta(days=20)
    sesi.commit()
    return site_hosting


@pytest.fixture
def pb(hosting_aktif, aktif, monkeypatch):
    palsu = PembantuHostingPalsu(hosting_aktif)
    palsu.state["toko-co-id"] = {"site_id": str(aktif.site_id), "domain": "toko.co.id", "mode": "aktif",
                                 "prefix": "wp_", "php": "8.1"}
    monkeypatch.setattr(umum, "buat_pembantu", lambda: palsu)
    return palsu


def _lama(sesi, h, n):
    """n backup lama, satu per hari mulai 30 hari lalu."""
    awal = datetime.now(timezone.utc) - timedelta(days=30)
    for i in range(n):
        t = awal + timedelta(days=i)
        sesi.add(HostingBackup(site_id=h.site_id, tujuan="lokal", stempel=backup.stempel_dari(t), status="tersedia",
                               manual=False, ukuran_db=1, ukuran_file=1, sha256_db="a" * 64, sha256_file="b" * 64,
                               dibuat_pada=t))
    sesi.commit()


def _jalankan(sesi, h, payload=None, job=None):
    job = job or buat_job(sesi, h.site_id, JobType.backup_hosting, payload or {})
    hasil = backup.tangani_backup_hosting(sesi, job, None)
    job.status = JobStatus.success
    sesi.commit()
    return job, hasil


def test_handler_terdaftar():
    assert handlers.HANDLER[JobType.backup_hosting] is backup.tangani_backup_hosting


def test_backup_sukses_dan_pangkas(sesi, aktif, pb):
    _lama(sesi, aktif, 12)
    job, hasil = _jalankan(sesi, aktif, {"manual": True})
    baru = sesi.query(HostingBackup).filter(HostingBackup.job_id == job.id).one()
    assert (baru.status, baru.manual, baru.tujuan, baru.sha256_db) == ("tersedia", True, "lokal", "a" * 64)
    assert baru.stempel == umum.kemajuan(job)["stempel"] == hasil["stempel"]
    tersedia = sesi.query(HostingBackup).filter(HostingBackup.status == "tersedia").all()
    dipangkas = sesi.query(HostingBackup).filter(HostingBackup.status == "dipangkas").all()
    assert len(tersedia) + len(dipangkas) == 13 and dipangkas
    assert hasil["dipangkas"] == len(dipangkas)
    assert {("prod_backup_hapus", "toko-co-id", b.stempel) for b in dipangkas} <= set(pb.panggilan)
    h = sesi.get(HostingVps, aktif.id, populate_existing=True)
    assert h.backup_terakhir_pada is not None and h.backup_gagal_pada is None
    assert h.status == StatusHosting.aktif
    assert sesi.query(ActivityLog).filter(ActivityLog.job_id == job.id).one().pesan == "Backup situs dibuat"


def test_backup_gagal_tidak_memangkas(sesi, aktif, pb):
    _lama(sesi, aktif, 12)
    pb.gagal["prod_backup"] = GalatPembantu("backup", "Membuat backup situs gagal. Backup situs gagal dibuat.")
    job = buat_job(sesi, aktif.site_id, JobType.backup_hosting)
    job.attempts = job.max_attempts
    sesi.commit()
    with pytest.raises(SiteError):
        backup.tangani_backup_hosting(sesi, job, None)
    assert "prod_backup_hapus" not in pb.nama_panggilan()
    assert sesi.query(HostingBackup).filter(HostingBackup.status == "tersedia").count() == 12
    h = sesi.get(HostingVps, aktif.id, populate_existing=True)
    assert h.backup_gagal_pada is not None and h.status == StatusHosting.aktif


def test_disk_backup_penuh_ditolak(sesi, aktif, pb):
    pb.status_prod = StatusProd(8 * GB, 200 * GB, 150 * GB, 100 * GB, 10 * GB, {})
    with pytest.raises(umum.GalatDitolakTanpaUbah):
        _jalankan(sesi, aktif)
    assert "prod_backup" not in pb.nama_panggilan()
    assert sesi.get(HostingVps, aktif.id, populate_existing=True).backup_gagal_pada is not None


def test_backup_idempoten_per_stempel(sesi, aktif, pb):
    job = buat_job(sesi, aktif.site_id, JobType.backup_hosting, {"kemajuan": {"stempel": "20261003T023000Z"}})
    sesi.add(HostingBackup(site_id=aktif.site_id, tujuan="lokal", stempel="20261003T023000Z", status="tersedia",
                           manual=False, ukuran_db=1, ukuran_file=1, sha256_db="a" * 64, sha256_file="b" * 64))
    sesi.commit()
    _jalankan(sesi, aktif, job=job)
    assert ("prod_backup", "toko-co-id", "20261003T023000Z") in pb.panggilan
    assert sesi.query(HostingBackup).filter(HostingBackup.stempel == "20261003T023000Z").count() == 1


def test_backup_ditolak_bila_belum_dilayani(sesi, aktif, pb):
    aktif.dilayani_vps_pada = None
    aktif.status = StatusHosting.pratinjau
    sesi.commit()
    with pytest.raises(umum.GalatDitolakTanpaUbah) as e:
        _jalankan(sesi, aktif)
    assert e.value.pesan == backup.PESAN_BELUM_DILAYANI


def test_hapus_gagal_tetap_tersedia_dan_dicoba_lagi(sesi, aktif, pb):
    _lama(sesi, aktif, 12)
    pb.gagal["prod_backup_hapus"] = GalatPembantu("ditolak", "Menghapus backup situs gagal.")
    _, hasil = _jalankan(sesi, aktif)
    assert hasil["dipangkas"] == 0
    assert sesi.query(HostingBackup).filter(HostingBackup.status == "tersedia").count() == 13


# ---- cron ------------------------------------------------------------------------


def test_backup_harian_mengantrekan_dan_melewati_yang_sibuk(sesi, aktif):
    sekarang = datetime.now(timezone.utc)
    assert cron.antrekan_backup_harian(sesi, sekarang) == {"diantrekan": 1, "dilewati": 0}
    assert cron.antrekan_backup_harian(sesi, sekarang) == {"diantrekan": 0, "dilewati": 1}
    job = sesi.query(Job).one()
    assert job.tipe == JobType.backup_hosting and job.payload == {"manual": False}


def test_cek_dns_mengantrekan_backup_pertama_sekali(sesi, aktif):
    assert cron.antrekan_backup_pertama(sesi) == 1
    assert cron.antrekan_backup_pertama(sesi) == 0
    sesi.query(Job).delete()
    aktif.backup_gagal_pada = datetime.now(timezone.utc)
    sesi.commit()
    assert cron.antrekan_backup_pertama(sesi) == 0
    aktif.backup_gagal_pada = None
    aktif.backup_terakhir_pada = datetime.now(timezone.utc)
    sesi.commit()
    assert cron.antrekan_backup_pertama(sesi) == 0


# ---- API -------------------------------------------------------------------------


@pytest.mark.parametrize("metode", ["GET", "POST"])
def test_api_backup_anonim_ditolak(engine, hosting_aktif, metode):
    from wpmgr.web.app import buat_app

    anon = TestClient(buat_app(), base_url="https://testserver")
    assert anon.request(metode, f"/api/sites/{uuid.uuid4()}/hosting/backup").status_code == 401


def test_api_backup_sekarang(klien_web, sesi, site_hosting):
    url = f"/api/sites/{site_hosting.site_id}/hosting/backup"
    r = klien_web.post(url)
    assert r.status_code == 409 and r.json()["detail"] == routes_hosting.PESAN_BACKUP_BELUM
    site_hosting.status = StatusHosting.aktif
    site_hosting.dilayani_vps_pada = datetime.now(timezone.utc)
    sesi.commit()
    r = klien_web.post(url)
    assert r.status_code == 200
    assert sesi.get(Job, r.json()["job_id"]).payload == {"manual": True}
    assert klien_web.post(url).status_code == 409
    assert klien_web.get(url).json() == {"backup": []}
```

- [ ] **Step 7: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_hosting_backup.py tests/integration/test_hosting_backup.py -q`. Expected: `ModuleNotFoundError: No module named 'wpmgr.hosting.backup'`.

- [ ] **Step 8: Pustaka dan job backup.**

File: `src/wpmgr/hosting/backup.py`
```python
"""Backup situs yang dihosting VPS (spec Lapis 4 §9) dan job backup_hosting (§10.5).

Isi backup hanya bisa dibaca root (BACKUP_DIR 0700); dashboard hanya
menyimpan metadata dari manifest yang dicetak skrip pembantu, setelah
divalidasi ketat. Pemangkasan hanya berjalan di akhir backup yang SUKSES:
backup yang gagal tidak pernah memangkas apa pun (RF6).
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from wpmgr.config import get_settings
from wpmgr.hosting import umum as hu
from wpmgr.models import HostingBackup, HostingVps
from wpmgr.staging import umum as stg
from wpmgr.staging.aman import POLA_SHA256
from wpmgr.staging.pembantu import GalatPembantu
from wpmgr.staging.rencana import SISA_DISK_MINIMUM, format_byte

log = logging.getLogger("wpmgr.hosting.backup")

POLA_STEMPEL = re.compile(r"[0-9]{8}T[0-9]{6}Z")
BATAS_MANIFEST = 4096
STATUS_TERSEDIA = "tersedia"
STATUS_DIPANGKAS = "dipangkas"
PESAN_MANIFEST = "Keluaran backup dari skrip pembantu tidak sah."
PESAN_BELUM_DILAYANI = "Backup hanya untuk situs yang sudah dilayani VPS."
PESAN_TUJUAN = "Tujuan backup (WPMGR_BACKUP_TUJUAN) tidak dikenal."


@dataclass(frozen=True)
class HasilBackup:
    ukuran_db: int
    ukuran_file: int
    sha256_db: str
    sha256_file: str


class TujuanBackup(Protocol):
    kode: str  # disimpan di hosting_backup.tujuan

    def buat(self, hosting, stempel: str) -> HasilBackup: ...

    def hapus(self, hosting, stempel: str) -> None: ...  # idempoten


def stempel_dari(waktu: datetime) -> str:
    return waktu.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def urai_manifest_backup(teks, hosting, stempel: str) -> HasilBackup:
    """Manifest dari `prod-backup`: harus milik situs, nama, dan stempel ini persis."""
    if not isinstance(teks, str) or len(teks.encode("utf-8")) > BATAS_MANIFEST:
        raise GalatPembantu("backup", PESAN_MANIFEST)
    try:
        data = json.loads(teks)
    except ValueError:
        raise GalatPembantu("backup", PESAN_MANIFEST) from None
    if not isinstance(data, dict) or data.get("versi") != 1 or data.get("site_id") != str(hosting.site_id) \
            or data.get("nama") != hosting.nama or data.get("stempel") != stempel:
        raise GalatPembantu("backup", PESAN_MANIFEST)
    nilai = {}
    for kunci in ("ukuran_db", "ukuran_file"):
        v = data.get(kunci)
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 2**53:
            raise GalatPembantu("backup", PESAN_MANIFEST)
        nilai[kunci] = v
    for kunci in ("sha256_db", "sha256_file"):
        v = data.get(kunci)
        if not isinstance(v, str) or not POLA_SHA256.fullmatch(v):
            raise GalatPembantu("backup", PESAN_MANIFEST)
        nilai[kunci] = v
    return HasilBackup(**nilai)


class TujuanLokal:
    """Disk VPS sendiri lewat `prod-backup`/`prod-backup-hapus` (spec §9.1)."""

    kode = "lokal"

    def __init__(self, pb) -> None:
        self.pb = pb

    def buat(self, hosting, stempel: str) -> HasilBackup:
        return urai_manifest_backup(self.pb.prod_backup(hosting.nama, stempel), hosting, stempel)

    def hapus(self, hosting, stempel: str) -> None:
        self.pb.prod_backup_hapus(hosting.nama, stempel)


# Tujuan di luar VPS (S3/R2) menjadi implementasi berikutnya tanpa mengubah antarmuka.
TUJUAN = {"lokal": TujuanLokal}


def tujuan_dari_setelan(pb) -> TujuanBackup:
    kelas = TUJUAN.get(get_settings().backup_tujuan)
    if kelas is None:
        raise stg.galat_gagal(PESAN_TUJUAN)
    return kelas(pb)


def cek_disk_backup(status, tambahan: int) -> str | None:
    """Sisa disk BACKUP_DIR sesudah backup >= 15% (spec §10.5); taksiran tanpa kompresi, konservatif."""
    total = max(1, status.backup_total)
    sisa = status.backup_bebas - max(0, tambahan)
    if sisa / total < SISA_DISK_MINIMUM:
        return (f"Sisa disk backup sesudah backup akan {format_byte(max(0, sisa))} "
                f"({int(max(0, sisa) * 100 // total)}% dari {format_byte(total)}); minimal 15%.")
    return None


def pilih_simpan(backups, harian: int = 7, mingguan: int = 4) -> set:
    """Id backup yang disimpan (spec §9.3): terbaru per tanggal UTC untuk `harian` tanggal
    berbeda terbaru, terbaru per minggu ISO untuk `mingguan` minggu berbeda terbaru, dan
    backup terbaru selalu. Tanggal yang punya backup, bukan hari kalender: bila backup
    gagal beberapa hari, backup lama bertahan lebih lama."""
    urut = sorted(backups, key=lambda b: (b.dibuat_pada, b.id), reverse=True)
    if not urut:
        return set()
    simpan = {urut[0].id}
    tanggal: set = set()
    minggu: set = set()
    for b in urut:
        t = b.dibuat_pada.astimezone(timezone.utc)
        if t.date() not in tanggal and len(tanggal) < harian:
            tanggal.add(t.date())
            simpan.add(b.id)
        mg = tuple(t.isocalendar())[:2]
        if mg not in minggu and len(minggu) < mingguan:
            minggu.add(mg)
            simpan.add(b.id)
    return simpan


def pangkas(sesi, h: HostingVps, tujuan: TujuanBackup) -> int:
    """Hapus backup di luar retensi; hapus yang gagal tetap `tersedia` dan dicoba pada backup berikutnya."""
    s = get_settings()
    baris = sesi.scalars(select(HostingBackup).where(
        HostingBackup.site_id == h.site_id, HostingBackup.tujuan == tujuan.kode,
        HostingBackup.status == STATUS_TERSEDIA)).all()
    simpan = pilih_simpan(baris, s.backup_harian, s.backup_mingguan)
    n = 0
    for b in baris:
        if b.id in simpan:
            continue
        stempel = b.stempel
        sesi.commit()
        try:
            tujuan.hapus(h, stempel)
        except GalatPembantu as exc:
            log.warning("Backup %s situs %s gagal dipangkas: %s", stempel, h.nama, exc.kode)
            continue
        b.status = STATUS_DIPANGKAS
        sesi.commit()
        n += 1
    return n


def backup_hosting(sesi, job, site, h: HostingVps, pb) -> dict:
    if h.dilayani_vps_pada is None:
        raise stg.GalatDitolakTanpaUbah(PESAN_BELUM_DILAYANI)
    stempel = stg.kemajuan(job).get("stempel")
    if not (isinstance(stempel, str) and POLA_STEMPEL.fullmatch(stempel)):
        # Dari mulai percobaan pertama, disimpan supaya percobaan ulang memakai stempel yang sama.
        stempel = stempel_dari(hu.sekarang())
        stg.simpan_kemajuan(sesi, job, stempel=stempel, tahap_backup="backup")
    status = pb.prod_status()
    pesan = cek_disk_backup(status, h.ukuran_file + h.ukuran_db)
    if pesan:
        raise stg.GalatDitolakTanpaUbah(pesan)
    tujuan = tujuan_dari_setelan(pb)
    with stg.detak_latar(sesi, job):
        hasil = tujuan.buat(h, stempel)
    sesi.execute(insert(HostingBackup).values(
        site_id=h.site_id, job_id=job.id, tujuan=tujuan.kode, stempel=stempel, status=STATUS_TERSEDIA,
        manual=bool((job.payload or {}).get("manual")), ukuran_db=hasil.ukuran_db, ukuran_file=hasil.ukuran_file,
        sha256_db=hasil.sha256_db, sha256_file=hasil.sha256_file,
    ).on_conflict_do_nothing(constraint="uq_hosting_backup_stempel"))
    sesi.commit()
    dipangkas = pangkas(sesi, h, tujuan)
    baris = sesi.get(HostingVps, h.id, populate_existing=True)
    baris.backup_terakhir_pada = hu.sekarang()
    baris.backup_gagal_pada = None
    ringkas = {"stempel": stempel, "ukuran_db": hasil.ukuran_db, "ukuran_file": hasil.ukuran_file,
               "dipangkas": dipangkas}
    stg.catat_aktivitas(sesi, h.site_id, job, "Backup situs dibuat", {
        **ringkas, "ukuran_teks": format_byte(hasil.ukuran_db + hasil.ukuran_file)})
    sesi.commit()
    return ringkas


def tangani_backup_hosting(sesi, job, klien) -> dict:
    """Handler worker; tidak memakai klien site."""
    def inti(sesi, job, site, h):
        return backup_hosting(sesi, job, site, h, stg.buat_pembantu())

    return hu.jalankan_hosting(sesi, job, inti, "Backup situs")
```

Di `src/wpmgr/jobs/handlers.py`, tambahkan `from wpmgr.hosting.backup import tangani_backup_hosting` dan entri `JobType.backup_hosting: tangani_backup_hosting,`.

- [ ] **Step 9: Cron, CLI, crontab.** Di `src/wpmgr/hosting/cron.py`, tambahkan di akhir:

```python
def antrekan_backup_harian(sesi, sekarang: datetime) -> dict:
    """backup_hosting untuk setiap situs yang dilayani VPS; uq_jobs_hosting_aktif = dilewati (spec §15)."""
    hasil = {"diantrekan": 0, "dilewati": 0}
    ids = sesi.scalars(select(HostingVps.site_id).where(HostingVps.dilayani_vps_pada.is_not(None))).all()
    sesi.commit()
    for site_id in ids:
        h = kunci_hosting(sesi, site_id)
        if h is None or h.dilayani_vps_pada is None:
            sesi.commit()
            continue
        sesi.add(Job(site_id=site_id, tipe=JobType.backup_hosting, payload={"manual": False}))
        try:
            sesi.commit()
            hasil["diantrekan"] += 1
        except IntegrityError:
            sesi.rollback()
            hasil["dilewati"] += 1
    return hasil


def antrekan_backup_pertama(sesi) -> int:
    """Backup pertama sesudah aktivasi (Koreksi #2): situs aktif yang belum pernah dibackup
    dan belum pernah gagal backup, tanpa job hosting aktif."""
    n = 0
    ids = sesi.scalars(select(HostingVps.site_id).where(
        HostingVps.status == StatusHosting.aktif, HostingVps.backup_terakhir_pada.is_(None),
        HostingVps.backup_gagal_pada.is_(None))).all()
    sesi.commit()
    for site_id in ids:
        h = kunci_hosting(sesi, site_id)
        if h is None or h.status != StatusHosting.aktif or h.backup_terakhir_pada is not None \
                or h.backup_gagal_pada is not None or ada_job_hosting(sesi, site_id):
            sesi.commit()
            continue
        sesi.add(Job(site_id=site_id, tipe=JobType.backup_hosting, payload={"manual": False}))
        try:
            sesi.commit()
            n += 1
        except IntegrityError:
            sesi.rollback()
    return n
```

Di `src/wpmgr/kunci.py`, tambahkan `KUNCI_HOSTING_BACKUP = 72_140_011`.

Di `src/wpmgr/cli.py`:
- tambahkan `KUNCI_HOSTING_BACKUP` ke impor `wpmgr.kunci` dan `antrekan_backup_harian`, `antrekan_backup_pertama` ke impor `wpmgr.hosting.cron`;
- ganti badan `hosting_cek_dns()` sesudah `if not dapat: ...`:

```python
        sekarang = datetime.now(timezone.utc)
        with get_session() as sesi:
            hasil = cek_dns_semua(sesi, sekarang)
            hasil["backup_pertama"] = antrekan_backup_pertama(sesi)
    print(f"Cek DNS hosting: {hasil['diperiksa']} diperiksa, {hasil['diantrekan']} aktivasi diantrekan, "
          f"{hasil['backup_pertama']} backup pertama diantrekan")
    return hasil
```

- sesudah `renew_hosting_certs()`, tambahkan:

```python
def backup_hosting() -> dict | None:
    if _hosting_mati():
        return None
    with kunci_advisory(db.engine, KUNCI_HOSTING_BACKUP) as dapat:
        if not dapat:
            print("Pengantrean backup hosting lain masih berjalan; dilewati")
            return None
        with get_session() as sesi:
            hasil = antrekan_backup_harian(sesi, datetime.now(timezone.utc))
    print(f"Backup hosting: {hasil['diantrekan']} diantrekan, {hasil['dilewati']} dilewati")
    return hasil
```

- di `main()`, tambahkan `sub.add_parser("backup-hosting")` dan cabang `elif args.perintah == "backup-hosting": backup_hosting()`.

Di `deploy/crontab`, di dalam blok Lapis 4 (sesudah baris `CRON_TZ=Asia/Jakarta`), tambahkan sebagai baris pertama blok itu:

```
30   2 * * *  cd /opt/wpmgr && .venv/bin/python -m wpmgr.cli backup-hosting        >> /var/log/wpmgr/cron.log 2>&1
```

- [ ] **Step 10: Route backup.** Di `src/wpmgr/web/routes_hosting.py`, sesudah `PESAN_HAPUS_DILAYANI`, tambahkan:

```python
PESAN_BACKUP_BELUM = "Backup hanya untuk situs yang sudah dilayani VPS."
```

dan di akhir berkas:

```python
# ---- backup (spec §11; tanpa route pemulihan, unduh, atau baca isi backup) --------------


@router.get("/api/sites/{site_id}/hosting/backup")
def daftar_backup_route(site_id: uuid.UUID, pengguna: PenggunaApi):
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        return {"backup": daftar_backup(sesi, site_id)}


@router.post("/api/sites/{site_id}/hosting/backup")
def backup_sekarang(site_id: uuid.UUID, pengguna: PenggunaApi):
    fitur_hosting()
    with db.SessionLocal() as sesi:
        site_atau_404(sesi, site_id)
        h = _hosting_atau_409(sesi, site_id)
        if h.dilayani_vps_pada is None:
            raise HTTPException(status_code=409, detail=PESAN_BACKUP_BELUM)
        tolak_bila_sibuk(sesi, site_id)
        stg.catat_aktivitas(sesi, site_id, None, "Backup situs manual diminta", user_id=pengguna.id)
        job = simpan_job_hosting(sesi, Job(site_id=site_id, tipe=JobType.backup_hosting, payload={"manual": True},
                                           dibuat_oleh=pengguna.id))
        return {"job_id": job.id}
```

- [ ] **Step 11: Tombol UI.** Di `src/wpmgr/templates/_tab_hosting.html`, ganti `<div id="hosting-backup"></div>` dengan:

```html
          <div id="hosting-backup" class="toolbar">
            <button type="button" @click="backupSekarang()" :disabled="!!data.job">Backup sekarang</button>
          </div>
```

Di `src/wpmgr/static/app/hosting.js`, tambahkan metode sesudah `batalkanPindah()`:

```js
    async backupSekarang() {
      await this.aksi('backup', 'Backup diantrekan.');
    },
```

- [ ] **Step 12: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/unit/test_hosting_backup.py tests/unit/test_deploy_staging.py tests/integration/test_hosting_backup.py tests/integration/test_hosting_cron.py tests/integration/test_api_hosting.py tests/integration/test_hosting_halaman.py -q`. Expected: semua lulus.

- [ ] **Step 13: Seluruh test unit dan integrasi, bats, shellcheck, lalu lint.** Expected: semua lulus; `ruff check .` bersih.

- [ ] **Step 14: Commit.**

```bash
git add deploy/staging/wpmgr-staging deploy/staging/tests/palsu/docker deploy/staging/tests/pembantu.bats src/wpmgr/hosting/backup.py src/wpmgr/jobs/handlers.py src/wpmgr/hosting/cron.py src/wpmgr/kunci.py src/wpmgr/cli.py deploy/crontab src/wpmgr/web/routes_hosting.py src/wpmgr/templates/_tab_hosting.html src/wpmgr/static/app/hosting.js tests/unit/test_hosting_backup.py tests/integration/test_hosting_backup.py
git commit -m "feat(hosting): backup harian lokal dengan retensi 7+4, backup manual, dan backup pertama

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Berkas deploy dan README

**Files:**
- Create: `deploy/staging/nginx-wpmgr-hosting.conf`
- Modify: `deploy/staging/staging.conf.contoh`, `deploy/staging/wpmgr-staging-siapkan.service`, `README.md`
- Test: `tests/unit/test_deploy_staging.py` (tambah)

**Interfaces:**
- Consumes: semua subperintah, variabel, perintah CLI, dan berkas Task 1–14.
- Produces: `deploy/staging/nginx-wpmgr-hosting.conf` (satu baris `include /etc/nginx/wpmgr-hosting/*.conf;`); unit `wpmgr-staging-siapkan.service` dengan `ExecStart` kedua `prod-siapkan`; `staging.conf.contoh` memuat kunci hosting; README bagian **"Pindah hosting (Lapis 4)"** sesudah "Staging (Lapis 3)".

- [ ] **Step 1: Tulis test yang gagal.** Tambahkan ke akhir `tests/unit/test_deploy_staging.py`:

```python
def test_nginx_hosting_hanya_include_direktori_domain():
    baris = [b.strip() for b in _teks("nginx-wpmgr-hosting.conf").splitlines()
             if b.strip() and not b.strip().startswith("#")]
    assert baris == ["include /etc/nginx/wpmgr-hosting/*.conf;"]


def test_unit_siapkan_juga_menyiapkan_produksi():
    t = _teks("wpmgr-staging-siapkan.service")
    assert t.index("ExecStart=/usr/local/sbin/wpmgr-staging siapkan") \
        < t.index("ExecStart=/usr/local/sbin/wpmgr-staging prod-siapkan")


def test_contoh_konfigurasi_memuat_kunci_hosting():
    t = _teks("staging.conf.contoh")
    for kunci in ("HOSTING_DIR=/var/lib/wpmgr/hosting", "PROD_SUBNET=172.31.251.0/24",
                  "PROD_ROUTER_PORT=127.0.0.1:8091", "PROD_CERT_DIR=/var/lib/wpmgr/hosting-certs",
                  "NGINX_HOSTING_DIR=/etc/nginx/wpmgr-hosting", "BACKUP_DIR=/var/lib/wpmgr/backup",
                  "IP_PUBLIK=169.58.91.181"):
        assert kunci in t, kunci
    assert "NGINX_UJI_SAJA=1" not in t and "SERTIFIKAT_SENDIRI=1" not in t


def test_readme_menjelaskan_pindah_hosting():
    readme = (AKAR / "README.md").read_text(encoding="utf-8")
    assert readme.index("## Staging (Lapis 3)") < readme.index("## Pindah hosting (Lapis 4)") \
        < readme.index("## Keterbatasan yang diketahui")
    for wajib in ("wpmgr-staging prod-siapkan", "install -d -o wpmgr -g wpmgr -m 0700 /var/lib/wpmgr/hosting",
                  "/etc/nginx/sites-enabled/wpmgr-hosting.conf", "install -d -m 0755 /etc/nginx/wpmgr-hosting",
                  "nginx -T | grep -n server_name", "grep -rn 'allow\\|deny' /etc/nginx/sites-enabled",
                  "WPMGR_HOSTING_IPV4", "WPMGR_HOSTING_IPV6", "2a02:c207:2347:2607::1", "CRON_TZ=Asia/Jakarta",
                  "wpmgr-worker@staging", "Izinkan staging", "AAAA", "cdn.hstgr.net", "TTL",
                  "### Pemulihan backup manual", "docker exec wpmgr-prod-db", "wpmgr-staging prod-jalan",
                  "files.sebelum-pulih-", "### Melepas site aktif secara manual", "DELETE FROM hosting_vps",
                  "rsync", "mail()", "src/wpmgr/hosting/", "ESTABLISHED,RELATED"):
        assert wajib in readme, wajib
```

- [ ] **Step 2: Jalankan dan pastikan gagal.** Run: `.venv/Scripts/python -m pytest tests/unit/test_deploy_staging.py -q`. Expected: 4 test baru gagal (`FileNotFoundError` untuk `nginx-wpmgr-hosting.conf`, `ValueError: substring not found`, dan `AssertionError` untuk kunci/README).

- [ ] **Step 3: Berkas deploy.**

File: `deploy/staging/nginx-wpmgr-hosting.conf`
```nginx
# /etc/nginx/sites-enabled/wpmgr-hosting.conf -- dipasang SEKALI (spec Lapis 4 §7.4).
# Satu berkas per domain situs yang dihosting VPS ditulis skrip pembantu
# (`wpmgr-staging prod-domain <nama>`) di /etc/nginx/wpmgr-hosting/, dari
# template tetap, selalu lewat `nginx -t` dan dengan rollback. Glob tanpa
# berkas sah di nginx, jadi baris ini aman dipasang sebelum situs pertama.
include /etc/nginx/wpmgr-hosting/*.conf;
```

Tambahkan ke akhir `deploy/staging/staging.conf.contoh`:

```
# ---- Hosting VPS (Lapis 4). Kosongkan HOSTING_DIR untuk mematikan prod-*.
# HOSTING_DIR dibuat operator: install -d -o wpmgr -g wpmgr -m 0700 /var/lib/wpmgr/hosting
# Tidak boleh sama dengan, di dalam, atau memuat STAGING_DIR.
HOSTING_DIR=/var/lib/wpmgr/hosting
# Tidak boleh beririsan dengan SUBNET staging.
PROD_SUBNET=172.31.251.0/24
PROD_ROUTER_PORT=127.0.0.1:8091
PROD_CERT_DIR=/var/lib/wpmgr/hosting-certs
NGINX_HOSTING_DIR=/etc/nginx/wpmgr-hosting
BACKUP_DIR=/var/lib/wpmgr/backup
# IPv4 publik VPS: tujuan satu-satunya yang boleh dibuka container produksi
# di host (port 80/443, panggilan situs ke URL-nya sendiri).
IP_PUBLIK=169.58.91.181
```

Di `deploy/staging/wpmgr-staging-siapkan.service`, tepat sesudah `ExecStart=/usr/local/sbin/wpmgr-staging siapkan`, tambahkan:

```ini
# Lapis 4: jaringan, isolasi, dan layanan produksi (keluar 0 bila HOSTING_DIR kosong).
ExecStart=/usr/local/sbin/wpmgr-staging prod-siapkan
```

- [ ] **Step 4: README.** Di `README.md`, sisipkan bagian berikut tepat sebelum baris `## Keterbatasan yang diketahui`:

````markdown
## Pindah hosting (Lapis 4)

Memindahkan site dari shared hosting (tanpa SSH) ke VPS dashboard dan menjalankannya di sana sebagai
produksi. Data diambil lewat connector 3.0 (endpoint baca `/staging/*` Lapis 3) dari IP hosting lama;
site lama **tidak pernah diubah**. Spesifikasi: `docs/superpowers/specs/2026-10-03-wp-manager-lapis4-design.md`.

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
2. Pasang ulang skrip pembantu: `install -o root -g root -m 0755 deploy/staging/wpmgr-staging /usr/local/sbin/wpmgr-staging`.
   Sudoers tidak berubah.
3. Tambahkan kunci hosting dari `deploy/staging/staging.conf.contoh` ke `/etc/wpmgr-staging/staging.conf`,
   lalu siapkan direktori data: `install -d -o wpmgr -g wpmgr -m 0700 /var/lib/wpmgr/hosting`.
4. Jalankan `wpmgr-staging prod-siapkan` (membuat `KONF_DIR/prod`, `php.ini`, jaringan `wpmgr-prod`,
   aturan iptables, `wpmgr-prod-db`, `wpmgr-prod-router`). Pasang ulang
   `deploy/staging/wpmgr-staging-siapkan.service` (kini punya `ExecStart` kedua `prod-siapkan`), lalu
   `systemctl daemon-reload`. Catatan: Lapis 4 juga memperbaiki aturan INPUT staging dengan
   `ESTABLISHED,RELATED` di atas `DROP`, supaya balasan router ke nginx host tidak dibuang; jalankan
   `wpmgr-staging siapkan` sekali lagi sesudah memasang skrip baru.
5. Pra-cek nginx host:
   - `grep -rn 'allow\|deny' /etc/nginx/sites-enabled` — tidak boleh ada vhost yang memercayai rentang
     privat (asumsi A5: container produksi boleh membuka 80/443 IP publik VPS);
   - `nginx -T | grep -n server_name` — tidak boleh ada `server_name` untuk domain yang akan dipindah.

   Lalu pasang `deploy/staging/nginx-wpmgr-hosting.conf` ke `/etc/nginx/sites-enabled/wpmgr-hosting.conf`,
   jalankan `install -d -m 0755 /etc/nginx/wpmgr-hosting`, lalu `nginx -t && systemctl reload nginx`.
6. Isi variabel di `.env`:

   | Variabel | Nilai di VPS ini |
   |---|---|
   | `WPMGR_HOSTING_IPV4` | `169.58.91.181` (kosong = fitur mati) |
   | `WPMGR_HOSTING_IPV6` | `2a02:c207:2347:2607::1` (kosong = AAAA wajib dihapus) |
   | `WPMGR_HOSTING_DIR` | `/var/lib/wpmgr/hosting` (sama dengan `HOSTING_DIR`) |
   | `WPMGR_HOSTING_RESOLVER` | `1.1.1.1,8.8.8.8` |
   | `WPMGR_BACKUP_TUJUAN` / `_HARIAN` / `_MINGGUAN` | `lokal` / `7` / `4` |

7. Pasang ulang `deploy/crontab` (`crontab -u wpmgr deploy/crontab`). Blok hosting ada di akhir berkas
   sesudah `CRON_TZ=Asia/Jakarta`, karena zona waktu sistem VPS `Europe/Berlin`: `hosting-cek-dns` tiap
   10 menit, `backup-hosting` 02:30 WIB, `renew-hosting-certs` 03:50 WIB. Pastikan `man 5 crontab` di VPS
   menyebut `CRON_TZ`.
8. Restart `wpmgr-web`, `wpmgr-worker@1`, `wpmgr-worker@2`, dan `wpmgr-worker@staging` (job hosting
   diproses worker staging).

### Alur pengguna

1. Tab **Hosting VPS** → **Pindahkan ke VPS**. Dashboard menurunkan domain dari URL site, mencari IP
   hosting lama lewat DNS publik, dan menampilkan kata sandi pratinjau **sekali**.
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
| `AAAA @`, `AAAA www` | ubah ke `2a02:c207:2347:2607::1` (bila `WPMGR_HOSTING_IPV6` diisi), atau **hapus** |

AAAA lama ke IPv6 shared hosting Hostinger wajib diubah atau dihapus: Let's Encrypt mendahulukan IPv6 saat
validasi HTTP-01 (sertifikat tidak akan terbit), dan pengunjung IPv6 akan tetap ke hosting lama sehingga
isian form tersebar di dua tempat. Cek DNS menahan aktivasi sampai semua resolver (`1.1.1.1`, `8.8.8.8`)
sepakat. CAA yang ada wajib mengizinkan `letsencrypt.org`.

**TTL:** turunkan TTL record ke 300 detik sehari sebelum mengubah DNS bila memungkinkan, supaya jendela
"sebagian pengunjung masih ke hosting lama" sependek mungkin.

**Rollback:** sampai hosting lama dimatikan, kembalikan DNS ke nilai lama. Data yang masuk di VPS sesudah
aktivasi tidak ikut kembali.

### Pemulihan backup manual

Backup harian ada di `/var/lib/wpmgr/backup/<site_id>/<stempel>/` (root-only): `db.sql.gz`,
`files.tar.gz`, dan `manifest.json`. Pulihkan sebagai root dengan
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

`tar` diekstrak sebagai `wpmgr`, sehingga kepemilikan sesuai pemeriksaan skrip. Hapus direktori
`files.sebelum-pulih-*` manual sesudah situs diperiksa. Lakukan satu kali latihan pemulihan pada situs pertama
sebelum hosting lamanya dimatikan.

Backup berada di disk yang sama dengan situs. Salin keluar VPS secara berkala, mis.
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

### Batas Lapis 4

- `mail()` PHP tidak berfungsi di container (image WordPress tanpa MTA): pakai plugin SMTP ke penyedia
  luar untuk form/notifikasi.
- Salt `wp-config.php` dibuat baru: semua pengguna login ulang sesudah pindah.
- Plugin cache LiteSpeed (Hostinger) tidak aktif di Apache; aman, dan boleh dinonaktifkan sesudah pindah.
- Jangan menjalankan `certbot --nginx` untuk domain yang dihosting dashboard: berkas di
  `/etc/nginx/wpmgr-hosting/` selalu ditimpa `prod-domain`.
- Sertifikat host pratinjau tidak diperpanjang otomatis; **Salin ulang** menerbitkannya lagi.
- Pindah balik (VPS ke hosting lain), multisite, dan WordPress di subfolder tidak didukung.
````

Di bagian `## Keterbatasan yang diketahui`, tambahkan di akhir bagian itu (sebelum `## Struktur repo (ringkas)`):

```markdown
Keterbatasan hosting VPS (Lapis 4): pemulihan backup hanya manual (lihat "Pemulihan backup manual"),
backup hanya ke disk VPS sendiri, dan pra-cek `nginx -T` membaca `server_name` per baris (direktif yang
dipecah ke beberapa baris di berkas milik site lain hanya terbaca baris pertamanya; deteksi
`conflicting server name` saat `nginx -t` tetap menjadi lapis kedua).
```

Di tabel `## Struktur repo (ringkas)`, tambahkan dua baris sebelum baris `docs/superpowers/`:

```markdown
| `src/wpmgr/hosting/` | Lapis 4: klien hosting lama baca-saja, pindah_tarik/pindah_aktifkan, cek DNS, backup, cron |
| `src/wpmgr/web/routes_hosting.py` | API JSON tab Hosting VPS |
```

- [ ] **Step 5: Jalankan test.** Run: `.venv/Scripts/python -m pytest tests/unit/test_deploy_staging.py -q`. Expected: semua lulus. Lalu perintah bats (berkas contoh hanya memuat kunci yang dikenal skrip). Expected: `93 tests, 0 failures`.

- [ ] **Step 6: Commit.**

```bash
git add deploy/staging/nginx-wpmgr-hosting.conf deploy/staging/staging.conf.contoh deploy/staging/wpmgr-staging-siapkan.service README.md tests/unit/test_deploy_staging.py
git commit -m "docs(hosting): berkas deploy dan README pindah hosting Lapis 4

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: E2E pindah hosting terhadap WordPress asli dan runtime produksi lokal

**Files:**
- Modify: `tests/e2e/pembantu/Dockerfile`
- Create: `tests/e2e/test_hosting.py`

**Interfaces:**
- Consumes (Task 1–15): seluruh subperintah `prod-*` (dijalankan sungguhan di container `pembantu`), `hosting.pindah` (`tangani_*`, `ambil_halaman_verifikasi`, `MU_PLUGIN_PRATINJAU`, `PESAN_SUDAH_DILAYANI`), `hosting.umum.klien_lama`, `KlienLamaBacaSaja`, `hosting.dns.buat_penanya`, `hosting.dns.Jawaban`, `hosting.dns.periksa_dns`, `HostingVps`, `HostingBackup`, `Pembantu.siapkan`/`prod_siapkan`, `hash_sandi`, `aman.tulis_atomik`; e2e conftest (`site_terpasang`, `klien_http`, `wpcli`, `WP_URL`, `AKAR_REPO`, `sesi`), `worker.proses_satu`.
- Produces: test `test_alur_pindah_hosting_lengkap` (spec §18.5 langkah 1–7) dan image `pembantu` yang memuat `nginx`, `openssl`, dan GNU `tar`.

- [ ] **Step 1: Image pembantu.** Ganti isi `tests/e2e/pembantu/Dockerfile`:

```dockerfile
# Menjalankan skrip pembantu (staging dan hosting) di Linux untuk e2e di Docker
# Desktop. Hanya untuk test: di VPS skrip dijalankan root langsung lewat sudo.
# nginx dipakai sungguhan untuk `nginx -t`/`nginx -T` (NGINX_UJI_SAJA=1), openssl
# untuk SERTIFIKAT_SENDIRI=1, dan GNU tar untuk prod-backup.
FROM docker:27-cli
RUN apk add --no-cache bash coreutils curl grep setpriv nginx openssl tar \
 && mkdir -p /run/nginx /etc/letsencrypt \
 && printf 'ssl_protocols TLSv1.2 TLSv1.3;\n' > /etc/letsencrypt/options-ssl-nginx.conf \
 && printf 'include /srv/wpmgr/nginx-hosting/*.conf;\n' > /etc/nginx/http.d/wpmgr-hosting.conf
```

- [ ] **Step 2: Tulis test e2e.**

File: `tests/e2e/test_hosting.py`
```python
"""E2E Lapis 4 (spec §18.5): WordPress e2e sebagai hosting lama, runtime produksi di Docker Desktop.

Skrip pembantu berjalan sungguhan di container `pembantu` (profil compose
`staging`) dengan socket Docker Desktop; `nginx -t`/`-T` sungguhan atas
`nginx.conf` container itu yang meng-include NGINX_HOSTING_DIR
(NGINX_UJI_SAJA=1), dan sertifikat self-signed (SERTIFIKAT_SENDIRI=1).
WordPress e2e berbicara HTTP polos di localhost:8081, jadi klien hosting lama
memakai `klien_http` (Koreksi #17), DNS publik diganti penanya tiruan, dan
verifikasi aktivasi lewat router produksi 127.0.0.1:8091 karena tidak ada
nginx host.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from wpmgr.config import get_settings
from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting import pindah
from wpmgr.hosting import umum as hu
from wpmgr.hosting.dns import Jawaban
from wpmgr.jobs.queue import buat_job
from wpmgr.models import HostingBackup, HostingVps, JobStatus, JobType, StatusHosting
from wpmgr.staging.aman import tulis_atomik
from wpmgr.staging.pembantu import Pembantu, hash_sandi
from wpmgr.worker import proses_satu

from .conftest import AKAR_REPO, WP_URL, klien_http, wpcli

pytestmark = pytest.mark.e2e

DOMAIN = "pindah-e2e.test"
NAMA = "pindah-e2e-test"
DOMAIN_STAGING = "staging.test"
HOST_PRATINJAU = f"vps-{NAMA}.{DOMAIN_STAGING}"
ROUTER = "http://127.0.0.1:8091"
SANDI = "sandi-pratinjau-e2e"
VPS = "169.58.91.181"
UID_DASHBOARD = 33
AKAR_E2E = Path(os.environ.get("WPMGR_E2E_STG_AKAR") or AKAR_REPO / "var" / "e2e-stg")
AKAR_DI_PEMBANTU = "/srv/wpmgr"
EXEC_PEMBANTU = ["docker", "compose", "--profile", "staging", "exec", "-T", "pembantu"]
AWALAN = "docker compose --profile staging exec -T pembantu /usr/local/sbin/wpmgr-staging"
PENANDA_AKAR = ".wpmgr-e2e-akar"
UJI_PHP = "wp-content/mu-plugins/wpmgr-e2e-uji.php"
# Halaman uji: hasil wp_mail() dan REMOTE_ADDR, untuk memeriksa pengaman pratinjau dan A2.
ISI_UJI_PHP = b"""<?php
if ( isset( $_GET['uji_surel'] ) ) {
    add_action( 'init', function () {
        echo 'SUREL=' . var_export( wp_mail( 'tujuan@contoh.test', 'uji', 'isi' ), true );
        exit;
    } );
}
if ( isset( $_GET['uji_ip'] ) ) {
    echo 'IP=' . $_SERVER['REMOTE_ADDR'];
    exit;
}
"""


def _docker(*args: str, check: bool = True) -> str:
    hasil = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=600, check=False)
    if check and hasil.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} gagal: {hasil.stderr}")
    return hasil.stdout.strip()


def _di_pembantu(*perintah: str, masukan: bytes | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run([*EXEC_PEMBANTU, *perintah], input=masukan, capture_output=True, timeout=timeout,
                          check=False)


def _bersihkan_runtime() -> None:
    for label in ("wpmgr.hosting", "wpmgr.staging"):
        nama = _docker("ps", "-aq", "--filter", f"label={label}", check=False).split()
        if nama:
            _docker("rm", "-f", *nama, check=False)
    for jaringan in ("wpmgr-prod", "wpmgr-staging"):
        _docker("network", "rm", jaringan, check=False)
    for volume in ("wpmgr-prod-db", "wpmgr-stg-db"):
        _docker("volume", "rm", volume, check=False)


def _akar_daemon() -> str:
    cid = subprocess.run(["docker", "compose", "--profile", "staging", "ps", "-q", "pembantu"],
                         capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    sumber = _docker("inspect", "--format",
                     '{{range .Mounts}}{{if eq .Destination "/srv/wpmgr"}}{{.Source}}{{end}}{{end}}', cid)
    cocok = re.match(r"([A-Za-z]):[\\/](.*)", sumber)
    if cocok:
        return f"/run/desktop/mnt/host/{cocok.group(1).lower()}/" + cocok.group(2).replace("\\", "/")
    return sumber


def _hapus_akar_e2e() -> None:
    """Sama dengan test_staging: hanya akar yang jelas milik e2e yang dihapus."""
    akar = AKAR_E2E.resolve()
    milik_e2e = (akar.name.startswith("wpmgr-e2e") or (akar / PENANDA_AKAR).is_file()
                 or akar == (AKAR_REPO / "var" / "e2e-stg").resolve())
    if akar.exists() and any(akar.iterdir()) and not milik_e2e:
        pytest.fail(f"WPMGR_E2E_STG_AKAR ({akar}) bukan direktori e2e; tidak dihapus.")
    shutil.rmtree(akar, ignore_errors=True)


def _chown_dashboard(*relatif: str) -> None:
    jalur = [f"{AKAR_DI_PEMBANTU}/{r}" for r in relatif]
    hasil = _di_pembantu("chown", f"{UID_DASHBOARD}:{UID_DASHBOARD}", *jalur)
    assert hasil.returncode == 0, hasil.stderr


@pytest.fixture(scope="module")
def runtime_hosting():
    _bersihkan_runtime()
    _hapus_akar_e2e()
    for d in ("staging", "hosting"):
        (AKAR_E2E / d).mkdir(parents=True)
    (AKAR_E2E / PENANDA_AKAR).write_bytes(b"")
    subprocess.run(["docker", "compose", "--profile", "staging", "up", "-d", "--build", "pembantu"],
                   check=True, capture_output=True, timeout=900)
    konf = "\n".join([
        f"DOMAIN={DOMAIN_STAGING}", "STAGING_DIR=/srv/wpmgr/staging", "KONF_DIR=/srv/wpmgr/etc",
        "CERT_DIR=/srv/wpmgr/certs", "ACME_DIR=/srv/wpmgr/acme", "LE_DIR=/srv/wpmgr/le", "LOG_DIR=/srv/wpmgr/log",
        "ROUTER_PORT=127.0.0.1:8090", "MAIL_PORT=127.0.0.1:8025", "SUBNET=172.31.250.0/24",
        f"PENGGUNA_UID={UID_DASHBOARD}", f"PENGGUNA_GID={UID_DASHBOARD}", "AKAR_LOKAL=/srv/wpmgr",
        f"AKAR_DAEMON={_akar_daemon()}", "TANPA_IPTABLES=1", "TANPA_SERTIFIKAT=1",
        "HOSTING_DIR=/srv/wpmgr/hosting", "PROD_CERT_DIR=/srv/wpmgr/hosting-certs",
        "NGINX_HOSTING_DIR=/srv/wpmgr/nginx-hosting", "BACKUP_DIR=/srv/wpmgr/backup", "IP_PUBLIK=127.0.0.1",
        "NGINX_UJI_SAJA=1", "SERTIFIKAT_SENDIRI=1",
    ]) + "\n"
    (AKAR_E2E / "staging.conf").write_bytes(konf.encode("ascii"))
    _chown_dashboard("staging", "hosting")
    _docker("pull", "--quiet", "wordpress:php8.1-apache")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WPMGR_STAGING_DOMAIN", DOMAIN_STAGING)
        mp.setenv("WPMGR_STAGING_DIR", str(AKAR_E2E / "staging"))
        mp.setenv("WPMGR_STAGING_PEMBANTU_AWALAN", AWALAN)
        mp.setenv("WPMGR_HOSTING_IPV4", VPS)
        mp.setenv("WPMGR_HOSTING_DIR", str(AKAR_E2E / "hosting"))
        get_settings.cache_clear()
        pb = Pembantu.dari_setelan()
        pb.siapkan()
        pb.prod_siapkan()
        yield
        get_settings.cache_clear()
    _bersihkan_runtime()


@pytest.fixture
def tiruan_luar(monkeypatch):
    """Hosting lama lewat HTTP e2e, DNS publik yang sudah menunjuk VPS, dan verifikasi lewat router."""
    monkeypatch.setattr(hu, "klien_lama", lambda site, h: hu.KlienLamaBacaSaja(klien_http(site)))

    class PenanyaVps:
        def tanya(self, resolver, nama, jenis, batas):
            return Jawaban((VPS,)) if jenis == "A" else Jawaban()

    monkeypatch.setattr(dns_mod, "buat_penanya", lambda: PenanyaVps())

    def lewat_router(host):
        r = httpx.get(f"{ROUTER}/", headers={"Host": host}, timeout=30)
        return r.status_code, {k.lower(): v for k, v in r.headers.items()}

    monkeypatch.setattr(pindah, "ambil_halaman_verifikasi", lewat_router)


@pytest.fixture
def home_https():
    """home/siteurl WordPress e2e menjadi https://pindah-e2e.test selama test (syarat periksa_info)."""
    wpcli("option", "update", "home", f"https://{DOMAIN}")
    wpcli("option", "update", "siteurl", f"https://{DOMAIN}")
    try:
        yield
    finally:
        wpcli("option", "update", "home", WP_URL)
        wpcli("option", "update", "siteurl", WP_URL)


def _jalankan(sesi, site_id, tipe, payload=None, batas=8):
    job = buat_job(sesi, site_id, tipe, payload or {})
    for _ in range(batas):
        sesi.refresh(job)
        if job.status not in (JobStatus.pending, JobStatus.running):
            break
        if not proses_satu(sesi, "uji-e2e", buat_klien_fn=klien_http, jenis="staging"):
            break
    sesi.refresh(job)
    return job


def _router(host: str, jalur: str = "/", **kw) -> httpx.Response:
    return httpx.get(f"{ROUTER}{jalur}", headers={"Host": host, **kw.pop("headers", {})}, timeout=30, **kw)


def test_alur_pindah_hosting_lengkap(sesi, site_terpasang, runtime_hosting, tiruan_luar, home_https):
    site = site_terpasang
    wpcli("option", "update", "wpmgr_izinkan_staging", "1")
    site.fitur = klien_http(site).ping()["fitur"]
    assert "staging" in site.fitur
    h = HostingVps(site_id=site.id, nama=NAMA, domain=DOMAIN, dengan_www=False, ip_lama="93.184.216.34",
                   sandi_hash=hash_sandi(SANDI))
    sesi.add(h)
    sesi.commit()
    akar = AKAR_E2E / "hosting" / str(site.id)
    (akar / "files").mkdir(parents=True)
    (akar / "log").mkdir()
    _chown_dashboard(f"hosting/{site.id}", f"hosting/{site.id}/files", f"hosting/{site.id}/log")

    # 1. Pindahkan: salin dari WordPress e2e ke runtime produksi.
    job = _jalankan(sesi, site.id, JobType.pindah_tarik)
    assert job.status == JobStatus.success, job.error
    sesi.refresh(h)
    assert h.status == StatusHosting.pratinjau
    tulis_atomik(akar / "files", UJI_PHP, ISI_UJI_PHP)

    # 2. Pratinjau lewat router: Basic Auth, noindex, email diblokir.
    assert _router(HOST_PRATINJAU).status_code == 401
    r = _router(HOST_PRATINJAU, auth=("pratinjau", SANDI))
    assert r.status_code == 200
    assert "noindex" in r.headers.get("x-robots-tag", "")
    r = _router(HOST_PRATINJAU, "/?uji_surel=1", auth=("pratinjau", SANDI))
    assert "SUREL=false" in r.text
    assert f"https://{HOST_PRATINJAU}" in _router(HOST_PRATINJAU, auth=("pratinjau", SANDI)).text

    # 3. Cek DNS dengan penanya tiruan (A = IPv4 VPS, AAAA dan CAA kosong).
    assert dns_mod.periksa_dns(h).ok

    # 4. Aktivasi penuh dengan nginx -t dan sertifikat sungguhan.
    h.status = StatusHosting.menunggu_dns
    sesi.commit()
    job = _jalankan(sesi, site.id, JobType.pindah_aktifkan)
    assert job.status == JobStatus.success, job.error
    sesi.refresh(h)
    assert h.status == StatusHosting.aktif and h.dilayani_vps_pada is not None
    nginx_domain = (AKAR_E2E / "nginx-hosting" / f"{DOMAIN}.conf").read_text(encoding="utf-8")
    assert f"/srv/wpmgr/hosting-certs/{DOMAIN}/fullchain.pem" in nginx_domain and "vps-" not in nginx_domain

    # 5. Pengaman pratinjau tercabut; IP pengunjung asli sampai ke PHP (A2).
    r = _router(DOMAIN)
    assert r.status_code in (200, 301, 302) and r.status_code != 401
    assert "noindex" not in r.headers.get("x-robots-tag", "")
    assert not (akar / "files" / pindah.MU_PLUGIN_PRATINJAU).exists()
    assert b"WPMGR_PRATINJAU" not in (akar / "files" / "wp-config.php").read_bytes()
    r = _router(DOMAIN, "/?uji_ip=1", headers={"X-Forwarded-For": "198.51.100.23"})
    assert "IP=198.51.100.23" in r.text, r.text[:300]

    # 6. Backup: berkas ada dan sha256 cocok dengan manifest.
    job = _jalankan(sesi, site.id, JobType.backup_hosting, {"manual": True})
    assert job.status == JobStatus.success, job.error
    b = sesi.query(HostingBackup).filter(HostingBackup.site_id == site.id).one()
    dasar = AKAR_E2E / "backup" / str(site.id) / b.stempel
    manifest = json.loads((dasar / "manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256((dasar / "db.sql.gz").read_bytes()).hexdigest() == manifest["sha256_db"] == b.sha256_db
    assert hashlib.sha256((dasar / "files.tar.gz").read_bytes()).hexdigest() == manifest["sha256_file"]

    # 7. Tarik sesudah aktif ditolak oleh dashboard dan oleh skrip.
    job = _jalankan(sesi, site.id, JobType.pindah_tarik)
    assert job.status == JobStatus.failed and pindah.PESAN_SUDAH_DILAYANI in (job.error or ""), job.error
    hasil = _di_pembantu("/usr/local/sbin/wpmgr-staging", "prod-db-impor", NAMA, masukan=b"DROP TABLE wp_options;")
    assert hasil.returncode == 3
    assert _router(DOMAIN).status_code != 500
```

- [ ] **Step 3: Jalankan.** Prasyarat: `docker compose up -d` (db, wpdb, wp, wpcli) dan internet (unduhan image dan wp-cli). Run: `.venv/Scripts/python -m pytest tests/e2e/test_hosting.py -q`. Expected: `1 passed`. Bila hanya pernyataan A2 (`IP=198.51.100.23`) yang gagal, asumsi A2 tidak berlaku untuk image ini: hentikan dan laporkan sebagai temuan (perbaikannya konfigurasi Apache read-only, di luar task ini), jangan melonggarkan test.

- [ ] **Step 4: Seluruh suite.** Run: unit, integrasi, PHPUnit 8.3 dan 7.4, bats, shellcheck, `ruff check .`, lalu `.venv/Scripts/python -m pytest -m e2e -q` (termasuk `test_staging.py`, yang memakai image `pembantu` yang sama). Expected: semua lulus.

- [ ] **Step 5: Commit.**

```bash
git add tests/e2e/pembantu/Dockerfile tests/e2e/test_hosting.py
git commit -m "test(hosting): e2e pindah hosting dengan nginx, sertifikat, dan router produksi sungguhan

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Verifikasi manual bersama pengguna (spec §18.6, sesudah Task 16)

Bukan task subagent; dijalankan operator di VPS bersama pengguna, mengikuti README "Pindah hosting (Lapis 4)":

1. `wpmgr-staging prod-siapkan`, pasang `wpmgr-hosting.conf`, `nginx -t`.
2. Pindahkan `rizkycahayaraya.com` (matikan CDN Hostinger lebih dulu, A12).
3. Pratinjau lewat URL `vps-*` dan lewat berkas hosts.
4. Ubah DNS (A ke `169.58.91.181`, AAAA ke `2a02:c207:2347:2607::1`, CNAME CDN `www` diganti A).
5. Tunggu aktivasi otomatis.
6. Cek dari ponsel ber-IPv6 dan ber-IPv4.
7. Backup manual, lalu satu latihan pemulihan "Pemulihan backup manual" sebelum hosting lama dimatikan.

Setelah itu `dutamakmurabadi.com` dan `scaffoldingsurabayamurah.com`.

