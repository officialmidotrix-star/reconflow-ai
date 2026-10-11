"""
Tenancy and authorization - enforced once, at composition level.

Why here and not inside each module: every module deliberately knows
nothing about users, organizations or other modules' tables (that is the
architecture), yet "may this caller touch this analysis / file / report?"
needs all of them. So the answer is computed in exactly one place, applied
to every router by `register_all`, and proven by tests that walk the whole
OpenAPI surface.

The model
---------
* PLATFORM_OPERATOR: cross-organization (onboards clients, runs analyses
  on their behalf). Has no organization (DB CHECK ck_users_operator_org).
* OWNER: every branch of their OWN organization, evaluated per request, so
  branches created later are included with no grants.
* FINANCE_MANAGER / ACCOUNTANT / OPS_MANAGER / AUDITOR: only branches
  explicitly granted in user_branch_access (and only inside their own org).
* Nobody ever crosses organizations except the operator.

The rules
---------
1. DENY BY DEFAULT. Every (method, route-template) must have a row in
   POLICIES. A route with no row answers 403 NO_ACCESS_POLICY, and
   tests/backend/test_access_control.py fails the build if one exists.
2. Authentication first (401), then role (403 INSUFFICIENT_ROLE), then
   resource scope.
3. Anything outside the caller's scope - or simply not existing - answers
   the SAME generic 404, so the API never confirms that another client's
   analysis/branch/report exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from fastapi import Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.analysis_orchestration.models import Analysis
from modules.audit_logging.service import AuditLogService
from modules.discrepancies.models import Discrepancy
from modules.identity_access.exceptions import IdentityAccessError_
from modules.identity_access.models import User, UserBranchAccess, UserRole
from modules.identity_access.service import IdentityAccessService
from modules.imports.models import UploadedFile
from modules.matching.models import ReconciliationMatch
from modules.organizations.models import Branch
from modules.reporting.models import Report

from .database import get_db

logger = logging.getLogger("reconflow.bootstrap.access")

_bearer_scheme = HTTPBearer(auto_error=False)


# -- the caller -----------------------------------------------------------


@dataclass(frozen=True)
class AccessContext:
    user_id: str
    role: UserRole
    organization_id: str | None  # None only for PLATFORM_OPERATOR

    @property
    def is_operator(self) -> bool:
        return self.role == UserRole.PLATFORM_OPERATOR


def not_found() -> HTTPException:
    """The one and only answer for 'missing' and 'not yours' alike."""
    return HTTPException(
        status_code=404,
        detail={"error_code": "NOT_FOUND", "message": "We couldn't find that."},
    )


# -- scope resolution -----------------------------------------------------


class ScopeResolver:
    """Answers 'which branches may this caller see' and 'which branch does
    this resource belong to'. One instance per request, sharing the
    request's DB session."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._grants_cache: dict[str, frozenset[str]] = {}

    # branches ----------------------------------------------------------

    def _grants(self, user_id: str) -> frozenset[str]:
        if user_id not in self._grants_cache:
            rows = self._db.execute(
                select(UserBranchAccess.branch_id).where(UserBranchAccess.user_id == user_id)
            ).scalars().all()
            self._grants_cache[user_id] = frozenset(rows)
        return self._grants_cache[user_id]

    def can_see_branch(self, ctx: AccessContext, branch_id: str) -> bool:
        branch = self._db.get(Branch, branch_id)
        if branch is None:
            return False
        if ctx.is_operator:
            return True
        if branch.organization_id != ctx.organization_id:
            return False
        return ctx.role == UserRole.OWNER or branch_id in self._grants(ctx.user_id)

    def branch_scope(self, ctx: AccessContext) -> frozenset[str] | None:
        """For list endpoints. None = 'every branch of the organization
        being listed' (operator, Owner); a set = only those branches."""
        if ctx.is_operator or ctx.role == UserRole.OWNER:
            return None
        return self._grants(ctx.user_id)

    def accessible_branch_ids(self, ctx: AccessContext) -> frozenset[str]:
        """The full, concrete set - used for Data Import's AuthContext and
        for GET /auth/me/branches."""
        query = select(Branch.id)
        if ctx.is_operator:
            pass
        elif ctx.role == UserRole.OWNER:
            query = query.where(Branch.organization_id == ctx.organization_id)
        else:
            grants = self._grants(ctx.user_id)
            if not grants:
                return frozenset()
            query = query.where(Branch.organization_id == ctx.organization_id, Branch.id.in_(grants))
        return frozenset(self._db.execute(query).scalars().all())

    # resources -> branch ------------------------------------------------

    def branch_of(self, model: type, resource_id: str) -> str | None:
        if model is Analysis:
            return self._db.execute(
                select(Analysis.branch_id).where(Analysis.id == resource_id)
            ).scalar_one_or_none()
        return self._db.execute(
            select(Analysis.branch_id)
            .select_from(model)
            .join(Analysis, Analysis.id == model.analysis_id)
            .where(model.id == resource_id)
        ).scalar_one_or_none()

    def require_resource(self, ctx: AccessContext, model: type, resource_id: str) -> None:
        branch_id = self.branch_of(model, resource_id)
        if branch_id is None or not self.can_see_branch(ctx, branch_id):
            raise not_found()

    def require_branch(self, ctx: AccessContext, branch_id: object) -> None:
        if not isinstance(branch_id, str) or not self.can_see_branch(ctx, branch_id):
            raise not_found()

    def require_org(self, ctx: AccessContext, organization_id: object) -> None:
        if not isinstance(organization_id, str):
            raise not_found()
        if ctx.is_operator or organization_id == ctx.organization_id:
            return
        raise not_found()


