"""Store one local reviewed CDC binding, its checkpoint and applied receipts."""

import sqlalchemy as sa
from alembic import op

revision = "984b2ef31a6c"
down_revision = "db42c1589a0e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_cdc_sandbox_binding",
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("stream_marker", sa.Uuid(), nullable=False),
        sa.Column("physical_digest", sa.String(64), nullable=False),
        sa.Column("applied_through", sa.BigInteger(), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("singleton AND generation = 1 AND applied_through >= 0",
                           name="ck_ontology_cdc_sandbox_binding"),
    )
    op.create_table(
        "ontology_cdc_sandbox_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("binding_key", sa.Boolean(), sa.ForeignKey("ontology_cdc_sandbox_binding.singleton"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("from_exclusive", sa.BigInteger(), nullable=False),
        sa.Column("applied_through", sa.BigInteger(), nullable=False),
        sa.Column("rows_affected", sa.Integer(), nullable=False),
        sa.Column("applied_by", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("binding_key AND generation = 1 AND from_exclusive >= 0 "
                           "AND applied_through > from_exclusive AND applied_through - from_exclusive <= 50 "
                           "AND rows_affected BETWEEN 1 AND applied_through - from_exclusive",
                           name="ck_ontology_cdc_sandbox_receipt"),
    )
    op.create_index("ix_ontology_cdc_sandbox_receipts_history", "ontology_cdc_sandbox_receipts", ["recorded_at", "id"])


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM ontology_cdc_sandbox_binding) THEN
                RAISE EXCEPTION 'Cannot downgrade a reviewed CDC stream and discard its applied checkpoint';
            END IF;
        END $$;
    """)
    op.drop_table("ontology_cdc_sandbox_receipts")
    op.drop_table("ontology_cdc_sandbox_binding")
