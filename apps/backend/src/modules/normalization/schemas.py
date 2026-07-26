from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .models import NormalizationStatus


class NormalizationWarningResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int | None
    field: str | None
    message: str


class NormalizationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    uploaded_file_id: str
    status: NormalizationStatus
    rows_created: int
    checked_at: datetime
    warnings: list[NormalizationWarningResponse]
