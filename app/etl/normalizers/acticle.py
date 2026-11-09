"""Normalize collected WeChat articles, preserving source identity and HTML."""

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from urllib.parse import urldefrag, urljoin

from lxml import html as lxml_html

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import (
    apply_asset_path_overrides,
    html_to_text,
    json_dumps,
    safe_datetime,
    safe_str,
)


def _article_asset_html(
    content: str | None, original: dict[str, Any], mapped: dict[str, Any], asset_paths: set[tuple[str, ...]],
) -> str | None:
    def canonical(url: str) -> str:
        try:
            return urldefrag(urljoin(str(original.get("url") or ""), url))[0]
        except ValueError:
            return url

    replacements = {}
    for field in ("images", "videos", "audios", "attachments"):
        sources, targets = original.get(field), mapped.get(field)
        if not isinstance(sources, list) or not isinstance(targets, list):
            continue
        for index, (source, target) in enumerate(zip(sources, targets, strict=False)):
            old_url = source.get("url") if isinstance(source, dict) else source
            new_url = target.get("url") if isinstance(target, dict) else target
            path = (field, str(index), "url") if isinstance(source, dict) else (field, str(index))
            if path not in asset_paths or not isinstance(new_url, str) or not new_url:
                continue
            aliases = [old_url, source.get("placeholder") if isinstance(source, dict) else None]
            for alias in aliases:
                if isinstance(alias, str) and alias:
                    replacements[canonical(alias)] = new_url
    if not content or not replacements:
        return content

    def resolve(url: str) -> str | None:
        return replacements.get(canonical(url))

    def srcset(value: str) -> str:
        pieces, previous, position = [], 0, 0
        while position < len(value):
            while position < len(value) and (value[position].isspace() or value[position] == ","):
                position += 1
            start = position
            while position < len(value) and not value[position].isspace():
                position += 1
            end = position
            while end > start and value[end - 1] == ",":
                end -= 1
            if replacement := resolve(value[start:end]):
                pieces.extend((value[previous:start], replacement))
                previous = end
            if end == position:
                while position < len(value) and value[position] != ",":
                    position += 1
        return "".join((*pieces, value[previous:]))

    try:
        tree = lxml_html.fragment_fromstring(content, create_parent="div")
    except (ValueError, lxml_html.etree.ParserError):
        return content
    changed = False
    for node in tree.iter():
        for attribute in ("src", "data-src", "href", "poster"):
            value = node.get(attribute)
            if value and (replacement := resolve(value)):
                node.set(attribute, replacement)
                if attribute == "data-src" and node.tag == "img":
                    node.set("src", replacement)
                changed = True
        value = node.get("srcset")
        if value and (replacement := srcset(value)) != value:
            node.set("srcset", replacement)
            changed = True
    if not changed:
        return content
    return escape(tree.text or "", quote=False) + "".join(
        lxml_html.tostring(node, encoding="unicode", method="html") for node in tree
    )


def normalize_acticle(record: dict[str, Any]) -> dict[str, Any]:
    original = record
    record, asset_paths = apply_asset_path_overrides(record)
    meta = record.get("_meta") or {}
    summary_html = safe_str(record.get("summary_html") or record.get("summary"))
    content_html = _article_asset_html(
        safe_str(record.get("content_html") or record.get("content")), original, record, asset_paths,
    )
    published = record.get("publish_time")
    # Historical WeChat page times without an offset are China local time.
    publish_time = safe_datetime(published)
    if publish_time is not None:
        try:
            parsed = published if isinstance(published, datetime) else datetime.fromisoformat(str(published))
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None and parsed.tzinfo is None:
            publish_time = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return {
        "record_id": safe_str(meta.get("record_id")),
        "data_source": safe_str(record.get("data_source")),
        "data_type": "acticle",
        **{field: safe_str(record.get(field)) for field in (
            "source_id", "account", "gh_id", "zoro_guid", "title", "url", "cover", "info_source", "source_type",
        )},
        "summary": html_to_text(summary_html),
        "summary_html": summary_html,
        "content": safe_str(record.get("content")) or html_to_text(content_html),
        "content_html": content_html,
        "publish_time": publish_time,
        "publish_time_raw": safe_str(record.get("publish_time")),
        "images": json_dumps(record.get("images")),
        "reads": record.get("reads"),
        "likes": record.get("likes"),
        "is_original": record.get("is_original"),
        "history_fetched_at": safe_datetime(record.get("history_fetched_at")),
    }


register_normalizer("acticle", "acticle_wechat", normalize_acticle)
