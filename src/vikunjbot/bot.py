from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import BotCommand, Message, MessageOriginChannel

from vikunjbot.database import Database, DeliveryDestination, HookView, TaskMessage
from vikunjbot.event_worker import EventWorker
from vikunjbot.settings import Settings, settings
from vikunjbot.task_actions import apply_task_actions, parse_task_actions
from vikunjbot.timeutils import utc_now
from vikunjbot.tokens import (
    TelegramInteraction,
    TokenBindingError,
    TokenCipher,
    TokenIdentityChangedError,
    TokenService,
)
from vikunjbot.vikunja import VikunjaAPIError, VikunjaClient

logger = logging.getLogger(__name__)


def _command_syntax(value: str) -> str:
    """Render command syntax safely when the bot's default parse mode is HTML."""
    return f"<code>{html.escape(value)}</code>"


_LOGIN_COMMAND = _command_syntax("/login <API token>")
_WEBHOOK_COMMAND = _command_syntax("/webhook <project-id> [kanban-view-ids]")
_INSTALL_WEBHOOK_COMMAND = _command_syntax("/install_webhook <project-id> [kanban-view-ids]")
_INSTALL_CHANNEL_WEBHOOK_COMMAND = _command_syntax(
    "/install_channel_webhook <project-id> [kanban-view-ids]"
)
_VIEWS_COMMAND = _command_syntax("/views <project-id>")


class ChannelBindingError(ValueError):
    """A channel cannot be safely connected to a webhook route."""


class HookConfigurationError(ValueError):
    """A requested webhook configuration is incomplete or unsafe."""


def _is_private_chat(chat_type: ChatType | str) -> bool:
    """Aiogram currently deserializes `Chat.type` to a string at runtime."""
    return chat_type == ChatType.PRIVATE


def _is_administrator(status: ChatMemberStatus | str) -> bool:
    return status == ChatMemberStatus.ADMINISTRATOR or status == ChatMemberStatus.CREATOR


