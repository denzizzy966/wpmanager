"""lapis 4: tabel hosting_vps, hosting_backup, dan indeks job hosting aktif

Revision ID: f2a3b4c5d6e8
Revises: e1f2a3b4c5d7
Create Date: 2026-10-03 00:00:01.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a3b4c5d6e8"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS = ("menyalin", "pratinjau", "menunggu_dns", "mengaktifkan", "aktif", "gagal")


def upgrade() -> None:
    status_hosting = postgresql.ENUM(*STATUS, name="status_hosting", create_type=False)
    status_hosting.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "hosting_vps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("nama", sa.Text(), nullable=False, unique=True),
        sa.Column("domain", sa.Text(), nullable=False, unique=True),
        sa.Column("dengan_www", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", status_hosting, nullable=False, server_default="menyalin"),
        sa.Column("gagal_asal", sa.Text()),
        sa.Column("ip_lama", sa.Text(), nullable=False),
        sa.Column("sandi_hash", sa.Text()),
        sa.Column("versi_php", sa.Text()),
        sa.Column("ukuran_file", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ukuran_db", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ditarik_pada", sa.DateTime(timezone=True)),
        sa.Column("pratinjau_sertifikat_pada", sa.DateTime(timezone=True)),
        sa.Column("dns_dicek_pada", sa.DateTime(timezone=True)),
        sa.Column("dns_hasil", postgresql.JSONB()),
        sa.Column("sertifikat_pada", sa.DateTime(timezone=True)),
        sa.Column("sertifikat_gagal_pada", sa.DateTime(timezone=True)),
        sa.Column("sertifikat_gagal_kali", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dilayani_vps_pada", sa.DateTime(timezone=True)),
        sa.Column("aktif_pada", sa.DateTime(timezone=True)),
        sa.Column("backup_terakhir_pada", sa.DateTime(timezone=True)),
        sa.Column("backup_gagal_pada", sa.DateTime(timezone=True)),
        sa.Column("galat", sa.Text()),
        sa.Column("batal_diminta_pada", sa.DateTime(timezone=True)),
        sa.Column("dibuat_pada", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("gagal_asal IS NULL OR gagal_asal IN ('salinan', 'produksi')",
                           name="ck_hosting_vps_gagal_asal"),
    )
    op.create_table(
        "hosting_backup",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("tujuan", sa.Text(), nullable=False),
        sa.Column("stempel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("manual", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("ukuran_db", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ukuran_file", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256_db", sa.Text(), nullable=False),
        sa.Column("sha256_file", sa.Text(), nullable=False),
        sa.Column("dibuat_pada", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("site_id", "tujuan", "stempel", name="uq_hosting_backup_stempel"),
    )
    op.create_index("ix_hosting_backup_site_dibuat", "hosting_backup", ["site_id", "dibuat_pada"])
    op.create_index(
        "uq_jobs_hosting_aktif", "jobs", ["site_id"], unique=True,
        postgresql_where=sa.text(
            "tipe IN ('pindah_tarik', 'pindah_aktifkan', 'backup_hosting') "
            "AND status IN ('pending', 'running')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_hosting_aktif", table_name="jobs")
    op.drop_index("ix_hosting_backup_site_dibuat", table_name="hosting_backup")
    op.drop_table("hosting_backup")
    op.drop_table("hosting_vps")
    postgresql.ENUM(name="status_hosting").drop(op.get_bind(), checkfirst=True)
