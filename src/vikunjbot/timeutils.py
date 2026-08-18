from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_db_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def from_db_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def parse_event_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("webhook time must be an ISO-8601 string")
    return from_db_time(value)


def exponential_backoff(attempt: int, maximum_seconds: int) -> timedelta:
    """A bounded, deterministic retry delay suitable for a persisted queue."""

    seconds = min(2 ** min(attempt, 16), maximum_seconds)
    return timedelta(seconds=seconds)
