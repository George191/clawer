"""Persist exact reviewed ranges and historical receipt bounds without changing existing modes."""
import sqlalchemy as sa
from alembic import op

revision = "b86d2b1d4e98"
down_revision = "6094e3ea0b7d"
branch_labels = None
depends_on = None

_PLAN = "ontology_batch_sync_plans"
_RECEIPT = "ontology_batch_sync_receipts"
_WINDOW = "ck_ontology_batch_sync_receipt_window"
_WINDOW_LEGACY = """((from_exclusive IS NULL AND through_inclusive IS NULL AND source_high_water IS NULL
                 AND events_read IS NULL AND manifest_hash IS NULL AND outcome = 'committed')
               OR (from_exclusive >= 0 AND through_inclusive >= from_exclusive
                   AND source_high_water >= through_inclusive AND source_identity_verified IS TRUE
                   AND target_table = 'committed_batch_fixture'
                   AND ((outcome = 'no_new_data' AND rows_affected = 0 AND events_read = 0 AND manifest_hash IS NULL
                         AND through_inclusive = from_exclusive AND source_high_water = from_exclusive)
                        OR (outcome = 'committed' AND events_read = rows_affected AND rows_affected BETWEEN 1 AND 50
                            AND through_inclusive - from_exclusive = 1 AND manifest_hash ~ '^[0-9a-f]{64}$')))) IS TRUE"""
_RANGE_WINDOW = """(range_min IS NOT NULL AND range_max IS NOT NULL AND range_min <= range_max
    AND source_identity_verified IS TRUE AND source_schema='batch_sync_range_fixture' AND source_table='records'
    AND target_table='range_fixture' AND from_exclusive IS NULL AND through_inclusive IS NULL
    AND source_high_water IS NULL AND events_read IS NULL AND manifest_hash IS NULL
    AND ((outcome='no_new_data' AND rows_affected=0) OR (outcome='committed' AND rows_affected>0)))"""


def upgrade() -> None:
    for table in (_PLAN, _RECEIPT):
        for name in ("range_min", "range_max"):
            op.add_column(table, sa.Column(name, sa.BigInteger()))
    for name in ("range_index_oid", "range_index_node"):
        op.add_column(_PLAN, sa.Column(name, sa.BigInteger()))
    op.create_check_constraint("ck_batch_sync_range_pin", _PLAN, """
        ((range_min IS NULL AND range_max IS NULL AND range_index_oid IS NULL AND range_index_node IS NULL)
        OR (range_min IS NOT NULL AND range_max IS NOT NULL AND range_min <= range_max
            AND range_index_oid > 0 AND range_index_node > 0 AND source_oid > 0 AND source_file_node > 0
            AND mode='snapshot' AND source_identity_fingerprint IS NOT NULL
            AND source_schema='batch_sync_range_fixture' AND source_table='records'
            AND source_key_column='id' AND target_table='range_fixture')) IS TRUE
    """)
    op.drop_constraint(_WINDOW, _RECEIPT, type_="check")
    op.create_check_constraint(_WINDOW, _RECEIPT,
                               f"((range_min IS NULL AND range_max IS NULL AND ({_WINDOW_LEGACY})) OR {_RANGE_WINDOW}) IS TRUE")
    op.create_index("ix_batch_receipt_range_private", _RECEIPT, ["id"],
                    postgresql_where=sa.text("target_table='range_fixture'"))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(f"LOCK TABLE {_PLAN}, {_RECEIPT} IN ACCESS EXCLUSIVE MODE")
    for table in (_PLAN, _RECEIPT):
        if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {table} WHERE range_min IS NOT NULL LIMIT 1")):
            raise RuntimeError("Cannot remove reviewed range snapshots or their receipts")
    op.drop_index("ix_batch_receipt_range_private", table_name=_RECEIPT)
    op.drop_constraint(_WINDOW, _RECEIPT, type_="check")
    op.create_check_constraint(_WINDOW, _RECEIPT, _WINDOW_LEGACY)
    op.drop_constraint("ck_batch_sync_range_pin", _PLAN, type_="check")
    for name in ("range_index_node", "range_index_oid"):
        op.drop_column(_PLAN, name)
    for table in (_PLAN, _RECEIPT):
        for name in ("range_max", "range_min"):
            op.drop_column(table, name)
