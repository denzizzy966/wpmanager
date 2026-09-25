"""Status keamanan per site dan status turunan error (spec §8.6, §9.4).

Ambang di sini adalah titik awal, bukan setelan UI: disetel ulang di kode
setelah pola serangan nyata di site-site client terlihat.
"""

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wpmgr.models import KejadianLogin, LoginGagal

AMBANG_GAGAL_SEBELUM_TEMBUS = 5
JENDELA_TEMBUS = timedelta(hours=24)
AMBANG_SERANGAN_TOTAL = 50
AMBANG_SERANGAN_PER_IP = 20
JENDELA_SERANGAN = timedelta(minutes=60)
JENDELA_NEGARA = timedelta(days=90)
JENDELA_ERROR_BARU = timedelta(hours=24)
_AWAL = datetime(1970, 1, 1, tzinfo=timezone.utc)


class StatusKeamanan(str, enum.Enum):
    aman = "aman"
    diserang = "diserang"
    perlu_diperiksa = "perlu_diperiksa"


class StatusError(str, enum.Enum):
    selesai = "selesai"
    baru = "baru"
    masih_terjadi = "masih_terjadi"
    berhenti = "berhenti"


@dataclass
class HasilKeamanan:
    status: StatusKeamanan
    alasan: list[str] = field(default_factory=list)
    percobaan_sejam: int = 0
    ip_teratas: list[tuple[str, int]] = field(default_factory=list)


def status_error(e, sekarang: datetime) -> StatusError:
    if e.ditandai_selesai_pada is not None and e.terakhir_terlihat <= e.ditandai_selesai_pada:
        return StatusError.selesai
    if e.pertama_terlihat >= sekarang - JENDELA_ERROR_BARU:
        return StatusError.baru
    if e.terakhir_terlihat >= sekarang - JENDELA_ERROR_BARU:
        return StatusError.masih_terjadi
    return StatusError.berhenti


def error_menyalakan_chip(e, sekarang: datetime) -> bool:
    # Warning terlalu sering dan jarang berarti site rusak; tetap terlihat di tab Error.
    return e.tingkat in ("fatal", "database") and status_error(e, sekarang) in (
        StatusError.baru, StatusError.masih_terjadi
    )


def _alasan_kritis(sesi: Session, site, sejak: datetime) -> list[str]:
    alasan: list[str] = []

    admin = sesi.scalars(
        select(KejadianLogin)
        .where(KejadianLogin.site_id == site.id,
               KejadianLogin.jenis.in_(("admin_baru", "jadi_admin")),
               KejadianLogin.waktu > sejak)
        .order_by(KejadianLogin.waktu)
    ).all()
    for k in admin:
        apa = "Administrator baru" if k.jenis == "admin_baru" else "User dinaikkan menjadi administrator"
        alasan.append(f"{apa}: {k.username} ({k.waktu:%Y-%m-%d %H:%M} UTC)")

    berhasil = sesi.scalars(
        select(KejadianLogin)
        .where(KejadianLogin.site_id == site.id,
               KejadianLogin.jenis == "berhasil",
               KejadianLogin.jalur.is_distinct_from("sso"),
               KejadianLogin.waktu > sejak)
        .order_by(KejadianLogin.waktu)
    ).all()
    for k in berhasil:
        if k.ip:
            gagal = sesi.scalar(
                select(func.coalesce(func.sum(LoginGagal.jumlah), 0)).where(
                    LoginGagal.site_id == site.id,
                    LoginGagal.ip == k.ip,
                    LoginGagal.jam >= k.waktu - JENDELA_TEMBUS,
                    LoginGagal.jam <= k.waktu,
                )
            )
            if gagal >= AMBANG_GAGAL_SEBELUM_TEMBUS:
                alasan.append(
                    f"Login berhasil sebagai {k.username} dari {k.ip}, yang sebelumnya "
                    f"gagal {gagal} kali dalam 24 jam"
                )
        if k.negara:
            riwayat = select(func.count()).select_from(KejadianLogin).where(
                KejadianLogin.site_id == site.id,
                KejadianLogin.username == k.username,
                KejadianLogin.jenis == "berhasil",
                KejadianLogin.negara.is_not(None),
                KejadianLogin.waktu < k.waktu,
                KejadianLogin.waktu >= k.waktu - JENDELA_NEGARA,
            )
            pernah = sesi.scalar(riwayat)
            sama = sesi.scalar(riwayat.where(KejadianLogin.negara == k.negara))
            if pernah and not sama:
                alasan.append(f"Login {k.username} dari negara yang belum pernah dipakai: {k.negara}")
    return alasan


def nilai_keamanan(sesi: Session, site, sekarang: datetime) -> HasilKeamanan:
    per_ip = sesi.execute(
        select(LoginGagal.ip, func.sum(LoginGagal.jumlah).label("n"))
        .where(LoginGagal.site_id == site.id, LoginGagal.jam >= sekarang - JENDELA_SERANGAN)
        .group_by(LoginGagal.ip)
        .order_by(func.sum(LoginGagal.jumlah).desc())
    ).all()
    total = int(sum(n for _, n in per_ip))
    teratas = [(ip or "(IP lain)", int(n)) for ip, n in per_ip[:5]]

    # Status merah tidak padam sendiri; hanya tombol "Sudah diperiksa".
    alasan = _alasan_kritis(sesi, site, site.keamanan_diperiksa_pada or _AWAL)
    if alasan:
        return HasilKeamanan(StatusKeamanan.perlu_diperiksa, alasan, total, teratas)

    if total >= AMBANG_SERANGAN_TOTAL or any(ip and n >= AMBANG_SERANGAN_PER_IP for ip, n in per_ip):
        return HasilKeamanan(
            StatusKeamanan.diserang,
            [f"{total} percobaan login gagal dalam 60 menit terakhir"],
            total, teratas,
        )
    return HasilKeamanan(StatusKeamanan.aman, [], total, teratas)
