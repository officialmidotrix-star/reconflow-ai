"""tenancy: platform-operator role, users.organization_id, audit_log_entries.organization_id

What this does to an EXISTING single-organization database:

  1. adds the PLATFORM_OPERATOR value to the userrole enum (PostgreSQL);
  2. adds users.organization_id and assigns every existing user to the one
     existing organization;
  3. promotes ONE explicitly named existing account to PLATFORM_OPERATOR
     (it is detached from the organization) - given as
         alembic -x operator_email=you@example.com upgrade head
     There is no guessing: without it the migration refuses to run on a
     database that has users but no operator, because once POST /users is
     closed and organization creation is operator-only, nobody could ever
     onboard a client again;
  4. adds the CHECK constraint that makes it structurally impossible for a
     client user to be an operator (and for an operator to belong to a client);
  5. adds audit_log_entries.organization_id and backfills it from each
     entry's analysis -> branch -> organization, falling back to the acting
     user's organization.

Every pre-condition is checked BEFORE any change is made. Optional:
    -x org_id=<uuid>   choose the organization when several exist (not
                       expected: the old code allowed exactly one).

Frozen: no application imports.
"""
from alembic import context, op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

OPERATOR = "PLATFORM_OPERATOR"
CHECK_NAME = "ck_users_operator_org"
CHECK_SQL = (
    "(role = 'PLATFORM_OPERATOR' AND organization_id IS NULL) "
    "OR (role <> 'PLATFORM_OPERATOR' AND organization_id IS NOT NULL)"
)


class MigrationRefused(Exception):
    pass


def _x() -> dict:
    return context.get_x_argument(as_dictionary=True)


def _preflight(bind) -> tuple[str | None, str | None]:
    """Returns (organization_id_to_assign, operator_email_to_promote). Raises
    MigrationRefused with an explicit message if anything is ambiguous."""
    x = _x()
    operator_email = x.get("operator_email")
    users = bind.execute(sa.text("SELECT id, email, CAST(role AS VARCHAR) AS role FROM users")).fetchall()
    orgs = [r[0] for r in bind.execute(sa.text("SELECT id FROM organizations")).fetchall()]
    existing_operators = [u for u in users if u.role == OPERATOR]

    if not users:
        return None, None  # fresh database: nothing to migrate; the startup seed creates the operator

    if operator_email:
        match = [u for u in users if u.email == operator_email]
        if not match:
            raise MigrationRefused(
                f"-x operator_email={operator_email!r} matches no existing user. Existing emails: "
                + ", ".join(sorted(u.email for u in users))
            )
    elif not existing_operators:
        raise MigrationRefused(
            "This database has users but no platform operator. Re-run with "
            "`alembic -x operator_email=<email of the account that will become the operator> upgrade head`. "
            "Refusing to guess: without an operator nobody could onboard clients after this migration."
        )

    needs_org = [u for u in users if u.role != OPERATOR and u.email != operator_email]
    org_id = x.get("org_id")
    if needs_org:
        if org_id:
            if org_id not in orgs:
                raise MigrationRefused(f"-x org_id={org_id!r} is not an existing organization.")
        elif len(orgs) == 1:
            org_id = orgs[0]
        elif not orgs:
            raise MigrationRefused(
                f"{len(needs_org)} user(s) would need an organization but none exists. "
                "Create the organization first, or promote more accounts."
            )
        else:
            raise MigrationRefused(
                f"{len(orgs)} organizations exist; choose one for the existing users with -x org_id=<uuid>."
            )
    return org_id, operator_email


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    org_id, operator_email = _preflight(bind)

    # 1. enum value (PostgreSQL). Must be committed before the new value is
    #    used anywhere, so it runs in its own autocommit block.
    if dialect == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'PLATFORM_OPERATOR'")

    # 2. columns
    with op.batch_alter_table("users") as batch:
        if dialect == "sqlite":
            # SQLite stores the enum as VARCHAR(15) (longest old label); widen it
            # so the column matches the models. PostgreSQL got its new value above.
            batch.alter_column(
                "role",
                existing_type=sa.String(length=15),
                type_=sa.Enum(
                    "OWNER", "FINANCE_MANAGER", "ACCOUNTANT", "OPS_MANAGER", "AUDITOR",
                    "PLATFORM_OPERATOR", name="userrole",
                ),
                existing_nullable=False,
            )
        batch.add_column(sa.Column("organization_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key("fk_users_organization_id", "organizations", ["organization_id"], ["id"])
        batch.create_index("ix_users_organization_id", ["organization_id"])
    with op.batch_alter_table("audit_log_entries") as batch:
        batch.add_column(sa.Column("organization_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key("fk_audit_log_entries_organization_id", "organizations", ["organization_id"], ["id"])
        batch.create_index("ix_audit_log_entries_organization_id", ["organization_id"])

    # 3. backfill users
    if operator_email:
        op.execute(
            sa.text(
                "UPDATE users SET role = 'PLATFORM_OPERATOR', organization_id = NULL WHERE email = :e"
            ).bindparams(e=operator_email)
        )
    if org_id:
        op.execute(
            sa.text(
                "UPDATE users SET organization_id = :o WHERE CAST(role AS VARCHAR) <> 'PLATFORM_OPERATOR'"
            ).bindparams(o=org_id)
        )

    # 4. the invariant
    with op.batch_alter_table("users") as batch:
        batch.create_check_constraint(CHECK_NAME, CHECK_SQL)

    # 5. backfill audit log: analysis -> branch -> organization, then the actor's organization
    op.execute(
        "UPDATE audit_log_entries SET organization_id = ("
        "  SELECT b.organization_id FROM analyses a JOIN branches b ON b.id = a.branch_id"
        "  WHERE a.id = audit_log_entries.analysis_id"
        ") WHERE analysis_id IS NOT NULL"
    )
    op.execute(
        "UPDATE audit_log_entries SET organization_id = ("
        "  SELECT u.organization_id FROM users u WHERE u.id = audit_log_entries.user_id"
        ") WHERE organization_id IS NULL"
    )


def downgrade() -> None:
    """Back to the single-organization schema. Operator accounts become
    OWNERs (the old code has no such role). The PLATFORM_OPERATOR enum value
    itself stays in the PostgreSQL type - PostgreSQL cannot remove an enum
    value - which is harmless once no row uses it."""
    # Order matters: the CHECK constraint must go first, because an operator
    # turned into an OWNER (with no organization) would violate it.
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint(CHECK_NAME, type_="check")
    op.execute("UPDATE users SET role = 'OWNER' WHERE CAST(role AS VARCHAR) = 'PLATFORM_OPERATOR'")
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_organization_id")
        batch.drop_constraint("fk_users_organization_id", type_="foreignkey")
        batch.drop_column("organization_id")
    with op.batch_alter_table("audit_log_entries") as batch:
        batch.drop_index("ix_audit_log_entries_organization_id")
        batch.drop_constraint("fk_audit_log_entries_organization_id", type_="foreignkey")
        batch.drop_column("organization_id")
