"""
Unit tests for AIInsightService.

Uses FakeAIProvider exclusively - a unit test suite should never depend on
a live, paid, external AI API. Discrepancy rows are constructed directly,
same testing-boundary rationale as every downstream module so far.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Column, String, Table, create_engine, select
from sqlalchemy.orm import Session

from modules.ai_insights.dependencies import FakeAIProvider, InMemoryAuditLogger
from modules.ai_insights.exceptions import AIProviderError, GroundingViolationError
from modules.ai_insights.models import AIInsight
from modules.ai_insights.service import AIInsightService, _allowed_numbers, _extract_numbers
from modules.discrepancies.models import Discrepancy, DiscrepancyCategory, Severity
from modules.imports.models import Base
from modules.matching.models import ReconciliationMatch  # noqa: F401 - registers reconciliation_matches
from modules.normalization.models import Transaction  # noqa: F401 - registers transactions

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
    return AIInsightService(db=db, ai_provider=FakeAIProvider(), audit_logger=audit_logger)


def _make_discrepancy(
    db: Session,
    *,
    category: DiscrepancyCategory,
    severity: Severity,
    estimated_loss: Decimal,
    reconciliation_match_id: str = "match-1",
) -> Discrepancy:
    d = Discrepancy(
        analysis_id=ANALYSIS_ID,
        reconciliation_match_id=reconciliation_match_id,
        category=category,
        severity=severity,
        estimated_loss=estimated_loss,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


class TestGroundingHelpers:
    def test_extract_numbers_strips_commas(self):
        assert _extract_numbers("Impact was 1,234.56 SAR across 3 issues") == {"1234.56", "3"}

    def test_extract_numbers_finds_nothing_in_plain_text(self):
        assert _extract_numbers("Everything reconciled cleanly.") == set()


class TestCleanAnalysis:
    def test_generates_summary_for_zero_discrepancies(self, service):
        insight = service.generate_insight(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert "no discrepancies" in insight.executive_summary.lower()
        assert insight.provider_name == "fake"


class TestFactsAggregation:
    def test_counts_and_totals_computed_correctly(self, service, db):
        _make_discrepancy(
            db, category=DiscrepancyCategory.MISSING_SETTLEMENT, severity=Severity.CRITICAL,
            estimated_loss=Decimal("1200.00"),
        )
        _make_discrepancy(
            db, category=DiscrepancyCategory.INCORRECT_COMMISSION, severity=Severity.MEDIUM,
            estimated_loss=Decimal("75.00"),
        )
        _make_discrepancy(
            db, category=DiscrepancyCategory.INCORRECT_COMMISSION, severity=Severity.LOW,
            estimated_loss=Decimal("25.00"),
        )

        insight = service.generate_insight(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        # The fake provider echoes total_discrepancies, critical_count, and
        # high_count back into the text - confirms the facts it received
        # were aggregated correctly, since the grounding check would have
        # rejected anything not actually present in what was computed.
        assert "3 discrepancies" in insight.executive_summary
        assert "1300.00" in insight.executive_summary  # 1200 + 75 + 25


class TestProviderFailure:
    def test_provider_exception_raises_ai_provider_error(self, db, audit_logger):
        class BrokenProvider:
            provider_name = "broken"

            def generate_summary(self, facts):
                raise ConnectionError("network down")

        service = AIInsightService(db=db, ai_provider=BrokenProvider(), audit_logger=audit_logger)
        with pytest.raises(AIProviderError):
            service.generate_insight(analysis_id=ANALYSIS_ID, requested_by=USER_ID)


class TestGroundingViolation:
    def test_ungrounded_number_is_rejected(self, db, audit_logger):
        service = AIInsightService(
            db=db,
            ai_provider=FakeAIProvider(inject_ungrounded_number=True),
            audit_logger=audit_logger,
        )
        _make_discrepancy(
            db, category=DiscrepancyCategory.MISSING_SETTLEMENT, severity=Severity.LOW,
            estimated_loss=Decimal("10.00"),
        )
        with pytest.raises(GroundingViolationError):
            service.generate_insight(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

    def test_rejected_summary_is_not_persisted(self, db, audit_logger):
        service = AIInsightService(
            db=db,
            ai_provider=FakeAIProvider(inject_ungrounded_number=True),
            audit_logger=audit_logger,
        )
        _make_discrepancy(
            db, category=DiscrepancyCategory.MISSING_SETTLEMENT, severity=Severity.LOW,
            estimated_loss=Decimal("10.00"),
        )
        with pytest.raises(GroundingViolationError):
            service.generate_insight(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        assert db.execute(select(AIInsight)).scalars().first() is None


class TestRerunSupersedes:
    def test_second_generation_replaces_first(self, service, db):
        service.generate_insight(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        service.generate_insight(analysis_id=ANALYSIS_ID, requested_by=USER_ID)

        insights = db.execute(
            select(AIInsight).where(AIInsight.analysis_id == ANALYSIS_ID)
        ).scalars().all()
        assert len(insights) == 1


class TestAuditLogging:
    def test_logs_generation(self, service, audit_logger):
        service.generate_insight(analysis_id=ANALYSIS_ID, requested_by=USER_ID)
        assert audit_logger.records[-1].event == "ai_insight_generated"
        assert audit_logger.records[-1].analysis_id == ANALYSIS_ID
        assert audit_logger.records[-1].metadata["provider_name"] == "fake"
