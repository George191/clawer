"""Bind a second immutable synthetic snapshot to Match by ID."""
import sqlalchemy as sa
from alembic import op

revision = "51b18bf851dd"
down_revision = "7ba3f09ec526"
branch_labels = depends_on = None

_REVISIONS = "ontology_pipeline_sandbox_revisions"
_OUTPUTS = "ontology_pipeline_sandbox_outputs"
_VERSIONS = "ontology_cdc_sandbox_versions"
_MARK = "ck_ontology_builder_revision_marking"
_LEGACY_MARK = """(marking_level = 1 AND NOT config ? 'additionalMarking')
    OR (marking_level = 2 AND (config->>'additionalMarking' = 'sandbox-restricted') IS TRUE)"""
_CHECKS = (
    (_REVISIONS, "ck_ontology_builder_revision_match",
     """(match_version_id IS NULL AND NOT config ? 'matchVersionId')
        OR (match_version_id IS NOT NULL AND match_version_id <> input_version_id
            AND (marking_level IN (1, 2)) IS TRUE
            AND (config->>'matchVersionId' = match_version_id::text) IS TRUE)"""),
    (_OUTPUTS, "ck_ontology_builder_output_match", """match_version_id IS NULL
        OR (match_version_id <> input_version_id AND (marking_level IN (1, 2)) IS TRUE)"""),
)


def upgrade() -> None:
    for table, name, check in _CHECKS:
        op.add_column(table, sa.Column("match_version_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(f"fk_{name}", table, _VERSIONS, ["match_version_id"], ["receipt_id"])
        op.create_check_constraint(name, table, check)
    op.drop_constraint(_MARK, _REVISIONS, type_="check")
    op.create_check_constraint(_MARK, _REVISIONS, _LEGACY_MARK + " OR (marking_level = 2 AND match_version_id IS NOT NULL)")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
    bind.execute(sa.text(f"LOCK TABLE {_REVISIONS}, {_OUTPUTS} IN ACCESS EXCLUSIVE MODE"))
    for table, _, _ in _CHECKS:
        if bind.scalar(sa.text(f"SELECT 1 FROM {table} WHERE match_version_id IS NOT NULL LIMIT 1")):
            raise RuntimeError("Cannot remove exact Match by ID ancestry while two-input history exists")
    op.drop_constraint(_MARK, _REVISIONS, type_="check")
    op.create_check_constraint(_MARK, _REVISIONS, _LEGACY_MARK)
    for table, name, _ in reversed(_CHECKS):
        op.drop_constraint(name, table, type_="check")
        op.drop_constraint(f"fk_{name}", table, type_="foreignkey")
        op.drop_column(table, "match_version_id")
