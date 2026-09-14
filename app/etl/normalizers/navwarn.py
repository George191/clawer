from __future__ import annotations

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
    meta = record.get("_meta", {})

    message_text = safe_str(record.get("message_text"))
    coordinates = _extract_coordinates(message_text)

    return {
        "data_source": safe_str(meta.get("template")),
        "data_type": safe_str(meta.get("data_type")),
        "record_id": safe_str(meta.get("record_id")),
        "navarea_id": safe_str(record.get("navarea_id")),
        "warning_no": safe_str(record.get("warning_no")),
        "serial_number": safe_str(record.get("serial_number")),
        "warning_year": safe_str(record.get("warning_year")),
        "region": safe_str(record.get("region")),
        "subregion": safe_str(record.get("subregion")),
        "oceans": safe_str(record.get("oceans")),
        "dnc_region": safe_str(record.get("dnc_region")),
        "status": safe_str(record.get("status")),
        "category": safe_str(record.get("category")),
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

def normalize_sealagom_navwarn(record: dict[str, Any]) -> dict[str, Any]:
    """sealagom_navwarn 专属字段映射 + 时间解析。"""
    warning_no = safe_str(record.get("warning_no"))
    serial_text, year_text = "0", "0"

    if "/" in warning_no:
        # SealaGOM: 117/26 -> serial=117, year=2026
        serial_text, year_text = (part.strip() for part in warning_no.split("/", 1))

    elif "-" in warning_no:
        # SealaGOM: 21-0325 -> year=2021, serial=325
        year_text, serial_text = (part.strip() for part in warning_no.split("-", 1))

    record["serial_number"] = int(serial_text) if serial_text != "0" else None
    record["warning_year"] = (
        int(year_text)
        if len(year_text) == 4
        else 2000 + int(year_text) if year_text != "0" else None
    )

    region, navarea_id = record["sea_name"].split(" ")
    record["region"] = region
    record["navarea_id"] = _roman_to_int(navarea_id)
    record["warning_no"] = f"{year_text}/{serial_text}"
    normalized = _navwarn_common(record, "sealagom")
    normalized["issued_at"] = safe_datetime(record.get("issue_time"))
    return normalized


def normalize_nga_navwarn(record: dict[str, Any]) -> dict[str, Any]:
    """nga_navwarn 专属字段映射 + 时间解析 + NGA 清洗。"""

    serial_number = record.get("warning_no")
    issue_time = safe_datetime(record.get("issue_time"))
    warning_year = issue_time.year
    record["warning_year"] = warning_year
    record["warning_no"] = f"{str(warning_year)}/{serial_number}"
    navarea = record["navarea"]
    record["region"] = navarea
    record["navarea_id"] = _NGA_REGION_NAVAREA_IDS.get(navarea)
    normalized = _navwarn_common(record, "nga")
    normalized["issued_at"] = issue_time

    return normalized


register_normalizer("navwarn", "sealagom_navwarn", normalize_sealagom_navwarn)
register_normalizer("navwarn", "nga_navwarn", normalize_nga_navwarn)
