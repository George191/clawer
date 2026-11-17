"""Persist reviewed compound upstream triggers and exact job event sets."""

import sqlalchemy as sa
from alembic import op

revision = "b6c45a7e9130"
down_revision = "aa4c7d2e1f90"
branch_labels = None
depends_on = None

_TRIGGERS = "ontology_transfer_upstream_triggers"
_JOBS = "ontology_transfer_upstream_jobs"
_PARENTS = "ontology_transfer_upstream_parents"
_EVENTS = "ontology_transfer_upstream_job_events"


def upgrade() -> None:
    for table in (_TRIGGERS, _JOBS):
        op.add_column(table, sa.Column("operator", sa.String(3), server_default="OR", nullable=False))
        op.alter_column(table, "operator", server_default=None)
    op.create_check_constraint("ck_ontology_transfer_upstream_job_operator", _JOBS, "operator IN ('AND','OR')")
    op.create_unique_constraint("uq_ontology_transfer_upstream_job_scope", _JOBS, ["id", "plan_id", "generation"])
    op.create_table(
        _PARENTS,
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey(f"{_TRIGGERS}.plan_id"), primary_key=True),
        sa.Column("parent_plan_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_plans.id"), primary_key=True),
        sa.Column("parent_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff_event_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("plan_id <> parent_plan_id AND cutoff_event_id >= 0",
                           name="ck_ontology_transfer_upstream_parent"),
    )
    op.create_table(
        _EVENTS,
        sa.Column("plan_id", sa.Uuid(), primary_key=True),
        sa.Column("generation", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.BigInteger(), sa.ForeignKey("ontology_transfer_commit_events.id"), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["job_id", "plan_id", "generation"],
                                [f"{_JOBS}.id", f"{_JOBS}.plan_id", f"{_JOBS}.generation"],
                                name="fk_ontology_transfer_job_event_scope"),
        sa.UniqueConstraint("job_id", "event_id", name="uq_ontology_transfer_upstream_job_event"),
    )
    op.execute(f"""
        INSERT INTO {_PARENTS} (plan_id, parent_plan_id, parent_updated_at, cutoff_event_id)
        SELECT plan_id, parent_plan_id, parent_updated_at, cutoff_event_id FROM {_TRIGGERS}
    """)
    op.execute(f"""
        INSERT INTO {_EVENTS} (plan_id, generation, event_id, job_id)
        SELECT plan_id, generation, event_id, id FROM {_JOBS}
    """)
    op.drop_constraint("ck_ontology_transfer_upstream_trigger", _TRIGGERS, type_="check")
    op.create_check_constraint("ck_ontology_transfer_upstream_trigger", _TRIGGERS,
                               "generation > 0 AND operator IN ('AND','OR')")
    # The old parent column owns a generated foreign key; PostgreSQL drops it
    # with its column. Parent identity now lives in the reviewed membership.
    for column in ("parent_plan_id", "parent_updated_at", "cutoff_event_id"):
        op.drop_column(_TRIGGERS, column)


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {_TRIGGERS}) OR EXISTS (SELECT 1 FROM {_JOBS}) THEN
                RAISE EXCEPTION 'Cannot downgrade after compound upstream configuration or jobs exist';
            END IF;
        END $$;
    """)
    op.drop_table(_EVENTS)
    op.drop_table(_PARENTS)
    op.drop_constraint("uq_ontology_transfer_upstream_job_scope", _JOBS, type_="unique")
    op.drop_constraint("ck_ontology_transfer_upstream_job_operator", _JOBS, type_="check")
    op.drop_constraint("ck_ontology_transfer_upstream_trigger", _TRIGGERS, type_="check")
    op.add_column(_TRIGGERS, sa.Column("parent_plan_id", sa.Uuid(), nullable=False))
    op.create_foreign_key("ontology_transfer_upstream_triggers_parent_plan_id_fkey", _TRIGGERS,
                          "ontology_transfer_plans", ["parent_plan_id"], ["id"])
    op.add_column(_TRIGGERS, sa.Column("parent_updated_at", sa.DateTime(timezone=True), nullable=False))
    op.add_column(_TRIGGERS, sa.Column("cutoff_event_id", sa.BigInteger(), nullable=False))
    op.create_check_constraint("ck_ontology_transfer_upstream_trigger", _TRIGGERS,
                               "plan_id <> parent_plan_id AND generation > 0 AND cutoff_event_id >= 0")
    for table in (_TRIGGERS, _JOBS):
        op.drop_column(table, "operator")