# -- the policy table -----------------------------------------------------


class R(Enum):
    """What, if anything, the route's resource scope is checked against."""

    NONE = "none"
    ANALYSIS = "analysis_id (path)"
    UPLOADED_FILE = "uploaded_file_id (path)"
    REPORT = "report_id (path)"
    DISCREPANCY = "discrepancy_id (path)"
    MATCH = "reconciliation_match_id (path)"
    BRANCH_PATH = "branch_id (path)"
    ORG_PATH = "organization_id (path)"
    BODY_BRANCH = "branch_id (JSON body)"
    BODY_ORG = "organization_id (JSON body)"
    USER_GRANT = "user_id (path) + branch_id (JSON body)"


@dataclass(frozen=True)
class Policy:
    roles: frozenset[UserRole] | None  # None = any signed-in role (incl. AUDITOR)
    resource: R = R.NONE
    public: bool = False


_O = UserRole
ANY = None
# Anyone who may change analysis data. AUDITOR is deliberately absent:
# read-only. The platform operator always passes role checks.
WRITERS = frozenset({_O.OWNER, _O.FINANCE_MANAGER, _O.ACCOUNTANT, _O.OPS_MANAGER})
OWNER_ONLY = frozenset({_O.OWNER})
CONTRACT_EDITORS = frozenset({_O.OWNER, _O.FINANCE_MANAGER})
OPERATOR_ONLY: frozenset[UserRole] = frozenset()  # only the operator passes

PUBLIC = Policy(roles=ANY, public=True)

