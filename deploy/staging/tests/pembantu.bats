#!/usr/bin/env bats
# Test skrip pembantu dengan docker tiruan. Jalankan:
#   MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/code" -w /code bats/bats:1.11.0 deploy/staging/tests
#
# Pernyataan negatif yang bukan baris terakhir ditulis `! perintah || false`:
# bats tidak menggagalkan test pada `! perintah` biasa (putusan F13).

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

# Direktori staging milik user dashboard (UID 1000), seperti dibuat `buat`.
buat_situs() {
  mkdir -p "$S/staging/$ID/files" "$S/staging/$ID/ekspor" "$S/staging/$ID/log"
  chown 1000:1000 "$S/staging/$ID" "$S/staging/$ID/files" "$S/staging/$ID/ekspor" "$S/staging/$ID/log"
}

# Jawaban `docker inspect .Mounts` untuk wp-toko: mount buatan `buat` bagi site $1.
tulis_mounts() {
  printf '%s|/var/www/html\n%s|/wpmgr-ekspor\n%s|/wpmgr-log\n%s|/usr/local/bin/wp\n' \
    "$S/staging/$1/files" "$S/staging/$1/ekspor" "$S/staging/$1/log" "$S/etc/wp-cli.phar" \
    > "$PALSU/wadah/wp-toko.mounts"
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

@test "pesan galat tidak menggemakan masukan mentah" {
  run "$SKRIP" jalan 'a$(id)JAHAT'
  [ "$status" -eq 2 ]
  [[ "$output" != *JAHAT* ]]
  run "$SKRIP" JAHAT
  [ "$status" -eq 2 ]
  [[ "$output" != *JAHAT* ]]
  echo 'JAHAT_X=1' >> "$WPMGR_STG_KONF"
  run "$SKRIP" status
  [ "$status" -eq 7 ]
  [[ "$output" != *JAHAT* ]]
}

@test "buat menjalankan container dengan batas sumber daya dan mount yang tepat" {
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 0 ]
  # Putusan R13: seluruh container berjalan sebagai UID dashboard tanpa
  # capability; sysctl membuat Apache non-root boleh listen di port 80.
  diharapkan="[run][-d][--name][wp-toko][--label][wpmgr.staging=situs:toko][--network][wpmgr-staging][--restart][unless-stopped][--memory][384m][--memory-swap][384m][--cpus][1][--pids-limit][256][--user][1000:1000][--cap-drop][ALL][--sysctl][net.ipv4.ip_unprivileged_port_start=0][--security-opt][no-new-privileges][--mount][type=bind,src=$S/staging/$ID/files,dst=/var/www/html][--mount][type=bind,src=$S/staging/$ID/ekspor,dst=/wpmgr-ekspor][--mount][type=bind,src=$S/staging/$ID/log,dst=/wpmgr-log][--mount][type=bind,src=$S/etc/wp-cli.phar,dst=/usr/local/bin/wp,readonly][wordpress@sha256:$D64]"
  grep -qxF "$diharapkan" "$PALSU/docker.log"
  ! grep -q 'APACHE_RUN_' "$PALSU/docker.log" || false
  [ -d "$S/staging/$ID/files" ]
}

@test "buat menolak container bernama sama yang bukan milik staging" {
  printf 'situs:lain' > "$PALSU/wadah/wp-toko"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 3 ]
  [[ "$output" == *"bukan milik staging"* ]]
  ! grep -q '^\[run\]' "$PALSU/docker.log" || false
  ! grep -q '^\[rm\]' "$PALSU/docker.log" || false
}

@test "buat hanya menjalankan ulang container milik sendiri dengan image yang sama" {
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  printf 'wordpress@sha256:%s' "$D64" > "$PALSU/wadah/wp-toko.image"
  printf '1000:1000' > "$PALSU/wadah/wp-toko.user"
  tulis_mounts "$ID"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 0 ]
  grep -qxF "[start][wp-toko]" "$PALSU/docker.log"
  ! grep -q '^\[run\]' "$PALSU/docker.log" || false
  grep -q '^\[inspect\]\[--type\]\[container\]\[--format\]\[{{range .Mounts}}' "$PALSU/docker.log"
}

