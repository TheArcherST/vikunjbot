from __future__ import annotations

from uuid import UUID


class InvalidRouteTag(ValueError):
    """A webhook route tag is not the canonical UUID issued by the bot."""


def parse_hook_id(tag: str) -> UUID:
    """Accept only canonical UUIDs, never an arbitrary delivery instruction."""

    try:
        hook_id = UUID(tag)
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidRouteTag("hook tag must be a UUID") from exc
    if str(hook_id) != tag.lower():
        raise InvalidRouteTag("hook tag must use the canonical UUID form")
    return hook_id
