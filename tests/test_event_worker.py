from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import ReactionTypeEmoji

from vikunjbot.database import Database, DeliveryDestination, HookView
from vikunjbot.event_worker import EventWorker
from vikunjbot.project_views import ProjectViewKind
from vikunjbot.settings import Settings
from vikunjbot.vikunja import VikunjaAPIError


@dataclass
class SentMessage:
    message_id: int


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.edited: list[tuple[int, int, str]] = []
        self.reactions: list[tuple[int, int, list[str]]] = []

    async def send_message(self, chat_id: int, text: str, **_: object) -> SentMessage:
        self.sent.append((chat_id, text))
        return SentMessage(message_id=100 + len(self.sent))

    async def edit_message_text(self, *, text: str, chat_id: int, message_id: int) -> None:
        self.edited.append((chat_id, message_id, text))

    async def set_message_reaction(
        self, *, chat_id: int, message_id: int, reaction: list[ReactionTypeEmoji]
    ) -> None:
        self.reactions.append((chat_id, message_id, [item.emoji for item in reaction]))


class ReactionRejectingBot(FakeBot):
    async def set_message_reaction(
        self, *, chat_id: int, message_id: int, reaction: list[ReactionTypeEmoji]
    ) -> None:
        raise TelegramBadRequest(
            method=SendMessage(chat_id=chat_id, text="test"),
            message="reaction is not available",
        )


async def test_task_updates_edit_the_original_telegram_message(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    first = event_payload()
    second = event_payload(event_name="task.updated", title="Write more tests")
    await database.enqueue_event(hook.id, json.dumps(first).encode(), first)
    await database.enqueue_event(hook.id, json.dumps(second).encode(), second)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]

    assert await worker.process_one() is True
    assert await worker.process_one() is True

    assert len(bot.sent) == 1
    assert bot.edited == [
        (12, 101, "<b>DEMO-42: Write more tests</b>\n⬜ Open\n📥 Bucket: Backlog")
    ]
    assert bot.reactions == []


async def test_done_state_is_reflected_by_the_bot_reaction(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    completed = event_payload()
    completed["data"]["task"]["done"] = True
    reopened = event_payload(event_name="task.updated")
    await database.enqueue_event(hook.id, json.dumps(completed).encode(), completed)
    await database.enqueue_event(hook.id, json.dumps(reopened).encode(), reopened)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]

    assert await worker.process_one() is True
    assert await worker.process_one() is True

    assert bot.reactions == [(12, 101, ["✅"]), (12, 101, [])]


async def test_reaction_rejection_does_not_retry_task_delivery(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    completed = event_payload()
    completed["data"]["task"]["done"] = True
    await database.enqueue_event(hook.id, json.dumps(completed).encode(), completed)
    bot = ReactionRejectingBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]

    assert await worker.process_one() is True
    assert bot.sent == [(12, "<b>DEMO-42: Write tests</b>\n✅ Completed\n📥 Bucket: Backlog")]
    assert await worker.process_one() is False


async def test_project_events_are_forwarded_without_a_task_mapping(
    config: Settings, database: Database
) -> None:
    hook = await database.create_hook(
        project_id=5,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    payload = {
        "event_name": "project.updated",
        "time": datetime.now(UTC).isoformat(),
        "data": {"project": {"id": 5, "title": "Roadmap"}},
    }
    await database.enqueue_event(hook.id, json.dumps(payload).encode(), payload)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]

    assert await worker.process_one() is True
    assert bot.sent == [(12, "📁 <b>Roadmap</b> updated.")]


async def test_view_filter_does_not_suppress_project_events(
    config: Settings, database: Database
) -> None:
    hook = await database.create_hook(
        project_id=5,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(HookView(7, "Completed", ProjectViewKind.TABLE),),
        filter_by_views=True,
    )
    payload = {
        "event_name": "project.updated",
        "time": datetime.now(UTC).isoformat(),
        "data": {"project": {"id": 5, "title": "Roadmap"}},
    }
    await database.enqueue_event(hook.id, json.dumps(payload).encode(), payload)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]

    assert await worker.process_one() is True
    assert bot.sent == [(12, "📁 <b>Roadmap</b> updated.")]


async def test_channel_route_publishes_in_the_channel_and_records_its_discussion(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=-100111, discussion_chat_id=-100222),
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    payload = event_payload()
    await database.enqueue_event(hook.id, json.dumps(payload).encode(), payload)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]

    assert await worker.process_one() is True
    assert bot.sent[0][0] == -100111
    task_message = await database.get_task_message(hook.id, 42)
    assert task_message is not None
    assert task_message.delivery_destination.discussion_chat_id == -100222


async def test_bucket_move_edits_the_persistent_task_message(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    created = event_payload()
    moved = event_payload(event_name="task.updated")
    moved["data"]["task"]["bucket"] = {"title": "Ready for review"}
    await database.enqueue_event(hook.id, json.dumps(created).encode(), created)
    await database.enqueue_event(hook.id, json.dumps(moved).encode(), moved)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]

    assert await worker.process_one() is True
    assert await worker.process_one() is True

    assert bot.edited == [
        (12, 101, "<b>DEMO-42: Write tests</b>\n⬜ Open\n📥 Bucket: Ready for review")
    ]


