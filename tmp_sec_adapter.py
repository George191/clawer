"""SEC EDGAR company and filing records adapter.

Single template, two record types in one collection:

- record_type="company" — one per CIK
  Base fields (cik, ticker, name) from company_tickers.json +
  company object from CIK{cik}.json (entity details, snake_case).

- record_type="filing" — one per accession number
  Structured row from submissions recent/history column data.
  When document_scope=all, includes document_format_files (human-readable
  docs: HTML/PDF/TXT/images) and data_files (machine-readable XBRL:
  XSD/XML schema/def/lab/pre + instance), parsed from the SEC index page.

Bootstrap mode (no company_name/cik):
  Stores all company_tickers.json entries AND concurrently fetches
  CIK{cik}.json for every CIK to populate company objects + recent filings.
  Each CIK fetch retries indefinitely until success (permanent 4xx errors
  still fail, since the data does not exist).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from lxml import html as lxml_html

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.models.template import RequestConfig
from app.storage.mongo_storage import MongoStorage

_SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
_SEC_SUBMISSIONS = "https://data.sec.gov/submissions"
_SEC_TEXT_TRIGGER = "https://www.sec.gov/robots.txt"
_SEC_COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
_SEC_COMPANY_FACTS = "https://data.sec.gov/api/xbrl/companyfacts"
_SEC_HEADERS = {
    "User-Agent": "Spider Research spider-research@example.com",
    "Accept": "application/json",
}
_PERMANENT_STATUSES = {404}
_BOOTSTRAP_CONCURRENCY = 10
_RETRY_INITIAL_DELAY = 2
_RETRY_MAX_DELAY = 60
_LOGGER = logging.getLogger("CRAWLER")


def normalize_cik(value: Any) -> str:
    text = str(value or "").strip()
    if not text.isdigit():
        raise ValueError(f"Invalid SEC CIK: {value}")
    return text.zfill(10)


def company_list_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten company_tickers.json into {cik, name, ticker} entries."""
    entries: list[dict[str, Any]] = []
    for item in payload.values():
        if not isinstance(item, dict):
            continue
        cik = item.get("cik_str")
        if cik is None:
            continue
        entries.append({
            "cik": normalize_cik(cik),
            "name": item.get("title"),
            "ticker": item.get("ticker"),
        })
    return entries


def match_companies(
    entries: list[dict[str, Any]], company_name: str
) -> list[dict[str, Any]]:
    query = " ".join(company_name.split()).casefold()
    exact = [
        entry
        for entry in entries
        if " ".join(str(entry.get("name", "")).split()).casefold() == query
    ]
    if not exact:
        exact = [
            entry
            for entry in entries
            if query in " ".join(str(entry.get("name", "")).split()).casefold()
        ]
    return list({entry["cik"]: entry for entry in exact}.values())


def _snake_case(name: str) -> str:
    """Convert camelCase / PascalCase to snake_case."""
    result = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    result = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", result)
    return result.lower()


