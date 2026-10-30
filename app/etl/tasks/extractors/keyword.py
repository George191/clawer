"""忽略大小写、带单词边界的字面关键词匹配。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.etl.tasks.extractors.base import BaseExtractor


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


class KeywordExtractor(BaseExtractor):
    def extract(self, values: Iterable[Any], keywords: list[str]) -> list[str]:
        texts = [text for value in values for text in _strings(value)]
        matched: list[str] = []
        seen: set[str] = set()
        for keyword in keywords:
            keyword = keyword.strip()
            identity = keyword.casefold()
            if not keyword or identity in seen:
                continue
            seen.add(identity)
            pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
            if any(pattern.search(text) for text in texts):
                matched.append(keyword)
        return matched
