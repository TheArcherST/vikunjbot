"""Add hook ownership and configurable task display fields."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260819_0004"
down_revision = "20260818_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.add_column("hooks", sa.Column("owner_telegram_user_id", sa.BigInteger()))
    op.add_column(
        "hooks",
        sa.Column(
            "task_display_fields",
            jsonb,
            nullable=False,
            server_default=sa.text(
                "'[\"identifier\", \"status\", \"bucket\", \"due_date\", "
                "\"labels\", \"assignees\"]'::jsonb"
            ),
        ),
    )
    op.execute(
        "update hooks set owner_telegram_user_id = "
        "(allowed_telegram_user_ids ->> 0)::bigint "
        "where jsonb_array_length(allowed_telegram_user_ids) > 0"
    )
    op.alter_column("hooks", "task_display_fields", server_default=None)
    op.create_index(
        "ix_hooks_owner_telegram_user_id",
        "hooks",
        ["owner_telegram_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_hooks_owner_telegram_user_id", table_name="hooks")
    op.drop_column("hooks", "task_display_fields")
    op.drop_column("hooks", "owner_telegram_user_id")
