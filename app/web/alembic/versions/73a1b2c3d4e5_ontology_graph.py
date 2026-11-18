"""Persist semantic link types, links and saved dynamic object sets."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "73a1b2c3d4e5"
down_revision = "56e7b40cc813"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ontology_link_types",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("key", sa.String(63), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "source_definition_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_registry_definitions.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_definition_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_registry_definitions.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("cardinality", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "cardinality IN ('one_to_one','one_to_many','many_to_one','many_to_many')",
            name="ck_ontology_link_cardinality",
        ),
        sa.UniqueConstraint("project_id", "key", name="uq_ontology_link_type_key"),
    )
    op.create_table(
        "ontology_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "link_type_id", sa.Uuid(), sa.ForeignKey("ontology_link_types.id"), nullable=False
        ),
        sa.Column(
            "source_record_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_runtime_records.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_record_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_runtime_records.id"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "link_type_id", "source_record_id", "target_record_id", name="uq_ontology_link_pair"
        ),
    )
    op.create_index(
        "ix_ontology_link_reverse",
        "ontology_links",
        ["target_record_id", "link_type_id", "source_record_id"],
    )
    op.create_table(
        "ontology_removed_links",
        sa.Column(
            "link_type_id", sa.Uuid(), sa.ForeignKey("ontology_link_types.id"), primary_key=True
        ),
        sa.Column(
            "source_record_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_runtime_records.id"),
            primary_key=True,
        ),
        sa.Column(
            "target_record_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_runtime_records.id"),
            primary_key=True,
        ),
        sa.Column("link_id", sa.Uuid(), nullable=False),
    )
    op.create_table(
        "ontology_saved_sets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "definition_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_registry_definitions.id"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("query", JSONB, nullable=False),
        sa.UniqueConstraint("definition_id", "owner_id", "name", name="uq_ontology_saved_set_name"),
    )


def downgrade():
    op.drop_table("ontology_saved_sets")
    op.drop_table("ontology_removed_links")
    op.drop_table("ontology_links")
    op.drop_table("ontology_link_types")
