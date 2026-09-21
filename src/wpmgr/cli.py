import argparse
import sys
import uuid
from pathlib import Path

from argon2 import PasswordHasher
from sqlalchemy import select

from wpmgr import db
from wpmgr.config import get_settings
from wpmgr.connector_paket import bangun_paket, sumber_bawaan
from wpmgr.db import get_session
from wpmgr.jobs.queue import antrekan_scan
from wpmgr.jobs.reaper import pulihkan_job_yatim
from wpmgr.kunci import KUNCI_SSL, KUNCI_UPTIME, kunci_advisory
from wpmgr.models import Site, SiteStatus, User
from wpmgr.ssl_cek import cek_semua_ssl
from wpmgr.uptime import buat_klien_http, cek_satu, jalankan_putaran

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


def build_connector(sumber: str | None = None) -> dict:
    manifest = bangun_paket(
        Path(sumber) if sumber else sumber_bawaan(), get_settings().jalur_connector
    )
    print(
        f"Connector {manifest['versi']} dibangun ({manifest['ukuran']} byte), "
        f"sha256 {manifest['sha256']}"
    )
    return manifest


def check_uptime() -> int:
    with kunci_advisory(db.engine, KUNCI_UPTIME) as dapat:
        if not dapat:
            print("Putaran uptime sebelumnya masih berjalan; putaran ini dilewati")
            return 0
        with buat_klien_http() as http, get_session() as sesi:
            putaran = jalankan_putaran(sesi, lambda url: cek_satu(http, url))
    if putaran is None:
        print("Tidak ada site untuk dicek")
        return 0
    catatan = " (gangguan dashboard, status tidak diubah)" if putaran.gangguan_dashboard else ""
    print(f"{putaran.jumlah_site} site dicek, {putaran.jumlah_gagal} gagal{catatan}")
    return putaran.jumlah_site


def check_ssl() -> int:
    with kunci_advisory(db.engine, KUNCI_SSL) as dapat:
        if not dapat:
            print("Pemeriksaan SSL lain masih berjalan; dilewati")
            return 0
        with get_session() as sesi:
            n = cek_semua_ssl(sesi)
    print(f"{n} sertifikat dicek")
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wpmgr")
    sub = parser.add_subparsers(dest="perintah", required=True)
    sub.add_parser("enqueue-scans")
    sub.add_parser("reap-jobs")
    p = sub.add_parser("create-user")
    p.add_argument("--email", required=True)
    p.add_argument("--nama", required=True)
    p.add_argument("--password", required=True)
    b = sub.add_parser("build-connector")
    b.add_argument("--sumber", default=None)
    sub.add_parser("check-uptime")
    sub.add_parser("check-ssl")

    args = parser.parse_args(argv)
    if args.perintah == "enqueue-scans":
        enqueue_scans()
    elif args.perintah == "reap-jobs":
        reap_jobs()
    elif args.perintah == "create-user":
        create_user(args.email, args.nama, args.password)
    elif args.perintah == "build-connector":
        build_connector(args.sumber)
    elif args.perintah == "check-uptime":
        check_uptime()
    elif args.perintah == "check-ssl":
        check_ssl()
    return 0


if __name__ == "__main__":
    sys.exit(main())
