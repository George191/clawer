"""Persist an exact same-scope donor for an appended Builder restoration."""

from alembic import op
import sqlalchemy as sa

revision = "f27ad8ce609b"
down_revision = "d31f0b483a76"
branch_labels = None
depends_on = None

_TABLE = "ontology_pipeline_sandbox_revisions"
_UNIQUE = "uq_pipeline_revision_generation_scope"
_FOREIGN = "fk_pipeline_revision_restore_scope"
_CHECK = "ck_pipeline_revision_restored_donor"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.add_column(_TABLE, sa.Column("restored_from_generation", sa.Integer(), nullable=True))
    op.create_unique_constraint(_UNIQUE, _TABLE, ["generation", "stream_marker", "physical_digest"])
    op.create_check_constraint(
        _CHECK, _TABLE, "restored_from_generation IS NULL OR (restored_from_generation>0 "
        "AND restored_from_generation<generation AND marking_level IN (1,2)) IS TRUE",
    )
    op.create_foreign_key(
        _FOREIGN, _TABLE, _TABLE, ["restored_from_generation", "stream_marker", "physical_digest"],
        ["generation", "stream_marker", "physical_digest"],
    )
    op.execute("""
        CREATE FUNCTION public.ontology_pipeline_keep_restore_provenance() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            IF NEW.restored_from_generation IS DISTINCT FROM OLD.restored_from_generation THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Builder restore provenance is immutable';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER ontology_pipeline_keep_restore_provenance BEFORE UPDATE OF restored_from_generation
        ON public.{_TABLE} FOR EACH ROW EXECUTE FUNCTION public.ontology_pipeline_keep_restore_provenance()
    """)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(f"LOCK TABLE ONLY public.{_TABLE} IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM ONLY public.{_TABLE} WHERE restored_from_generation IS NOT NULL)")):
        raise RuntimeError("Cannot discard durable Builder restore provenance")
    op.execute(f"DROP TRIGGER ontology_pipeline_keep_restore_provenance ON public.{_TABLE}")
    op.execute("DROP FUNCTION public.ontology_pipeline_keep_restore_provenance()")
    op.drop_constraint(_FOREIGN, _TABLE, type_="foreignkey")
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_constraint(_UNIQUE, _TABLE, type_="unique")
    op.drop_column(_TABLE, "restored_from_generation")
