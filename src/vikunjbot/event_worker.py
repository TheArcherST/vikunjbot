from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import ReactionTypeEmoji, ReplyParameters

from vikunjbot.database import Database, Hook, StoredEvent
from vikunjbot.settings import Settings
from vikunjbot.task_text import change_summary, render_project_event, render_task, task_snapshot
from vikunjbot.timeutils import utc_now
from vikunjbot.vikunja import VikunjaAPIError, VikunjaClient

logger = logging.getLogger(__name__)


class EventEnricher:
    """Optionally fill missing read-only task details using `vikunjbot`'s account."""

    def __init__(self, config: Settings) -> None:
        self._client = (
            VikunjaClient(config.vikunja_api_base_url, config.vikunjbot_service_token)
            if config.vikunjbot_service_token
            else None
        )

    async def task(self, event: StoredEvent, hook: Hook) -> dict[str, Any] | None:
        data = event.payload.get("data")
        embedded = data.get("task") if isinstance(data, dict) else None
        if isinstance(embedded, dict):
            if (
                (hook.views or _needs_bucket_enrichment(embedded))
                and self._client is not None
                and event.task_id is not None
            ):
                try:
                    enriched = await self._client.get_task(event.task_id, expand_buckets=True)
                    if hook.views:
                        # A configured view is an explicit request for the current
                        # per-view state, rather than the often incomplete generic
                        # task object supplied by a webhook.
                        return enriched
                    return _merge_event_bucket(embedded, enriched)
                except VikunjaAPIError:
                    logger.warning(
                        "Could not enrich task %s with the service account", event.task_id
                    )
            return embedded
        if self._client is None or event.task_id is None:
            return None
        try:
            return await self._client.get_task(event.task_id, expand_buckets=True)
        except VikunjaAPIError:
            logger.warning("Could not read task %s with the service account", event.task_id)
            return None


class EventWorker:
    def __init__(self, bot: Bot, database: Database, config: Settings) -> None:
        self._bot = bot
        self._database = database
        self._config = config
        self._enricher = EventEnricher(config)

    async def run(self) -> None:
        while True:
            handled = await self.process_one()
            if not handled:
                await asyncio.sleep(self._config.worker_poll_seconds)

    async def process_one(self) -> bool:
        event = await self._database.claim_next_event(self._config.relay_lease_seconds)
        if event is None:
            return False
        try:
            await self._deliver(event)
        except Exception as exc:
            logger.exception("Event %s delivery failed", event.id)
            await self._database.retry_event(
                event.id,
                event.attempts,
                str(exc),
                self._config.worker_max_backoff_seconds,
            )
        else:
            await self._database.complete_event(event.id)
        return True

    async def _deliver(self, event: StoredEvent) -> None:
        hook = await self._database.get_active_hook(event.hook_id)
        if hook is None:
            # An operator may deactivate a hook after the relay accepted an event.
            # In that case it must not reach the former Telegram destination.
            logger.info("Ignoring event %s for inactive hook %s", event.id, event.hook_id)
            return
        expires_at = event.event_time + timedelta(seconds=hook.event_permission_ttl_seconds)
        if expires_at <= utc_now():
            logger.info("Ignoring expired event %s", event.id)
            return
        task = await self._enricher.task(event, hook)
        if task is None or event.task_id is None:
            if event.event_name.startswith("project."):
                await self._deliver_project_event(event, hook)
                return
            logger.info("Ignoring non-task event %s (%s)", event.id, event.event_name)
            return
        await self._deliver_task(event, task, hook, expires_at)

    async def _deliver_project_event(self, event: StoredEvent, hook: Hook) -> None:
        text = render_project_event(event.event_name, event.payload)
        await self._bot.send_message(chat_id=hook.chat_id, text=text)

    async def _deliver_task(
        self, event: StoredEvent, task: dict[str, Any], hook: Hook, expires_at: datetime
    ) -> None:
        current_snapshot = task_snapshot(task, hook.views)
        existing = await self._database.get_task_message(hook.id, event.task_id or 0)
        text = render_task(task, hook.views)
        if existing is None:
            sent = await self._bot.send_message(chat_id=hook.chat_id, text=text)
            message_id = sent.message_id
            previous_snapshot: dict[str, Any] = {}
        else:
            message_id = existing.message_id
            previous_snapshot = existing.snapshot
            try:
                await self._bot.edit_message_text(
                    text=text,
                    chat_id=hook.chat_id,
                    message_id=message_id,
                )
            except TelegramBadRequest as exc:
                # Telegram treats an idempotent edit as an error; retaining the
                # mapping makes retrying a crashed delivery safe in that common case.
                if "message is not modified" not in str(exc).lower():
                    raise
        await self._database.save_task_message(
            hook_id=hook.id,
            chat_id=hook.chat_id,
            task_id=event.task_id or 0,
            message_id=message_id,
            expires_at=expires_at,
            allowed_telegram_user_ids=hook.allowed_telegram_user_ids,
            snapshot=current_snapshot,
            discussion_chat_id=hook.discussion_chat_id,
        )
        should_sync_done_reaction = (
            current_snapshot["done"]
            if existing is None
            else previous_snapshot.get("done") != current_snapshot["done"]
        )
        if should_sync_done_reaction:
            await self._sync_done_reaction(
                chat_id=hook.chat_id,
                message_id=message_id,
                is_done=current_snapshot["done"],
            )
        if (
            existing is not None
            and hook.discussion_chat_id is None
            and await self._database.comment_updates_enabled(hook.chat_id)
        ):
            summary = change_summary(
                event.event_name, previous_snapshot, current_snapshot, event.payload
            )
            await self._bot.send_message(
                chat_id=hook.chat_id,
                text=summary,
                reply_parameters=ReplyParameters(message_id=message_id),
            )

    async def _sync_done_reaction(self, *, chat_id: int, message_id: int, is_done: bool) -> None:
        """Reflect completion without making task delivery depend on reactions."""

        reaction = [ReactionTypeEmoji(emoji="✅")] if is_done else []
        try:
            await self._bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction,
            )
        except TelegramAPIError as exc:
            logger.warning(
                "Could not %s done reaction for Telegram message %s in chat %s: %s",
                "set" if is_done else "clear",
                message_id,
                chat_id,
                exc,
            )


def _needs_bucket_enrichment(task: dict[str, Any]) -> bool:
    return isinstance(task.get("bucket_id"), int) and not task_snapshot(task)["bucket"]


def _merge_event_bucket(
    event_task: dict[str, Any], enriched_task: dict[str, Any]
) -> dict[str, Any]:
    """Keep the bucket context captured by the webhook event itself."""
    merged = dict(enriched_task)
    event_bucket_id = event_task.get("bucket_id")
    if isinstance(event_bucket_id, int) and event_bucket_id > 0:
        merged["bucket_id"] = event_bucket_id
    event_buckets = event_task.get("buckets")
    if isinstance(event_buckets, (list, dict)) and event_buckets:
        merged["buckets"] = event_buckets
    return merged
