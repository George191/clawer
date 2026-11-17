"""Store a bounded committed batch interval and exact physical stream checkpoint."""
import sqlalchemy as sa
from alembic import op

revision = "b8e42d47136f"
down_revision = "0e653c2f6a91"
branch_labels = None
depends_on = None

_PLAN = "ontology_batch_sync_plans"
_RECEIPT = "ontology_batch_sync_receipts"
_WATERMARK = "ontology_batch_sync_incremental_watermark"
_ATTEMPT = "ontology_batch_sync_attempts"
_JOB = "ontology_batch_sync_schedule_jobs"

_PLAN_MODE = (
    'ck_ontology_batch_sync_mode_pin',
    """((mode = 'snapshot' AND source_stream_digest IS NULL AND
                 ((source_identity_fingerprint IS NULL AND source_oid IS NULL AND source_file_node IS NULL) OR
                  (source_identity_fingerprint ~ '^[0-9a-f]{64}$' AND source_oid > 0 AND source_file_node > 0)))
                OR (mode = 'incremental' AND source_oid IS NULL AND source_file_node IS NULL
                    AND source_identity_fingerprint ~ '^[0-9a-f]{64}$' AND source_stream_digest ~ '^[0-9a-f]{64}$'
                    AND source_schema = 'batch_sync_stream' AND source_table = 'batches'
                    AND source_key_column = 'id' AND target_table = 'committed_batch_fixture')) IS TRUE""",
)
_RECEIPT_WINDOW = (
    'ck_ontology_batch_sync_receipt_window',
    """((from_exclusive IS NULL AND through_inclusive IS NULL AND source_high_water IS NULL
                 AND events_read IS NULL AND manifest_hash IS NULL AND outcome = 'committed')
               OR (from_exclusive >= 0 AND through_inclusive >= from_exclusive
                   AND source_high_water >= through_inclusive AND source_identity_verified IS TRUE
                   AND target_table = 'committed_batch_fixture'
                   AND ((outcome = 'no_new_data' AND rows_affected = 0 AND events_read = 0 AND manifest_hash IS NULL
                         AND through_inclusive = from_exclusive AND source_high_water = from_exclusive)
                        OR (outcome = 'committed' AND events_read = rows_affected AND rows_affected BETWEEN 1 AND 50
                            AND through_inclusive - from_exclusive = 1 AND manifest_hash ~ '^[0-9a-f]{64}$')))) IS TRUE""",
)
_ATTEMPT_NEW = (
    'ck_ontology_batch_sync_attempt_status',
    """(status = 'started' AND finished_at IS NULL AND receipt_id IS NULL AND code IS NULL AND message IS NULL) OR (status IN ('committed','no_new_data','replayed') AND finished_at IS NOT NULL AND receipt_id IS NOT NULL AND code IS NULL AND message IS NULL) OR (status IN ('rejected','retryable','outcome_unknown') AND finished_at IS NOT NULL AND receipt_id IS NULL AND code IS NOT NULL AND message IS NOT NULL)""",
)
_JOB_STATE_NEW = (
    'ck_ontology_batch_sync_schedule_jobs_state',
    """state IN ('pending','dispatched','committed','no_new_data','rejected','outcome_unknown','expired')""",
)
_JOB_RECEIPT_NEW = (
    'ck_ontology_batch_sync_schedule_job_receipt',
    """(state IN ('committed','no_new_data')) = (receipt_id IS NOT NULL)""",
)
_WATERMARK_SCOPE = (
    'ck_ontology_batch_sync_watermark',
    """singleton IS TRUE AND applied_through >= 0 AND target_oid > 0 AND target_file_node > 0 AND source_identity_fingerprint ~ '^[0-9a-f]{64}$' AND source_stream_digest ~ '^[0-9a-f]{64}$'""",
)
_PLAN_OLD = (
    'ck_batch_sync_source_identity_pin',
    """(source_identity_fingerprint IS NULL AND source_oid IS NULL AND source_file_node IS NULL) OR (source_identity_fingerprint ~ '^[0-9a-f]{64}$' AND source_oid > 0 AND source_file_node > 0) IS TRUE""",
)
_ATTEMPT_OLD = (
    'ck_ontology_batch_sync_attempt_status',
    """(status = 'started' AND finished_at IS NULL AND receipt_id IS NULL AND code IS NULL AND message IS NULL) OR (status IN ('committed','replayed') AND finished_at IS NOT NULL AND receipt_id IS NOT NULL AND code IS NULL AND message IS NULL) OR (status IN ('rejected','retryable','outcome_unknown') AND finished_at IS NOT NULL AND receipt_id IS NULL AND code IS NOT NULL AND message IS NOT NULL)""",
)
_JOB_STATE_OLD = (
    'ck_ontology_batch_sync_schedule_jobs_state',
    """state IN ('pending','dispatched','committed','rejected','outcome_unknown','expired')""",
)
_JOB_RECEIPT_OLD = (
    'ck_ontology_batch_sync_schedule_job_receipt',
    """(state = 'committed') = (receipt_id IS NOT NULL)""",
)


