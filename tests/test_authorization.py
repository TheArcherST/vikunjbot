from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from vikunjbot.authorization import (
    AuthorizationDenialReason,
    DeliveryDestinationAction,
    DeliveryDestinationAuthorizationService,
    DeliveryDestinationGrant,
    TelegramActor,
)
from vikunjbot.database import DeliveryDestination, Hook
from vikunjbot.task_fields import ALL_TASK_DISPLAY_FIELDS

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)
DESTINATION = DeliveryDestination(chat_id=-100123, discussion_chat_id=-100456)
ACTOR = TelegramActor(42)


def _hook(owner_id: int | None) -> Hook:
    return Hook(
        id=UUID("00000000-0000-0000-0000-000000000042"),
        project_id=7,
        owner_telegram_user_id=owner_id,
        delivery_destination=DESTINATION,
        allowed_telegram_user_ids=frozenset({42}),
        event_permission_ttl_seconds=86_400,
        task_display_fields=ALL_TASK_DISPLAY_FIELDS,
        active=True,
        views=(),
    )


class HookRepository:
    def __init__(self, hook: Hook) -> None:
        self.hook = hook

    async def get_hook(self, hook_id: UUID) -> Hook | None:
        return self.hook if hook_id == self.hook.id else None

    async def list_hooks_owned_by_telegram_user(self, telegram_user_id: int) -> tuple[Hook, ...]:
        return (self.hook,) if self.hook.owner_telegram_user_id == telegram_user_id else ()


def _grant(
    *,
    destination: DeliveryDestination = DESTINATION,
    actor: TelegramActor = ACTOR,
    actions: frozenset[DeliveryDestinationAction] = frozenset(
        {DeliveryDestinationAction.ACT_ON_TASK}
    ),
    expires_at: datetime | None = None,
) -> DeliveryDestinationGrant:
    return DeliveryDestinationGrant(destination, actor, actions, expires_at)


def test_allows_an_actor_with_a_matching_unexpired_grant() -> None:
    service = DeliveryDestinationAuthorizationService()

    decision = service.authorize(
        actor=ACTOR,
        action=DeliveryDestinationAction.ACT_ON_TASK,
        destination=DESTINATION,
        grants=(_grant(expires_at=NOW + timedelta(seconds=1)),),
        at=NOW,
    )

    assert decision.allowed is True
    assert decision.denial_reason is None


def test_denies_a_different_actor() -> None:
    service = DeliveryDestinationAuthorizationService()

    decision = service.authorize(
        actor=TelegramActor(99),
        action=DeliveryDestinationAction.ACT_ON_TASK,
        destination=DESTINATION,
        grants=(_grant(),),
        at=NOW,
    )

    assert decision.allowed is False
    assert decision.denial_reason == AuthorizationDenialReason.ACTOR_NOT_GRANTED


def test_denies_a_grant_for_a_different_destination() -> None:
    service = DeliveryDestinationAuthorizationService()

    decision = service.authorize(
        actor=ACTOR,
        action=DeliveryDestinationAction.ACT_ON_TASK,
        destination=DESTINATION,
        grants=(_grant(destination=DeliveryDestination(chat_id=-100999)),),
        at=NOW,
    )

    assert decision.allowed is False
    assert decision.denial_reason == AuthorizationDenialReason.NO_GRANTS


def test_denies_an_expired_grant() -> None:
    service = DeliveryDestinationAuthorizationService()

    decision = service.authorize(
        actor=ACTOR,
        action=DeliveryDestinationAction.ACT_ON_TASK,
        destination=DESTINATION,
        grants=(_grant(expires_at=NOW),),
        at=NOW,
    )

    assert decision.allowed is False
    assert decision.denial_reason == AuthorizationDenialReason.GRANT_EXPIRED


def test_denies_a_grant_that_does_not_include_the_action() -> None:
    service = DeliveryDestinationAuthorizationService()

    decision = service.authorize(
        actor=ACTOR,
        action=DeliveryDestinationAction.ACT_ON_TASK,
        destination=DESTINATION,
        grants=(_grant(actions=frozenset()),),
        at=NOW,
    )

    assert decision.allowed is False
    assert decision.denial_reason == AuthorizationDenialReason.ACTION_NOT_GRANTED


def test_is_allowed_is_a_boolean_convenience_api() -> None:
    service = DeliveryDestinationAuthorizationService()

    assert service.is_allowed(
        actor=ACTOR,
        action=DeliveryDestinationAction.ACT_ON_TASK,
        destination=DESTINATION,
        grants=(_grant(),),
        at=NOW,
    )


async def test_returns_only_hooks_explicitly_owned_by_the_actor() -> None:
    owned_hook = _hook(ACTOR.user_id)
    service = DeliveryDestinationAuthorizationService(HookRepository(owned_hook))

    assert await service.owned_hooks(ACTOR) == (owned_hook,)
    assert await service.owned_hooks(TelegramActor(99)) == ()
    assert await service.owned_hook(ACTOR, owned_hook.id) == owned_hook
    assert await service.owned_hook(TelegramActor(99), owned_hook.id) is None


async def test_deleted_hook_is_no_longer_owned_for_management() -> None:
    deleted_hook = replace(_hook(ACTOR.user_id), deleted_at=NOW)
    service = DeliveryDestinationAuthorizationService(HookRepository(deleted_hook))

    assert await service.owned_hook(ACTOR, deleted_hook.id) is None
