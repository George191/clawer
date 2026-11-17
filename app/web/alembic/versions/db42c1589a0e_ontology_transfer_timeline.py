"""Pin new transfer attempts to reviewed destination revisions without backfilling old failures."""

import sqlalchemy as sa
from alembic import op

revision = "db42c1589a0e"
down_revision = "f32194ec7a60"
branch_labels = None
depends_on = None

_ATTEMPTS = "ontology_transfer_attempts"
_RUNS = "ontology_transfer_runs"
_FIELDS = (
    ("plan_updated_at", sa.DateTime(timezone=True)), ("source_table", sa.String(63)), ("target_table", sa.String(63)),
    ("target_oid", sa.BigInteger()), ("target_filenode", sa.BigInteger()), ("scope_trigger", sa.String(9)),
    ("schedule_job_id", sa.Uuid()),
)


def upgrade() -> None:
    for name, kind in _FIELDS:
        op.add_column(_ATTEMPTS, sa.Column(name, kind, nullable=True))
    op.create_foreign_key("fk_ontology_transfer_attempt_schedule_job", _ATTEMPTS, "ontology_transfer_schedule_jobs",
                          ["schedule_job_id"], ["id"])
    op.create_check_constraint(
        "ck_ontology_transfer_attempt_timeline_scope", _ATTEMPTS,
        "(target_table IS NULL) = (plan_updated_at IS NULL) "
        "AND (target_table IS NULL) = (source_table IS NULL) "
        "AND (target_table IS NULL) = (scope_trigger IS NULL) "
        "AND (target_oid IS NULL) = (target_filenode IS NULL) "
        "AND (target_table IS NOT NULL OR target_oid IS NULL) "
        "AND (scope_trigger IS NULL OR scope_trigger IN ('manual','daily_utc','upstream')) "
        "AND (scope_trigger IS DISTINCT FROM 'daily_utc' OR schedule_job_id IS NOT NULL)",
    )
    op.create_index("ix_ontology_transfer_attempt_timeline", _ATTEMPTS, ["target_table", "started_at", "id"],
                    postgresql_where=sa.text("target_table IS NOT NULL"))
    op.create_index("ix_ontology_transfer_attempt_manual_receipt", _ATTEMPTS, ["receipt_id"],
                    postgresql_where=sa.text("scope_trigger = 'manual' AND status = 'committed' AND receipt_id IS NOT NULL"))
    op.create_index("ix_ontology_transfer_timeline_receipts", _RUNS,
                    ["target_schema", "target_table", "recorded_at", "id"])
    op.create_index("ix_ontology_transfer_timeline_current", _RUNS,
                    ["target_schema", "target_table", "target_oid", "target_filenode", "recorded_at", "id"],
                    postgresql_where=sa.text("rows_affected > 0"))


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {_ATTEMPTS} WHERE target_table IS NOT NULL) THEN
                RAISE EXCEPTION 'Cannot downgrade while immutable transfer attempt scopes exist';
            END IF;
        END $$;
    """)
    op.drop_index("ix_ontology_transfer_timeline_current", table_name=_RUNS)
    op.drop_index("ix_ontology_transfer_timeline_receipts", table_name=_RUNS)
    op.drop_index("ix_ontology_transfer_attempt_manual_receipt", table_name=_ATTEMPTS)
    op.drop_index("ix_ontology_transfer_attempt_timeline", table_name=_ATTEMPTS)
    op.drop_constraint("ck_ontology_transfer_attempt_timeline_scope", _ATTEMPTS, type_="check")
    op.drop_constraint("fk_ontology_transfer_attempt_schedule_job", _ATTEMPTS, type_="foreignkey")
    for name, _ in reversed(_FIELDS):
        op.drop_column(_ATTEMPTS, name)
