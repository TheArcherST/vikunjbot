from __future__ import annotations

from aiogram.enums import ChatType
from aiogram.types import Chat

from vikunjbot.bot import (
    _INSTALL_WEBHOOK_COMMAND,
    _LOGIN_COMMAND,
    _command_syntax,
    _delete_matching_project_webhooks,
    _is_private_chat,
)


class FakeWebhookClient:
    def __init__(self) -> None:
        self.deleted: list[tuple[int, int]] = []

    async def project_webhooks(self, project_id: int) -> list[dict[str, object]]:
        assert project_id == 17
        return [
            {"id": 8, "target_url": "http://relay/events/owned"},
            {"id": 9, "target_url": "http://relay/events/owned/"},
            {"id": 10, "target_url": "http://relay/events/other"},
            {"id": "invalid", "target_url": "http://relay/events/owned"},
        ]

    async def delete_project_webhook(self, project_id: int, webhook_id: int) -> None:
        self.deleted.append((project_id, webhook_id))


def test_command_syntax_escapes_angle_brackets_for_html_parse_mode() -> None:
    assert _command_syntax("/login <API token>") == "<code>/login &lt;API token&gt;</code>"


def test_help_command_syntax_uses_html_safe_literals() -> None:
    assert _LOGIN_COMMAND == "<code>/login &lt;API token&gt;</code>"
    assert _INSTALL_WEBHOOK_COMMAND == (
        "<code>/install_webhook &lt;project-id&gt; [kanban-view-ids]</code>"
    )


def test_private_chat_check_uses_value_equality() -> None:
    chat = Chat(id=1, type="private")

    assert chat.type is not ChatType.PRIVATE
    assert _is_private_chat(chat.type)


async def test_hook_cleanup_removes_only_matching_vikunja_webhooks() -> None:
    client = FakeWebhookClient()

    removed = await _delete_matching_project_webhooks(  # type: ignore[arg-type]
        client,
        17,
        "http://relay/events/owned",
    )

    assert removed == 2
    assert client.deleted == [(17, 8), (17, 9)]
