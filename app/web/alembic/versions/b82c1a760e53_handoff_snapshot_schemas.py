"""Restore missing private stores and name their schemas handoff and snapshot."""

from importlib import import_module

from alembic import op
from sqlalchemy import text

revision = "b82c1a760e53"
down_revision = "a11e7b9d6204"
branch_labels = None
depends_on = None

_SCHEMAS = (
    ("ontology_handoff_private", "handoff", (
        "6094e3ea0b7d_ontology_private_handoff_copy",
    )),
    ("ontology_snapshot_intent_private", "snapshot", (
        "fb804e491f6d_ontology_private_snapshot_intent",
        "e57b4216c9a8_ontology_snapshot_intent_delivery_ledger",
    )),
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    bind = op.get_bind()
    for old, new, migrations in _SCHEMAS:
        if bind.scalar(text("SELECT to_regnamespace(:name)"), {"name": new}) is not None:
            raise RuntimeError(f"Cannot rename {old}: schema {new} already exists")
        if bind.scalar(text("SELECT to_regnamespace(:name)"), {"name": old}) is None:
            for migration in migrations:
                import_module(f"app.web.alembic.versions.{migration}").upgrade()
        op.execute(f"ALTER SCHEMA {old} RENAME TO {new}")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    for old, new, _ in reversed(_SCHEMAS):
        op.execute(f"ALTER SCHEMA {new} RENAME TO {old}")
