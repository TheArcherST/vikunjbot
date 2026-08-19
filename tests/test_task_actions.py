from __future__ import annotations

import pytest

from vikunjbot.task_actions import PartialTaskActionError, TaskActions, apply_task_actions
from vikunjbot.vikunja import VikunjaAPIError


class PartiallyFailingClient:
    async def task_labels(self, task_id: int) -> list[dict[str, object]]:
        return [{"id": 1, "title": "done"}]

    async def labels(self, search: str) -> list[dict[str, object]]:
        return [{"id": 1, "title": "done"}, {"id": 2, "title": "next"}]

    async def remove_task_label(self, task_id: int, label_id: int) -> dict[str, object]:
        return {}

    async def add_task_label(self, task_id: int, label_id: int) -> dict[str, object]:
        raise VikunjaAPIError(503, "Vikunja is unavailable")


async def test_task_actions_expose_confirmed_partial_completion() -> None:
    actions = TaskActions(labels=("done", "next"), assignees=(), comment="")

    with pytest.raises(PartialTaskActionError) as captured:
        await apply_task_actions(  # type: ignore[arg-type]
            PartiallyFailingClient(),
            10,
            actions,
        )

    assert captured.value.completed == ("removed label *done",)
    assert captured.value.status_code == 503
