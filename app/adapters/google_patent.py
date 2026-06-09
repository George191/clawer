"""Google Patents 适配器 — 封装 gen_204 信令和页面事件序列。

核心逻辑
--------
Google Patents 的 XHR API 需要在翻页前发送 /gen_204 信令事件，
否则后续页返回空数据。本适配器封装了完整的事件序列：

1. 首页加载:  emit_num_per_page → emit_search → 请求第1页
2. 翻页:     emit_url_change → emit_page_change → 请求第N页
3. 错误恢复:  重置 PageSession，重新发送初始化事件

peid/eid 生命周期
-----------------
- peid (previous event id): 上一页的 eid
- eid (event id): 当前页的随机标识
- 翻页时: peid = 旧 eid, eid = 新随机值
- XHR 请求 URL 中的 peid 参数使用当前 eid（不是 peid 字段！）
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.engine.browser_events import PageSession

logger = logging.getLogger(__name__)


@register_adapter("google_patent")
class GooglePatentAdapter(BaseSiteAdapter):
    """Google Patents 站点适配器。"""

    adapter_name = "google_patent"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)

    async def on_before_crawl(self, template: Any) -> None:
        """采集开始前：由基类处理 _batch_data 拼接。

        BaseSiteAdapter._resolve_batch_param 已实现 "+OR+" 拼接，
        与 Google Patents API 的 OR 查询语法一致。
        """
        await super().on_before_crawl(template)

    async def on_before_page(self, page: int, is_first: bool) -> None:
        """请求每页数据前：发送翻页信令。

        事件序列（与真实浏览器对齐）：
        - 首页：已在 on_before_crawl 发送
        - 翻页：URL_CHANGE → PAGE_CHANGE
        """
        # if is_first:
        #     return

        # # 1. SPA pushState → URL 变化事件
        # await self._emitter.emit_url_change(self._session)
        # # 2. 翻页操作信令
        # await self._emitter.emit_page_change(self._session)
        # logger.debug(
        #     "[GooglePatentAdapter] Page %d events sent: peid=%s, eid=%s",
        #     page,
        #     self._session.peid[:20],
        #     self._session.eid[:20],
        # )
        ...

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """每页数据返回后：过滤相似文档。"""
        # Google Patents 返回的 is_similar_document=True 记录通常是噪音
        # filtered = [
        #     r for r in records
        #     if not r.get("is_similar_document", False)
        # ]
        # if len(filtered) < len(records):
        #     logger.info(
        #         "[GooglePatentAdapter] Filtered %d similar documents on page %d",
        #         len(records) - len(filtered),
        #         page,
        #     )
        # return filtered
        ...

    def on_page_advance(self) -> None:
        """翻页状态推进：peid 继承 eid，生成新 eid。"""
        # self._session.advance_page()
        # logger.debug(
        #     "[GooglePatentAdapter] Page advanced: new peid=%s, new eid=%s",
        #     self._session.peid[:20],
        #     self._session.eid[:20],
        # )
        ...

    def on_request_headers(self, page: int) -> dict[str, str]:
        """注入 Google Patents 特有请求头。"""
        return {
            "Accept": "application/json, text/plain, */*",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    async def on_error(self, error: Exception, page: int, attempt: int) -> str | None:
        """处理 Google Patents 特有错误。

        - 429 Too Many Requests → 重置会话重试
        - CAPTCHA → 终止采集（需人工介入）
        - 其他 → 默认重试
        """
        error_str = str(error)
        if "429" in error_str:
            logger.warning(
                "[GooglePatentAdapter] Rate limited on page %d, resetting session",
                page,
            )
            self._session = PageSession()
            return "reset_session"
        if "captcha" in error_str.lower():
            logger.error(
                "[GooglePatentAdapter] CAPTCHA detected on page %d, aborting",
                page,
            )
            return "abort"
        return None

    @staticmethod
    def build_batch_patent_param(ids: list[str]) -> str:
        """构建批量专利查询参数值。

        格式: (ID1)+OR+ID2+OR+ID3。多个 ID 用 +OR+ 拼接，
        用于 Google Patents 的 q= 参数中实现 OR 批量查询。

        示例::

            >>> GooglePatentAdapter.build_batch_patent_param(['US-123-A1', 'US-456-B2'])
            'US-123-A1+OR+US-456-B2'

        Args:
            ids: 专利公开编号列表。

        Returns:
            OR 拼接的参数字符串。
        """
        return "+OR+".join(ids)

    @staticmethod
    def get_batch_size() -> int:
        """返回 Google Patents 推荐的批量查询大小。

        Google Patents API 对单次查询 URL 长度有限制，一般建议 5 个 ID 为一批。

        Returns:
            推荐的批量查询数量。
        """
        return 5
