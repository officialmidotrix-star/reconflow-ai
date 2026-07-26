"""
Unit tests for MatchingService.

Transaction rows are constructed directly rather than via the full
Import -> Validate -> Normalize pipeline: Normalization's own tests
already cover that it produces correct Transaction rows, and Matching's
contract is specifically against the Transaction model, so testing
against directly-constructed rows is the right isolation boundary.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from sqlalchemy import Column, String, Table, create_engine, select
from sqlalchemy.orm import Session

from modules.imports.models import Base, SourceType
from modules.matching.dependencies import InMemoryAuditLogger
from modules.matching.exceptions import InsufficientTransactionsError
from modules.matching.models import MatchingStatus, ReconciliationMatch
from modules.matching.service import MatchingService
from modules.normalization.models import Transaction

ANALYSIS_ID = "analysis-1"
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
    return MatchingService(db=db, audit_logger=audit_logger)


def _day(offset: int = 0, hour: int = 10) -> datetime:
    return datetime(2026, 1, 15 + offset, hour, 0, 0, tzinfo=dt_timezone.utc)


def _make_txn(
    db: Session,
    *,
    source_type: SourceType,
    external_reference: str,
    occurred_at: datetime,
    amount: Decimal,
    currency_code: str = "SAR",
    analysis_id: str = ANALYSIS_ID,
) -> Transaction:
    txn = Transaction(
        analysis_id=analysis_id,
        uploaded_file_id=f"uploaded-file-{source_type.value}",
        source_type=source_type,
        external_reference=external_reference,
        occurred_at=occurred_at,
        amount=amount,
        currency_code=currency_code,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _matches_for(db: Session, analysis_id: str = ANALYSIS_ID) -> list[ReconciliationMatch]:
    """Active matches only - the current state, mirroring what every real
    caller (Financial Comparison, Discrepancy Detection) filters to."""
    return db.execute(
        select(ReconciliationMatch).where(
            ReconciliationMatch.analysis_id == analysis_id,
            ReconciliationMatch.superseded_at.is_(None),
        )
    ).scalars().all()


def _all_matches_including_superseded(
    db: Session, analysis_id: str = ANALYSIS_ID
) -> list[ReconciliationMatch]:
    return db.execute(
        select(ReconciliationMatch).where(ReconciliationMatch.analysis_id == analysis_id)
    ).scalars().all()


class TestExactReferenceMatch:
    def test_scores_full_confidence(self, service, db):
        pos = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("42.50"),
        )
        platform = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("42.50"),
        )
        run = service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.matched_count == 1
        match = _matches_for(db)[0]
        assert match.pos_transaction_id == pos.id
        assert match.platform_transaction_id == platform.id
        assert match.confidence_score == Decimal("1.00")

    def test_case_and_whitespace_insensitive(self, service, db):
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="ORD1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference=" ord1001 ",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        run = service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert run.matched_count == 1


class TestDuplicateReferenceTieBreak:
    def test_closest_date_wins(self, service, db):
        pos = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(hour=12), amount=Decimal("10.00"),
        )
        far = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(hour=8), amount=Decimal("10.00"),  # 4h away
        )
        close = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(hour=12, offset=0), amount=Decimal("10.00"),  # exact
        )
        service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        match = next(m for m in _matches_for(db) if m.pos_transaction_id == pos.id)
        assert match.platform_transaction_id == close.id
        assert match.platform_transaction_id != far.id


class TestAmountDateFallback:
    def test_confidence_decays_with_distance(self, service, db):
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(0), amount=Decimal("50.00"),
        )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="2002",
            occurred_at=_day(3), amount=Decimal("50.00"),  # different ref, 3 days later
        )
        run = service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.matched_count == 1
        match = _matches_for(db)[0]
        assert match.confidence_score == Decimal("0.55")  # 0.70 - 0.05*3

    def test_no_match_beyond_window(self, service, db):
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(0), amount=Decimal("50.00"),
        )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="2002",
            occurred_at=_day(8), amount=Decimal("50.00"),  # 8 days later, window is 7
        )
        run = service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert run.matched_count == 0
        assert run.unmatched_pos_count == 1
        assert run.unmatched_platform_count == 1

    def test_no_match_on_amount_mismatch(self, service, db):
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(0), amount=Decimal("50.00"),
        )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="2002",
            occurred_at=_day(0), amount=Decimal("51.00"),
        )
        run = service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert run.matched_count == 0


class TestUnmatchedRecording:
    def test_pos_without_platform_recorded(self, service, db):
        matched_pos = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        orphan_pos = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="9999",
            occurred_at=_day(), amount=Decimal("999.00"),
        )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        run = service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.unmatched_pos_count == 1
        unmatched = next(m for m in _matches_for(db) if m.pos_transaction_id == orphan_pos.id)
        assert unmatched.platform_transaction_id is None
        assert unmatched.confidence_score is None

    def test_platform_without_pos_recorded(self, service, db):
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        orphan_platform = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="8888",
            occurred_at=_day(), amount=Decimal("888.00"),
        )
        run = service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.unmatched_platform_count == 1
        unmatched = next(
            m for m in _matches_for(db) if m.platform_transaction_id == orphan_platform.id
        )
        assert unmatched.pos_transaction_id is None


class TestNoDoubleClaiming:
    def test_each_transaction_claimed_at_most_once(self, service, db):
        # Two POS transactions with identical amount/date contending for one
        # platform transaction via the fallback pass - only one may win it.
        pos_a = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(hour=9), amount=Decimal("20.00"),
        )
        pos_b = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1002",
            occurred_at=_day(hour=11), amount=Decimal("20.00"),
        )
        platform = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="9999",
            occurred_at=_day(hour=10), amount=Decimal("20.00"),
        )
        run = service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        matches = _matches_for(db)
        claims_on_platform = [m for m in matches if m.platform_transaction_id == platform.id]
        assert len(claims_on_platform) == 1
        assert run.matched_count == 1
        assert run.unmatched_pos_count == 1
        winner_id = claims_on_platform[0].pos_transaction_id
        assert winner_id in (pos_a.id, pos_b.id)


class TestRerunSupersedes:
    def test_second_run_replaces_first_runs_matches(self, service, db):
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert len(_matches_for(db)) == 1  # only one *active* match
        assert len(_all_matches_including_superseded(db)) == 2  # history preserved, not deleted

    def test_first_runs_match_is_marked_superseded(self, service, db):
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        all_matches = sorted(_all_matches_including_superseded(db), key=lambda m: m.created_at)
        assert all_matches[0].superseded_at is not None
        assert all_matches[1].superseded_at is None


class TestPrecondition:
    def test_fails_with_no_pos_transactions(self, service, db):
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        with pytest.raises(InsufficientTransactionsError):
            service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

    def test_fails_with_no_platform_transactions(self, service, db):
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        with pytest.raises(InsufficientTransactionsError):
            service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)


class TestRunSummary:
    def test_counts_are_accurate(self, service, db):
        _make_txn(  # matches exactly
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        _make_txn(  # unmatched pos
            db, source_type=SourceType.POS_EXPORT, external_reference="2002",
            occurred_at=_day(), amount=Decimal("77.00"),
        )
        _make_txn(  # unmatched platform
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="3003",
            occurred_at=_day(), amount=Decimal("88.00"),
        )
        run = service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.status == MatchingStatus.COMPLETED
        assert run.matched_count == 1
        assert run.unmatched_pos_count == 1
        assert run.unmatched_platform_count == 1


class TestAuditLogging:
    def test_logs_completion(self, service, db, audit_logger):
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("10.00"),
        )
        service.run_matching(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert audit_logger.records[-1].event == "matching_run_completed"
        assert audit_logger.records[-1].analysis_id == ANALYSIS_ID
        assert audit_logger.records[-1].metadata["matched_count"] == 1
