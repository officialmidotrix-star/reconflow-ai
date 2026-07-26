"""
Persistence model for the Notification module.

One table: Notification, a permanent log of every send attempt. Never
superseded, same reasoning as Reporting & Export's Report table - this is
historical record-keeping, not "current state."
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


class NotificationEventType(str, enum.Enum):
    ANALYSIS_COMPLETED = "ANALYSIS_COMPLETED"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"


class NotificationStatus(str, enum.Enum):
    SENT = "SENT"
    FAILED = "FAILED"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    event_type: Mapped[NotificationEventType] = mapped_column(
        Enum(NotificationEventType), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(Enum(NotificationStatus), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
