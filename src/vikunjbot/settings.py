from __future__ import annotations

from urllib.parse import urlparse

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Runtime configuration shared by the relay and bot processes."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    database_url_override: str = Field(default="", validation_alias="VIKUNJBOT_DATABASE_URL")
    postgres_password: str = Field(default="", validation_alias="POSTGRES_PASSWORD")
    relay_host: str = "0.0.0.0"
    relay_port: int = 8080
    relay_max_body_bytes: int = 1_048_576
    relay_lease_seconds: int = 60
    worker_poll_seconds: float = 1.0
    worker_max_backoff_seconds: int = 300
    telegram_bot_token: str = ""
    telegram_proxy_url: str | None = None
    token_encryption_key: str = ""
    vikunja_api_url: HttpUrl = Field(default=HttpUrl("http://vikunja:3456/api/v2"))
    relay_webhook_url: str = "http://vikunjbot-event-relay:8080/events"
    vikunjbot_service_token: str = ""
    log_level: str = "INFO"

    @field_validator("telegram_proxy_url", mode="before")
    @classmethod
    def validate_telegram_proxy_url(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Telegram proxy URL must be a string")
        normalized = value.strip()
        if not normalized:
            return None
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "socks4", "socks5"} or not parsed.hostname:
            raise ValueError("Telegram proxy URL must use http, socks4, or socks5")
        if parsed.port is None:
            raise ValueError("Telegram proxy URL must include a port")
        return normalized

    @property
    def vikunja_api_base_url(self) -> str:
        return str(self.vikunja_api_url).rstrip("/")

    @property
    def database_url(self) -> str:
        """Return the dedicated bot database URL without exposing it in logs."""

        if self.database_url_override:
            return self.database_url_override
        if not self.postgres_password:
            raise RuntimeError(
                "POSTGRES_PASSWORD or VIKUNJBOT_DATABASE_URL is required for vikunjbot storage"
            )
        return URL.create(
            "postgresql+psycopg",
            username="vikunjbot",
            password=self.postgres_password,
            host="vikunjbot-postgres",
            port=5432,
            database="vikunjbot",
        ).render_as_string(hide_password=False)


settings = Settings()
