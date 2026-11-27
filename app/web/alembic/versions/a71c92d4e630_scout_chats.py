"""Persist Scout chats and immutable shared snapshots."""

from alembic import op

revision = "a71c92d4e630"
down_revision = "e38a62f9c104"
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE app.scout_chats (
        id uuid PRIMARY KEY,
        owner_id uuid NOT NULL REFERENCES app."user"(id) ON DELETE CASCADE,
        title text NOT NULL,
        turns jsonb NOT NULL DEFAULT '[]',
        pinned boolean NOT NULL DEFAULT false,
        archived boolean NOT NULL DEFAULT false,
        revision integer NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )''')
    op.execute("CREATE INDEX scout_chats_owner ON app.scout_chats(owner_id, archived, updated_at DESC)")
    op.execute('''CREATE TABLE app.scout_chat_shares (
        id uuid PRIMARY KEY,
        chat_id uuid NOT NULL REFERENCES app.scout_chats(id) ON DELETE CASCADE,
        title text NOT NULL,
        turns jsonb NOT NULL,
        audience text NOT NULL CHECK (audience IN ('anyone','invited')),
        emails jsonb NOT NULL DEFAULT '[]',
        created_at timestamptz NOT NULL DEFAULT now()
    )''')
    op.execute("CREATE INDEX scout_chat_shares_chat ON app.scout_chat_shares(chat_id)")
    op.execute('''CREATE TABLE app.scout_chat_schedules (
        chat_id uuid PRIMARY KEY REFERENCES app.scout_chats(id) ON DELETE CASCADE,
        request jsonb NOT NULL,
        interval_seconds integer NOT NULL CHECK (interval_seconds >= 3600),
        next_run_at timestamptz NOT NULL,
        last_run_id uuid REFERENCES app.agent_runs(id) ON DELETE SET NULL,
        last_error text,
        enabled boolean NOT NULL DEFAULT true
    )''')


def downgrade():
    op.drop_table("scout_chat_schedules", schema="app")
    op.drop_table("scout_chat_shares", schema="app")
    op.drop_table("scout_chats", schema="app")
