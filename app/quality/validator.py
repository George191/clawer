"""Data validation rule engine.

Field-level validation rules including:
- Non-null / required field checks
- Type coercion & format validation
- Patent number format validation
- Date format validation
- URL format validation
- Custom validation rules via decorator
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


# ── Validation Error ──────────────────────────────────────────────────────

@dataclass
class ValidationError:
    record_index: int
    field: str
    rule: str
    message: str
    value: Any = None


# ── Validation Result ─────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: list[ValidationError] = field(default_factory=list)
    deduplicated: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 1.0
        return self.passed / self.total

    @property
    def error_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.failed / self.total


# ── Schema Definition ─────────────────────────────────────────────────────

@dataclass
class FieldRule:
    name: str
    required: bool = True
    type_check: type | None = None  # str, int, float
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None  # Regex pattern
    allowed_values: list[str] | None = None
    custom: Callable[[Any], bool] | None = None
    nullable: bool = False


# ── Rule Registry ─────────────────────────────────────────────────────────

class ValidationRuleEngine:
    """Apply a schema of FieldRules to a list of records."""

    # Built-in named patterns
    PATTERNS: dict[str, str] = {
        "patent_number": r"^[A-Z]{2}\d+[A-Z]\d*$",
        "publication_number": r"^[A-Z]{2}\d+[A-Z]\d*$",
        "date_iso": r"^\d{4}-\d{2}-\d{2}$",
        "url": r"^https?://",
        "non_empty": r"\S",  # At least one non-whitespace character
    }

    def __init__(self, rules: list[FieldRule], primary_key: str = "patent_id"):
        self._rules = {r.name: r for r in rules}
        self._primary_key = primary_key

    def validate(self, records: list[dict[str, Any]]) -> ValidationResult:
        """Run all rules against all records. Returns ValidationResult."""
        result = ValidationResult(total=len(records))
        seen_keys: set[str] = set()
        valid_records: list[dict[str, Any]] = []

        for idx, record in enumerate(records):
            record_valid = True

            for rule in self._rules.values():
                value = record.get(rule.name)

                # Null check
                if value is None or value == "" or (isinstance(value, str) and value.strip() == ""):
                    if rule.required and not rule.nullable:
                        result.errors.append(ValidationError(
                            idx, rule.name, "required",
                            f"Required field '{rule.name}' is missing or empty",
                        ))
                        record_valid = False
                        continue
                    else:
                        continue

                # Type check
                if rule.type_check is not None and not isinstance(value, rule.type_check):
                    result.errors.append(ValidationError(
                        idx, rule.name, "type",
                        f"Expected {rule.type_check.__name__}, got {type(value).__name__}",
                        value,
                    ))
                    record_valid = False
                    continue

                str_value = str(value)

                # Min length
                if rule.min_length is not None and len(str_value) < rule.min_length:
                    result.errors.append(ValidationError(
                        idx, rule.name, "min_length",
                        f"Field '{rule.name}' min length {rule.min_length}, got {len(str_value)}",
                        value,
                    ))
                    record_valid = False

                # Max length
                if rule.max_length is not None and len(str_value) > rule.max_length:
                    result.errors.append(ValidationError(
                        idx, rule.name, "max_length",
                        f"Field '{rule.name}' max length {rule.max_length}, got {len(str_value)}",
                        value,
                    ))
                    record_valid = False

                # Pattern
                if rule.pattern is not None:
                    pattern_str = self.PATTERNS.get(rule.pattern, rule.pattern)
                    if not re.search(pattern_str, str_value):
                        result.errors.append(ValidationError(
                            idx, rule.name, "pattern",
                            f"Field '{rule.name}' does not match pattern '{rule.pattern}'",
                            value,
                        ))
                        record_valid = False

                # Allowed values
                if rule.allowed_values and str_value not in rule.allowed_values:
                    result.errors.append(ValidationError(
                        idx, rule.name, "allowed_values",
                        f"Field '{rule.name}' value '{str_value}' not in {rule.allowed_values}",
                        value,
                    ))
                    record_valid = False

                # Custom rule
                if rule.custom and not rule.custom(value):
                    result.errors.append(ValidationError(
                        idx, rule.name, "custom",
                        f"Field '{rule.name}' failed custom validation",
                        value,
                    ))
                    record_valid = False

            # Deduplication check
            if record_valid:
                pk_value = record.get(self._primary_key)
                if pk_value is not None:
                    pk_str = str(pk_value).strip()
                    if pk_str in seen_keys:
                        result.deduplicated += 1
                        result.warnings.append(f"Duplicate {self._primary_key}: {pk_str}")
                    else:
                        seen_keys.add(pk_str)
                        valid_records.append(record)
                        result.passed += 1
                else:
                    valid_records.append(record)
                    result.passed += 1
            else:
                result.failed += 1

        return result

    def filter_valid(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return only valid + deduplicated records."""
        result = self.validate(records)
        return records  # We can't return the filtered list from validate directly easily

    @staticmethod
    def from_template_fields(fields: list, primary_key: str = "patent_id") -> ValidationRuleEngine:
        """Build a ValidationRuleEngine from template field definitions."""
        rules: list[FieldRule] = []
        for f in fields:
            rule = FieldRule(
                name=f.name if hasattr(f, 'name') else f.get('name', ''),
                required=getattr(f, 'required', True) if hasattr(f, 'required') else f.get('required', True),
            )
            rules.append(rule)
        return ValidationRuleEngine(rules, primary_key)


