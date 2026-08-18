from __future__ import annotations

import html
from typing import Any


def task_snapshot(task: dict[str, Any]) -> dict[str, Any]:
    """Keep only displayable task properties when comparing successive events."""

    return {
        "title": _text(task.get("title")),
        "identifier": _text(task.get("identifier")),
        "done": bool(task.get("done")),
        "due_date": _due_date(task.get("due_date")),
        "bucket": _bucket_name(task),
        "labels": sorted(_names(task.get("labels"), title_key="title")),
        "assignees": sorted(_names(task.get("assignees"), title_key="username")),
    }


def render_task(task: dict[str, Any]) -> str:
    snapshot = task_snapshot(task)
    title = html.escape(snapshot["title"] or "Untitled task")
    identifier = html.escape(snapshot["identifier"])
    heading = f"<b>{identifier}: {title}</b>" if identifier else f"<b>{title}</b>"
    lines = [heading, "✅ Completed" if snapshot["done"] else "⬜ Open"]
    if snapshot["bucket"]:
        lines.append(f"📥 Bucket: {html.escape(snapshot['bucket'])}")
    if snapshot["due_date"]:
        lines.append(f"🗓 Due: {html.escape(_human_date(snapshot['due_date']))}")
    if snapshot["labels"]:
        lines.append("🏷 " + ", ".join(html.escape(item) for item in snapshot["labels"]))
    if snapshot["assignees"]:
        lines.append("👤 " + ", ".join(html.escape(item) for item in snapshot["assignees"]))
    return "\n".join(lines)


def render_project_event(event_name: str, payload: dict[str, Any]) -> str:
    """Render a project event that has no stable task message to update."""

    data = payload.get("data")
    project = data.get("project") if isinstance(data, dict) else None
    title = _text(project.get("title")) if isinstance(project, dict) else ""
    if not title:
        title = "Untitled project"
    action = {
        "project.updated": "updated",
        "project.deleted": "deleted",
        "project.shared.user": "shared with a user",
        "project.shared.team": "shared with a team",
    }.get(event_name, "changed")
    return f"📁 <b>{html.escape(title)}</b> {action}."


def change_summary(
    event_name: str,
    previous: dict[str, Any],
    current: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    """A compact, readable reply below the persistent task message."""

    if event_name == "task.created":
        return "Task created."
    data = payload.get("data")
    if event_name.startswith("task.comment.") and isinstance(data, dict):
        comment = data.get("comment")
        if isinstance(comment, dict):
            author = comment.get("author")
            author_name = author.get("username") if isinstance(author, dict) else None
            text = _text(comment.get("comment"))
            prefix = f"Comment from {author_name}: " if author_name else "New comment: "
            return html.escape((prefix + text).strip()[:1_000])
    changes: list[str] = []
    labels = {
        "title": "title",
        "done": "status",
        "due_date": "due date",
        "bucket": "bucket",
        "labels": "labels",
        "assignees": "assignees",
    }
    for field, label in labels.items():
        if previous.get(field) != current.get(field):
            changes.append(label)
    if changes:
        return "Updated: " + ", ".join(changes) + "."
    return "Task updated."


def _bucket_name(task: dict[str, Any]) -> str:
    bucket = task.get("bucket")
    if isinstance(bucket, dict):
        for key in ("title", "name"):
            value = _text(bucket.get(key))
            if value:
                return value
    buckets = task.get("buckets")
    if isinstance(buckets, dict):
        buckets = list(buckets.values())
    if isinstance(buckets, list):
        bucket_id = task.get("bucket_id")
        for candidate in buckets:
            if isinstance(candidate, dict) and candidate.get("id") == bucket_id:
                return _text(candidate.get("title") or candidate.get("name"))
    return ""


def _names(value: object, *, title_key: str) -> list[str]:
    if not isinstance(value, list):
        return []
    names = []
    for item in value:
        if isinstance(item, dict):
            candidate = _text(item.get(title_key) or item.get("name") or item.get("username"))
            if candidate:
                names.append(candidate)
    return names


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _due_date(value: object) -> str:
    """Normalize Vikunja's Go zero-time representation to an absent due date."""
    due_date = _text(value)
    return "" if due_date.startswith("0001-01-01") else due_date


def _human_date(value: str) -> str:
    return value.replace("T", " ").replace("+00:00", " UTC")
