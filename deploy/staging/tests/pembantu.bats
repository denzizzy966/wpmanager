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
  [[ "$output" == *"kosong"* ]]
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
  [[ "$output" == *"PROD_ROUTER_PORT harus di 127.0.0.1"* ]]
  sed -i '/^PROD_ROUTER_PORT=/d' "$WPMGR_STG_KONF"
  sed -i 's/^IP_PUBLIK=.*/IP_PUBLIK=169.58.91.300/' "$WPMGR_STG_KONF"
  run "$SKRIP" prod-status
  [ "$status" -eq 7 ]
  [[ "$output" == *"alamat IPv4 tidak sah"* ]]
  sed -i '/^IP_PUBLIK=/d' "$WPMGR_STG_KONF"
  run "$SKRIP" prod-status
  [ "$status" -eq 7 ]
  [[ "$output" == *"IP_PUBLIK wajib diisi"* ]]
}

@test "HOSTING_DIR di dalam STAGING_DIR ditolak" {
  aktifkan_hosting
  for salah in "$S/staging/hosting" "$S/staging" "$S"; do
    sed -i "s#^HOSTING_DIR=.*#HOSTING_DIR=$salah#" "$WPMGR_STG_KONF"
    run "$SKRIP" prod-status
    [ "$status" -eq 7 ]
    [[ "$output" == *"berimpit"* ]]
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
  for baris in 'upload_max_filesize = 64M' 'post_max_size = 68M' 'memory_limit = 256M' \
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
  # Rantai diganti atomik lewat iptables-restore: tidak ada -F/-N/-A langsung.
  ! grep -q '^\[-F\]\|^\[-N\]\|^\[-A\]' "$PALSU/iptables-langsung.log" || false
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
  [[ "$output" == *"bukan milik user dashboard"* ]]
  rm -rf "$S/hosting"
  mkdir -p "$S/lain"
  chown 1000:1000 "$S/lain"
  ln -s "$S/lain" "$S/hosting"
  run "$SKRIP" prod-siapkan
  [ "$status" -eq 7 ]
  [[ "$output" == *"HOSTING_DIR tidak ada atau berupa symlink"* ]]
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

# ---- putaran perbaikan 1 --------------------------------------------------------

@test "prod-siapkan mengganti rantai produksi secara atomik dengan iptables-restore --noflush" {
  aktifkan_hosting
  sed -i '/^TANPA_IPTABLES=/d' "$WPMGR_STG_KONF"
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\n' "$D64" "$D64" >> "$S/etc/digest.lock"
  run "$SKRIP" prod-siapkan
  [ "$status" -eq 0 ]
  harapan='*filter
:WPMGR-PROD-MASUK - [0:0]
-A WPMGR-PROD-MASUK -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A WPMGR-PROD-MASUK -d 169.58.91.181 -p tcp -m multiport --dports 80,443 -j ACCEPT
-A WPMGR-PROD-MASUK -j DROP
COMMIT
*filter
:WPMGR-PROD-ANTAR - [0:0]
-A WPMGR-PROD-ANTAR -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A WPMGR-PROD-ANTAR -s 172.31.251.254 -p tcp --dport 80 -j ACCEPT
-A WPMGR-PROD-ANTAR -d 172.31.251.252 -p tcp --dport 3306 -j ACCEPT
-A WPMGR-PROD-ANTAR -j DROP
COMMIT'
  [ "$(cat "$PALSU/iptables-restore.log")" = "$harapan" ]
  [ "$(cat "$PALSU/iptables-restore.args")" = "$(printf '[--noflush]\n[--noflush]')" ]
  # Tidak ada -F, -N, atau -A langsung pada rantai itu.
  ! grep -q '^\[-F\]\|^\[-N\]\|^\[-A\]' "$PALSU/iptables-langsung.log" || false
  # Lompatan idempoten (-C sebelum -I) dan dipasang sesudah rantainya ada.
  grep -qxF "[-C][INPUT][-i][br-wpmgrprod][-j][WPMGR-PROD-MASUK]" "$PALSU/iptables-langsung.log"
  grep -qxF "[-I][INPUT][-i][br-wpmgrprod][-j][WPMGR-PROD-MASUK]" "$PALSU/iptables-langsung.log"
}

@test "siapkan staging mengganti rantai WPMGR-STG-ANTAR secara atomik" {
  sed -i '/^TANPA_IPTABLES=/d' "$WPMGR_STG_KONF"
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\nmailpit=p@sha256:%s\n' "$D64" "$D64" "$D64" >> "$S/etc/digest.lock"
  rm -f "$S/etc/wp-cli.phar"
  run "$SKRIP" siapkan
  [ "$status" -eq 0 ]
  harapan='*filter
:WPMGR-STG-ANTAR - [0:0]
-A WPMGR-STG-ANTAR -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A WPMGR-STG-ANTAR -s 172.31.250.254 -p tcp --dport 80 -j ACCEPT
-A WPMGR-STG-ANTAR -d 172.31.250.252 -p tcp --dport 3306 -j ACCEPT
-A WPMGR-STG-ANTAR -d 172.31.250.253 -p tcp --dport 1025 -j ACCEPT
-A WPMGR-STG-ANTAR -j DROP
COMMIT'
  [ "$(cat "$PALSU/iptables-restore.log")" = "$harapan" ]
  [ "$(cat "$PALSU/iptables-restore.args")" = '[--noflush]' ]
  ! grep -q '^\[-F\]\|^\[-N\]\|^\[-A\]' "$PALSU/iptables-langsung.log" || false
}

@test "siapkan menolak bila iptables-restore gagal dan tidak memasang lompatan" {
  sed -i '/^TANPA_IPTABLES=/d' "$WPMGR_STG_KONF"
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\nmailpit=p@sha256:%s\n' "$D64" "$D64" "$D64" >> "$S/etc/digest.lock"
  rm -f "$S/etc/wp-cli.phar"
  mkdir -p "$BATS_TEST_TMPDIR/gagal"
  printf '#!/usr/bin/env bash\ncat >/dev/null\nexit 1\n' > "$BATS_TEST_TMPDIR/gagal/iptables-restore"
  chmod +x "$BATS_TEST_TMPDIR/gagal/iptables-restore"
  WPMGR_STG_PATH="$BATS_TEST_TMPDIR/gagal:$WPMGR_STG_PATH" run "$SKRIP" siapkan
  [ "$status" -eq 4 ]
  [[ "$output" == *"rantai iptables tidak dapat dipasang"* ]]
  ! grep -q '^\[-I\]\[DOCKER-USER\]\[-i\]\[br-wpmgrstg\]\[-o\]' "$PALSU/iptables.log" || false
}

@test "HOSTING_DIR: sibling berawalan sama diizinkan, jalur tidak dinormalkan ditolak" {
  aktifkan_hosting
  sed -i "s#^HOSTING_DIR=.*#HOSTING_DIR=$S/staging-hosting#" "$WPMGR_STG_KONF"
  run "$SKRIP" prod-status
  [ "$status" -eq 0 ]
  for salah in "$S//hosting" "$S/./hosting" "$S/hosting/" "$S/hosting/."; do
    sed -i "s#^HOSTING_DIR=.*#HOSTING_DIR=$salah#" "$WPMGR_STG_KONF"
    run "$SKRIP" prod-status
    [ "$status" -eq 7 ]
    [[ "$output" == *"tidak dinormalkan"* ]]
  done
}

@test "jalur STAGING_DIR/KONF_DIR/BACKUP_DIR tidak dinormalkan ditolak, juga lewat kunci produksi" {
  aktifkan_hosting
  sed -i "s#^STAGING_DIR=.*#STAGING_DIR=$S//staging#" "$WPMGR_STG_KONF"
  run "$SKRIP" prod-status
  [ "$status" -eq 7 ]
  [[ "$output" == *"tidak dinormalkan"* ]]
  sed -i "s#^STAGING_DIR=.*#STAGING_DIR=$S/staging#" "$WPMGR_STG_KONF"
  sed -i "s#^BACKUP_DIR=.*#BACKUP_DIR=$S/backup/#" "$WPMGR_STG_KONF"
  run "$SKRIP" prod-status
  [ "$status" -eq 7 ]
  [[ "$output" == *"tidak dinormalkan"* ]]
}

@test "HOSTING_DIR yang memuat STAGING_DIR ditolak dan direktori milik root di dalam HOSTING_DIR/STAGING_DIR ditolak" {
  aktifkan_hosting
  sed -i "s#^HOSTING_DIR=.*#HOSTING_DIR=$S#" "$WPMGR_STG_KONF"
  run "$SKRIP" prod-status
  [ "$status" -eq 7 ]
  [[ "$output" == *"berimpit"* ]]
  sed -i "s#^HOSTING_DIR=.*#HOSTING_DIR=$S/hosting#" "$WPMGR_STG_KONF"
  for kunci in BACKUP_DIR PROD_CERT_DIR NGINX_HOSTING_DIR; do
    for dalam in "$S/hosting/x" "$S/staging/x"; do
      sed -i "s#^$kunci=.*#$kunci=$dalam#" "$WPMGR_STG_KONF"
      run "$SKRIP" prod-status
      [ "$status" -eq 7 ]
      [[ "$output" == *"direktori milik root"* ]]
    done
    sed -i "s#^$kunci=.*#$kunci=$S/aman#" "$WPMGR_STG_KONF"
  done
  sed -i "s#^KONF_DIR=.*#KONF_DIR=$S/hosting/etc#" "$WPMGR_STG_KONF"
  run "$SKRIP" prod-status
  [ "$status" -eq 7 ]
  [[ "$output" == *"direktori milik root"* ]]
}

@test "prod-siapkan menolak jaringan wpmgr-prod yang jembatan atau subnetnya salah sebelum menyentuh iptables" {
  aktifkan_hosting
  sed -i '/^TANPA_IPTABLES=/d' "$WPMGR_STG_KONF"
  printf 'mariadb=m@sha256:%s\nnginx=n@sha256:%s\n' "$D64" "$D64" >> "$S/etc/digest.lock"
  printf 'br-lain' > "$PALSU/jaringan-prod-jembatan"
  run "$SKRIP" prod-siapkan
  [ "$status" -eq 7 ]
  [[ "$output" == *"jembatan atau subnetnya tidak sesuai"* ]]
  rm -f "$PALSU/jaringan-prod-jembatan"
  printf '10.9.0.0/24' > "$PALSU/jaringan-prod-subnet"
  run "$SKRIP" prod-siapkan
  [ "$status" -eq 7 ]
  [[ "$output" == *"jembatan atau subnetnya tidak sesuai"* ]]
  [ ! -e "$PALSU/iptables.log" ]
  ! grep -q '^\[run\]\|^\[start\]\|^\[rm\]' "$PALSU/docker.log" || false
}

@test "konfigurasi staging-saja dengan SUBNET 172.31.251.0/24 tetap lolos (M8)" {
  sed -i 's#^SUBNET=.*#SUBNET=172.31.251.0/24#' "$WPMGR_STG_KONF"
  run "$SKRIP" status
  [ "$status" -eq 0 ]
}

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
  [[ "$output" != *"subperintah tidak dikenal"* ]]
  [ -z "$(docker_log)" ]
  [ -z "$(ls -A "$S/etc/prod/situs")" ]
}

