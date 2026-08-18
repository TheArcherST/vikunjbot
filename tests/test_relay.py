from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from vikunjbot.database import Database
from vikunjbot.relay import create_app
from vikunjbot.settings import Settings


async def test_relay_commits_event_before_acknowledging(
    config: Settings, event_payload: Callable[..., dict[str, Any]]
) -> None:
    payload = event_payload()
    raw_body = json.dumps(payload).encode()
    Database(config.app_db_path).initialize()
    app = create_app(config)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        first = await client.post("/events/telegram-id:12,expiry:1d", content=raw_body)
        duplicate = await client.post("/events/telegram-id:12,expiry:1d", content=raw_body)

    assert first.status_code == 202
    assert first.json()["accepted"] is True
    assert duplicate.status_code == 202
    assert duplicate.json() == {"id": first.json()["id"], "accepted": False}

    stored = Database(config.app_db_path).claim_next_event(60)
    assert stored is not None
    assert stored.payload == payload
    assert stored.route_tag == "telegram-id:12,expiry:1d"


async def test_relay_rejects_an_unrouted_webhook(
    config: Settings, event_payload: Callable[..., dict[str, Any]]
) -> None:
    payload = event_payload()
    Database(config.app_db_path).initialize()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(config)), base_url="http://testserver"
    ) as client:
        response = await client.post("/events", json=payload)

    assert response.status_code == 422


async def test_relay_rejects_an_unbounded_route(
    config: Settings, event_payload: Callable[..., dict[str, Any]]
) -> None:
    payload = event_payload()
    Database(config.app_db_path).initialize()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(config)), base_url="http://testserver"
    ) as client:
        response = await client.post("/events/telegram-id:12", json=payload)

    assert response.status_code == 422
