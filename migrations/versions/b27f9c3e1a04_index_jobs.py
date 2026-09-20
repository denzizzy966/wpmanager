"""index jobs

Revision ID: b27f9c3e1a04
Revises: a13f14d16b83
Create Date: 2026-09-21 04:10:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b27f9c3e1a04'
down_revision: str | Sequence[str] | None = 'a13f14d16b83'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index('ix_jobs_status_scheduled_for', 'jobs', ['status', 'scheduled_for'], unique=False)
    op.create_index('ix_jobs_site_id_status', 'jobs', ['site_id', 'status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_jobs_site_id_status', table_name='jobs')
    op.drop_index('ix_jobs_status_scheduled_for', table_name='jobs')
