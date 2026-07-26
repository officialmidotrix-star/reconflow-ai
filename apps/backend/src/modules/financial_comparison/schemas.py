from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from .models import ComparisonStatus


class ComparisonResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    reconciliation_match_id: str
    expected_commission: Decimal
    actual_commission: Decimal
    commission_variance: Decimal
    commission_within_tolerance: bool
    settlement_variance: Decimal
    settlement_within_tolerance: bool
    checked_at: datetime


class ComparisonRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_id: str
    status: ComparisonStatus
    compared_count: int
    within_tolerance_count: int
    out_of_tolerance_count: int
    skipped_no_contract_count: int
    checked_at: datetime
