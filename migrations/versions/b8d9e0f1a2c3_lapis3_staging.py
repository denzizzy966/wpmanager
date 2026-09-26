"""lapis 3: tabel staging, snapshot, uji, dan indeks job staging aktif

Revision ID: b8d9e0f1a2c3
Revises: a7c8d9e0f1b2
Create Date: 2026-09-26 00:00:01.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8d9e0f1a2c3"
down_revision: str | Sequence[str] | None = "a7c8d9e0f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS = ("menyalin", "siap", "berjalan_uji", "mendorong", "dijeda", "gagal")


def upgrade() -> None:
    status_staging = postgresql.ENUM(*STATUS, name="status_staging", create_type=False)
    status_staging.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "staging",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("nama", sa.Text(), nullable=False, unique=True),
        sa.Column("status", status_staging, nullable=False, server_default="menyalin"),
        sa.Column("aktif", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sandi_hash", sa.Text()),
        sa.Column("rahasia_router_terenkripsi", sa.LargeBinary()),
        sa.Column("versi_php", sa.Text()),
        sa.Column("ukuran_file", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ukuran_db", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ditarik_pada", sa.DateTime(timezone=True)),
        sa.Column("tanda_air", postgresql.JSONB()),
        sa.Column("diubah_pada", sa.DateTime(timezone=True)),
        sa.Column("dibuka_pada", sa.DateTime(timezone=True)),
        sa.Column("sertifikat_pada", sa.DateTime(timezone=True)),
        sa.Column("galat", sa.Text()),
        sa.Column("dorong_gagal_pada", sa.DateTime(timezone=True)),
        sa.Column("batal_diminta_pada", sa.DateTime(timezone=True)),
        sa.Column("dibuat_pada", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "staging_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("jenis", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("ukuran", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("dibuat_pada", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_staging_snapshot_site_dibuat", "staging_snapshot", ["site_id", "dibuat_pada"])
    op.create_table(
        "staging_uji",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("paket", postgresql.JSONB(), nullable=False),
        sa.Column("hasil", sa.Text(), nullable=False),
        sa.Column("pemeriksaan", postgresql.JSONB(), nullable=False),
        sa.Column("dibuat_pada", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_staging_uji_site_dibuat", "staging_uji", ["site_id", "dibuat_pada"])
    op.create_index(
        "uq_jobs_staging_aktif", "jobs", ["site_id"], unique=True,
        postgresql_where=sa.text(
            "tipe IN ('staging_tarik', 'staging_uji_update', 'staging_dorong', "
            "'staging_kembalikan') AND status IN ('pending', 'running')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_staging_aktif", table_name="jobs")
    op.drop_index("ix_staging_uji_site_dibuat", table_name="staging_uji")
    op.drop_table("staging_uji")
    op.drop_index("ix_staging_snapshot_site_dibuat", table_name="staging_snapshot")
    op.drop_table("staging_snapshot")
    op.drop_table("staging")
    postgresql.ENUM(name="status_staging").drop(op.get_bind(), checkfirst=True)
