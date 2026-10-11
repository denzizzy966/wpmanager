#!/usr/bin/env bash
# Diagnosa CSS hilang di pratinjau. Hanya membaca. Jalankan:
#   sudo bash deploy/vps/diag-css.sh scaffoldingsurabayamurah-com
set -u
[[ $EUID -eq 0 ]] || { echo "jalankan dengan sudo" >&2; exit 1; }
N="${1:-scaffoldingsurabayamurah-com}"
C="wpp-$N"
HOST="vps-$N.staging.halosocia.my.id"
# Permintaan HTTP dari dalam container lewat PHP (image wordpress belum tentu punya curl).
# MODE=kode -> cetak kode status; MODE=isi -> cetak isi.
# shellcheck disable=SC2016 # kode PHP literal, bukan ekspansi shell
PHPGET='$c=stream_context_create(["http"=>["header"=>"Host: ".getenv("H")."\r\nX-Forwarded-Proto: https\r\n","ignore_errors"=>true,"timeout"=>30]]);
$b=@file_get_contents("http://127.0.0.1".getenv("U"),false,$c);
$k=isset($http_response_header[0])?explode(" ",$http_response_header[0])[1]:"ERR";
echo getenv("MODE")==="isi"?$b:$k;'
minta() { docker exec -e U="$2" -e H="$HOST" -e MODE="$1" "$C" php -r "$PHPGET"; }

echo "=== 1. Pengguna proses web di container"
docker exec "$C" sh -c 'ps -eo user,comm 2>/dev/null | grep -E "apache|php" | sort -u | head -3; id www-data'

echo "=== 2. Pemilik/izin wp-content/litespeed dan uploads"
docker exec "$C" sh -c 'cd /var/www/html/wp-content && ls -ld . litespeed litespeed/css uploads 2>&1; echo "berkas litespeed/css: $(ls litespeed/css 2>/dev/null | wc -l)"'

echo "=== 3. Stylesheet yang dirujuk halaman depan pratinjau + status HTTP-nya"
html=$(minta isi /)
echo "panjang HTML: ${#html}"
printf '%s' "$html" | grep -oE '<link[^>]+stylesheet[^>]*>' | grep -oE "href=[\"'][^\"']+" | cut -c7- | while read -r u; do
  p="${u#https://"$HOST"}"; p="${p#http://"$HOST"}"
  case "$p" in
    /*) echo "$(minta kode "$p")  $p" ;;
    *)  echo "luar  $u" ;;
  esac
done
echo "--- jejak optimasi LiteSpeed di HTML (ucss/ccss/data-optimized):"
printf '%s' "$html" | grep -oE 'litespeed/(ucss|ccss)[^"'"'"' ]*' | sort -u | head
printf '%s' "$html" | grep -o 'data-optimized="[0-9]*"' | sort | uniq -c

echo "=== 4. Log PHP situs (20 baris terakhir)"
SID=$(grep '^SITE_ID=' "/etc/wpmgr-staging/prod/situs/$N" | cut -d= -f2)
tail -20 "/var/lib/wpmgr/hosting/$SID/log/php-error.log" 2>&1
