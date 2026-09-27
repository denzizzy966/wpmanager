"""E2E Lapis 3 (spec §14.5): WordPress e2e sebagai produksi, runtime staging di Docker Desktop.

Skrip pembantu berjalan sebagai root di container `pembantu` (profil compose
`staging`) dengan socket Docker Desktop; akar staging adalah bind mount
`./var/e2e-stg` (atau `WPMGR_E2E_STG_AKAR`) di `/srv/wpmgr`. Bind mount Windows di Docker Desktop (9p
drvfs dengan opsi `metadata`) menyimpan pemilik dan mode Unix, jadi cek pemilik
skrip (F2, `cek_mount_pengguna`) tetap berlaku apa adanya. Yang tidak bisa
ditiru proses dashboard di Windows hanya satu: direktori yang ia buat tampak
milik root di Linux. Karena itu test ini menyerahkan direktori site ke UID 33
(user dashboard tiruan) seperti keadaan di VPS, dan lebih dulu membuktikan
bahwa skrip menolak direktori yang bukan miliknya.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from wpmgr.config import get_settings
from wpmgr.crypto import dekripsi_secret, enkripsi_secret
from wpmgr.jobs.queue import buat_job
from wpmgr.models import (
    JobStatus,
    JobType,
    Staging,
    StagingSnapshot,
    StagingUji,
    StatusStaging,
)
from wpmgr.sso import buat_token
from wpmgr.staging.pembantu import (
    GalatPembantu,
    Pembantu,
    cookie_akses,
    hash_sandi,
    tautan_masuk,
)
from wpmgr.staging.sql_impor import periksa_sql
from wpmgr.worker import proses_satu

from .conftest import (
    AKAR_REPO,
    hapus_di_kontainer,
    klien_http,
    tulis_di_kontainer,
    tunggu_hingga,
    wpcli,
)

pytestmark = pytest.mark.e2e

NAMA = "uji-e2e"
DOMAIN = "staging.test"
HOST = f"{NAMA}.{DOMAIN}"
ROUTER = "http://localhost:8090"
MAILPIT = "http://localhost:8025"
SANDI = "sandi-preview-e2e"
RAHASIA = "e" * 64
UID_DASHBOARD = 33
# Sama dengan sumber mount /srv/wpmgr di docker-compose.yml (`WPMGR_E2E_STG_AKAR`).
AKAR_E2E = Path(os.environ.get("WPMGR_E2E_STG_AKAR") or AKAR_REPO / "var" / "e2e-stg")
AKAR_DI_PEMBANTU = "/srv/wpmgr"
EXEC_PEMBANTU = ["docker", "compose", "--profile", "staging", "exec", "-T", "pembantu"]
AWALAN = "docker compose --profile staging exec -T pembantu /usr/local/sbin/wpmgr-staging"
JEBAKAN = "/var/www/html/wp-content/mu-plugins/wpmgr-e2e-jebakan.php"


def _docker(*args: str, check: bool = True) -> str:
    hasil = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=600, check=False)
    if check and hasil.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} gagal: {hasil.stderr}")
    return hasil.stdout.strip()


def _di_pembantu(*perintah: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run([*EXEC_PEMBANTU, *perintah], capture_output=True, text=True, timeout=timeout,
                          check=False)


def _bersihkan_runtime() -> None:
    nama = _docker("ps", "-aq", "--filter", "label=wpmgr.staging", check=False).split()
    if nama:
        _docker("rm", "-f", *nama, check=False)
    _docker("network", "rm", "wpmgr-staging", check=False)
    _docker("volume", "rm", "wpmgr-stg-db", check=False)


def _akar_daemon() -> str:
    cid = subprocess.run(["docker", "compose", "--profile", "staging", "ps", "-q", "pembantu"],
                         capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    sumber = _docker("inspect", "--format",
                     '{{range .Mounts}}{{if eq .Destination "/srv/wpmgr"}}{{.Source}}{{end}}{{end}}', cid)
    cocok = re.match(r"([A-Za-z]):[\\/](.*)", sumber)
    if cocok:
        # Docker Desktop (WSL2) menerima path Windows sebagai /run/desktop/mnt/host/<drive>/...
        # dan menampilkannya kembali apa adanya di `docker inspect` container staging.
        return f"/run/desktop/mnt/host/{cocok.group(1).lower()}/" + cocok.group(2).replace("\\", "/")
    return sumber


@pytest.fixture(scope="module")
def runtime_staging():
    _bersihkan_runtime()
    shutil.rmtree(AKAR_E2E, ignore_errors=True)
    (AKAR_E2E / "staging").mkdir(parents=True)
    subprocess.run(["docker", "compose", "--profile", "staging", "up", "-d", "--build", "pembantu"],
                   check=True, capture_output=True, timeout=600)
    konf = "\n".join([
        f"DOMAIN={DOMAIN}", "STAGING_DIR=/srv/wpmgr/staging", "KONF_DIR=/srv/wpmgr/etc",
        "CERT_DIR=/srv/wpmgr/certs", "ACME_DIR=/srv/wpmgr/acme", "LE_DIR=/srv/wpmgr/le", "LOG_DIR=/srv/wpmgr/log",
        "ROUTER_PORT=127.0.0.1:8090", "MAIL_PORT=127.0.0.1:8025", "SUBNET=172.31.250.0/24",
        f"PENGGUNA_UID={UID_DASHBOARD}", f"PENGGUNA_GID={UID_DASHBOARD}", "AKAR_LOKAL=/srv/wpmgr",
        f"AKAR_DAEMON={_akar_daemon()}", "TANPA_IPTABLES=1", "TANPA_SERTIFIKAT=1",
    ]) + "\n"
    (AKAR_E2E / "staging.conf").write_bytes(konf.encode("ascii"))
    # Unduhan pertama image PHP staging (±200 MB) bisa melewati tenggat
    # `buat` (600 detik) di koneksi lambat; itu bukan yang diuji di sini.
    _docker("pull", "--quiet", "wordpress:php8.1-apache")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WPMGR_STAGING_DOMAIN", DOMAIN)
        mp.setenv("WPMGR_STAGING_DIR", str(AKAR_E2E / "staging"))
        mp.setenv("WPMGR_STAGING_PEMBANTU_AWALAN", AWALAN)
        mp.setenv("WPMGR_STAGING_ROUTER_URL", ROUTER)
        mp.setenv("WPMGR_STAGING_MAILPIT_URL", MAILPIT)
        get_settings.cache_clear()
        Pembantu.dari_setelan().siapkan()
        yield
        get_settings.cache_clear()
    _bersihkan_runtime()


def _halaman(jalur: str = "/", **kw) -> httpx.Response:
    return httpx.get(f"{ROUTER}{jalur}", headers={"Host": HOST, **kw.pop("headers", {})}, timeout=30, **kw)


def _jalankan(sesi, site, tipe, payload=None, batas=5):
    """Seperti `jalankan_sampai_selesai`, tetapi berhenti (tanpa assert) bila
    antrean kosong -- mis. job dijadwalkan ulang sesudah galat yang bisa
    diulang -- supaya assert pemanggil menampilkan status dan galat job."""
    job = buat_job(sesi, site.id, tipe, payload or {})
    for _ in range(batas):
        sesi.refresh(job)
        if job.status not in (JobStatus.pending, JobStatus.running):
            break
        if not proses_satu(sesi, "uji-e2e", buat_klien_fn=klien_http):
            break
    sesi.refresh(job)
    return job


def _files() -> Path:
    return AKAR_E2E / "staging"


def _di_produksi(jalur: str) -> bytes:
    return subprocess.run(["docker", "compose", "exec", "-T", "wpcli", "cat", jalur],
                          capture_output=True, check=True, timeout=60).stdout


def _serahkan_ke_dashboard(site_id) -> None:
    """Direktori site yang dibuat dashboard menjadi milik UID dashboard, seperti di VPS.

    Di VPS dashboard berjalan sebagai user `wpmgr`, jadi `files/`, `ekspor/`,
    dan `log/` yang ia buat otomatis miliknya. Proses Windows tidak punya UID
    di Linux: direktori buatannya tampak milik root (uid=0 bawaan drvfs). Cek
    pemilik skrip tidak dilonggarkan; test yang menyerahkan pemiliknya.
    """
    akar = f"{AKAR_DI_PEMBANTU}/staging/{site_id}"
    hasil = _di_pembantu("chown", f"{UID_DASHBOARD}:{UID_DASHBOARD}", akar, f"{akar}/files", f"{akar}/ekspor",
                         f"{akar}/log")
    assert hasil.returncode == 0, hasil.stderr


def _kunci_dorong() -> str | None:
    """Pemegang kunci dorong connector di produksi (None bila bebas)."""
    hasil = subprocess.run(["docker", "compose", "exec", "-T", "wpcli", "wp", "--path=/var/www/html",
                            "option", "get", "wpmgr_dorong_kunci"], capture_output=True, text=True, timeout=60,
                           check=False)
    if hasil.returncode != 0:
        return None
    return hasil.stdout.strip() or None


def _bersihkan_sisa_dorong(tema: str) -> None:
    """Sisa dorong dari jalan e2e sebelumnya yang berhenti di tengah.

    WordPress e2e bertahan antarjalan, sedangkan tabel dashboard dikosongkan
    fixture `sesi`: kunci dorong yang ditinggal job yang tidak pernah
    dilanjutkan akan menahan dorongan baru (2 jam) tanpa ada yang tahu.
    """
    skrip = ("wp --path=/var/www/html option delete wpmgr_dorong_kunci >/dev/null 2>&1;"
             " rm -rf /var/www/html/wp-content/wpmgr-dorong;"
             f" sed -i '/e2e-dorong/d' '/var/www/html/wp-content/themes/{tema}/style.css'")
    subprocess.run(["docker", "compose", "exec", "-T", "wpcli", "sh", "-c", skrip],
                   capture_output=True, timeout=60, check=False)


def _ada_internet() -> bool:
    try:
        return httpx.get("https://api.wordpress.org/plugins/info/1.0/hello-dolly.json", timeout=10).status_code == 200
    except httpx.HTTPError:
        return False


def test_alur_staging_lengkap(sesi, site_terpasang, runtime_staging):
    site = site_terpasang
    # --- Persiapan: connector 3.0 mengizinkan staging --------------------
    wpcli("option", "update", "wpmgr_izinkan_staging", "1")
    fitur = klien_http(site).ping()["fitur"]
    assert "staging" in fitur
    site.fitur = fitur
    sesi.add(Staging(site_id=site.id, nama=NAMA, sandi_hash=hash_sandi(SANDI),
                     rahasia_router_terenkripsi=enkripsi_secret(RAHASIA)))
    sesi.commit()
    akar = _files() / str(site.id)
    files = akar / "files"
    tema = wpcli("theme", "list", "--status=active", "--field=name")
    _bersihkan_sisa_dorong(tema)

    # --- Cek pemilik F2 tetap berlaku -------------------------------------
    # Direktori yang dibuat proses dashboard di Windows tampak milik root:
    # skrip menolak memasangnya ke container (kode keluar 3, "ditolak").
    for d in ("files", "ekspor", "log"):
        (akar / d).mkdir(parents=True, exist_ok=True)
    with pytest.raises(GalatPembantu) as ditolak:
        Pembantu.dari_setelan().buat(NAMA, "8.1", site.id)
    assert ditolak.value.kode == "ditolak"
    _serahkan_ke_dashboard(site.id)

    # Isi yang mirip SQL berbahaya di DATA (teks berkutip) tidak menahan tarik;
    # pernyataan LOAD DATA LOCAL INFILE sungguhan ditolak sebelum impor.
    wpcli("post", "create", "--post_title=Catatan e2e", "--post_status=publish",
          "--post_content=LOAD DATA LOCAL INFILE '/etc/passwd' INTO TABLE wp_users;")
    assert periksa_sql(b"LOAD DATA LOCAL INFILE '/etc/passwd' INTO TABLE `wp_users`;") is not None

    # --- 1. Tarik penuh ---------------------------------------------------
    job = _jalankan(sesi, site, JobType.staging_tarik)
    assert job.status == JobStatus.success, job.error
    st = sesi.query(Staging).one()
    assert (st.status, st.aktif, st.versi_php) == (StatusStaging.siap, True, "8.1")
    assert (files / "wp-includes" / "version.php").exists()
    assert "define( 'DB_HOST', 'wpmgr-stg-db' );" in (files / "wp-config.php").read_text(encoding="utf-8")

    # R13: container site berjalan sebagai UID dashboard tanpa capability, dan
    # Apache non-root tetap melayani port 80 (dibuktikan preview di bawah).
    wadah = json.loads(_docker("inspect", f"wp-{NAMA}"))[0]
    assert wadah["Config"]["User"] == f"{UID_DASHBOARD}:{UID_DASHBOARD}"
    assert wadah["HostConfig"]["CapDrop"] == ["ALL"]
    assert wadah["HostConfig"]["Sysctls"] == {"net.ipv4.ip_unprivileged_port_start": "0"}
    assert "no-new-privileges" in wadah["HostConfig"]["SecurityOpt"]
    assert _docker("exec", f"wp-{NAMA}", "id", "-u") == str(UID_DASHBOARD)

    # db-impor: server MariaDB staging berjalan dengan local_infile=OFF.
    sandi_root = (AKAR_E2E / "etc" / "db-root").read_text(encoding="ascii").strip()
    assert _docker("exec", "-e", f"MYSQL_PWD={sandi_root}", "wpmgr-stg-db", "mariadb", "-uroot", "-N", "-e",
                   "SELECT @@GLOBAL.local_infile") == "0"

    # wp-cli di bawah `-u` tanpa HOME: plugin list tetap menghasilkan JSON.
    daftar = _di_pembantu("/usr/local/sbin/wpmgr-staging", "wpcli", NAMA, "plugin", "list", timeout=300)
    assert daftar.returncode == 0, daftar.stderr
    assert isinstance(json.loads(daftar.stdout), list)
    print(f"stderr wp-cli plugin list (tanpa HOME): {daftar.stderr.strip()!r}")

    assert tunggu_hingga(lambda: _halaman(auth=("staging", SANDI)).status_code == 200, 90)
    beranda = _halaman(auth=("staging", SANDI))
    assert "<title>" in beranda.text and "Uji" in beranda.text
    assert "noindex" in beranda.headers.get("x-robots-tag", "")
    assert _halaman().status_code == 401
    assert _halaman(auth=("staging", "salah")).status_code == 401

    # Cookie secure_link membuka preview tanpa kata sandi (probe dan SSO).
    kue = "; ".join(f"{a}={b}" for a, b in cookie_akses(RAHASIA, HOST, int(time.time())).items())
    assert _halaman(headers={"Cookie": kue}).status_code == 200
    token = buat_token(dekripsi_secret(site.secret_terenkripsi), str(site.id))
    masuk = _halaman(tautan_masuk(RAHASIA, HOST, token, int(time.time())), follow_redirects=False)
    assert masuk.status_code == 302 and masuk.headers["location"].startswith("/?wpmgr_sso=")
    assert "wpmgr_stg_m=" in masuk.headers.get("set-cookie", "")
    # Tautan palsu tetap ditolak walau browser membawa cookie akses yang sah.
    palsu = _halaman("/__wpmgr_masuk?e=4102444800&m=palsu&sso=x", headers={"Cookie": kue}, follow_redirects=False)
    assert palsu.status_code == 403

    # Email dari staging tertangkap Mailpit dengan tag nama staging.
    httpx.post(f"{ROUTER}/wp-login.php?action=lostpassword", headers={"Host": HOST},
               auth=("staging", SANDI), data={"user_login": "admin", "redirect_to": ""}, timeout=30)
    assert tunggu_hingga(lambda: httpx.get(f"{MAILPIT}/api/v1/search", params={"query": f'tag:"{NAMA}"'},
                                           timeout=10).json().get("messages"), 30)

    # --- 2. Segarkan inkremental ----------------------------------------
    tulis_di_kontainer("/var/www/html/wp-content/uploads/e2e-baru.txt", "baru dari produksi")
    # F3: perubahan yang hanya ada di staging dibatalkan oleh segarkan.
    indeks_wp = files / "wp-content" / "index.php"
    asli_indeks = indeks_wp.read_bytes()
    indeks_wp.write_bytes(b"<?php // diubah hanya di staging\n")
    hanya_staging = files / "wp-content" / "e2e-hanya-staging.txt"
    hanya_staging.write_bytes(b"hanya di staging")
    job = _jalankan(sesi, site, JobType.staging_tarik)
    assert job.status == JobStatus.success, job.error
    assert (files / "wp-content" / "uploads" / "e2e-baru.txt").read_text(encoding="utf-8") == "baru dari produksi"
    assert job.payload["kemajuan"]["byte_selesai"] < 1024 * 1024
    assert indeks_wp.read_bytes() == asli_indeks
    assert not hanya_staging.exists()

    # --- 3–4. Uji update (butuh wordpress.org) ----------------------------
    if _ada_internet():
        wpcli("plugin", "install", "hello-dolly", "--version=1.6", "--force", "--activate")
        paket = [{"tipe": "plugin", "slug": "hello-dolly/hello.php", "dari": "1.6", "ke": "1.7.2"}]
        job = _jalankan(sesi, site, JobType.staging_uji_update, {"paket": paket})
        assert job.status == JobStatus.success, job.error
        uji = sesi.query(StagingUji).order_by(StagingUji.id.desc()).first()
        assert uji.hasil == "lolos", uji.pemeriksaan

        tulis_di_kontainer(JEBAKAN, (
            "<?php\n"
            "add_action( 'plugins_loaded', function () {\n"
            "    if ( ! defined( 'WPMGR_STAGING' ) ) { return; }\n"
            "    $d = get_file_data( WP_PLUGIN_DIR . '/hello-dolly/hello.php', array( 'v' => 'Version' ) );\n"
            "    if ( version_compare( $d['v'], '1.7.2', '>=' ) ) { wpmgr_e2e_fungsi_tidak_ada(); }\n"
            "} );\n"
        ))
        try:
            job = _jalankan(sesi, site, JobType.staging_uji_update, {"paket": paket})
            assert job.status == JobStatus.success, job.error
            uji = sesi.query(StagingUji).order_by(StagingUji.id.desc()).first()
            assert uji.hasil == "gagal"
            assert any("error fatal baru" in a for a in uji.pemeriksaan["alasan"])
        finally:
            hapus_di_kontainer(JEBAKAN)
        # Samakan staging dengan produksi lagi sebelum dorong.
        assert _jalankan(sesi, site, JobType.staging_tarik).status == JobStatus.success
    else:
        print("Uji update dilewati: wordpress.org tidak dapat dihubungi dari mesin ini.")

    # --- 5. Dorong hanya kode --------------------------------------------
    jalur_gaya = f"/var/www/html/wp-content/themes/{tema}/style.css"
    gaya = files / "wp-content" / "themes" / tema / "style.css"
    asli = gaya.read_bytes()
    assert b"e2e-dorong" not in asli
    gaya.write_bytes(asli + b"/* e2e-dorong */\n")
    job = _jalankan(sesi, site, JobType.staging_dorong, {"mode": "hanya_kode"})
    assert job.status == JobStatus.success, job.error
    assert job.hasil["dorong_gagal"] is False
    # Dorongan yang sukses melepas kunci dorong connector (bersihkan).
    assert _kunci_dorong() is None
    assert b"/* e2e-dorong */" in _di_produksi(jalur_gaya)
    assert httpx.get(site.url, timeout=30).status_code < 400
    snap = sesi.query(StagingSnapshot).one()
    assert snap.status == "tersedia"

    # --- 6. Timpa penuh ditolak karena komentar baru ----------------------
    komentar_awal = wpcli("comment", "list", "--format=count")
    wpcli("comment", "create", "--comment_post_ID=1", "--comment_content=komentar e2e", "--comment_approved=1")
    job = _jalankan(sesi, site, JobType.staging_dorong, {"mode": "timpa_penuh"})
    assert job.status == JobStatus.failed
    assert "komentar baru" in job.error
    # Ditolak sebelum snapshot atau unggahan apa pun: produksi tidak berubah.
    assert b"/* e2e-dorong */" in _di_produksi(jalur_gaya)
    assert int(wpcli("comment", "list", "--format=count")) == int(komentar_awal) + 1
    assert sesi.query(StagingSnapshot).count() == 1

    # --- 6b. R8: komentar SQL di ekspor staging ---------------------------
    # MariaDB menulis kolom COMPRESSED sebagai /*M!100301 COMPRESSED*/ di
    # SHOW CREATE TABLE. Connector menolak komentar saat impor, jadi dorong
    # timpa penuh (sudah dikonfirmasi nama) harus berhenti sebelum snapshot
    # atau unggahan apa pun.
    _docker("exec", "-e", f"MYSQL_PWD={sandi_root}", "wpmgr-stg-db", "mariadb", "-uroot", f"stg_{NAMA.replace('-', '_')}",
            "-e", "CREATE TABLE wp_e2e_r8 (id INT PRIMARY KEY, isi TEXT COMPRESSED)")
    job = _jalankan(sesi, site, JobType.staging_dorong, {"mode": "timpa_penuh", "konfirmasi_nama": site.nama})
    assert job.status == JobStatus.failed
    assert "Ekspor database staging memuat" in job.error and "sebelum apa pun diubah" in job.error, job.error
    assert b"/* e2e-dorong */" in _di_produksi(jalur_gaya)
    assert int(wpcli("comment", "list", "--format=count")) == int(komentar_awal) + 1
    assert "wp_e2e_r8" not in wpcli("db", "tables", "--all-tables")
    assert sesi.query(StagingSnapshot).count() == 1
    assert _kunci_dorong() is None

    # --- 7. Kembalikan snapshot -------------------------------------------
    job = _jalankan(sesi, site, JobType.staging_kembalikan, {"snapshot_id": snap.id, "konfirmasi_nama": site.nama})
    assert job.status == JobStatus.success, (job.status, job.attempts, job.error, job.payload)
    kembali = _di_produksi(jalur_gaya)
    assert kembali.replace(b"\r\n", b"\n") == asli.replace(b"\r\n", b"\n")
    sesi.refresh(snap)
    assert snap.status == "dipakai"
    assert httpx.get(site.url, timeout=30).status_code < 400
    assert os.path.isdir(_files() / str(site.id) / "snapshot")
