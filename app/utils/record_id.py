"""Canonical record identity generation shared by storage and ETL."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def resolve_record_id(identity: dict[str, Any]) -> str:
    canonical = json.dumps(identity, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()
