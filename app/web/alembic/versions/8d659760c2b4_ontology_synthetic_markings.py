"""Record immutable synthetic marks without backfilling legacy rows."""
import sqlalchemy as sa
from alembic import op

revision = "8d659760c2b4"
down_revision = "b46891e47c52"
branch_labels = None
depends_on = None

_TABLES = (
    ("ontology_cdc_sandbox_versions", "ck_ontology_cdc_version_marking", "marking_level = 1"),
    ("ontology_pipeline_sandbox_revisions", "ck_ontology_builder_revision_marking",
     """(marking_level = 1 AND NOT config ? 'additionalMarking')
        OR (marking_level = 2 AND (config->>'additionalMarking' = 'sandbox-restricted') IS TRUE)"""),
    ("ontology_pipeline_sandbox_outputs", "ck_ontology_builder_output_marking", "marking_level IN (1, 2)"),
)


def upgrade() -> None:
    for table, name, condition in _TABLES:
        op.add_column(table, sa.Column("marking_level", sa.SmallInteger(), nullable=True))
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    # Dropping active markings would make protected history readable as legacy.
    for table, _, _ in _TABLES:
        if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {table} WHERE marking_level IS NOT NULL LIMIT 1")):
            raise RuntimeError("Cannot remove governance metadata while marked synthetic history exists")
    for table, name, _ in reversed(_TABLES):
        op.drop_constraint(name, table, type_="check")
        op.drop_column(table, "marking_level")