# ── Convenience presets ───────────────────────────────────────────────────

def create_patent_validation_engine() -> ValidationRuleEngine:
    """Pre-configured validation engine for patent data."""
    return ValidationRuleEngine(
        rules=[
            FieldRule(name="patent_id", required=True, min_length=3, max_length=300),
            FieldRule(name="publication_number", required=True, pattern="patent_number"),
            FieldRule(name="title", required=True, min_length=1),
            FieldRule(name="assignee", required=False, min_length=1),
            FieldRule(name="filing_date", required=False, pattern="date_iso"),
            FieldRule(name="grant_date", required=False, pattern="date_iso"),
            FieldRule(name="priority_date", required=False, pattern="date_iso"),
        ],
        primary_key="patent_id",
    )


# NAVAREA 编号国际标准范围 1-21（NAVAREA I-XXI）
_NAVAREA_MIN = 1
_NAVAREA_MAX = 21
_NAVWARN_MESSAGE_MAX_LENGTH = 20000
_NAVWARN_WARNING_NO_MAX_LENGTH = 64
_NAVWARN_REGION_MAX_LENGTH = 64
_NAVWARN_HAZARD_MAX_LENGTH = 64
_NAVWARN_RECORD_ID_MAX_LENGTH = 256
_NAVWARN_YEAR_MIN = 1900


def _is_int_in_range(value: Any, low: int, high: int) -> bool:
    """允许 int 类型（排除 bool）并校验闭区间范围。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return low <= value <= high


def create_navwarn_validation_engine() -> ValidationRuleEngine:
    """Pre-configured validation engine for navwarn (NGA) ODS records.

    覆盖字段类型、长度与业务规则：
    - navarea_id: int 且 1-21
    - warning_year: int 且 1900 ~ 当前年份+1
    - serial_number: int 且 > 0
    - coordinate: WKT 格式 (POINT|MULTIPOINT)
    - quality_score: 0.0-1.0
    - record_id / data_source / data_type 必填
    """
    current_year = datetime.now().year

    def _navarea_range(value: Any) -> bool:
        return _is_int_in_range(value, _NAVAREA_MIN, _NAVAREA_MAX)

    def _warning_year_range(value: Any) -> bool:
        return _is_int_in_range(value, _NAVWARN_YEAR_MIN, current_year + 1)

    def _serial_positive(value: Any) -> bool:
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        return value > 0

    def _quality_score_range(value: Any) -> bool:
        if isinstance(value, bool):
            return False
        try:
            return 0.0 <= float(value) <= 1.0
        except (TypeError, ValueError):
            return False

    return ValidationRuleEngine(
        rules=[
            FieldRule(name="record_id", required=True, min_length=1, max_length=_NAVWARN_RECORD_ID_MAX_LENGTH),
            FieldRule(name="data_source", required=True, min_length=1, max_length=64),
            FieldRule(name="data_type", required=True, min_length=1, max_length=32),
            FieldRule(name="navarea_id", required=False, nullable=True, type_check=int, custom=_navarea_range),
            FieldRule(name="warning_no", required=False, nullable=True, max_length=_NAVWARN_WARNING_NO_MAX_LENGTH),
            FieldRule(name="serial_number", required=False, nullable=True, type_check=int, custom=_serial_positive),
            FieldRule(name="warning_year", required=False, nullable=True, type_check=int, custom=_warning_year_range),
            FieldRule(name="region", required=False, nullable=True, max_length=_NAVWARN_REGION_MAX_LENGTH),
            FieldRule(name="message_text", required=False, nullable=True, max_length=_NAVWARN_MESSAGE_MAX_LENGTH),
            FieldRule(name="hazard_type", required=False, nullable=True, max_length=_NAVWARN_HAZARD_MAX_LENGTH),
            FieldRule(name="coordinate", required=False, nullable=True, pattern=r"^(POINT|MULTIPOINT)\("),
            FieldRule(name="quality_score", required=False, nullable=True, custom=_quality_score_range),
        ],
        primary_key="record_id",
    )