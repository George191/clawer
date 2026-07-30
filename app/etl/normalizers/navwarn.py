from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_datetime, safe_str
from app.logger import get_logger

logger = get_logger(__name__)

_NAVAREA_PREFIX_RE = re.compile(r"^(NAVAREA\s+[IVXLC\d]+)\b[\s\-:,.]*", re.IGNORECASE)
_WARNING_SLASH_RE = re.compile(r"(?P<serial>\d{1,4})\s*/\s*(?P<year>\d{2,4})")
_WARNING_YEAR_SERIAL_RE = re.compile(r"(?P<year>\d{2,4})\s*-\s*(?P<serial>\d{1,4})")

_LAT_MIN, _LAT_MAX = -90.0, 90.0
_LON_MIN, _LON_MAX = -180.0, 180.0
_NAVAREA_MIN, _NAVAREA_MAX = 1, 21
_NAVWARN_YEAR_MIN = 1900
_NAVWARN_MESSAGE_MAX_LENGTH = 20000
_NAVWARN_WARNING_NO_MAX_LENGTH = 64
_NAVWARN_REGION_MAX_LENGTH = 64


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

    match = _WARNING_SLASH_RE.search(candidate)
    year_first = False
    if not match:
        match = _WARNING_YEAR_SERIAL_RE.search(candidate)
        year_first = match is not None
    if not match:
        return candidate or text, warning_region or region_prefix, None, None

    serial_text = match.group("serial")
    year_text = match.group("year")
    serial_number = int(serial_text)
    warning_year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
    if year_first:
        warning_no_normalized = f"{year_text}-{serial_number:0{len(serial_text)}d}"
    else:
        warning_no_normalized = f"{serial_number:0{len(serial_text)}d}/{year_text}"
    return warning_no_normalized, warning_region or region_prefix, serial_number, warning_year


