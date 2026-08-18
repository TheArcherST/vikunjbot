from __future__ import annotations

from dataclasses import dataclass

import pytest

from vikunjbot.database import Database
from vikunjbot.settings import Settings
from vikunjbot.tokens import (
    TelegramInteraction,
    TokenCipher,
    TokenIdentityChangedError,
    TokenService,
)


@dataclass
class FakeClient:
    calls: list[str]
    identity: dict[str, object]

    async def whoami(self) -> dict[str, object]:
        self.calls.append("whoami")
        return self.identity


def _factory(calls: list[str], identity: dict[str, object]):
    def create(_: str, __: str) -> FakeClient:
        return FakeClient(calls, identity)

    return create


async def test_telegram_interaction_checks_a_bound_token_once(
    config: Settings, database: Database
) -> None:
    calls: list[str] = []
    service = TokenService(
        database,
        TokenCipher(config.token_encryption_key),
        config.vikunja_api_base_url,
        _factory(calls, {"id": 9, "username": "lena"}),  # type: ignore[arg-type]
    )

    await service.bind_direct_token(123, "secret-token")
    interaction = TelegramInteraction(123)
    await service.client_for_telegram_action(interaction)
    await service.client_for_telegram_action(interaction)

    assert calls == ["whoami", "whoami"]
    binding = await database.get_token_binding(123)
    assert binding is not None
    assert b"secret-token" not in binding.encrypted_token


async def test_bound_token_cannot_silently_switch_vikunja_accounts(
    config: Settings, database: Database
) -> None:
    calls: list[str] = []
    good_factory = _factory(calls, {"id": 9, "username": "lena"})
    service = TokenService(
        database,
        TokenCipher(config.token_encryption_key),
        config.vikunja_api_base_url,
        good_factory,  # type: ignore[arg-type]
    )
    await service.bind_direct_token(123, "secret-token")
    service._client_factory = _factory(calls, {"id": 10, "username": "other"})  # type: ignore[assignment]

    with pytest.raises(TokenIdentityChangedError):
        await service.client_for_telegram_action(TelegramInteraction(123))
