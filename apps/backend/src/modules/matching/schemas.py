from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .models import MatchingStatus


class MatchingRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_id: str
    status: MatchingStatus
    matched_count: int
    unmatched_pos_count: int
    unmatched_platform_count: int
    checked_at: datetime
