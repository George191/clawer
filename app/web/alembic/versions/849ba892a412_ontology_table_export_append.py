"""Add explicit mode to the synthetic export ledger, preserving historical Upsert hashes."""
import sqlalchemy as sa
from alembic import op

revision = "849ba892a412"
down_revision = "68ff119daf40"
branch_labels = None
depends_on = None

_TABLES = ("plan", "grants", "requests")
_PLAN = "ontology_table_export_plan"
_DESTINATION = "ck_ontology_table_export_plan_destination"
_LEGACY = "(destination = 'primary' AND receiver_proof IS NULL) OR " \
          "(destination = 'secondary' AND (receiver_proof ~ '^[0-9a-f]{64}$') IS TRUE)"
_EXACT = "(destination = 'primary' AND write_mode = 'upsert' AND receiver_proof IS NULL) OR " \
         "((destination = 'secondary' OR (destination = 'primary' AND write_mode = 'append')) " \
         "AND (receiver_proof ~ '^[0-9a-f]{64}$') IS TRUE)"



def _install_admission_guard() -> None:
    # Older senders omit write_mode (therefore default Upsert). They must not
    # approve/admit an Upsert against a reviewed Append route during rollout.
    op.execute("""
        CREATE FUNCTION app.guard_table_export_write_mode() RETURNS trigger
        LANGUAGE plpgsql SET search_path TO pg_catalog, pg_temp AS $guard$
        DECLARE
            current_mode text;
            current_revision timestamptz;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF (NEW.destination, NEW.plan_updated_at, NEW.write_mode)
                    IS DISTINCT FROM (OLD.destination, OLD.plan_updated_at, OLD.write_mode) THEN
                    RAISE EXCEPTION 'Export admission scope is immutable' USING ERRCODE = '23514';
                END IF;
                RETURN NEW; -- Preserve exact payload digest pinning and existing audit use.
            END IF;
            SELECT write_mode, updated_at INTO current_mode, current_revision
              FROM app.ontology_table_export_plan WHERE destination = NEW.destination FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'An exact reviewed export scope is required' USING ERRCODE = '23514';
            END IF;
            IF TG_TABLE_NAME = 'ontology_table_export_grants' THEN
                IF NEW.action = 'revoked' THEN
                    -- A separately acknowledged current review may revoke its older grant.
                    -- Archive the original grant mode and revision, never the new plan's mode.
                    SELECT write_mode, plan_updated_at INTO current_mode, current_revision
                      FROM app.ontology_table_export_grants
                      WHERE destination = NEW.destination
                      ORDER BY generation DESC LIMIT 1;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'An exact prior export grant is required' USING ERRCODE = '23514';
                    END IF;
                END IF;
            END IF;
            IF (NEW.write_mode, NEW.plan_updated_at) IS DISTINCT FROM (current_mode, current_revision) THEN
                RAISE EXCEPTION 'Export admission mode or revision does not match its reviewed scope'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $guard$
    """)
    for item in ("grants", "requests"):
        op.execute(f"""
            CREATE TRIGGER guard_table_export_write_mode
            BEFORE INSERT OR UPDATE ON app.ontology_table_export_{item}
            FOR EACH ROW EXECUTE FUNCTION app.guard_table_export_write_mode()
        """)


def upgrade() -> None:
    for item in _TABLES:
        name = f"ontology_table_export_{item}"
        op.add_column(name, sa.Column("write_mode", sa.String(6), server_default="upsert", nullable=False))
        op.create_check_constraint(f"ck_table_export_{item}_write_mode", name, "write_mode IN ('upsert','append')")
    op.drop_constraint(_DESTINATION, _PLAN, type_="check")
    op.create_check_constraint(_DESTINATION, _PLAN, _EXACT)
    _install_admission_guard()


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE ontology_table_export_plan, ontology_table_export_grants, "
               "ontology_table_export_requests IN ACCESS EXCLUSIVE MODE")
    for item in _TABLES:
        if op.get_bind().scalar(sa.text(f"SELECT 1 FROM ontology_table_export_{item} WHERE write_mode='append' LIMIT 1")):
            raise RuntimeError("Cannot remove reviewed Append mode or its recorded history")
    for item in ("grants", "requests"):
        op.execute(f"DROP TRIGGER guard_table_export_write_mode ON app.ontology_table_export_{item}")
    op.execute("DROP FUNCTION app.guard_table_export_write_mode()")
    op.drop_constraint(_DESTINATION, _PLAN, type_="check")
    op.create_check_constraint(_DESTINATION, _PLAN, _LEGACY)
    for item in reversed(_TABLES):
        name = f"ontology_table_export_{item}"
        op.drop_constraint(f"ck_table_export_{item}_write_mode", name, type_="check")
        op.drop_column(name, "write_mode")
