from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram.enums import ChatMemberStatus
from aiogram.types import Chat, MessageOriginChannel

from vikunjbot.bot import (
    ChannelBindingError,
    _channel_discussion_from_forward,
    _task_message_for_reply,
)
from vikunjbot.database import Database


class ChannelBot:
    def __init__(self, requester_status: str = "administrator") -> None:
        self.requester_status = requester_status

    async def get_chat(self, _: int) -> SimpleNamespace:
        return SimpleNamespace(id=-100111, type="channel", linked_chat_id=-100222)

    async def get_me(self) -> SimpleNamespace:
        return SimpleNamespace(id=999)

    async def get_chat_member(self, chat_id: int, user_id: int) -> SimpleNamespace:
        if chat_id == -100111 and user_id == 12:
            return SimpleNamespace(status=self.requester_status)
        return SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR, can_post_messages=True)


def _channel_forward() -> SimpleNamespace:
    return SimpleNamespace(
        forward_origin=MessageOriginChannel(
            date=datetime(2026, 8, 18, tzinfo=UTC),
            chat=Chat(id=-100111, type="channel"),
            message_id=100,
        )
    )


async def test_channel_installation_requires_admin_and_uses_the_linked_discussion() -> None:
    destination = await _channel_discussion_from_forward(
        ChannelBot(),  # type: ignore[arg-type]
        _channel_forward(),  # type: ignore[arg-type]
        12,
    )

    assert destination.channel_id == -100111
    assert destination.discussion_chat_id == -100222


async def test_channel_installation_rejects_a_non_admin_requester() -> None:
    with pytest.raises(ChannelBindingError, match="only a channel administrator"):
        await _channel_discussion_from_forward(
            ChannelBot(requester_status="member"),  # type: ignore[arg-type]
            _channel_forward(),  # type: ignore[arg-type]
            12,
        )


def test_discussion_reply_must_reference_an_automatic_forward_in_the_linked_group(tmp_path) -> None:
    database = Database(tmp_path / "vikunjbot.sqlite3")
    database.initialize()
    database.save_task_message(
        -100111,
        42,
        100,
        datetime(2026, 8, 19, tzinfo=UTC),
        frozenset({12}),
        {},
        discussion_chat_id=-100222,
    )
    channel_origin = MessageOriginChannel(
        date=datetime(2026, 8, 18, tzinfo=UTC),
        chat=Chat(id=-100111, type="channel"),
        message_id=100,
    )
    automatic_forward = SimpleNamespace(
        message_id=700,
        is_automatic_forward=True,
        forward_origin=channel_origin,
    )
    linked_reply = SimpleNamespace(
        chat=SimpleNamespace(id=-100222),
        reply_to_message=automatic_forward,
    )
    unrelated_reply = SimpleNamespace(
        chat=SimpleNamespace(id=-100333),
        reply_to_message=automatic_forward,
    )
    manual_forward = SimpleNamespace(
        message_id=700,
        is_automatic_forward=False,
        forward_origin=channel_origin,
    )
    manually_forwarded_reply = SimpleNamespace(
        chat=SimpleNamespace(id=-100222),
        reply_to_message=manual_forward,
    )

    assert _task_message_for_reply(database, linked_reply) is not None  # type: ignore[arg-type]
    assert _task_message_for_reply(database, unrelated_reply) is None  # type: ignore[arg-type]
    assert _task_message_for_reply(database, manually_forwarded_reply) is None  # type: ignore[arg-type]


def test_existing_database_is_migrated_for_channel_discussions(tmp_path) -> None:
    path = tmp_path / "vikunjbot.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE task_messages (
                chat_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                allowed_telegram_user_ids_json TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, task_id)
            )
            """
        )

    Database(path).initialize()

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(task_messages)")}
    assert "discussion_chat_id" in columns
