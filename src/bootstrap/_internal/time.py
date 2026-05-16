"""Timezone-aware UTC helpers. All timestamps in bootstrap are tz-aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return current time as a tz-aware UTC datetime."""
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Coerce a datetime to UTC; reject naive datetimes."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime is not allowed; provide tz-aware UTC")
    return dt.astimezone(UTC)
