"""lapis 2: kunci unik login_events memuat waktu

AUTO_INCREMENT tabel login di site bisa mulai ulang (restore backup, atau
MySQL 5.7 setelah restart), sehingga id lama dipakai lagi untuk kejadian
baru. Dengan kunci (site_id, id_di_site) saja, kejadian baru itu -- termasuk
admin_baru -- dibuang diam-diam oleh ON CONFLICT DO NOTHING.

Revision ID: f1a2b3c4d5e6
Revises: e8d3b4f5a6c9
Create Date: 2026-09-26 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e8d3b4f5a6c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_login_events_id_site", "login_events", type_="unique")
    op.create_unique_constraint(
        "uq_login_events_id_site_waktu", "login_events", ["site_id", "id_di_site", "waktu"]
    )


def downgrade() -> None:
    # Bisa gagal bila sudah ada id kembar dengan waktu berbeda; itu memang
    # data yang tidak muat di kunci lama dan harus dibereskan manual.
    op.drop_constraint("uq_login_events_id_site_waktu", "login_events", type_="unique")
    op.create_unique_constraint(
        "uq_login_events_id_site", "login_events", ["site_id", "id_di_site"]
    )
