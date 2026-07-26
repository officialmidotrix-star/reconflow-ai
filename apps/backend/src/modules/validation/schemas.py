from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .models import IssueCode, ValidationStatus


class ValidationIssueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int | None
    column_name: str | None
    issue_code: IssueCode
    message: str


class FileValidationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    uploaded_file_id: str
    status: ValidationStatus
    row_count: int
    checked_at: datetime
    issues: list[ValidationIssueResponse]