def _extract_coordinates(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []

    pattern = re.compile(
        r"(?<!\d)"
        r"(\d{1,2})[- ](\d{2}(?:\.\d+)?)(?:[- ](\d{2}(?:\.\d+)?))?\s*([NS])[\s,;]*"
        r"(\d{1,3})[- ](\d{2}(?:\.\d+)?)(?:[- ](\d{2}(?:\.\d+)?))?\s*([EW])",
        re.IGNORECASE,
    )
    coordinates: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        lat_deg = int(match.group(1))
        lat_min = float(match.group(2))
        lat_sec = float(match.group(3) or 0)
        lat_dir = match.group(4).upper()
        lon_deg = int(match.group(5))
        lon_min = float(match.group(6))
        lon_sec = float(match.group(7) or 0)
        lon_dir = match.group(8).upper()
        lat = lat_deg + lat_min / 60.0 + lat_sec / 3600.0
        lon = lon_deg + lon_min / 60.0 + lon_sec / 3600.0
        if lat_dir == "S":
            lat = -lat
        if lon_dir == "W":
            lon = -lon
        # 坐标范围校验：纬度 ±90，经度 ±180，异常值丢弃并记录
        if not (_LAT_MIN <= lat <= _LAT_MAX and _LON_MIN <= lon <= _LON_MAX):
            logger.warning(
                "Coordinate out of range dropped: lat=%.6f lon=%.6f raw=%r",
                lat, lon, match.group(0).strip(),
            )
            continue
        coordinates.append(
            {
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "raw": match.group(0).strip(),
            }
        )
    return coordinates


def _coordinates_to_wkt(coordinates: list[dict[str, Any]]) -> str | None:
    if not coordinates:
        return None
    points = [f"{item['lon']} {item['lat']}" for item in coordinates]
    if len(points) == 1:
        return f"POINT({points[0]})"
    return f"MULTIPOINT({', '.join(f'({point})' for point in points)})"


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


def _parse_sealagom_issue_time(time_str: str | None) -> datetime | None:
    """sealagom issue_time 解析，格式如 '01/01/2026, 12:00'。"""
    return safe_datetime(time_str)


def _parse_nga_issue_time(time_str: str | None) -> datetime | None:
    """NGA issue_time 解析，格式如 '011200Z JAN 2026'。"""
    parsed = safe_datetime(time_str)
    if parsed or not time_str:
        return parsed
    try:
        return datetime.strptime(time_str.strip(), "%d%H%MZ %b %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _clean_nga_navwarn_fields(
    *,
    navarea_id: int | None,
    warning_year: int | None,
    serial_number: int | None,
    message_text: str | None,
    warning_no: str | None,
    region: str | None,
    record_id: str,
    quality_flags: list[str],
) -> tuple[int | None, int | None, int | None, str | None, str | None, str | None]:
    """NGA navwarn 专属清洗：业务范围校验、异常值修正、字段长度规范化。

    清洗规则：
    - navarea_id 不在 1-21 范围 → 置 None 并记录 flag
    - warning_year 不在 1900 ~ 当前年份+1 → 置 None 并记录 flag
    - serial_number <= 0 → 置 None 并记录 flag
    - message_text 超长 → 截断并记录 flag
    - warning_no / region 超长 → 截断并记录 flag
    """
    current_year = datetime.now().year

    if navarea_id is not None and not (_NAVAREA_MIN <= navarea_id <= _NAVAREA_MAX):
        logger.warning(
            "nga navarea_id out of range dropped: %s record_id=%s",
            navarea_id, record_id,
        )
        quality_flags.append("navarea_id_out_of_range")
        navarea_id = None

    if warning_year is not None and not (_NAVWARN_YEAR_MIN <= warning_year <= current_year + 1):
        logger.warning(
            "nga warning_year out of range dropped: %s record_id=%s",
            warning_year, record_id,
        )
        quality_flags.append("warning_year_out_of_range")
        warning_year = None

    if serial_number is not None and serial_number <= 0:
        logger.warning(
            "nga serial_number non-positive dropped: %s record_id=%s",
            serial_number, record_id,
        )
        quality_flags.append("serial_number_non_positive")
        serial_number = None

    if message_text and len(message_text) > _NAVWARN_MESSAGE_MAX_LENGTH:
        logger.warning(
            "nga message_text truncated: %d -> %d record_id=%s",
            len(message_text), _NAVWARN_MESSAGE_MAX_LENGTH, record_id,
        )
        quality_flags.append("message_text_truncated")
        message_text = message_text[:_NAVWARN_MESSAGE_MAX_LENGTH]

    if warning_no and len(warning_no) > _NAVWARN_WARNING_NO_MAX_LENGTH:
        warning_no = warning_no[:_NAVWARN_WARNING_NO_MAX_LENGTH]
        quality_flags.append("warning_no_truncated")

    if region and len(region) > _NAVWARN_REGION_MAX_LENGTH:
        region = region[:_NAVWARN_REGION_MAX_LENGTH]
        quality_flags.append("region_truncated")

    return navarea_id, warning_year, serial_number, message_text, warning_no, region


def _navwarn_common(record: dict[str, Any], source: str) -> dict[str, Any]:
    """通用 navwarn 字段提取（参考 news._news_common 多源融合模式）。

    返回完整基础字段 dict（含 coordinate WKT）；issued_at 由源专属函数填充，
    quality_flags / quality_score 由 _finalize_navwarn 计算。
    """
    meta = record.get("_meta", {}) or {}

    message_id = safe_str(record.get("message_id"))
    region = safe_str(record.get("region") or record.get("sea_name") or record.get("navarea"))
    warning_no, region, serial_number, warning_year = _normalize_warning_number(
        record.get("warning_no"),
        region,
    )
    message_text = safe_str(record.get("message_text"))
    coordinates = _extract_coordinates(message_text)
    hazard_type = _classify_hazard_type(message_text)
    navarea_id_raw = record.get("navarea_id") or (meta.get("search_params") or {}).get("navarea_id")
    navarea_id = int(navarea_id_raw) if str(navarea_id_raw or "").isdigit() else None

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

    data_source = (
        record.get("data_source")
        or meta.get("data_source")
        or meta.get("template")
        or source
    )

    return {
        "data_source": data_source,
        "data_type": record.get("data_type") or "navwarn",
        "record_id": record_id,
        "navarea_id": navarea_id,
        "warning_no": warning_no,
        "serial_number": serial_number,
        "warning_year": warning_year,
        "region": region,
        "message_text": message_text,
        "hazard_type": hazard_type,
        "coordinate": _coordinates_to_wkt(coordinates),
    }


def _finalize_navwarn(
    normalized: dict[str, Any],
    quality_flags: list[str],
) -> dict[str, Any]:
    """通用 navwarn 收尾：缺失判断 + quality_score，原地补全并返回。"""
    if not normalized["warning_no"]:
        quality_flags.append("missing_warning_no")
    if normalized["serial_number"] is None:
        quality_flags.append("missing_serial_number")
    if normalized["warning_year"] is None:
        quality_flags.append("missing_warning_year")
    if not normalized["message_text"]:
        quality_flags.append("missing_message_text")
    if not normalized.get("issued_at"):
        quality_flags.append("missing_issue_date")
    if not normalized.get("coordinate"):
        quality_flags.append("no_coordinates_extracted")

    normalized["quality_score"] = max(0.0, 1.0 - len(quality_flags) * 0.2)
    normalized["quality_flags"] = json.dumps(quality_flags, ensure_ascii=False)
    return normalized


def normalize_sealagom_navwarn(record: dict[str, Any]) -> dict[str, Any]:
    """sealagom_navwarn 专属 normalizer：通用提取 + sealagom 时间解析 + 收尾。"""
    normalized = _navwarn_common(record, "sealagom_navwarn")
    normalized["issued_at"] = _parse_sealagom_issue_time(record.get("issue_time"))
    return _finalize_navwarn(normalized, quality_flags=[])


def normalize_nga_navwarn(record: dict[str, Any]) -> dict[str, Any]:
    """nga_navwarn 专属 normalizer：通用提取 + NGA 时间解析 + NGA 清洗 + 收尾。"""
    normalized = _navwarn_common(record, "nga_navwarn")
    normalized["issued_at"] = _parse_nga_issue_time(record.get("issue_time"))
    quality_flags: list[str] = []

    # ── NGA navwarn 专属清洗：异常值修正与字段规范化 ──
    (
        normalized["navarea_id"],
        normalized["warning_year"],
        normalized["serial_number"],
        normalized["message_text"],
        normalized["warning_no"],
        normalized["region"],
    ) = _clean_nga_navwarn_fields(
        navarea_id=normalized["navarea_id"],
        warning_year=normalized["warning_year"],
        serial_number=normalized["serial_number"],
        message_text=normalized["message_text"],
        warning_no=normalized["warning_no"],
        region=normalized["region"],
        record_id=normalized["record_id"],
        quality_flags=quality_flags,
    )

    return _finalize_navwarn(normalized, quality_flags=quality_flags)


register_normalizer("navwarn", "sealagom_navwarn", normalize_sealagom_navwarn)
register_normalizer("navwarn", "nga_navwarn", normalize_nga_navwarn)
