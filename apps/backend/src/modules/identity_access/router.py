"""
HTTP layer for the Identity & Access module.

get_current_user_id() here is the real implementation of the pattern
every other module's router has been placeholding - a FastAPI dependency
that resolves a bearer token to a user id via IdentityAccessService.
Wiring every other router's own placeholder to actually call this is
application-assembly work, not this module's job (see package docstring).

POST /users is deliberately open (no auth required) - bootstrapping the
very first account has to be possible before anyone can log in. Real
deployments would want this locked down (invite-only, or "first user
becomes Owner") - flagged here, not solved, since that's a product
decision beyond this module's scope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .exceptions import IdentityAccessError_
from .schemas import (
    CreateUserRequest,
    GrantBranchAccessRequest,
    LoginRequest,
    LoginResponse,
    UserResponse,
)
from .service import IdentityAccessService

router = APIRouter(tags=["identity-access"])
_bearer_scheme = HTTPBearer(auto_error=False)


def get_identity_access_service() -> IdentityAccessService:
    """Real wiring assembled at application start-up, same pattern as
    every other module's router."""
    raise NotImplementedError(
        "Wire up IdentityAccessService (db session, audit_logger) at "
        "application start-up and override this dependency."
    )


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    service: IdentityAccessService = Depends(get_identity_access_service),
) -> str:
    """The real auth dependency other modules' routers have been
    placeholding. Resolves a bearer token to a user id, or raises 401."""
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail={"error_code": "MISSING_TOKEN", "message": "Sign in required."},
        )
    try:
        user = service.get_current_user(token=credentials.credentials)
    except IdentityAccessError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return user.id


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    body: CreateUserRequest,
    service: IdentityAccessService = Depends(get_identity_access_service),
) -> UserResponse:
    try:
        user = service.create_user(email=body.email, password=body.password, role=body.role)
    except IdentityAccessError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return UserResponse.model_validate(user)


@router.post("/auth/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    service: IdentityAccessService = Depends(get_identity_access_service),
) -> LoginResponse:
    try:
        token, user = service.authenticate(email=body.email, password=body.password)
    except IdentityAccessError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return LoginResponse(token=token, user=UserResponse.model_validate(user))


@router.post("/auth/logout", status_code=204)
async def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    service: IdentityAccessService = Depends(get_identity_access_service),
) -> None:
    if credentials is not None:
        service.revoke_session(token=credentials.credentials)


@router.get("/auth/me", response_model=UserResponse)
async def get_me(
    user_id: str = Depends(get_current_user_id),
    service: IdentityAccessService = Depends(get_identity_access_service),
) -> UserResponse:
    user = service.get_user_by_id(user_id=user_id)
    return UserResponse.model_validate(user)


@router.post("/users/{user_id}/branch-access", status_code=201)
async def grant_branch_access(
    user_id: str,
    body: GrantBranchAccessRequest,
    requested_by: str = Depends(get_current_user_id),
    service: IdentityAccessService = Depends(get_identity_access_service),
) -> dict:
    try:
        service.grant_branch_access(
            user_id=user_id, branch_id=body.branch_id, requested_by=requested_by
        )
    except IdentityAccessError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return {"user_id": user_id, "branch_id": body.branch_id}
