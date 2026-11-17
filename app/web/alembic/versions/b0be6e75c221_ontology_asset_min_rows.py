"""Persist a threshold-only approved sample; never store observed row counts."""
import sqlalchemy as sa
from alembic import op

revision = "b0be6e75c221"
down_revision = "849ba892a412"
branch_labels = None
depends_on = None

_REVIEW = "ontology_asset_min_rows"
_INCIDENTS = "ontology_asset_quality_incidents"
_KIND = "ck_ontology_asset_quality_incident_kind"
_PRIOR = """(kind = 'freshness' AND reason = 'write_overdue') OR
            (kind = 'schema' AND reason = 'schema_drift') OR
            (kind = 'heap_size' AND reason IN ('below_minimum','above_maximum')) OR
            (kind = 'daily_job' AND reason = 'target_rejected') OR
            (kind = 'field_time' AND reason = 'field_lag')"""


def _constraints(include_min_rows: bool) -> None:
    extra = " OR (kind = 'minimum_rows' AND reason = 'row_threshold')" if include_min_rows else ""
    op.drop_constraint(_KIND, _INCIDENTS, type_="check")
    op.create_check_constraint(_KIND, _INCIDENTS, f"generation>0 AND ({_PRIOR}{extra})")


def upgrade() -> None:
    op.create_table(
        _REVIEW,
        sa.Column("target_table", sa.String(63), sa.ForeignKey("ontology_asset_freshness_policies.target_table"),
                  primary_key=True),
        *(sa.Column(name, sa.Integer, nullable=False) for name in ("generation", "policy_generation", "minimum_rows")),
        *(sa.Column(name, sa.BigInteger, nullable=False)
          for name in ("target_oid", "target_filenode", "index_oid", "index_filenode")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("at_least_minimum", sa.Boolean()), sa.Column("receipt_id", sa.Uuid()),
        sa.Column("receipt_recorded_at", sa.DateTime(timezone=True)), sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("target_table='field_freshness_fixture' AND generation>0 AND policy_generation>0 "
                           "AND minimum_rows BETWEEN 1 AND 100 AND target_oid>0 AND target_filenode>0 "
                           "AND index_oid>0 AND index_filenode>0 AND isfinite(reviewed_at)",
                           name="ck_asset_min_rows_scope"),
        sa.CheckConstraint(
            "(checked_at IS NULL AND at_least_minimum IS NULL AND receipt_recorded_at IS NULL AND receipt_id IS NULL) OR "
            "(checked_at IS NOT NULL AND at_least_minimum IS NOT NULL AND receipt_recorded_at IS NOT NULL "
            "AND receipt_id IS NOT NULL AND isfinite(checked_at) AND isfinite(receipt_recorded_at) "
            "AND receipt_recorded_at>reviewed_at AND receipt_recorded_at<=checked_at)",
            name="ck_asset_min_rows_observation",
        ),
    )
    _constraints(True)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(f"LOCK TABLE {_REVIEW}, {_INCIDENTS} IN ACCESS EXCLUSIVE MODE")
    for table, pred in ((_REVIEW, "TRUE"), (_INCIDENTS, "kind='minimum_rows'")):
        if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {table} WHERE {pred} LIMIT 1")):
            raise RuntimeError("Cannot remove reviewed minimum-row checks or recorded evidence")
    _constraints(False)
    op.drop_table(_REVIEW)
