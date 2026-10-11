#!/usr/bin/env bash
# Mengganti WPMGR_SECRET_KEY, WPMGR_SESSION_SECRET, dan password database wpmgr.
# Aman HANYA selama belum ada site terdaftar (secret site dienkripsi dengan WPMGR_SECRET_KEY).
# Jalankan: sudo bash deploy/vps/putar-rahasia.sh   (nilai baru tidak pernah dicetak)
#
# Keamanan: semua pekerjaan dilakukan satu proses python3 SISTEM milik root (bukan venv di
# /opt/wpmgr yang dapat ditulis wpmgr). Rahasia baru tidak pernah menjadi argumen proses:
# SQL dikirim ke psql lewat stdin dan .env ditulis ulang secara atomik dari dalam Python.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "jalankan dengan sudo" >&2; exit 1; }
APP=/opt/wpmgr

jumlah=$(sudo -u postgres psql -d wpmgr -tAc "SELECT count(*) FROM sites")
[[ "$jumlah" == "0" ]] || { echo "GAGAL: sudah ada $jumlah site; WPMGR_SECRET_KEY tidak boleh diganti begitu saja" >&2; exit 1; }

/usr/bin/python3 - "$APP/.env" <<'PY'
import base64, os, pwd, re, secrets, subprocess, sys, tempfile

jalur = sys.argv[1]
db = secrets.token_urlsafe(32)
kunci = base64.urlsafe_b64encode(os.urandom(32)).decode()
sesi = secrets.token_urlsafe(48)

subprocess.run(["sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-q"],
               input=f"ALTER ROLE wpmgr WITH PASSWORD '{db}';\n", text=True, check=True)

with open(jalur, encoding="utf-8") as f:
    isi = f.read()
ganti = {
    "DATABASE_URL": f"postgresql+psycopg://wpmgr:{db}@127.0.0.1:5432/wpmgr",
    "WPMGR_SECRET_KEY": kunci,
    "WPMGR_SESSION_SECRET": sesi,
}
for nama, nilai in ganti.items():
    isi, n = re.subn(rf"(?m)^{nama}=.*$", lambda _m, a=nama, b=nilai: f"{a}={b}", isi)
    if n != 1:
        sys.exit(f"GAGAL: {nama} tidak ditemukan tepat satu kali di .env")

w = pwd.getpwnam("wpmgr")
fd, sementara = tempfile.mkstemp(dir=os.path.dirname(jalur), prefix=".env.")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(isi)
    os.chmod(sementara, 0o600)
    os.chown(sementara, w.pw_uid, w.pw_gid)
    os.replace(sementara, jalur)
except BaseException:
    os.unlink(sementara)
    raise
PY

systemctl restart wpmgr-web 'wpmgr-worker@*'
sleep 3
systemctl is-active wpmgr-web wpmgr-worker@1 wpmgr-worker@2 wpmgr-worker@staging
sudo -u wpmgr -H bash -c "cd $APP && .venv/bin/python -m alembic current" | tail -1
curl -s -o /dev/null -w "Dashboard -> HTTP %{http_code}\n" https://wpmgr.halosocia.my.id/login
echo "Rahasia diganti. Login ulang di dashboard. Simpan salinan .env baru di tempat aman (jangan ditempel ke chat)."
