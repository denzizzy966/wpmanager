import re
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from sqlalchemy import func as safunc
from sqlalchemy import select
from sqlalchemy.orm import Session

from wpmgr.crypto import dekripsi_secret
from wpmgr.errors import SiteError
from wpmgr.jobs.monitoring import (
    tangani_collect_events,
    tangani_collect_traffic,
    tangani_update_connector,
)
from wpmgr.jobs.queue import antrekan_scan, jeda_menit
from wpmgr.models import (
    ActivityLog,
    Job,
    JobStatus,
    JobType,
    PackageType,
    Site,
    SitePackage,
    SiteStatus,
    User,
)
from wpmgr.site_client import SiteClient


def buat_klien(site: Site) -> SiteClient:
    return SiteClient(site.url, str(site.id), dekripsi_secret(site.secret_terenkripsi))


MODE_PENANGKAP_SAH = frozenset({"penuh", "terbatas"})


def simpan_kemampuan(site: Site, data: dict) -> None:
    """Catat apa yang diumumkan connector tentang dirinya sendiri.

    Connector 1.x tidak mengirim `fitur`; nilainya dikosongkan, bukan
    dibiarkan, supaya site yang di-downgrade berhenti dijadwalkan untuk
    endpoint yang tidak lagi ia punya.
    """
    fitur = data.get("fitur")
    site.fitur = sorted({f for f in fitur if isinstance(f, str)}) if isinstance(fitur, list) else []
    mode = data.get("mode_penangkap")
    site.mode_penangkap = mode if isinstance(mode, str) and mode in MODE_PENANGKAP_SAH else None
    if "percayai_xff" in data:
        site.percayai_xff = bool(data.get("percayai_xff"))
    versi = data.get("connector_version")
    if isinstance(versi, str) and versi:
        site.connector_version = versi


def _item_inventaris(data: dict) -> Iterator[tuple[PackageType, dict]]:
    inti = data.get("core")
    if inti:
        yield PackageType.core, {**inti, "slug": "core"}
    for p in data.get("plugins", []):
        yield PackageType.plugin, p
    for t in data.get("themes", []):
        yield PackageType.theme, t


def _tipe_dilaporkan(data: dict) -> set[PackageType]:
    """Kategori mana yang benar-benar dilaporkan pada payload ini.

    Pembedaannya penting: `themes: []` berarti "tema dilaporkan, tidak ada satu
    pun" dan baris tema lama memang harus dihapus. `themes` yang tidak hadir
    berarti "tidak ada informasi tentang tema", dan menghapus apa pun atas dasar
    itu adalah kehilangan data, bukan sinkronisasi.
    """
    dilaporkan: set[PackageType] = set()
    if data.get("core"):
        dilaporkan.add(PackageType.core)
    if isinstance(data.get("plugins"), list):
        dilaporkan.add(PackageType.plugin)
    if isinstance(data.get("themes"), list):
        dilaporkan.add(PackageType.theme)
    return dilaporkan


def simpan_inventaris(sesi: Session, site: Site, data: dict) -> int:
    sekarang = datetime.now(timezone.utc)
    terlihat: set[tuple[PackageType, str]] = set()
    dilaporkan = _tipe_dilaporkan(data)

    for tipe, item in _item_inventaris(data):
        kunci = (tipe, item["slug"])
        terlihat.add(kunci)
        baris = sesi.scalar(
            select(SitePackage).where(
                SitePackage.site_id == site.id,
                SitePackage.tipe == tipe,
                SitePackage.slug == item["slug"],
            )
        )
        if baris is None:
            baris = SitePackage(site_id=site.id, tipe=tipe, slug=item["slug"])
            sesi.add(baris)
        baris.nama = item["nama"]
        baris.versi_terpasang = item["versi_terpasang"]
        baris.versi_tersedia = item.get("versi_tersedia")
        baris.aktif = bool(item.get("aktif", True))
        baris.auto_update = bool(item.get("auto_update", False))
        baris.last_scan_at = sekarang

    sesi.flush()

    lama = sesi.scalars(select(SitePackage).where(SitePackage.site_id == site.id)).all()
    dihapus = 0
    for baris in lama:
        if baris.tipe in dilaporkan and (baris.tipe, baris.slug) not in terlihat:
            sesi.delete(baris)
            dihapus += 1

    site.last_scan_at = sekarang
    site.last_seen_at = sekarang
    site.last_error = None
    sesi.commit()
    return len(lama) - dihapus