@test "buat membuat ulang container milik sendiri bila user atau site_id mount-nya berbeda" {
  ID2=11111111-2222-3333-4444-555555555555
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  printf 'wordpress@sha256:%s' "$D64" > "$PALSU/wadah/wp-toko.image"
  printf '1000:1000' > "$PALSU/wadah/wp-toko.user"
  # Mount milik site lain (nama staging dipakai ulang untuk site lain).
  tulis_mounts "$ID2"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 0 ]
  grep -qxF "[rm][-f][wp-toko]" "$PALSU/docker.log"
  grep -qF "[--mount][type=bind,src=$S/staging/$ID/files,dst=/var/www/html]" "$PALSU/docker.log"
  ! grep -q '^\[start\]' "$PALSU/docker.log" || false

  # Container lama tanpa --user (atau user lain) juga dibuat ulang.
  rm -f "$PALSU/docker.log"
  tulis_mounts "$ID"
  printf '' > "$PALSU/wadah/wp-toko.user"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 0 ]
  grep -qxF "[rm][-f][wp-toko]" "$PALSU/docker.log"
  grep -q '^\[run\]' "$PALSU/docker.log"
  ! grep -q '^\[start\]' "$PALSU/docker.log" || false

  # Mount asing ditolak, tidak dibuat ulang dan tidak dijalankan.
  rm -f "$PALSU/docker.log"
  printf '1000:1000' > "$PALSU/wadah/wp-toko.user"
  printf '/etc|/host-etc\n' >> "$PALSU/wadah/wp-toko.mounts"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 3 ]
  ! grep -q '^\[start\]\|^\[rm\]\|^\[run\]' "$PALSU/docker.log" || false
}

@test "buat menolak direktori bind mount yang berupa symlink atau bukan milik user dashboard" {
  # files/ diganti symlink ke direktori lain (milik user yang sama).
  buat_situs
  mkdir -p "$S/lain"
  chown 1000:1000 "$S/lain"
  rmdir "$S/staging/$ID/files"
  ln -s "$S/lain" "$S/staging/$ID/files"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 3 ]
  [[ "$output" == *"GALAT ditolak"* ]]
  ! grep -q '^\[run\]' "$PALSU/docker.log" || false

  # Direktori site itu sendiri berupa symlink.
  rm -rf "$S/staging/$ID"
  mkdir -p "$S/lain/files" "$S/lain/ekspor" "$S/lain/log"
  chown -R 1000:1000 "$S/lain"
  ln -s "$S/lain" "$S/staging/$ID"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 3 ]
  ! grep -q '^\[run\]' "$PALSU/docker.log" || false

  # Direktori milik UID lain.
  rm -f "$S/staging/$ID"
  buat_situs
  chown 1001:1001 "$S/staging/$ID/log"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 3 ]
  ! grep -q '^\[run\]' "$PALSU/docker.log" || false

  # Container milik sendiri juga tidak dijalankan ulang di atas symlink.
  chown 1000:1000 "$S/staging/$ID/log"
  rmdir "$S/staging/$ID/ekspor"
  ln -s /etc "$S/staging/$ID/ekspor"
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  printf 'wordpress@sha256:%s' "$D64" > "$PALSU/wadah/wp-toko.image"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 3 ]
  ! grep -q '^\[start\]' "$PALSU/docker.log" || false
  ! grep -q '^\[run\]' "$PALSU/docker.log" || false
}

