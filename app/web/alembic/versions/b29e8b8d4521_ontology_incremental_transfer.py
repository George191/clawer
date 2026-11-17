"""Record checkpoints for incremental manual sandbox transfers and execution attempts."""

import sqlalchemy as sa
from alembic import op

revision = "b29e8b8d4521"
down_revision = "61a4c8fed182"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ontology_transfer_plans", sa.Column("incremental_cursor", sa.String(63)))
    op.create_check_constraint(
        "ck_ontology_transfer_plans_incremental", "ontology_transfer_plans",
        "incremental_cursor IS NULL OR (write_mode = 'append' AND trigger = 'manual' "
        "AND source_schema = 'ts_transfer_sandbox' AND target_schema = 'ts_transfer_sandbox')",
    )
    op.add_column("ontology_transfer_runs", sa.Column("cursor_through", sa.BigInteger()))
    op.add_column("ontology_transfer_runs", sa.Column("has_more", sa.Boolean()))
    op.drop_index("uq_ontology_transfer_runs_append_route", table_name="ontology_transfer_runs")
    op.create_index("uq_ontology_transfer_runs_append_route", "ontology_transfer_runs",
                    ["source_schema", "source_table", "target_schema", "target_table"], unique=True,
                    postgresql_where=sa.text("write_mode = 'append' AND has_more IS NULL"))
    op.create_table(
        "ontology_transfer_checkpoints",
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_plans.id"), primary_key=True),
        sa.Column("source_table", sa.String(63), nullable=False),
        sa.Column("target_table", sa.String(63), nullable=False),
        sa.Column("cursor_column", sa.String(63), nullable=False),
        sa.Column("source_oid", sa.BigInteger(), nullable=False),
        sa.Column("source_node", sa.BigInteger(), nullable=False),
        sa.Column("target_oid", sa.BigInteger(), nullable=False),
        sa.Column("target_node", sa.BigInteger(), nullable=False),
        sa.Column("cursor_through", sa.BigInteger()),
        sa.UniqueConstraint("source_table", "target_table", name="uq_ontology_transfer_checkpoints_route"),
    )
    op.create_table(
        "ontology_transfer_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_plans.id"), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_runs.id")),
        sa.Column("message", sa.String(240)),
        sa.CheckConstraint("status IN ('started', 'committed', 'replayed', 'rejected', 'outcome_unknown')",
                           name="ck_ontology_transfer_attempts_status"),
    )
    op.create_index("ix_ontology_transfer_attempts_plan", "ontology_transfer_attempts", ["plan_id", "started_at"])


def downgrade() -> None:
    # Restoring the one-time Append constraint cannot safely discard newer evidence.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM ontology_transfer_attempts)
               OR EXISTS (SELECT 1 FROM ontology_transfer_checkpoints)
               OR EXISTS (SELECT 1 FROM ontology_transfer_plans WHERE incremental_cursor IS NOT NULL) THEN
                RAISE EXCEPTION 'Cannot downgrade after sandbox transfer attempts or incremental configuration exist';
            END IF;
        END $$;
    """)
    op.drop_table("ontology_transfer_attempts")
    op.drop_table("ontology_transfer_checkpoints")
    op.drop_index("uq_ontology_transfer_runs_append_route", table_name="ontology_transfer_runs")
    op.create_index("uq_ontology_transfer_runs_append_route", "ontology_transfer_runs",
                    ["source_schema", "source_table", "target_schema", "target_table"], unique=True,
                    postgresql_where=sa.text("write_mode = 'append'"))
    op.drop_column("ontology_transfer_runs", "has_more")
    op.drop_column("ontology_transfer_runs", "cursor_through")
    op.drop_constraint("ck_ontology_transfer_plans_incremental", "ontology_transfer_plans", type_="check")
    op.drop_column("ontology_transfer_plans", "incremental_cursor")
