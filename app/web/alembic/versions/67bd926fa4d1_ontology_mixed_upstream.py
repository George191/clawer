"""Persist pre-slot observation and first UTC decision for mixed upstream triggers."""
import sqlalchemy as sa
from alembic import op

revision = "67bd926fa4d1"
down_revision = "b66fbad735c2"
branch_labels = None
depends_on = None

_TRIGGER = "ontology_transfer_upstream_triggers"
_JOB = "ontology_transfer_upstream_jobs"
_SLOT = "ontology_transfer_upstream_mixed_slots"


def upgrade():
    op.add_column(_TRIGGER, sa.Column("mixed_time_utc", sa.String(5)))
    op.create_check_constraint("ck_transfer_upstream_mixed_clock", _TRIGGER,
                               "mixed_time_utc ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'")
    op.create_index("ix_transfer_upstream_mixed_active", _TRIGGER, ["mixed_time_utc", "plan_id"],
                    postgresql_where=sa.text("enabled AND mixed_time_utc IS NOT NULL"))
    op.add_column(_JOB, sa.Column("mixed_slot", sa.DateTime(timezone=True)))
    op.create_unique_constraint("uq_transfer_upstream_mixed_job_slot", _JOB, ["plan_id", "generation", "mixed_slot"])
    op.create_check_constraint("ck_transfer_upstream_mixed_job_slot", _JOB,
                               "mixed_slot IS NULL OR expires_at = mixed_slot + interval '5 minutes'")
    op.create_table(
        _SLOT,
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey(_TRIGGER + ".plan_id"), primary_key=True),
        sa.Column("generation", sa.Integer(), primary_key=True),
        sa.Column("slot", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("rule_digest", sa.String(64)),
        sa.Column("event_ids", sa.ARRAY(sa.BigInteger())),
        sa.Column("rule_parent_ids", sa.ARRAY(sa.Uuid())),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey(_JOB + ".id"), unique=True),
        sa.CheckConstraint("generation > 0 AND slot = date_trunc('minute', slot)",
                        name="ck_transfer_upstream_mixed_slot"),
        sa.CheckConstraint("""(
            (observed_at IS NULL AND rule_digest IS NULL AND event_ids IS NULL AND rule_parent_ids IS NULL
                AND decided_at IS NOT NULL)
            OR
            (observed_at >= slot - interval '5 minutes' AND observed_at < slot
              AND rule_digest ~ '^[0-9a-f]{64}$' AND cardinality(event_ids) BETWEEN 1 AND 3
              AND array_position(event_ids, NULL) IS NULL AND cardinality(rule_parent_ids) BETWEEN 1 AND 3
              AND array_position(rule_parent_ids, NULL) IS NULL)
        ) IS TRUE""", name="ck_transfer_upstream_mixed_observation"),
        sa.CheckConstraint("""(decided_at IS NULL OR (decided_at >= slot AND decided_at < slot + interval '1 minute'))
            AND (job_id IS NULL OR (decided_at IS NOT NULL AND observed_at IS NOT NULL))""",
                        name="ck_transfer_upstream_mixed_decision"),
    )
    op.create_index("ix_transfer_upstream_mixed_history", _SLOT, ["plan_id", "slot", "generation"],
                    postgresql_where=sa.text("decided_at IS NOT NULL"))


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
    bind.execute(sa.text(f"LOCK TABLE {_TRIGGER}, {_JOB}, {_SLOT} IN ACCESS EXCLUSIVE MODE"))
    for table, clause in (
        (_TRIGGER, "mixed_time_utc IS NOT NULL"), (_JOB, "mixed_slot IS NOT NULL"), (_SLOT, "true"),
    ):
        if bind.scalar(sa.text(f"SELECT 1 FROM {table} WHERE {clause} LIMIT 1")):
            raise RuntimeError("Cannot remove reviewed mixed upstream scheduling evidence")
    op.drop_table(_SLOT)
    op.drop_constraint("ck_transfer_upstream_mixed_job_slot", _JOB, type_="check")
    op.drop_constraint("uq_transfer_upstream_mixed_job_slot", _JOB, type_="unique")
    op.drop_column(_JOB, "mixed_slot")
    op.drop_index("ix_transfer_upstream_mixed_active", table_name=_TRIGGER)
    op.drop_constraint("ck_transfer_upstream_mixed_clock", _TRIGGER, type_="check")
    op.drop_column(_TRIGGER, "mixed_time_utc")
