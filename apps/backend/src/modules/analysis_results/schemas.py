"""
Response schemas for the Analysis Results module.

Every other module's response schema mirrors exactly one ORM row
(`model_config = ConfigDict(from_attributes=True)`, `Model.model_validate(obj)`).
These two don't: AnalysisSummaryResponse is an aggregate across five
other modules' tables with no single backing row, and
DiscrepancyDetailResponse joins in a field (order_reference) that isn't
on the Discrepancy row it's mostly built from. Both are constructed with
explicit keyword arguments in the service instead, deliberately, rather
than forcing an ORM-mirroring shape onto data that was never one row to
begin with.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from modules.analysis_orchestration.models import AnalysisStatus
from modules.discrepancies.models import DiscrepancyCategory, Severity


class DiscrepancyBreakdownItem(BaseModel):
    category: DiscrepancyCategory
    count: int
    total_amount: Decimal


class AnalysisSummaryResponse(BaseModel):
    analysis_id: str
    status: AnalysisStatus

    orders_processed: int
    matched_count: int
    unmatched_pos_count: int
    unmatched_platform_count: int

    total_potential_revenue_leakage: Decimal
    discrepancy_breakdown: list[DiscrepancyBreakdownItem]

    # None until AI Insights has actually run for this analysis - a
    # results page should treat that as "not generated yet", not as an
    # error, since it's a legitimate pipeline state (e.g. AWAITING_FILES
    # or PROCESSING, before that step has been reached).
    ai_executive_summary: str | None
    ai_provider_name: str | None


class DiscrepancyDetailResponse(BaseModel):
    id: str
    category: DiscrepancyCategory
    severity: Severity
    estimated_loss: Decimal
    # Sourced from Transaction.external_reference via the discrepancy's
    # match - null only if the linked transaction itself was somehow
    # removed, which nothing in this codebase currently does.
    order_reference: str | None
    created_at: datetime
