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
  # certs/acme/le meniru direktori yang sudah dibuat root oleh `siapkan`
  # (install -d) sebelum `sertifikat` pernah dipanggil di VPS sungguhan.
  mkdir -p "$S/staging/router" "$S/etc/router/conf.d" "$S/etc/router/htpasswd" "$S/etc/db" "$S/log" \
    "$S/certs" "$S/acme" "$S/le"
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
BRNF=$S/brnf
TANPA_IPTABLES=1
KONF
  printf 'MemTotal:       11000000 kB\nMemAvailable:    4194304 kB\n' > "$S/meminfo"
  printf 'rootrahasia' > "$S/etc/db-root"
  printf '1
' > "$S/brnf"
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
  # Ditulis lewat berkas sementara baru lalu mv -fT (putusan I3), semuanya sebagai user dashboard.
  grep -q "^\[--reuid=1000\]\[--regid=1000\]\[--clear-groups\]\[--\]\[tee\]\[$S/staging/$ID/files/.wp-config.[A-Za-z0-9]*\]$" "$PALSU/setpriv.log"
  grep -q "^\[--reuid=1000\]\[--regid=1000\]\[--clear-groups\]\[--\]\[mv\]\[-fT\]\[--\]\[$S/staging/$ID/files/.wp-config.[A-Za-z0-9]*\]\[$cfg\]$" "$PALSU/setpriv.log"
  [ -z "$(ls -A "$S/staging/$ID/files" | grep '^\.wp-config\.' || true)" ]
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

@test "router-muat: tautan masuk diverifikasi dari query, bukan dari nilai cookie yang tersimpan" {
  # nginx menyimpan $secure_link sekali per request. Bila server membacanya
  # dari cookie lebih dulu, `secure_link $arg_m,$arg_e` di location masuk tidak
  # pernah dievaluasi: tautan SSO sah ditolak 403 tanpa cookie, dan tautan
  # palsu diterima bila cookie sah (ditemukan e2e Task 22). Sumbernya harus
  # dipilih per URI SEBELUM $secure_link dibaca, dengan satu direktif saja.
  printf 'layanan:router' > "$PALSU/wadah/wpmgr-stg-router"
  printf '%s' "$(printf 'e%.0s' $(seq 1 64))" > "$S/staging/router/toko.rahasia"
  printf 'staging:%s\n' '$2b$10$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ01234' > "$S/staging/router/toko.htpasswd"
  run "$SKRIP" router-muat
  [ "$status" -eq 0 ]
  konf="$S/etc/router/conf.d/stg-toko.conf"
  [ "$(grep -c '^ *secure_link ' "$konf")" -eq 1 ]
  [ "$(grep -c '^ *secure_link_md5 ' "$konf")" -eq 1 ]
  grep -qF 'secure_link $wpmgr_m,$wpmgr_e;' "$konf"
  # Urutan: sumber cookie, lalu query khusus URI masuk, lalu secure_link, lalu if pertama yang membacanya.
  n_cookie="$(grep -nF 'set $wpmgr_m $cookie_wpmgr_stg_m;' "$konf" | cut -d: -f1)"
  n_uri="$(grep -nF 'if ($uri = /__wpmgr_masuk) {' "$konf" | cut -d: -f1)"
  n_arg="$(grep -nF 'set $wpmgr_m $arg_m;' "$konf" | cut -d: -f1)"
  n_sl="$(grep -nF 'secure_link $wpmgr_m,$wpmgr_e;' "$konf" | cut -d: -f1)"
  n_baca="$(grep -nF '$secure_link = "1"' "$konf" | head -n 1 | cut -d: -f1)"
  [ -n "$n_cookie" ] && [ -n "$n_uri" ] && [ -n "$n_arg" ] && [ -n "$n_baca" ]
  [ "$n_cookie" -lt "$n_uri" ] && [ "$n_uri" -lt "$n_arg" ] && [ "$n_arg" -lt "$n_sl" ] && [ "$n_sl" -lt "$n_baca" ]
  grep -qF 'set $wpmgr_e $arg_e;' "$konf"
  grep -qF 'set $wpmgr_e $cookie_wpmgr_stg_e;' "$konf"
}

