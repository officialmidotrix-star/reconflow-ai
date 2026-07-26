"""
Persistence model for the Deployment, Update & Licensing module.

DeploymentInfo is a singleton (one row per deployment, same discipline as
Organization). LicenseStatus is deliberately not a stored column - it's
computed fresh from license_key/license_expires_at on every read, since a
derived value that's persisted risks going stale the moment time passes.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date as Date
from datetime import datetime, timezone

from sqlalchemy import Date as SQLDate
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime

DEFAULT_VERSION = "0.1.0-dev"


class LicenseStatus(str, enum.Enum):
    UNLICENSED = "UNLICENSED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DeploymentInfo(Base):
    __tablename__ = "deployment_info"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    current_version: Mapped[str] = mapped_column(String(32), nullable=False, default=DEFAULT_VERSION)
    license_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    license_expires_at: Mapped[Date | None] = mapped_column(SQLDate, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class UpdateEvent(Base):
    __tablename__ = "update_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
