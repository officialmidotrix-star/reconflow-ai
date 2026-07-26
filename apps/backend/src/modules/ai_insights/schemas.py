from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AIInsightResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_id: str
    executive_summary: str
    provider_name: str
    model_name: str | None
    generated_at: datetime
