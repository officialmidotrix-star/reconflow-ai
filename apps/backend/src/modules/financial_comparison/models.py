"""
Persistence model for the Financial Comparison module.

Two tables belong to this module: ComparisonResult (one row per pair that
was actually compared - i.e. fully matched AND a contract was found) and
ComparisonRun (the summary, mirroring every prior module's run-record
pattern). One-sided matches and no-contract pairs never get a
ComparisonResult row at all - they're only reflected in the run's summary
counts, since there's nothing to compare for them.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


class ComparisonStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ComparisonResult(Base):
    __tablename__ = "comparison_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    reconciliation_match_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reconciliation_matches.id"), nullable=False, index=True
    )

    expected_commission: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    actual_commission: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    commission_variance: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    commission_within_tolerance: Mapped[bool] = mapped_column(Boolean, nullable=False)

    settlement_variance: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    settlement_within_tolerance: Mapped[bool] = mapped_column(Boolean, nullable=False)

    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class ComparisonRun(Base):
    __tablename__ = "comparison_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    status: Mapped[ComparisonStatus] = mapped_column(Enum(ComparisonStatus), nullable=False)
    compared_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    within_tolerance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    out_of_tolerance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_no_contract_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
