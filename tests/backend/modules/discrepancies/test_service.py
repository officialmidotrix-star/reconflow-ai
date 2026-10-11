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
from modules.discrepancies.exceptions import NotACancellationError
from modules.discrepancies.models import (
    CancelledBy, Direction, Discrepancy, DiscrepancyCategory, Severity, Treatment,
)
from modules.discrepancies.service import (
    DiscrepancyService, StatusClass, classify_order_status, net_of_commission, severity_for,
)
from modules.financial_comparison.models import ComparisonResult
from modules.imports.models import Base, SourceType
from modules.matching.models import ReconciliationMatch
from modules.normalization.models import Transaction, TransactionOrderStatus

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


class FakeRates:
    """Stands in for the contract lookup. `rate=None` = no / ambiguous contract."""

    def __init__(self, rate: Decimal | None) -> None:
        self.rate = rate
        self.calls = 0

    def get_commission_rate(self, *, analysis_id: str, as_of):
        self.calls += 1
        return self.rate


@pytest.fixture()
def service(db, audit_logger):
    """No contract lookup at all: nothing can be quantified net of commission."""
    return DiscrepancyService(db=db, audit_logger=audit_logger)


@pytest.fixture()
def rates():
    return FakeRates(Decimal("0.15"))


@pytest.fixture()
def priced(db, audit_logger, rates):
    """A service that knows the commission rate: 15%."""
    return DiscrepancyService(db=db, audit_logger=audit_logger, contract_lookup=rates)


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


class TestCancelledAfterPreparation:
    def test_reclassified_when_status_indicates_cancellation(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("30.00"))
        db.add(TransactionOrderStatus(transaction_id=pos.id, raw_status="Cancelled by customer"))
        db.commit()
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)

        run = service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.total_count == 1
        d = _discrepancies(db)[0]
        assert d.category == DiscrepancyCategory.CANCELLED_AFTER_PREPARATION
        assert d.estimated_loss == Decimal("30.00")  # still a real loss, same amount either way

    @pytest.mark.parametrize(
        "raw_status",
        ["CANCELLED", "Canceled", "  cancel  ", "Cancelled - refunded", "cancelled_by_customer", "ملغي", "طلب ملغى"],
    )
    def test_recognizes_several_real_world_spellings(self, service, db, raw_status):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("30.00"))
        db.add(TransactionOrderStatus(transaction_id=pos.id, raw_status=raw_status))
        db.commit()
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)

        service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert _discrepancies(db)[0].category == DiscrepancyCategory.CANCELLED_AFTER_PREPARATION

    def test_unrecognized_status_value_stays_missing_settlement(self, service, db):
        # A status column with a value that doesn't indicate cancellation
        # (e.g. "PAID", "COMPLETED") shouldn't reclassify anything - only
        # actual cancellation/refund indicators should.
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("30.00"))
        db.add(TransactionOrderStatus(transaction_id=pos.id, raw_status="COMPLETED"))
        db.commit()
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)

        service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert _discrepancies(db)[0].category == DiscrepancyCategory.MISSING_SETTLEMENT

    def test_no_status_row_stays_missing_settlement(self, service, db):
        # The default case (no order_status column in the uploaded file
        # at all) - today's exact prior behavior, unchanged.
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("30.00"))
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)

        service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert _discrepancies(db)[0].category == DiscrepancyCategory.MISSING_SETTLEMENT


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


class TestStatusClassification:
    """Whole words, never substrings - and a refund/void is NOT a cancellation after preparation."""

    @pytest.mark.parametrize("raw,expected", [
        ("Cancelled", StatusClass.CANCELLED),
        ("CANCELED", StatusClass.CANCELLED),
        ("Cancelled by customer", StatusClass.CANCELLED),
        ("CANCELLED_BY_CUSTOMER", StatusClass.CANCELLED),
        ("order-voided", StatusClass.REFUNDED_OR_VOID),
        ("cancelled and refunded", StatusClass.CANCELLED),   # a cancel word wins; a human decides who cancelled
        ("ملغي", StatusClass.CANCELLED),
        ("Refunded", StatusClass.REFUNDED_OR_VOID),
        ("voided by platform", StatusClass.REFUNDED_OR_VOID),
        ("Reversal", StatusClass.REFUNDED_OR_VOID),
        ("COMPLETED", StatusClass.OTHER),
        ("Paid", StatusClass.OTHER),
        ("", StatusClass.OTHER),
        ("Cancellation fee waived", StatusClass.OTHER),      # "cancellation" is not "cancelled"
        ("Avoided", StatusClass.OTHER),                      # contains "void" but is not the word
        ("uncancelled", StatusClass.OTHER),
    ])
    def test_whole_words_only(self, raw, expected):
        assert classify_order_status(raw) == expected

    def test_a_refunded_order_without_settlement_is_excluded_and_counted_not_claimed(self, service, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("30.00"))
        db.add(TransactionOrderStatus(transaction_id=pos.id, raw_status="Refunded")); db.commit()
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)

        run = service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert run.total_count == 0 and _discrepancies(db) == []
        assert run.excluded_refund_void_count == 1  # visible, not silently dropped


