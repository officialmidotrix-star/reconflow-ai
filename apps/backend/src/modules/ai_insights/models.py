"""
Persistence model for the AI Insights module.

One table: AIInsight, the entity the Phase 2 ERD already reserved for
this (AI_INSIGHT: analysis_id, executive_summary), extended with
provider/model metadata for traceability. No separate run-summary table
this time - unlike every prior pipeline module, this one produces a single
document per invocation rather than many rows needing aggregate counts, so
the AIInsight row itself is the complete result.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AIInsight(Base):
    __tablename__ = "ai_insights"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, index=True
    )
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
