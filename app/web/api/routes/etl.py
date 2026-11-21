from __future__ import annotations

import asyncio
from datetime import datetime
import json
from typing import Annotated, Any, Literal
from uuid import UUID

from anyio import CancelScope, fail_after
from fastapi import APIRouter, Depends, File, Header, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.tasks.extractors.keyword_files import MAX_FILE_SIZE
from app.web.api.dependencies.common import CurrentActiveSuperuser, SessionDep, get_current_active_superuser, get_current_user
from app.web.api.dependencies.session import get_db
from app.web.api.models.column_planner_statistic import ColumnPlannerStatistic
from app.web.api.models.lineage_column_mapping_paths import ColumnMappingPaths
from app.web.api.models.lineage_neighbors import (
    LineageColumnReferences, LineageCommonDownstream, LineageCommonUpstream, LineageDirection, LineageNeighbors, LineagePath,
    LineagePathTargets, ObservedLineage,
)
from app.web.api.models.indexed_id_sample import (
    IndexedCompoundSampleRequest, IndexedProjectionCapability, IndexedProjectionRequest, IndexedProjectionResult,
    IndexedSampleCapability, IndexedSampleRequest, IndexedSampleResult,
)
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
    BatchSyncIncrementalPreflight, BatchSyncIncrementalPreflightRequest,
    BatchSyncAttempt, BatchSyncCapabilities, BatchSyncPayload, BatchSyncPlan, BatchSyncReceipt, BatchSyncRunRequest,
)
from app.web.api.models.batch_sync_schedules import (
    BatchSyncSchedule, BatchSyncScheduleChange, BatchSyncScheduleJob, BatchSyncScheduleSummary,
)
from app.web.api.models.cdc_sandbox import CdcSandboxApply, CdcSandboxReceipt, CdcSandboxReview, CdcSandboxStatus
from app.web.api.models.cdc_sandbox_versions import (
    CdcSandboxComparison, CdcSandboxVersionDetail, CdcSandboxVersionsPage,
)
from app.web.api.models.push_inbox import (
    PushInboxEventPath, PushInboxEventPathRequest, PushInboxIntakeChange, PushInboxStatus,
)
from app.web.api.models.push_inbox_apply import PushApplyRequest, PushApplyResult, PushApplyReview, PushApplyStatus
from app.web.api.models.pipeline_handoff import (
    PipelineHandoffCapability, PipelineHandoffCopyReceipt, PipelineHandoffCopyRequest, PipelineHandoffEligibility,
    PipelineHandoffRecipientCapability, PipelineHandoffRecipientProof, PipelineHandoffRecipientRequest,
    PipelineHandoffRequest,
)
from app.web.api.models.pipeline_sandbox import (
    PipelineSandboxConfig, PipelineSandboxPreview, PipelineSandboxRestore, PipelineSandboxRevision, PipelineSandboxSave, PipelineSandboxStatus,
)
from app.web.api.models.pipeline_sandbox_outputs import (
    PipelineSandboxCommit, PipelineSandboxOutput, PipelineSandboxOutputCompareRequest, PipelineSandboxOutputComparison,
    PipelineSandboxOutputDetail, PipelineSandboxOutputsPage,
    PipelineSandboxRecordHistory, PipelineSandboxRecordHistoryRequest,
)
from app.web.api.models.snapshot_intent_web import (
    SnapshotIntentWebCapability, SnapshotIntentWebHead, SnapshotIntentWebHeadRequest, SnapshotIntentWebPrepareRequest,
    SnapshotIntentWebPrepared,
)
from app.web.api.models.snapshot_delivery_web import (
    SnapshotDeliveryWebCapability, SnapshotDeliveryWebLookup, SnapshotDeliveryWebReceipt, SnapshotDeliveryWebReview,
    SnapshotDeliveryWebRun,
)
from app.web.api.models.snapshot_delivery_evidence import (
    SnapshotDeliveryEvidence, SnapshotDeliveryEvidenceCapability, SnapshotDeliveryEvidenceRequest,
)
from app.web.api.models.table_exports import (
    ExportDestination,
    TableExportAttempt, TableExportCapabilities, TableExportGrant, TableExportGrantChange, TableExportGrantEvent,
    TableExportLineage, TableExportPlan, TableExportReceipt, TableExportReview, TableExportRunRequest,
    TableExportSnapshotPreview, TableExportSnapshotPreviewCapability, TableExportSnapshotPreviewRequest,
)
from app.web.api.models.transfer_timeline import TimelineKind, TimelineView, TransferTimelineDetail, TransferTimelinePage
from app.web.api.models.transfer_upstream import (
    UpstreamCandidate, UpstreamMixedSlot, UpstreamTrigger, UpstreamTriggerChange, UpstreamTriggerJob, UpstreamTriggerSummary,
)
from app.web.api.models.etl import RowPreviewPolicy
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
    ExplorerRowKey,
    ExplorerFileQuery,
    ExplorerRowLinksQuery,
    SqlQuery,
)
from app.web.api.models.data_connections import (
    DataConnectionDraftPayload, DataConnectionPauseChange, DataConnectionPayload, SourceCatalog, SourceColumns, SourceReferenceDirection, SourceReferences,
)
from app.web.api.models.transfer_plans import (
    TableName, TransferAttempt, TransferPlan, TransferPlanPayload, TransferPreflight, TransferRun, TransferRunCapabilities,
    TransferRunRequest, WarehouseSchema,
)
from app.web.api.models.monthly_committed_run import MonthlyCommittedRun
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
from app.web.services.batch_lineage import available_batch_lineage_asset, list_batch_lineage, read_batch_lineage
from app.web.services.batch_source_identity import read_source_identity, verify_source_identity
from app.web.services.batch_incremental_preflight import preflight_incremental_batch
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
from app.web.services.pipeline_handoff_sender import SenderProofUnavailable, read_sender_proof
from app.web.services.pipeline_handoff_recipient import (
    read_recipient_proof, recipient_proof_capability, recipient_proof_unavailable,
)
from app.web.services.pipeline_handoff import handoff_capability, handoff_eligibility
from app.web.services.pipeline_sandbox import (
    list_pipeline_revisions, preview_pipeline_sandbox, read_pipeline_revision, read_pipeline_sandbox,
    restore_pipeline_revision, save_pipeline_revision,
)
from app.web.services.pipeline_sandbox_outputs import (
    commit_pipeline_output, compare_pipeline_outputs, encode_record_history, list_pipeline_outputs,
    read_pipeline_output, trace_pipeline_output_record,
)
from app.web.services.push_inbox import change_push_intake, read_push_status
from app.web.services.push_inbox_apply import apply_push_range, read_push_apply, review_push_apply
from app.web.services.push_inbox_event_path import PushInboxEventPathUnavailable, read_push_event_path
from app.web.services.table_export_snapshot import compare_table_export_snapshots, read_snapshot_preview_capability
from app.web.services.table_export_snapshot_intent import (
    SnapshotPublicationIntentPredecessorChanged, SnapshotPublicationIntentUnavailable,
)
from app.web.services.table_export_snapshot_intent_web import (
    snapshot_intent_web_capability, snapshot_intent_web_head, snapshot_intent_web_prepare,
)
from app.web.services.table_export_snapshot_delivery import SnapshotDeliveryUnavailable
from app.web.services.table_export_snapshot_delivery_web import (
    SnapshotWebDeliveryUnavailable, snapshot_delivery_web_capability, snapshot_delivery_web_review,
    snapshot_delivery_web_run,
)
from app.web.services.table_export_snapshot_delivery_evidence import (
    read_snapshot_delivery_evidence, read_snapshot_delivery_evidence_capability, SnapshotDeliveryEvidenceUnavailable,
)
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
from app.web.services.indexed_compound_id_sample import indexed_compound_sample_capability, sample_indexed_compound_ids
from app.web.services.indexed_id_sample import indexed_sample_capability, sample_indexed_ids
from app.web.services.indexed_projection_preview import indexed_projection_capability, preview_indexed_projection
from app.web.services.column_planner_statistic import ColumnPlannerStatisticUnavailable, read_column_planner_statistic
from app.web.services.lineage_column_mapping_paths import ColumnMappingRootUnavailable, read_column_mapping_paths
from app.web.services.lineage_column_references import read_lineage_column_references
from app.web.services.preview_index_coverage import preview_index_coverage
from app.web.services.transfer_observed_lineage import observed_transfer_lineage
from app.web.services.transfer_plans import list_transfer_plans, preflight_transfer_plan, save_transfer_plan
from app.web.services.transfer_runs import (
    SANDBOX, list_transfer_attempts, list_transfer_runs, run_sandbox_transfer, transfer_run_capabilities,
)
from app.web.services.monthly_committed_run import MonthlyCommittedRunUnavailable, read_monthly_committed_run
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