class TestNetOfCommission:
    def test_rounds_half_up_to_cents(self):
        assert net_of_commission(Decimal("100.00"), Decimal("0.15")) == Decimal("85.00")
        assert net_of_commission(Decimal("60.25"), Decimal("0.15")) == Decimal("51.21")  # 51.2125
        assert net_of_commission(Decimal("10.00"), Decimal("0.125")) == Decimal("8.75")
        assert net_of_commission(Decimal("0.10"), Decimal("0.25")) == Decimal("0.08")    # 0.075 -> up


class TestHeadlineTreatment:
    def _missing(self, db, amount="100.00", ref="1001"):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal(amount), external_reference=ref)
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)
        return pos

    def test_missing_settlement_is_claimed_net_of_commission(self, priced, db):
        self._missing(db, "100.00")
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        assert (d.treatment, d.direction) == (Treatment.RECOVERABLE, Direction.UNDERPAID)
        assert d.estimated_loss == Decimal("100.00")        # the gross order value, as before
        assert d.claimable_amount == Decimal("85.00")       # what the platform would actually have paid
        assert d.order_reference == "1001"

    def test_no_contract_means_needs_contract_never_a_guess(self, db, audit_logger):
        svc = DiscrepancyService(db=db, audit_logger=audit_logger, contract_lookup=FakeRates(None))
        self._missing(db)
        svc.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        assert (d.treatment, d.claimable_amount) == (Treatment.NEEDS_CONTRACT, None)
        assert d.estimated_loss == Decimal("100.00")

    def test_without_any_lookup_it_is_also_needs_contract(self, service, db):
        self._missing(db)
        service.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert _discrepancies(db)[0].treatment == Treatment.NEEDS_CONTRACT

    def test_a_zero_or_negative_amount_is_never_claimed(self, priced, db):
        self._missing(db, "-20.00")
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        assert (d.treatment, d.claimable_amount) == (Treatment.TO_VERIFY, None)

    def test_unexpected_settlement_is_never_a_loss(self, priced, db):
        plat = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT, amount=Decimal("55.00"), external_reference="9")
        _make_match(db, pos_transaction_id=None, platform_transaction_id=plat.id)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        assert d.category == DiscrepancyCategory.UNEXPECTED_SETTLEMENT
        assert (d.treatment, d.direction, d.claimable_amount) == (Treatment.TO_VERIFY, Direction.NOT_APPLICABLE, None)
        assert d.estimated_loss == Decimal("55.00")

    def test_the_rate_is_looked_up_once_per_day_not_once_per_order(self, priced, db, rates):
        for i in range(25):
            self._missing(db, "10.00", ref=f"R{i}")
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert rates.calls == 1  # all on the same day
        assert all(d.claimable_amount == Decimal("8.50") for d in _discrepancies(db))


class TestMismatchDirection:
    def _pair(self, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("100.00"))
        plat = _make_txn(db, source_type=SourceType.PLATFORM_SETTLEMENT, amount=Decimal("100.00"))
        return _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=plat.id)

    def test_underpayment_is_recoverable_net_of_commission(self, priced, db):
        m = self._pair(db)
        _make_comparison_result(db, reconciliation_match_id=m.id, settlement_variance=Decimal("-10.00"),
                                settlement_within_tolerance=False)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        assert (d.category, d.treatment, d.direction) == (
            DiscrepancyCategory.SETTLEMENT_AMOUNT_MISMATCH, Treatment.RECOVERABLE, Direction.UNDERPAID)
        assert d.estimated_loss == Decimal("10.00") and d.claimable_amount == Decimal("8.50")

    def test_an_overpayment_is_not_a_loss(self, priced, db):
        m = self._pair(db)
        _make_comparison_result(db, reconciliation_match_id=m.id, settlement_variance=Decimal("3.00"),
                                settlement_within_tolerance=False)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        assert (d.treatment, d.direction, d.claimable_amount) == (Treatment.TO_VERIFY, Direction.OVERPAID, None)

    def test_commission_charged_above_the_contract_is_recoverable_as_is(self, priced, db):
        m = self._pair(db)
        _make_comparison_result(db, reconciliation_match_id=m.id, commission_variance=Decimal("4.00"),
                                commission_within_tolerance=False)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        assert (d.category, d.treatment, d.claimable_amount) == (
            DiscrepancyCategory.INCORRECT_COMMISSION, Treatment.RECOVERABLE, Decimal("4.00"))

    def test_commission_charged_below_the_contract_is_in_the_restaurants_favour(self, priced, db):
        m = self._pair(db)
        _make_comparison_result(db, reconciliation_match_id=m.id, commission_variance=Decimal("-2.50"),
                                commission_within_tolerance=False)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        assert (d.treatment, d.direction, d.claimable_amount) == (Treatment.TO_VERIFY, Direction.OVERPAID, None)

    def test_underpayment_without_a_rate_needs_a_contract(self, db, audit_logger):
        svc = DiscrepancyService(db=db, audit_logger=audit_logger, contract_lookup=FakeRates(None))
        m = self._pair(db)
        _make_comparison_result(db, reconciliation_match_id=m.id, settlement_variance=Decimal("-10.00"),
                                settlement_within_tolerance=False)
        svc.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert _discrepancies(db)[0].treatment == Treatment.NEEDS_CONTRACT

    def test_a_gross_shortfall_and_its_commission_effect_are_not_double_counted(self, priced, db):
        """Platform records 10 less gross AND (correctly) 1.50 less commission:
        the loss is 10 - 1.50 = 8.50 (the mismatch claim); the lower commission
        is in the restaurant's favour and must not add to or subtract from it."""
        m = self._pair(db)
        _make_comparison_result(db, reconciliation_match_id=m.id,
                                settlement_variance=Decimal("-10.00"), settlement_within_tolerance=False,
                                commission_variance=Decimal("-1.50"), commission_within_tolerance=False)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        by_cat = {d.category: d for d in _discrepancies(db)}
        assert by_cat[DiscrepancyCategory.SETTLEMENT_AMOUNT_MISMATCH].claimable_amount == Decimal("8.50")
        assert by_cat[DiscrepancyCategory.INCORRECT_COMMISSION].claimable_amount is None
        total = sum((d.claimable_amount or Decimal(0)) for d in by_cat.values())
        assert total == Decimal("8.50")