@test "jalan memeriksa sumber bind mount container sebelum start" {
  buat_situs
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  mounts() {
    printf '%s|/var/www/html\n%s|/wpmgr-ekspor\n%s|/wpmgr-log\n%s|/usr/local/bin/wp\n' \
      "$1/files" "$1/ekspor" "$1/log" "$S/etc/wp-cli.phar" > "$PALSU/wadah/wp-toko.mounts"
  }
  mounts "$S/staging/$ID"
  run "$SKRIP" jalan toko
  [ "$status" -eq 0 ]
  grep -qxF "[start][wp-toko]" "$PALSU/docker.log"
  rm -f "$PALSU/docker.log"

  # Sumber mount berupa symlink.
  mkdir -p "$S/lain"
  chown 1000:1000 "$S/lain"
  rmdir "$S/staging/$ID/files"
  ln -s "$S/lain" "$S/staging/$ID/files"
  run "$SKRIP" jalan toko
  [ "$status" -eq 3 ]
  [[ "$output" == *"GALAT ditolak"* ]]
  ! grep -q '^\[start\]' "$PALSU/docker.log" || false

  # Sumber mount milik UID lain.
  rm -f "$S/staging/$ID/files"
  mkdir "$S/staging/$ID/files"
  chown 1001:1001 "$S/staging/$ID/files"
  run "$SKRIP" jalan toko
  [ "$status" -eq 3 ]
  ! grep -q '^\[start\]' "$PALSU/docker.log" || false

  # Mount tambahan di luar pola yang diharapkan.
  chown 1000:1000 "$S/staging/$ID/files"
  mounts "$S/staging/$ID"
  printf '/etc|/host-etc\n' >> "$PALSU/wadah/wp-toko.mounts"
  run "$SKRIP" jalan toko
  [ "$status" -eq 3 ]
  ! grep -q '^\[start\]' "$PALSU/docker.log" || false

  # Mount site yang hilang, atau menunjuk tujuan yang tertukar.
  printf '%s|/var/www/html\n%s|/wpmgr-log\n%s|/wpmgr-ekspor\n' \
    "$S/staging/$ID/files" "$S/staging/$ID/ekspor" "$S/staging/$ID/log" > "$PALSU/wadah/wp-toko.mounts"
  run "$SKRIP" jalan toko
  [ "$status" -eq 3 ]
  printf '%s|/var/www/html\n' "$S/staging/$ID/files" > "$PALSU/wadah/wp-toko.mounts"
  run "$SKRIP" jalan toko
  [ "$status" -eq 3 ]
  ! grep -q '^\[start\]' "$PALSU/docker.log" || false
}

@test "jalan dan jeda menolak container asing" {
  printf 'layanan:db' > "$PALSU/wadah/wp-toko"
  run "$SKRIP" jeda toko
  [ "$status" -eq 3 ]
  ! grep -q '^\[stop\]' "$PALSU/docker.log" || false
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
  ! grep -q '^\[exec\]' "$PALSU/docker.log" || false
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
  # Putusan R13: tidak ada exec ke container site tanpa -u UID dashboard.
  grep -q '^\[exec\]' "$PALSU/docker.log"
  ! grep '^\[exec\]' "$PALSU/docker.log" | grep -v '^\[exec\]\(\[-i\]\)\?\[-u\]\[1000:1000\]\[wp-' || false
}

@test "wpcli gagal dilaporkan dengan kode 8" {
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  touch "$PALSU/exec-gagal"
  run "$SKRIP" wpcli toko core version
  [ "$status" -eq 8 ]
  [[ "$output" == *"GALAT wpcli"* ]]
}

