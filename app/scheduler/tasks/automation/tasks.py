"""Scheduling and system handlers backed by shared automation task records."""

from typing import Any

from app.logger import get_logger
from app.scheduler.celery_app import app, run_async
from app.scheduler.dispatch import dispatch_run, is_recurring
from app.scheduler.task_log_capture import TaskLogCapture
from app.web.services.automation_task_store import automation_task_store

logger = get_logger(__name__)
CLEANUP_TASK_NAME = "app.scheduler.tasks.automation.cleanup_task_logs"


async def _dispatch_due_tasks() -> dict[str, int]:
    tasks = await automation_task_store.claim_due_recurring_tasks()
    dispatched = 0
    for task in tasks:
        task_id = str(task["id"])
        run_id = str(task["current_run_id"])
        try:
            dispatch_run(task)
            await automation_task_store.append_task_log(
                task_id, "info", "周期调度已提交到 Celery Worker", run_id=run_id,
            )
            dispatched += 1
        except Exception:
            await automation_task_store.update_task(
                task_id, {"status": "failed", "throughput": 0},
                expected_run_id=run_id,
                expected_celery_task_id=str(task["celery_task_id"]),
            )
            logger.exception("Failed to dispatch automation task %s", task_id)
    return {"claimed": len(tasks), "dispatched": dispatched}


@app.task(name="app.scheduler.tasks.automation.dispatch_due")
def dispatch_due() -> dict[str, int]:
    return run_async(_dispatch_due_tasks())


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
    except Exception:
        logger.exception("Task log cleanup failed")
        await automation_task_store.update_task(
            task_id, {"status": "failed"},
            expected_run_id=run_id, expected_celery_task_id=delivery_id,
        )
        raise
    finally:
        await capture.stop()


@app.task(bind=True, name=CLEANUP_TASK_NAME)
def cleanup_task_logs(self: Any, task_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a claimed cleanup task and persist its own batch and console logs."""
    return run_async(_cleanup_task_logs(task_id, str(self.request.id)))