class TestCancellationConfirmation:
    def _cancelled(self, db, amount="100.00", ref="2001"):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal(amount), external_reference=ref)
        db.add(TransactionOrderStatus(transaction_id=pos.id, raw_status="Cancelled by customer")); db.commit()
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)

    def test_starts_as_needs_confirmation_and_is_not_in_any_claim(self, priced, db):
        self._cancelled(db)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        assert d.category == DiscrepancyCategory.CANCELLED_AFTER_PREPARATION
        assert (d.treatment, d.claimable_amount) == (Treatment.NEEDS_CONFIRMATION, None)

    @pytest.mark.parametrize("who", [CancelledBy.PLATFORM, CancelledBy.CUSTOMER])
    def test_platform_or_customer_cancellation_becomes_claimable_net(self, priced, db, who):
        self._cancelled(db)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        out = priced.confirm_cancellation(discrepancy_id=d.id, cancelled_by=who, requested_by=USER_ID)
        assert (out.treatment, out.claimable_amount) == (Treatment.RECOVERABLE, Decimal("85.00"))

    def test_the_restaurants_own_cancellation_is_never_claimable(self, priced, db):
        self._cancelled(db)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        out = priced.confirm_cancellation(discrepancy_id=d.id, cancelled_by=CancelledBy.RESTAURANT, requested_by=USER_ID)
        assert (out.treatment, out.claimable_amount) == (Treatment.NOT_CLAIMABLE, None)

    def test_the_answer_can_be_changed(self, priced, db):
        self._cancelled(db)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        d = _discrepancies(db)[0]
        priced.confirm_cancellation(discrepancy_id=d.id, cancelled_by=CancelledBy.PLATFORM, requested_by=USER_ID)
        out = priced.confirm_cancellation(discrepancy_id=d.id, cancelled_by=CancelledBy.RESTAURANT, requested_by=USER_ID)
        assert out.treatment == Treatment.NOT_CLAIMABLE

    def test_confirmation_survives_running_the_analysis_again(self, priced, db):
        self._cancelled(db)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        priced.confirm_cancellation(discrepancy_id=_discrepancies(db)[0].id, cancelled_by=CancelledBy.PLATFORM, requested_by=USER_ID)

        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)  # a re-run replaces the rows

        d = _discrepancies(db)[0]
        assert (d.treatment, d.claimable_amount) == (Treatment.RECOVERABLE, Decimal("85.00"))

    def test_confirming_without_a_rate_needs_a_contract(self, db, audit_logger):
        svc = DiscrepancyService(db=db, audit_logger=audit_logger, contract_lookup=FakeRates(None))
        self._cancelled(db)
        svc.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        out = svc.confirm_cancellation(discrepancy_id=_discrepancies(db)[0].id, cancelled_by=CancelledBy.PLATFORM, requested_by=USER_ID)
        assert (out.treatment, out.claimable_amount) == (Treatment.NEEDS_CONTRACT, None)

    def test_only_cancellations_can_be_confirmed(self, priced, db):
        pos = _make_txn(db, source_type=SourceType.POS_EXPORT, amount=Decimal("50.00"))
        _make_match(db, pos_transaction_id=pos.id, platform_transaction_id=None)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        with pytest.raises(NotACancellationError):
            priced.confirm_cancellation(discrepancy_id=_discrepancies(db)[0].id, cancelled_by=CancelledBy.PLATFORM, requested_by=USER_ID)

    def test_it_is_audited(self, priced, db, audit_logger):
        self._cancelled(db)
        priced.detect_discrepancies(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        priced.confirm_cancellation(discrepancy_id=_discrepancies(db)[0].id, cancelled_by=CancelledBy.CUSTOMER, requested_by=USER_ID)
        rec = [r for r in audit_logger.records if r.event == "cancellation_confirmed"]
        assert len(rec) == 1 and rec[0].metadata["cancelled_by"] == "CUSTOMER"
