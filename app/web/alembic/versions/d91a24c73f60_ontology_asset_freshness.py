"""Record physical transfer evidence and exact-sandbox write-cadence checks."""

import sqlalchemy as sa
from alembic import op

revision = "d91a24c73f60"
down_revision = "b6c45a7e9130"
branch_labels = None
depends_on = None

_RUNS = "ontology_transfer_runs"
_POLICIES = "ontology_asset_freshness_policies"
_TRANSITIONS = "ontology_asset_freshness_transitions"
_RECEIPT_FILTER = "rows_affected > 0 AND target_oid IS NOT NULL AND target_filenode IS NOT NULL"


def upgrade() -> None:
    op.add_column(_RUNS, sa.Column("target_oid", sa.BigInteger()))
    op.add_column(_RUNS, sa.Column("target_filenode", sa.BigInteger()))
    op.create_check_constraint("ck_ontology_transfer_runs_physical_target", _RUNS,
                               "(target_oid IS NULL) = (target_filenode IS NULL)")
    op.create_index("ix_ontology_transfer_runs_freshness", _RUNS,
                    ["plan_id", "plan_updated_at", "target_oid", "target_filenode", "recorded_at", "id"],
                    postgresql_where=sa.text(_RECEIPT_FILTER))
    op.create_table(
        _POLICIES,
        sa.Column("target_table", sa.String(63), primary_key=True),
        sa.Column("target_oid", sa.BigInteger(), nullable=False),
        sa.Column("target_filenode", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_plans.id"), nullable=False),
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("threshold_minutes", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(15), nullable=False),
        sa.Column("reason", sa.String(40)),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_recorded_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey(f"{_RUNS}.id")),
        sa.CheckConstraint("threshold_minutes BETWEEN 5 AND 10080 AND generation > 0",
                           name="ck_ontology_asset_freshness_policy_values"),
        sa.CheckConstraint("state IN ('not_checked','healthy','overdue','unknown','paused')",
                           name="ck_ontology_asset_freshness_policy_state"),
    )
    op.create_index("ix_ontology_asset_freshness_due", _POLICIES, ["enabled", "next_check_at", "target_table"])
    op.create_table(
        _TRANSITIONS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_table", sa.String(63), sa.ForeignKey(f"{_POLICIES}.target_table"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(15), nullable=False),
        sa.Column("reason", sa.String(40)),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_recorded_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey(f"{_RUNS}.id")),
        sa.CheckConstraint("state IN ('healthy','overdue','unknown') AND generation > 0",
                           name="ck_ontology_asset_freshness_transition_state"),
    )
    op.create_index("ix_ontology_asset_freshness_history", _TRANSITIONS, ["target_table", "checked_at", "id"])


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {_POLICIES}) OR EXISTS (SELECT 1 FROM {_RUNS} WHERE target_oid IS NOT NULL) THEN
                RAISE EXCEPTION 'Cannot downgrade after recording asset freshness evidence';
            END IF;
        END $$;
    """)
    op.drop_table(_TRANSITIONS)
    op.drop_table(_POLICIES)
    op.drop_index("ix_ontology_transfer_runs_freshness", table_name=_RUNS)
    op.drop_constraint("ck_ontology_transfer_runs_physical_target", _RUNS, type_="check")
    op.drop_column(_RUNS, "target_filenode")
    op.drop_column(_RUNS, "target_oid")
