"""任务注册中心。

所有任务通过 register_task 注册，调度器通过 get_tasks 获取 TaskRunner 批量执行。

架构：
    调度者 (ts_task handler) → TaskRunner → 任务器 (PdfToMarkdownTask) → 功能函数 (_download/_convert)
    调度者只管取任务 + 写 DB + emit，不接触任何任务依赖（如 MinIO）

扩展新任务：
    1. 在 tasks/ 下新建模块（如 tasks/translate.py）
    2. 继承 BaseTask 实现 execute(message=message) → TaskResult
    3. 在模块末尾调用 register_task(TranslateTask)
    4. 调度器中 get_tasks("pdf_to_markdown", "translate") 即可同时执行
"""

from __future__ import annotations

import logging
from typing import Any, Type

from app.etl.tasks.base import BaseTask, TaskResult

logger = logging.getLogger(__name__)

_TASK_REGISTRY: dict[str, BaseTask] = {}


class TaskRunner:
    def __init__(self, tasks: list[BaseTask]) -> None:
        self._tasks = tasks

    async def execute(self, *, message: dict[str, Any]) -> dict[str, TaskResult]:
        results: dict[str, TaskResult] = {}
        for task in self._tasks:
            logger.debug("TaskRunner: executing %s", task.name)
            results[task.name] = await task.execute(message=message)
        return results


def register_task(task_cls: Type[BaseTask]) -> None:
    instance = task_cls()
    _TASK_REGISTRY[instance.name] = instance
    logger.info("Task registered: %s", instance.name)


def _get_task(name: str) -> BaseTask:
    task = _TASK_REGISTRY.get(name)
    if task is None:
        raise KeyError(f"Task not found: {name}")
    return task


def get_tasks(*names: str) -> TaskRunner:
    return TaskRunner([_get_task(name) for name in names])


from app.etl.tasks import pdf_to_markdown  # noqa: E402, F401