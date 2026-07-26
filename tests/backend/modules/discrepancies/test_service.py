"""
Unit tests for DiscrepancyService.

ReconciliationMatch, Transaction, and ComparisonResult rows are
constructed directly, same testing-boundary rationale as Matching Engine
and Financial Comparison's own tests: this module's contract is against
those three models, not against the full upstream pipeline.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from sqlalchemy import Column, String, Table, create_engine, select
from sqlalchemy.orm import Session

from modules.discrepancies.dependencies import InMemoryAuditLogger
from modules.discrepancies.models import Discrepancy, DiscrepancyCategory, Severity
from modules.discrepancies.service import DiscrepancyService, severity_for
from modules.financial_comparison.models import ComparisonResult
from modules.imports.models import Base, SourceType
from modules.matching.models import ReconciliationMatch
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
    return DiscrepancyService(db=db, audit_logger=audit_logger)


def _day(offset: int = 0) -> datetime:
    return datetime(2026, 1, 15 + offset, 10, 0, 0, tzinfo=dt_timezone.utc)


def _make_txn(
    db: Session, *, source_type: SourceType, amount: Decimal, external_reference: str = "1001"
) -> Transaction:
    txn = Transaction(
        analysis_id=ANALYSIS_ID,
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
    superseded_at=None,
) -> ReconciliationMatch:
    confidence = Decimal("1.00") if pos_transaction_id and platform_transaction_id else None
    match = ReconciliationMatch(
        analysis_id=ANALYSIS_ID,
        pos_transaction_id=pos_transaction_id,
        platform_transaction_id=platform_transaction_id,
        confidence_score=confidence,
        superseded_at=superseded_at,
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


def _make_comparison_result(
    db: Session,
    *,
    reconciliation_match_id: str,
    commission_variance: Decimal = Decimal("0.00"),
    commission_within_tolerance: bool = True,
    settlement_variance: Decimal = Decimal("0.00"),
    settlement_within_tolerance: bool = True,
) -> ComparisonResult:
    result = ComparisonResult(
        analysis_id=ANALYSIS_ID,
        reconciliation_match_id=reconciliation_match_id,
        expected_commission=Decimal("15.00"),
        actual_commission=Decimal("15.00") + commission_variance,
        commission_variance=commission_variance,
        commission_within_tolerance=commission_within_tolerance,
        settlement_variance=settlement_variance,
        settlement_within_tolerance=settlement_within_tolerance,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


def _discrepancies(db: Session) -> list[Discrepancy]:
    return db.execute(
        select(Discrepancy).where(Discrepancy.analysis_id == ANALYSIS_ID)
    ).scalars().all()


class TestSeverityThresholds:
    @pytest.mark.parametrize(
        "amount,expected",
        [
            (Decimal("0.00"), Severity.LOW),
            (Decimal("49.99"), Severity.LOW),
            (Decimal("50.00"), Severity.MEDIUM),
            (Decimal("199.99"), Severity.MEDIUM),
            (Decimal("200.00"), Severity.HIGH),
            (Decimal("999.99"), Severity.HIGH),
            (Decimal("1000.00"), Severity.CRITICAL),
            (Decimal("50000.00"), Severity.CRITICAL),
        ],
    )
    def test_boundaries(self, amount, expected):
        assert severity_for(amount) == expected


class TestMissingSettlement:
    def test_detected_from_pos_only_match(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("30.00"))
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)

        run = service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.total_count == 1
        d = _discrepancies(db)[0]
        assert d.category == DiscrepancyCategory.MISSING_SETTLEMENT
        assert d.estimated_loss == Decimal("30.00")
        assert d.severity == Severity.LOW


class TestUnexpectedSettlement:
    def test_detected_from_platform_only_match(self, service, db):
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT, amount=Decimal("250.00"))
        _make_match(db, pos_transaction_id=None, platform_transaction_id=platform.id)

        run = service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.total_count == 1
        d = _discrepancies(db)[0]
        assert d.category == DiscrepancyCategory.UNEXPECTED_SETTLEMENT
        assert d.estimated_loss == Decimal("250.00")
        assert d.severity == Severity.HIGH


class TestIncorrectCommission:
    def test_detected_from_out_of_tolerance_commission(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("100.00"))
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT, amount=Decimal("100.00"))
        match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)
        _make_comparison_result(
            db,
            reconciliation_match_id=match.id,
            commission_variance=Decimal("-75.00"),
            commission_within_tolerance=False,
        )

        run = service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.total_count == 1
        d = _discrepancies(db)[0]
        assert d.category == DiscrepancyCategory.INCORRECT_COMMISSION
        assert d.estimated_loss == Decimal("75.00")  # abs() applied
        assert d.severity == Severity.MEDIUM


class TestSettlementAmountMismatch:
    def test_detected_from_out_of_tolerance_settlement(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("2000.00"))
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT, amount=Decimal("500.00"))
        match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)
        _make_comparison_result(
            db,
            reconciliation_match_id=match.id,
            settlement_variance=Decimal("-1500.00"),
            settlement_within_tolerance=False,
        )

        run = service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.total_count == 1
        d = _discrepancies(db)[0]
        assert d.category == DiscrepancyCategory.SETTLEMENT_AMOUNT_MISMATCH
        assert d.estimated_loss == Decimal("1500.00")
        assert d.severity == Severity.CRITICAL


class TestBothChecksFailing:
    def test_one_comparison_result_yields_two_discrepancies(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("100.00"))
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT, amount=Decimal("80.00"))
        match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)
        _make_comparison_result(
            db,
            reconciliation_match_id=match.id,
            commission_variance=Decimal("30.00"),
            commission_within_tolerance=False,
            settlement_variance=Decimal("-20.00"),
            settlement_within_tolerance=False,
        )

        run = service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.total_count == 2
        found = _discrepancies(db)
        categories = {d.category for d in found}
        assert categories == {
            DiscrepancyCategory.INCORRECT_COMMISSION,
            DiscrepancyCategory.SETTLEMENT_AMOUNT_MISMATCH,
        }
        # Both trace back to the same match.
        assert all(d.reconciliation_match_id == match.id for d in found)


class TestCleanAnalysis:
    def test_zero_discrepancies_when_everything_is_fine(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("100.00"))
        platform = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT, amount=Decimal("100.00"))
        match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)
        _make_comparison_result(db, reconciliation_match_id=match.id)  # both within tolerance

        run = service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.total_count == 0
        assert run.critical_count == run.high_count == run.medium_count == run.low_count == 0
        assert _discrepancies(db) == []


class TestSupersededMatchesExcluded:
    def test_superseded_match_produces_no_discrepancy(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("30.00"))
        _make_match(
            db, pos_transaction_id=pos.id, platform_transaction_id=None,
            superseded_at=datetime.now(dt_timezone.utc),
        )

        run = service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert run.total_count == 0


class TestRerunSupersedes:
    def test_second_run_replaces_first_runs_discrepancies(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("30.00"))
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)

        service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert len(_discrepancies(db)) == 1


class TestRunSummary:
    def test_counts_are_accurate(self, service, db):
        # LOW
        pos1 = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("10.00"), external_reference="a")
        _make_match(db, pos_transaction_id=pos1.id, platform_transaction_id=None)
        # HIGH
        platform2 = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, amount=Decimal("300.00"), external_reference="b"
        )
        _make_match(db, pos_transaction_id=None, platform_transaction_id=platform2.id)
        # CRITICAL
        pos3 = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("5000.00"), external_reference="c")
        _make_match(db, pos_transaction_id=pos3.id, platform_transaction_id=None)

        run = service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.total_count == 3
        assert run.low_count == 1
        assert run.high_count == 1
        assert run.critical_count == 1
        assert run.medium_count == 0


class TestAuditLogging:
    def test_logs_completion(self, service, db, audit_logger):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("30.00"))
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)

        service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert audit_logger.records[-1].event == "discrepancy_run_completed"
        assert audit_logger.records[-1].analysis_id == ANALYSIS_ID
        assert audit_logger.records[-1].metadata["total_count"] == 1
