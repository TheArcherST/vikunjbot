from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import httpx

from vikunjbot.database import Database, DeliveryDestination
from vikunjbot.relay import create_app
from vikunjbot.settings import Settings


async def test_relay_commits_event_before_acknowledging(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    hook = await database.create_hook(
        project_id=1,
        delivery_destination=DeliveryDestination(chat_id=12),
        allowed_telegram_user_ids=frozenset({12}),
        views=(),
    )
    payload = event_payload()
    raw_body = json.dumps(payload).encode()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(config, database)),
        base_url="http://testserver",
    ) as client:
        first = await client.post(f"/events/{hook.id}", content=raw_body)
        duplicate = await client.post(f"/events/{hook.id}", content=raw_body)

    assert first.status_code == 202
    assert first.json()["accepted"] is True
    assert duplicate.status_code == 202
    assert duplicate.json() == {"id": first.json()["id"], "accepted": False}

    stored = await database.claim_next_event(60)
    assert stored is not None
    assert stored.payload == payload
    assert stored.hook_id == hook.id


async def test_relay_rejects_an_unknown_webhook(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(config, database)),
        base_url="http://testserver",
    ) as client:
        response = await client.post(f"/events/{uuid4()}", json=event_payload())

    assert response.status_code == 404


async def test_relay_rejects_a_non_uuid_tag(
    config: Settings, database: Database, event_payload: Callable[..., dict[str, Any]]
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(config, database)),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/events/telegram-id:12", json=event_payload())

    assert response.status_code == 422
