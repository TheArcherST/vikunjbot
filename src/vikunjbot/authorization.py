from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from vikunjbot.database import DeliveryDestination, Hook
from vikunjbot.timeutils import utc_now


@dataclass(frozen=True, slots=True)
class TelegramActor:
    """An authenticated actor originating from a Telegram user account."""

    user_id: int

    def __post_init__(self) -> None:
        if self.user_id <= 0:
            raise ValueError("Telegram actor user_id must be positive")


class DeliveryDestinationAction(StrEnum):
    """Actions for which access to a delivery destination can be granted."""

    ACT_ON_TASK = "act_on_task"


@dataclass(frozen=True, slots=True)
class DeliveryDestinationGrant:
    """Permission for one actor to perform actions in one destination."""

    destination: DeliveryDestination
    actor: TelegramActor
    actions: frozenset[DeliveryDestinationAction]
    expires_at: datetime | None = None


class AuthorizationDenialReason(StrEnum):
    NO_GRANTS = "no_grants"
    ACTOR_NOT_GRANTED = "actor_not_granted"
    ACTION_NOT_GRANTED = "action_not_granted"
    GRANT_EXPIRED = "grant_expired"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    denial_reason: AuthorizationDenialReason | None = None


class HookOwnershipRepository(Protocol):
    async def get_hook(self, hook_id: UUID) -> Hook | None: ...

    async def list_hooks_owned_by_telegram_user(
        self, telegram_user_id: int
    ) -> tuple[Hook, ...]: ...


class DeliveryDestinationAuthorizationService:
    """Evaluate explicit grants without depending on a concrete auth transport."""

    def __init__(self, hook_repository: HookOwnershipRepository | None = None) -> None:
        self._hook_repository = hook_repository

    async def owned_hooks(self, actor: TelegramActor) -> tuple[Hook, ...]:
        """Return hooks explicitly created by this Telegram actor."""

        repository = self._ownership_repository()
        return await repository.list_hooks_owned_by_telegram_user(actor.user_id)

    async def owned_hook(self, actor: TelegramActor, hook_id: UUID) -> Hook | None:
        repository = self._ownership_repository()
        hook = await repository.get_hook(hook_id)
        if hook is None or hook.owner_telegram_user_id != actor.user_id:
            return None
        return hook

    def _ownership_repository(self) -> HookOwnershipRepository:
        if self._hook_repository is None:
            raise RuntimeError("a hook repository is required for ownership queries")
        return self._hook_repository

    def authorize(
        self,
        *,
        actor: TelegramActor,
        action: DeliveryDestinationAction,
        destination: DeliveryDestination,
        grants: tuple[DeliveryDestinationGrant, ...],
        at: datetime | None = None,
    ) -> AuthorizationDecision:
        relevant = tuple(grant for grant in grants if grant.destination == destination)
        if not relevant:
            return AuthorizationDecision(False, AuthorizationDenialReason.NO_GRANTS)

        actor_grants = tuple(grant for grant in relevant if grant.actor == actor)
        if not actor_grants:
            return AuthorizationDecision(False, AuthorizationDenialReason.ACTOR_NOT_GRANTED)

        action_grants = tuple(grant for grant in actor_grants if action in grant.actions)
        if not action_grants:
            return AuthorizationDecision(False, AuthorizationDenialReason.ACTION_NOT_GRANTED)

        checked_at = at or utc_now()
        has_active_grant = any(
            grant.expires_at is None or grant.expires_at > checked_at for grant in action_grants
        )
        if has_active_grant:
            return AuthorizationDecision(True)
        return AuthorizationDecision(False, AuthorizationDenialReason.GRANT_EXPIRED)

    def is_allowed(
        self,
        *,
        actor: TelegramActor,
        action: DeliveryDestinationAction,
        destination: DeliveryDestination,
        grants: tuple[DeliveryDestinationGrant, ...],
        at: datetime | None = None,
    ) -> bool:
        return self.authorize(
            actor=actor,
            action=action,
            destination=destination,
            grants=grants,
            at=at,
        ).allowed


def task_action_grants(
    *,
    destination: DeliveryDestination,
    telegram_user_ids: frozenset[int],
    expires_at: datetime,
) -> tuple[DeliveryDestinationGrant, ...]:
    """Adapt the currently persisted task-message permission into destination grants."""

    return tuple(
        DeliveryDestinationGrant(
            destination=destination,
            actor=TelegramActor(user_id),
            actions=frozenset({DeliveryDestinationAction.ACT_ON_TASK}),
            expires_at=expires_at,
        )
        for user_id in sorted(telegram_user_ids)
    )
