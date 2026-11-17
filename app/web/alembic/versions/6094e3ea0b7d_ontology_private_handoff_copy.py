"""Private, independently approved synthetic handoff storage (unprovisioned)."""
import sqlalchemy as sa
from alembic import op

revision = "6094e3ea0b7d"
down_revision = "b0be6e75c221"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA ontology_handoff_private")
    op.execute("REVOKE ALL ON SCHEMA ontology_handoff_private FROM PUBLIC")
    op.execute("""
        CREATE TABLE ontology_handoff_private.sink_state (
            singleton boolean PRIMARY KEY CHECK (singleton),
            marker uuid NOT NULL
        );
    """)
    op.execute("""
        CREATE TABLE ontology_handoff_private.copies (
            id uuid PRIMARY KEY, request_id uuid NOT NULL UNIQUE, output_id uuid NOT NULL,
            sink_generation uuid NOT NULL, actor_id uuid NOT NULL,
            config_hash text NOT NULL CHECK (config_hash ~ '^[0-9a-f]{64}$'),
            result_hash text NOT NULL CHECK (result_hash ~ '^[0-9a-f]{64}$'),
            stream_marker uuid NOT NULL,
            marking_level smallint NOT NULL CHECK (marking_level IN (1,2)),
            rows_copied integer NOT NULL CHECK (rows_copied BETWEEN 0 AND 100),
            recorded_at timestamptz NOT NULL CHECK (isfinite(recorded_at)),
            UNIQUE (output_id, sink_generation)
        )
    """)
    op.execute("""
        CREATE TABLE ontology_handoff_private.copy_rows (
            copy_id uuid NOT NULL REFERENCES ontology_handoff_private.copies(id),
            position integer NOT NULL CHECK (position BETWEEN 1 AND 100),
            row_values text[] NOT NULL CHECK (array_ndims(row_values)=1 AND array_lower(row_values,1)=1
                AND cardinality(row_values) BETWEEN 1 AND 2 AND array_position(row_values,NULL) IS NULL),
            PRIMARY KEY (copy_id, position)
        )
    """)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE ontology_handoff_private.sink_state, ontology_handoff_private.copies, "
               "ontology_handoff_private.copy_rows IN ACCESS EXCLUSIVE MODE")
    if any(op.get_bind().scalar(sa.text(f"SELECT 1 FROM ontology_handoff_private.{name} LIMIT 1"))
           for name in ("sink_state", "copies", "copy_rows")):
        raise RuntimeError("Cannot remove provisioned synthetic handoff or recorded copy evidence")
    op.execute("DROP TABLE ontology_handoff_private.copy_rows")
    op.execute("DROP TABLE ontology_handoff_private.copies")
    op.execute("DROP TABLE ontology_handoff_private.sink_state")
    op.execute("DROP SCHEMA ontology_handoff_private")
