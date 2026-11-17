"""Store committed sandbox transfer receipts."""

import sqlalchemy as sa
from alembic import op

revision = "61a4c8fed182"
down_revision = "2f6f09ba33a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_transfer_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_plans.id"), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("initiated_by", sa.Uuid(), nullable=False),
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_schema", sa.String(63), nullable=False),
        sa.Column("source_table", sa.String(63), nullable=False),
        sa.Column("target_schema", sa.String(63), nullable=False),
        sa.Column("target_table", sa.String(63), nullable=False),
        sa.Column("write_mode", sa.String(6), nullable=False),
        sa.Column("rows_affected", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plan_id", "request_id", name="uq_ontology_transfer_runs_request"),
        sa.CheckConstraint("rows_affected BETWEEN 0 AND 100", name="ck_ontology_transfer_runs_size"),
        sa.CheckConstraint("write_mode IN ('append', 'upsert')", name="ck_ontology_transfer_runs_mode"),
        sa.CheckConstraint("source_schema = 'ts_transfer_sandbox' AND target_schema = 'ts_transfer_sandbox' "
                           "AND source_table <> target_table", name="ck_ontology_transfer_runs_sandbox"),
    )
    op.create_index("ix_ontology_transfer_runs_plan", "ontology_transfer_runs", ["plan_id", "recorded_at"])
    op.create_index("ix_ontology_transfer_runs_source", "ontology_transfer_runs",
                    ["source_schema", "source_table", "recorded_at"])
    op.create_index("ix_ontology_transfer_runs_target", "ontology_transfer_runs",
                    ["target_schema", "target_table", "recorded_at"])
    op.create_index("uq_ontology_transfer_runs_append_route", "ontology_transfer_runs",
                    ["source_schema", "source_table", "target_schema", "target_table"], unique=True,
                    postgresql_where=sa.text("write_mode = 'append'"))


def downgrade() -> None:
    op.drop_table("ontology_transfer_runs")
