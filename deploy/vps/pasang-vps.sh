#!/usr/bin/env bash
# Pemasang wp-manager Lapis 1-4 di VPS ini (sekali; aman diulang bila berhenti di tengah).
# Jalankan dari clone MILIK ROOT (bukan /opt/wpmgr, yang dapat ditulis pengguna wpmgr):
#   sudo git clone https://github.com/denzizzy966/wpmanager.git /root/wpmgr-src
#   sudo bash /root/wpmgr-src/deploy/vps/pasang-vps.sh
# Nilai di bawah adalah VPS halosocia; timpa lewat variabel lingkungan untuk VPS lain, mis.
#   sudo WPMGR_DOM=wpmgr.contoh.id WPMGR_IP4=1.2.3.4 bash /root/wpmgr-src/deploy/vps/pasang-vps.sh
# Rahasia (kunci, password DB) dibangkitkan di VPS ini dan hanya ditulis ke /opt/wpmgr/.env (0600).
#
# Keamanan:
# - Root tidak pernah menjalankan atau memasang berkas yang dapat ditulis pengguna wpmgr
#   (/opt/wpmgr). Berkas milik root (skrip pembantu, sudoers, unit systemd, nginx, crontab)
#   dipasang dari clone baru milik root di direktori sementara; git di /opt/wpmgr dijalankan
#   sebagai wpmgr. Semua berkas di dalam /opt/wpmgr (.env, penanda akun) ditulis SEBAGAI wpmgr,
#   sehingga symlink yang ditanam di sana tidak pernah diikuti dengan hak root.
# - Skrip ini sendiri harus milik root dan tidak dapat ditulis pengguna lain (diperiksa di awal).
# - python3 sistem dijalankan dengan -I (mode terisolasi): direktori kerja dan PYTHON* tidak
#   masuk sys.path, jadi modul palsu tidak dapat dimuat.
# - Rahasia dibangkitkan dengan python3 sistem dan tidak pernah muncul di argumen proses
#   (yang terlihat lewat ps); password admin dikirim lewat stdin.
set -euo pipefail

REPO="${WPMGR_REPO:-https://github.com/denzizzy966/wpmanager.git}"
DOM="${WPMGR_DOM:-wpmgr.halosocia.my.id}"
STG_DOM="${WPMGR_STG_DOM:-staging.halosocia.my.id}"
IP4="${WPMGR_IP4:-169.58.91.181}"
IP6="${WPMGR_IP6:-2a02:c207:2347:2607::1}"
APP=/opt/wpmgr
# 8000/8090/8091 sudah dipakai layanan lain di VPS halosocia.
PORT_WEB="${WPMGR_PORT_WEB:-8100}"
PORT_STG="${WPMGR_PORT_STG:-8190}"
PORT_PROD="${WPMGR_PORT_PROD:-8191}"
PORT_MAIL="${WPMGR_PORT_MAIL:-8025}"

