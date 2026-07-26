from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CreateOrganizationRequest(BaseModel):
    legal_name: str
    default_currency: str


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    legal_name: str
    default_currency: str
    created_at: datetime


class CreateBranchRequest(BaseModel):
    organization_id: str
    name: str
    timezone: str


class BranchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    name: str
    timezone: str
    created_at: datetime
