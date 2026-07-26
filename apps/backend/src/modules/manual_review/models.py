"""
Persistence model for the Manual Review & Override module.

Two tables, both permanent audit trails - neither is ever updated or
deleted, only appended to. MatchReview.reconciliation_match_id and
DiscrepancyReview.discrepancy_id are real foreign keys to tables this
module never writes to (Matching's, Discrepancy Detection's) except for
the one deliberate exception described in this package's docstring: manual
pairing also writes a new row to reconciliation_matches directly.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


class MatchReviewDecision(str, enum.Enum):
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"
    # Recorded automatically when a manual pairing creates a brand new
    # match, rather than reviewing an algorithm-made one - see
    # service.py's create_manual_match.
    MANUALLY_PAIRED = "MANUALLY_PAIRED"


class DiscrepancyReviewDecision(str, enum.Enum):
    ACKNOWLEDGE = "ACKNOWLEDGE"
    DISPUTE = "DISPUTE"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MatchReview(Base):
    __tablename__ = "match_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    reconciliation_match_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reconciliation_matches.id"), nullable=False, index=True
    )
    decision: Mapped[MatchReviewDecision] = mapped_column(Enum(MatchReviewDecision), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class DiscrepancyReview(Base):
    __tablename__ = "discrepancy_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    discrepancy_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("discrepancies.id"), nullable=False, index=True
    )
    decision: Mapped[DiscrepancyReviewDecision] = mapped_column(
        Enum(DiscrepancyReviewDecision), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