# (METHOD, route template) -> Policy. Add a row for every new route; the
# route-coverage test fails until you do.
POLICIES: dict[tuple[str, str], Policy] = {
    # -- identity ---------------------------------------------------------
    ("POST", "/auth/login"): PUBLIC,
    ("POST", "/auth/logout"): PUBLIC,  # revokes the presented token if any; never needs one
    ("GET", "/auth/me"): Policy(ANY),
    ("GET", "/auth/me/branches"): Policy(ANY),
    ("POST", "/users"): Policy(OWNER_ONLY),  # service enforces own-org / no-operator rules
    ("GET", "/users"): Policy(OWNER_ONLY),  # operator: everyone; Owner: own organization (service filters)
    ("POST", "/users/{user_id}/branch-access"): Policy(OWNER_ONLY, R.USER_GRANT),
    # Any signed-in user may call it; the service decides whose password the
    # caller may change (own, with the current one; operator: anyone's; Owner:
    # own organization's) and answers a generic 404 for everything else.
    ("POST", "/users/{user_id}/password"): Policy(ANY),
    # -- organizations / branches / contracts / platforms ----------------
    ("POST", "/organizations"): Policy(OPERATOR_ONLY),
    ("GET", "/organizations"): Policy(ANY),  # view filtered to the caller's scope
    ("GET", "/organizations/current"): Policy(ANY),
    ("GET", "/organizations/{organization_id}/branches"): Policy(ANY, R.ORG_PATH),
    ("POST", "/branches"): Policy(OWNER_ONLY, R.BODY_ORG),
    ("GET", "/branches/{branch_id}"): Policy(ANY, R.BRANCH_PATH),
    ("GET", "/branches/{branch_id}/contracts"): Policy(ANY, R.BRANCH_PATH),
    ("POST", "/contracts"): Policy(CONTRACT_EDITORS, R.BODY_BRANCH),
    ("GET", "/platforms"): Policy(ANY),  # global reference list
    ("POST", "/platforms"): Policy(OPERATOR_ONLY),
    # -- audit log / deployment -------------------------------------------
    ("GET", "/audit-log"): Policy(OWNER_ONLY),  # own organization's entries only (operator: all)
    ("GET", "/deployment/info"): Policy(OPERATOR_ONLY),
    ("POST", "/deployment/license"): Policy(OPERATOR_ONLY),
    ("GET", "/deployment/updates"): Policy(OPERATOR_ONLY),
    ("POST", "/deployment/updates"): Policy(OPERATOR_ONLY),
    # -- analyses ---------------------------------------------------------
    ("POST", "/analyses"): Policy(WRITERS, R.BODY_BRANCH),
    ("GET", "/analyses/{analysis_id}"): Policy(ANY, R.ANALYSIS),
    ("GET", "/analyses/{analysis_id}/summary"): Policy(ANY, R.ANALYSIS),
    ("GET", "/analyses/{analysis_id}/discrepancies"): Policy(ANY, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/files"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/lead"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/matching"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/manual-match"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/comparison"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/discrepancies"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/ai-insights"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/mark-processing"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/mark-completed"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/mark-failed"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/versions"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/notifications"): Policy(WRITERS, R.ANALYSIS),
    ("POST", "/analyses/{analysis_id}/reports"): Policy(WRITERS, R.ANALYSIS),
    ("GET", "/reports/{report_id}/download"): Policy(ANY, R.REPORT),
    ("POST", "/uploaded-files/{uploaded_file_id}/validation"): Policy(WRITERS, R.UPLOADED_FILE),
    ("POST", "/uploaded-files/{uploaded_file_id}/normalization"): Policy(WRITERS, R.UPLOADED_FILE),
    ("POST", "/discrepancies/{discrepancy_id}/review"): Policy(WRITERS, R.DISCREPANCY),
    ("POST", "/discrepancies/{discrepancy_id}/cancellation"): Policy(WRITERS, R.DISCREPANCY),
    ("POST", "/matches/{reconciliation_match_id}/review"): Policy(WRITERS, R.MATCH),
}

# Routes registered directly on the app (not through an included router),
# which therefore never pass through enforce_policy. Reviewed by hand.
UNGUARDED_BY_DESIGN = frozenset({("GET", "/health")})


# -- the audit logger that knows about tenancy ------------------------------


class ScopedAuditLogService(AuditLogService):
    """AuditLogService that stamps each entry with its organization, so
    non-operators can only ever read their own organization's entries."""

    def _organization_for(self, *, user_id: str, analysis_id: str | None) -> str | None:
        try:
            if analysis_id:
                org = self._db.execute(
                    select(Branch.organization_id)
                    .select_from(Analysis)
                    .join(Branch, Branch.id == Analysis.branch_id)
                    .where(Analysis.id == analysis_id)
                ).scalar_one_or_none()
                if org:
                    return org
            return self._db.execute(
                select(User.organization_id).where(User.id == user_id)
            ).scalar_one_or_none()
        except Exception:  # noqa: BLE001 - audit logging must never raise
            return None


# -- enforcement ----------------------------------------------------------


