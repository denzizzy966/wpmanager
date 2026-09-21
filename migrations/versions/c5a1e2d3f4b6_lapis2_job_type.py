"""lapis 2: nilai job_type baru

Revision ID: c5a1e2d3f4b6
Revises: b27f9c3e1a04
Create Date: 2026-09-22 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "c5a1e2d3f4b6"
down_revision: str | Sequence[str] | None = "b27f9c3e1a04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nilai enum yang baru ditambahkan tidak boleh dipakai di transaksi yang
    # sama dengan penambahannya. Revisi ini berdiri sendiri dan memakai
    # autocommit supaya revisi berikutnya (dan worker) bisa langsung
    # memakainya.
    with op.get_context().autocommit_block():
        for nilai in ("collect_events", "collect_traffic", "update_connector"):
            op.execute(f"ALTER TYPE job_type ADD VALUE IF NOT EXISTS '{nilai}'")


def downgrade() -> None:
    # PostgreSQL tidak menyediakan penghapusan nilai enum. Nilai yang tidak
    # dipakai tidak berbahaya, jadi downgrade sengaja tidak melakukan apa-apa.
    pass
