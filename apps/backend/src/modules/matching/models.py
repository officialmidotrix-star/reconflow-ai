"""
Persistence model for the Matching Engine module.

ReconciliationMatch is the entity the Phase 2 ERD already specified - no
departure there. MatchingRun is a refinement beyond that original sketch,
for the same reason FileValidation and NormalizationRun were: a clean
audit-trail entry point and API response shape, consistent with every
module so far.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


class MatchingStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReconciliationMatch(Base):
    __tablename__ = "reconciliation_matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    pos_transaction_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("transactions.id"), nullable=True, index=True
    )
    platform_transaction_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("transactions.id"), nullable=True, index=True
    )
    confidence_score: Mapped[object | None] = mapped_column(Numeric(3, 2), nullable=True)
    # Null = currently active. Set when superseded by a rerun of Matching
    # Engine or by a manual pairing (modules/manual_review) - never
    # deleted, since MatchReview rows must be able to reference a match
    # permanently. Added when Manual Review's design surfaced that this
    # table's original hard-delete-on-rerun behavior would orphan review
    # history; see modules/matching/service.py's _persist for the rerun
    # side of this and modules/manual_review/service.py for the other.
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class MatchingRun(Base):
    __tablename__ = "matching_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    status: Mapped[MatchingStatus] = mapped_column(Enum(MatchingStatus), nullable=False)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unmatched_pos_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unmatched_platform_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
