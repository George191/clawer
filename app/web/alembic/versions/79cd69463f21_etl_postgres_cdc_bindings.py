"""Persist internal PostgreSQL CDC identity independently of physical names."""

import hashlib
import json

from alembic import context, op
from sqlalchemy import text

from app.config.settings import settings

revision = "79cd69463f21"
down_revision = "a9d614b22e73"
branch_labels = None
depends_on = None

_IDENTITY_KEYS = ("connector_name", "topic_prefix", "slot_name", "publication_name")


def upgrade() -> None:
    op.execute("""CREATE TABLE app.etl_postgres_cdc_bindings (
        connection_id uuid PRIMARY KEY REFERENCES app.ontology_data_connections(id) ON DELETE CASCADE,
        connector_name text NOT NULL UNIQUE,
        topic_prefix text NOT NULL UNIQUE,
        slot_name text NOT NULL,
        publication_name text NOT NULL
    )""")
    if context.is_offline_mode():
        raise RuntimeError("Run the CDC identity migration against the system database to preserve existing offsets")
    bind = op.get_bind()
    row = bind.execute(text("""SELECT s.id, s.config
        FROM app.etl_business_connection b JOIN app.ontology_data_connections s ON s.id=b.connection_id
        WHERE b.singleton AND s.engine='postgresql'""")).mappings().first()
    overrides = context.get_x_argument(as_dictionary=True)
    inventory = {key: overrides.get(f"legacy_cdc_{key}") for key in _IDENTITY_KEYS}
    if any(inventory.values()):
        if row is None or not all(inventory.values()):
            raise RuntimeError("Supply all four existing CDC identity values for the saved legacy business connection")
    elif row is None or not settings.kafka_topic:
        return
    else:
        config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
        database = config["database"]
        prefix = f"{settings.kafka_topic}-postgres-cdc"
        digest = hashlib.sha256(f"{prefix}:{database}".encode()).hexdigest()[:16]
        inventory = {"connector_name": f"{prefix}-{database}", "topic_prefix": prefix,
                     "slot_name": f"spider_cdc_{digest}", "publication_name": f"spider_cdc_{digest}"}
    bind.execute(text("""INSERT INTO app.etl_postgres_cdc_bindings
        (connection_id, connector_name, topic_prefix, slot_name, publication_name)
        VALUES (:id, :connector_name, :topic_prefix, :slot_name, :publication_name)"""), {"id": row["id"], **inventory})


def downgrade() -> None:
    op.drop_table("etl_postgres_cdc_bindings", schema="app")
