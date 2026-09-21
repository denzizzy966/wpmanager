import os
import subprocess
import time

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from wpmgr.models import Base

WP_URL = "http://localhost:8081"
pytestmark = pytest.mark.e2e

DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://wpmgr:wpmgr@localhost:5433/wpmgr_test"
)


def wpcli(*args: str) -> str:
    hasil = subprocess.run(
        ["docker", "compose", "exec", "-T", "wpcli", "wp", "--path=/var/www/html",
         "--allow-root", *args],
        capture_output=True, text=True, check=False,
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
        capture_output=True, text=True, check=False,
    ).returncode


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

    wpcli("plugin", "activate", "wp-manager-connector")
    return WP_URL
