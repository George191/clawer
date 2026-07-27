"""Execute an AI Collect workspace template through the existing SpiderEngine."""

from __future__ import annotations

import asyncio
from typing import Any

from app.engine.spider_engine import SpiderEngine
from app.engine.template_loader import TemplateLoader
from app.scheduler.celery_app import app
from app.web.services.ai_collect_store import ai_collect_store


class WorkspaceTaskStopped(Exception):
    """Raised when the operator cancels a running workspace task."""


async def _wait_for_task_control(workspace_task_id: str, page: int) -> None:
    """Pause between crawl pages and stop promptly after a cancellation request."""
    while True:
        task = await ai_collect_store.get_task(workspace_task_id)
        if task is None or task.get("control_state") == "canceled":
            raise WorkspaceTaskStopped("Workspace task was canceled")
        if task.get("status") == "running":
            await ai_collect_store.update_task(
                workspace_task_id,
                {"progress": min(max(page, 1), 99)},
            )
            return
        if task.get("status") != "paused":
            raise WorkspaceTaskStopped("Workspace task is no longer runnable")
        await asyncio.sleep(1)


async def _crawl_workspace_template(
    workspace_task_id: str,
    template_name: str,
    parameters: dict[str, str],
) -> dict[str, Any]:
    engine = SpiderEngine()
    try:
        template = TemplateLoader().load(template_name, param_values=parameters)
        result = await engine.crawl_from_page(
            template,
            None,
            progress_callback=lambda page: _wait_for_task_control(workspace_task_id, page),
        )
        summary = result.to_dict()
        if result.success:
            await ai_collect_store.update_task(
                workspace_task_id,
                {"status": "completed", "progress": 100, "records": result.saved_records, "throughput": 0},
            )
        else:
            await ai_collect_store.update_task(
                workspace_task_id,
                {"status": "failed", "records": result.saved_records, "throughput": 0},
            )
        return summary
    except Exception:
        await ai_collect_store.update_task(
            workspace_task_id,
            {"status": "failed", "throughput": 0},
        )
        raise
    finally:
        await engine.close()


@app.task(name="app.scheduler.tasks.workspace.crawl_template")
def crawl_workspace_template(
    workspace_task_id: str,
    template_name: str,
    parameters: dict[str, str],
) -> dict[str, Any]:
    """Run one persisted workspace task and mirror its final state to PostgreSQL."""
    return asyncio.run(_crawl_workspace_template(workspace_task_id, template_name, parameters))
