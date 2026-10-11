"""numbers: treatment / direction / claimable amount on discrepancies, cancellation confirmations

What changes for existing data:
  * discrepancies get four new columns. Rows written before this migration are
    labelled honestly rather than guessed: Unexpected Settlement -> TO_VERIFY,
    Cancelled After Preparation -> NEEDS_CONFIRMATION, everything else ->
    NEEDS_RERUN ("calculated before these rules; run the analysis again").
    Nothing is deleted or re-computed, so the headline figure of an old analysis
    shows 0 recoverable until it is run again - never an invented number.
  * discrepancy_runs get excluded_refund_void_count (0 for old runs).
  * new table cancellation_confirmations.

Frozen: no application imports.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TREATMENTS = ("RECOVERABLE", "NEEDS_CONFIRMATION", "NEEDS_CONTRACT", "TO_VERIFY", "NOT_CLAIMABLE", "NEEDS_RERUN")


def upgrade() -> None:
    with op.batch_alter_table("discrepancies") as batch:
        batch.add_column(sa.Column("treatment", sa.Enum(*TREATMENTS, name="treatment", native_enum=False, length=24), nullable=True))
        batch.add_column(sa.Column("direction", sa.Enum("UNDERPAID", "OVERPAID", "NOT_APPLICABLE", name="direction", native_enum=False, length=16), nullable=True))
        batch.add_column(sa.Column("claimable_amount", sa.Numeric(14, 2), nullable=True))
        batch.add_column(sa.Column("order_reference", sa.String(length=255), nullable=True))

    op.execute(
        "UPDATE discrepancies SET treatment = CASE CAST(category AS VARCHAR) "
        "WHEN 'UNEXPECTED_SETTLEMENT' THEN 'TO_VERIFY' "
        "WHEN 'CANCELLED_AFTER_PREPARATION' THEN 'NEEDS_CONFIRMATION' "
        "ELSE 'NEEDS_RERUN' END"
    )
    with op.batch_alter_table("discrepancies") as batch:
        batch.alter_column(
            "treatment",
            existing_type=sa.Enum(*TREATMENTS, name="treatment", native_enum=False, length=24),
            nullable=False,
        )

    with op.batch_alter_table("discrepancy_runs") as batch:
        batch.add_column(sa.Column("excluded_refund_void_count", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("discrepancy_runs") as batch:
        batch.alter_column("excluded_refund_void_count", existing_type=sa.Integer(), server_default=None, existing_nullable=False)

    op.create_table(
        "cancellation_confirmations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("analysis_id", sa.String(length=36), nullable=False),
        sa.Column("order_reference", sa.String(length=255), nullable=False),
        sa.Column("cancelled_by", sa.Enum("CUSTOMER", "PLATFORM", "RESTAURANT", name="cancelledby", native_enum=False, length=16), nullable=False),
        sa.Column("confirmed_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_id", "order_reference", name="uq_cancellation_order"),
    )
    op.create_index("ix_cancellation_confirmations_analysis_id", "cancellation_confirmations", ["analysis_id"])


def downgrade() -> None:
    op.drop_index("ix_cancellation_confirmations_analysis_id", table_name="cancellation_confirmations")
    op.drop_table("cancellation_confirmations")
    with op.batch_alter_table("discrepancy_runs") as batch:
        batch.drop_column("excluded_refund_void_count")
    with op.batch_alter_table("discrepancies") as batch:
        batch.drop_column("order_reference")
        batch.drop_column("claimable_amount")
        batch.drop_column("direction")
        batch.drop_column("treatment")
