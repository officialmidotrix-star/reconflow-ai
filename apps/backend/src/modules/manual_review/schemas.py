from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .models import DiscrepancyReviewDecision, MatchReviewDecision


class MatchReviewRequest(BaseModel):
    decision: MatchReviewDecision
    note: str | None = None


class MatchReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    reconciliation_match_id: str
    decision: MatchReviewDecision
    note: str | None
    reviewed_by: str
    reviewed_at: datetime


class DiscrepancyReviewRequest(BaseModel):
    decision: DiscrepancyReviewDecision
    note: str | None = None


class DiscrepancyReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    discrepancy_id: str
    decision: DiscrepancyReviewDecision
    note: str | None
    reviewed_by: str
    reviewed_at: datetime


class ManualMatchRequest(BaseModel):
    pos_transaction_id: str
    platform_transaction_id: str
    note: str | None = None


class ManualMatchResponse(BaseModel):
    reconciliation_match_id: str
    pos_transaction_id: str
    platform_transaction_id: str
    review: MatchReviewResponse
