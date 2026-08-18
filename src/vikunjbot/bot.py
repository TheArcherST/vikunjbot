from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import BotCommand, Message, MessageOriginChannel

from vikunjbot.database import Database, TaskMessage
from vikunjbot.event_worker import EventWorker
from vikunjbot.routing import InvalidRouteTag, make_route_tag
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
from vikunjbot.vikunja import VikunjaAPIError

logger = logging.getLogger(__name__)


def _command_syntax(value: str) -> str:
    """Render command syntax safely when the bot's default parse mode is HTML."""
    return f"<code>{html.escape(value)}</code>"


_LOGIN_COMMAND = _command_syntax("/login <API token>")
_INSTALL_WEBHOOK_COMMAND = _command_syntax("/install_webhook <project-id> [expiry]")
_INSTALL_CHANNEL_WEBHOOK_COMMAND = _command_syntax("/install_channel_webhook <project-id> [expiry]")


class ChannelBindingError(ValueError):
    """A channel cannot be safely connected to a webhook route."""


@dataclass(frozen=True, slots=True)
class ChannelDiscussion:
    channel_id: int
    discussion_chat_id: int


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
            "Use /webhook [expiry] to get a webhook URL, or "
            f"{_INSTALL_WEBHOOK_COMMAND} "
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
        removed = token_service.unbind(message.from_user.id)
        await message.answer(
            "Vikunja token removed." if removed else "No Vikunja token was connected."
        )

    @router.message(Command("webhook"))
    async def webhook(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        expiry = (command.args or "1d").split()[0]
        try:
            target = _webhook_target(config, message.from_user.id, expiry, message.chat.id)
        except InvalidRouteTag as exc:
            await message.answer(f"Invalid expiry: {html.escape(str(exc))}")
            return
        await message.answer(
            "Create a project webhook in Vikunja with this target URL "
            "and the task events you need:\n"
            f"<code>{html.escape(target)}</code>\n\n"
            "The tag grants this Telegram user access only until the expiry "
            "calculated from each event. For a group/channel, its chat id is "
            "included as the recipient and your Telegram id stays "
            "the allowed actor."
        )

    @router.message(Command("install_webhook"))
    async def install_webhook(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        arguments = (command.args or "").split()
        if not arguments or not arguments[0].isdigit():
            await message.answer(f"Usage: {_INSTALL_WEBHOOK_COMMAND}")
            return
        expiry = arguments[1] if len(arguments) > 1 else "1d"
        try:
            target = _webhook_target(config, message.from_user.id, expiry, message.chat.id)
            client = await token_service.client_for_telegram_action(
                TelegramInteraction(message.from_user.id)
            )
            await client.create_project_webhook(int(arguments[0]), target, _TASK_EVENTS)
        except (TokenBindingError, TokenIdentityChangedError) as exc:
            await message.answer(html.escape(str(exc)))
            return
        except (InvalidRouteTag, VikunjaAPIError) as exc:
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
        arguments = (command.args or "").split()
        if not arguments or not arguments[0].isdigit() or message.reply_to_message is None:
            await message.answer(
                "Forward any post from the target channel here, then reply to it with "
                f"{_INSTALL_CHANNEL_WEBHOOK_COMMAND}."
            )
            return
        expiry = arguments[1] if len(arguments) > 1 else "1d"
        try:
            destination = await _channel_discussion_from_forward(
                bot, message.reply_to_message, message.from_user.id
            )
            target = _channel_webhook_target(config, message.from_user.id, expiry, destination)
            client = await token_service.client_for_telegram_action(
                TelegramInteraction(message.from_user.id)
            )
            await client.create_project_webhook(int(arguments[0]), target, _TASK_EVENTS)
        except (ChannelBindingError, TokenBindingError, TokenIdentityChangedError) as exc:
            await message.answer(html.escape(str(exc)))
            return
        except (InvalidRouteTag, VikunjaAPIError) as exc:
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
        linked = _task_message_for_reply(database, message)
        if linked is None:
            return
        if linked.expires_at <= utc_now():
            await message.reply("This event permission has expired; create a fresh webhook route.")
            return
        if not linked.allowed_telegram_user_ids:
            await message.reply(
                "This channel route is read-only: it has no telegram-id actor grant."
            )
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
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        await message.answer("Only a group administrator can change this setting.")
        return
    database.set_comment_updates_enabled(message.chat.id, enabled)
    if enabled:
        await message.answer("Human-readable task update summaries are now enabled in this group.")
    else:
        await message.answer("Human-readable task update summaries are now disabled in this group.")


async def _channel_discussion_from_forward(
    bot: Bot, forwarded_message: Message, requesting_user_id: int
) -> ChannelDiscussion:
    origin = forwarded_message.forward_origin
    if not isinstance(origin, MessageOriginChannel):
        raise ChannelBindingError(
            "forward a post from the target channel, not a message from a group"
        )

    try:
        channel = await bot.get_chat(origin.chat.id)
        if channel.type != ChatType.CHANNEL:
            raise ChannelBindingError("the forwarded post does not belong to a channel")
        if channel.linked_chat_id is None:
            raise ChannelBindingError("enable a linked discussion group for the channel first")

        bot_user = await bot.get_me()
        bot_in_channel = await bot.get_chat_member(channel.id, bot_user.id)
        if not _is_administrator(bot_in_channel.status):
            raise ChannelBindingError(
                "make the bot a channel administrator with permission to post"
            )
        if getattr(bot_in_channel, "can_post_messages", True) is False:
            raise ChannelBindingError("give the bot permission to post in the channel")

        requester_in_channel = await bot.get_chat_member(channel.id, requesting_user_id)
        if not _is_administrator(requester_in_channel.status):
            raise ChannelBindingError("only a channel administrator can install its webhook")

        bot_in_discussion = await bot.get_chat_member(channel.linked_chat_id, bot_user.id)
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

    return ChannelDiscussion(channel_id=channel.id, discussion_chat_id=channel.linked_chat_id)


def _task_message_for_reply(database: Database, message: Message) -> TaskMessage | None:
    """Resolve replies in a group or under an automatic forward of a channel post."""
    reply = message.reply_to_message
    if reply is None:  # pragma: no cover - enforced by the router filter
        return None
    direct = database.find_task_message(message.chat.id, reply.message_id)
    if direct is not None:
        return direct
    if not reply.is_automatic_forward or not isinstance(reply.forward_origin, MessageOriginChannel):
        return None
    return database.find_discussion_task_message(
        message.chat.id,
        reply.forward_origin.chat.id,
        reply.forward_origin.message_id,
    )


def _webhook_target(config: Settings, telegram_user_id: int, expiry: str, chat_id: int) -> str:
    tag = make_route_tag(telegram_user_id, expiry, chat_id)
    return f"{config.relay_webhook_url.rstrip('/')}/{tag}"


def _channel_webhook_target(
    config: Settings, telegram_user_id: int, expiry: str, destination: ChannelDiscussion
) -> str:
    tag = make_route_tag(
        telegram_user_id,
        expiry,
        channel_id=destination.channel_id,
        discussion_chat_id=destination.discussion_chat_id,
    )
    return f"{config.relay_webhook_url.rstrip('/')}/{tag}"


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
    database = Database(config.app_db_path)
    database.initialize()
    bot = create_telegram_bot(config)
    dispatcher = create_dispatcher(bot, database, config)
    worker = EventWorker(bot, database, config)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="About this bot"),
            BotCommand(command="login", description="Connect a Vikunja API token"),
            BotCommand(command="logout", description="Remove the connected token"),
            BotCommand(command="webhook", description="Show a webhook target URL"),
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


def main() -> None:
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    asyncio.run(run_bot())
