from __future__ import annotations

import json
import re
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_datetime, safe_str
from app.logger import get_logger

logger = get_logger(__name__)

_NGA_REGION_NAVAREA_IDS = {
    "HYDROLANT": 4,
    "HYDROPAC": 12,
}

_LAT_MIN, _LAT_MAX = -90.0, 90.0
_LON_MIN, _LON_MAX = -180.0, 180.0


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("_meta")
    return value if isinstance(value, dict) else {}


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


def _navwarn_common(
    record: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    """通用 navwarn 字段提取（参考 news._news_common 多源融合模式）。

    返回完整基础字段 dict（含 coordinate WKT）；issued_at 由源专属函数填充，
    """
    def pick(name: str) -> Any:
        return record.get(name)

    def dump(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None
    
    meta = _meta(record)

    message_text = safe_str(pick("message_text"))
    coordinates = _extract_coordinates(message_text)

    return {
        "data_source": safe_str(meta.get("template")),
        "data_type": safe_str(meta.get("data_type")),
        "record_id": safe_str(meta.get("record_id")),
        "navarea_id": pick("navarea_id"),
        "warning_no": safe_str(pick("warning_no")),
        "serial_number": pick("serial_number"),
        "warning_year": pick("warning_year"),
        "region": safe_str(pick("region")),
        "sub_region": safe_str(pick("sub_region")),
        "oceans": safe_str(pick("oceans")),
        "dnc_region": safe_str(pick("dnc_region")),
        "status": safe_str(pick("status")),
        "category": safe_str(pick("category")),
        "message_text": message_text,
        "coordinate": _coordinates_to_wkt(coordinates),
    }


def _roman_to_int(value: str) -> int | None:
    roman_values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    previous = 0
    for char in reversed(value):
        current = roman_values[char]
        total += -current if current < previous else current
        previous = current
    return total


def _navarea_id(value: str | None) -> int | None:
    normalized = safe_str(value)
    if not normalized:
        return None
    normalized = re.sub(r"\s+", " ", normalized).upper()
    mapped = _NGA_REGION_NAVAREA_IDS.get(normalized)
    if mapped is not None:
        return mapped
    match = re.fullmatch(r"NAVAREA\s+([IVXLCDM]+|\d+)", normalized)
    if not match:
        return None
    identifier = match.group(1)
    return _roman_to_int(identifier)


def _parse_warning_no(
    warning_no: str | None,
) -> tuple[int | None, int | None, str | None]:
    if not warning_no:
        return None, None, None

    match = re.fullmatch(
        r"\s*(?P<left>\d+)\s*(?P<separator>[/\-])\s*"
        r"(?P<right>\d+)\s*"
        r"(?:\((?P<sub_region>[^()]*)\))?\s*",
        warning_no,
    )
    if not match:
        return None, None, None

    left = match.group("left")
    right = match.group("right")
    separator = match.group("separator")

    # 496/26：序号/年份
    # 21-0325：年份-序号
    if separator == "-" and len(left) == 2 and len(right) == 4:
        year_text = left
        serial_text = right
    else:
        serial_text = left
        year_text = right

    serial_number = int(serial_text)

    if len(year_text) == 2:
        warning_year = 2000 + int(year_text)
    else:
        warning_year = int(year_text)

    sub_region = match.group("sub_region")
    sub_region = sub_region.strip() if sub_region else None

    return serial_number, warning_year, sub_region


def normalize_sealagom_navwarn(record: dict[str, Any]) -> dict[str, Any]:
    """sealagom_navwarn 专属字段映射 + 时间解析。"""
    warning_no = safe_str(record.get("warning_no"))
    region = safe_str(record.get("sea_name"))

    if warning_no:
        warning_prefix_match = re.fullmatch(
            r"\s*(?P<area>[A-Za-z][A-Za-z0-9 _-]*?)\s+"
            r"(?P<label>WARNING|MESSAGE)\s+"
            r"(?P<number>\d+\s*[/\-]\s*\d+(?:\([^()]*\))?)\s*",
            warning_no,
            re.IGNORECASE,
        )
        if warning_prefix_match:
            area = re.sub(
                r"\s+", " ", warning_prefix_match.group("area")
            ).strip().upper()
            if (
                not area.startswith("NAVAREA ")
                and not safe_str(record.get("oceans"))
            ):
                record["oceans"] = area
            warning_no = warning_prefix_match.group("number")
        elif region:
            warning_no = re.sub(
                rf"^\s*{re.escape(region)}\s+",
                "",
                warning_no,
                count=1,
                flags=re.IGNORECASE,
            )

    serial_number, warning_year, sub_region = _parse_warning_no(warning_no)

    record["serial_number"] = serial_number
    record["warning_year"] = warning_year
    record["sub_region"] = sub_region

    record["region"] = region
    record["navarea_id"] = _navarea_id(region)
    record["warning_no"] = (
        f"{warning_year}/{serial_number}"
        if warning_year is not None and serial_number is not None
        else warning_no
    )
    normalized = _navwarn_common(record, "sealagom_navwarn")
    normalized["issued_at"] = safe_datetime(record.get("issue_time"))
    return normalized


def normalize_nga_navwarn(record: dict[str, Any]) -> dict[str, Any]:
    """nga_navwarn 专属字段映射 + 时间解析 + NGA 清洗。"""

    serial_number = int(record.get("warning_no"))
    issue_time = safe_datetime(record.get("issue_time"))
    warning_year = issue_time.year

    if serial_number <= 0:
        header_match = re.search(
            r"^(?:NAVAREA\s+[IVXLCDM]+|HYDROLANT|HYDROPAC|HYDROARC)\s+"
            r"(?P<serial>\d+)\s*/\s*(?P<year>\d{2,4})\b",
            safe_str(record.get("message_text")) or "",
            re.IGNORECASE | re.MULTILINE,
        )
        if header_match:
            serial_number = int(header_match.group("serial"))
            year_text = header_match.group("year")
            warning_year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)

    record["warning_year"] = warning_year
    record["serial_number"] = serial_number
    record["warning_no"] = f"{str(warning_year)}/{serial_number}"
    navarea = safe_str(record.get("navarea"))
    record["region"] = navarea
    record["navarea_id"] = _navarea_id(navarea)
    normalized = _navwarn_common(record, "nga_navwarn")
    normalized["issued_at"] = issue_time

    return normalized


register_normalizer("navwarn", "sealagom_navwarn", normalize_sealagom_navwarn)
register_normalizer("navwarn", "nga_navwarn", normalize_nga_navwarn)
