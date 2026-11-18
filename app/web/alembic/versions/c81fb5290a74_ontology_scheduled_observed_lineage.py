"""Capture exact source heap identity on newly committed clock transfers."""

import sqlalchemy as sa
from alembic import op

revision = "c81fb5290a74"
down_revision = "deb2f8719046"
branch_labels = None
depends_on = None

_RUNS = "ontology_transfer_runs"
_ATTEMPTS = "ontology_transfer_attempts"
_CHECK = "ck_ontology_transfer_run_scheduled_source"
_SOURCE_INDEX = "ix_ontology_transfer_run_scheduled_source"
_ATTEMPT_INDEX = "ix_ontology_transfer_attempt_clock_receipt"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column(_RUNS, sa.Column("scheduled_source_oid", sa.BigInteger(), nullable=True))
    op.add_column(_RUNS, sa.Column("scheduled_source_filenode", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        _CHECK, _RUNS,
        """((scheduled_source_oid IS NULL AND scheduled_source_filenode IS NULL) OR
              (scheduled_source_oid BETWEEN 1 AND 4294967295
              AND scheduled_source_filenode BETWEEN 1 AND 4294967295
              AND target_oid > 0 AND target_filenode > 0 AND rows_affected > 0
              AND write_mode = 'upsert' AND cursor_through IS NULL AND has_more IS NULL
              AND initiated_by = '827a40a4-ea50-5e8f-a4f8-191ebfadd1a6'::uuid)) IS TRUE""",
    )
    op.create_index(_SOURCE_INDEX, _RUNS, ["source_schema", "source_table", "recorded_at", "id"],
                    postgresql_where=sa.text("scheduled_source_oid IS NOT NULL"))
    op.create_index(_ATTEMPT_INDEX, _ATTEMPTS, ["receipt_id"],
                    postgresql_where=sa.text("scope_trigger IN ('daily_utc','week_utc') AND status = 'committed' "
                                             "AND receipt_id IS NOT NULL"))

    op.execute("""
        CREATE FUNCTION public.ontology_transfer_keep_scheduled_source() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            IF NEW.scheduled_source_oid IS DISTINCT FROM OLD.scheduled_source_oid
                OR NEW.scheduled_source_filenode IS DISTINCT FROM OLD.scheduled_source_filenode
            THEN
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'A committed scheduled source cannot change';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER ontology_transfer_keep_scheduled_source
        BEFORE UPDATE OF scheduled_source_oid, scheduled_source_filenode ON public.{_RUNS}
        FOR EACH ROW EXECUTE FUNCTION public.ontology_transfer_keep_scheduled_source()
    """)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute(f"LOCK TABLE public.{_RUNS}, public.{_ATTEMPTS} IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(f"""
        SELECT EXISTS (SELECT 1 FROM public.{_RUNS}
                       WHERE scheduled_source_oid IS NOT NULL OR scheduled_source_filenode IS NOT NULL)
    """)):
        raise RuntimeError("Cannot discard committed Daily or Weekly UTC source provenance")
    op.execute(f"DROP TRIGGER ontology_transfer_keep_scheduled_source ON public.{_RUNS}")
    op.execute("DROP FUNCTION public.ontology_transfer_keep_scheduled_source()")
    op.drop_index(_ATTEMPT_INDEX, table_name=_ATTEMPTS)
    op.drop_index(_SOURCE_INDEX, table_name=_RUNS)
    op.drop_constraint(_CHECK, _RUNS, type_="check")
    op.drop_column(_RUNS, "scheduled_source_filenode")
    op.drop_column(_RUNS, "scheduled_source_oid")
