from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from anyio import fail_after
from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import AwareDatetime
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.tasks.extractors.keyword_files import MAX_FILE_SIZE
from app.web.api.dependencies.common import CurrentActiveSuperuser, SessionDep, get_current_active_superuser, get_current_user
from app.web.api.dependencies.session import get_db
from app.web.api.models.lineage_neighbors import (
    LineageColumnReferences, LineageCommonDownstream, LineageCommonUpstream, LineageDirection, LineageNeighbors, LineagePath,
    LineagePathTargets, ObservedLineage,
)
from app.web.api.models.indexed_id_sample import IndexedSampleCapability, IndexedSampleRequest, IndexedSampleResult
from app.web.api.models.index_preview_sandbox import IndexSandboxCapability, IndexSandboxPreview, IndexSandboxQuery
from app.web.api.models.asset_collected_stats import AssetCollectedStats
from app.web.api.models.asset_freshness import (
    AssetFreshnessCandidate, AssetFreshnessChange, AssetFreshnessResponse, AssetQualityIncident, AssetQualityIncidentAck,
    AssetSchemaCaptureRequest, AssetSchemaPreview, AssetSchemaPreviewRequest,
)
from app.web.api.models.asset_field_freshness import AssetFieldFreshnessReview, AssetFieldFreshnessStatus
from app.web.api.models.asset_min_rows import AssetMinRowsReview, AssetMinRowsStatus
from app.web.api.models.asset_job_quality import AssetJobQualityReview, AssetJobQualityStatus, WeeklyJobEvidence, WeeklyJobQualityStatus
from app.web.api.models.batch_lineage import BatchLineageDetail, BatchLineageKind, BatchLineagePage
from app.web.api.models.batch_source_identity import BatchSourceIdentityReview, BatchSourceIdentityStatus
from app.web.api.models.batch_syncs import (
    BatchSyncAttempt, BatchSyncCapabilities, BatchSyncPayload, BatchSyncPlan, BatchSyncReceipt, BatchSyncRunRequest,
)
from app.web.api.models.batch_sync_schedules import (
    BatchSyncSchedule, BatchSyncScheduleChange, BatchSyncScheduleJob, BatchSyncScheduleSummary,
)
from app.web.api.models.cdc_sandbox import CdcSandboxApply, CdcSandboxReceipt, CdcSandboxReview, CdcSandboxStatus
from app.web.api.models.cdc_sandbox_versions import (
    CdcSandboxComparison, CdcSandboxVersionDetail, CdcSandboxVersionsPage,
)
from app.web.api.models.push_inbox import PushInboxIntakeChange, PushInboxStatus
from app.web.api.models.push_inbox_apply import PushApplyRequest, PushApplyResult, PushApplyReview, PushApplyStatus
from app.web.api.models.pipeline_handoff import (
    PipelineHandoffCapability, PipelineHandoffCopyReceipt, PipelineHandoffCopyRequest, PipelineHandoffEligibility,
    PipelineHandoffRequest,
)
from app.web.api.models.pipeline_sandbox import (
    PipelineSandboxConfig, PipelineSandboxPreview, PipelineSandboxRevision, PipelineSandboxSave, PipelineSandboxStatus,
)
from app.web.api.models.pipeline_sandbox_outputs import (
    PipelineSandboxCommit, PipelineSandboxOutput, PipelineSandboxOutputCompareRequest, PipelineSandboxOutputComparison,
    PipelineSandboxOutputDetail, PipelineSandboxOutputsPage,
    PipelineSandboxRecordHistory, PipelineSandboxRecordHistoryRequest,
)
from app.web.api.models.table_exports import (
    ExportDestination,
    TableExportAttempt, TableExportCapabilities, TableExportGrant, TableExportGrantChange, TableExportGrantEvent,
    TableExportLineage, TableExportPlan, TableExportReceipt, TableExportReview, TableExportRunRequest,
)
from app.web.api.models.transfer_timeline import TimelineKind, TimelineView, TransferTimelineDetail, TransferTimelinePage
from app.web.api.models.transfer_upstream import (
    UpstreamCandidate, UpstreamMixedSlot, UpstreamTrigger, UpstreamTriggerChange, UpstreamTriggerJob, UpstreamTriggerSummary,
)
from app.web.api.models.etl import (
    CodePayload,
    DataQuery,
    FavoriteImport,
    FavoriteMutation,
    KeywordVersion,
    OffsetUpdate,
    ObjectVisibility,
    PreviewIndexCoverage,
    PreviewIndexCoverageInput,
    PreviewRowsQuery,
    SqlQuery,
)
from app.web.api.models.data_connections import (
    DataConnectionPauseChange, DataConnectionPayload, SourceCatalog, SourceColumns, SourceReferenceDirection, SourceReferences,
)
from app.web.api.models.transfer_plans import (
    TableName, TransferAttempt, TransferPlan, TransferPlanPayload, TransferPreflight, TransferRun, TransferRunCapabilities,
    TransferRunRequest, WarehouseSchema,
)
from app.web.api.models.transfer_schedules import (
    TransferSchedule, TransferScheduleChange, TransferScheduleJob, TransferScheduleJobCancel, TransferScheduleSummary,
)
from app.web.services.asset_collected_stats import read_asset_collected_stats
from app.web.services.asset_freshness import (
    acknowledge_quality_incident, capture_schema_baseline, get_asset_freshness, get_asset_freshness_candidates,
    preview_schema_baseline, put_asset_freshness,
)
from app.web.services.index_preview_sandbox import (
    index_range_pagination_available, index_sandbox_available, run_bounded_index_query, run_index_preview_until_disconnect,
)
from app.web.services.asset_field_freshness import (
    acknowledge_field_freshness, read_field_freshness, review_field_freshness,
)
from app.web.services.asset_min_rows import acknowledge_min_rows, read_min_rows, review_min_rows
from app.web.services.asset_job_quality import acknowledge_daily_job, read_job_quality, review_job_quality
from app.web.services.asset_weekly_job_quality import (
    acknowledge_weekly_job, read_weekly_job_evidence, read_weekly_job_quality, review_weekly_job_quality,
    weekly_evidence_unavailable,
)
from app.web.services.data_connections import connector_types, get_data_connections
from app.web.services.batch_lineage import list_batch_lineage, read_batch_lineage
from app.web.services.batch_source_identity import read_source_identity, verify_source_identity
from app.web.services.batch_syncs import (
    batch_sync_capabilities, list_batch_sync_attempts, list_batch_sync_runs, list_batch_syncs, run_batch_sync, save_batch_sync,
)
from app.web.services.batch_sync_schedules import (
    change_batch_sync_schedule, list_batch_sync_schedule_jobs, list_batch_sync_schedules, read_batch_sync_schedule,
)
from app.web.services.cdc_sandbox import (
    apply_cdc_sandbox, list_cdc_sandbox_receipts, read_cdc_sandbox, review_cdc_sandbox,
)
from app.web.services.cdc_sandbox_versions import compare_cdc_versions, list_cdc_versions, read_cdc_version
from app.web.services.pipeline_handoff_copy import copy_handoff
from app.web.services.pipeline_handoff import handoff_capability, handoff_eligibility
from app.web.services.pipeline_sandbox import (
    list_pipeline_revisions, preview_pipeline_sandbox, read_pipeline_revision, read_pipeline_sandbox, save_pipeline_revision,
)
from app.web.services.pipeline_sandbox_outputs import (
    commit_pipeline_output, compare_pipeline_outputs, encode_record_history, list_pipeline_outputs,
    read_pipeline_output, trace_pipeline_output_record,
)
from app.web.services.push_inbox import change_push_intake, read_push_status
from app.web.services.push_inbox_apply import apply_push_range, read_push_apply, review_push_apply
from app.web.services.table_exports import (
    change_table_export_grant, get_table_export, get_table_export_grant, get_table_export_lineage,
    list_table_export_attempts, list_table_export_grant_history, list_table_export_receipts,
    review_table_export, run_table_export, table_export_capabilities,
)
from app.web.services.synthetic_governance import marking_limit
from app.web.services.transfer_timeline import list_transfer_timeline, parse_timeline_filters, read_transfer_timeline_entry
from app.web.services.transfer_upstream import (
    change_upstream_trigger, list_upstream_candidates, list_upstream_jobs, list_upstream_mixed_slots, list_upstream_triggers, read_upstream_trigger,
)
from app.web.services import etl_runtime
from app.web.services import vulnerability_keywords as keyword_files
from app.web.services.etl_layers import get_etl_layers
from app.web.services.etl_workspace import get_etl_workspace
from app.web.services.indexed_id_sample import indexed_sample_capability, sample_indexed_ids
from app.web.services.lineage_column_references import read_lineage_column_references
from app.web.services.preview_index_coverage import preview_index_coverage
from app.web.services.transfer_observed_lineage import observed_transfer_lineage
from app.web.services.transfer_plans import list_transfer_plans, preflight_transfer_plan, save_transfer_plan
from app.web.services.transfer_runs import (
    SANDBOX, list_transfer_attempts, list_transfer_runs, run_sandbox_transfer, transfer_run_capabilities,
)
from app.web.services.transfer_schedules import (
    cancel_transfer_schedule_job, change_transfer_schedule, list_transfer_schedule_jobs, list_transfer_schedules, read_transfer_schedule,
)

