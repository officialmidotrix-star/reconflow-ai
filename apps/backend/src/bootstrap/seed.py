"""
Startup seed for the PLATFORM_OPERATOR account (the person who onboards
client organizations and runs analyses on their behalf).

Runs only when the operator email (RECONFLOW_OPERATOR_EMAIL, or the older
RECONFLOW_SEED_OPERATOR_EMAIL) and RECONFLOW_SEED_OPERATOR_PASSWORD are both set, and only if no user with
that email exists - safe to leave the variables set. It never changes an
existing account; promoting an existing user is an explicit act
(`python -m bootstrap.create_operator --promote-existing`, or the
`-x operator_email=...` migration argument).

The previous RECONFLOW_SEED_OWNER_* variables are no longer honoured: an
OWNER now has to belong to an organization, which a seed cannot know.
"""

from __future__ import annotations

import logging
import os

from bootstrap.access import ScopedAuditLogService
from bootstrap.config import get_operator_email
from bootstrap.database import SessionLocal
from modules.identity_access.exceptions import IdentityAccessError_
from modules.identity_access.models import User, UserRole
from modules.identity_access.service import IdentityAccessService

logger = logging.getLogger("reconflow.bootstrap.seed")


def run_operator_seed() -> None:
    if os.environ.get("RECONFLOW_SEED_OWNER_EMAIL") or os.environ.get("RECONFLOW_SEED_OWNER_PASSWORD"):
        logger.warning(
            "RECONFLOW_SEED_OWNER_* is no longer used. Create the platform operator with "
            "RECONFLOW_SEED_OPERATOR_EMAIL / RECONFLOW_SEED_OPERATOR_PASSWORD, then create the "
            "client organization and its OWNER through the API."
        )

    email = get_operator_email()
    password = os.environ.get("RECONFLOW_SEED_OPERATOR_PASSWORD")
    if not email or not password:
        return

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing is not None:
            if existing.role != UserRole.PLATFORM_OPERATOR:
                logger.warning(
                    "Seed operator email %s already exists as %s - leaving it untouched. "
                    "Promote it explicitly if that is what you want.",
                    email, existing.role.value,
                )
            else:
                logger.info("Seed operator %s already exists - skipping.", email)
            return
        try:
            IdentityAccessService(db=db, audit_logger=ScopedAuditLogService(db=db)).create_operator(
                email=email, password=password
            )
        except IdentityAccessError_ as exc:
            logger.error("Could not seed the platform operator: %s", exc.message)
            return
        logger.warning("Seeded the PLATFORM_OPERATOR account for %s.", email)
    finally:
        db.close()
