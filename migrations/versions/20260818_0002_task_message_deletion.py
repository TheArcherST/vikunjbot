"""Make Telegram task-message mappings terminal after task deletion."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260818_0002"
down_revision = "20260818_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_messages",
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("task_messages", "deleted", server_default=None)


def downgrade() -> None:
    op.drop_column("task_messages", "deleted")
