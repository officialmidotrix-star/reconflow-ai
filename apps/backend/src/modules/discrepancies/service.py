"""
Core business logic for the Discrepancy Detection & Classification module.

`DiscrepancyService.detect_discrepancies` reads Matching's and Comparison's
already-persisted output and classifies it - it never re-evaluates
whether a match or comparison was correct. A single ComparisonResult
failing both its checks produces two Discrepancy rows, one per category,
each independently severity-scored.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from modules.financial_comparison.models import ComparisonResult
from modules.matching.models import ReconciliationMatch
from modules.normalization.models import Transaction, TransactionOrderStatus

from .dependencies import AuditLogger, CommissionRateLookup
from .exceptions import DiscrepancyNotFoundError, DiscrepancyPersistError, NotACancellationError
from .models import (
    CLAIMABLE_CANCELLERS,
    CancellationConfirmation,
    CancelledBy,
    Direction,
    Discrepancy,
    DiscrepancyCategory,
    DiscrepancyRun,
    DiscrepancyStatus,
    Severity,
    Treatment,
)

# Severity thresholds against estimated_loss - implicitly the same
# currency every prior module has assumed (SAR). One shared scale across
# all categories, for MVP simplicity; a defensible starting point, not
# calibrated against real data.
_CRITICAL_THRESHOLD = Decimal("1000")
_HIGH_THRESHOLD = Decimal("200")
_MEDIUM_THRESHOLD = Decimal("50")


def severity_for(estimated_loss: Decimal) -> Severity:
    if estimated_loss >= _CRITICAL_THRESHOLD:
        return Severity.CRITICAL
    if estimated_loss >= _HIGH_THRESHOLD:
        return Severity.HIGH
    if estimated_loss >= _MEDIUM_THRESHOLD:
        return Severity.MEDIUM
    return Severity.LOW


class StatusClass(Enum):
    CANCELLED = "CANCELLED"
    REFUNDED_OR_VOID = "REFUNDED_OR_VOID"
    OTHER = "OTHER"


# WHOLE WORDS, not substrings. The earlier version looked for "cancel", "refund"
# or "void" anywhere in the text, so "Cancellation fee waived" or "Avoided"
# style spellings could be misread, and a refund or a void - which is NOT a
# cancellation after preparation - was claimed as one.
#   * a word that says "cancelled" wins (a cancelled-and-refunded order is still
#     a cancellation - it just needs a human to say who cancelled);
#   * a refund/void with no cancel word means "no payout expected": excluded.
# The Arabic spellings are a best-effort starting set: real POS exports have
# never been sampled (same caveat as the order_status column itself).
_CANCEL_WORDS = frozenset(
    {"cancel", "cancelled", "canceled", "ملغي", "ملغى", "ملغاة", "ملغية", "الغاء", "إلغاء"}
)
_REFUND_VOID_WORDS = frozenset(
    {"refund", "refunded", "refunds", "void", "voided", "reversed", "reversal"}
)
# Letters and digits in any script (Arabic included); "_" and "-" separate words,
# so CANCELLED_BY_CUSTOMER is read as three words.
_WORD = re.compile(r"[^\W_]+")


def classify_order_status(raw_status: str) -> StatusClass:
    words = set(_WORD.findall(raw_status.strip().lower()))
    if words & _CANCEL_WORDS:
        return StatusClass.CANCELLED
    if words & _REFUND_VOID_WORDS:
        return StatusClass.REFUNDED_OR_VOID
    return StatusClass.OTHER


_CENT = Decimal("0.01")


def net_of_commission(gross: Decimal, rate: Decimal) -> Decimal:
    """What the restaurant would actually have been paid for `gross`: the
    platform keeps its commission either way."""
    return (gross * (Decimal(1) - rate)).quantize(_CENT, rounding=ROUND_HALF_UP)


class _Classified:
    """Treatment / direction / claimable amount for one discrepancy."""

    __slots__ = ("treatment", "direction", "claimable")

    def __init__(self, treatment: Treatment, direction: Direction, claimable: Decimal | None) -> None:
        self.treatment = treatment
        self.direction = direction
        self.claimable = claimable


class DiscrepancyService:
    def __init__(
        self,
        *,
        db: Session,
        audit_logger: AuditLogger,
        contract_lookup: CommissionRateLookup | None = None,
    ) -> None:
        self._db = db
        self._audit_logger = audit_logger
        # Without a lookup nothing can be quantified net of commission, so every
        # would-be-recoverable amount becomes NEEDS_CONTRACT - never a guess.
        self._contract_lookup = contract_lookup
        self._rate_cache: dict[tuple[str, date], Decimal | None] = {}

    # -- public ------------------------------------------------------------

    def detect_discrepancies(self, *, analysis_id: str, requested_by: str) -> DiscrepancyRun:
        all_matches = self._db.execute(
            select(ReconciliationMatch).where(ReconciliationMatch.analysis_id == analysis_id)
        ).scalars().all()
        matches = [m for m in all_matches if m.superseded_at is None]
        comparison_results = self._db.execute(
            select(ComparisonResult).where(ComparisonResult.analysis_id == analysis_id)
        ).scalars().all()

        # One query each instead of one per row.
        txns = {
            t.id: t
            for t in self._db.execute(
                select(Transaction).where(Transaction.analysis_id == analysis_id)
            ).scalars()
        }
        statuses = {
            st.transaction_id: st.raw_status
            for st in self._db.execute(
                select(TransactionOrderStatus)
                .join(Transaction, Transaction.id == TransactionOrderStatus.transaction_id)
                .where(Transaction.analysis_id == analysis_id)
            ).scalars()
        }
        confirmations = {
            c.order_reference: c.cancelled_by
            for c in self._db.execute(
                select(CancellationConfirmation).where(CancellationConfirmation.analysis_id == analysis_id)
            ).scalars()
        }
        matches_by_id = {m.id: m for m in all_matches}

        found: list[Discrepancy] = []
        unmatched, excluded = self._detect_unmatched(analysis_id, matches, txns, statuses, confirmations)
        found.extend(unmatched)
        found.extend(self._detect_out_of_tolerance(analysis_id, comparison_results, matches_by_id, txns))
        return self._persist(analysis_id, found, excluded, requested_by)

    def confirm_cancellation(
        self, *, discrepancy_id: str, cancelled_by: CancelledBy, requested_by: str
    ) -> Discrepancy:
        """Record who cancelled one order. A cancellation by the customer or
        the platform makes it claimable (net of commission); a cancellation by
        the restaurant makes it not claimable. Survives re-running the analysis."""
        d = self._db.get(Discrepancy, discrepancy_id)
        if d is None:
            raise DiscrepancyNotFoundError("We couldn't find that discrepancy.")
        if d.category != DiscrepancyCategory.CANCELLED_AFTER_PREPARATION or not d.order_reference:
            raise NotACancellationError(
                "Only an order cancelled after preparation can be confirmed this way. "
                "If this analysis is old, run it again first."
            )

        existing = self._db.execute(
            select(CancellationConfirmation).where(
                CancellationConfirmation.analysis_id == d.analysis_id,
                CancellationConfirmation.order_reference == d.order_reference,
            )
        ).scalar_one_or_none()
        if existing is None:
            self._db.add(
                CancellationConfirmation(
                    analysis_id=d.analysis_id, order_reference=d.order_reference,
                    cancelled_by=cancelled_by, confirmed_by_user_id=requested_by,
                )
            )
        else:
            existing.cancelled_by = cancelled_by
            existing.confirmed_by_user_id = requested_by

        match = self._db.get(ReconciliationMatch, d.reconciliation_match_id)
        pos_txn = self._db.get(Transaction, match.pos_transaction_id) if match and match.pos_transaction_id else None
        c = self._classify_cancellation(d.analysis_id, pos_txn, d.estimated_loss, cancelled_by)
        d.treatment, d.direction, d.claimable_amount = c.treatment, c.direction, c.claimable

        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise DiscrepancyPersistError("We couldn't save that. Please try again.") from exc
        self._db.refresh(d)
        self._audit_logger.log(
            event="cancellation_confirmed", user_id=requested_by, analysis_id=d.analysis_id,
            metadata={"discrepancy_id": d.id, "order_reference": d.order_reference,
                      "cancelled_by": cancelled_by.value, "treatment": d.treatment.value},
        )
        return d

    # -- classification ------------------------------------------------------

    def _rate(self, analysis_id: str, occurred_at: datetime | None) -> Decimal | None:
        if self._contract_lookup is None or occurred_at is None:
            return None
        key = (analysis_id, occurred_at.date())
        if key not in self._rate_cache:
            self._rate_cache[key] = self._contract_lookup.get_commission_rate(
                analysis_id=analysis_id, as_of=occurred_at
            )
        return self._rate_cache[key]

    def _recoverable_gross(self, analysis_id: str, occurred_at: datetime | None, gross: Decimal) -> _Classified:
        """The restaurant was underpaid by `gross` (before commission)."""
        if gross <= 0:
            return _Classified(Treatment.TO_VERIFY, Direction.NOT_APPLICABLE, None)
        rate = self._rate(analysis_id, occurred_at)
        if rate is None:
            return _Classified(Treatment.NEEDS_CONTRACT, Direction.UNDERPAID, None)
        return _Classified(Treatment.RECOVERABLE, Direction.UNDERPAID, net_of_commission(gross, rate))

    def _classify_cancellation(
        self, analysis_id: str, pos_txn: Transaction | None, gross: Decimal, cancelled_by: CancelledBy | None
    ) -> _Classified:
        if cancelled_by is None:
            return _Classified(Treatment.NEEDS_CONFIRMATION, Direction.UNDERPAID, None)
        if cancelled_by not in CLAIMABLE_CANCELLERS:
            return _Classified(Treatment.NOT_CLAIMABLE, Direction.NOT_APPLICABLE, None)
        return self._recoverable_gross(analysis_id, pos_txn.occurred_at if pos_txn else None, gross)

    # -- detection ---------------------------------------------------------------

    def _detect_unmatched(
        self,
        analysis_id: str,
        matches: list[ReconciliationMatch],
        txns: dict[str, Transaction],
        statuses: dict[str, str],
        confirmations: dict[str, CancelledBy],
    ) -> tuple[list[Discrepancy], int]:
        found: list[Discrepancy] = []
        excluded = 0
        for match in matches:
            if match.pos_transaction_id and not match.platform_transaction_id:
                pos_txn = txns[match.pos_transaction_id]
                raw = statuses.get(pos_txn.id)
                status_class = classify_order_status(raw) if raw is not None else StatusClass.OTHER
                if status_class == StatusClass.REFUNDED_OR_VOID:
                    excluded += 1  # no payout is expected for a refunded / voided order
                    continue
                if status_class == StatusClass.CANCELLED:
                    c = self._classify_cancellation(
                        analysis_id, pos_txn, pos_txn.amount, confirmations.get(pos_txn.external_reference)
                    )
                    category = DiscrepancyCategory.CANCELLED_AFTER_PREPARATION
                else:
                    c = self._recoverable_gross(analysis_id, pos_txn.occurred_at, pos_txn.amount)
                    category = DiscrepancyCategory.MISSING_SETTLEMENT
                found.append(self._make(analysis_id, match.id, category, pos_txn.amount, c, pos_txn.external_reference))
            elif match.platform_transaction_id and not match.pos_transaction_id:
                platform_txn = txns[match.platform_transaction_id]
                found.append(
                    self._make(
                        analysis_id, match.id, DiscrepancyCategory.UNEXPECTED_SETTLEMENT, platform_txn.amount,
                        _Classified(Treatment.TO_VERIFY, Direction.NOT_APPLICABLE, None),
                        platform_txn.external_reference,
                    )
                )
        return found, excluded

    def _detect_out_of_tolerance(
        self,
        analysis_id: str,
        comparison_results: list[ComparisonResult],
        matches_by_id: dict[str, ReconciliationMatch],
        txns: dict[str, Transaction],
    ) -> list[Discrepancy]:
        found: list[Discrepancy] = []
        for result in comparison_results:
            match = matches_by_id.get(result.reconciliation_match_id)
            pos_txn = txns.get(match.pos_transaction_id) if match and match.pos_transaction_id else None
            reference = pos_txn.external_reference if pos_txn else None

            if not result.commission_within_tolerance:
                v = result.commission_variance  # actual - expected
                if v > 0:  # charged more commission than the contract allows: a real loss, already net
                    c = _Classified(Treatment.RECOVERABLE, Direction.UNDERPAID, v.quantize(_CENT, rounding=ROUND_HALF_UP))
                else:  # charged LESS than the contract: in the restaurant's favour, not a loss
                    c = _Classified(Treatment.TO_VERIFY, Direction.OVERPAID, None)
                found.append(self._make(
                    analysis_id, result.reconciliation_match_id, DiscrepancyCategory.INCORRECT_COMMISSION,
                    abs(v), c, reference))

            if not result.settlement_within_tolerance:
                v = result.settlement_variance  # platform - POS
                if v < 0:  # the platform recorded less than the POS did
                    c = self._recoverable_gross(analysis_id, pos_txn.occurred_at if pos_txn else None, -v)
                else:  # the platform recorded MORE: an overpayment is not a loss
                    c = _Classified(Treatment.TO_VERIFY, Direction.OVERPAID, None)
                found.append(self._make(
                    analysis_id, result.reconciliation_match_id, DiscrepancyCategory.SETTLEMENT_AMOUNT_MISMATCH,
                    abs(v), c, reference))
        return found

    def _make(
        self,
        analysis_id: str,
        reconciliation_match_id: str,
        category: DiscrepancyCategory,
        estimated_loss: Decimal,
        classified: _Classified,
        order_reference: str | None,
    ) -> Discrepancy:
        return Discrepancy(
            analysis_id=analysis_id,
            reconciliation_match_id=reconciliation_match_id,
            category=category,
            severity=severity_for(abs(estimated_loss)),
            estimated_loss=estimated_loss,
            treatment=classified.treatment,
            direction=classified.direction,
            claimable_amount=classified.claimable,
            order_reference=order_reference,
        )

    def _persist(
        self, analysis_id: str, discrepancies: list[Discrepancy], excluded_refund_void: int, requested_by: str
    ) -> DiscrepancyRun:
        # Supersede: replace any discrepancies from a previous run of this
        # same analysis, rather than duplicating them.
        self._db.execute(delete(Discrepancy).where(Discrepancy.analysis_id == analysis_id))
        for d in discrepancies:
            self._db.add(d)

        counts = {severity: 0 for severity in Severity}
        for d in discrepancies:
            counts[d.severity] += 1

        run = DiscrepancyRun(
            analysis_id=analysis_id,
            status=DiscrepancyStatus.COMPLETED,
            total_count=len(discrepancies),
            critical_count=counts[Severity.CRITICAL],
            high_count=counts[Severity.HIGH],
            medium_count=counts[Severity.MEDIUM],
            low_count=counts[Severity.LOW],
            excluded_refund_void_count=excluded_refund_void,
        )
        self._db.add(run)

        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise DiscrepancyPersistError(
                "We couldn't save the discrepancy results. Please try again."
            ) from exc
        self._db.refresh(run)

        self._audit_logger.log(
            event="discrepancy_run_completed",
            user_id=requested_by,
            analysis_id=analysis_id,
            metadata={
                "total_count": run.total_count,
                "critical_count": run.critical_count,
                "high_count": run.high_count,
                "medium_count": run.medium_count,
                "low_count": run.low_count,
                "excluded_refund_void_count": run.excluded_refund_void_count,
            },
        )
        return run
