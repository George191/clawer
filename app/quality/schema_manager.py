"""Schema version management for template compatibility.

Tracks template field definitions across versions and checks
compatibility when templates are updated.

Features:
- Schema fingerprinting (hash of field definitions)
- Version diffing (added/removed/changed fields)
- Compatibility checks (breaking vs non-breaking changes)
- Migration path suggestion
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ChangeType(Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


class CompatibilityLevel(Enum):
    COMPATIBLE = "compatible"  # No breaking changes
    WARNING = "warning"  # May cause issues with downstream consumers
    BREAKING = "breaking"  # Requires migration


@dataclass
class SchemaChange:
    field_name: str
    change_type: ChangeType
    before: Any = None
    after: Any = None
    detail: str = ""


@dataclass
class SchemaDiff:
    template_name: str
    old_version: str
    new_version: str
    changes: list[SchemaChange] = field(default_factory=list)

    @property
    def compatibility(self) -> CompatibilityLevel:
        breaking = [c for c in self.changes if self._is_breaking(c)]
        warnings = [c for c in self.changes if not self._is_breaking(c) and c.change_type != ChangeType.UNCHANGED]
        if breaking:
            return CompatibilityLevel.BREAKING
        if warnings:
            return CompatibilityLevel.WARNING
        return CompatibilityLevel.COMPATIBLE

    @staticmethod
    def _is_breaking(change: SchemaChange) -> bool:
        if change.change_type == ChangeType.REMOVED:
            return True
        if change.change_type == ChangeType.CHANGED:
            # Changing field type, required status, or name is breaking
            return True
        return False

    def to_report(self) -> dict[str, Any]:
        return {
            "template": self.template_name,
            "old_version": self.old_version,
            "new_version": self.new_version,
            "compatibility": self.compatibility.value,
            "changes": [
                {
                    "field": c.field_name,
                    "type": c.change_type.value,
                    "before": c.before,
                    "after": c.after,
                    "detail": c.detail,
                }
                for c in self.changes
            ],
        }


@dataclass
class SchemaVersion:
    template_name: str
    version: str
    fields: list[dict[str, Any]]  # Serialized field definitions
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    checksum: str = ""

    def __post_init__(self):
        if not self.checksum:
            self.checksum = self._compute_checksum()

    def _compute_checksum(self) -> str:
        """Fingerprint: MD5 of sorted canonical JSON of all field definitions."""
        sorted_fields = sorted(self.fields, key=lambda f: f.get("name", ""))
        canonical = json.dumps(sorted_fields, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(canonical.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_name": self.template_name,
            "version": self.version,
            "checksum": self.checksum,
            "created_at": self.created_at.isoformat(),
            "field_count": len(self.fields),
            "field_names": [f.get("name") for f in self.fields],
        }


class SchemaManager:
    """Tracks and compares schema versions for templates.

    Usage:
        mgr = SchemaManager()
        v1 = mgr.register("my_template", [{"name": "field1", "required": True}], "1.0.0")
        v2 = mgr.register("my_template", [{"name": "field1", "required": False}], "1.1.0")
        diff = mgr.diff("my_template", "1.0.0", "1.1.0")
        print(diff.compatibility)  # → WARNING
    """

    def __init__(self):
        self._versions: dict[str, dict[str, SchemaVersion]] = {}  # template → version → SchemaVersion

    def register(
        self,
        template_name: str,
        fields: list[dict[str, Any]],
        version: str,
    ) -> SchemaVersion:
        """Register a new schema version. Returns the new SchemaVersion."""
        sv = SchemaVersion(template_name, version, fields)
        if template_name not in self._versions:
            self._versions[template_name] = {}
        self._versions[template_name][version] = sv
        return sv

    def get(self, template_name: str, version: str) -> SchemaVersion | None:
        return self._versions.get(template_name, {}).get(version)

    def latest_version(self, template_name: str) -> str | None:
        versions = self._versions.get(template_name, {})
        if not versions:
            return None
        return sorted(versions.keys(), reverse=True)[0]

    def diff(
        self,
        template_name: str,
        old_version: str,
        new_version: str,
    ) -> SchemaDiff:
        """Compute field-level diff between two schema versions."""
        old = self.get(template_name, old_version)
        new = self.get(template_name, new_version)

        if old is None or new is None:
            raise ValueError(
                f"Version not found: old={old_version}, new={new_version} for {template_name}"
            )

        old_checksum = old.checksum
        new_checksum = new.checksum

        if old_checksum == new_checksum:
            return SchemaDiff(template_name, old_version, new_version)

        diff = SchemaDiff(template_name, old_version, new_version)
        old_map = {f.get("name"): f for f in old.fields}
        new_map = {f.get("name"): f for f in new.fields}

        all_names = set(old_map.keys()) | set(new_map.keys())

        for name in sorted(all_names):
            in_old = name in old_map
            in_new = name in new_map

            if in_old and not in_new:
                diff.changes.append(SchemaChange(
                    name, ChangeType.REMOVED,
                    before=old_map[name],
                    detail=f"Field '{name}' removed"
                ))
            elif not in_old and in_new:
                diff.changes.append(SchemaChange(
                    name, ChangeType.ADDED,
                    after=new_map[name],
                    detail=f"Field '{name}' added"
                ))
            else:
                old_field = old_map[name]
                new_field = new_map[name]
                if old_field != new_field:
                    changes_detail = []
                    for key in set(old_field.keys()) | set(new_field.keys()):
                        ov = old_field.get(key)
                        nv = new_field.get(key)
                        if ov != nv:
                            changes_detail.append(f"  {key}: {ov!r} → {nv!r}")
                    diff.changes.append(SchemaChange(
                        name, ChangeType.CHANGED,
                        before=old_field, after=new_field,
                        detail="\n".join(changes_detail),
                    ))
                else:
                    diff.changes.append(SchemaChange(
                        name, ChangeType.UNCHANGED,
                    ))

        return diff

    def is_compatible(self, template_name: str, old_version: str, new_version: str) -> bool:
        """Check if new version is backwards-compatible with old."""
        d = self.diff(template_name, old_version, new_version)
        return d.compatibility != CompatibilityLevel.BREAKING

    def all_versions(self, template_name: str) -> list[str]:
        return sorted(self._versions.get(template_name, {}).keys())

    def export_manifest(self, template_name: str) -> dict[str, Any]:
        """Export version manifest for a template."""
        versions = self._versions.get(template_name, {})
        return {
            "template": template_name,
            "versions": {v: sv.to_dict() for v, sv in versions.items()},
            "latest": self.latest_version(template_name),
        }

    def from_template_fields(self, template_name: str, fields: list[dict], version: str) -> SchemaVersion:
        """Register schema from raw field dicts (as stored in YAML)."""
        serialized = []
        for f in fields:
            if hasattr(f, 'model_dump'):
                serialized.append(f.model_dump())
            elif isinstance(f, dict):
                serialized.append(f)
            else:
                serialized.append({"name": str(f)})
        return self.register(template_name, serialized, version)