"""Dispatch durable batch sync jobs on the existing scheduler queue."""

import os
from uuid import UUID

from app.logger import get_logger
from app.scheduler.celery_app import app, run_async

logger = get_logger(__name__)
EXECUTE_TASK = "app.scheduler.tasks.batch_syncs.execute_scheduled_batch_sync"


def batch_sync_scheduler_worker_enabled() -> bool:
    truthy = {"1", "true", "on", "yes"}
    return (
        os.environ.get("ENVIRONMENT") == "local"
        and os.environ.get("BATCH_SYNC_SANDBOX_ENABLED", "").strip().lower() in truthy
        and os.environ.get("BATCH_SYNC_SANDBOX_SCHEDULER_ENABLED", "").strip().lower() in truthy
    )


async def dispatch_batch_sync_schedule_jobs() -> dict[str, int] | None:
    if not batch_sync_scheduler_worker_enabled():
        return None

    from app.web.services.batch_sync_schedules import list_due_batch_sync_jobs, record_batch_sync_job_dispatched

    jobs = await list_due_batch_sync_jobs()
    dispatched = 0
    for job_id in jobs:
        try:
            app.send_task(EXECUTE_TASK, args=[str(job_id)], task_id=str(job_id), queue="scheduler", retry=False)
        except Exception:
            # Preserve the DB reservation. On ambiguous publish the worker may
            # already have the message; on loss the next tick can lease it again.
            logger.exception("Could not publish scheduled batch sync job %s", job_id)
            continue
        dispatched += 1
        try:
            await record_batch_sync_job_dispatched(job_id)
        except Exception:
            logger.exception("Could not record dispatch of scheduled batch sync job %s", job_id)
    return {"reserved": len(jobs), "dispatched": dispatched}


@app.task(name=EXECUTE_TASK, acks_late=True, reject_on_worker_lost=True)
def execute_scheduled_batch_sync(job_id: str) -> str:
    if not batch_sync_scheduler_worker_enabled():
        return "disabled"

    from app.web.services.batch_sync_schedules import execute_scheduled_batch_sync_job

    return run_async(execute_scheduled_batch_sync_job(UUID(job_id)))