@test "operasi panjang dibungkus timeout sedikit di atas tenggat sisi Python" {
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  run "$SKRIP" wpcli toko core version
  [ "$status" -eq 0 ]
  baris="$(grep -F '[docker][exec][-u][1000:1000][wp-toko]' "$PALSU/timeout.log")"
  [[ "$baris" =~ ^\[-k\]\[10\]\[([0-9]+)\]\[docker\] ]]
  (( BASH_REMATCH[1] > 900 && BASH_REMATCH[1] <= 960 ))

  printf 'layanan:db' > "$PALSU/wadah/wpmgr-stg-db"
  printf 'sandi-user' > "$S/etc/db/toko"
  run bash -c "printf 'SELECT 1;' | '$SKRIP' db-impor toko"
  [ "$status" -eq 0 ]
  baris="$(grep -F '[--binary-mode]' "$PALSU/timeout.log")"
  [[ "$baris" =~ ^\[-k\]\[10\]\[([0-9]+)\]\[docker\]\[exec\]\[-i\]\[wpmgr-stg-db\]\[mariadb\] ]]
  (( BASH_REMATCH[1] > 10800 && BASH_REMATCH[1] <= 11100 ))

  run "$SKRIP" sertifikat toko
  [ "$status" -eq 0 ]
  baris="$(grep -F '[certbot]' "$PALSU/timeout.log")"
  [[ "$baris" =~ ^\[-k\]\[10\]\[([0-9]+)\]\[certbot\] ]]
  (( BASH_REMATCH[1] > 300 && BASH_REMATCH[1] <= 360 ))

  # Setiap panggilan docker (juga inspect/start/stop) punya batas waktu.
  jumlah_docker="$(wc -l < "$PALSU/docker.log")"
  jumlah_dibungkus="$(grep -c '\]\[docker\]' "$PALSU/timeout.log")"
  [ "$jumlah_docker" -eq "$jumlah_dibungkus" ]
}

@test "db-buat menulis wp-config staging sebagai user dashboard dengan hak DB terbatas" {
  printf 'layanan:db' > "$PALSU/wadah/wpmgr-stg-db"
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
  [[ "$sql" != *TRIGGER* && "$sql" != *EVENT* && "$sql" != *ROUTINE* && "$sql" != *FILE* ]]
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
  [[ "$(cat "$PALSU/stdin-2")" == *'SET GLOBAL local_infile=0; DROP DATABASE IF EXISTS `stg_toko`; CREATE DATABASE `stg_toko`'* ]]
  [[ "$(cat "$PALSU/stdin-3")" == *"user=stg_toko"* && "$(cat "$PALSU/stdin-3")" == *"password=sandi-user"* ]]
  [ "$(cat "$PALSU/stdin-4")" = "INSERT INTO t VALUES (1);" ]
  grep -q '^\[exec\]\[-i\]\[wpmgr-stg-db\]\[mariadb\]\[--defaults-extra-file=/run/wpmgr-klien-[0-9-]*\.cnf\]\[--binary-mode\]\[--local-infile=0\]\[--max-allowed-packet=64M\]\[stg_toko\]$' "$PALSU/docker.log"
  # Kata sandi tidak pernah muncul di argumen proses.
  ! grep -q 'sandi-user\|rootrahasia' "$PALSU/docker.log" || false
}

@test "db-impor ditolak bila db-buat belum dijalankan" {
  run "$SKRIP" db-impor toko
  [ "$status" -eq 3 ]
}

@test "exec ke layanan bersama ditolak bila labelnya bukan milik staging" {
  printf 'sandi-user' > "$S/etc/db/toko"
  buat_situs
  # Container db tidak ada.
  run bash -c "printf 'SELECT 1;' | '$SKRIP' db-impor toko"
  [ "$status" -eq 3 ]
  # Container db ada tetapi milik pihak lain.
  printf 'situs:db' > "$PALSU/wadah/wpmgr-stg-db"
  run bash -c "printf 'SELECT 1;' | '$SKRIP' db-impor toko"
  [ "$status" -eq 3 ]
  [[ "$output" == *"bukan milik staging"* ]]
  run "$SKRIP" db-buat toko "$ID" wp_
  [ "$status" -eq 3 ]
  [ ! -e "$S/staging/$ID/files/wp-config.php" ]
  run "$SKRIP" db-hapus toko
  [ "$status" -eq 3 ]
  ! grep -q '^\[exec\]' "$PALSU/docker.log" || false

  # Router bukan milik staging: konfigurasi tidak disentuh sama sekali.
  printf 'lama' > "$S/etc/router/conf.d/stg-lama.conf"
  printf '%s' "$(printf 'e%.0s' $(seq 1 64))" > "$S/staging/router/toko.rahasia"
  printf 'staging:%s\n' '$2b$10$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ01234' > "$S/staging/router/toko.htpasswd"
  printf 'layanan:db' > "$PALSU/wadah/wpmgr-stg-router"
  run "$SKRIP" router-muat
  [ "$status" -eq 3 ]
  [ "$(cat "$S/etc/router/conf.d/stg-lama.conf")" = "lama" ]
  [ ! -e "$S/etc/router/conf.d/stg-toko.conf" ]
  ! grep -q '^\[exec\]' "$PALSU/docker.log" || false
}

