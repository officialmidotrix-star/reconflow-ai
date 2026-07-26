from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CreatePlatformRequest(BaseModel):
    name: str


class PlatformResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime


class CreateContractRequest(BaseModel):
    branch_id: str
    platform_id: str
    commission_pct: Decimal
    valid_from: date
    valid_to: date | None = None


class ContractResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    branch_id: str
    platform_id: str
    commission_pct: Decimal
    valid_from: date
    valid_to: date | None
    created_at: datetime
