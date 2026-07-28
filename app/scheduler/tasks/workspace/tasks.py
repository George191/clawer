"""Celery execution bridge for AI Collect workspace crawl tasks."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.adapters import load_adapter_class_from_source
from app.crawler.checkpoint import PageCheckpointStore
from app.crawler.incremental import build_time_watermark
from app.engine.spider_engine import CrawlResult, SpiderEngine
from app.engine.template_loader import TemplateLoader
from app.logger import get_logger
from app.scheduler.celery_app import app, run_async
from app.scheduler.task_log_capture import WorkspaceTaskLogCapture
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


async def _crawl_template(
    task_id: str,
    template_name: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    engine: SpiderEngine | None = None
    checkpoint_store: PageCheckpointStore | None = None
    checkpoint_connected = False
    run_id = str(uuid.uuid4())
    log_capture = WorkspaceTaskLogCapture(task_id, run_id, ai_collect_store)
    log_capture.start()
    try:
        logger.info("Workspace crawl task started: %s", task_id)
        task = await ai_collect_store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Workspace task not found: {task_id}")
        schedule = dict(task.get("schedule") or {})
        policies = dict(task.get("policies") or {})
        params = {str(key): str(value) for key, value in parameters.items() if value is not None}
        template_version = str(task.get("template_version") or "v1.0")
        artifacts = await ai_collect_store.get_template_runtime_artifacts(
            template_name,
            template_version,
        )
        template = TemplateLoader().load_content(
            artifacts["template_yaml"],
            param_values=params or None,
            source=artifacts["template_key"],
        )
        if template.name != template_name:
            raise RuntimeError(
                f"Released template name mismatch: expected {template_name}, got {template.name}"
            )
        adapter_class = None
        if template.adapter:
            adapter_code = artifacts["adapter_code"]
            if not adapter_code:
                raise RuntimeError(
                    f"Released adapter is missing: {template_name}@{template_version}"
                )
            adapter_class = load_adapter_class_from_source(
                template.adapter,
                adapter_code,
                artifacts["adapter_key"],
            )
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
        template._crawl_context = {
            "task_id": task_id,
            "workspace_task_id": task_id,
            "batch_index": 1,
            "batch_count": 1,
            "incremental_watermark": incremental_watermark,
        }
        engine = SpiderEngine(adapter_class=adapter_class)

        async def update_progress(_page: int, result: CrawlResult) -> None:
            if not await _wait_until_runnable(task_id):
                raise asyncio.CancelledError
            await checkpoint_store.save(_page)
            await ai_collect_store.update_task(
                task_id,
                {
                    "progress": _progress_percent(result),
                    "records": result.saved_records,
                    "inserted_records": result.inserted_records,
                    "updated_records": result.updated_records,
                    "deleted_records": result.deleted_records,
                },
            )

        result = await engine.crawl_from_page(
            template, resume_page, update_progress
        )
        if not await _wait_until_runnable(task_id):
            return {"task_id": task_id, "status": "canceled"}
        if not result.success:
            raise RuntimeError("; ".join(result.errors) or "Crawl failed")

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
    except Exception:
        await ai_collect_store.update_task(
            task_id,
            {"status": "failed", "throughput": 0},
        )
        logger.exception("Workspace crawl task failed: %s", task_id)
        raise
    finally:
        if engine is not None:
            await engine.close()
        if checkpoint_store is not None:
            await checkpoint_store.close()
        await log_capture.stop()


@app.task(name=TASK_NAME)
def crawl_template(
    task_id: str,
    template_name: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Run one released workspace template in a Celery worker."""
    return run_async(_crawl_template(task_id, template_name, parameters))


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
                task_id=task_id,
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
