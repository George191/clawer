"""任务执行基类。

所有任务继承 BaseTask，实现 execute 方法即可。
handler 通过 get_task(name) 获取任务实例并执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskResult:
    data: str = ""
    data_type: str = "text"


class BaseTask:
    name: str = ""

    async def execute(self, **kwargs) -> TaskResult:
        raise NotImplementedError(f"{self.__class__.__name__}.execute() not implemented")