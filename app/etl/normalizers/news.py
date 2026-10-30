from __future__ import annotations

import json
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


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("_meta")
    return value if isinstance(value, dict) else {}


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
        value = value.get("url")

    text = safe_str(value)
    if not text:
        return None

    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    # 接受本地 MinIO 路径（如 news/<source>/<record_id>/<file>）
    if "/" in text and not text.startswith(("data:", "javascript:")):
        return text
    return None


def _news_common(record: dict[str, Any], source: str) -> dict[str, Any]:

    def pick(name: str) -> Any:
        return record.get(name)

    def dump(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None
    
    meta = _meta(record)
    record, _ = apply_asset_path_overrides(record)

    content_html = safe_str(pick("content_html"))
    summary_html = safe_str(pick("summary_html"))
    attachments = pick("attachments")
    images = pick("images")
    videos = pick("videos")
    audios = pick("audios")
    iframes = pick("iframe")
    slides = pick("slides")
    tags = pick("tags")
    organization = pick("organization")

    return {
        "data_source": safe_str(record.get("data_source")),
        "data_type": safe_str(meta.get("data_type")),
        "record_id": safe_str(meta.get("record_id")),
        "title": safe_str(pick("title")),
        "url": safe_str(pick("url")),
        "source_url": safe_str(pick("source_url")),
        "summary": html_to_text(summary_html),
        "content": html_to_text(content_html),
        "content_html": content_html,
        "summary_html": summary_html,
        "author": safe_str(pick("author")),
        "news_type": None,
        "organization": dump(organization),
        "tags": dump(tags),
        "external_links": dump(pick("external_links")),
        "attachments": dump(attachments),
        "images": dump(images),
        "videos": dump(videos),
        "audios": dump(audios),
        "iframes": dump(iframes),
        "slides": dump(slides),
        "thumbnail": _media_source_url(pick("thumbnail")),
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
    featured_media = record.get("featured_media")
    record["thumbnail"] = (
        featured_media.get("source_url") if isinstance(featured_media, dict) else None
    )
    if record.get("assets"):
        record["assets"]["thumbnail"] = (
            record["assets"].get("featured_media") or {}
        ).get("source_url")

    record["summary_html"] = record.get("excerpt_html")
    normalized = _news_common(record, "blacksky_press")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_blacksky_news(record: dict[str, Any]) -> dict[str, Any]:
    featured_media = record.get("featured_media")
    record["thumbnail"] = (
        featured_media.get("source_url") if isinstance(featured_media, dict) else None
    )
    if record.get("assets"):
        record["assets"]["thumbnail"] = (
            record["assets"].get("featured_media") or {}
        ).get("source_url")

    record["organization"] = record.get("source_names")
    normalized = _news_common(record, "blacksky_news")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_blacksky_posts(record: dict[str, Any]) -> dict[str, Any]:
    featured_media = record.get("featured_media")
    record["thumbnail"] = (
        featured_media.get("source_url") if isinstance(featured_media, dict) else None
    )
    if record.get("assets"):
        record["assets"]["thumbnail"] = (
            record["assets"].get("featured_media") or {}
        ).get("source_url")
        
    record["summary_html"] = record.get("excerpt_html")
    normalized = _news_common(record, "blacksky_posts")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_satellite_today(record: dict[str, Any]) -> dict[str, Any]:
    record["tags"] = record.get("tag_names")
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


def normalize_breakingdefense(record: dict[str, Any]) -> dict[str, Any]:
    record["summary_html"] = record.get("excerpt_html")
    normalized = _news_common(record, "breakingdefense")
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


def normalize_unseenlabs_news(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "unseenlabs_news")
    # category（单个字符串）-> news_type（JSON 数组，与 satellite_today/arstechnica 一致）
    category = safe_str(record.get("category"))
    normalized["news_type"] = json_dumps([category] if category else None)
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = None
    return normalized


def normalize_planet_investors_news(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "planet_investors_news")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_planet_pulse_news(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "planet_pulse_news")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_n2yo_news(record: dict[str, Any]) -> dict[str, Any]:
    publisher = safe_str(record.get("publisher"))
    record["organization"] = [publisher] if publisher else None
    normalized = _news_common(record, "n2yo_news")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_russianspaceweb_news(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "russianspaceweb_news")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_thespacedevs_news(record: dict[str, Any]) -> dict[str, Any]:
    publisher = safe_str(record.get("publisher"))
    record["organization"] = [publisher] if publisher else None
    normalized = _news_common(record, "thespacedevs_news")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_spacedaily_news(record: dict[str, Any]) -> dict[str, Any]:
    featured_media = record.get("featured_media")
    record["thumbnail"] = (
        featured_media.get("source_url") if isinstance(featured_media, dict) else None
    )
    if record.get("assets"):
        record["assets"]["thumbnail"] = (
            record["assets"].get("featured_media") or {}
        ).get("source_url")
    author = record.get("author")
    record["author"] = author.get("name") if isinstance(author, dict) else None
    record["summary_html"] = record.get("excerpt_html")
    record["tags"] = record.get("tag_names")
    normalized = _news_common(record, "spacedaily_news")
    normalized["news_type"] = json_dumps(record.get("category_names"))
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_space_news(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "space_news")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_spaceflightnow_news(record: dict[str, Any]) -> dict[str, Any]:
    featured_media = record.get("featured_media")
    record["thumbnail"] = (
        featured_media.get("source_url") if isinstance(featured_media, dict) else None
    )
    if record.get("assets"):
        record["assets"]["thumbnail"] = (
            record["assets"].get("featured_media") or {}
        ).get("source_url")
    author = record.get("author")
    record["author"] = author.get("name") if isinstance(author, dict) else None
    record["summary_html"] = record.get("excerpt_html")
    record["tags"] = record.get("tag_names")
    normalized = _news_common(record, "spaceflightnow_news")
    normalized["news_type"] = json_dumps(record.get("category_names"))
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_clearspace_news(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "clearspace_news")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_defensenews_news(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "defensenews_news")
    normalized["news_type"] = json_dumps(record.get("category_names"))
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_defenseone_news(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _news_common(record, "defenseone_news")
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_spacenews_news(record: dict[str, Any]) -> dict[str, Any]:
    featured_media = record.get("featured_media")
    record["thumbnail"] = (
        featured_media.get("source_url") if isinstance(featured_media, dict) else None
    )
    if record.get("assets"):
        record["assets"]["thumbnail"] = (
            record["assets"].get("featured_media") or {}
        ).get("source_url")
    author = record.get("author")
    record["author"] = author.get("name") if isinstance(author, dict) else None
    record["summary_html"] = record.get("excerpt_html")
    record["tags"] = record.get("tag_names")
    normalized = _news_common(record, "spacenews_news")
    normalized["news_type"] = json_dumps(record.get("category_names"))
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized


def normalize_satblog_news(record: dict[str, Any]) -> dict[str, Any]:
    featured_media = record.get("featured_media")
    record["thumbnail"] = (
        featured_media.get("source_url") if isinstance(featured_media, dict) else None
    )
    if record.get("assets"):
        record["assets"]["thumbnail"] = (
            record["assets"].get("featured_media") or {}
        ).get("source_url")
    author = record.get("author")
    record["author"] = author.get("name") if isinstance(author, dict) else None
    record["summary_html"] = record.get("excerpt_html")
    record["tags"] = record.get("tag_names")
    normalized = _news_common(record, "satblog_news")
    normalized["news_type"] = json_dumps(record.get("category_names"))
    normalized["source_published_at"] = safe_datetime(record.get("date"))
    normalized["source_updated_at"] = safe_datetime(record.get("modified"))
    return normalized



register_normalizer("news", "news_ssc", normalize_ssc_news)
register_normalizer("news", "news_blacksky_press", normalize_blacksky_press)
register_normalizer("news", "news_blacksky", normalize_blacksky_news)
register_normalizer("news", "news_blacksky_posts", normalize_blacksky_posts)
register_normalizer("news", "news_satellite_today", normalize_satellite_today)
register_normalizer("news", "news_arstechnica", normalize_arstechnica)
register_normalizer("news", "news_breakingdefense", normalize_breakingdefense)
register_normalizer("news", "news_ssc_press", normalize_ssc_press)
register_normalizer("news", "news_unseenlabs", normalize_unseenlabs_news)
register_normalizer("news", "news_planet_investors", normalize_planet_investors_news)
register_normalizer("news", "news_planet_pulse", normalize_planet_pulse_news)
register_normalizer("news", "news_n2yo", normalize_n2yo_news)
register_normalizer("news", "news_russianspaceweb", normalize_russianspaceweb_news)
register_normalizer("news", "news_thespacedevs", normalize_thespacedevs_news)
register_normalizer("news", "news_spacedaily", normalize_spacedaily_news)
register_normalizer("news", "news_space_com", normalize_space_news)
register_normalizer("news", "news_spaceflightnow", normalize_spaceflightnow_news)
register_normalizer("news", "news_clearspace", normalize_clearspace_news)
register_normalizer("news", "news_defensenews", normalize_defensenews_news)
register_normalizer("news", "news_defenseone", normalize_defenseone_news)
register_normalizer("news", "news_spacenews", normalize_spacenews_news)
register_normalizer("news", "news_satblog", normalize_satblog_news)
