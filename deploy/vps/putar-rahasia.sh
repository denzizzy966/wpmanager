#!/usr/bin/env bash
# Mengganti WPMGR_SECRET_KEY, WPMGR_SESSION_SECRET, dan password database wpmgr.
# Aman HANYA selama belum ada site terdaftar (secret site dienkripsi dengan WPMGR_SECRET_KEY).
# Jalankan dari clone milik root: sudo bash /root/wpmgr-src/deploy/vps/putar-rahasia.sh
# (nilai baru tidak pernah dicetak)
#
# Keamanan: rahasia dibangkitkan python3 SISTEM dalam mode terisolasi (-I, direktori kerja tidak
# masuk sys.path), bukan venv di /opt/wpmgr yang dapat ditulis wpmgr. Rahasia baru tidak pernah
# menjadi argumen proses: SQL ke psql dan isi .env lewat stdin. Root tidak pernah membuka path di
# /opt/wpmgr: .env dibaca dan ditulis ulang SEBAGAI wpmgr (symlink di sana tidak bisa dipakai
# untuk membaca/menimpa berkas root).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "jalankan dengan sudo" >&2; exit 1; }
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
cek_milik_root "$(readlink -f -- "$0")" \
  || { echo "GAGAL: skrip harus berada di clone milik root yang tidak dapat ditulis pengguna lain" >&2; exit 1; }
APP=/opt/wpmgr
cd /

jumlah=$(sudo -u postgres psql -d wpmgr -tAc "SELECT count(*) FROM sites")
[[ "$jumlah" == "0" ]] || { echo "GAGAL: sudah ada $jumlah site; WPMGR_SECRET_KEY tidak boleh diganti begitu saja" >&2; exit 1; }

/usr/bin/python3 -I - <<'PY'
import base64, os, re, secrets, subprocess, sys

JALUR = "/opt/wpmgr/.env"
SEBAGAI = ["sudo", "-u", "wpmgr", "--"]
db = secrets.token_urlsafe(32)
kunci = base64.urlsafe_b64encode(os.urandom(32)).decode()
sesi = secrets.token_urlsafe(48)

subprocess.run(["sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-q"],
               input=f"ALTER ROLE wpmgr WITH PASSWORD '{db}';\n", text=True, check=True)

# Dibaca sebagai wpmgr: root tidak membuka path yang dikendalikan wpmgr.
isi = subprocess.run(SEBAGAI + ["cat", JALUR], capture_output=True, text=True, check=True).stdout
ganti = {
    "DATABASE_URL": f"postgresql+psycopg://wpmgr:{db}@127.0.0.1:5432/wpmgr",
    "WPMGR_SECRET_KEY": kunci,
    "WPMGR_SESSION_SECRET": sesi,
}
for nama, nilai in ganti.items():
    isi, n = re.subn(rf"(?m)^{nama}=.*$", lambda _m, a=nama, b=nilai: f"{a}={b}", isi)
    if n != 1:
        sys.exit(f"GAGAL: {nama} tidak ditemukan tepat satu kali di .env")

# Ditulis sebagai wpmgr lewat stdin ke berkas sementara, lalu diganti atomik (mv).
subprocess.run(SEBAGAI + ["sh", "-c", 'umask 077 && t=$(mktemp /opt/wpmgr/.env.XXXXXX) '
                          '&& cat > "$t" && mv -f "$t" /opt/wpmgr/.env'],
               input=isi, text=True, check=True)
PY

systemctl restart wpmgr-web 'wpmgr-worker@*'
sleep 3
systemctl is-active wpmgr-web wpmgr-worker@1 wpmgr-worker@2 wpmgr-worker@staging
sudo -u wpmgr -H bash -c "cd $APP && .venv/bin/python -m alembic current" | tail -1
curl -s -o /dev/null -w "Dashboard -> HTTP %{http_code}\n" https://wpmgr.halosocia.my.id/login
echo "Rahasia diganti. Login ulang di dashboard. Simpan salinan .env baru di tempat aman (jangan ditempel ke chat)."