def create_telegram_bot(config: Settings) -> Bot:
    """Create the bot session, optionally routing all Bot API calls through a proxy."""
    session = AiohttpSession(proxy=config.telegram_proxy_url)
    return Bot(
        config.telegram_bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(bot: Bot, database: Database, config: Settings) -> Dispatcher:
    token_service = TokenService(
        database,
        TokenCipher(config.token_encryption_key),
        config.vikunja_api_base_url,
    )
    router = Router(name="vikunjbot")

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        await message.answer(
            "Vikunja events can be delivered here or to a group/channel.\n\n"
            f"Use {_LOGIN_COMMAND} in a private chat, then reply to a task message: "
            "*label toggles a label, @username toggles an assignee, and "
            "remaining text becomes a comment.\n\n"
            f"Use {_VIEWS_COMMAND} to list a project's Kanban views. Then use "
            f"{_WEBHOOK_COMMAND} to get a webhook URL, or {_INSTALL_WEBHOOK_COMMAND} "
            "to create it through your bound account. To publish into a channel and its linked "
            "discussion, forward a channel post here and reply with\n"
            f"{_INSTALL_CHANNEL_WEBHOOK_COMMAND}."
        )

    @router.message(Command("login"))
    async def login(message: Message, command: CommandObject) -> None:
        if not _is_private_chat(message.chat.type):
            await message.answer("For your security, send /login only in a private chat.")
            return
        if message.from_user is None or not command.args:
            await message.answer(f"Usage: {_LOGIN_COMMAND}")
            return
        try:
            username = await token_service.bind_direct_token(
                message.from_user.id, command.args.strip()
            )
        except VikunjaAPIError as exc:
            await message.answer(_api_error_message(exc))
            return
        await message.answer(f"Connected to Vikunja as <b>{html.escape(username)}</b>.")

    @router.message(Command("logout"))
    async def logout(message: Message) -> None:
        if not _is_private_chat(message.chat.type) or message.from_user is None:
            await message.answer("Use /logout in a private chat.")
            return
        removed = await token_service.unbind(message.from_user.id)
        await message.answer(
            "Vikunja token removed." if removed else "No Vikunja token was connected."
        )

    @router.message(Command("webhook"))
    async def webhook(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        arguments = _hook_arguments(command.args)
        if arguments is None:
            await message.answer(f"Usage: {_WEBHOOK_COMMAND}")
            return
        project_id, view_ids = arguments
        try:
            client = await token_service.client_for_telegram_action(
                TelegramInteraction(message.from_user.id)
            )
            webhook_url = await _create_hook_target(
                database=database,
                config=config,
                client=client,
                project_id=project_id,
                telegram_user_id=message.from_user.id,
                delivery_destination=DeliveryDestination(chat_id=message.chat.id),
                view_ids=view_ids,
            )
        except (HookConfigurationError, TokenBindingError, TokenIdentityChangedError) as exc:
            await message.answer(html.escape(str(exc)))
            return
        await message.answer(
            "Create a project webhook in Vikunja with this target URL and the task events "
            "you need:\n"
            f"<code>{html.escape(webhook_url)}</code>\n\n"
            "The URL contains only an opaque UUID. Its delivery destination, actor permission, and "
            "selected views are stored in the bot database."
        )

    @router.message(Command("views"))
    async def views(message: Message, command: CommandObject) -> None:
        if message.from_user is None or not (command.args or "").strip().isdigit():
            await message.answer(f"Usage: {_VIEWS_COMMAND}")
            return
        project_id = int((command.args or "").strip())
        try:
            client = await token_service.client_for_telegram_action(
                TelegramInteraction(message.from_user.id)
            )
            project_views = await client.project_views(project_id)
        except (TokenBindingError, TokenIdentityChangedError) as exc:
            await message.answer(html.escape(str(exc)))
            return
        except VikunjaAPIError as exc:
            await message.answer(_api_error_message(exc))
            return
        kanban = [view for view in project_views if view.get("view_kind") == "kanban"]
        if not kanban:
            await message.answer("This project has no Kanban views.")
            return
        lines = ["Kanban views:"]
        for view in kanban:
            view_id = view.get("id")
            title = view.get("title")
            if isinstance(view_id, int) and isinstance(title, str):
                lines.append(f"• <code>{view_id}</code> — {html.escape(title)}")
        await message.answer("\n".join(lines))

    @router.message(Command("install_webhook"))
    async def install_webhook(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        arguments = _hook_arguments(command.args)
        if arguments is None:
            await message.answer(f"Usage: {_INSTALL_WEBHOOK_COMMAND}")
            return
        project_id, view_ids = arguments
        try:
            client = await token_service.client_for_telegram_action(
                TelegramInteraction(message.from_user.id)
            )
            webhook_url = await _create_hook_target(
                database=database,
                config=config,
                client=client,
                project_id=project_id,
                telegram_user_id=message.from_user.id,
                delivery_destination=DeliveryDestination(chat_id=message.chat.id),
                view_ids=view_ids,
            )
            await client.create_project_webhook(project_id, webhook_url, _TASK_EVENTS)
        except (HookConfigurationError, TokenBindingError, TokenIdentityChangedError) as exc:
            await message.answer(html.escape(str(exc)))
            return
        except VikunjaAPIError as exc:
            await message.answer(_error_message(exc))
            return
        await message.answer(
            "Webhook created. New and updated task events will be sent to this chat."
        )

    @router.message(Command("install_channel_webhook"))
    async def install_channel_webhook(message: Message, command: CommandObject) -> None:
        if not _is_private_chat(message.chat.type) or message.from_user is None:
            await message.answer(
                "For your security, install a channel webhook from a private chat."
            )
            return
        arguments = _hook_arguments(command.args)
        if arguments is None or message.reply_to_message is None:
            await message.answer(
                "Forward any post from the delivery channel here, then reply to it with "
                f"{_INSTALL_CHANNEL_WEBHOOK_COMMAND}."
            )
            return
        project_id, view_ids = arguments
        try:
            destination = await _channel_discussion_from_forward(
                bot, message.reply_to_message, message.from_user.id
            )
            client = await token_service.client_for_telegram_action(
                TelegramInteraction(message.from_user.id)
            )
            webhook_url = await _create_hook_target(
                database=database,
                config=config,
                client=client,
                project_id=project_id,
                telegram_user_id=message.from_user.id,
                delivery_destination=destination,
                view_ids=view_ids,
            )
            await client.create_project_webhook(project_id, webhook_url, _TASK_EVENTS)
        except (
            ChannelBindingError,
            HookConfigurationError,
            TokenBindingError,
            TokenIdentityChangedError,
        ) as exc:
            await message.answer(html.escape(str(exc)))
            return
        except VikunjaAPIError as exc:
            await message.answer(_error_message(exc))
            return
        await message.answer(
            "Webhook created. Task messages will be published in the channel; Telegram will "
            "place their replies in its linked discussion."
        )

    @router.message(Command("enable_comment_updates"))
    async def enable_comment_updates(message: Message) -> None:
        await _set_comment_updates(bot, database, message, enabled=True)

    @router.message(Command("disable_comment_updates"))
    async def disable_comment_updates(message: Message) -> None:
        await _set_comment_updates(bot, database, message, enabled=False)

    @router.message(F.reply_to_message, F.text)
    async def task_message_reply(message: Message) -> None:
        if message.from_user is None or message.reply_to_message is None or not message.text:
            return
        linked = await _task_message_for_reply(database, message)
        if linked is None:
            return
        if linked.expires_at <= utc_now():
            await message.reply("This event permission has expired; create a fresh webhook route.")
            return
        if not linked.allowed_telegram_user_ids:
            await message.reply("This channel route is read-only: it has no Telegram actor grant.")
            return
        if message.from_user.id not in linked.allowed_telegram_user_ids:
            await message.reply(
                "This route does not grant your Telegram account access to this task."
            )
            return
        actions = parse_task_actions(message.text)
        if not (actions.labels or actions.assignees or actions.comment):
            return
        try:
            client = await token_service.client_for_telegram_action(
                TelegramInteraction(message.from_user.id)
            )
            completed = await apply_task_actions(client, linked.task_id, actions)
        except (TokenBindingError, TokenIdentityChangedError) as exc:
            await message.reply(html.escape(str(exc)))
            return
        except VikunjaAPIError as exc:
            await message.reply(_api_error_message(exc))
            return
        await message.reply("Done: " + ", ".join(html.escape(item) for item in completed) + ".")

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def _set_comment_updates(
    bot: Bot, database: Database, message: Message, *, enabled: bool
) -> None:
    is_group = message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}
    if message.from_user is None or not is_group:
        await message.answer("Only a group administrator can change this setting in that group.")
        return
    member = await bot.get_chat_member(chat_id=message.chat.id, user_id=message.from_user.id)
    if member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        await message.answer("Only a group administrator can change this setting.")
        return
    await database.set_comment_updates_enabled(message.chat.id, enabled)
    if enabled:
        await message.answer("Human-readable task update summaries are now enabled in this group.")
    else:
        await message.answer("Human-readable task update summaries are now disabled in this group.")


async def _channel_discussion_from_forward(
    bot: Bot, forwarded_message: Message, requesting_user_id: int
) -> DeliveryDestination:
    origin = forwarded_message.forward_origin
    if not isinstance(origin, MessageOriginChannel):
        raise ChannelBindingError(
            "forward a post from the delivery channel, not a message from a group"
        )

    try:
        channel = await bot.get_chat(chat_id=origin.chat.id)
        if channel.type != ChatType.CHANNEL:
            raise ChannelBindingError("the forwarded post does not belong to a channel")
        if channel.linked_chat_id is None:
            raise ChannelBindingError("enable a linked discussion group for the channel first")

        bot_user = await bot.get_me()
        bot_in_channel = await bot.get_chat_member(chat_id=channel.id, user_id=bot_user.id)
        if not _is_administrator(bot_in_channel.status):
            raise ChannelBindingError(
                "make the bot a channel administrator with permission to post"
            )
        if getattr(bot_in_channel, "can_post_messages", True) is False:
            raise ChannelBindingError("give the bot permission to post in the channel")

        requester_in_channel = await bot.get_chat_member(
            chat_id=channel.id,
            user_id=requesting_user_id,
        )
        if not _is_administrator(requester_in_channel.status):
            raise ChannelBindingError("only a channel administrator can install its webhook")

        bot_in_discussion = await bot.get_chat_member(
            chat_id=channel.linked_chat_id,
            user_id=bot_user.id,
        )
        if not _is_administrator(bot_in_discussion.status):
            raise ChannelBindingError(
                "make the bot an administrator in the linked discussion group too"
            )
        if getattr(bot_in_discussion, "can_send_messages", True) is False:
            raise ChannelBindingError("give the bot permission to send messages in the discussion")
    except TelegramAPIError as exc:
        raise ChannelBindingError(
            "the bot cannot verify the channel and its discussion; check its memberships and rights"
        ) from exc

    return DeliveryDestination(chat_id=channel.id, discussion_chat_id=channel.linked_chat_id)


def _hook_arguments(value: str | None) -> tuple[int, tuple[int, ...]] | None:
    arguments = (value or "").split()
    if not arguments or len(arguments) > 2 or not arguments[0].isdigit():
        return None
    project_id = int(arguments[0])
    if project_id <= 0:
        return None
    if len(arguments) == 1:
        return project_id, ()
    raw_view_ids = arguments[1].split(",")
    if not raw_view_ids or any(not item.isdigit() or int(item) <= 0 for item in raw_view_ids):
        return None
    view_ids = tuple(dict.fromkeys(int(item) for item in raw_view_ids))
    return project_id, view_ids


async def _create_hook_target(
    *,
    database: Database,
    config: Settings,
    client: VikunjaClient,
    project_id: int,
    telegram_user_id: int,
    delivery_destination: DeliveryDestination,
    view_ids: tuple[int, ...],
) -> str:
    views = await _selected_kanban_views(client, project_id, view_ids)
    if views and not config.vikunjbot_service_token:
        raise HookConfigurationError(
            "VIKUNJBOT_SERVICE_TOKEN is required when a hook displays configured Kanban views"
        )
    hook = await database.create_hook(
        project_id=project_id,
        delivery_destination=delivery_destination,
        allowed_telegram_user_ids=frozenset({telegram_user_id}),
        views=views,
    )
    return f"{config.relay_webhook_url.rstrip('/')}/{hook.id}"


async def _selected_kanban_views(
    client: VikunjaClient, project_id: int, view_ids: tuple[int, ...]
) -> tuple[HookView, ...]:
    if not view_ids:
        return ()
    available = {
        int(view["id"]): view
        for view in await client.project_views(project_id)
        if isinstance(view.get("id"), int) and view.get("view_kind") == "kanban"
    }
    selected: list[HookView] = []
    for view_id in view_ids:
        view = available.get(view_id)
        title = view.get("title") if view is not None else None
        if not isinstance(title, str) or not title.strip():
            raise HookConfigurationError(
                f"Kanban view {view_id} does not exist in project {project_id}; "
                f"use {_VIEWS_COMMAND} to list available views"
            )
        selected.append(HookView(project_view_id=view_id, title=title.strip()))
    return tuple(selected)


async def _task_message_for_reply(database: Database, message: Message) -> TaskMessage | None:
    """Resolve replies in a group or under an automatic forward of a channel post."""
    reply = message.reply_to_message
    if reply is None:  # pragma: no cover - enforced by the router filter
        return None
    direct = await database.find_task_message_in_delivery_destination(
        message.chat.id,
        reply.message_id,
    )
    if direct is not None:
        return direct
    if not reply.is_automatic_forward or not isinstance(reply.forward_origin, MessageOriginChannel):
        return None
    return await database.find_task_message_from_delivery_discussion(
        message.chat.id,
        reply.forward_origin.chat.id,
        reply.forward_origin.message_id,
    )


def _api_error_message(error: VikunjaAPIError) -> str:
    if error.status_code == 401:
        return "Your Vikunja token is no longer valid. Send /login with a new API token privately."
    if error.status_code == 403:
        return "Vikunja denied that action for your account."
    if error.status_code == 404:
        return "The requested Vikunja resource was not found or is not accessible to your account."
    return "Vikunja could not complete the action: " + html.escape(error.detail)


def _error_message(error: Exception) -> str:
    if isinstance(error, VikunjaAPIError):
        return _api_error_message(error)
    return html.escape(str(error))


_TASK_EVENTS = [
    "task.created",
    "task.updated",
    "task.deleted",
    "task.assignee.created",
    "task.assignee.deleted",
    "task.comment.created",
    "task.comment.edited",
    "task.comment.deleted",
    "project.updated",
    "project.deleted",
    "project.shared.user",
    "project.shared.team",
]


async def run_bot(config: Settings = settings) -> None:
    if not config.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    database = Database(config.database_url)
    bot = create_telegram_bot(config)
    dispatcher = create_dispatcher(bot, database, config)
    worker = EventWorker(bot, database, config)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="About this bot"),
            BotCommand(command="login", description="Connect a Vikunja API token"),
            BotCommand(command="logout", description="Remove the connected token"),
            BotCommand(command="webhook", description="Show a webhook target URL"),
            BotCommand(command="views", description="List project Kanban views"),
            BotCommand(command="install_webhook", description="Create a project webhook"),
            BotCommand(
                command="install_channel_webhook", description="Connect a channel and discussion"
            ),
            BotCommand(
                command="enable_comment_updates",
                description="Enable update summaries in this group",
            ),
        ]
    )
    worker_task = asyncio.create_task(worker.run(), name="vikunjbot-event-worker")
    try:
        await dispatcher.start_polling(bot)
    finally:
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)
        await bot.session.close()
        await database.dispose()


def main() -> None:
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    asyncio.run(run_bot())
