"""
Core business logic for the Deployment, Update & Licensing module.

get_deployment_info() auto-creates the singleton row on first call - there
is exactly one DeploymentInfo per deployment, and nothing ever needs to
explicitly "initialize" it first. License status is always computed
fresh from license_key/license_expires_at, never stored (see models.py).
Recording a license or an update requires the Owner role, checked against
Identity & Access's real User table.
"""

from __future__ import annotations

from datetime import date as Date

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.identity_access.models import User, UserRole

from .dependencies import AuditLogger
from .exceptions import DeploymentPersistError, InsufficientPrivilegeError
from .models import DEFAULT_VERSION, DeploymentInfo, LicenseStatus, UpdateEvent


class DeploymentService:
    def __init__(self, *, db: Session, audit_logger: AuditLogger) -> None:
        self._db = db
        self._audit_logger = audit_logger

    def get_deployment_info(self) -> DeploymentInfo:
        info = self._db.execute(select(DeploymentInfo)).scalars().first()
        if info is None:
            info = DeploymentInfo(current_version=DEFAULT_VERSION)
            self._db.add(info)
            self._commit()
            self._db.refresh(info)
        return info

    def get_license_status(self, *, today: Date | None = None) -> LicenseStatus:
        info = self.get_deployment_info()
        return self._compute_license_status(info, today=today)

    def record_license(
        self, *, license_key: str, expires_at: Date | None, requested_by: str
    ) -> DeploymentInfo:
        self._ensure_owner(requested_by)
        info = self.get_deployment_info()
        info.license_key = license_key
        info.license_expires_at = expires_at
        self._commit()
        self._db.refresh(info)

        self._audit_logger.log(
            event="license_recorded", user_id=requested_by, analysis_id=None,
            metadata={"expires_at": str(expires_at) if expires_at else None},
        )
        return info

    def record_update(self, *, version: str, notes: str | None, requested_by: str) -> UpdateEvent:
        self._ensure_owner(requested_by)
        info = self.get_deployment_info()
        info.current_version = version

        event = UpdateEvent(version=version, notes=notes)
        self._db.add(event)
        self._commit()
        self._db.refresh(event)

        self._audit_logger.log(
            event="update_recorded", user_id=requested_by, analysis_id=None,
            metadata={"version": version},
        )
        return event

    def list_update_history(self) -> list[UpdateEvent]:
        return self._db.execute(
            select(UpdateEvent).order_by(UpdateEvent.applied_at.desc())
        ).scalars().all()

    # -- internal steps -------------------------------------------------

    @staticmethod
    def _compute_license_status(info: DeploymentInfo, *, today: Date | None = None) -> LicenseStatus:
        today = today or Date.today()
        if not info.license_key:
            return LicenseStatus.UNLICENSED
        if info.license_expires_at is None:
            return LicenseStatus.ACTIVE
        if info.license_expires_at >= today:
            return LicenseStatus.ACTIVE
        return LicenseStatus.EXPIRED

    def _ensure_owner(self, user_id: str) -> None:
        user = self._db.get(User, user_id)
        if user is None or user.role != UserRole.OWNER:
            raise InsufficientPrivilegeError("Only an Owner can perform this action.")

    def _commit(self) -> None:
        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise DeploymentPersistError("We couldn't save that. Please try again.") from exc
