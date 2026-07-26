"""
Core business logic for the Financial Comparison module.

`ComparisonService.run_comparison` checks each fully-matched pair for one
analysis against its contracted commission rate. It computes everything
into local lists first and only persists once at the end, so a mid-run
exception (see ComparisonInternalError) leaves the database untouched
rather than half-applying a supersede.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from modules.matching.models import ReconciliationMatch
from modules.normalization.models import Transaction

from .dependencies import AuditLogger, ContractLookup
from .exceptions import ComparisonInternalError, ComparisonPersistError
from .models import ComparisonResult, ComparisonRun, ComparisonStatus

TOLERANCE_PCT = Decimal("0.01")
TOLERANCE_FLOOR = Decimal("1.00")


class ComparisonService:
    def __init__(
        self,
        *,
        db: Session,
        contract_lookup: ContractLookup,
        audit_logger: AuditLogger,
        tolerance_pct: Decimal = TOLERANCE_PCT,
        tolerance_floor: Decimal = TOLERANCE_FLOOR,
    ) -> None:
        self._db = db
        self._contract_lookup = contract_lookup
        self._audit_logger = audit_logger
        self._tolerance_pct = tolerance_pct
        self._tolerance_floor = tolerance_floor

    def run_comparison(self, *, analysis_id: str, requested_by: str) -> ComparisonRun:
        matches = self._db.execute(
            select(ReconciliationMatch).where(
                ReconciliationMatch.analysis_id == analysis_id,
                ReconciliationMatch.superseded_at.is_(None),
            )
        ).scalars().all()
        fully_matched = [
            m for m in matches if m.pos_transaction_id and m.platform_transaction_id
        ]

        results: list[ComparisonResult] = []
        skipped_no_contract = 0

        for match in fully_matched:
            pos_txn = self._db.get(Transaction, match.pos_transaction_id)
            platform_txn = self._db.get(Transaction, match.platform_transaction_id)

            rate = self._contract_lookup.get_commission_rate(
                analysis_id=analysis_id, as_of=pos_txn.occurred_at
            )
            if rate is None:
                skipped_no_contract += 1
                continue

            if platform_txn.platform_commission_amount is None:
                raise ComparisonInternalError(
                    "A platform settlement transaction is missing its commission "
                    "amount, despite Normalization requiring one for this source "
                    "type - this indicates an inconsistency, not a normal gap."
                )

            results.append(
                self._compare_pair(
                    analysis_id=analysis_id,
                    match=match,
                    pos_txn=pos_txn,
                    platform_txn=platform_txn,
                    rate=rate,
                )
            )

        return self._persist(analysis_id, results, skipped_no_contract, requested_by)

    # -- internal steps -------------------------------------------------

    def _compare_pair(
        self,
        *,
        analysis_id: str,
        match: ReconciliationMatch,
        pos_txn: Transaction,
        platform_txn: Transaction,
        rate: Decimal,
    ) -> ComparisonResult:
        expected_commission = (pos_txn.amount * rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        actual_commission = platform_txn.platform_commission_amount
        commission_variance = actual_commission - expected_commission
        commission_tolerance = max(
            expected_commission * self._tolerance_pct, self._tolerance_floor
        )
        commission_within = abs(commission_variance) <= commission_tolerance

        settlement_variance = platform_txn.amount - pos_txn.amount
        settlement_tolerance = max(pos_txn.amount * self._tolerance_pct, self._tolerance_floor)
        settlement_within = abs(settlement_variance) <= settlement_tolerance

        return ComparisonResult(
            analysis_id=analysis_id,
            reconciliation_match_id=match.id,
            expected_commission=expected_commission,
            actual_commission=actual_commission,
            commission_variance=commission_variance,
            commission_within_tolerance=commission_within,
            settlement_variance=settlement_variance,
            settlement_within_tolerance=settlement_within,
        )

    def _persist(
        self,
        analysis_id: str,
        results: list[ComparisonResult],
        skipped_no_contract: int,
        requested_by: str,
    ) -> ComparisonRun:
        # Supersede: replace any results from a previous comparison run of
        # this same analysis, rather than duplicating them.
        self._db.execute(
            delete(ComparisonResult).where(ComparisonResult.analysis_id == analysis_id)
        )
        for result in results:
            self._db.add(result)

        compared_count = len(results)
        within_tolerance_count = sum(
            1 for r in results if r.commission_within_tolerance and r.settlement_within_tolerance
        )
        out_of_tolerance_count = compared_count - within_tolerance_count

        run = ComparisonRun(
            analysis_id=analysis_id,
            status=ComparisonStatus.COMPLETED,
            compared_count=compared_count,
            within_tolerance_count=within_tolerance_count,
            out_of_tolerance_count=out_of_tolerance_count,
            skipped_no_contract_count=skipped_no_contract,
        )
        self._db.add(run)

        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise ComparisonPersistError(
                "We couldn't save the comparison result. Please try again."
            ) from exc
        self._db.refresh(run)

        self._audit_logger.log(
            event="comparison_run_completed",
            user_id=requested_by,
            analysis_id=analysis_id,
            metadata={
                "compared_count": compared_count,
                "within_tolerance_count": within_tolerance_count,
                "out_of_tolerance_count": out_of_tolerance_count,
                "skipped_no_contract_count": skipped_no_contract,
            },
        )
        return run