router = APIRouter(prefix="/etl", tags=["etl"])
read = APIRouter(dependencies=[Depends(get_current_user)])
write = APIRouter(dependencies=[Depends(get_current_active_superuser)])


@write.get("/push-inbox-sandbox", response_model_exclude_none=True)
async def get_local_push_inbox(
    response: Response, session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PushInboxStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await read_push_status(session)
    except DBAPIError:
        raise HTTPException(503, "Synthetic inbox status is unavailable") from None


@write.put("/push-inbox-sandbox/intake", response_model_exclude_none=True)
async def put_push_inbox_intake(
    payload: PushInboxIntakeChange, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PushInboxStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await change_push_intake(session, payload)
    except DBAPIError:
        raise HTTPException(503, "Synthetic inbox intake change could not be confirmed; reload current state") from None


@write.get("/push-inbox-sandbox/apply")
async def get_push_apply(
    response: Response, session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PushApplyStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await read_push_apply(session)
    except DBAPIError:
        raise HTTPException(503, "Synthetic apply status is unavailable") from None


@write.put("/push-inbox-sandbox/apply")
async def put_push_apply(
    payload: PushApplyReview, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PushApplyStatus:
    try:
        return await review_push_apply(session, current_user.id)
    except DBAPIError:
        raise HTTPException(503, "The synthetic apply review could not be confirmed") from None


@write.post("/push-inbox-sandbox/apply/runs")
async def run_push_apply(
    payload: PushApplyRequest, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PushApplyResult:
    try:
        return await apply_push_range(session, payload, current_user.id)
    except (DBAPIError, OSError):
        raise HTTPException(503, "The synthetic apply outcome could not be confirmed; retry the same request ID") from None


@write.get("/index-preview-sandbox")
async def get_local_index_preview() -> IndexSandboxCapability:
    return IndexSandboxCapability(
        available=index_sandbox_available(), rangePagination=True if index_range_pagination_available() else None,
    )


@write.post("/index-preview-sandbox")
async def post_local_index_preview(
    payload: IndexSandboxQuery, request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> IndexSandboxPreview:
    response.headers["Cache-Control"] = "no-store"
    # FastAPI has consumed/validated the request body. Watch only disconnects
    # for this route's own read-only transaction, never a caller-supplied job.
    async def wait_for_disconnect() -> None:
        while (await request.receive())["type"] != "http.disconnect":
            pass

    return await run_index_preview_until_disconnect(session, payload, current_user.id, wait_for_disconnect())


@write.get("/cdc-sandbox")
async def get_local_cdc_sandbox(session: SessionDep, current_user: CurrentActiveSuperuser) -> CdcSandboxStatus:
    return await read_cdc_sandbox(session, current_user.id)


@write.put("/cdc-sandbox")
async def put_local_cdc_sandbox(
    payload: CdcSandboxReview, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> CdcSandboxStatus:
    return await review_cdc_sandbox(session, current_user.id)


@write.get("/cdc-sandbox/runs")
async def get_local_cdc_receipts(session: SessionDep, current_user: CurrentActiveSuperuser) -> list[CdcSandboxReceipt]:
    return await list_cdc_sandbox_receipts(session, current_user.id)


@write.post("/cdc-sandbox/runs")
async def post_local_cdc_apply(
    payload: CdcSandboxApply, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> CdcSandboxReceipt:
    return await apply_cdc_sandbox(session, payload, current_user.id)


@write.get("/cdc-sandbox/versions")
async def get_local_cdc_versions(session: SessionDep, current_user: CurrentActiveSuperuser) -> CdcSandboxVersionsPage:
    return await list_cdc_versions(session, marking_limit(current_user.id))


@write.get("/cdc-sandbox/versions/compare")
async def get_local_cdc_comparison(
    fromVersionId: UUID, toVersionId: UUID, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> CdcSandboxComparison:
    return await compare_cdc_versions(session, fromVersionId, toVersionId, marking_limit(current_user.id))


@write.get("/cdc-sandbox/versions/{version_id}")
async def get_local_cdc_version(version_id: UUID, session: SessionDep, current_user: CurrentActiveSuperuser) -> CdcSandboxVersionDetail:
    return await read_cdc_version(session, version_id, marking_limit(current_user.id))


@write.get("/pipeline-sandbox")
async def get_local_pipeline_sandbox(session: SessionDep, current_user: CurrentActiveSuperuser) -> PipelineSandboxStatus:
    return await read_pipeline_sandbox(session, current_user.id)


@write.post("/pipeline-sandbox/preview")
async def post_local_pipeline_preview(
    payload: PipelineSandboxConfig, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> PipelineSandboxPreview:
    return await preview_pipeline_sandbox(session, payload, current_user.id)


@write.get("/pipeline-sandbox/revisions")
async def get_local_pipeline_revisions(session: SessionDep, current_user: CurrentActiveSuperuser) -> list[PipelineSandboxRevision]:
    return await list_pipeline_revisions(session, current_user.id)


@write.post("/pipeline-sandbox/revisions", status_code=201)
async def post_local_pipeline_revision(
    payload: PipelineSandboxSave, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> PipelineSandboxRevision:
    return await save_pipeline_revision(session, payload, current_user.id)


@write.get("/pipeline-sandbox/revisions/{generation}")
async def get_local_pipeline_revision(
    session: SessionDep, current_user: CurrentActiveSuperuser, generation: int = Path(ge=1, le=2147483647),
) -> PipelineSandboxRevision:
    return await read_pipeline_revision(session, generation, current_user.id)


@write.get("/pipeline-sandbox/handoff")
async def get_pipeline_handoff(
    request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PipelineHandoffCapability:
    if request.query_params:
        raise HTTPException(422, "This handoff review uses one fixed synthetic sink")
    response.headers["Cache-Control"] = "no-store"
    try:
        with fail_after(3):
            return await handoff_capability(session, current_user.id)
    except (DBAPIError, TimeoutError):
        raise HTTPException(503, "Synthetic handoff eligibility is temporarily unavailable") from None


@write.post("/pipeline-sandbox/handoff/eligibility")
async def post_pipeline_handoff_eligibility(
    payload: PipelineHandoffRequest, request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PipelineHandoffEligibility:
    if request.query_params:
        raise HTTPException(422, "This handoff review uses one fixed synthetic sink")
    response.headers["Cache-Control"] = "no-store"
    try:
        with fail_after(3):
            return await handoff_eligibility(session, current_user.id, payload)
    except (DBAPIError, TimeoutError):
        raise HTTPException(503, "Synthetic handoff eligibility is temporarily unavailable") from None


@write.post("/pipeline-sandbox/handoff/copies", status_code=201)
async def post_pipeline_handoff_copy(
    payload: PipelineHandoffCopyRequest, request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PipelineHandoffCopyReceipt:
    if request.query_params:
        raise HTTPException(422, "This handoff uses one fixed private synthetic sink")
    response.headers["Cache-Control"] = "no-store"
    try:
        with fail_after(4):
            return await copy_handoff(session, current_user.id, payload)
    except (DBAPIError, TimeoutError):
        raise HTTPException(503, "Synthetic handoff could not settle; retry the same request ID") from None


@write.get("/pipeline-sandbox/outputs")
async def get_local_pipeline_outputs(session: SessionDep, current_user: CurrentActiveSuperuser) -> PipelineSandboxOutputsPage:
    return await list_pipeline_outputs(session, current_user.id)


@write.post("/pipeline-sandbox/outputs", status_code=201)
async def post_local_pipeline_output(
    payload: PipelineSandboxCommit, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> PipelineSandboxOutput:
    return await commit_pipeline_output(session, payload, current_user.id)


@write.post("/pipeline-sandbox/outputs/compare")
async def compare_local_pipeline_outputs(
    payload: PipelineSandboxOutputCompareRequest, current_user: CurrentActiveSuperuser, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PipelineSandboxOutputComparison:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await compare_pipeline_outputs(session, payload, current_user.id)
    except DBAPIError:
        raise HTTPException(503, "Sealed Builder output comparison is temporarily unavailable") from None


@write.post("/pipeline-sandbox/outputs/record-history", response_model=PipelineSandboxRecordHistory)
async def trace_local_pipeline_output_record(
    payload: PipelineSandboxRecordHistoryRequest, current_user: CurrentActiveSuperuser, request: Request,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> Response:
    headers = {"Cache-Control": "private, no-store"}
    if request.query_params:
        raise HTTPException(422, "Record history accepts only the selected outputs and ID in the body", headers=headers)
    try:
        with fail_after(5):
            history = await trace_pipeline_output_record(session, payload, current_user.id)
            return Response(encode_record_history(history), media_type="application/json", headers=headers)
    except HTTPException as exc:
        raise HTTPException(exc.status_code, exc.detail, headers=headers) from None
    except (DBAPIError, TimeoutError):
        raise HTTPException(503, "Sealed Builder record history is temporarily unavailable", headers=headers) from None


@write.get("/pipeline-sandbox/outputs/{output_id}")
async def get_local_pipeline_output(
    output_id: UUID, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> PipelineSandboxOutputDetail:
    return await read_pipeline_output(session, output_id, current_user.id)


@write.get("/asset-freshness/{schema}/{table}/daily-job-quality")
async def get_daily_job_quality(
    schema: WarehouseSchema, table: TableName, session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> AssetJobQualityStatus:
    try:
        return await read_job_quality(session, schema, table)
    except DBAPIError:
        raise HTTPException(503, "Recorded Daily UTC status is temporarily unavailable") from None


@write.put("/asset-freshness/{schema}/{table}/daily-job-quality")
async def put_daily_job_quality(
    schema: WarehouseSchema, table: TableName, payload: AssetJobQualityReview,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> AssetJobQualityStatus:
    try:
        return await review_job_quality(session, schema, table, payload)
    except DBAPIError:
        raise HTTPException(503, "The Daily UTC review could not be confirmed") from None


@write.post("/asset-freshness/{schema}/{table}/daily-job-quality/incidents/{incident_id}/acknowledge")
async def acknowledge_daily_quality_incident(
    schema: WarehouseSchema, table: TableName, incident_id: UUID, payload: AssetQualityIncidentAck,
    current_user: CurrentActiveSuperuser, session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> AssetQualityIncident:
    try:
        return await acknowledge_daily_job(session, schema, table, incident_id, payload.expectedGeneration, current_user.id)
    except DBAPIError:
        raise HTTPException(503, "Daily UTC acknowledgement could not be confirmed") from None


@write.get("/asset-freshness/{schema}/{table}/weekly-job-quality")
async def get_weekly_job_quality(
    schema: WarehouseSchema, table: TableName, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> WeeklyJobQualityStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await read_weekly_job_quality(session, schema, table)
    except DBAPIError:
        raise HTTPException(503, "Recorded Weekly UTC status is temporarily unavailable") from None


@write.get("/asset-freshness/{schema}/{table}/weekly-job-quality/evidence")
async def get_weekly_job_evidence(
    schema: WarehouseSchema, table: TableName, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
    expectedGeneration: str = Query(..., pattern=r"^[1-9][0-9]{0,9}$"),
    expectedSlot: str = Query(..., max_length=35, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$"),
) -> WeeklyJobEvidence:
    response.headers["Cache-Control"] = "private, no-store"
    if set(request.query_params) != {"expectedGeneration", "expectedSlot"} or len(request.query_params.multi_items()) != 2:
        raise HTTPException(422, "Use one exact review generation and UTC slot")
    generation = int(expectedGeneration)
    try:
        slot = datetime.fromisoformat(expectedSlot)
        if generation > 2_147_483_647:
            raise ValueError("generation outside the supported range")
    except ValueError:
        raise HTTPException(422, "Invalid review generation or UTC slot") from None
    try:
        with fail_after(3):
            return await read_weekly_job_evidence(session, schema, table, generation, slot)
    except (DBAPIError, TimeoutError):
        raise weekly_evidence_unavailable() from None


@write.put("/asset-freshness/{schema}/{table}/weekly-job-quality")
async def put_weekly_job_quality(
    schema: WarehouseSchema, table: TableName, payload: AssetJobQualityReview, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> WeeklyJobQualityStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await review_weekly_job_quality(session, schema, table, payload)
    except DBAPIError:
        raise HTTPException(503, "The Weekly UTC review could not be confirmed") from None


@write.post("/asset-freshness/{schema}/{table}/weekly-job-quality/incidents/{incident_id}/acknowledge")
async def acknowledge_weekly_quality_incident(
    schema: WarehouseSchema, table: TableName, incident_id: UUID, payload: AssetQualityIncidentAck,
    current_user: CurrentActiveSuperuser, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> AssetQualityIncident:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await acknowledge_weekly_job(session, schema, table, incident_id, payload.expectedGeneration, current_user.id)
    except DBAPIError:
        raise HTTPException(503, "Weekly UTC acknowledgement could not be confirmed") from None


@write.get("/asset-freshness/{schema}/{table}/field-freshness")
async def get_field_freshness(
    schema: WarehouseSchema, table: TableName, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> AssetFieldFreshnessStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await read_field_freshness(session, schema, table)
    except DBAPIError:
        raise HTTPException(503, "Recorded field-time freshness is temporarily unavailable") from None


@write.put("/asset-freshness/{schema}/{table}/field-freshness")
async def put_field_freshness(
    schema: WarehouseSchema, table: TableName, payload: AssetFieldFreshnessReview,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> AssetFieldFreshnessStatus:
    try:
        return await review_field_freshness(session, schema, table, payload)
    except DBAPIError:
        raise HTTPException(503, "The field-time review could not be confirmed") from None


@write.post("/asset-freshness/{schema}/{table}/field-freshness/incidents/{incident_id}/acknowledge")
async def acknowledge_field_time_incident(
    schema: WarehouseSchema, table: TableName, incident_id: UUID, payload: AssetQualityIncidentAck,
    current_user: CurrentActiveSuperuser, session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> AssetQualityIncident:
    try:
        return await acknowledge_field_freshness(session, schema, table, incident_id, payload.expectedGeneration, current_user.id)
    except DBAPIError:
        raise HTTPException(503, "The field-time acknowledgement could not be confirmed") from None


@write.get("/asset-freshness/{schema}/{table}/min-rows")
async def get_min_rows_status(
    schema: WarehouseSchema, table: TableName, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> AssetMinRowsStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await read_min_rows(session, schema, table)
    except DBAPIError:
        raise HTTPException(503, "Recorded minimum-row evidence is temporarily unavailable") from None


@write.put("/asset-freshness/{schema}/{table}/min-rows")
async def put_min_rows_review(
    schema: WarehouseSchema, table: TableName, payload: AssetMinRowsReview,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> AssetMinRowsStatus:
    try:
        return await review_min_rows(session, schema, table, payload)
    except DBAPIError:
        raise HTTPException(503, "The minimum-row review could not be confirmed") from None


@write.post("/asset-freshness/{schema}/{table}/min-rows/incidents/{incident_id}/acknowledge")
async def acknowledge_minimum_row_incident(
    schema: WarehouseSchema, table: TableName, incident_id: UUID, payload: AssetQualityIncidentAck,
    current_user: CurrentActiveSuperuser, session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> AssetQualityIncident:
    try:
        return await acknowledge_min_rows(session, schema, table, incident_id, payload.expectedGeneration, current_user.id)
    except DBAPIError:
        raise HTTPException(503, "The minimum-row acknowledgement could not be confirmed") from None


@write.get("/asset-freshness/{schema}/{table}/collected-stats")
async def get_collected_stats(
    schema: WarehouseSchema, table: TableName, session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> AssetCollectedStats:
    try:
        return await read_asset_collected_stats(session, schema, table)
    except DBAPIError:
        raise HTTPException(503, "Recorded catalog statistics are temporarily unavailable") from None


@write.get("/asset-freshness/{schema}/{table}/candidates")
async def freshness_candidates(schema: WarehouseSchema, table: TableName, session: SessionDep) -> list[AssetFreshnessCandidate]:
    """List candidate reviewed plans for this sandbox target."""
    return await get_asset_freshness_candidates(session, schema, table)


@write.get("/asset-freshness/{schema}/{table}")
async def read_freshness(schema: WarehouseSchema, table: TableName, session: SessionDep) -> AssetFreshnessResponse:
    """Read local verified-write cadence and bounded evidence history."""
    return await get_asset_freshness(session, schema, table)


@write.put("/asset-freshness/{schema}/{table}")
async def write_freshness(
    schema: WarehouseSchema, table: TableName, payload: AssetFreshnessChange, session: SessionDep,
) -> AssetFreshnessResponse:
    """Version or pause the one exact-target check."""
    return await put_asset_freshness(session, schema, table, payload)


@write.post("/asset-freshness/{schema}/{table}/schema-baseline/preview")
async def preview_freshness_schema_baseline(
    schema: WarehouseSchema, table: TableName, payload: AssetSchemaPreviewRequest, session: SessionDep,
) -> AssetSchemaPreview:
    """Preview bounded eligible table metadata without changing the baseline."""
    return await preview_schema_baseline(session, schema, table, payload)


@write.post("/asset-freshness/{schema}/{table}/schema-baseline")
async def capture_freshness_schema_baseline(
    schema: WarehouseSchema, table: TableName, payload: AssetSchemaCaptureRequest, session: SessionDep,
) -> AssetFreshnessResponse:
    """Capture a metadata snapshot explicitly reviewed by an administrator."""
    return await capture_schema_baseline(session, schema, table, payload)


@write.post("/asset-freshness/{schema}/{table}/incidents/{incident_id}/acknowledge")
async def acknowledge_freshness_incident(
    schema: WarehouseSchema, table: TableName, incident_id: UUID, payload: AssetQualityIncidentAck,
    session: SessionDep, user: CurrentActiveSuperuser,
) -> AssetQualityIncident:
    """Acknowledge an ongoing exact-target quality incident without resolving it."""
    return await acknowledge_quality_incident(session, schema, table, incident_id, payload, user.id)


@router.get("/published/{token}")
async def published_data(token: str, limit: int = Query(50, ge=1, le=500),
                         offset: int = Query(0, ge=0, le=1_000_000)) -> dict[str, Any]:
    return await get_etl_workspace().published_data(token, {"limit": limit, "offset": offset})


@read.get("/layers")
async def layers() -> list[dict[str, Any]]:
    return await get_etl_layers().layers()


@read.get("/object-types")
async def object_types(schema: str | None = None, search: str = Query("", max_length=1000),
                       group: str = "", limit: int = Query(100, ge=1, le=500),
                       offset: int = Query(0, ge=0, le=1_000_000),
                       ids: str | None = Query(None, max_length=26_000),
                       includeHidden: bool = False, current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    if includeHidden and not getattr(current_user, "is_superuser", False):
        raise HTTPException(403, "Only administrators can include hidden object types")
    if includeHidden:
        return await get_etl_layers().object_types(schema, search, group, limit, offset, ids, include_hidden=True)
    return await get_etl_layers().object_types(schema, search, group, limit, offset, ids)


@read.get("/object-types/favorites")
async def object_favorites(current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_workspace().object_favorites(str(current_user.id))


@read.post("/object-types/favorites/import")
async def import_object_favorites(payload: FavoriteImport,
                                  current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_workspace().import_object_favorites(payload.ids, str(current_user.id))


@read.get("/task-config/vulnerability-keywords")
async def vulnerability_keywords() -> dict[str, Any]:
    return await keyword_files.configuration()


@write.post("/task-config/vulnerability-keywords/versions")
async def upload_vulnerability_keywords(file: UploadFile = File(...)) -> dict[str, Any]:
    try:
        data = await file.read(MAX_FILE_SIZE + 1)
    finally:
        await file.close()
    return await keyword_files.upload(data)


@write.put("/task-config/vulnerability-keywords/current")
async def activate_vulnerability_keywords(payload: KeywordVersion) -> dict[str, Any]:
    return await keyword_files.activate(payload.version)


@read.get("/task-config/vulnerability-keywords/versions/{version}/file")
async def download_vulnerability_keywords(version: str) -> Response:
    data = await keyword_files.download(version)
    return Response(data, media_type="application/yaml", headers={
        "Content-Disposition": f'attachment; filename="satellite-vulnerability-keywords-{version}.yaml"',
    })


@read.get("/task-config/vulnerability-keywords/current/file")
@read.get("/task-config/vulnerability-keywords/example", include_in_schema=False)
async def download_current_vulnerability_keywords() -> Response:
    return Response(await keyword_files.download(), media_type="application/yaml", headers={
        "Content-Disposition": 'attachment; filename="satellite-vulnerability-keywords.yaml"',
    })


@read.get("/connections")
async def connections() -> list[dict[str, Any]]:
    return await get_etl_workspace().connections()


@write.get("/transfer-plans")
async def transfer_plans(session: SessionDep, schema: WarehouseSchema | None = None) -> list[TransferPlan]:
    return await list_transfer_plans(session, schema)


@write.post("/transfer-plans", status_code=201)
async def create_transfer_plan(payload: TransferPlanPayload, session: SessionDep) -> TransferPlan:
    return await save_transfer_plan(session, payload)


@write.post("/transfer-plans/preflight", response_model_exclude_none=True)
async def transfer_plan_preflight(payload: TransferPlanPayload) -> TransferPreflight:
    return await preflight_transfer_plan(payload)


@write.get("/transfer-plans/run-capabilities")
async def get_transfer_run_capabilities() -> TransferRunCapabilities:
    return transfer_run_capabilities()


@write.get("/transfer-plans/{plan_id}/runs")
async def get_transfer_plan_runs(plan_id: UUID, session: SessionDep) -> list[TransferRun]:
    return await list_transfer_runs(session, plan_id=plan_id)


@write.get("/transfer-plans/{plan_id}/attempts")
async def get_transfer_plan_attempts(plan_id: UUID, session: SessionDep) -> list[TransferAttempt]:
    return await list_transfer_attempts(session, plan_id)


@write.get("/transfer-upstream-triggers")
async def get_upstream_triggers(session: SessionDep) -> list[UpstreamTriggerSummary]:
    return await list_upstream_triggers(session)


@write.get("/transfer-plans/{plan_id}/upstream-trigger")
async def get_upstream_trigger(plan_id: UUID, session: SessionDep) -> UpstreamTrigger:
    return await read_upstream_trigger(session, plan_id)


@write.put("/transfer-plans/{plan_id}/upstream-trigger")
async def put_upstream_trigger(
    plan_id: UUID, payload: UpstreamTriggerChange, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> UpstreamTrigger:
    return await change_upstream_trigger(session, plan_id, payload, current_user.id)


@write.get("/transfer-plans/{plan_id}/upstream-trigger/candidates")
async def get_upstream_candidates(plan_id: UUID, session: SessionDep) -> list[UpstreamCandidate]:
    return await list_upstream_candidates(session, plan_id)


@write.get("/transfer-plans/{plan_id}/upstream-trigger/jobs")
async def get_upstream_jobs(plan_id: UUID, session: SessionDep) -> list[UpstreamTriggerJob]:
    return await list_upstream_jobs(session, plan_id)


@write.get("/transfer-plans/{plan_id}/upstream-trigger/slots")
async def get_upstream_mixed_slots(plan_id: UUID, session: SessionDep) -> list[UpstreamMixedSlot]:
    return await list_upstream_mixed_slots(session, plan_id)


@write.get("/transfer-schedules")
async def get_transfer_schedules(session: SessionDep) -> list[TransferScheduleSummary]:
    return await list_transfer_schedules(session)


@write.get("/transfer-plans/{plan_id}/schedule")
async def get_transfer_schedule(plan_id: UUID, session: SessionDep) -> TransferSchedule:
    return await read_transfer_schedule(session, plan_id)


@write.put("/transfer-plans/{plan_id}/schedule")
async def put_transfer_schedule(
    plan_id: UUID, payload: TransferScheduleChange, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> TransferSchedule:
    return await change_transfer_schedule(session, plan_id, payload, current_user.id)


@write.get("/transfer-plans/{plan_id}/schedule/jobs")
async def get_transfer_schedule_jobs(plan_id: UUID, session: SessionDep) -> list[TransferScheduleJob]:
    return await list_transfer_schedule_jobs(session, plan_id)


@write.put("/transfer-plans/{plan_id}/schedule/jobs/{job_id}/cancel")
async def put_transfer_schedule_job_cancel(
    plan_id: UUID, job_id: UUID, payload: TransferScheduleJobCancel, session: SessionDep,
    current_user: CurrentActiveSuperuser,
) -> TransferScheduleJob:
    return await cancel_transfer_schedule_job(session, plan_id, job_id, payload, current_user.id)


@write.post("/transfer-plans/{plan_id}/runs")
async def run_transfer_plan(
    plan_id: UUID, payload: TransferRunRequest, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> TransferRun:
    return await run_sandbox_transfer(session, plan_id, payload, current_user.id)


@write.get("/{layer}/{table}/transfer-runs")
async def get_table_transfer_runs(layer: str, table: TableName, session: SessionDep) -> list[TransferRun]:
    if layer != SANDBOX:
        return []
    return await list_transfer_runs(session, table=table)


@write.get("/{layer}/{table}/transfer-timeline")
async def get_transfer_timeline(
    layer: str, table: TableName, request: Request,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
    limit: int = Query(25, ge=1, le=50), cursor: str | None = Query(None, min_length=1, max_length=512),
    view: TimelineView | None = None, start: str | None = Query(None, alias="from", max_length=32),
    before: str | None = Query(None, max_length=32),
) -> TransferTimelinePage:
    """Read bounded runtime evidence persisted for an exact destination table."""
    params = request.query_params
    if set(params) - {"limit", "cursor", "view", "from", "before"} or len(params) != len(params.multi_items()):
        raise HTTPException(422, "Invalid or duplicate transfer timeline filter")
    filters = parse_timeline_filters(view, start, before)
    try:
        with fail_after(4):
            return await list_transfer_timeline(session, layer, table, limit, cursor, filters)
    except (TimeoutError, DBAPIError):
        raise HTTPException(503, "Transfer timeline evidence is temporarily unavailable") from None


@write.get("/{layer}/{table}/transfer-timeline/{kind}/{entry_id}")
async def get_transfer_timeline_entry(
    layer: str, table: TableName, kind: TimelineKind, entry_id: UUID, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
    require_visible: Literal["true"] | None = Query(None, alias="requireVisible"),
) -> TransferTimelineDetail:
    """Read scheduler provenance for an exact persisted attempt or receipt."""
    if require_visible is None:
        return await read_transfer_timeline_entry(session, layer, table, kind, entry_id)
    if kind != "receipt" or list(request.query_params.multi_items()) != [("requireVisible", "true")]:
        raise HTTPException(422, "The exact visibility option is supported only for receipt links")
    response.headers["Cache-Control"] = "no-store"
    try:
        with fail_after(3):
            return await read_transfer_timeline_entry(session, layer, table, kind, entry_id, require_visible=True)
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) in {"42P01", "3F000", "42809"}:
            raise HTTPException(404, "Transfer timeline entry is not present for this destination") from None
        raise HTTPException(503, "Visible transfer receipt is temporarily unavailable") from None
    except TimeoutError:
        raise HTTPException(503, "Visible transfer receipt is temporarily unavailable") from None


@write.put("/transfer-plans/{plan_id}")
async def update_transfer_plan(plan_id: UUID, payload: TransferPlanPayload, session: SessionDep) -> TransferPlan:
    return await save_transfer_plan(session, payload, plan_id)


@write.get("/connector-types")
async def available_connector_types() -> list[dict[str, Any]]:
    return connector_types()


@write.get("/{layer}/{table}/batch-lineage-timeline")
async def batch_lineage_timeline(
    layer: str, table: str, session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
    from_at: AwareDatetime | None = Query(default=None, alias="from"),
    to: AwareDatetime | None = None, limit: int = Query(default=25, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
) -> BatchLineagePage:
    try:
        return await list_batch_lineage(session, layer, table, from_at, to, limit, cursor)
    except DBAPIError:
        raise HTTPException(503, "Managed incremental timeline is temporarily unavailable") from None


@write.get("/{layer}/{table}/batch-lineage-timeline/{kind}/{entry_id}")
async def batch_lineage_entry(
    layer: str, table: str, kind: BatchLineageKind, entry_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> BatchLineageDetail:
    try:
        return await read_batch_lineage(session, layer, table, kind, entry_id)
    except DBAPIError:
        raise HTTPException(503, "Managed incremental timeline is temporarily unavailable") from None


@write.get("/batch-syncs/capabilities")
async def get_batch_sync_capabilities() -> BatchSyncCapabilities:
    return batch_sync_capabilities()


def export_destination(request: Request, destination: ExportDestination = "primary") -> ExportDestination:
    if len(request.query_params.getlist("destination")) > 1:
        raise HTTPException(422, "Specify exactly one approved Table Export destination")
    return destination


ExportDestinationDep = Annotated[ExportDestination, Depends(export_destination)]


@write.get("/table-export/capabilities")
async def get_table_export_capabilities(destination: ExportDestinationDep) -> TableExportCapabilities:
    return table_export_capabilities(destination)


@write.get("/table-export")
async def get_table_export_review(session: SessionDep, destination: ExportDestinationDep) -> TableExportPlan | None:
    return await get_table_export(session, destination)


@write.put("/table-export")
async def put_table_export_review(
    payload: TableExportReview, session: SessionDep, current_user: CurrentActiveSuperuser, destination: ExportDestinationDep,
) -> TableExportPlan:
    return await review_table_export(session, payload, current_user.id, destination)


@write.get("/table-export/grant")
async def get_export_grant(session: SessionDep, destination: ExportDestinationDep) -> TableExportGrant:
    return await get_table_export_grant(session, destination)


@write.put("/table-export/grant")
async def put_export_grant(
    payload: TableExportGrantChange, session: SessionDep, current_user: CurrentActiveSuperuser, destination: ExportDestinationDep,
) -> TableExportGrant:
    return await change_table_export_grant(session, payload, current_user.id, destination)


@write.get("/table-export/grant/history")
async def get_export_grant_history(session: SessionDep, destination: ExportDestinationDep) -> list[TableExportGrantEvent]:
    return await list_table_export_grant_history(session, destination)


@write.get("/table-export/runs")
async def get_table_export_runs(session: SessionDep, destination: ExportDestinationDep) -> list[TableExportReceipt]:
    return await list_table_export_receipts(session, destination)


@write.post("/table-export/runs")
async def post_table_export_run(
    payload: TableExportRunRequest, session: SessionDep, current_user: CurrentActiveSuperuser, destination: ExportDestinationDep,
) -> TableExportReceipt:
    return await run_table_export(session, payload, current_user.id, destination)


@write.get("/table-export/attempts")
async def get_table_export_attempts(session: SessionDep, destination: ExportDestinationDep) -> list[TableExportAttempt]:
    return await list_table_export_attempts(session, destination)


@write.get("/table-export/lineage")
async def get_table_export_lineage_evidence(session: SessionDep, destination: ExportDestinationDep) -> TableExportLineage:
    return await get_table_export_lineage(session, destination)


@write.get("/batch-syncs")
async def get_batch_syncs(session: SessionDep, connectionId: UUID | None = None, planId: UUID | None = None) -> list[BatchSyncPlan]:
    return await list_batch_syncs(session, connectionId, planId)


@write.post("/batch-syncs", status_code=201)
async def create_batch_sync(payload: BatchSyncPayload, session: SessionDep) -> BatchSyncPlan:
    return await save_batch_sync(session, payload)


@write.put("/batch-syncs/{plan_id}")
async def update_batch_sync(plan_id: UUID, payload: BatchSyncPayload, session: SessionDep) -> BatchSyncPlan:
    return await save_batch_sync(session, payload, plan_id)


@write.get("/batch-syncs/{plan_id}/runs")
async def get_batch_sync_runs(plan_id: UUID, session: SessionDep) -> list[BatchSyncReceipt]:
    return await list_batch_sync_runs(session, plan_id)


@write.get("/batch-syncs/{plan_id}/attempts")
async def get_batch_sync_attempts(plan_id: UUID, session: SessionDep) -> list[BatchSyncAttempt]:
    return await list_batch_sync_attempts(session, plan_id)


@write.get("/batch-sync-schedules")
async def get_batch_sync_schedules(session: SessionDep) -> list[BatchSyncScheduleSummary]:
    return await list_batch_sync_schedules(session)


@write.get("/batch-syncs/{plan_id}/schedule")
async def get_batch_sync_schedule(plan_id: UUID, session: SessionDep) -> BatchSyncSchedule:
    return await read_batch_sync_schedule(session, plan_id)


@write.put("/batch-syncs/{plan_id}/schedule")
async def put_batch_sync_schedule(
    plan_id: UUID, payload: BatchSyncScheduleChange, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> BatchSyncSchedule:
    return await change_batch_sync_schedule(session, plan_id, payload, current_user.id)


@write.get("/batch-syncs/{plan_id}/schedule/jobs")
async def get_batch_sync_schedule_jobs(plan_id: UUID, session: SessionDep) -> list[BatchSyncScheduleJob]:
    return await list_batch_sync_schedule_jobs(session, plan_id)


@write.post("/batch-syncs/{plan_id}/runs")
async def run_saved_batch_sync(
    plan_id: UUID, payload: BatchSyncRunRequest, session: SessionDep, current_user: CurrentActiveSuperuser,
) -> BatchSyncReceipt:
    return await run_batch_sync(session, plan_id, payload, current_user.id)


@write.get("/data-connections")
async def data_connections() -> list[dict[str, Any]]:
    return await get_data_connections().list()


@write.post("/data-connections/test")
async def test_data_connection(payload: DataConnectionPayload, connectionId: UUID | None = None) -> dict[str, Any]:
    return await get_data_connections().test(payload, str(connectionId) if connectionId else None)


@write.post("/data-connections")
async def create_data_connection(payload: DataConnectionPayload,
                                 current_user: Any = Depends(get_current_active_superuser)) -> dict[str, Any]:
    return await get_data_connections().save(payload, str(current_user.id))


@write.get("/data-connections/{connection_id}/batch-source-identity")
async def batch_source_identity(connection_id: UUID, session: SessionDep) -> BatchSourceIdentityStatus:
    return await read_source_identity(session, connection_id)


@write.post("/data-connections/{connection_id}/batch-source-identity")
async def review_batch_source_identity(
    connection_id: UUID, payload: BatchSourceIdentityReview, session: SessionDep,
) -> BatchSourceIdentityStatus:
    return await verify_source_identity(session, connection_id, payload.expectedSourceUpdatedAt)


@write.put("/data-connections/{connection_id}/pause")
async def change_data_connection_pause(connection_id: UUID, payload: DataConnectionPauseChange) -> dict[str, Any]:
    return await get_data_connections().change_pause(str(connection_id), payload)


@write.post("/data-connections/{connection_id}/test")
async def test_saved_data_connection(connection_id: UUID) -> dict[str, Any]:
    return await get_data_connections().test_saved(str(connection_id))


@write.get("/data-connections/{connection_id}/catalog")
async def browse_data_connection_catalog(
    connection_id: UUID, schema: str | None = Query(None, min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$"),
    search: str = Query("", max_length=128, pattern=r"^[^\x00-\x1f\x7f]*$"),
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=5000),
) -> SourceCatalog:
    return await get_data_connections().catalog(str(connection_id), schema=schema, search=search, limit=limit, offset=offset)


@write.get("/data-connections/{connection_id}/catalog/columns", response_model_exclude_none=True)
async def browse_data_connection_columns(
    connection_id: UUID, schema: str = Query(..., min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$"),
    table: str = Query(..., min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$"),
) -> SourceColumns:
    return await get_data_connections().catalog_columns(str(connection_id), schema=schema, table=table)


@write.get("/data-connections/{connection_id}/catalog/references")
async def browse_data_connection_references(
    connection_id: UUID, request: Request, response: Response,
    schema: str = Query(..., min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$"),
    table: str = Query(..., min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$"),
    direction: SourceReferenceDirection = Query(...), expectedUpdatedAt: AwareDatetime = Query(...),
) -> SourceReferences:
    if set(request.query_params) != {"schema", "table", "direction", "expectedUpdatedAt"} or len(request.query_params.multi_items()) != 4:
        raise HTTPException(422, "Use one exact schema, table, direction and saved source revision")
    response.headers["Cache-Control"] = "private, no-store"
    return await get_data_connections().catalog_references(
        str(connection_id), schema=schema, table=table, direction=direction, expected_updated_at=expectedUpdatedAt,
    )


@write.put("/data-connections/{connection_id}")
async def update_data_connection(connection_id: UUID, payload: DataConnectionPayload,
                                 current_user: Any = Depends(get_current_active_superuser)) -> dict[str, Any]:
    return await get_data_connections().save(payload, str(current_user.id), str(connection_id))


@write.delete("/data-connections/{connection_id}")
async def delete_data_connection(connection_id: UUID) -> dict[str, Any]:
    return await get_data_connections().delete(str(connection_id))


@write.post("/connections")
async def create_connection(payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().save_connection(payload)


@write.put("/connections/{connection_id}")
async def update_connection(connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().save_connection(payload, connection_id)


@write.delete("/connections/{connection_id}")
async def delete_connection(connection_id: str) -> dict[str, Any]:
    return await get_etl_workspace().delete_connection(connection_id)


@write.put("/schemas/{schema}")
async def update_schema(schema: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().rename_schema(schema, payload)


@write.delete("/schemas/{schema}")
async def delete_schema(schema: str) -> dict[str, Any]:
    return await get_etl_workspace().delete_schema(schema)


@read.get("/{layer}/tables")
async def tables(layer: str, tableRole: str = "current") -> list[dict[str, Any]]:
    return await get_etl_layers().tables(layer, tableRole)


@write.post("/{layer}/tables")
async def create_table(layer: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().create_table(layer, payload)


@read.get("/{layer}/{table}/data")
async def table_data(layer: str, table: str, limit: int = Query(50, ge=1, le=500),
                     offset: int = Query(0, ge=0, le=1_000_000),
                     partition: str | None = None) -> dict[str, Any]:
    return await get_etl_layers().data(layer, table, {"limit": limit, "offset": offset, "partition": partition})


@read.get("/{layer}/{table}/preview")
async def object_preview(layer: str, table: str, includeHidden: bool = False,
                         current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    if includeHidden and not getattr(current_user, "is_superuser", False):
        raise HTTPException(403, "Only administrators can include hidden object types")
    if includeHidden:
        return await get_etl_layers().preview(layer, table, include_hidden=True)
    return await get_etl_layers().preview(layer, table)


@write.get("/{schema}/{table}/indexed-id-sample")
async def get_indexed_id_sample(
    schema: WarehouseSchema, table: TableName, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> IndexedSampleCapability:
    if request.query_params:
        raise HTTPException(422, "Indexed ID sample capability accepts no query parameters")
    response.headers["Cache-Control"] = "no-store"
    return await indexed_sample_capability(session, schema, table)


@write.post("/{schema}/{table}/indexed-id-sample")
async def post_indexed_id_sample(
    schema: WarehouseSchema, table: TableName, payload: IndexedSampleRequest, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> IndexedSampleResult:
    if request.query_params:
        raise HTTPException(422, "Indexed ID sample accepts only exact ID or range bounds in the body")
    response.headers["Cache-Control"] = "no-store"

    async def wait_for_disconnect() -> None:
        while (await request.receive())["type"] != "http.disconnect":
            pass

    return await run_bounded_index_query(
        sample_indexed_ids(session, schema, table, payload), wait_for_disconnect(),
        IndexedSampleResult(state="blocked", reason="query_unavailable"),
        cancelled_message="Indexed ID sample was cancelled",
    )


@read.post("/{schema}/{table}/preview-index-coverage")
async def get_preview_index_coverage(
    schema: WarehouseSchema, table: TableName, payload: PreviewIndexCoverageInput, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PreviewIndexCoverage:
    if request.query_params:
        raise HTTPException(422, "Index coverage accepts only a filter shape in the body")
    response.headers["Cache-Control"] = "no-store"
    return await preview_index_coverage(session, schema, table, payload)


@write.get("/{schema}/{table}/lineage-observed")
async def table_observed_transfer_lineage(
    schema: WarehouseSchema, table: TableName, direction: LineageDirection, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> ObservedLineage:
    if set(request.query_params) != {"direction"} or len(request.query_params.getlist("direction")) != 1:
        raise HTTPException(422, "Specify exactly one observed transfer direction")
    response.headers["Cache-Control"] = "no-store"
    return await observed_transfer_lineage(session, schema, table, direction)


@read.get("/{schema}/{table}/lineage-column-references")
async def table_lineage_column_references(
    schema: WarehouseSchema, table: TableName, column: TableName, direction: LineageDirection,
    request: Request, response: Response, session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> LineageColumnReferences:
    if set(request.query_params) != {"column", "direction"} or len(request.query_params.multi_items()) != 2:
        raise HTTPException(422, "Specify exactly one column and reference direction")
    response.headers["Cache-Control"] = "no-store"
    return await read_lineage_column_references(session, schema, table, column, direction)


@read.get("/{layer}/{table}/lineage-neighbors")
async def table_lineage_neighbors(
    layer: str, table: str, direction: LineageDirection, response: Response,
) -> LineageNeighbors:
    response.headers["Cache-Control"] = "no-store"
    return await get_etl_layers().lineage_neighbors(layer, table, direction)


@read.get("/{layer}/{table}/lineage-path-targets")
async def table_lineage_targets(
    layer: str, table: str, request: Request, response: Response,
    search: str = Query("", max_length=100, pattern=r"^[^\x00-\x1f\x7f]*$"),
) -> LineagePathTargets:
    if set(request.query_params) - {"search"} or len(request.query_params.getlist("search")) > 1:
        raise HTTPException(422, "Specify at most one destination search term")
    response.headers["Cache-Control"] = "no-store"
    return await get_etl_layers().lineage_path_targets(layer, table, search)


@read.get("/{layer}/{table}/lineage-path")
async def table_lineage_path(
    layer: str, table: str, targetSchema: str, targetTable: str, request: Request, response: Response,
) -> LineagePath:
    if set(request.query_params) != {"targetSchema", "targetTable"} or len(request.query_params.multi_items()) != 2:
        raise HTTPException(422, "Specify exactly one destination schema and table")
    response.headers["Cache-Control"] = "no-store"
    return await get_etl_layers().lineage_path(layer, table, targetSchema, targetTable)


@read.get("/{layer}/{table}/lineage-common-upstream")
async def table_lineage_common_upstream(
    layer: str, table: str, targetSchema: str, targetTable: str, request: Request, response: Response,
) -> LineageCommonUpstream:
    if set(request.query_params) != {"targetSchema", "targetTable"} or len(request.query_params.multi_items()) != 2:
        raise HTTPException(422, "Specify exactly one destination schema and table")
    response.headers["Cache-Control"] = "no-store"
    return await get_etl_layers().lineage_common_upstream(layer, table, targetSchema, targetTable)


@read.get("/{layer}/{table}/lineage-common-downstream")
async def table_lineage_common_downstream(
    layer: str, table: str, targetSchema: str, targetTable: str, request: Request, response: Response,
) -> LineageCommonDownstream:
    if set(request.query_params) != {"targetSchema", "targetTable"} or len(request.query_params.multi_items()) != 2:
        raise HTTPException(422, "Specify exactly one destination schema and table")
    response.headers["Cache-Control"] = "no-store"
    return await get_etl_layers().lineage_common_downstream(layer, table, targetSchema, targetTable)


@read.get("/{layer}/{table}/columns")
async def table_columns(layer: str, table: str) -> list[dict[str, Any]]:
    return await get_etl_layers().columns(layer, table)


@read.get("/{layer}/{table}/evidence")
async def table_evidence(layer: str, table: str,
                         current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_layers().evidence(
        layer, table, include_hidden=bool(getattr(current_user, "is_superuser", False)),
    )


@write.put("/{layer}/{table}/visibility")
async def set_object_visibility(layer: str, table: str, payload: ObjectVisibility,
                                current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_workspace().set_object_visibility(layer, table, payload.visibility, str(current_user.id))


@read.put("/{layer}/{table}/favorite")
async def favorite_object(layer: str, table: str, payload: FavoriteMutation | None = None,
                           current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_workspace().set_object_favorite(layer, table, True, str(current_user.id))


@read.delete("/{layer}/{table}/favorite")
async def unfavorite_object(layer: str, table: str, payload: FavoriteMutation | None = None,
                             current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_workspace().set_object_favorite(layer, table, False, str(current_user.id))


@read.post("/{layer}/{table}/data")
async def filtered_data(layer: str, table: str, payload: DataQuery) -> dict[str, Any]:
    return await get_etl_layers().data(layer, table, payload.model_dump())


@read.post("/{layer}/{table}/preview-rows")
async def table_preview_rows(layer: str, table: str, payload: PreviewRowsQuery) -> dict[str, Any]:
    return await get_etl_layers().data(layer, table, payload.model_dump(), preview_rows=True)


@write.patch("/{layer}/{table}/rows")
async def update_row(layer: str, table: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().patch_row(layer, table, payload)


@write.delete("/{layer}/{table}/rows")
async def delete_row(layer: str, table: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().delete_row(layer, table, payload)


@read.get("/{layer}/{table}/partitions")
async def partitions(layer: str, table: str) -> list[dict[str, Any]]:
    return await get_etl_layers().partitions(layer, table)


@read.get("/{layer}/{table}/stream")
async def stream(layer: str, table: str, topic: str | None = None) -> dict[str, Any]:
    return await etl_runtime.stream(layer, table, topic)


@write.put("/{layer}/{table}/offset")
async def set_offset(layer: str, table: str, payload: OffsetUpdate) -> dict[str, Any]:
    return await etl_runtime.set_offset(layer, table, payload.model_dump())


@read.get("/{layer}/{table}/script")
async def script(layer: str, table: str) -> dict[str, Any]:
    return await etl_runtime.script(layer, table)


@write.post("/query")
async def query(payload: SqlQuery) -> dict[str, Any]:
    return await get_etl_layers().query(payload.sql)


@read.get("/handlers/{layer}/{table}")
async def handler(layer: str, table: str) -> dict[str, Any]:
    return await etl_runtime.read_handler(layer, table)


@write.put("/handlers/{layer}/{table}")
async def save_handler(layer: str, table: str, payload: CodePayload) -> dict[str, Any]:
    return await etl_runtime.save_handler(layer, table, payload.code)


@read.post("/handlers/{layer}/{table}/validate")
async def validate_handler(layer: str, table: str, payload: CodePayload) -> dict[str, Any]:
    await get_etl_layers().columns(layer, table)
    return etl_runtime.validate(payload.code)


@read.post("/{layer}/{table}/dry-run")
async def dry_run(layer: str, table: str, payload: CodePayload) -> dict[str, Any]:
    return await etl_runtime.dry_run(layer, table, payload.model_dump())


@write.get("/{layer}/{table}/apis")
async def publications(layer: str, table: str) -> list[dict[str, Any]]:
    return await get_etl_workspace().publications(layer, table)


@write.post("/{layer}/{table}/apis")
async def publish(layer: str, table: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().save_publication(layer, table, payload)


@write.put("/{layer}/{table}/apis/{publication_id}")
async def update_publication(layer: str, table: str, publication_id: str,
                             payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().save_publication(layer, table, payload, publication_id)


@write.delete("/{layer}/{table}/apis/{publication_id}")
async def revoke_publication(layer: str, table: str, publication_id: str) -> dict[str, Any]:
    return await get_etl_workspace().delete_publication(layer, table, publication_id)


router.include_router(read)
router.include_router(write)
