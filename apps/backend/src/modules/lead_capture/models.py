"""
Persistence model for Lead Capture.

A new table, not new columns on Analysis - deliberately. This codebase
has no migration tool (checked - only create_all_tables(), a plain
Base.metadata.create_all()), which creates missing tables but never
alters existing ones. Adding columns to Analysis would silently do
nothing against the already-live Postgres schema until someone hand-runs
an ALTER TABLE; a new table needs no such step; create_all() picks it up
on the next deploy same as every other table in this project has.

One row per analysis (analysis_id is unique below), written once right
after Upload gets an analysis_id back, read by Analysis Results to put a
name on the dispute report and results page instead of a bare branch_id.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisLead(Base):
    __tablename__ = "analysis_leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id"), nullable=False, unique=True, index=True
    )
    restaurant_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(320), nullable=False)
    whatsapp_number: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
