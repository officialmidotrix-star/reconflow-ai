"""
Persistence model for the Organization & Branch Management module.

Organization is enforced as a singleton at the service layer, not the
schema layer (no unique constraint trick needed - a plain "does one
already exist" check in the service is simpler and just as correct for a
row that only ever gets created once). Branch is the real "branches"
table every other module has been stubbing since Data Import.
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


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # IANA zone name (e.g. "Asia/Riyadh") - validated as a real zone at
    # creation time in service.py, not just accepted as an arbitrary
    # string the way every stub before this module did.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
