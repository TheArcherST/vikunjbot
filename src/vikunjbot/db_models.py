from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid

from vikunjbot.project_views import ProjectViewKind

JSONValue = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class HookModel(Base):
    __tablename__ = "hooks"
    __table_args__ = (
        CheckConstraint(
            "delivery_destination_chat_id <> 0",
            name="hooks_delivery_destination_chat_id_not_zero",
        ),
        CheckConstraint(
            "event_permission_ttl_seconds > 0", name="hooks_event_permission_ttl_positive"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    delivery_destination_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivery_destination_discussion_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    allowed_telegram_user_ids: Mapped[list[int]] = mapped_column(JSONValue, nullable=False)
    event_permission_ttl_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=86_400
    )
    task_display_fields: Mapped[list[str]] = mapped_column(JSONValue, nullable=False)
    filter_by_views: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    views: Mapped[list[HookViewModel]] = relationship(
        back_populates="hook",
        cascade="all, delete-orphan",
        order_by="HookViewModel.display_order",
    )


class HookViewModel(Base):
    __tablename__ = "hook_views"
    __table_args__ = (
        CheckConstraint("project_view_id > 0", name="hook_views_project_view_id_positive"),
        UniqueConstraint("hook_id", "project_view_id", name="hook_views_unique_view_per_hook"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, autoincrement=True)
    hook_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("hooks.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_view_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    view_kind: Mapped[ProjectViewKind] = mapped_column(
        Enum(
            ProjectViewKind,
            name="project_view_kind",
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)

    hook: Mapped[HookModel] = relationship(back_populates="views")


class EventModel(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'processing', 'retry', 'done')", name="events_valid_state"
        ),
        UniqueConstraint("hook_id", "payload_sha256", name="events_deduplication"),
        Index("events_claimable", "state", "available_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, autoincrement=True)
    hook_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("hooks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONValue, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_name: Mapped[str] = mapped_column(String(255), nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    task_id: Mapped[int | None] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class TokenBindingModel(Base):
    __tablename__ = "token_bindings"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    encrypted_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    vikunja_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vikunja_username: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskMessageModel(Base):
    __tablename__ = "task_messages"
    __table_args__ = (
        UniqueConstraint("hook_id", "task_id", name="task_messages_hook_task"),
        Index(
            "task_messages_by_delivery_destination_message",
            "delivery_destination_chat_id",
            "message_id",
        ),
        Index(
            "task_messages_by_delivery_destination_discussion_message",
            "delivery_destination_discussion_chat_id",
            "delivery_destination_chat_id",
            "message_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, autoincrement=True)
    hook_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("hooks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    delivery_destination_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivery_destination_discussion_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    allowed_telegram_user_ids: Mapped[list[int]] = mapped_column(JSONValue, nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONValue, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    filtered_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatSettingsModel(Base):
    __tablename__ = "chat_settings"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    comment_updates_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
