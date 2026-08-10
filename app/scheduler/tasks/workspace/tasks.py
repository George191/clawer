"""Celery execution bridge for AI Collect workspace crawl tasks."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from pymongo.errors import ConnectionFailure

from app.adapters import BaseSiteAdapter, GenericAdapter
from app.crawler.batch_params import (
    BatchParamNames,
    build_batch_params,
    normalize_batch_param_names,
)
from app.crawler.checkpoint import PageCheckpointStore
from app.crawler.incremental import build_time_watermark
from app.engine.spider_engine import CrawlResult, SpiderEngine
from app.engine.template_loader import TemplateLoader
from app.logger import get_logger
from app.scheduler.celery_app import app, run_async
from app.scheduler.task_log_capture import WorkspaceTaskLogCapture
from app.storage.minio_client import get_business_metadata_minio_client
from app.utils.runtime_control import reset_control_checkpoint, set_control_checkpoint
from app.web.services.ai_collect_store import ai_collect_store

logger = get_logger(__name__)

TASK_NAME = "app.scheduler.tasks.workspace.crawl_template"
DISPATCH_TASK_NAME = "app.scheduler.tasks.workspace.dispatch_due"


def _is_recurring(schedule: dict[str, Any]) -> bool:
    mode = str(schedule.get("mode") or "once")
    if mode == "recurring":
        return schedule.get("recurring_mode") in {"daily", "interval"}
    return mode in {"daily", "interval"}


def _status_after_success(schedule: dict[str, Any]) -> str:
    return "queued" if _is_recurring(schedule) else "completed"


async def _wait_until_runnable(task_id: str) -> bool:
    """Wait while paused and return false when the task was canceled."""
    while True:
        control = await ai_collect_store.get_task_control(task_id)
        if control is None or control.get("control_state") == "canceled":
            return False
        if control.get("status") != "paused":
            return True
        await asyncio.sleep(1)


def _progress_percent(result: CrawlResult) -> int:
    if not result.total_pages:
        return 0
    return min(99, int(result.pages_processed * 100 / result.total_pages))


async def _batch_parameter_sets(
    template: Any,
    policies: dict[str, Any],
    adapter_class: type[BaseSiteAdapter] | None,
) -> list[tuple[dict[str, str], dict[str, Any]]]:
    batch = policies.get("batch")
    if not isinstance(batch, dict):
        return []

    config = template.batch_params
    raw_param_names: BatchParamNames = batch.get("parameter") or (
        config.param_name if config else ""
    )
    param_names = normalize_batch_param_names(raw_param_names)

    raw_values = batch.get("values")
    if isinstance(raw_values, list) and raw_values:
        values = [str(value).strip() for value in raw_values if str(value).strip()]
    else:
        object_key = str(batch.get("object_key") or "").strip()
        if not object_key:
            raise ValueError("Batch input must be uploaded to MinIO before the task starts")
        content = await get_business_metadata_minio_client().get_object_bytes(object_key)
        if content is None:
            raise RuntimeError(f"MinIO batch input is unavailable: {object_key}")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Batch input must use UTF-8 encoding") from exc
        values = [line.strip() for line in text.splitlines() if line.strip()]

    start_line = max(0, int(batch.get("start_line", config.start_line if config else 0) or 0))
    raw_limit = batch.get("limit", config.limit if config else None)
    limit = int(raw_limit) if raw_limit not in (None, "") else None
    values = values[start_line:]
    if limit is not None:
        values = values[:max(0, limit)]
    if not values:
        raise ValueError("Batch parameter input contains no values")

    batch_size = max(1, int(batch.get("size", config.batch_size if config else 1) or 1))
    if len(param_names) > 1 and batch_size != 1:
        raise ValueError("Multi-field batch_params requires batch_size=1")
    builder = adapter_class or GenericAdapter
    parameter_sets: list[tuple[dict[str, str], dict[str, Any]]] = []
    for offset in range(0, len(values), batch_size):
        chunk = values[offset:offset + batch_size]
        parameter_sets.append((
            build_batch_params(
                chunk,
                param_names,
                builder.build_batch_param_value,
            ),
            {
                "start_line": start_line + offset,
                "end_line": start_line + offset + len(chunk) - 1,
                "batch_size": len(chunk),
                "first_value": chunk[0],
                "last_value": chunk[-1],
            },
        ))
    return parameter_sets


def _merge_crawl_result(target: CrawlResult, source: CrawlResult) -> None:
    target.records.extend(source.records)
    target.saved_records += source.saved_records
    target.inserted_records += source.inserted_records
    target.updated_records += source.updated_records
    target.unchanged_records += source.unchanged_records
    target.deleted_records += source.deleted_records
    target.pages_processed += source.pages_processed
    target.downloaded_files.extend(source.downloaded_files)
    target.errors.extend(source.errors)
    if source.latest_record_time and (
        target.latest_record_time is None
        or source.latest_record_time > target.latest_record_time
    ):
        target.latest_record_time = source.latest_record_time


async def _crawl_template(
    task_id: str,
    template_name: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    engine: SpiderEngine | None = None
    checkpoint_store: PageCheckpointStore | None = None
    log_capture: WorkspaceTaskLogCapture | None = None
    checkpoint_connected = False
    run_id = str(uuid.uuid4())
    control_token = set_control_checkpoint(lambda: _wait_until_runnable(task_id))
    try:
        task = await ai_collect_store.get_task(task_id)
        if task is None:
            logger.warning("Skipping stale workspace crawl task: %s", task_id)
            return {"task_id": task_id, "status": "canceled"}
        log_capture = WorkspaceTaskLogCapture(task_id, run_id, ai_collect_store)
        log_capture.start()
        logger.info("Workspace crawl task started: %s", task_id)
        schedule = dict(task.get("schedule") or {})
        policies = dict(task.get("policies") or {})
        params = {str(key): str(value) for key, value in parameters.items() if value is not None}
        template_version = str(task.get("template_version") or "v1.0")
        released = await TemplateLoader().load_released(
            template_name,
            template_version,
            validate_params=False,
        )
        template = released.template
        batch_parameter_sets = await _batch_parameter_sets(
            template, policies, released.adapter_class
        )
        if not batch_parameter_sets:
            template.apply_params(params or None)
            batch_parameter_sets = [(params, {})]
        checkpoint_store = PageCheckpointStore(
            template_name,
            task_id,
            task_id=task_id,
        )
        checkpoint_connected = await checkpoint_store.connect()
        if policies.get("incremental") and not checkpoint_connected:
            raise RuntimeError(
                f"Redis is required for incremental task {task_id}"
            )
        resume_page = await checkpoint_store.load()
        loaded_batch_index = await checkpoint_store.load_batch_index()
        resume_batch_index = loaded_batch_index or 0
        if loaded_batch_index is None and len(batch_parameter_sets) > 1:
            resume_page = None
        if resume_batch_index >= len(batch_parameter_sets):
            resume_batch_index = 0
            resume_page = None
        redis_watermark = (
            await checkpoint_store.load_watermark()
            if policies.get("incremental") else None
        )
        incremental_watermark = build_time_watermark(
            template, policies, redis_watermark
        )
        if incremental_watermark.enabled:
            logger.info(
                "Workspace incremental crawl: task=%s field=%s watermark=%s window_start=%s",
                task_id,
                incremental_watermark.field,
                incremental_watermark.value.isoformat()
                if incremental_watermark.value else "bootstrap",
                incremental_watermark.window_start.isoformat()
                if incremental_watermark.window_start else "full",
            )
        engine = SpiderEngine(adapter_class=released.adapter_class)
        result = CrawlResult(template.name, template.data_type)
        batch_count = len(batch_parameter_sets)
        batch_policy = policies.get("batch") or {}
        delay = float(batch_policy.get(
            "delay", template.batch_params.delay if template.batch_params else 0
        ) or 0)
        loader = TemplateLoader()
        for batch_index, (batch_params, batch_context) in enumerate(batch_parameter_sets):
            if batch_index < resume_batch_index:
                continue
            if not await _wait_until_runnable(task_id):
                raise asyncio.CancelledError
            current_template = loader.load_content(
                released.yaml_content,
                param_values={**params, **batch_params},
                source=released.template_key,
            )
            current_template._crawl_context = {
                "task_id": task_id,
                "workspace_task_id": task_id,
                "batch_index": batch_index + 1,
                "batch_count": batch_count,
                "incremental_watermark": incremental_watermark,
                **batch_context,
            }

            async def update_progress(
                _page: int,
                current: CrawlResult,
                _batch_index: int = batch_index,
            ) -> None:
                if not await _wait_until_runnable(task_id):
                    raise asyncio.CancelledError
                await checkpoint_store.save(
                    _page,
                    batch_index=_batch_index,
                )
                page_progress = _progress_percent(current) / batch_count
                await ai_collect_store.update_task(
                    task_id,
                    {
                        "progress": min(99, int(_batch_index * 100 / batch_count + page_progress)),
                        "records": result.saved_records + current.saved_records,
                        "inserted_records": result.inserted_records + current.inserted_records,
                        "updated_records": result.updated_records + current.updated_records,
                        "deleted_records": result.deleted_records + current.deleted_records,
                    },
                )

            current_result = await engine.crawl_from_page(
                current_template,
                resume_page if batch_index == resume_batch_index else None,
                update_progress,
            )
            _merge_crawl_result(result, current_result)
            if not current_result.success:
                if isinstance(current_result.error, ConnectionFailure):
                    raise current_result.error
                raise RuntimeError("; ".join(current_result.errors) or "Crawl failed")
            await ai_collect_store.update_task(
                task_id,
                {
                    "progress": min(99, int((batch_index + 1) * 100 / batch_count)),
                    "records": result.saved_records,
                    "inserted_records": result.inserted_records,
                    "updated_records": result.updated_records,
                    "deleted_records": result.deleted_records,
                },
            )
            if batch_index + 1 < batch_count:
                next_start_page = (
                    template.list_pagination.start_page
                    if template.list_pagination else 1
                )
                await checkpoint_store.save(
                    next_start_page,
                    batch_index=batch_index + 1,
                )
            if delay > 0 and batch_index + 1 < batch_count:
                await asyncio.sleep(delay)
        if not await _wait_until_runnable(task_id):
            return {"task_id": task_id, "status": "canceled"}

        if incremental_watermark.enabled:
            successful_times = [
                value for value in (
                    incremental_watermark.value,
                    result.latest_record_time,
                ) if value is not None
            ]
            new_watermark = max(successful_times, default=None)
            checkpoint_saved = await checkpoint_store.complete(
                new_watermark.isoformat() if new_watermark else None
            )
        else:
            checkpoint_saved = await checkpoint_store.clear()
        if checkpoint_connected and not checkpoint_saved:
            raise RuntimeError(
                f"Failed to commit Redis checkpoint for task {task_id}"
            )
        final_status = _status_after_success(schedule)
        await ai_collect_store.update_task(
            task_id,
            {
                "status": final_status,
                "progress": 100,
                "records": result.saved_records,
                "throughput": 0,
                "inserted_records": result.inserted_records,
                "updated_records": result.updated_records,
                "deleted_records": result.deleted_records,
            },
        )
        logger.info(
            "Workspace crawl task completed: task=%s saved=%d",
            task_id,
            result.saved_records,
        )
        return {"task_id": task_id, "status": final_status, **result.to_dict()}
    except asyncio.CancelledError:
        logger.info("Workspace crawl task canceled: %s", task_id)
        return {"task_id": task_id, "status": "canceled"}
    except ConnectionFailure:
        logger.warning(
            "Workspace crawl task will retry after MongoDB connection failure: %s",
            task_id,
        )
        raise
    except Exception as exc:
        await ai_collect_store.update_task(
            task_id,
            {"status": "failed", "throughput": 0},
        )
        logger.error("%s: %s", type(exc).__name__, exc)
        raise
    finally:
        reset_control_checkpoint(control_token)
        if engine is not None:
            await engine.close()
        if checkpoint_store is not None:
            await checkpoint_store.close()
        if log_capture is not None:
            await log_capture.stop()


@app.task(
    bind=True,
    name=TASK_NAME,
    max_retries=None,
    throws=(RuntimeError,),
)
def crawl_template(
    self: Any,
    task_id: str,
    template_name: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Run one released workspace template in a Celery worker."""
    try:
        return run_async(_crawl_template(task_id, template_name, parameters))
    except ConnectionFailure as exc:
        raise self.retry(exc=exc, countdown=60) from exc


async def _dispatch_due_workspace_tasks() -> dict[str, Any]:
    tasks = await ai_collect_store.claim_due_recurring_tasks()
    dispatched = 0
    for task in tasks:
        task_id = str(task["id"])
        try:
            app.send_task(
                TASK_NAME,
                args=[
                    task_id,
                    str(task["template_name"]),
                    dict(task.get("parameters") or {}),
                ],
                task_id=str(task["celery_task_id"]),
            )
            await ai_collect_store.append_task_log(
                task_id,
                "info",
                "周期调度已提交到 Celery Worker",
            )
            dispatched += 1
        except Exception:
            await ai_collect_store.update_task(
                task_id,
                {"status": "failed", "throughput": 0},
            )
            logger.exception("Failed to dispatch recurring workspace task %s", task_id)
    return {"claimed": len(tasks), "dispatched": dispatched}


@app.task(name=DISPATCH_TASK_NAME)
def dispatch_due() -> dict[str, Any]:
    """Claim and enqueue due recurring workspace tasks."""
    return run_async(_dispatch_due_workspace_tasks())
