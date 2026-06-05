"""优先级队列 - Priority Queue for Crawl Scheduling.

支持按模板优先级排序采集任务，重要模板优先处理。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.models.template import SiteTemplate

logger = logging.getLogger(__name__)

# 优先级常量
PRIORITY_CRITICAL = 0   # 最高优先级：实时需求
PRIORITY_HIGH = 10      # 高优先级：重要模板
PRIORITY_NORMAL = 50    # 普通优先级（默认）
PRIORITY_LOW = 100      # 低优先级：非关键数据
PRIORITY_BACKGROUND = 200  # 后台优先级：闲时采集


@dataclass(order=True)
class CrawlTask:
    """采集任务包装，用于优先级队列。

    排序规则：priority 升序（越小越优先），
    若优先级相同则按 creation_time 升序（先到先服务）。
    """

    priority: int
    creation_time: float = field(compare=True)
    template: Any = field(compare=False)
    template_name: str = field(compare=False)
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    def __repr__(self) -> str:
        return (
            f"CrawlTask(priority={self.priority}, "
            f"template={self.template_name}, "
            f"extra={list(self.extra.keys())})"
        )


class CrawlPriorityQueue:
    """采集任务优先级队列。

    底层使用 asyncio.PriorityQueue，自动按优先级排序。
    """

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._count: int = 0

    @property
    def is_empty(self) -> bool:
        return self._queue.empty()

    @property
    def size(self) -> int:
        return self._count

    async def enqueue(
        self,
        template: SiteTemplate,
        priority: Optional[int] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """将模板加入优先级队列。

        Args:
            template: 站点模板
            priority: 优先级（None 时从模板推断）
            extra: 额外参数
        """
        import time

        if priority is None:
            priority = self._template_priority(template)

        task = CrawlTask(
            priority=priority,
            creation_time=time.monotonic(),
            template=template,
            template_name=template.name,
            extra=extra or {},
        )

        await self._queue.put(task)
        self._count += 1

        logger.debug(
            "Enqueued template: %s (priority=%d, queue_size=%d)",
            template.name,
            priority,
            self._count,
        )

    async def dequeue(self) -> CrawlTask:
        """出队下一个任务（按优先级）。"""
        task = await self._queue.get()
        self._count -= 1

        logger.info(
            "Dequeued template: %s (priority=%d, remaining=%d)",
            task.template_name,
            task.priority,
            self._count,
        )
        return task

    async def enqueue_batch(
        self,
        templates: list[SiteTemplate],
        default_priority: int = PRIORITY_NORMAL,
    ) -> None:
        """批量将模板加入队列。

        Args:
            templates: 模板列表
            default_priority: 默认优先级
        """
        import time

        for tpl in templates:
            priority = self._template_priority(tpl) or default_priority
            task = CrawlTask(
                priority=priority,
                creation_time=time.monotonic(),
                template=tpl,
                template_name=tpl.name,
            )
            await self._queue.put(task)

        self._count += len(templates)
        logger.info("Enqueued %d templates in batch", len(templates))

    @staticmethod
    def _template_priority(template: SiteTemplate) -> int:
        """从模板元数据推断优先级。

        优先级来源（按顺序）：
        1. 模板 extra 中的 priority 字段
        2. 模板 data_type: patent → NORMAL, contract → HIGH
        3. 默认 NORMAL
        """
        try:
            extra = template.model_extra or {}
            if "priority" in extra:
                return int(extra["priority"])
        except (AttributeError, TypeError):
            pass

        # 根据数据类型推断优先级
        if template.data_type in ("contract", "legal"):
            return PRIORITY_HIGH
        elif template.data_type in ("background",):
            return PRIORITY_BACKGROUND

        return PRIORITY_NORMAL


# 全局单例
_queue: Optional[CrawlPriorityQueue] = None


def get_priority_queue() -> CrawlPriorityQueue:
    """获取全局优先级队列单例。"""
    global _queue
    if _queue is None:
        _queue = CrawlPriorityQueue()
    return _queue
