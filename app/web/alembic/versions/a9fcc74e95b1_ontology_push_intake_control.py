"""Keep the synthetic Push lifecycle on the singleton that serializes acceptance."""
import sqlalchemy as sa
from alembic import op

revision = "a9fcc74e95b1"
down_revision = "b86d2b1d4e98"
branch_labels = None
depends_on = None

_STATE = "ontology_push_inbox_state"
_TRIGGER = "push_inbox_intake_fence"
_CHECK = "ck_push_inbox_intake_generation"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column(_STATE, sa.Column("intake_generation", sa.Integer(), nullable=False, server_default="0"), schema="app")
    op.add_column(_STATE, sa.Column("intake_paused_at", sa.DateTime(timezone=True)), schema="app")
    op.create_check_constraint(_CHECK, _STATE,
                               "intake_generation >= 0 AND (intake_generation > 0 OR intake_paused_at IS NULL)",
                               schema="app")
    # An old ingress binary always updates these counters in the transaction inserting
    # its receipt. Fence that update without changing the separately reviewed Apply source.
    op.execute("""
        CREATE FUNCTION app.push_inbox_intake_fence() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, app, pg_temp AS $$
        BEGIN
            IF OLD.intake_paused_at IS NOT NULL AND
                (NEW.accepted_count, NEW.window_epoch, NEW.window_accepted)
                IS DISTINCT FROM (OLD.accepted_count, OLD.window_epoch, OLD.window_accepted) THEN
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'Synthetic inbox unavailable';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER {_TRIGGER} BEFORE UPDATE OF accepted_count, window_epoch, window_accepted ON app.{_STATE}
        FOR EACH ROW EXECUTE FUNCTION app.push_inbox_intake_fence()
    """)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute(f"LOCK TABLE app.{_STATE} IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(f"SELECT 1 FROM app.{_STATE} WHERE intake_generation > 0 LIMIT 1")):
        raise RuntimeError("Cannot remove a reviewed synthetic inbox intake generation")
    op.execute(f"DROP TRIGGER {_TRIGGER} ON app.{_STATE}")
    op.execute("DROP FUNCTION app.push_inbox_intake_fence()")
    op.drop_constraint(_CHECK, _STATE, type_="check", schema="app")
    op.drop_column(_STATE, "intake_paused_at", schema="app")
    op.drop_column(_STATE, "intake_generation", schema="app")
