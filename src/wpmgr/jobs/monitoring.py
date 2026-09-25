"""Handler job Lapis 2: pengambilan data pemantauan dan pembaruan connector.

Modul ini tidak mengimpor wpmgr.jobs.handlers; handlers yang mengimpor modul
ini untuk menyusun HANDLER.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from wpmgr import geoip
from wpmgr.config import get_settings
from wpmgr.connector_paket import baca_manifest, baca_zip
from wpmgr.errors import BAD_RESPONSE, SiteError
from wpmgr.jobs.queue import antrekan_jika_belum
from wpmgr.models import (
    ActivityLog,
    CatatanError,
    Job,
    JobStatus,
    JobType,
    KejadianLogin,
    LoginGagal,
    Site,
    User,
)
from wpmgr.site_client import SiteClient

log = logging.getLogger("wpmgr.jobs.monitoring")


def _email_pembuat(sesi: Session, job: Job) -> str | None:
    if job.dibuat_oleh is None:
        return None
    pembuat = sesi.get(User, job.dibuat_oleh)
    return pembuat.email if pembuat is not None else None


def tangani_update_connector(sesi: Session, job: Job, klien: SiteClient) -> dict:
    folder = get_settings().jalur_connector
    manifest = baca_manifest(folder)
    if manifest is None:
        # Kesalahan konfigurasi dashboard, bukan kondisi site: RuntimeError
        # membuat worker menandainya internal_error tanpa retry.
        raise RuntimeError(
            "Paket connector belum dibangun; jalankan python -m wpmgr.cli build-connector"
        )
    isi = baca_zip(folder)
    if hashlib.sha256(isi).hexdigest() != manifest["sha256"]:
        raise RuntimeError("Zip connector tidak cocok dengan manifest-nya; bangun ulang paket")

    site = sesi.get(Site, job.site_id)
    hasil = klien.self_update(manifest["versi"], manifest["sha256"], isi)
    sebelum = hasil.get("versi_sebelum") or site.connector_version
    sesudah = hasil.get("versi_sesudah") or manifest["versi"]
    site.connector_version = sesudah

    email = _email_pembuat(sesi, job)
    oleh = f" oleh {email}" if email else ""
    detail = {"versi_sebelum": sebelum, "versi_sesudah": sesudah, "email": email}
    if hasil.get("pesan"):
        detail["pesan"] = str(hasil["pesan"])[:500]
    sesi.add(
        ActivityLog(
            site_id=site.id, job_id=job.id, user_id=job.dibuat_oleh, level="info",
            pesan=f"Connector diperbarui: {sebelum or '?'} → {sesudah}{oleh}",
            detail=detail,
        )
    )
    sesi.commit()
    # Versi baru mungkin mengumumkan fitur baru; verify mencatatnya segera,
    # bukan menunggu scan per jam.
    antrekan_jika_belum(sesi, site.id, JobType.verify_site)
    return hasil


BATAS_HALAMAN_EVENTS = 10
JENDELA_KAITAN_UPDATE = timedelta(minutes=60)
TINGKAT_SAH = frozenset({"fatal", "warning", "database"})
KOMPONEN_SAH = frozenset({"plugin", "mu-plugin", "theme", "core", "lainnya"})
JENIS_LOGIN_SAH = frozenset({"berhasil", "admin_baru", "jadi_admin"})
# Batas kolom Postgres: `integer` (baris, login_gagal.jumlah, ...) dan
# `bigint` (id_di_site). Site yang disusupi bisa mengirim angka apa saja;
# tanpa dijepit di sini, satu baris dengan angka di luar jangkauan menolak
# seluruh transaksi halaman (lihat begin_nested() di bawah untuk lapis kedua).
_JUMLAH_MAKS = 2**31 - 1
_BARIS_MAKS = 2**31 - 1
_ID_MAKS = 2**63 - 1
# Konteks sengaja kecil (path + jenis); apa pun yang jauh lebih besar dari itu
# adalah site yang disusupi mencoba menyimpan sampah, bukan data nyata.
_BATAS_KONTEKS_BYTE = 8192
# Toleransi jam site yang meleset; lebih dari ini adalah timestamp yang
# tidak masuk akal (mis. hasil parsing yang salah), bukan sekadar jitter jam.
_TOLERANSI_MASA_DEPAN = timedelta(days=1)


def _waktu(detik) -> datetime:
    dt = datetime.fromtimestamp(int(detik), tz=timezone.utc)
    if dt > datetime.now(timezone.utc) + _TOLERANSI_MASA_DEPAN:
        raise ValueError("waktu terlalu jauh di masa depan")
    return dt


def _bersihkan_teks(s: str) -> str:
    """Buang byte NUL (ditolak kolom `text` Postgres) dan perbaiki surrogate
    lepas (diterima `json.loads` tetapi gagal di-encode ke UTF-8)."""
    s = s.replace("\x00", "")
    return s.encode("utf-8", "replace").decode("utf-8")


def _teks(nilai, panjang: int) -> str | None:
    if nilai is None:
        return None
    return _bersihkan_teks(str(nilai))[:panjang]


def _bersihkan_json(nilai):
    """Terapkan _bersihkan_teks() ke setiap string di dalam struktur JSONB,
    termasuk yang bersarang -- `konteks` datang apa adanya dari site."""
    if isinstance(nilai, str):
        return _bersihkan_teks(nilai)
    if isinstance(nilai, dict):
        return {
            (_bersihkan_teks(k) if isinstance(k, str) else k): _bersihkan_json(v)
            for k, v in nilai.items()
        }
    if isinstance(nilai, list):
        return [_bersihkan_json(v) for v in nilai]
    return nilai


def _jumlah(nilai) -> int:
    return max(1, min(int(nilai), _JUMLAH_MAKS))


def _baris_kolom(nilai) -> int | None:
    if nilai is None:
        return None
    n = int(nilai)
    return n if 0 <= n <= _BARIS_MAKS else None


def _id_bigint(nilai) -> int:
    n = int(nilai)
    if not (0 <= n <= _ID_MAKS):
        raise ValueError("id di luar jangkauan bigint")
    return n


def _konteks_bersih(nilai) -> dict | None:
    if not isinstance(nilai, dict):
        return None
    bersih = _bersihkan_json(nilai)
    if len(json.dumps(bersih)) > _BATAS_KONTEKS_BYTE:
        return None
    return bersih


def _daftar(data: dict, kunci: str) -> list:
    nilai = data.get(kunci)
    return nilai if isinstance(nilai, list) else []


def kaitkan_update(sesi: Session, site_id, komponen_tipe: str, slug: str | None,
                   pertama: datetime) -> dict | None:
    """Update sukses atas komponen ini dalam 60 menit sebelum error pertama muncul."""
    if komponen_tipe not in ("plugin", "theme") or not slug:
        return None
    kandidat = sesi.scalars(
        select(Job)
        .where(
            Job.site_id == site_id,
            Job.tipe == JobType.update_package,
            Job.status == JobStatus.success,
            Job.finished_at >= pertama - JENDELA_KAITAN_UPDATE,
            Job.finished_at <= pertama,
        )
        .order_by(Job.finished_at.desc())
    ).all()
    for job in kandidat:
        p = job.payload or {}
        if p.get("tipe") != komponen_tipe:
            continue
        slug_job = str(p.get("slug", ""))
        # Error menyebut direktori plugin ("elementor"); job menyebut berkas
        # utamanya ("elementor/elementor.php"). Tema memakai nama direktori
        # di kedua sisi.
        if komponen_tipe == "plugin":
            cocok = slug_job == slug or slug_job.startswith(slug + "/")
        else:
            cocok = slug_job == slug
        if cocok:
            hasil = job.hasil or {}
            return {
                "slug": slug_job,
                "versi_sebelum": hasil.get("versi_sebelum") or p.get("dari_versi"),
                "versi_sesudah": hasil.get("versi_sesudah") or p.get("ke_versi"),
                "job_id": job.id,
                "waktu": job.finished_at.isoformat(),
            }
    return None


def simpan_errors(sesi: Session, site: Site, baris: list) -> int:
    n = 0
    for b in baris:
        try:
            sidik = _teks(b["sidik_jari"], 64)
            tingkat = b["tingkat"]
            if not sidik or tingkat not in TINGKAT_SAH:
                continue
            komponen = b.get("komponen_tipe") if b.get("komponen_tipe") in KOMPONEN_SAH else "lainnya"
            slug = _teks(b.get("komponen_slug"), 191) or None
            nilai = {
                "tingkat": tingkat,
                "komponen_tipe": komponen,
                "komponen_slug": slug,
                "pesan": _teks(b.get("pesan"), 2000) or "",
                "file": _teks(b.get("file"), 255),
                "baris": _baris_kolom(b.get("baris")),
                "konteks": _konteks_bersih(b.get("konteks")),
                "jumlah": _jumlah(b.get("jumlah") or 1),
                "pertama_terlihat": _waktu(b["pertama"]),
                "terakhir_terlihat": _waktu(b["terakhir"]),
            }
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue

        try:
            with sesi.begin_nested():
                sudah_ada = sesi.scalar(
                    select(CatatanError.id).where(
                        CatatanError.site_id == site.id, CatatanError.sidik_jari == sidik
                    )
                ) is not None
                # ditandai_selesai_pada dan setelah_update milik dashboard: tidak
                # ada di `nilai`, sehingga upsert tidak pernah menimpanya.
                sesi.execute(
                    insert(CatatanError)
                    .values(site_id=site.id, sidik_jari=sidik, **nilai)
                    .on_conflict_do_update(constraint="uq_site_errors_sidik", set_=nilai)
                )
                if not sudah_ada:
                    kaitan = kaitkan_update(sesi, site.id, komponen, slug, nilai["pertama_terlihat"])
                    if kaitan is not None:
                        sesi.execute(
                            update(CatatanError)
                            .where(CatatanError.site_id == site.id, CatatanError.sidik_jari == sidik)
                            .values(setelah_update=kaitan)
                        )
        except (DBAPIError, UnicodeEncodeError):
            # Lapis kedua setelah validasi di atas: kalau tetap ada nilai yang
            # ditolak database, savepoint ini yang gagal, bukan seluruh
            # transaksi halaman -- baris lain di halaman yang sama tetap
            # tersimpan dan kursor tetap maju.
            log.warning("Baris error dilewati karena ditolak database (site %s)", site.id)
            continue
        n += 1
    return n


def simpan_logins(sesi: Session, site: Site, baris: list) -> int:
    n = 0
    for b in baris:
        try:
            nilai = {
                "id_di_site": _id_bigint(b["id"]),
                "waktu": _waktu(b["waktu"]),
                "jenis": str(b["jenis"]),
                "username": _teks(b.get("username"), 60) or "",
                "role": _teks(b.get("role"), 60),
                "ip": _teks(b.get("ip"), 45),
                "lewat_cloudflare": bool(b.get("lewat_cloudflare")),
                "user_agent": _teks(b.get("user_agent"), 255),
                "jalur": _teks(b.get("jalur"), 20),
            }
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        if nilai["jenis"] not in JENIS_LOGIN_SAH:
            continue
        nilai["negara"] = geoip.negara(nilai["ip"])
        try:
            with sesi.begin_nested():
                sesi.execute(
                    insert(KejadianLogin)
                    .values(site_id=site.id, **nilai)
                    .on_conflict_do_nothing(constraint="uq_login_events_id_site")
                )
        except (DBAPIError, UnicodeEncodeError):
            log.warning("Baris login dilewati karena ditolak database (site %s)", site.id)
            continue
        n += 1
    return n


def simpan_login_gagal(sesi: Session, site: Site, baris: list) -> int:
    n = 0
    for b in baris:
        try:
            nilai = {
                "jam": _waktu(b["jam"]),
                "ip": _teks(b.get("ip"), 45) or "",
                "username": _teks(b.get("username"), 60) or "",
                "jalur": _teks(b.get("jalur"), 20) or "form",
                "jumlah": _jumlah(b["jumlah"]),
                "user_agent": _teks(b.get("user_agent"), 255),
            }
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        nilai["negara"] = geoip.negara(nilai["ip"]) if nilai["ip"] else None
        try:
            # jumlah dari site sudah kumulatif per (jam, ip, username, jalur):
            # ditimpa, bukan ditambah, supaya pengambilan ulang tidak
            # menggandakan.
            with sesi.begin_nested():
                sesi.execute(
                    insert(LoginGagal)
                    .values(site_id=site.id, **nilai)
                    .on_conflict_do_update(
                        constraint="uq_login_gagal_kunci",
                        set_={"jumlah": nilai["jumlah"], "user_agent": nilai["user_agent"],
                              "negara": nilai["negara"]},
                    )
                )
        except (DBAPIError, UnicodeEncodeError):
            log.warning("Baris login gagal dilewati karena ditolak database (site %s)", site.id)
            continue
        n += 1
    return n


def tangani_collect_events(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    total = {"errors": 0, "logins": 0, "login_gagal": 0, "halaman": 0}
    kursor = site.events_kursor
    for _ in range(BATAS_HALAMAN_EVENTS):
        data = klien.events(kursor)
        if not isinstance(data, dict):
            # Connector hidup dan menjawab 200, tetapi bodinya bukan objek
            # JSON yang kita harapkan -- site di belakang proksi yang
            # mengubah body, atau bug di connector. Tanpa penjagaan ini
            # `.get()` di bawah melempar AttributeError yang tercatat sebagai
            # internal_error, padahal ini kondisi site, bukan bug dashboard.
            raise SiteError(BAD_RESPONSE, "Respons /events bukan objek JSON")
        total["errors"] += simpan_errors(sesi, site, _daftar(data, "errors"))
        total["logins"] += simpan_logins(sesi, site, _daftar(data, "logins"))
        total["login_gagal"] += simpan_login_gagal(sesi, site, _daftar(data, "login_gagal"))
        total["halaman"] += 1
        baru = data.get("kursor")
        if isinstance(baru, str) and baru:
            kursor = baru[:200]
        # Kursor disimpan per halaman: bila halaman berikutnya gagal, yang
        # sudah tersimpan tidak diambil ulang dari awal.
        site.events_kursor = kursor
        site.last_seen_at = datetime.now(timezone.utc)
        sesi.commit()
        if not data.get("lagi"):
            break
    return total
