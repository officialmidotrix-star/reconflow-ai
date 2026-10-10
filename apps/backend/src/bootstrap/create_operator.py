"""
Create (or explicitly promote) the platform-operator account.

    python -m bootstrap.create_operator --email you@example.com
    python -m bootstrap.create_operator --email existing@example.com --promote-existing

Run from apps/backend/src with RECONFLOW_DATABASE_URL pointing at the
target database. The password is read from a hidden prompt (or the
environment variable named by --password-env) - never from argv, so it
does not land in shell history or the process list.

This is the ONLY way, besides the startup seed, that a platform operator
can come into existence: there is deliberately no HTTP route for it.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from bootstrap.access import ScopedAuditLogService
from bootstrap.database import SessionLocal
from bootstrap.schema import ensure_schema
from modules.identity_access.exceptions import IdentityAccessError_
from modules.identity_access.models import User, UserRole
from modules.identity_access.service import IdentityAccessService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or promote a platform operator.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password-env", help="Read the password from this environment variable.")
    parser.add_argument(
        "--promote-existing", action="store_true",
        help="Turn an EXISTING user into the operator (detaches it from its organization).",
    )
    args = parser.parse_args(argv)

    # Import every module so all tables are registered before first use.
    import bootstrap.wiring  # noqa: F401

    ensure_schema()
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == args.email).first()
        if args.promote_existing:
            if existing is None:
                print(f"No user with email {args.email}.", file=sys.stderr)
                return 1
            existing.role = UserRole.PLATFORM_OPERATOR
            existing.organization_id = None
            db.commit()
            print(f"{args.email} is now a platform operator.")
            return 0

        if existing is not None:
            print(f"A user with email {args.email} already exists "
                  f"(role {existing.role.value}); use --promote-existing to promote it.", file=sys.stderr)
            return 1
        password = os.environ.get(args.password_env) if args.password_env else None
        if not password:
            password = getpass.getpass("Password for the new operator: ")
            if password != getpass.getpass("Repeat password: "):
                print("Passwords do not match.", file=sys.stderr)
                return 1
        if len(password) < 12:
            print("Use a password of at least 12 characters.", file=sys.stderr)
            return 1
        try:
            IdentityAccessService(db=db, audit_logger=ScopedAuditLogService(db=db)).create_operator(
                email=args.email, password=password
            )
        except IdentityAccessError_ as exc:
            print(exc.message, file=sys.stderr)
            return 1
        print(f"Platform operator {args.email} created.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
