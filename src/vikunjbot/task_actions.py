from __future__ import annotations

import re
from dataclasses import dataclass

from vikunjbot.vikunja import VikunjaAPIError, VikunjaClient

_LABEL_RE = re.compile(r"(?<!\S)\*(?P<label>[^\s*@]+)")
_ASSIGNEE_RE = re.compile(r"(?<!\S)@(?P<username>[A-Za-z0-9._-]{1,64})")


@dataclass(frozen=True, slots=True)
class TaskActions:
    labels: tuple[str, ...]
    assignees: tuple[str, ...]
    comment: str


def parse_task_actions(text: str) -> TaskActions:
    labels = tuple(match["label"] for match in _LABEL_RE.finditer(text))
    assignees = tuple(match["username"] for match in _ASSIGNEE_RE.finditer(text))
    comment = _LABEL_RE.sub("", text)
    comment = _ASSIGNEE_RE.sub("", comment)
    return TaskActions(labels=labels, assignees=assignees, comment=" ".join(comment.split()))


async def apply_task_actions(
    client: VikunjaClient, task_id: int, actions: TaskActions
) -> list[str]:
    """Toggle explicit labels/assignees, then add remaining text as a comment."""

    completed: list[str] = []
    if actions.labels:
        current_labels = await client.task_labels(task_id)
        by_title = {
            str(label.get("title")).casefold(): int(label["id"])
            for label in current_labels
            if isinstance(label.get("id"), int) and isinstance(label.get("title"), str)
        }
        for title in dict.fromkeys(actions.labels):
            label_id = await _label_id(client, title)
            if label_id in by_title.values():
                await client.remove_task_label(task_id, label_id)
                completed.append(f"removed label *{title}")
                by_title = {key: value for key, value in by_title.items() if value != label_id}
            else:
                await client.add_task_label(task_id, label_id)
                completed.append(f"added label *{title}")
                by_title[title.casefold()] = label_id
    if actions.assignees:
        current_assignees = await client.task_assignees(task_id)
        assigned_ids = {
            int(user["id"]) for user in current_assignees if isinstance(user.get("id"), int)
        }
        for username in dict.fromkeys(actions.assignees):
            user_id = await _user_id(client, username)
            if user_id in assigned_ids:
                await client.remove_task_assignee(task_id, user_id)
                assigned_ids.remove(user_id)
                completed.append(f"unassigned @{username}")
            else:
                await client.add_task_assignee(task_id, user_id)
                assigned_ids.add(user_id)
                completed.append(f"assigned @{username}")
    if actions.comment:
        await client.add_task_comment(task_id, actions.comment)
        completed.append("added a comment")
    return completed


async def _label_id(client: VikunjaClient, title: str) -> int:
    labels = await client.labels(title)
    for label in labels:
        stored_title = label.get("title")
        if (
            isinstance(stored_title, str)
            and stored_title.casefold() == title.casefold()
            and isinstance(label.get("id"), int)
        ):
            return int(label["id"])
    created = await client.create_label(title)
    label_id = created.get("id")
    if not isinstance(label_id, int):
        raise VikunjaAPIError(502, "Vikunja did not return an id for the new label")
    return label_id


async def _user_id(client: VikunjaClient, username: str) -> int:
    users = await client.find_users(username)
    for user in users:
        if user.get("username") == username and isinstance(user.get("id"), int):
            return int(user["id"])
    raise VikunjaAPIError(404, f"Vikunja user @{username} was not found")
