"""lapis 4: nilai job_type hosting

Revision ID: e1f2a3b4c5d7
Revises: d0e1f2a3b4c5
Create Date: 2026-10-03 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "e1f2a3b4c5d7"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Pola a7c8d9e0f1b2: nilai enum baru tidak boleh dipakai di transaksi yang
    # sama dengan penambahannya, dan revisi berikutnya memakainya di predikat
    # indeks parsial.
    with op.get_context().autocommit_block():
        for nilai in ("pindah_tarik", "pindah_aktifkan", "backup_hosting"):
            op.execute(f"ALTER TYPE job_type ADD VALUE IF NOT EXISTS '{nilai}'")


def downgrade() -> None:
    # PostgreSQL tidak menyediakan penghapusan nilai enum.
    pass
