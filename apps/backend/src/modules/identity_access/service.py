"""
Core business logic for the Identity & Access module.

`build_auth_context` is the module's real "closes the loop" moment: it
returns a genuine modules.imports.dependencies.AuthContext built from real
session/branch-access data, in place of the InMemory stand-in every
module's tests have constructed by hand since Data Import. See this
module's own test suite for an integration test that hands one straight
to a real ImportService call.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from modules.imports.dependencies import AuthContext

from .dependencies import AuditLogger
from .exceptions import (
    EmailAlreadyExistsError,
    IdentityPersistError,
    InsufficientRoleError,
    InvalidCredentialsError,
    InvalidSessionError,
    UserNotFoundError,
)
from .models import Session, User, UserBranchAccess, UserRole
from .security import generate_session_token, hash_password, hash_token, verify_password

DEFAULT_SESSION_LIFETIME = timedelta(hours=24)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IdentityAccessService:
    def __init__(
        self,
        *,
        db: DBSession,
        audit_logger: AuditLogger,
        session_lifetime: timedelta = DEFAULT_SESSION_LIFETIME,
    ) -> None:
        self._db = db
        self._audit_logger = audit_logger
        self._session_lifetime = session_lifetime

    def create_user(self, *, email: str, password: str, role: UserRole) -> User:
        existing = self._db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existing is not None:
            raise EmailAlreadyExistsError("An account with that email already exists.")

        user = User(email=email, password_hash=hash_password(password), role=role)
        self._db.add(user)
        self._commit()
        self._db.refresh(user)

        self._audit_logger.log(
            event="user_created", user_id=user.id, analysis_id=None,
            metadata={"email": email, "role": role.value},
        )
        return user

    def authenticate(self, *, email: str, password: str) -> tuple[str, User]:
        """Returns (raw_token, user). The raw token is never persisted -
        only its hash is - so this is the one and only time it exists in
        a form that can be used again."""
        user = self._db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        # Deliberately the same error for "no such user" and "wrong
        # password" - distinguishing them lets an attacker enumerate valid
        # emails, which a login endpoint shouldn't reveal.
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("Incorrect email or password.")

        raw_token = generate_session_token()
        session = Session(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=_utcnow() + self._session_lifetime,
        )
        self._db.add(session)
        self._commit()

        self._audit_logger.log(
            event="user_logged_in", user_id=user.id, analysis_id=None, metadata={}
        )
        return raw_token, user

    def get_current_user(self, *, token: str) -> User:
        session = self._db.execute(
            select(Session).where(Session.token_hash == hash_token(token))
        ).scalar_one_or_none()
        if session is None:
            raise InvalidSessionError("That session is not valid. Please log in again.")
        if session.revoked_at is not None:
            raise InvalidSessionError("That session has been signed out. Please log in again.")
        if session.expires_at <= _utcnow():
            raise InvalidSessionError("That session has expired. Please log in again.")

        user = self._db.get(User, session.user_id)
        if user is None or not user.is_active:
            raise InvalidSessionError("That account is no longer active.")
        return user

    def get_user_by_id(self, *, user_id: str) -> User:
        user = self._db.get(User, user_id)
        if user is None:
            raise UserNotFoundError("We couldn't find that user.")
        return user

    def revoke_session(self, *, token: str) -> None:
        session = self._db.execute(
            select(Session).where(Session.token_hash == hash_token(token))
        ).scalar_one_or_none()
        if session is None or session.revoked_at is not None:
            return  # already gone/revoked - logging out twice isn't an error
        session.revoked_at = _utcnow()
        self._commit()

        self._audit_logger.log(
            event="user_logged_out", user_id=session.user_id, analysis_id=None, metadata={}
        )

    def grant_branch_access(self, *, user_id: str, branch_id: str, requested_by: str) -> UserBranchAccess:
        user = self._db.get(User, user_id)
        if user is None:
            raise UserNotFoundError("We couldn't find that user.")

        existing = self._db.execute(
            select(UserBranchAccess).where(
                UserBranchAccess.user_id == user_id, UserBranchAccess.branch_id == branch_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing  # already granted - not an error to grant it again

        grant = UserBranchAccess(user_id=user_id, branch_id=branch_id)
        self._db.add(grant)
        self._commit()
        self._db.refresh(grant)

        self._audit_logger.log(
            event="branch_access_granted", user_id=requested_by, analysis_id=None,
            metadata={"granted_to": user_id, "branch_id": branch_id},
        )
        return grant

    def get_accessible_branch_ids(self, *, user_id: str) -> frozenset[str]:
        rows = self._db.execute(
            select(UserBranchAccess.branch_id).where(UserBranchAccess.user_id == user_id)
        ).scalars().all()
        return frozenset(rows)

    def build_auth_context(self, *, token: str) -> AuthContext:
        user = self.get_current_user(token=token)
        return AuthContext(
            user_id=user.id, accessible_branch_ids=self.get_accessible_branch_ids(user_id=user.id)
        )

    def ensure_role(self, user: User, *, allowed: set[UserRole]) -> None:
        if user.role not in allowed:
            raise InsufficientRoleError(
                f"This action requires one of: {', '.join(r.value for r in allowed)}."
            )

    def _commit(self) -> None:
        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise IdentityPersistError("We couldn't save that. Please try again.") from exc
