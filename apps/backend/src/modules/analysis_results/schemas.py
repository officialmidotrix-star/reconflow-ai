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
from modules.discrepancies.models import Direction, DiscrepancyCategory, Severity, Treatment


class DiscrepancyBreakdownItem(BaseModel):
    category: DiscrepancyCategory
    count: int
    # Gross amount involved, whatever its treatment (what older clients showed).
    total_amount: Decimal
    # The part of this category that is recoverable, NET of commission.
    recoverable_amount: Decimal


class SummaryGroup(BaseModel):
    count: int
    amount: Decimal


class SummaryGroups(BaseModel):
    """The five things a discrepancy can mean. Only `recoverable` is the headline."""

    # Underpaid, claimable. `amount` is NET of commission - the sum of claimable_amount.
    recoverable: SummaryGroup
    # Cancelled after preparation; waiting for "who cancelled?". `amount` is gross.
    needs_confirmation: SummaryGroup
    # Would be recoverable but there is no (or an ambiguous) commission rate. `amount` is gross.
    needs_contract: SummaryGroup
    # Not a loss: money received with no POS order, overpayments, commission charged below contract.
    to_verify: SummaryGroup
    # The restaurant's own cancellations. `amount` is gross.
    not_claimable: SummaryGroup
    # Calculated before these rules existed - run the analysis again. `amount` is gross.
    needs_rerun: SummaryGroup


class AnalysisSummaryResponse(BaseModel):
    analysis_id: str
    status: AnalysisStatus
    # Sourced from the branch's organization at request time, not stored
    # on Analysis itself - added after the fact, noticed while building
    # the results page that a revenue-leakage figure with no currency
    # attached isn't something a restaurant owner should be asked to trust.
    currency: str
    # True when this analysis's transactions genuinely span more than one
    # currency (per-row currency now flows from the uploaded file when
    # present) - the frontend surfaces this as a small caveat rather than
    # silently summing amounts across currencies into one misleading
    # total. Detecting and disclosing this is in scope; actually
    # converting between currencies to make the total correct is not.
    has_mixed_currencies: bool

    orders_processed: int
    matched_count: int
    unmatched_pos_count: int
    unmatched_platform_count: int

    # THE headline: what the restaurant can claim back - underpayments only,
    # net of commission. Overpayments and unexpected settlements are not in it.
    total_recoverable_amount: Decimal
    # Deprecated name for the same number, kept so a frontend deployed before
    # this change shows the corrected figure instead of breaking.
    total_potential_revenue_leakage: Decimal
    groups: SummaryGroups
    discrepancy_breakdown: list[DiscrepancyBreakdownItem]
    # Matched orders that could not be checked for commission / amount errors
    # because no single commission contract applied. Silent before; never again.
    orders_not_checked_no_contract: int
    # Orders with no settlement whose status says refunded / void: no payout expected.
    excluded_refund_void_count: int

    # None for analyses run before lead capture existed, or if the lead
    # call failed after upload for some reason - a results page can still
    # render the whole report without these, just without a name to put
    # on the dispute report in place of the raw branch_id.
    restaurant_name: str | None
    contact_email: str | None
    whatsapp_number: str | None

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
    treatment: Treatment
    direction: Direction | None
    claimable_amount: Decimal | None
    # Sourced from Transaction.external_reference via the discrepancy's
    # match - null only if the linked transaction itself was somehow
    # removed, which nothing in this codebase currently does.
    order_reference: str | None
    created_at: datetime
