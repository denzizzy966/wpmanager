import os
import re
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from wpmgr.crypto import dekripsi_secret
from wpmgr.models import Base, JobStatus
from wpmgr.pairing import buat_site
from wpmgr.signing import new_nonce, sign
from wpmgr.site_client import SiteClient
from wpmgr.worker import proses_satu

# Sama dengan port di docker-compose.yml (`WPMGR_E2E_WP_PORT`, bawaan 8081).
WP_URL = f"http://localhost:{os.environ.get('WPMGR_E2E_WP_PORT', '8081')}"
pytestmark = pytest.mark.e2e

DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://wpmgr:wpmgr@localhost:5433/wpmgr_test"
)

AKAR_REPO = Path(__file__).resolve().parents[2]
SUMBER_DI_KONTAINER = "/opt/wpmgr-connector-src"
PLUGIN_DI_KONTAINER = "/var/www/html/wp-content/plugins/wp-manager-connector"


def versi_connector_sumber() -> str:
    teks = (AKAR_REPO / "connector" / "wp-manager-connector" / "wp-manager-connector.php").read_text(
        encoding="utf-8"
    )
    return re.search(r"^\s*\*\s*Version:\s*(\S+)", teks, re.MULTILINE).group(1)


def sinkronkan_connector() -> None:
    """Salin connector dari sumber read-only ke direktori plugin container.

    Dipanggil di awal setiap sesi e2e (dan oleh test yang menimpa connector),
    sehingga yang diuji selalu kode di checkout ini, dan self-update bisa
    menimpa direktori plugin tanpa menyentuh repo.
    """
    subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "sh", "-c",
         f"rm -rf '{PLUGIN_DI_KONTAINER}' && cp -r '{SUMBER_DI_KONTAINER}' '{PLUGIN_DI_KONTAINER}'"],
        capture_output=True, text=True, check=True, timeout=300,
    )


def tulis_di_kontainer(path: str, isi: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "sh", "-c",
         f"mkdir -p \"$(dirname '{path}')\" && cat > '{path}'"],
        input=isi, text=True, capture_output=True, check=True, timeout=300,
    )


def hapus_di_kontainer(path: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "rm", "-rf", path],
        capture_output=True, check=False, timeout=300,
    )


def permintaan_bertanda(site, secret: str, method: str, route: str,
                        body: bytes = b"", query: str = "") -> httpx.Response:
    """Permintaan HMAC mentah, untuk test yang perlu melihat header respons."""
    path = f"/wp-json{route}"
    ts, nonce = int(time.time()), new_nonce()
    headers = {
        "X-Wpmgr-Site": str(site.id),
        "X-Wpmgr-Timestamp": str(ts),
        "X-Wpmgr-Nonce": nonce,
        "X-Wpmgr-Signature": sign(secret, method, path, ts, nonce, body),
    }
    if body:
        headers["Content-Type"] = "application/json"
    return httpx.request(method, f"{site.url}{path}{query}", content=body or None,
                         headers=headers, timeout=60)


def klien_http(site) -> SiteClient:
    """SiteClient untuk WordPress lokal yang memakai http, bukan https.

    SiteClient menolak base_url non-https di konstruktor -- sengaja, karena
    tanpa TLS body respons dan token SSO yang lewat bisa dibaca di jalan.
    Kontainer WordPress di lingkungan test ini bicara HTTP polos di
    localhost:8081, jadi klien dibangun dengan URL https palsu lalu
    base_url-nya ditimpa setelah konstruksi. Pemeriksaan di konstruktor
    sendiri TIDAK dilonggarkan; workaround ini dikurung di sini saja.
    """
    klien = SiteClient("https://placeholder.test", str(site.id),
                       dekripsi_secret(site.secret_terenkripsi))
    klien.base_url = site.url
    return klien


def wpcli(*args: str) -> str:
    hasil = subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "wp", "--path=/var/www/html",
         "--allow-root", *args],
        capture_output=True, text=True, check=False, timeout=300,
    )
    if hasil.returncode != 0:
        raise RuntimeError(f"wp {' '.join(args)} gagal: {hasil.stderr}")
    return hasil.stdout.strip()


def _wpcli_status(*args: str) -> int:
    """Seperti wpcli(), tetapi tidak melempar -- hanya mengembalikan kode keluar.

    Dipakai untuk pemeriksaan idempoten (mis. "apakah sudah terpasang") yang
    memang boleh gagal secara wajar.
    """
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "wp", "--path=/var/www/html",
         "--allow-root", *args],
        capture_output=True, text=True, check=False, timeout=300,
    ).returncode


def jalankan_sampai_selesai(sesi, job, batas: int = 10) -> None:
    """Proses antrean sampai `job` tertentu keluar dari pending/running.

    proses_satu() memproses SATU job per panggilan -- yang tertua menurut
    scheduled_for, di seluruh antrean, bukan hanya job yang baru dibuat.
    verify_site dan update_connector sama-sama meng-antre-kan job susulan
    (scan_site, verify_site) yang scheduled_for-nya lebih tua daripada job
    yang baru saja dibuat test ini, sehingga satu panggilan proses_satu()
    tidak menjamin job yang baru itu yang diambil. Deviasi dari brief Task 7
    (yang memanggil _jalankan() sekali per job): dengan hanya satu panggilan,
    test ini gagal karena scan_site/verify_site susulan itu diproses lebih
    dulu dan job yang diperiksa tetap `pending`.
    """
    for _ in range(batas):
        sesi.refresh(job)
        if job.status not in (JobStatus.pending, JobStatus.running):
            return
        assert proses_satu(sesi, "uji-e2e", buat_klien_fn=klien_http)
    sesi.refresh(job)