langkah() { echo; echo "=== $*"; }
mati()    { echo "GAGAL: $*" >&2; exit 1; }
sebagai() { sudo -u wpmgr -H bash -c "cd $APP && $1"; }
acak()    { /usr/bin/python3 -I -c "import secrets; print(secrets.token_urlsafe($1))"; }
# Berkas milik root dan tidak dapat ditulis grup/lain, begitu pula setiap direktori induknya.
cek_milik_root() {
  local p="$1" m
  while :; do
    m=$(stat -c "%u %a" -- "$p")
    [[ "${m%% *}" == 0 ]] || return 1
    (( (8#${m##* } & 8#022) == 0 )) || return 1
    [[ "$p" == / ]] && return 0
    p=$(dirname -- "$p")
  done
}

[[ $EUID -eq 0 ]] || mati "jalankan dengan: sudo bash $0"
cek_milik_root "$(readlink -f -- "$0")" \
  || mati "skrip harus berada di clone milik root yang tidak dapat ditulis pengguna lain (lihat kepala berkas)"
cd /
SRC="$(mktemp -d)"
trap 'rm -rf "$SRC"' EXIT

langkah "0. Pra-cek"
alamat=$(getent ahostsv4 "$DOM" | awk 'NR==1{print $1}' || true)
[[ "$alamat" == "$IP4" ]] || mati "DNS $DOM belum mengarah ke $IP4 (sekarang: '${alamat:-kosong}'). Tambahkan A record 'wpmgr' di hPanel lalu ulangi."
if [[ ! -f $APP/.env ]]; then
  for p in $PORT_WEB $PORT_STG $PORT_PROD $PORT_MAIL; do
    ! ss -ltnH "( sport = :$p )" | grep -q . || mati "port $p sudah dipakai proses lain"
  done
fi
[[ -f /etc/letsencrypt/options-ssl-nginx.conf ]] || mati "/etc/letsencrypt/options-ssl-nginx.conf tidak ada"

read -rp "Email admin dashboard (login + Let's Encrypt): " EMAIL
[[ "$EMAIL" == *@*.* ]] || mati "email tidak sah"
PASS=""
if ! [[ -f $APP/.akun-dibuat ]]; then
  read -rsp "Password admin dashboard (min. 12 karakter): " PASS; echo
  read -rsp "Ulangi password: " PASS2; echo
  [[ "$PASS" == "$PASS2" && ${#PASS} -ge 12 ]] || mati "password tidak sama atau kurang dari 12 karakter"
  unset PASS2
fi

langkah "1. Pengguna wpmgr, kode, virtualenv"
id wpmgr >/dev/null 2>&1 || adduser --system --group --home "$APP" --shell /usr/sbin/nologin wpmgr
install -d -o wpmgr -g wpmgr -m 0755 "$APP"
if [[ ! -d $APP/.git ]]; then
  [[ -z "$(ls -A "$APP" 2>/dev/null)" ]] || mati "$APP tidak kosong dan bukan clone git"
  sudo -u wpmgr -H git clone -q "$REPO" "$APP"
else
  sebagai "git pull -q --ff-only"
fi
sebagai "[ -x .venv/bin/python ] || python3 -m venv .venv; .venv/bin/pip install -q --upgrade pip; .venv/bin/pip install -q -e ."
# Sumber berkas milik root: clone baru milik root, bukan /opt/wpmgr yang dapat ditulis wpmgr.
git clone -q --depth 1 "$REPO" "$SRC/repo"
R="$SRC/repo"
if [[ "$(git -C "$R" rev-parse HEAD)" != "$(sudo -u wpmgr git -C "$APP" rev-parse HEAD)" ]]; then
  echo "PERINGATAN: commit $APP berbeda dari clone root; berkas root dipasang dari clone root."
fi

langkah "2. Database PostgreSQL"
if [[ ! -f $APP/.env ]]; then
  DBPASS=$(acak 32)
  # SQL lewat stdin (heredoc), bukan argumen proses.
  sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wpmgr') THEN
    CREATE ROLE wpmgr LOGIN;
  END IF;
END \$\$;
ALTER ROLE wpmgr WITH LOGIN PASSWORD '$DBPASS';
SQL
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='wpmgr'" | grep -q 1 \
    || sudo -u postgres createdb -O wpmgr wpmgr

  langkah "3. Berkas .env"
  # Kunci Fernet = base64 urlsafe dari 32 byte acak.
  SECRET=$(/usr/bin/python3 -I -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')
  SESI=$(acak 48)
  # Ditulis SEBAGAI wpmgr (isi lewat stdin): root tidak pernah membuka path di /opt/wpmgr.
  sudo -u wpmgr bash -c 'umask 077 && cat > /opt/wpmgr/.env' <<ENV
DATABASE_URL=postgresql+psycopg://wpmgr:$DBPASS@127.0.0.1:5432/wpmgr
WPMGR_SECRET_KEY=$SECRET
WPMGR_BASE_URL=https://$DOM
WPMGR_SESSION_SECRET=$SESI

WPMGR_STAGING_DOMAIN=$STG_DOM
WPMGR_STAGING_DIR=/var/lib/wpmgr/staging
WPMGR_STAGING_PEMBANTU=/usr/local/sbin/wpmgr-staging
WPMGR_STAGING_EMAIL_ACME=$EMAIL
WPMGR_STAGING_ROUTER_URL=http://127.0.0.1:$PORT_STG
WPMGR_STAGING_MAILPIT_URL=http://127.0.0.1:$PORT_MAIL

WPMGR_HOSTING_IPV4=$IP4
WPMGR_HOSTING_IPV6=$IP6
WPMGR_HOSTING_DIR=/var/lib/wpmgr/hosting
WPMGR_HOSTING_RESOLVER=1.1.1.1,8.8.8.8
WPMGR_BACKUP_TUJUAN=lokal
WPMGR_BACKUP_HARIAN=7
WPMGR_BACKUP_MINGGUAN=4
ENV
  unset DBPASS SECRET SESI
  echo "PENTING: simpan salinan $APP/.env di tempat aman (WPMGR_SECRET_KEY hilang = semua secret site hilang)."
fi

langkah "4. Skema, connector, akun admin"
sebagai ".venv/bin/python -m alembic upgrade head"
sebagai ".venv/bin/python -m wpmgr.cli build-connector >/dev/null"
if [[ ! -f $APP/.akun-dibuat ]]; then
  # Email dan password lewat stdin (printf adalah builtin bash), bukan argumen proses.
  printf '%s\n%s\n' "$EMAIL" "$PASS" | sudo -u wpmgr -H bash -c "cd $APP && exec .venv/bin/python -c '
import sys
from wpmgr.cli import create_user
email = sys.stdin.readline().rstrip(\"\\n\")
sandi = sys.stdin.readline().rstrip(\"\\n\")
create_user(email, \"Admin\", sandi)
'"
  sudo -u wpmgr touch "$APP/.akun-dibuat"
fi
unset PASS
install -d -o wpmgr -g wpmgr -m 0755 /var/log/wpmgr

langkah "5. Pra-cek nginx host (TINJAU)"
echo "--- set_real_ip_from / allow rentang privat (harus kosong atau tidak mencakup 172.31.x):"
grep -rn "set_real_ip_from\|allow 172\|allow 10\.\|allow 192\.168" /etc/nginx/ || echo "(tidak ada)"
echo "--- default_server di :443 / wildcard halosocia:"
grep -rn "default_server" /etc/nginx/sites-enabled || echo "(tidak ada)"
grep -rn 'server_name.*\*\.\(staging\.\)\?halosocia' /etc/nginx/sites-enabled || echo "(tidak ada)"
echo "--- server_name untuk domain yang akan dipindah (harus kosong):"
# Hanya nama persis (domain atau www.domain); subdomain lain seperti rizkycahayaraya.halosocia.my.id tidak relevan.
nginx -T 2>/dev/null | grep -nE "server_name([[:space:]][^;]*)?[[:space:]](www\.)?(dutamakmurabadi\.com|scaffoldingsurabayamurah\.com|rizkycahayaraya\.com)[[:space:];]" || echo "(tidak ada)"
read -rp "Hasil di atas aman? Ketik 'ya' untuk lanjut: " ok
[[ "$ok" == "ya" ]] || mati "dihentikan oleh operator"

langkah "6. Sertifikat dan nginx dashboard"
if [[ ! -f /etc/letsencrypt/live/$DOM/fullchain.pem ]]; then
  certbot certonly --nginx -n --agree-tos -m "$EMAIL" -d "$DOM"
fi
sed -e "s/wpmgr\.example\.com/$DOM/g" -e "s/127\.0\.0\.1:8000/127.0.0.1:$PORT_WEB/" \
  "$R/deploy/nginx.conf" > /etc/nginx/sites-available/wpmgr.conf
ln -sfn /etc/nginx/sites-available/wpmgr.conf /etc/nginx/sites-enabled/wpmgr.conf
if ! nginx -t; then rm -f /etc/nginx/sites-enabled/wpmgr.conf; mati "nginx -t gagal; berkas wpmgr dicabut, nginx tidak di-reload"; fi
systemctl reload nginx

langkah "7. Layanan web dan worker"
sed "s/--port 8000/--port $PORT_WEB/" "$R/deploy/wpmgr-web.service" > /etc/systemd/system/wpmgr-web.service
install -m 0644 "$R/deploy/wpmgr-worker@.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wpmgr-web wpmgr-worker@1 wpmgr-worker@2

langkah "8. Skrip pembantu staging/hosting"
install -o root -g root -m 0755 "$R/deploy/staging/wpmgr-staging" /usr/local/sbin/wpmgr-staging
install -d -o root -g root -m 0755 /etc/wpmgr-staging
if [[ ! -f /etc/wpmgr-staging/staging.conf ]]; then
  sed -e "s/^ACME_EMAIL=.*/ACME_EMAIL=$EMAIL/" \
      -e "s/^ROUTER_PORT=.*/ROUTER_PORT=127.0.0.1:$PORT_STG/" \
      -e "s/^MAIL_PORT=.*/MAIL_PORT=127.0.0.1:$PORT_MAIL/" \
      -e "s/^PROD_ROUTER_PORT=.*/PROD_ROUTER_PORT=127.0.0.1:$PORT_PROD/" \
      "$R/deploy/staging/staging.conf.contoh" > /etc/wpmgr-staging/staging.conf
  chown root:root /etc/wpmgr-staging/staging.conf; chmod 0644 /etc/wpmgr-staging/staging.conf
fi
install -d -o root -g root -m 0755 /var/lib/wpmgr
install -d -o wpmgr -g wpmgr -m 0700 /var/lib/wpmgr/staging /var/lib/wpmgr/hosting
install -o root -g root -m 0440 "$R/deploy/staging/sudoers-wpmgr-staging" /etc/sudoers.d/wpmgr-staging
visudo -cf /etc/sudoers.d/wpmgr-staging
echo br_netfilter > /etc/modules-load.d/br_netfilter.conf
echo 'net.bridge.bridge-nf-call-iptables=1' > /etc/sysctl.d/99-wpmgr-staging.conf
modprobe br_netfilter
sysctl --system >/dev/null

langkah "9. Siapkan staging dan produksi (Docker, iptables, MariaDB, router)"
wpmgr-staging siapkan
wpmgr-staging prod-siapkan
install -m 0644 "$R/deploy/staging/wpmgr-staging-siapkan.service" /etc/systemd/system/
# Lapis pertama isolasi saat boot (putusan L21): aturan iptables dipasang sebelum dockerd
# menjalankan ulang container. Docker TIDAK direstart di sini (container lain di VPS ikut mati);
# drop-in berlaku pada start Docker/reboot berikutnya. `isolasi` idempoten, jadi aman dijalankan.
install -d -m 0755 /etc/systemd/system/docker.service.d
install -m 0644 "$R/deploy/staging/docker-wpmgr-isolasi.conf" /etc/systemd/system/docker.service.d/10-wpmgr-isolasi.conf
systemctl daemon-reload
wpmgr-staging isolasi
systemctl enable wpmgr-staging-siapkan
systemctl restart wpmgr-staging-siapkan

langkah "10. nginx staging dan hosting"
# Host pratinjau vps-<nama>.<STG_DOM> panjang; server_names_hash_bucket_size bawaan (64)
# membuat nginx host menolak berkas domain pratinjau. Pasang sekali bila belum diatur di mana pun.
if ! grep -rqsE '^[[:space:]]*server_names_hash_bucket_size' /etc/nginx/; then
  echo "server_names_hash_bucket_size 128;" > /etc/nginx/conf.d/00-wpmgr-server-names.conf
fi
sed "s/127\.0\.0\.1:8090/127.0.0.1:$PORT_STG/" "$R/deploy/staging/nginx-wpmgr-staging.conf" \
  > /etc/nginx/sites-enabled/wpmgr-staging.conf
install -d -o root -g root -m 0755 /etc/nginx/wpmgr-hosting
install -m 0644 "$R/deploy/staging/nginx-wpmgr-hosting.conf" /etc/nginx/sites-enabled/wpmgr-hosting.conf
if ! nginx -t; then
  rm -f /etc/nginx/sites-enabled/wpmgr-staging.conf /etc/nginx/sites-enabled/wpmgr-hosting.conf /etc/nginx/conf.d/00-wpmgr-server-names.conf
  mati "nginx -t gagal; berkas staging/hosting dicabut, nginx tidak di-reload"
fi
systemctl reload nginx

langkah "11. Cron dan worker staging"
crontab -u wpmgr "$R/deploy/crontab"
systemctl enable --now wpmgr-worker@staging
systemctl restart 'wpmgr-worker@*' wpmgr-web

langkah "12. Pemeriksaan akhir"
sleep 3
sudo -u wpmgr sudo -n /usr/local/sbin/wpmgr-staging status | head -c 400; echo
sudo -u wpmgr sudo -n /usr/local/sbin/wpmgr-staging prod-status | head -c 400; echo
systemctl is-active wpmgr-web wpmgr-worker@1 wpmgr-worker@2 wpmgr-worker@staging wpmgr-staging-siapkan
curl -s -o /dev/null -w "Dashboard https://$DOM/login -> HTTP %{http_code}\n" "https://$DOM/login"
iptables -S WPMGR-PROD-MASUK | head -3
echo
echo "SELESAI. Buka https://$DOM dan login dengan akun admin yang dibuat saat pemasangan pertama."
