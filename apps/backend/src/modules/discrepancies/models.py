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

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint
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


class Treatment(str, enum.Enum):
    """What the number means for the restaurant. Only RECOVERABLE counts toward
    the headline figure; everything else is shown, but separately."""

    RECOVERABLE = "RECOVERABLE"  # underpaid; amount is NET of commission; claimable
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"  # cancelled after preparation: who cancelled?
    NEEDS_CONTRACT = "NEEDS_CONTRACT"  # would be recoverable, but no (or ambiguous) commission rate
    TO_VERIFY = "TO_VERIFY"  # not a loss: money received, overpayment, commission undercharge
    NOT_CLAIMABLE = "NOT_CLAIMABLE"  # confirmed the restaurant's own cancellation
    NEEDS_RERUN = "NEEDS_RERUN"  # calculated before these rules existed; run the analysis again


class Direction(str, enum.Enum):
    UNDERPAID = "UNDERPAID"  # the restaurant received less than it should have
    OVERPAID = "OVERPAID"  # the restaurant received more / was charged less
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CancelledBy(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    PLATFORM = "PLATFORM"
    RESTAURANT = "RESTAURANT"


# Who may have cancelled for the order to be worth claiming. The restaurant's
# own cancellation is never claimable. Whether a platform actually pays for a
# customer-cancelled order is the platform's policy - the claim letter states
# who cancelled and the platform decides.
CLAIMABLE_CANCELLERS = frozenset({CancelledBy.CUSTOMER, CancelledBy.PLATFORM})


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
    # The gross amount involved (the order's value, or the size of the variance).
    # NOT the headline number - see `treatment` and `claimable_amount`.
    estimated_loss: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)

    treatment: Mapped[Treatment] = mapped_column(
        Enum(Treatment, native_enum=False, length=24), nullable=False, default=Treatment.NEEDS_RERUN
    )
    direction: Mapped[Direction | None] = mapped_column(
        Enum(Direction, native_enum=False, length=16), nullable=True
    )
    # What can actually be claimed, NET of commission. NULL unless treatment is RECOVERABLE.
    claimable_amount: Mapped[object | None] = mapped_column(Numeric(14, 2), nullable=True)
    # The POS/platform order reference, kept here so confirmations survive re-runs.
    order_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class CancellationConfirmation(Base):
    """The restaurant's statement of who cancelled one order. Keyed by order
    reference (not discrepancy id) so it survives re-running the analysis."""

    __tablename__ = "cancellation_confirmations"
    __table_args__ = (UniqueConstraint("analysis_id", "order_reference", name="uq_cancellation_order"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    order_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    cancelled_by: Mapped[CancelledBy] = mapped_column(
        Enum(CancelledBy, native_enum=False, length=16), nullable=False
    )
    confirmed_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


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
    # Orders with no settlement whose status says refunded/void: expected to
    # have no payout, so not discrepancies - counted so nothing disappears silently.
    excluded_refund_void_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