@test "prod-buat menjalankan wpp-<nama> dengan batas dan mount produksi lalu menulis state" {
  aktifkan_hosting
  run "$SKRIP" prod-buat toko 8.1 "$ID" "$DOM" 1
  [ "$status" -eq 0 ]
  diharapkan="[run][-d][--name][wpp-toko][--label][wpmgr.hosting=situs:toko][--network][wpmgr-prod][--restart][unless-stopped][--memory][512m][--memory-swap][512m][--cpus][1][--pids-limit][256][--user][1000:1000][--cap-drop][ALL][--sysctl][net.ipv4.ip_unprivileged_port_start=0][--security-opt][no-new-privileges][--log-opt][max-size=10m][--log-opt][max-file=3][--mount][type=bind,src=$S/hosting/$ID/files,dst=/var/www/html][--mount][type=bind,src=$S/hosting/$ID/log,dst=/wpmgr-log][--mount][type=bind,src=$S/etc/prod/php.ini,dst=/usr/local/etc/php/conf.d/zz-wpmgr.ini,readonly][wordpress@sha256:$D64]"
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
  # Penolakan tidak meninggalkan state (M1): nama, domain, dan site tetap bebas.
  [ -z "$(ls -A "$S/etc/prod/situs")" ]
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
  printf 'bawaan' > "$S/etc/prod/router/conf.d/00-bawaan.conf"
  printf '%s\n' "$HTPASSWD" > "$S/etc/prod/router/htpasswd/lama"
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
  # Pemulihan juga mengembalikan htpasswd lama dan konfigurasi bawaan (M5).
  [ "$(cat "$S/etc/prod/router/conf.d/00-bawaan.conf")" = "bawaan" ]
  [ "$(cat "$S/etc/prod/router/htpasswd/lama")" = "$HTPASSWD" ]
  [ ! -e "$S/etc/prod/router/htpasswd/toko" ]
  ! grep -q 'reload' "$PALSU/docker.log" || false
}

