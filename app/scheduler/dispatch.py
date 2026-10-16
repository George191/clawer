"""Dispatch shared automation tasks to their execution handlers."""

from typing import Any


def is_recurring(schedule: dict[str, Any]) -> bool:
    mode = str(schedule.get("mode") or "once")
    if mode == "recurring":
        return schedule.get("recurring_mode") in {"daily", "interval"}
    return mode in {"daily", "interval"}


def dispatch_run(task: dict) -> None:
    from app.scheduler.celery_app import app

    if task.get("task_type") == "celery":
        app.send_task(
            task["task_path"],
            args=[str(task["id"]), task.get("parameters") or {}],
            task_id=str(task["celery_task_id"]),
            queue="scheduler",
            retry=False,
        )
    else:
        app.send_task(
            "app.scheduler.tasks.workspace.crawl_template",
            args=[str(task["id"]), task["template_name"], task.get("parameters") or {}],
            task_id=str(task["celery_task_id"]),
            retry=False,
        )
