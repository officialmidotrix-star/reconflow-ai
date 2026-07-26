from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .models import UserRole


class CreateUserRequest(BaseModel):
    email: str
    password: str
    role: UserRole


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    role: UserRole
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
