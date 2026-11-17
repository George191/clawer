"""Persist terminal, auditable cancellation of one ordinary Daily UTC transfer job."""
import sqlalchemy as sa
from alembic import op

revision = "21fe23589f8d"
down_revision = "67bd926fa4d1"
branch_labels = None
depends_on = None

_TABLE = "ontology_transfer_schedule_jobs"
_STATE = "ck_ontology_transfer_schedule_jobs_state"
_CANCEL = "ck_transfer_schedule_job_cancel"
_LEGACY = "'pending','dispatched','committed','rejected','outcome_unknown','expired'"


def upgrade():
    op.add_column(_TABLE, sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(_TABLE, sa.Column("canceled_by", sa.Uuid(), nullable=True))
    op.drop_constraint(_STATE, _TABLE, type_="check")
    op.create_check_constraint(_STATE, _TABLE, f"state IN ({_LEGACY},'canceled')")
    op.create_check_constraint(_CANCEL, _TABLE, """
        ((state = 'canceled' AND canceled_by IS NOT NULL AND canceled_at IS NOT NULL
           AND finished_at = canceled_at AND canceled_at >= slot AND canceled_at < expires_at
           AND receipt_id IS NULL)
         OR (state <> 'canceled' AND canceled_by IS NULL AND canceled_at IS NULL)) IS TRUE
    """)

    op.create_index("ix_ontology_transfer_attempts_request", "ontology_transfer_attempts", ["plan_id", "request_id"])


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
    bind.execute(sa.text(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE"))
    if bind.scalar(sa.text(f"SELECT 1 FROM {_TABLE} WHERE state = 'canceled' LIMIT 1")):
        raise RuntimeError("Cannot downgrade while terminal Daily UTC cancellation evidence exists")
    op.drop_index("ix_ontology_transfer_attempts_request", table_name="ontology_transfer_attempts")
    op.drop_constraint(_CANCEL, _TABLE, type_="check")
    op.drop_constraint(_STATE, _TABLE, type_="check")
    op.create_check_constraint(_STATE, _TABLE, f"state IN ({_LEGACY})")
    op.drop_column(_TABLE, "canceled_by")
    op.drop_column(_TABLE, "canceled_at")
