"""E2E Lapis 4 (spec §18.5): WordPress e2e sebagai hosting lama, runtime produksi di Docker Desktop.

Skrip pembantu berjalan sungguhan di container `pembantu` (profil compose
`staging`) dengan socket Docker Desktop; `nginx -t`/`-T` sungguhan atas
`nginx.conf` container itu yang meng-include NGINX_HOSTING_DIR
(NGINX_UJI_SAJA=1), dan sertifikat self-signed (SERTIFIKAT_SENDIRI=1).
WordPress e2e berbicara HTTP polos di localhost:8081, jadi klien hosting lama
memakai `klien_http` (Koreksi #17), DNS publik diganti penanya tiruan, dan
verifikasi aktivasi lewat router produksi 127.0.0.1:8091 karena tidak ada
nginx host.

Disk: tarik menolak bila sisa disk sesudahnya < 15%. Bila drive repo sesempit itu, set
`WPMGR_E2E_STG_AKAR` ke direktori di drive lain (path pendek), mis. C:/Users/<anda>/AppData/Local/Temp/wpmgr-e2e-stg.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from wpmgr.config import get_settings
from wpmgr.hosting import dns as dns_mod
from wpmgr.hosting import pindah
from wpmgr.hosting import umum as hu
from wpmgr.hosting.dns import Jawaban
from wpmgr.jobs.queue import buat_job
from wpmgr.models import HostingBackup, HostingVps, JobStatus, JobType, StatusHosting
from wpmgr.staging.aman import tulis_atomik
from wpmgr.staging.pembantu import Pembantu, hash_sandi
from wpmgr.worker import proses_satu

from .conftest import AKAR_REPO, WP_URL, klien_http, wpcli

pytestmark = pytest.mark.e2e

DOMAIN = "pindah-e2e.test"
NAMA = "pindah-e2e-test"
DOMAIN_STAGING = "staging.test"
HOST_PRATINJAU = f"vps-{NAMA}.{DOMAIN_STAGING}"
ROUTER = "http://127.0.0.1:8091"
SANDI = "sandi-pratinjau-e2e"
VPS = "169.58.91.181"
UID_DASHBOARD = 33
AKAR_E2E = Path(os.environ.get("WPMGR_E2E_STG_AKAR") or AKAR_REPO / "var" / "e2e-stg")
AKAR_DI_PEMBANTU = "/srv/wpmgr"
EXEC_PEMBANTU = ["docker", "compose", "--profile", "staging", "exec", "-T", "pembantu"]
AWALAN = "docker compose --profile staging exec -T pembantu /usr/local/sbin/wpmgr-staging"
PENANDA_AKAR = ".wpmgr-e2e-akar"
UJI_PHP = "wp-content/mu-plugins/wpmgr-e2e-uji.php"
# Halaman uji: hasil wp_mail() dan REMOTE_ADDR, untuk memeriksa pengaman pratinjau dan A2.
ISI_UJI_PHP = b"""<?php
if ( isset( $_GET['uji_surel'] ) ) {
    add_action( 'init', function () {
        echo 'SUREL=' . var_export( wp_mail( 'tujuan@contoh.test', 'uji', 'isi' ), true );
        exit;
    } );
}
if ( isset( $_GET['uji_ip'] ) ) {
    echo 'IP=' . $_SERVER['REMOTE_ADDR'];
    exit;
}
"""


def _docker(*args: str, check: bool = True) -> str:
    hasil = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=600, check=False)
    if check and hasil.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} gagal: {hasil.stderr}")
    return hasil.stdout.strip()


def _di_pembantu(*perintah: str, masukan: bytes | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run([*EXEC_PEMBANTU, *perintah], input=masukan, capture_output=True, timeout=timeout,
                          check=False)


def _bersihkan_runtime() -> None:
    for label in ("wpmgr.hosting", "wpmgr.staging"):
        nama = _docker("ps", "-aq", "--filter", f"label={label}", check=False).split()
        if nama:
            _docker("rm", "-f", *nama, check=False)
    for jaringan in ("wpmgr-prod", "wpmgr-staging"):
        _docker("network", "rm", jaringan, check=False)
    for volume in ("wpmgr-prod-db", "wpmgr-stg-db"):
        _docker("volume", "rm", volume, check=False)


def _akar_daemon() -> str:
    cid = subprocess.run(["docker", "compose", "--profile", "staging", "ps", "-q", "pembantu"],
                         capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    sumber = _docker("inspect", "--format",
                     '{{range .Mounts}}{{if eq .Destination "/srv/wpmgr"}}{{.Source}}{{end}}{{end}}', cid)
    cocok = re.match(r"([A-Za-z]):[\\/](.*)", sumber)
    if cocok:
        return f"/run/desktop/mnt/host/{cocok.group(1).lower()}/" + cocok.group(2).replace("\\", "/")
    return sumber


def _hapus_akar_e2e() -> None:
    """Sama dengan test_staging: hanya akar yang jelas milik e2e yang dihapus."""
    akar = AKAR_E2E.resolve()
    milik_e2e = (akar.name.startswith("wpmgr-e2e") or (akar / PENANDA_AKAR).is_file()
                 or akar == (AKAR_REPO / "var" / "e2e-stg").resolve())
    if akar.exists() and any(akar.iterdir()) and not milik_e2e:
        pytest.fail(f"WPMGR_E2E_STG_AKAR ({akar}) bukan direktori e2e; tidak dihapus.")
    shutil.rmtree(akar, ignore_errors=True)


def _chown_dashboard(*relatif: str) -> None:
    jalur = [f"{AKAR_DI_PEMBANTU}/{r}" for r in relatif]
    hasil = _di_pembantu("chown", f"{UID_DASHBOARD}:{UID_DASHBOARD}", *jalur)
    assert hasil.returncode == 0, hasil.stderr


@pytest.fixture(scope="module")
def runtime_hosting():
    _bersihkan_runtime()
    _hapus_akar_e2e()
    try:
        for d in ("staging", "hosting"):
            (AKAR_E2E / d).mkdir(parents=True)
        (AKAR_E2E / PENANDA_AKAR).write_bytes(b"")
        subprocess.run(["docker", "compose", "--profile", "staging", "up", "-d", "--build", "pembantu"],
                       check=True, capture_output=True, timeout=900)
        konf = "\n".join([
            f"DOMAIN={DOMAIN_STAGING}", "STAGING_DIR=/srv/wpmgr/staging", "KONF_DIR=/srv/wpmgr/etc",
            "CERT_DIR=/srv/wpmgr/certs", "ACME_DIR=/srv/wpmgr/acme", "LE_DIR=/srv/wpmgr/le", "LOG_DIR=/srv/wpmgr/log",
            "ROUTER_PORT=127.0.0.1:8090", "MAIL_PORT=127.0.0.1:8025", "SUBNET=172.31.250.0/24",
            f"PENGGUNA_UID={UID_DASHBOARD}", f"PENGGUNA_GID={UID_DASHBOARD}", "AKAR_LOKAL=/srv/wpmgr",
            f"AKAR_DAEMON={_akar_daemon()}", "TANPA_IPTABLES=1", "TANPA_SERTIFIKAT=1",
            "HOSTING_DIR=/srv/wpmgr/hosting", "PROD_CERT_DIR=/srv/wpmgr/hosting-certs",
            "NGINX_HOSTING_DIR=/srv/wpmgr/nginx-hosting", "BACKUP_DIR=/srv/wpmgr/backup", "IP_PUBLIK=127.0.0.1",
            "NGINX_UJI_SAJA=1", "SERTIFIKAT_SENDIRI=1",
        ]) + "\n"
        (AKAR_E2E / "staging.conf").write_bytes(konf.encode("ascii"))
        _chown_dashboard("staging", "hosting")
        _docker("pull", "--quiet", "wordpress:php8.1-apache")
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("WPMGR_STAGING_DOMAIN", DOMAIN_STAGING)
            mp.setenv("WPMGR_STAGING_DIR", str(AKAR_E2E / "staging"))
            mp.setenv("WPMGR_STAGING_PEMBANTU_AWALAN", AWALAN)
            mp.setenv("WPMGR_HOSTING_IPV4", VPS)
            mp.setenv("WPMGR_HOSTING_DIR", str(AKAR_E2E / "hosting"))
            get_settings.cache_clear()
            pb = Pembantu.dari_setelan()
            pb.siapkan()
            pb.prod_siapkan()
            yield
            get_settings.cache_clear()
    finally:
        # Setiap langkah teardown berdiri sendiri: galat satu tidak melewati yang lain.
        try:
            _bersihkan_runtime()
        finally:
            try:
                _hapus_akar_e2e()
            finally:
                subprocess.run(["docker", "compose", "--profile", "staging", "rm", "-sf", "pembantu"],
                               capture_output=True, check=False, timeout=120)


@pytest.fixture
def tiruan_luar(monkeypatch):
    """Hosting lama lewat HTTP e2e, DNS publik yang sudah menunjuk VPS, dan verifikasi lewat router."""
    def klien_https_semu(site):
        # WordPress e2e berbicara HTTP polos, sehingga is_ssl() salah dan site_url() melaporkan http://
        # walau opsi siteurl https. Di hosting sungguhan TLS ada di depan PHP; header ini meniru itu
        # (wp-config image WordPress membaca X-Forwarded-Proto).
        klien = klien_http(site)
        klien._header_host = {"X-Forwarded-Proto": "https"}
        return klien

    monkeypatch.setattr(hu, "klien_lama", lambda site, h: hu.KlienLamaBacaSaja(klien_https_semu(site)))

    class PenanyaVps:
        def tanya(self, resolver, nama, jenis, batas):
            return Jawaban((VPS,)) if jenis == "A" else Jawaban()

    monkeypatch.setattr(dns_mod, "buat_penanya", lambda: PenanyaVps())

    def lewat_router(host):
        r = httpx.get(f"{ROUTER}/", headers={"Host": host}, timeout=30)
        return r.status_code, {k.lower(): v for k, v in r.headers.items()}

    monkeypatch.setattr(pindah, "ambil_halaman_verifikasi", lewat_router)


@pytest.fixture
def home_https():
    """home/siteurl WordPress e2e menjadi https://pindah-e2e.test selama test (syarat periksa_info)."""
    wpcli("option", "update", "home", f"https://{DOMAIN}")
    wpcli("option", "update", "siteurl", f"https://{DOMAIN}")
    try:
        yield
    finally:
        wpcli("option", "update", "home", WP_URL)
        wpcli("option", "update", "siteurl", WP_URL)


