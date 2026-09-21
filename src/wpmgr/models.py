import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SiteStatus(str, enum.Enum):
    pending_pair = "pending_pair"
    active = "active"
    needs_reconnect = "needs_reconnect"
    blocked = "blocked"
    unreachable = "unreachable"
    disabled = "disabled"


class PackageType(str, enum.Enum):
    core = "core"
    plugin = "plugin"
    theme = "theme"


class JobType(str, enum.Enum):
    scan_site = "scan_site"
    update_package = "update_package"
    verify_site = "verify_site"
    collect_events = "collect_events"
    collect_traffic = "collect_traffic"
    update_connector = "update_connector"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    unknown = "unknown"


class UptimeStatus(str, enum.Enum):
    belum_dicek = "belum_dicek"
    naik = "naik"
    mati = "mati"
    terblokir = "terblokir"


class UptimeHasil(str, enum.Enum):
    naik = "naik"
    gagal = "gagal"
    terblokir = "terblokir"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    nama: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="admin")
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    nama: Mapped[str] = mapped_column(Text, nullable=False)
    kontak: Mapped[str | None] = mapped_column(Text)
    catatan: Mapped[str | None] = mapped_column(Text)
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL")
    )
    nama: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[SiteStatus] = mapped_column(
        Enum(SiteStatus, name="site_status"), nullable=False, default=SiteStatus.pending_pair
    )
    secret_terenkripsi: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    connector_version: Mapped[str | None] = mapped_column(Text)
    wp_version: Mapped[str | None] = mapped_column(Text)
    php_version: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # Lapis 2 -- dilaporkan connector (bagian 11.3 spec Lapis 2)
    fitur: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'")
    )
    mode_penangkap: Mapped[str | None] = mapped_column(Text)
    percayai_xff: Mapped[bool | None] = mapped_column(Boolean)
    events_kursor: Mapped[str | None] = mapped_column(Text)
    traffic_diambil_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Lapis 2 -- uptime dan SSL
    uptime_status: Mapped[UptimeStatus] = mapped_column(
        Enum(UptimeStatus, name="uptime_status"), nullable=False,
        default=UptimeStatus.belum_dicek, server_default="belum_dicek",
    )
    uptime_sejak: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uptime_gagal_beruntun: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ssl_kedaluwarsa: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ssl_dicek_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ssl_error: Mapped[str | None] = mapped_column(Text)
    # Lapis 2 -- keamanan dan GA4
    keamanan_diperiksa_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ga4_property_id: Mapped[str | None] = mapped_column(Text)
    ga4_diambil_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ga4_error: Mapped[str | None] = mapped_column(Text)
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SitePackage(Base):
    __tablename__ = "site_packages"
    __table_args__ = (UniqueConstraint("site_id", "tipe", "slug", name="uq_site_package"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tipe: Mapped[PackageType] = mapped_column(Enum(PackageType, name="package_type"), nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    nama: Mapped[str] = mapped_column(Text, nullable=False)
    versi_terpasang: Mapped[str] = mapped_column(Text, nullable=False)
    versi_tersedia: Mapped[str | None] = mapped_column(Text)
    aktif: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_update: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_scan_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_scheduled_for", "status", "scheduled_for"),
        Index("ix_jobs_site_id_status", "site_id", "status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    tipe: Mapped[JobType] = mapped_column(Enum(JobType, name="job_type"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), nullable=False, default=JobStatus.pending
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hasil: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    error_class: Mapped[str | None] = mapped_column(Text)
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dibuat_oleh: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class ActivityLog(Base):
    __tablename__ = "activity_log"
    # Laporan bulanan dan tab Aktivitas membaca per site menurut waktu
    # (R60 Lapis 1, menjadi wajib di Lapis 2).
    __table_args__ = (Index("ix_activity_log_site_dibuat", "site_id", "dibuat_pada"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    level: Mapped[str] = mapped_column(Text, nullable=False)
    pesan: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    dibuat_pada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UptimePutaran(Base):
    __tablename__ = "uptime_putaran"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mulai: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    jumlah_site: Mapped[int] = mapped_column(Integer, nullable=False)
    jumlah_gagal: Mapped[int] = mapped_column(Integer, nullable=False)
    gangguan_dashboard: Mapped[bool] = mapped_column(Boolean, nullable=False)


class UptimeCheck(Base):
    __tablename__ = "uptime_checks"
    __table_args__ = (Index("ix_uptime_checks_site_dicek", "site_id", "dicek_pada"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    putaran_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("uptime_putaran.id", ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    dicek_pada: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    hasil: Mapped[UptimeHasil] = mapped_column(Enum(UptimeHasil, name="uptime_hasil"), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    waktu_ms: Mapped[int | None] = mapped_column(Integer)
    pesan: Mapped[str | None] = mapped_column(Text)


class UptimeInsiden(Base):
    __tablename__ = "uptime_insiden"
    __table_args__ = (
        Index("ix_uptime_insiden_site_mulai", "site_id", "mulai"),
        # Paling banyak satu insiden terbuka per site; penjaga terakhir bila
        # dua putaran uptime entah bagaimana berjalan bersamaan.
        Index("uq_uptime_insiden_terbuka", "site_id", unique=True,
              postgresql_where=text("selesai IS NULL")),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    mulai: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    selesai: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    penyebab: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)


class CatatanError(Base):
    """Satu error PHP unik (per sidik jari) di satu site."""

    __tablename__ = "site_errors"
    __table_args__ = (
        UniqueConstraint("site_id", "sidik_jari", name="uq_site_errors_sidik"),
        Index("ix_site_errors_site_terakhir", "site_id", "terakhir_terlihat"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    sidik_jari: Mapped[str] = mapped_column(Text, nullable=False)
    tingkat: Mapped[str] = mapped_column(Text, nullable=False)
    komponen_tipe: Mapped[str] = mapped_column(Text, nullable=False)
    komponen_slug: Mapped[str | None] = mapped_column(Text)
    pesan: Mapped[str] = mapped_column(Text, nullable=False)
    file: Mapped[str | None] = mapped_column(Text)
    baris: Mapped[int | None] = mapped_column(Integer)
    konteks: Mapped[dict | None] = mapped_column(JSONB)
    jumlah: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pertama_terlihat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terakhir_terlihat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    setelah_update: Mapped[dict | None] = mapped_column(JSONB)
    ditandai_selesai_pada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KejadianLogin(Base):
    """Login berhasil dan kemunculan administrator, satu baris per kejadian."""

    __tablename__ = "login_events"
    __table_args__ = (
        UniqueConstraint("site_id", "id_di_site", name="uq_login_events_id_site"),
        Index("ix_login_events_site_waktu", "site_id", "waktu"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    id_di_site: Mapped[int] = mapped_column(BigInteger, nullable=False)
    waktu: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    jenis: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(Text)
    lewat_cloudflare: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    negara: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    jalur: Mapped[str | None] = mapped_column(Text)


class LoginGagal(Base):
    """Login gagal teragregasi per jam per (IP, username, jalur)."""

    __tablename__ = "login_gagal"
    __table_args__ = (
        UniqueConstraint("site_id", "jam", "ip", "username", "jalur", name="uq_login_gagal_kunci"),
        Index("ix_login_gagal_site_jam", "site_id", "jam"),
        Index("ix_login_gagal_ip_jam", "ip", "jam"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    jam: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # '' = baris "(IP lain)"; lihat catatan koreksi #3 di rencana.
    ip: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    jalur: Mapped[str] = mapped_column(Text, nullable=False)
    jumlah: Mapped[int] = mapped_column(Integer, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text)
    negara: Mapped[str | None] = mapped_column(Text)


class TrafficHarian(Base):
    __tablename__ = "traffic_harian"
    __table_args__ = (PrimaryKeyConstraint("site_id", "tanggal", "sumber"),)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    tanggal: Mapped[date] = mapped_column(Date, nullable=False)
    sumber: Mapped[str] = mapped_column(Text, nullable=False)
    kunjungan: Mapped[int] = mapped_column(Integer, nullable=False)
    pengunjung: Mapped[int] = mapped_column(Integer, nullable=False)


class TrafficRincian(Base):
    __tablename__ = "traffic_rincian"
    __table_args__ = (PrimaryKeyConstraint("site_id", "tanggal", "sumber", "dimensi", "kunci"),)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    tanggal: Mapped[date] = mapped_column(Date, nullable=False)
    sumber: Mapped[str] = mapped_column(Text, nullable=False)
    dimensi: Mapped[str] = mapped_column(Text, nullable=False)
    kunci: Mapped[str] = mapped_column(Text, nullable=False)
    kunjungan: Mapped[int] = mapped_column(Integer, nullable=False)
