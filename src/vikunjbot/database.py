from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload

from vikunjbot.db_models import (
    ChatSettingsModel,
    EventModel,
    HookModel,
    HookViewModel,
    TaskMessageModel,
    TokenBindingModel,
)
from vikunjbot.timeutils import exponential_backoff, parse_event_time, utc_now


@dataclass(frozen=True, slots=True)
class StoredEvent:
    id: int
    hook_id: UUID
    payload: dict[str, Any]
    event_name: str
    event_time: datetime
    task_id: int | None
    attempts: int


@dataclass(frozen=True, slots=True)
class HookView:
    project_view_id: int
    title: str


@dataclass(frozen=True, slots=True)
class DeliveryDestination:
    """A Telegram chat where one hook delivers its task updates."""

    chat_id: int
    discussion_chat_id: int | None = None


@dataclass(frozen=True, slots=True)
class Hook:
    id: UUID
    project_id: int
    delivery_destination: DeliveryDestination
    allowed_telegram_user_ids: frozenset[int]
    event_permission_ttl_seconds: int
    views: tuple[HookView, ...]


@dataclass(frozen=True, slots=True)
class TaskMessage:
    hook_id: UUID
    delivery_destination: DeliveryDestination
    task_id: int
    message_id: int
    expires_at: datetime
    allowed_telegram_user_ids: frozenset[int]
    snapshot: dict[str, Any]
    deleted: bool


@dataclass(frozen=True, slots=True)
class TokenBinding:
    telegram_user_id: int
    encrypted_token: bytes
    vikunja_user_id: int
    vikunja_username: str


