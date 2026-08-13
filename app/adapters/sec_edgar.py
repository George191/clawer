"""SEC EDGAR company ticker, detail, and filing records.

Produces three record types (all stored in the ``sec_edgar`` collection):

- ``company``  – one per ticker entry from ``company_tickers.json``
- ``detail``   – one per CIK from ``data.sec.gov/submissions/CIK{cik}.json``
- ``filing``   – one per filing row (column-oriented ``recent``/history data
                 converted to standard structured records)

Each record carries a unique ``record_id`` used for deduplication, so the
full ticker list, per-CIK details, and per-CIK filings can all coexist and
be updated independently by CIK.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from typing import Any

from lxml import html as lxml_html

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.models.template import RequestConfig

_SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
_SEC_SUBMISSIONS = "https://data.sec.gov/submissions"
_SEC_COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
_SEC_HEADERS = {
    "User-Agent": "Spider Research spider-research@example.com",
    "Accept": "application/json",
}
_PERMANENT_STATUSES = {400, 410, 404}
_MAX_RETRY_ATTEMPTS = 3


def normalize_cik(value: Any) -> str:
    text = str(value or "").strip()
    if not text.isdigit():
        raise ValueError(f"Invalid SEC CIK: {value}")
    return text.zfill(10)


def company_list_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten ``company_tickers.json`` into ``{cik, name, ticker}`` entries."""
    entries = []
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
    """Convert SEC column-oriented filing data to row-oriented records.

    The ``recent`` / history blocks of the submissions response store each
    field as a parallel array (e.g. ``accessionNumber``, ``filingDate``).
    This pivots them into one dict per filing with NaN replaced by ``None``.
    """
    accessions = columns.get("accessionNumber")
    if not isinstance(accessions, list):
        raise ValueError("SEC submissions response has no accessionNumber list")
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


def _build_company_record(cik: str, ticker: str, name: str) -> dict[str, Any]:
    return {
        "record_type": "company",
        "record_id": f"company:{cik}",
        "cik": cik,
        "ticker": ticker,
        "name": name,
    }


def _build_detail_record(detail: dict[str, Any]) -> dict[str, Any]:
    cik = normalize_cik(detail.get("cik"))
    filings = detail.get("filings") or {}
    recent = filings.get("recent") or {}
    filing_dates = recent.get("filingDate") or []
    latest_filing_date = max(filing_dates) if filing_dates else None

    record: dict[str, Any] = {
        "record_type": "detail",
        "record_id": f"detail:{cik}",
        "cik": cik,
    }
    for key, value in detail.items():
        if key in {"cik", "filings"}:
            continue
        record[_snake_case(key)] = value
    if latest_filing_date:
        record["filing_date"] = latest_filing_date
    return record


def _build_filing_record(cik: str, row: dict[str, Any]) -> dict[str, Any] | None:
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
        "record_type": "filing",
        "record_id": f"filing:{cik}:{accession}",
        "cik": cik,
        "accession_number": accession,
        "filing_base": filing_base,
        "primary_document_url": primary_url,
        "complete_submission_url": complete_url,
        "filing_index_url": f"{filing_base}/{accession}-index.htm",
        "submission_documents": submission_documents,
    }
    for key, value in row.items():
        if key == "accessionNumber":
            continue
        record[_snake_case(key)] = value
    return record


def _parse_filing_index(content: str, filing_base: str) -> list[dict[str, str]]:
    tree = lxml_html.fromstring(content)
    documents: list[dict[str, str]] = []
    for table in tree.xpath('//table[contains(@class,"tableFile")]'):
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
            documents.append({
                "url": url,
                "filename": url.rsplit("/", 1)[-1].split("?", 1)[0],
                "sequence": "".join(row.xpath("./td[1]//text()")).strip(),
                "description": "".join(row.xpath("./td[2]//text()")).strip(),
                "doc_type": "".join(row.xpath("./td[4]//text()")).strip(),
                "size": "".join(row.xpath("./td[5]//text()")).strip(),
            })
    return documents


