"""
Persistence model for the Analysis Orchestration module.

AnalysisStatus is the canonical definition - Data Import's own
dependencies.py used to define a local copy of this enum (a stand-in,
since this module didn't exist yet) and will now import it from here
instead, so there's exactly one definition, not two that happen to share
string values. FAILED is a new addition to that original stand-in: the
Phase 2 workflow design already called for a "fail-analysis" endpoint,
which needs somewhere to record that outcome.

branch_id and created_by are forward-reference foreign keys to tables
that don't have real models yet (Organization & Branch Management,
Identity & Access) - the same pattern every module has used since Data
Import for tables owned by not-yet-built modules.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date as Date
from datetime import datetime, timezone

from sqlalchemy import Date as SQLDate
from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


class AnalysisStatus(str, enum.Enum):
    # Reserved for a future finer-grained pre-upload state (e.g. once
    # Organization & Branch Management exists and an analysis might be
    # configured before it's ready to accept files) - not assigned by any
    # transition in this module yet, but kept in the enum so the schema
    # doesn't need to change when that day comes.
    DRAFT = "DRAFT"
    AWAITING_FILES = "AWAITING_FILES"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    branch_id: Mapped[str] = mapped_column(String(36), ForeignKey("branches.id"), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    parent_analysis_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[AnalysisStatus] = mapped_column(Enum(AnalysisStatus), nullable=False)

    period_start: Mapped[Date] = mapped_column(SQLDate, nullable=False)
    period_end: Mapped[Date] = mapped_column(SQLDate, nullable=False)

    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