@test "prod-router-muat membaca htpasswd dashboard lewat setpriv" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  printf '%s
' "$HTPASSWD" > "$S/hosting/router/toko.htpasswd"
  run "$SKRIP" prod-router-muat
  [ "$status" -eq 0 ]
  grep -qF "[--reuid=1000][--regid=1000][--clear-groups][--][head][-c][1024][$S/hosting/router/toko.htpasswd]" "$PALSU/setpriv.log"
}

@test "prod-buat menolak nama kedua untuk site_id yang sama dan tidak menulis apa pun" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  run "$SKRIP" prod-buat toko-dua 8.1 "$ID" baru.co.id 1
  [ "$status" -eq 3 ]
  [ "$(ls "$S/etc/prod/situs")" = "toko" ]
  [ ! -e "$S/etc/prod/situs/toko-dua" ]
  ! grep -q '^\[run\]\|^\[rm\]\|^\[start\]' "$PALSU/docker.log" || false
  [ ! -e "$S/hosting/$ID/files" ]
}

@test "prod-buat pratinjau membuat ulang container bila image berubah dan hanya start bila sama" {
  aktifkan_hosting
  buat_situs_prod
  tulis_state_prod toko pratinjau
  wadah_prod wpp-toko situs:toko
  printf 'wordpress@sha256:lama' > "$PALSU/wadah/wpp-toko.image"
  printf '1000:1000' > "$PALSU/wadah/wpp-toko.user"
  mounts_prod "$ID"
  run "$SKRIP" prod-buat toko 8.1 "$ID" "$DOM" 1
  [ "$status" -eq 0 ]
  grep -qxF "[rm][-f][wpp-toko]" "$PALSU/docker.log"
  grep -q '^\[run\]\[-d\]\[--name\]\[wpp-toko\]' "$PALSU/docker.log"
  ! grep -q '^\[start\]' "$PALSU/docker.log" || false
  : > "$PALSU/docker.log"
  printf 'wordpress@sha256:%s' "$D64" > "$PALSU/wadah/wpp-toko.image"
  run "$SKRIP" prod-buat toko 8.1 "$ID" "$DOM" 1
  [ "$status" -eq 0 ]
  grep -qxF "[start][wpp-toko]" "$PALSU/docker.log"
  ! grep -q '^\[run\]\|^\[rm\]' "$PALSU/docker.log" || false
}

