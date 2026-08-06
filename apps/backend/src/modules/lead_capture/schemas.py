from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RecordLeadRequest(BaseModel):
    restaurant_name: str
    contact_email: str
    whatsapp_number: str


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_id: str
    restaurant_name: str
    contact_email: str
    whatsapp_number: str
    created_at: datetime
