from __future__ import annotations

import pytest
from pydantic import ValidationError

from vikunjbot.bot import create_telegram_bot
from vikunjbot.settings import Settings


def test_telegram_proxy_url_is_normalized() -> None:
    config = Settings(telegram_proxy_url="  socks5://user:password@proxy.example:1080  ")

    assert config.telegram_proxy_url == "socks5://user:password@proxy.example:1080"


@pytest.mark.parametrize(
    "proxy_url",
    ["proxy.example:1080", "https://proxy.example:443", "socks5://proxy.example"],
)
def test_telegram_proxy_url_rejects_unsupported_or_incomplete_addresses(proxy_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(telegram_proxy_url=proxy_url)


async def test_telegram_bot_uses_optional_proxy() -> None:
    bot = create_telegram_bot(
        Settings(
            telegram_bot_token="123456:testing-token",
            telegram_proxy_url="http://127.0.0.1:11809",
        )
    )
    try:
        assert bot.session.proxy == "http://127.0.0.1:11809"
    finally:
        await bot.session.close()