@test "router-muat merender template tetap dan menolak htpasswd berbahaya" {
  printf 'layanan:router' > "$PALSU/wadah/wpmgr-stg-router"
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
  printf 'layanan:router' > "$PALSU/wadah/wpmgr-stg-router"
  printf 'lama' > "$S/etc/router/conf.d/stg-lama.conf"
  printf '%s' "$(printf 'e%.0s' $(seq 1 64))" > "$S/staging/router/toko.rahasia"
  printf 'staging:%s\n' '$2b$10$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ01234' > "$S/staging/router/toko.htpasswd"
  touch "$PALSU/nginx-gagal"
  run "$SKRIP" router-muat
  [ "$status" -eq 4 ]
  [ "$(cat "$S/etc/router/conf.d/stg-lama.conf")" = "lama" ]
  [ ! -e "$S/etc/router/conf.d/stg-toko.conf" ]
  ! grep -q 'reload' "$PALSU/docker.log" || false
}

@test "sertifikat memakai webroot dan direktori milik wpmgr lalu menyalin hasilnya" {
  run "$SKRIP" sertifikat toko
  [ "$status" -eq 0 ]
  grep -qxF "[certonly][--non-interactive][--agree-tos][-m][admin@contoh.id][--webroot][-w][$S/acme][-d][toko.staging.contoh.id][--cert-name][toko.staging.contoh.id][--config-dir][$S/le/config][--work-dir][$S/le/work][--logs-dir][$S/le/logs][--keep-until-expiring]" "$PALSU/certbot.log"
  [ "$(cat "$S/certs/toko.staging.contoh.id/fullchain.pem")" = "rantai" ]
  # Kunci privat harus terbaca worker nginx host (bukan cuma root), karena
  # ssl_certificate_key di nginx-wpmgr-staging.conf berbasis variabel dan
  # dibaca proses worker (bukan master) setiap handshake (review Task 21).
  # Direktori sertifikat dan fullchain (publik) tetap root:root biasa.
  [ "$(stat -c '%a %U:%G' "$S/certs/toko.staging.contoh.id")" = "755 root:root" ]
  [ "$(stat -c '%a %U:%G' "$S/certs/toko.staging.contoh.id/fullchain.pem")" = "644 root:root" ]
  [ "$(stat -c '%a %U:%G' "$S/certs/toko.staging.contoh.id/privkey.pem")" = "640 root:www-data" ]
  touch "$PALSU/certbot-gagal"
  run "$SKRIP" sertifikat toko
  [ "$status" -eq 5 ]
}

