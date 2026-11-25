"""Allow an administrator to pause an exact saved PostgreSQL source revision."""
from alembic import op
from sqlalchemy import text

from app.etl.schema.data_connections import DATA_CONNECTIONS_PAUSE_DDL

revision = "b66fbad735c2"
down_revision = "ad6207fa991b"
branch_labels = None
depends_on = None

_REGISTRY = "app.ontology_data_connections"
_INSTALLED = text(f"SELECT pg_catalog.to_regclass('{_REGISTRY}') IS NOT NULL")
_UPGRADED = text(f"""SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute
    WHERE attrelid = pg_catalog.to_regclass('{_REGISTRY}')
      AND attname = 'paused_at' AND attnum > 0 AND NOT attisdropped)""")


def _lock_installed(bind):
    bind.execute(text("SET LOCAL lock_timeout = '3s'"))
    bind.execute(text("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext("
                      "'app.ontology_data_connections_migration'))"))
    if not bind.scalar(_INSTALLED):
        return False    # The registry is optional and may be installed separately later.
    bind.execute(text(f"LOCK TABLE {_REGISTRY} IN ACCESS EXCLUSIVE MODE"))
    return True


def upgrade():
    bind = op.get_bind()
    if _lock_installed(bind) and not bind.scalar(_UPGRADED):
        op.execute(DATA_CONNECTIONS_PAUSE_DDL)


def downgrade():
    bind = op.get_bind()
    if not _lock_installed(bind) or not bind.scalar(_UPGRADED):
        return
    if bind.scalar(text(f"SELECT 1 FROM {_REGISTRY} WHERE paused_at IS NOT NULL OR tested_at IS NULL LIMIT 1")):
        raise RuntimeError("Cannot remove source pause or untested lifecycle state from the registry")
    op.drop_constraint("ck_ontology_pg_source_pause", "ontology_data_connections", type_="check", schema="app")
    op.drop_column("ontology_data_connections", "paused_at", schema="app")
    op.alter_column("ontology_data_connections", "tested_at", nullable=False, schema="app")
