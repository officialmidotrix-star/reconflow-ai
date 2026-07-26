from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from .models import LicenseStatus


class DeploymentInfoResponse(BaseModel):
    id: str
    current_version: str
    license_status: LicenseStatus
    license_expires_at: date | None
    installed_at: datetime


class RecordLicenseRequest(BaseModel):
    license_key: str
    expires_at: date | None = None


class RecordUpdateRequest(BaseModel):
    version: str
    notes: str | None = None


class UpdateEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version: str
    applied_at: datetime
    notes: str | None
