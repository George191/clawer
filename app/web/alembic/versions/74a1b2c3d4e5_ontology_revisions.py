"""Keep published ontology snapshots and independent change reviews."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "74a1b2c3d4e5"
down_revision = "73a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ontology_revision_grants",
        sa.Column("membership_id", sa.Uuid(), sa.ForeignKey("team_user_join.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("can_edit", sa.Boolean(), nullable=False),
        sa.Column("can_review", sa.Boolean(), nullable=False),
        sa.CheckConstraint("can_edit OR can_review", name="ck_ontology_revision_grant_permission"),
    )
    op.create_table(
        "ontology_revision_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("definition_id", sa.Uuid(), sa.ForeignKey("ontology_registry_definitions.id"), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("restore_revision", sa.Integer()),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("proposed_definition", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("reviewer_id", sa.Uuid()),
        sa.Column("review_note", sa.String(500), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("merged_revision", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("base_revision > 0", name="ck_ontology_revision_base"),
        sa.CheckConstraint("restore_revision IS NULL OR restore_revision < base_revision", name="ck_ontology_revision_restore"),
        sa.CheckConstraint("reviewer_id IS NULL OR reviewer_id != author_id", name="ck_ontology_revision_independent"),
        sa.CheckConstraint(
            "(status = 'pending' AND reviewer_id IS NULL AND reviewed_at IS NULL AND merged_revision IS NULL) OR "
            "(status IN ('approved', 'rejected') AND reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL AND merged_revision IS NULL) OR "
            "(status = 'merged' AND reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL AND merged_revision = base_revision + 1)",
            name="ck_ontology_revision_review_state",
        ),
    )
    op.create_index("ix_ontology_revision_proposals_definition", "ontology_revision_proposals", ["definition_id", "created_at", "id"])
    op.create_table(
        "ontology_revision_snapshots",
        sa.Column("definition_id", sa.Uuid(), sa.ForeignKey("ontology_registry_definitions.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), sa.ForeignKey("ontology_revision_proposals.id"), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_ontology_revision_snapshot"),
    )
    # The pre-migration Web can still publish during a rolling cutover. Install the
    # trigger before backfilling so no first publication can pass between the two.
    op.execute("""
        CREATE FUNCTION ontology_capture_initial_revision() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF OLD.published_at IS NOT NULL THEN
                    RETURN NULL;
                END IF;
            END IF;
            EXECUTE format(
                'INSERT INTO %I.ontology_revision_snapshots '
                '(definition_id, revision, snapshot, actor_id, created_at) VALUES ($1, $2, $3, $4, $5)',
                TG_TABLE_SCHEMA
            ) USING NEW.id, NEW.revision, NEW.published_definition, NEW.owner_id, NEW.published_at;
            RETURN NULL;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER ontology_initial_revision
        AFTER INSERT OR UPDATE OF published_at ON ontology_registry_definitions
        FOR EACH ROW WHEN (NEW.published_at IS NOT NULL)
        EXECUTE FUNCTION ontology_capture_initial_revision()
    """)
    op.execute("""
        INSERT INTO ontology_revision_snapshots (definition_id, revision, snapshot, actor_id, created_at)
        SELECT id, revision, published_definition, owner_id, published_at
        FROM ontology_registry_definitions
        WHERE published_at IS NOT NULL
    """)


def downgrade():
    op.execute("DROP TRIGGER ontology_initial_revision ON ontology_registry_definitions")
    op.execute("DROP FUNCTION ontology_capture_initial_revision()")
    op.drop_table("ontology_revision_snapshots")
    op.drop_table("ontology_revision_proposals")
    op.drop_table("ontology_revision_grants")
