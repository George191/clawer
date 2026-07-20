"""NGA SMAPS navigational warning adapter.

The public query page is a React shell. Its data source is the SMAPS API:
``/api/publications/smaps``. During NGA maintenance the API returns a 503
HTML page, which links to official ``DailyMem*.txt`` snapshots. This adapter
uses the API first and switches to the matching text snapshot on maintenance.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from contextlib import suppress
from typing import Any

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.logger import get_adapter_logger

logger = get_adapter_logger(__name__, "nga")

_MAX_RETRIES = 5
_RETRYABLE_PATTERNS = ("(28)", "(7)", "(6)", "HTTP Error 0", "HTTP Error 103")
_MAINTENANCE_MARKERS = (
    "currently under maintenance",
    "portal is currently unavailable",
    "msi is currently under maintenance",
)

_NAVAREAS: dict[str, tuple[str, str]] = {
    "4": ("NAVAREA IV", "DailyMemIV.txt"),
    "12": ("NAVAREA XII", "DailyMemXII.txt"),
    "A": ("HYDROLANT", "DailyMemLAN.txt"),
    "P": ("HYDROPAC", "DailyMemPAC.txt"),
    "C": ("HYDROARC", "DailyMemARC.txt"),
}

_TEXT_MESSAGE_RE = re.compile(
    r"(?ms)^(?P<issue_time>\d{6}Z\s+[A-Z]{3}\s+\d{2})\s*\r?\n"
    r"(?P<header>(?:NAVAREA\s+(?:IV|XII)|HYDRO(?:LANT|PAC|ARC))\s+"
    r"(?P<warning_no>\d{1,4}/\d{2})[^\r\n]*)\.?\s*\r?\n"
    r"(?P<body>.*?)"
    r"(?=^\d{6}Z\s+[A-Z]{3}\s+\d{2}\s*$|\Z)"
)


@register_adapter("nga_navwarn")
class NgaNavwarnAdapter(BaseSiteAdapter):
    """Collect NGA SMAPS warnings from JSON or the official text fallback."""

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
        self._template: Any | None = None
        self._using_fallback = False
        self._retry_count = 0
        self._error_count = 0
        self._seen_warning_keys: set[tuple[str, str]] = set()

    @property
    def area_name(self) -> str:
        return _NAVAREAS[self._current_navarea][0]

    async def on_before_crawl(self, template: Any) -> None:
        await super().on_before_crawl(template)
        self._template = template

        value = str((getattr(template, "_param_values", {}) or {}).get("navarea_id", "4")).upper()
        if value not in _NAVAREAS:
            supported = ", ".join(_NAVAREAS)
            raise ValueError(f"Unsupported NGA navarea_id '{value}'; expected one of: {supported}")

        self._current_navarea = value
        if not self._using_fallback:
            self._retry_count = 0
            self._error_count = 0
            self._seen_warning_keys.clear()

        source = "official maintenance text" if self._using_fallback else "SMAPS API"
        logger.info("Starting crawl for %s via %s", self.area_name, source)

    async def on_before_page(self, page: int, is_first: bool) -> None:
        if self._retry_count > 0:
            base = min(30, 3 * (2 ** self._retry_count))
            await asyncio.sleep(base + random.uniform(0, base * 0.5))
        elif not is_first:
            await asyncio.sleep(self._delay)

    async def parse_list_response(self, page: int, content: str) -> list[dict] | None:
        stripped = content.lstrip()
        lowered = stripped[:4000].lower()
        if any(marker in lowered for marker in _MAINTENANCE_MARKERS):
            raise RuntimeError("NGA MSI maintenance page returned instead of warning data")

        if stripped.startswith("{") or stripped.startswith("["):
            return self._parse_api_response(content)
        if self._using_fallback or "IN FORCE AS OF" in content[:500].upper():
            return self._parse_text_snapshot(content)
        return None

    def _parse_api_response(self, content: str) -> list[dict]:
        payload = json.loads(content)
        if isinstance(payload, dict):
            items = payload.get("smaps", [])
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        if not isinstance(items, list):
            raise ValueError("NGA SMAPS response does not contain a list in 'smaps'")
        return [record for item in items if isinstance(item, dict) if (record := self._normalize_api_record(item))]

    def _normalize_api_record(self, item: dict[str, Any]) -> dict[str, Any] | None:
        warning_no = self._first_value(
            item,
            "warning_no",
            "warningNumber",
            "msgNumber",
            "msgSqncNumber",
            "messageNumber",
            "number",
        )
        year = self._first_value(item, "msgYear", "messageYear", "year")
        issue_time = self._first_value(item, "createdOn", "issueDate", "date")
        if not year:
            year_match = re.search(r"\b(20\d{2})\b", issue_time)
            year = year_match.group(1) if year_match else ""
        if warning_no and year and "/" not in warning_no:
            warning_no = f"{warning_no}/{str(year)[-2:]}"

        text = self._first_value(
            item,
            "message_text",
            "messageText",
            "warningText",
            "msgText",
            "text",
            "message",
        )
        if not warning_no and not text:
            return None

        return self._build_record(
            warning_no=warning_no,
            issue_time=issue_time,
            category=self._first_value(item, "category"),
            dnc_region=self._first_value(item, "dncRegion"),
            navarea=self._first_value(item, "usNavArea", "msgType", "navArea") or self.area_name,
            oceans=self._first_value(item, "oceans"),
            subregion=self._first_value(item, "subRegion"),
            status=self._first_value(item, "status"),
            text=text,
        )

    def _parse_text_snapshot(self, content: str) -> list[dict]:
        records: list[dict] = []
        for match in _TEXT_MESSAGE_RE.finditer(content.replace("\ufeff", "")):
            text = "\n".join(
                (match.group("issue_time"), match.group("header").strip(), match.group("body").strip())
            )
            records.append(
                self._build_record(
                    warning_no=match.group("warning_no"),
                    issue_time=match.group("issue_time"),
                    category="",
                    dnc_region="",
                    navarea=self.area_name,
                    oceans="",
                    subregion="",
                    status="INFORCE",
                    text=text,
                )
            )
        logger.info("Parsed %d warnings from %s fallback", len(records), self.area_name)
        return records

    def _build_record(
        self,
        *,
        warning_no: str,
        issue_time: str,
        category: str,
        dnc_region: str,
        navarea: str,
        oceans: str,
        subregion: str,
        status: str,
        text: str,
    ) -> dict[str, Any]:
        return {
            "warning_no": warning_no.strip(),
            "issue_time": issue_time.strip(),
            "category": category.strip(),
            "dnc_region": dnc_region.strip(),
            "navarea": navarea.strip(),
            "oceans": oceans.strip(),
            "subregion": subregion.strip(),
            "status": status.strip(),
            "message_text": text.strip(),
        }

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        unique: list[dict] = []
        for record in records:
            warning_key = (
                str(record.get("navarea") or "").strip(),
                str(record.get("warning_no") or "").strip(),
            )
            if not all(warning_key) or warning_key in self._seen_warning_keys:
                continue
            self._seen_warning_keys.add(warning_key)
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
            "Accept": "text/plain, application/json, text/html;q=0.8, */*;q=0.5",
            "Referer": f"{self._base_url}/queryResults?publications/smaps",
            "Cache-Control": "no-cache",
        }

    async def on_error(self, error: Exception, page: int, attempt: int) -> str | None:
        self._error_count += 1
        error_text = str(error)

        if ("503" in error_text or "maintenance" in error_text.lower()) and not self._using_fallback:
            self._activate_fallback()
            logger.warning("NGA SMAPS API unavailable; switching %s to official text fallback", self.area_name)
            return "reset_session"

        if attempt >= _MAX_RETRIES:
            logger.error(
                "%s gave up page %d after %d attempts: %s",
                self.area_name,
                page,
                attempt,
                error_text[:120],
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

    def _activate_fallback(self) -> None:
        if self._template is None:
            raise RuntimeError("NGA template is not initialized")
        self._using_fallback = True
        self._template.list_page = f"/apology_objects/{_NAVAREAS[self._current_navarea][1]}"

    @staticmethod
    def _first_value(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if value is not None and str(value).strip().lower() not in {"", "null", "none"}:
                return str(value).strip()
        return ""

    @classmethod
    def build_batch_param_value(cls, batch_data: list[str], param_name: str) -> str:
        if not batch_data:
            return ""
        return batch_data[0].strip().upper()
