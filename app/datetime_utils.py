"""UTC datetime helpers and database fields.

PostgreSQL ``TIMESTAMPTZ`` values and SQLite test values do not necessarily
come back from drivers with the same ``tzinfo``.  Normalize at the persistence
boundary as well as before security-sensitive comparisons.
"""
from datetime import datetime, timezone

from peewee import DateTimeField


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Return *value* as an aware UTC datetime.

    Legacy naive database values represent UTC in this application, so attaching
    UTC (rather than interpreting them in the server's local timezone) preserves
    their intended instant.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def isoformat_utc(value: datetime | None) -> str | None:
    """Serialize persisted datetimes with the backend-wide UTC ``Z`` convention."""
    normalized = ensure_utc(value)
    if normalized is None:
        return None
    return normalized.isoformat().replace("+00:00", "Z")


class UTCDateTimeField(DateTimeField):
    """Peewee datetime field backed by PostgreSQL ``TIMESTAMPTZ``."""

    field_type = "TIMESTAMPTZ"

    def db_value(self, value):
        return super().db_value(ensure_utc(value))

    def python_value(self, value):
        converted = super().python_value(value)
        # Peewee's default formats do not include a UTC offset. SQLite can
        # return the ISO-8601 representation written by its adapter, though.
        if isinstance(converted, str):
            converted = datetime.fromisoformat(converted)
        return ensure_utc(converted)
