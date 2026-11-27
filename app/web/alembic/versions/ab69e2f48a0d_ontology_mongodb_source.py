"""Allow saved MongoDB document database sources."""
from alembic import op
from sqlalchemy import text

from app.etl.schema.data_connections import (
    DATA_CONNECTIONS_MONGODB_DDL, DATA_CONNECTIONS_MONGODB_ENABLED, ENGINE_CHECK,
)

# Preserve the SQL contract of this historical revision.
DATA_CONNECTIONS_MONGODB_DDL = DATA_CONNECTIONS_MONGODB_DDL.replace("helio_", "ontology_")
DATA_CONNECTIONS_MONGODB_ENABLED = DATA_CONNECTIONS_MONGODB_ENABLED.replace("helio_", "ontology_")

revision = "ab69e2f48a0d"
down_revision = "18f214f739c5"
branch_labels = None
depends_on = None

_REGISTRY = "app.ontology_data_connections"


def _lock_installed(bind):
    bind.execute(text("SET LOCAL lock_timeout = '3s'"))
    bind.execute(text("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext("
                      "'app.ontology_data_connections_migration'))"))
    if not bind.scalar(text(f"SELECT pg_catalog.to_regclass('{_REGISTRY}') IS NOT NULL")):
        return False
    bind.execute(text(f"LOCK TABLE {_REGISTRY} IN ACCESS EXCLUSIVE MODE"))
    return True


def upgrade():
    bind = op.get_bind()
    if _lock_installed(bind) and not bind.scalar(text(DATA_CONNECTIONS_MONGODB_ENABLED)):
        op.execute(DATA_CONNECTIONS_MONGODB_DDL)


def downgrade():
    bind = op.get_bind()
    if not _lock_installed(bind) or not bind.scalar(text(DATA_CONNECTIONS_MONGODB_ENABLED)):
        return
    if bind.scalar(text(f"SELECT 1 FROM {_REGISTRY} WHERE engine = 'mongodb' LIMIT 1")):
        raise RuntimeError("Cannot remove MongoDB source support while MongoDB sources are saved")
    op.execute(f"""
        ALTER TABLE {_REGISTRY}
          DROP CONSTRAINT ck_ontology_source_engine_v2,
          ADD {ENGINE_CHECK}
    """)