class Database:
    """Transactional PostgreSQL storage shared by the relay and bot processes."""

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        if self._engine.url.get_backend_name() != "postgresql":
            raise ValueError("vikunjbot storage requires a PostgreSQL database")
        self._sessions = async_sessionmaker(
            self._engine,
            autoflush=False,
            expire_on_commit=False,
        )

    async def dispose(self) -> None:
        await self._engine.dispose()

    async def create_hook(
        self,
        *,
        project_id: int,
        delivery_destination: DeliveryDestination,
        allowed_telegram_user_ids: frozenset[int],
        views: tuple[HookView, ...],
        event_permission_ttl_seconds: int = 86_400,
    ) -> Hook:
        now = utc_now()
        record = HookModel(
            project_id=project_id,
            delivery_destination_chat_id=delivery_destination.chat_id,
            delivery_destination_discussion_chat_id=delivery_destination.discussion_chat_id,
            allowed_telegram_user_ids=sorted(allowed_telegram_user_ids),
            event_permission_ttl_seconds=event_permission_ttl_seconds,
            created_at=now,
            updated_at=now,
            views=[
                HookViewModel(
                    project_view_id=view.project_view_id,
                    title=view.title,
                    display_order=index,
                )
                for index, view in enumerate(views)
            ],
        )
        async with self._sessions.begin() as session:
            session.add(record)
            await session.flush()
        return _hook(record)

    async def get_active_hook(self, hook_id: UUID) -> Hook | None:
        async with self._sessions() as session:
            record = await session.scalar(
                select(HookModel)
                .options(selectinload(HookModel.views))
                .where(HookModel.id == hook_id, HookModel.active.is_(True))
            )
        return _hook(record) if record is not None else None

    async def enqueue_event(
        self, hook_id: UUID, raw_body: bytes, payload: dict[str, Any]
    ) -> tuple[int, bool]:
        event_time = parse_event_time(payload.get("time"))
        now = utc_now()
        digest = hashlib.sha256(raw_body).hexdigest()
        values = {
            "hook_id": hook_id,
            "payload": payload,
            "payload_sha256": digest,
            "event_name": str(payload["event_name"]),
            "event_time": event_time,
            "task_id": _task_id(payload),
            "received_at": now,
            "state": "pending",
            "available_at": now,
        }
        async with self._sessions.begin() as session:
            statement = (
                postgresql_insert(EventModel)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["hook_id", "payload_sha256"])
                .returning(EventModel.id)
            )
            event_id = await session.scalar(statement)
            if event_id is not None:
                return int(event_id), True
            duplicate = await session.scalar(
                select(EventModel.id).where(
                    EventModel.hook_id == hook_id,
                    EventModel.payload_sha256 == digest,
                )
            )
            if duplicate is None:  # pragma: no cover - database invariant
                raise RuntimeError("event deduplication lookup failed")
            return int(duplicate), False

    async def claim_next_event(self, lease_seconds: int) -> StoredEvent | None:
        now = utc_now()
        lease_until = now + timedelta(seconds=lease_seconds)
        async with self._sessions.begin() as session:
            await session.execute(
                update(EventModel)
                .where(
                    EventModel.state == "processing",
                    EventModel.lease_until.is_not(None),
                    EventModel.lease_until < now,
                )
                .values(state="retry", lease_until=None, available_at=now)
            )
            statement: Select[tuple[EventModel]] = (
                select(EventModel)
                .where(
                    EventModel.state.in_(("pending", "retry")),
                    EventModel.available_at <= now,
                )
                .order_by(EventModel.id)
                .limit(1)
            )
            statement = statement.with_for_update(skip_locked=True)
            record = await session.scalar(statement)
            if record is None:
                return None
            record.state = "processing"
            record.attempts += 1
            record.lease_until = lease_until
            await session.flush()
            return _stored_event(record)

    async def complete_event(self, event_id: int) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(EventModel)
                .where(EventModel.id == event_id)
                .values(state="done", lease_until=None, last_error=None)
            )

    async def retry_event(
        self, event_id: int, attempts: int, error: str, maximum_backoff: int
    ) -> None:
        available_at = utc_now() + exponential_backoff(attempts, maximum_backoff)
        async with self._sessions.begin() as session:
            await session.execute(
                update(EventModel)
                .where(EventModel.id == event_id)
                .values(
                    state="retry",
                    lease_until=None,
                    available_at=available_at,
                    last_error=error[:2_000],
                )
            )

    async def save_token_binding(
        self,
        telegram_user_id: int,
        encrypted_token: bytes,
        vikunja_user_id: int,
        vikunja_username: str,
    ) -> None:
        now = utc_now()
        statement = (
            postgresql_insert(TokenBindingModel)
            .values(
                telegram_user_id=telegram_user_id,
                encrypted_token=encrypted_token,
                vikunja_user_id=vikunja_user_id,
                vikunja_username=vikunja_username,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["telegram_user_id"],
                set_={
                    "encrypted_token": encrypted_token,
                    "vikunja_user_id": vikunja_user_id,
                    "vikunja_username": vikunja_username,
                    "updated_at": now,
                },
            )
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def get_token_binding(self, telegram_user_id: int) -> TokenBinding | None:
        async with self._sessions() as session:
            record = await session.get(TokenBindingModel, telegram_user_id)
        if record is None:
            return None
        return TokenBinding(
            telegram_user_id=record.telegram_user_id,
            encrypted_token=record.encrypted_token,
            vikunja_user_id=record.vikunja_user_id,
            vikunja_username=record.vikunja_username,
        )

    async def delete_token_binding(self, telegram_user_id: int) -> bool:
        async with self._sessions.begin() as session:
            record = await session.get(TokenBindingModel, telegram_user_id)
            if record is None:
                return False
            await session.delete(record)
            return True

    async def get_task_message(self, hook_id: UUID, task_id: int) -> TaskMessage | None:
        async with self._sessions() as session:
            record = await session.scalar(
                select(TaskMessageModel).where(
                    TaskMessageModel.hook_id == hook_id,
                    TaskMessageModel.task_id == task_id,
                )
            )
        return _task_message(record) if record is not None else None

    async def find_task_message_in_delivery_destination(
        self, delivery_destination_chat_id: int, message_id: int
    ) -> TaskMessage | None:
        return await self._find_task_message(
            TaskMessageModel.delivery_destination_chat_id == delivery_destination_chat_id,
            TaskMessageModel.message_id == message_id,
        )

    async def find_task_message_from_delivery_discussion(
        self,
        discussion_chat_id: int,
        delivery_destination_chat_id: int,
        delivery_destination_message_id: int,
    ) -> TaskMessage | None:
        return await self._find_task_message(
            TaskMessageModel.delivery_destination_discussion_chat_id == discussion_chat_id,
            TaskMessageModel.delivery_destination_chat_id == delivery_destination_chat_id,
            TaskMessageModel.message_id == delivery_destination_message_id,
        )

    async def _find_task_message(self, *criteria: object) -> TaskMessage | None:
        async with self._sessions() as session:
            record = await session.scalar(
                select(TaskMessageModel)
                .join(HookModel, TaskMessageModel.hook_id == HookModel.id)
                .where(*criteria, HookModel.active.is_(True), TaskMessageModel.deleted.is_(False))
            )
        return _task_message(record) if record is not None else None

    async def save_task_message(
        self,
        *,
        hook_id: UUID,
        delivery_destination: DeliveryDestination,
        task_id: int,
        message_id: int,
        expires_at: datetime,
        allowed_telegram_user_ids: frozenset[int],
        snapshot: dict[str, Any],
    ) -> None:
        now = utc_now()
        values = {
            "hook_id": hook_id,
            "delivery_destination_chat_id": delivery_destination.chat_id,
            "delivery_destination_discussion_chat_id": delivery_destination.discussion_chat_id,
            "task_id": task_id,
            "message_id": message_id,
            "expires_at": expires_at,
            "allowed_telegram_user_ids": sorted(allowed_telegram_user_ids),
            "snapshot": snapshot,
            "deleted": False,
            "updated_at": now,
        }
        updates = {
            "delivery_destination_chat_id": delivery_destination.chat_id,
            "delivery_destination_discussion_chat_id": delivery_destination.discussion_chat_id,
            "message_id": message_id,
            "expires_at": expires_at,
            "allowed_telegram_user_ids": sorted(allowed_telegram_user_ids),
            "snapshot": snapshot,
            "updated_at": now,
        }
        statement = (
            postgresql_insert(TaskMessageModel)
            .values(**values)
            .on_conflict_do_update(
                constraint="task_messages_hook_task",
                set_=updates,
                # Vikunja task IDs are never reused. A delayed task.updated event
                # must not reactivate a mapping after task.deleted revoked it.
                where=TaskMessageModel.deleted.is_(False),
            )
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def mark_task_message_deleted(self, hook_id: UUID, task_id: int) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(TaskMessageModel)
                .where(TaskMessageModel.hook_id == hook_id, TaskMessageModel.task_id == task_id)
                .values(deleted=True, updated_at=utc_now())
            )

    async def comment_updates_enabled(self, chat_id: int) -> bool:
        async with self._sessions() as session:
            record = await session.get(ChatSettingsModel, chat_id)
        return bool(record and record.comment_updates_enabled)

    async def set_comment_updates_enabled(self, chat_id: int, enabled: bool) -> None:
        now = utc_now()
        statement = (
            postgresql_insert(ChatSettingsModel)
            .values(chat_id=chat_id, comment_updates_enabled=enabled, updated_at=now)
            .on_conflict_do_update(
                index_elements=["chat_id"],
                set_={"comment_updates_enabled": enabled, "updated_at": now},
            )
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)


def _task_id(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    task = data.get("task")
    if isinstance(task, dict) and isinstance(task.get("id"), int):
        return int(task["id"])
    comment = data.get("comment")
    if isinstance(comment, dict) and isinstance(comment.get("task_id"), int):
        return int(comment["task_id"])
    return None


def _stored_event(record: EventModel) -> StoredEvent:
    return StoredEvent(
        id=record.id,
        hook_id=record.hook_id,
        payload=dict(record.payload),
        event_name=record.event_name,
        event_time=record.event_time,
        task_id=record.task_id,
        attempts=record.attempts,
    )


def _hook(record: HookModel) -> Hook:
    return Hook(
        id=record.id,
        project_id=record.project_id,
        delivery_destination=DeliveryDestination(
            chat_id=record.delivery_destination_chat_id,
            discussion_chat_id=record.delivery_destination_discussion_chat_id,
        ),
        allowed_telegram_user_ids=frozenset(record.allowed_telegram_user_ids),
        event_permission_ttl_seconds=record.event_permission_ttl_seconds,
        views=tuple(
            HookView(project_view_id=view.project_view_id, title=view.title)
            for view in record.views
        ),
    )


def _task_message(record: TaskMessageModel) -> TaskMessage:
    return TaskMessage(
        hook_id=record.hook_id,
        delivery_destination=DeliveryDestination(
            chat_id=record.delivery_destination_chat_id,
            discussion_chat_id=record.delivery_destination_discussion_chat_id,
        ),
        task_id=record.task_id,
        message_id=record.message_id,
        expires_at=record.expires_at,
        allowed_telegram_user_ids=frozenset(record.allowed_telegram_user_ids),
        snapshot=dict(record.snapshot),
        deleted=record.deleted,
    )
