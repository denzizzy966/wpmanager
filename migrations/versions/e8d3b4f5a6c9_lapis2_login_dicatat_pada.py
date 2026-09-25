"""lapis 2: login_events.dicatat_pada

Revision ID: e8d3b4f5a6c9
Revises: d6b2f3e4a5c7
Create Date: 2026-09-26 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8d3b4f5a6c9"
down_revision: str | Sequence[str] | None = "d6b2f3e4a5c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "login_events",
        sa.Column("dicatat_pada", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_column("login_events", "dicatat_pada")
