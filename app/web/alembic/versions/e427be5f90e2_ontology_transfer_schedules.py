"""Create sandbox-only transfer scheduling bindings and UTC job slots."""

import sqlalchemy as sa
from alembic import op

revision = "e427be5f90e2"
down_revision = "b29e8b8d4521"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_transfer_schedules",
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_plans.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("armed_by", sa.Uuid(), nullable=False),
        sa.Column("armed_for_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation > 0", name="ck_ontology_transfer_schedules_generation"),
    )
    op.create_table(
        "ontology_transfer_schedule_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_schedules.plan_id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("slot", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("armed_by", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_runs.id")),
        sa.Column("message", sa.String(240)),
        sa.UniqueConstraint("plan_id", "generation", "slot", name="uq_ontology_transfer_schedule_jobs_slot"),
        sa.CheckConstraint("state IN ('pending','dispatched','committed','rejected','outcome_unknown','expired')",
                           name="ck_ontology_transfer_schedule_jobs_state"),
    )
    op.create_index("ix_ontology_transfer_schedule_jobs_due", "ontology_transfer_schedule_jobs", ["state", "dispatch_after"])
    op.create_index("ix_ontology_transfer_schedule_jobs_plan", "ontology_transfer_schedule_jobs", ["plan_id", "slot"])


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM ontology_transfer_schedules) THEN
                RAISE EXCEPTION 'Cannot downgrade after sandbox transfer schedule bindings exist';
            END IF;
        END $$;
    """)
    op.drop_table("ontology_transfer_schedule_jobs")
    op.drop_table("ontology_transfer_schedules")
