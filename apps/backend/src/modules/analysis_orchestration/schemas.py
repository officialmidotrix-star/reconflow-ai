from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from .models import AnalysisStatus


class CreateAnalysisRequest(BaseModel):
    branch_id: str
    period_start: date
    period_end: date


class MarkFailedRequest(BaseModel):
    reason: str


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    branch_id: str
    created_by: str
    parent_analysis_id: str | None
    version: int
    status: AnalysisStatus
    period_start: date
    period_end: date
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime
