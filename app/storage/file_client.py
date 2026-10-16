"""Local filesystem access without record schemas or lifecycle rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config.settings import settings


class FileClient:
    def __init__(self, base_dir: str | None = None) -> None:
        self._base_dir = Path(base_dir or settings.output_dir)

    @staticmethod
    def read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def write_json(path: Path, data: dict[str, Any]) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
