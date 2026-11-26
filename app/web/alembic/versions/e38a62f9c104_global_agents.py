"""Global agent storage, system definitions and explicit sharing grants."""

import hashlib
import json

from alembic import op
from sqlalchemy import text

revision = "e38a62f9c104"
down_revision = "d29c0e317fa2"
branch_labels = None
depends_on = None

SYSTEM_ID = "c011ec70-0000-4000-8000-000000000001"


def upgrade():
    for name in ("settings", "runs", "events"):
        op.rename_table("collection_agent_" + name, "agent_" + name, schema="app")
    for old, new in (("collection_agent_claim", "agent_claim"), ("collection_agent_owner", "agent_owner")):
        op.execute(f"ALTER INDEX app.{old} RENAME TO {new}")
    # Rename implicit indexes/constraints too; references and existing rows remain intact.
    for table, names in {
        "settings": ("pkey", "singleton_check", "updated_by_fkey"),
        "runs": ("pkey", "owner_id_fkey", "idempotency_key_check", "status_check", "stage_check",
                 "attempts_check", "owner_id_idempotency_key_key", "check", "check1"),
        "events": ("pkey", "run_id_fkey"),
    }.items():
        for name in names:
            op.execute(f"ALTER TABLE app.agent_{table} RENAME CONSTRAINT collection_agent_{table}_{name} TO agent_{table}_{name}")
    op.execute("ALTER TABLE app.agent_definitions ALTER COLUMN owner_id DROP NOT NULL, ALTER COLUMN connection_id DROP NOT NULL")
    op.execute("""ALTER TABLE app.agent_definitions
        ADD COLUMN system_key text UNIQUE,
        ADD COLUMN visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','shared','public')),
        ADD CONSTRAINT agent_definition_origin CHECK (
            (system_key IS NOT NULL AND owner_id IS NULL AND connection_id IS NULL AND visibility='public')
            OR (system_key IS NULL AND owner_id IS NOT NULL AND connection_id IS NOT NULL))""")
    op.execute('''CREATE TABLE app.agent_shares (
        agent_id uuid NOT NULL REFERENCES app.agent_definitions(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES app."user"(id) ON DELETE CASCADE,
        PRIMARY KEY (agent_id,user_id)
    )''')
    op.execute("CREATE INDEX agent_shares_user ON app.agent_shares(user_id)")
    config = json.dumps({"name": "Collection", "description": "System agent for public web evidence and collection templates.",
                         "harness": "collection", "instructions": "", "model": "gpt-6-sol", "reasoning_effort": "high",
                         "timeout_seconds": 120, "max_output_tokens": 8192, "enabled": False})
    op.execute(f"""INSERT INTO app.agent_definitions(id,system_key,visibility,config)
        VALUES ('{SYSTEM_ID}','collection','public','{config}'::jsonb)""")
    op.execute("""ALTER TABLE app.agent_runs
        DROP CONSTRAINT agent_runs_stage_check, DROP CONSTRAINT agent_runs_check,
        ADD COLUMN agent_id uuid REFERENCES app.agent_definitions(id) ON DELETE SET NULL,
        ADD COLUMN product_domain text NOT NULL DEFAULT 'ai-collect',
        ADD COLUMN harness text NOT NULL DEFAULT 'collection',
        ADD COLUMN stage_count integer NOT NULL DEFAULT 4 CHECK (stage_count>0),
        ADD CONSTRAINT agent_runs_stage_check CHECK (stage BETWEEN 0 AND stage_count),
        ADD CONSTRAINT agent_runs_complete CHECK (status <> 'completed' OR (stage=stage_count AND result IS NOT NULL))""")
    bind = op.get_bind()
    for row in bind.execute(text("SELECT id,request,model_config FROM app.agent_runs")).mappings():
        previous = dict(row["request"])
        agent_id = previous.pop("agent_id", None) or SYSTEM_ID
        request = {"agent_id": agent_id, "product_domain": "ai-collect", "input": previous}
        snapshot = {**row["model_config"], "harness": "collection"}
        if "agent_id" not in snapshot:
            snapshot.update(agent_id=SYSTEM_ID, agent_revision=1, connection_revision=snapshot.pop("revision"), instructions="")
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        bind.execute(text("""UPDATE app.agent_runs SET
            agent_id=(SELECT id FROM app.agent_definitions WHERE id=CAST(:agent AS uuid)),
            request=CAST(:request AS jsonb),request_hash=:hash,model_config=CAST(:config AS jsonb) WHERE id=:id"""),
            {"id": row["id"], "agent": agent_id, "request": encoded, "hash": hashlib.sha256(encoded.encode()).hexdigest(),
             "config": json.dumps(snapshot)})
    op.execute("ALTER TABLE app.agent_runs ALTER COLUMN product_domain DROP DEFAULT, ALTER COLUMN harness DROP DEFAULT, ALTER COLUMN stage_count DROP DEFAULT")
    op.execute("CREATE INDEX agent_runs_domain ON app.agent_runs(product_domain,created_at DESC)")


def downgrade():
    # Publishing and per-recipient access cannot be represented by the previous schema.
    raise RuntimeError("Global agent sharing requires an explicit data migration before downgrade")
