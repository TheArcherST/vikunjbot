from __future__ import annotations

from vikunjbot.bot import _INSTALL_WEBHOOK_COMMAND, _LOGIN_COMMAND, _command_syntax


def test_command_syntax_escapes_angle_brackets_for_html_parse_mode() -> None:
    assert _command_syntax("/login <API token>") == "<code>/login &lt;API token&gt;</code>"


def test_help_command_syntax_uses_html_safe_literals() -> None:
    assert _LOGIN_COMMAND == "<code>/login &lt;API token&gt;</code>"
    assert _INSTALL_WEBHOOK_COMMAND == "<code>/install_webhook &lt;project-id&gt; [expiry]</code>"
