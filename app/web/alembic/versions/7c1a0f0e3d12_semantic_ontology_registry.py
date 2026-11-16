"""Add the draft and published semantic object registry."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7c1a0f0e3d12"
down_revision = "f772c8d25f9b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ontology_registry_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("semantic_id", sa.String(63), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("draft_definition", postgresql.JSONB(), nullable=False),
        sa.Column("published_definition", postgresql.JSONB(), nullable=True),
        sa.Column("source_oid", sa.BigInteger(), nullable=True),
        sa.Column("source_signature", postgresql.JSONB(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision > 0", name="ontology_registry_positive_revision"),
        sa.CheckConstraint("(published_definition IS NULL) = (published_at IS NULL)",
                           name="ontology_registry_publish_state"),
        sa.CheckConstraint("(source_oid IS NULL) = (source_signature IS NULL)",
                           name="ontology_registry_source_identity"),
    )
    op.create_index("ix_ontology_registry_definitions_project_id", "ontology_registry_definitions", ["project_id"])
    op.create_index("ix_ontology_registry_definitions_owner_id", "ontology_registry_definitions", ["owner_id"])
    op.create_index(
        "uq_ontology_registry_project_published_id", "ontology_registry_definitions",
        ["project_id", "semantic_id"], unique=True,
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )


def downgrade():
    op.drop_table("ontology_registry_definitions")
