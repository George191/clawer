from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from app.web.services.ai_collect_store import ai_collect_store

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _empty_series() -> list[dict[str, object]]:
    now = datetime.now(timezone.utc)
    return [
        {"ts": (now - timedelta(minutes=5 - index)).isoformat(), "v": 0}
        for index in range(6)
    ]


@router.get("/metrics")
async def metrics() -> dict:
    tasks = await ai_collect_store.list_tasks()
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
        "etl_throughput": {"current": 0, "history": _empty_series()},
        "kafka_lag": {"total": 0, "by_layer": {}},
        "data_volume": {"total": sum(int(task.get("records") or 0) for task in tasks), "daily_increment": 0},
        "pipeline_nodes": [],
        "layer_throughput_history": [],
        "task_status_dist": [
            {"name": status, "value": count}
            for status, count in counts.items()
            if count
        ],
        "error_rate_history": _empty_series(),
        "error_threshold": 5,
        "kafka_lag_history": _empty_series(),
    }


@router.get("/alerts")
async def alerts() -> list[dict]:
    tasks = await ai_collect_store.list_tasks()
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
