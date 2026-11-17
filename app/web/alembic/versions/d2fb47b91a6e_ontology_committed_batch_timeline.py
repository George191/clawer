"""Keep exact destination admission on new attempts and bounded asset lookup paths."""
import sqlalchemy as sa
from alembic import op

revision = "d2fb47b91a6e"
down_revision = "b8e42d47136f"
branch_labels = None
depends_on = None

_ATTEMPTS = "ontology_batch_sync_attempts"
_RUNS = "ontology_batch_sync_receipts"
_SCOPE = """from_exclusive IS NOT NULL AND source_identity_verified IS TRUE
            AND source_schema = 'batch_sync_stream' AND source_table = 'batches'
            AND target_table = 'committed_batch_fixture'"""


def upgrade() -> None:
    op.add_column(_ATTEMPTS, sa.Column("incremental_scope", sa.String(64)))
    op.create_check_constraint("ck_batch_attempt_incremental_scope", _ATTEMPTS, "incremental_scope ~ '^[0-9a-f]{64}$'")
    op.create_index("ix_batch_attempt_incremental_timeline", _ATTEMPTS, ["incremental_scope", "started_at", "id"],
                    postgresql_where=sa.text("incremental_scope IS NOT NULL"))
    op.create_index("ix_batch_attempt_legacy_timeline", _ATTEMPTS, ["started_at", "id"],
                    postgresql_where=sa.text("incremental_scope IS NULL AND receipt_id IS NOT NULL"))
    op.create_index("ix_batch_receipt_incremental_timeline", _RUNS, ["connection_id", "recorded_at", "id"],
                    postgresql_where=sa.text(_SCOPE))
    op.create_index("ix_batch_receipt_incremental_positive", _RUNS, ["connection_id", "recorded_at", "id"],
                    postgresql_where=sa.text(_SCOPE + " AND outcome = 'committed' AND rows_affected > 0"))


def downgrade() -> None:
    op.drop_index("ix_batch_receipt_incremental_positive", table_name=_RUNS)
    op.drop_index("ix_batch_receipt_incremental_timeline", table_name=_RUNS)
    op.drop_index("ix_batch_attempt_legacy_timeline", table_name=_ATTEMPTS)
    op.drop_index("ix_batch_attempt_incremental_timeline", table_name=_ATTEMPTS)
    op.drop_constraint("ck_batch_attempt_incremental_scope", _ATTEMPTS, type_="check")
    op.drop_column(_ATTEMPTS, "incremental_scope")
