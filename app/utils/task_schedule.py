"""Display labels derived from workspace task scheduling fields."""

from typing import Any


def with_task_schedule_label(schedule: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with a standard label, leaving execution fields untouched."""
    result = dict(schedule)
    mode = schedule.get("mode")
    if mode == "recurring":
        mode = schedule.get("recurring_mode")
    if mode == "once":
        result["label"] = "One-time task"
    elif mode == "daily":
        daily_time = str(schedule.get("daily_time") or "09:00")
        parts = daily_time.split(":")
        if len(parts) == 2 and all(part.isdigit() and len(part) <= 2 for part in parts):
            daily_time = ":".join(part.zfill(2) for part in parts)
        result["label"] = f"Daily at {daily_time} (Asia/Shanghai)"
    elif mode == "interval":
        value = schedule.get("interval_value")
        value = 30 if value is None or value == "" else value
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        unit = schedule.get("interval_unit")
        if unit not in {"hour", "day", "week", "month"}:
            unit = "minute"
        suffix = "" if value == 1 else "s"
        result["label"] = f"Every {value} {unit}{suffix}"
    return result
