"""
Core logic for the Analysis Results module.

This module owns no tables of its own - it's a read model over five
other modules' persisted output (Normalization's Transaction, Matching's
ReconciliationMatch/MatchingRun, Discrepancies' Discrepancy, AI Insights'
AIInsight), assembled for a frontend results page. Every pipeline module
up to this point was write-once, read-never past its own synchronous
response; this is the first module whose entire purpose is answering
"what did the pipeline already decide", after the fact, same rationale
Analysis Orchestration's own docstring gives for being the one other
GET-shaped module in the build.

Discrepancy and MatchingRun both follow the same "supersede on rerun"
pattern noted in their own modules (discrepancies/service.py deletes and
reinserts; matching rows are marked superseded_at rather than deleted) -
so a plain analysis_id filter on Discrepancy is always "the current set",
while *Run tables are queried by most-recent checked_at, since old run
records themselves aren't deleted, just superseded by a newer row.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.ai_insights.models import AIInsight
from modules.analysis_orchestration.models import Analysis
from modules.discrepancies.models import Discrepancy
from modules.imports.models import SourceType
from modules.lead_capture.models import AnalysisLead
from modules.matching.models import MatchingRun, ReconciliationMatch
from modules.normalization.models import Transaction
from modules.organizations.models import Branch, Organization

from .exceptions import AnalysisNotFoundError
from .schemas import AnalysisSummaryResponse, DiscrepancyBreakdownItem, DiscrepancyDetailResponse


class AnalysisResultsService:
    def __init__(self, *, db: Session) -> None:
        self._db = db

    def get_summary(self, analysis_id: str) -> AnalysisSummaryResponse:
        analysis = self._get_analysis_or_raise(analysis_id)

        orders_processed = len(
            self._db.execute(
                select(Transaction.id).where(
                    Transaction.analysis_id == analysis_id,
                    Transaction.source_type == SourceType.POS_EXPORT,
                )
            ).scalars().all()
        )

        latest_matching_run = self._db.execute(
            select(MatchingRun)
            .where(MatchingRun.analysis_id == analysis_id)
            .order_by(MatchingRun.checked_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        discrepancies = self._db.execute(
            select(Discrepancy).where(Discrepancy.analysis_id == analysis_id)
        ).scalars().all()
        total_leakage = sum((d.estimated_loss for d in discrepancies), Decimal("0"))
        breakdown = self._group_by_category(discrepancies)

        latest_insight = self._db.execute(
            select(AIInsight)
            .where(AIInsight.analysis_id == analysis_id)
            .order_by(AIInsight.generated_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        lead = self._db.execute(
            select(AnalysisLead).where(AnalysisLead.analysis_id == analysis_id)
        ).scalar_one_or_none()

        return AnalysisSummaryResponse(
            analysis_id=analysis_id,
            status=analysis.status,
            currency=self._resolve_currency(analysis.branch_id),
            restaurant_name=lead.restaurant_name if lead else None,
            contact_email=lead.contact_email if lead else None,
            whatsapp_number=lead.whatsapp_number if lead else None,
            orders_processed=orders_processed,
            matched_count=latest_matching_run.matched_count if latest_matching_run else 0,
            unmatched_pos_count=latest_matching_run.unmatched_pos_count if latest_matching_run else 0,
            unmatched_platform_count=(
                latest_matching_run.unmatched_platform_count if latest_matching_run else 0
            ),
            total_potential_revenue_leakage=total_leakage,
            discrepancy_breakdown=breakdown,
            ai_executive_summary=latest_insight.executive_summary if latest_insight else None,
            ai_provider_name=latest_insight.provider_name if latest_insight else None,
        )

    def list_discrepancies(self, analysis_id: str) -> list[DiscrepancyDetailResponse]:
        self._get_analysis_or_raise(analysis_id)

        discrepancies = self._db.execute(
            select(Discrepancy).where(Discrepancy.analysis_id == analysis_id)
        ).scalars().all()

        return [
            DiscrepancyDetailResponse(
                id=d.id,
                category=d.category,
                severity=d.severity,
                estimated_loss=d.estimated_loss,
                order_reference=self._order_reference_for(d),
                created_at=d.created_at,
            )
            for d in discrepancies
        ]

    # -- internal steps ---------------------------------------------------

    def _resolve_currency(self, branch_id: str) -> str:
        branch = self._db.get(Branch, branch_id)
        if branch is None:
            return "USD"  # Shouldn't happen - branch_id is a required FK on Analysis -
            # but a currency label beats a 500 if data's ever inconsistent.
        organization = self._db.get(Organization, branch.organization_id)
        return organization.default_currency if organization else "USD"

    def _order_reference_for(self, discrepancy: Discrepancy) -> str | None:
        match = self._db.get(ReconciliationMatch, discrepancy.reconciliation_match_id)
        if match is None:
            return None
        # Prefer the POS side when both are populated (a fully-matched
        # pair) - either side carries the same external_reference in
        # that case, since matching a pair in the first place depends on
        # the two sides agreeing on it.
        transaction_id = match.pos_transaction_id or match.platform_transaction_id
        if transaction_id is None:
            return None
        transaction = self._db.get(Transaction, transaction_id)
        return transaction.external_reference if transaction else None

    def _group_by_category(self, discrepancies: list[Discrepancy]) -> list[DiscrepancyBreakdownItem]:
        counts: dict = defaultdict(int)
        totals: dict = defaultdict(lambda: Decimal("0"))
        for d in discrepancies:
            counts[d.category] += 1
            totals[d.category] += d.estimated_loss
        return [
            DiscrepancyBreakdownItem(category=category, count=counts[category], total_amount=totals[category])
            for category in counts
        ]

    def _get_analysis_or_raise(self, analysis_id: str) -> Analysis:
        analysis = self._db.get(Analysis, analysis_id)
        if analysis is None:
            raise AnalysisNotFoundError("We couldn't find that analysis.")
        return analysis
