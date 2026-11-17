"""Persist narrow table-export sender review, requests and receipt observations."""

import sqlalchemy as sa
from alembic import op

revision = "b31c72f6a9de"
down_revision = "a83a40b7dc19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_table_export_plan",
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("singleton = true", name="ck_ontology_table_export_singleton"),
    )
    op.create_table(
        "ontology_table_export_requests",
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("payload_digest", sa.String(64)),
    )
    op.create_table(
        "ontology_table_export_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("ontology_table_export_requests.request_id"), unique=True, nullable=False),
        sa.Column("rows_affected", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rows_affected BETWEEN 0 AND 100", name="ck_ontology_table_export_rows"),
    )
    op.create_table(
        "ontology_table_export_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("ontology_table_export_requests.request_id"), nullable=False),
        sa.Column("initiated_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("ontology_table_export_receipts.id")),
        sa.Column("code", sa.String(40)),
        sa.Column("message", sa.String(240)),
        sa.CheckConstraint(
            "(status = 'started' AND finished_at IS NULL AND receipt_id IS NULL AND code IS NULL AND message IS NULL)"
            " OR (status IN ('committed','replayed') AND finished_at IS NOT NULL AND receipt_id IS NOT NULL"
            " AND code IS NULL AND message IS NULL)"
            " OR (status IN ('rejected','retryable','outcome_unknown') AND finished_at IS NOT NULL"
            " AND receipt_id IS NULL AND code IS NOT NULL AND message IS NOT NULL)",
            name="ck_ontology_table_export_attempt_status",
        ),
    )
    op.create_index("ix_ontology_table_export_attempt_started", "ontology_table_export_attempts", ["started_at"])


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM ontology_table_export_plan) THEN
            RAISE EXCEPTION 'Cannot downgrade after table export review or attempt evidence exists';
          END IF;
        END $$;
    """)
    op.drop_table("ontology_table_export_attempts")
    op.drop_table("ontology_table_export_receipts")
    op.drop_table("ontology_table_export_requests")
    op.drop_table("ontology_table_export_plan")
