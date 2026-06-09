"""ODS 航行警告标准化器 (navwarn)。

参考 Google Patent normalizer 的设计模式：
- 按 data_source 注册独立标准化函数
- 使用 _normalize_navwarn_common 提取公共字段逻辑
- 质量评分基于必填字段缺失情况

Sealagom 数据源结构：
- message_id:      消息数字ID (data-message-id 属性)
- warning_no:      警告编号文本 (如 "NAVAREA X 040/26" 或 "AUSCOAST WARNING 050/26")
- sea_name:        海域名称 (如 "NAVAREA X")
- issue_time:      发布时间 (如 "08/06/2026, 04:41")
- message_text:    消息正文纯文本

注册方式：
    from app.etl.normalizers import register_normalizer
    register_normalizer("navwarn", "new_source", normalize_xxx_navwarn)
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import _pick_first, safe_date, safe_str


def _parse_warning_number(warning_no: str | None) -> tuple[str | None, int | None, int | None]:
    """从 warning_no 中解析区域、序号和年份。

    示例:
        "NAVAREA X 040/26"       → ("NAVAREA X", 40, 26)
        "AUSCOAST WARNING 050/26" → ("AUSCOAST WARNING", 50, 26)
        "NAVAREA X 114/26"       → ("NAVAREA X", 114, 26)
    """
    if not warning_no:
        return None, None, None
    # 匹配: 任意前缀 + 数字/年份
    match = re.match(r'(.+?)\s+(\d+)/(\d+)', warning_no.strip())
    if match:
        prefix = match.group(1).strip()
        serial = int(match.group(2))
        year = int(match.group(3))
        return prefix, serial, year
    return None, None, None


def _extract_coordinates(text: str | None) -> list[dict[str, Any]]:
    """从消息文本中提取坐标。

    支持格式:
    - DD-MM.MMN DDD-MM.MME (如 52-07.70N 003-56.40E)
    - DD-MM.MM'S DDD-MM.MM'E
    """
    if not text:
        return []

    coords: list[dict[str, Any]] = []
    # 格式: DD-MM.MM[NS] DDD-MM.MM[EW]
    pattern = re.compile(
        r'(\d{1,2})-(\d{2}(?:\.\d+)?)\s*([NS])\s+'
        r'(\d{1,3})-(\d{2}(?:\.\d+)?)\s*([EW])',
        re.IGNORECASE
    )
    for m in pattern.finditer(text):
        lat_deg, lat_min, lat_dir = int(m.group(1)), float(m.group(2)), m.group(3).upper()
        lon_deg, lon_min, lon_dir = int(m.group(4)), float(m.group(5)), m.group(6).upper()
        lat = lat_deg + lat_min / 60.0
        lon = lon_deg + lon_min / 60.0
        if lat_dir == 'S':
            lat = -lat
        if lon_dir == 'W':
            lon = -lon
        coords.append({
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "raw": m.group(0).strip()
        })

    return coords


def _classify_hazard_type(text: str | None) -> str | None:
    """根据消息内容推断危险类型。"""
    if not text:
        return None
    text_upper = text.upper()
    if any(kw in text_upper for kw in ["RIG", "PLATFORM", "FPSO", "INSTALLATION", "DRILL"]):
        return "offshore_installation"
    if any(kw in text_upper for kw in ["MINE", "ORDNANCE", "UXO", "EXPLOSIVE"]):
        return "military_ordnance"
    if any(kw in text_upper for kw in ["WRECK", "SUNKEN", "SUBMERGED"]):
        return "wreck"
    if any(kw in text_upper for kw in ["CABLE", "PIPE", "LAYING", "PIPELINE"]):
        return "cable_pipe"
    if any(kw in text_upper for kw in ["BUOY", "LIGHT", "MARK", "BEACON", "LIGHTBUOY"]):
        return "aid_to_navigation"
    if any(kw in text_upper for kw in ["EXERCISE", "FIRING", "MILITARY"]):
        return "military_exercise"
    if any(kw in text_upper for kw in ["DRILL", "SURVEY", "SEISMIC", "RESEARCH"]):
        return "survey_operations"
    if any(kw in text_upper for kw in ["TOWING", "TOW", "CONVOY"]):
        return "towing"
    if any(kw in text_upper for kw in ["SPACE DEBRIS", "RE-ENTRY"]):
        return "space_debris"
    if any(kw in text_upper for kw in ["RADIO", "DSC", "MF", "VHF", "NAVTEX"]):
        return "communications"
    return "general"


def _parse_issue_time(time_str: str | None) -> str | None:
    """解析 Sealagom 的时间格式为 ISO 日期字符串。

    输入格式: "08/06/2026, 04:41" (DD/MM/YYYY, HH:MM)
    输出格式: "2026-06-08" (YYYY-MM-DD, 仅日期部分)
    """
    if not time_str:
        return None
    try:
        # 尝试解析 DD/MM/YYYY, HH:MM 格式
        dt = datetime.strptime(time_str.strip(), "%d/%m/%Y, %H:%M")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        # 尝试其他常见格式
        for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
            try:
                dt = datetime.strptime(time_str.strip(), fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None


def _normalize_navwarn_common(record: dict[str, Any], source: str) -> dict[str, Any]:
    """航行警告通用标准化逻辑。"""
    quality_flags: list[str] = []

    # 基本字段提取
    message_id = safe_str(record.get("message_id"))
    warning_no = safe_str(record.get("warning_no"))
    sea_name = safe_str(record.get("sea_name"))
    issue_time_raw = safe_str(record.get("issue_time"))
    message_text = safe_str(record.get("message_text"))

    # 解析 warning_no 获取结构化信息
    warning_prefix, serial_number, year_suffix = _parse_warning_number(warning_no)

    # 解析时间
    issue_date = _parse_issue_time(issue_time_raw)

    # 构建唯一 record_id
    # 优先用 warning_no (含年份信息), 回退到 message_id
    if warning_no:
        # 清理 warning_no 用于 record_id: 空格→下划线, 去掉特殊字符
        clean_warning = re.sub(r'[^a-zA-Z0-9/]', '_', warning_no).strip('_').lower()
        record_id = f"sealagom:{clean_warning}"
    elif message_id:
        record_id = f"sealagom:msg_{message_id}"
    else:
        record_id = f"sealagom:unknown_{hash(message_text or '')}"

    # 提取坐标
    coordinates = _extract_coordinates(message_text)

    # 分类危险类型
    hazard_type = _classify_hazard_type(message_text)

    # 质量评估
    if not warning_no:
        quality_flags.append("missing_warning_no")
    if not message_text:
        quality_flags.append("missing_message_text")
    if not issue_date:
        quality_flags.append("missing_issue_date")
    if not coordinates:
        quality_flags.append("no_coordinates_extracted")

    quality_score = max(0.0, 1.0 - len(quality_flags) * 0.2)

    # extra_data: 保留不在标准字段中的原始数据
    excluded_keys = {
        "message_id", "warning_no", "sea_name", "issue_time", "message_text",
        "message_body", "_meta", "_kafka_meta",
        "data_source", "data_type", "record_id",
    }
    extra = {k: v for k, v in record.items() if k not in excluded_keys and v is not None}

    return {
        "data_source": source,
        "data_type": record.get("data_type") or "navwarn",
        "record_id": record_id,
        "navarea_id": record.get("navarea_id"),
        "warning_no": warning_no,
        "warning_prefix": warning_prefix,
        "serial_number": serial_number,
        "year": (2000 + year_suffix) if year_suffix else None,
        "sea_name": sea_name,
        "issue_date": issue_date,
        "message_text": message_text,
        "hazard_type": hazard_type,
        "coordinates": json.dumps(coordinates) if coordinates else None,
        "quality_score": quality_score,
        "quality_flags": json.dumps(quality_flags),
        "extra_data": json.dumps(extra) if extra else None,
    }


def normalize_sealagom_navwarn(record: dict[str, Any]) -> dict[str, Any]:
    """Sealagom 航行警告标准化入口。"""
    return _normalize_navwarn_common(record, "sealagom")


# 注册到全局标准化器注册中心
register_normalizer("navwarn", "sealagom", normalize_sealagom_navwarn)
