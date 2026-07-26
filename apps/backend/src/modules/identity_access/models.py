"""
Persistence model for the Identity & Access module.

Three tables: User (the real "users" table every other module has been
stubbing since Data Import), Session (opaque token hashes, never the raw
token), and UserBranchAccess (which branches a user may act on - a real
join table, though "branches" itself is still a forward-reference stub
until Organization & Branch Management exists).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


class UserRole(str, enum.Enum):
    OWNER = "OWNER"
    FINANCE_MANAGER = "FINANCE_MANAGER"
    ACCOUNTANT = "ACCOUNTANT"
    OPS_MANAGER = "OPS_MANAGER"
    AUDITOR = "AUDITOR"  # read-only, per the Phase 2 persona list


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    # SHA-256 hex of the opaque token - the raw token is returned to the
    # caller exactly once, at login, and never persisted anywhere.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class UserBranchAccess(Base):
    __tablename__ = "user_branch_access"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    branch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("branches.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
