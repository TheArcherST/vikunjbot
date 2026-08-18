from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from vikunjbot.timeutils import exponential_backoff, from_db_time, to_db_time, utc_now


@dataclass(frozen=True, slots=True)
class StoredEvent:
    id: int
    route_tag: str
    payload: dict[str, Any]
    event_name: str
    event_time: datetime
    task_id: int | None
    attempts: int


@dataclass(frozen=True, slots=True)
class TaskMessage:
    chat_id: int
    task_id: int
    message_id: int
    expires_at: datetime
    allowed_telegram_user_ids: frozenset[int]
    snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TokenBinding:
    telegram_user_id: int
    encrypted_token: bytes
    vikunja_user_id: int
    vikunja_username: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    route_tag TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    event_name TEXT NOT NULL,
    event_time TEXT NOT NULL,
    task_id INTEGER,
    received_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'processing', 'retry', 'done')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    lease_until TEXT,
    last_error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS events_deduplication
    ON events(route_tag, payload_sha256);
CREATE INDEX IF NOT EXISTS events_claimable
    ON events(state, available_at, id);

CREATE TABLE IF NOT EXISTS token_bindings (
    telegram_user_id INTEGER PRIMARY KEY,
    encrypted_token BLOB NOT NULL,
    vikunja_user_id INTEGER NOT NULL,
    vikunja_username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_messages (
    chat_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    allowed_telegram_user_ids_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, task_id)
);
CREATE INDEX IF NOT EXISTS task_messages_by_reply
    ON task_messages(chat_id, message_id);

CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id INTEGER PRIMARY KEY,
    comment_updates_enabled INTEGER NOT NULL DEFAULT 0 CHECK (comment_updates_enabled IN (0, 1)),
    updated_at TEXT NOT NULL
);
"""


class Database:
    """Small, transactional SQLite persistence layer shared by both services.

    Each method owns a short-lived connection. SQLite WAL permits the relay's
    writer and the bot's reader to run from separate containers against the same
    mounted file, while `synchronous=FULL` ensures an accepted webhook survives
    a host crash subject to the filesystem's durability guarantees.
    """

    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(_SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            yield connection
        finally:
            connection.close()

    def enqueue_event(
        self, route_tag: str, raw_body: bytes, payload: dict[str, Any]
    ) -> tuple[int, bool]:
        event_time = _event_time(payload)
        now = utc_now()
        digest = hashlib.sha256(raw_body).hexdigest()
        task_id = _task_id(payload)
        values = (
            route_tag,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            digest,
            str(payload["event_name"]),
            to_db_time(event_time),
            task_id,
            to_db_time(now),
            to_db_time(now),
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                inserted = connection.execute(
                    """
                    INSERT INTO events (
                        route_tag, payload_json, payload_sha256, event_name, event_time,
                        task_id, received_at, state, available_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    ON CONFLICT(route_tag, payload_sha256) DO NOTHING
                    """,
                    values,
                )
                if inserted.rowcount:
                    event_id = int(inserted.lastrowid)
                    created = True
                else:
                    duplicate = connection.execute(
                        "SELECT id FROM events WHERE route_tag = ? AND payload_sha256 = ?",
                        (route_tag, digest),
                    ).fetchone()
                    if duplicate is None:  # pragma: no cover - defensive SQLite invariant
                        raise RuntimeError("event deduplication lookup failed")
                    event_id = int(duplicate["id"])
                    created = False
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return event_id, created

    def claim_next_event(self, lease_seconds: int) -> StoredEvent | None:
        now = utc_now()
        now_value = to_db_time(now)
        lease_until = to_db_time(now + timedelta(seconds=lease_seconds))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE events
                    SET state = 'retry', lease_until = NULL, available_at = ?
                    WHERE state = 'processing' AND lease_until < ?
                    """,
                    (now_value, now_value),
                )
                row = connection.execute(
                    """
                    SELECT * FROM events
                    WHERE state IN ('pending', 'retry') AND available_at <= ?
                    ORDER BY id
                    LIMIT 1
                    """,
                    (now_value,),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                connection.execute(
                    """
                    UPDATE events
                    SET state = 'processing', attempts = attempts + 1, lease_until = ?
                    WHERE id = ?
                    """,
                    (lease_until, row["id"]),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return _stored_event(row, attempts=int(row["attempts"]) + 1)

    def complete_event(self, event_id: int) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE events
                SET state = 'done', lease_until = NULL, last_error = NULL
                WHERE id = ?
                """,
                (event_id,),
            )

    def retry_event(self, event_id: int, attempts: int, error: str, maximum_backoff: int) -> None:
        available_at = utc_now() + exponential_backoff(attempts, maximum_backoff)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE events
                SET state = 'retry', lease_until = NULL, available_at = ?, last_error = ?
                WHERE id = ?
                """,
                (to_db_time(available_at), error[:2_000], event_id),
            )

    def save_token_binding(
        self,
        telegram_user_id: int,
        encrypted_token: bytes,
        vikunja_user_id: int,
        vikunja_username: str,
    ) -> None:
        now = to_db_time(utc_now())
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO token_bindings (
                    telegram_user_id, encrypted_token, vikunja_user_id,
                    vikunja_username, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    encrypted_token = excluded.encrypted_token,
                    vikunja_user_id = excluded.vikunja_user_id,
                    vikunja_username = excluded.vikunja_username,
                    updated_at = excluded.updated_at
                """,
                (telegram_user_id, encrypted_token, vikunja_user_id, vikunja_username, now, now),
            )

    def get_token_binding(self, telegram_user_id: int) -> TokenBinding | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM token_bindings WHERE telegram_user_id = ?", (telegram_user_id,)
            ).fetchone()
        if row is None:
            return None
        return TokenBinding(
            telegram_user_id=int(row["telegram_user_id"]),
            encrypted_token=bytes(row["encrypted_token"]),
            vikunja_user_id=int(row["vikunja_user_id"]),
            vikunja_username=str(row["vikunja_username"]),
        )

    def delete_token_binding(self, telegram_user_id: int) -> bool:
        with self._connection() as connection:
            result = connection.execute(
                "DELETE FROM token_bindings WHERE telegram_user_id = ?", (telegram_user_id,)
            )
        return bool(result.rowcount)

    def get_task_message(self, chat_id: int, task_id: int) -> TaskMessage | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM task_messages WHERE chat_id = ? AND task_id = ?", (chat_id, task_id)
            ).fetchone()
        if row is None:
            return None
        return TaskMessage(
            chat_id=int(row["chat_id"]),
            task_id=int(row["task_id"]),
            message_id=int(row["message_id"]),
            expires_at=from_db_time(str(row["expires_at"])),
            allowed_telegram_user_ids=frozenset(
                json.loads(str(row["allowed_telegram_user_ids_json"]))
            ),
            snapshot=json.loads(str(row["snapshot_json"])),
        )

    def find_task_message(self, chat_id: int, message_id: int) -> TaskMessage | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM task_messages WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()
        if row is None:
            return None
        return TaskMessage(
            chat_id=int(row["chat_id"]),
            task_id=int(row["task_id"]),
            message_id=int(row["message_id"]),
            expires_at=from_db_time(str(row["expires_at"])),
            allowed_telegram_user_ids=frozenset(
                json.loads(str(row["allowed_telegram_user_ids_json"]))
            ),
            snapshot=json.loads(str(row["snapshot_json"])),
        )

    def save_task_message(
        self,
        chat_id: int,
        task_id: int,
        message_id: int,
        expires_at: datetime,
        allowed_telegram_user_ids: frozenset[int],
        snapshot: dict[str, Any],
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO task_messages (
                    chat_id, task_id, message_id, expires_at, allowed_telegram_user_ids_json,
                    snapshot_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, task_id) DO UPDATE SET
                    message_id = excluded.message_id,
                    expires_at = excluded.expires_at,
                    allowed_telegram_user_ids_json = excluded.allowed_telegram_user_ids_json,
                    snapshot_json = excluded.snapshot_json,
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    task_id,
                    message_id,
                    to_db_time(expires_at),
                    json.dumps(sorted(allowed_telegram_user_ids)),
                    json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                    to_db_time(utc_now()),
                ),
            )

    def comment_updates_enabled(self, chat_id: int) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT comment_updates_enabled FROM chat_settings WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return bool(row and row["comment_updates_enabled"])

    def set_comment_updates_enabled(self, chat_id: int, enabled: bool) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO chat_settings (chat_id, comment_updates_enabled, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    comment_updates_enabled = excluded.comment_updates_enabled,
                    updated_at = excluded.updated_at
                """,
                (chat_id, int(enabled), to_db_time(utc_now())),
            )


def _event_time(payload: dict[str, Any]) -> datetime:
    from vikunjbot.timeutils import parse_event_time

    return parse_event_time(payload.get("time"))


def _task_id(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    task = data.get("task")
    if isinstance(task, dict) and isinstance(task.get("id"), int):
        return int(task["id"])
    comment = data.get("comment")
    if isinstance(comment, dict) and isinstance(comment.get("task_id"), int):
        return int(comment["task_id"])
    return None


def _stored_event(row: sqlite3.Row, attempts: int) -> StoredEvent:
    payload = json.loads(str(row["payload_json"]))
    if not isinstance(payload, dict):  # pragma: no cover - validated on insert
        raise RuntimeError("persisted event payload is not an object")
    return StoredEvent(
        id=int(row["id"]),
        route_tag=str(row["route_tag"]),
        payload=payload,
        event_name=str(row["event_name"]),
        event_time=from_db_time(str(row["event_time"])),
        task_id=int(row["task_id"]) if row["task_id"] is not None else None,
        attempts=attempts,
    )
