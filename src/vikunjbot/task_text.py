from __future__ import annotations

import html
from typing import Any

from vikunjbot.database import HookView
from vikunjbot.task_fields import ALL_TASK_DISPLAY_FIELDS, TaskDisplayField


def task_snapshot(task: dict[str, Any], views: tuple[HookView, ...] = ()) -> dict[str, Any]:
    """Keep only displayable task properties when comparing successive events."""

    return {
        "title": _text(task.get("title")),
        "identifier": _text(task.get("identifier")),
        "done": bool(task.get("done")),
        "due_date": _due_date(task.get("due_date")),
        "bucket": _bucket_name(task),
        "view_buckets": _view_buckets(task, views),
        "labels": sorted(_names(task.get("labels"), title_key="title")),
        "assignees": sorted(_names(task.get("assignees"), title_key="username")),
    }


def render_task(
    task: dict[str, Any],
    views: tuple[HookView, ...] = (),
    display_fields: frozenset[TaskDisplayField] = ALL_TASK_DISPLAY_FIELDS,
) -> str:
    snapshot = task_snapshot(task, views)
    heading = _task_heading(
        snapshot,
        show_identifier=TaskDisplayField.IDENTIFIER in display_fields,
    )
    lines = [heading]
    if TaskDisplayField.STATUS in display_fields:
        lines.append("✅ Completed" if snapshot["done"] else "⬜ Open")
    if views and TaskDisplayField.BUCKET in display_fields:
        for view_title, bucket_title in snapshot["view_buckets"]:
            lines.append(f"📥 {html.escape(view_title)}: {html.escape(bucket_title)}")
    elif snapshot["bucket"] and TaskDisplayField.BUCKET in display_fields:
        lines.append(f"📥 Bucket: {html.escape(snapshot['bucket'])}")
    if snapshot["due_date"] and TaskDisplayField.DUE_DATE in display_fields:
        lines.append(f"🗓 Due: {html.escape(_human_date(snapshot['due_date']))}")
    if snapshot["labels"] and TaskDisplayField.LABELS in display_fields:
        lines.append("🏷 " + ", ".join(html.escape(item) for item in snapshot["labels"]))
    if snapshot["assignees"] and TaskDisplayField.ASSIGNEES in display_fields:
        lines.append("👤 " + ", ".join(html.escape(item) for item in snapshot["assignees"]))
    return "\n".join(lines)


def render_deleted_task(
    task: dict[str, Any],
    display_fields: frozenset[TaskDisplayField] = ALL_TASK_DISPLAY_FIELDS,
) -> str:
    """Render a terminal, non-actionable task-deletion notice."""

    heading = _task_heading(
        task_snapshot(task),
        show_identifier=TaskDisplayField.IDENTIFIER in display_fields,
    )
    return f"{heading}\n🗑 Deleted"


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
        "view_buckets": "buckets",
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
    buckets = _buckets(task.get("buckets"))
    bucket_id = task.get("bucket_id")
    if isinstance(bucket_id, int) and bucket_id > 0:
        for candidate in buckets:
            if candidate.get("id") == bucket_id:
                return _text(candidate.get("title") or candidate.get("name"))
    if len(buckets) == 1:
        return _text(buckets[0].get("title") or buckets[0].get("name"))
    return ""


def _buckets(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return []
    return [candidate for candidate in value if isinstance(candidate, dict)]


def _view_buckets(task: dict[str, Any], views: tuple[HookView, ...]) -> list[tuple[str, str]]:
    if not views:
        return []
    buckets_by_view = {
        bucket.get("project_view_id"): _text(bucket.get("title") or bucket.get("name"))
        for bucket in _buckets(task.get("buckets"))
    }
    return [
        (view.title, buckets_by_view[view.project_view_id])
        for view in views
        if buckets_by_view.get(view.project_view_id)
    ]


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


def _task_heading(snapshot: dict[str, Any], *, show_identifier: bool = True) -> str:
    title = html.escape(snapshot["title"] or "Untitled task")
    identifier = html.escape(snapshot["identifier"])
    return f"<b>{identifier}: {title}</b>" if identifier and show_identifier else f"<b>{title}</b>"


def _due_date(value: object) -> str:
    """Normalize Vikunja's Go zero-time representation to an absent due date."""
    due_date = _text(value)
    return "" if due_date.startswith("0001-01-01") else due_date


def _human_date(value: str) -> str:
    return value.replace("T", " ").replace("+00:00", " UTC")
