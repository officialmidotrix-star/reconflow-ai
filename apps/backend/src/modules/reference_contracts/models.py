"""
Persistence model for the Reference & Contract Configuration module.

DeliveryPlatform is a simple reference list. CommissionContract is
temporally versioned per branch+platform (valid_from/valid_to, null end =
still in effect) - the same bitemporal-lite pattern Phase 2's database
design called for from the start, now real instead of assumed.
"""

from __future__ import annotations

import uuid
from datetime import date as Date
from datetime import datetime, timezone

from sqlalchemy import Date as SQLDate
from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DeliveryPlatform(Base):
    __tablename__ = "delivery_platforms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class CommissionContract(Base):
    __tablename__ = "commission_contracts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    branch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("branches.id"), nullable=False, index=True
    )
    platform_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("delivery_platforms.id"), nullable=False, index=True
    )
    # Fraction, not percentage points - Decimal("0.1500") means 15%,
    # matching how Financial Comparison already multiplies pos_amount * rate.
    commission_pct: Mapped[object] = mapped_column(Numeric(6, 4), nullable=False)
    valid_from: Mapped[Date] = mapped_column(SQLDate, nullable=False)
    valid_to: Mapped[Date | None] = mapped_column(SQLDate, nullable=True)  # null = still in effect
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
