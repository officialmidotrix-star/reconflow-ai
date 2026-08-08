"""
Persistence model for the Data Validation module.

Two tables belong to this module: FileValidation (one result per validation
run) and ValidationIssue (zero or more specific problems found in that run).
FileValidation.uploaded_file_id is a real foreign key to Data Import's
`uploaded_files` table - a genuine dependency now, since that module and
its table already exist (unlike the still-stubbed `analyses`/`users`
references Data Import itself declares).

Deliberately NOT writing anything onto Data Import's own UploadedFile row:
each module writes only the tables it owns. Analysis Orchestration, once
built, will read both this table and Data Import's to decide whether an
analysis is ready to proceed.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from db.types import UTCDateTime


class ValidationStatus(str, enum.Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class IssueCode(str, enum.Enum):
    MISSING_COLUMN = "MISSING_COLUMN"
    NO_DATA_ROWS = "NO_DATA_ROWS"
    UNREADABLE_FILE = "UNREADABLE_FILE"
    TOO_MANY_ROWS = "TOO_MANY_ROWS"
    INVALID_NUMBER = "INVALID_NUMBER"
    INVALID_DATE = "INVALID_DATE"
    EMPTY_REQUIRED_FIELD = "EMPTY_REQUIRED_FIELD"
    INVALID_STATUS_VALUE = "INVALID_STATUS_VALUE"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FileValidation(Base):
    __tablename__ = "file_validations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    uploaded_file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("uploaded_files.id"), nullable=False, index=True
    )
    status: Mapped[ValidationStatus] = mapped_column(Enum(ValidationStatus), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)

    issues: Mapped[list["ValidationIssue"]] = relationship(
        back_populates="file_validation", cascade="all, delete-orphan"
    )


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    file_validation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("file_validations.id"), nullable=False, index=True
    )
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issue_code: Mapped[IssueCode] = mapped_column(Enum(IssueCode), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    file_validation: Mapped["FileValidation"] = relationship(back_populates="issues")
