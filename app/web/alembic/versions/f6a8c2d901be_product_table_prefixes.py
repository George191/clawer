"""Rename product metadata tables in place, preserving relation identity and data."""

import re

from alembic import op
from sqlalchemy import text

revision = "f6a8c2d901be"
down_revision = "a71c92d4e630"
branch_labels = None
depends_on = None

TABLE_RENAMES = (
    ("ai_collect_analyses", "scout_analyses"),
    ("ai_collect_template_alias", "scout_template_alias"),
    ("ai_collect_templates", "scout_templates"),
    ("automation_task_logs", "scout_task_logs"),
    ("automation_task_runs", "scout_task_runs"),
    ("automation_task_stats_legacy", "scout_task_stats_legacy"),
    ("automation_tasks", "scout_tasks"),
    ("automation_workflows", "scout_workflows"),
    ("ontology_action_events", "helio_action_events"),
    ("ontology_asset_collected_stats", "helio_asset_collected_stats"),
    ("ontology_asset_daily_job_quality", "helio_asset_daily_job_quality"),
    ("ontology_asset_field_freshness", "helio_asset_field_freshness"),
    ("ontology_asset_freshness_policies", "helio_asset_freshness_policies"),
    ("ontology_asset_freshness_transitions", "helio_asset_freshness_transitions"),
    ("ontology_asset_min_rows", "helio_asset_min_rows"),
    ("ontology_asset_quality_incidents", "helio_asset_quality_incidents"),
    ("ontology_asset_quality_observations", "helio_asset_quality_observations"),
    ("ontology_asset_quality_transitions", "helio_asset_quality_transitions"),
    ("ontology_asset_weekly_job_quality", "helio_asset_weekly_job_quality"),
    ("ontology_batch_source_identities", "helio_batch_source_identities"),
    ("ontology_batch_sync_attempts", "helio_batch_sync_attempts"),
    ("ontology_batch_sync_incremental_watermark", "helio_batch_sync_incremental_watermark"),
    ("ontology_batch_sync_plans", "helio_batch_sync_plans"),
    ("ontology_batch_sync_receipts", "helio_batch_sync_receipts"),
    ("ontology_batch_sync_requests", "helio_batch_sync_requests"),
    ("ontology_batch_sync_schedule_jobs", "helio_batch_sync_schedule_jobs"),
    ("ontology_batch_sync_schedules", "helio_batch_sync_schedules"),
    ("ontology_cdc_sandbox_binding", "helio_cdc_sandbox_binding"),
    ("ontology_cdc_sandbox_receipts", "helio_cdc_sandbox_receipts"),
    ("ontology_cdc_sandbox_version_rows", "helio_cdc_sandbox_version_rows"),
    ("ontology_cdc_sandbox_versions", "helio_cdc_sandbox_versions"),
    ("ontology_dameng_cdc_bindings", "helio_dameng_cdc_bindings"),
    ("ontology_data_connections", "helio_data_connections"),
    ("ontology_deliveries", "helio_deliveries"),
    ("ontology_explorer_result_layouts", "helio_explorer_result_layouts"),
    ("ontology_explorer_saved_items", "helio_explorer_saved_items"),
    ("ontology_link_types", "helio_link_types"),
    ("ontology_links", "helio_links"),
    ("ontology_marking_changes", "helio_marking_changes"),
    ("ontology_markings", "helio_markings"),
    ("ontology_object_favorites", "helio_object_favorites"),
    ("ontology_object_links", "helio_object_links"),
    ("ontology_object_properties", "helio_object_properties"),
    ("ontology_object_relations", "helio_object_relations"),
    ("ontology_objects", "helio_objects"),
    ("ontology_pipeline_sandbox_output_rows", "helio_pipeline_sandbox_output_rows"),
    ("ontology_pipeline_sandbox_outputs", "helio_pipeline_sandbox_outputs"),
    ("ontology_pipeline_sandbox_revisions", "helio_pipeline_sandbox_revisions"),
    ("ontology_project_profiles", "helio_project_profiles"),
    ("ontology_publications", "helio_publications"),
    ("ontology_push_inbox_apply_binding", "helio_push_inbox_apply_binding"),
    ("ontology_push_inbox_apply_receipts", "helio_push_inbox_apply_receipts"),
    ("ontology_push_inbox_receipts", "helio_push_inbox_receipts"),
    ("ontology_push_inbox_state", "helio_push_inbox_state"),
    ("ontology_registry_definitions", "helio_registry_definitions"),
    ("ontology_removed_links", "helio_removed_links"),
    ("ontology_revision_grants", "helio_revision_grants"),
    ("ontology_revision_proposals", "helio_revision_proposals"),
    ("ontology_revision_snapshots", "helio_revision_snapshots"),
    ("ontology_runtime_records", "helio_runtime_records"),
    ("ontology_saved_sets", "helio_saved_sets"),
    ("ontology_table_export_attempts", "helio_table_export_attempts"),
    ("ontology_table_export_grants", "helio_table_export_grants"),
    ("ontology_table_export_plan", "helio_table_export_plan"),
    ("ontology_table_export_receipts", "helio_table_export_receipts"),
    ("ontology_table_export_requests", "helio_table_export_requests"),
    ("ontology_transactions", "helio_transactions"),
    ("ontology_transfer_attempts", "helio_transfer_attempts"),
    ("ontology_transfer_checkpoints", "helio_transfer_checkpoints"),
    ("ontology_transfer_commit_events", "helio_transfer_commit_events"),
    ("ontology_transfer_plans", "helio_transfer_plans"),
    ("ontology_transfer_runs", "helio_transfer_runs"),
    ("ontology_transfer_schedule_jobs", "helio_transfer_schedule_jobs"),
    ("ontology_transfer_schedule_target_rejections", "helio_transfer_schedule_target_rejections"),
    ("ontology_transfer_schedules", "helio_transfer_schedules"),
    ("ontology_transfer_upstream_job_events", "helio_transfer_upstream_job_events"),
    ("ontology_transfer_upstream_jobs", "helio_transfer_upstream_jobs"),
    ("ontology_transfer_upstream_mixed_slots", "helio_transfer_upstream_mixed_slots"),
    ("ontology_transfer_upstream_parents", "helio_transfer_upstream_parents"),
    ("ontology_transfer_upstream_triggers", "helio_transfer_upstream_triggers"),
)


def _rename(pairs):
    bind = op.get_bind()
    bind.execute(text("SET LOCAL lock_timeout = '5s'"))
    bind.execute(text("SELECT pg_advisory_xact_lock(hashtext('product-table-prefixes'))"))
    present = set(bind.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='app'")))
    for old, new in pairs:
        if old in present and new in present:
            raise RuntimeError(f"Both app.{old} and app.{new} exist; reconcile before migrating")
    # Optional registries may be installed separately; never create replacement tables.
    for old, new in pairs:
        if old in present:
            op.rename_table(old, new, schema="app")
    # PostgreSQL tracks FK/view dependencies by OID, but PL/pgSQL SQL bodies contain names.
    mapping = dict(pairs)
    pattern = re.compile(r"\b(" + "|".join(mapping) + r")\b")
    definitions = bind.scalars(text("""SELECT pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='app' AND p.prokind IN ('f','p')""")).all()
    for definition in definitions:
        updated = pattern.sub(lambda match: mapping[match[0]], definition)
        if updated != definition:
            bind.exec_driver_sql(updated)


def upgrade():
    _rename(TABLE_RENAMES)


def downgrade():
    _rename(tuple((new, old) for old, new in reversed(TABLE_RENAMES)))
