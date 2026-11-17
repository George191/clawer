"""Store pilot batch-sync definitions and committed target receipts."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "3c0db1c2a19e"
down_revision = "e427be5f90e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_batch_sync_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("source_schema", sa.String(128), nullable=False),
        sa.Column("source_table", sa.String(128), nullable=False),
        sa.Column("source_key_column", sa.String(128), nullable=False),
        sa.Column("target_table", sa.String(63), nullable=False),
        sa.Column("target_oid", sa.BigInteger(), nullable=False),
        sa.Column("target_file_node", sa.BigInteger(), nullable=False),
        sa.Column("mapping", JSONB(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("jsonb_typeof(mapping) = 'array' AND jsonb_array_length(mapping) BETWEEN 1 AND 32",
                           name="ck_ontology_batch_sync_mapping"),
    )
    op.create_index("ix_ontology_batch_sync_plans_connection", "ontology_batch_sync_plans", ["connection_id", "updated_at"])
    op.create_table(
        "ontology_batch_sync_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_batch_sync_plans.id"), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("initiated_by", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("source_schema", sa.String(128), nullable=False),
        sa.Column("source_table", sa.String(128), nullable=False),
        sa.Column("target_table", sa.String(63), nullable=False),
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows_affected", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plan_id", "request_id", name="uq_ontology_batch_sync_request"),
        sa.CheckConstraint("rows_affected BETWEEN 0 AND 100", name="ck_ontology_batch_sync_rows"),
    )
    op.create_index("ix_ontology_batch_sync_receipts_plan", "ontology_batch_sync_receipts", ["plan_id", "recorded_at"])


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM ontology_batch_sync_plans) THEN
            RAISE EXCEPTION 'Cannot downgrade after batch sync plans or receipts exist';
          END IF;
        END $$;
    """)
    op.drop_table("ontology_batch_sync_receipts")
    op.drop_table("ontology_batch_sync_plans")
