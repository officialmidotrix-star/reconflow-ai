from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .models import ReportFormat


class GenerateReportRequest(BaseModel):
    format: ReportFormat


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_id: str
    format: ReportFormat
    generated_at: datetime
