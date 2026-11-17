"""Persist optional Daily UTC review and exact post-rollback failure provenance."""
import sqlalchemy as sa
from alembic import op

revision = "7ba3f09ec526"
down_revision = "af02d96bc517"
branch_labels = None
depends_on = None

_REVIEW = "ontology_asset_daily_job_quality"
_FAILURES = "ontology_transfer_schedule_target_rejections"
_INCIDENTS = "ontology_asset_quality_incidents"
_KIND = "ck_ontology_asset_quality_incident_kind"
_LIFE = "ck_ontology_asset_quality_incident_lifecycle"
_KINDS = """(kind = 'freshness' AND reason = 'write_overdue') OR
           (kind = 'schema' AND reason = 'schema_drift') OR
           (kind = 'heap_size' AND reason IN ('below_minimum','above_maximum'))"""
_CLOSE = "'healthy','policy_paused','policy_reviewed','schema_baseline_captured'"


def _incident_constraints(*, expanded: bool) -> None:
    extra_kind = " OR (kind = 'daily_job' AND reason = 'target_rejected')" if expanded else ""
    extra_close = ",'schedule_changed','target_changed'" if expanded else ""
    op.drop_constraint(_KIND, _INCIDENTS, type_="check")
    op.drop_constraint(_LIFE, _INCIDENTS, type_="check")
    op.create_check_constraint(_KIND, _INCIDENTS, f"generation>0 AND ({_KINDS}{extra_kind})")
    op.create_check_constraint(
        _LIFE, _INCIDENTS, "(closed_at IS NULL) = (close_reason IS NULL) AND "
        f"(close_reason IS NULL OR close_reason IN ({_CLOSE}{extra_close})) AND "
        "(acknowledged_at IS NULL) = (acknowledged_by IS NULL)",
    )


def upgrade() -> None:
    op.create_table(
        _REVIEW,
        sa.Column("target_table", sa.String(63), sa.ForeignKey("ontology_asset_freshness_policies.target_table"),
                  primary_key=True),
        *(sa.Column(name, sa.Integer(), nullable=False) for name in ("generation", "freshness_generation", "schedule_generation")),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("armed_by", sa.Uuid(), nullable=False),
        sa.Column("target_oid", sa.BigInteger(), nullable=False), sa.Column("target_filenode", sa.BigInteger(), nullable=False),
        sa.Column("planned_time_utc", sa.String(5), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        *(sa.Column(name, sa.DateTime(timezone=True)) for name in ("checked_at", "confirmed_slot", "observed_slot", "finished_at")),
        sa.Column("job_state", sa.String(16)), sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32)), sa.Column("receipt_id", sa.Uuid()), sa.Column("rows_affected", sa.Integer()),
        sa.CheckConstraint("generation>0 AND freshness_generation>0 AND schedule_generation>0 AND target_oid>0 "
                           "AND target_filenode>0 AND planned_time_utc ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$' "
                           "AND state IN ('not_checked','healthy','unhealthy','unknown','superseded')",
                           name="ck_asset_daily_job_review"),
    )
    op.create_table(
        _FAILURES,
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_schedule_jobs.id"), primary_key=True),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("ontology_transfer_attempts.id"), nullable=False, unique=True),
        sa.Column("code", sa.String(20), nullable=False), sa.Column("target_oid", sa.BigInteger(), nullable=False),
        sa.Column("target_filenode", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("code='target_constraint' AND target_oid>0 AND target_filenode>0",
                           name="ck_transfer_target_rejection_scope"),
    )
    _incident_constraints(expanded=True)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(f"LOCK TABLE {_REVIEW}, {_FAILURES}, {_INCIDENTS} IN ACCESS EXCLUSIVE MODE")
    for table, where in ((_REVIEW, "TRUE"), (_FAILURES, "TRUE"), (_INCIDENTS, "kind='daily_job'")):
        if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1")):
            raise RuntimeError("Cannot remove reviewed Daily UTC checks or recorded evidence")
    _incident_constraints(expanded=False)
    op.drop_table(_FAILURES)
    op.drop_table(_REVIEW)
