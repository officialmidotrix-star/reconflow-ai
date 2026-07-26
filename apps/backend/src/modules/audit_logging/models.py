"""
Persistence model for the Audit Logging module.

One table: AuditLogEntry. The metadata dict is stored in a column named
`details`, not `metadata` - SQLAlchemy reserves `metadata` as a
class-level attribute on every declarative model (the table's own
MetaData registry), so a column with that name would fail at
class-definition time. The public log() parameter is still named
`metadata` to match the shared protocol shape exactly; only the ORM
column name differs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLogEntry(Base):
    __tablename__ = "audit_log_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    analysis_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=True, index=True
    )
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow, index=True)
