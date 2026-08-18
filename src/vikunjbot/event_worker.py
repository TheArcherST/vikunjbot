from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ReplyParameters

from vikunjbot.database import Database, StoredEvent
from vikunjbot.routing import DeliveryRoute, InvalidRouteTag, parse_route_tag
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

    async def task(self, event: StoredEvent) -> dict[str, Any] | None:
        data = event.payload.get("data")
        embedded = data.get("task") if isinstance(data, dict) else None
        if isinstance(embedded, dict):
            if (
                _needs_bucket_enrichment(embedded)
                and self._client is not None
                and event.task_id is not None
            ):
                try:
                    return await self._client.get_task(event.task_id, expand_buckets=True)
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
        self._database.initialize()
        while True:
            handled = await self.process_one()
            if not handled:
                await asyncio.sleep(self._config.worker_poll_seconds)

    async def process_one(self) -> bool:
        event = self._database.claim_next_event(self._config.relay_lease_seconds)
        if event is None:
            return False
        try:
            await self._deliver(event)
        except Exception as exc:
            logger.exception("Event %s delivery failed", event.id)
            self._database.retry_event(
                event.id,
                event.attempts,
                str(exc),
                self._config.worker_max_backoff_seconds,
            )
        else:
            self._database.complete_event(event.id)
        return True

    async def _deliver(self, event: StoredEvent) -> None:
        try:
            routes = parse_route_tag(event.route_tag, event.event_time)
        except InvalidRouteTag as exc:
            # This only protects rows accepted by an earlier relay version. A
            # malformed route can never be made valid by retrying its event.
            logger.warning("Ignoring event %s with invalid route tag: %s", event.id, exc)
            return
        if all(route.expires_at <= utc_now() for route in routes):
            logger.info("Ignoring expired event %s", event.id)
            return
        task = await self._enricher.task(event)
        if task is None or event.task_id is None:
            if event.event_name.startswith("project."):
                await self._deliver_project_event(event, routes)
                return
            logger.info("Ignoring non-task event %s (%s)", event.id, event.event_name)
            return
        for route in routes:
            if route.expires_at > utc_now():
                await self._deliver_task(event, task, route)

    async def _deliver_project_event(
        self, event: StoredEvent, routes: tuple[DeliveryRoute, ...]
    ) -> None:
        text = render_project_event(event.event_name, event.payload)
        for route in routes:
            if route.expires_at > utc_now():
                await self._bot.send_message(route.chat_id, text)

    async def _deliver_task(
        self, event: StoredEvent, task: dict[str, Any], route: DeliveryRoute
    ) -> None:
        current_snapshot = task_snapshot(task)
        existing = self._database.get_task_message(route.chat_id, event.task_id or 0)
        text = render_task(task)
        if existing is None:
            sent = await self._bot.send_message(route.chat_id, text)
            message_id = sent.message_id
            previous_snapshot: dict[str, Any] = {}
        else:
            message_id = existing.message_id
            previous_snapshot = existing.snapshot
            try:
                await self._bot.edit_message_text(text, route.chat_id, message_id)
            except TelegramBadRequest as exc:
                # Telegram treats an idempotent edit as an error; retaining the
                # mapping makes retrying a crashed delivery safe in that common case.
                if "message is not modified" not in str(exc).lower():
                    raise
        self._database.save_task_message(
            route.chat_id,
            event.task_id or 0,
            message_id,
            route.expires_at,
            route.allowed_telegram_user_ids,
            current_snapshot,
            discussion_chat_id=route.discussion_chat_id,
        )
        if (
            existing is not None
            and route.discussion_chat_id is None
            and self._database.comment_updates_enabled(route.chat_id)
        ):
            summary = change_summary(
                event.event_name, previous_snapshot, current_snapshot, event.payload
            )
            await self._bot.send_message(
                route.chat_id,
                summary,
                reply_parameters=ReplyParameters(message_id=message_id),
            )


def _needs_bucket_enrichment(task: dict[str, Any]) -> bool:
    return isinstance(task.get("bucket_id"), int) and not isinstance(task.get("bucket"), dict)
