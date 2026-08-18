from __future__ import annotations

from uuid import uuid4

import pytest

from vikunjbot.routing import InvalidRouteTag, parse_hook_id


def test_canonical_uuid_is_a_valid_hook_tag() -> None:
    hook_id = uuid4()

    assert parse_hook_id(str(hook_id)) == hook_id


@pytest.mark.parametrize(
    "value",
    [
        "",
        "telegram-id:12345,expiry:1d",
        "4f00f7b4-a5f8-4e10-8c24-3d5bc2f52d21/extra",
        "{4f00f7b4-a5f8-4e10-8c24-3d5bc2f52d21}",
    ],
)
def test_hook_tag_must_be_a_canonical_uuid(value: str) -> None:
    with pytest.raises(InvalidRouteTag, match="UUID"):
        parse_hook_id(value)
