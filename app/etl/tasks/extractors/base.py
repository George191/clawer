"""抽取器接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any


class BaseExtractor(ABC):
    @abstractmethod
    def extract(self, values: Iterable[Any], keywords: list[str]) -> list[str]:
        """从业务值中抽取命中的关键词。"""
        raise NotImplementedError
