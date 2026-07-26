from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from .models import DiscrepancyCategory, DiscrepancyStatus, Severity


class DiscrepancyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    reconciliation_match_id: str
    category: DiscrepancyCategory
    severity: Severity
    estimated_loss: Decimal
    created_at: datetime


class DiscrepancyRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_id: str
    status: DiscrepancyStatus
    total_count: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    checked_at: datetime