@test "prod-router-muat, prod-db-buat, dan prod-db-impor mengambil kunci router sebelum bekerja" {
  aktifkan_hosting
  buat_situs_prod
  tulis_state_prod toko pratinjau
  printf '%s
' "$HTPASSWD" > "$S/hosting/router/toko.htpasswd"
  run "$SKRIP" prod-router-muat
  [ "$status" -eq 0 ]
  grep -qE '^\[-w\]\[30\]\[-x\]\[[0-9]+\]$' "$PALSU/flock.log"
  [ "$(wc -l < "$PALSU/flock.log")" -eq 1 ]
  [ -e "$S/etc/prod/router.lock" ]
  rm -f "$PALSU/flock.log"
  run "$SKRIP" prod-db-buat toko wp_
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$PALSU/flock.log")" -eq 1 ]
  rm -f "$PALSU/flock.log"
  run bash -c "printf 'SELECT 1;' | '$SKRIP' prod-db-impor toko"
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$PALSU/flock.log")" -eq 1 ]
  # prod-jalan tidak menyentuh router, jadi tidak mengambil kunci.
  rm -f "$PALSU/flock.log"
  wadah_prod wpp-toko situs:toko
  mounts_prod "$ID"
  run "$SKRIP" prod-jalan toko
  [ "$status" -eq 0 ]
  [ ! -e "$PALSU/flock.log" ]
}

@test "kunci router yang tenggatnya habis menolak sebelum mengubah apa pun" {
  aktifkan_hosting
  buat_situs_prod
  tulis_state_prod toko pratinjau
  touch "$PALSU/flock-gagal"
  : > "$PALSU/docker.log"
  run "$SKRIP" prod-router-muat
  [ "$status" -eq 3 ]
  run "$SKRIP" prod-db-buat toko wp_
  [ "$status" -eq 3 ]
  run bash -c "printf 'SELECT 1;' | '$SKRIP' prod-db-impor toko"
  [ "$status" -eq 3 ]
  [ -z "$(docker_log)" ]
  [ ! -e "$S/hosting/$ID/files/wp-config.php" ]
}

@test "prod-db-buat dan prod-db-impor memeriksa MODE di bawah kunci router" {
  aktifkan_hosting
  buat_situs_prod
  tulis_state_prod toko aktif
  run "$SKRIP" prod-db-buat toko wp_
  [ "$status" -eq 3 ]
  [ "$(wc -l < "$PALSU/flock.log")" -eq 1 ]
}

# ---- nginx host per domain, sertifikat domain, aktivasi, hapus (Task 3) --------

NGF() { printf '%s' "$S/nginx-hosting/$DOM.conf"; }

# Nomor baris pertama di timeout.log (semua perintah lewat `dibatasi`) yang
# cocok dengan regex $1; dipakai untuk menguji urutan langkah antar-perintah.
baris_timeout() { grep -nE "$1" "$PALSU/timeout.log" | head -n 1 | cut -d: -f1; }

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

# Preflight M14 (spec §18.4 "argumen tidak sah ditolak di setiap prod-*"):
# subperintah Task 1-2 yang belum punya test argumen. Penjaga regresi.
@test "argumen prod-jalan, prod-db-impor, prod-router-muat, dan prod-status divalidasi" {
  aktifkan_hosting
  for nama in "" "Toko" "../x" "a;id" "$(printf 'a\nb')" "$(printf 'a%.0s' $(seq 1 37))"; do
    run "$SKRIP" prod-jalan "$nama"
    [ "$status" -eq 2 ]
    run bash -c "printf 'SELECT 1;' | \"\$0\" prod-db-impor \"\$1\"" "$SKRIP" "$nama"
    [ "$status" -eq 2 ]
  done
  run "$SKRIP" prod-jalan toko lain
  [ "$status" -eq 2 ]
  [[ "$output" != *"subperintah tidak dikenal"* ]]
  run "$SKRIP" prod-db-impor
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-router-muat toko
  [ "$status" -eq 2 ]
  run "$SKRIP" prod-status tambahan
  [ "$status" -eq 2 ]
  [ -z "$(docker_log)" ]
  [ ! -e "$PALSU/flock.log" ]
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
  # Preflight M13: tenggat kunci 60 s, di dalam TIMEOUT Python 120 s.
  grep -qxF '[-w][60][-x][9]' "$PALSU/flock.log"
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
  chmod 0644 "$(NGF)"
  touch "$PALSU/nginx-t-gagal"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 10 ]
  [[ "$output" == *"GALAT nginx"* ]]
  [ "$(cat "$(NGF)")" = "LAMA" ]
  # Dipulihkan dengan mode aslinya, bukan 0600 dari umask skrip.
  [ "$(stat -c %a "$(NGF)")" = 644 ]
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

@test "prod-domain: pra-cek membaca server_name sebaris, berkutip, dan gagal tertutup tanpa penanda berkas" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  # Satu baris memuat beberapa direktif.
  printf '# configuration file /etc/nginx/sites-enabled/a.conf:\nserver { listen 80; server_name a.id %s; }\n' "$DOM" > "$PALSU/nginx-T"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 3 ]
  # Nama berkutip dan dipisah tab.
  printf '# configuration file /etc/nginx/sites-enabled/a.conf:\nserver {\n\tserver_name\t"www.%s";\n}\n' "$DOM" > "$PALSU/nginx-T"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 3 ]
  # Konfigurasi sebelum penanda berkas pertama dianggap milik pihak lain.
  printf 'server {\n    server_name %s;\n}\n' "$DOM" > "$PALSU/nginx-T"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 3 ]
  [ ! -e "$(NGF)" ]
  [ ! -e "$PALSU/systemctl.log" ]
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

