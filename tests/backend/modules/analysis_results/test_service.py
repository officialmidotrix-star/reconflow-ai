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
from modules.discrepancies.models import Discrepancy, DiscrepancyCategory, Severity
from modules.identity_access.models import Base, User  # noqa: F401 - registers "users"
from modules.imports.models import SourceType, UploadedFile  # noqa: F401 - registers "uploaded_files"
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


def _make_txn(db: Session, *, source_type: SourceType, amount: Decimal, external_reference: str) -> Transaction:
    txn = Transaction(
        analysis_id=ANALYSIS_ID, uploaded_file_id=f"uploaded-file-{source_type.value}",
        source_type=source_type, external_reference=external_reference,
        occurred_at=datetime(2026, 7, 15, 10, 0, 0, tzinfo=dt_timezone.utc),
        amount=amount, currency_code="SAR",
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


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
            )
        )
        db.add(
            Discrepancy(
                id="disc-2", analysis_id=ANALYSIS_ID, reconciliation_match_id="match-unexpected",
                category=DiscrepancyCategory.UNEXPECTED_SETTLEMENT, severity=Severity.MEDIUM,
                estimated_loss=Decimal("55.00"),
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
        assert summary.total_potential_revenue_leakage == Decimal("115.25")
        breakdown = {b.category: (b.count, b.total_amount) for b in summary.discrepancy_breakdown}
        assert breakdown[DiscrepancyCategory.MISSING_SETTLEMENT] == (1, Decimal("60.25"))
        assert breakdown[DiscrepancyCategory.UNEXPECTED_SETTLEMENT] == (1, Decimal("55.00"))
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
