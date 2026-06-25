from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import (
    html_to_text,
    json_dumps,
    replace_img_placeholders,
    resolve_asset_link,
    resolve_news_media_items,
    safe_datetime,
    safe_str,
)


def _news_common(record: dict[str, Any], source: str) -> dict[str, Any]:
    meta = record.get("_meta", {}) or {}
    assets = record.get("assets") or {}
    data_source = safe_str(meta.get("data_source") or meta.get("template")) or source
    record_id = safe_str(meta.get("record_id") or record.get("record_id") or record.get("url") or record.get("id")) or ""

    content_html = replace_img_placeholders(
        safe_str(record.get("content_html")),
        resolve_news_media_items(record.get("images"), assets, "images"),
    )
    summary_html = safe_str(record.get("excerpt_html"))
    attachments = resolve_news_media_items(record.get("attachments"), assets, "attachments")
    images = resolve_news_media_items(record.get("images"), assets, "images")
    slides = resolve_news_media_items(record.get("slides"), assets, "slides")
    tags = record.get("tags") or record.get("tag_names")
    organization = record.get("organization") or record.get("source_names")

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
        "thumbnail": (
            resolve_asset_link(assets, "thumbnail", "featured_media", "featured_media.source_url")
            or safe_str(record.get("thumbnail") or record.get("featured_media"))
        ),
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


_parse_ssc_news_datetime = _parse_ssc_datetime


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
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
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
register_normalizer("news", "ssc_press", normalize_ssc_press)
