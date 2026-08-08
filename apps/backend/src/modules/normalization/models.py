"""
Persistence model for the Data Normalization module.

Three tables belong to this module: Transaction (the canonical output the
rest of the pipeline consumes), NormalizationRun (one row per normalization
attempt, mirroring FileValidation), and NormalizationWarning (non-blocking
notes, mirroring ValidationIssue). Transaction.uploaded_file_id and
NormalizationRun.uploaded_file_id are real foreign keys to Data Import's
table; analysis_id is still a forward reference to the not-yet-built
Analysis Orchestration module's table, same as Data Import's own
analysis_id column.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from db.types import UTCDateTime
from modules.imports.models import SourceType


class NormalizationStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    uploaded_file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("uploaded_files.id"), nullable=False, index=True
    )
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), nullable=False)

    external_reference: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    amount: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    # Populated only for PLATFORM_SETTLEMENT rows - the commission the
    # platform actually deducted, needed by Financial Comparison to check
    # it against the contracted rate. Null for POS_EXPORT rows, which have
    # no commission concept. Added when Financial Comparison's design
    # surfaced that this field was validated by Data Validation's schema
    # registry but then discarded rather than carried into Transaction.
    platform_commission_amount: Mapped[object | None] = mapped_column(Numeric(14, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class TransactionOrderStatus(Base):
    """
    One row per Transaction that had a value in the optional order_status
    column - a new table rather than a column on Transaction itself, same
    create_all()-doesn't-alter-existing-tables reasoning as Lead Capture's
    AnalysisLead. Only ever populated for POS_EXPORT transactions in
    practice (order_status is only in that schema, per Cancellations/
    Refunds design), but keyed generically off transaction_id rather than
    assuming that in the schema itself.

    Stores the raw string exactly as read from the file, not a coerced
    enum - Data Validation's own schema registry docstring is explicit
    that real export formats haven't been sampled yet, so which spellings
    actually mean "cancelled" is something to refine once real samples
    exist, not something to lock in now by discarding whatever a file
    actually said. Interpretation (does this raw value indicate a
    cancellation) lives in Discrepancy Detection, the one place that
    needs to act on it.
    """

    __tablename__ = "transaction_order_statuses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    raw_status: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class NormalizationRun(Base):
    __tablename__ = "normalization_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    uploaded_file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("uploaded_files.id"), nullable=False, index=True
    )
    status: Mapped[NormalizationStatus] = mapped_column(Enum(NormalizationStatus), nullable=False)
    rows_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)

    warnings: Mapped[list["NormalizationWarning"]] = relationship(
        back_populates="normalization_run", cascade="all, delete-orphan"
    )


class NormalizationWarning(Base):
    __tablename__ = "normalization_warnings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    normalization_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("normalization_runs.id"), nullable=False, index=True
    )
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    field: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    normalization_run: Mapped["NormalizationRun"] = relationship(back_populates="warnings")
