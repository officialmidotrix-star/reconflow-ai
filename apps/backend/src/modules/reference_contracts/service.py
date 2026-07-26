"""
Core business logic for the Reference & Contract Configuration module.

`get_commission_rate` is this module's real "closes the loop" moment: it
satisfies the exact shape modules.financial_comparison.dependencies.
ContractLookup declared as a stand-in since that module was built. See
this module's own test suite for an integration test that hands this
service straight to a real ComparisonService call.

Ambiguous-contract handling: if a branch has more than one contract
active on the same date (concurrent platforms), this method cannot
disambiguate which applies - Data Import still doesn't capture platform
identity (see package docstring). Rather than guess, or raise a new
exception type Financial Comparison's contract doesn't expect, the
ambiguous case returns None, exactly like "no contract configured" -
Financial Comparison already skips that pair gracefully rather than
failing the whole run.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.analysis_orchestration.models import Analysis
from modules.organizations.models import Branch

from .dependencies import AuditLogger
from .exceptions import (
    BranchNotFoundError,
    OverlappingContractError,
    PlatformAlreadyExistsError,
    PlatformNotFoundError,
    ReferenceContractPersistError,
)
from .models import CommissionContract, DeliveryPlatform


class ReferenceContractService:
    def __init__(self, *, db: Session, audit_logger: AuditLogger) -> None:
        self._db = db
        self._audit_logger = audit_logger

    def create_platform(self, *, name: str, requested_by: str) -> DeliveryPlatform:
        existing = self._db.execute(
            select(DeliveryPlatform).where(DeliveryPlatform.name == name)
        ).scalar_one_or_none()
        if existing is not None:
            raise PlatformAlreadyExistsError(f"A platform named '{name}' already exists.")

        platform = DeliveryPlatform(name=name)
        self._db.add(platform)
        self._commit()
        self._db.refresh(platform)

        self._audit_logger.log(
            event="platform_created", user_id=requested_by, analysis_id=None, metadata={"name": name}
        )
        return platform

    def list_platforms(self) -> list[DeliveryPlatform]:
        return self._db.execute(select(DeliveryPlatform)).scalars().all()

    def create_contract(
        self,
        *,
        branch_id: str,
        platform_id: str,
        commission_pct: Decimal,
        valid_from: Date,
        valid_to: Date | None,
        requested_by: str,
    ) -> CommissionContract:
        if self._db.get(Branch, branch_id) is None:
            raise BranchNotFoundError("We couldn't find that branch.")
        if self._db.get(DeliveryPlatform, platform_id) is None:
            raise PlatformNotFoundError("We couldn't find that platform.")

        existing = self._db.execute(
            select(CommissionContract).where(
                CommissionContract.branch_id == branch_id,
                CommissionContract.platform_id == platform_id,
            )
        ).scalars().all()
        for other in existing:
            if self._ranges_overlap(valid_from, valid_to, other.valid_from, other.valid_to):
                raise OverlappingContractError(
                    "This contract's date range overlaps an existing contract for the "
                    "same branch and platform."
                )

        contract = CommissionContract(
            branch_id=branch_id, platform_id=platform_id, commission_pct=commission_pct,
            valid_from=valid_from, valid_to=valid_to,
        )
        self._db.add(contract)
        self._commit()
        self._db.refresh(contract)

        self._audit_logger.log(
            event="contract_created", user_id=requested_by, analysis_id=None,
            metadata={"branch_id": branch_id, "platform_id": platform_id},
        )
        return contract

    def list_contracts_for_branch(self, *, branch_id: str) -> list[CommissionContract]:
        return self._db.execute(
            select(CommissionContract).where(CommissionContract.branch_id == branch_id)
        ).scalars().all()

    # -- satisfies modules.financial_comparison.dependencies.ContractLookup --

    def get_commission_rate(self, *, analysis_id: str, as_of: datetime) -> Decimal | None:
        analysis = self._db.get(Analysis, analysis_id)
        if analysis is None:
            return None

        as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
        contracts = self._db.execute(
            select(CommissionContract).where(CommissionContract.branch_id == analysis.branch_id)
        ).scalars().all()
        active = [c for c in contracts if self._is_active_on(c, as_of_date)]

        if len(active) == 1:
            return active[0].commission_pct
        return None  # zero matches, or ambiguous (multiple) - see module docstring

    # -- internal steps -------------------------------------------------

    @staticmethod
    def _is_active_on(contract: CommissionContract, as_of_date: Date) -> bool:
        if as_of_date < contract.valid_from:
            return False
        if contract.valid_to is not None and as_of_date >= contract.valid_to:
            return False
        return True

    @staticmethod
    def _ranges_overlap(
        start1: Date, end1: Date | None, start2: Date, end2: Date | None
    ) -> bool:
        effective_end1 = end1 or Date.max
        effective_end2 = end2 or Date.max
        return start1 < effective_end2 and start2 < effective_end1

    def _commit(self) -> None:
        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise ReferenceContractPersistError(
                "We couldn't save that. Please try again."
            ) from exc
