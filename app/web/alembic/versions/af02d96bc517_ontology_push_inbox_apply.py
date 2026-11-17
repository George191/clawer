"""Store a synthetic Push apply binding and idempotent manual results."""
import sqlalchemy as sa
from alembic import op

revision = "af02d96bc517"
down_revision = "e61c9af524d7"
branch_labels = None
depends_on = None

_BINDING = "ontology_push_inbox_apply_binding"
_RESULTS = "ontology_push_inbox_apply_receipts"


def upgrade() -> None:
    op.create_table(
        _BINDING, sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False), sa.Column("database_marker", sa.Uuid(), nullable=False),
        *(sa.Column(name, sa.BigInteger(), nullable=False) for name in (
            "source_oid", "source_filenode", "target_oid", "target_filenode", "applied_through",
        )),
        sa.Column("empty_checks", sa.Integer(), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("""singleton IS TRUE AND generation=1 AND source_oid>0 AND source_filenode>0
                           AND target_oid>0 AND target_filenode>0 AND applied_through BETWEEN 0 AND 1000
                           AND empty_checks BETWEEN 0 AND 200""", name="ck_push_apply_binding"),
    )
    op.create_table(
        _RESULTS, sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("request_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("database_marker", sa.Uuid(), nullable=False), sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(12), nullable=False),
        *(sa.Column(name, sa.BigInteger(), nullable=False) for name in (
            "from_exclusive", "through_inclusive", "reviewed_accepted_through",
        )),
        sa.Column("events_applied", sa.Integer(), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("""generation=1 AND from_exclusive BETWEEN 0 AND 1000
                           AND reviewed_accepted_through BETWEEN from_exclusive AND 1000
                           AND ((outcome='no_new_data' AND from_exclusive=through_inclusive
                                 AND through_inclusive=reviewed_accepted_through AND events_applied=0)
                                OR (outcome='applied' AND through_inclusive=least(from_exclusive+50,reviewed_accepted_through)
                                    AND events_applied=through_inclusive-from_exclusive AND events_applied BETWEEN 1 AND 50))""",
                           name="ck_push_apply_result"),
    )
    op.create_index("ix_push_apply_recent", _RESULTS, ["recorded_at", "id"])
    op.create_index("uq_push_apply_ordinal", _RESULTS, ["through_inclusive"], unique=True,
                    postgresql_where=sa.text("outcome='applied'"))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(f"LOCK TABLE {_BINDING}, {_RESULTS} IN ACCESS EXCLUSIVE MODE")
    for table in (_BINDING, _RESULTS):
        if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {table} LIMIT 1")):
            raise RuntimeError("Cannot remove reviewed synthetic Push apply scope or recorded results")
    op.drop_table(_RESULTS)
    op.drop_table(_BINDING)
