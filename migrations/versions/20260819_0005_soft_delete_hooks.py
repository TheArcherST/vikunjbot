"""Allow hook owners to retire routes without deleting delivery history."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260819_0005"
down_revision = "20260819_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hooks", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("hooks", "deleted_at")