@test "prod-domain membaca state sesudah kunci nginx diambil" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  printf 'LAMA' > "$(NGF)"
  touch "$PALSU/flock-gagal-9"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 10 ]
  [ "$(cat "$(NGF)")" = "LAMA" ]
  [ ! -e "$PALSU/nginx.log" ]
  # Kunci lebih dulu: situs yang belum ada pun menunggu kunci, sehingga state
  # yang dihapus/diaktifkan proses pemegang kunci tidak pernah dibaca basi.
  run "$SKRIP" prod-domain belum-ada
  [ "$status" -eq 10 ]
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

@test "prod-sertifikat memasang sertifikat di bawah kunci nginx" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  touch "$PALSU/flock-gagal-9"
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 10 ]
  [ ! -e "$S/hcerts/$DOM/fullchain.pem" ]
  rm -f "$PALSU/flock-gagal-9"
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 0 ]
  grep -qxF '[-w][60][-x][9]' "$PALSU/flock.log"
}

@test "prod-sertifikat situs aktif: sertifikat baru yang ditolak nginx dipulihkan ke yang lama" {
  aktifkan_hosting
  tulis_state_prod toko aktif
  mkdir -p "$S/hcerts/$DOM"
  chmod 0700 "$S/hcerts/$DOM"
  printf 'rantai-lama' > "$S/hcerts/$DOM/fullchain.pem"
  printf 'kunci-lama' > "$S/hcerts/$DOM/privkey.pem"
  chmod 0600 "$S/hcerts/$DOM/privkey.pem"
  touch "$PALSU/nginx-t-gagal"
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 10 ]
  [ "$(cat "$S/hcerts/$DOM/fullchain.pem")" = "rantai-lama" ]
  [ "$(cat "$S/hcerts/$DOM/privkey.pem")" = "kunci-lama" ]
  [ "$(stat -c %a:%u:%g "$S/hcerts/$DOM/privkey.pem")" = "600:0:0" ]
  [ ! -e "$PALSU/systemctl.log" ]
  # Reload gagal: sertifikat lama dipulihkan, nginx diuji ulang, lalu dimuat
  # ulang lagi (pola hapus_domain).
  rm -f "$PALSU/nginx-t-gagal"
  : > "$PALSU/nginx.log"
  touch "$PALSU/reload-gagal"
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 10 ]
  [ "$(cat "$S/hcerts/$DOM/fullchain.pem")" = "rantai-lama" ]
  [ "$(cat "$S/hcerts/$DOM/privkey.pem")" = "kunci-lama" ]
  [ "$(grep -c '^\[reload\]\[nginx\]$' "$PALSU/systemctl.log")" -eq 2 ]
  [ "$(grep -c '^\[-t\]$' "$PALSU/nginx.log")" -eq 2 ]
}

# Siapkan situs aktif dengan pasangan sertifikat lama (fullchain 0644,
# privkey 0600) di PROD_CERT_DIR.
sert_lama_aktif() {
  aktifkan_hosting
  tulis_state_prod toko aktif
  mkdir -p "$S/hcerts/$DOM"
  chmod 0700 "$S/hcerts/$DOM"
  printf 'rantai-lama' > "$S/hcerts/$DOM/fullchain.pem"
  printf 'kunci-lama' > "$S/hcerts/$DOM/privkey.pem"
  chmod 0644 "$S/hcerts/$DOM/fullchain.pem"
  chmod 0600 "$S/hcerts/$DOM/privkey.pem"
}

sert_masih_lama() {
  [ "$(cat "$S/hcerts/$DOM/fullchain.pem")" = "rantai-lama" ]
  [ "$(cat "$S/hcerts/$DOM/privkey.pem")" = "kunci-lama" ]
  [ "$(stat -c %a:%u:%g "$S/hcerts/$DOM/fullchain.pem")" = "644:0:0" ]
  [ "$(stat -c %a:%u:%g "$S/hcerts/$DOM/privkey.pem")" = "600:0:0" ]
  [ -z "$(ls -A "$S/hcerts/$DOM" | grep '^\.' || true)" ]
}

@test "prod-sertifikat: kegagalan di antara dua rename memulihkan pasangan lama lewat jebakan EXIT (I1)" {
  sert_lama_aktif
  # rename fullchain berhasil, rename privkey gagal: tanpa pemulihan situs
  # aktif tertinggal dengan fullchain baru + privkey lama.
  printf '/privkey.pem' > "$PALSU/mv-gagal"
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 9 ]
  sert_masih_lama
  [ ! -e "$PALSU/systemctl.log" ]
  # Penerbitan pertama yang terpotong tidak meninggalkan setengah pasangan,
  # dan percobaan berikutnya tetap melaporkan `terbit`.
  rm -rf "$S/hcerts/$DOM"
  sed -i 's/^MODE=.*/MODE=pratinjau/' "$S/etc/prod/situs/toko"
  printf '/privkey.pem' > "$PALSU/mv-gagal"
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 9 ]
  [ ! -e "$S/hcerts/$DOM/fullchain.pem" ]
  [ ! -e "$S/hcerts/$DOM/privkey.pem" ]
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 0 ]
  [ "$output" = "terbit" ]
}

