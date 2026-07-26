"""
Core business logic for the Organization & Branch Management module.

`get_timezone` and `get_currency` are this module's real "closes the
loop" moment: they satisfy the shape
modules.normalization.dependencies.AnalysisTimezoneLookup declared as a
stand-in since Normalization was built, resolved through a real
Analysis.branch_id now that Analysis Orchestration exists for real. See
this module's own test suite for an integration test that hands this
service straight to a real NormalizationService call.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.analysis_orchestration.models import Analysis

from .dependencies import AuditLogger
from .exceptions import (
    BranchNotFoundError,
    InvalidTimezoneError,
    OrganizationAlreadyExistsError,
    OrganizationNotFoundError,
    OrganizationPersistError,
)
from .models import Branch, Organization


class OrganizationService:
    def __init__(self, *, db: Session, audit_logger: AuditLogger) -> None:
        self._db = db
        self._audit_logger = audit_logger

    def create_organization(
        self, *, legal_name: str, default_currency: str, requested_by: str
    ) -> Organization:
        existing = self._db.execute(select(Organization)).scalars().first()
        if existing is not None:
            raise OrganizationAlreadyExistsError(
                "An organization already exists for this deployment - only one is supported."
            )

        org = Organization(legal_name=legal_name, default_currency=default_currency.upper())
        self._db.add(org)
        self._commit()
        self._db.refresh(org)

        self._audit_logger.log(
            event="organization_created", user_id=requested_by, analysis_id=None,
            metadata={"legal_name": legal_name},
        )
        return org

    def get_current_organization(self) -> Organization | None:
        return self._db.execute(select(Organization)).scalars().first()

    def create_branch(
        self, *, organization_id: str, name: str, timezone: str, requested_by: str
    ) -> Branch:
        org = self._db.get(Organization, organization_id)
        if org is None:
            raise OrganizationNotFoundError("We couldn't find that organization.")
        self._validate_timezone(timezone)

        branch = Branch(organization_id=organization_id, name=name, timezone=timezone)
        self._db.add(branch)
        self._commit()
        self._db.refresh(branch)

        self._audit_logger.log(
            event="branch_created", user_id=requested_by, analysis_id=None,
            metadata={"organization_id": organization_id, "name": name},
        )
        return branch

    def get_branch(self, *, branch_id: str) -> Branch:
        branch = self._db.get(Branch, branch_id)
        if branch is None:
            raise BranchNotFoundError("We couldn't find that branch.")
        return branch

    def list_branches(self, *, organization_id: str) -> list[Branch]:
        return self._db.execute(
            select(Branch).where(Branch.organization_id == organization_id)
        ).scalars().all()

    # -- satisfies modules.normalization.dependencies.AnalysisTimezoneLookup --

    def get_timezone(self, analysis_id: str) -> str | None:
        branch = self._resolve_branch_for_analysis(analysis_id)
        return branch.timezone if branch else None

    def get_currency(self, analysis_id: str) -> str | None:
        branch = self._resolve_branch_for_analysis(analysis_id)
        if branch is None:
            return None
        org = self._db.get(Organization, branch.organization_id)
        return org.default_currency if org else None

    # -- internal steps -------------------------------------------------

    def _resolve_branch_for_analysis(self, analysis_id: str) -> Branch | None:
        analysis = self._db.get(Analysis, analysis_id)
        if analysis is None:
            return None
        return self._db.get(Branch, analysis.branch_id)

    def _validate_timezone(self, tz_name: str) -> None:
        try:
            ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise InvalidTimezoneError(f"'{tz_name}' is not a recognized timezone.") from exc

    def _commit(self) -> None:
        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise OrganizationPersistError("We couldn't save that. Please try again.") from exc
