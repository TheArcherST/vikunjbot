from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from vikunjbot.settings import Settings


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
def config(tmp_path: Path) -> Settings:
    return Settings(
        app_db_path=tmp_path / "vikunjbot.sqlite3",
        token_encryption_key="FoaLNNRoapMeitZ4gc8xB3KMLfd9eHKvD2KpwgCOhHg=",
        telegram_bot_token="123456:token",
        worker_poll_seconds=0,
    )
