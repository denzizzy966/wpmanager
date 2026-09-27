"""lapis 3: asal status gagal staging (putusan R20)

Revision ID: c9e0f1a2b3d4
Revises: b8d9e0f1a2c3
Create Date: 2026-09-27 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e0f1a2b3d4"
down_revision: str | Sequence[str] | None = "b8d9e0f1a2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 'salinan': tarik/uji gagal, salinan staging bisa setengah jadi (dorong
    # ditolak, R19). 'produksi': dorong/kembalikan gagal sesudah tukar. Baris
    # lama dibiarkan NULL: NULL tidak pernah menahan dorong.
    op.add_column("staging", sa.Column("gagal_asal", sa.Text()))
    op.create_check_constraint(
        "ck_staging_gagal_asal", "staging", "gagal_asal IS NULL OR gagal_asal IN ('salinan', 'produksi')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_staging_gagal_asal", "staging", type_="check")
    op.drop_column("staging", "gagal_asal")
