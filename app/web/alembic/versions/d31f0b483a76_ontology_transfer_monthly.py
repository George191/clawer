"""Add isolated Monthly UTC cadence to the existing transfer scheduler."""

from alembic import op
import sqlalchemy as sa

revision = "d31f0b483a76"
down_revision = "e57b4216c9a8"
branch_labels = None
depends_on = None

_PLANS = "ontology_transfer_plans"
_JOBS = "ontology_transfer_schedule_jobs"
_ATTEMPTS = "ontology_transfer_attempts"
_PLAN_CHECK = "ck_ontology_transfer_plans_schedule"
_JOB_CHECK = "ck_transfer_schedule_job_month_day"
_ATTEMPT_CHECK = "ck_ontology_transfer_attempt_timeline_scope"
_WEEKLY_PLAN = (
    "((((trigger = 'manual' AND schedule_time IS NULL) OR "
    "(trigger = 'scheduled' AND schedule_time IS NOT NULL AND schedule_time ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$') OR "
    "(trigger = 'upstream' AND schedule_time IS NULL AND write_mode = 'upsert' "
    "AND source_schema = 'ts_transfer_sandbox' AND target_schema = 'ts_transfer_sandbox')) "
    "AND schedule_weekday_utc IS NULL) OR "
    "(trigger = 'weekly' AND schedule_time IS NOT NULL "
    "AND schedule_time ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$' AND schedule_weekday_utc BETWEEN 1 AND 7 "
    "AND write_mode = 'upsert' AND source_schema = 'ts_transfer_sandbox' AND target_schema = 'ts_transfer_sandbox')) IS TRUE"
)
_MONTHLY_PLAN = (
    f"((({_WEEKLY_PLAN}) AND schedule_day_of_month_utc IS NULL) OR "
    "(trigger = 'monthly' AND schedule_time IS NOT NULL AND schedule_time ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$' "
    "AND schedule_weekday_utc IS NULL AND schedule_day_of_month_utc BETWEEN 1 AND 28 "
    "AND write_mode = 'upsert' AND source_schema = 'ts_transfer_sandbox' AND target_schema = 'ts_transfer_sandbox')) IS TRUE"
)
_ATTEMPT_PREFIX = (
    "(target_table IS NULL) = (plan_updated_at IS NULL) "
    "AND (target_table IS NULL) = (source_table IS NULL) "
    "AND (target_table IS NULL) = (scope_trigger IS NULL) "
    "AND (target_oid IS NULL) = (target_filenode IS NULL) "
    "AND (target_table IS NOT NULL OR target_oid IS NULL) "
)
_WEEKLY_ATTEMPT = (
    _ATTEMPT_PREFIX + "AND (scope_trigger IS NULL OR scope_trigger IN ('manual','daily_utc','upstream','week_utc')) "
    "AND (scope_trigger IS NULL OR scope_trigger NOT IN ('daily_utc','week_utc') OR schedule_job_id IS NOT NULL)"
)
_MONTHLY_ATTEMPT = (
    _ATTEMPT_PREFIX + "AND (scope_trigger IS NULL OR scope_trigger IN ('manual','daily_utc','upstream','week_utc','month_utc')) "
    "AND (scope_trigger IS NULL OR scope_trigger NOT IN ('daily_utc','week_utc','month_utc') OR schedule_job_id IS NOT NULL)"
)


def _replace(table: str, name: str, expression: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, expression)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column(_PLANS, sa.Column("schedule_day_of_month_utc", sa.Integer(), nullable=True))
    op.add_column(_JOBS, sa.Column("schedule_day_of_month_utc", sa.Integer(), nullable=True))
    _replace(_PLANS, _PLAN_CHECK, _MONTHLY_PLAN)
    _replace(_ATTEMPTS, _ATTEMPT_CHECK, _MONTHLY_ATTEMPT)
    op.create_check_constraint(
        _JOB_CHECK, _JOBS,
        "schedule_day_of_month_utc IS NULL OR (schedule_weekday_utc IS NULL AND schedule_day_of_month_utc BETWEEN 1 AND 28 "
        "AND schedule_day_of_month_utc=EXTRACT(DAY FROM slot AT TIME ZONE 'UTC') AND state <> 'canceled')",
    )
    op.execute("""
        CREATE FUNCTION app.ontology_transfer_keep_month_day() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, app, pg_temp AS $$
        BEGIN
            IF NEW.schedule_day_of_month_utc IS DISTINCT FROM OLD.schedule_day_of_month_utc THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='A recorded UTC job cannot change cadence';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER ontology_transfer_keep_month_day BEFORE UPDATE OF schedule_day_of_month_utc ON app.{_JOBS}
        FOR EACH ROW EXECUTE FUNCTION app.ontology_transfer_keep_month_day()
    """)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute(f"LOCK TABLE app.{_PLANS}, app.{_JOBS}, app.{_ATTEMPTS} IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(f"""
        SELECT EXISTS(SELECT 1 FROM app.{_PLANS} WHERE trigger='monthly' OR schedule_day_of_month_utc IS NOT NULL)
            OR EXISTS(SELECT 1 FROM app.{_JOBS} WHERE schedule_day_of_month_utc IS NOT NULL)
            OR EXISTS(SELECT 1 FROM app.{_ATTEMPTS} WHERE scope_trigger='month_utc')
    """)):
        raise RuntimeError("Cannot discard reviewed Monthly UTC plans or their durable execution history")
    op.execute(f"DROP TRIGGER ontology_transfer_keep_month_day ON app.{_JOBS}")
    op.execute("DROP FUNCTION app.ontology_transfer_keep_month_day()")
    op.drop_constraint(_JOB_CHECK, _JOBS, type_="check")
    _replace(_PLANS, _PLAN_CHECK, _WEEKLY_PLAN)
    _replace(_ATTEMPTS, _ATTEMPT_CHECK, _WEEKLY_ATTEMPT)
    op.drop_column(_JOBS, "schedule_day_of_month_utc")
    op.drop_column(_PLANS, "schedule_day_of_month_utc")
