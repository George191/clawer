import asyncio
from uuid import uuid4

from fastapi import HTTPException

from app.scheduler.dispatch import dispatch_run
from app.web.services.automation_task_store import automation_task_store


async def run_action(task_id: str, action: str) -> dict:
    current = await automation_task_store.get_task_control(task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if current.get("control_state") == "canceled":
        raise HTTPException(status_code=409, detail="任务已取消")
    if current.get("task_type") == "celery" and action in {
        "pause", "resume", "start_download", "pause_download", "start_sync", "pause_sync", "cancel_sync",
    }:
        raise HTTPException(status_code=409, detail="此任务不支持暂停、续跑或采集阶段操作")

    if action in {"start", "restart", "resume"}:
        delivery_id = str(uuid4())
        if action == "start":
            task = await automation_task_store.start_task(task_id, delivery_id)
        elif action == "restart":
            task = await automation_task_store.restart_task(task_id, delivery_id)
        else:
            task = await automation_task_store.set_active_task_status(task_id, "paused", "running", delivery_id)
        if task is not None:
            try:
                await asyncio.to_thread(dispatch_run, task)
            except Exception as exc:
                await automation_task_store.set_active_task_status(
                    task_id, "running", "failed", expected_celery_task_id=delivery_id,
                )
                raise HTTPException(status_code=503, detail="提交任务失败，请检查 Celery 队列后重试") from exc
            task.pop("previous_celery_task_id", None)
    elif action == "pause":
        task = await automation_task_store.set_active_task_status(task_id, "running", "paused")
    elif action == "cancel":
        task = await automation_task_store.cancel_task(task_id)
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
        task = await automation_task_store.transition_task_stage_state(task_id, *transitions[action])
    if task is None:
        raise HTTPException(status_code=409, detail="任务状态不允许此操作，请刷新后重试")
    return task


async def delete_run(task_id: str) -> dict[str, bool]:
    current = await automation_task_store.get_task_control(task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if current.get("status") == "paused":
        # 暂停的任务没有活跃执行，删除前自动取消其挂起的运行
        if await automation_task_store.cancel_task(task_id) is None:
            raise HTTPException(status_code=409, detail="任务状态不允许此操作，请刷新后重试")
    if not await automation_task_store.delete_task(task_id):
        raise HTTPException(status_code=409, detail="请先取消运行中的任务")
    return {"deleted": True}
