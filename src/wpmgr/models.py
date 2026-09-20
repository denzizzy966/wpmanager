import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    unknown = "unknown"


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
