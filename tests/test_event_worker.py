from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from vikunjbot.database import Database
from vikunjbot.event_worker import EventWorker
from vikunjbot.settings import Settings


@dataclass
class SentMessage:
    message_id: int


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.edited: list[tuple[int, int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_: object) -> SentMessage:
        self.sent.append((chat_id, text))
        return SentMessage(message_id=100 + len(self.sent))

    async def edit_message_text(self, text: str, chat_id: int, message_id: int) -> None:
        self.edited.append((chat_id, message_id, text))


async def test_task_updates_edit_the_original_telegram_message(
    config: Settings, event_payload: Callable[..., dict[str, Any]]
) -> None:
    database = Database(config.app_db_path)
    database.initialize()
    first = event_payload()
    second = event_payload(event_name="task.updated", title="Write more tests")
    database.enqueue_event("telegram-id:12,expiry:1d", json.dumps(first).encode(), first)
    database.enqueue_event("telegram-id:12,expiry:1d", json.dumps(second).encode(), second)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]

    assert await worker.process_one() is True
    assert await worker.process_one() is True

    assert len(bot.sent) == 1
    assert bot.edited == [
        (12, 101, "<b>DEMO-42: Write more tests</b>\n⬜ Open\n📥 Bucket: Backlog")
    ]


async def test_project_events_are_forwarded_without_a_task_mapping(config: Settings) -> None:
    database = Database(config.app_db_path)
    database.initialize()
    payload = {
        "event_name": "project.updated",
        "time": "2026-08-18T12:00:00+00:00",
        "data": {"project": {"id": 5, "title": "Roadmap"}},
    }
    database.enqueue_event("telegram-id:12,expiry:1d", json.dumps(payload).encode(), payload)
    bot = FakeBot()
    worker = EventWorker(bot, database, config)  # type: ignore[arg-type]

    assert await worker.process_one() is True
    assert bot.sent == [(12, "📁 <b>Roadmap</b> updated.")]
