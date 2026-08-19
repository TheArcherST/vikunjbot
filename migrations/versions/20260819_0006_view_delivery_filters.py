"""Add explicit delivery filtering and support every project view kind."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260819_0006"
down_revision = "20260819_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    view_kind = postgresql.ENUM(
        "list",
        "table",
        "gantt",
        "kanban",
        name="project_view_kind",
    )
    view_kind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "hooks",
        sa.Column(
            "filter_by_views",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "hook_views",
        sa.Column(
            "view_kind",
            postgresql.ENUM(
                "list",
                "table",
                "gantt",
                "kanban",
                name="project_view_kind",
                create_type=False,
            ),
            nullable=False,
            server_default="kanban",
        ),
    )
    op.add_column(
        "task_messages",
        sa.Column(
            "filtered_out",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("hooks", "filter_by_views", server_default=None)
    op.alter_column("hook_views", "view_kind", server_default=None)
    op.alter_column("task_messages", "filtered_out", server_default=None)


def downgrade() -> None:
    op.drop_column("task_messages", "filtered_out")
    op.drop_column("hook_views", "view_kind")
    op.drop_column("hooks", "filter_by_views")
    postgresql.ENUM(name="project_view_kind").drop(op.get_bind(), checkfirst=True)
