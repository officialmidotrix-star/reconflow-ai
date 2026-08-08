"""
Core business logic for the Discrepancy Detection & Classification module.

`DiscrepancyService.detect_discrepancies` reads Matching's and Comparison's
already-persisted output and classifies it - it never re-evaluates
whether a match or comparison was correct. A single ComparisonResult
failing both its checks produces two Discrepancy rows, one per category,
each independently severity-scored.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from modules.financial_comparison.models import ComparisonResult
from modules.matching.models import ReconciliationMatch
from modules.normalization.models import Transaction, TransactionOrderStatus

from .dependencies import AuditLogger
from .exceptions import DiscrepancyPersistError
from .models import Discrepancy, DiscrepancyCategory, DiscrepancyRun, DiscrepancyStatus, Severity

# Severity thresholds against estimated_loss - implicitly the same
# currency every prior module has assumed (SAR). One shared scale across
# all four categories, for MVP simplicity; a defensible starting point,
# not calibrated against real data, same caveat as every numeric
# assumption in this build so far.
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


# Starting set, not a confirmed vocabulary - order_status is a free-text
# optional column precisely because real POS export spellings haven't
# been sampled yet (same caveat as the column's own definition in
# validation/schema_registry.py). Matched case-insensitively, substring
# rather than exact-equals, since real values are more likely to be
# "Cancelled by customer" than a bare "CANCELLED".
_CANCELLATION_INDICATORS = ("cancel", "refund", "void")


def _indicates_cancellation(raw_status: str) -> bool:
    normalized = raw_status.strip().lower()
    return any(indicator in normalized for indicator in _CANCELLATION_INDICATORS)


class DiscrepancyService:
    def __init__(self, *, db: Session, audit_logger: AuditLogger) -> None:
        self._db = db
        self._audit_logger = audit_logger

    def detect_discrepancies(self, *, analysis_id: str, requested_by: str) -> DiscrepancyRun:
        matches = self._db.execute(
            select(ReconciliationMatch).where(
                ReconciliationMatch.analysis_id == analysis_id,
                ReconciliationMatch.superseded_at.is_(None),
            )
        ).scalars().all()
        comparison_results = self._db.execute(
            select(ComparisonResult).where(ComparisonResult.analysis_id == analysis_id)
        ).scalars().all()

        discrepancies: list[Discrepancy] = []
        discrepancies.extend(self._detect_unmatched(analysis_id, matches))
        discrepancies.extend(self._detect_out_of_tolerance(analysis_id, comparison_results))

        return self._persist(analysis_id, discrepancies, requested_by)

    # -- internal steps -------------------------------------------------

    def _detect_unmatched(
        self, analysis_id: str, matches: list[ReconciliationMatch]
    ) -> list[Discrepancy]:
        # Estimated loss here is the raw transaction amount, not abs() of
        # it - as explicitly specified in the approved design, unlike the
        # two comparison-based categories below. This is a known
        # simplification: a refund (negative amount) with no counterpart
        # will bucket as LOW severity regardless of its magnitude, since
        # severity_for() compares against positive thresholds. Flagged
        # rather than silently changed, since the design approved the
        # formula as written - revisit if refund-without-settlement turns
        # out to be a real scenario worth its own treatment.
        found: list[Discrepancy] = []
        for match in matches:
            if match.pos_transaction_id and not match.platform_transaction_id:
                pos_txn = self._db.get(Transaction, match.pos_transaction_id)
                category = DiscrepancyCategory.MISSING_SETTLEMENT
                status = self._db.execute(
                    select(TransactionOrderStatus).where(
                        TransactionOrderStatus.transaction_id == pos_txn.id
                    )
                ).scalar_one_or_none()
                if status is not None and _indicates_cancellation(status.raw_status):
                    category = DiscrepancyCategory.CANCELLED_AFTER_PREPARATION
                found.append(self._make(analysis_id, match.id, category, pos_txn.amount))
            elif match.platform_transaction_id and not match.pos_transaction_id:
                platform_txn = self._db.get(Transaction, match.platform_transaction_id)
                found.append(
                    self._make(
                        analysis_id,
                        match.id,
                        DiscrepancyCategory.UNEXPECTED_SETTLEMENT,
                        platform_txn.amount,
                    )
                )
        return found

    def _detect_out_of_tolerance(
        self, analysis_id: str, comparison_results: list[ComparisonResult]
    ) -> list[Discrepancy]:
        found: list[Discrepancy] = []
        for result in comparison_results:
            if not result.commission_within_tolerance:
                found.append(
                    self._make(
                        analysis_id,
                        result.reconciliation_match_id,
                        DiscrepancyCategory.INCORRECT_COMMISSION,
                        abs(result.commission_variance),
                    )
                )
            if not result.settlement_within_tolerance:
                found.append(
                    self._make(
                        analysis_id,
                        result.reconciliation_match_id,
                        DiscrepancyCategory.SETTLEMENT_AMOUNT_MISMATCH,
                        abs(result.settlement_variance),
                    )
                )
        return found

    def _make(
        self,
        analysis_id: str,
        reconciliation_match_id: str,
        category: DiscrepancyCategory,
        estimated_loss: Decimal,
    ) -> Discrepancy:
        return Discrepancy(
            analysis_id=analysis_id,
            reconciliation_match_id=reconciliation_match_id,
            category=category,
            severity=severity_for(estimated_loss),
            estimated_loss=estimated_loss,
        )

    def _persist(
        self, analysis_id: str, discrepancies: list[Discrepancy], requested_by: str
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
            },
        )
        return run