async def test_task_deletion_revokes_replies_and_cannot_be_undone_by_a_late_update(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    created = event_payload()
    created["data"]["task"]["done"] = True
    deleted = event_payload(event_name="task.deleted")
    stale_update = event_payload(event_name="task.updated", title="Should not reappear")
    await database.enqueue_event(hook.id, json.dumps(created).encode(), created)
    await database.enqueue_event(hook.id, json.dumps(deleted).encode(), deleted)
    await database.enqueue_event(hook.id, json.dumps(stale_update).encode(), stale_update)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]

    assert await worker.process_one() is True
    assert await worker.process_one() is True
    assert await worker.process_one() is True

    assert bot.edited == [(12, 101, "<b>DEMO-42: Write tests</b>\n🗑 Deleted")]
    assert bot.reactions == [(12, 101, ["✅"]), (12, 101, [])]
    task_message = await database.get_task_message(hook.id, 42)
    assert task_message is not None
    assert task_message.deleted is True
    assert await database.find_task_message_in_delivery_destination(12, 101) is None


async def test_untracked_task_deletion_is_a_notification_not_an_actionable_message(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    deleted = event_payload(event_name="task.deleted")
    await database.enqueue_event(hook.id, json.dumps(deleted).encode(), deleted)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]

    assert await worker.process_one() is True
    assert bot.sent == [(12, "<b>DEMO-42: Write tests</b>\n🗑 Deleted")]
    assert await database.find_task_message_in_delivery_destination(12, 101) is None


class ViewMembershipClient:
    def __init__(self, visibility: list[bool]) -> None:
        self.visibility = visibility

    async def task_in_project_view(
        self, project_id: int, view_id: int, task_id: int
    ) -> dict[str, Any] | None:
        assert (project_id, view_id, task_id) == (1, 7, 42)
        visible = self.visibility.pop(0)
        if not visible:
            return None
        return {
            "id": 42,
            "title": "Visible task",
            "identifier": "DEMO-42",
            "done": False,
            "labels": [],
            "assignees": [],
        }


async def test_view_filter_revokes_and_restores_the_same_task_message(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(HookView(7, "Completed table", ProjectViewKind.TABLE),),
        filter_by_views=True,
    )
    for index, event_name in enumerate(("task.created", "task.updated", "task.updated")):
        payload = event_payload(event_name=event_name, title=f"Event version {index}")
        await database.enqueue_event(hook.id, json.dumps(payload).encode(), payload)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]
    worker._enricher._client = ViewMembershipClient([True, False, True])  # type: ignore[assignment]

    assert await worker.process_one() is True
    assert bot.sent == [(12, "<b>DEMO-42: Visible task</b>\n⬜ Open")]
    assert await database.find_task_message_in_delivery_destination(12, 101) is not None

    assert await worker.process_one() is True
    assert bot.edited[-1] == (
        12,
        101,
        "<s><b>DEMO-42: Visible task</b>\n⬜ Open</s>\n"
        "<i>This task is no longer visible in the selected delivery views.</i>",
    )
    filtered = await database.get_task_message(hook.id, 42)
    assert filtered is not None and filtered.filtered_out is True
    assert await database.find_task_message_in_delivery_destination(12, 101) is None

    assert await worker.process_one() is True
    assert bot.edited[-1] == (12, 101, "<b>DEMO-42: Visible task</b>\n⬜ Open")
    restored = await database.get_task_message(hook.id, 42)
    assert restored is not None and restored.filtered_out is False
    assert len(bot.sent) == 1


class FailingViewMembershipClient:
    async def task_in_project_view(
        self, project_id: int, view_id: int, task_id: int
    ) -> dict[str, Any] | None:
        raise VikunjaAPIError(503, "Vikunja is unavailable")


class PartiallyAvailableViewClient:
    async def task_in_project_view(
        self, project_id: int, view_id: int, task_id: int
    ) -> dict[str, Any] | None:
        if view_id == 7:
            raise VikunjaAPIError(503, "one view is unavailable")
        return {
            "id": task_id,
            "title": "Matched elsewhere",
            "identifier": "DEMO-42",
            "done": False,
            "labels": [],
            "assignees": [],
        }


async def test_view_filter_api_failure_retries_instead_of_dropping(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(HookView(7, "Important", ProjectViewKind.LIST),),
        filter_by_views=True,
    )
    payload = event_payload()
    await database.enqueue_event(hook.id, json.dumps(payload).encode(), payload)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]
    worker._enricher._client = FailingViewMembershipClient()  # type: ignore[assignment]

    assert await worker.process_one() is True
    assert bot.sent == []
    assert await worker.process_one() is False


async def test_one_confirmed_view_match_is_enough_when_another_view_fails(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(
            HookView(7, "Unavailable", ProjectViewKind.LIST),
            HookView(8, "Matching", ProjectViewKind.GANTT),
        ),
        filter_by_views=True,
    )
    payload = event_payload()
    await database.enqueue_event(hook.id, json.dumps(payload).encode(), payload)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]
    worker._enricher._client = PartiallyAvailableViewClient()  # type: ignore[assignment]

    assert await worker.process_one() is True
    assert bot.sent == [(12, "<b>DEMO-42: Matched elsewhere</b>\n⬜ Open")]
