import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.web.api.dependencies.common import get_current_user
from app.web.services.ai_collect_store import ai_collect_store

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(get_current_user)])


async def _tasks() -> list[dict]:
    try:
        return await asyncio.wait_for(ai_collect_store.list_tasks(), timeout=5)
    except Exception as exc:
        raise HTTPException(503, "任务指标暂时无法读取") from exc


@router.get("/metrics")
async def metrics() -> dict:
    tasks = await _tasks()
    counts = {status: 0 for status in ("queued", "running", "completed", "failed", "paused")}
    for task in tasks:
        status = str(task.get("status") or "queued")
        counts[status] = counts.get(status, 0) + 1
    return {
        "tasks": {
            "total": len(tasks),
            "running": counts["running"],
            "completed": counts["completed"],
            "failed": counts["failed"],
        },
        "etl_throughput": {"current": None, "history": []},
        "kafka_lag": {"total": None, "by_layer": {}},
        "data_volume": {"total": sum(int(task.get("records") or 0) for task in tasks), "daily_increment": None},
        "pipeline_nodes": [],
        "layer_throughput_history": [],
        "task_status_dist": [
            {"name": status, "value": count}
            for status, count in counts.items()
            if count
        ],
        "error_rate_history": [],
        "error_threshold": None,
        "kafka_lag_history": [],
        "unavailable_metrics": ["ETL 吞吐", "Kafka 积压", "每日增量", "错误率趋势"],
    }


@router.get("/alerts")
async def alerts() -> list[dict]:
    tasks = await _tasks()
    return [
        {
            "id": str(task["id"]),
            "level": "critical",
            "source": "workspace-task",
            "message": f"任务 {task.get('name') or task.get('template_name')} 执行失败",
            "time": task.get("updated_at"),
            "status": "active",
        }
        for task in tasks
        if task.get("status") == "failed"
    ]
