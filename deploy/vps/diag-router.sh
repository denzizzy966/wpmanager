#!/usr/bin/env bash
# Diagnosa gagal "Memuat ulang router hosting". Tidak mencetak kata sandi atau isi htpasswd.
# Jalankan: sudo bash deploy/vps/diag-router.sh
[[ $EUID -eq 0 ]] || { echo "jalankan dengan sudo" >&2; exit 1; }
garis() { echo; echo "=== $*"; }

garis "1. Log worker staging (60 baris terakhir)"
journalctl -u wpmgr-worker@staging --since "-2h" --no-pager | tail -60

garis "2. Container produksi"
docker ps -a --format '{{.Names}}  {{.Status}}  {{.Ports}}' | grep -E 'wpp-|wpmgr-prod' || echo "(tidak ada)"

garis "3. Mount router"
docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Mode}}){{"\n"}}{{end}}' wpmgr-prod-router 2>&1

garis "4. Isi conf.d/htpasswd router (nama berkas saja)"
ls -la /etc/wpmgr-staging/prod/router/ /etc/wpmgr-staging/prod/router/conf.d /etc/wpmgr-staging/prod/router/htpasswd 2>&1
ls -la /var/lib/wpmgr/hosting/router 2>&1

garis "5. nginx -t di router (konfigurasi sekarang)"
docker exec wpmgr-prod-router nginx -t 2>&1

garis "6. Log container router"
docker logs --tail 30 wpmgr-prod-router 2>&1

garis "7. State situs (tanpa rahasia)"
for f in /etc/wpmgr-staging/prod/situs/*; do echo "--- $f"; grep -vE 'SANDI|PASS|KUNCI' "$f"; done 2>&1

garis "8. Coba muat ulang router (aman diulang; konfigurasi lama dipulihkan bila gagal)"
wpmgr-staging prod-router-muat; echo "exit=$?"

garis "9. Konfigurasi router sesudah percobaan"
for f in /etc/wpmgr-staging/prod/router/conf.d/*.conf; do echo "--- $f"; cat "$f"; done 2>&1
docker exec wpmgr-prod-router nginx -t 2>&1
