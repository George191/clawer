"""Collect one Google Patents update sitemap at a time, batching publication queries."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone
from itertools import count
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from lxml import html

from app.adapters import BaseSiteAdapter, register_adapter
from app.config.settings import settings
from app.downloader.http_client import DownloadError
from app.engine.template_loader import TemplateLoader
from app.parser.template_parser import TemplateParser
from app.storage.redis_client import RedisClient
from app.utils.connection_errors import is_retryable_connection_error
from app.utils.runtime_control import check_control_state, controlled_sleep

SHANGHAI = timezone(timedelta(hours=8))
WEEK_PATH = r"/sitemap/(\d{4})-W(\d{2})(?:-p\d+)?\.html"


class _UnexpectedSitemapResponse(ValueError):
    """The public sitemap was replaced by a challenge or proxy error page."""


def canonical(value):
    return re.sub(r"[-\s]", "", value).upper()


def week_date(url):
    match = re.fullmatch(WEEK_PATH, urlsplit(url).path)
    if not match:
        raise ValueError(f"Invalid weekly sitemap URL: {url}")
    return date.fromisocalendar(int(match[1]), int(match[2]), 1)


def _links(content, page_url, kind="sitemap"):
    links = set()
    for href in html.fromstring(content).xpath("//a/@href"):
        url = urlsplit(urljoin(page_url, href))
        if url.scheme != "https" or url.netloc != urlsplit(page_url).netloc:
            continue
        if kind == "sitemap" and re.fullmatch(WEEK_PATH, url.path):
            try:
                week_date(url.geturl())
            except ValueError:
                continue
            links.add(urlunsplit((url.scheme, url.netloc, url.path, "", "")))
        elif kind == "patent":
            match = re.fullmatch(r"/patent/([^/]+)(?:/[a-zA-Z]{2}(?:-[a-zA-Z]{2})?)?/?", url.path)
            number = canonical(match[1]) if match else ""
            if re.fullmatch(r"[A-Z]{2,}[A-Z0-9]*\d[A-Z0-9]*", number):
                links.add(number)
    return sorted(links)


@register_adapter("google_patent_daily")
class GooglePatentDailyAdapter(BaseSiteAdapter):
    adapter_name = "google_patent_daily"
    _links = staticmethod(_links)

    async def on_before_crawl(self, template):
        await super().on_before_crawl(template)
        self._template = template
        self._parser = TemplateParser()
        self._start = date.fromisoformat(template._param_values.get("start_date", datetime.now(SHANGHAI).date().isoformat()))
        watermark = self._crawl_context.get("incremental_watermark")
        if watermark and watermark.window_start:
            self._start = max(self._start, watermark.window_start.astimezone(SHANGHAI).date())
        self._history = await TemplateLoader().load_released("google_patent", "v1.0", validate_params=False)
        if self._history.adapter_class is None:
            raise RuntimeError("google_patent v1.0 has no released adapter")
        pagination = template.list_pagination
        self.batch_concurrency = max(1, (pagination.page_concurrency if pagination else None) or settings.page_concurrency)
        self._crawl_context["records_streamed"] = True
        self._redis = None
        task_id = self._crawl_context.get("workspace_task_id")
        if task_id:
            self._connection = RedisClient(settings.checkpoint_redis_url)
            self._redis = await self._connection.ensure_connected()
            if self._redis is None:
                raise RuntimeError("Redis is required for the patent sitemap cursor")
            self._checkpoint_key = f"checkpoint:{task_id}"
            cursor = await self._redis.hget(self._checkpoint_key, "sitemap_week")
            if cursor:
                self._start = min(self._start, date.fromisoformat(cursor))

    async def iter_sitemap_files(self, start, end, config):
        root = self._base_url + "/sitemap/"
        content = await self._request(root, config)
        weeks = sorted((url for url in self._links(content, root)
                        if re.fullmatch(r"/sitemap/\d{4}-W\d{2}\.html", urlsplit(url).path)
                        and week_date(url) <= end), key=week_date)
        if not weeks:
            raise ValueError("Google Patent root sitemap has no available weeks")
        selected = [url for url in weeks if week_date(url) + timedelta(days=6) >= start]
        # A current watermark can precede a delayed upload in the latest published week.
        if not selected:
            selected = weeks[-1:]
        for week in selected:
            await check_control_state()
            content = await self._request(week, config)
            prefix = urlsplit(week).path.removesuffix(".html") + "-p"
            files = [url for url in self._links(content, week) if urlsplit(url).path.startswith(prefix)]
            if not files:
                ids = self._links(content, week, "patent")
                if not ids:
                    raise ValueError(f"Google Patent weekly sitemap has no publication files: {week}")
                yield week, ids
            for source in sorted(files, key=lambda url: int(re.search(r"-p(\d+)\.html$", url)[1])):
                content = await self._request(source, config)
                ids = self._links(content, source, "patent")
                if not ids:
                    raise ValueError(f"Google Patent sitemap has no publication numbers: {source}")
                yield source, ids
            if getattr(self, "_redis", None) is not None:
                await self._redis.hset(self._checkpoint_key, "sitemap_week", week_date(week).isoformat())

    async def request_list_page(self, url, config, **kwargs):
        today = datetime.now(SHANGHAI).date()
        if self._start > today:
            raise ValueError("Patent sitemap start_date is after today")
        scanned_end = None
        async for source, ids in self.iter_sitemap_files(self._start, today, config):
            await self._crawl_file(ids, config)
            scanned_end = datetime.combine(week_date(source) + timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc)
            self._site_logger.info("Patent sitemap saved: source=%s publications=%d", source, len(ids))
        result = self._crawl_context.get("_crawl_result")
        if result and scanned_end is not None:
            result.latest_record_time = scanned_end
        return json.dumps({"results": {"total_num_results": result.saved_records if result else 0,
                                      "total_num_pages": 1, "cluster": [{"result": []}]}})

    async def _crawl_file(self, ids, config):
        batches = iter(ids[offset:offset + 100] for offset in range(0, len(ids), 100))
        save_lock = asyncio.Lock()

        async def worker():
            for batch in batches:
                await check_control_state()
                found = await self._read_batch(batch, config)
                async with save_lock:
                    await self._save([found[number] for number in batch])

        tasks = [asyncio.create_task(worker()) for _ in range(min(self.batch_concurrency, (len(ids) + 99) // 100))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _batch_url(self, ids, page):
        query = self._history.adapter_class.build_batch_param_value(ids, "query")
        template = TemplateLoader().load_content(self._history.yaml_content, param_values={"query": query})
        outer = urlsplit(template.get_full_list_url(page, num=100))
        params = parse_qs(outer.query, keep_blank_values=True)
        inner = parse_qs(params["url"][0], keep_blank_values=True)
        inner.update(dups=["language"], group=["none"])
        params["url"] = [urlencode(inner, doseq=True)]
        return urlunsplit(outer._replace(query=urlencode(params, doseq=True)))

    async def _read_batch(self, ids, config):
        requested, found = set(ids), {}
        data = await self._request(self._batch_url(ids, 0), config, True)
        pages = max(1, int(data["results"]["total_num_pages"]))
        for page in range(pages):
            if page:
                data = await self._request(self._batch_url(ids, page), config, True, page=page)
            for cluster in data["results"]["cluster"]:
                for item in cluster.get("result", []):
                    number = canonical(str(item.get("patent", {}).get("publication_number", "")))
                    if number in requested:
                        found[number] = item
            if requested <= found.keys():
                break
        missing = requested - found.keys()
        if missing and len(requested) > 1:
            pending = sorted(missing)
            size = (len(pending) + 1) // 2
            self._site_logger.warning(
                "Patent batch incomplete: requested=%d missing=%d; querying smaller batches",
                len(requested), len(missing),
            )
            for offset in range(0, len(pending), size):
                found.update(await self._read_batch(pending[offset:offset + size], config))
            missing = requested - found.keys()
        if missing:
            raise ValueError(f"Patent batch missing {len(missing)} requested publications: {','.join(sorted(missing)[:10])}")
        return found

    async def _save(self, items):
        records = self._parser.parse_list_json(
            {"results": {"cluster": [{"result": items}]}},
            self._template.json_item_path, self._template.list_fields,
        )
        if len(records) != len(items):
            raise ValueError("Patent records lost during parsing")
        # Sitemap weeks describe updates, so older publication dates must also be saved.
        saved = await self._discovered_records_callback(records)
        if saved != len(records):
            raise ValueError(f"Patent records not fully saved: expected={len(records)} saved={saved}")
        result = self._crawl_context.get("_crawl_result")
        if result:
            result.records.clear()
            task_id = self._crawl_context.get("workspace_task_id")
            if task_id:
                from app.web.services.ai_collect_store import ai_collect_store

                await ai_collect_store.update_task(task_id, {
                    "records": result.saved_records, "inserted_records": result.inserted_records,
                    "updated_records": result.updated_records,
                })

    async def _request(self, url, config, json_response=False, page=0):
        jump_attempted = False
        for attempt in count():
            await check_control_state()
            use_jump = bool(settings.proxy_pre_proxy_url) and not jump_attempted and (
                getattr(self, "_jump_only", False) or attempt == 1
            )
            try:
                if use_jump:
                    jump_attempted = True
                    from curl_cffi.requests.exceptions import RequestException

                    try:
                        async with await self._client._create_client(settings.proxy_pre_proxy_url) as client:
                            response = await asyncio.wait_for(client.request(
                                method=config.method, url=url, headers=config.headers,
                                params=config.params, cookies=config.cookies,
                            ), timeout=settings.http_request_timeout * 2)
                            if response.status_code != 200:
                                raise DownloadError(url, response.status_code, "Patent proxy route request failed")
                            content = response.text
                    except RequestException as exc:
                        raise OSError("Patent proxy route request failed") from exc
                else:
                    content = await self._client.request_page(
                        url, config, anti_crawl_enabled=self._template.effective_anti_crawl_enabled,
                        adapter_name="google_patent", page=page, attempt=attempt, rotate_proxy=attempt > 0,
                    )
                if json_response:
                    data = json.loads(content)
                    results = data["results"]
                    if results.get("user_error"):
                        raise RuntimeError("Google Patent query rejected: " + str(results["user_error"])[:300])
                    if data.get("error"):
                        raise ValueError("Google Patent returned an error response")
                    total = int(results["total_num_results"])
                    results.setdefault("total_num_pages", (total + 99) // 100)
                    if total == 0:
                        results.setdefault("cluster", [])
                    if total < 0 or int(results["total_num_pages"]) < 0 or not isinstance(results["cluster"], list):
                        raise ValueError("Invalid patent pagination or results")
                    content = data
                elif "Google Patents Sitemap" not in content or not (self._links(content, url) or self._links(content, url, "patent")):
                    raise _UnexpectedSitemapResponse("Google Patent returned an unexpected sitemap page")
                if use_jump:
                    self._jump_only = True
                return content
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status is None:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                if not (
                    is_retryable_connection_error(exc)
                    or isinstance(exc, json.JSONDecodeError | _UnexpectedSitemapResponse)
                    or status in {403, 407, 408, 429, *settings.http_retry_on_statuses}
                    or isinstance(status, int) and 500 <= status < 600
                    or isinstance(exc, DownloadError) and status is None
                ):
                    raise
                source = url if len(url) <= 140 else url[:70] + "..." + url[-70:]
                self._site_logger.warning("Patent request failed: source=%s attempt=%d error=%s", source, attempt + 1, exc)
                if use_jump:
                    self._jump_only = False
                else:
                    await self._client.mark_last_proxy_failed("google_patent")
                await controlled_sleep(min(5 * (attempt + 1), 60))

    async def on_error(self, error, page, attempt):
        return "abort"

    async def close(self):
        if hasattr(self, "_connection"):
            await self._connection.close()

    def on_request_headers(self, page):
        return {"Accept": "application/json, text/plain, */*", "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"}