def _authenticate(db: Session, credentials: HTTPAuthorizationCredentials | None) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=401, detail={"error_code": "MISSING_TOKEN", "message": "Sign in required."}
        )
    try:
        return IdentityAccessService(
            db=db, audit_logger=ScopedAuditLogService(db=db)
        ).get_current_user(token=credentials.credentials)
    except IdentityAccessError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=422,
            detail={"error_code": "INVALID_BODY", "message": "A JSON object body is required."},
        )
    return body


_BODY_RESOURCES = {R.BODY_BRANCH, R.BODY_ORG, R.USER_GRANT}


async def enforce_policy(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> None:
    """Router-level dependency applied to every included router.

    Only reading the JSON body is asynchronous; every database lookup runs in the
    thread pool (`run_in_threadpool`) so authorization can never block the event
    loop - which would freeze every other request, including Render's health check."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    policy = POLICIES.get((request.method, template))
    body: dict | None = None
    if policy is not None and not policy.public and policy.resource in _BODY_RESOURCES:
        body = await _json_body(request)
    await run_in_threadpool(_enforce, request, template, policy, credentials, db, body)


def _enforce(
    request: Request,
    template: str | None,
    policy: Policy | None,
    credentials: HTTPAuthorizationCredentials | None,
    db: Session,
    body: dict | None,
) -> None:
    if policy is None:
        logger.error("No access policy for %s %s - denying.", request.method, template)
        raise HTTPException(
            status_code=403,
            detail={"error_code": "NO_ACCESS_POLICY", "message": "This action is not available."},
        )
    if policy.public:
        return

    user = _authenticate(db, credentials)
    ctx = AccessContext(user_id=user.id, role=user.role, organization_id=user.organization_id)
    request.state.access = ctx

    if policy.roles is not None and not ctx.is_operator and ctx.role not in policy.roles:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "INSUFFICIENT_ROLE",
                "message": "Your role does not allow this action.",
            },
        )

    scope = ScopeResolver(db)
    params = request.path_params
    match policy.resource:
        case R.NONE:
            return
        case R.ANALYSIS:
            scope.require_resource(ctx, Analysis, params["analysis_id"])
        case R.UPLOADED_FILE:
            scope.require_resource(ctx, UploadedFile, params["uploaded_file_id"])
        case R.REPORT:
            scope.require_resource(ctx, Report, params["report_id"])
        case R.DISCREPANCY:
            scope.require_resource(ctx, Discrepancy, params["discrepancy_id"])
        case R.MATCH:
            scope.require_resource(ctx, ReconciliationMatch, params["reconciliation_match_id"])
        case R.BRANCH_PATH:
            scope.require_branch(ctx, params["branch_id"])
        case R.ORG_PATH:
            scope.require_org(ctx, params["organization_id"])
        case R.BODY_BRANCH:
            scope.require_branch(ctx, (body or {}).get("branch_id"))
        case R.BODY_ORG:
            scope.require_org(ctx, (body or {}).get("organization_id"))
        case R.USER_GRANT:
            branch_id = (body or {}).get("branch_id")
            target = db.get(User, params["user_id"])
            branch = db.get(Branch, branch_id) if isinstance(branch_id, str) else None
            if (
                target is None
                or branch is None
                or target.organization_id is None
                or target.organization_id != branch.organization_id
            ):
                raise not_found()
            scope.require_org(ctx, branch.organization_id)


# -- accessors used to override each module's placeholder dependencies -----


def current_access(request: Request) -> AccessContext:
    ctx = getattr(request.state, "access", None)
    if ctx is None:  # a route that skipped enforce_policy must never read as authenticated
        raise HTTPException(
            status_code=401, detail={"error_code": "MISSING_TOKEN", "message": "Sign in required."}
        )
    return ctx


def current_user_id(ctx: AccessContext = Depends(current_access)) -> str:
    return ctx.user_id


def current_organization_id(ctx: AccessContext = Depends(current_access)) -> str | None:
    return ctx.organization_id


def current_branch_scope(
    ctx: AccessContext = Depends(current_access), db: Session = Depends(get_db)
) -> frozenset[str] | None:
    return ScopeResolver(db).branch_scope(ctx)


def current_audit_org_scope(ctx: AccessContext = Depends(current_access)) -> str | None:
    return None if ctx.is_operator else ctx.organization_id
