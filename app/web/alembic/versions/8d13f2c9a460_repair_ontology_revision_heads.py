"""Restore missing current ontology baselines without inventing historical revisions."""
import sqlalchemy as sa
from alembic import op

revision = "8d13f2c9a460"
down_revision = "88a471f93c62"
branch_labels = None
depends_on = None


def upgrade():
    # Freeze publication heads while copying them. A lock timeout aborts this
    # migration instead of letting an overlapping deployment hold writes indefinitely.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE ontology_registry_definitions IN SHARE ROW EXCLUSIVE MODE")
    op.alter_column("ontology_revision_snapshots", "actor_id", existing_type=sa.Uuid(), nullable=True)
    # Only the live head is authoritative. Do not manufacture intervening revisions
    # or replay historical proposals against a different schema.
    op.execute("""
        INSERT INTO ontology_revision_snapshots
            (definition_id, revision, snapshot, actor_id, created_at)
        SELECT d.id, d.revision, d.published_definition, NULL, d.updated_at
        FROM ontology_registry_definitions AS d
        WHERE d.published_at IS NOT NULL
          AND jsonb_typeof(d.published_definition) = 'object'
          AND NOT EXISTS (
              SELECT 1 FROM ontology_revision_snapshots AS s
              WHERE s.definition_id = d.id AND s.revision = d.revision
          )
        ON CONFLICT (definition_id, revision) DO NOTHING
    """)


def downgrade():
    # SET NOT NULL intentionally refuses a downgrade when a recovered baseline
    # exists. Dropping it would strand its proposals; assigning an actor would
    # falsify the audit history. A Web-only rollback can retain this migration.
    op.alter_column("ontology_revision_snapshots", "actor_id", existing_type=sa.Uuid(), nullable=False)
