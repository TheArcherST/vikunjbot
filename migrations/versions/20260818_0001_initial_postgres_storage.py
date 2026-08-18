"""Create the dedicated PostgreSQL storage for vikunjbot."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260818_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    now = sa.DateTime(timezone=True)
    op.create_table(
        "hooks",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("target_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("discussion_chat_id", sa.BigInteger()),
        sa.Column("allowed_telegram_user_ids", jsonb, nullable=False),
        sa.Column("event_permission_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", now, nullable=False),
        sa.Column("updated_at", now, nullable=False),
        sa.CheckConstraint("target_chat_id <> 0", name="hooks_target_chat_id_not_zero"),
        sa.CheckConstraint(
            "event_permission_ttl_seconds > 0", name="hooks_event_permission_ttl_positive"
        ),
    )
    op.create_table(
        "hook_views",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True, nullable=False),
        sa.Column("hook_id", uuid, sa.ForeignKey("hooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_view_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=250), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.CheckConstraint("project_view_id > 0", name="hook_views_project_view_id_positive"),
        sa.UniqueConstraint("hook_id", "project_view_id", name="hook_views_unique_view_per_hook"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True, nullable=False),
        sa.Column("hook_id", uuid, sa.ForeignKey("hooks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payload", jsonb, nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("event_name", sa.String(length=255), nullable=False),
        sa.Column("event_time", now, nullable=False),
        sa.Column("task_id", sa.BigInteger()),
        sa.Column("received_at", now, nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", now, nullable=False),
        sa.Column("lease_until", now),
        sa.Column("last_error", sa.Text()),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'retry', 'done')", name="events_valid_state"
        ),
        sa.UniqueConstraint("hook_id", "payload_sha256", name="events_deduplication"),
    )
    op.create_index("events_claimable", "events", ["state", "available_at", "id"])
    op.create_table(
        "token_bindings",
        sa.Column("telegram_user_id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("encrypted_token", sa.LargeBinary(), nullable=False),
        sa.Column("vikunja_user_id", sa.BigInteger(), nullable=False),
        sa.Column("vikunja_username", sa.String(length=255), nullable=False),
        sa.Column("created_at", now, nullable=False),
        sa.Column("updated_at", now, nullable=False),
    )
    op.create_table(
        "task_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True, nullable=False),
        sa.Column("hook_id", uuid, sa.ForeignKey("hooks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("discussion_chat_id", sa.BigInteger()),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", now, nullable=False),
        sa.Column("allowed_telegram_user_ids", jsonb, nullable=False),
        sa.Column("snapshot", jsonb, nullable=False),
        sa.Column("updated_at", now, nullable=False),
        sa.UniqueConstraint("hook_id", "task_id", name="task_messages_hook_task"),
    )
    op.create_index("task_messages_by_reply", "task_messages", ["chat_id", "message_id"])
    op.create_index(
        "task_messages_by_discussion_reply",
        "task_messages",
        ["discussion_chat_id", "chat_id", "message_id"],
    )
    op.create_table(
        "chat_settings",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("comment_updates_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", now, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_settings")
    op.drop_index("task_messages_by_discussion_reply", table_name="task_messages")
    op.drop_index("task_messages_by_reply", table_name="task_messages")
    op.drop_table("task_messages")
    op.drop_table("token_bindings")
    op.drop_index("events_claimable", table_name="events")
    op.drop_table("events")
    op.drop_table("hook_views")
    op.drop_table("hooks")
