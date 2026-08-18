from __future__ import annotations

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

    async def get_chat(self, *, chat_id: int) -> SimpleNamespace:
        assert chat_id == -100111
        return SimpleNamespace(id=-100111, type="channel", linked_chat_id=-100222)

    async def get_me(self) -> SimpleNamespace:
        return SimpleNamespace(id=999)

    async def get_chat_member(self, *, chat_id: int, user_id: int) -> SimpleNamespace:
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


async def test_discussion_reply_must_reference_an_automatic_forward_in_the_linked_group(
    database: Database,
) -> None:
    hook = await database.create_hook(
        project_id=1,
        chat_id=-100111,
        discussion_chat_id=-100222,
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    await database.save_task_message(
        hook_id=hook.id,
        chat_id=-100111,
        task_id=42,
        message_id=100,
        expires_at=datetime(2026, 8, 19, tzinfo=UTC),
        allowed_telegram_user_ids=frozenset({12}),
        snapshot={},
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

    assert await _task_message_for_reply(database, linked_reply) is not None  # type: ignore[arg-type]
    assert await _task_message_for_reply(database, unrelated_reply) is None  # type: ignore[arg-type]
    assert await _task_message_for_reply(database, manually_forwarded_reply) is None  # type: ignore[arg-type]
