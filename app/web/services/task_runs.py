import asyncio
from uuid import uuid4

from fastapi import HTTPException

from app.web.services.ai_collect_store import ai_collect_store


def dispatch_run(task: dict) -> None:
    from app.scheduler.celery_app import app

    app.send_task(
        "app.scheduler.tasks.workspace.crawl_template",
        args=[str(task["id"]), task["template_name"], task.get("parameters") or {}],
        task_id=task["celery_task_id"],
        retry=False,
    )


async def run_action(task_id: str, action: str) -> dict:
    current = await ai_collect_store.get_task_control(task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if current.get("control_state") == "canceled":
        raise HTTPException(status_code=409, detail="任务已取消")

    if action in {"start", "restart", "resume"}:
        delivery_id = str(uuid4())
        if action == "start":
            task = await ai_collect_store.start_task(task_id, delivery_id)
        elif action == "restart":
            task = await ai_collect_store.restart_task(task_id, delivery_id)
        else:
            task = await ai_collect_store.set_active_task_status(task_id, "paused", "running", delivery_id)
        if task is not None:
            try:
                await asyncio.to_thread(dispatch_run, task)
            except Exception as exc:
                await ai_collect_store.set_active_task_status(
                    task_id, "running", "failed", expected_celery_task_id=delivery_id,
                )
                raise HTTPException(status_code=503, detail="提交任务失败，请检查 Celery 队列后重试") from exc
            task.pop("previous_celery_task_id", None)
    elif action == "pause":
        task = await ai_collect_store.set_active_task_status(task_id, "running", "paused")
    elif action == "cancel":
        task = await ai_collect_store.cancel_task(task_id)
    else:
        transitions = {
            "start_download": ("download", "paused", "running"),
            "pause_download": ("download", "running", "paused"),
            "start_sync": ("sync", "paused", "running"),
            "pause_sync": ("sync", "running", "paused"),
            "cancel_sync": ("sync", current.get("sync_state"), "canceled"),
        }
        if action not in transitions:
            raise HTTPException(status_code=422, detail="不支持的任务操作")
        task = await ai_collect_store.transition_task_stage_state(task_id, *transitions[action])
    if task is None:
        raise HTTPException(status_code=409, detail="任务状态不允许此操作，请刷新后重试")
    return task


async def delete_run(task_id: str) -> dict[str, bool]:
    if await ai_collect_store.get_task_control(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await ai_collect_store.delete_task(task_id):
        raise HTTPException(status_code=409, detail="请先取消运行中或已暂停的任务")
    return {"deleted": True}
