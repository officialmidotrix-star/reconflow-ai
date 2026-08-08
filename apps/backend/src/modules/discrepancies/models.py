"""
Persistence model for the Discrepancy Detection & Classification module.

Discrepancy.reconciliation_match_id is a real, non-null foreign key to
Matching's table - every discrepancy, regardless of category, traces back
to exactly one match. DiscrepancyRun mirrors every prior module's
run-summary pattern.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


class DiscrepancyCategory(str, enum.Enum):
    MISSING_SETTLEMENT = "MISSING_SETTLEMENT"
    UNEXPECTED_SETTLEMENT = "UNEXPECTED_SETTLEMENT"
    INCORRECT_COMMISSION = "INCORRECT_COMMISSION"
    SETTLEMENT_AMOUNT_MISMATCH = "SETTLEMENT_AMOUNT_MISMATCH"
    CANCELLED_AFTER_PREPARATION = "CANCELLED_AFTER_PREPARATION"


class Severity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DiscrepancyStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Discrepancy(Base):
    __tablename__ = "discrepancies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    reconciliation_match_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reconciliation_matches.id"), nullable=False, index=True
    )
    category: Mapped[DiscrepancyCategory] = mapped_column(Enum(DiscrepancyCategory), nullable=False)
    severity: Mapped[Severity] = mapped_column(Enum(Severity), nullable=False)
    estimated_loss: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class DiscrepancyRun(Base):
    __tablename__ = "discrepancy_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    status: Mapped[DiscrepancyStatus] = mapped_column(Enum(DiscrepancyStatus), nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    critical_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    high_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    medium_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
