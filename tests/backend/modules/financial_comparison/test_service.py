"""
Unit tests for ComparisonService.

Transaction and ReconciliationMatch rows are constructed directly, same
testing-boundary rationale as Matching Engine's own tests: Financial
Comparison's contract is against those two models, not against the full
upstream pipeline, so exercising it that way is the right isolation level.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from sqlalchemy import Column, String, Table, create_engine, select
from sqlalchemy.orm import Session

from modules.financial_comparison.dependencies import InMemoryAuditLogger, InMemoryContractLookup
from modules.financial_comparison.exceptions import ComparisonInternalError
from modules.financial_comparison.models import ComparisonResult, ComparisonStatus
from modules.financial_comparison.service import ComparisonService
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
def contract_lookup():
    return InMemoryContractLookup()


@pytest.fixture()
def audit_logger():
    return InMemoryAuditLogger()


@pytest.fixture()
def service(db, contract_lookup, audit_logger):
    return ComparisonService(db=db, contract_lookup=contract_lookup, audit_logger=audit_logger)


def _day(offset: int = 0) -> datetime:
    return datetime(2026, 1, 15 + offset, 10, 0, 0, tzinfo=dt_timezone.utc)


def _make_txn(
    db: Session,
    *,
    source_type: SourceType,
    external_reference: str,
    occurred_at: datetime,
    amount: Decimal,
    currency_code: str = "SAR",
    platform_commission_amount: Decimal | None = None,
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
        platform_commission_amount=platform_commission_amount,
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
    confidence_score: Decimal | None = Decimal("1.00"),
    analysis_id: str = ANALYSIS_ID,
    superseded_at=None,
) -> ReconciliationMatch:
    match = ReconciliationMatch(
        analysis_id=analysis_id,
        pos_transaction_id=pos_transaction_id,
        platform_transaction_id=platform_transaction_id,
        confidence_score=confidence_score,
        superseded_at=superseded_at,
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


def _make_pair(db, *, pos_amount, commission_amount, rate, day_offset=0):
    """Convenience: a fully-matched pos/platform pair plus a registered
    contract at the given rate, returning the ReconciliationMatch."""
    pos = _make_txn(
        db, source_type=SourceType.POS_EXPORT, external_reference="1001",
        occurred_at=_day(day_offset), amount=pos_amount,
    )
    platform = _make_txn(
        db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
        occurred_at=_day(day_offset), amount=pos_amount,
        platform_commission_amount=commission_amount,
    )
    match = _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)
    return match, pos, platform


class TestCommissionMath:
    def test_correct_commission_is_within_tolerance(self, service, db, contract_lookup):
        contract_lookup.register(ANALYSIS_ID, Decimal("0.15"), valid_from=_day(-1))
        _make_pair(db, pos_amount=Decimal("100.00"), commission_amount=Decimal("15.00"), rate=Decimal("0.15"))

        run = service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert run.compared_count == 1
        assert run.within_tolerance_count == 1

        result = db.execute(select(ComparisonResult)).scalars().first()
        assert result.expected_commission == Decimal("15.00")
        assert result.actual_commission == Decimal("15.00")
        assert result.commission_variance == Decimal("0.00")
        assert result.commission_within_tolerance is True


class TestToleranceBoundary:
    def test_exactly_at_boundary_is_within_tolerance(self, service, db, contract_lookup):
        # expected commission 200.00 -> tolerance = max(2.00, 1.00) = 2.00
        contract_lookup.register(ANALYSIS_ID, Decimal("0.20"), valid_from=_day(-1))
        _make_pair(db, pos_amount=Decimal("1000.00"), commission_amount=Decimal("202.00"), rate=Decimal("0.20"))

        run = service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        result = db.execute(select(ComparisonResult)).scalars().first()
        assert result.commission_variance == Decimal("2.00")
        assert result.commission_within_tolerance is True
        assert run.within_tolerance_count == 1

    def test_just_outside_boundary_is_out_of_tolerance(self, service, db, contract_lookup):
        contract_lookup.register(ANALYSIS_ID, Decimal("0.20"), valid_from=_day(-1))
        _make_pair(db, pos_amount=Decimal("1000.00"), commission_amount=Decimal("202.01"), rate=Decimal("0.20"))

        run = service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        result = db.execute(select(ComparisonResult)).scalars().first()
        assert result.commission_within_tolerance is False
        assert run.out_of_tolerance_count == 1


class TestSettlementVariance:
    def test_settlement_mismatch_computed_and_flagged(self, service, db, contract_lookup):
        contract_lookup.register(ANALYSIS_ID, Decimal("0.15"), valid_from=_day(-1))
        pos = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("100.00"),
        )
        platform = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("90.00"),  # 10 short of the POS amount
            platform_commission_amount=Decimal("15.00"),
        )
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)

        service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        result = db.execute(select(ComparisonResult)).scalars().first()
        assert result.settlement_variance == Decimal("-10.00")
        assert result.settlement_within_tolerance is False


class TestOneSidedMatchesSkipped:
    def test_pos_only_match_not_compared(self, service, db, contract_lookup):
        contract_lookup.register(ANALYSIS_ID, Decimal("0.15"), valid_from=_day(-1))
        pos = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("100.00"),
        )
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None, confidence_score=None)

        run = service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert run.compared_count == 0
        assert run.status == ComparisonStatus.COMPLETED  # skipped, not failed


class TestNoContractSkipped:
    def test_missing_contract_skipped_not_failed(self, service, db, contract_lookup):
        # No contract registered at all.
        _make_pair(db, pos_amount=Decimal("100.00"), commission_amount=Decimal("15.00"), rate=Decimal("0.15"))

        run = service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert run.compared_count == 0
        assert run.skipped_no_contract_count == 1
        assert run.status == ComparisonStatus.COMPLETED

    def test_no_contract_pair_does_not_block_others(self, service, db, contract_lookup):
        contract_lookup.register(ANALYSIS_ID, Decimal("0.15"), valid_from=_day(-1))

        # Pair with a contract - should be compared.
        _make_pair(db, pos_amount=Decimal("100.00"), commission_amount=Decimal("15.00"), rate=Decimal("0.15"))

        # A second, separate pair predating the contract's valid_from -
        # falls outside the registered window, so no contract applies.
        pos2 = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="2002",
            occurred_at=_day(-5), amount=Decimal("50.00"),
        )
        platform2 = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="2002",
            occurred_at=_day(-5), amount=Decimal("50.00"), platform_commission_amount=Decimal("7.50"),
        )
        _make_match(db, pos_transaction_id=pos2.id, platform_transaction_id=platform2.id)

        run = service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert run.compared_count == 1
        assert run.skipped_no_contract_count == 1


class TestSupersededMatchesExcluded:
    def test_superseded_match_is_not_compared(self, service, db, contract_lookup):
        contract_lookup.register(ANALYSIS_ID, Decimal("0.15"), valid_from=_day(-1))
        pos = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("100.00"),
        )
        platform = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("100.00"), platform_commission_amount=Decimal("15.00"),
        )
        _make_match(
            db, pos_transaction_id=pos.id, platform_transaction_id=platform.id,
            superseded_at=datetime.now(dt_timezone.utc),
        )

        run = service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert run.compared_count == 0  # the only match is superseded, so nothing to compare


class TestRerunSupersedes:
    def test_second_run_replaces_first_runs_results(self, service, db, contract_lookup):
        contract_lookup.register(ANALYSIS_ID, Decimal("0.15"), valid_from=_day(-1))
        _make_pair(db, pos_amount=Decimal("100.00"), commission_amount=Decimal("15.00"), rate=Decimal("0.15"))

        service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert len(db.execute(select(ComparisonResult)).scalars().all()) == 1


class TestRunSummary:
    def test_counts_are_accurate(self, service, db, contract_lookup):
        contract_lookup.register(ANALYSIS_ID, Decimal("0.15"), valid_from=_day(-1))

        # Within tolerance.
        _make_pair(db, pos_amount=Decimal("100.00"), commission_amount=Decimal("15.00"), rate=Decimal("0.15"))

        # Out of tolerance - wrong commission by far more than 1%/1.00 floor.
        pos2 = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="2002",
            occurred_at=_day(), amount=Decimal("100.00"),
        )
        platform2 = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="2002",
            occurred_at=_day(), amount=Decimal("100.00"), platform_commission_amount=Decimal("25.00"),
        )
        _make_match(db, pos_transaction_id=pos2.id, platform_transaction_id=platform2.id)

        run = service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert run.compared_count == 2
        assert run.within_tolerance_count == 1
        assert run.out_of_tolerance_count == 1


class TestAuditLogging:
    def test_logs_completion(self, service, db, contract_lookup, audit_logger):
        contract_lookup.register(ANALYSIS_ID, Decimal("0.15"), valid_from=_day(-1))
        _make_pair(db, pos_amount=Decimal("100.00"), commission_amount=Decimal("15.00"), rate=Decimal("0.15"))

        service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert audit_logger.records[-1].event == "comparison_run_completed"
        assert audit_logger.records[-1].analysis_id == ANALYSIS_ID
        assert audit_logger.records[-1].metadata["compared_count"] == 1


class TestInternalConsistencyGuard:
    def test_raises_if_commission_amount_missing_on_platform_transaction(
        self, service, db, contract_lookup
    ):
        contract_lookup.register(ANALYSIS_ID, Decimal("0.15"), valid_from=_day(-1))
        pos = _make_txn(
            db, source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("100.00"),
        )
        # Simulates an inconsistent state that shouldn't occur given
        # Normalization's guarantee - constructed directly since this test
        # is specifically about defending against that guarantee failing.
        platform = _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=_day(), amount=Decimal("100.00"), platform_commission_amount=None,
        )
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=platform.id)

        with pytest.raises(ComparisonInternalError):
            service.run_comparison(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
