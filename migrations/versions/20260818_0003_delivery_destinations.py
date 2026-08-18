"""Name hook and message locations as delivery destinations."""

from __future__ import annotations

from alembic import op

revision = "20260818_0003"
down_revision = "20260818_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "hooks",
        "target_chat_id",
        new_column_name="delivery_destination_chat_id",
    )
    op.alter_column(
        "hooks",
        "discussion_chat_id",
        new_column_name="delivery_destination_discussion_chat_id",
    )
    op.drop_constraint("hooks_target_chat_id_not_zero", "hooks", type_="check")
    op.create_check_constraint(
        "hooks_delivery_destination_chat_id_not_zero",
        "hooks",
        "delivery_destination_chat_id <> 0",
    )

    op.alter_column(
        "task_messages",
        "chat_id",
        new_column_name="delivery_destination_chat_id",
    )
    op.alter_column(
        "task_messages",
        "discussion_chat_id",
        new_column_name="delivery_destination_discussion_chat_id",
    )
    op.execute(
        "alter index task_messages_by_reply rename to task_messages_by_delivery_destination_message"
    )
    op.execute(
        "alter index task_messages_by_discussion_reply "
        "rename to task_messages_by_delivery_destination_discussion_message"
    )


def downgrade() -> None:
    op.execute(
        "alter index task_messages_by_delivery_destination_discussion_message "
        "rename to task_messages_by_discussion_reply"
    )
    op.execute(
        "alter index task_messages_by_delivery_destination_message rename to task_messages_by_reply"
    )
    op.alter_column(
        "task_messages",
        "delivery_destination_discussion_chat_id",
        new_column_name="discussion_chat_id",
    )
    op.alter_column(
        "task_messages",
        "delivery_destination_chat_id",
        new_column_name="chat_id",
    )

    op.drop_constraint("hooks_delivery_destination_chat_id_not_zero", "hooks", type_="check")
    op.alter_column(
        "hooks",
        "delivery_destination_discussion_chat_id",
        new_column_name="discussion_chat_id",
    )
    op.alter_column(
        "hooks",
        "delivery_destination_chat_id",
        new_column_name="target_chat_id",
    )
    op.create_check_constraint("hooks_target_chat_id_not_zero", "hooks", "target_chat_id <> 0")