def _replace(table, before, after):
    op.drop_constraint(before[0], table, type_="check")
    op.create_check_constraint(after[0], table, after[1])


def upgrade() -> None:
    op.add_column(_PLAN, sa.Column("mode", sa.String(16), server_default="snapshot", nullable=False))
    op.add_column(_PLAN, sa.Column("source_stream_digest", sa.String(64)))
    _replace(_PLAN, _PLAN_OLD, _PLAN_MODE)

    op.add_column(_RECEIPT, sa.Column("outcome", sa.String(16), server_default="committed", nullable=False))
    for field in ("from_exclusive", "through_inclusive", "source_high_water"):
        op.add_column(_RECEIPT, sa.Column(field, sa.BigInteger()))
    op.add_column(_RECEIPT, sa.Column("events_read", sa.Integer()))
    op.add_column(_RECEIPT, sa.Column("manifest_hash", sa.String(64)))
    op.create_check_constraint(_RECEIPT_WINDOW[0], _RECEIPT, _RECEIPT_WINDOW[1])
    op.create_index("uq_ontology_batch_sync_incremental_ordinal", _RECEIPT, ["through_inclusive"], unique=True,
                    postgresql_where=sa.text("outcome = 'committed' AND through_inclusive IS NOT NULL"))
    _replace(_ATTEMPT, _ATTEMPT_OLD, _ATTEMPT_NEW)
    _replace(_JOB, _JOB_STATE_OLD, _JOB_STATE_NEW)
    _replace(_JOB, _JOB_RECEIPT_OLD, _JOB_RECEIPT_NEW)

    op.create_table(
        _WATERMARK,
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("source_identity_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_stream_digest", sa.String(64), nullable=False),
        sa.Column("target_oid", sa.BigInteger(), nullable=False),
        sa.Column("target_file_node", sa.BigInteger(), nullable=False),
        sa.Column("applied_through", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(_WATERMARK_SCOPE[1], name=_WATERMARK_SCOPE[0]),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute(f"LOCK TABLE {_PLAN} IN ACCESS EXCLUSIVE MODE")
    op.execute(f"LOCK TABLE {_RECEIPT}, {_ATTEMPT}, {_JOB}, {_WATERMARK} IN ACCESS EXCLUSIVE MODE")
    for table, predicate in (
        (_WATERMARK, "TRUE"), (_PLAN, "mode = 'incremental'"), (_RECEIPT, "from_exclusive IS NOT NULL"),
    ):
        if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {table} WHERE {predicate} LIMIT 1")):
            raise RuntimeError("Cannot remove an admitted committed batch stream or its recorded outcomes")
    op.drop_table(_WATERMARK)
    _replace(_JOB, _JOB_RECEIPT_NEW, _JOB_RECEIPT_OLD)
    _replace(_JOB, _JOB_STATE_NEW, _JOB_STATE_OLD)
    _replace(_ATTEMPT, _ATTEMPT_NEW, _ATTEMPT_OLD)
    op.drop_index("uq_ontology_batch_sync_incremental_ordinal", table_name=_RECEIPT)
    op.drop_constraint(_RECEIPT_WINDOW[0], _RECEIPT, type_="check")
    for field in ("manifest_hash", "events_read", "source_high_water", "through_inclusive", "from_exclusive", "outcome"):
        op.drop_column(_RECEIPT, field)
    _replace(_PLAN, _PLAN_MODE, _PLAN_OLD)
    op.drop_column(_PLAN, "source_stream_digest")
    op.drop_column(_PLAN, "mode")
