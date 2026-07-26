"""
Unit tests for ManualReviewService.

ReconciliationMatch, Discrepancy, and Transaction rows are constructed
directly, same testing-boundary rationale as every downstream module's
tests so far.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from sqlalchemy import Column, String, Table, create_engine, select
from sqlalchemy.orm import Session

from modules.discrepancies.models import Discrepancy, DiscrepancyCategory, Severity
from modules.imports.models import Base, SourceType
from modules.manual_review.dependencies import InMemoryAuditLogger
from modules.manual_review.exceptions import (
    DiscrepancyNotFoundError,
    MatchNotFoundError,
    TransactionAlreadyMatchedError,
    TransactionNotFoundError,
    TransactionNotInAnalysisError,
    WrongSourceTypeError,
)
from modules.manual_review.models import DiscrepancyReviewDecision, MatchReview, MatchReviewDecision
from modules.manual_review.service import ManualReviewService
from modules.matching.models import ReconciliationMatch
from modules.normalization.models import Transaction

ANALYSIS_ID = "analysis-1"
OTHER_ANALYSIS_ID = "analysis-2"
USER_ID = "user-1"

# "analyses" is now a real table (Analysis Orchestration) - importing its
# model registers it, replacing the stand-in this file used before that
# module existed. "branches" and "users" still don't have real models.
from modules.analysis_orchestration.models import Analysis  # noqa: E402,F401

# "branches" is now a real table (Organization & Branch Management) -
# importing its model registers it, replacing the stand-in this file
# used before that module existed.
from modules.organizations.models import Branch  # noqa: E402,F401
# "users" is now a real table (Identity & Access) - importing its model
# registers it, replacing the stand-in this file used before that module
# existed.
from modules.identity_access.models import User  # noqa: E402,F401


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def audit_logger():
    return InMemoryAuditLogger()


@pytest.fixture()
def service(db, audit_logger):
    return ManualReviewService(db=db, audit_logger=audit_logger)


def _day() -> datetime:
    return datetime(2026, 1, 15, 10, 0, 0, tzinfo=dt_timezone.utc)


def _make_txn(
    db: Session,
    *,
    source_type: SourceType,
    external_reference: str = "1001",
    amount: Decimal = Decimal("10.00"),
    analysis_id: str = ANALYSIS_ID,
) -> Transaction:
    txn = Transaction(
        analysis_id=analysis_id,
        uploaded_file_id=f"uploaded-file-{source_type.value}",
        source_type=source_type,
        external_reference=external_reference,
        occurred_at=_day(),
        amount=amount,
        currency_code="SAR",
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _make_match(
    db: Session,
    *,
    pos_transaction_id: str | None,
    platform_transaction_id: str | None,
    analysis_id: str = ANALYSIS_ID,
    superseded_at=None,
) -> ReconciliationMatch:
    match = ReconciliationMatch(
        analysis_id=analysis_id,
        pos_transaction_id=pos_transaction_id,
        platform_transaction_id=platform_transaction_id,
        confidence_score=Decimal("1.00") if pos_transaction_id and platform_transaction_id else None,
        superseded_at=superseded_at,
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


def _make_discrepancy(db: Session, *, reconciliation_match_id: str) -> Discrepancy:
    d = Discrepancy(
        analysis_id=ANALYSIS_ID,
        reconciliation_match_id=reconciliation_match_id,
        category=DiscrepancyCategory.MISSING_SETTLEMENT,
        severity=Severity.LOW,
        estimated_loss=Decimal("10.00"),
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


class TestMatchReview:
    def test_confirm_recorded(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT)
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT)
        match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)

        review = service.review_match(
            reconciliation_match_id=match.id, decision=MatchReviewDecision.CONFIRM,
            reviewed_by=USER_ID, note="Looks right.",
        )
        assert review.decision == MatchReviewDecision.CONFIRM
        assert review.reviewed_by == USER_ID
        assert review.note == "Looks right."

    def test_reject_recorded(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT)
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT)
        match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)

        review = service.review_match(
            reconciliation_match_id=match.id, decision=MatchReviewDecision.REJECT, reviewed_by=USER_ID,
        )
        assert review.decision == MatchReviewDecision.REJECT

    def test_rereviewing_creates_new_record_not_overwrite(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT)
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT)
        match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)

        service.review_match(
            reconciliation_match_id=match.id, decision=MatchReviewDecision.REJECT, reviewed_by=USER_ID,
        )
        service.review_match(
            reconciliation_match_id=match.id, decision=MatchReviewDecision.CONFIRM, reviewed_by=USER_ID,
        )

        reviews = db.execute(
            select(MatchReview).where(MatchReview.reconciliation_match_id == match.id)
        ).scalars().all()
        assert len(reviews) == 2
        decisions = {r.decision for r in reviews}
        assert decisions == {MatchReviewDecision.REJECT, MatchReviewDecision.CONFIRM}

    def test_match_not_found_raises(self, service):
        with pytest.raises(MatchNotFoundError):
            service.review_match(
                reconciliation_match_id="does-not-exist",
                decision=MatchReviewDecision.CONFIRM,
                reviewed_by=USER_ID,
            )


class TestDiscrepancyReview:
    def test_acknowledge_recorded(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT)
        match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)
        discrepancy = _make_discrepancy(db, reconciliation_match_id=match.id)

        review = service.review_discrepancy(
            discrepancy_id=discrepancy.id, decision=DiscrepancyReviewDecision.ACKNOWLEDGE,
            reviewed_by=USER_ID,
        )
        assert review.decision == DiscrepancyReviewDecision.ACKNOWLEDGE

    def test_dispute_recorded(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT)
        match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)
        discrepancy = _make_discrepancy(db, reconciliation_match_id=match.id)

        review = service.review_discrepancy(
            discrepancy_id=discrepancy.id, decision=DiscrepancyReviewDecision.DISPUTE,
            reviewed_by=USER_ID, note="Escalating to platform.",
        )
        assert review.decision == DiscrepancyReviewDecision.DISPUTE
        assert review.note == "Escalating to platform."

    def test_discrepancy_not_found_raises(self, service):
        with pytest.raises(DiscrepancyNotFoundError):
            service.review_discrepancy(
                discrepancy_id="does-not-exist",
                decision=DiscrepancyReviewDecision.ACKNOWLEDGE,
                reviewed_by=USER_ID,
            )


class TestManualPairing:
    def test_creates_new_match_and_supersedes_prior_ones(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, external_reference="1001")
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="9999")
        old_pos_match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)
        old_platform_match = _make_match(db, pos_transaction_id=None, platform_transaction_id=platform.id)

        new_match, review = service.create_manual_match(
            analysis_id=ANALYSIS_ID,
            pos_transaction_id=pos.id,
            platform_transaction_id=platform.id,
            reviewed_by=USER_ID,
        )

        assert new_match.pos_transaction_id == pos.id
        assert new_match.platform_transaction_id == platform.id
        assert new_match.confidence_score is None  # a human decided, not the algorithm
        assert review.decision == MatchReviewDecision.MANUALLY_PAIRED
        assert review.reconciliation_match_id == new_match.id

        db.refresh(old_pos_match)
        db.refresh(old_platform_match)
        assert old_pos_match.superseded_at is not None
        assert old_platform_match.superseded_at is not None

    def test_succeeds_when_transactions_never_touched_by_matching(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT)
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT)
        # No ReconciliationMatch rows exist at all for these transactions.

        new_match, _ = service.create_manual_match(
            analysis_id=ANALYSIS_ID,
            pos_transaction_id=pos.id,
            platform_transaction_id=platform.id,
            reviewed_by=USER_ID,
        )
        assert new_match.pos_transaction_id == pos.id

    def test_transaction_not_found_raises(self, service, db):
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT)
        with pytest.raises(TransactionNotFoundError):
            service.create_manual_match(
                analysis_id=ANALYSIS_ID,
                pos_transaction_id="does-not-exist",
                platform_transaction_id=platform.id,
                reviewed_by=USER_ID,
            )

    def test_transaction_not_in_analysis_raises(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, analysis_id=OTHER_ANALYSIS_ID)
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT)
        with pytest.raises(TransactionNotInAnalysisError):
            service.create_manual_match(
                analysis_id=ANALYSIS_ID,
                pos_transaction_id=pos.id,
                platform_transaction_id=platform.id,
                reviewed_by=USER_ID,
            )

    def test_wrong_source_type_raises(self, service, db):
        # Both transactions are POS exports - the second slot must be a
        # platform settlement transaction.
        pos1 = _make_txn(db, source_type=SourceType.POS_EXPORT, external_reference="a")
        pos2 = _make_txn(db, source_type=SourceType.POS_EXPORT, external_reference="b")
        with pytest.raises(WrongSourceTypeError):
            service.create_manual_match(
                analysis_id=ANALYSIS_ID,
                pos_transaction_id=pos1.id,
                platform_transaction_id=pos2.id,
                reviewed_by=USER_ID,
            )

    def test_already_matched_transaction_raises(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, external_reference="1001")
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001")
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)  # already fully matched

        other_platform = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="2002"
        )
        with pytest.raises(TransactionAlreadyMatchedError):
            service.create_manual_match(
                analysis_id=ANALYSIS_ID,
                pos_transaction_id=pos.id,
                platform_transaction_id=other_platform.id,
                reviewed_by=USER_ID,
            )


class TestAuditLogging:
    def test_match_review_logs(self, service, db, audit_logger):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT)
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT)
        match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)

        service.review_match(
            reconciliation_match_id=match.id, decision=MatchReviewDecision.CONFIRM, reviewed_by=USER_ID,
        )
        assert audit_logger.records[-1].event == "match_reviewed"
        assert audit_logger.records[-1].analysis_id == ANALYSIS_ID

    def test_discrepancy_review_logs(self, service, db, audit_logger):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT)
        match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)
        discrepancy = _make_discrepancy(db, reconciliation_match_id=match.id)

        service.review_discrepancy(
            discrepancy_id=discrepancy.id, decision=DiscrepancyReviewDecision.ACKNOWLEDGE,
            reviewed_by=USER_ID,
        )
        assert audit_logger.records[-1].event == "discrepancy_reviewed"

    def test_manual_match_logs(self, service, db, audit_logger):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT)
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT)

        service.create_manual_match(
            analysis_id=ANALYSIS_ID, pos_transaction_id=pos.id, platform_transaction_id=platform.id,
            reviewed_by=USER_ID,
        )
        assert audit_logger.records[-1].event == "manual_match_created"
        assert audit_logger.records[-1].analysis_id == ANALYSIS_ID
