"""
Persistence model for the Data Import module.

Only one table belongs to this module: UploadedFile. It references
`analyses.id` and `users.id` by table name only (forward string references),
since the Analysis Orchestration and Identity & Access modules own those
tables and have not been implemented yet in this phased build. SQLAlchemy
resolves string-based ForeignKey targets at mapper-configuration time, so
this is safe to declare now without importing those modules.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Enum,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime

# Re-exported for backward compatibility: existing code (including this
# module's own test suite) imports Base from here. Now that the Data
# Validation module needs to share this metadata for a real foreign key,
# Base itself lives in db.base - see that module's docstring.
__all__ = ["Base", "SourceType", "FileStatus", "UploadedFile"]


class SourceType(str, enum.Enum):
    POS_EXPORT = "POS_EXPORT"
    PLATFORM_SETTLEMENT = "PLATFORM_SETTLEMENT"


class FileStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    SUPERSEDED = "SUPERSEDED"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UploadedFile(Base):
    """One raw file uploaded for one (analysis, source_type) slot.

    A corrected re-upload does not overwrite the previous row - it marks the
    previous row SUPERSEDED and inserts a new row with version + 1, so the
    audit trail from the Phase 2 database design is preserved at the storage
    layer too, not just conceptually.
    """

    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)

    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), nullable=False)

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus), nullable=False, default=FileStatus.RECEIVED
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    uploaded_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    superseded_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
