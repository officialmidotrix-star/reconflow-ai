"""API-facing schemas for the Data Import module.

Kept separate from the ORM models (models.py) on purpose: the response
shape the frontend depends on shouldn't change just because a database
column is renamed, and vice versa.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .models import FileStatus, SourceType


class UploadedFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_id: str
    source_type: SourceType
    original_filename: str
    size_bytes: int
    status: FileStatus
    version: int
    created_at: datetime


class ImportErrorResponse(BaseModel):
    """Structured, human-language error - never a raw stack trace or
    filesystem/database error surfaces to the caller (Phase 1 principle:
    users never see technical error text)."""

    error_code: str
    message: str
