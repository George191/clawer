"""站点适配器框架 — 解耦站点特定逻辑。

每个 SiteAdapter 封装一类站点的特殊行为（如 Google Patents 的 gen_204 信令、
USPTO 的 CAPTCHA 处理等），使 SpiderEngine 保持通用性。

扩展新站点只需：
1. 创建 app/adapters/<site_name>.py，继承 BaseSiteAdapter
2. 在 ADAPTER_REGISTRY 中注册
3. 在 YAML 模板中设置 adapter: <site_name>
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from app.downloader.http_client import HttpClient

logger = logging.getLogger(__name__)

# ── 全局注册表 ──────────────────────────────────────────────────────────

_ADAPTER_REGISTRY: dict[str, type[BaseSiteAdapter]] = {}
_adapters_loaded: bool = False


def _ensure_adapters_loaded() -> None:
    """扫描 app/adapters/ 目录，导入所有子模块以触发 @register_adapter 注册。"""
    global _adapters_loaded
    if _adapters_loaded:
        return
    _adapters_loaded = True
    package_path = __path__  # type: ignore[name-defined]
    for _, module_name, _ in pkgutil.iter_modules(package_path):
        try:
            importlib.import_module(f"app.adapters.{module_name}")
        except Exception as e:
            logger.debug("Skip adapter module '%s': %s", module_name, e)


def register_adapter(name: str):
    """装饰器：注册站点适配器。"""
    def decorator(cls: type[BaseSiteAdapter]):
        _ADAPTER_REGISTRY[name] = cls
        return cls
    return decorator


def get_adapter(
    name: str,
    base_url: str,
    http_client: HttpClient | None = None,
    **kwargs: Any,
) -> BaseSiteAdapter:
    """根据名称获取适配器实例。"""
    _ensure_adapters_loaded()
    cls = _ADAPTER_REGISTRY.get(name)
    if cls is None:
        return GenericAdapter(base_url, http_client, **kwargs)
    return cls(base_url, http_client, **kwargs)


def get_adapter_class(name: str | None) -> type[BaseSiteAdapter]:
    """根据名称获取适配器类（不实例化）。"""
    _ensure_adapters_loaded()
    cls = _ADAPTER_REGISTRY.get(name)
    return cls if cls is not None else GenericAdapter


# ── 基类 ────────────────────────────────────────────────────────────────

class BaseSiteAdapter:
    """站点适配器基类。

    子类可以覆盖以下钩子方法来注入站点特定行为：
    - on_before_crawl:    采集开始前（初始化会话等）
    - on_before_page:     请求每页数据前（发送信令事件）
    - on_after_page:      每页数据返回后（解析特殊字段等）
    - on_page_advance:    翻页状态推进
    - on_request_headers: 注入额外请求头
    - on_error:           处理站点特有错误（如验证码、限流等）
    """

    adapter_name: str = "generic"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = http_client or HttpClient()

    async def on_before_crawl(self, template: Any) -> None:
        """采集开始前。子类覆盖。"""
        pass

    async def on_before_page(self, page: int, is_first: bool) -> None:
        """请求每页数据前。子类覆盖。"""
        pass

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """每页数据返回后。子类覆盖。"""
        return records

    def on_page_advance(self) -> None:
        """翻页状态推进。子类覆盖。"""
        pass

    def on_request_headers(self, page: int) -> dict[str, str]:
        """注入额外请求头。子类覆盖。"""
        return {}

    async def on_error(self, error: Exception, page: int, attempt: int) -> str | None:
        """处理站点特有错误。

        返回:
            None → 继续默认重试逻辑
            "skip" → 跳过当前页
            "abort" → 终止采集
            "reset_session" → 重置会话后重试
        """
        return None

    async def close(self) -> None:
        pass

    # ── 静态工具方法 ────────────────────────────────────────────────────

    @classmethod
    def build_batch_param_value(cls, batch_data: list[str], param_name: str) -> str:
        """将批次数据组装为最终参数值。子类可覆盖以适配站点特定拼接格式。

        基类默认行为：返回第一条数据（适用于 batch_size=1 的通用场景）。
        子类可覆盖实现自定义拼接，如 Google Patents 的 +OR+ 语法。

        Args:
            batch_data: 批次数据列表（至少 1 条）
            param_name: 参数名称

        Returns:
            拼接后的参数值

        示例:
            基类默认:       batch_data[0]             → "10"
            Google Patents:  "+OR+".join(batch_data)  → "US-123+OR+US-456"
        """
        if not batch_data:
            return ""
        return batch_data[0]


# ── 通用适配器（默认） ──────────────────────────────────────────────────
class GenericAdapter(BaseSiteAdapter):
    """通用适配器：无特殊行为，直接使用默认逻辑。"""

    adapter_name = "generic"