@test "NGINX_GROUP menentukan grup kunci privat dan divalidasi ketat" {
  # Nilai kustom yang sah (grup "root" pasti ada di semua sistem) dipakai
  # apa adanya, bukan default www-data yang diam-diam di-hardcode.
  printf 'NGINX_GROUP=root\n' >> "$WPMGR_STG_KONF"
  run "$SKRIP" sertifikat toko
  [ "$status" -eq 0 ]
  [ "$(stat -c '%a %U:%G' "$S/certs/toko.staging.contoh.id/privkey.pem")" = "640 root:root" ]

  # Pola tidak sah (spasi) ditolak sebelum sampai ke pengecekan grup sistem.
  sed -i 's/^NGINX_GROUP=.*/NGINX_GROUP=grup tidak sah/' "$WPMGR_STG_KONF"
  run "$SKRIP" status
  [ "$status" -eq 7 ]
  [[ "$output" == *"NGINX_GROUP"* ]]

  # Pola sah tetapi grupnya tidak ada di sistem juga ditolak (getent group).
  sed -i 's/^NGINX_GROUP=.*/NGINX_GROUP=grup-yang-tidak-ada/' "$WPMGR_STG_KONF"
  run "$SKRIP" status
  [ "$status" -eq 7 ]
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
  ! grep -q '^\[rm\]' "$PALSU/docker.log" || false
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
  # Server db bersama menolak LOAD DATA LOCAL INFILE (local_infile OFF).
  grep -q '^\[run\]\[-d\]\[--name\]\[wpmgr-stg-db\].*\[--max-allowed-packet=64M\]\[--local-infile=0\]$' "$PALSU/docker.log"
  ! grep -q 'MARIADB_ROOT_PASSWORD=' "$PALSU/docker.log" || false
  # Router (root di container) hanya memasang berkas yang dirender skrip ini,
  # read-only; tidak ada path yang bisa ditulis user dashboard.
  grep -qF "[-v][$S/etc/router/conf.d:/etc/nginx/conf.d:ro]" "$PALSU/docker.log"
  grep -qF "[-v][$S/etc/router/htpasswd:/etc/nginx/wpmgr-htpasswd:ro]" "$PALSU/docker.log"
  ! grep -qF "[-v][$S/staging" "$PALSU/docker.log" || false
  grep -qF 'return 444;' "$S/etc/router/conf.d/00-bawaan.conf"
}

@test "digest.lock yang sudah ada dipakai tanpa unduh ulang" {
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 0 ]
  ! grep -q '^\[pull\]' "$PALSU/docker.log" || false
}

@test "AKAR_DAEMON menerjemahkan path bind mount untuk Docker Desktop" {
  printf 'AKAR_LOKAL=%s\nAKAR_DAEMON=/run/desktop/mnt/host/d/repo/var\n' "$S" >> "$WPMGR_STG_KONF"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 0 ]
  grep -qF "[--mount][type=bind,src=/run/desktop/mnt/host/d/repo/var/staging/$ID/files,dst=/var/www/html]" "$PALSU/docker.log"
  # jalan menerjemahkan balik sumber mount versi daemon sebelum memeriksanya.
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  a=/run/desktop/mnt/host/d/repo/var
  printf '%s|/var/www/html\n%s|/wpmgr-ekspor\n%s|/wpmgr-log\n%s|/usr/local/bin/wp\n' \
    "$a/staging/$ID/files" "$a/staging/$ID/ekspor" "$a/staging/$ID/log" "$a/etc/wp-cli.phar" > "$PALSU/wadah/wp-toko.mounts"
  run "$SKRIP" jalan toko
  [ "$status" -eq 0 ]
  grep -qxF "[start][wp-toko]" "$PALSU/docker.log"
}

@test "daemon Docker gagal tidak dianggap container tidak ada" {
  printf 'situs:toko' > "$PALSU/wadah/wp-toko"
  printf 'lama' > "$S/etc/router/conf.d/stg-toko.conf"
  touch "$PALSU/daemon-gagal"
  # `hapus` akan menghapus konfigurasi staging yang masih berjalan bila
  # kegagalan ini dibaca sebagai "tidak ada".
  run "$SKRIP" hapus toko
  [ "$status" -eq 4 ]
  [[ "$output" == *"GALAT docker"* ]]
  [ "$(cat "$S/etc/router/conf.d/stg-toko.conf")" = "lama" ]
  run "$SKRIP" jalan toko
  [ "$status" -eq 4 ]
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 4 ]
  ! grep -q '^\[rm\]\|^\[run\]\|^\[start\]' "$PALSU/docker.log" || false
  grep -q '^\[inspect\]\[--type\]\[container\]' "$PALSU/docker.log"
}

