"""Owner-scoped agent definitions and reusable encrypted model connections."""
from alembic import op

revision = "d29c0e317fa2"
down_revision = "c801b426df90"
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE app.agent_connections (
        id uuid PRIMARY KEY,
        owner_id uuid NOT NULL REFERENCES app."user"(id) ON DELETE CASCADE,
        name text NOT NULL, base_url text NOT NULL,
        api_key_ciphertext text NOT NULL,
        revision bigint NOT NULL DEFAULT 1,
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (owner_id, name)
    )''')
    op.execute('''CREATE TABLE app.agent_definitions (
        id uuid PRIMARY KEY,
        owner_id uuid NOT NULL REFERENCES app."user"(id) ON DELETE CASCADE,
        connection_id uuid NOT NULL REFERENCES app.agent_connections(id) ON DELETE RESTRICT,
        config jsonb NOT NULL,
        revision bigint NOT NULL DEFAULT 1,
        tested_revision bigint,
        tested_connection_revision bigint,
        updated_at timestamptz NOT NULL DEFAULT now()
    )''')
    op.execute("CREATE INDEX agent_definitions_owner ON app.agent_definitions(owner_id)")
    op.execute("CREATE INDEX agent_connections_owner ON app.agent_connections(owner_id)")


def downgrade():
    op.execute("DROP TABLE app.agent_definitions")
    op.execute("DROP TABLE app.agent_connections")
