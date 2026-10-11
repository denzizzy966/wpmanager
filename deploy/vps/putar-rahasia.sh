#!/usr/bin/env bash
# Mengganti WPMGR_SECRET_KEY, WPMGR_SESSION_SECRET, dan password database wpmgr.
# Aman HANYA selama belum ada site terdaftar (secret site dienkripsi dengan WPMGR_SECRET_KEY).
# Jalankan: sudo bash deploy/vps/putar-rahasia.sh   (nilai baru tidak pernah dicetak)
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "jalankan dengan sudo" >&2; exit 1; }
APP=/opt/wpmgr
ENV=$APP/.env
PY=$APP/.venv/bin/python

jumlah=$(sudo -u postgres psql -d wpmgr -tAc "SELECT count(*) FROM sites")
[[ "$jumlah" == "0" ]] || { echo "GAGAL: sudah ada $jumlah site; WPMGR_SECRET_KEY tidak boleh diganti begitu saja" >&2; exit 1; }

DB=$($PY -c 'import secrets; print(secrets.token_urlsafe(32))')
SK=$($PY -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
SS=$($PY -c 'import secrets; print(secrets.token_urlsafe(48))')

sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
ALTER ROLE wpmgr WITH PASSWORD '$DB';
SQL

cp -p "$ENV" "$ENV.lama"
sed -i \
  -e "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+psycopg://wpmgr:$DB@127.0.0.1:5432/wpmgr|" \
  -e "s|^WPMGR_SECRET_KEY=.*|WPMGR_SECRET_KEY=$SK|" \
  -e "s|^WPMGR_SESSION_SECRET=.*|WPMGR_SESSION_SECRET=$SS|" \
  "$ENV"
chown wpmgr:wpmgr "$ENV"; chmod 600 "$ENV"
shred -u "$ENV.lama" 2>/dev/null || rm -f "$ENV.lama"

systemctl restart wpmgr-web 'wpmgr-worker@*'
sleep 3
systemctl is-active wpmgr-web wpmgr-worker@1 wpmgr-worker@2 wpmgr-worker@staging
sudo -u wpmgr -H bash -c "cd $APP && .venv/bin/python -m alembic current" | tail -1
curl -s -o /dev/null -w "Dashboard -> HTTP %{http_code}\n" https://wpmgr.halosocia.my.id/login
echo "Rahasia diganti. Login ulang di dashboard. Simpan salinan .env baru di tempat aman (jangan ditempel ke chat)."