@test "router-muat memulihkan konfigurasi lama bila pemasangan gagal di tengah" {
  printf 'layanan:router' > "$PALSU/wadah/wpmgr-stg-router"
  printf 'lama' > "$S/etc/router/conf.d/stg-lama.conf"
  printf 'staging:lama\n' > "$S/etc/router/htpasswd/lama"
  printf '%s' "$(printf 'e%.0s' $(seq 1 64))" > "$S/staging/router/toko.rahasia"
  printf 'staging:%s\n' '$2b$10$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ01234' > "$S/staging/router/toko.htpasswd"
  touch "$PALSU/install-gagal"
  run "$SKRIP" router-muat
  [ "$status" -eq 4 ]
  [[ "$output" == *"GALAT docker"* ]]
  [ "$(cat "$S/etc/router/conf.d/stg-lama.conf")" = "lama" ]
  [ "$(cat "$S/etc/router/htpasswd/lama")" = "staging:lama" ]
  [ ! -e "$S/etc/router/conf.d/stg-toko.conf" ]
  [ ! -e "$S/etc/router/htpasswd/toko" ]
  ! grep -q 'reload\|nginx' "$PALSU/docker.log" || false
}

@test "kegagalan tak terduga menjadi GALAT internal kode 9, galat di subshell mempertahankan kodenya" {
  sed -i "s|^MEMINFO=.*|MEMINFO=$S/tidak-ada|" "$WPMGR_STG_KONF"
  run "$SKRIP" status
  [ "$status" -eq 9 ]
  [[ "$output" == *"GALAT internal"* ]]
  [ "$(grep -c '^GALAT' <<< "$output")" -eq 1 ]

  # galat() di dalam $(image ...) tetap keluar dengan kodenya sendiri (7).
  printf 'php81=RUSAK\n' > "$S/etc/digest.lock"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 7 ]
  [ "$(grep -c '^GALAT' <<< "$output")" -eq 1 ]
  [[ "$output" == *"GALAT konfigurasi"* ]]
}

@test "UID/GID root ditolak secara numerik dan dinormalkan" {
  sed -i 's/^PENGGUNA_UID=.*/PENGGUNA_UID=00/' "$WPMGR_STG_KONF"
  run "$SKRIP" status
  [ "$status" -eq 7 ]
  sed -i 's/^PENGGUNA_UID=.*/PENGGUNA_UID=1000/; s/^PENGGUNA_GID=.*/PENGGUNA_GID=0000/' "$WPMGR_STG_KONF"
  run "$SKRIP" status
  [ "$status" -eq 7 ]
  sed -i 's/^PENGGUNA_UID=.*/PENGGUNA_UID=01000/; s/^PENGGUNA_GID=.*/PENGGUNA_GID=001000/' "$WPMGR_STG_KONF"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 0 ]
  grep -qF '[--user][1000:1000]' "$PALSU/docker.log"
}

@test "sumber mount milik root ditolak bila berupa symlink, juga di komponen induknya" {
  # wp-cli.phar berupa symlink.
  mv "$S/etc/wp-cli.phar" "$S/etc/wp-cli.asli"
  ln -s "$S/etc/wp-cli.asli" "$S/etc/wp-cli.phar"
  run "$SKRIP" buat toko 8.1 "$ID"
  [ "$status" -eq 3 ]
  ! grep -q '^\[run\]' "$PALSU/docker.log" || false

  # Komponen induk KONF_DIR/router berupa symlink (ke direktori milik root).
  rm -f "$S/etc/wp-cli.phar"
  mv "$S/etc/wp-cli.asli" "$S/etc/wp-cli.phar"
  mv "$S/etc/router" "$S/router-asli"
  ln -s "$S/router-asli" "$S/etc/router"
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\nmailpit=p@sha256:%s\n' "$D64" "$D64" "$D64" >> "$S/etc/digest.lock"
  run "$SKRIP" siapkan
  [ "$status" -eq 3 ]
  [[ "$output" == *"GALAT ditolak"* ]]
  ! grep -q '^\[run\]' "$PALSU/docker.log" || false
}

