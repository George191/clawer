"""Dispatch durable transfer jobs through the existing Celery scheduler queue."""

import os
from uuid import UUID

from app.logger import get_logger
from app.scheduler.celery_app import app, run_async

logger = get_logger(__name__)
EXECUTE_TASK = "app.scheduler.tasks.transfers.execute_scheduled_transfer"


def transfer_scheduler_worker_enabled() -> bool:
    """Require explicit local opt-in before importing the web service on a worker."""
    truthy = {"1", "true", "yes", "on"}
    return (
        os.environ.get("ENVIRONMENT") == "local"
        and os.environ.get("TRANSFER_SANDBOX_ENABLED", "").strip().lower() in truthy
        and os.environ.get("TRANSFER_SANDBOX_SCHEDULER_ENABLED", "").strip().lower() in truthy
    )


async def dispatch_transfer_schedule_jobs() -> dict[str, int] | None:
    if not transfer_scheduler_worker_enabled():
        return None

    from app.web.services.transfer_schedules import list_due_dispatch_jobs, record_transfer_job_dispatched

    jobs = await list_due_dispatch_jobs()
    dispatched = 0
    for job_id in jobs:
        try:
            app.send_task(
                EXECUTE_TASK, args=[str(job_id)], task_id=str(job_id), queue="scheduler",
                retry=False,
            )
        except Exception:
            # Do not mark failure or discard the job: the DB reservation expires,
            # and even a broker failure may actually have accepted the message.
            logger.exception("Could not publish scheduled sandbox transfer job %s", job_id)
            continue
        dispatched += 1
        try:
            await record_transfer_job_dispatched(job_id)
        except Exception:
            logger.exception("Could not record dispatch of scheduled sandbox transfer job %s", job_id)
    return {"reserved": len(jobs), "dispatched": dispatched}


@app.task(name=EXECUTE_TASK, acks_late=True, reject_on_worker_lost=True)
def execute_scheduled_transfer(job_id: str) -> str:
    if not transfer_scheduler_worker_enabled():
        return "disabled"

    from app.web.services.transfer_schedules import execute_scheduled_transfer_job

    return run_async(execute_scheduled_transfer_job(UUID(job_id)))