def _column_rows(columns: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert SEC column-oriented filing data to row-oriented records."""
    accessions = columns.get("accessionNumber")
    if not isinstance(accessions, list):
        return []
    keys = list(columns.keys())
    rows: list[dict[str, Any]] = []
    for i in range(len(accessions)):
        row: dict[str, Any] = {}
        for key in keys:
            values = columns[key]
            if isinstance(values, list) and i < len(values):
                value = values[i]
                if isinstance(value, float) and value != value:
                    value = None
                row[key] = value
            else:
                row[key] = None
        rows.append(row)
    return rows


def _build_company_object(detail: dict[str, Any]) -> dict[str, Any]:
    """Build the company object from CIK{cik}.json (excluding filings)."""
    cik = normalize_cik(detail.get("cik"))
    obj: dict[str, Any] = {"cik": cik}
    for key, value in detail.items():
        if key in {"cik", "filings"}:
            continue
        obj[_snake_case(key)] = value
    return obj


def _build_filing_record(cik: str, row: dict[str, Any]) -> dict[str, Any] | None:
    """Build a filing record from a column row."""
    accession = str(row.get("accessionNumber") or "").strip()
    if not accession:
        return None
    cik_path = str(int(cik))
    accession_path = accession.replace("-", "")
    filing_base = f"{_SEC_ARCHIVES}/{cik_path}/{accession_path}"
    primary_document = str(row.get("primaryDocument") or "").strip()
    primary_url = f"{filing_base}/{primary_document}" if primary_document else ""
    complete_url = f"{filing_base}/{accession}.txt"

    submission_documents: list[dict[str, Any]] = []
    if primary_url:
        submission_documents.append({
            "url": primary_url,
            "filename": primary_document,
            "sequence": "1",
            "doc_type": str(row.get("form") or ""),
            "description": str(row.get("primaryDocDescription") or ""),
        })
    submission_documents.append({
        "url": complete_url,
        "filename": f"{accession}.txt",
        "sequence": "",
        "doc_type": "complete-submission",
        "description": "Complete submission text file",
    })

    record: dict[str, Any] = {
        "data_type": "filing",
        "_meta": {"record_id": f"filing:{cik}:{accession}"},
        "cik": cik,
        "accession_number": accession,
        "filing_base": filing_base,
        "filing_index_url": f"{filing_base}/{accession}-index.htm",
        "submission_documents": submission_documents,
        "document_format_files": [],
        "data_files": [],
    }
    for key, value in row.items():
        if key in {"accessionNumber", "primaryDocument", "primaryDocDescription"}:
            continue
        record[_snake_case(key)] = value
    return record


def _parse_filing_index(
    content: str, filing_base: str
) -> dict[str, list[dict[str, str]]]:
    """Parse SEC filing index page, separating Document Format Files from Data Files.

    SEC index pages have two <table class="tableFile"> tables:
    - summary="Document Format Files": human-readable docs (HTML, PDF, TXT, images)
    - summary="Data Files": machine-readable XBRL data (XML, XSD)

    Falls back to table order if summary attribute is missing:
    first table = Document Format Files, second = Data Files.

    Returns:
        {"document_format_files": [...], "data_files": [...]}
    """
    tree = lxml_html.fromstring(content)
    result: dict[str, list[dict[str, str]]] = {
        "document_format_files": [],
        "data_files": [],
    }

    tables = tree.xpath('//table[contains(@class,"tableFile")]')
    for i, table in enumerate(tables):
        summary = (table.get("summary") or "").strip().lower()
        if "data" in summary:
            bucket = result["data_files"]
        elif "document" in summary or "format" in summary:
            bucket = result["document_format_files"]
        else:
            bucket = result["data_files"] if i > 0 else result["document_format_files"]

        for row in table.xpath("./tr[td]"):
            hrefs = row.xpath(".//a/@href")
            if not hrefs:
                continue
            raw_url = hrefs[0].strip()
            if "/ix?doc=" in raw_url:
                url = "https://www.sec.gov" + raw_url.split("/ix?doc=", 1)[1]
            elif raw_url.startswith("/"):
                url = "https://www.sec.gov" + raw_url
            elif raw_url.startswith("http"):
                url = raw_url
            else:
                url = f"{filing_base}/{raw_url}"
            bucket.append({
                "url": url,
                "filename": url.rsplit("/", 1)[-1].split("?", 1)[0],
                "sequence": "".join(row.xpath("./td[1]//text()")).strip(),
                "description": "".join(row.xpath("./td[2]//text()")).strip(),
                "doc_type": "".join(row.xpath("./td[4]//text()")).strip(),
                "size": "".join(row.xpath("./td[5]//text()")).strip(),
            })
    return result


class _SecCompanyStorage(MongoStorage):
    def _get_collection_name(self, template_name: str) -> str:
        return "sec_edgar_company"

    def _resolve_record_id(self, record: dict[str, Any]) -> str:
        cik = normalize_cik(record.get("cik")) if record.get("cik") else ""
        return f"company:{cik}" if cik else super()._resolve_record_id(record)


class _SecFilingStorage(MongoStorage):
    def _get_collection_name(self, template_name: str) -> str:
        return "sec_edgar_filing"

    def _resolve_record_id(self, record: dict[str, Any]) -> str:
        cik = normalize_cik(record.get("cik")) if record.get("cik") else ""
        accession = str(record.get("accession_number") or "").strip()
        if cik and accession:
            return f"filing:{cik}:{accession}"
        return super()._resolve_record_id(record)


@register_adapter("sec_edgar")
class SecEdgarAdapter(BaseSiteAdapter):
    """SEC EDGAR company and filing records adapter.

    Single template, two record types in one collection:
    - record_type="company": one per CIK (base fields + company object)
    - record_type="filing": one per accession (structured filing data)

    Bootstrap mode (no company_name/cik):
      Stores all company_tickers.json entries AND concurrently fetches
      CIK{cik}.json for every CIK to populate company objects + recent filings.
      Each CIK retries indefinitely on transient errors (permanent 4xx
      errors like 404/410 fail immediately since the data does not exist).
    """

    adapter_name = "sec_edgar"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._template = None
        self._company_name = ""
        self._cik = ""
        self._include_history = True
        self._document_scope = "primary"
        self._daily_mode = True
        self._daily_accessions: dict[str, set[str]] = {}
        self._company_storage = _SecCompanyStorage()
        self._filing_storage = _SecFilingStorage()

    async def on_before_crawl(self, template: Any) -> None:
        await super().on_before_crawl(template)
        self._template = template
        params = getattr(template, "_param_values", {})
        self._company_name = str(params.get("company_name") or "").strip()
        self._cik = str(params.get("cik") or "").strip()
        self._daily_mode = not self._cik and not self._company_name
        self._include_history = str(params.get("include_history", "true")).lower() in {
            "1", "true", "yes"
        }
        self._document_scope = str(params.get("document_scope", "primary")).lower()
        if self._document_scope not in {"primary", "all"}:
            raise ValueError("document_scope must be 'primary' or 'all'")
        template.base_url = ""
        # Spider's base engine requires a text list request; discovery comes from today's master index.
        # SEC CIK mode uses the submissions API as the transport trigger.
        if self._cik:
            template.list_page = f"{_SEC_SUBMISSIONS}/CIK{normalize_cik(self._cik)}.json"
        else:
            template.list_page = f"{_SEC_SUBMISSIONS}/CIK0000004962.json"
        # SEC CIK mode uses direct submissions API.
        # SEC daily mode uses a real API transport trigger.

    async def _fetch_cik_records(
        self, cik: str, entry: dict[str, Any] | None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch company detail and filing records for a single CIK.

        Retries indefinitely on transient errors. Permanent 4xx errors
        (404/410/400) fail immediately since the data does not exist.

        Returns:
            (company_record, filing_records) tuple.
        """
        company_record: dict[str, Any] = {
            "data_type": "company",
            "_meta": {"record_id": f"company:{cik}"},
            "cik": cik,
        }
        if entry:
            company_record["ticker"] = str(entry.get("ticker") or "")
            company_record["name"] = str(entry.get("name") or "")

        # CIK{cik}.json → company object (infinite retry)
        detail_content = await self._request_with_infinite_retry(
            f"{_SEC_SUBMISSIONS}/CIK{cik}.json",
            cik=cik,
            accept="application/json",
        )
        detail = json.loads(detail_content)
        company_record["company"] = _build_company_object(detail)

        # latest filing_date for incremental watermark
        recent = (detail.get("filings") or {}).get("recent") or {}
        recent_dates = recent.get("filingDate") or []
        if recent_dates:
            company_record["filing_date"] = max(recent_dates)

        # filing records (recent block)
        filing_records: list[dict[str, Any]] = []
        for row in _column_rows(recent):
            filing_record = _build_filing_record(cik, row)
            if filing_record is not None:
                filing_records.append(filing_record)

        # historical filing records (infinite retry per file)
        if self._include_history:
            for history in (detail.get("filings") or {}).get("files") or []:
                name = str(history.get("name") or "").strip()
                if not re.fullmatch(r"CIK\d{10}-submissions-\d{3}\.json", name):
                    raise ValueError(
                        f"Invalid SEC submissions history name: {name}"
                    )
                history_content = await self._request_with_infinite_retry(
                    f"{_SEC_SUBMISSIONS}/{name}",
                    cik=cik,
                    accept="application/json",
                )
                for row in _column_rows(json.loads(history_content)):
                    filing_record = _build_filing_record(cik, row)
                    if filing_record is not None:
                        filing_records.append(filing_record)

        return company_record, filing_records

    async def _load_daily_index(self) -> list[dict[str, Any]]:
        """Load the next SEC master index after the incremental watermark."""
        crawl_context = getattr(self._template, "_crawl_context", {}) or {}
        watermark = crawl_context.get("incremental_watermark")
        watermark_value = getattr(watermark, "value", None)
        if watermark_value is None:
            raise RuntimeError(
                "sec_edgar daily crawl requires an incremental watermark"
            )
        candidate = watermark_value + timedelta(days=1)
        if candidate.date() > datetime.now(timezone.utc).date():
            _LOGGER.info(
                "sec_edgar: next daily master index %s is not available yet",
                candidate.date(),
            )
            return []
        quarter = ((candidate.month - 1) // 3) + 1
        daily_url = (
            f"https://www.sec.gov/Archives/edgar/daily-index/{candidate.year}/"
            f"QTR{quarter}/master.{candidate:%Y%m%d}.idx"
        )
        try:
            daily = await self._request_with_infinite_retry(
                daily_url, cik="daily-index", accept="text/plain"
            )
        except Exception as exc:
            if HttpClient._error_status_code(exc) in _PERMANENT_STATUSES:
                _LOGGER.info(
                    "sec_edgar: no daily master index for %s", candidate.date()
                )
                return []
            raise
        _LOGGER.info("sec_edgar: using daily master index for %s", candidate.date())

        entries_by_cik: dict[str, dict[str, Any]] = {}
        self._daily_accessions = {}
        data_lines = 0
        for raw_line in daily.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("CIK|") or line.startswith("-"):
                continue
            parts = [part.strip() for part in line.split("|", 4)]
            if len(parts) < 5:
                continue
            try:
                cik = normalize_cik(parts[0])
            except ValueError:
                continue
            filename = parts[4].strip()
            accession = filename.rsplit("/", 1)[-1].removesuffix(".txt")
            if not accession:
                continue
            data_lines += 1
            entries_by_cik.setdefault(
                cik, {"cik": cik, "name": parts[1].strip(), "ticker": ""}
            )
            self._daily_accessions.setdefault(cik, set()).add(accession)

        if not data_lines and candidate.weekday() < 5:
            raise RuntimeError(
                f"SEC daily index for {candidate.date()} contained no data rows"
            )

        _LOGGER.info(
            "sec_edgar: daily index parsed %d data lines, %d CIKs and %d accessions",
            data_lines,
            len(entries_by_cik),
            sum(len(values) for values in self._daily_accessions.values()),
        )
        return list(entries_by_cik.values())

    async def parse_list_response(
        self, page: int, content: str
    ) -> list[dict[str, Any]] | None:
        # The list request is only a text transport trigger. Company discovery
        # comes only from today's complete master filing index.
        if self._cik:
            target_ciks = {normalize_cik(self._cik)}
            entries = [{"cik": next(iter(target_ciks)), "name": "", "ticker": ""}]
        else:
            entries = await self._load_daily_index()
            if self._company_name:
                matches = match_companies(entries, self._company_name)
                if not matches:
                    raise ValueError(f"SEC company not found: {self._company_name}")
                target_ciks = {entry["cik"] for entry in matches}
            else:
                target_ciks = None

        return await self._bootstrap_all_ciks(entries, target_ciks)

    async def _bootstrap_all_ciks(
        self,
        entries: list[dict[str, Any]],
        target_ciks: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Persist companies first, then filing history, then company facts."""
        streaming = callable(
            getattr(self, "_discovered_records_callback", None)
        ) and callable(getattr(self, "_stream_records_callback", None))
        if streaming:
            self._template._crawl_context["records_streamed"] = True

        task_id = str(
            self._template._crawl_context.get("workspace_task_id") or ""
        ).strip()
        checkpoint_key = f"checkpoint:{task_id}" if task_id else ""
        redis = None
        completed_stages: set[str] = set()
        if checkpoint_key:
            from app.base.redis_connection import RedisConnection
            from app.config.settings import settings

            self._item_checkpoint_connection = RedisConnection(
                settings.checkpoint_redis_url
            )
            redis = await self._item_checkpoint_connection.ensure_connected()
            if redis is None:
                raise RuntimeError(
                    f"Redis is required for SEC stage checkpoints: {task_id}"
                )
            completed_stages = {
                str(field)[len("sec:"):]
                for field in await redis.hkeys(checkpoint_key)
                if str(field).startswith("sec:")
            }

        collected_records: list[dict[str, Any]] = []

        async def persist(records: list[dict[str, Any]]) -> None:
            if not records:
                return
            search_params = dict(
                getattr(self._template, "_param_values", None) or {}
            )
            if task_id:
                search_params["__workspace_task_id"] = task_id
            typed_records: list[dict[str, Any]] = []
            for record in records:
                if not isinstance(record, dict):
                    continue
                record.pop("record_id", None)
                data_type = str(record.get("data_type") or "").strip()
                if data_type not in {"company", "filing"}:
                    raise ValueError(
                        f"SEC record is missing a valid data_type: {data_type!r}"
                    )
                meta = record.get("_meta")
                if not isinstance(meta, dict) or not str(meta.get("record_id") or "").strip():
                    raise ValueError("SEC record is missing _meta.record_id identity key")
                typed_records.append(record)
            company_records = [
                record for record in typed_records
                if record.get("data_type") == "company"
            ]
            filing_records = [
                record for record in typed_records
                if record.get("data_type") == "filing"
            ]
            saved_count = 0
            inserted_count = 0
            updated_count = 0
            unchanged_count = 0
            for collection_name, data_type, storage, stage_records in (
                (
                    "sec_edgar_company", "company",
                    self._company_storage, company_records,
                ),
                (
                    "sec_edgar_filing", "filing",
                    self._filing_storage, filing_records,
                ),
                ):
                if not stage_records:
                    continue
                for record in stage_records:
                    if search_params:
                        record["_meta_search_params"] = search_params
                dedup_fields = (
                    ["cik"]
                    if data_type == "company"
                    else ["cik", "accession_number"]
                )
                record_ids = await storage.save_records(
                    collection_name, data_type, dedup_fields, stage_records
                )
                saved_count += len(record_ids)
                stats = storage.last_save_stats
                inserted_count += int(stats.get("inserted", len(record_ids)))
                updated_count += int(stats.get("updated", 0))
                unchanged_count += int(stats.get("unchanged", 0))
            crawl_result = self._template._crawl_context.get("_crawl_result")
            if crawl_result is not None:
                crawl_result.records.extend(records)
                crawl_result.saved_records += saved_count
                crawl_result.inserted_records += inserted_count
                crawl_result.updated_records += updated_count
                crawl_result.unchanged_records += unchanged_count
            if not streaming:
                collected_records.extend(records)

        async def mark_completed(stage: str) -> None:
            completed_stages.add(stage)
            if redis is not None:
                await redis.hset(checkpoint_key, f"sec:{stage}", "1")

        async def handle_permanent_error(
            stage: str, cik: str, label: str, exc: Exception
        ) -> bool:
            status = HttpClient._error_status_code(exc)
            if status not in _PERMANENT_STATUSES:
                return False
            _LOGGER.error(
                "sec_edgar: %s unavailable for CIK %s (status=%s): %s",
                label, cik, status, exc,
            )
            await mark_completed(stage)
            return True

        async def enrich_filing_indexes(
            cik: str, filing_records: list[dict[str, Any]]
        ) -> None:
            if self._document_scope != "all":
                return
            for record in filing_records:
                accession = str(record.get("accession_number") or "")
                index_stage = f"index:{cik}:{accession}"
                if index_stage in completed_stages:
                    continue
                try:
                    index_content = await self._request_with_infinite_retry(
                        record["filing_index_url"],
                        cik=cik,
                        accept="text/html",
                    )
                    parsed = _parse_filing_index(
                        index_content, record["filing_base"]
                    )
                    record["document_format_files"] = parsed[
                        "document_format_files"
                    ]
                    record["data_files"] = parsed["data_files"]
                    await persist([record])
                    await mark_completed(index_stage)
                except Exception as exc:
                    if not await handle_permanent_error(
                        index_stage, cik, "filing index", exc
                    ):
                        raise

        list_records = [
            {
                    "data_type": "company",
                    "_meta": {"record_id": f"company:{entry['cik']}"},
                "cik": entry["cik"],
                "ticker": str(entry.get("ticker") or ""),
                "name": str(entry.get("name") or ""),
            }
            for entry in entries
        ]
        if "company_list" not in completed_stages:
            await persist(list_records)
            await mark_completed("company_list")
            _LOGGER.info(
                "sec_edgar: persisted company list: %d companies", len(list_records)
            )

        selected_entries = [
            entry
            for entry in entries
            if target_ciks is None or entry["cik"] in target_ciks
        ]

        async def update_daily_progress(completed: int, total: int) -> None:
            # SEC daily progress is based on completed CIKs: the CIK JSON
            # and all daily filing index pages must both be complete.
            if not self._daily_mode or not task_id or not total:
                return
            crawl_result = self._template._crawl_context.get("_crawl_result")
            if crawl_result is None:
                return
            from app.web.services.ai_collect_store import ai_collect_store

            await ai_collect_store.update_task(task_id, {
                "progress": min(99, int(completed * 100 / total)),
                "records": completed,
                "inserted_records": crawl_result.inserted_records,
                "updated_records": crawl_result.updated_records,
                "deleted_records": crawl_result.deleted_records,
            })
        # SEC daily runs each CIK chain serially. A CIK is complete only
        # after submissions, every daily filing index, and company facts succeed.
        if self._daily_mode:
            total_daily_rows = sum(
                len(values) for values in self._daily_accessions.values()
            )
            completed_daily_rows = 0
            for entry in selected_entries:
                cik = entry["cik"]
                stage = f"submissions:{cik}"
                if stage not in completed_stages:
                    content = await self._request_with_infinite_retry(
                        f"{_SEC_SUBMISSIONS}/CIK{cik}.json",
                        cik=cik,
                        accept="application/json",
                    )
                    detail = json.loads(content)
                    company_record = {
                        "data_type": "company",
                        "_meta": {"record_id": f"company:{cik}"},
                        "ticker": str(entry.get("ticker") or ""),
                        "name": str(entry.get("name") or ""),
                        **_build_company_object(detail),
                    }
                    recent = (detail.get("filings") or {}).get("recent") or {}
                    filing_records = [
                        record
                        for row in _column_rows(recent)
                        if (record := _build_filing_record(cik, row)) is not None
                        and record.get("accession_number")
                        in self._daily_accessions.get(cik, set())
                    ]
                    for record in filing_records:
                        record["data_type"] = "filing"
                        record.pop("record_id", None)
                    await persist([company_record, *filing_records])
                    await enrich_filing_indexes(cik, filing_records)
                    facts_content = await self._request_with_infinite_retry(
                        f"{_SEC_COMPANY_FACTS}/CIK{cik}.json",
                        cik=cik,
                        accept="application/json",
                    )
                    facts = json.loads(facts_content)
                    await persist([{
                        "data_type": "company",
                        "_meta": {"record_id": f"company:{cik}"},
                        "cik": cik,
                        "entity_name": facts.get("entityName"),
                        "facts": facts.get("facts") or {},
                    }])
                    await mark_completed(stage)
                completed_daily_rows += len(self._daily_accessions.get(cik, set()))
                await update_daily_progress(completed_daily_rows, total_daily_rows)
            return collected_records

        semaphore = asyncio.Semaphore(_BOOTSTRAP_CONCURRENCY)
        detail_payloads: dict[str, dict[str, Any]] = {}

        async def collect_company_detail(entry: dict[str, Any]) -> None:
            cik = entry["cik"]
            stage = f"submissions:{cik}"
            if stage in completed_stages:
                return
            try:
                async with semaphore:
                    content = await self._request_with_infinite_retry(
                        f"{_SEC_SUBMISSIONS}/CIK{cik}.json",
                        cik=cik,
                        accept="application/json",
                    )
                detail = json.loads(content)
                detail_payloads[cik] = detail
                company_record = {
                            "data_type": "company",
                            "_meta": {"record_id": f"company:{cik}"},
                    "ticker": str(entry.get("ticker") or ""),
                    "name": str(entry.get("name") or ""),
                    **_build_company_object(detail),
                }
                recent = (detail.get("filings") or {}).get("recent") or {}
                recent_dates = recent.get("filingDate") or []
                if recent_dates:
                    company_record["filing_date"] = max(recent_dates)
                filing_records = [
                    record
                    for row in _column_rows(recent)
                    if (record := _build_filing_record(cik, row)) is not None
                ]
                if self._daily_mode:
                    daily_accessions = self._daily_accessions.get(cik, set())
                    filing_records = [
                        record for record in filing_records
                        if record.get("accession_number") in daily_accessions
                    ]
                await persist([company_record, *filing_records])
                # SEC daily completion requires submissions and filing indexes.
                await enrich_filing_indexes(cik, filing_records)
                await mark_completed(stage)
            except Exception as exc:
                if not await handle_permanent_error(
                    stage, cik, "submissions", exc
                ):
                    raise

        detail_tasks = [
            asyncio.create_task(collect_company_detail(entry))
            for entry in selected_entries
        ]
        detail_completed = 0
        for task in asyncio.as_completed(detail_tasks):
            await task
            detail_completed += 1
            await update_daily_progress(
                detail_completed, len(detail_tasks)
            )
            if detail_completed % 100 == 0 or detail_completed == len(detail_tasks):
                _LOGGER.info(
                    "sec_edgar: company detail progress %d/%d CIKs",
                    detail_completed, len(detail_tasks),
                )

        async def load_detail_for_filings(cik: str) -> dict[str, Any]:
            detail = detail_payloads.get(cik)
            if detail is not None:
                detail_payloads[cik] = detail
                return detail
            async with semaphore:
                content = await self._request_with_infinite_retry(
                    f"{_SEC_SUBMISSIONS}/CIK{cik}.json",
                    cik=cik,
                    accept="application/json",
                )
            detail = json.loads(content)
            detail_payloads[cik] = detail
            return detail

        async def collect_filing_history(entry: dict[str, Any]) -> int:
            cik = entry["cik"]
            if self._daily_mode:
                return 0
            if not self._include_history and self._document_scope != "all":
                return 0
            detail = await load_detail_for_filings(cik)
            filing_count = 0
            recent = (detail.get("filings") or {}).get("recent") or {}
            recent_records = [
                record
                for row in _column_rows(recent)
                if (record := _build_filing_record(cik, row)) is not None
            ]
            if self._daily_mode:
                daily_accessions = self._daily_accessions.get(cik, set())
                recent_records = [
                    record for record in recent_records
                    if record.get("accession_number") in daily_accessions
                ]
            await enrich_filing_indexes(cik, recent_records)
            if self._daily_mode or not self._include_history:
                return 0
            for item in (detail.get("filings") or {}).get("files") or []:
                name = str(item.get("name") or "").strip()
                if not re.fullmatch(
                    r"CIK\d{10}-submissions-\d{3}\.json", name
                ):
                    raise ValueError(
                        f"Invalid SEC submissions history name: {name}"
                    )
                stage = f"history:{name}"
                if stage in completed_stages:
                    continue
                try:
                    async with semaphore:
                        history_content = await self._request_with_infinite_retry(
                            f"{_SEC_SUBMISSIONS}/{name}",
                            cik=cik,
                            accept="application/json",
                        )
                    history_records = [
                        record
                        for row in _column_rows(json.loads(history_content))
                        if (record := _build_filing_record(cik, row)) is not None
                    ]
                    await persist(history_records)
                    filing_count += len(history_records)
                    await mark_completed(stage)
                    await enrich_filing_indexes(cik, history_records)
                except Exception as exc:
                    if not await handle_permanent_error(
                        stage, cik, "submissions history", exc
                    ):
                        raise
            return filing_count

        history_tasks = [
            asyncio.create_task(collect_filing_history(entry))
            for entry in selected_entries
        ]
        history_completed = 0
        history_filings = 0
        for task in asyncio.as_completed(history_tasks):
            history_filings += await task
            history_completed += 1
            if history_completed % 100 == 0 or history_completed == len(history_tasks):
                _LOGGER.info(
                    "sec_edgar: filing history progress %d/%d CIKs, %d filings",
                    history_completed, len(history_tasks), history_filings,
                )

        async def collect_company_facts(entry: dict[str, Any]) -> None:
            cik = entry["cik"]
            # SEC daily completion requires company facts.
            if self._daily_mode:
                pass
            stage = f"companyfacts:{cik}"
            if stage in completed_stages:
                return
            try:
                async with semaphore:
                    content = await self._request_with_infinite_retry(
                        f"{_SEC_COMPANY_FACTS}/CIK{cik}.json",
                        cik=cik,
                        accept="application/json",
                    )
                payload = json.loads(content)
                await persist([{
                            "data_type": "company",
                            "_meta": {"record_id": f"company:{cik}"},
                    "cik": cik,
                    "entity_name": payload.get("entityName"),
                    "facts": payload.get("facts") or {},
                }])
                await mark_completed(stage)
                if self._daily_mode:
                    nonlocal daily_completed
                    async with daily_progress_lock:
                        daily_completed += len(
                            self._daily_accessions.get(cik, set())
                        )
                        await update_daily_progress(
                            daily_completed,
                            sum(len(values) for values in self._daily_accessions.values()),
                        )
            except Exception as exc:
                if not await handle_permanent_error(
                    stage, cik, "company facts", exc
                ):
                    raise

        daily_completed = 0
        daily_progress_lock = asyncio.Lock()

        facts_tasks = [
            asyncio.create_task(collect_company_facts(entry))
            for entry in selected_entries
        ]
        facts_completed = 0
        for task in asyncio.as_completed(facts_tasks):
            await task
            facts_completed += 1
            if facts_completed % 100 == 0 or facts_completed == len(facts_tasks):
                _LOGGER.info(
                    "sec_edgar: company facts progress %d/%d CIKs",
                    facts_completed, len(facts_tasks),
                )

        if redis is not None:
            stage_fields = [
                field
                for field in await redis.hkeys(checkpoint_key)
                if str(field).startswith("sec:")
            ]
            if stage_fields:
                await redis.hdel(checkpoint_key, *stage_fields)

        _LOGGER.info(
            "sec_edgar: collection complete, %d company details and facts processed",
            len(selected_entries),
        )
        return collected_records

    async def _request_with_infinite_retry(
        self, url: str, *, cik: str, accept: str = "application/json"
    ) -> str:
        """Request with infinite retry on transient errors.

        Permanent 4xx errors (404/410/400) raise immediately since the data
        does not exist. Every attempt and retry is logged.
        """
        config = RequestConfig(
            headers={**_SEC_HEADERS, "Accept": accept},
        )
        attempt = 0
        delay = _RETRY_INITIAL_DELAY
        while True:
            attempt += 1
            _LOGGER.info(
                "sec_edgar: CIK %s request attempt %d: %s",
                cik, attempt, url,
            )
            try:
                response = await self._client.request_page(
                    url,
                    config=config,
                    anti_crawl_enabled=True,
                    adapter_name=self.adapter_name,
                    rotate_proxy=attempt > 1,
                )
                # SEC JSON endpoints can return empty or non-JSON bodies.
                # Raise here so the existing retry loop handles them.
                if accept == "application/json":
                    if not response or not response.strip():
                        raise ValueError("SEC JSON response was empty")
                    try:
                        json.loads(response)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            "SEC JSON response was not valid JSON"
                        ) from exc
                # SEC JSON response must be validated before success is logged.
                _LOGGER.info(
                    "sec_edgar: CIK %s request success on attempt %d (%d bytes)",
                    cik, attempt, len(response),
                )
                return response
            except Exception as exc:
                status = HttpClient._error_status_code(exc)
                if status in _PERMANENT_STATUSES and not self._daily_mode:
                    _LOGGER.error(
                        "sec_edgar: CIK %s permanent error %s on %s, giving up",
                        cik, status, url,
                    )
                    raise
                _LOGGER.warning(
                    "sec_edgar: CIK %s attempt %d failed (status=%s, error=%s), "
                    "rotating proxy and retrying immediately",
                    cik, attempt, status, exc,
                )
                delay = 0

    async def close(self) -> None:
        connection = getattr(self, "_item_checkpoint_connection", None)
        if connection is not None:
            await connection.close()
        await self._company_storage.close()
        await self._filing_storage.close()

    def on_request_headers(self, page: int) -> dict[str, str]:
        return _SEC_HEADERS

    async def on_error(
        self, error: Exception, page: int, attempt: int
    ) -> str | None:
        if HttpClient._error_status_code(error) in _PERMANENT_STATUSES:
            return "abort"
        return None

# SEC records use metadata identity keys
