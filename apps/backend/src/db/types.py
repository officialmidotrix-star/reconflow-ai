"""
A DateTime type that actually stays timezone-aware across every backend.

Plain SQLAlchemy `DateTime(timezone=True)` round-trips correctly on
PostgreSQL (this project's production target) but silently returns a
*naive* datetime on SQLite (this project's test target), because SQLite
has no real timestamp-with-timezone type. Rather than let every module's
tests quietly tolerate that gap - or worse, let it reach production code
that assumes tzinfo is always present - this type decorator normalizes to
UTC on the way in and guarantees UTC tzinfo on the way out, everywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.types import DateTime, TypeDecorator


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "UTCDateTime requires a timezone-aware datetime - naive "
                "datetimes are never silently assumed to be UTC here."
            )
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # SQLite: the driver already handed back a naive value that we
            # know we stored as UTC - reattach it explicitly.
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
