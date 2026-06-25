from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_datetime, safe_str


_NAVAREA_PREFIX_RE = re.compile(r"^(NAVAREA\s+[IVXLC\d]+)\b[\s\-:,.]*", re.IGNORECASE)
_WARNING_NO_RE = re.compile(r"(?P<serial>\d{1,4})\s*[/\-]\s*(?P<year>\d{2,4})")


def _normalize_warning_number(
    warning_no: str | None,
    region: str | None,
) -> tuple[str | None, str | None, int | None, int | None]:
    text = safe_str(warning_no)
    if not text:
        return None, safe_str(region), None, None

    candidate = re.sub(r"\s+", " ", text).strip()
    warning_region = safe_str(region)
    region_prefix = None

    if warning_region:
        region_match = re.match(
            rf"^{re.escape(warning_region)}(?:\b|$)[\s\-:,.]*",
            candidate,
            flags=re.IGNORECASE,
        )
        if region_match:
            region_prefix = warning_region
            candidate = candidate[region_match.end():].strip()

    if region_prefix is None:
        navarea_match = _NAVAREA_PREFIX_RE.match(candidate)
        if navarea_match:
            region_prefix = navarea_match.group(1).strip()
            candidate = candidate[navarea_match.end():].strip()

    match = _WARNING_NO_RE.search(candidate)
    if not match:
        return candidate or text, warning_region or region_prefix, None, None

    serial_text = match.group("serial")
    year_text = match.group("year")
    warning_no_normalized = f"{int(serial_text):0{len(serial_text)}d}/{year_text}"
    warning_year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
    return warning_no_normalized, warning_region or region_prefix, int(serial_text), warning_year


def _extract_coordinates(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []

    pattern = re.compile(
        r"(?<!\d)"
        r"(\d{1,2})[- ](\d{2}(?:\.\d+)?)\s*([NS])[\s,;]*"
        r"(\d{1,3})[- ](\d{2}(?:\.\d+)?)\s*([EW])",
        re.IGNORECASE,
    )
    coordinates: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        lat_deg, lat_min, lat_dir = int(match.group(1)), float(match.group(2)), match.group(3).upper()
        lon_deg, lon_min, lon_dir = int(match.group(4)), float(match.group(5)), match.group(6).upper()
        lat = lat_deg + lat_min / 60.0
        lon = lon_deg + lon_min / 60.0
        if lat_dir == "S":
            lat = -lat
        if lon_dir == "W":
            lon = -lon
        coordinates.append(
            {
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "raw": match.group(0).strip(),
            }
        )
    return coordinates


def _classify_hazard_type(text: str | None) -> str | None:
    if not text:
        return None
    text_upper = text.upper()
    if any(keyword in text_upper for keyword in ["RIG", "PLATFORM", "FPSO", "INSTALLATION", "DRILL"]):
        return "offshore_installation"
    if any(keyword in text_upper for keyword in ["MINE", "ORDNANCE", "UXO", "EXPLOSIVE"]):
        return "military_ordnance"
    if any(keyword in text_upper for keyword in ["WRECK", "SUNKEN", "SUBMERGED"]):
        return "wreck"
    if any(keyword in text_upper for keyword in ["CABLE", "PIPE", "LAYING", "PIPELINE"]):
        return "cable_pipe"
    if any(keyword in text_upper for keyword in ["BUOY", "LIGHT", "MARK", "BEACON", "LIGHTBUOY"]):
        return "aid_to_navigation"
    if any(keyword in text_upper for keyword in ["EXERCISE", "FIRING", "MILITARY"]):
        return "military_exercise"
    if any(keyword in text_upper for keyword in ["DRILL", "SURVEY", "SEISMIC", "RESEARCH"]):
        return "survey_operations"
    if any(keyword in text_upper for keyword in ["TOWING", "TOW", "CONVOY"]):
        return "towing"
    if any(keyword in text_upper for keyword in ["SPACE DEBRIS", "RE-ENTRY"]):
        return "space_debris"
    if any(keyword in text_upper for keyword in ["RADIO", "DSC", "MF", "VHF", "NAVTEX"]):
        return "communications"
    return "general"


def _parse_issue_time(time_str: str | None):
    return safe_datetime(time_str)


def normalize_sealagom_navwarn(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("_meta", {}) or {}
    quality_flags: list[str] = []

    message_id = safe_str(record.get("message_id"))
    region = safe_str(record.get("region") or record.get("sea_name"))
    warning_no, region, serial_number, warning_year = _normalize_warning_number(
        record.get("warning_no"),
        region,
    )
    issue_time_raw = safe_str(record.get("issue_time"))
    message_text = safe_str(record.get("message_text"))

    issued_at = _parse_issue_time(issue_time_raw)
    coordinates = _extract_coordinates(message_text)
    hazard_type = _classify_hazard_type(message_text)
    latitude = coordinates[0]["lat"] if coordinates else None
    longitude = coordinates[0]["lon"] if coordinates else None
    coordinate_count = len(coordinates)

    meta_record_id = safe_str(meta.get("record_id") or record.get("record_id"))
    if meta_record_id:
        record_id = meta_record_id
    elif warning_no:
        clean_warning = re.sub(r"[^a-zA-Z0-9/]", "_", warning_no).strip("_").lower()
        record_id = f"navwarn:{clean_warning}"
    elif message_id:
        record_id = f"navwarn:msg_{message_id}"
    else:
        message_hash = hashlib.sha1((message_text or "missing").encode("utf-8")).hexdigest()[:16]
        record_id = f"navwarn:unknown_{message_hash}"

    if not warning_no:
        quality_flags.append("missing_warning_no")
    if serial_number is None:
        quality_flags.append("missing_serial_number")
    if warning_year is None:
        quality_flags.append("missing_warning_year")
    if not message_text:
        quality_flags.append("missing_message_text")
    if not issued_at:
        quality_flags.append("missing_issue_date")
    if not coordinates:
        quality_flags.append("no_coordinates_extracted")

    quality_score = max(0.0, 1.0 - len(quality_flags) * 0.2)

    return {
        "data_source": record.get("data_source") or meta.get("data_source") or meta.get("template") or "sealagom_navwarn",
        "data_type": record.get("data_type") or "navwarn",
        "record_id": record_id,
        "navarea_id": record.get("navarea_id"),
        "warning_no": warning_no,
        "serial_number": serial_number,
        "warning_year": warning_year,
        "region": region,
        "issued_at": issued_at,
        "message_text": message_text,
        "hazard_type": hazard_type,
        "latitude": latitude,
        "longitude": longitude,
        "coordinate_count": coordinate_count,
        "quality_score": quality_score,
        "quality_flags": json.dumps(quality_flags, ensure_ascii=False),
    }
register_normalizer("navwarn", "sealagom_navwarn", normalize_sealagom_navwarn)