@test "router-muat: redirect router relatif supaya tidak turun ke http di balik TLS" {
  # Router hanya bicara http di belakang nginx host (TLS). Tanpa
  # absolute_redirect off, `return 302 /?wpmgr_sso=...` menjadi
  # Location: http://<host>/..., dan proxy_redirect bawaan host tidak
  # menulis ulangnya (ditemukan e2e Task 22).
  printf 'layanan:router' > "$PALSU/wadah/wpmgr-stg-router"
  printf '%s' "$(printf 'e%.0s' $(seq 1 64))" > "$S/staging/router/toko.rahasia"
  printf 'staging:%s\n' '$2b$10$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ01234' > "$S/staging/router/toko.htpasswd"
  run "$SKRIP" router-muat
  [ "$status" -eq 0 ]
  grep -qxF '    absolute_redirect off;' "$S/etc/router/conf.d/stg-toko.conf"
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
  # Nilai kustom yang sah ("bin", gid 1, bukan gid 0/GID_W) dipakai apa
  # adanya, bukan default www-data yang diam-diam di-hardcode. "root" TIDAK
  # dipakai di sini karena gid-nya (0) sekarang ditolak (lihat test lain).
  printf 'NGINX_GROUP=bin\n' >> "$WPMGR_STG_KONF"
  run "$SKRIP" sertifikat toko
  [ "$status" -eq 0 ]
  [ "$(stat -c '%a %U:%G' "$S/certs/toko.staging.contoh.id/privkey.pem")" = "640 root:bin" ]

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

@test "NGINX_GROUP ditolak bila grup root, grup dashboard, atau grup tambahan pengguna dashboard" {
  # Kasus 1: NGINX_GROUP adalah grup root (gid 0) -- privkey bisa dibaca
  # terlalu banyak proses sistem.
  printf 'NGINX_GROUP=root\n' >> "$WPMGR_STG_KONF"
  run "$SKRIP" status
  [ "$status" -eq 7 ]
  [[ "$output" == *"NGINX_GROUP"* ]]

  # Kasus 2: gid NGINX_GROUP sama dengan grup UTAMA user dashboard
  # (GID_W=1000 dari PENGGUNA_GID di setup()) -- proses dashboard sendiri
  # akan bisa membaca kunci privat staging manapun.
  echo "grupsama:x:1000:" >> /etc/group
  sed -i 's/^NGINX_GROUP=.*/NGINX_GROUP=grupsama/' "$WPMGR_STG_KONF"
  run "$SKRIP" status
  [ "$status" -eq 7 ]
  [[ "$output" == *"NGINX_GROUP"* ]]

  # Kasus 3: NGINX_GROUP bukan grup utama pengguna dashboard, tapi pengguna
  # itu (UID_W) adalah anggota TAMBAHANnya (id -G). Butuh entri /etc/passwd
  # sungguhan untuk UID 1000 supaya UID itu bisa diterjemahkan ke nama dulu.
  echo "cobapengguna:x:1000:1000::/nonexistent:/bin/false" >> /etc/passwd
  echo "grupekstra:x:1234:cobapengguna" >> /etc/group
  sed -i 's/^NGINX_GROUP=.*/NGINX_GROUP=grupekstra/' "$WPMGR_STG_KONF"
  run "$SKRIP" status
  [ "$status" -eq 7 ]
  [[ "$output" == *"NGINX_GROUP"* ]]
}

@test "guard NGINX_GROUP gagal TERTUTUP bila getent/id gagal, bukan 'tidak ditemukan'" {
  # getent passwd gagal karena NSS (kode 1, bukan "tidak ditemukan" = 2):
  # ditolak, bukan diam-diam dilewati (putusan review Task 21, putaran 3).
  printf '1' > "$PALSU/getent-passwd-gagal"
  run "$SKRIP" status
  [ "$status" -eq 7 ]
  [[ "$output" == *"pengguna dashboard tidak dapat diperiksa"* ]]
  rm -f "$PALSU/getent-passwd-gagal"

  # getent passwd "tidak ditemukan" (kode 2) TETAP diterima: hanya GID
  # utama yang berlaku di kasus itu, dan sudah dicek di tempat lain.
  printf '2' > "$PALSU/getent-passwd-gagal"
  run "$SKRIP" status
  [ "$status" -eq 0 ]
  rm -f "$PALSU/getent-passwd-gagal"

  # User dashboard DITEMUKAN (getent passwd sungguhan, bukan tiruan gagal),
  # tapi grupnya tidak bisa diperiksa (id -G gagal): ditolak, bukan
  # dianggap "tidak ada anggota tambahan".
  echo "cobapengguna:x:1000:1000::/nonexistent:/bin/false" >> /etc/passwd
  touch "$PALSU/id-gagal"
  run "$SKRIP" status
  [ "$status" -eq 7 ]
  [[ "$output" == *"pengguna dashboard tidak dapat diperiksa"* ]]
}

@test "sertifikat menolak CERT_DIR/ACME_DIR/LE_DIR yang bukan milik root atau berupa symlink" {
  # CERT_DIR sebagai symlink: cek_mount_root menolak sebelum menulis apa
  # pun (sertifikat/kunci belum pernah diminta ke certbot tiruan).
  rm -rf "$S/certs"
  ln -s /tmp "$S/certs"
  run "$SKRIP" sertifikat toko
  [ "$status" -eq 3 ]
  [ ! -e "$PALSU/certbot.log" ]
  rm -f "$S/certs"
  mkdir -p "$S/certs"

  # ACME_DIR milik user lain (bukan root): user dashboard tidak boleh bisa
  # menukar direktori ini di bawah skrip root.
  chown 1000:1000 "$S/acme"
  run "$SKRIP" sertifikat toko
  [ "$status" -eq 3 ]
  [ ! -e "$PALSU/certbot.log" ]
  chown 0:0 "$S/acme"

  # LE_DIR sebagai symlink.
  rm -rf "$S/le"
  ln -s /tmp "$S/le"
  run "$SKRIP" sertifikat toko
  [ "$status" -eq 3 ]
  [ ! -e "$PALSU/certbot.log" ]
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
  # Isolasi antar-container (R25): rantai sendiri, alamat tetap di puncak subnet.
  iptables_urut='[-A][WPMGR-STG-ANTAR][-m][conntrack][--ctstate][ESTABLISHED,RELATED][-j][ACCEPT]
[-A][WPMGR-STG-ANTAR][-s][172.31.250.254][-p][tcp][--dport][80][-j][ACCEPT]
[-A][WPMGR-STG-ANTAR][-d][172.31.250.252][-p][tcp][--dport][3306][-j][ACCEPT]
[-A][WPMGR-STG-ANTAR][-d][172.31.250.253][-p][tcp][--dport][1025][-j][ACCEPT]
[-A][WPMGR-STG-ANTAR][-j][DROP]'
  [ "$(grep '^\[-A\]' "$PALSU/iptables.log")" = "$iptables_urut" ]
  grep -qxF "[-I][DOCKER-USER][-i][br-wpmgrstg][-o][br-wpmgrstg][-j][WPMGR-STG-ANTAR]" "$PALSU/iptables.log"
  # Rantai terisi penuh SEBELUM lompatan dipasang.
  [ "$(grep -n '^\[-I\]\[DOCKER-USER\]\[-i\]\[br-wpmgrstg\]\[-o\]' "$PALSU/iptables.log" | cut -d: -f1)" -gt "$(grep -n '^\[-A\]\[WPMGR-STG-ANTAR\]\[-j\]\[DROP\]' "$PALSU/iptables.log" | cut -d: -f1)" ]
  # Alamat tetap untuk db, mail, dan router.
  grep -q '^\[run\]\[-d\]\[--name\]\[wpmgr-stg-db\].*\[--network\]\[wpmgr-staging\]\[--ip\]\[172.31.250.252\]' "$PALSU/docker.log"
  grep -q '^\[run\]\[-d\]\[--name\]\[wpmgr-stg-mail\].*\[--network\]\[wpmgr-staging\]\[--ip\]\[172.31.250.253\]' "$PALSU/docker.log"
  grep -q '^\[run\]\[-d\]\[--name\]\[wpmgr-stg-router\].*\[--network\]\[wpmgr-staging\]\[--ip\]\[172.31.250.254\]' "$PALSU/docker.log"
  # Mailpit dikunci kata sandi; kredensial tidak muncul di argumen docker.
  [[ "$(cat "$S/etc/mail-auth")" =~ ^wpmgr:[0-9a-f]{48}$ ]]
  [ "$(stat -c %a "$S/etc/mail-auth")" = 600 ]
  grep -q '^\[run\]\[-d\]\[--name\]\[wpmgr-stg-mail\].*\[--env-file\]\[[^]]*\]' "$PALSU/docker.log"
  ! grep -q 'MP_UI_AUTH\|wpmgr:[0-9a-f]\{48\}' "$PALSU/docker.log" || false
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

# ---- isolasi jaringan (putusan R25) ---------------------------------------------

@test "siapkan memasang lompatan ke rantai isolasi dengan -C sebelum -I dan mengosongkan rantai dulu" {
  sed -i '/^TANPA_IPTABLES=/d' "$WPMGR_STG_KONF"
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\nmailpit=p@sha256:%s\n' "$D64" "$D64" "$D64" >> "$S/etc/digest.lock"
  rm -f "$S/etc/wp-cli.phar"
  run "$SKRIP" siapkan
  [ "$status" -eq 0 ]
  # iptables tiruan: -C selalu "belum ada", jadi -I harus mengikuti -C.
  c="$(grep -n '^\[-C\]\[DOCKER-USER\]\[-i\]\[br-wpmgrstg\]\[-o\]\[br-wpmgrstg\]\[-j\]\[WPMGR-STG-ANTAR\]$' "$PALSU/iptables.log" | cut -d: -f1)"
  i="$(grep -n '^\[-I\]\[DOCKER-USER\]\[-i\]\[br-wpmgrstg\]\[-o\]\[br-wpmgrstg\]\[-j\]\[WPMGR-STG-ANTAR\]$' "$PALSU/iptables.log" | cut -d: -f1)"
  [ "$c" -lt "$i" ]
  f="$(grep -n '^\[-F\]\[WPMGR-STG-ANTAR\]$' "$PALSU/iptables.log" | cut -d: -f1)"
  a="$(grep -n '^\[-A\]\[WPMGR-STG-ANTAR\]' "$PALSU/iptables.log" | head -1 | cut -d: -f1)"
  [ "$f" -lt "$a" ]
}

@test "siapkan membuat ulang layanan lama tanpa alamat tetap dan hanya menjalankan ulang yang sudah sesuai" {
  sed -i '/^TANPA_IPTABLES=/d' "$WPMGR_STG_KONF"
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\nmailpit=p@sha256:%s\n' "$D64" "$D64" "$D64" >> "$S/etc/digest.lock"
  rm -f "$S/etc/wp-cli.phar"
  printf 'wpmgr:%s' "$(printf 'c%.0s' $(seq 1 48))" > "$S/etc/mail-auth"
  # db: milik staging, tanpa alamat tetap (container lama) -> dibuat ulang.
  printf 'layanan:db' > "$PALSU/wadah/wpmgr-stg-db"
  # router: alamat tetap sudah benar -> hanya dijalankan ulang.
  printf 'layanan:router' > "$PALSU/wadah/wpmgr-stg-router"
  printf '172.31.250.254' > "$PALSU/wadah/wpmgr-stg-router.ip"
  # mail: alamat benar tetapi tanpa auth -> dibuat ulang.
  printf 'layanan:mail' > "$PALSU/wadah/wpmgr-stg-mail"
  printf '172.31.250.253' > "$PALSU/wadah/wpmgr-stg-mail.ip"
  printf 'PATH=/x\n' > "$PALSU/wadah/wpmgr-stg-mail.env"
  run "$SKRIP" siapkan
  [ "$status" -eq 0 ]
  grep -qxF "[rm][-f][wpmgr-stg-db]" "$PALSU/docker.log"
  grep -q '^\[run\]\[-d\]\[--name\]\[wpmgr-stg-db\].*\[--ip\]\[172.31.250.252\]' "$PALSU/docker.log"
  grep -qxF "[rm][-f][wpmgr-stg-mail]" "$PALSU/docker.log"
  grep -q '^\[run\]\[-d\]\[--name\]\[wpmgr-stg-mail\].*\[--ip\]\[172.31.250.253\]' "$PALSU/docker.log"
  grep -qxF "[start][wpmgr-stg-router]" "$PALSU/docker.log"
  ! grep -qxF "[rm][-f][wpmgr-stg-router]" "$PALSU/docker.log" || false
  ! grep -q '^\[run\]\[-d\]\[--name\]\[wpmgr-stg-router\]' "$PALSU/docker.log" || false

  # Kedua kali: semuanya sudah sesuai (alamat tetap + env auth) -> hanya start.
  : > "$PALSU/docker.log"
  printf '172.31.250.252' > "$PALSU/wadah/wpmgr-stg-db.ip"
  printf 'PATH=/x\nMP_UI_AUTH=%s\n' "$(cat "$S/etc/mail-auth")" > "$PALSU/wadah/wpmgr-stg-mail.env"
  run "$SKRIP" siapkan
  [ "$status" -eq 0 ]
  ! grep -q '^\[rm\]\|^\[run\]' "$PALSU/docker.log" || false
  grep -qxF "[start][wpmgr-stg-mail]" "$PALSU/docker.log"
}

@test "alamat tetap layanan mengikuti SUBNET dan SUBNET yang tidak sah ditolak" {
  sed -i 's#^SUBNET=.*#SUBNET=10.99.4.0/22#' "$WPMGR_STG_KONF"
  sed -i '/^TANPA_IPTABLES=/d' "$WPMGR_STG_KONF"
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\nmailpit=p@sha256:%s\n' "$D64" "$D64" "$D64" >> "$S/etc/digest.lock"
  rm -f "$S/etc/wp-cli.phar"
  run "$SKRIP" siapkan
  [ "$status" -eq 0 ]
  grep -qxF "[-A][WPMGR-STG-ANTAR][-s][10.99.7.254][-p][tcp][--dport][80][-j][ACCEPT]" "$PALSU/iptables.log"
  grep -qxF "[-A][WPMGR-STG-ANTAR][-d][10.99.7.252][-p][tcp][--dport][3306][-j][ACCEPT]" "$PALSU/iptables.log"
  for salah in 10.99.4.0/8 10.99.4.0/30 300.1.1.0/24; do
    sed -i "s#^SUBNET=.*#SUBNET=$salah#" "$WPMGR_STG_KONF"
    run "$SKRIP" status
    [ "$status" -eq 7 ]
  done
}

@test "mail-kredensial hanya mencetak kredensial yang dibuat siapkan" {
  run "$SKRIP" mail-kredensial
  [ "$status" -eq 3 ]
  printf 'wpmgr:%s' "$(printf 'd%.0s' $(seq 1 48))" > "$S/etc/mail-auth"
  run "$SKRIP" mail-kredensial
  [ "$status" -eq 0 ]
  [ "$output" = "wpmgr:$(printf 'd%.0s' $(seq 1 48))" ]
  printf 'sembarang; rm -rf /' > "$S/etc/mail-auth"
  run "$SKRIP" mail-kredensial
  [ "$status" -eq 7 ]
  [[ "$output" == *"kredensial mail rusak"* && "$output" != *sembarang* ]]
  run "$SKRIP" mail-kredensial ekstra
  [ "$status" -eq 2 ]
}

# ---- wp-config tidak mengikuti symlink (putusan I3) --------------------------------

@test "db-buat tidak menulis lewat symlink wp-config.php yang ditanam di files/" {
  printf 'layanan:db' > "$PALSU/wadah/wpmgr-stg-db"
  mkdir -p "$S/staging/$ID/files"
  printf 'JANGAN-DISENTUH' > "$S/target-luar"
  ln -s "$S/target-luar" "$S/staging/$ID/files/wp-config.php"
  run "$SKRIP" db-buat toko "$ID" wp_
  [ "$status" -eq 0 ]
  [ "$(cat "$S/target-luar")" = "JANGAN-DISENTUH" ]
  [ ! -L "$S/staging/$ID/files/wp-config.php" ]
  grep -qF "define( 'DB_NAME', 'stg_toko' );" "$S/staging/$ID/files/wp-config.php"
}

# ---- br_netfilter harus aktif agar isolasi berlaku ---------------------------------

siapkan_dengan_iptables() {
  sed -i '/^TANPA_IPTABLES=/d' "$WPMGR_STG_KONF"
  printf 'mariadb=m@sha256:%s
nginx=n@sha256:%s
mailpit=p@sha256:%s
' "$D64" "$D64" "$D64" >> "$S/etc/digest.lock"
  rm -f "$S/etc/wp-cli.phar"
}

@test "siapkan menerima bridge-nf-call-iptables=1 tanpa modprobe" {
  siapkan_dengan_iptables
  run "$SKRIP" siapkan
  [ "$status" -eq 0 ]
  [ ! -e "$PALSU/modprobe.log" ]
}

@test "siapkan menolak bridge-nf-call-iptables=0 dengan kode 7 dan tidak menulis sysctl" {
  siapkan_dengan_iptables
  printf '0
' > "$S/brnf"
  run "$SKRIP" siapkan
  [ "$status" -eq 7 ]
  [[ "$output" == *"br_netfilter/bridge-nf-call-iptables harus aktif"* ]]
  [ "$(cat "$S/brnf")" = 0 ]
  # Aturan sudah terpasang sebelum pemeriksaan (pemeriksaan sesudah aturan).
  grep -q 'WPMGR-STG-ANTAR' "$PALSU/iptables.log"
}

@test "siapkan menolak berkas sysctl yang tidak ada sesudah mencoba modprobe sekali" {
  siapkan_dengan_iptables
  rm -f "$S/brnf"
  run "$SKRIP" siapkan
  [ "$status" -eq 7 ]
  [ "$(grep -c '^\[br_netfilter\]$' "$PALSU/modprobe.log")" -eq 1 ]
  [ ! -e "$S/brnf" ]
}

@test "modprobe yang memunculkan sysctl bernilai 1 diterima" {
  siapkan_dengan_iptables
  rm -f "$S/brnf"
  touch "$PALSU/modprobe-buat"
  run "$SKRIP" siapkan
  [ "$status" -eq 0 ]
}

@test "TANPA_IPTABLES=1 melewati pemeriksaan br_netfilter" {
  printf '0
' > "$S/brnf"
  printf 'mariadb=m@sha256:%s
nginx=n@sha256:%s
mailpit=p@sha256:%s
' "$D64" "$D64" "$D64" >> "$S/etc/digest.lock"
  rm -f "$S/etc/wp-cli.phar"
  run "$SKRIP" siapkan
  [ "$status" -eq 0 ]
}
