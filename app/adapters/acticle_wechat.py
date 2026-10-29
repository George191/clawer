"""取阅（feed.zoro.build）微信公众号文章 RSS 采集适配器。

取阅聚合 RSS 是标准 RSS 2.0 XML，模板解析器仅支持 HTML/JSON，
由本适配器覆盖 parse_list_response 直接解析 XML。
Free 套餐仅输出文章摘要；升级后 description/content 为全文。
"""

from __future__ import annotations

import base64
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime

from app.adapters import BaseSiteAdapter, register_adapter

_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
_TZ_CN = timezone(timedelta(hours=8))


def _text(item: ET.Element, tag: str) -> str:
    node = item.find(tag)
    if node is None or not node.text:
        return ""
    return " ".join(node.text.split())


def _cover(item: ET.Element) -> str:
    """enclosure 为取阅代理的封面图：/i/<hash>/<base64(微信原始地址)>。"""
    node = item.find("enclosure")
    if node is None:
        return ""
    url = (node.get("url") or "").strip()
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    try:
        decoded = base64.urlsafe_b64decode(tail + "=" * (-len(tail) % 4)).decode()
    except Exception:
        return url
    return decoded if decoded.startswith("http") else url


def _guid(item: ET.Element) -> str:
    return _text(item, "guid")


def _biz(link: str) -> str:
    """从文章链接提取公众号 __biz 标识。"""
    m = re.search(r"__biz=([^&#]+)", link)
    return m.group(1) if m else ""


def _parse_pub_date(raw: str) -> str:
    try:
        return parsedate_to_datetime(raw).astimezone(_TZ_CN).isoformat()
    except (TypeError, ValueError):
        return raw


@register_adapter("acticle_wechat")
class ZoroWeixinFeedAdapter(BaseSiteAdapter):
    """解析取阅公众号聚合 RSS；feed 暂无文章时返回 0 条记录。"""

    adapter_name = "acticle_wechat"

    async def parse_list_response(self, page: int, content: str) -> list[dict]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as error:
            raise ValueError(f"取阅 RSS 响应不是合法 XML: {error}") from error

        records: list[dict] = []
        for item in root.iter("item"):
            link = _text(item, "link")
            if not link:
                continue
            records.append({
                "source_id": hashlib.sha256(link.encode()).hexdigest()[:32],
                "account": _text(item, _DC_CREATOR) or _text(item, "author"),
                "gh_id": _biz(link),
                "zoro_guid": _guid(item),
                "title": _text(item, "title"),
                "url": link,
                "cover": _cover(item),
                "summary": _text(item, "description"),
                "publish_time": _parse_pub_date(_text(item, "pubDate")),
                "info_source": self._base_url,
            })
        return records