@test "SIGTERM dari pemanggil menghentikan docker exec yang sedang berjalan" {
  printf 'layanan:db' > "$PALSU/wadah/wpmgr-stg-db"
  printf 'sandi-user' > "$S/etc/db/toko"
  touch "$PALSU/exec-lama"
  mkfifo "$BATS_TEST_TMPDIR/masuk"
  # stdin tetap terbuka (penulis fd 8), seperti dashboard yang masih mengalirkan SQL.
  "$SKRIP" db-impor toko < "$BATS_TEST_TMPDIR/masuk" > "$BATS_TEST_TMPDIR/keluar" 2>&1 3>&- &
  pid=$!
  exec 8> "$BATS_TEST_TMPDIR/masuk"
  for _ in $(seq 1 100); do
    [[ -s "$PALSU/exec-lama.pid" ]] && break
    sleep 0.1
  done
  anak="$(cat "$PALSU/exec-lama.pid")"
  kill -0 "$anak"
  kill -TERM "$pid"
  rc=0
  wait "$pid" || rc=$?
  exec 8>&-
  for _ in $(seq 1 50); do
    kill -0 "$anak" 2>/dev/null || break
    sleep 0.1
  done
  ! kill -0 "$anak" 2>/dev/null || { kill -KILL "$anak"; false; }
  [ "$rc" -eq 9 ]
  grep -q '^GALAT internal: dihentikan' "$BATS_TEST_TMPDIR/keluar"
  [ "$(grep -c '^GALAT' "$BATS_TEST_TMPDIR/keluar")" -eq 1 ]
}

@test "SIGTERM saat memulihkan router tidak memotong pemulihan konfigurasi lama" {
  printf 'layanan:router' > "$PALSU/wadah/wpmgr-stg-router"
  printf 'lama' > "$S/etc/router/conf.d/stg-lama.conf"
  printf 'staging:lama\n' > "$S/etc/router/htpasswd/lama"
  printf '%s' "$(printf 'e%.0s' $(seq 1 64))" > "$S/staging/router/toko.rahasia"
  printf 'staging:%s\n' '$2b$10$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ01234' > "$S/staging/router/toko.htpasswd"
  touch "$PALSU/nginx-gagal"
  # cp tiruan khusus test ini: salinan pemulihan ke conf.d aktif berhenti
  # sampai test mengirim SIGTERM, supaya sinyal pasti tiba di tengah pemulihan.
  local jeda="$BATS_TEST_TMPDIR/jeda"
  mkdir -p "$jeda"
  cat > "$jeda/cp" <<CP
#!/usr/bin/env bash
if [[ "\${*: -1}" == "$S/etc/router/conf.d/" ]]; then
  touch "$BATS_TEST_TMPDIR/pulih-mulai"
  for _ in \$(seq 1 100); do
    [[ -e "$BATS_TEST_TMPDIR/lanjut" ]] && break
    sleep 0.1
  done
fi
exec $(command -v cp) "\$@"
CP
  chmod +x "$jeda/cp"
  WPMGR_STG_PATH="$jeda:$WPMGR_STG_PATH" "$SKRIP" router-muat > "$BATS_TEST_TMPDIR/keluar" 2>&1 3>&- &
  pid=$!
  for _ in $(seq 1 100); do
    [[ -e "$BATS_TEST_TMPDIR/pulih-mulai" ]] && break
    sleep 0.1
  done
  [ -e "$BATS_TEST_TMPDIR/pulih-mulai" ]
  kill -TERM "$pid"
  sleep 0.3
  touch "$BATS_TEST_TMPDIR/lanjut"
  rc=0
  wait "$pid" || rc=$?
  [ "$rc" -eq 4 ]
  [ "$(cat "$S/etc/router/conf.d/stg-lama.conf")" = "lama" ]
  [ "$(cat "$S/etc/router/htpasswd/lama")" = "staging:lama" ]
  [ ! -e "$S/etc/router/conf.d/stg-toko.conf" ]
}
