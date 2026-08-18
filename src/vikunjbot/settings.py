from __future__ import annotations

from pathlib import Path

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by the relay and bot processes."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_db_path: Path = Path("/data/vikunjbot.sqlite3")
    relay_host: str = "0.0.0.0"
    relay_port: int = 8080
    relay_max_body_bytes: int = 1_048_576
    relay_lease_seconds: int = 60
    worker_poll_seconds: float = 1.0
    worker_max_backoff_seconds: int = 300
    telegram_bot_token: str = ""
    token_encryption_key: str = ""
    vikunja_api_url: HttpUrl = Field(default=HttpUrl("http://vikunja:3456/api/v1"))
    relay_webhook_url: str = "http://vikunjbot-event-relay:8080/events"
    vikunjbot_service_token: str = ""
    log_level: str = "INFO"

    @property
    def vikunja_api_base_url(self) -> str:
        return str(self.vikunja_api_url).rstrip("/")


settings = Settings()