def tunggu_hingga(kondisi, batas_detik: float = 20, jeda: float = 1.0) -> bool:
    """Ulangi `kondisi()` sampai True atau `batas_detik` terlampaui.

    /events hanya mengirim baris yang sudah melewati CAKRAWALA
    (WPMGR_Events::CAKRAWALA, 5 detik) -- lihat koreksi #10 di
    konteks-global.md. Karena itu, memicu sebuah kejadian di site lalu
    langsung memanggil collect_events SEKALI tidak cukup: baris itu belum
    tentu "tenang". Dipakai lewat pola "coba job lalu cek kondisi" (lihat
    _job_sampai di test_monitoring.py), bukan sleep tetap, supaya test
    berhenti secepat kondisinya terpenuhi.
    """
    batas = time.time() + batas_detik
    while True:
        if kondisi():
            return True
        if time.time() >= batas:
            return False
        time.sleep(jeda)


@pytest.fixture(scope="session")
def engine():
    dasar = DB_URL.rsplit("/", 1)[0] + "/postgres"
    adm = create_engine(dasar, isolation_level="AUTOCOMMIT", future=True)
    nama = DB_URL.rsplit("/", 1)[1]
    with adm.connect() as c:
        c.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :nama AND pid <> pg_backend_pid()"
            ),
            {"nama": nama},
        )
        c.execute(text(f'DROP DATABASE IF EXISTS "{nama}"'))
        c.execute(text(f'CREATE DATABASE "{nama}"'))
    adm.dispose()

    e = create_engine(DB_URL, future=True)
    Base.metadata.create_all(e)
    yield e
    e.dispose()


@pytest.fixture
def sesi(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = Session()
    yield s
    s.rollback()
    for tabel in reversed(Base.metadata.sorted_tables):
        s.execute(text(f'TRUNCATE TABLE "{tabel.name}" CASCADE'))
    s.commit()
    s.close()


@pytest.fixture(scope="session")
def wp_site():
    batas = time.time() + 180
    while time.time() < batas:
        try:
            if httpx.get(WP_URL, timeout=5).status_code < 500:
                break
        except httpx.HTTPError:
            pass
        time.sleep(3)
    else:
        pytest.fail("WordPress tidak siap dalam 180 detik")

    # Idempoten: sesi pytest boleh dijalankan berkali-kali terhadap kontainer
    # yang sama tanpa `docker compose down -v` di antaranya. `core install`
    # pada WordPress yang sudah terpasang gagal dengan kode keluar bukan-nol,
    # jadi pemeriksaan ini dilakukan lebih dulu alih-alih memercayai wpcli()
    # untuk melempar pada kegagalan yang sebetulnya tidak apa-apa.
    if _wpcli_status("core", "is-installed") != 0:
        wpcli("core", "install", f"--url={WP_URL}", "--title=Uji",
              "--admin_user=admin", "--admin_password=admin-uji-123",
              "--admin_email=admin@uji.local", "--skip-email")

    # Permalink "Plain" (bawaan instalasi baru) membuat WordPress mem-301
    # setiap permintaan /wp-json/... untuk menambahkan garis miring di akhir
    # -- SiteClient tidak mengikuti redirect (follow_redirects=False,
    # sengaja, karena redirect dapat membawa permintaan bertanda tangan
    # keluar dari HTTPS tanpa terlihat), sehingga guard() PHP tidak pernah
    # tercapai sama sekali dan setiap test gagal dengan BAD_RESPONSE, bukan
    # petunjuk apa pun tentang tanda tangan. Site WordPress nyata yang
    # dikelola dashboard ini nyaris selalu memakai permalink cantik, jadi
    # menyamakannya di sini membuat lingkungan test merepresentasikan
    # keadaan sebenarnya, bukan menghindari sebuah kegagalan.
    wpcli("rewrite", "structure", "/%postname%/", "--hard")
    wpcli("rewrite", "flush", "--hard")

    sinkronkan_connector()
    wpcli("plugin", "activate", "wp-manager-connector")
    return WP_URL


@pytest.fixture
def site_terpasang(sesi, wp_site):
    site, _kunci = buat_site(sesi, "Uji E2E", "https://uji.test", None, None)
    site.url = wp_site  # http://localhost:8081
    sesi.commit()

    # Ditulis langsung lewat `wp option update`, BUKAN lewat
    # WPMGR_Settings::simpan_kunci() -- fixture ini hanya perlu WordPress
    # dalam keadaan "sudah terpasang" secepat mungkin. Jalur simpan_kunci()
    # sungguhan diuji terpisah di test_alur_penuh.py.
    wpcli("option", "update", "wpmgr_site_id", str(site.id))
    wpcli("option", "update", "wpmgr_secret", dekripsi_secret(site.secret_terenkripsi))
    wpcli("option", "update", "wpmgr_dashboard_url", "http://host.docker.internal:8000")
    return site
