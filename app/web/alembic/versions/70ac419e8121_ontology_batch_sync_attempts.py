"""Persist stable batch request identity and execution attempts separately from receipts."""

import sqlalchemy as sa
from alembic import op

revision = "70ac419e8121"
down_revision = "3c0db1c2a19e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_batch_sync_requests",
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_batch_sync_plans.id"), primary_key=True),
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Preserve pre-existing receipt identities without fabricating past attempts.
    op.execute("""
        INSERT INTO ontology_batch_sync_requests (plan_id, request_id, plan_updated_at)
        SELECT plan_id, request_id, plan_updated_at FROM ontology_batch_sync_receipts
    """)
    op.create_table(
        "ontology_batch_sync_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("initiated_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("ontology_batch_sync_receipts.id")),
        sa.Column("code", sa.String(40)),
        sa.Column("message", sa.String(240)),
        sa.ForeignKeyConstraint(["plan_id", "request_id"], [
            "ontology_batch_sync_requests.plan_id", "ontology_batch_sync_requests.request_id",
        ]),
        sa.CheckConstraint(
            "(status = 'started' AND finished_at IS NULL AND receipt_id IS NULL AND code IS NULL AND message IS NULL)"
            " OR (status IN ('committed','replayed') AND finished_at IS NOT NULL AND receipt_id IS NOT NULL"
            " AND code IS NULL AND message IS NULL)"
            " OR (status IN ('rejected','retryable','outcome_unknown') AND finished_at IS NOT NULL"
            " AND receipt_id IS NULL AND code IS NOT NULL AND message IS NOT NULL)",
            name="ck_ontology_batch_sync_attempt_status",
        ),
    )
    op.create_index("ix_ontology_batch_sync_attempts_plan", "ontology_batch_sync_attempts", ["plan_id", "started_at"])


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM ontology_batch_sync_attempts) THEN
            RAISE EXCEPTION 'Cannot downgrade after batch sync attempts exist';
          END IF;
        END $$;
    """)
    op.drop_table("ontology_batch_sync_attempts")
    op.drop_table("ontology_batch_sync_requests")
