"""NGA SMAPS navigational warning adapter."""

from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from typing import Any

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.logger import get_adapter_logger

logger = get_adapter_logger(__name__, "nga_navwarn")

_MAX_RETRIES = 5
_RETRYABLE_PATTERNS = ("(28)", "(7)", "(6)", "HTTP Error 0", "HTTP Error 103")
_NAVAREAS = {
    "4": "NAVAREA IV",
    "12": "NAVAREA XII",
    "A": "HYDROLANT",
    "P": "HYDROPAC",
    "C": "HYDROARC",
}


@register_adapter("nga_navwarn")
class NgaNavwarnAdapter(BaseSiteAdapter):
    """Apply NGA request, retry, and record deduplication behavior."""

    adapter_name = "nga_navwarn"
    DEFAULT_DELAY = 3.0

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._delay = kwargs.get("request_delay", self.DEFAULT_DELAY)
        self._current_navarea = "4"
        self._retry_count = 0
        self._seen_record_ids: set[str] = set()

    @property
    def area_name(self) -> str:
        return _NAVAREAS[self._current_navarea]

    async def on_before_crawl(self, template: Any) -> None:
        await super().on_before_crawl(template)
        value = str((getattr(template, "_param_values", {}) or {}).get("navarea_id", "4")).upper()
        if value not in _NAVAREAS:
            supported = ", ".join(_NAVAREAS)
            raise ValueError(f"Unsupported NGA navarea_id '{value}'; expected one of: {supported}")

        self._current_navarea = value
        self._retry_count = 0
        self._seen_record_ids.clear()
        logger.info("Starting crawl for %s via SMAPS API", self.area_name)

    async def on_before_page(self, page: int, is_first: bool) -> None:
        if self._retry_count > 0:
            base = min(30, 3 * (2 ** self._retry_count))
            await asyncio.sleep(base + random.uniform(0, base * 0.5))
        elif not is_first:
            await asyncio.sleep(self._delay)

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        unique: list[dict] = []
        for record in records:
            outgoing_id = str(record.get("outgoing_id") or "").strip()
            if not outgoing_id or outgoing_id in self._seen_record_ids:
                continue
            self._seen_record_ids.add(outgoing_id)
            unique.append(record)
        if self._retry_count:
            logger.info(
                "NGA %s recovered after %d retries; got %d records",
                self.area_name,
                self._retry_count,
                len(unique),
            )
            self._retry_count = 0
        return unique

    def on_request_headers(self, page: int) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self._base_url}/queryResults?publications/smaps",
            "Cache-Control": "no-cache",
        }

    async def on_error(self, error: Exception, page: int, attempt: int) -> str | None:
        error_text = str(error)

        if attempt + 1 >= _MAX_RETRIES:
            logger.error(
                f"{self.area_name} gave up page {page} after {attempt + 1} attempts: {error_text[:120]}"
            )
            return "skip"

        if "404" in error_text:
            return "skip"

        if "429" in error_text or "503" in error_text:
            self._retry_count += 1
            await asyncio.sleep(min(60, 10 * (attempt + 1)))
            return None

        if any(pattern in error_text for pattern in _RETRYABLE_PATTERNS):
            with suppress(Exception):
                await self._client.mark_last_proxy_failed(self.adapter_name)
            self._retry_count += 1
            await asyncio.sleep(min(60, 10 * (2 ** attempt)) + random.uniform(0, 3))
            return None

        self._retry_count += 1
        await asyncio.sleep(min(30, 5 * (attempt + 1)))
        return None

    @classmethod
    def build_batch_param_value(cls, batch_data: list[str], param_name: str) -> str:
        if not batch_data:
            return ""
        return batch_data[0].strip().upper()
