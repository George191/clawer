"""Normalize collected WeChat articles, preserving source identity and HTML."""

from datetime import datetime, timedelta, timezone
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import html_to_text, json_dumps, safe_datetime, safe_str


def normalize_acticle(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("_meta") or {}
    summary_html = safe_str(record.get("summary_html") or record.get("summary"))
    content_html = safe_str(record.get("content_html") or record.get("content"))
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
