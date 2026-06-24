"""ODS 标准化工具 — 基础函数。

提供各类型/源标准化器的公共依赖：
- safe_str / safe_date / _pick_first  类型安全的值提取
- _extract_asset_paths              从 RDS 原始数据中提取 MinIO 资源路径
- _normalize_generic                通用兜底标准化器
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

from lxml import html as lxml_html


def safe_str(val: Any) -> str | None:
    if val is None:
        return None
    return str(val).strip() or None


def safe_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    text = str(val).strip()
    if not text:
        return None
    formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
        "%d-%m-%Y", "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date()
        except ValueError:
            continue
    return None


def safe_datetime(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time(), tzinfo=timezone.utc)

    text = str(val).strip()
    if not text:
        return None

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%d/%m/%Y, %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S %z",
        "%m/%d/%Y %H:%M %z",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %I:%M:%S %p %z",
        "%m/%d/%Y %I:%M %p %z",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    if text.endswith("Z"):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _pick_first(*vals: Any) -> Any:
    for v in vals:
        if v is not None:
            return v
    return None


def json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def html_to_text(html: Any) -> str | None:
    if html is None:
        return None

    text = str(html).strip()
    if not text:
        return None

    try:
        wrapper = lxml_html.fragment_fromstring(text, create_parent="div")
        content = " ".join(part.strip() for part in wrapper.text_content().split())
    except Exception:
        content = " ".join(text.split())

    return content or None


def normalize_asset_value(value: Any) -> Any:
    if isinstance(value, dict):
        if "source_url" in value:
            source_url = safe_str(value.get("source_url"))
            if source_url:
                return source_url
        if "url" in value:
            url = safe_str(value.get("url"))
            if url:
                return url
        if "href" in value:
            href = safe_str(value.get("href"))
            if href:
                return href
    return value


def build_asset_lookup(assets: Any) -> dict[str, str]:
    lookup: dict[str, str] = {}

    def _visit(node: Any, path: list[str]) -> None:
        normalized = normalize_asset_value(node)
        if normalized is not node and isinstance(normalized, str) and path:
            lookup[".".join(path)] = normalized
        elif isinstance(node, str) and path:
            value = safe_str(node)
            if value:
                lookup[".".join(path)] = value

        if isinstance(node, dict):
            for key, value in node.items():
                _visit(value, [*path, str(key)])
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                _visit(value, [*path, str(idx)])

    if assets not in (None, "", [], {}):
        _visit(assets, [])
    return lookup


def resolve_asset_link(
    assets: Any,
    *candidates: str | None,
) -> str | None:
    lookup = build_asset_lookup(assets)
    for candidate in candidates:
        key = safe_str(candidate)
        if not key:
            continue
        if key in lookup:
            return lookup[key]
    return None


def resolve_news_media_items(
    items: Any,
    assets: Any,
    asset_key: str,
) -> list[dict[str, Any]] | None:
    if not isinstance(items, list):
        return items if isinstance(items, list) else None

    resolved: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            resolved.append(item)
            continue

        asset_url = resolve_asset_link(
            assets,
            f"{asset_key}.{idx}",
            f"{asset_key}.{idx}.url",
            f"{asset_key}.{idx}.href",
            f"{asset_key}.{idx}.source_url",
        )
        merged = dict(item)
        if asset_url:
            merged["url"] = asset_url
        resolved.append(merged)
    return resolved


_IMG_PLACEHOLDER_RE = re.compile(r"\{\{img_(\d+)}}")


def replace_img_placeholders(content_html: str | None, images: Any) -> str | None:
    html = safe_str(content_html)
    if not html or not isinstance(images, list):
        return html

    def _repl(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        if idx >= len(images):
            return match.group(0)
        item = images[idx]
        if not isinstance(item, dict):
            return match.group(0)
        return safe_str(item.get("url")) or match.group(0)

    return _IMG_PLACEHOLDER_RE.sub(_repl, html)


def _extract_asset_paths(record: dict[str, Any]) -> tuple[str | None, str | None, Any]:
    assets = record.get("assets", {}) or {}
    patent_data = record.get("patent", {}) or {}

    pdf = _pick_first(
        resolve_asset_link(assets, "pdf", "url"),
        safe_str(patent_data.get("pdf")),
    )
    thumbnail = _pick_first(
        resolve_asset_link(assets, "thumbnail", "featured_media", "featured_media.source_url"),
        safe_str(patent_data.get("thumbnail")),
    )

    figures_raw = _pick_first(
        assets.get("figures"),
        patent_data.get("figures"),
    )
    if isinstance(figures_raw, (list, dict)):
        figures = figures_raw
    else:
        figures_text = safe_str(figures_raw)
        if not figures_text:
            figures = None
        else:
            try:
                figures = json.loads(figures_text)
            except (json.JSONDecodeError, TypeError):
                figures = [figures_text]

    return pdf, thumbnail, figures if figures else None


def normalize_generic(record: dict[str, Any]) -> dict[str, Any]:
    from app.etl.base import extract_meta

    meta = extract_meta(record)
    return {
        "data_source": meta.get("data_source", ""),
        "data_type": meta.get("data_type", "unknown"),
        "record_id": meta.get("record_id", ""),
        "title": safe_str(record.get("title")),
        "description": safe_str(record.get("description") or record.get("abstract") or record.get("summary")),
        "quality_score": 1.0,
        "quality_flags": "[]",
    }
