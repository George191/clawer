
from functools import reduce
from typing import Any


def get_nested_value(d: dict[str, Any], path: str, default: Any = None) -> Any:
    """完全复刻 mongo_storage.py → get_nested_value。

    按 "." 分隔的路径从嵌套字典中取值。
    """
    try:
        return reduce(lambda c, k: c[k], path.split("."), d)
    except (KeyError, TypeError):
        return default
