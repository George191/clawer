"""Provision a default-off durable local synthetic Push inbox."""
import sqlalchemy as sa
from alembic import op

revision = "e61c9af524d7"
down_revision = "ab4e962c7d10"
branch_labels = None
depends_on = None

_STATE = "ontology_push_inbox_state"
_RECEIPTS = "ontology_push_inbox_receipts"


def upgrade() -> None:
    state = op.create_table(
        _STATE,
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column("database_marker", sa.Uuid()),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("window_epoch", sa.BigInteger()),
        sa.Column("window_accepted", sa.Integer(), nullable=False),
        sa.CheckConstraint("singleton IS TRUE AND accepted_count BETWEEN 0 AND 1000 AND window_accepted BETWEEN 0 AND 20 "
                           "AND (window_epoch IS NOT NULL OR window_accepted=0)", name="ck_push_inbox_synthetic_singleton"),
    )
    op.bulk_insert(state, [{"singleton": True, "accepted_count": 0, "window_accepted": 0}])
    op.create_table(
        _RECEIPTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("ordinal", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("body_digest", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("fixture_value", sa.String(56), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ordinal BETWEEN 1 AND 1000 AND schema_version=1 AND entity_id > 0 "
                           "AND body_digest ~ '^[0-9a-f]{64}$' AND fixture_value ~ '^fixture-[a-z0-9-]{1,48}$'",
                           name="ck_push_inbox_synthetic_receipt"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute(f"LOCK TABLE {_STATE}, {_RECEIPTS} IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {_RECEIPTS} LIMIT 1")):
        raise RuntimeError("Cannot remove a synthetic inbox containing accepted records")
    op.drop_table(_RECEIPTS)
    op.drop_table(_STATE)
