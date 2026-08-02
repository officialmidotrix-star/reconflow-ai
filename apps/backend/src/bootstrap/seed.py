"""
One-time helper to create the initial OWNER account when the database is
empty. Only runs when RECONFLOW_SEED_OWNER_EMAIL and
RECONFLOW_SEED_OWNER_PASSWORD are both set, and only if no user with that
email already exists - safe to leave the env vars set or remove them
after first use.
"""
from __future__ import annotations
import logging
import os

from bootstrap.database import SessionLocal, create_all_tables
from modules.identity_access.models import User, UserRole
from modules.identity_access.security import hash_password

logger = logging.getLogger("reconflow.bootstrap.seed")


def run_owner_seed() -> None:
    email = os.environ.get("RECONFLOW_SEED_OWNER_EMAIL")
    password = os.environ.get("RECONFLOW_SEED_OWNER_PASSWORD")
    if not email or not password:
        return

    create_all_tables()

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            logger.info("Seed owner 's' already exists - skipping.", email)
            return
        user = User(
            email=email,
            password_hash=hash_password(password),
            role=UserRole.OWNER,
            is_active=True,
        )
        db.add(user)
        db.commit()
        logger.warning("Seeded initial OWNER account for 's'.", email)
    finally:
        db.close()
