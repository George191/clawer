"""BR issue increments and SNS release checks, with weekly reconciliation.

ITU documents that SNS is refreshed with each BR IFIC publication:
https://www.itu.int/itu-r-membership/space-explorer-faq/
Receipt dates and notice IDs are not modification cursors.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.logger import get_logger

logger = get_logger(__name__)
RECONCILE_INTERVAL = timedelta(days=7)
ISSUES_PATH = "/itu-r/space/apps/ep/spaceexplorer/v1/dictionaries/wics"


class ItuIncrementalQuery:
    """Freeze one run's query and stage its cursor until the task succeeds."""

    def __init__(self, template: Any, query: dict[str, Any]) -> None:
        self._template = template
        self._query = copy.deepcopy(query)
        self._store = template._crawl_context.get("snapshot_store")
        self._prepared = False
        self._selected: dict[str, Any] | None = None
        self._identity = json.dumps(
            ["itu-issues-v1", template.name, template.base_url, template.data_type,
             template.list_pagination.results_per_page, query],
            sort_keys=True,
        )

    async def prepare(self, client: Any, config: Any) -> dict[str, Any] | None:
        if self._prepared:
            return copy.deepcopy(self._selected)
        if self._store is None:
            return copy.deepcopy(self._query)

        request = config.model_copy(update={
            "method": "GET", "json_data": None, "form_data": {}, "params": {},
        })
        content = await client.request_page(
            self._template.base_url.rstrip("/") + ISSUES_PATH, request,
            anti_crawl_enabled=self._template.effective_anti_crawl_enabled,
        )
        payload = json.loads(content)
        issues = payload.get("wics") if isinstance(payload, dict) else None
        if not isinstance(issues, list) or not issues:
            raise ValueError("ITU issue dictionary is missing or empty")
        now = datetime.now(timezone.utc)
        published = []
        for issue in issues:
            if (not isinstance(issue, dict) or type(issue.get("wic_no")) is not int
                    or issue["wic_no"] <= 0):
                raise ValueError("Invalid ITU issue number")
            try:
                issue_date = datetime.strptime(issue["d_wic"], "%d.%m.%Y").date()
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("Invalid ITU issue date") from error
            if issue_date <= now.date():
                published.append(issue["wic_no"])
        if not published:
            raise ValueError("ITU issue dictionary contains no published issues")
        latest = max(published)

        previous = await self._store.get_state(self._identity)
        full = previous is None
        if previous is not None:
            if (not isinstance(previous, dict)
                    or type(previous.get("last_issue")) is not int
                    or previous["last_issue"] <= 0):
                raise ValueError("Invalid ITU incremental state")
            try:
                last_full = datetime.fromisoformat(previous["last_full_at"])
                if last_full.tzinfo is None:
                    raise ValueError("Missing timezone")
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("Invalid ITU reconciliation time") from error
            if latest < previous["last_issue"]:
                raise ValueError("ITU latest issue moved backwards")
            full = now - last_full >= RECONCILE_INTERVAL

        query = copy.deepcopy(self._query)
        network = query["general"]["network"]
        if network == "active":
            full = full or latest != previous["last_issue"]
            if not full:
                self._prepared = True
                logger.info("ITU SNS unchanged: issue=%s; skipping catalog", latest)
                return None
        elif network == "publication":
            if not full:
                # Re-read the last successful issue as well as every missing issue.
                query["publications"].update({
                    "brIficNumberFrom": previous["last_issue"],
                    "brIficNumberTo": latest,
                })
        else:
            raise ValueError("Invalid ITU network for incremental query")

        await self._store.set_state(self._identity, {
            "last_issue": latest,
            "last_full_at": now.isoformat() if full else previous["last_full_at"],
        })
        self._selected = query
        self._prepared = True
        logger.info(
            "ITU crawl: network=%s mode=%s issue=%s from_issue=%s",
            network, "full reconciliation" if full else "issue increment", latest,
            query["publications"].get("brIficNumberFrom", "all"),
        )
        return copy.deepcopy(query)
