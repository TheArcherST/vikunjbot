from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def test_migrations_upgrade_an_empty_database_to_head(test_database_url: str) -> None:
    """The production migration path must create the storage schema, not just the ORM."""

    root = Path(__file__).resolve().parents[1]
    engine = create_async_engine(test_database_url)
    async with engine.begin() as connection:
        await connection.execute(text("drop schema public cascade"))
        await connection.execute(text("create schema public"))

    config = Config(root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", test_database_url)
    command.upgrade(config, "20260818_0002")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                insert into hooks (
                    id, project_id, target_chat_id, discussion_chat_id,
                    allowed_telegram_user_ids, event_permission_ttl_seconds, active,
                    created_at, updated_at
                ) values (
                    '00000000-0000-0000-0000-000000000042', 1, -100111, -100222,
                    '[12]'::jsonb, 86400, true, now(), now()
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                insert into task_messages (
                    hook_id, chat_id, discussion_chat_id, task_id, message_id, expires_at,
                    allowed_telegram_user_ids, snapshot, deleted, updated_at
                ) values (
                    '00000000-0000-0000-0000-000000000042', -100111, -100222, 42, 100,
                    now(), '[12]'::jsonb, '{}'::jsonb, false, now()
                )
                """
            )
        )
    command.upgrade(config, "head")

    async with engine.connect() as connection:
        tables = set(
            (
                await connection.scalars(
                    text(
                        "select tablename from pg_tables "
                        "where schemaname = 'public' order by tablename"
                    )
                )
            ).all()
        )
        task_message_columns = set(
            (
                await connection.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'task_messages'"
                    )
                )
            ).all()
        )
        hook_columns = set(
            (
                await connection.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'hooks'"
                    )
                )
            ).all()
        )
        migrated_hook_settings = (
            (
                await connection.execute(
                    text(
                        "select owner_telegram_user_id, task_display_fields from hooks "
                        "where id = '00000000-0000-0000-0000-000000000042'"
                    )
                )
            )
            .one()
            ._tuple()
        )
        migrated_destination = (
            (
                await connection.execute(
                    text(
                        """
                        select delivery_destination_chat_id,
                               delivery_destination_discussion_chat_id
                        from task_messages
                        where task_id = 42
                        """
                    )
                )
            )
            .one()
            ._tuple()
        )
    await engine.dispose()

    assert {"alembic_version", "hooks", "events", "task_messages"} <= tables
    assert {
        "delivery_destination_chat_id",
        "delivery_destination_discussion_chat_id",
        "deleted",
    } <= task_message_columns
    assert migrated_destination == (-100111, -100222)
    assert {"owner_telegram_user_id", "task_display_fields", "deleted_at"} <= hook_columns
    assert migrated_hook_settings[0] == 12
    assert set(migrated_hook_settings[1]) == {
        "identifier",
        "status",
        "bucket",
        "due_date",
        "labels",
        "assignees",
    }
