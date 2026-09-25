"""Status keamanan per site dan status turunan error (spec §8.6, §9.4).

Ambang di sini adalah titik awal, bukan setelan UI: disetel ulang di kode
setelah pola serangan nyata di site-site client terlihat.
"""

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from wpmgr.models import KejadianLogin, LoginGagal

AMBANG_GAGAL_SEBELUM_TEMBUS = 5
JENDELA_TEMBUS = timedelta(hours=24)
AMBANG_SERANGAN_TOTAL = 50
AMBANG_SERANGAN_PER_IP = 20
JENDELA_SERANGAN = timedelta(minutes=60)
JENDELA_NEGARA = timedelta(days=90)
JENDELA_ERROR_BARU = timedelta(hours=24)
_AWAL = datetime(1970, 1, 1, tzinfo=timezone.utc)
# LoginGagal.jam adalah AWAL jam, bukan tengahnya (connector membulatkan ke
# bawah -- koreksi #11): ember 11:00 mewakili gagal sepanjang 11:00-11:59.
# Semua jendela waktu yang dibandingkan ke `jam` mundur satu jam ekstra
# supaya ember yang sedang berjalan tetap terhitung di menit mana pun,
# bukan cuma tepat pukul xx:00.
_MARJIN_EMBER = timedelta(hours=1)
# Riwayat negara toh tidak pernah menoleh lebih jauh dari JENDELA_NEGARA;
# tanpa batas ini, site yang belum pernah "diperiksa" (sejak = 1970) akan
# memindai seluruh login_events dari awal setiap kali dipanggil.
_BATAS_KANDIDAT_BERHASIL = 50


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


def _alasan_kritis(sesi: Session, site, sekarang: datetime) -> list[str]:
    """Alasan yang membuat site "perlu_diperiksa": admin baru/dinaikkan,
    login sukses yang sebelumnya digempur brute force, atau login dari
    negara baru.

    "Sejak diperiksa" dibandingkan ke `dicatat_pada` (waktu baris ini
    TERSIMPAN di dashboard), bukan ke `waktu` (waktu kejadian di site):
    `waktu` selalu datang belakangan lewat /events (cakrawala + interval
    collect_events + jitter jam site), jadi kejadian yang terjadi sebelum
    klik "Sudah diperiksa" tetapi baru terkumpul sesudahnya tidak boleh
    dianggap sudah terlihat begitu saja (koreksi #11). `waktu` tetap dipakai
    untuk jendela waktu yang berkaitan dengan ember gagal dan riwayat negara.

    Rule (b) dan (c) diekspresikan sebagai satu query dengan sub-select
    berkorelasi (bukan satu query Python per login) supaya jumlah query
    tetap konstan walau jumlah login sukses bertambah.
    """
    alasan: list[str] = []
    sejak = site.keamanan_diperiksa_pada or _AWAL

    admin = sesi.scalars(
        select(KejadianLogin)
        .where(KejadianLogin.site_id == site.id,
               KejadianLogin.jenis.in_(("admin_baru", "jadi_admin")),
               KejadianLogin.dicatat_pada > sejak)
        .order_by(KejadianLogin.waktu)
    ).all()
    for k in admin:
        apa = "Administrator baru" if k.jenis == "admin_baru" else "User dinaikkan menjadi administrator"
        waktu_utc = k.waktu.astimezone(timezone.utc)
        alasan.append(f"{apa}: {k.username} ({waktu_utc:%Y-%m-%d %H:%M} UTC)")

    sejak_berhasil = max(sejak, sekarang - JENDELA_NEGARA)
    e2 = aliased(KejadianLogin)

    def _riwayat_negara(tambahan=()):
        return (
            select(e2.id)
            .where(
                e2.site_id == site.id,
                e2.username == KejadianLogin.username,
                e2.jenis == "berhasil",
                e2.negara.is_not(None),
                e2.waktu < KejadianLogin.waktu,
                e2.waktu >= KejadianLogin.waktu - JENDELA_NEGARA,
                *tambahan,
            )
            .correlate(KejadianLogin)
            .exists()
        )

    pernah = _riwayat_negara()
    sama = _riwayat_negara((e2.negara == KejadianLogin.negara,))
    gagal_sum = (
        select(func.coalesce(func.sum(LoginGagal.jumlah), 0))
        .where(
            LoginGagal.site_id == site.id,
            LoginGagal.ip == KejadianLogin.ip,
            LoginGagal.jam > KejadianLogin.waktu - JENDELA_TEMBUS - _MARJIN_EMBER,
            LoginGagal.jam <= KejadianLogin.waktu,
        )
        .correlate(KejadianLogin)
        .scalar_subquery()
    )

    berhasil = sesi.execute(
        select(KejadianLogin.username, KejadianLogin.ip, KejadianLogin.negara,
              gagal_sum.label("gagal"), pernah.label("pernah"), sama.label("sama"))
        .where(KejadianLogin.site_id == site.id,
               KejadianLogin.jenis == "berhasil",
               KejadianLogin.jalur.is_distinct_from("sso"),
               KejadianLogin.dicatat_pada > sejak_berhasil)
        .order_by(KejadianLogin.waktu)
        .limit(_BATAS_KANDIDAT_BERHASIL)
    ).all()
    for username, ip, negara, gagal, pernah_ada, sama_ada in berhasil:
        if ip and gagal >= AMBANG_GAGAL_SEBELUM_TEMBUS:
            alasan.append(
                f"Login berhasil sebagai {username} dari {ip}, yang sebelumnya "
                f"gagal {gagal} kali dalam 24 jam"
            )
        if negara and pernah_ada and not sama_ada:
            alasan.append(f"Login {username} dari negara yang belum pernah dipakai: {negara}")
    return alasan


def nilai_keamanan(sesi: Session, site, sekarang: datetime) -> HasilKeamanan:
    per_ip = sesi.execute(
        select(LoginGagal.ip, func.sum(LoginGagal.jumlah).label("n"))
        .where(LoginGagal.site_id == site.id,
               LoginGagal.jam > sekarang - JENDELA_SERANGAN - _MARJIN_EMBER)
        .group_by(LoginGagal.ip)
        .order_by(func.sum(LoginGagal.jumlah).desc(), LoginGagal.ip)
    ).all()
    total = int(sum(n for _, n in per_ip))
    teratas = [(ip or "(IP lain)", int(n)) for ip, n in per_ip[:5]]

    # Status merah tidak padam sendiri; hanya tombol "Sudah diperiksa".
    alasan = _alasan_kritis(sesi, site, sekarang)
    if alasan:
        return HasilKeamanan(StatusKeamanan.perlu_diperiksa, alasan, total, teratas)

    if total >= AMBANG_SERANGAN_TOTAL or any(ip and n >= AMBANG_SERANGAN_PER_IP for ip, n in per_ip):
        return HasilKeamanan(
            StatusKeamanan.diserang,
            [f"{total} percobaan login gagal dalam 60 menit terakhir"],
            total, teratas,
        )
    return HasilKeamanan(StatusKeamanan.aman, [], total, teratas)
