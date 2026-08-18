from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from vikunjbot.database import Database, DeliveryDestination, HookView


async def test_hook_configuration_persists_its_selected_kanban_views(database: Database) -> None:
    hook = await database.create_hook(
        project_id=17,
        delivery_destination=DeliveryDestination(chat_id=-100123, discussion_chat_id=-100456),
        allowed_telegram_user_ids=frozenset({5, 9}),
        views=(HookView(3, "Development"), HookView(8, "Release")),
    )

    stored = await database.get_active_hook(hook.id)

    assert stored == hook


async def test_event_queue_deduplicates_raw_payloads_and_claims_once(database: Database) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    payload = {
        "event_name": "task.updated",
        "time": "2026-08-18T12:00:00+00:00",
        "data": {"task": {"id": 42, "title": "Queue me"}},
    }
    raw_body = json.dumps(payload, sort_keys=True).encode()

    event_id, accepted = await database.enqueue_event(hook.id, raw_body, payload)
    duplicate_id, duplicate_accepted = await database.enqueue_event(hook.id, raw_body, payload)

    assert accepted is True
    assert (duplicate_id, duplicate_accepted) == (event_id, False)
    claimed = await database.claim_next_event(60)
    assert claimed is not None
    assert claimed.id == event_id
    assert claimed.hook_id == hook.id
    assert claimed.payload == payload
    await database.complete_event(claimed.id)
    assert await database.claim_next_event(60) is None


async def test_task_message_mapping_is_scoped_to_each_hook(database: Database) -> None:
    first = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    second = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=24),
        allowed_telegram_user_ids=frozenset({24}),
        views=(),
    )
    expires_at = datetime(2026, 8, 19, tzinfo=UTC)

    await database.save_task_message(
        hook_id=first.id,
        delivery_destination=DeliveryDestination(chat_id=12),
        task_id=42,
        message_id=100,
        expires_at=expires_at,
        allowed_telegram_user_ids=frozenset({12}),
        snapshot={"title": "Original"},
    )
    await database.save_task_message(
        hook_id=first.id,
        delivery_destination=DeliveryDestination(chat_id=12),
        task_id=42,
        message_id=101,
        expires_at=expires_at + timedelta(hours=1),
        allowed_telegram_user_ids=frozenset({12}),
        snapshot={"title": "Edited"},
    )
    await database.save_task_message(
        hook_id=second.id,
        delivery_destination=DeliveryDestination(chat_id=24),
        task_id=42,
        message_id=200,
        expires_at=expires_at,
        allowed_telegram_user_ids=frozenset({24}),
        snapshot={"title": "Other destination"},
    )

    first_mapping = await database.get_task_message(first.id, 42)
    second_mapping = await database.get_task_message(second.id, 42)
    assert first_mapping is not None
    assert second_mapping is not None
    assert first_mapping.message_id == 101
    assert second_mapping.message_id == 200
