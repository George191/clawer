"""Create sandbox-only transfer scheduling bindings and UTC job slots."""

import sqlalchemy as sa
from alembic import op

revision = "a83a40b7dc19"
down_revision = "70ac419e8121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_batch_sync_schedules",
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_batch_sync_plans.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("armed_by", sa.Uuid(), nullable=False),
        sa.Column("execution_scope", sa.String(128), nullable=False),
        sa.Column("planned_time_utc", sa.String(5), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("armed_for_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation > 0", name="ck_ontology_batch_sync_schedules_generation"),
        sa.CheckConstraint("planned_time_utc ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$'",
                           name="ck_ontology_batch_sync_schedules_time"),
    )
    op.create_index("ix_ontology_batch_sync_schedules_time", "ontology_batch_sync_schedules", ["enabled", "planned_time_utc"])
    op.create_table(
        "ontology_batch_sync_schedule_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_batch_sync_schedules.plan_id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("slot", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("armed_by", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("ontology_batch_sync_receipts.id")),
        sa.Column("message", sa.String(240)),
        sa.UniqueConstraint("plan_id", "generation", "slot", name="uq_ontology_batch_sync_schedule_jobs_slot"),
        sa.CheckConstraint("state IN ('pending','dispatched','committed','rejected','outcome_unknown','expired')",
                           name="ck_ontology_batch_sync_schedule_jobs_state"),
        sa.CheckConstraint("(state = 'committed') = (receipt_id IS NOT NULL)",
                           name="ck_ontology_batch_sync_schedule_job_receipt"),
    )
    op.create_index("ix_ontology_batch_sync_schedule_expiry", "ontology_batch_sync_schedule_jobs", ["expires_at"],
                    postgresql_where=sa.text("state IN ('pending','dispatched')"))
    op.create_index("ix_ontology_batch_sync_schedule_jobs_due", "ontology_batch_sync_schedule_jobs", ["state", "dispatch_after"])
    op.create_index("ix_ontology_batch_sync_schedule_jobs_plan", "ontology_batch_sync_schedule_jobs", ["plan_id", "slot"])
    op.add_column("ontology_batch_sync_attempts", sa.Column("scheduled_job_id", sa.Uuid()))
    op.create_foreign_key("fk_ontology_batch_attempt_scheduled_job", "ontology_batch_sync_attempts",
                          "ontology_batch_sync_schedule_jobs", ["scheduled_job_id"], ["id"])


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM ontology_batch_sync_schedules) THEN
                RAISE EXCEPTION 'Cannot downgrade after sandbox transfer schedule bindings exist';
            END IF;
        END $$;
    """)
    op.drop_constraint("fk_ontology_batch_attempt_scheduled_job", "ontology_batch_sync_attempts", type_="foreignkey")
    op.drop_column("ontology_batch_sync_attempts", "scheduled_job_id")
    op.drop_table("ontology_batch_sync_schedule_jobs")
    op.drop_table("ontology_batch_sync_schedules")
