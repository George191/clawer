"""Jinja2 模板渲染引擎 - Enhanced Template Rendering.

替代简单字符串替换，支持条件字段和预处理钩子。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional, Awaitable

from app.config.settings import settings

logger = logging.getLogger(__name__)

# 预处理钩子类型
PreHook = Callable[..., Awaitable[dict[str, Any]]]


class Jinja2Renderer:
    """Jinja2 模板渲染器。

    使用 Jinja2 替代简单的 {placeholder} 替换，提供：
    - 条件渲染（if/else/for）
    - 过滤器（upper, lower, default 等）
    - 自定义过滤器
    - 继承和宏支持
    """

    def __init__(self) -> None:
        self._env = None
        self._initialized: bool = False

    @property
    def enabled(self) -> bool:
        return settings.jinja2_enabled

    def _ensure_init(self) -> None:
        """延迟初始化 Jinja2 环境。"""
        if self._initialized:
            return

        try:
            from jinja2 import Environment, BaseLoader

            self._env = Environment(
                loader=BaseLoader(),
                autoescape=False,
                trim_blocks=True,
                lstrip_blocks=True,
                enable_async=True,
            )

            # 注册自定义过滤器
            self._env.filters["url_encode"] = self._url_encode
            self._env.filters["default"] = lambda v, d="": v if v else d
            self._env.filters["strip"] = lambda v: str(v).strip() if v else ""
            self._env.filters["int"] = lambda v: int(str(v).replace(",", "")) if v else 0
            self._env.filters["json_dumps"] = lambda v: json.dumps(v, ensure_ascii=False)

            self._initialized = True
            logger.info("Jinja2 renderer initialized")

        except ImportError:
            logger.warning("Jinja2 not installed, falling back to simple template. Run: pip install jinja2")
            self._env = None
            self._initialized = True

    def render(self, template_str: str, context: dict[str, Any]) -> str:
        """渲染 Jinja2 模板。

        如果 Jinja2 未启用或未安装，回退到简单的 {key} 替换。

        Args:
            template_str: 模板字符串（Jinja2 语法）
            context: 渲染上下文

        Returns:
            渲染后的字符串
        """
        if not self.enabled or self._env is None:
            return self._simple_replace(template_str, context)

        self._ensure_init()

        if self._env is None:
            return self._simple_replace(template_str, context)

        try:
            template = self._env.from_string(template_str)
            result = template.render(**context)
            return result
        except Exception as e:
            logger.error("Jinja2 render error for template: %s", e)
            logger.debug("Template: %s", template_str[:200])
            return self._simple_replace(template_str, context)

    @staticmethod
    def _simple_replace(text: str, context: dict[str, Any]) -> str:
        """简单的 {key} → value 替换（回退方案）。"""
        result = text
        for key, value in context.items():
            placeholder = "{" + key + "}"
            if placeholder in result:
                result = result.replace(placeholder, str(value))
        return result

    @staticmethod
    def _url_encode(value: str) -> str:
        """URL 编码过滤器。"""
        from urllib.parse import quote_plus
        return quote_plus(str(value))

    def render_url(
        self,
        base_url: str,
        path_template: str,
        context: dict[str, Any],
    ) -> str:
        """渲染完整 URL。

        Args:
            base_url: 基础 URL
            path_template: 路径模板
            context: 渲染上下文

        Returns:
            完整 URL
        """
        path = self.render(path_template, context)
        return f"{base_url.rstrip('/')}{path}"

    def render_headers(
        self,
        headers: dict[str, str],
        context: dict[str, Any],
    ) -> dict[str, str]:
        """渲染请求头模板。

        请求头中每个值都可以使用 Jinja2 模板语法。
        """
        return {
            key: self.render(value, context)
            for key, value in headers.items()
        }


# ── 条件字段支持 ────────────────────────────────────────────────────────────

class ConditionalField:
    """条件字段包装器。

    支持：
    - if_exists: 仅当数据中存在该字段时才提取
    - if_value: 仅当字段值匹配某条件时才提取
    - if_missing_default: 字段缺失时的默认值
    - transform: 对提取值执行转换
    """

    def __init__(
        self,
        name: str,
        selector: str,
        if_exists: bool = False,
        if_value: Optional[str] = None,
        if_missing_default: Any = None,
        transform: Optional[Callable[[Any], Any]] = None,
        children_readers: Optional[list["ConditionalField"]] = None,
    ) -> None:
        self.name = name
        self.selector = selector
        self.if_exists = if_exists
        self.if_value = if_value
        self.if_missing_default = if_missing_default
        self.transform = transform
        self.children_readers = children_readers or []

    def should_extract(self, data: dict[str, Any]) -> bool:
        """检查是否应该提取此字段。"""
        if self.if_exists and self.name not in data:
            return False
        return True

    def extract_value(self, data: dict[str, Any]) -> Any:
        """提取字段值（带条件逻辑）。"""
        value = data.get(self.name, self.if_missing_default)

        if value is None and self.if_missing_default is not None:
            value = self.if_missing_default

        if self.if_value is not None and str(value) != self.if_value:
            return None

        if self.transform and value is not None:
            try:
                value = self.transform(value)
            except Exception as e:
                logger.warning("Transform failed for field %s: %s", self.name, e)

        return value


def apply_conditional_fields(
    record: dict[str, Any],
    conditional_fields: list[ConditionalField],
) -> dict[str, Any]:
    """应用条件字段提取。

    遍历条件字段列表，按条件提取数据。

    Args:
        record: 原始数据记录
        conditional_fields: 条件字段列表

    Returns:
        过滤后的数据字典
    """
    result: dict[str, Any] = {}

    for cf in conditional_fields:
        if not cf.should_extract(record):
            continue

        value = cf.extract_value(record)
        if value is not None:
            result[cf.name] = value
        elif cf.if_missing_default is not None:
            result[cf.name] = cf.if_missing_default
        # else: 跳过此字段（conditional skip）

        # 子字段提取（嵌套数据）
        if cf.children_readers and value and isinstance(value, dict):
            child_result = apply_conditional_fields(value, cf.children_readers)
            for child_key, child_val in child_result.items():
                result[f"{cf.name}.{child_key}"] = child_val

    return result


# ── 预处理钩子支持 ────────────────────────────────────────────────────────────

class PreHookManager:
    """预处理钩子管理器。

    支持在请求之前执行自定义异步钩子，例如：
    - 先请求登录页获取 CSRF token
    - 先获取临时 access_token
    - 先请求验证码识别

    钩子可以在模板 YAML 中定义，也可以在代码中注册。
    """

    def __init__(self) -> None:
        # {hook_name: hook_function}
        self._hooks: dict[str, PreHook] = {}

    @property
    def enabled(self) -> bool:
        return settings.pre_hooks_enabled

    def register(self, name: str, hook: PreHook) -> None:
        """注册一个预处理钩子。

        Args:
            name: 钩子名称（在模板中通过 pre_hooks 字段引用）
            hook: 异步钩子函数，接收任意参数，返回上下文字典
        """
        self._hooks[name] = hook
        logger.debug("Registered pre-hook: %s", name)

    def get(self, name: str) -> Optional[PreHook]:
        """获取注册的钩子函数。"""
        return self._hooks.get(name)

    async def execute(
        self,
        hook_names: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """按顺序执行一系列预处理钩子。

        每个钩子接收当前上下文，返回新的上下文（合并到原上下文）。

        Args:
            hook_names: 钩子名称列表（按执行顺序）
            context: 初始上下文

        Returns:
            合并后的上下文
        """
        if not self.enabled:
            return context

        result_context = dict(context)

        for name in hook_names:
            hook = self._hooks.get(name)
            if hook is None:
                logger.warning("Pre-hook %s not registered, skipping", name)
                continue

            try:
                logger.info("Executing pre-hook: %s", name)
                new_context = await hook(**result_context)
                if new_context:
                    result_context.update(new_context)
            except Exception as e:
                logger.error("Pre-hook %s failed: %s", name, e)

        return result_context


# 全局单例
_renderer: Optional[Jinja2Renderer] = None
_hook_manager: Optional[PreHookManager] = None


def get_jinja2_renderer() -> Jinja2Renderer:
    """获取全局 Jinja2 渲染器单例。"""
    global _renderer
    if _renderer is None:
        _renderer = Jinja2Renderer()
    return _renderer


def get_prehook_manager() -> PreHookManager:
    """获取全局预处理钩子管理器单例。"""
    global _hook_manager
    if _hook_manager is None:
        _hook_manager = PreHookManager()
    return _hook_manager
