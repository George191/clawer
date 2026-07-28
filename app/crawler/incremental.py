"""Redis-backed time-watermark filtering for recurring crawls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from app.models.template import SiteTemplate
from app.utils.path import get_nested_value

WATERMARK_LOOKBACK = timedelta(days=1)

_TEMPLATE_TIME_FIELDS = {
    "arstechnica": "date",
    "blacksky_news": "modified",
    "blacksky_posts": "modified",
    "blacksky_press": "modified",
    "google_patent": "patent.publication_date",
    "nga_navwarn": "issue_time",
    "planet": "modified",
    "satellite_today": "modified",
    "sealagom_navwarn": "issue_time",
    "ssc_news": "date",
    "ssc_press": "date",
}

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y%m%d",
    "%d %b %Y",
    "%m/%d/%Y, %H:%M",
    "%m/%d/%Y %I:%M:%S %p",
)


@dataclass(frozen=True)
class TimeWatermark:
    enabled: bool
    field: str
    value: datetime | None

    @property
    def window_start(self) -> datetime | None:
        return self.value - WATERMARK_LOOKBACK if self.value else None


@dataclass(frozen=True)
class FilteredRecords:
    records: list[dict[str, Any]]
    latest_time: datetime | None
    missing_time: int
    all_before_window: bool


def parse_record_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, int | float):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value or "").strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            parsed = None
        if parsed is None:
            for date_format in _DATE_FORMATS:
                try:
                    parsed = datetime.strptime(text, date_format)
                    break
                except ValueError:
                    continue
        if parsed is None:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, OverflowError):
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
    known = _TEMPLATE_TIME_FIELDS.get(template.name)
    if known:
        return known
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
        return TimeWatermark(False, "", None)
    field = resolve_time_field(template, policies)
    watermark = parse_record_time(redis_value) if redis_value else None
    if redis_value and watermark is None:
        raise ValueError(
            f"Invalid Redis watermark for task template {template.name}: {redis_value!r}"
        )
    return TimeWatermark(True, field, watermark)


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
        record_time = parse_record_time(get_nested_value(record, watermark.field))
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
