"""News parsing helpers and HTML detail flow, using template-owned fields."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from lxml import html

from app.adapters.utils.news import NewsBaseAdapter, assets
from app.crawler.incremental import TimeWatermark, filter_records_by_watermark
from app.parser.template_parser import TemplateParser
from app.utils.path import get_nested_value

TIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def canonical_url(value: str, base: str = "") -> str:
    parts = urlsplit(urljoin(base, str(value or "").strip()))
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"Invalid article URL: {value!r}")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.startswith("utm_") and k not in {"oref", "fbclid", "gclid"}
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), "")
    )


def utc_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for pattern in ("%b %d, %Y", "%B %d, %Y", "%Y %b %d", "%Y %B %d", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                parsed = parsedate_to_datetime(text)
            except (ValueError, TypeError):
                raise ValueError(f"Unrecognized article timestamp: {text!r}") from None
    return (
        parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
        .astimezone(timezone.utc)
        .strftime(TIME_FORMAT)
    )


def text_content(value: str) -> str:
    return " ".join(
        html.fragment_fromstring(unescape(value or ""), create_parent=True).text_content().split()
    )


def prepare_record(record: dict, base: str) -> dict:
    record["url"] = canonical_url(record.get("url", ""), base)
    if not record.get("title") or not str(record["title"]).strip():
        raise ValueError(f"Article has no title: {record['url']}")
    for key in ("date", "modified"):
        if record.get(key):
            record[key] = utc_time(record[key])
    return record


def article_metadata(tree) -> dict:
    data = {}
    for script in tree.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            payload = json.loads(script)
        except ValueError:
            continue
        queue = payload if isinstance(payload, list) else [payload]
        while queue:
            item = queue.pop(0)
            if not isinstance(item, dict):
                continue
            queue.extend(item.get("@graph", []))
            types = item.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if any(
                t in {"NewsArticle", "Article", "BlogPosting", "LiveBlogPosting"} for t in types
            ):
                for source, key in (("datePublished", "date"), ("dateModified", "modified")):
                    if item.get(source):
                        data[key] = utc_time(item[source])
                authors = item.get("author", [])
                if isinstance(authors, dict | str):
                    authors = [authors]
                names = [a.get("name", "") if isinstance(a, dict) else a for a in authors]
                data["author"] = ", ".join(str(n) for n in names if n)
    for property_name, key in (
        ("article:published_time", "date"),
        ("article:modified_time", "modified"),
    ):
        nodes = tree.xpath(f'//meta[@property="{property_name}"]/@content')
        if nodes and not data.get(key):
            data[key] = utc_time(nodes[0])
    return data


def validate_json_response(content: str, template) -> str:
    payload = json.loads(content)
    rows = get_nested_value(payload, template.json_item_path)
    if not isinstance(rows, list):
        raise ValueError(f"News API response has no {template.json_item_path} array")
    for row in rows:
        for field in template.list_fields:
            value = get_nested_value(row, field.selector)
            if field.required and (value is None or value == ""):
                raise ValueError(f"News API record is missing required field: {field.name}")
    return content


def finish_news(record: dict) -> None:
    assets.process_content(record, record["url"])
    files = [
        url
        for url in record.get("external_links", [])
        if urlsplit(url)
        .path.lower()
        .endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip"))
    ]
    if files:
        record["attachments"] = [{"url": url, "type": "file"} for url in dict.fromkeys(files)]


class HtmlNewsAdapter(NewsBaseAdapter):
    """Parse independent list rows and enrich only eligible article details."""

    row_xpath = ""

    async def parse_list_response(self, page: int, content: str) -> list[dict]:
        tree = html.fromstring(content)
        nodes = tree.xpath(self.row_xpath)
        if not nodes and page == self._template.list_pagination.start_page:
            raise ValueError("News list has no article rows; refusing a successful empty crawl")
        parser = TemplateParser()
        records = [
            parser.parse_detail(html.tostring(n, encoding="unicode"), self._template.list_fields)
            for n in nodes
        ]
        records = [r for r in records if r]
        if nodes and not records:
            raise ValueError("Article rows no longer match required template fields")
        return records

    async def normalize_list_records(self, page: int, records: list[dict]) -> list[dict]:
        result = {}
        for record in records:
            prepare_record(record, self._base_url)
            result.setdefault(record["url"], record)
        return list(result.values())

    def parse_detail(self, record: dict, content: str) -> None:
        record.update(
            {
                k: v
                for k, v in TemplateParser()
                .parse_detail(content, self._template.detail_fields)
                .items()
                if v
            }
        )
        tree = html.fromstring(content)
        field = next((f for f in self._template.detail_fields if f.name == "content_html"), None)
        if field is not None:
            nodes = (
                tree.xpath(field.selector)
                if field.selector_type.value == "xpath"
                else tree.cssselect(field.selector)
            )
            for node in nodes:
                for unwanted in node.xpath(".//script|.//style|.//noscript"):
                    unwanted.drop_tree()
            record["content_html"] = "".join(html.tostring(n, encoding="unicode") for n in nodes)
        for key, value in article_metadata(tree).items():
            if value:
                record[key] = max(record.get(key, ""), value) if key == "modified" else value

    async def resolve_detail_metadata(self, record: dict) -> None:
        """Hook for a source whose list/detail omits an absolute timestamp."""

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        watermark = self._template._crawl_context.get("incremental_watermark")
        if isinstance(watermark, TimeWatermark) and watermark.enabled:
            filtered = filter_records_by_watermark(records, watermark)
            # Keep old rows in the return value so the engine can detect the end.
            eligible = [r for r in records if r in filtered.records or not r.get(watermark.field)]
        else:
            eligible = records
        semaphore = asyncio.Semaphore(3)

        async def enrich(record):
            if record.get("requires_subscription"):
                record["content_status"] = "subscription_required"
                return
            async with semaphore:
                content = await self._client.request_page(
                    record["url"],
                    self._template.detail_request,
                    anti_crawl_enabled=self._template.effective_anti_crawl_enabled,
                    adapter_name=self.adapter_name,
                )
                self.parse_detail(record, content)
                await self.resolve_detail_metadata(record)
            prepare_record(record, self._base_url)
            if not record.get(self._template.incremental.field):
                raise ValueError(f"No absolute incremental timestamp for {record['url']}")
            if not record.get("content_html"):
                raise ValueError(f"Article body selector returned no content: {record['url']}")
            record["content_status"] = "public"
            finish_news(record)

        await asyncio.gather(*(enrich(r) for r in eligible))
        return records


def offset_url(url: str, page: int, size_key: str = "limit") -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["offset"] = str(page * int(query[size_key]))
    return urlunsplit(parts._replace(query=urlencode(query)))
