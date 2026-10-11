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
from modules.organizations.models import Branch, Organization

from .dependencies import AuditLogger
from .exceptions import (
    CrossOrganizationError,
    CurrentPasswordIncorrectError,
    EmailAlreadyExistsError,
    IdentityPersistError,
    InsufficientRoleError,
    InvalidUserScopeError,
    OperatorRoleNotAssignableError,
    PasswordUnchangedError,
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

    def create_user(
        self,
        *,
        email: str,
        password: str,
        role: UserRole,
        organization_id: str | None = None,
        created_by: str | None = None,
    ) -> User:
        """Create a client user. PLATFORM_OPERATOR is never creatable here.

        When `created_by` is given (every HTTP path), the actor's own
        authority bounds the result: a PLATFORM_OPERATOR may create a user
        in any organization; an OWNER may create users only inside their
        own organization; nobody else may create users at all. Callers
        that pass no actor (seed scripts, tests) are trusted code and only
        the structural invariants are checked.
        """
        if role == UserRole.PLATFORM_OPERATOR:
            raise OperatorRoleNotAssignableError(
                "The platform-operator role cannot be assigned through the API."
            )
        organization_id = self._resolve_creation_org(created_by, organization_id)
        return self._insert_user(
            email=email, password=password, role=role, organization_id=organization_id,
            audit_user_id=created_by,
        )

    def create_operator(self, *, email: str, password: str) -> User:
        """Create (or refuse to duplicate) a platform operator. Only
        called from bootstrap/create_operator.py and the startup seed -
        there is deliberately no HTTP route to it."""
        return self._insert_user(
            email=email, password=password, role=UserRole.PLATFORM_OPERATOR,
            organization_id=None, audit_user_id=None,
        )

    def _resolve_creation_org(self, created_by: str | None, requested_org: str | None) -> str:
        if created_by is not None:
            actor = self._db.get(User, created_by)
            if actor is None or not actor.is_active:
                raise InsufficientRoleError("You are not allowed to create users.")
            if actor.role == UserRole.OWNER:
                if requested_org is not None and requested_org != actor.organization_id:
                    # Same answer as 'no such organization' - never confirm
                    # that another client's organization exists.
                    raise CrossOrganizationError("We couldn't find that organization.")
                requested_org = actor.organization_id
            elif actor.role != UserRole.PLATFORM_OPERATOR:
                raise InsufficientRoleError("Only an Owner or the platform operator can create users.")
        if requested_org is None:
            raise InvalidUserScopeError("Every user except the platform operator must belong to an organization.")
        if self._db.get(Organization, requested_org) is None:
            raise CrossOrganizationError("We couldn't find that organization.")
        return requested_org

    def _insert_user(
        self, *, email: str, password: str, role: UserRole,
        organization_id: str | None, audit_user_id: str | None,
    ) -> User:
        existing = self._db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existing is not None:
            raise EmailAlreadyExistsError("An account with that email already exists.")

        user = User(
            email=email, password_hash=hash_password(password), role=role,
            organization_id=organization_id,
        )
        self._db.add(user)
        self._commit()
        self._db.refresh(user)

        self._audit_logger.log(
            event="user_created", user_id=audit_user_id or user.id, analysis_id=None,
            metadata={"email": email, "role": role.value, "organization_id": organization_id},
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

    def list_users(self, *, actor_id: str) -> list[User]:
        """The platform operator sees every user; an Owner sees the users of
        their own organization; nobody else lists users."""
        actor = self._db.get(User, actor_id)
        if actor is None or not actor.is_active:
            raise InsufficientRoleError("You are not allowed to list users.")
        query = select(User).order_by(User.email)
        if actor.role == UserRole.OWNER:
            query = query.where(User.organization_id == actor.organization_id)
        elif actor.role != UserRole.PLATFORM_OPERATOR:
            raise InsufficientRoleError("Only an Owner or the platform operator can list users.")
        return list(self._db.execute(query).scalars().all())

    def change_password(
        self,
        *,
        actor_id: str,
        target_user_id: str,
        new_password: str,
        current_password: str | None = None,
    ) -> None:
        """Change (own) or reset (someone else's) a password.

        Who may do what: anyone may change their own password given the
        correct current one; the platform operator may reset anyone's; an
        Owner may reset the password of a user in their own organization
        (never an operator, never another organization). Everything else
        answers the same generic 'not found' as a user that does not exist.

        Every session of the target is revoked, so a leaked token or an
        old password stops working at once; the user must sign in again.
        """
        actor = self._db.get(User, actor_id)
        target = self._db.get(User, target_user_id)
        if actor is None or not actor.is_active or target is None:
            raise CrossOrganizationError("We couldn't find that user.")

        if actor.id == target.id:
            if current_password is None or not verify_password(current_password, target.password_hash):
                raise CurrentPasswordIncorrectError("The current password is incorrect.")
        elif actor.role == UserRole.PLATFORM_OPERATOR:
            pass
        elif (
            actor.role == UserRole.OWNER
            and target.role != UserRole.PLATFORM_OPERATOR
            and target.organization_id == actor.organization_id
        ):
            pass
        else:
            raise CrossOrganizationError("We couldn't find that user.")

        if verify_password(new_password, target.password_hash):
            raise PasswordUnchangedError("The new password must be different from the current one.")

        target.password_hash = hash_password(new_password)
        now = _utcnow()
        for session in self._db.execute(
            select(Session).where(Session.user_id == target.id, Session.revoked_at.is_(None))
        ).scalars():
            session.revoked_at = now
        self._commit()

        self._audit_logger.log(
            event="password_changed", user_id=actor.id, analysis_id=None,
            metadata={"target_user_id": target.id, "self_service": actor.id == target.id},
        )

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
        branch = self._db.get(Branch, branch_id)
        # A grant can only ever connect a user to a branch of the user's
        # own organization (operators hold no grants - they see everything).
        if (
            branch is None
            or user.role == UserRole.PLATFORM_OPERATOR
            or branch.organization_id != user.organization_id
        ):
            raise CrossOrganizationError("We couldn't find that branch.")

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
