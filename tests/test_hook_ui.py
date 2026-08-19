from uuid import UUID

from vikunjbot.database import DeliveryDestination, Hook, HookView
from vikunjbot.hook_ui import (
    delete_hook_confirmation_panel,
    fields_panel,
    hook_panel,
    hooks_list_panel,
    views_panel,
)
from vikunjbot.task_fields import ALL_TASK_DISPLAY_FIELDS


def _hook() -> Hook:
    return Hook(
        id=UUID("00000000-0000-0000-0000-000000000042"),
        project_id=17,
        owner_telegram_user_id=5,
        delivery_destination=DeliveryDestination(-100123, -100456),
        allowed_telegram_user_ids=frozenset({5}),
        event_permission_ttl_seconds=86_400,
        task_display_fields=ALL_TASK_DISPLAY_FIELDS,
        active=True,
        views=(HookView(3, "Development"),),
    )


def test_hook_panels_use_compact_valid_callback_payloads() -> None:
    hook = _hook()
    panels = (
        hooks_list_panel((hook,)),
        hook_panel(hook),
        delete_hook_confirmation_panel(hook),
        fields_panel(hook),
        views_panel(hook, (HookView(3, "Development"), HookView(8, "Release"))),
    )

    callback_data = [
        button.callback_data
        for _, keyboard in panels
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]

    assert callback_data
    assert all(value.startswith("hk:") and len(value.encode()) <= 64 for value in callback_data)
    assert f"hk:wf:{hook.id}" in callback_data


def test_hook_deletion_requires_an_explicit_confirmation() -> None:
    text, keyboard = delete_hook_confirmation_panel(_hook())

    assert "cannot be undone" in text
    assert [button.text for row in keyboard.inline_keyboard for button in row] == [
        "Delete permanently",
        "Cancel",
    ]
