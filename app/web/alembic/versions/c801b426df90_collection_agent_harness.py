"""Durable, owner-scoped collection harness and encrypted system model settings."""

from alembic import op

revision = "c801b426df90"
down_revision = "79cd69463f21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE app.collection_agent_settings (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        config jsonb NOT NULL DEFAULT '{}',
        api_key_ciphertext text,
        revision bigint NOT NULL DEFAULT 0,
        tested_revision bigint,
        tested_at timestamptz,
        updated_by uuid REFERENCES app."user"(id),
        updated_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("INSERT INTO app.collection_agent_settings (singleton) VALUES (true)")
    op.execute("""CREATE TABLE app.collection_agent_runs (
        id uuid PRIMARY KEY,
        owner_id uuid NOT NULL REFERENCES app."user"(id) ON DELETE CASCADE,
        idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
        request_hash text NOT NULL,
        request jsonb NOT NULL,
        model_config jsonb NOT NULL,
        status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed','cancelled')),
        stage integer NOT NULL DEFAULT 0 CHECK (stage BETWEEN 0 AND 4),
        checkpoints jsonb NOT NULL DEFAULT '{}',
        result jsonb,
        error_code text,
        attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
        lease_token uuid,
        lease_until timestamptz,
        event_seq bigint NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (owner_id, idempotency_key),
        CHECK (status <> 'completed' OR (stage=4 AND result IS NOT NULL)),
        CHECK ((status='running') = (lease_token IS NOT NULL AND lease_until IS NOT NULL))
    )""")
    op.execute("CREATE INDEX collection_agent_claim ON app.collection_agent_runs (status, lease_until, created_at) WHERE status IN ('queued','running')")
    op.execute("CREATE INDEX collection_agent_owner ON app.collection_agent_runs (owner_id, created_at DESC)")
    op.execute("""CREATE TABLE app.collection_agent_events (
        run_id uuid NOT NULL REFERENCES app.collection_agent_runs(id) ON DELETE CASCADE,
        seq bigint NOT NULL,
        event jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, seq)
    )""")


def downgrade() -> None:
    op.drop_table("collection_agent_events", schema="app")
    op.drop_table("collection_agent_runs", schema="app")
    op.drop_table("collection_agent_settings", schema="app")
