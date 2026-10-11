"""
Unit tests for AnalysisResultsService.

Seeds Transaction, ReconciliationMatch, MatchingRun, Discrepancy, and
AIInsight rows directly, same testing-boundary rationale as Discrepancy
Detection's own tests: this module's contract is against those five
already-persisted shapes, not against the full upstream pipeline.
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from modules.ai_insights.models import AIInsight
from modules.analysis_orchestration.models import Analysis, AnalysisStatus
from modules.analysis_results.exceptions import AnalysisNotFoundError
from modules.analysis_results.service import AnalysisResultsService
from modules.discrepancies.models import (
    Direction, Discrepancy, DiscrepancyCategory, DiscrepancyRun, DiscrepancyStatus, Severity, Treatment,
)
from modules.financial_comparison.models import ComparisonRun, ComparisonStatus
from modules.identity_access.models import Base, User  # noqa: F401 - registers "users"
from modules.imports.models import SourceType, UploadedFile  # noqa: F401 - registers "uploaded_files"
from modules.lead_capture.models import AnalysisLead
from modules.matching.models import MatchingRun, MatchingStatus, ReconciliationMatch
from modules.normalization.models import Transaction
from modules.organizations.models import Branch, Organization

ANALYSIS_ID = "analysis-1"
BRANCH_ID = "branch-1"
ORG_ID = "org-1"
USER_ID = "user-1"


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Organization(id=ORG_ID, legal_name="Test Restaurant Group", default_currency="SAR"))
        session.add(Branch(id=BRANCH_ID, organization_id=ORG_ID, name="Test Branch", timezone="Asia/Riyadh"))
        session.commit()
        yield session


@pytest.fixture()
def service(db):
    return AnalysisResultsService(db=db)


def _seed_analysis(db: Session, *, status: AnalysisStatus = AnalysisStatus.COMPLETED) -> None:
    db.add(
        Analysis(
            id=ANALYSIS_ID, branch_id=BRANCH_ID, created_by=USER_ID, version=1, status=status,
            period_start=date(2026, 7, 15), period_end=date(2026, 7, 18),
        )
    )
    db.commit()


def _make_txn(
    db: Session, *, source_type: SourceType, amount: Decimal, external_reference: str,
    currency_code: str = "SAR",
) -> Transaction:
    txn = Transaction(
        analysis_id=ANALYSIS_ID, uploaded_file_id=f"uploaded-file-{source_type.value}",
        source_type=source_type, external_reference=external_reference,
        occurred_at=datetime(2026, 7, 15, 10, 0, 0, tzinfo=dt_timezone.utc),
        amount=amount, currency_code=currency_code,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


class TestCurrencyResolution:
    def test_reads_currency_from_actual_transactions_not_just_branch_default(self, db, service):
        # Org default is SAR (see _seed_analysis / Organization fixture),
        # but the file specified USD for this row - summary should
        # reflect what the data actually says, not the independent
        # branch/org lookup that used to be the only source.
        _seed_analysis(db)
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, amount=Decimal("100.00"),
            external_reference="ORD-1", currency_code="USD",
        )

        summary = service.get_summary(ANALYSIS_ID)

        assert summary.currency == "USD"
        assert summary.has_mixed_currencies is False

    def test_falls_back_to_branch_org_default_before_pipeline_has_run(self, db, service):
        # No transactions exist yet - nothing to read currency from, so
        # this falls back to the branch/org default rather than showing
        # nothing on a page that hasn't processed anything yet.
        _seed_analysis(db, status=AnalysisStatus.AWAITING_FILES)

        summary = service.get_summary(ANALYSIS_ID)

        assert summary.currency == "SAR"  # the org default from the fixture
        assert summary.has_mixed_currencies is False

    def test_detects_genuinely_mixed_currencies(self, db, service):
        _seed_analysis(db)
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, amount=Decimal("100.00"),
            external_reference="ORD-1", currency_code="SAR",
        )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, amount=Decimal("50.00"),
            external_reference="ORD-2", currency_code="USD",
        )

        summary = service.get_summary(ANALYSIS_ID)

        assert summary.has_mixed_currencies is True
        assert summary.currency == "SAR"  # the org default, present in the data - deterministic now

    def test_org_default_wins_even_when_its_a_small_minority(self, db, service):
        # The exact scenario reported live: 10 DZD orders against 105
        # split between SAR/USD, org default SAR. Alphabetically DZD
        # sorts first ('D' < 'S' < 'U') and would win under a naive
        # order-by-name pick despite being the smallest slice by far.
        _seed_analysis(db)  # org default is SAR
        for i in range(10):
            _make_txn(
                db, source_type=SourceType.POS_EXPORT, amount=Decimal("10.00"),
                external_reference=f"DZD-{i}", currency_code="DZD",
            )
        for i in range(60):
            _make_txn(
                db, source_type=SourceType.POS_EXPORT, amount=Decimal("10.00"),
                external_reference=f"USD-{i}", currency_code="USD",
            )
        for i in range(45):
            _make_txn(
                db, source_type=SourceType.POS_EXPORT, amount=Decimal("10.00"),
                external_reference=f"SAR-{i}", currency_code="SAR",
            )

        summary = service.get_summary(ANALYSIS_ID)

        # SAR wins because it's the org default and present - even though
        # USD (60 orders) is more frequent than SAR (45 orders) here.
        assert summary.currency == "SAR"
        assert summary.has_mixed_currencies is True

    def test_falls_back_to_most_frequent_when_org_currency_is_entirely_absent(self, db, service):
        # Org default is SAR, but this analysis has no SAR transactions
        # at all - nothing to prefer, so most-frequent is the least
        # surprising choice. USD (3) beats DZD (1) here.
        _seed_analysis(db)
        for i in range(3):
            _make_txn(
                db, source_type=SourceType.POS_EXPORT, amount=Decimal("10.00"),
                external_reference=f"USD-{i}", currency_code="USD",
            )
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, amount=Decimal("10.00"),
            external_reference="DZD-1", currency_code="DZD",
        )

        summary = service.get_summary(ANALYSIS_ID)

        assert summary.currency == "USD"
        assert summary.has_mixed_currencies is True

    def test_alphabetical_tiebreak_when_frequencies_tie_and_org_currency_absent(self, db, service):
        _seed_analysis(db)  # org default SAR, not present below
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, amount=Decimal("10.00"),
            external_reference="USD-1", currency_code="USD",
        )
        _make_txn(
            db, source_type=SourceType.POS_EXPORT, amount=Decimal("10.00"),
            external_reference="DZD-1", currency_code="DZD",
        )

        summary = service.get_summary(ANALYSIS_ID)

        assert summary.currency == "DZD"  # tied 1-1, 'D' < 'U'


class TestGetSummary:
    def test_raises_when_analysis_does_not_exist(self, service):
        with pytest.raises(AnalysisNotFoundError):
            service.get_summary("no-such-analysis")

    def test_returns_zeroed_defaults_before_pipeline_has_run(self, db, service):
        _seed_analysis(db, status=AnalysisStatus.AWAITING_FILES)

        summary = service.get_summary(ANALYSIS_ID)

        assert summary.status == AnalysisStatus.AWAITING_FILES
        assert summary.currency == "SAR"
        assert summary.orders_processed == 0
        assert summary.matched_count == 0
        assert summary.unmatched_pos_count == 0
        assert summary.unmatched_platform_count == 0
        assert summary.total_potential_revenue_leakage == Decimal("0")
        assert summary.discrepancy_breakdown == []
        assert summary.ai_executive_summary is None
        assert summary.ai_provider_name is None
        assert summary.restaurant_name is None
        assert summary.contact_email is None
        assert summary.whatsapp_number is None

    def test_aggregates_a_completed_analysis_correctly(self, db, service):
        _seed_analysis(db)

        # 5 POS orders is "orders processed", regardless of how many
        # platform-side rows exist.
        for i in range(5):
            _make_txn(
                db, source_type=SourceType.POS_EXPORT, amount=Decimal("100.00"),
                external_reference=f"ORD-100{i}",
            )
        _make_txn(
            db, source_type=SourceType.PLATFORM_SETTLEMENT, amount=Decimal("55.00"),
            external_reference="ORD-1006",
        )

        db.add(
            MatchingRun(
                analysis_id=ANALYSIS_ID, status=MatchingStatus.COMPLETED,
                matched_count=4, unmatched_pos_count=1, unmatched_platform_count=1,
            )
        )
        db.add(
            Discrepancy(
                id="disc-1", analysis_id=ANALYSIS_ID, reconciliation_match_id="match-missing",
                category=DiscrepancyCategory.MISSING_SETTLEMENT, severity=Severity.MEDIUM,
                estimated_loss=Decimal("60.25"),
                treatment=Treatment.RECOVERABLE, direction=Direction.UNDERPAID,
                claimable_amount=Decimal("51.21"),  # 60.25 gross at a 15% commission, net
            )
        )
        db.add(
            Discrepancy(
                id="disc-2", analysis_id=ANALYSIS_ID, reconciliation_match_id="match-unexpected",
                category=DiscrepancyCategory.UNEXPECTED_SETTLEMENT, severity=Severity.MEDIUM,
                estimated_loss=Decimal("55.00"),
                treatment=Treatment.TO_VERIFY, direction=Direction.NOT_APPLICABLE,
            )
        )
        db.add(
            AIInsight(
                analysis_id=ANALYSIS_ID, executive_summary="Two discrepancies found.",
                provider_name="fake",
            )
        )
        db.commit()

        summary = service.get_summary(ANALYSIS_ID)

        assert summary.status == AnalysisStatus.COMPLETED
        assert summary.currency == "SAR"
        assert summary.orders_processed == 5  # POS-side only, not +1 for the platform row
        assert summary.matched_count == 4
        assert summary.unmatched_pos_count == 1
        assert summary.unmatched_platform_count == 1
        # THE headline: underpayments only, net of commission. The 55.00 unexpected
        # settlement is money the restaurant RECEIVED - it is not in the headline.
        assert summary.total_recoverable_amount == Decimal("51.21")
        assert summary.total_potential_revenue_leakage == Decimal("51.21")  # deprecated alias, same number
        assert (summary.groups.recoverable.count, summary.groups.recoverable.amount) == (1, Decimal("51.21"))
        assert (summary.groups.to_verify.count, summary.groups.to_verify.amount) == (1, Decimal("55.00"))
        breakdown = {b.category: (b.count, b.total_amount, b.recoverable_amount) for b in summary.discrepancy_breakdown}
        assert breakdown[DiscrepancyCategory.MISSING_SETTLEMENT] == (1, Decimal("60.25"), Decimal("51.21"))
        assert breakdown[DiscrepancyCategory.UNEXPECTED_SETTLEMENT] == (1, Decimal("55.00"), Decimal("0"))
        assert summary.ai_executive_summary == "Two discrepancies found."
        assert summary.ai_provider_name == "fake"

    def test_uses_the_most_recent_matching_run_when_more_than_one_exists(self, db, service):
        _seed_analysis(db)
        db.add(MatchingRun(analysis_id=ANALYSIS_ID, status=MatchingStatus.COMPLETED, matched_count=1))
        db.commit()
        db.add(MatchingRun(analysis_id=ANALYSIS_ID, status=MatchingStatus.COMPLETED, matched_count=9))
        db.commit()

        summary = service.get_summary(ANALYSIS_ID)

        assert summary.matched_count == 9


class TestSummaryGroupsAndDisclosure:
    def _disc(self, db, did, category, treatment, loss, claimable=None, direction=Direction.UNDERPAID):
        db.add(Discrepancy(
            id=did, analysis_id=ANALYSIS_ID, reconciliation_match_id=f"m-{did}", category=category,
            severity=Severity.LOW, estimated_loss=Decimal(loss), treatment=treatment, direction=direction,
            claimable_amount=Decimal(claimable) if claimable else None,
        ))

    def test_every_treatment_lands_in_its_own_group_and_only_recoverable_is_the_headline(self, db, service):
        _seed_analysis(db)
        C = DiscrepancyCategory
        self._disc(db, "a", C.MISSING_SETTLEMENT, Treatment.RECOVERABLE, "100.00", "85.00")
        self._disc(db, "b", C.INCORRECT_COMMISSION, Treatment.RECOVERABLE, "4.00", "4.00")
        self._disc(db, "c", C.CANCELLED_AFTER_PREPARATION, Treatment.NEEDS_CONFIRMATION, "40.00")
        self._disc(db, "d", C.MISSING_SETTLEMENT, Treatment.NEEDS_CONTRACT, "70.00")
        self._disc(db, "e", C.SETTLEMENT_AMOUNT_MISMATCH, Treatment.TO_VERIFY, "3.00", direction=Direction.OVERPAID)
        self._disc(db, "f", C.CANCELLED_AFTER_PREPARATION, Treatment.NOT_CLAIMABLE, "25.00", direction=Direction.NOT_APPLICABLE)
        self._disc(db, "g", C.MISSING_SETTLEMENT, Treatment.NEEDS_RERUN, "9.00")
        db.commit()

        g = service.get_summary(ANALYSIS_ID).groups
        assert (g.recoverable.count, g.recoverable.amount) == (2, Decimal("89.00"))  # net: 85 + 4
        assert (g.needs_confirmation.count, g.needs_confirmation.amount) == (1, Decimal("40.00"))
        assert (g.needs_contract.count, g.needs_contract.amount) == (1, Decimal("70.00"))
        assert (g.to_verify.count, g.to_verify.amount) == (1, Decimal("3.00"))
        assert (g.not_claimable.count, g.not_claimable.amount) == (1, Decimal("25.00"))
        assert (g.needs_rerun.count, g.needs_rerun.amount) == (1, Decimal("9.00"))
        assert service.get_summary(ANALYSIS_ID).total_recoverable_amount == Decimal("89.00")

    def test_orders_skipped_for_lack_of_a_contract_are_disclosed_not_silent(self, db, service):
        _seed_analysis(db)
        db.add(ComparisonRun(analysis_id=ANALYSIS_ID, status=ComparisonStatus.COMPLETED,
                             compared_count=0, skipped_no_contract_count=973))
        db.add(DiscrepancyRun(analysis_id=ANALYSIS_ID, status=DiscrepancyStatus.COMPLETED, total_count=0,
                              critical_count=0, high_count=0, medium_count=0, low_count=0,
                              excluded_refund_void_count=4))
        db.commit()
        summary = service.get_summary(ANALYSIS_ID)
        assert summary.orders_not_checked_no_contract == 973
        assert summary.excluded_refund_void_count == 4

    def test_detail_rows_carry_treatment_direction_and_claimable_amount(self, db, service):
        _seed_analysis(db)
        self._disc(db, "a", DiscrepancyCategory.MISSING_SETTLEMENT, Treatment.RECOVERABLE, "100.00", "85.00")
        db.commit()
        row = service.list_discrepancies(ANALYSIS_ID)[0]
        assert (row.treatment, row.direction, row.claimable_amount) == (Treatment.RECOVERABLE, Direction.UNDERPAID, Decimal("85.00"))


class TestLeadInfoOnSummary:
    def test_surfaces_lead_info_when_present(self, db, service):
        _seed_analysis(db)
        db.add(
            AnalysisLead(
                analysis_id=ANALYSIS_ID, restaurant_name="Downtown Grill",
                contact_email="owner@downtowngrill.example", whatsapp_number="+966501234567",
            )
        )
        db.commit()

        summary = service.get_summary(ANALYSIS_ID)

        assert summary.restaurant_name == "Downtown Grill"
        assert summary.contact_email == "owner@downtowngrill.example"
        assert summary.whatsapp_number == "+966501234567"


class TestListDiscrepancies:
    def test_raises_when_analysis_does_not_exist(self, service):
        with pytest.raises(AnalysisNotFoundError):
            service.list_discrepancies("no-such-analysis")

    def test_resolves_order_reference_through_the_match(self, db, service):
        _seed_analysis(db)
        pos_txn = _make_txn(
            db, source_type=SourceType.POS_EXPORT, amount=Decimal("60.25"), external_reference="ORD-1005"
        )
        db.add(
            ReconciliationMatch(id="match-1", analysis_id=ANALYSIS_ID, pos_transaction_id=pos_txn.id)
        )
        db.add(
            Discrepancy(
                id="disc-1", analysis_id=ANALYSIS_ID, reconciliation_match_id="match-1",
                category=DiscrepancyCategory.MISSING_SETTLEMENT, severity=Severity.MEDIUM,
                estimated_loss=Decimal("60.25"),
                treatment=Treatment.RECOVERABLE, direction=Direction.UNDERPAID,
                claimable_amount=Decimal("51.21"),  # 60.25 gross at a 15% commission, net
            )
        )
        db.commit()

        results = service.list_discrepancies(ANALYSIS_ID)

        assert len(results) == 1
        assert results[0].order_reference == "ORD-1005"
        assert results[0].category == DiscrepancyCategory.MISSING_SETTLEMENT

    def test_order_reference_is_none_when_the_match_cannot_be_found(self, db, service):
        _seed_analysis(db)
        db.add(
            Discrepancy(
                id="disc-1", analysis_id=ANALYSIS_ID, reconciliation_match_id="does-not-exist",
                category=DiscrepancyCategory.MISSING_SETTLEMENT, severity=Severity.LOW,
                estimated_loss=Decimal("10.00"),
            )
        )
        db.commit()

        results = service.list_discrepancies(ANALYSIS_ID)

        assert results[0].order_reference is None
