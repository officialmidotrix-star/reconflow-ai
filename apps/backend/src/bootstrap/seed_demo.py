"""
Optional TEST DATA so a freshly deployed system has something to try.

Switched on by RECONFLOW_SEED_DEMO_DATA=1. It creates, once (never twice), one
organization that is unmistakably not real, three branches and one commission
contract per branch:

    TEST Branch 1 - Talabat       20% commission
    TEST Branch 2 - Jahez         15% commission
    TEST Branch 3 - HungerStation 30% commission

THE COMMISSION RATES ARE TEST VALUES that only illustrate the 15%-30% range.
They are NOT any platform's real rate and NOT any client's contract. Replace
them with the real contract of each client before anyone relies on a number.

One platform per branch on purpose: until analyses are scoped to a branch+platform
pair (Batch 4) a branch with two platforms has no single commission rate.
It needs a platform operator account to attribute the records to (the audit log).
"""

from __future__ import annotations

import logging
import os
from datetime import date
from decimal import Decimal

from bootstrap.access import ScopedAuditLogService
from bootstrap.config import get_operator_email
from bootstrap.database import SessionLocal
from modules.identity_access.models import User, UserRole
from modules.organizations.models import Organization
from modules.organizations.service import OrganizationService
from modules.reference_contracts.models import DeliveryPlatform
from modules.reference_contracts.service import ReferenceContractService

logger = logging.getLogger("reconflow.bootstrap.seed_demo")

DEMO_ORG_NAME = "TEST DATA - Demo Restaurants (not a real client)"
# (branch name, timezone, platform, TEST commission rate)
DEMO_BRANCHES = [
    ("TEST Branch 1 - Talabat (test rate 20%)", "Asia/Riyadh", "Talabat", Decimal("0.20")),
    ("TEST Branch 2 - Jahez (test rate 15%)", "Asia/Riyadh", "Jahez", Decimal("0.15")),
    ("TEST Branch 3 - HungerStation (test rate 30%)", "Asia/Dubai", "HungerStation", Decimal("0.30")),
]


def run_demo_seed() -> None:
    if (os.environ.get("RECONFLOW_SEED_DEMO_DATA") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    db = SessionLocal()
    try:
        if db.query(Organization).filter(Organization.legal_name == DEMO_ORG_NAME).first():
            logger.info("Demo data already present - skipping.")
            return
        email = get_operator_email()
        operator = db.query(User).filter(User.email == email, User.role == UserRole.PLATFORM_OPERATOR).first() if email else None
        if operator is None:
            logger.warning("RECONFLOW_SEED_DEMO_DATA is on but there is no platform operator account yet - skipping.")
            return

        audit = ScopedAuditLogService(db=db)
        orgs = OrganizationService(db=db, audit_logger=audit)
        contracts = ReferenceContractService(db=db, audit_logger=audit)
        org = orgs.create_organization(legal_name=DEMO_ORG_NAME, default_currency="SAR", requested_by=operator.id)
        for branch_name, tz, platform_name, rate in DEMO_BRANCHES:
            branch = orgs.create_branch(organization_id=org.id, name=branch_name, timezone=tz, requested_by=operator.id)
            platform = db.query(DeliveryPlatform).filter(DeliveryPlatform.name == platform_name).first() or \
                contracts.create_platform(name=platform_name, requested_by=operator.id)
            contracts.create_contract(
                branch_id=branch.id, platform_id=platform.id, commission_pct=rate,
                valid_from=date(2026, 1, 1), valid_to=None, requested_by=operator.id,
            )
        logger.warning("TEST DATA created: %r with 3 branches. The commission rates are test values.", DEMO_ORG_NAME)
    finally:
        db.close()
