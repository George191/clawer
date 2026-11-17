"""Persist reviewed local source identity proof and immutable execution attribution."""

import sqlalchemy as sa
from alembic import op

revision = "0e653c2f6a91"
down_revision = "8d659760c2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_batch_source_identities",
        sa.Column("connection_id", sa.Uuid(), primary_key=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="ck_batch_source_identity_fingerprint"),
    )
    plans = "ontology_batch_sync_plans"
    op.add_column(plans, sa.Column("source_identity_fingerprint", sa.String(64)))
    op.add_column(plans, sa.Column("source_oid", sa.BigInteger()))
    op.add_column(plans, sa.Column("source_file_node", sa.BigInteger()))
    op.create_check_constraint(
        "ck_batch_sync_source_identity_pin", plans,
        "(source_identity_fingerprint IS NULL AND source_oid IS NULL AND source_file_node IS NULL) "
        "OR (source_identity_fingerprint ~ '^[0-9a-f]{64}$' AND source_oid > 0 AND source_file_node > 0) IS TRUE",
    )
    op.add_column("ontology_batch_sync_receipts", sa.Column("source_identity_verified", sa.Boolean()))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    # Freeze all proof/plan/receipt writers before testing the no-evidence downgrade guard.
    op.execute("LOCK TABLE ontology_batch_sync_plans IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE ontology_batch_sync_receipts, ontology_batch_source_identities IN ACCESS EXCLUSIVE MODE")
    for table, predicate in (
        ("ontology_batch_source_identities", "TRUE"),
        ("ontology_batch_sync_plans", "source_identity_fingerprint IS NOT NULL"),
        ("ontology_batch_sync_receipts", "source_identity_verified IS TRUE"),
    ):
        if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {table} WHERE {predicate} LIMIT 1")):
            raise RuntimeError("Cannot remove saved-source identity while protected reviews or evidence exist")
    op.drop_column("ontology_batch_sync_receipts", "source_identity_verified")
    plans = "ontology_batch_sync_plans"
    op.drop_constraint("ck_batch_sync_source_identity_pin", plans, type_="check")
    for column in ("source_file_node", "source_oid", "source_identity_fingerprint"):
        op.drop_column(plans, column)
    op.drop_table("ontology_batch_source_identities")
