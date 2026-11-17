"""Persist bounded synthetic field timestamp reviews and observations."""
import sqlalchemy as sa
from alembic import op

revision = "68ff119daf40"
down_revision = "21fe23589f8d"
branch_labels = None
depends_on = None

_REVIEW = "ontology_asset_field_freshness"
_INCIDENTS = "ontology_asset_quality_incidents"
_KIND = "ck_ontology_asset_quality_incident_kind"
_BASE = """(kind = 'freshness' AND reason = 'write_overdue') OR
           (kind = 'schema' AND reason = 'schema_drift') OR
           (kind = 'heap_size' AND reason IN ('below_minimum','above_maximum')) OR
           (kind = 'daily_job' AND reason = 'target_rejected')"""


def _constraint(include_field: bool) -> None:
    field = " OR (kind = 'field_time' AND reason = 'field_lag')" if include_field else ""
    op.drop_constraint(_KIND, _INCIDENTS, type_="check")
    op.create_check_constraint(_KIND, _INCIDENTS, f"generation>0 AND ({_BASE}{field})")


def upgrade() -> None:
    op.create_table(
        _REVIEW,
        sa.Column("target_table", sa.String(63), sa.ForeignKey("ontology_asset_freshness_policies.target_table"),
                  primary_key=True),
        *(sa.Column(name, sa.Integer, nullable=False) for name in ("generation", "policy_generation", "max_lag_seconds")),
        *(sa.Column(name, sa.BigInteger, nullable=False)
          for name in ("target_oid", "target_filenode", "index_oid", "index_filenode")),
        sa.Column("receipt_id", sa.Uuid()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        *(sa.Column(name, sa.DateTime(timezone=True)) for name in ("field_recorded_at", "receipt_recorded_at", "checked_at")),
        sa.CheckConstraint("target_table='field_freshness_fixture' AND generation>0 AND policy_generation>0 "
                           "AND max_lag_seconds BETWEEN 60 AND 604800 AND target_oid>0 AND target_filenode>0 "
                           "AND index_oid>0 AND index_filenode>0", name="ck_asset_field_freshness_scope"),
        sa.CheckConstraint(
            "(checked_at IS NULL AND field_recorded_at IS NULL AND receipt_recorded_at IS NULL AND receipt_id IS NULL) OR "
            "(checked_at IS NOT NULL AND field_recorded_at IS NOT NULL AND receipt_recorded_at IS NOT NULL "
            "AND receipt_id IS NOT NULL AND isfinite(checked_at) AND isfinite(field_recorded_at) AND isfinite(receipt_recorded_at) "
            "AND field_recorded_at<=receipt_recorded_at AND receipt_recorded_at>reviewed_at "
            "AND receipt_recorded_at<=checked_at)", name="ck_asset_field_freshness_observation"),
    )
    _constraint(True)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(f"LOCK TABLE {_REVIEW}, {_INCIDENTS} IN ACCESS EXCLUSIVE MODE")
    for table, where in ((_REVIEW, "TRUE"), (_INCIDENTS, "kind='field_time'")):
        if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1")):
            raise RuntimeError("Cannot remove reviewed field freshness or recorded evidence")
    _constraint(False)
    op.drop_table(_REVIEW)
