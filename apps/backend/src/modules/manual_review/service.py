"""
Core business logic for the Manual Review & Override module.

Three independent actions, each its own method: reviewing a match,
reviewing a discrepancy, and manually pairing two transactions. The first
two never touch the row they're reviewing - only Matching Engine and
Discrepancy Detection ever write to ReconciliationMatch/Discrepancy under
normal operation. Manual pairing is the one deliberate exception (see
package docstring): it writes a new ReconciliationMatch directly and
soft-supersedes up to two prior one-sided ones, using the same
superseded_at column Matching Engine's own reruns use.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.discrepancies.models import Discrepancy
from modules.imports.models import SourceType
from modules.matching.models import ReconciliationMatch
from modules.normalization.models import Transaction

from .dependencies import AuditLogger
from .exceptions import (
    DiscrepancyNotFoundError,
    MatchNotFoundError,
    ReviewPersistError,
    TransactionAlreadyMatchedError,
    TransactionNotFoundError,
    TransactionNotInAnalysisError,
    WrongSourceTypeError,
)
from .models import DiscrepancyReview, DiscrepancyReviewDecision, MatchReview, MatchReviewDecision


class ManualReviewService:
    def __init__(self, *, db: Session, audit_logger: AuditLogger) -> None:
        self._db = db
        self._audit_logger = audit_logger

    def review_match(
        self,
        *,
        reconciliation_match_id: str,
        decision: MatchReviewDecision,
        reviewed_by: str,
        note: str | None = None,
    ) -> MatchReview:
        match = self._db.get(ReconciliationMatch, reconciliation_match_id)
        if match is None:
            raise MatchNotFoundError("We couldn't find that match.")

        review = MatchReview(
            reconciliation_match_id=reconciliation_match_id,
            decision=decision,
            note=note,
            reviewed_by=reviewed_by,
        )
        self._db.add(review)
        self._commit()
        self._db.refresh(review)

        self._audit_logger.log(
            event="match_reviewed",
            user_id=reviewed_by,
            analysis_id=match.analysis_id,
            metadata={"reconciliation_match_id": reconciliation_match_id, "decision": decision.value},
        )
        return review

    def review_discrepancy(
        self,
        *,
        discrepancy_id: str,
        decision: DiscrepancyReviewDecision,
        reviewed_by: str,
        note: str | None = None,
    ) -> DiscrepancyReview:
        discrepancy = self._db.get(Discrepancy, discrepancy_id)
        if discrepancy is None:
            raise DiscrepancyNotFoundError("We couldn't find that discrepancy.")

        review = DiscrepancyReview(
            discrepancy_id=discrepancy_id,
            decision=decision,
            note=note,
            reviewed_by=reviewed_by,
        )
        self._db.add(review)
        self._commit()
        self._db.refresh(review)

        self._audit_logger.log(
            event="discrepancy_reviewed",
            user_id=reviewed_by,
            analysis_id=discrepancy.analysis_id,
            metadata={"discrepancy_id": discrepancy_id, "decision": decision.value},
        )
        return review

    def create_manual_match(
        self,
        *,
        analysis_id: str,
        pos_transaction_id: str,
        platform_transaction_id: str,
        reviewed_by: str,
        note: str | None = None,
    ) -> tuple[ReconciliationMatch, MatchReview]:
        pos_txn = self._get_transaction_or_raise(pos_transaction_id, label="POS")
        platform_txn = self._get_transaction_or_raise(platform_transaction_id, label="platform")

        self._check_belongs_to_analysis(pos_txn, analysis_id, label="POS")
        self._check_belongs_to_analysis(platform_txn, analysis_id, label="platform")

        if pos_txn.source_type != SourceType.POS_EXPORT:
            raise WrongSourceTypeError(
                "The first transaction must be a POS export transaction."
            )
        if platform_txn.source_type != SourceType.PLATFORM_SETTLEMENT:
            raise WrongSourceTypeError(
                "The second transaction must be a platform settlement transaction."
            )

        existing_pos_match = self._active_match_referencing(pos_transaction_id, side="pos")
        self._check_not_already_matched(existing_pos_match, label="POS")
        existing_platform_match = self._active_match_referencing(
            platform_transaction_id, side="platform"
        )
        self._check_not_already_matched(existing_platform_match, label="platform")

        now = datetime.now(timezone.utc)
        if existing_pos_match is not None:
            existing_pos_match.superseded_at = now
        if existing_platform_match is not None:
            existing_platform_match.superseded_at = now

        new_match = ReconciliationMatch(
            analysis_id=analysis_id,
            pos_transaction_id=pos_transaction_id,
            platform_transaction_id=platform_transaction_id,
            confidence_score=None,  # a human made this call, not the algorithm
        )
        self._db.add(new_match)
        self._db.flush()  # need new_match.id before the review can reference it

        review = MatchReview(
            reconciliation_match_id=new_match.id,
            decision=MatchReviewDecision.MANUALLY_PAIRED,
            note=note,
            reviewed_by=reviewed_by,
        )
        self._db.add(review)

        self._commit()
        self._db.refresh(new_match)
        self._db.refresh(review)

        self._audit_logger.log(
            event="manual_match_created",
            user_id=reviewed_by,
            analysis_id=analysis_id,
            metadata={
                "reconciliation_match_id": new_match.id,
                "pos_transaction_id": pos_transaction_id,
                "platform_transaction_id": platform_transaction_id,
            },
        )
        return new_match, review

    # -- internal steps -------------------------------------------------

    def _get_transaction_or_raise(self, transaction_id: str, *, label: str) -> Transaction:
        txn = self._db.get(Transaction, transaction_id)
        if txn is None:
            raise TransactionNotFoundError(f"We couldn't find that {label} transaction.")
        return txn

    def _check_belongs_to_analysis(self, txn: Transaction, analysis_id: str, *, label: str) -> None:
        if txn.analysis_id != analysis_id:
            raise TransactionNotInAnalysisError(
                f"The {label} transaction doesn't belong to this analysis."
            )

    def _active_match_referencing(
        self, transaction_id: str, *, side: str
    ) -> ReconciliationMatch | None:
        column = (
            ReconciliationMatch.pos_transaction_id
            if side == "pos"
            else ReconciliationMatch.platform_transaction_id
        )
        return self._db.execute(
            select(ReconciliationMatch).where(
                column == transaction_id, ReconciliationMatch.superseded_at.is_(None)
            )
        ).scalars().first()

    def _check_not_already_matched(
        self, match: ReconciliationMatch | None, *, label: str
    ) -> None:
        if match is None:
            return  # never touched by matching at all - fine, available
        if match.pos_transaction_id is not None and match.platform_transaction_id is not None:
            raise TransactionAlreadyMatchedError(
                f"The {label} transaction is already part of a current match."
            )
        # else: it's the one-sided match we're about to supersede - fine

    def _commit(self) -> None:
        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise ReviewPersistError("We couldn't save that. Please try again.") from exc
