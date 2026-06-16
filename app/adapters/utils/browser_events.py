"""浏览器事件发射器 — 封装 gen_204 及页面行为事件。

Google Patents 专用适配模块，模拟浏览器行为（发送无状态信令事件）
以绕过反爬检测。将事件发送和 peid/eid 生命周期管理与 SpiderEngine
解耦。

注意：此模块仅由 GooglePatentAdapter 引入，不应出现在 BaseSiteAdapter 或
其他通用组件中。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import settings
from app.downloader.http_client import HttpClient

logger = logging.getLogger(__name__)

# ── 事件模板（Google Patents 规范）───────────────────────────────────

EVENT_NUM_PER_PAGE: dict[str, Any] = {
    "view": {"page": "resultlist"},
    "action": {"interaction_type": "RESULTLIST_NUMPERPAGE_MENU"},
}

EVENT_PAGE_CHANGE: dict[str, Any] = {
    "view": {"page": "resultlist"},
    "action": {"interaction_type": "RESULTLIST_PAGE_CHANGE"},
}

EVENT_URL_CHANGE: dict[str, Any] = {
    "view": {"page": "resultlist"},
    "action": {"interaction_type": "URL_CHANGE"},
}

EVENT_SEARCH: dict[str, Any] = {
    "view": {"page": "resultlist"},
    "action": {"interaction_type": "SEARCH"},
}

EVENT_SORT: dict[str, Any] = {
    "view": {"page": "resultlist"},
    "action": {"interaction_type": "RESULTLIST_SORT"},
}


@dataclass
class PageSession:
    """单次列表页浏览的会话状态，管理 peid/eid 生命周期。"""

    peid: str = field(default_factory=lambda: BrowserEventEmitter._gen_id())
    eid: str = field(default_factory=lambda: BrowserEventEmitter._gen_id())
    page_num: int = 1
    start_ts: float = field(default_factory=time.time)

    def advance_page(self) -> None:
        """翻页时推进 eid，peid 继承上一次的 eid。"""
        self.peid = self.eid
        self.eid = BrowserEventEmitter._gen_id()
        self.page_num += 1

    def to_event_context(self) -> dict[str, Any]:
        return {
            "peid": self.peid,
            "eid": self.eid,
            "page": self.page_num,
            "timestamp": int(time.time() * 1000),
        }


class BrowserEventEmitter:
    """向目标站点发送浏览器行为模拟事件（如 Google Patents 的 gen_204）。

    设计要点
    --------
    - gen_204 返回 HTTP 204 No Content，无响应体，使用「即发即弃」策略
    - 事件 payload 注入 peid/eid/timestamp，与 Google 官方行为对齐
    - 自动携带 Referer / Accept-* 等浏览器常规请求头
    - 支持限流（每次事件间隔）以避免触发异常流量检测
    """

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        site: str = "google_patent",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = http_client or HttpClient()
        self._site = site
        self._session = PageSession()
        self._last_event_ts: float = 0.0
        # gen_204 最小间隔（秒），模拟真人操作节奏
        self._min_interval: float = 0.1

    # ── Public API ───────────────────────────────────────────────────────

    @property
    def session(self) -> PageSession:
        return self._session

    async def emit(
        self,
        event: dict[str, Any],
        session: PageSession | None = None,
    ) -> None:
        """发送单条事件（即发即弃，不阻塞等待响应体）。"""
        ctx = (session or self._session).to_event_context()
        payload = {**event, "timestamp": ctx["timestamp"]}
        await self._send_gen_204(payload, ctx["peid"], ctx["eid"])

    async def emit_num_per_page(self, session: PageSession | None = None) -> None:
        """设置每页条数时发送。"""
        await self.emit(EVENT_NUM_PER_PAGE, session)

    async def emit_page_change(self, session: PageSession | None = None) -> None:
        """翻页时发送（在请求新页数据之前）。"""
        await self.emit(EVENT_PAGE_CHANGE, session)

    async def emit_url_change(self, session: PageSession | None = None) -> None:
        """URL 通过 pushState 变化时发送（SPA 场景）。"""
        await self.emit(EVENT_URL_CHANGE, session)

    async def emit_search(self, session: PageSession | None = None) -> None:
        """执行新搜索时发送。"""
        await self.emit(EVENT_SEARCH, session)

    async def emit_sort(self, session: PageSession | None = None) -> None:
        """更改排序方式时发送。"""
        await self.emit(EVENT_SORT, session)

    def advance_page(self) -> None:
        """推进翻页状态（在 emit_page_change 之后调用）。"""
        self._session.advance_page()

    async def emit_page_change_and_advance(self) -> None:
        """复合操作：发送翻页事件 + 推进会话状态。
        调用时序：请求新页数据之前调用此方法。"""
        await self.emit_page_change()
        self.advance_page()

    # ── Internals ───────────────────────────────────────────────────────

    @staticmethod
    def _gen_id() -> str:
        """生成与 Google Patents 前端一致的 peid/eid 格式。"""
        import uuid
        import random

        p1 = uuid.uuid4().hex[:12]
        p2 = hex(random.randint(0, 0xFFF))[2:].zfill(3)
        p3 = uuid.uuid4().hex[:8]
        return f"{p1}:{p2}:{p3}"

    async def _send_gen_204(
        self,
        event: dict[str, Any],
        peid: str,
        eid: str,
    ) -> None:
        """向 /gen_204 发送事件（即发即弃，不解析响应体）。"""
        import urllib.parse

        # 限流：避免高频触发被识别为爬虫
        now = time.time()
        elapsed = now - self._last_event_ts
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_event_ts = time.time()

        event_json = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        event_encoded = urllib.parse.quote(event_json, safe="")

        url = (
            f"{self._base_url}/gen_204"
            f"?event={event_encoded}"
            f"&peid={urllib.parse.quote(peid, safe='')}"
            f"&eid={urllib.parse.quote(eid, safe='')}"
            f"&exp="
        )

        headers = self._build_headers()

        try:
            logger.debug("gen_204 → %s  [peid=%s, eid=%s]", self._site, peid[:20], eid[:20])
            await self._client.request_page(
                url,
                self._build_request_config(headers),
            )
            logger.debug("gen_204 OK (204 No Content expected)")
        except Exception as e:
            # gen_204 返回 204 时 curl_cffi 可能报空响应异常，
            # 这属于正常情况，仅记录调试日志，不中断采集流程。
            if "204" in str(e) or "No content" in str(e).lower():
                logger.debug("gen_204 returned 204 (expected): %s", e)
            else:
                logger.warning("gen_204 request failed (non-critical): %s", e)

    def _build_headers(self) -> dict[str, str]:
        """构造与真实浏览器一致的请求头。"""
        referer = f"{self._base_url}/"
        if self._site == "google_patent":
            referer = f"{self._base_url}/?q="
        return {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": referer,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": settings.http_user_agent,
        }

    def _build_request_config(self, headers: dict[str, str]):
        """构造 RequestConfig，复用模板中的 Referer 等配置。"""
        from app.models.template import RequestConfig

        return RequestConfig(
            method="GET",
            headers=headers,
            cookies={},
        )

    async def close(self) -> None:
        await self._client.close()