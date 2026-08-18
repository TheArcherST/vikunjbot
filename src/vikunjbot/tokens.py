from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from vikunjbot.database import Database
from vikunjbot.vikunja import VikunjaAPIError, VikunjaClient


class TokenConfigurationError(RuntimeError):
    """The token cipher cannot safely be initialized."""


class TokenBindingError(RuntimeError):
    """A Telegram user has no usable Vikunja token binding."""


class TokenIdentityChangedError(TokenBindingError):
    """A stored token no longer belongs to the account that bound it."""


class TokenCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise TokenConfigurationError("TOKEN_ENCRYPTION_KEY is required")
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise TokenConfigurationError("TOKEN_ENCRYPTION_KEY must be a Fernet key") from exc

    def encrypt(self, token: str) -> bytes:
        return self._fernet.encrypt(token.encode())

    def decrypt(self, encrypted_token: bytes) -> str:
        try:
            return self._fernet.decrypt(encrypted_token).decode()
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise TokenBindingError("stored Vikunja token can no longer be decrypted") from exc


@dataclass(slots=True)
class TelegramInteraction:
    """One received Telegram update and its one-time token validity check."""

    telegram_user_id: int
    _checked: bool = False
    _identity: dict[str, object] | None = None

    async def validate(self, client: VikunjaClient) -> dict[str, object]:
        if not self._checked:
            # This is deliberately the sole expiry/revocation check made for an
            # interaction. All later API calls reuse this validated client.
            identity = await client.whoami()
            self._checked = True
            self._identity = identity
        if self._identity is None:  # pragma: no cover - protects future refactors
            raise RuntimeError("interaction validation did not produce an identity")
        return self._identity


class TokenService:
    """Owns encrypted bindings and the Telegram-action validation boundary."""

    def __init__(
        self,
        database: Database,
        cipher: TokenCipher,
        api_base_url: str,
        client_factory: Callable[[str, str], VikunjaClient] = VikunjaClient,
    ) -> None:
        self._database = database
        self._cipher = cipher
        self._api_base_url = api_base_url
        self._client_factory = client_factory

    async def bind_direct_token(self, telegram_user_id: int, token: str) -> str:
        """Bind a token explicitly supplied by its owner in a private chat.

        This is intentionally a direct-token path. It verifies the supplied token
        only to identify its owner before storing it and is not a Telegram action
        performed through a pre-existing binding.
        """

        client = self._client_factory(self._api_base_url, token)
        identity = await client.whoami()
        user_id, username = _identity(identity)
        self._database.save_token_binding(
            telegram_user_id,
            self._cipher.encrypt(token),
            user_id,
            username,
        )
        return username

    async def client_for_telegram_action(self, interaction: TelegramInteraction) -> VikunjaClient:
        binding = self._database.get_token_binding(interaction.telegram_user_id)
        if binding is None:
            raise TokenBindingError("send /login <Vikunja API token> in a private chat first")
        token = self._cipher.decrypt(binding.encrypted_token)
        client = self._client_factory(self._api_base_url, token)
        identity = await interaction.validate(client)
        user_id, _ = _identity(identity)
        if user_id != binding.vikunja_user_id:
            raise TokenIdentityChangedError("the token belongs to a different Vikunja account")
        return client

    def unbind(self, telegram_user_id: int) -> bool:
        return self._database.delete_token_binding(telegram_user_id)


def _identity(value: dict[str, object]) -> tuple[int, str]:
    user_id = value.get("id")
    username = value.get("username")
    if not isinstance(user_id, int) or user_id <= 0:
        raise VikunjaAPIError(502, "Vikunja returned an invalid user identity")
    if not isinstance(username, str) or not username:
        username = str(value.get("name") or user_id)
    return user_id, username
