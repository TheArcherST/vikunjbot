from __future__ import annotations

import html
import math
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from vikunjbot.database import Hook, HookView
from vikunjbot.project_views import ProjectViewKind
from vikunjbot.task_fields import TASK_DISPLAY_FIELD_LABELS, TaskDisplayField

HOOKS_PAGE_SIZE = 6
TTL_PRESETS = (
    (3_600, "1 hour"),
    (21_600, "6 hours"),
    (86_400, "1 day"),
    (259_200, "3 days"),
    (604_800, "7 days"),
)


def hooks_list_panel(
    hooks: tuple[Hook, ...], page: int = 0
) -> tuple[str, InlineKeyboardMarkup]:
    page_count = max(1, math.ceil(len(hooks) / HOOKS_PAGE_SIZE))
    page = min(max(page, 0), page_count - 1)
    visible = hooks[page * HOOKS_PAGE_SIZE : (page + 1) * HOOKS_PAGE_SIZE]
    rows = [
        [
            InlineKeyboardButton(
                text=("🟢" if hook.active else "⚪️")
                + f" Project {hook.project_id} → {_destination_label(hook)}",
                callback_data=f"hk:v:{hook.id}",
            )
        ]
        for hook in visible
    ]
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="‹ Previous", callback_data=f"hk:l:{page - 1}"))
    if page + 1 < page_count:
        navigation.append(InlineKeyboardButton(text="Next ›", callback_data=f"hk:l:{page + 1}"))
    if navigation:
        rows.append(navigation)
    text = "<b>Your hooks</b>"
    if not hooks:
        text += "\n\nYou do not own any hooks yet. Create one with /install_webhook."
    elif page_count > 1:
        text += f"\n\nPage {page + 1} of {page_count}. Choose a hook to manage it."
    else:
        text += "\n\nChoose a hook to manage it."
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def hook_panel(hook: Hook) -> tuple[str, InlineKeyboardMarkup]:
    state = "enabled" if hook.active else "disabled"
    fields = ", ".join(
        TASK_DISPLAY_FIELD_LABELS[field]
        for field in TaskDisplayField
        if field in hook.task_display_fields
    )
    views = ", ".join(html.escape(view.title) for view in hook.views) or "all task events"
    delivery_filter = "selected views (OR)" if hook.filter_by_views else "off"
    text = (
        f"<b>Hook for project {hook.project_id}</b>\n\n"
        f"Status: <b>{state}</b>\n"
        f"Destination: <code>{hook.delivery_destination.chat_id}</code>\n"
        f"Action window: <b>{format_ttl(hook.event_permission_ttl_seconds)}</b>\n"
        f"Views: {views}\n"
        f"Delivery filter: <b>{delivery_filter}</b>\n"
        f"Task fields: {html.escape(fields or 'title only')}\n\n"
        f"Hook ID: <code>{hook.id}</code>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏸ Disable" if hook.active else "▶️ Enable",
                    callback_data=f"hk:a:{hook.id}",
                )
            ],
            [
                InlineKeyboardButton(text="⏱ Action window", callback_data=f"hk:t:{hook.id}"),
                InlineKeyboardButton(text="🧩 Task fields", callback_data=f"hk:f:{hook.id}"),
            ],
            [InlineKeyboardButton(text="🗂 Project views", callback_data=f"hk:w:{hook.id}")],
            [InlineKeyboardButton(text="🗑 Delete hook", callback_data=f"hk:d:{hook.id}")],
            [InlineKeyboardButton(text="‹ All hooks", callback_data="hk:l:0")],
        ]
    )
    return text, keyboard


def delete_hook_confirmation_panel(hook: Hook) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"<b>Delete hook for project {hook.project_id}?</b>\n\n"
        "New events will no longer be delivered. Existing Telegram messages and event history "
        "will remain. The bot will also try to remove the matching webhook from Vikunja.\n\n"
        "This action cannot be undone."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Delete permanently",
                    callback_data=f"hk:dx:{hook.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Cancel",
                    callback_data=f"hk:v:{hook.id}",
                )
            ],
        ]
    )
    return text, keyboard


def ttl_panel(hook: Hook) -> tuple[str, InlineKeyboardMarkup]:
    rows = [
        [
            InlineKeyboardButton(
                text=("✓ " if seconds == hook.event_permission_ttl_seconds else "") + label,
                callback_data=f"hk:ts:{hook.id}:{seconds}",
            )
        ]
        for seconds, label in TTL_PRESETS
    ]
    rows.append([InlineKeyboardButton(text="‹ Hook settings", callback_data=f"hk:v:{hook.id}")])
    text = (
        "<b>Action window</b>\n\n"
        "For how long after a Vikunja event may an authorized Telegram user act on its task?\n\n"
        f"Current value: <b>{format_ttl(hook.event_permission_ttl_seconds)}</b>"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def fields_panel(hook: Hook) -> tuple[str, InlineKeyboardMarkup]:
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if field in hook.task_display_fields else "▫️ ")
            + TASK_DISPLAY_FIELD_LABELS[field],
            callback_data=f"hk:ft:{hook.id}:{field.value}",
        )
        for field in TaskDisplayField
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="Done", callback_data=f"hk:v:{hook.id}")])
    return (
        "<b>Task fields</b>\n\n"
        "The title is always shown. Tap a field to show or hide it when task messages are "
        "created or next updated.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


def views_panel(
    hook: Hook, available_views: tuple[HookView, ...]
) -> tuple[str, InlineKeyboardMarkup]:
    selected_ids = {view.project_view_id for view in hook.views}
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if view.project_view_id in selected_ids else "▫️ ")
                + _view_kind_label(view.view_kind)
                + " · "
                + view.title,
                callback_data=f"hk:wt:{hook.id}:{view.project_view_id}",
            )
        ]
        for view in available_views
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=("✅ " if hook.filter_by_views else "▫️ ")
                + "Deliver only matching tasks",
                callback_data=f"hk:wf:{hook.id}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="Done", callback_data=f"hk:v:{hook.id}")])
    text = (
        "<b>Project views</b>\n\n"
        "Selected views provide their task and bucket context. Enable delivery filtering to "
        "deliver a task only while it is visible in at least one selected view."
    )
    if not available_views:
        text += "\n\nThis project has no views."
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def parse_hook_id(value: str) -> UUID:
    return UUID(value)


def format_ttl(seconds: int) -> str:
    for preset_seconds, label in TTL_PRESETS:
        if seconds == preset_seconds:
            return label
    if seconds % 86_400 == 0:
        return f"{seconds // 86_400} days"
    if seconds % 3_600 == 0:
        return f"{seconds // 3_600} hours"
    return f"{seconds} seconds"


def _destination_label(hook: Hook) -> str:
    suffix = " + discussion" if hook.delivery_destination.discussion_chat_id is not None else ""
    return f"{hook.delivery_destination.chat_id}{suffix}"


def _view_kind_label(kind: ProjectViewKind) -> str:
    return {
        ProjectViewKind.LIST: "List",
        ProjectViewKind.TABLE: "Table",
        ProjectViewKind.GANTT: "Gantt",
        ProjectViewKind.KANBAN: "Kanban",
    }[kind]