def tangani_scan_site(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    data = klien.inventory()
    jumlah = simpan_inventaris(sesi, site, data)
    simpan_kemampuan(site, data)
    if site.status in (SiteStatus.unreachable, SiteStatus.needs_reconnect, SiteStatus.blocked):
        site.status = SiteStatus.active
    sesi.commit()
    return {"jumlah_paket": jumlah}


def tangani_verify_site(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    data = klien.ping()
    site.wp_version = data.get("wp_version")
    site.php_version = data.get("php_version")
    site.last_seen_at = datetime.now(timezone.utc)
    site.last_error = None
    simpan_kemampuan(site, data)
    if site.status != SiteStatus.disabled:
        site.status = SiteStatus.active
    sesi.commit()
    # Site yang baru terpasang langsung mendapat inventarisnya, bukan
    # menunggu cron jam berikutnya atau operator ingat menekan Scan.
    antrekan_scan(sesi, site.id)
    return data


def _catat_update_sukses(
    sesi: Session,
    site: Site,
    job: Job,
    versi_sebelum: str | None,
    versi_sesudah: str,
    pesan: str | None = None,
) -> None:
    """Jejak audit update yang berhasil: siapa, apa, dari versi berapa ke berapa.

    Tanpa ini, satu-satunya bukti sebuah update pernah terjadi adalah baris
    job -- yang kelak dibersihkan -- dan tidak ada jawaban untuk "siapa yang
    meng-update WooCommerce di site client X minggu lalu".
    """
    p = job.payload
    email = None
    if job.dibuat_oleh is not None:
        pembuat = sesi.get(User, job.dibuat_oleh)
        email = pembuat.email if pembuat is not None else None

    detail = {
        "tipe": p["tipe"],
        "slug": p["slug"],
        "versi_sebelum": versi_sebelum,
        "versi_sesudah": versi_sesudah,
        "dibuat_oleh": str(job.dibuat_oleh) if job.dibuat_oleh is not None else None,
        "email": email,
    }
    if pesan:
        detail["pesan"] = pesan[:500]
    oleh = f" oleh {email}" if email else ""
    sesi.add(
        ActivityLog(
            site_id=site.id,
            job_id=job.id,
            user_id=job.dibuat_oleh,
            level="info",
            pesan=f"Update {p['tipe']} {p['slug']}: {versi_sebelum or '?'} → {versi_sesudah}{oleh}",
            detail=detail,
        )
    )


def tangani_update_package(sesi: Session, job: Job, klien: SiteClient) -> dict:
    site = sesi.get(Site, job.site_id)
    p = job.payload
    hasil = klien.update(p["tipe"], p["slug"], p["ke_versi"])

    versi_sebelum = hasil.get("versi_sebelum") or p.get("dari_versi")
    versi_sesudah = hasil.get("versi_sesudah") or p["ke_versi"]
    _catat_update_sukses(sesi, site, job, versi_sebelum, versi_sesudah, hasil.get("pesan"))
    baris = sesi.scalar(
        select(SitePackage).where(
            SitePackage.site_id == site.id,
            SitePackage.tipe == PackageType(p["tipe"]),
            SitePackage.slug == p["slug"],
        )
    )
    if baris is not None:
        baris.versi_terpasang = versi_sesudah
        if baris.versi_tersedia == versi_sesudah:
            baris.versi_tersedia = None
        baris.last_scan_at = datetime.now(timezone.utc)
    else:
        sesi.add(
            ActivityLog(
                site_id=site.id,
                job_id=job.id,
                level="warning",
                pesan=f"Update berhasil untuk paket yang tidak ada di inventaris: {p['slug']}",
                detail={"tipe": p["tipe"], "slug": p["slug"], "ke_versi": p["ke_versi"]},
            )
        )
    site.last_seen_at = datetime.now(timezone.utc)
    sesi.commit()
    return hasil


def _komponen_versi(v: str) -> tuple[int, ...]:
    return tuple(
        int(p) if p.isdigit() else -1 for p in re.split(r"[.\-+_]", v.strip()) if p
    )


def _sudah_mencapai(terpasang: str, target: str) -> bool:
    """Apakah versi terpasang sudah di target atau melewatinya.

    Kesetaraan didahulukan karena itu kasus normal. Perbandingan komponen
    numerik menangani kasus client meng-update manual ke versi lebih baru
    selagi job kita berjalan.

    Bila salah satu versi memuat komponen non-numerik (mis. `1.0-beta`) dan
    keduanya tidak sama persis, jawabannya adalah False -- bukan karena kita
    tahu targetnya belum tercapai, melainkan karena kita TIDAK tahu. Sisi PHP
    memakai version_compare() yang punya aturan urutan sendiri untuk pra-rilis,
    dan menebak-nebak di sini berarti dua sisi bisa berbeda pendapat.

    False adalah jawaban yang aman: ia menghasilkan percobaan ulang, dan
    percobaan ulang aman secara konstruksi karena endpoint /update di sisi
    connector bersifat idempoten. Menjawab True secara keliru berarti menandai
    job sebagai sukses padahal update tidak pernah selesai.
    """
    if terpasang == target:
        return True

    komponen_terpasang = _komponen_versi(terpasang)
    komponen_target = _komponen_versi(target)

    if -1 in komponen_terpasang or -1 in komponen_target:
        return False

    return komponen_terpasang >= komponen_target


def _jadwalkan_ulang_atau_gagal(sesi: Session, job: Job) -> str:
    if job.attempts < job.max_attempts:
        job.status = JobStatus.pending
        job.scheduled_for = safunc.now() + timedelta(minutes=jeda_menit(job.attempts))
        job.started_at = None
        sesi.commit()
        return "pending"

    job.status = JobStatus.failed
    job.finished_at = safunc.now()
    sesi.commit()
    return "failed"


def resolusi_unknown(sesi: Session, job: Job, klien: SiteClient) -> str:
    """Setelah timeout, tanyakan keadaan sebenarnya ke site alih-alih menebak."""
    site = sesi.get(Site, job.site_id)
    p = job.payload

    try:
        simpan_inventaris(sesi, site, klien.inventory())
    except SiteError:
        # Pemeriksaan realitasnya sendiri gagal. Kita tetap tidak tahu apa yang
        # terjadi, jadi jadwalkan ulang seluruh percobaan alih-alih menebak.
        # Membiarkannya sebagai `unknown` berarti tidak ada yang akan melihatnya
        # lagi: SQL_AMBIL hanya mengklaim `pending`, reaper hanya `running`.
        return _jadwalkan_ulang_atau_gagal(sesi, job)

    baris = sesi.scalar(
        select(SitePackage).where(
            SitePackage.site_id == site.id,
            SitePackage.tipe == PackageType(p["tipe"]),
            SitePackage.slug == p["slug"],
        )
    )
    if baris is not None and _sudah_mencapai(baris.versi_terpasang, p["ke_versi"]):
        pesan = "terverifikasi lewat scan ulang setelah timeout"
        job.status = JobStatus.success
        job.hasil = {"versi_sesudah": baris.versi_terpasang, "pesan": pesan}
        job.error = None
        job.error_class = None
        job.finished_at = safunc.now()
        _catat_update_sukses(sesi, site, job, p.get("dari_versi"), baris.versi_terpasang, pesan)
        sesi.commit()
        return "success"

    return _jadwalkan_ulang_atau_gagal(sesi, job)


HANDLER = {
    JobType.scan_site: tangani_scan_site,
    JobType.update_package: tangani_update_package,
    JobType.verify_site: tangani_verify_site,
    JobType.update_connector: tangani_update_connector,
    JobType.collect_events: tangani_collect_events,
    JobType.collect_traffic: tangani_collect_traffic,
}
