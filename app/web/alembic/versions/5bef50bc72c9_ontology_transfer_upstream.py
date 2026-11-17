"""Persist one-hop sandbox upstream commit events, bindings, and durable jobs."""

import sqlalchemy as sa
from alembic import op

revision = "5bef50bc72c9"
down_revision = "b31c72f6a9de"
branch_labels = None
depends_on = None

_OLD = "(trigger = 'manual' AND schedule_time IS NULL) OR (trigger = 'scheduled' AND schedule_time IS NOT NULL AND schedule_time ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$')"


def upgrade() -> None:
    op.drop_constraint("ck_ontology_transfer_plans_schedule", "ontology_transfer_plans", type_="check")
    op.create_check_constraint(
        "ck_ontology_transfer_plans_schedule", "ontology_transfer_plans", _OLD +
        " OR (trigger = 'upstream' AND schedule_time IS NULL AND write_mode = 'upsert' "
        "AND source_schema = 'ts_transfer_sandbox' AND target_schema = 'ts_transfer_sandbox')",
    )
    op.create_table(
        "ontology_transfer_commit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("parent_plan_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_plans.id"), nullable=False),
        sa.Column("parent_receipt_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_runs.id"), nullable=False, unique=True),
        sa.Column("parent_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_oid", sa.BigInteger(), nullable=False),
        sa.Column("source_node", sa.BigInteger(), nullable=False),
        sa.Column("target_oid", sa.BigInteger(), nullable=False),
        sa.Column("target_node", sa.BigInteger(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ontology_transfer_commit_event_parent", "ontology_transfer_commit_events", ["parent_plan_id", "id"])
    op.create_index("ix_ontology_transfer_commit_event_time", "ontology_transfer_commit_events",
                    ["parent_plan_id", "recorded_at", "id"])
    op.create_table(
        "ontology_transfer_upstream_triggers",
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_plans.id"), primary_key=True),
        sa.Column("parent_plan_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_plans.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("armed_by", sa.Uuid(), nullable=False),
        sa.Column("parent_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("child_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff_event_id", sa.BigInteger(), nullable=False),
        sa.Column("physical_digest", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("plan_id <> parent_plan_id AND generation > 0 AND cutoff_event_id >= 0",
                           name="ck_ontology_transfer_upstream_trigger"),
    )
    op.create_table(
        "ontology_transfer_upstream_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_upstream_triggers.plan_id"), nullable=False),
        sa.Column("event_id", sa.BigInteger(), sa.ForeignKey("ontology_transfer_commit_events.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("child_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("armed_by", sa.Uuid(), nullable=False),
        sa.Column("dispatch_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_runs.id")),
        sa.Column("message", sa.String(240)),
        sa.UniqueConstraint("plan_id", "generation", "event_id", name="uq_ontology_transfer_upstream_jobs_event"),
        sa.CheckConstraint("state IN ('pending','dispatched','committed','rejected','outcome_unknown','expired')",
                           name="ck_ontology_transfer_upstream_job_state"),
        sa.CheckConstraint("(state = 'committed') = (receipt_id IS NOT NULL)", name="ck_ontology_transfer_upstream_job_receipt"),
    )
    op.create_index("ix_ontology_transfer_upstream_jobs_due", "ontology_transfer_upstream_jobs", ["state", "dispatch_after"])
    op.create_index("ix_ontology_transfer_upstream_jobs_plan", "ontology_transfer_upstream_jobs", ["plan_id", "event_id"])
    op.create_index("ix_ontology_transfer_upstream_expiry", "ontology_transfer_upstream_jobs", ["expires_at"],
                    postgresql_where=sa.text("state IN ('pending','dispatched')"))
    op.add_column("ontology_transfer_attempts", sa.Column("upstream_job_id", sa.Uuid()))
    op.create_foreign_key("fk_ontology_transfer_attempt_upstream_job", "ontology_transfer_attempts",
                          "ontology_transfer_upstream_jobs", ["upstream_job_id"], ["id"])


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM ontology_transfer_upstream_triggers)
                OR EXISTS (SELECT 1 FROM ontology_transfer_commit_events)
                OR EXISTS (SELECT 1 FROM ontology_transfer_plans WHERE trigger = 'upstream') THEN
                RAISE EXCEPTION 'Cannot downgrade after upstream configuration or commit events exist';
            END IF;
        END $$;
    """)
    op.drop_constraint("fk_ontology_transfer_attempt_upstream_job", "ontology_transfer_attempts", type_="foreignkey")
    op.drop_column("ontology_transfer_attempts", "upstream_job_id")
    op.drop_table("ontology_transfer_upstream_jobs")
    op.drop_table("ontology_transfer_upstream_triggers")
    op.drop_table("ontology_transfer_commit_events")
    op.drop_constraint("ck_ontology_transfer_plans_schedule", "ontology_transfer_plans", type_="check")
    op.create_check_constraint("ck_ontology_transfer_plans_schedule", "ontology_transfer_plans", _OLD)
