"""代理源适配器抽象基类 - Proxy Source Adapter Abstract Base.

定义统一的代理源适配器接口规范以及通用的 ProxyInfo 数据对象。
所有具体的代理源实现（API、文件、数据库等）必须继承此基类并实现其抽象方法。

设计原则：
- 适配器负责从特定来源获取原始代理数据，并将其转换为统一的 ProxyInfo 列表
- 适配器不关心代理池的调度、健康检查、故障处理等框架层逻辑
- 每个适配器自包含其特有的解析逻辑和配置
- ProxyInfo 作为数据传输对象在此定义，避免循环导入
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any


class ProxyInfo:
    """单个代理的运行时信息。

    Attributes:
        url: 代理 URL，格式如 http://host:port
        failures: 连续失败次数
        last_used: 上次使用时间戳
        last_check: 上次健康检查时间戳
        healthy: 当前是否健康
    """

    __slots__ = ("url", "failures", "last_used", "last_check", "healthy")

    def __init__(self, url: str) -> None:
        self.url: str = url
        self.failures: int = 0
        self.last_used: float = 0.0
        self.last_check: float = 0.0
        self.healthy: bool = True

    def __repr__(self) -> str:
        return f"ProxyInfo({self.url}, healthy={self.healthy}, failures={self.failures})"


# 异步代理加载回调类型
ProxyCallback = Callable[[list[ProxyInfo]], Awaitable[None]]


class ProxySourceAdapter(ABC):
    """代理源适配器抽象基类。

    所有代理源适配器必须实现以下方法：
    - name: 返回适配器唯一标识名称
    - fetch: 从代理源获取代理列表，返回 ProxyInfo 列表
    - validate_config: 验证适配器配置是否有效

    使用示例::

        class MyAPIAdapter(ProxySourceAdapter):
            @property
            def name(self) -> str:
                return "my_api"

            def validate_config(self) -> bool:
                return bool(self._config.get("url"))

            async def fetch(self) -> list[ProxyInfo]:
                # 实现具体的数据获取与解析逻辑
                ...
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """初始化适配器。

        Args:
            config: 适配器配置字典，由子类按需解析。
        """
        self._config = config or {}

    @property
    @abstractmethod
    def name(self) -> str:
        """适配器唯一名称，用于日志和配置标识。"""
        ...

    @abstractmethod
    def validate_config(self) -> bool:
        """验证适配器配置是否有效。

        Returns:
            True 如果配置有效，否则 False。
        """
        ...

    @abstractmethod
    async def fetch(self) -> list[ProxyInfo]:
        """从代理源获取代理列表。

        Returns:
            ProxyInfo 对象列表。如果获取失败，返回空列表。
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"