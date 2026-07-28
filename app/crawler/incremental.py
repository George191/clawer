"""Redis-backed time-watermark filtering for recurring crawls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models.template import SiteTemplate
from app.utils.path import get_nested_value

WATERMARK_LOOKBACK = timedelta(days=1)

_TEMPLATE_TIME_RULES = {
    "arstechnica": ("date", "%Y-%m-%d"),
    "blacksky_news": ("modified", "%Y-%m-%dT%H:%M:%S"),
    "blacksky_posts": ("modified", "%Y-%m-%dT%H:%M:%S"),
    "blacksky_press": ("modified", "%Y-%m-%dT%H:%M:%S"),
    "google_patent": ("patent.publication_date", "%Y-%m-%d"),
    "nga_navwarn": ("issue_time", "%d%H%MZ %b %Y"),
    "planet": ("modified", "%m/%d/%Y %I:%M:%S %p %z"),
    "satellite_today": ("modified", "%Y-%m-%dT%H:%M:%S"),
    "sealagom_navwarn": ("issue_time", "%d/%m/%Y, %H:%M"),
    "ssc_news": ("date", "%B %d, %Y"),
    "ssc_press": ("date", "%B %d, %Y"),
}


@dataclass(frozen=True)
class TimeWatermark:
    enabled: bool
    field: str
    value: datetime | None
    record_time_format: str

    @property
    def window_start(self) -> datetime | None:
        return self.value - WATERMARK_LOOKBACK if self.value else None


@dataclass(frozen=True)
class FilteredRecords:
    records: list[dict[str, Any]]
    latest_time: datetime | None
    missing_time: int
    all_before_window: bool


def parse_record_time(value: Any, date_format: str) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.strptime(text, date_format)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_watermark_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_time_field(
    template: SiteTemplate,
    policies: dict[str, Any],
) -> str:
    configured = str(policies.get("incremental_field") or "").strip()
    if configured:
        return configured
    known_rule = _TEMPLATE_TIME_RULES.get(template.name)
    if known_rule:
        return known_rule[0]
    available = {field.name for field in template.list_fields}
    for candidate in (
        "modified", "updated_at", "date", "issue_time",
        "publication_date", "filing_date", "created_at",
    ):
        if candidate in available:
            return candidate
    raise ValueError(
        f"Incremental crawl requires a time field: template={template.name}"
    )


def build_time_watermark(
    template: SiteTemplate,
    policies: dict[str, Any],
    redis_value: str | None,
) -> TimeWatermark:
    if not policies.get("incremental"):
        return TimeWatermark(False, "", None, "")
    field = resolve_time_field(template, policies)
    time_rule = _TEMPLATE_TIME_RULES.get(template.name)
    if time_rule is None:
        raise ValueError(
            f"Incremental crawl has no time parser: template={template.name}"
        )
    record_time_format = time_rule[1]
    watermark = parse_watermark_time(redis_value) if redis_value else None
    if redis_value and watermark is None:
        raise ValueError(
            f"Invalid Redis watermark for task template {template.name}: {redis_value!r}"
        )
    return TimeWatermark(True, field, watermark, record_time_format)


def filter_records_by_watermark(
    records: list[dict[str, Any]],
    watermark: TimeWatermark,
) -> FilteredRecords:
    if not watermark.enabled:
        return FilteredRecords(records, None, 0, False)

    selected: list[dict[str, Any]] = []
    valid_times: list[datetime] = []
    selected_times: list[datetime] = []
    missing_time = 0
    window_start = watermark.window_start
    for record in records:
        record_time = parse_record_time(
            get_nested_value(record, watermark.field),
            watermark.record_time_format,
        )
        if record_time is None:
            missing_time += 1
            if window_start is None:
                selected.append(record)
            continue
        valid_times.append(record_time)
        if window_start is None or record_time >= window_start:
            selected.append(record)
            selected_times.append(record_time)
    latest_time = max(selected_times, default=None)
    all_before_window = bool(
        window_start
        and valid_times
        and len(valid_times) == len(records)
        and all(record_time < window_start for record_time in valid_times)
    )
    return FilteredRecords(
        selected,
        latest_time,
        missing_time,
        all_before_window,
    )
