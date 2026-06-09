"""站点适配器框架 — 解耦站点特定逻辑。

每个 SiteAdapter 封装一类站点的特殊行为（如 Google Patents 的 gen_204 信令、
USPTO 的 CAPTCHA 处理等），使 SpiderEngine 保持通用性。

扩展新站点只需：
1. 创建 app/adapters/<site_name>.py，继承 BaseSiteAdapter
2. 在 ADAPTER_REGISTRY 中注册
3. 在 YAML 模板中设置 adapter: <site_name>
"""

from __future__ import annotations

from typing import Any

from app.engine.browser_events import BrowserEventEmitter, PageSession
from app.downloader.http_client import HttpClient

# ── 全局注册表 ──────────────────────────────────────────────────────────

_ADAPTER_REGISTRY: dict[str, type[BaseSiteAdapter]] = {}


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
    cls = _ADAPTER_REGISTRY.get(name)
    if cls is None:
        return GenericAdapter(base_url, http_client, **kwargs)
    return cls(base_url, http_client, **kwargs)


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
        self._emitter = BrowserEventEmitter(
            base_url=self._base_url,
            http_client=self._client,
            site=self.adapter_name,
        )
        self._session = PageSession()

    @property
    def emitter(self) -> BrowserEventEmitter:
        return self._emitter

    @property
    def session(self) -> PageSession:
        return self._session

    async def on_before_crawl(self, template: Any) -> None:
        """采集开始前。子类覆盖。"""
        self._resolve_batch_param(template)

    def _resolve_batch_param(self, template: Any) -> None:
        """通用批量参数解析 — 将 _batch_data 填入模板 URL。

        处理 main.py 传入的 _batch_data 列表：
        - 单条 (batch_size=1): 直接取值，替换空占位
        - 多条: 子类应覆盖此方法实现自定义拼接

        工作原理：从 _param_values 找到空字符串的参数名，
        用 _batch_data 中的值替换 list_page 中的空占位。
        """
        batch_data = getattr(template, "_batch_data", None)
        if not batch_data or not isinstance(batch_data, list) or len(batch_data) == 0:
            return

        param_values = getattr(template, "_param_values", {}) or {}
        for param_name, param_val in param_values.items():
            if param_val == "":
                # 单条直接取值，多条由子类 adapter 处理
                value = batch_data[0] if len(batch_data) == 1 else "+OR+".join(batch_data)
                # 替换 URL 中的空占位: param_name=& 或 param_name=}
                if hasattr(template, "list_page") and template.list_page:
                    template.list_page = template.list_page.replace(
                        f"{param_name}=&", f"{param_name}={value}&"
                    ).replace(
                        f"{param_name}=}}", f"{param_name}={value}}}"
                    ).replace(
                        f"{param_name}=/", f"{param_name}={value}/"
                    )
                template._param_values[param_name] = value
                break

    async def on_before_page(self, page: int, is_first: bool) -> None:
        """请求每页数据前。子类覆盖。"""
        pass

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """每页数据返回后。子类覆盖。"""
        return records

    def on_page_advance(self) -> None:
        """翻页状态推进。子类覆盖。"""
        self._session.advance_page()

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
        await self._emitter.close()


# ── 通用适配器（默认） ──────────────────────────────────────────────────

class GenericAdapter(BaseSiteAdapter):
    """通用适配器：无特殊行为，直接使用默认逻辑。"""

    adapter_name = "generic"
