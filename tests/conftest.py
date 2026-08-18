from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from vikunjbot.database import Database
from vikunjbot.db_models import Base
from vikunjbot.settings import Settings

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://vikunjbot_test:vikunjbot_test@localhost:5437/vikunjbot_test"
)


def _test_database_url() -> URL:
    url = make_url(os.getenv("VIKUNJBOT_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))
    _assert_safe_test_database(url)
    return url


def _assert_safe_test_database(url: URL) -> None:
    database = url.database or ""
    protected_databases = {"postgres", "template0", "template1", "vikunjbot"}
    if database in protected_databases:
        raise RuntimeError(f"Refusing to use protected database {database!r} for tests")
    if not (database.startswith("vikunjbot_test") or database.endswith("_test")):
        raise RuntimeError(
            "Refusing to run destructive test setup against database "
            f"{database!r}; use a name starting with 'vikunjbot_test' or ending with '_test'."
        )


def _maintenance_url(url: URL) -> URL:
    return url.set(database=os.getenv("VIKUNJBOT_TEST_MAINTENANCE_DATABASE", "postgres"))


def _psycopg_url(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def _database_exists(connection: psycopg.AsyncConnection, database: str) -> bool:
    cursor = await connection.execute(
        "select 1 from pg_database where datname = %s",
        (database,),
    )
    return await cursor.fetchone() is not None


@pytest.fixture(scope="session")
async def test_database_url() -> AsyncIterator[str]:
    """Provision only an explicitly named disposable PostgreSQL database."""

    url = _test_database_url()
    database = url.database
    if database is None:
        raise RuntimeError("Test database URL must include a database name")

    async with await psycopg.AsyncConnection.connect(
        _psycopg_url(_maintenance_url(url)),
        autocommit=True,
    ) as connection:
        if not await _database_exists(connection, database):
            await connection.execute(f"create database {_quote_identifier(database)}")

    try:
        yield url.render_as_string(hide_password=False)
    finally:
        if os.getenv("VIKUNJBOT_TEST_DROP_DATABASE") == "1":
            _assert_safe_test_database(url)
            async with await psycopg.AsyncConnection.connect(
                _psycopg_url(_maintenance_url(url)),
                autocommit=True,
            ) as connection:
                await connection.execute(
                    """
                    select pg_terminate_backend(pid)
                    from pg_stat_activity
                    where datname = %s and pid <> pg_backend_pid()
                    """,
                    (database,),
                )
                await connection.execute(f"drop database if exists {_quote_identifier(database)}")


@pytest.fixture
def event_payload() -> Callable[..., dict[str, Any]]:
    def build(
        *,
        event_name: str = "task.created",
        task_id: int = 42,
        title: str = "Write tests",
        event_time: datetime = datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    ) -> dict[str, Any]:
        return {
            "event_name": event_name,
            "time": event_time.isoformat(),
            "data": {
                "task": {
                    "id": task_id,
                    "title": title,
                    "identifier": "DEMO-42",
                    "done": False,
                    "bucket": {"title": "Backlog"},
                    "labels": [],
                    "assignees": [],
                }
            },
        }

    return build


@pytest.fixture
def config() -> Settings:
    return Settings(
        token_encryption_key="FoaLNNRoapMeitZ4gc8xB3KMLfd9eHKvD2KpwgCOhHg=",
        telegram_bot_token="123456:token",
        worker_poll_seconds=0,
    )


@pytest.fixture
async def database(test_database_url: str) -> AsyncIterator[Database]:
    """Give every test a clean schema while preserving production ORM semantics."""

    engine = create_async_engine(test_database_url, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    database = Database(test_database_url)
    try:
        yield database
    finally:
        await database.dispose()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
