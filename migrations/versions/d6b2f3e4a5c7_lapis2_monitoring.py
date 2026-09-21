"""lapis 2: tabel dan kolom monitoring

Revision ID: d6b2f3e4a5c7
Revises: c5a1e2d3f4b6
Create Date: 2026-09-22 00:05:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6b2f3e4a5c7"
down_revision: str | Sequence[str] | None = "c5a1e2d3f4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPTIME_STATUS = ("belum_dicek", "naik", "mati", "terblokir")
UPTIME_HASIL = ("naik", "gagal", "terblokir")


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*UPTIME_STATUS, name="uptime_status").create(bind, checkfirst=False)
    sa.Enum(*UPTIME_HASIL, name="uptime_hasil").create(bind, checkfirst=False)
    status_t = postgresql.ENUM(*UPTIME_STATUS, name="uptime_status", create_type=False)
    hasil_t = postgresql.ENUM(*UPTIME_HASIL, name="uptime_hasil", create_type=False)
    ts = sa.DateTime(timezone=True)

    op.add_column("sites", sa.Column("fitur", postgresql.ARRAY(sa.Text()), nullable=False,
                                     server_default=sa.text("'{}'")))
    op.add_column("sites", sa.Column("mode_penangkap", sa.Text()))
    op.add_column("sites", sa.Column("percayai_xff", sa.Boolean()))
    op.add_column("sites", sa.Column("events_kursor", sa.Text()))
    op.add_column("sites", sa.Column("traffic_diambil_pada", ts))
    op.add_column("sites", sa.Column("uptime_status", status_t, nullable=False,
                                     server_default="belum_dicek"))
    op.add_column("sites", sa.Column("uptime_sejak", ts))
    op.add_column("sites", sa.Column("uptime_gagal_beruntun", sa.Integer(), nullable=False,
                                     server_default="0"))
    op.add_column("sites", sa.Column("ssl_kedaluwarsa", ts))
    op.add_column("sites", sa.Column("ssl_dicek_pada", ts))
    op.add_column("sites", sa.Column("ssl_error", sa.Text()))
    op.add_column("sites", sa.Column("keamanan_diperiksa_pada", ts))
    op.add_column("sites", sa.Column("ga4_property_id", sa.Text()))
    op.add_column("sites", sa.Column("ga4_diambil_pada", ts))
    op.add_column("sites", sa.Column("ga4_error", sa.Text()))

    def site_fk() -> sa.ForeignKey:
        # Setiap Column butuh instance ForeignKey sendiri -- satu objek
        # ForeignKey tidak bisa dipakai ulang untuk beberapa kolom sekaligus.
        return sa.ForeignKey("sites.id", ondelete="CASCADE")

    op.create_table(
        "uptime_putaran",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("mulai", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("jumlah_site", sa.Integer(), nullable=False),
        sa.Column("jumlah_gagal", sa.Integer(), nullable=False),
        sa.Column("gangguan_dashboard", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "uptime_checks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("putaran_id", sa.BigInteger(),
                  sa.ForeignKey("uptime_putaran.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk(), nullable=False),
        sa.Column("dicek_pada", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("hasil", hasil_t, nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("waktu_ms", sa.Integer()),
        sa.Column("pesan", sa.Text()),
    )
    op.create_index("ix_uptime_checks_site_dicek", "uptime_checks", ["site_id", "dicek_pada"])
    op.create_table(
        "uptime_insiden",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk(), nullable=False),
        sa.Column("mulai", ts, nullable=False),
        sa.Column("selesai", ts),
        sa.Column("penyebab", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer()),
    )
    op.create_index("ix_uptime_insiden_site_mulai", "uptime_insiden", ["site_id", "mulai"])
    op.create_index("uq_uptime_insiden_terbuka", "uptime_insiden", ["site_id"], unique=True,
                    postgresql_where=sa.text("selesai IS NULL"))
    op.create_table(
        "site_errors",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk(), nullable=False),
        sa.Column("sidik_jari", sa.Text(), nullable=False),
        sa.Column("tingkat", sa.Text(), nullable=False),
        sa.Column("komponen_tipe", sa.Text(), nullable=False),
        sa.Column("komponen_slug", sa.Text()),
        sa.Column("pesan", sa.Text(), nullable=False),
        sa.Column("file", sa.Text()),
        sa.Column("baris", sa.Integer()),
        sa.Column("konteks", postgresql.JSONB()),
        sa.Column("jumlah", sa.BigInteger(), nullable=False),
        sa.Column("pertama_terlihat", ts, nullable=False),
        sa.Column("terakhir_terlihat", ts, nullable=False),
        sa.Column("setelah_update", postgresql.JSONB()),
        sa.Column("ditandai_selesai_pada", ts),
        sa.UniqueConstraint("site_id", "sidik_jari", name="uq_site_errors_sidik"),
    )
    op.create_index("ix_site_errors_site_terakhir", "site_errors", ["site_id", "terakhir_terlihat"])
    op.create_table(
        "login_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk(), nullable=False),
        sa.Column("id_di_site", sa.BigInteger(), nullable=False),
        sa.Column("waktu", ts, nullable=False),
        sa.Column("jenis", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("role", sa.Text()),
        sa.Column("ip", sa.Text()),
        sa.Column("lewat_cloudflare", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("negara", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("jalur", sa.Text()),
        sa.UniqueConstraint("site_id", "id_di_site", name="uq_login_events_id_site"),
    )
    op.create_index("ix_login_events_site_waktu", "login_events", ["site_id", "waktu"])
    op.create_table(
        "login_gagal",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk(), nullable=False),
        sa.Column("jam", ts, nullable=False),
        sa.Column("ip", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("jalur", sa.Text(), nullable=False),
        sa.Column("jumlah", sa.Integer(), nullable=False),
        sa.Column("user_agent", sa.Text()),
        sa.Column("negara", sa.Text()),
        sa.UniqueConstraint("site_id", "jam", "ip", "username", "jalur",
                            name="uq_login_gagal_kunci"),
    )
    op.create_index("ix_login_gagal_site_jam", "login_gagal", ["site_id", "jam"])
    op.create_index("ix_login_gagal_ip_jam", "login_gagal", ["ip", "jam"])
    op.create_table(
        "traffic_harian",
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk(), nullable=False),
        sa.Column("tanggal", sa.Date(), nullable=False),
        sa.Column("sumber", sa.Text(), nullable=False),
        sa.Column("kunjungan", sa.Integer(), nullable=False),
        sa.Column("pengunjung", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("site_id", "tanggal", "sumber"),
    )
    op.create_table(
        "traffic_rincian",
        sa.Column("site_id", postgresql.UUID(as_uuid=True), site_fk(), nullable=False),
        sa.Column("tanggal", sa.Date(), nullable=False),
        sa.Column("sumber", sa.Text(), nullable=False),
        sa.Column("dimensi", sa.Text(), nullable=False),
        sa.Column("kunci", sa.Text(), nullable=False),
        sa.Column("kunjungan", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("site_id", "tanggal", "sumber", "dimensi", "kunci"),
    )
    op.create_index("ix_activity_log_site_dibuat", "activity_log", ["site_id", "dibuat_pada"])


def downgrade() -> None:
    op.drop_index("ix_activity_log_site_dibuat", table_name="activity_log")
    for tabel in ("traffic_rincian", "traffic_harian", "login_gagal", "login_events",
                  "site_errors", "uptime_insiden", "uptime_checks", "uptime_putaran"):
        op.drop_table(tabel)
    for kolom in ("ga4_error", "ga4_diambil_pada", "ga4_property_id", "keamanan_diperiksa_pada",
                  "ssl_error", "ssl_dicek_pada", "ssl_kedaluwarsa", "uptime_gagal_beruntun",
                  "uptime_sejak", "uptime_status", "traffic_diambil_pada", "events_kursor",
                  "percayai_xff", "mode_penangkap", "fitur"):
        op.drop_column("sites", kolom)
    sa.Enum(name="uptime_hasil").drop(op.get_bind(), checkfirst=False)
    sa.Enum(name="uptime_status").drop(op.get_bind(), checkfirst=False)
