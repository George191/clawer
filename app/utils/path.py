
from functools import reduce
import re
from typing import Any


def get_nested_value(d: dict[str, Any], path: str, default: Any = None) -> Any:
    """完全复刻 mongo_storage.py → get_nested_value。

    按 "." 分隔的路径从嵌套字典中取值。
    """
    parts = [part for part in re.split(r"\.|\[|\]", path) if part]

    try:
        return reduce(
            lambda current, key: current[key] if isinstance(current, dict) else current[int(key)],
            parts,
            d,
        )
    except (KeyError, TypeError, IndexError, ValueError):
        return default
