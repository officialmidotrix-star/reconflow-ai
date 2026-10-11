from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import UserRole


class CreateUserRequest(BaseModel):
    email: str
    password: str
    role: UserRole
    # Required for every role except PLATFORM_OPERATOR (which cannot be
    # created over HTTP at all). An OWNER creating a user always creates
    # it inside their own organization; this field is then optional and
    # must match if supplied.
    organization_id: str | None = None


class ChangePasswordRequest(BaseModel):
    # Minimum length is enforced here (not on user creation, which predates it).
    new_password: str = Field(min_length=12, max_length=256)
    # Required when changing your OWN password; not used when the platform
    # operator (or an Owner, for a user of their own organization) resets
    # someone else's.
    current_password: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    role: UserRole
    organization_id: str | None = None
    is_active: bool
    created_at: datetime


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: UserResponse


class GrantBranchAccessRequest(BaseModel):
    branch_id: str