@test "prod-sertifikat: install berkas kedua gagal, pasangan lama tidak tersentuh (I1)" {
  sert_lama_aktif
  # Cocok dengan install privkey mana pun (langsung ke privkey.pem atau ke
  # berkas sementaranya): install fullchain sudah berhasil lebih dulu.
  printf 'privkey.pem' > "$PALSU/install-gagal-pola"
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -ne 0 ]
  sert_masih_lama
}

@test "prod-sertifikat: SIGTERM saat nginx -t memulihkan pasangan sertifikat lama (I1)" {
  sert_lama_aktif
  touch "$PALSU/nginx-t-tahan"
  "$SKRIP" prod-sertifikat toko > "$BATS_TEST_TMPDIR/keluar" 2>&1 3>&- &
  pid=$!
  for _ in $(seq 1 100); do
    [[ -s "$PALSU/nginx-t-tahan.pid" ]] && break
    sleep 0.1
  done
  [ -s "$PALSU/nginx-t-tahan.pid" ]
  # Sertifikat baru sudah terpasang saat nginx -t berjalan.
  [ "$(cat "$S/hcerts/$DOM/fullchain.pem")" = "rantai" ]
  mulai=$SECONDS
  kill -TERM "$pid"
  rc=0
  wait "$pid" || rc=$?
  # Sinyal diteruskan segera, bukan sesudah nginx -t selesai sendiri.
  (( SECONDS - mulai < 30 ))
  [ "$rc" -eq 9 ]
  grep -q '^GALAT internal: dihentikan' "$BATS_TEST_TMPDIR/keluar"
  sert_masih_lama
  [ ! -e "$PALSU/systemctl.log" ]
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

@test "prod-aktifkan: files/ yang bukan direktori asli milik user ditolak sebagai prasyarat" {
  siap_aktifkan
  mv "$S/hosting/$ID/files" "$S/hosting/$ID/files-asli"
  ln -s "$S/hosting/$ID/files-asli" "$S/hosting/$ID/files"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 3 ]
  [ "$(cat "$S/hosting/$ID/files-asli/wp-config.php")" = "ASLI" ]
  ! grep -q '^\[exec\]' "$PALSU/docker.log" || false
  [ ! -e "$PALSU/nginx.log" ]
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
  # Preflight M12 (spec §18.4 "urutan (1)-(4)"): wp-config ditukar sebelum
  # router dimuat ulang, router sebelum nginx host diuji lalu dimuat ulang.
  b_cfg="$(baris_timeout '\[setpriv\].*\[mv\]\[-fT\]\[--\].*/wp-config\.php\]$')"
  b_router="$(baris_timeout '\[docker\]\[exec\]\[wpmgr-prod-router\]\[nginx\]\[-s\]\[reload\]$')"
  b_uji="$(baris_timeout '^\[-k\]\[10\]\[[0-9]+\]\[nginx\]\[-t\]$')"
  b_muat="$(baris_timeout '\[systemctl\]\[reload\]\[nginx\]$')"
  [ -n "$b_cfg" ] && [ -n "$b_router" ] && [ -n "$b_uji" ] && [ -n "$b_muat" ]
  (( b_cfg < b_router && b_router < b_uji && b_uji < b_muat ))
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 0 ]
  grep -qxF 'MODE=aktif' "$S/etc/prod/situs/toko"
}

@test "prod-aktifkan mengambil kunci router lalu kunci nginx sebelum langkah apa pun" {
  siap_aktifkan
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 0 ]
  # Dua kunci saja (kunci_router idempoten di dalam muat_router_prod), urutan
  # router -> nginx. Urutan yang sama dipakai di semua subperintah.
  [ "$(wc -l < "$PALSU/flock.log")" -eq 2 ]
  sed -n 1p "$PALSU/flock.log" | grep -qE '^\[-w\]\[30\]\[-x\]\[[0-9]+\]$'
  [ "$(sed -n 1p "$PALSU/flock.log")" != '[-w][30][-x][9]' ]
  [ "$(sed -n 2p "$PALSU/flock.log")" = '[-w][60][-x][9]' ]
}

@test "prod-aktifkan: kunci router atau kunci nginx sibuk keluar 3 tanpa perubahan" {
  siap_aktifkan
  touch "$PALSU/flock-gagal"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 3 ]
  rm -f "$PALSU/flock-gagal"
  # Putusan L4: belum ada perubahan, jadi kunci nginx yang sibuk juga 3.
  touch "$PALSU/flock-gagal-9"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 3 ]
  [[ "$output" == *"GALAT ditolak"* ]]
  [ "$(cat "$S/hosting/$ID/files/wp-config.php")" = "ASLI" ]
  grep -qxF 'MODE=pratinjau' "$S/etc/prod/situs/toko"
  [ ! -e "$PALSU/nginx.log" ]
  ! grep -q '^\[exec\]' "$PALSU/docker.log" || false
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
  # Putusan L5: MODE=aktif sudah ditulis lebih dulu (pagar satu arah).
  grep -qxF 'MODE=aktif' "$S/etc/prod/situs/toko"
  # Percobaan ulang menuntaskan langkah sisanya.
  wadah_prod wpmgr-prod-router layanan:router
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 0 ]
  grep -qxF "    server_name $DOM www.$DOM;" "$S/etc/prod/router/conf.d/prd-toko.conf"
  grep -qxF "    ssl_certificate     $S/hcerts/$DOM/fullchain.pem;" "$(NGF)"
}

