from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

_DIRECTIVE_RE = re.compile(
    r"(?:^|[\\s,/*])(?P<name>telegram-id|telegram-chat-id|telegram-channel-id|"
    r"telegram-discussion-chat-id|expiry):(?P<value>[^,/*\\s]+)"
)
_DURATION_RE = re.compile(r"(?P<amount>[1-9][0-9]*)(?P<unit>[smhdw])$")


class InvalidRouteTag(ValueError):
    """A route tag cannot safely grant Telegram access."""


@dataclass(frozen=True, slots=True)
class DeliveryRoute:
    chat_id: int
    allowed_telegram_user_ids: frozenset[int]
    expires_at: datetime
    discussion_chat_id: int | None = None


def _parse_duration(value: str) -> timedelta:
    matched = _DURATION_RE.fullmatch(value)
    if not matched:
        raise InvalidRouteTag("expiry must be a positive value such as 1d, 12h or 30m")
    amount = int(matched["amount"])
    multiplier = {
        "s": 1,
        "m": 60,
        "h": 60 * 60,
        "d": 24 * 60 * 60,
        "w": 7 * 24 * 60 * 60,
    }[matched["unit"]]
    return timedelta(seconds=amount * multiplier)


def parse_route_tag(tag: str, event_time: datetime) -> tuple[DeliveryRoute, ...]:
    """Parse the explicit route encoded in a webhook path.

    `telegram-id` is both a direct-message recipient and the allowed actor for that
    delivery. A channel/group route can use `telegram-chat-id`; a linked channel and
    discussion use `telegram-channel-id` and `telegram-discussion-chat-id`. Adding
    one or more `telegram-id` directives restricts who may act through its task
    message. The expiry is compulsory: an unbounded tag must never become a standing
    grant.
    """

    values: dict[str, list[str]] = {
        "telegram-id": [],
        "telegram-chat-id": [],
        "telegram-channel-id": [],
        "telegram-discussion-chat-id": [],
        "expiry": [],
    }
    for matched in _DIRECTIVE_RE.finditer(tag):
        values[matched["name"]].append(matched["value"])

    if len(values["expiry"]) != 1:
        raise InvalidRouteTag("exactly one expiry directive is required")
    try:
        expires_at = event_time + _parse_duration(values["expiry"][0])
    except OverflowError as exc:
        raise InvalidRouteTag("expiry is outside the supported range") from exc
    try:
        direct_ids = frozenset(int(value) for value in values["telegram-id"])
        chat_ids = frozenset(int(value) for value in values["telegram-chat-id"])
        channel_ids = frozenset(int(value) for value in values["telegram-channel-id"])
        discussion_chat_ids = frozenset(
            int(value) for value in values["telegram-discussion-chat-id"]
        )
    except ValueError as exc:
        raise InvalidRouteTag("Telegram ids must be integers") from exc

    if any(identifier <= 0 for identifier in direct_ids):
        raise InvalidRouteTag("telegram-id must be a positive user id")
    if any(identifier == 0 for identifier in chat_ids):
        raise InvalidRouteTag("telegram-chat-id must not be zero")
    if any(identifier == 0 for identifier in channel_ids | discussion_chat_ids):
        raise InvalidRouteTag("Telegram chat ids must not be zero")
    if channel_ids:
        if chat_ids or len(channel_ids) != 1 or len(discussion_chat_ids) != 1:
            raise InvalidRouteTag(
                "a channel route requires exactly one channel and linked discussion chat"
            )
        return (
            DeliveryRoute(
                chat_id=next(iter(channel_ids)),
                allowed_telegram_user_ids=direct_ids,
                expires_at=expires_at,
                discussion_chat_id=next(iter(discussion_chat_ids)),
            ),
        )
    if discussion_chat_ids:
        raise InvalidRouteTag("telegram-discussion-chat-id requires telegram-channel-id")
    if not direct_ids and not chat_ids:
        raise InvalidRouteTag("at least one telegram-id, telegram-chat-id, or channel is required")

    # When a chat is explicitly named, `telegram-id` grants actor access within
    # that chat instead of producing an unexpected duplicate private message.
    direct_routes = [
        DeliveryRoute(
            chat_id=identifier,
            allowed_telegram_user_ids=frozenset({identifier}),
            expires_at=expires_at,
        )
        for identifier in direct_ids
    ]
    chat_routes = [
        DeliveryRoute(
            chat_id=identifier,
            allowed_telegram_user_ids=direct_ids,
            expires_at=expires_at,
        )
        for identifier in chat_ids
    ]
    return tuple(chat_routes or direct_routes)


def make_route_tag(
    telegram_user_id: int,
    expiry: str = "1d",
    chat_id: int | None = None,
    *,
    channel_id: int | None = None,
    discussion_chat_id: int | None = None,
) -> str:
    """Create the canonical, copyable stream tag presented by the bot."""

    if telegram_user_id <= 0:
        raise InvalidRouteTag("telegram-id must be a positive user id")
    try:
        _parse_duration(expiry)
    except OverflowError as exc:
        raise InvalidRouteTag("expiry is outside the supported range") from exc
    if chat_id is not None and channel_id is not None:
        raise InvalidRouteTag("a route cannot target both a chat and a channel")
    if (channel_id is None) != (discussion_chat_id is None):
        raise InvalidRouteTag("a channel route requires its linked discussion chat")

    parts = [f"telegram-id:{telegram_user_id}"]
    if chat_id is not None:
        if chat_id == 0:
            raise InvalidRouteTag("Telegram chat id must not be zero")
        parts.append(f"telegram-chat-id:{chat_id}")
    if channel_id is not None and discussion_chat_id is not None:
        if channel_id == 0 or discussion_chat_id == 0:
            raise InvalidRouteTag("Telegram chat ids must not be zero")
        parts.extend(
            [
                f"telegram-channel-id:{channel_id}",
                f"telegram-discussion-chat-id:{discussion_chat_id}",
            ]
        )
    parts.append(f"expiry:{expiry}")
    return ",".join(parts)
