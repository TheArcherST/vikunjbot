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
    await engine.dispose()

    assert {"alembic_version", "hooks", "events", "task_messages"} <= tables
