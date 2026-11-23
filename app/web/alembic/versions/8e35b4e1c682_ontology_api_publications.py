"""Install explicit API publications for Published object types."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "8e35b4e1c682"
down_revision = "8d13f2c9a460"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ontology_publications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("definition_id", sa.Uuid(), sa.ForeignKey("ontology_registry_definitions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("fields", JSONB(), nullable=False),
        sa.Column("query", JSONB(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("key_digest", sa.String(64)),
        sa.Column("key_status", sa.String(12), nullable=False),
        sa.Column("version", sa.Uuid(), nullable=False),
        sa.CheckConstraint("valid_until > valid_from", name="ck_ontology_publication_dates"),
        sa.CheckConstraint("key_status IN ('unissued','active','revoked')", name="ck_ontology_publication_status"),
    )
    op.create_index("ix_ontology_publications_definition_id", "ontology_publications", ["definition_id"])


def downgrade():
    op.drop_table("ontology_publications")
