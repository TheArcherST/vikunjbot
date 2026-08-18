from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vikunjbot.routing import InvalidRouteTag, make_route_tag, parse_route_tag


def test_direct_route_grants_the_recipient_access() -> None:
    event_time = datetime(2026, 8, 18, 12, tzinfo=UTC)

    routes = parse_route_tag("*/telegram-id:12345,expiry:12h/*", event_time)

    assert len(routes) == 1
    assert routes[0].chat_id == 12345
    assert routes[0].allowed_telegram_user_ids == frozenset({12345})
    assert routes[0].expires_at == event_time + timedelta(hours=12)


def test_group_route_does_not_duplicate_to_the_actor_private_chat() -> None:
    event_time = datetime(2026, 8, 18, 12, tzinfo=UTC)

    routes = parse_route_tag("telegram-id:12345,telegram-chat-id:-100987,expiry:1d", event_time)

    assert len(routes) == 1
    assert routes[0].chat_id == -100987
    assert routes[0].allowed_telegram_user_ids == frozenset({12345})


def test_route_requires_a_bounded_expiry() -> None:
    with pytest.raises(InvalidRouteTag, match="expiry"):
        parse_route_tag("telegram-id:12345", datetime(2026, 8, 18, tzinfo=UTC))


def test_canonical_tag_can_target_a_group() -> None:
    assert make_route_tag(12345, "30m", -100987) == (
        "telegram-id:12345,telegram-chat-id:-100987,expiry:30m"
    )
