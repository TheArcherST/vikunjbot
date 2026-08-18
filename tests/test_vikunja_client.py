from __future__ import annotations

import httpx

from vikunjbot.vikunja import VikunjaClient


async def test_client_keeps_the_api_version_prefix_in_request_urls() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/user"
        assert request.headers["authorization"] == "Bearer user-token"
        return httpx.Response(200, json={"id": 1, "username": "lena"})

    client = VikunjaClient(
        "http://vikunja:3456/api/v1",
        "user-token",
        transport=httpx.MockTransport(handler),
    )

    assert await client.whoami() == {"id": 1, "username": "lena"}
