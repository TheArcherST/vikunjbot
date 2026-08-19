from __future__ import annotations

from typing import Any

import httpx


class VikunjaAPIError(RuntimeError):
    """A Vikunja API request failed without exposing credentials to callers."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"Vikunja API returned {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class VikunjaClient:
    """Minimal typed facade for the Vikunja endpoints used by the bot."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float = 15,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def whoami(self) -> dict[str, Any]:
        return await self._request("GET", "/user")

    async def get_task(self, task_id: int, *, expand_buckets: bool = False) -> dict[str, Any]:
        params = {"expand": "buckets"} if expand_buckets else None
        return await self._request("GET", f"/tasks/{task_id}", params=params)

    async def create_project_webhook(
        self, project_id: int, target_url: str, events: list[str]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/projects/{project_id}/webhooks",
            json={"target_url": target_url, "events": events},
        )

    async def project_webhooks(self, project_id: int) -> list[dict[str, Any]]:
        result = await self._request(
            "GET", f"/projects/{project_id}/webhooks", params={"per_page": 100}
        )
        return _as_object_list_or_list(result, "project webhooks")

    async def delete_project_webhook(self, project_id: int, webhook_id: int) -> None:
        await self._request("DELETE", f"/projects/{project_id}/webhooks/{webhook_id}")

    async def project_views(self, project_id: int) -> list[dict[str, Any]]:
        result = await self._request(
            "GET", f"/projects/{project_id}/views", params={"per_page": 100}
        )
        return _as_object_list_or_list(result, "project views")

    async def task_in_project_view(
        self, project_id: int, view_id: int, task_id: int
    ) -> dict[str, Any] | None:
        """Return the task only when the selected view's own filters include it."""

        result = await self._request(
            "GET",
            f"/projects/{project_id}/views/{view_id}/tasks",
            params={
                "filter": f"id = {task_id}",
                "per_page": 100,
                "expand": "buckets",
            },
        )
        if not isinstance(result, list) and not isinstance(result.get("items"), list):
            raise VikunjaAPIError(502, "Vikunja returned invalid project view tasks")
        return _find_task_in_view_response(result, task_id)

    async def labels(self, search: str) -> list[dict[str, Any]]:
        result = await self._request("GET", "/labels", params={"q": search, "per_page": 50})
        return _as_object_list(result, "labels")

    async def create_label(self, title: str) -> dict[str, Any]:
        return await self._request("POST", "/labels", json={"title": title})

    async def task_labels(self, task_id: int) -> list[dict[str, Any]]:
        result = await self._request("GET", f"/tasks/{task_id}/labels", params={"per_page": 100})
        return _as_object_list(result, "task labels")

    async def add_task_label(self, task_id: int, label_id: int) -> dict[str, Any]:
        return await self._request("POST", f"/tasks/{task_id}/labels", json={"label_id": label_id})

    async def remove_task_label(self, task_id: int, label_id: int) -> dict[str, Any]:
        return await self._request("DELETE", f"/tasks/{task_id}/labels/{label_id}")

    async def find_users(self, search: str) -> list[dict[str, Any]]:
        result = await self._request("GET", "/users", params={"q": search, "per_page": 50})
        return _as_object_list(result, "users")

    async def task_assignees(self, task_id: int) -> list[dict[str, Any]]:
        result = await self._request("GET", f"/tasks/{task_id}/assignees", params={"per_page": 100})
        return _as_object_list(result, "task assignees")

    async def add_task_assignee(self, task_id: int, user_id: int) -> dict[str, Any]:
        return await self._request("POST", f"/tasks/{task_id}/assignees", json={"user_id": user_id})

    async def remove_task_assignee(self, task_id: int, user_id: int) -> dict[str, Any]:
        return await self._request("DELETE", f"/tasks/{task_id}/assignees/{user_id}")

    async def add_task_comment(self, task_id: int, comment: str) -> dict[str, Any]:
        return await self._request("POST", f"/tasks/{task_id}/comments", json={"comment": comment})

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(
                base_url=f"{self.base_url}/",
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path.lstrip("/"), headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise VikunjaAPIError(503, "Vikunja is unavailable") from exc
        if response.is_error:
            raise VikunjaAPIError(response.status_code, _response_detail(response))
        if response.status_code == 204 or not response.content:
            return {}
        try:
            decoded = response.json()
        except ValueError as exc:
            raise VikunjaAPIError(502, "Vikunja returned malformed JSON") from exc
        if not isinstance(decoded, (dict, list)):
            raise VikunjaAPIError(502, "Vikunja returned an unexpected response")
        return decoded


def _response_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "request failed"
    if isinstance(body, dict):
        message = (
            body.get("detail") or body.get("title") or body.get("message") or body.get("error")
        )
        if isinstance(message, str):
            return message[:500]
    return "request failed"


def _as_object_list(value: dict[str, Any] | list[Any], name: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise VikunjaAPIError(502, f"Vikunja returned invalid {name}")
    items = value.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise VikunjaAPIError(502, f"Vikunja returned invalid {name}")
    return items


def _as_object_list_or_list(value: dict[str, Any] | list[Any], name: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        if all(isinstance(item, dict) for item in value):
            return value
        raise VikunjaAPIError(502, f"Vikunja returned invalid {name}")
    return _as_object_list(value, name)


def _find_task_in_view_response(
    value: object,
    task_id: int,
) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("id") == task_id and ("done" in value or "identifier" in value):
            return value
        for key in ("items", "tasks"):
            found = _find_task_in_view_response(value.get(key), task_id)
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_task_in_view_response(item, task_id)
            if found is not None:
                return found
    return None
