"""WordPress 新闻站通用适配器。

放置 WordPress REST API 共有逻辑，非 WP 站点不受影响。
"""

from __future__ import annotations

from typing import Any

from app.adapters.utils.news import NewsBaseAdapter
from app.logger import get_adapter_logger

logger = get_adapter_logger(__name__, "wp_base")


class WordPressBaseAdapter(NewsBaseAdapter):
    """WordPress 站点通用适配器。

    子类应覆盖:
    - site_domain: 站点主域名
    - on_request_headers(): 站点特定请求头
    """

    adapter_name = "wp_base"

    @staticmethod
    def _error_payload(error: Exception) -> dict[str, Any] | None:
        response = getattr(error, "response", None)
        if response is None:
            return None
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _is_pagination_end_error(cls, error: Exception) -> bool:
        response = getattr(error, "response", None)
        status_code = getattr(error, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)
        if status_code != 400:
            return False

        payload = cls._error_payload(error)
        if payload is not None:
            code = payload.get("code")
            return code == "rest_post_invalid_page_number"

        error_str = str(error)
        return (
            "rest_post_invalid_page_number" in error_str
        )

    async def on_error(self, error: Exception, page: int, attempt: int) -> str | None:
        """WordPress 站点错误处理。

        WP REST API 翻页超出总数时返回 400 rest_post_invalid_page_number，
        终止翻页而非无限重试。
        """
        if self._is_pagination_end_error(error):
            logger.info(
                "Page %d reached the end of WordPress pagination; stopping",
                page,
            )
            return "stop"
        return await super().on_error(error, page, attempt)
