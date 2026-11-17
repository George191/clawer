"""Dispatch durable receipt-triggered transfers on the existing scheduler queue."""

import os
from uuid import UUID

from app.logger import get_logger
from app.scheduler.celery_app import app, run_async

logger = get_logger(__name__)
EXECUTE_TASK = "app.scheduler.tasks.transfer_upstream.execute_upstream_transfer"


def upstream_transfer_worker_enabled() -> bool:
    truthy = {"1", "true", "on", "yes"}
    return (
        os.environ.get("ENVIRONMENT") == "local"
        and os.environ.get("TRANSFER_SANDBOX_ENABLED", "").strip().lower() in truthy
        and os.environ.get("TRANSFER_SANDBOX_UPSTREAM_ENABLED", "").strip().lower() in truthy
    )


async def dispatch_upstream_transfer_jobs() -> dict[str, int] | None:
    if not upstream_transfer_worker_enabled():
        return None

    from app.web.services.transfer_upstream import list_due_upstream_transfer_jobs, record_upstream_transfer_job_dispatched

    jobs = await list_due_upstream_transfer_jobs()
    dispatched = 0
    for job_id in jobs:
        try:
            app.send_task(EXECUTE_TASK, args=[str(job_id)], task_id=str(job_id), queue="scheduler", retry=False)
        except Exception:
            # A timeout does not prove failure; the durable lease handles retry.
            logger.exception("Could not publish receipt-triggered transfer job %s", job_id)
            continue
        dispatched += 1
        try:
            await record_upstream_transfer_job_dispatched(job_id)
        except Exception:
            logger.exception("Could not record dispatch of receipt-triggered transfer job %s", job_id)
    return {"reserved": len(jobs), "dispatched": dispatched}


@app.task(name=EXECUTE_TASK, acks_late=True, reject_on_worker_lost=True)
def execute_upstream_transfer(job_id: str) -> str:
    if not upstream_transfer_worker_enabled():
        return "disabled"

    from app.web.services.transfer_upstream import execute_upstream_transfer_job

    return run_async(execute_upstream_transfer_job(UUID(job_id)))
