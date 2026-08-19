from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from aiogram.enums import ChatType
from aiogram.types import Chat

from vikunjbot.bot import (
    _INSTALL_WEBHOOK_COMMAND,
    _LOGIN_COMMAND,
    HookConfigurationError,
    _command_syntax,
    _delete_matching_project_webhooks,
    _delete_owned_hook_everywhere,
    _install_project_webhook,
    _is_private_chat,
)
from vikunjbot.database import DeliveryDestination
from vikunjbot.vikunja import VikunjaAPIError


class FakeWebhookClient:
    def __init__(self) -> None:
        self.deleted: list[tuple[int, int]] = []

    async def project_webhooks(self, project_id: int) -> list[dict[str, object]]:
        assert project_id == 17
        return (
            [
                {"id": 8, "target_url": "http://relay/events/owned"},
                {"id": 9, "target_url": "http://relay/events/owned/"},
                {"id": 10, "target_url": "http://relay/events/other"},
                {"id": "invalid", "target_url": "http://relay/events/owned"},
            ]
            if not self.deleted
            else [
                {"id": 10, "target_url": "http://relay/events/other"},
            ]
        )

    async def delete_project_webhook(self, project_id: int, webhook_id: int) -> None:
        self.deleted.append((project_id, webhook_id))


def test_command_syntax_escapes_angle_brackets_for_html_parse_mode() -> None:
    assert _command_syntax("/login <API token>") == "<code>/login &lt;API token&gt;</code>"


def test_help_command_syntax_uses_html_safe_literals() -> None:
    assert _LOGIN_COMMAND == "<code>/login &lt;API token&gt;</code>"
    assert _INSTALL_WEBHOOK_COMMAND == (
        "<code>/install_webhook &lt;project-id&gt; [view-ids]</code>"
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


class FailingWebhookClient(FakeWebhookClient):
    async def project_webhooks(self, project_id: int) -> list[dict[str, object]]:
        raise VikunjaAPIError(503, "Vikunja is unavailable")


class FakeHookDatabase:
    def __init__(self) -> None:
        self.hook_id = uuid4()
        self.deleted: list[tuple[UUID, int]] = []

    async def create_hook(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(id=self.hook_id)

    async def delete_owned_hook(self, hook_id: UUID, owner_id: int) -> bool:
        self.deleted.append((hook_id, owner_id))
        return True


async def test_remote_cleanup_failure_does_not_delete_local_hook() -> None:
    database = FakeHookDatabase()

    with pytest.raises(VikunjaAPIError):
        await _delete_owned_hook_everywhere(  # type: ignore[arg-type]
            database=database,
            client=FailingWebhookClient(),
            project_id=17,
            target_url="http://relay/events/owned",
            hook_id=database.hook_id,
            owner_telegram_user_id=42,
        )

    assert database.deleted == []


class AmbiguousInstallClient:
    async def create_project_webhook(
        self, project_id: int, target_url: str, events: list[str]
    ) -> dict[str, object]:
        raise VikunjaAPIError(503, "Vikunja is unavailable")

    async def project_webhooks(self, project_id: int) -> list[dict[str, object]]:
        raise VikunjaAPIError(503, "Vikunja is unavailable")


async def test_ambiguous_install_keeps_local_route_for_safe_reconciliation() -> None:
    database = FakeHookDatabase()
    config = SimpleNamespace(
        relay_webhook_url="http://relay/events",
        vikunjbot_service_token=None,
    )

    with pytest.raises(HookConfigurationError, match="did not confirm"):
        await _install_project_webhook(  # type: ignore[arg-type]
            database=database,
            config=config,
            client=AmbiguousInstallClient(),
            project_id=17,
            telegram_user_id=42,
            delivery_destination=DeliveryDestination(chat_id=42),
            view_ids=(),
        )

    assert database.deleted == []


class RejectedInstallClient(AmbiguousInstallClient):
    async def project_webhooks(self, project_id: int) -> list[dict[str, object]]:
        return []


async def test_confirmed_failed_install_rolls_back_local_route() -> None:
    database = FakeHookDatabase()
    config = SimpleNamespace(
        relay_webhook_url="http://relay/events",
        vikunjbot_service_token=None,
    )

    with pytest.raises(VikunjaAPIError):
        await _install_project_webhook(  # type: ignore[arg-type]
            database=database,
            config=config,
            client=RejectedInstallClient(),
            project_id=17,
            telegram_user_id=42,
            delivery_destination=DeliveryDestination(chat_id=42),
            view_ids=(),
        )

    assert database.deleted == [(database.hook_id, 42)]
