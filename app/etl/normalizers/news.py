from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import (
    apply_asset_path_overrides,
    html_to_text,
    json_dumps,
    safe_datetime,
    safe_str,
)


def _normalize_satellite_today_news_types(record: dict[str, Any]) -> list[str]:
    value = record.get("category_names")
    if isinstance(value, list):
        news_types = [safe_str(item) for item in value]
        news_types = [item for item in news_types if item]
        if news_types:
            return news_types
    return []


def _media_source_url(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("source_url")

    text = safe_str(value)
    if not text:
        return None

    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return None


def _news_common(record: dict[str, Any], source: str) -> dict[str, Any]:
    record, _ = apply_asset_path_overrides(record)
    meta = record.get("_meta", {}) or {}
    data_source = safe_str(meta.get("data_source") or meta.get("template")) or source
    record_id = safe_str(meta.get("record_id") or record.get("record_id") or record.get("url") or record.get("id")) or ""

    content_html = safe_str(record.get("content_html"))
    summary_html = safe_str(record.get("excerpt_html"))
    attachments = record.get("attachments")
    images = record.get("images")
    slides = record.get("slides")
    tags = record.get("tags") or record.get("tag_names")
    organization = record.get("organization") or record.get("source_names")
    featured_media_url = _media_source_url(record.get("featured_media"))

    return {
        "data_source": data_source,
        "data_type": "news",
        "record_id": record_id,
        "title": safe_str(record.get("title")),
        "url": safe_str(record.get("url")),
        "source_url": safe_str(record.get("source_url")),
        "summary": html_to_text(record.get("summary") or record.get("excerpt") or summary_html),
        "content": html_to_text(record.get("content") or content_html),
        "content_html": content_html,
        "summary_html": summary_html,
        "author": safe_str(record.get("author")),
        "organization": json_dumps(organization),
        "tags": json_dumps(tags),
        "external_links": json_dumps(record.get("external_links")),
        "attachments": json_dumps(attachments),
        "images": json_dumps(images),
        "slides": json_dumps(slides),
        "thumbnail": _media_source_url(record.get("thumbnail")) or featured_media_url,
    }


def _parse_ssc_datetime(value: Any) -> datetime | None:
    parsed = safe_datetime(value)
    if parsed is not None:
        return parsed

    text = safe_str(value)
    if not text:
        return None

    text = re.sub(r"\bsept(?=\.?\s)", "Sep", text, flags=re.IGNORECASE)

    for fmt in ("%b. %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_ssc_press_month_group(value: Any) -> datetime | None:
    text = safe_str(value)
    if not text:
        return None

    for fmt in ("%B %Y", "%b %Y"):
        try:
            return datetime.strptime(text.title(), fmt).replace(day=1, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalize_ssc_news(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "ssc_news")
    normalized["source_published_at"] = _parse_ssc_datetime(record.get("date"))
    normalized["source_updated_at"] = _parse_ssc_datetime(record.get("modified"))
    return normalized


def normalize_blacksky_press(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "blacksky_press")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_blacksky_news(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "blacksky_news")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_blacksky_posts(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "blacksky_posts")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_satellite_today(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "satellite_today")
    normalized["news_type"] = json_dumps(_normalize_satellite_today_news_types(record))
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def _parse_arstechnica_datetime(*values: Any) -> datetime | None:
    for value in values:
        parsed = safe_datetime(value)
        if parsed is not None:
            return parsed

        text = safe_str(value)
        if not text:
            continue
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def normalize_arstechnica(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "arstechnica")
    normalized["summary_html"] = safe_str(record.get("summary_html"))
    normalized["summary"] = html_to_text(record.get("summary") or record.get("summary_html"))
    normalized["news_type"] = json_dumps(record.get("category_names"))
    normalized["source_published_at"] = _parse_arstechnica_datetime(
        record.get("source_published_at"),
        record.get("date"),
    )
    normalized["source_updated_at"] = _parse_arstechnica_datetime(
        record.get("source_updated_at"),
        record.get("modified"),
    )
    return normalized


def normalize_ssc_press(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "ssc_press")
    normalized["source_published_at"] = (
        _parse_ssc_datetime(record.get("date"))
        or _parse_ssc_press_month_group(record.get("month_group"))
    )
    normalized["source_updated_at"] = _parse_ssc_datetime(record.get("modified"))
    return normalized


register_normalizer("news", "ssc_news", normalize_ssc_news)
register_normalizer("news", "blacksky_press", normalize_blacksky_press)
register_normalizer("news", "blacksky_news", normalize_blacksky_news)
register_normalizer("news", "blacksky_posts", normalize_blacksky_posts)
register_normalizer("news", "satellite_today", normalize_satellite_today)
register_normalizer("news", "arstechnica", normalize_arstechnica)
register_normalizer("news", "ssc_press", normalize_ssc_press)
