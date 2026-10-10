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

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import UTCDateTime


class UserRole(str, enum.Enum):
    OWNER = "OWNER"
    FINANCE_MANAGER = "FINANCE_MANAGER"
    ACCOUNTANT = "ACCOUNTANT"
    OPS_MANAGER = "OPS_MANAGER"
    AUDITOR = "AUDITOR"  # read-only, per the Phase 2 persona list
    # Cross-organization operator of the platform (onboards clients, runs
    # analyses on their behalf). Never assignable over HTTP - created only
    # by bootstrap/create_operator.py or the startup seed - and the CHECK
    # constraint on User makes it structurally impossible for a user that
    # belongs to a client organization to hold it.
    PLATFORM_OPERATOR = "PLATFORM_OPERATOR"


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(role = 'PLATFORM_OPERATOR' AND organization_id IS NULL) "
            "OR (role <> 'PLATFORM_OPERATOR' AND organization_id IS NOT NULL)",
            name="ck_users_operator_org",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    # NULL only for PLATFORM_OPERATOR (enforced by ck_users_operator_org);
    # every client user belongs to exactly one organization.
    organization_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=True, index=True
    )
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
