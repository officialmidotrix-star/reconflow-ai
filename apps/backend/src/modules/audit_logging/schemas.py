from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditLogEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event: str
    user_id: str
    analysis_id: str | None
    details: dict
    created_at: datetime
