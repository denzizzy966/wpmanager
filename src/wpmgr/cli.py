import argparse
import sys
import uuid

from argon2 import PasswordHasher
from sqlalchemy import select

from wpmgr.db import get_session
from wpmgr.jobs.queue import antrekan_scan
from wpmgr.jobs.reaper import pulihkan_job_yatim
from wpmgr.models import Site, SiteStatus, User

# Deviasi sadar dari spec §7.5 ("setiap site berstatus active", Ruling R54):
# `unreachable` menurut definisinya sementara, dan tanpa scan terjadwal satu
# gangguan jaringan membuat site keluar dari pemantauan selamanya -- tidak ada
# yang akan pernah mencobanya lagi. `needs_reconnect` dan `blocked` tetap
# dikecualikan karena keduanya menunggu tindakan manusia (tempel ulang kunci,
# allowlist IP); tombol Scan tetap jalur pemulihannya.
STATUS_DISCAN = (SiteStatus.active, SiteStatus.unreachable)


def enqueue_scans() -> int:
    dibuat = 0
    with get_session() as sesi:
        sites = sesi.scalars(select(Site).where(Site.status.in_(STATUS_DISCAN))).all()
        for site in sites:
            if antrekan_scan(sesi, site.id) is not None:
                dibuat += 1
    print(f"{dibuat} job scan dibuat")
    return dibuat


def reap_jobs() -> int:
    with get_session() as sesi:
        n = pulihkan_job_yatim(sesi)
    print(f"{n} job yatim dipulihkan")
    return n


def create_user(email: str, nama: str, password: str) -> None:
    with get_session() as sesi:
        sesi.add(
            User(id=uuid.uuid4(), email=email, nama=nama,
                 password_hash=PasswordHasher().hash(password))
        )
    print(f"User {email} dibuat")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wpmgr")
    sub = parser.add_subparsers(dest="perintah", required=True)
    sub.add_parser("enqueue-scans")
    sub.add_parser("reap-jobs")
    p = sub.add_parser("create-user")
    p.add_argument("--email", required=True)
    p.add_argument("--nama", required=True)
    p.add_argument("--password", required=True)

    args = parser.parse_args(argv)
    if args.perintah == "enqueue-scans":
        enqueue_scans()
    elif args.perintah == "reap-jobs":
        reap_jobs()
    elif args.perintah == "create-user":
        create_user(args.email, args.nama, args.password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