@test "prod-aktifkan: nginx gagal di langkah (3) memulihkan berkas pratinjau, percobaan ulang menuntaskan" {
  siap_aktifkan
  printf 'PRATINJAU' > "$(NGF)"
  chmod 0644 "$(NGF)"
  touch "$PALSU/nginx-t-gagal"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 10 ]
  [ "$(cat "$(NGF)")" = "PRATINJAU" ]
  [ "$(stat -c %a "$(NGF)")" = 644 ]
  grep -qxF 'MODE=aktif' "$S/etc/prod/situs/toko"
  [ ! -e "$PALSU/systemctl.log" ]
  [ -z "$(ls -A "$S/etc/prod/nginx-cadangan")" ]
  rm -f "$PALSU/nginx-t-gagal"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 0 ]
  grep -qxF "    ssl_certificate     $S/hcerts/$DOM/fullchain.pem;" "$(NGF)"
}

@test "prod-aktifkan: MODE=aktif ditulis sebelum langkah (1); berhenti sesudahnya lalu diulang tuntas (L5)" {
  siap_aktifkan
  # Langkah (1) gagal (rename wp-config): state sudah aktif, wp-config lama.
  printf '/wp-config.php' > "$PALSU/mv-gagal"
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 9 ]
  grep -qxF 'MODE=aktif' "$S/etc/prod/situs/toko"
  [ "$(cat "$S/hosting/$ID/files/wp-config.php")" = "ASLI" ]
  [ ! -e "$S/etc/prod/router/conf.d/prd-toko.conf" ]
  # Situs yang sudah aktif menolak impor/hapus selama aktivasi belum tuntas.
  run bash -c "printf 'SELECT 1;' | \"\$0\" prod-db-impor toko" "$SKRIP"
  [ "$status" -eq 3 ]
  run "$SKRIP" prod-hapus toko
  [ "$status" -eq 3 ]
  # Percobaan ulang dengan MODE=aktif menuntaskan langkah (1)-(3).
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 0 ]
  [ "$output" = "aktif" ]
  grep -qF "define( 'DB_NAME', 'prd_toko' );" "$S/hosting/$ID/files/wp-config.php"
  ! grep -q 'auth_basic' "$S/etc/prod/router/conf.d/prd-toko.conf" || false
  grep -qxF "    ssl_certificate     $S/hcerts/$DOM/fullchain.pem;" "$(NGF)"
}

@test "prod-domain: SIGTERM saat nginx -t memulihkan berkas lama lewat jebakan EXIT" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  printf 'LAMA' > "$(NGF)"
  chmod 0644 "$(NGF)"
  touch "$PALSU/nginx-t-tahan"
  "$SKRIP" prod-domain toko > "$BATS_TEST_TMPDIR/keluar" 2>&1 3>&- &
  pid=$!
  for _ in $(seq 1 100); do
    [[ -s "$PALSU/nginx-t-tahan.pid" ]] && break
    sleep 0.1
  done
  [ -s "$PALSU/nginx-t-tahan.pid" ]
  [ "$(cat "$(NGF)")" != "LAMA" ]
  mulai=$SECONDS
  kill -TERM "$pid"
  rc=0
  wait "$pid" || rc=$?
  # Sinyal diteruskan segera, bukan sesudah nginx -t selesai sendiri.
  (( SECONDS - mulai < 30 ))
  [ "$rc" -eq 9 ]
  # Baris GALAT sampai ke stderr asli walau stderr nginx -t sedang dialihkan.
  [ "$(grep -c '^GALAT internal: dihentikan' "$BATS_TEST_TMPDIR/keluar")" -eq 1 ]
  [ "$(cat "$(NGF)")" = "LAMA" ]
  [ "$(stat -c %a "$(NGF)")" = 644 ]
  [ -z "$(ls -A "$S/nginx-hosting" | grep '^\.' || true)" ]
  [ ! -e "$PALSU/systemctl.log" ]
}

@test "prod-domain: pra-cek menolak wildcard induk, wildcard akhir, dan regex yang memuat nama domain" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  for nama in "*.co.id" ".co.id" "*.id" "toko.*" "www.toko.co.*" '~^(www\.)?toko\.co\.id$' '~^TOKO\.'; do
    printf '# configuration file /etc/nginx/sites-enabled/a.conf:\nserver {\n    server_name a.id %s;\n}\n' "$nama" > "$PALSU/nginx-T"
    run "$SKRIP" prod-domain toko
    [ "$status" -eq 3 ]
    [ ! -e "$(NGF)" ]
  done
  # Bukan induk dan bukan regex yang memuat nama domain: diterima.
  printf '# configuration file /etc/nginx/sites-enabled/a.conf:\nserver {\n    server_name *.lain.co.id lain.* ~^lain\\.id$ _;\n}\n' > "$PALSU/nginx-T"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 0 ]
}

@test "prod-domain: conflicting server name untuk host pratinjau vps-<nama> ditolak" {
  aktifkan_hosting
  tulis_state_prod toko pratinjau
  printf 'nginx: [warn] conflicting server name "vps-toko.staging.contoh.id" on 0.0.0.0:443, ignored\n' > "$PALSU/nginx-t-peringatan"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 10 ]
  [ ! -e "$(NGF)" ]
}

