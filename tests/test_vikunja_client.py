from __future__ import annotations

import httpx
import pytest

from vikunjbot.vikunja import VikunjaAPIError, VikunjaClient


async def test_client_keeps_the_api_version_prefix_in_request_urls() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/user"
        assert request.headers["authorization"] == "Bearer user-token"
        return httpx.Response(200, json={"id": 1, "username": "lena"})

    client = VikunjaClient(
        "http://vikunja:3456/api/v2",
        "user-token",
        transport=httpx.MockTransport(handler),
    )

    assert await client.whoami() == {"id": 1, "username": "lena"}


async def test_client_uses_v2_verbs_search_and_pagination() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"items": [{"id": 3, "title": "urgent"}]})
        return httpx.Response(201, json={"id": 3})

    client = VikunjaClient(
        "http://vikunja:3456/api/v2",
        "user-token",
        transport=httpx.MockTransport(handler),
    )

    assert await client.labels("urgent") == [{"id": 3, "title": "urgent"}]
    assert await client.find_users("alex") == [{"id": 3, "title": "urgent"}]
    assert await client.task_labels(9) == [{"id": 3, "title": "urgent"}]
    assert await client.task_assignees(9) == [{"id": 3, "title": "urgent"}]
    await client.create_label("urgent")
    await client.create_project_webhook(4, "http://relay/events/tag", ["task.created"])
    await client.add_task_label(9, 3)
    await client.add_task_assignee(9, 6)
    await client.add_task_comment(9, "hello")

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/v2/labels"),
        ("GET", "/api/v2/users"),
        ("GET", "/api/v2/tasks/9/labels"),
        ("GET", "/api/v2/tasks/9/assignees"),
        ("POST", "/api/v2/labels"),
        ("POST", "/api/v2/projects/4/webhooks"),
        ("POST", "/api/v2/tasks/9/labels"),
        ("POST", "/api/v2/tasks/9/assignees"),
        ("POST", "/api/v2/tasks/9/comments"),
    ]
    assert requests[0].url.params == httpx.QueryParams({"q": "urgent", "per_page": "50"})
    assert requests[1].url.params == httpx.QueryParams({"q": "alex", "per_page": "50"})


async def test_client_lists_and_deletes_project_webhooks() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"items": [{"id": 8, "target_url": "http://relay/events/hook"}]},
            )
        return httpx.Response(200, json={"message": "deleted"})

    client = VikunjaClient(
        "http://vikunja:3456/api/v2",
        "user-token",
        transport=httpx.MockTransport(handler),
    )

    assert await client.project_webhooks(4) == [
        {"id": 8, "target_url": "http://relay/events/hook"}
    ]
    await client.delete_project_webhook(4, 8)

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/v2/projects/4/webhooks"),
        ("DELETE", "/api/v2/projects/4/webhooks/8"),
    ]
    assert requests[0].url.params == httpx.QueryParams({"per_page": "100"})


async def test_client_checks_task_membership_in_flat_and_bucketed_views() -> None:
    responses = [
        {"items": [{"id": 42, "identifier": "DEMO-42", "done": False}]},
        {
            "items": [
                {
                    "id": 7,
                    "title": "Backlog",
                    "tasks": [{"id": 42, "identifier": "DEMO-42", "done": False}],
                }
            ]
        },
        {"items": []},
    ]
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=responses.pop(0))

    client = VikunjaClient(
        "http://vikunja:3456/api/v2",
        "service-token",
        transport=httpx.MockTransport(handler),
    )

    assert await client.task_in_project_view(4, 10, 42) is not None
    assert await client.task_in_project_view(4, 11, 42) is not None
    assert await client.task_in_project_view(4, 12, 42) is None
    assert all(request.url.params["filter"] == "id = 42" for request in requests)
    assert [request.url.path for request in requests] == [
        "/api/v2/projects/4/views/10/tasks",
        "/api/v2/projects/4/views/11/tasks",
        "/api/v2/projects/4/views/12/tasks",
    ]


async def test_malformed_view_response_is_not_treated_as_confirmed_absence() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": []})

    client = VikunjaClient(
        "http://vikunja:3456/api/v2",
        "service-token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(VikunjaAPIError) as captured:
        await client.task_in_project_view(4, 10, 42)

    assert captured.value.status_code == 502