@register_adapter("sec_edgar")
class SecEdgarAdapter(BaseSiteAdapter):
    """SEC EDGAR company ticker, detail, and filing records adapter."""

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

    async def on_before_crawl(self, template: Any) -> None:
        await super().on_before_crawl(template)
        self._template = template
        params = getattr(template, "_param_values", {})
        self._company_name = str(params.get("company_name") or "").strip()
        self._cik = str(params.get("cik") or "").strip()
        self._include_history = str(params.get("include_history", "true")).lower() in {
            "1", "true", "yes"
        }
        self._document_scope = str(params.get("document_scope", "primary")).lower()
        if self._document_scope not in {"primary", "all"}:
            raise ValueError("document_scope must be 'primary' or 'all'")
        template.base_url = ""
        template.list_page = _SEC_COMPANY_TICKERS

    async def parse_list_response(
        self, page: int, content: str
    ) -> list[dict[str, Any]] | None:
        payload = json.loads(content)

        # Bootstrap mode: no company_name/cik → store the full ticker list.
        if not self._company_name and not self._cik:
            records: list[dict[str, Any]] = []
            for item in payload.values():
                if not isinstance(item, dict):
                    continue
                cik = item.get("cik_str")
                if cik is None:
                    continue
                records.append(_build_company_record(
                    normalize_cik(cik),
                    str(item.get("ticker") or ""),
                    str(item.get("title") or ""),
                ))
            return records

        # Per-CIK mode: resolve target CIKs from the ticker list.
        if self._cik:
            target_ciks = [normalize_cik(self._cik)]
        else:
            target_ciks = [
                entry["cik"]
                for entry in match_companies(
                    company_list_entries(payload), self._company_name
                )
            ]
            if not target_ciks:
                raise ValueError(f"SEC company not found: {self._company_name}")

        entries_by_cik = {
            entry["cik"]: entry for entry in company_list_entries(payload)
        }
        records = []
        for cik in target_ciks:
            entry = entries_by_cik.get(cik)
            if entry:
                records.append(_build_company_record(
                    cik,
                    str(entry.get("ticker") or ""),
                    str(entry.get("name") or ""),
                ))

        watermark = self._crawl_context.get("incremental_watermark")
        is_incremental_resume = bool(getattr(watermark, "value", None))

        for cik in target_ciks:
            detail_content = await self._request_with_retry(
                f"{_SEC_SUBMISSIONS}/CIK{cik}.json",
                accept="application/json",
            )
            detail = json.loads(detail_content)
            records.append(_build_detail_record(detail))

            filings = detail.get("filings") or {}
            recent = filings.get("recent") or {}
            for row in _column_rows(recent):
                filing_record = _build_filing_record(cik, row)
                if filing_record is not None:
                    records.append(filing_record)

            if not self._include_history or is_incremental_resume:
                continue
            for history in filings.get("files") or []:
                name = str(history.get("name") or "").strip()
                if not re.fullmatch(r"CIK\d{10}-submissions-\d{3}\.json", name):
                    raise ValueError(f"Invalid SEC submissions history name: {name}")
                history_content = await self._request_with_retry(
                    f"{_SEC_SUBMISSIONS}/{name}",
                    accept="application/json",
                )
                for row in _column_rows(json.loads(history_content)):
                    filing_record = _build_filing_record(cik, row)
                    if filing_record is not None:
                        records.append(filing_record)

        if self._document_scope == "all":
            for record in records:
                if record.get("record_type") != "filing":
                    continue
                index_content = await self._request_with_retry(
                    record["filing_index_url"],
                    accept="text/html",
                )
                documents = _parse_filing_index(
                    index_content, record["filing_base"]
                )
                if documents:
                    record["filing_documents"] = documents

        return records

    async def _request_with_retry(
        self, url: str, *, accept: str = "application/json"
    ) -> str:
        config = RequestConfig(
            headers={**_SEC_HEADERS, "Accept": accept},
        )
        for attempt in range(1, _MAX_RETRY_ATTEMPTS + 1):
            try:
                return await self._client.request_page(
                    url,
                    config=config,
                    anti_crawl_enabled=True,
                    adapter_name=self.adapter_name,
                )
            except Exception as exc:
                status = HttpClient._error_status_code(exc)
                if status in _PERMANENT_STATUSES:
                    raise
                if attempt >= _MAX_RETRY_ATTEMPTS:
                    raise
                await asyncio.sleep(min(2 ** attempt, 60))

    def on_request_headers(self, page: int) -> dict[str, str]:
        return _SEC_HEADERS

    async def on_error(
        self, error: Exception, page: int, attempt: int
    ) -> str | None:
        if HttpClient._error_status_code(error) in _PERMANENT_STATUSES:
            return "abort"
        return None
