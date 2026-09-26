"""lapis 3: nilai job_type staging

Revision ID: a7c8d9e0f1b2
Revises: f1a2b3c4d5e6
Create Date: 2026-09-26 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "a7c8d9e0f1b2"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sama seperti c5a1e2d3f4b6: nilai enum baru tidak boleh dipakai di
    # transaksi yang sama dengan penambahannya, dan revisi berikutnya memakai
    # nilai ini di predikat indeks parsial.
    with op.get_context().autocommit_block():
        for nilai in ("staging_tarik", "staging_uji_update", "staging_dorong", "staging_kembalikan"):
            op.execute(f"ALTER TYPE job_type ADD VALUE IF NOT EXISTS '{nilai}'")


def downgrade() -> None:
    # PostgreSQL tidak menyediakan penghapusan nilai enum.
    pass
