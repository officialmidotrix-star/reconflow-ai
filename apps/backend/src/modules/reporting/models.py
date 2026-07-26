"""
Persistence model for the Reporting & Export module.

One table: Report, the entity the Phase 2 ERD already reserved for this
(REPORT: analysis_id, format, file_path). Every generation creates a new
row - reports are kept history, not superseded state (see package
docstring).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


class ReportFormat(str, enum.Enum):
    CSV = "CSV"
    XLSX = "XLSX"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    format: Mapped[ReportFormat] = mapped_column(Enum(ReportFormat), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
