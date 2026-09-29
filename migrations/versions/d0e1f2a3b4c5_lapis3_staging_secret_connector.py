"""lapis 3: secret connector per staging (putusan R25)

Revision ID: d0e1f2a3b4c5
Revises: c9e0f1a2b3d4
Create Date: 2026-09-28 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9e0f1a2b3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Ciphertext Fernet secret connector salinan staging. Baris lama NULL:
    # tarik berikutnya membuatnya dan menanamnya ke database staging; sampai
    # itu SSO staging menolak dengan pesan "segarkan dulu".
    op.add_column("staging", sa.Column("secret_connector_terenkripsi", sa.LargeBinary()))


def downgrade() -> None:
    op.drop_column("staging", "secret_connector_terenkripsi")
