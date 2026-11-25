"""Bind Weekly UTC job outcome checks independently of Daily UTC reviews."""

import sqlalchemy as sa
from alembic import op

revision = "af834982b6d1"
down_revision = "c81fb5290a74"
branch_labels = None
depends_on = None

_WEEKLY = "ontology_asset_weekly_job_quality"
_INCIDENTS = "ontology_asset_quality_incidents"
_KIND = "ck_ontology_asset_quality_incident_kind"
_PRIOR = """(kind = 'freshness' AND reason = 'write_overdue') OR
            (kind = 'schema' AND reason = 'schema_drift') OR
            (kind = 'heap_size' AND reason IN ('below_minimum','above_maximum')) OR
            (kind = 'daily_job' AND reason = 'target_rejected') OR
            (kind = 'field_time' AND reason = 'field_lag') OR
            (kind = 'minimum_rows' AND reason = 'row_threshold')"""


def _constraints(include_weekly: bool) -> None:
    extra = " OR (kind = 'weekly_job' AND reason = 'target_rejected')" if include_weekly else ""
    op.drop_constraint(_KIND, _INCIDENTS, type_="check")
    op.create_check_constraint(_KIND, _INCIDENTS, f"generation>0 AND ({_PRIOR}{extra})")


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.create_table(
        _WEEKLY,
        sa.Column("target_table", sa.String(63), sa.ForeignKey("ontology_asset_freshness_policies.target_table"),
                  primary_key=True),
        *(sa.Column(name, sa.Integer, nullable=False)
          for name in ("generation", "freshness_generation", "schedule_generation", "planned_weekday_utc")),
        *(sa.Column(name, sa.Uuid(), nullable=False) for name in ("plan_id", "armed_by")),
        *(sa.Column(name, sa.BigInteger, nullable=False) for name in ("target_oid", "target_filenode")),
        sa.Column("planned_time_utc", sa.String(5), nullable=False),
        *(sa.Column(name, sa.DateTime(timezone=True), nullable=False) for name in ("plan_updated_at", "reviewed_at")),
        *(sa.Column(name, sa.DateTime(timezone=True))
          for name in ("checked_at", "confirmed_slot", "observed_slot", "finished_at")),
        sa.Column("job_state", sa.String(16)),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32)),
        sa.Column("receipt_id", sa.Uuid()),
        sa.Column("rows_affected", sa.Integer()),
        sa.CheckConstraint(
            "generation>0 AND freshness_generation>0 AND schedule_generation>0 AND target_oid>0 AND target_filenode>0 "
            "AND planned_time_utc ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$' AND planned_weekday_utc BETWEEN 1 AND 7 "
            "AND state IN ('not_checked','healthy','unhealthy','unknown','superseded')",
            name="ck_asset_weekly_job_review",
        ),
    )
    _constraints(True)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(f"LOCK TABLE app.{_WEEKLY}, app.{_INCIDENTS} IN ACCESS EXCLUSIVE MODE")
    for table, predicate in ((_WEEKLY, "TRUE"), (_INCIDENTS, "kind='weekly_job'")):
        if op.get_bind().scalar(sa.text(f"SELECT 1 FROM app.{table} WHERE {predicate} LIMIT 1")):
            raise RuntimeError("Cannot discard reviewed Weekly UTC quality or recorded incidents")
    _constraints(False)
    op.drop_table(_WEEKLY)
