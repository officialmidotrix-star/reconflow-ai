"""
Core business logic for the Matching Engine module.

Two-pass algorithm, exactly as designed:
  pass 1 - exact reference match (confidence 1.00), ties broken by closest
           transaction date then by transaction id
  pass 2 - exact amount + same currency within a day window, confidence
           decaying with date distance to a floor
Whatever remains unmatched on either side becomes a ReconciliationMatch
row with a null counterpart, per the Phase 2 ERD.

Re-running matching for an analysis soft-supersedes its previous matches
(marks them superseded_at, then inserts fresh rows) rather than deleting
them - MatchReview rows need to be able to reference a match permanently,
so this table never hard-deletes. Modules reading ReconciliationMatch
elsewhere (Financial Comparison, Discrepancy Detection) filter to
superseded_at IS NULL to see only the current state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.imports.models import SourceType
from modules.normalization.models import Transaction

from .dependencies import AuditLogger
from .exceptions import InsufficientTransactionsError, MatchingPersistError
from .models import MatchingRun, MatchingStatus, ReconciliationMatch

DAY_WINDOW = 7
EXACT_CONFIDENCE = Decimal("1.00")
BASE_FALLBACK_CONFIDENCE = Decimal("0.70")
DECAY_PER_DAY = Decimal("0.05")
FLOOR_CONFIDENCE = Decimal("0.40")


def _normalize_reference(reference: str) -> str:
    return reference.strip().lower()


class MatchingService:
    def __init__(
        self,
        *,
        db: Session,
        audit_logger: AuditLogger,
        day_window: int = DAY_WINDOW,
        exact_confidence: Decimal = EXACT_CONFIDENCE,
        base_fallback_confidence: Decimal = BASE_FALLBACK_CONFIDENCE,
        decay_per_day: Decimal = DECAY_PER_DAY,
        floor_confidence: Decimal = FLOOR_CONFIDENCE,
    ) -> None:
        self._db = db
        self._audit_logger = audit_logger
        self._day_window = day_window
        self._exact_confidence = exact_confidence
        self._base_fallback_confidence = base_fallback_confidence
        self._decay_per_day = decay_per_day
        self._floor_confidence = floor_confidence

    def run_matching(self, *, analysis_id: str, requested_by: str) -> MatchingRun:
        pos_txns = self._transactions_for(analysis_id, SourceType.POS_EXPORT)
        platform_txns = self._transactions_for(analysis_id, SourceType.PLATFORM_SETTLEMENT)

        if not pos_txns:
            raise InsufficientTransactionsError(
                "There are no POS transactions to match for this analysis."
            )
        if not platform_txns:
            raise InsufficientTransactionsError(
                "There are no platform settlement transactions to match for this analysis."
            )

        unmatched_pos = {t.id: t for t in pos_txns}
        unmatched_platform = {t.id: t for t in platform_txns}
        matches: list[ReconciliationMatch] = []

        self._match_exact_references(unmatched_pos, unmatched_platform, matches, analysis_id)
        self._match_amount_and_date_fallback(unmatched_pos, unmatched_platform, matches, analysis_id)
        self._record_remaining_as_unmatched(unmatched_pos, unmatched_platform, matches, analysis_id)

        return self._persist(analysis_id, matches, requested_by)

    # -- internal steps -------------------------------------------------

    def _transactions_for(self, analysis_id: str, source_type: SourceType) -> list[Transaction]:
        rows = self._db.execute(
            select(Transaction).where(
                Transaction.analysis_id == analysis_id, Transaction.source_type == source_type
            )
        ).scalars().all()
        return sorted(rows, key=lambda t: (t.occurred_at, t.id))

    def _match_exact_references(
        self,
        unmatched_pos: dict[str, Transaction],
        unmatched_platform: dict[str, Transaction],
        matches: list[ReconciliationMatch],
        analysis_id: str,
    ) -> None:
        platform_by_ref: dict[str, list[Transaction]] = {}
        for txn in unmatched_platform.values():
            platform_by_ref.setdefault(_normalize_reference(txn.external_reference), []).append(txn)

        for pos_txn in sorted(unmatched_pos.values(), key=lambda t: (t.occurred_at, t.id)):
            ref = _normalize_reference(pos_txn.external_reference)
            candidates = [p for p in platform_by_ref.get(ref, []) if p.id in unmatched_platform]
            if not candidates:
                continue

            best = min(
                candidates,
                key=lambda p: (abs((p.occurred_at - pos_txn.occurred_at).total_seconds()), p.id),
            )
            matches.append(
                ReconciliationMatch(
                    analysis_id=analysis_id,
                    pos_transaction_id=pos_txn.id,
                    platform_transaction_id=best.id,
                    confidence_score=self._exact_confidence,
                )
            )
            del unmatched_pos[pos_txn.id]
            del unmatched_platform[best.id]

    def _match_amount_and_date_fallback(
        self,
        unmatched_pos: dict[str, Transaction],
        unmatched_platform: dict[str, Transaction],
        matches: list[ReconciliationMatch],
        analysis_id: str,
    ) -> None:
        for pos_txn in sorted(unmatched_pos.values(), key=lambda t: (t.occurred_at, t.id)):
            candidates: list[tuple[int, Transaction]] = []
            for platform_txn in unmatched_platform.values():
                if platform_txn.currency_code != pos_txn.currency_code:
                    continue
                if platform_txn.amount != pos_txn.amount:
                    continue
                day_diff = abs((platform_txn.occurred_at.date() - pos_txn.occurred_at.date()).days)
                if day_diff <= self._day_window:
                    candidates.append((day_diff, platform_txn))

            if not candidates:
                continue

            day_diff, best = min(candidates, key=lambda pair: (pair[0], pair[1].id))
            confidence = max(
                self._floor_confidence,
                self._base_fallback_confidence - self._decay_per_day * day_diff,
            )
            matches.append(
                ReconciliationMatch(
                    analysis_id=analysis_id,
                    pos_transaction_id=pos_txn.id,
                    platform_transaction_id=best.id,
                    confidence_score=confidence,
                )
            )
            del unmatched_pos[pos_txn.id]
            del unmatched_platform[best.id]

    def _record_remaining_as_unmatched(
        self,
        unmatched_pos: dict[str, Transaction],
        unmatched_platform: dict[str, Transaction],
        matches: list[ReconciliationMatch],
        analysis_id: str,
    ) -> None:
        for pos_txn in sorted(unmatched_pos.values(), key=lambda t: (t.occurred_at, t.id)):
            matches.append(
                ReconciliationMatch(
                    analysis_id=analysis_id,
                    pos_transaction_id=pos_txn.id,
                    platform_transaction_id=None,
                    confidence_score=None,
                )
            )
        for platform_txn in sorted(unmatched_platform.values(), key=lambda t: (t.occurred_at, t.id)):
            matches.append(
                ReconciliationMatch(
                    analysis_id=analysis_id,
                    pos_transaction_id=None,
                    platform_transaction_id=platform_txn.id,
                    confidence_score=None,
                )
            )

    def _persist(
        self, analysis_id: str, matches: list[ReconciliationMatch], requested_by: str
    ) -> MatchingRun:
        # Soft-supersede: mark any currently-active matches from a previous
        # run of this same analysis as superseded (never deleted, so a
        # MatchReview referencing one of them stays valid permanently),
        # then insert this run's matches as the new active set.
        previously_active = self._db.execute(
            select(ReconciliationMatch).where(
                ReconciliationMatch.analysis_id == analysis_id,
                ReconciliationMatch.superseded_at.is_(None),
            )
        ).scalars().all()
        now = datetime.now(timezone.utc)
        for old_match in previously_active:
            old_match.superseded_at = now

        for match in matches:
            self._db.add(match)

        matched_count = sum(1 for m in matches if m.pos_transaction_id and m.platform_transaction_id)
        unmatched_pos_count = sum(
            1 for m in matches if m.pos_transaction_id and not m.platform_transaction_id
        )
        unmatched_platform_count = sum(
            1 for m in matches if m.platform_transaction_id and not m.pos_transaction_id
        )

        run = MatchingRun(
            analysis_id=analysis_id,
            status=MatchingStatus.COMPLETED,
            matched_count=matched_count,
            unmatched_pos_count=unmatched_pos_count,
            unmatched_platform_count=unmatched_platform_count,
        )
        self._db.add(run)

        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise MatchingPersistError(
                "We couldn't save the matching result. Please try again."
            ) from exc
        self._db.refresh(run)

        self._audit_logger.log(
            event="matching_run_completed",
            user_id=requested_by,
            analysis_id=analysis_id,
            metadata={
                "matched_count": matched_count,
                "unmatched_pos_count": unmatched_pos_count,
                "unmatched_platform_count": unmatched_platform_count,
            },
        )
        return run
