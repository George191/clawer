"""PDF 转换器基类。

所有转换器继承 BaseConverter，实现 convert 方法即可。
通过 get_converter(name) 获取指定转换器实例。

扩展新转换器：
    1. 继承 BaseConverter，设置 name 属性
    2. 实现 convert(pdf_bytes: bytes) -> ConvertResult
    3. 在 converters/__init__.py 中注册
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ConvertResult:
    content: str = ""
    page_count: int = 0
    metadata: dict[str, object] = field(default_factory=dict)


class BaseConverter(ABC):
    name: str = ""

    @abstractmethod
    def convert(self, pdf_bytes: bytes) -> ConvertResult:
        raise NotImplementedError