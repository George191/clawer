"""Scheduling and system handlers backed by shared automation task records."""

import os
from typing import Any

from app.logger import get_logger
from app.scheduler.celery_app import app, run_async
from app.scheduler.dispatch import dispatch_run, is_recurring
from app.scheduler.task_log_capture import TaskLogCapture
from app.utils.connection_errors import is_retryable_connection_error
from app.web.services.automation_task_store import automation_task_store

logger = get_logger(__name__)
CLEANUP_TASK_NAME = "app.scheduler.tasks.automation.cleanup_task_logs"


async def _evaluate_transfer_freshness_checks() -> dict[str, int] | None:
    truthy = {"1", "true", "on", "yes"}
    if (
        os.environ.get("ENVIRONMENT") != "local"
        or os.environ.get("TRANSFER_SANDBOX_ENABLED", "").strip().lower() not in truthy
        or os.environ.get("TRANSFER_SANDBOX_FRESHNESS_ENABLED", "").strip().lower() not in truthy
    ):
        return None

    from app.web.services.asset_freshness import dispatch_asset_freshness_checks

    # A bounded DB-only evaluation can run directly in the existing minute
    # task. It does not need another beat entry or another broker delivery.
    return await dispatch_asset_freshness_checks()


async def _dispatch_due_tasks() -> dict[str, Any]:
    tasks = await automation_task_store.claim_due_recurring_tasks()
    dispatched = 0
    for task in tasks:
        task_id = str(task["id"])
        run_id = str(task["current_run_id"])
        try:
            dispatch_run(task)
        except Exception:
            await automation_task_store.update_task(
                task_id, {"status": "failed", "throughput": 0},
                expected_run_id=run_id,
                expected_celery_task_id=str(task["celery_task_id"]),
            )
            logger.exception("Failed to dispatch automation task %s", task_id)
            continue
        dispatched += 1
        try:
            await automation_task_store.write_task_log(
                task_id, run_id, "Scheduler", "info",
                "Recurring dispatch submitted to Celery worker",
            )
        except Exception:
            logger.exception("Failed to write recurring dispatch log for task %s", task_id)
    result: dict[str, Any] = {"claimed": len(tasks), "dispatched": dispatched}
    from app.scheduler.tasks.batch_syncs.tasks import dispatch_batch_sync_schedule_jobs
    from app.scheduler.tasks.transfer_upstream.tasks import dispatch_upstream_transfer_jobs
    from app.scheduler.tasks.transfers.tasks import dispatch_transfer_schedule_jobs

    for key, dispatch in (
        ("transferSchedules", dispatch_transfer_schedule_jobs),
        ("batchSyncSchedules", dispatch_batch_sync_schedule_jobs),
        ("upstreamTransfers", dispatch_upstream_transfer_jobs),
        ("transferFreshnessChecks", _evaluate_transfer_freshness_checks),
    ):
        try:
            counts = await dispatch()
            if counts is not None:
                result[key] = counts
        except Exception:
            # Each durable dispatcher retries on the next minute tick independently.
            logger.exception("Scheduled dispatch tick failed for %s", key)
            result[key] = {"error": "dispatch_tick_failed"}
    return result


@app.task(bind=True, name="app.scheduler.tasks.automation.dispatch_due", max_retries=None)
def dispatch_due(self: Any) -> dict[str, Any]:
    try:
        return run_async(_dispatch_due_tasks())
    except Exception as exc:
        if not is_retryable_connection_error(exc):
            raise
        raise self.retry(exc=exc, countdown=0) from exc


async def _cleanup_task_logs(task_id: str, delivery_id: str) -> dict[str, Any]:
    task = await automation_task_store.get_task_for_execution(task_id)
    if (
        task is None
        or task["status"] != "running"
        or str(task.get("celery_task_id")) != delivery_id
        or task.get("task_path") != CLEANUP_TASK_NAME
        or task.get("control_state") == "canceled"
    ):
        return {"skipped": True}
    run_id = str(task["current_run_id"])
    capture = TaskLogCapture(task_id, run_id, automation_task_store)
    capture.start()
    try:
        logger.info("Task log cleanup started")
        result = await automation_task_store.cleanup_task_logs()
        await automation_task_store.increment_run_stats(
            task_id, run_id, deleted=result["deleted_runs"],
        )
        logger.info("Task log cleanup completed: deleted_runs=%d", result["deleted_runs"])
        await automation_task_store.update_task(
            task_id,
            {"status": "queued" if is_recurring(task.get("schedule") or {}) else "completed", "progress": 100},
            expected_run_id=run_id, expected_celery_task_id=delivery_id,
        )
        return result
    except Exception as exc:
        if is_retryable_connection_error(exc):
            logger.warning("Task log cleanup will retry after connection failure: %s", exc)
            raise
        logger.exception("Task log cleanup failed")
        await automation_task_store.update_task(
            task_id, {"status": "failed"},
            expected_run_id=run_id, expected_celery_task_id=delivery_id,
        )
        raise
    finally:
        await capture.stop()


@app.task(bind=True, name=CLEANUP_TASK_NAME, max_retries=None)
def cleanup_task_logs(self: Any, task_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a claimed cleanup task and persist its own batch and console logs."""
    try:
        return run_async(_cleanup_task_logs(task_id, str(self.request.id)))
    except Exception as exc:
        if not is_retryable_connection_error(exc):
            raise
        raise self.retry(exc=exc, countdown=0) from exc