@write.post("/push-inbox-sandbox/event-path", response_model=PushInboxEventPath, response_model_exclude_none=True)
async def post_local_push_event_path(
    request: Request, response: Response, session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PushInboxEventPath | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    denied = JSONResponse({"detail": "Synthetic event path unavailable"}, status_code=404,
                          headers={"Cache-Control": "private, no-store"})
    try:
        payload = PushInboxEventPathRequest.model_validate(await _parse_snapshot_intent_json(request))
    except (ValueError, TimeoutError, RecursionError):
        return denied
    try:
        return await read_push_event_path(session, payload.receiptId)
    except PushInboxEventPathUnavailable:
        return denied


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


@write.post(
    "/pipeline-sandbox/revisions/restore", status_code=201, response_model=PipelineSandboxRevision,
    openapi_extra={"requestBody": {"required": True, "content": {
        "application/json": {"schema": PipelineSandboxRestore.model_json_schema()},
    }}},
)
async def post_local_pipeline_restore(
    request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PipelineSandboxRevision | JSONResponse:
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    try:
        payload = PipelineSandboxRestore.model_validate(await _parse_snapshot_intent_json(request))
    except Exception:
        return JSONResponse({"detail": "Builder restoration accepts only an exact reviewed recipe"},
                            status_code=422, headers=headers)
    try:
        return await restore_pipeline_revision(session, payload, current_user.id)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)


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


@write.get("/pipeline-sandbox/handoff/recipient-proof")
async def get_pipeline_handoff_recipient_proof(
    current_user: CurrentActiveSuperuser, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PipelineHandoffRecipientCapability:
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    if request.query_params:
        raise HTTPException(422, "Recipient proof uses one fixed synthetic sink", headers=headers)
    try:
        with fail_after(3):
            return await recipient_proof_capability(session, current_user.id)
    except (DBAPIError, TimeoutError, OSError):
        return PipelineHandoffRecipientCapability(available=False)


@write.post("/pipeline-sandbox/handoff/recipient-proof")
async def post_pipeline_handoff_recipient_proof(
    payload: PipelineHandoffRecipientRequest, current_user: CurrentActiveSuperuser, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PipelineHandoffRecipientProof:
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    if request.query_params:
        raise HTTPException(422, "Recipient proof uses only the supplied receipt and generation", headers=headers)
    try:
        with fail_after(3):
            return await read_recipient_proof(session, current_user.id, payload)
    except (DBAPIError, TimeoutError, OSError):
        raise recipient_proof_unavailable() from None


@write.post("/pipeline-sandbox/handoff/sender-proof", response_model=PipelineHandoffCopyReceipt)
async def post_pipeline_handoff_sender_proof(
    current_user: CurrentActiveSuperuser, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> PipelineHandoffCopyReceipt | JSONResponse:
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    denied = JSONResponse({"detail": "No authorized recorded sender proof is available"}, status_code=404,
                          headers=headers)
    try:
        data = await _parse_snapshot_intent_json(request)
        payload = PipelineHandoffCopyRequest.model_validate(data)
        if any(data[key] != str(getattr(payload, key)) for key in ("requestId", "outputId", "expectedSinkGeneration")):
            return denied
    except (ValueError, TimeoutError, RecursionError):
        return denied
    try:
        with fail_after(3):
            return await read_sender_proof(session, current_user.id, payload)
    except (SenderProofUnavailable, DBAPIError, MultipleResultsFound, TimeoutError, OSError):
        return denied


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


def published_query(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0, le=1_000_000),
    search: str = Query("", max_length=1000),
    filters: str = Query(
        "[]",
        max_length=8192,
        description='JSON filter rows, e.g. [{"field":"category","operator":"=","value":"alpha"}]',
    ),
) -> dict[str, Any]:
    if set(request.query_params) - {"limit", "offset", "search", "filters"}:
        raise HTTPException(422, "Supported parameters are filters, search, limit and offset")
    try:
        rows = json.loads(filters)
    except (ValueError, RecursionError):
        raise HTTPException(422, "filters must be a JSON array") from None
    if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
        raise HTTPException(422, "filters must contain up to 100 filter rows")
    return {"limit": limit, "offset": offset, "search": search, "filters": rows}


@router.get("/published/{publication_id}/data")
async def published_rows(
    publication_id: UUID,
    response: Response,
    query: Annotated[dict, Depends(published_query)],
    key: Annotated[str | None, Header(alias="X-API-Key", max_length=256)] = None,
) -> dict[str, Any]:
    """Read rows within the published scope. Use nextOffset for the next page."""
    if not key:
        raise HTTPException(401, "X-API-Key is required")
    response.headers["Cache-Control"] = "private, no-store"
    return await get_etl_workspace().published_data(key, query, publication_id=str(publication_id))


@router.get("/published/{token}", deprecated=True)
async def published_data(
    token: str, response: Response, query: Annotated[dict, Depends(published_query)]
) -> dict[str, Any]:
    """Previously issued URL tokens remain valid until rotated, revoked or expired."""
    response.headers["Cache-Control"] = "private, no-store"
    return await get_etl_workspace().published_data(token, query)


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


@write.get("/transfer-plans/{plan_id}/schedule/jobs/{job_id}/committed-run", response_model=MonthlyCommittedRun)
async def get_monthly_committed_run(
    plan_id: str, job_id: str, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> MonthlyCommittedRun | JSONResponse:
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    denied = JSONResponse({"detail": "Monthly committed run unavailable"}, status_code=404, headers=headers)
    if request.query_params or len(plan_id) != 36 or len(job_id) != 36:
        return denied
    try:
        plan, job = UUID(plan_id), UUID(job_id)
        if str(plan) != plan_id or str(job) != job_id:
            return denied
        return await read_monthly_committed_run(session, plan, job)
    except (MonthlyCommittedRunUnavailable, ValueError):
        return denied


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
async def get_batch_sync_capabilities(
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> BatchSyncCapabilities:
    capabilities = batch_sync_capabilities()
    try:
        asset = await available_batch_lineage_asset(session)
    except DBAPIError:
        raise HTTPException(503, "Managed batch lineage availability is temporarily unavailable") from None
    capabilities.lineageAsset = asset
    capabilities.incrementalLineage = True if asset else None
    return capabilities


def export_destination(request: Request, destination: ExportDestination = "primary") -> ExportDestination:
    if len(request.query_params.getlist("destination")) > 1:
        raise HTTPException(422, "Specify exactly one approved Table Export destination")
    return destination


ExportDestinationDep = Annotated[ExportDestination, Depends(export_destination)]


@write.get("/table-export-sandbox/snapshot-preview")
async def get_table_export_snapshot_preview(
    request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> TableExportSnapshotPreviewCapability:
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    if request.query_params:
        raise HTTPException(422, "This comparison uses one fixed snapshot profile", headers=headers)
    try:
        with fail_after(3):
            return await read_snapshot_preview_capability(session)
    except (DBAPIError, TimeoutError, OSError):
        return TableExportSnapshotPreviewCapability(available=False)


@write.post("/table-export-sandbox/snapshot-preview")
async def post_table_export_snapshot_preview(
    payload: TableExportSnapshotPreviewRequest, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> TableExportSnapshotPreview:
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    if request.query_params:
        raise HTTPException(422, "This comparison uses one fixed snapshot profile", headers=headers)
    try:
        with fail_after(7):
            return await compare_table_export_snapshots(session, payload)
    except HTTPException as exc:
        raise HTTPException(exc.status_code, exc.detail, headers=headers) from None
    except (DBAPIError, TimeoutError, OSError):
        raise HTTPException(503, "Synthetic snapshot comparison is temporarily unavailable", headers=headers) from None


def _unique_delivery_evidence_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON key")
    return result


@write.get("/table-export-sandbox/snapshot-delivery-evidence", response_model_exclude_none=True)
async def get_table_export_snapshot_delivery_evidence(
    request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> SnapshotDeliveryEvidenceCapability:
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    if request.query_params:
        return SnapshotDeliveryEvidenceCapability(available=False)
    return await read_snapshot_delivery_evidence_capability(session, current_user.id)


@write.post(
    "/table-export-sandbox/snapshot-delivery-evidence",
    response_model=SnapshotDeliveryEvidence,
    response_model_exclude_none=True,
)
async def post_table_export_snapshot_delivery_evidence(
    request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> SnapshotDeliveryEvidence | JSONResponse:
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    denied = JSONResponse({"detail": "Recorded snapshot delivery evidence unavailable"}, status_code=404, headers=headers)
    if request.query_params or request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        return denied
    try:
        body = bytearray()
        with fail_after(2):
            async for chunk in request.stream():
                if len(chunk) > 1024 - len(body):
                    raise ValueError("Invalid lookup")
                body.extend(chunk)
        payload = SnapshotDeliveryEvidenceRequest.model_validate(
            json.loads(body.decode("utf-8"), object_pairs_hook=_unique_delivery_evidence_keys)
        )
    except Exception:
        return denied
    try:
        return await read_snapshot_delivery_evidence(session, current_user.id, payload)
    except SnapshotDeliveryEvidenceUnavailable:
        return denied


async def _parse_snapshot_intent_json(request: Request):
    if request.query_params or request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise ValueError("Invalid request")
    body = bytearray()
    with fail_after(2):
        async for chunk in request.stream():
            if len(chunk) > 1024 - len(body):
                raise ValueError("Invalid request")
            body.extend(chunk)
    return json.loads(body.decode("utf-8"), object_pairs_hook=_unique_delivery_evidence_keys)


def _snapshot_delivery_web_denied() -> JSONResponse:
    return JSONResponse({"detail": "Synthetic snapshot delivery review unavailable"}, status_code=404,
                        headers={"Cache-Control": "private, no-store"})


async def _parse_snapshot_delivery_web(request: Request, model):
    data = await _parse_snapshot_intent_json(request)
    payload = model.model_validate(data)
    if any(data[key] != str(getattr(payload, key)) for key in model.model_fields):
        raise ValueError("Invalid request")
    return payload


@write.get("/table-export-sandbox/snapshot-delivery", response_model=SnapshotDeliveryWebCapability)
async def get_snapshot_delivery_web(
    request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> SnapshotDeliveryWebCapability | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    if request.query_params:
        return _snapshot_delivery_web_denied()
    return await snapshot_delivery_web_capability(session, current_user.id)


@write.post("/table-export-sandbox/snapshot-delivery/review", response_model=SnapshotDeliveryWebReview)
async def post_snapshot_delivery_web_review(
    request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> SnapshotDeliveryWebReview | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        payload = await _parse_snapshot_delivery_web(request, SnapshotDeliveryWebLookup)
        return await snapshot_delivery_web_review(session, current_user.id, payload)
    except (ValueError, TimeoutError, RecursionError, SnapshotWebDeliveryUnavailable):
        return _snapshot_delivery_web_denied()


@write.post("/table-export-sandbox/snapshot-delivery", response_model=SnapshotDeliveryWebReceipt)
async def post_snapshot_delivery_web(
    request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> SnapshotDeliveryWebReceipt | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        payload = await _parse_snapshot_delivery_web(request, SnapshotDeliveryWebRun)
        return await snapshot_delivery_web_run(session, current_user.id, payload)
    except (ValueError, TimeoutError, RecursionError, SnapshotWebDeliveryUnavailable):
        return _snapshot_delivery_web_denied()
    except SnapshotDeliveryUnavailable:
        return JSONResponse({"detail": "Synthetic snapshot delivery unconfirmed; do not resend"}, status_code=503,
                            headers={"Cache-Control": "private, no-store"})


def _snapshot_intent_web_denied() -> JSONResponse:
    return JSONResponse({"detail": "Snapshot preparation unavailable"}, status_code=404,
                        headers={"Cache-Control": "private, no-store"})


@write.get("/table-export-sandbox/snapshot-intent", response_model_exclude_none=True)
async def get_snapshot_intent_web(
    request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> SnapshotIntentWebCapability:
    response.headers["Cache-Control"] = "private, no-store"
    if request.query_params:
        return SnapshotIntentWebCapability(available=False)
    return await snapshot_intent_web_capability(session, current_user.id)


@write.post("/table-export-sandbox/snapshot-intent/head", response_model=SnapshotIntentWebHead,
            response_model_exclude_none=True)
async def post_snapshot_intent_web_head(
    request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> SnapshotIntentWebHead | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        payload = SnapshotIntentWebHeadRequest.model_validate(await _parse_snapshot_intent_json(request))
    except Exception:
        return _snapshot_intent_web_denied()
    try:
        return await snapshot_intent_web_head(session, current_user.id, payload)
    except SnapshotPublicationIntentUnavailable:
        return _snapshot_intent_web_denied()


@write.post("/table-export-sandbox/snapshot-intent", response_model=SnapshotIntentWebPrepared)
async def post_snapshot_intent_web_prepare(
    request: Request, response: Response, current_user: CurrentActiveSuperuser,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> SnapshotIntentWebPrepared | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        payload = SnapshotIntentWebPrepareRequest.model_validate(await _parse_snapshot_intent_json(request))
    except Exception:
        return _snapshot_intent_web_denied()
    try:
        return await snapshot_intent_web_prepare(session, current_user.id, payload)
    except SnapshotPublicationIntentPredecessorChanged:
        return JSONResponse({"detail": {"code": "predecessor_changed", "message": "Reviewed predecessor changed"}},
                            status_code=409, headers={"Cache-Control": "private, no-store"})
    except SnapshotPublicationIntentUnavailable:
        return _snapshot_intent_web_denied()


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


@write.post(
    "/batch-syncs/{plan_id}/incremental-preflight", response_model=BatchSyncIncrementalPreflight,
    response_model_exclude_none=True, openapi_extra={"requestBody": {"required": True, "content": {
        "application/json": {"schema": BatchSyncIncrementalPreflightRequest.model_json_schema()},
    }}},
)
async def post_incremental_batch_preflight(
    plan_id: UUID, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> BatchSyncIncrementalPreflight | JSONResponse:
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    try:
        payload = BatchSyncIncrementalPreflightRequest.model_validate(await _parse_snapshot_intent_json(request))
    except Exception:
        return JSONResponse({"detail": "Specify the exact saved plan and source revisions"}, status_code=422, headers=headers)
    try:
        return await preflight_incremental_batch(session, plan_id, payload)
    except HTTPException as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=headers)


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


def _data_connection_draft_openapi() -> dict[str, Any]:
    # The route parses explicitly to redact validation failures; retain the
    # documented body schema without adding unresolved local JSON-schema refs.
    schema = DataConnectionDraftPayload.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference is not None:
                value = {**definitions[reference.rsplit("/", 1)[-1]], **{k: v for k, v in value.items() if k != "$ref"}}
            return {key: inline(item) for key, item in value.items()}
        return value

    return {"requestBody": {"required": True, "content": {"application/json": {"schema": inline(schema)}}}}


async def _data_connection_draft_body(request: Request, *, saved: bool) -> DataConnectionPayload:
    # Pydantic validation errors may reflect attacker-controlled credential field paths.
    # Parse only these draft routes and return one stable error without payload/location echo.
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media and not (media == "application/json" or (media.startswith("application/") and media.endswith("+json"))):
        raise HTTPException(422, "Invalid data source configuration or credentials")
    try:
        body = bytearray()
        with fail_after(3):
            async for chunk in request.stream():
                if len(chunk) > 128 * 1024 - len(body):
                    raise ValueError("Invalid connector draft")
                body.extend(chunk)
        model = DataConnectionDraftPayload if saved else DataConnectionPayload
        return model.model_validate(json.loads(body.decode("utf-8"), object_pairs_hook=_unique_delivery_evidence_keys))
    except Exception:
        raise HTTPException(422, "Invalid data source configuration or credentials") from None


@write.post("/data-connections/test", openapi_extra=_data_connection_draft_openapi())
async def test_data_connection(request: Request, connectionId: UUID | None = None) -> dict[str, Any]:
    if len(request.query_params.getlist("connectionId")) > 1:
        raise HTTPException(422, "Invalid data source configuration or credentials")
    payload = await _data_connection_draft_body(request, saved=connectionId is not None)
    expected = payload.expectedUpdatedAt if isinstance(payload, DataConnectionDraftPayload) else None
    return await get_data_connections().test(payload, str(connectionId) if connectionId else None, expected_updated_at=expected)


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


@write.put("/data-connections/{connection_id}", openapi_extra=_data_connection_draft_openapi())
async def update_data_connection(connection_id: UUID, request: Request,
                                 current_user: Any = Depends(get_current_active_superuser)) -> dict[str, Any]:
    payload = await _data_connection_draft_body(request, saved=True)
    assert isinstance(payload, DataConnectionDraftPayload)
    return await get_data_connections().save(
        payload, str(current_user.id), str(connection_id), expected_updated_at=payload.expectedUpdatedAt,
    )


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


@read.get("/{layer}/{table}/row-preview-policy", response_model=RowPreviewPolicy)
async def get_row_preview_policy(
    layer: str, table: str, request: Request, response: Response,
) -> RowPreviewPolicy | JSONResponse:
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    if request.query_params:
        return JSONResponse({"detail": "Row preview policy accepts no query parameters"}, status_code=422, headers=headers)
    try:
        return await get_etl_layers().row_preview_policy(layer, table)
    except HTTPException as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=headers)


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


@write.get("/{schema}/{table}/indexed-compound-id-sample")
async def get_indexed_compound_id_sample(
    schema: WarehouseSchema, table: TableName, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> IndexedSampleCapability:
    if request.query_params:
        raise HTTPException(422, "Indexed compound ID sample capability accepts no query parameters")
    response.headers["Cache-Control"] = "private, no-store"
    return await indexed_compound_sample_capability(session, schema, table)


@write.post("/{schema}/{table}/indexed-compound-id-sample")
async def post_indexed_compound_id_sample(
    schema: WarehouseSchema, table: TableName, payload: IndexedCompoundSampleRequest, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> IndexedSampleResult:
    if request.query_params:
        raise HTTPException(422, "Indexed compound ID sample accepts only its fixed predicate body")
    response.headers["Cache-Control"] = "private, no-store"

    async def wait_for_disconnect() -> None:
        while (await request.receive())["type"] != "http.disconnect":
            pass

    return await run_bounded_index_query(
        sample_indexed_compound_ids(session, schema, table, payload), wait_for_disconnect(),
        IndexedSampleResult(state="blocked", reason="query_unavailable"),
        cancelled_message="Indexed compound ID sample was cancelled",
    )


@write.get("/{schema}/{table}/indexed-projection-preview", response_model=IndexedProjectionCapability)
async def get_indexed_projection_preview(
    schema: WarehouseSchema, table: TableName, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> IndexedProjectionCapability | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    if request.query_params:
        return JSONResponse({"detail": "Invalid projection preview request"}, status_code=422,
                            headers={"Cache-Control": "private, no-store"})
    return await indexed_projection_capability(session, schema, table)


@write.post("/{schema}/{table}/indexed-projection-preview", response_model=IndexedProjectionResult)
async def post_indexed_projection_preview(
    schema: WarehouseSchema, table: TableName, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> IndexedProjectionResult | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        data = await _parse_snapshot_intent_json(request)
        payload = IndexedProjectionRequest.model_validate(data)
        if data["expectedProjectionRevision"] != str(payload.expectedProjectionRevision):
            raise ValueError("Invalid revision")
    except (ValueError, TimeoutError, RecursionError):
        return JSONResponse({"detail": "Invalid projection preview request"}, status_code=422,
                            headers={"Cache-Control": "private, no-store"})

    async def wait_for_disconnect() -> None:
        while (await request.receive())["type"] != "http.disconnect":
            pass

    return await run_bounded_index_query(
        preview_indexed_projection(session, schema, table, payload), wait_for_disconnect(),
        IndexedProjectionResult(state="blocked", reason="query_unavailable"),
        cancelled_message="Indexed projection preview was cancelled",
    )


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
        raise HTTPException(422, "Index coverage accepts only a filter or sort shape in the body")
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


@write.get(
    "/{schema}/{table}/column-planner-statistic", response_model=ColumnPlannerStatistic, response_model_exclude_none=True,
)
async def table_column_planner_statistic(
    schema: str, table: str, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> ColumnPlannerStatistic | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    denied = JSONResponse({"detail": "Column planner statistic unavailable"}, status_code=404,
                          headers={"Cache-Control": "private, no-store"})
    if len(request.query_params.multi_items()) != 1 or "column" not in request.query_params:
        return denied
    try:
        return await read_column_planner_statistic(session, schema, table, request.query_params["column"])
    except ColumnPlannerStatisticUnavailable:
        return denied


@write.get("/{schema}/{table}/lineage-column-mapping-paths", response_model=ColumnMappingPaths)
async def table_lineage_column_mapping_paths(
    schema: str, table: str, request: Request, response: Response,
    session: Annotated[AsyncSession, Depends(get_db, use_cache=False)],
) -> ColumnMappingPaths | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    denied = JSONResponse({"detail": "Column mapping path unavailable"}, status_code=404,
                          headers={"Cache-Control": "private, no-store"})
    query = request.query_params
    if len(query.multi_items()) != 2 or set(query) != {"column", "direction"}:
        return denied
    direction = query["direction"]
    if direction not in ("incoming", "outgoing"):
        return denied
    try:
        return await read_column_mapping_paths(session, schema, table, query["column"], direction)
    except ColumnMappingRootUnavailable:
        return denied


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
async def filtered_data(layer: str, table: str, payload: DataQuery, current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    if payload.forEditing:
        get_current_active_superuser(current_user)
    return await get_etl_layers().data(layer, table, payload.model_dump())


@read.post("/{layer}/{table}/preview-rows")
async def table_preview_rows(layer: str, table: str, payload: PreviewRowsQuery, request: Request, current_user: Any = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if payload.forEditing:
        get_current_active_superuser(current_user)
    row_keys = None
    if payload.savedListId is not None:
        from app.web.api.models.explorer_saved_items import ExplorerSavedItem
        from app.web.api.routes.explorer_layouts import asset
        from app.web.api.routes.explorer_saved_items import scope

        schema, _ = await asset(layer, table, current_user)
        item = await session.scalar(select(ExplorerSavedItem).where(
            *scope(schema, table),
            ExplorerSavedItem.id == payload.savedListId,
            ExplorerSavedItem.kind == "list",
            (ExplorerSavedItem.owner_id == current_user.id) | ExplorerSavedItem.shared.is_(True),
        ))
        if item is None:
            raise HTTPException(404, "Saved list not found")
        row_keys = item.content.get("keys")
        if not isinstance(row_keys, list):
            raise HTTPException(409, "Saved list has no object keys")
    async def wait_for_disconnect():
        while (await request.receive())["type"] != "http.disconnect":
            pass

    query = asyncio.create_task(get_etl_layers().data(
        layer, table, payload.model_dump(exclude={"savedListId"}), preview_rows=True, row_keys=row_keys,
    ))
    watcher = asyncio.create_task(wait_for_disconnect())
    try:
        done, _ = await asyncio.wait((query, watcher), return_when=asyncio.FIRST_COMPLETED)
        if query in done:
            return query.result()
        watcher.result()
        raise HTTPException(499, "Explorer query was cancelled")
    finally:
        for task in (query, watcher):
            if not task.done():
                task.cancel()
        with CancelScope(shield=True):
            await asyncio.shield(asyncio.gather(query, watcher, return_exceptions=True))


@read.get("/object-storage")
async def explorer_object_storage(current_user: Any = Depends(get_current_user)):
    from app.web.services.explorer_files import storage_configuration
    return storage_configuration()


@read.post("/{layer}/{table}/record-html")
async def explorer_record_html(layer: str, table: str, payload: ExplorerFileQuery, response: Response,
                               current_user: Any = Depends(get_current_user)):
    from app.web.services.explorer_files import html_preview
    response.headers["Cache-Control"] = "private, no-store"
    return await html_preview(layer, table, payload.key, payload.field)


@read.post("/{layer}/{table}/record-file")
async def explorer_record_file(layer: str, table: str, payload: ExplorerFileQuery, response: Response,
                               current_user: Any = Depends(get_current_user)):
    from app.web.services.explorer_files import download_link
    response.headers["Cache-Control"] = "private, no-store"
    return await download_link(layer, table, payload.key, payload.field, download=payload.download, path=payload.path)


@read.post("/{layer}/{table}/row-by-key")
async def explorer_row_by_key(layer: str, table: str, payload: ExplorerRowKey, response: Response,
                              current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    from app.web.services.explorer_rows import row_by_key

    response.headers["Cache-Control"] = "no-store"
    return await row_by_key(layer, table, payload.key, editable=current_user.is_superuser)


@read.post("/{layer}/{table}/row-links")
async def explorer_row_links(layer: str, table: str, payload: ExplorerRowLinksQuery, response: Response) -> dict[str, Any]:
    from app.web.services.explorer_rows import row_links

    response.headers["Cache-Control"] = "no-store"
    return await row_links(layer, table, payload)


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
async def stream(layer: str, table: str, topic: str | None = None,
                 current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await etl_runtime.stream(layer, table, topic, include_hidden=current_user.is_superuser)


@read.get("/{layer}/{table}/cdc-evidence")
async def capture_evidence(layer: str, table: str, response: Response,
                           current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    from app.web.services.etl_capture import capture

    response.headers["Cache-Control"] = "no-store"
    return await capture(layer, table, include_hidden=current_user.is_superuser)


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