@test "direktori milik root yang bisa ditulis grup/pengguna lain ditolak" {
  siap_aktifkan
  chmod 0777 "$S/nginx-hosting"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 3 ]
  [ ! -e "$(NGF)" ]
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 3 ]
  chmod 0755 "$S/nginx-hosting"
  chmod 0770 "$S/hcerts"
  run "$SKRIP" prod-sertifikat toko
  [ "$status" -eq 3 ]
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 3 ]
  chmod 0700 "$S/hcerts"
  chmod 0757 "$S/etc/prod"
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 3 ]
  run "$SKRIP" prod-aktifkan toko
  [ "$status" -eq 3 ]
  [ "$(cat "$S/hosting/$ID/files/wp-config.php")" = "ASLI" ]
  grep -qxF 'MODE=pratinjau' "$S/etc/prod/situs/toko"
  [ ! -e "$PALSU/systemctl.log" ]
}

@test "prod-hapus menghapus sertifikat domain dan lineage certbot-nya (L6)" {
  siap_aktifkan
  mkdir -p "$S/le/config/renewal"
  printf 'x' > "$S/le/config/renewal/$DOM.conf"
  run "$SKRIP" prod-hapus toko
  [ "$status" -eq 0 ]
  [ ! -e "$S/hcerts/$DOM" ]
  grep -qxF "[delete][--non-interactive][--cert-name][$DOM][--config-dir][$S/le/config][--work-dir][$S/le/work][--logs-dir][$S/le/logs]" "$PALSU/certbot.log"
}

@test "prod-hapus: certbot delete gagal tetap sukses dengan peringatan; tanpa lineage tidak memanggil certbot (L6)" {
  siap_aktifkan
  mkdir -p "$S/le/config/renewal"
  printf 'x' > "$S/le/config/renewal/$DOM.conf"
  touch "$PALSU/certbot-gagal"
  run "$SKRIP" prod-hapus toko
  [ "$status" -eq 0 ]
  [[ "$output" == *"PERINGATAN"* ]]
  [ ! -e "$S/hcerts/$DOM" ]
  [ ! -e "$S/etc/prod/situs/toko" ]
  rm -f "$PALSU/certbot-gagal" "$PALSU/certbot.log" "$S/le/config/renewal/$DOM.conf"
  siap_aktifkan
  run "$SKRIP" prod-hapus toko
  [ "$status" -eq 0 ]
  [ ! -e "$PALSU/certbot.log" ]
  [ ! -e "$S/hcerts/$DOM" ]
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
  # MODE dibaca di bawah kunci router (serial dengan prod-aktifkan dan
  # render router), lalu kunci nginx: urutan sama dengan prod-aktifkan.
  [ "$(wc -l < "$PALSU/flock.log")" -eq 2 ]
  sed -n 1p "$PALSU/flock.log" | grep -qE '^\[-w\]\[30\]\[-x\]\[[0-9]+\]$'
  [ "$(sed -n 2p "$PALSU/flock.log")" = '[-w][60][-x][9]' ]
}

@test "prod-domain dan prod-hapus bertenggat 360 s (TIMEOUT Python 300 + 60): kunci bisa menunggu 90 s" {
  siap_aktifkan
  run "$SKRIP" prod-domain toko
  [ "$status" -eq 0 ]
  baris="$(grep -F '[flock][-w][60][-x][9]' "$PALSU/timeout.log")"
  [[ "$baris" =~ ^\[-k\]\[10\]\[([0-9]+)\]\[flock\] ]]
  (( BASH_REMATCH[1] > 300 && BASH_REMATCH[1] <= 360 ))

  rm -f "$PALSU/timeout.log"
  run "$SKRIP" prod-hapus toko
  [ "$status" -eq 0 ]
  baris="$(grep -F '[flock][-w][30][-x]' "$PALSU/timeout.log")"
  [[ "$baris" =~ ^\[-k\]\[10\]\[([0-9]+)\]\[flock\] ]]
  (( BASH_REMATCH[1] > 300 && BASH_REMATCH[1] <= 360 ))
}

@test "prod-hapus: reload gagal memulihkan berkas domain, menguji ulang, lalu reload lagi (M10)" {
  siap_aktifkan
  printf 'PRATINJAU' > "$(NGF)"
  touch "$PALSU/reload-gagal"
  run "$SKRIP" prod-hapus toko
  [ "$status" -eq 10 ]
  [ "$(cat "$(NGF)")" = "PRATINJAU" ]
  [ "$(grep -c '^\[-t\]$' "$PALSU/nginx.log")" -eq 2 ]
  [ "$(grep -c '^\[reload\]\[nginx\]$' "$PALSU/systemctl.log")" -eq 2 ]
  [ -e "$S/etc/prod/situs/toko" ]
  ! grep -q '^\[rm\]' "$PALSU/docker.log" || false
}

@test "prod-hapus: nginx -t gagal sesudah berkas dihapus memulihkan berkas tanpa reload" {
  siap_aktifkan
  printf 'PRATINJAU' > "$(NGF)"
  touch "$PALSU/nginx-t-gagal"
  run "$SKRIP" prod-hapus toko
  [ "$status" -eq 10 ]
  [ "$(cat "$(NGF)")" = "PRATINJAU" ]
  [ ! -e "$PALSU/systemctl.log" ]
  [ -e "$S/etc/prod/situs/toko" ]
}
