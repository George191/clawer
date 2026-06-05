"""Health check & monitoring for crawl results.

Monitors:
- Record count thresholds (too few / unexpected zero)
- Error rate thresholds
- Empty result detection
- Data freshness (stale data warning)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class HealthCheckItem:
    name: str
    status: HealthStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CrawlHealthReport:
    template_name: str
    data_type: str
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    overall_status: HealthStatus = HealthStatus.OK
    items: list[HealthCheckItem] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return self.overall_status != HealthStatus.CRITICAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template_name,
            "data_type": self.data_type,
            "checked_at": self.checked_at.isoformat(),
            "status": self.overall_status.value,
            "items": [
                {"name": i.name, "status": i.status.value, "message": i.message, "details": i.details}
                for i in self.items
            ],
        }


class HealthChecker:
    """Checks crawl results against configurable thresholds."""

    def __init__(
        self,
        min_records: int = 1,
        max_error_rate: float = 0.3,
        max_consecutive_empty: int = 3,
    ):
        self.min_records = min_records
        self.max_error_rate = max_error_rate
        self.max_consecutive_empty = max_consecutive_empty

    def check(
        self,
        template_name: str,
        data_type: str,
        total_records: int,
        error_count: int,
        downloaded_count: int = 0,
        expected_min: int | None = None,
        previous_empty_count: int = 0,
    ) -> CrawlHealthReport:
        report = CrawlHealthReport(template_name, data_type)

        # Check 1: Record count threshold
        report.items.append(self._check_record_count(total_records, expected_min))

        # Check 2: Error rate
        report.items.append(self._check_error_rate(total_records, error_count))

        # Check 3: Empty results
        report.items.append(self._check_empty_results(
            total_records, previous_empty_count
        ))

        # Check 4: Download ratio (if downloads expected)
        if downloaded_count > 0 or total_records > 0:
            report.items.append(self._check_download_ratio(total_records, downloaded_count))

        # Determine overall status
        statuses = [i.status for i in report.items]
        if HealthStatus.CRITICAL in statuses:
            report.overall_status = HealthStatus.CRITICAL
        elif HealthStatus.WARNING in statuses:
            report.overall_status = HealthStatus.WARNING

        return report

    def _check_record_count(
        self, total: int, expected_min: int | None
    ) -> HealthCheckItem:
        threshold = expected_min or self.min_records
        if total < threshold:
            if total == 0:
                return HealthCheckItem(
                    "record_count",
                    HealthStatus.CRITICAL,
                    f"Zero records returned (threshold ≥ {threshold})",
                    {"actual": total, "threshold": threshold},
                )
            return HealthCheckItem(
                "record_count",
                HealthStatus.WARNING,
                f"Record count {total} below minimum threshold {threshold}",
                {"actual": total, "threshold": threshold},
            )
        return HealthCheckItem(
            "record_count",
            HealthStatus.OK,
            f"Record count {total} meets threshold",
            {"actual": total, "threshold": threshold},
        )

    def _check_error_rate(self, total: int, errors: int) -> HealthCheckItem:
        if total == 0 and errors > 0:
            return HealthCheckItem(
                "error_rate",
                HealthStatus.CRITICAL,
                f"All requests failed: {errors} errors with no records",
                {"errors": errors, "total": total},
            )

        rate = errors / total if total > 0 else 0.0
        if rate >= self.max_error_rate:
            return HealthCheckItem(
                "error_rate",
                HealthStatus.CRITICAL,
                f"Error rate {rate:.1%} exceeds threshold {self.max_error_rate:.1%}",
                {"error_rate": round(rate, 4), "threshold": self.max_error_rate, "errors": errors},
            )
        elif rate > self.max_error_rate / 2:
            return HealthCheckItem(
                "error_rate",
                HealthStatus.WARNING,
                f"Error rate {rate:.1%} above half-threshold",
                {"error_rate": round(rate, 4), "threshold": self.max_error_rate, "errors": errors},
            )
        return HealthCheckItem(
            "error_rate",
            HealthStatus.OK,
            f"Error rate {rate:.1%} within acceptable range",
            {"error_rate": round(rate, 4), "errors": errors},
        )

    def _check_empty_results(
        self, total: int, previous_empty_count: int
    ) -> HealthCheckItem:
        if total == 0:
            if previous_empty_count + 1 >= self.max_consecutive_empty:
                return HealthCheckItem(
                    "empty_results",
                    HealthStatus.CRITICAL,
                    f"Empty results for {previous_empty_count + 1} consecutive runs (threshold {self.max_consecutive_empty})",
                    {"consecutive_empty": previous_empty_count + 1, "threshold": self.max_consecutive_empty},
                )
            return HealthCheckItem(
                "empty_results",
                HealthStatus.WARNING,
                f"Empty results this run ({previous_empty_count + 1} consecutive)",
                {"consecutive_empty": previous_empty_count + 1},
            )
        return HealthCheckItem(
            "empty_results",
            HealthStatus.OK,
            "Results contain data",
            {"consecutive_empty": 0},
        )

    def _check_download_ratio(self, total: int, downloaded: int) -> HealthCheckItem:
        """Check that downloaded files are within reasonable ratio of records."""
        if total == 0:
            return HealthCheckItem(
                "download_ratio", HealthStatus.OK, "No records to check downloads against"
            )
        ratio = downloaded / total
        if ratio < 0.1 and downloaded == 0:
            return HealthCheckItem(
                "download_ratio",
                HealthStatus.WARNING,
                f"No files downloaded from {total} records (download config may be broken)",
                {"downloaded": downloaded, "total": total, "ratio": round(ratio, 4)},
            )
        return HealthCheckItem(
            "download_ratio",
            HealthStatus.OK,
            f"Download ratio {ratio:.1%}",
            {"downloaded": downloaded, "total": total, "ratio": round(ratio, 4)},
        )


_default_checker = HealthChecker()


def quick_health_check(
    template_name: str,
    data_type: str,
    total_records: int,
    error_count: int,
    **kwargs,
) -> CrawlHealthReport:
    return _default_checker.check(template_name, data_type, total_records, error_count, **kwargs)