def _jalankan(sesi, site_id, tipe, payload=None, batas=8):
    job = buat_job(sesi, site_id, tipe, payload or {})
    for _ in range(batas):
        sesi.refresh(job)
        if job.status not in (JobStatus.pending, JobStatus.running):
            break
        if not proses_satu(sesi, "uji-e2e", buat_klien_fn=klien_http, jenis="staging"):
            break
    sesi.refresh(job)
    return job


def _router(host: str, jalur: str = "/", **kw) -> httpx.Response:
    return httpx.get(f"{ROUTER}{jalur}", headers={"Host": host, **kw.pop("headers", {})}, timeout=30, **kw)


def test_alur_pindah_hosting_lengkap(sesi, site_terpasang, runtime_hosting, tiruan_luar, home_https, monkeypatch):
    site = site_terpasang
    wpcli("option", "update", "wpmgr_izinkan_staging", "1")
    site.fitur = klien_http(site).ping()["fitur"]
    assert "staging" in site.fitur
    h = HostingVps(site_id=site.id, nama=NAMA, domain=DOMAIN, dengan_www=False, ip_lama="93.184.216.34",
                   sandi_hash=hash_sandi(SANDI))
    sesi.add(h)
    sesi.commit()
    akar = AKAR_E2E / "hosting" / str(site.id)
    (akar / "files").mkdir(parents=True)
    (akar / "log").mkdir()
    _chown_dashboard(f"hosting/{site.id}", f"hosting/{site.id}/files", f"hosting/{site.id}/log")

    # 1. Pindahkan: salin dari WordPress e2e ke runtime produksi.
    job = _jalankan(sesi, site.id, JobType.pindah_tarik)
    assert job.status == JobStatus.success, job.error
    sesi.refresh(h)
    assert h.status == StatusHosting.pratinjau
    tulis_atomik(akar / "files", UJI_PHP, ISI_UJI_PHP)

    # 2. Pratinjau lewat router: Basic Auth, noindex, email diblokir.
    assert _router(HOST_PRATINJAU).status_code == 401
    r = _router(HOST_PRATINJAU, auth=("pratinjau", SANDI))
    assert r.status_code == 200
    assert "noindex" in r.headers.get("x-robots-tag", "")
    r = _router(HOST_PRATINJAU, "/?uji_surel=1", auth=("pratinjau", SANDI))
    assert "SUREL=false" in r.text
    assert f"https://{HOST_PRATINJAU}" in _router(HOST_PRATINJAU, auth=("pratinjau", SANDI)).text

    # 3. Cek DNS dengan penanya tiruan (A = IPv4 VPS, AAAA dan CAA kosong).
    assert dns_mod.periksa_dns(h).ok

    # 4. Aktivasi penuh dengan nginx -t dan sertifikat sungguhan.
    h.status = StatusHosting.menunggu_dns
    sesi.commit()
    # stdout mentah prod-aktifkan harus persis "aktif": keluaran lain dibaca dashboard sebagai hasil
    # tidak pasti dan memicu ulang tiap 15 menit selama 24 jam (R26).
    keluaran_aktifkan = []
    jalankan_asli = Pembantu.jalankan

    def rekam(self, *argumen, **opsi):
        hasil = jalankan_asli(self, *argumen, **opsi)
        if argumen and argumen[0] == "prod-aktifkan":
            keluaran_aktifkan.append(hasil)
        return hasil

    monkeypatch.setattr(Pembantu, "jalankan", rekam)
    job = _jalankan(sesi, site.id, JobType.pindah_aktifkan)
    monkeypatch.setattr(Pembantu, "jalankan", jalankan_asli)
    assert job.status == JobStatus.success, job.error
    assert keluaran_aktifkan and all(k in ("aktif", "aktif\n") for k in keluaran_aktifkan), keluaran_aktifkan
    sesi.refresh(h)
    assert h.status == StatusHosting.aktif and h.dilayani_vps_pada is not None
    # P1: tarik terakhir di dalam aktivasi menghapus berkas uji (tidak ada di manifest hosting lama),
    # jadi berkas ditulis ulang sekarang, sebelum langkah 5.
    tulis_atomik(akar / "files", UJI_PHP, ISI_UJI_PHP)
    assert (akar / "files" / UJI_PHP).is_file()
    nginx_domain = (AKAR_E2E / "nginx-hosting" / f"{DOMAIN}.conf").read_text(encoding="utf-8")
    assert f"/srv/wpmgr/hosting-certs/{DOMAIN}/fullchain.pem" in nginx_domain and "vps-" not in nginx_domain

    # 5. Pengaman pratinjau tercabut; IP pengunjung asli sampai ke PHP (A2).
    r = _router(DOMAIN)
    assert r.status_code in (200, 301, 302)
    status_aktif = r.status_code
    assert "noindex" not in r.headers.get("x-robots-tag", "")
    assert not (akar / "files" / pindah.MU_PLUGIN_PRATINJAU).exists()
    assert b"WPMGR_PRATINJAU" not in (akar / "files" / "wp-config.php").read_bytes()
    r = _router(DOMAIN, "/?uji_ip=1", headers={"X-Forwarded-For": "198.51.100.23"})
    assert "IP=198.51.100.23" in r.text, r.text[:300]

    # 6. Backup: berkas ada dan sha256 cocok dengan manifest.
    job = _jalankan(sesi, site.id, JobType.backup_hosting, {"manual": True})
    assert job.status == JobStatus.success, job.error
    b = sesi.query(HostingBackup).filter(HostingBackup.site_id == site.id).one()
    dasar = AKAR_E2E / "backup" / str(site.id) / b.stempel
    manifest = json.loads((dasar / "manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256((dasar / "db.sql.gz").read_bytes()).hexdigest() == manifest["sha256_db"] == b.sha256_db
    assert hashlib.sha256((dasar / "files.tar.gz").read_bytes()).hexdigest() == manifest["sha256_file"]

    # 7. Tarik sesudah aktif ditolak oleh dashboard dan oleh skrip.
    job = _jalankan(sesi, site.id, JobType.pindah_tarik)
    assert job.status == JobStatus.failed and pindah.PESAN_SUDAH_DILAYANI in (job.error or ""), job.error
    hasil = _di_pembantu("/usr/local/sbin/wpmgr-staging", "prod-db-impor", NAMA, masukan=b"DROP TABLE wp_options;")
    assert hasil.returncode == 3
    assert _router(DOMAIN).status_code == status_aktif
